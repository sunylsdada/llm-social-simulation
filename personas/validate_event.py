"""事件包校验的轻量入口（等价于 python -m simulation.infra.event --check）。"""

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.infra.event import load_event, print_issues, validate_event


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="校验事件包")
    ap.add_argument("--event", default="demo")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    spec = load_event(args.event)
    issues = validate_event(spec, strict=args.strict)
    if args.json:
        print(json.dumps([
            {"level": i.level, "message": i.message, "where": i.where}
            for i in issues
        ], ensure_ascii=False, indent=2))
    else:
        print(f"事件: {spec.event_id} ({spec.name})")
        print(f"窗口: {spec.start_date} ~ {spec.end_date}，{spec.total_days} 天")
        print(f"席位: {spec.seats}，决策日: {spec.decision_days}")
        print(f"语言/平台: {spec.language}/{spec.platform}")
        if not issues:
            print("校验通过，没有发现 issue。")
        else:
            print_issues(issues)
    errors = sum(1 for i in issues if i.level == "error")
    raise SystemExit(0 if errors == 0 else 2)


if __name__ == "__main__":
    main()
