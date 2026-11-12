"use strict";

(function () {
  const DATA_URL = "data.json";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function fmt(value, digits) {
    if (value === null || value === undefined || value === "") return "-";
    const num = Number(value);
    if (!Number.isFinite(num)) return String(value);
    const d = Number.isInteger(digits) ? digits : 2;
    return num.toFixed(d);
  }

  function statCard(label, value) {
    const card = el("div", "stat");
    card.appendChild(el("span", "label", label));
    card.appendChild(el("span", "value", value));
    return card;
  }

  function renderSummary(summary, records) {
    const host = document.getElementById("summary");
    host.textContent = "";

    const digits = Number.isInteger(summary.round_digits) ? summary.round_digits : 2;
    const unit = summary.unit || "value";

    const grid = el("div", "stat-grid");
    grid.appendChild(statCard("Unit", unit));
    grid.appendChild(statCard("Records", summary.record_count));
    grid.appendChild(statCard("Kept", summary.kept_count));
    grid.appendChild(statCard("Total", fmt(summary.total, digits)));
    grid.appendChild(statCard("Mean", fmt(summary.mean, digits)));
    grid.appendChild(statCard("Max", fmt(summary.max && summary.max.value, digits)));
    grid.appendChild(statCard("Min", fmt(summary.min && summary.min.value, digits)));
    host.appendChild(grid);

    if (summary.max) {
      host.appendChild(el("p", "note",
        "Max at " + summary.max.site + " / " + summary.max.device + " on " + summary.max.day));
    }
    if (summary.min) {
      host.appendChild(el("p", "note",
        "Min at " + summary.min.site + " / " + summary.min.device + " on " + summary.min.day));
    }

    const sites = Array.isArray(summary.by_site) ? summary.by_site : [];
    host.appendChild(el("h2", null, "By site"));
    if (sites.length === 0) {
      host.appendChild(el("p", "loading", "No site data available."));
    } else {
      const table = el("table");
      const thead = el("thead");
      const headRow = el("tr");
      ["Site", "Total", "Mean", "Devices", "Kept"].forEach(function (label, idx) {
        const th = el("th", idx === 0 ? null : "num", label);
        headRow.appendChild(th);
      });
      thead.appendChild(headRow);
      table.appendChild(thead);

      const tbody = el("tbody");
      sites.forEach(function (row) {
        const tr = el("tr");
        tr.appendChild(el("td", null, row.site));
        tr.appendChild(el("td", "num", fmt(row.total, digits)));
        tr.appendChild(el("td", "num", fmt(row.mean, digits)));
        tr.appendChild(el("td", "num", row.device_count));
        tr.appendChild(el("td", "num", row.kept_count));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      host.appendChild(table);
    }

    const top = Array.isArray(summary.top_devices) ? summary.top_devices : [];
    host.appendChild(el("h2", null, "Top devices"));
    if (top.length === 0) {
      host.appendChild(el("p", "loading", "No top devices available."));
    } else {
      const list = el("ol", "top-devices");
      top.forEach(function (dev) {
        list.appendChild(el("li", null,
          dev.site + " / " + dev.device + " - total " + fmt(dev.total, digits) +
          " (mean " + fmt(dev.mean, digits) + ")"));
      });
      host.appendChild(list);
    }

    host.appendChild(el("p", "note", "Loaded " + records.length + " raw records."));
  }

  function renderChart(summary) {
    const host = document.getElementById("chart");
    host.textContent = "";

    const digits = Number.isInteger(summary.round_digits) ? summary.round_digits : 2;
    const sites = Array.isArray(summary.by_site) ? summary.by_site.slice() : [];
    if (sites.length === 0) {
      host.appendChild(el("p", "loading", "No chart data available."));
      return;
    }

    sites.sort(function (a, b) { return Number(b.total) - Number(a.total); });
    const maxTotal = sites.reduce(function (acc, row) {
      const v = Number(row.total);
      return Number.isFinite(v) && v > acc ? v : acc;
    }, 0);

    sites.forEach(function (row) {
      const wrap = el("div", "bar-row");
      wrap.appendChild(el("span", "bar-label", row.site));

      const track = el("div", "bar-track");
      const fill = el("div", "bar-fill");
      const pct = maxTotal > 0 ? Math.max(0, Math.min(100, (Number(row.total) / maxTotal) * 100)) : 0;
      fill.style.width = pct.toFixed(1) + "%";
      fill.setAttribute("title", row.site + ": " + fmt(row.total, digits));
      track.appendChild(fill);
      wrap.appendChild(track);

      wrap.appendChild(el("span", "bar-value", fmt(row.total, digits)));
      host.appendChild(wrap);
    });
  }

  function renderError(message) {
    const summaryHost = document.getElementById("summary");
    const chartHost = document.getElementById("chart");
    if (summaryHost) {
      summaryHost.textContent = "";
      summaryHost.appendChild(el("p", "error", "Failed to load data: " + message));
    }
    if (chartHost) {
      chartHost.textContent = "";
    }
  }

  function boot() {
    fetch(DATA_URL, { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (payload) {
        const summary = (payload && payload.summary) || {};
        const records = (payload && Array.isArray(payload.records)) ? payload.records : [];
        renderSummary(summary, records);
        renderChart(summary);
      })
      .catch(function (err) {
        renderError(err && err.message ? err.message : String(err));
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
