/* SHORT ALLOCATION - "Auto Run" window, opened from Credentials (🔑) > Auto Run. Runs start by themselves at their time (core/sa_auto.py); here you set the time and
   the Auto switch of each run (1-8), the waiting / retry numbers, and see what happened today. Alert numbers are in the
   "WhatsApp numbers" window, row "FAILURE ALERT (auto run)". Injected at serve time (api.py /legacy). */
(function () {
  'use strict';
  if (window.__saAutoInjected) return;
  window.__saAutoInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var D = null, PW = '';

  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function get(p) { return fetch(API + p, { cache: 'no-store' }).then(function (r) { return r.json(); }); }
  function post(p, b) { return fetch(API + p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) }).then(function (r) { return r.json(); }); }

  var STATE_TXT = { '': 'waiting for its time', done: '✓ done', failed: '✗ failed (alert sent)', stopped: 'stopped', skipped: 'skipped today', missed: 'missed', running: 'running…' };
  function stateText(r) {
    var s = r.state || '';
    if (STATE_TXT[s] != null) return STATE_TXT[s];
    return s;                                         // "waiting for peak file (15 min)", "retry 1"
  }

  function label() { /* the summary lives in the Credentials window (saAutoCardHtml) */ }
  function onList() {
    return D ? D.runs.filter(function (r) { return r.auto; }).map(function (r) { return r.run; }) : [];
  }
  /* the card shown at the top of the Credentials window */
  window.saAutoCardHtml = function () {
    var on = onList();
    var sum = on.length ? 'Auto is <b style="color:var(--green)">ON</b> for run ' + on.join(', ') : 'Auto is <b style="color:var(--muted)">OFF</b> for every run';
    return '<div style="border:1px solid rgba(52,211,153,.4);background:rgba(52,211,153,.06);border-radius:11px;padding:14px;margin-bottom:12px">' +
      '<div style="font-weight:650;font-size:14px;margin-bottom:3px">⚡ Auto Run</div>' +
      '<div style="font-size:10.5px;color:var(--dim);margin-bottom:10px">Runs start by themselves at their time - times, On/Off for runs 1-8, waiting, and the WhatsApp failure alert. ' + sum + '.</div>' +
      '<button type="button" class="sa-save" style="width:100%" onclick="saAutoOpen(window._saPw)">Open Auto Run settings</button></div>';
  };
  function load() {
    return get('/sa/auto/status').then(function (d) { if (d && d.ok) { D = d; label(); } }).catch(function () {});
  }
  load(); setInterval(load, 30000);

  function toggleCell(n, on) {
    return '<span class="sa-auto-sw" data-run="' + n + '" style="display:inline-flex;gap:4px">' +
      '<button type="button" class="sa-tool" data-v="1" style="padding:3px 10px;' + (on ? 'background:rgba(52,211,153,.22);border-color:rgba(52,211,153,.6);color:var(--green)' : '') + '">On</button>' +
      '<button type="button" class="sa-tool" data-v="0" style="padding:3px 10px;' + (!on ? 'background:rgba(248,113,113,.18);border-color:rgba(248,113,113,.5);color:var(--red)' : '') + '">Off</button></span>';
  }
  function num(id, v, lab, hint) {
    return '<div class="sa-field" style="margin-bottom:8px"><label>' + lab + ' <span style="color:var(--dim)">' + hint + '</span></label>' +
      '<input id="' + id + '" type="number" min="0" max="600" value="' + esc(v) + '" style="width:110px"></div>';
  }

  function render(msg) {
    var d = D, rows = '';
    d.runs.forEach(function (r) {
      var las = r.run > 4 ? '<span style="color:var(--dim);font-size:10.5px"> · no LAS</span>' : '';
      var dep = r.run <= 6 ? '' : '<span style="color:var(--dim);font-size:10.5px"> · deposit by hand</span>';
      rows += '<tr data-row="' + r.run + '"><td style="padding:6px 8px;font-weight:650;white-space:nowrap">SA-0' + r.run + las + dep + '</td>' +
        '<td style="padding:6px 8px"><input type="time" class="sa-auto-time" value="' + esc(r.time) + '" style="width:112px"></td>' +
        '<td style="padding:6px 8px">' + toggleCell(r.run, r.auto) + '</td>' +
        '<td style="padding:6px 8px;font-size:11.5px;color:var(--muted)">' + esc(stateText(r)) + (r.deposit ? ' · deposit ' + esc(r.deposit) : '') +
        ((r.state === '' || r.state === 'skipped') ? ' <a href="#" class="sa-auto-skip" data-run="' + r.run + '" data-undo="' + (r.state === 'skipped' ? 1 : 0) + '" style="color:var(--blue);margin-left:6px">' + (r.state === 'skipped' ? 'undo skip' : 'skip today') + '</a>' : '') + '</td></tr>';
    });
    var s = d.settings;
    window._saListMask = window._saListMask || {};
    window._saListState = window._saListState || {};
    if (!window._saListState.SA_WA_FAILURE_ALERT) window._saListState.SA_WA_FAILURE_ALERT = (d.alert_numbers_raw || []).slice();
    var nums = '';
    var age = d.deposit_age_min == null ? 'no MCX deposit file in the base folder' : 'MCX deposit file in the base folder is ' + d.deposit_age_min + ' min old';
    var html = '<div style="font-size:12.5px;color:var(--muted);line-height:1.6;margin-bottom:12px">A run with <b>Auto ON</b> starts by itself at its time - nobody has to click, and no pop-up opens. ' +
      '10 minutes before (runs 1-6) the <b>MCX Deposit</b> file is downloaded, unless a file newer than ' + s.fresh_min + ' minutes is already in the base folder. ' +
      'If an exchange peak file has not come, it waits and tries again; after the last try a <b>WhatsApp failure alert</b> goes out.</div>' +
      '<div style="font-size:11.5px;margin-bottom:10px">' + nums + '<br><span style="color:var(--dim)">' + esc(age) + ' · timer ' + (d.timer ? 'running' : '<b style="color:var(--red)">NOT running</b>') + ' · now ' + esc(d.now) + '</span></div>' +
      '<table style="width:100%;border-collapse:collapse;margin-bottom:12px"><thead><tr style="text-align:left;font-size:10.5px;color:var(--dim);text-transform:uppercase"><th style="padding:4px 8px">Run</th><th style="padding:4px 8px">Time</th><th style="padding:4px 8px">Auto</th><th style="padding:4px 8px">Today</th></tr></thead><tbody>' + rows + '</tbody></table>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 18px">' +
      num('saAu_predl', s.predl_min, 'MCX deposit starts', 'minutes before the run') +
      num('saAu_fresh', s.fresh_min, 'Deposit file counts as fresh', 'if changed less than N minutes ago') +
      num('saAu_wait', s.wait_min, 'Wait for a missing peak file', 'minutes - every automatic run') +
      num('saAu_retries', s.retries, 'Tries again', 'times, then the alert') +
      num('saAu_grace', s.grace_min, 'Too late to start', 'minutes after its time = missed') + '</div>' +
      '<div style="border-top:1px solid var(--line);margin:6px 0 12px;padding-top:12px">' +
      (typeof window.saListFieldCollapsed === 'function' ? window.saListFieldCollapsed('SA_WA_FAILURE_ALERT', '⚠ FAILURE ALERT (AUTO RUN)', '9811122233#Kartavya', 'phone') : '') +
      '<div style="font-size:12px;color:var(--muted);line-height:1.6;margin:-4px 0 10px">These numbers get a WhatsApp picture saying which run failed and why. Type just the 10-digit number - +91 is added automatically. Add #Name after a number (e.g. 9811122233#Kartavya) to remember whose it is - optional.</div>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px"><button type="button" class="sa-tool" id="saAuPreview">🖼 See the picture that will be sent</button>' +
      '<button type="button" class="sa-tool" id="saAuTest">📲 Send a TEST alert to these numbers</button></div></div>' +
      '<button class="sa-save" style="width:100%" id="saAuSave">Save</button><div class="sa-msg" id="saAuMsg">' + (msg || '') + '</div>';
    if (typeof window.saShowModal === 'function') window.saShowModal('⚡ Auto Run - Short Allocation', html);
    wire();
    if (typeof window.saListUpdateSummary === 'function') window.saListUpdateSummary('SA_WA_FAILURE_ALERT');
  }

  function wire() {
    var pv = document.getElementById('saAuPreview');
    if (pv) pv.onclick = function () {
      collect();
      window.saShowModal('🖼 The WhatsApp failure alert', '<div style="font-size:12.5px;color:var(--muted);margin-bottom:10px">This is the picture that goes out when an automatic run fails (sample wording, marked TEST).</div>' +
        '<img src="' + API + '/sa/auto/preview?run=5&t=' + Date.now() + '" style="width:100%;border:1px solid var(--line);border-radius:10px" alt="alert preview">' +
        '<button class="sa-save" style="width:100%;margin-top:14px" id="saAuBack">← Back</button>');
      var bk = document.getElementById('saAuBack'); if (bk) bk.onclick = function () { render(); };
    };
    var ts = document.getElementById('saAuTest');
    if (ts) ts.onclick = function () {
      collect();
      var m = document.getElementById('saAuMsg');
      var list = (window._saListState.SA_WA_FAILURE_ALERT || []).filter(function (x) { return (x || '').trim(); });
      if (!list.length) { if (m) m.innerHTML = '<span style="color:var(--red)">✕ Add a number first, then press Save, then test.</span>'; return; }
      if (m) m.innerHTML = '<span style="color:var(--muted)">Sending the test…</span>';
      post('/sa/auto/test-alert', { password: PW, run: 5 }).then(function (d) {
        if (m) m.innerHTML = d && d.ok ? '<span style="color:var(--green)">✓ Test sent: ' + esc(d.reason) + '. (It goes to the numbers that are SAVED - press Save first if you just added one.)</span>' : '<span style="color:var(--red)">✕ ' + esc((d && d.error) || 'not sent') + '</span>';
      }).catch(function () { if (m) m.innerHTML = '<span style="color:var(--red)">✕ Could not reach the server.</span>'; });
    };
    document.querySelectorAll('.sa-auto-sw button').forEach(function (b) {
      b.onclick = function () {
        var n = +b.parentNode.dataset.run, on = b.dataset.v === '1';
        D.runs.forEach(function (r) { if (r.run === n) r.auto = on; });
        collect(); render();
      };
    });
    document.querySelectorAll('.sa-auto-skip').forEach(function (a) {
      a.onclick = function (e) {
        e.preventDefault();
        post('/sa/auto/skip?run=' + a.dataset.run + '&undo=' + (a.dataset.undo === '1'), {}).then(function (d) { if (d && d.ok) { D = d; label(); render(); } });
      };
    });
    var sv = document.getElementById('saAuSave');
    if (sv) sv.onclick = save;
  }

  function val(id) { var e = document.getElementById(id); return e ? e.value : ''; }
  function collect() {
    document.querySelectorAll('tr[data-row]').forEach(function (tr) {
      var n = +tr.dataset.row, t = tr.querySelector('.sa-auto-time');
      D.runs.forEach(function (r) { if (r.run === n && t && t.value) r.time = t.value; });
    });
    var s = D.settings;
    s.predl_min = val('saAu_predl') || s.predl_min; s.fresh_min = val('saAu_fresh') || s.fresh_min;
    s.wait_min = val('saAu_wait') || s.wait_min;
    s.retries = val('saAu_retries') === '' ? s.retries : val('saAu_retries'); s.grace_min = val('saAu_grace') || s.grace_min;
  }
  function save() {
    collect();
    var runs = {};
    D.runs.forEach(function (r) { runs[r.run] = { time: r.time, auto: !!r.auto }; });
    var s = D.settings, body = { runs: runs, predl_min: s.predl_min, fresh_min: s.fresh_min, wait_min: s.wait_min, retries: s.retries, grace_min: s.grace_min };
    var m = document.getElementById('saAuMsg'); if (m) m.innerHTML = '<span style="color:var(--muted)">Saving…</span>';
    var nums = (window._saListState.SA_WA_FAILURE_ALERT || []).map(function (x) { return (x || '').trim(); }).filter(Boolean).join(',');
    post('/sa/auto/settings', { password: PW, settings: body }).then(function (d) {
      if (!d || !d.ok) { if (m) m.innerHTML = '<span style="color:var(--red)">✕ ' + esc((d && d.error) || 'Could not save') + '</span>'; return; }
      D = d; label();
      return post('/sa/config', { password: PW, updates: { SA_WA_FAILURE_ALERT: nums } }).then(function (c) {
        if (!c || !c.ok) { if (m) m.innerHTML = '<span style="color:var(--red)">✕ Settings saved, but the numbers were not: ' + esc((c && c.error) || 'error') + '</span>'; return; }
        delete window._saListState.SA_WA_FAILURE_ALERT;
        load().then(done);
      });
    }).catch(function () { if (m) m.innerHTML = '<span style="color:var(--red)">✕ Could not reach the server.</span>'; });
  }
  function done() {
    if (typeof window.saShowModal !== 'function') return;
    window.saShowModal('✓ Saved', '<div style="text-align:center;padding:14px 0 4px"><div style="width:64px;height:64px;border-radius:50%;margin:0 auto 16px;display:grid;place-items:center;font-size:32px;background:rgba(52,211,153,.14);border:2px solid rgba(52,211,153,.6);color:var(--green)">✓</div>' +
      '<div style="font-size:15px;color:var(--ink);font-weight:650;margin-bottom:6px">Auto Run settings saved</div><div style="font-size:13px;color:var(--muted)">They apply right away.</div></div>' +
      '<button class="sa-save" style="width:100%;margin-top:16px" onclick="saCloseModal()">OK</button>');
  }

  window.saAutoOpen = function (pw) {
    var go = function (p) { PW = p; delete (window._saListState || {}).SA_WA_FAILURE_ALERT; load().then(function () { if (D) render(); }); };
    if (pw) go(pw);                                    // opened from Credentials: the password was already checked there
    else if (typeof window.saAskPassword === 'function') window.saAskPassword('Auto Run settings', go); else go('');
  };
})();
