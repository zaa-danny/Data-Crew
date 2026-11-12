import json, sqlite3, itertools

recs = [json.loads(l) for l in open('data/records.jsonl') if l.strip()]
params = json.load(open('params.json'))
summary = json.load(open('data/summary.json'))

mv = params['min_value']; rd = params['round_digits']; tk = params['top_k']
kept = [r for r in recs if r['value'] >= mv]

# independent recompute
raw_total = sum(r['value'] for r in kept)
total = round(raw_total, rd)
mean = round(total / len(kept), rd) if kept else 0.0
print('record_count', len(recs), summary['record_count'])
print('kept_count', len(kept), summary['kept_count'])
print('total', total, summary['total'])
print('mean', mean, summary['mean'])
assert len(recs) == summary['record_count']
assert len(kept) == summary['kept_count']
assert total == summary['total']
assert mean == summary['mean']

# by_site check
sites = {}
for r in kept:
    s = sites.setdefault(r['site'], {'raw': 0.0, 'n': 0, 'dev': set()})
    s['raw'] += r['value']; s['n'] += 1; s['dev'].add(r['device'])
exp = [{'site': k, 'total': round(v['raw'], rd), 'mean': round(round(v['raw'], rd)/v['n'], rd),
        'device_count': len(v['dev']), 'kept_count': v['n']} for k, v in sites.items()]
exp.sort(key=lambda d: (-d['total'], d['site']))
assert exp == summary['by_site'], (exp, summary['by_site'])
print('by_site OK')

# top_devices check
devs = {}
for r in kept:
    k = (r['site'], r['device'])
    d = devs.setdefault(k, {'raw': 0.0, 'n': 0})
    d['raw'] += r['value']; d['n'] += 1
rows = [{'site': k[0], 'device': k[1], 'total': round(v['raw'], rd),
         'mean': round(round(v['raw'], rd)/v['n'], rd)} for k, v in devs.items()]
rows.sort(key=lambda d: (-d['total'], d['site'], d['device']))
assert rows[:tk] == summary['top_devices'], (rows[:tk], summary['top_devices'])
print('top_devices OK')

# max/min check
def pick(fn):
    best = None
    for r in kept:
        if best is None or fn(r['value'], best['value']) or (r['value'] == best['value'] and (r['site'], r['device'], r['day']) < (best['site'], best['device'], best['day'])):
            best = r
    return best
assert pick(lambda a, b: a > b) == summary['max']
assert pick(lambda a, b: a < b) == summary['min']
print('max/min OK')

# db checks
conn = sqlite3.connect('data/app.db')
cur = conn.cursor()
n = cur.execute('SELECT COUNT(*) FROM records').fetchone()[0]
print('db records', n, 'expected', len(recs))
assert n == len(recs)
keys = dict(cur.execute('SELECT key, value FROM summary').fetchall())
for k in ('total', 'mean', 'record_count', 'kept_count', 'site_count', 'device_count', 'unit', 'min_value', 'round_digits', 'top_k'):
    assert k in keys, k
assert float(keys['total']) == summary['total']
assert int(keys['record_count']) == len(recs)
print('db summary keys OK')
conn.close()
print('ALL CHECKS PASSED')
