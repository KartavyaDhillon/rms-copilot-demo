/* MTF Impact Cost — compact tile in "Processing & Reports" + a detail modal.
   Backend: /mtfic/* (api.py) → core/pipelines/mtf_impact_cost.py. Injected at serve time
   (see api.py /legacy); dashboard.html only carries the tile markup. */
(function () {
  'use strict';
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'MTF Impact Cost';
  var STEPS = ['Check files', 'NSE file', 'Reconcile', 'WhatsApp', 'Backup'];
  var S = { iso: '', wa: true, data: null, msg: '', msgKind: '', busy: false, timer: null, sig: '', ctl: null };

  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"]/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]; }); }
  function n(x) { return Number(x || 0).toLocaleString('en-IN'); }
  function dmy(iso) { return iso ? iso.split('-').reverse().join('-') : ''; }

  function load() {
    return fetch(API + '/mtfic/status', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) { S.data = d; if (!S.iso && d.iso) S.iso = d.iso; return d; });
  }

  // ───────────────────────── tile ─────────────────────────
  function tileRefresh() {
    var el = document.getElementById('mtficMini');
    if (!el) return;
    fetch(API + '/mtfic/status', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
      var chip = document.getElementById('mtficChip');
      var l = d.last || {};
      if (d.running) {
        var p = d.progress || {};
        el.textContent = 'Running · step ' + (p.step || 1) + '/' + (p.total || 5) + (p.msg ? ' — ' + p.msg : '');
        if (chip) { chip.textContent = 'running'; chip.style.color = 'var(--amber)'; }
      } else if (l.date) {
        el.textContent = 'Last run ' + l.date + ' · ' + n(l.ok) + ' OK · ' + n(l.check) + ' to check';
        if (chip) { chip.textContent = l.check ? 'review' : 'all clear'; chip.style.color = l.check ? 'var(--red)' : 'var(--green)'; }
      } else {
        el.textContent = d.ready ? 'Files ready — open to run' : 'Not run yet · open to upload files';
        if (chip) { chip.textContent = 'idle'; chip.style.color = 'var(--dim)'; }
      }
    }).catch(function () {});
  }

  // ───────────────────────── modal ─────────────────────────
  function modalOpen() {
    var t = document.getElementById('saModalTitle');
    var m = document.getElementById('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE);
  }

  function inputRow(it) {
    var ic = it.ok ? '<span style="color:var(--green)">&#10003;</span>'
      : (it.key === 'catg' ? '<span style="color:var(--amber)">&#9679;</span>' : '<span style="color:var(--red)">&#10007;</span>');
    var sub = it.ok ? esc(it.file) : esc(it.need);
    var note = '';
    if (it.key === 'catg' && !it.ok) note = it.file ? esc(it.note || '') + ' — will be downloaded when you run' : 'not here yet — the run downloads it (or use Upload files)';
    if (!it.ok && it.key !== 'catg') note = 'missing — ' + esc(it.where);
    return '<div style="display:flex;gap:8px;align-items:baseline;padding:5px 0;border-bottom:1px solid var(--line)">' + ic +
      '<span style="font-size:12.5px;min-width:96px">' + esc(it.label) + '</span>' +
      '<span style="font-family:var(--mono);font-size:11px;color:' + (it.ok ? 'var(--ink)' : 'var(--dim)') + ';flex:1;word-break:break-all">' + sub + '</span>' +
      (note ? '<span style="font-size:10.5px;color:var(--amber)">' + note + '</span>' : '') + '</div>';
  }

  function stepsHtml(p, running) {
    var cur = running ? (p.step || 1) : 0;
    var done = (!running && p.ok === true) ? 5 : 0;
    return '<div style="display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 4px">' + STEPS.map(function (s, i) {
      var k = i + 1, on = (cur === k), ok = (done >= k) || (running && cur > k);
      var bg = on ? 'rgba(251,191,36,.15)' : (ok ? 'rgba(52,211,153,.14)' : 'transparent');
      var col = on ? 'var(--amber)' : (ok ? 'var(--green)' : 'var(--dim)');
      return '<span style="font-size:10.5px;padding:3px 9px;border-radius:99px;border:1px solid var(--line-2);background:' + bg + ';color:' + col + '">' + k + ' ' + s + '</span>';
    }).join('') + '</div>';
  }

  function resultHtml(l) {
    if (!l || !l.date) return '<div style="font-size:12px;color:var(--dim);padding:8px 0">No finished run yet. The result, the Excel and the image will appear here.</div>';
    var tile = function (lab, val, col) {
      return '<div style="flex:1;min-width:92px;border:1px solid var(--line);border-radius:10px;padding:8px 10px"><div style="font-size:10px;letter-spacing:.5px;color:var(--dim);text-transform:uppercase">' + lab + '</div><div style="font-size:21px;font-weight:700;color:' + col + '">' + n(val) + '</div></div>';
    };
    var rows = (l.top || []).slice(0, 8).map(function (r) {
      return '<tr><td style="padding:3px 8px 3px 0;font-weight:600">' + esc(r.symbol) + '</td><td style="padding:3px 8px 3px 0;font-family:var(--mono);color:var(--dim)">' + esc(r.isin) + '</td><td style="padding:3px 8px 3px 0;color:var(--muted)">' + esc(r.reason) + '</td><td style="padding:3px 0;text-align:right;color:var(--red);font-weight:700">' + n(r.trades) + '</td></tr>';
    }).join('');
    var more = (l.unique_symbols || 0) > 8 ? '<div style="font-size:10.5px;color:var(--dim);margin-top:4px">+ ' + (l.unique_symbols - 8) + ' more in the Excel (Check_Required sheet)</div>' : '';
    var wa = l.wa || {};
    var waTxt = wa.skipped ? ('WhatsApp: not sent (' + esc(wa.skipped) + ')') : ('WhatsApp: ' + n(wa.sent) + ' sent' + (wa.failed ? ', ' + n(wa.failed) + ' failed' : ''));
    return '<div style="font-size:11px;color:var(--dim);margin-bottom:6px">Run for <b style="color:var(--ink)">' + esc(l.date) + '</b> · finished ' + esc(l.finished) + '</div>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap">' + tile('MTF trades', l.mtf, 'var(--ink)') + tile('OK', l.ok, 'var(--green)') + tile('Check required', l.check, l.check ? 'var(--red)' : 'var(--ink)') + '</div>' +
      (l.check ? '<div style="font-size:11px;color:var(--muted);margin:6px 0">' + n(l.no_holding) + ' no Holding record · ' + n(l.isin_missing) + ' ISIN not in NSE file · ' + n(l.wrong_category) + ' wrong category</div>' : '') +
      (rows ? '<table style="width:100%;font-size:11.5px;border-collapse:collapse;margin-top:6px"><tbody>' + rows + '</tbody></table>' + more : '') +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 4px">' +
      '<a class="sa-save" style="text-decoration:none;text-align:center;flex:1;min-width:150px;background:var(--green);color:#04120a" href="' + API + '/mtfic/file?kind=xlsx" download>&#11015; Excel report</a>' +
      (l.files && l.files.png ? '<a class="sa-save" style="text-decoration:none;text-align:center;flex:1;min-width:150px;background:var(--blue);color:#061020" href="' + API + '/mtfic/file?kind=png" download>&#11015; Dashboard image</a>' : '') + '</div>' +
      '<div style="font-size:10.5px;color:var(--dim);line-height:1.6">' + waTxt + '<br>Backed up to <span style="font-family:var(--mono)">' + esc(l.backup_dir) + '</span>' +
      ((l.backup_problems || []).length ? '<br><span style="color:var(--amber)">' + l.backup_problems.map(esc).join('<br>') + '</span>' : '') + '</div>';
  }

  function render(force) {
    var d = S.data;
    if (!d || !modalOpen()) return;
    var running = !!d.running;
    if (!d.done_today) S.rerun = false;
    var p = d.progress || {};
    var sig = JSON.stringify([d, S.msg, S.busy]);
    if (!force && sig === S.sig) return;
    S.sig = sig;
    var items = (d.items || []).map(inputRow).join('');
    var runBtn = (running || S.busy)
      ? '<button class="sa-save" style="flex:1;background:var(--red);color:#1a0505" onclick="mtficStop()">&#9940; Stop</button>'
      : '<button class="sa-save" style="flex:1;' + (d.ready ? '' : 'opacity:.55;') + '" onclick="mtficRun()">&#9654; Run now</button>';
    var spin = '<span style="display:inline-block;width:14px;height:14px;margin-right:8px;vertical-align:-3px;border:2px solid var(--line-2);border-top-color:var(--green);border-radius:50%;animation:spin .8s linear infinite"></span>';
    var line = (running || S.busy) ? spin + esc(running ? (p.msg || 'Starting…') : 'Starting…') : (p.ok === false && p.msg ? '<span style="color:var(--red)">' + esc(p.msg) + '</span>' : '');
    var msg = S.msg ? '<div style="font-size:11.5px;margin:8px 0;color:' + (S.msgKind === 'bad' ? 'var(--red)' : (S.msgKind === 'ok' ? 'var(--green)' : 'var(--amber)')) + ';white-space:pre-wrap">' + S.msg + '</div>' : '';
    var finished = !!d.done_today && !S.rerun && !running && !S.busy;
    var l0 = d.last || {};
    var doneBanner =
      '<div style="display:flex;align-items:center;gap:12px;border:1px solid rgba(52,211,153,.35);background:rgba(52,211,153,.07);border-radius:10px;padding:10px 14px;margin-bottom:12px">' +
      '<span style="font-size:22px;color:var(--green)">&#10003;</span>' +
      '<div style="flex:1"><div style="font-weight:650;color:var(--green)">Completed today</div>' +
      '<div style="font-size:11px;color:var(--dim)">This runs once a day · finished ' + esc(l0.finished) + (l0.by ? ' by ' + esc(l0.by) : '') + (l0.rerun ? ' (admin re-run)' : '') + '</div></div>' +
      '<button class="sa-tool" onclick="mtficRerun()" data-tip="Only an admin can run it again today. Replaces today\'s result." style="border-color:rgba(251,191,36,.45);color:var(--amber)">&#128274; Re-run (admin)</button></div>' + msg;
    var rerunNote = S.rerun
      ? '<div style="font-size:11.5px;color:var(--amber);border:1px solid rgba(251,191,36,.35);border-radius:9px;padding:8px 12px;margin-bottom:10px">Admin re-run unlocked — upload today\'s TradeBook again, then Run now. This replaces today\'s result.</div>' : '';
    var workPart =
      rerunNote +
      '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px">' +
      '<span style="font-size:11.5px;color:var(--muted)">Today: <b style="color:var(--ink)">' + esc(d.date) + '</b></span>' +
      '<label style="font-size:11.5px;color:var(--muted);cursor:pointer"><input type="checkbox" ' + (S.wa ? 'checked' : '') + ' onchange="mtficWa(this.checked)"> Send WhatsApp image' +
      ' <span style="color:var(--dim)">(' + (d.wa_numbers ? d.wa_numbers + ' number' + (d.wa_numbers > 1 ? 's' : '') : 'no numbers set') + ')</span></label></div>' +
      '<div style="border:1px dashed var(--line-2);border-radius:10px;padding:4px 12px;margin-bottom:10px">' + items + '</div>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">' +
      '<button class="sa-tool" onclick="mtficPick()" style="border-color:rgba(52,211,153,.45);color:var(--green)">&#11014;&#65039; Upload files</button>' +
      '<button class="sa-tool" onclick="mtficDownload()" style="border-color:rgba(96,165,250,.45);color:var(--blue)"' + (running ? ' disabled' : '') + '>&#128229; Download NSE file</button>' +
      '<span style="font-size:10.5px;color:var(--dim);align-self:center">Upload names: ' + esc(((d.items || [])[0] || {}).need || 'TradeBook_DDMMYYYY.csv') + ' (today\'s date: DD day, MM month, YYYY year) · C_CATG_OCT2026.T01 style for the NSE file. HOLDING comes from COMMON FILES.</span></div>' +
      '<div style="display:flex;gap:8px">' + runBtn + '</div>' +
      stepsHtml(p, running) + '<div style="font-size:11px;color:var(--dim);min-height:15px">' + line + '</div>' + msg;
    var html =
      '<div style="font-size:12px;color:var(--muted);margin-bottom:10px">Checks today\'s MTF trades against NSE\'s security-category impact-cost file. One folder for everything: <span style="font-family:var(--mono);color:var(--dim)">' + esc(d.folder) + '</span></div>' +
      (finished ? doneBanner : workPart) +
      '<div style="font-size:10px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:12px 0 6px">' + (finished ? "Today's result" : 'Last result') + '</div>' + resultHtml(d.last) +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px;padding-top:10px;border-top:1px solid var(--line)">' +
      '<button class="sa-tool" onclick="pxCredentials(\'mtfic\')" data-tip="WhatsApp token and numbers">&#128273; Credentials</button>' +
      '<button class="sa-tool" onclick="pxBackup(\'mtfic\')" data-tip="Browse past runs: every file used and generated is kept here">&#128230; Backup files</button>' +
      '<button class="sa-tool" onclick="pxLogs(\'mtfic\')" data-tip="Full saved output of past runs">&#128450;&#65039; Logs</button></div>' +
      '<div style="font-size:10px;color:var(--dim);margin-top:8px">After a completed run the TradeBook, NSE file, Excel and image are backed up and removed from the folder. HOLDING in COMMON FILES is never touched.</div>';
    var body = document.getElementById('saModalBody');
    if (body) body.innerHTML = html;
  }

  function poll() {
    if (!modalOpen()) { clearInterval(S.timer); S.timer = null; tileRefresh(); return; }
    load().then(function () { render(false); }).catch(function () {});
  }

  window.mtficRerun = function () {
    var go = function (pw) {
      fetch(API + '/mtfic/unlock', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw || '' }) })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (j.ok) { S.rerun = true; window.mtficOpen(); }
          else window.mtficOpen(esc(j.error || 'Not allowed'), 'bad');
        }).catch(function () { window.mtficOpen('Could not reach the server.', 'bad'); });
    };
    if ((S.data || {}).admin) go('');
    else if (typeof saAskPassword === 'function') saAskPassword('Admin re-run', function (pw) { go(pw); });
    else setMsg('Only an admin can re-run this.', 'bad');
  };

  window.mtficOpen = function (msg, kind) {
    if (typeof saShowModal !== 'function') return;
    S.msg = (typeof msg === 'string') ? msg : ''; S.msgKind = kind || ''; S.sig = ''; S.iso = '';
    saShowModal(TITLE, '<div style="color:var(--muted);font-size:12px">Checking files…</div>');
    load().then(function () { render(true); }).catch(function () {
      saShowModal(TITLE, '<div style="color:var(--red);font-size:13px">Could not reach the server.</div>');
    });
    if (S.timer) clearInterval(S.timer);
    S.timer = setInterval(poll, 2500);
  };
  window.mtficDate = function (v) { S.iso = v || ''; S.msg = ''; load().then(function () { render(true); }); };
  window.mtficWa = function (v) { S.wa = !!v; };

  function setMsg(t, kind) { S.msg = t; S.msgKind = kind || ''; render(true); }

  // ───────────────────────── upload ─────────────────────────
  var fileInput = null;
  window.mtficPick = function () {
    if (!fileInput) {
      fileInput = document.createElement('input');
      fileInput.type = 'file'; fileInput.multiple = true; fileInput.style.display = 'none';
      fileInput.addEventListener('change', doUpload);
      document.body.appendChild(fileInput);
    }
    fileInput.value = '';
    fileInput.click();
  };
  function doUpload() {
    var files = [].slice.call(fileInput.files || []);
    if (!files.length) return;
    // same flow as the other upload buttons: "Uploading…" with a spinner → "Upload result" → Done
    saShowModal('Uploading…',
      '<div style="text-align:center;padding:14px 0"><div style="width:30px;height:30px;margin:0 auto 12px;border:3px solid var(--line-2);border-top-color:var(--green);border-radius:50%;animation:spin .8s linear infinite"></div>' +
      '<div style="color:var(--muted);font-size:13px">Copying ' + files.length + ' file(s)…</div></div>');
    var fd = new FormData();
    files.forEach(function (f) { fd.append('files', f); });
    var ctl = new AbortController(); window._saUploadCtl = ctl;
    var to = setTimeout(function () { ctl.abort(); }, 180000);
    var done = '<button class="sa-save" style="width:100%;margin-top:14px" onclick="mtficOpen()">Done</button>';
    fetch(API + '/mtfic/upload', { method: 'POST', body: fd, signal: ctl.signal }).then(function (r) {
      clearTimeout(to);
      if (ctl.signal.aborted) return null;
      if (!r.ok) throw new Error('Server ' + r.status);
      return r.json();
    }).then(function (d) {
      if (!d) return;
      var html = '';
      (d.ok || []).forEach(function (m) { html += '<div class="sa-ev ok"><span class="ic">✓</span><span>' + esc(m) + '</span><span class="what">uploaded</span></div>'; });
      (d.rejected || []).forEach(function (m) { html += '<div class="sa-ev bad"><span class="ic">✕</span><span>' + esc(m) + '</span><span class="what">not accepted</span></div>'; });
      (d.notes || []).forEach(function (m) { html += '<div style="font-size:11.5px;color:var(--amber);margin:6px 0">' + esc(m) + '</div>'; });
      saShowModal('Upload result', html + done);
    }).catch(function (e) {
      clearTimeout(to);
      if (ctl.signal.aborted) return;
      saShowModal('Upload failed', '<div style="color:var(--red);font-size:13px">' + esc((e && e.message) ? e.message : String(e)) + '</div>' + done);
    }).then(function () { window._saUploadCtl = null; });
  }

  // ───────────────────────── run / download / stop ─────────────────────────
  function stream(url, startMsg) {
    S.busy = true; setMsg(startMsg, '');
    var ctl = new AbortController(); S.ctl = ctl;
    return fetch(url, { signal: ctl.signal }).then(function (resp) {
      var rd = resp.body.getReader(), dec = new TextDecoder(), buf = '';
      function pump() {
        return rd.read().then(function (x) {
          if (x.done) return;
          buf += dec.decode(x.value, { stream: true });
          var parts = buf.split('\n\n'); buf = parts.pop();
          parts.forEach(function (part) {
            if (part.indexOf('data: ') !== 0) return;
            try {
              var ev = JSON.parse(part.slice(6));
              if (ev.type === 'error') setMsg(esc(ev.msg), 'bad');
              else if (ev.type === 'stopped') setMsg('Stopped.', 'warn');
            } catch (e) {}
          });
          return pump();
        });
      }
      return pump();
    }).catch(function (e) { if (!e || e.name !== 'AbortError') setMsg('Connection interrupted — the run continues on the server; reopen this window to follow it.', 'warn'); })
      .then(function () { S.busy = false; S.ctl = null; return load(); }).then(function () { render(true); tileRefresh(); });
  }

  window.mtficRun = function () {
    var d = S.data || {};
    if (d.running) return;
    if (!d.ready) {
      var miss = (d.items || []).filter(function (i) { return !i.ok && i.key !== 'catg'; }).map(function (i) { return i.need; });
      setMsg('Cannot run yet — missing: ' + esc(miss.join(', ')), 'bad');
      return;
    }
    stream(API + '/mtfic/run-stream?wa=' + (S.wa ? 1 : 0), 'Run started…').then(function () {
      var p = (S.data || {}).progress || {};
      if (p.ok === true) { S.rerun = false; setMsg('', ''); }
      else if (p.ok === false) setMsg(esc(p.msg || 'The run failed — see Logs.'), 'bad');
    });
  };

  window.mtficDownload = function () {
    if ((S.data || {}).running) return;
    stream(API + '/mtfic/download-stream', 'Downloading the NSE impact-cost file…').then(function () {
      var it = ((S.data || {}).items || []).filter(function (i) { return i.key === 'catg'; })[0];
      if (it && it.ok) setMsg('&#10003; NSE file downloaded: ' + esc(it.file), 'ok');
      else setMsg('The download did not finish — see Logs. You can upload the file by hand instead.', 'bad');
    });
  };

  window.mtficStop = function () {
    fetch(API + '/mtfic/stop', { method: 'POST' }).then(function (r) {
      if (r.status === 403) return r.json().then(function (j) { setMsg(esc(j.error || "You didn't start this run."), 'bad'); });
      if (S.ctl) { try { S.ctl.abort(); } catch (e) {} }
      return load().then(function () { setMsg('Stopped.', 'warn'); });
    }).catch(function () {});
  };

  function init() {
    tileRefresh();
    setInterval(function () { if (!modalOpen()) tileRefresh(); }, 20000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
