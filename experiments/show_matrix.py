"""打印当前效用矩阵"""
import argparse
import sys
from pathlib import Path

# 修复 Windows 终端 GBK 编码无法打印中文的问题
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 支持直接运行：python experiments/show_matrix.py
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ap = argparse.ArgumentParser(description='打印效用矩阵')
ap.add_argument('--event', default='demo', help='事件 ID（默认 demo）')
args = ap.parse_args()

from simulation.infra.event import build_config
cfg = build_config(args.event)

m = cfg['strategy_config']['utility_matrix']

strategies = {
    'S1': '沉默观望 — 不主动介入',
    'S2': '压制封堵 — 删帖限流控制传播',
    'S3': '坦诚回应 — 主动公开信息回应关切',
    'S4': '转移议程 — 引导公众关注新话题',
    'S5': '组合应对 — 综合多种手段平衡诉求',
}

print()
print("策略效用矩阵")
print("=" * 80)
header = "%-12s %10s %10s %10s %12s" % ('策略', '防恐慌', '遏谣言', '建信任', '总效用(等权)')
print(header)
print("-" * 80)

for s in ['S1', 'S2', 'S3', 'S4', 'S5']:
    u = m[s]
    total = sum(u) / 3
    row = "%-12s %+10.2f %+10.2f %+10.2f %+12.2f" % (s, u[0], u[1], u[2], total)
    print(row)

print("-" * 80)
print()
print("策略说明:")
for s, desc in strategies.items():
    print("  %s %s" % (s, desc))
print()
print("效用解释: 正值 = 有益于该目标, 负值 = 有害于该目标, 0 = 无影响")

if all(all(abs(x) < 1e-12 for x in m[s]) for s in m):
    print()
    print("⚠️ 当前效用矩阵全为 0：尚未标定。")
    print("   所有策略效用相同，会选到第一个 key（S1）；这只是占位，不是研究结论。")
