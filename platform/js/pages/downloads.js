/* pages/downloads.js — Download Center.
   Real files from the pipeline data folders (/ops/downloads), plus failed
   download-type runs from the execution archive. Downloads are EXECUTED from
   the Process Center (that's where the buttons, captchas and credentials
   live) — this page is where you find, verify, preview and save the results. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false;
  const state = { days: 1, folder: "all", q: "", items: [], hist: [] };

  function shell() {
    return (
      '<div class="kpi-grid" id="dKpis" style="margin-bottom:12px"></div>' +
      '<div class="card" style="margin-bottom:12px"><div class="filters">' +
      '<div class="seg" id="dDays"><button data-d="1" class="on">Today</button><button data-d="3">3d</button><button data-d="7">7d</button></div>' +
      '<select class="f-select" id="dFolder"><option value="all">All folders</option></select>' +
      '<input class="f-input" id="dSearch" placeholder="Search file name…">' +
      '<div class="spacer"></div>' +
      '<button class="btn sm ghost" id="dRefresh">' + RMS.icon("refresh") + " Refresh</button></div>" +
      '<div class="root-chips" id="dRoots" style="margin-top:10px"></div></div>' +

      '<div class="dash-cols" style="grid-template-columns:1.7fr 1fr;align-items:start">' +
      '<div class="card" style="padding:0;overflow:hidden">' +
      '<div class="card-head" style="padding:14px 16px 0;margin-bottom:8px"><div class="card-title">' + RMS.icon("download") + ' Downloaded Files</div>' +
      '<span class="card-sub" id="dCount"></span></div>' +
      '<div style="max-height:560px;overflow:auto"><table class="htable"><thead><tr>' +
      "<th>File</th><th>Folder</th><th>Size</th><th>Modified</th><th></th></tr></thead>" +
      '<tbody id="dRows"></tbody></table></div></div>' +

      '<div><div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("alert") + ' Failed Download Runs</div>' +
      '<span class="card-sub">click to read the log</span></div><div class="feed" id="dFails"></div></div>' +
      '<div class="card" style="margin-top:12px"><div class="card-head"><div class="card-title">' + RMS.icon("clock") + ' Pending Downloads</div>' +
      '<span class="card-sub">download windows still open</span></div><div class="feed" id="dPending"></div>' +
      '<div class="card-sub" style="margin-top:10px">Run them from the <a style="color:var(--accent);cursor:pointer" onclick="RMS.go(\'process\')">Process Center</a>.</div></div></div>' +
      "</div>"
    );
  }

  function kpi(icon, label, v, foot, cls) {
    return '<div class="card kpi' + (cls ? " accent-" + cls : "") + '"><div class="k-label">' + RMS.icon(icon) + label +
      '</div><div class="k-value" style="font-size:22px">' + v + '</div><div class="k-foot">' + (foot || "") + "</div></div>";
  }

  function filtered() {
    return state.items.filter((f) =>
      (state.folder === "all" || f.root === state.folder) &&
      (!state.q || f.name.toLowerCase().includes(state.q)));
  }

  function render() {
    const items = filtered();
    const today = new Date().toISOString().slice(0, 10);
    const dlRuns = state.hist.filter((e) => /download|mcx|nmass|ncdex|ftp/i.test(e.label));
    const dlFails = dlRuns.filter((e) => e.status === "failed");
    $("#dKpis").innerHTML =
      kpi("download", "Files (" + (state.days === 1 ? "today" : state.days + "d") + ")", state.items.length, "across all data folders") +
      kpi("check", "Download Runs OK", dlRuns.filter((e) => e.status === "ok").length, "in the last 2 days", "ok") +
      kpi("alert", "Failed Downloads", dlFails.length, dlFails.length ? "see list on the right" : "none 🎉", dlFails.length ? "err" : "ok") +
      kpi("disk", "Volume", RMS.fmtBytes(items.reduce((a, f) => a + (f.size || 0), 0)), "filtered selection");

    $("#dCount").textContent = items.length + " files";
    $("#dRows").innerHTML = items.length ? items.slice(0, 400).map((f, i) =>
      '<tr class="fx-row" data-i="' + i + '"><td><span class="fx-ic">' + RMS.icon("file") + "</span><b>" + esc(f.name) + "</b></td>" +
      "<td>" + esc(f.folder) + '</td><td class="mono">' + RMS.fmtBytes(f.size) + '</td><td class="mono">' +
      esc(f.mtime.slice(f.mtime.startsWith(today) ? 11 : 5)) + "</td>" +
      '<td><button class="btn sm ghost" data-save="' + i + '">' + RMS.icon("download") + "</button></td></tr>").join("")
      : '<tr><td colspan="5"><div class="bm-empty">No files match. Run a download from the Process Center first.</div></td></tr>';
    $("#dRows").querySelectorAll("tr[data-i]").forEach((tr) => {
      tr.onclick = (e) => {
        if (e.target.closest("[data-save]")) return;
        const f = items[+tr.dataset.i];
        RMS.previewFile(f.root, f.name);
      };
    });
    $("#dRows").querySelectorAll("[data-save]").forEach((b) => {
      b.onclick = () => { const f = items[+b.dataset.save]; location.href = "/ops/file/get?root=" + encodeURIComponent(f.root) + "&sub=" + encodeURIComponent(f.name); };
    });

    $("#dFails").innerHTML = dlFails.length ? dlFails.slice(0, 8).map((e, i) =>
      '<div class="feed-item" data-f="' + i + '"><span class="feed-ic failed">' + RMS.icon("alert") + "</span>" +
      '<div class="feed-main"><b>' + esc(e.label) + "</b><small>" + esc(e.pipe_name) + "</small></div>" +
      '<span class="feed-time">' + esc(e.start.slice(5, 16)) + "</span></div>").join("")
      : '<div class="bm-empty">No failed download runs.</div>';
    $("#dFails").querySelectorAll("[data-f]").forEach((el) => {
      el.onclick = () => RMS.openLogViewer(dlFails[+el.dataset.f]);
    });
  }

  function renderPending(tl) {
    const pend = (tl.items || []).filter((i) => ["due", "overdue", "upcoming"].includes(i.status)).slice(0, 8);
    $("#dPending").innerHTML = pend.length ? pend.map((i) =>
      '<div class="feed-item" style="cursor:default"><span class="feed-ic ' + (i.status === "upcoming" ? "unknown" : "stopped") + '">' + RMS.icon("clock") + "</span>" +
      '<div class="feed-main"><b>' + esc(i.id) + "</b><small>" + esc(i.pipe_name) + " · " + esc(i.window || "") + "</small></div>" +
      '<span class="badge ' + (i.status === "upcoming" ? "dim" : "warn") + '">' + i.status + "</span></div>").join("")
      : '<div class="bm-empty">Nothing pending — schedule complete.</div>';
  }

  function load() {
    RMS.api("/ops/downloads?days=" + state.days, { timeout: 20000 }).then((d) => {
      state.items = d.items || [];
      const sel = $("#dFolder"), cur = sel.value;
      const roots = [...new Map(state.items.map((f) => [f.root, f.folder])).entries()];
      sel.innerHTML = '<option value="all">All folders</option>' + roots.map(([k, l]) => '<option value="' + k + '">' + esc(l) + "</option>").join("");
      sel.value = roots.some(([k]) => k === cur) ? cur : "all";
      state.folder = sel.value;
      $("#dRoots").innerHTML = roots.map(([k, l]) =>
        '<span class="chip" data-r="' + k + '">' + RMS.icon("folder") + esc(l) + " <b>" +
        state.items.filter((f) => f.root === k).length + "</b></span>").join("");
      $("#dRoots").querySelectorAll(".chip").forEach((c) => {
        c.onclick = () => { sel.value = c.dataset.r; state.folder = c.dataset.r; render(); };
      });
      render();
    }).catch(() => {});
    RMS.api("/ops/history?days=2", { quiet: true }).then((d) => { state.hist = d.items || []; render(); }).catch(() => {});
    RMS.api("/ops/timeline", { quiet: true }).then(renderPending).catch(() => {});
  }

  RMS.pages = RMS.pages || {};
  RMS.pages.downloads = {
    title: "Downloads", crumb: "Every file the pipelines produced — find, preview, save",
    mount(el) {
      if (!booted) {
        el.innerHTML = shell();
        RMS.mountIcons(el);
        $("#dDays").querySelectorAll("button").forEach((b) => {
          b.onclick = () => { $("#dDays").querySelectorAll("button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); state.days = +b.dataset.d; load(); };
        });
        $("#dFolder").onchange = (e) => { state.folder = e.target.value; render(); };
        $("#dSearch").oninput = (e) => { state.q = e.target.value.trim().toLowerCase(); render(); };
        $("#dRefresh").onclick = load;
        booted = true;
      }
      load();
    },
  };
})();
