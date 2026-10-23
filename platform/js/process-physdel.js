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
  var S = { d: null, es: null, running: false };

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

  // ───────────────────────── card ─────────────────────────
  var BTN = 'class="sa-tool" ';
  function build() {
    var slot = $('physdelSlot');
    if (!slot) return false;
    if ($('task-physdel')) return true;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-physdel';
    tile.style.cssText = '--tile-accent:var(--amber);grid-column:1/-1;min-height:0;transform:none';
    tile.innerHTML =
      '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">' +
        '<div class="tile-icon" style="margin:0">📦</div>' +
        '<div class="tile-name" style="margin:0;font-size:16px">PHYSICAL DELIVERY <span class="st" id="pdChip">idle</span></div>' +
        '<div id="pdWindow" style="font-size:12px;color:var(--muted);flex:1;min-width:240px">Checking…</div>' +
      '</div>' +
      '<div class="tile-desc">Prices the physical-delivery stocks, then e-mails every branch, region and zone and WhatsApps the clients and the 1 Cr alert. ' +
        'Run once a day in the 5 trading days before the last Tuesday, and as often as needed on that Tuesday.</div>' +
      '<div id="pdTest" style="display:none;font-size:12px;font-weight:700;padding:8px 12px;border-radius:9px;background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.4);color:#fbbf24"></div>' +
      '<div id="pdFiles" style="font-size:12px"></div>' +
      '<div class="sa-tools" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:2px">' +
        '<button ' + BTN + 'id="pdRunBtn" onclick="pdRun()" style="background:var(--amber);color:#1a0d00;border-color:transparent;font-weight:700" data-tip="Asks the CASH % and starts the run. Never blocked by the date.">▶ Run</button>' +
        '<button ' + BTN + 'onclick="pdSafeTest()" data-tip="One rehearsal: everything really runs but every mail / WhatsApp goes only to the test address.">🧪 Safe test run</button>' +
        '<button ' + BTN + 'onclick="pxConfig(\'physdel\')" data-tip="Zone and log e-mail addresses (To / CC).">✉️ Email To / CC</button>' +
        '<button ' + BTN + 'onclick="pdWa()" data-tip="WhatsApp numbers that get the 1 Cr alert pictures (Delhi, Ludhiana, Chandigarh, Mumbai, full list).">📱 WhatsApp numbers</button>' +
        '<button ' + BTN + 'onclick="pxCredentials(\'physdel\')" data-tip="Mail account, WhatsApp token, webpayout login, Excel password.">🔑 Credentials</button>' +
        '<button ' + BTN + 'onclick="pdUpload()" data-tip="PositionBook, master files, risk report.">⬆️ Upload files</button>' +
        '<button ' + BTN + 'onclick="pxBackup(\'physdel\')" data-tip="Past runs\' files.">📦 Backup files</button>' +
        '<button ' + BTN + 'onclick="pxLogs(\'physdel\')" data-tip="Saved output of past runs.">🗂️ Logs</button>' +
        '<button ' + BTN + 'onclick="pdStop()" style="border-color:rgba(248,113,113,.45);color:var(--red)">⛔ Stop</button>' +
      '</div>' +
      '<div class="sa-status" id="pxStatus-physdel"><div class="sa-status-empty">Run status appears here. The full output is saved in Logs.</div></div>';
    slot.parentNode.insertBefore(tile, slot);
    return true;
  }

  function fileRow(label, info, extra) {
    var ok = !!info;
    return '<div style="display:flex;gap:8px;align-items:baseline;padding:3px 0">' +
      '<span style="width:16px;color:' + (ok ? 'var(--green)' : 'var(--red)') + '">' + (ok ? '✓' : '✗') + '</span>' +
      '<span style="width:170px;color:var(--dim);flex:none">' + esc(label) + '</span>' +
      '<span style="flex:1;font-family:var(--mono);font-size:11.5px;color:' + (ok ? 'var(--ink)' : 'var(--red)') + '">' +
      (ok ? esc(info.name) + ' <span style="color:var(--dim)">— ' + esc(info.where) + (info.mtime ? ', saved ' + esc(when(info.mtime)) : '') + '</span>' : esc(extra || 'not found')) +
      '</span></div>';
  }
  function filesHtml(d) {
    var i = d.inputs || {};
    var h = '<div style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:2px 0 4px">Files the next run will use</div>';
    h += fileRow('PositionBook', i.positionbook, 'not uploaded yet — use Upload files');
    h += fileRow('CTCL vs NMASS output', i.ctcl, 'no CTCL vs NMASS backup found');
    h += fileRow('Risk report', i.risk, 'today\'s RISK_REPORT is not in COMMON FILES');
    (i.masters || []).forEach(function (m) {
      h += fileRow('Master — ' + m.label, m.present ? { name: m.name, where: 'master files', mtime: m.mtime } : null, m.name + ' missing — upload it under Upload files › Master files');
    });
    return h;
  }

  function refresh() {
    if (!$('task-physdel')) return;
    jget('/pd/status').then(function (d) {
      S.d = d;
      var w = d.window || {}, col = w.phase === 'expiry_day' ? 'var(--red)' : (w.phase === 'window' ? 'var(--green)' : 'var(--muted)');
      var el = $('pdWindow');
      if (el) el.innerHTML = '<span style="color:' + col + '">●</span> ' + esc(w.text || '') +
        (d.last_run ? ' <span style="color:var(--dim)">· last run ' + esc(dmy(d.last_run.date)) + ' ' + esc(d.last_run.time) + (d.last_run.ok ? ' ok' : ' with errors') + (d.last_run.test ? ' (test)' : '') + ', CASH ' + esc(d.last_run.percent) + '%</span>' : '');
      var f = $('pdFiles'); if (f) f.innerHTML = filesHtml(d);
      var t = $('pdTest'), st = d.safe_test || {};
      if (t) { t.style.display = st.armed ? 'block' : 'none'; if (st.armed) t.textContent = '🧪 SAFE TEST ARMED — the next Run sends only to ' + (st.email_to || []).join(', ') + ' / ' + (st.wa_to || '') + '. It switches itself off after that run.'; }
    }).catch(function () {});
    jget('/px/status?pipe=' + PIPE).then(function (s) { if (!S.es) setRunning(!!(s && s.running)); }).catch(function () {});
  }
  function setRunning(on) {
    S.running = on;
    var c = $('pdChip'); if (c) { c.textContent = on ? 'running' : 'idle'; c.style.color = on ? 'var(--amber)' : 'var(--dim)'; }
    var b = $('pdRunBtn'); if (b) { b.disabled = on; b.style.opacity = on ? '.5' : '1'; }
  }

  // ───────────────────────── Run (with the CASH % popup) ─────────────────────────
  window.pdRun = function () {
    if (S.running) return;
    jget('/pd/status').then(function (d) {
      S.d = d;
      var armed = d.safe_test && d.safe_test.armed, miss = d.missing || [];
      var h = '<div style="font-size:13px;color:var(--muted);margin-bottom:12px;line-height:1.55">' + esc((d.window || {}).text || '') + '</div>' +
        (armed ? '<div style="margin-bottom:12px;padding:9px 12px;border-radius:9px;background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.4);color:#fbbf24;font-size:12.5px;font-weight:700">🧪 Safe test is armed — this run sends only to the test address.</div>' : '') +
        '<div class="sa-field"><label>CASH % (how much the price is moved up / down)</label>' +
        '<input id="pdPct" type="number" step="0.1" min="0.1" max="100" value="' + esc(d.percent) + '" onkeydown="if(event.keyCode===13)pdStart()"></div>' +
        '<div style="margin:10px 0 4px;padding:10px 12px;border:1px dashed var(--line-2);border-radius:10px">' + filesHtml(d) + '</div>' +
        (miss.length ? '<div style="color:var(--red);font-size:12px;margin:8px 0">The run will stop at once because:<br>• ' + miss.map(esc).join('<br>• ') + '</div>' : '') +
        '<button class="sa-save" style="width:100%;margin-top:8px" onclick="pdStart()">' + (armed ? '🧪 Start test run' : '▶ Start run') + '</button>' +
        '<div class="sa-msg" id="pdRunMsg"></div>';
      modal('PHYSICAL DELIVERY — run', h);
      setTimeout(function () { var e = $('pdPct'); if (e) { e.focus(); e.select(); } }, 60);
    });
  };
  window.pdStart = function () {
    var v = ($('pdPct') || {}).value, msg = $('pdRunMsg');
    if (msg) msg.innerHTML = '<span style="color:var(--muted)">Starting…</span>';
    jpost('/pd/percent?value=' + encodeURIComponent(v || '')).then(function (r) {
      if (!r.ok) { if (msg) msg.innerHTML = '<span style="color:var(--red)">' + esc(r.error || 'Enter a number like 2') + '</span>'; return; }
      closeModal();
      streamRun();
    }).catch(function () { if (msg) msg.innerHTML = '<span style="color:var(--red)">Could not reach the backend.</span>'; });
  };
  function claim() { return typeof window._pxMarkRunning === 'function' ? window._pxMarkRunning(PIPE) : true; }
  function release() { if (typeof window._pxMarkDone === 'function') window._pxMarkDone(PIPE); }
  function streamRun() {
    if (!claim()) return;
    setRunning(true);
    var st = $('pxStatus-' + PIPE);
    if (st) st.innerHTML = '<div class="sa-prog"><span class="sa-pill running" id="pxP-' + PIPE + '">running…</span></div><div id="pxEvents-' + PIPE + '"></div>';
    var ev = new EventSource(API + '/px/run-stream?pipe=' + PIPE);
    S.es = ev;
    if (typeof window._pxSetES === 'function') window._pxSetES(PIPE, ev);
    ev.onmessage = function (m) {
      var d; try { d = JSON.parse(m.data); } catch (e) { return; }
      if (typeof window.pxHandle === 'function') window.pxHandle(PIPE, d);
      if (d.type === 'done') {
        window._pxRunLog = window._pxRunLog || {};
        window._pxRunLog[PIPE] = { log: d.log || null, hasError: d.ok === false, err: d.err || '', ts: new Date().toISOString() };
        if (typeof window.pxInjectViewBtn === 'function') { try { window.pxInjectViewBtn(PIPE); } catch (e) {} }
      }
      if (d.type === 'end' || d.type === 'done') {
        ev.close(); S.es = null;
        if (typeof window._pxClearES === 'function') window._pxClearES(PIPE);
        release(); setRunning(false);
        var pp = $('pxP-' + PIPE);
        if (pp) { pp.className = 'sa-pill ' + (d.ok === false ? 'error' : 'done'); pp.textContent = d.ok === false ? 'finished with errors' : 'done'; }
        refresh();
      }
    };
    ev.onerror = function () {
      ev.close(); S.es = null;
      if (typeof window._pxClearES === 'function') window._pxClearES(PIPE);
      release(); setRunning(false);
      if (typeof window.pxEv === 'function') window.pxEv(PIPE, false, 'Connection lost — is the backend running?', 'stream');
    };
  }
  window.pdStop = function () {
    try { if (S.es) { S.es.close(); S.es = null; } } catch (e) {}
    if (typeof window._pxCloseES === 'function') { try { window._pxCloseES(PIPE); } catch (e) {} }
    fetch(API + '/px/stop?pipe=' + PIPE, { method: 'POST' }).catch(function () {});
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
      '<button class="sa-save" style="flex:1;min-width:170px;background:var(--teal);color:#003311" onclick="pdPick(\'common\')">📁 Risk report (COMMON FILES)</button></div>' +
      '<div style="font-size:10.5px;color:var(--dim);margin-top:12px;font-family:var(--mono);line-height:1.8">PositionBook → ' + esc(f.input || '') + '<br>Master files → ' + esc(f.master || '') +
      '<br>Risk report → ' + esc(f.common || '') + '</div>' +
      '<div style="margin-top:12px;padding:10px 14px;border:1px dashed var(--line-2);border-radius:10px;font-size:11.5px;line-height:1.9">' +
      '<b style="color:var(--ink);font-size:10.5px;letter-spacing:.5px;text-transform:uppercase">Accepted names</b><br>' +
      '<span style="font-family:var(--mono);color:var(--dim)">PositionBook*.csv / PositionBook*.xlsx</span><br>' +
      ((i.masters || []).map(function (m) { return '<span style="font-family:var(--mono);color:var(--teal)">' + esc(m.name) + '</span> <span style="color:var(--dim)">(' + esc(m.label) + ')</span>'; }).join('<br>')) +
      '<br><span style="font-family:var(--mono);color:var(--dim)">RISK_REPORT_&lt;DDMMYYYY&gt;.csv</span></div>');
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
    if (!build()) { setTimeout(init, 250); return; }
    refresh();
    setInterval(function () { if (!S.running) refresh(); }, 15000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
