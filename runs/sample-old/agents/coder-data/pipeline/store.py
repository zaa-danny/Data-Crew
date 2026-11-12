"""Store: persist all records and summary scalars into SQLite app.db."""
from __future__ import annotations

import argparse
import json
import sqlite3


def store(records: list[dict], summary: dict, db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS records")
        cur.execute(
            "CREATE TABLE records ("
            "site TEXT, device TEXT, day TEXT, value REAL, "
            "PRIMARY KEY(site, device, day))"
        )
        cur.executemany(
            "INSERT OR REPLACE INTO records (site, device, day, value) VALUES (?, ?, ?, ?)",
            [(r["site"], r["device"], r["day"], r["value"]) for r in records],
        )

        # Proper summary table: one row with typed columns for the required
        # scalars (total, mean, record_count) plus the other top-level scalars.
        cur.execute("DROP TABLE IF EXISTS summary")
        cur.execute(
            "CREATE TABLE summary ("
            "unit TEXT, "
            "record_count INTEGER, "
            "kept_count INTEGER, "
            "site_count INTEGER, "
            "device_count INTEGER, "
            "min_value REAL, "
            "round_digits INTEGER, "
            "top_k INTEGER, "
            "total REAL, "
            "mean REAL)"
        )
        cur.execute(
            "INSERT INTO summary ("
            "unit, record_count, kept_count, site_count, device_count, "
            "min_value, round_digits, top_k, total, mean) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                summary.get("unit"),
                summary.get("record_count"),
                summary.get("kept_count"),
                summary.get("site_count"),
                summary.get("device_count"),
                summary.get("min_value"),
                summary.get("round_digits"),
                summary.get("top_k"),
                summary.get("total"),
                summary.get("mean"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _load_records(path: str) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()
    records = _load_records(args.records)
    with open(args.summary, "r", encoding="utf-8") as f:
        summary = json.load(f)
    store(records, summary, args.db)


if __name__ == "__main__":
    main()
