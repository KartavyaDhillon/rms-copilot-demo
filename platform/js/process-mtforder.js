/* MTF ORDER FILE - small tile of the Process Center. "Open" shows the window: single downloads (shortage mail, MTFPOSITION),
   Credentials, Email To / CC, Upload files, Backup files, Logs, Run, Auto on/off, Stop, Settings (run time), KPI cards, step
   tiles, the input files and the live log. Timeline: the shortage mail is picked from 08:00, the run is at 10:00 (editable).
   Backend: /mtf-order/* (api.py, core/mtf_order_file.py) and the shared /px/* windows with pipe=mtfof.
   Injected at serve time (see api.py /legacy); dashboard.html is only touched in its two e-mail-window lists. */
(function () {
  'use strict';
  if (window.__mtfOrderInjected) return;
  window.__mtfOrderInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'MTF ORDER FILE';
  var PIPE = 'mtfof';
  var BASE = '/mtf-order';
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
    var old = $('mofToast'); if (old) old.remove();
    var el = document.createElement('div'); el.id = 'mofToast';
    var col = kind === 'err' ? 'var(--red,#f87171)' : kind === 'warn' ? 'var(--amber,#fbbf24)' : 'var(--green,#34d399)';
    el.style.cssText = 'position:fixed;top:22px;left:50%;transform:translateX(-50%);z-index:100000;max-width:min(560px,92vw);padding:12px 18px;border-radius:12px;' +
      'background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:13px;line-height:1.45;display:flex;gap:10px;align-items:flex-start';
    el.innerHTML = '<span style="color:' + col + ';font-weight:800">' + (kind === 'err' ? '✕' : kind === 'warn' ? '!' : '✓') + '</span><span>' + esc(text) + '</span>';
    document.body.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, kind === 'err' ? 7000 : 3800);
  }
  function okBox(title, text, kind) {      // a popup with an OK button (stays until OK is pressed)
    var old = $('mofOk'); if (old) old.remove();
    var col = kind === 'err' ? 'var(--red,#f87171)' : 'var(--green,#34d399)';
    var bg = document.createElement('div'); bg.id = 'mofOk';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100002;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px;color:' + col + '">' + (kind === 'err' ? '✕ ' : '✓ ') + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;justify-content:flex-end"><button class="sa-tool" data-no-busy="1" id="mofOkBtn" style="min-width:90px;background:' + col + ';color:#06251a;border-color:transparent">OK</button></div></div>';
    document.body.appendChild(bg);
    $('mofOkBtn').onclick = function () { bg.remove(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
    $('mofOkBtn').focus();
  }
  function confirmBox(title, text, okLabel, cb) {
    var old = $('mofConfirm'); if (old) old.remove();
    var bg = document.createElement('div'); bg.id = 'mofConfirm';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--line-2,#2a3342);border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px">' + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end"><button class="sa-tool" id="mofNo" style="min-width:90px">Cancel</button><button class="sa-tool" id="mofYes" style="min-width:90px;background:var(--red,#f87171);color:#2a0a0a;border-color:transparent">' + esc(okLabel || 'OK') + '</button></div></div>';
    document.body.appendChild(bg);
    $('mofNo').onclick = function () { bg.remove(); };
    $('mofYes').onclick = function () { bg.remove(); cb(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
  }

  var css = document.createElement('style');
  css.textContent =
    '.mof-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.mof-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.mof-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.mof-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.mof-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.mof-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.mof-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}' +
    '@media(max-width:820px){.mof-kpis,.mof-steps{grid-template-columns:repeat(2,1fr)}}' +
    '.mof-step{border:1px solid var(--line);border-radius:11px;padding:9px 11px;background:var(--panel-2);min-width:0}' +
    '.mof-step.ok{border-color:rgba(52,211,153,.4)}.mof-step.failed{border-color:rgba(248,113,113,.55)}.mof-step.running{border-color:var(--amber)}' +
    '.mof-step .n{font-size:12px;font-weight:650}.mof-step .s{font-family:var(--mono);font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);margin-top:2px}' +
    '.mof-step.ok .s{color:var(--green)}.mof-step.failed .s{color:var(--red)}.mof-step.running .s{color:var(--amber)}' +
    '.mof-step .m{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.35;overflow:hidden;max-height:42px}' +
    '.mof-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.mof-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:170px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.mof-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.mof-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.mof-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.mof-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.mof-frow a{color:var(--blue);text-decoration:none}.mof-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.mof-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '@keyframes mofSpin{to{transform:rotate(360deg)}}.mof-spin{display:inline-block;width:12px;height:12px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:mofSpin .8s linear infinite;vertical-align:-2px;margin-right:6px}';
  document.head.appendChild(css);

  // ───────────────────────── the small tile: always the LAST tile of the console ─────────────────────────
  function grid() {
    var t = $('task-mtfconc') || $('task-ms15card') || $('task-mcxcomm') || $('task-liveallocation');
    if (t && t.parentNode) return t.parentNode;
    var slot = $('physdelSlot');
    return slot ? slot.parentNode : null;
  }
  function build() {
    if ($('task-mtforder')) return true;
    var g = grid(); if (!g) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-mtforder';
    tile.style.cssText = '--tile-accent:#f59e0b;cursor:pointer';
    tile.onclick = function () { mofOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">🧾</div>' +
      '<div class="tile-name">MTF ORDER FILE <span class="st" id="mofChip">idle</span></div>' +
      '<div class="tile-desc">Picks the MTF risk shortage mail (from the start time set in Settings, 08:00 by default), reads MTFPOSITION + RISK_REPORT from COMMON FILES, makes the Kambala / 0319 order files at 10:00 and e-mails each zone.</div>' +
      '<div class="tile-desc" id="mofMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#f59e0b;color:#2b1a02" onclick="event.stopPropagation();mofOpen()" data-tip="Open MTF ORDER FILE: downloads, run, auto switch, credentials, e-mail To / CC, upload and backup files, logs.">Open</button></div>';
    g.appendChild(tile);
    return true;
  }
  function keepLast() {      // this tile is the very last one of the row
    if (window.__tilesManaged) return;      // process-organizer.js owns the card order (A-Z or your own)
    var el = $('task-mtforder'); if (!el || !el.parentNode) return;
    var p = el.parentNode, bk = document.getElementById('task-entrisk'); bk = bk && bk.parentNode === p ? bk : null;
    var kids = [].slice.call(p.children), want = kids.filter(function (e) { return e !== el && e !== bk; }).concat([el]).concat(bk ? [bk] : []);
    if (kids.some(function (e, i) { return e !== want[i]; })) want.forEach(function (e) { p.appendChild(e); });
  }

  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('mofDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(940px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__mof) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__mof = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (fn.indexOf('mofRun') === 0 ? ' data-no-busy="1"' : '') + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  var RED = ';border-color:rgba(248,113,113,.45);color:var(--red)';
  window.mofSub = function (fn) { window._saReturn = mofOpen; fn(); };
  window.mofCreds = function () { mofSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.mofEmail = function () { mofSub(function () { if (typeof window.pxConfig === 'function') window.pxConfig(PIPE); }); };
  window.mofLogs = function () { mofSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function mofOpen() {
    window._saReturn = null;
    var auto = S.data ? S.data.auto : true;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin:0 0 8px">' +
        tb('⬇ Shortage mail', "mofFetch('mail')", 'Looks in the mailbox for today\'s MTF_RISK_SHORTAGE_REPORT mail (start time in Settings - 12:00) and saves its attachment. A button always checks again.') +
        tb('⬇ MTFPOSITION', "mofFetch('mtfpos')", 'Fetches today\'s MTFPOSITION csv from the FTP into COMMON FILES (skipped if it is already there).') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 8px">' +
        tb('🔑 Credentials', 'mofCreds()', 'Mail sender, mailbox (shortage report) and FTP logins.') +
        tb('✉️ Email To / CC', 'mofEmail()', 'Who gets the full file and each zone file.') +
        tb('⬆️ Upload files', 'mofSub(mofUpload)', 'Put the shortage report in by hand (MTF_RISK_SHORTAGE_REPORT.xlsx) if the mail is late.') +
        tb('📦 Backup files', 'mofSub(mofBackup)', 'Pick a date or a range and download the backed-up files as one zip.') +
        tb('🗂️ Logs', 'mofLogs()', 'The full log of every run.') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 14px">' +
        tb('▶ Run', 'mofRun()', 'Runs the whole job now (picks anything still missing first). If today\'s run already succeeded it asks for the password.', ';background:var(--blue);color:var(--blue-d);border-color:transparent') +
        tb(auto ? '⏸ Auto: ON' : '▶ Auto: OFF', 'mofAuto()', 'ON: the timer picks the mail and runs by itself on trading days. OFF: only the buttons work. Asks for the password.', auto ? '' : RED) +
        tb('⚙ Settings', 'mofSub(mofSettings)', 'Run time, e-mail on/off, WhatsApp alert numbers.') +
        tb('⛔ Stop', 'mofStop()', 'Stops the run or download that is going right now.', RED) +
      '</div><div id="mofMsg"></div><div id="mofDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.mofOpen = mofOpen;

  function say(txt, kind) {
    if (txt) toast(txt, kind);
    var el = $('mofMsg');
    if (el) el.innerHTML = txt ? '<div class="mof-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('mofMsg'); if (e) e.innerHTML = ''; }, 8000);
  }

  // ───────────────────────── render ─────────────────────────
  var ST = { idle: 'Not run yet today', running: 'Running…', done: '✓ Done', failed: '✗ Failed', stopped: 'Stopped' };
  function kpi(k, v, f) { return '<div class="mof-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div></div>'; }
  function banner(d) {
    var s = d.settings || {};
    if (d.status === 'failed') return '<div class="mof-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>Today\'s run FAILED.</b> ' + esc(d.error || '') + ' <span style="color:var(--dim)">· ' + (d.alert_sent ? 'WhatsApp alert sent' : 'alert not sent' + (d.alert_reason ? ' (' + esc(d.alert_reason) + ')' : '')) + ' · press <b>Run</b> to try again (no password needed).</span></div>';
    if (d.status === 'done') return '<div class="mof-ban" style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:var(--green)"><b>✓ Done today</b> - ' + ((d.counts || {}).kamb || 0) + ' Kambala + ' + ((d.counts || {}).o319 || 0) + ' 0319 order(s) · mailed: ' + esc((d.sent || []).join(', ') || 'none') + ((d.empty_zones || []).length ? ' · no orders: ' + esc(d.empty_zones.join(', ')) : '') + ' · Run again asks for the password.</div>';
    if (!d.auto) return '<div class="mof-ban" style="background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.45);color:var(--amber)"><b>Auto is OFF</b> - the timer does nothing. Use the buttons.</div>';
    return '<div class="mof-ban" style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.35);color:var(--blue)">● Auto is ON - the shortage mail is picked from <b>' + esc(s.mail_from || '08:00') + '</b> (until 12:00), the run is at <b>' + esc(s.run_at) + '</b> (trading days).' + (s.send_mail ? '' : ' <b style="color:var(--amber)">E-mail is switched OFF.</b>') + '</div>';
  }
  function kpis(d) {
    var ins = d.inputs || [], ok = ins.filter(function (x) { return x.ok; }).length, c = d.counts || {};
    var run = d.stopping ? 'Stopping…' : d.running ? 'Running' : (ST[d.status] || d.status);
    return '<div class="mof-kpis">' +
      kpi('Today', '<span style="color:' + (d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : 'var(--ink)') + '">' + run + '</span>', (d.started ? 'started ' + d.started.slice(11, 16) : 'not started') + (d.trigger ? ' · ' + d.trigger : '') + (d.by ? ' · ' + d.by : '')) +
      kpi('Input files', ok + '<small> / ' + ins.length + '</small>', ok === ins.length ? 'everything is in place' : 'missing: ' + ins.filter(function (x) { return !x.ok; }).map(function (x) { return x.label; }).join(', ')) +
      kpi('Order lines', c.kamb != null ? (c.kamb + c.o319) : '—', c.kamb != null ? c.kamb + ' Kambala + ' + c.o319 + ' 0319 · ' + c.accounts + ' account(s)' : 'no calculation yet') +
      kpi('E-mails sent', (d.sent || []).length || '—', (d.failed_mail || []).length ? 'FAILED: ' + d.failed_mail.length : ((d.sent || []).join(', ') || 'none yet')) + '</div>';
  }
  function steps(d) {
    return '<div class="mof-lbl">Steps</div><div class="mof-steps">' + (d.steps || []).map(function (s) {
      var c = s.status === 'ok' || s.status === 'skipped' || s.status === 'done' ? 'ok' : s.status === 'failed' ? 'failed' : s.status === 'running' ? 'running' : '';
      var t = { ok: '✓ done', done: '✓ in place', skipped: '✓ already there', failed: '✗ failed', running: 'running…', stopped: 'stopped' }[s.status] || '—';
      return '<div class="mof-step ' + c + '"><div class="n">' + esc(s.label) + '</div><div class="s">' + t + '</div><div class="m" title="' + esc(s.msg) + '">' + esc(s.msg) + '</div></div>';
    }).join('') + '</div>';
  }
  function inputs(d) {
    return '<div class="mof-lbl">Input files</div><div style="margin-bottom:12px">' + (d.inputs || []).map(function (x) {
      return '<div class="mof-frow"><span style="width:18px;color:' + (x.ok ? 'var(--green)' : 'var(--red)') + '">' + (x.ok ? '✓' : '✗') + '</span><span style="width:200px;color:var(--muted)">' + esc(x.label) + '</span><span class="n">' + (x.found ? esc(x.name) : '— not there (' + esc(x.name) + ')') + '</span>' +
        '<span style="color:var(--dim);font-size:11px">' + (x.found ? kb(x.size) + ' · ' + esc(x.time) + (x.ok ? '' : ' · ' + esc(x.why)) : '') + '</span></div>';
    }).join('') + '</div>';
  }
  function logBox(d) {
    var lines = d.log || [];
    return '<div class="mof-lbl">Live log</div><div class="mof-logbox" id="mofLogBox">' + (lines.length ? lines.slice(-30).map(esc).join('\n') : 'No activity since the server started. The full log of every run is under 🗂️ Logs.') + '</div>';
  }
  function paintDyn(force) {
    var d = S.data, el = $('mofDyn');
    if (!el || !d || !d.steps) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('mofLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    el.innerHTML = banner(d) + kpis(d) + steps(d) + logBox(d) + inputs(d) +
      '<div style="font-size:11px;color:var(--dim);line-height:1.6">Folder: <span style="font-family:var(--mono)">' + esc(d.folder) + '</span> · backups in <span style="font-family:var(--mono)">BACKUP FILES AUTOMATED / dd-mm-yyyy</span> (a finished month is filed as OCT 2026 / dd-mm-yyyy). ' +
      'RISK_REPORT and MTFPOSITION are read from COMMON FILES (copied for the run - the originals stay). A zone with no order lines gets no mail.</div>';
    var nb = $('mofLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }
  function paintTile(d) {
    var chip = $('mofChip'), mini = $('mofMini');
    if (!chip || !d || !d.steps) return;
    chip.textContent = d.stopping ? 'stopping' : d.running ? 'running' : d.status === 'failed' ? 'failed' : d.status === 'done' ? 'done' : !d.auto ? 'auto off' : 'idle';
    chip.style.color = d.running ? 'var(--blue)' : d.status === 'failed' ? 'var(--red)' : d.status === 'done' ? 'var(--green)' : !d.auto ? 'var(--amber)' : 'var(--dim)';
    var ok = (d.inputs || []).filter(function (x) { return x.ok; }).length, s = d.settings || {};
    mini.textContent = d.stopping ? 'Stopping…' : d.running ? 'Running…' : d.status === 'done' ? 'Done · ' + (((d.counts || {}).kamb || 0) + ((d.counts || {}).o319 || 0)) + ' order(s) · ' + (d.sent || []).length + ' mail(s)' : d.status === 'failed' ? 'Failed - open for details' : ok + ' / ' + (d.inputs || []).length + ' files in' + (d.auto ? ' · run at ' + s.run_at : ' · auto OFF');
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
  window.mofFetch = function (what) {
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
  window.mofRun = function () { startRun(''); };
  window.mofStop = function () {
    confirmBox('Stop MTF ORDER FILE?', 'The run or download that is going now will be stopped. Files already downloaded are kept.', 'Stop', function () {
      post(BASE + '/stop').then(function (j) { say(j && j.stopped ? 'Stop requested - it ends in a few seconds.' : 'Nothing is running.', j && j.stopped ? 'ok' : 'warn'); reload(); });
    });
  };
  window.mofAuto = function () {
    var on = !(S.data && S.data.auto);
    ask(on ? 'Switch auto ON' : 'Switch auto OFF', function (pw) {
      post(BASE + '/settings', { password: pw, auto: on }).then(function (j) {
        mofOpen();
        if (j && j.ok) say(on ? 'Auto is ON.' : 'Auto is OFF - only the buttons work.'); else say((j && j.error) || 'Failed', 'err');
        reload();
      });
    });
  };

  // ───────────────────────── Settings ─────────────────────────
  window.mofSettings = function () {
    ask('Settings', function (pw) {
      get(BASE + '/settings').then(function (s) {
        S.pw = pw;
        modal('⚙ ' + TITLE + ' - settings',
          '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:8px">' +
          '<div><div class="mof-lbl">Mail look-up start time</div><input type="time" id="mofT1" class="mof-in" style="width:100%" value="' + esc(s.mail_from || '08:00') + '"></div>' +
          '<div><div class="mof-lbl">Run time (order files made and e-mailed)</div><input type="time" id="mofT3" class="mof-in" style="width:100%" value="' + esc(s.run_at) + '"></div></div>' +
          '<div style="font-size:11.5px;color:var(--dim);line-height:1.55;margin-bottom:12px">The shortage mail is looked for every 5 minutes from the start time until 12:00; only a mail that arrived at or after the start time is taken. If it is not there at the run time, the run waits for it until 12:00.</div>' +
          '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:12px"><input type="checkbox" id="mofMail" ' + (s.send_mail ? 'checked' : '') + '> Send the e-mails automatically at the run</label>' +
          '<div class="mof-lbl">WhatsApp failure-alert numbers (blank = the common alert numbers of Scheduler &gt; Credentials &gt; WhatsApp)</div><input type="text" id="mofAlert" class="mof-in" style="width:100%;margin-bottom:14px" placeholder="9876543210, 9123456789" value="' + esc(s.alert_numbers || '') + '">' +
          '<button class="sa-save" id="mofSetBtn" data-no-busy="1" style="width:100%" onclick="mofSetSave()">Save settings</button><div class="sa-msg" id="mofSetMsg"></div>');
      });
    });
  };
  window.mofSetSave = function () {
    var btn = $('mofSetBtn'), m = $('mofSetMsg'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post(BASE + '/settings', { password: S.pw, mail_from: $('mofT1').value, run_at: $('mofT3').value, send_mail: $('mofMail').checked, alert_numbers: $('mofAlert').value }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = j && j.ok ? '✓ Saved' : 'Save settings'; }
      if (j && j.ok) okBox('Settings saved', 'Mail look-up starts at ' + $('mofT1').value + ', the run is at ' + $('mofT3').value + '.'); else okBox('Not saved', (j && j.error) || 'Could not save the settings.', 'err');
      if (m) m.innerHTML = j && j.ok ? '<span style="color:var(--green)">Saved.</span>' : '<span style="color:var(--red)">' + esc((j && j.error) || 'Failed') + '</span>';
      reload();
    });
  };

  // ───────────────────────── Backup files ─────────────────────────
  window.mofBackup = function () {
    S.bk = { from: iso(0), to: iso(0) };
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">After every successful run the day\'s input and output files are kept in <span style="font-family:var(--mono)">BACKUP FILES AUTOMATED / dd-mm-yyyy</span> (finished months are filed under e.g. OCT 2026). Pick one day or a range.</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="mof-lbl">From</div><input type="date" id="mofBkFrom" class="mof-in" style="width:100%" value="' + S.bk.from + '" onchange="mofBkLoad()"></div>' +
      '<div><div class="mof-lbl">To</div><input type="date" id="mofBkTo" class="mof-in" style="width:100%" value="' + S.bk.to + '" onchange="mofBkLoad()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="mof-chip" onclick="mofBkQuick(0)">Today</button><button class="mof-chip" onclick="mofBkQuick(1)">Yesterday</button><button class="mof-chip" onclick="mofBkQuick(7)">Last 7 days</button><button class="mof-chip" onclick="mofBkQuick(30)">Last 30 days</button></div>' +
      '<div id="mofBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="mofBkGo" data-no-busy="1" style="width:100%" onclick="mofBkDownload()">⬇ Download zip</button>');
    widen(true);
    mofBkLoad();
  };
  window.mofBkQuick = function (n) { $('mofBkTo').value = n === 1 ? iso(1) : iso(0); $('mofBkFrom').value = iso(n); mofBkLoad(); };
  window.mofBkLoad = function () {
    var a = $('mofBkFrom').value, b = $('mofBkTo').value || a; if (b < a) { var x = a; a = b; b = x; }
    S.bk.from = a; S.bk.to = b;
    get(BASE + '/range?from_date=' + encodeURIComponent(a) + '&to_date=' + encodeURIComponent(b)).then(function (j) {
      var l = $('mofBkList'); if (!l) return;
      var byDay = {}; (j.files || []).forEach(function (f) { (byDay[f.date] = byDay[f.date] || []).push(f); });
      var days = Object.keys(byDay), tot = (j.files || []).length;
      l.innerHTML = tot ? days.map(function (day) {
        return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + (days.length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day) + ' <span style="font-weight:400;color:var(--dim)">· ' + byDay[day].length + ' files</span></summary>' +
          byDay[day].map(function (f) { return '<div class="mof-frow"><span class="n">' + esc(f.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span><a href="' + API + BASE + '/file?date=' + encodeURIComponent(day) + '&name=' + encodeURIComponent(f.name) + '" download>⬇</a></div>'; }).join('') + '</details>';
      }).join('') : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No backed-up files for that ' + (a === b ? 'day' : 'range') + '.</div>';
      var go = $('mofBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; }
    });
  };
  window.mofBkDownload = function () { toast('Preparing the zip - the download starts in a moment.'); window.location.href = API + BASE + '/zip-range?from_date=' + encodeURIComponent(S.bk.from) + '&to_date=' + encodeURIComponent(S.bk.to); };

  // ───────────────────────── Upload files ─────────────────────────
  window.mofUpload = function () {
    modal('⬆️ ' + TITLE + ' - upload files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.6;margin-bottom:12px">Shortage mail late? Put the report here - it must be named <b>MTF_RISK_SHORTAGE_REPORT.xlsx</b> and is used by the next run instead of the mailbox.</div>' +
      '<div style="padding:10px 14px;border:1px dashed var(--line-2);border-radius:10px;font-size:11.5px;line-height:1.9;margin-bottom:12px"><b style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase">File used</b><br><span style="font-family:var(--mono);color:var(--teal)">MTF_RISK_SHORTAGE_REPORT.xlsx</span><br><span style="color:var(--dim)">RISK_REPORT and MTFPOSITION are not uploaded here - they are read from COMMON FILES (Upload Common Files).</span></div>' +
      '<button class="sa-save" data-no-busy="1" style="width:100%" onclick="mofUpPick()">📄 Choose files…</button><div id="mofUpRes" style="margin-top:12px"></div>');
  };
  window.mofUpPick = function () {
    var old = $('mofUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.multiple = true; inp.id = 'mofUpInput'; inp.style.display = 'none';
    inp.onchange = function () { var fl = [].slice.call(inp.files); inp.remove(); if (fl.length) mofUpSend(fl); };
    document.body.appendChild(inp); inp.click();
  };
  function mofUpSend(files) {
    var res = $('mofUpRes'); if (res) res.innerHTML = '<div style="color:var(--muted);font-size:12.5px"><span class="mof-spin"></span>Uploading ' + files.length + ' file(s)…</div>';
    var fd = new FormData(); files.forEach(function (f) { fd.append('files', f); });
    fetch(API + BASE + '/upload', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (j) {
      if (!res) return;
      if (j.error || j.detail) { res.innerHTML = '<div style="color:var(--red)">' + esc(j.error || j.detail) + '</div>'; return; }
      var h = '<div class="mof-msg" style="color:' + ((j.rejected || []).length ? 'var(--amber)' : 'var(--green)') + '">' + (j.ok || []).length + ' file(s) saved' + ((j.rejected || []).length ? ' · ' + j.rejected.length + ' not accepted' : '') + '</div>';
      (j.ok || []).forEach(function (m) { h += '<div class="mof-frow"><span style="color:var(--green)">✓</span><span class="n">' + esc(m) + '</span></div>'; });
      (j.rejected || []).forEach(function (m) { h += '<div class="mof-frow"><span style="color:var(--red)">✕</span><span class="n">' + esc(m) + '</span></div>'; });
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
