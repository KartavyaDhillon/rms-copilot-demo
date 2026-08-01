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

  function shell() {
    return (
      '<div class="dash-greet"><div><h2 id="dgHi">—</h2><div class="sub" id="dgSub"></div></div>' +
      '<div style="display:flex;gap:8px"><button class="btn" id="dgHist">' + RMS.icon("history") + " History</button>" +
      '<button class="btn primary" id="dgGo">' + RMS.icon("play") + " Open Process Center</button></div></div>" +

      '<div class="dash-cols" style="margin-bottom:12px">' +
      '<div class="ai-card"><div class="ai-inner">' +
      '<div class="card-head" style="margin-bottom:10px"><div class="card-title">' + RMS.icon("sparkles") + ' AI Copilot</div>' +
      '<div style="display:flex;gap:6px;align-items:center"><span class="badge dim" id="aiBadge">…</span>' +
      '<button class="btn sm ghost" id="aiRefresh" title="Regenerate">' + RMS.icon("refresh") + "</button></div></div>" +
      '<div class="ai-text" id="aiText"><span class="skel" style="display:block;width:70%"></span></div>' +
      '<div class="ai-meta" id="aiMeta"></div></div></div>' +

      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("gauge") + " Today's Success</div>" +
      '<span class="card-sub" id="succSub"></span></div>' +
      '<div class="ring-wrap"><svg class="ring" width="92" height="92" viewBox="0 0 92 92">' +
      '<circle class="bgc" cx="46" cy="46" r="38" fill="none" stroke-width="9"/>' +
      '<circle class="fgc" id="succArc" cx="46" cy="46" r="38" fill="none" stroke-width="9" stroke-dasharray="238.8" stroke-dashoffset="238.8"/></svg>' +
      '<div><div class="k-value" id="succPct" style="font-size:30px">—</div>' +
      '<div class="k-foot" id="succFoot">of finished executions</div></div></div>' +
      '<div style="margin-top:12px" id="succMini"></div></div>' +
      "</div>" +

      '<div class="kpi-grid">' +
      kpiCard("kRuns", "check", "Runs Completed", "var(--ok)") +
      kpiCard("kRunning", "activity", "Running Now", "var(--info)") +
      kpiCard("kDue", "clock", "Due / Ready", "var(--warn)") +
      kpiCard("kFail", "alert", "Failed Today", "var(--err)") +
      kpiCard("kFiles", "download", "Files Downloaded") +
      kpiCard("kMail", "mail", "Emails Sent") +
      kpiCard("kAvg", "gauge", "Avg Runtime") +
      kpiCard("kExec", "terminal", "Executions") +
      "</div>" +

      '<div class="sec-head"><h3>System Resources</h3><span id="resSub"></span></div>' +
      '<div class="card"><div class="res-strip" id="resStrip"></div></div>' +

      '<div class="dash-cols" style="margin-top:12px">' +
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("clock") + " Today's Timeline</div>" +
      '<span class="card-sub" id="tlSub"></span></div><div class="tl" id="tlList"></div></div>' +
      '<div><div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("activity") + " System Health</div>" +
      '<button class="btn sm ghost" id="hpRefresh">' + RMS.icon("refresh") + '</button></div>' +
      '<div class="hp-grid" id="hpGrid"></div>' +
      '<div class="card-sub" id="hpSub" style="margin-top:10px;text-align:right"></div></div>' +
      '<div class="card" style="margin-top:12px"><div class="card-head"><div class="card-title">' + RMS.icon("sparkles") + " Insights</div>" +
      '<span class="card-sub" id="insSub">anomaly checks</span></div><div class="feed" id="insList"><div class="bm-empty">Analyzing…</div></div></div>' +
      '<div class="card" style="margin-top:12px"><div class="card-head"><div class="card-title">' + RMS.icon("history") + " Recent Activity</div>" +
      '<button class="btn sm ghost" id="feedMore">View all</button></div>' +
      '<div class="feed" id="feedList"></div></div></div>' +
      "</div>"
    );
  }

  /* ── renderers ───────────────────────────────────────────────────────── */
  function setKpi(id, valueHTML, foot, accent) {
    const el = $("#" + id); if (!el) return;
    el.querySelector(".k-value").innerHTML = valueHTML;
    el.querySelector(".k-foot").textContent = foot || "";
    el.className = "card kpi hover" + (accent ? " accent-" + accent : "");
  }

  function renderGreeting(s) {
    const h = new Date().getHours();
    const name = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    $("#dgHi").textContent = name + ".";
    $("#dgSub").textContent = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" }) +
      " · " + s.runs_completed + " of " + s.runs_total + " scheduled runs done";
  }

  function renderSummary(s) {
    renderGreeting(s);
    setKpi("kRuns", s.runs_completed + '<small> / ' + s.runs_total + "</small>", "scheduled slots today", s.runs_completed ? "ok" : "");
    if (s.running && s.running.length) {
      const r = s.running[0];
      setKpi("kRunning", '<span style="display:inline-flex;align-items:center;gap:8px"><span class="spinner"></span>' + esc(r.label) + "</span>",
        (r.by && r.by !== "unknown user" ? "by " + r.by + " · " : "") +
        "started " + (r.since ? RMS.fmtAgo(r.since) : "recently") +
        (r.ip ? " · " + r.ip : ""));
      $("#navRunDot").hidden = false;
    } else { setKpi("kRunning", "0", "nothing running right now"); $("#navRunDot").hidden = true; }
    const due = s.due_now || [];
    setKpi("kDue", String(due.length), due.length ? due[0].id + (due[0].overdue ? " is overdue" : " window open") : "nothing due right now", due.some((d) => d.overdue) ? "err" : due.length ? "warn" : "");
    setKpi("kFail", String(s.failed_today), s.failed_today ? "click to inspect in History" : "no failures today 🎉", s.failed_today ? "err" : "ok");
    setKpi("kFiles", String(s.files_today ?? "—"), "across all download folders");
    setKpi("kMail", String(s.emails_today ?? 0) + (s.whatsapp_today ? '<small> +' + s.whatsapp_today + " WA</small>" : ""), "detected in today's run logs");
    setKpi("kAvg", RMS.fmtDur(s.avg_runtime_s), "per successful execution · total " + RMS.fmtDur(s.total_runtime_s));
    setKpi("kExec", String(s.executions_today), "log files written today");

    // success ring
    const pct = s.success_rate;
    $("#succPct").textContent = pct == null ? "—" : pct + "%";
    $("#succSub").textContent = s.executions_today + " executions";
    const C = 238.8;
    const arc = $("#succArc");
    arc.style.strokeDashoffset = pct == null ? C : C * (1 - pct / 100);
    arc.style.stroke = pct == null ? "var(--line-2)" : pct >= 90 ? "var(--ok)" : pct >= 60 ? "var(--warn)" : "var(--err)";
    $("#succFoot").textContent = (s.failed_today || 0) + " failed · " + Math.max(0, (s.executions_today || 0) - (s.failed_today || 0)) + " clean";

    // activity feed
    const feed = $("#feedList");
    const rec = s.recent || [];
    feed.innerHTML = rec.length ? rec.map((e, i) =>
      '<div class="feed-item" data-i="' + i + '"><span class="feed-ic ' + e.status + '">' +
      RMS.icon(e.status === "ok" ? "check" : e.status === "failed" ? "alert" : e.status === "stopped" ? "stop" : "clock") +
      "</span><div class=\"feed-main\"><b>" + esc(e.label) + "</b><small>" + esc(e.pipe_name) + (e.duration_s ? " · " + RMS.fmtDur(e.duration_s) : "") + "</small></div>" +
      '<span class="badge ' + (e.status === "ok" ? "ok" : e.status === "failed" ? "err" : "dim") + '">' + e.status + "</span>" +
      '<span class="feed-time">' + esc((e.start || "").slice(11, 16)) + "</span></div>").join("")
      : '<div class="bm-empty">No executions logged today yet.</div>';
    feed.querySelectorAll(".feed-item").forEach((el) => {
      el.onclick = () => RMS.openLogViewer(rec[+el.dataset.i]);
    });

    RMS.digestRecent(rec);
  }

  function renderTimeline(t) {
    const list = $("#tlList");
    const items = t.items || [];
    $("#tlSub").textContent = items.filter((i) => i.status === "completed").length + " done · " +
      items.filter((i) => ["due", "overdue"].includes(i.status)).length + " due";
    let html = "", nowInserted = false;
    items.forEach((i) => {
      if (!nowInserted && i.start_min != null && i.start_min > t.now_min) {
        html += '<div class="tl-now">NOW ' + String(Math.floor(t.now_min / 60)).padStart(2, "0") + ":" + String(t.now_min % 60).padStart(2, "0") + "</div>";
        nowInserted = true;
      }
      const st = i.status;
      const badge = st === "completed" ? '<span class="badge ok">✓ done</span>'
        : st === "running" ? '<span class="badge info"><span class="spinner" style="width:9px;height:9px;border-width:1.5px"></span> running</span>'
        : st === "due" ? '<span class="badge warn">ready — run now</span>'
        : st === "overdue" ? '<span class="badge err">overdue</span>'
        : st === "waiting" ? '<span class="badge dim">waiting</span>'
        : '<span class="badge dim">upcoming</span>';
      html += '<div class="tl-item ' + st + '"><span class="tl-time">' + esc((i.window || "").split("-")[0] || "—") + "</span>" +
        '<span class="tl-rail"><span class="tl-dot"></span></span>' +
        '<span class="tl-main"><span class="tl-id">' + esc(i.id) + '</span><span class="tl-name">' + esc(i.pipe_name) + "</span></span>" + badge + "</div>";
    });
    if (!nowInserted) html += '<div class="tl-now">NOW — end of schedule</div>';
    list.innerHTML = html || '<div class="bm-empty">No run windows configured.</div>';
    const now = list.querySelector(".tl-now");
    if (now && !list.dataset.scrolled) { now.scrollIntoView({ block: "center" }); list.dataset.scrolled = "1"; }
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
          RMS.api("/ops/timeline").then(renderTimeline).catch(() => {});
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
