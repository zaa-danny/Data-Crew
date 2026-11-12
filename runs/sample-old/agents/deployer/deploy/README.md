# Deploy — dataapp-sample

Stdlib-only Python (>= 3.10). Run all commands from the repository root.

## Start the server

```bash
python server/app.py --port 8000
```

Use `--port 0` to let the OS pick a free port; the server then prints exactly one
line `LISTENING <port>` to stdout (logs go to stderr).

## Health check

```bash
curl http://127.0.0.1:8000/api/health
```

Expected response:

```json
{"ok": true}
```

Other endpoints: `GET /api/summary`, `GET /api/records?site=&limit=`, and the
static UI at `/` (`index.html`, `app.js`, `style.css`, `data.json`).

## Run the tests

```bash
python -m unittest discover -s tests -q
```

## Data layout

| Path                 | Contents                                                        |
| -------------------- | --------------------------------------------------------------- |
| `data/records.jsonl` | One JSON record per line: `site`, `device`, `day`, `value`.      |
| `data/summary.json`  | Analyzer output: totals, counts, `by_site`, `top_devices`, etc.  |
| `data/app.db`        | SQLite DB with `records` and `summary` tables (from `store`).    |
| `site/data.json`     | Summary copy served to the browser UI at `/data.json`.           |

Pipeline (regenerate data):

```bash
python pipeline/collect.py --spec spec.json --out data/records.jsonl
python pipeline/analyze.py --records data/records.jsonl --params params.json --out data/summary.json
python pipeline/store.py --records data/records.jsonl --summary data/summary.json --db data/app.db
```
