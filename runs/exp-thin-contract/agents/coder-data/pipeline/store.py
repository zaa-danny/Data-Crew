"""Store: persist records and the summary into a SQLite database.

Schema
------
``records``  : one row per input record (all records, unfiltered), primary key
               ``(site, device, day)``.
``summary``  : key/value table holding the scalar summary fields.
``by_site``  : one row per site aggregate, ordered by ``(-total, site)``.
``by_device``: one row per device aggregate, ordered by ``(-total, device)``.
``top_devices``: one row per top device, preserving ``(-total, site, device)``
               rank order.

``store`` is idempotent: it drops and recreates its own tables on each call.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any

_SCHEMA = """
DROP TABLE IF EXISTS records;
DROP TABLE IF EXISTS summary;
DROP TABLE IF EXISTS by_site;
DROP TABLE IF EXISTS by_device;
DROP TABLE IF EXISTS top_devices;

CREATE TABLE records (
    site    TEXT NOT NULL,
    device  TEXT NOT NULL,
    day     TEXT NOT NULL,
    value   REAL,
    PRIMARY KEY (site, device, day)
);

CREATE TABLE summary (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE by_site (
    site         TEXT PRIMARY KEY,
    total        REAL NOT NULL,
    mean         REAL NOT NULL,
    device_count INTEGER NOT NULL,
    kept_count   INTEGER NOT NULL
);

CREATE TABLE by_device (
    device TEXT PRIMARY KEY,
    site   TEXT NOT NULL,
    total  REAL NOT NULL,
    mean   REAL NOT NULL,
    count  INTEGER NOT NULL
);

CREATE TABLE top_devices (
    rank   INTEGER PRIMARY KEY,
    device TEXT NOT NULL,
    site   TEXT NOT NULL,
    total  REAL NOT NULL,
    mean   REAL NOT NULL,
    count  INTEGER NOT NULL
);
"""

_SCALAR_KEYS = (
    "unit",
    "min_value",
    "round_digits",
    "top_k",
    "record_count",
    "kept_count",
    "dropped_count",
    "total",
    "mean",
)


def _as_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def store(records: list[dict], summary: dict, db_path: str) -> None:
    """Create/refresh ``db_path`` with records and summary tables."""
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executescript(_SCHEMA)

            conn.executemany(
                "INSERT OR REPLACE INTO records (site, device, day, value) "
                "VALUES (?, ?, ?, ?)",
                [
                    (
                        rec.get("site"),
                        rec.get("device"),
                        rec.get("day"),
                        rec.get("value"),
                    )
                    for rec in records
                ],
            )

            conn.executemany(
                "INSERT INTO summary (key, value) VALUES (?, ?)",
                [(key, _as_text(summary.get(key))) for key in _SCALAR_KEYS],
            )
            for key in ("largest", "smallest"):
                conn.execute(
                    "INSERT INTO summary (key, value) VALUES (?, ?)",
                    (key, _as_text(summary.get(key))),
                )

            by_site = summary.get("by_site") or []
            conn.executemany(
                "INSERT INTO by_site (site, total, mean, device_count, kept_count) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        agg.get("site"),
                        agg.get("total", 0.0),
                        agg.get("mean", 0.0),
                        agg.get("device_count", 0),
                        agg.get("kept_count", 0),
                    )
                    for agg in by_site
                ],
            )

            by_device = summary.get("by_device") or []
            conn.executemany(
                "INSERT INTO by_device (device, site, total, mean, count) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        agg.get("device"),
                        agg.get("site", ""),
                        agg.get("total", 0.0),
                        agg.get("mean", 0.0),
                        agg.get("count", 0),
                    )
                    for agg in by_device
                ],
            )

            top_devices = summary.get("top_devices") or []
            conn.executemany(
                "INSERT INTO top_devices (rank, device, site, total, mean, count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        rank,
                        item.get("device"),
                        item.get("site", ""),
                        item.get("total", 0.0),
                        item.get("mean", 0.0),
                        item.get("count", 0),
                    )
                    for rank, item in enumerate(top_devices, start=1)
                ],
            )
    finally:
        conn.close()


def _load_records(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description="Store records and summary in SQLite.")
    ap.add_argument("--records", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    records = _load_records(args.records)
    with open(args.summary, "r", encoding="utf-8") as fh:
        summary = json.load(fh)

    store(records, summary, args.db)


if __name__ == "__main__":
    main()
