/* pages/process.js — Process Center.
   The battle-tested execution console (dashboard.html) embedded UNCHANGED via
   /legacy?embed=1 (same origin, so SSE, captcha/token popups, everything works
   exactly as before). The iframe is created once and never destroyed: leaving
   the page only hides it, so live runs, log streams and pollers keep going
   while you check the Dashboard or History.

   Embedded mode is a CLEAN PRODUCTION EXECUTION SCREEN: the console's inner
   sidebar AND its internal views (Console/Downloads/Compliance/Agents/Data
   Explorer/Audit Log/Reports/Workflows) are not exposed here at all — no tabs,
   no toolbar, no relocated navigation (per explicit request). The page shows
   the four production workflows and nothing else. Every internal/developer
   view remains available on the standalone /legacy page, unchanged. */
(function () {
  window.RMS = window.RMS || {};
  const $ = RMS.$;
  let booted = false;

  RMS.pages = RMS.pages || {};
  RMS.pages.process = {
    title: "Process Center", crumb: "Execute and monitor today's automation workflows.",
    keepAlive: true,
    mount(el) {
      if (booted) return;
      // NOTE: no page title inside the content area — the platform topbar above
      // already says "Process Center"; this slim bar only carries utilities.
      el.innerHTML =
        '<div class="pc-bar"><div class="l"></div>' +
        '<div style="display:flex;gap:8px">' +
        '<button class="btn sm ghost" id="pcReload" title="Reload console (only if nothing is running!)">' + RMS.icon("refresh") + " Reload</button>" +
        '<a class="btn sm ghost" href="/legacy" target="_blank" title="Original console with sidebar and internal views, in a new tab">' + RMS.icon("external") + " Standalone</a></div></div>" +
        '<iframe class="pc-frame" id="pcFrame" src="/legacy?embed=1"></iframe>';
      RMS.mountIcons(el);
      $("#pcReload").onclick = () => {
        if (confirm("Reload the Process Center console?\n\nOnly do this if no run is currently streaming in it — an active run keeps running on the server, but this tab would stop showing its live output.")) {
          $("#pcFrame").src = "/legacy?embed=1&t=" + Date.now();
        }
      };
      booted = true;
    },
  };
})();
