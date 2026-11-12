# Deploy

## Start

```bash
python server/app.py --port 8000
```

The server listens on `127.0.0.1:8000` by default.

## Health check

```bash
curl http://127.0.0.1:8000/api/health
```

Expected response:

```json
{"ok": true}
```

## Tests

```bash
python -m unittest discover -s tests -q
```

## Data layout

| Path | Description |
| --- | --- |
| `data/records.jsonl` | Input records, one JSON object per line |
| `data/summary.json` | Aggregated summary (unit `count`, min value 5.0, top 2, 2 decimal places) |
| `data/app.db` | SQLite database built from the records |
| `site/data.json` | JSON payload consumed by the static site |

Requirements: Python >= 3.10, standard library only.
