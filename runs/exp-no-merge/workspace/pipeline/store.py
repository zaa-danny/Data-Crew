"""Store records and summary into SQLite (stdlib only, idempotent).

Creates/refreshes two tables in the target database:
  * records(site TEXT, device TEXT, day TEXT, value REAL, PRIMARY KEY(site, device, day))
    holding ALL records (no filtering, no rounding);
  * summary(key TEXT PRIMARY KEY, value TEXT) holding every top-level summary key
    (scalars as text, nested lists/dicts as JSON text).
Both tables are DROPped before CREATE so repeated runs are idempotent. The parent
directory of the database path is created when missing.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3


def _load_jsonl(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


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
            [
                (r["site"], r["device"], r["day"], float(r["value"]))
                for r in records
            ],
        )
        cur.execute("DROP TABLE IF EXISTS summary")
        cur.execute("CREATE TABLE summary (key TEXT PRIMARY KEY, value TEXT)")
        cur.executemany(
            "INSERT OR REPLACE INTO summary (key, value) VALUES (?, ?)",
            [(str(k), _scalar_text(v)) for k, v in summary.items()],
        )
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Store records and summary into app.db")
    ap.add_argument("--records", default="data/records.jsonl")
    ap.add_argument("--summary", default="data/summary.json")
    ap.add_argument("--db", default="data/app.db")
    args = ap.parse_args(argv)

    records = _load_jsonl(args.records)
    with open(args.summary, "r", encoding="utf-8") as fh:
        summary = json.load(fh)
    store(records, summary, args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
