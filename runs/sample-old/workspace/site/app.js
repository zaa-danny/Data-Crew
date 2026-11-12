"use strict";

(function () {
  var DATA_URL = "data.json";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function fmt(value) {
    if (value === null || value === undefined) return "-";
    if (typeof value === "number") {
      return Number.isInteger(value) ? String(value) : value.toFixed(2);
    }
    return String(value);
  }

  function statCard(label, value) {
    var li = el("li", "stat");
    li.appendChild(el("span", "label", label));
    li.appendChild(el("span", "value", fmt(value)));
    return li;
  }

  function renderSummary(summary, records) {
    var root = document.getElementById("summary");
    root.textContent = "";

    var grid = el("ul", "stat-grid");
    grid.appendChild(statCard("Unit", summary.unit));
    grid.appendChild(statCard("Records", summary.record_count));
    grid.appendChild(statCard("Kept", summary.kept_count));
    grid.appendChild(statCard("Total", summary.total));
    grid.appendChild(statCard("Mean", summary.mean));
    grid.appendChild(statCard("Max", summary.max && summary.max.value));
    grid.appendChild(statCard("Min", summary.min && summary.min.value));
    root.appendChild(grid);

    if (summary.max) {
      root.appendChild(el("p", "note",
        "Max: " + summary.max.site + " / " + summary.max.device + " on " + summary.max.day));
    }
    if (summary.min) {
      root.appendChild(el("p", "note",
        "Min: " + summary.min.site + " / " + summary.min.device + " on " + summary.min.day));
    }

    var bySite = Array.isArray(summary.by_site) ? summary.by_site : [];
    if (bySite.length) {
      var table = el("table");
      var thead = el("thead");
      var hrow = el("tr");
      ["Site", "Total", "Mean", "Devices", "Kept"].forEach(function (h, i) {
        hrow.appendChild(el("th", i === 0 ? "" : "num", h));
      });
      thead.appendChild(hrow);
      table.appendChild(thead);
      var tbody = el("tbody");
      bySite.forEach(function (row) {
        var tr = el("tr");
        tr.appendChild(el("td", "", row.site));
        tr.appendChild(el("td", "num", fmt(row.total)));
        tr.appendChild(el("td", "num", fmt(row.mean)));
        tr.appendChild(el("td", "num", fmt(row.device_count)));
        tr.appendChild(el("td", "num", fmt(row.kept_count)));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      root.appendChild(table);
    }

    var top = Array.isArray(summary.top_devices) ? summary.top_devices : [];
    if (top.length) {
      root.appendChild(el("h2", "", "Top devices"));
      var list = el("ol", "top-devices");
      top.forEach(function (d) {
        list.appendChild(el("li", "",
          d.device + " (" + d.site + ") - total " + fmt(d.total) + ", mean " + fmt(d.mean)));
      });
      root.appendChild(list);
    }

    if (Array.isArray(records) && records.length) {
      root.appendChild(el("p", "note", "Loaded " + records.length + " raw records."));
    }
  }

  function renderChart(summary) {
    var chart = document.getElementById("chart");
    chart.textContent = "";
    var bySite = Array.isArray(summary.by_site) ? summary.by_site.slice() : [];
    if (!bySite.length) {
      chart.appendChild(el("p", "loading", "No site data available."));
      return;
    }
    bySite.sort(function (a, b) { return b.total - a.total; });
    var maxTotal = bySite.reduce(function (m, r) {
      return Math.max(m, Number(r.total) || 0);
    }, 0);

    bySite.forEach(function (row) {
      var total = Number(row.total) || 0;
      var pct = maxTotal > 0 ? (total / maxTotal) * 100 : 0;

      var wrap = el("div", "bar-row");
      wrap.appendChild(el("span", "bar-label", row.site));

      var track = el("div", "bar-track");
      var fill = el("div", "bar-fill");
      fill.style.width = pct.toFixed(1) + "%";
      fill.setAttribute("role", "img");
      fill.setAttribute("aria-label", row.site + " total " + fmt(total));
      track.appendChild(fill);
      wrap.appendChild(track);

      wrap.appendChild(el("span", "bar-value", fmt(total)));
      chart.appendChild(wrap);
    });
  }

  function renderError(message) {
    var root = document.getElementById("summary");
    root.textContent = "";
    root.appendChild(el("p", "error", message));
    var chart = document.getElementById("chart");
    chart.textContent = "";
  }

  function boot() {
    fetch(DATA_URL, { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status + " loading " + DATA_URL);
        return res.json();
      })
      .then(function (payload) {
        var summary = (payload && payload.summary) || {};
        var records = (payload && payload.records) || [];
        renderSummary(summary, records);
        renderChart(summary);
      })
      .catch(function (err) {
        renderError("Could not load data: " + err.message);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
