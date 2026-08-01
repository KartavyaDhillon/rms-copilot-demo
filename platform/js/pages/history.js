/* pages/history.js — Execution History.
   Every log the pipelines ever wrote, indexed by /ops/history: filter by box,
   status, date range; per-day success/fail chart; click any row for the
   interactive log viewer (search, colorize, copy, download). */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false;
  const state = { days: 7, pipe: "all", status: "all", q: "", limit: 100, items: [] };

  function shell() {
    return (
      '<div class="card" style="margin-bottom:12px"><div class="filters">' +
      '<div class="seg" id="hDays">' +
      '<button data-d="1">Today</button><button data-d="3">3d</button>' +
      '<button data-d="7" class="on">7d</button><button data-d="30">30d</button></div>' +
      '<select class="f-select" id="hPipe"><option value="all">All pipelines</option>' +
      '<option value="ctcl">CTCL vs NMASS</option><option value="sa">Short Allocation</option>' +
      '<option value="ms15">Margin Shortage (15-min)</option><option value="cli">Client Prudential Limit</option></select>' +
      '<select class="f-select" id="hStatus"><option value="all">Any status</option>' +
      '<option value="ok">✓ Success</option><option value="failed">✕ Failed</option>' +
      '<option value="stopped">⛔ Stopped</option><option value="unknown">? Unknown</option></select>' +
      '<input class="f-input" id="hSearch" placeholder="Search step / file name…">' +
      '<div class="spacer"></div>' +
      '<button class="btn sm ghost" id="hRefresh">' + RMS.icon("refresh") + " Refresh</button>" +
      "</div></div>" +

      '<div class="dash-cols" style="margin-bottom:12px;grid-template-columns:1fr 1.6fr">' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("gauge") + " Filtered Stats</div></div>" +
      '<div class="kpi-grid" style="grid-template-columns:repeat(2,1fr)" id="hStats"></div></div>' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("chart") + " Runs per Day</div>" +
      '<span class="card-sub">green = success · red = failed</span></div>' +
      '<div class="bars" id="hBars"></div><div class="bars-lbl" id="hBarsLbl"></div></div></div>' +

      '<div class="card" style="padding:0;overflow:hidden">' +
      '<div style="max-height:560px;overflow:auto"><table class="htable"><thead><tr>' +
      "<th>Time</th><th>Pipeline</th><th>Step</th><th>Status</th><th>Duration</th><th>Emails</th><th>Size</th>" +
      '</tr></thead><tbody id="hRows"></tbody></table></div>' +
      '<div style="padding:10px;display:flex;justify-content:center;border-top:1px solid var(--line)">' +
      '<button class="btn sm ghost" id="hMore">Load more</button></div></div>' +

      '<div class="sec-head"><h3>Audit Trail</h3><span>who started / stopped / was rejected — from the lock manager</span></div>' +
      '<div class="card"><div class="feed" id="hAudit"><div class="bm-empty">Loading…</div></div></div>'
    );
  }

  function statLine(label, value, cls) {
    return '<div class="card kpi" style="padding:12px"><div class="k-label">' + label + "</div>" +
      '<div class="k-value" style="font-size:21px" ' + (cls ? 'data-c="' + cls + '"' : "") + ">" + value + "</div></div>";
  }

  function filtered() {
    return state.items.filter((e) =>
      (state.pipe === "all" || e.pipe === state.pipe) &&
      (state.status === "all" || e.status === state.status) &&
      (!state.q || (e.label + " " + e.file + " " + e.pipe_name).toLowerCase().includes(state.q)));
  }

  function render() {
    const items = filtered();
    const ok = items.filter((e) => e.status === "ok").length;
    const fail = items.filter((e) => e.status === "failed").length;
    const durs = items.filter((e) => e.duration_s).map((e) => e.duration_s);
    $("#hStats").innerHTML =
      statLine("Total", items.length) +
      statLine("Success", '<span style="color:var(--ok)">' + ok + "</span>") +
      statLine("Failed", '<span style="color:var(--err)">' + fail + "</span>") +
      statLine("Avg time", RMS.fmtDur(durs.length ? durs.reduce((a, b) => a + b, 0) / durs.length : null));

    // per-day bars
    const days = {};
    for (let i = state.days - 1; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400000);
      days[d.toISOString().slice(0, 10)] = { ok: 0, fail: 0 };
    }
    items.forEach((e) => { if (days[e.date]) { e.status === "failed" ? days[e.date].fail++ : e.status === "ok" ? days[e.date].ok++ : 0; } });
    const keys = Object.keys(days);
    const max = Math.max(1, ...keys.map((k) => days[k].ok + days[k].fail));
    $("#hBars").innerHTML = keys.map((k) =>
      '<div class="bcol" title="' + k + ": " + days[k].ok + " ok / " + days[k].fail + ' failed">' +
      '<i class="b-ok" style="height:' + Math.round(58 * days[k].ok / max) + 'px"></i>' +
      '<i class="b-err" style="height:' + Math.round(58 * days[k].fail / max) + 'px"></i></div>').join("");
    $("#hBarsLbl").innerHTML = keys.map((k) => "<span>" + k.slice(8) + "/" + k.slice(5, 7) + "</span>").join("");

    // table
    const rows = items.slice(0, state.limit);
    $("#hRows").innerHTML = rows.length ? rows.map((e, i) => {
      const b = e.status === "ok" ? '<span class="badge ok">✓ success</span>'
        : e.status === "failed" ? '<span class="badge err">✕ failed</span>'
        : e.status === "stopped" ? '<span class="badge warn">⛔ stopped</span>'
        : '<span class="badge dim">unknown</span>';
      return '<tr data-i="' + i + '"><td class="mono">' + esc(e.start.slice(5, 16)) + "</td>" +
        "<td>" + esc(e.pipe_name) + "</td><td><b>" + esc(e.label) + "</b></td>" +
        "<td>" + b + "</td><td class=\"mono\">" + RMS.fmtDur(e.duration_s) + "</td>" +
        '<td class="mono">' + (e.emails || "—") + "</td><td class=\"mono\">" + RMS.fmtBytes(e.size) + "</td></tr>";
    }).join("") : '<tr><td colspan="7"><div class="bm-empty">No executions match these filters.</div></td></tr>';
    $("#hRows").querySelectorAll("tr[data-i]").forEach((tr) => {
      tr.onclick = () => RMS.openLogViewer(rows[+tr.dataset.i]);
    });
    $("#hMore").hidden = items.length <= state.limit;
  }

  function renderAudit(events) {
    const box = $("#hAudit");
    if (!events.length) {
      box.innerHTML = '<div class="bm-empty">No audit events yet — they appear as soon as someone starts a run.</div>';
      return;
    }
    box.innerHTML = events.slice(0, 30).map((a) => {
      const kind = a.event === "reject" ? "failed"
        : a.event === "start" ? "unknown"
        : a.outcome === "completed" ? "ok" : a.outcome === "stopped" ? "stopped" : a.outcome === "failed" ? "failed" : "unknown";
      const icon = a.event === "reject" ? "alert" : a.event === "start" ? "play" : kind === "ok" ? "check" : kind === "failed" ? "alert" : "stop";
      const who = a.by && a.by !== "unknown user" ? a.by : "Someone";
      const text = a.event === "start" ? who + " started " + (a.label || a.lock)
        : a.event === "reject" ? who + " tried to start " + (a.label || a.lock) + " — rejected (already running" + (a.holder ? " by " + a.holder : "") + ")"
        : (a.label || a.lock) + " " + (a.outcome || "ended") + (a.duration_s ? " after " + RMS.fmtDur(a.duration_s) : "");
      return '<div class="feed-item" style="cursor:default"><span class="feed-ic ' + kind + '">' + RMS.icon(icon) + "</span>" +
        '<div class="feed-main"><b>' + esc(text) + "</b><small>" + esc(a.ip || "") + "</small></div>" +
        '<span class="feed-time">' + esc((a.ts || "").slice(5, 16).replace("T", " ")) + "</span></div>";
    }).join("");
  }

  function load() {
    RMS.api("/ops/history?days=" + state.days, { timeout: 20000 }).then((d) => {
      state.items = d.items || [];
      RMS._histCache = state.items;   // feeds the command palette's "Recent logs"
      render();
    }).catch(() => {});
    RMS.api("/ops/audit?limit=60", { quiet: true }).then((d) => renderAudit(d.events || [])).catch(() => {});
  }

  RMS.pages = RMS.pages || {};
  RMS.pages.history = {
    title: "Execution History", crumb: "Every pipeline run, indexed from the log archive",
    mount(el) {
      if (!booted) {
        el.innerHTML = shell();
        RMS.mountIcons(el);
        $("#hDays").querySelectorAll("button").forEach((b) => {
          b.onclick = () => {
            $("#hDays").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
            b.classList.add("on");
            state.days = +b.dataset.d; state.limit = 100; load();
          };
        });
        $("#hPipe").onchange = (e) => { state.pipe = e.target.value; render(); };
        $("#hStatus").onchange = (e) => { state.status = e.target.value; render(); };
        $("#hSearch").oninput = (e) => { state.q = e.target.value.trim().toLowerCase(); render(); };
        $("#hRefresh").onclick = load;
        $("#hMore").onclick = () => { state.limit += 150; render(); };
        booted = true;
      }
      load();
    },
  };
})();
