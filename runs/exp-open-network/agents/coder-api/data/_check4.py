"""Emit server/app.py tail and verification evidence in small, untruncated chunks."""
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
print("app.py lines=%d chars=%d" % (len(lines), len(src)))
print("--- lines 236-283 ---")
for i in range(235, len(lines)):
    print("%4d| %s" % (i + 1, lines[i]))
print("--- end ---")
