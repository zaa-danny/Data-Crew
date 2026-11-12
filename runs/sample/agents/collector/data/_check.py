import json
from collections import Counter

lines = [l for l in open("data/records.jsonl", encoding="utf-8") if l.strip()]
recs = [json.loads(l) for l in lines]
sites = Counter(r["site"] for r in recs)
print("records:", len(recs))
print("sites:", dict(sites))
print("sample:", recs[0] if recs else None)
