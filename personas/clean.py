"""事件包的确定性评论清洗。

输入：``events/<id>/input/comments.csv``
输出：``events/<id>/interim/comments_clean.csv``

不调用 LLM。清洗规则可通过事件配置的
``input.min_body_chars`` 和 ``input.bad_authors`` 调整。
"""

import argparse
import csv
import re
import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personas.schema import phase_of

BAD_AUTHORS = {'[deleted]', '[removed]', 'AutoModerator'}
MIN_BODY_CHARS = 25

URL_ONLY = re.compile(r'^\s*(https?://\S+\s*)+$')
SYMBOLS_ONLY = re.compile(r'^[\W_]+$', re.UNICODE)


def is_junk(body: str, min_chars: int = MIN_BODY_CHARS) -> bool:
    text = (body or '').strip()
    if len(text) < min_chars:
        return True
    if URL_ONLY.match(text):
        return True
    if SYMBOLS_ONLY.match(text):
        return True
    return False


def normalize_body(body: str) -> str:
    text = (body or '').replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def load_canonical(path: Path) -> list:
    """读取规范评论表。"""
    rows = []
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            channel = (r.get('channel') or r.get('subreddit') or 'unknown').strip()
            rows.append({
                'comment_id': (r.get('comment_id') or '').strip(),
                'author': (r.get('author') or '').strip(),
                'body': r.get('body') or '',
                'published': (r.get('published') or '').strip(),
                'depth': r.get('depth', ''),
                'parent_id': (r.get('parent_id') or '').strip(),
                'source': (r.get('source') or 'other').strip(),
                'source_file': (r.get('source_file') or '').strip(),
                'post_id': (r.get('post_id') or '').strip(),
                'post_title': (r.get('post_title') or '').strip(),
                'subreddit': channel or 'unknown',
            })
    return rows


def clean(rows: list, phase_boundaries,
          min_body_chars: int = MIN_BODY_CHARS,
          bad_authors=None) -> list:
    seen_ids = set()
    bad = set(bad_authors if bad_authors is not None else BAD_AUTHORS)
    out = []
    for r in rows:
        if r['author'] in bad or not r['author']:
            continue
        if is_junk(r['body'], min_body_chars):
            continue

        cid = r['comment_id']
        if not cid or cid in seen_ids:
            stem = r['source_file'] or 'canonical'
            cid = f"{stem}#{len(out) + 1}"
            if cid in seen_ids:
                continue
        seen_ids.add(cid)

        body = normalize_body(r['body'])
        if is_junk(body, min_body_chars):
            continue

        out.append({
            'comment_id': cid,
            'author': r['author'],
            'body': body,
            'published': r['published'],
            'phase': phase_of(r['published'], phase_boundaries),
            'depth': r['depth'],
            'parent_id': r['parent_id'],
            'source': r['source'],
            'post_id': r['post_id'],
            'subreddit': r['subreddit'],
            'post_title': r['post_title'],
            'source_file': r['source_file'],
        })
    return out


def report(rows: list, phases) -> None:
    from collections import Counter
    authors = {r['author'] for r in rows}
    phase_counts = Counter(r['phase'] for r in rows)
    channels = Counter(r['subreddit'] for r in rows)

    print(f'有效评论: {len(rows)}  有效作者: {len(authors)}')
    print('  来源: ' + ', '.join(
        f'{k}={v}' for k, v in Counter(r['source'] for r in rows).most_common()))
    for p in phases:
        print(f'  {p}: {phase_counts.get(p, 0):4d}')
    print('  渠道: ' + ', '.join(
        f'{s}={n}' for s, n in channels.most_common(10)))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(description='清洗事件包评论')
    ap.add_argument('--event', default='demo', help='事件 ID（默认 demo）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    from simulation.infra.event import (
        load_event, event_paths, phase_cutoffs, phase_names)
    spec = load_event(args.event)
    paths = event_paths(spec)
    boundaries = phase_cutoffs(spec)
    phases = tuple(phase_names(spec).keys())

    rows = clean(
        load_canonical(paths.input_comments), boundaries,
        min_body_chars=int(spec.inputs.get('min_body_chars') or MIN_BODY_CHARS),
        bad_authors=spec.inputs.get('bad_authors'),
    )
    report(rows, phases)

    if args.dry_run:
        print('\n[dry-run] not written')
        return

    fields = ['comment_id', 'author', 'body', 'published', 'phase',
              'depth', 'parent_id', 'source',
              'post_id', 'subreddit', 'post_title', 'source_file']
    out_path = paths.comments_clean
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    print(f'\nwrote {out_path}')


if __name__ == '__main__':
    main()
