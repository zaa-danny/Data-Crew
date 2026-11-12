"""Store records and summary into a SQLite database (idempotent)."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3


def _scalar_text(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def store(records: list[dict], summary: dict, db_path: str) -> None:
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS records")
        cur.execute(
            "CREATE TABLE records ("
            "site TEXT NOT NULL, device TEXT NOT NULL, day TEXT NOT NULL, "
            "value REAL NOT NULL, PRIMARY KEY (site, device, day))"
        )
        cur.executemany(
            "INSERT OR REPLACE INTO records (site, device, day, value) VALUES (?, ?, ?, ?)",
            [(r["site"], r["device"], r["day"], float(r["value"])) for r in records],
        )
        cur.execute("DROP TABLE IF EXISTS summary")
        cur.execute("CREATE TABLE summary (key TEXT PRIMARY KEY, value TEXT)")
        rows = []
        for key, value in summary.items():
            if isinstance(value, (dict, list)):
                continue
            rows.append((key, _scalar_text(value)))
        cur.executemany(
            "INSERT OR REPLACE INTO summary (key, value) VALUES (?, ?)", rows
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", default="data/records.jsonl")
    ap.add_argument("--summary", default="data/summary.json")
    ap.add_argument("--db", default="data/app.db")
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
    print("wrote %d records and summary to %s" % (len(records), args.db))


if __name__ == "__main__":
    main()
