/* pages/scheduler.js — Scheduler / Automation Center.
   Tabs:  Schedules (create / edit / pause / run now) · History · Audit · Today (the read-only
   timeline of the Process Center's own run windows, unchanged).
   All next-run times come from the backend (/sched/preview, /sched/schedules) — the browser never
   calculates a schedule itself, so UI and engine cannot disagree. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false;
  const S = { tab: "schedules", filter: "all", q: "", schedules: [], summary: null, procs: [],
              hist: [], audit: [], histF: { date: "", process: "", schedule: "", status: "" },
              today: null, tview: "timeline", tpipes: new Set(["ctcl", "sa", "ms15", "cli"]), err: "", loaded: {} };
  const PIPES = [["ctcl", "CTCL vs NMASS"], ["sa", "Short Allocation"], ["ms15", "Margin Shortage"], ["cli", "Client Prudential"]];
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const TZS = ["Asia/Kolkata", "UTC", "Asia/Dubai", "Asia/Singapore", "Europe/London", "America/New_York"];

  /* ── tiny fetch helpers (RMS.api is GET-only) ─────────────────────────── */
  async function call(method, path, body) {
    const opt = { method, credentials: "same-origin", headers: {} };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    const r = await fetch(path, opt);
    let d = {};
    try { d = await r.json(); } catch (e) { /* empty body */ }
    if (!r.ok || d.ok === false) {
      const err = new Error(d.error || ("HTTP " + r.status));
      err.errors = d.errors || null; err.status = r.status;
      throw err;
    }
    return d;
  }
  const jget = (p) => call("GET", p);

  const BADGE = {
    enabled: '<span class="badge ok">🟢 Enabled</span>', running: '<span class="badge info">🟡 Running</span>',
    paused: '<span class="badge dim">⚪ Paused</span>', disabled: '<span class="badge dim">⚪ Disabled</span>',
    failed: '<span class="badge err">🔴 Failed</span>', missed: '<span class="badge warn">🟠 Missed</span>',
    completed: '<span class="badge dim">✓ Completed</span>',
  };
  const RBADGE = {
    SUCCESS: '<span class="badge ok">Success</span>', FAILED: '<span class="badge err">Failed</span>',
    RUNNING: '<span class="badge info">Running</span>', SKIPPED: '<span class="badge dim">Skipped</span>',
    MISSED: '<span class="badge warn">Missed</span>', CANCELLED: '<span class="badge dim">Cancelled</span>',
    QUEUED: '<span class="badge warn">Queued</span>', RETRY_WAIT: '<span class="badge warn">Retry wait</span>',
  };
  const TRIG = { SCHEDULED: "Scheduled", MANUAL_RUN_NOW: "Run Now", MISSED_RUN_RECOVERY: "Missed-run recovery" };

  function kpi(icon, label, v, foot, cls) {
    return '<div class="card kpi' + (cls ? " accent-" + cls : "") + '"><div class="k-label">' + RMS.icon(icon) + label +
      '</div><div class="k-value" style="font-size:22px">' + v + '</div><div class="k-foot">' + (foot || "") + "</div></div>";
  }

  /* ── page shell ───────────────────────────────────────────────────────── */
  function shell() {
    return '<div class="card" style="margin-bottom:12px"><div class="filters">' +
      '<div class="seg" id="scTabs"><button data-t="schedules" class="on">Schedules</button><button data-t="processes">Processes</button>' +
      '<button data-t="portal">Portal Downloads</button>' +
      '<button data-t="history">History</button><button data-t="audit">Audit</button>' +
      '<button data-t="today">Today</button></div>' +
      '<div class="spacer"></div><span class="card-sub" id="scMeta"></span>' +
      '<button class="btn sm ghost" id="scRefresh">' + RMS.icon("refresh") + "</button></div></div>" +
      '<div id="scBody"></div>';
  }

  function setTab(t) {
    S.tab = t;
    S.pw = "";                                  // a verified console password never outlives the tab it was typed on
    $("#scTabs").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.t === t));
    // Switch instantly: show what this tab last had (refreshed quietly below), or a placeholder on first visit.
    if (S.loaded[t]) render();
    else if ($("#scBody")) $("#scBody").innerHTML = '<div class="card"><div class="bm-empty">Loading…</div></div>';
    load();
  }

  /* ── data loading ─────────────────────────────────────────────────────── */
  async function load() {
    const tab = S.tab;
    try {
      S.err = "";
      if (S.tab === "schedules") {
        const [sum, list, procs] = await Promise.all([jget("/sched/summary"), jget("/sched/schedules"), jget("/sched/processes")]);
        S.summary = sum; S.schedules = list.items || []; S.procs = procs.processes || [];
      } else if (S.tab === "history") {
        if (!S.procs.length) S.procs = (await jget("/sched/processes")).processes || [];
        if (!S.schedules.length) S.schedules = (await jget("/sched/schedules")).items || [];
        const f = S.histF, qs = new URLSearchParams();
        Object.keys(f).forEach((k) => f[k] && qs.set(k, f[k]));
        S.hist = (await jget("/sched/executions?" + qs.toString())).items || [];
      } else if (S.tab === "portal") {
        if (S.pedit && $("#pdEditor")) return;      // never wipe a form that is being filled in
        const [pd, procs, conns] = await Promise.all([jget("/sched/portal"), jget("/sched/processes"), jget("/sched/connections")]);
        S.portal = pd; S.procs = procs.processes || []; S.conns = conns;
      } else if (S.tab === "audit") {
        S.audit = (await jget("/sched/audit")).items || [];
      } else if (S.tab === "processes") {
        const [procs, list, conns] = await Promise.all([jget("/sched/processes"), jget("/sched/schedules"), jget("/sched/connections")]);
        S.procs = procs.processes || []; S.schedules = list.items || []; S.conns = conns;
        S.pset = {};
        await Promise.all(S.procs.filter((p) => (p.params || []).some((q) => q.scope === "process")).map(async (p) => {
          try { S.pset[p.key] = (await jget("/sched/process-settings/" + encodeURIComponent(p.key))).values || {}; } catch (e) { S.pset[p.key] = {}; }
        }));
      } else {
        S.today = await RMS.api("/ops/timeline");
      }
    } catch (e) { S.err = e.message || String(e); }
    if (tab !== S.tab) return;                  // user already moved on to another tab — don't paint over it
    if (!S.err) S.loaded[tab] = true;
    render();
    if (tab === "processes") S.focusProc = "";   // the jump-to-process request has now been honoured
  }

  function render() {
    if (!$("#scBody")) return;
    $("#scMeta").textContent = "live · " + new Date().toTimeString().slice(0, 5);
    if (S.err && S.tab !== "today") {
      $("#scBody").innerHTML = '<div class="card"><div class="bm-empty" style="color:var(--err)">' + esc(S.err) + "</div></div>";
      return;
    }
    ({ schedules: renderSchedules, history: renderHistory, audit: renderAudit, processes: renderProcesses, portal: renderPortal, today: renderToday })[S.tab]();
  }

  /* ═══════════════════════ SCHEDULES TAB ═══════════════════════════════ */
  function healthStrip() {
    const h = (S.summary && S.summary.health) || {};
    let dot, txt;
    if (!h.enabled_in_env) { dot = "⚪"; txt = "OFF — set SCHEDULER_ENABLED=true in .env and restart to run schedules"; }
    else if (h.stale) { dot = "🔴"; txt = "No heartbeat — the scheduler loop is not running"; }
    else { dot = "🟢"; txt = "Running · heartbeat " + Math.round(h.heartbeat_age_s || 0) + "s ago"; }
    const bad = h.enabled_in_env && h.stale;
    return '<div class="card" style="margin-bottom:12px;padding:10px 14px;' + (bad ? "border-color:var(--err)" : "") + '">' +
      "<b>Scheduler: " + dot + "</b> <span class='card-sub'>" + esc(txt) + "</span>" +
      (h.holder ? " <span class='card-sub'>· leader " + esc(h.holder) + "</span>" : "") +
      (h.last_error ? "<div class='card-sub' style='color:var(--err)'>Last error: " + esc(h.last_error) + "</div>" : "") + "</div>";
  }

  function matches(s) {
    const f = S.filter;
    if (f === "enabled" && s.status !== "enabled") return false;
    if (f === "paused" && !(s.status === "paused" || s.status === "disabled")) return false;
    if (f === "running" && s.display_status !== "running") return false;
    if (f === "failed" && s.display_status !== "failed") return false;
    const q = S.q.trim().toLowerCase();
    return !q || (s.name + " " + s.process_name).toLowerCase().includes(q);
  }

  function renderSchedules() {
    const sm = S.summary || {};
    const nx = sm.next_run;
    const kp =
      kpi("check", "Active Schedules", sm.active ?? 0, "of " + (sm.total ?? 0), "ok") +
      kpi("clock", "Paused", sm.paused ?? 0, "") +
      kpi("activity", "Running Now", sm.running ?? 0, "") +
      kpi("alert", "Failed Today", sm.failed_today ?? 0, "", (sm.failed_today ? "err" : "")) +
      kpi("check", "Successful Today", sm.success_today ?? 0, "", "ok") +
      kpi("calendar", "Next Scheduled Run", nx ? esc(nx.text) : "—", nx ? esc(nx.schedule) : "nothing scheduled");
    // Click a column title to sort: 1st click ascending, 2nd descending, 3rd back to the normal order.
    const SORTS = { name: (s) => (s.name || "").toLowerCase(), process: (s) => (s.process_name || "").toLowerCase(),
      next: (s) => s.next_run_utc || "", last: (s) => s.last_run_utc || "", result: (s) => (s.last_result && s.last_result !== "—" ? s.last_result : ""),
      dur: (s) => (s.last_duration_sec == null ? "" : Number(s.last_duration_sec)), status: (s) => s.display_status || "" };
    let list = S.schedules.filter(matches);
    if (S.sort && SORTS[S.sort.k]) {
      const g = SORTS[S.sort.k], dir = S.sort.d === "desc" ? -1 : 1;
      list = list.slice().sort((a, b) => {
        const x = g(a), y = g(b);
        if (x === "" && y === "") return 0;
        if (x === "") return 1;                        // empty values always at the bottom
        if (y === "") return -1;
        return (x < y ? -1 : x > y ? 1 : 0) * dir;
      });
    }
    const th = (k, label) => "<th class='sc-sort' data-sk='" + k + "' style='cursor:pointer;user-select:none' title='Click to sort'>" + label +
      (S.sort && S.sort.k === k ? (S.sort.d === "desc" ? " ▼" : " ▲") : " <span style='opacity:.3'>⇅</span>") + "</th>";
    const rows = list.map((s) => {
      const paused = s.status === "paused", off = s.status === "disabled";
      return "<tr data-id='" + s.id + "'><td><b>" + esc(s.name) + "</b>" + (s.description ? "<div class='card-sub'>" + esc(s.description) + "</div>" : "") + "</td>" +
        "<td><a href='javascript:void(0)' class='sch-plink' data-pk='" + esc(s.process_key) + "' title='Open this process (logs, recipients, files)'>" + esc(s.process_name) + "</a>" +
        (s.process_missing ? " <span class='badge err'>missing</span>" : "") + "</td>" +
        "<td>" + esc(s.trigger_text) + "</td><td>" + esc(s.days_text) + "</td>" +
        "<td>" + esc(s.next_run_text) + "</td><td>" + esc(s.last_run_text) + "</td>" +
        "<td>" + (s.last_result && s.last_result !== "—" ? (RBADGE[s.last_result] || esc(s.last_result)) : "—") + "</td>" +
        "<td>" + RMS.fmtDur(s.last_duration_sec) + "</td><td>" + (BADGE[s.display_status] || esc(s.display_status)) + "</td>" +
        "<td style='white-space:nowrap'>" +
        "<button class='btn sm' data-a='run' title='Run now (the normal schedule is not changed)'>▶ Run Now</button> " +
        "<button class='btn sm ghost' data-a='edit'>Edit</button> " +
        (paused ? "<button class='btn sm ghost' data-a='resume'>Resume</button> "
          : off ? "<button class='btn sm ghost' data-a='enable'>Enable</button> "
            : s.status === "enabled" ? "<button class='btn sm ghost' data-a='pause'>Pause</button> " : "") +
        "<button class='btn sm ghost' data-a='more'>⋯</button></td></tr>";
    }).join("");
    $("#scBody").innerHTML = healthStrip() +
      '<div class="kpi-grid" style="margin-bottom:12px">' + kp + "</div>" +
      '<div class="card" style="margin-bottom:12px"><div class="filters">' +
      ["all", "enabled", "paused", "running", "failed"].map((f) =>
        '<span class="chip' + (S.filter === f ? " on" : "") + '" data-f="' + f + '">' + f[0].toUpperCase() + f.slice(1) + "</span>").join("") +
      '<input class="f-input" id="scSearch" placeholder="Search schedule or process…" value="' + esc(S.q) + '">' +
      '<div class="spacer"></div><button class="btn primary" id="scCreate">+ Create Schedule</button></div></div>' +
      '<div class="card" style="padding:0;overflow:hidden"><div style="overflow:auto"><table class="htable"><thead><tr>' +
      th("name", "Schedule Name") + th("process", "Process") + "<th>Trigger</th><th>Days</th>" + th("next", "Next Run") + th("last", "Last Run") +
      th("result", "Last Result") + th("dur", "Duration") + th("status", "Status") + "<th>Actions</th>" +
      "</tr></thead><tbody>" + (rows || "<tr><td colspan='10'><div class='bm-empty'>" +
        (S.schedules.length ? "No schedules match this filter." :
          (S.procs.length ? "No schedules yet — click “Create Schedule”." :
            "No schedulable processes are registered yet. Processes are added to <code>core/scheduler/processes/</code>; once one exists you can schedule it here.")) +
        "</div></td></tr>") + "</tbody></table></div></div>";
    $("#scBody").querySelectorAll(".chip").forEach((c) => { c.onclick = () => { S.filter = c.dataset.f; renderSchedules(); }; });
    $("#scBody").querySelectorAll("th.sc-sort").forEach((h) => {
      h.onclick = () => {
        const k = h.dataset.sk;
        S.sort = !S.sort || S.sort.k !== k ? { k, d: "asc" } : S.sort.d === "asc" ? { k, d: "desc" } : null;
        renderSchedules();
      };
    });
    const se = $("#scSearch");
    se.oninput = () => { S.q = se.value; const pos = se.selectionStart; renderSchedules(); const n = $("#scSearch"); n.focus(); n.setSelectionRange(pos, pos); };
    $("#scCreate").onclick = () => openEditor(null);
    $("#scBody").querySelectorAll(".sch-plink").forEach((a) => {
      a.onclick = () => { S.focusProc = a.dataset.pk; setTab("processes"); };
    });
    $("#scBody").querySelectorAll("tbody button[data-a]").forEach((b) => {
      b.onclick = () => rowAction(b.dataset.a, b.closest("tr").dataset.id);
    });
  }

  async function rowAction(a, id) {
    const s = S.schedules.find((x) => x.id === id);
    if (!s) return;
    try {
      if (a === "edit") return openEditor(s);
      if (a === "more") return openMore(s);
      if (a === "run") {
        if (!(await confirmBox("Run now — " + s.name, "This really runs the process now (for example it sends the WhatsApp / e-mail), even if it already ran today. The normal schedule is not changed.", "Run now"))) return;
        const pw = await unlock(needsPw(), "Run now — " + s.name);
        if (pw === null) return;
        await call("POST", "/sched/schedules/" + id + "/run-now", pw ? { _password: pw } : undefined);
        toast("ok", "Started", s.name);
        S.fastUntil = Date.now() + 180000;               // watch closely for the next 3 minutes (see the poll below)
      } else {
        const pw = await unlock(needsPw(), { pause: "Pause", resume: "Resume", enable: "Enable", disable: "Disable" }[a] + " — " + s.name);
        if (pw === null) return;
        await call("POST", "/sched/schedules/" + id + "/" + a, pw ? { _password: pw } : undefined);
        toast("ok", { pause: "Paused", resume: "Resumed", enable: "Enabled", disable: "Disabled" }[a] || "Done", s.name);
      }
      load();
    } catch (e) { toast("err", "Not done", e.message); load(); }
  }

  function openMore(s) {
    const close = RMS.modal(s.name, '<div style="display:grid;gap:8px">' +
      (s.status === "enabled" ? '<button class="btn" data-m="disable">Disable (keeps the configuration)</button>' : "") +
      '<button class="btn" data-m="history">View this schedule\'s history</button>' +
      '<button class="btn" data-m="delete" style="color:var(--err)">Delete schedule…</button></div>', { width: 420 });
    document.querySelectorAll("#modalRoot [data-m]").forEach((b) => {
      b.onclick = async () => {
        const m = b.dataset.m;
        if (m === "history") { close(); S.histF = { date: "", process: "", schedule: s.id, status: "" }; setTab("history"); return; }
        if (m === "delete" && !(await confirmBox("Delete — " + s.name, "Delete this schedule permanently? Its run history is kept; the configuration cannot be recovered.", "Delete", true))) return;
        close();
        const pw = await unlock(needsPw(), (m === "delete" ? "Delete" : "Disable") + " — " + s.name);
        if (pw === null) return;
        try {
          if (m === "delete") await call("DELETE", "/sched/schedules/" + s.id, pw ? { _password: pw } : undefined);
          else await call("POST", "/sched/schedules/" + s.id + "/disable", pw ? { _password: pw } : undefined);
          toast("ok", m === "delete" ? "Deleted" : "Disabled", s.name); load();
        } catch (e) { toast("err", "Not done", e.message); }
      };
    });
  }

  /* ═══════════════════════ CREATE / EDIT FORM ══════════════════════════ */
  function blank() {
    return { name: "", description: "", process_key: "", params: {}, tz: "Asia/Kolkata", start_date: "", end_date: "",
      skip_holidays: true, missed_policy: "latest", missed_grace_min: 30, overlap_policy: "skip",
      retry: { enabled: false, max: 3, delay_min: 5, backoff: false },
      rule: { type: "times", times: ["07:45"], days: [0, 1, 2, 3, 4] } };
  }

  let ED = null;           // { id, draft, timer }

  function field(label, html, hint) {
    return '<div style="margin-bottom:12px"><div class="card-sub" style="margin-bottom:4px;font-weight:700;min-height:24px;display:flex;align-items:center;gap:6px">' + label + "</div>" + html +
      (hint ? '<div class="card-sub" style="margin-top:3px">' + hint + "</div>" : "") + "</div>";
  }
  const optHtml = (arr, sel) => arr.map(([v, l]) => '<option value="' + esc(v) + '"' + (String(v) === String(sel) ? " selected" : "") + ">" + esc(l) + "</option>").join("");

  const needsPw = () => !!((S.summary && S.summary.needs_password) || (S.conns && S.conns.needs_password));

  /* create / edit: console password first (unless administrator), exactly like the Process Center */
  async function openEditor(s, presetKey) {
    const pw = await unlock(needsPw(), s ? "Edit schedule — " + s.name : "Create schedule");
    if (pw === null) return;
    openEditorForm(s, presetKey, pw);
  }

  function openEditorForm(s, presetKey, pw) {
    const d = s ? JSON.parse(JSON.stringify(s)) : blank();
    if (!s && presetKey) d.process_key = presetKey;
    d.retry = Object.assign({ enabled: false, max: 3, delay_min: 5, backoff: false }, d.retry || {});
    if (d.rule && d.rule.type === "interval" && !d.rule.days) d.rule.days = null;
    ED = { id: s ? s.id : null, draft: d, timer: null, pw: pw || "" };
    const body =
      '<div id="edForm" style="max-height:72vh;overflow:auto;padding-right:6px">' +
      '<div id="edErr" style="color:var(--err);font-size:12.5px;margin-bottom:8px;white-space:pre-wrap"></div>' +
      '<div class="card-sub" style="font-weight:800;margin:0 0 8px">BASIC DETAILS</div>' +
      field("Schedule name", '<input class="f-input" id="edName" style="width:100%" maxlength="120" value="' + esc(d.name) + '">') +
      field("Description (optional)", '<input class="f-input" id="edDesc" style="width:100%" maxlength="500" value="' + esc(d.description || "") + '">') +
      field("Process", '<input class="f-input" id="edProcQ" placeholder="Search processes…" style="width:100%;margin-bottom:6px">' +
        '<select class="f-select" id="edProc" style="width:100%"></select><div class="card-sub" id="edProcInfo" style="margin-top:4px"></div>') +
      '<div id="edParams"></div>' +
      '<div class="card-sub" style="font-weight:800;margin:14px 0 8px">WHEN TO RUN</div>' +
      field("Run", '<select class="f-select" id="edType">' + optHtml([["times", "At specific times (daily / selected weekdays)"], ["interval", "Repeat every X minutes / hours"],
        ["multi", "Combine several timings (each with its own days)"],
        ["monthly", "Monthly"], ["once", "One-time dates (run exactly once each)"]], d.rule.type) + "</select>") +
      '<div id="edWhen"></div>' +
      '<div class="card-sub" style="font-weight:800;margin:14px 0 8px">TIME SETTINGS</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px">' +
      field("Start date", '<input type="date" class="f-input" id="edSd" value="' + esc(d.start_date || "") + '">') +
      field("End date (optional)", '<input type="date" class="f-input" id="edEd" value="' + esc(d.end_date || "") + '">') +
      field("Timezone", '<input class="f-input" id="edTz" list="edTzList" value="' + esc(d.tz) + '"><datalist id="edTzList">' +
        TZS.map((z) => '<option value="' + z + '">').join("") + "</datalist>") + "</div>" +
      '<label style="font-size:12.5px"><input type="checkbox" id="edHol"' + (d.skip_holidays ? " checked" : "") + '> Skip market holidays (uses the shared holiday list; applies to every timing)</label>' +
      '<div class="card-sub" style="font-weight:800;margin:14px 0 8px">SAFETY</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">' +
      field("If a run was missed (server was off)", '<select class="f-select" id="edMiss" style="width:100%">' + optHtml([["latest", "Run the latest missed execution"],
        ["grace", "Run only if missed by less than N minutes"], ["skip", "Skip missed executions"]], d.missed_policy) + "</select>" +
        '<div id="edGraceWrap" style="margin-top:6px">N = <input type="number" min="1" max="1440" class="f-input" id="edGrace" style="width:80px" value="' + (d.missed_grace_min || 30) + '"> minutes</div>') +
      field("If it is already running", '<select class="f-select" id="edOver" style="width:100%"></select>') + "</div>" +
      '<div id="edRetryBox"><label style="font-size:12.5px"><input type="checkbox" id="edRetry"' + (d.retry.enabled ? " checked" : "") + '> Retry on failure</label> ' +
      'max <input type="number" min="1" max="10" class="f-input" id="edRetryMax" style="width:60px" value="' + d.retry.max + '"> · wait ' +
      '<input type="number" min="1" max="1440" class="f-input" id="edRetryDelay" style="width:70px" value="' + d.retry.delay_min + '"> min ' +
      '<label style="font-size:12.5px"><input type="checkbox" id="edBackoff"' + (d.retry.backoff ? " checked" : "") + '> exponential backoff</label>' +
      '<div class="card-sub" id="edRetryNote"></div></div>' +
      '<div class="card-sub" style="font-weight:800;margin:14px 0 8px">UPCOMING EXECUTIONS</div>' +
      '<div class="card" id="edPrev" style="padding:10px"><span class="card-sub">…</span></div></div>' +
      '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">' +
      '<button class="btn ghost" id="edCancel">Cancel</button><button class="btn primary" id="edSave">' + (s ? "Save changes" : "Create schedule") + "</button></div>";
    const close = RMS.modal(s ? "Edit schedule" : "Create schedule", body, { width: 820, icon: "calendar" });
    ED.close = close;
    fillProcs($("#edProcQ").value);
    $("#edProc").value = d.process_key || ($("#edProc").options[0] ? $("#edProc").options[0].value : "");
    onProcChange(true);
    renderWhen(d.rule);
    syncMissed();
    const form = $("#edForm");
    form.addEventListener("input", schedulePreview);
    form.addEventListener("change", schedulePreview);
    $("#edProcQ").oninput = () => { fillProcs($("#edProcQ").value); onProcChange(false); schedulePreview(); };
    $("#edProc").onchange = () => { onProcChange(false); schedulePreview(); };
    $("#edType").onchange = () => { renderWhen({ type: $("#edType").value }); schedulePreview(); };
    $("#edMiss").onchange = syncMissed;
    $("#edCancel").onclick = close;
    $("#edSave").onclick = save;
    schedulePreview();
  }

  function procDef(key) { return S.procs.find((p) => p.key === key) || null; }

  function fillProcs(q) {
    const sel = $("#edProc"), cur = sel.value, ql = (q || "").toLowerCase();
    const list = S.procs.filter((p) => !ql || (p.name + " " + p.key + " " + p.category).toLowerCase().includes(ql));
    sel.innerHTML = list.length ? list.map((p) => '<option value="' + esc(p.key) + '">' + esc(p.name) + " — " + esc(p.category) + "</option>").join("")
      : '<option value="">(no processes registered / found)</option>';
    if (cur && list.some((p) => p.key === cur)) sel.value = cur;
  }

  function onProcChange(initial) {
    const p = procDef($("#edProc").value);
    $("#edProcInfo").textContent = p ? (p.description || "") : "No processes are registered yet — add them in core/scheduler/processes/.";
    // parameters (dynamic, from the registry)
    const cur = initial ? (ED.draft.params || {}) : readParams();
    const sparams = p ? p.params.filter((q) => q.scope !== "process") : [];
    $("#edParams").innerHTML = (p && p.params.some((q) => q.scope === "process")
      ? '<div class="card-sub" style="margin:6px 0">Numbers / e-mail addresses are set on the <b>Processes</b> tab (📱 / ✉️ button) and shared by all schedules of this process.</div>' : "") +
      (sparams.length ? '<div class="card-sub" style="font-weight:800;margin:6px 0">PROCESS PARAMETERS</div>' +
      sparams.map((q) => {
        const v = cur[q.name] != null ? cur[q.name] : q.default;
        const id = "edp_" + q.name;
        let inp;
        if (q.type === "bool") inp = '<input type="checkbox" id="' + id + '" data-p="' + q.name + '"' + (v ? " checked" : "") + ">";
        else if (q.type === "choice") inp = '<select class="f-select" id="' + id + '" data-p="' + q.name + '">' + optHtml((q.choices || []).map((c) => [c, c]), v) + "</select>";
        else if (q.type === "text") inp = '<textarea class="f-input" id="' + id + '" data-p="' + q.name + '" rows="3" style="width:100%">' + esc(v == null ? "" : v) + "</textarea>";
        else inp = '<input class="f-input" id="' + id + '" data-p="' + q.name + '" type="' + (q.type === "int" ? "number" : "text") + '" value="' + esc(v == null ? "" : v) + '" style="width:100%">';
        return field(esc(q.label) + (q.required ? " *" : ""), inp, esc(q.help || ""));
      }).join("") : "");
    // overlap options & retry availability depend on the process
    const over = $("#edOver");
    const keep = initial ? ED.draft.overlap_policy : over.value;
    over.innerHTML = optHtml([["skip", "Skip this execution"], ["queue", "Queue one execution"]].concat(p && p.parallel_safe ? [["parallel", "Allow parallel execution"]] : []), keep || "skip");
    const retryOk = !!(p && p.retry_safe);
    $("#edRetry").disabled = !retryOk;
    if (!retryOk) $("#edRetry").checked = false;
    $("#edRetryNote").textContent = retryOk ? "" : "Retry is only offered for processes marked safe to run twice.";
  }

  function readParams() {
    const out = {};
    document.querySelectorAll("#edParams [data-p]").forEach((el) => {
      const n = el.dataset.p;
      if (el.type === "checkbox") out[n] = el.checked;
      else if (el.value !== "") out[n] = el.type === "number" ? Number(el.value) : el.value;
    });
    return out;
  }

  function syncMissed() { $("#edGraceWrap").style.display = $("#edMiss").value === "grace" ? "block" : "none"; }

  function timeRow(v) {
    return '<div class="ed-row" style="display:flex;gap:6px;margin-bottom:6px"><input type="time" class="f-input ed-time" value="' + esc(v || "") + '">' +
      '<button type="button" class="btn sm ghost ed-del">✕</button></div>';
  }
  function atRow(v) {
    return '<div class="ed-row" style="display:flex;gap:6px;margin-bottom:6px"><input type="datetime-local" class="f-input ed-at" value="' + esc(v || "") + '">' +
      '<button type="button" class="btn sm ghost ed-del">✕</button></div>';
  }
  function daysBlock(days) {
    const all = !days || !days.length;
    return field("On", '<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">' +
      DAYS.map((n, i) => '<label style="font-size:12.5px"><input type="checkbox" class="ed-day" data-d="' + i + '"' + ((all ? true : days.includes(i)) ? " checked" : "") + "> " + n + "</label>").join("") +
      '</div><div style="margin-top:6px"><button type="button" class="btn sm ghost" data-sc="wk">Weekdays</button> <button type="button" class="btn sm ghost" data-sc="we">Weekends</button> ' +
      '<button type="button" class="btn sm ghost" data-sc="all">Every day</button></div>');
  }

  /* one timing block of a combined schedule: its own kind (repeat / specific times) and its own days */
  function partHtml(p) {
    const t = p.type === "interval" ? "interval" : "times";
    let h = '<div class="ed-part card" style="padding:10px;margin-bottom:10px"><div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">' +
      '<select class="f-select ed-ptype">' + optHtml([["interval", "Repeat every X minutes / hours"], ["times", "At specific times"]], t) + "</select>" +
      '<button type="button" class="btn sm ghost ed-pdel" style="margin-left:auto">Remove</button></div>';
    if (t === "interval") {
      h += field("Repeat every", '<div style="display:flex;gap:8px"><input type="number" min="1" class="f-input ed-every" style="width:90px" value="' + (p.every || 1) + '">' +
        '<select class="f-select ed-unit">' + optHtml([["minutes", "Minutes"], ["hours", "Hours"]], p.unit || "hours") + "</select></div>") +
        field("Between", '<div style="display:flex;gap:8px;align-items:center"><input type="time" class="f-input ed-start" value="' + (p.start || "09:00") +
          '"> and <input type="time" class="f-input ed-end" value="' + (p.end || "23:00") + '"></div>');
    } else {
      h += field("At", '<div class="ed-times">' + ((p.times && p.times.length) ? p.times : ["07:45"]).map(timeRow).join("") +
        '</div><button type="button" class="btn sm ed-padd">+ Add another time</button>');
    }
    return h + daysBlock(p.days === undefined ? [0, 1, 2, 3, 4] : p.days) + "</div>";
  }

  function renderMulti(r) {
    const parts = (r.parts && r.parts.length) ? r.parts
      : [{ type: "interval", every: 1, unit: "hours", start: "09:00", end: "23:00", days: [0, 1, 2, 3, 4] },
         { type: "times", times: ["00:00"], days: [1, 2, 3, 4, 5] }];
    const box = $("#edWhen");
    box.innerHTML = '<div id="edParts">' + parts.map(partHtml).join("") + "</div>" +
      '<button type="button" class="btn sm" id="edAddPart">+ Add another timing</button>' +
      '<div class="card-sub" style="margin-top:6px">All timings run the same process in ONE schedule. A time that appears in two timings runs once. “Skip market holidays” below applies to every timing.</div>';
    const redraw = () => { const cur = readMulti().parts; $("#edParts").innerHTML = cur.map(partHtml).join(""); };
    $("#edAddPart").onclick = () => {
      if (box.querySelectorAll(".ed-part").length >= 6) return;
      $("#edParts").insertAdjacentHTML("beforeend", partHtml({ type: "times", times: [""], days: [0, 1, 2, 3, 4] })); schedulePreview();
    };
    box.onchange = (e) => { if (e.target.classList.contains("ed-ptype")) { const p = e.target.closest(".ed-part"); const all = Array.from(box.querySelectorAll(".ed-part")); const cur = readMulti().parts; cur[all.indexOf(p)] = { type: e.target.value, days: cur[all.indexOf(p)].days }; $("#edParts").innerHTML = cur.map(partHtml).join(""); schedulePreview(); } };
    box.onclick = (e) => {
      const part = e.target.closest(".ed-part");
      if (e.target.closest(".ed-pdel")) { if (box.querySelectorAll(".ed-part").length > 1) { part.remove(); schedulePreview(); } return; }
      if (e.target.closest(".ed-padd")) { part.querySelector(".ed-times").insertAdjacentHTML("beforeend", timeRow("")); schedulePreview(); return; }
      const del = e.target.closest(".ed-del");
      if (del) { del.parentNode.remove(); schedulePreview(); return; }
      const sc = e.target.closest("[data-sc]");
      if (sc && part) {
        const set = { wk: [0, 1, 2, 3, 4], we: [5, 6], all: [0, 1, 2, 3, 4, 5, 6] }[sc.dataset.sc];
        part.querySelectorAll(".ed-day").forEach((c) => { c.checked = set.includes(Number(c.dataset.d)); });
        schedulePreview();
      }
    };
  }

  function readMulti() {
    const parts = Array.from(document.querySelectorAll("#edWhen .ed-part")).map((p) => {
      const days = Array.from(p.querySelectorAll(".ed-day")).filter((c) => c.checked).map((c) => Number(c.dataset.d));
      if (p.querySelector(".ed-ptype").value === "interval")
        return { type: "interval", every: parseInt(p.querySelector(".ed-every").value, 10), unit: p.querySelector(".ed-unit").value,
          start: p.querySelector(".ed-start").value, end: p.querySelector(".ed-end").value, days };
      return { type: "times", times: Array.from(p.querySelectorAll(".ed-time")).map((i) => i.value).filter(Boolean), days };
    });
    return { type: "multi", parts };
  }

  function renderWhen(r) {
    const t = r.type || "times", box = $("#edWhen");
    if (t === "multi") { renderMulti(r); return; }
    box.onchange = null;
    let h = "";
    if (t === "times") {
      const times = (r.times && r.times.length) ? r.times : ["07:45"];
      h = field("At", '<div id="edTimes">' + times.map(timeRow).join("") + '</div><button type="button" class="btn sm" id="edAddTime">+ Add another time</button>') +
        daysBlock(r.days === undefined ? [0, 1, 2, 3, 4] : r.days);
    } else if (t === "interval") {
      h = field("Repeat every", '<div style="display:flex;gap:8px"><input type="number" min="1" class="f-input" id="edEvery" style="width:90px" value="' + (r.every || 15) + '">' +
        '<select class="f-select" id="edUnit">' + optHtml([["minutes", "Minutes"], ["hours", "Hours"]], r.unit || "minutes") + "</select></div>") +
        field("Between", '<div style="display:flex;gap:8px;align-items:center"><input type="time" class="f-input" id="edStart" value="' + (r.start || "09:00") +
          '"> and <input type="time" class="f-input" id="edEnd" value="' + (r.end || "17:00") + '"></div>') +
        daysBlock(r.days === undefined ? [0, 1, 2, 3, 4] : r.days);
    } else if (t === "monthly") {
      const dayOpts = [["last", "Last day of the month"]].concat(Array.from({ length: 31 }, (_, i) => [i + 1, "Day " + (i + 1)]));
      h = field("On", '<select class="f-select" id="edMDay">' + optHtml(dayOpts, r.day == null ? 1 : r.day) + "</select>",
        "Day 29–31 runs only in months that have that day — use “Last day” for month-end.") +
        field("At", '<div id="edTimes">' + ((r.times && r.times.length) ? r.times : ["08:30"]).map(timeRow).join("") + '</div><button type="button" class="btn sm" id="edAddTime">+ Add another time</button>');
    } else {
      h = field("Run exactly once at each of", '<div id="edAts">' + ((r.at && r.at.length) ? r.at : [""]).map(atRow).join("") + '</div><button type="button" class="btn sm" id="edAddAt">+ Add date &amp; time</button>',
        "After the last date has run, the schedule is marked Completed.");
    }
    box.innerHTML = h;
    const addT = $("#edAddTime"); if (addT) addT.onclick = () => { $("#edTimes").insertAdjacentHTML("beforeend", timeRow("")); schedulePreview(); };
    const addA = $("#edAddAt"); if (addA) addA.onclick = () => { $("#edAts").insertAdjacentHTML("beforeend", atRow("")); schedulePreview(); };
    box.onclick = (e) => {
      const del = e.target.closest(".ed-del");
      if (del) { del.parentNode.remove(); schedulePreview(); return; }
      const sc = e.target.closest("[data-sc]");
      if (sc) {
        const set = { wk: [0, 1, 2, 3, 4], we: [5, 6], all: [0, 1, 2, 3, 4, 5, 6] }[sc.dataset.sc];
        box.querySelectorAll(".ed-day").forEach((c) => { c.checked = set.includes(Number(c.dataset.d)); });
        schedulePreview();
      }
    };
  }

  function readRule() {
    const t = $("#edType").value;
    if (t === "multi") return readMulti();
    const days = () => {
      const d = []; document.querySelectorAll("#edWhen .ed-day").forEach((c) => { if (c.checked) d.push(Number(c.dataset.d)); });
      return d;
    };
    const times = () => Array.from(document.querySelectorAll("#edWhen .ed-time")).map((i) => i.value).filter(Boolean);
    if (t === "times") return { type: "times", times: times(), days: days() };
    if (t === "interval") return { type: "interval", every: parseInt($("#edEvery").value, 10), unit: $("#edUnit").value,
      start: $("#edStart").value, end: $("#edEnd").value, days: days() };
    if (t === "monthly") { const v = $("#edMDay").value; return { type: "monthly", day: v === "last" ? "last" : parseInt(v, 10), times: times() }; }
    return { type: "once", at: Array.from(document.querySelectorAll("#edWhen .ed-at")).map((i) => i.value).filter(Boolean) };
  }

  function readForm() {
    return { name: $("#edName").value.trim(), description: $("#edDesc").value.trim(), process_key: $("#edProc").value,
      params: readParams(), rule: readRule(), tz: $("#edTz").value.trim() || "Asia/Kolkata",
      start_date: $("#edSd").value || null, end_date: $("#edEd").value || null, skip_holidays: $("#edHol").checked,
      missed_policy: $("#edMiss").value, missed_grace_min: parseInt($("#edGrace").value, 10) || 30,
      overlap_policy: $("#edOver").value || "skip",
      retry: { enabled: $("#edRetry").checked, max: parseInt($("#edRetryMax").value, 10) || 3,
        delay_min: parseFloat($("#edRetryDelay").value) || 5, backoff: $("#edBackoff").checked } };
  }

  function schedulePreview() {
    if (!ED) return;
    clearTimeout(ED.timer);
    ED.timer = setTimeout(async () => {
      const box = $("#edPrev"); if (!box) return;
      try {
        const r = await call("POST", "/sched/preview", readForm());
        const tz = r.tz ? ' <span class="card-sub">(' + esc(r.tz) + ")</span>" : "";
        box.innerHTML = (r.upcoming && r.upcoming.length ? r.upcoming.map((u) => "<div>" + esc(u.text) + "</div>").join("") + tz : "") +
          (r.errors && r.errors.length ? '<div style="color:var(--warn);font-size:12px;margin-top:4px">' + r.errors.map(esc).join("<br>") + "</div>" : "");
        if (!box.innerHTML) box.innerHTML = '<span class="card-sub">—</span>';
      } catch (e) { box.innerHTML = '<span style="color:var(--err)">' + esc(e.message) + "</span>"; }
    }, 300);
  }

  async function save() {
    const btn = $("#edSave"); btn.disabled = true;
    try {
      const body = readForm();
      if (ED.pw) body._password = ED.pw;
      if (ED.id) await call("PUT", "/sched/schedules/" + ED.id, body); else await call("POST", "/sched/schedules", body);
      ED.close(); ED = null;
      toast("ok", "Saved", body.name);
      load();
    } catch (e) {
      $("#edErr").textContent = e.errors ? e.errors.join("\n") : e.message;
      $("#edForm").scrollTop = 0;
      btn.disabled = false;
    }
  }

  /* ═══════════════════════ HISTORY TAB ═════════════════════════════════ */
  function renderHistory() {
    const f = S.histF;
    const rows = S.hist.map((r) => "<tr data-id='" + r.id + "'><td>" + esc(r.started_text !== "—" ? r.started_text : r.scheduled_text) + "</td>" +
      "<td>" + esc(r.schedule_name || "") + "</td><td>" + esc(r.process_key || "") + "</td>" +
      "<td>" + esc(TRIG[r.trigger_type] || r.trigger_type) + "</td><td>" + esc(r.scheduled_text) + "</td>" +
      "<td>" + RMS.fmtDur(r.duration_sec) + "</td><td>" + (RBADGE[r.status] || esc(r.status)) + "</td><td>" + (r.attempt || 1) + "</td>" +
      "<td class='card-sub'>" + esc((r.error_msg || "").slice(0, 90)) + "</td></tr>").join("");
    $("#scBody").innerHTML = '<div class="card" style="margin-bottom:12px"><div class="filters">' +
      '<input type="date" class="f-input" id="hfDate" value="' + esc(f.date) + '">' +
      '<select class="f-select" id="hfProc"><option value="">All processes</option>' + optHtml(S.procs.map((p) => [p.key, p.name]), f.process) + "</select>" +
      '<select class="f-select" id="hfSched"><option value="">All schedules</option>' + optHtml(S.schedules.map((s) => [s.id, s.name]), f.schedule) + "</select>" +
      '<select class="f-select" id="hfStatus"><option value="">Any status</option>' +
      optHtml(["SUCCESS", "FAILED", "RUNNING", "SKIPPED", "MISSED", "CANCELLED", "QUEUED", "RETRY_WAIT"].map((x) => [x, x]), f.status) + "</select>" +
      '<div class="spacer"></div><span class="card-sub">' + S.hist.length + " shown</span></div></div>" +
      '<div class="card" style="padding:0;overflow:hidden"><div style="max-height:620px;overflow:auto"><table class="htable"><thead><tr>' +
      "<th>Started</th><th>Schedule</th><th>Process</th><th>Trigger</th><th>Scheduled for</th><th>Duration</th><th>Result</th><th>Try</th><th>Note</th></tr></thead><tbody>" +
      (rows || "<tr><td colspan='9'><div class='bm-empty'>No executions for this filter.</div></td></tr>") + "</tbody></table></div></div>";
    const bind = (id, key) => { $(id).onchange = () => { S.histF[key] = $(id).value; load(); }; };
    bind("#hfDate", "date"); bind("#hfProc", "process"); bind("#hfSched", "schedule"); bind("#hfStatus", "status");
    $("#scBody").querySelectorAll("tbody tr[data-id]").forEach((tr) => { tr.onclick = () => openExec(tr.dataset.id); });
  }

  async function openExec(id) {
    try {
      const r = await jget("/sched/executions/" + id);
      const row = (k, v) => '<div style="display:flex;gap:10px;padding:3px 0"><div class="card-sub" style="width:130px">' + k + "</div><div>" + v + "</div></div>";
      RMS.modal("Execution details", row("Execution ID", "<code>" + esc(r.id) + "</code>") + row("Schedule", esc(r.schedule_name || "")) +
        row("Process", esc(r.process_key || "")) + row("Trigger", esc(TRIG[r.trigger_type] || r.trigger_type)) +
        row("Result", RBADGE[r.status] || esc(r.status)) + row("Scheduled for", esc(r.scheduled_text)) + row("Started", esc(r.started_text)) +
        row("Ended", esc(r.ended_text)) + row("Duration", RMS.fmtDur(r.duration_sec)) + row("Attempt", r.attempt || 1) +
        row("Worker", esc(r.worker || "—")) + (r.triggered_by ? row("Triggered by", esc(r.triggered_by)) : "") +
        (r.error_type ? row("Error", "<span style='color:var(--err)'>" + esc(r.error_type) + ": " + esc(r.error_msg || "") + "</span>") : (r.error_msg ? row("Note", esc(r.error_msg)) : "")) +
        '<div class="card-sub" style="margin:10px 0 4px;font-weight:800">LOG</div><pre class="logview" style="max-height:340px;overflow:auto;white-space:pre-wrap;font-size:11.5px">' +
        (r.log_text ? RMS.colorizeLog(r.log_text) : "(no log text)") + "</pre>", { width: 760 });
    } catch (e) { toast("err", "Could not open", e.message); }
  }

  /* ═══════════════════════ AUDIT TAB ═══════════════════════════════════ */
  function diff(b, a) {
    try {
      b = b ? JSON.parse(b) : {}; a = a ? JSON.parse(a) : {};
      const keys = Array.from(new Set(Object.keys(b).concat(Object.keys(a))));
      const out = keys.filter((k) => JSON.stringify(b[k]) !== JSON.stringify(a[k]))
        .map((k) => k + ": " + JSON.stringify(b[k] === undefined ? "—" : b[k]) + " → " + JSON.stringify(a[k] === undefined ? "—" : a[k]));
      return out.join("; ").slice(0, 260);
    } catch (e) { return ""; }
  }

  function renderAudit() {
    const rows = S.audit.map((r) => "<tr><td>" + esc(r.ts_text) + "</td><td>" + esc(r.user_name || "") + "</td><td><b>" + esc(r.action) + "</b></td>" +
      "<td>" + esc(r.schedule_name || "") + "</td><td class='card-sub'>" + esc(diff(r.before_json, r.after_json)) + "</td></tr>").join("");
    $("#scBody").innerHTML = '<div class="card" style="padding:0;overflow:hidden"><div style="max-height:660px;overflow:auto"><table class="htable"><thead><tr>' +
      "<th>When</th><th>User</th><th>Action</th><th>Schedule</th><th>Changes</th></tr></thead><tbody>" +
      (rows || "<tr><td colspan='5'><div class='bm-empty'>No scheduler changes recorded yet.</div></td></tr>") + "</tbody></table></div></div>" +
      '<div class="card-sub" style="margin-top:8px">Secrets are never written to this trail.</div>';
  }

  /* ═══════════════════════ PROCESSES TAB ═══════════════════════════════ */
  function connReady(group) {
    const g = ((S.conns && S.conns.groups) || []).find((x) => x.group === group);
    return !!g && g.fields.every((f) => f.optional || (f.secret ? f.set : !!f.value));
  }
  const CONN_NAME = { whatsapp: "WhatsApp", smtp: "E-mail (SMTP)", pop3: "Mailbox (POP3)",
    smtpb: "E-mail 2 (SMTP)", pop3b: "Mailbox 2 (POP3)", nse: "NSE Extranet", kambala_ftp: "Kambala FTP", alloc_pop3: "Mailbox (POP3)", alloc_smtp: "E-mail (SMTP)", mcx_sftp: "MCX portal",
    mcx_eclear: "MCX eclear", atom: "ATOM", backoffice: "Back-office API", client_margin: "Excel passwords" };
  const LOGO = { mcx_web_morning_email: "MX", partic_id_email: "PI", sn327_whatsapp: "327", sn359_whatsapp: "359",
    co_bo_blocking_file: "CB", otm_3_9_blocking_files: "3-9", contract_delta: "CD", persistent_noise_creators: "PN",
    cm_deloi_file: "DL", slbm_reports: "SL", fo_reports: "FO", mcx_restricted_basket: "MR", circuit_validation: "CV",
    atom_vs_bank_alert: "AB", mcx_hourly_surveillance: "MH", active_client_report: "AC", nmass_deposit_image: "NM",
    ctcl_deposit_image: "CT", zone_wise_top20_margin: "Z20", peak_margin_report: "PM" };

  function renderProcesses() {
    if (!S.procs.length) {
      $("#scBody").innerHTML = '<div class="card"><div class="bm-empty">No schedulable processes are registered yet.</div></div>';
      return;
    }
    // The card's buttons. E-mail and WhatsApp numbers are TWO buttons; "Add schedule" fills whatever is left of the
    // 3-column row so the card never shows a lonely button.
    const toolsHtml = (p, procParams, isPhone, isMail, missingReq) => {
      const hasUp = p.upload_exts && p.upload_exts.length;
      const lead = p.key === "rms_limits_mand_kambala" ? '<button class="sch-tool blue full" data-add="' + esc(p.key) + '">⏰ Add schedule</button>' : "";
      const items = [];
      const needMail = procParams.some((q) => q.fmt === "emails" && q.required && !String(((S.pset && S.pset[p.key]) || {})[q.name] || "").trim());
      const needPhone = procParams.some((q) => q.fmt === "phones" && q.required && !String(((S.pset && S.pset[p.key]) || {})[q.name] || "").trim());
      if (isMail) items.push('<button class="sch-tool' + (needMail ? " warn" : "") + '" data-recip="' + esc(p.key) + '" data-kind="mail">✉️ Email To / CC</button>');
      if (isPhone) items.push('<button class="sch-tool' + (needPhone ? " warn" : "") + '" data-recip="' + esc(p.key) + '" data-kind="phone">📱 WhatsApp numbers</button>');
      if ((p.uses || []).length) items.push('<button class="sch-tool' + ((p.uses || []).every(connReady) ? "" : " warn") + '" data-cred="' + esc(p.key) + '">🔑 Credentials</button>');
      if (hasUp) items.push('<button class="sch-tool green" data-upload="' + esc(p.key) + '">⬆️ Upload files</button>');
      if (p.key === "rms_limits_mand_kambala") items.push('<button class="sch-tool green" data-addcodes="1">➕ Add client codes</button>');
      items.push('<button class="sch-tool" data-logs="' + esc(p.key) + '">📜 Logs</button>');
      let add = "";
      if (p.key !== "rms_limits_mand_kambala") {
        const left = items.length % 3;
        add = '<button class="sch-tool blue ' + (left === 0 ? "full" : left === 1 ? "wide" : "") + '" data-add="' + esc(p.key) + '">⏰ Add schedule</button>';
      }
      return lead + items.join("") + add;
    };
    const card = (p) => {
      const mine = S.schedules.filter((s) => s.process_key === p.key);
      const live = mine.some((s) => s.status === "enabled");
      const state = !mine.length ? '<span class="badge dim">Not scheduled</span>' : live ? '<span class="badge ok">Scheduled</span>' : '<span class="badge dim">Paused</span>';
      const procParams = (p.params || []).filter((q) => q.scope === "process");
      const isPhone = procParams.some((q) => q.fmt === "phones");
      const isMail = procParams.some((q) => q.fmt === "emails");
      const vals0 = (S.pset && S.pset[p.key]) || {};
      const missingReq = procParams.some((q) => q.required && !String(vals0[q.name] || "").trim());
      const rec = procParams.filter((q) => q.fmt).map((q) => {
        const vals = String(vals0[q.name] || "").split(/[,;\n]+/).map((x) => x.trim()).filter(Boolean);
        return '<div class="sch-rrow"><span class="k">' + esc(q.label) + '</span><span class="v">' + (vals.length ? vals.map((v) => '<i>' + esc(v) + "</i>").join("") :
          '<em class="' + (q.required ? "bad" : "") + '">' + (q.required ? "not set" : "—") + "</em>") + "</span></div>";
      }).join("");
      const sched = mine.slice(0, 2).map((s) =>
        '<div class="sch-line"><span class="t">' + esc(s.trigger_text) + " · " + esc(s.days_text) + '</span><span class="n">Next ' + esc(s.next_run_text) +
        '</span><button class="sch-link" data-edit="' + s.id + '">Edit</button></div>').join("") +
        (mine.length > 2 ? '<div class="sch-none">+ ' + (mine.length - 2) + " more on the Schedules tab</div>" : "");
      const chips = (p.uses || []).map((g) => '<span class="badge ' + (connReady(g) ? "ok" : "warn") + '" data-cred="' + esc(p.key) + '" style="cursor:pointer" title="Open credentials">' +
        esc(CONN_NAME[g] || g) + (connReady(g) ? " ✓" : " — not set") + "</span>").join(" ");
      const wa = (p.uses || []).includes("whatsapp");
      const logo = LOGO[p.key] || p.name.split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
      return '<div class="sch-box" data-pk="' + esc(p.key) + '"><div class="sch-head"><div class="sch-logo ' + (wa ? "wa" : "mail") + '">' + esc(logo) + "</div>" +
        '<div class="sch-tw"><div class="sch-title">' + esc(p.name) + '</div><div class="sch-sub">' + esc(p.description || "") + "</div></div>" + state + "</div>" +
        '<div class="sch-tools">' + toolsHtml(p, procParams, isPhone, isMail, missingReq) + "</div>" +
        '<div class="sch-foot">' + (sched || '<div class="sch-none">No schedule yet</div>') + "</div></div>";
    };
    const hadFocus = document.activeElement && document.activeElement.id === "pcSearch";
    $("#scBody").innerHTML = '<div class="card-sub" style="margin-bottom:10px">Every process the scheduler can run, with who it sends to, its connections and its folder. ' +
      "To run one right now use <b>Run Now</b> on the Schedules tab.</div>" +
      '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px"><input class="f-input" id="pcSearch" type="search" placeholder="🔍 Search processes (name or description)…" value="' + esc(S.procQ || "") + '" style="flex:1;max-width:460px;height:40px;box-sizing:border-box"><span class="card-sub" id="pcCount"></span></div>' +
      '<div class="sch-grid">' + S.procs.map(card).join("") + "</div>" +
      '<div class="sch-none" id="pcNone" style="display:none;padding:24px;text-align:center">No process matches your search.</div>';
    const b = $("#scBody");
    const applyProcFilter = () => {
      const q = String(S.procQ || "").trim().toLowerCase();
      let shown = 0;
      b.querySelectorAll(".sch-box").forEach((box) => {
        const hit = !q || box.textContent.toLowerCase().includes(q);
        box.style.display = hit ? "" : "none";
        if (hit) shown++;
      });
      $("#pcCount").textContent = q ? shown + " of " + S.procs.length : S.procs.length + " processes";
      $("#pcNone").style.display = shown ? "none" : "block";
    };
    $("#pcSearch").oninput = (ev) => { S.procQ = ev.target.value; applyProcFilter(); };
    applyProcFilter();
    if (hadFocus) { const si = $("#pcSearch"); si.focus(); si.setSelectionRange(si.value.length, si.value.length); }
    b.querySelectorAll("[data-edit]").forEach((x) => { x.onclick = () => openEditor(S.schedules.find((s) => s.id === x.dataset.edit)); });
    b.querySelectorAll("[data-add]").forEach((x) => { x.onclick = () => openEditor(null, x.dataset.add); });
    b.querySelectorAll("[data-upload]").forEach((x) => { x.onclick = () => openUpload(x.dataset.upload); });
    b.querySelectorAll("[data-recip]").forEach((x) => { x.onclick = () => openRecipients(x.dataset.recip, x.dataset.kind); });
    b.querySelectorAll("[data-cred]").forEach((x) => { x.onclick = () => openCredentials(x.dataset.cred); });
    b.querySelectorAll("[data-logs]").forEach((x) => { x.onclick = () => openLogs(x.dataset.logs); });
    b.querySelectorAll("[data-addcodes]").forEach((x) => { x.onclick = () => openAddCodes(); });
    if (S.focusProc) {                              // arrived by clicking a process name on the Schedules tab
      const box = b.querySelector('.sch-box[data-pk="' + S.focusProc + '"]');
      // not cleared here: the quiet refresh that follows re-draws the page, so the jump is repeated once (see load())
      if (box) {
        box.scrollIntoView({ block: "center" });
        box.classList.add("sch-focus");
        setTimeout(() => box.classList.remove("sch-focus"), 2500);
      }
    }
  }

  /* ── Email To/CC · WhatsApp numbers (process-level, shared by all its schedules) ── */
  async function openRecipients(key, kind) {
    try {
      const d = await jget("/sched/process-settings/" + encodeURIComponent(key));
      const p = S.procs.find((x) => x.key === key) || { name: key };
      if (kind === "mail") d.params = d.params.filter((q) => q.fmt === "emails");        // the ✉️ button shows only e-mail boxes
      else if (kind === "phone") d.params = d.params.filter((q) => q.fmt === "phones");  // the 📱 button shows only WhatsApp boxes
      const phone = d.params.some((q) => q.fmt === "phones");
      const mail = d.params.some((q) => q.fmt === "emails");
      const both = phone && mail;
      const ttl = both ? "WhatsApp numbers / Email To & CC — " : phone ? "WhatsApp numbers — " : "Email To / CC — ";
      const pwd = await unlock(d.needs_password, ttl + p.name);
      if (pwd === null) return;
      const helpPhone = "<b>How to add WhatsApp numbers:</b> type ONE number per line (press Enter for the next number), or put a comma between numbers. " +
        "Type only the 10-digit mobile number - +91 is added by itself. To remember whose number it is, write a name after # . " +
        "Example:<br><b>9812345678 #Ravi</b><br><b>9876543210 #Amit</b>";
      const helpMail = "<b>How to add e-mail addresses:</b> type ONE address per line (press Enter for the next one), or put a comma between addresses. " +
        "To remember whose address it is, write a name after # . Example:<br><b>ravi@mastertrust.co.in #Ravi</b><br><b>amit@mastertrust.co.in #Amit</b>";
      const body = '<div id="rcErr" style="color:var(--err);font-size:12.5px;margin-bottom:8px;white-space:pre-wrap"></div>' +
        '<div class="card-sub" style="margin-bottom:10px">' + (both ? helpPhone + "<br><br>" + helpMail : phone ? helpPhone : helpMail) +
        "<br><br>These lists are used for every schedule of <b>" + esc(p.name) + "</b>.</div>" +
        d.params.map((q) => field(esc(q.label) + (q.required ? " *" : ""),
          '<textarea class="f-input" data-rc="' + q.name + '" rows="4" style="width:100%" placeholder="' + (q.fmt === "phones" ? "9812345678 #Name" : "name@company.com #Name") + '">' + esc(d.values[q.name] || "").replace(/,/g, ",\n") + "</textarea>", esc(q.help || ""))).join("") +
        '<div style="display:flex;justify-content:flex-end;gap:8px"><button class="btn ghost" id="rcCancel">Cancel</button><button class="btn primary" id="rcSave">Save</button></div>';
      const close = RMS.modal(ttl + p.name, body, { width: 620, icon: phone ? "bell" : "mail" });
      const cancel = $("#rcCancel"); if (cancel) cancel.onclick = close;
      const save = $("#rcSave");
      if (save) save.onclick = async () => {
        const v = {};
        document.querySelectorAll("[data-rc]").forEach((t) => { v[t.dataset.rc] = t.value.trim(); });
        if (pwd) v._password = pwd;
        save.disabled = true;
        try { await call("PUT", "/sched/process-settings/" + encodeURIComponent(key), v); close(); toast("ok", "Saved", p.name); load(); }
        catch (e) { $("#rcErr").textContent = e.message; save.disabled = false; }
      };
    } catch (e) { toast("err", "Could not open", e.message); }
  }

  /* ── Credentials: the connection groups this process uses ── */
  // the Kambala ON/OFF switches belong to ONE process each — a process's Credentials shows only its own
  const UPLOAD_SWITCH_OWNER = { upload_enabled: "rms_limits_mand_kambala", upload_above4cr: "above_4cr_holding_blocking_file" };
  function connGroupHtml(g, canEdit, pkey) {
    g = { ...g, fields: g.fields.filter((f) => !UPLOAD_SWITCH_OWNER[f.name] || UPLOAD_SWITCH_OWNER[f.name] === pkey) };
    const ALERT = /^alert_/;                                   // the WhatsApp failure-alert fields get their own box
    const one = (f) => {
      const lab = esc(f.label) + (f.secret ? (f.set ? ' <span class="badge ok">set ' + esc(f.hint) + "</span>" : ' <span class="badge ' + (f.optional ? "dim\">optional" : "warn\">not set") + "</span>") : "");
      const dis = canEdit ? "" : " disabled", st = 'style="width:100%;height:42px;box-sizing:border-box" data-f="' + f.name + '"';
      if (UPLOAD_SWITCH_OWNER[f.name]) {                       // Kambala ON/OFF switch (only these two fields, NOT upload_url)
        const yes = String(f.value).toLowerCase() === "yes";
        return field(lab, '<select class="f-input" ' + st + dis + '><option value="no"' + (yes ? "" : " selected") + '>OFF — only e-mail the file</option><option value="yes"' + (yes ? " selected" : "") + ">ON — e-mail and upload to Kambala</option></select>");
      }
      if (f.name === "alert_switch") {
        const off = String(f.value).toLowerCase() === "off";
        return field(lab, '<select class="f-input" ' + st + dis + '><option value="on"' + (off ? "" : " selected") + '>on</option><option value="off"' + (off ? " selected" : "") + ">off</option></select>");
      }
      return field(lab, '<input class="f-input" ' + st + " " + (f.secret ? 'type="password" autocomplete="new-password" placeholder="' + (f.set ? "(unchanged)" : "enter value") + '"' : 'value="' + esc(f.value || "") + '"') + dis + ">");
    };
    const GRID = '<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 10px;align-items:start">';
    const main = g.fields.filter((f) => !ALERT.test(f.name)), alert = g.fields.filter((f) => ALERT.test(f.name));
    let body = GRID + main.map(one).join("") + "</div>";
    if (alert.length) {
      const num = alert.find((f) => f.name === "alert_numbers"), rest = alert.filter((f) => f !== num);
      body += '<div style="margin-top:14px;padding:12px;border:1px solid var(--border,#2a3142);border-radius:10px">' +
        '<div style="font-weight:700;margin-bottom:4px">⚠ Failure alerts on WhatsApp — COMMON numbers</div>' +
        '<div class="card-sub" style="margin-bottom:10px">Type the numbers here ONCE (with country code, e.g. 919812345678, separated by commas). Every process that has no numbers of its own sends its failure alert to these. A process that has its own numbers (📱 WhatsApp button on its row) uses only those.</div>' +
        (num ? '<div style="margin-bottom:10px">' + one(num) + "</div>" : "") + GRID + rest.map(one).join("") + "</div></div>";
    }
    return '<div class="card" style="margin-bottom:12px" data-g="' + g.group + '"><div class="sec-head"><h3>' + esc(g.label) + "</h3></div>" + body +
      (canEdit ? '<div style="margin-top:10px"><button class="btn primary" data-save="' + g.group + '">Save ' + esc(g.label) + "</button></div>" : "") + "</div>";
  }

  function bindConnSave(root, after) {
    root.querySelectorAll("[data-save]").forEach((b) => {
      b.onclick = async () => {
        const box = b.closest("[data-g]"), body = {};
        box.querySelectorAll("[data-f]").forEach((i) => { body[i.dataset.f] = i.value; });
        if (S.pw) body._password = S.pw;
        try {
          const r = await call("PUT", "/sched/connections/" + b.dataset.save, body);
          toast("ok", "Saved", r.changed && r.changed.length ? r.changed.length + " field(s) updated" : "nothing changed");
          after();
        } catch (e) { toast("err", "Not saved", e.message); }
      };
    });
  }

  async function openCredentials(key) {
    try {
      const c = await jget("/sched/connections");
      const p = S.procs.find((x) => x.key === key) || { name: key, uses: [] };
      const groups = c.groups.filter((g) => (p.uses || []).includes(g.group));
      const pwd = await unlock(c.needs_password, "Credentials — " + p.name);
      if (pwd === null) return;
      S.pw = pwd;
      const close = RMS.modal("Credentials — " + p.name,
        '<div class="card-sub" style="margin-bottom:10px">Shared with every process that uses the same connection. Saved in the .env files under “# scheduler connection”; passwords and tokens go to .env.local. Secrets are never shown again — leave blank to keep the current one.</div>' +
        groups.map((g) => connGroupHtml(g, true, key)).join(""), { width: 760, icon: "settings" });
      bindConnSave(document, () => { S.pw = ""; close(); load(); });
    } catch (e) { toast("err", "Could not open", e.message); }
  }

  /* ── RMS LIMITS MAND KAMBALA: add client codes (account id, limit, category) → file → e-mail → Kambala ── */
  async function openAddCodes() {
    try {
      const c = await jget("/sched/connections");
      const pwd = await unlock(c.needs_password, "Add client codes — RMS LIMITS MAND KAMBALA");
      if (pwd === null) return;
      const rowHtml = () => '<tr><td><input class="f-input ac-a" placeholder="e.g. 09110022" style="width:100%;text-transform:uppercase"></td>' +
        '<td><input class="f-input ac-l" inputmode="numeric" placeholder="e.g. 280000" style="width:100%"></td>' +
        '<td><input class="f-input ac-c" placeholder="blank = from risk report" style="width:100%;text-transform:uppercase"></td></tr>';
      const close = RMS.modal("Add client codes — RMS LIMITS MAND KAMBALA",
        '<div class="card-sub" style="margin-bottom:10px">New codes are added at the <b>end</b> of the master file only — nothing is e-mailed or uploaded now. ' +
        "The next scheduled run picks up the corrected file. Leave CATEGORY blank to take it from the risk report. Type the code exactly as it must appear in the file (e.g. with its leading zero). " +
        "Who added what is recorded in this process's Logs.</div>" +
        '<div id="acErr" style="color:var(--err);font-size:12.5px;margin-bottom:8px;white-space:pre-wrap"></div>' +
        '<table class="htable" style="width:100%"><thead><tr><th>ACCOUNT ID</th><th>LIMIT</th><th>CATEGORY</th></tr></thead><tbody id="acRows">' + rowHtml() + rowHtml() + rowHtml() + "</tbody></table>" +
        '<div style="display:flex;justify-content:space-between;gap:8px;margin-top:10px"><button class="btn ghost" id="acMore">+ Row</button>' +
        '<div style="display:flex;gap:8px"><button class="btn ghost" id="acCancel">Cancel</button><button class="btn primary" id="acGo">Add to file</button></div></div>' +
        '<pre class="logview" id="acLog" style="display:none;margin-top:12px;max-height:260px;overflow:auto;white-space:pre-wrap;font-size:11.5px"></pre>',
        { width: 760, icon: "upload" });
      $("#acMore").onclick = () => $("#acRows").insertAdjacentHTML("beforeend", rowHtml());
      $("#acCancel").onclick = close;
      $("#acGo").onclick = async () => {
        const rows = [...document.querySelectorAll("#acRows tr")].map((tr) => ({
          account: tr.querySelector(".ac-a").value.trim(), limit: tr.querySelector(".ac-l").value.trim(), category: tr.querySelector(".ac-c").value.trim(),
        })).filter((r) => r.account || r.limit || r.category);
        const go = $("#acGo"); go.disabled = true; $("#acErr").textContent = "";
        try {
          const body = { rows }; if (pwd) body._password = pwd;
          const r = await call("POST", "/sched/rms-limits/add", body);
          const log = $("#acLog"); log.style.display = "block"; log.textContent = r.log || "";
          $("#acErr").style.color = "var(--ok)"; $("#acErr").textContent = "✔ " + r.result;
          $("#acGo").style.display = "none"; $("#acCancel").textContent = "Close";
        } catch (e) { $("#acErr").textContent = e.message; go.disabled = false; }
      };
    } catch (e) { toast("err", "Could not open", e.message); }
  }

  /* ── Logs of the last runs of this process ── */
  async function openLogs(key) {
    try {
      const f = await jget("/sched/files/" + encodeURIComponent(key));
      const close = RMS.modal("Logs — " + f.process,
        '<div class="card-sub" style="margin-bottom:8px">Last runs of this process (newest first). Folder: <code>' + esc(f.folder) + "\\logs</code></div>" +
        '<table class="htable"><tbody>' + ((f.logs || []).length ? f.logs.map((l) => "<tr><td>" + esc(l.modified) + "</td><td>" + (l.name.includes("_FAILED") ? '<span class="badge err">Failed</span>' : l.name.includes("_SKIPPED") ? '<span class="badge dim">Skipped</span>' : '<span class="badge ok">OK</span>') +
          "</td><td style='text-align:right'><button class='btn sm ghost' data-lg='" + esc(l.name) + "'>View</button> <a class='btn sm ghost' href='/sched/files/" + encodeURIComponent(key) + "/download?area=logs&name=" + encodeURIComponent(l.name) + "'>Download</a></td></tr>").join("")
          : "<tr><td><div class='bm-empty'>No runs yet</div></td></tr>") + "</tbody></table>", { width: 720, icon: "terminal" });
      document.querySelectorAll("[data-lg]").forEach((b) => {
        b.onclick = async () => {
          try {
            const t = await jget("/sched/files/" + encodeURIComponent(key) + "/log?name=" + encodeURIComponent(b.dataset.lg));
            close();
            RMS.modal(t.name, '<pre class="logview" style="max-height:460px;overflow:auto;white-space:pre-wrap;font-size:11.5px">' + RMS.colorizeLog(t.text) + "</pre>", { width: 820, icon: "terminal" });
          } catch (e) { toast("err", "Could not open", e.message); }
        };
      });
    } catch (e) { toast("err", "Could not open", e.message); }
  }

  /* ═══════════════════════ UPLOAD POPUP ═════════════════════════════════ */
  function fmtSize(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }

  async function openUpload(key) {
    try {
      const f = await jget("/sched/files/" + encodeURIComponent(key));
      const enc = encodeURIComponent(key), exts = (f.upload_exts || []).join(", ");
      const close = RMS.modal("Upload — " + f.process,
        '<div class="card-sub" style="margin-bottom:10px">' + esc(f.upload_help || "") + " Allowed: <b>" + esc(exts) + "</b>.</div>" +
        '<input type="file" id="upFile" accept="' + esc(exts) + '" style="display:none">' +
        '<button class="btn primary" id="upPick" style="width:100%;justify-content:center;height:44px;margin-bottom:6px">⬆️ Choose file to upload</button>' +
        '<div id="upMsg" class="card-sub" style="min-height:18px;margin-bottom:8px"></div>' +
        '<div class="card-sub" style="font-weight:800;margin:8px 0 4px">FILES WAITING FOR THE NEXT RUN</div>' +
        '<table class="htable"><tbody>' + ((f.inputs || []).length ? f.inputs.map((i) => "<tr><td>" + esc(i.name) + "</td><td class='card-sub'>" + fmtSize(i.size) + "</td><td class='card-sub'>" +
          esc(i.modified) + "</td><td style='text-align:right'><button class='btn sm ghost' data-del='" + esc(i.name) + "'>Remove</button></td></tr>").join("")
          : "<tr><td><div class='bm-empty'>Nothing uploaded</div></td></tr>") + "</tbody></table>" +
        '<div class="card-sub" style="margin-top:10px">After uploading, use <b>Run Now</b> on the Schedules tab if you do not want to wait for the next scheduled time.</div>', { width: 620, icon: "download" });
      $("#upPick").onclick = () => $("#upFile").click();
      $("#upFile").onchange = async () => {
        const file = $("#upFile").files[0];
        if (!file) return;
        $("#upMsg").textContent = "Uploading " + file.name + "…";
        const fd = new FormData(); fd.append("file", file);
        try {
          const r = await fetch("/sched/files/" + enc + "/upload", { method: "POST", body: fd, credentials: "same-origin" });
          const d = await r.json().catch(() => ({}));
          if (!r.ok || d.ok === false) throw new Error(d.error || ("HTTP " + r.status));
          close();
          notice("Upload successful", d.name + " is saved and will be used by the next run.");
        } catch (e) { $("#upMsg").innerHTML = '<span style="color:var(--err)">' + esc(e.message) + "</span>"; }
      };
      document.querySelectorAll("[data-del]").forEach((b) => {
        b.onclick = async () => {
          try { await call("DELETE", "/sched/files/" + enc + "?name=" + encodeURIComponent(b.dataset.del)); close(); notice("File removed", b.dataset.del + " has been removed."); }
          catch (e) { close(); notice("Could not remove", e.message, "err"); }
        };
      });
    } catch (e) { toast("err", "Could not open", e.message); }
  }

  /* ── in-app popups (no browser "localhost says" boxes, no toasts at the screen edge) ── */
  function notice(title, msg, kind) {
    const ok = kind !== "err";
    const close = RMS.modal(title,
      '<div style="text-align:center;padding:6px 4px 2px"><div style="font-size:40px;line-height:1.1">' + (ok ? "✅" : "⚠️") + "</div>" +
      '<div style="margin:12px 0 4px;font-size:13.5px;line-height:1.5;word-break:break-word">' + esc(msg) + "</div>" +
      '<button class="btn primary" id="ntOk" style="margin-top:14px;min-width:120px;justify-content:center">OK</button></div>',
      { width: 400, icon: ok ? "check" : "alert" });
    const b = $("#ntOk");
    b.onclick = close;
    b.focus();
    if (ok) setTimeout(() => { try { close(); } catch (e) { /* already closed */ } }, 4000);
  }

  /* every result message on this page is a centred popup, never an edge toast */
  function toast(kind, title, msg) { notice(title, msg || "", kind === "err" ? "err" : "ok"); }

  function confirmBox(title, msg, okLabel, danger) {
    return new Promise((resolve) => {
      const close = RMS.modal(title,
        '<div style="font-size:13.5px;line-height:1.55;margin-bottom:16px">' + esc(msg) + "</div>" +
        '<div style="display:flex;justify-content:flex-end;gap:8px"><button class="btn ghost" id="cbNo">Cancel</button>' +
        '<button class="btn primary" id="cbYes"' + (danger ? ' style="background:var(--err);border-color:var(--err);color:#fff"' : "") + ">" + esc(okLabel || "OK") + "</button></div>",
        { width: 440, icon: danger ? "alert" : "info" });
      $("#cbYes").onclick = () => { close(); resolve(true); };
      $("#cbNo").onclick = () => { close(); resolve(false); };
      $("#cbNo").focus();
    });
  }

  /* ── password gate: asked BEFORE the form opens (same as the Process Center) ── */
  function askPassword(title) {
    return new Promise((resolve) => {
      const close = RMS.modal(title,
        '<div class="card-sub" style="margin-bottom:10px">Enter the console password to continue (the same one the Process Center asks for).</div>' +
        '<div id="pwErr" style="color:var(--err);font-size:12.5px;min-height:18px;margin-bottom:6px"></div>' +
        '<input type="password" id="pwIn" class="f-input" style="width:100%;height:42px;box-sizing:border-box" autocomplete="new-password" placeholder="Password">' +
        '<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px"><button class="btn ghost" id="pwCancel">Cancel</button><button class="btn primary" id="pwOk">Continue</button></div>',
        { width: 420, icon: "settings" });
      const go = async () => {
        const v = $("#pwIn").value;
        if (!v) return;
        try { await call("POST", "/sched/verify-password", { password: v }); close(); resolve(v); }
        catch (e) { $("#pwErr").textContent = e.message; $("#pwIn").select(); }
      };
      $("#pwOk").onclick = go;
      $("#pwIn").onkeydown = (e) => { if (e.key === "Enter") go(); };
      $("#pwCancel").onclick = () => { close(); resolve(null); };
      $("#pwIn").focus();
    });
  }

  /* resolves "" for an administrator, the verified password for everyone else, null if cancelled */
  async function unlock(needsPassword, title) {
    if (!needsPassword) return "";
    return askPassword(title);
  }

  /* ═══════════════════════ PORTAL DOWNLOADS TAB ═════════════════════════
     File jobs defined on screen (core/scheduler/portal_jobs.py).  List of job cards  →  full-page editor with a live
     preview of the file name, the flow and a Test download that logs live.  Every saved job is a normal Scheduler
     process (⏰ Schedule opens the usual schedule editor for it). */
  const PD_SEGMENTS = ["CM", "FO", "CO", "CD", "SLB", "COM"];

  function pdBlank() {
    const d = (S.portal && S.portal.defaults) || {};
    return { id: "", name: "", description: "", source: "member", segment: "", folder: "Reports", filename: "", days_back: 1,
      trading_day_only: true, wait_minutes: 0, unzip: true,
      email: { enabled: false, to: "", cc: "", subject: "{NAME} - {DD-MM-YYYY}", body: d.body || "Hi Team,\n\nPlease find attached {FILE}.",
        signature: d.signature || "", notify_no_file: true },
      kambala: { enabled: false, name: "" },
      filter: { enabled: false, keep_name: true, mail_note: false, match: "all", conditions: [{ column: "", op: "in", values: "" }] } };
  }
  const PD_OPS = [["eq", "is exactly (same case)"], ["neq", "is not exactly"], ["in", "is one of"], ["not_in", "is not one of"], ["contains", "contains"], ["not_contains", "does not contain"],
    ["starts", "starts with"], ["gt", "is greater than"], ["gte", "is at least"], ["lt", "is less than"], ["lte", "is at most"]];
  const pdNeedsPw = () => !!(S.portal && S.portal.needs_password);

  function flowChips(j) {
    const st = (on, ic, tx) => '<span class="pd-step ' + (on ? "on" : "off") + '">' + ic + " " + tx + "</span>";
    return st(true, "⬇", "Download") + '<span class="pd-arrow">›</span>' + st(!!j.unzip, "🗜", "Unzip") + '<span class="pd-arrow">›</span>' +
      st(!!(j.filter && j.filter.enabled), "⛁", "Filter") + '<span class="pd-arrow">›</span>' +
      st(!!(j.email && j.email.enabled), "✉", "E-mail") + '<span class="pd-arrow">›</span>' + st(!!(j.kambala && j.kambala.enabled), "⬆", "Kambala");
  }

  function renderPortal() {
    if (S.pedit && $("#pdEditor")) return;
    if (S.pedit) { renderPortalEditor(); return; }
    const P = S.portal || { jobs: [] };
    const jobs = P.jobs || [];
    const hero = '<div class="pd-hero"><div class="pd-hero-tx"><h2>Portal Downloads</h2>' +
      "<p>Tell the console which file to fetch from the NSE portal once. It downloads it on the day you choose, unzips it, e-mails it and uploads it to the Kambala FTP — and says so clearly when NSE has no file.</p></div>" +
      '<button class="btn primary pd-new" id="pdNew">＋ New download</button></div>';
    if (!jobs.length) {
      $("#scBody").innerHTML = hero + '<div class="pd-empty"><div style="font-size:40px">📥</div><h3>No downloads yet</h3>' +
        "<p>Create your first one in about a minute. No code, no deployment.</p>" +
        '<div class="pd-how"><div><b><em>1</em>Describe the file</b>Segment, folder and the file name with date placeholders, e.g. <code>{YYYYMMDD}</code>.</div>' +
        '<div><b><em>2</em>Choose what happens</b>Unzip, e-mail it, upload to Kambala. Test it with one click.</div>' +
        '<div><b><em>3</em>Schedule it</b>It then runs by itself and follows the holiday calendar.</div></div>' +
        '<button class="btn primary pd-new" id="pdNew2">＋ New download</button></div>';
    } else {
      const card = (j) => {
        const r = j.resolved || {};
        const live = (j.schedules || []).some((s) => s.status === "enabled");
        const st = !(j.schedules || []).length ? '<span class="badge dim">Not scheduled</span>' : live ? '<span class="badge ok">Scheduled</span>' : '<span class="badge dim">Paused</span>';
        const dayTxt = j.days_back <= 0 ? "today" : j.days_back === 1 ? "previous trading day" : j.days_back + " trading days back";
        return '<div class="pd-card" data-id="' + esc(j.id) + '"><div class="pd-top"><div class="pd-seg">' + esc(j.segment) + "</div>" +
          '<div class="pd-ttl"><b title="' + esc(j.name) + '">' + esc(j.name) + "</b><span>" + (j.source === "common" ? "Common" : "Member") + " portal · " + esc(j.segment) + " › " + esc(j.folder) + "</span></div>" + st + "</div>" +
          '<div class="pd-file"><div class="lbl"><span>File for ' + esc(dayTxt) + "</span><span>" + esc(r.date_text || "") + "</span></div><code>" + esc(r.filename || j.filename) + "</code></div>" +
          '<div class="pd-flow">' + flowChips(j) + "</div>" +
          ((j.schedules || []).length ? '<div class="pd-sched">⏰ <i>' + esc(j.schedules[0].name) + "</i>" + (j.schedules.length > 1 ? " +" + (j.schedules.length - 1) + " more" : "") + "</div>" : "") +
          '<div class="pd-actions"><button class="sch-tool" data-a="test">▶ Test</button><button class="sch-tool" data-a="edit">✎ Edit</button>' +
          '<button class="sch-tool blue" data-a="sched">⏰ Schedule</button><button class="sch-tool" data-a="logs">📜 Logs</button></div></div>';
      };
      $("#scBody").innerHTML = hero + '<div class="pd-grid">' + jobs.map(card).join("") + "</div>";
    }
    const b = $("#scBody");
    ["pdNew", "pdNew2"].forEach((id) => { const e = b.querySelector("#" + id); if (e) e.onclick = () => pdOpen(null); });
    b.querySelectorAll(".pd-card [data-a]").forEach((x) => {
      x.onclick = () => {
        const j = (S.portal.jobs || []).find((q) => q.id === x.closest(".pd-card").dataset.id);
        if (!j) return;
        if (x.dataset.a === "edit") pdOpen(j);
        else if (x.dataset.a === "test") pdTestModal(j);
        else if (x.dataset.a === "logs") openLogs(j.key);
        else openEditor(null, j.key);
      };
    });
  }

  function pdOpen(job) {
    S.pedit = { id: job ? job.id : "", draft: job ? JSON.parse(JSON.stringify(job)) : pdBlank(), test: null, last: null };
    ["resolved", "key", "schedules", "updated"].forEach((k) => delete S.pedit.draft[k]);
    if (!S.pedit.draft.filter) S.pedit.draft.filter = pdBlank().filter;
    if (S.pedit.draft.filter.keep_name == null) S.pedit.draft.filter.keep_name = true;
    if (S.pedit.draft.filter.mail_note == null) S.pedit.draft.filter.mail_note = false;
    if (!S.pedit.draft.filter.conditions.length) S.pedit.draft.filter.conditions.push({ column: "", op: "in", values: "" });
    renderPortalEditor();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function pdClose() { S.pedit = null; render(); load(); }

  function pdGet(path) { return path.split(".").reduce((o, k) => (o == null ? o : o[k]), S.pedit.draft); }
  function pdSet(path, v) {
    const ks = path.split("."); let o = S.pedit.draft;
    while (ks.length > 1) o = o[ks.shift()];
    o[ks[0]] = v;
  }

  const pdIn = (path, label, opts) => {
    opts = opts || {};
    const tag = opts.area ? "textarea" : "input";
    return "<div" + (opts.style ? ' style="' + opts.style + '"' : "") + '><label class="pd-fl">' + label + (opts.opt ? " <em>(optional)</em>" : "") + "</label>" +
      "<" + tag + ' class="pd-in' + (opts.mono ? " mono" : "") + '" data-p="' + path + '" ' + (opts.area ? 'rows="' + (opts.rows || 3) + '"' : 'type="' + (opts.type || "text") + '"') +
      ' placeholder="' + esc(opts.ph || "") + '" autocomplete="off" spellcheck="false"' + (opts.list ? ' list="' + opts.list + '"' : "") + (opts.id ? ' id="' + opts.id + '"' : "") +
      (opts.area ? ">" + esc(pdGet(path) || "") + "</textarea>" : ' value="' + esc(pdGet(path) == null ? "" : pdGet(path)) + '">') +
      (opts.hint ? '<div class="pd-hint">' + opts.hint + "</div>" : "") + "</div>";
  };
  const pdSwitch = (path, title, sub) =>
    '<div class="pd-tog" data-sw="' + path + '"><div class="tx"><b>' + title + "</b><span class='s'>" + sub + '</span></div><div class="switch' + (pdGet(path) ? " on" : "") + '"></div></div>';

  function renderPortalEditor() {
    const E = S.pedit, d = E.draft, P = S.portal || {};
    const tokens = (P.tokens || []).map((t) => '<span class="pd-chip" data-tok="' + esc(t.token) + '" title="' + esc(t.example) + '">' + esc(t.token) + "</span>").join("");
    const dayMode = d.days_back <= 0 ? "0" : d.days_back === 1 ? "1" : "n";
    $("#scBody").innerHTML =
      '<div id="pdEditor"><div class="pd-bar"><button class="btn ghost" id="pdBack">← All downloads</button><h2>' + (E.id ? "Edit download" : "New download") + "</h2>" +
      '<button class="btn ghost" id="pdCancel">Cancel</button><button class="btn primary" id="pdSave">Save download</button></div>' +
      '<div class="pd-edit"><div>' +

      /* 1 — what */
      '<div class="pd-sec"><div class="pd-sh"><span class="pd-n">1</span><h3>What to download</h3><small>from the NSE portal</small></div>' +
      '<div class="pd-row c21">' + pdIn("name", "Name", { ph: "e.g. NCL CO Position File", hint: "Also the name of the process in the Scheduler." }) +
      '<div><label class="pd-fl">Portal</label><div class="pd-seg2" data-seg="source"><button data-v="member" class="' + (d.source === "member" ? "on" : "") + '">Member</button><button data-v="common" class="' + (d.source === "common" ? "on" : "") + '">Common</button></div></div></div>' +
      '<div class="pd-row c2">' + pdIn("segment", "Segment", { ph: "CO", list: "pdSegs", mono: true }) + pdIn("folder", "Folder", { ph: "Reports", mono: true, hint: "As in the portal tree, e.g. <b>Reports</b>, <b>Letters/Dnld</b>, <b>/Bhavcopy</b>." }) +
      '<datalist id="pdSegs">' + PD_SEGMENTS.map((s) => '<option value="' + s + '">').join("") + "</datalist></div>" +
      '<div class="pd-row" style="grid-template-columns:1fr">' + pdIn("filename", "File name", { ph: "Position_NCL_CO_0_CM_{member}_{YYYYMMDD}_F_0000.csv.gz", mono: true, id: "pdFile",
        hint: "Type the name once and replace the changing parts with placeholders. Click one below to insert it where the cursor is." }) +
      '<div style="margin-top:-6px"><div class="pd-chips">' + tokens + "</div></div></div>" +
      '<div class="pd-row" style="grid-template-columns:1fr">' + pdIn("description", "Note", { opt: true, ph: "What is this file for?" }) + "</div></div>" +

      /* 2 — when */
      '<div class="pd-sec"><div class="pd-sh"><span class="pd-n">2</span><h3>Which day’s file</h3><small>the date that goes into the file name</small></div>' +
      '<div class="pd-row"><div class="pd-seg2" id="pdDay"><button data-v="0" class="' + (dayMode === "0" ? "on" : "") + '">Today</button><button data-v="1" class="' + (dayMode === "1" ? "on" : "") +
      '">Previous trading day</button><button data-v="n" class="' + (dayMode === "n" ? "on" : "") + '">Earlier day…</button></div>' +
      '<div id="pdDayN" style="' + (dayMode === "n" ? "" : "display:none;") + 'display:' + (dayMode === "n" ? "flex" : "none") + ';align-items:center;gap:10px;font-size:12.5px;color:var(--text-2)">' +
      '<input class="pd-in pd-step-in" type="number" min="2" max="10" data-p="days_back" value="' + (d.days_back >= 2 ? d.days_back : 2) + '"> trading days back (weekends and NSE holidays are skipped)</div></div>' +
      pdSwitch("trading_day_only", "Run only on trading days", "On a weekend or NSE holiday the run is skipped instead of looking for a file.") +
      '<div class="pd-row c2">' + pdIn("wait_minutes", "Wait for the file", { type: "number", hint: "0 = try once. Otherwise keep checking every minute for up to this many minutes." }) + "<div></div></div></div>" +

      /* 3 — after */
      '<div class="pd-sec"><div class="pd-sh"><span class="pd-n">3</span><h3>After the download</h3><small>everything is backed up automatically</small></div>' +
      pdSwitch("unzip", "Unzip the file", "For .gz and .zip files — the extracted file is what gets e-mailed / uploaded.") +
      pdSwitch("filter.enabled", "Keep only some rows (filter)", "Off = the full file. On = only the rows that match are saved, e-mailed and uploaded (the full file stays in the backup).") +
      '<div class="pd-sub" id="pdFilt"><div class="pd-row" style="grid-template-columns:1fr"><div><label class="pd-fl">Keep a row when</label>' +
      '<div class="pd-seg2" data-seg="filter.match"><button data-v="all" class="' + (d.filter.match !== "any" ? "on" : "") + '">ALL conditions match</button><button data-v="any" class="' + (d.filter.match === "any" ? "on" : "") + '">ANY condition matches</button></div></div></div>' +
      d.filter.conditions.map((c, i) => '<div class="pd-row" style="grid-template-columns:1.2fr 1fr 1.6fr auto;align-items:end">' +
        pdIn("filter.conditions." + i + ".column", i ? "" : "Column name", { mono: true, ph: "e.g. Symbol" }) +
        '<div><label class="pd-fl">' + (i ? "" : "Condition") + '</label><select class="pd-in" data-p="filter.conditions.' + i + '.op">' +
        PD_OPS.map((o) => '<option value="' + o[0] + '"' + (c.op === o[0] ? " selected" : "") + ">" + o[1] + "</option>").join("") + "</select></div>" +
        pdIn("filter.conditions." + i + ".values", i ? "" : "Value(s)", { mono: true, ph: "e.g. NIFTY, BANKNIFTY" }) +
        '<button class="sch-tool" data-rmc="' + i + '" title="Remove this condition">✕</button></div>').join("") +
      '<div class="pd-hint" style="margin:2px 0 8px">Column names are the header names in the file (case does not matter). Several values: separate with commas. If a name is wrong the run stops and lists the real columns.</div>' +
      '<button class="sch-tool" id="pdAddCond">＋ Add condition</button>' +
      pdSwitch("filter.keep_name", "Keep the original file name", "On = the filtered file has the same name as the portal file (the full file is kept in the backup as …_FULL). Off = name ends with _filtered.") +
      pdSwitch("filter.mail_note", "Add “Filtered file: X of Y rows kept” to the e-mail", "Off = the e-mail text is exactly your message.") + "</div>" +
      pdSwitch("email.enabled", "E-mail the file", "Sent first, before the Kambala upload.") +
      '<div class="pd-sub" id="pdMail"><div class="pd-row c2">' + pdIn("email.to", "To", { ph: "name@mastertrust.co.in, …" }) + pdIn("email.cc", "Cc", { opt: true, ph: "optional" }) + "</div>" +
      '<div class="pd-row" style="grid-template-columns:1fr">' + pdIn("email.subject", "Subject", { mono: true, hint: "Placeholders work here too, plus <b>{NAME}</b> and <b>{FILE}</b>." }) +
      pdIn("email.body", "Message", { area: true, rows: 4, hint: "Keep it simple. <b>{FILE}</b> becomes the file name." }) +
      pdIn("email.signature", "Signature", { area: true, rows: 4 }) + "</div>" +
      pdSwitch("email.notify_no_file", "E-mail me when there is no file", "Sends the same subject + “ - NO FILE” saying the portal has no file for that date. The run still ends as success.") + "</div>" +
      pdSwitch("kambala.enabled", "Upload to the Kambala FTP", "Same server and folder as the other Kambala uploads.") +
      '<div class="pd-sub" id="pdKamb"><div class="pd-row" style="grid-template-columns:1fr">' + pdIn("kambala.name", "Name on the FTP", { opt: true, mono: true, ph: "leave empty to keep the file name", hint: "Placeholders work here too." }) + "</div></div>" +
      "</div></div>" +

      /* live preview */
      '<div class="pd-side"><div class="pd-pv"><h4>Live preview</h4>' +
      '<div class="kv"><span>File date</span><b id="pvDate">—</b></div>' +
      '<div style="margin:10px 0 4px;font-size:11px;font-weight:700;color:var(--text-3);letter-spacing:.5px;text-transform:uppercase">File name</div><div class="big" id="pvFile">—</div>' +
      '<div class="kv" id="pvSubjRow"><span>E-mail subject</span><b id="pvSubj">—</b></div>' +
      '<div style="margin:12px 0 8px" class="pd-flow" id="pvFlow"></div><div class="pd-conn" id="pvConn"></div><div class="pd-errs" id="pvErrs"></div></div>' +
      '<div class="pd-pv"><h4>Test it</h4><div style="font-size:12px;color:var(--text-2);line-height:1.5;margin-bottom:12px">Tries ONE real download from NSE with today’s settings. Nothing is e-mailed, uploaded or kept.</div>' +
      '<button class="btn primary" id="pdTest" style="width:100%;justify-content:center;height:38px">▶ Test download</button><div class="pd-verdict" id="pdVerdict"></div><div class="pd-log" id="pdLog"></div></div>' +
      "</div></div></div>";

    const root = $("#pdEditor");
    $("#pdBack").onclick = $("#pdCancel").onclick = async () => {
      if (pdDirty() && !(await confirmBox("Discard changes?", "You have unsaved changes to this download.", "Discard", true))) return;
      pdClose();
    };
    $("#pdSave").onclick = pdSave;
    $("#pdTest").onclick = () => pdRunTest($("#pdTest"), $("#pdVerdict"), $("#pdLog"), S.pedit.draft);

    root.querySelectorAll("[data-p]").forEach((el) => {
      el.addEventListener("focus", () => { if (el.tagName !== "SELECT" && el.type !== "number") S.pLast = el; });
      el.addEventListener("input", () => {
        let v = el.value;
        if (el.type === "number") v = v === "" ? 0 : parseInt(v, 10) || 0;
        pdSet(el.dataset.p, v);
        pdSync(false);
      });
    });
    root.querySelectorAll("[data-sw]").forEach((el) => {
      el.onclick = () => { const p = el.dataset.sw, v = !pdGet(p); pdSet(p, v); el.querySelector(".switch").classList.toggle("on", v); pdSync(true); };
    });
    root.querySelectorAll("[data-seg]").forEach((g) => g.querySelectorAll("button").forEach((b) => {
      b.onclick = () => { pdSet(g.dataset.seg, b.dataset.v); g.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); pdSync(true); };
    }));
    $("#pdDay").querySelectorAll("button").forEach((b) => {
      b.onclick = () => {
        $("#pdDay").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
        const n = b.dataset.v === "n" ? Math.max(2, parseInt(root.querySelector('[data-p="days_back"]').value, 10) || 2) : parseInt(b.dataset.v, 10);
        pdSet("days_back", n);
        $("#pdDayN").style.display = b.dataset.v === "n" ? "flex" : "none";
        if (b.dataset.v === "n") root.querySelector('[data-p="days_back"]').value = n;
        pdSync(true);
      };
    });
    root.querySelectorAll("[data-tok]").forEach((c) => {
      c.onmousedown = (e) => e.preventDefault();         // keep the cursor where it was
      c.onclick = () => {
        const el = S.pLast && root.contains(S.pLast) ? S.pLast : $("#pdFile");
        const s = el.selectionStart == null ? el.value.length : el.selectionStart, e2 = el.selectionEnd == null ? s : el.selectionEnd;
        el.value = el.value.slice(0, s) + c.dataset.tok + el.value.slice(e2);
        el.focus(); el.setSelectionRange(s + c.dataset.tok.length, s + c.dataset.tok.length);
        pdSet(el.dataset.p, el.value); pdSync(false);
      };
    });
    const rerender = () => { const snap = E.snap; renderPortalEditor(); E.snap = snap; };
    $("#pdAddCond").onclick = () => { d.filter.conditions.push({ column: "", op: "in", values: "" }); rerender(); };
    root.querySelectorAll("[data-rmc]").forEach((b) => {
      b.onclick = () => {
        d.filter.conditions.splice(parseInt(b.dataset.rmc, 10), 1);
        if (!d.filter.conditions.length) d.filter.conditions.push({ column: "", op: "in", values: "" });
        rerender();
      };
    });
    S.pedit.snap = JSON.stringify(S.pedit.draft);
    pdSync(true);
  }

  const pdDirty = () => !!(S.pedit && S.pedit.snap && S.pedit.snap !== JSON.stringify(S.pedit.draft));

  let pdTimer = null;
  function pdSync(now) {
    const d = S.pedit.draft;
    $("#pdFilt").style.display = d.filter && d.filter.enabled ? "" : "none";
    $("#pdMail").style.display = d.email.enabled ? "" : "none";
    $("#pdKamb").style.display = d.kambala.enabled ? "" : "none";
    $("#pvFlow").innerHTML = flowChips(d);
    $("#pvSubjRow").style.display = d.email.enabled ? "" : "none";
    const ok = (g) => { const x = ((S.conns && S.conns.groups) || []).find((q) => q.group === g); return !!x && x.fields.every((f) => f.optional || (f.secret ? f.set : !!f.value)); };
    const need = ["nse"].concat(d.email.enabled ? ["smtp"] : [], d.kambala.enabled ? ["kambala_ftp"] : []);
    $("#pvConn").innerHTML = need.map((g) => '<span class="badge ' + (ok(g) ? "ok" : "warn") + '">' + esc(CONN_NAME[g] || g) + (ok(g) ? " ✓" : " — not set") + "</span>").join("");
    clearTimeout(pdTimer);
    pdTimer = setTimeout(pdPreview, now ? 0 : 350);
  }

  async function pdPreview() {
    if (!S.pedit || !$("#pvFile")) return;
    const seq = (S.pedit.pvSeq = (S.pedit.pvSeq || 0) + 1);
    try {
      const r = await call("POST", "/sched/portal/preview", Object.assign({}, S.pedit.draft, { id: S.pedit.id }));
      if (!S.pedit || seq !== S.pedit.pvSeq || !$("#pvFile")) return;
      const x = r.resolved || {};
      $("#pvDate").textContent = x.date_text ? x.date_text + " — " + new Date(x.date + "T00:00:00").toLocaleDateString("en-GB", { weekday: "long" }) : "—";
      $("#pvFile").textContent = x.filename || "—";
      $("#pvSubj").textContent = x.subject || "—";
      // while the form is still being filled in only placeholder typos are shown; everything after a Save attempt
      const errs = (r.errors || []).filter((e) => S.pedit.tried || /placeholder/i.test(e));
      $("#pvErrs").innerHTML = errs.map((e) => "• " + esc(e)).join("<br>");
    } catch (e) { /* preview is a nicety — never block the form */ }
  }

  async function pdPoll(tid, verdict, log, btn) {
    for (let i = 0; i < 400; i++) {
      await new Promise((r) => setTimeout(r, 900));
      let s;
      try { s = await jget("/sched/portal/test/" + tid); } catch (e) { verdict.className = "pd-verdict err"; verdict.textContent = e.message; break; }
      log.textContent = s.log || ""; log.scrollTop = log.scrollHeight;
      if (s.done) {
        verdict.className = "pd-verdict " + (s.ok === false ? "err" : s.found ? "ok" : "warn");
        verdict.textContent = (s.ok === false ? "✕ " : s.found ? "✓ " : "ⓘ ") + s.result;
        break;
      }
    }
    btn.disabled = false; btn.innerHTML = "▶ Test download";
  }

  async function pdRunTest(btn, verdict, log, job) {
    btn.disabled = true; btn.innerHTML = '<span class="pd-spin"></span>Testing…';
    verdict.className = "pd-verdict"; verdict.textContent = ""; log.textContent = "";
    try {
      const r = await call("POST", "/sched/portal/test", job);
      await pdPoll(r.id, verdict, log, btn);
    } catch (e) {
      verdict.className = "pd-verdict err"; verdict.textContent = "✕ " + e.message;
      btn.disabled = false; btn.innerHTML = "▶ Test download";
    }
  }

  function pdTestModal(j) {
    const draft = JSON.parse(JSON.stringify(j));
    const close = RMS.modal("Test download — " + j.name,
      '<div class="card-sub" style="margin-bottom:10px">File: <code>' + esc((j.resolved || {}).filename || j.filename) + "</code><br>One real download attempt from NSE. Nothing is e-mailed, uploaded or kept.</div>" +
      '<button class="btn primary" id="tmGo" style="width:100%;justify-content:center;height:38px">▶ Test download</button><div class="pd-verdict" id="tmV"></div><div class="pd-log" id="tmL"></div>',
      { width: 640, icon: "download" });
    $("#tmGo").onclick = () => pdRunTest($("#tmGo"), $("#tmV"), $("#tmL"), draft);
  }

  async function pdSave() {
    const E = S.pedit, btn = $("#pdSave");
    E.tried = true;
    const pw = await unlock(pdNeedsPw(), E.id ? "Save download — " + E.draft.name : "Create download");
    if (pw === null) return;
    const body = JSON.parse(JSON.stringify(E.draft));
    if (pw) body._password = pw;
    btn.disabled = true;
    try {
      const r = await call(E.id ? "PUT" : "POST", E.id ? "/sched/portal/" + encodeURIComponent(E.id) : "/sched/portal", body);
      const created = !E.id, job = r.job;
      S.pedit = null; S.loaded.portal = false;
      await load();
      const next = created ? " Next: press ⏰ Schedule on its card to set the time." : "";
      notice("Saved", "“" + job.name + "” is saved." + next);
    } catch (e) {
      btn.disabled = false;
      const errs = (e.errors && e.errors.length) ? e.errors : [e.message];
      $("#pvErrs").innerHTML = errs.map((x) => "• " + esc(x)).join("<br>");
      $("#pvErrs").scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  /* ═══════════════════════ TODAY TAB (unchanged timeline) ═══════════════ */
  const TB = {
    completed: '<span class="badge ok">✓ done</span>', running: '<span class="badge info">running</span>',
    due: '<span class="badge warn">ready now</span>', overdue: '<span class="badge err">overdue</span>',
    waiting: '<span class="badge dim">waiting</span>', upcoming: '<span class="badge dim">upcoming</span>',
  };
  const titems = () => ((S.today && S.today.items) || []).filter((i) => S.tpipes.has(i.pipe));

  function renderToday() {
    if (!S.today) { $("#scBody").innerHTML = '<div class="card"><div class="bm-empty">' + (S.err ? esc(S.err) : "Loading…") + "</div></div>"; return; }
    const it = titems(), g = (s) => it.filter((i) => s.includes(i.status));
    const kp = kpi("check", "Completed", g(["completed"]).length, "of " + it.length + " today", "ok") +
      kpi("clock", "Due Now", g(["due", "overdue"]).length, g(["overdue"]).length + " overdue", g(["overdue"]).length ? "err" : g(["due"]).length ? "warn" : "") +
      kpi("activity", "Running", g(["running"]).length, "") + kpi("calendar", "Upcoming", g(["upcoming"]).length, "later today") +
      kpi("alert", "Missed Window", g(["waiting"]).length, "window passed, earlier run pending", g(["waiting"]).length ? "warn" : "");
    $("#scBody").innerHTML = '<div class="card" style="margin-bottom:12px"><div class="filters">' +
      '<div class="seg" id="tvSeg"><button data-v="timeline" class="' + (S.tview === "timeline" ? "on" : "") + '">Timeline</button><button data-v="list" class="' + (S.tview === "list" ? "on" : "") + '">List</button></div>' +
      '<div class="root-chips" id="tvPipes">' + PIPES.map(([k, n]) => '<span class="chip' + (S.tpipes.has(k) ? " on" : "") + '" data-p="' + k + '">' + esc(n) + "</span>").join("") + "</div></div></div>" +
      '<div class="kpi-grid" style="margin-bottom:12px">' + kp + '</div><div id="tvBody"></div>' +
      '<div class="card-sub" style="margin-top:14px">These are the Process Center\'s own run windows (read-only). Schedules you create on the Schedules tab are separate.</div>';
    $("#tvSeg").querySelectorAll("button").forEach((b) => { b.onclick = () => { S.tview = b.dataset.v; renderToday(); }; });
    $("#tvPipes").querySelectorAll(".chip").forEach((c) => {
      c.onclick = () => { const p = c.dataset.p; if (S.tpipes.has(p) && S.tpipes.size > 1) S.tpipes.delete(p); else S.tpipes.add(p); renderToday(); };
    });
    S.tview === "timeline" ? renderTimeline() : renderList();
  }

  function renderTimeline() {
    const it = titems().filter((i) => i.start_min != null);
    if (!it.length) { $("#tvBody").innerHTML = '<div class="card"><div class="bm-empty">No windows for the selected pipelines.</div></div>'; return; }
    const H = 56, first = Math.max(0, Math.floor(Math.min(...it.map((i) => i.start_min)) / 60) - 1);
    const last = Math.min(23, Math.ceil(Math.max(...it.map((i) => i.start_min)) / 60) + 1);
    let hours = "";
    for (let h = first; h <= last; h++) hours += '<div class="sched-hour"><span>' + String(h).padStart(2, "0") + ":00</span></div>";
    const sorted = it.slice().sort((a, b) => a.start_min - b.start_min), lanes = [];
    sorted.forEach((i) => { let l = 0; while (lanes[l] != null && i.start_min - lanes[l] < 26) l++; lanes[l] = i.start_min; i._lane = l; });
    const maxLane = Math.max(...sorted.map((i) => i._lane)) + 1;
    const slots = sorted.map((i) => {
      const top = (i.start_min - first * 60) / 60 * H - 1, w = 100 / maxLane;
      return '<div class="sched-slot ' + i.status + '" style="top:' + top.toFixed(1) + "px;left:calc(" + (i._lane * w).toFixed(2) + "% + 10px);right:auto;width:calc(" + w.toFixed(2) + '% - 16px)"><b>' +
        esc(i.id) + '</b><span style="overflow:hidden;text-overflow:ellipsis">' + esc(i.pipe_name) + "</span>" + TB[i.status] + "</div>";
    }).join("");
    const nowTop = (S.today.now_min - first * 60) / 60 * H;
    const nowLine = (S.today.now_min >= first * 60 && S.today.now_min <= (last + 1) * 60) ? '<div class="sched-now" style="top:' + nowTop.toFixed(1) + 'px"></div>' : "";
    $("#tvBody").innerHTML = '<div class="card"><div class="sched-grid">' + hours + slots + nowLine + "</div></div>";
  }

  function renderList() {
    const groups = [["Due now", ["due", "overdue"]], ["Running", ["running"]], ["Missed window (earlier run pending)", ["waiting"]],
      ["Upcoming", ["upcoming"]], ["Completed", ["completed"]]];
    let html = "";
    groups.forEach(([name, sts]) => {
      const g = titems().filter((i) => sts.includes(i.status));
      if (!g.length) return;
      html += '<div class="sec-head"><h3>' + name + "</h3><span>" + g.length + '</span></div><div class="card" style="padding:6px">' +
        g.map((i) => '<div class="tl-item ' + i.status + '"><span class="tl-time">' + esc((i.window || "—").split("-")[0]) + '</span><span class="tl-rail"><span class="tl-dot"></span></span>' +
          '<span class="tl-main"><span class="tl-id">' + esc(i.id) + '</span><span class="tl-name">' + esc(i.pipe_name) + (i.window ? " · window " + esc(i.window) : "") + "</span></span>" + TB[i.status] + "</div>").join("") + "</div>";
    });
    $("#tvBody").innerHTML = html || '<div class="card"><div class="bm-empty">Nothing scheduled.</div></div>';
  }

  /* ═══════════════════════ page registration ═══════════════════════════ */
  RMS.pages = RMS.pages || {};
  RMS.pages.scheduler = {
    title: "Scheduler", crumb: "Automation Center — create, run and monitor scheduled processes",
    mount(el) {
      if (!booted) {
        el.innerHTML = shell();
        RMS.mountIcons(el);
        $("#scTabs").querySelectorAll("button").forEach((b) => { b.onclick = () => setTab(b.dataset.t); });
        $("#scRefresh").onclick = load;
        // Auto-refresh: every 3 s while something is running (so Running → Success/Failed appears by itself),
        // every 15 s otherwise.  Never while the editor is open or while typing in the search box.
        let lastLoad = 0;
        RMS.poll(() => {
          if (ED || !document.getElementById("scBody")) return;
          const a = document.activeElement;
          if (a && a.id === "scSearch") return;
          const busy = (S.schedules || []).some((s) => s.display_status === "running") ||
                       (S.hist || []).some((h) => h.status === "RUNNING") || Date.now() < (S.fastUntil || 0);
          if (Date.now() - lastLoad >= (busy ? 3000 : 15000)) { lastLoad = Date.now(); load(); }
        }, 1000);
        booted = true;
      } else {
        // Coming back to the Scheduler always starts on the Schedules tab, not the last one visited.
        S.tab = "schedules"; S.pw = "";
        const tabs = $("#scTabs");
        if (tabs) tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.t === "schedules"));
        if (S.loaded.schedules) render();
        load();
      }
    },
    // Clicking "Scheduler" in the side menu while already here: refresh and go back to the first tab (Schedules).
    reselect(el) {
      S.filter = "all"; S.q = "";
      this.mount(el);
      const body = $("#scBody"); if (body) body.scrollTop = 0;
    },
  };
})();
