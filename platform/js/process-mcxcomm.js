/* MCX COMM LIMIT - a small card at the END of the Process Center's "Processing & Reports" row (after MARGIN SHORTAGE).
   "Open" shows the window: single downloads (TCS search result, mail file, KORP file, NMASS files), Credentials, Email To / CC,
   Upload files, Backup files, Logs, Run, Auto on/off, Stop, Settings - plus the KPI cards, the step tiles and the live log.
   The job starts BY ITSELF 10 s after Run is clicked on PROBABLE (once a day); Run here is the manual start.
   Backend: /mcx-comm/* (api.py, core/mcx_comm_limit.py) and the shared /px/* windows (Credentials, Email, Logs) with pipe=mcxcomm.
   Injected at serve time (see api.py /legacy); dashboard.html is not modified. */
(function () {
  'use strict';
  if (window.__mcxCommInjected) return;
  window.__mcxCommInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'MCX COMM LIMIT';
  var PIPE = 'mcxcomm';
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
  function isoToday() { var d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function isoAgo(n) { var d = new Date(Date.now() - n * 864e5); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }

  var css = document.createElement('style');
  css.textContent =
    '.mq-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.mq-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.mq-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.mq-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.mq-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.mq-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.mq-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}' +
    '@media(max-width:820px){.mq-kpis,.mq-steps{grid-template-columns:repeat(2,1fr)}}' +
    '.mq-step{border:1px solid var(--line);border-radius:11px;padding:9px 11px;background:var(--panel-2);min-width:0}' +
    '.mq-step.ok{border-color:rgba(52,211,153,.4)}.mq-step.failed{border-color:rgba(248,113,113,.55)}.mq-step.running{border-color:var(--amber)}' +
    '.mq-step .n{font-size:12px;font-weight:650}.mq-step .s{font-family:var(--mono);font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);margin-top:2px}' +
    '.mq-step.ok .s{color:var(--green)}.mq-step.failed .s{color:var(--red)}.mq-step.running .s{color:var(--amber)}' +
    '.mq-step .m{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.35;overflow:hidden;max-height:42px}' +
    '.mq-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.mq-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:170px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.mq-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.mq-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.mq-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.mq-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.mq-frow a{color:var(--blue);text-decoration:none}.mq-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.mq-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '@keyframes mqSpin{to{transform:rotate(360deg)}}.mq-spin{display:inline-block;width:12px;height:12px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:mqSpin .8s linear infinite;vertical-align:-2px;margin-right:6px}';
  document.head.appendChild(css);

  // ───────────────────────── the small card (last tile of "Processing & Reports") ─────────────────────────
  function grid() {
    var t = $('task-ms15card') || $('task-mcxcash') || $('task-liveallocation');
    if (t && t.parentNode) return t.parentNode;
    var slot = $('physdelSlot');
    return slot ? slot.parentNode : null;
  }
  function build() {
    if ($('task-mcxcomm')) return true;
    var g = grid(); if (!g) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-mcxcomm';
    tile.style.cssText = '--tile-accent:#22d3ee;cursor:pointer';
    tile.onclick = function () { mqOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">📜</div>' +
      '<div class="tile-name">MCX COMM LIMIT <span class="st" id="mqChip">idle</span></div>' +
      '<div class="tile-desc">Builds the daily Kambala commodity-limit file, e-mails it and uploads it to Kambala. Starts by itself 10 seconds after PROBABLE runs.</div>' +
      '<div class="tile-desc" id="mqMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#22d3ee;color:#04222a" onclick="event.stopPropagation();mqOpen()" data-tip="Open MCX COMM LIMIT: downloads, run, auto switch, credentials, e-mail To / CC, upload and backup files, logs.">Open</button></div>';
    g.appendChild(tile);
    return true;
  }
  function keepLast() {      // the last tiles of the row, in this order: MARGIN SHORTAGE, MCX COMM LIMIT, NCDEX COMM LIMIT
    var els = ['task-ms15card', 'task-mcxcomm', 'task-ncdexcomm'].map(function (i) { return document.getElementById(i); }).filter(Boolean);
    if (!els.length || !els[0].parentNode) return;
    var p = els[0].parentNode; els = els.filter(function (e) { return e.parentNode === p; });
    var kids = [].slice.call(p.children), tail = kids.slice(kids.length - els.length);
    if (!els.every(function (e, i) { return tail[i] === e; })) els.forEach(function (e) { p.appendChild(e); });
  }

  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('mqDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(940px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__mq) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__mq = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  var RED = ';border-color:rgba(248,113,113,.45);color:var(--red)';
  window.mqSub = function (fn) { window._saReturn = mqOpen; fn(); };
  window.mqCreds = function () { mqSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.mqEmail = function () { mqSub(function () { if (typeof window.pxConfig === 'function') window.pxConfig(PIPE); }); };
  window.mqLogs = function () { mqSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function mqOpen() {
    window._saReturn = null;
    var auto = S.data ? S.data.auto : true;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 8px">' +
        tb('⬇ TCS search result', "mqFetch('tcs')", 'Downloads the TCS BANCS search result (RSK136 - SearchResults) into the folder. Skipped when today\'s file is already there.') +
        tb('✉ Mail file', "mqFetch('mail')", 'Downloads MCX_ACTIVE_DETAILS from the "MCX ACTIVE CLIENT DETAILS" mail (today\'s mail if it has arrived, else the latest earlier one).') +
        tb('⬇ KORP file', "mqFetch('korp')", 'Downloads the CASH_MARGIN custom report from the back-office API.') +
        tb('⬇ NMASS files', "mqFetch('nmass')", 'Downloads the CO, FO and CM client-level collateral files from NMASS (same download as Margin Shortage).') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 8px">' +
        tb('🔑 Credentials', 'mqCreds()', 'Mailbox, MCX eclear, NMASS, back-office and FTP logins.') +
        tb('✉️ Email To / CC', 'mqEmail()', 'Who gets the MCX COMMODITY LIMIT FILE mail.') +
        tb('⬆️ Upload files', 'mqSub(mqUpload)', 'Add a file you got by hand (or EXCLUSION.xlsx) to the folder.') +
        tb('📦 Backup files', 'mqSub(mqBackup)', 'Pick a date or a range and download the backed-up files as one zip.') +
        tb('🗂️ Logs', 'mqLogs()', 'The full log of every run.') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 14px">' +
        tb('▶ Run', 'mqRun()', 'Runs the whole job now. If today\'s run already succeeded it asks for the password; after a failed run it goes straight through.', ';background:var(--blue);color:var(--blue-d);border-color:transparent') +
        tb(auto ? '⏸ Auto: ON' : '▶ Auto: OFF', 'mqAuto()', 'ON: starts by itself 10 s after PROBABLE runs (once a day). OFF: only the Run button starts it. Asks for the password.', auto ? '' : RED) +
        tb('⚙ Settings', 'mqSub(mqSettings)', 'Which day\'s mail to take, start delay, Kambala FTP on/off, WhatsApp alert numbers.') +
        tb('⛔ Stop', 'mqStop()', 'Stops the run or download that is going right now.', RED) +
      '</div><div id="mqMsg"></div><div id="mqDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.mqOpen = mqOpen;

  function say(txt, kind) {
    var el = $('mqMsg');
    if (el) el.innerHTML = txt ? '<div class="mq-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('mqMsg'); if (e) e.innerHTML = ''; }, 8000);
  }

  // ───────────────────────── render ─────────────────────────
  var ST = { idle: 'Not run yet today', running: 'Running…', done: '✓ Done', failed: '✗ Failed', stopped: 'Stopped' };
  function kpi(k, v, f) { return '<div class="mq-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div></div>'; }
  function banner(d) {
    if (d.status === 'failed') return '<div class="mq-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>Today\'s run FAILED.</b> ' + esc(d.error || '') + ' <span style="color:var(--dim)">· ' + (d.alert_sent ? 'WhatsApp alert sent' : 'alert not sent' + (d.alert_reason ? ' (' + esc(d.alert_reason) + ')' : '')) + ' · press <b>Run</b> to try again (no password needed).</span></div>';
    if (d.status === 'done') return '<div class="mq-ban" style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:var(--green)"><b>✓ Done today</b> - ' + esc(d.rows) + ' clients written · mail sent' + (d.uploaded ? ' · uploaded to Kambala' : ' · <span style="color:var(--amber)">NOT uploaded to Kambala</span>') + ' · Run again asks for the password.</div>';
    if (!d.auto) return '<div class="mq-ban" style="background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.45);color:var(--amber)"><b>Auto is OFF</b> - it will not start with PROBABLE. Use Run.</div>';
    return '<div class="mq-ban" style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.35);color:var(--blue)">' + (d.auto_tried ? 'The automatic start has already happened today.' : '● Auto is ON - starts ' + esc((d.settings || {}).delay_s) + ' s after Run is clicked on PROBABLE (once a day).') + '</div>';
  }
  function kpis(d) {
    var ins = d.inputs || [], ok = ins.filter(function (x) { return x.ok; }).length;
    var run = d.running ? 'Running' : (ST[d.status] || d.status);
    return '<div class="mq-kpis">' +
      kpi('Today', '<span style="color:' + (d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--ink)') + '">' + run + '</span>', (d.started ? 'started ' + d.started.slice(11, 16) : 'not started') + (d.trigger ? ' · ' + d.trigger : '') + (d.by ? ' · ' + d.by : '')) +
      kpi('Input files', ok + '<small> / ' + ins.length + '</small>', ok === ins.length ? 'all of today\'s files are in the folder' : 'missing: ' + ins.filter(function (x) { return !x.ok; }).map(function (x) { return x.label; }).join(', ')) +
      kpi('Clients written', d.rows == null ? '—' : d.rows, d.eligible == null ? 'no calculation yet' : d.eligible + ' passed the 25 lakh test') +
      kpi('Mail · Kambala', (d.mailed ? '✓' : '—') + ' · ' + (d.uploaded ? '✓' : '—'), 'e-mail sent · FTP upload') + '</div>';
  }
  function steps(d) {
    return '<div class="mq-lbl">Steps</div><div class="mq-steps">' + (d.steps || []).map(function (s) {
      var c = s.status === 'ok' || s.status === 'skipped' ? 'ok' : s.status === 'failed' ? 'failed' : s.status === 'running' ? 'running' : '';
      var t = { ok: '✓ done', skipped: '✓ already there', failed: '✗ failed', running: 'running…', stopped: 'stopped' }[s.status] || '—';
      return '<div class="mq-step ' + c + '"><div class="n">' + esc(s.label) + '</div><div class="s">' + t + '</div><div class="m" title="' + esc(s.msg) + '">' + esc(s.msg) + '</div></div>';
    }).join('') + '</div>';
  }
  function inputs(d) {
    return '<div class="mq-lbl">Files in the folder</div><div style="margin-bottom:12px">' + (d.inputs || []).map(function (x) {
      return '<div class="mq-frow"><span style="width:18px;color:' + (x.ok ? 'var(--green)' : 'var(--red)') + '">' + (x.ok ? '✓' : '✗') + '</span><span style="width:170px;color:var(--muted)">' + esc(x.label) + '</span><span class="n">' + (x.found ? esc(x.name) : '— not there') + '</span>' +
        '<span style="color:var(--dim);font-size:11px">' + (x.found ? kb(x.size) + ' · ' + esc(x.time) + (x.ok ? '' : ' · ' + esc(x.why)) : '') + '</span></div>';
    }).join('') + '</div>';
  }
  function logBox(d) {
    var lines = d.log || [];
    return '<div class="mq-lbl">Live log</div><div class="mq-logbox" id="mqLogBox">' + (lines.length ? lines.slice(-30).map(esc).join('\n') : 'No activity since the server started. The full log of every run is under 🗂️ Logs.') + '</div>';
  }
  function paintDyn(force) {
    var d = S.data, el = $('mqDyn');
    if (!el || !d || !d.steps) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('mqLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    el.innerHTML = banner(d) + kpis(d) + steps(d) + logBox(d) + inputs(d) +
      '<div style="font-size:11px;color:var(--dim);line-height:1.6">Folder: <span style="font-family:var(--mono)">' + esc(d.folder) + '</span> · backups in <span style="font-family:var(--mono)">BACKUPFILES AUTOMATED / dd-mm-yyyy</span>. ' +
      'The risk report is read straight from COMMON FILES (never copied or backed up here). Files already in the folder are never downloaded again. The TCS search result is normally saved by PROBABLE\'s TCS download; this card downloads it itself only if it is missing.</div>';
    var nb = $('mqLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }
  function paintTile(d) {
    var chip = $('mqChip'), mini = $('mqMini');
    if (!chip || !d || !d.steps) return;
    chip.textContent = d.running ? 'running' : d.status === 'failed' ? 'failed' : d.status === 'done' ? 'done' : !d.auto ? 'auto off' : 'idle';
    chip.style.color = d.running ? 'var(--blue)' : d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : !d.auto ? 'var(--amber)' : 'var(--dim)';
    var ok = (d.inputs || []).filter(function (x) { return x.ok; }).length;
    mini.textContent = d.running ? 'Running…' : d.status === 'done' ? 'Done · ' + d.rows + ' clients' + (d.uploaded ? ' · uploaded' : '') : d.status === 'failed' ? 'Failed - open for details' : ok + ' / ' + (d.inputs || []).length + ' files in' + (d.auto ? ' · waits for PROBABLE' : ' · auto OFF');
  }
  function load() {
    var next = 8000;
    return get('/mcx-comm/status').then(function (d) {
      S.data = d;
      if (d && d.steps) { paintTile(d); if (modalOpen()) paintDyn(); if (d.running || d.fetching) next = 2500; }
    }).catch(function () { next = 15000; }).then(function () { schedule(next); });
  }
  function schedule(ms) {
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (document.hidden) { schedule(ms); } else { load(); } }, ms);
  }
  function reload() { S.sig = ''; clearTimeout(S.timer); setTimeout(load, 700); }

  // ───────────────────────── actions ─────────────────────────
  window.mqFetch = function (what) {
    post('/mcx-comm/fetch', { what: what }).then(function (j) {
      if (j && j.ok) say('Download started - watch the live log.'); else say((j && (j.error || j.detail)) || 'Could not start.', 'err');
      reload();
    });
  };
  function startRun(pw) {
    post('/mcx-comm/run', pw ? { password: pw } : {}).then(function (j) {
      if (j && j.need_password) { ask('Run again (today\'s run already succeeded)', function (p) { startRun(p); }); return; }
      if (j && j.ok) say('Started - watch the steps and the live log.'); else say((j && (j.error || j.msg || j.detail)) || 'Could not start.', 'err');
      reload();
    });
  }
  window.mqRun = function () { startRun(''); };
  window.mqStop = function () {
    if (!window.confirm('Stop the run / download that is going now?')) return;
    post('/mcx-comm/stop').then(function (j) { say(j && j.stopped ? 'Stopping…' : 'Nothing is running.'); reload(); });
  };
  window.mqAuto = function () {
    var on = !(S.data && S.data.auto);
    ask(on ? 'Switch auto start ON' : 'Switch auto start OFF', function (pw) {
      post('/mcx-comm/settings', { password: pw, auto: on }).then(function (j) {
        mqOpen();
        if (j && j.ok) say(on ? 'Auto is ON - starts 10 s after PROBABLE runs.' : 'Auto is OFF - only the Run button starts it.'); else say((j && j.error) || 'Failed', 'err');
        reload();
      });
    });
  };

  // ───────────────────────── Settings ─────────────────────────
  window.mqSettings = function () {
    ask('Settings', function (pw) {
      get('/mcx-comm/settings').then(function (s) {
        S.pw = pw;
        modal('⚙ ' + TITLE + ' - settings',
          '<div class="mq-lbl">Which day\'s MCX ACTIVE mail</div>' +
          '<select id="mqMailDay" class="mq-in" style="width:100%;margin-bottom:12px"><option value="auto"' + (s.mail_day === 'auto' ? ' selected' : '') + '>Automatic - today\'s mail if it has arrived, else the latest earlier one</option>' +
          '<option value="today"' + (s.mail_day === 'today' ? ' selected' : '') + '>Today\'s mail only</option><option value="yesterday"' + (s.mail_day === 'yesterday' ? ' selected' : '') + '>Yesterday\'s mail only</option></select>' +
          '<div class="mq-lbl">Start delay after PROBABLE Run (seconds)</div><input type="number" id="mqDelay" min="0" max="600" class="mq-in" style="width:100%;margin-bottom:12px" value="' + esc(s.delay_s) + '">' +
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:12px"><input type="checkbox" id="mqFtp" ' + (s.ftp_enabled ? 'checked' : '') + '> Upload the finished file to Kambala FTP</label>' +
          '<div class="mq-lbl">WhatsApp failure-alert numbers (blank = the Margin Shortage / common alert numbers)</div><input type="text" id="mqAlert" class="mq-in" style="width:100%;margin-bottom:14px" placeholder="9876543210, 9123456789" value="' + esc(s.alert_numbers || '') + '">' +
          '<button class="sa-save" id="mqSetBtn" data-no-busy="1" style="width:100%" onclick="mqSetSave()">Save settings</button><div class="sa-msg" id="mqSetMsg"></div>');
      });
    });
  };
  window.mqSetSave = function () {
    var btn = $('mqSetBtn'), m = $('mqSetMsg'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post('/mcx-comm/settings', { password: S.pw, mail_day: $('mqMailDay').value, delay_s: parseInt($('mqDelay').value || '10', 10), ftp_enabled: $('mqFtp').checked, alert_numbers: $('mqAlert').value }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = j && j.ok ? '✓ Saved' : 'Save settings'; }
      if (m) m.innerHTML = j && j.ok ? '<span style="color:var(--green)">Saved.</span>' : '<span style="color:var(--red)">' + esc((j && j.error) || 'Failed') + '</span>';
      reload();
    });
  };

  // ───────────────────────── Backup files ─────────────────────────
  window.mqBackup = function () {
    var t = isoToday();
    S.bk = { from: isoAgo(0), to: t, data: null };
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">After every successful run the day\'s input files and the finished limit file are kept in <span style="font-family:var(--mono)">BACKUPFILES AUTOMATED / dd-mm-yyyy</span>. Pick one day or a range.</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="mq-lbl">From</div><input type="date" id="mqBkFrom" class="mq-in" style="width:100%" value="' + S.bk.from + '" onchange="mqBkLoad()"></div>' +
      '<div><div class="mq-lbl">To</div><input type="date" id="mqBkTo" class="mq-in" style="width:100%" value="' + S.bk.to + '" onchange="mqBkLoad()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="mq-chip" onclick="mqBkQuick(0)">Today</button><button class="mq-chip" onclick="mqBkQuick(1)">Yesterday</button><button class="mq-chip" onclick="mqBkQuick(7)">Last 7 days</button><button class="mq-chip" onclick="mqBkQuick(30)">Last 30 days</button></div>' +
      '<div id="mqBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="mqBkGo" data-no-busy="1" style="width:100%" onclick="mqBkDownload()">⬇ Download zip</button>');
    widen(true);
    mqBkLoad();
  };
  window.mqBkQuick = function (n) { $('mqBkTo').value = n === 1 ? isoAgo(1) : isoToday(); $('mqBkFrom').value = isoAgo(n); mqBkLoad(); };
  window.mqBkLoad = function () {
    var a = $('mqBkFrom').value, b = $('mqBkTo').value || a; if (b < a) { var x = a; a = b; b = x; }
    S.bk.from = a; S.bk.to = b;
    get('/mcx-comm/range?from_date=' + encodeURIComponent(a) + '&to_date=' + encodeURIComponent(b)).then(function (j) {
      var l = $('mqBkList'); if (!l) return;
      var byDay = {}; (j.files || []).forEach(function (f) { (byDay[f.date] = byDay[f.date] || []).push(f); });
      var days = Object.keys(byDay), tot = (j.files || []).length;
      l.innerHTML = tot ? days.map(function (day) {
        return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + (days.length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day) + ' <span style="font-weight:400;color:var(--dim)">· ' + byDay[day].length + ' files</span></summary>' +
          byDay[day].map(function (f) { return '<div class="mq-frow"><span class="n">' + esc(f.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span><a href="' + API + '/mcx-comm/file?date=' + encodeURIComponent(day) + '&name=' + encodeURIComponent(f.name) + '" download>⬇</a></div>'; }).join('') + '</details>';
      }).join('') : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No backed-up files for that ' + (a === b ? 'day' : 'range') + '.</div>';
      var go = $('mqBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; }
    });
  };
  window.mqBkDownload = function () { window.location.href = API + '/mcx-comm/zip-range?from_date=' + encodeURIComponent(S.bk.from) + '&to_date=' + encodeURIComponent(S.bk.to); };

  // ───────────────────────── Upload files ─────────────────────────
  window.mqUpload = function () {
    modal('⬆️ ' + TITLE + ' - upload files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.6;margin-bottom:12px">Got a file by hand? Add it here - it goes into the working folder under its own name and the next run uses it instead of downloading. EXCLUSION.xlsx here replaces the permanent exclusion list.</div>' +
      '<div style="padding:10px 14px;border:1px dashed var(--line-2);border-radius:10px;font-size:11.5px;line-height:1.9;margin-bottom:12px"><b style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase">Files used</b><br><span style="font-family:var(--mono);color:var(--teal)">MCX_ACTIVE_DETAILS_&lt;ddmmyyyy&gt;.csv · CASH_MARGIN*.csv<br>SearchResults*.xlsx · CO / FO / CM_ClientLevelCollaterals_*.csv<br>Kambala_OMNY_COM_DEPO_ADHOC_&lt;ddmmyyyy&gt;.TXT · EXCLUSION.xlsx</span><br><span style="color:var(--dim)">The RISK_REPORT is not uploaded here - it is read straight from COMMON FILES.</span></div>' +
      '<button class="sa-save" data-no-busy="1" style="width:100%" onclick="mqUpPick()">📄 Choose files…</button><div id="mqUpRes" style="margin-top:12px"></div>');
  };
  window.mqUpPick = function () {
    var old = $('mqUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.multiple = true; inp.id = 'mqUpInput'; inp.style.display = 'none';
    inp.onchange = function () { var fl = [].slice.call(inp.files); inp.remove(); if (fl.length) mqUpSend(fl); };
    document.body.appendChild(inp); inp.click();
  };
  function mqUpSend(files) {
    var res = $('mqUpRes'); if (res) res.innerHTML = '<div style="color:var(--muted);font-size:12.5px"><span class="mq-spin"></span>Uploading ' + files.length + ' file(s)…</div>';
    var fd = new FormData(); files.forEach(function (f) { fd.append('files', f); });
    fetch(API + '/mcx-comm/upload', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (j) {
      if (!res) return;
      if (j.error || j.detail) { res.innerHTML = '<div style="color:var(--red)">' + esc(j.error || j.detail) + '</div>'; return; }
      var h = '<div class="mq-msg" style="color:' + ((j.rejected || []).length ? 'var(--amber)' : 'var(--green)') + '">' + (j.ok || []).length + ' file(s) saved' + ((j.rejected || []).length ? ' · ' + j.rejected.length + ' not accepted' : '') + '</div>';
      (j.ok || []).forEach(function (m) { h += '<div class="mq-frow"><span style="color:var(--green)">✓</span><span class="n">' + esc(m) + '</span></div>'; });
      (j.rejected || []).forEach(function (m) { h += '<div class="mq-frow"><span style="color:var(--red)">✕</span><span class="n">' + esc(m) + '</span></div>'; });
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
