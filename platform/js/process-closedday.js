/* process-closedday.js - "Today is a weekend / market holiday - run anyway?" popup.
 *
 * Scheduled runs and the auto timers skip weekends and market holidays; a person who clicks Run is always allowed
 * to run. This file only asks once, in a console popup (never a browser alert), before a manual Run on such a day,
 * so nobody e-mails / WhatsApps stale data by accident.
 *
 *  - RMS.closedDay()          -> {date, weekday, closed, reason} from /calendar/today (cached, refreshed in the background)
 *  - RMS.confirmClosedDay(x)  -> Promise<boolean>   the popup
 *  - a click guard on the Process Center page: any button whose label starts with "Run" asks first on a closed day.
 *    (The Scheduler page has its own confirmation, which adds the same warning - see scheduler.js.)
 */
(function () {
  "use strict";
  const RMS = window.RMS;
  if (!RMS) return;

  let cache = { t: 0, v: null };

  function refresh() {
    return fetch("/calendar/today", { credentials: "same-origin" })
      .then((r) => r.json())
      .then((j) => { cache = { t: Date.now(), v: j }; return j; })
      .catch(() => cache.v || { closed: false });
  }

  RMS.closedDay = function () {
    if (cache.v && Date.now() - cache.t < 60000) return Promise.resolve(cache.v);
    return refresh();
  };

  RMS.closedDayText = function (cd) {
    return "Today (" + cd.weekday + ") is a " + (cd.reason || "market holiday") +
      ". Scheduled runs and auto timers are skipped on such days, but a manual run is allowed.";
  };

  RMS.confirmClosedDay = function (what) {
    return RMS.closedDay().then((cd) => new Promise((resolve) => {
      const esc = RMS.esc || ((s) => String(s));
      const close = RMS.modal("Today is a " + (cd.reason || "market holiday"),
        '<div style="font-size:13.5px;line-height:1.55;margin-bottom:16px">' + esc(RMS.closedDayText(cd)) +
        (what ? "<br><br><b>" + esc(what) + "</b>" : "") + "<br><br>Run it anyway?</div>" +
        '<div style="display:flex;justify-content:flex-end;gap:8px"><button class="btn ghost" id="cdNo">Cancel</button>' +
        '<button class="btn primary" id="cdYes">Run anyway</button></div>',
        { width: 460, icon: "alert" });
      const yes = document.getElementById("cdYes"), no = document.getElementById("cdNo");
      if (yes) yes.onclick = () => { close(); resolve(true); };
      if (no) { no.onclick = () => { close(); resolve(false); }; no.focus(); }
    }));
  };

  // keep the answer fresh (a tab left open overnight must not remember yesterday)
  refresh();
  setInterval(refresh, 5 * 60 * 1000);

  // ---- the click guard (Process Center page only) ----------------------------------------------------------------
  const RUN_LABEL = /^[^A-Za-z0-9]*run\b/i;                       // "Run", "▶ Run", "Run now", "Run all" ...
  const NOT_A_RUN = /\b(log|logs|history|status|settings?|credentials?)\b/i;

  document.addEventListener("click", function (ev) {
    const t = ev.target;
    const btn = t && t.closest ? t.closest("button, a.btn, [role='button']") : null;
    if (!btn || btn.dataset.cdOk || btn.disabled) return;
    if (!/^#\/?process/.test(location.hash || "")) return;       // the Scheduler page confirms by itself
    const label = (btn.innerText || btn.textContent || "").replace(/\s+/g, " ").trim();
    if (!RUN_LABEL.test(label) || NOT_A_RUN.test(label)) return;
    const cd = cache.v;                                          // decided at once - no waiting inside a click
    if (!cd || !cd.closed) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    RMS.confirmClosedDay(label).then((ok) => {
      if (!ok) return;
      btn.dataset.cdOk = "1";
      try { btn.click(); } finally { delete btn.dataset.cdOk; }
    });
  }, true);
})();
