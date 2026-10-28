/* LIVE ALLOCATION CAPTURE - a small card at the end of the Process Center's "Processing & Reports" row (same look as
   PHYSICAL DELIVERY). "Open" shows the window with the same kind of buttons the other processes have:
   Timings, Override, Credentials, Backup files, Logs, Auto on/off, Stop - plus the 4 KPI cards, the live log and the
   numbered folders (1-8) with Run / Retry / Skip / Undo skip.
   The capture itself starts BY ITSELF at each timing (timer inside the server: core/live_allocation.py).
   Backend: /live-alloc/* (api.py) and the shared /px/* windows (Credentials, Logs) with pipe=liveallocation.
   Injected at serve time (see api.py /legacy); dashboard.html is not modified. */
(function () {
  'use strict';
  if (window.__liveAllocInjected) return;
  window.__liveAllocInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'LIVE ALLOCATION CAPTURE';
  var PIPE = 'liveallocation';
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
  function hideTip() { var t = $('globalTip'); if (t) t.style.display = 'none'; }       // the hovered button is gone - its tip must go too
  function modal(t, h) { hideTip(); if (typeof window.saShowModal === 'function') window.saShowModal(t, h); }
  function dmyToIso(s) { var p = String(s || '').split('-'); return p.length === 3 ? p[2] + '-' + p[1] + '-' + p[0] : ''; }
  function isoToday() { var d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function isoAgo(n) { var d = new Date(Date.now() - n * 864e5); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }

  var css = document.createElement('style');
  css.textContent =
    '.la-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.la-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.la-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.la-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.la-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.la-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.la-bar{height:5px;border-radius:9px;background:var(--line);margin-top:7px;overflow:hidden}.la-bar i{display:block;height:100%;background:var(--green);transition:width .6s}' +
    '.la-runs{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}' +
    '@media(max-width:820px){.la-kpis,.la-runs{grid-template-columns:repeat(2,1fr)}}' +
    '.la-run{background:var(--panel-2);border:1px solid var(--line);border-radius:13px;padding:12px 11px 11px;text-align:center;display:flex;flex-direction:column;gap:6px;min-width:0}' +
    '.la-run.done{border-color:rgba(52,211,153,.4)}.la-run.fail{border-color:rgba(248,113,113,.5)}' +
    '.la-run.running{border-color:var(--amber);box-shadow:0 0 0 1px rgba(251,191,36,.25)}' +
    '.la-run.skipped,.la-run.later{opacity:.75}' +
    '.la-id{font-family:var(--display);font-size:16px;font-weight:750}' +
    '.la-time{font-family:var(--mono);font-size:10.5px;color:var(--muted)}' +
    '.la-st{font-family:var(--mono);font-size:9.5px;letter-spacing:.8px;text-transform:uppercase;color:var(--dim)}' +
    '.la-run.done .la-st{color:var(--green)}.la-run.fail .la-st{color:var(--red)}.la-run.running .la-st{color:var(--amber)}' +
    '.la-dots{display:flex;gap:5px;justify-content:center}.la-dots i{width:9px;height:9px;border-radius:50%;background:var(--line-2);display:block}' +
    '.la-dots i.on{background:var(--green)}.la-dots i.bad{background:var(--red)}' +
    '.la-note{font-size:10.5px;color:var(--muted);min-height:26px;line-height:1.35;overflow:hidden}' +
    '.la-btn{width:100%;font-family:var(--sans);font-weight:650;font-size:12px;border:0;border-radius:9px;padding:8px;cursor:pointer;background:var(--blue);color:var(--blue-d)}' +
    '.la-btn:disabled{background:var(--panel-3);color:var(--dim);cursor:not-allowed}' +
    '@keyframes laSpin{to{transform:rotate(360deg)}}.la-spin{display:inline-block;width:14px;height:14px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:laSpin .8s linear infinite;vertical-align:-2px;margin-right:8px}' +
    '.la-btn:disabled.amber{background:var(--amber);color:var(--amber-d);cursor:default}' +
    '.la-btn.red{background:var(--red);color:#fff}.la-btn.amber{background:var(--amber);color:var(--amber-d)}' +
    '.la-link{background:none;border:0;color:var(--muted);font-size:11px;cursor:pointer;text-decoration:underline;padding:0}' +
    '.la-link:disabled{opacity:.4;cursor:not-allowed}' +
    '.la-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.la-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:150px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.la-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.la-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.la-frow a{color:var(--blue);text-decoration:none}.la-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.la-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '.la-chip.on{border-color:rgba(59,130,246,.6);background:rgba(59,130,246,.14);color:var(--blue)}' +
    '.la-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.la-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.la-ovr{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:9px 0;border-bottom:1px solid var(--line);font-size:12.5px}';
  document.head.appendChild(css);

  // ───────────────────────── the small card (last in "Processing & Reports", like PHYSICAL DELIVERY) ─────────────────────────
  function build() {
    if ($('task-liveallocation')) return true;
    var slot = $('physdelSlot');
    var grid = slot ? slot.parentNode : null;
    if (!grid) {
      var secs = [].slice.call(document.querySelectorAll('.sec'));
      var sec = secs.filter(function (s) { return /processing/i.test(s.textContent); })[0];
      grid = sec ? sec.nextElementSibling : null;
    }
    if (!grid) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-liveallocation';
    tile.style.cssText = '--tile-accent:var(--violet);cursor:pointer';
    tile.onclick = function () { laOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">📥</div>' +
      '<div class="tile-name">LIVE ALLOCATION CAPTURE <span class="st" id="laChip">idle</span></div>' +
      '<div class="tile-desc">Downloads the NMASS, NCDEX and MCX allocation files 8 times a day into folders 1 to 8. Starts by itself at each timing - nothing to click.</div>' +
      '<div class="tile-desc" id="laMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:var(--violet);color:var(--violet-d,#1a0d33)" onclick="event.stopPropagation();laOpen()" data-tip="Open LIVE ALLOCATION CAPTURE: folders 1-8, timings, override, credentials, backup files, logs.">Open</button></div>';
    grid.appendChild(tile);
    return true;
  }
  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('laDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(940px,96vw)' : '';
  }
  (function hookClose() {                                     // the window is wider than the others - back to normal on close
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__la) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__la = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  window.laSub = function (fn) { window._saReturn = laOpen; fn(); };
  window.laCreds = function () { laSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.laLogs = function () { laSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function laOpen() {
    window._saReturn = null;
    var auto = S.data ? S.data.auto : true;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 14px">' +
        tb('⏱️ Timings', 'laSub(laTimings)', 'Change the 8 timings. The timer uses the new times within seconds.') +
        tb('🛠️ Override', 'laSub(laOverride)', 'Mark a folder done / not done, so it is skipped or captured again at its time.') +
        tb('🔑 Credentials', 'laCreds()', 'NMASS, NCDEX and MCX logins - change a password here.') +
        tb('⬆️ Upload files', 'laSub(laUpload)', 'Add files you captured by hand to a folder. They are saved, listed in Backup files and copied to PostgreSQL.') +
        tb('📦 Backup files', 'laSub(laBackup)', 'Pick a date or a date range, tick Full folder, and download everything as one zip.') +
        tb('🗂️ Logs', 'laLogs()', 'The full log of every capture.') +
        tb(auto ? '⏸ Auto: ON' : '▶ Auto: OFF', 'laAuto()', 'ON: every folder starts by itself at its time, nothing to click. OFF: nothing starts by itself - you press Run now yourself. Asks for the password.', auto ? '' : ';border-color:rgba(248,113,113,.45);color:var(--red)') +
        tb('⛔ Stop', 'laStop()', 'Stops the capture that is running right now.', ';border-color:rgba(248,113,113,.45);color:var(--red)') +
      '</div><div id="laMsg"></div><div id="laDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.laOpen = laOpen;

  function say(txt, kind) {
    var el = $('laMsg');
    if (el) el.innerHTML = txt ? '<div class="la-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('laMsg'); if (e) e.innerHTML = ''; }, 7000);
  }

  // ───────────────────────── render ─────────────────────────
  var STATE_TXT = { done: '✓ Captured', running: '<span class="la-spin" style="width:10px;height:10px;margin-right:5px"></span>Running…', failed: '✗ Failed', missed: '✗ Missed', partial: '◐ Partly done', due: '● Starting…', upcoming: 'Later today', skipped: '⏭ Skipped' };

  function plain(h) { return String(h).replace(/<[^>]*>/g, ''); }
  function kpi(k, v, f, extra) { return '<div class="la-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div>' + (extra || '') + '</div>'; }

  function banner(d) {
    var nx = d.next;
    if (!d.trading_day) return '<div class="la-ban" style="background:rgba(148,163,184,.10);border:1px solid var(--line-2);color:var(--muted)">Today is not a trading day (weekend / holiday) - nothing starts by itself.</div>';
    if (!d.auto) return '<div class="la-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>Auto is OFF</b> - nothing starts by itself. Switch it on with the Auto button, or run a folder by hand below.</div>';
    return '<div class="la-ban" style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:var(--green)"><b>● Auto is ON</b> - each folder starts by itself at its time' + (nx ? ' · next: <b>folder ' + nx.slot + ' at ' + esc(nx.time) + '</b>' : ' · no more folders today') +
      '<span style="color:var(--dim)"> · a failed capture is retried ' + (d.retries || 0) + ' time(s)</span></div>';
  }

  function kpis(d) {
    var c = d.counts || {}, total = (d.slots || []).length, done = c.done || 0;
    var run = (d.slots || []).filter(function (s) { return s.state === 'running'; })[0];
    var bad = (c.failed || 0) + (c.missed || 0) + (c.partial || 0);
    var nx = d.next;
    return '<div class="la-kpis">' +
      kpi('Captured', done + '<small> / ' + total + '</small>', (c.skipped ? c.skipped + ' skipped · ' : '') + 'folders complete (5 of 5 files)',
        '<div class="la-bar"><i style="width:' + (total ? Math.round(100 * done / total) : 0) + '%"></i></div>') +
      kpi('Running now', run ? 'Folder ' + run.slot : '—', run ? 'started ' + (run.started || '').slice(11, 16) + (run.by ? ' · ' + run.by : '') : 'nothing running') +
      kpi('Needs attention', '<span style="color:' + (bad ? 'var(--red)' : 'var(--green)') + '">' + bad + '</span>', bad ? 'failed / missed - Retry or Skip' : 'all good') +
      kpi('Next capture', nx ? nx.time : '—', nx ? 'folder ' + nx.slot : 'no more today') + '</div>';
  }

  function dots(s) {
    var order = ['CM collaterals', 'FO collaterals', 'CO collaterals', 'NCDEX net deposits', 'MCX web allocation'];
    return '<div class="la-dots">' + order.map(function (l) {
      var on = s.found && s.found[l], bad = !on && (s.state === 'failed' || s.state === 'partial' || s.state === 'missed');
      return '<i class="' + (on ? 'on' : bad ? 'bad' : '') + '" title="' + esc(l + (on ? ' - ' + s.found[l] : ' - not there')) + '"></i>';
    }).join('') + '</div>';
  }

  function tile(s, anyRunning) {
    var st = s.state;
    var cls = st === 'done' ? 'done' : (st === 'failed' || st === 'missed' || st === 'partial') ? 'fail' : st === 'running' ? 'running' : st === 'skipped' ? 'skipped' : (st === 'upcoming' ? 'later' : '');
    var note = s.msg || (st === 'upcoming' ? 'waiting for ' + s.time : st === 'missed' ? 'did not start - nothing was captured' : '');
    if (s.attempts > 1 && (st === 'failed' || st === 'running')) note = 'try ' + s.attempts + ' · ' + note;
    var btn = '', sec = '';
    var lock = anyRunning && st !== 'running';
    var tip = lock ? ' title="Another capture is running - wait, or Skip &amp; stop it"' : '';
    if (st === 'done') {
      btn = '<button class="la-btn" onclick="laSub(function(){laBackup(' + s.slot + ')})">📂 View files</button>';
      sec = '<button class="la-link"' + (lock ? ' disabled' : '') + tip + ' onclick="laRun(' + s.slot + ',true)">↻ capture again</button>';
    } else if (st === 'running') {
      btn = '<button class="la-btn amber" disabled style="opacity:1"><span class="la-spin"></span>Running…</button>';
      sec = '<button class="la-link" onclick="laSkip(' + s.slot + ')">⏭ Skip &amp; stop this one</button>';
    } else if (st === 'skipped') {
      btn = '<button class="la-btn" onclick="laSkip(' + s.slot + ',true)">↩ Undo skip</button>';
    } else if (st === 'upcoming') {
      btn = '<button class="la-btn"' + (lock ? ' disabled' : '') + tip + ' onclick="laRun(' + s.slot + ',false)">▶ Run now</button>';
      sec = '<button class="la-link" onclick="laSkip(' + s.slot + ')">⏭ Skip this slot</button>';
    } else {
      btn = '<button class="la-btn red"' + (lock ? ' disabled' : '') + tip + ' onclick="laRun(' + s.slot + ',false)">↻ ' + (st === 'due' ? 'Start now' : 'Retry') + '</button>';
      sec = '<button class="la-link" onclick="laSkip(' + s.slot + ')">⏭ Skip - go on to the next</button>';
    }
    var steps = '';
    ['nmass_ncdex', 'mcx'].forEach(function (k) {
      var r = (s.steps || {})[k]; if (!r) return;
      if (st === 'done') r = { ok: true, msg: r.ok ? r.msg : 'Files added by hand - all files are there.' };
      steps += '<span title="' + esc(r.msg) + '" style="font-size:10px;color:' + (r.ok ? 'var(--green)' : 'var(--red)') + '">' + (r.ok ? '✓' : '✗') + ' ' + (k === 'mcx' ? 'MCX' : 'NMASS+NCDEX') + '</span> ';
    });
    return '<div class="la-run ' + cls + '"><div class="la-id">Folder ' + s.slot + '</div><div class="la-time">' + esc(s.time) + (s.override ? ' · override' : '') + '</div>' +
      '<div class="la-st">' + (STATE_TXT[st] || st) + ' · ' + s.files + '/' + s.total + ' files</div>' + dots(s) +
      '<div class="la-note" title="' + esc(s.msg) + '">' + esc(note) + (steps ? '<br>' + steps : '') + '</div>' + btn + '<div>' + sec + '</div></div>';
  }

  function logBox(d) {
    var lines = (d.log || []);
    var txt = lines.length ? lines.slice(-25).map(function (l) { return esc(l.t + '  [F' + l.slot + ']  ' + l.msg); }).join('\n') : 'No capture has run since the server started. The log of every capture is under 🗂️ Logs.';
    return '<div class="la-lbl">Live log <span style="text-transform:none;letter-spacing:0">- latest lines (the full log of each capture is under 🗂️ Logs)</span></div><div class="la-logbox" id="laLogBox">' + txt + '</div>';
  }

  function paintDyn(force) {
    var d = S.data, el = $('laDyn');
    if (!el || !d || !d.slots) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('laLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    var any = (d.slots || []).some(function (s) { return s.state === 'running'; });
    // the live log is only shown while a capture runs and for 15 seconds after it stops or finishes
    var showLog = any || (Date.now() - S.lastRunAt < 15000);
    if (!any && showLog) { clearTimeout(S.logTimer); S.logTimer = setTimeout(function () { S.sig = ''; paintDyn(true); }, 15200 - (Date.now() - S.lastRunAt)); }
    el.innerHTML = banner(d) + kpis(d) + (showLog ? logBox(d) : '') + '<div class="la-lbl">Folders for ' + esc(d.date) + '</div><div class="la-runs">' +
      (d.slots || []).map(function (s) { return tile(s, any); }).join('') + '</div>' +
      '<div style="font-size:11px;color:var(--dim);margin-top:12px;line-height:1.6">Folder numbers follow the timings: folder 1 = the first time, folder 2 = the second … A retry fills the same folder. Files are saved as <span style="font-family:var(--mono)">&lt;date&gt; / 1 … 8 / files</span>.</div>';
    var nb = $('laLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }

  function paintTile(d) {
    var chip = $('laChip'), mini = $('laMini');
    if (!chip || !d || !d.slots) return;
    var c = d.counts || {}, total = d.slots.length, bad = (c.failed || 0) + (c.missed || 0) + (c.partial || 0);
    var run = d.slots.filter(function (s) { return s.state === 'running'; })[0];
    chip.textContent = run ? 'running' : bad ? 'attention' : !d.auto ? 'auto off' : 'idle';
    chip.style.color = run ? 'var(--blue)' : bad ? 'var(--red)' : !d.auto ? 'var(--amber)' : 'var(--dim)';
    mini.textContent = !d.trading_day ? 'Not a trading day' :
      run ? 'Folder ' + run.slot + ' running…' :
      (c.done || 0) + ' / ' + total + ' captured' + (bad ? ' · ' + bad + ' need attention' : '') + (d.next ? ' · next ' + d.next.time + ' (folder ' + d.next.slot + ')' : '') + (!d.auto ? ' · auto OFF' : '');
  }

  function load() {
    var next = 8000;
    return get('/live-alloc/status').then(function (d) {
      S.data = d;
      if (d && d.slots) { if (d.running) S.lastRunAt = Date.now(); paintTile(d); if (modalOpen()) paintDyn(); if (d.running) next = 2500; }
    }).catch(function () { next = 15000; }).then(function () { schedule(next); });
  }
  function schedule(ms) {
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (document.hidden) { schedule(ms); } else { load(); } }, ms);
  }
  function reload() { S.sig = ''; clearTimeout(S.timer); setTimeout(load, 600); }
  window.laRefresh = function () { S.sig = ''; load(); };

  // ───────────────────────── actions ─────────────────────────
  window.laRun = function (slot, force) {
    if (force && !window.confirm('Capture folder ' + slot + ' again? The files are downloaded again (the old ones are kept aside).')) return;
    post('/live-alloc/run', { slot: slot, force: !!force }).then(function (j) {
      if (j && j.ok) say('Folder ' + slot + ' started.'); else say((j && (j.error || j.detail)) || 'Could not start.', 'err');
      reload();
    });
  };
  window.laSkip = function (slot, undo) {
    var running = ((S.data || {}).slots || []).some(function (s) { return s.slot === slot && s.state === 'running'; });
    if (!undo && running && !window.confirm('Stop the capture that is running and skip folder ' + slot + '?')) return;
    post('/live-alloc/skip', { slot: slot, undo: !!undo }).then(function () {
      say(undo ? 'Folder ' + slot + ' is open again.' : 'Folder ' + slot + ' skipped - the next one runs on time.');
      reload();
    });
  };
  window.laStop = function () {
    if (!window.confirm('Stop the capture that is running now?')) return;
    post('/live-alloc/stop').then(function (j) { say(j && j.stopped ? 'Stopping…' : 'Nothing is running.'); reload(); });
  };
  window.laAuto = function () {
    var on = !(S.data && S.data.auto);
    ask(on ? 'Switch auto capture ON' : 'Switch auto capture OFF', function (pw) {
      post('/live-alloc/settings', { password: pw, auto: on }).then(function (j) {
        if (j && j.ok) { laOpen(); say(on ? 'Auto is ON - folders start by themselves.' : 'Auto is OFF - nothing starts by itself.'); }
        else { laOpen(); say((j && j.error) || 'Failed', 'err'); }
      });
    });
  };

  // ───────────────────────── Timings ─────────────────────────
  window.laTimings = function () {
    ask('Timings', function (pw) {
      get('/live-alloc/settings').then(function (s) {
        S.pw = pw;
        var times = (s.times || '').split(',').filter(Boolean);
        var h = '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">Folder 1 = the first time, folder 2 = the second … The timer reads these every few seconds, so a new time works at once - nothing to restart. ' +
          'To capture a folder again at its new time, press <b>Override → Mark not done</b> for it.</div>' +
          '<div id="laTimeRows" style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px">' +
          times.map(function (t, i) { return timeRow(i + 1, t); }).join('') + '</div>' +
          '<div style="display:flex;gap:8px;margin-bottom:14px"><button class="sa-tool" onclick="laTimeAdd()">＋ Add a time</button><button class="sa-tool" onclick="laTimeDel()">－ Remove last</button>' +
          '<button class="sa-tool" onclick="laTimeDefault()">↺ Back to ' + esc(s.default) + '</button></div>' +
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:14px"><input type="checkbox" id="laAutoChk" ' + (s.auto ? 'checked' : '') + '> Start each folder by itself at its time</label>' +
          '<div style="font-size:11.5px;color:var(--dim);margin-bottom:12px;line-height:1.6">A folder that missed its time is still started up to ' + esc(s.grace_min) + ' minutes late (never after the next timing begins). A failed capture is retried ' + esc(s.retries) + ' time(s), ' + esc(s.retry_gap_min) + ' minutes apart.</div>' +
          '<button class="sa-save" id="laTimeBtn" data-no-busy="1" style="width:100%" onclick="laTimeSave()">Save timings</button><div class="sa-msg" id="laTimeMsg"></div>';
        modal('⏱️ ' + TITLE + ' - timings', h);
      });
    });
  };
  function timeRow(n, t) { return '<div><div class="la-lbl">Folder ' + n + '</div><input type="time" class="la-in laTimeIn" value="' + esc(t) + '" style="width:100%"></div>'; }
  window.laTimeAdd = function () {
    var box = $('laTimeRows'); var n = box.querySelectorAll('.laTimeIn').length;
    if (n >= 12) return;
    box.insertAdjacentHTML('beforeend', timeRow(n + 1, ''));
  };
  window.laTimeDel = function () { var ins = $('laTimeRows').querySelectorAll('.laTimeIn'); if (ins.length > 1) ins[ins.length - 1].parentNode.remove(); };
  window.laTimeDefault = function () {
    get('/live-alloc/settings').then(function (s) {
      var t = (s.default || '').split(',').filter(Boolean);
      $('laTimeRows').innerHTML = t.map(function (x, i) { return timeRow(i + 1, x); }).join('');
    });
  };
  window.laTimeSave = function () {
    var vals = [].slice.call(document.querySelectorAll('.laTimeIn')).map(function (i) { return i.value; });
    var m = $('laTimeMsg');
    if (vals.some(function (v) { return !v; })) { if (m) m.innerHTML = '<span style="color:var(--red)">Fill every time (or remove the empty ones).</span>'; return; }
    var btn = $('laTimeBtn'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post('/live-alloc/settings', { password: S.pw, times: vals.join(','), auto: $('laAutoChk').checked }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = j && j.ok ? '✓ Saved' : 'Save timings'; }
      if (m) m.innerHTML = j && j.ok ? '<span style="color:var(--green)">Saved - the timer now uses: ' + esc(j.times) + '</span>' : '<span style="color:var(--red)">' + esc((j && j.error) || 'Failed') + '</span>';
      reload();
    });
  };

  // ───────────────────────── Override ─────────────────────────
  window.laOverride = function () {
    ask('Override', function (pw) { S.pw = pw; paintOverride(); });
  };
  function paintOverride(note) {
    get('/live-alloc/status').then(function (d) {
      S.data = d;
      var rows = (d.slots || []).map(function (s) {
        return '<div class="la-ovr"><b style="width:70px">Folder ' + s.slot + '</b><span style="font-family:var(--mono);width:48px">' + esc(s.time) + '</span>' +
          '<span style="flex:1;min-width:150px;color:var(--muted)">' + esc(plain(STATE_TXT[s.state] || s.state)) + ' · ' + s.files + '/' + s.total + ' files' + (s.override ? ' · override' : '') + (s.redo ? ' · will run again' : '') + '</span>' +
          '<button class="sa-tool" onclick="laOv(' + s.slot + ',\'undone\')" data-tip="Treat as NOT captured: the timer captures it again from scratch at its time (old files are kept aside).">↺ Mark not done</button>' +
          '<button class="sa-tool" onclick="laOv(' + s.slot + ',\'done\')" data-tip="Treat as captured (for example you put the files there yourself). The timer leaves it alone.">✓ Mark done</button>' +
          (s.state === 'skipped' ? '<button class="sa-tool" onclick="laSkipOv(' + s.slot + ',true)">↩ Undo skip</button>' : '<button class="sa-tool" onclick="laSkipOv(' + s.slot + ')">⏭ Skip</button>') + '</div>';
      }).join('');
      modal('🛠️ ' + TITLE + ' - override',
        '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:10px">Changed a time and want that folder captured again? Press <b>Mark not done</b> - it runs at its time (straight away if that time has already passed). Nothing is deleted: old files are moved aside.</div>' +
        (note ? '<div class="la-msg" style="color:' + (note.err ? 'var(--red)' : 'var(--green)') + '">' + esc(note.t) + '</div>' : '') + rows);
    });
  }
  window.laOv = function (slot, mode) {
    if (!window.confirm(mode === 'undone' ? 'Mark folder ' + slot + ' as NOT done? It will be captured again at its time.' : 'Mark folder ' + slot + ' as done without capturing?')) return;
    post('/live-alloc/override', { slot: slot, mode: mode, password: S.pw }).then(function (j) {
      paintOverride(j && j.ok ? { t: 'Folder ' + slot + (mode === 'undone' ? ' will be captured again at its time.' : ' marked done.') } : { t: (j && (j.error || j.detail)) || 'Failed', err: true });
      reload();
    });
  };
  window.laSkipOv = function (slot, undo) {
    post('/live-alloc/skip', { slot: slot, undo: !!undo }).then(function () { paintOverride({ t: undo ? 'Folder ' + slot + ' is open again.' : 'Folder ' + slot + ' skipped.' }); reload(); });
  };

  // ───────────────────────── Backup files (date range, full folder, one zip) ─────────────────────────
  function bkState() {
    if (!S.bk) { var t = (S.data && S.data.date) ? dmyToIso(S.data.date) : isoToday(); S.bk = { from: t, to: t, full: true, sel: {}, data: null, focus: 0 }; }
    return S.bk;
  }
  window.laBackup = function (focusSlot) {
    var b = bkState();
    b.full = true; b.sel = {};                                           // opens as "Full folder" every time
    if (focusSlot) { b.full = false; b.sel[focusSlot] = true; b.from = b.to = (S.data && S.data.date) ? dmyToIso(S.data.date) : isoToday(); }
    var n = Math.max(8, ((S.data || {}).slots || []).length);
    var chips = '';
    for (var i = 1; i <= n; i++) chips += '<button class="la-chip' + (b.sel[i] ? ' on' : '') + '" id="laBkChip' + i + '" onclick="laBkToggle(' + i + ')">Folder ' + i + '</button> ';
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">Pick one day or a range of days. The zip keeps the same layout as on disk: <span style="font-family:var(--mono)">date / 1 … 8 / files</span>.</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="la-lbl">From</div><input type="date" id="laBkFrom" class="la-in" style="width:100%" value="' + b.from + '" onchange="laBkDates()"></div>' +
      '<div><div class="la-lbl">To</div><input type="date" id="laBkTo" class="la-in" style="width:100%" value="' + b.to + '" onchange="laBkDates()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="la-chip" onclick="laBkQuick(0)">Today</button><button class="la-chip" onclick="laBkQuick(1)">Yesterday</button><button class="la-chip" onclick="laBkQuick(7)">Last 7 days</button><button class="la-chip" onclick="laBkQuick(30)">Last 30 days</button></div>' +
      '<label style="display:flex;gap:8px;align-items:center;font-size:13px;font-weight:650;margin-bottom:10px"><input type="checkbox" id="laBkFull" ' + (b.full ? 'checked' : '') + ' onchange="laBkFull()"> Full folder - every folder (1 … 8) of the whole day(s)</label>' +
      '<div id="laBkChips" style="display:' + (b.full ? 'none' : 'flex') + ';gap:6px;flex-wrap:wrap;margin-bottom:12px">' + chips + '</div>' +
      '<div id="laBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="laBkGo" data-no-busy="1" style="width:100%" onclick="laBkDownload()">⬇ Download zip</button><div class="sa-msg" id="laBkMsg"></div>');
    widen(true);
    laBkLoad();
  };
  window.laBkDates = function () { var b = bkState(); b.from = $('laBkFrom').value || b.from; b.to = $('laBkTo').value || b.from; if (b.to < b.from) { var x = b.from; b.from = b.to; b.to = x; } laBkLoad(); };
  window.laBkQuick = function (n) { var b = bkState(); b.to = isoToday(); b.from = n === 1 ? isoAgo(1) : isoAgo(n); if (n === 1) b.to = isoAgo(1); $('laBkFrom').value = b.from; $('laBkTo').value = b.to; laBkLoad(); };
  window.laBkFull = function () { var b = bkState(); b.full = $('laBkFull').checked; $('laBkChips').style.display = b.full ? 'none' : 'flex'; laBkPaint(); };
  window.laBkToggle = function (i) { var b = bkState(); b.sel[i] = !b.sel[i]; var c = $('laBkChip' + i); if (c) c.classList.toggle('on', !!b.sel[i]); laBkPaint(); };
  function selSlots() { var b = bkState(); return b.full ? null : Object.keys(b.sel).filter(function (k) { return b.sel[k]; }).map(Number); }
  window.laBkLoad = function () {
    var b = bkState();
    get('/live-alloc/range?from_date=' + encodeURIComponent(b.from) + '&to_date=' + encodeURIComponent(b.to)).then(function (j) { b.data = j; laBkPaint(); })
      .catch(function () { var l = $('laBkList'); if (l) l.innerHTML = '<div style="color:var(--red)">Could not read the folders.</div>'; });
  };
  function laBkPaint() {
    var b = bkState(), l = $('laBkList'); if (!l || !b.data) return;
    var want = selSlots(), tot = 0, nf = 0, days = 0;
    var html = (b.data.days || []).map(function (day) {
      var folders = (day.folders || []).filter(function (f) { return !want || want.indexOf(f.slot) >= 0; });
      var cnt = folders.reduce(function (a, f) { return a + f.files.length; }, 0);
      if (!cnt) return '';
      days++; tot += cnt; nf += folders.length;
      var body = folders.map(function (f) {
        return '<div style="margin:6px 0 2px;font-size:12px;font-weight:650">Folder ' + f.slot + ' <span style="font-weight:400;color:var(--dim)">· ' + f.files.length + ' file' + (f.files.length === 1 ? '' : 's') + '</span></div>' +
          f.files.map(function (x) {
            return '<div class="la-frow"><span class="n">' + esc(x.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(x.size) + ' · ' + esc((x.mtime || '').slice(11, 16)) + '</span>' +
              '<a href="' + API + '/live-alloc/file?date=' + encodeURIComponent(day.date) + '&slot=' + f.slot + '&name=' + encodeURIComponent(x.name) + '" download title="Download this file">⬇</a></div>';
          }).join('');
      }).join('');
      return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + ((b.data.days || []).length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day.date) +
        ' <span style="font-weight:400;color:var(--dim)">· ' + folders.length + ' folder' + (folders.length === 1 ? '' : 's') + ' · ' + cnt + ' files</span></summary>' + body + '</details>';
    }).join('');
    l.innerHTML = (tot ? '<div style="font-size:12px;color:var(--muted);margin-bottom:8px"><b style="color:var(--ink)">' + days + '</b> day' + (days === 1 ? '' : 's') + ' · <b style="color:var(--ink)">' + nf + '</b> folders · <b style="color:var(--ink)">' + tot + '</b> files will be in the zip</div>' + html
      : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No files for that ' + (b.from === b.to ? 'day' : 'range') + (want && want.length ? ' in the chosen folders' : '') + '.</div>');
    var go = $('laBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; go.textContent = tot ? '⬇ Download zip (' + tot + (tot === 1 ? ' file)' : ' files)') : '⬇ Download zip'; }
  }
  window.laBkDownload = function () {
    var b = bkState(), want = selSlots();
    if (want && !want.length) { var m = $('laBkMsg'); if (m) m.innerHTML = '<span style="color:var(--red)">Tick at least one folder, or tick Full folder.</span>'; return; }
    window.location.href = API + '/live-alloc/zip-range?from_date=' + encodeURIComponent(b.from) + '&to_date=' + encodeURIComponent(b.to) + (want ? '&slots=' + want.join(',') : '');
  };

  // ───────────────────────── Upload files (captured by hand) ─────────────────────────
  window.laUpload = function () {
    var d = S.data || { slots: [] };
    var first = (d.slots.filter(function (s) { return s.state !== 'done' && s.state !== 'upcoming'; })[0] || d.slots[0] || { slot: 1 }).slot;
    var opts = d.slots.map(function (s) { return '<option value="' + s.slot + '"' + (s.slot === first ? ' selected' : '') + '>Folder ' + s.slot + ' · ' + esc(s.time) + ' · ' + esc(plain(STATE_TXT[s.state] || s.state)) + ' · ' + s.files + '/' + s.total + ' files</option>'; }).join('');
    modal('⬆️ ' + TITLE + ' - upload files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.6;margin-bottom:12px">Captured a folder by hand today? Choose its number and add the files here. They are saved in that folder, appear in <b>Backup files</b>, and are copied to PostgreSQL. When all 5 files are there the folder turns green by itself.</div>' +
      '<div class="la-lbl">Folder (today)</div><select id="laUpSlot" class="la-in" style="width:100%;margin-bottom:12px">' + opts + '</select>' +
      '<div style="padding:10px 14px;border:1px dashed var(--line-2);border-radius:10px;font-size:11.5px;line-height:1.9;margin-bottom:12px"><b style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase">Accepted names</b><br>' +
      '<span style="font-family:var(--mono);color:var(--teal)">CM_ClientLevelCollaterals_*.csv<br>FO_ClientLevelCollaterals_*.csv<br>CO_ClientLevelCollaterals_*.csv<br>ClientNetEffectiveDeposits*.csv<br>MCX_Web_Allocation_&lt;number&gt;.xlsx</span></div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><button class="sa-save" data-no-busy="1" onclick="laUpPick()">📄 Choose files…</button>' +
      '<button class="sa-save" data-no-busy="1" style="background:var(--blue);color:var(--blue-d)" onclick="laUpPick(true)">📁 Choose a whole folder…</button></div>' +
      '<div style="font-size:11.5px;color:var(--dim);margin-top:6px">Whole folder: pick the folder you captured - every file in it that this capture makes is uploaded into the folder number chosen above; other files are ignored.</div>' +
      '<div id="laUpRes" style="margin-top:12px"></div>');
  };
  var ACCEPT = [/^CM_ClientLevelCollaterals_.*\.(csv|xlsx)$/i, /^FO_ClientLevelCollaterals_.*\.(csv|xlsx)$/i, /^CO_ClientLevelCollaterals_.*\.(csv|xlsx)$/i,
                /^ClientNetEffectiveDeposits.*\.(csv|xlsx)$/i, /^MCX_Web_Allocation_\d+\.xlsx$/i];
  function accepted(n) { return ACCEPT.some(function (r) { return r.test(n); }); }

  // a clear "done" popup after an upload: big green tick, one OK button (amber warning if something was not accepted)
  function donePopup(ok, title, lines) {
    var old = document.getElementById('laDone'); if (old) old.remove();
    var o = document.createElement('div'); o.id = 'laDone';
    o.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center';
    var col = ok ? '#34d399' : '#fbbf24';
    o.innerHTML = '<div style="background:var(--panel,#10151d);border:1px solid ' + col + ';border-radius:18px;padding:28px 34px;min-width:320px;max-width:460px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="width:72px;height:72px;border-radius:50%;margin:0 auto 14px;display:flex;align-items:center;justify-content:center;font-size:42px;font-weight:800;color:#06140d;background:' + col + '">' + (ok ? '\u2713' : '!') + '</div>' +
      '<div style="font-size:20px;font-weight:750;margin-bottom:8px;color:var(--ink,#e9eef6)">' + esc(title) + '</div>' +
      '<div style="font-size:13px;line-height:1.6;color:var(--muted,#93a0b4);margin-bottom:18px">' + (lines || []).map(esc).join('<br>') + '</div>' +
      '<button id="laDoneOk" style="min-width:120px;padding:10px 22px;border:0;border-radius:10px;font-weight:700;font-size:14px;cursor:pointer;background:' + col + ';color:#06140d">OK</button></div>';
    document.body.appendChild(o);
    var close = function () { o.remove(); document.removeEventListener('keydown', onKey); };
    var onKey = function (e) { if (e.key === 'Enter' || e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    document.getElementById('laDoneOk').onclick = close;
    document.getElementById('laDoneOk').focus();
  }

  window.laUpPick = function (isDir) {
    var old = $('laUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.multiple = true; inp.id = 'laUpInput'; inp.style.display = 'none';
    if (isDir) { inp.setAttribute('webkitdirectory', ''); inp.setAttribute('directory', ''); }
    inp.onchange = function () {
      var all = [].slice.call(inp.files), ok = all, skipped = 0;
      if (isDir) { ok = all.filter(function (f) { return accepted(f.name); }); skipped = all.length - ok.length; }
      inp.remove();
      if (!ok.length) { var r = $('laUpRes'); if (r) r.innerHTML = '<div style="color:var(--red)">No file this capture makes was found in that selection.</div>'; return; }
      laUpSend(ok, skipped);
    };
    document.body.appendChild(inp);
    inp.click();
  };
  function laUpSend(files, skipped) {
    var slot = ($('laUpSlot') || {}).value || '1', res = $('laUpRes');
    if (res) res.innerHTML = '<div style="color:var(--muted);font-size:12.5px"><span class="la-spin"></span>Uploading ' + files.length + ' file(s) into folder ' + esc(slot) + '…</div>';
    var fd = new FormData(); files.forEach(function (f) { fd.append('files', f); });
    fetch(API + '/live-alloc/upload?slot=' + encodeURIComponent(slot), { method: 'POST', body: fd })
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); })
      .then(function (j) {
        if (!res) return;
        if (j.error || j.detail) { res.innerHTML = '<div style="color:var(--red)">' + esc(j.error || j.detail) + '</div>'; return; }
        var nok = (j.ok || []).length, nbad = (j.rejected || []).length;
        var good = nok && !nbad;
        var h = '<div style="padding:12px 14px;border-radius:10px;margin-bottom:10px;border:1px solid ' + (good ? 'rgba(52,211,153,.5)' : 'rgba(251,191,36,.5)') + ';background:' + (good ? 'rgba(52,211,153,.10)' : 'rgba(251,191,36,.10)') + ';color:' + (good ? 'var(--green)' : 'var(--amber)') + ';font-size:13px;font-weight:650">' +
          (good ? '✓ Success - ' : '') + nok + ' file' + (nok === 1 ? '' : 's') + ' saved into folder ' + esc(j.slot) + (nbad ? ' · ' + nbad + ' not accepted' : '') + (skipped ? ' · ' + skipped + ' other file(s) in the folder ignored' : '') +
          (nok ? '<div style="font-weight:400;margin-top:4px">' + (j.complete ? 'All 5 files are there - folder ' + esc(j.slot) + ' is now green and copied to PostgreSQL.' : 'Still missing: ' + esc((j.missing || []).join(', ')) + '. Add them, or use Override → Mark done.') + '</div>' : '') + '</div>' +
          '<button class="sa-save" data-no-busy="1" style="width:100%;margin-bottom:12px" onclick="saCloseModal()">✓ Done</button>' +
          '<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Folder: <span style="font-family:var(--mono);color:var(--ink)">' + esc(j.folder) + '</span></div>';
        (j.ok || []).forEach(function (m) { h += '<div class="sa-ev ok"><span class="ic">✓</span><span>' + esc(m) + '</span><span class="what">saved</span></div>'; });
        (j.rejected || []).forEach(function (m) { h += '<div class="sa-ev bad"><span class="ic">✕</span><span>' + esc(m) + '</span><span class="what">not accepted</span></div>'; });
        res.innerHTML = h;
        donePopup(good, good ? 'Upload done' : (nok ? 'Uploaded with problems' : 'Nothing was uploaded'), [nok + ' file' + (nok === 1 ? '' : 's') + ' saved into ' + (typeof tlabel === 'function' ? tlabel(j.slot) : 'folder ' + j.slot)].concat(nbad ? [nbad + ' not accepted - see the list'] : []));
        var body = $('saModalBody'); if (body) body.scrollTop = body.scrollHeight;
        reload();
      }).catch(function (e) { if (res) res.innerHTML = '<div style="color:var(--red)">Upload failed: ' + esc(e.message || e) + '</div>'; });
  }

  // ───────────────────────── start ─────────────────────────
  function start() { if (build()) load(); else setTimeout(start, 600); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
