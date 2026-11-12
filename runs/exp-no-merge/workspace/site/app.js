(function () {
  "use strict";

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
    var card = el("div", "stat");
    card.appendChild(el("span", "label", label));
    card.appendChild(el("span", "value", fmt(value)));
    return card;
  }

  function renderSummary(summary) {
    var root = document.getElementById("summary");
    root.textContent = "";

    var grid = el("div", "stat-grid");
    grid.appendChild(statCard("Unit", summary.unit));
    grid.appendChild(statCard("Records", summary.record_count));
    grid.appendChild(statCard("Kept", summary.kept_count));
    grid.appendChild(statCard("Total", summary.total));
    grid.appendChild(statCard("Mean", summary.mean));
    grid.appendChild(statCard("Max", summary.max ? summary.max.value : null));
    grid.appendChild(statCard("Min", summary.min ? summary.min.value : null));
    root.appendChild(grid);

    var bySite = Array.isArray(summary.by_site) ? summary.by_site : [];
    root.appendChild(el("h2", null, "By site"));
    if (bySite.length === 0) {
      root.appendChild(el("p", "loading", "No site data available."));
    } else {
      var table = el("table");
      var thead = el("thead");
      var headRow = el("tr");
      ["Site", "Total", "Mean", "Devices", "Kept"].forEach(function (name, idx) {
        var th = el("th", idx === 0 ? null : "num", name);
        headRow.appendChild(th);
      });
      thead.appendChild(headRow);
      table.appendChild(thead);

      var tbody = el("tbody");
      bySite.forEach(function (row) {
        var tr = el("tr");
        tr.appendChild(el("td", null, row.site));
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
    root.appendChild(el("h2", null, "Top devices"));
    if (top.length === 0) {
      root.appendChild(el("p", "loading", "No device data available."));
    } else {
      var list = el("ol", "top-devices");
      top.forEach(function (dev) {
        var li = el("li");
        li.textContent = dev.device + " (" + dev.site + ") - total " +
          fmt(dev.total) + ", mean " + fmt(dev.mean);
        list.appendChild(li);
      });
      root.appendChild(list);
    }
  }

  function renderChart(summary) {
    var root = document.getElementById("chart");
    root.textContent = "";

    var bySite = Array.isArray(summary.by_site) ? summary.by_site.slice() : [];
    if (bySite.length === 0) {
      root.appendChild(el("p", "loading", "No chart data available."));
      return;
    }

    bySite.sort(function (a, b) { return (b.total || 0) - (a.total || 0); });
    var maxTotal = bySite.reduce(function (acc, row) {
      var v = typeof row.total === "number" ? row.total : 0;
      return v > acc ? v : acc;
    }, 0);

    bySite.forEach(function (row) {
      var line = el("div", "chart-row");
      line.appendChild(el("span", "chart-label", row.site));

      var track = el("div", "chart-track");
      var bar = el("div", "chart-bar");
      var pct = maxTotal > 0 ? (row.total / maxTotal) * 100 : 0;
      bar.style.width = Math.max(pct, 0).toFixed(2) + "%";
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", row.site + ": " + fmt(row.total));
      track.appendChild(bar);
      line.appendChild(track);

      line.appendChild(el("span", "chart-value", fmt(row.total)));
      root.appendChild(line);
    });
  }

  function showError(message) {
    ["summary", "chart"].forEach(function (id) {
      var root = document.getElementById(id);
      if (root) {
        root.textContent = "";
        root.appendChild(el("p", "error", message));
      }
    });
  }

  function init() {
    fetch(DATA_URL, { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Failed to load " + DATA_URL + " (HTTP " + response.status + ")");
        }
        return response.json();
      })
      .then(function (payload) {
        var summary = (payload && payload.summary) || {};
        renderSummary(summary);
        renderChart(summary);
      })
      .catch(function (err) {
        showError("Could not load data: " + err.message);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
