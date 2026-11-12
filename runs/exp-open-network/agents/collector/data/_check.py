import json
import os
import subprocess
import sys
from collections import Counter

rows = [json.loads(l) for l in open("data/records.jsonl", encoding="utf-8") if l.strip()]
print("records:", len(rows))
print("sites:", sorted({r["site"] for r in rows}))
print("per site:", dict(Counter(r["site"] for r in rows)))
print("sample:", rows[0])
assert all(set(r) == {"site", "device", "day", "value"} for r in rows)
assert all(isinstance(r["value"], float) for r in rows)

# missing parent directory must be created
out = os.path.join("data", "_tmp_nested", "deep", "records.jsonl")
if os.path.exists(os.path.join("data", "_tmp_nested")):
    import shutil
    shutil.rmtree(os.path.join("data", "_tmp_nested"))
p = subprocess.run([sys.executable, "pipeline/collect.py", "--spec", "spec.json", "--out", out],
                   capture_output=True, text=True)
print("nested out rc:", p.returncode, p.stdout.strip(), p.stderr.strip()[:200])
assert p.returncode == 0 and os.path.exists(out)
n = sum(1 for l in open(out, encoding="utf-8") if l.strip())
print("nested records:", n)
assert n == len(rows)

# loud failure: bad page (no h1[data-site])
bad = {"kind": "http", "pages": ["http://127.0.0.1:8000/sites/does-not-exist.html"]}
sys.path.insert(0, ".")
from pipeline.collect import collect, CollectError
try:
    collect(bad)
except CollectError as exc:
    print("loud error OK:", str(exc)[:120])
else:
    raise AssertionError("expected CollectError")
print("ALL CHECKS PASSED")
