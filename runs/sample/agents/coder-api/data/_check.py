"""Verification helper for server/app.py (not delivered)."""

import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.app import load_records, load_summary, make_server  # noqa: E402


def get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.headers.get("Content-Type"), resp.read()


def main():
    # --- importable functions -------------------------------------------
    summary = load_summary()
    assert isinstance(summary, dict) and summary["record_count"] == 44, summary
    print("load_summary ok:", summary["record_count"], "records")

    all_recs = load_records()
    assert len(all_recs) == 44, len(all_recs)
    keys = [(r["device"], r["day"]) for r in all_recs]
    assert keys == sorted(keys), "not sorted by (device, day)"
    print("load_records all ok:", len(all_recs))

    site = summary["by_site"][0]["site"]
    filtered = load_records(site=site)
    assert filtered and all(r["site"] == site for r in filtered)
    limited = load_records(site=site, limit=2)
    assert len(limited) == 2 and limited == filtered[:2]
    print(f"load_records site={site} ok: {len(filtered)} -> limit 2")

    # invalid limit ignored
    assert len(load_records(limit="bad")) == 44  # type: ignore[arg-type]
    print("invalid limit ignored ok")

    # --- real server via --port 0 ---------------------------------------
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server" / "app.py"), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stdout.readline().strip()
        assert line.startswith("LISTENING "), line
        port = int(line.split()[1])
        base = f"http://127.0.0.1:{port}"
        print("server line:", line)

        status, ctype, body = get(base + "/api/health")
        assert status == 200 and json.loads(body) == {"ok": True}
        print("GET /api/health ok")

        status, ctype, body = get(base + "/api/summary")
        assert status == 200 and "application/json" in ctype
        assert json.loads(body)["summary"] == summary
        print("GET /api/summary ok")

        status, ctype, body = get(base + f"/api/records?site={site}&limit=2")
        recs = json.loads(body)["records"]
        assert status == 200 and len(recs) == 2 and all(r["site"] == site for r in recs)
        assert recs == limited
        print("GET /api/records ok")

        status, ctype, body = get(base + "/api/records?limit=xyz")
        assert status == 200 and len(json.loads(body)["records"]) == 44
        print("GET /api/records invalid limit ok")

        # static: site/ may not exist yet -> 404 is acceptable here
        try:
            status, ctype, body = get(base + "/")
            print("GET / (real site) status:", status, ctype)
        except urllib.error.HTTPError as exc:
            print("GET / (real site) status:", exc.code)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        err = proc.stderr.read()
        assert "LISTENING" not in err, "LISTENING leaked to stderr"
        print("server stopped; stderr lines:", len(err.splitlines()))

    # --- static serving against a TEMPORARY root ------------------------
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        (tmp_root / "site").mkdir()
        (tmp_root / "site" / "index.html").write_text("<h1>tmp index</h1>", encoding="utf-8")
        (tmp_root / "site" / "app.js").write_text("console.log(1);", encoding="utf-8")
        (tmp_root / "site" / "style.css").write_text("body{}", encoding="utf-8")
        (tmp_root / "site" / "data.json").write_text('{"x":1}', encoding="utf-8")

        httpd = make_server(tmp_root, 0)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        base = f"http://127.0.0.1:{port}"
        try:
            status, ctype, body = get(base + "/")
            assert status == 200 and body == b"<h1>tmp index</h1>", body
            assert "text/html" in ctype, ctype
            print("temp root GET / ok:", ctype)

            status, ctype, body = get(base + "/app.js")
            assert status == 200 and "javascript" in ctype, (status, ctype)
            status, ctype, body = get(base + "/style.css")
            assert status == 200 and "text/css" in ctype, (status, ctype)
            status, ctype, body = get(base + "/data.json")
            assert status == 200 and "application/json" in ctype, (status, ctype)
            print("temp root static content-types ok")

            try:
                get(base + "/missing.html")
                raise AssertionError("expected 404")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404, exc.code
            print("temp root missing file -> 404 ok")
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(timeout=5)

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
