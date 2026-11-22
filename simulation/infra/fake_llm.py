"""
确定性假 LLM：用于无网络测试、dry-run 冒烟和 CI。

它只根据 prompt 中的关键词返回合法格式的固定输出，不做任何推理。
不要用它产生论文结果。
"""

from __future__ import annotations

import re

from simulation.infra.llm import BaseLLM


class FakeLLM(BaseLLM):
    def run(self, message_list, temperature=1.0, penalty_score=0.0):
        text = "\n".join(str(m.get("content", "")) for m in message_list)
        result = self._respond(text)
        response = {
            "time_stamp": 0,
            "result": result,
            "input_token": 0,
            "output_token": 0,
            "total_token": 0,
        }
        self.call_time += 1
        self.add_consumption(response)
        return response

    def _respond(self, text: str) -> str:
        if "只输出你选择的数字" in text:
            return "0"

        if "请以第一人称发表一条评论" in text:
            return "测试评论：这是一条确定性的模拟评论。"

        if "COMMENT_ID:" in text and "REPLY:" in text:
            return "COMMENT_ID: 1\nREPLY: 测试回复"

        if "VOTE_TYPE:" in text:
            return "VOTE_TYPE: upvote\nCOMMENT_ID: 1"

        if "SHARE_TYPE:" in text:
            return "SHARE_TYPE: post\nSHARE_ID: 1\nFORWARD_NOTE: 测试转发"

        if "PANIC_SCORE:" in text:
            panic = self._previous_score(text, "恐慌程度") or 0.5
            trust = self._previous_score(text, "对政府信任程度") or 0.5
            rumor = self._previous_score(text, "对不实信息的易感程度") or 0.5
            return (
                "LATEST_POSTS: NONE\n"
                "OUTDATED_POSTS: NONE\n"
                f"PANIC_SCORE: {panic:.2f}\n"
                f"TRUST_SCORE: {trust:.2f}\n"
                f"RUMOR_SCORE: {rumor:.2f}\n"
                "REASONING: fake LLM 保持原状态。"
            )

        if "POLARITY:" in text:
            return "POLARITY: NEU\nEVIDENCE: fake LLM 中性。"

        if "BIG5:" in text:
            return "BIG5: O,C\nEVIDENCE: fake LLM 标注。"

        if '"weights"' in text or "权重必须满足" in text:
            return (
                '{"situation_assessment": "fake LLM 态势判断",'
                ' "priority_reasoning": "fake LLM 权重重分配",'
                ' "weights": {"panic_prevention": 0.34,'
                ' "rumor_containment": 0.33, "long_term_trust": 0.33}}'
            )

        if "TITLE:" in text:
            return "TITLE: 测试新话题\nCONTENT: 这是一条确定性的测试话题内容。"

        if "官方通报" in text or "新闻发言人" in text or "官方声明" in text:
            return "这是一条确定性的测试官方通报。"

        if "舆情分析报告" in text or "CORE_TOPICS:" in text:
            return (
                "CORE_TOPICS: 测试议题\n"
                "MAINSTREAM_EMOTION: 测试情绪\n"
                "DERIVATIVE_RISKS: 测试风险"
            )

        return "OK"

    @staticmethod
    def _previous_score(text: str, label: str) -> float | None:
        m = re.search(rf"{re.escape(label)}[：:]\s*([0-9.]+)", text)
        if not m:
            return None
        try:
            return max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            return None
