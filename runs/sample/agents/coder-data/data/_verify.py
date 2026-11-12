"""Independent verification of data/summary.json and data/app.db.

Recomputes every summary field from data/records.jsonl + params.json using
plain loops (no import of pipeline.analyze) and prints one evidence line per
check, including ordering and tie-break details.
"""
import json
import sqlite3
import subprocess
import sys

params = json.load(open('params.json', encoding='utf-8'))
records = [json.loads(l) for l in open('data/records.jsonl', encoding='utf-8') if l.strip()]
summary = json.load(open('data/summary.json', encoding='utf-8'))
rd = params['round_digits']
mv = params['min_value']
tk = params['top_k']

kept = [r for r in records if r['value'] >= mv]
errs = []


def chk(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    if not cond:
        errs.append(msg)


# ---- scalars -------------------------------------------------------------
chk(summary['record_count'] == len(records),
    f"record_count={summary['record_count']} (all records={len(records)})")
chk(summary['kept_count'] == len(kept),
    f"kept_count={summary['kept_count']} (value>={mv} -> {len(kept)})")
chk(summary['site_count'] == len({r['site'] for r in kept}),
    f"site_count={summary['site_count']} (distinct kept sites={len({r['site'] for r in kept})})")
chk(summary['device_count'] == len({(r['site'], r['device']) for r in kept}),
    f"device_count={summary['device_count']} (distinct kept (site,device)={len({(r['site'], r['device']) for r in kept})})")
tot = round(sum(r['value'] for r in kept), rd)
chk(summary['total'] == tot, f"total={summary['total']} (raw sum rounded once={tot})")
exp_mean = round(tot / len(kept), rd) if kept else 0.0
chk(summary['mean'] == exp_mean, f"mean={summary['mean']} (rounded total/kept={exp_mean})")
chk(summary['unit'] == params['unit'] and summary['min_value'] == mv
    and summary['round_digits'] == rd and summary['top_k'] == tk,
    f"params echoed: unit={summary['unit']} min_value={summary['min_value']} "
    f"round_digits={summary['round_digits']} top_k={summary['top_k']}")

# ---- by_site -------------------------------------------------------------
site_vals = {}
for r in kept:
    site_vals.setdefault(r['site'], []).append(r['value'])
exp_sites = []
for s, vs in site_vals.items():
    st = round(sum(vs), rd)
    exp_sites.append({
        'site': s,
        'total': st,
        'mean': round(st / len(vs), rd),
        'device_count': len({r['device'] for r in kept if r['site'] == s}),
        'kept_count': len(vs),
    })
exp_sites.sort(key=lambda x: (-x['total'], x['site']))
chk(summary['by_site'] == exp_sites,
    f"by_site recomputed+ordered(total desc, site asc)={[(r['site'], r['total']) for r in exp_sites]}")
chk([r['site'] for r in summary['by_site']] == [r['site'] for r in exp_sites],
    f"by_site order={[r['site'] for r in summary['by_site']]}")
chk(all(summary['by_site'][i]['total'] >= summary['by_site'][i + 1]['total']
        for i in range(len(summary['by_site']) - 1)),
    "by_site totals non-increasing")

# ---- top_devices ---------------------------------------------------------
dev_vals = {}
for r in kept:
    dev_vals.setdefault((r['site'], r['device']), []).append(r['value'])
exp_devs = []
for (s, d), vs in dev_vals.items():
    dt = round(sum(vs), rd)
    exp_devs.append({'site': s, 'device': d, 'total': dt, 'mean': round(dt / len(vs), rd)})
exp_devs.sort(key=lambda x: (-x['total'], x['site'], x['device']))
chk(summary['top_devices'] == exp_devs[:tk],
    f"top_devices recomputed+ordered(total desc, site asc, device asc) top{tk}="
    f"{[(r['site'], r['device'], r['total']) for r in exp_devs[:tk]]}")
chk(len(summary['top_devices']) == min(tk, len(exp_devs)),
    f"top_devices length={len(summary['top_devices'])} (min(top_k, devices)={min(tk, len(exp_devs))})")
chk(all(summary['top_devices'][i]['total'] >= summary['top_devices'][i + 1]['total']
        for i in range(len(summary['top_devices']) - 1)),
    "top_devices totals non-increasing")

# ---- max / min with tie-break -------------------------------------------
def extreme(largest):
    if not kept:
        return None
    best = min(kept, key=lambda r: ((-r['value']) if largest else r['value'],
                                    r['site'], r['device'], r['day']))
    return {k: best[k] for k in ('site', 'device', 'day', 'value')}


exp_max = extreme(True)
exp_min = extreme(False)
chk(summary['max'] == exp_max,
    f"max={summary['max']} (expected {exp_max}; ties -> smallest (site,device,day))")
chk(summary['min'] == exp_min,
    f"min={summary['min']} (expected {exp_min}; ties -> smallest (site,device,day))")
max_ties = [r for r in kept if r['value'] == exp_max['value']] if exp_max else []
min_ties = [r for r in kept if r['value'] == exp_min['value']] if exp_min else []
print(f"INFO max value {exp_max['value'] if exp_max else None} occurs {len(max_ties)}x: "
      f"{[(r['site'], r['device'], r['day']) for r in max_ties]}")
print(f"INFO min value {exp_min['value'] if exp_min else None} occurs {len(min_ties)}x: "
      f"{[(r['site'], r['device'], r['day']) for r in min_ties]}")

# ---- database ------------------------------------------------------------
conn = sqlite3.connect('data/app.db')
n = conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]
chk(n == len(records), f"db records rows={n} (all records={len(records)})")
db_rows = conn.execute('SELECT site, device, day, value FROM records').fetchall()
chk(sorted(db_rows) == sorted((r['site'], r['device'], r['day'], r['value']) for r in records),
    "db records content matches records.jsonl exactly")
kv = dict(conn.execute('SELECT key, value FROM summary').fetchall())
print(f"INFO db summary keys={sorted(kv)}")
for k in ('total', 'mean', 'record_count'):
    chk(k in kv, f"db summary has key {k}")
chk(float(kv['total']) == summary['total'], f"db total={kv['total']} == summary total")
chk(float(kv['mean']) == summary['mean'], f"db mean={kv['mean']} == summary mean")
chk(int(kv['record_count']) == summary['record_count'],
    f"db record_count={kv['record_count']} == summary record_count")
conn.close()

# ---- idempotency ---------------------------------------------------------
subprocess.run([sys.executable, 'pipeline/store.py', '--records', 'data/records.jsonl',
                '--summary', 'data/summary.json', '--db', 'data/app.db'], check=True)
conn2 = sqlite3.connect('data/app.db')
n2 = conn2.execute('SELECT COUNT(*) FROM records').fetchone()[0]
k2 = conn2.execute('SELECT COUNT(*) FROM summary').fetchone()[0]
chk(n2 == len(records), f"idempotent re-run: records rows still {n2}")
chk(k2 == len(kv), f"idempotent re-run: summary rows still {k2}")
conn2.close()

print('ERRORS:', errs if errs else 'none')
print('record_count', summary['record_count'], 'kept_count', summary['kept_count'], 'total', summary['total'])
