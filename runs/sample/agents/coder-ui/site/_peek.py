import json

with open("data/records.jsonl", "r", encoding="utf-8") as f:
    lines = [ln for ln in f if ln.strip()]
print("lines:", len(lines))
print("first:", lines[0].strip())
print("last:", lines[-1].strip())
rec = json.loads(lines[0])
print("keys:", sorted(rec.keys()))
<<<
