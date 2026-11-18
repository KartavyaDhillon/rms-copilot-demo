/* TENTATIVE PAYIN PAYOUT - small tile of the Process Center (NARRATION_COMBINED.py as a card). "Open" shows the window: Upload PositionBook,
   Run (asks for the settlement prices first when ITM contracts expire today - typed, or taken from ANY csv / xlsx file whose SYMBOL and PRICE
   columns you pick), Stop, Credentials, Email To / CC, Backup files, Logs, Settings, KPI cards, step tiles, input files and the live log.
   Backend: /tpp/* (api.py, core/tpp_card.py) and the shared /px/* windows with pipe=tpp.
   Injected at serve time (see api.py /legacy). Every button answers with an in-console popup (never the browser's own). */
(function () {
  'use strict';
  if (window.__tppInjected) return;
  window.__tppInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'TENTATIVE PAYIN PAYOUT';
  var PIPE = 'tpp';
  var BASE = '/tpp';
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
    var old = $('tppToast'); if (old) old.remove();
    var el = document.createElement('div'); el.id = 'tppToast';
    var col = kind === 'err' ? 'var(--red,#f87171)' : kind === 'warn' ? 'var(--amber,#fbbf24)' : 'var(--green,#34d399)';
    el.style.cssText = 'position:fixed;top:22px;left:50%;transform:translateX(-50%);z-index:100000;max-width:min(560px,92vw);padding:12px 18px;border-radius:12px;' +
      'background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:13px;line-height:1.45;display:flex;gap:10px;align-items:flex-start';
    el.innerHTML = '<span style="color:' + col + ';font-weight:800">' + (kind === 'err' ? '✕' : kind === 'warn' ? '!' : '✓') + '</span><span>' + esc(text) + '</span>';
    document.body.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, kind === 'err' ? 7000 : 3800);
  }
  function okBox(title, text, kind) {      // a popup with an OK button (stays until OK is pressed)
    var old = $('tppOk'); if (old) old.remove();
    var col = kind === 'err' ? 'var(--red,#f87171)' : 'var(--green,#34d399)';
    var bg = document.createElement('div'); bg.id = 'tppOk';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100002;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px;color:' + col + '">' + (kind === 'err' ? '✕ ' : '✓ ') + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;justify-content:flex-end"><button class="sa-tool" data-no-busy="1" id="tppOkBtn" style="min-width:90px;background:' + col + ';color:#06251a;border-color:transparent">OK</button></div></div>';
    document.body.appendChild(bg);
    $('tppOkBtn').onclick = function () { bg.remove(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
    $('tppOkBtn').focus();
  }
  function confirmBox(title, text, okLabel, cb) {
    var old = $('tppConfirm'); if (old) old.remove();
    var bg = document.createElement('div'); bg.id = 'tppConfirm';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--line-2,#2a3342);border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px">' + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end"><button class="sa-tool" id="tppNo" style="min-width:90px">Cancel</button><button class="sa-tool" id="tppYes" style="min-width:90px;background:var(--red,#f87171);color:#2a0a0a;border-color:transparent">' + esc(okLabel || 'OK') + '</button></div></div>';
    document.body.appendChild(bg);
    $('tppNo').onclick = function () { bg.remove(); };
    $('tppYes').onclick = function () { bg.remove(); cb(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
  }

  var css = document.createElement('style');
  css.textContent =
    '.tpp-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.tpp-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.tpp-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.tpp-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.tpp-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.tpp-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.tpp-steps{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:12px}' +
    '@media(max-width:820px){.tpp-kpis,.tpp-steps{grid-template-columns:repeat(2,1fr)}}' +
    '.tpp-step{border:1px solid var(--line);border-radius:11px;padding:9px 11px;background:var(--panel-2);min-width:0}' +
    '.tpp-step.ok{border-color:rgba(52,211,153,.4)}.tpp-step.failed{border-color:rgba(248,113,113,.55)}.tpp-step.running{border-color:var(--amber)}' +
    '.tpp-step .n{font-size:12px;font-weight:650}.tpp-step .s{font-family:var(--mono);font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);margin-top:2px}' +
    '.tpp-step.ok .s{color:var(--green)}.tpp-step.failed .s{color:var(--red)}.tpp-step.running .s{color:var(--amber)}' +
    '.tpp-step .m{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.35;overflow:hidden;max-height:42px}' +
    '.tpp-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.tpp-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:170px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.tpp-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.tpp-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.tpp-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.tpp-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.tpp-frow a{color:var(--blue);text-decoration:none}.tpp-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.tpp-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '@keyframes tppSpin{to{transform:rotate(360deg)}}.tpp-spin{display:inline-block;width:12px;height:12px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:tppSpin .8s linear infinite;vertical-align:-2px;margin-right:6px}';
  document.head.appendChild(css);

  // ───────────────────────── the small tile ─────────────────────────
  function grid() {
    var t = $('task-obligation') || $('task-payoutchk') || $('task-mtforder') || $('task-mtfconc') || $('task-ms15card') || $('task-mcxcomm') || $('task-liveallocation');
    if (t && t.parentNode) return t.parentNode;
    var slot = $('physdelSlot');
    return slot ? slot.parentNode : null;
  }
  function build() {
    if ($('task-tpp')) return true;
    var g = grid(); if (!g) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-tpp';
    tile.style.cssText = '--tile-accent:#f59e0b;cursor:pointer';
    tile.onclick = function () { tppOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">🧾</div>' +
      '<div class="tile-name">TENTATIVE PAYIN PAYOUT <span class="st" id="tppChip">idle</span></div>' +
      '<div class="tile-desc">Upload today\'s PositionBook, Run: normal + custodian pay-in / pay-out report from the PositionBook and the risk report (COMMON FILES), settlement prices typed or read from any file, one password-protected mail, backup.</div>' +
      '<div class="tile-desc" id="tppMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#f59e0b;color:#2a1a02" onclick="event.stopPropagation();tppOpen()" data-tip="Open TENTATIVE PAYIN PAYOUT: upload, run (settlement prices), credentials, e-mail To / CC, backup files, logs, settings.">Open</button></div>';
    g.appendChild(tile);
    return true;
  }
  function keepLast() {
    if (window.__tilesManaged) return;      // process-organizer.js owns the card order (A-Z or your own)
    var el = $('task-tpp'); if (!el || !el.parentNode) return;
    var p = el.parentNode, bk = document.getElementById('task-entrisk'); bk = bk && bk.parentNode === p ? bk : null;
    var kids = [].slice.call(p.children), want = kids.filter(function (e) { return e !== el && e !== bk; }).concat([el]).concat(bk ? [bk] : []);
    if (kids.some(function (e, i) { return e !== want[i]; })) want.forEach(function (e) { p.appendChild(e); });
  }

  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('tppDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(900px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__tpp) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__tpp = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (fn.indexOf('tppRun') === 0 ? ' data-no-busy="1"' : '') + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  var RED = ';border-color:rgba(248,113,113,.45);color:var(--red)';
  window.tppSub = function (fn) { window._saReturn = tppOpen; fn(); };
  window.tppCreds = function () { tppSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.tppEmail = function () { tppSub(function () { if (typeof window.pxConfig === 'function') window.pxConfig(PIPE); }); };
  window.tppLogs = function () { tppSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function tppOpen() {
    window._saReturn = null;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 8px">' +
        tb('⬆️ Upload PositionBook', 'tppUpload()', 'The derivative PositionBook (not the CNC F11 one). Only TODAY\'s is taken: today\'s date in the file name (e.g. Positionbook_18112026.csv or .xlsx), or - if the name has no date - a file saved in the last 60 minutes. Every upload is written to Logs.') +
        tb('🔑 Credentials', 'tppCreds()', 'The e-mail login this card sends from.') +
        tb('✉️ Email To / CC', 'tppEmail()', 'Who gets the Tentative Obligation mail.') +
        tb('📦 Backup files', 'tppSub(tppBackup)', 'Pick a date or a range and download the backed-up files as one zip.') +
        tb('🗂️ Logs', 'tppLogs()', 'The full log of every run.') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 14px">' +
        tb('▶ Run', 'tppRun()', 'If ITM contracts expire today it first asks for the settlement prices (type them or take them from a file). Then it makes both reports, mails them and files everything in the backup. Once a day - a second run asks for the password.', ';background:var(--blue);color:var(--blue-d);border-color:transparent') +
        tb('⚙ Settings', 'tppSub(tppSettings)', 'E-mail on/off and the WhatsApp numbers that get the FAILURE ALERT.') +
        tb('⛔ Stop', 'tppStop()', 'Stops the run that is going right now.', RED) +
      '</div><div id="tppMsg"></div><div id="tppDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.tppOpen = tppOpen;

  function say(txt, kind) {
    if (txt) toast(txt, kind);
    var el = $('tppMsg');
    if (el) el.innerHTML = txt ? '<div class="tpp-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('tppMsg'); if (e) e.innerHTML = ''; }, 8000);
  }

  // ───────────────────────── render ─────────────────────────
  var ST = { idle: 'Not run yet today', running: 'Running…', done: '✓ Done', failed: '✗ Failed', stopped: 'Stopped' };
  function kpi(k, v, f) { return '<div class="tpp-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div></div>'; }
  function banner(d) {
    if (d.status === 'failed') return '<div class="tpp-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>Today\'s run FAILED.</b> ' + esc(d.error || '') + ' <span style="color:var(--dim)">· ' + (d.alert_sent ? 'WhatsApp alert sent' : 'alert not sent' + (d.alert_reason ? ' (' + esc(d.alert_reason) + ')' : '')) + ' · fix it and press <b>Run</b> again (no password needed).</span></div>';
    if (d.status === 'done') return '<div class="tpp-ban" style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:var(--green)"><b>✓ Done today</b> - ' + esc(((d.steps || []).filter(function (x) { return x.key === 'email'; })[0] || {}).msg || '') + ' · Run again asks for the password.</div>';
    return '<div class="tpp-ban" style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.35);color:var(--blue)">Upload today\'s PositionBook (the risk report is read from COMMON FILES), then press <b>Run</b>. One run a day, no timer.' + ((d.settings || {}).send_mail === false ? ' <b style="color:var(--amber)">E-mail is switched OFF.</b>' : '') + '</div>';
  }
  function kpis(d) {
    var ins = d.inputs || [], ok = ins.filter(function (x) { return x.ok; }).length, c = d.counts || {};
    var run = d.stopping ? 'Stopping…' : d.running ? 'Running' : (ST[d.status] || d.status);
    return '<div class="tpp-kpis">' +
      kpi('Today', '<span style="color:' + (d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--ink)') + '">' + run + '</span>', (d.started ? 'started ' + d.started.slice(11, 16) : 'not started') + (d.trigger ? ' · ' + d.trigger : '') + (d.by ? ' · ' + d.by : '')) +
      kpi('Input files', ok + '<small> / ' + ins.length + '</small>', ok === ins.length ? 'PositionBook + risk report in place' : (ins.filter(function (x) { return !x.ok; })[0] || {}).label + ': ' + ((ins.filter(function (x) { return !x.ok; })[0] || {}).why || 'missing')) +
      kpi('Settlement prices', (d.settlement || {}).count || '—', (d.settlement || {}).count ? esc((d.settlement || {}).source || 'saved') : 'asked when you press Run') +
      kpi('E-mail', (d.sent || []).length ? 'sent' : '—', (d.sent || []).length ? 'Tentative Obligation file mailed' : 'not sent yet') + '</div>';
  }
  function steps(d) {
    return '<div class="tpp-lbl">Steps</div><div class="tpp-steps">' + (d.steps || []).map(function (s) {
      var c = s.status === 'ok' || s.status === 'skipped' || s.status === 'done' ? 'ok' : s.status === 'failed' ? 'failed' : s.status === 'running' ? 'running' : '';
      var t = { ok: '✓ done', done: '✓ in place', skipped: '✓ skipped', failed: '✗ failed', running: 'running…', stopped: 'stopped' }[s.status] || '—';
      return '<div class="tpp-step ' + c + '"><div class="n">' + esc(s.label) + '</div><div class="s">' + t + '</div><div class="m" title="' + esc(s.msg) + '">' + esc(s.msg) + '</div></div>';
    }).join('') + '</div>';
  }
  function inputs(d) {
    return '<div class="tpp-lbl">Input files</div><div style="margin-bottom:12px">' + (d.inputs || []).map(function (x) {
      return '<div class="tpp-frow"><span style="width:18px;color:' + (x.ok ? 'var(--green)' : 'var(--red)') + '">' + (x.ok ? '✓' : '✗') + '</span><span style="width:190px;color:var(--muted)">' + esc(x.label) + '</span><span class="n">' + (x.found ? esc(x.name) : '— not there (' + esc(x.name) + ')') + '</span>' +
        '<span style="color:var(--dim);font-size:11px">' + (x.found ? kb(x.size) + ' · ' + esc(x.time) + (x.why ? ' · ' + esc(x.why) : '') : esc(x.why || '')) + '</span></div>';
    }).join('') + '</div>';
  }
  function logBox(d) {
    var lines = d.log || [];
    return '<div class="tpp-lbl">Live log</div><div class="tpp-logbox" id="tppLogBox">' + (lines.length ? lines.slice(-30).map(esc).join('\n') : 'No activity since the server started. The full log of every run is under 🗂️ Logs.') + '</div>';
  }
  function paintDyn(force) {
    var d = S.data, el = $('tppDyn');
    if (!el || !d || !d.steps) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('tppLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    el.innerHTML = banner(d) + kpis(d) + steps(d) + logBox(d) + inputs(d) +
      '<div style="font-size:11px;color:var(--dim);line-height:1.6">Folder: <span style="font-family:var(--mono)">' + esc(d.folder) + '</span> · after a successful run the day\'s files move to <span style="font-family:var(--mono)">BACKUP FILES AUTOMATED / dd-mm-yyyy</span> (the risk report is only copied). ' +
      'If the e-mail does not go, the run FAILS and the files stay where they are.</div>';
    var nb = $('tppLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }
  function paintTile(d) {
    var chip = $('tppChip'), mini = $('tppMini');
    if (!chip || !d || !d.steps) return;
    chip.textContent = d.stopping ? 'stopping' : d.running ? 'running' : d.status === 'failed' ? 'failed' : d.status === 'done' ? 'done' : 'idle';
    chip.style.color = d.running ? 'var(--blue)' : d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--dim)';
    var ok = (d.inputs || []).filter(function (x) { return x.ok; }).length;
    mini.textContent = d.stopping ? 'Stopping…' : d.running ? 'Running…' : d.status === 'done' ? 'Done today' : d.status === 'failed' ? 'Failed - open for details' : (ok >= 2 ? 'Files are in - press Run' : 'Upload the PositionBook');
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
  function startRun(pw) {
    post(BASE + '/run', pw ? { password: pw } : {}).then(function (j) {
      if (j && j.need_password) { ask('Run again (today\'s run already succeeded)', function (p) { startRun(p); }); return; }
      if (j && j.ok) okBox('Run started', 'TENTATIVE PAYIN PAYOUT is running - watch the steps and the live log in the window.');
      else okBox('Could not start', (j && (j.error || j.msg || j.detail)) || 'The run did not start.', 'err');
      reload();
    });
  }
  // ───────────────────────── Run: settlement prices first ─────────────────────────
  function proceedRun(pw) {
    get(BASE + '/settle/need').then(function (j) {
      if (!j || !j.ok) { okBox('Cannot start', (j && (j.error || j.detail)) || 'The PositionBook could not be checked.', 'err'); return; }
      if (!(j.symbols || []).length) { startRun(pw); return; }                 // no ITM contract expires today - nothing to ask
      settleDialog(j.symbols, function () { startRun(pw); });
    }).catch(function (e) { okBox('Cannot start', (e && e.message) || String(e), 'err'); });
  }
  window.tppRun = function () {
    if (S.data && S.data.needs_password) { ask('Run again (today\'s run already succeeded)', function (p) { proceedRun(p); }); return; }
    proceedRun('');
  };

  function overlay(id, z) {
    var old = $(id); if (old) old.remove();
    var bg = document.createElement('div'); bg.id = id;
    bg.style.cssText = 'position:fixed;inset:0;z-index:' + z + ';background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center';
    document.body.appendChild(bg);
    return bg;
  }
  var BOX = 'background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--line-2,#2a3342);border-radius:14px;padding:20px 22px;box-shadow:0 20px 60px rgba(0,0,0,.6);max-height:90vh;overflow:auto;';

  function settleDialog(symbols, onSubmit) {
    S.settleToken = ''; S.settleName = '';
    var bg = overlay('tppSet', 100001);
    var rows = symbols.map(function (m, i) {
      return '<div style="display:flex;gap:12px;align-items:center;border:1px solid var(--line,#222);border-radius:10px;padding:9px 12px;margin-bottom:8px">' +
        '<div style="flex:1;min-width:0"><div style="font-size:14px;font-weight:700">' + esc(m.symbol) + (m.optional ? ' <span style="font-size:10px;color:var(--amber,#fbbf24);font-weight:600">OPTIONAL - blank = skip</span>' : '') + '</div>' +
        '<div style="font-size:11px;color:var(--dim)">' + esc(m.exch) + ' · ' + m.rows + ' ITM rows · strikes ' + esc(m.strikes) + '</div></div>' +
        '<span style="color:var(--green);font-weight:700">₹</span><input type="text" inputmode="decimal" class="tpp-in" id="tppP' + i + '" data-sym="' + esc(m.symbol) + '" data-opt="' + (m.optional ? '1' : '') + '" style="width:130px;text-align:right;font-weight:700" value="' + (m.price != null ? esc(String(m.price)) : '') + '"></div>';
    }).join('');
    bg.innerHTML = '<div style="' + BOX + 'width:min(560px,94vw)">' +
      '<div style="font-size:16px;font-weight:700;margin-bottom:4px">📊 Expiry settlement prices</div>' +
      '<div style="font-size:12px;color:var(--muted);line-height:1.5;margin-bottom:12px">ITM contracts expiring today were found. Enter the final settlement price for each symbol - or add them from any csv / Excel file: you tell the console which column is the <b>SYMBOL</b> and which is the <b>PRICE</b>.</div>' +
      '<div id="tppSetRows">' + rows + '</div><div id="tppSetMsg" style="font-size:12px;min-height:18px;margin:6px 0 12px"></div>' +
      '<div style="display:flex;gap:10px;justify-content:space-between;flex-wrap:wrap"><button class="sa-tool" data-no-busy="1" id="tppSetFile">📂 Add prices from a file</button>' +
      '<span style="display:flex;gap:10px"><button class="sa-tool" data-no-busy="1" id="tppSetNo" style="min-width:90px">Cancel</button><button class="sa-tool" data-no-busy="1" id="tppSetYes" style="min-width:130px;background:var(--green,#34d399);color:#06251a;border-color:transparent">✅ Submit &amp; Run</button></span></div></div>';
    var first = bg.querySelector('input'); if (first) first.focus();
    function msg(t, bad) { var e = $('tppSetMsg'); if (e) e.innerHTML = t ? '<span style="color:' + (bad ? 'var(--red,#f87171)' : 'var(--green,#34d399)') + '">' + t + '</span>' : ''; }
    $('tppSetNo').onclick = function () { bg.remove(); };
    $('tppSetFile').onclick = function () { pickSettleFile(function (res) {
      var n = 0, inputs = [].slice.call(bg.querySelectorAll('input[data-sym]'));
      inputs.forEach(function (i) { var v = res.prices[String(i.dataset.sym).toUpperCase()]; if (v != null) { i.value = String(v); n++; } });
      S.settleToken = res.token; S.settleName = res.name;
      var miss = inputs.filter(function (i) { return res.prices[String(i.dataset.sym).toUpperCase()] == null; }).map(function (i) { return i.dataset.sym; });
      msg('Loaded ' + n + ' of ' + inputs.length + ' price(s) from ' + esc(res.name) + (miss.length ? ' - still to enter: ' + esc(miss.join(', ')) : ' - check them and press Submit &amp; Run.'), miss.length > 0);
    }); };
    function submit() {
      var prices = {}, bad = '';
      [].slice.call(bg.querySelectorAll('input[data-sym]')).forEach(function (i) {
        var t = i.value.replace(/,/g, '').trim();
        if (t === '' && i.dataset.opt) return;
        var v = parseFloat(t);
        if (!(v > 0) || !/^[0-9]*\.?[0-9]+$/.test(t)) { if (!bad) { bad = i.dataset.sym; i.focus(); } return; }
        prices[i.dataset.sym] = v;
      });
      if (bad) { msg('⚠ Enter a valid settlement price for ' + esc(bad), true); return; }
      var btn = $('tppSetYes'); btn.disabled = true; btn.textContent = 'Saving…';
      post(BASE + '/settle/save', { prices: prices, source: S.settleName ? 'file ' + S.settleName : 'typed in the popup', token: S.settleToken || '' }).then(function (j) {
        btn.disabled = false; btn.innerHTML = '✅ Submit &amp; Run';
        if (!j || !j.ok) { msg(esc((j && (j.error || j.detail)) || 'Could not save the prices.'), true); return; }
        bg.remove(); onSubmit();
      });
    }
    $('tppSetYes').onclick = submit;
    bg.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
  }

  // any csv / Excel file -> pick which column is SYMBOL and which is PRICE
  function pickSettleFile(done) {
    var old = $('tppSetPick'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.id = 'tppSetPick'; inp.accept = '.csv,.xlsx,.xlsm,.xls,.txt'; inp.style.display = 'none';
    inp.onchange = function () {
      var f = inp.files && inp.files[0]; inp.remove(); if (!f) return;
      toast('Reading ' + f.name + '…', 'warn');
      var fd = new FormData(); fd.append('file', f);
      fetch(API + BASE + '/settle/inspect', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (j) {
        if (!j || !j.ok) { okBox('Cannot read the file', (j && (j.error || j.detail)) || 'The file could not be read.', 'err'); return; }
        mapDialog(j, done);
      }).catch(function (e) { okBox('Cannot read the file', (e && e.message) || String(e), 'err'); });
    };
    document.body.appendChild(inp); inp.click();
  }

  function mapDialog(info, done) {
    var bg = overlay('tppMap', 100003);
    function opts(sel, cols) { return '<option value="">— choose —</option>' + cols.map(function (c) { return '<option value="' + esc(c) + '"' + (c === sel ? ' selected' : '') + '>' + esc(c) + '</option>'; }).join(''); }
    function paint(i) {
      var g = i.guess || {}, cols = i.columns || [];
      var head = '<tr>' + cols.map(function (c) { return '<th style="text-align:left;padding:4px 8px;border-bottom:1px solid var(--line-2);white-space:nowrap">' + esc(c) + '</th>'; }).join('') + '</tr>';
      var body = (i.sample || []).map(function (r) { return '<tr>' + r.map(function (x) { return '<td style="padding:3px 8px;border-bottom:1px solid var(--line);white-space:nowrap;color:var(--muted)">' + esc(x) + '</td>'; }).join('') + '</tr>'; }).join('');
      bg.innerHTML = '<div style="' + BOX + 'width:min(760px,95vw)">' +
        '<div style="font-size:16px;font-weight:700;margin-bottom:4px">📂 ' + esc(i.name) + '</div>' +
        '<div style="font-size:12px;color:var(--muted);line-height:1.5;margin-bottom:12px">' + i.rows + ' data row(s). Tell the console which column holds the <b>SYMBOL</b> (the same name as in the PositionBook, e.g. NIFTY) and which holds the <b>PRICE</b>.' + (g.remembered ? ' <span style="color:var(--green)">Your last choice is selected.</span>' : '') + '</div>' +
        '<div style="display:grid;grid-template-columns:' + ((i.sheets || []).length > 1 ? '1fr ' : '') + '110px 1fr 1fr;gap:12px;margin-bottom:12px">' +
          ((i.sheets || []).length > 1 ? '<div><div class="tpp-lbl">Sheet</div><select id="tppMapSheet" class="tpp-in" style="width:100%">' + i.sheets.map(function (s) { return '<option' + (s === i.sheet ? ' selected' : '') + '>' + esc(s) + '</option>'; }).join('') + '</select></div>' : '') +
          '<div><div class="tpp-lbl">Header row</div><input type="number" min="1" id="tppMapHr" class="tpp-in" style="width:100%" value="' + i.header_row + '"></div>' +
          '<div><div class="tpp-lbl">SYMBOL column</div><select id="tppMapSym" class="tpp-in" style="width:100%">' + opts(g.symbol_col, cols) + '</select></div>' +
          '<div><div class="tpp-lbl">PRICE column</div><select id="tppMapPrice" class="tpp-in" style="width:100%">' + opts(g.price_col, cols) + '</select></div></div>' +
        '<div class="tpp-lbl">Preview</div><div style="overflow:auto;max-height:190px;border:1px solid var(--line-2);border-radius:9px;margin-bottom:10px;font-family:var(--mono);font-size:11px"><table style="border-collapse:collapse;width:100%">' + head + body + '</table></div>' +
        '<div id="tppMapMsg" style="font-size:12px;min-height:18px;margin-bottom:10px"></div>' +
        '<div style="display:flex;gap:10px;justify-content:flex-end"><button class="sa-tool" data-no-busy="1" id="tppMapNo" style="min-width:90px">Cancel</button><button class="sa-tool" data-no-busy="1" id="tppMapYes" style="min-width:130px;background:var(--blue,#60a5fa);color:#06182f;border-color:transparent">Read prices</button></div></div>';
      $('tppMapNo').onclick = function () { bg.remove(); };
      function reinspect() {
        var fd = new FormData(); fd.append('token', i.token); if ($('tppMapSheet')) fd.append('sheet', $('tppMapSheet').value); fd.append('header_row', $('tppMapHr').value || '1');
        fetch(API + BASE + '/settle/inspect', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (j) { if (j && j.ok) paint(j); else { var m = $('tppMapMsg'); if (m) m.innerHTML = '<span style="color:var(--red)">' + esc((j && j.error) || 'Cannot read that sheet') + '</span>'; } });
      }
      if ($('tppMapSheet')) $('tppMapSheet').onchange = reinspect;
      $('tppMapHr').onchange = reinspect;
      $('tppMapYes').onclick = function () {
        var sc = $('tppMapSym').value, pc = $('tppMapPrice').value, m = $('tppMapMsg');
        if (!sc || !pc) { m.innerHTML = '<span style="color:var(--red)">Choose the SYMBOL column and the PRICE column.</span>'; return; }
        if (sc === pc) { m.innerHTML = '<span style="color:var(--red)">The SYMBOL and the PRICE column must be different.</span>'; return; }
        var b = $('tppMapYes'); b.disabled = true; b.textContent = 'Reading…';
        post(BASE + '/settle/apply', { token: i.token, symbol_col: sc, price_col: pc, sheet: i.sheet, header_row: i.header_row }).then(function (j) {
          b.disabled = false; b.textContent = 'Read prices';
          if (!j || !j.ok) { m.innerHTML = '<span style="color:var(--red)">' + esc((j && (j.error || j.detail)) || 'Could not read the prices.') + '</span>'; return; }
          bg.remove(); done(j);
        });
      };
    }
    paint(info);
  }
  window.tppStop = function () {
    confirmBox('Stop TENTATIVE PAYIN PAYOUT?', 'The run that is going now will be stopped. Files are kept where they are.', 'Stop', function () {
      post(BASE + '/stop').then(function (j) {
        if (j && j.stopped) okBox('Stop requested', 'The run ends in a few seconds.'); else okBox('Nothing to stop', 'No run is going right now.', 'err');
        reload();
      });
    });
  };

  // ───────────────────────── Settings ─────────────────────────
  window.tppSettings = function () {
    ask('Settings', function (pw) {
      get(BASE + '/settings').then(function (s) {
        S.pw = pw;
        modal('⚙ ' + TITLE + ' - settings',
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:14px"><input type="checkbox" id="tppMail" ' + (s.send_mail !== false ? 'checked' : '') + '> Send the e-mail at the run</label>' +
          '<div class="tpp-lbl">WhatsApp FAILURE-alert numbers (blank = the common alert numbers of Credentials > WhatsApp)</div><input type="text" id="tppAlert" class="tpp-in" style="width:100%;margin-bottom:6px" placeholder="9876543210, 9123456789" value="' + esc(s.alert_numbers || '') + '">' +
          '<div style="font-size:11.5px;color:var(--dim);line-height:1.55;margin-bottom:14px">When a run of this card fails, these numbers get the red FAILURE ALERT picture on WhatsApp. 10-digit numbers, +91 is added; several separated by commas.</div>' +
          '<button class="sa-save" id="tppSetBtn" data-no-busy="1" style="width:100%" onclick="tppSetSave()">Save settings</button><div class="sa-msg" id="tppSetMsg"></div>');
      });
    });
  };
  window.tppSetSave = function () {
    var btn = $('tppSetBtn'), m = $('tppSetMsg'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post(BASE + '/settings', { password: S.pw, send_mail: $('tppMail').checked, alert_numbers: $('tppAlert').value }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = j && j.ok ? '✓ Saved' : 'Save settings'; }
      if (j && j.ok) okBox('Settings saved', 'E-mail is ' + ($('tppMail').checked ? 'ON' : 'OFF') + '; failure-alert numbers: ' + ($('tppAlert').value.trim() || 'common list') + '.'); else okBox('Not saved', (j && j.error) || 'Could not save the settings.', 'err');
      if (m) m.innerHTML = j && j.ok ? '<span style="color:var(--green)">Saved.</span>' : '<span style="color:var(--red)">' + esc((j && j.error) || 'Failed') + '</span>';
      reload();
    });
  };

  // ───────────────────────── Backup files ─────────────────────────
  window.tppBackup = function () {
    S.bk = { from: iso(0), to: iso(0) };
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">After every successful run the day\'s files are kept in <span style="font-family:var(--mono)">BACKUP FILES AUTOMATED / dd-mm-yyyy</span>. Pick one day or a range.</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="tpp-lbl">From</div><input type="date" id="tppBkFrom" class="tpp-in" style="width:100%" value="' + S.bk.from + '" onchange="tppBkLoad()"></div>' +
      '<div><div class="tpp-lbl">To</div><input type="date" id="tppBkTo" class="tpp-in" style="width:100%" value="' + S.bk.to + '" onchange="tppBkLoad()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="tpp-chip" onclick="tppBkQuick(0)">Today</button><button class="tpp-chip" onclick="tppBkQuick(1)">Yesterday</button><button class="tpp-chip" onclick="tppBkQuick(7)">Last 7 days</button><button class="tpp-chip" onclick="tppBkQuick(30)">Last 30 days</button></div>' +
      '<div id="tppBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="tppBkGo" data-no-busy="1" style="width:100%" onclick="tppBkDownload()">⬇ Download zip</button>');
    widen(true);
    tppBkLoad();
  };
  window.tppBkQuick = function (n) { $('tppBkTo').value = n === 1 ? iso(1) : iso(0); $('tppBkFrom').value = iso(n); tppBkLoad(); };
  window.tppBkLoad = function () {
    var a = $('tppBkFrom').value, b = $('tppBkTo').value || a; if (b < a) { var x = a; a = b; b = x; }
    S.bk.from = a; S.bk.to = b;
    get(BASE + '/range?from_date=' + encodeURIComponent(a) + '&to_date=' + encodeURIComponent(b)).then(function (j) {
      var l = $('tppBkList'); if (!l) return;
      var byDay = {}; (j.files || []).forEach(function (f) { (byDay[f.date] = byDay[f.date] || []).push(f); });
      var days = Object.keys(byDay), tot = (j.files || []).length;
      l.innerHTML = tot ? days.map(function (day) {
        return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + (days.length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day) + ' <span style="font-weight:400;color:var(--dim)">· ' + byDay[day].length + ' files</span></summary>' +
          byDay[day].map(function (f) { return '<div class="tpp-frow"><span class="n">' + esc(f.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span><a href="' + API + BASE + '/file?date=' + encodeURIComponent(day) + '&name=' + encodeURIComponent(f.name) + '" download>⬇</a></div>'; }).join('') + '</details>';
      }).join('') : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No backed-up files for that ' + (a === b ? 'day' : 'range') + '.</div>';
      var go = $('tppBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; }
    });
  };
  window.tppBkDownload = function () { toast('Preparing the zip - the download starts in a moment.'); window.location.href = API + BASE + '/zip-range?from_date=' + encodeURIComponent(S.bk.from) + '&to_date=' + encodeURIComponent(S.bk.to); };

  // ───────────────────────── Upload PositionBook ─────────────────────────
  window.tppUpload = function () {
    var old = $('tppUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.id = 'tppUpInput'; inp.accept = '.csv,.xlsx,.xls'; inp.style.display = 'none';
    inp.onchange = function () { var f = inp.files && inp.files[0]; inp.remove(); if (f) tppUpSend(f); };
    document.body.appendChild(inp); inp.click();
  };
  function tppUpSend(file) {
    toast('Uploading ' + file.name + '…', 'warn');
    var fd = new FormData(); fd.append('files', file); fd.append('modified', String(file.lastModified || ''));      // the file's own saved time: the server checks it when the name has no date
    fetch(API + BASE + '/upload', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.error || j.detail) { okBox('Upload failed', String(j.error || j.detail), 'err'); return; }
      if ((j.ok || []).length) okBox('PositionBook uploaded', 'Saved as ' + j.ok[0] + ' - press Run to make the reports.');
      else okBox('Not accepted', (j.rejected || ['The file was not accepted.']).join(' '), 'err');
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
