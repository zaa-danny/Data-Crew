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


def sample_records():
    """Small synthetic set where the threshold and ties both matter.

    Values: 5.0 is exactly the threshold (kept), 4.99 is below (dropped).
    """
    return [
        rec("alpha", "d1", "2024-01-01", 5.0),   # boundary -> kept
        rec("alpha", "d1", "2024-01-02", 4.99),  # below -> dropped
        rec("alpha", "d2", "2024-01-01", 3.0),   # below -> dropped
        rec("beta", "d1", "2024-01-01", 6.0),    # kept
        rec("beta", "d2", "2024-01-01", 2.0),    # below -> dropped
        rec("gamma", "d1", "2024-01-01", 5.0),   # kept, ties alpha total
        rec("gamma", "d2", "2024-01-01", 7.0),   # kept
    ]


class ThresholdFilteringTests(unittest.TestCase):
    def test_threshold_boundary_and_counts(self):
        records = sample_records()
        summary = analyze(records, PARAMS)

        # record_count counts ALL rows, kept_count only value >= min_value
        self.assertEqual(summary["record_count"], len(records))
        self.assertEqual(summary["kept_count"], 4)
        self.assertEqual(summary["min_value"], 5.0)

        # boundary value 5.0 is kept; 4.99 is not
        kept_values = sorted(
            r["value"] for r in records if r["value"] >= PARAMS["min_value"]
        )
        self.assertEqual(kept_values, [5.0, 5.0, 6.0, 7.0])

        # total = round(sum raw kept) = 23.0 ; mean = round(23.0/4) = 5.75
        self.assertAlmostEqual(summary["total"], 23.0, places=6)
        self.assertAlmostEqual(summary["mean"], 5.75, places=6)

        # distinct sites among kept: alpha, beta, gamma
        self.assertEqual(summary["site_count"], 3)
        # distinct (site, device) among kept: alpha/d1, beta/d1, gamma/d1, gamma/d2
        self.assertEqual(summary["device_count"], 4)

    def test_nothing_kept_gives_zero_mean(self):
        records = [rec("alpha", "d1", "2024-01-01", 1.0),
                   rec("beta", "d1", "2024-01-01", 2.0)]
        summary = analyze(records, PARAMS)
        self.assertEqual(summary["record_count"], 2)
        self.assertEqual(summary["kept_count"], 0)
        self.assertEqual(summary["total"], 0.0)
        self.assertEqual(summary["mean"], 0.0)
        self.assertEqual(summary["by_site"], [])
        self.assertEqual(summary["top_devices"], [])
        self.assertIsNone(summary["max"])
        self.assertIsNone(summary["min"])

    def test_max_min_tie_break(self):
        records = [
            rec("beta", "d1", "2024-01-02", 9.0),
            rec("alpha", "d2", "2024-01-03", 9.0),  # same value, smaller site
            rec("alpha", "d1", "2024-01-01", 5.0),
            rec("gamma", "d1", "2024-01-01", 5.0),  # min tie, larger site
        ]
        summary = analyze(records, PARAMS)
        self.assertEqual(summary["max"]["site"], "alpha")
        self.assertEqual(summary["max"]["device"], "d2")
        self.assertEqual(summary["min"]["site"], "alpha")
        self.assertEqual(summary["min"]["device"], "d1")


class BySiteOrderingTests(unittest.TestCase):
    def test_by_site_total_desc_then_site_asc(self):
        records = [
            # gamma total 12.0 (highest)
            rec("gamma", "d1", "2024-01-01", 6.0),
            rec("gamma", "d2", "2024-01-01", 6.0),
            # alpha total 10.0 and beta total 10.0 -> tie broken by site ASC
            rec("alpha", "d1", "2024-01-01", 10.0),
            rec("beta", "d1", "2024-01-01", 10.0),
            # delta entirely below threshold -> omitted
            rec("delta", "d1", "2024-01-01", 1.0),
        ]
        summary = analyze(records, PARAMS)
        sites = [row["site"] for row in summary["by_site"]]
        self.assertEqual(sites, ["gamma", "alpha", "beta"])

        by_site = {row["site"]: row for row in summary["by_site"]}
        self.assertNotIn("delta", by_site)
        self.assertAlmostEqual(by_site["gamma"]["total"], 12.0, places=6)
        self.assertAlmostEqual(by_site["gamma"]["mean"], 6.0, places=6)
        self.assertEqual(by_site["gamma"]["kept_count"], 2)
        self.assertEqual(by_site["gamma"]["device_count"], 2)
        self.assertAlmostEqual(by_site["alpha"]["total"], 10.0, places=6)
        self.assertEqual(by_site["alpha"]["kept_count"], 1)


class TopDevicesTests(unittest.TestCase):
    def test_top_devices_truncation_and_tie_order(self):
        records = [
            # alpha/d1 total 20.0 -> rank 1
            rec("alpha", "d1", "2024-01-01", 10.0),
            rec("alpha", "d1", "2024-01-02", 10.0),
            # beta/d1 total 15.0 and gamma/d1 total 15.0 -> tie, site ASC
            rec("beta", "d1", "2024-01-01", 15.0),
            rec("gamma", "d1", "2024-01-01", 15.0),
            # alpha/d2 total 5.0 -> below the top_k cut
            rec("alpha", "d2", "2024-01-01", 5.0),
        ]
        params = dict(PARAMS, top_k=2)
        summary = analyze(records, params)
        self.assertEqual(len(summary["top_devices"]), 2)
        self.assertEqual(
            [(d["site"], d["device"]) for d in summary["top_devices"]],
            [("alpha", "d1"), ("beta", "d1")],
        )
        self.assertAlmostEqual(summary["top_devices"][0]["total"], 20.0, places=6)
        self.assertAlmostEqual(summary["top_devices"][0]["mean"], 10.0, places=6)

    def test_top_devices_tie_on_site_uses_device_asc(self):
        records = [
            rec("alpha", "d2", "2024-01-01", 8.0),
            rec("alpha", "d1", "2024-01-01", 8.0),
            rec("beta", "d1", "2024-01-01", 1.0),
        ]
        params = dict(PARAMS, top_k=2)
        summary = analyze(records, params)
        self.assertEqual(
            [(d["site"], d["device"]) for d in summary["top_devices"]],
            [("alpha", "d1"), ("alpha", "d2")],
        )


class StoreRoundTripTests(unittest.TestCase):
    def test_sqlite_round_trip_is_idempotent(self):
        records = sample_records()
        summary = analyze(records, PARAMS)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "nested" / "app.db")
            store(records, summary, db_path)
            store(records, summary, db_path)  # idempotent: no duplicate rows

            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM records")
                self.assertEqual(cur.fetchone()[0], len(records))

                cur.execute(
                    "SELECT site, device, day, value FROM records "
                    "ORDER BY site, device, day"
                )
                rows = cur.fetchall()
                expected = sorted(
                    (r["site"], r["device"], r["day"], float(r["value"]))
                    for r in records
                )
                self.assertEqual(rows, expected)

                cur.execute("SELECT key, value FROM summary")
                stored = dict(cur.fetchall())
                self.assertIn("total", stored)
                self.assertIn("mean", stored)
                self.assertIn("record_count", stored)
                self.assertEqual(stored["record_count"], str(len(records)))
                self.assertAlmostEqual(float(stored["total"]), summary["total"], places=6)
                self.assertAlmostEqual(float(stored["mean"]), summary["mean"], places=6)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
