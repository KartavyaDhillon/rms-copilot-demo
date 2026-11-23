/* MARGIN SHORTFALL TRACK - small tile of the Process Center. "Open" shows the window: Upload files (any number of Margin_Detail files, or old
   FINAL_SHORTFALL files / a zip to import), Run, Stop, Credentials, Email To / CC, Backup files, Logs, Settings, KPI cards, step tiles,
   a table of the dates (margin version, the four console files, FINAL, TRACK) and the live log.
   Job: Margin_Detail (uploaded, versions) + SA_CM / SA_FO / MCX_EODSAR / MCX_Intra_Output (picked from the console by date) -> FINAL_SHORTFALL
   -> TRACK (5 following days) -> ONE mail for the newest date that is newer than the last sent date.
   Backend: /margin-track/* (api.py, core/margin_shortfall_track.py) and the shared /px/* windows with pipe=mst.
   Injected at serve time (see api.py /legacy). Every button answers with an in-console popup (never the browser's own). */
(function () {
  'use strict';
  if (window.__mstInjected) return;
  window.__mstInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'MARGIN SHORTFALL TRACK';
  var PIPE = 'mst';
  var BASE = '/margin-track';
  var S = { data: null, sig: '', timer: null, msgTimer: null, bk: null };

  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function $(id) { return document.getElementById(id); }
  function kb(n) { n = Number(n || 0); return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }
  function get(path) { return fetch(API + path, { cache: 'no-store' }).then(function (r) { return r.json(); }); }
  function post(path, body) {
    return fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }
  function ask(what, cb) { if (typeof window.saAskPassword === 'function') window.saAskPassword(what, cb); }
  function hideTip() { var t = $('globalTip'); if (t) t.style.display = 'none'; }
  function modal(t, h) { hideTip(); if (typeof window.saShowModal === 'function') window.saShowModal(t, h); }
  function iso(n) { var d = new Date(Date.now() - (n || 0) * 864e5); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }

  // ---- in-console popups (never the browser's own alert / confirm) ----
  function toast(text, kind) {
    var old = $('mstToast'); if (old) old.remove();
    var el = document.createElement('div'); el.id = 'mstToast';
    var col = kind === 'err' ? 'var(--red,#f87171)' : kind === 'warn' ? 'var(--amber,#fbbf24)' : 'var(--green,#34d399)';
    el.style.cssText = 'position:fixed;top:22px;left:50%;transform:translateX(-50%);z-index:100000;max-width:min(560px,92vw);padding:12px 18px;border-radius:12px;' +
      'background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:13px;line-height:1.45;display:flex;gap:10px;align-items:flex-start';
    el.innerHTML = '<span style="color:' + col + ';font-weight:800">' + (kind === 'err' ? '✕' : kind === 'warn' ? '!' : '✓') + '</span><span>' + esc(text) + '</span>';
    document.body.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, kind === 'err' ? 7000 : 3800);
  }
  function okBox(title, text, kind, html) {      // a popup with an OK button (stays until OK is pressed); html = an optional table under the text
    var old = $('mstOk'); if (old) old.remove();
    var col = kind === 'err' ? 'var(--red,#f87171)' : 'var(--green,#34d399)';
    var bg = document.createElement('div'); bg.id = 'mstOk';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100002;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';border-radius:14px;padding:20px 22px;width:min(' + (html ? '720' : '420') + 'px,94vw);max-height:88vh;overflow:auto;box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px;color:' + col + '">' + (kind === 'err' ? '✕ ' : '✓ ') + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:' + (html ? '10' : '16') + 'px">' + esc(text) + '</div>' + (html || '') +
      '<div style="display:flex;justify-content:flex-end;margin-top:14px"><button class="sa-tool" data-no-busy="1" id="mstOkBtn" style="min-width:90px;background:' + col + ';color:#06251a;border-color:transparent">OK</button></div></div>';
    document.body.appendChild(bg);
    $('mstOkBtn').onclick = function () { bg.remove(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
    $('mstOkBtn').focus();
  }
  function confirmBox(title, text, okLabel, cb) {
    var old = $('mstConfirm'); if (old) old.remove();
    var bg = document.createElement('div'); bg.id = 'mstConfirm';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--line-2,#2a3342);border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px">' + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end"><button class="sa-tool" id="mstNo" style="min-width:90px">Cancel</button><button class="sa-tool" id="mstYes" style="min-width:90px;background:var(--red,#f87171);color:#2a0a0a;border-color:transparent">' + esc(okLabel || 'OK') + '</button></div></div>';
    document.body.appendChild(bg);
    $('mstNo').onclick = function () { bg.remove(); };
    $('mstYes').onclick = function () { bg.remove(); cb(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
  }

  var css = document.createElement('style');
  css.textContent =
    '.mst-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.mst-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.mst-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.mst-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.mst-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.mst-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.mst-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}' +
    '@media(max-width:820px){.mst-kpis,.mst-steps{grid-template-columns:repeat(2,1fr)}}' +
    '.mst-step{border:1px solid var(--line);border-radius:11px;padding:9px 11px;background:var(--panel-2);min-width:0}' +
    '.mst-step.ok{border-color:rgba(52,211,153,.4)}.mst-step.failed{border-color:rgba(248,113,113,.55)}.mst-step.running{border-color:var(--amber)}' +
    '.mst-step .n{font-size:12px;font-weight:650}.mst-step .s{font-family:var(--mono);font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);margin-top:2px}' +
    '.mst-step.ok .s{color:var(--green)}.mst-step.failed .s{color:var(--red)}.mst-step.running .s{color:var(--amber)}' +
    '.mst-step .m{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.35;overflow:hidden;max-height:42px}' +
    '.mst-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.mst-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:170px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.mst-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.mst-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.mst-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.mst-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.mst-frow a{color:var(--blue);text-decoration:none}.mst-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.mst-tbl{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:12px}' +
    '.mst-tbl th{font-size:10px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);text-align:left;padding:4px 8px;border-bottom:1px solid var(--line-2);font-weight:600}' +
    '.mst-tbl td{padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}' +
    '.mst-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '.mst-dot{display:inline-block;min-width:16px;text-align:center;font-weight:700}' +
    '@keyframes mstSpin{to{transform:rotate(360deg)}}.mst-spin{display:inline-block;width:12px;height:12px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:mstSpin .8s linear infinite;vertical-align:-2px;margin-right:6px}';
  document.head.appendChild(css);

  // ───────────────────────── the small tile ─────────────────────────
  function grid() {
    var t = $('task-tpp') || $('task-obligation') || $('task-cncf11') || $('task-mtforder') || $('task-mtfconc') || $('task-ms15card') || $('task-mcxcomm') || $('task-liveallocation');
    if (t && t.parentNode) return t.parentNode;
    var slot = $('physdelSlot');
    return slot ? slot.parentNode : null;
  }
  function build() {
    if ($('task-mst')) return true;
    var g = grid(); if (!g) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-mst';
    tile.style.cssText = '--tile-accent:#f59e0b;cursor:pointer';
    tile.onclick = function () { mstOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">📉</div>' +
      '<div class="tile-name">MARGIN SHORTFALL TRACK <span class="st" id="mstChip">idle</span></div>' +
      '<div class="tile-desc">Upload the Margin_Detail files (any number, any dates), Run: the SA / EODSAR / MCX files are picked from the console by date, FINAL and TRACK are built, and only the newest new date is mailed to RMS DEL.</div>' +
      '<div class="tile-desc" id="mstMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#f59e0b;color:#2b1a00" onclick="event.stopPropagation();mstOpen()" data-tip="Open MARGIN SHORTFALL TRACK: upload, run, credentials, e-mail To / CC, backup files, logs, settings.">Open</button></div>';
    g.appendChild(tile);
    return true;
  }
  function keepLast() {
    if (window.__tilesManaged) return;      // process-organizer.js owns the card order (A-Z or your own)
    var el = $('task-mst'); if (!el || !el.parentNode) return;
    var p = el.parentNode, bk = document.getElementById('task-entrisk'); bk = bk && bk.parentNode === p ? bk : null;
    var kids = [].slice.call(p.children), want = kids.filter(function (e) { return e !== el && e !== bk; }).concat([el]).concat(bk ? [bk] : []);
    if (kids.some(function (e, i) { return e !== want[i]; })) want.forEach(function (e) { p.appendChild(e); });
  }

  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('mstDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(980px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__mst) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__mst = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (fn.indexOf('mstRun') === 0 ? ' data-no-busy="1"' : '') + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  var RED = ';border-color:rgba(248,113,113,.45);color:var(--red)';
  window.mstSub = function (fn) { window._saReturn = mstOpen; fn(); };
  window.mstCreds = function () { mstSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.mstEmail = function () { mstSub(function () { if (typeof window.pxConfig === 'function') window.pxConfig(PIPE); }); };
  window.mstLogs = function () { mstSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function mstOpen() {
    window._saReturn = null;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 8px">' +
        tb('⬆️ Upload files', 'mstUpload()', 'Pick any number of Margin_Detail_<ddmmyyyy>.xlsx files at once (the date must be in the name). A changed file for a date already there becomes the next version; an identical one is ignored. Old FINAL_SHORTFALL files (or a zip of them) can be imported here too.') +
        tb('🔑 Credentials', 'mstCreds()', 'The e-mail login this card sends from.') +
        tb('✉️ Email To / CC', 'mstEmail()', 'Who gets the Daily Shortfall Track mail (default: RMS DEL only).') +
        tb('📦 Backup files', 'mstSub(mstBackup)', 'Pick a date or a range and download the margin versions, FINAL and TRACK files as one zip.') +
        tb('🗂️ Logs', 'mstLogs()', 'The full log of every run and every upload.') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 14px">' +
        tb('▶ Run', 'mstRun()', 'Builds every new / changed date, refreshes the TRACK files and mails the newest date that is newer than the last sent date.', ';background:var(--blue);color:var(--blue-d);border-color:transparent') +
        tb('⚙ Settings', 'mstSub(mstSettings)', 'E-mail on/off, the last sent date, and the WhatsApp numbers that get the FAILURE ALERT.') +
        tb('⛔ Stop', 'mstStop()', 'Stops the run that is going right now.', RED) +
      '</div><div id="mstMsg"></div><div id="mstDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.mstOpen = mstOpen;

  function say(txt, kind) {
    if (txt) toast(txt, kind);
    var el = $('mstMsg');
    if (el) el.innerHTML = txt ? '<div class="mst-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('mstMsg'); if (e) e.innerHTML = ''; }, 8000);
  }

  // ───────────────────────── render ─────────────────────────
  var ST = { idle: 'Not run yet', running: 'Running…', done: '✓ Done', failed: '✗ Failed', stopped: 'Stopped' };
  function kpi(k, v, f) { return '<div class="mst-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div></div>'; }
  function banner(d) {
    var nm = d.next_mail || {};
    var base = '<span style="color:var(--dim)">Last sent date: <b>' + esc(d.last_sent || (d.baseline ? d.baseline + ' (newest imported)' : 'none')) + '</b></span>';
    if (d.status === 'failed') return '<div class="mst-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>The last run FAILED.</b> ' + esc(d.error || '') + ' <span style="color:var(--dim)">· ' + (d.alert_sent ? 'WhatsApp alert sent' : 'alert not sent' + (d.alert_reason ? ' (' + esc(d.alert_reason) + ')' : '')) + ' · fix it and press <b>Run</b> again - a mail that did not go is tried again.</span></div>';
    if (nm.hold) return '<div class="mst-ban" style="background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.45);color:var(--amber)"><b>Mail is waiting:</b> ' + esc(nm.why) + '. ' + base + '</div>';
    if (nm.date) return '<div class="mst-ban" style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.35);color:var(--blue)">Next mail: <b>' + esc(nm.date) + '</b> with ' + (nm.files || []).length + ' TRACK file(s). ' + base + '</div>';
    return '<div class="mst-ban" style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:var(--green)">Upload the Margin_Detail file(s), then press <b>Run</b>. ' + esc(nm.why || '') + ' ' + base + ((d.settings || {}).send_mail === false ? ' <b style="color:var(--amber)">E-mail is switched OFF.</b>' : '') + '</div>';
  }
  function kpis(d) {
    var dates = d.dates || [], c = d.counts || {};
    var blocked = dates.filter(function (x) { return x.blocked; }).length, todo = dates.filter(function (x) { return x.todo && !x.blocked; }).length;
    var run = d.stopping ? 'Stopping…' : d.running ? 'Running' : (ST[d.status] || d.status);
    return '<div class="mst-kpis">' +
      kpi('Last run', '<span style="color:' + (d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--ink)') + '">' + run + '</span>', (d.started ? 'started ' + d.started.slice(0, 16).replace('T', ' ') : 'not started') + (d.by ? ' · ' + d.by : '')) +
      kpi('To build', todo, todo ? 'press Run' : 'nothing waiting') +
      kpi('Blocked', '<span style="color:' + (blocked ? 'var(--red)' : 'var(--ink)') + '">' + blocked + '</span>', blocked ? 'a file of the console is missing - see the table' : 'none') +
      kpi('Last mail', esc(d.last_sent || '—'), (d.mail_log && d.mail_log[0] ? 'sent ' + d.mail_log[0].at.slice(0, 16) : 'no mail sent yet')) + '</div>';
  }
  function steps(d) {
    return '<div class="mst-lbl">Steps of the last run</div><div class="mst-steps">' + (d.steps || []).map(function (s) {
      var c = s.status === 'ok' || s.status === 'skipped' || s.status === 'done' ? 'ok' : s.status === 'failed' ? 'failed' : s.status === 'running' ? 'running' : '';
      var t = { ok: '✓ done', done: '✓ in place', skipped: '✓ skipped', failed: '✗ failed', running: 'running…', stopped: 'stopped' }[s.status] || '—';
      return '<div class="mst-step ' + c + '"><div class="n">' + esc(s.label) + '</div><div class="s">' + t + '</div><div class="m" title="' + esc(s.msg) + '">' + esc(s.msg) + '</div></div>';
    }).join('') + '</div>';
  }
  var SRC = [['SA_CM', 'SA CM'], ['SA_FO', 'SA FO'], ['MCX_EODSAR', 'EODSAR'], ['MCX_INTRA', 'INTRA']];
  function datesTable(d) {
    var rows = d.dates || [];
    if (!rows.length) return '<div class="mst-lbl">Dates</div><div style="color:var(--dim);font-size:12.5px;margin-bottom:12px">No date yet - use <b>Upload files</b> to add the Margin_Detail files (or import the old FINAL_SHORTFALL files).</div>';
    return '<div class="mst-lbl">Dates (newest first)</div><div style="max-height:260px;overflow:auto;margin-bottom:6px"><table class="mst-tbl"><thead><tr><th>Date</th><th>Margin</th><th>Console files</th><th>FINAL</th><th>TRACK</th></tr></thead><tbody>' + rows.map(function (r) {
      var col = r.blocked ? 'var(--red)' : r.todo ? 'var(--amber)' : 'var(--green)';
      var src = r.margin_v ? SRC.map(function (s) { var ok = (r.sources || {})[s[0]]; return '<span class="mst-dot" title="' + esc(s[1]) + (ok ? '' : ' - not found in the console') + '" style="color:' + (ok ? 'var(--green)' : 'var(--red)') + '">' + (ok ? '✓' : '✗') + '</span>'; }).join(' ') + ' <span style="color:var(--dim);font-size:10.5px">' + SRC.map(function (s) { return s[1]; }).join(' · ') + '</span>' : '<span style="color:var(--dim)">-</span>';
      var miss = r.blocked ? '<div style="color:var(--red);font-size:10.5px">missing: ' + esc((r.missing || []).join(', ') || 'margin file') + '</div>' : '';
      return '<tr><td style="font-family:var(--mono);white-space:nowrap">' + esc(r.date) + '</td><td>' + (r.margin_v ? 'v' + String(r.margin_v).padStart(3, '0') + ' <span style="color:var(--dim)">· ' + r.margin_clients + ' client(s)' + (r.margin_versions > 1 ? ' · ' + r.margin_versions + ' versions' : '') + '</span>' : '<span style="color:var(--dim)">imported only</span>') + '</td><td>' + src + miss + '</td>' +
        '<td style="color:' + col + '">' + esc(r.state) + (r.how === 'imported' && !r.todo ? '' : '') + '</td><td style="color:var(--dim)">' + esc(r.track || '-') + '</td></tr>';
    }).join('') + '</tbody></table></div>';
  }
  function mailLog(d) {
    var l = d.mail_log || [];
    if (!l.length) return '';
    return '<div class="mst-lbl">Mails</div><div style="margin-bottom:12px">' + l.map(function (x) {
      return '<div class="mst-frow"><span style="width:18px;color:' + (x.ok ? 'var(--green)' : 'var(--red)') + '">' + (x.ok ? '✓' : '✗') + '</span><span style="width:130px;color:var(--muted)">' + esc((x.at || '').slice(0, 16)) + '</span><span class="n">' + esc(x.date) + ' · ' + esc((x.files || []).join(', ')) + (x.ok ? '' : ' · ' + esc(x.error || '')) + '</span><span style="color:var(--dim);font-size:11px">' + esc(x.by || '') + '</span></div>';
    }).join('') + '</div>';
  }
  function logBox(d) {
    var lines = d.log || [];
    return '<div class="mst-lbl">Live log</div><div class="mst-logbox" id="mstLogBox">' + (lines.length ? lines.slice(-30).map(esc).join('\n') : 'No activity since the server started. The full log of every run is under 🗂️ Logs.') + '</div>';
  }
  function paintDyn(force) {
    var d = S.data, el = $('mstDyn');
    if (!el || !d || !d.steps) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('mstLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    el.innerHTML = banner(d) + kpis(d) + datesTable(d) + steps(d) + logBox(d) + mailLog(d) +
      '<div style="font-size:11px;color:var(--dim);line-height:1.6">Folder: <span style="font-family:var(--mono)">' + esc(d.folder) + '</span> · SA CM / SA FO / EODSAR / INTRA are read from the console\'s own SA04 REPORTING, EODSAR and MASTER FILE outputs of that date. ' +
      'Only the newest date newer than the last sent date is mailed; older or revised dates are only processed. Nothing is overwritten - every margin file stays as a version.</div>';
    var nb = $('mstLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }
  function paintTile(d) {
    var chip = $('mstChip'), mini = $('mstMini');
    if (!chip || !d || !d.steps) return;
    chip.textContent = d.stopping ? 'stopping' : d.running ? 'running' : d.status === 'failed' ? 'failed' : d.status === 'done' ? 'done' : 'idle';
    chip.style.color = d.running ? 'var(--blue)' : d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--dim)';
    var dates = d.dates || [], blocked = dates.filter(function (x) { return x.blocked; }).length, todo = dates.filter(function (x) { return x.todo && !x.blocked; }).length;
    mini.textContent = d.stopping ? 'Stopping…' : d.running ? 'Running…' : d.status === 'failed' ? 'Failed - open for details' :
      (blocked ? blocked + ' date(s) blocked - open for details' : todo ? todo + ' date(s) to build - press Run' : 'Last sent: ' + (d.last_sent || 'none') + (dates.length ? '' : ' · upload Margin_Detail'));
  }
  function load() {
    var next = 8000;
    return get(BASE + '/status').then(function (d) {
      S.data = d;
      if (d && d.steps) { paintTile(d); if (modalOpen()) paintDyn(); if (d.running || d.stopping) next = 2000; }
    }).catch(function () { next = 15000; }).then(function () { schedule(next); });
  }
  function schedule(ms) {
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (document.hidden) { schedule(ms); } else { load(); } }, ms);
  }
  function reload() { S.sig = ''; clearTimeout(S.timer); setTimeout(load, 700); }

  // ───────────────────────── actions ─────────────────────────
  window.mstRun = function () {
    post(BASE + '/run', {}).then(function (j) {
      if (j && j.ok) okBox('Run started', 'MARGIN SHORTFALL TRACK is running - watch the steps and the live log in the window.');
      else okBox('Could not start', (j && (j.error || j.msg || j.detail)) || 'The run did not start.', 'err');
      reload();
    });
  };
  window.mstStop = function () {
    confirmBox('Stop MARGIN SHORTFALL TRACK?', 'The run that is going now will be stopped. Files already built are kept.', 'Stop', function () {
      post(BASE + '/stop').then(function (j) {
        if (j && j.stopped) okBox('Stop requested', 'The run ends in a few seconds.'); else okBox('Nothing to stop', 'No run is going right now.', 'err');
        reload();
      });
    });
  };

  // ───────────────────────── Settings ─────────────────────────
  window.mstSettings = function () {
    ask('Settings', function (pw) {
      get(BASE + '/settings').then(function (s) {
        S.pw = pw;
        modal('⚙ ' + TITLE + ' - settings',
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:14px"><input type="checkbox" id="mstMail" ' + (s.send_mail !== false ? 'checked' : '') + '> Send the e-mail at the run</label>' +
          '<div class="mst-lbl">Last date already sent to RMS DEL (dd-mm-yyyy)</div><input type="text" id="mstLast" class="mst-in" style="width:100%;margin-bottom:6px" placeholder="19-11-2026" value="' + esc(s.last_sent || '') + '">' +
          '<div style="font-size:11.5px;color:var(--dim);line-height:1.55;margin-bottom:14px">Only a date NEWER than this is mailed. It moves forward by itself after every mail that goes. Set it once after importing the old files (the date of the last mail you sent by hand); leave it blank only if nothing was imported.</div>' +
          '<div class="mst-lbl">WhatsApp FAILURE-alert numbers (blank = the common alert numbers of Credentials > WhatsApp)</div><input type="text" id="mstAlert" class="mst-in" style="width:100%;margin-bottom:6px" placeholder="9876543210, 9123456789" value="' + esc(s.alert_numbers || '') + '">' +
          '<div style="font-size:11.5px;color:var(--dim);line-height:1.55;margin-bottom:14px">When a run of this card fails, these numbers get the red FAILURE ALERT picture on WhatsApp. 10-digit numbers, +91 is added; several separated by commas.</div>' +
          '<button class="sa-save" id="mstSetBtn" data-no-busy="1" style="width:100%" onclick="mstSetSave()">Save settings</button><div class="sa-msg" id="mstSetMsg"></div>');
      });
    });
  };
  window.mstSetSave = function () {
    var btn = $('mstSetBtn'), m = $('mstSetMsg'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post(BASE + '/settings', { password: S.pw, send_mail: $('mstMail').checked, last_sent: $('mstLast').value, alert_numbers: $('mstAlert').value }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = j && j.ok ? '✓ Saved' : 'Save settings'; }
      if (j && j.ok) okBox('Settings saved', 'E-mail is ' + ($('mstMail').checked ? 'ON' : 'OFF') + '; last sent date: ' + ($('mstLast').value.trim() || 'blank') + '.'); else okBox('Not saved', (j && j.error) || 'Could not save the settings.', 'err');
      if (m) m.innerHTML = j && j.ok ? '<span style="color:var(--green)">Saved.</span>' : '<span style="color:var(--red)">' + esc((j && j.error) || 'Failed') + '</span>';
      reload();
    });
  };

  // ───────────────────────── Backup files ─────────────────────────
  window.mstBackup = function () {
    S.bk = { from: iso(7), to: iso(0) };
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">Every margin version, FINAL and TRACK file is kept by date. Pick one day or a range.</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="mst-lbl">From</div><input type="date" id="mstBkFrom" class="mst-in" style="width:100%" value="' + S.bk.from + '" onchange="mstBkLoad()"></div>' +
      '<div><div class="mst-lbl">To</div><input type="date" id="mstBkTo" class="mst-in" style="width:100%" value="' + S.bk.to + '" onchange="mstBkLoad()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="mst-chip" onclick="mstBkQuick(0)">Today</button><button class="mst-chip" onclick="mstBkQuick(1)">Yesterday</button><button class="mst-chip" onclick="mstBkQuick(7)">Last 7 days</button><button class="mst-chip" onclick="mstBkQuick(30)">Last 30 days</button></div>' +
      '<div id="mstBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="mstBkGo" data-no-busy="1" style="width:100%" onclick="mstBkDownload()">⬇ Download zip</button>');
    widen(true);
    mstBkLoad();
  };
  window.mstBkQuick = function (n) { $('mstBkTo').value = n === 1 ? iso(1) : iso(0); $('mstBkFrom').value = iso(n); mstBkLoad(); };
  window.mstBkLoad = function () {
    var a = $('mstBkFrom').value, b = $('mstBkTo').value || a; if (b < a) { var x = a; a = b; b = x; }
    S.bk.from = a; S.bk.to = b;
    get(BASE + '/range?from_date=' + encodeURIComponent(a) + '&to_date=' + encodeURIComponent(b)).then(function (j) {
      var l = $('mstBkList'); if (!l) return;
      var byDay = {}; (j.files || []).forEach(function (f) { (byDay[f.date] = byDay[f.date] || []).push(f); });
      var days = Object.keys(byDay), tot = (j.files || []).length;
      l.innerHTML = tot ? days.map(function (day) {
        return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + (days.length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day) + ' <span style="font-weight:400;color:var(--dim)">· ' + byDay[day].length + ' files</span></summary>' +
          byDay[day].map(function (f) { return '<div class="mst-frow"><span class="n">' + esc(f.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span><a href="' + API + BASE + '/file?date=' + encodeURIComponent(day) + '&name=' + encodeURIComponent(f.name) + '" download>⬇</a></div>'; }).join('') + '</details>';
      }).join('') : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No files for that ' + (a === b ? 'day' : 'range') + '.</div>';
      var go = $('mstBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; }
    });
  };
  window.mstBkDownload = function () { toast('Preparing the zip - the download starts in a moment.'); window.location.href = API + BASE + '/zip-range?from_date=' + encodeURIComponent(S.bk.from) + '&to_date=' + encodeURIComponent(S.bk.to); };

  // ───────────────────────── Upload files ─────────────────────────
  window.mstUpload = function () {
    var old = $('mstUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.id = 'mstUpInput'; inp.multiple = true; inp.accept = '.xlsx,.xls,.csv,.zip'; inp.style.display = 'none';
    inp.onchange = function () { var fs = [].slice.call(inp.files || []); inp.remove(); if (fs.length) mstUpSend(fs); };
    document.body.appendChild(inp); inp.click();
  };
  var STAT = { new: ['NEW date', 'var(--green)'], revised: ['NEW VERSION', 'var(--amber)'], identical: ['identical - ignored', 'var(--dim)'], imported: ['imported', 'var(--blue)'],
               kept: ['kept (already built)', 'var(--dim)'], skipped: ['not needed', 'var(--dim)'], zip: ['zip', 'var(--blue)'] };
  function resultTable(j) {
    var rows = (j.results || []).map(function (r) {
      var s = STAT[r.status] || [r.status, 'var(--ink)'];
      return '<tr><td style="font-family:var(--mono);word-break:break-all">' + esc(r.name) + '</td><td style="white-space:nowrap">' + esc(r.date || '') + '</td><td style="color:' + s[1] + ';white-space:nowrap">' + s[0] + '</td><td style="color:var(--muted)">' + esc(r.msg || '') + '</td></tr>';
    }).concat((j.rejected || []).map(function (m) { return '<tr><td colspan="3" style="color:var(--red)">✕ not accepted</td><td style="color:var(--red)">' + esc(m) + '</td></tr>'; }));
    return '<table class="mst-tbl"><thead><tr><th>File</th><th>Date</th><th>Result</th><th></th></tr></thead><tbody>' + rows.join('') + '</tbody></table>';
  }
  function mstUpSend(files) {
    toast('Uploading ' + files.length + ' file(s)…', 'warn');
    var fd = new FormData(); files.forEach(function (f) { fd.append('files', f); });
    fetch(API + BASE + '/upload', { method: 'POST', body: fd }).then(function (r) { return r.json().then(function (j) { j._http = r.status; return j; }); }).then(function (j) {
      if (j.error || j.detail) { okBox('Upload failed', String(j.error || j.detail), 'err'); return; }
      var n = (j.results || []).length, bad = (j.rejected || []).length;
      okBox(bad && !n ? 'Nothing accepted' : 'Upload finished', n + ' file(s) processed' + (bad ? ', ' + bad + ' not accepted' : '') + '. Press Run to build and mail.', bad && !n ? 'err' : '', resultTable(j));
      reload();
    }).catch(function (e) { okBox('Upload failed', (e && e.message) || String(e), 'err'); });
  }

  // ───────────────────────── start ─────────────────────────
  function start() {
    if (build()) {
      load();
      var n = 0, iv = setInterval(function () { keepLast(); if (++n > 24) clearInterval(iv); }, 1500);
    } else setTimeout(start, 600);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
