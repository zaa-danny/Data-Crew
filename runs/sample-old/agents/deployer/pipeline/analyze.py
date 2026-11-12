"""Analyzer: compute summary statistics from records according to params."""
from __future__ import annotations

import argparse
import json


def _round(x: float, digits: int) -> float:
    return round(x, digits)


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
    site_count = len(sites)
    device_count = len(devices)

    raw_total = sum(r["value"] for r in kept)
    total = _round(raw_total, round_digits)
    mean = _round(total / kept_count, round_digits) if kept_count else 0.0

    # by_site
    site_raw: dict[str, float] = {}
    site_kept: dict[str, int] = {}
    site_devices: dict[str, set] = {}
    for r in kept:
        s = r["site"]
        site_raw[s] = site_raw.get(s, 0.0) + r["value"]
        site_kept[s] = site_kept.get(s, 0) + 1
        site_devices.setdefault(s, set()).add(r["device"])

    by_site = []
    for s in site_raw:
        st = _round(site_raw[s], round_digits)
        by_site.append(
            {
                "site": s,
                "total": st,
                "mean": _round(st / site_kept[s], round_digits),
                "device_count": len(site_devices[s]),
                "kept_count": site_kept[s],
            }
        )
    by_site.sort(key=lambda d: (-d["total"], d["site"]))

    # top_devices
    dev_raw: dict[tuple, float] = {}
    dev_cnt: dict[tuple, int] = {}
    for r in kept:
        key = (r["site"], r["device"])
        dev_raw[key] = dev_raw.get(key, 0.0) + r["value"]
        dev_cnt[key] = dev_cnt.get(key, 0) + 1

    dev_list = []
    for (s, d), raw in dev_raw.items():
        dt = _round(raw, round_digits)
        dev_list.append(
            {
                "site": s,
                "device": d,
                "total": dt,
                "mean": _round(raw / dev_cnt[(s, d)], round_digits),
            }
        )
    dev_list.sort(key=lambda d: (-d["total"], d["site"], d["device"]))
    top_devices = dev_list[:top_k]

    # max / min
    def _tie_key(r: dict):
        return (r["site"], r["device"], r["day"])

    max_rec = None
    min_rec = None
    for r in kept:
        if max_rec is None or r["value"] > max_rec["value"] or (
            r["value"] == max_rec["value"] and _tie_key(r) < _tie_key(max_rec)
        ):
            max_rec = r
        if min_rec is None or r["value"] < min_rec["value"] or (
            r["value"] == min_rec["value"] and _tie_key(r) < _tie_key(min_rec)
        ):
            min_rec = r

    def _rec(r):
        if r is None:
            return None
        return {"site": r["site"], "device": r["device"], "day": r["day"], "value": r["value"]}

    return {
        "unit": unit,
        "record_count": record_count,
        "kept_count": kept_count,
        "site_count": site_count,
        "device_count": device_count,
        "min_value": min_value,
        "round_digits": round_digits,
        "top_k": top_k,
        "total": total,
        "mean": mean,
        "max": _rec(max_rec),
        "min": _rec(min_rec),
        "by_site": by_site,
        "top_devices": top_devices,
    }


def _load_records(path: str) -> list[dict]:
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
    records = _load_records(args.records)
    with open(args.params, "r", encoding="utf-8") as f:
        params = json.load(f)
    summary = analyze(records, params)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
