# Event-Driven LLM Social Simulation

A small, event-package-based framework for simulating public-opinion dynamics with
LLM agents. Each event supplies comments and a daily stimulus timeline; the
framework builds agent personas, runs a day-by-day multi-agent simulation, and
produces validation summaries and plots.

The repository contains the framework, a Chinese synthetic demo event, and the
tests needed to run it without network access. It does **not** ship a scraped
dataset or a paper-specific case study.

## First time here?

- **Just want to see it run?** Follow “Demo quick start” below. No API key is
  needed.
- **Want to add your own event?** Read `events/README.md` and
  `docs/ADDING_AN_EVENT.md`, then follow “Full pipeline for a new event”.
- **Want to understand the internals?** Read `docs/ARCHITECTURE.md`.

`events/README.md` and `docs/ADDING_AN_EVENT.md` are currently written in
Chinese; `docs/ARCHITECTURE.md` is in English.

## 五分钟上手（中文）

本项目是一个“事件包驱动”的 LLM 多智能体舆情仿真框架。每个事件放在
`events/<id>/`，自带输入、缓存、处理结果和输出。

```bash
# 1. 安装依赖
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. 跑自带的 synthetic demo（不需要 API key）
python -m personas.pipeline --event demo --stage free
python -m simulation.run --event demo --run-id smoke --days 3 --llm-type fake
```

结果在 `events/demo/outputs/runs/smoke/record.json`。

要导入自己的事件：复制 `events/_template`，按
`docs/ADDING_AN_EVENT.md` 填 `event.json`、`raw_comments.csv` 和
`stimuli.csv`，然后执行：

```bash
python -m personas.import_event --event my_event
python -m personas.pipeline --event my_event --stage free
```

付费的 LLM 打分步骤需要先设置 `LLM_API_KEY`，并加 `--yes-paid`；建议先用
`--pilot` 小规模跑。

## What is in the box

- **Event packages** (`events/<id>/`) with their own input, interim, processed,
  and output directories.
- **Persona pipeline**: comment import, cleaning, LLM scoring, author-level Big
  Five labels, seat allocation, and initial psychological states.
- **Simulation core**: configurable agents, one analyst agent, one
  government agent, a post pool, information-flow freshness handling, and five
  government strategies (S1-S5).
- **Validation helpers**: daily negative-emotion curve, phase summaries, shape
  comparison against a simulation record, and event-level orchestration.
- **Fake LLM** for deterministic, network-free smoke tests.

## Installation

Requires **Python 3.10+**.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

For real LLM calls, set:

```bash
export LLM_API_KEY="your-key"          # or OPENAI_API_KEY
export LLM_BASE_URL="https://api.siliconflow.cn/v1"   # optional
export LLM_MODEL_NAME="deepseek-ai/DeepSeek-V4-Flash" # optional
```

Never commit a key. `record.json` stores a redacted configuration.

## Demo quick start (no paid LLM)

`events/demo` is a 3-day Chinese synthetic example. It already contains the
author-level Big Five cache needed to build personas.

```bash
# One command for the free pipeline (import, clean, personas, stimuli, validate)
python -m personas.pipeline --event demo --stage free

# Deterministic smoke simulation (no network)
python -m simulation.run --event demo --run-id smoke --days 3 --llm-type fake
```

You can also run the free steps individually:

```bash
python -m personas.import_event --event demo
python -m personas.clean --event demo
python -m personas.build_agents_author --event demo
python -m personas.prepare_stimulus --event demo
python -m personas.validate_event --event demo --strict
```

Output is written under:

```text
events/demo/outputs/runs/smoke/
  record.json
  simulation.log
  simulation_agents.log
  simulation_llm.log
```

To run the same demo with a real LLM, omit `--llm-type fake` and make sure
`LLM_API_KEY` is set.

## Full pipeline for a new event

1. Copy the template:

   ```bash
   cp -r events/_template events/my_event
   ```

2. Edit `events/my_event/event.json` and fill in the event metadata, window,
   phases, and column mapping.

3. Put the raw comments at `events/my_event/input/raw_comments.csv` and the
   daily facts/official statements at `events/my_event/input/stimuli.csv`.

4. Import and validate:

   ```bash
   python -m personas.import_event --event my_event --dry-run
   python -m personas.import_event --event my_event
   python -m personas.validate_event --event my_event --strict
   ```

5. Run the free steps:

   ```bash
   python -m personas.pipeline --event my_event --stage free
   ```

6. Run paid steps in pilots first:

   ```bash
   python -m personas.pipeline --event my_event --only score --pilot 20 --yes-paid
   python -m personas.pipeline --event my_event --only author_big5 --pilot 20 --yes-paid
   python -m personas.pipeline --event my_event --only build_agents_author
   python -m personas.pipeline --event my_event --only initial_states --yes-paid
   ```

7. Dry-run and then run the simulation:

   ```bash
   python -m simulation.run --event my_event --run-id pilot --days 2 --dry-run
   python -m simulation.run --event my_event --run-id baseline
   ```

8. Validate and compare:

   ```bash
   python -m experiments.run_event --event my_event --mode validate
   python -m experiments.run_event --event my_event --mode compare --run-id baseline
   ```

See `docs/ADDING_AN_EVENT.md` for the full field contract and
`events/README.md` for the event-package layout.

## Event package layout

```text
events/<id>/
  event.json
  input/
    raw_comments.csv      # supplied by you
    comments.csv          # generated by import_event, git-ignored
    stimuli.csv           # daily facts / official statements
  interim/                # cleaning and LLM caches
  processed/              # user.csv, post.csv, validation curves
  outputs/runs/<run_id>/  # record.json and logs
  outputs/figures/        # plots
```

Only `event.json`, the template/demo inputs, and the demo author cache are
committed. Generated comments, processed files, records, and logs are ignored.

## Simulation model

- Each网民 agent has three dynamic states: `panic`, `government_trust`, and
  `rumor_susceptibility`.
- Each day an agent may comment, reply, vote, share, or stay silent.
- After acting, the agent reflects on the information flow and updates the three
  states. A small baseline reversion term prevents unbounded one-way drift.
- The government decides on configured decision days among five strategy
  packages: silence, suppression, transparent communication, agenda shifting,
  and a combined approach.
- The analyst agent turns the day's posts and comments into a short brief.

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers event loading, Chinese CSV import, date parsing, persona
building, stimulus preparation, the free demo pipeline, and a FakeLLM simulation.
It also includes a release-hygiene test that rejects private data, paper
materials, key-like tokens, and case-specific strings.

## Repository layout

```text
simulation/    core loop, agents, government, posts, event config, LLM adapters
personas/      import, clean, score, author Big Five, seats, initial states
experiments/   validation, comparison, plotting, strategy pilot, event runner
events/        _template and synthetic demo
tests/         network-free unit/integration tests
docs/          event contract and architecture notes
scraping/      optional generic Reddit RSS collector
```

## Troubleshooting

- **`missing input/comments.csv`** — run
  `python -m personas.import_event --event <id>` first.
- **`missing interim/author_big5.jsonl`** — run the paid author-level Big Five
  step (`personas.pipeline --event <id> --only author_big5 --yes-paid`).
- **`No API key configured`** — set `LLM_API_KEY` or `OPENAI_API_KEY`; the demo
  with `--llm-type fake` needs no key.
- **`output already exists`** — use a different `--run-id`, or add `--force`.
- **comments are dropped as too short** — lower `input.min_body_chars` in
  `event.json` for Chinese or short-comment corpora.
- **dates fail to parse** — add the exact format to `input.time_formats` and
  check `interim/import_report.json`.
- **no stimuli for some days** — fill `input/stimuli.csv`; for a link-only smoke
  test use `personas.prepare_stimulus --allow-placeholder`, but the result is not
  usable for research.

## Notes

- The demo is synthetic and is only for smoke testing; it is not a research
  result.
- Event data is supplied by the user. Do not commit private corpora.
- LLM sampling is stochastic; fix the seed for the Python-side random process and
  report multiple seeds for substantive experiments.
- The utility matrix ships at zero. It must be calibrated per event before
  strategy choices are meaningful.
