/* PHYSICAL DELIVERY — a card in the Process Center's "Processing & Reports" row.
   Same buttons as the other processes (Email, WhatsApp, Credentials, Upload, Backup, Logs) but with its own
   Run popup (CASH %), the 5-days-before-expiry window shown as information only (Run is never blocked), and a
   clear list of the files the next run will use.  Backend: /pd/* (api.py) + /px/* (generic).  Injected at serve time. */
(function () {
  'use strict';
  if (window.__physdelInjected) return;
  window.__physdelInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var PIPE = 'physdel';
  var PIPE_FILE = 'physdelfile';
  var S = { d: null, es: null, running: false, steps: [], ok: null, pipe: 'physdel' };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"]/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]; }); }
  function when(iso) { return String(iso || '').replace('T', ' ').slice(0, 16); }
  function dmy(iso) { var p = String(iso || '').slice(0, 10).split('-'); return p.length === 3 ? p[2] + '-' + p[1] + '-' + p[0] : ''; }
  function jget(u) { return fetch(API + u, { cache: 'no-store' }).then(function (r) { return r.json(); }); }
  function jpost(u, body) {
    return fetch(API + u, { method: 'POST', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json(); });
  }
  function ask(what, cb) { if (typeof window.saAskPassword === 'function') window.saAskPassword(what, cb); }
  function modal(t, h) { if (typeof window.saShowModal === 'function') window.saShowModal(t, h); }
  function closeModal() { if (typeof window.saCloseModal === 'function') window.saCloseModal(); }

  // ───────────────────────── tile (small card, like MTF Impact Cost / Allocation Report) ─────────────────────────
  var TITLE = 'PHYSICAL DELIVERY';
  function build() {
    var slot = $('physdelSlot');
    if (!slot) return false;
    if ($('task-physdel')) return true;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-physdel';
    tile.style.cssText = '--tile-accent:var(--amber);cursor:pointer';
    tile.onclick = function () { pdOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">📦</div>' +
      '<div class="tile-name">PHYSICAL DELIVERY <span class="st" id="pdChip">idle</span></div>' +
      '<div class="tile-desc">Prices the physical-delivery stocks, then e-mails every branch, region and zone and WhatsApps the clients. Runs 5 trading days before the last Tuesday, many times on that Tuesday.</div>' +
      '<div class="tile-desc" id="pdMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:var(--amber);color:#1a0d00" onclick="event.stopPropagation();pdOpen()" data-tip="Open PHYSICAL DELIVERY: run, upload files, e-mail, WhatsApp, credentials, backup, logs.">Open</button></div>';
    slot.parentNode.insertBefore(tile, slot);
    return true;
  }
  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE);
  }

  function fileRow(label, info, extra) {
    var ok = !!info;
    return '<div style="display:flex;gap:8px;align-items:baseline;padding:3px 0">' +
      '<span style="width:16px;color:' + (ok ? 'var(--green)' : 'var(--red)') + '">' + (ok ? '✓' : '✗') + '</span>' +
      '<span style="width:150px;color:var(--dim);flex:none;font-size:12px">' + esc(label) + '</span>' +
      '<span style="flex:1;font-family:var(--mono);font-size:11px;color:' + (ok ? 'var(--ink)' : 'var(--red)') + '">' +
      (ok ? esc(info.name) + ' <span style="color:var(--dim)">— ' + esc(info.where) + (info.mtime ? ', saved ' + esc(when(info.mtime)) : '') + '</span>' : esc(extra || 'not found')) +
      '</span></div>';
  }
  function filesHtml(d) {
    var i = d.inputs || {};
    var h = '<div style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 4px">Files the next run will use</div>';
    var st = i.positionbook_stale;
    h += fileRow('PositionBook', i.positionbook, st ? (st.name + ' is ' + st.age_min + ' min old — only the last ' + st.max_min + ' minutes are used. Upload a fresh one.') : 'not uploaded yet — use Upload files');
    h += fileRow('CTCL vs NMASS output', i.ctcl, 'no CTCL vs NMASS backup found');
    h += fileRow('Risk report', i.risk, 'today\'s RISK_REPORT missing — use Upload Common Files (top of page)');
    (i.masters || []).forEach(function (m) {            // master files are shown only when one is missing
      if (!m.present) h += fileRow('Master — ' + m.label, null, m.name + ' missing — upload under Upload files › Master files');
    });
    return h;
  }
  function lbl(t) { return '<div style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 6px">' + t + '</div>'; }

  // ───────────────────────── the window (all buttons + a big Run button) ─────────────────────────
  function dynHtml() {
    var d = S.d || {}, w = d.window || {}, st = d.safe_test || {}, miss = d.missing || [];
    var col = w.phase === 'expiry_day' ? 'var(--red)' : (w.phase === 'window' ? 'var(--green)' : 'var(--muted)');
    var h = '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px"><span style="color:' + col + '">●</span> ' + esc(w.text || 'Checking…') +
      (d.last_run ? '<br><span style="color:var(--dim)">Last run ' + esc(dmy(d.last_run.date)) + ' ' + esc(d.last_run.time) + (d.last_run.ok ? ' — ok' : ' — with errors') + (d.last_run.test ? ' (test)' : '') + ', CASH ' + esc(d.last_run.percent) + '%</span>' : '') + '</div>';
    if (st.armed) h += '<div style="margin-bottom:12px;padding:9px 12px;border-radius:9px;background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.4);color:#fbbf24;font-size:12.5px;font-weight:700">🧪 SAFE TEST ARMED — the next Run sends only to ' + esc((st.email_to || []).join(', ')) + ' / ' + esc(st.wa_to || '') + '. It switches itself off after that run.</div>';
    h += '<div style="margin:0 0 12px;padding:10px 12px;border:1px dashed var(--line-2);border-radius:10px">' + filesHtml(d) + '</div>';
    return h;
  }
  function pdOpen() {
    window._saReturn = null;
    var INP = 'style="background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit;width:110px"';
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 14px">' +
        '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap" onclick="pdSub(function(){pxConfig(\'physdel\')})">✉️ Email To / CC</button>' +
        '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap" onclick="pdSub(pdWa)">📱 WhatsApp numbers</button>' +
        '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap" onclick="pdSub(function(){pxCredentials(\'physdel\')})">🔑 Credentials</button>' +
        '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap" onclick="pdSub(pdUpload)">⬆️ Upload files</button>' +
        '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap" onclick="pdSub(function(){pxBackup(\'physdel\')})">📦 Backup files</button>' +
        '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap" onclick="pdSub(function(){pxLogs(\'physdel\')})">🗂️ Logs</button>' +
        '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap" onclick="pdSub(pdSafeTest)">🧪 Safe test run</button>' +
        '<button class="sa-tool" onclick="pdStop()" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap;border-color:rgba(248,113,113,.45);color:var(--red)">⛔ Stop</button>' +
      '</div>' +
      '<div id="pdDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>' +
      '<button id="pdRunBtn" onclick="pdAskPct()" style="width:100%;border:0;border-radius:12px;padding:15px;font-size:15px;font-weight:700;cursor:pointer;background:#34e3a5;color:#04261a">▶ Run PHYSICAL DELIVERY</button>' +
      '<button id="pdFileBtn" onclick="pdAskPct(\'file\')" style="width:100%;margin-top:10px;border:1px solid rgba(96,165,250,.55);border-radius:12px;padding:12px;font-size:14px;font-weight:700;cursor:pointer;background:rgba(96,165,250,.12);color:var(--blue)">📄 Create Full File &amp; Email to RMS Team</button>' +
      '<div style="font-size:11.5px;color:var(--dim);margin:6px 2px 0;text-align:center">Makes the complete file only and e-mails it to the RMS team. Nothing is sent to branches, regions, zones or clients. After the e-mail, the PositionBook is backed up and cleared.</div>' +
      '<div class="sa-msg" id="pdRunMsg"></div>' +
      '<div class="sa-status" id="pxStatus-physdel" style="margin-top:12px">' + '<div id="pdSteps">' + stepsHtml() + '</div><div id="pxEvents-physdel"></div>' + (!S.running && !S.steps.length ? '<div class="sa-status-empty">Run status appears here. The full output is saved in Logs.</div>' : '') + '</div>');
    paintDyn();
    refresh();
  }
  window.pdOpen = pdOpen;
  window.pdSub = function (fn) { window._saReturn = pdOpen; fn(); };
  function stepsHtml() {
    if (!S.steps.length) return S.running ? '<div style="color:var(--muted);font-size:12px">Starting…</div>' : '';
    return S.steps.map(function (s, i) {
      var cur = S.running && i === S.steps.length - 1;
      var ic = cur ? '<span class="pdSpin" style="color:var(--amber);margin-right:0"></span>' : (S.ok === false && i === S.steps.length - 1 ? '<span style="color:var(--red)">✗</span>' : '<span style="color:var(--green)">✓</span>');
      return '<div style="display:flex;gap:8px;font-size:12.5px;padding:3px 0;color:' + (cur ? 'var(--ink)' : 'var(--muted)') + '">' + ic + '<span>' + esc(s) + (cur ? '…' : '') + '</span></div>';
    }).join('');
  }
  function paintSteps() { var e = $('pdSteps'); if (e) e.innerHTML = stepsHtml(); paintDyn(); }
  function paintDyn() {
    var e = $('pdDyn'); if (e && modalOpen()) e.innerHTML = dynHtml();
    var last = S.steps.length ? S.steps[S.steps.length - 1] : 'starting', isFile = S.pipe === PIPE_FILE;
    var runTxt = ((S.d || {}).safe_test || {}).armed ? '🧪 Run as safe test' : '▶ Run PHYSICAL DELIVERY';
    var b = $('pdRunBtn');
    if (b) {
      b.disabled = S.running; b.style.cursor = S.running ? 'default' : 'pointer';
      if (S.running && !isFile) { b.style.opacity = '1'; b.innerHTML = '<span class="pdSpin"></span>Running — ' + esc(last) + '…'; }
      else { b.style.opacity = S.running ? '.45' : '1'; b.innerHTML = runTxt; }
    }
    var f = $('pdFileBtn');
    if (f) {
      f.disabled = S.running; f.style.cursor = S.running ? 'default' : 'pointer';
      if (S.running && isFile) { f.style.opacity = '1'; f.innerHTML = '<span class="pdSpin"></span>Making the full file — ' + esc(last) + '…'; }
      else { f.style.opacity = S.running ? '.45' : '1'; f.innerHTML = '📄 Create Full File &amp; Email to RMS Team'; }
    }
  }

  function refresh() {
    jget('/pd/status').then(function (d) {
      S.d = d;
      var w = d.window || {}, el = $('pdMini');
      if (el && !S.running) el.textContent = (w.phase === 'expiry_day' ? 'Expiry day — run as needed' : (w.phase === 'window' ? 'Window open — ' + (w.runs_today ? 'run today' : 'not run today') : 'Not due — window opens ' + dmy(w.window_start))) + (d.safe_test && d.safe_test.armed ? ' · 🧪 test armed' : '');
      paintDyn();
    }).catch(function () {});
    jget('/px/status?pipe=' + PIPE).then(function (s) { if (!S.es) { if (s && s.running) S.pipe = PIPE; setRunning(!!(s && s.running)); } }).catch(function () {});
    jget('/px/status?pipe=' + PIPE_FILE).then(function (s) { if (!S.es && s && s.running) { S.pipe = PIPE_FILE; setRunning(true); } }).catch(function () {});
  }
  function setRunning(on) {
    S.running = on;
    var c = $('pdChip'); if (c) { c.textContent = on ? 'running' : 'idle'; c.style.color = on ? 'var(--amber)' : 'var(--dim)'; }
    var m = $('pdMini'); if (m && on) m.textContent = 'Running… open to follow it';
    paintDyn();
  }

  // ───────────────────────── Run ─────────────────────────
  // The CASH % is asked in a popup every time Run is pressed - nobody can forget to fill it.
  window.pdAskPct = function (mode) {
    if (S.running) return;
    var isFile = mode === 'file';
    var last = isFile ? (S.d || {}).file_percent : (S.d || {}).percent, armed = !isFile && ((S.d || {}).safe_test || {}).armed;
    window._saReturn = pdOpen;
    modal(isFile ? 'Full file - CASH %' : 'CASH % for this run',
      '<div style="padding:16px;border-radius:14px;border:2px solid var(--amber);background:rgba(251,191,36,.08);margin-bottom:14px">' +
        '<div style="font-size:15px;font-weight:700;color:var(--amber);margin-bottom:4px">Enter the CASH % for this run</div>' +
        (isFile ? '<div style="font-size:12px;color:var(--blue);margin-bottom:6px">This only creates the full file and e-mails it to the RMS team. Nothing goes to branches or clients.</div>' : '') +
        '<div style="font-size:12.5px;color:var(--muted);margin-bottom:12px">How much the price is moved up / down. Any number from 0 (like 0, 0.5 or 2). Last time you used <b style="color:var(--ink)">' + esc(last) + '%</b>.</div>' +
        '<div style="display:flex;gap:10px;align-items:center"><input id="pdPct" type="number" step="0.1" min="0" max="100" placeholder="e.g. 2" ' +
          'style="flex:1;font-size:26px;font-weight:700;text-align:center;padding:12px;border-radius:12px;border:2px solid var(--amber);background:transparent;color:var(--ink)" onkeydown="if(event.keyCode===13)pdStart(' + (isFile ? '\'file\'' : '') + ')">' +
          '<span style="font-size:26px;font-weight:700;color:var(--amber)">%</span></div>' +
        '<div style="margin-top:10px"><button class="sa-tool" onclick="document.getElementById(\'pdPct\').value=' + esc(last) + '">Use last value (' + esc(last) + '%)</button></div>' +
      '</div>' +
      (armed ? '<div style="margin-bottom:12px;color:#fbbf24;font-size:12.5px;font-weight:700">🧪 Safe test is armed — this run sends only to the test address.</div>' : '') +
      '<button onclick="pdStart(' + (isFile ? '\'file\'' : '') + ')" style="width:100%;border:0;border-radius:12px;padding:14px;font-size:15px;font-weight:700;cursor:pointer;background:#34e3a5;color:#04261a">' + (isFile ? '📄 Create file &amp; email it' : (armed ? '🧪 Start test run' : '▶ Start run')) + '</button>' +
      '<div class="sa-msg" id="pdRunMsg"></div>');
    setTimeout(function () { var e = $('pdPct'); if (e) e.focus(); }, 60);
  };
  window.pdStart = function (mode) {
    if (S.running) return;
    var isFile = mode === 'file';
    var v = ($('pdPct') || {}).value, msg = $('pdRunMsg');
    if (msg) msg.innerHTML = '<span style="color:var(--muted)">Starting…</span>';
    jpost((isFile ? '/pd/file-percent' : '/pd/percent') + '?value=' + encodeURIComponent(v || '')).then(function (r) {
      if (!r.ok) { if (msg) msg.innerHTML = '<span style="color:var(--red)">' + esc(r.error || 'Enter a number like 2') + '</span>'; return; }
      window._saReturn = null; S.pipe = isFile ? PIPE_FILE : PIPE; pdOpen(); streamRun();
    }).catch(function () { if (msg) msg.innerHTML = '<span style="color:var(--red)">Could not reach the backend.</span>'; });
  };
  function claim() { return typeof window._pxMarkRunning === 'function' ? window._pxMarkRunning(S.pipe) : true; }
  function release() { if (typeof window._pxMarkDone === 'function') window._pxMarkDone(S.pipe); }
  function streamRun() {
    if (!claim()) return;
    setRunning(true);
    var st = $('pxStatus-' + PIPE);
    S.steps = []; S.ok = null;
    if (st) st.innerHTML = '<div id="pdSteps"></div><div id="pxEvents-' + PIPE + '"></div>';
    paintSteps();
    var ev = new EventSource(API + '/px/run-stream?pipe=' + S.pipe);
    S.es = ev;
    if (typeof window._pxSetES === 'function') window._pxSetES(S.pipe, ev);
    ev.onmessage = function (m) {
      var d; try { d = JSON.parse(m.data); } catch (e) { return; }
      if (d.type === 'note') { if (S.steps[S.steps.length - 1] !== d.msg) S.steps.push(d.msg); paintSteps(); }
      else if (typeof window.pxHandle === 'function') window.pxHandle(PIPE, d);
      if (d.type === 'done') {
        window._pxRunLog = window._pxRunLog || {};
        window._pxRunLog[PIPE] = { log: d.log || null, hasError: d.ok === false, err: d.err || '', ts: new Date().toISOString() };
        if (typeof window.pxInjectViewBtn === 'function') { try { window.pxInjectViewBtn(PIPE); } catch (e) {} }
      }
      if (d.type === 'end' || d.type === 'done') {
        ev.close(); S.es = null;
        if (typeof window._pxClearES === 'function') window._pxClearES(S.pipe);
        release(); setRunning(false);
        S.ok = d.ok !== false;
        S.steps.push(S.ok ? 'Finished' : 'Finished with errors — open Logs to see why');
        paintSteps();
        refresh();
      }
    };
    ev.onerror = function () {
      ev.close(); S.es = null;
      if (typeof window._pxClearES === 'function') window._pxClearES(S.pipe);
      release(); setRunning(false);
      if (typeof window.pxEv === 'function') window.pxEv(PIPE, false, 'Connection lost — is the backend running?', 'stream');
    };
  }
  window.pdStop = function () {
    try { if (S.es) { S.es.close(); S.es = null; } } catch (e) {}
    if (typeof window._pxCloseES === 'function') { try { window._pxCloseES(S.pipe); } catch (e) {} }
    fetch(API + '/px/stop?pipe=' + S.pipe, { method: 'POST' }).catch(function () {});
    release(); setRunning(false);
    if (typeof window.pxEv === 'function') window.pxEv(PIPE, false, 'Process terminated by user', 'stopped');
  };

  // ───────────────────────── Safe test ─────────────────────────
  window.pdSafeTest = function () {
    jget('/pd/safe-test').then(function (st) {
      if (st.armed) {
        modal('🧪 Safe test run', '<div style="font-size:13px;color:var(--muted);line-height:1.6;margin-bottom:16px">A safe test is <b style="color:#fbbf24">already armed</b>. The next Run is a test, then it switches itself off.</div>' +
          '<button class="sa-save" style="width:100%" onclick="pdDisarm()">Cancel the armed test</button>');
        return;
      }
      modal('🧪 Safe test run — one run only',
        '<div style="font-size:13px;color:var(--muted);line-height:1.6;margin-bottom:12px">Runs the <b style="color:var(--ink)">complete, real</b> process. Only the destinations change:</div>' +
        '<div style="border:1px solid var(--line);border-radius:11px;padding:12px 14px;margin-bottom:12px;font-size:12.5px;line-height:1.9">' +
        '<div>✉️ Every e-mail → <b>' + esc((st.email_to || []).join(', ') || 'NOT SET') + '</b> (CC dropped, subject “[TEST - IGNORE]”)</div>' +
        '<div>📲 Client WhatsApp and alert pictures → <b>' + esc(st.wa_to || 'NOT SET') + '</b> (first ' + esc(st.max_clients) + ' clients only)</div>' +
        '<div>📦 Backup → <b>skipped</b> (today\'s files stay for the real run)</div></div>' +
        '<div style="font-size:11.5px;color:var(--dim);margin-bottom:12px">Change the targets in <code>.env</code>: <code>PD_TEST_EMAIL_TO</code>, <code>PD_TEST_WA_TO</code>.</div>' +
        '<div class="sa-field"><label>Authorisation password</label><input type="password" id="pdStPw" onkeydown="if(event.keyCode===13)pdArm()"></div>' +
        '<button class="sa-save" style="width:100%" onclick="pdArm()">Arm the next run as a safe test</button><div class="sa-msg" id="pdStMsg"></div>');
      setTimeout(function () { var e = $('pdStPw'); if (e) e.focus(); }, 60);
    });
  };
  window.pdArm = function () {
    var pw = ($('pdStPw') || {}).value || '', m = $('pdStMsg');
    jpost('/pd/safe-test/arm?password=' + encodeURIComponent(pw)).then(function (r) {
      if (!r.ok) { if (m) m.innerHTML = '<span style="color:var(--red)">' + esc(r.error || 'Failed') + '</span>'; return; }
      closeModal(); refresh();
    });
  };
  window.pdDisarm = function () { jpost('/pd/safe-test/disarm').then(function () { closeModal(); refresh(); }); };

  // ───────────────────────── WhatsApp numbers ─────────────────────────
  window.pdWa = function () {
    ask('WhatsApp numbers', function (pw) {
      jget('/pd/wa-config').then(function (d) {
        window._pdPw = pw;
        var h = '<div style="font-size:12.5px;color:var(--muted);margin-bottom:10px">10-digit mobile numbers, separated by commas (max 10 per list). These get the 1 Cr alert pictures.</div>' +
          d.lists.map(function (l) {
            return '<div class="sa-field"><label>' + esc(l.label) + '</label><input data-k="' + esc(l.key) + '" class="pdWaIn" value="' + esc(l.value) + '"></div>';
          }).join('') +
          '<button class="sa-save" style="width:100%" onclick="pdWaSave()">Save</button><div class="sa-msg" id="pdWaMsg"></div>';
        modal('📱 PHYSICAL DELIVERY — WhatsApp numbers', h);
      });
    });
  };
  window.pdWaSave = function () {
    var lists = {};
    [].forEach.call(document.querySelectorAll('.pdWaIn'), function (i) { lists[i.getAttribute('data-k')] = i.value; });
    jpost('/pd/wa-config', { password: window._pdPw || '', lists: lists }).then(function (r) {
      var m = $('pdWaMsg'); if (m) m.innerHTML = r.ok ? '<span style="color:var(--green)">Saved.</span>' : '<span style="color:var(--red)">' + esc(r.error || 'Failed') + '</span>';
    });
  };

  // ───────────────────────── Upload files ─────────────────────────
  var UP = { target: 'main' };
  window.pdUpload = function () {
    var d = S.d || {}, i = d.inputs || {}, f = i.folders || {};
    modal('⬆️ PHYSICAL DELIVERY — upload files',
      '<div style="font-size:12.5px;color:var(--muted);margin-bottom:12px;line-height:1.55">Choose where the file belongs. The CTCL vs NMASS output is <b>not</b> uploaded — each run takes it from the latest CTCL vs NMASS backup.</div>' +
      '<div style="display:flex;gap:10px;flex-wrap:wrap">' +
      '<button class="sa-save" style="flex:1;min-width:170px;background:var(--blue);color:var(--blue-d)" onclick="pdPick(\'main\')">📁 PositionBook</button>' +
      '<button class="sa-save" style="flex:1;min-width:170px;background:var(--violet);color:var(--violet-d)" onclick="pdPick(\'master\')">📁 Master files</button>' +
      '</div>' +
      '<div style="font-size:10.5px;color:var(--dim);margin-top:12px;font-family:var(--mono);line-height:1.8">PositionBook → ' + esc(f.input || '') + '<br>Master files → ' + esc(f.master || '') +
      '</div>' +
      '<div style="margin-top:12px;padding:10px 14px;border:1px dashed var(--line-2);border-radius:10px;font-size:11.5px;line-height:1.9">' +
      '<b style="color:var(--ink);font-size:10.5px;letter-spacing:.5px;text-transform:uppercase">Accepted names</b><br>' +
      '<span style="font-family:var(--mono);color:var(--dim)">PositionBook*.csv / PositionBook*.xlsx</span><br>' +
      ((i.masters || []).map(function (m) { return '<span style="font-family:var(--mono);color:var(--teal)">' + esc(m.name) + '</span> <span style="color:var(--dim)">(' + esc(m.label) + ')</span>'; }).join('<br>')) +
      '</div><div style="font-size:11.5px;color:var(--amber);margin-top:10px">Risk report: upload it with the <b>Upload Common Files</b> button at the top of Process Center (it is read from COMMON FILES).</div>');
  };
  window.pdPick = function (target) {
    UP.target = target;
    var inp = $('pdFileInput');
    if (!inp) {
      inp = document.createElement('input'); inp.type = 'file'; inp.multiple = true; inp.id = 'pdFileInput'; inp.style.display = 'none';
      inp.onchange = function () { var l = Array.prototype.slice.call(inp.files); inp.value = ''; if (l.length) pdSend(l, ''); };
      document.body.appendChild(inp);
    }
    closeModal();
    inp.click();
  };
  function pdSend(files, pw) {
    var names = { main: 'PositionBook (INPUT) folder', master: 'master files folder', common: 'COMMON FILES folder' };
    modal('Uploading…', '<div style="color:var(--muted);font-size:13px">Copying ' + files.length + ' file(s) into the ' + names[UP.target] + '…</div>');
    var fd = new FormData(); files.forEach(function (f) { fd.append('files', f); }); fd.append('password', pw || '');
    fetch(API + '/px/upload?pipe=' + PIPE + '&target=' + encodeURIComponent(UP.target), { method: 'POST', body: fd })
      .then(function (r) { if (!r.ok) throw new Error('Server ' + r.status); return r.json(); })
      .then(function (d) {
        if ((d.password_required || []).length) {
          ask('Replace master file — ' + d.password_required.join(', '), function (p) { pdSend(files, p); });
          return;
        }
        var h = '<div style="font-size:12.5px;color:var(--muted);margin-bottom:10px">Folder: <span style="font-family:var(--mono);color:var(--ink)">' + esc(d.dest || '') + '</span></div>';
        (d.ok || []).forEach(function (m) { h += '<div class="sa-ev ok"><span class="ic">✓</span><span>' + esc(m) + '</span><span class="what">copied</span></div>'; });
        (d.skipped || []).forEach(function (m) { h += '<div class="sa-ev bad"><span class="ic">!</span><span>' + esc(m) + '</span><span class="what">skipped</span></div>'; });
        (d.rejected || []).forEach(function (m) { h += '<div class="sa-ev bad"><span class="ic">✕</span><span>' + esc(m) + '</span><span class="what">not accepted</span></div>'; });
        if ((d.rejected || []).length) h += '<div style="margin-top:10px;font-size:12px;color:var(--muted)">Accepted here:<br>' + (d.valid || []).map(function (v) { return '<span style="font-family:var(--mono);color:var(--teal)">' + esc(v) + '</span>'; }).join('<br>') + '</div>';
        h += '<button class="sa-save" style="width:100%;margin-top:14px" onclick="saCloseModal()">Done</button>';
        modal((d.rejected || []).length ? 'Upload result' : '✓ Upload complete', h);
        refresh();
      }).catch(function (e) { modal('Upload failed', '<div style="color:var(--red)">' + esc(e.message || e) + '</div>'); });
  }

  // ───────────────────────── start ─────────────────────────
  function init() {
    if (!document.getElementById('pdSpinCss')) { var st = document.createElement('style'); st.id = 'pdSpinCss'; st.textContent = '@keyframes pdSpin{to{transform:rotate(360deg)}}.pdSpin{display:inline-block;width:14px;height:14px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:pdSpin .8s linear infinite;vertical-align:-2px;margin-right:8px}'; document.head.appendChild(st); }
    if (!build()) { setTimeout(init, 250); return; }
    refresh();
    setInterval(function () { if (!S.running) refresh(); }, 15000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
