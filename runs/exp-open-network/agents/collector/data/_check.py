import json
from collections import Counter

lines = 0
sites = Counter()
with open("data/records.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        lines += 1
        sites[rec["site"]] += 1
print("records:", lines)
print("sites:", dict(sites))
