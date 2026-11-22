import random
import re
import warnings
from simulation.core.mechanism import (
    get_action_prompt, get_comment_prompt, get_reply_prompt,
    get_share_prompt, get_vote_prompt, get_reflection_prompt,
    summarize_info_flow
)
from simulation.infra.utils import extract_score, extract_pid_list
from simulation.infra.exceptions import LLMException


# ============================================================================
# 旧版 fallback：大五人格 → 初始心理状态的效应权重
# ============================================================================
# ⚠️ 2026-10-19：主路径已改为「LLM 根据画像生成三个初值 + 小随机扰动」，
# 初值缓存在 events/<id>/processed/initial_states.csv。
# 下面的加性模型只在缺少 LLM 初值时作为 fallback，保留是为了兼容旧数据；
# It is now only a fallback.
#
# 方向依据人格心理学中较一致的结论：
#   · Neuroticism（情绪不稳定性 / 威胁敏感）→ 更易恐慌、更不信任、更易轻信
#   · Agreeableness（宜人性 / 合作倾向）    → 更信任、较少恐慌
#   · Openness / Conscientiousness          → 更审慎、更不易轻信未经证实的信息
#   · Extraversion                          → 情绪更积极、信任略高
BIG_FIVE_EFFECTS = {
    #                    panic   trust   rumor
    'Openness':          (-0.05, +0.02, -0.08),
    'Conscientiousness': (-0.05, +0.03, -0.08),
    'Extraversion':      (-0.04, +0.04, +0.02),
    'Agreeableness':     (-0.04, +0.08, -0.03),
    'Neuroticism':       (+0.12, -0.06, +0.10),
}

# 旧版 fallback 的校准常数：使加性模型的 40 人均值接近旧版均匀随机初始化
# （panic≈0.21 / trust≈0.46 / rumor≈0.35）。它不是“中性心理状态”的测量值，
# These constants are not calibrated observations.
INIT_BASE = (0.213, 0.441, 0.391)   # panic, trust, rumor
INIT_NOISE = 0.03                   # fallback 的默认个体噪声


def _clip01(x):
    return max(0.0, min(1.0, x))


def initial_state_from_big_five(personality, config):
    """
    旧版 fallback：由大五标签推导初始 (panic, trust, rumor)。

    主路径已改为 LLM 生成 + 噪声；只有在 profile 缺少 init_* 字段时才调用本函数。
    输入形如 "Openness；Conscientiousness"。缺失/无法解析时回退到 config 的均匀范围。
    """
    text = str(personality or '').replace(';', '；')
    labels = [x.strip() for x in text.split('；') if x.strip()]
    noise_sd = config.get('initial_noise', INIT_NOISE)

    if not labels:
        return (random.uniform(*config.get('initial_panic', [0.1, 0.3])),
                random.uniform(*config.get('initial_trust', [0.3, 0.7])),
                random.uniform(*config.get('initial_rumor', [0.2, 0.5])))

    d = [0.0, 0.0, 0.0]
    for lab in labels:
        eff = BIG_FIVE_EFFECTS.get(lab)
        if eff:
            for i in range(3):
                d[i] += eff[i]

    return tuple(_clip01(INIT_BASE[i] + d[i] + random.gauss(0.0, noise_sd))
                 for i in range(3))


class Agent:
    """网民 Agent：恐慌指数 + 谣言易感性 + 政府信任度 三变量认知循环"""

    def __init__(self, config, profile, llm):
        self.global_config = config
        self.config = config['agent_config']
        self.llm = llm

        # ---- 静态属性 ----
        self.aid = int(profile['uid'])
        self.name = profile['name']
        self.age = profile['age']
        self.gender = profile['gender']
        self.personality = profile['big_five']
        # moral_foundation 已于 2026-10-19 删除；不再进入 profile 或 prompt。
        self.activity = profile['activity']  # High / Middle / Low

        # ---- 动态状态 ----
        # 主路径：LLM 根据画像生成三个初值（initial_states.csv），再叠加个体噪声。
        # 没有 LLM 初值时回退到旧版大五加性模型，并给出警告。
        noise_sd = self.config.get('initial_noise', INIT_NOISE)
        if all(k in profile for k in ('init_panic', 'init_trust', 'init_rumor')):
            self.panic = _clip01(float(profile['init_panic'])
                                 + random.gauss(0.0, noise_sd))
            self.government_trust = _clip01(float(profile['init_trust'])
                                            + random.gauss(0.0, noise_sd))
            self.rumor_susceptibility = _clip01(float(profile['init_rumor'])
                                                + random.gauss(0.0, noise_sd))
            self.initial_source = 'llm'
        else:
            warnings.warn(
                'profile 缺少 init_panic/init_trust/init_rumor，'
                '回退到旧的大五加性模型；请运行 '
                'personas/generate_initial_states.py 生成 LLM 初值')
            self.panic, self.government_trust, self.rumor_susceptibility = \
                initial_state_from_big_five(self.personality, self.config)
            self.initial_source = 'big5_fallback'

        # ---- 心理基线（用于回归项）----
        # 人的心理状态有回归性：不会因为持续接触同类信息而无限恐慌下去。
        # 没有这一项时，实测每次反思 panic 都单调 +0.027（100% 正向），
        # Without it, long runs tend to saturate and strategies become indistinguishable.
        self.panic_baseline = self.panic
        self.trust_baseline = self.government_trust
        self.rumor_baseline = self.rumor_susceptibility

        # ---- 行动历史 ----
        self.action_history = []

    def recover_llm(self, llm):
        self.llm = llm

    # ================================================================
    # 三步认知循环
    # ================================================================

    def perceive_and_act(self, info_flow, day, agent_action_record):
        """
        感知 → 行动（含内容生成）。

        Args:
            info_flow: PostPool.get_info_flow() 的返回值
            day: 当前天
            agent_action_record: dict，存储本轮的 prompt/response 记录

        Returns:
            action_result: dict，含 action_type, content, target_id 等
        """
        record = {}

        # Step 1: 感知 + 选择行动
        # 传入 agent 和 day：摘要里会标出「今日新增」「官方发布」「你的评论」
        flow_summary = summarize_info_flow(info_flow, agent=self, current_day=day)
        action_prompt = get_action_prompt(self, flow_summary)
        record['action_prompt'] = action_prompt

        # 行动是离散选择（0-4），用低温度降低随机噪声；
        # 内容生成仍用默认的较高温度保留多样性。
        action_raw = self.llm.fast_run(action_prompt, temperature=0.3)
        record['action_response'] = action_raw

        # 解析行动编号
        action_code = self._parse_action_code(action_raw)
        action_map = {
            0: 'comment',
            1: 'reply',
            2: 'vote',
            3: 'share',
            4: 'silent',
        }
        action_type = action_map.get(action_code, 'silent')

        # Step 2: 如果需要，生成内容
        content_info = {}
        if action_type == 'comment':
            comment_prompt = get_comment_prompt(self, flow_summary)
            record['content_prompt'] = comment_prompt
            comment_text = self.llm.fast_run(comment_prompt)
            record['content_response'] = comment_text
            content_info = {'type': 'comment', 'text': comment_text, 'target_pid': None}

        elif action_type == 'reply':
            reply_prompt = get_reply_prompt(self, flow_summary)
            record['content_prompt'] = reply_prompt
            reply_text = self.llm.fast_run(reply_prompt)
            record['content_response'] = reply_text
            parsed = self._parse_reply_output(reply_text)
            content_info = {
                'type': 'reply',
                'text': parsed.get('reply', ''),
                'target_cid': parsed.get('comment_id'),
            }

        elif action_type == 'vote':
            vote_prompt = get_vote_prompt(self, flow_summary)
            record['content_prompt'] = vote_prompt
            vote_text = self.llm.fast_run(vote_prompt)
            record['content_response'] = vote_text
            parsed = self._parse_vote_output(vote_text)
            content_info = {
                'type': 'vote',
                'direction': parsed.get('vote_type', 'upvote'),
                'target_cid': parsed.get('comment_id'),
            }

        elif action_type == 'share':
            share_prompt = get_share_prompt(self, flow_summary)
            record['content_prompt'] = share_prompt
            share_text = self.llm.fast_run(share_prompt)
            record['content_response'] = share_text
            parsed = self._parse_share_output(share_text)
            content_info = {
                'type': 'share',
                'share_type': parsed.get('share_type', 'post'),
                'target_id': parsed.get('share_id'),
                'note': parsed.get('forward_note', ''),
            }

        else:  # silent
            content_info = {'type': 'silent'}

        agent_action_record['action_detail'] = record
        agent_action_record['content_info'] = content_info

        return action_type, content_info, flow_summary

    def reflect(self, action_type, content_info, flow_summary,
                reflection_record, current_day=None, info_flow=None):
        """
        更新状态：LLM 根据当天的浏览和行动，更新 panic / trust / rumor。

        Args:
            action_type: str
            content_info: dict
            flow_summary: str (summarize_info_flow 的文本输出)
            reflection_record: dict，存储 prompt/response 记录
            current_day: 当前天，用于时效性 prompt
            info_flow: 结构化信息流，用于记录模型实际看到的 pid 与年龄
        """
        action_desc = self._describe_action(action_type, content_info)
        reflection_prompt = get_reflection_prompt(
            self, action_desc, flow_summary, current_day=current_day)
        reflection_record['prompt'] = reflection_prompt

        response = self.llm.fast_run(reflection_prompt)
        response_text = response if isinstance(response, str) else str(response or '')
        reflection_record['response'] = response_text
        reflection_record['previous_state'] = {
            'panic': self.panic,
            'government_trust': self.government_trust,
            'rumor_susceptibility': self.rumor_susceptibility,
        }

        # ---- 记录时效性判断（不参与三值计算，只用于审计与分析） ----
        max_age = self.config.get('freshness_max_age_days', 2)
        try:
            max_age = max(0, int(max_age))
        except (TypeError, ValueError):
            max_age = 2

        info_meta = []
        for item in (info_flow or []):
            updated = item.get('updated_day', item.get('day'))
            age = item.get('age_days')
            if age is None and current_day is not None and updated is not None:
                age = max(0, current_day - updated)
            info_meta.append({
                'pid': int(item['pid']),
                'day': item.get('day'),
                'updated_day': updated,
                'age_days': age,
                'is_stimulus': bool(item.get('is_stimulus')),
                'stimulus_type': item.get('stimulus_type'),
                'is_notice': bool(item.get('is_notice')),
            })

        valid_pids = [m['pid'] for m in info_meta]
        latest_raw = extract_pid_list(response_text, 'LATEST_POSTS')
        outdated_raw = extract_pid_list(response_text, 'OUTDATED_POSTS')
        if valid_pids:
            latest = extract_pid_list(response_text, 'LATEST_POSTS', valid_pids)
            outdated = extract_pid_list(response_text, 'OUTDATED_POSTS', valid_pids)
        else:
            latest, outdated = latest_raw, outdated_raw

        reflection_record['freshness'] = {
            'current_day': current_day,
            'max_age_days': max_age,
            'info_flow_pids': valid_pids,
            'info_flow_meta': info_meta,
            'latest_posts': latest,
            'outdated_posts': outdated,
            'latest_posts_raw': latest_raw,
            'outdated_posts_raw': outdated_raw,
            'latest_key_present': 'LATEST_POSTS' in response_text.upper(),
            'outdated_key_present': 'OUTDATED_POSTS' in response_text.upper(),
            'outdated_labeled_pids': [
                m['pid'] for m in info_meta
                if m['age_days'] is not None and m['age_days'] > max_age
            ],
        }

        # 提取结构化输出
        new_panic = extract_score(response_text, 'PANIC_SCORE')
        new_trust = extract_score(response_text, 'TRUST_SCORE')
        new_rumor = extract_score(response_text, 'RUMOR_SCORE')

        # 心理回归（习惯化）：观测值向自身基线温和回落。
        # 实测没有这一项时，每次反思 panic 都 +0.027 且 100% 正向 —— 信息流里
        # With only alarming content, panic would drift upward without bound.
        # 真实数据里 distrust 是"先涨后平"（P1 0.190→P2 0.295→P3 0.293→P4 0.306），
        # 这一项正是用来复现那个平台期的；系数可在 config 里标定。
        rho = self.global_config['agent_config'].get('affect_reversion', 0.0)

        if new_panic is not None:
            self.panic = (1 - rho) * new_panic + rho * self.panic_baseline
        else:
            e = LLMException('Agent-Reflection-Fail-Panic',
                           reflection_prompt, response)
            warnings.warn(e.__str__())

        if new_trust is not None:
            self.government_trust = (1 - rho) * new_trust + rho * self.trust_baseline
        else:
            e = LLMException('Agent-Reflection-Fail-Trust',
                           reflection_prompt, response)
            warnings.warn(e.__str__())

        if new_rumor is not None:
            self.rumor_susceptibility = (1 - rho) * new_rumor + rho * self.rumor_baseline
        else:
            e = LLMException('Agent-Reflection-Fail-Rumor',
                           reflection_prompt, response)
            warnings.warn(e.__str__())

        reflection_record['new_state'] = {
            'panic': self.panic,
            'government_trust': self.government_trust,
            'rumor_susceptibility': self.rumor_susceptibility,
        }

    def take_action(self, info_flow, day):
        """
        对外统一接口：感知→行动→反思。

        Returns:
            (action_type, content_info, action_record)
        """
        action_record = {}
        action_type, content_info, flow_summary = self.perceive_and_act(
            info_flow, day, action_record
        )
        reflection_record = {}
        self.reflect(action_type, content_info, flow_summary, reflection_record,
                    current_day=day, info_flow=info_flow)
        action_record['reflection'] = reflection_record
        action_record['day'] = day

        # ---- 记忆：记录自己说过的话 ----
        # 原来 action_history 只初始化、从不写入，导致 agent 每天重新"现编"立场，
        # 可能出现昨天骂政府、今天夸政府这种前后矛盾。这里落盘供后续 prompt 引用。
        text = ((content_info or {}).get('text')
                or (content_info or {}).get('note') or '').strip()
        if action_type in ('comment', 'reply', 'share') and text:
            self.action_history.append({
                'day': day,
                'action': action_type,
                'text': text[:120],
            })

        return action_type, content_info, action_record

    def recent_memory_text(self, n=2):
        """最近发布的 1–2 条内容，用于在 prompt 中提供一致性锚点。"""
        recent = [a for a in self.action_history
                  if a.get('action') in ('comment', 'reply', 'share')
                  and (a.get('text') or '').strip()][-n:]
        if not recent:
            return ''
        return '\n'.join(f"第{a['day']}天你说过：{a['text'][:80]}" for a in recent)

    # ================================================================
    # 解析辅助函数
    # ================================================================

    def _parse_action_code(self, raw):
        """从 LLM 输出中提取行动编号 0-4"""
        if not raw:
            return 4  # LLM 输出为空，默认沉默
        digits = re.findall(r'\d', raw)
        if not digits:
            return 4  # 默认沉默
        code = int(digits[0])
        if 0 <= code <= 4:
            return code
        return 4

    def _parse_reply_output(self, text):
        """解析回复输出：COMMENT_ID + REPLY"""
        if not text:
            return {'comment_id': None, 'reply': ''}
        import re as _re
        pattern = r"COMMENT_ID:\s*(\d+)\s*REPLY:\s*([\s\S]*)"
        match = _re.search(pattern, text)
        if match:
            return {
                'comment_id': int(match.group(1)),
                'reply': match.group(2).strip()
            }
        # fallback: 尝试提取数字
        digits = _re.findall(r'\d+', text)
        return {
            'comment_id': int(digits[0]) if digits else None,
            'reply': text
        }

    def _parse_vote_output(self, text):
        """解析赞/踩输出"""
        if not text:
            return {'vote_type': 'upvote', 'comment_id': None}
        up = 'upvote' in text.lower() or '点赞' in text
        down = 'downvote' in text.lower() or '点踩' in text
        vote_type = 'downvote' if down else 'upvote'

        digits = re.findall(r'COMMENT_ID:\s*(\d+)', text)
        if not digits:
            digits = re.findall(r'\d+', text)
        comment_id = int(digits[0]) if digits else None

        return {'vote_type': vote_type, 'comment_id': comment_id}

    def _parse_share_output(self, text):
        """解析转发输出"""
        if not text:
            return {'share_type': 'post', 'share_id': None, 'forward_note': ''}
        share_type = 'comment' if 'comment' in text.lower() else 'post'

        type_match = re.findall(r'SHARE_TYPE:\s*(\w+)', text)
        if type_match:
            share_type = type_match[0].lower()

        id_match = re.findall(r'SHARE_ID:\s*(\d+)', text)
        share_id = int(id_match[0]) if id_match else None

        note_match = re.findall(r'FORWARD_NOTE:\s*(.*)', text)
        note = note_match[0].strip() if note_match else ''

        return {'share_type': share_type, 'share_id': share_id, 'forward_note': note}

    def _describe_action(self, action_type, content_info):
        """生成行动的自然语言描述（给 reflection prompt 用）"""
        if action_type == 'comment':
            return f"发表了一条评论：'{content_info.get('text', '')}'"
        elif action_type == 'reply':
            return f"回复了评论[cid={content_info.get('target_cid')}]：'{content_info.get('text', '')}'"
        elif action_type == 'vote':
            direction = '点赞' if content_info.get('direction') == 'upvote' else '点踩'
            return f"给评论[cid={content_info.get('target_cid')}]{direction}"
        elif action_type == 'share':
            note = content_info.get('note', '')
            note_str = f"，附言：'{note}'" if note else ""
            return f"转发了{content_info.get('share_type', '内容')}[id={content_info.get('target_id')}]{note_str}"
        else:
            return "浏览了信息，没有进行任何互动（保持沉默）"
