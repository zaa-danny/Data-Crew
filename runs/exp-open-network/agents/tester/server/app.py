"""Standard-library HTTP server for the data app.

Serves:
  GET /api/health                      -> {"ok": true}
  GET /api/summary                     -> {"summary": <data/summary.json>}
  GET /api/records?site=X&limit=N      -> {"records": [...]}
  GET /                                -> site/index.html
  GET /app.js, /style.css, /data.json  -> matching files in site/

Usage: python server/app.py --port P
With --port 0 the OS assigns the port and exactly one line
"LISTENING <port>" is printed to stdout; all logs go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = "data"
SITE_DIR = "site"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
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
def load_summary(root: Path | str = ROOT) -> dict:
    """Return the parsed content of <root>/data/summary.json."""
    path = Path(root) / DATA_DIR / "summary.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_records(root: Path | str) -> list[dict]:
    path = Path(root) / DATA_DIR / "records.jsonl"
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


def _parse_limit(limit) -> int | None:
    """Return a non-negative int for a valid limit, else None (ignored)."""
    if limit is None or isinstance(limit, bool):
        return None
    if isinstance(limit, int):
        return limit if limit >= 0 else None
    if isinstance(limit, str):
        text = limit.strip()
        if not text:
            return None
        try:
            value = int(text, 10)
        except ValueError:
            return None
        return value if value >= 0 else None
    return None


def load_records(root: Path | str = ROOT, site: str | None = None,
                 limit=None) -> list[dict]:
    """Load all records from <root>/data/records.jsonl.

    Records are unfiltered by value; optionally filtered by ``site``,
    sorted ascending by (device, day) and truncated to ``limit`` when the
    limit is valid (invalid limits are ignored).
    """
    records = _read_records(root)

    if site is not None and site != "":
        records = [r for r in records if r.get("site") == site]

    records.sort(key=lambda r: (str(r.get("device", "")), str(r.get("day", ""))))

    parsed = _parse_limit(limit)
    if parsed is not None:
        records = records[:parsed]
    return records


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------
class AppHandler(BaseHTTPRequestHandler):
    """Request handler; ``root`` is set on the subclass by make_server()."""

    root: Path = ROOT
    server_version = "DataAppHTTP/1.0"
    protocol_version = "HTTP/1.1"

    # -- logging -----------------------------------------------------------
    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stderr.flush()

    # -- helpers -----------------------------------------------------------
    def _send_bytes(self, status: int, body: bytes, content_type: str,
                    extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    # -- routing -----------------------------------------------------------
    def do_GET(self):  # noqa: N802 - stdlib naming
        try:
            self._route()
        except BrokenPipeError:  # client went away
            pass
        except Exception as exc:  # pragma: no cover - defensive
            self.log_message("unhandled error: %r", exc)
            try:
                self._send_error_json(500, "internal server error")
            except Exception:
                pass

    do_HEAD = do_GET

    def _route(self) -> None:
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = parse_qs(parts.query, keep_blank_values=True)

        if path == "/api/health":
            self._send_json(200, {"ok": True})
            return
        if path == "/api/summary":
            try:
                summary = load_summary(self.root)
            except FileNotFoundError:
                self._send_error_json(404, "summary not found")
                return
            except (OSError, json.JSONDecodeError) as exc:
                self._send_error_json(500, "cannot read summary: %s" % exc)
                return
            self._send_json(200, {"summary": summary})
            return
        if path == "/api/records":
            site = query.get("site", [None])[0]
            limit = query.get("limit", [None])[0]
            try:
                records = load_records(self.root, site=site, limit=limit)
            except OSError as exc:
                self._send_error_json(500, "cannot read records: %s" % exc)
                return
            self._send_json(200, {"records": records})
            return
        if path.startswith("/api/"):
            self._send_error_json(404, "unknown endpoint")
            return

        self._serve_static(path)

    def _serve_static(self, path: str) -> None:
        name = STATIC_ROUTES.get(path)
        if name is None:
            self._send_error_json(404, "not found")
            return
        site_dir = (self.root / SITE_DIR).resolve()
        target = (site_dir / name).resolve()
        try:
            target.relative_to(site_dir)
        except ValueError:
            self._send_error_json(404, "not found")
            return
        if not target.is_file():
            self._send_error_json(404, "not found")
            return
        try:
            body = target.read_bytes()
        except OSError as exc:
            self._send_error_json(500, "cannot read file: %s" % exc)
            return
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send_bytes(200, body, ctype)


# --------------------------------------------------------------------------
# Server factory / entry point
# --------------------------------------------------------------------------
def make_server(root: Path | str = ROOT, port: int = 0,
                host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Create (but do not start) an HTTP server rooted at ``root``."""
    root_path = Path(root).resolve()

    handler_cls = type("BoundAppHandler", (AppHandler,), {"root": root_path})
    httpd = ThreadingHTTPServer((host, int(port)), handler_cls)
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Data app HTTP server")
    parser.add_argument("--port", type=int, default=8000,
                        help="port to listen on (0 = OS assigned)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--root", default=str(ROOT),
                        help="project root containing data/ and site/")
    args = parser.parse_args(argv)

    httpd = make_server(args.root, args.port, args.host)
    actual_port = httpd.server_address[1]
    print("LISTENING %d" % actual_port, flush=True)
    sys.stderr.write("serving %s on http://%s:%d\n"
                     % (Path(args.root).resolve(), args.host, actual_port))
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
