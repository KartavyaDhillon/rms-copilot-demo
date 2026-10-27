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
        '<button class="btn sm ghost" id="pcCaptchaAuto" title="Automatic NMASS / NCDEX captcha filling (2Captcha)">⚡ Captcha auto-fill: <b id="pcCaptchaAutoState">…</b></button>' +
        '<button class="btn sm primary" id="pcCommon" style="font-weight:700" title="Upload today\'s HOLDING and RISK_REPORT files">' + RMS.icon("download") + " Upload Common Files</button>" +
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
      captchaAutoInit();
      booted = true;
    },
  };

  /* ── Captcha auto-fill switch: one server-side switch (/captcha/auto) shared by everybody ── */
  let captchaAuto = { enabled: false, key_configured: false };

  function captchaAutoPaint() {
    const btn = $("#pcCaptchaAuto"), st = $("#pcCaptchaAutoState");
    if (!btn || !st) return;
    const on = captchaAuto.enabled && captchaAuto.key_configured;
    st.textContent = !captchaAuto.key_configured ? "No key" : (captchaAuto.enabled ? "On" : "Off");
    st.style.color = on ? "#4ade80" : (captchaAuto.key_configured ? "" : "#fbbf24");
    const t = captchaAuto.today;
    btn.title = captchaAuto.key_configured
      ? "Fills NMASS / NCDEX captchas automatically (up to " + (captchaAuto.max_tries || 5) + " tries of " + (captchaAuto.timeout_s || 45) +
        " s each), then the normal captcha popup appears. Applies to everybody." + (t ? " Today: " + t.accepted + " accepted, " + t.rejected + " wrong." : "")
      : "No 2Captcha key found. Add APIKEY_2CAPTCHA=... to the .env file and restart the server.";
  }

  async function captchaAutoRefresh() {
    try { captchaAuto = await (await fetch("/captcha/auto")).json(); captchaAutoPaint(); } catch (e) { /* server not updated yet */ }
  }

  function captchaAutoInit() {
    $("#pcCaptchaAuto").onclick = async () => {
      if (!captchaAuto.key_configured) { popup("Captcha auto-fill", "No 2Captcha key found.\nAdd APIKEY_2CAPTCHA=... to the .env file and restart the server.", false); return; }
      try {
        const d = await (await fetch("/captcha/auto", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !captchaAuto.enabled }) })).json();
        if (d.ok === false) popup("Captcha auto-fill", d.error || "Could not change the switch", false);
      } catch (e) { popup("Captcha auto-fill", "Could not reach the server: " + e.message, false); }
      captchaAutoRefresh();
    };
    captchaAutoRefresh();
    setInterval(captchaAutoRefresh, 30000);
  }

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

  /* ── Get files tab: any date range of HOLDING / RISK_REPORT, from COMMON FILES (+ its dated backup) and the database ── */
  function getFilesHtml() {
    const iso = (d) => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    const t = new Date(), w = new Date(); w.setDate(w.getDate() - 7);
    const inp = "background:var(--panel2,#0f141c);color:inherit;border:1px solid var(--line,#2a3342);border-radius:8px;padding:6px 8px;color-scheme:dark";
    return '<div class="card-sub" style="margin-bottom:10px">Pick a date range and the files you want. Files come from <b>COMMON FILES</b> (including its dated backup folders); the table also shows whether each one is <b>backed up in the database</b>. If a file is no longer on disk, it is downloaded from the database.</div>' +
      '<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:end;margin-bottom:10px">' +
      '<label class="card-sub">From<br><input type="date" id="pcgFrom" value="' + iso(w) + '" style="' + inp + '"></label>' +
      '<label class="card-sub">To<br><input type="date" id="pcgTo" value="' + iso(t) + '" style="' + inp + '"></label>' +
      '<label style="display:flex;gap:6px;align-items:center;font-weight:600"><input type="checkbox" id="pcgRisk" checked> RISK REPORT</label>' +
      '<label style="display:flex;gap:6px;align-items:center;font-weight:600"><input type="checkbox" id="pcgHold" checked> HOLDING</label>' +
      '<button class="btn sm primary" id="pcgFind" style="font-weight:700">🔍 Find files</button></div>' +
      '<div id="pcgOut" class="card-sub" style="min-height:40px">Choose dates and click <b>Find files</b>.</div>';
  }

  function wireGetFiles() {
    const kinds = () => [$("#pcgRisk").checked ? "RISK_REPORT" : "", $("#pcgHold").checked ? "HOLDING" : ""].filter(Boolean).join(",");
    const fmtSize = (n) => n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
    const dl = (url, name) => { const a = document.createElement("a"); a.href = url; a.download = name || ""; document.body.appendChild(a); a.click(); a.remove(); };
    const badge = (on, yes, no) => on ? '<span class="badge ok">' + yes + "</span>" : '<span class="badge warn">' + no + "</span>";
    $("#pcgFind").onclick = async () => {
      const f = $("#pcgFrom").value, t = $("#pcgTo").value, k = kinds();
      if (!f) { $("#pcgOut").textContent = "Pick a From date."; return; }
      if (!k) { $("#pcgOut").textContent = "Tick RISK REPORT and/or HOLDING."; return; }
      $("#pcgOut").textContent = "Searching…";
      let d;
      try {
        d = await (await fetch("/common/range?frm=" + f + "&to=" + (t || f) + "&kinds=" + k, { credentials: "same-origin" })).json();
      } catch (e) { $("#pcgOut").innerHTML = '<span style="color:var(--err)">' + esc(e.message) + "</span>"; return; }
      if (d.error) { $("#pcgOut").innerHTML = '<span style="color:var(--err)">' + esc(d.error) + "</span>"; return; }
      if (d.detail) { $("#pcgOut").innerHTML = '<span style="color:var(--err)">' + esc(d.detail) + "</span>"; return; }
      const rows = d.rows || [];
      if (!rows.length) { $("#pcgOut").textContent = "No files found for those dates (neither on disk nor in the database)."; return; }
      const nDb = rows.filter((r) => r.db).length, nDisk = rows.filter((r) => r.disk).length;
      const body = rows.map((r) =>
        "<tr><td>" + esc(r.date.split("-").reverse().join("-")) + "</td><td><b>" + esc(r.kind.replace("_", " ")) + "</b></td>" +
        "<td>" + badge(r.disk, "On disk", "Not on disk") + "</td><td>" + badge(r.db, "In database", "Not in database") + "</td>" +
        "<td style='text-align:right'>" + esc(fmtSize(r.size)) + "</td>" +
        "<td style='text-align:right'><a href='/common/get?name=" + encodeURIComponent(r.name) + "' download='" + esc(r.name) + "' title='Download " + esc(r.name) + "' " +
        "style='display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:8px;border:1px solid var(--line,#2a3342);color:inherit;text-decoration:none'>" +
        RMS.icon("download") + "</a></td></tr>").join("");
      $("#pcgOut").innerHTML =
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap">' +
        "<span><b>" + rows.length + "</b> file(s) · on disk <b>" + nDisk + "</b> · in database <b>" + nDb + "</b>" +
        (nDb < rows.length ? ' · <span style="color:var(--warn,#fbbf24)">' + (rows.length - nDb) + " not backed up in the database</span>" : "") + "</span>" +
        '<button class="btn sm primary" id="pcgZip" style="font-weight:700">⬇️ Download all (ZIP)</button></div>' +
        '<div style="max-height:300px;overflow:auto"><table class="htable"><thead><tr><th>Date</th><th>File</th><th>Disk</th><th>Database</th><th style="text-align:right">Size</th><th></th></tr></thead><tbody>' +
        body + "</tbody></table></div>" +
        (d.db_error ? '<div style="color:var(--err);margin-top:6px">Database check failed: ' + esc(d.db_error) + "</div>" : "");
      $("#pcgZip").onclick = () => dl("/common/zip?frm=" + f + "&to=" + (t || f) + "&kinds=" + k);
    };
  }

  async function openCommon() {
    let st;
    try { st = await (await fetch("/common/status", { credentials: "same-origin" })).json(); }
    catch (e) { popup("Upload Common Files", "Could not reach the server: " + e.message, false); return; }
    if (!st.items) { popup("Upload Common Files", "The server has not been updated with this feature yet. Copy the new api.py to the live folder and restart the console.", false); return; }
    let fs = {};
    try { fs = await (await fetch("/common/ftp/status", { credentials: "same-origin" })).json(); } catch (e) { fs = {}; }
    const ftpLine = (f) => {
      if (!f || f.configured === undefined) return "";
      if (!f.configured) return "Automatic fetch from the FTP is not set up yet (COMMON_FTP_* in .env). Uploading by hand works as always.";
      const per = Object.keys(f.files || {}).map((n) => { const x = f.files[n] || {};
        const bar = (x.status === "downloading" && x.pct != null)
          ? '<div style="height:8px;margin:4px 0 6px;border-radius:5px;background:rgba(127,127,127,.25);overflow:hidden"><div style="height:100%;width:' + x.pct + '%;background:#4f9dff;transition:width .8s"></div></div>' : "";
        return esc(n) + ": " + esc(x.detail || "") + " (" + esc(x.at || "") + ")" + bar; }).join("<br>");
      return (f.enabled ? "Automatic fetch from the FTP runs every morning " + esc(f.window || "") + " and skips a file you already uploaded. " : "Automatic fetch is switched off. ") +
        (f.running ? "<b>Fetching now…</b> " : "") + (f.message ? esc(f.message) + ". " : "") + (per ? "<br>" + per : "");
    };
    const rows = (st.items || []).map((i) =>
      "<tr><td><b>" + esc(i.name) + "</b></td><td style='text-align:right'>" +
      (i.present
        ? '<span class="badge ok">Present · ' + esc(i.modified) + "</span>" +
          (i.source === "FTP" ? ' <span class="badge" title="Fetched automatically from the FTP" style="background:rgba(168,85,247,.16);color:#c084fc;border:1px solid rgba(168,85,247,.5)">⚡ Auto-fetched</span>'
            : i.source === "Manual" ? ' <span class="badge" title="' + esc("Uploaded by " + (i.source_by || "a user")) + '" style="background:rgba(56,189,248,.14);color:#38bdf8;border:1px solid rgba(56,189,248,.5)">✋ Uploaded by hand</span>' : "") +
          ' <a href="/common/download?name=' + encodeURIComponent(i.name) + '" download="' + esc(i.name) + '" title="Download ' + esc(i.name) + ' to this computer" ' +
          'style="display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;margin-left:8px;border-radius:8px;border:1px solid var(--border,#2a3342);color:inherit;text-decoration:none;vertical-align:middle">' +
          RMS.icon("download") + "</a>"
        : '<span class="badge warn">Missing</span>') + "</td></tr>").join("");
    const tabBtn = (id, label, on) =>
      '<button class="btn sm ' + (on ? "primary" : "ghost") + '" data-pctab="' + id + '" style="font-weight:700">' + label + "</button>";
    const close = RMS.modal("Upload Common Files",
      '<div style="display:flex;gap:8px;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid var(--line,#2a3342)">' +
      tabBtn("up", "⬆️ Upload", true) + tabBtn("get", "📦 Get files", false) + "</div>" +
      '<div id="pcTabUp">' +
      '<div class="card-sub" style="margin-bottom:10px">Only <b>today\'s</b> HOLDING and RISK_REPORT files are accepted, named exactly as below. Anything else is rejected. An existing file of the same name is replaced.</div>' +
      '<table class="htable"><tbody>' + rows + "</tbody></table>" +
      '<div id="pcFtpLine" class="card-sub" style="margin-top:10px;padding:8px 10px;border:1px dashed var(--line,#2a3342);border-radius:8px;line-height:1.5">' + ftpLine(fs) + "</div>" +
      (fs.configured ? '<button class="btn" id="pcFtpGo" style="width:100%;justify-content:center;height:38px;margin-top:8px">📥 Fetch from FTP now</button>' +
        '<button class="btn ghost" id="pcFtpLog" data-no-busy style="width:100%;justify-content:center;height:34px;margin-top:6px">📜 View FTP log</button>' : "") +
      '<input type="file" id="pcCommonFile" accept=".csv" multiple style="display:none">' +
      '<button class="btn primary" id="pcCommonPick" style="width:100%;justify-content:center;height:44px;margin-top:12px">⬆️ Choose file(s) to upload</button>' +
      '<div id="pcCommonMsg" class="card-sub" style="min-height:18px;margin-top:8px"></div></div>' +
      '<div id="pcTabGet" style="display:none">' + getFilesHtml() + "</div>",
      { width: 640, icon: "download" });
    document.querySelectorAll("[data-pctab]").forEach((b) => {
      b.onclick = () => {
        const get = b.getAttribute("data-pctab") === "get";
        $("#pcTabUp").style.display = get ? "none" : "";
        $("#pcTabGet").style.display = get ? "" : "none";
        document.querySelectorAll("[data-pctab]").forEach((x) => {
          x.className = "btn sm " + (x === b ? "primary" : "ghost");
        });
        if (get && !getBooted) { getBooted = true; wireGetFiles(); }
      };
    });
    let getBooted = false;
    if ($("#pcFtpLog")) $("#pcFtpLog").onclick = async () => {
      const show = async (d) => {
        let r = {}; try { r = await (await fetch("/common/ftp/log" + (d ? "?date=" + d : ""), { credentials: "same-origin" })).json(); } catch (e) {}
        const opts = (r.days || []).slice().reverse().map((x) => '<option' + (x === r.date ? " selected" : "") + ">" + x + "</option>").join("");
        const box = $("#pcLogBody"); if (!box) return;
        box.innerHTML = '<div style="margin-bottom:8px">Day: <select id="pcLogDay">' + (opts || "<option>" + esc(r.date || "") + "</option>") + "</select></div>" +
          '<pre style="max-height:50vh;overflow:auto;white-space:pre-wrap;font-size:12px;line-height:1.5;margin:0">' + (esc(r.text || "") || "Nothing logged for this day yet.") + "</pre>";
        $("#pcLogDay").onchange = (e) => show(e.target.value);
      };
      RMS.modal("FTP fetch log", '<div id="pcLogBody">Loading…</div>');
      show("");
    };
    if ($("#pcFtpGo")) $("#pcFtpGo").onclick = async () => {
      const goBtn = $("#pcFtpGo"), goTxt = goBtn.innerHTML;
      goBtn.disabled = true; goBtn.style.opacity = ".7";
      goBtn.innerHTML = '<span class="rms-spin"></span>Fetching… please wait';          // spinner stays for the whole fetch
      const unbusy = () => { goBtn.disabled = false; goBtn.style.opacity = ""; goBtn.innerHTML = goTxt; };
      let r = {};
      try { r = await (await fetch("/common/ftp/fetch", { method: "POST", credentials: "same-origin" })).json(); } catch (e) { r = { ok: false, error: e.message }; }
      if (!r.ok) { $("#pcFtpLine").innerHTML = '<span style="color:var(--err)">' + esc(r.error || "Could not start the fetch") + "</span>"; unbusy(); return; }
      $("#pcFtpLine").innerHTML = "<b>Fetching from the FTP…</b> big files can take a minute or two.";
      const t0 = Date.now();
      const tick = async () => {
        let f = {};
        try { f = await (await fetch("/common/ftp/status", { credentials: "same-origin" })).json(); } catch (e) {}
        const el = $("#pcFtpLine"); if (!el) return;                       // window was closed
        el.innerHTML = ftpLine(f);
        if (f.running && Date.now() - t0 < 30 * 60 * 1000) { setTimeout(tick, 1500); return; }
        close(); await openCommon();                                        // reopen so the Present / Missing badges are fresh
        const l2 = $("#pcFtpLine"); if (l2) l2.innerHTML = ftpLine(f);       // keep the result message visible
      };
      setTimeout(tick, 2500);
    };
    $("#pcCommonPick").onclick = () => $("#pcCommonFile").click();
    $("#pcCommonFile").onchange = async () => {
      const list = Array.from($("#pcCommonFile").files || []);
      if (!list.length) return;
      const names = (st.items || []).map((i) => i.name.toLowerCase());
      const bad = list.filter((f) => !names.includes(f.name.toLowerCase()));
      if (bad.length) {
        const today = (st.items[0].name.match(/(\d{2})(\d{2})(\d{4})/) || []).slice(1).join("-");
        close();
        popup("Not today's file", bad.map((f) => {
          const m = f.name.match(/^(HOLDING|RISK_REPORT)_(\d{2})(\d{2})(\d{4})\.csv$/i);
          return m ? f.name + " is dated " + m[2] + "-" + m[3] + "-" + m[4] + ". Only today's file (" + today + ") is accepted."
                   : f.name + " is not allowed. Only today's HOLDING_" + today.replace(/-/g, "") + ".csv and RISK_REPORT_" + today.replace(/-/g, "") + ".csv are accepted.";
        }).join("\n\n"), false);
        return;
      }
      $("#pcCommonPick").disabled = true;
      $("#pcCommonPick").style.opacity = ".5";
      $("#pcCommonMsg").innerHTML =
        '<style>@keyframes pcspin{to{transform:rotate(360deg)}}</style>' +
        '<div style="display:flex;align-items:center;justify-content:center;gap:10px;color:var(--ink);font-weight:600;padding:6px 0">' +
        '<span style="width:20px;height:20px;border:3px solid rgba(255,255,255,.2);border-top-color:var(--accent,#fbbf24);border-radius:50%;display:inline-block;animation:pcspin .8s linear infinite"></span>' +
        "Uploading " + list.length + " file" + (list.length > 1 ? "s" : "") + "…</div>";
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
      } catch (e) {
        $("#pcCommonPick").disabled = false; $("#pcCommonPick").style.opacity = "";
        $("#pcCommonMsg").innerHTML = '<span style="color:var(--err)">' + esc(e.message) + "</span>";
      }
    };
  }
})();
