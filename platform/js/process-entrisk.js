/* ENTERPRISE RISK REPORT - the LAST small tile of the Process Center. "Open" shows the window: report type switch (REDFLAG / SIMPLE),
   single buttons (mailbox files, MTF CONCENTRATION file), Screener (any company, on demand, e-mailed to rms_del), Credentials,
   Email To / CC, Upload files, Backup files, Logs, Run, Auto on/off, Stop, Settings (times), KPI cards, step tiles, input files, live log.
   Timeline (Mon-Fri, editable in Settings): mailbox from 10:03 (until 11:00), run at 10:30.
   Backend: /entrisk/* (api.py, core/enterprise_risk.py) and the shared /px/* windows with pipe=entrisk.
   Injected at serve time (see api.py /legacy); dashboard.html is only touched in its two e-mail-window lists. */
(function () {
  'use strict';
  if (window.__entRiskInjected) return;
  window.__entRiskInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'ENTERPRISE RISK REPORT';
  var PIPE = 'entrisk';
  var BASE = '/entrisk';
  var S = { data: null, sig: '', timer: null, msgTimer: null, bk: null, scrSeen: '' };

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
    var old = $('entToast'); if (old) old.remove();
    var el = document.createElement('div'); el.id = 'entToast';
    var col = kind === 'err' ? 'var(--red,#f87171)' : kind === 'warn' ? 'var(--amber,#fbbf24)' : 'var(--green,#34d399)';
    el.style.cssText = 'position:fixed;top:22px;left:50%;transform:translateX(-50%);z-index:100000;max-width:min(560px,92vw);padding:12px 18px;border-radius:12px;' +
      'background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:13px;line-height:1.45;display:flex;gap:10px;align-items:flex-start';
    el.innerHTML = '<span style="color:' + col + ';font-weight:800">' + (kind === 'err' ? '✕' : kind === 'warn' ? '!' : '✓') + '</span><span>' + esc(text) + '</span>';
    document.body.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, kind === 'err' ? 7000 : 3800);
  }
  function okBox(title, text, kind) {      // a popup with an OK button (stays until OK is pressed)
    var old = $('entOk'); if (old) old.remove();
    var col = kind === 'err' ? 'var(--red,#f87171)' : 'var(--green,#34d399)';
    var bg = document.createElement('div'); bg.id = 'entOk';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100002;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px;color:' + col + '">' + (kind === 'err' ? '✕ ' : '✓ ') + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;justify-content:flex-end"><button class="sa-tool" data-no-busy="1" id="entOkBtn" style="min-width:90px;background:' + col + ';color:#06251a;border-color:transparent">OK</button></div></div>';
    document.body.appendChild(bg);
    $('entOkBtn').onclick = function () { bg.remove(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
    $('entOkBtn').focus();
  }
  function confirmBox(title, text, okLabel, cb) {
    var old = $('entConfirm'); if (old) old.remove();
    var bg = document.createElement('div'); bg.id = 'entConfirm';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--line-2,#2a3342);border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px">' + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end"><button class="sa-tool" id="entNo" style="min-width:90px">Cancel</button><button class="sa-tool" id="entYes" style="min-width:90px;background:var(--red,#f87171);color:#2a0a0a;border-color:transparent">' + esc(okLabel || 'OK') + '</button></div></div>';
    document.body.appendChild(bg);
    $('entNo').onclick = function () { bg.remove(); };
    $('entYes').onclick = function () { bg.remove(); cb(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
  }

  var css = document.createElement('style');
  css.textContent =
    '.ent-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.ent-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.ent-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.ent-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.ent-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.ent-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.ent-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}' +
    '@media(max-width:820px){.ent-kpis,.ent-steps{grid-template-columns:repeat(2,1fr)}}' +
    '.ent-step{border:1px solid var(--line);border-radius:11px;padding:9px 11px;background:var(--panel-2);min-width:0}' +
    '.ent-step.ok{border-color:rgba(52,211,153,.4)}.ent-step.failed{border-color:rgba(248,113,113,.55)}.ent-step.running{border-color:var(--amber)}' +
    '.ent-step .n{font-size:12px;font-weight:650}.ent-step .s{font-family:var(--mono);font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);margin-top:2px}' +
    '.ent-step.ok .s{color:var(--green)}.ent-step.failed .s{color:var(--red)}.ent-step.running .s{color:var(--amber)}' +
    '.ent-step .m{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.35;overflow:hidden;max-height:42px}' +
    '.ent-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.ent-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:170px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.ent-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.ent-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.ent-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.ent-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.ent-frow a{color:var(--blue);text-decoration:none}.ent-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.ent-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '@keyframes entSpin{to{transform:rotate(360deg)}}.ent-spin{display:inline-block;width:12px;height:12px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:entSpin .8s linear infinite;vertical-align:-2px;margin-right:6px}';
  document.head.appendChild(css);

  // ───────────────────────── the small tile: always the LAST tile of the console ─────────────────────────
  function grid() {
    var t = $('task-mtforder') || $('task-mtfconc') || $('task-ms15card') || $('task-mcxcomm') || $('task-liveallocation');
    if (t && t.parentNode) return t.parentNode;
    var slot = $('physdelSlot');
    return slot ? slot.parentNode : null;
  }
  function build() {
    if ($('task-entrisk')) return true;
    var g = grid(); if (!g) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-entrisk';
    tile.style.cssText = '--tile-accent:#ef4444;cursor:pointer';
    tile.onclick = function () { entOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">🚩</div>' +
      '<div class="tile-name">ENTERPRISE RISK REPORT <span class="st" id="entChip">idle</span></div>' +
      '<div class="tile-desc">Picks the Enterprise Risk mails (from 10:03), takes the MTF CONCENTRATION file, builds the two reports with screener.in data and e-mails them at 10:30 (Mon-Fri). Screener button: any company on demand.</div>' +
      '<div class="tile-desc" id="entMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#ef4444;color:#2a0707" onclick="event.stopPropagation();entOpen()" data-tip="Open ENTERPRISE RISK REPORT: report type, screener, run, auto switch, credentials, e-mail To / CC, upload and backup files, logs.">Open</button></div>';
    g.appendChild(tile);
    return true;
  }
  function keepLast() {      // the very last tile of the row
    var el = $('task-entrisk'); if (!el || !el.parentNode) return;
    var p = el.parentNode; if (p.lastElementChild !== el) p.appendChild(el);
  }

  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('entDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(940px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__ent) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__ent = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (fn.indexOf('entRun') === 0 ? ' data-no-busy="1"' : '') + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  var RED = ';border-color:rgba(248,113,113,.45);color:var(--red)';
  window.entSub = function (fn) { window._saReturn = entOpen; fn(); };
  window.entCreds = function () { entSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.entEmail = function () { entSub(function () { if (typeof window.pxConfig === 'function') window.pxConfig(PIPE); }); };
  window.entLogs = function () { entSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function entOpen() {
    window._saReturn = null;
    var d = S.data, auto = d ? d.auto : true, v = d ? d.variant : 'redflag';
    modal(TITLE,
      '<div style="display:flex;gap:10px;align-items:center;margin:0 0 10px"><span class="ent-lbl" style="margin:0">Report type</span>' +
        '<button class="sa-tool" id="entVRed" data-no-busy="1" onclick="entVariant(\'redflag\')" data-tip="REDFLAG files: the Enterprise Risk Red Flag mails (FINAL_REDFLAG.py / EMAIL_POP3_REDFLAG.py)." style="padding:5px 14px;' + (v === 'redflag' ? 'background:var(--blue);color:var(--blue-d);border-color:transparent' : '') + '">🚩 REDFLAG files</button>' +
        '<button class="sa-tool" id="entVSim" data-no-busy="1" onclick="entVariant(\'simple\')" data-tip="SIMPLE files: the normal Enterprise Risk mails (FINAL.py / EMAIL_POP3.py)." style="padding:5px 14px;' + (v === 'simple' ? 'background:var(--blue);color:var(--blue-d);border-color:transparent' : '') + '">📄 SIMPLE files</button></div>' +
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 8px">' +
        tb('⬇ Mailbox files', "entFetch('mail')", 'Looks in the mailbox for today\'s Enterprise Risk mails (With / Without Margin) and the Concentration csv mail and saves their attachments. A button always checks again.') +
        tb('⬇ MTF CONCENTRATION file', "entFetch('conc')", 'Takes today\'s FULLFILE_CONCENTRATION from the MTF CONCENTRATION card (copied - the original stays).') +
        tb('🔍 Screener (any stock)', 'entSub(entScreener)', 'Type a company symbol - its screener.in data is made as a file and e-mailed to rms_del@mastertrust.co.in. Only when you ask.') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 8px">' +
        tb('🔑 Credentials', 'entCreds()', 'Mail sender, mailbox, screener.in login, file passwords.') +
        tb('✉️ Email To / CC', 'entEmail()', 'Who gets the daily report and the screener file.') +
        tb('⬆️ Upload files', 'entSub(entUpload)', 'Put a file in by hand if a mail or MTF CONCENTRATION is late.') +
        tb('📦 Backup files', 'entSub(entBackup)', 'Pick a date or a range and download the backed-up files as one zip.') +
        tb('🗂️ Logs', 'entLogs()', 'The full log of every run.') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 14px">' +
        tb('▶ Run', 'entRun()', 'Runs the whole job now (picks anything still missing first). If today\'s run already succeeded it asks for the password.', ';background:var(--blue);color:var(--blue-d);border-color:transparent') +
        tb(auto ? '⏸ Auto: ON' : '▶ Auto: OFF', 'entAuto()', 'ON: the timer picks the mails and runs by itself Monday to Friday. OFF: only the buttons work. Asks for the password.', auto ? '' : RED) +
        tb('⚙ Settings', 'entSub(entSettings)', 'Report type, mail start / end time, run time, symbols per report, protection, e-mail on/off, alert numbers.') +
        tb('⛔ Stop', 'entStop()', 'Stops the run, download or screener request that is going right now.', RED) +
      '</div><div id="entMsg"></div><div id="entDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.entOpen = entOpen;

  function say(txt, kind) {
    if (txt) toast(txt, kind);
    var el = $('entMsg');
    if (el) el.innerHTML = txt ? '<div class="ent-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('entMsg'); if (e) e.innerHTML = ''; }, 8000);
  }

  // ───────────────────────── render ─────────────────────────
  var ST = { idle: 'Not run yet today', running: 'Running…', done: '✓ Done', failed: '✗ Failed', stopped: 'Stopped' };
  function kpi(k, v, f) { return '<div class="ent-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div></div>'; }
  function banner(d) {
    var s = d.settings || {}, tag = (d.variant === 'redflag' ? 'REDFLAG' : 'SIMPLE') + ' files';
    if (d.status === 'failed') return '<div class="ent-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>Today\'s run FAILED.</b> ' + esc(d.error || '') + ' <span style="color:var(--dim)">· ' + (d.alert_sent ? 'WhatsApp alert sent' : 'alert not sent' + (d.alert_reason ? ' (' + esc(d.alert_reason) + ')' : '')) + ' · press <b>Run</b> to try again (no password needed).</span></div>';
    if (d.status === 'done') return '<div class="ent-ban" style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:var(--green)"><b>✓ Done today</b> (' + tag + ') - report e-mailed to ' + esc((d.sent || []).length ? 'the daily list' : 'nobody (e-mail off)') + ' · Run again asks for the password.</div>';
    if (!d.auto) return '<div class="ent-ban" style="background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.45);color:var(--amber)"><b>Auto is OFF</b> - the timer does nothing. Use the buttons.</div>';
    return '<div class="ent-ban" style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.35);color:var(--blue)">● Auto is ON (' + tag + ') - Monday to Friday: mailbox from <b>' + esc(s.mail_from) + '</b> to <b>' + esc(s.mail_until) + '</b>, run at <b>' + esc(s.run_at) + '</b>.' + (s.send_mail ? '' : ' <b style="color:var(--amber)">E-mail is switched OFF.</b>') + (s.protect ? '' : ' <b style="color:var(--amber)">File protection is OFF.</b>') + '</div>';
  }
  function scrLine(d) {
    var x = d.screener || {};
    if (!x.status) return '';
    var col = x.status === 'done' ? 'var(--green)' : x.status === 'failed' ? 'var(--red)' : 'var(--amber)';
    return '<div class="ent-msg" style="color:' + col + '">Screener <b>' + esc(x.symbol) + '</b>: ' + esc(x.status) + ' - ' + esc(x.msg || '') + ' <span style="color:var(--dim)">' + esc(String(x.at || '').slice(11, 16)) + '</span></div>';
  }
  function kpis(d) {
    var ins = d.inputs || [], ok = ins.filter(function (x) { return x.ok; }).length, c = d.counts || {};
    var run = d.stopping ? 'Stopping…' : d.running ? 'Running' : (ST[d.status] || d.status);
    var nW = (c.without || {}).symbols, nM = (c.with || {}).symbols;
    return '<div class="ent-kpis">' +
      kpi('Today', '<span style="color:' + (d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--ink)') + '">' + run + '</span>', (d.started ? 'started ' + d.started.slice(11, 16) : 'not started') + (d.trigger ? ' · ' + d.trigger : '') + (d.by ? ' · ' + d.by : '')) +
      kpi('Report type', d.variant === 'redflag' ? '🚩 REDFLAG' : '📄 SIMPLE', 'switch it at the top') +
      kpi('Input files', ok + '<small> / ' + ins.length + '</small>', ok === ins.length ? 'everything is in place' : 'missing: ' + ins.filter(function (x) { return !x.ok; }).map(function (x) { return x.label; }).join(', ')) +
      kpi('Symbols', nW != null ? (nW + ' + ' + nM) : '—', nW != null ? 'Without + With Margin' : 'no calculation yet') + '</div>';
  }
  function steps(d) {
    return '<div class="ent-lbl">Steps</div><div class="ent-steps">' + (d.steps || []).map(function (s) {
      var c = s.status === 'ok' || s.status === 'skipped' || s.status === 'done' ? 'ok' : s.status === 'failed' ? 'failed' : s.status === 'running' ? 'running' : '';
      var t = { ok: '✓ done', done: '✓ in place', skipped: '✓ already there', failed: '✗ failed', running: 'running…', stopped: 'stopped' }[s.status] || '—';
      return '<div class="ent-step ' + c + '"><div class="n">' + esc(s.label) + '</div><div class="s">' + t + '</div><div class="m" title="' + esc(s.msg) + '">' + esc(s.msg) + '</div></div>';
    }).join('') + '</div>';
  }
  function inputs(d) {
    return '<div class="ent-lbl">Input files</div><div style="margin-bottom:12px">' + (d.inputs || []).map(function (x) {
      return '<div class="ent-frow"><span style="width:18px;color:' + (x.ok ? 'var(--green)' : 'var(--red)') + '">' + (x.ok ? '✓' : '✗') + '</span><span style="width:200px;color:var(--muted)">' + esc(x.label) + '</span><span class="n">' + (x.found ? esc(x.name) : '— not there (' + esc(x.name) + ')') + '</span>' +
        '<span style="color:var(--dim);font-size:11px">' + (x.found ? kb(x.size) + ' · ' + esc(x.time) + (x.ok ? '' : ' · ' + esc(x.why)) : '') + '</span></div>';
    }).join('') + '</div>';
  }
  function logBox(d) {
    var lines = d.log || [];
    return '<div class="ent-lbl">Live log</div><div class="ent-logbox" id="entLogBox">' + (lines.length ? lines.slice(-30).map(esc).join('\n') : 'No activity since the server started. The full log of every run is under 🗂️ Logs.') + '</div>';
  }
  function paintDyn(force) {
    var d = S.data, el = $('entDyn');
    if (!el || !d || !d.steps) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('entLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    el.innerHTML = banner(d) + scrLine(d) + kpis(d) + steps(d) + logBox(d) + inputs(d) +
      '<div style="font-size:11px;color:var(--dim);line-height:1.6">Folder: <span style="font-family:var(--mono)">' + esc(d.folder) + '</span> · backups in <span style="font-family:var(--mono)">BACKUP FILES AUTOMATED / dd-mm-yyyy</span> (a finished month is filed as OCT 2026 / dd-mm-yyyy). ' +
      'RISK_REPORT and MTFPOSITION are read from COMMON FILES (copied for the run - the originals stay). A zone with no order lines gets no mail.</div>';
    var nb = $('entLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }
  function paintTile(d) {
    var chip = $('entChip'), mini = $('entMini');
    if (!chip || !d || !d.steps) return;
    chip.textContent = d.stopping ? 'stopping' : d.running ? 'running' : d.status === 'failed' ? 'failed' : d.status === 'done' ? 'done' : !d.auto ? 'auto off' : 'idle';
    chip.style.color = d.running ? 'var(--blue)' : d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : !d.auto ? 'var(--amber)' : 'var(--dim)';
    var ok = (d.inputs || []).filter(function (x) { return x.ok; }).length, s = d.settings || {};
    mini.textContent = d.stopping ? 'Stopping…' : d.running ? 'Running…' : d.status === 'done' ? 'Done · ' + (d.variant === 'redflag' ? 'REDFLAG' : 'SIMPLE') + ' · ' + (d.sent || []).length + ' mail(s)' : d.status === 'failed' ? 'Failed - open for details' : ok + ' / ' + (d.inputs || []).length + ' files in · ' + (d.variant === 'redflag' ? 'REDFLAG' : 'SIMPLE') + (d.auto ? ' · run at ' + s.run_at : ' · auto OFF');
  }
  function load() {
    var next = 8000;
    return get(BASE + '/status').then(function (d) {
      S.data = d;
      if (d && d.steps) { paintTile(d); if (modalOpen()) paintDyn(); if (d.running || d.fetching || d.stopping) next = 2000; }
    }).catch(function () { next = 15000; }).then(function () { schedule(next); });
  }
  function schedule(ms) {
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (document.hidden) { schedule(ms); } else { load(); } }, ms);
  }
  function reload() { S.sig = ''; clearTimeout(S.timer); setTimeout(load, 700); }

  // ───────────────────────── actions ─────────────────────────
  window.entFetch = function (what) {
    post(BASE + '/fetch', { what: what }).then(function (j) {
      if (j && j.ok) say('Download started - watch the live log.'); else say((j && (j.error || j.detail)) || 'Could not start.', 'err');
      reload();
    });
  };
  function startRun(pw) {
    post(BASE + '/run', pw ? { password: pw } : {}).then(function (j) {
      if (j && j.need_password) { ask('Run again (today\'s run already succeeded)', function (p) { startRun(p); }); return; }
      if (j && j.ok) say('Run started - watch the steps and the live log.'); else say((j && (j.error || j.msg || j.detail)) || 'Could not start.', 'err');
      reload();
    });
  }
  window.entRun = function () { startRun(''); };
  window.entStop = function () {
    confirmBox('Stop ENTERPRISE RISK REPORT?', 'The run or download that is going now will be stopped. Files already downloaded are kept.', 'Stop', function () {
      post(BASE + '/stop').then(function (j) { say(j && j.stopped ? 'Stop requested - it ends in a few seconds.' : 'Nothing is running.', j && j.stopped ? 'ok' : 'warn'); reload(); });
    });
  };
  window.entAuto = function () {
    var on = !(S.data && S.data.auto);
    ask(on ? 'Switch auto ON' : 'Switch auto OFF', function (pw) {
      post(BASE + '/settings', { password: pw, auto: on }).then(function (j) {
        entOpen();
        if (j && j.ok) say(on ? 'Auto is ON.' : 'Auto is OFF - only the buttons work.'); else say((j && j.error) || 'Failed', 'err');
        reload();
      });
    });
  };

  // ───────────────────────── Settings ─────────────────────────
  window.entSettings = function () {
    ask('Settings', function (pw) {
      get(BASE + '/settings').then(function (s) {
        S.pw = pw;
        modal('⚙ ' + TITLE + ' - settings',
          '<div style="margin-bottom:12px"><div class="ent-lbl">Report type</div><select id="entVar" class="ent-in" style="width:100%"><option value="redflag"' + (s.variant === 'redflag' ? ' selected' : '') + '>REDFLAG files (Enterprise Risk Red Flag mails)</option><option value="simple"' + (s.variant === 'simple' ? ' selected' : '') + '>SIMPLE files (normal Enterprise Risk mails)</option></select></div>' +
          '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:8px">' +
          '<div><div class="ent-lbl">Mail look-up start time</div><input type="time" id="entT1" class="ent-in" style="width:100%" value="' + esc(s.mail_from) + '"></div>' +
          '<div><div class="ent-lbl">Mail look-up end time</div><input type="time" id="entT2" class="ent-in" style="width:100%" value="' + esc(s.mail_until) + '"></div>' +
          '<div><div class="ent-lbl">Run time (Mon-Fri)</div><input type="time" id="entT3" class="ent-in" style="width:100%" value="' + esc(s.run_at) + '"></div></div>' +
          '<div style="font-size:11.5px;color:var(--dim);line-height:1.55;margin-bottom:12px">The mailbox is looked at every 5 minutes between the start and end time; only mails of today that arrived at or after the start time are taken (the Concentration csv mail from 07:30). If the files are not all there at the run time, the run waits for them until the end time and then sends one failure alert.</div>' +
          '<div style="margin-bottom:12px"><div class="ent-lbl">Symbols per report (top N by NET_EXPOSURE + top N by days to liquidate)</div><input type="number" min="1" max="200" id="entTop" class="ent-in" style="width:100%" value="' + esc(s.top_n) + '"></div>' +
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:8px"><input type="checkbox" id="entProt" ' + (s.protect ? 'checked' : '') + '> Password-protect the two report files (password in Credentials)</label>' +
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:12px"><input type="checkbox" id="entMail" ' + (s.send_mail ? 'checked' : '') + '> Send the e-mail automatically at the run</label>' +
          '<div class="ent-lbl">WhatsApp failure-alert numbers (blank = the Margin Shortage / common alert numbers)</div><input type="text" id="entAlert" class="ent-in" style="width:100%;margin-bottom:14px" placeholder="9876543210, 9123456789" value="' + esc(s.alert_numbers || '') + '">' +
          '<button class="sa-save" id="entSetBtn" data-no-busy="1" style="width:100%" onclick="entSetSave()">Save settings</button><div class="sa-msg" id="entSetMsg"></div>');
      });
    });
  };
  window.entSetSave = function () {
    var btn = $('entSetBtn'), m = $('entSetMsg'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post(BASE + '/settings', { password: S.pw, variant: $('entVar').value, mail_from: $('entT1').value, mail_until: $('entT2').value, run_at: $('entT3').value, top_n: parseInt($('entTop').value, 10), protect: $('entProt').checked, send_mail: $('entMail').checked, alert_numbers: $('entAlert').value }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = j && j.ok ? '✓ Saved' : 'Save settings'; }
      if (j && j.ok) okBox('Settings saved', 'Report type ' + ($('entVar').value === 'redflag' ? 'REDFLAG' : 'SIMPLE') + ' · mailbox ' + $('entT1').value + '-' + $('entT2').value + ' · run at ' + $('entT3').value + '.'); else okBox('Not saved', (j && j.error) || 'Could not save the settings.', 'err');
      if (m) m.innerHTML = j && j.ok ? '<span style="color:var(--green)">Saved.</span>' : '<span style="color:var(--red)">' + esc((j && j.error) || 'Failed') + '</span>';
      reload();
    });
  };

  // ───────────────────────── report type switch + Screener ─────────────────────────
  window.entVariant = function (v) {
    if (S.data && S.data.variant === v) { okBox('Already selected', (v === 'redflag' ? 'REDFLAG' : 'SIMPLE') + ' files are already in use.'); return; }
    ask('Switch report type to ' + (v === 'redflag' ? 'REDFLAG' : 'SIMPLE') + ' files', function (pw) {
      post(BASE + '/settings', { password: pw, variant: v }).then(function (j) {
        if (j && j.ok) { okBox('Report type changed', 'From now on the ' + (v === 'redflag' ? 'REDFLAG' : 'SIMPLE') + ' files are used (mails, file names, sheets and the e-mail wording).'); S.data = null; reload(); setTimeout(entOpen, 900); }
        else okBox('Not changed', (j && j.error) || 'Could not change it.', 'err');
      });
    });
  };
  window.entScreener = function () {
    modal('🔍 ' + TITLE + ' - screener',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.6;margin-bottom:12px">Type the symbol of any company (e.g. <b>TCS</b>, <b>M&amp;M</b>, <b>BAJAJ-AUTO</b>). Its screener.in data is made as <b>&lt;SYMBOL&gt;_SCREENER.xlsx</b> and e-mailed to the Screener address (Email To / CC, <b>rms_del@mastertrust.co.in</b> by default). It runs only when you press the button.</div>' +
      '<div style="display:flex;gap:10px;margin-bottom:12px"><input type="text" id="entSym" class="ent-in" style="flex:1;text-transform:uppercase" maxlength="25" placeholder="Company symbol" onkeydown="if(event.key===\'Enter\')entScrGo()">' +
      '<button class="sa-save" id="entScrBtn" data-no-busy="1" style="min-width:170px" onclick="entScrGo()">Make &amp; e-mail</button></div><div id="entScrRes"></div>');
    var el = $('entSym'); if (el) el.focus();
  };
  window.entScrGo = function () {
    var sym = ($('entSym').value || '').trim().toUpperCase();
    if (!sym) { okBox('Symbol needed', 'Type a company symbol first.', 'err'); return; }
    S.scrSeen = (S.data && S.data.screener && S.data.screener.at) || '';
    post(BASE + '/screener', { symbol: sym }).then(function (j) {
      if (j && j.ok) { toast('Screener started for ' + sym + ' - the result comes as a popup.'); scrPoll(sym, 0); }
      else okBox('Could not start', (j && (j.error || j.detail)) || 'Failed.', 'err');
    });
  };
  function scrPoll(sym, n) {
    get(BASE + '/status').then(function (d) {
      S.data = d; paintTile(d);
      var x = d.screener || {};
      if (x.symbol === sym && (x.status === 'done' || x.status === 'failed' || x.status === 'stopped') && x.at !== S.scrSeen) {
        if (x.status === 'done') okBox('Screener done - ' + sym, x.msg || 'The file was e-mailed.');
        else okBox(x.status === 'stopped' ? 'Stopped' : 'Screener failed - ' + sym, x.msg || '', 'err');
        return;
      }
      if (n < 120) setTimeout(function () { scrPoll(sym, n + 1); }, 2000);
    }).catch(function () { if (n < 120) setTimeout(function () { scrPoll(sym, n + 1); }, 4000); });
  }

  // ───────────────────────── Backup files ─────────────────────────
  window.entBackup = function () {
    S.bk = { from: iso(0), to: iso(0) };
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">After every successful run the day\'s input and output files are kept in <span style="font-family:var(--mono)">BACKUPFILES AUTOMATED / ddmmyyyy</span> (months are filed under e.g. NOV 2026). Pick one day or a range.</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="ent-lbl">From</div><input type="date" id="entBkFrom" class="ent-in" style="width:100%" value="' + S.bk.from + '" onchange="entBkLoad()"></div>' +
      '<div><div class="ent-lbl">To</div><input type="date" id="entBkTo" class="ent-in" style="width:100%" value="' + S.bk.to + '" onchange="entBkLoad()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="ent-chip" onclick="entBkQuick(0)">Today</button><button class="ent-chip" onclick="entBkQuick(1)">Yesterday</button><button class="ent-chip" onclick="entBkQuick(7)">Last 7 days</button><button class="ent-chip" onclick="entBkQuick(30)">Last 30 days</button></div>' +
      '<div id="entBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="entBkGo" data-no-busy="1" style="width:100%" onclick="entBkDownload()">⬇ Download zip</button>');
    widen(true);
    entBkLoad();
  };
  window.entBkQuick = function (n) { $('entBkTo').value = n === 1 ? iso(1) : iso(0); $('entBkFrom').value = iso(n); entBkLoad(); };
  window.entBkLoad = function () {
    var a = $('entBkFrom').value, b = $('entBkTo').value || a; if (b < a) { var x = a; a = b; b = x; }
    S.bk.from = a; S.bk.to = b;
    get(BASE + '/range?from_date=' + encodeURIComponent(a) + '&to_date=' + encodeURIComponent(b)).then(function (j) {
      var l = $('entBkList'); if (!l) return;
      var byDay = {}; (j.files || []).forEach(function (f) { (byDay[f.date] = byDay[f.date] || []).push(f); });
      var days = Object.keys(byDay), tot = (j.files || []).length;
      l.innerHTML = tot ? days.map(function (day) {
        return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + (days.length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day) + ' <span style="font-weight:400;color:var(--dim)">· ' + byDay[day].length + ' files</span></summary>' +
          byDay[day].map(function (f) { return '<div class="ent-frow"><span class="n">' + esc(f.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span><a href="' + API + BASE + '/file?date=' + encodeURIComponent(day) + '&name=' + encodeURIComponent(f.name) + '" download>⬇</a></div>'; }).join('') + '</details>';
      }).join('') : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No backed-up files for that ' + (a === b ? 'day' : 'range') + '.</div>';
      var go = $('entBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; }
    });
  };
  window.entBkDownload = function () { toast('Preparing the zip - the download starts in a moment.'); window.location.href = API + BASE + '/zip-range?from_date=' + encodeURIComponent(S.bk.from) + '&to_date=' + encodeURIComponent(S.bk.to); };

  // ───────────────────────── Upload files ─────────────────────────
  window.entUpload = function () {
    modal('⬆️ ' + TITLE + ' - upload files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.6;margin-bottom:12px">A mail or MTF CONCENTRATION late? Put the file here under its own name - the next run uses it instead of fetching it.</div>' +
      '<div style="padding:10px 14px;border:1px dashed var(--line-2);border-radius:10px;font-size:11.5px;line-height:1.9;margin-bottom:12px"><b style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase">Files used</b><br><span style="font-family:var(--mono);color:var(--teal)">EnterpriseRiskReport_Without_Margin[_REDFLAG]_&lt;yyyy-mm-dd&gt;.xlsx<br>EnterpriseRiskReport_With_Margin[_REDFLAG]_&lt;yyyy-mm-dd&gt;.xlsx<br>Concentration_abv20percentage.csv · FULLFILE_CONCENTRATION_&lt;ddmmyyyy&gt;.xlsx</span></div>' +
      '<button class="sa-save" data-no-busy="1" style="width:100%" onclick="entUpPick()">📄 Choose files…</button><div id="entUpRes" style="margin-top:12px"></div>');
  };
  window.entUpPick = function () {
    var old = $('entUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.multiple = true; inp.id = 'entUpInput'; inp.style.display = 'none';
    inp.onchange = function () { var fl = [].slice.call(inp.files); inp.remove(); if (fl.length) entUpSend(fl); };
    document.body.appendChild(inp); inp.click();
  };
  function entUpSend(files) {
    var res = $('entUpRes'); if (res) res.innerHTML = '<div style="color:var(--muted);font-size:12.5px"><span class="ent-spin"></span>Uploading ' + files.length + ' file(s)…</div>';
    var fd = new FormData(); files.forEach(function (f) { fd.append('files', f); });
    fetch(API + BASE + '/upload', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (j) {
      if (!res) return;
      if (j.error || j.detail) { res.innerHTML = '<div style="color:var(--red)">' + esc(j.error || j.detail) + '</div>'; return; }
      var h = '<div class="ent-msg" style="color:' + ((j.rejected || []).length ? 'var(--amber)' : 'var(--green)') + '">' + (j.ok || []).length + ' file(s) saved' + ((j.rejected || []).length ? ' · ' + j.rejected.length + ' not accepted' : '') + '</div>';
      (j.ok || []).forEach(function (m) { h += '<div class="ent-frow"><span style="color:var(--green)">✓</span><span class="n">' + esc(m) + '</span></div>'; });
      (j.rejected || []).forEach(function (m) { h += '<div class="ent-frow"><span style="color:var(--red)">✕</span><span class="n">' + esc(m) + '</span></div>'; });
      toast((j.ok || []).length ? (j.ok.length + ' file(s) saved.') : 'No file was saved.', (j.ok || []).length ? (((j.rejected || []).length) ? 'warn' : 'ok') : 'err');
      res.innerHTML = h; reload();
    }).catch(function (e) { if (res) res.innerHTML = '<div style="color:var(--red)">Upload failed: ' + esc(e.message || e) + '</div>'; });
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
