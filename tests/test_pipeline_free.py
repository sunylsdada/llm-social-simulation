import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd, env):
    r = subprocess.run(cmd, cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise AssertionError(f"command failed: {cmd}\n{r.stdout[-3000:]}")
    return r.stdout


def copy_clean_demo(events_root: Path) -> Path:
    """复制示例并删除生成物，保证测试可重复。"""
    dst = events_root / "demo"
    shutil.copytree(ROOT / "events" / "demo", dst)
    for rel in ("input/comments.csv", "interim/comments_clean.csv",
                "interim/import_report.json", "processed/user.csv",
                "processed/post.csv"):
        p = dst / rel
        if p.exists():
            p.unlink()
    shutil.rmtree(dst / "outputs", ignore_errors=True)
    return dst


class PipelineFreeTest(unittest.TestCase):
    def test_demo_end_to_end_with_fake_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            events_root = Path(tmp)
            copy_clean_demo(events_root)
            env = os.environ.copy()
            env["YUQING_EVENTS_DIR"] = str(events_root)

            run([sys.executable, "-m", "personas.import_event",
                 "--event", "demo"], env)
            run([sys.executable, "-m", "personas.clean",
                 "--event", "demo"], env)
            run([sys.executable, "-m", "personas.build_agents_author",
                 "--event", "demo"], env)
            run([sys.executable, "-m", "personas.prepare_stimulus",
                 "--event", "demo"], env)
            run([sys.executable, "-m", "personas.validate_event",
                 "--event", "demo", "--strict"], env)
            run([sys.executable, "-m", "simulation.run",
                 "--event", "demo", "--run-id", "test",
                 "--days", "3", "--llm-type", "fake"], env)

            record = events_root / "demo" / "outputs" / "runs" / "test" / "record.json"
            self.assertTrue(record.exists())
            data = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(data["meta_info"]["event_id"], "demo")
            self.assertEqual(len(data["daily_snapshots"]), 3)
            text = record.read_text(encoding="utf-8").lower()
            self.assertNotIn("sk-", text)
            self.assertIn("api_key", text)
            self.assertIn("<redacted>", text)

    def test_import_reports_author_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            events_root = Path(tmp)
            copy_clean_demo(events_root)
            env = os.environ.copy()
            env["YUQING_EVENTS_DIR"] = str(events_root)
            out = run([sys.executable, "-m", "personas.import_event",
                       "--event", "demo"], env)
            self.assertIn("唯一作者: 4", out)
            report = events_root / "demo" / "interim" / "import_report.json"
            self.assertTrue(report.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["accepted"], 9)


if __name__ == "__main__":
    unittest.main()
