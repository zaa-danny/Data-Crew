"""Collector: fetch site pages and parse table#readings into records.

Contract
--------
Input (``collect(source)``):
    ``source`` is a dict shaped like::

        {"kind": "http", "index": "...", "pages": ["http://...", ...]}

    Only ``source["pages"]`` is used: an ordered list of page URLs to fetch.

Output:
    A ``list[dict]`` in page order, one dict per ``<tr data-device data-day>``
    row inside ``table#readings``, with the shape::

        {"site": <h1 data-site>, "device": <tr data-device>,
         "day": <tr data-day>, "value": <float from td.value>}

    ALL records are returned in page order with NO filtering.

Failure policy (fail loudly):
    - Any fetch error propagates (wrapped with the offending URL).
    - A page whose ``table#readings`` yields no records raises ``ValueError``.
    - A value cell that cannot be parsed as ``float`` raises ``ValueError``
      instead of being silently skipped.
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
        self._in_value = False
        self._value_text: list[str] = []

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
            else:
                self._cur = None
        elif tag == "td" and self._in_table and self._cur is not None:
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
            if self._cur is not None:
                text = "".join(self._value_text).strip()
                try:
                    value = float(text)
                except ValueError as exc:
                    raise ValueError(
                        f"unparseable value {text!r} for device "
                        f"{self._cur.get('device')!r} day {self._cur.get('day')!r}"
                    ) from exc
                self._cur["value"] = value
        elif tag == "tr" and self._in_table and self._cur is not None:
            if "value" not in self._cur:
                raise ValueError(
                    f"row for device {self._cur.get('device')!r} day "
                    f"{self._cur.get('day')!r} has no td.value cell"
                )
            self.records.append(
                {
                    "site": self.site,
                    "device": self._cur["device"],
                    "day": self._cur["day"],
                    "value": self._cur["value"],
                }
            )
            self._cur = None

    def handle_data(self, data):
        if self._in_value:
            self._value_text.append(data)


def _fetch(url: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=30) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - re-raise loudly with context
        raise RuntimeError(f"failed to fetch {url}: {exc}") from exc
    return raw.decode("utf-8", errors="replace")


def collect(source: dict) -> list[dict]:
    records: list[dict] = []
    for url in source.get("pages", []):
        html = _fetch(url)
        parser = _ReadingsParser()
        parser.feed(html)
        parser.close()
        if not parser.records:
            raise ValueError(f"no records parsed from table#readings at {url}")
        records.extend(parser.records)
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.spec, "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    records = collect(spec["source"])

    parent = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(parent, exist_ok=True)

    with open(args.out, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
