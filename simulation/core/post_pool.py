"""
帖子池模块 — 管理多帖动态信息流。
每个 Post 有独立的评论树，PostPool 管理预定义事件 + 动态注入。
"""

import random


class Comment:
    """单条评论，支持树形回复结构"""
    def __init__(self, cid, parent_id, comment_text, author_id=None):
        self.cid = cid              # 全局唯一评论ID
        self.parent_id = parent_id  # 父评论ID（None=一级评论）
        self.comment_text = comment_text
        self.author_id = author_id  # 发评论的 agent uid

        self.like_num = 0
        self.dislike_num = 0
        self.reply_list = []        # 子回复列表 [Comment, ...]

    def __str__(self):
        return self.comment_text

    def __lt__(self, other):
        if isinstance(other, Comment):
            return self.like_num < other.like_num
        return NotImplemented

    def add_reply(self, reply_comment):
        self.reply_list.append(reply_comment)

    def get_all_replies(self):
        """递归获取所有子孙回复"""
        result = []
        for r in self.reply_list:
            result.append(r)
            result.extend(r.get_all_replies())
        return result


class Post:
    """单条帖子"""
    def __init__(self, pid, title, content, source, day):
        self.pid = pid                # 全局唯一帖子ID
        self.title = title
        self.content = content        # 新闻正文
        self.source = source          # 来源/链接
        self.day = day                # 出现在第几天（1-based）
        # 最后一次内容更新的天数。预设帖 fact/gov_comm 等于 day；
        # S3 的每日进展通报会原地更新通告正文，此时 updated_day 会推进到当天。
        self.updated_day = day
        # 预设类型：fact / gov_comm；S4 新话题为 new_topic；普通帖为 None。
        self.stimulus_type = None

        self.comments = {}            # {cid: Comment}
        self.view_num = 0
        self.like_num = 0
        self.dislike_num = 0

        # 政府通告标记：通告在信息流里始终可见（置顶），不受随机抽样影响。
        # 否则政府最重要的沟通动作只有 ~1/3 的 agent 看得到，且只看得到一次。
        self.is_notice = False

        # 新闻刺激源标记：从 post.csv 加载的预设事件。
        # 当天新增的刺激源强制全员可见 —— 它是"关键时间点的事件冲击"。
        # 不标记的话，随着帖子池累积，Day 13 当天的新事件只有约 20% 的 agent 能看到。
        self.is_stimulus = False

    @property
    def heat(self):
        """帖子热度 = 浏览 + 点赞×2 − 踩"""
        return self.view_num + self.like_num * 2 - self.dislike_num

    @property
    def comment_count(self):
        """一级评论数"""
        return len(self.top_level_comments)

    @property
    def top_level_comments(self):
        """一级评论（按点赞排序）"""
        top = [c for c in self.comments.values() if c.parent_id is None]
        top.sort(key=lambda c: c.like_num, reverse=True)
        return top

    def add_comment(self, comment):
        """添加评论"""
        self.comments[comment.cid] = comment

    def remove_comment(self, cid):
        """删除评论及其子回复"""
        if cid in self.comments:
            # 递归删除子回复
            to_remove = [cid]
            comment = self.comments[cid]
            for reply in comment.get_all_replies():
                to_remove.append(reply.cid)
            for rid in to_remove:
                if rid in self.comments:
                    del self.comments[rid]

    def get_info_snapshot(self):
        """获取帖子基本信息（给 agent 看的信息流片段）"""
        return {
            'pid': self.pid,
            'title': self.title,
            'source': self.source,
            'view_num': self.view_num,
            'like_num': self.like_num,
            'comment_count': self.comment_count,
        }


class PostPool:
    """
    帖子池 — 管理整个模拟期间的帖子。
    支持预定义事件（从 CSV 加载）+ 动态注入（政府 S4 等）。
    """
    def __init__(self, config, llm=None):
        self.config = config
        self.llm = llm
        self.posts = {}           # {pid: Post}
        self._next_pid = 1
        self._next_cid = 1

        # 加载预定义帖子
        self._load_preset_posts()

    def _load_preset_posts(self):
        """
        从 CSV 加载预定义事件。

        根据 stimulus_config.mode 过滤：
          replay          fact + gov_comm（重演真实历史）
          counterfactual  仅 fact（政府表态由模拟 agent 产生）
        """
        from simulation.infra.utils import load_posts

        mode = self.config.get('stimulus_config', {}).get('mode', 'replay')
        post_list = load_posts(self.config)

        loaded = skipped = 0
        for p in post_list:
            stype = (p.get('stimulus_type') or 'fact').strip()
            if mode == 'counterfactual' and stype == 'gov_comm':
                # 反事实模式：去掉历史政府的表态，避免与模拟政府的动作叠加
                skipped += 1
                continue

            day = int(p.get('day', 1))
            self.add_post(
                title=p['title'],
                content=p.get('news_content', ''),
                source=p.get('source', ''),
                day=day,
                is_stimulus=True,     # 预设新闻事件：出现当天强制全员可见
                stimulus_type=stype,  # fact / gov_comm，供时效分组识别
            )
            loaded += 1

        self.stimulus_mode = mode
        self.stimulus_skipped = skipped

    def add_post(self, title, content, source, day, is_notice=False,
                 is_stimulus=False, stimulus_type=None):
        """添加帖子，返回 pid。

        is_notice=True   政府通告，信息流中始终置顶
        is_stimulus=True 预设新闻事件，在它出现的当天强制全员可见
        stimulus_type    fact / gov_comm / new_topic，用于信息流时效分组
        """
        pid = self._next_pid
        self._next_pid += 1
        post = Post(pid, title, content, source, day)
        post.is_notice = is_notice
        post.is_stimulus = is_stimulus
        post.stimulus_type = stimulus_type
        self.posts[pid] = post
        return pid

    def add_comment(self, pid, parent_id, comment_text, author_id=None):
        """添加评论，返回 cid"""
        cid = self._next_cid
        self._next_cid += 1
        comment = Comment(cid, parent_id, comment_text, author_id)
        if pid in self.posts:
            self.posts[pid].add_comment(comment)
            # 如果是回复，同时挂到父评论下
            if parent_id is not None:
                for post in self.posts.values():
                    if parent_id in post.comments:
                        post.comments[parent_id].add_reply(comment)
                        break
        return cid

    def find_comment(self, cid):
        """按 cid 找到 (pid, Comment)；找不到返回 None。"""
        for pid, post in self.posts.items():
            comment = post.comments.get(cid)
            if comment is not None:
                return pid, comment
        return None

    def find_post_by_comment(self, cid):
        """按 cid 反查所属帖子 pid；找不到返回 None。"""
        found = self.find_comment(cid)
        return found[0] if found else None

    def get_posts_by_day(self, day, day_range=0):
        """
        获取某天可见的帖子列表。
        day_range=0 表示只看当天发布的帖子
        day_range>0 表示看 day ± range 内的帖子（模拟帖子存留）
        """
        visible = []
        for post in self.posts.values():
            if day - day_range <= post.day <= day:
                visible.append(post)
        visible.sort(key=lambda p: p.heat, reverse=True)
        return visible

    def get_all_active_posts(self, current_day):
        """获取当天所有活跃帖子（从帖子出生日到今天）"""
        return self.get_posts_by_day(current_day, day_range=current_day)

    def get_info_flow(self, current_day, max_posts=5, max_comments_per_post=10,
                      shuffle_subset=True, seed=None):
        """
        生成 agent 看到的信息流。
        随机抽样 max_posts 条帖子，每条取点赞最高的 max_comments_per_post 条评论。

        Returns:
            list of dict: 每项包含帖子和其评论快照
        """
        all_posts = self.get_all_active_posts(current_day)
        if not all_posts:
            return []

        # ---- 信息流选取：置顶项 + 半热度 + 半随机 ----
        # 置顶项 = 政府通告 + **当天新增的新闻刺激源**。
        #   通告置顶：否则政府最重要的沟通只有约 1/3 的 agent 看得到。
        #   当天刺激源置顶：事件冲击应当全员可见；不置顶时随着帖子池累积，
        #   Day 13 当天的新事件只有约 20% 的 agent 能看到，等于阶段刺激失效。
        # 其余帖子里一半按热度取、一半随机，保留个体差异。
        pinned = [p for p in all_posts
                  if p.is_notice or (p.is_stimulus and p.day == current_day)]

        # 最新消息优先：今天发布/更新的置顶项排前面；旧通告最多保留
        # max_pinned_old_notices 条，其余回到 rest 参与热度/随机竞争。
        # 否则历史通告会按热度挤掉当天最新的事实进展。
        pinned.sort(
            key=lambda p: (
                getattr(p, 'updated_day', p.day) == current_day,
                getattr(p, 'updated_day', p.day),
                p.heat,
            ),
            reverse=True,
        )
        max_old_notices = max(
            0, int(self.config.get('agent_config', {}).get(
                'max_pinned_old_notices', 1)))
        kept, old_notices = [], 0
        for p in pinned:
            if getattr(p, 'updated_day', p.day) == current_day:
                kept.append(p)
            elif old_notices < max_old_notices:
                kept.append(p)
                old_notices += 1
        pinned = kept

        pinned_ids = {p.pid for p in pinned}
        rest = [p for p in all_posts if p.pid not in pinned_ids]

        selected = pinned[:max_posts]
        remaining = max_posts - len(selected)

        if remaining > 0 and rest:
            rest_sorted = sorted(rest, key=lambda p: p.heat, reverse=True)
            n_hot = remaining // 2
            hot = rest_sorted[:n_hot]
            pool = rest_sorted[len(hot):]

            n_cold = remaining - len(hot)
            if shuffle_subset and len(pool) > n_cold:
                rng = random.Random(seed)
                cold = rng.sample(pool, n_cold)
            else:
                cold = pool[:n_cold]
            selected = selected + hot + cold

        # 曝光即浏览：被 agent 看到的帖子累加浏览量（自然曝光效应）
        for post in selected:
            post.view_num += 1

        flow = []
        for post in selected:
            top_comments = post.top_level_comments[:max_comments_per_post]
            comment_snapshots = []
            for c in top_comments:
                snap = {
                    'cid': c.cid,
                    'text': c.comment_text,
                    'author_id': c.author_id,     # 用于标记"你的评论"
                    'like_num': c.like_num,
                    'dislike_num': c.dislike_num,
                    'replies': [{'cid': r.cid, 'text': r.comment_text,
                                 'author_id': r.author_id,
                                 'like_num': r.like_num}
                                for r in c.reply_list[:3]],  # 回复最多展示3条
                }
                comment_snapshots.append(snap)

            updated_day = getattr(post, 'updated_day', post.day)
            flow.append({
                'pid': post.pid,
                'day': post.day,                  # 首次发布日
                'updated_day': updated_day,       # 最后一次内容更新日
                'age_days': max(0, current_day - updated_day),
                'is_stimulus': post.is_stimulus,
                'stimulus_type': getattr(post, 'stimulus_type', None),
                'title': post.title,
                'content': post.content,
                'source': post.source,            # 用于区分官方发布/网友
                'is_notice': post.is_notice,
                'view_num': post.view_num,
                'like_num': post.like_num,
                'comments': comment_snapshots,
            })

        return flow

    def get_day_summary(self, current_day):
        """获取当天帖子池的统计摘要（供舆情分析师使用）"""
        all_posts = self.get_all_active_posts(current_day)
        total_comments = sum(p.comment_count for p in all_posts)
        total_views = sum(p.view_num for p in all_posts)

        # 点赞要**同时统计帖子级和评论级**。
        # agent 的 vote 动作是记在评论上的，只统计帖子级会让分析师看不到真实的
        # 互动热度（原来 total_likes 恒等于注入通告的初始值，就是这个原因）。
        post_likes = sum(p.like_num for p in all_posts)
        comment_likes = sum(c.like_num for p in all_posts
                            for c in p.comments.values())
        total_likes = post_likes + comment_likes

        # 收集所有评论
        all_comments = []
        for p in all_posts:
            for c in p.comments.values():
                all_comments.append({
                    'pid': p.pid,
                    'cid': c.cid,
                    'text': c.comment_text,
                    'like_num': c.like_num,
                    'is_reply': c.parent_id is not None,
                })

        # 按点赞排序
        all_comments.sort(key=lambda x: x['like_num'], reverse=True)

        return {
            'day': current_day,
            'active_posts': len(all_posts),
            'total_comments': total_comments,
            'total_views': total_views,
            'total_likes': total_likes,
            'post_likes': post_likes,
            'comment_likes': comment_likes,
            'all_comments': all_comments,
            'top_posts': [p.get_info_snapshot() for p in all_posts[:5]],
        }
