import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personas.prompts import parse_scoring_output  # noqa: E402
from personas.schema import validate_score_record  # noqa: E402


class ScoringSchemaTest(unittest.TestCase):
    def test_partisan_is_optional(self):
        raw = """ON_TOPIC: 1
ARCHETYPE: technical_inquiry
ANXIETY: 0.3
ANGER: 0.2
DISTRUST: 0.4
TACITUS: 0
RUMOR: 0.1
EVIDENCE: 测试
"""
        parsed = parse_scoring_output(raw)
        rec = validate_score_record(parsed)
        self.assertEqual(rec["partisan_us"], 0.0)
        self.assertEqual(rec["anxiety"], 0.3)

    def test_partisan_alias(self):
        raw = """ON_TOPIC: 1
ARCHETYPE: technical_inquiry
ANXIETY: 0.3
ANGER: 0.2
DISTRUST: 0.4
TACITUS: 0
PARTISAN: 0.6
RUMOR: 0.1
EVIDENCE: 测试
"""
        parsed = parse_scoring_output(raw)
        rec = validate_score_record(parsed)
        self.assertEqual(rec["partisan_us"], 0.6)


if __name__ == "__main__":
    unittest.main()
