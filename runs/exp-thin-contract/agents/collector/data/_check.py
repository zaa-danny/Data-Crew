import json
from collections import Counter

path = "data/records.jsonl"
rows = []
with open(path, "r", encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line:
            rows.append(json.loads(line))

print("records:", len(rows))
print("sites:", sorted({r["site"] for r in rows}))
print("per-site:", dict(Counter(r["site"] for r in rows)))
print("devices:", len({r["device"] for r in rows}))
vals = [r["value"] for r in rows]
print("total:", round(sum(vals), 2), "mean:", round(sum(vals) / len(vals), 2))
print("max:", max(rows, key=lambda r: r["value"]))
print("min:", min(rows, key=lambda r: r["value"]))
