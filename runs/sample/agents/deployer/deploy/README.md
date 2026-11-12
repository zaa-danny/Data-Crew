# Deploy

Standard-library only, Python >= 3.10. Run everything from the repository root.

## Start the server

```
python server/app.py --port 8000
```

The server serves the API and the static site from `site/` on
`http://127.0.0.1:8000/`. Logs go to stderr; with `--port 0` the OS picks a
free port and exactly one line `LISTENING <port>` is printed to stdout.

## Health check

```
curl http://127.0.0.1:8000/api/health
```

Expected response:

```json
{"ok": true}
```

Other endpoints: `GET /api/summary` -> `{"summary": ...}`,
`GET /api/records?site=X&limit=N` -> `{"records": [...]}`.

## Run the tests

```
python -m unittest discover -s tests -q
```

## Data layout

| Path                 | Contents                                                        |
| -------------------- | --------------------------------------------------------------- |
| `data/records.jsonl` | One JSON record per line: `site`, `device`, `day`, `value`.     |
| `data/summary.json`  | Analyzer output: totals, means, `by_site`, `top_devices`, etc.  |
| `data/app.db`        | SQLite DB with `records` and `summary` tables.                  |
| `site/data.json`     | Summary copy served to the browser at `/data.json`.             |

## Regenerating the data

```
python pipeline/collect.py --spec spec.json --out data/records.jsonl
python pipeline/analyze.py --records data/records.jsonl --params params.json --out data/summary.json
python pipeline/store.py --records data/records.jsonl --summary data/summary.json --db data/app.db
```

`params.json` holds `unit`, `min_value`, `top_k` and `round_digits`; the
analyzer reads them at run time.
