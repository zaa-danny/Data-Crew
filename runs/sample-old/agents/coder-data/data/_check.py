import json, sqlite3

summary = json.load(open("data/summary.json"))
records = [json.loads(l) for l in open("data/records.jsonl") if l.strip()]
params = json.load(open("params.json"))

kept = [r for r in records if r["value"] >= params["min_value"]]
raw_total = sum(r["value"] for r in kept)
total = round(raw_total, params["round_digits"])
mean = round(total / len(kept), params["round_digits"]) if kept else 0.0

assert summary["record_count"] == len(records), summary["record_count"]
assert summary["kept_count"] == len(kept)
assert summary["total"] == total, (summary["total"], total)
assert summary["mean"] == mean, (summary["mean"], mean)
assert summary["site_count"] == len({r["site"] for r in kept})
assert summary["device_count"] == len({(r["site"], r["device"]) for r in kept})

bs = summary["by_site"]
for a, b in zip(bs, bs[1:]):
    assert (-a["total"], a["site"]) <= (-b["total"], b["site"]), (a, b)
td = summary["top_devices"]
assert len(td) <= params["top_k"]
for a, b in zip(td, td[1:]):
    assert (-a["total"], a["site"], a["device"]) <= (-b["total"], b["site"], b["device"])

conn = sqlite3.connect("data/app.db")
n = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
assert n == len(records), (n, len(records))

# summary table must be a proper table with total/mean/record_count columns
cols = [r[1] for r in conn.execute("PRAGMA table_info(summary)")]
for c in ("total", "mean", "record_count"):
    assert c in cols, (c, cols)
rows = conn.execute("SELECT COUNT(*) FROM summary").fetchone()[0]
assert rows == 1, rows
db_total, db_mean, db_rc = conn.execute(
    "SELECT total, mean, record_count FROM summary"
).fetchone()
assert db_total == total, (db_total, total)
assert db_mean == mean, (db_mean, mean)
assert db_rc == len(records), (db_rc, len(records))
conn.close()

print("record_count", summary["record_count"])
print("kept_count", summary["kept_count"])
print("total", summary["total"])
print("mean", summary["mean"])
print("summary cols", cols)
print("ALL CHECKS PASSED")
