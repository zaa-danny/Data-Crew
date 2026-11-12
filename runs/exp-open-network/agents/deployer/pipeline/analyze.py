"""Analyze records into a summary dict (see data contract).

Methodology (exact):
  * kept = records with value >= min_value; only kept records enter statistics
  * record_count = all records; kept_count = len(kept)
  * site_count = distinct sites among kept; device_count = distinct (site, device) among kept
  * total = round(sum of RAW kept values, round_digits)  (sum first, round once)
  * mean = round(total / kept_count, round_digits) using the ROUNDED total; 0.0 if nothing kept
  * by_site: {site, total, mean, device_count, kept_count}; total = round(sum raw kept);
    mean = round(site_total / site_kept_count); sort total DESC, ties site ASC; omit empty sites
  * top_devices: per (site, device) {site, device, total, mean}; total = round(sum raw kept);
    mean = round(sum raw / count); sort total DESC, ties site ASC then device ASC; first top_k
  * max / min: kept record with largest / smallest value; ties -> lexicographically smallest
    (site, device, day)
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict


def _round(value: float, digits: int) -> float:
    return round(value, digits)


def _pick_extreme(kept: list[dict], largest: bool) -> dict | None:
    if not kept:
        return None
    best = None
    best_key = None
    for rec in kept:
        value = rec["value"]
        tie = (rec["site"], rec["device"], rec["day"])
        if best is None:
            best, best_key = rec, (value, tie)
            continue
        if largest:
            cand = (-value, tie)
            cur = (-best_key[0], best_key[1])
        else:
            cand = (value, tie)
            cur = (best_key[0], best_key[1])
        if cand < cur:
            best, best_key = rec, (value, tie)
    return {
        "site": best["site"],
        "device": best["device"],
        "day": best["day"],
        "value": best["value"],
    }


def analyze(records: list[dict], params: dict) -> dict:
    unit = params["unit"]
    min_value = params["min_value"]
    top_k = params["top_k"]
    round_digits = params["round_digits"]

    record_count = len(records)
    kept = [r for r in records if r["value"] >= min_value]
    kept_count = len(kept)

    sites = {r["site"] for r in kept}
    devices = {(r["site"], r["device"]) for r in kept}

    raw_total = 0.0
    for r in kept:
        raw_total += r["value"]
    total = _round(raw_total, round_digits)
    mean = _round(total / kept_count, round_digits) if kept_count else 0.0

    site_raw: dict[str, float] = defaultdict(float)
    site_kept: dict[str, int] = defaultdict(int)
    site_devices: dict[str, set] = defaultdict(set)
    dev_raw: dict[tuple, float] = defaultdict(float)
    dev_count: dict[tuple, int] = defaultdict(int)

    for r in kept:
        site = r["site"]
        key = (site, r["device"])
        site_raw[site] += r["value"]
        site_kept[site] += 1
        site_devices[site].add(r["device"])
        dev_raw[key] += r["value"]
        dev_count[key] += 1

    by_site = []
    for site in site_raw:
        st = _round(site_raw[site], round_digits)
        by_site.append({
            "site": site,
            "total": st,
            "mean": _round(st / site_kept[site], round_digits),
            "device_count": len(site_devices[site]),
            "kept_count": site_kept[site],
        })
    by_site.sort(key=lambda d: (-d["total"], d["site"]))

    top_devices = []
    for key in dev_raw:
        site, device = key
        dt = _round(dev_raw[key], round_digits)
        top_devices.append({
            "site": site,
            "device": device,
            "total": dt,
            "mean": _round(dev_raw[key] / dev_count[key], round_digits),
        })
    top_devices.sort(key=lambda d: (-d["total"], d["site"], d["device"]))
    top_devices = top_devices[:top_k]

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
        "max": _pick_extreme(kept, largest=True),
        "min": _pick_extreme(kept, largest=False),
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
    ap.add_argument("--records", default="data/records.jsonl")
    ap.add_argument("--params", default="params.json")
    ap.add_argument("--out", default="data/summary.json")
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
    print("wrote summary: record_count=%d kept_count=%d total=%s"
          % (summary["record_count"], summary["kept_count"], summary["total"]))


if __name__ == "__main__":
    main()
