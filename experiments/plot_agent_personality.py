"""
Plot the Big Five distribution of the agent population.

左图：五个特质各覆盖多少个 agent（一个 agent 可含多个特质）
右图：13 种人格组合各有多少个 agent

输入：events/<id>/processed/user.csv
输出：events/<id>/outputs/figures/agent_personality.png

用法:
    python experiments/plot_agent_personality.py
"""

import csv
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TRAITS = [
    ('Openness', '开放性 O'),
    ('Conscientiousness', '尽责性 C'),
    ('Extraversion', '外向性 E'),
    ('Agreeableness', '宜人性 A'),
    ('Neuroticism', '神经质 N'),
]
SHORT = {'Openness': 'O', 'Conscientiousness': 'C', 'Extraversion': 'E',
         'Agreeableness': 'A', 'Neuroticism': 'N'}
SEP = '；'


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    import argparse
    ap = argparse.ArgumentParser(description='画网民 agent 的大五人格分布')
    ap.add_argument('--event', default='demo',
                    help='Event id (default: demo)')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    from simulation.infra.event import event_paths, load_event
    paths = event_paths(load_event(args.event))
    user_path = paths.user_csv
    out_path = Path(args.out) if args.out else paths.outputs_dir / 'figures' / 'agent_personality.png'

    rows = list(csv.DictReader(open(user_path, encoding='utf-8')))
    if not rows:
        raise SystemExit('user.csv 为空')

    # 单特质覆盖
    per_trait = Counter()
    combos = Counter()
    for r in rows:
        labels = [x.strip() for x in r['big_five'].split(SEP) if x.strip()]
        for lab in labels:
            per_trait[lab] += 1
        combos['+'.join(SHORT.get(x, x) for x in labels)] += 1

    print(f'agent 数: {len(rows)}')
    print('单特质覆盖:')
    for key, label in TRAITS:
        print(f'  {label:8s} {per_trait[key]:2d}')
    print('\n人格组合分布:')
    for c, n in combos.most_common():
        print(f'  {c:8s} {n:2d}')

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5),
                                   gridspec_kw={'width_ratios': [1, 1.25]})

    # ---- 左图：单特质 ----
    labels = [lab for _, lab in TRAITS]
    values = [per_trait[key] for key, _ in TRAITS]
    colors = ['#4c78a8', '#59a14f', '#f28e2b', '#e15759', '#b07aa1']
    bars = ax1.bar(labels, values, color=colors, alpha=0.85)
    ax1.set_title('各人格特质覆盖的 agent 数（可多选）', fontsize=12)
    ax1.set_ylabel('agent 数')
    ax1.set_ylim(0, max(values) + 5)
    ax1.grid(axis='y', alpha=0.3)
    for b, v in zip(bars, values):
        ax1.text(b.get_x() + b.get_width() / 2, v + 0.4, str(v),
                 ha='center', va='bottom', fontsize=10, fontweight='bold')
    ax1.tick_params(axis='x', labelsize=9)

    # ---- 右图：人格组合 ----
    pairs = combos.most_common()
    names = [c for c, _ in pairs]
    counts = [n for _, n in pairs]
    y = range(len(names))
    ax2.barh(list(y), counts, color='#72b7b2', alpha=0.85)
    ax2.set_yticks(list(y))
    ax2.set_yticklabels(names, fontsize=9)
    ax2.invert_yaxis()
    ax2.set_title('人格组合分布（共 %d 种）' % len(names), fontsize=12)
    ax2.set_xlabel('agent 数')
    ax2.set_xlim(0, max(counts) + 1)
    ax2.grid(axis='x', alpha=0.3)
    for yi, v in zip(y, counts):
        ax2.text(v + 0.1, yi, str(v), va='center', fontsize=9)

    fig.suptitle(f'{len(rows)} 个网民 agent 的大五人格分布',
                 fontsize=13, fontweight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f'\n已写入 {out_path}')


if __name__ == '__main__':
    main()
