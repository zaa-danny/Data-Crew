"""Tests for pipeline.analyze and pipeline.store (stdlib unittest, offline)."""

from __future__ import annotations

import json
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


class ThresholdFilterTests(unittest.TestCase):
    def test_threshold_boundary_and_counts(self):
        records = [
            rec("a", "d1", "2024-01-01", 5.0),   # boundary -> kept
            rec("a", "d1", "2024-01-02", 4.99),  # below -> dropped
            rec("b", "d2", "2024-01-01", 10.0),  # kept
            rec("b", "d2", "2024-01-02", 1.0),   # dropped
        ]
        s = analyze(records, PARAMS)
        self.assertEqual(s["record_count"], 4)   # counts ALL rows
        self.assertEqual(s["kept_count"], 2)
        self.assertEqual(s["site_count"], 2)
        self.assertEqual(s["device_count"], 2)
        self.assertEqual(s["total"], 15.0)
        self.assertEqual(s["mean"], 7.5)

    def test_nothing_kept(self):
        records = [rec("a", "d1", "2024-01-01", 1.0)]
        s = analyze(records, PARAMS)
        self.assertEqual(s["record_count"], 1)
        self.assertEqual(s["kept_count"], 0)
        self.assertEqual(s["total"], 0.0)
        self.assertEqual(s["mean"], 0.0)
        self.assertEqual(s["by_site"], [])
        self.assertEqual(s["top_devices"], [])
        self.assertIsNone(s["max"])
        self.assertIsNone(s["min"])


class BySiteOrderingTests(unittest.TestCase):
    def test_total_desc_then_site_asc(self):
        records = [
            rec("b", "d1", "2024-01-01", 10.0),
            rec("a", "d1", "2024-01-01", 10.0),  # tie with b -> a first
            rec("c", "d1", "2024-01-01", 20.0),  # highest total
            rec("d", "d1", "2024-01-01", 1.0),   # dropped
        ]
        s = analyze(records, PARAMS)
        sites = [row["site"] for row in s["by_site"]]
        self.assertEqual(sites, ["c", "a", "b"])
        self.assertEqual([row["total"] for row in s["by_site"]], [20.0, 10.0, 10.0])
        # site mean uses site total / site kept count
        self.assertEqual(s["by_site"][0]["mean"], 20.0)
        self.assertEqual(s["by_site"][0]["kept_count"], 1)


class TopDevicesTests(unittest.TestCase):
    def test_truncation_and_tie_order(self):
        records = [
            rec("a", "z", "2024-01-01", 10.0),
            rec("a", "y", "2024-01-01", 10.0),  # tie total, device asc -> y before z
            rec("b", "x", "2024-01-01", 30.0),  # highest
            rec("c", "w", "2024-01-01", 5.0),   # lowest
        ]
        s = analyze(records, PARAMS)
        self.assertEqual(len(s["top_devices"]), PARAMS["top_k"])
        got = [(d["site"], d["device"]) for d in s["top_devices"]]
        self.assertEqual(got, [("b", "x"), ("a", "y")])
        self.assertEqual(s["top_devices"][0]["total"], 30.0)

    def test_top_k_larger_than_devices(self):
        params = dict(PARAMS, top_k=10)
        records = [rec("a", "d1", "2024-01-01", 6.0)]
        s = analyze(records, params)
        self.assertEqual(len(s["top_devices"]), 1)


class StoreRoundTripTests(unittest.TestCase):
    def test_sqlite_round_trip_idempotent(self):
        records = [
            rec("a", "d1", "2024-01-01", 5.0),
            rec("a", "d1", "2024-01-02", 4.0),
            rec("b", "d2", "2024-01-01", 10.0),
        ]
        summary = analyze(records, PARAMS)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "app.db")
            store(records, summary, db_path)
            store(records, summary, db_path)  # idempotent second call

            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM records")
                self.assertEqual(cur.fetchone()[0], 3)  # ALL records stored
                cur.execute(
                    "SELECT value FROM records WHERE site=? AND device=? AND day=?",
                    ("a", "d1", "2024-01-02"),
                )
                self.assertEqual(cur.fetchone()[0], 4.0)
                cur.execute("SELECT value FROM summary WHERE key='total'")
                self.assertEqual(float(cur.fetchone()[0]), summary["total"])
                cur.execute("SELECT value FROM summary WHERE key='record_count'")
                self.assertEqual(int(cur.fetchone()[0]), 3)
                cur.execute("SELECT value FROM summary WHERE key='mean'")
                self.assertEqual(float(cur.fetchone()[0]), summary["mean"])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
