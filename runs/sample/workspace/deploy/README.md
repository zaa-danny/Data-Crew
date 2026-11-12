# dataapp — deploy notes

Stdlib only, Python >= 3.10. Run everything from the repository root.

## Start the server

```
python server/app.py --port 8000
```

The server serves the API and the static site from `site/` on the same port
(`/` -> `site/index.html`). Use `--port 0` to let the OS pick a free port; the
server then prints exactly one line `LISTENING <port>` to stdout.

## Health check

```
curl http://127.0.0.1:8000/api/health
```

Expected response:

```json
{"ok": true}
```

Other endpoints: `GET /api/summary`, `GET /api/records?site=<site>&limit=<n>`.

## Run the tests

```
python -m unittest discover -s tests -q
```

## Data layout

| Path                 | Contents                                                        |
| -------------------- | --------------------------------------------------------------- |
| `data/records.jsonl` | One JSON record per line (collected site data).                 |
| `data/summary.json`  | Analysis summary (params: unit=count, min_value=5.0, top_k=2, round_digits=2). |
| `data/app.db`        | SQLite database written by the store step.                      |
| `site/data.json`     | Summary snapshot consumed by the front end (`/data.json`).      |
