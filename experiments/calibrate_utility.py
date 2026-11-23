"""按事件标定政府效用矩阵。

做法：
- 对每个随机种子，先跑 S1 沉默观望作为基准；
- 用同一初始状态、同一刺激源、同一模型，强制跑 S2–S5；
- 计算每个策略相对 S1 的终局变化：
  - 防恐慌：`u = -Δpanic`
  - 遏谣言：`u = -Δrumor`
  - 建信任：`u = +Δtrust`
- 对多个种子取均值，写入 `events/<id>/processed/utility_matrix.json`。

注意：效用是仿真模型内的相对效果，不是现实世界的效应量。
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import statistics as st
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STRATEGIES = ["S1", "S2", "S3", "S4", "S5"]
TARGETS = [
    ("panic_prevention", "panic", -1.0),
    ("rumor_containment", "rumor", -1.0),
    ("long_term_trust", "trust", +1.0),
]
METRICS = ("final", "trajectory")


def _mean_state(snapshot):
    return {
        "panic": float(snapshot["avg_panic"]),
        "trust": float(snapshot["avg_trust"]),
        "rumor": float(snapshot.get("avg_rumor", 0.0)),
    }


def run_one(base_cfg, strategy, seed, logger=None):
    """跑一次强制策略仿真，返回初始/终局/轨迹均值。"""
    from experiments.forced_strategy import ForcedStrategySimulator
    from simulation.infra.llm import create_llm
    from simulation.infra.recorder import Recorder
    from simulation.infra.utils import Checkpoint

    cfg = copy.deepcopy(base_cfg)
    cfg["simulation_config"]["seed"] = seed

    # 在构造 agent 之前播种，保证同一 seed 下各策略起点一致。
    random.seed(seed)

    recorder = Recorder(cfg)
    llm = create_llm(cfg, logger=None)
    sim = ForcedStrategySimulator(
        cfg, llm, recorder, Checkpoint(cfg, time.time()),
        strategy, logger=logger,
    )
    initial = {
        "panic": st.mean([a.panic for a in sim.agents]),
        "trust": st.mean([a.government_trust for a in sim.agents]),
        "rumor": st.mean([a.rumor_susceptibility for a in sim.agents]),
    }

    sim.run()
    if not recorder.daily_snapshots:
        raise RuntimeError(f"{strategy} seed={seed} 没有产生每日快照")

    final = _mean_state(recorder.daily_snapshots[-1])
    trajectory = {
        key: st.mean([float(s[f"avg_{field}"]) for s in recorder.daily_snapshots])
        for key, field in (("panic", "panic"), ("trust", "trust"),
                           ("rumor", "rumor"))
    }
    return {
        "strategy": strategy,
        "seed": seed,
        "initial": initial,
        "final": final,
        "trajectory": trajectory,
        "days": len(recorder.daily_snapshots),
        "actions": len(recorder.agent_actions),
    }


def _round_utility(value):
    if abs(value) < 1e-12:
        return 0.0
    return round(value, 3)


def compute_matrix(results, metric="final"):
    """由逐种子结果计算效用矩阵。"""
    if metric not in METRICS:
        raise ValueError(f"未知 metric: {metric}")

    seeds = sorted(results)
    if not seeds:
        raise ValueError("没有可用的标定结果")

    # seed -> strategy -> mean delta
    deltas = {}
    for seed in seeds:
        by_strategy = results[seed]
        if "S1" not in by_strategy:
            raise ValueError(f"seed={seed} 缺少 S1 基准")
        base = by_strategy["S1"][metric]
        deltas[seed] = {}
        for strategy in STRATEGIES:
            if strategy not in by_strategy:
                continue
            cur = by_strategy[strategy][metric]
            deltas[seed][strategy] = {
                "panic": cur["panic"] - base["panic"],
                "rumor": cur["rumor"] - base["rumor"],
                "trust": cur["trust"] - base["trust"],
            }

    matrix = {s: [0.0, 0.0, 0.0] for s in STRATEGIES}
    aggregate = {}
    for strategy in STRATEGIES:
        rows = [d[strategy] for d in deltas.values() if strategy in d]
        if not rows:
            continue
        mean_delta = {
            key: round(st.mean([r[key] for r in rows]), 4)
            for key in ("panic", "rumor", "trust")
        }
        aggregate[strategy] = mean_delta
        matrix[strategy] = [
            _round_utility(-mean_delta["panic"]),
            _round_utility(-mean_delta["rumor"]),
            _round_utility(+mean_delta["trust"]),
        ]

    matrix["S1"] = [0.0, 0.0, 0.0]
    return matrix, deltas, aggregate


def _print_table(matrix, aggregate, seeds):
    print("\n" + "=" * 78)
    print(f"标定结果（种子: {seeds}）")
    print("=" * 78)
    print(f"{'策略':6s} {'Δ恐慌':>10s} {'Δ谣言':>10s} {'Δ信任':>10s} | "
          f"{'防恐慌':>10s} {'遏谣言':>10s} {'建信任':>10s}")
    for strategy in STRATEGIES:
        d = aggregate.get(strategy, {"panic": 0.0, "rumor": 0.0, "trust": 0.0})
        u = matrix[strategy]
        print(f"{strategy:6s} {d['panic']:+10.3f} {d['rumor']:+10.3f} "
              f"{d['trust']:+10.3f} | {u[0]:+10.3f} {u[1]:+10.3f} {u[2]:+10.3f}")
    print("=" * 78)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="按事件标定政府效用矩阵")
    ap.add_argument("--event", default="demo", help="事件 ID（默认 demo）")
    ap.add_argument("--seeds", default="42", help="逗号分隔的随机种子")
    ap.add_argument("--strategies", default="S1,S2,S3,S4,S5",
                    help="逗号分隔的策略；S1 会自动作为基准")
    ap.add_argument("--metric", choices=METRICS, default="final",
                    help="用终局状态还是全时域轨迹均值计算")
    ap.add_argument("--stimulus-mode", choices=["replay", "counterfactual"],
                    default="counterfactual",
                    help="刺激源模式；标定默认 counterfactual，避免历史官方表态叠加")
    ap.add_argument("--agents", type=int, default=None, help="覆盖 agent 数量")
    ap.add_argument("--days", type=int, default=None, help="覆盖仿真天数")
    ap.add_argument("--model", default=None, help="覆盖模型名")
    ap.add_argument("--llm-type", default=None,
                    help="覆盖 LLM 类型；可用 fake 做无网络演示")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=None, help="输出路径")
    ap.add_argument("--dry-run", action="store_true", help="只列出运行计划")
    ap.add_argument("--yes-paid", action="store_true",
                    help="确认真实 LLM 调用")
    ap.add_argument("--force", action="store_true", help="允许覆盖已有矩阵")
    args = ap.parse_args()

    from simulation.infra.event import build_config, event_paths, load_event

    spec = load_event(args.event)
    paths = event_paths(spec)
    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]
    if not seeds:
        raise SystemExit("至少需要一个随机种子")

    requested = [s.strip() for s in args.strategies.split(",") if s.strip()]
    strategies = ["S1"] + [s for s in requested if s != "S1"]
    for s in strategies:
        if s not in STRATEGIES:
            raise SystemExit(f"未知策略: {s}")
    strategies = [s for s in STRATEGIES if s in strategies]

    out_path = Path(args.out) if args.out else paths.utility_matrix_json
    if out_path.exists() and not args.force:
        raise SystemExit(f"矩阵已存在，拒绝覆盖: {out_path}\n加 --force 允许覆盖。")

    llm_type = args.llm_type or None
    paid = (llm_type != "fake")
    if paid and not args.yes_paid and not args.dry_run:
        raise SystemExit("标定会调用真实 LLM；请加 --yes-paid，或使用 --llm-type fake。")

    base_cfg = build_config(args.event, run_id="calibrate")
    base_cfg.setdefault("stimulus_config", {})["mode"] = args.stimulus_mode
    requested_agents = args.agents or base_cfg["simulation_config"]["max_agents"]
    if args.dry_run:
        available = requested_agents
    else:
        from simulation.infra.utils import load_user
        available = len(load_user(base_cfg))
        if requested_agents > available:
            print(f"[提示] 请求 {requested_agents} 个 agent，但 user.csv 只有 {available} 行，"
                  f"按 {available} 个运行。")
    base_cfg["simulation_config"]["max_agents"] = min(requested_agents, available)
    if args.days:
        base_cfg["simulation_config"]["total_days"] = args.days
    base_cfg["simulation_config"]["max_workers"] = args.workers
    if args.model:
        base_cfg["LLM_config"]["model_name"] = args.model
    if args.llm_type:
        base_cfg["LLM_config"]["llm_type"] = args.llm_type

    print(f"事件: {spec.event_id} ({spec.name})")
    print(f"策略: {strategies}")
    print(f"种子: {seeds}")
    print(f"规模: {base_cfg['simulation_config']['max_agents']} agents × "
          f"{base_cfg['simulation_config']['total_days']} 天")
    print(f"指标: {args.metric}")
    print(f"刺激源模式: {args.stimulus_mode}")
    print(f"输出: {out_path}")
    if llm_type == "fake":
        print("LLM: fake（结果仅用于流程演示，不是研究结论）")

    if args.dry_run:
        for seed in seeds:
            for strategy in strategies:
                print(f"  [dry-run] seed={seed} strategy={strategy}")
        return

    results = {}
    for seed in seeds:
        results[seed] = {}
        for strategy in strategies:
            print(f"\n[{seed}] 策略 {strategy} 开始 ...", flush=True)
            t0 = time.time()
            results[seed][strategy] = run_one(base_cfg, strategy, seed)
            print(f"[{seed}] 策略 {strategy} 完成 "
                  f"({time.time() - t0:.0f}s) "
                  f"final panic={results[seed][strategy]['final']['panic']:.3f} "
                  f"trust={results[seed][strategy]['final']['trust']:.3f} "
                  f"rumor={results[seed][strategy]['final']['rumor']:.3f}")

    matrix, deltas, aggregate = compute_matrix(results, args.metric)
    _print_table(matrix, aggregate, seeds)

    if all(all(abs(v) < 1e-12 for v in matrix[s]) for s in matrix):
        print("\n⚠️ 所有效用为 0：请检查策略是否真的执行，或增加种子/天数。")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "event_id": spec.event_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "method": "反事实实验：以 S1 为基准，比较同一初始状态下的终局变化",
        "metric": args.metric,
        "stimulus_mode": args.stimulus_mode,
        "seeds": seeds,
        "strategies": strategies,
        "agents": base_cfg["simulation_config"]["max_agents"],
        "days": base_cfg["simulation_config"]["total_days"],
        "model": base_cfg["LLM_config"].get("model_name"),
        "llm_type": base_cfg["LLM_config"].get("llm_type"),
        "utility_matrix": matrix,
        "mean_deltas": aggregate,
        "per_seed_deltas": deltas,
        "raw_results": results,
        "caveat": "效用是仿真模型内的相对效果，不是现实世界的效应量。",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out_path}")


if __name__ == "__main__":
    main()
