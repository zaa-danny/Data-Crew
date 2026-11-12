import json
rs = [json.loads(l) for l in open('data/records.jsonl', encoding='utf-8') if l.strip()]
print(len(rs))
print(rs[:3])
print(sorted({r['site'] for r in rs}))
print(sorted({(r['site'], r['device']) for r in rs}))
