/* pages/scheduler.js — calendar-style view of the automation day.
   Built from /ops/timeline (the same env-configured run windows the pipelines
   actually use). Two views: a positioned day timeline (hour grid, now-line)
   and grouped lists (due / missed / upcoming / completed). Windows recur every
   trading day; editing them stays where it always was — the Process Center's
   password-protected config. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false;
  const state = { view: "timeline", pipes: new Set(["ctcl", "sa", "ms15", "cli"]), data: null };
  const PIPES = [["ctcl", "CTCL vs NMASS"], ["sa", "Short Allocation"], ["ms15", "Margin Shortage"], ["cli", "Client Prudential"]];

  function shell() {
    return (
      '<div class="card" style="margin-bottom:12px"><div class="filters">' +
      '<div class="seg" id="sView"><button data-v="timeline" class="on">Timeline</button><button data-v="list">List</button></div>' +
      '<div class="root-chips" id="sPipes">' +
      PIPES.map(([k, n]) => '<span class="chip on" data-p="' + k + '">' + esc(n) + "</span>").join("") + "</div>" +
      '<div class="spacer"></div><span class="card-sub" id="sMeta"></span>' +
      '<button class="btn sm ghost" id="sRefresh">' + RMS.icon("refresh") + "</button></div></div>" +
      '<div class="kpi-grid" id="sKpis" style="margin-bottom:12px"></div>' +
      '<div id="sBody"></div>' +
      '<div class="card-sub" style="margin-top:14px">All windows recur every trading day (Mon–Fri). ' +
      "To change a window time, use the pipeline's own ⚙ config inside the Process Center (password-protected) — the Scheduler always reflects the live values.</div>"
    );
  }

  function items() {
    return (state.data?.items || []).filter((i) => state.pipes.has(i.pipe));
  }

  const BADGE = {
    completed: '<span class="badge ok">✓ done</span>',
    running: '<span class="badge info">running</span>',
    due: '<span class="badge warn">ready now</span>',
    overdue: '<span class="badge err">overdue</span>',
    waiting: '<span class="badge dim">waiting</span>',
    upcoming: '<span class="badge dim">upcoming</span>',
  };

  function kpi(icon, label, v, foot, cls) {
    return '<div class="card kpi' + (cls ? " accent-" + cls : "") + '"><div class="k-label">' + RMS.icon(icon) + label +
      '</div><div class="k-value" style="font-size:22px">' + v + '</div><div class="k-foot">' + (foot || "") + "</div></div>";
  }

  function renderKpis() {
    const it = items();
    const g = (s) => it.filter((i) => s.includes(i.status));
    $("#sKpis").innerHTML =
      kpi("check", "Completed", g(["completed"]).length, "of " + it.length + " today", "ok") +
      kpi("clock", "Due Now", g(["due", "overdue"]).length, g(["overdue"]).length + " overdue", g(["overdue"]).length ? "err" : g(["due"]).length ? "warn" : "") +
      kpi("activity", "Running", g(["running"]).length, "") +
      kpi("calendar", "Upcoming", g(["upcoming"]).length, "later today") +
      kpi("alert", "Missed Window", g(["waiting"]).length, "window passed, earlier run pending", g(["waiting"]).length ? "warn" : "");
  }

  function renderTimeline() {
    const it = items().filter((i) => i.start_min != null);
    if (!it.length) { $("#sBody").innerHTML = '<div class="card"><div class="bm-empty">No windows for the selected pipelines.</div></div>'; return; }
    const H = 56; // px per hour
    const first = Math.max(0, Math.floor(Math.min(...it.map((i) => i.start_min)) / 60) - 1);
    const last = Math.min(23, Math.ceil(Math.max(...it.map((i) => i.start_min)) / 60) + 1);
    let hours = "";
    for (let h = first; h <= last; h++) {
      hours += '<div class="sched-hour"><span>' + String(h).padStart(2, "0") + ":00</span></div>";
    }
    // lane packing: overlapping slots share the row space side by side
    const sorted = it.slice().sort((a, b) => a.start_min - b.start_min);
    const lanes = [];
    sorted.forEach((i) => {
      let l = 0;
      while (lanes[l] != null && i.start_min - lanes[l] < 26) l++;
      lanes[l] = i.start_min; i._lane = l;
    });
    const maxLane = Math.max(...sorted.map((i) => i._lane)) + 1;
    const slots = sorted.map((i) => {
      const top = (i.start_min - first * 60) / 60 * H - 1;
      const w = 100 / maxLane;
      return '<div class="sched-slot ' + i.status + '" style="top:' + top.toFixed(1) + "px;left:calc(" + (i._lane * w).toFixed(2) + "% + 10px);right:auto;width:calc(" + w.toFixed(2) + "% - 16px)\">" +
        "<b>" + esc(i.id) + "</b><span style=\"overflow:hidden;text-overflow:ellipsis\">" + esc(i.pipe_name) + "</span>" + BADGE[i.status] + "</div>";
    }).join("");
    const nowTop = (state.data.now_min - first * 60) / 60 * H;
    const nowLine = (state.data.now_min >= first * 60 && state.data.now_min <= (last + 1) * 60)
      ? '<div class="sched-now" style="top:' + nowTop.toFixed(1) + 'px"></div>' : "";
    $("#sBody").innerHTML = '<div class="card"><div class="sched-grid">' + hours + slots + nowLine + "</div></div>";
  }

  function renderList() {
    const groups = [["due", "Due now", ["due", "overdue"]], ["running", "Running", ["running"]],
      ["waiting", "Missed window (earlier run pending)", ["waiting"]],
      ["upcoming", "Upcoming", ["upcoming"]], ["completed", "Completed", ["completed"]]];
    let html = "";
    groups.forEach(([id, name, sts]) => {
      const g = items().filter((i) => sts.includes(i.status));
      if (!g.length) return;
      html += '<div class="sec-head"><h3>' + name + "</h3><span>" + g.length + "</span></div>" +
        '<div class="card" style="padding:6px">' + g.map((i) =>
          '<div class="tl-item ' + i.status + '"><span class="tl-time">' + esc((i.window || "—").split("-")[0]) + "</span>" +
          '<span class="tl-rail"><span class="tl-dot"></span></span>' +
          '<span class="tl-main"><span class="tl-id">' + esc(i.id) + '</span><span class="tl-name">' + esc(i.pipe_name) +
          (i.window ? " · window " + esc(i.window) : "") + "</span></span>" + BADGE[i.status] + "</div>").join("") + "</div>";
    });
    $("#sBody").innerHTML = html || '<div class="card"><div class="bm-empty">Nothing scheduled.</div></div>';
  }

  function render() {
    if (!state.data) return;
    $("#sMeta").textContent = "live · " + new Date().toTimeString().slice(0, 5);
    renderKpis();
    state.view === "timeline" ? renderTimeline() : renderList();
  }

  function load() {
    RMS.api("/ops/timeline").then((d) => { state.data = d; render(); }).catch(() => {});
  }

  RMS.pages = RMS.pages || {};
  RMS.pages.scheduler = {
    title: "Scheduler", crumb: "Today's execution windows across all pipelines",
    mount(el) {
      if (!booted) {
        el.innerHTML = shell();
        RMS.mountIcons(el);
        $("#sView").querySelectorAll("button").forEach((b) => {
          b.onclick = () => { $("#sView").querySelectorAll("button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); state.view = b.dataset.v; render(); };
        });
        $("#sPipes").querySelectorAll(".chip").forEach((c) => {
          c.onclick = () => {
            const p = c.dataset.p;
            state.pipes.has(p) && state.pipes.size > 1 ? (state.pipes.delete(p), c.classList.remove("on"))
              : (state.pipes.add(p), c.classList.add("on"));
            render();
          };
        });
        $("#sRefresh").onclick = load;
        RMS.poll(load, 30000);
        booted = true;
      } else load();
    },
  };
})();
