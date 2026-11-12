"use strict";

(function () {
  const summaryEl = document.getElementById("summary");
  const chartEl = document.getElementById("chart");

  function fmt(value) {
    if (value === null || value === undefined) return "-";
    if (typeof value === "number") {
      return Number.isInteger(value) ? String(value) : value.toFixed(2);
    }
    return String(value);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function statItem(label, value) {
    const li = el("li", "stat");
    li.appendChild(el("span", "label", label));
    li.appendChild(el("span", "value", fmt(value)));
    return li;
  }

  function renderSummary(summary) {
    summaryEl.textContent = "";

    const grid = el("ul", "stat-grid");
    grid.appendChild(statItem("Unit", summary.unit));
    grid.appendChild(statItem("Records", summary.record_count));
    grid.appendChild(statItem("Kept", summary.kept_count));
    grid.appendChild(statItem("Total", summary.total));
    grid.appendChild(statItem("Mean", summary.mean));
    grid.appendChild(statItem("Max", summary.max && summary.max.value));
    grid.appendChild(statItem("Min", summary.min && summary.min.value));
    summaryEl.appendChild(grid);

    if (summary.max) {
      summaryEl.appendChild(
        el(
          "p",
          "subtitle",
          "Max at " + summary.max.site + " / " + summary.max.device + " on " + summary.max.day
        )
      );
    }
    if (summary.min) {
      summaryEl.appendChild(
        el(
          "p",
          "subtitle",
          "Min at " + summary.min.site + " / " + summary.min.device + " on " + summary.min.day
        )
      );
    }

    const bySite = Array.isArray(summary.by_site) ? summary.by_site : [];
    if (bySite.length) {
      const table = el("table");
      const thead = el("thead");
      const headRow = el("tr");
      const cols = [
        ["Site", ""],
        ["Total", "num"],
        ["Mean", "num"],
        ["Devices", "num"],
        ["Kept", "num"],
      ];
      cols.forEach(function (c) {
        headRow.appendChild(el("th", c[1], c[0]));
      });
      thead.appendChild(headRow);
      table.appendChild(thead);

      const tbody = el("tbody");
      bySite.forEach(function (row) {
        const tr = el("tr");
        tr.appendChild(el("td", "", row.site));
        tr.appendChild(el("td", "num", fmt(row.total)));
        tr.appendChild(el("td", "num", fmt(row.mean)));
        tr.appendChild(el("td", "num", fmt(row.device_count)));
        tr.appendChild(el("td", "num", fmt(row.kept_count)));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      summaryEl.appendChild(table);
    }

    const topDevices = Array.isArray(summary.top_devices) ? summary.top_devices : [];
    if (topDevices.length) {
      summaryEl.appendChild(el("h2", "", "Top devices"));
      const list = el("ol", "top-devices");
      topDevices.forEach(function (d) {
        list.appendChild(
          el(
            "li",
            "",
            d.site + " / " + d.device + " - total " + fmt(d.total) + ", mean " + fmt(d.mean)
          )
        );
      });
      summaryEl.appendChild(list);
    }
  }

  function renderChart(summary) {
    chartEl.textContent = "";
    const bySite = Array.isArray(summary.by_site) ? summary.by_site.slice() : [];
    if (!bySite.length) {
      chartEl.appendChild(el("p", "loading", "No site data available."));
      return;
    }

    bySite.sort(function (a, b) {
      return b.total - a.total;
    });

    let maxTotal = 0;
    bySite.forEach(function (row) {
      if (typeof row.total === "number" && row.total > maxTotal) maxTotal = row.total;
    });
    if (maxTotal <= 0) maxTotal = 1;

    bySite.forEach(function (row) {
      const line = el("div", "bar-row");
      line.appendChild(el("span", "bar-label", row.site));

      const track = el("div", "bar-track");
      const fill = el("div", "bar-fill");
      const pct = Math.max(0, Math.min(100, (row.total / maxTotal) * 100));
      fill.style.width = pct.toFixed(2) + "%";
      fill.title = row.site + ": " + fmt(row.total);
      track.appendChild(fill);
      line.appendChild(track);

      line.appendChild(el("span", "bar-value", fmt(row.total)));
      chartEl.appendChild(line);
    });
  }

  function renderError(message) {
    summaryEl.textContent = "";
    chartEl.textContent = "";
    summaryEl.appendChild(el("p", "error", message));
  }

  fetch("data.json")
    .then(function (response) {
      if (!response.ok) {
        throw new Error("HTTP " + response.status + " while loading data.json");
      }
      return response.json();
    })
    .then(function (payload) {
      const summary = payload && payload.summary ? payload.summary : {};
      renderSummary(summary);
      renderChart(summary);
    })
    .catch(function (err) {
      renderError("Could not load data.json: " + err.message);
    });
})();
