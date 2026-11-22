"""
步骤 7：各阶段"真实负面情绪" —— 自然日窗口内的阶段汇总。

输入：interim/comments_clean.csv + interim/comment_scores.jsonl
输出：processed/phase_sentiment.json

⚠️ 唯一可用的真实侧指标是 negative = (anxiety + anger) / 2。
   评论文本无法可靠提取“政府信任”“恐慌指数”“谣言易感性”，
   因此 distrust / rumor 只保留为语料描述，不作为仿真状态的验证目标。

Window, phases, and validation variants come from the event package.

Usage:
    python -m personas.phase_sentiment --event demo
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

METRICS = ['negative']


def metric_value(score, metric):
    if metric == 'negative':
        return (score.get('anxiety', 0.0) + score.get('anger', 0.0)) / 2.0
    return score.get(metric, 0.0)


def load(interim: Path):
    with open(interim / 'comments_clean.csv', 'r', encoding='utf-8', newline='') as f:
        comments = {r['comment_id']: r for r in csv.DictReader(f)}
    scores = {}
    with open(interim / 'comment_scores.jsonl', 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    o = json.loads(line)
                    scores[o['comment_id']] = o
                except json.JSONDecodeError:
                    pass
    return comments, scores


def aggregate(pairs: list, phases, phase_names) -> dict:
    """把 (评论, 打分) 对聚合为各阶段指标。"""
    phases = list(phases)
    phase_names = dict(phase_names)
    by_phase = defaultdict(list)
    for c, s in pairs:
        by_phase[c['phase']].append(s)

    out = {}
    for p in phases:
        ss = by_phase.get(p, [])
        rec = {'n_comments': len(ss), 'phase_name': phase_names.get(p, p)}
        for m in METRICS:
            vals = [metric_value(s, m) for s in ss]
            rec[m] = round(sum(vals) / len(vals), 4) if vals else None
        out[p] = rec
    return out


def _field(comment: dict, score: dict, field: str):
    if field in score:
        return score[field]
    if field in comment:
        return comment[field]
    if field == 'channel':
        return comment.get('channel') or comment.get('subreddit')
    return None


def _match_filter(comment: dict, score: dict, spec: dict) -> bool:
    field = str(spec.get('field') or '')
    op = str(spec.get('op') or 'eq')
    value = spec.get('value')
    values = spec.get('values')
    actual = _field(comment, score, field)
    if op == 'exists':
        return actual is not None
    if actual is None:
        return False
    try:
        if op == 'eq':
            return actual == value
        if op == 'neq':
            return actual != value
        if op == 'in':
            return actual in (values or [])
        if op == 'not_in':
            return actual not in (values or [])
        if op == 'lte':
            return float(actual) <= float(value)
        if op == 'gte':
            return float(actual) >= float(value)
    except (TypeError, ValueError):
        return False
    return False


def _apply_filters(pairs: list, filters: list) -> list:
    if not filters:
        return pairs
    return [
        (c, s) for c, s in pairs
        if all(_match_filter(c, s, f) for f in filters)
    ]


def _event_variants(base: list, spec) -> dict:
    configured = spec.validation.get('variants') or []
    if not configured:
        return {'all': base}
    out = {}
    for item in configured:
        vid = str(item.get('id') or 'variant')
        out[vid] = _apply_filters(base, item.get('filters') or [])
    if 'all' not in out:
        out['all'] = base
    return out


def _phase_boundary_texts(spec) -> dict:
    from datetime import date, timedelta
    start = date.fromisoformat(spec.start_date)
    phases = sorted(spec.phases, key=lambda p: int(p.get('start_day', 0)))
    out = {}
    for i, ph in enumerate(phases):
        lo = start + timedelta(days=int(ph['start_day']) - 1)
        hi = start + timedelta(days=int(ph['end_day']) - 1)
        if i == 0:
            out[str(ph['id'])] = f'<={hi.isoformat()}'
        elif i == len(phases) - 1:
            out[str(ph['id'])] = f'>={lo.isoformat()}'
        else:
            out[str(ph['id'])] = f'{lo.isoformat()}..{hi.strftime("%m-%d")}'
    return out


def _note(spec) -> str:
    return (f'基于 {spec.start_date}~{spec.end_date} 的 {spec.total_days} 天自然日窗口。'
            '唯一可用的真实侧指标是 negative=(anxiety+anger)/2；'
            '评论无法可靠提取“政府信任”“恐慌指数”“谣言易感性”，'
            '因此 distrust/rumor 不作为仿真状态的真实侧测量。'
            '当前语料可能是真实、合成或混合评论，不能当作真实世界的舆情测量。')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(description='Per-phase sentiment summary')
    ap.add_argument('--event', default='demo', help='Event id (default: demo)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    from simulation.infra.event import (
        event_paths, load_event, phase_names)
    spec = load_event(args.event)
    paths = event_paths(spec)
    interim, processed = paths.interim_dir, paths.processed_dir
    phases = [str(p['id']) for p in sorted(
        spec.phases, key=lambda p: int(p.get('start_day', 0)))]
    phase_name_map = phase_names(spec)
    window_start, window_end = spec.start_date, spec.end_date
    out_path = paths.phase_sentiment_json
    boundary_text = _phase_boundary_texts(spec)
    note = _note(spec)

    processed.mkdir(parents=True, exist_ok=True)
    comments, scores = load(interim)

    base = [(comments[cid], s) for cid, s in scores.items()
            if cid in comments and s.get('on_topic') == 1
            and window_start <= (comments[cid].get('published') or '')[:10] <= window_end]
    if not base:
        sys.exit(f'没有落在 {window_start}~{window_end} 窗口内的 on_topic=1 评论可供聚合')

    variants = _event_variants(base, spec)

    result = {
        'note': note,
        'primary_metric': 'negative',
        'metric_mapping': {'negative': '(anxiety + anger) / 2'},
        'not_validation_targets': {
            'distrust': '评论文本无法可靠测量政府信任，只能作语料描述',
            'rumor': '评论文本无法可靠测量谣言易感性，只能作语料描述',
            'anxiety_as_panic': 'anxiety 是负面情绪的成分，不等于仿真中的 panic 指数',
        },
        'window': {'start': window_start, 'end': window_end},
        'phase_boundaries': boundary_text,
        'attribution_rule': '按每条评论自己的 published 归属，不用帖子日期',
        'variants': {name: aggregate(pairs, phases, phase_name_map)
                     for name, pairs in variants.items()},
    }

    for name, agg in result['variants'].items():
        print(f"\n=== 口径: {name} ===")
        print(f"{'阶段':4s} {'名称':22s} {'n':>5s} " +
              ' '.join(f'{m:>9s}' for m in METRICS))
        for p in phases:
            r = agg[p]
            cells = ' '.join(
                f"{r[m]:9.3f}" if r[m] is not None else f"{'—':>9s}" for m in METRICS)
            print(f"{p:4s} {r['phase_name']:22s} {r['n_comments']:5d} {cells}")

    print('\n=== 归一化形状（0-1），仅 all 口径 ===')
    agg = result['variants'].get('all') or {}
    for m in METRICS:
        vals = [agg[p][m] for p in phases if agg.get(p, {}).get(m) is not None]
        if not vals:
            continue
        lo, hi = min(vals), max(vals)
        rng = (hi - lo) or 1.0
        norm = {p: (round((agg[p][m] - lo) / rng, 3)
                    if agg.get(p, {}).get(m) is not None else None)
                for p in phases}
        print(f'  {m:9s} {norm}')

    if 'P1' in phases and 'P3' in phases:
        n1 = (agg.get('P1') or {}).get('negative')
        n3 = (agg.get('P3') or {}).get('negative')
        print('\n=== sanity 检查 ===')
        print(f"  negative  P1={n1} -> P3={n3}  "
              f"{'上升 ✓' if (n3 or 0) > (n1 or 0) else '未上升 ✗'}")

    if args.dry_run:
        print('\n[dry-run] 未写入')
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'\n已写入 {out_path}')


if __name__ == '__main__':
    main()
