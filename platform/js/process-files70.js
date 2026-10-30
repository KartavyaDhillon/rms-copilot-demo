/* 76 FILES DOWNLOADER - a small card at the end of the Process Center's "Processing & Reports" row (same look as
   MCX CASH MAINTENANCE). "Open" shows the window: date (default = previous trading day), one tile per group of files
   (NSE CM / FO / CO / CD, MCX, NCDEX end file, NCDEX peaks, EPI) with found / total, Download missing, Credentials,
   Backup files (full day folder, chosen groups or chosen files -> one zip), Logs and Stop.
   Files go ONLY into <console data>\76 FILES DOWNLOADER\BACKUPFILES AUTOMATED\<date>\ - nothing is copied anywhere else.
   Backend: /files70/* (api.py) -> core/files70.py. Injected at serve time (see api.py /legacy). */
(function () {
  'use strict';
  if (window.__files70Injected) return;
  window.__files70Injected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = '76 FILES DOWNLOADER';
  var PIPE = 'files70';
  var S = { data: null, date: '', sig: '', timer: null, lastRunAt: 0, logTimer: null, bk: null, msgTimer: null };

  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function $(id) { return document.getElementById(id); }
  function kb(n) { n = Number(n || 0); return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }
  function get(path) { return fetch(API + path, { cache: 'no-store' }).then(function (r) { return r.json(); }); }
  function post(path, body) {
    return fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }
  function hideTip() { var t = $('globalTip'); if (t) { t.style.display = 'none'; t.classList.remove('show'); } }
  function modal(t, h) { hideTip(); if (typeof window.saShowModal === 'function') window.saShowModal(t, h); }
  function dmy(iso) { var p = String(iso || '').split('-'); return p.length === 3 ? p[2] + '-' + p[1] + '-' + p[0] : iso; }

  var css = document.createElement('style');
  css.textContent =
    '.f7-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.f7-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.f7-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.f7-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.f7-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.f7-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.f7-bar{height:5px;border-radius:9px;background:var(--line);margin-top:7px;overflow:hidden}.f7-bar i{display:block;height:100%;background:var(--green);transition:width .6s}' +
    '.f7-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;align-items:stretch}' +
    '@media(max-width:820px){.f7-kpis,.f7-grid{grid-template-columns:repeat(2,1fr)}}' +
    '.f7-g{background:var(--panel-2);border:1px solid var(--line);border-radius:13px;padding:12px 11px 11px;text-align:center;display:flex;flex-direction:column;gap:6px;min-width:0}' +
    '.f7-g.done{border-color:rgba(52,211,153,.4)}.f7-g.part{border-color:rgba(251,191,36,.5)}.f7-g.none{border-color:rgba(248,113,113,.45)}' +
    '.f7-g.running{border-color:var(--amber);box-shadow:0 0 0 1px rgba(251,191,36,.25)}' +
    '.f7-id{font-family:var(--display);font-size:15px;font-weight:750}' +
    '.f7-n{font-size:20px;font-weight:750;font-variant-numeric:tabular-nums}.f7-n small{font-size:12px;color:var(--dim);font-weight:600}' +
    '.f7-st{font-family:var(--mono);font-size:9.5px;letter-spacing:.8px;text-transform:uppercase;color:var(--dim)}' +
    '.f7-g.done .f7-st{color:var(--green)}.f7-g.part .f7-st{color:var(--amber)}.f7-g.none .f7-st{color:var(--red)}.f7-g.running .f7-st{color:var(--amber)}' +
    '.f7-miss{font-size:10.5px;color:var(--muted);line-height:1.4;overflow:hidden;word-break:break-all;font-family:var(--mono);flex:1;min-height:58px;max-height:58px;display:flex;align-items:center;justify-content:center}.f7-g>.f7-btn,.f7-g>.f7-link{margin-top:auto}.f7-g .f7-link{padding:8px 0;text-align:center}' +
    '.f7-btn{width:100%;font-family:var(--sans);font-weight:650;font-size:12px;border:0;border-radius:9px;padding:8px;cursor:pointer;background:var(--blue);color:var(--blue-d)}' +
    '.f7-btn:disabled{background:var(--panel-3);color:var(--dim);cursor:not-allowed}' +
    '.f7-btn.big{font-size:13.5px;padding:11px}.f7-btn.amber:disabled{background:var(--amber);color:var(--amber-d);cursor:default}' +
    '@keyframes f7Spin{to{transform:rotate(360deg)}}.f7-spin{display:inline-block;width:14px;height:14px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:f7Spin .8s linear infinite;vertical-align:-2px;margin-right:8px}' +
    '.f7-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:170px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.f7-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.f7-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.f7-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.f7-chip{padding:5px 12px;border-radius:20px;font-size:11.5px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '.f7-chip.on{border-color:rgba(59,130,246,.6);background:rgba(59,130,246,.14);color:var(--blue)}' +
    '.f7-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.f7-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.f7-frow a{color:var(--blue);text-decoration:none}' +
    '.f7-link{background:none;border:0;color:var(--muted);font-size:11px;cursor:pointer;text-decoration:underline;padding:0}';
  document.head.appendChild(css);

  // ───────────────────────── the small card ─────────────────────────
  function build() {
    if ($('task-files70new')) return true;
    var anchor = $('task-mcxcash') || $('task-liveallocation') || $('physdelSlot');
    var grid = anchor ? anchor.parentNode : null;
    if (!grid) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-files70new';
    tile.style.cssText = '--tile-accent:#f59e0b;cursor:pointer';
    tile.onclick = function () { f7Open(); };
    tile.innerHTML =
      '<div class="tile-icon">📁</div>' +
      '<div class="tile-name">76 FILES DOWNLOADER <span class="st" id="f7Chip">idle</span></div>' +
      '<div class="tile-desc">NSE CM / FO / CO / CD, MCX, NCDEX and EPI files of the previous trading day (or any date you pick) into one day folder. Captchas are solved automatically.</div>' +
      '<div class="tile-desc" id="f7Mini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#f59e0b;color:#1a0d00" onclick="event.stopPropagation();f7Open()" data-tip="Open 76 FILES DOWNLOADER: date, what is there, download what is missing, backup files, logs.">Open</button></div>';
    var after = $('task-mcxcash') || $('task-liveallocation');
    if (after && after.parentNode === grid) grid.insertBefore(tile, after.nextSibling); else grid.appendChild(tile);
    return true;
  }
  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('f7Dyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(940px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__f7) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__f7 = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  window.f7Sub = function (fn) { window._saReturn = f7Open; hideTip(); fn(); [60, 250, 700].forEach(function (ms) { setTimeout(hideTip, ms); }); };
  window.f7Creds = function () { f7Sub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.f7Logs = function () { f7Sub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function f7Open() {
    window._saReturn = null;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 14px">' +
        tb('🔑 Credentials', 'f7Creds()', 'NCDEX logins (NCFE and web extranet) and the security answer. NSE, MCX and the back office use Scheduler > Connections.') +
        tb('📦 Backup files', 'f7Sub(f7Backup)', 'Pick a date and download the whole day folder, some groups, or only the files you tick - one zip.') +
        tb('🗂️ Logs', 'f7Logs()', 'The full log of every run.') +
        tb('⛔ Stop', 'f7Stop()', 'Stops the download that is running now.', ';border-color:rgba(248,113,113,.45);color:var(--red)') +
      '</div><div id="f7Msg"></div><div id="f7Dyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    load();
  }
  window.f7Open = f7Open;

  function say(txt, kind) {
    var el = $('f7Msg');
    if (el) el.innerHTML = txt ? '<div class="f7-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('f7Msg'); if (e) e.innerHTML = ''; }, 8000);
  }

  // ───────────────────────── render ─────────────────────────
  function kpi(k, v, f, extra) { return '<div class="f7-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div>' + (extra || '') + '</div>'; }

  function groupTile(g, job) {
    var running = job.running && job.group === g.key;
    var cls = running ? 'running' : g.found >= g.total ? 'done' : g.found ? 'part' : 'none';
    var st = running ? '<span class="f7-spin" style="width:10px;height:10px;margin-right:5px"></span>downloading' : g.found >= g.total ? '✓ complete' : g.found ? '◐ part missing' : '✗ not downloaded';
    var miss = (g.files || []).filter(function (f) { return !f.present; }).map(function (f) { return f.name; });
    var note = miss.length ? 'missing: ' + miss.slice(0, 3).join(', ') + (miss.length > 3 ? ' +' + (miss.length - 3) + ' more' : '') : 'folder ' + g.folder;
    var btn = g.found >= g.total
      ? '<button class="f7-link" ' + (job.running ? 'disabled' : '') + ' onclick="f7Run([\'' + g.key + '\'])" title="Everything is there - nothing would be downloaded">✓ all there</button>'
      : '<button class="f7-btn" ' + (job.running ? 'disabled' : '') + ' onclick="f7Run([\'' + g.key + '\'])">⬇ Get missing (' + miss.length + ')</button>';
    return '<div class="f7-g ' + cls + '"><div class="f7-id">' + esc(g.label) + '</div><div class="f7-n">' + g.found + '<small> / ' + g.total + '</small></div>' +
      '<div class="f7-st">' + st + '</div><div class="f7-miss" title="' + esc(miss.join('\n')) + '">' + esc(note) + '</div>' + btn + '</div>';
  }

  function logBox(job) {
    var lines = job.lines || [];
    return '<div class="f7-lbl">Live log <span style="text-transform:none;letter-spacing:0">- latest lines (the full log of each run is under 🗂️ Logs)</span></div>' +
      '<div class="f7-logbox" id="f7LogBox">' + (lines.length ? lines.slice(-40).map(esc).join('\n') : 'Starting…') + '</div>';
  }

  function lastBox(d) {
    var r = d.last || {};
    if (!r.finished) return '';
    var ok = r.ok, miss = r.missing || [], errs = r.errors || {};
    var h = '<div class="f7-msg" style="margin-top:12px;color:' + (ok ? 'var(--green)' : 'var(--amber)') + '">' +
      '<b>Last run for ' + esc(d.date) + '</b> · finished ' + esc(String(r.finished).replace('T', ' ').slice(0, 16)) + ' · ' +
      (ok ? '✓ every file is there' : (miss.length + ' file(s) missing')) + '</div>';
    Object.keys(errs).forEach(function (g) { h += '<div style="font-size:11.5px;color:var(--red);margin:-4px 0 8px">✗ ' + esc(g) + ': ' + esc(errs[g]) + '</div>'; });
    return h;
  }

  function paint(force) {
    var d = S.data, el = $('f7Dyn');
    if (!el || !d || !d.groups) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var job = d.job || {};
    var old = $('f7LogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    var showLog = job.running || (Date.now() - S.lastRunAt < 20000);
    if (!job.running && showLog) { clearTimeout(S.logTimer); S.logTimer = setTimeout(function () { S.sig = ''; paint(true); }, 20200 - (Date.now() - S.lastRunAt)); }
    var other = job.running && job.date && job.date !== d.date;
    var missing = d.total - d.found;
    var runBtn = job.running
      ? '<button class="f7-btn big amber" disabled style="opacity:1"><span class="f7-spin"></span>Downloading ' + esc(job.date || '') + '…' + (job.msg ? ' ' + esc(job.msg) : '') + '</button>'
      : '<button class="f7-btn big" onclick="f7Run()">' + (missing ? '⬇ Download all missing files (' + missing + ')' : '↻ Check again - everything is there') + '</button>';
    el.innerHTML =
      '<div style="display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin:0 0 12px">' +
        '<div><div class="f7-lbl">Trade date</div><input type="date" id="f7Date" class="f7-in" value="' + esc(d.iso) + '" onchange="f7SetDate(this.value)"></div>' +
        (d.iso !== d.default_iso ? '<button class="f7-link" style="margin-bottom:9px" onclick="f7SetDate(\'' + esc(d.default_iso) + '\')">↺ back to the previous trading day (' + esc(dmy(d.default_iso)) + ')</button>'
          : '<span style="font-size:11.5px;color:var(--dim);margin-bottom:9px">previous trading day</span>') +
        '<div style="flex:1;min-width:240px">' + runBtn + '</div>' +
      '</div>' +
      (other ? '<div class="f7-msg" style="color:var(--amber)">A download for ' + esc(job.date) + ' is running - its progress shows when you pick that date.</div>' : '') +
      '<div class="f7-kpis">' +
        kpi('Files there', d.found + '<small> / ' + d.total + '</small>', missing ? missing + ' missing' : 'complete', '<div class="f7-bar"><i style="width:' + (d.total ? Math.round(100 * d.found / d.total) : 0) + '%"></i></div>') +
        kpi('Running now', job.running ? '<span class="f7-spin"></span>' : '—', job.running ? (job.date + (job.by ? ' · ' + job.by : '')) : 'nothing running') +
        kpi('EODSAR folder', d.eodsar.found + '<small> / ' + d.eodsar.total + '</small>', 'MCX & NCDEX EODSAR (CC02, MGN, MCX MARGIN)') +
        kpi('Day folder', d.exists ? '✓' : '—', d.folder) +
      '</div>' +
      (showLog ? logBox(job) : '') +
      '<div class="f7-lbl">Files of ' + esc(d.date) + '</div><div class="f7-grid">' +
      d.groups.map(function (g) { return groupTile(g, job); }).join('') + '</div>' +
      lastBox(d) +
      '<div style="font-size:11px;color:var(--dim);margin-top:12px;line-height:1.6">Files are saved only in <span style="font-family:var(--mono)">' + esc(d.folder) + '</span> - nothing is copied to other folders. ' +
      'Downloading again only fetches what is missing. Both NCDEX captchas are answered by the auto-solver. Logins: NSE, MCX and the back office (EPI) in Scheduler &gt; Connections; NCDEX under 🔑 Credentials.</div>';
    var nb = $('f7LogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }

  function paintTile(d) {
    var chip = $('f7Chip'), mini = $('f7Mini');
    if (!chip || !d || !d.groups) return;
    var job = d.job || {}, miss = d.total - d.found;
    chip.textContent = job.running ? 'running' : miss ? 'missing' : 'complete';
    chip.style.color = job.running ? 'var(--blue)' : miss ? 'var(--amber)' : 'var(--green)';
    mini.textContent = job.running ? 'Downloading ' + (job.date || '') + '…' : d.date + ': ' + d.found + ' / ' + d.total + ' files' + (miss ? ' · ' + miss + ' missing' : ' · complete');
  }

  function load() {
    var next = 15000;
    var q = S.date ? '?date=' + encodeURIComponent(S.date) : '';
    return get('/files70/status' + q).then(function (d) {
      if (d && d.groups) {
        S.data = d;
        if (!S.date) S.date = d.iso;
        if (d.job && d.job.running) { S.lastRunAt = Date.now(); next = 2500; }
        paintTile(d);
        if (modalOpen()) paint();
      } else if (d && d.error && modalOpen()) { $('f7Dyn').innerHTML = '<div style="color:var(--red)">' + esc(d.error) + '</div>'; }
    }).catch(function () { next = 20000; }).then(function () { schedule(next); });
  }
  function schedule(ms) {
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (document.hidden) { schedule(ms); } else { load(); } }, ms);
  }
  function reload() { S.sig = ''; clearTimeout(S.timer); setTimeout(load, 700); }

  // ───────────────────────── actions ─────────────────────────
  window.f7SetDate = function (iso) { if (!iso) return; S.date = iso; S.sig = ''; load(); };
  window.f7Run = function (groups) {
    var d = S.date || (S.data && S.data.iso);
    post('/files70/run', { date: d, groups: groups || [] }).then(function (j) {
      if (j && j.ok) { S.lastRunAt = Date.now(); say('Started - ' + (groups ? 'downloading the missing files of that group' : 'downloading every missing file') + ' for ' + dmy(d) + '.'); }
      else say((j && (j.error || j.detail)) || 'Could not start.', 'err');
      reload();
    });
  };
  window.f7Stop = function () {
    if (!window.confirm('Stop the download that is running now?')) return;
    post('/files70/stop').then(function (j) { say(j && j.stopped ? 'Stopping…' : 'Nothing is running.'); reload(); });
  };

  // ───────────────────────── Backup files ─────────────────────────
  function bk() {
    if (!S.bk) S.bk = { date: S.date || (S.data && S.data.iso) || '', mode: 'full', groups: {}, sel: {}, data: null, q: '' };
    return S.bk;
  }
  window.f7Backup = function () {
    var b = bk();
    b.date = S.date || b.date;
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">Pick a date, then what to put in the zip. The zip keeps the same layout as the day folder ' +
      '(<span style="font-family:var(--mono)">date / CM, FO, CO, CD, MCX, NCDEX, MCX &amp; NCDEX EODSAR, 1-8 / files</span>), so it can be pasted on the server as it is. Folders 1-8 are the LIVE ALLOCATION CAPTURE slots of that day; a folder with no files is shown as \u2717.</div>' +
      '<div style="display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin-bottom:12px"><div><div class="f7-lbl">Date</div><input type="date" id="f7BkDate" class="f7-in" value="' + esc(b.date) + '" onchange="f7BkDate(this.value)"></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
        '<button class="f7-chip' + (b.mode === 'full' ? ' on' : '') + '" id="f7BkM_full" onclick="f7BkMode(\'full\')">Full day folder</button>' +
        '<button class="f7-chip' + (b.mode === 'groups' ? ' on' : '') + '" id="f7BkM_groups" onclick="f7BkMode(\'groups\')">Choose groups</button>' +
        '<button class="f7-chip' + (b.mode === 'files' ? ' on' : '') + '" id="f7BkM_files" onclick="f7BkMode(\'files\')">Choose files</button></div></div>' +
      '<div id="f7BkBody" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="f7BkGo" data-no-busy="1" style="width:100%" onclick="f7BkDownload()">⬇ Download zip</button><div class="sa-msg" id="f7BkMsg"></div>');
    widen(true);
    f7BkLoad();
  };
  window.f7BkDate = function (v) { var b = bk(); if (!v) return; b.date = v; b.sel = {}; f7BkLoad(); };
  window.f7BkMode = function (m) {
    var b = bk(); b.mode = m;
    ['full', 'groups', 'files'].forEach(function (x) { var c = $('f7BkM_' + x); if (c) c.classList.toggle('on', x === m); });
    f7BkPaint();
  };
  window.f7BkLoad = function () {
    var b = bk();
    get('/files70/files?date=' + encodeURIComponent(b.date)).then(function (j) { b.data = j; f7BkPaint(); })
      .catch(function () { var l = $('f7BkBody'); if (l) l.innerHTML = '<div style="color:var(--red)">Could not read the folders.</div>'; });
  };
  var FOLDER_NAMES = { 'CM': 'NSE CM', 'FO': 'NSE FO', 'CO': 'NSE CO', 'CD': 'NSE CD', 'MCX': 'MCX', 'NCDEX': 'NCDEX', 'MCX & NCDEX EODSAR': 'MCX & NCDEX EODSAR', '(day folder)': 'Day folder (EPI.xlsx)' };
  function folderLabel(k) { return /^\d+$/.test(k) ? 'Live Allocation Capture \u00b7 folder ' + k : (FOLDER_NAMES[k] || k); }
  var ALL_FOLDERS = ['CM', 'FO', 'CO', 'CD', 'MCX', 'NCDEX', 'MCX & NCDEX EODSAR', '(day folder)', '1', '2', '3', '4', '5', '6', '7', '8'];
  var FOLDER_ORDER = ['CM', 'FO', 'CO', 'CD', 'MCX', 'NCDEX', 'MCX & NCDEX EODSAR', '(day folder)'];
  function folderKeys(obj) {
    return Object.keys(obj).sort(function (a, b) {
      var ia = FOLDER_ORDER.indexOf(a), ib = FOLDER_ORDER.indexOf(b);
      if (ia < 0 && /^\d+$/.test(a)) ia = 100 + Number(a);
      if (ib < 0 && /^\d+$/.test(b)) ib = 100 + Number(b);
      return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib) || (a < b ? -1 : 1);
    });
  }
  function picked() {
    var b = bk(), files = (b.data && b.data.files) || [];
    if (b.mode === 'groups') return files.filter(function (f) { return b.groups[f.group]; });
    if (b.mode === 'files') return files.filter(function (f) { return b.sel[f.rel]; });
    return files;
  }
  function f7BkPaint() {
    var b = bk(), l = $('f7BkBody'); if (!l || !b.data) return;
    var files = b.data.files || [];
    var html = '';
    if (!files.length) html = '<div style="color:var(--dim);font-size:13px;padding:8px 0">No files for ' + esc(dmy(b.date)) + '.</div>';
    else if (b.mode === 'groups') {
      html = '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:6px">' + (b.data.groups || []).map(function (g) {
        var n = files.filter(function (f) { return f.group === g.key; }).length;
        return '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;padding:6px 8px;border:1px solid var(--line);border-radius:9px;opacity:' + (n ? 1 : 0.45) + '">' +
          '<input type="checkbox" ' + (b.groups[g.key] ? 'checked' : '') + (n ? '' : ' disabled') + ' onchange="f7BkGroup(\'' + g.key + '\',this.checked)"> ' + esc(g.label) +
          ' <span style="margin-left:auto;color:var(--dim);font-size:11px">' + n + ' file' + (n === 1 ? '' : 's') + '</span></label>';
      }).join('') + '</div>';
    } else if (b.mode === 'files') {
      var q = (b.q || '').toLowerCase();
      var byFolder = {};
      files.forEach(function (f) {
        if (q && f.rel.toLowerCase().indexOf(q) < 0) return;
        var parts = f.rel.split('/'), folder = parts.length > 2 ? parts[1] : '(day folder)';
        (byFolder[folder] = byFolder[folder] || []).push(f);
      });
      html = '<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px"><input type="text" class="f7-in" style="flex:1" placeholder="Search a file name…" value="' + esc(b.q) + '" oninput="f7BkSearch(this.value)">' +
        '<button class="sa-tool" onclick="f7BkAll(true)">Select all shown</button><button class="sa-tool" onclick="f7BkAll(false)">Clear</button></div>' +
        '<div style="max-height:46vh;overflow:auto">' + folderKeys(byFolder).map(function (folder) {
          var fs = byFolder[folder], on = fs.filter(function (f) { return b.sel[f.rel]; }).length;
          return '<details style="border:1px solid var(--line);border-radius:10px;padding:6px 12px;margin-bottom:6px"' + (q || Object.keys(byFolder).length < 3 ? ' open' : '') + '>' +
            '<summary style="cursor:pointer;font-size:12.5px;font-weight:650">' + esc(folderLabel(folder)) + ' <span style="font-weight:400;color:var(--dim)">· ' + fs.length + ' files' + (on ? ' · ' + on + ' ticked' : '') + '</span>' +
            ' <button class="f7-link" onclick="event.preventDefault();f7BkFolder(\'' + esc(folder) + '\')">tick all</button></summary>' +
            fs.map(function (f) {
              var nm = f.rel.split('/').pop();
              return '<div class="f7-frow"><input type="checkbox" ' + (b.sel[f.rel] ? 'checked' : '') + ' onchange="f7BkTick(' + esc(JSON.stringify(f.rel)) + ',this.checked)">' +
                '<span class="n">' + esc(nm) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span>' +
                '<a href="' + API + '/files70/file?date=' + encodeURIComponent(b.date) + '&rel=' + encodeURIComponent(f.rel) + '" download title="Download only this file">⬇</a></div>';
            }).join('') + '</details>';
        }).join('') + '</div>';
    } else {
      var folders = {};
      files.forEach(function (f) { var p = f.rel.split('/'); var k = p.length > 2 ? p[1] : '(day folder)'; folders[k] = (folders[k] || 0) + 1; });
      html = '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:6px">' + ALL_FOLDERS.map(function (k) {
        var n = folders[k] || 0;
        return '<div style="display:flex;gap:8px;align-items:center;font-size:12.5px;padding:6px 10px;border:1px solid var(--line);border-radius:9px;opacity:' + (n ? 1 : 0.45) + '">' +
          '<span style="color:' + (n ? 'var(--green)' : 'var(--red)') + '">' + (n ? '\u2713' : '\u2717') + '</span><span>' + esc(folderLabel(k)) + '</span>' +
          '<span style="margin-left:auto;color:var(--dim);font-size:11px">' + n + ' file' + (n === 1 ? '' : 's') + '</span></div>';
      }).join('') + '</div>';
    }
    var p = picked(), size = p.reduce(function (a, f) { return a + (f.size || 0); }, 0);
    l.innerHTML = html + (files.length ? '<div style="font-size:12px;color:var(--muted);margin-top:10px"><b style="color:var(--ink)">' + p.length + '</b> file' + (p.length === 1 ? '' : 's') + ' · ' + kb(size) + ' will be in the zip</div>' : '');
    var go = $('f7BkGo'); if (go) { go.disabled = !p.length; go.style.opacity = p.length ? '1' : '.45'; go.textContent = p.length ? '⬇ Download zip (' + p.length + ' file' + (p.length === 1 ? '' : 's') + ')' : '⬇ Download zip'; }
  }
  window.f7BkGroup = function (k, on) { bk().groups[k] = on; f7BkPaint(); };
  window.f7BkTick = function (rel, on) { var b = bk(); if (on) b.sel[rel] = true; else delete b.sel[rel]; f7BkPaint(); };
  window.f7BkSearch = function (v) {
    var b = bk(); b.q = v; f7BkPaint();
    var i = document.querySelector('#f7BkBody input[type=text]'); if (i) { i.focus(); i.setSelectionRange(v.length, v.length); }
  };
  window.f7BkAll = function (on) {
    var b = bk(), q = (b.q || '').toLowerCase();
    ((b.data && b.data.files) || []).forEach(function (f) { if (!q || f.rel.toLowerCase().indexOf(q) >= 0) { if (on) b.sel[f.rel] = true; else delete b.sel[f.rel]; } });
    f7BkPaint();
  };
  window.f7BkFolder = function (folder) {
    var b = bk();
    ((b.data && b.data.files) || []).forEach(function (f) { var p = f.rel.split('/'); var k = p.length > 2 ? p[1] : '(day folder)'; if (k === folder) b.sel[f.rel] = true; });
    f7BkPaint();
  };
  window.f7BkDownload = function () {
    var b = bk(), p = picked(), m = $('f7BkMsg'), go = $('f7BkGo');
    if (!p.length) return;
    var body = { date: b.date, mode: b.mode };
    if (b.mode === 'groups') body.groups = Object.keys(b.groups).filter(function (k) { return b.groups[k]; });
    if (b.mode === 'files') body.files = p.map(function (f) { return f.rel; });
    if (go) { go.disabled = true; go.innerHTML = '<span class="f7-spin"></span>Making the zip…'; }
    fetch(API + '/files70/zip', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { if (!r.ok) throw new Error(r.status === 404 ? 'No files for that choice.' : 'Server answered ' + r.status); return r.blob(); })
      .then(function (blob) {
        var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = '76_FILES_' + dmy(b.date) + '.zip';
        document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
        if (m) m.innerHTML = '<span style="color:var(--green)">✓ Zip downloaded (' + p.length + ' files).</span>';
      })
      .catch(function (e) { if (m) m.innerHTML = '<span style="color:var(--red)">' + esc(e.message || e) + '</span>'; })
      .then(function () { f7BkPaint(); });
  };

  // ───────────────────────── start ─────────────────────────
  function start() { if (build()) load(); else setTimeout(start, 700); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
