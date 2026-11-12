"""Standard-library HTTP server: JSON API over data/ plus static files from site/.

Usage:
    python server/app.py --port P

With ``--port 0`` the OS assigns a free port; exactly one line
``LISTENING <port>`` is printed to stdout (flushed) and every log message
goes to stderr.

Endpoints:
    GET /api/health              -> {"ok": true}
    GET /api/summary             -> {"summary": <data/summary.json>}
    GET /api/records?site=&limit= -> {"records": [...]} from data/records.jsonl
    GET /                        -> site/index.html
    GET /app.js /style.css /data.json -> matching files in site/
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]

DATA_DIRNAME = "data"
SITE_DIRNAME = "site"
SUMMARY_FILENAME = "summary.json"
RECORDS_FILENAME = "records.jsonl"

# Static routes: URL path -> (file name inside site/, Content-Type)
STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/data.json": ("data.json", "application/json; charset=utf-8"),
}


# --------------------------------------------------------------------------- #
# Data loading helpers (importable for tests)
# --------------------------------------------------------------------------- #
def _data_dir(root) -> Path:
    return Path(root).resolve() / DATA_DIRNAME


def load_summary(root=ROOT) -> dict:
    """Return the parsed content of ``<root>/data/summary.json``."""
    path = _data_dir(root) / SUMMARY_FILENAME
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_records(root=ROOT, site=None, limit=None) -> list[dict]:
    """Return records from ``<root>/data/records.jsonl``.

    All records are read (unfiltered); when ``site`` is given only records
    whose ``site`` matches are kept.  The result is sorted ascending by
    ``(device, day)`` and truncated to ``limit`` when ``limit`` is a valid
    non-negative integer (an invalid limit is ignored).
    """
    path = _data_dir(root) / RECORDS_FILENAME
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    if site is not None:
        records = [r for r in records if r.get("site") == site]

    records.sort(key=lambda r: (str(r.get("device", "")), str(r.get("day", ""))))

    limit_value = _coerce_limit(limit)
    if limit_value is not None:
        records = records[:limit_value]
    return records


def _coerce_limit(limit):
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


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class AppHandler(BaseHTTPRequestHandler):
    """Request handler; ``root`` is set on the subclass by ``make_server``."""

    root: Path = ROOT
    server_version = "DataAppHTTP/1.0"
    protocol_version = "HTTP/1.1"

    # -- logging ---------------------------------------------------------- #
    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stderr.flush()

    # -- helpers ---------------------------------------------------------- #
    def _send_bytes(self, status, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status, message: str) -> None:
        self._send_json(status, {"error": message})

    # -- routing ---------------------------------------------------------- #
    def do_GET(self):  # noqa: N802 - stdlib naming
        try:
            self._route()
        except BrokenPipeError:  # client went away
            pass
        except Exception as exc:  # pragma: no cover - defensive
            self.log_message("unhandled error: %r", exc)
            try:
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error"
                )
            except Exception:
                pass

    do_HEAD = do_GET

    def _route(self) -> None:
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = parse_qs(parts.query, keep_blank_values=True)

        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/api/summary":
            self._handle_summary()
            return
        if path == "/api/records":
            self._handle_records(query)
            return
        if path.startswith("/api/"):
            self._send_error_json(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return
        self._handle_static(path)

    def _handle_summary(self) -> None:
        try:
            summary = load_summary(self.root)
        except FileNotFoundError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "summary not found")
            return
        except (OSError, ValueError) as exc:
            self.log_message("summary read failed: %r", exc)
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, "summary unavailable"
            )
            return
        self._send_json(HTTPStatus.OK, {"summary": summary})

    def _handle_records(self, query: dict) -> None:
        site_values = query.get("site")
        site = site_values[0] if site_values else None
        if site is not None and site == "":
            site = None
        limit_values = query.get("limit")
        limit = limit_values[0] if limit_values else None
        try:
            records = load_records(self.root, site=site, limit=limit)
        except FileNotFoundError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "records not found")
            return
        except (OSError, ValueError) as exc:
            self.log_message("records read failed: %r", exc)
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, "records unavailable"
            )
            return
        self._send_json(HTTPStatus.OK, {"records": records})

    def _handle_static(self, path: str) -> None:
        route = STATIC_ROUTES.get(path)
        if route is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        filename, content_type = route
        site_dir = (Path(self.root).resolve() / SITE_DIRNAME).resolve()
        target = (site_dir / filename).resolve()
        # Guard against traversal (defensive; filenames are fixed constants).
        if site_dir != target.parent:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            body = target.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        except OSError as exc:
            self.log_message("static read failed for %s: %r", target, exc)
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, "static file unavailable"
            )
            return
        self._send_bytes(HTTPStatus.OK, body, content_type)


# --------------------------------------------------------------------------- #
# Server factory / entry point
# --------------------------------------------------------------------------- #
def make_server(root=ROOT, port: int = 0, host: str = "127.0.0.1"):
    """Create (but do not start) a server serving ``root``.

    Returns the ``ThreadingHTTPServer`` instance; ``server.server_address[1]``
    is the actual bound port.
    """
    root_path = Path(root).resolve()
    handler_cls = type("BoundAppHandler", (AppHandler,), {"root": root_path})
    httpd = ThreadingHTTPServer((host, int(port)), handler_cls)
    httpd.daemon_threads = True
    return httpd


def serve(root=ROOT, port: int = 0, host: str = "127.0.0.1") -> None:
    """Run the server until interrupted, printing the LISTENING line."""
    httpd = make_server(root=root, port=port, host=host)
    actual_port = httpd.server_address[1]
    sys.stdout.write("LISTENING %d\n" % actual_port)
    sys.stdout.flush()
    sys.stderr.write(
        "serving root=%s on http://%s:%d\n" % (Path(root).resolve(), host, actual_port)
    )
    sys.stderr.flush()
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        sys.stderr.write("interrupted, shutting down\n")
    finally:
        httpd.shutdown()
        httpd.server_close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Serve the data app API and static site.")
    ap.add_argument("--port", type=int, default=8000, help="port to bind (0 = auto)")
    ap.add_argument("--host", default="127.0.0.1", help="host to bind")
    ap.add_argument(
        "--root",
        default=str(ROOT),
        help="project root containing data/ and site/ (default: repo root)",
    )
    args = ap.parse_args(argv)
    serve(root=args.root, port=args.port, host=args.host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
