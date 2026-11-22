"""
根据作者级大五标签构建网民 agent 群体。

流程：
1. 读取 ``interim/author_big5.jsonl``（每个作者一组标签）；
2. 以作者的评论条数作为标签权重；
3. 按比例分配配置的席位数，并对单一标签设上限；
4. 在每个标签内挑选评论数最多的作者；
5. 写出 ``processed/user.csv``，列为 uid/name/age/gender/big_five/activity。

年龄/性别是确定性先验采样，事件可以在 ``event.json`` 中覆盖。
活跃度按评论数百分位切分。

用法：
    python -m personas.build_agents_author --event demo
    python -m personas.build_agents_author --event demo --dry-run
"""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personas.seat_allocation import largest_remainder
from personas.demographics import sample_age_gender
from personas.schema import SEP, USER_COLUMNS, validate_user_row

CODE_TO_TRAIT = {
    'O': 'Openness', 'C': 'Conscientiousness', 'E': 'Extraversion',
    'A': 'Agreeableness', 'N': 'Neuroticism',
}

CAP_SHARE = 0.40          # 单一标签最多占总席位的比例


def load_data(interim: Path):
    """返回 (作者→标签combo, 作者→评论正文列表)。"""
    by_author = defaultdict(list)
    for r in csv.DictReader(open(interim / 'comments_clean.csv', encoding='utf-8')):
        by_author[r['author']].append(r.get('body') or '')

    labels = {}
    with open(interim / 'author_big5.jsonl', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            labels[o['comment_id']] = ''.join(sorted(set(o['big5'])))  # key = 作者名
    return labels, by_author


def capped_alloc(total, weights, supply, cap):
    """最大余数法 + 单标签上限 + 作者供给上限。"""
    keys = list(weights)
    alloc = largest_remainder(total, [weights[k] for k in keys], floor=0)

    def limit(k):
        return min(cap, supply[k])

    for _ in range(len(keys) + 2):
        overflow = 0
        for i, k in enumerate(keys):
            if alloc[i] > limit(k):
                overflow += alloc[i] - limit(k)
                alloc[i] = limit(k)
        if overflow == 0:
            break
        cand = [i for i, k in enumerate(keys) if alloc[i] < limit(k)]
        if not cand:
            break
        sub = largest_remainder(overflow, [weights[keys[i]] for i in cand], floor=0)
        for j, i in enumerate(cand):
            room = limit(keys[i]) - alloc[i]
            take = min(sub[j], room)
            alloc[i] += take
            overflow -= take
        # 兜底：逐个补
        for i in cand:
            if overflow == 0:
                break
            if alloc[i] < limit(keys[i]):
                alloc[i] += 1
                overflow -= 1

    if sum(alloc) != total:
        raise ValueError(f'分配失败：合计 {sum(alloc)}，期望 {total}')
    return dict(zip(keys, alloc))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(description='按作者级大五标签构建网民 agent')
    ap.add_argument('--event', default='demo', help='事件 ID（默认 demo）')
    ap.add_argument('--seats', type=int, default=None, help='覆盖事件的席位数')
    ap.add_argument('--cap-share', type=float, default=None,
                    help='单一标签的席位上限（默认取事件配置，0.40）')
    ap.add_argument('--allow-fewer', action='store_true',
                    help='作者不足时自动缩减席位')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    from simulation.infra.event import load_event, event_paths
    spec = load_event(args.event)
    paths = event_paths(spec)
    interim, processed = paths.interim_dir, paths.processed_dir
    total_seats = args.seats or spec.seats
    allow_fewer = args.allow_fewer or spec.allow_fewer_seats
    cap_share = (args.cap_share if args.cap_share is not None
                 else float(spec.simulation.get('cap_share', CAP_SHARE)))
    age_bands = spec.demographics.get('age_bands')
    gender_weights = spec.demographics.get('gender_weights')
    event_label = spec.event_id

    labels, by_author = load_data(interim)

    if not labels:
        raise SystemExit('没有作者级标签，请先运行 personas/author_big5_score.py')

    # 只用「有标签 + 有评论」的作者
    labels = {a: lab for a, lab in labels.items() if a in by_author and by_author[a]}
    if not labels:
        raise SystemExit('没有同时具备标签和评论的作者')

    if len(labels) < total_seats:
        if not allow_fewer:
            raise SystemExit(
                f'事件 {event_label}: 有标签作者 {len(labels)} 少于席位 {total_seats}。\n'
                f'请补充语料、调小 --seats，或加 --allow-fewer。'
            )
        print(f'[警告] 有标签作者只有 {len(labels)} 个，席位从 {total_seats} '
              f'缩减为 {len(labels)}')
        total_seats = len(labels)
    if total_seats < 1:
        raise SystemExit('席位数必须 >= 1')

    weights = Counter()
    supply = Counter()
    members = defaultdict(list)
    for a, lab in labels.items():
        n = len(by_author[a])
        weights[lab] += n
        supply[lab] += 1
        members[lab].append(a)

    total_w = sum(weights.values())
    print(f'事件: {event_label}  席位: {total_seats}')
    print(f'有标签作者: {len(labels)}，加权总量（评论条数）: {total_w}\n')

    print('作者级人格分布（按评论条数加权，前 10）:')
    for lab, w in weights.most_common(10):
        print(f'  {lab:5s} {w:5d}  ({100*w/total_w:5.1f}%)  [{supply[lab]} 位作者]')

    cap = max(1, int(round(total_seats * cap_share)))
    alloc = capped_alloc(total_seats, weights, supply, cap)
    print(f'\n席位分配（单标签上限 {cap} 席）:')
    for lab, n in sorted(alloc.items(), key=lambda kv: -kv[1]):
        print(f'  {lab:5s} {n:2d}  (原始加权占比 {100*weights[lab]/total_w:4.1f}%)')

    # 每个标签内按评论数降序挑人
    chosen = []
    for lab, n in alloc.items():
        pool = sorted(members[lab], key=lambda a: (-len(by_author[a]), a))
        chosen.extend(pool[:n])
    if len(chosen) != total_seats:
        raise SystemExit(f'选中 {len(chosen)} 人，期望 {total_seats}')

    # 活跃度档：按评论数百分位
    chosen.sort(key=lambda a: -len(by_author[a]))
    n_high = max(1, round(total_seats * 0.25))
    n_mid = max(1, round(total_seats * 0.40))

    rows = []
    for i, a in enumerate(chosen, 1):
        if i <= n_high:
            activity = 'High'
        elif i <= n_high + n_mid:
            activity = 'Middle'
        else:
            activity = 'Low'

        age, gender = sample_age_gender(i, age_bands, gender_weights)
        lab = labels[a]
        rec = {
            'uid': i,
            'name': a,
            'age': age,
            'gender': gender,
            'big_five': SEP.join(CODE_TO_TRAIT[c] for c in lab),
            'activity': activity,
        }
        validate_user_row(rec)
        rows.append(rec)

    print(f'\n活跃度分布: {dict(Counter(r["activity"] for r in rows))}')
    print(f'选取作者的评论数范围: {len(by_author[chosen[0]])} ~ '
          f'{len(by_author[chosen[-1]])}')

    if args.dry_run:
        print('\n[dry-run] 未写入')
        for r in rows[:5]:
            print('  ', {k: r[k] for k in ('uid', 'name', 'big_five', 'activity')})
        return

    out = processed / 'user.csv'
    processed.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=USER_COLUMNS, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    print(f'\n已写入 {out}')


if __name__ == '__main__':
    main()
