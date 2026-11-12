"""Collector: fetch site pages and parse table#readings into records.

Contract
--------
Input: ``collect(source)`` where ``source`` is a dict with a ``"pages"`` key
holding a list of URLs (e.g. ``{"kind": "http", "index": "...", "pages": [...]}``).
Each page is fetched with urllib (no proxy) and parsed with html.parser:
the site name comes from ``<h1 data-site="...">`` and every
``<tr data-device=".." data-day="..">`` inside ``table#readings`` is one
record whose value is the text of ``<td class="value">`` as a float.

Output: a list of dicts ``{"site": str, "device": str, "day": str,
"value": float}`` in page order. ALL records are returned -- no filtering,
no rounding, no reordering.

Failures are reported loudly: a fetch error propagates, and any
``tr[data-device][data-day]`` row that lacks a non-empty, numeric
``td.value`` raises ``ValueError`` (with the URL and row identity) instead of
being silently dropped, so the record count never shrinks unnoticed.

CLI: ``python pipeline/collect.py --spec spec.json --out data/records.jsonl``
reads ``spec["source"]``, calls ``collect(spec["source"])`` and writes one
JSON object per line to ``--out`` (creating the parent directory if missing).
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from html.parser import HTMLParser


class _ReadingsParser(HTMLParser):
    def __init__(self, url: str = "") -> None:
        super().__init__()
        self.url = url
        self.site: str | None = None
        self.records: list[dict] = []
        self._in_table = False
        self._table_depth = 0
        self._cur: dict | None = None
        self._in_value = False
        self._value_text: list[str] = []

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
                self._value_text = []
        elif tag == "td" and self._in_table and self._cur is not None:
            if a.get("class") == "value":
                self._in_value = True
                self._value_text = []

    def handle_endtag(self, tag):
        if tag == "table" and self._in_table:
            self._table_depth -= 1
            if self._table_depth <= 0:
                self._in_table = False
        elif tag == "td" and self._in_value:
            self._in_value = False
        elif tag == "tr" and self._in_table and self._cur is not None:
            text = "".join(self._value_text).strip()
            ident = f"site={self.site!r} device={self._cur['device']!r} day={self._cur['day']!r}"
            if not text:
                raise ValueError(
                    f"missing/empty td.value for row {ident} in {self.url!r}"
                )
            try:
                value = float(text)
            except ValueError as exc:
                raise ValueError(
                    f"non-numeric td.value {text!r} for row {ident} in {self.url!r}"
                ) from exc
            self._cur["value"] = value
            self._cur["site"] = self.site
            self.records.append(self._cur)
            self._cur = None
            self._value_text = []

    def handle_data(self, data):
        if self._in_value:
            self._value_text.append(data)


def _fetch(url: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def collect(source: dict) -> list[dict]:
    records: list[dict] = []
    for url in source["pages"]:
        html = _fetch(url)
        parser = _ReadingsParser(url)
        parser.feed(html)
        parser.close()
        for rec in parser.records:
            records.append({
                "site": rec["site"],
                "device": rec["device"],
                "day": rec["day"],
                "value": rec["value"],
            })
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
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()
