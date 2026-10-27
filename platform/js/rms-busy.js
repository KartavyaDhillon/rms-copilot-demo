/* RMS Copilot - automatic "working..." spinner on EVERY button that starts a server request.
   House rule: any button that does something shows a small spinning circle while it works, so nobody wonders whether the
   click did anything.  This file does it for all buttons at once (no per-button code needed):
     - it remembers which button was just clicked;
     - if that click starts a request (fetch) within 0.4 s, a small circle is put in the button until the request(s) finish
       (shown for at least 0.6 s so even an instant answer is noticed);
     - buttons that already show their own spinner (class pdSpin / rms-spin inside) are left alone.
   Loaded by platform/index.html and injected into the Process Center page (api.py). */
(function () {
  'use strict';
  if (window.__rmsBusy) return;
  window.__rmsBusy = true;

  var st = document.createElement('style');
  st.textContent = '@keyframes rmsSpin{to{transform:rotate(360deg)}}' +
    '.rms-spin{display:inline-block;width:14px;height:14px;box-sizing:border-box;border:2px solid currentColor;border-right-color:transparent;' +
    'border-radius:50%;animation:rmsSpin .8s linear infinite;vertical-align:-2px;margin-right:8px;flex:none}' +
    'button[data-rms-busy]{cursor:progress}';
  (document.head || document.documentElement).appendChild(st);

  var last = null, lastAt = 0;
  document.addEventListener('click', function (e) {
    var b = e.target && e.target.closest ? e.target.closest('button') : null;
    if (b && !b.disabled) { last = b; lastAt = Date.now(); }
  }, true);

  function mark(btn) {
    if (btn.hasAttribute('data-rms-busy')) { btn.__rmsN = (btn.__rmsN || 1) + 1; return btn.__rmsN; }
    if (btn.querySelector('.pdSpin,.rms-spin,.spinner,[class*="spin"]')) return 0;   // it has its own spinner
    var s = document.createElement('span'); s.className = 'rms-spin';
    btn.insertBefore(s, btn.firstChild);
    btn.setAttribute('data-rms-busy', '1'); btn.setAttribute('aria-busy', 'true');
    btn.__rmsSpin = s; btn.__rmsN = 1; btn.__rmsT = Date.now();
    return 1;
  }
  function unmark(btn) {
    btn.__rmsN = (btn.__rmsN || 1) - 1;
    if (btn.__rmsN > 0) return;
    var wait = Math.max(0, 600 - (Date.now() - (btn.__rmsT || 0)));
    setTimeout(function () {
      if (btn.__rmsN > 0) return;
      btn.removeAttribute('data-rms-busy'); btn.removeAttribute('aria-busy');
      if (btn.__rmsSpin && btn.__rmsSpin.parentNode) btn.__rmsSpin.parentNode.removeChild(btn.__rmsSpin);
      btn.__rmsSpin = null;
    }, wait);
  }

  var realFetch = window.fetch;
  if (typeof realFetch !== 'function') return;
  window.fetch = function () {
    var btn = last, p = realFetch.apply(this, arguments);
    try {
      if (btn && Date.now() - lastAt < 400 && document.contains(btn) && mark(btn)) {
        var done = function () { unmark(btn); };
        p.then(done, done);
      }
    } catch (e) { /* never get in the way of a request */ }
    return p;
  };
})();
