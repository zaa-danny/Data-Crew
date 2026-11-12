"""Tests for pipeline/analyze.py and pipeline/store.py."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.analyze import analyze  # noqa: E402
from pipeline.store import store  # noqa: E402


PARAMS = {"unit": "count", "min_value": 5.0, "top_k": 2, "round_digits": 2}


def rec(site, device, day, value):
    return {"site": site, "device": device, "day": day, "value": value}


class ThresholdTests(unittest.TestCase):
    def test_threshold_filtering_and_counts(self):
        records = [
            rec("a", "d1", "2024-01-01", 5.0),   # boundary -> kept
            rec("a", "d1", "2024-01-02", 4.99),  # below -> dropped
            rec("b", "d2", "2024-01-01", 10.0),  # kept
            rec("b", "d2", "2024-01-02", 1.0),   # dropped
        ]
        s = analyze(records, PARAMS)
        self.assertEqual(s["record_count"], 4)  # counts ALL rows
        self.assertEqual(s["kept_count"], 2)
        self.assertEqual(s["total"], 15.0)
        self.assertEqual(s["mean"], 7.5)
        self.assertEqual(s["site_count"], 2)
        self.assertEqual(s["device_count"], 2)

    def test_nothing_kept(self):
        records = [rec("a", "d1", "2024-01-01", 1.0)]
        s = analyze(records, PARAMS)
        self.assertEqual(s["kept_count"], 0)
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["mean"], 0.0)
        self.assertEqual(s["by_site"], [])
        self.assertEqual(s["top_devices"], [])
        self.assertIsNone(s["max"])
        self.assertIsNone(s["min"])


class BySiteTests(unittest.TestCase):
    def test_by_site_order_desc_then_site_asc(self):
        records = [
            rec("b", "d1", "2024-01-01", 10.0),
            rec("a", "d2", "2024-01-01", 10.0),  # tie total with b -> a first
            rec("c", "d3", "2024-01-01", 20.0),
        ]
        s = analyze(records, PARAMS)
        sites = [d["site"] for d in s["by_site"]]
        self.assertEqual(sites, ["c", "a", "b"])
        self.assertEqual(s["by_site"][0]["total"], 20.0)
        self.assertEqual(s["by_site"][1]["total"], 10.0)


class TopDevicesTests(unittest.TestCase):
    def test_top_devices_truncation_and_ties(self):
        records = [
            rec("a", "d1", "2024-01-01", 10.0),
            rec("a", "d2", "2024-01-01", 10.0),  # tie total, site a, d1<d2
            rec("b", "d1", "2024-01-01", 10.0),  # tie total, site b last
            rec("c", "d9", "2024-01-01", 30.0),  # highest
        ]
        s = analyze(records, PARAMS)
        self.assertEqual(len(s["top_devices"]), 2)
        self.assertEqual(s["top_devices"][0]["site"], "c")
        self.assertEqual(s["top_devices"][1]["site"], "a")
        self.assertEqual(s["top_devices"][1]["device"], "d1")


class StoreTests(unittest.TestCase):
    def test_sqlite_roundtrip_idempotent(self):
        records = [
            rec("a", "d1", "2024-01-01", 5.0),
            rec("b", "d2", "2024-01-02", 10.0),
        ]
        summary = analyze(records, PARAMS)
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "app.db")
            store(records, summary, db)
            store(records, summary, db)  # idempotent second call
            conn = sqlite3.connect(db)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM records")
                self.assertEqual(cur.fetchone()[0], 2)
                cur.execute("SELECT value FROM records WHERE site='b'")
                self.assertEqual(cur.fetchone()[0], 10.0)
                cur.execute("SELECT total, mean, record_count FROM summary")
                row = cur.fetchone()
                self.assertEqual(float(row[0]), 15.0)
                self.assertEqual(float(row[1]), 7.5)
                self.assertEqual(int(row[2]), 2)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
