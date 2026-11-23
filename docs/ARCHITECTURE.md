# 架构设计

本文说明框架的整体结构、模块职责、数据流和运行流程。面向想阅读源码、扩展机制或排查问题的使用者。

## 一、总体定位

这是一个“事件包驱动”的社会舆情仿真框架：

- 每个事件是一个独立目录，包含评论数据、每日刺激源和事件配置；
- 画像流水线把评论转成网民 agent；
- 仿真核心按天运行 agent、政府和分析师；
- 验证工具把仿真结果与评论侧的情绪曲线做形态对比；
- 所有缓存、结果、日志都写在事件目录内，不跨事件共享。

它**不是**一个开箱即用的舆情预测系统，也不是一个已经标定好政府策略效用的研究结论。效用矩阵默认全为 0，必须按事件做反事实实验后再使用。

## 二、分层结构

```text
输入层
  comments.csv / stimuli.csv / event.json
        │
        ▼
事件层
  simulation/infra/event.py
  读取配置、校验路径、解析事件上下文
        │
        ▼
画像层
  personas/import_event.py
  personas/clean.py
  personas/score.py
  personas/author_big5_score.py
  personas/build_agents_author.py
  personas/generate_initial_states.py
        │
        ▼
仿真层
  simulation/core/environment.py
  agent.py / government.py / post_pool.py / mechanism.py
        │
        ▼
记录与验证层
  simulation/infra/recorder.py
  experiments/negative_emotion_daily.py
  experiments/compare_replay_vs_real.py
  experiments/plot_*.py
```

## 三、目录与模块职责

| 模块 | 职责 |
|---|---|
| `simulation/infra/event.py` | 事件配置读取、路径解析、配置注入、事件校验、配置脱敏 |
| `simulation/infra/config.py` | 全局默认配置；事件配置会覆盖其中的运行相关字段 |
| `simulation/infra/llm.py` | OpenAI 兼容接口、Anthropic 接口的 LLM 适配 |
| `simulation/infra/fake_llm.py` | 确定性假 LLM，用于无网络测试 |
| `simulation/infra/logger.py` | 主日志、agent 日志、LLM 请求/响应日志 |
| `simulation/infra/recorder.py` | 记录每日快照、政府决策、agent 行动，写出 `record.json` |
| `simulation/core/agent.py` | 网民 agent 的感知、行动、反思和状态更新 |
| `simulation/core/environment.py` | 主循环、每日调度、策略执行、快照 |
| `simulation/core/government.py` | 舆情分析师、政府决策、效用矩阵和记忆 |
| `simulation/core/post_pool.py` | 帖子、评论树、热度、信息流采样 |
| `simulation/core/mechanism.py` | 所有 agent prompt 与信息流摘要 |
| `personas/import_event.py` | 把用户 CSV 导入为规范评论表 |
| `personas/clean.py` | 确定性清洗、阶段归属 |
| `personas/score.py` | 逐条评论 LLM 打分 |
| `personas/author_big5_score.py` | 作者级大五人格标注 |
| `personas/build_agents_author.py` | 席位分配，生成 `user.csv` |
| `personas/generate_initial_states.py` | 生成第 1 天的 panic/trust/rumor |
| `personas/prepare_stimulus.py` | 校验刺激源并生成 `post.csv` |
| `personas/validate_event.py` | 事件包静态校验 |
| `experiments/` | 验证曲线、形态对比、绘图、策略预检、效用标定、事件编排 |

## 四、事件包

```text
events/<event_id>/
  event.json
  input/
    raw_comments.csv
    comments.csv
    stimuli.csv
  interim/
  processed/
  outputs/
    runs/<run_id>/
    figures/
```

`event.json` 是事件的唯一配置来源，主要字段：

| 字段 | 含义 |
|---|---|
| `event_id` | 必须与目录名一致 |
| `name` / `language` / `platform` | 展示名、语言、平台 |
| `timezone` / `utc_offset` | 日期归属使用的时区 |
| `window` | 起止日期与总天数 |
| `phases` | 阶段 id、名称、起止天 |
| `simulation` | 决策日、席位数、模式、干预时序等 |
| `input` | 评论/刺激源路径、列名映射、时间格式、清洗参数 |
| `prompts` | 事件名、事件描述、官方机构、转移话题方向等 |
| `scoring` | 是否启用党派/阵营字段、archetype 覆盖 |
| `keywords` | 删帖/限流使用的关键词 |
| `demographics` | 年龄/性别先验 |
| `validation` | 每日最小样本量、验证变体 |

## 五、数据流

### 5.1 评论侧

```text
raw_comments.csv
  → import_event：列名映射、日期解析、去重、父评论重映射
  → input/comments.csv
  → clean：长度/作者/链接过滤、阶段归属
  → interim/comments_clean.csv
  → score：逐条 LLM 打分
  → interim/comment_scores.jsonl
  → author_big5_score：作者级大五
  → interim/author_big5.jsonl
  → build_agents_author：席位分配
  → processed/user.csv
  → generate_initial_states：第 1 天三项状态
  → processed/initial_states.csv
```

### 5.2 刺激源侧

```text
stimuli.csv
  → prepare_stimulus：校验天数、类型
  → processed/post.csv
```

### 5.3 仿真侧

```text
processed/user.csv
processed/initial_states.csv
processed/post.csv
  → simulation.run
  → outputs/runs/<run_id>/record.json
  → outputs/runs/<run_id>/simulation*.log
```

### 5.4 验证侧

```text
interim/comments_clean.csv + comment_scores.jsonl
  → negative_emotion_daily：日度负面情绪
  → processed/negative_emotion_daily.csv/.json

processed/negative_emotion_daily.csv + record.json
  → compare_replay_vs_real：归一化形态对比
  → outputs/analysis/<run_id>/replay_vs_real.json/.png
```

## 六、运行流程

### 6.1 免费流程

```bash
python -m personas.import_event --event <id>
python -m personas.clean --event <id>
python -m personas.build_agents_author --event <id>
python -m personas.prepare_stimulus --event <id>
python -m personas.validate_event --event <id> --strict
```

也可以一条命令：

```bash
python -m personas.pipeline --event <id> --stage free
```

### 6.2 付费流程

```bash
python -m personas.pipeline --event <id> --only score --pilot 20 --yes-paid
python -m personas.pipeline --event <id> --only score --yes-paid

python -m personas.pipeline --event <id> --only author_big5 --pilot 20 --yes-paid
python -m personas.pipeline --event <id> --only author_big5 --yes-paid

python -m personas.pipeline --event <id> --only build_agents_author
python -m personas.pipeline --event <id> --only initial_states --pilot 3 --yes-paid
python -m personas.pipeline --event <id> --only initial_states --yes-paid
```

### 6.3 仿真

```bash
# 只加载数据与 prompt，不调用 LLM
python -m simulation.run --event <id> --run-id pilot --days 2 --dry-run

# 历史重演
python -m simulation.run --event <id> --run-id baseline --stimulus-mode replay

# 反事实策略实验
python -m simulation.run --event <id> --run-id cf --stimulus-mode counterfactual
```

### 6.4 验证与出图

```bash
python -m experiments.run_event --event <id> --mode validate
python -m experiments.run_event --event <id> --mode compare --run-id baseline

python -m experiments.plot_simulation_results --event <id> --run-id baseline
python -m experiments.plot_panic_normalized --event <id> --run-id baseline
python -m experiments.plot_agent_personality --event <id>
```

## 七、仿真循环

`Simulator.run()` 逐日执行：

1. **决策日**：分析师生成简报，政府分配目标权重，按效用矩阵选择 S1–S5 之一。
2. **策略动作**：按 `intervention_timing` 在 agent 浏览前或浏览后执行。
3. **信息流**：每个 agent 获得最多 5 条帖子；当天新刺激源和政府通告置顶。
4. **行动**：agent 选择评论、回复、赞踩、转发或沉默。
5. **反思**：agent 根据最新消息和自身行动更新 panic/trust/rumor。
6. **写回**：行动按 `aid` 排序后写入帖子池，保证可复现。
7. **快照**：记录当天均值、帖子数、评论数等。

### 7.1 三个动态状态

- `panic`：对事件和自身处境的恐惧程度；
- `government_trust`：对负责处置的官方机构的信任；
- `rumor_susceptibility`：对未经证实信息的轻信与转发倾向。

反思后的值会向 agent 自身基线做小幅回归，避免长跑中单向漂移。

### 7.2 信息时效性

信息流按最后更新日分为四组：

| 分组 | 用途 |
|---|---|
| 今日最新消息 | 唯一可以作为状态变化新依据的信息 |
| 今日其他内容 | 当天发布但与事件进展无关，只作浏览背景 |
| 近期信息 | 未超过时效阈值，可作背景 |
| 已过时信息 | 超过时效阈值，只作背景，不得作为状态变化依据 |

agent 的反思输出会记录它认为哪些 pid 是最新、哪些已过时，写入 `record.json` 便于审计。

### 7.3 政府策略

| 策略 | 动作 |
|---|---|
| S1 沉默观望 | 不主动介入 |
| S2 压制封堵 | 删除最负面的一批评论 |
| S3 坦诚回应 | 发布/更新官方通告 |
| S4 转移议程 | 注入无关正面话题，降低旧帖曝光 |
| S5 组合应对 | 简短声明 + 删除极端评论 |

政府决策基于三个目标的加权效用：防恐慌、遏谣言、建信任。效用矩阵按事件从 `processed/utility_matrix.json` 读取；没有文件时全为 0，此时政府会默认选 S1，这只是占位。

### 7.4 效用矩阵标定

默认矩阵全为 0，政府会默认选择 S1。标定流程在 `experiments/calibrate_utility.py`，默认使用 `counterfactual` 刺激源（只加载事实，避免历史官方表态叠加）：

1. 对每个随机种子，先跑一次 S1 作为基准；
2. 用同一初始状态、同一刺激源、同一模型，强制跑 S2–S5；
3. 取终局或全时域轨迹均值；
4. 计算相对 S1 的变化；
5. 按“防恐慌 = -Δpanic、遏谣言 = -Δrumor、建信任 = +Δtrust”写入
   `events/<id>/processed/utility_matrix.json`；
6. `build_config()` 会自动加载该文件，政府决策开始使用标定后的矩阵。

推荐命令：

```bash
python -m experiments.calibrate_utility --event <id> \
  --seeds 42,43,44 --yes-paid

# 或
python -m experiments.run_event --event <id> \
  --mode calibrate --seeds 42,43,44 --yes-paid
```

`--metric trajectory` 可以用轨迹均值代替终局值，适合末态容易饱和的事件。`--llm-type fake` 只用于流程演示，不能作为研究结论。

## 八、配置与路径解析

- `simulation/infra/config.py` 提供全局默认值；
- `simulation/infra/event.py` 的 `build_config()` 深拷贝默认配置，注入事件路径、天数、决策日、席位数、关键词、效用矩阵；
- `event_paths()` 把所有输入/缓存/输出路径解析到事件目录内，拒绝路径穿越；
- `sanitize_config()` 在写入 `record.json` 前移除 API key。

因此同一进程内顺序运行多个事件时，只要每次都调用 `build_config()`，不会共享缓存或输出。

## 九、Prompt 设计

prompt 由事件上下文生成，不写死具体案例：

- `event_name` / `event_description`；
- `platform` / `language`；
- `official_body`；
- `scoring_fields.partisan` 决定是否要求党派/阵营字段；
- archetype 分类默认 8 类，可由事件覆盖。

公开版本不包含任何具体案例的旧 prompt；示例事件使用通用中文模板。

## 十、可复现性

- `simulation_config.seed` 固定 Python 随机过程；
- 初始状态扰动、每日上线抽取、信息流随机抽样都受该种子影响；
- agent 行动写回按 `aid` 排序，避免线程完成顺序影响结果；
- LLM 采样本身仍是随机的，不能逐字节复现；正式实验应报告多个种子。

## 十一、输出文件

```text
events/<id>/outputs/runs/<run_id>/
  record.json
  simulation.log
  simulation_agents.log
  simulation_llm.log
```

`record.json` 主要包含：

- `meta_info`：事件 id、run id、种子、天数、agent 列表、初始状态；
- `validation`：真实侧指标与仿真代理的说明；
- `indicators`：panic/trust/rumor 曲线、负向情绪代理；
- `government_trajectory`：政府决策轨迹；
- `daily_snapshots`：每日均值与帖子统计；
- `agent_actions`：逐条 agent 行动与反思记录。

## 十二、FakeLLM

`simulation/infra/fake_llm.py` 根据 prompt 关键词返回格式正确的固定输出：

- 行动选择返回 `0`；
- 评论返回固定短句；
- 反思返回保持原状态的三项分数；
- 政府决策返回合法 JSON；
- 分析师返回合法简报。

它只用于测试和冒烟，不能用于研究结果。

## 十三、扩展点

- 新平台/语言：改 `event.json` 的 `platform`、`language`、`prompts`；
- 新分类体系：覆盖 `scoring.archetypes`；
- 新事件时间线：只改 `stimuli.csv` 与 `window/phases`；
- 新策略：在 `government_config`、`environment.execute_*`、效用矩阵中扩展；
- 新验证指标：在 `negative_emotion_daily` / `phase_sentiment` 中增加 metric；
- 新 LLM 提供商：实现 `simulation/infra/llm.py` 的适配类并在 `create_llm()` 注册。

## 十四、安全与发布卫生

- API key 只从环境变量读取；
- `record.json` 中的配置已脱敏；
- 发布测试会拒绝私有数据、研究材料、key 形式字符串和特定案例字符串；
- 如果曾经提交过 secret，应删除仓库重建，不要用后续提交掩盖。
