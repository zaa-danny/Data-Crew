"""Generate site/data.json from data/summary.json and data/records.jsonl."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
summary_path = ROOT / "data" / "summary.json"
records_path = ROOT / "data" / "records.jsonl"
out_path = ROOT / "site" / "data.json"

summary = json.loads(summary_path.read_text(encoding="utf-8"))

records = []
with records_path.open(encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line:
            records.append(json.loads(line))

payload = {"summary": summary, "records": records}
out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", out_path, "records:", len(records))
print("first record keys:", sorted(records[0].keys()) if records else None)
