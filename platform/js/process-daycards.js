/* MASTER FILE / SA / EODSAR - three small cards at the end of the Process Center's "Processing & Reports" row (same look as
   76 FILES DOWNLOADER). "Open" shows the window: date (default = previous trading day), one panel per process with a
   pre-check of every file it needs, Run, its output files; Manual files (SA, EODSAR); Email To / CC, Credentials,
   Backup files (zip of the files a process used + its output), Logs and Stop.
   Inputs are READ from where they already are (76 FILES DOWNLOADER, LIVE ALLOCATION CAPTURE, COMMON FILES, manual files);
   a run works in a temporary folder; outputs go to <console data>\<CARD>\<date>\<PROCESS>\ and are e-mailed by the script.
   Backend: /daycards/* (api.py) -> core/daycards.py. Injected at serve time (see api.py /legacy). */
(function () {
  'use strict';
  if (window.__dayCardsInjected) return;
  window.__dayCardsInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var META = null, ST = {};

  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function $(id) { return document.getElementById(id); }
  function kb(n) { n = Number(n || 0); return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }
  function get(path) { return fetch(API + path, { cache: 'no-store' }).then(function (r) { return r.json(); }); }
  function post(path, body) {
    return fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }
  function hideTip() { var t = $('globalTip'); if (t) { t.style.display = 'none'; t.classList.remove('show'); } }
  function modal(t, h) { hideTip(); if (typeof window.saShowModal === 'function') window.saShowModal(t, h); }
  function dmy(iso) { var p = String(iso || '').split('-'); return p.length === 3 ? p[2] + '-' + p[1] + '-' + p[0] : iso; }
  function S(card) { return ST[card] || (ST[card] = { data: null, date: '', sig: '', timer: null, lastRunAt: 0, logTimer: null, open: {}, bk: null, msgTimer: null }); }
  function cardMeta(card) { return ((META && META.cards) || []).filter(function (c) { return c.key === card; })[0]; }

  var css = document.createElement('style');
  css.textContent =
    '.dc-p{border:1px solid var(--line);border-radius:13px;padding:13px 15px;margin:0 0 12px;background:var(--panel-2,rgba(255,255,255,.03))}' +
    '.dc-p.ok{border-color:rgba(52,211,153,.4)}.dc-p.bad{border-color:rgba(248,113,113,.45)}.dc-p.run{border-color:var(--amber)}' +
    '.dc-h{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.dc-t{font-family:var(--display);font-size:15px;font-weight:750}' +
    '.dc-chip{font-family:var(--mono);font-size:9.5px;letter-spacing:.7px;text-transform:uppercase;padding:2px 9px;border-radius:20px;border:1px solid var(--line);color:var(--dim)}' +
    '.dc-chip.ok{color:var(--green);border-color:rgba(52,211,153,.5)}.dc-chip.bad{color:var(--red);border-color:rgba(248,113,113,.5)}.dc-chip.run{color:var(--amber);border-color:rgba(251,191,36,.5)}' +
    '.dc-note{font-size:11.5px;color:var(--muted);margin:4px 0 8px;line-height:1.45}' +
    '.dc-bar{height:5px;border-radius:9px;background:var(--line);overflow:hidden;margin:6px 0 4px}.dc-bar i{display:block;height:100%;background:var(--green);transition:width .5s}' +
    '.dc-btn{font-family:var(--sans);font-weight:650;font-size:12px;border:0;border-radius:20px;padding:7px 16px;cursor:pointer;background:var(--blue);color:var(--blue-d)}' +
    '.dc-btn.ghost{background:transparent;border:1px solid var(--line-2);color:var(--ink)}.dc-btn.ghost:hover{border-color:var(--blue);color:var(--blue)}.dc-btn:disabled{background:var(--panel-3);color:var(--dim);cursor:not-allowed}.dc-btn.amber:disabled{background:var(--amber);color:var(--amber-d);cursor:default;opacity:1}' +
    '.dc-link{background:none;border:0;color:var(--muted);font-size:11px;cursor:pointer;text-decoration:underline;padding:0}' +
    '.dc-list{font-size:11px;color:var(--muted);line-height:1.55;border-top:1px solid var(--line);padding-top:6px;margin-top:6px;max-height:150px;overflow:auto}' +
    '.dc-list .m{color:var(--red)}.dc-list .w{color:var(--dim);font-size:10px}' +
    '.dc-out{display:flex;gap:8px;align-items:center;font-size:12px;padding:3px 0}.dc-out a{color:var(--blue);text-decoration:none}' +
    '.dc-man{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:0 0 12px}' +
    '.dc-m{border:1px solid var(--line);border-radius:12px;padding:10px 12px;font-size:12px}.dc-m.ok{border-color:rgba(52,211,153,.4)}.dc-m.bad{border-color:rgba(248,113,113,.45)}' +
    '@keyframes dcSpin{to{transform:rotate(360deg)}}.dc-spin{display:inline-block;width:13px;height:13px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:dcSpin .8s linear infinite;vertical-align:-2px;margin-right:7px}' +
    '.dc-log{font-family:var(--mono);font-size:10.5px;line-height:1.65;margin:0 0 12px;padding:9px 12px;border:1px solid var(--line-2);border-radius:9px;background:rgba(255,255,255,.02);max-height:200px;overflow:auto;white-space:pre-wrap;word-break:break-word}' +
    '.dc-msg{font-size:12px;margin:0 0 10px;padding:8px 12px;border-radius:9px;border:1px solid var(--line-2)}' +
    '.dc-lbl{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 5px}' +
    '.dc-in{background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit}' +
    '.dc-chip2{padding:5px 12px;border-radius:20px;font-size:11.5px;font-weight:600;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--dim)}' +
    '.dc-chip2.on{border-color:rgba(59,130,246,.6);background:rgba(59,130,246,.14);color:var(--blue)}' +
    '.dc-drop{border:1.5px dashed var(--line-2);border-radius:14px;padding:14px 16px;margin:0 0 12px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;cursor:pointer;transition:.15s;background:rgba(255,255,255,.015)}' +
    '.dc-drop:hover,.dc-drop.over{border-color:var(--blue);background:rgba(59,130,246,.07)}.dc-drop .ic{font-size:26px}' +
    '.dc-pill{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px;padding:4px 11px;border-radius:20px;border:1px solid var(--line)}' +
    '.dc-pill.ok{color:var(--green);border-color:rgba(52,211,153,.5);background:rgba(52,211,153,.07)}.dc-pill.bad{color:var(--red);border-color:rgba(248,113,113,.5);background:rgba(248,113,113,.07)}' +
    '.dc-res{font-size:11.5px;line-height:1.6;margin:-4px 0 12px;padding:8px 12px;border:1px solid var(--line);border-radius:10px}.dc-res .e{color:var(--red)}.dc-res .g{color:var(--green)}' +
    '.dc-frow{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:12px}.dc-frow .n{flex:1;min-width:0;overflow-wrap:anywhere;font-family:var(--mono);font-size:11.5px}';
  document.head.appendChild(css);

  // ───────────────────────── the small cards ─────────────────────────
  function buildTile(c, after) {
    var id = 'task-dc-' + c.key;
    if ($(id)) return $(id);
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = id;
    tile.style.cssText = '--tile-accent:' + c.accent + ';cursor:pointer';
    tile.onclick = function () { dcOpen(c.key); };
    tile.innerHTML =
      '<div class="tile-icon">' + c.icon + '</div>' +
      '<div class="tile-name">' + esc(c.title) + ' <span class="st" id="dcChip-' + c.key + '">idle</span></div>' +
      '<div class="tile-desc">' + esc(c.desc) + '</div>' +
      '<div class="tile-desc" id="dcMini-' + c.key + '" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:' + c.accent + ';color:#fff" onclick="event.stopPropagation();dcOpen(\'' + c.key + '\')" data-tip="Open ' + esc(c.title) + ': pre-check of the files, run, outputs, backup files, logs.">Open</button></div>';
    after.parentNode.insertBefore(tile, after.nextSibling);
    return tile;
  }
  function buildAll() {
    if (!META) return false;
    var anchor = $('task-files70new') || $('task-mcxcash') || $('task-liveallocation') || $('physdelSlot');
    if (!anchor) return false;
    META.cards.forEach(function (c) { anchor = buildTile(c, anchor); });
    if (anchor && anchor.parentNode) anchor.parentNode.style.gridAutoRows = '1fr';      // every row as tall as the tallest card
    return true;
  }
  function modalOpen(card) {
    var m = $('saModal'), d = $('dcDyn');
    return !!(m && m.classList.contains('show') && d && d.getAttribute('data-card') === card);
  }
  function widen(on) { var m = $('saModal'), c = m && m.querySelector('.sa-modal-card'); if (c) c.style.width = on ? 'min(960px,96vw)' : ''; }
  (function hookClose() {
    var orig = window.saCloseModal;
    if (typeof orig !== 'function' || orig.__dc) return;
    var f = function () { widen(false); return orig.apply(this, arguments); };
    f.__dc = true; window.saCloseModal = f;
  })();

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;padding-left:6px;padding-right:6px;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  window.dcSub = function (card, fn) { window._saReturn = function () { dcOpen(card); }; hideTip(); fn(); [60, 250, 700].forEach(function (ms) { setTimeout(hideTip, ms); }); };

  function dcOpen(card) {
    var c = cardMeta(card); if (!c) return;
    window._saReturn = null;
    var q = "'" + card + "'";
    modal(c.title,
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:0 0 14px">' +
        tb('✉️ Email To / CC', 'dcSub(' + q + ',function(){pxConfig(\'dc_' + card + '\')})', 'Who each report is e-mailed to (To and CC), per process.') +
        tb('🔑 Credentials', 'dcSub(' + q + ',function(){pxCredentials(\'dc_' + card + '\')})', 'The e-mail login these reports are sent from. Empty = the shared login of Scheduler > Connections.') +
        tb('📦 Backup files', 'dcSub(' + q + ',function(){dcBackup(' + q + ')})', 'Zip of the files a process used and the file it made, for any date.') +
        tb('🗂️ Logs', 'dcSub(' + q + ',function(){pxLogs(\'dc_' + card + '\')})', 'The full log of every run.') +
        tb('⛔ Stop', 'dcStop(' + q + ')', 'Stops the run that is going on now.', ';border-color:rgba(248,113,113,.45);color:var(--red)') +
      '</div><div id="dcMsg"></div><div id="dcDyn" data-card="' + card + '"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    widen(true);
    S(card).sig = '';
    load(card);
  }
  window.dcOpen = dcOpen;

  function say(txt, kind) {
    var el = $('dcMsg'); if (!el) return;
    el.innerHTML = txt ? '<div class="dc-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    var s = ST._msg = ST._msg || {}; clearTimeout(s.t);
    if (txt) s.t = setTimeout(function () { var e = $('dcMsg'); if (e) e.innerHTML = ''; }, 9000);
  }

  // ───────────────────────── render ─────────────────────────
  function procPanel(card, p, job) {
    var running = job.running && job.current === p.key;
    var last = p.last || {};
    var chip, cls = '';
    if (running) { chip = '<span class="dc-chip run"><span class="dc-spin" style="width:9px;height:9px;margin-right:5px"></span>running</span>'; cls = 'run'; }
    else if (last.finished) {
      var t = String(last.finished).replace('T', ' ').slice(11, 16);
      if (last.ok) { chip = '<span class="dc-chip ok">✓ done ' + t + '</span>'; cls = 'ok'; }
      else { chip = '<span class="dc-chip bad">✗ ' + (last.stage === 'pre-check' ? 'stopped - files missing' : 'failed') + ' ' + t + '</span>'; cls = 'bad'; }
    } else chip = '<span class="dc-chip">not run yet</span>';
    var pct = p.total ? Math.round(100 * p.found / p.total) : 0, miss = p.total - p.found;
    var open = !!S(card).open[p.key];
    var missHtml = '';
    if (open) {
      missHtml = '<div class="dc-list">' + (p.groups || []).filter(function (g) { return g.missing && g.missing.length; }).map(function (g) {
        return '<div><b style="color:var(--ink)">' + esc(g.name) + '</b> <span class="w">' + g.found + ' / ' + g.total + (g.optional ? ' · optional' : '') + '</span><br>' +
          g.missing.map(function (m) { return '<span class="' + (g.optional ? 'w' : 'm') + '">✗ ' + esc(m) + '</span>'; }).join('<br>') + '</div>';
      }).join('<div style="height:6px"></div>') + '</div>';
    }
    var lastErr = '';
    if (!running && last.finished && !last.ok) {
      if (last.stage === 'pre-check' && (last.missing || []).length) {
        lastErr = '<div class="dc-list" style="max-height:120px"><b style="color:var(--red)">Last run stopped at the pre-check - not found:</b><br>' +
          last.missing.slice(0, 25).map(function (m) { return '<span class="m">✗ ' + esc(m) + '</span>'; }).join('<br>') + (last.missing.length > 25 ? '<br>… and ' + (last.missing.length - 25) + ' more' : '') + '</div>';
      } else lastErr = '<div class="dc-list"><span class="m">Last run failed' + (last.error ? ': ' + esc(last.error) : '') + ' - see 🗂️ Logs.</span></div>';
    }
    var outs = (p.outputs || []).map(function (o) {
      return '<div class="dc-out">📄 <a href="' + API + '/daycards/output?card=' + card + '&proc=' + p.key + '&date=' + encodeURIComponent(S(card).data.iso) + '&name=' + encodeURIComponent(o.name) + '" download>' + esc(o.name) + '</a><span style="color:var(--dim);font-size:11px">' + kb(o.size) + '</span></div>';
    }).join('');
    return '<div class="dc-p ' + cls + '"><div class="dc-h"><span class="dc-t">' + esc(p.label) + '</span>' + chip +
      '<span style="margin-left:auto;display:flex;gap:8px"><button class="dc-btn" ' + (job.running ? 'disabled' : '') + ' onclick="dcRun(\'' + card + '\',[\'' + p.key + '\'])">▶ Run</button></span></div>' +
      '<div class="dc-note">' + esc(p.note) + '</div>' +
      '<div style="font-size:12px"><b>' + p.found + '</b><span style="color:var(--dim)"> / ' + p.total + ' required files ready</span> ' +
      (miss ? '<button class="dc-link" onclick="dcToggle(\'' + card + '\',\'' + p.key + '\')">' + (open ? 'hide' : 'show') + ' missing files (' + miss + ')</button>' : '<span style="color:var(--green)">✓ pre-check passes</span>') +
      ((p.later || []).length ? ' <span style="color:var(--dim);font-size:11px">· ' + p.later.length + ' file' + (p.later.length > 1 ? 's' : '') + ' will be made by ' + esc(p.later[0].from) + ' first</span>' : '') + '</div>' +
      '<div class="dc-bar"><i style="width:' + pct + '%"></i></div>' + missHtml + lastErr +
      (outs ? '<div style="margin-top:8px"><div class="dc-lbl">Output</div>' + outs + '</div>' : '') +
      '<div style="font-size:10.5px;color:var(--dim);margin-top:6px">E-mailed to: ' + esc(p.to || '-') + (p.cc ? ' · Cc: ' + esc(p.cc) : '') + '</div></div>';
  }

  function acceptedHtml(d) {
    var n = 0, body = (d.accepted || []).map(function (g) {
      n += g.files.length;
      return '<div style="margin-bottom:6px"><b style="color:var(--ink)">' + esc(g.group) + '</b><br>' + g.files.map(function (f) { return '<span style="font-family:var(--mono)">' + esc(f) + '</span>'; }).join('<br>') + '</div>';
    }).join('');
    return '<details style="margin:-4px 0 12px;font-size:11.5px;color:var(--muted)"><summary style="cursor:pointer">Files accepted by the upload (' + n + ') - anything else is refused</summary><div class="dc-list" style="max-height:220px">' + body + '</div></details>';
  }
  function manualHtml(card, d) {
    var s = S(card);
    var pills = ((d.manual && d.manual.slots) || []).map(function (x) {
      return '<span class="dc-pill ' + (x.present ? 'ok' : 'bad') + '">' + (x.present ? '✓' : '✗') + ' ' + esc(x.label) + '</span>';
    }).join('');
    var res = (s.upl || []).length ? '<div class="dc-res">' + s.upl.map(function (r) {
      return r.ok ? '<div class="g">✓ ' + esc(r.name) + ' <span style="color:var(--dim)">→ ' + esc(r.where) + '</span></div>' : '<div class="e">✗ ' + esc(r.name) + ' - ' + esc(r.error) + '</div>';
    }).join('') + '</div>' : '';
    return '<div class="dc-drop" id="dcDrop" onclick="dcPick(\'' + card + '\')" ondragover="event.preventDefault();this.classList.add(\'over\')" ondragleave="this.classList.remove(\'over\')" ondrop="dcDrop(event,\'' + card + '\')">' +
      '<span class="ic">⬆️</span><div style="flex:1;min-width:220px"><div style="font-weight:700;font-size:13.5px">Upload files for ' + esc(d.date) + '</div>' +
      '<div style="font-size:11.5px;color:var(--muted);margin-top:2px">Drop or choose files - only the files listed below are accepted, each is put in its right folder by its name. Normally the downloader brings them; use this (or ⬆ Upload files in 76 FILES DOWNLOADER) when one is missing.</div></div>' +
      (pills ? '<div style="display:flex;gap:8px;flex-wrap:wrap">' + pills + '</div>' : '') + '</div>' + res + acceptedHtml(d);
  }

  function paint(card, force) {
    var d = S(card).data, el = $('dcDyn');
    if (!el || !d || !d.procs || el.getAttribute('data-card') !== card) return;
    var sig = JSON.stringify(d);
    if (sig === S(card).sig && !force) return;
    S(card).sig = sig;
    var job = d.job || {}, c = cardMeta(card);
    var old = $('dcLogBox'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30), keep = old ? old.scrollTop : 0;
    var showLog = job.running || (Date.now() - S(card).lastRunAt < 25000);
    if (!job.running && showLog) { clearTimeout(S(card).logTimer); S(card).logTimer = setTimeout(function () { S(card).sig = ''; paint(card, true); }, 25200 - (Date.now() - S(card).lastRunAt)); }
    var other = job.running && job.date && job.date !== d.date;
    var allBtn = job.running
      ? '<button class="dc-btn amber" disabled><span class="dc-spin"></span>Running ' + esc(job.current || '') + ' for ' + esc(job.date || '') + '…</button>'
      : '<button class="dc-btn" onclick="dcRun(\'' + card + '\',null)" data-tip="Runs the processes below that are not done yet for this date, one after the other, and stops at the first one that fails. A process that is already done is skipped - use its own Run to repeat it.">▶ ' + (d.procs.length < 2 ? 'Run' : todo(card).length === d.procs.length ? 'Run all (in order)' : todo(card).length ? 'Run the rest (' + todo(card).length + ' left)' : 'All done') + '</button>';
    el.innerHTML =
      '<div style="display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin:0 0 12px">' +
        '<div><div class="dc-lbl">Trade date</div><input type="date" id="dcDate" class="dc-in" value="' + esc(d.iso) + '" onchange="dcSetDate(\'' + card + '\',this.value)"></div>' +
        (d.iso !== d.default_iso ? '<button class="dc-link" style="margin-bottom:9px" onclick="dcSetDate(\'' + card + '\',\'' + esc(d.default_iso) + '\')">↺ back to the previous trading day (' + esc(dmy(d.default_iso)) + ')</button>'
          : '<span style="font-size:11.5px;color:var(--dim);margin-bottom:9px">previous trading day</span>') +
        '<div style="margin-bottom:2px">' + allBtn + '</div>' +
        '<span style="font-size:11px;color:var(--dim);margin:0 0 9px auto;text-align:right;line-height:1.5">' +
          (d.auto && d.auto.time ? (d.auto.on ? 'Auto run: every trading day at ' + esc(d.auto.time) + (d.manual ? ' (upload the manual files before)' : '') : 'Auto run is off') + ' · change under 🔑 Credentials<br>' : '') +
          'Outputs: <span style="font-family:var(--mono)">' + esc(d.folder) + '</span></span></div>' +
      (other ? '<div class="dc-msg" style="color:var(--amber)">A run for ' + esc(job.date) + ' is going on - its progress shows when you pick that date.</div>' : '') +
      (showLog ? '<div class="dc-lbl">Live log <span style="text-transform:none;letter-spacing:0">- the full log of each run is under 🗂️ Logs</span></div><div class="dc-log" id="dcLogBox">' + ((job.lines || []).length ? job.lines.slice(-60).map(esc).join('\n') : 'Starting…') + '</div>' : '') +
      manualHtml(card, d) +
      d.procs.map(function (p) { return procPanel(card, p, job); }).join('') +
      '<div style="font-size:11px;color:var(--dim);line-height:1.6">Every process starts with a <b>pre-check</b>: if a file it needs is not found it stops at once, says which one and where to get it, and sends no e-mail. ' +
      'Files are read from where they already are (76 FILES DOWNLOADER, LIVE ALLOCATION CAPTURE, COMMON FILES' + (d.manual ? ', manual files' : '') + ') - nothing is copied into another folder; a run works in a temporary folder that is removed afterwards.</div>';
    var nb = $('dcLogBox'); if (nb) nb.scrollTop = stick ? nb.scrollHeight : keep;
  }

  function paintTile(card, d) {
    var chip = $('dcChip-' + card), mini = $('dcMini-' + card);
    if (!chip || !d || !d.procs) return;
    var job = d.job || {}, bad = 0, done = 0;
    d.procs.forEach(function (p) { if (p.outputs && p.outputs.length) done++; if (p.found < p.total) bad++; });
    chip.textContent = job.running ? 'running' : done === d.procs.length ? 'done' : bad ? 'files missing' : 'ready';
    chip.style.color = job.running ? 'var(--blue)' : done === d.procs.length ? 'var(--green)' : bad ? 'var(--amber)' : 'var(--dim)';
    mini.textContent = job.running ? 'Running ' + (job.current || '') + '…' : d.date + ': ' + done + ' / ' + d.procs.length + ' reports made' + (bad && done < d.procs.length ? ' · ' + bad + ' need files' : '');
  }

  function load(card) {
    var s = S(card), next = 20000;
    var q = '?card=' + card + (s.date ? '&date=' + encodeURIComponent(s.date) : '');
    return get('/daycards/status' + q).then(function (d) {
      if (d && d.procs) {
        s.data = d;
        if (!s.date) s.date = d.iso;
        if (d.job && d.job.running) { s.lastRunAt = Date.now(); next = 2500; }
        paintTile(card, d);
        if (modalOpen(card)) paint(card);
      } else if (d && d.error && modalOpen(card)) { $('dcDyn').innerHTML = '<div style="color:var(--red)">' + esc(d.error) + '</div>'; }
    }).catch(function () { next = 25000; }).then(function () {
      clearTimeout(s.timer);
      s.timer = setTimeout(function () { if (document.hidden) { s.timer = setTimeout(function () { load(card); }, next); } else { load(card); } }, next);
    });
  }
  function reload(card) { S(card).sig = ''; clearTimeout(S(card).timer); setTimeout(function () { load(card); }, 700); }

  // ───────────────────────── actions ─────────────────────────
  window.dcSetDate = function (card, iso) { if (!iso) return; S(card).date = iso; S(card).sig = ''; load(card); };
  window.dcToggle = function (card, key) { S(card).open[key] = !S(card).open[key]; paint(card, true); };
  function isDone(p) { return !!(p && p.last && p.last.ok && p.outputs && p.outputs.length); }
  function todo(card) { var d = S(card).data; return d ? d.procs.filter(function (p) { return !isDone(p); }) : []; }
  window.dcRun = function (card, procs) {
    var s = S(card), d = s.date || (s.data && s.data.iso), skipped = [];
    var keys = procs;
    if (!keys) {                                   // "Run all": only what is not done yet for this date
      var all = s.data ? s.data.procs : [];
      keys = all.filter(function (p) { return !isDone(p); }).map(function (p) { return p.key; });
      skipped = all.filter(isDone).map(function (p) { return p.label; });
      if (!keys.length) { say('Everything is already done for ' + dmy(d) + '. To repeat one, press its own Run.', 'err'); return; }
    }
    post('/daycards/run', { card: card, date: d, procs: keys }).then(function (j) {
      if (j && j.ok) { s.lastRunAt = Date.now(); say('Started for ' + dmy(d) + (skipped.length ? ' - skipped (already done): ' + skipped.join(', ') + '.' : ' - it begins with the pre-check of the files.')); }
      else say((j && (j.error || j.detail)) || 'Could not start.', 'err');
      reload(card);
    });
  };
  window.dcStop = function (card) {
    if (!window.confirm('Stop the run that is going on now?')) return;
    post('/daycards/stop', { card: card }).then(function (j) { say(j && j.stopped ? 'Stopping…' : 'Nothing is running.'); reload(card); });
  };
  function sendFiles(card, files) {
    var s = S(card), d = s.date || (s.data && s.data.iso), list = Array.prototype.slice.call(files || []);
    if (!list.length) return;
    s.upl = [];
    var chain = Promise.resolve();
    list.forEach(function (f) {
      chain = chain.then(function () {
        return fetch(API + '/daycards/upload?card=' + card + '&date=' + encodeURIComponent(d) + '&name=' + encodeURIComponent(f.name), { method: 'POST', body: f })
          .then(function (r) { return r.json().then(function (j) { return j; }); })
          .then(function (j) { s.upl.push(j && j.ok ? { ok: true, name: j.name, where: j.where } : { ok: false, name: f.name, error: (j && (j.error || j.detail)) || 'not saved' }); })
          .catch(function (e) { s.upl.push({ ok: false, name: f.name, error: String(e) }); });
      });
    });
    chain.then(function () {
      var ok = s.upl.filter(function (x) { return x.ok; }).length;
      say(ok + ' of ' + list.length + ' file' + (list.length > 1 ? 's' : '') + ' saved' + (ok < list.length ? ' - see the list for the others.' : '.'), ok < list.length ? 'err' : '');
      reload(card);
    });
  }
  window.dcPick = function (card) {
    var i = document.createElement('input'); i.type = 'file'; i.multiple = true; i.style.display = 'none';
    i.onchange = function () { sendFiles(card, i.files); i.remove(); };
    document.body.appendChild(i); i.click();
  };
  window.dcDrop = function (ev, card) { ev.preventDefault(); ev.currentTarget.classList.remove('over'); sendFiles(card, ev.dataTransfer && ev.dataTransfer.files); };

  // ───────────────────────── Backup files ─────────────────────────
  function bk(card) {
    var s = S(card);
    if (!s.bk) s.bk = { date: s.date || (s.data && s.data.iso) || '', procs: {}, io: 'both', mode: 'all', sel: {}, data: null, q: '', init: false };
    return s.bk;
  }
  window.dcBackup = function (card) {
    var b = bk(card), c = cardMeta(card);
    b.date = S(card).date || b.date;
    if (!b.init) { c.procs.forEach(function (p) { b.procs[p.key] = true; }); b.init = true; }
    modal('📦 ' + c.title + ' - backup files',
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin-bottom:12px">Pick a date and the processes. The zip holds the files they <b>used</b> (inputs, laid out like the day folder) and/or the file they <b>made</b> (under OUTPUT). ' +
      'Files that are not there are listed in red.</div>' +
      '<div style="display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin-bottom:10px"><div><div class="dc-lbl">Date</div><input type="date" id="dcBkDate" class="dc-in" value="' + esc(b.date) + '" onchange="dcBkDate(\'' + card + '\',this.value)"></div>' +
      '<div style="display:flex;gap:10px;flex-wrap:wrap">' + c.procs.map(function (p) {
        return '<label style="display:flex;gap:6px;align-items:center;font-size:12.5px"><input type="checkbox" ' + (b.procs[p.key] ? 'checked' : '') + ' onchange="dcBkProc(\'' + card + '\',\'' + p.key + '\',this.checked)"> ' + esc(p.label) + '</label>';
      }).join('') + '</div></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">' +
        [['both', 'Inputs + output'], ['in', 'Inputs only'], ['out', 'Output only']].map(function (x) { return '<button class="dc-chip2' + (b.io === x[0] ? ' on' : '') + '" onclick="dcBkIo(\'' + card + '\',\'' + x[0] + '\')">' + x[1] + '</button>'; }).join('') +
        '<span style="width:14px"></span>' +
        [['all', 'All these files'], ['files', 'Choose files']].map(function (x) { return '<button class="dc-chip2' + (b.mode === x[0] ? ' on' : '') + '" onclick="dcBkMode(\'' + card + '\',\'' + x[0] + '\')">' + x[1] + '</button>'; }).join('') + '</div>' +
      '<div id="dcBkBody" style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px">Loading…</div></div>' +
      '<button class="sa-save" id="dcBkGo" data-no-busy="1" style="width:100%" onclick="dcBkDownload(\'' + card + '\')">⬇ Download zip</button><div class="sa-msg" id="dcBkMsg"></div>');
    widen(true);
    dcBkLoad(card);
  };
  function chosenProcs(card) { var b = bk(card); return Object.keys(b.procs).filter(function (k) { return b.procs[k]; }); }
  window.dcBkLoad = function (card) {
    var b = bk(card), pr = chosenProcs(card);
    get('/daycards/files?card=' + card + '&date=' + encodeURIComponent(b.date) + '&procs=' + encodeURIComponent(pr.join(','))).then(function (j) { b.data = j; bkPaint(card); })
      .catch(function () { var l = $('dcBkBody'); if (l) l.innerHTML = '<div style="color:var(--red)">Could not read the folders.</div>'; });
  };
  window.dcBkDate = function (card, v) { var b = bk(card); if (!v) return; b.date = v; b.sel = {}; dcBkLoad(card); };
  window.dcBkProc = function (card, k, on) { bk(card).procs[k] = on; bk(card).sel = {}; dcBkLoad(card); };
  window.dcBkIo = function (card, io) { var b = bk(card); b.io = io; dcBackup(card); };
  window.dcBkMode = function (card, m) { var b = bk(card); b.mode = m; dcBackup(card); };
  function shown(card) {
    var b = bk(card);
    return ((b.data && b.data.files) || []).filter(function (f) { return b.io === 'both' || f.io === b.io; });
  }
  function picked(card) {
    var b = bk(card), fs = shown(card);
    return b.mode === 'files' ? fs.filter(function (f) { return b.sel[f.rel]; }) : fs;
  }
  function bkPaint(card) {
    var b = bk(card), l = $('dcBkBody'); if (!l || !b.data) return;
    var fs = shown(card), html = '';
    var miss = (b.data.missing || []).filter(function (m) { return b.io !== 'out'; });
    if (miss.length) html += '<div class="dc-list" style="border:1px solid rgba(248,113,113,.4);border-radius:10px;padding:8px 12px;margin:0 0 10px"><b style="color:var(--red)">Not there (' + miss.length + '):</b><br>' + miss.slice(0, 30).map(function (m) { return '<span class="m">✗ ' + esc(m.label) + ' <span class="w">[' + esc(m.group) + ']</span></span>'; }).join('<br>') + (miss.length > 30 ? '<br>… and ' + (miss.length - 30) + ' more' : '') + '</div>';
    if (!fs.length) html += '<div style="color:var(--dim);font-size:13px;padding:8px 0">No files for ' + esc(dmy(b.date)) + ' with this choice.</div>';
    else if (b.mode === 'files') {
      var q = (b.q || '').toLowerCase(), byFolder = {};
      fs.forEach(function (f) {
        if (q && f.rel.toLowerCase().indexOf(q) < 0) return;
        var parts = f.rel.split('/'), k = f.io === 'out' ? 'OUTPUT · ' + (parts[2] || '') : parts.length > 2 ? parts[1] : '(day folder)';
        (byFolder[k] = byFolder[k] || []).push(f);
      });
      html += '<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px"><input type="text" class="dc-in" style="flex:1" placeholder="Search a file name…" value="' + esc(b.q) + '" oninput="dcBkSearch(\'' + card + '\',this.value)">' +
        '<button class="sa-tool" onclick="dcBkAll(\'' + card + '\',true)">Select all shown</button><button class="sa-tool" onclick="dcBkAll(\'' + card + '\',false)">Clear</button></div>' +
        '<div style="max-height:44vh;overflow:auto">' + Object.keys(byFolder).sort().map(function (k) {
          var arr = byFolder[k], on = arr.filter(function (f) { return b.sel[f.rel]; }).length;
          return '<details style="border:1px solid var(--line);border-radius:10px;padding:6px 12px;margin-bottom:6px"' + (q || Object.keys(byFolder).length < 3 ? ' open' : '') + '><summary style="cursor:pointer;font-size:12.5px;font-weight:650">' + esc(/^\d$/.test(k) ? 'Live Allocation Capture · folder ' + k : k) +
            ' <span style="font-weight:400;color:var(--dim)">· ' + arr.length + ' files' + (on ? ' · ' + on + ' ticked' : '') + '</span></summary>' +
            arr.map(function (f) {
              return '<div class="dc-frow"><input type="checkbox" ' + (b.sel[f.rel] ? 'checked' : '') + ' onchange="dcBkTick(\'' + card + '\',' + esc(JSON.stringify(f.rel)) + ',this.checked)"><span class="n">' + esc(f.rel.split('/').pop()) + '</span><span style="color:var(--dim);font-size:11px">' + kb(f.size) + '</span></div>';
            }).join('') + '</details>';
        }).join('') + '</div>';
    }
    var p = picked(card), size = p.reduce(function (a, f) { return a + (f.size || 0); }, 0);
    l.innerHTML = html + (fs.length ? '<div style="font-size:12px;color:var(--muted);margin-top:10px"><b style="color:var(--ink)">' + p.length + '</b> file' + (p.length === 1 ? '' : 's') + ' · ' + kb(size) + ' will be in the zip</div>' : '');
    var go = $('dcBkGo'); if (go) { go.disabled = !p.length; go.style.opacity = p.length ? '1' : '.45'; go.textContent = p.length ? '⬇ Download zip (' + p.length + ' file' + (p.length === 1 ? '' : 's') + ')' : '⬇ Download zip'; }
  }
  window.dcBkTick = function (card, rel, on) { var b = bk(card); if (on) b.sel[rel] = true; else delete b.sel[rel]; bkPaint(card); };
  window.dcBkSearch = function (card, v) { var b = bk(card); b.q = v; bkPaint(card); var i = document.querySelector('#dcBkBody input[type=text]'); if (i) { i.focus(); i.setSelectionRange(v.length, v.length); } };
  window.dcBkAll = function (card, on) {
    var b = bk(card), q = (b.q || '').toLowerCase();
    shown(card).forEach(function (f) { if (!q || f.rel.toLowerCase().indexOf(q) >= 0) { if (on) b.sel[f.rel] = true; else delete b.sel[f.rel]; } });
    bkPaint(card);
  };
  window.dcBkDownload = function (card) {
    var b = bk(card), p = picked(card), m = $('dcBkMsg'), go = $('dcBkGo'), c = cardMeta(card);
    if (!p.length) return;
    var body = { card: card, date: b.date, procs: chosenProcs(card), io: b.io };
    if (b.mode === 'files') body.files = p.map(function (f) { return f.rel; });
    if (go) { go.disabled = true; go.innerHTML = '<span class="dc-spin"></span>Making the zip…'; }
    fetch(API + '/daycards/zip', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { if (!r.ok) throw new Error(r.status === 404 ? 'No files for that choice.' : 'Server answered ' + r.status); return r.blob(); })
      .then(function (blob) {
        var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = c.title.replace(/ /g, '_') + '_' + dmy(b.date) + '.zip';
        document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
        if (m) m.innerHTML = '<span style="color:var(--green)">✓ Zip downloaded (' + p.length + ' files).</span>';
      })
      .catch(function (e) { if (m) m.innerHTML = '<span style="color:var(--red)">' + esc(e.message || e) + '</span>'; })
      .then(function () { bkPaint(card); });
  };

  // ───────────────────────── start ─────────────────────────
  function start() {
    if (!META) { get('/daycards/meta').then(function (m) { META = m; start(); }).catch(function () { setTimeout(start, 3000); }); return; }
    if (buildAll()) META.cards.forEach(function (c) { load(c.key); }); else setTimeout(start, 700);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
