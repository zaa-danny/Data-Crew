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
            rec("a", "d1", "2024-01-02", 4.999),  # below -> dropped
            rec("b", "d2", "2024-01-01", 10.0),  # kept
            rec("b", "d2", "2024-01-02", 1.0),   # dropped
        ]
        summary = analyze(records, PARAMS)
        self.assertEqual(summary["record_count"], 4)  # counts ALL rows
        self.assertEqual(summary["kept_count"], 2)
        self.assertEqual(summary["total"], 15.0)
        self.assertEqual(summary["mean"], 7.5)
        self.assertEqual(summary["site_count"], 2)
        self.assertEqual(summary["device_count"], 2)
        # dropped site/device must not appear
        self.assertEqual({s["site"] for s in summary["by_site"]}, {"a", "b"})

    def test_nothing_kept(self):
        records = [rec("a", "d1", "2024-01-01", 1.0)]
        summary = analyze(records, PARAMS)
        self.assertEqual(summary["kept_count"], 0)
        self.assertEqual(summary["total"], 0.0)
        self.assertEqual(summary["mean"], 0.0)
        self.assertEqual(summary["by_site"], [])
        self.assertEqual(summary["top_devices"], [])
        self.assertIsNone(summary["max"])
        self.assertIsNone(summary["min"])


class BySiteOrderingTests(unittest.TestCase):
    def test_total_desc_then_site_asc(self):
        records = [
            rec("b", "d1", "2024-01-01", 10.0),
            rec("a", "d1", "2024-01-01", 10.0),  # tie with b -> a first
            rec("c", "d1", "2024-01-01", 20.0),  # highest total
        ]
        summary = analyze(records, PARAMS)
        order = [s["site"] for s in summary["by_site"]]
        self.assertEqual(order, ["c", "a", "b"])
        totals = [s["total"] for s in summary["by_site"]]
        self.assertEqual(totals, [20.0, 10.0, 10.0])


class TopDevicesTests(unittest.TestCase):
    def test_truncation_and_tie_order(self):
        records = [
            rec("a", "d1", "2024-01-01", 10.0),
            rec("a", "d2", "2024-01-01", 10.0),  # tie total with a/d1
            rec("b", "d1", "2024-01-01", 10.0),  # tie total, site b last
            rec("c", "d9", "2024-01-01", 30.0),  # biggest
        ]
        summary = analyze(records, PARAMS)
        top = summary["top_devices"]
        self.assertEqual(len(top), PARAMS["top_k"])  # truncated to top_k
        self.assertEqual(
            [(d["site"], d["device"]) for d in top],
            [("c", "d9"), ("a", "d1")],
        )


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
                self.assertEqual(cur.fetchone()[0], len(records))  # ALL rows
                cur.execute(
                    "SELECT value FROM records WHERE site=? AND device=? AND day=?",
                    ("a", "d1", "2024-01-02"),
                )
                self.assertEqual(cur.fetchone()[0], 4.0)  # unfiltered stored
                cur.execute("SELECT value FROM summary WHERE key='total'")
                self.assertEqual(float(cur.fetchone()[0]), summary["total"])
                cur.execute("SELECT value FROM summary WHERE key='record_count'")
                self.assertEqual(int(cur.fetchone()[0]), len(records))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
