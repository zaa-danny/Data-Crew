import json, sqlite3, sys

recs = [json.loads(l) for l in open("data/records.jsonl", encoding="utf-8") if l.strip()]
summary = json.load(open("data/summary.json", encoding="utf-8"))
params = json.load(open("params.json", encoding="utf-8"))

errors = []
checks = []

def check(name, got, want):
    ok = got == want
    checks.append((name, got, want, ok))
    if not ok:
        errors.append(name)

# ---- independent recomputation from raw records ----
mv, rd, tk = params["min_value"], params["round_digits"], params["top_k"]
kept = [r for r in recs if r["value"] >= mv]
raw_total = sum(r["value"] for r in kept)
total = round(raw_total, rd)
mean = round(total / len(kept), rd) if kept else 0.0

check("record_count", summary["record_count"], len(recs))
check("kept_count", summary["kept_count"], len(kept))
check("total", summary["total"], total)
check("mean", summary["mean"], mean)
check("site_count", summary["site_count"], len({r["site"] for r in kept}))
check("device_count", summary["device_count"], len({(r["site"], r["device"]) for r in kept}))
check("unit", summary["unit"], params["unit"])
check("min_value", summary["min_value"], mv)
check("round_digits", summary["round_digits"], rd)
check("top_k", summary["top_k"], tk)

# ---- by_site: recompute each site independently ----
exp_sites = {}
for r in kept:
    s = exp_sites.setdefault(r["site"], {"raw": 0.0, "n": 0, "dev": set()})
    s["raw"] += r["value"]; s["n"] += 1; s["dev"].add(r["device"])
exp_by_site = []
for site, s in exp_sites.items():
    st = round(s["raw"], rd)
    exp_by_site.append({"site": site, "total": st,
                        "mean": round(st / s["n"], rd),
                        "device_count": len(s["dev"]), "kept_count": s["n"]})
exp_by_site.sort(key=lambda d: (-d["total"], d["site"]))
check("by_site", summary["by_site"], exp_by_site)

# ---- top_devices: recompute each (site,device) independently ----
exp_dev = {}
for r in kept:
    k = (r["site"], r["device"])
    d = exp_dev.setdefault(k, {"raw": 0.0, "n": 0})
    d["raw"] += r["value"]; d["n"] += 1
exp_dev_rows = [{"site": s, "device": dv, "total": round(d["raw"], rd),
                 "mean": round(round(d["raw"], rd) / d["n"], rd)}
                for (s, dv), d in exp_dev.items()]
exp_dev_rows.sort(key=lambda d: (-d["total"], d["site"], d["device"]))
check("top_devices", summary["top_devices"], exp_dev_rows[:tk])

# ---- max / min with tie-break ----
if kept:
    mx = min(kept, key=lambda r: (-r["value"], r["site"], r["device"], r["day"]))
    mn = min(kept, key=lambda r: (r["value"], r["site"], r["device"], r["day"]))
    check("max", summary["max"], {k: mx[k] for k in ("site","device","day","value")})
    check("min", summary["min"], {k: mn[k] for k in ("site","device","day","value")})

# ---- db checks ----
conn = sqlite3.connect("data/app.db")
cur = conn.cursor()
check("db.records_count", cur.execute("SELECT COUNT(*) FROM records").fetchone()[0], len(recs))
check("db.records_sum", round(cur.execute("SELECT SUM(value) FROM records").fetchone()[0], rd),
      round(sum(r["value"] for r in recs), rd))
keys = {k: v for k, v in cur.execute("SELECT key, value FROM summary")}
for k in ("total", "mean", "record_count", "kept_count", "site_count", "device_count"):
    check(f"db.key.{k}", k in keys, True)
check("db.total", float(keys["total"]), total)
check("db.mean", float(keys["mean"]), mean)
check("db.record_count", int(keys["record_count"]), len(recs))
conn.close()

# ---- print full comparison table ----
for name, got, want, ok in checks:
    print(f"[{'OK ' if ok else 'BAD'}] {name}: got={got!r} want={want!r}")
print(f"\n{len(checks)} checks, {len(errors)} failures")
print("ERRORS:", errors if errors else "none")
sys.exit(1 if errors else 0)
