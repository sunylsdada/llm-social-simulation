"""Simulation entry point.

Usage:
    python -m simulation.run                      # runs events/demo
    python -m simulation.run --event my_event
    python -m simulation.run --event my_event --dry-run
    python -m simulation.run --event my_event --run-id pilot --days 2
"""

import argparse
import copy
import sys
import time
from pathlib import Path

# 支持两种运行方式：
#   python -m simulation.run          （从项目根）
#   python simulation/run.py          （直接跑／IDE 点 Run）
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.infra.llm import create_llm
from simulation.infra.recorder import Recorder
from simulation.core.environment import Simulator
from simulation.infra.config import CONFIG
from simulation.infra.event import (
    EventError,
    build_config,
    list_events,
    sanitize_config,
)
from simulation.infra.utils import Checkpoint, update_config
from simulation.infra.logger import Logger


def run(cfg, load_checkpoint=False):
    logger = Logger(log_path=cfg['data_path'].get('log'))
    sc = cfg['simulation_config']
    logger.log(f'Event: {cfg.get("event_id")} / run_id={cfg.get("run_id")}')
    logger.log(f'LLM 类型: {cfg["LLM_config"].get("llm_type", "local")}')
    logger.log(f'模型: {cfg["LLM_config"].get("model_name", "unknown")}')
    logger.log(f'模拟天数: {sc["total_days"]}')
    logger.log(f'随机种子: {sc.get("seed")}')
    logger.log(f'刺激源模式: {cfg.get("stimulus_config", {}).get("mode")}')
    logger.log(f'政府决策日: {sc["government_decision_days"]}')

    if not load_checkpoint:
        recorder = Recorder(cfg)
        st = time.time()
        llm = create_llm(cfg, logger=logger)
        checkpoint = Checkpoint(cfg, st)
        simulator = Simulator(cfg, llm, recorder, checkpoint, logger=logger)

        meta = {
            'event_id': cfg.get('event_id'),
            'event_name': cfg.get('event_name'),
            'run_id': cfg.get('run_id'),
            'configuration': sanitize_config(cfg),
        }
        recorder.add_record('META', meta)
        simulator.run()
        ed = time.time()
        recorder.add_record('META', {'simulation_total_time_seconds': ed - st})
        recorder.write_output()
        logger.log(f'总用时: {ed - st:.1f}s')
        llm.print_total_consumption()

    else:
        raise SystemExit(
            'checkpoint 加载功能尚未实现（Checkpoint.load_checkpoint 仍是 stub）。\n'
            '请去掉 -load，或在后续版本实现 checkpoint。'
        )

    logger.close()


def dry_run(cfg):
    """不调用 LLM，只验证路径、数据加载与 prompt 上下文。"""
    from simulation.infra.utils import load_user, load_posts
    from simulation.core.agent import Agent
    from simulation.core import mechanism as mech
    from simulation.core.post_pool import PostPool

    print('\n===== dry-run 配置检查 =====')
    print(f"事件: {cfg.get('event_id')} / run_id={cfg.get('run_id')}")
    print(f"数据: user={cfg['data_path']['user']}")
    print(f"      posts={cfg['data_path']['posts']}")
    print(f"记录: {cfg['data_path']['record']}")
    print(f"日志: {cfg['data_path'].get('log')}")

    users = load_user(cfg)
    posts = load_posts(cfg)
    print(f"user.csv: {len(users)} 行")
    print(f"post.csv: {len(posts)} 行")
    if not users:
        raise SystemExit('user.csv 为空')
    if not posts:
        raise SystemExit('post.csv 为空')

    import random
    random.seed(cfg['simulation_config'].get('seed', 42))
    agents = [Agent(cfg, u, None) for u in users[:cfg['simulation_config'].get('max_agents', 40)]]
    print(f"构造 Agent: {len(agents)} 个")

    pool = PostPool(cfg, None)
    print(f"加载刺激源: {len(pool.posts)} 条，模式={getattr(pool, 'stimulus_mode', '?')}")

    sample = mech.get_reflection_prompt(
        agents[0], '（dry-run）', '（dry-run 信息流）', current_day=1)
    print(f"反思 prompt 长度: {len(sample)} chars")
    ctx = cfg.get('event_context') or {}
    print(f"事件名: {ctx.get('event_name')}")
    print('===========================\n')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description='舆情模拟系统')
    parser.add_argument('--event', default='demo',
                        help='Event id (default: demo)')
    parser.add_argument('--run-id', default=None,
                        help='本次运行 ID（输出目录名）；默认 baseline 或时间戳')
    parser.add_argument('--dry-run', action='store_true',
                        help='只加载配置/数据/prompt，不调用 LLM、不写 record')
    parser.add_argument('--force', action='store_true',
                        help='允许覆盖已存在的 run 目录')
    parser.add_argument('--list-events', action='store_true',
                        help='列出 events/ 下的事件')
    parser.add_argument('-load', '--load', action='store_true',
                        help='（未实现）从 checkpoint 继续')
    parser.add_argument('-seed', '--seed', type=int, default=None,
                        help='覆盖 simulation_config.seed')
    parser.add_argument('-days', '--days', type=int, default=None,
                        help='覆盖 simulation_config.total_days')
    parser.add_argument('-stimulus_mode', '--stimulus-mode', '--stimulus_mode',
                        dest='stimulus_mode', default=None,
                        choices=['replay', 'counterfactual'],
                        help='覆盖 stimulus_config.mode')
    parser.add_argument('-model_name', '--model-name', '--model_name',
                        dest='model_name', default=None,
                        help='覆盖 LLM_config.model_name')
    parser.add_argument('-llm_type', '--llm-type', '--llm_type',
                        dest='llm_type', default=None,
                        help='覆盖 LLM_config.llm_type（如 fake）')
    parser.add_argument('-api_key', '--api-key', '--api_key',
                        dest='api_key', default=None,
                        help='覆盖 LLM_config.api_key（建议用环境变量）')
    parser.add_argument('-base_url', '--base-url', '--base_url',
                        dest='base_url', default=None,
                        help='覆盖 LLM_config.base_url')
    args, extra_args = parser.parse_known_args()

    if args.list_events:
        print('\n'.join(list_events()) or '（没有事件）')
        return

    run_id = args.run_id or 'baseline'
    try:
        cfg = build_config(args.event, run_id=run_id)
    except EventError as e:
        raise SystemExit(str(e))

    # 兼容旧的 -model_name/-api_key/-base_url/-port/-llm_type 等参数
    cfg = update_config(cfg, extra_args)
    if args.seed is not None:
        cfg['simulation_config']['seed'] = args.seed
    if args.days is not None:
        cfg['simulation_config']['total_days'] = args.days
    if args.stimulus_mode is not None:
        cfg.setdefault('stimulus_config', {})['mode'] = args.stimulus_mode
    if args.model_name:
        cfg['LLM_config']['model_name'] = args.model_name
    if args.llm_type:
        cfg['LLM_config']['llm_type'] = args.llm_type
    if args.api_key:
        cfg['LLM_config']['api_key'] = args.api_key
    if args.base_url:
        cfg['LLM_config']['base_url'] = args.base_url

    if not args.dry_run:
        record_path = Path(cfg['data_path']['record'])
        if record_path.exists() and not args.force:
            raise SystemExit(
                f'输出已存在，拒绝覆盖: {record_path}\n'
                f'请换 --run-id，或加 --force。'
            )

    if args.dry_run:
        dry_run(cfg)
        return

    run(cfg, load_checkpoint=args.load)


if __name__ == '__main__':
    main()
