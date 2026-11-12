"""Tests for server/app.py data functions."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app import load_records, load_summary  # noqa: E402


def rec(site, device, day, value):
    return {"site": site, "device": device, "day": day, "value": value}


class ApiDataTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        data = self.root / "data"
        data.mkdir()
        self.summary = {"unit": "count", "total": 15.0, "record_count": 3}
        (data / "summary.json").write_text(json.dumps(self.summary), encoding="utf-8")
        self.records = [
            rec("a", "d2", "2024-01-02", 5.0),
            rec("a", "d1", "2024-01-01", 10.0),
            rec("b", "d1", "2024-01-01", 7.0),
        ]
        with (data / "records.jsonl").open("w", encoding="utf-8") as fh:
            for r in self.records:
                fh.write(json.dumps(r) + "\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_summary(self):
        self.assertEqual(load_summary(self.root), self.summary)

    def test_load_records_sorted_by_device_day(self):
        out = load_records(self.root)
        self.assertEqual(len(out), 3)
        keys = [(r["device"], r["day"]) for r in out]
        self.assertEqual(keys, sorted(keys))

    def test_load_records_site_filter(self):
        out = load_records(self.root, site="a")
        self.assertEqual(len(out), 2)
        self.assertTrue(all(r["site"] == "a" for r in out))

    def test_load_records_limit(self):
        out = load_records(self.root, limit=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["device"], "d1")


if __name__ == "__main__":
    unittest.main()
