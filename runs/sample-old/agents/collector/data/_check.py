import json
from collections import Counter

path = "data/records.jsonl"
n = 0
sites = Counter()
with open(path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        n += 1
        sites[rec["site"]] += 1
print("records:", n)
print("sites:", dict(sites))
