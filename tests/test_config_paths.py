import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulation.infra.event import build_config, sanitize_config  # noqa: E402


class ConfigPathsTest(unittest.TestCase):
    def test_demo_paths_are_namespaced(self):
        cfg = build_config("demo", run_id="abc")
        self.assertIn("events", cfg["data_path"]["user"])
        self.assertIn("demo", cfg["data_path"]["user"])
        self.assertIn("events", cfg["data_path"]["record"])
        self.assertIn("abc", cfg["data_path"]["record"])

    def test_run_ids_are_separate(self):
        a = build_config("demo", run_id="a")["data_path"]["record"]
        b = build_config("demo", run_id="b")["data_path"]["record"]
        self.assertNotEqual(a, b)

    def test_sanitize_removes_api_key(self):
        cfg = build_config("demo")
        cfg["LLM_config"]["api_key"] = "sk-secret"
        safe = sanitize_config(cfg)
        self.assertEqual(safe["LLM_config"]["api_key"], "<redacted>")
        self.assertNotIn("sk-secret", str(safe))

if __name__ == "__main__":
    unittest.main()
