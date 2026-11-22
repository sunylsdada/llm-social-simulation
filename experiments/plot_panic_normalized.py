"""绘制仿真恐慌指数的 min-max 归一化曲线。

不读取真实数据，不做任何对比。

用法：
    python -m experiments.plot_panic_normalized --event demo --run-id baseline
"""

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SURFACE = '#fcfcfb'
INK = '#0b0b0b'
SECONDARY = '#52514e'
MUTED = '#898781'
GRID = '#e1e0d9'
AXIS = '#c3c2b7'
PANIC = '#2a78d6'   # 单序列，使用分类槽 1

PHASES = [('P1', 1, 3), ('P2', 4, 7), ('P3', 8, 11), ('P4', 12, 15)]


def _abs(path):
    from simulation.infra.config import PROJECT_ROOT
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_panic(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    snaps = sorted(data.get('daily_snapshots') or [],
                   key=lambda s: int(s['day']))
    days = [int(s['day']) for s in snaps]
    raw = [float(s['avg_panic']) for s in snaps]
    if not raw:
        raise SystemExit(f'record 里没有 daily_snapshots: {path}')
    lo, hi = min(raw), max(raw)
    if hi - lo < 1e-12:
        norm = [0.5] * len(raw)
    else:
        norm = [(v - lo) / (hi - lo) for v in raw]
    return days, raw, norm, lo, hi


def main():
    global PHASES
    parser = argparse.ArgumentParser(description='只画归一化恐慌曲线')
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
    out_path = _abs(args.out) if args.out else paths.outputs_dir / 'figures' / 'panic_normalized.png'
    PHASES = [
        (str(p['id']), int(p['start_day']), int(p['end_day']))
        for p in sorted(spec.phases, key=lambda x: int(x['start_day']))
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    days, raw, norm, lo, hi = load_panic(record_path)

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(figsize=(11.0, 5.6), facecolor=SURFACE)

    for i, (name, start, end) in enumerate(PHASES):
        if i % 2 == 0:
            ax.axvspan(start - 0.5, end + 0.5, color='#f0efec', alpha=0.55, zorder=0)
        ax.text((start + end) / 2, 1.045, name, ha='center', va='bottom',
                fontsize=8.5, color=MUTED)

    ax.fill_between(days, norm, 0, color=PANIC, alpha=0.08, zorder=1)
    ax.plot(days, norm, color=PANIC, marker='o', lw=2.2, ms=6.5,
            mec=SURFACE, mew=1.5, zorder=3)

    peak_i = max(range(len(norm)), key=lambda i: norm[i])
    ax.plot([days[peak_i]], [norm[peak_i]], marker='o', ms=10,
            color=PANIC, mec=SURFACE, mew=2, zorder=4)
    ax.annotate(f'峰值 D{days[peak_i]} = {norm[peak_i]:.2f}',
                xy=(days[peak_i], norm[peak_i]),
                xytext=(days[peak_i] + 0.5, 1.04),
                fontsize=9, color=SECONDARY, va='center',
                arrowprops=dict(arrowstyle='-', color=AXIS, lw=0.8))

    ax.annotate(f'{norm[0]:.2f}', xy=(days[0], norm[0]),
                xytext=(days[0] + 0.35, norm[0] + 0.06),
                fontsize=8.5, color=SECONDARY)
    ax.annotate(f'{norm[-1]:.2f}', xy=(days[-1], norm[-1]),
                xytext=(days[-1] - 0.55, norm[-1] - 0.08),
                fontsize=8.5, color=SECONDARY)

    ax.set_ylim(0, 1.12)
    ax.set_xlim(0.5, len(days) + 0.5)
    ax.set_ylabel('归一化恐慌指数（0–1）', fontsize=10, color=SECONDARY)
    ax.set_xlabel('仿真日', fontsize=10, color=SECONDARY)
    ax.set_title(f'仿真恐慌指数：{len(days)} 天 min-max 归一化曲线',
                 loc='left', fontsize=13, fontweight='bold', color=INK, pad=10)
    ax.grid(True, color=GRID, lw=0.8, alpha=0.85)
    ax.set_axisbelow(True)
    ax.set_xticks(days)
    ax.set_xticklabels([f'D{d}' for d in days], fontsize=8.5, color=MUTED)
    ax.tick_params(axis='y', labelsize=8.5, colors=MUTED)

    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    for sp in ('left', 'bottom'):
        ax.spines[sp].set_color(AXIS)
        ax.spines[sp].set_linewidth(0.8)

    fig.text(0.01, 0.040,
             f'原始 panic 范围 {lo:.3f}–{hi:.3f}；归一化公式：'
             '(panic - min) / (max - min)。',
             fontsize=7.8, color=SECONDARY)
    fig.text(0.01, 0.012,
             '曲线为仿真内部状态；具体规模/模式/种子见 record meta；'
             '仅描述本次仿真结果。',
             fontsize=7.8, color=SECONDARY)

    fig.tight_layout(rect=(0, 0.075, 1, 1))
    fig.savefig(out_path, dpi=180, facecolor=SURFACE)
    plt.close(fig)
    print(f'输出: {out_path}')
    print(f'原始范围: {lo:.3f}–{hi:.3f}; 峰值 D{days[peak_i]} = {norm[peak_i]:.2f}; '
          f'首末 = {norm[0]:.2f} → {norm[-1]:.2f}')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    main()
