import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulation.infra.event import load_event  # noqa: E402
from personas.import_event import (  # noqa: E402
    DEFAULT_TIME_FORMATS, _field_map, _parse_published, _source_value,
)


class ImportEventTest(unittest.TestCase):
    def setUp(self):
        self.spec = load_event("demo")
        self.formats = list(self.spec.inputs.get("time_formats") or DEFAULT_TIME_FORMATS)

    def test_chinese_column_map(self):
        header = ["评论ID", "用户名", "评论内容", "发布时间", "来源"]
        mapping = self.spec.inputs.get("column_map") or {}
        got = _field_map(header, mapping)
        self.assertEqual(got["comment_id"], "评论ID")
        self.assertEqual(got["author"], "用户名")
        self.assertEqual(got["body"], "评论内容")
        self.assertEqual(got["published"], "发布时间")
        self.assertEqual(got["channel"], "来源")

    def test_parse_chinese_date(self):
        dt = _parse_published("2024年1月1日 09:30", self.spec, self.formats)
        self.assertIsInstance(dt, datetime)
        self.assertEqual(dt.isoformat(), "2024-01-01T09:30:00+08:00")

    def test_parse_iso_and_offset(self):
        dt = _parse_published("2024-01-01T01:30:00Z", self.spec, self.formats)
        self.assertEqual(dt.isoformat(), "2024-01-01T09:30:00+08:00")

    def test_parse_epoch(self):
        dt = _parse_published("1704067200", self.spec, self.formats)
        self.assertEqual(dt.date().isoformat(), "2024-01-01")

    def test_source_policy_constant(self):
        mapping = {"author": "用户名"}
        value = _source_value({"用户名": "张三"}, mapping, self.spec)
        self.assertEqual(value, "synthetic")

    def test_source_policy_column(self):
        spec = self.spec
        spec.spec["input"]["source_policy"] = {"mode": "column", "column": "source"}
        value = _source_value({"source": "other"}, {"source": "source"}, spec)
        self.assertEqual(value, "other")


if __name__ == "__main__":
    unittest.main()
