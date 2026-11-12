"""Print the tail of server/app.py and run a full, untruncated verification."""
import json
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

src = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
lines = src.splitlines()
print("=== server/app.py: %d lines, %d chars ===" % (len(lines), len(src)))
print("=== tail from line %d ===" % (len(lines) - 95))
for i, ln in enumerate(lines[len(lines) - 95:], start=len(lines) - 94):
    print("%4d| %s" % (i, ln))
print("=== end tail ===")
print()

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILS.append(name)


def raw_get(url, timeout=10):
    """Return (status, headers dict, body bytes) without raising on 4xx."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# ---- temp root static checks -------------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="apicheck3_"))
(tmp / "site").mkdir()
(tmp / "site" / "index.html").write_text("<html>TEMP-INDEX</html>", encoding="utf-8")
(tmp / "site" / "app.js").write_text("console.log('t');\n", encoding="utf-8")
(tmp / "site" / "style.css").write_text("body{color:red}\n", encoding="utf-8")
(tmp / "site" / "data.json").write_text('{"temp":true}\n', encoding="utf-8")

srv = appmod.make_server(root=tmp, port=0)
port = srv.server_address[1]
th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
th.start()
try:
    b = "http://127.0.0.1:%d" % port
    for path, needle, ctype in [
        ("/", b"TEMP-INDEX", "text/html"),
        ("/app.js", b"console.log", "javascript"),
        ("/style.css", b"color:red", "text/css"),
        ("/data.json", b'"temp"', "application/json"),
    ]:
        st, hd, body = raw_get(b + path)
        check("temp GET %s 200" % path, st == 200, st)
        check("temp GET %s body" % path, needle in body, body[:60])
        check("temp GET %s content-type" % path, ctype in hd.get("Content-Type", ""), hd.get("Content-Type"))
        check("temp GET %s content-length" % path,
              hd.get("Content-Length") == str(len(body)), (hd.get("Content-Length"), len(body)))
    st, hd, body = raw_get(b + "/missing.html")
    check("temp GET /missing.html 404", st == 404, st)
    check("temp 404 is JSON", json.loads(body).get("error") == "not found", body[:80])
finally:
    srv.shutdown()
    srv.server_close()
    th.join(timeout=5)

# ---- real server via --port 0 ------------------------------------------
proc = subprocess.Popen(
    [sys.executable, str(ROOT / "server" / "app.py"), "--port", "0"],
    cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
try:
    line = proc.stdout.readline()
    print("STDOUT LINE 1: %r" % line)
    check("LISTENING line exact format", line.strip().startswith("LISTENING ") and line.strip().split()[1].isdigit(), line)
    port = int(line.split()[1])
    b = "http://127.0.0.1:%d" % port
    for _ in range(100):
        try:
            if raw_get(b + "/api/health", timeout=1)[0] == 200:
                break
        except Exception:
            pass
        time.sleep(0.05)

    st, hd, body = raw_get(b + "/api/health")
    print("GET /api/health -> %d %s %s" % (st, hd.get("Content-Type"), body.decode()))
    check("health 200", st == 200, st)
    check("health body", json.loads(body) == {"ok": True}, body)
    check("health content-length", hd.get("Content-Length") == str(len(body)), hd.get("Content-Length"))

    st, hd, body = raw_get(b + "/api/summary")
    payload = json.loads(body)
    print("GET /api/summary -> %d, keys=%s, record_count=%s, total=%s"
          % (st, list(payload), payload["summary"]["record_count"], payload["summary"]["total"]))
    check("summary 200", st == 200, st)
    check("summary wrapped", payload.get("summary") == appmod.load_summary(), list(payload))
    check("summary content-length", hd.get("Content-Length") == str(len(body)), hd.get("Content-Length"))

    st, hd, body = raw_get(b + "/api/records?site=center&limit=2")
    recs = json.loads(body)["records"]
    print("GET /api/records?site=center&limit=2 -> %d %s" % (st, json.dumps(recs)))
    check("records 200", st == 200, st)
    check("records limit=2", len(recs) == 2, recs)
    check("records site filter", all(r["site"] == "center" for r in recs), recs)
    check("records matches function", recs == appmod.load_records(site="center", limit=2), recs)
    check("records content-length", hd.get("Content-Length") == str(len(body)), hd.get("Content-Length"))

    st, hd, body = raw_get(b + "/api/records")
    print("GET /api/records -> %d, count=%d" % (st, len(json.loads(body)["records"])))
    check("records all 44", len(json.loads(body)["records"]) == 44, st)

    st, hd, body = raw_get(b + "/api/records?limit=abc")
    print("GET /api/records?limit=abc -> %d, count=%d" % (st, len(json.loads(body)["records"])))
    check("invalid limit ignored", len(json.loads(body)["records"]) == 44, st)

    st, hd, body = raw_get(b + "/api/nope")
    print("GET /api/nope -> %d %s %s" % (st, hd.get("Content-Type"), body.decode()))
    check("unknown api 404", st == 404, st)
    check("unknown api 404 JSON", json.loads(body).get("error") == "unknown endpoint", body[:80])

    st, hd, body = raw_get(b + "/")
    idx = ROOT / "site" / "index.html"
    print("GET / -> %d (site/index.html exists=%s)" % (st, idx.exists()))
    if idx.exists():
        check("real index served", st == 200 and body == idx.read_bytes(), st)
    else:
        check("GET / 404 when site/index.html absent", st == 404, st)
        check("GET / 404 JSON", json.loads(body).get("error") == "not found", body[:80])
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    err = proc.stderr.read()
    print("=== STDERR (first 12 lines) ===")
    for ln in err.splitlines()[:12]:
        print("  " + ln)
    check("logs on stderr", "serving root=" in err, err[:120])
    check("stdout had only LISTENING", proc.stdout.read().strip() == "", "extra stdout")

print()
print("FAILURES: %s" % (FAILS or "none"))
sys.exit(1 if FAILS else 0)
