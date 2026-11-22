"""
策略效果预检：五个策略**能不能被区分开**？

这是整个"策略评估"设计的前提检验。如果五个策略跑出来的结果几乎一样，
那么后面的矩阵验证、checkpoint 分支全都无从谈起 —— 此时该先修动作包，
而不是继续投钱做实验。

做法：从**同一个初始状态**出发，强制每个策略各跑一遍，比较各自的轨迹。
用小规模设定（默认 10 个 agent、5 天）把成本压到最低。

⚠️ 会真实调用 LLM。默认规模约几块钱。

用法：
    python -m experiments.strategy_effect_pilot --event demo
    python -m experiments.strategy_effect_pilot --event demo --agents 20 --days 7
    python -m experiments.strategy_effect_pilot --event demo --seeds 3
"""

import argparse
import copy
import json
import random
import statistics as st
import sys
import time
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STRATEGIES = ['S1', 'S2', 'S3', 'S4', 'S5']
NAMES = {
    'S1': '沉默观望', 'S2': '压制封堵', 'S3': '坦诚回应',
    'S4': '转移议程', 'S5': '组合应对',
}


def run_one(base_cfg, strategy, total_days, max_agents, logger,
            model_name=None, seed=0):
    """从同一初始状态强制跑一个策略，返回首末指标。"""
    from simulation.infra.llm import create_llm
    from simulation.infra.recorder import Recorder
    from simulation.infra.utils import Checkpoint
    from experiments.forced_strategy import ForcedStrategySimulator

    cfg = copy.deepcopy(base_cfg)
    cfg['simulation_config']['total_days'] = total_days
    cfg['simulation_config']['max_agents'] = max_agents
    cfg['simulation_config']['government_decision_days'] = [1]
    # Simulator 在构造 agent 前会按这个值播种；这里显式写入，保证同一 seed 下
    # 五个策略共享完全相同的初始状态。
    cfg['simulation_config']['seed'] = seed
    if model_name:
        # 模型必须可控：用弱模型跑出的"策略无差异"无法解释 ——
        # 分不清是动作包太弱，还是 agent 对干预内容本来就不敏感。
        cfg['LLM_config']['model_name'] = model_name

    # ---- 关键：固定随机种子，让五个策略共享同一套初始状态 ----
    # Agent 构造时会读取 initial_states.csv，并叠加 random.gauss 个体噪声；
    # 不在构造前播种，每个策略就会得到一套不同的起点。
    # 同一个 seed 下各策略起跑线一致，差异才可归因到策略本身。
    random.seed(seed)

    recorder = Recorder(cfg)
    llm = create_llm(cfg, logger=None)
    checkpoint = Checkpoint(cfg, time.time())
    sim = ForcedStrategySimulator(cfg, llm, recorder, checkpoint, strategy, logger=logger)

    # ⚠️ 必须在 run() 之前抓初始状态。
    # recorder.daily_snapshots[0] 是**第 1 天互动之后**的快照（run 的循环是先让
    # agent 行动、再记录），拿它当"首态"会看到跨策略的差异，从而误判种子失效。
    init = {
        'avg_panic': st.mean([a.panic for a in sim.agents]),
        'avg_trust': st.mean([a.government_trust for a in sim.agents]),
        'avg_rumor': st.mean([a.rumor_susceptibility for a in sim.agents]),
    }

    sim.run()

    snaps = recorder.daily_snapshots
    if not snaps:
        return None
    last = snaps[-1]
    daily = [{'day': s['day'],
              'panic': s['avg_panic'],
              'trust': s['avg_trust'],
              'rumor': s.get('avg_rumor', 0)} for s in snaps]
    return {
        'strategy': strategy,
        'first': init,
        'daily': daily,
        'last': {k: last[k] for k in ('avg_panic', 'avg_trust', 'avg_rumor')},
        'comments': last.get('total_comments', 0),
        'tokens': llm.total_consumption if hasattr(llm, 'total_consumption') else 0,
    }


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(description='策略效果预检（能否区分五个策略）')
    ap.add_argument('--event', default='demo',
                    help='Event id (default: demo)')
    ap.add_argument('--agents', type=int, default=10)
    ap.add_argument('--days', type=int, default=5)
    ap.add_argument('--seeds', type=int, default=1)
    ap.add_argument('--strategies', default='S1,S2,S3,S4,S5')
    ap.add_argument('--model', default=None,
                    help='覆盖 config 里的模型。默认已是 deepseek-ai/DeepSeek-V4-Flash；'
                         '如果换成弱模型，跑出的“策略无差异”无法解释')
    ap.add_argument('--seed', type=int, default=42,
                    help='初始状态随机种子。同一 seed 下各策略起跑线一致')
    args = ap.parse_args()

    from simulation.infra.logger import Logger
    from simulation.infra.event import build_config, event_paths, load_event
    spec = load_event(args.event)
    paths = event_paths(spec)
    cfg_base = build_config(args.event)
    out_dir = paths.outputs_dir / 'analysis'
    log_path = out_dir / 'pilot.log'
    out_json = out_dir / 'strategy_effect_pilot.json'

    strategies = [s.strip() for s in args.strategies.split(',') if s.strip()]
    n_runs = len(strategies) * args.seeds

    print(f'预检：{len(strategies)} 个策略 × {args.seeds} 个种子 = {n_runs} 次仿真')
    print(f'规模：{args.agents} agents × {args.days} 天（会真实调用 LLM）\n')

    out_dir.mkdir(parents=True, exist_ok=True)
    logger = Logger(log_path=str(log_path))
    results = []

    t0 = time.time()
    for seed_i in range(args.seeds):
        for s in strategies:
            print(f'[{len(results)+1}/{n_runs}] 策略 {s} {NAMES[s]} '
                  f'(seed {seed_i}) ...', flush=True)
            r = run_one(cfg_base, s, args.days, args.agents, logger,
                        model_name=args.model, seed=args.seed + seed_i)
            if r:
                r['seed'] = seed_i
                results.append(r)
                print(f'       末态 panic={r["last"]["avg_panic"]:.3f} '
                      f'trust={r["last"]["avg_trust"]:.3f} '
                      f'rumor={r["last"]["avg_rumor"]:.3f}')
    logger.close()
    print(f'\n总用时 {time.time()-t0:.0f}s\n')

    # ---- 汇总 ----
    by_strategy = {}
    for r in results:
        by_strategy.setdefault(r['strategy'], []).append(r)

    KEYMAP = {'avg_panic': 'panic', 'avg_trust': 'trust', 'avg_rumor': 'rumor'}

    def traj(r, k):
        """轨迹均值（≈曲线下面积）。

        为什么不能只看末态：本仿真里 panic 会在 5 天内单调冲到 ~0.95、
        trust 掉到 ~0.02，**末态贴在天花板上**，策略间的真实差异被压没了。
        轨迹均值保留中段的分离信息。
        """
        return st.mean([d[KEYMAP[k]] for d in r['daily']])

    print('=' * 100)
    print('%-4s %-10s | %-22s | %-22s | %-22s' %
          ('', '', '首态(应跨策略相同)', '轨迹均值(抗饱和)', '末态(易饱和)'))
    print('%-4s %-10s | %7s %7s %7s | %7s %7s %7s | %7s %7s %7s' %
          ('策略', '名称', 'panic', 'trust', 'rumor',
           'panic', 'trust', 'rumor', 'panic', 'trust', 'rumor'))
    print('=' * 100)
    for s in strategies:
        rs = by_strategy.get(s)
        if not rs:
            continue
        f = {k: st.mean([r['first'][k] for r in rs]) for k in rs[0]['first']}
        t = {k: st.mean([traj(r, k) for r in rs]) for k in KEYMAP}
        l = {k: st.mean([r['last'][k] for r in rs]) for k in rs[0]['last']}
        print('%-4s %-10s | %7.3f %7.3f %7.3f | %7.3f %7.3f %7.3f | %7.3f %7.3f %7.3f' %
              (s, NAMES[s], f['avg_panic'], f['avg_trust'], f['avg_rumor'],
               t['avg_panic'], t['avg_trust'], t['avg_rumor'],
               l['avg_panic'], l['avg_trust'], l['avg_rumor']))
    print('=' * 100)

    # ---- 区分度 vs 噪声底 ----
    # 策略间极差 = 信号；同一策略跨种子的标准差 = 噪声。
    # 只有信号明显大于噪声（>2 倍），才能说"能区分"。
    print('\n区分度（策略间极差）与噪声底（同策略跨种子 SD）:')
    print('%-24s %10s %10s  %s' % ('口径/指标', '策略间极差', '组内SD', '判定'))
    verdict, noise = {}, {}
    for label, getter in (('末态', lambda r, k: r['last'][k]),
                          ('轨迹均值', traj)):
        for k in KEYMAP:
            groups = [[getter(r, k) for r in by_strategy[s]]
                      for s in strategies if s in by_strategy]
            means = [st.mean(g) for g in groups]
            spread = max(means) - min(means)
            # 单种子时，用组内 SD 估计不了噪声，此时只能看极差
            sds = [st.pstdev(g) for g in groups if len(g) > 1]
            sd = st.mean(sds) if sds else float('nan')
            ratio = (spread / sd) if (sd == sd and sd > 0) else float('nan')
            judge = ('信号>2×噪声 ✓' if ratio == ratio and ratio > 2
                     else ('信号≈噪声 ✗' if ratio == ratio else '(单种子，无法估噪声)'))
            print('%-24s %10.4f %10.4f  %s' %
                  (f'{label}/{k}', spread, sd, judge))
            if label == '轨迹均值':
                verdict[k] = spread
            noise[k] = sd

    print('\n' + '=' * 60)

    # ---- 有效性自检：首态必须跨策略一致 ----
    init_spread = 0.0
    for k in KEYMAP:
        vals = [st.mean([r['first'][k] for r in by_strategy[s]])
                for s in strategies if s in by_strategy]
        if vals:
            init_spread = max(init_spread, max(vals) - min(vals))

    if init_spread > 1e-6:
        print('!! 自检失败：五个策略的**首态不一致**（首态极差 %.4f）' % init_spread)
        print('   差异无法归因到策略。请确认 --seed 生效。')
    else:
        print('自检通过：五个策略首态一致（极差 %.2e）' % init_spread)
        max_spread = max(verdict.values()) if verdict else 0
        max_noise = max((v for v in noise.values() if v == v), default=float('nan'))
        print('以**轨迹均值**为准（末态已饱和，不可用）:')
        if max_spread < 0.02:
            print('结论：**区分不出来**（轨迹极差 %.4f < 0.02）。' % max_spread)
            print('  => 先别做后面的实验，回头检查动作包强度。')
        elif max_spread < 0.05:
            print('结论：**区分度很弱**（轨迹极差 %.4f）。' % max_spread)
            print('  => 需要加大动作强度或增加种子数。')
        else:
            print('结论：**能区分**（轨迹极差 %.4f）。' % max_spread)
            if max_noise == max_noise:
                print('  但必须核对信号/噪声比：仅当信号 > 2×噪声才算稳。')
            else:
                print('  注意：当前只有 1 个种子，无法估计噪声 —— '
                      '多跑几个种子再下结论。')
    print('=' * 60)

    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump({'args': vars(args), 'results': results,
                   'spread': verdict}, f, ensure_ascii=False, indent=2)
    print(f'\n已写入 {out_json}')


if __name__ == '__main__':
    main()
