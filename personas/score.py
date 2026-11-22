"""
步骤 2：逐条评论 LLM 打分  ⚠️ 整个流水线唯一花钱的一步。

输入：events/<id>/interim/comments_clean.csv
输出：events/<id>/interim/comment_scores.jsonl  （追加式缓存 = 输出文件，可中断续跑）

⚠️ 真实侧验证只使用 negative = (anxiety + anger) / 2。
   distrust / rumor / tacitus 只作语料描述，不能当作仿真中 trust / rumor 的测量。

Run a pilot first, then the full set:
    python -m personas.score --event demo --pilot 30
    python -m personas.score --event demo

Use --report to inspect the cache without API calls:
    python -m personas.score --event demo --report
"""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personas.prompts import (build_scoring_system,
                              build_scoring_user, parse_scoring_output)
from personas.schema import (ARCHETYPES, FLOAT_FIELDS, validate_score_record,
                             SchemaError)



# ---------------------------------------------------------------------------
# 解析 + 校验（供 wrapper 回调用；失败必须抛异常以触发重试）
# ---------------------------------------------------------------------------

def parse_and_validate(raw_text: str) -> dict:
    parsed = parse_scoring_output(raw_text)
    if not parsed.get('archetype'):
        raise SchemaError(f'未解析出 ARCHETYPE，原始输出: {raw_text[:200]!r}')
    return validate_score_record(parsed)


# ---------------------------------------------------------------------------
# 读输入
# ---------------------------------------------------------------------------

def load_comments(path: Path, limit=None, offset=0) -> list:
    with open(path, 'r', encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    rows = rows[offset:]
    if limit:
        rows = rows[:limit]
    return rows


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def report(cache_path: Path, clean_path: Path) -> None:
    if not cache_path.exists():
        print(f'缓存不存在: {cache_path}')
        return
    recs = {}
    with open(cache_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    o = json.loads(line)
                    recs[o['comment_id']] = o
                except json.JSONDecodeError:
                    pass

    total = sum(1 for _ in open(clean_path, encoding='utf-8')) - 1
    print(f'已打分 {len(recs)} / {total} 条  ({100*len(recs)/max(total,1):.1f}%)')
    if not recs:
        return

    models = Counter(r.get('model', '?') for r in recs.values())
    print('模型: ' + ', '.join(f'{m}×{n}' for m, n in models.items()))

    print('\narchetype 分布:')
    for code, n in Counter(r['archetype'] for r in recs.values()).most_common():
        print(f'  {code:20s} {ARCHETYPES[code]:16s} {n:5d}  ({100*n/len(recs):5.1f}%)')

    on_topic = [r for r in recs.values() if r.get('on_topic') == 1]
    print(f'\non_topic: {len(on_topic)}/{len(recs)} ({100*len(on_topic)/len(recs):.1f}%)')

    print('\n字段均值（on_topic=1 的评论）:')
    for f in FLOAT_FIELDS + ['tacitus']:
        vals = [r.get(f, 0) for r in on_topic]
        if vals:
            print(f'  {f:12s} mean={sum(vals)/len(vals):.3f}  '
                  f'min={min(vals):.2f}  max={max(vals):.2f}')

    from personas.schema import phase_of
    comments = {r['comment_id']: r for r in _read_clean(clean_path)}
    phase_cnt = Counter()
    for cid, r in recs.items():
        c = comments.get(cid)
        if c:
            phase_cnt[c['phase']] += 1
    print('\n各阶段已打分条数: ' + ', '.join(f'{p}={phase_cnt[p]}' for p in sorted(phase_cnt)))

    print('\n--- 抽样 3 条看 evidence ---')
    for r in list(recs.values())[:3]:
        c = comments.get(r['comment_id'], {})
        print(f"  [{r['archetype']}] {c.get('body','')[:100]}")
        print(f"      -> {r.get('evidence','')}")


def _read_clean(path: Path) -> list:
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------

def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(description='Score comments with an LLM')
    ap.add_argument('--event', default='demo', help='Event id (default: demo)')
    ap.add_argument('--pilot', type=int, default=None,
                    help='只打分前 N 条（试点用）')
    ap.add_argument('--offset', type=int, default=0, help='跳过前 N 条')
    ap.add_argument('--model', default=None,
                    help='模型名。留空则用 config 里的（默认 deepseek-ai/DeepSeek-V4-Flash）；'
                         '换模型时记得加 --reset-cache，避免不同模型的分数混用')
    ap.add_argument('--workers', type=int, default=8, help='并发数')
    ap.add_argument('--rpm', type=int, default=300, help='全局每分钟请求上限')
    ap.add_argument('--report', action='store_true',
                    help='只打印当前缓存统计，不调用 API')
    ap.add_argument('--reset-cache', action='store_true',
                    help='换模型时清空缓存（旧文件会被改名留底，不会真删）')
    args = ap.parse_args()

    from simulation.infra.event import build_config, event_paths, load_event
    spec = load_event(args.event)
    paths = event_paths(spec)
    cfg = build_config(args.event)
    clean_path = paths.comments_clean
    cache_path = paths.scores_cache
    clean_hint = f'python -m personas.clean --event {args.event}'

    if not clean_path.exists():
        sys.exit(f'缺少 {clean_path}，请先运行: {clean_hint}')

    if args.report:
        report(cache_path, clean_path)
        return

    if args.reset_cache and cache_path.exists():
        import time as _time
        aside = cache_path.with_name(cache_path.name + '.superseded-' + _time.strftime('%Y%m%d-%H%M%S'))
        cache_path.rename(aside)
        print(f'已把旧缓存改名留底: {aside.name}\n')

    comments = load_comments(clean_path, limit=args.pilot, offset=args.offset)
    if not comments:
        sys.exit('没有待打分的评论')

    # 延迟 import：只有真正要调 API 时才构造客户端
    from personas.llm_wrapper import ScoringLLM

    model = args.model or cfg['LLM_config']['model_name']
    system_prompt = build_scoring_system(cfg.get('event_context'))
    print(f'模型: {model}')
    if '7B' in model:
        print('  [警告] 7B 模型对英文讽刺与立场判断偏弱，而打分质量直接决定'
              '验证曲线的有效性。正式跑之前建议 --model 换成更强的模型。')
    print(f'本次待处理 {len(comments)} 条，并发 {args.workers}\n')

    client = ScoringLLM(
        cfg, cache_path, parse_and_validate,
        workers=args.workers, rpm=args.rpm, model_name=args.model,
    )

    items = [(c['comment_id'], (c['body'], c.get('post_title', ''))) for c in comments]
    done, failed = client.score_many(
        items, system_prompt,
        user_fn=lambda p: build_scoring_user(p[0], p[1]),
    )

    print(f'\n完成: 成功 {len(done)}，失败 {len(failed)}')
    if failed:
        print('失败样例:')
        for cid, err in list(failed.items())[:5]:
            print(f'  {cid}: {err[:150]}')
        print('（重跑本命令会自动只重试失败的那些）')
    print(f'用量: {client.usage()}')
    print(f'缓存: {cache_path}')


if __name__ == '__main__':
    main()
