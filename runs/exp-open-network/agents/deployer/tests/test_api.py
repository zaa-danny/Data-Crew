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
        data_dir = self.root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        self.summary = {
            "unit": "count",
            "record_count": 5,
            "kept_count": 3,
            "site_count": 2,
            "device_count": 3,
            "min_value": 5.0,
            "round_digits": 2,
            "top_k": 2,
            "total": 21.0,
            "mean": 7.0,
            "max": rec("beta", "d1", "2024-01-02", 9.0),
            "min": rec("alpha", "d1", "2024-01-01", 5.0),
            "by_site": [],
            "top_devices": [],
        }
        (data_dir / "summary.json").write_text(
            json.dumps(self.summary), encoding="utf-8"
        )

        self.records = [
            rec("alpha", "d2", "2024-01-02", 6.0),
            rec("alpha", "d1", "2024-01-03", 5.0),
            rec("beta", "d1", "2024-01-02", 9.0),
            rec("alpha", "d1", "2024-01-01", 7.0),
            rec("beta", "d2", "2024-01-01", 1.0),
        ]
        with open(data_dir / "records.jsonl", "w", encoding="utf-8") as f:
            for r in self.records:
                f.write(json.dumps(r) + "\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_summary_round_trip(self):
        loaded = load_summary(self.root)
        self.assertEqual(loaded, self.summary)
        self.assertEqual(loaded["record_count"], 5)
        self.assertAlmostEqual(loaded["total"], 21.0, places=6)

    def test_load_records_sorted_by_device_then_day(self):
        loaded = load_records(self.root)
        self.assertEqual(len(loaded), len(self.records))
        keys = [(r["device"], r["day"]) for r in loaded]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(
            keys,
            [
                ("d1", "2024-01-01"),
                ("d1", "2024-01-02"),
                ("d1", "2024-01-03"),
                ("d2", "2024-01-01"),
                ("d2", "2024-01-02"),
            ],
        )

    def test_load_records_site_filter(self):
        loaded = load_records(self.root, site="alpha")
        self.assertEqual(len(loaded), 3)
        self.assertTrue(all(r["site"] == "alpha" for r in loaded))
        self.assertEqual(
            [(r["device"], r["day"]) for r in loaded],
            [("d1", "2024-01-01"), ("d1", "2024-01-03"), ("d2", "2024-01-02")],
        )

        missing = load_records(self.root, site="nope")
        self.assertEqual(missing, [])

    def test_load_records_limit(self):
        loaded = load_records(self.root, limit=2)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(
            [(r["device"], r["day"]) for r in loaded],
            [("d1", "2024-01-01"), ("d1", "2024-01-02")],
        )

        # limit applies after the site filter
        filtered = load_records(self.root, site="alpha", limit=1)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["site"], "alpha")
        self.assertEqual(filtered[0]["device"], "d1")

        # limit 0 yields nothing; invalid limits are ignored
        self.assertEqual(load_records(self.root, limit=0), [])
        self.assertEqual(len(load_records(self.root, limit="abc")), len(self.records))
        self.assertEqual(len(load_records(self.root, limit=-1)), len(self.records))


if __name__ == "__main__":
    unittest.main()
