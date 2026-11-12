"""Tests for server.app data functions (stdlib unittest, offline)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app import load_records, load_summary  # noqa: E402


def rec(site, device, day, value):
    return {"site": site, "device": device, "day": day, "value": float(value)}


class TestApiDataFunctions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        data = self.root / "data"
        data.mkdir(parents=True)

        self.records = [
            rec("a", "d2", "2024-01-02", 7.0),
            rec("a", "d1", "2024-01-03", 6.0),
            rec("b", "d1", "2024-01-01", 9.0),
            rec("a", "d1", "2024-01-01", 5.0),
        ]
        with (data / "records.jsonl").open("w", encoding="utf-8") as fh:
            for r in self.records:
                fh.write(json.dumps(r) + "\n")

        self.summary = {"unit": "count", "total": 27.0, "record_count": 4}
        with (data / "summary.json").open("w", encoding="utf-8") as fh:
            json.dump(self.summary, fh)

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_summary(self):
        self.assertEqual(load_summary(self.root), self.summary)

    def test_load_records_all_sorted_by_device_day(self):
        got = load_records(self.root)
        self.assertEqual(len(got), 4)
        keys = [(r["device"], r["day"]) for r in got]
        self.assertEqual(keys, sorted(keys))

    def test_load_records_site_filter(self):
        got = load_records(self.root, site="a")
        self.assertEqual(len(got), 3)
        self.assertTrue(all(r["site"] == "a" for r in got))
        self.assertEqual(
            [(r["device"], r["day"]) for r in got],
            [("d1", "2024-01-01"), ("d1", "2024-01-03"), ("d2", "2024-01-02")],
        )

    def test_load_records_limit(self):
        got = load_records(self.root, limit=2)
        self.assertEqual(len(got), 2)
        # sorted by (device, day): both d1/2024-01-01 rows come first
        self.assertEqual(
            [(r["device"], r["day"]) for r in got],
            [("d1", "2024-01-01"), ("d1", "2024-01-01")],
        )

    def test_load_records_site_and_limit(self):
        got = load_records(self.root, site="a", limit=1)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["site"], "a")
        self.assertEqual(got[0]["device"], "d1")


if __name__ == "__main__":
    unittest.main()
