"""
作者级大五人格标注。

与逐条标注的区别
----------------
每一条请求包含**同一个作者的全部评论**，模型只输出**一个**大五标签。
不再"逐条标注 + 作者取众数"。

输出：events/<id>/interim/author_big5.jsonl（追加式缓存，key = 作者名）

Usage:
    python -m personas.author_big5_score --event demo --pilot 20
    python -m personas.author_big5_score --event demo
    python -m personas.author_big5_score --event demo --report
"""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personas.prompts import (BIG5_CODES, BIG5_LABELS,
                              build_author_big5_system, build_author_big5_user,
                              parse_big5_output)



def parse_and_validate(text):
    return parse_big5_output(text)


def load_authors(clean_path: Path):
    """返回 {author: [body, ...]}，按语料顺序。"""
    by = defaultdict(list)
    with open(clean_path, 'r', encoding='utf-8', newline='') as f:
        for r in csv.DictReader(f):
            by[r['author']].append(r.get('body') or '')
    return by


def load_scores(path: Path):
    recs = {}
    if not path.exists():
        return recs
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            recs[o['comment_id']] = o   # 这里 comment_id 存的是作者名
    return recs


def report(by_author, scores):
    print(f'已标注作者: {len(scores)} / {len(by_author)}')

    if not scores:
        return

    combo = Counter(''.join(sorted(set(s['big5']))) for s in scores.values())
    per_trait = Counter(c for s in scores.values() for c in s['big5'])

    print('\n作者级人格组合分布（按作者数）:')
    for c, n in combo.most_common(10):
        print(f'  {c:5s} {n:5d}  ({100*n/len(scores):5.1f}%)')

    print('\n单特质覆盖的作者数:')
    for c in BIG5_CODES:
        print(f'  {c} {BIG5_LABELS[c]:42s} {per_trait[c]:5d}')

    # 按评论条数加权的分布
    weighted = Counter()
    for a, s in scores.items():
        weighted[''.join(sorted(set(s['big5'])))] += len(by_author.get(a, []))
    total = sum(weighted.values())
    print('\n按评论条数加权的分布（前 10）:')
    for c, w in weighted.most_common(10):
        print(f'  {c:5s} {w:5d}  ({100*w/total:5.1f}%)')

    print('\n抽样 5 条:')
    for a, s in list(scores.items())[:5]:
        print(f'  u/{a}: {"".join(s["big5"])} — {s.get("evidence","")[:50]}')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(description='Author-level Big Five labelling')
    ap.add_argument('--event', default='demo', help='Event id (default: demo)')
    ap.add_argument('--pilot', type=int, default=None)
    ap.add_argument('--offset', type=int, default=0)
    ap.add_argument('--model', default=None)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--rpm', type=int, default=300)
    ap.add_argument('--report', action='store_true')
    args = ap.parse_args()

    from simulation.infra.event import build_config, event_paths, load_event
    spec = load_event(args.event)
    paths = event_paths(spec)
    cfg = build_config(args.event)
    clean_path = paths.comments_clean
    cache_path = paths.author_big5_cache
    if not clean_path.exists():
        sys.exit(f'缺少 {clean_path}')

    by_author = load_authors(clean_path)

    if args.report:
        report(by_author, load_scores(cache_path))
        return

    # 只处理有评论的作者；按评论数降序，让信息量大的先跑
    authors = sorted(by_author.items(), key=lambda kv: -len(kv[1]))
    authors = authors[args.offset:]
    if args.pilot:
        authors = authors[:args.pilot]

    from personas.llm_wrapper import ScoringLLM

    model = args.model or cfg['LLM_config']['model_name']
    system_prompt = build_author_big5_system(cfg.get('event_context'))
    print(f'模型: {model}')
    print(f'待标注作者: {len(authors)}（按评论数降序）\n')

    client = ScoringLLM(cfg, cache_path, parse_and_validate,
                        workers=args.workers, rpm=args.rpm, model_name=args.model)

    items = [(a, (a, bodies)) for a, bodies in authors]
    done, failed = client.score_many(
        items, system_prompt,
        user_fn=lambda p: build_author_big5_user(p[0], p[1]))

    print(f'\n完成: 成功 {len(done)}，失败 {len(failed)}')
    if failed:
        for k, err in list(failed.items())[:5]:
            print(f'  {k}: {err[:120]}')
    print(f'用量: {client.usage()}\n')

    report(by_author, load_scores(cache_path))


if __name__ == '__main__':
    main()
