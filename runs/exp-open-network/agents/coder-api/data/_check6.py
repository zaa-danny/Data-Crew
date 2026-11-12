"""Temp-root static serving evidence, short lines."""
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import app as appmod  # noqa: E402


def raw_get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


tmp = Path(tempfile.mkdtemp(prefix="apicheck6_"))
(tmp / "site").mkdir()
(tmp / "site" / "index.html").write_text("<html>TEMP-INDEX</html>", encoding="utf-8")
(tmp / "site" / "app.js").write_text("console.log('t');\n", encoding="utf-8")
(tmp / "site" / "style.css").write_text("body{color:red}\n", encoding="utf-8")
(tmp / "site" / "data.json").write_text('{"temp":true}\n', encoding="utf-8")
print("TMPROOT=%s" % tmp)

srv = appmod.make_server(root=tmp, port=0)
port = srv.server_address[1]
th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
th.start()
try:
    b = "http://127.0.0.1:%d" % port
    for path in ["/", "/app.js", "/style.css", "/data.json", "/missing.html", "/api/summary"]:
        st, hd, body = raw_get(b + path)
        print("PATH=%s STATUS=%d CT=%s CL=%s BODYLEN=%d"
              % (path, st, hd.get("Content-Type"), hd.get("Content-Length"), len(body)))
        print("  BODY=%s" % body.decode("utf-8", "replace")[:120].replace("\n", " "))
finally:
    srv.shutdown()
    srv.server_close()
    th.join(timeout=5)
