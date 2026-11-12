/* MARGIN SHORTAGE (15-min) - a small KPI tile at the END of the Process Center's "Processing & Reports" row (same look as
   MCX CASH MAINTENANCE / LIVE ALLOCATION). "Open" shows the window: KPI cards (runs done, running now, needs attention, next run)
   and below them the Margin Shortage box itself - all its buttons (Email, Credentials, Upload, Backup, Stop, Logs, NMASS/NCDEX,
   MCX files, Test run), the auto-run switch and the run tiles - exactly as before.
   The box is NOT rebuilt: the existing element (#pxBox-ms15, with every handler and the live status) is moved into the window
   when it opens and parked (hidden) in its old place when the window closes or another window (Credentials, Logs ...) takes over.
   Injected at serve time (see api.py /legacy); dashboard.html is not modified. */
(function () {
  'use strict';
  if (window.__ms15CardInjected) return;
  window.__ms15CardInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'Margin Shortage (15-min)';
  var S = { ph: null, timer: null, data: null };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function get(path) { return fetch(API + path, { cache: 'no-store' }).then(function (r) { return r.json(); }); }
  function hideTip() { var t = $('globalTip'); if (t) t.style.display = 'none'; }

  var css = document.createElement('style');
  css.textContent =
    '.ms-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px}' +
    '.ms-kpi{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0}' +
    '.ms-kpi .k{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)}' +
    '.ms-kpi .v{font-size:22px;font-weight:750;margin-top:3px;font-variant-numeric:tabular-nums}' +
    '.ms-kpi .v small{font-size:12px;font-weight:600;color:var(--dim)}' +
    '.ms-kpi .f{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.ms-bar{height:5px;border-radius:9px;background:var(--line);margin-top:7px;overflow:hidden}.ms-bar i{display:block;height:100%;background:var(--green);transition:width .6s}' +
    '@media(max-width:820px){.ms-kpis{grid-template-columns:repeat(2,1fr)}}' +
    '#saModalBody #pxBox-ms15{margin:0}';
  document.head.appendChild(css);

  // ───────────────────────── the tile ─────────────────────────
  function gridOf() {
    var t = $('task-mcxcash') || $('task-liveallocation') || $('physdelSlot');
    if (t) return t.parentNode;
    var sec = [].slice.call(document.querySelectorAll('.sec')).filter(function (s) { return /processing/i.test(s.textContent); })[0];
    return sec ? sec.nextElementSibling : null;
  }
  function build() {
    if ($('task-ms15card')) return true;
    var grid = gridOf(); if (!grid) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-ms15card';
    tile.style.cssText = '--tile-accent:#fbbf24;cursor:pointer';
    tile.onclick = function () { msOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">📉</div>' +
      '<div class="tile-name">MARGIN SHORTAGE (15-min) <span class="st" id="msChip">idle</span></div>' +
      '<div class="tile-desc">Computes the margin shortfall 17 times a day, e-mails the main and zone reports and sends the WhatsApp margin alerts. Files download by themselves before each run.</div>' +
      '<div class="tile-desc" id="msMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#fbbf24;color:#2a1a02" onclick="event.stopPropagation();msOpen()" data-tip="Open Margin Shortage: the runs, auto-run switch, e-mail, credentials, files, backup, logs.">Open</button></div>';
    grid.appendChild(tile);
    return true;
  }
  // keep it the LAST tile even when other cards are injected later
  function keepLast() {      // the last tiles of the row, in this order: MARGIN SHORTAGE, MCX COMM LIMIT, NCDEX COMM LIMIT
    var els = ['task-ms15card', 'task-mcxcomm', 'task-ncdexcomm'].map(function (i) { return document.getElementById(i); }).filter(Boolean);
    if (!els.length || !els[0].parentNode) return;
    var p = els[0].parentNode; els = els.filter(function (e) { return e.parentNode === p; });
    var kids = [].slice.call(p.children), tail = kids.slice(kids.length - els.length);
    if (!els.every(function (e, i) { return tail[i] === e; })) els.forEach(function (e) { p.appendChild(e); });
  }


  // ───────────────────────── park / show the existing box ─────────────────────────
  function box() { return $('pxBox-ms15'); }
  function ensurePark() {
    var b = box(); if (!b) return null;
    if (S.ph && S.ph.isConnected) return S.ph;
    var prev = b.previousElementSibling;
    if (prev && prev.classList.contains('sec')) prev.style.display = 'none';      // the old "Margin Shortage (15-min)" heading
    var ph = document.createElement('div'); ph.id = 'ms15Park'; ph.style.display = 'none';
    b.parentNode.insertBefore(ph, b); ph.appendChild(b);
    S.ph = ph;
    return ph;
  }
  function inModal() { var b = box(); return !!(b && b.closest && b.closest('#saModalBody')); }
  function park() {
    var b = box(), ph = ensurePark();
    if (b && ph && b.parentNode !== ph) ph.appendChild(b);
    widen(false);
  }
  function widen(on) {
    var m = $('saModal'); var card = m && m.querySelector('.sa-modal-card');
    if (card) card.style.width = on ? 'min(1180px,97vw)' : '';
  }
  // another window (Credentials, Logs ...) replaces this one: park the box first, and come back here when that one closes
  (function hooks() {
    var show = window.saShowModal, close = window.saCloseModal;
    if (typeof show === 'function' && !show.__ms) {
      var f = function () { if (inModal()) { park(); if (!window._saReturn) window._saReturn = msOpen; } return show.apply(this, arguments); };
      f.__ms = true; window.saShowModal = f; S.show = show;
    }
    if (typeof close === 'function' && !close.__ms) {
      var g = function () { park(); return close.apply(this, arguments); };
      g.__ms = true; window.saCloseModal = g;
    }
  })();

  // ───────────────────────── the window ─────────────────────────
  function msOpen() {
    var b = box(); if (!b) return;
    ensurePark();
    hideTip();
    window._saReturn = null;
    var show = S.show || window.saShowModal;
    show(TITLE, '<div id="msKpis"><div style="color:var(--muted);font-size:12px;margin-bottom:12px">Checking…</div></div><div id="msBoxHost"></div>');
    var host = $('msBoxHost'); if (host) host.appendChild(b);
    widen(true);
    if (S.data) paintKpis(S.data);
    load();
  }
  window.msOpen = msOpen;

  // ───────────────────────── KPI cards + tile text ─────────────────────────
  function kpi(k, v, f, extra) { return '<div class="ms-kpi"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="f" title="' + esc(f) + '">' + esc(f) + '</div>' + (extra || '') + '</div>'; }
  function compute(px, au) {
    var runs = (px && px.runs) || 0, done = Math.max(0, Math.min(runs, (px && px.run_state) || 0));
    var rows = (au && au.runs) || [];
    var bad = rows.filter(function (r) { return r.state === 'failed' || r.state === 'missed'; });
    var nx = rows.filter(function (r) { return r.run === done + 1; })[0];
    var running = (typeof window._pxIsRunning === 'function') ? !!window._pxIsRunning('ms15') : false;
    var auto = !!(au && au.settings && au.settings.auto);
    return { runs: runs, done: done, bad: bad, next: nx, running: running, auto: auto, hasAuto: !!au };
  }
  function paintKpis(c) {
    var el = $('msKpis'); if (!el) return;
    var nxTxt = c.next ? c.next.time : (c.runs && c.done >= c.runs ? 'done' : '—');
    var runLabel = c.running ? 'MS15-0' + (c.done + 1) : '—';
    el.innerHTML = '<div class="ms-kpis">' +
      kpi('Runs done', c.done + '<small> / ' + (c.runs || 0) + '</small>', 'runs completed today',
        '<div class="ms-bar"><i style="width:' + (c.runs ? Math.round(100 * c.done / c.runs) : 0) + '%"></i></div>') +
      kpi('Running now', runLabel, c.running ? 'a run is going' : 'nothing running') +
      kpi('Needs attention', '<span style="color:' + (c.bad.length ? 'var(--red)' : 'var(--green)') + '">' + c.bad.length + '</span>',
        c.bad.length ? 'failed / missed: ' + c.bad.map(function (r) { return r.time; }).join(', ') : 'all good') +
      kpi('Next run', nxTxt, c.next ? (c.auto ? 'files download and the run starts by itself' : 'auto is OFF - run it yourself') : 'no more today') +
      '</div>';
  }
  function paintTile(c) {
    var chip = $('msChip'), mini = $('msMini'); if (!chip || !mini) return;
    chip.textContent = c.running ? 'running' : c.bad.length ? 'attention' : (c.hasAuto && !c.auto) ? 'auto off' : 'idle';
    chip.style.color = c.running ? 'var(--blue)' : c.bad.length ? 'var(--red)' : (c.hasAuto && !c.auto) ? 'var(--amber)' : 'var(--dim)';
    mini.textContent = c.running ? 'MS15-0' + (c.done + 1) + ' running…' :
      c.done + ' / ' + (c.runs || 0) + ' done' + (c.bad.length ? ' · ' + c.bad.length + ' need attention' : '') +
      (c.next ? ' · next ' + c.next.time : '') + ((c.hasAuto && !c.auto) ? ' · auto OFF' : '');
  }
  function load() {
    var next = 30000;
    return Promise.all([
      get('/px/status?pipe=ms15').catch(function () { return null; }),
      get('/ms15/auto/status').catch(function () { return null; })
    ]).then(function (r) {
      if (r[0]) { var c = compute(r[0], r[1]); S.data = c; paintTile(c); if (inModal()) { paintKpis(c); next = 4000; } }
    }).catch(function () { next = 30000; }).then(function () { schedule(next); });
  }
  function schedule(ms) {
    clearTimeout(S.timer);
    S.timer = setTimeout(function () { if (document.hidden) schedule(ms); else load(); }, ms);
  }

  // ───────────────────────── start ─────────────────────────
  function start() {
    if (build()) {
      ensurePark(); load();
      var n = 0, iv = setInterval(function () { keepLast(); if (++n > 20) clearInterval(iv); }, 1500);   // others inject their tiles a little later
    } else setTimeout(start, 600);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
