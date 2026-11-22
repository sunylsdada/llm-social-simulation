"""
Generate first-day panic/trust/rumor from agent profiles with an LLM.

输出
----
    events/<id>/interim/initial_states_v2.jsonl  追加式缓存（可按 uid:name 断点续跑）
    events/<id>/processed/initial_states.csv     最终结果，供仿真加载

主路径
------
1. 读取事件包 processed/user.csv 中的画像；
2. 每个 agent 一次请求，把年龄、性别、大五人格、活跃度交给模型；
3. 模型输出事件第 1 天的 PANIC / TRUST / RUMOR 三个 0–1 分数和一句理由；
4. 结果写入缓存与 CSV；
5. 仿真构造 Agent 时读取这三个值，再加一个很小的随机扰动。

为什么不再用大五加性模型
------------------------
加性模型的 BASE 和效应权重都是研究者设定，且 BASE 是按旧版随机初始化的均值
反推的，不是数据估计。直接让模型根据画像生成三个值更直观，也去掉了
“BASE 怎么来的”这一层解释负担。

⚠️ Calls the LLM. Run --pilot first to validate the output format.

用法:
    python personas/generate_initial_states.py --pilot 3
    python personas/generate_initial_states.py
    python personas/generate_initial_states.py --report
    python -m personas.generate_initial_states --event demo
    python personas/generate_initial_states.py --reset-cache   # 换模型时用
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.infra.utils import extract_score

# v2：2026-10-19 移除了画像中的 moral_foundation，旧缓存不能再复用。


SYSTEM_PROMPT = """你是一名社会心理学研究者，擅长根据人口学特征与人格特质估计个体在公共危机初期的心理反应。

请根据给定的网民画像，估计他在事件刚发生时的三项心理状态。所有分数范围都是 0 到 1：

- PANIC：恐慌/焦虑程度。0 = 完全平静，1 = 极度恐慌。
- TRUST：对负责处置此事的官方机构的信任程度。0 = 完全不信任，1 = 完全信任。
- RUMOR：对未经证实信息的轻信与转发倾向。0 = 有辨别力，1 = 非常容易轻信。

请利用画像中的差异做出区分，不要给所有人相同的分数。
姓名只是标识，不要根据姓名推断种族、性别或年龄。

输出格式（严格按此格式，每个字段一行）：
PANIC: <0到1之间的数字>
TRUST: <0到1之间的数字>
RUMOR: <0到1之间的数字>
REASON: <一句话说明判断依据>
"""


def build_user_prompt(profile, ctx=None):
    ctx = ctx or {}
    background = ctx.get('event_description') or ctx.get('event_name') or 'current public event'
    day1 = ctx.get('start_date') or 'day 1'
    official = ctx.get('official_body') or 'the responsible public authority'
    event_line = (
        f"Event background: {background}\n"
        f"Estimate this user's initial psychological state on day 1 ({day1}), "
        f"including trust in {official}.\n"
    )

    return f"""{event_line}
[User profile]
Name: {profile.get('name', '')}
Age: {profile.get('age', '')}
Gender: {profile.get('gender', '')}
Big Five: {profile.get('big_five', '')}
Activity: {profile.get('activity', '')}
"""


def _first_score(raw, keys):
    for k in keys:
        v = extract_score(raw, k)
        if v is not None:
            return v
    return None


def parse_initial(raw):
    """解析模型输出；任何一项缺失都抛异常，让 ScoringLLM 重试。"""
    panic = _first_score(raw, ['PANIC', 'PANIC_SCORE'])
    trust = _first_score(raw, ['TRUST', 'TRUST_SCORE'])
    rumor = _first_score(raw, ['RUMOR', 'RUMOR_SCORE'])
    if panic is None or trust is None or rumor is None:
        raise ValueError(f'初始状态解析失败: {raw[:200]!r}')

    m = re.search(r'REASON:\s*(.*)', raw)
    reason = m.group(1).strip()[:200] if m else ''
    return {
        'init_panic': round(panic, 4),
        'init_trust': round(trust, 4),
        'init_rumor': round(rumor, 4),
        'reason': reason,
    }


def load_profiles(path):
    path = Path(path)
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def cache_key(profile):
    # 用 uid:name 作为 key：如果 user.csv 换人，旧缓存不会误命中。
    return f"{profile['uid']}:{profile.get('name', '')}"


def read_cache(path: Path):
    cache = {}
    if not path.exists():
        return cache
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = o.get('comment_id')
            if key:
                cache[key] = o
    return cache


def write_csv(users, cache, out_path: Path):
    fields = ['uid', 'name', 'init_panic', 'init_trust', 'init_rumor',
              'reason', 'model']
    rows = []
    for u in users:
        rec = cache.get(cache_key(u))
        if not rec:
            continue
        rows.append({
            'uid': u['uid'],
            'name': u.get('name', ''),
            'init_panic': rec.get('init_panic'),
            'init_trust': rec.get('init_trust'),
            'init_rumor': rec.get('init_rumor'),
            'reason': rec.get('reason', ''),
            'model': rec.get('model', ''),
        })
    rows.sort(key=lambda r: int(r['uid']))
    with open(out_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    return rows


def report(users, cache):
    import statistics as st
    missing = [u for u in users if cache_key(u) not in cache]
    print(f'已生成: {len(users) - len(missing)} / {len(users)}')
    if missing:
        print('缺少: ' + ', '.join(f"{u['uid']}:{u.get('name', '')}" for u in missing[:20])
              + (' ...' if len(missing) > 20 else ''))
    if not cache:
        return
    recs = list(cache.values())
    models = {}
    for r in recs:
        m = r.get('model', '(未记录)')
        models[m] = models.get(m, 0) + 1
    print('模型分布: ' + ', '.join(f'{m}×{n}' for m, n in models.items()))
    for field, label in [('init_panic', '恐慌'), ('init_trust', '信任'),
                         ('init_rumor', '谣言易感性')]:
        vals = [r[field] for r in recs if field in r]
        if vals:
            print(f'{label}: mean={st.mean(vals):.3f} '
                  f'min={min(vals):.3f} max={max(vals):.3f} n={len(vals)}')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        description='用 LLM 根据画像生成三项初始心理状态')
    ap.add_argument('--event', default='demo', help='Event id (default: demo)')
    ap.add_argument('--pilot', type=int, default=None,
                    help='只生成前 N 个缺失的 agent（试点用）')
    ap.add_argument('--report', action='store_true',
                    help='只打印缓存统计，不调用 API')
    ap.add_argument('--dry-run', action='store_true',
                    help='只打印前几条 prompt，不调用 API')
    ap.add_argument('--model', default=None,
                    help='覆盖 config 里的模型名')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--rpm', type=int, default=300)
    ap.add_argument('--reset-cache', action='store_true',
                    help='换模型时把旧缓存改名留底')
    args = ap.parse_args()

    from simulation.infra.event import build_config, event_paths, load_event
    spec = load_event(args.event)
    paths = event_paths(spec)
    cfg = build_config(args.event)
    users = load_profiles(paths.user_csv)
    cache_path = paths.initial_states_cache
    out_path = paths.initial_states_csv

    if args.reset_cache and cache_path.exists():
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup = cache_path.with_name(f'{cache_path.name}.superseded-{stamp}')
        cache_path.rename(backup)
        print(f'旧缓存已改名: {backup}')

    ctx = cfg.get('event_context')

    if args.report:
        report(users, read_cache(cache_path))
        return

    if args.dry_run:
        n = args.pilot or 3
        for u in users[:n]:
            print('=' * 70)
            print(build_user_prompt(u, ctx))
        return

    from personas.llm_wrapper import ScoringLLM

    client = ScoringLLM(
        cfg, cache_path, parse_initial,
        workers=args.workers, max_retries=5, rpm=args.rpm,
        model_name=args.model,
    )

    items = [(cache_key(u), u) for u in users]
    if args.pilot:
        items = [it for it in items if it[0] not in client._cache][:args.pilot]

    print(f'待生成 {len(items)} 个 agent；缓存已有 {len(client._cache)} 个')
    done, failed = client.score_many(
        items, SYSTEM_PROMPT,
        user_fn=lambda u: build_user_prompt(u, ctx),
        progress_every=10,
    )

    print('\n本次用量:', client.usage())
    if failed:
        print(f'失败 {len(failed)} 个:')
        for k, err in list(failed.items())[:10]:
            print(f'  {k}: {err}')

    cache = read_cache(cache_path)
    rows = write_csv(users, cache, out_path)
    print(f'\n已写入 {out_path}（{len(rows)} 行）')
    report(users, cache)

    if len(rows) < len(users):
        sys.exit(1)


if __name__ == '__main__':
    main()
