"""
只画仿真结果：三个状态曲线 + 个体恐慌轨迹。

不读取真实负面情绪曲线，不读取基线 record，不做任何对比。

用法：
    python -m experiments.plot_simulation_results --event demo --run-id baseline
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 绘图参考调色板（浅色背景）
SURFACE = '#fcfcfb'
INK = '#0b0b0b'
SECONDARY = '#52514e'
MUTED = '#898781'
GRID = '#e1e0d9'
AXIS = '#c3c2b7'
PANIC = '#2a78d6'       # 分类槽 1：blue
RUMOR = '#eb6834'       # 分类槽 2：orange
TRUST = '#1baf7a'       # 分类槽 3：aqua
INDIVIDUAL = '#c3c2b7'  # 个体轨迹：中性灰，不占用分类色位

PHASES = [('P1', 1, 3), ('P2', 4, 7), ('P3', 8, 11), ('P4', 12, 15)]


def _abs(path):
    from simulation.infra.config import PROJECT_ROOT
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_record(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    snaps = sorted(data.get('daily_snapshots') or [],
                   key=lambda s: int(s['day']))
    days = [int(s['day']) for s in snaps]
    panic = [float(s['avg_panic']) for s in snaps]
    trust = [float(s['avg_trust']) for s in snaps]
    rumor = [float(s['avg_rumor']) for s in snaps]

    individual = defaultdict(dict)
    names = {}
    for a in data.get('agent_actions', []):
        aid = int(a['aid'])
        individual[aid][int(a['day'])] = float(a['panic'])
        names[aid] = a.get('name', f'agent{aid}')

    meta = data.get('meta_info') or {}
    return {
        'days': days,
        'panic': panic,
        'trust': trust,
        'rumor': rumor,
        'individual': individual,
        'names': names,
        'meta': meta,
    }


def main():
    global PHASES
    parser = argparse.ArgumentParser(description='只画仿真状态结果')
    parser.add_argument('--event', default='demo',
                        help='事件 ID（默认 demo）')
    parser.add_argument('--run-id', default='baseline')
    parser.add_argument('--record', default=None)
    parser.add_argument('--out', default=None)
    args = parser.parse_args()

    from simulation.infra.event import event_paths, load_event
    spec = load_event(args.event)
    paths = event_paths(spec)
    record_path = _abs(args.record) if args.record else paths.record_path(args.run_id)
    out_path = _abs(args.out) if args.out else paths.outputs_dir / 'figures' / 'simulation_results.png'
    PHASES = [
        (str(p['id']), int(p['start_day']), int(p['end_day']))
        for p in sorted(spec.phases, key=lambda x: int(x['start_day']))
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    d = load_record(record_path)
    days = d['days']
    panic, trust, rumor = d['panic'], d['trust'], d['rumor']
    individual = d['individual']
    meta = d['meta']

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    fig = plt.figure(figsize=(12.8, 9.2), facecolor=SURFACE)
    gs = fig.add_gridspec(
        2, 1, height_ratios=[1, 1],
        left=0.075, right=0.97, top=0.84, bottom=0.12,
        hspace=0.38)
    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)

    fig.suptitle('时效性机制重演：仿真状态演化',
                 x=0.5, y=0.975, fontsize=17, fontweight='bold', color=INK)
    fig.text(0.5, 0.936,
             '曲线为仿真内部状态；具体规模/模式/种子见 record meta',
             ha='center', fontsize=10, color=SECONDARY)

    legend_handles = [
        Line2D([0], [0], color=PANIC, marker='o', lw=2, ms=6, label='恐慌'),
        Line2D([0], [0], color=TRUST, marker='s', lw=2, ms=6, label='政府信任'),
        Line2D([0], [0], color=RUMOR, marker='^', lw=2, ms=6, label='谣言易感性'),
    ]
    fig.legend(handles=legend_handles, loc='upper center',
               bbox_to_anchor=(0.5, 0.905), ncol=3, frameon=False,
               fontsize=10, handlelength=2.2, columnspacing=2.0)

    # ---------------- A. 全网平均状态 ----------------
    for i, (name, lo, hi) in enumerate(PHASES):
        if i % 2 == 0:
            ax1.axvspan(lo - 0.5, hi + 0.5, color='#f0efec', alpha=0.55, zorder=0)
        ax1.text((lo + hi) / 2, 1.045, name, ha='center', va='bottom',
                 fontsize=8, color=MUTED)

    ax1.plot(days, panic, color=PANIC, marker='o', lw=2.2, ms=6,
             mec=SURFACE, mew=1.5, zorder=3)
    ax1.plot(days, trust, color=TRUST, marker='s', lw=2.0, ms=6,
             mec=SURFACE, mew=1.5, zorder=3)
    ax1.plot(days, rumor, color=RUMOR, marker='^', lw=2.0, ms=6,
             mec=SURFACE, mew=1.5, zorder=3)

    # 末端直接标注（带细引线，避免三条线末端挤在一起）
    end_labels = [
        (panic[-1], 0.80, '恐慌 {:.2f}'.format(panic[-1]), PANIC),
        (rumor[-1], 0.53, '谣言 {:.2f}'.format(rumor[-1]), RUMOR),
        (trust[-1], 0.38, '信任 {:.2f}'.format(trust[-1]), TRUST),
    ]
    for y_end, y_text, label, color in end_labels:
        ax1.annotate(label, xy=(days[-1], y_end), xytext=(15.7, y_text),
                     fontsize=8.5, color=SECONDARY, va='center',
                     arrowprops=dict(arrowstyle='-', color=AXIS, lw=0.8))
        ax1.plot([days[-1] + 0.30], [y_end], marker='o', ms=4,
                 color=color, mec=SURFACE, mew=1.2, clip_on=False, zorder=4)

    init_panic = meta.get('initial_avg_panic')
    init_trust = meta.get('initial_avg_trust')
    init_rumor = meta.get('initial_avg_rumor')
    if all(v is not None for v in (init_panic, init_trust, init_rumor)):
        init_text = (f'初值均值：恐慌 {init_panic:.3f} / 信任 {init_trust:.3f} / '
                     f'谣言 {init_rumor:.3f}')
    else:
        init_text = '初值均值：见 record meta'
    ax1.text(0.015, 0.025,
             init_text + '\n' +
             f'第 {days[-1]} 天：恐慌 {panic[-1]:.3f} / 信任 {trust[-1]:.3f} / '
             f'谣言 {rumor[-1]:.3f}',
             transform=ax1.transAxes, fontsize=8.3, color=SECONDARY,
             va='bottom', bbox=dict(boxstyle='round,pad=0.35',
                                    facecolor=SURFACE, edgecolor=GRID,
                                    alpha=0.95))

    ax1.set_ylim(0, 1.10)
    ax1.set_xlim(0.5, days[-1] + 0.5)
    ax1.set_ylabel('状态值（0–1）', fontsize=10, color=SECONDARY)
    ax1.set_title('A. 全网平均状态', loc='left',
                  fontsize=11.5, fontweight='bold', color=INK, pad=8)
    ax1.grid(True, color=GRID, lw=0.8, alpha=0.85)
    ax1.set_axisbelow(True)
    ax1.tick_params(axis='y', labelsize=8, colors=MUTED)
    ax1.tick_params(axis='x', labelbottom=False)

    # ---------------- B. 个体恐慌轨迹 ----------------
    for i, (name, lo, hi) in enumerate(PHASES):
        if i % 2 == 0:
            ax2.axvspan(lo - 0.5, hi + 0.5, color='#f0efec', alpha=0.55, zorder=0)

    for aid in sorted(individual):
        ys = [individual[aid].get(day) for day in days]
        ax2.plot(days, ys, color=INDIVIDUAL, lw=0.9, alpha=0.85,
                 zorder=2)
        # 只在有观测的日子上打小点，空白表示当天未上线
        obs_x = [day for day in days if individual[aid].get(day) is not None]
        obs_y = [individual[aid][day] for day in obs_x]
        ax2.plot(obs_x, obs_y, linestyle='none', marker='o', ms=2.6,
                 color=INDIVIDUAL, zorder=2)

    ax2.plot(days, panic, color=PANIC, lw=2.4, zorder=4)

    ax2.annotate(f'全网均值 {panic[-1]:.2f}', xy=(days[-1], panic[-1]),
                 xytext=(days[-1] - 0.3, panic[-1] + 0.06), fontsize=8.5,
                 color=SECONDARY, va='center',
                 arrowprops=dict(arrowstyle='-', color=AXIS, lw=0.8))
    ax2.plot([days[-1] + 0.30], [panic[-1]], marker='o', ms=4,
             color=PANIC, mec=SURFACE, mew=1.2, clip_on=False, zorder=5)

    ax2.text(0.015, 0.025,
             '每条细线 = 1 个 agent；空白 = 当天未上线\n'
             '细线为个体 agent 的恐慌轨迹',
             transform=ax2.transAxes, fontsize=8.3, color=SECONDARY,
             va='bottom', bbox=dict(boxstyle='round,pad=0.35',
                                    facecolor=SURFACE, edgecolor=GRID,
                                    alpha=0.95))

    indiv_handle = Line2D([0], [0], color=INDIVIDUAL, lw=0.9,
                          label=f'个体 agent（n={len(individual)}）')
    mean_handle = Line2D([0], [0], color=PANIC, lw=2.4, label='全网均值')
    ax2.legend(handles=[indiv_handle, mean_handle], loc='lower right',
               frameon=False, fontsize=8.5)

    ax2.set_ylim(0, 1.0)
    ax2.set_xlim(0.5, days[-1] + 0.5)
    ax2.set_ylabel('恐慌指数（0–1）', fontsize=10, color=SECONDARY)
    ax2.set_title('B. 个体恐慌轨迹', loc='left',
                  fontsize=11.5, fontweight='bold', color=INK, pad=8)
    ax2.grid(True, color=GRID, lw=0.8, alpha=0.85)
    ax2.set_axisbelow(True)
    ax2.set_xticks(days)
    ax2.set_xticklabels([f'D{d}' for d in days], fontsize=8, color=MUTED)
    ax2.tick_params(axis='y', labelsize=8, colors=MUTED)

    for ax in (ax1, ax2):
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
        for sp in ('left', 'bottom'):
            ax.spines[sp].set_color(AXIS)
            ax.spines[sp].set_linewidth(0.8)

    fig.text(0.01, 0.018,
             '注：本图只包含仿真输出，不含真实数据或基线对比。',
             fontsize=7.5, color=SECONDARY)
    fig.text(0.01, 0.003,
             '曲线为仿真内部状态，'
             '不做外部效度声明。',
             fontsize=7.5, color=SECONDARY)

    fig.savefig(out_path, dpi=180, facecolor=SURFACE)
    plt.close(fig)
    print(f'输出: {out_path}')
    print(f"恐慌: {panic[0]:.3f} → {panic[-1]:.3f}，峰值 day "
          f"{panic.index(max(panic)) + 1} ({max(panic):.3f})")
    print(f"信任: {trust[0]:.3f} → {trust[-1]:.3f}")
    print(f"谣言: {rumor[0]:.3f} → {rumor[-1]:.3f}")
    net_up = sum(
        1 for aid in individual
        if max(individual[aid].values()) > min(individual[aid].values()))
    print(f"个体净上升: {net_up}/{len(individual)}")


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    main()
