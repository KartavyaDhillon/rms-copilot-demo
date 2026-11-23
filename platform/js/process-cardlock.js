/* process-cardlock.js - ONE lock for every KPI card window (CNC F11, PAYOUT CHECK, OBLIGATION REPORT, TENTATIVE PAYIN PAYOUT, MCX / NCDEX
   COMM LIMIT, MTF CONCENTRATION / ORDER FILE, ENTERPRISE RISK, LIVE ALLOCATION, MCX CASH MAINTENANCE, 76 FILES ...).
   While a card is running - started by ANYONE, on any screen - its Run buttons are greyed ("Running - by <name>") for everybody, a
   "running" strip names who started it and when, and a click on a Run button is stopped before anything is sent. Stop, Logs, Settings ...
   stay usable. The server refuses a second run as well (HTTP 409), this is only the instant, visible side of it.
   Source: GET /ops/card-locks (core/card_locks.py), polled every second while the window is open (every 4 s otherwise).
   Injected at serve time (api.py /legacy). */
(function () {
  'use strict';
  if (window.__cardLockInjected) return;
  window.__cardLockInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var LOCKS = {}, EXTRA = {}, LAST_OK = 0;
  // windows whose run is tracked by the console's other lock list (/ops/locks) instead of /ops/card-locks: window title -> lock pipes
  var PIPE_TITLE = { 'PHYSICAL DELIVERY': ['physdel', 'physdelfile'] };
  // what stays usable while a card runs: Stop, Logs, Backup files and read-only viewing/downloading. Everything else (Upload, Run, Email,
  // WhatsApp, Credentials, Settings, Safe test, Override ...) is locked, so nobody changes a file or a setting under a running process.
  var ALLOW = /(^|[^a-z])(stop|logs?|backup|download|zip|view|open|copy|close|back|refresh|reload|cancel|preview)([^a-z]|$)|^[\s×✕✖xX]*$/i;

  var css = document.createElement('style');
  css.textContent = 'button[data-cl="1"]{opacity:.55!important;cursor:not-allowed!important}' +
    '.cl-strip{display:flex;align-items:center;gap:10px;margin:0 0 12px;padding:9px 13px;border:1px solid rgba(251,191,36,.45);border-radius:11px;background:rgba(251,191,36,.09);font-size:12.5px;color:var(--ink,#e9eef6)}' +
    '.cl-sp{display:inline-block;width:12px;height:12px;box-sizing:border-box;border:2px solid #fbbf24;border-right-color:transparent;border-radius:50%;animation:clSp .8s linear infinite;flex:none}' +
    '@keyframes clSp{to{transform:rotate(360deg)}}';
  document.head.appendChild(css);

  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function $(id) { return document.getElementById(id); }
  function lockFor(t) { return t ? (LOCKS[t] || EXTRA[t] || null) : null; }
  function allowed(b) { return b.hasAttribute('data-lock-ok') || ALLOW.test((b.textContent || '').replace(/\s+/g, ' ').trim()) || /close|modal-x|sa-x/i.test(b.className || '') || b.getAttribute('aria-label') === 'Close'; }
  function title() { var t = $('saModalTitle'), m = $('saModal'); return (t && m && m.classList.contains('show')) ? (t.textContent || '').trim() : ''; }
  function isRun(b) { return /^\s*[^A-Za-z]*run\b/i.test((b.textContent || '').replace(/\s+/g, ' ')); }
  function hhmm(since) { return since && since.length >= 16 ? since.slice(11, 16) : ''; }
  function who(l) { return (l.by ? 'by ' + l.by : 'by someone else') + (hhmm(l.since) ? ' · since ' + hhmm(l.since) : ''); }

  function unlock(body) {
    [].slice.call(body.querySelectorAll('button[data-cl="1"]')).forEach(function (b) {
      if (b.dataset.clOrig !== undefined) b.innerHTML = b.dataset.clOrig;
      b.disabled = b.dataset.clWas === '1'; b.removeAttribute('title'); delete b.dataset.cl; delete b.dataset.clOrig; delete b.dataset.clWas;
    });
    [].slice.call(body.querySelectorAll('input[data-cl="1"]')).forEach(function (i) { i.disabled = i.dataset.clWas === '1'; delete i.dataset.cl; delete i.dataset.clWas; });
    var s = $('clStrip'); if (s) s.remove();
  }
  function apply() {
    var m = $('saModalBody'); if (!m) return;
    var t = title(), l = lockFor(t);
    if (!l) { unlock(m); return; }
    var strip = $('clStrip'), html = '<span class="cl-sp"></span><span><b>' + esc(l.label) + '</b> is running — started ' + esc(who(l)) + '. Run, Upload and settings are locked until it finishes - Backup files and Logs stay open.</span>';
    if (!strip) { strip = document.createElement('div'); strip.id = 'clStrip'; strip.className = 'cl-strip'; m.insertBefore(strip, m.firstChild); }
    if (strip.dataset.sig !== html) { strip.innerHTML = html; strip.dataset.sig = html; }
    [].slice.call(m.querySelectorAll('button')).forEach(function (b) {
      if (b.dataset.cl === '1') return;
      if (isRun(b)) {
        b.dataset.clOrig = b.innerHTML; b.dataset.cl = '1'; b.dataset.clWas = b.disabled ? '1' : ''; b.disabled = true;
        b.innerHTML = '<span class="cl-sp" style="margin-right:6px"></span>Running…';
      } else if (!allowed(b)) {
        b.dataset.cl = '1'; b.dataset.clWas = b.disabled ? '1' : ''; b.disabled = true; b.title = 'Locked while this process is running';
      }
    });
    [].slice.call(m.querySelectorAll('input[type="file"]')).forEach(function (i) {
      if (i.dataset.cl === '1') return;
      i.dataset.cl = '1'; i.dataset.clWas = i.disabled ? '1' : ''; i.disabled = true;
    });
  }

  // a click on a Run button of a running card never goes out (also covers the half second before the next poll)
  document.addEventListener('click', function (e) {
    var b = e.target && e.target.closest ? e.target.closest('button') : null;
    var m = $('saModalBody');
    if (!b || !m || !m.contains(b)) return;
    var t = title(), l = lockFor(t);
    if (l && (isRun(b) || !allowed(b))) {
      e.preventDefault(); e.stopImmediatePropagation();
      notice(l);
      return;
    }
    if (t && isRun(b) && b.dataset.cl !== '1') setTimeout(poll, 250);       // a run is being started: look again at once
  }, true);

  function notice(l) {
    var old = $('clNote'); if (old) old.remove();
    var bg = document.createElement('div'); bg.id = 'clNote';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100010;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--amber,#fbbf24);border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px;color:var(--amber,#fbbf24)">🔒 ' + esc(l.label) + ' is already running</div>' +
      '<div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">Started ' + esc(who(l)) + '. Run, Upload and settings are locked until it finishes (Backup files and Logs stay open) - they unlock by themselves.</div>' +
      '<div style="display:flex;justify-content:flex-end"><button class="sa-tool" data-no-busy="1" id="clNoteOk" style="min-width:90px">OK</button></div></div>';
    document.body.appendChild(bg);
    $('clNoteOk').onclick = function () { bg.remove(); };
    bg.onclick = function (ev) { if (ev.target === bg) bg.remove(); };
  }

  // ---- instant feedback: a spinning circle on the Upload / Run button the moment it is pressed, until the server has answered ----
  var lastRun = { b: null, t: 0 };
  document.addEventListener('click', function (e) {
    var b = e.target && e.target.closest ? e.target.closest('button') : null, m = $('saModalBody');
    if (b && m && m.contains(b) && b.dataset.cl !== '1' && isRun(b)) lastRun = { b: b, t: Date.now() };
  }, true);
  function spin(b, text) {
    if (!b || b.dataset.clSpin) return function () {};
    b.dataset.clSpin = '1'; b.dataset.clSpinOrig = b.innerHTML;
    b.innerHTML = '<span class="cl-sp" style="margin-right:6px"></span>' + text;
    var t0 = Date.now();
    return function () {                       // shown for at least 0.7 s so even an instant answer is noticed
      setTimeout(function () {
        if (b.dataset.clSpin && b.dataset.clSpinOrig !== undefined) b.innerHTML = b.dataset.clSpinOrig;
        delete b.dataset.clSpin; delete b.dataset.clSpinOrig;
        poll();
      }, Math.max(0, 700 - (Date.now() - t0)));
    };
  }
  function uploadBtn() {
    var m = $('saModalBody'); if (!m) return null;
    return [].slice.call(m.querySelectorAll('button')).filter(function (b) { return /upload/i.test(b.textContent || '') && !/backup|common/i.test(b.textContent || ''); })[0] || null;
  }
  var realFetch = window.fetch;
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '', method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    var done = null;
    try {
      if (title() && !/\/ops\//.test(url)) {
        if (method === 'POST' && /\/upload(\?|$)/.test(url)) done = spin(uploadBtn(), 'Uploading…');
        else if ((method === 'POST' && /\/run(\?|$)/.test(url)) || /\/settle\/need(\?|$)/.test(url)) {
          if (lastRun.b && Date.now() - lastRun.t < 20000) done = spin(lastRun.b, 'Starting…');
        }
      }
    } catch (e) { done = null; }
    var p = realFetch.apply(this, arguments);
    if (done) p.then(done, done);
    return p;
  };

  function poll() {
    return fetch(API + '/ops/card-locks', { cache: 'no-store', credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
      LOCKS = (d && d.cards) || {}; LAST_OK = Date.now(); return extra();
    }).then(apply).catch(function () {});
  }
  // windows that are not in /ops/card-locks (PHYSICAL DELIVERY ...): the console's other lock list, matched by pipe or by name
  function extra() {
    var t = title(); EXTRA = {};
    if (!t || LOCKS[t]) return null;
    return fetch(API + '/ops/locks', { cache: 'no-store', credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
      (d.locks || []).forEach(function (l) {
        var pipes = PIPE_TITLE[t] || [], lab = String(l.label || '').toUpperCase();
        if (pipes.indexOf(l.pipe) >= 0 || (lab && lab.length > 4 && lab.indexOf(t) >= 0))
          EXTRA[t] = { key: l.pipe, running: true, by: l.by || '', since: l.started_at || '', label: t };
      });
    }).catch(function () {});
  }
  function loop() {
    poll().then(function () { setTimeout(loop, document.hidden ? 5000 : (title() ? 1000 : 4000)); });
  }
  // the console re-draws its windows: keep the lock on whatever is drawn
  new MutationObserver(function () { if (title()) apply(); }).observe(document.body, { childList: true, subtree: true });
  window.cardLockPoll = poll;
  loop();
})();
