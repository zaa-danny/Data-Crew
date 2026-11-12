"use strict";

(function () {
  var summaryEl = document.getElementById("summary");
  var chartEl = document.getElementById("chart");

  function fmt(value) {
    if (value === null || value === undefined) return "\u2014";
    if (typeof value === "number") {
      return Number.isInteger(value) ? String(value) : value.toFixed(2);
    }
    return String(value);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function showError(message) {
    if (summaryEl) summaryEl.replaceChildren(el("p", "error", message));
    if (chartEl) chartEl.replaceChildren(el("p", "error", message));
  }

  function renderStats(summary) {
    var grid = el("ul", "stat-grid");
    var stats = [
      ["Unit", summary.unit],
      ["Records", summary.record_count],
      ["Kept", summary.kept_count],
      ["Total", summary.total],
      ["Mean", summary.mean],
      ["Max", summary.max ? summary.max.value : null],
      ["Min", summary.min ? summary.min.value : null]
    ];
    stats.forEach(function (pair) {
      var item = el("li", "stat");
      item.appendChild(el("span", "label", pair[0]));
      item.appendChild(el("span", "value", fmt(pair[1])));
      grid.appendChild(item);
    });
    return grid;
  }

  function renderExtremes(summary) {
    var wrap = document.createElement("div");
    var table = document.createElement("table");
    table.appendChild(el("caption", null, "Extreme records"));
    var thead = document.createElement("thead");
    var hr = document.createElement("tr");
    ["Kind", "Site", "Device", "Day", "Value"].forEach(function (h, i) {
      hr.appendChild(el("th", i === 4 ? "num" : null, h));
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    [["Max", summary.max], ["Min", summary.min]].forEach(function (pair) {
      var rec = pair[1];
      var tr = document.createElement("tr");
      tr.appendChild(el("td", null, pair[0]));
      tr.appendChild(el("td", null, rec ? rec.site : "\u2014"));
      tr.appendChild(el("td", null, rec ? rec.device : "\u2014"));
      tr.appendChild(el("td", null, rec ? rec.day : "\u2014"));
      tr.appendChild(el("td", "num", rec ? fmt(rec.value) : "\u2014"));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function renderBySite(summary) {
    var table = document.createElement("table");
    table.appendChild(el("caption", null, "By site"));
    var thead = document.createElement("thead");
    var hr = document.createElement("tr");
    [["Site", false], ["Total", true], ["Mean", true], ["Devices", true], ["Kept", true]]
      .forEach(function (pair) {
        hr.appendChild(el("th", pair[1] ? "num" : null, pair[0]));
      });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    var sites = Array.isArray(summary.by_site) ? summary.by_site : [];
    if (sites.length === 0) {
      var emptyTr = document.createElement("tr");
      var emptyTd = el("td", null, "No sites with kept records.");
      emptyTd.colSpan = 5;
      emptyTr.appendChild(emptyTd);
      tbody.appendChild(emptyTr);
    }
    sites.forEach(function (row) {
      var tr = document.createElement("tr");
      tr.appendChild(el("td", null, row.site));
      tr.appendChild(el("td", "num", fmt(row.total)));
      tr.appendChild(el("td", "num", fmt(row.mean)));
      tr.appendChild(el("td", "num", fmt(row.device_count)));
      tr.appendChild(el("td", "num", fmt(row.kept_count)));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function renderTopDevices(summary) {
    var wrap = document.createElement("div");
    wrap.appendChild(el("h3", null, "Top devices"));
    var list = el("ol", "top-devices");
    var devices = Array.isArray(summary.top_devices) ? summary.top_devices : [];
    if (devices.length === 0) {
      list.appendChild(el("li", null, "No devices with kept records."));
    }
    devices.forEach(function (d) {
      var text = d.site + " / " + d.device + " \u2014 total " + fmt(d.total) +
        ", mean " + fmt(d.mean);
      list.appendChild(el("li", null, text));
    });
    wrap.appendChild(list);
    return wrap;
  }

  function renderSummary(summary) {
    if (!summaryEl) return;
    var frag = document.createDocumentFragment();
    frag.appendChild(renderStats(summary));
    frag.appendChild(renderBySite(summary));
    frag.appendChild(renderExtremes(summary));
    frag.appendChild(renderTopDevices(summary));
    summaryEl.replaceChildren(frag);
  }

  function renderChart(summary) {
    if (!chartEl) return;
    var sites = Array.isArray(summary.by_site) ? summary.by_site.slice() : [];
    if (sites.length === 0) {
      chartEl.replaceChildren(el("p", "loading", "No data to chart."));
      return;
    }
    var maxTotal = sites.reduce(function (acc, row) {
      var v = Number(row.total) || 0;
      return v > acc ? v : acc;
    }, 0);

    var frag = document.createDocumentFragment();
    sites.forEach(function (row) {
      var value = Number(row.total) || 0;
      var pct = maxTotal > 0 ? (value / maxTotal) * 100 : 0;

      var line = el("div", "bar-row");
      line.appendChild(el("span", "bar-label", row.site));

      var track = el("div", "bar-track");
      var fill = el("div", "bar-fill");
      fill.style.width = pct.toFixed(2) + "%";
      fill.setAttribute("role", "img");
      fill.setAttribute("aria-label", row.site + ": " + fmt(value) + " " + (summary.unit || ""));
      track.appendChild(fill);
      line.appendChild(track);

      line.appendChild(el("span", "bar-value", fmt(value)));
      frag.appendChild(line);
    });
    chartEl.replaceChildren(frag);
  }

  function render(payload) {
    var summary = payload && payload.summary ? payload.summary : null;
    if (!summary) {
      showError("data.json did not contain a summary object.");
      return;
    }
    renderSummary(summary);
    renderChart(summary);
  }

  function load() {
    fetch("data.json", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status + " while loading data.json");
        }
        return response.json();
      })
      .then(render)
      .catch(function (err) {
        showError("Could not load data.json: " + err.message);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
<<<

@tool write_file {"path": "site/_build_data.py"}
<<<
"""Generate site/data.json from data/summary.json + data/records.jsonl."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main() -> None:
    with open(os.path.join(ROOT, "data", "summary.json"), "r", encoding="utf-8") as f:
        summary = json.load(f)

    records = []
    with open(os.path.join(ROOT, "data", "records.jsonl"), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    payload = {"summary": summary, "records": records}
    out = os.path.join(HERE, "data.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("wrote", out, "records:", len(records), "sites:", len(summary.get("by_site", [])))


if __name__ == "__main__":
    main()
<<<

@tool run_bash {"command": "python site/_build_data.py"}
