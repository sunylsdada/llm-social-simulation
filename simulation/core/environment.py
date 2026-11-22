"""
环境模块 — SocialMedia + Simulator 15天主循环。
"""

import random
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from simulation.core.agent import Agent
from simulation.core.post_pool import PostPool
from simulation.core.government import AnalystAgent, GovernmentAgent
from simulation.infra.utils import (
    is_agent_active_today, score_comment_negativity,
    load_user
)


class _DummyLogger:
    """空日志器，不使用日志时的占位"""
    def log(self, msg, level='INFO'): pass
    def agent(self, day, aid, action_type, panic, trust, rumor): pass
    def phase(self, msg): pass
    def llm_query(self, prompt): pass
    def llm_response(self, result): pass


class SocialMedia:
    """
    社交媒体环境，包装 PostPool + 策略执行通道。
    """

    def __init__(self, config, llm):
        self.global_config = config
        self.config = config
        self.llm = llm

        # 帖子池
        self.post_pool = PostPool(config, llm)

        # 策略相关
        self.strategy_config = config['strategy_config']
        self.panic_keywords = config.get('panic_keywords', [])
        self.negative_words = config.get('negative_emotion_words', [])

        # 政府通告内容（S3/S5 注入）
        self.government_notice = None

        # 每日统计
        self.daily_stats = []

    def recover_llm(self, llm):
        self.llm = llm
        self.post_pool.llm = llm

    # ---- 信息流 ----

    def get_info_flow(self, current_day, agent):
        """为某个 agent 生成当日信息流"""
        max_posts = self.config['agent_config'].get('max_posts_per_agent', 5)
        max_comments = self.config['agent_config'].get('max_comments_per_post', 10)
        seed = agent.aid * 1000 + current_day  # 确定性随机，方便复现
        return self.post_pool.get_info_flow(
            current_day,
            max_posts=max_posts,
            max_comments_per_post=max_comments,
            shuffle_subset=True,
            seed=seed,
        )

    # ---- 策略执行通道 ----

    def set_strategy(self, strategy_id):
        """记录当前策略（政府决策后调用）"""
        self.current_strategy = strategy_id

    def execute_daily_intervention(self, current_day):
        """旧版每日干预：所有动作都在 agent 行动之后执行。"""
        strategy = getattr(self, 'current_strategy', 'S1')

        if strategy == 'S1':
            pass  # 沉默，什么都不做

        elif strategy == 'S2':
            self._execute_suppress()

        elif strategy == 'S3':
            # 坦诚回应是**持续沟通**，不是发一次声明就沉默。
            # 原实现只在决策日注入通告，其余日子与 S1 完全相同，
            # 导致 S3 与 S1 几乎不可能测出差异。改为每日更新进展通报。
            self._daily_government_update(current_day)

        elif strategy == 'S4':
            # S4 日常降权旧帖（降低非政府帖的可见度）
            self._execute_demote_old_posts(current_day)

        elif strategy == 'S5':
            self._execute_suppress_extreme()

    def execute_pre_action(self, current_day):
        """新事件：在 agent 浏览前更新可见信息（S3 进展、S4 降权）。"""
        strategy = getattr(self, 'current_strategy', 'S1')
        if strategy == 'S3':
            self._daily_government_update(current_day)
        elif strategy == 'S4':
            self._execute_demote_old_posts(current_day)

    def execute_post_action(self, current_day):
        """新事件：在 agent 行动后执行删除类动作（S2/S5）。"""
        strategy = getattr(self, 'current_strategy', 'S1')
        if strategy == 'S2':
            self._execute_suppress()
        elif strategy == 'S5':
            self._execute_suppress_extreme()

    def execute_decision_day_action(self, current_day, strategy, llm=None):
        """
        政府决策日当天的额外操作（注入通告、发新帖等）。
        """
        self.current_strategy = strategy

        if strategy == 'S3':
            self._inject_government_notice(current_day, llm, full=True)
        elif strategy == 'S4':
            self._inject_new_topic_post(current_day, llm)
        elif strategy == 'S5':
            self._inject_government_notice(current_day, llm, full=False)

    # ---- S2: 压制封堵 ----

    def _execute_suppress(self):
        """删除最负面的评论（规则驱动，不用 LLM）"""
        max_delete = self.strategy_config.get('max_delete_per_day', 5)
        scored = []

        for post in self.post_pool.posts.values():
            for cid, comment in post.comments.items():
                if comment.parent_id is None:  # 只处理一级评论
                    neg = score_comment_negativity(
                        comment.comment_text, self.panic_keywords, self.negative_words
                    )
                    if neg > 0:
                        scored.append((neg, cid, post.pid))

        # 按负面程度排序，删最负面的
        scored.sort(key=lambda x: x[0], reverse=True)
        for i, (neg, cid, pid) in enumerate(scored):
            if i >= max_delete:
                break
            if pid in self.post_pool.posts:
                self.post_pool.posts[pid].remove_comment(cid)

    # ---- S3: 坦诚回应 ----

    def _recent_facts_text(self, current_day, n=3):
        """
        把最近已公开的客观事实整理成提示词片段，供政府生成通报时对照。

        为什么需要：通报 prompt 若不包含当天事实，模型可能生成与事实相悖的内容
        （例如在"刑事移交 + EPA 罚款"当天仍称"情况总体可控"）—— 那不是隐瞒，
        而是幻觉。把事实喂进去，才能让"隐瞒/美化"成为**有意识的选择**而非无知。
        """
        facts = [p for p in self.post_pool.posts.values()
                 if getattr(p, 'is_stimulus', False)
                 and not getattr(p, 'is_notice', False)
                 and p.day <= current_day]
        if not facts:
            return '（暂无已公开的事实）'
        facts.sort(key=lambda p: (p.day, p.pid))
        facts = facts[-n:]
        return '\n'.join(
            f'第{p.day}天｜{p.title}：{p.content[:100]}' for p in facts)

    def _inject_government_notice(self, current_day, llm=None, full=True):
        """
        注入政府通告作为高可见内容。
        full=True: 长篇通告（S3）
        full=False: 简短通告（S5）
        """
        if llm is None:
            llm = self.llm

        facts = self._recent_facts_text(current_day)

        if full:
            # S3 坦诚回应：必须与已公开事实一致，包括不利信息
            prompt = f"""你是政府的新闻发言人。当前社会发生了一起引发广泛关注的公共事件。

[当前已公开的事实]
{facts}

请撰写一份坦诚、负责任的官方通报（约150字），内容包括：
1. 基于上述事实说明当前情况（包括对政府不利的信息）
2. 政府对公众关切的回应态度
3. 政府正在采取的措施和下一步计划

[硬约束]
- 你的表述必须与上述已公开事实一致，不得否认、回避或编造。
- 不得声称"一切安全""没有风险"，除非上述事实支持。
- 语气应诚恳、透明、有担当。

请以“关于XX事件的官方通报”为标题，正文以第一人称政府口吻撰写。"""
        else:
            # S5 组合应对：淡化不利事实、强调正面信息
            # 注意：与 S3 的"如实一致"不同，S5 允许对不利事实作淡化处理 ——
            # 这正是"美化"类策略的特征，也是它与坦诚回应的关键区别。
            prompt = f"""你是政府的新闻发言人。当前社会发生了一起引发关注的公共事件。

[当前已公开的事实]
{facts}

请撰写一份简短官方声明（约60字），表达政府正在关注此事并采取行动。

[策略要求：淡化 + 正面强调]
- 对政府不利的事实，可以少提、轻描淡写或使用模糊表述；
- 重点强调政府已经采取的积极措施、正面进展和未来承诺；
- 语气要显得诚恳、克制，避免明显的宣传腔。"""

        notice_text = llm.fast_run(prompt)
        self.government_notice = notice_text

        # 将通告作为一条特殊的"帖子"或置顶评论注入
        # 方案：创建一个新的政府公告帖子
        labels = self._source_labels()
        notice_title = labels['government_notice_title'] if full else "官方声明"
        pid = self.post_pool.add_post(
            title=notice_title,
            content=notice_text,
            source=labels['official'],
            day=current_day,
            is_notice=True,          # 通告在信息流中置顶，不受随机抽样影响
        )
        self._notice_pid = pid
        # 初始热度只参与热度排序；可见性由 is_notice 置顶保证
        self.post_pool.posts[pid].view_num = 100
        self.post_pool.posts[pid].like_num = 50

    def _daily_government_update(self, current_day, llm=None):
        """
        S3 的每日持续沟通：把置顶通告更新为当天的进展通报。

        为什么更新通告正文、而不是每天新发一帖：新帖会稀释帖子池，
        而更新置顶通告既保证可见性，也更接近"官方持续发布进展"的实际做法。
        """
        if llm is None:
            llm = self.llm

        notice_pid = getattr(self, '_notice_pid', None)
        if notice_pid is None or notice_pid not in self.post_pool.posts:
            # 还没发布过通告（例如首个决策日不在第 1 天），先补一份
            self._inject_government_notice(current_day, llm, full=True)
            return

        notice = self.post_pool.posts[notice_pid]
        if getattr(notice, 'updated_day', notice.day) == current_day:
            # 决策日刚注入过完整通告；不要在 agent 浏览前又覆盖成短进展。
            return

        facts = self._recent_facts_text(current_day)
        prompt = (
            "你是政府的新闻发言人。请就该公共事件撰写一条**今日进展通报**（约 80 字）。\n\n"
            f"[当前已公开的事实]\n{facts}\n\n"
            "内容包括：今天新掌握的情况、已经采取的具体措施、或下一步安排。\n"
            "[硬约束] 要求具体、不空泛，不重复之前的表述，且不得与上述已公开事实相悖。"
        )
        text = llm.fast_run(prompt)
        self.post_pool.posts[notice_pid].content = f"【第{current_day}日进展】{text}"
        # 通告正文今天被更新过，时效分组应按今天算，而不是按首次发布日。
        self.post_pool.posts[notice_pid].updated_day = current_day

    # ---- S4: 转移议程 ----

    def _source_labels(self) -> dict:
        ctx = self.global_config.get('event_context') or {}
        defaults = {
            'official': '政府官方发布',
            'local_news': '本地新闻',
            'government_notice_title': '关于事件的官方通报',
        }
        labels = dict(defaults)
        labels.update(ctx.get('source_labels') or {})
        return labels

    def _inject_new_topic_post(self, current_day, llm=None):
        """
        注入新话题帖子来转移注意力。
        LLM 生成一个与当前事件无关但积极/中性的新话题。
        """
        if llm is None:
            llm = self.llm

        ctx = self.global_config.get('event_context') or {}
        event_name = ctx.get('event_name') or '当前公共事件'
        guidance = ctx.get('topic_shift_guidance') or             '科技创新、民生改善、文化活动、体育赛事等积极方向'
        prompt = f"""当前社交媒体上热议的是「{event_name}」，舆论情绪较为负面。
请设计一个全新的、与该事件无关的正面话题，用于引导公众注意力。
新话题可以是：{guidance}。

请输出格式：
TITLE: <新话题标题，有吸引力>
CONTENT: <新话题内容，约100字，积极正面>
"""
        result = llm.fast_run(prompt)

        # 解析
        import re as _re
        title_match = _re.search(r'TITLE:\s*(.*)', result)
        content_match = _re.search(r'CONTENT:\s*([\s\S]*)', result)

        if title_match and content_match:
            title = title_match.group(1).strip()
            content = content_match.group(1).strip()
        else:
            fallback = ctx.get('fallback_topic') or {}
            title = fallback.get('title') or "本地积极进展"
            content = fallback.get('content') or                 "近日本地在民生与公共服务方面取得积极进展，相关部门表示将继续改善居民生活。"

        pid = self.post_pool.add_post(
            title=title,
            content=content,
            source=self._source_labels()['local_news'],
            day=current_day,
            stimulus_type='new_topic',
        )
        # 给转移帖初始热度
        self.post_pool.posts[pid].view_num = 200
        self.post_pool.posts[pid].like_num = 80

    # ---- 降权旧帖 ----

    def _execute_demote_old_posts(self, current_day):
        """S4 日常：降低已发布超过2天的非政府帖的可见度"""
        labels = self._source_labels()
        protected = {labels['official'], labels['local_news']}
        for post in self.post_pool.posts.values():
            age = current_day - post.day
            if age > 2 and not any(s in post.source for s in protected):
                post.view_num = max(post.view_num - 3, 0)

    # ---- S5: 组合应对 ----

    def _execute_suppress_extreme(self):
        """S5 日常：只删极端评论（恐慌关键词命中的）"""
        max_delete = self.strategy_config.get('max_delete_per_day', 5)
        deleted = 0

        for post in self.post_pool.posts.values():
            to_remove = []
            for cid, comment in post.comments.items():
                if comment.parent_id is None:
                    neg = score_comment_negativity(
                        comment.comment_text, self.panic_keywords, self.negative_words
                    )
                    # S5 阈值比 S2 高：只删极端评论
                    if neg >= 4:
                        to_remove.append(cid)
            for cid in to_remove:
                if deleted >= max_delete:
                    break
                post.remove_comment(cid)
                deleted += 1

    # ---- 统计 ----

    def get_snapshot(self, current_day, agents):
        """获取当天社交媒体全量快照"""
        day_summary = self.post_pool.get_day_summary(current_day)

        panic_values = [a.panic for a in agents]
        trust_values = [a.government_trust for a in agents]
        rumor_values = [a.rumor_susceptibility for a in agents]

        return {
            'day': current_day,
            'avg_panic': sum(panic_values) / len(panic_values) if panic_values else 0,
            'avg_trust': sum(trust_values) / len(trust_values) if trust_values else 0,
            'avg_rumor': sum(rumor_values) / len(rumor_values) if rumor_values else 0,
            'active_posts': day_summary['active_posts'],
            'total_comments': day_summary['total_comments'],
            'total_views': day_summary['total_views'],
            'total_likes': day_summary['total_likes'],
            'all_comments': day_summary['all_comments'],
            'top_posts': day_summary['top_posts'],
        }


class Simulator:
    """
    Main simulation loop.
    """

    def __init__(self, config, llm, recorder, checkpoint, logger=None):
        self.global_config = config
        self.config = config['simulation_config']
        self.llm = llm
        self.recorder = recorder
        self.checkpoint = checkpoint
        self.logger = logger or _DummyLogger()

        self.total_days = self.config['total_days']
        self.decision_days = set(self.config['government_decision_days'])
        self.max_workers = self.config.get('max_workers', 8)
        self.intervention_timing = self.global_config.get(
            'intervention_timing', 'split')

        # ---- 可复现性：在构造 agent 之前固定 Python 随机种子 ----
        # Agent 的初始 panic/trust/rumor 由 random.gauss 抽取；不在构造前播种，
        # 同一份 user.csv 每次跑出的初始状态都不同，跨策略/跨种子比较会混入初态差异。
        # LLM 采样仍有随机性；这里保证的是仿真侧的随机过程可复现。
        self.seed = self.config.get('seed')
        if self.seed is not None:
            random.seed(self.seed)

        self.social_media = SocialMedia(config, llm)
        self.agents = []

        self.current_strategy = 'S1'
        self.government_decisions = []
        self.government_agent = GovernmentAgent(config, llm)
        self.last_decision_snapshot = None
        self.previous_strategy = None     # 上次决策选的策略，用于更新其效用

        # 线程安全锁
        self._pool_lock = threading.Lock()

        self._initialize_agents()
        self.logger.log(f'初始化完成: {len(self.agents)} agents, '
                        f'{self.total_days}天模拟, {self.max_workers}并发')

    def _initialize_agents(self):
        """初始化 agent：支持按 uid 显式选取（agent_uids）或取前 N 个。"""
        users = load_user(self.global_config)
        total_users = len(users)

        wanted = [int(x) for x in (self.config.get('agent_uids') or [])]
        if wanted:
            duplicated = sorted({u for u in wanted if wanted.count(u) > 1})
            if duplicated:
                raise ValueError(f'agent_uids 重复: {duplicated}')
            by_uid = {int(u['uid']): u for u in users}
            missing = [u for u in wanted if u not in by_uid]
            if missing:
                raise ValueError(f'agent_uids 不存在于 user.csv: {missing}')
            # 保持调用方给定的顺序：Agent.__init__ 里的 random.gauss 按此顺序取数，
            # 顺序一变，同一 seed 的初始扰动就会变，跨运行不可复现。
            user_list = [by_uid[u] for u in wanted]
            selection_mode = 'explicit'
        else:
            max_agents = self.config.get('max_agents', total_users)
            user_list = users[:max_agents]
            selection_mode = 'first_n'

        self.recorder.add_record('META', {
            'user_count': len(user_list),
            'total_users_in_csv': total_users,
            'requested_agent_uids': wanted or None,
            'agent_uids': [int(u['uid']) for u in user_list],
            'selection_mode': selection_mode,
            'seed': self.seed,
            'total_days': self.total_days,
            'government_decision_days': sorted(self.decision_days),
            'stimulus_mode': self.global_config.get('stimulus_config', {}).get('mode'),
            'stimulus_skipped': getattr(
                self.social_media.post_pool, 'stimulus_skipped', None),
        })

        for user in user_list:
            agent = Agent(self.global_config, user, self.llm)
            self.agents.append(agent)

        # daily_snapshots[0] 是第 1 天互动之后的状态，不能代表初值。
        # 单独留一份初始快照，方便核对 LLM 初值水平与 fallback 情况。
        initial_states = [{
            'aid': a.aid,
            'name': a.name,
            'activity': a.activity,
            'big_five': a.personality,
            'panic': a.panic,
            'trust': a.government_trust,
            'rumor': a.rumor_susceptibility,
            'initial_source': a.initial_source,
        } for a in self.agents]
        n = len(initial_states)
        self.recorder.add_record('META', {
            'initial_avg_panic': sum(x['panic'] for x in initial_states) / n if n else 0,
            'initial_avg_trust': sum(x['trust'] for x in initial_states) / n if n else 0,
            'initial_avg_rumor': sum(x['rumor'] for x in initial_states) / n if n else 0,
            'agent_initial_states': initial_states,
        })

    def recover_llm(self, llm):
        self.llm = llm
        for agent in self.agents:
            agent.recover_llm(llm)
        self.social_media.recover_llm(llm)
        self.government_agent.llm = llm

    # ---- 主循环 ----

    def run(self):
        """仿真主循环。"""
        for day in range(1, self.total_days + 1):
            self.logger.phase(f'Day {day}/{self.total_days}  策略={self.current_strategy}')

            # ---- 政府决策日 ----
            if day in self.decision_days:
                self.logger.log(f'政府决策 #{len(self.government_decisions)+1} 开始...')
                snapshot = self.social_media.get_snapshot(day, self.agents)
                analyst = AnalystAgent(self.global_config, self.llm)
                report = analyst.generate_report(snapshot)

                # 如果非首次决策，更新效用矩阵
                if self.previous_strategy and self.last_decision_snapshot:
                    observed = {
                        'panic': snapshot['avg_panic'] - self.last_decision_snapshot['avg_panic'],
                        'rumor': snapshot.get('avg_rumor', 0) - self.last_decision_snapshot.get('avg_rumor', 0),
                        'trust': snapshot['avg_trust'] - self.last_decision_snapshot['avg_trust'],
                    }
                    old_matrix = self.government_agent.utility_matrix[self.previous_strategy].copy()
                    self.government_agent.update_utility(self.previous_strategy, observed)
                    new_matrix = self.government_agent.utility_matrix[self.previous_strategy]
                    self.logger.log(f'学习更新 {self.previous_strategy}: '
                                   f'[{old_matrix[0]:+.3f},{old_matrix[1]:+.3f},{old_matrix[2]:+.3f}]'
                                   f' → [{new_matrix[0]:+.3f},{new_matrix[1]:+.3f},{new_matrix[2]:+.3f}]')

                # 使用持久化的政府 agent，传入上次快照
                decision = self.government_agent.make_decision(
                    report, day,
                    previous_snapshot=self.last_decision_snapshot
                )
                self.last_decision_snapshot = snapshot
                self.previous_strategy = decision['selected_strategy']
                self.current_strategy = decision['selected_strategy']
                self.government_decisions.append(decision)
                self.recorder.add_record('GOVERNMENT', decision)

                self.logger.log(f'政府决策: {self.current_strategy} '
                                f'权重={decision.get("weights", {})}')
                self.logger.log(f'态势评估: {decision.get("situation_assessment", "")[:100]}')
                predicted = decision.get('predicted_effect', {})
                self.logger.log(f'预测效果: panic={predicted.get("panic",0):+.2f} '
                               f'rumor={predicted.get("rumor",0):+.2f} '
                               f'trust={predicted.get("trust",0):+.2f}')

                self.social_media.execute_decision_day_action(
                    day, self.current_strategy, llm=self.llm
                )

            # 新事件：在 agent 浏览前更新 S3 进展 / S4 降权，
            # 避免“今日进展”永远晚一天才被看到。
            if self.intervention_timing == 'split':
                self.social_media.execute_pre_action(day)

            # ---- 每天：Agent 互动（并行） ----
            activity_prob = self.global_config['agent_config']['activity_prob']
            active_agents = [a for a in self.agents
                             if is_agent_active_today(a.activity, activity_prob)]

            if active_agents:
                results = []
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {}
                    for agent in active_agents:
                        info_flow = self.social_media.get_info_flow(day, agent)
                        future = executor.submit(agent.take_action, info_flow, day)
                        futures[future] = agent

                    for future in as_completed(futures):
                        agent = futures[future]
                        try:
                            action_type, content_info, action_record = future.result()
                        except Exception as e:
                            self.logger.log(f'Agent{agent.aid} 出错: {e}', 'ERROR')
                            action_type, content_info, action_record = 'silent', {'type': 'silent'}, {}
                        results.append((agent, action_type, content_info, action_record))

                # ---- 关键：按 aid 排序后再写回 PostPool ----
                # as_completed 的顺序受网络与线程调度影响；若按完成顺序写入，
                # 同一 seed 的两次运行会得到不同的帖子热度/次日信息流，
                # 仿真侧仍然不可复现。这里先收齐结果，再按固定顺序落盘。
                results.sort(key=lambda x: x[0].aid)
                for agent, action_type, content_info, action_record in results:
                    self._apply_agent_action(agent, action_type, content_info)
                    self.logger.agent(day, agent.aid, action_type,
                                     agent.panic, agent.government_trust,
                                     agent.rumor_susceptibility)
                    self.recorder.add_record('AGENT_ACTION', {
                        'day': day,
                        'aid': agent.aid,
                        'name': agent.name,
                        'panic': agent.panic,
                        'government_trust': agent.government_trust,
                        'rumor_susceptibility': agent.rumor_susceptibility,
                        'action_type': action_type,
                        'content_info': content_info,
                        'action_record': action_record,
                    })

            self.logger.log(f'Day{day} 活跃={len(active_agents)}/{len(self.agents)}', 'STAT')

            # ---- 策略自动干预 ----
            if self.intervention_timing == 'split':
                self.social_media.execute_post_action(day)
            else:
                self.social_media.execute_daily_intervention(day)

            # ---- 快照 ----
            snapshot = self.social_media.get_snapshot(day, self.agents)
            self.recorder.add_record('DAILY_SNAPSHOT', snapshot)
            self.logger.log(f'Day{day} avg_panic={snapshot["avg_panic"]:.3f} '
                            f'avg_trust={snapshot["avg_trust"]:.3f} avg_rumor={snapshot["avg_rumor"]:.3f} '
                            f'comments={snapshot["total_comments"]}', 'STAT')

        # ---- 模拟结束 ----
        final_snapshot = self.social_media.get_snapshot(self.total_days, self.agents)
        self.recorder.add_record('META', {
            'final_snapshot': final_snapshot,
            'government_decisions': self.government_decisions,
        })

        self.logger.phase('模拟完成')
        self.logger.log(f'最终 avg_panic={final_snapshot["avg_panic"]:.3f} '
                        f'avg_trust={final_snapshot["avg_trust"]:.3f}')
        self.logger.log(f'政府决策轨迹: {[d["selected_strategy"] for d in self.government_decisions]}')
        self.logger.log('演化后的效用矩阵:')
        for s in ['S1', 'S2', 'S3', 'S4', 'S5']:
            u = self.government_agent.utility_matrix[s]
            self.logger.log(f'  {s}: [{u[0]:+.3f}, {u[1]:+.3f}, {u[2]:+.3f}]')
        if self.government_agent.memory:
            for m in self.government_agent.memory:
                pred = m.get('predicted_effect', {})
                self.logger.log(f'  Day{m["day"]} {m["strategy"]} '
                               f'预测[p={pred.get("panic",0):+.2f} '
                               f'r={pred.get("rumor",0):+.2f} '
                               f't={pred.get("trust",0):+.2f}] '
                               f'权重={m["weights"]}')

    def _apply_agent_action(self, agent, action_type, content_info):
        """将 agent 的行动反映到 PostPool 中"""
        strict = bool(self.global_config.get('strict_comment_targets', False))
        pool = self.social_media.post_pool
        try:
            if action_type == 'comment':
                # 新事件：评论可以带 target_pid；旧事件继续挂最热帖。
                target_pid = content_info.get('target_pid')
                pid = target_pid if (strict and target_pid in pool.posts) \
                    else self._find_active_pid()
                if pid:
                    pool.add_comment(
                        pid=pid, parent_id=None,
                        comment_text=content_info.get('text', ''),
                        author_id=agent.aid,
                    )

            elif action_type == 'reply':
                parent_cid = content_info.get('target_cid')
                pid = self._find_active_pid()
                if strict and parent_cid is not None:
                    resolved = pool.find_post_by_comment(parent_cid)
                    if resolved is None:
                        self.logger.log(
                            f'Agent{agent.aid} 回复目标 cid={parent_cid} 不存在，跳过',
                            'WARN')
                        return
                    pid = resolved
                if pid and parent_cid is not None:
                    pool.add_comment(
                        pid=pid, parent_id=parent_cid,
                        comment_text=content_info.get('text', ''),
                        author_id=agent.aid,
                    )

            elif action_type == 'vote':
                cid = content_info.get('target_cid')
                if cid is not None:
                    for post in self.social_media.post_pool.posts.values():
                        if cid in post.comments:
                            if content_info.get('direction') == 'upvote':
                                post.comments[cid].like_num += 1
                            else:
                                post.comments[cid].dislike_num += 1
                            break

            elif action_type == 'share':
                target_id = content_info.get('target_id')
                if target_id is not None:
                    share_type = content_info.get('share_type', 'post')
                    if share_type == 'post' and target_id in self.social_media.post_pool.posts:
                        post = self.social_media.post_pool.posts[target_id]
                        post.view_num += 5  # 转发扩散效应
                    else:
                        # 转发评论：给帖子加浏览
                        pid = self._find_active_pid()
                        if pid and pid in self.social_media.post_pool.posts:
                            self.social_media.post_pool.posts[pid].view_num += 3

        except Exception as e:
            print(f"  [WARN] Agent {agent.aid} action 写入失败: {e}")

    def _find_active_pid(self):
        """找一个有活跃度的帖子 pid"""
        posts = sorted(self.social_media.post_pool.posts.values(),
                       key=lambda p: p.heat, reverse=True)
        if posts:
            return posts[0].pid
        return None
