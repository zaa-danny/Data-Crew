import json, os, sqlite3
from pathlib import Path
root = Path(__file__).resolve().parents[1]
print("ROOT", root)
print("data exists", (root/"data").exists())
print("site exists", (root/"site").exists())
for p in sorted((root/"data").iterdir()):
    print("DATA", p.name, p.stat().st_size)
print("--- summary.json ---")
print((root/"data"/"summary.json").read_text(encoding="utf-8")[:2000])
print("--- records head ---")
lines = (root/"data"/"records.jsonl").read_text(encoding="utf-8").splitlines()
print("count", len(lines))
for l in lines[:3]:
    print(l)
print("--- db ---")
c = sqlite3.connect(root/"data"/"app.db")
print(c.execute("select name from sqlite_master").fetchall())
