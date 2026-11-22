"""
仿真冒烟测试：用少量天数跑通整条仿真链路，确认新生成的 user.csv 能被
完整加载并驱动仿真，产出事件包下的 record.json。

⚠️ 会真实调用 LLM（花钱）。默认只跑 1 天以最小化花费。

用法：
    python -m experiments.smoke_test --event demo
    python -m experiments.smoke_test --event demo --days 2
    python -m experiments.smoke_test --event demo --dry-run
"""

import argparse
import copy
import sys
import time
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(description='仿真冒烟测试')
    ap.add_argument('--event', default='demo',
                    help='Event id (default: demo)')
    ap.add_argument('--run-id', default='smoke')
    ap.add_argument('--days', type=int, default=1, help='模拟天数（默认 1，最小花费）')
    ap.add_argument('--dry-run', action='store_true',
                    help='只验证 user.csv 加载与 agent 初始化，不调用 LLM')
    args = ap.parse_args()

    from simulation.infra.utils import load_user
    from simulation.core.agent import Agent
    from simulation.core import mechanism as mech

    from simulation.infra.event import build_config
    cfg = build_config(args.event, run_id=args.run_id)
    cfg['simulation_config']['total_days'] = args.days
    cfg['simulation_config']['government_decision_days'] = [1] if args.days >= 1 else []

    # ---- 第一步：加载与初始化（不花钱）----
    # 这里构造的 Agent 只用于字段检查；仍先播种，保证检查结果与正式运行一致。
    import random
    random.seed(cfg['simulation_config'].get('seed', 42))

    users = load_user(cfg)
    expected = cfg['simulation_config'].get('max_agents', len(users))
    print(f'user.csv 加载到 {len(users)} 行（期望 {expected}）')
    if len(users) < expected:
        sys.exit(f'[失败] 期望至少 {expected} 行，实际 {len(users)} 行')

    agents = [Agent(cfg, u, None) for u in users]
    print(f'构造 {len(agents)} 个 Agent 成功')

    a = agents[0]
    prompt = mech.get_action_prompt(a, '（冒烟测试信息流）')
    missing = [k for k, v in [('name', a.name), ('age', a.age), ('gender', a.gender),
                              ('big_five', a.personality),
                              ('activity', a.activity)] if str(v) not in prompt]
    if missing:
        sys.exit(f'[失败] 这些画像字段没有进入 prompt: {missing}')
    print('画像字段全部进入 prompt')

    activity_prob = cfg['agent_config']['activity_prob']
    bad = {u['activity'] for u in users if u['activity'] not in activity_prob}
    if bad:
        sys.exit(f'[失败] 非法 activity 值: {bad}')
    print('activity 取值全部合法')

    if args.dry_run:
        print('\n[dry-run] 加载与初始化验证通过，未调用 LLM。')
        return

    # ---- 第二步：真正跑仿真（花钱）----
    from simulation.infra.llm import create_llm
    from simulation.infra.recorder import Recorder
    from simulation.core.environment import Simulator
    from simulation.infra.utils import Checkpoint
    from simulation.infra.logger import Logger

    print(f'\n开始仿真：{args.days} 天、{len(agents)} agents（会调用 LLM）')
    t0 = time.time()
    logger = Logger(cfg.get('data_path', {}).get('log'))
    llm = create_llm(cfg, logger=logger)
    recorder = Recorder(cfg)
    checkpoint = Checkpoint(cfg, t0)
    simulator = Simulator(cfg, llm, recorder, checkpoint, logger=logger)
    recorder.add_record('META', {'user_count': len(users), 'smoke_test': True})
    simulator.run()
    recorder.write_output()
    logger.close()

    record_path = Path(cfg['data_path']['record'])
    if not record_path.exists():
        sys.exit(f'[失败] 没有产出 {record_path}')

    import json
    out = json.loads(record_path.read_text(encoding='utf-8'))
    snaps = out.get('daily_snapshots', [])
    print(f'\n[通过] record.json 已产出，daily_snapshots={len(snaps)} 天，'
          f'用时 {time.time() - t0:.0f}s')
    print(f'  meta_info.user_count = {out.get("meta_info", {}).get("user_count")}')
    for s in snaps:
        print(f"  Day {s['day']}: panic={s['avg_panic']:.3f} "
              f"trust={s['avg_trust']:.3f} rumor={s.get('avg_rumor', 0):.3f}")
    llm.print_total_consumption()


if __name__ == '__main__':
    main()
