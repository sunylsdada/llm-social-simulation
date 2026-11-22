"""
画像 / 事件流水线编排。

事件模式（推荐，传 --event）
----------------------------
    import -> clean -> score -> author_big5 -> build_agents_author
    -> initial_states -> phase_sentiment -> stimulus -> validate

- `--stage free`：只跑不依赖付费缓存的步骤（import/clean/stimulus/validate，
  以及已有缓存时的 build_agents_author/phase_sentiment）。
- `--stage all`：按顺序跑全部步骤；付费步骤必须加 `--yes-paid`。
- `--only STEP` / `--from STEP`：手工分步。
- 不调用 API 的步骤不会产生费用；`score`/`author_big5`/`initial_states`
  会真实调用 LLM，先 `--pilot` 试点。

用法:
    python -m personas.pipeline --event my_event --stage free
    python -m personas.pipeline --event my_event --only score --pilot 20 --yes-paid
    python -m personas.pipeline --event my_event --stage all --yes-paid
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 事件模式
# ---------------------------------------------------------------------------

# (step, module, description, paid)
EVENT_STEPS = [
    ('import', 'import_event', '导入评论 CSV', False),
    ('clean', 'clean', '清洗评论', False),
    ('score', 'score', '逐条评论打分', True),
    ('author_big5', 'author_big5_score', '作者级大五标注', True),
    ('build_agents_author', 'build_agents_author', '生成 user.csv', False),
    ('initial_states', 'generate_initial_states', '生成初始心理状态', True),
    ('phase_sentiment', 'phase_sentiment', '阶段情感汇总', False),
    ('stimulus', 'prepare_stimulus', '准备刺激源 post.csv', False),
    ('validate', 'validate_event', '事件包校验', False),
]

PAID = {name for name, _, _, paid in EVENT_STEPS if paid}


def _event_state(args):
    from simulation.infra.event import event_paths, load_event
    spec = load_event(args.event)
    paths = event_paths(spec)
    return spec, paths


def _precondition(step: str, spec, paths, force: bool = False) -> tuple[bool, str]:
    raw = paths.input_dir / 'raw_comments.csv'
    if step == 'import':
        if paths.input_comments.exists() and not force:
            return False, f'{paths.input_comments.name} 已存在（加 --force 重新导入）'
        if not raw.exists() and not paths.input_comments.exists():
            return False, f'缺少源文件 {raw}'
    elif step == 'clean':
        if not paths.input_comments.exists():
            return False, '缺少 input/comments.csv，先跑 import'
    elif step in {'score', 'author_big5'}:
        if not paths.comments_clean.exists():
            return False, '缺少 interim/comments_clean.csv，先跑 clean'
    elif step == 'build_agents_author':
        if not paths.author_big5_cache.exists():
            return False, '缺少 interim/author_big5.jsonl，先跑 author_big5'
    elif step == 'initial_states':
        if not paths.user_csv.exists():
            return False, '缺少 processed/user.csv，先跑 build_agents_author'
    elif step == 'phase_sentiment':
        if not paths.scores_cache.exists():
            return False, '缺少 interim/comment_scores.jsonl，先跑 score'
    elif step == 'stimulus':
        if not paths.input_stimuli.exists() and not spec.inputs.get('allow_placeholder_stimuli'):
            return False, '缺少 input/stimuli.csv'
    return True, ''


def _build_command(args, step: str, module: str) -> list[str]:
    cmd = [sys.executable, '-m', f'personas.{module}', '--event', args.event]
    if step == 'import' and args.force:
        cmd.append('--force')
    if step in {'score', 'author_big5', 'initial_states'} and args.pilot:
        cmd += ['--pilot', str(args.pilot)]
    if step == 'build_agents_author':
        if args.seats:
            cmd += ['--seats', str(args.seats)]
        if args.cap_share is not None:
            cmd += ['--cap-share', str(args.cap_share)]
        if args.allow_fewer:
            cmd.append('--allow-fewer')
        if args.dry_run:
            cmd.append('--dry-run')
    if step == 'stimulus' and args.allow_placeholder_stimuli:
        cmd.append('--allow-placeholder')
    if step == 'validate' and args.strict:
        cmd.append('--strict')
    return cmd


def run_event_pipeline(args) -> None:
    spec, paths = _event_state(args)
    names = [s for s, _, _, _ in EVENT_STEPS]

    if args.from_step and args.only:
        sys.exit('--from 与 --only 只能二选一')
    if args.only:
        if args.only not in names:
            sys.exit(f'未知步骤 {args.only}，可选: {names}')
        todo = [s for s in EVENT_STEPS if s[0] == args.only]
    elif args.from_step:
        if args.from_step not in names:
            sys.exit(f'未知步骤 {args.from_step}，可选: {names}')
        todo = EVENT_STEPS[names.index(args.from_step):]
    else:
        todo = list(EVENT_STEPS)

    stage = args.stage
    if stage == 'free':
        todo = [s for s in todo if not s[3]]
    elif stage == 'paid':
        todo = [s for s in todo if s[3]]
    elif stage != 'all':
        sys.exit(f'未知 --stage: {stage}')

    if args.only and args.only in PAID and not args.yes_paid:
        cmd = _build_command(args, *next((s[0], s[1]) for s in EVENT_STEPS if s[0] == args.only))
        print('付费步骤需要显式确认。请运行：')
        print('  ' + ' '.join(cmd) + ' --yes-paid')
        raise SystemExit(2)

    print(f'事件: {spec.event_id} ({spec.name})')
    print(f'阶段: {stage}，将检查/执行 {len(todo)} 步\n', flush=True)

    failed = []
    for step, module, desc, paid in todo:
        ok, why = _precondition(step, spec, paths, force=args.force)
        if not ok:
            # 自动阶段里跳过缺依赖的步骤；显式 --only 则视为失败。
            if args.only:
                print(f'[失败] {step}: {why}')
                failed.append((step, why))
            else:
                print(f'[跳过] {step} —— {why}')
            continue

        cmd = _build_command(args, step, module)
        paid_flag = '（付费）' if paid else ''
        print('=' * 70)
        print(f'>>> {step}  {desc} {paid_flag}')
        print('   ' + ' '.join(cmd))
        print('=' * 70)

        if paid and not args.yes_paid:
            print('  [跳过] 付费步骤需要 --yes-paid')
            continue
        if args.dry_run:
            print('  [dry-run] 未执行')
            continue

        sys.stdout.flush()
        t0 = time.time()
        r = subprocess.run(cmd)
        dt = time.time() - t0
        if r.returncode != 0:
            print(f'[失败] {step} 退出码 {r.returncode}')
            failed.append((step, f'rc={r.returncode}'))
        else:
            print(f'[完成] {step}  ({dt:.1f}s)\n')

    print('=' * 70)
    if failed:
        print('有步骤未成功完成:')
        for s, why in failed:
            print(f'  - {s}: {why}')
        sys.exit(1)
    print('流水线结束。')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(description='画像 / 事件流水线')
    ap.add_argument('--event', default='demo', help='Event id (default: demo)')
    ap.add_argument('--stage', choices=['free', 'paid', 'all'], default='free',
                    help='事件模式：free（默认）/paid/all')
    ap.add_argument('--yes-paid', action='store_true',
                    help='确认执行付费 LLM 步骤')
    ap.add_argument('--pilot', type=int, default=None,
                    help='付费步骤只处理前 N 条/人')
    ap.add_argument('--from', dest='from_step', default=None,
                    help='从某一步开始')
    ap.add_argument('--only', default=None, help='只跑某一步')
    ap.add_argument('--seats', type=int, default=None,
                    help='席位数（事件模式的 build_agents_author / 旧 allocate）')
    ap.add_argument('--cap-share', type=float, default=None, dest='cap_share',
                    help='单一标签的席位上限（默认 0.40）')
    ap.add_argument('--allow-fewer', action='store_true',
                    help='作者不足席位时自动缩减')
    ap.add_argument('--force', action='store_true',
                    help='事件模式：允许重新导入覆盖 comments.csv')
    ap.add_argument('--strict', action='store_true',
                    help='事件模式：validate 把 warning 当 error')
    ap.add_argument('--allow-placeholder-stimuli', action='store_true',
                    help='事件模式：允许占位刺激源（仅链路冒烟）')
    ap.add_argument('--dry-run', action='store_true',
                    help='只打印命令/分配结果，不执行')
    args = ap.parse_args()

    run_event_pipeline(args)


if __name__ == '__main__':
    main()
