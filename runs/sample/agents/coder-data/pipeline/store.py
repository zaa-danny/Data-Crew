"""Store: persist all records and the summary scalars into SQLite.

Contract
--------
``store(records, summary, db_path)`` creates/refreshes ``db_path`` with:

* ``records(site TEXT, device TEXT, day TEXT, value REAL,
  PRIMARY KEY(site, device, day))`` holding ALL records (no filtering);
* ``summary(key TEXT PRIMARY KEY, value TEXT)`` holding every top-level
  scalar of the summary (``total``, ``mean``, ``record_count``, ...) as text.

The operation is idempotent: both tables are dropped before being recreated.

CLI: ``python pipeline/store.py --records data/records.jsonl
--summary data/summary.json --db data/app.db``
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3

_SCALAR_KEYS = (
    "unit",
    "record_count",
    "kept_count",
    "site_count",
    "device_count",
    "min_value",
    "round_digits",
    "top_k",
    "total",
    "mean",
)


def _as_text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if value is None:
        return ""
    return str(value)


def store(records: list[dict], summary: dict, db_path: str) -> None:
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute("DROP TABLE IF EXISTS records")
            conn.execute(
                "CREATE TABLE records ("
                "site TEXT NOT NULL, "
                "device TEXT NOT NULL, "
                "day TEXT NOT NULL, "
                "value REAL NOT NULL, "
                "PRIMARY KEY (site, device, day))"
            )
            conn.executemany(
                "INSERT OR REPLACE INTO records (site, device, day, value) "
                "VALUES (?, ?, ?, ?)",
                [
                    (rec["site"], rec["device"], rec["day"], float(rec["value"]))
                    for rec in records
                ],
            )

            conn.execute("DROP TABLE IF EXISTS summary")
            conn.execute(
                "CREATE TABLE summary (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            rows = []
            for key in _SCALAR_KEYS:
                if key in summary:
                    rows.append((key, _as_text(summary[key])))
            for key, value in summary.items():
                if key in _SCALAR_KEYS:
                    continue
                if isinstance(value, (dict, list)):
                    continue
                rows.append((key, _as_text(value)))
            conn.executemany(
                "INSERT OR REPLACE INTO summary (key, value) VALUES (?, ?)", rows
            )
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    records = []
    with open(args.records, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    with open(args.summary, "r", encoding="utf-8") as f:
        summary = json.load(f)

    store(records, summary, args.db)


if __name__ == "__main__":
    main()
