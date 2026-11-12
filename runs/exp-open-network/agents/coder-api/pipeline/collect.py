"""Collector: fetch site pages and parse table#readings into records.

Contract
--------
Input: ``collect(source)`` where ``source`` is the ``source`` object from
spec.json, shaped like::

    {"kind": "http", "index": "...", "pages": ["http://host/sites/x.html", ...]}

Each page is fetched with urllib (no proxy) and parsed with html.parser:
  * the site name comes from ``<h1 data-site="...">``;
  * every ``<tr data-device=".." data-day="..">`` inside ``table#readings``
    is one record, its value being the text of ``<td class="value">`` as float.

Output: a list of dicts, one per row, in page order::

    {"site": str, "device": str, "day": str, "value": float}

No filtering, no rounding, no reordering: every parsed row is returned.

Errors are loud: a fetch failure, a page without ``h1[data-site]``, or a row
without a parseable value cell raises ``CollectError`` (the CLI exits non-zero)
instead of silently dropping records.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from html.parser import HTMLParser


class CollectError(RuntimeError):
    """Raised when a page cannot be fetched or parsed as expected."""


class _ReadingsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.site: str | None = None
        self.records: list[dict] = []
        self._in_table = False
        self._table_depth = 0
        self._cur: dict | None = None
        self._in_value = False
        self._value_text: list[str] = []
        self._row_has_value = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h1" and self.site is None and "data-site" in a:
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
                self._row_has_value = False
            else:
                self._cur = None
        elif tag == "td" and self._cur is not None:
            if "value" in (a.get("class") or "").split():
                self._in_value = True
                self._value_text = []

    def handle_endtag(self, tag):
        if tag == "table" and self._in_table:
            self._table_depth -= 1
            if self._table_depth <= 0:
                self._in_table = False
        elif tag == "td" and self._in_value:
            self._in_value = False
            self._row_has_value = True
        elif tag == "tr" and self._cur is not None:
            text = "".join(self._value_text).strip()
            if not self._row_has_value or not text:
                raise CollectError(
                    "row without a value cell: site=%r device=%r day=%r"
                    % (self.site, self._cur.get("device"), self._cur.get("day"))
                )
            try:
                value = float(text)
            except ValueError as exc:
                raise CollectError(
                    "non-numeric value %r for site=%r device=%r day=%r"
                    % (text, self.site, self._cur.get("device"), self._cur.get("day"))
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
    try:
        with opener.open(url, timeout=30) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - report loudly, never skip
        raise CollectError("failed to fetch %s: %s" % (url, exc)) from exc
    return raw.decode("utf-8", errors="replace")


def collect(source: dict) -> list[dict]:
    out: list[dict] = []
    for url in source.get("pages", []):
        parser = _ReadingsParser()
        try:
            parser.feed(_fetch(url))
            parser.close()
        except CollectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CollectError("failed to parse %s: %s" % (url, exc)) from exc
        if parser.site is None:
            raise CollectError("no h1[data-site] found in %s" % url)
        for rec in parser.records:
            out.append({
                "site": rec["site"],
                "device": rec["device"],
                "day": rec["day"],
                "value": rec["value"],
            })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="spec.json")
    ap.add_argument("--out", default="data/records.jsonl")
    args = ap.parse_args()
    with open(args.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)
    try:
        records = collect(spec["source"])
    except CollectError as exc:
        print("collect error: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
    parent = os.path.dirname(os.path.abspath(args.out))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} records to {args.out}")


if __name__ == "__main__":
    main()
