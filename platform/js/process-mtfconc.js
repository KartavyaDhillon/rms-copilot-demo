/* MTF CONCENTRATION - the LAST small tile of the Process Center. "Open" shows the window: single downloads (KORP shortage file,
   index files, MTFPOSITION), Credentials, Email To / CC, Upload files, Backup files, Logs, Run, Auto on/off, Stop, Settings,
   plus KPI cards, step tiles, the input files and the live log.
   Timeline (editable in Settings): 08:40 downloads, 09:15 MTFPOSITION from the FTP, 09:30 run + e-mails.
   Backend: /mtf-conc/* (api.py, core/mtf_concentration.py) and the shared /px/* windows with pipe=mtfconc.
   Injected at serve time (see api.py /legacy); dashboard.html is not modified. */
(function () {
  'use strict';
  if (window.__mtfConcInjected) return;
  window.__mtfConcInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'MTF CONCENTRATION';
  var PIPE = 'mtfconc';
  var BASE = '/mtf-conc';
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
    var old = $('cqToast'); if (old) old.remove();
    var el = document.createElement('div'); el.id = 'cqToast';
    var col = kind === 'err' ? 'var(--red,#f87171)' : kind === 'warn' ? 'var(--amber,#fbbf24)' : 'var(--green,#34d399)';
    el.style.cssText = 'position:fixed;top:22px;left:50%;transform:translateX(-50%);z-index:100000;max-width:min(560px,92vw);padding:12px 18px;border-radius:12px;' +
      'background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:13px;line-height:1.45;display:flex;gap:10px;align-items:flex-start';
    el.innerHTML = '<span style="color:' + col + ';font-weight:800">' + (kind === 'err' ? '✕' : kind === 'warn' ? '!' : '✓') + '</span><span>' + esc(text) + '</span>';
    document.body.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, kind === 'err' ? 7000 : 3800);
  }
  function confirmBox(title, text, okLabel, cb) {
    var old = $('cqConfirm'); if (old) old.remove();
    var bg = document.createElement('div'); bg.id = 'cqConfirm';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--line-2,#2a3342);border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px">' + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end"><button class="sa-tool" id="cqNo" style="min-width:90px">Cancel</button><button class="sa-tool" id="cqYes" style="min-width:90px;background:var(--red,#f87171);color:#2a0a0a;border-color:transparent">' + esc(okLabel || 'OK') + '</button></div></div>';
    document.body.appendChild(bg);
    $('cqNo').onclick = function () { bg.remove(); };
    $('cqYes').onclick = function () { bg.remove(); cb(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
  }

  var css = document.createElement('style');
  css.textContent =
    '.cq-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.cq-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.cq-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.cq-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.cq-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.cq-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.cq-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}' +
    '@media(max-width:820px){.cq-kpis,.cq-steps{grid-template-columns:repeat(2,1fr)}}' +
    '.cq-step{border:1px solid var(--line);border-radius:11px;padding:9px 11px;background:var(--panel-2);min-width:0}' +
    '.cq-step.ok{border-color:rgba(52,211,153,.4)}.cq-step.failed{border-color:rgba(248,113,113,.55)}.cq-step.running{border-color:var(--amber)}' +
    '.cq-step .n{font-size:12px;font-weight:650}.cq-step .s{font-family:var(--mono);font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);margin-top:2px}' +
    '.cq-step.ok .s{color:var(--green)}.cq-step.failed .s{color:var(--red)}.cq-step.running .s{color:var(--amber)}' +
    '.cq-step .m{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.35;overflow:hidden;max-height:42px}' +
    '.cq-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.cq-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:170px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.cq-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.cq-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.cq-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.cq-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.cq-frow a{color:var(--blue);text-decoration:none}.cq-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.cq-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '@keyframes cqSpin{to{transform:rotate(360deg)}}.cq-spin{display:inline-block;width:12px;height:12px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:cqSpin .8s linear infinite;vertical-align:-2px;margin-right:6px}';
  document.head.appendChild(css);

  // ───────────────────────── the small tile: always the LAST tile of the console ─────────────────────────
  function grid() {
    var t = $('task-ms15card') || $('task-mcxcomm') || $('task-liveallocation');
    if (t && t.parentNode) return t.parentNode;
    var slot = $('physdelSlot');
    return slot ? slot.parentNode : null;
  }
  function build() {
    if ($('task-mtfconc')) return true;
    var g = grid(); if (!g) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-mtfconc';
    tile.style.cssText = '--tile-accent:#a78bfa;cursor:pointer';
    tile.onclick = function () { cqOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">📊</div>' +
      '<div class="tile-name">MTF CONCENTRATION <span class="st" id="cqChip">idle</span></div>' +
      '<div class="tile-desc">Downloads the KORP shortage file and the index lists (08:40), fetches MTFPOSITION (09:15), runs the concentration report and e-mails it (09:30).</div>' +
      '<div class="tile-desc" id="cqMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#a78bfa;color:#1b1233" onclick="event.stopPropagation();cqOpen()" data-tip="Open MTF CONCENTRATION: downloads, run, auto switch, credentials, e-mail To / CC, upload and backup files, logs.">Open</button></div>';
    g.appendChild(tile);
    return true;
  }
  function keepLast() {      // this tile is the very last one of the row
    var el = $('task-mtfconc'); if (!el || !el.parentNode) return;
    var p = el.parentNode, after = ['task-mtforder', 'task-entrisk'].map(function (i) { return document.getElementById(i); }).filter(function (e) { return e && e.parentNode === p; });
    var kids = [].slice.call(p.children), want = kids.filter(function (e) { return e !== el && after.indexOf(e) < 0; }).concat([el]).concat(after);
    if (kids.some(function (e, i) { return e !== want[i]; })) want.forEach(function (e) { p.appendChild(e); });
  }

  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('cqDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(940px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__cq) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__cq = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  var RED = ';border-color:rgba(248,113,113,.45);color:var(--red)';
  window.cqSub = function (fn) { window._saReturn = cqOpen; fn(); };
  window.cqCreds = function () { cqSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.cqEmail = function () { cqSub(function () { if (typeof window.pxConfig === 'function') window.pxConfig(PIPE); }); };
  window.cqLogs = function () { cqSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function cqOpen() {
    window._saReturn = null;
    var auto = S.data ? S.data.auto : true;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 8px">' +
        tb('⬇ KORP shortage file', "cqFetch('korp')", 'Downloads MTF CRR report 5 (<ddmmyyyyHHMMSS>_SHORTAGE.xlsx) from the back-office API. A button always downloads a fresh one.') +
        tb('⬇ Index files', "cqFetch('index')", 'Downloads NIFTY 50, BSE 200 and BSE 500 constituent lists (no browser needed).') +
        tb('⬇ MTFPOSITION', "cqFetch('mtfpos')", 'Fetches today\'s MTFPOSITION csv from the FTP into COMMON FILES (skipped if it is already there).') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 8px">' +
        tb('🔑 Credentials', 'cqCreds()', 'Mail sender, back-office API and FTP logins.') +
        tb('✉️ Email To / CC', 'cqEmail()', 'Who gets the full file and each zone file.') +
        tb('⬆️ Upload files', 'cqSub(cqUpload)', 'Add a file you got by hand (or EXCEPTION.xlsx) to the folder.') +
        tb('📦 Backup files', 'cqSub(cqBackup)', 'Pick a date or a range and download the backed-up files as one zip.') +
        tb('🗂️ Logs', 'cqLogs()', 'The full log of every run.') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 14px">' +
        tb('▶ Run', 'cqRun()', 'Runs the whole job now (fetches anything still missing first). If today\'s run already succeeded it asks for the password.', ';background:var(--blue);color:var(--blue-d);border-color:transparent') +
        tb(auto ? '⏸ Auto: ON' : '▶ Auto: OFF', 'cqAuto()', 'ON: the timer does the downloads, the FTP fetch and the run by itself on trading days. OFF: only the buttons work. Asks for the password.', auto ? '' : RED) +
        tb('⚙ Settings', 'cqSub(cqSettings)', 'Times, e-mail on/off, Enterprise Risk copy folder, WhatsApp alert numbers.') +
        tb('⛔ Stop', 'cqStop()', 'Stops the run or download that is going right now.', RED) +
      '</div><div id="cqMsg"></div><div id="cqDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.cqOpen = cqOpen;

  function say(txt, kind) {
    if (txt) toast(txt, kind);
    var el = $('cqMsg');
    if (el) el.innerHTML = txt ? '<div class="cq-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('cqMsg'); if (e) e.innerHTML = ''; }, 8000);
  }

  // ───────────────────────── render ─────────────────────────
  var ST = { idle: 'Not run yet today', running: 'Running…', done: '✓ Done', failed: '✗ Failed', stopped: 'Stopped' };
  function kpi(k, v, f) { return '<div class="cq-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div></div>'; }
  function banner(d) {
    var s = d.settings || {};
    if (d.status === 'failed') return '<div class="cq-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>Today\'s run FAILED.</b> ' + esc(d.error || '') + ' <span style="color:var(--dim)">· ' + (d.alert_sent ? 'WhatsApp alert sent' : 'alert not sent' + (d.alert_reason ? ' (' + esc(d.alert_reason) + ')' : '')) + ' · press <b>Run</b> to try again (no password needed).</span></div>';
    if (d.status === 'done') return '<div class="cq-ban" style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:var(--green)"><b>✓ Done today</b> - ' + (d.made || []).length + ' file(s) made · mailed: ' + esc((d.sent || []).join(', ') || 'none') + (d.copy_failed ? ' · <span style="color:var(--amber)">copy to Enterprise Risk FAILED</span>' : '') + ' · Run again asks for the password.</div>';
    if (!d.auto) return '<div class="cq-ban" style="background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.45);color:var(--amber)"><b>Auto is OFF</b> - the timer does nothing. Use the buttons.</div>';
    return '<div class="cq-ban" style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.35);color:var(--blue)">● Auto is ON - downloads at <b>' + esc(s.download_at) + '</b>, MTFPOSITION at <b>' + esc(s.mtf_at) + '</b>, run at <b>' + esc(s.run_at) + '</b> (trading days).' + (s.send_mail ? '' : ' <b style="color:var(--amber)">E-mail is switched OFF.</b>') + '</div>';
  }
  function kpis(d) {
    var ins = d.inputs || [], ok = ins.filter(function (x) { return x.ok; }).length;
    var run = d.stopping ? 'Stopping…' : d.running ? 'Running' : (ST[d.status] || d.status);
    var zones = (d.made || []).filter(function (x) { return x !== 'FULL'; }).length;
    return '<div class="cq-kpis">' +
      kpi('Today', '<span style="color:' + (d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--ink)') + '">' + run + '</span>', (d.started ? 'started ' + d.started.slice(11, 16) : 'not started') + (d.trigger ? ' · ' + d.trigger : '') + (d.by ? ' · ' + d.by : '')) +
      kpi('Input files', ok + '<small> / ' + ins.length + '</small>', ok === ins.length ? 'everything is in place' : 'missing: ' + ins.filter(function (x) { return !x.ok; }).map(function (x) { return x.label; }).join(', ')) +
      kpi('Files made', (d.made || []).length ? (d.made.indexOf('FULL') >= 0 ? 1 : 0) + ' + ' + zones + '<small> zone</small>' : '—', (d.made || []).length ? 'full file + zone files with violations' : 'no calculation yet') +
      kpi('E-mails sent', (d.sent || []).length || '—', (d.failed_mail || []).length ? 'FAILED: ' + d.failed_mail.length : ((d.sent || []).join(', ') || 'none yet')) + '</div>';
  }
  function steps(d) {
    return '<div class="cq-lbl">Steps</div><div class="cq-steps">' + (d.steps || []).map(function (s) {
      var c = s.status === 'ok' || s.status === 'skipped' || s.status === 'done' ? 'ok' : s.status === 'failed' ? 'failed' : s.status === 'running' ? 'running' : '';
      var t = { ok: '✓ done', done: '✓ in place', skipped: '✓ already there', failed: '✗ failed', running: 'running…', stopped: 'stopped' }[s.status] || '—';
      return '<div class="cq-step ' + c + '"><div class="n">' + esc(s.label) + '</div><div class="s">' + t + '</div><div class="m" title="' + esc(s.msg) + '">' + esc(s.msg) + '</div></div>';
    }).join('') + '</div>';
  }
  function inputs(d) {
    return '<div class="cq-lbl">Input files</div><div style="margin-bottom:12px">' + (d.inputs || []).map(function (x) {
      return '<div class="cq-frow"><span style="width:18px;color:' + (x.ok ? 'var(--green)' : 'var(--red)') + '">' + (x.ok ? '✓' : '✗') + '</span><span style="width:200px;color:var(--muted)">' + esc(x.label) + '</span><span class="n">' + (x.found ? esc(x.name) : '— not there (' + esc(x.name) + ')') + '</span>' +
        '<span style="color:var(--dim);font-size:11px">' + (x.found ? kb(x.size) + ' · ' + esc(x.time) + (x.ok ? '' : ' · ' + esc(x.why)) : '') + '</span></div>';
    }).join('') + '</div>';
  }
  function logBox(d) {
    var lines = d.log || [];
    return '<div class="cq-lbl">Live log</div><div class="cq-logbox" id="cqLogBox">' + (lines.length ? lines.slice(-30).map(esc).join('\n') : 'No activity since the server started. The full log of every run is under 🗂️ Logs.') + '</div>';
  }
  function paintDyn(force) {
    var d = S.data, el = $('cqDyn');
    if (!el || !d || !d.steps) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('cqLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    el.innerHTML = banner(d) + kpis(d) + steps(d) + logBox(d) + inputs(d) +
      '<div style="font-size:11px;color:var(--dim);line-height:1.6">Folder: <span style="font-family:var(--mono)">' + esc(d.folder) + '</span> · backups in <span style="font-family:var(--mono)">BACKUPFILES AUTOMATED / 16th Nov 2026</span>. ' +
      'RISK_REPORT and MTFPOSITION are read from COMMON FILES (copied for the run - the originals stay). EXCEPTION.xlsx is kept (copied to the backup, never moved). The full file is also copied to <span style="font-family:var(--mono)">' + esc((d.settings || {}).copy_dir || '(no copy folder)') + '</span>.</div>';
    var nb = $('cqLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }
  function paintTile(d) {
    var chip = $('cqChip'), mini = $('cqMini');
    if (!chip || !d || !d.steps) return;
    chip.textContent = d.stopping ? 'stopping' : d.running ? 'running' : d.status === 'failed' ? 'failed' : d.status === 'done' ? 'done' : !d.auto ? 'auto off' : 'idle';
    chip.style.color = d.running ? 'var(--blue)' : d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : !d.auto ? 'var(--amber)' : 'var(--dim)';
    var ok = (d.inputs || []).filter(function (x) { return x.ok; }).length, s = d.settings || {};
    mini.textContent = d.stopping ? 'Stopping…' : d.running ? 'Running…' : d.status === 'done' ? 'Done · ' + (d.made || []).length + ' file(s) · ' + (d.sent || []).length + ' mail(s)' : d.status === 'failed' ? 'Failed - open for details' : ok + ' / ' + (d.inputs || []).length + ' files in' + (d.auto ? ' · run at ' + s.run_at : ' · auto OFF');
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
  window.cqFetch = function (what) {
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
  window.cqRun = function () { startRun(''); };
  window.cqStop = function () {
    confirmBox('Stop MTF CONCENTRATION?', 'The run or download that is going now will be stopped. Files already downloaded are kept.', 'Stop', function () {
      post(BASE + '/stop').then(function (j) { say(j && j.stopped ? 'Stop requested - it ends in a few seconds.' : 'Nothing is running.', j && j.stopped ? 'ok' : 'warn'); reload(); });
    });
  };
  window.cqAuto = function () {
    var on = !(S.data && S.data.auto);
    ask(on ? 'Switch auto ON' : 'Switch auto OFF', function (pw) {
      post(BASE + '/settings', { password: pw, auto: on }).then(function (j) {
        cqOpen();
        if (j && j.ok) say(on ? 'Auto is ON.' : 'Auto is OFF - only the buttons work.'); else say((j && j.error) || 'Failed', 'err');
        reload();
      });
    });
  };

  // ───────────────────────── Settings ─────────────────────────
  window.cqSettings = function () {
    ask('Settings', function (pw) {
      get(BASE + '/settings').then(function (s) {
        S.pw = pw;
        modal('⚙ ' + TITLE + ' - settings',
          '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px">' +
          '<div><div class="cq-lbl">Downloads (KORP + index files)</div><input type="time" id="cqT1" class="cq-in" style="width:100%" value="' + esc(s.download_at) + '"></div>' +
          '<div><div class="cq-lbl">MTFPOSITION from FTP</div><input type="time" id="cqT2" class="cq-in" style="width:100%" value="' + esc(s.mtf_at) + '"></div>' +
          '<div><div class="cq-lbl">Run + e-mails</div><input type="time" id="cqT3" class="cq-in" style="width:100%" value="' + esc(s.run_at) + '"></div></div>' +
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:12px"><input type="checkbox" id="cqMail" ' + (s.send_mail ? 'checked' : '') + '> Send the e-mails automatically at the run</label>' +
          '<div class="cq-lbl">Also copy the full file to this folder (blank = no copy)</div><input type="text" id="cqCopy" class="cq-in" style="width:100%;margin-bottom:12px" value="' + esc(s.copy_dir || '') + '">' +
          '<div class="cq-lbl">WhatsApp failure-alert numbers (blank = the Margin Shortage / common alert numbers)</div><input type="text" id="cqAlert" class="cq-in" style="width:100%;margin-bottom:14px" placeholder="9876543210, 9123456789" value="' + esc(s.alert_numbers || '') + '">' +
          '<button class="sa-save" id="cqSetBtn" data-no-busy="1" style="width:100%" onclick="cqSetSave()">Save settings</button><div class="sa-msg" id="cqSetMsg"></div>');
      });
    });
  };
  window.cqSetSave = function () {
    var btn = $('cqSetBtn'), m = $('cqSetMsg'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post(BASE + '/settings', { password: S.pw, download_at: $('cqT1').value, mtf_at: $('cqT2').value, run_at: $('cqT3').value, send_mail: $('cqMail').checked, copy_dir: $('cqCopy').value, alert_numbers: $('cqAlert').value }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = j && j.ok ? '✓ Saved' : 'Save settings'; }
      if (j && j.ok) toast('Settings saved.'); else toast((j && j.error) || 'Could not save the settings.', 'err');
      if (m) m.innerHTML = j && j.ok ? '<span style="color:var(--green)">Saved.</span>' : '<span style="color:var(--red)">' + esc((j && j.error) || 'Failed') + '</span>';
      reload();
    });
  };

  // ───────────────────────── Backup files ─────────────────────────
  window.cqBackup = function () {
    S.bk = { from: iso(0), to: iso(0) };
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">After every successful run the day\'s input and output files are kept in <span style="font-family:var(--mono)">BACKUPFILES AUTOMATED / 16th Nov 2026</span>. Pick one day or a range.</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="cq-lbl">From</div><input type="date" id="cqBkFrom" class="cq-in" style="width:100%" value="' + S.bk.from + '" onchange="cqBkLoad()"></div>' +
      '<div><div class="cq-lbl">To</div><input type="date" id="cqBkTo" class="cq-in" style="width:100%" value="' + S.bk.to + '" onchange="cqBkLoad()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="cq-chip" onclick="cqBkQuick(0)">Today</button><button class="cq-chip" onclick="cqBkQuick(1)">Yesterday</button><button class="cq-chip" onclick="cqBkQuick(7)">Last 7 days</button><button class="cq-chip" onclick="cqBkQuick(30)">Last 30 days</button></div>' +
      '<div id="cqBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="cqBkGo" data-no-busy="1" style="width:100%" onclick="cqBkDownload()">⬇ Download zip</button>');
    widen(true);
    cqBkLoad();
  };
  window.cqBkQuick = function (n) { $('cqBkTo').value = n === 1 ? iso(1) : iso(0); $('cqBkFrom').value = iso(n); cqBkLoad(); };
  window.cqBkLoad = function () {
    var a = $('cqBkFrom').value, b = $('cqBkTo').value || a; if (b < a) { var x = a; a = b; b = x; }
    S.bk.from = a; S.bk.to = b;
    get(BASE + '/range?from_date=' + encodeURIComponent(a) + '&to_date=' + encodeURIComponent(b)).then(function (j) {
      var l = $('cqBkList'); if (!l) return;
      var byDay = {}; (j.files || []).forEach(function (f) { (byDay[f.date] = byDay[f.date] || []).push(f); });
      var days = Object.keys(byDay), tot = (j.files || []).length;
      l.innerHTML = tot ? days.map(function (day) {
        return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + (days.length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day) + ' <span style="font-weight:400;color:var(--dim)">· ' + byDay[day].length + ' files</span></summary>' +
          byDay[day].map(function (f) { return '<div class="cq-frow"><span class="n">' + esc(f.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span><a href="' + API + BASE + '/file?date=' + encodeURIComponent(day) + '&name=' + encodeURIComponent(f.name) + '" download>⬇</a></div>'; }).join('') + '</details>';
      }).join('') : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No backed-up files for that ' + (a === b ? 'day' : 'range') + '.</div>';
      var go = $('cqBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; }
    });
  };
  window.cqBkDownload = function () { toast('Preparing the zip - the download starts in a moment.'); window.location.href = API + BASE + '/zip-range?from_date=' + encodeURIComponent(S.bk.from) + '&to_date=' + encodeURIComponent(S.bk.to); };

  // ───────────────────────── Upload files ─────────────────────────
  window.cqUpload = function () {
    modal('⬆️ ' + TITLE + ' - upload files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.6;margin-bottom:12px">Got a file by hand? Add it here - it goes into the working folder under its own name and the next run uses it instead of downloading. <b>EXCEPTION.xlsx</b> here replaces the permanent exception list.</div>' +
      '<div style="padding:10px 14px;border:1px dashed var(--line-2);border-radius:10px;font-size:11.5px;line-height:1.9;margin-bottom:12px"><b style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase">Files used</b><br><span style="font-family:var(--mono);color:var(--teal)">&lt;ddmmyyyyHHMMSS&gt;_SHORTAGE.xlsx · ind_nifty50list.csv<br>BSE 200index_Constituents.csv · BSE 500index_Constituents.csv · EXCEPTION.xlsx</span><br><span style="color:var(--dim)">RISK_REPORT and MTFPOSITION are not uploaded here - they are read from COMMON FILES (Upload Common Files).</span></div>' +
      '<button class="sa-save" data-no-busy="1" style="width:100%" onclick="cqUpPick()">📄 Choose files…</button><div id="cqUpRes" style="margin-top:12px"></div>');
  };
  window.cqUpPick = function () {
    var old = $('cqUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.multiple = true; inp.id = 'cqUpInput'; inp.style.display = 'none';
    inp.onchange = function () { var fl = [].slice.call(inp.files); inp.remove(); if (fl.length) cqUpSend(fl); };
    document.body.appendChild(inp); inp.click();
  };
  function cqUpSend(files) {
    var res = $('cqUpRes'); if (res) res.innerHTML = '<div style="color:var(--muted);font-size:12.5px"><span class="cq-spin"></span>Uploading ' + files.length + ' file(s)…</div>';
    var fd = new FormData(); files.forEach(function (f) { fd.append('files', f); });
    fetch(API + BASE + '/upload', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (j) {
      if (!res) return;
      if (j.error || j.detail) { res.innerHTML = '<div style="color:var(--red)">' + esc(j.error || j.detail) + '</div>'; return; }
      var h = '<div class="cq-msg" style="color:' + ((j.rejected || []).length ? 'var(--amber)' : 'var(--green)') + '">' + (j.ok || []).length + ' file(s) saved' + ((j.rejected || []).length ? ' · ' + j.rejected.length + ' not accepted' : '') + '</div>';
      (j.ok || []).forEach(function (m) { h += '<div class="cq-frow"><span style="color:var(--green)">✓</span><span class="n">' + esc(m) + '</span></div>'; });
      (j.rejected || []).forEach(function (m) { h += '<div class="cq-frow"><span style="color:var(--red)">✕</span><span class="n">' + esc(m) + '</span></div>'; });
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
