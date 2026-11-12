"""Self-check for server/app.py (helper, not delivered)."""

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
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.headers.get("Content-Type"), resp.read()


def main():
    # 1. importable functions
    summary = load_summary()
    assert isinstance(summary, dict) and summary["record_count"] == 44, summary
    all_recs = load_records()
    assert len(all_recs) == 44, len(all_recs)
    keys = [(r["device"], r["day"]) for r in all_recs]
    assert keys == sorted(keys), "not sorted by (device, day)"
    center = load_records(site="center")
    assert len(center) == 20 and all(r["site"] == "center" for r in center)
    limited = load_records(site="center", limit=2)
    assert len(limited) == 2 and limited == center[:2]
    assert len(load_records(limit="bad")) == 44  # invalid limit ignored
    print("functions OK")

    # 2. static serving against a temporary root
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        (tmp_root / "site").mkdir()
        (tmp_root / "site" / "index.html").write_text("<h1>TMP ROOT</h1>", encoding="utf-8")
        (tmp_root / "site" / "app.js").write_text("console.log(1);", encoding="utf-8")
        (tmp_root / "site" / "style.css").write_text("body{}", encoding="utf-8")
        (tmp_root / "site" / "data.json").write_text('{"x":1}', encoding="utf-8")
        (tmp_root / "data").mkdir()
        (tmp_root / "data" / "summary.json").write_text('{"tmp": true}', encoding="utf-8")
        (tmp_root / "data" / "records.jsonl").write_text(
            '{"site":"a","device":"a1","day":"2026-01-01","value":1}\n', encoding="utf-8")

        httpd = make_server(tmp_root, 0)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        base = f"http://127.0.0.1:{port}"
        try:
            st, ct, body = get(base + "/")
            assert st == 200 and "text/html" in ct and b"TMP ROOT" in body, (st, ct, body)
            st, ct, body = get(base + "/app.js")
            assert st == 200 and "javascript" in ct, (st, ct)
            st, ct, body = get(base + "/style.css")
            assert st == 200 and "text/css" in ct, (st, ct)
            st, ct, body = get(base + "/data.json")
            assert st == 200 and "application/json" in ct, (st, ct)
            st, ct, body = get(base + "/api/summary")
            assert json.loads(body) == {"summary": {"tmp": True}}, body
            st, ct, body = get(base + "/api/records")
            assert len(json.loads(body)["records"]) == 1, body
            # missing static file -> 404
            try:
                get(base + "/missing.html")
                raise AssertionError("expected 404")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404, exc.code
            print("temp-root static OK (incl. 404)")
        finally:
            httpd.shutdown()
            httpd.server_close()

    # 3. real server via CLI with --port 0
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server" / "app.py"), "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        line = proc.stdout.readline().strip()
        assert line.startswith("LISTENING "), line
        port = int(line.split()[1])
        base = f"http://127.0.0.1:{port}"

        st, ct, body = get(base + "/api/health")
        assert st == 200 and json.loads(body) == {"ok": True}, body

        st, ct, body = get(base + "/api/summary")
        assert json.loads(body)["summary"]["record_count"] == 44, body

        st, ct, body = get(base + "/api/records?site=center&limit=2")
        recs = json.loads(body)["records"]
        assert len(recs) == 2 and all(r["site"] == "center" for r in recs), recs
        assert recs == center[:2], recs

        # invalid limit ignored
        st, ct, body = get(base + "/api/records?site=center&limit=abc")
        assert len(json.loads(body)["records"]) == 20, body

        # site/ may not exist yet -> 404 is acceptable
        try:
            st, ct, body = get(base + "/")
            print("root served from real site/:", st, ct)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, exc.code
            print("root 404 (site/ not present yet) - acceptable")
        print("CLI server OK on port", port)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
