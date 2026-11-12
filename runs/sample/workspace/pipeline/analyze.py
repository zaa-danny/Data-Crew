"""Analyze: turn raw records into a summary dict.

Contract
--------
``analyze(records, params) -> dict`` where ``params`` has ``unit``,
``min_value``, ``top_k`` and ``round_digits`` (read at run time, never
hardcoded).

Methodology (exact):
  - kept = records with ``value >= min_value``; only kept records enter
    statistics.
  - record_count = len(records) (ALL records, before filtering);
    kept_count = len(kept).
  - site_count = distinct sites among kept;
    device_count = distinct (site, device) pairs among kept.
  - total = round(sum of RAW kept values, round_digits) - sum first, round once.
  - mean = round(total / kept_count, round_digits) using the ROUNDED total;
    0.0 when nothing is kept.
  - by_site: [{site, total, mean, device_count, kept_count}] with
    total = round(sum raw kept for site), mean = round(site_total / site_kept),
    sorted by total DESCENDING, ties by site ASCENDING; sites with no kept
    record are omitted.
  - top_devices: per (site, device) {site, device, total, mean} with
    total = round(sum raw kept), mean = round(sum raw / count), sorted by
    total DESCENDING, ties by site ASC, then device ASC; first top_k kept.
  - max / min: the kept record {site, device, day, value} with the largest /
    smallest value; ties resolved by the lexicographically smallest
    (site, device, day). ``None`` when nothing is kept.

CLI: ``python pipeline/analyze.py --records data/records.jsonl
--params params.json --out data/summary.json``
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict


def _round(x: float, digits: int) -> float:
    return round(x, digits)


def _record_key(rec: dict) -> tuple[str, str, str]:
    return (rec["site"], rec["device"], rec["day"])


def analyze(records: list[dict], params: dict) -> dict:
    unit = params["unit"]
    min_value = params["min_value"]
    top_k = params["top_k"]
    round_digits = params["round_digits"]

    kept = [r for r in records if r["value"] >= min_value]

    record_count = len(records)
    kept_count = len(kept)

    sites = {r["site"] for r in kept}
    devices = {(r["site"], r["device"]) for r in kept}

    raw_total = 0.0
    for r in kept:
        raw_total += r["value"]
    total = _round(raw_total, round_digits)
    mean = _round(total / kept_count, round_digits) if kept_count else 0.0

    # per-site aggregation over raw values
    site_raw: dict[str, float] = defaultdict(float)
    site_kept: dict[str, int] = defaultdict(int)
    site_devices: dict[str, set] = defaultdict(set)
    for r in kept:
        site_raw[r["site"]] += r["value"]
        site_kept[r["site"]] += 1
        site_devices[r["site"]].add(r["device"])

    by_site = []
    for site in site_raw:
        s_total = _round(site_raw[site], round_digits)
        s_count = site_kept[site]
        by_site.append({
            "site": site,
            "total": s_total,
            "mean": _round(s_total / s_count, round_digits) if s_count else 0.0,
            "device_count": len(site_devices[site]),
            "kept_count": s_count,
        })
    by_site.sort(key=lambda d: (-d["total"], d["site"]))

    # per (site, device) aggregation over raw values
    dev_raw: dict[tuple[str, str], float] = defaultdict(float)
    dev_count: dict[tuple[str, str], int] = defaultdict(int)
    for r in kept:
        key = (r["site"], r["device"])
        dev_raw[key] += r["value"]
        dev_count[key] += 1

    dev_rows = []
    for (site, device), raw in dev_raw.items():
        d_total = _round(raw, round_digits)
        cnt = dev_count[(site, device)]
        dev_rows.append({
            "site": site,
            "device": device,
            "total": d_total,
            "mean": _round(d_total / cnt, round_digits) if cnt else 0.0,
        })
    dev_rows.sort(key=lambda d: (-d["total"], d["site"], d["device"]))
    top_devices = dev_rows[:top_k]

    max_rec = None
    min_rec = None
    if kept:
        max_rec = min(kept, key=lambda r: (-r["value"],) + _record_key(r))
        min_rec = min(kept, key=lambda r: (r["value"],) + _record_key(r))
        max_rec = {
            "site": max_rec["site"],
            "device": max_rec["device"],
            "day": max_rec["day"],
            "value": max_rec["value"],
        }
        min_rec = {
            "site": min_rec["site"],
            "device": min_rec["device"],
            "day": min_rec["day"],
            "value": min_rec["value"],
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
        "max": max_rec,
        "min": min_rec,
        "by_site": by_site,
        "top_devices": top_devices,
    }


def load_records(path: str) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
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

    parent = os.path.dirname(os.path.abspath(args.out))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(
        f"analyzed {summary['record_count']} records "
        f"({summary['kept_count']} kept) -> {args.out}"
    )


if __name__ == "__main__":
    main()
