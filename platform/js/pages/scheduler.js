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
              today: null, tview: "timeline", tpipes: new Set(["ctcl", "sa", "ms15", "cli"]), err: "" };
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
      '<div class="seg" id="scTabs"><button data-t="schedules" class="on">Schedules</button><button data-t="history">History</button>' +
      '<button data-t="audit">Audit</button><button data-t="today">Today</button></div>' +
      '<div class="spacer"></div><span class="card-sub" id="scMeta"></span>' +
      '<button class="btn sm ghost" id="scRefresh">' + RMS.icon("refresh") + "</button></div></div>" +
      '<div id="scBody"></div>';
  }

  function setTab(t) {
    S.tab = t;
    $("#scTabs").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.t === t));
    load();
  }

  /* ── data loading ─────────────────────────────────────────────────────── */
  async function load() {
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
      } else if (S.tab === "audit") {
        S.audit = (await jget("/sched/audit")).items || [];
      } else {
        S.today = await RMS.api("/ops/timeline");
      }
    } catch (e) { S.err = e.message || String(e); }
    render();
  }

  function render() {
    if (!$("#scBody")) return;
    $("#scMeta").textContent = "live · " + new Date().toTimeString().slice(0, 5);
    if (S.err && S.tab !== "today") {
      $("#scBody").innerHTML = '<div class="card"><div class="bm-empty" style="color:var(--err)">' + esc(S.err) + "</div></div>";
      return;
    }
    ({ schedules: renderSchedules, history: renderHistory, audit: renderAudit, today: renderToday })[S.tab]();
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
    const rows = S.schedules.filter(matches).map((s) => {
      const paused = s.status === "paused", off = s.status === "disabled";
      return "<tr data-id='" + s.id + "'><td><b>" + esc(s.name) + "</b>" + (s.description ? "<div class='card-sub'>" + esc(s.description) + "</div>" : "") + "</td>" +
        "<td>" + esc(s.process_name) + (s.process_missing ? " <span class='badge err'>missing</span>" : "") + "</td>" +
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
      "<th>Schedule Name</th><th>Process</th><th>Trigger</th><th>Days</th><th>Next Run</th><th>Last Run</th><th>Last Result</th><th>Duration</th><th>Status</th><th>Actions</th>" +
      "</tr></thead><tbody>" + (rows || "<tr><td colspan='10'><div class='bm-empty'>" +
        (S.schedules.length ? "No schedules match this filter." :
          (S.procs.length ? "No schedules yet — click “Create Schedule”." :
            "No schedulable processes are registered yet. Processes are added to <code>core/scheduler/processes/</code>; once one exists you can schedule it here.")) +
        "</div></td></tr>") + "</tbody></table></div></div>";
    $("#scBody").querySelectorAll(".chip").forEach((c) => { c.onclick = () => { S.filter = c.dataset.f; renderSchedules(); }; });
    const se = $("#scSearch");
    se.oninput = () => { S.q = se.value; const pos = se.selectionStart; renderSchedules(); const n = $("#scSearch"); n.focus(); n.setSelectionRange(pos, pos); };
    $("#scCreate").onclick = () => openEditor(null);
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
        if (!confirm("Run “" + s.name + "” now?\n\nThe normal schedule is not changed.")) return;
        await call("POST", "/sched/schedules/" + id + "/run-now");
        RMS.toast("ok", "Started", s.name);
      } else {
        await call("POST", "/sched/schedules/" + id + "/" + a);
        RMS.toast("ok", { pause: "Paused", resume: "Resumed", enable: "Enabled", disable: "Disabled" }[a] || "Done", s.name);
      }
      load();
    } catch (e) { RMS.toast("err", "Not done", e.message); load(); }
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
        if (m === "delete" && !confirm("Delete “" + s.name + "” permanently?\nIts run history is kept; the configuration cannot be recovered.")) return;
        try {
          if (m === "delete") await call("DELETE", "/sched/schedules/" + s.id);
          else await call("POST", "/sched/schedules/" + s.id + "/disable");
          close(); RMS.toast("ok", m === "delete" ? "Deleted" : "Disabled", s.name); load();
        } catch (e) { RMS.toast("err", "Not done", e.message); }
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
    return '<div style="margin-bottom:12px"><div class="card-sub" style="margin-bottom:4px;font-weight:700">' + label + "</div>" + html +
      (hint ? '<div class="card-sub" style="margin-top:3px">' + hint + "</div>" : "") + "</div>";
  }
  const optHtml = (arr, sel) => arr.map(([v, l]) => '<option value="' + esc(v) + '"' + (String(v) === String(sel) ? " selected" : "") + ">" + esc(l) + "</option>").join("");

  function openEditor(s) {
    const d = s ? JSON.parse(JSON.stringify(s)) : blank();
    d.retry = Object.assign({ enabled: false, max: 3, delay_min: 5, backoff: false }, d.retry || {});
    if (d.rule && d.rule.type === "interval" && !d.rule.days) d.rule.days = null;
    ED = { id: s ? s.id : null, draft: d, timer: null };
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
        ["monthly", "Monthly"], ["once", "One-time dates (run exactly once each)"]], d.rule.type) + "</select>") +
      '<div id="edWhen"></div>' +
      '<div class="card-sub" style="font-weight:800;margin:14px 0 8px">TIME SETTINGS</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px">' +
      field("Start date", '<input type="date" class="f-input" id="edSd" value="' + esc(d.start_date || "") + '">') +
      field("End date (optional)", '<input type="date" class="f-input" id="edEd" value="' + esc(d.end_date || "") + '">') +
      field("Timezone", '<input class="f-input" id="edTz" list="edTzList" value="' + esc(d.tz) + '"><datalist id="edTzList">' +
        TZS.map((z) => '<option value="' + z + '">').join("") + "</datalist>") + "</div>" +
      '<label style="font-size:12.5px"><input type="checkbox" id="edHol"' + (d.skip_holidays ? " checked" : "") + '> Skip market holidays (uses the shared holiday list)</label>' +
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
    $("#edParams").innerHTML = p && p.params.length ? '<div class="card-sub" style="font-weight:800;margin:6px 0">PROCESS PARAMETERS</div>' +
      p.params.map((q) => {
        const v = cur[q.name] != null ? cur[q.name] : q.default;
        const id = "edp_" + q.name;
        let inp;
        if (q.type === "bool") inp = '<input type="checkbox" id="' + id + '" data-p="' + q.name + '"' + (v ? " checked" : "") + ">";
        else if (q.type === "choice") inp = '<select class="f-select" id="' + id + '" data-p="' + q.name + '">' + optHtml((q.choices || []).map((c) => [c, c]), v) + "</select>";
        else if (q.type === "text") inp = '<textarea class="f-input" id="' + id + '" data-p="' + q.name + '" rows="3" style="width:100%">' + esc(v == null ? "" : v) + "</textarea>";
        else inp = '<input class="f-input" id="' + id + '" data-p="' + q.name + '" type="' + (q.type === "int" ? "number" : "text") + '" value="' + esc(v == null ? "" : v) + '" style="width:100%">';
        return field(esc(q.label) + (q.required ? " *" : ""), inp, esc(q.help || ""));
      }).join("") : "";
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

  function renderWhen(r) {
    const t = r.type || "times", box = $("#edWhen");
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
      if (ED.id) await call("PUT", "/sched/schedules/" + ED.id, body); else await call("POST", "/sched/schedules", body);
      ED.close(); ED = null;
      RMS.toast("ok", "Saved", body.name);
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
    } catch (e) { RMS.toast("err", "Could not open", e.message); }
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
        RMS.poll(() => { if (!ED && document.getElementById("scBody")) load(); }, 15000);
        booted = true;
      } else load();
    },
  };
})();
