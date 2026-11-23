"""
事件级实验入口：串起验证曲线、仿真、对比和出图。

用法:
    python -m experiments.run_event --event my_event --mode validate
    python -m experiments.run_event --event my_event --mode replay --run-id baseline --yes-paid
    python -m experiments.run_event --event my_event --mode counterfactual --run-id cf --yes-paid
    python -m experiments.run_event --event my_event --mode compare --run-id baseline
    python -m experiments.run_event --event my_event --mode calibrate --seeds 42,43,44 --yes-paid
    python -m experiments.run_event --event my_event --mode all --run-id baseline --yes-paid

付费模式：replay / counterfactual / calibrate / all 需要 --yes-paid。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.infra.event import event_paths, load_event


def _run(cmd: list[str], dry_run: bool) -> int:
    print("  " + " ".join(cmd))
    if dry_run:
        print("  [dry-run] 未执行")
        return 0
    t0 = time.time()
    r = subprocess.run(cmd)
    print(f"  rc={r.returncode} ({time.time() - t0:.1f}s)")
    return r.returncode


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="事件级实验编排")
    ap.add_argument("--event", default="demo")
    ap.add_argument("--mode", required=True,
                    choices=["validate", "replay", "counterfactual", "compare", "plots", "calibrate", "all"])
    ap.add_argument("--run-id", default="baseline")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--seeds", default="42", help="逗号分隔的随机种子")
    ap.add_argument("--agents", type=int, default=None, help="覆盖 agent 数量（标定用）")
    ap.add_argument("--model", default=None)
    ap.add_argument("--llm-type", default=None)
    ap.add_argument("--stimulus-mode", choices=["replay", "counterfactual"], default=None,
                    help="覆盖刺激源模式（标定默认 counterfactual）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes-paid", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    spec = load_event(args.event)
    paths = event_paths(spec)
    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]

    paid = args.mode in {"replay", "counterfactual", "calibrate", "all"}
    if paid and not args.yes_paid and not args.dry_run:
        raise SystemExit("replay/counterfactual/calibrate/all 会调用 LLM；请加 --yes-paid，或先 --dry-run。")

    if args.mode == "calibrate":
        print("\n[calibrate] 反事实效用矩阵标定")
        cmd = [
            sys.executable, "-m", "experiments.calibrate_utility",
            "--event", args.event,
            "--seeds", args.seeds,
        ]
        if args.agents:
            cmd += ["--agents", str(args.agents)]
        if args.days:
            cmd += ["--days", str(args.days)]
        if args.model:
            cmd += ["--model", args.model]
        if args.llm_type:
            cmd += ["--llm-type", args.llm_type]
        if args.stimulus_mode:
            cmd += ["--stimulus-mode", args.stimulus_mode]
        if args.force:
            cmd += ["--force"]
        if args.yes_paid:
            cmd += ["--yes-paid"]
        rc = _run(cmd, args.dry_run)
        if rc != 0:
            raise SystemExit(rc)

    if args.mode in {"validate", "all"}:
        print("\n[validate] 真实侧曲线与阶段汇总")
        if not paths.scores_cache.exists():
            print(f"  [跳过] 缺少 {paths.scores_cache.name}，先跑 score")
        else:
            _run([sys.executable, "-m", "experiments.negative_emotion_daily",
                  "--event", args.event], args.dry_run)
            _run([sys.executable, "-m", "personas.phase_sentiment",
                  "--event", args.event], args.dry_run)

    if args.mode in {"replay", "counterfactual", "all"}:
        mode = "replay" if args.mode == "all" else args.mode
        for seed in seeds:
            run_id = args.run_id if len(seeds) == 1 else f"{args.run_id}-seed{seed}"
            print(f"\n[simulate] mode={mode} seed={seed} run_id={run_id}")
            cmd = [
                sys.executable, "-m", "simulation.run",
                "--event", args.event,
                "--run-id", run_id,
                "--seed", str(seed),
                "--stimulus-mode", mode,
            ]
            if args.days:
                cmd += ["--days", str(args.days)]
            if args.model:
                cmd += ["--model-name", args.model]
            if args.llm_type:
                cmd += ["--llm-type", args.llm_type]
            if args.force:
                cmd += ["--force"]
            rc = _run(cmd, args.dry_run)
            if rc != 0:
                raise SystemExit(rc)

    if args.mode in {"compare", "replay", "all"}:
        for seed in seeds:
            run_id = args.run_id if len(seeds) == 1 else f"{args.run_id}-seed{seed}"
            record = paths.record_path(run_id)
            print(f"\n[compare] run_id={run_id}")
            if not args.dry_run and not record.exists():
                raise SystemExit(f"找不到 record: {record}")
            _run([
                sys.executable, "-m", "experiments.compare_replay_vs_real",
                "--event", args.event,
                "--record", str(record),
                "--out-dir", str(paths.outputs_dir / "analysis" / run_id),
            ], args.dry_run)

    if args.mode == "plots":
        print("\n[plots] 当前 compare 已生成对比图；其他事件化绘图脚本可按需接入。")
        print(f"  输出目录: {paths.outputs_dir / 'analysis'}")

    print("\n完成。")


if __name__ == "__main__":
    main()
