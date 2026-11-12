"""Tests for server.app data helpers (stdlib unittest, offline)."""
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
        # Every (device, day) pair is unique so the (device, day) sort is
        # fully deterministic with no tie-breaking needed.
        self.records = [
            rec("a", "d2", "2024-01-02", 5.0),
            rec("a", "d1", "2024-01-01", 10.0),
            rec("b", "d3", "2024-01-01", 1.0),
            rec("a", "d1", "2024-01-03", 2.0),
        ]
        with (data / "records.jsonl").open("w", encoding="utf-8") as fh:
            for r in self.records:
                fh.write(json.dumps(r) + "\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_summary(self):
        self.assertEqual(load_summary(self.root), self.summary)

    def test_load_records_site_filter_and_order(self):
        rows = load_records(self.root, site="a")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["site"] == "a" for r in rows))
        keys = [(r["device"], r["day"]) for r in rows]
        self.assertEqual(keys, sorted(keys))  # ascending by (device, day)

    def test_load_records_limit(self):
        # Unique (device, day) keys -> deterministic sort order:
        #   d1/2024-01-01, d1/2024-01-03, d2/2024-01-02, d3/2024-01-01
        rows = load_records(self.root, limit=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [(r["device"], r["day"]) for r in rows],
            [("d1", "2024-01-01"), ("d1", "2024-01-03")],
        )
        rows3 = load_records(self.root, limit=3)
        self.assertEqual(len(rows3), 3)
        self.assertEqual(
            [(r["device"], r["day"]) for r in rows3],
            [("d1", "2024-01-01"), ("d1", "2024-01-03"), ("d2", "2024-01-02")],
        )

    def test_load_records_no_filter_returns_all(self):
        rows = load_records(self.root)
        self.assertEqual(len(rows), len(self.records))


if __name__ == "__main__":
    unittest.main()
