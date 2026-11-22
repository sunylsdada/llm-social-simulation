"""发布卫生检查：不包含私有数据、特定案例声明或 key。"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_DIRS = [
    "data", "outputs", "backups", "archive", ".idea",
    "events/" + "east_" + "palestine",
]
FORBIDDEN_EXT = {".pdf", ".pptx", ".docx", ".jpg", ".jpeg", ".png",
                 ".pickle", ".pyc", ".pyo"}
GENERATED_PARTS = {"outputs", "processed"}
MAX_FILE = 250 * 1024
MAX_TOTAL = 2 * 1024 * 1024

# 用拼接片段构造敏感词，避免测试文件匹配到自己。
FRAGMENTS = [
    "东" + "巴勒斯坦", "俄" + "亥俄", "East " + "Palestine", "Oh" + "io",
    "12" + "27", "13" + "67", "16" + "78", "6" + "18", "19" + "50",
    "7" + "3%", "AI " + "生成评论", "合成" + "作者",
    "36 " + "个 agent", "真实+" + "AI", "data/" + "simulated",
    "data" + "test", "sk-" + "ojyvg", "D:" + "\\桌面", "C:" + "\\Users",
    "/root/" + "autodl",
]


def _is_generated(path: Path) -> bool:
    parts = set(path.parts)
    if parts & GENERATED_PARTS:
        return True
    if "interim" in parts and path.name != "author_big5.jsonl":
        return True
    return False


def _iter_files():
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if ".git" in p.parts or "__pycache__" in p.parts:
            continue
        if _is_generated(p):
            continue
        yield p


class ReleaseHygieneTest(unittest.TestCase):
    def test_no_forbidden_directories(self):
        for rel in FORBIDDEN_DIRS:
            self.assertFalse((ROOT / rel).exists(), f"出现禁止路径: {rel}")

    def test_no_binary_or_large_files(self):
        total = 0
        for p in _iter_files():
            self.assertNotIn(p.suffix.lower(), FORBIDDEN_EXT, str(p))
            size = p.stat().st_size
            self.assertLessEqual(size, MAX_FILE, f"文件过大: {p}")
            total += size
        self.assertLessEqual(total, MAX_TOTAL, f"发布目录过大: {total}")

    def test_no_forbidden_strings(self):
        pieces = FRAGMENTS
        key_re = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
        for p in _iter_files():
            if p.suffix.lower() not in {".py", ".md", ".json", ".txt", ".csv"}:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for piece in pieces:
                self.assertNotIn(piece, text, f"{piece!r} 出现在 {p}")
            self.assertIsNone(key_re.search(text), f"{p} 中出现类似 key 的字符串")

    def test_demo_files_exist(self):
        for rel in (
            "events/demo/event.json",
            "events/demo/input/raw_comments.csv",
            "events/demo/input/stimuli.csv",
            "events/demo/interim/author_big5.jsonl",
        ):
            self.assertTrue((ROOT / rel).exists(), rel)


if __name__ == "__main__":
    unittest.main()
