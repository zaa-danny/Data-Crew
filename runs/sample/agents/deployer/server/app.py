"""Standard-library HTTP server exposing the dataapp API and static site.

Usage:
    python server/app.py --port P

With ``--port 0`` the OS assigns a free port; exactly one line
``LISTENING <port>`` is printed to stdout (flushed) and all other logging
goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = "data"
SITE_DIR = "site"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

# Static URL path -> file name inside <root>/site
STATIC_ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
    "/data.json": "data.json",
}


# --------------------------------------------------------------------------
# Data loading helpers (importable by tests)
# --------------------------------------------------------------------------
def _data_dir(root: Path | str | None = None) -> Path:
    base = Path(root) if root is not None else ROOT
    return base / DATA_DIR


def load_summary(root: Path | str | None = None) -> dict:
    """Return the parsed content of ``<root>/data/summary.json``."""
    path = _data_dir(root) / "summary.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_records(root: Path | str | None = None) -> list[dict]:
    path = _data_dir(root) / "records.jsonl"
    records: list[dict] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
    return records


def _sort_key(record: dict) -> tuple:
    return (
        str(record.get("device", "")),
        str(record.get("day", "")),
    )


def load_records(
    root: Path | str | None = None,
    site: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Load records from ``<root>/data/records.jsonl``.

    All records are returned (unfiltered by value); when ``site`` is given the
    result is filtered to that site.  Records are sorted ascending by
    ``(device, day)`` and truncated to ``limit`` when it is a valid
    non-negative integer.
    """
    records = _read_records(root)

    if site is not None:
        records = [r for r in records if r.get("site") == site]

    records.sort(key=_sort_key)

    if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0:
        records = records[:limit]

    return records


def _parse_limit(raw: str | None) -> int | None:
    """Parse a ``limit`` query value; return None when invalid."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


# --------------------------------------------------------------------------
# HTTP handler / server
# --------------------------------------------------------------------------
class AppHandler(BaseHTTPRequestHandler):
    """Request handler; ``root`` is set on the subclass by :func:`make_server`."""

    root: Path = ROOT
    server_version = "DataAppAPI/1.0"
    protocol_version = "HTTP/1.1"

    # -- logging -----------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stderr.flush()

    # -- helpers -----------------------------------------------------------
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message, "status": status}, status=status)

    def _send_file(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_error_json(404, "not found")
            return
        ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)

        if path == "/api/health":
            self._send_json({"ok": True})
            return

        if path == "/api/summary":
            try:
                summary = load_summary(self.root)
            except (OSError, json.JSONDecodeError) as exc:
                self._send_error_json(500, f"summary unavailable: {exc}")
                return
            self._send_json({"summary": summary})
            return

        if path == "/api/records":
            site_values = query.get("site")
            site = site_values[0] if site_values else None
            limit_values = query.get("limit")
            limit = _parse_limit(limit_values[0] if limit_values else None)
            try:
                records = load_records(self.root, site=site, limit=limit)
            except OSError as exc:
                self._send_error_json(500, f"records unavailable: {exc}")
                return
            self._send_json({"records": records})
            return

        if path in STATIC_ROUTES:
            self._send_file(self.root / SITE_DIR / STATIC_ROUTES[path])
            return

        self._send_error_json(404, "not found")

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in STATIC_ROUTES:
            target = self.root / SITE_DIR / STATIC_ROUTES[path]
            if target.is_file():
                ctype = CONTENT_TYPES.get(
                    target.suffix.lower(), "application/octet-stream"
                )
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(target.stat().st_size))
                self.end_headers()
                return
        self._send_error_json(404, "not found")


def make_server(root: Path | str | None = None, port: int = 0,
                host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Create (but do not start) an HTTP server rooted at ``root``."""
    base = Path(root).resolve() if root is not None else ROOT

    handler_cls = type("BoundAppHandler", (AppHandler,), {"root": base})
    httpd = ThreadingHTTPServer((host, int(port)), handler_cls)
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DataApp backend API server")
    parser.add_argument("--port", type=int, default=8000,
                        help="port to listen on (0 = OS-assigned)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--root", default=None,
                        help="project root (defaults to repository root)")
    args = parser.parse_args(argv)

    httpd = make_server(args.root, args.port, args.host)
    actual_port = httpd.server_address[1]

    sys.stdout.write(f"LISTENING {actual_port}\n")
    sys.stdout.flush()
    sys.stderr.write(f"serving {Path(args.root).resolve() if args.root else ROOT} "
                     f"on http://{args.host}:{actual_port}\n")
    sys.stderr.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("shutting down\n")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
