"""Base configuration for the simulation framework.

Event packages override the values here through ``simulation.infra.event``.
Every run should be built with ``build_config(event_id, ...)`` rather than by
editing this module.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "outputs"

CONFIG = {
    "LLM_config": {
        # 'local' | 'remote_openai' | 'remote_claude' | 'fake'
        "llm_type": "remote_openai",
        "model_name": os.getenv("LLM_MODEL_NAME", "deepseek-ai/DeepSeek-V4-Flash"),
        "port": 8000,
        "api_key": os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL", "https://api.siliconflow.cn/v1"),
    },

    "simulation_config": {
        "total_days": 3,
        "seed": 42,
        "government_decision_days": [3],
        "agents_per_day_ratio": 1.0,
        "agent_uids": None,
        "max_agents": 4,
        "max_workers": 8,
    },

    "stimulus_config": {
        # replay: load fact + gov_comm; counterfactual: load fact only.
        "mode": "replay",
    },

    "agent_config": {
        "activity_prob": {"High": 1.0, "Middle": 0.7, "Low": 0.4},
        # Heuristic ranges used only when an agent has no generated initial state
        # and no Big Five labels.
        "initial_panic": [0.1, 0.3],
        "initial_trust": [0.3, 0.7],
        "initial_rumor": [0.2, 0.5],
        # Pull each reflected value slightly toward the agent's own baseline.
        "affect_reversion": 0.06,
        "initial_noise": 0.03,
        "max_posts_per_agent": 5,
        "max_comments_per_post": 10,
        "freshness_max_age_days": 2,
        "max_pinned_old_notices": 1,
    },

    "strategy_config": {
        # [panic_prevention, rumor_containment, long_term_trust]
        "utility_matrix": {
            "S1": [0.0, 0.0, 0.0],
            "S2": [0.0, 0.0, 0.0],
            "S3": [0.0, 0.0, 0.0],
            "S4": [0.0, 0.0, 0.0],
            "S5": [0.0, 0.0, 0.0],
        },
        "max_delete_per_day": 5,
        "suppress_keywords": [],
        "negative_emotion_threshold": 0.3,
        "utility_learning_rate": 0.3,
    },

    "government_config": {
        "objectives": {
            "panic_prevention": {
                "name": "Prevent panic",
                "description": "Contain extreme emotion and secondary harms.",
            },
            "rumor_containment": {
                "name": "Contain rumors",
                "description": "Reduce the spread of unverified information.",
            },
            "long_term_trust": {
                "name": "Build trust",
                "description": "Maintain transparent and credible communication.",
            },
        },
    },

    # Filled by simulation.infra.event.build_config().
    "data_path": {},

    "panic_keywords": [
        "恐慌", "可怕", "完了", "活不下去", "吓死", "末日",
        "逃", "抢购", "囤", "疯传", "爆炸", "死亡",
        "恐怖", "崩溃", "绝望", "不敢", "救命", "完蛋",
        "怎么办", "太可怕", "吓人", "灭顶之灾",
    ],

    "negative_emotion_words": [
        "愤怒", "恶心", "悲哀", "绝望", "失望", "恐怖",
        "恐惧", "担心", "害怕", "惨", "崩溃", "渣",
        "死", "垃圾", "操", "骂", "恨", "滚", "屁",
    ],
}
