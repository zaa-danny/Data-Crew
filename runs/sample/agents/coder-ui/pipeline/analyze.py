"""Analyzer: turn raw records into the summary document.

Contract
--------
``analyze(records, params) -> summary`` where ``params`` provides ``unit``,
``min_value``, ``top_k`` and ``round_digits`` (read at run time, never
hardcoded).

Semantics
---------
* kept = records with ``value >= min_value``; only kept records enter the
  statistics.
* ``record_count`` counts ALL records, ``kept_count`` counts kept ones.
* ``site_count`` = distinct sites among kept; ``device_count`` = distinct
  ``(site, device)`` pairs among kept.
* ``total`` = ``round(sum(raw kept values), round_digits)`` -- raw values are
  summed first and rounded exactly once at the end.
* ``mean`` = ``round(total / kept_count, round_digits)`` using the ROUNDED
  total; ``0.0`` when nothing is kept.
* ``by_site``: one entry per site with at least one kept record,
  ``{site, total, mean, device_count, kept_count}``; sorted by ``total``
  descending, ties by ``site`` ascending.
* ``top_devices``: per ``(site, device)`` with at least one kept record,
  ``{site, device, total, mean}``; sorted by ``total`` descending, ties by
  ``site`` ascending then ``device`` ascending; truncated to ``top_k``.
* ``max`` / ``min``: the kept record ``{site, device, day, value}`` with the
  largest / smallest value; ties resolved by the lexicographically smallest
  ``(site, device, day)``.

CLI: ``python pipeline/analyze.py --records data/records.jsonl
--params params.json --out data/summary.json``
"""
from __future__ import annotations

import argparse
import json
import os

_RECORD_FIELDS = ("site", "device", "day", "value")


def _round(value: float, digits: int) -> float:
    return round(value, digits)


def _pick_extreme(kept: list[dict], largest: bool) -> dict | None:
    if not kept:
        return None
    best = None
    best_key = None
    for rec in kept:
        value = rec["value"]
        # primary: value (descending for max, ascending for min)
        # secondary: lexicographically smallest (site, device, day)
        key = ((-value) if largest else value, rec["site"], rec["device"], rec["day"])
        if best_key is None or key < best_key:
            best_key = key
            best = rec
    return {field: best[field] for field in _RECORD_FIELDS}


def analyze(records: list[dict], params: dict) -> dict:
    unit = params["unit"]
    min_value = params["min_value"]
    top_k = params["top_k"]
    round_digits = params["round_digits"]

    kept = [rec for rec in records if rec["value"] >= min_value]
    kept_count = len(kept)

    total = _round(sum(rec["value"] for rec in kept), round_digits)
    mean = _round(total / kept_count, round_digits) if kept_count else 0.0

    site_raw: dict[str, list[float]] = {}
    device_raw: dict[tuple[str, str], list[float]] = {}
    for rec in kept:
        site_raw.setdefault(rec["site"], []).append(rec["value"])
        device_raw.setdefault((rec["site"], rec["device"]), []).append(rec["value"])

    by_site = []
    for site, values in site_raw.items():
        site_total = _round(sum(values), round_digits)
        site_count = len(values)
        by_site.append({
            "site": site,
            "total": site_total,
            "mean": _round(site_total / site_count, round_digits),
            "device_count": len({dev for (s, dev) in device_raw if s == site}),
            "kept_count": site_count,
        })
    by_site.sort(key=lambda row: (-row["total"], row["site"]))

    devices = []
    for (site, device), values in device_raw.items():
        dev_total = _round(sum(values), round_digits)
        devices.append({
            "site": site,
            "device": device,
            "total": dev_total,
            "mean": _round(dev_total / len(values), round_digits),
        })
    devices.sort(key=lambda row: (-row["total"], row["site"], row["device"]))
    top_devices = devices[:top_k]

    return {
        "unit": unit,
        "record_count": len(records),
        "kept_count": kept_count,
        "site_count": len(site_raw),
        "device_count": len(device_raw),
        "min_value": min_value,
        "round_digits": round_digits,
        "top_k": top_k,
        "total": total,
        "mean": mean,
        "max": _pick_extreme(kept, largest=True),
        "min": _pick_extreme(kept, largest=False),
        "by_site": by_site,
        "top_devices": top_devices,
    }


def load_records(path: str) -> list[dict]:
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    records = load_records(args.records)
    with open(args.params, "r", encoding="utf-8") as f:
        params = json.load(f)
    summary = analyze(records, params)

    parent = os.path.dirname(os.path.abspath(args.out))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=False)
        f.write("\n")


if __name__ == "__main__":
    main()
