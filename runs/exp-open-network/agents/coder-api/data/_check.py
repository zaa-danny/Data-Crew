"""Self-check for server/app.py: functions, live HTTP API, and static serving."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import app as appmod  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("PASS %s" % name)
    else:
        print("FAIL %s %s" % (name, detail))
        FAILS.append(name)


def get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


# --------------------------------------------------------------------- #
# 1. Importable functions
# --------------------------------------------------------------------- #
summary = appmod.load_summary()
check("load_summary returns dict", isinstance(summary, dict), type(summary))
check("load_summary record_count", summary.get("record_count") == 44, summary.get("record_count"))
check("load_summary total", summary.get("total") == 2005.8, summary.get("total"))

all_recs = appmod.load_records()
check("load_records all count", len(all_recs) == 44, len(all_recs))
check(
    "load_records sorted by (device, day)",
    all_recs == sorted(all_recs, key=lambda r: (r["device"], r["day"])),
)
check(
    "load_records unfiltered includes all sites",
    {r["site"] for r in all_recs} == {"center", "east", "south"},
)

center = appmod.load_records(site="center")
check("load_records site filter", len(center) == 20 and all(r["site"] == "center" for r in center), len(center))

limited = appmod.load_records(site="center", limit=2)
check("load_records limit=2", len(limited) == 2, len(limited))
check("load_records limit keeps first sorted", limited == center[:2], limited)

check("load_records invalid limit ignored", len(appmod.load_records(limit="abc")) == 44)
check("load_records negative limit ignored", len(appmod.load_records(limit=-3)) == 44)
check("load_records limit=0", appmod.load_records(limit=0) == [])
check("load_records unknown site", appmod.load_records(site="nope") == [])

# --------------------------------------------------------------------- #
# 2. Static serving against a TEMPORARY root
# --------------------------------------------------------------------- #
tmp = Path(tempfile.mkdtemp(prefix="apicheck_"))
(tmp / "site").mkdir()
(tmp / "site" / "index.html").write_text("<html><body>TEMP-INDEX</body></html>", encoding="utf-8")
(tmp / "site" / "app.js").write_text("console.log('temp');\n", encoding="utf-8")
(tmp / "site" / "style.css").write_text("body{color:red}\n", encoding="utf-8")
(tmp / "site" / "data.json").write_text('{"temp": true}\n', encoding="utf-8")

tmp_server = appmod.make_server(root=tmp, port=0)
tmp_port = tmp_server.server_address[1]
t = threading.Thread(target=tmp_server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
t.start()
try:
    base = "http://127.0.0.1:%d" % tmp_port
    st, ct, body = get(base + "/")
    check("temp GET / status", st == 200, st)
    check("temp GET / body", b"TEMP-INDEX" in body, body[:80])
    check("temp GET / content-type html", "text/html" in ct, ct)

    st, ct, body = get(base + "/app.js")
    check("temp GET /app.js", st == 200 and b"temp" in body, (st, body[:40]))
    check("temp GET /app.js content-type js", "javascript" in ct, ct)

    st, ct, body = get(base + "/style.css")
    check("temp GET /style.css", st == 200 and b"color:red" in body, (st, body[:40]))
    check("temp GET /style.css content-type css", "text/css" in ct, ct)

    st, ct, body = get(base + "/data.json")
    check("temp GET /data.json", st == 200 and json.loads(body) == {"temp": True}, (st, body[:40]))
    check("temp GET /data.json content-type json", "application/json" in ct, ct)

    st, ct, body = get(base + "/missing.html")
    check("temp GET missing -> 404", st == 404, st)

    # temp root has no data/ -> API should 404, not crash
    st, ct, body = get(base + "/api/summary")
    check("temp root /api/summary -> 404", st == 404, st)
finally:
    tmp_server.shutdown()
    tmp_server.server_close()
    t.join(timeout=5)

# --------------------------------------------------------------------- #
# 3. Real server via `python server/app.py --port 0`
# --------------------------------------------------------------------- #
proc = subprocess.Popen(
    [sys.executable, str(ROOT / "server" / "app.py"), "--port", "0"],
    cwd=str(ROOT),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
try:
    line = proc.stdout.readline().strip()
    check("LISTENING line format", line.startswith("LISTENING "), repr(line))
    port = int(line.split()[1])
    base = "http://127.0.0.1:%d" % port

    # wait for readiness
    for _ in range(100):
        try:
            st, _, _ = get(base + "/api/health", timeout=1)
            if st == 200:
                break
        except Exception:
            pass
        time.sleep(0.05)

    st, ct, body = get(base + "/api/health")
    check("GET /api/health status", st == 200, st)
    check("GET /api/health body", json.loads(body) == {"ok": True}, body[:80])
    check("GET /api/health content-type", "application/json" in ct, ct)

    st, ct, body = get(base + "/api/summary")
    payload = json.loads(body)
    check("GET /api/summary status", st == 200, st)
    check("GET /api/summary wraps summary", payload.get("summary") == summary, list(payload)[:3])

    st, ct, body = get(base + "/api/records?site=center&limit=2")
    payload = json.loads(body)
    recs = payload.get("records")
    check("GET /api/records status", st == 200, st)
    check("GET /api/records limit=2", isinstance(recs, list) and len(recs) == 2, recs)
    check("GET /api/records site filter", all(r["site"] == "center" for r in recs), recs)
    check("GET /api/records matches function", recs == appmod.load_records(site="center", limit=2), recs)

    st, ct, body = get(base + "/api/records")
    check("GET /api/records all", len(json.loads(body)["records"]) == 44, len(body))

    st, ct, body = get(base + "/api/records?limit=notanumber")
    check("GET /api/records invalid limit ignored", len(json.loads(body)["records"]) == 44, st)

    st, ct, body = get(base + "/api/nope")
    check("GET /api/nope -> 404", st == 404, st)

    # real site/ may not exist yet (another agent writes it) -> 404 is correct
    st, ct, body = get(base + "/")
    site_index = ROOT / "site" / "index.html"
    if site_index.exists():
        check("GET / real index served", st == 200 and body == site_index.read_bytes(), st)
    else:
        check("GET / 404 when site/index.html absent", st == 404, st)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    err = proc.stderr.read()
    check("logs went to stderr", "serving root=" in err, err[:120])

print()
if FAILS:
    print("FAILURES: %d -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("ALL CHECKS PASSED")
