"""
Prompt 工程模块 — 网民 Agent 全部 prompt。
行动选择、内容生成、状态更新。
"""

# ============================================================
# 记忆块（观点原型已彻底移除，不再注入立场倾向）
# ============================================================

def _memory_block(agent):
    """最近说过的话，用于保持一致；无记忆时返回空串。"""
    fn = getattr(agent, 'recent_memory_text', None)
    mem = fn() if callable(fn) else ''
    return f"[你最近说过的话]\n{mem}\n" if mem else ''

# ============================================================
# 行动选择 Prompt
# ============================================================

def get_action_prompt(agent, info_flow_summary):
    """
    Agent 看完信息流后，选择 5 种行动之一。
    返回决策数字。
    """
    prompt = f"""
请扮演以下角色，你正在浏览社交媒体上的热点内容。

[你的基本画像]
姓名：{agent.name}
年龄：{agent.age}
性别：{agent.gender}
人格特质（大五）：{agent.personality}
社交活跃度：{agent.activity}
{_memory_block(agent)}
[你当前的心理状态]
恐慌程度（0=完全平静，1=极度恐慌）：{agent.panic:.2f}
对政府的信任程度（0=完全不信任，1=完全信任）：{agent.government_trust:.2f}
对不实信息的易感程度（0=有辨别力，1=容易轻信转发）：{agent.rumor_susceptibility:.2f}

[你今天浏览到的信息流摘要]
{info_flow_summary}

请基于你的性格特质、当前心理状态和浏览到的信息，选择你要做什么：

[0] 发表评论 — 在帖子下表达你的看法
[1] 回复评论 — 回复一条已有评论
[2] 赞或踩 — 给某条评论点赞或点踩
[3] 转发 — 将某条内容转发出去
[4] 保持沉默 — 只浏览，不互动

请只输出你选择的数字（0-4），不输出任何其他内容。
输出示例：
2
"""
    return prompt


# ============================================================
# 内容生成 Prompt
# ============================================================

def get_comment_prompt(agent, info_flow_summary):
    """生成评论内容（约30字，第一人称）"""
    prompt = f"""
请扮演以下角色，你正在社交媒体上浏览热点内容。

[你的基本画像]
姓名：{agent.name}
年龄：{agent.age}
性别：{agent.gender}
人格特质：{agent.personality}
{_memory_block(agent)}
[你当前的心理状态]
恐慌程度（0=完全平静，1=极度恐慌）：{agent.panic:.2f}
对政府的信任程度（0=完全不信任，1=完全信任）：{agent.government_trust:.2f}
对不实信息的易感程度（0=有辨别力，1=容易轻信转发）：{agent.rumor_susceptibility:.2f}

[你浏览到的信息]
{info_flow_summary}

请以第一人称发表一条评论，大约30字。评论应符合你的性格特质和当前情绪状态。
只输出评论内容，不输出任何前缀或说明。
输出示例：
这种事情还能发生多少次？监管部门到底在干什么，老百姓的安全谁负责？
"""
    return prompt


def get_reply_prompt(agent, info_flow_summary):
    """选择要回复的评论 + 生成回复内容"""
    prompt = f"""
请扮演以下角色，你正在社交媒体上浏览热点内容。

[你的基本画像]
姓名：{agent.name}
年龄：{agent.age}
性别：{agent.gender}
人格特质：{agent.personality}
{_memory_block(agent)}
[你当前的心理状态]
恐慌程度（0=完全平静，1=极度恐慌）：{agent.panic:.2f}
对政府的信任程度（0=完全不信任，1=完全信任）：{agent.government_trust:.2f}
对不实信息的易感程度（0=有辨别力，1=容易轻信转发）：{agent.rumor_susceptibility:.2f}

[你浏览到的信息和评论]
{info_flow_summary}

请先选择你要回复的评论编号（cid），然后以第一人称写一条约30字的回复。
回复应符合你的性格特质和当前情绪状态。

输出格式（严格按此格式）：
COMMENT_ID: <cid>
REPLY: <回复内容>

输出示例：
COMMENT_ID: 3
REPLY: 我也觉得这件事没这么简单，官方通报避重就轻。
"""
    return prompt


def get_share_prompt(agent, info_flow_summary):
    """选择转发对象 + 可选附言"""
    prompt = f"""
请扮演以下角色，你正在社交媒体上浏览热点内容。

[你的基本画像]
姓名：{agent.name}
年龄：{agent.age}
性别：{agent.gender}
人格特质：{agent.personality}
{_memory_block(agent)}
[你当前的心理状态]
恐慌程度（0=完全平静，1=极度恐慌）：{agent.panic:.2f}
对政府的信任程度（0=完全不信任，1=完全信任）：{agent.government_trust:.2f}
对不实信息的易感程度（0=有辨别力，1=容易轻信转发）：{agent.rumor_susceptibility:.2f}

[你浏览到的信息和评论]
{info_flow_summary}

请选择你要转发的内容。你可以转发某条帖子（用 pid 标识）或某条评论（用 cid 标识）。
如果需要，可以附上一句简短的转发语。

输出格式（严格按此格式）：
SHARE_TYPE: <post 或 comment>
SHARE_ID: <pid 或 cid>
FORWARD_NOTE: <转发语，可选，不超过20字；如不需要留空>

输出示例：
SHARE_TYPE: post
SHARE_ID: 2
FORWARD_NOTE: 这才是真相，大家都应该知道。
"""
    return prompt


def get_vote_prompt(agent, info_flow_summary):
    """选择赞或踩的目标评论"""
    prompt = f"""
请扮演以下角色，你正在社交媒体上浏览热点内容。

[你的基本画像]
姓名：{agent.name}
年龄：{agent.age}
性别：{agent.gender}
人格特质：{agent.personality}
{_memory_block(agent)}
[你当前的心理状态]
恐慌程度（0=完全平静，1=极度恐慌）：{agent.panic:.2f}
对政府的信任程度（0=完全不信任，1=完全信任）：{agent.government_trust:.2f}
对不实信息的易感程度（0=有辨别力，1=容易轻信转发）：{agent.rumor_susceptibility:.2f}

[你浏览到的信息和评论]
{info_flow_summary}

请选择你要点赞或点踩的评论编号（cid），并指定操作方向。

输出格式（严格按此格式）：
VOTE_TYPE: <upvote 或 downvote>
COMMENT_ID: <cid>

输出示例：
VOTE_TYPE: upvote
COMMENT_ID: 5
"""
    return prompt


# ============================================================
# 状态更新 Prompt（强制结构化输出）
# ============================================================

def get_reflection_prompt(agent, action_desc, info_flow_summary,
                          current_day=None):
    """
    Agent 完成行动后，更新 panic / trust / rumor。
    强制输出结构化格式：LATEST_POSTS / OUTDATED_POSTS + 三个 SCORE + REASONING。
    """
    max_age = 2
    cfg = getattr(agent, 'config', None) or {}
    try:
        max_age = max(0, int(cfg.get('freshness_max_age_days', 2)))
    except (TypeError, ValueError):
        max_age = 2
    day_line = (f"今天是第{current_day}天。" if current_day is not None
                else "今天。")

    prompt = f"""
请扮演以下角色，你刚刚在社交媒体上浏览了热点内容并采取了行动。

[你的基本画像]
姓名：{agent.name}
年龄：{agent.age}
性别：{agent.gender}
人格特质：{agent.personality}
{_memory_block(agent)}
[你今天浏览到的信息]
{info_flow_summary}

[信息时效性判断]
{day_line}上面的信息已按发布时间分组，请先判断时效性，再更新状态。

- 【今日最新消息】：今天发布或更新的、关于事件本身的事实进展或官方表态。只有这一类信息可以作为改变你判断的新依据。
- 【今日其他内容】：今天发布、但不是事件进展的内容（如无关新话题），不构成对事件事实的更新，不应单独改变你对事件的判断。
- 【近期信息】：发布未超过{max_age}天，仍可作背景；但其中已经反映在你状态里的旧信息，不要重复调整。
- 【已过时信息】：发布超过{max_age}天，只作背景，不得作为调整三项分数的依据。如果它与最新消息冲突，以最新消息为准。

补充规则：
1. 只有今天的新消息带来增量时，才调整分数；同一件旧事被重复提及时，不要继续上调恐慌或下调信任。
2. 旧帖与最新消息冲突时，以最新消息为准，不要同时按两套说法叠加。
3. 如果今天没有新的实质信息，三个分数应接近之前；方向可升、可降或不变。
4. 你过去的发言只代表当时的状态；如果与最新消息冲突，以最新消息为准。
5. 只能使用信息流中出现过的 pid，不要编造。
6. 即使没有最新消息或没有过时帖子，也必须保留 LATEST_POSTS 和 OUTDATED_POSTS 两行并写 NONE，不要省略。

[你今天的行动]
{action_desc}

[你之前的心理状态]
恐慌程度：{agent.panic:.2f}（0=完全平静，1=极度恐慌）
对政府信任程度：{agent.government_trust:.2f}（0=完全不信任，1=完全信任）
对不实信息的易感程度：{agent.rumor_susceptibility:.2f}（0=有辨别力，1=容易轻信转发）

请根据今天的最新消息和你的行动，在**你之前状态的基础上**做出调整：

- 恐慌程度：最新消息若带来新的威胁或不确定性，可能上调；若带来澄清、控制进展或风险解除，可能下调；若没有新信息，保持不变。
- 政府信任度：最新消息若显示政府透明、高效、担责，可能上调；若显示掩盖、推诿或前后矛盾，可能下调；若没有新信息，保持不变。
- 谣言易感性：最新消息若清晰、可核验、来源权威，可能下调；若互相矛盾、缺乏权威来源，可能上调；若没有新信息，保持不变。

【关于调整幅度——请特别注意】
你的新状态应当是在**之前状态基础上的小幅变化**，而不是每天重新给一个全新的值：
- 除非今天出现了**重大的新情况**，否则单个字段的日变化通常不超过 ±0.15。
- 如果今天没有值得改变你判断的新信息，新值就应当与之前**接近**。
- 人的心理状态是渐变的：不会因为看到一条普通帖子就从平静变得极度恐慌。
- 不要为了“看起来有变化”而调整；不变是允许的。

请先判断信息时效性，再给出更新后的分数。严格按以下格式输出，每个字段各占一行：
LATEST_POSTS: <属于【今日最新消息】的 pid，逗号分隔；没有写 NONE>
OUTDATED_POSTS: <你判定为过时、只作背景的 pid，逗号分隔；没有写 NONE>
PANIC_SCORE: <0到1之间的数字>
TRUST_SCORE: <0到1之间的数字>
RUMOR_SCORE: <0到1之间的数字>
REASONING: <简要解释状态变化的原因；请点明依据的最新消息 pid，以及被判定过时的旧帖 pid（如有），1-2句话>

填写规则：
- LATEST_POSTS 只能从【今日最新消息】中选，不要把【今日其他内容】或【近期信息】填进来。
- OUTDATED_POSTS 填标注【已过时】、你作为背景处理的旧帖；没有就写 NONE。
- pid 必须出现在上面的信息流中。

输出示例（仅演示格式；“不变”只是示例，方向可升可降可不变）：
假设你之前的状态是——恐慌 0.55、信任 0.45、易感 0.50；今天的最新消息只是重复已知情况：
LATEST_POSTS: 10
OUTDATED_POSTS: 3, 4
PANIC_SCORE: 0.55
TRUST_SCORE: 0.45
RUMOR_SCORE: 0.50
REASONING: 今天的最新消息（pid=10）没有提供新的实质信息；pid=3、4 已超过时效，只作背景，三项分数基本不变。
"""
    return prompt


# ============================================================
# 信息流摘要生成（给 prompt 用的文本化版本）
# ============================================================

def _flow_group(item, current_day, max_age):
    """把一条信息流帖子归入 latest / today_other / recent / outdated。"""
    day = item.get('day')
    updated = item.get('updated_day', day)
    if updated is None:
        return 'recent'
    age = item.get('age_days')
    if age is None:
        age = max(0, current_day - updated)
    is_stim = bool(item.get('is_stimulus'))
    is_notice = bool(item.get('is_notice'))
    stype = item.get('stimulus_type')

    if age == 0 and (is_stim or is_notice) and stype != 'new_topic':
        return 'latest'
    if age == 0:
        return 'today_other'
    if age <= max_age:
        return 'recent'
    return 'outdated'


def _post_tags(item, group, current_day, max_age):
    """生成单条帖子的来源与时效标签。"""
    day = item.get('day')
    updated = item.get('updated_day', day)
    age = item.get('age_days')
    if age is None and updated is not None:
        age = max(0, current_day - updated)
    stype = item.get('stimulus_type')
    is_official = (bool(item.get('is_notice'))
                   or stype == 'gov_comm'
                   or '政府' in (item.get('source') or ''))

    tags = []
    if is_official:
        tags.append('【官方发布】')
    if stype == 'fact':
        tags.append('【事实进展】')
    elif stype == 'gov_comm' or item.get('is_notice'):
        tags.append('【官方表态】')
    elif stype == 'new_topic':
        tags.append('【其他话题】')

    if group == 'latest':
        if day is not None and day < current_day:
            tags.append(f'【今日更新·第{day}天首发】')
        else:
            tags.append('【今日最新】')
    elif group == 'today_other':
        tags.append('【今日发布·非事件进展】')
    elif group == 'outdated':
        tags.append(f'【已过时·{age}天前】')
    elif age is not None:
        tags.append(f'【第{updated}天发布·{age}天前】')
    return ''.join(tags)


def _render_post(item, index, my_aid, group, current_day, max_age):
    """渲染单条帖子及其评论；评论逻辑与旧版保持一致。"""
    title = item.get('title', '无标题')
    content = item.get('content', '')
    like_num = item.get('like_num', 0)
    view_num = item.get('view_num', 0)
    tag_str = _post_tags(item, group, current_day, max_age)
    content_short = content[:200] if content else '（无正文）'

    post_text = f"""
[帖子{index}] {tag_str}(pid={item['pid']}, 浏览{view_num}, 点赞{like_num})
标题：{title}
内容摘要：{content_short}
"""
    comments = item.get('comments', [])
    if comments:
        post_text += "评论：\n"
        for c in comments:
            mine = ' [你的评论]' if (my_aid is not None
                                    and c.get('author_id') == my_aid) else ''
            post_text += (f"  [cid={c['cid']}]{mine} {c['text']} "
                          f"(赞{c['like_num']})\n")
            for r in c.get('replies', []):
                mine_r = ' [你的回复]' if (my_aid is not None
                                          and r.get('author_id') == my_aid) else ''
                post_text += (f"    ↳ [cid={r['cid']}]{mine_r} {r['text']} "
                              f"(赞{r['like_num']})\n")
    return post_text


def summarize_info_flow(info_flow, agent=None, current_day=None):
    """
    将结构化的信息流转化为 prompt 可用的文本摘要。

    2026-10-20：加入时效分组。按“最后更新日”分为：
      今日最新消息 / 今日其他内容 / 近期信息 / 已过时信息。
    “过时”按年龄阈值判定：
      current_day - updated_day > freshness_max_age_days。

    Args:
        info_flow: PostPool.get_info_flow() 的返回值
        agent: 当前 agent（用于标记自己的评论、读取时效阈值），可为 None
        current_day: 当前天，可为 None（回退为信息流中的最新更新日）

    Returns:
        str: 供 prompt 使用的文本摘要
    """
    if not info_flow:
        return "（今天没有看到新的内容）"

    my_aid = getattr(agent, 'aid', None)
    max_age = 2
    if agent is not None:
        cfg = getattr(agent, 'config', None) or {}
        try:
            max_age = max(0, int(cfg.get('freshness_max_age_days', 2)))
        except (TypeError, ValueError):
            max_age = 2
    if current_day is None:
        current_day = max(
            (item.get('updated_day', item.get('day', 1)) for item in info_flow),
            default=1)

    groups = {'latest': [], 'today_other': [], 'recent': [], 'outdated': []}
    for item in info_flow:
        groups[_flow_group(item, current_day, max_age)].append(item)

    parts = [(
        f"\n【时间基准】今天是第{current_day}天。你看到 {len(info_flow)} 条帖子："
        f"今日最新消息 {len(groups['latest'])} 条，"
        f"今日其他内容 {len(groups['today_other'])} 条，"
        f"近期信息 {len(groups['recent'])} 条，"
        f"已过时信息 {len(groups['outdated'])} 条。\n"
    )]

    sections = [
        ('latest', '今日最新消息（更新三项状态的唯一新信息依据）',
         '（今天没有新的事实进展或官方表态）'),
        ('today_other', '今日其他内容（非事件进展，只作浏览背景）', '（无）'),
        ('recent', f'近期信息（发布未超过{max_age}天，可作背景）', '（无）'),
        ('outdated', f'已过时信息（发布超过{max_age}天，只作背景，不得作为状态变化依据）',
         '（无）'),
    ]
    index = 0
    for key, header, empty in sections:
        parts.append(f"\n--- {header} ---\n")
        if not groups[key]:
            parts.append(empty + '\n')
            continue
        for item in groups[key]:
            index += 1
            parts.append(_render_post(item, index, my_aid, key,
                                      current_day, max_age))

    return ''.join(parts)
