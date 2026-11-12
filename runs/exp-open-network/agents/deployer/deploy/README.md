# Deploy

Standard-library only, Python >= 3.10. Run all commands from the repository root.

## Start the server

```
python server/app.py --port 8000
```

The server serves the JSON API under `/api/` and the static site from `site/`
(`/`, `/app.js`, `/style.css`, `/data.json`). Logs go to stderr.

## Health check

```
curl http://127.0.0.1:8000/api/health
```

Expected response:

```json
{"ok": true}
```

## Run the tests

```
python -m unittest discover -s tests -q
```

## Data layout

| Path                 | Contents                                                        |
| -------------------- | --------------------------------------------------------------- |
| `data/records.jsonl` | One JSON record per line: `site`, `device`, `day`, `value`.     |
| `data/summary.json`  | Aggregated summary (totals, means, by-site, top devices, min/max). |
| `data/app.db`        | SQLite copy: `records` table plus a `summary` key/value table.  |
| `site/data.json`     | Summary data consumed by the static front end.                  |

Regenerate the derived files (from the repo root):

```
python pipeline/collect.py --spec spec.json --out data/records.jsonl
python pipeline/analyze.py --records data/records.jsonl --params params.json --out data/summary.json
python pipeline/store.py --records data/records.jsonl --summary data/summary.json --db data/app.db
```
