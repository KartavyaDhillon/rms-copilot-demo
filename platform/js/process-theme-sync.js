/* process-theme-sync.js — keeps the embedded Process Center's colors in sync
   with the platform's theme switcher (Settings → Appearance, or the moon-icon
   Ctrl+K "cycle theme" button in the topbar).

   Injected at SERVE time by api.py's /legacy?embed=1 route, same mechanism as
   process-reskin.css and process-locks.js — dashboard.html on disk is never
   touched. The iframe and the parent platform page are same-origin
   (localhost:8000), so this can read the SAME "rms_ops_prefs" localStorage
   key the platform's theme.js writes to, and react live via the "storage"
   event the browser fires in every OTHER same-origin document the instant
   one of them calls localStorage.setItem() — no polling needed.

   process-reskin.css defines one palette block per theme keyed off
   html[data-rms-theme="…"]; this script's only job is keeping that attribute
   (plus the user's custom accent, if set) in sync with the platform's prefs. */
(function () {
  var KEY = "rms_ops_prefs";

  function apply() {
    var theme = "dark", accent = "";
    try {
      var p = JSON.parse(localStorage.getItem(KEY) || "{}");
      if (p.theme) theme = p.theme;
      if (p.accent) accent = p.accent;
    } catch (e) {}
    var el = document.documentElement;
    el.setAttribute("data-rms-theme", theme);
    if (accent) el.style.setProperty("--ring", "0 0 0 3px " + accent + "40");
  }

  apply();
  // fires in this document whenever the PARENT page (or any other same-origin
  // tab/frame) changes rms_ops_prefs — this is how a live theme switch while
  // Process Center is open updates instantly without a reload.
  window.addEventListener("storage", function (e) {
    if (!e.key || e.key === KEY) apply();
  });
})();
