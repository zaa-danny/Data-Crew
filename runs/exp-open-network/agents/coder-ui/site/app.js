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
    if (value === null || value === undefined) return "-";
    if (typeof value !== "number" || !isFinite(value)) return String(value);
    const d = typeof digits === "number" ? digits : 2;
    return value.toFixed(d);
  }

  function metric(label, value) {
    const li = el("li", "metric");
    li.appendChild(el("span", "label", label));
    li.appendChild(el("span", "value", value));
    return li;
  }

  function buildTable(captionText, columns, rows) {
    const table = el("table");
    const caption = el("caption", null, captionText);
    table.appendChild(caption);

    const thead = el("thead");
    const headRow = el("tr");
    columns.forEach(function (col) {
      const th = el("th", col.numeric ? "num" : null, col.label);
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = el("tbody");
    rows.forEach(function (row) {
      const tr = el("tr");
      columns.forEach(function (col) {
        const td = el("td", col.numeric ? "num" : null, col.render(row));
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function renderSummary(summary, records) {
    const host = document.getElementById("summary");
    host.textContent = "";

    const digits = typeof summary.round_digits === "number" ? summary.round_digits : 2;
    const unit = summary.unit || "value";

    const metrics = el("ul", "metrics");
    metrics.appendChild(metric("Unit", unit));
    metrics.appendChild(metric("Records", summary.record_count));
    metrics.appendChild(metric("Kept", summary.kept_count));
    metrics.appendChild(metric("Total", fmt(summary.total, digits)));
    metrics.appendChild(metric("Mean", fmt(summary.mean, digits)));
    metrics.appendChild(metric("Max", fmt(summary.max && summary.max.value, digits)));
    metrics.appendChild(metric("Min", fmt(summary.min && summary.min.value, digits)));
    host.appendChild(metrics);

    if (summary.max) {
      host.appendChild(
        el(
          "p",
          "subtitle",
          "Max at " + summary.max.site + " / " + summary.max.device + " on " + summary.max.day +
            "; min at " + summary.min.site + " / " + summary.min.device + " on " + summary.min.day + "."
        )
      );
    }

    const bySite = Array.isArray(summary.by_site) ? summary.by_site : [];
    host.appendChild(
      buildTable(
        "By site",
        [
          { label: "Site", render: function (r) { return r.site; } },
          { label: "Total", numeric: true, render: function (r) { return fmt(r.total, digits); } },
          { label: "Mean", numeric: true, render: function (r) { return fmt(r.mean, digits); } },
          { label: "Devices", numeric: true, render: function (r) { return r.device_count; } },
          { label: "Kept", numeric: true, render: function (r) { return r.kept_count; } }
        ],
        bySite
      )
    );

    const topDevices = Array.isArray(summary.top_devices) ? summary.top_devices : [];
    const topHeading = el("h3", null, "Top devices");
    host.appendChild(topHeading);
    if (topDevices.length === 0) {
      host.appendChild(el("p", "loading", "No top devices reported."));
    } else {
      const list = el("ol", "top-devices");
      topDevices.forEach(function (d) {
        list.appendChild(
          el(
            "li",
            null,
            d.device + " (" + d.site + ") - total " + fmt(d.total, digits) + ", mean " + fmt(d.mean, digits)
          )
        );
      });
      host.appendChild(list);
    }

    host.appendChild(el("p", "subtitle", "Loaded " + records.length + " raw records from data.json."));
  }

  function renderChart(summary) {
    const host = document.getElementById("chart");
    host.textContent = "";

    const digits = typeof summary.round_digits === "number" ? summary.round_digits : 2;
    const bySite = Array.isArray(summary.by_site) ? summary.by_site.slice() : [];
    if (bySite.length === 0) {
      host.appendChild(el("p", "loading", "No site data available."));
      return;
    }

    bySite.sort(function (a, b) { return b.total - a.total; });
    const maxTotal = bySite.reduce(function (m, r) {
      return Math.max(m, typeof r.total === "number" ? r.total : 0);
    }, 0);

    const chart = el("div", "chart");
    bySite.forEach(function (row) {
      const line = el("div", "bar-row");
      line.appendChild(el("span", "bar-label", row.site));

      const track = el("div", "bar-track");
      const fill = el("div", "bar-fill");
      const pct = maxTotal > 0 ? (row.total / maxTotal) * 100 : 0;
      fill.style.width = Math.max(0, Math.min(100, pct)).toFixed(2) + "%";
      fill.title = row.site + ": " + fmt(row.total, digits);
      track.appendChild(fill);
      line.appendChild(track);

      line.appendChild(el("span", "bar-value", fmt(row.total, digits)));
      chart.appendChild(line);
    });

    host.appendChild(chart);
  }

  function showError(message) {
    ["summary", "chart"].forEach(function (id) {
      const host = document.getElementById(id);
      if (host) {
        host.textContent = "";
        host.appendChild(el("p", "error", message));
      }
    });
  }

  function main() {
    fetch(DATA_URL, { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Failed to load " + DATA_URL + " (HTTP " + response.status + ")");
        }
        return response.json();
      })
      .then(function (payload) {
        const summary = (payload && payload.summary) || {};
        const records = (payload && Array.isArray(payload.records)) ? payload.records : [];
        renderSummary(summary, records);
        renderChart(summary);
      })
      .catch(function (err) {
        showError("Could not load data: " + (err && err.message ? err.message : String(err)));
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
