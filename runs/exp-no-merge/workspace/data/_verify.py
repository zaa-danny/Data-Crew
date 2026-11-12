"""Independent verification of analyze/store output.

Recomputes every summary field from data/records.jsonl with a separate, deliberately
naive implementation (no import of pipeline.analyze), compares field by field, checks
ordering rules, checks app.db contents, and prints a hand-checked example.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath("."))

summary = json.load(open("data/summary.json", encoding="utf-8"))
recs = [json.loads(l) for l in open("data/records.jsonl", encoding="utf-8") if l.strip()]
params = json.load(open("params.json", encoding="utf-8"))

mv = float(params["min_value"])
rd = int(params["round_digits"])
tk = int(params["top_k"])

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}: got {got!r} want {want!r}")
    print(f"  {name}: {'OK' if got == want else 'MISMATCH'} (got={got!r})")


# ---- independent recomputation (naive loops, no shared helpers) ----
kept = []
for r in recs:
    if r["value"] >= mv:
        kept.append(r)

exp_total = round(sum(r["value"] for r in kept), rd)
exp_mean = round(exp_total / len(kept), rd) if kept else 0.0

site_totals = {}
site_counts = {}
site_devs = {}
for r in kept:
    site_totals[r["site"]] = site_totals.get(r["site"], 0.0) + r["value"]
    site_counts[r["site"]] = site_counts.get(r["site"], 0) + 1
    site_devs.setdefault(r["site"], set()).add(r["device"])

exp_by_site = []
for s in site_totals:
    st = round(site_totals[s], rd)
    exp_by_site.append({
        "site": s,
        "total": st,
        "mean": round(st / site_counts[s], rd),
        "device_count": len(site_devs[s]),
        "kept_count": site_counts[s],
    })
exp_by_site.sort(key=lambda d: (-d["total"], d["site"]))

dev_totals = {}
dev_counts = {}
for r in kept:
    k = (r["site"], r["device"])
    dev_totals[k] = dev_totals.get(k, 0.0) + r["value"]
    dev_counts[k] = dev_counts.get(k, 0) + 1
exp_devs = []
for (s, d) in dev_totals:
    dt = round(dev_totals[(s, d)], rd)
    exp_devs.append({"site": s, "device": d, "total": dt,
                     "mean": round(dt / dev_counts[(s, d)], rd)})
exp_devs.sort(key=lambda d: (-d["total"], d["site"], d["device"]))
exp_top = exp_devs[:tk]

exp_max = exp_min = None
if kept:
    hi = max(r["value"] for r in kept)
    lo = min(r["value"] for r in kept)
    hi_c = sorted([r for r in kept if r["value"] == hi], key=lambda r: (r["site"], r["device"], r["day"]))[0]
    lo_c = sorted([r for r in kept if r["value"] == lo], key=lambda r: (r["site"], r["device"], r["day"]))[0]
    exp_max = {"site": hi_c["site"], "device": hi_c["device"], "day": hi_c["day"], "value": float(hi_c["value"])}
    exp_min = {"site": lo_c["site"], "device": lo_c["device"], "day": lo_c["day"], "value": float(lo_c["value"])}

print("== field-by-field vs independent recomputation ==")
check("unit", summary["unit"], params["unit"])
check("record_count", summary["record_count"], len(recs))
check("kept_count", summary["kept_count"], len(kept))
check("site_count", summary["site_count"], len({r["site"] for r in kept}))
check("device_count", summary["device_count"], len({(r["site"], r["device"]) for r in kept}))
check("min_value", summary["min_value"], mv)
check("round_digits", summary["round_digits"], rd)
check("top_k", summary["top_k"], tk)
check("total", summary["total"], exp_total)
check("mean", summary["mean"], exp_mean)
check("max", summary["max"], exp_max)
check("min", summary["min"], exp_min)
check("by_site", summary["by_site"], exp_by_site)
check("top_devices", summary["top_devices"], exp_top)

print("== ordering rules ==")
bs = summary["by_site"]
check("by_site sorted total desc, site asc", bs, sorted(bs, key=lambda d: (-d["total"], d["site"])))
td = summary["top_devices"]
check("top_devices sorted total desc, site asc, device asc", td,
      sorted(td, key=lambda d: (-d["total"], d["site"], d["device"])))
check("top_devices length <= top_k", len(td) <= tk, True)
check("by_site omits empty sites", all(d["kept_count"] > 0 for d in bs), True)

print("== hand-checked example ==")
if bs:
    s0 = bs[0]["site"]
    raw = [r["value"] for r in kept if r["site"] == s0]
    print(f"  site {s0!r}: {len(raw)} kept values, raw sum={sum(raw)!r}, "
          f"rounded={round(sum(raw), rd)!r}, summary total={bs[0]['total']!r}")
    check(f"hand sum for {s0}", bs[0]["total"], round(sum(raw), rd))
    check(f"hand mean for {s0}", bs[0]["mean"], round(round(sum(raw), rd) / len(raw), rd))

print("== app.db ==")
conn = sqlite3.connect("data/app.db")
cur = conn.cursor()
n = cur.execute("SELECT COUNT(*) FROM records").fetchone()[0]
check("db records row count", n, len(recs))
kv = dict(cur.execute("SELECT key, value FROM summary").fetchall())
print("  db summary keys:", sorted(kv))
for k in ("total", "mean", "record_count", "kept_count", "site_count", "device_count"):
    check(f"db summary has {k}", k in kv, True)
check("db total", float(kv["total"]), summary["total"])
check("db mean", float(kv["mean"]), summary["mean"])
check("db record_count", int(kv["record_count"]), summary["record_count"])
check("db kept_count", int(kv["kept_count"]), summary["kept_count"])
# every record present with exact value
db_rows = cur.execute("SELECT site, device, day, value FROM records").fetchall()
check("db rows match input set",
      sorted(db_rows), sorted((r["site"], r["device"], r["day"], float(r["value"])) for r in recs))
conn.close()

print()
if failures:
    print("FAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
