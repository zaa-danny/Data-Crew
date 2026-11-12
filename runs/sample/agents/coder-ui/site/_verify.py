import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

for name in ("index.html", "app.js", "style.css", "data.json"):
    p = os.path.join(HERE, name)
    print(name, os.path.getsize(p), "bytes")

with open(os.path.join(HERE, "data.json"), "r", encoding="utf-8") as f:
    payload = json.load(f)
print("top keys:", sorted(payload.keys()))
print("records:", len(payload["records"]))
s = payload["summary"]
print("summary keys:", sorted(s.keys()))
print("by_site:", [(r["site"], r["total"]) for r in s["by_site"]])
print("top_devices:", [(d["site"], d["device"], d["total"]) for d in s["top_devices"]])

html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
print("has #summary:", 'id="summary"' in html)
print("has #chart:", 'id="chart"' in html)
print("refs app.js:", "app.js" in html, "refs style.css:", "style.css" in html)

js = open(os.path.join(HERE, "app.js"), encoding="utf-8").read()
print("fetch data.json:", 'fetch("data.json"' in js)
for token in ("center", "east", "south", "1100.9", "2005.8"):
    print("hardcoded", token, ":", token in js or token in html)
<<<
