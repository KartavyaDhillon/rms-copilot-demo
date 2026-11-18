/* CNC F11 - small tile of the Process Center. "Open" shows the window: Upload PositionBook, Run, Stop, Credentials, Email To / CC,
   Backup files, Logs, Settings (mail on/off, WhatsApp failure-alert numbers), KPI cards, step tiles, the input file and the live log.
   Job (CNC_final.py): PositionBook.csv -> buying / selling match -> Sheet4 -> CNC_F11_DATED_<date>.xlsx mailed -> backup.
   Backend: /cnc-f11/* (api.py, core/cnc_f11_card.py) and the shared /px/* windows with pipe=cncf11.
   Injected at serve time (see api.py /legacy). Every button answers with an in-console popup (never the browser's own). */
(function () {
  'use strict';
  if (window.__cncF11Injected) return;
  window.__cncF11Injected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'CNC F11';
  var PIPE = 'cncf11';
  var BASE = '/cnc-f11';
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
    var old = $('cncToast'); if (old) old.remove();
    var el = document.createElement('div'); el.id = 'cncToast';
    var col = kind === 'err' ? 'var(--red,#f87171)' : kind === 'warn' ? 'var(--amber,#fbbf24)' : 'var(--green,#34d399)';
    el.style.cssText = 'position:fixed;top:22px;left:50%;transform:translateX(-50%);z-index:100000;max-width:min(560px,92vw);padding:12px 18px;border-radius:12px;' +
      'background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:13px;line-height:1.45;display:flex;gap:10px;align-items:flex-start';
    el.innerHTML = '<span style="color:' + col + ';font-weight:800">' + (kind === 'err' ? '✕' : kind === 'warn' ? '!' : '✓') + '</span><span>' + esc(text) + '</span>';
    document.body.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, kind === 'err' ? 7000 : 3800);
  }
  function okBox(title, text, kind) {      // a popup with an OK button (stays until OK is pressed)
    var old = $('cncOk'); if (old) old.remove();
    var col = kind === 'err' ? 'var(--red,#f87171)' : 'var(--green,#34d399)';
    var bg = document.createElement('div'); bg.id = 'cncOk';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100002;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px;color:' + col + '">' + (kind === 'err' ? '✕ ' : '✓ ') + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;justify-content:flex-end"><button class="sa-tool" data-no-busy="1" id="cncOkBtn" style="min-width:90px;background:' + col + ';color:#06251a;border-color:transparent">OK</button></div></div>';
    document.body.appendChild(bg);
    $('cncOkBtn').onclick = function () { bg.remove(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
    $('cncOkBtn').focus();
  }
  function confirmBox(title, text, okLabel, cb) {
    var old = $('cncConfirm'); if (old) old.remove();
    var bg = document.createElement('div'); bg.id = 'cncConfirm';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--line-2,#2a3342);border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px">' + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end"><button class="sa-tool" id="cncNo" style="min-width:90px">Cancel</button><button class="sa-tool" id="cncYes" style="min-width:90px;background:var(--red,#f87171);color:#2a0a0a;border-color:transparent">' + esc(okLabel || 'OK') + '</button></div></div>';
    document.body.appendChild(bg);
    $('cncNo').onclick = function () { bg.remove(); };
    $('cncYes').onclick = function () { bg.remove(); cb(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
  }

  var css = document.createElement('style');
  css.textContent =
    '.cnc-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.cnc-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.cnc-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.cnc-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.cnc-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.cnc-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.cnc-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}' +
    '@media(max-width:820px){.cnc-kpis,.cnc-steps{grid-template-columns:repeat(2,1fr)}}' +
    '.cnc-step{border:1px solid var(--line);border-radius:11px;padding:9px 11px;background:var(--panel-2);min-width:0}' +
    '.cnc-step.ok{border-color:rgba(52,211,153,.4)}.cnc-step.failed{border-color:rgba(248,113,113,.55)}.cnc-step.running{border-color:var(--amber)}' +
    '.cnc-step .n{font-size:12px;font-weight:650}.cnc-step .s{font-family:var(--mono);font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);margin-top:2px}' +
    '.cnc-step.ok .s{color:var(--green)}.cnc-step.failed .s{color:var(--red)}.cnc-step.running .s{color:var(--amber)}' +
    '.cnc-step .m{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.35;overflow:hidden;max-height:42px}' +
    '.cnc-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.cnc-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:170px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.cnc-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.cnc-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.cnc-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.cnc-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.cnc-frow a{color:var(--blue);text-decoration:none}.cnc-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.cnc-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '@keyframes cncSpin{to{transform:rotate(360deg)}}.cnc-spin{display:inline-block;width:12px;height:12px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:cncSpin .8s linear infinite;vertical-align:-2px;margin-right:6px}';
  document.head.appendChild(css);

  // ───────────────────────── the small tile ─────────────────────────
  function grid() {
    var t = $('task-mtforder') || $('task-mtfconc') || $('task-ms15card') || $('task-mcxcomm') || $('task-liveallocation');
    if (t && t.parentNode) return t.parentNode;
    var slot = $('physdelSlot');
    return slot ? slot.parentNode : null;
  }
  function build() {
    if ($('task-cncf11')) return true;
    var g = grid(); if (!g) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-cncf11';
    tile.style.cssText = '--tile-accent:#a78bfa;cursor:pointer';
    tile.onclick = function () { cncOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">📑</div>' +
      '<div class="tile-name">CNC F11 <span class="st" id="cncChip">idle</span></div>' +
      '<div class="tile-desc">Upload the PositionBook, Run: matches the MTF / NRML buy rows with the MTFS / CNC sell rows, makes Sheet4 (CNC_F11_DATED file), mails it and files everything in the backup.</div>' +
      '<div class="tile-desc" id="cncMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#a78bfa;color:#1d1240" onclick="event.stopPropagation();cncOpen()" data-tip="Open CNC F11: upload, run, credentials, e-mail To / CC, backup files, logs, settings.">Open</button></div>';
    g.appendChild(tile);
    return true;
  }
  function keepLast() {
    if (window.__tilesManaged) return;      // process-organizer.js owns the card order (A-Z or your own)
    var el = $('task-cncf11'); if (!el || !el.parentNode) return;
    var p = el.parentNode, bk = document.getElementById('task-entrisk'); bk = bk && bk.parentNode === p ? bk : null;
    var kids = [].slice.call(p.children), want = kids.filter(function (e) { return e !== el && e !== bk; }).concat([el]).concat(bk ? [bk] : []);
    if (kids.some(function (e, i) { return e !== want[i]; })) want.forEach(function (e) { p.appendChild(e); });
  }

  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('cncDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(900px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__cnc) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__cnc = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (fn.indexOf('cncRun') === 0 ? ' data-no-busy="1"' : '') + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  var RED = ';border-color:rgba(248,113,113,.45);color:var(--red)';
  window.cncSub = function (fn) { window._saReturn = cncOpen; fn(); };
  window.cncCreds = function () { cncSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.cncEmail = function () { cncSub(function () { if (typeof window.pxConfig === 'function') window.pxConfig(PIPE); }); };
  window.cncLogs = function () { cncSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function cncOpen() {
    window._saReturn = null;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 8px">' +
        tb('⬆️ Upload PositionBook', 'cncUpload()', 'Only TODAY\'s PositionBook is taken: today\'s date in the file name (e.g. PositionBook_18112026.csv), or - if the name has no date - a file saved in the last 30 minutes. Kept as PositionBook.csv; every upload is written to Logs.') +
        tb('🔑 Credentials', 'cncCreds()', 'The e-mail login this card sends from.') +
        tb('✉️ Email To / CC', 'cncEmail()', 'Who gets the CNC_F11 file.') +
        tb('📦 Backup files', 'cncSub(cncBackup)', 'Pick a date or a range and download the backed-up files as one zip.') +
        tb('🗂️ Logs', 'cncLogs()', 'The full log of every run.') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 14px">' +
        tb('▶ Run', 'cncRun()', 'Makes Sheet4, mails the CNC_F11 file and files everything in the backup. If today\'s run already succeeded it asks for the password.', ';background:var(--blue);color:var(--blue-d);border-color:transparent') +
        tb('⚙ Settings', 'cncSub(cncSettings)', 'E-mail on/off and the WhatsApp numbers that get the FAILURE ALERT.') +
        tb('⛔ Stop', 'cncStop()', 'Stops the run that is going right now.', RED) +
      '</div><div id="cncMsg"></div><div id="cncDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.cncOpen = cncOpen;

  function say(txt, kind) {
    if (txt) toast(txt, kind);
    var el = $('cncMsg');
    if (el) el.innerHTML = txt ? '<div class="cnc-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('cncMsg'); if (e) e.innerHTML = ''; }, 8000);
  }

  // ───────────────────────── render ─────────────────────────
  var ST = { idle: 'Not run yet today', running: 'Running…', done: '✓ Done', failed: '✗ Failed', stopped: 'Stopped' };
  function kpi(k, v, f) { return '<div class="cnc-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div></div>'; }
  function banner(d) {
    if (d.status === 'failed') return '<div class="cnc-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>Today\'s run FAILED.</b> ' + esc(d.error || '') + ' <span style="color:var(--dim)">· ' + (d.alert_sent ? 'WhatsApp alert sent' : 'alert not sent' + (d.alert_reason ? ' (' + esc(d.alert_reason) + ')' : '')) + ' · fix it and press <b>Run</b> again (no password needed).</span></div>';
    if (d.status === 'done') return '<div class="cnc-ban" style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:var(--green)"><b>✓ Done today</b> - ' + ((d.counts || {}).sheet4 || 0) + ' row(s) in Sheet4 · mailed: ' + esc((d.sent || []).length ? 'yes' : 'no (e-mail is off in Settings)') + ' · Run again asks for the password.</div>';
    return '<div class="cnc-ban" style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.35);color:var(--blue)">Upload today\'s PositionBook, then press <b>Run</b>. There is no timer for this card.' + ((d.settings || {}).send_mail === false ? ' <b style="color:var(--amber)">E-mail is switched OFF.</b>' : '') + '</div>';
  }
  function kpis(d) {
    var ins = d.inputs || [], ok = ins.filter(function (x) { return x.ok; }).length, c = d.counts || {};
    var run = d.stopping ? 'Stopping…' : d.running ? 'Running' : (ST[d.status] || d.status);
    return '<div class="cnc-kpis">' +
      kpi('Today', '<span style="color:' + (d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--ink)') + '">' + run + '</span>', (d.started ? 'started ' + d.started.slice(11, 16) : 'not started') + (d.trigger ? ' · ' + d.trigger : '') + (d.by ? ' · ' + d.by : '')) +
      kpi('PositionBook', ok + '<small> / ' + ins.length + '</small>', ok === ins.length ? ((ins[0] || {}).stale ? 'in place - ' + ins[0].why : 'in place') : 'not uploaded yet') +
      kpi('Sheet4 rows', c.sheet4 != null ? c.sheet4 : '—', c.rows != null ? c.rows + ' PositionBook row(s) read' : 'no calculation yet') +
      kpi('E-mail', (d.sent || []).length ? 'sent' : '—', (d.sent || []).length ? 'CNC_F11 file mailed' : 'not sent yet') + '</div>';
  }
  function steps(d) {
    return '<div class="cnc-lbl">Steps</div><div class="cnc-steps">' + (d.steps || []).map(function (s) {
      var c = s.status === 'ok' || s.status === 'skipped' || s.status === 'done' ? 'ok' : s.status === 'failed' ? 'failed' : s.status === 'running' ? 'running' : '';
      var t = { ok: '✓ done', done: '✓ in place', skipped: '✓ skipped', failed: '✗ failed', running: 'running…', stopped: 'stopped' }[s.status] || '—';
      return '<div class="cnc-step ' + c + '"><div class="n">' + esc(s.label) + '</div><div class="s">' + t + '</div><div class="m" title="' + esc(s.msg) + '">' + esc(s.msg) + '</div></div>';
    }).join('') + '</div>';
  }
  function inputs(d) {
    return '<div class="cnc-lbl">Input file</div><div style="margin-bottom:12px">' + (d.inputs || []).map(function (x) {
      return '<div class="cnc-frow"><span style="width:18px;color:' + (x.ok ? 'var(--green)' : 'var(--red)') + '">' + (x.ok ? '✓' : '✗') + '</span><span style="width:170px;color:var(--muted)">' + esc(x.label) + '</span><span class="n">' + (x.found ? esc(x.name) : '— not there (' + esc(x.name) + ')') + '</span>' +
        '<span style="color:var(--dim);font-size:11px">' + (x.found ? kb(x.size) + ' · ' + esc(x.time) + (x.why ? ' · ' + esc(x.why) : '') : esc(x.why || '')) + '</span></div>';
    }).join('') + '</div>';
  }
  function logBox(d) {
    var lines = d.log || [];
    return '<div class="cnc-lbl">Live log</div><div class="cnc-logbox" id="cncLogBox">' + (lines.length ? lines.slice(-30).map(esc).join('\n') : 'No activity since the server started. The full log of every run is under 🗂️ Logs.') + '</div>';
  }
  function paintDyn(force) {
    var d = S.data, el = $('cncDyn');
    if (!el || !d || !d.steps) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('cncLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    el.innerHTML = banner(d) + kpis(d) + steps(d) + logBox(d) + inputs(d) +
      '<div style="font-size:11px;color:var(--dim);line-height:1.6">Folder: <span style="font-family:var(--mono)">' + esc(d.folder) + '</span> · after a successful run the csv / xlsx files move to <span style="font-family:var(--mono)">BACKUP AUTOMATION / dd-mm-yyyy</span>. ' +
      'If the e-mail does not go, the run FAILS and the files stay where they are.</div>';
    var nb = $('cncLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }
  function paintTile(d) {
    var chip = $('cncChip'), mini = $('cncMini');
    if (!chip || !d || !d.steps) return;
    chip.textContent = d.stopping ? 'stopping' : d.running ? 'running' : d.status === 'failed' ? 'failed' : d.status === 'done' ? 'done' : 'idle';
    chip.style.color = d.running ? 'var(--blue)' : d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--dim)';
    var ok = (d.inputs || []).filter(function (x) { return x.ok; }).length;
    mini.textContent = d.stopping ? 'Stopping…' : d.running ? 'Running…' : d.status === 'done' ? 'Done · ' + ((d.counts || {}).sheet4 || 0) + ' row(s) in Sheet4' : d.status === 'failed' ? 'Failed - open for details' : (ok ? 'PositionBook is in - press Run' : 'Upload the PositionBook');
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
      if (j && j.ok) okBox('Run started', 'CNC F11 is running - watch the steps and the live log in the window.');
      else okBox('Could not start', (j && (j.error || j.msg || j.detail)) || 'The run did not start.', 'err');
      reload();
    });
  }
  window.cncRun = function () { startRun(''); };
  window.cncStop = function () {
    confirmBox('Stop CNC F11?', 'The run that is going now will be stopped. Files are kept where they are.', 'Stop', function () {
      post(BASE + '/stop').then(function (j) {
        if (j && j.stopped) okBox('Stop requested', 'The run ends in a few seconds.'); else okBox('Nothing to stop', 'No run is going right now.', 'err');
        reload();
      });
    });
  };

  // ───────────────────────── Settings ─────────────────────────
  window.cncSettings = function () {
    ask('Settings', function (pw) {
      get(BASE + '/settings').then(function (s) {
        S.pw = pw;
        modal('⚙ ' + TITLE + ' - settings',
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:14px"><input type="checkbox" id="cncMail" ' + (s.send_mail !== false ? 'checked' : '') + '> Send the e-mail at the run</label>' +
          '<div class="cnc-lbl">WhatsApp FAILURE-alert numbers (blank = the common alert numbers of Credentials > WhatsApp)</div><input type="text" id="cncAlert" class="cnc-in" style="width:100%;margin-bottom:6px" placeholder="9876543210, 9123456789" value="' + esc(s.alert_numbers || '') + '">' +
          '<div style="font-size:11.5px;color:var(--dim);line-height:1.55;margin-bottom:14px">When a run of this card fails, these numbers get the red FAILURE ALERT picture on WhatsApp. 10-digit numbers, +91 is added; several separated by commas.</div>' +
          '<button class="sa-save" id="cncSetBtn" data-no-busy="1" style="width:100%" onclick="cncSetSave()">Save settings</button><div class="sa-msg" id="cncSetMsg"></div>');
      });
    });
  };
  window.cncSetSave = function () {
    var btn = $('cncSetBtn'), m = $('cncSetMsg'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post(BASE + '/settings', { password: S.pw, send_mail: $('cncMail').checked, alert_numbers: $('cncAlert').value }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = j && j.ok ? '✓ Saved' : 'Save settings'; }
      if (j && j.ok) okBox('Settings saved', 'E-mail is ' + ($('cncMail').checked ? 'ON' : 'OFF') + '; failure-alert numbers: ' + ($('cncAlert').value.trim() || 'common list') + '.'); else okBox('Not saved', (j && j.error) || 'Could not save the settings.', 'err');
      if (m) m.innerHTML = j && j.ok ? '<span style="color:var(--green)">Saved.</span>' : '<span style="color:var(--red)">' + esc((j && j.error) || 'Failed') + '</span>';
      reload();
    });
  };

  // ───────────────────────── Backup files ─────────────────────────
  window.cncBackup = function () {
    S.bk = { from: iso(0), to: iso(0) };
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">After every successful run the day\'s files are kept in <span style="font-family:var(--mono)">BACKUP AUTOMATION / dd-mm-yyyy</span>. Pick one day or a range.</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="cnc-lbl">From</div><input type="date" id="cncBkFrom" class="cnc-in" style="width:100%" value="' + S.bk.from + '" onchange="cncBkLoad()"></div>' +
      '<div><div class="cnc-lbl">To</div><input type="date" id="cncBkTo" class="cnc-in" style="width:100%" value="' + S.bk.to + '" onchange="cncBkLoad()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="cnc-chip" onclick="cncBkQuick(0)">Today</button><button class="cnc-chip" onclick="cncBkQuick(1)">Yesterday</button><button class="cnc-chip" onclick="cncBkQuick(7)">Last 7 days</button><button class="cnc-chip" onclick="cncBkQuick(30)">Last 30 days</button></div>' +
      '<div id="cncBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="cncBkGo" data-no-busy="1" style="width:100%" onclick="cncBkDownload()">⬇ Download zip</button>');
    widen(true);
    cncBkLoad();
  };
  window.cncBkQuick = function (n) { $('cncBkTo').value = n === 1 ? iso(1) : iso(0); $('cncBkFrom').value = iso(n); cncBkLoad(); };
  window.cncBkLoad = function () {
    var a = $('cncBkFrom').value, b = $('cncBkTo').value || a; if (b < a) { var x = a; a = b; b = x; }
    S.bk.from = a; S.bk.to = b;
    get(BASE + '/range?from_date=' + encodeURIComponent(a) + '&to_date=' + encodeURIComponent(b)).then(function (j) {
      var l = $('cncBkList'); if (!l) return;
      var byDay = {}; (j.files || []).forEach(function (f) { (byDay[f.date] = byDay[f.date] || []).push(f); });
      var days = Object.keys(byDay), tot = (j.files || []).length;
      l.innerHTML = tot ? days.map(function (day) {
        return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + (days.length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day) + ' <span style="font-weight:400;color:var(--dim)">· ' + byDay[day].length + ' files</span></summary>' +
          byDay[day].map(function (f) { return '<div class="cnc-frow"><span class="n">' + esc(f.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span><a href="' + API + BASE + '/file?date=' + encodeURIComponent(day) + '&name=' + encodeURIComponent(f.name) + '" download>⬇</a></div>'; }).join('') + '</details>';
      }).join('') : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No backed-up files for that ' + (a === b ? 'day' : 'range') + '.</div>';
      var go = $('cncBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; }
    });
  };
  window.cncBkDownload = function () { toast('Preparing the zip - the download starts in a moment.'); window.location.href = API + BASE + '/zip-range?from_date=' + encodeURIComponent(S.bk.from) + '&to_date=' + encodeURIComponent(S.bk.to); };

  // ───────────────────────── Upload PositionBook ─────────────────────────
  window.cncUpload = function () {
    var old = $('cncUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.id = 'cncUpInput'; inp.accept = '.csv'; inp.style.display = 'none';
    inp.onchange = function () { var f = inp.files && inp.files[0]; inp.remove(); if (f) cncUpSend(f); };
    document.body.appendChild(inp); inp.click();
  };
  function cncUpSend(file) {
    toast('Uploading ' + file.name + '…', 'warn');
    var fd = new FormData(); fd.append('files', file); fd.append('modified', String(file.lastModified || ''));      // the file's own saved time: the server checks it when the name has no date
    fetch(API + BASE + '/upload', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.error || j.detail) { okBox('Upload failed', String(j.error || j.detail), 'err'); return; }
      if ((j.ok || []).length) okBox('PositionBook uploaded', 'Saved as ' + j.ok[0] + ' - press Run to make the CNC F11 file.');
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
