"""评论打分与人格标注的 prompt 构造。

所有 prompt 都从事件上下文字典生成（见
``simulation.infra.event.event_context``），因此不绑定特定平台、语言或案例。
"""

from personas.schema import ARCHETYPES

# ============================================================================
# 打分规则（system）
# ============================================================================

_PLATFORM_LABELS = {
    'reddit': 'Reddit',
    'weibo': '微博',
    'douyin': '抖音',
    'xiaohongshu': '小红书',
    'toutiao': '今日头条',
    'bilibili': 'B站',
    'news': '新闻评论区',
}
_LANGUAGE_LABELS = {'zh': '中文', 'en': '英文', 'mixed': '中文或英文'}


def _platform_label(ctx: dict) -> str:
    raw = str(ctx.get('platform') or 'unknown')
    return _PLATFORM_LABELS.get(raw.lower(), raw or '社交媒体')


def _language_label(ctx: dict) -> str:
    raw = str(ctx.get('language') or 'zh').lower()
    return _LANGUAGE_LABELS.get(raw, raw or '中文')


def _archetype_lines(ctx: dict) -> str:
    archetypes = ctx.get('archetypes') or ARCHETYPES
    return '\n'.join(f'  - {code}: {label}' for code, label in archetypes.items())


def build_scoring_system(ctx: dict | None = None) -> str:
    """根据事件上下文生成通用评论打分 prompt。"""
    ctx = ctx or {}

    platform = _platform_label(ctx)
    language = _language_label(ctx)
    event_name = ctx.get('event_name') or '当前公共事件'
    official = ctx.get('official_body') or '负责处置此事的官方机构'
    fields = ctx.get('scoring_fields') or {}
    partisan_line = ''
    if fields.get('partisan', False):
        partisan_line = (
            'PARTISAN: <0.0-1.0>      # 评论中的党派/阵营/群体对立程度；'
            '不适用或没有表达时记 0\n'
        )

    return f"""你是一名社会科学的语料标注员。你会读到一条来自{platform}的{language}评论，\
它出现在「{event_name}」的相关讨论中。

请对这条评论做结构化标注，只输出下方规定格式的键值行，不要输出任何其他文字。

【判断立场时的关键约定】
评论中的“政府/官方”泛指**{official}**。
请把“对官方的信任”作为一个**通用维度**来打分，不要因为它具体指向某个国家或机构就拒绝打分。

【archetype 观点原型 —— 必须从下面 8 个里选且只选 1 个】
{_archetype_lines(ctx)}

  选择要点：
  - 若评论主要在讽刺、玩梗、跑题，选 detached_banter。
  - 若在追问真相但**没有**指控隐瞒，选 technical_inquiry；若有明确的“他们在遮掩”指控，选 coverup_suspicion。
  - 若作者自称本地居民或目击者，优先选 local_victim。

【输出格式 —— 严格照抄键名，每行一个，顺序不变】
ON_TOPIC: <0 或 1>        # 是否在讨论本事件本身（玩笑/纯跑题记 0）
ARCHETYPE: <上面 8 个 code 之一>
ANXIETY: <0.0-1.0>        # 焦虑/恐惧/不安的强度
ANGER: <0.0-1.0>          # 愤怒、道德义愤的强度
DISTRUST: <0.0-1.0>       # 对上述“负责处置的官方机构”的不信任程度
TACITUS: <0 或 1>         # “官方说什么我都不信”的显式表达，与普通不信任不同
{partisan_line}RUMOR: <0.0-1.0>          # 是否主张未经证实的说法（阴谋论、无来源的重病指控等）
EVIDENCE: <一句话中文说明你的判断依据，不超过 60 字>

【打分纪律】
- 一条评论里没有表现出某种情绪就是 0.0，不要“脑补”。
- 大量评论只是短评或玩笑，这时各项分数偏低、ON_TOPIC 可能为 0，这是正常的。
- 分数是连续强度，不要一律给 0.5。"""


def build_big5_system(ctx: dict | None = None) -> str:
    """生成通用的单条评论大五人格 prompt。"""
    ctx = ctx or {}

    platform = _platform_label(ctx)
    language = _language_label(ctx)
    event_name = ctx.get('event_name') or '当前公共事件'
    return f"""你是一名人格心理学标注员。你会读到一条来自{platform}的{language}评论，\
它出现在「{event_name}」的讨论中。

请判断这条评论体现了以下哪些大五人格特质。可多选，**1–3 个**：

{_BIG5_LINES}

【重要提醒】
评论的**主题和情境**会强烈影响表达方式：一个人写“这太可怕了”，可能是因为事件本身可怕，
不一定代表他高神经质。请只标注文本中**可直接观察到的表达倾向**，不要过度推断。

请严格按以下格式输出，每行一个，不要输出其他内容：
BIG5: <用逗号分隔的字母，如 O,C>
EVIDENCE: <一句话说明依据，不超过 40 字>"""


def build_author_big5_system(ctx: dict | None = None) -> str:
    """生成通用的作者级大五人格 prompt。"""
    ctx = ctx or {}

    platform = _platform_label(ctx)
    event_name = ctx.get('event_name') or '当前公共事件'
    return f"""你是一名人格心理学标注员。你会读到**同一个{platform}用户**在\
「{event_name}」讨论中发表的**全部评论**（可能只有 1 条，也可能有多条）。

请综合这些评论，判断这个用户整体上体现的**主要**大五人格倾向。只输出 1–3 个特质：

{_BIG5_LINES}

【判断规则】
- 用户消息开头会给出评论条数。若有多条，**必须综合全部评论**，不能只看第一条。
- 只有确认评论数确实为 1 时，才在 EVIDENCE 里写“仅 1 条评论”。
- 你要判断的是“这个人整体上表现出什么倾向”，不是某一条评论的情绪。
- 评论的主题和情境会强烈影响表达方式，不要过度推断。
- 如果多条评论指向不同特质，选最能概括其整体风格的那些，不要把所有特质都列上。

请严格按以下格式输出，每行一个，不要输出其他内容：
BIG5: <用逗号分隔的字母，如 O,C>
EVIDENCE: <一句话说明依据，不超过 50 字>"""


def build_scoring_user(body: str, post_title: str = '') -> str:
    """构造单条评论的打分请求。"""
    title = (post_title or '').strip()
    ctx = f'【所在帖子标题】{title}\n' if title else ''
    return f'{ctx}【待标注的评论】\n{body}'


# ============================================================================
# 输出解析
# ============================================================================

_KEYS = ['ON_TOPIC', 'ARCHETYPE', 'ANXIETY', 'ANGER', 'DISTRUST',
         'TACITUS', 'PARTISAN_US', 'PARTISAN', 'RUMOR', 'EVIDENCE']


def parse_scoring_output(text: str) -> dict:
    """
    把 LLM 的键值行输出解析成 dict（未校验，交给 schema.validate_score_record）。

    刻意不用 utils.extract_score：那个函数是为 `KEY: 0.5` 标量设计的，
    还会把 >1 的值自动缩放，既解析不了枚举，也会把整数静默缩放。
    """
    found = {}
    for line in (text or '').splitlines():
        line = line.strip().lstrip('#*-• ').strip()
        # 模型有时会输出全角冒号，先归一化
        line = line.replace('：', ':')
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key = key.strip().upper().replace(' ', '_')
        if key not in _KEYS:
            continue
        val = val.strip()
        # 去掉行尾注释
        for marker in ('  #', '\t#'):
            if marker in val:
                val = val.split(marker)[0].strip()
        found[key] = val

    rec = {}
    for k in _KEYS:
        rec[k.lower()] = found.get(k, '').strip()
    # 通用事件 prompt 使用 PARTISAN；旧缓存/schema 使用 partisan_us。
    if not rec.get('partisan_us') and rec.get('partisan'):
        rec['partisan_us'] = rec['partisan']
    return rec


# ============================================================================
# 大五人格逐条标注（用于检验"从单条评论能否可靠推断人格"）
# ============================================================================

BIG5_CODES = ['O', 'C', 'E', 'A', 'N']

BIG5_LABELS = {
    'O': 'Openness 开放性（关注新信息、技术细节，愿意探究）',
    'C': 'Conscientiousness 尽责性（强调事实、证据、责任、秩序）',
    'E': 'Extraversion 外向性（表达强烈、社交活跃、情绪外露）',
    'A': 'Agreeableness 宜人性（共情、缓和、体谅他人）',
    'N': 'Neuroticism 神经质（焦虑、恐惧、对威胁敏感、情绪波动）',
}

_BIG5_LINES = '\n'.join(f'  - {c}: {lab}' for c, lab in BIG5_LABELS.items())

def build_big5_user(body: str, post_title: str = '') -> str:
    title = (post_title or '').strip()
    ctx = f'【所在帖子标题】{title}\n' if title else ''
    return f'{ctx}【待标注的评论】\n{body}'


def parse_big5_output(text: str):
    """
    解析大五标注输出。返回 {'big5': [...], 'evidence': str}。
    无法解析或含非法 code 时抛 ValueError —— 让上层重试，而不是静默接受。
    """
    big5_raw = evidence = ''
    for line in (text or '').splitlines():
        line = line.strip().lstrip('#*-• ').strip()
        # 模型有时会输出全角冒号，先归一化
        line = line.replace('：', ':')
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key = key.strip().upper()
        if key == 'BIG5':
            big5_raw = val.strip()
        elif key == 'EVIDENCE':
            evidence = val.strip()

    if not big5_raw:
        raise ValueError(f'未解析出 BIG5，原始输出: {text[:160]!r}')

    # 容忍逗号、顿号、分号、空格、斜杠
    import re as _re
    codes = [c.upper() for c in _re.split(r'[,，、;；/\s]+', big5_raw) if c]
    codes = [c for c in codes if c in BIG5_CODES]

    # 去重保序
    seen = set()
    codes = [c for c in codes if not (c in seen or seen.add(c))]

    if not codes:
        raise ValueError(f'BIG5 中没有合法 code（应属于 {BIG5_CODES}）: {big5_raw!r}')
    if len(codes) > 3:
        raise ValueError(f'BIG5 标签数 {len(codes)} 超过 3: {codes}')

    return {'big5': codes, 'evidence': evidence[:120]}


# ============================================================================
# 作者级大五标注（把同一作者的全部评论放在一次请求里）
# ============================================================================

def build_author_big5_user(author: str, bodies: list, max_chars: int = 6000) -> str:
    """
    把同一作者的全部评论拼成一次请求。

    超长时截断：保留最早的若干条，并注明总数与截断情况。
    评论条数放在最显眼的位置 —— 实测模型会忽略不显眼的头部说明。
    """
    valid = [(b or '').strip().replace('\n', ' ') for b in bodies]
    valid = [b for b in valid if b]

    parts = []
    used = 0
    kept = 0
    for i, text in enumerate(valid, 1):
        block = f'[评论{i}] {text}'
        if used + len(block) > max_chars and kept > 0:
            break
        parts.append(block)
        used += len(block)
        kept += 1

    if kept < len(valid):
        header = (f'【用户】u/{author}\n'
                  f'该用户共有 {len(valid)} 条评论。以下为前 {kept} 条（其余因长度截断）。\n'
                  f'请综合以上 {kept} 条评论判断。')
    else:
        header = (f'【用户】u/{author}\n'
                  f'该用户共有 {len(valid)} 条评论，以下为全部。\n'
                  f'请综合全部 {len(valid)} 条评论判断。')

    return header + '\n\n' + '\n'.join(parts)
