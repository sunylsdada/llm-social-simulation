"""仿真框架的基础配置。

事件包通过 ``simulation.infra.event`` 覆盖这里的默认值。
每次运行都应调用 ``build_config(event_id, ...)`` 生成独立配置，
不要直接修改本模块。
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
        # replay：加载事实 + 历史官方表态；counterfactual：只加载事实。
        "mode": "replay",
    },

    "agent_config": {
        "activity_prob": {"High": 1.0, "Middle": 0.7, "Low": 0.4},
        # 仅当 agent 既没有生成的初始状态、也没有大五标签时使用的启发式范围。
        "initial_panic": [0.1, 0.3],
        "initial_trust": [0.3, 0.7],
        "initial_rumor": [0.2, 0.5],
        # 每次反思后的值向 agent 自身基线做小幅回归。
        "affect_reversion": 0.06,
        "initial_noise": 0.03,
        "max_posts_per_agent": 5,
        "max_comments_per_post": 10,
        "freshness_max_age_days": 2,
        "max_pinned_old_notices": 1,
    },

    "strategy_config": {
        # [防恐慌, 遏谣言, 建信任]
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
                "name": "防止社会恐慌扩散",
                "description": "遏制极端情绪蔓延，减少次生伤害。",
            },
            "rumor_containment": {
                "name": "遏制谣言传播",
                "description": "减少未经证实信息的传播。",
            },
            "long_term_trust": {
                "name": "建设长期信任关系",
                "description": "通过公开、可信的沟通建立持久互信。",
            },
        },
    },

    # 由 simulation.infra.event.build_config() 注入。
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
