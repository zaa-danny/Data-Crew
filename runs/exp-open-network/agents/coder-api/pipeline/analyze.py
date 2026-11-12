"""Analyze records into a summary dict.

Contract
--------
``analyze(records, params) -> summary`` where ``params`` provides ``unit``,
``min_value``, ``top_k`` and ``round_digits`` at run time (never hardcoded).

Semantics
---------
* kept = records with ``value >= min_value``; only kept records enter stats.
* ``record_count`` counts ALL records, ``kept_count`` counts kept ones.
* ``total`` = round(sum of RAW kept values, round_digits) - sum first, round once.
* ``mean`` = round(total / kept_count, round_digits) using the ROUNDED total;
  0.0 when nothing is kept.
* ``by_site`` sorted by total DESC, ties by site ASC; sites without kept
  records are omitted.
* ``top_devices`` per (site, device) sorted by total DESC, ties by site ASC
  then device ASC, truncated to ``top_k``.
* ``max`` / ``min`` are the kept records with the largest / smallest value,
  ties broken by the lexicographically smallest (site, device, day).
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Iterable


def _round(value: float, digits: int) -> float:
    return round(value, digits)


def _record_key(rec: dict) -> tuple[str, str, str]:
    return (str(rec["site"]), str(rec["device"]), str(rec["day"]))


def analyze(records: list[dict], params: dict) -> dict:
    unit = params["unit"]
    min_value = float(params["min_value"])
    top_k = int(params["top_k"])
    round_digits = int(params["round_digits"])

    all_records = list(records)
    kept = [r for r in all_records if float(r["value"]) >= min_value]

    record_count = len(all_records)
    kept_count = len(kept)

    sites = {str(r["site"]) for r in kept}
    devices = {(str(r["site"]), str(r["device"])) for r in kept}

    raw_total = 0.0
    for r in kept:
        raw_total += float(r["value"])
    total = _round(raw_total, round_digits)
    mean = _round(total / kept_count, round_digits) if kept_count else 0.0

    # per-site aggregation over raw values
    site_raw: dict[str, float] = {}
    site_count: dict[str, int] = {}
    site_devices: dict[str, set[str]] = {}
    for r in kept:
        site = str(r["site"])
        site_raw[site] = site_raw.get(site, 0.0) + float(r["value"])
        site_count[site] = site_count.get(site, 0) + 1
        site_devices.setdefault(site, set()).add(str(r["device"]))

    by_site = []
    for site in site_raw:
        site_total = _round(site_raw[site], round_digits)
        by_site.append({
            "site": site,
            "total": site_total,
            "mean": _round(site_total / site_count[site], round_digits),
            "device_count": len(site_devices[site]),
            "kept_count": site_count[site],
        })
    by_site.sort(key=lambda d: (-d["total"], d["site"]))

    # per (site, device) aggregation over raw values
    dev_raw: dict[tuple[str, str], float] = {}
    dev_count: dict[tuple[str, str], int] = {}
    for r in kept:
        key = (str(r["site"]), str(r["device"]))
        dev_raw[key] = dev_raw.get(key, 0.0) + float(r["value"])
        dev_count[key] = dev_count.get(key, 0) + 1

    dev_rows = []
    for key, raw in dev_raw.items():
        dev_total = _round(raw, round_digits)
        dev_rows.append({
            "site": key[0],
            "device": key[1],
            "total": dev_total,
            "mean": _round(dev_total / dev_count[key], round_digits),
        })
    dev_rows.sort(key=lambda d: (-d["total"], d["site"], d["device"]))
    top_devices = dev_rows[:top_k]

    max_rec = None
    min_rec = None
    for r in kept:
        if max_rec is None or float(r["value"]) > float(max_rec["value"]) or (
            float(r["value"]) == float(max_rec["value"])
            and _record_key(r) < _record_key(max_rec)
        ):
            max_rec = r
        if min_rec is None or float(r["value"]) < float(min_rec["value"]) or (
            float(r["value"]) == float(min_rec["value"])
            and _record_key(r) < _record_key(min_rec)
        ):
            min_rec = r

    def _plain(rec: dict | None) -> dict | None:
        if rec is None:
            return None
        return {
            "site": str(rec["site"]),
            "device": str(rec["device"]),
            "day": str(rec["day"]),
            "value": float(rec["value"]),
        }

    return {
        "unit": unit,
        "record_count": record_count,
        "kept_count": kept_count,
        "site_count": len(sites),
        "device_count": len(devices),
        "min_value": min_value,
        "round_digits": round_digits,
        "top_k": top_k,
        "total": total,
        "mean": mean,
        "max": _plain(max_rec),
        "min": _plain(min_rec),
        "by_site": by_site,
        "top_devices": top_devices,
    }


def load_records(path: str) -> list[dict]:
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


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

    parent = os.path.dirname(args.out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=False)
        f.write("\n")


if __name__ == "__main__":
    main()
