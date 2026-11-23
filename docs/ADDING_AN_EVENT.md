# 新增一个事件：从评论 CSV 到仿真结果

本文以中文社媒（微博/抖音等）为例。整个流程**免费步骤先跑通**，付费 LLM 步骤
最后再执行；每一步都可以单独重跑。

## 0. 准备事件目录

```bash
cp -r events/_template events/my_event
```

然后编辑 `events/my_event/event.json`：

- `event_id` 必须等于目录名 `my_event`；
- `window.start/end` 改成事件的自然日窗口，`total_days` 与日期差一致；
- `phases` 连续覆盖 1..total_days；
- `simulation.decision_days` 设在阶段转折点；
- `language="zh"`，`platform="weibo"/"douyin"/...`；
- `timezone="Asia/Shanghai"`，`utc_offset="+08:00"`；
- `prompts.event_name/event_description/official_body` 写这个事件的背景；
- `scoring.fields.partisan` 对中文事件设 `false`（不输出美国党派字段）；
- `keywords.panic/negative` 换成该事件语料里真正出现的词；
- `input.column_map` 把中文列名映射到规范字段。

### event.json 关键字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `event_id` | 是 | 必须与目录名一致，只能包含小写字母、数字、`-`、`_` |
| `name` | 是 | 事件展示名 |
| `language` | 建议 | `zh` / `en` / `mixed`，影响 prompt 语言 |
| `platform` | 建议 | `weibo` / `douyin` / `xiaohongshu` / `reddit` 等 |
| `timezone` / `utc_offset` | 是 | 日期归属使用的时区 |
| `window.start/end/total_days` | 是 | 事件自然日窗口 |
| `phases` | 是 | 阶段 id、名称、起止天，必须连续覆盖 1..total_days |
| `simulation.decision_days` | 是 | 政府做决策的天数 |
| `simulation.seats` | 是 | 网民 agent 数量 |
| `simulation.allow_fewer_seats` | 否 | 作者不足时是否自动缩减席位 |
| `simulation.intervention_timing` | 否 | `split`（浏览前后分开执行）或 `post` |
| `simulation.strict_comment_targets` | 否 | 回复是否严格要求挂在目标评论所在帖子 |
| `input.comments` | 是 | 规范评论表路径，通常是 `input/comments.csv` |
| `input.stimuli` | 是 | 刺激源路径，通常是 `input/stimuli.csv` |
| `input.column_map` | 是 | 原始 CSV 列名到规范字段的映射 |
| `input.time_formats` | 否 | 额外时间格式 |
| `input.min_body_chars` | 否 | 最短正文长度，中文建议 8–10 |
| `input.bad_authors` | 否 | 需要剔除的作者名 |
| `prompts.event_description` | 是 | 事件背景，进入初始状态与官方通告 prompt |
| `prompts.official_body` | 是 | 负责处置的官方机构 |
| `scoring.fields.partisan` | 否 | 是否要求党派/阵营字段 |
| `keywords.panic/negative` | 建议 | S2/S5 删除评论时使用的关键词 |
| `validation.min_n_per_day` | 否 | 每日最少评论数，低于该值不参与归一化 |

### 运行流程总览

```text
原始评论
  → import_event（列名映射、日期解析、去重）
  → clean（清洗、阶段归属）
  → score（逐条 LLM 打分）
  → author_big5_score（作者级大五）
  → build_agents_author（席位分配、user.csv）
  → generate_initial_states（第 1 天三项状态）
  → prepare_stimulus（post.csv）
  → validate_event（校验）
  → simulation.run（逐日仿真）
  → negative_emotion_daily / phase_sentiment（真实侧曲线）
  → compare_replay_vs_real / plot_*（对比与出图）
```

## 1. 放入评论 CSV

把原始评论放到：

```text
events/my_event/input/raw_comments.csv
```

最小字段：

| 规范字段 | 常见中文列名 | 说明 |
|---|---|---|
| `comment_id` | 评论ID / id | 缺省时自动生成稳定 hash |
| `author` | 用户名 / 用户 | 必填 |
| `body` | 评论内容 / 正文 / text | 必填 |
| `published` | 发布时间 / 时间 / created_at | 必填，支持 ISO、中文日期、epoch |
| `post_id` | 帖子ID / 话题ID | 可缺省，填 `unassigned` |
| `parent_id` | 父评论ID / reply_to | 可缺省 |
| `channel` | 来源 / 平台 | 可缺省，默认 `platform` |
| `source` | 数据来源 | 可缺省，默认 `real` |

如果列名和模板不同，在 `event.json` 的 `input.column_map` 里加别名，不要改 CSV。

## 2. 导入与校验（免费）

```bash
python -m personas.import_event --event my_event --dry-run
python -m personas.import_event --event my_event
python -m personas.validate_event --event my_event --strict
```

导入会：

- 把时间统一成带时区的 ISO 时间，并生成 `published_date`；
- 按事件窗口过滤窗口外评论（`--keep-out-of-window` 可保留）；
- 缺 ID 时生成 `gen_<sha1>`；重复 ID 保留第一条；
- 重映射 `parent_id`，孤儿父 ID 写入 `interim/import_report.json`；
- 写出 `input/comments.csv` 和导入报告。

校验会检查：阶段是否连续、决策日是否越界、评论列/日期/作者数、刺激源是否
覆盖每天、已生成产物的 schema、输出目录冲突等。

## 3. 免费流水线

```bash
python -m personas.pipeline --event my_event --stage free
```

它会运行 `clean`、`stimulus`、`validate`；如果已有作者级大五缓存，也会重建
`user.csv`；如果已有打分缓存，会重算阶段情感。缺少付费缓存时会明确提示。

## 4. 付费步骤（先试点，再全量）

```bash
# 逐条评论打分
python -m personas.pipeline --event my_event --only score --pilot 20 --yes-paid
python -m personas.pipeline --event my_event --only score --yes-paid

# 作者级大五
python -m personas.pipeline --event my_event --only author_big5 --pilot 20 --yes-paid
python -m personas.pipeline --event my_event --only author_big5 --yes-paid

# 生成 user.csv
python -m personas.pipeline --event my_event --only build_agents_author

# 初始心理状态
python -m personas.pipeline --event my_event --only initial_states --pilot 3 --yes-paid
python -m personas.pipeline --event my_event --only initial_states --yes-paid
```

也可以一键：

```bash
python -m personas.pipeline --event my_event --stage all --yes-paid
```

建议第一次一定先 `--pilot` 看输出格式和花费。

## 5. 仿真

```bash
# 先 dry-run：只加载 user/post/prompt，不调用 LLM
python -m simulation.run --event my_event --run-id pilot --days 2 --dry-run

# 历史重演
python -m simulation.run --event my_event --run-id baseline --stimulus-mode replay

# 反事实策略实验
python -m simulation.run --event my_event --run-id cf --stimulus-mode counterfactual
```

输出在：

```text
events/my_event/outputs/runs/<run_id>/
  record.json
  simulation.log
  simulation_agents.log
  simulation_llm.log
```

`record.json` 的 `meta_info.event_id` 会标记事件；`configuration` 已脱敏，
不会写入 API key。

## 6. 验证与出图（免费）

```bash
python -m experiments.run_event --event my_event --mode validate
python -m experiments.run_event --event my_event --mode compare --run-id baseline

python -m experiments.plot_simulation_results --event my_event --run-id baseline
python -m experiments.plot_panic_normalized --event my_event --run-id baseline
```

输出在 `events/my_event/outputs/analysis/` 和 `outputs/figures/`。

## 7. 标定效用矩阵

政府默认效用矩阵全为 0，所以会默认选 S1。正式比较策略前，需要先用反事实实验标定：

```bash
python -m experiments.calibrate_utility --event my_event   --seeds 42,43,44 --yes-paid
```

也可以走事件编排入口：

```bash
python -m experiments.run_event --event my_event   --mode calibrate --seeds 42,43,44 --yes-paid
```

标定默认使用 `counterfactual` 刺激源，以 S1 为基准，用同一初始状态强制跑 S2–S5，计算相对 S1 的终局变化，并写入：

```text
events/my_event/processed/utility_matrix.json
```

符号约定：

- 防恐慌效用 = `-Δpanic`
- 遏谣言效用 = `-Δrumor`
- 建信任效用 = `+Δtrust`

之后 `simulation.run --event my_event` 会自动加载这个矩阵。查看当前矩阵：

```bash
python -m experiments.show_matrix --event my_event
```

只想演示流程、不调用真实 LLM 时，可以加 `--llm-type fake`；结果仅用于链路验证。

## 8. 没有每日时间线怎么办

仿真每天至少需要一条刺激。若你只有评论、没有事件时间线：

```bash
python -m personas.prepare_stimulus --event my_event --allow-placeholder
```

这会生成“第 N 天事件进展待补充”的占位事实，输出会标记
`placeholder_stimuli=true`。**占位结果不能用于论文**；等你补好
`input/stimuli.csv` 后重跑 `--only stimulus` 和仿真即可。

## 9. 常见问题

- **作者数不足席位**：事件配置 `simulation.seats` 调到可用作者数，或设
  `allow_fewer_seats=true`；程序会在输出中记录实际席位数。
- **中文评论被清掉太多**：`input.min_body_chars` 默认 25（英文标准），中文事件
  建议 8–10；这是设计参数，论文里要说明。
- **日期解析失败**：在 `input.time_formats` 增加格式；解析失败行会写入
  `import_report.json`，不会静默混进语料。
- **`PARTISAN_US` 报错**：中文事件把 `scoring.fields.partisan` 设为 `false`；
  schema 会把缺失的该字段按 0 处理。
- **不传 `--event` 会怎样**：默认使用 `events/demo`；请始终显式指定事件 ID。
