/* app.js — router + shell wiring + global keyboard shortcuts + market clock. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$;
  const ROUTES = ["dashboard", "process", "history", "analytics", "reports",
                  "assistant", "scheduler", "downloads", "files", "email", "settings"];

  RMS.go = function (route) { location.hash = "#/" + route; };

  function currentRoute() {
    const h = (location.hash || "").replace(/^#\/?/, "");
    return ROUTES.includes(h) ? h : "dashboard";
  }

  function navigate() {
    const r = currentRoute();
    ROUTES.forEach((name) => {
      const page = $("#page-" + name);
      if (!page) return;
      if (name === r) {
        page.hidden = false;
        const def = RMS.pages[name];
        if (def) {
          try {
            def.mount(page);
            $("#topTitle").textContent = def.title;
            $("#topCrumb").textContent = def.crumb || "";
          } catch (err) {
            // NEVER a silent blank page: render the crash where the page
            // would be, with the exact error — instantly diagnosable.
            console.error("[RMS] page '" + name + "' crashed:", err);
            page.innerHTML = '<div class="card" style="border-color:var(--err);max-width:720px;margin:40px auto">' +
              '<div class="card-title" style="color:var(--err);margin-bottom:8px">⚠ This page failed to render</div>' +
              '<div style="font-size:12.5px;color:var(--text-2);margin-bottom:10px">Page: <b>' + name + "</b></div>" +
              '<pre class="mono" style="font-size:11.5px;white-space:pre-wrap;color:var(--err)">' +
              String(err && (err.stack || err.message || err)).replace(/[<>]/g, "") + "</pre>" +
              '<div style="font-size:12px;color:var(--text-3);margin-top:10px">Try a hard refresh (Ctrl+Shift+R). ' +
              "If it persists, screenshot this box — it names the exact file and line.</div></div>";
            $("#topTitle").textContent = def.title || name;
          }
        } else {
          // page module never registered (its script failed to load/parse)
          const diag = (RMS._modDiag && RMS._modDiag[name]) || "diagnostics running — check the toast / revisit this page in a moment";
          const early = (window.__jsErrors || []).join("\n") || "(no early script errors captured)";
          page.innerHTML = '<div class="card" style="border-color:var(--warn);max-width:760px;margin:40px auto">' +
            '<div class="card-title" style="color:var(--warn);margin-bottom:8px">⚠ Page module not loaded</div>' +
            '<div style="font-size:12.5px;color:var(--text-2);margin-bottom:10px">The script for <b>' + name + "</b> did not register.</div>" +
            '<div style="font-size:11px;color:var(--text-3);margin-bottom:4px">DIAGNOSTIC</div>' +
            '<pre class="mono" style="font-size:11.5px;white-space:pre-wrap;color:var(--warn);margin-bottom:10px">' + diag.replace(/[<>]/g, "") + "</pre>" +
            '<div style="font-size:11px;color:var(--text-3);margin-bottom:4px">SCRIPT ERRORS THIS SESSION</div>' +
            '<pre class="mono" style="font-size:11.5px;white-space:pre-wrap;color:var(--err)">' + early.replace(/[<>]/g, "") + "</pre>" +
            '<div style="font-size:12px;color:var(--text-3);margin-top:10px">Screenshot this box — it contains the exact cause.</div></div>';
          $("#topTitle").textContent = name;
          $("#topCrumb").textContent = "module failed to load";
        }
        // re-trigger the page entrance animation (except keep-alive iframes)
        if (!def || !def.keepAlive) { page.style.animation = "none"; void page.offsetWidth; page.style.animation = ""; }
      } else {
        page.hidden = true;   // hidden, NOT destroyed — Process Center's iframe keeps running
      }
    });
    document.querySelectorAll(".nav-item[data-route]").forEach((a) => {
      a.classList.toggle("active", a.dataset.route === r);
    });
  }

  /* ── market hours chips (NSE + MCX, machine local time = IST) ─────────── */
  function marketChips() {
    const d = new Date();
    const mins = d.getHours() * 60 + d.getMinutes();
    const wd = d.getDay() >= 1 && d.getDay() <= 5;
    const nse = wd && mins >= 555 && mins < 930;          // 09:15–15:30
    const nsePre = wd && mins >= 540 && mins < 555;       // 09:00–09:15
    const mcx = wd && mins >= 540 && mins < 1410;         // 09:00–23:30
    $("#marketChips").innerHTML =
      '<span class="mkt-chip ' + (nse ? "open" : "closed") + '"><span class="dot"></span>NSE ' + (nse ? "OPEN" : nsePre ? "PRE-OPEN" : "CLOSED") + "</span>" +
      '<span class="mkt-chip ' + (mcx ? "open" : "closed") + '"><span class="dot"></span>MCX ' + (mcx ? "OPEN" : "CLOSED") + "</span>";
  }

  function clock() {
    const el = $("#tbClock");
    if (el) el.textContent = new Date().toTimeString().slice(0, 8);
  }

  /* ── global keyboard shortcuts ────────────────────────────────────────── */
  let gPressed = false, gTimer = null;
  function onKey(e) {
    const inField = /INPUT|TEXTAREA|SELECT/.test((e.target.tagName || "")) || e.target.isContentEditable;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      RMS.palette.open ? RMS.closePalette() : RMS.openPalette();
      return;
    }
    if (inField || RMS.palette.open || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key.toLowerCase() === "g") {
      gPressed = true;
      clearTimeout(gTimer);
      gTimer = setTimeout(() => (gPressed = false), 900);
      return;
    }
    if (gPressed) {
      const map = { d: "dashboard", p: "process", h: "history", s: "settings", a: "assistant" };
      const r = map[e.key.toLowerCase()];
      if (r) { RMS.go(r); e.preventDefault(); }
      gPressed = false;
    }
  }

  /* ── boot ─────────────────────────────────────────────────────────────── */
  function boot() {
    // surface ANY uncaught error as a visible sticky toast instead of a
    // silently broken/blank UI (message includes file:line for screenshots)
    window.addEventListener("error", (e) => {
      try {
        RMS.toast("err", "Script error: " + (e.message || "unknown"),
          (e.filename || "").split("/").pop() + ":" + (e.lineno || "?"), { sticky: true });
      } catch (_) {}
    });
    window.addEventListener("unhandledrejection", (e) => {
      try {
        const m = String((e.reason && (e.reason.message || e.reason)) || "unknown");
        if (!/abort/i.test(m)) RMS.toast("warn", "Unhandled promise rejection", m.slice(0, 140));
      } catch (_) {}
    });

    RMS.mountIcons(document);
    RMS.applyPrefs();
    RMS.initBell();
    RMS.initUser();

    // verify every page module registered — and if one didn't, DIAGNOSE and
    // SELF-HEAL: re-fetch the script ourselves, try to execute it, and either
    // recover the page live or report the exact failure (HTTP status, byte
    // count, JS error message, plus any early parse errors captured in <head>).
    const FILE_FOR = { assistant: "ask" };   // assistant module ships as ask.js
    RMS._modDiag = {};
    ROUTES.filter((r) => !RMS.pages[r]).forEach((name) => {
      (async () => {
        const url = "/platform/js/pages/" + (FILE_FOR[name] || name) + ".js?diag=" + Date.now();
        let info;
        try {
          const resp = await fetch(url, { cache: "no-store" });
          const text = await resp.text();
          info = "HTTP " + resp.status + " · " + text.length + " bytes";
          try {
            new Function(text)();               // evaluate it right now
            if (RMS.pages[name]) {              // …and it registered → healed
              RMS.toast("ok", "'" + name + "' module recovered",
                "Loaded on retry (" + info + "). Something interfered with the original load.");
              navigate();
              return;
            }
            info += " · executed but did not register";
          } catch (e2) {
            info += " · JS error: " + (e2.message || e2);
          }
        } catch (e1) {
          info = "fetch failed: " + (e1.message || e1) + " · likely blocked by an extension or AV";
        }
        const early = (window.__jsErrors || []).slice(0, 3).join(" | ");
        if (early) info += " · earlier errors: " + early;
        RMS._modDiag[name] = info;
        RMS.toast("err", "Module '" + name + "' failed", info, { sticky: true });
        console.error("[RMS] module diagnostic:", name, info);
      })();
    });

    $("#searchBtn").onclick = () => RMS.openPalette();
    $("#themeBtn").onclick = () => RMS.toast("ok", RMS.cycleTheme() + " theme");
    $("#railToggle").onclick = () => RMS.savePrefs({ sidebar: RMS.prefs.sidebar === "rail" ? "full" : "rail" });
    document.querySelector(".brand").onclick = () => RMS.go("dashboard");
    document.querySelectorAll(".nav-item.soon").forEach((a) => {
      a.onclick = (e) => {
        e.preventDefault();
        RMS.toast("info", a.querySelector("span").textContent + " — coming in Phase 2",
          "The core platform ships first; this module is on the roadmap.");
      };
    });

    // Clicking the menu item of the page you are already on does not change the URL, so nothing happened.
    // A page that defines reselect(el) (the Scheduler does) now refreshes itself and returns to its first tab.
    document.querySelectorAll(".nav-item[data-route]").forEach((a) => {
      a.addEventListener("click", () => {
        const r = a.dataset.route;
        if (r !== currentRoute()) return;
        const def = RMS.pages[r], page = $("#page-" + r);
        if (def && typeof def.reselect === "function" && page) {
          try { def.reselect(page); window.scrollTo(0, 0); } catch (err) { console.error("[RMS] reselect failed:", err); }
        }
      });
    });

    document.addEventListener("keydown", onKey);
    window.addEventListener("hashchange", navigate);

    clock(); setInterval(clock, 1000);
    marketChips(); setInterval(marketChips, 30000);

    // background summary poll even when not on the Dashboard — keeps the bell,
    // sidebar run-dot and server pill live from anywhere in the app.
    RMS.poll(() => {
      RMS.api("/ops/summary", { quiet: false }).then((s) => {
        RMS.digestRecent(s.recent || []);
        const dot = $("#navRunDot");
        if (dot) dot.hidden = !(s.running && s.running.length);
      }).catch(() => {});
    }, 20000);
    // warm the history cache so the palette's "Recent logs" works immediately
    RMS.api("/ops/history?days=2", { quiet: true, timeout: 20000 })
      .then((d) => { RMS._histCache = d.items || []; }).catch(() => {});

    // live global lock stream — the server is the source of truth for what is
    // running anywhere; every page (and the embedded console via its own
    // injected overlay) reacts instantly, no refresh needed.
    RMS.locks = [];
    (function lockStream() {
      let known = {};
      try {
        const es = new EventSource("/ops/locks/stream");
        es.onmessage = (m) => {
          let snap; try { snap = JSON.parse(m.data); } catch (e) { return; }
          const locks = snap.locks || [];
          locks.forEach((l) => {
            if (!known[l.key + l.started_at] && Object.keys(known).length) {
              // Was an unconditional RMS.toast() — every run start, anywhere on
              // the console, by anyone, popped a toast for every viewer. With
              // several pipelines running many times a day that made the toast
              // stack essentially constant. Record it in the bell (history)
              // only now; nothing pops up unless the user opens the bell menu.
              RMS.pushNotif("info", l.label + " started",
                "by " + (l.by || "unknown user") + (l.ip ? " · " + l.ip : ""));
            }
          });
          known = {};
          locks.forEach((l) => (known[l.key + l.started_at] = 1));
          if (!Object.keys(known).length) known = { _empty: 1 };
          RMS.locks = locks;
          document.dispatchEvent(new CustomEvent("rms:locks"));
        };
      } catch (e) {}
    })();

    navigate();
  }

  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", boot) : boot();
})();
