/* core.js — shared runtime: fetch/polling, toasts, modals, log viewer,
   notifications (bell), command palette, small format helpers. */
(function () {
  window.RMS = window.RMS || {};
  const $ = (s, r) => (r || document).querySelector(s);
  RMS.$ = $;

  /* ── helpers ─────────────────────────────────────────────────────────── */
  RMS.esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  RMS.fmtDur = (s) => {
    if (s == null) return "—";
    s = Math.round(s);
    if (s < 60) return s + "s";
    const m = Math.floor(s / 60), r = s % 60;
    if (m < 60) return m + "m " + (r ? r + "s" : "").trim();
    return Math.floor(m / 60) + "h " + (m % 60) + "m";
  };

  RMS.fmtAgo = (iso) => {
    const t = new Date(String(iso).replace(" ", "T"));
    if (isNaN(t)) return "";
    const d = (Date.now() - t.getTime()) / 1000;
    if (d < 60) return "just now";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  };

  RMS.fmtBytes = (b) => {
    if (b == null) return "—";
    if (b < 1024) return b + " B";
    if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
    return (b / 1048576).toFixed(1) + " MB";
  };

  /* ── API ─────────────────────────────────────────────────────────────── */
  RMS.api = async function (path, opts) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), (opts && opts.timeout) || 12000);
    try {
      const r = await fetch(path, { signal: ctl.signal });
      if (!r.ok) throw new Error("HTTP " + r.status);
      RMS.setOnline(true);
      return await r.json();
    } catch (e) {
      if (!(opts && opts.quiet)) RMS.setOnline(false);
      throw e;
    } finally { clearTimeout(t); }
  };

  RMS._online = null;
  RMS.setOnline = function (up) {
    if (RMS._online === up) return;
    RMS._online = up;
    const dot = $("#serverDot"), up2 = $("#serverUp");
    if (dot) dot.className = "dot " + (up ? "ok" : "err");
    // Previously only wrote text on the DOWN transition ("unreachable"), never
    // reset it back on the UP transition — so one flaky/slow request early on
    // (e.g. during page load) would permanently latch the sidebar to
    // "unreachable" even after the server was clearly responding again (dot
    // back to green, live data flowing). Only the Dashboard page's own
    // resource-strip poll (dashboard.js) ever overwrote it with "up <duration>",
    // so any OTHER page (Process Center, etc.) stayed stuck on the stale label
    // forever. Now every successful RMS.api() call clears it immediately,
    // page-agnostic; dashboard.js's more detailed "up <duration>" text still
    // overwrites this shortly after on pages that run that poll.
    if (up2) up2.textContent = up ? "online" : "unreachable";
  };

  /* polling registry — pauses everything while the tab is hidden */
  const _polls = [];
  RMS.poll = function (fn, ms) {
    fn();
    const id = setInterval(() => { if (!document.hidden) fn(); }, ms);
    _polls.push(id);
    return id;
  };
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) document.dispatchEvent(new CustomEvent("rms:wake"));
  });

  /* ── toasts ──────────────────────────────────────────────────────────── */
  RMS.toast = function (kind, title, sub, opts) {
    const root = $("#toasts");
    const el = document.createElement("div");
    el.className = "toast " + kind;
    const ic = { ok: "check", err: "alert", warn: "alert", info: "info" }[kind] || "info";
    el.innerHTML = RMS.icon(ic) + "<div><b>" + RMS.esc(title) + "</b>" +
      (sub ? "<small>" + RMS.esc(sub) + "</small>" : "") + "</div>";
    el.onclick = () => { if (opts && opts.onClick) opts.onClick(); dismiss(); };
    root.appendChild(el);
    let gone = false;
    function dismiss() {
      if (gone) return; gone = true;
      el.classList.add("leaving");
      setTimeout(() => el.remove(), 260);
    }
    if (!(opts && opts.sticky)) setTimeout(dismiss, (opts && opts.ms) || 4200);
    return dismiss;
  };

  /* ── modal ───────────────────────────────────────────────────────────── */
  RMS.modal = function (title, bodyHTML, opts) {
    const root = $("#modalRoot");
    const mySeq = (RMS._modalSeq = (RMS._modalSeq || 0) + 1);   // which modal is on screen right now
    root.innerHTML =
      '<div class="modal"' + ((opts && opts.width) ? ' style="width:min(' + opts.width + 'px,94vw)"' : "") + ">" +
      '<div class="modal-head">' + ((opts && opts.icon) ? RMS.icon(opts.icon) : "") +
      "<h3>" + RMS.esc(title) + '</h3><button class="modal-x" id="modalX">' + RMS.icon("x") + "</button></div>" +
      (opts && opts.raw ? bodyHTML : '<div class="modal-body">' + bodyHTML + "</div>") +
      "</div>";
    root.hidden = false;
    // A close() from an OLDER modal (e.g. a success popup's 4-second auto-close timer) must never close a
    // newer modal that was opened in the meantime — that was the "window closes by itself" bug.
    const close = () => {
      document.removeEventListener("keydown", onKey);
      if (RMS._modalSeq !== mySeq) return;
      root.hidden = true; root.innerHTML = "";
    };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    $("#modalX").onclick = close;
    root.onclick = (e) => { if (e.target === root) close(); };
    return close;
  };

  /* ── interactive log viewer ──────────────────────────────────────────── */
  RMS.colorizeLog = function (text, query) {
    let html = RMS.esc(text);
    html = html.split("\n").map((line) => {
      let cls = "";
      if (/❌|Traceback|AUTH_FAIL|_LOCKED\]|Error:|Exception:/i.test(line)) cls = "l-err";
      else if (/✅|successfully|Completed|✓/.test(line)) cls = "l-ok";
      else if (/⚠|⏳|WARNING|Retry/i.test(line)) cls = "l-warn";
      else if (/\[[A-Z_]{3,}\]/.test(line)) cls = "l-tag";
      return cls ? '<span class="' + cls + '">' + line + "</span>" : line;
    }).join("\n");
    if (query) {
      const q = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      html = html.replace(new RegExp("(" + q + ")", "gi"), "<mark>$1</mark>");
    }
    return html;
  };

  RMS.openLogViewer = function (entry) {
    // entry: {file, logdir, label, pipe_name} — routes to the right existing endpoint
    const isSA = entry.logdir === "short_allocation";
    const url = isSA
      ? "/sa/log?name=" + encodeURIComponent(entry.file)
      : "/px/log?pipe=" + encodeURIComponent(entry.pipe) + "&name=" + encodeURIComponent(entry.file);
    const close = RMS.modal(entry.pipe_name + " — " + entry.file,
      '<div class="logv-tools">' +
      '<input class="f-input" id="logvSearch" placeholder="Search in log…" style="flex:1">' +
      '<button class="btn sm ghost" id="logvWrap" title="Toggle wrap">' + RMS.icon("wrap") + " Wrap</button>" +
      '<button class="btn sm ghost" id="logvCopy">' + RMS.icon("copy") + " Copy</button>" +
      '<button class="btn sm ghost" id="logvDl">' + RMS.icon("download") + " Save</button></div>" +
      '<div class="logv" id="logvBody">Loading…</div>', { raw: true, icon: "terminal" });
    let raw = "";
    RMS.api(url).then((d) => {
      raw = (d && (d.content || d.text)) || "(empty log)";
      $("#logvBody").innerHTML = RMS.colorizeLog(raw);
      $("#logvBody").scrollTop = $("#logvBody").scrollHeight;
    }).catch(() => { $("#logvBody").textContent = "Could not load log."; });
    $("#logvSearch").oninput = (e) => {
      $("#logvBody").innerHTML = RMS.colorizeLog(raw, e.target.value.trim() || null);
      const m = $("#logvBody").querySelector("mark");
      if (m) m.scrollIntoView({ block: "center" });
    };
    $("#logvWrap").onclick = () => {
      const b = $("#logvBody");
      b.style.whiteSpace = b.style.whiteSpace === "pre" ? "pre-wrap" : "pre";
    };
    $("#logvCopy").onclick = () => {
      navigator.clipboard.writeText(raw).then(() => RMS.toast("ok", "Log copied to clipboard"));
    };
    $("#logvDl").onclick = () => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([raw], { type: "text/plain" }));
      a.download = entry.file;
      a.click();
    };
    return close;
  };

  /* ── hand a question to the AI Assistant from anywhere ───────────────── */
  RMS.assistantAsk = function (q) {
    RMS._pendingAsk = q;
    RMS.go("assistant");
  };

  /* ── file preview (shared by Downloads + Files pages) ────────────────── */
  RMS.previewFile = function (root, sub) {
    const name = sub.split(/[\\/]/).pop();
    const saveUrl = "/ops/file/get?root=" + encodeURIComponent(root) + "&sub=" + encodeURIComponent(sub);
    RMS.modal(name,
      '<div class="logv-tools"><span class="card-sub" id="pvMeta">Loading…</span>' +
      '<div style="flex:1"></div>' +
      '<button class="btn sm ghost" id="pvAsk">' + RMS.icon("sparkles") + " Ask AI</button>" +
      '<a class="btn sm" href="' + saveUrl + '">' + RMS.icon("download") + " Save file</a></div>" +
      '<div class="logv" id="pvBody" style="white-space:pre">Loading…</div>', { raw: true, icon: "file" });
    $("#pvAsk").onclick = () => RMS.assistantAsk(
      "Explain the report file \"" + name + "\" — which process generates it, what do its sheets and " +
      "columns mean, and who receives it?");
    RMS.api("/ops/file/preview?root=" + encodeURIComponent(root) + "&sub=" + encodeURIComponent(sub), { timeout: 15000 })
      .then((d) => {
        $("#pvMeta").textContent = (d.size != null ? RMS.fmtBytes(d.size) + " · " : "") + (d.mtime || "");
        if (d.error) $("#pvBody").textContent = "Preview unavailable: " + d.error;
        else if (d.binary) $("#pvBody").innerHTML =
          '<div class="bm-empty">Binary file (' + RMS.esc(name.split(".").pop().toUpperCase()) +
          ") — no inline preview.<br>Use <b>Save file</b> to download and open it in the right application.</div>";
        else $("#pvBody").innerHTML = RMS.colorizeLog(d.text || "(empty file)") +
          (d.truncated ? '\n<span class="l-warn">… preview truncated (first 256 KB)</span>' : "");
      }).catch(() => { $("#pvBody").textContent = "Could not load preview."; });
  };

  /* ── notifications (bell) ────────────────────────────────────────────── */
  const NKEY = "rms_ops_notifs";
  RMS.notifs = { list: [], unread: 0 };
  try { Object.assign(RMS.notifs, JSON.parse(localStorage.getItem(NKEY) || "{}")); } catch (e) {}
  RMS.notifs.unread = RMS.notifs.unread || 0;
  RMS.notifs.list = RMS.notifs.list || [];

  function saveNotifs() {
    try { localStorage.setItem(NKEY, JSON.stringify({ list: RMS.notifs.list.slice(0, 30), unread: RMS.notifs.unread, seen: RMS.notifs.seen })); } catch (e) {}
    const b = $("#bellBadge");
    if (b) { b.hidden = !RMS.notifs.unread; b.textContent = RMS.notifs.unread > 9 ? "9+" : RMS.notifs.unread; }
  }
  RMS.pushNotif = function (kind, title, sub, entry) {
    RMS.notifs.list.unshift({ kind, title, sub, at: new Date().toISOString(), entry });
    RMS.notifs.unread++;
    saveNotifs();
  };

  /* diff each summary poll against what we've already announced */
  RMS.digestRecent = function (recent) {
    RMS.notifs.seen = RMS.notifs.seen || {};
    const seen = RMS.notifs.seen;
    (recent || []).slice().reverse().forEach((e) => {
      if (e.status !== "ok" && e.status !== "failed" && e.status !== "stopped") return;
      const k = e.file;
      if (seen[k] === e.status) return;
      const isNew = !(k in seen);
      seen[k] = e.status;
      if (isNew && Object.keys(seen).length < 12 && !RMS._notifWarm) return; // first boot: don't replay history
      if (e.status === "failed") {
        // Run status (ok/failed/stopped) ONLY goes to the bell now — no
        // on-screen toast. These fire from background polling, not a direct
        // user action, and a busy day of retries/waits (each one a
        // legitimate, expected "failed" status until the file arrives — see
        // margin_shortage_15min and short-allocation's own wait/retry flows)
        // used to stack up several un-dismissing sticky toasts at once,
        // piling up over the page. Open the bell (top right) to see these —
        // click one there to jump straight to its log, same as the toast
        // used to.
        RMS.pushNotif("err", e.label + " failed", e.pipe_name, e);
      } else if (e.status === "ok" && RMS._notifWarm) {
        RMS.pushNotif("ok", e.label + " completed", e.pipe_name, e);
      }
    });
    RMS._notifWarm = true;
    // trim seen map
    const keys = Object.keys(seen);
    if (keys.length > 300) keys.slice(0, 150).forEach((k) => delete seen[k]);
    saveNotifs();
  };

  RMS.renderBell = function () {
    const menu = $("#bellMenu");
    const items = RMS.notifs.list.slice(0, 20);
    menu.innerHTML = '<div class="bm-head"><span>Notifications</span>' +
      '<button class="btn sm ghost" id="bellClear">Clear</button></div>' +
      '<div class="bm-list">' + (items.length ? items.map((n, i) =>
        '<div class="bm-item" data-i="' + i + '"><span class="feed-ic ' + (n.kind === "err" ? "failed" : n.kind === "ok" ? "ok" : "unknown") + '">' +
        RMS.icon(n.kind === "err" ? "alert" : "check") + "</span><div><b>" + RMS.esc(n.title) + "</b><small>" +
        RMS.esc(n.sub || "") + "</small></div><small>" + RMS.fmtAgo(n.at) + "</small></div>").join("")
        : '<div class="bm-empty">No notifications yet.<br>Run completions and failures appear here.</div>') + "</div>";
    $("#bellClear").onclick = (e) => { e.stopPropagation(); RMS.notifs.list = []; RMS.notifs.unread = 0; saveNotifs(); RMS.renderBell(); };
    menu.querySelectorAll(".bm-item").forEach((el) => {
      el.onclick = () => {
        const n = RMS.notifs.list[+el.dataset.i];
        if (n && n.entry) RMS.openLogViewer(n.entry);
      };
    });
  };

  RMS.initBell = function () {
    const btn = $("#bellBtn"), menu = $("#bellMenu");
    btn.onclick = (e) => {
      e.stopPropagation();
      if (menu.hidden) { RMS.renderBell(); menu.hidden = false; RMS.notifs.unread = 0; saveNotifs(); }
      else menu.hidden = true;
    };
    document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) menu.hidden = true; });
    saveNotifs();
  };

  /* ── signed-in user (avatar → who am I / sign out) ───────────────────── */
  RMS.initUser = function () {
    const btn = $("#userBtn"), menu = $("#userMenu");
    if (!btn || !menu) return;

    fetch("/auth/me").then((r) => r.json()).then((me) => {
      RMS._me = me;
      if (me.signed_in && me.display_name) {
        const initials = me.display_name.trim().split(/\s+/).slice(0, 2)
          .map((w) => w[0]).join("").toUpperCase() || "?";
        btn.textContent = initials;
        btn.title = me.display_name + " (" + me.employee_id + ")" + (me.is_admin ? " — Admin" : "");
      }
      // Not signed in (or auth not enforced and nobody ever logged in): leave
      // the default "MT" avatar as-is — nothing to sign out of.
    }).catch(() => {});

    btn.onclick = (e) => {
      e.stopPropagation();
      if (!menu.hidden) { menu.hidden = true; return; }
      const me = RMS._me || {};
      if (me.signed_in) {
        menu.innerHTML =
          '<div class="um-head"><span class="um-name">' + RMS.esc(me.display_name) + '</span>' +
          '<span class="um-id">' + RMS.esc(me.employee_id) + '</span>' +
          (me.is_admin ? '<span style="display:inline-block;margin-top:6px;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:800;background:rgba(251,191,36,.18);color:#fbbf24;border:1px solid rgba(251,191,36,.5)">ADMIN</span>' : "") + '</div>' +
          '<div class="um-body"><button class="um-signout" id="umSignOut">Sign out</button></div>';
        $("#umSignOut").onclick = () => {
          fetch("/auth/logout", { method: "POST" }).finally(() => { location.href = "/login"; });
        };
      } else {
        menu.innerHTML = '<div class="um-note">' +
          (me.auth_required ? "Not signed in." : "Sign-in isn't required on this server yet.") +
          "</div>";
      }
      menu.hidden = false;
    };
    document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) menu.hidden = true; });
  };

  /* ── command palette ─────────────────────────────────────────────────── */
  RMS.palette = { items: [], open: false };

  RMS.paletteItems = function () {
    const nav = [
      { sec: "Pages", icon: "dashboard", label: "Dashboard", hint: "G D", run: () => RMS.go("dashboard") },
      { sec: "Pages", icon: "play", label: "Process Center", hint: "G P", run: () => RMS.go("process") },
      { sec: "Pages", icon: "history", label: "Execution History", hint: "G H", run: () => RMS.go("history") },
      { sec: "Pages", icon: "chart", label: "Analytics", run: () => RMS.go("analytics") },
      { sec: "Pages", icon: "file", label: "Reports", run: () => RMS.go("reports") },
      { sec: "Pages", icon: "bot", label: "AI Assistant", hint: "G A", run: () => RMS.go("assistant") },
      { sec: "Pages", icon: "calendar", label: "Scheduler", run: () => RMS.go("scheduler") },
      { sec: "Pages", icon: "download", label: "Downloads", run: () => RMS.go("downloads") },
      { sec: "Pages", icon: "folder", label: "Files", run: () => RMS.go("files") },
      { sec: "Pages", icon: "mail", label: "Email Center", run: () => RMS.go("email") },
      { sec: "Pages", icon: "settings", label: "Settings", hint: "G S", run: () => RMS.go("settings") },
      { sec: "Actions", icon: "refresh", label: "Refresh AI briefing", run: () => { RMS.go("dashboard"); document.dispatchEvent(new CustomEvent("rms:refresh-brief")); } },
      { sec: "Actions", icon: "external", label: "Open legacy console (standalone)", run: () => window.open("/legacy", "_blank") },
      { sec: "Actions", icon: "external", label: "Open API docs (Swagger)", run: () => window.open("/docs", "_blank") },
      { sec: "Actions", icon: "copy", label: "Copy server URL", run: () => { navigator.clipboard.writeText(location.origin); RMS.toast("ok", "Server URL copied", location.origin); } },
      { sec: "Actions", icon: "alert", label: "View latest failed log", run: () => {
          const f = (RMS._histCache || []).find((e) => e.status === "failed");
          if (f) RMS.openLogViewer(f); else RMS.toast("info", "No failed runs found", "Nothing failed recently 🎉");
        } },
    ];
    RMS.THEMES.forEach((t) => nav.push({ sec: "Theme", icon: "moon", label: "Theme: " + t.name, run: () => { RMS.savePrefs({ theme: t.id }); RMS.toast("ok", t.name + " theme applied"); } }));
    (RMS._histCache || []).slice(0, 6).forEach((e) => nav.push({
      sec: "Recent logs", icon: "terminal", label: e.label + " — " + e.pipe_name,
      hint: (e.start || "").slice(11, 16), run: () => RMS.openLogViewer(e),
    }));
    return nav;
  };

  function fuzzy(q, s) {
    q = q.toLowerCase(); s = s.toLowerCase();
    if (!q) return 1;
    if (s.includes(q)) return 100 - s.indexOf(q);
    let i = 0, score = 0;
    for (const c of s) { if (c === q[i]) { i++; score += 2; } }
    return i === q.length ? score : 0;
  }

  RMS.openPalette = function () {
    const wrap = $("#paletteWrap"), input = $("#palInput"), list = $("#palList");
    wrap.hidden = false; RMS.palette.open = true;
    input.value = ""; input.focus();
    let sel = 0, shown = [], kbItems = [], kbTimer = null;
    const KB_ICO = { output: "file", client: "bot", process: "layers", code: "terminal" };
    function kbFetch(q) {
      RMS.api("/kb/search?q=" + encodeURIComponent(q), { quiet: true, timeout: 8000 }).then((d) => {
        if (input.value.trim() !== q) return;   // stale response
        kbItems = (d.hits || []).slice(0, 10).map((h) => ({
          sec: "Knowledge", icon: KB_ICO[h.type] || "info",
          label: h.label + (h.detail ? " — " + h.detail : ""),
          run: () => {
            if (h.type === "output" && h.root) RMS.previewFile(h.root, h.sub);
            else if (h.type === "client") RMS.assistantAsk("Show all reports containing client " + h.value);
            else if (h.type === "process") RMS.assistantAsk("Explain the " + h.label + " process end to end");
            else if (h.type === "code") RMS.assistantAsk("Explain the code file " + h.path + " — what does it do and which process uses it?");
          },
        }));
        render();
      }).catch(() => {});
    }
    function render() {
      const q = input.value.trim();
      const all = RMS.paletteItems()
        .map((it) => ({ ...it, score: fuzzy(q, it.label) }))
        .filter((it) => it.score > 0)
        .sort((a, b) => b.score - a.score);
      shown = q ? all.concat(kbItems) : RMS.paletteItems();
      sel = Math.min(sel, Math.max(0, shown.length - 1));
      let html = "", lastSec = null;
      if (!shown.length) html = '<div class="pal-empty">No matches for “' + RMS.esc(q) + '”.</div>';
      shown.forEach((it, i) => {
        if (it.sec !== lastSec && !q) { html += '<div class="pal-sec">' + it.sec + "</div>"; lastSec = it.sec; }
        html += '<div class="pal-item' + (i === sel ? " sel" : "") + '" data-i="' + i + '">' +
          RMS.icon(it.icon) + "<span>" + RMS.esc(it.label) + "</span>" +
          (it.hint ? "<small>" + it.hint + "</small>" : "") + "</div>";
      });
      list.innerHTML = html;
      const s = list.querySelector(".sel");
      if (s) s.scrollIntoView({ block: "nearest" });
      list.querySelectorAll(".pal-item").forEach((el) => {
        el.onmousedown = (e) => { e.preventDefault(); pick(+el.dataset.i); };
        el.onmousemove = () => { if (sel !== +el.dataset.i) { sel = +el.dataset.i; render(); } };
      });
    }
    function pick(i) { const it = shown[i]; RMS.closePalette(); if (it) setTimeout(() => it.run(), 10); }
    input.oninput = () => {
      sel = 0;
      const q = input.value.trim();
      clearTimeout(kbTimer);
      if (q.length >= 3) kbTimer = setTimeout(() => kbFetch(q), 250);
      else kbItems = [];
      render();
    };
    input.onkeydown = (e) => {
      if (e.key === "ArrowDown") { sel = Math.min(sel + 1, shown.length - 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
      else if (e.key === "Enter") pick(sel);
      else if (e.key === "Escape") RMS.closePalette();
    };
    wrap.onclick = (e) => { if (e.target === wrap) RMS.closePalette(); };
    render();
  };
  RMS.closePalette = function () { $("#paletteWrap").hidden = true; RMS.palette.open = false; };
})();
