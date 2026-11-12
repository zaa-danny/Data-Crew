"""Standard-library HTTP server for the data dashboard.

Serves a small JSON API backed by ``data/`` and static files from ``site/``.

Usage::

    python server/app.py --port P

With ``--port 0`` the OS assigns a free port and exactly one line
``LISTENING <port>`` is printed to stdout (flushed). All log output goes to
stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]

# Content types for the static files we serve.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".txt": "text/plain; charset=utf-8",
}

# Static routes: URL path -> file name inside site/.
STATIC_ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
    "/data.json": "data.json",
}


# ---------------------------------------------------------------------------
# Data loading helpers (importable by tests)
# ---------------------------------------------------------------------------
def load_summary(root: Path | str = ROOT) -> dict:
    """Return the parsed content of ``<root>/data/summary.json``."""
    path = Path(root) / "data" / "summary.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_records(
    root: Path | str = ROOT,
    site: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return records from ``<root>/data/records.jsonl``.

    All records are read (unfiltered). When ``site`` is given only records for
    that site are kept. Records are sorted ascending by ``(device, day)`` and
    truncated to ``limit`` when it is a valid non-negative integer.
    """
    path = Path(root) / "data" / "records.jsonl"
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    if site is not None:
        records = [r for r in records if r.get("site") == site]

    records.sort(key=lambda r: (r.get("device", ""), r.get("day", "")))

    if limit is not None:
        records = records[:limit]

    return records


def _parse_limit(raw: str | None) -> int | None:
    """Parse a ``limit`` query value; return None when invalid."""
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class AppHandler(BaseHTTPRequestHandler):
    """Request handler; ``root`` is set on the server instance."""

    server_version = "DataDashboard/1.0"

    # -- helpers ---------------------------------------------------------
    @property
    def root(self) -> Path:
        return getattr(self.server, "root", ROOT)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _send_file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._send_error_json(404, "not found")
            return
        ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- routing ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/api/health":
                self._send_json({"ok": True})
                return
            if path == "/api/summary":
                self._send_json({"summary": load_summary(self.root)})
                return
            if path == "/api/records":
                site = query.get("site", [None])[0]
                limit = _parse_limit(query.get("limit", [None])[0])
                records = load_records(self.root, site=site, limit=limit)
                self._send_json({"records": records})
                return
        except FileNotFoundError:
            self._send_error_json(404, "not found")
            return
        except Exception as exc:  # pragma: no cover - defensive
            self.log_message("error handling %s: %s", path, exc)
            self._send_error_json(500, "internal server error")
            return

        # Static files.
        filename = STATIC_ROUTES.get(path)
        if filename is not None:
            self._send_file(self.root / "site" / filename)
            return

        self._send_error_json(404, "not found")


# ---------------------------------------------------------------------------
# Server factory / entry point
# ---------------------------------------------------------------------------
def make_server(root: Path | str = ROOT, port: int = 0) -> ThreadingHTTPServer:
    """Create (but do not start) an HTTP server bound to ``port``."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    httpd.root = Path(root)
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Data dashboard HTTP server")
    parser.add_argument("--port", type=int, default=8000, help="port to bind (0 = auto)")
    parser.add_argument("--root", default=str(ROOT), help="project root directory")
    args = parser.parse_args(argv)

    httpd = make_server(args.root, args.port)
    actual_port = httpd.server_address[1]
    print(f"LISTENING {actual_port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
