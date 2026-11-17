/* pages/tracker.js - WORK TRACKER: tasks you hand out (console work or anything else), a weekly diary, follow-ups that wait on a reply,
   comments, pictures and a history. Backend: core/tracker.py, routes /tracker/*. Views: My work | Board | Diary | All tasks. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  const S = { boot: null, tasks: [], summary: {}, view: "mine", q: "", who: "", kpi: "", weekStart: null, win: null, winClose: null, ready: false };
  const COLS = [["open", "Open", "var(--text-3)"], ["in_progress", "In progress", "var(--info)"], ["waiting", "Waiting for reply", "var(--warn)"],
                ["review", "Review", "#c084fc"], ["done", "Done", "var(--ok)"]];
  const PRI = { urgent: "Urgent", high: "High", normal: "Normal", low: "Low" };
  const KIND = { task: "Task", followup: "Follow-up", change: "Change", bug: "Bug", note: "Private note" };

  // ───────────────────────── helpers ─────────────────────────
  async function api(path, body) {
    try {
      const r = await fetch(path, body === undefined ? { credentials: "same-origin" } :
        { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (r.status === 401) return { ok: false, error: "Your session has ended. Sign in again." };
      return await r.json();
    } catch (e) { return { ok: false, error: "Could not reach the server." }; }
  }
  const hue = (s) => { let h = 0; for (const c of String(s || "?")) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
  const initials = (n) => String(n || "?").trim().split(/\s+/).slice(0, 2).map((x) => x[0]).join("").toUpperCase();
  const av = (name, big) => '<span class="wt-av' + (big ? " lg" : "") + '" style="--h:' + hue(name) + '" title="' + esc(name) + '">' + esc(initials(name)) + "</span>";
  const iso = (d) => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  const todayIso = () => iso(new Date());
  const parse = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  function dueText(t) {
    if (!t.due) return "";
    const a = parse(t.due), b = parse(todayIso()), n = Math.round((a - b) / 864e5);
    if (n === 0) return "Today";
    if (n === 1) return "Tomorrow";
    if (n === -1) return "Yesterday";
    return a.getDate() + " " + MON[a.getMonth()] + (n < -1 ? " · " + (-n) + "d late" : "");
  }
  const when = (s) => { const d = new Date(s); return isNaN(d) ? "" : d.getDate() + " " + MON[d.getMonth()] + ", " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };
  const open_ = (t) => t.status !== "done" && t.status !== "cancelled";
  const me = () => (S.boot && S.boot.me) || { id: "", name: "", manager: false };
  // In-console confirmation (never the browser's confirm box). Replaces the open window; "No" brings the task back.
  function ask(title, text, yesLabel, onYes, onNo) {
    const close = RMS.modal(title, '<div style="font-size:13px;line-height:1.6;margin-bottom:18px">' + text + "</div>" +
      '<div style="display:flex;justify-content:flex-end;gap:10px"><button class="btn ghost" id="wtNo">Cancel</button><button class="btn primary" id="wtYes" style="background:var(--err);border-color:var(--err)">' + esc(yesLabel) + "</button></div>",
      { width: 460, icon: "alert" });
    $("#wtNo").onclick = () => { close(); if (onNo) onNo(); };
    $("#wtYes").onclick = () => { close(); onYes(); };
  }
  function ok(msg) { RMS.toast("ok", msg); }
  function bad(msg) { RMS.toast("err", "Not saved", msg); }

  function pills(t) {
    let h = "";
    if (t.overdue) h += '<span class="wt-pill late">' + RMS.icon("alert") + dueText(t) + "</span>";
    else if (t.due_today) h += '<span class="wt-pill today">Today</span>';
    else if (t.due) h += '<span class="wt-pill">' + RMS.icon("calendar") + dueText(t) + "</span>";
    if (t.priority === "urgent" || t.priority === "high") h += '<span class="wt-pill p-' + t.priority + '">' + PRI[t.priority] + "</span>";
    if (t.status === "waiting") h += '<span class="wt-pill s-waiting">' + (t.waiting_days != null ? "No reply · " + t.waiting_days + "d" : "Waiting") + "</span>";
    return h;
  }
  function card(t, opts) {
    opts = opts || {};
    const sub = t.status === "waiting" && t.waiting_on ? '<div class="wt-sub">' + RMS.icon("mail") + " waiting on " + esc(t.waiting_on) + "</div>" :
      (t.process ? '<div class="wt-sub">' + RMS.icon("layers") + " " + esc(t.process) + "</div>" : "");
    return '<div class="wt-card pr-' + t.priority + (open_(t) ? "" : " closed") + '" data-id="' + t.id + '"' + (opts.drag ? ' draggable="true"' : "") + ">" +
      '<div class="t"><span class="k">' + esc(t.key) + "</span>" + (t.private ? RMS.icon("eye") + " " : "") + esc(t.title) + "</div>" + sub +
      '<div class="m">' + pills(t) + '<div class="r">' +
      (t.comments ? RMS.icon("file") + t.comments + " " : "") + (t.files ? "📎" + t.files + " " : "") +
      (opts.noAv ? "" : av(t.assignee_name || t.assignee)) + "</div></div></div>";
  }
  function wire(root) {
    root.querySelectorAll(".wt-card").forEach((c) => { c.onclick = () => openTask(+c.dataset.id); });
  }

  // ───────────────────────── data ─────────────────────────
  async function load(first) {
    const d = await api(first ? "/tracker/bootstrap" : "/tracker/tasks");
    if (!d.ok) {
      $("#trkBody").innerHTML = '<div class="card" style="max-width:560px;margin:40px auto;text-align:center"><div class="card-title" style="justify-content:center;color:var(--err)">' +
        RMS.icon("alert") + " Work Tracker could not load</div><p class='card-sub' style='margin:10px 0 14px'>" + esc(d.error || "Unknown error") +
        '</p><button class="btn primary" id="trkRetry">Try again</button></div>';
      $("#trkRetry").onclick = () => load(first);
      return;
    }
    if (first) S.boot = d;
    S.tasks = d.tasks; S.summary = d.summary || {}; S.ready = true;
    navBadge(); render();
  }
  function reload() { return load(false); }

  function visible() {
    const q = S.q.trim().toLowerCase();
    return S.tasks.filter((t) => {
      if (S.who && t.assignee !== S.who) return false;
      if (q && !(t.title + " " + (t.description || "") + " " + t.key + " " + (t.waiting_on || "") + " " + (t.process || "") + " " + (t.assignee_name || "")).toLowerCase().includes(q)) return false;
      return true;
    });
  }
  const KP = {
    mine: (t) => t.mine && open_(t), overdue: (t) => t.mine && t.overdue, today: (t) => t.mine && t.due_today,
    waiting: (t) => t.status === "waiting", review: (t) => t.status === "review" && t.created_by === me().id,
  };

  // ───────────────────────── page shell ─────────────────────────
  function shell() {
    return '<div id="trkKpis"></div>' +
      '<div class="wt-bar"><div class="seg" id="trkViews">' +
      [["mine", "My work"], ["board", "Board"], ["diary", "Diary"], ["all", "All tasks"]].map((v) => '<button data-v="' + v[0] + '">' + v[1] + "</button>").join("") + "</div>" +
      '<input class="wt-in" id="trkQ" placeholder="Search tasks…" style="width:220px">' +
      '<select class="wt-sel" id="trkWho"></select><span id="trkKchip"></span><div class="spacer"></div>' +
      '<button class="btn ghost" id="trkPeople" title="Mobile, e-mail and reminder settings">' + RMS.icon("bell") + " People and alerts</button>" +
      '<button class="btn primary" id="trkNew"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg> New task</button></div>' +
      '<div id="trkBody"><div class="card-sub" style="padding:40px;text-align:center">Loading…</div></div>';
  }

  function renderKpis() {
    const s = S.summary, mgr = me().manager;
    const k = [["mine", "My open tasks", s.mine, "var(--accent)", "assigned to me"], ["overdue", "Overdue", s.overdue, "var(--err)", "mine, past the due date"],
      ["today", "Due today", s.today, "var(--warn)", "mine"], ["waiting", "Waiting for reply", s.waiting, "#f59e0b", mgr ? "across the team" : "visible to me"],
      ["review", "To review", s.review, "#c084fc", "done by others, waiting for me"]];
    $("#trkKpis").innerHTML = '<div class="wt-kpis">' + k.map((x) => '<div class="card wt-kpi' + (S.kpi === x[0] ? " on" : "") + '" data-k="' + x[0] + '"><div class="l"><i style="--c:' + x[3] + '"></i>' +
      x[1] + '</div><div class="n" style="' + (x[0] === "overdue" && x[2] ? "color:var(--err)" : "") + '">' + (x[2] || 0) + '</div><div class="s">' + x[4] + "</div></div>").join("") + "</div>";
    $("#trkKpis").querySelectorAll(".wt-kpi").forEach((c) => {
      c.onclick = () => { S.kpi = S.kpi === c.dataset.k ? "" : c.dataset.k; if (S.kpi) S.view = "all"; render(); };
    });
  }

  function render() {
    if (!$("#trkBody")) return;
    renderKpis();
    document.querySelectorAll("#trkViews button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.view));
    const sel = $("#trkWho");
    if (sel) {
      sel.innerHTML = '<option value="">Everyone</option>' + ((S.boot && S.boot.people) || []).map((p) => '<option value="' + esc(p.id) + '"' + (S.who === p.id ? " selected" : "") + ">" + esc(p.name) + "</option>").join("");
      sel.style.display = me().manager || S.view === "board" ? "" : "none";
    }
    const kc = $("#trkKchip");
    if (kc) {
      const lab = { mine: "My open tasks", overdue: "Overdue", today: "Due today", waiting: "Waiting for reply", review: "To review" }[S.kpi];
      kc.innerHTML = S.kpi ? '<span class="wt-pill today">' + lab + ' <span style="cursor:pointer" id="trkKx">✕</span></span>' : "";
      if (S.kpi) $("#trkKx").onclick = () => { S.kpi = ""; render(); };
    }
    ({ mine: viewMine, board: viewBoard, diary: viewDiary, all: viewAll }[S.view] || viewMine)();
  }

  // ───────────────────────── views ─────────────────────────
  function group(title, color, list, extra) {
    if (!list.length) return "";
    return '<div class="wt-group"><h4><i style="width:8px;height:8px;border-radius:50%;background:' + color + ';display:inline-block"></i>' + title + " <b>" + list.length + "</b></h4>" +
      list.map((t) => card(t, extra)).join("") + "</div>";
  }
  function viewMine() {
    const v = visible(), mine = v.filter((t) => t.mine && open_(t)), t0 = todayIso();
    const by = (f) => mine.filter(f);
    const overdue = by((t) => t.overdue), today = by((t) => t.due_today && !t.overdue), waiting = by((t) => t.status === "waiting" && !t.overdue && !t.due_today);
    const rest = by((t) => !t.overdue && !t.due_today && t.status !== "waiting");
    const prog = rest.filter((t) => t.status === "in_progress"), later = rest.filter((t) => t.status !== "in_progress");
    const left = group("Overdue", "var(--err)", overdue) + group("Due today", "var(--warn)", today) + group("Waiting for a reply", "#f59e0b", waiting) +
      group("In progress", "var(--info)", prog) + group("Open", "var(--text-3)", later);
    const given = v.filter((t) => t.created_by === me().id && !t.mine && open_(t));
    const right = group("I assigned - still open", "var(--accent)", given.filter((t) => t.status !== "review")) + group("Waiting for my review", "#c084fc", given.filter((t) => t.status === "review"));
    const done = v.filter((t) => t.mine && !open_(t)).slice(0, 5);
    $("#trkBody").innerHTML = '<div class="wt-two"><div>' + (left || empty("Nothing on your plate", "Tasks assigned to you will show up here. Add your own with New task.")) +
      (done.length ? group("Recently done", "var(--ok)", done) : "") + "</div><div>" +
      (right || empty("You have not handed out any open task", "Assign a task to a colleague and follow it from here.")) + "</div></div>";
    wire($("#trkBody"));
  }
  function empty(h, p) { return '<div class="wt-empty"><b style="display:block;margin-bottom:4px;color:var(--text-2)">' + esc(h) + "</b>" + esc(p) + "</div>"; }

  function viewBoard() {
    const v = visible().filter((t) => !S.kpi || KP[S.kpi](t));
    $("#trkBody").innerHTML = '<div class="wt-board">' + COLS.map((c) => {
      const l = v.filter((t) => t.status === c[0]);
      return '<div class="wt-col" data-s="' + c[0] + '"><h4><i style="--c:' + c[2] + '"></i>' + c[1] + "<b>" + l.length + "</b></h4>" +
        (l.length ? l.map((t) => card(t, { drag: t.can_edit || t.mine })).join("") : '<div class="wt-empty">Drop a task here</div>') + "</div>";
    }).join("") + "</div>";
    wire($("#trkBody"));
    let dragId = null;
    $("#trkBody").querySelectorAll(".wt-card[draggable]").forEach((c) => {
      c.ondragstart = (e) => { dragId = +c.dataset.id; c.classList.add("dragging"); try { e.dataTransfer.setData("text/plain", String(dragId)); } catch (x) { /* old browser */ } };
      c.ondragend = () => c.classList.remove("dragging");
    });
    $("#trkBody").querySelectorAll(".wt-col").forEach((col) => {
      col.ondragover = (e) => { e.preventDefault(); col.classList.add("over"); };
      col.ondragleave = () => col.classList.remove("over");
      col.ondrop = async (e) => {
        e.preventDefault(); col.classList.remove("over");
        const t = S.tasks.find((x) => x.id === dragId); if (!t || t.status === col.dataset.s) return;
        const r = await api("/tracker/task/" + t.id, { status: col.dataset.s });
        if (r.ok) { ok(t.key + " moved to " + col.querySelector("h4").childNodes[1].textContent); reload(); } else bad(r.error);
      };
    });
  }

  function weekStart(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }
  function viewDiary() {
    if (!S.weekStart) S.weekStart = weekStart(new Date());
    const v = visible().filter((t) => !S.kpi || KP[S.kpi](t)), ws = S.weekStart, days = [];
    for (let i = 0; i < 7; i++) { const d = new Date(ws); d.setDate(ws.getDate() + i); days.push(d); }
    const t0 = todayIso(), end = new Date(ws); end.setDate(ws.getDate() + 6);
    const overdue = v.filter((t) => t.overdue), nodate = v.filter((t) => !t.due && open_(t) && t.mine);
    $("#trkBody").innerHTML =
      '<div class="wt-bar" style="margin-bottom:10px"><button class="btn sm" id="dPrev">‹</button><button class="btn sm" id="dToday">This week</button><button class="btn sm" id="dNext">›</button>' +
      '<b style="font-size:13px">' + ws.getDate() + " " + MON[ws.getMonth()] + " – " + end.getDate() + " " + MON[end.getMonth()] + " " + end.getFullYear() + "</b>" +
      '<span class="card-sub">Type under a day and press Enter to add a task for that day.</span></div>' +
      (overdue.length ? '<div class="wt-group" style="margin-bottom:12px"><h4 style="color:var(--err)">' + RMS.icon("alert") + " Overdue <b>" + overdue.length + '</b></h4><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px">' +
        overdue.map((t) => card(t)).join("") + "</div></div>" : "") +
      '<div class="wt-week">' + days.map((d) => {
        const k = iso(d), l = v.filter((t) => t.due === k);
        return '<div class="wt-day' + (k === t0 ? " today" : "") + (k < t0 ? " past" : "") + '"><div class="dh"><b>' + d.getDate() + "</b><span>" + DOW[d.getDay()] + "</span><em>" + (l.length || "") + "</em></div>" +
          l.map((t) => card(t, { noAv: !me().manager && t.mine })).join("") +
          '<div class="add"><input data-d="' + k + '" placeholder="+ add task" maxlength="200"></div></div>';
      }).join("") + "</div>" +
      (nodate.length ? '<div class="wt-group" style="margin-top:16px"><h4>No date <b>' + nodate.length + '</b></h4><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px">' + nodate.map((t) => card(t)).join("") + "</div></div>" : "");
    wire($("#trkBody"));
    $("#dPrev").onclick = () => { S.weekStart.setDate(S.weekStart.getDate() - 7); viewDiary(); };
    $("#dNext").onclick = () => { S.weekStart.setDate(S.weekStart.getDate() + 7); viewDiary(); };
    $("#dToday").onclick = () => { S.weekStart = weekStart(new Date()); viewDiary(); };
    $("#trkBody").querySelectorAll(".add input").forEach((i) => {
      i.onkeydown = async (e) => {
        if (e.key !== "Enter" || !i.value.trim()) return;
        const r = await api("/tracker/task", { title: i.value.trim(), due: i.dataset.d, kind: "task" });
        if (r.ok) { ok("Added " + r.task.key); reload(); } else bad(r.error);
      };
    });
  }

  function viewAll() {
    const v = visible().filter((t) => !S.kpi || KP[S.kpi](t));
    const ord = { urgent: 0, high: 1, normal: 2, low: 3 };
    v.sort((a, b) => (open_(b) - open_(a)) || ((a.due || "9999") < (b.due || "9999") ? -1 : 1) || (ord[a.priority] - ord[b.priority]));
    $("#trkBody").innerHTML = '<div class="card" style="padding:0;overflow:hidden"><div style="max-height:calc(100vh - 380px);overflow:auto"><table class="htable"><thead><tr><th>Task</th><th>Assigned to</th><th>Status</th><th>Priority</th><th>Due</th><th></th></tr></thead><tbody>' +
      (v.length ? v.map((t) => '<tr data-id="' + t.id + '"><td><span class="mono" style="color:var(--text-3);margin-right:6px">' + esc(t.key) + "</span><b>" + esc(t.title) + "</b>" +
        (t.status === "waiting" && t.waiting_on ? '<div class="wt-sub">waiting on ' + esc(t.waiting_on) + "</div>" : "") + "</td>" +
        '<td><div style="display:flex;gap:7px;align-items:center">' + av(t.assignee_name || t.assignee) + esc(t.assignee_name || t.assignee) + "</div></td>" +
        '<td><span class="wt-pill s-' + t.status + '">' + esc(t.status_label) + '</span></td><td><span class="wt-pill p-' + t.priority + '">' + PRI[t.priority] + "</span></td>" +
        '<td style="white-space:nowrap">' + (t.overdue ? '<span class="wt-pill late">' + dueText(t) + "</span>" : esc(dueText(t)) || "-") + '</td><td style="color:var(--text-3)">' + (t.comments ? RMS.icon("file") + t.comments + " " : "") + (t.files ? "📎" + t.files : "") + "</td></tr>").join("")
        : '<tr><td colspan="6" style="padding:30px;text-align:center;color:var(--text-3)">No task matches.</td></tr>') + "</tbody></table></div></div>";
    $("#trkBody").querySelectorAll("tbody tr[data-id]").forEach((r) => { r.onclick = () => openTask(+r.dataset.id); });
  }

  // ───────────────────────── new task ─────────────────────────
  function peopleOpts(sel) { return ((S.boot && S.boot.people) || []).map((p) => '<option value="' + esc(p.id) + '"' + (p.id === sel ? " selected" : "") + ">" + esc(p.name) + "</option>").join(""); }
  function procList() { return '<datalist id="wtProcs">' + ((S.boot && S.boot.processes) || []).map((p) => '<option value="' + esc(p) + '">').join("") + "</datalist>"; }

  function newTask(pre) {
    pre = pre || {};
    let kind = pre.kind || "task";
    const close = RMS.modal("New task",
      '<div class="wt-seg" id="ntKind" style="margin-bottom:14px">' + ["task", "followup", "change", "note"].map((k) => '<button data-k="' + k + '"' + (k === kind ? ' class="on"' : "") + ">" + KIND[k] + "</button>").join("") + "</div>" +
      '<div class="wt-lbl">What needs to be done</div><input class="wt-in" id="ntTitle" style="width:100%;margin-bottom:12px" maxlength="200" placeholder="Send the ledger query to the Ludhiana branch" value="' + esc(pre.title || "") + '">' +
      '<div class="wt-lbl">Details (optional)</div><textarea class="wt-ta" id="ntDesc" placeholder="Anything the person should know"></textarea>' +
      '<div class="wt-grid2" style="margin-top:12px"><div><div class="wt-lbl">Assign to</div><select class="wt-sel" id="ntWho" style="width:100%">' + peopleOpts(pre.assignee || me().id) + "</select></div>" +
      '<div><div class="wt-lbl">Due date</div><input type="date" class="wt-in" id="ntDue" style="width:100%" value="' + esc(pre.due || "") + '"></div>' +
      '<div><div class="wt-lbl">Priority</div><select class="wt-sel" id="ntPri" style="width:100%">' + ["normal", "low", "high", "urgent"].map((p) => '<option value="' + p + '">' + PRI[p] + "</option>").join("") + "</select></div>" +
      '<div><div class="wt-lbl">Linked process (optional)</div><input class="wt-in" id="ntProc" list="wtProcs" style="width:100%" placeholder="e.g. Margin Shortage">' + procList() + "</div></div>" +
      '<div id="ntFollow" style="display:none;margin-top:12px" class="wt-grid2"><div><div class="wt-lbl">Waiting for a reply from</div><input class="wt-in" id="ntWait" style="width:100%" placeholder="Exchange helpdesk, branch manager…"></div>' +
      '<div><div class="wt-lbl">Remind me every (days)</div><input type="number" min="1" max="60" class="wt-in" id="ntDays" style="width:100%" value="2"></div></div>' +
      '<div id="ntNote" class="card-sub" style="display:none;margin-top:10px">A private note is visible to you only, even managers cannot see it.</div>' +
      '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:18px"><button class="btn ghost" id="ntCancel">Cancel</button><button class="btn primary" id="ntSave">Create task</button></div>',
      { width: 600, icon: "check" });
    const sync = () => {
      $("#ntFollow").style.display = kind === "followup" ? "grid" : "none";
      $("#ntNote").style.display = kind === "note" ? "" : "none";
      $("#ntWho").disabled = kind === "note"; if (kind === "note") $("#ntWho").value = me().id;
      document.querySelectorAll("#ntKind button").forEach((b) => b.classList.toggle("on", b.dataset.k === kind));
    };
    document.querySelectorAll("#ntKind button").forEach((b) => { b.onclick = () => { kind = b.dataset.k; sync(); }; });
    sync(); $("#ntTitle").focus();
    $("#ntCancel").onclick = close;
    $("#ntSave").onclick = async () => {
      const btn = $("#ntSave"); btn.disabled = true; btn.textContent = "Creating…";
      const body = { title: $("#ntTitle").value, description: $("#ntDesc").value, assignee: $("#ntWho").value, due: $("#ntDue").value, priority: $("#ntPri").value,
        kind, process: $("#ntProc").value, private: kind === "note" };
      if (kind === "followup") { body.waiting_on = $("#ntWait").value; body.follow_up_days = $("#ntDays").value; }
      const r = await api("/tracker/task", body);
      btn.disabled = false; btn.textContent = "Create task";
      if (!r.ok) { bad(r.error); return; }
      close(); ok(r.task.key + " created" + (r.task.assignee !== me().id ? " and assigned to " + r.task.assignee_name : "")); await reload(); openTask(r.task.id);
    };
  }

  // ───────────────────────── task window ─────────────────────────
  async function openTask(id) {
    const r = await api("/tracker/task/" + id);
    if (!r.ok) { bad(r.error); return; }
    S.win = r.task;
    if (S.winClose) { try { S.winClose(); } catch (e) { /* closed */ } }
    S.winClose = RMS.modal(r.task.key, '<div class="wt-win" id="wtWin"></div>', { raw: true, width: 980, icon: "check" });
    paintTask();
  }
  function selOpts(list, cur) { return list.map((o) => '<option value="' + o[0] + '"' + (o[0] === cur ? " selected" : "") + ">" + esc(o[1]) + "</option>").join(""); }
  function paintTask() {
    const t = S.win, el = $("#wtWin"); if (!el || !t) return;
    const edit = t.can_edit, canSt = t.can_edit || t.mine;
    const acts = t.activity.map((a) => a.kind === "comment" ?
      '<div class="wt-act">' + av(a.by_name, true) + '<div class="b"><small>' + esc(a.by_name) + " · " + when(a.at) + "</small>" + esc(a.body) + "</div></div>" :
      '<div class="wt-act ev">' + esc(a.body) + " · " + esc(a.by_name) + ", " + when(a.at) + "</div>").join("");
    const files = t.file_list.map((f) => '<a class="wt-file" href="/tracker/file/' + f.id + '" target="_blank" title="' + esc(f.name + " · " + f.by_name) + '">' +
      (f.is_image ? '<img src="/tracker/file/' + f.id + '" alt="' + esc(f.name) + '">' : esc(f.name)) + '<span class="x" data-del="' + f.id + '" title="Remove">✕</span></a>').join("");
    const follow = t.status === "waiting" || t.kind === "followup";
    el.innerHTML = '<div class="wt-main">' +
      '<input class="wt-title" id="wtTitle" value="' + esc(t.title) + '"' + (edit ? "" : " readonly") + ' maxlength="200">' +
      '<div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap"><span class="wt-pill s-' + t.status + '">' + esc(t.status_label) + '</span><span class="wt-pill">' + esc(KIND[t.kind] || t.kind) + "</span>" + pills(t) +
      '<span class="card-sub">Created by ' + esc(t.created_by_name) + ", " + when(t.created_at) + "</span></div>" +
      (t.status === "waiting" ? '<div class="wt-warn">' + RMS.icon("mail") + " Waiting for a reply" + (t.waiting_on ? " from <b>" + esc(t.waiting_on) + "</b>" : "") +
        (t.waiting_days != null ? " · " + (t.waiting_days === 0 ? "since today" : t.waiting_days + " day(s) with no reply") : "") +
        (t.follow_up_days ? " · reminder every " + t.follow_up_days + " day(s)" : "") + "</div>" : "") +
      '<div class="wt-lbl">Details</div><textarea class="wt-ta" id="wtDesc"' + (edit ? "" : " readonly") + ' placeholder="No details">' + esc(t.description || "") + "</textarea>" +
      '<div class="wt-lbl" style="margin-top:14px">Pictures and files</div><div class="wt-files">' + files + "</div>" +
      '<div class="wt-drop" id="wtDrop">' + RMS.icon("download") + ' Drop pictures or files here, click to choose, or paste a screenshot (Ctrl+V)</div><input type="file" id="wtPick" multiple style="display:none">' +
      '<div class="wt-lbl" style="margin-top:16px">Comments and history</div>' + acts +
      '<div style="display:flex;gap:8px;margin-top:10px"><textarea class="wt-ta" id="wtCmt" style="min-height:44px" placeholder="Write a comment… (Ctrl+Enter to send)"></textarea><button class="btn primary" id="wtSend" style="align-self:flex-end">Send</button></div></div>' +
      '<div class="wt-side"><div class="f"><div class="wt-lbl">Status</div><select class="wt-sel" id="wtSt"' + (canSt ? "" : " disabled") + ">" + selOpts(S.boot.statuses.map((s) => [s.id, s.label]), t.status) + "</select></div>" +
      '<div class="f"><div class="wt-lbl">Assigned to</div><select class="wt-sel" id="wtWho"' + (edit && !t.private ? "" : " disabled") + ">" + peopleOpts(t.assignee) + "</select></div>" +
      '<div class="f"><div class="wt-lbl">Due date</div><input type="date" class="wt-in" id="wtDue" value="' + esc(t.due || "") + '"' + (edit ? "" : " disabled") + "></div>" +
      '<div class="f"><div class="wt-lbl">Priority</div><select class="wt-sel" id="wtPri"' + (edit ? "" : " disabled") + ">" + selOpts(S.boot.priorities.map((p) => [p, PRI[p]]), t.priority) + "</select></div>" +
      '<div class="f"><div class="wt-lbl">Type</div><select class="wt-sel" id="wtKind"' + (edit ? "" : " disabled") + ">" + selOpts(S.boot.kinds.map((k) => [k, KIND[k]]), t.kind) + "</select></div>" +
      '<div class="f"><div class="wt-lbl">Linked process</div><input class="wt-in" id="wtProc" list="wtProcs" value="' + esc(t.process || "") + '"' + (edit ? "" : " disabled") + ">" + procList() + "</div>" +
      (follow ? '<div class="f"><div class="wt-lbl">Waiting for reply from</div><input class="wt-in" id="wtWait" value="' + esc(t.waiting_on || "") + '"' + (canSt ? "" : " disabled") + "></div>" +
        '<div class="f"><div class="wt-lbl">Remind every (days)</div><input type="number" min="1" max="60" class="wt-in" id="wtDays" value="' + (t.follow_up_days || "") + '"' + (canSt ? "" : " disabled") + "></div>" : "") +
      '<div class="card-sub" style="margin:4px 0 12px">Assigned to ' + esc(t.assignee_name || t.assignee) + "</div>" +
      (t.status !== "done" && canSt ? '<button class="btn primary" id="wtDone" style="width:100%;justify-content:center;margin-bottom:8px">' + RMS.icon("check") + " Mark done</button>" : "") +
      (edit ? '<button class="btn ghost" id="wtDel" style="width:100%;justify-content:center;color:var(--err)">Delete task</button>' : "") + "</div>";
    wireTask();
  }
  async function patch(body, okMsg) {
    const r = await api("/tracker/task/" + S.win.id, body);
    if (!r.ok) { bad(r.error); paintTask(); return; }
    S.win = r.task; paintTask(); if (okMsg) ok(okMsg); reload();
  }
  async function upload(files) {
    if (!files.length) return;
    const drop = $("#wtDrop"); if (drop) drop.textContent = "Uploading " + files.length + " file(s)…";
    const fd = new FormData(); files.forEach((f) => fd.append("files", f, f.name || "screenshot.png"));
    let r;
    try { r = await (await fetch("/tracker/task/" + S.win.id + "/file", { method: "POST", body: fd, credentials: "same-origin" })).json(); } catch (e) { r = { ok: false, error: "Upload failed." }; }
    if (r.task) { S.win = r.task; paintTask(); }
    if (!r.ok || (r.rejected && r.rejected.length)) bad((r.rejected || []).join("; ") || r.error); else ok("Attached " + (r.saved || []).join(", "));
    reload();
  }
  function wireTask() {
    const t = S.win, g = (id) => $("#" + id);
    if (t.can_edit) {
      g("wtTitle").onchange = () => patch({ title: g("wtTitle").value }, "Title saved");
      g("wtDesc").onchange = () => patch({ description: g("wtDesc").value }, "Details saved");
      g("wtWho").onchange = () => patch({ assignee: g("wtWho").value }, "Assigned to " + g("wtWho").selectedOptions[0].textContent);
      g("wtDue").onchange = () => patch({ due: g("wtDue").value }, "Due date saved");
      g("wtPri").onchange = () => patch({ priority: g("wtPri").value }, "Priority saved");
      g("wtKind").onchange = () => patch({ kind: g("wtKind").value }, "Type saved");
      g("wtProc").onchange = () => patch({ process: g("wtProc").value }, "Process linked");
      if (g("wtDel")) g("wtDel").onclick = () => ask("Delete " + t.key + "?", "<b>" + esc(t.title) + "</b><br>The task, its comments and its files will be removed. This cannot be undone.", "Delete task",
        async () => {
          const r = await api("/tracker/task/" + t.id + "/delete", {});
          if (r.ok) { ok(t.key + " deleted"); reload(); } else { bad(r.error); openTask(t.id); }
        }, () => openTask(t.id));
    }
    g("wtSt").onchange = () => {
      const v = g("wtSt").value, b = { status: v };
      if (v === "waiting") b.follow_up_days = t.follow_up_days || 2;
      patch(b, "Status: " + g("wtSt").selectedOptions[0].textContent);
    };
    if (g("wtWait")) {
      g("wtWait").onchange = () => patch({ waiting_on: g("wtWait").value }, "Saved");
      g("wtDays").onchange = () => patch({ follow_up_days: g("wtDays").value || 0 }, "Reminder saved");
    }
    if (g("wtDone")) g("wtDone").onclick = () => patch({ status: "done" }, t.key + " marked done");
    const send = async () => {
      const txt = g("wtCmt").value.trim(); if (!txt) { g("wtCmt").focus(); return; }
      const r = await api("/tracker/task/" + t.id + "/comment", { text: txt });
      if (r.ok) { S.win = r.task; paintTask(); reload(); } else bad(r.error);
    };
    g("wtSend").onclick = send;
    g("wtCmt").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); };
    const pick = g("wtPick"), drop = g("wtDrop");
    drop.onclick = () => pick.click();
    pick.onchange = () => upload(Array.from(pick.files));
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); upload(Array.from(e.dataTransfer.files || [])); };
    g("wtWin").onpaste = (e) => {
      const fs = Array.from((e.clipboardData && e.clipboardData.files) || []).filter((f) => f.type.startsWith("image/"));
      if (fs.length) { e.preventDefault(); upload(fs); }
    };
    el_files_del();
  }
  function el_files_del() {
    document.querySelectorAll("#wtWin [data-del]").forEach((x) => {
      x.onclick = async (e) => {
        e.preventDefault(); e.stopPropagation();
        const tid = S.win.id;
        ask("Remove this file?", "It will be removed from the task.", "Remove", async () => {
          const r = await api("/tracker/file/" + x.dataset.del + "/delete", {});
          if (!r.ok) bad(r.error); else ok("File removed");
          reload(); openTask(tid);
        }, () => openTask(tid));
      };
    });
  }

  // ───────────────────────── people and alerts ─────────────────────────
  function peopleWin() {
    const mgr = me().manager, s = S.boot.settings;
    const rows = S.boot.people.filter((p) => mgr || p.id === me().id).map((p) =>
      '<tr data-id="' + esc(p.id) + '"><td><div style="display:flex;gap:8px;align-items:center">' + av(p.name) + "<div><b>" + esc(p.name) + '</b><div class="wt-sub">' + esc(p.id) + "</div></div></div></td>" +
      '<td><input class="wt-in" data-f="mobile" style="width:130px" placeholder="9876543210" value="' + esc(p.mobile) + '"></td>' +
      '<td><input class="wt-in" data-f="email" style="width:210px" placeholder="name@company.com" value="' + esc(p.email) + '"></td>' +
      "<td>" + (mgr && !p.admin ? '<select class="wt-sel" data-f="role"><option value="member"' + (p.role === "member" ? " selected" : "") + '>Member</option><option value="manager"' + (p.role === "manager" ? " selected" : "") + ">Manager</option></select>" :
        '<span class="wt-pill">' + (p.admin ? "Admin" : p.role === "manager" ? "Manager" : "Member") + "</span>") + '</td><td><button class="btn sm" data-save="' + esc(p.id) + '">Save</button></td></tr>').join("");
    const close = RMS.modal("People and alerts",
      '<div class="card-sub" style="margin-bottom:12px;line-height:1.6">The mobile number and e-mail are where a person gets the WhatsApp picture and the mail when a task is assigned to them, when somebody comments, and in the daily list. ' +
      (mgr ? "Managers see every task that is not a private note." : "You can change your own details here.") + "</div>" +
      '<div style="max-height:46vh;overflow:auto"><table class="htable"><thead><tr><th>Person</th><th>Mobile</th><th>E-mail</th><th>Role</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
      (mgr ? '<div style="border-top:1px solid var(--line);margin-top:16px;padding-top:14px"><div class="wt-lbl">Daily reminder (Monday to Saturday)</div><div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">' +
        '<label style="font-size:12.5px;display:flex;gap:6px;align-items:center"><input type="checkbox" id="stOn"' + (s.enabled ? " checked" : "") + "> Send it</label>" +
        '<label style="font-size:12.5px;display:flex;gap:6px;align-items:center">at <input type="time" class="wt-in" id="stAt" value="' + esc(s.remind_at) + '"></label>' +
        '<label style="font-size:12.5px;display:flex;gap:6px;align-items:center"><input type="checkbox" id="stMgr"' + (s.manager_summary ? " checked" : "") + "> Summary to managers</label>" +
        '<label style="font-size:12.5px;display:flex;gap:6px;align-items:center"><input type="checkbox" id="stWa"' + (s.notify_whatsapp ? " checked" : "") + "> WhatsApp</label>" +
        '<label style="font-size:12.5px;display:flex;gap:6px;align-items:center"><input type="checkbox" id="stMail"' + (s.notify_email ? " checked" : "") + "> E-mail</label>" +
        '<button class="btn primary sm" id="stSave">Save settings</button></div></div>' : ""),
      { width: 820, icon: "bell" });
    document.querySelectorAll("[data-save]").forEach((b) => {
      b.onclick = async () => {
        const tr = b.closest("tr"), f = (n) => { const e = tr.querySelector('[data-f="' + n + '"]'); return e ? e.value : undefined; };
        const r = await api("/tracker/people", { id: b.dataset.save, mobile: f("mobile") || "", email: f("email") || "", role: f("role") || "member" });
        if (r.ok) { S.boot.people = r.people; ok("Saved"); } else bad(r.error);
      };
    });
    if (mgr) $("#stSave").onclick = async () => {
      const r = await api("/tracker/settings", { enabled: $("#stOn").checked, remind_at: $("#stAt").value, manager_summary: $("#stMgr").checked, notify_whatsapp: $("#stWa").checked, notify_email: $("#stMail").checked });
      if (r.ok) { S.boot.settings = r.settings; ok("Settings saved"); close(); } else bad(r.error);
    };
  }

  // ───────────────────────── nav badge (works before the page is opened) ─────────────────────────
  function navBadge() {
    const it = document.querySelector('.nav-item[data-route="tracker"]'); if (!it) return;
    let b = it.querySelector(".nav-count");
    const n = (S.summary.overdue || 0) + (S.summary.today || 0);
    if (!n) { if (b) b.remove(); return; }
    if (!b) { b = document.createElement("em"); b.className = "nav-count"; it.appendChild(b); }
    b.textContent = n; b.classList.toggle("late", !!S.summary.overdue);
    b.title = (S.summary.overdue || 0) + " overdue, " + (S.summary.today || 0) + " due today";
  }
  async function pollBadge() {
    if (document.hidden) return;
    const d = await api("/tracker/summary");
    if (d && d.ok !== false && d.mine !== undefined) { S.summary = Object.assign(S.summary || {}, d); navBadge(); }
  }
  setTimeout(pollBadge, 2500);
  setInterval(pollBadge, 90000);

  // ───────────────────────── page ─────────────────────────
  let booted = false;
  RMS.pages = RMS.pages || {};
  RMS.pages.tracker = {
    title: "Work Tracker", crumb: "Tasks, follow-ups and your diary - nothing gets forgotten.",
    mount(el) {
      if (booted) { reload(); return; }
      el.innerHTML = shell();
      $("#trkNew").onclick = () => newTask();
      $("#trkPeople").onclick = () => { if (S.boot) peopleWin(); };
      $("#trkQ").oninput = (e) => { S.q = e.target.value; render(); };
      $("#trkWho").onchange = (e) => { S.who = e.target.value; render(); };
      document.querySelectorAll("#trkViews button").forEach((b) => { b.onclick = () => { S.view = b.dataset.v; render(); }; });
      booted = true;
      load(true);
    },
  };
  RMS.trackerNew = newTask;
})();
