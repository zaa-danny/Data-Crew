"""Collector: fetch site pages and parse table#readings into records.

Contract
--------
Input: ``collect(source)`` where ``source`` is a dict shaped like
``{"kind": "http", "index": "...", "pages": ["http://...", ...]}``. Only
``source["pages"]`` is used: each URL is fetched with urllib (no proxy) and
parsed with the stdlib html.parser.

Output: a list of record dicts, one per ``<tr data-device data-day>`` row
inside ``table#readings``, each shaped as
``{"site": str, "device": str, "day": str, "value": float}``. The site name
comes from ``<h1 data-site="...">`` and the value from the text of
``<td class="value">``.

Guarantee: ALL records are returned in page order with NO filtering and NO
rounding. Failures are loud: a non-float value, a page with no site name, or
a page that yields zero records raises an error instead of silently
producing fewer records.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from html.parser import HTMLParser


class _ReadingsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.site: str | None = None
        self.records: list[dict] = []
        self._in_table = False
        self._table_depth = 0
        self._cur: dict | None = None
        self._in_value_td = False
        self._value_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h1" and "data-site" in a:
            self.site = a["data-site"]
        elif tag == "table":
            if a.get("id") == "readings":
                self._in_table = True
                self._table_depth = 1
            elif self._in_table:
                self._table_depth += 1
        elif tag == "tr" and self._in_table:
            if "data-device" in a and "data-day" in a:
                self._cur = {"device": a["data-device"], "day": a["data-day"]}
            else:
                self._cur = None
        elif tag == "td" and self._in_table and self._cur is not None:
            if a.get("class") == "value":
                self._in_value_td = True
                self._value_buf = []

    def handle_endtag(self, tag):
        if tag == "table" and self._in_table:
            self._table_depth -= 1
            if self._table_depth <= 0:
                self._in_table = False
        elif tag == "td" and self._in_value_td:
            self._in_value_td = False
            if self._cur is not None:
                text = "".join(self._value_buf).strip()
                try:
                    value = float(text)
                except ValueError as exc:
                    raise ValueError(
                        f"non-float value {text!r} for device="
                        f"{self._cur['device']!r} day={self._cur['day']!r}"
                    ) from exc
                self.records.append({
                    "site": self.site,
                    "device": self._cur["device"],
                    "day": self._cur["day"],
                    "value": value,
                })
                self._cur = None
        elif tag == "tr" and self._in_table:
            self._cur = None

    def handle_data(self, data):
        if self._in_value_td:
            self._value_buf.append(data)


def _fetch(url: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def collect(source: dict) -> list[dict]:
    records: list[dict] = []
    for url in source["pages"]:
        html = _fetch(url)
        parser = _ReadingsParser()
        parser.feed(html)
        parser.close()
        if parser.site is None:
            raise ValueError(f"no <h1 data-site> found in page {url!r}")
        if not parser.records:
            raise ValueError(f"no records parsed from page {url!r}")
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
    parent = os.path.dirname(args.out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()
