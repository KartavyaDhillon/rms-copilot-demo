/* LIVE ALLOCATION CAPTURE - Process Center card: 8 numbered slots (folders 1-8), live status, Get files, Full folder,
   Skip / Retry / Stop. Backend: /live-alloc/* (api.py) -> core/live_allocation.py. The capture itself runs by the
   Scheduler (process "LIVE ALLOCATION CAPTURE"); the buttons here only start/skip/stop one slot by hand.
   Injected at serve time (see api.py /legacy); dashboard.html is not modified. */
(function () {
  'use strict';
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'Live Allocation files';
  var S = { data: null, sig: '', timer: null, msg: '', msgTimer: null, fdate: '', ffocus: 0 };

  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function $(id) { return document.getElementById(id); }
  function kb(n) { n = Number(n || 0); return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }
  function get(path) { return fetch(API + path, { cache: 'no-store' }).then(function (r) { return r.json(); }); }
  function post(path, body) {
    return fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }

  var css = document.createElement('style');
  css.textContent =
    '.la-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.la-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.la-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.la-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.la-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.la-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.la-bar{height:5px;border-radius:9px;background:var(--line);margin-top:7px;overflow:hidden}.la-bar i{display:block;height:100%;background:var(--green);transition:width .6s}' +
    '.la-runs{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}' +
    '@media(max-width:1100px){.la-kpis,.la-runs{grid-template-columns:repeat(2,1fr)}}' +
    '.la-run{background:var(--panel-2);border:1px solid var(--line);border-radius:13px;padding:13px 13px 12px;text-align:center;display:flex;flex-direction:column;gap:7px;min-width:0}' +
    '.la-run.done{border-color:rgba(52,211,153,.4)}.la-run.fail{border-color:rgba(248,113,113,.5)}' +
    '.la-run.running{border-color:var(--amber);box-shadow:0 0 0 1px rgba(251,191,36,.25)}' +
    '.la-run.skipped,.la-run.later{opacity:.72}' +
    '.la-id{font-family:var(--display);font-size:17px;font-weight:750}' +
    '.la-time{font-family:var(--mono);font-size:10.5px;color:var(--muted)}' +
    '.la-st{font-family:var(--mono);font-size:9.5px;letter-spacing:1px;text-transform:uppercase;color:var(--dim)}' +
    '.la-run.done .la-st{color:var(--green)}.la-run.fail .la-st{color:var(--red)}.la-run.running .la-st{color:var(--amber)}' +
    '.la-dots{display:flex;gap:5px;justify-content:center}.la-dots i{width:9px;height:9px;border-radius:50%;background:var(--line-2);display:block}' +
    '.la-dots i.on{background:var(--green)}.la-dots i.bad{background:var(--red)}' +
    '.la-note{font-size:10.5px;color:var(--muted);min-height:26px;line-height:1.35;overflow:hidden}' +
    '.la-btn{width:100%;font-family:var(--sans);font-weight:650;font-size:12.5px;border:0;border-radius:9px;padding:9px;cursor:pointer;background:var(--blue);color:var(--blue-d)}' +
    '.la-btn:disabled{background:var(--panel-3);color:var(--dim);cursor:not-allowed}' +
    '.la-btn.red{background:var(--red);color:#fff}.la-btn.amber{background:var(--amber);color:var(--amber-d)}' +
    '.la-link{background:none;border:0;color:var(--muted);font-size:11px;cursor:pointer;text-decoration:underline;padding:0}' +
    '.la-link:disabled{opacity:.4;cursor:not-allowed}' +
    '.la-live{font-family:var(--mono);font-size:10.5px;line-height:1.7;margin:0 0 12px;padding:9px 14px;border:1px solid rgba(251,191,36,.3);border-radius:9px;background:rgba(251,191,36,.05)}' +
    '.la-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.la-frow{display:flex;gap:8px;align-items:center;padding:5px 0;border-bottom:1px solid var(--line);font-size:12px}' +
    '.la-frow a{color:var(--blue);text-decoration:none}.la-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}';
  document.head.appendChild(css);

  function say(txt, kind) {
    S.msg = txt ? '<div class="la-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    var el = $('laMsg'); if (el) el.innerHTML = S.msg;
    clearTimeout(S.msgTimer);
    if (txt) S.msgTimer = setTimeout(function () { var e = $('laMsg'); if (e) e.innerHTML = ''; S.msg = ''; }, 7000);
  }

  // ───────────────────────── card skeleton ─────────────────────────
  function mount() {
    if ($('laBox')) return true;
    var secs = [].slice.call(document.querySelectorAll('.sec'));
    var anchor = secs.filter(function (s) { return /processing/i.test(s.textContent); })[0] || null;
    var host = anchor ? anchor.parentNode : ($('saBox') && $('saBox').parentNode);
    if (!host) return false;
    var wrap = document.createElement('div');
    wrap.id = 'laWrap';
    wrap.innerHTML =
      '<div class="sec">Live Allocation Capture <span style="font-weight:400;color:var(--dim);font-size:10px;letter-spacing:0">— runs 8× daily, by itself</span></div>' +
      '<div class="sa-box" id="laBox">' +
      '<div class="sa-head"><div class="sa-title-wrap"><div class="sa-logo">LA</div><div><div class="sa-title">LIVE ALLOCATION CAPTURE</div>' +
      '<div class="sa-sub">NMASS · NCDEX · MCX files → folders 1–8 · started by the timer, nothing to click</div></div></div>' +
      '<div class="sa-tools">' +
      '<button class="sa-tool" onclick="laFiles()" data-tip="Open any day, see every folder and download single files.">📦 Get files</button>' +
      '<button class="sa-tool" onclick="laZip()" data-tip="Download the whole date folder (1 to 8 inside, exactly as on disk) as one zip.">📁 Full folder</button>' +
      '<button class="sa-tool" id="laStopBtn" onclick="laStop()" style="border-color:rgba(248,113,113,.45);color:var(--red)" data-tip="Stops the capture that is running right now.">⛔ Stop</button>' +
      '</div></div>' +
      '<div id="laMsg"></div><div id="laKpis" class="la-kpis"></div><div id="laLive"></div><div class="la-runs" id="laRuns"></div></div>';
    if (anchor) host.insertBefore(wrap, anchor); else host.appendChild(wrap);
    return true;
  }

  // ───────────────────────── render ─────────────────────────
  var STATE_TXT = { done: '✓ Captured', running: '● Running…', failed: '✗ Failed', missed: '✗ Missed', partial: '◐ Partly done', due: '● Starting…', upcoming: 'Later today', skipped: '⏭ Skipped' };

  function kpi(k, v, f, extra) { return '<div class="la-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div>' + (extra || '') + '</div>'; }

  function renderKpis(d) {
    var c = d.counts || {}, total = (d.slots || []).length, done = c.done || 0;
    var run = (d.slots || []).filter(function (s) { return s.state === 'running'; })[0];
    var bad = (c.failed || 0) + (c.missed || 0) + (c.partial || 0);
    var nx = d.next;
    $('laKpis').innerHTML =
      kpi('Captured', done + '<small> / ' + total + '</small>', (c.skipped ? c.skipped + ' skipped · ' : '') + 'folders complete (5 of 5 files)',
        '<div class="la-bar"><i style="width:' + (total ? Math.round(100 * done / total) : 0) + '%"></i></div>') +
      kpi('Running now', run ? 'Folder ' + run.slot : '—', run ? 'started ' + (run.started || '').slice(11, 16) + (run.by ? ' · ' + run.by : '') : 'nothing running') +
      kpi('Needs attention', '<span style="color:' + (bad ? 'var(--red)' : 'var(--green)') + '">' + bad + '</span>', bad ? 'failed / missed / partly done - use Retry or Skip' : 'all good') +
      kpi('Next capture', nx ? nx.time : '—', nx ? 'folder ' + nx.slot : (d.today ? 'no more today' : 'a past day'));
  }

  function dots(s) {
    var order = ['CM collaterals', 'FO collaterals', 'CO collaterals', 'NCDEX net deposits', 'MCX web allocation'];
    return '<div class="la-dots">' + order.map(function (l) {
      var on = s.found && s.found[l], bad = !on && (s.state === 'failed' || s.state === 'partial' || s.state === 'missed');
      return '<i class="' + (on ? 'on' : bad ? 'bad' : '') + '" title="' + esc(l + (on ? ' - ' + s.found[l] : ' - not there')) + '"></i>';
    }).join('') + '</div>';
  }

  function tile(s, anyRunning, isToday) {
    var st = s.state;
    var cls = st === 'done' ? 'done' : (st === 'failed' || st === 'missed' || st === 'partial') ? 'fail' : st === 'running' ? 'running' : st === 'skipped' ? 'skipped' : (st === 'upcoming' ? 'later' : '');
    var note = s.msg || (st === 'upcoming' ? 'waiting for ' + s.time : st === 'missed' ? 'did not start - nothing was captured' : '');
    var btn = '', sec = '';
    var lock = anyRunning && st !== 'running';
    var tip = lock ? ' title="Another capture is running - wait, or Skip &amp; stop it"' : '';
    if (!isToday) {
      btn = '<button class="la-btn" onclick="laFiles(' + s.slot + ')">📂 Files</button>';
    } else if (st === 'done') {
      btn = '<button class="la-btn" onclick="laFiles(' + s.slot + ')">📂 View files</button>';
      sec = '<button class="la-link"' + (lock ? ' disabled' : '') + tip + ' onclick="laRun(' + s.slot + ',true)">↻ capture again</button>';
    } else if (st === 'running') {
      btn = '<button class="la-btn amber" disabled>⏳ Running…</button>';
      sec = '<button class="la-link" onclick="laSkip(' + s.slot + ')">⏭ Skip &amp; stop this one</button>';
    } else if (st === 'skipped') {
      btn = '<button class="la-btn" onclick="laSkip(' + s.slot + ',true)">↩ Undo skip</button>';
    } else if (st === 'upcoming') {
      btn = '<button class="la-btn"' + (lock ? ' disabled' : '') + tip + ' onclick="laRun(' + s.slot + ',false)">▶ Run now</button>';
      sec = '<button class="la-link" onclick="laSkip(' + s.slot + ')">⏭ Skip this slot</button>';
    } else {
      btn = '<button class="la-btn red"' + (lock ? ' disabled' : '') + tip + ' onclick="laRun(' + s.slot + ',false)">↻ ' + (st === 'due' ? 'Start now' : 'Retry') + '</button>';
      sec = '<button class="la-link" onclick="laSkip(' + s.slot + ')">⏭ Skip - go on to the next</button>';
    }
    var steps = '';
    ['nmass_ncdex', 'mcx'].forEach(function (k) {
      var r = (s.steps || {})[k]; if (!r) return;
      steps += '<span title="' + esc(r.msg) + '" style="font-size:10px;color:' + (r.ok ? 'var(--green)' : 'var(--red)') + '">' + (r.ok ? '✓' : '✗') + ' ' + (k === 'mcx' ? 'MCX' : 'NMASS+NCDEX') + '</span> ';
    });
    return '<div class="la-run ' + cls + '"><div class="la-id">Folder ' + s.slot + '</div><div class="la-time">' + esc(s.time) + '</div>' +
      '<div class="la-st">' + (STATE_TXT[st] || st) + ' · ' + s.files + '/' + s.total + ' files</div>' + dots(s) +
      '<div class="la-note" title="' + esc(s.msg) + '">' + esc(note) + (steps ? '<br>' + steps : '') + '</div>' + btn + '<div>' + sec + '</div></div>';
  }

  function render(d) {
    if (!mount()) return;
    var sig = JSON.stringify(d);
    if (sig === S.sig) return;
    S.sig = sig;
    var any = (d.slots || []).some(function (s) { return s.state === 'running'; });
    renderKpis(d);
    $('laRuns').innerHTML = (d.slots || []).map(function (s) { return tile(s, any, d.today); }).join('');
    var sb = $('laStopBtn'); if (sb) { sb.disabled = !any; sb.style.opacity = any ? '1' : '.45'; }
    $('laLive').innerHTML = any && (d.log || []).length
      ? '<div class="la-live"><b style="color:var(--amber)">Live</b><br>' + d.log.slice(-6).map(function (l) { return esc(l.t + '  ' + l.msg); }).join('<br>') + '</div>' : '';
    if (S.msg) $('laMsg').innerHTML = S.msg;
  }

  function load() {
    var next = 8000;
    return get('/live-alloc/status').then(function (d) {
      S.data = d;
      if (d && d.slots) { render(d); if (d.running) next = 2500; }
    }).catch(function () { next = 15000; }).then(function () { schedule(next); });
  }
  function schedule(ms) {
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (document.hidden) { schedule(ms); } else { load(); } }, ms);
  }

  // ───────────────────────── actions ─────────────────────────
  window.laRun = function (slot, force) {
    if (force && !window.confirm('Capture folder ' + slot + ' again? The files are downloaded again.')) return;
    post('/live-alloc/run', { slot: slot, force: !!force }).then(function (j) {
      if (j && j.ok) say('Folder ' + slot + ' started.'); else say((j && (j.error || j.detail)) || 'Could not start.', 'err');
      S.sig = ''; clearTimeout(S.timer); setTimeout(load, 700);
    });
  };
  window.laSkip = function (slot, undo) {
    var running = ((S.data || {}).slots || []).some(function (s) { return s.slot === slot && s.state === 'running'; });
    if (!undo && running && !window.confirm('Stop the capture that is running and skip folder ' + slot + '?')) return;
    post('/live-alloc/skip', { slot: slot, undo: !!undo }).then(function () {
      say(undo ? 'Folder ' + slot + ' is open again.' : 'Folder ' + slot + ' skipped - the next one runs on time.');
      S.sig = ''; clearTimeout(S.timer); setTimeout(load, 500);
    });
  };
  window.laStop = function () {
    if (!window.confirm('Stop the capture that is running now?')) return;
    post('/live-alloc/stop').then(function (j) { say(j && j.stopped ? 'Stopping…' : 'Nothing is running.'); S.sig = ''; clearTimeout(S.timer); setTimeout(load, 800); });
  };
  window.laZip = function (date, slot) {
    var d = date || (S.data && S.data.date) || '';
    window.location.href = API + '/live-alloc/zip?date=' + encodeURIComponent(d) + (slot ? '&slot=' + slot : '');
  };

  // ───────────────────────── Get files window ─────────────────────────
  function filesHtml(f) {
    var dates = (f.dates || []).slice(); if (dates.indexOf(f.date) < 0) dates.unshift(f.date);
    var sel = '<select onchange="laPickDate(this.value)" style="background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:6px 9px;font-size:12.5px">' +
      dates.map(function (x) { return '<option' + (x === f.date ? ' selected' : '') + '>' + esc(x) + '</option>'; }).join('') + '</select>';
    var lastWith = (f.slots.filter(function (x) { return x.files.length; }).slice(-1)[0] || {}).slot;
    var rows = (f.slots || []).map(function (s) {
      var open = S.ffocus ? s.slot === S.ffocus : s.slot === lastWith;
      var body = s.files.length ? s.files.map(function (x) {
        return '<div class="la-frow"><span class="n">' + esc(x.name) + '</span><span style="color:var(--dim);font-size:11px">' + kb(x.size) + ' · ' + esc((x.mtime || '').slice(11, 16)) + '</span>' +
          '<a href="' + API + '/live-alloc/file?date=' + encodeURIComponent(f.date) + '&slot=' + s.slot + '&name=' + encodeURIComponent(x.name) + '" download>⬇</a></div>';
      }).join('') : '<div style="font-size:12px;color:var(--dim);padding:6px 0">No files in this folder.</div>';
      return '<details' + (open ? ' open' : '') + ' style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px">' +
        '<summary style="cursor:pointer;font-size:13px;font-weight:650">Folder ' + s.slot + ' <span style="font-weight:400;color:var(--dim)">· ' + esc(s.time) + ' · ' + s.files.length + ' file' + (s.files.length === 1 ? '' : 's') + ' · ' + esc(s.state) + '</span></summary>' +
        '<div style="margin-top:6px">' + body + (s.files.length ? '<div style="margin-top:8px"><button class="sa-tool" onclick="laZip(\'' + f.date + '\',' + s.slot + ')">⬇ Folder ' + s.slot + ' (zip)</button></div>' : '') + '</div></details>';
    }).join('');
    return '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px">' + sel +
      '<button class="sa-tool" onclick="laZip(\'' + f.date + '\')">📁 Full folder (whole day, zip)</button></div>' +
      '<div style="font-size:11.5px;color:var(--dim);margin-bottom:10px">Same layout as on disk: <span style="font-family:var(--mono)">' + esc(f.date) + ' / 1 … 8 / files</span></div>' + rows;
  }
  window.laFiles = function (focus) {
    S.ffocus = focus || 0;
    var date = S.fdate || (S.data && S.data.date) || '';
    if (typeof saShowModal !== 'function') return;
    saShowModal(TITLE, '<div style="padding:14px;color:var(--dim)">Loading…</div>');
    get('/live-alloc/files?date=' + encodeURIComponent(date)).then(function (f) { S.fdate = f.date; saShowModal(TITLE, filesHtml(f)); })
      .catch(function () { saShowModal(TITLE, '<div style="color:var(--red)">Could not read the folders.</div>'); });
  };
  window.laPickDate = function (d) { S.fdate = d; S.ffocus = 0; window.laFiles(0); };

  // ───────────────────────── start ─────────────────────────
  function start() { if (mount()) load(); else setTimeout(start, 600); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
