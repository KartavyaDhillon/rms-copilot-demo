/* pages/settings.js — theme engine UI + platform info.
   Every control writes straight into RMS.prefs (localStorage) and applies live. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$, esc = RMS.esc;
  let booted = false;

  function sw(on) { return '<button class="switch' + (on ? " on" : "") + '"></button>'; }

  function shell() {
    const p = RMS.prefs;
    return (
      '<div class="set-grid">' +

      /* ── appearance ── */
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("moon") + " Theme</div>" +
      '<span class="card-sub">applies instantly, saved to this browser</span></div>' +
      '<div class="theme-gallery" id="themeGal">' +
      RMS.THEMES.map((t) =>
        '<div class="theme-tile' + (p.theme === t.id ? " on" : "") + '" data-t="' + t.id + '">' +
        '<div class="theme-swatch" style="background:' + t.bg + '"><i style="background:' + t.accent + '"></i></div>' +
        "<b>" + t.name + "</b></div>").join("") +
      "</div>" +
      '<div class="sec-head" style="margin:18px 2px 8px"><h3>Accent color</h3><span>overrides the theme accent</span></div>' +
      '<div class="accent-row" id="accRow">' +
      '<span class="acc-dot' + (!p.accent ? " on" : "") + '" data-a="" title="Theme default" style="background:conic-gradient(from 0deg,#60a5fa,#34d399,#a78bfa,#60a5fa)"></span>' +
      RMS.ACCENTS.map((a) => '<span class="acc-dot' + (p.accent === a ? " on" : "") + '" data-a="' + a + '" style="background:' + a + '"></span>').join("") +
      '<input type="color" id="accCustom" value="' + (p.accent || "#60a5fa") + '" title="Custom accent" style="width:34px;height:28px;border:0;background:none;cursor:pointer">' +
      "</div></div>" +

      /* ── density / motion ── */
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("settings") + " Interface</div></div>" +
      '<div class="set-row"><div class="s-l"><b>Font size</b><small>base text size across the platform</small></div>' +
      '<div class="seg" id="fontSeg">' +
      [["13", "Small"], ["14", "Default"], ["15", "Large"]].map(([v, n]) =>
        '<button data-v="' + v + '"' + (String(p.fontSize) === v ? ' class="on"' : "") + ">" + n + "</button>").join("") +
      "</div></div>" +
      '<div class="set-row"><div class="s-l"><b>Card radius</b><small>roundness of cards & buttons — <span id="radVal">' + p.radius + 'px</span></small></div>' +
      '<input type="range" class="slider" id="radSlider" min="6" max="22" step="1" value="' + p.radius + '"></div>' +
      '<div class="set-row"><div class="s-l"><b>Compact mode</b><small>tighter paddings, more on screen</small></div><span id="swCompact">' + sw(p.compact) + "</span></div>" +
      '<div class="set-row"><div class="s-l"><b>Animations</b><small>transitions, pulses, typewriter effects</small></div><span id="swAnim">' + sw(p.anim !== false) + "</span></div>" +
      '<div class="set-row"><div class="s-l"><b>Sidebar</b><small>full labels or icon rail</small></div>' +
      '<div class="seg" id="sideSeg"><button data-v="full"' + (p.sidebar !== "rail" ? ' class="on"' : "") + '>Full</button>' +
      '<button data-v="rail"' + (p.sidebar === "rail" ? ' class="on"' : "") + ">Rail</button></div></div>" +
      '<div class="set-row"><div class="s-l"><b>Reset appearance</b><small>back to defaults (Dark, 14px)</small></div>' +
      '<button class="btn sm" id="resetPrefs">Reset</button></div></div>' +

      /* ── identity ── */
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("bot") + " Identity</div>" +
      '<span class="card-sub">shown to teammates when you start a run</span></div>' +
      '<div class="set-row"><div class="s-l"><b>Your name</b><small>appears in lock banners, the audit trail and AI answers — e.g. "RUNNING · started by Kartavya"</small></div>' +
      '<input class="f-input" id="userName" style="min-width:150px" placeholder="e.g. Kartavya" value="' + esc(localStorage.getItem("rms_user_name") || "") + '"></div>' +
      '<div class="card-sub">Saved per browser. Sent automatically with every run you start (cookie <span class="mono">rms_user</span>).</div></div>' +

      /* ── notifications ── */
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("bell") + " Notifications</div></div>" +
      '<div class="set-row"><div class="s-l"><b>Success toasts</b><small>pop a toast when a run completes cleanly</small></div>' +
      '<span id="swNotifOk">' + sw(p.notifySuccess !== false) + "</span></div>" +
      '<div class="set-row"><div class="s-l"><b>Failure toasts</b><small>always on — failures stay until clicked (opens the log)</small></div>' +
      '<span class="badge err">always on</span></div>' +
      '<div class="set-row"><div class="s-l"><b>Notification history</b><small>recent completions & failures in the bell menu</small></div>' +
      '<button class="btn sm ghost" id="clearNotifs">Clear history</button></div></div>' +

      /* ── platform ── */
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("zap") + " Platform</div></div>" +
      '<div class="set-row"><div class="s-l"><b>Server</b><small class="mono">' + esc(location.origin) + "</small></div>" +
      '<button class="btn sm ghost" id="copyUrl">' + RMS.icon("copy") + " Copy</button></div>" +
      '<div class="set-row"><div class="s-l"><b>Legacy console</b><small>the original single-page dashboard, unchanged</small></div>' +
      '<a class="btn sm ghost" href="/legacy" target="_blank">' + RMS.icon("external") + " Open</a></div>" +
      '<div class="set-row"><div class="s-l"><b>API documentation</b><small>FastAPI auto-generated Swagger UI</small></div>' +
      '<a class="btn sm ghost" href="/docs" target="_blank">' + RMS.icon("external") + " /docs</a></div>" +
      '<div class="set-row"><div class="s-l"><b>Keyboard shortcuts</b><small>Ctrl+K palette · G then D/P/H/S to jump</small></div>' +
      '<button class="btn sm ghost" id="showKeys">View</button></div>' +
      '<div class="set-row"><div class="s-l"><b>Version</b><small>AI Operations Platform · Phase 2</small></div>' +
      '<span class="badge info">v2.1</span></div></div>' +

      /* ── about ── */
      '<div class="card"><div class="card-head"><div class="card-title">' + RMS.icon("info") + " About RMS Copilot</div></div>" +
      '<p style="font-size:12.5px;color:var(--text-2);line-height:1.7">The AI Operations Platform for the mastertrust RMS team. ' +
      "It runs and monitors the four production pipelines — <b>CTCL vs NMASS</b>, <b>Short Allocation</b>, " +
      "<b>Margin Shortage (15-min)</b> and <b>Client Prudential Limit</b> — with live dashboards, execution history, " +
      "analytics, reports and an AI assistant grounded in real platform state.</p>" +
      '<div class="set-row" style="margin-top:8px"><div class="s-l"><b>Modules</b><small>Dashboard · Process Center · History · Analytics · Reports · AI Assistant · Scheduler · Downloads · Files · Email Center</small></div></div>' +
      '<div class="set-row"><div class="s-l"><b>Backend</b><small>FastAPI + Python pipelines · read-only /ops data layer · zero-build frontend served by uvicorn</small></div></div>' +
      '<div class="set-row"><div class="s-l"><b>AI</b><small>Groq (llama-3.1-8b-instant) for briefings, error explanations and the assistant — with deterministic fallbacks</small></div></div></div>' +

      "</div>"
    );
  }

  function bind(el) {
    el.querySelectorAll("#themeGal .theme-tile").forEach((t) => {
      t.onclick = () => { RMS.savePrefs({ theme: t.dataset.t }); refresh(el); };
    });
    el.querySelectorAll("#accRow .acc-dot").forEach((d) => {
      d.onclick = () => { RMS.savePrefs({ accent: d.dataset.a }); refresh(el); };
    });
    $("#accCustom").oninput = (e) => RMS.savePrefs({ accent: e.target.value });
    $("#fontSeg").querySelectorAll("button").forEach((b) => {
      b.onclick = () => { RMS.savePrefs({ fontSize: +b.dataset.v }); refresh(el); };
    });
    $("#sideSeg").querySelectorAll("button").forEach((b) => {
      b.onclick = () => { RMS.savePrefs({ sidebar: b.dataset.v }); refresh(el); };
    });
    $("#radSlider").oninput = (e) => { $("#radVal").textContent = e.target.value + "px"; RMS.savePrefs({ radius: +e.target.value }); };
    $("#swCompact").onclick = () => { RMS.savePrefs({ compact: !RMS.prefs.compact }); refresh(el); };
    $("#swNotifOk").onclick = () => { RMS.savePrefs({ notifySuccess: RMS.prefs.notifySuccess === false }); refresh(el); };
    $("#userName").onchange = (e) => {
      const name = e.target.value.trim().slice(0, 40);
      localStorage.setItem("rms_user_name", name);
      document.cookie = "rms_user=" + encodeURIComponent(name) + "; path=/; max-age=31536000; SameSite=Lax";
      RMS.toast("ok", name ? "You'll appear as “" + name + "”" : "Name cleared", "Applies to runs you start from now on.");
    };
    $("#clearNotifs").onclick = () => { RMS.notifs.list = []; RMS.notifs.unread = 0; RMS.toast("ok", "Notification history cleared"); };
    $("#swAnim").onclick = () => { RMS.savePrefs({ anim: RMS.prefs.anim === false }); refresh(el); };
    $("#resetPrefs").onclick = () => { RMS.resetPrefs(); refresh(el); RMS.toast("ok", "Appearance reset to defaults"); };
    $("#copyUrl").onclick = () => { navigator.clipboard.writeText(location.origin); RMS.toast("ok", "Server URL copied"); };
    $("#showKeys").onclick = () =>
      RMS.modal("Keyboard shortcuts",
        '<div style="display:grid;grid-template-columns:auto 1fr;gap:10px 18px;font-size:13px;align-items:center">' +
        "<kbd>Ctrl K</kbd><span>Open command palette (search pages, actions, themes, logs)</span>" +
        "<kbd>G D</kbd><span>Go to Dashboard</span><kbd>G P</kbd><span>Go to Process Center</span>" +
        "<kbd>G H</kbd><span>Go to Execution History</span><kbd>G S</kbd><span>Go to Settings</span>" +
        "<kbd>Esc</kbd><span>Close palette / modal / notifications</span></div>", { icon: "terminal", width: 560 });
  }

  function refresh(el) { el.innerHTML = shell(); RMS.mountIcons(el); bind(el); }

  RMS.pages = RMS.pages || {};
  RMS.pages.settings = {
    title: "Settings", crumb: "Appearance, themes and platform info",
    mount(el) { refresh(el); booted = true; },
  };
})();
