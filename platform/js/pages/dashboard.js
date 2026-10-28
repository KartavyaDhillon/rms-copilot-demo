/* pages/dashboard.js — the new front page.
   Answers: what happened today · what's running · what's broken · what's next.
   All data is REAL: /ops/summary, /ops/timeline, /ops/health, /ops/brief. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false, briefTimer = null;

  const HP_META = [
    ["smtp", "SMTP", "mail", "Email relay"],
    ["ftp", "FTP", "layers", "File server"],
    ["postgres", "PostgreSQL", "database", "Backups DB"],
    ["internet", "Internet", "globe", "Outbound"],
    ["groq_ai", "Groq AI", "sparkles", "Error explain"],
    ["chrome", "Chrome", "monitor", "Browser bots"],
    ["outlook", "Outlook", "send", "Desktop mail"],
    ["fastapi", "FastAPI", "zap", "This server"],
  ];

  function kpiCard(id, icon, label, glow) {
    return '<div class="card kpi hover" id="' + id + '">' +
      '<span class="k-glow" style="background:' + (glow || "var(--accent)") + '"></span>' +
      '<div class="k-label">' + RMS.icon(icon) + label + "</div>" +
      '<div class="k-value"><span class="skel" style="display:inline-block;width:56px"></span></div>' +
      '<div class="k-foot"></div></div>';
  }

  const FILTERS = [["all", "Everything"], ["todo", "Still to do"], ["attn", "Needs attention"], ["sched", "Scheduler"], ["box", "Process Center"]];

  function shell() {
    return (
      '<div class="dash-greet"><div><h2 id="dgHi">—</h2><div class="sub" id="dgSub"></div></div>' +
      '<div style="display:flex;gap:8px"><button class="btn" id="dgHist">' + RMS.icon("history") + " History</button>" +
      '<button class="btn primary" id="dgGo">' + RMS.icon("play") + " Open Process Center</button></div></div>" +

      /* one plain-language status banner */
      '<div class="d-banner ok" id="dBanner"><span class="d-b-ic" id="dBanIc"></span><div class="d-b-txt"><b id="dBanT">Checking…</b><small id="dBanS"></small></div>' +
      '<div class="d-b-prog"><div class="d-b-num" id="dBanNum">—</div><div class="d-b-bar"><i id="dBanBar" style="width:0"></i></div><small id="dBanFoot"></small></div></div>' +

      '<div class="kpi-grid d-kpis">' +
      kpiCard("kRuns", "check", "Finished today", "var(--ok)") +
      kpiCard("kRunning", "activity", "Running now", "var(--info)") +
      kpiCard("kFail", "alert", "Needs attention", "var(--err)") +
      kpiCard("kNext", "clock", "Next up", "var(--warn)") +
      kpiCard("kFiles", "download", "Files downloaded") +
      kpiCard("kMail", "mail", "Emails sent") +
      "</div>" +

      '<div class="dash-cols eq" style="margin-top:14px">' +
      '<div class="card tl-card"><div class="card-head"><div class="card-title">' + RMS.icon("clock") + " Today's Timeline</div>" +
      '<span class="card-sub" id="tlSub"></span></div>' +
      '<div class="d-chips" id="tlChips">' + FILTERS.map(([k, n]) => '<button class="chip' + (k === "all" ? " on" : "") + '" data-f="' + k + '">' + n + "</button>").join("") + "</div>" +
      '<div class="tl-body"><div class="tl" id="tlList"></div></div></div>' +

      '<div class="d-side">' +
      '<div class="ai-card"><div class="ai-inner">' +
      '<div class="card-head" style="margin-bottom:8px"><div class="card-title">' + RMS.icon("sparkles") + ' AI Copilot</div>' +
      '<div style="display:flex;gap:6px;align-items:center"><span class="badge dim" id="aiBadge">…</span>' +
      '<button class="btn sm ghost" id="aiRefresh" title="Regenerate">' + RMS.icon("refresh") + "</button></div></div>" +
      '<div class="ai-text" id="aiText"><span class="skel" style="display:block;width:70%"></span></div>' +
      '<div class="ai-meta" id="aiMeta"></div></div></div>' +

      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("alert") + " Needs your attention</div>" +
      '<span class="card-sub" id="attnSub"></span></div><div class="feed" id="attnList"></div></div>' +

      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("history") + " Recent Activity</div>" +
      '<button class="btn sm ghost" id="feedMore">View all</button></div>' +
      '<div class="feed" id="feedList"></div></div>' +
      "</div></div>" +

      '<div class="sec-head"><h3>System</h3><span id="resSub"></span></div>' +
      '<div class="dash-cols sys">' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("activity") + " Services &amp; connections</div>" +
      '<div style="display:flex;gap:8px;align-items:center"><span class="card-sub" id="hpSub"></span><button class="btn sm ghost" id="hpRefresh">' + RMS.icon("refresh") + "</button></div></div>" +
      '<div class="hp-grid hp-4" id="hpGrid"></div></div>' +
      '<div class="d-side">' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("gauge") + ' Server resources</div></div><div class="res-strip res-col" id="resStrip"></div></div>' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("sparkles") + " Insights</div>" +
      '<span class="card-sub" id="insSub">anomaly checks</span></div><div class="feed" id="insList"><div class="bm-empty">Analyzing…</div></div></div>' +
      "</div></div>"
    );
  }

  /* ── renderers ───────────────────────────────────────────────────────── */
  function setKpi(id, valueHTML, foot, accent) {
    const el = $("#" + id); if (!el) return;
    el.querySelector(".k-value").innerHTML = valueHTML;
    el.querySelector(".k-foot").textContent = foot || "";
    el.className = "card kpi hover" + (accent ? " accent-" + accent : "");
  }

  let lastSum = null, lastOv = null, tlFilter = "all";

  const ST = {
    done: ["ok", "Finished"], completed: ["ok", "Finished"], running: ["info", "Running"],
    due: ["warn", "Ready to start"], overdue: ["err", "Late"], failed: ["err", "Failed"],
    missed: ["err", "Missed"], waiting: ["warn", "Waiting"], upcoming: ["dim", "Later today"],
  };
  const isDone = (st) => st === "done" || st === "completed";
  const isAttn = (st) => st === "failed" || st === "missed" || st === "overdue";
  const hhmm = (m) => String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");

  function renderGreeting(s) {
    const h = new Date().getHours();
    const name = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    $("#dgHi").textContent = name + ".";
    $("#dgSub").textContent = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  }

  function attnItems() {
    return ((lastOv && lastOv.items) || []).filter((i) => isAttn(i.status));
  }

  function renderAttnKpi(s) {
    const attn = attnItems().length + (s.failed_today || 0);
    setKpi("kFail", String(attn), attn ? "failed or missed \u2014 see list below" : "no problems today", attn ? "err" : "ok");
  }

  function renderBanner(s) {
    const total = s.runs_total || 0, done = s.runs_completed || 0;
    const attn = attnItems().length + (s.failed_today || 0);
    const running = (s.running || []).length;
    const ban = $("#dBanner");
    const cls = attn ? (attn >= 3 ? "err" : "warn") : "ok";
    ban.className = "d-banner " + cls;
    $("#dBanIc").innerHTML = RMS.icon(attn ? "alert" : "check");
    $("#dBanT").textContent = attn ? attn + (attn === 1 ? " thing needs" : " things need") + " your attention" : "Everything is running smoothly";
    $("#dBanS").textContent = attn
      ? "Check the \u201cNeeds your attention\u201d list below \u2014 a task failed or was missed."
      : (running ? "A task is running right now. Nothing has failed or been missed today." : "Nothing has failed or been missed today.");
    const pct = total ? Math.round(100 * done / total) : 0;
    $("#dBanNum").innerHTML = done + '<small> of ' + total + " tasks done</small>";
    $("#dBanBar").style.width = pct + "%";
    const b = s.boxes || {}, sc = s.sched || {};
    $("#dBanFoot").textContent = pct + "% complete \u00b7 Process Center " + (b.done || 0) + "/" + (b.total || 0) + " \u00b7 Scheduler " + (sc.done || 0) + "/" + (sc.total || 0);
  }

  function renderSummary(s) {
    lastSum = s;
    renderGreeting(s);
    renderBanner(s);
    const b = s.boxes || {}, sc = s.sched || {};
    setKpi("kRuns", s.runs_completed + '<small> / ' + s.runs_total + "</small>",
      "Process Center " + (b.done || 0) + "/" + (b.total || 0) + " \u00b7 Scheduler " + (sc.done || 0) + "/" + (sc.total || 0), s.runs_completed ? "ok" : "");
    if (s.running && s.running.length) {
      const r = s.running[0];
      setKpi("kRunning", '<span style="display:inline-flex;align-items:center;gap:8px"><span class="spinner"></span>' + esc(r.label) + "</span>",
        (r.by && r.by !== "unknown user" ? "by " + r.by + " \u00b7 " : "") +
        "started " + (r.since ? RMS.fmtAgo(r.since) : "recently") + (r.ip ? " \u00b7 " + r.ip : "") +
        (s.running.length > 1 ? " \u00b7 +" + (s.running.length - 1) + " more" : ""));
      $("#navRunDot").hidden = false;
    } else { setKpi("kRunning", "0", "nothing running right now"); $("#navRunDot").hidden = true; }
    renderAttnKpi(s);
    setKpi("kFiles", String(s.files_today ?? "\u2014"), "across all download folders");
    setKpi("kMail", String(s.emails_today ?? 0) + (s.whatsapp_today ? '<small> +' + s.whatsapp_today + " WA</small>" : ""), "detected in today's run logs");
    renderNext();

    // recent activity feed
    const feed = $("#feedList");
    const rec = s.recent || [];
    feed.innerHTML = rec.length ? rec.map((e, i) =>
      '<div class="feed-item" data-i="' + i + '"><span class="feed-ic ' + e.status + '">' +
      RMS.icon(e.status === "ok" ? "check" : e.status === "failed" ? "alert" : e.status === "stopped" ? "stop" : "clock") +
      "</span><div class=\"feed-main\"><b>" + esc(e.label) + "</b><small>" + esc(e.pipe_name) + (e.duration_s ? " \u00b7 " + RMS.fmtDur(e.duration_s) : "") + "</small></div>" +
      '<span class="badge ' + (e.status === "ok" ? "ok" : e.status === "failed" ? "err" : "dim") + '">' + (e.status === "ok" ? "ok" : e.status) + "</span>" +
      '<span class="feed-time">' + esc((e.start || "").slice(11, 16)) + "</span></div>").join("")
      : '<div class="bm-empty">No executions logged today yet.</div>';
    feed.querySelectorAll(".feed-item").forEach((el) => { el.onclick = () => RMS.openLogViewer(rec[+el.dataset.i]); });
    renderAttention();
    RMS.digestRecent(rec);
  }

  function renderNext() {
    if (!lastOv) return;
    const now = lastOv.now_min;
    const nx = (lastOv.items || []).find((i) => i.start_min != null && i.start_min >= now && i.status === "upcoming") ||
               (lastOv.items || []).find((i) => i.status === "due");
    if (!nx) { setKpi("kNext", "\u2014", "nothing more scheduled today"); return; }
    const d = nx.start_min - now;
    const eta = nx.status === "due" ? "ready now" : d <= 0 ? "starting" : d < 60 ? "in " + d + " min" : "in " + Math.floor(d / 60) + "h " + (d % 60) + "m";
    setKpi("kNext", hhmm(nx.start_min) + '<small> ' + eta + "</small>", nx.pipe_name + (nx.source === "scheduler" ? " \u00b7 " + nx.id : " \u00b7 " + nx.id), nx.status === "due" ? "warn" : "");
  }

  function renderAttention() {
    const box = $("#attnList"); if (!box) return;
    const rows = [];
    attnItems().forEach((i) => rows.push({ st: i.status, title: i.pipe_name, sub: (i.source === "scheduler" ? "Scheduler \u00b7 " : "Process Center \u00b7 ") + i.id + " \u00b7 planned " + (i.window || "").split("-")[0] }));
    ((lastSum && lastSum.recent) || []).filter((e) => e.status === "failed").forEach((e) =>
      rows.push({ st: "failed", title: e.pipe_name + " \u2014 " + e.label, sub: "Failed at " + (e.start || "").slice(11, 16) + " \u00b7 click to open the log", entry: e }));
    $("#attnSub").textContent = rows.length ? rows.length + " item" + (rows.length > 1 ? "s" : "") : "";
    if (!rows.length) { box.innerHTML = '<div class="d-allgood">' + RMS.icon("check") + "<b>All clear</b><small>Nothing failed or was missed today.</small></div>"; return; }
    box.innerHTML = rows.slice(0, 8).map((r, k) =>
      '<div class="feed-item" data-k="' + k + '" style="' + (r.entry ? "" : "cursor:default") + '"><span class="feed-ic failed">' + RMS.icon("alert") + "</span>" +
      '<div class="feed-main"><b>' + esc(r.title) + "</b><small>" + esc(r.sub) + "</small></div>" +
      '<span class="badge err">' + (ST[r.st] || ST.failed)[1] + "</span><span></span></div>").join("");
    box.querySelectorAll("[data-k]").forEach((el) => { const r = rows[+el.dataset.k]; if (r && r.entry) el.onclick = () => RMS.openLogViewer(r.entry); });
  }

  function renderTimeline(t) {
    lastOv = t;
    const list = $("#tlList");
    const all = t.items || [];
    const pass = (i) => tlFilter === "all" ? true : tlFilter === "todo" ? !isDone(i.status)
      : tlFilter === "attn" ? isAttn(i.status) : tlFilter === "sched" ? i.source === "scheduler" : i.source !== "scheduler";
    const items = all.filter(pass);
    $("#tlSub").textContent = all.filter((i) => isDone(i.status)).length + " finished \u00b7 " +
      all.filter((i) => !isDone(i.status)).length + " to go";
    let html = "", nowInserted = false;
    items.forEach((i) => {
      if (!nowInserted && i.start_min != null && i.start_min > t.now_min) {
        html += '<div class="tl-now">NOW ' + hhmm(t.now_min) + "</div>";
        nowInserted = true;
      }
      const st = i.status, m = ST[st] || ST.upcoming;
      const cls = isDone(st) ? "completed" : st;
      const badge = st === "running" ? '<span class="badge info"><span class="spinner" style="width:9px;height:9px;border-width:1.5px"></span> Running</span>'
        : '<span class="badge ' + m[0] + '">' + (isDone(st) ? "\u2713 " : "") + m[1] + "</span>";
      const src = i.source === "scheduler" ? '<span class="src-tag sched">Scheduler</span>' : '<span class="src-tag box">Process Center</span>';
      const sub = i.source === "scheduler" ? (i.id && i.id !== i.pipe_name ? i.id : "") : i.id;
      html += '<div class="tl-item ' + cls + '"><span class="tl-time">' + esc((i.window || "").split("-")[0] || "\u2014") + "</span>" +
        '<span class="tl-rail"><span class="tl-dot"></span></span>' +
        '<span class="tl-main"><span class="tl-name2"><b>' + esc(i.pipe_name) + "</b>" + (sub ? "<small>" + esc(sub) + "</small>" : "") + "</span></span>" + src + badge + "</div>";
    });
    if (!nowInserted && items.length) html += '<div class="tl-now">NOW ' + hhmm(t.now_min) + " \u2014 end of schedule</div>";
    list.innerHTML = html || '<div class="bm-empty">Nothing matches this filter.</div>';
    const now = list.querySelector(".tl-now");
    if (now && list.dataset.scrolled !== tlFilter) { list.scrollTop = Math.max(0, now.offsetTop - list.clientHeight / 2); list.dataset.scrolled = tlFilter; }
    if (lastSum) { renderBanner(lastSum); renderAttnKpi(lastSum); renderAttention(); }
    renderNext();
  }

  function meterClass(p) { return p == null ? "" : p > 88 ? "err" : p > 72 ? "warn" : ""; }

  function renderHealth(h) {
    const grid = $("#hpGrid");
    grid.innerHTML = HP_META.map(([key, name, icon, sub]) => {
      const r = h[key] || {};
      const up = !!r.up;
      const detail = r.ms != null ? r.ms + " ms" : r.count != null ? r.count + " proc" : r.configured === false ? "no key" : up ? "online" : (r.err || "offline");
      return '<div class="hp-card ' + (up ? "up" : "down") + '"><span class="hp-ic">' + RMS.icon(icon) + "</span>" +
        '<div class="hp-body"><b>' + name + "</b><small>" + esc(sub) + " · " + esc(detail) + "</small></div>" +
        '<span class="hp-state">' + (up ? "ONLINE" : "DOWN") + "</span></div>";
    }).join("");
    $("#hpSub").textContent = "checked " + (h.checked_at || "—");

    // resources
    const res = h.resources || {};
    const rows = [];
    if (res.cpu != null) rows.push(["cpu", "CPU", res.cpu]);
    if (res.ram != null) rows.push(["gauge", "Memory", res.ram]);
    (res.disks || []).forEach((d) => rows.push(["disk", "Disk " + d.drive, d.pct, d.free_gb + " GB free"]));
    $("#resStrip").innerHTML = rows.map(([ic, name, pct, extra]) =>
      '<div class="res"><div class="r-top"><span style="display:inline-flex;gap:6px;align-items:center">' + RMS.icon(ic) + " " + name +
      "</span><b>" + Math.round(pct) + "%" + (extra ? ' <small style="color:var(--text-3);font-weight:500">' + extra + "</small>" : "") + "</b></div>" +
      '<div class="meter ' + meterClass(pct) + '"><i style="width:' + Math.min(100, pct) + '%"></i></div></div>').join("") ||
      '<span class="card-sub">Resource metrics unavailable.</span>';
    const up = res.uptime_s;
    $("#resSub").textContent = up ? "server up " + RMS.fmtDur(up) + (res.cpu == null ? " · install psutil for CPU%" : "") : "";
    const sUp = $("#serverUp");
    if (sUp && up) sUp.textContent = "up " + RMS.fmtDur(up);
  }

  function typewrite(el, text) {
    if (RMS.prefs.anim === false) { el.textContent = text; return; }
    el.innerHTML = "";
    let i = 0;
    const cur = '<span class="cursor"></span>';
    clearInterval(briefTimer);
    briefTimer = setInterval(() => {
      i += 3;
      el.innerHTML = esc(text.slice(0, i)) + (i < text.length ? cur : "");
      if (i >= text.length) clearInterval(briefTimer);
    }, 14);
  }

  function loadBrief(refresh) {
    const badge = $("#aiBadge");
    if (refresh) { badge.textContent = "thinking…"; $("#aiText").innerHTML = '<span class="skel" style="display:block;width:60%"></span>'; }
    RMS.api("/ops/brief" + (refresh ? "?refresh=1" : ""), { timeout: 15000 }).then((b) => {
      typewrite($("#aiText"), b.text || "—");
      badge.textContent = b.ai ? "✦ AI generated" : "rule-based";
      badge.className = "badge " + (b.ai ? "info" : "dim");
      $("#aiMeta").innerHTML = "Generated at " + esc(b.generated_at || "—") +
        ((b.due && b.due.length) ? ' · <b style="color:var(--warn)">' + b.due.length + " run(s) waiting</b>" : "");
    }).catch(() => { $("#aiText").textContent = "Briefing unavailable — backend unreachable."; badge.textContent = "offline"; });
  }

  /* ── page lifecycle ──────────────────────────────────────────────────── */
  RMS.pages = RMS.pages || {};
  RMS.pages.dashboard = {
    title: "Dashboard", crumb: "Overview of today's operations",
    mount(el) {
      if (!booted) {
        el.innerHTML = shell();
        RMS.mountIcons(el);
        $("#dgGo").onclick = () => RMS.go("process");
        $("#dgHist").onclick = () => RMS.go("history");
        $("#kFail").onclick = () => RMS.go("history");
        $("#tlChips").querySelectorAll("button").forEach((b) => {
          b.onclick = () => {
            tlFilter = b.dataset.f;
            $("#tlChips").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
            if (lastOv) renderTimeline(lastOv);
          };
        });
        $("#feedMore").onclick = () => RMS.go("history");
        $("#aiRefresh").onclick = () => loadBrief(true);
        $("#hpRefresh").onclick = () => { RMS.api("/ops/health").then(renderHealth).catch(() => {}); };
        document.addEventListener("rms:refresh-brief", () => loadBrief(true));
        // lock stream fired → refresh the Running/Due KPIs immediately
        document.addEventListener("rms:locks", () => {
          RMS.api("/ops/summary", { quiet: true }).then(renderSummary).catch(() => {});
        });
        RMS.poll(() => {
          RMS.api("/ops/summary").then(renderSummary).catch(() => {});
          RMS.api("/ops/overview").then(renderTimeline).catch(() => {});
        }, 12000);
        RMS.poll(() => RMS.api("/ops/health").then(renderHealth).catch(() => {}), 45000);
        // insight engine: deterministic anomaly checks over the knowledge index
        RMS.poll(() => RMS.api("/kb/insights", { quiet: true, timeout: 25000 }).then((d) => {
          const items = d.items || [];
          $("#insSub").textContent = "checked " + (d.generated_at || "—");
          $("#insList").innerHTML = items.map((it, i) => {
            const cls = it.sev === "err" ? "failed" : it.sev === "warn" ? "stopped" : "ok";
            const ic = it.sev === "err" ? "alert" : it.sev === "warn" ? "clock" : "check";
            return '<div class="feed-item" data-ins="' + i + '" style="' + (it.entry ? "" : "cursor:default") + '">' +
              '<span class="feed-ic ' + cls + '">' + RMS.icon(ic) + '</span>' +
              '<div class="feed-main"><b style="font-weight:600;font-size:12px;white-space:normal">' +
              RMS.esc(it.text) + "</b></div></div>";
          }).join("") || '<div class="bm-empty">No insights yet.</div>';
          $("#insList").querySelectorAll("[data-ins]").forEach((el) => {
            const it = items[+el.dataset.ins];
            if (it && it.entry) el.onclick = () => RMS.openLogViewer(it.entry);
          });
        }).catch(() => {}), 300000);
        loadBrief(false);
        booted = true;
      }
    },
  };
})();
