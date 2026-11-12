import json
import pathlib

root = pathlib.Path(__file__).resolve().parent
data = json.loads((root / "data.json").read_text(encoding="utf-8"))
print("top-level keys:", sorted(data.keys()))
print("summary keys:", sorted(data["summary"].keys()))
print("records:", len(data["records"]))
print("record sample:", data["records"][0])

for name in ("index.html", "app.js", "style.css", "data.json"):
    p = root / name
    print(name, p.stat().st_size, "bytes")

html = (root / "index.html").read_text(encoding="utf-8")
for needle in ('id="summary"', 'id="chart"', "app.js", "style.css", "<!DOCTYPE html>"):
    print("html contains", needle, ":", needle in html)

js = (root / "app.js").read_text(encoding="utf-8")
print("js fetches data.json:", 'fetch(DATA_URL' in js and 'DATA_URL = "data.json"' in js)
print("js hardcodes site names:", any(s in js for s in ("center", "east", "south")))

src_summary = json.loads((root.parent / "data" / "summary.json").read_text(encoding="utf-8"))
src_records = []
with (root.parent / "data" / "records.jsonl").open(encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line:
            src_records.append(json.loads(line))

print("summary matches source:", data["summary"] == src_summary)
print("records match source:", data["records"] == src_records)
print("record count matches source:", len(data["records"]) == len(src_records))
print("summary record_count matches records:", data["summary"]["record_count"] == len(data["records"]))
print("by_site totals sum to total:",
      round(sum(r["total"] for r in data["summary"]["by_site"]), 2) == data["summary"]["total"])
print("chart data present (by_site non-empty):", len(data["summary"]["by_site"]) > 0)
print("top_devices present:", len(data["summary"]["top_devices"]) > 0)
# end of check
