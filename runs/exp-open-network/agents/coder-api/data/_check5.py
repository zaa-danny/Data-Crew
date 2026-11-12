"""Live server evidence, printed in short lines to avoid truncation."""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def raw_get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


proc = subprocess.Popen(
    [sys.executable, str(ROOT / "server" / "app.py"), "--port", "0"],
    cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
try:
    line = proc.stdout.readline()
    print("STDOUT1=%r" % line)
    port = int(line.split()[1])
    b = "http://127.0.0.1:%d" % port
    for _ in range(100):
        try:
            if raw_get(b + "/api/health", timeout=1)[0] == 200:
                break
        except Exception:
            pass
        time.sleep(0.05)

    for path in ["/api/health", "/api/summary", "/api/records?site=center&limit=2",
                 "/api/records?limit=abc", "/api/nope", "/"]:
        st, hd, body = raw_get(b + path)
        print("PATH=%s STATUS=%d CT=%s CL=%s BODYLEN=%d"
              % (path, st, hd.get("Content-Type"), hd.get("Content-Length"), len(body)))
        print("  BODY=%s" % body.decode("utf-8", "replace")[:220].replace("\n", " "))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    err = proc.stderr.read()
    rest = proc.stdout.read()
    print("STDOUT_REST=%r" % rest)
    print("STDERR_LINES=%d" % len(err.splitlines()))
    for ln in err.splitlines()[:8]:
        print("  ERR| " + ln)
