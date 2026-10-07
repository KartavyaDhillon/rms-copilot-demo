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
        '<button class="btn sm ghost" id="pcCommon" title="Upload today\'s HOLDING and RISK_REPORT files">' + RMS.icon("download") + " Upload Common Files</button>" +
        '<button class="btn sm ghost" id="pcReload" title="Reload console (only if nothing is running!)">' + RMS.icon("refresh") + " Reload</button>" +
        '<a class="btn sm ghost" href="/legacy" target="_blank" title="Original console with sidebar and internal views, in a new tab">' + RMS.icon("external") + " Standalone</a></div></div>" +
        '<iframe class="pc-frame" id="pcFrame" src="/legacy?embed=1"></iframe>';
      RMS.mountIcons(el);
      $("#pcReload").onclick = () => {
        if (confirm("Reload the Process Center console?\n\nOnly do this if no run is currently streaming in it — an active run keeps running on the server, but this tab would stop showing its live output.")) {
          $("#pcFrame").src = "/legacy?embed=1&t=" + Date.now();
        }
      };
      $("#pcCommon").onclick = openCommon;
      booted = true;
    },
  };

  /* ── Upload Common Files: strictly today's HOLDING + RISK_REPORT ── */
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function popup(title, msg, ok) {
    const close = RMS.modal(title,
      '<div style="text-align:center;padding:6px 4px 2px"><div style="font-size:40px;line-height:1.1">' + (ok ? "✅" : "⚠️") + "</div>" +
      '<div style="margin:12px 0 4px;font-size:13.5px;line-height:1.5;word-break:break-word;white-space:pre-line">' + esc(msg) + "</div>" +
      '<button class="btn primary" id="pcOk" style="margin-top:14px;min-width:120px;justify-content:center">OK</button></div>',
      { width: 440, icon: ok ? "check" : "alert" });
    $("#pcOk").onclick = close;
    $("#pcOk").focus();
    if (ok) setTimeout(() => { try { close(); } catch (e) { /* already closed */ } }, 4000);
  }

  async function openCommon() {
    let st;
    try { st = await (await fetch("/common/status", { credentials: "same-origin" })).json(); }
    catch (e) { popup("Upload Common Files", "Could not reach the server: " + e.message, false); return; }
    const rows = (st.items || []).map((i) =>
      "<tr><td><b>" + esc(i.name) + "</b></td><td style='text-align:right'>" +
      (i.present ? '<span class="badge ok">Present · ' + esc(i.modified) + "</span>" : '<span class="badge warn">Missing</span>') + "</td></tr>").join("");
    const close = RMS.modal("Upload Common Files",
      '<div class="card-sub" style="margin-bottom:10px">Only <b>today\'s</b> HOLDING and RISK_REPORT files are accepted, named exactly as below. Anything else is rejected. An existing file of the same name is replaced.</div>' +
      '<table class="htable"><tbody>' + rows + "</tbody></table>" +
      '<input type="file" id="pcCommonFile" accept=".csv" multiple style="display:none">' +
      '<button class="btn primary" id="pcCommonPick" style="width:100%;justify-content:center;height:44px;margin-top:12px">⬆️ Choose file(s) to upload</button>' +
      '<div id="pcCommonMsg" class="card-sub" style="min-height:18px;margin-top:8px"></div>',
      { width: 520, icon: "download" });
    $("#pcCommonPick").onclick = () => $("#pcCommonFile").click();
    $("#pcCommonFile").onchange = async () => {
      const list = Array.from($("#pcCommonFile").files || []);
      if (!list.length) return;
      $("#pcCommonMsg").textContent = "Uploading…";
      const fd = new FormData();
      list.forEach((f) => fd.append("files", f));
      try {
        const r = await fetch("/common/upload", { method: "POST", body: fd, credentials: "same-origin" });
        const d = await r.json().catch(() => ({}));
        close();
        const saved = d.saved || [], rej = d.rejected || [];
        if (saved.length && !rej.length) popup("Upload successful", saved.join("\n") + "\nsaved to COMMON FILES.", true);
        else if (saved.length) popup("Partly uploaded", "Saved: " + saved.join(", ") + "\n\nRejected:\n" + rej.join("\n"), false);
        else popup("Not uploaded", rej.join("\n") || d.error || ("HTTP " + r.status), false);
      } catch (e) { $("#pcCommonMsg").innerHTML = '<span style="color:var(--err)">' + esc(e.message) + "</span>"; }
    };
  }
})();
