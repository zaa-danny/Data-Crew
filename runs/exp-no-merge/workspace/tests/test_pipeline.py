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
    return {"site": site, "device": device, "day": day, "value": float(value)}


class TestAnalyzeThreshold(unittest.TestCase):
    def test_threshold_filtering_and_counts(self):
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
        self.assertEqual(s["min_value"], 5.0)
        self.assertEqual(s["unit"], "count")

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


class TestAnalyzeBySite(unittest.TestCase):
    def test_by_site_order_total_desc_ties_site_asc(self):
        records = [
            rec("zeta", "d1", "2024-01-01", 10.0),
            rec("alpha", "d1", "2024-01-01", 10.0),  # tie with zeta -> alpha first
            rec("mid", "d1", "2024-01-01", 20.0),    # highest total
        ]
        s = analyze(records, PARAMS)
        order = [row["site"] for row in s["by_site"]]
        self.assertEqual(order, ["mid", "alpha", "zeta"])
        for row in s["by_site"]:
            self.assertEqual(row["kept_count"], 1)
            self.assertEqual(row["device_count"], 1)
            self.assertEqual(row["mean"], row["total"])

    def test_by_site_omits_sites_without_kept(self):
        records = [
            rec("a", "d1", "2024-01-01", 6.0),
            rec("b", "d1", "2024-01-01", 2.0),  # dropped
        ]
        s = analyze(records, PARAMS)
        self.assertEqual([r["site"] for r in s["by_site"]], ["a"])


class TestAnalyzeTopDevices(unittest.TestCase):
    def test_top_devices_truncation_and_tie_order(self):
        records = [
            rec("s1", "dA", "2024-01-01", 30.0),  # total 30
            rec("s1", "dB", "2024-01-01", 20.0),  # total 20
            rec("s2", "dC", "2024-01-01", 20.0),  # total 20, tie -> s1 before s2
            rec("s3", "dD", "2024-01-01", 10.0),  # total 10
        ]
        s = analyze(records, PARAMS)  # top_k = 2
        self.assertEqual(len(s["top_devices"]), 2)
        self.assertEqual(
            [(d["site"], d["device"]) for d in s["top_devices"]],
            [("s1", "dA"), ("s1", "dB")],
        )

    def test_top_devices_tie_by_device_asc(self):
        params = dict(PARAMS, top_k=3)
        records = [
            rec("s1", "dZ", "2024-01-01", 10.0),
            rec("s1", "dA", "2024-01-01", 10.0),
            rec("s1", "dM", "2024-01-01", 10.0),
        ]
        s = analyze(records, params)
        self.assertEqual(
            [d["device"] for d in s["top_devices"]], ["dA", "dM", "dZ"]
        )


class TestStoreRoundTrip(unittest.TestCase):
    def test_sqlite_round_trip_idempotent(self):
        records = [
            rec("a", "d1", "2024-01-01", 5.0),
            rec("a", "d1", "2024-01-02", 4.0),
            rec("b", "d2", "2024-01-01", 12.5),
        ]
        summary = analyze(records, PARAMS)
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "sub" / "app.db")
            store(records, summary, db)
            store(records, summary, db)  # idempotent second call

            conn = sqlite3.connect(db)
            try:
                rows = conn.execute(
                    "SELECT site, device, day, value FROM records "
                    "ORDER BY site, device, day"
                ).fetchall()
                self.assertEqual(len(rows), 3)  # ALL records stored
                self.assertEqual(rows[0], ("a", "d1", "2024-01-01", 5.0))
                self.assertEqual(rows[1], ("a", "d1", "2024-01-02", 4.0))
                self.assertEqual(rows[2], ("b", "d2", "2024-01-01", 12.5))

                kv = dict(
                    conn.execute("SELECT key, value FROM summary").fetchall()
                )
                self.assertEqual(float(kv["total"]), summary["total"])
                self.assertEqual(float(kv["mean"]), summary["mean"])
                self.assertEqual(int(kv["record_count"]), 3)
                self.assertEqual(int(kv["kept_count"]), 2)
                # nested structures stored as JSON text
                self.assertEqual(json.loads(kv["by_site"]), summary["by_site"])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
