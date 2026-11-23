import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.calibrate_utility import compute_matrix  # noqa: E402


def state(panic, trust, rumor):
    return {"panic": panic, "trust": trust, "rumor": rumor}


class CalibrationTest(unittest.TestCase):
    def test_matrix_signs_and_deltas(self):
        results = {
            42: {
                "S1": {"final": state(0.50, 0.40, 0.30), "trajectory": state(0.50, 0.40, 0.30)},
                "S2": {"final": state(0.60, 0.35, 0.35), "trajectory": state(0.60, 0.35, 0.35)},
                "S3": {"final": state(0.40, 0.50, 0.20), "trajectory": state(0.40, 0.50, 0.20)},
                "S4": {"final": state(0.55, 0.38, 0.32), "trajectory": state(0.55, 0.38, 0.32)},
                "S5": {"final": state(0.45, 0.45, 0.25), "trajectory": state(0.45, 0.45, 0.25)},
            }
        }
        matrix, deltas, aggregate = compute_matrix(results, metric="final")

        self.assertEqual(matrix["S1"], [0.0, 0.0, 0.0])
        self.assertEqual(matrix["S2"], [-0.1, -0.05, -0.05])
        self.assertEqual(matrix["S3"], [0.1, 0.1, 0.1])
        self.assertEqual(matrix["S4"], [-0.05, -0.02, -0.02])
        self.assertEqual(matrix["S5"], [0.05, 0.05, 0.05])
        self.assertAlmostEqual(deltas[42]["S2"]["panic"], 0.1, places=6)
        self.assertAlmostEqual(aggregate["S3"]["trust"], 0.1, places=6)

    def test_multiple_seeds_are_averaged(self):
        results = {
            42: {
                "S1": {"final": state(0.50, 0.40, 0.30)},
                "S3": {"final": state(0.40, 0.50, 0.20)},
            },
            43: {
                "S1": {"final": state(0.60, 0.40, 0.30)},
                "S3": {"final": state(0.40, 0.50, 0.20)},
            },
        }
        matrix, _, aggregate = compute_matrix(results, metric="final")
        self.assertAlmostEqual(aggregate["S3"]["panic"], -0.15, places=4)
        self.assertAlmostEqual(matrix["S3"][0], 0.15, places=4)

    def test_missing_s1_raises(self):
        results = {42: {"S2": {"final": state(0.5, 0.4, 0.3)}}}
        with self.assertRaises(ValueError):
            compute_matrix(results, metric="final")


if __name__ == "__main__":
    unittest.main()
