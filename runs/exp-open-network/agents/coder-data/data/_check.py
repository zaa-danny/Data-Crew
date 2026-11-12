import json, sqlite3, sys

params = json.load(open("params.json", encoding="utf-8"))
records = [json.loads(l) for l in open("data/records.jsonl", encoding="utf-8") if l.strip()]
summary = json.load(open("data/summary.json", encoding="utf-8"))

mv, rd, tk = params["min_value"], params["round_digits"], params["top_k"]
kept = [r for r in records if r["value"] >= mv]

# independent recompute
raw = sum(r["value"] for r in kept)
total = round(raw, rd)
mean = round(total / len(kept), rd) if kept else 0.0
assert summary["record_count"] == len(records), summary["record_count"]
assert summary["kept_count"] == len(kept)
assert summary["total"] == total, (summary["total"], total)
assert summary["mean"] == mean, (summary["mean"], mean)
assert summary["site_count"] == len({r["site"] for r in kept})
assert summary["device_count"] == len({(r["site"], r["device"]) for r in kept})
assert summary["unit"] == params["unit"]
assert summary["min_value"] == mv and summary["round_digits"] == rd and summary["top_k"] == tk

# by_site ordering
bs = summary["by_site"]
assert bs == sorted(bs, key=lambda d: (-d["total"], d["site"])), "by_site order"
assert {d["site"] for d in bs} == {r["site"] for r in kept}
for d in bs:
    sub = [r for r in kept if r["site"] == d["site"]]
    assert d["total"] == round(sum(r["value"] for r in sub), rd)
    assert d["mean"] == round(d["total"] / len(sub), rd)
    assert d["kept_count"] == len(sub)
    assert d["device_count"] == len({r["device"] for r in sub})

# top_devices
td = summary["top_devices"]
assert len(td) <= tk
assert td == sorted(td, key=lambda d: (-d["total"], d["site"], d["device"])), "top_devices order"
all_dev = {}
for r in kept:
    k = (r["site"], r["device"])
    all_dev.setdefault(k, []).append(r["value"])
expected = sorted(
    ({"site": s, "device": dv, "total": round(sum(v), rd), "mean": round(sum(v) / len(v), rd)}
     for (s, dv), v in all_dev.items()),
    key=lambda d: (-d["total"], d["site"], d["device"]),
)[:tk]
assert td == expected, (td, expected)

# max/min
def extreme(largest):
    best = None
    for r in kept:
        k = (-r["value"] if largest else r["value"], r["site"], r["device"], r["day"])
        if best is None or k < best[0]:
            best = (k, r)
    return {kk: best[1][kk] for kk in ("site", "device", "day", "value")}
assert summary["max"] == extreme(True), summary["max"]
assert summary["min"] == extreme(False), summary["min"]

# db checks
conn = sqlite3.connect("data/app.db")
cur = conn.cursor()
n = cur.execute("SELECT COUNT(*) FROM records").fetchone()[0]
assert n == len(records), n
kv = dict(cur.execute("SELECT key, value FROM summary").fetchall())
for k in ("total", "mean", "record_count"):
    assert k in kv, k
assert float(kv["total"]) == total
assert float(kv["mean"]) == mean
assert int(kv["record_count"]) == len(records)
# idempotency: rerun store
conn.close()
print("ALL CHECKS PASSED")
print("record_count=%d kept_count=%d total=%s mean=%s" % (summary["record_count"], summary["kept_count"], summary["total"], summary["mean"]))
print("by_site:", [(d["site"], d["total"]) for d in bs])
print("top_devices:", [(d["site"], d["device"], d["total"]) for d in td])
