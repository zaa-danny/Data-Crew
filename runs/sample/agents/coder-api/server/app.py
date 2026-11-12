"""Standard-library HTTP server for the data app.

Serves:
  GET /api/health                     -> {"ok": true}
  GET /api/summary                    -> {"summary": <data/summary.json>}
  GET /api/records?site=X&limit=N     -> {"records": [...]}
  GET /                               -> site/index.html
  GET /app.js, /style.css, /data.json -> matching files in site/

Usage: python server/app.py --port P
With --port 0 the OS assigns the port; exactly one line
"LISTENING <port>" is printed to stdout, all logs go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]

# Static routes: URL path -> file name inside <root>/site
STATIC_ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
    "/data.json": "data.json",
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


# --------------------------------------------------------------------------
# Data loading helpers (importable by tests)
# --------------------------------------------------------------------------
def load_summary(root: Path | str = ROOT) -> dict:
    """Return the parsed content of <root>/data/summary.json."""
    path = Path(root) / "data" / "summary.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_records(
    root: Path | str = ROOT,
    site: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return records from <root>/data/records.jsonl.

    All records are read (unfiltered); when ``site`` is given only records
    with that site are kept.  The result is sorted ascending by
    (device, day) and truncated to ``limit`` when it is a valid int >= 0.
    """
    path = Path(root) / "data" / "records.jsonl"
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if site is not None and rec.get("site") != site:
                continue
            records.append(rec)

    records.sort(key=lambda r: (str(r.get("device", "")), str(r.get("day", ""))))

    if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0:
        records = records[:limit]
    return records


def _parse_limit(raw: str | None) -> int | None:
    """Parse a limit query value; return None when invalid."""
    if raw is None:
        return None
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------
class AppHandler(BaseHTTPRequestHandler):
    """Request handler; ``root`` is set by :func:`make_server`."""

    root: Path = ROOT
    server_version = "DataApp/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stderr.flush()

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    # -- routing ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        try:
            self._route()
        except BrokenPipeError:  # client went away
            pass
        except Exception as exc:  # pragma: no cover - defensive
            self.log_message("internal error: %r", exc)
            try:
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error"
                )
            except Exception:
                pass

    do_HEAD = do_GET  # noqa: N815

    def _route(self) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)

        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if path == "/api/summary":
            try:
                summary = load_summary(self.root)
            except FileNotFoundError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "summary not found")
                return
            except (OSError, json.JSONDecodeError) as exc:
                self.log_message("summary read failed: %r", exc)
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "summary unavailable"
                )
                return
            self._send_json(HTTPStatus.OK, {"summary": summary})
            return

        if path == "/api/records":
            site_vals = query.get("site")
            site = site_vals[0] if site_vals else None
            if site is not None and site == "":
                site = None
            limit_vals = query.get("limit")
            limit = _parse_limit(limit_vals[0] if limit_vals else None)
            try:
                records = load_records(self.root, site=site, limit=limit)
            except FileNotFoundError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "records not found")
                return
            except OSError as exc:
                self.log_message("records read failed: %r", exc)
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "records unavailable"
                )
                return
            self._send_json(HTTPStatus.OK, {"records": records})
            return

        if path.startswith("/api/"):
            self._send_error_json(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return

        self._serve_static(path)

    def _serve_static(self, path: str) -> None:
        name = STATIC_ROUTES.get(path)
        if name is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        file_path = Path(self.root) / "site" / name
        try:
            body = file_path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        except OSError as exc:
            self.log_message("static read failed: %r", exc)
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, "static file unavailable"
            )
            return
        ctype = CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
        self._send_bytes(HTTPStatus.OK, body, ctype)


# --------------------------------------------------------------------------
# Server factory / entry point
# --------------------------------------------------------------------------
def make_server(root: Path | str = ROOT, port: int = 0, host: str = "127.0.0.1"):
    """Create (but do not start) a ThreadingHTTPServer serving ``root``."""
    root_path = Path(root).resolve()

    class _Handler(AppHandler):
        pass

    _Handler.root = root_path
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Data app HTTP server")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind")
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="project root containing data/ and site/ (default: repo root)",
    )
    args = parser.parse_args(argv)

    httpd = make_server(args.root, args.port, args.host)
    actual_port = httpd.server_address[1]
    print(f"LISTENING {actual_port}", flush=True)
    sys.stderr.write(f"serving {Path(args.root).resolve()} on {args.host}:{actual_port}\n")
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
