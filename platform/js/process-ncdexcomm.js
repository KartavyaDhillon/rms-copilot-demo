/* NCDEX COMM LIMIT - a small card at the END of the Process Center's "Processing & Reports" row (after MCX COMM LIMIT).
   Builds NCXDEX_DEPOSIT_FILE.txt (core/ncdex_comm_limit.py). Same window and rules as MCX COMM LIMIT.
   "Open" shows the window: single downloads (ClientNet deposits from NCDEX, 3-month active mail file), Credentials, Email To / CC,
   Upload files, Backup files, Logs, Run, Auto on/off, Stop, Settings - plus the KPI cards, the step tiles and the live log.
   The job starts BY ITSELF 10 s after Run is clicked on PROBABLE (once a day); Run here is the manual start.
   Backend: /ncdex-comm/* (api.py, core/ncdex_comm_limit.py) and the shared /px/* windows (Credentials, Email, Logs) with pipe=ncdexcomm.
   Injected at serve time (see api.py /legacy); dashboard.html is not modified. */
(function () {
  'use strict';
  if (window.__ncdexCommInjected) return;
  window.__ncdexCommInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'NCDEX COMM LIMIT';
  var PIPE = 'ncdexcomm';
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
    '.nq-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.nq-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.nq-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.nq-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.nq-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.nq-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.nq-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}' +
    '@media(max-width:820px){.nq-kpis,.nq-steps{grid-template-columns:repeat(2,1fr)}}' +
    '.nq-step{border:1px solid var(--line);border-radius:11px;padding:9px 11px;background:var(--panel-2);min-width:0}' +
    '.nq-step.ok{border-color:rgba(52,211,153,.4)}.nq-step.failed{border-color:rgba(248,113,113,.55)}.nq-step.running{border-color:var(--amber)}' +
    '.nq-step .n{font-size:12px;font-weight:650}.nq-step .s{font-family:var(--mono);font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);margin-top:2px}' +
    '.nq-step.ok .s{color:var(--green)}.nq-step.failed .s{color:var(--red)}.nq-step.running .s{color:var(--amber)}' +
    '.nq-step .m{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.35;overflow:hidden;max-height:42px}' +
    '.nq-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.nq-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:170px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.nq-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.nq-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.nq-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.nq-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.nq-frow a{color:var(--blue);text-decoration:none}.nq-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.nq-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '@keyframes nqSpin{to{transform:rotate(360deg)}}.nq-spin{display:inline-block;width:12px;height:12px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:nqSpin .8s linear infinite;vertical-align:-2px;margin-right:6px}';
  document.head.appendChild(css);

  // ───────────────────────── the small card (last tile of "Processing & Reports") ─────────────────────────
  function grid() {
    var t = $('task-ms15card') || $('task-mcxcash') || $('task-liveallocation');
    if (t && t.parentNode) return t.parentNode;
    var slot = $('physdelSlot');
    return slot ? slot.parentNode : null;
  }
  function build() {
    if ($('task-ncdexcomm')) return true;
    var g = grid(); if (!g) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-ncdexcomm';
    tile.style.cssText = '--tile-accent:#a3e635;cursor:pointer';
    tile.onclick = function () { nqOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">🌾</div>' +
      '<div class="tile-name">NCDEX COMM LIMIT <span class="st" id="nqChip">idle</span></div>' +
      '<div class="tile-desc">Builds the daily NCDEX deposit limit file (NCXDEX_DEPOSIT_FILE.txt), e-mails it and uploads it to Kambala. Starts by itself 10 seconds after PROBABLE runs.</div>' +
      '<div class="tile-desc" id="nqMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#a3e635;color:#1a2a04" onclick="event.stopPropagation();nqOpen()" data-tip="Open NCDEX COMM LIMIT: downloads, run, auto switch, credentials, e-mail To / CC, upload and backup files, logs.">Open</button></div>';
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
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('nqDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(940px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__nq) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__nq = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  var RED = ';border-color:rgba(248,113,113,.45);color:var(--red)';
  window.nqSub = function (fn) { window._saReturn = nqOpen; fn(); };
  window.nqCreds = function () { nqSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.nqEmail = function () { nqSub(function () { if (typeof window.pxConfig === 'function') window.pxConfig(PIPE); }); };
  window.nqLogs = function () { nqSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function nqOpen() {
    window._saReturn = null;
    var auto = S.data ? S.data.auto : true;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin:0 0 8px">' +
        tb('⬇ ClientNet deposits (NCDEX)', "nqFetch('clientnet')", 'Downloads today\'s ClientNetEffectiveDeposits from NCDEX NCFE (same download as Margin Shortage, the captcha is filled in by itself). Taken from MCX COMM LIMIT\'s folder when its NMASS run already brought it.') +
        tb('✉ 3-month active mail file', "nqFetch('active')", 'Downloads the previous day\'s NCDEX_3MONTH_ACTIVE_DETAILS from the mailbox (Monday: Sunday, then Saturday, then Friday).') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 8px">' +
        tb('🔑 Credentials', 'nqCreds()', 'NCDEX login, mailbox and Kambala FTP.') +
        tb('✉️ Email To / CC', 'nqEmail()', 'Who gets the NCDEX RMS Limits File mail.') +
        tb('⬆️ Upload files', 'nqSub(nqUpload)', 'Add a file you got by hand to the folder.') +
        tb('📦 Backup files', 'nqSub(nqBackup)', 'Pick a date or a range and download the backed-up files as one zip.') +
        tb('🗂️ Logs', 'nqLogs()', 'The full log of every run.') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 14px">' +
        tb('▶ Run', 'nqRun()', 'Runs the whole job now. If today\'s run already succeeded it asks for the password; after a failed run it goes straight through.', ';background:var(--blue);color:var(--blue-d);border-color:transparent') +
        tb(auto ? '⏸ Auto: ON' : '▶ Auto: OFF', 'nqAuto()', 'ON: starts by itself 10 s after PROBABLE runs (once a day). OFF: only the Run button starts it. Asks for the password.', auto ? '' : RED) +
        tb('⚙ Settings', 'nqSub(nqSettings)', 'Excluded client codes, start delay, Kambala FTP on/off, WhatsApp alert numbers.') +
        tb('⛔ Stop', 'nqStop()', 'Stops the run or download that is going right now.', RED) +
      '</div><div id="nqMsg"></div><div id="nqDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.nqOpen = nqOpen;

  function say(txt, kind) {
    var el = $('nqMsg');
    if (el) el.innerHTML = txt ? '<div class="nq-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('nqMsg'); if (e) e.innerHTML = ''; }, 8000);
  }

  // ───────────────────────── render ─────────────────────────
  var ST = { idle: 'Not run yet today', running: 'Running…', done: '✓ Done', failed: '✗ Failed', stopped: 'Stopped' };
  function kpi(k, v, f) { return '<div class="nq-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div></div>'; }
  function banner(d) {
    if (d.status === 'failed') return '<div class="nq-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>Today\'s run FAILED.</b> ' + esc(d.error || '') + ' <span style="color:var(--dim)">· ' + (d.alert_sent ? 'WhatsApp alert sent' : 'alert not sent' + (d.alert_reason ? ' (' + esc(d.alert_reason) + ')' : '')) + ' · press <b>Run</b> to try again (no password needed).</span></div>';
    if (d.status === 'done') return '<div class="nq-ban" style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:var(--green)"><b>✓ Done today</b> - ' + esc(d.rows) + ' clients written · mail sent' + (d.uploaded ? ' · uploaded to Kambala' : ' · <span style="color:var(--amber)">NOT uploaded to Kambala</span>') + ' · Run again asks for the password.</div>';
    if (!d.auto) return '<div class="nq-ban" style="background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.45);color:var(--amber)"><b>Auto is OFF</b> - it will not start with PROBABLE. Use Run.</div>';
    return '<div class="nq-ban" style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.35);color:var(--blue)">' + (d.auto_tried ? 'The automatic start has already happened today.' : '● Auto is ON - starts ' + esc((d.settings || {}).delay_s) + ' s after Run is clicked on PROBABLE (once a day).') + '</div>';
  }
  function kpis(d) {
    var ins = d.inputs || [], ok = ins.filter(function (x) { return x.ok; }).length;
    var run = d.running ? 'Running' : (ST[d.status] || d.status);
    return '<div class="nq-kpis">' +
      kpi('Today', '<span style="color:' + (d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--ink)') + '">' + run + '</span>', (d.started ? 'started ' + d.started.slice(11, 16) : 'not started') + (d.trigger ? ' · ' + d.trigger : '') + (d.by ? ' · ' + d.by : '')) +
      kpi('Input files', ok + '<small> / ' + ins.length + '</small>', ok === ins.length ? 'all of today\'s files are in the folder' : 'missing: ' + ins.filter(function (x) { return !x.ok; }).map(function (x) { return x.label; }).join(', ')) +
      kpi('Clients written', d.rows == null ? '—' : d.rows, d.rows == null ? 'file not built yet' : (d.with_collateral || 0) + ' with collateral · ' + (d.added || 0) + ' added from the 3-month file') +
      kpi('Mail · Kambala', (d.mailed ? '✓' : '—') + ' · ' + (d.uploaded ? '✓' : '—'), 'e-mail sent · FTP upload') + '</div>';
  }
  function steps(d) {
    return '<div class="nq-lbl">Steps</div><div class="nq-steps">' + (d.steps || []).map(function (s) {
      var c = s.status === 'ok' || s.status === 'skipped' ? 'ok' : s.status === 'failed' ? 'failed' : s.status === 'running' ? 'running' : '';
      var t = { ok: '✓ done', skipped: '✓ already there', failed: '✗ failed', running: 'running…', stopped: 'stopped' }[s.status] || '—';
      return '<div class="nq-step ' + c + '"><div class="n">' + esc(s.label) + '</div><div class="s">' + t + '</div><div class="m" title="' + esc(s.msg) + '">' + esc(s.msg) + '</div></div>';
    }).join('') + '</div>';
  }
  function inputs(d) {
    return '<div class="nq-lbl">Files in the folder</div><div style="margin-bottom:12px">' + (d.inputs || []).map(function (x) {
      return '<div class="nq-frow"><span style="width:18px;color:' + (x.ok ? 'var(--green)' : 'var(--red)') + '">' + (x.ok ? '✓' : '✗') + '</span><span style="width:170px;color:var(--muted)">' + esc(x.label) + '</span><span class="n">' + (x.found ? esc(x.name) : '— not there') + '</span>' +
        '<span style="color:var(--dim);font-size:11px">' + (x.found ? kb(x.size) + ' · ' + esc(x.time) + (x.ok ? '' : ' · ' + esc(x.why)) : '') + '</span></div>';
    }).join('') + '</div>';
  }
  function logBox(d) {
    var lines = d.log || [];
    return '<div class="nq-lbl">Live log</div><div class="nq-logbox" id="nqLogBox">' + (lines.length ? lines.slice(-30).map(esc).join('\n') : 'No activity since the server started. The full log of every run is under 🗂️ Logs.') + '</div>';
  }
  function paintDyn(force) {
    var d = S.data, el = $('nqDyn');
    if (!el || !d || !d.steps) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('nqLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    el.innerHTML = banner(d) + kpis(d) + steps(d) + logBox(d) + inputs(d) +
      '<div style="font-size:11px;color:var(--dim);line-height:1.6">Folder: <span style="font-family:var(--mono)">' + esc(d.folder) + '</span> · backups in <span style="font-family:var(--mono)">BACKUPFILES AUTOMATED / dd-mm-yyyy</span>. ' +
      'Files already in the folder are never downloaded again. The ClientNet file is taken from MCX COMM LIMIT\'s NMASS download when it is already there, so NCDEX is not logged in to twice.</div>';
    var nb = $('nqLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }
  function paintTile(d) {
    var chip = $('nqChip'), mini = $('nqMini');
    if (!chip || !d || !d.steps) return;
    chip.textContent = d.running ? 'running' : d.status === 'failed' ? 'failed' : d.status === 'done' ? 'done' : !d.auto ? 'auto off' : 'idle';
    chip.style.color = d.running ? 'var(--blue)' : d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : !d.auto ? 'var(--amber)' : 'var(--dim)';
    var ok = (d.inputs || []).filter(function (x) { return x.ok; }).length;
    mini.textContent = d.running ? 'Running…' : d.status === 'done' ? 'Done · ' + d.rows + ' clients' + (d.uploaded ? ' · uploaded' : '') : d.status === 'failed' ? 'Failed - open for details' : ok + ' / ' + (d.inputs || []).length + ' files in' + (d.auto ? ' · waits for PROBABLE' : ' · auto OFF');
  }
  function load() {
    var next = 8000;
    return get('/ncdex-comm/status').then(function (d) {
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
  window.nqFetch = function (what) {
    post('/ncdex-comm/fetch', { what: what }).then(function (j) {
      if (j && j.ok) say('Download started - watch the live log.'); else say((j && (j.error || j.detail)) || 'Could not start.', 'err');
      reload();
    });
  };
  function startRun(pw) {
    post('/ncdex-comm/run', pw ? { password: pw } : {}).then(function (j) {
      if (j && j.need_password) { ask('Run again (today\'s run already succeeded)', function (p) { startRun(p); }); return; }
      if (j && j.ok) say('Started - watch the steps and the live log.'); else say((j && (j.error || j.msg || j.detail)) || 'Could not start.', 'err');
      reload();
    });
  }
  window.nqRun = function () { startRun(''); };
  window.nqStop = function () {
    if (!window.confirm('Stop the run / download that is going now?')) return;
    post('/ncdex-comm/stop').then(function (j) { say(j && j.stopped ? 'Stopping…' : 'Nothing is running.'); reload(); });
  };
  window.nqAuto = function () {
    var on = !(S.data && S.data.auto);
    ask(on ? 'Switch auto start ON' : 'Switch auto start OFF', function (pw) {
      post('/ncdex-comm/settings', { password: pw, auto: on }).then(function (j) {
        nqOpen();
        if (j && j.ok) say(on ? 'Auto is ON - starts 10 s after PROBABLE runs.' : 'Auto is OFF - only the Run button starts it.'); else say((j && j.error) || 'Failed', 'err');
        reload();
      });
    });
  };

  // ───────────────────────── Settings ─────────────────────────
  window.nqSettings = function () {
    ask('Settings', function (pw) {
      get('/ncdex-comm/settings').then(function (s) {
        S.pw = pw;
        modal('⚙ ' + TITLE + ' - settings',
          '<div class="nq-lbl">Client codes always left out (comma separated)</div><input type="text" id="nqExcl" class="nq-in" style="width:100%;margin-bottom:12px" value="' + esc(s.excluded || '') + '">' +
          '<div class="nq-lbl">Start delay after PROBABLE Run (seconds)</div><input type="number" id="nqDelay" min="0" max="600" class="nq-in" style="width:100%;margin-bottom:12px" value="' + esc(s.delay_s) + '">' +
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:12px"><input type="checkbox" id="nqFtp" ' + (s.ftp_enabled ? 'checked' : '') + '> Upload the finished file to Kambala FTP</label>' +
          '<div class="nq-lbl">WhatsApp failure-alert numbers (blank = the Margin Shortage / common alert numbers)</div><input type="text" id="nqAlert" class="nq-in" style="width:100%;margin-bottom:14px" placeholder="9876543210, 9123456789" value="' + esc(s.alert_numbers || '') + '">' +
          '<button class="sa-save" id="nqSetBtn" data-no-busy="1" style="width:100%" onclick="nqSetSave()">Save settings</button><div class="sa-msg" id="nqSetMsg"></div>');
      });
    });
  };
  window.nqSetSave = function () {
    var btn = $('nqSetBtn'), m = $('nqSetMsg'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post('/ncdex-comm/settings', { password: S.pw, excluded: $('nqExcl').value, delay_s: parseInt($('nqDelay').value || '10', 10), ftp_enabled: $('nqFtp').checked, alert_numbers: $('nqAlert').value }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = j && j.ok ? '✓ Saved' : 'Save settings'; }
      if (m) m.innerHTML = j && j.ok ? '<span style="color:var(--green)">Saved.</span>' : '<span style="color:var(--red)">' + esc((j && j.error) || 'Failed') + '</span>';
      reload();
    });
  };

  // ───────────────────────── Backup files ─────────────────────────
  window.nqBackup = function () {
    var t = isoToday();
    S.bk = { from: isoAgo(0), to: t, data: null };
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">After every successful run the day\'s input files and the finished limit file are kept in <span style="font-family:var(--mono)">BACKUPFILES AUTOMATED / dd-mm-yyyy</span>. Pick one day or a range.</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="nq-lbl">From</div><input type="date" id="nqBkFrom" class="nq-in" style="width:100%" value="' + S.bk.from + '" onchange="nqBkLoad()"></div>' +
      '<div><div class="nq-lbl">To</div><input type="date" id="nqBkTo" class="nq-in" style="width:100%" value="' + S.bk.to + '" onchange="nqBkLoad()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="nq-chip" onclick="nqBkQuick(0)">Today</button><button class="nq-chip" onclick="nqBkQuick(1)">Yesterday</button><button class="nq-chip" onclick="nqBkQuick(7)">Last 7 days</button><button class="nq-chip" onclick="nqBkQuick(30)">Last 30 days</button></div>' +
      '<div id="nqBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="nqBkGo" data-no-busy="1" style="width:100%" onclick="nqBkDownload()">⬇ Download zip</button>');
    widen(true);
    nqBkLoad();
  };
  window.nqBkQuick = function (n) { $('nqBkTo').value = n === 1 ? isoAgo(1) : isoToday(); $('nqBkFrom').value = isoAgo(n); nqBkLoad(); };
  window.nqBkLoad = function () {
    var a = $('nqBkFrom').value, b = $('nqBkTo').value || a; if (b < a) { var x = a; a = b; b = x; }
    S.bk.from = a; S.bk.to = b;
    get('/ncdex-comm/range?from_date=' + encodeURIComponent(a) + '&to_date=' + encodeURIComponent(b)).then(function (j) {
      var l = $('nqBkList'); if (!l) return;
      var byDay = {}; (j.files || []).forEach(function (f) { (byDay[f.date] = byDay[f.date] || []).push(f); });
      var days = Object.keys(byDay), tot = (j.files || []).length;
      l.innerHTML = tot ? days.map(function (day) {
        return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + (days.length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day) + ' <span style="font-weight:400;color:var(--dim)">· ' + byDay[day].length + ' files</span></summary>' +
          byDay[day].map(function (f) { return '<div class="nq-frow"><span class="n">' + esc(f.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span><a href="' + API + '/ncdex-comm/file?date=' + encodeURIComponent(day) + '&name=' + encodeURIComponent(f.name) + '" download>⬇</a></div>'; }).join('') + '</details>';
      }).join('') : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No backed-up files for that ' + (a === b ? 'day' : 'range') + '.</div>';
      var go = $('nqBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; }
    });
  };
  window.nqBkDownload = function () { window.location.href = API + '/ncdex-comm/zip-range?from_date=' + encodeURIComponent(S.bk.from) + '&to_date=' + encodeURIComponent(S.bk.to); };

  // ───────────────────────── Upload files ─────────────────────────
  window.nqUpload = function () {
    modal('⬆️ ' + TITLE + ' - upload files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.6;margin-bottom:12px">Got a file by hand? Add it here - it goes into the working folder under its own name and the next run uses it instead of downloading.</div>' +
      '<div style="padding:10px 14px;border:1px dashed var(--line-2);border-radius:10px;font-size:11.5px;line-height:1.9;margin-bottom:12px"><b style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase">Files used</b><br><span style="font-family:var(--mono);color:var(--teal)">ClientNetEffectiveDeposits_&lt;ddmmyyyy&gt;.csv (today)<br>NCDEX_3MONTH_ACTIVE_DETAILS_&lt;ddmmyyyy&gt;.csv (previous day)</span></div>' +
      '<button class="sa-save" data-no-busy="1" style="width:100%" onclick="nqUpPick()">📄 Choose files…</button><div id="nqUpRes" style="margin-top:12px"></div>');
  };
  window.nqUpPick = function () {
    var old = $('nqUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.multiple = true; inp.id = 'nqUpInput'; inp.style.display = 'none';
    inp.onchange = function () { var fl = [].slice.call(inp.files); inp.remove(); if (fl.length) nqUpSend(fl); };
    document.body.appendChild(inp); inp.click();
  };
  function nqUpSend(files) {
    var res = $('nqUpRes'); if (res) res.innerHTML = '<div style="color:var(--muted);font-size:12.5px"><span class="nq-spin"></span>Uploading ' + files.length + ' file(s)…</div>';
    var fd = new FormData(); files.forEach(function (f) { fd.append('files', f); });
    fetch(API + '/ncdex-comm/upload', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (j) {
      if (!res) return;
      if (j.error || j.detail) { res.innerHTML = '<div style="color:var(--red)">' + esc(j.error || j.detail) + '</div>'; return; }
      var h = '<div class="nq-msg" style="color:' + ((j.rejected || []).length ? 'var(--amber)' : 'var(--green)') + '">' + (j.ok || []).length + ' file(s) saved' + ((j.rejected || []).length ? ' · ' + j.rejected.length + ' not accepted' : '') + '</div>';
      (j.ok || []).forEach(function (m) { h += '<div class="nq-frow"><span style="color:var(--green)">✓</span><span class="n">' + esc(m) + '</span></div>'; });
      (j.rejected || []).forEach(function (m) { h += '<div class="nq-frow"><span style="color:var(--red)">✕</span><span class="n">' + esc(m) + '</span></div>'; });
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
