# 事件包（Event Packages）

每个事件一个自包含目录：

```text
events/<event_id>/
  event.json
  input/
    raw_comments.csv     # 你提供的原始评论（列名可不同，靠 column_map 映射）
    comments.csv         # import_event 生成的规范表（不要手改）
    stimuli.csv          # 每日事实/官方表态
  interim/               # 清洗、打分、作者级大五、初值缓存
  processed/             # user.csv / initial_states.csv / post.csv / 验证曲线
  outputs/
    runs/<run_id>/       # record.json + simulation*.log
    analysis/
    figures/
```

`events/_template/` 是可复制的中文社媒示例（微博风格）。复制后把
`event.json` 的 `event_id` 改成目录名，即可使用。

## 规范评论表

`input/comments.csv` 由 `personas/import_event.py` 生成，列：

`comment_id, author, body, published, published_date, post_id, post_title,
parent_id, depth, channel, source, likes, source_file`

- `published` 带时区（如 `2024-01-01T09:00:00+08:00`）；
- `published_date` 是该评论在事件时区下的自然日，用于阶段归属；
- `channel` 是平台/社区（Reddit 的 subreddit、微博的来源等）；
- `source` 是可选的自由标签（例如 `real` 或来源名称），用于统计来源构成。

## 刺激源表

`input/stimuli.csv` 列：

`day, real_date, stimulus_type, title, news_content, source`

- `day` 从 1 开始，必须覆盖 1..total_days；
- `stimulus_type` 取 `fact` / `gov_comm` / `new_topic`；
- `gov_comm` 在 `counterfactual` 模式下会被跳过，因为政府表态由模拟政府产生；
- `real_date` 只用于核对，不参与仿真。

如果暂时没有每日时间线，先跑
`python -m personas.prepare_stimulus --event <id> --allow-placeholder` 做链路冒烟；
占位刺激会在输出中标记，不能用于论文结果。

## 常用命令

```bash
# 导入 + 校验
python -m personas.import_event --event <id> --dry-run
python -m personas.import_event --event <id>
python -m personas.validate_event --event <id> --strict

# 免费步骤
python -m personas.pipeline --event <id> --stage free

# 付费步骤（先试点）
python -m personas.pipeline --event <id> --only score --pilot 20 --yes-paid
python -m personas.pipeline --event <id> --only author_big5 --pilot 20 --yes-paid
python -m personas.pipeline --event <id> --only build_agents_author
python -m personas.pipeline --event <id> --only initial_states --yes-paid

# 仿真 dry-run / 正式
python -m simulation.run --event <id> --run-id pilot --days 2 --dry-run
python -m simulation.run --event <id> --run-id pilot --days 2   # 只要不是 dry-run 就会调用 LLM
python -m simulation.run --event <id> --run-id baseline

# 验证与对比
python -m experiments.run_event --event <id> --mode validate
python -m experiments.run_event --event <id> --mode compare --run-id baseline

# 标定政府效用矩阵（S1 为基准，建议至少 3 个种子）
python -m experiments.calibrate_utility --event <id> --seeds 42,43,44 --yes-paid
```

> `simulation.run` 没有 `--yes-paid` 开关；只要不是 `--dry-run` 就会调用 LLM。
> 先用 `--dry-run` 检查路径和 prompt，再小规模跑。
