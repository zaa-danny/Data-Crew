import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
for name in ("index.html", "app.js", "style.css", "data.json"):
    p = os.path.join(HERE, name)
    print(name, os.path.getsize(p), "bytes")

with open(os.path.join(HERE, "data.json"), encoding="utf-8") as f:
    payload = json.load(f)
print("keys:", sorted(payload.keys()))
print("records:", len(payload["records"]))
s = payload["summary"]
print("by_site:", [(r["site"], r["total"]) for r in s["by_site"]])
print("top_devices:", [(d["site"], d["device"]) for d in s["top_devices"]])

html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
print("summary div:", 'id="summary"' in html, "chart div:", 'id="chart"' in html)
print("refs:", "app.js" in html, "style.css" in html)

js = open(os.path.join(HERE, "app.js"), encoding="utf-8").read()
print("fetch:", 'fetch("data.json"' in js)
for tok in ("center", "east", "south", "1100.9", "2005.8"):
    print("hardcoded", tok, ":", tok in js or tok in html)
