"""Store records and summary into a SQLite database.

Contract
--------
``store(records, summary, db_path)`` creates/refreshes ``db_path`` with:

* ``records(site TEXT, device TEXT, day TEXT, value REAL,
  PRIMARY KEY(site, device, day))`` holding ALL records (no filtering).
* ``summary(key TEXT PRIMARY KEY, value TEXT)`` holding every top-level
  scalar summary key (lists/dicts are stored as JSON text).

Idempotent: tables are dropped before being recreated.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any


def _to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def store(records: list[dict], summary: dict, db_path: str) -> None:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS records")
        cur.execute("DROP TABLE IF EXISTS summary")
        cur.execute(
            "CREATE TABLE records ("
            "site TEXT NOT NULL, "
            "device TEXT NOT NULL, "
            "day TEXT NOT NULL, "
            "value REAL NOT NULL, "
            "PRIMARY KEY (site, device, day))"
        )
        cur.execute(
            "CREATE TABLE summary (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        cur.executemany(
            "INSERT OR REPLACE INTO records (site, device, day, value) "
            "VALUES (?, ?, ?, ?)",
            [
                (str(r["site"]), str(r["device"]), str(r["day"]), float(r["value"]))
                for r in records
            ],
        )
        cur.executemany(
            "INSERT OR REPLACE INTO summary (key, value) VALUES (?, ?)",
            [(str(k), _to_text(v)) for k, v in summary.items()],
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    records: list[dict] = []
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
