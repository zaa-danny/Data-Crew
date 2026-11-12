"""Confirm cwd-independence and clean import of server.app."""
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
other = tempfile.mkdtemp(prefix="cwd_")

proc = subprocess.Popen(
    [sys.executable, str(ROOT / "server" / "app.py"), "--port", "0"],
    cwd=other, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
try:
    port = int(proc.stdout.readline().split()[1])
    with urllib.request.urlopen("http://127.0.0.1:%d/api/summary" % port, timeout=10) as r:
        data = json.load(r)
    print("cwd-independent summary ok:", data["summary"]["record_count"] == 44)
finally:
    proc.terminate()
    proc.wait(timeout=10)

# import from a foreign cwd too
code = "import sys; sys.path.insert(0, %r); from server import app; print(app.ROOT.name, len(app.load_records()))" % str(ROOT)
out = subprocess.run([sys.executable, "-c", code], cwd=other, capture_output=True, text=True)
print("import from other cwd:", out.returncode, out.stdout.strip(), out.stderr.strip()[:200])
