import json
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import app  # noqa: E402

failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


# --- importable functions -------------------------------------------------
summary = app.load_summary()
check(isinstance(summary, dict) and summary.get("unit") == "count", "load_summary returns dict")

all_records = app.load_records()
check(len(all_records) == 44, f"load_records returns all 44 records (got {len(all_records)})")
keys = [(r["device"], r["day"]) for r in all_records]
check(keys == sorted(keys), "records sorted ascending by (device, day)")

sites = sorted({r["site"] for r in all_records})
print("sites:", sites)
real_site = sites[0]
filtered = app.load_records(site=real_site)
check(all(r["site"] == real_site for r in filtered) and len(filtered) > 0,
      f"site filter works for {real_site} ({len(filtered)} records)")
limited = app.load_records(site=real_site, limit=2)
check(len(limited) == 2 and limited == filtered[:2], "limit truncates after sort")

# --- live server via --port 0 --------------------------------------------
proc = subprocess.Popen(
    [sys.executable, str(ROOT / "server" / "app.py"), "--port", "0"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
try:
    line = proc.stdout.readline().strip()
    check(line.startswith("LISTENING "), f"stdout line: {line!r}")
    port = int(line.split()[1])
    base = f"http://127.0.0.1:{port}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=5) as resp:
            return resp.status, resp.read()

    status, body = get("/api/health")
    check(status == 200 and json.loads(body) == {"ok": True}, "/api/health -> {ok:true}")

    status, body = get("/api/summary")
    payload = json.loads(body)
    check(status == 200 and payload["summary"] == summary, "/api/summary matches file")

    status, body = get(f"/api/records?site={real_site}&limit=2")
    payload = json.loads(body)
    check(status == 200 and payload["records"] == limited, "/api/records site+limit matches")

    status, body = get("/api/records?limit=notanumber")
    payload = json.loads(body)
    check(status == 200 and len(payload["records"]) == 44, "invalid limit ignored")

    # static: report whether site/ exists and what GET / does
    site_dir = ROOT / "site"
    print("site/ exists:", site_dir.exists())
    try:
        status, body = get("/")
        check(status == 200, "GET / -> 200 (site/index.html present)")
    except urllib.error.HTTPError as exc:
        check(exc.code == 404, "GET / -> 404 (site/index.html missing)")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

# --- static serving against a TEMPORARY root ------------------------------
with tempfile.TemporaryDirectory() as tmp:
    tmp_root = Path(tmp)
    (tmp_root / "data").mkdir()
    (tmp_root / "data" / "summary.json").write_text(json.dumps({"unit": "count"}), encoding="utf-8")
    (tmp_root / "data" / "records.jsonl").write_text("", encoding="utf-8")
    site = tmp_root / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>TEMP INDEX</html>", encoding="utf-8")
    (site / "app.js").write_text("console.log('temp');", encoding="utf-8")
    (site / "style.css").write_text("body{color:red}", encoding="utf-8")
    (site / "data.json").write_text('{"x":1}', encoding="utf-8")

    httpd = app.make_server(tmp_root, 0)
    tport = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    tbase = f"http://127.0.0.1:{tport}"

    def tget(path):
        with urllib.request.urlopen(tbase + path, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()

    try:
        status, ctype, body = tget("/")
        check(status == 200 and b"TEMP INDEX" in body and "text/html" in ctype,
              "GET / serves temp index.html with text/html")

        status, ctype, body = tget("/app.js")
        check(status == 200 and b"temp" in body and "javascript" in ctype,
              "GET /app.js serves temp app.js with JS content-type")

        status, ctype, body = tget("/style.css")
        check(status == 200 and "text/css" in ctype, "GET /style.css -> text/css")

        status, ctype, body = tget("/data.json")
        check(status == 200 and "application/json" in ctype, "GET /data.json -> application/json")

        try:
            tget("/missing.html")
            check(False, "missing static file should 404")
        except urllib.error.HTTPError as exc:
            check(exc.code == 404, "missing static file -> 404")

        status, ctype, body = tget("/api/summary")
        check(json.loads(body)["summary"] == {"unit": "count"}, "temp root summary used")
    finally:
        httpd.shutdown()
        httpd.server_close()

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    sys.exit(1)
print("ALL CHECKS PASSED")
