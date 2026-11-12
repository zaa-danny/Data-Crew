"""Tests for pipeline/analyze.py and pipeline/store.py (stdlib unittest)."""
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


def _rec(site, device, day, value):
    return {"site": site, "device": device, "day": day, "value": value}


PARAMS = {"unit": "count", "min_value": 5.0, "top_k": 2, "round_digits": 2}


class ThresholdFilterTests(unittest.TestCase):
    def test_boundary_kept_and_record_count_counts_all(self):
        records = [
            _rec("a", "d1", "2024-01-01", 5.0),   # boundary -> kept
            _rec("a", "d1", "2024-01-02", 4.99),  # below -> dropped
            _rec("b", "d2", "2024-01-01", 10.0),  # kept
            _rec("b", "d2", "2024-01-02", 1.0),   # dropped
        ]
        summary = analyze(records, PARAMS)
        self.assertEqual(summary["record_count"], 4)
        self.assertEqual(summary["kept_count"], 2)
        self.assertEqual(summary["total"], 15.0)
        self.assertEqual(summary["mean"], 7.5)
        self.assertEqual(summary["site_count"], 2)
        self.assertEqual(summary["device_count"], 2)

    def test_nothing_kept_gives_zero_mean(self):
        records = [_rec("a", "d1", "2024-01-01", 1.0)]
        summary = analyze(records, PARAMS)
        self.assertEqual(summary["kept_count"], 0)
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["mean"], 0.0)
        self.assertEqual(summary["by_site"], [])
        self.assertEqual(summary["top_devices"], [])
        self.assertIsNone(summary["max"])
        self.assertIsNone(summary["min"])


class BySiteOrderingTests(unittest.TestCase):
    def test_total_descending_ties_by_site_ascending(self):
        records = [
            _rec("zeta", "d1", "2024-01-01", 10.0),
            _rec("alpha", "d1", "2024-01-01", 10.0),  # tie with zeta -> alpha first
            _rec("beta", "d1", "2024-01-01", 20.0),   # highest total
            _rec("beta", "d2", "2024-01-02", 5.0),
        ]
        summary = analyze(records, PARAMS)
        sites = [row["site"] for row in summary["by_site"]]
        self.assertEqual(sites, ["beta", "alpha", "zeta"])
        beta = summary["by_site"][0]
        self.assertEqual(beta["total"], 25.0)
        self.assertEqual(beta["kept_count"], 2)
        self.assertEqual(beta["device_count"], 2)
        self.assertEqual(beta["mean"], 12.5)


class TopDevicesTests(unittest.TestCase):
    def test_truncation_and_tie_ordering(self):
        records = [
            _rec("s1", "b", "2024-01-01", 10.0),
            _rec("s1", "a", "2024-01-01", 10.0),  # tie with b -> a first
            _rec("s2", "a", "2024-01-01", 10.0),  # tie total, site s1 < s2
            _rec("s3", "z", "2024-01-01", 30.0),  # highest
        ]
        summary = analyze(records, PARAMS)
        top = summary["top_devices"]
        self.assertEqual(len(top), PARAMS["top_k"])
        self.assertEqual((top[0]["site"], top[0]["device"]), ("s3", "z"))
        self.assertEqual((top[1]["site"], top[1]["device"]), ("s1", "a"))
        self.assertEqual(top[0]["total"], 30.0)
        self.assertEqual(top[1]["mean"], 10.0)


class StoreRoundTripTests(unittest.TestCase):
    def test_sqlite_round_trip_and_idempotent(self):
        records = [
            _rec("a", "d1", "2024-01-01", 5.0),
            _rec("a", "d1", "2024-01-02", 4.0),
            _rec("b", "d2", "2024-01-01", 10.0),
        ]
        summary = analyze(records, PARAMS)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "app.db")
            store(records, summary, db_path)
            store(records, summary, db_path)  # idempotent second call

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT site, device, day, value FROM records "
                    "ORDER BY site, device, day"
                ).fetchall()
                self.assertEqual(len(rows), 3)  # ALL records stored
                self.assertEqual(rows[0], ("a", "d1", "2024-01-01", 5.0))
                self.assertEqual(rows[1], ("a", "d1", "2024-01-02", 4.0))

                summary_rows = dict(
                    conn.execute("SELECT key, value FROM summary").fetchall()
                )
                self.assertIn("total", summary_rows)
                self.assertIn("mean", summary_rows)
                self.assertIn("record_count", summary_rows)
                self.assertEqual(summary_rows["record_count"], "3")
                self.assertEqual(float(summary_rows["total"]), 15.0)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
