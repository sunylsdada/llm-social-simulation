import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulation.infra.event import event_paths, load_event, phase_cutoffs  # noqa: E402


class EventLoaderTest(unittest.TestCase):
    def test_load_demo_event(self):
        spec = load_event("demo")
        self.assertEqual(spec.event_id, "demo")
        self.assertEqual(spec.total_days, 3)
        self.assertEqual(spec.seats, 4)
        self.assertEqual([p["id"] for p in spec.phases], ["P1", "P2"])
        self.assertEqual(spec.start_date, "2024-01-01")

    def test_phase_cutoffs(self):
        spec = load_event("demo")
        self.assertEqual(
            phase_cutoffs(spec),
            [("P1", "2024-01-02"), ("P2", "2024-01-03")],
        )

    def test_paths_are_inside_event_root(self):
        spec = load_event("demo")
        paths = event_paths(spec)
        self.assertTrue(str(paths.input_comments).startswith(str(spec.root)))
        self.assertTrue(str(paths.post_csv).startswith(str(spec.root)))

    def test_unknown_event_raises(self):
        with self.assertRaises(Exception):
            load_event("does_not_exist")


if __name__ == "__main__":
    unittest.main()
