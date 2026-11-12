"""Tests for server/app.py data helpers (stdlib unittest, offline)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app import load_records, load_summary  # noqa: E402


def _rec(site, device, day, value):
    return {"site": site, "device": device, "day": day, "value": value}


class ApiDataFunctionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        data = self.root / "data"
        data.mkdir(parents=True)

        self.summary = {
            "unit": "count",
            "record_count": 4,
            "kept_count": 2,
            "total": 15.0,
            "mean": 7.5,
        }
        (data / "summary.json").write_text(
            json.dumps(self.summary), encoding="utf-8"
        )

        self.records = [
            _rec("a", "d2", "2024-01-02", 6.0),
            _rec("a", "d1", "2024-01-03", 5.0),
            _rec("b", "d1", "2024-01-01", 10.0),
            _rec("a", "d1", "2024-01-01", 4.0),
        ]
        with (data / "records.jsonl").open("w", encoding="utf-8") as fh:
            for rec in self.records:
                fh.write(json.dumps(rec) + "\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_summary(self):
        self.assertEqual(load_summary(self.root), self.summary)

    def test_load_records_sorted_by_device_then_day(self):
        records = load_records(self.root)
        self.assertEqual(len(records), 4)
        keys = [(r["device"], r["day"]) for r in records]
        self.assertEqual(keys, sorted(keys))

    def test_load_records_site_filter(self):
        records = load_records(self.root, site="a")
        self.assertEqual(len(records), 3)
        self.assertTrue(all(r["site"] == "a" for r in records))
        keys = [(r["device"], r["day"]) for r in records]
        self.assertEqual(keys, sorted(keys))

    def test_load_records_limit(self):
        records = load_records(self.root, limit=2)
        self.assertEqual(len(records), 2)
        # limit applies after sorting by (device, day): both d1/2024-01-01 first
        self.assertEqual(
            [(r["device"], r["day"]) for r in records],
            [("d1", "2024-01-01"), ("d1", "2024-01-01")],
        )

    def test_load_records_site_and_limit(self):
        records = load_records(self.root, site="a", limit=1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["site"], "a")
        self.assertEqual(records[0]["device"], "d1")
        self.assertEqual(records[0]["day"], "2024-01-01")


if __name__ == "__main__":
    unittest.main()
