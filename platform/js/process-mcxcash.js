/* MCX CASH MAINTENANCE - a small card at the end of the Process Center's "Processing & Reports" row (same look and the same
   buttons as LIVE ALLOCATION CAPTURE). "Open" shows the window: Timings, Override, Credentials, Email To / CC, Upload files,
   Backup files, Logs, Auto on/off, Stop - plus the 4 KPI cards, the live log and one tile per TIME (09:30 … 16:30).
   The reports are produced and e-mailed BY THEMSELVES at each time (timer inside the server: core/mcx_cash_maintenance.py);
   some times take their files from the MARGIN SHORTAGE (15 minute) run, the others download them.
   Backend: /mcx-cash/* (api.py) and the shared /px/* windows (Credentials, Email, Logs) with pipe=mcxcash.
   Injected at serve time (see api.py /legacy); dashboard.html is not modified. */
(function () {
  'use strict';
  if (window.__mcxCashInjected) return;
  window.__mcxCashInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'MCX CASH MAINTENANCE';
  var PIPE = 'mcxcash';
  var S = { data: null, sig: '', timer: null, pw: '', bk: null, msgTimer: null, lastRunAt: 0, logTimer: null };

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
  function dmyToIso(s) { var p = String(s || '').split('-'); return p.length === 3 ? p[2] + '-' + p[1] + '-' + p[0] : ''; }
  function isoToday() { var d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function isoAgo(n) { var d = new Date(Date.now() - n * 864e5); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function tlabel(slot) { return String(slot).replace('-', ':'); }

  var css = document.createElement('style');
  css.textContent =
    '.mc-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.mc-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.mc-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.mc-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.mc-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.mc-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.mc-bar{height:5px;border-radius:9px;background:var(--line);margin-top:7px;overflow:hidden}.mc-bar i{display:block;height:100%;background:var(--green);transition:width .6s}' +
    '.mc-runs{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}' +
    '@media(max-width:820px){.mc-kpis,.mc-runs{grid-template-columns:repeat(2,1fr)}}' +
    '.mc-run{background:var(--panel-2);border:1px solid var(--line);border-radius:13px;padding:12px 11px 11px;text-align:center;display:flex;flex-direction:column;gap:6px;min-width:0}' +
    '.mc-run.done{border-color:rgba(52,211,153,.4)}.mc-run.fail{border-color:rgba(248,113,113,.5)}' +
    '.mc-run.running{border-color:var(--amber);box-shadow:0 0 0 1px rgba(251,191,36,.25)}' +
    '.mc-run.skipped,.mc-run.later{opacity:.75}' +
    '.mc-id{font-family:var(--display);font-size:19px;font-weight:750;font-variant-numeric:tabular-nums}' +
    '.mc-src{font-family:var(--mono);font-size:10px;color:var(--muted)}' +
    '.mc-st{font-family:var(--mono);font-size:9.5px;letter-spacing:.8px;text-transform:uppercase;color:var(--dim)}' +
    '.mc-run.done .mc-st{color:var(--green)}.mc-run.fail .mc-st{color:var(--red)}.mc-run.running .mc-st{color:var(--amber)}' +
    '.mc-dots{display:flex;gap:5px;justify-content:center}.mc-dots i{width:9px;height:9px;border-radius:50%;background:var(--line-2);display:block}' +
    '.mc-dots i.on{background:var(--green)}.mc-dots i.bad{background:var(--red)}.mc-dots i.sq{border-radius:2px}' +
    '.mc-note{font-size:10.5px;color:var(--muted);min-height:26px;line-height:1.35;overflow:hidden}' +
    '.mc-btn{width:100%;font-family:var(--sans);font-weight:650;font-size:12px;border:0;border-radius:9px;padding:8px;cursor:pointer;background:var(--blue);color:var(--blue-d)}' +
    '.mc-btn:disabled{background:var(--panel-3);color:var(--dim);cursor:not-allowed}' +
    '@keyframes mcSpin{to{transform:rotate(360deg)}}.mc-spin{display:inline-block;width:14px;height:14px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:mcSpin .8s linear infinite;vertical-align:-2px;margin-right:8px}' +
    '.mc-btn:disabled.amber{background:var(--amber);color:var(--amber-d);cursor:default}' +
    '.mc-btn.red{background:var(--red);color:#fff}.mc-btn.amber{background:var(--amber);color:var(--amber-d)}' +
    '.mc-link{background:none;border:0;color:var(--muted);font-size:11px;cursor:pointer;text-decoration:underline;padding:0}' +
    '.mc-link:disabled{opacity:.4;cursor:not-allowed}' +
    '.mc-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.mc-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:150px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.mc-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.mc-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.mc-frow a{color:var(--blue);text-decoration:none}.mc-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.mc-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '.mc-chip.on{border-color:rgba(59,130,246,.6);background:rgba(59,130,246,.14);color:var(--blue)}' +
    '.mc-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.mc-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.mc-ovr{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:9px 0;border-bottom:1px solid var(--line);font-size:12.5px}' +
    '.mc-trow{display:grid;grid-template-columns:110px 1fr 96px;gap:8px;align-items:end;margin-bottom:8px}';
  document.head.appendChild(css);

  // ───────────────────────── the small card (after LIVE ALLOCATION CAPTURE, at the end of "Processing & Reports") ─────────────────────────
  function build() {
    if ($('task-mcxcash')) return true;
    var slot = $('physdelSlot');
    var grid = slot ? slot.parentNode : null;
    if (!grid) {
      var secs = [].slice.call(document.querySelectorAll('.sec'));
      var sec = secs.filter(function (s) { return /processing/i.test(s.textContent); })[0];
      grid = sec ? sec.nextElementSibling : null;
    }
    if (!grid) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-mcxcash';
    tile.style.cssText = '--tile-accent:var(--violet);cursor:pointer';
    tile.onclick = function () { mcOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">💰</div>' +
      '<div class="tile-name">MCX CASH MAINTENANCE <span class="st" id="mcChip">idle</span></div>' +
      '<div class="tile-desc">Builds and e-mails the MCX cash maintenance shortfall reports 8 times a day, from the MCX 90%, MCX deposit and CM / FO collateral files. Fully automatic.</div>' +
      '<div class="tile-desc" id="mcMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:var(--violet);color:var(--violet-d,#1a0d33)" onclick="event.stopPropagation();mcOpen()" data-tip="Open MCX CASH MAINTENANCE: the times, timings, override, credentials, e-mail To / CC, backup files, logs.">Open</button></div>';
    var after = $('task-liveallocation');
    if (after && after.parentNode === grid) grid.insertBefore(tile, after.nextSibling); else grid.appendChild(tile);
    return true;
  }
  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('mcDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(940px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__mc) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__mc = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  window.mcSub = function (fn) { window._saReturn = mcOpen; fn(); };
  window.mcCreds = function () { mcSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.mcEmail = function () { mcSub(function () { if (typeof window.pxConfig === 'function') window.pxConfig(PIPE); }); };
  window.mcLogs = function () { mcSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function mcOpen() {
    window._saReturn = null;
    var auto = S.data ? S.data.auto : true;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 14px">' +
        tb('⏱️ Timings', 'mcSub(mcTimings)', 'Change the times and where each time takes its files from (the 15-minute run, or its own download).') +
        tb('🛠️ Override', 'mcSub(mcOverride)', 'Mark a time done / not done, so it is skipped or produced again.') +
        tb('🔑 Credentials', 'mcCreds()', 'NMASS and MCX logins and the password of the report files - change them here.') +
        tb('✉️ Email To / CC', 'mcEmail()', 'Who gets the full file report and the summary report (To and CC).') +
        tb('⬆️ Upload files', 'mcSub(mcUpload)', 'Add files you got by hand to a time. They are saved, listed in Backup files and copied to PostgreSQL.') +
        tb('📦 Backup files', 'mcSub(mcBackup)', 'Pick a date or a date range, tick Full folder, and download everything as one zip.') +
        tb('🗂️ Logs', 'mcLogs()', 'The full log of every run.') +
        tb(auto ? '⏸ Auto: ON' : '▶ Auto: OFF', 'mcAuto()', 'Switch the by-itself runs on or off.', auto ? '' : ';border-color:rgba(248,113,113,.45);color:var(--red)') +
        tb('⛔ Stop', 'mcStop()', 'Stops the run that is going right now.', ';border-color:rgba(248,113,113,.45);color:var(--red)') +
      '</div><div id="mcMsg"></div><div id="mcDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.mcOpen = mcOpen;

  function say(txt, kind) {
    var el = $('mcMsg');
    if (el) el.innerHTML = txt ? '<div class="mc-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('mcMsg'); if (e) e.innerHTML = ''; }, 7000);
  }

  // ───────────────────────── render ─────────────────────────
  var STATE_TXT = { done: '✓ Sent', running: '<span class="mc-spin" style="width:10px;height:10px;margin-right:5px"></span>Running…', failed: '✗ Failed', missed: '✗ Missed', partial: '◐ Partly done', due: '● Starting…', upcoming: 'Later today', skipped: '⏭ Skipped' };
  var STEP_TXT = { ms15: '15-min link', nmass: 'NMASS', mcx: 'MCX', report: 'Report', email: 'E-mail' };

  function plain(h) { return String(h).replace(/<[^>]*>/g, ''); }
  function kpi(k, v, f, extra) { return '<div class="mc-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div>' + (extra || '') + '</div>'; }
  function srcText(s) { return s.mode === 'ms15' ? '🔗 15-min run ' + (s.ms15_run || '?') : '⬇ own download'; }

  function banner(d) {
    var nx = d.next;
    if (!d.trading_day) return '<div class="mc-ban" style="background:rgba(148,163,184,.10);border:1px solid var(--line-2);color:var(--muted)">Today is not a trading day (weekend / holiday) - nothing starts by itself.</div>';
    if (!d.auto) return '<div class="mc-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>Auto is OFF</b> - nothing starts by itself. Switch it on with the Auto button, or run a time by hand below.</div>';
    return '<div class="mc-ban" style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:var(--green)"><b>● Auto is ON</b> - each time starts by itself' + (nx ? ' · next: <b>' + esc(nx.time) + '</b>' : ' · no more times today') +
      '<span style="color:var(--dim)"> · a failed run is retried ' + (d.retries || 0) + ' time(s)</span></div>';
  }

  function kpis(d) {
    var c = d.counts || {}, total = (d.slots || []).length, done = c.done || 0;
    var run = (d.slots || []).filter(function (s) { return s.state === 'running'; })[0];
    var bad = (c.failed || 0) + (c.missed || 0) + (c.partial || 0);
    var nx = d.next;
    return '<div class="mc-kpis">' +
      kpi('Reports sent', done + '<small> / ' + total + '</small>', (c.skipped ? c.skipped + ' skipped · ' : '') + 'times complete (4 files in, 2 reports mailed)',
        '<div class="mc-bar"><i style="width:' + (total ? Math.round(100 * done / total) : 0) + '%"></i></div>') +
      kpi('Running now', run ? tlabel(run.slot) : '—', run ? 'started ' + (run.started || '').slice(11, 16) + (run.by ? ' · ' + run.by : '') : 'nothing running') +
      kpi('Needs attention', '<span style="color:' + (bad ? 'var(--red)' : 'var(--green)') + '">' + bad + '</span>', bad ? 'failed / missed - Retry or Skip' : 'all good') +
      kpi('Next run', nx ? nx.time : '—', nx ? 'in a few minutes by itself' : 'no more today') + '</div>';
  }

  var DOT_ORDER = ['MCX 90%', 'MCX deposit', 'CM collaterals', 'FO collaterals', 'Full report', 'Summary report'];
  function dots(s) {
    return '<div class="mc-dots">' + DOT_ORDER.map(function (l, i) {
      var on = s.found && s.found[l], bad = !on && (s.state === 'failed' || s.state === 'partial' || s.state === 'missed');
      return '<i class="' + (on ? 'on' : bad ? 'bad' : '') + (i >= 4 ? ' sq' : '') + '" title="' + esc(l + (on ? ' - ' + s.found[l] : ' - not there')) + '"></i>';
    }).join('') + '</div>';
  }

  function tile(s, anyRunning) {
    var st = s.state, k = "'" + s.slot + "'";
    var cls = st === 'done' ? 'done' : (st === 'failed' || st === 'missed' || st === 'partial') ? 'fail' : st === 'running' ? 'running' : st === 'skipped' ? 'skipped' : (st === 'upcoming' ? 'later' : '');
    var note = s.msg || (st === 'upcoming' ? 'waiting for ' + s.time : st === 'missed' ? 'did not start - nothing was sent' : '');
    if (s.attempts > 1 && (st === 'failed' || st === 'running')) note = 'try ' + s.attempts + ' · ' + note;
    var btn = '', sec = '';
    var lock = anyRunning && st !== 'running';
    var tip = lock ? ' title="Another run is going - wait, or Skip &amp; stop it"' : '';
    if (st === 'done') {
      btn = '<button class="mc-btn" onclick="mcSub(function(){mcBackup(' + k + ')})">📂 View files</button>';
      sec = '<button class="mc-link"' + (lock ? ' disabled' : '') + tip + ' onclick="mcRun(' + k + ',true)">↻ run again</button>';
    } else if (st === 'running') {
      btn = '<button class="mc-btn amber" disabled style="opacity:1"><span class="mc-spin"></span>Running…</button>';
      sec = '<button class="mc-link" onclick="mcSkip(' + k + ')">⏭ Skip &amp; stop this one</button>';
    } else if (st === 'skipped') {
      btn = '<button class="mc-btn" onclick="mcSkip(' + k + ',true)">↩ Undo skip</button>';
    } else if (st === 'upcoming') {
      btn = '<button class="mc-btn"' + (lock ? ' disabled' : '') + tip + ' onclick="mcRun(' + k + ',false)">▶ Run now</button>';
      sec = '<button class="mc-link" onclick="mcSkip(' + k + ')">⏭ Skip this time</button>';
    } else {
      btn = '<button class="mc-btn red"' + (lock ? ' disabled' : '') + tip + ' onclick="mcRun(' + k + ',false)">↻ ' + (st === 'due' ? 'Start now' : 'Retry') + '</button>';
      sec = '<button class="mc-link" onclick="mcSkip(' + k + ')">⏭ Skip - go on to the next</button>';
    }
    var steps = '';
    ['ms15', 'nmass', 'mcx', 'report', 'email'].forEach(function (n) {
      var r = (s.steps || {})[n]; if (!r) return;
      steps += '<span title="' + esc(r.msg) + '" style="font-size:10px;color:' + (r.ok ? 'var(--green)' : 'var(--red)') + '">' + (r.ok ? '✓' : '✗') + ' ' + STEP_TXT[n] + '</span> ';
    });
    var mailed = s.mailed && s.mailed.full && s.mailed.summary ? ' · ✉ mailed' : (s.mailed && (s.mailed.full || s.mailed.summary) ? ' · ✉ half mailed' : '');
    return '<div class="mc-run ' + cls + '"><div class="mc-id">' + esc(s.time) + '</div><div class="mc-src">' + esc(srcText(s)) + (s.override ? ' · override' : '') + '</div>' +
      '<div class="mc-st">' + (STATE_TXT[st] || st) + ' · ' + s.files + '/' + s.total + ' files' + mailed + '</div>' + dots(s) +
      '<div class="mc-note" title="' + esc(s.msg) + '">' + esc(note) + (steps ? '<br>' + steps : '') + '</div>' + btn + '<div>' + sec + '</div></div>';
  }

  function logBox(d) {
    var lines = (d.log || []);
    var txt = lines.length ? lines.slice(-25).map(function (l) { return esc(l.t + '  [' + tlabel(l.slot) + ']  ' + l.msg); }).join('\n') : 'No run since the server started. The log of every run is under 🗂️ Logs.';
    return '<div class="mc-lbl">Live log <span style="text-transform:none;letter-spacing:0">- latest lines (the full log of each run is under 🗂️ Logs)</span></div><div class="mc-logbox" id="mcLogBox">' + txt + '</div>';
  }

  function paintDyn(force) {
    var d = S.data, el = $('mcDyn');
    if (!el || !d || !d.slots) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('mcLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    var any = (d.slots || []).some(function (s) { return s.state === 'running'; });
    var showLog = any || (Date.now() - S.lastRunAt < 15000);        // the live log shows while a run goes and 15 s after
    if (!any && showLog) { clearTimeout(S.logTimer); S.logTimer = setTimeout(function () { S.sig = ''; paintDyn(true); }, 15200 - (Date.now() - S.lastRunAt)); }
    el.innerHTML = banner(d) + kpis(d) + (showLog ? logBox(d) : '') + '<div class="mc-lbl">Times for ' + esc(d.date) + '</div><div class="mc-runs">' +
      (d.slots || []).map(function (s) { return tile(s, any); }).join('') + '</div>' +
      '<div style="font-size:11px;color:var(--dim);margin-top:12px;line-height:1.6">🔗 <b>15-min run</b>: waits for that MARGIN SHORTAGE run to finish, then takes the 4 files from its backup (anything missing is downloaded). ⬇ <b>own download</b>: NMASS for CM / FO, and one MCX login that exports the MCX 90% and the MCX deposit files together. ' +
      'Files are saved as <span style="font-family:var(--mono)">&lt;date&gt; / &lt;time&gt; / files</span>. Dots: MCX 90%, MCX deposit, CM, FO, then the two reports.</div>';
    var nb = $('mcLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }

  function paintTile(d) {
    var chip = $('mcChip'), mini = $('mcMini');
    if (!chip || !d || !d.slots) return;
    var c = d.counts || {}, total = d.slots.length, bad = (c.failed || 0) + (c.missed || 0) + (c.partial || 0);
    var run = d.slots.filter(function (s) { return s.state === 'running'; })[0];
    chip.textContent = run ? 'running' : bad ? 'attention' : !d.auto ? 'auto off' : 'idle';
    chip.style.color = run ? 'var(--blue)' : bad ? 'var(--red)' : !d.auto ? 'var(--amber)' : 'var(--dim)';
    mini.textContent = !d.trading_day ? 'Not a trading day' :
      run ? tlabel(run.slot) + ' running…' :
      (c.done || 0) + ' / ' + total + ' sent' + (bad ? ' · ' + bad + ' need attention' : '') + (d.next ? ' · next ' + d.next.time : '') + (!d.auto ? ' · auto OFF' : '');
  }

  function load() {
    var next = 8000;
    return get('/mcx-cash/status').then(function (d) {
      S.data = d;
      if (d && d.slots) { if (d.running) S.lastRunAt = Date.now(); paintTile(d); if (modalOpen()) paintDyn(); if (d.running) next = 2500; }
    }).catch(function () { next = 15000; }).then(function () { schedule(next); });
  }
  function schedule(ms) {
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (document.hidden) { schedule(ms); } else { load(); } }, ms);
  }
  function reload() { S.sig = ''; clearTimeout(S.timer); setTimeout(load, 600); }
  window.mcRefresh = function () { S.sig = ''; load(); };

  // ───────────────────────── actions ─────────────────────────
  window.mcRun = function (slot, force) {
    if (force && !window.confirm('Run ' + tlabel(slot) + ' again? Everything is fetched, built and e-mailed again (the old files are kept aside).')) return;
    post('/mcx-cash/run', { slot: slot, force: !!force }).then(function (j) {
      if (j && j.ok) say(tlabel(slot) + ' started.'); else say((j && (j.error || j.detail)) || 'Could not start.', 'err');
      reload();
    });
  };
  window.mcSkip = function (slot, undo) {
    var running = ((S.data || {}).slots || []).some(function (s) { return s.slot === slot && s.state === 'running'; });
    if (!undo && running && !window.confirm('Stop the run that is going and skip ' + tlabel(slot) + '?')) return;
    post('/mcx-cash/skip', { slot: slot, undo: !!undo }).then(function () {
      say(undo ? tlabel(slot) + ' is open again.' : tlabel(slot) + ' skipped - the next one runs on time.');
      reload();
    });
  };
  window.mcStop = function () {
    if (!window.confirm('Stop the run that is going now?')) return;
    post('/mcx-cash/stop').then(function (j) { say(j && j.stopped ? 'Stopping…' : 'Nothing is running.'); reload(); });
  };
  window.mcAuto = function () {
    var on = !(S.data && S.data.auto);
    ask(on ? 'Switch auto runs ON' : 'Switch auto runs OFF', function (pw) {
      post('/mcx-cash/settings', { password: pw, auto: on }).then(function (j) {
        if (j && j.ok) { mcOpen(); say(on ? 'Auto is ON - each time starts by itself.' : 'Auto is OFF - nothing starts by itself.'); }
        else { mcOpen(); say((j && j.error) || 'Failed', 'err'); }
      });
    });
  };

  // ───────────────────────── Timings (+ where each time takes its files from) ─────────────────────────
  window.mcTimings = function () {
    ask('Timings', function (pw) {
      get('/mcx-cash/settings').then(function (s) {
        S.pw = pw;
        var ov = s.ms15_override || {};
        var rows = (s.slots || []).map(function (x) { return timeRow(x.time, x.mode, ov[x.time] || ''); }).join('');
        var runs = (s.ms15_runs || []).map(function (r) { return 'run ' + r.run + ' = ' + r.start; }).join(' · ');
        var h = '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">Each time has a <b>source</b>: <b>15-min run</b> waits for that MARGIN SHORTAGE run to finish and takes its files; <b>Own download</b> fetches them itself. ' +
          'The 15-min run number is picked from the times of the runs; type a number in <b>Run no.</b> only to force a different one. The timer reads this every few seconds - nothing to restart. To run a changed time again today, use <b>Override → Mark not done</b>.</div>' +
          '<div style="display:grid;grid-template-columns:110px 1fr 96px;gap:8px;margin-bottom:4px"><div class="mc-lbl">Time</div><div class="mc-lbl">Files come from</div><div class="mc-lbl">Run no.</div></div>' +
          '<div id="mcTimeRows">' + rows + '</div>' +
          '<div style="display:flex;gap:8px;margin:10px 0 14px"><button class="sa-tool" onclick="mcTimeAdd()">＋ Add a time</button><button class="sa-tool" onclick="mcTimeDel()">－ Remove last</button>' +
          '<button class="sa-tool" onclick="mcTimeDefault()">↺ Back to the standard 8 times</button></div>' +
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:14px"><input type="checkbox" id="mcAutoChk" ' + (s.auto ? 'checked' : '') + '> Start each time by itself</label>' +
          '<div style="font-size:11.5px;color:var(--dim);margin-bottom:12px;line-height:1.6">15-minute runs today: ' + esc(runs || 'none set (MS15_RUN windows)') + '.<br>A time that missed its start is still run up to ' + esc(s.grace_min) + ' minutes late (never after the next time begins). A failed run is retried ' + esc(s.retries) + ' time(s), ' + esc(s.retry_gap_min) + ' minutes apart.</div>' +
          '<button class="sa-save" id="mcTimeBtn" data-no-busy="1" style="width:100%" onclick="mcTimeSave()">Save timings</button><div class="sa-msg" id="mcTimeMsg"></div>';
        modal('⏱️ ' + TITLE + ' - timings', h);
      });
    });
  };
  function timeRow(t, mode, run) {
    return '<div class="mc-trow"><input type="time" class="mc-in mcTimeIn" value="' + esc(t) + '">' +
      '<select class="mc-in mcModeIn"><option value="ms15"' + (mode === 'ms15' ? ' selected' : '') + '>🔗 15-min run (wait for it, take its files)</option><option value="own"' + (mode === 'own' ? ' selected' : '') + '>⬇ Own download</option></select>' +
      '<input type="number" min="1" class="mc-in mcRunIn" placeholder="auto" value="' + esc(run) + '"></div>';
  }
  window.mcTimeAdd = function () { var box = $('mcTimeRows'); if (box.querySelectorAll('.mcTimeIn').length >= 12) return; box.insertAdjacentHTML('beforeend', timeRow('', 'own', '')); };
  window.mcTimeDel = function () { var r = $('mcTimeRows').querySelectorAll('.mc-trow'); if (r.length > 1) r[r.length - 1].remove(); };
  window.mcTimeDefault = function () {
    var def = ['09:30', '10:30', '11:30', '12:30', '13:30', '14:30', '15:30', '16:30'], ms = ['09:30', '11:30', '14:30', '16:30'];
    $('mcTimeRows').innerHTML = def.map(function (t) { return timeRow(t, ms.indexOf(t) >= 0 ? 'ms15' : 'own', ''); }).join('');
  };
  window.mcTimeSave = function () {
    var rows = [].slice.call(document.querySelectorAll('#mcTimeRows .mc-trow')), m = $('mcTimeMsg');
    var times = [], modes = {}, runs = {}, bad = false;
    rows.forEach(function (r) {
      var t = r.querySelector('.mcTimeIn').value; if (!t) { bad = true; return; }
      times.push(t); modes[t] = r.querySelector('.mcModeIn').value;
      var n = r.querySelector('.mcRunIn').value; if (n) runs[t] = n;
    });
    if (bad) { if (m) m.innerHTML = '<span style="color:var(--red)">Fill every time (or remove the empty ones).</span>'; return; }
    var btn = $('mcTimeBtn'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post('/mcx-cash/settings', { password: S.pw, times: times.join(','), modes: modes, ms15_runs: runs, auto: $('mcAutoChk').checked }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = j && j.ok ? '✓ Saved' : 'Save timings'; }
      if (m) m.innerHTML = j && j.ok ? '<span style="color:var(--green)">Saved - the timer now uses: ' + esc(j.times) + '</span>' : '<span style="color:var(--red)">' + esc((j && j.error) || 'Failed') + '</span>';
      reload();
    });
  };

  // ───────────────────────── Override ─────────────────────────
  window.mcOverride = function () {
    ask('Override', function (pw) { S.pw = pw; paintOverride(); });
  };
  function paintOverride(note) {
    get('/mcx-cash/status').then(function (d) {
      S.data = d;
      var rows = (d.slots || []).map(function (s) {
        var k = "'" + s.slot + "'";
        return '<div class="mc-ovr"><b style="width:56px;font-family:var(--mono)">' + esc(s.time) + '</b>' +
          '<span style="flex:1;min-width:170px;color:var(--muted)">' + esc(plain(STATE_TXT[s.state] || s.state)) + ' · ' + s.files + '/' + s.total + ' files' + (s.override ? ' · override' : '') + (s.redo ? ' · will run again' : '') + '</span>' +
          '<button class="sa-tool" onclick="mcOv(' + k + ',\'undone\')" data-tip="Treat as NOT done: the timer runs it again from scratch (old files are kept aside) and mails the reports again.">↺ Mark not done</button>' +
          '<button class="sa-tool" onclick="mcOv(' + k + ',\'done\')" data-tip="Treat as done (for example you sent the reports yourself). The timer leaves it alone.">✓ Mark done</button>' +
          (s.state === 'skipped' ? '<button class="sa-tool" onclick="mcSkipOv(' + k + ',true)">↩ Undo skip</button>' : '<button class="sa-tool" onclick="mcSkipOv(' + k + ')">⏭ Skip</button>') + '</div>';
      }).join('');
      modal('🛠️ ' + TITLE + ' - override',
        '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:10px">Changed a time, or want a time done again? Press <b>Mark not done</b> - it runs at its time (straight away if that time has already passed) and the reports are mailed again. Nothing is deleted: old files are moved aside.</div>' +
        (note ? '<div class="mc-msg" style="color:' + (note.err ? 'var(--red)' : 'var(--green)') + '">' + esc(note.t) + '</div>' : '') + rows);
    });
  }
  window.mcOv = function (slot, mode) {
    if (!window.confirm(mode === 'undone' ? 'Mark ' + tlabel(slot) + ' as NOT done? It will run again and the reports are mailed again.' : 'Mark ' + tlabel(slot) + ' as done without running?')) return;
    post('/mcx-cash/override', { slot: slot, mode: mode, password: S.pw }).then(function (j) {
      paintOverride(j && j.ok ? { t: tlabel(slot) + (mode === 'undone' ? ' will run again at its time.' : ' marked done.') } : { t: (j && (j.error || j.detail)) || 'Failed', err: true });
      reload();
    });
  };
  window.mcSkipOv = function (slot, undo) {
    post('/mcx-cash/skip', { slot: slot, undo: !!undo }).then(function () { paintOverride({ t: undo ? tlabel(slot) + ' is open again.' : tlabel(slot) + ' skipped.' }); reload(); });
  };

  // ───────────────────────── Backup files (date range, full folder, one zip) ─────────────────────────
  function bkState() {
    if (!S.bk) { var t = (S.data && S.data.date) ? dmyToIso(S.data.date) : isoToday(); S.bk = { from: t, to: t, full: true, sel: {}, data: null }; }
    return S.bk;
  }
  window.mcBackup = function (focusSlot) {
    var b = bkState();
    b.full = true; b.sel = {};
    if (focusSlot) { b.full = false; b.sel[focusSlot] = true; b.from = b.to = (S.data && S.data.date) ? dmyToIso(S.data.date) : isoToday(); }
    var chips = ((S.data || {}).slots || []).map(function (s, i) {
      return '<button class="mc-chip' + (b.sel[s.slot] ? ' on' : '') + '" id="mcBkChip' + i + '" onclick="mcBkToggle(\'' + s.slot + '\',' + i + ')">' + esc(s.time) + '</button> ';
    }).join('');
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">Pick one day or a range of days. The zip keeps the same layout as on disk: <span style="font-family:var(--mono)">date / time / files</span> (the 4 inputs and the 2 reports).</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="mc-lbl">From</div><input type="date" id="mcBkFrom" class="mc-in" style="width:100%" value="' + b.from + '" onchange="mcBkDates()"></div>' +
      '<div><div class="mc-lbl">To</div><input type="date" id="mcBkTo" class="mc-in" style="width:100%" value="' + b.to + '" onchange="mcBkDates()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="mc-chip" onclick="mcBkQuick(0)">Today</button><button class="mc-chip" onclick="mcBkQuick(1)">Yesterday</button><button class="mc-chip" onclick="mcBkQuick(7)">Last 7 days</button><button class="mc-chip" onclick="mcBkQuick(30)">Last 30 days</button></div>' +
      '<label style="display:flex;gap:8px;align-items:center;font-size:13px;font-weight:650;margin-bottom:10px"><input type="checkbox" id="mcBkFull" ' + (b.full ? 'checked' : '') + ' onchange="mcBkFull()"> Full folder - every time of the whole day(s)</label>' +
      '<div id="mcBkChips" style="display:' + (b.full ? 'none' : 'flex') + ';gap:6px;flex-wrap:wrap;margin-bottom:12px">' + chips + '</div>' +
      '<div id="mcBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="mcBkGo" data-no-busy="1" style="width:100%" onclick="mcBkDownload()">⬇ Download zip</button><div class="sa-msg" id="mcBkMsg"></div>');
    widen(true);
    mcBkLoad();
  };
  window.mcBkDates = function () { var b = bkState(); b.from = $('mcBkFrom').value || b.from; b.to = $('mcBkTo').value || b.from; if (b.to < b.from) { var x = b.from; b.from = b.to; b.to = x; } mcBkLoad(); };
  window.mcBkQuick = function (n) { var b = bkState(); b.to = isoToday(); b.from = n === 1 ? isoAgo(1) : isoAgo(n); if (n === 1) b.to = isoAgo(1); $('mcBkFrom').value = b.from; $('mcBkTo').value = b.to; mcBkLoad(); };
  window.mcBkFull = function () { var b = bkState(); b.full = $('mcBkFull').checked; $('mcBkChips').style.display = b.full ? 'none' : 'flex'; mcBkPaint(); };
  window.mcBkToggle = function (slot, i) { var b = bkState(); b.sel[slot] = !b.sel[slot]; var c = $('mcBkChip' + i); if (c) c.classList.toggle('on', !!b.sel[slot]); mcBkPaint(); };
  function selSlots() { var b = bkState(); return b.full ? null : Object.keys(b.sel).filter(function (k) { return b.sel[k]; }); }
  window.mcBkLoad = function () {
    var b = bkState();
    get('/mcx-cash/range?from_date=' + encodeURIComponent(b.from) + '&to_date=' + encodeURIComponent(b.to)).then(function (j) { b.data = j; mcBkPaint(); })
      .catch(function () { var l = $('mcBkList'); if (l) l.innerHTML = '<div style="color:var(--red)">Could not read the folders.</div>'; });
  };
  function mcBkPaint() {
    var b = bkState(), l = $('mcBkList'); if (!l || !b.data) return;
    var want = selSlots(), tot = 0, nf = 0, days = 0;
    var html = (b.data.days || []).map(function (day) {
      var folders = (day.folders || []).filter(function (f) { return !want || want.indexOf(f.slot) >= 0; });
      var cnt = folders.reduce(function (a, f) { return a + f.files.length; }, 0);
      if (!cnt) return '';
      days++; tot += cnt; nf += folders.length;
      var body = folders.map(function (f) {
        return '<div style="margin:6px 0 2px;font-size:12px;font-weight:650">' + esc(tlabel(f.slot)) + ' <span style="font-weight:400;color:var(--dim)">· ' + f.files.length + ' file' + (f.files.length === 1 ? '' : 's') + '</span></div>' +
          f.files.map(function (x) {
            return '<div class="mc-frow"><span class="n">' + esc(x.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(x.size) + ' · ' + esc((x.mtime || '').slice(11, 16)) + '</span>' +
              '<a href="' + API + '/mcx-cash/file?date=' + encodeURIComponent(day.date) + '&slot=' + encodeURIComponent(f.slot) + '&name=' + encodeURIComponent(x.name) + '" download title="Download this file">⬇</a></div>';
          }).join('');
      }).join('');
      return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + ((b.data.days || []).length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day.date) +
        ' <span style="font-weight:400;color:var(--dim)">· ' + folders.length + ' time' + (folders.length === 1 ? '' : 's') + ' · ' + cnt + ' files</span></summary>' + body + '</details>';
    }).join('');
    l.innerHTML = (tot ? '<div style="font-size:12px;color:var(--muted);margin-bottom:8px"><b style="color:var(--ink)">' + days + '</b> day' + (days === 1 ? '' : 's') + ' · <b style="color:var(--ink)">' + nf + '</b> folders · <b style="color:var(--ink)">' + tot + '</b> files will be in the zip</div>' + html
      : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No files for that ' + (b.from === b.to ? 'day' : 'range') + (want && want.length ? ' in the chosen times' : '') + '.</div>');
    var go = $('mcBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; go.textContent = tot ? '⬇ Download zip (' + tot + (tot === 1 ? ' file)' : ' files)') : '⬇ Download zip'; }
  }
  window.mcBkDownload = function () {
    var b = bkState(), want = selSlots();
    if (want && !want.length) { var m = $('mcBkMsg'); if (m) m.innerHTML = '<span style="color:var(--red)">Tick at least one time, or tick Full folder.</span>'; return; }
    window.location.href = API + '/mcx-cash/zip-range?from_date=' + encodeURIComponent(b.from) + '&to_date=' + encodeURIComponent(b.to) + (want ? '&slots=' + encodeURIComponent(want.join(',')) : '');
  };

  // ───────────────────────── Upload files (got by hand) ─────────────────────────
  window.mcUpload = function () {
    var d = S.data || { slots: [] };
    var first = (d.slots.filter(function (s) { return s.state !== 'done' && s.state !== 'upcoming'; })[0] || d.slots[0] || { slot: '09-30' }).slot;
    var opts = d.slots.map(function (s) { return '<option value="' + esc(s.slot) + '"' + (s.slot === first ? ' selected' : '') + '>' + esc(s.time) + ' · ' + esc(plain(STATE_TXT[s.state] || s.state)) + ' · ' + s.files + '/' + s.total + ' files</option>'; }).join('');
    modal('⬆️ ' + TITLE + ' - upload files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.6;margin-bottom:12px">Got a file by hand today? Choose the time and add it here. It is saved in that time\'s folder, appears in <b>Backup files</b>, and is copied to PostgreSQL. When the 4 input files are there, the next run of that time only builds and mails the reports.</div>' +
      '<div class="mc-lbl">Time (today)</div><select id="mcUpSlot" class="mc-in" style="width:100%;margin-bottom:12px">' + opts + '</select>' +
      '<div style="padding:10px 14px;border:1px dashed var(--line-2);border-radius:10px;font-size:11.5px;line-height:1.9;margin-bottom:12px"><b style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase">Accepted files</b><br>' +
      '<span style="font-family:var(--mono);color:var(--teal)">CM_ClientLevelCollaterals_*.csv<br>FO_ClientLevelCollaterals_*.csv<br>MCX 90% file (.xlsx with “Cli / TM Mrgn 90% (Rs)”)<br>MCX deposit file (.xlsx with “UCC Code”, “Allocation at EOD”)</span></div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><button class="sa-save" data-no-busy="1" onclick="mcUpPick()">📄 Choose files…</button>' +
      '<button class="sa-save" data-no-busy="1" style="background:var(--blue);color:var(--blue-d)" onclick="mcUpPick(true)">📁 Choose a whole folder…</button></div>' +
      '<div style="font-size:11.5px;color:var(--dim);margin-top:6px">Whole folder: every .csv / .xlsx in it is sent; the ones this process does not use are listed as not accepted.</div>' +
      '<div id="mcUpRes" style="margin-top:12px"></div>');
  };
  window.mcUpPick = function (isDir) {
    var old = $('mcUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.multiple = true; inp.id = 'mcUpInput'; inp.style.display = 'none';
    if (isDir) { inp.setAttribute('webkitdirectory', ''); inp.setAttribute('directory', ''); }
    inp.onchange = function () {
      var all = [].slice.call(inp.files), ok = all, skipped = 0;
      if (isDir) { ok = all.filter(function (f) { return /\.(csv|xlsx)$/i.test(f.name); }); skipped = all.length - ok.length; }
      inp.remove();
      if (!ok.length) { var r = $('mcUpRes'); if (r) r.innerHTML = '<div style="color:var(--red)">No .csv / .xlsx file was found in that selection.</div>'; return; }
      mcUpSend(ok, skipped);
    };
    document.body.appendChild(inp);
    inp.click();
  };
  function mcUpSend(files, skipped) {
    var slot = ($('mcUpSlot') || {}).value || '', res = $('mcUpRes');
    if (res) res.innerHTML = '<div style="color:var(--muted);font-size:12.5px"><span class="mc-spin"></span>Uploading ' + files.length + ' file(s) into ' + esc(tlabel(slot)) + '…</div>';
    var fd = new FormData(); files.forEach(function (f) { fd.append('files', f); });
    fetch(API + '/mcx-cash/upload?slot=' + encodeURIComponent(slot), { method: 'POST', body: fd })
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); })
      .then(function (j) {
        if (!res) return;
        if (j.error || j.detail) { res.innerHTML = '<div style="color:var(--red)">' + esc(j.error || j.detail) + '</div>'; return; }
        var nok = (j.ok || []).length, nbad = (j.rejected || []).length;
        var good = nok && !nbad;
        var h = '<div style="padding:12px 14px;border-radius:10px;margin-bottom:10px;border:1px solid ' + (good ? 'rgba(52,211,153,.5)' : 'rgba(251,191,36,.5)') + ';background:' + (good ? 'rgba(52,211,153,.10)' : 'rgba(251,191,36,.10)') + ';color:' + (good ? 'var(--green)' : 'var(--amber)') + ';font-size:13px;font-weight:650">' +
          (good ? '✓ Success - ' : '') + nok + ' file' + (nok === 1 ? '' : 's') + ' saved into ' + esc(tlabel(j.slot)) + (nbad ? ' · ' + nbad + ' not accepted' : '') + (skipped ? ' · ' + skipped + ' other file(s) ignored' : '') +
          (nok ? '<div style="font-weight:400;margin-top:4px">' + (j.inputs_complete ? 'All 4 input files are there - the reports for ' + esc(tlabel(j.slot)) + ' are built and mailed on the next run of that time (or press Run now).' : 'Still missing: ' + esc((j.missing || []).join(', ')) + '.') + '</div>' : '') + '</div>' +
          '<button class="sa-save" data-no-busy="1" style="width:100%;margin-bottom:12px" onclick="saCloseModal()">✓ Done</button>' +
          '<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Folder: <span style="font-family:var(--mono);color:var(--ink)">' + esc(j.folder) + '</span></div>';
        (j.ok || []).forEach(function (m) { h += '<div class="sa-ev ok"><span class="ic">✓</span><span>' + esc(m) + '</span><span class="what">saved</span></div>'; });
        (j.rejected || []).forEach(function (m) { h += '<div class="sa-ev bad"><span class="ic">✕</span><span>' + esc(m) + '</span><span class="what">not accepted</span></div>'; });
        res.innerHTML = h;
        var body = $('saModalBody'); if (body) body.scrollTop = body.scrollHeight;
        reload();
      }).catch(function (e) { if (res) res.innerHTML = '<div style="color:var(--red)">Upload failed: ' + esc(e.message || e) + '</div>'; });
  }

  // ───────────────────────── start ─────────────────────────
  function start() { if (build()) load(); else setTimeout(start, 600); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
