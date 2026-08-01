/* process-locks.js — GLOBAL PROCESS LOCK overlay for the embedded console.
   Injected at serve time by /legacy?embed=1 (dashboard.html on disk is never
   modified — delete this file or the inject line to remove the layer).

   What it does:
     * subscribes to /ops/locks/stream (SSE, auto-reconnecting) — the server's
       DERIVED lock state (who is running what, from which machine, since when)
     * when a lock is active and THIS tab is not the one that started it,
       disables the affected boxes' action buttons and shows a live RUNNING
       strip (started by / machine / started at / elapsed)
     * re-applies itself after the console's own render cycles (it wraps
       pxRenderRuns/saRenderRuns), and releases everything the moment the
       server says the lock is gone — no refresh needed anywhere.

   Granularity comes from the SERVER ("affects" list per lock): ctcl/ms15/cli
   share one execution slot so they lock together; Short Allocation is
   independent and only locks itself. Stop / Logs / View stay ENABLED — an
   operator must always be able to stop a run or read output. */
(function () {
  "use strict";
  var LOCKS = [];
  var TICKER = null;

  /* ── styles ─────────────────────────────────────────────────────────── */
  var st = document.createElement("style");
  st.textContent =
    ".lm-strip{display:flex;align-items:center;gap:10px;margin:0 0 12px;padding:9px 13px;" +
    "border:1px solid rgba(96,165,250,.35);border-radius:11px;background:rgba(96,165,250,.09);" +
    "font-size:12px;color:var(--ink,#e9eef6);animation:lmIn .25s ease}" +
    "@keyframes lmIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1}}" +
    ".lm-dot{width:9px;height:9px;border-radius:50%;background:#60a5fa;flex:none;" +
    "box-shadow:0 0 0 3px rgba(96,165,250,.25);animation:lmPulse 1.4s infinite}" +
    "@keyframes lmPulse{50%{opacity:.35}}" +
    ".lm-strip b{font-weight:700}.lm-strip .lm-meta{color:var(--muted,#93a0b4);margin-left:auto;" +
    "font-family:var(--mono,monospace);font-size:11px;white-space:nowrap}" +
    ".lm-badge{font-size:9px;font-weight:800;letter-spacing:.08em;padding:2px 8px;border-radius:12px;" +
    "background:rgba(96,165,250,.18);color:#8fc2ff;border:1px solid rgba(96,165,250,.35)}" +
    "button[data-lm-dis='1']{opacity:.45!important;cursor:not-allowed!important}" +
    /* The console's green "ready — please run it now" call-to-action banner
       (.sa-banner) restyled to a neutral blue "someone else is running this"
       notice. Applied by class, so removing the class restores the original
       green styling exactly — nothing is overwritten permanently. */
    ".sa-banner.lm-busy{background:rgba(96,165,250,.09)!important;" +
    "border-color:rgba(96,165,250,.35)!important;color:#8fc2ff!important}" +
    ".sa-banner.lm-busy .dot{background:#60a5fa!important;box-shadow:0 0 8px #60a5fa!important}";
  document.head.appendChild(st);

  /* ── helpers ────────────────────────────────────────────────────────── */
  function localBusy(subsys, target) {
    // The initiating tab manages its own buttons/spinners — never fight it, and
    // never tell someone their OWN action is "running on another screen".
    try {
      if (subsys === "sa") return typeof _saRunning !== "undefined" && _saRunning;

      // Full pipeline runs set both flags, and _pxRunningPipe says WHICH box —
      // so a local CTCL run no longer suppresses a genuine MS15 lock strip from
      // another screen (it used to suppress every px box indiscriminately).
      if (typeof _pxRunning !== "undefined" && _pxRunning) {
        if (typeof _pxRunningPipe !== "undefined" && _pxRunningPipe && target) {
          if (_pxRunningPipe === target) return true;
        } else {
          return true;
        }
      }

      // THE FIX: single-step actions — CLI's "CM/FO/CO/NCDEX Download" and "MCX
      // Web Allocation", MS15's NMASS/NCDEX and MCX 90% buttons — go through
      // _cliRunOneStep()/equivalents, which set _pxES (this tab's EventSource)
      // but deliberately NOT _pxRunning (that flag drives run-tile state, and a
      // one-off download is not a run). localBusy() only knew about _pxRunning,
      // so the backend lock those downloads legitimately take looked like it
      // belonged to somebody else: the operator clicked Download and was told
      // their own click was "running on another screen by unknown user", with
      // the whole box greyed out. An open EventSource in THIS tab means the
      // action is ours.
      if (typeof _pxES !== "undefined" && _pxES) return true;
    } catch (e) { return false; }
    return false;
  }

  function boxEl(target) {
    return document.getElementById(target === "sa" ? "saBox" : "pxBox-" + target);
  }

  var KEEP = /⛔|stop|logs|🗂|view|📄|explain|❔/i;

  function disableBox(box) {
    box.querySelectorAll("button").forEach(function (b) {
      if (b.dataset.lmDis) return;
      if (KEEP.test(b.textContent || "") || KEEP.test(b.id || "")) return;
      if (b.disabled) return;             // already disabled by the console itself
      b.disabled = true;
      b.dataset.lmDis = "1";
    });
  }

  function enableBox(box) {
    box.querySelectorAll("button[data-lm-dis='1']").forEach(function (b) {
      b.disabled = false;
      delete b.dataset.lmDis;
    });
  }

  function fmtElapsed(startedIso) {
    if (!startedIso) return "";
    var s = Math.max(0, Math.floor((Date.now() - new Date(startedIso).getTime()) / 1000));
    var m = Math.floor(s / 60);
    return (m ? m + "m " : "") + (s % 60) + "s";
  }

  function stripHTML(lock) {
    var t = (lock.started_at || "").slice(11, 19);
    return '<span class="lm-dot"></span><span class="lm-badge">RUNNING</span>' +
      "<span><b>" + (lock.label || "A process") + "</b> is running on another screen</span>" +
      '<span class="lm-meta">by <b>' + (lock.by || "unknown user") + "</b>" +
      (lock.ip ? " · " + lock.ip : "") + (t ? " · started " + t : "") +
      ' · <span class="lm-el" data-start="' + (lock.started_at || "") + '">' +
      fmtElapsed(lock.started_at) + "</span></span>";
  }

  /* ── the "ready — please run it now" banner ─────────────────────────────
     dashboard.html's pxRenderRuns()/saRenderRuns() light up a green banner
     ("MS15-02 is ready — please run it now") whenever a run window is open and
     THIS TAB isn't running anything: `if (due && !_pxRunning)`. _pxRunning is
     purely local — it's only true in the tab that pressed Run. So on everybody
     ELSE's screen the condition still holds while a colleague is mid-run, and
     they keep being told to run something that is already running and whose
     button is (correctly) locked. That contradiction is exactly what makes
     people think the console is stuck.

     Since dashboard.html can't be edited, this rewrites the banner in place
     whenever a lock covers that box, and restores it the moment the lock
     clears. Deliberately rewritten rather than hidden: an empty space where a
     banner used to be tells the operator nothing, whereas naming who is running
     it and since when answers the question they actually have. The original
     text is stashed on the element first, so nothing is lost.               */
  function bannerEl(target) {
    return document.getElementById(target === "sa" ? "saBanner" : "pxBanner-" + target);
  }

  function bannerTextEl(target, ban) {
    return target === "sa"
      ? document.getElementById("saBannerText")
      : ban.querySelector(".t");
  }

  function banBusy(target, lock) {
    var ban = bannerEl(target);
    if (!ban) return;
    var t = bannerTextEl(target, ban);
    if (!t) return;
    // Remember the console's own wording once, so restore is exact.
    if (ban.dataset.lmPrev === undefined) ban.dataset.lmPrev = t.textContent || "";
    var who = lock.by && lock.by !== "unknown user" ? lock.by : "someone else";
    var msg = (lock.label || "This process") + " is already running — started by " +
              who + (lock.started_at ? " at " + lock.started_at.slice(11, 16) : "") +
              ". Please wait for it to finish.";
    if (t.textContent !== msg) t.textContent = msg;
    ban.classList.add("show", "lm-busy");
  }

  function banFree(target) {
    var ban = bannerEl(target);
    if (!ban || !ban.classList.contains("lm-busy")) return;
    var t = bannerTextEl(target, ban);
    // Put the original text back and drop the busy styling. The very next
    // render re-derives the correct text anyway; this just avoids a flash of
    // the stale "is running" line in between.
    if (t && ban.dataset.lmPrev !== undefined) t.textContent = ban.dataset.lmPrev;
    delete ban.dataset.lmPrev;
    ban.classList.remove("lm-busy");
  }

  /* ── core apply ─────────────────────────────────────────────────────── */
  function apply() {
    var wanted = {};   // boxTarget -> lock
    LOCKS.forEach(function (lock) {
      (lock.affects || []).forEach(function (t) {
        var subsys = t === "sa" ? "sa" : "px";
        if (!localBusy(subsys, t)) wanted[t] = lock;
      });
    });

    // "probable" was missing from this list, so PROBABLE's box never showed the
    // RUNNING strip and never had its buttons locked from another screen — even
    // though it builds the same #pxBox-probable / #pxBanner-probable markup as
    // every other pipe and the backend already emits locks for it.
    ["ctcl", "ms15", "cli", "sa", "probable"].forEach(function (t) {
      var box = boxEl(t);
      if (!box) return;
      var strip = box.querySelector(".lm-strip");
      if (wanted[t]) {
        disableBox(box);
        var html = stripHTML(wanted[t]);
        if (!strip) {
          strip = document.createElement("div");
          strip.className = "lm-strip";
          box.insertBefore(strip, box.firstChild);
        }
        if (strip.dataset.sig !== html) { strip.innerHTML = html; strip.dataset.sig = html; }
        // …and stop the green banner telling this screen to run something that
        // is already running (see bannerEl/banBusy above).
        banBusy(t, wanted[t]);
      } else {
        if (strip) strip.remove();
        enableBox(box);
        banFree(t);
      }
    });

    if (LOCKS.length && !TICKER) {
      TICKER = setInterval(function () {
        document.querySelectorAll(".lm-el").forEach(function (el) {
          el.textContent = fmtElapsed(el.dataset.start);
        });
      }, 1000);
    } else if (!LOCKS.length && TICKER) {
      clearInterval(TICKER); TICKER = null;
    }
  }

  /* re-apply after the console's own renders wipe the DOM */
  function wrap(name, after) {
    var orig = window[name];
    if (typeof orig !== "function") return;
    window[name] = function () {
      var r = orig.apply(this, arguments);
      try { after(); } catch (e) {}
      return r;
    };
  }
  wrap("pxRenderRuns", apply);
  wrap("saRenderRuns", apply);

  /* ── live subscription (SSE, browser auto-reconnects) ───────────────── */
  function connect() {
    var es = new EventSource("/ops/locks/stream");
    es.onmessage = function (m) {
      try { LOCKS = (JSON.parse(m.data).locks || []).filter(function (l) { return (l.affects || []).length; }); }
      catch (e) { return; }
      apply();
    };
    es.onerror = function () { /* EventSource retries automatically */ };
  }
  connect();

  /* ── MCX Deposit / MCX Web Allocation shared TCS BANCS session lock ────
     This is a SEPARATE, narrower lock (sa_codes/mcx_deposit_downloader.py's
     file lock, polled via /sa/mcx-deposit/lock-status) — deliberately kept
     out of the generic /ops/locks system above since these two buttons
     don't conflict with anything else, only with each other.

     dashboard.html's own mcxLockPollTick() already polls this and greys
     saMcxDepositBtn / cliMcxBtn — but its skip condition is
     `!_saMcxLocalRunning && d.owner !== 'sa'`. That assumes "owner === sa"
     can only ever mean "this tab's own click", which breaks the moment a
     SECOND person on the LAN clicks the SAME sa/cli button — every OTHER
     tab then sees owner==='sa' and (wrongly) treats it as its own request,
     so the button never greys out there and just errors on click instead
     ("[MCX_DEPOSIT_BUSY]" → "still in use, most likely a leftover..." —
     which is misleading too, since it's not stale, it's just someone else
     genuinely using it right now).

     Can't patch mcxLockPollTick() itself — dashboard.html already passed
     the ORIGINAL function reference to setInterval() before this script
     ever loads, so reassigning window.mcxLockPollTick afterward wouldn't
     change what that interval calls. Instead this runs its own corrected
     poller alongside it. It only ever GREYS a button the native poller
     left wrongly enabled (using the exact same dataset flag the native
     poller already knows how to restore) — it never implements its own
     restore path, so the two pollers can't fight over un-greying and
     there's no flicker war. */
  function _mcxSessionLockTick() {
    fetch("/sa/mcx-deposit/lock-status").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.busy) return;
      var saLocal = false, cliLocal = false;
      try { saLocal = typeof _saMcxLocalRunning !== "undefined" && _saMcxLocalRunning; } catch (e) {}
      try { cliLocal = typeof _cliMcxLocalRunning !== "undefined" && _cliMcxLocalRunning; } catch (e) {}
      var ownerLabel = d.owner === "cli" ? "Client Prudential Limit" : "Short Allocation";
      var saBtn = document.getElementById("saMcxDepositBtn");
      var cliBtn = document.getElementById("cliMcxBtn");
      if (saBtn && !saLocal && !saBtn.disabled) {
        saBtn.disabled = true; saBtn.dataset.mcxLockGreyed = "1";
        saBtn.textContent = "🔒 In use by " + ownerLabel;
      }
      if (cliBtn && !cliLocal && !cliBtn.disabled) {
        cliBtn.disabled = true; cliBtn.dataset.mcxLockGreyed = "1";
        cliBtn.textContent = "🔒 In use by " + ownerLabel;
      }
    }).catch(function () {});
  }
  _mcxSessionLockTick();
  setInterval(_mcxSessionLockTick, 1500);
})();
