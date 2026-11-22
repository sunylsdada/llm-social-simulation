"""强制使用单一策略的 Simulator 变体。

``strategy_effect_pilot.py`` 用它从同一初始状态比较 S1–S5，
不调用政府决策模型。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from simulation.core.environment import Simulator
from simulation.infra.utils import is_agent_active_today


class ForcedStrategySimulator(Simulator):
    """运行正常仿真循环，但每天都强制使用 ``force_strategy``。"""

    def __init__(self, config, llm, recorder, checkpoint, force_strategy, logger=None):
        super().__init__(config, llm, recorder, checkpoint, logger=logger)
        self.force_strategy = force_strategy

    def run(self):
        for day in range(1, self.total_days + 1):
            self.logger.phase(
                f"第 {day}/{self.total_days} 天  强制策略={self.force_strategy}")

            if day in self.decision_days:
                self.current_strategy = self.force_strategy
                self.logger.log(f"强制策略: {self.force_strategy}")
                self.social_media.execute_decision_day_action(
                    day, self.force_strategy, llm=self.llm)
                self.recorder.add_record("GOVERNMENT", {
                    "day": day,
                    "selected_strategy": self.force_strategy,
                    "weights": {"forced": True},
                    "situation_assessment": f"强制策略 {self.force_strategy}",
                })

            activity_prob = self.global_config["agent_config"]["activity_prob"]
            active_agents = [
                a for a in self.agents
                if is_agent_active_today(a.activity, activity_prob)
            ]

            if active_agents:
                results = []
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {}
                    for agent in active_agents:
                        info_flow = self.social_media.get_info_flow(day, agent)
                        future = executor.submit(agent.take_action, info_flow, day)
                        futures[future] = agent

                    for future in as_completed(futures):
                        agent = futures[future]
                        try:
                            action_type, content_info, action_record = future.result()
                        except Exception as e:  # noqa: BLE001
                            self.logger.log(f"Agent{agent.aid} 出错: {e}", "ERROR")
                            action_type = "silent"
                            content_info = {"type": "silent"}
                            action_record = {}
                        results.append((agent, action_type, content_info,
                                        action_record))

                results.sort(key=lambda x: x[0].aid)
                for agent, action_type, content_info, action_record in results:
                    self._apply_agent_action(agent, action_type, content_info)
                    self.logger.agent(day, agent.aid, action_type,
                                      agent.panic, agent.government_trust,
                                      agent.rumor_susceptibility)
                    self.recorder.add_record("AGENT_ACTION", {
                        "day": day,
                        "aid": agent.aid,
                        "name": agent.name,
                        "panic": agent.panic,
                        "government_trust": agent.government_trust,
                        "rumor_susceptibility": agent.rumor_susceptibility,
                        "action_type": action_type,
                        "content_info": content_info,
                        "action_record": action_record,
                    })

            self.logger.log(
                f"第{day}天 活跃={len(active_agents)}/{len(self.agents)}", "STAT")
            self.social_media.execute_daily_intervention(day)
            snapshot = self.social_media.get_snapshot(day, self.agents)
            self.recorder.add_record("DAILY_SNAPSHOT", snapshot)
            self.logger.log(
                f'Day{day} avg_panic={snapshot["avg_panic"]:.3f} '
                f'avg_trust={snapshot["avg_trust"]:.3f} '
                f'avg_rumor={snapshot["avg_rumor"]:.3f}', "STAT")

        final_snapshot = self.social_media.get_snapshot(
            self.total_days, self.agents)
        self.recorder.add_record("META", {"final_snapshot": final_snapshot})
        self.logger.phase("强制策略运行完成")
