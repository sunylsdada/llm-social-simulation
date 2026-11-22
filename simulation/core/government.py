"""
政府决策模块 — 舆情分析师 + 政府 RDEU 决策（含记忆环比）。
"""

import copy
import re
import json


class AnalystAgent:
    """
    舆情分析师 Agent。
    仅在政府决策日调用，生成结构化舆情简报。
    """

    def __init__(self, config, llm):
        self.global_config = config
        self.config = config
        self.llm = llm

    def generate_report(self, snapshot):
        """生成舆情简报"""
        # ---- 定量部分（纯计算） ----
        quantitative = {
            'avg_panic': round(snapshot['avg_panic'], 3),
            'avg_trust': round(snapshot['avg_trust'], 3),
            'avg_rumor': round(snapshot.get('avg_rumor', 0), 3),
            'active_posts': snapshot['active_posts'],
            'total_comments': snapshot['total_comments'],
            'total_views': snapshot['total_views'],
            'total_likes': snapshot['total_likes'],
        }

        # ---- 定性部分 ----
        all_comments = snapshot.get('all_comments', [])
        import random
        sample_count = min(15, len(all_comments))
        random_sample = random.sample(all_comments, sample_count) if sample_count > 0 else []
        top5 = all_comments[:5] if len(all_comments) >= 5 else all_comments

        seen = set()
        sample_for_llm = []
        for c in top5:
            if c['cid'] not in seen:
                sample_for_llm.append(c)
                seen.add(c['cid'])
        for c in random_sample:
            if c['cid'] not in seen:
                sample_for_llm.append(c)
                seen.add(c['cid'])

        qualitative = self._qualitative_analysis(sample_for_llm, quantitative)

        return {
            'day': snapshot['day'],
            'quantitative': quantitative,
            'qualitative': qualitative,
        }

    def _qualitative_analysis(self, sample_comments, quantitative):
        if not sample_comments:
            return {
                'core_topics': ['信息不足'],
                'mainstream_emotion': '无法判断',
                'derivative_risks': '未知',
            }

        comment_texts = []
        for c in sample_comments:
            label = f"[cid={c['cid']}, 点赞={c['like_num']}]"
            comment_texts.append(f"{label} {c['text'][:100]}")
        comments_str = '\n'.join(comment_texts)

        prompt = f"""你是一名舆情分析师。以下是当前社交媒体上的代表性评论样本。请基于这些评论，撰写一份简要的舆情分析报告。

[量化数据]
- 平均恐慌指数：{quantitative['avg_panic']:.2f}（0=完全平静，1=极度恐慌）
- 平均政府信任度：{quantitative['avg_trust']:.2f}（0=完全不信任，1=完全信任）
- 平均谣言易感性：{quantitative['avg_rumor']:.2f}（0=有辨别力，1=容易轻信不实信息）
- 活跃帖子数：{quantitative['active_posts']}
- 总评论数：{quantitative['total_comments']}
- 总浏览量：{quantitative['total_views']}

[代表性评论样本]
{comments_str}

请分析以下三个维度，用中文输出：

1. 核心议题（2-3个，概括公众讨论的焦点）
2. 主流情绪（公众情绪的总体态势和主要倾向）
3. 衍生风险（可能引发更大危机的新信号，如线下行动倾向、谣言传播、对政府的尖锐质疑等）

输出格式：
CORE_TOPICS: <2-3个核心议题，逗号分隔>
MAINSTREAM_EMOTION: <主流情绪描述，1-2句话>
DERIVATIVE_RISKS: <衍生风险分析，1-2句话>
"""
        result = self.llm.fast_run(prompt)

        topics_match = re.search(r'CORE_TOPICS:\s*(.*)', result)
        emotion_match = re.search(r'MAINSTREAM_EMOTION:\s*([\s\S]*?)(?=DERIVATIVE_RISKS:|$)', result)
        risks_match = re.search(r'DERIVATIVE_RISKS:\s*([\s\S]*)', result)

        return {
            'core_topics': topics_match.group(1).strip() if topics_match else '解析失败',
            'mainstream_emotion': emotion_match.group(1).strip() if emotion_match else '解析失败',
            'derivative_risks': risks_match.group(1).strip() if risks_match else '解析失败',
            'sample_comment_count': len(sample_comments),
        }


class GovernmentAgent:
    """
    政府 Agent — RDEU 决策（含记忆环比）。

    五步：
    ① 态势回顾：对比上轮策略的预测效果 vs 实际效果
    ② 态势评估 + 权重分配：LLM 基于历史和当前简报决定权重
    ③ 策略匹配：加权查效用矩阵
    ④ 记录到记忆链
    """

    def __init__(self, config, llm):
        self.global_config = config
        self.config = config
        self.llm = llm

        self.strategy_config = config['strategy_config']
        # 深拷贝：在线学习会原地修改矩阵。若同一进程里多次构造 Simulator
        # （例如多策略/多种子实验），共享同一个 dict 会让上一轮的学习结果
        # 污染下一轮的起点。
        self.utility_matrix = copy.deepcopy(self.strategy_config['utility_matrix'])
        self.objectives = config['government_config']['objectives']
        self.learning_rate = self.strategy_config.get('utility_learning_rate', 0.3)
        self.memory = []  # 决策历史

    # ================================================================
    # 主入口
    # ================================================================

    def make_decision(self, analyst_report, day, previous_snapshot=None):
        """
        政府 RDEU 决策。

        Args:
            analyst_report: 当前舆情简报
            day: 当前天
            previous_snapshot: 上次决策日的快照（用于计算实际效果），首次为 None

        Returns:
            dict: 含 situation_assessment, weights, selected_strategy 等
        """
        current_snapshot = {
            'avg_panic': analyst_report['quantitative']['avg_panic'],
            'avg_rumor': analyst_report['quantitative'].get('avg_rumor', 0),
            'avg_trust': analyst_report['quantitative']['avg_trust'],
        }

        # 第一步：态势回顾 — 对比上次策略的预测 vs 实际
        history_section = self._build_history_section(previous_snapshot, current_snapshot)

        # 第二、三步：LLM 态势评估 + 权重分配
        prompt = self._build_decision_prompt(analyst_report, day, history_section)
        result = self.llm.fast_run(prompt)
        decision = self._parse_decision(result)

        # 第四步：效用矩阵选策略
        weights = decision.get('weights', {
            'panic_prevention': 0.33,
            'rumor_containment': 0.33,
            'long_term_trust': 0.34,
        })
        best_strategy = self._select_strategy(weights)
        predicted_effect = self._compute_predicted_effect(best_strategy)

        decision['selected_strategy'] = best_strategy
        decision['day'] = day
        decision['weights'] = weights
        decision['predicted_effect'] = predicted_effect

        # 第五步：记录到记忆链
        self.memory.append({
            'day': day,
            'strategy': best_strategy,
            'weights': weights,
            'predicted_effect': predicted_effect,
            'snapshot_at_decision': current_snapshot,
        })

        return decision

    # ================================================================
    # 记忆环比
    # ================================================================

    def _build_history_section(self, previous_snapshot, current_snapshot):
        """构建决策历史段落"""
        if not self.memory:
            return "（这是首次决策，没有历史记录可供参考。）"

        last = self.memory[-1]
        last_strategy = last['strategy']
        last_weights = last['weights']
        predicted = last['predicted_effect']
        prev_snap = last.get('snapshot_at_decision', {})

        # 计算实际效果
        observed = {}
        if previous_snapshot:
            observed['delta_panic'] = round(current_snapshot['avg_panic'] - previous_snapshot['avg_panic'], 3)
            observed['delta_rumor'] = round(current_snapshot.get('avg_rumor', 0) - previous_snapshot.get('avg_rumor', 0), 3)
            observed['delta_trust'] = round(current_snapshot['avg_trust'] - previous_snapshot['avg_trust'], 3)
        else:
            observed = {'delta_panic': '?', 'delta_rumor': '?', 'delta_trust': '?'}

        # 构建对比文本
        lines = []
        lines.append(f"## 上次决策回顾")
        lines.append(f"- 你在第 {last['day']} 天选择了 **{last_strategy}**")
        lines.append(f"- 当时分配的权重：防恐慌={last_weights.get('panic_prevention',0):.2f}, "
                     f"遏谣言={last_weights.get('rumor_containment',0):.2f}, "
                     f"建信任={last_weights.get('long_term_trust',0):.2f}")

        lines.append(f"\n### 你对 {last_strategy} 的预期效果（来自效用矩阵）：")
        lines.append(f"- 恐慌变化: {predicted['panic']:+.2f}")
        lines.append(f"- 谣言易感变化: {predicted['rumor']:+.2f}")
        lines.append(f"- 信任变化: {predicted['trust']:+.2f}")

        lines.append(f"\n### {last_strategy} 至今的实际效果：")
        lines.append(f"- 恐慌实际变化: {observed['delta_panic']:+.2f}" if isinstance(observed['delta_panic'], float) else f"- 恐慌实际变化: {observed['delta_panic']}")
        lines.append(f"- 谣言易感实际变化: {observed['delta_rumor']:+.2f}" if isinstance(observed['delta_rumor'], float) else f"- 谣言易感实际变化: {observed['delta_rumor']}")
        lines.append(f"- 信任实际变化: {observed['delta_trust']:+.2f}" if isinstance(observed['delta_trust'], float) else f"- 信任实际变化: {observed['delta_trust']}")

        # 判断策略是否奏效
        if isinstance(observed['delta_panic'], float) and isinstance(predicted['panic'], float):
            # 预期恐慌下降（负值）但实际上升 → 策略未奏效
            if predicted['panic'] < 0 and observed['delta_panic'] > 0:
                lines.append(f"\n⚠️ 上次策略未能如预期遏制恐慌。恐慌不降反升，需要反思是否继续坚持。")
            elif predicted['panic'] > 0 and observed['delta_panic'] > predicted['panic']:
                lines.append(f"\n⚠️ 上次策略的恐慌恶化程度超出预期。")

        if isinstance(observed['delta_trust'], float) and isinstance(predicted['trust'], float):
            if predicted['trust'] > 0 and observed['delta_trust'] < 0:
                lines.append(f"⚠️ 上次策略预期能提升信任，但实际信任下降。信任建设可能比预期更难。")

        lines.append(f"\n请基于以上经验教训，反思你这次的权重分配是否需要调整。")

        return '\n'.join(lines)

    def _compute_predicted_effect(self, strategy):
        """从效用矩阵获取策略的预期效果"""
        util = self.utility_matrix.get(strategy, [0, 0, 0])
        return {
            'panic': util[0],
            'rumor': util[1],
            'trust': util[2],
        }

    def update_utility(self, strategy, observed_deltas):
        """
        根据实际观测效果，指数滑动更新效用矩阵。

        Args:
            strategy: 'S1'~'S5'
            observed_deltas: {'panic': Δ, 'rumor': Δ, 'trust': Δ}
        """
        if strategy not in self.utility_matrix:
            return

        alpha = self.learning_rate
        if alpha <= 0:
            return

        # 目标方向：
        #   panic / rumor 越低越好 —— Δ 上升 = 恶化 = 负效用；
        #   trust        越高越好 —— Δ 上升 = 改善 = 正效用。
        # 三项统一取负会把“信任上升”学成负效用，与效用矩阵的符号约定相反。
        keys = ['panic', 'rumor', 'trust']
        signs = {'panic': -1.0, 'rumor': -1.0, 'trust': +1.0}
        old = self.utility_matrix[strategy]
        new_vals = []

        for i, key in enumerate(keys):
            observed_utility = signs[key] * observed_deltas[key]
            updated = old[i] + alpha * (observed_utility - old[i])
            new_vals.append(round(updated, 3))

        self.utility_matrix[strategy] = new_vals

    # ================================================================
    # Prompt 构建
    # ================================================================

    def _build_decision_prompt(self, report, day, history_section):
        obj1 = self.objectives['panic_prevention']
        obj2 = self.objectives['rumor_containment']
        obj3 = self.objectives['long_term_trust']

        quant = report.get('quantitative', {})
        qual = report.get('qualitative', {})

        # 趋势提示（如果记忆里有历史数据）
        trend_section = ""
        if len(self.memory) >= 1:
            prev = self.memory[-1].get('snapshot_at_decision', {})
            curr_panic = quant.get('avg_panic', 0)
            prev_panic = prev.get('avg_panic', curr_panic)
            curr_trust = quant.get('avg_trust', 0)
            prev_trust = prev.get('avg_trust', curr_trust)
            curr_rumor = quant.get('avg_rumor', 0)
            prev_rumor = prev.get('avg_rumor', curr_rumor)
            trend_section = f"""
## 指标趋势（相比上次决策日）
- 恐慌：{prev_panic:.2f} → {curr_panic:.2f}（{'上升' if curr_panic > prev_panic else '下降' if curr_panic < prev_panic else '持平'}）
- 谣言易感：{prev_rumor:.2f} → {curr_rumor:.2f}（{'上升' if curr_rumor > prev_rumor else '下降' if curr_rumor < prev_rumor else '持平'}）
- 信任：{prev_trust:.2f} → {curr_trust:.2f}（{'上升' if curr_trust > prev_trust else '下降' if curr_trust < prev_trust else '持平'}）
"""

        prompt = f"""你是政府的舆情应对决策者。今天是事件发生后的第{day}天。
你刚刚收到一份由舆情分析部门提交的简报。你的职责是：客观评估当前舆论态势，确定最需要优先处理的治理目标，并据此选择应对策略。

{history_section}

## 当前舆情简报

### 定量数据
- 平均恐慌指数：{quant.get('avg_panic', 'N/A')}（0=完全平静，1=极度恐慌）
- 平均谣言易感性：{quant.get('avg_rumor', 'N/A')}（0=有辨别力，1=容易轻信不实信息）
- 平均政府信任度：{quant.get('avg_trust', 'N/A')}（0=完全不信任，1=完全信任）
- 活跃帖子数：{quant.get('active_posts', 'N/A')}
- 总评论数：{quant.get('total_comments', 'N/A')}
- 总浏览量：{quant.get('total_views', 'N/A')}
{trend_section}
### 定性分析
- 核心议题：{qual.get('core_topics', 'N/A')}
- 主流情绪：{qual.get('mainstream_emotion', 'N/A')}
- 衍生风险：{qual.get('derivative_risks', 'N/A')}

## 你有三个正当的治理目标，都需要维护，但必须在有限的资源和注意力下决定优先级：

1. **{obj1['name']}**：{obj1['description']}

2. **{obj2['name']}**：{obj2['description']}

3. **{obj3['name']}**：{obj3['description']}

## 可选策略

| 策略 | 操作方式 | 防恐慌 | 遏谣言 | 建信任 |
|------|---------|:--:|:--:|:--:|
| S1 沉默观望 | 不主动介入 | {self.utility_matrix['S1'][0]:+.2f} | {self.utility_matrix['S1'][1]:+.2f} | {self.utility_matrix['S1'][2]:+.2f} |
| S2 压制封堵 | 删帖限流控传 | {self.utility_matrix['S2'][0]:+.2f} | {self.utility_matrix['S2'][1]:+.2f} | {self.utility_matrix['S2'][2]:+.2f} |
| S3 坦诚回应 | 通告+放开讨论 | {self.utility_matrix['S3'][0]:+.2f} | {self.utility_matrix['S3'][1]:+.2f} | {self.utility_matrix['S3'][2]:+.2f} |
| S4 转移议程 | 新话题稀释 | {self.utility_matrix['S4'][0]:+.2f} | {self.utility_matrix['S4'][1]:+.2f} | {self.utility_matrix['S4'][2]:+.2f} |
| S5 组合应对 | 删极端评论+淡化不利、强调正面 | {self.utility_matrix['S5'][0]:+.2f} | {self.utility_matrix['S5'][1]:+.2f} | {self.utility_matrix['S5'][2]:+.2f} |

（表中的数字是各策略对各目标的预期效用，正值=有益于该目标，负值=有害于该目标）

## 输出要求

请以 JSON 格式输出你的决策：

```json
{{
  "situation_assessment": "对当前舆情态势的简要判断（2-3句话）",
  "priority_reasoning": "解释为什么在当前阶段选择这个权重分配。如果有历史经验可以参考，请说明是否吸取了上次的教训（1-2句话）",
  "weights": {{
    "panic_prevention": 0.x,
    "rumor_containment": 0.x,
    "long_term_trust": 0.x
  }}
}}
```

权重必须满足：三个权重之和 = 1.0，每个权重 ∈ [0, 1]。
"""
        return prompt

    # ================================================================
    # 解析 + 策略选择
    # ================================================================

    def _parse_decision(self, raw):
        try:
            json_match = re.search(r'```json\s*([\s\S]*?)\s*```', raw)
            if json_match:
                json_str = json_match.group(1)
            else:
                brace_match = re.search(r'\{[\s\S]*\}', raw)
                json_str = brace_match.group(0) if brace_match else raw
            decision = json.loads(json_str)
        except (json.JSONDecodeError, AttributeError):
            decision = {
                'situation_assessment': '解析失败',
                'priority_reasoning': '解析失败',
                'weights': {
                    'panic_prevention': 0.4,
                    'rumor_containment': 0.3,
                    'long_term_trust': 0.3,
                }
            }

        # 归一化
        w = decision.get('weights', {})
        total = sum([
            w.get('panic_prevention', 0),
            w.get('rumor_containment', 0),
            w.get('long_term_trust', 0),
        ])
        if total > 0 and abs(total - 1.0) > 0.01:
            for k in ['panic_prevention', 'rumor_containment', 'long_term_trust']:
                if k in w:
                    w[k] = w[k] / total

        return decision

    def _select_strategy(self, weights):
        w = [
            weights.get('panic_prevention', 0.33),
            weights.get('rumor_containment', 0.33),
            weights.get('long_term_trust', 0.34),
        ]
        best = None
        best_u = float('-inf')
        for s, u in self.utility_matrix.items():
            total = sum(w[i] * u[i] for i in range(3))
            if total > best_u:
                best_u = total
                best = s
        return best
