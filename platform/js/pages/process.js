    const ftpLine = (f) => {
      if (!f || f.configured === undefined) return "";
      if (!f.configured) return "Automatic fetch from the FTP is not set up yet. Uploading by hand works as always.";
      const fl = f.files || {};
      const live = Object.keys(fl).filter((n) => (fl[n] || {}).status === "downloading").map((n) => {
        const x = fl[n];
        const bar = x.pct != null ? '<div style="height:8px;margin:4px 0 6px;border-radius:5px;background:rgba(127,127,127,.25);overflow:hidden"><div style="height:100%;width:' + x.pct + '%;background:#4f9dff;transition:width .8s"></div></div>' : "";
        return esc(n) + ": " + esc(x.detail || "") + bar; }).join("");
      const bad = Object.keys(fl).filter((n) => ["error", "not on ftp", "being written"].includes((fl[n] || {}).status)).map((n) => '<span style="color:var(--err)">' + esc(n) + ": " + esc(fl[n].detail || "") + "</span>").join("<br>");
      return live || bad;
    };
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
        '<button class="btn sm ghost" id="pcAuto" title="Automatic captcha and RSA-token filling, and the low-balance e-mail">\u26a1 Auto-fill CAPTCHA / TOKEN: <b id="pcAutoState">\u2026</b></button>' +
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

  /* ── Auto-fill CAPTCHA / TOKEN: one button, a small window with the two on/off switches + the balance alert.
        All switches live on the server (/captcha/auto, /captcha/token-auto) and apply to everybody. ── */
  let captchaAuto = { enabled: false, key_configured: false }, tokenAuto = { enabled: true, seed_ok: false };

  function autoPaintBtn() {
    const st = $("#pcAutoState"); if (!st) return;
    const c = captchaAuto.key_configured && captchaAuto.enabled, t = tokenAuto.enabled;
    st.innerHTML = 'Captcha <span style="color:' + (c ? "#4ade80" : "#fbbf24") + '">' + (!captchaAuto.key_configured ? "No key" : c ? "On" : "Off") + "</span> \u00b7 Token <span style=\"color:" + (t ? "#4ade80" : "#fbbf24") + '">' + (t ? "On" : "Off") + "</span>";
  }
  async function autoRefresh() {
    try { captchaAuto = await (await fetch("/captcha/auto")).json(); } catch (e) { /* server not updated yet */ }
    try { tokenAuto = await (await fetch("/captcha/token-auto")).json(); } catch (e) { /* server not updated yet */ }
    autoPaintBtn();
    if ($("#pcAutoWin")) autoWinPaint();
  }
  async function autoPost(url, body) {
    try {
      const d = await (await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
      if (d.ok === false) popup("Auto-fill", d.error || "Could not change it", false);
      return d;
    } catch (e) { popup("Auto-fill", "Could not reach the server: " + e.message, false); }
  }
  function sw(id, label, on, hint) {
    const b = (v, txt) => '<button class="btn sm ' + (on === v ? "primary" : "ghost") + '" data-sw="' + id + '" data-v="' + (v ? 1 : 0) + '" style="min-width:64px;justify-content:center">' + txt + "</button>";
    return '<div style="border:1px solid var(--line,#2a3342);border-radius:12px;padding:12px 14px;margin-bottom:10px">' +
      '<div style="display:flex;align-items:center;gap:10px"><b style="flex:1">' + label + '</b><span id="' + id + 'St" style="font-size:12px"></span>' + b(true, "On") + b(false, "Off") + "</div>" +
      '<div style="font-size:11.5px;color:var(--text-3,#93a0b4);margin-top:6px;line-height:1.5">' + hint + "</div></div>";
  }
  function autoWinHtml() {
    return '<div id="pcAutoWin">' +
      sw("swCap", "\u26a1 Captcha auto-fill (2Captcha)", captchaAuto.enabled, "<b>On:</b> NMASS and NCDEX captchas are typed in for you by the 2Captcha service (it costs a little money per captcha). <b>Off:</b> a popup asks a person to type every captcha.") +
      sw("swTok", "\ud83d\udd11 Token auto-fill (MCX RSA code)", tokenAuto.enabled, "<b>On:</b> the MCX RSA code is generated by the console from the saved token seed - nobody types it. <b>Off:</b> a popup asks a person for the code (runs with nobody watching, like Live Allocation, will fail while this is Off).") +
      '<div style="border:1px solid var(--line,#2a3342);border-radius:12px;padding:12px 14px">' +
      '<div style="font-weight:650;margin-bottom:4px">\u2709 Low balance e-mail alert</div>' +
      '<div id="balInfo" style="font-size:12px;color:var(--text-3,#93a0b4);line-height:1.6;margin-bottom:10px">Checking the 2Captcha balance\u2026</div>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn sm ghost" id="balCfg">\u2709 Email To / CC</button><button class="btn sm ghost" id="balCheck">\u21bb Check balance now</button><button class="btn sm primary" id="balTest">\ud83d\udce8 Send me a test e-mail</button></div>' +
      '<div id="balMsg" style="font-size:12px;margin-top:8px"></div></div></div>';
  }
  function autoWinPaint() {
    const set = (id, txt, col) => { const e = $("#" + id); if (e) { e.textContent = txt; e.style.color = col || ""; } };
    set("swCapSt", !captchaAuto.key_configured ? "no key in .env" : captchaAuto.enabled ? "is ON" : "is OFF", captchaAuto.key_configured && captchaAuto.enabled ? "#4ade80" : "#fbbf24");
    set("swTokSt", tokenAuto.enabled ? (tokenAuto.seed_ok ? "is ON" : "ON - seed missing") : "is OFF", tokenAuto.enabled && tokenAuto.seed_ok ? "#4ade80" : "#fbbf24");
    document.querySelectorAll("#pcAutoWin [data-sw]").forEach((b) => {
      const cur = b.dataset.sw === "swCap" ? !!captchaAuto.enabled : !!tokenAuto.enabled, mine = b.dataset.v === "1";
      b.className = "btn sm " + (cur === mine ? "primary" : "ghost");
    });
  }
  async function balLoad() {
    try {
      const b = await (await fetch("/captcha/balance")).json();
      const el = $("#balInfo"); if (!el) return;
      el.innerHTML = (b.balance != null ? "Balance now: <b style=\"color:" + (b.low ? "#f87171" : "#4ade80") + "\">$" + Number(b.balance).toFixed(2) + "</b> (checked " + esc((b.checked_at || "").slice(11, 16)) + ")" : "Not checked yet") +
        " \u00b7 alert at or below <b>$" + Number(b.limit).toFixed(2) + "</b> \u00b7 checked every hour<br>To: " + esc(b.to || "\u2014 not set") + " \u00b7 CC: " + esc(b.cc || "\u2014") +
        (b.error ? '<br><span style="color:#f87171">Could not read the balance: ' + esc(b.error) + "</span>" : "") + (b.alert_error ? '<br><span style="color:#f87171">Last alert mail failed: ' + esc(b.alert_error) + "</span>" : "");
    } catch (e) { /* ignore */ }
  }
  function autoOpen() {
    RMS.modal("Auto-fill CAPTCHA / TOKEN", autoWinHtml(), { width: 560, icon: "zap" });
    autoRefresh(); balLoad();
    document.querySelectorAll("#pcAutoWin [data-sw]").forEach((b) => {
      b.onclick = async () => {
        const on = b.dataset.v === "1";
        if (b.dataset.sw === "swCap" && !captchaAuto.key_configured) { popup("Captcha auto-fill", "No 2Captcha key found.\nAdd APIKEY_2CAPTCHA=... to the .env file and restart the server.", false); return; }
        await autoPost(b.dataset.sw === "swCap" ? "/captcha/auto" : "/captcha/token-auto", { enabled: on });
        autoRefresh();
      };
    });
    $("#balCfg").onclick = () => {
      const w = $("#pcFrame").contentWindow;
      if (w && typeof w.pxConfig === "function") w.pxConfig("captchabal"); else popup("Balance alert", "The console is still loading - try again in a moment.", false);
    };
    $("#balCheck").onclick = async () => {
      const btn = $("#balCheck"), msg = $("#balMsg");
      btn.disabled = true;
      msg.innerHTML = '<span style="display:inline-block;width:11px;height:11px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:spin .8s linear infinite;vertical-align:-1px;margin-right:6px"></span>Asking 2Captcha for the balance - this can take up to 30 seconds\u2026';
      let d = null;
      try {
        const ac = new AbortController(), tm = setTimeout(() => ac.abort(), 45000);
        d = await (await fetch("/captcha/balance/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", signal: ac.signal })).json();
        clearTimeout(tm);
      } catch (e) {
        msg.textContent = e.name === "AbortError" ? "2Captcha did not answer within 45 seconds - please try again." : "Could not reach the server: " + e.message;
        btn.disabled = false; return;
      }
      btn.disabled = false;
      msg.textContent = d && d.error ? "Could not read the balance: " + d.error
        : d && d.balance != null ? "Balance is $" + Number(d.balance).toFixed(2) + (d.low ? " - LOW, alert mail sent if one was due." : " - fine.") : "No answer - please try again.";
      balLoad();
    };
    $("#balTest").onclick = async () => {
      $("#balMsg").textContent = "Sending\u2026";
      const d = await autoPost("/captcha/balance/test");
      $("#balMsg").style.color = d && d.ok ? "#4ade80" : "#f87171";
      $("#balMsg").textContent = d && d.ok ? "Test e-mail sent to " + d.to.join(", ") + (d.cc.length ? " (cc " + d.cc.join(", ") + ")" : "") + ". It is marked [TEST] and shows the real wording." : (d && d.error) || "Could not send";
    };
  }
  function captchaAutoInit() {
    $("#pcAuto").onclick = autoOpen;
    autoRefresh();
    setInterval(autoRefresh, 30000);
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
      '<div id="pcFtpLine" class="card-sub" style="margin-top:8px;line-height:1.5">' + ftpLine(fs) + "</div>" +
      (fs.configured ? '<button class="btn" id="pcFtpGo" style="width:100%;justify-content:center;height:38px;margin-top:8px">📥 Fetch from FTP now</button>' : "") +
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
        el.innerHTML = ftpLine(f) || "<b>Fetching from the FTP…</b>";
        if (f.running && Date.now() - t0 < 30 * 60 * 1000) { setTimeout(tick, 1500); return; }
        close(); await openCommon();                                        // reopen so the Present / Missing badges are fresh
        const l2 = $("#pcFtpLine");
        if (l2) {                                                           // one short result line
          const fl = f.files || {}, got = Object.keys(fl).filter((n) => (fl[n] || {}).status === "downloaded");
          l2.innerHTML = ftpLine(f) || (got.length ? '<span style="color:var(--ok,#34d399)">✔ Downloaded: ' + got.map(esc).join(", ") + "</span>"
            : '<span style="color:var(--ok,#34d399)">✔ Both files are already here - nothing to download.</span>');
        }
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
