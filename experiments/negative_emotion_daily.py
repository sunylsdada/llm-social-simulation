"""事件包的日度负面情绪曲线。

指标：``negative = (焦虑 + 愤怒) / 2``，只统计 on_topic=1 的评论。

输入：events/<id>/interim/comments_clean.csv + comment_scores.jsonl
输出：events/<id>/processed/negative_emotion_daily.csv/.json 和图表
"""

import argparse
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MIN_N_PLOT = 40
METRICS = {
    'negative': lambda s: (s.get('anxiety', 0.0) + s.get('anger', 0.0)) / 2.0,
}


def days(start, end):
    d = start
    out = []
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def load(interim: Path):
    scores = {}
    with open(interim / 'comment_scores.jsonl', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                o = json.loads(line)
                scores[o['comment_id']] = o

    rows = []
    with open(interim / 'comments_clean.csv', encoding='utf-8', newline='') as f:
        for r in csv.DictReader(f):
            s = scores.get(r['comment_id'])
            if s and s.get('on_topic') == 1:
                rows.append((r, s))
    return rows


def aggregate(rows, start, end):
    by_day = defaultdict(list)
    for r, s in rows:
        by_day[(r.get('published') or '')[:10]].append((r, s))

    out = []
    for d in days(start, end):
        items = by_day.get(d, [])
        rec = {'day': d, 'n': len(items)}
        for name, fn in METRICS.items():
            vals = [fn(s) for _, s in items]
            rec[name] = round(st.mean(vals), 4) if vals else None
        out.append(rec)
    return out


def normalize(baseline, min_n=MIN_N_PLOT):
    stats = {}
    for key in METRICS:
        vals = [r[key] for r in baseline if r[key] is not None and r['n'] >= min_n]
        lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
        rng = (hi - lo) or 1.0
        stats[key] = {'min': round(lo, 4), 'max': round(hi, 4),
                      'basis': f'n>={min_n}'}
        for r in baseline:
            r[key + '_norm'] = (
                round((r[key] - lo) / rng, 4)
                if r[key] is not None and r['n'] >= min_n else None)
    return stats


def plot(baseline, out_dir: Path, normalized=False, min_n=MIN_N_PLOT):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    suffix = '_norm' if normalized else ''
    tag = f'(n>={min_n})'
    points = [r for r in baseline if r['n'] >= min_n]
    if not points:
        return []

    fig, ax = plt.subplots(figsize=(9, 4.5))
    xs = list(range(1, len(points) + 1))
    ys = [r['negative' + suffix] for r in points]
    ax.plot(xs, ys, marker='o', linewidth=2, color='#7f7f7f', markersize=7)
    ax.set_xticks(xs)
    ax.set_xticklabels([r['day'][5:] for r in points], rotation=45)
    ax.set_xlabel('日期')
    ax.set_ylabel('负面情绪' + ('（归一化）' if normalized else ''))
    ax.set_title(f'日度负面情绪 {tag}')
    ax.grid(True, alpha=0.3)
    for x, y, r in zip(xs, ys, points):
        if y is not None:
            ax.annotate(f"n={r['n']}", (x, y), textcoords='offset points',
                        xytext=(0, 8), ha='center', fontsize=7, color='#555')
    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'negative_negative{suffix}.png'
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return [path]


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(description='Daily negative-emotion curve')
    ap.add_argument('--event', default='demo', help='Event id (default: demo)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    from simulation.infra.event import event_paths, load_event
    spec = load_event(args.event)
    paths = event_paths(spec)
    start = date.fromisoformat(spec.start_date)
    end = date.fromisoformat(spec.end_date)
    min_n = int(spec.validation.get('min_n_per_day') or MIN_N_PLOT)

    rows = load(paths.interim_dir)
    baseline = aggregate(rows, start, end)
    stats = normalize(baseline, min_n)

    print(f'on-topic comments: {len(rows)}\n')
    print(f"{'日期':12s} {'n':>5s} {'negative':>10s}")
    for r in baseline:
        val = '—' if r['negative'] is None else f"{r['negative']:.3f}"
        print(f"{r['day']:12s} {r['n']:5d} {val:>10s}")

    if args.dry_run:
        print('\n[dry-run] not written')
        return

    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    csv_path = paths.negative_daily_csv
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(
            f, fieldnames=['day', 'n', 'negative', 'negative_norm'],
            extrasaction='ignore')
        w.writeheader()
        w.writerows(baseline)

    json_path = paths.negative_daily_json
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({
            'note': ('事件窗口内的日度负面情绪；真实侧语料可能是真实、合成或混合数据。'),
            'primary_metric': 'negative',
            'metric_mapping': {'negative': '(anxiety + anger) / 2'},
            'filters': {'on_topic': 1, 'weight': '每条评论等权'},
            'plot_filter': {'metric': 'n_comments', 'min': min_n},
            'normalization': {'method': '每个指标 min-max',
                              'basis_min_n': min_n, **stats},
            'days': baseline,
        }, f, ensure_ascii=False, indent=2)

    plot_paths = (plot(baseline, paths.outputs_dir / 'figures', min_n=min_n)
                  + plot(baseline, paths.outputs_dir / 'figures',
                         normalized=True, min_n=min_n))
    print(f'\nwrote {csv_path}')
    print(f'已写入 {json_path}')
    for p in plot_paths:
        print(f'已写入 {p}')


if __name__ == '__main__':
    main()
