"""
画像流水线的规范定义 —— 所有封闭标签集与校验的唯一真源。

任何一步要用到"合法取值"都从这里 import，不要在别处另写一份。
"""

import re

# ============================================================================
# 分隔符
# ============================================================================

# 多标签用全角分号分隔。注意：绝不能用普通逗号 ','，否则 pandas 会把它当成列分隔符。
SEP = '；'          # U+FF1B
ASCII_SEP = ';'     # 归一化时也会接受

# ============================================================================
# user.csv columns
# ============================================================================

USER_COLUMNS = ['uid', 'name', 'age', 'gender', 'big_five', 'activity']

# ============================================================================
# 封闭标签集
# ============================================================================

GENDERS = {'Male', 'Female'}

ACTIVITIES = {'High', 'Middle', 'Low'}      # 必须与 config.agent_config.activity_prob 的键完全一致

BIG_FIVE = {
    'Openness',
    'Conscientiousness',
    'Extraversion',
    'Agreeableness',
    'Neuroticism',
}

# ============================================================================
# 观点原型：8 类封闭标签
# ============================================================================
# 用 ASCII code 作为机器标签、中文作为人读标签 —— 让 LLM 输出 ASCII code，
# 比让它逐字复现中文标签可靠得多。

ARCHETYPES = {
    'justice_anger':     '公正愤怒/追责型',
    'coverup_suspicion': '掩盖阴谋怀疑型',
    'technical_inquiry': '技术求证/科学理性型',
    'local_victim':      '地方受害/亲历型',
    'health_anxiety':    '环境健康焦虑型',
    'official_defense':  '官方辩护/信任型',
    'info_seeking':      '信息求助型',
    'detached_banter':   '冷漠调侃/旁观型',
}

MIN_ARCHETYPE_LABELS = 1
MAX_ARCHETYPE_LABELS = 3

# ============================================================================
# 评论打分字段
# ============================================================================

SCORE_FIELDS = ['comment_id', 'on_topic', 'archetype', 'anxiety',
                'anger', 'distrust', 'tacitus', 'partisan_us', 'rumor']

FLOAT_FIELDS = ['anxiety', 'anger', 'distrust', 'partisan_us', 'rumor']
INT_FIELDS = ['on_topic', 'tacitus']

# ============================================================================
# 阶段划分
# ============================================================================


def phase_of(published: str, phase_boundaries) -> str:
    """Map a published timestamp to a phase using explicit (id, cutoff) pairs."""
    if not phase_boundaries:
        return 'P1'
    d = (published or '')[:10]
    if not re.match(r'\d{4}-\d{2}-\d{2}', d):
        return str(phase_boundaries[0][0])
    for name, cutoff in phase_boundaries:
        if d <= cutoff:
            return str(name)
    return str(phase_boundaries[-1][0])


# ============================================================================
# 校验
# ============================================================================


class SchemaError(ValueError):
    """画像数据不满足仿真要求。"""


def split_labels(value: str) -> list:
    """把 'A；B；C' 拆成 ['A','B','C']，同时容忍半角分号。"""
    if not value:
        return []
    text = value.replace(ASCII_SEP, SEP)
    return [p.strip() for p in text.split(SEP) if p.strip()]


def _check_labels(field, value, canonical, allow_empty=False):
    labels = split_labels(value)
    if not labels:
        if allow_empty:
            return
        raise SchemaError(f'{field} 不能为空')
    if len(labels) != len(set(labels)):
        dupes = [x for x in set(labels) if labels.count(x) > 1]
        raise SchemaError(f'{field} 有重复标签: {dupes}')
    bad = [x for x in labels if x not in canonical]
    if bad:
        raise SchemaError(f'{field} 含非法标签: {bad}')
    if field == 'big_five':
        if len(labels) > MAX_ARCHETYPE_LABELS:
            raise SchemaError(f'{field} 标签数 {len(labels)} 超过上限 {MAX_ARCHETYPE_LABELS}')


def validate_user_row(row: dict) -> None:
    """校验一行 persona 是否满足仿真加载要求。不通过则抛 SchemaError。"""
    missing = [c for c in USER_COLUMNS if c not in row]
    if missing:
        raise SchemaError(f'缺列: {missing}')

    # uid
    try:
        uid = int(row['uid'])
    except (TypeError, ValueError):
        raise SchemaError(f"uid 不是整数: {row['uid']!r}")
    if uid < 1:
        raise SchemaError(f'uid 必须 >= 1，得到 {uid}')

    # name / age / gender
    if not str(row['name']).strip():
        raise SchemaError('name 不能为空')
    try:
        age = int(row['age'])
    except (TypeError, ValueError):
        raise SchemaError(f"age 不是整数: {row['age']!r}")
    if not (13 <= age <= 100):
        raise SchemaError(f'age 超出合理范围: {age}')
    if row['gender'] not in GENDERS:
        raise SchemaError(f"gender 非法: {row['gender']!r}（必须是 {sorted(GENDERS)}）")

    # activity —— 必须精确匹配，否则 is_agent_active_today 会静默退化成 0.7
    if row['activity'] not in ACTIVITIES:
        raise SchemaError(
            f"activity 非法: {row['activity']!r}（必须是 {sorted(ACTIVITIES)}，"
            f'否则会静默退化成默认概率 0.7）')

    # 多标签字段
    _check_labels('big_five', row['big_five'], BIG_FIVE)

    # CSV 安全性：字段内不能有逗号/引号/换行
    for col in USER_COLUMNS:
        val = str(row[col])
        for ch, name in ((',', '逗号'), ('"', '双引号'), ('\n', '换行'), ('\r', '回车')):
            if ch in val:
                raise SchemaError(f'{col} 含{name}，会破坏 CSV')


def validate_score_record(rec: dict) -> dict:
    """校验并归一化一条评论打分记录。不合法则抛 SchemaError。"""
    if rec.get('archetype') not in ARCHETYPES:
        raise SchemaError(f"archetype 非法: {rec.get('archetype')!r}")

    out = {'archetype': rec['archetype']}
    # comment_id 由调用方（llm_wrapper）在校验通过后附加 —— LLM 看不到它，故这里可选
    if rec.get('comment_id'):
        out['comment_id'] = rec['comment_id']

    for f in INT_FIELDS:
        v = rec.get(f)
        if isinstance(v, bool):
            v = int(v)
        try:
            v = int(float(v))
        except (TypeError, ValueError):
            raise SchemaError(f'{f} 无法解析为 0/1: {rec.get(f)!r}')
        if v not in (0, 1):
            raise SchemaError(f'{f} 必须是 0 或 1，得到 {v}')
        out[f] = v

    for f in FLOAT_FIELDS:
        raw = rec.get(f)
        if f == 'partisan_us' and (raw is None or raw == ''):
            # 非美国语境的事件不要求党派字段；缺失按 0 处理。
            out[f] = 0.0
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            raise SchemaError(f'{f} 无法解析为浮点: {raw!r}')
        out[f] = min(1.0, max(0.0, v))     # 夹到 [0,1]

    out['evidence'] = str(rec.get('evidence', ''))[:200]
    return out
