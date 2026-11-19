/* pages/tracker.js - WORK TRACKER: tasks you hand out (console work or anything else), a weekly diary, follow-ups that wait on a reply,
   comments, pictures and a history. Backend: core/tracker.py, routes /tracker/*. Views: My work | Board | Diary | All tasks. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  const S = { tchip: "", boot: null, tasks: [], summary: {}, view: "mine", q: "", who: "", kpi: "", weekStart: null, win: null, winClose: null, ready: false };
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
      '<div style="display:flex;justify-content:flex-end;gap:10px"><button class="btn ghost" id="wtNo" data-no-busy>Cancel</button><button class="btn primary" id="wtYes" data-no-busy style="background:var(--err);border-color:var(--err)">' + esc(yesLabel) + "</button></div>",
      { width: 460, icon: "alert" });
    $("#wtNo").onclick = () => { close(); if (onNo) onNo(); };
    $("#wtYes").onclick = () => { close(); onYes(); };
  }
  function ok(msg) { RMS.toast("ok", msg); }
  // Visible "done" popup for button actions (centre of the screen, closes by itself after 3 s or on OK)
  function okBox(title, sub) {
    const old = document.getElementById("wtOkBox"); if (old) old.remove();
    const w = document.createElement("div"); w.id = "wtOkBox";
    w.style.cssText = "position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.35)";
    w.innerHTML = '<div style="background:var(--surface);border:1px solid var(--line-2);border-radius:14px;box-shadow:var(--sh-3);padding:24px 30px;min-width:300px;max-width:440px;text-align:center">' +
      '<div style="width:46px;height:46px;border-radius:50%;background:rgba(52,211,153,.15);color:var(--ok);display:flex;align-items:center;justify-content:center;margin:0 auto 12px"><svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></div>' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:4px">' + esc(title) + "</div>" + (sub ? '<div style="font-size:12.5px;color:var(--text-2);margin-bottom:4px">' + esc(sub) + "</div>" : "") +
      '<button class="btn primary" id="wtOkBtn" data-no-busy style="margin-top:14px;min-width:90px;justify-content:center">OK</button></div>';
    document.body.appendChild(w);
    const done = () => { if (w.parentNode) w.remove(); };
    $("#wtOkBtn").onclick = done; w.onclick = (e) => { if (e.target === w) done(); };
    $("#wtOkBtn").focus(); setTimeout(done, 3000);
  }
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
      (t.process ? '<div class="wt-sub">' + RMS.icon("layers") + " " + esc(procLabel(t.process)) + "</div>" : "");
    // who it is between: "from Abhishek" (given to me) / "to Abhishek" (I gave it) - nothing for my own notes
    const mineId = me().id, own = t.private || (t.assignee === mineId && t.created_by === mineId) ? "" :
      t.assignee === mineId ? "from " + esc(t.created_by_name || t.created_by) : "to " + esc(t.assignee_name || t.assignee);
    const tick = open_(t) && (t.can_edit || t.mine) && !opts.noTick ? '<button class="wt-tick" data-done="' + t.id + '" data-no-busy title="Mark done">' + RMS.icon("check") + "</button>" : "";
    return '<div class="wt-card pr-' + t.priority + (open_(t) ? "" : " closed") + (opts.compact ? " compact" : "") + '" data-id="' + t.id + '"' + (opts.drag ? ' draggable="true"' : "") + ">" + tick +
      '<div class="t" title="' + esc(t.title) + '"><span class="k">' + esc(t.key) + "</span>" + (t.private ? RMS.icon("eye") + " " : "") + esc(t.title) + "</div>" + sub +
      '<div class="m">' + pills(t) + (own && !opts.noOwn ? '<span class="wt-own">' + own + "</span>" : "") + '<div class="r">' +
      (t.comments ? RMS.icon("file") + t.comments + " " : "") + (t.files ? "📎" + t.files + " " : "") +
      (opts.noAv ? "" : av(t.assignee_name || t.assignee)) + "</div></div></div>";
  }
  function wire(root) {
    root.querySelectorAll(".wt-card").forEach((c) => { c.onclick = () => openTask(+c.dataset.id); });
    root.querySelectorAll("[data-done]").forEach((b) => {
      b.onclick = async (e) => {
        e.stopPropagation();
        const t = S.tasks.find((x) => x.id === +b.dataset.done); b.disabled = true;
        const r = await api("/tracker/task/" + b.dataset.done, { status: "done" });
        if (r.ok) { okBox((t ? t.key : "Task") + " marked done", "The creator is told."); reload(); } else { b.disabled = false; bad(r.error); }
      };
    });
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
      [["mine", "My work"], ["board", "Board"], ["diary", "Diary"], ["team", "Team diary"], ["all", "All tasks"]].map((v) => '<button data-v="' + v[0] + '">' + v[1] + "</button>").join("") + "</div>" +
      '<input class="wt-in" id="trkQ" placeholder="Search tasks…" style="width:220px">' +
      '<select class="wt-sel" id="trkWho"></select><span id="trkKchip"></span><div class="spacer"></div>' +
      '<button class="btn ghost" id="trkPeople" data-no-busy title="Mobile, e-mail and reminder settings">' + RMS.icon("bell") + " People and alerts</button>" +
      '<button class="btn primary" id="trkNew" data-no-busy><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg> New task</button></div>' +
      '<div id="trkBody"><div class="card-sub" style="padding:40px;text-align:center">Loading…</div></div>';
  }

  function renderKpis() {
    const s = S.summary, mgr = me().manager;
    const k = [["mine", "My open tasks", s.mine, "var(--accent)", "assigned to me"], ["overdue", "Overdue", s.overdue, "var(--err)", "mine, past the due date"],
      ["today", "Due today", s.today, "var(--warn)", "mine"], ["waiting", "Waiting for reply", s.waiting, "#f59e0b", mgr ? "across the team" : "visible to me"],
      ["review", "To review", s.review, "#c084fc", "done by others, waiting for me"]];
    $("#trkKpis").innerHTML = '<div class="wt-kpis">' + k.map((x) => '<div class="card wt-kpi' + (S.kpi === x[0] ? " on" : "") + '" title="' + x[4] + '" data-k="' + x[0] + '"><div class="l"><i style="--c:' + x[3] + '"></i>' +
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
    ({ mine: viewMine, board: viewBoard, diary: viewDiary, team: viewTeam, all: viewAll }[S.view] || viewMine)();
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
          l.map((t) => card(t, { noAv: true, compact: true })).join("") +
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

  // Team diary: what the manager handed out and what is stuck, with the reply state of every waiting task
  function stText(t) {
    if (t.status === "waiting") return ['<span class="wt-pill s-waiting">' + (t.waiting_days ? "No reply · " + t.waiting_days + (t.waiting_days === 1 ? " day" : " days") : "Waiting · today") + "</span>"];
    if (t.replied && t.replied_at && (Date.now() - new Date(t.replied_at)) < 7 * 864e5) return ['<span class="wt-pill s-reply">Reply received</span>'];
    return ['<span class="wt-pill s-' + t.status + '">' + (t.status === "open" ? "Not started" : esc(t.status_label)) + "</span>"];
  }
  function viewTeam() {
    const v = visible(), id = me().id;
    const F = { given: (t) => t.created_by === id && t.assignee !== id && open_(t), waiting: (t) => t.status === "waiting", overdue: (t) => t.overdue };
    const n = (k) => v.filter(F[k]).length;
    let list = v.filter((t) => open_(t) || (t.replied && t.replied_at && (Date.now() - new Date(t.replied_at)) < 7 * 864e5));
    if (S.tchip) list = list.filter(F[S.tchip]);
    const ord = { urgent: 0, high: 1, normal: 2, low: 3 };
    list.sort((a, b) => ((b.status === "waiting") - (a.status === "waiting")) || (b.overdue - a.overdue) || ((a.due || "9999") < (b.due || "9999") ? -1 : 1) || (ord[a.priority] - ord[b.priority]));
    const chip = (k, lab, c) => '<button class="wt-chip' + (S.tchip === k ? " on" : "") + '" data-c="' + k + '" style="--c:' + c + '">' + lab + " <b>" + n(k) + "</b></button>";
    $("#trkBody").innerHTML = '<div class="card" style="padding:0;overflow:hidden"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:14px 16px;border-bottom:1px solid var(--line)">' +
      '<div class="card-title" style="margin-right:6px">Task diary</div>' + chip("given", "Assigned by me", "var(--accent)") + chip("waiting", "Waiting for reply", "#f59e0b") + chip("overdue", "Overdue", "var(--err)") +
      '<span class="card-sub" style="margin-left:auto">Everything handed out, and what is stuck. Click a row to open it.</span></div>' +
      '<div style="max-height:calc(100vh - 400px);overflow:auto"><table class="htable"><thead><tr><th>Task</th><th>Owner</th><th>Status</th><th>Due</th></tr></thead><tbody>' +
      (list.length ? list.map((t) => '<tr data-id="' + t.id + '"><td><span class="mono" style="color:var(--text-3);margin-right:6px">' + esc(t.key) + "</span><b>" + esc(t.title) + "</b>" +
        (t.status === "waiting" && t.waiting_on ? '<div class="wt-sub">waiting on ' + esc(t.waiting_on) + "</div>" : "") + "</td>" +
        '<td><div style="display:flex;gap:7px;align-items:center">' + av(t.assignee_name || t.assignee) + esc(t.assignee_name || t.assignee) + "</div></td><td>" + stText(t)[0] + "</td>" +
        '<td style="white-space:nowrap;' + (t.overdue ? "color:var(--err)" : "") + '">' + (esc(dueText(t)) || "-") + "</td></tr>").join("")
        : '<tr><td colspan="4" style="padding:30px;text-align:center;color:var(--text-3)">Nothing here.</td></tr>') + "</tbody></table></div></div>";
    $("#trkBody").querySelectorAll("[data-c]").forEach((b) => { b.onclick = () => { S.tchip = S.tchip === b.dataset.c ? "" : b.dataset.c; viewTeam(); }; });
    $("#trkBody").querySelectorAll("tbody tr[data-id]").forEach((r) => { r.onclick = () => openTask(+r.dataset.id); });
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
  function procList() { return ""; }
  // ── Linked process: proper multi-select dropdown (search, tick boxes, All processes, Clear). Value is stored as "A; B" or "*ALL*".
  const ALL = "*ALL*";
  const procArr = (s) => String(s || "").split(";").map((x) => x.trim()).filter(Boolean);
  const procLabel = (s) => (String(s || "").trim() === ALL ? "All processes" : procArr(s).join(", "));
  function procPicker(host, initial, disabled, onChange) {
    document.querySelectorAll(".wt-pp-panel").forEach((x) => x.remove());
    const all = (S.boot && S.boot.processes) || [];
    let sel = new Set(procArr(initial)), isAll = sel.has(ALL); if (isAll) sel = new Set();
    host.classList.add("wt-pp");
    host.innerHTML = '<button type="button" class="wt-pp-btn" data-no-busy' + (disabled ? " disabled" : "") + '></button>';
    const btn = host.firstChild; let panel = null;
    const value = () => (isAll ? ALL : Array.from(sel).join("; "));
    const paintBtn = () => {
      const chips = isAll ? '<span class="wt-chip">All processes</span>' : Array.from(sel).slice(0, 3).map((x) => '<span class="wt-chip">' + esc(x) + "</span>").join("") + (sel.size > 3 ? '<span class="wt-chip">+' + (sel.size - 3) + " more</span>" : "");
      btn.innerHTML = '<span class="wt-pp-v">' + (chips || '<span style="color:var(--text-3)">Select process(es)…</span>') + '</span><span class="wt-pp-c">▾</span>';
    };
    const closePanel = () => { if (panel) { panel.remove(); panel = null; document.removeEventListener("mousedown", outside, true); } };
    const outside = (e) => { if (panel && !panel.contains(e.target) && !btn.contains(e.target)) closePanel(); };
    const commit = () => { paintBtn(); if (onChange) onChange(value()); };
    const paintList = () => {
      const q = (panel.querySelector(".q").value || "").toLowerCase();
      panel.querySelector(".lst").innerHTML = all.filter((p) => p.toLowerCase().includes(q)).map((p) =>
        '<label class="wt-pp-r"><input type="checkbox" data-p="' + esc(p) + '"' + (isAll || sel.has(p) ? " checked" : "") + (isAll ? " disabled" : "") + "><span>" + esc(p) + "</span></label>").join("") || '<div class="wt-pp-r" style="color:var(--text-3)">No match</div>';
      panel.querySelector(".all").checked = isAll;
      panel.querySelector(".cnt").textContent = isAll ? "All" : sel.size + " selected";
      panel.querySelectorAll("[data-p]").forEach((c) => { c.onchange = () => { c.checked ? sel.add(c.dataset.p) : sel.delete(c.dataset.p); panel.querySelector(".cnt").textContent = sel.size + " selected"; commit(); }; });
    };
    btn.onclick = () => {
      if (panel) { closePanel(); return; }
      panel = document.createElement("div"); panel.className = "wt-pp-panel";
      panel.innerHTML = '<input class="wt-in q" placeholder="Search process…" style="width:100%;margin-bottom:6px"><label class="wt-pp-r" style="font-weight:700"><input type="checkbox" class="all"><span>All processes (Select all)</span></label>' +
        '<div class="lst"></div><div class="wt-pp-f"><span class="cnt"></span><span><a href="#" class="clr">Clear</a> &nbsp; <a href="#" class="dn">Done</a></span></div>';
      document.body.appendChild(panel);
      const r = btn.getBoundingClientRect(), h = 330;
      panel.style.left = r.left + "px"; panel.style.width = Math.max(r.width, 280) + "px";
      panel.style.top = (r.bottom + h > innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4) + "px";
      paintList();
      panel.querySelector(".q").oninput = paintList; panel.querySelector(".q").focus();
      panel.querySelector(".all").onchange = (e) => { isAll = e.target.checked; sel.clear(); paintList(); commit(); };
      panel.querySelector(".clr").onclick = (e) => { e.preventDefault(); isAll = false; sel.clear(); paintList(); commit(); };
      panel.querySelector(".dn").onclick = (e) => { e.preventDefault(); closePanel(); };
      document.addEventListener("mousedown", outside, true);
    };
    paintBtn();
    return { get: value, set: (v) => { sel = new Set(procArr(v)); isAll = sel.has(ALL); if (isAll) sel = new Set(); paintBtn(); } };
  }

  function newTask(pre) {
    pre = pre || {};
    let kind = pre.kind || "task";
    const close = RMS.modal("New task",
      '<div class="wt-seg" id="ntKind" style="margin-bottom:14px">' + ["task", "followup", "change", "note"].map((k) => '<button data-k="' + k + '"' + (k === kind ? ' class="on"' : "") + ">" + KIND[k] + "</button>").join("") + "</div>" +
      '<div id="ntHint" style="border-left:3px solid var(--accent);background:var(--surface-2);border-radius:6px;padding:9px 12px;font-size:12.5px;line-height:1.5;margin-bottom:14px"></div>' +
      '<div class="wt-lbl" id="ntTitleL">What needs to be done</div><input class="wt-in" id="ntTitle" style="width:100%;margin-bottom:12px" maxlength="200" value="' + esc(pre.title || "") + '">' +
      '<div class="wt-lbl">Details (optional)</div><textarea class="wt-ta" id="ntDesc"></textarea>' +
      '<div class="wt-grid2" style="margin-top:12px"><div id="ntWhoBox"><div class="wt-lbl">Assign to</div><select class="wt-sel" id="ntWho" style="width:100%">' + peopleOpts(pre.assignee || me().id) + "</select></div>" +
      '<div><div class="wt-lbl" id="ntDueL">Due date</div><input type="date" class="wt-in" id="ntDue" style="width:100%" value="' + esc(pre.due || "") + '"></div>' +
      '<div id="ntPriBox"><div class="wt-lbl">Priority</div><select class="wt-sel" id="ntPri" style="width:100%">' + ["normal", "low", "high", "urgent"].map((p) => '<option value="' + p + '">' + PRI[p] + "</option>").join("") + "</select></div>" +
      '<div id="ntProcBox"><div class="wt-lbl" id="ntProcL">Linked process (optional)</div><div id="ntProc"></div></div></div>' +
      '<div id="ntFollow" style="display:none;margin-top:12px" class="wt-grid2"><div><div class="wt-lbl">Waiting for a reply from</div><input class="wt-in" id="ntWait" style="width:100%" placeholder="Exchange helpdesk, branch manager…"></div>' +
      '<div><div class="wt-lbl">Remind me every (days)</div><input type="number" min="1" max="60" class="wt-in" id="ntDays" style="width:100%" value="2"></div></div>' +
      '<div class="wt-lbl" style="margin-top:14px">Pictures and files (optional)</div><div class="wt-files" id="ntFiles"></div>' +
      '<div class="wt-drop" id="ntDrop">' + RMS.icon("download") + ' Drop pictures here, click to choose, or paste a screenshot (Ctrl+V)</div><input type="file" id="ntPick" multiple style="display:none">' +
      '<div class="wt-lbl" style="margin-top:14px">First comment (optional)</div><textarea class="wt-ta" id="ntCmt" style="min-height:44px" placeholder="Add a comment for the assignee…"></textarea>' +
      '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:18px"><button class="btn ghost" id="ntCancel" data-no-busy>Cancel</button><button class="btn primary" id="ntSave" data-no-busy>Create task</button></div>',
      { width: 640, icon: "check" });
    const pp = procPicker($("#ntProc"), pre.process || "", false);
    const pend = [];   // pictures chosen before the task exists - uploaded right after it is created
    const paintPend = () => {
      $("#ntFiles").innerHTML = pend.map((f, i) => '<div class="wt-file" title="' + esc(f.name) + '">' + (f.type.startsWith("image/") ? '<img src="' + URL.createObjectURL(f) + '">' : esc(f.name)) +
        '<span class="x" data-i="' + i + '" style="display:flex">✕</span></div>').join("");
      document.querySelectorAll("#ntFiles .x").forEach((x) => { x.onclick = () => { pend.splice(+x.dataset.i, 1); paintPend(); }; });
    };
    const addPend = (fs) => { fs.forEach((f) => pend.push(f)); paintPend(); };
    $("#ntDrop").onclick = () => $("#ntPick").click();
    $("#ntPick").onchange = () => { addPend(Array.from($("#ntPick").files)); $("#ntPick").value = ""; };
    $("#ntDrop").ondragover = (e) => { e.preventDefault(); $("#ntDrop").classList.add("over"); };
    $("#ntDrop").ondragleave = () => $("#ntDrop").classList.remove("over");
    $("#ntDrop").ondrop = (e) => { e.preventDefault(); $("#ntDrop").classList.remove("over"); addPend(Array.from(e.dataTransfer.files || [])); };
    document.onpaste = (e) => { if (!$("#ntDrop")) { document.onpaste = null; return; } const fs = Array.from((e.clipboardData && e.clipboardData.files) || []).filter((f) => f.type.startsWith("image/")); if (fs.length) { e.preventDefault(); addPend(fs); } };
    const T = {
      task: ["var(--accent)", "<b>Task</b> - work you give to someone (or to yourself) with a due date. They get a WhatsApp and an e-mail.", "What needs to be done", "Send the ledger query to the Ludhiana branch", "Anything the person should know", "Due date"],
      followup: ["#f59e0b", "<b>Follow-up</b> - something you sent and are <b>waiting for a reply on</b>. It stays on the list and you are reminded every few days until the reply comes.", "What are you following up", "Penalty query sent to the exchange - reply awaited", "What was sent, to whom, reference numbers", "Follow up by"],
      change: ["#c084fc", "<b>Change</b> - a change or fix to be made in the console or a process. Link the process so it is easy to find later.", "Change required", "Update Margin Shortage run time to 14:45", "What exactly should change, and why", "Needed by"],
      note: ["var(--text-3)", "<b>Private note</b> - your own reminder. Only you can see it, not even managers, and nobody is notified.", "Note to myself", "Call the bank about the payout", "Anything to remember", "Remind me on"],
    };
    const sync = () => {
      const t = T[kind];
      $("#ntHint").style.borderLeftColor = t[0]; $("#ntHint").innerHTML = t[1];
      $("#ntTitleL").textContent = t[2]; $("#ntTitle").placeholder = t[3]; $("#ntDesc").placeholder = t[4]; $("#ntDueL").textContent = t[5];
      $("#ntProcL").textContent = kind === "change" ? "Process / area changed" : "Linked process (optional)";
      $("#ntFollow").style.display = kind === "followup" ? "grid" : "none";
      $("#ntWhoBox").style.display = kind === "note" ? "none" : "";
      $("#ntPriBox").style.display = kind === "note" ? "none" : "";
      $("#ntProcBox").style.display = kind === "note" ? "none" : "";
      if (kind === "note") $("#ntWho").value = me().id;
      document.querySelectorAll("#ntKind button").forEach((b) => b.classList.toggle("on", b.dataset.k === kind));
    };
    document.querySelectorAll("#ntKind button").forEach((b) => { b.onclick = () => { kind = b.dataset.k; sync(); }; });
    sync(); if (pre.desc) $("#ntDesc").value = pre.desc; $("#ntTitle").focus();
    $("#ntCancel").onclick = close;
    $("#ntSave").onclick = async () => {
      const btn = $("#ntSave"); btn.disabled = true; btn.textContent = "Creating…";
      const body = { title: $("#ntTitle").value, description: $("#ntDesc").value, assignee: $("#ntWho").value, due: $("#ntDue").value, priority: $("#ntPri").value,
        kind, process: pp.get(), private: kind === "note" };
      if (pre.log) body.log_ref = pre.log;
      if (kind === "followup") { body.waiting_on = $("#ntWait").value; body.follow_up_days = $("#ntDays").value; }
      const r = await api("/tracker/task", body);
      if (!r.ok) { btn.disabled = false; btn.textContent = "Create task"; bad(r.error); return; }
      const cm = $("#ntCmt").value.trim(); let extra = "";
      if (cm) { const c = await api("/tracker/task/" + r.task.id + "/comment", { text: cm }); if (!c.ok) extra += " Comment not saved: " + c.error; }
      if (pend.length) {
        const fd = new FormData(); pend.forEach((f) => fd.append("files", f, f.name || "screenshot.png"));
        let u; try { u = await (await fetch("/tracker/task/" + r.task.id + "/file", { method: "POST", body: fd, credentials: "same-origin" })).json(); } catch (e) { u = { ok: false, error: "Upload failed." }; }
        if (!u.ok || (u.rejected && u.rejected.length)) extra += " Some pictures were not attached: " + ((u.rejected || []).join("; ") || u.error);
      }
      document.onpaste = null; close();
      okBox(r.task.key + " created", (r.task.assignee !== me().id ? "Assigned to " + r.task.assignee_name + " - they are notified on WhatsApp / e-mail if their contacts are filled in." : "Added to your list.") + extra);
      await reload();
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
    // History as a timeline (newest first): coloured dots on a vertical line - comments are cards, changes are one-liners
    const evCls = (b) => /^Status/.test(b) ? "st" : /^Assigned/.test(b) ? "as" : /reply|mail/i.test(b) ? "ml" : "ch";
    const acts = t.activity.slice().reverse().map((a) => a.kind === "comment" ?
      '<div class="wt-tn cm"><i></i><div class="c">' + av(a.by_name) + '<div class="b"><small>' + esc(a.by_name) + " · " + when(a.at) + "</small>" + esc(a.body) + "</div></div></div>" :
      '<div class="wt-tn ' + evCls(a.body) + '"><i></i><div class="e">' + esc(a.body) + ' <small>· ' + esc(a.by_name) + ", " + when(a.at) + "</small></div></div>").join("") +
      '<div class="wt-tn st"><i></i><div class="e"><b>Created</b> <small>· ' + esc(t.created_by_name) + ", " + when(t.created_at) + "</small></div></div>";
    // Status flow strip: where the task has been (ticked), where it is now (glowing), what is still ahead (grey)
    const seen = new Set(["open", t.status]);
    t.activity.forEach((a) => { const m = /^Status: (.+?) \(was (.+)\)$/.exec(a.body || ""); if (m) COLS.forEach((c) => { if (c[1] === m[1] || c[1] === m[2]) seen.add(c[0]); }); });
    const flow = '<div class="wt-flow">' + COLS.map((c, i) => '<div class="wt-fs' + (c[0] === t.status ? " now" : seen.has(c[0]) ? " seen" : "") + '" style="--c:' + c[2] + '"><b>' + (seen.has(c[0]) && c[0] !== t.status ? "✓" : i + 1) + "</b><span>" + esc(c[1]) + "</span></div>").join('<em></em>') + "</div>";
    const cmts = t.activity.filter((a) => a.kind === "comment").slice().reverse();
    const cmtList = '<div class="wt-lbl" style="margin-top:4px">Comments (' + cmts.length + ")</div>" + (cmts.length ?
      '<div class="wt-tl" style="margin-bottom:14px">' + cmts.map((a) => '<div class="wt-tn cm"><i></i><div class="c">' + av(a.by_name) + '<div class="b"><small>' + esc(a.by_name) + " · " + when(a.at) + "</small>" + esc(a.body) + "</div></div></div>").join("") + "</div>" :
      '<div class="card-sub" style="margin-bottom:14px">No comments yet.</div>');
    const files = t.file_list.map((f) => '<a class="wt-file" href="/tracker/file/' + f.id + '" target="_blank" title="' + esc(f.name + " · " + f.by_name) + '">' +
      (f.is_image ? '<img src="/tracker/file/' + f.id + '" alt="' + esc(f.name) + '">' : esc(f.name)) + '<span class="x" data-del="' + f.id + '" title="Remove">✕</span></a>').join("");
    const follow = t.status === "waiting" || t.kind === "followup";
    const links = (t.log || t.process) ? '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">' +
      (t.process ? (procArr(t.process).includes(ALL) || procArr(t.process).length > 1 ?
        '<span class="card-sub" style="align-self:center">' + RMS.icon("layers") + " " + esc(procLabel(t.process)) + "</span>" +
        (procArr(t.process).includes(ALL) ? '<button class="btn sm ghost" data-gp="" data-no-busy>' + RMS.icon("layers") + " Open Process Center</button>" :
          procArr(t.process).map((p) => '<button class="btn sm ghost" data-gp="' + esc(p) + '" data-no-busy>' + esc(p) + "</button>").join("")) :
        '<button class="btn sm ghost" data-gp="' + esc(t.process) + '" data-no-busy>' + RMS.icon("layers") + " Open " + esc(t.process) + " in Process Center</button>") : "") +
      (t.log ? '<button class="btn sm ghost" id="wtGoLog" data-no-busy>' + RMS.icon("terminal") + " Open run log</button>" : "") + "</div>" : "";
    const mb = (S.boot && S.boot.mailbox) || {};
    const mbLine = t.status !== "waiting" ? (t.replied ? "A reply was received " + when(t.replied_at) + "." : "")
      : mb.configured ? (mb.last_checked ? "Tracker mailbox checked " + when(mb.last_checked) + " - no reply found" + (t.mail_subject ? "" : " (link or send a mail so replies can be matched)") : "Tracker mailbox connected - first check is pending.")
        : "Tracker mailbox is not connected - press <b>Reply received</b> yourself when the reply comes.";
    const mailPanel = follow && canSt ? '<div class="wt-mail"><div class="h">' + RMS.icon("mail") + ' <b>E-mail tracking</b></div>' +
      (t.mail_subject ? '<div class="l">Tracked mail: <b>' + esc(t.mail_subject) + "</b>" + (t.waiting_email ? " to " + esc(t.waiting_email) : "") + "</div>" : "") +
      (mbLine ? '<div class="l">' + mbLine + "</div>" : "") +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px"><button class="btn sm" id="wtMailSend" data-no-busy>' + RMS.icon("send") + " Send follow-up mail</button>" +
      '<button class="btn sm" id="wtMailLink" data-no-busy>' + RMS.icon("copy") + " Link a sent mail</button>" +
      (t.status === "waiting" ? '<button class="btn sm primary" id="wtReply" data-no-busy>' + RMS.icon("check") + " Reply received</button>" : "") +
      (mb.configured && t.status === "waiting" ? '<button class="btn sm ghost" id="wtMailCheck" data-no-busy>' + RMS.icon("refresh") + " Check mailbox now</button>" : "") + "</div></div>" : "";
    el.className = "wt-win st-" + t.status;
    el.innerHTML = '<div class="wt-main">' +
      '<input class="wt-title" id="wtTitle" value="' + esc(t.title) + '"' + (edit ? "" : " readonly") + ' maxlength="200">' +
      '<div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap"><span class="wt-pill s-' + t.status + '">' + esc(t.status_label) + '</span><span class="wt-pill">' + esc(KIND[t.kind] || t.kind) + "</span>" + pills(t) +
      '<span class="card-sub">Created by ' + esc(t.created_by_name) + ", " + when(t.created_at) + "</span></div>" +
      '<div class="wt-lbl">Progress</div>' + flow +
      (t.status === "waiting" ? '<div class="wt-warn">' + RMS.icon("mail") + " Waiting for a reply" + (t.waiting_on ? " from <b>" + esc(t.waiting_on) + "</b>" : "") +
        (t.waiting_days != null ? " · " + (t.waiting_days === 0 ? "since today" : t.waiting_days + " day(s) with no reply") : "") +
        (t.follow_up_days ? " · reminder every " + t.follow_up_days + " day(s)" : "") + "</div>" : "") +
      links + mailPanel +
      '<div class="wt-cbox"><div class="wt-lbl" style="color:var(--text)">' + RMS.icon("file") + ' Add a comment</div><textarea class="wt-ta" id="wtCmt" style="min-height:54px" placeholder="Write a comment… (Ctrl+Enter to send)"></textarea>' +
      '<div style="display:flex;justify-content:flex-end;margin-top:6px"><button class="btn primary" id="wtSend" data-no-busy>Send comment</button></div></div>' + cmtList +
      '<div class="wt-lbl">Details</div><textarea class="wt-ta" id="wtDesc"' + (edit ? "" : " readonly") + ' placeholder="No details">' + esc(t.description || "") + "</textarea>" +
      '<div class="wt-lbl" style="margin-top:14px">Pictures and files</div><div class="wt-files">' + files + "</div>" +
      '<div class="wt-drop" id="wtDrop">' + RMS.icon("download") + ' Drop pictures or files here, click to choose, or paste a screenshot (Ctrl+V)</div><input type="file" id="wtPick" multiple style="display:none">' +
      '<div class="wt-lbl" style="margin-top:16px">History</div><div class="wt-tl">' + acts + "</div></div>" +
      '<div class="wt-side"><div class="f"><div class="wt-lbl">Status</div><select class="wt-sel st-sel s-' + t.status + '" id="wtSt"' + (canSt && (edit || ["done", "cancelled"].indexOf(t.status) < 0) ? "" : " disabled") + ">" + selOpts(S.boot.statuses.map((s) => [s.id, s.label]), t.status) + "</select></div>" +
      '<div class="f"><div class="wt-lbl">Assigned to</div><select class="wt-sel" id="wtWho"' + (edit && !t.private ? "" : " disabled") + ">" + peopleOpts(t.assignee) + "</select></div>" +
      '<div class="f"><div class="wt-lbl">Due date</div><input type="date" class="wt-in" id="wtDue" value="' + esc(t.due || "") + '"' + (edit ? "" : " disabled") + "></div>" +
      '<div class="f"><div class="wt-lbl">Priority</div><select class="wt-sel" id="wtPri"' + (edit ? "" : " disabled") + ">" + selOpts(S.boot.priorities.map((p) => [p, PRI[p]]), t.priority) + "</select></div>" +
      '<div class="f"><div class="wt-lbl">Type</div><select class="wt-sel" id="wtKind"' + (edit ? "" : " disabled") + ">" + selOpts(S.boot.kinds.map((k) => [k, KIND[k]]), t.kind) + "</select></div>" +
      '<div class="f"><div class="wt-lbl">Linked process</div><div id="wtProc"></div></div>' +
      (follow ? '<div class="f"><div class="wt-lbl">Waiting for reply from</div><input class="wt-in" id="wtWait" value="' + esc(t.waiting_on || "") + '"' + (canSt ? "" : " disabled") + "></div>" +
        '<div class="f"><div class="wt-lbl">Remind every (days)</div><input type="number" min="1" max="60" class="wt-in" id="wtDays" value="' + (t.follow_up_days || "") + '"' + (canSt ? "" : " disabled") + "></div>" : "") +
      '<div class="card-sub" style="margin:4px 0 12px">Assigned to ' + esc(t.assignee_name || t.assignee) + "</div>" +
      (t.status === "done" ? '<div class="wt-doneb">' + RMS.icon("check") + " <b>Completed</b><span>" + (edit ? "Not satisfied? Reopen it with a comment." : "Only the creator or a manager can reopen it.") + "</span>" +
        (edit ? '<button class="btn sm" id="wtReopen" data-no-busy style="margin-top:8px;justify-content:center">' + RMS.icon("refresh") + " Reopen task</button>" : "") + "</div>" : "") +
      (((S.boot.me || {}).admin) ? '<button class="btn ghost" id="wtDel" data-no-busy style="width:100%;justify-content:center;color:var(--err);margin-top:10px">Delete task (admin)</button>' : "") + "</div>";
    wireTask();
  }
  function followupWin(t) {
    const back = () => openTask(t.id);
    const close = RMS.modal("Send follow-up mail - " + t.key,
      '<div class="card-sub" style="margin-bottom:12px;line-height:1.55">Sent from the console\'s mail account. The subject keeps <b>[' + esc(t.key) + ']</b> so the reply can be found in the tracker mailbox.</div>' +
      '<div class="wt-lbl">To (e-mail, several with a comma)</div><input class="wt-in" id="fuTo" style="width:100%;margin-bottom:10px" value="' + esc(t.waiting_email || "") + '" placeholder="helpdesk@exchange.com">' +
      '<div class="wt-lbl">Subject</div><input class="wt-in" id="fuSub" style="width:100%;margin-bottom:10px" value="' + esc(t.mail_subject || ("[" + t.key + "] " + t.title)) + '">' +
      '<div class="wt-lbl">Message</div><textarea class="wt-ta" id="fuBody" style="min-height:130px">Dear Sir/Madam,\n\nThis is a gentle reminder regarding: ' + esc(t.title) + '.\nKindly reply at the earliest.\n\nRegards,\n' + esc(me().name) + '</textarea>' +
      '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px"><button class="btn ghost" id="fuNo" data-no-busy>Cancel</button><button class="btn primary" id="fuGo" data-no-busy>Send mail</button></div>',
      { width: 620, icon: "mail" });
    $("#fuNo").onclick = () => { close(); back(); };
    $("#fuGo").onclick = async () => {
      const b = $("#fuGo"); b.disabled = true; b.textContent = "Sending…";
      const r = await api("/tracker/task/" + t.id + "/followup", { to: $("#fuTo").value, subject: $("#fuSub").value, body: $("#fuBody").value });
      b.disabled = false; b.textContent = "Send mail";
      if (!r.ok) { bad(r.error); return; }
      close(); okBox("Mail sent", "To " + r.task.waiting_email + ". The task now waits for the reply."); await reload(); back();
    };
  }
  function linkMailWin(t) {
    const back = () => openTask(t.id);
    const close = RMS.modal("Link a sent mail - " + t.key,
      '<div class="card-sub" style="margin-bottom:12px;line-height:1.55">Already sent the mail from Outlook? Enter its subject exactly as sent. When a reply with that subject arrives in the tracker mailbox, the task is moved on automatically. Replies that carry <b>[' + esc(t.key) + ']</b> are found too.</div>' +
      '<div class="wt-lbl">Subject of the mail you sent</div><input class="wt-in" id="lkSub" style="width:100%;margin-bottom:10px" value="' + esc(t.mail_subject || "") + '">' +
      '<div class="wt-lbl">Sent to (optional)</div><input class="wt-in" id="lkTo" style="width:100%" value="' + esc(t.waiting_email || "") + '" placeholder="person@company.com">' +
      '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px"><button class="btn ghost" id="lkNo" data-no-busy>Cancel</button><button class="btn primary" id="lkGo" data-no-busy>Link mail</button></div>',
      { width: 560, icon: "copy" });
    $("#lkNo").onclick = () => { close(); back(); };
    $("#lkGo").onclick = async () => {
      if (!$("#lkSub").value.trim()) { bad("Enter the subject of the mail."); return; }
      const body = { mail_subject: $("#lkSub").value, waiting_email: $("#lkTo").value }; if (t.status !== "waiting") body.status = "waiting";
      const r = await api("/tracker/task/" + t.id, body);
      if (!r.ok) { bad(r.error); return; }
      close(); okBox("Mail linked", "Replies to it will be tracked."); await reload(); back();
    };
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
    if (!r.ok || (r.rejected && r.rejected.length)) bad((r.rejected || []).join("; ") || r.error); else okBox("Attached", (r.saved || []).join(", "));
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
      if (g("wtDel")) g("wtDel").onclick = () => ask("Delete " + t.key + "?", "<b>" + esc(t.title) + "</b><br>The task, its comments and its files will be removed. This cannot be undone.", "Delete task",
        async () => {
          const r = await api("/tracker/task/" + t.id + "/delete", {});
          if (r.ok) { okBox(t.key + " deleted", "The task, comments and files are removed."); reload(); } else { bad(r.error); openTask(t.id); }
        }, () => openTask(t.id));
    }
    let procTimer = null;
    procPicker(g("wtProc"), t.process || "", !t.can_edit, (v) => { clearTimeout(procTimer); procTimer = setTimeout(async () => {
      const r = await api("/tracker/task/" + t.id, { process: v });   // saved quietly - no repaint, so the dropdown stays open while ticking
      if (r.ok) { S.win = r.task; ok("Process link saved"); reload(); } else bad(r.error);
    }, 700); });
    if (g("wtReopen")) g("wtReopen").onclick = () => {
      const close = RMS.modal("Reopen " + t.key,
        '<div class="card-sub" style="margin-bottom:12px;line-height:1.55">Tell <b>' + esc(t.assignee_name || t.assignee) + '</b> what is still not right. The task goes back to <b>In progress</b> and they are notified.</div>' +
        '<div class="wt-lbl">What is not satisfactory <span style="color:var(--err)">*</span></div><textarea class="wt-ta" id="roTxt" style="min-height:90px" placeholder="Explain what needs to be redone…"></textarea>' +
        '<div class="wt-lbl" style="margin-top:12px">New due date (project deadline)</div><input type="date" class="wt-in" id="roDue" style="width:100%" value="' + esc(t.due || "") + '">' +
        '<div class="wt-lbl" style="margin-top:12px">Pictures / files (optional)</div><input type="file" id="roFiles" multiple class="wt-in" style="width:100%">' +
        '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px"><button class="btn ghost" id="roNo" data-no-busy>Cancel</button><button class="btn primary" id="roGo" data-no-busy>Reopen task</button></div>',
        { width: 560, icon: "refresh" });
      $("#roNo").onclick = () => { close(); openTask(t.id); };
      $("#roGo").onclick = async () => {
        const txt = $("#roTxt").value.trim(); if (!txt) { $("#roTxt").focus(); return; }
        const btn = $("#roGo"); btn.disabled = true; btn.textContent = "Reopening…";
        const b = { status: "in_progress" }; const due = $("#roDue").value; if (due && due !== t.due) b.due = due;
        const r1 = await api("/tracker/task/" + t.id, b);
        if (!r1.ok) { btn.disabled = false; btn.textContent = "Reopen task"; bad(r1.error); return; }
        await api("/tracker/task/" + t.id + "/comment", { text: "Reopened: " + txt });
        const files = Array.from($("#roFiles").files || []);
        if (files.length) {
          const fd = new FormData(); files.forEach((f) => fd.append("files", f, f.name));
          try { await fetch("/tracker/task/" + t.id + "/file", { method: "POST", body: fd, credentials: "same-origin" }); } catch (e) { /* ignore */ }
        }
        close(); await openTask(t.id); reload(); okBox(t.key + " reopened", "It is back In progress and " + (t.assignee_name || "the assignee") + " is notified.");
      };
    };
    g("wtSt").onchange = () => {
      const v = g("wtSt").value, b = { status: v };
      if (v === "waiting") b.follow_up_days = t.follow_up_days || 2;
      patch(b, "Status: " + g("wtSt").selectedOptions[0].textContent);
    };
    if (g("wtWait")) {
      g("wtWait").onchange = () => patch({ waiting_on: g("wtWait").value }, "Saved");
      g("wtDays").onchange = () => patch({ follow_up_days: g("wtDays").value || 0 }, "Reminder saved");
    }
    document.querySelectorAll("#wtWin [data-gp]").forEach((b) => { b.onclick = () => { const p = b.dataset.gp; S.winClose && S.winClose(); RMS.go("process"); setTimeout(() => RMS.pcApply && RMS.pcApply(p, false), 700); }; });
    if (g("wtGoLog")) g("wtGoLog").onclick = () => RMS.openLogViewer(t.log);
    if (g("wtMailSend")) g("wtMailSend").onclick = () => followupWin(t);
    if (g("wtMailLink")) g("wtMailLink").onclick = () => linkMailWin(t);
    if (g("wtReply")) g("wtReply").onclick = async () => { const r = await api("/tracker/task/" + t.id + "/reply", {}); if (r.ok) { S.win = r.task; paintTask(); reload(); okBox("Reply received", t.key + " moved to In progress."); } else bad(r.error); };
    if (g("wtMailCheck")) g("wtMailCheck").onclick = async () => {
      const r = await api("/tracker/mailbox/check", {}); if (r.mailbox) S.boot.mailbox = r.mailbox;
      if (!r.ok) { bad(r.error); return; }
      const d = await api("/tracker/task/" + t.id); if (d.ok) { S.win = d.task; paintTask(); }
      okBox(r.matched ? r.matched + " reply found" : "Mailbox checked", r.matched ? "Tasks with a reply moved to In progress." : "No reply found yet."); reload();
    };
    const send = async () => {
      const txt = g("wtCmt").value.trim(); if (!txt) { g("wtCmt").focus(); return; }
      const r = await api("/tracker/task/" + t.id + "/comment", { text: txt });
      if (r.ok) { S.win = r.task; paintTask(); reload(); okBox("Comment sent", "The other person is notified."); } else bad(r.error);
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
        '<span class="wt-pill">' + (p.admin ? "Admin" : p.role === "manager" ? "Manager" : "Member") + "</span>") + '</td><td><button class="btn sm" data-no-busy data-save="' + esc(p.id) + '">Save</button></td></tr>').join("");
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
        '</div><div class="wt-lbl" style="margin-top:14px">Reply tracking - shared tracker mailbox</div><div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:12.5px">' +
        '<span id="stMbLine"></span><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="stMbOn"' + (s.mailbox_enabled ? " checked" : "") + "> Look for replies</label>" +
        '<label style="display:flex;gap:6px;align-items:center">every <input type="number" min="2" max="120" class="wt-in" id="stMbMin" style="width:70px" value="' + (s.mailbox_poll_min || 10) + '"> min</label>' +
        '<button class="btn sm" id="stMbCheck" data-no-busy>Check now</button></div>' +
        '<div class="card-sub" style="margin-top:6px">The mailbox login is added once in Scheduler &gt; Connections &gt; "Work Tracker - shared mailbox". Follow-up mails ask for replies to go there.</div>' +
        '<div style="margin-top:14px"><button class="btn primary sm" id="stSave" data-no-busy>Save settings</button></div></div>' : ""),
      { width: 820, icon: "bell" });
    document.querySelectorAll("[data-save]").forEach((b) => {
      b.onclick = async () => {
        const tr = b.closest("tr"), f = (n) => { const e = tr.querySelector('[data-f="' + n + '"]'); return e ? e.value : undefined; };
        const r = await api("/tracker/people", { id: b.dataset.save, mobile: f("mobile") || "", email: f("email") || "", role: f("role") || "member" });
        if (r.ok) { S.boot.people = r.people; okBox("Saved", tr.querySelector("b").textContent + " - contact details updated."); } else bad(r.error);
      };
    });
    if (mgr) {
      const mb = S.boot.mailbox || {};
      $("#stMbLine").innerHTML = mb.configured ? 'Mailbox <b>' + esc(mb.address) + "</b> " + (mb.last_error ? '<span style="color:var(--err)">- ' + esc(mb.last_error) + "</span>" : mb.last_checked ? "- last checked " + when(mb.last_checked) : "- not checked yet") : '<span style="color:var(--warn)">Mailbox not connected yet</span>';
      $("#stMbCheck").onclick = async () => { const r = await api("/tracker/mailbox/check", {}); if (r.mailbox) S.boot.mailbox = r.mailbox; if (r.ok) { okBox(r.matched ? r.matched + " reply found" : "Mailbox checked", r.matched ? "Tasks moved to In progress." : "No new reply."); reload(); } else bad(r.error); };
    }
    if (mgr) $("#stSave").onclick = async () => {
      const r = await api("/tracker/settings", { enabled: $("#stOn").checked, remind_at: $("#stAt").value, manager_summary: $("#stMgr").checked, notify_whatsapp: $("#stWa").checked, notify_email: $("#stMail").checked, mailbox_enabled: $("#stMbOn").checked, mailbox_poll_min: $("#stMbMin").value });
      if (r.ok) { S.boot.settings = r.settings; close(); okBox("Settings saved", "Daily reminder at " + r.settings.remind_at + "."); } else bad(r.error);
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
