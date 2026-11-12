"""Standard-library HTTP server for the dataapp sample.

Serves:
  GET /api/health            -> {"ok": true}
  GET /api/summary           -> {"summary": <data/summary.json>}
  GET /api/records?site=&limit= -> {"records": [...]} from data/records.jsonl
  GET /                        -> site/index.html
  GET /app.js /style.css /data.json -> matching files in site/

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
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]

# Static file name -> Content-Type
STATIC_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "data.json": "application/json; charset=utf-8",
}

# URL path -> static file name
STATIC_ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
    "/data.json": "data.json",
}


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_summary(root: Path | str = ROOT) -> dict:
    """Return the parsed content of <root>/data/summary.json."""
    path = Path(root) / "data" / "summary.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_records(root: Path | str) -> list[dict]:
    path = Path(root) / "data" / "records.jsonl"
    records: list[dict] = []
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


def _parse_limit(limit) -> int | None:
    """Return a non-negative int for a valid limit, else None."""
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


def load_records(root: Path | str = ROOT, site=None, limit=None) -> list[dict]:
    """Load all records, optionally filter by site, sort, then truncate.

    Records are sorted ascending by (device, day). An invalid limit is ignored.
    """
    records = _read_records(root)
    if site is not None and site != "":
        site = str(site)
        records = [r for r in records if str(r.get("site", "")) == site]
    records.sort(key=_sort_key)
    parsed = _parse_limit(limit)
    if parsed is not None:
        records = records[:parsed]
    return records


class AppHandler(BaseHTTPRequestHandler):
    """Request handler; the served root is taken from ``self.server.root``."""

    server_version = "DataAppHTTP/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------
    @property
    def root(self) -> Path:
        return Path(getattr(self.server, "root", ROOT))

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        _log("%s - %s" % (self.address_string(), fmt % args))

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    # -- routing ---------------------------------------------------------
    def do_GET(self):  # noqa: N802 - stdlib naming
        try:
            self._route()
        except BrokenPipeError:  # pragma: no cover - client went away
            pass
        except Exception as exc:  # pragma: no cover - defensive
            _log("error handling %s: %r" % (self.path, exc))
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
            try:
                summary = load_summary(self.root)
            except FileNotFoundError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "summary not found")
                return
            except (OSError, json.JSONDecodeError) as exc:
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "summary unreadable: %s" % exc
                )
                return
            self._send_json(HTTPStatus.OK, {"summary": summary})
            return

        if path == "/api/records":
            site = query.get("site", [None])[0]
            limit = query.get("limit", [None])[0]
            try:
                records = load_records(self.root, site=site, limit=limit)
            except FileNotFoundError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "records not found")
                return
            except OSError as exc:
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "records unreadable: %s" % exc
                )
                return
            self._send_json(HTTPStatus.OK, {"records": records})
            return

        if path in STATIC_ROUTES:
            self._serve_static(STATIC_ROUTES[path])
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def _serve_static(self, name: str) -> None:
        site_dir = (self.root / "site").resolve()
        target = (site_dir / name).resolve()
        # Guard against path traversal (name is from a fixed whitelist anyway).
        if site_dir != target.parent and site_dir not in target.parents:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            body = target.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        except OSError as exc:
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, "static read failed: %s" % exc
            )
            return
        content_type = STATIC_TYPES.get(name, "application/octet-stream")
        self._send_bytes(HTTPStatus.OK, body, content_type)


class AppServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the served root directory."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler_cls=AppHandler, root: Path | str = ROOT):
        self.root = Path(root).resolve()
        super().__init__(server_address, handler_cls)


def make_server(root: Path | str = ROOT, port: int = 0, host: str = "127.0.0.1") -> AppServer:
    """Create (but do not start) a server bound to host:port serving ``root``."""
    return AppServer((host, int(port)), AppHandler, root=Path(root))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serve the dataapp API and site.")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (0 = any)")
    parser.add_argument("--host", default="127.0.0.1", help="host/interface to bind")
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="project root containing data/ and site/ (default: repo root)",
    )
    args = parser.parse_args(argv)

    server = make_server(root=args.root, port=args.port, host=args.host)
    actual_port = server.server_address[1]
    print("LISTENING %d" % actual_port, flush=True)
    _log("serving root=%s on http://%s:%d" % (server.root, args.host, actual_port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("interrupted, shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
