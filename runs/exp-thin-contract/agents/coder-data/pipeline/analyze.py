"""Analyze: compute summary statistics over collected records.

Methodology (exact)
-------------------
* Filter: keep only records whose ``value`` is >= ``params["min_value"]``
  (records with a non-numeric / missing value are dropped).
* Sums are computed on RAW (unrounded) values; each emitted aggregate total is
  rounded to ``params["round_digits"]``.
* ``mean`` is computed from the ROUNDED total as ``round(total / count,
  digits)`` where ``total`` is the already-rounded total. This applies to the
  overall mean and to every per-site / per-device / top-device mean.
* ``by_site`` is a LIST of objects with fields ``site, total, mean,
  device_count, kept_count``, sorted by ``(-total, site)`` (total descending,
  then site name ascending).
* ``by_device`` is a LIST of objects with fields ``device, site, total, mean,
  count``, sorted by ``(-total, device)`` (total descending, then device name
  ascending).
* ``top_devices`` is the first ``params["top_k"]`` devices from the
  ``by_device`` ordering, each with fields ``device, site, total, mean, count``,
  ordered by ``(-total, site, device)`` (total descending, then site name
  ascending, then device name ascending).
* ``largest`` / ``smallest`` are the single kept record with the max / min
  value; ties are broken by ``(site, device, day)`` ascending.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any


def _num(value: Any) -> float | None:
    """Coerce a record value to float, or None if it is not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _round(value: float, digits: int) -> float:
    return round(value, digits)


def _record_key(rec: dict) -> tuple:
    return (
        str(rec.get("site") or ""),
        str(rec.get("device") or ""),
        str(rec.get("day") or ""),
    )


def _pick(rec: dict, digits: int) -> dict:
    return {
        "site": rec.get("site"),
        "device": rec.get("device"),
        "day": rec.get("day"),
        "value": _round(float(rec["value"]), digits),
    }


def _mean_from_rounded_total(rounded_total: float, count: int, digits: int) -> float:
    """Mean = round(total / count, digits) using the ROUNDED total."""
    if not count:
        return 0.0
    return _round(rounded_total / count, digits)


def analyze(records: list[dict], params: dict) -> dict:
    """Compute the summary dict for ``records`` under ``params``."""
    params = params or {}
    min_value = float(params.get("min_value", 0.0))
    top_k = int(params.get("top_k", 0))
    digits = int(params.get("round_digits", 2))
    unit = params.get("unit", "count")

    record_count = len(records)

    kept: list[dict] = []
    for rec in records:
        value = _num(rec.get("value"))
        if value is None:
            continue
        if value < min_value:
            continue
        kept.append(
            {
                "site": rec.get("site"),
                "device": rec.get("device"),
                "day": rec.get("day"),
                "value": value,
            }
        )

    kept_count = len(kept)
    raw_total = sum(r["value"] for r in kept)
    total = _round(raw_total, digits)
    mean = _mean_from_rounded_total(total, kept_count, digits)

    # Per-site aggregates (raw sums, rounded at emit time).
    site_sums: dict[str, float] = {}
    site_counts: dict[str, int] = {}
    site_devices: dict[str, set] = {}
    device_sums: dict[str, float] = {}
    device_counts: dict[str, int] = {}
    device_site: dict[str, str] = {}
    for r in kept:
        site = str(r["site"] or "")
        device = str(r["device"] or "")
        site_sums[site] = site_sums.get(site, 0.0) + r["value"]
        site_counts[site] = site_counts.get(site, 0) + 1
        site_devices.setdefault(site, set()).add(device)
        device_sums[device] = device_sums.get(device, 0.0) + r["value"]
        device_counts[device] = device_counts.get(device, 0) + 1
        device_site.setdefault(device, site)

    # by_site: list sorted by (-total, site).
    by_site: list[dict] = []
    for site in site_sums:
        count = site_counts[site]
        site_total = _round(site_sums[site], digits)
        by_site.append(
            {
                "site": site,
                "total": site_total,
                "mean": _mean_from_rounded_total(site_total, count, digits),
                "device_count": len(site_devices.get(site, ())),
                "kept_count": count,
            }
        )
    by_site.sort(key=lambda a: (-a["total"], a["site"]))

    # by_device: list sorted by (-total, device).
    by_device: list[dict] = []
    for device in device_sums:
        count = device_counts[device]
        dev_total = _round(device_sums[device], digits)
        by_device.append(
            {
                "device": device,
                "site": device_site.get(device, ""),
                "total": dev_total,
                "mean": _mean_from_rounded_total(dev_total, count, digits),
                "count": count,
            }
        )
    by_device.sort(key=lambda a: (-a["total"], a["device"]))

    # top_devices: first top_k by (-total, site, device).
    ordered_devices = sorted(
        by_device, key=lambda a: (-a["total"], a["site"], a["device"])
    )
    top_devices = [
        {
            "device": item["device"],
            "site": item["site"],
            "total": item["total"],
            "mean": item["mean"],
            "count": item["count"],
        }
        for item in ordered_devices[: max(top_k, 0)]
    ]

    largest = None
    smallest = None
    if kept:
        largest = _pick(max(kept, key=lambda r: (r["value"], _neg_key(r))), digits)
        smallest = _pick(min(kept, key=lambda r: (r["value"], _record_key(r))), digits)

    return {
        "unit": unit,
        "min_value": min_value,
        "round_digits": digits,
        "top_k": top_k,
        "record_count": record_count,
        "kept_count": kept_count,
        "dropped_count": record_count - kept_count,
        "total": total,
        "mean": mean,
        "by_site": by_site,
        "by_device": by_device,
        "top_devices": top_devices,
        "largest": largest,
        "smallest": smallest,
    }


def _neg_key(rec: dict) -> tuple:
    """Tie-break helper so max() prefers the lexicographically smallest key."""
    site, device, day = _record_key(rec)
    return tuple(_Invert(s) for s in (site, device, day))


class _Invert:
    """Wrapper that inverts ordering for string tie-breaks in max()."""

    __slots__ = ("s",)

    def __init__(self, s: str) -> None:
        self.s = s

    def __lt__(self, other: "_Invert") -> bool:
        return self.s > other.s

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Invert) and self.s == other.s


def _load_records(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze records into a summary.")
    ap.add_argument("--records", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    records = _load_records(args.records)
    with open(args.params, "r", encoding="utf-8") as fh:
        params = json.load(fh)

    summary = analyze(records, params)

    parent = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


if __name__ == "__main__":
    main()
