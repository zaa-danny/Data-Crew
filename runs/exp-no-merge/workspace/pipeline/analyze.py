"""Analyze records into a summary dict (stdlib only).

Methodology (exact, must not be changed):
  * kept = records with value >= min_value; only kept records enter statistics.
  * record_count counts ALL records (before filtering); kept_count = len(kept).
  * site_count = distinct sites among kept; device_count = distinct (site, device) among kept.
  * total = round(sum of RAW kept values, round_digits) -- sum raw first, round once at the end.
  * mean = round(total / kept_count, round_digits) using the ROUNDED total; 0.0 when nothing is kept.
  * by_site: one entry per site with a kept record (sites with no kept record are omitted);
    site total = round(sum of raw kept values for that site, round_digits);
    site mean = round(site_total / site_kept_count, round_digits);
    ordering: total DESCENDING, ties broken by site ASCENDING.
  * top_devices: one entry per (site, device) with kept records;
    total = round(sum of raw kept values, round_digits); mean = round(sum / count, round_digits);
    ordering: total DESCENDING, ties by site ASCENDING, then device ASCENDING;
    only the first top_k entries are kept.
  * max / min: the kept record {site, device, day, value} with the largest / smallest value;
    ties resolved by the lexicographically smallest (site, device, day).
  * No threshold, precision, top_k, site name, device name or ordering is hardcoded: all come
    from params at run time.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any


def _round(x: float, digits: int) -> float:
    return round(float(x), int(digits))


def _load_jsonl(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def analyze(records: list[dict], params: dict) -> dict:
    unit = params.get("unit", "count")
    min_value = float(params.get("min_value", 0.0))
    top_k = int(params.get("top_k", 0))
    round_digits = int(params.get("round_digits", 2))

    record_count = len(records)
    kept = [r for r in records if float(r["value"]) >= min_value]
    kept_count = len(kept)

    sites = {r["site"] for r in kept}
    devices = {(r["site"], r["device"]) for r in kept}

    raw_total = sum(float(r["value"]) for r in kept)
    total = _round(raw_total, round_digits)
    mean = _round(total / kept_count, round_digits) if kept_count else 0.0

    # by_site
    site_raw: dict[str, float] = {}
    site_count: dict[str, int] = {}
    site_devices: dict[str, set] = {}
    for r in kept:
        s = r["site"]
        site_raw[s] = site_raw.get(s, 0.0) + float(r["value"])
        site_count[s] = site_count.get(s, 0) + 1
        site_devices.setdefault(s, set()).add(r["device"])

    by_site = []
    for s, raw in site_raw.items():
        st = _round(raw, round_digits)
        cnt = site_count[s]
        by_site.append(
            {
                "site": s,
                "total": st,
                "mean": _round(st / cnt, round_digits) if cnt else 0.0,
                "device_count": len(site_devices[s]),
                "kept_count": cnt,
            }
        )
    by_site.sort(key=lambda d: (-d["total"], d["site"]))

    # top_devices
    dev_raw: dict[tuple[str, str], float] = {}
    dev_count: dict[tuple[str, str], int] = {}
    for r in kept:
        key = (r["site"], r["device"])
        dev_raw[key] = dev_raw.get(key, 0.0) + float(r["value"])
        dev_count[key] = dev_count.get(key, 0) + 1

    dev_rows = []
    for (s, d), raw in dev_raw.items():
        dt = _round(raw, round_digits)
        cnt = dev_count[(s, d)]
        dev_rows.append(
            {
                "site": s,
                "device": d,
                "total": dt,
                "mean": _round(dt / cnt, round_digits) if cnt else 0.0,
            }
        )
    dev_rows.sort(key=lambda d: (-d["total"], d["site"], d["device"]))
    top_devices = dev_rows[:top_k] if top_k > 0 else []

    # max / min with tie-break on (site, device, day) ascending
    def _pick(rec: dict) -> dict:
        return {
            "site": rec["site"],
            "device": rec["device"],
            "day": rec["day"],
            "value": float(rec["value"]),
        }

    max_rec: Any = None
    min_rec: Any = None
    if kept:
        max_rec = _pick(
            min(kept, key=lambda r: (-float(r["value"]), r["site"], r["device"], r["day"]))
        )
        min_rec = _pick(
            min(kept, key=lambda r: (float(r["value"]), r["site"], r["device"], r["day"]))
        )

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analyze records into summary.json")
    ap.add_argument("--records", default="data/records.jsonl")
    ap.add_argument("--params", default="params.json")
    ap.add_argument("--out", default="data/summary.json")
    args = ap.parse_args(argv)

    records = _load_jsonl(args.records)
    with open(args.params, "r", encoding="utf-8") as fh:
        params = json.load(fh)
    summary = analyze(records, params)

    parent = os.path.dirname(os.path.abspath(args.out))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
