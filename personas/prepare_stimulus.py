"""
把事件包的 input/stimuli.csv 校验后复制为仿真读取的 processed/post.csv。

新事件只需要维护 input/stimuli.csv；仿真始终读 processed/post.csv。
不调用 LLM。

用法:
    python -m personas.prepare_stimulus --event my_event
    python -m personas.prepare_stimulus --event my_event --dry-run
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.infra.event import event_paths, load_event

REQUIRED = ["day", "real_date", "stimulus_type", "title", "news_content", "source"]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="准备仿真刺激源 post.csv")
    ap.add_argument("--event", default="demo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-placeholder", action="store_true",
                    help="允许缺失时写占位事实（不可用于论文结果）")
    args = ap.parse_args()

    spec = load_event(args.event)
    paths = event_paths(spec)
    src = paths.input_stimuli
    if not src.exists():
        if not args.allow_placeholder and not spec.inputs.get("allow_placeholder_stimuli"):
            raise SystemExit(
                f"找不到刺激源: {src}\n"
                f"请填写每天的事实/官方表态，或用 --allow-placeholder 做链路冒烟。"
            )
        rows = [{
            "day": str(d),
            "real_date": "",
            "stimulus_type": "fact",
            "title": f"第 {d} 天事件进展待补充",
            "news_content": "占位事实：请在 input/stimuli.csv 中补充当天真实事件。",
            "source": "placeholder",
        } for d in range(1, spec.total_days + 1)]
        print("[警告] 使用占位刺激源；结果不可用于论文。")
    else:
        with open(src, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

    missing = [c for c in REQUIRED if rows and c not in rows[0]]
    if missing:
        raise SystemExit(f"刺激源缺少列: {missing}")

    days = set()
    for r in rows:
        try:
            day = int(r.get("day") or 0)
        except (TypeError, ValueError):
            raise SystemExit(f"刺激源 day 不是整数: {r.get('day')!r}")
        if not (1 <= day <= spec.total_days):
            raise SystemExit(f"刺激源 day={day} 不在 1..{spec.total_days}")
        if str(r.get("stimulus_type") or "fact") not in {"fact", "gov_comm", "new_topic"}:
            raise SystemExit(f"未知 stimulus_type: {r.get('stimulus_type')}")
        days.add(day)

    missing_days = [d for d in range(1, spec.total_days + 1) if d not in days]
    if missing_days:
        print(f"[警告] 这些天没有刺激源: {missing_days[:10]}")

    print(f"事件: {spec.event_id}")
    print(f"刺激源: {len(rows)} 条，覆盖 {len(days)}/{spec.total_days} 天")
    print(f"输出: {paths.post_csv}")

    if args.dry_run:
        print("[dry-run] 未写入")
        return

    paths.post_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(paths.post_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REQUIRED, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"已写入 {paths.post_csv}")


if __name__ == "__main__":
    main()
