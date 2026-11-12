"""Collector: fetch site pages and parse table#readings into records.

Contract
--------
Input: ``collect(source)`` where ``source`` is a dict shaped like
``{"kind": "http", "index": "...", "pages": ["http://...", ...]}``. Every URL
in ``source["pages"]`` is fetched with urllib (no proxy) and parsed.

Output: a list of record dicts, each ``{"site": str, "device": str,
"day": str, "value": float}``, in page order. The site name comes from
``<h1 data-site="...">``; each ``<tr data-device=".." data-day="..">`` inside
``table#readings`` is one record whose value is the text of
``<td class="value">`` parsed as float.

Guarantee: ALL records are returned in page order with NO filtering and NO
rounding. Fetch failures and parse failures (a row without a value cell, a
non-numeric value cell, or a missing site) raise a clear error instead of
being silently dropped.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from html.parser import HTMLParser


class CollectError(RuntimeError):
    """Raised when a page cannot be fetched or parsed."""


class _ReadingsParser(HTMLParser):
    def __init__(self, url: str = "") -> None:
        super().__init__()
        self.url = url
        self.site: str | None = None
        self.records: list[dict] = []
        self._in_table = False
        self._table_depth = 0
        self._cur: dict | None = None
        self._cur_has_value = False
        self._in_value = False
        self._value_text: list[str] = []

    def _finish_row(self) -> None:
        if self._cur is None:
            return
        if not self._cur_has_value:
            raise CollectError(
                f"row without a value cell for device={self._cur['device']!r} "
                f"day={self._cur['day']!r} in {self.url or 'page'}"
            )
        if self.site is None:
            raise CollectError(
                f"no <h1 data-site> found before row device="
                f"{self._cur['device']!r} day={self._cur['day']!r} in "
                f"{self.url or 'page'}"
            )
        text = "".join(self._value_text).strip()
        try:
            value = float(text)
        except ValueError as exc:
            raise CollectError(
                f"non-numeric value {text!r} for device="
                f"{self._cur['device']!r} day={self._cur['day']!r} "
                f"in {self.url or 'page'}"
            ) from exc
        self.records.append({
            "site": self.site,
            "device": self._cur["device"],
            "day": self._cur["day"],
            "value": value,
        })
        self._cur = None
        self._cur_has_value = False
        self._value_text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h1" and "data-site" in a and self.site is None:
            self.site = a["data-site"]
        elif tag == "table":
            if a.get("id") == "readings":
                self._in_table = True
                self._table_depth = 1
            elif self._in_table:
                self._table_depth += 1
        elif tag == "tr" and self._in_table:
            self._finish_row()
            if "data-device" in a and "data-day" in a:
                self._cur = {"device": a["data-device"], "day": a["data-day"]}
                self._cur_has_value = False
            else:
                self._cur = None
        elif tag == "td" and self._in_table and self._cur is not None:
            if a.get("class") == "value":
                self._in_value = True
                self._cur_has_value = True
                self._value_text = []

    def handle_endtag(self, tag):
        if tag == "table" and self._in_table:
            self._table_depth -= 1
            if self._table_depth <= 0:
                self._finish_row()
                self._in_table = False
        elif tag == "td" and self._in_value:
            self._in_value = False

    def handle_data(self, data):
        if self._in_value:
            self._value_text.append(data)

    def close(self):
        super().close()
        self._finish_row()


def _fetch(url: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - report any fetch failure clearly
        raise CollectError(f"failed to fetch {url}: {exc}") from exc
    return raw.decode("utf-8", errors="replace")


def collect(source: dict) -> list[dict]:
    records: list[dict] = []
    for url in source["pages"]:
        html = _fetch(url)
        parser = _ReadingsParser(url)
        try:
            parser.feed(html)
            parser.close()
        except CollectError:
            raise
        except Exception as exc:  # noqa: BLE001 - report parse failures clearly
            raise CollectError(f"failed to parse {url}: {exc}") from exc
        if parser.site is None:
            raise CollectError(f"no <h1 data-site> found in {url}")
        records.extend(parser.records)
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with open(args.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)
    records = collect(spec["source"])
    parent = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
