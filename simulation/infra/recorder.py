"""
记录器 — 收集模拟过程中的所有数据。
"""

import json
import os


class Recorder:
    def __init__(self, config):
        self.global_config = config
        self.config = config
        self.meta_info = {}
        self.daily_snapshots = []      # 每天快照
        self.government_decisions = []  # 政府决策记录
        self.agent_actions = []         # Agent 行动轨迹

    def add_record(self, record_type, record):
        if record_type == 'META':
            self.meta_info.update(record)
        elif record_type == 'DAILY_SNAPSHOT':
            self.daily_snapshots.append(record)
        elif record_type == 'GOVERNMENT':
            self.government_decisions.append(record)
        elif record_type == 'AGENT_ACTION':
            self.agent_actions.append(record)
        else:
            raise ValueError(f"Unknown record type: {record_type}")

    def write_output(self):
        """写入完整的模拟输出（JSON 格式）"""
        path = self.global_config['data_path']['record']
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # 生成三条核心指标曲线
        panic_curve = []
        rumor_curve = []
        trust_curve = []
        for s in self.daily_snapshots:
            panic_curve.append({
                'day': s['day'],
                'avg_panic': s['avg_panic'],
            })
            rumor_curve.append({
                'day': s['day'],
                'avg_rumor': s.get('avg_rumor', 0),
            })
            trust_curve.append({
                'day': s['day'],
                'avg_trust': s['avg_trust'],
            })

        # 政府决策轨迹
        decision_trajectory = []
        for d in self.government_decisions:
            decision_trajectory.append({
                'day': d.get('day'),
                'strategy': d.get('selected_strategy'),
                'weights': d.get('weights'),
                'reasoning': d.get('priority_reasoning', '')[:120],
            })

        # 真实侧唯一可用指标是 negative=(anxiety+anger)/2。
        # 仿真没有 anger 状态，只能用 panic 作为负向情绪代理；
        # trust / rumor 是内部状态，不做外部验证。
        negative_proxy_curve = [
            {'day': s['day'], 'avg_negative_proxy': s['avg_panic']}
            for s in self.daily_snapshots
        ]

        output = {
            'meta_info': self.meta_info,
            'validation': {
                'real_side_metric': 'negative = (anxiety + anger) / 2',
                'simulation_proxy': 'panic',
                'not_externally_validated': [
                    'government_trust', 'rumor_susceptibility'],
                'note': ('真实评论只能稳定提取负面情绪指数，无法可靠测量'
                         '政府信任/恐慌指数/谣言易感性；仿真侧只用 panic '
                         '作为负向情绪代理，trust/rumor 不对外部验证。'),
            },
            'indicators': {
                'panic_curve': panic_curve,
                'rumor_curve': rumor_curve,
                'trust_curve': trust_curve,
                'negative_proxy_curve': negative_proxy_curve,
            },
            'government_trajectory': decision_trajectory,
            'daily_snapshots': self.daily_snapshots,
            'agent_action_count': len(self.agent_actions),
            'agent_actions': self.agent_actions,
        }

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n输出已写入: {path}")
        print(f"  恐慌曲线: {[round(p['avg_panic'], 2) for p in panic_curve]}")
        print(f"  谣言曲线: {[round(r['avg_rumor'], 2) for r in rumor_curve]}")
        print(f"  信任曲线: {[round(t['avg_trust'], 2) for t in trust_curve]}")
        print(f"  负向情绪代理（panic）: "
              f"{[round(p['avg_negative_proxy'], 2) for p in negative_proxy_curve]}")
        print(f"  政府决策轨迹: {[d['strategy'] for d in decision_trajectory]}")
