import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulation.infra.event import build_config, event_context, load_event  # noqa: E402
from personas.prompts import (  # noqa: E402
    build_author_big5_system, build_big5_system, build_scoring_system,
)


class PromptCompatTest(unittest.TestCase):
    def test_demo_prompt_is_generic(self):
        ctx = build_config("demo")["event_context"]
        prompt = build_scoring_system(ctx)
        self.assertIn("合成示例事件", prompt)
        self.assertIn("微博", prompt)
        self.assertIn("中文", prompt)
        self.assertNotIn("Reddit", prompt)
        self.assertNotIn("PARTISAN", prompt)

    def test_partisan_is_optional(self):
        spec = load_event("demo")
        ctx = event_context(spec)
        ctx["scoring_fields"] = {"partisan": True}
        prompt = build_scoring_system(ctx)
        self.assertIn("PARTISAN:", prompt)

    def test_big5_prompts_are_generic(self):
        ctx = build_config("demo")["event_context"]
        self.assertIn("合成示例事件", build_big5_system(ctx))
        self.assertIn("合成示例事件", build_author_big5_system(ctx))


if __name__ == "__main__":
    unittest.main()
