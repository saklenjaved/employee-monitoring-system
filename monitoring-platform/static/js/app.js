// Small helpers for every page: toast messages and trend charts.
// Live updates, filters and paging use HTMX attributes in the templates, not code here.

(function () {
  "use strict";

  // Toasts close by themselves after a few seconds; the close button closes them at once.
  function setUpToasts(root) {
    root.querySelectorAll(".toast").forEach(function (toast) {
      if (toast.dataset.ready) return;
      toast.dataset.ready = "1";
      var close = function () { toast.classList.add("toast-hide"); setTimeout(function () { toast.remove(); }, 300); };
      toast.querySelector("button").addEventListener("click", close);
      setTimeout(close, 6000);
    });
  }

  // Category order and labels match analytics.services.CATEGORY_KEYS.
  var CATEGORIES = [
    ["productive", "Productive"],
    ["neutral", "Neutral"],
    ["unproductive", "Unproductive"],
    ["unclassified", "Unclassified"],
  ];

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function shortDate(iso) {
    // The date is a calendar day (YYYY-MM-DD). Format it in UTC so the browser's own time zone cannot shift the day.
    var parts = iso.split("-");
    var d = new Date(Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])));
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
  }

  function hours(value) {
    // Round to whole minutes first, so 59.6 minutes shows as 1h 00m and never as 0h 60m.
    var minutes = Math.round(value * 60);
    return Math.floor(minutes / 60) + "h " + String(minutes % 60).padStart(2, "0") + "m";
  }

  function fillTable(table, data) {
    var head = "<tr><th>Date</th>" + CATEGORIES.map(function (c) { return "<th>" + c[1] + "</th>"; }).join("") + "<th>Activity</th></tr>";
    var rows = data.labels.map(function (label, i) {
      var cells = CATEGORIES.map(function (c) { return "<td>" + hours(data.series[c[0]][i]) + "</td>"; }).join("");
      var activity = data.activity_percent[i] === null ? "-" : data.activity_percent[i] + "%";
      return "<tr><td>" + shortDate(label) + "</td>" + cells + "<td>" + activity + "</td></tr>";
    });
    table.innerHTML = head + rows.join("");
  }

  function drawTrend(box) {
    if (box.dataset.ready || typeof Chart === "undefined") return;
    box.dataset.ready = "1";
    fetch(box.dataset.trendUrl, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (response) { if (!response.ok) throw new Error(response.status); return response.json(); })
      .then(function (data) {
        fillTable(box.querySelector("table"), data);
        var total = CATEGORIES.reduce(function (sum, c) { return sum + data.series[c[0]].reduce(function (a, b) { return a + b; }, 0); }, 0);
        if (!total) {
          box.querySelector(".chart-box").hidden = true;
          var empty = box.querySelector(".chart-empty");
          var first = shortDate(data.labels[0]);
          var last = shortDate(data.labels[data.labels.length - 1]);
          empty.textContent = "No application time was recorded from " + first + " to " + last + ". " +
            "Time shows here once an agent uploads its usage. Use the date filter above to choose other days.";
          empty.hidden = false;
          return;
        }
        var surface = cssVar("--color-surface");
        // Idle time is a line on its own axis, so it is not stacked on top of the category bars.
        var idleLine = {
          type: "line", label: "Idle", data: data.idle_hours || [], yAxisID: "y2", order: 0,
          borderColor: cssVar("--color-muted"), backgroundColor: cssVar("--color-muted"),
          borderWidth: 2, pointRadius: 2, tension: 0.3,
        };
        new Chart(box.querySelector("canvas"), {
          type: "bar",
          data: {
            labels: data.labels.map(shortDate),
            datasets: CATEGORIES.map(function (c) {
              return {
                label: c[1],
                data: data.series[c[0]],
                backgroundColor: cssVar("--cat-" + c[0]),
                borderColor: surface,
                borderWidth: { top: 2 },  // a 2px surface gap between stacked segments
                borderRadius: 4,
                borderSkipped: "start",
                maxBarThickness: 28,
              };
            }).concat([idleLine]),
          },
          options: {
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            scales: {
              x: { stacked: true, grid: { display: false }, ticks: { color: cssVar("--color-muted"), maxRotation: 0, autoSkip: true } },
              y: {
                stacked: true,
                border: { display: false },
                grid: { color: cssVar("--color-border") },
                ticks: { color: cssVar("--color-muted"), callback: function (v) { return v + "h"; } },
                title: { display: true, text: "Hours", color: cssVar("--color-muted") },
              },
              y2: {
                position: "right",
                beginAtZero: true,
                border: { display: false },
                grid: { drawOnChartArea: false },
                ticks: { color: cssVar("--color-muted"), callback: function (v) { return v + "h"; } },
                title: { display: true, text: "Idle hours", color: cssVar("--color-muted") },
              },
            },
            plugins: {
              legend: { position: "top", align: "start", labels: { usePointStyle: true, pointStyle: "rectRounded", color: cssVar("--color-text") } },
              tooltip: {
                callbacks: {
                  label: function (ctx) { return " " + ctx.dataset.label + ": " + hours(ctx.parsed.y); },
                  footer: function (items) {
                    var activity = data.activity_percent[items[0].dataIndex];
                    return activity === null ? "" : "Activity: " + activity + "%";
                  },
                },
              },
            },
          },
        });
      })
      .catch(function () {
        box.querySelector(".chart-box").hidden = true;
        var empty = box.querySelector(".chart-empty");
        empty.textContent = "The chart could not be loaded. Reload the page to try again.";
        empty.hidden = false;
      });
  }

  // Forms with data-confirm ask first. The dialog is built here, so no template needs markup for it.
  function openConfirm(form) {
    var backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.innerHTML =
      '<div class="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title">' +
      '<h2 id="confirm-title">Are you sure?</h2>' +
      '<p class="muted"></p>' +
      '<div class="actions">' +
      '<button type="button" class="secondary" data-cancel>Cancel</button>' +
      '<button type="button" data-ok></button>' +
      "</div></div>";
    backdrop.querySelector("p").textContent = form.dataset.confirm;
    var ok = backdrop.querySelector("[data-ok]");
    ok.textContent = form.dataset.confirmLabel || "Continue";
    if (form.hasAttribute("data-confirm-danger")) ok.classList.add("danger");

    function close() {
      document.removeEventListener("keydown", onKey);
      backdrop.remove();
      form.querySelector("button, [type=submit]") && form.querySelector("button, [type=submit]").focus();
    }
    function onKey(event) { if (event.key === "Escape") close(); }

    backdrop.querySelector("[data-cancel]").addEventListener("click", close);
    backdrop.addEventListener("click", function (event) { if (event.target === backdrop) close(); });
    ok.addEventListener("click", function () {
      close();
      form.dataset.confirmed = "1";
      form.requestSubmit();
    });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(backdrop);
    backdrop.querySelector("[data-cancel]").focus();
  }

  function setUpConfirm(root) {
    root.querySelectorAll("form[data-confirm]").forEach(function (form) {
      if (form.dataset.ready) return;
      form.dataset.ready = "1";
      form.addEventListener("submit", function (event) {
        if (form.dataset.confirmed) { delete form.dataset.confirmed; return; }
        event.preventDefault();
        openConfirm(form);
      });
    });
  }

  // Device connections on the Admin dashboard. The numbers come from the page, which reads them from the database.
  function drawDonut(box) {
    if (box.dataset.ready || typeof Chart === "undefined") return;
    box.dataset.ready = "1";
    var values = [
      { label: "Connected", value: Number(box.dataset.online), color: cssVar("--color-online") },
      { label: "Disconnected", value: Number(box.dataset.offline), color: cssVar("--color-offline") },
      { label: "Revoked", value: Number(box.dataset.revoked), color: cssVar("--color-muted") },
    ];
    var total = values.reduce(function (sum, v) { return sum + v.value; }, 0);
    if (!total) {
      box.querySelector("canvas").hidden = true;
      box.nextElementSibling.hidden = false;
      return;
    }
    new Chart(box.querySelector("canvas"), {
      type: "doughnut",
      data: {
        labels: values.map(function (v) { return v.label; }),
        datasets: [{ data: values.map(function (v) { return v.value; }), backgroundColor: values.map(function (v) { return v.color; }), borderWidth: 0 }],
      },
      options: {
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: {
          legend: { position: "right", labels: { color: cssVar("--color-text"), usePointStyle: true } },
          tooltip: { callbacks: { label: function (ctx) { return " " + ctx.label + ": " + ctx.parsed + " PC" + (ctx.parsed === 1 ? "" : "s"); } } },
        },
      },
    });
  }

  // Screenshots captured per day, for the Admin dashboard.
  function drawShotBars(box) {
    if (box.dataset.ready || typeof Chart === "undefined") return;
    box.dataset.ready = "1";
    var labels = box.dataset.labels ? box.dataset.labels.split(",") : [];
    var counts = box.dataset.counts ? box.dataset.counts.split(",").map(Number) : [];
    var total = counts.reduce(function (a, b) { return a + b; }, 0);
    if (!total) {
      box.querySelector("canvas").hidden = true;
      box.nextElementSibling.hidden = false;
      return;
    }
    new Chart(box.querySelector("canvas"), {
      type: "bar",
      data: {
        labels: labels.map(shortDate),
        datasets: [{ label: "Screenshots", data: counts, backgroundColor: cssVar("--color-online"), borderRadius: 4, maxBarThickness: 28 }],
      },
      options: {
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (ctx) { return " " + ctx.parsed.y + " screenshot" + (ctx.parsed.y === 1 ? "" : "s"); } } } },
        scales: {
          x: { grid: { display: false }, ticks: { color: cssVar("--color-muted"), maxRotation: 0, autoSkip: true } },
          y: { beginAtZero: true, ticks: { color: cssVar("--color-muted"), precision: 0 }, grid: { color: cssVar("--color-border") } },
        },
      },
    });
  }

  // The date range bar: presets submit at once; "Custom range" shows the two date fields first.
  function setUpRange(form) {
    if (form.dataset.ready) return;
    form.dataset.ready = "1";
    var select = form.querySelector("select[name=preset]");
    var custom = form.querySelector("[data-range-custom]");
    select.addEventListener("change", function () {
      if (select.value === "custom") {
        custom.hidden = false;
        form.querySelector("input[name=start]").focus();
      } else {
        form.submit();
      }
    });
  }

  // In-place actions (HTMX) send their result as a "toast" event from the HX-Trigger header.
  document.body.addEventListener("toast", function (event) {
    var detail = event.detail || {};
    var container = document.querySelector(".toasts");
    if (!container) {
      container = document.createElement("ul");
      container.className = "toasts";
      container.setAttribute("role", "status");
      container.setAttribute("aria-live", "polite");
      document.body.appendChild(container);
    }
    var item = document.createElement("li");
    item.className = "toast " + (detail.tags || "");
    var text = document.createElement("span");
    text.textContent = detail.message || "";
    var close = document.createElement("button");
    close.type = "button";
    close.setAttribute("aria-label", "Close message");
    close.innerHTML = "&times;";
    item.append(text, close);
    container.appendChild(item);
    setUpToasts(container);
  });

  // Light / dark theme. base.html applies the saved choice before the page paints; this keeps the buttons in step.
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function paintThemeToggles() {
    var dark = currentTheme() === "dark";
    var label = dark ? "Switch to light mode" : "Switch to dark mode";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      button.setAttribute("aria-label", label);
      button.title = label;
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-theme-toggle]");
    if (!button) return;
    var next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) { /* storage blocked: the choice lasts until reload */ }
    // Charts take their colors when they are drawn, so a page with charts is reloaded to redraw them.
    if (document.querySelector("canvas") && window.Chart) { window.location.reload(); return; }
    paintThemeToggles();
  });

  // Eye icons drawn inline, because the shared icon sprite is only on signed-in pages.
  var EYE_ICON = '<svg class="i" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF_ICON = '<svg class="i" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l18 18"/><path d="M10.6 6.1A9.8 9.8 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1"/><path d="M6.6 6.6C3.7 8.4 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 4.4-1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';

  // Forms marked data-password-toggle get a small eye button on each password field. It shows or hides the typed password.
  function setUpPasswordToggles(root) {
    root.querySelectorAll("form[data-password-toggle] input[type=password]").forEach(function (input) {
      if (input.dataset.toggleReady) return;
      input.dataset.toggleReady = "1";
      var wrap = document.createElement("div");
      wrap.className = "pw-wrap";
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);
      var button = document.createElement("button");
      button.type = "button";
      button.className = "pw-toggle";
      wrap.appendChild(button);
      var show = function (visible) {
        var label = visible ? "Hide password" : "Show password";
        input.type = visible ? "text" : "password";
        button.setAttribute("aria-pressed", visible ? "true" : "false");
        button.setAttribute("aria-label", label);
        button.title = label;
        button.innerHTML = visible ? EYE_OFF_ICON : EYE_ICON;
      };
      button.addEventListener("click", function () { show(input.type === "password"); input.focus(); });
      // Send the form with the field hidden again, so password managers still see a password field.
      if (input.form) input.form.addEventListener("submit", function () { show(false); });
      show(false);
    });
  }

  function setUp(root) {
    setUpToasts(root);
    setUpPasswordToggles(root);
    setUpConfirm(root);
    root.querySelectorAll("[data-range-form]").forEach(setUpRange);
    root.querySelectorAll("[data-trend-url]").forEach(drawTrend);
    root.querySelectorAll("[data-donut]").forEach(drawDonut);
    root.querySelectorAll("[data-shot-bars]").forEach(drawShotBars);
  }

  document.addEventListener("DOMContentLoaded", function () { setUp(document); paintThemeToggles(); });
  // Content swapped in by HTMX gets the same treatment.
  document.addEventListener("htmx:load", function (event) { setUp(event.target); });
})();
