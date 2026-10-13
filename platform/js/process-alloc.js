/* Allocation Report — tile in "Processing & Reports" + modal (day so far / up to a time / custom range,
   optional client codes, download or e-mail). Backend: /alloc/* (api.py) → core/pipelines/allocation_report.py.
   The nightly close is NOT started here — it runs from the Scheduler. Injected at serve time (see api.py /legacy). */
(function () {
  'use strict';
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'Allocation Report';
  var S = { mode: 'sofar', until: '15:00', fd: '', ft: '10:00', td: '', tt: '14:30', clients: [], deliver: 'download',
            mailto: '', data: null, busy: false, msg: '', msgKind: '', timer: null, ctl: null };

  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"]/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]; }); }
  function n(x) { return Number(x || 0).toLocaleString('en-IN'); }
  function $(id) { return document.getElementById(id); }
  function dmy(iso) { var p = String(iso || '').slice(0, 10).split('-'); return p.length === 3 ? p[2] + '-' + p[1] + '-' + p[0] : ''; }
  function pretty(s) { s = String(s || ''); return s ? dmy(s) + ' ' + s.slice(11, 16) : ''; }

  function load() {
    return fetch(API + '/alloc/status', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) { S.data = d; if (!S.fd && d.today) { S.fd = d.today; S.td = d.today; } return d; });
  }

  // ───────────────────────── tile ─────────────────────────
  function tileRefresh() {
    var el = $('allocMini');
    if (!el) return;
    fetch(API + '/alloc/status', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
      var chip = $('allocChip'), lc = d.last_close || {}, p = d.progress || {};
      if (d.running) {
        el.textContent = 'Running · step ' + (p.step || 1) + '/' + (p.total || 5) + (p.msg ? ' — ' + p.msg : '');
        if (chip) { chip.textContent = 'running'; chip.style.color = 'var(--amber)'; }
      } else if (!d.script_ok) {
        el.textContent = 'Setup needed — allocation script not copied yet';
        if (chip) { chip.textContent = 'setup'; chip.style.color = 'var(--amber)'; }
      } else {
        el.textContent = lc.finished ? 'Last nightly close ' + pretty(lc.to) : (d.marker ? 'Nightly window starts ' + pretty(d.marker) : 'Open to run a report');
        if (chip) { chip.textContent = 'idle'; chip.style.color = 'var(--dim)'; }
      }
    }).catch(function () {});
  }

  function modalOpen() {
    var t = $('saModalTitle'), m = $('saModal');
    return !!(t && m && m.classList.contains('show') && t.textContent === TITLE);
  }

  // ───────────────────────── pieces ─────────────────────────
  function seg(id, items, cur, fn) {
    return '<div id="' + id + '" style="display:flex;gap:3px;padding:3px;border:1px solid var(--line);border-radius:10px;background:rgba(255,255,255,.03)">' +
      items.map(function (it) {
        var on = it[0] === cur;
        return '<button type="button" data-v="' + it[0] + '" onclick="' + fn + '(\'' + it[0] + '\')" style="flex:1;border:0;border-radius:7px;padding:7px 8px;font-size:12px;cursor:pointer;' +
          (on ? 'background:var(--line-2);color:var(--ink);font-weight:650' : 'background:transparent;color:var(--muted)') + '">' + it[1] + '</button>';
      }).join('') + '</div>';
  }
  function lbl(t, extra) { return '<div style="font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:0 0 6px">' + t + (extra || '') + '</div>'; }
  var INPS = 'background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px;font-size:12.5px;font-family:inherit;';
  var INP = 'style="' + INPS + '"';
  var INPW = 'style="' + INPS + 'width:100%;box-sizing:border-box"';

  function windowText() {
    var d = S.data || {}, today = dmy(d.today);
    if (S.mode === 'sofar') return today + ' 00:00 → now';
    if (S.mode === 'until') return today + ' 00:00 → ' + (S.until || '--:--');
    return dmy(S.fd) + ' ' + S.ft + ' → ' + dmy(S.td) + ' ' + S.tt;
  }
  function paintMode() {
    var b = $('allocSeg'); if (!b) return;
    [].forEach.call(b.querySelectorAll('button'), function (x) {
      var on = x.getAttribute('data-v') === S.mode;
      x.style.background = on ? 'var(--line-2)' : 'transparent';
      x.style.color = on ? 'var(--ink)' : 'var(--muted)';
      x.style.fontWeight = on ? '650' : '400';
    });
    $('allocUntil').style.display = S.mode === 'until' ? 'block' : 'none';
    $('allocCustom').style.display = S.mode === 'custom' ? 'block' : 'none';
    $('allocWin').textContent = windowText();
  }
  function paintDeliver() {
    var b = $('allocSeg2'); if (!b) return;
    [].forEach.call(b.querySelectorAll('button'), function (x) {
      var on = x.getAttribute('data-v') === S.deliver;
      x.style.background = on ? 'var(--line-2)' : 'transparent';
      x.style.color = on ? 'var(--ink)' : 'var(--muted)';
      x.style.fontWeight = on ? '650' : '400';
    });
    $('allocMail').style.display = S.deliver === 'mail' ? 'block' : 'none';
  }
  function paintChips() {
    var c = $('allocChips'); if (!c) return;
    c.innerHTML = S.clients.map(function (x, i) {
      return '<span style="display:inline-flex;align-items:center;gap:6px;background:rgba(96,165,250,.14);color:var(--blue);font-size:12px;padding:3px 9px;border-radius:7px;margin:0 6px 6px 0">' + esc(x) +
        '<span style="cursor:pointer;opacity:.7" onclick="allocDel(' + i + ')">&#10005;</span></span>';
    }).join('');
    $('allocCnt').textContent = S.clients.length ? S.clients.length + (S.clients.length > 1 ? ' clients' : ' client') : 'all clients';
  }

  function formHtml() {
    var d = S.data || {};
    var warn = d.script_ok ? '' :
      '<div style="font-size:12px;color:var(--amber);border:1px solid rgba(251,191,36,.35);border-radius:9px;padding:8px 12px;margin-bottom:10px">' +
      'Setup needed: copy <span style="font-family:var(--mono)">ALLOCATION_REPORT.py</span> to <span style="font-family:var(--mono)">core/pipelines/allocation_report_src.py</span> on the console machine.</div>';
    return warn +
      '<div style="font-size:11.5px;color:var(--dim);margin-bottom:12px">Nightly close: window starts <b style="color:var(--ink)">' + esc(pretty(d.marker) || '—') +
      '</b>. Reports run here never move it.</div>' +
      lbl('Time range') + seg('allocSeg', [['sofar', 'Day so far'], ['until', 'Up to a time'], ['custom', 'Custom range']], S.mode, 'allocMode') +
      '<div id="allocUntil" style="display:none;margin-top:10px">' + lbl('Up to (today)') + '<input type="time" id="allocUntilIn" value="' + esc(S.until) + '" ' + INP + ' oninput="allocSet(\'until\',this.value)"></div>' +
      '<div id="allocCustom" style="display:none;margin-top:10px"><div style="display:flex;gap:14px;flex-wrap:wrap">' +
      '<div>' + lbl('From') + '<input type="date" value="' + esc(S.fd) + '" ' + INP + ' oninput="allocSet(\'fd\',this.value)"> <input type="time" value="' + esc(S.ft) + '" ' + INP + ' oninput="allocSet(\'ft\',this.value)"></div>' +
      '<div>' + lbl('To') + '<input type="date" value="' + esc(S.td) + '" ' + INP + ' oninput="allocSet(\'td\',this.value)"> <input type="time" value="' + esc(S.tt) + '" ' + INP + ' oninput="allocSet(\'tt\',this.value)"></div></div></div>' +
      '<div id="allocWin" style="font-size:12px;color:var(--muted);margin:10px 0 14px;font-family:var(--mono)"></div>' +
      '<div style="border-top:1px solid var(--line);padding-top:12px">' +
      '<div style="display:flex;justify-content:space-between;align-items:baseline">' + lbl('Client codes', ' <span style="text-transform:none;letter-spacing:0;color:var(--dim)">(optional — blank means everyone)</span>') +
      '<span id="allocCnt" style="font-size:11px;color:var(--dim)"></span></div>' +
      '<div id="allocChips"></div>' +
      '<input type="text" id="allocCC" placeholder="Type a code and press Enter, or paste several" ' + INPW + ' onkeydown="allocKey(event)" onpaste="setTimeout(allocAdd,0)">' +
      '<div style="font-size:10.5px;color:var(--dim);margin-top:6px">Applies to every sheet of the report. Codes with no record in the window are listed after the run.</div></div>' +
      '<div style="border-top:1px solid var(--line);padding-top:12px;margin-top:14px">' + lbl('Delivery') +
      seg('allocSeg2', [['download', 'Download only'], ['mail', 'E-mail to']], S.deliver, 'allocDeliver') +
      '<div id="allocMail" style="display:none;margin-top:10px"><input type="text" id="allocMailIn" value="' + esc(S.mailto) + '" placeholder="name@mastertrust.co.in, second@mastertrust.co.in" ' + INPW + ' oninput="allocSet(\'mailto\',this.value)"></div></div>' +
      '<div id="allocBtnRow" style="display:flex;gap:8px;margin-top:16px"></div>' +
      '<div id="allocLive" style="font-size:11.5px;min-height:18px;margin-top:10px"></div>' +
      '<div id="allocResult"></div>';
  }

  function btnRow() {
    var r = $('allocBtnRow'); if (!r) return;
    var d = S.data || {};
    r.innerHTML = (d.running || S.busy)
      ? '<button class="sa-save" style="flex:1;background:var(--red);color:#1a0505" onclick="allocStop()">&#9940; Stop</button>'
      : '<button class="sa-save" style="flex:1" onclick="allocRun()">&#9654; Run report</button>';
  }

  function liveHtml() {
    var d = S.data || {}, p = d.progress || {};
    var spin = '<span style="display:inline-block;width:14px;height:14px;margin-right:8px;vertical-align:-3px;border:2px solid var(--line-2);border-top-color:var(--green);border-radius:50%;animation:spin .8s linear infinite"></span>';
    var out = '';
    if (d.running || S.busy) {
      var who = (d.lock && d.lock.mode === 'close') ? 'The nightly close is running — ' : '';
      out = '<div style="color:var(--muted)">' + spin + esc(who + (p.msg || 'Starting…')) + (p.step ? ' <span style="color:var(--dim)">(step ' + p.step + '/' + (p.total || 5) + ')</span>' : '') + '</div>';
    } else if (p.ok === false && p.msg) {
      out = '<div style="color:var(--red)">' + esc(p.msg) + '</div>';
    }
    if (S.msg) out += '<div style="margin-top:6px;color:' + (S.msgKind === 'bad' ? 'var(--red)' : (S.msgKind === 'ok' ? 'var(--green)' : 'var(--amber)')) + ';white-space:pre-wrap">' + S.msg + '</div>';
    return out;
  }

  function resultHtml() {
    var l = (S.data || {}).last_manual || {};
    if (!l.finished) return '';
    var tile = function (lab, val) {
      return '<div style="flex:1;min-width:92px;border:1px solid var(--line);border-radius:10px;padding:8px 10px"><div style="font-size:10px;letter-spacing:.5px;color:var(--dim);text-transform:uppercase">' + lab + '</div><div style="font-size:19px;font-weight:700;color:var(--ink)">' + val + '</div></div>';
    };
    var head = '<div style="font-size:10px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin:16px 0 6px;border-top:1px solid var(--line);padding-top:12px">Last manual report</div>' +
      '<div style="font-size:11px;color:var(--dim);margin-bottom:6px"><b style="color:var(--ink)">' + esc(pretty(l.from)) + ' → ' + esc(pretty(l.to)) + '</b>' +
      (l.clients && l.clients.length ? ' · ' + esc(l.clients.join(', ')) : ' · all clients') + ' · finished ' + esc(l.finished) + (l.by ? ' by ' + esc(l.by) : '') + '</div>';
    if (l.empty) return head + '<div style="font-size:12px;color:var(--amber)">' + esc(l.msg || 'Nothing to report for this window.') + '</div>';
    var files = (l.files || []).map(function (f) {
      return '<a class="sa-save" style="text-decoration:none;text-align:center;flex:1;min-width:190px;font-size:11.5px" href="' + API + '/alloc/file?name=' + encodeURIComponent(f) + '" download>&#11015; ' + esc(f.length > 34 ? f.slice(0, 31) + '…' : f) + '</a>';
    }).join('');
    return head + '<div style="display:flex;gap:8px;flex-wrap:wrap">' + tile('E-mails read', n(l.emails)) + tile('Rows reported', n(l.success_rows)) + tile('Not successful', n(l.excluded_rows)) + '</div>' +
      (l.missing_clients && l.missing_clients.length ? '<div style="font-size:11.5px;color:var(--amber);margin-top:8px">No record in this window for: ' + esc(l.missing_clients.join(', ')) + '</div>' : '') +
      (l.delivered ? '<div style="font-size:11.5px;color:var(--green);margin-top:8px">&#10003; E-mailed to ' + esc(l.delivered) + '</div>' : '') +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">' + files + '</div>' +
      ((l.opening_files === 0) ? '<div style="font-size:11px;color:var(--amber);margin-top:8px">No opening-balance files were found for this day — opening balances count as 0.</div>' : '');
  }

  function paintDynamic() {
    if (!modalOpen()) return;
    var a = $('allocLive'); if (a) a.innerHTML = liveHtml();
    var r = $('allocResult'); if (r) r.innerHTML = resultHtml();
    btnRow();
  }

  function poll() {
    if (!modalOpen()) { clearInterval(S.timer); S.timer = null; tileRefresh(); return; }
    load().then(paintDynamic).catch(function () {});
  }

  // ───────────────────────── handlers ─────────────────────────
  window.allocOpen = function () {
    if (typeof saShowModal !== 'function') return;
    S.msg = ''; S.msgKind = '';
    saShowModal(TITLE, '<div style="color:var(--muted);font-size:12px">Loading…</div>');
    load().then(function () {
      saShowModal(TITLE, formHtml());
      paintMode(); paintDeliver(); paintChips(); paintDynamic();
    }).catch(function () {
      saShowModal(TITLE, '<div style="color:var(--red);font-size:13px">Could not reach the server.</div>');
    });
    if (S.timer) clearInterval(S.timer);
    S.timer = setInterval(poll, 2500);
  };
  window.allocMode = function (v) { S.mode = v; S.msg = ''; paintMode(); };
  window.allocDeliver = function (v) { S.deliver = v; paintDeliver(); };
  window.allocSet = function (k, v) { S[k] = v; if (k !== 'mailto') { var w = $('allocWin'); if (w) w.textContent = windowText(); } };
  window.allocAdd = function () {
    var i = $('allocCC'); if (!i) return;
    String(i.value || '').split(/[\s,;]+/).forEach(function (c) {
      c = c.trim().toUpperCase().replace(/[^A-Z0-9_\-]/g, '');
      if (c && S.clients.indexOf(c) < 0) S.clients.push(c);
    });
    i.value = ''; paintChips();
  };
  window.allocKey = function (e) { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); window.allocAdd(); } };
  window.allocDel = function (i) { S.clients.splice(i, 1); paintChips(); };

  function setMsg(t, kind) { S.msg = t; S.msgKind = kind || ''; paintDynamic(); }

  window.allocRun = function () {
    var d = S.data || {};
    if (d.running || S.busy) return;
    if (!d.script_ok) { setMsg('Setup needed: the allocation script has not been copied to the console yet.', 'bad'); return; }
    window.allocAdd();
    var q = 'mode=' + S.mode + '&clients=' + encodeURIComponent(S.clients.join(',')) + '&deliver=' + S.deliver;
    if (S.mode === 'until') {
      if (!/^\d{1,2}:\d{2}$/.test(S.until || '')) { setMsg('Choose the time to report up to.', 'bad'); return; }
      q += '&until=' + encodeURIComponent(S.until);
    } else if (S.mode === 'custom') {
      if (!S.fd || !S.td || !S.ft || !S.tt) { setMsg('Fill in both dates and times.', 'bad'); return; }
      var a = S.fd + ' ' + S.ft, b = S.td + ' ' + S.tt;
      if (a >= b) { setMsg('The start must be before the end.', 'bad'); return; }
      q += '&frm=' + encodeURIComponent(a) + '&to=' + encodeURIComponent(b);
    }
    if (S.deliver === 'mail') {
      if (!/@/.test(S.mailto || '')) { setMsg('Enter at least one e-mail address.', 'bad'); return; }
      q += '&mailto=' + encodeURIComponent(S.mailto);
    }
    S.busy = true; setMsg('', '');
    var ctl = new AbortController(); S.ctl = ctl;
    fetch(API + '/alloc/run-stream?' + q, { signal: ctl.signal }).then(function (resp) {
      if (!resp.ok) return resp.json().then(function (j) { throw new Error(j.error || ('Server ' + resp.status)); });
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
              else if (ev.type === 'note') setMsg(esc(ev.msg), 'warn');
              else if (ev.type === 'stopped') setMsg('Stopped.', 'warn');
            } catch (e) {}
          });
          return pump();
        });
      }
      return pump();
    }).catch(function (e) {
      if (e && e.name === 'AbortError') return;
      setMsg(esc((e && e.message) || 'Connection interrupted — the run continues on the server; reopen this window to follow it.'), 'warn');
    }).then(function () { S.busy = false; S.ctl = null; return load(); }).then(function () {
      var p = (S.data || {}).progress || {};
      if (p.ok === true && !S.msg) setMsg('&#10003; Report ready — download below.', 'ok'); else paintDynamic();
      tileRefresh();
    });
  };

  window.allocStop = function () {
    fetch(API + '/alloc/stop', { method: 'POST' }).then(function (r) {
      if (r.status === 403) return r.json().then(function (j) { setMsg(esc(j.error || "You didn't start this run."), 'bad'); });
      if (S.ctl) { try { S.ctl.abort(); } catch (e) {} }
      return load().then(function () { S.busy = false; setMsg('Stopped.', 'warn'); });
    }).catch(function () {});
  };

  function init() {
    tileRefresh();
    setInterval(function () { if (!modalOpen()) tileRefresh(); }, 20000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
