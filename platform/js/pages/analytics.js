/* pages/analytics.js — professional analytics over real execution history.
   Everything is computed from /ops/history (the indexed log archive):
   runs/day, success trend, runtime trend + distribution, per-pipeline split,
   busiest hours, leaders (most executed / slowest), CSV export. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false;
  const state = { days: 14, pipe: "all", items: [] };
  const PIPE_COLORS = { ctcl: "#60a5fa", sa: "#2dd4bf", ms15: "#fbbf24", cli: "#a78bfa" };

  function shell() {
    return (
      '<div class="card" style="margin-bottom:12px"><div class="filters">' +
      '<div class="seg" id="aDays"><button data-d="7">7d</button><button data-d="14" class="on">14d</button><button data-d="30">30d</button></div>' +
      '<select class="f-select" id="aPipe"><option value="all">All pipelines</option>' +
      '<option value="ctcl">CTCL vs NMASS</option><option value="sa">Short Allocation</option>' +
      '<option value="ms15">Margin Shortage (15-min)</option><option value="cli">Client Prudential Limit</option></select>' +
      '<div class="spacer"></div>' +
      '<button class="btn sm" id="aExport">' + RMS.icon("download") + " Export CSV</button>" +
      '<button class="btn sm ghost" id="aRefresh">' + RMS.icon("refresh") + "</button></div></div>" +

      '<div class="kpi-grid" id="aKpis" style="margin-bottom:12px"></div>' +

      '<div class="dash-cols" style="margin-bottom:12px">' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("chart") + ' Runs per Day</div>' +
      '<span class="card-sub">green = success · red = failed</span></div><div id="aRunsChart"></div></div>' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("gauge") + ' Pipeline Split</div></div>' +
      '<div style="display:flex;gap:18px;align-items:center" id="aSplit"></div></div></div>' +

      '<div class="dash-cols" style="margin-bottom:12px">' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("activity") + ' Success Rate Trend</div>' +
      '<span class="card-sub">% per day</span></div><div id="aSuccChart"></div></div>' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("clock") + ' Avg Runtime Trend</div>' +
      '<span class="card-sub">seconds per day</span></div><div id="aRtChart"></div></div></div>' +

      '<div class="dash-cols">' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("layers") + ' Runtime Distribution</div>' +
      '<span class="card-sub">successful executions</span></div><div id="aHist"></div></div>' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("zap") + ' Busiest Hours</div>' +
      '<span class="card-sub">executions by start hour</span></div><div id="aHours"></div></div></div>' +

      '<div class="sec-head"><h3>Leaders</h3><span>within the selected range</span></div>' +
      '<div class="dash-cols" style="grid-template-columns:1fr 1fr">' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("check") + ' Most Executed Steps</div></div><div id="aTop"></div></div>' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("alert") + ' Slowest Steps (avg)</div></div><div id="aSlow"></div></div></div>'
    );
  }

  function kpi(icon, label, value, foot) {
    return '<div class="card kpi"><div class="k-label">' + RMS.icon(icon) + label + "</div>" +
      '<div class="k-value" style="font-size:22px">' + value + '</div><div class="k-foot">' + (foot || "") + "</div></div>";
  }

  function filtered() {
    return state.items.filter((e) => state.pipe === "all" || e.pipe === state.pipe);
  }

  function dayKeys() {
    const out = [];
    for (let i = state.days - 1; i >= 0; i--) out.push(new Date(Date.now() - i * 86400000).toISOString().slice(0, 10));
    return out;
  }

  function render() {
    const items = filtered();
    const ok = items.filter((e) => e.status === "ok"), fail = items.filter((e) => e.status === "failed");
    const durs = ok.filter((e) => e.duration_s).map((e) => e.duration_s);
    const finished = ok.length + fail.length;
    const dlOk = ok.filter((e) => /download|mcx|nmass|ncdex/i.test(e.label)).length;
    const emails = items.reduce((a, e) => a + (e.emails || 0), 0);

    // leaders
    const byLabel = {};
    items.forEach((e) => {
      const k = e.pipe_name + " · " + e.label;
      (byLabel[k] = byLabel[k] || { n: 0, durs: [] }).n++;
      if (e.status === "ok" && e.duration_s) byLabel[k].durs.push(e.duration_s);
    });
    const top = Object.entries(byLabel).sort((a, b) => b[1].n - a[1].n).slice(0, 7);
    const slow = Object.entries(byLabel)
      .map(([k, v]) => [k, v.durs.length ? v.durs.reduce((a, b) => a + b, 0) / v.durs.length : 0, v.durs.length])
      .filter((x) => x[2] >= 2).sort((a, b) => b[1] - a[1]).slice(0, 7);

    $("#aKpis").innerHTML =
      kpi("terminal", "Executions", items.length, state.days + " days") +
      kpi("gauge", "Success Rate", finished ? Math.round(100 * ok.length / finished) + "%" : "—", ok.length + " ok · " + fail.length + " failed") +
      kpi("clock", "Avg Runtime", RMS.fmtDur(durs.length ? durs.reduce((a, b) => a + b, 0) / durs.length : null), "total " + RMS.fmtDur(durs.reduce((a, b) => a + b, 0))) +
      kpi("download", "Downloads OK", dlOk, "download-type steps") +
      kpi("mail", "Emails Sent", emails, "from run logs") +
      kpi("check", "Most Executed", top.length ? esc(top[0][0].split("·")[1] || top[0][0]).trim() : "—", top.length ? top[0][1].n + " executions" : "");

    // runs/day
    const days = dayKeys();
    const perDay = days.map((d) => {
      const de = items.filter((e) => e.date === d);
      return { label: d.slice(8) + "/" + d.slice(5, 7),
               ok: de.filter((e) => e.status === "ok").length,
               fail: de.filter((e) => e.status === "failed").length,
               durs: de.filter((e) => e.status === "ok" && e.duration_s).map((e) => e.duration_s) };
    });
    $("#aRunsChart").innerHTML = RMS.chartBars(perDay, 140);

    // success + runtime trends
    const succ = perDay.map((d) => (d.ok + d.fail) ? Math.round(100 * d.ok / (d.ok + d.fail)) : null);
    $("#aSuccChart").innerHTML = RMS.chartLine([{ name: "success", color: "var(--ok)", points: succ }], perDay.map((d) => d.label), 130, "%");
    const rt = perDay.map((d) => d.durs.length ? Math.round(d.durs.reduce((a, b) => a + b, 0) / d.durs.length) : null);
    $("#aRtChart").innerHTML = RMS.chartLine([{ name: "runtime", color: "var(--accent)", points: rt }], perDay.map((d) => d.label), 130, "s");

    // split donut
    const parts = ["ctcl", "sa", "ms15", "cli"].map((p) => ({
      label: { ctcl: "CTCL vs NMASS", sa: "Short Allocation", ms15: "Margin Shortage", cli: "Client Prudential" }[p],
      value: state.items.filter((e) => e.pipe === p).length, color: PIPE_COLORS[p],
    })).filter((p) => p.value);
    $("#aSplit").innerHTML = parts.length
      ? RMS.chartDonut(parts, 120) + '<div style="flex:1">' + RMS.legend(parts) + "</div>"
      : '<div class="bm-empty" style="flex:1">No executions in range.</div>';

    // histogram + hours
    $("#aHist").innerHTML = RMS.chartHist(durs, 120);
    const hours = Array.from({ length: 13 }, (_, i) => i + 8); // 08..20
    $("#aHours").innerHTML = RMS.chartBars(hours.map((h) => ({
      label: String(h).padStart(2, "0"),
      ok: items.filter((e) => +e.start.slice(11, 13) === h).length, fail: 0,
    })), 120);

    const li = (rows) => rows.length ? '<div class="feed">' + rows.join("") + "</div>" : '<div class="bm-empty">Not enough data.</div>';
    $("#aTop").innerHTML = li(top.map(([k, v]) =>
      '<div class="feed-item" style="cursor:default;grid-template-columns:1fr auto"><div class="feed-main"><b>' + esc(k) + "</b></div><b>" + v.n + "×</b></div>"));
    $("#aSlow").innerHTML = li(slow.map(([k, avg, n]) =>
      '<div class="feed-item" style="cursor:default;grid-template-columns:1fr auto"><div class="feed-main"><b>' + esc(k) + '</b><small>' + n + " samples</small></div><b>" + RMS.fmtDur(avg) + "</b></div>"));
  }

  function exportCSV() {
    const rows = [["start", "pipeline", "step", "status", "duration_s", "emails", "whatsapp", "size_bytes", "file"]];
    filtered().forEach((e) => rows.push([e.start, e.pipe_name, e.label, e.status, e.duration_s ?? "", e.emails, e.whatsapp, e.size, e.file]));
    RMS.downloadCSV(rows, "rms_analytics_" + state.days + "d.csv");
  }

  function load() {
    RMS.api("/ops/history?days=" + state.days, { timeout: 25000 }).then((d) => {
      state.items = d.items || []; render();
    }).catch(() => {});
  }

  RMS.pages = RMS.pages || {};
  RMS.pages.analytics = {
    title: "Analytics", crumb: "Execution intelligence from the run archive",
    mount(el) {
      if (!booted) {
        el.innerHTML = shell();
        RMS.mountIcons(el);
        $("#aDays").querySelectorAll("button").forEach((b) => {
          b.onclick = () => { $("#aDays").querySelectorAll("button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); state.days = +b.dataset.d; load(); };
        });
        $("#aPipe").onchange = (e) => { state.pipe = e.target.value; render(); };
        $("#aExport").onclick = exportCSV;
        $("#aRefresh").onclick = load;
        booted = true;
      }
      load();
    },
  };
})();
