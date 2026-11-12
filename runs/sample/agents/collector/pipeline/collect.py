"""Collector: fetch site pages and parse table#readings into records.

Contract
--------
Input: ``collect(source)`` where ``source`` is a dict shaped like
``{"kind": "http", "index": "...", "pages": ["http://...", ...]}``. Every URL
in ``source["pages"]`` is fetched with urllib (no proxy) and parsed with the
stdlib html.parser.

Parsing rules:
  - the site name comes from ``<h1 data-site="...">``;
  - every ``<tr data-device=".." data-day="..">`` inside ``table#readings`` is
    one record;
  - the value is the text of ``<td class="value">`` parsed as float.

Output: a list of records ``{"site": str, "device": str, "day": str,
"value": float}`` in page order. ALL records are returned - no filtering,
no rounding, no ordering assumptions.

Failure handling: fetch errors (URLError, HTTPError, ...) and parse failures
(a page that yields no records, e.g. missing <h1 data-site>, missing
table#readings, or unparseable value text) are reported loudly by raising
``CollectError`` so the CLI exits non-zero instead of silently writing a
truncated file.

CLI: ``python pipeline/collect.py --spec spec.json --out data/records.jsonl``
reads ``spec["source"]`` and writes one JSON object per line to ``--out``
(creating the parent directory if needed). It prints per-page record counts
and exits non-zero on any fetch/parse failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from html.parser import HTMLParser


class CollectError(RuntimeError):
    """Raised when a page cannot be fetched or parsed into records."""


class _ReadingsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.site: str | None = None
        self.records: list[dict] = []
        self._in_table = False
        self._table_depth = 0
        self._cur: dict | None = None
        self._in_value = False
        self._value_text: list[str] = []
        self.bad_values: list[str] = []

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
            if "data-device" in a and "data-day" in a:
                self._cur = {"device": a["data-device"], "day": a["data-day"]}
                self._value_text = []
            else:
                self._cur = None
        elif tag == "td" and self._cur is not None:
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
        elif tag == "tr" and self._cur is not None:
            if self._value_text:
                text = "".join(self._value_text).strip()
                if text:
                    try:
                        self._cur["value"] = float(text)
                    except ValueError:
                        self.bad_values.append(text)
                    else:
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
        with opener.open(url) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - report any fetch failure loudly
        raise CollectError(f"failed to fetch {url}: {exc}") from exc
    return raw.decode("utf-8", errors="replace")


def collect(source: dict) -> list[dict]:
    pages = source.get("pages", [])
    if not pages:
        raise CollectError("source has no pages to collect")
    out: list[dict] = []
    for url in pages:
        html = _fetch(url)
        p = _ReadingsParser()
        try:
            p.feed(html)
            p.close()
        except Exception as exc:  # noqa: BLE001 - report parse failure loudly
            raise CollectError(f"failed to parse {url}: {exc}") from exc
        if p.bad_values:
            raise CollectError(
                f"unparseable value text on {url}: {p.bad_values[:5]}"
            )
        if not p.records:
            raise CollectError(
                f"no records parsed from {url} "
                f"(site={p.site!r}; missing <h1 data-site> or table#readings?)"
            )
        for r in p.records:
            out.append({
                "site": r["site"],
                "device": r["device"],
                "day": r["day"],
                "value": r["value"],
            })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with open(args.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)
    try:
        records = collect(spec["source"])
    except CollectError as exc:
        print(f"collect error: {exc}", file=sys.stderr)
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
