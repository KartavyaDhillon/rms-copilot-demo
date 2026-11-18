/* 📲 WhatsApp alerts - one button for every console card that has a Credentials button.
   The numbers typed here get the red FAILURE ALERT picture on WhatsApp when a run of that card FAILS - started by its timer
   or by a button (the watcher in core/card_alerts.py reads the run logs). Blank = the common list (Scheduler > Credentials >
   WhatsApp). Cards that already keep their own numbers in their Settings (MTF ORDER FILE, MTF CONCENTRATION, ENTERPRISE RISK,
   MCX / NCDEX COMM LIMIT, CNC F11, Margin Shortage, Short Allocation) are not listed here.
   Backend: GET/POST /alert-numbers/<card> (api.py). Saving asks for the password, inside this popup.
   Every answer is an in-console popup (never the browser's own alert / confirm / prompt). */
(function () {
  'use strict';
  if (window.__waNumbersInjected) return;
  window.__waNumbersInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';

  // normalised window title (letters + digits, upper case) -> card key used by the run logs
  var CARDS = [
    ['ALLOCATIONREPORT', 'alloc'], ['LIVEALLOCATION', 'liveallocation'], ['MCXCASHMAINTENANCE', 'mcxcash'],
    ['PHYSICALDELIVERY', 'physdel'], ['MTFIMPACTCOST', 'mtfic'], ['76FILESDOWNLOADER', 'files70'],
    ['PROBABLE', 'probable'], ['CTCLVSNMASS', 'ctcl'], ['CLIENTPRUDENTIALLIMIT', 'cli'],
    ['R1R2COMPARISON', 'r1r2'], ['RECONCILIATION', 'reco']
  ];

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function cardOf(title) {
    var t = String(title || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    for (var i = 0; i < CARDS.length; i++) if (t.indexOf(CARDS[i][0]) === 0) return CARDS[i][1];
    return '';
  }

  function box(title, text, kind) {            // popup with an OK button
    var old = $('waOk'); if (old) old.remove();
    var col = kind === 'err' ? 'var(--red,#f87171)' : 'var(--green,#34d399)';
    var bg = document.createElement('div'); bg.id = 'waOk';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100004;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';border-radius:14px;padding:20px 22px;width:min(420px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px;color:' + col + '">' + (kind === 'err' ? '✕ ' : '✓ ') + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;justify-content:flex-end"><button class="sa-tool" data-no-busy="1" id="waOkBtn" style="min-width:90px;background:' + col + ';color:#06251a;border-color:transparent">OK</button></div></div>';
    document.body.appendChild(bg);
    $('waOkBtn').onclick = function () { bg.remove(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
    $('waOkBtn').focus();
  }

  window.waNumbersOpen = function (pipe, label) {
    fetch(API + '/alert-numbers/' + encodeURIComponent(pipe), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (cur) {
      var old = $('waNum'); if (old) old.remove();
      var bg = document.createElement('div'); bg.id = 'waNum';
      bg.style.cssText = 'position:fixed;inset:0;z-index:100003;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
      var inp = 'width:100%;box-sizing:border-box;background:transparent;border:1px solid var(--line-2,#2a3342);border-radius:8px;color:var(--ink,#e8ecf3);padding:8px 10px;font-size:13px;font-family:inherit';
      bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--line-2,#2a3342);border-radius:14px;padding:20px 22px;width:min(480px,94vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
        '<div style="font-size:15px;font-weight:700;margin-bottom:6px">📲 WhatsApp failure alerts - ' + esc(label) + '</div>' +
        '<div style="font-size:12.5px;color:var(--muted,#9aa4b5);line-height:1.55;margin-bottom:12px">When a run of this card FAILS (by its timer or by a button), these numbers get the red FAILURE ALERT picture on WhatsApp. 10-digit numbers (+91 is added), several separated by commas. Blank = the common alert numbers of Scheduler &gt; Credentials &gt; WhatsApp.</div>' +
        '<div style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim,#6b7686);margin-bottom:5px">Numbers</div>' +
        '<input id="waNumIn" type="text" style="' + inp + ';margin-bottom:10px" placeholder="9876543210, 9123456789" value="' + esc(cur.raw || '') + '">' +
        '<div style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim,#6b7686);margin-bottom:5px">Password</div>' +
        '<input id="waNumPw" type="password" style="' + inp + ';margin-bottom:14px" placeholder="the console password" autocomplete="off">' +
        '<div style="display:flex;gap:10px;justify-content:flex-end"><button class="sa-tool" data-no-busy="1" id="waNumNo" style="min-width:90px">Cancel</button>' +
        '<button class="sa-tool" data-no-busy="1" id="waNumGo" style="min-width:90px;background:var(--blue,#3b82f6);color:#06142e;border-color:transparent">Save</button></div></div>';
      document.body.appendChild(bg);
      $('waNumNo').onclick = function () { bg.remove(); };
      bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
      $('waNumIn').focus();
      $('waNumGo').onclick = function () {
        var btn = $('waNumGo'); btn.disabled = true; btn.textContent = 'Saving…';
        fetch(API + '/alert-numbers/' + encodeURIComponent(pipe), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: $('waNumPw').value, numbers: $('waNumIn').value })
        }).then(function (r) { return r.json(); }).then(function (j) {
          btn.disabled = false; btn.textContent = 'Save';
          if (j && j.ok) {
            bg.remove();
            box('Saved', j.count ? ('Failure alerts of ' + label + ' go to ' + j.count + ' number(s).') : ('No number of its own - ' + label + ' uses the common alert numbers.'));
          } else box('Not saved', (j && (j.error || j.detail)) || 'Could not save the numbers.', 'err');
        }).catch(function (e) { btn.disabled = false; btn.textContent = 'Save'; box('Not saved', (e && e.message) || String(e), 'err'); });
      };
    }).catch(function (e) { box('Could not open', (e && e.message) || String(e), 'err'); });
  };

  function inject() {
    var body = $('saModalBody'), t = $('saModalTitle'), m = $('saModal');
    if (!body || !t || !m || !m.classList.contains('show')) return;
    var pipe = cardOf(t.textContent);
    if (!pipe || $('waNumBtn')) return;
    var cred = [].slice.call(body.querySelectorAll('button')).filter(function (b) { return /Credentials/i.test(b.textContent) && !b.id; })[0];
    if (!cred) return;
    var b = document.createElement('button');
    b.id = 'waNumBtn'; b.type = 'button';
    b.className = cred.className; b.setAttribute('style', cred.getAttribute('style') || '');
    b.setAttribute('data-no-busy', '1');
    b.setAttribute('data-tip', 'WhatsApp numbers that get the FAILURE ALERT picture when a run of this card fails (timer or button).');
    b.textContent = '📲 WhatsApp alerts';
    var label = String(t.textContent || '').replace(/^[^A-Za-z0-9]+/, '').trim();
    b.onclick = function () { window.waNumbersOpen(pipe, label); };
    cred.parentNode.insertBefore(b, cred.nextSibling);
  }

  function start() {
    var body = $('saModalBody');
    if (!body) { setTimeout(start, 600); return; }
    new MutationObserver(function () { inject(); }).observe(body, { childList: true, subtree: false });
    inject();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
