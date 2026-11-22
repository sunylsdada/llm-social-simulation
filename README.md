# 事件驱动的 LLM 社会舆情仿真框架

一个以“事件包”为单位的轻量级社会舆情仿真框架。每个事件提供评论数据和每日刺激源时间线，框架负责构建网民画像、逐日运行多智能体仿真，并生成验证曲线和图表。

仓库包含框架代码、一个中文合成示例和一套无网络测试；**不包含**抓取数据集，也不包含任何具体论文案例。

## 第一次使用？

- **只想先跑起来？** 看下面的“快速开始”，自带的合成示例不需要 API key。
- **想导入自己的事件？** 先读 `events/README.md` 和 `docs/ADDING_AN_EVENT.md`，再按“新事件完整流程”操作。
- **想了解内部实现？** 读 `docs/ARCHITECTURE.md`。

目前 `README.md`、`events/README.md`、`docs/ADDING_AN_EVENT.md` 是中文，`docs/ARCHITECTURE.md` 是英文。

## 快速开始

本项目是一个“事件包驱动”的 LLM 多智能体舆情仿真框架。每个事件放在
`events/<id>/`，自带输入、缓存、处理结果和输出。

```bash
# 1. 安装依赖
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. 跑自带的合成示例（不需要 API key）
python -m personas.pipeline --event demo --stage free
python -m simulation.run --event demo --run-id smoke --days 3 --llm-type fake
```

结果在：

```text
events/demo/outputs/runs/smoke/record.json
```

要导入自己的事件：复制 `events/_template`，按
`docs/ADDING_AN_EVENT.md` 填 `event.json`、`raw_comments.csv` 和
`stimuli.csv`，然后执行：

```bash
python -m personas.import_event --event my_event
python -m personas.pipeline --event my_event --stage free
```

付费的 LLM 打分步骤需要先设置 `LLM_API_KEY`，并加 `--yes-paid`；建议先用
`--pilot` 小规模跑。

## 这个仓库包含什么

- **事件包**（`events/<id>/`）：每个事件有独立的输入、缓存、处理结果和输出目录。
- **画像流水线**：评论导入、清洗、LLM 打分、作者级大五人格、席位分配、初始心理状态。
- **仿真核心**：可配置数量的网民 agent、1 个分析师 agent、1 个政府 agent、帖子池、信息时效性处理，以及 S1–S5 五种政府策略。
- **验证工具**：日度负面情绪曲线、阶段汇总、真实曲线与仿真记录的形态对比、事件级实验编排。
- **FakeLLM**：无需网络即可跑通完整流程的确定性假模型，用于测试和冒烟。

## 安装

需要 **Python 3.10 或以上**。

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

如果要调用真实 LLM，设置：

```bash
export LLM_API_KEY="你的 key"          # 也可以使用 OPENAI_API_KEY
export LLM_BASE_URL="https://api.siliconflow.cn/v1"   # 可选
export LLM_MODEL_NAME="deepseek-ai/DeepSeek-V4-Flash" # 可选
```

不要把 key 写进代码或提交到仓库。`record.json` 中的配置会自动脱敏。

## 示例快速开始（不需要付费 LLM）

`events/demo` 是一个 3 天的中文合成示例，已经包含构建画像所需的作者级大五缓存。

```bash
# 一条命令跑完免费流程：导入、清洗、画像、刺激源、校验
python -m personas.pipeline --event demo --stage free

# 确定性冒烟仿真（不联网）
python -m simulation.run --event demo --run-id smoke --days 3 --llm-type fake
```

也可以手动分步执行：

```bash
python -m personas.import_event --event demo
python -m personas.clean --event demo
python -m personas.build_agents_author --event demo
python -m personas.prepare_stimulus --event demo
python -m personas.validate_event --event demo --strict
```

输出在：

```text
events/demo/outputs/runs/smoke/
  record.json
  simulation.log
  simulation_agents.log
  simulation_llm.log
```

如果想用真实 LLM 跑同一个示例，去掉 `--llm-type fake`，并确保已经设置
`LLM_API_KEY`。

## 新事件完整流程

1. 复制模板：

   ```bash
   cp -r events/_template events/my_event
   ```

2. 编辑 `events/my_event/event.json`，填写事件名称、时间窗口、阶段划分和列名映射。

3. 把原始评论放到 `events/my_event/input/raw_comments.csv`，把每日事实/官方表态放到 `events/my_event/input/stimuli.csv`。

4. 导入并校验：

   ```bash
   python -m personas.import_event --event my_event --dry-run
   python -m personas.import_event --event my_event
   python -m personas.validate_event --event my_event --strict
   ```

5. 跑免费步骤：

   ```bash
   python -m personas.pipeline --event my_event --stage free
   ```

6. 付费步骤先试点，再全量：

   ```bash
   python -m personas.pipeline --event my_event --only score --pilot 20 --yes-paid
   python -m personas.pipeline --event my_event --only author_big5 --pilot 20 --yes-paid
   python -m personas.pipeline --event my_event --only build_agents_author
   python -m personas.pipeline --event my_event --only initial_states --yes-paid
   ```

7. 先 dry-run，再正式仿真：

   ```bash
   python -m simulation.run --event my_event --run-id pilot --days 2 --dry-run
   python -m simulation.run --event my_event --run-id baseline
   ```

8. 验证与对比：

   ```bash
   python -m experiments.run_event --event my_event --mode validate
   python -m experiments.run_event --event my_event --mode compare --run-id baseline
   ```

完整的字段约定见 `docs/ADDING_AN_EVENT.md`，事件包结构见 `events/README.md`。

## 事件包结构

```text
events/<id>/
  event.json
  input/
    raw_comments.csv      # 你提供的原始评论
    comments.csv          # import_event 生成，已被 git 忽略
    stimuli.csv           # 每日事实 / 官方表态
  interim/                # 清洗和 LLM 缓存
  processed/              # user.csv、post.csv、验证曲线
  outputs/runs/<run_id>/  # record.json 和日志
  outputs/figures/        # 图表
```

只有 `event.json`、模板/示例输入和示例作者缓存会被提交；生成的评论、处理结果、记录和日志都被忽略。

## 仿真模型

- 每个网民 agent 有三个动态状态：`panic`（恐慌）、`government_trust`（政府信任）、`rumor_susceptibility`（谣言易感性）。
- 每天 agent 可以选择：发表评论、回复、点赞/点踩、转发、保持沉默。
- 行动之后，agent 会根据当天信息流反思并更新三个状态；一个较小的基线回归项用于抑制单向漂移。
- 政府在配置的决策日，从五种策略中选择一种：沉默观望、压制封堵、坦诚回应、转移议程、组合应对。
- 分析师 agent 把当天的帖子和评论整理成一份简报。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖事件加载、中文 CSV 导入、日期解析、画像构建、刺激源准备、免费示例流水线和 FakeLLM 仿真。此外还有发布卫生检查，拒绝私有数据、研究材料、key 形式的字符串和特定案例字符串。

## 仓库结构

```text
simulation/    仿真主循环、agent、政府、帖子、事件配置、LLM 适配
personas/      导入、清洗、打分、作者级大五、席位、初始状态
experiments/   验证、对比、绘图、策略预检、事件编排
events/        _template 和合成示例
tests/         无网络单元/集成测试
docs/          事件约定和架构说明
scraping/      可选的通用 Reddit RSS 采集器
```

## 常见问题

- **提示缺少 `input/comments.csv`**：先运行
  `python -m personas.import_event --event <id>`。
- **提示缺少 `interim/author_big5.jsonl`**：先跑付费的作者级大五步骤
  （`personas.pipeline --event <id> --only author_big5 --yes-paid`）。
- **提示 `No API key configured`**：设置 `LLM_API_KEY` 或 `OPENAI_API_KEY`；
  使用 `--llm-type fake` 的示例不需要 key。
- **提示输出已存在**：换一个 `--run-id`，或加 `--force`。
- **评论被判定为太短而丢弃**：在 `event.json` 中调低 `input.min_body_chars`，
  中文评论或短评语料通常需要更小的值。
- **日期解析失败**：把实际格式加入 `input.time_formats`，并查看
  `interim/import_report.json`。
- **某些天没有刺激源**：补全 `input/stimuli.csv`；只做链路冒烟时可以加
  `personas.prepare_stimulus --allow-placeholder`，但结果不能用于正式分析。

## 注意事项

- 自带示例是合成数据，只用于冒烟测试，不是研究结果。
- 事件数据由使用者自行提供；不要提交私有语料。
- LLM 采样具有随机性；Python 侧随机过程可以固定种子，但正式实验应报告多个种子。
- 效用矩阵默认全为 0，必须先按事件标定，政府策略选择才有实际意义。
