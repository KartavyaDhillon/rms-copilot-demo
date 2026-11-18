/* OBLIGATION REPORT - small tile of the Process Center. "Open" shows the window: Upload MAIN file, Run, Stop, Manual override, Credentials
   (logins + the timings + auto switch + alert numbers), Email To / CC, Backup files, Logs, the slots, KPI cards, step tiles and the live log.
   Job (tcs_nmass.py + obligation_new_final.py): MAIN_<hh.mm>.csv uploaded -> FO collaterals (NMASS, auto captcha) + MCX Margin Utilization (eclear, auto RSA code)
   -> detailed picture -> mail + WhatsApp -> backup. Timings are RELAXED: a late run is only ATTENTION in the console, a FAILED run alerts on WhatsApp.
   Backend: /obligation/* (api.py, core/obligation_card.py) and the shared /px/* windows with pipe=obligation. Injected at serve time (see api.py /legacy). */
(function () {
  'use strict';
  if (window.__oblInjected) return;
  window.__oblInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'OBLIGATION REPORT';
  var PIPE = 'obligation';
  var BASE = '/obligation';
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
    var old = $('oblToast'); if (old) old.remove();
    var el = document.createElement('div'); el.id = 'oblToast';
    var col = kind === 'err' ? 'var(--red,#f87171)' : kind === 'warn' ? 'var(--amber,#fbbf24)' : 'var(--green,#34d399)';
    el.style.cssText = 'position:fixed;top:22px;left:50%;transform:translateX(-50%);z-index:100000;max-width:min(560px,92vw);padding:12px 18px;border-radius:12px;' +
      'background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:13px;line-height:1.45;display:flex;gap:10px;align-items:flex-start';
    el.innerHTML = '<span style="color:' + col + ';font-weight:800">' + (kind === 'err' ? '✕' : kind === 'warn' ? '!' : '✓') + '</span><span>' + esc(text) + '</span>';
    document.body.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, kind === 'err' ? 7000 : 3800);
  }
  function okBox(title, text, kind) {      // a popup with an OK button (stays until OK is pressed)
    var old = $('oblOk'); if (old) old.remove();
    var col = kind === 'err' ? 'var(--red,#f87171)' : 'var(--green,#34d399)';
    var bg = document.createElement('div'); bg.id = 'oblOk';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100002;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px;color:' + col + '">' + (kind === 'err' ? '✕ ' : '✓ ') + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;justify-content:flex-end"><button class="sa-tool" data-no-busy="1" id="oblOkBtn" style="min-width:90px;background:' + col + ';color:#06251a;border-color:transparent">OK</button></div></div>';
    document.body.appendChild(bg);
    $('oblOkBtn').onclick = function () { bg.remove(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
    $('oblOkBtn').focus();
  }
  function confirmBox(title, text, okLabel, cb) {
    var old = $('oblConfirm'); if (old) old.remove();
    var bg = document.createElement('div'); bg.id = 'oblConfirm';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--line-2,#2a3342);border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px">' + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end"><button class="sa-tool" id="oblNo" style="min-width:90px">Cancel</button><button class="sa-tool" id="oblYes" style="min-width:90px;background:var(--red,#f87171);color:#2a0a0a;border-color:transparent">' + esc(okLabel || 'OK') + '</button></div></div>';
    document.body.appendChild(bg);
    $('oblNo').onclick = function () { bg.remove(); };
    $('oblYes').onclick = function () { bg.remove(); cb(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
  }

  var css = document.createElement('style');
  css.textContent =
    '.obl-slots{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}' +
    '.obl-slot{border:1px solid var(--line);border-radius:11px;padding:8px 11px;background:var(--panel-2);min-width:0}' +
    '.obl-slot .n{font-size:12px;font-weight:650;font-family:var(--mono)}.obl-slot .s{font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);margin-top:2px}.obl-slot .m{font-size:10.5px;color:var(--muted);margin-top:2px;overflow:hidden;max-height:30px}' +
    '.obl-slot.ok{border-color:rgba(52,211,153,.4)}.obl-slot.ok .s{color:var(--green)}.obl-slot.failed{border-color:rgba(248,113,113,.55)}.obl-slot.failed .s{color:var(--red)}.obl-slot.running{border-color:var(--amber)}.obl-slot.wait{border-color:rgba(59,130,246,.45)}.obl-slot.wait .s{color:var(--blue)}.obl-slot.attn{border-color:rgba(245,158,11,.7);background:rgba(245,158,11,.08)}.obl-slot.attn .s{color:var(--amber);font-weight:700}' +
    '.obl-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.obl-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.obl-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.obl-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.obl-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.obl-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.obl-steps{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:12px}' +
    '@media(max-width:820px){.obl-kpis,.obl-steps{grid-template-columns:repeat(2,1fr)}}' +
    '.obl-step{border:1px solid var(--line);border-radius:11px;padding:9px 11px;background:var(--panel-2);min-width:0}' +
    '.obl-step.ok{border-color:rgba(52,211,153,.4)}.obl-step.failed{border-color:rgba(248,113,113,.55)}.obl-step.running{border-color:var(--amber)}' +
    '.obl-step .n{font-size:12px;font-weight:650}.obl-step .s{font-family:var(--mono);font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);margin-top:2px}' +
    '.obl-step.ok .s{color:var(--green)}.obl-step.failed .s{color:var(--red)}.obl-step.running .s{color:var(--amber)}' +
    '.obl-step .m{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.35;overflow:hidden;max-height:42px}' +
    '.obl-ban{margin:0 0 12px;padding:10px 12px;border-radius:9px;font-size:12.5px;line-height:1.5}' +
    '.obl-logbox{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:170px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.obl-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.obl-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.obl-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.obl-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.obl-frow a{color:var(--blue);text-decoration:none}.obl-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}' +
    '.obl-chip{padding:4px 11px;border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '@keyframes oblSpin{to{transform:rotate(360deg)}}.obl-spin{display:inline-block;width:12px;height:12px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:oblSpin .8s linear infinite;vertical-align:-2px;margin-right:6px}';
  document.head.appendChild(css);

  // ───────────────────────── the small tile ─────────────────────────
  function grid() {
    var t = $('task-payoutchk') || $('task-cncf11') || $('task-mtforder') || $('task-mtfconc') || $('task-ms15card') || $('task-mcxcomm') || $('task-liveallocation');
    if (t && t.parentNode) return t.parentNode;
    var slot = $('physdelSlot');
    return slot ? slot.parentNode : null;
  }
  function build() {
    if ($('task-obligation')) return true;
    var g = grid(); if (!g) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-obligation';
    tile.style.cssText = '--tile-accent:#f59e0b;cursor:pointer';
    tile.onclick = function () { oblOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">📑</div>' +
      '<div class="tile-name">OBLIGATION REPORT <span class="st" id="oblChip">idle</span></div>' +
      '<div class="tile-desc">Upload the MAIN file; at each timing it downloads FO collaterals (NMASS) and the MCX Margin Utilization, draws the Daily Obligation picture, mails it and sends it on WhatsApp. A late run only shows ATTENTION here - an alert goes out only if a run FAILS.</div>' +
      '<div class="tile-desc" id="oblMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#f59e0b;color:#2a1700" onclick="event.stopPropagation();oblOpen()" data-tip="Open OBLIGATION REPORT: upload, run, manual override, credentials (timings), e-mail To / CC, backup files, logs.">Open</button></div>';
    g.appendChild(tile);
    return true;
  }
  function keepLast() {
    if (window.__tilesManaged) return;      // process-organizer.js owns the card order (A-Z or your own)
    var el = $('task-obligation'); if (!el || !el.parentNode) return;
    var p = el.parentNode, bk = document.getElementById('task-entrisk'); bk = bk && bk.parentNode === p ? bk : null;
    var kids = [].slice.call(p.children), want = kids.filter(function (e) { return e !== el && e !== bk; }).concat([el]).concat(bk ? [bk] : []);
    if (kids.some(function (e, i) { return e !== want[i]; })) want.forEach(function (e) { p.appendChild(e); });
  }

  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('oblDyn'));
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(900px,96vw)' : '';
  }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__obl) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__obl = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (fn.indexOf('oblRun') === 0 ? ' data-no-busy="1"' : '') + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  var RED = ';border-color:rgba(248,113,113,.45);color:var(--red)';
  window.oblSub = function (fn) { window._saReturn = oblOpen; fn(); };
  window.oblCreds = function () { oblSub(function () { if (typeof window.pxCredentials === 'function') window.pxCredentials(PIPE); }); };
  window.oblEmail = function () { oblSub(function () { if (typeof window.pxConfig === 'function') window.pxConfig(PIPE); }); };
  window.oblLogs = function () { oblSub(function () { if (typeof window.pxLogs === 'function') window.pxLogs(PIPE); }); };

  function oblOpen() {
    window._saReturn = null;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 8px">' +
        tb('⬆️ Upload MAIN file', 'oblUpload()', 'Only TODAY\'s MAIN file is taken (a name like MAIN_11.30.csv - the time in the name is the time shown in the mail). Today\'s date in the name, or - if the name has no date - a file saved in the last 30 minutes. Every upload is written to Logs.') +
        tb('🔑 Credentials', 'oblCreds()', 'NMASS and MCX eclear logins, the e-mail login this card sends from, the timings, the auto switch, the ATTENTION minutes and the WhatsApp numbers.') +
        tb('✉️ Email To / CC', 'oblEmail()', 'Who gets the Daily Obligation mail.') +
        tb('📦 Backup files', 'oblSub(oblBackup)', 'Pick a date or a range and download the backed-up files as one zip.') +
        tb('🗂️ Logs', 'oblLogs()', 'The full log of every run.') +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 14px">' +
        tb('▶ Run', 'oblRun()', 'Runs the next pending slot now with the uploaded MAIN file: downloads, picture, mail + WhatsApp, backup. No password.', ';background:var(--blue);color:var(--blue-d);border-color:transparent') +
        tb('✔ Manual override', 'oblSub(oblOverride)', 'Tick the slots that were already done by hand today. The counter (e.g. 2/8) then continues from the next slot.') +
        tb('⛔ Stop', 'oblStop()', 'Stops the run that is going right now.', RED) +
      '</div><div id="oblMsg"></div><div id="oblDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S.sig = '';
    paintDyn();
    load();
  }
  window.oblOpen = oblOpen;

  function say(txt, kind) {
    if (txt) toast(txt, kind);
    var el = $('oblMsg');
    if (el) el.innerHTML = txt ? '<div class="obl-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('oblMsg'); if (e) e.innerHTML = ''; }, 8000);
  }

  // ───────────────────────── render ─────────────────────────
  var ST = { idle: 'Not run yet', running: 'Running…', done: '✓ Done', failed: '✗ Failed', stopped: 'Stopped', attention: 'ATTENTION' };
  function kpi(k, v, f) { return '<div class="obl-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div></div>'; }
  function banner(d) {
    var out = '';
    if (d.status === 'attention') out += '<div class="obl-ban" style="background:rgba(245,158,11,.10);border:1px solid rgba(245,158,11,.5);color:var(--amber,#fbbf24)"><b>ATTENTION</b> - ' + (d.attention || []).map(esc).join(' · ') + ' <span style="color:var(--dim)">· no alert is sent for a late run.</span></div>';
    if (d.run_status === 'failed') out += '<div class="obl-ban" style="background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.45);color:var(--red)"><b>The last run FAILED.</b> ' + esc(d.error || '') + ' <span style="color:var(--dim)">· ' + (d.alert_sent ? 'WhatsApp alert sent' : 'alert not sent' + (d.alert_reason ? ' (' + esc(d.alert_reason) + ')' : '')) + ' · fix it and press <b>Run</b>.</span></div>';
    if (!out) out = '<div class="obl-ban" style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.35);color:var(--blue)">' + (d.auto ? 'Auto is <b>ON</b>: upload the MAIN file and the report runs at the next timing by itself.' : 'Auto is <b>OFF</b> (Credentials) - press <b>Run</b> yourself.') + ' Late runs only show here - an alert goes out only if a run FAILS.</div>';
    return out;
  }
  function kpis(d) {
    var ins = d.inputs || [], x = ins[0] || {};
    var run = d.stopping ? 'Stopping…' : d.running ? 'Running' : (ST[d.status] || d.status);
    var col = d.status === 'failed' ? 'var(--red)' : d.status === 'attention' ? 'var(--amber)' : d.status === 'done' ? 'var(--green)' : 'var(--ink)';
    return '<div class="obl-kpis">' +
      kpi('Status', '<span style="color:' + col + '">' + run + '</span>', (d.started ? 'last run ' + d.started.slice(11, 16) : 'no run yet') + (d.trigger ? ' · ' + d.trigger : '')) +
      kpi('Reports today', (d.done || 0) + '<small> / ' + (d.total || 0) + '</small>', (d.extra ? d.extra + ' extra · ' : '') + (d.next ? 'next ' + d.next.time : 'all slots passed')) +
      kpi('MAIN file', x.ok ? 'in' : '—', x.ok ? esc(x.name) : (x.why || 'not uploaded')) +
      kpi('Last report', esc(d.report_time || '—'), (d.sent || []).length ? 'sent: ' + (d.sent || []).join(' + ') : 'nothing sent yet') + '</div>';
  }
  var SLOT = { done: ['✓ done', 'ok'], running: ['running…', 'running'], failed: ['✗ failed', 'failed'], stopped: ['stopped', ''], upcoming: ['upcoming', ''], waiting: ['waiting', 'wait'], attention: ['ATTENTION', 'attn'], missed: ['missed', ''] };
  function slots(d) {
    return '<div class="obl-lbl">Slots (' + (d.done || 0) + ' / ' + (d.total || 0) + ' done today)</div><div class="obl-slots">' + (d.slots || []).map(function (r) {
      var t = SLOT[r.state] || [r.state, ''];
      var sub = r.state === 'done' ? (r.override ? 'by hand' : esc(r.report_time || (r.done_at || '').slice(11, 16))) : (r.state === 'attention' || r.state === 'waiting') ? 'late ' + r.late_min + ' min' : esc(r.msg || '');
      return '<div class="obl-slot ' + t[1] + '" title="' + esc(r.msg || '') + '"><div class="n">' + r.slot + ' · ' + esc(r.time) + '</div><div class="s">' + t[0] + '</div><div class="m">' + sub + '</div></div>';
    }).join('') + '</div>';
  }
  function steps(d) {
    return '<div class="obl-lbl">Last run - steps</div><div class="obl-steps">' + (d.steps || []).map(function (s) {
      var c = s.status === 'ok' || s.status === 'skipped' || s.status === 'done' ? 'ok' : s.status === 'failed' ? 'failed' : s.status === 'running' ? 'running' : '';
      var t = { ok: '✓ done', done: '✓ in place', skipped: '✓ skipped', failed: '✗ failed', running: 'running…', stopped: 'stopped' }[s.status] || '—';
      return '<div class="obl-step ' + c + '"><div class="n">' + esc(s.label) + '</div><div class="s">' + t + '</div><div class="m" title="' + esc(s.msg) + '">' + esc(s.msg) + '</div></div>';
    }).join('') + '</div>';
  }
  function inputs(d) {
    return '<div class="obl-lbl">Input file</div><div style="margin-bottom:12px">' + (d.inputs || []).map(function (x) {
      return '<div class="obl-frow"><span style="width:18px;color:' + (x.ok ? 'var(--green)' : 'var(--red)') + '">' + (x.ok ? '✓' : '✗') + '</span><span style="width:150px;color:var(--muted)">' + esc(x.label) + '</span><span class="n">' + (x.found ? esc(x.name) : '— not there (' + esc(x.name) + ')') + '</span>' +
        '<span style="color:var(--dim);font-size:11px">' + (x.found ? kb(x.size) + ' · ' + esc(x.time) + (x.why ? ' · ' + esc(x.why) : '') : esc(x.why || '')) + '</span></div>';
    }).join('') + '</div>';
  }
  function logBox(d) {
    var lines = d.log || [];
    return '<div class="obl-lbl">Live log</div><div class="obl-logbox" id="oblLogBox">' + (lines.length ? lines.slice(-30).map(esc).join('\n') : 'No activity since the server started. The full log of every run is under 🗂️ Logs.') + '</div>';
  }
  function paintDyn(force) {
    var d = S.data, el = $('oblDyn');
    if (!el || !d || !d.steps) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig && !force) return;
    S.sig = sig;
    var old = $('oblLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    el.innerHTML = banner(d) + kpis(d) + slots(d) + steps(d) + logBox(d) + inputs(d) +
      '<div style="font-size:11px;color:var(--dim);line-height:1.6">Folder: <span style="font-family:var(--mono)">' + esc(d.folder) + '</span> · after a run the MAIN, FO, SearchResults files and the picture are kept in <span style="font-family:var(--mono)">BACKUP FILES AUTOMATED / dd-mm-yyyy / hh-mm-AM|PM</span>. The MAIN file is used once - upload the next one for the next report.</div>';
    var nb = $('oblLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }
  function paintTile(d) {
    var chip = $('oblChip'), mini = $('oblMini');
    if (!chip || !d || !d.steps) return;
    chip.textContent = d.stopping ? 'stopping' : d.running ? 'running' : d.status === 'attention' ? 'attention' : d.run_status === 'failed' ? 'failed' : (d.done + '/' + d.total);
    chip.style.color = d.running ? 'var(--blue)' : d.status === 'attention' ? 'var(--amber)' : d.run_status === 'failed' ? 'var(--red)' : d.done >= d.total ? 'var(--green)' : 'var(--dim)';
    mini.textContent = d.stopping ? 'Stopping…' : d.running ? 'Running…' : d.status === 'attention' ? (d.attention || [''])[0] : d.run_status === 'failed' ? 'Last run failed - open for details' : (d.next ? 'Next ' + d.next.time + ((d.inputs[0] || {}).ok ? ' · MAIN file is in' : ' · upload the MAIN file') : 'All slots passed today');
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
  window.oblRun = function () {
    post(BASE + '/run', {}).then(function (j) {
      if (j && j.ok) okBox('Run started', 'OBLIGATION REPORT is running - watch the steps and the live log in the window.');
      else okBox('Could not start', (j && (j.error || j.msg || j.detail)) || 'The run did not start.', 'err');
      reload();
    });
  };
  window.oblStop = function () {
    confirmBox('Stop OBLIGATION REPORT?', 'The run that is going now will be stopped. The MAIN file stays where it is.', 'Stop', function () {
      post(BASE + '/stop').then(function (j) {
        if (j && j.stopped) okBox('Stop requested', 'The run ends in a few seconds.'); else okBox('Nothing to stop', 'No run is going right now.', 'err');
        reload();
      });
    });
  };

  // ───────────────────────── Manual override: slots already done today ─────────────────────────
  window.oblOverride = function () {
    ask('Manual override', function (pw) {
      S.pw = pw;
      get(BASE + '/status').then(function (d) {
        var rows = (d.slots || []).map(function (r) {
          var real = r.state === 'done' && !r.override;
          return '<label style="display:flex;gap:10px;align-items:center;padding:7px 4px;border-bottom:1px solid var(--line);font-size:13px"><input type="checkbox" class="oblOvr" value="' + r.slot + '" ' + (r.state === 'done' ? 'checked' : '') + (real ? ' disabled' : '') + '> <span style="font-family:var(--mono)">' + r.slot + ' · ' + esc(r.time) + '</span><span style="color:var(--dim);font-size:11.5px">' + (real ? 'done by the console' : r.override ? 'marked done by hand' : '') + '</span></label>';
        }).join('');
        modal('✔ ' + TITLE + ' - manual override',
          '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:10px">Tick the slots that were <b>already done by hand today</b>. The counter goes up (e.g. 2 / 8) and the console continues from the next slot. Un-tick to undo.</div>' + rows +
          '<button class="sa-save" id="oblOvrBtn" data-no-busy="1" style="width:100%;margin-top:14px" onclick="oblOvrSave()">Save</button>');
      });
    });
  };
  window.oblOvrSave = function () {
    var boxes = [].slice.call(document.querySelectorAll('.oblOvr'));
    var pick = boxes.filter(function (c) { return c.checked; }).map(function (c) { return Number(c.value); });   // real runs are sent too (they are never changed)
    var btn = $('oblOvrBtn'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    post('/obligation/override', { password: S.pw, slots: pick }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
      if (j && j.ok) okBox('Saved', 'Done today: ' + j.done + ' / ' + j.total + '. The next run continues from the next slot.'); else okBox('Not saved', (j && j.error) || 'Could not save.', 'err');
      reload();
    });
  };

  // ───────────────────────── Credentials window extra: timings, auto, attention, numbers ─────────────────────────
  window.oblCredHtml = function (pw) {
    S.pw = pw;
    return get(BASE + '/settings').then(function (s) {
      return '<div style="border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px"><div style="font-weight:650;margin-bottom:3px">Run times &amp; alerts</div>' +
        '<div style="font-size:10.5px;color:var(--dim);margin-bottom:10px">Everything here is saved by the Save credentials button below. Relaxed times: the report runs at its time once the MAIN file is uploaded. Nothing is sent for a missing or late run - it only shows ATTENTION in the console.</div>' +
        '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:10px"><input type="checkbox" id="oblAuto" ' + (s.auto !== false ? 'checked' : '') + '> Auto run (timer) is ON</label>' +
        '<div class="obl-lbl">Times (24-hour, comma separated - one report each)</div><input type="text" id="oblTimes" class="obl-in" style="width:100%;margin-bottom:10px" value="' + esc(s.times || '') + '">' +
        '<div class="obl-lbl">Show ATTENTION when a slot is more than ... minutes late</div><input type="number" id="oblAttn" class="obl-in" style="width:120px;margin-bottom:10px" min="10" max="600" value="' + esc(s.attention_min || 60) + '">' +
        '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:6px"><input type="checkbox" id="oblMail" ' + (s.send_mail !== false ? 'checked' : '') + '> Send the e-mail</label>' +
        '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:10px"><input type="checkbox" id="oblWa" ' + (s.send_whatsapp !== false ? 'checked' : '') + '> Send the picture on WhatsApp</label>' +
        '<div class="obl-lbl">Split time (system clock): reports sent up to this time go to the first group, after it until 00:30 to the second group</div><input type="text" id="oblSplit" class="obl-in" style="width:120px;margin-bottom:10px" value="' + esc(s.split_time || '17:00') + '">' +
        '<div class="obl-lbl">WhatsApp numbers - REPORT up to the split time (blank = the common numbers of Credentials &gt; WhatsApp)</div><input type="text" id="oblRep" class="obl-in" style="width:100%;margin-bottom:10px" placeholder="9876543210, 9123456789" value="' + esc(s.report_numbers || '') + '">' +
        '<div class="obl-lbl">WhatsApp numbers - REPORT after the split time until 00:30 (blank = same as the first group). The e-mail To / CC of this group is in Email To / CC.</div><input type="text" id="oblRepLate" class="obl-in" style="width:100%;margin-bottom:10px" placeholder="9876543210, 9123456789" value="' + esc(s.report_numbers_late || '') + '">' +
        '<div class="obl-lbl">WhatsApp numbers that get the FAILURE ALERT (blank = the common numbers)</div><input type="text" id="oblAlert" class="obl-in" style="width:100%;margin-bottom:10px" placeholder="9876543210, 9123456789" value="' + esc(s.alert_numbers || '') + '">' +
        '<div class="obl-lbl">WhatsApp template of the report</div><input type="text" id="oblTpl" class="obl-in" style="width:220px;margin-bottom:12px" value="' + esc(s.report_template || 'daily_report') + '">' +
        '</div>';
    }).catch(function () { return ''; });
  };
  // saved together with the logins by the one 'Save credentials' button of the window (dashboard.html pxSave)
  window.oblSettingsPost = function (pw) {
    return post(BASE + '/settings', { password: pw || S.pw, auto: $('oblAuto').checked, times: $('oblTimes').value, attention_min: $('oblAttn').value, send_mail: $('oblMail').checked,
      send_whatsapp: $('oblWa').checked, report_numbers: $('oblRep').value, report_numbers_late: $('oblRepLate').value, split_time: $('oblSplit').value,
      alert_numbers: $('oblAlert').value, report_template: $('oblTpl').value }).then(function (j) { reload(); return j; });
  };

  // ───────────────────────── Backup files ─────────────────────────
  window.oblBackup = function () {
    S.bk = { from: iso(0), to: iso(0) };
    modal('📦 ' + TITLE + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">After every successful run the day\'s files are kept in <span style="font-family:var(--mono)">BACKUP FILES AUTOMATED / dd-mm-yyyy</span>. Pick one day or a range.</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px"><div><div class="obl-lbl">From</div><input type="date" id="oblBkFrom" class="obl-in" style="width:100%" value="' + S.bk.from + '" onchange="oblBkLoad()"></div>' +
      '<div><div class="obl-lbl">To</div><input type="date" id="oblBkTo" class="obl-in" style="width:100%" value="' + S.bk.to + '" onchange="oblBkLoad()"></div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px"><button class="obl-chip" onclick="oblBkQuick(0)">Today</button><button class="obl-chip" onclick="oblBkQuick(1)">Yesterday</button><button class="obl-chip" onclick="oblBkQuick(7)">Last 7 days</button><button class="obl-chip" onclick="oblBkQuick(30)">Last 30 days</button></div>' +
      '<div id="oblBkList" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="oblBkGo" data-no-busy="1" style="width:100%" onclick="oblBkDownload()">⬇ Download zip</button>');
    widen(true);
    oblBkLoad();
  };
  window.oblBkQuick = function (n) { $('oblBkTo').value = n === 1 ? iso(1) : iso(0); $('oblBkFrom').value = iso(n); oblBkLoad(); };
  window.oblBkLoad = function () {
    var a = $('oblBkFrom').value, b = $('oblBkTo').value || a; if (b < a) { var x = a; a = b; b = x; }
    S.bk.from = a; S.bk.to = b;
    get(BASE + '/range?from_date=' + encodeURIComponent(a) + '&to_date=' + encodeURIComponent(b)).then(function (j) {
      var l = $('oblBkList'); if (!l) return;
      var byDay = {}; (j.files || []).forEach(function (f) { (byDay[f.date] = byDay[f.date] || []).push(f); });
      var days = Object.keys(byDay), tot = (j.files || []).length;
      l.innerHTML = tot ? days.map(function (day) {
        return '<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px"' + (days.length === 1 ? ' open' : '') + '><summary style="cursor:pointer;font-size:13px;font-weight:650">' + esc(day) + ' <span style="font-weight:400;color:var(--dim)">· ' + byDay[day].length + ' files</span></summary>' +
          byDay[day].map(function (f) { return '<div class="obl-frow"><span class="n">' + esc(f.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span><a href="' + API + BASE + '/file?date=' + encodeURIComponent(day) + '&name=' + encodeURIComponent(f.name) + '" download>⬇</a></div>'; }).join('') + '</details>';
      }).join('') : '<div style="color:var(--dim);font-size:13px;padding:8px 0">No backed-up files for that ' + (a === b ? 'day' : 'range') + '.</div>';
      var go = $('oblBkGo'); if (go) { go.disabled = !tot; go.style.opacity = tot ? '1' : '.45'; }
    });
  };
  window.oblBkDownload = function () { toast('Preparing the zip - the download starts in a moment.'); window.location.href = API + BASE + '/zip-range?from_date=' + encodeURIComponent(S.bk.from) + '&to_date=' + encodeURIComponent(S.bk.to); };

  // ───────────────────────── Upload MAIN file ─────────────────────────
  window.oblUpload = function () {
    var old = $('oblUpInput'); if (old) old.remove();
    var inp = document.createElement('input'); inp.type = 'file'; inp.id = 'oblUpInput'; inp.accept = '.csv'; inp.style.display = 'none';
    inp.onchange = function () { var f = inp.files && inp.files[0]; inp.remove(); if (f) oblUpSend(f); };
    document.body.appendChild(inp); inp.click();
  };
  function oblUpSend(file) {
    toast('Uploading ' + file.name + '…', 'warn');
    var fd = new FormData(); fd.append('files', file); fd.append('modified', String(file.lastModified || ''));      // the file's own saved time: the server checks it when the name has no date
    fetch(API + BASE + '/upload', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.error || j.detail) { okBox('Upload failed', String(j.error || j.detail), 'err'); return; }
      if ((j.ok || []).length) okBox('MAIN file uploaded', 'Saved as ' + j.ok[0] + '. The report runs at the next timing by itself (or press Run).');
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
