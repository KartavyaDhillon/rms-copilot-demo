/* process-organizer.js - Process Center helpers, loaded LAST in the console page (injected by api.py /legacy).

   1. SEARCH / FILTER  (driven by the top search box of the platform shell)
        pcList(q)     -> [{kind:'process'|'card', title, hint}]  what matches q
        pcFilter(q,o) -> number of items left visible; shows ONLY the matching process boxes / KPI cards.
                         o.exact = the query is a full title (picked from the list): show just that one.
        pcClear()     -> show everything again
   2. KPI CARD ORDER   (the small tiles at the end of the page)
        default = A -> Z by name; drag a card to put it anywhere (saved in this browser);
        "A-Z" button = back to alphabetical, "Reset" = same. New cards are added in A-Z order after your own order.
   Nothing here touches what a card does. */
(function () {
  if (window.__pcOrganizer) return;
  window.__pcOrganizer = true;
  window.__tilesManaged = true;                 // the cards' own "keep me last" timers stand down (see keepLast in process-*.js)

  var LS_MODE = 'rms.tiles.mode', LS_ORDER = 'rms.tiles.order';
  function $(id) { return document.getElementById(id); }
  function norm(s) { return String(s || '').toLowerCase().replace(/\s+/g, ' ').trim(); }
  function lsGet(k, d) { try { var v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: the order just is not remembered */ } }

  // ───────────────────────── what is on the page ─────────────────────────
  function secs() { return Array.prototype.slice.call(document.querySelectorAll('#view-dashboard .sec')); }
  function groupOf(sec) {                       // a section header + everything up to the next header
    var els = [sec], n = sec.nextElementSibling;
    while (n && !n.classList.contains('sec')) { els.push(n); n = n.nextElementSibling; }
    return els;
  }
  function secTitle(sec) {
    var c = sec.cloneNode(true);
    Array.prototype.forEach.call(c.querySelectorAll('span,em'), function (x) { x.remove(); });
    return c.textContent.replace(/\s+/g, ' ').trim() || sec.textContent.replace(/\s+/g, ' ').trim();
  }
  function tileName(t) {
    var n = t.querySelector('.tile-name'); if (!n) return t.id;
    var c = n.cloneNode(true);
    Array.prototype.forEach.call(c.querySelectorAll('.st'), function (x) { x.remove(); });
    return c.textContent.replace(/\s+/g, ' ').trim();
  }
  function tiles() { return Array.prototype.slice.call(document.querySelectorAll('#view-dashboard .tile-grid .tile')); }
  function tileGrid() { var t = tiles()[0]; return t ? t.parentNode : null; }
  function isTileGroup(els) { return els.some(function (e) { return e.classList && e.classList.contains('tile-grid'); }); }

  function units() {
    var out = [];
    secs().forEach(function (sec) {
      var els = groupOf(sec);
      if (isTileGroup(els)) return;             // the cards are searched one by one below
      out.push({ kind: 'process', title: secTitle(sec), els: els, text: null, key: 'sec:' + secTitle(sec) });
    });
    tiles().forEach(function (t) {
      var d = t.querySelector('.tile-desc');
      out.push({ kind: 'card', title: tileName(t), els: [t], text: norm(tileName(t) + ' ' + (d ? d.textContent : '')), key: 'tile:' + t.id });
    });
    return out;
  }
  function textOf(u) {                          // the whole box text only when the title alone found nothing
    if (u.text == null) u.text = norm(u.els.map(function (e) { return e.textContent; }).join(' '));
    return u.text;
  }
  function matchWords(hay, q) {
    var w = q.split(' ').filter(Boolean);
    for (var i = 0; i < w.length; i++) if (hay.indexOf(w[i]) < 0) return false;
    return w.length > 0;
  }

  window.pcList = function (q) {
    q = norm(q);
    var us = units(), hits = us.filter(function (u) { return matchWords(norm(u.title), q); });
    if (!hits.length) hits = us.filter(function (u) { return matchWords(textOf(u), q); });
    return hits.map(function (u) { return { kind: u.kind, title: u.title }; });
  };

  // ───────────────────────── filter ─────────────────────────
  var hidden = [];
  function hide(el) { if (el.__pcHid) return; el.__pcHid = el.style.display || ' '; el.style.display = 'none'; hidden.push(el); }
  function showAll() {
    hidden.forEach(function (el) { el.style.display = el.__pcHid === ' ' ? '' : el.__pcHid; el.__pcHid = null; });
    hidden = [];
    var m = $('pcNoMatch'); if (m) m.remove();
  }
  window.pcClear = function () { showAll(); return 0; };

  window.pcFilter = function (q, o) {
    q = norm(q);
    showAll();
    if (!q) return -1;
    var us = units(), exact = !!(o && o.exact), keep;
    if (exact) keep = us.filter(function (u) { return norm(u.title) === q; });
    else {
      keep = us.filter(function (u) { return matchWords(norm(u.title), q); });
      if (!keep.length) keep = us.filter(function (u) { return matchWords(textOf(u), q); });
    }
    var keepSet = keep.reduce(function (m, u) { m.push(u); return m; }, []);
    us.forEach(function (u) { if (keepSet.indexOf(u) < 0) u.els.forEach(hide); });
    // headers of groups that lost everything, the card grid header + the order bar when no card is left
    var anyCard = keep.some(function (u) { return u.kind === 'card'; });
    secs().forEach(function (sec) {
      var els = groupOf(sec);
      if (isTileGroup(els)) {
        if (!anyCard) els.forEach(hide);
        else els.forEach(function (e) { if (e.classList.contains('sec')) { /* keep the header */ } });
      }
    });
    var bar = $('pcTileBar'); if (bar && !anyCard) hide(bar);
    if (!keep.length) {
      var host = secs()[0];
      if (host) {
        var d = document.createElement('div');
        d.id = 'pcNoMatch';
        d.style.cssText = 'padding:26px;text-align:center;color:var(--muted);border:1px dashed var(--line-2);border-radius:12px;margin:10px 0';
        d.textContent = 'No process or KPI card matches “' + q + '”.';
        host.parentNode.insertBefore(d, host);
      }
    } else {
      var first = keep[0].els[0];
      if (first && first.scrollIntoView) first.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }
    return keep.length;
  };

  // ───────────────────────── KPI card order ─────────────────────────
  var dragging = null, orderLock = false;
  function saved() { try { return JSON.parse(lsGet(LS_ORDER, '[]')) || []; } catch (e) { return []; } }
  function wantedOrder() {
    var ts = tiles(), mode = lsGet(LS_MODE, 'az');
    var byName = ts.slice().sort(function (a, b) { return tileName(a).localeCompare(tileName(b), undefined, { sensitivity: 'base', numeric: true }); });
    if (mode !== 'custom') return byName;
    var ids = saved(), out = [];
    ids.forEach(function (id) { var t = $(id); if (t && ts.indexOf(t) >= 0 && out.indexOf(t) < 0) out.push(t); });
    byName.forEach(function (t) { if (out.indexOf(t) < 0) out.push(t); });
    return out;
  }
  function applyOrder() {
    if (orderLock || dragging) return;
    var g = tileGrid(); if (!g) return;
    var want = wantedOrder(), cur = tiles(), same = want.length === cur.length;
    for (var i = 0; same && i < want.length; i++) if (want[i] !== cur[i]) same = false;
    if (same) { paintBar(); return; }
    orderLock = true;
    var slot = $('physdelSlot');                 // an empty placeholder some cards are built next to - keep it out of the way, first
    want.forEach(function (t) { g.appendChild(t); });
    if (slot && slot.parentNode === g) g.insertBefore(slot, g.firstChild);
    setTimeout(function () { orderLock = false; }, 0);
    paintBar();
  }
  function saveCurrent() { lsSet(LS_ORDER, JSON.stringify(tiles().map(function (t) { return t.id; }))); lsSet(LS_MODE, 'custom'); }

  function ensureBar() {
    if ($('pcTileBar')) return;
    var g = tileGrid(); if (!g) return;
    var bar = document.createElement('div');
    bar.id = 'pcTileBar';
    bar.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 12px;font-size:12px;color:var(--muted)';
    bar.innerHTML = '<span id="pcTileMode" style="font-family:var(--mono);font-size:11px"></span>' +
      '<button class="tbtn" id="pcTileAz" style="padding:4px 12px" data-tip="Put the KPI cards in A to Z order again.">A → Z</button>' +
      '<span style="opacity:.7">Drag a card to move it - your order is kept.</span>';
    g.parentNode.insertBefore(bar, g);
    $('pcTileAz').onclick = function () {
      lsSet(LS_MODE, 'az'); applyOrder();
      if (window.okBox) window.okBox('KPI cards', 'Sorted A to Z.'); else if (window.toast) window.toast('KPI cards sorted A to Z.');
    };
  }
  function paintBar() {
    var m = $('pcTileMode'); if (!m) return;
    m.textContent = tiles().length + ' KPI cards · ' + (lsGet(LS_MODE, 'az') === 'custom' ? 'your order' : 'A → Z');
  }

  function wireDrag(t) {
    if (t.__pcDrag) return; t.__pcDrag = true;
    t.setAttribute('draggable', 'true');
    t.addEventListener('dragstart', function (e) {
      dragging = t; t.style.opacity = '.45';
      try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', t.id); } catch (x) { /* old browser */ }
    });
    t.addEventListener('dragend', function () {
      t.style.opacity = ''; var was = dragging; dragging = null;
      tiles().forEach(function (x) { x.style.outline = ''; });
      if (was) { saveCurrent(); paintBar(); }
    });
    t.addEventListener('dragover', function (e) {
      if (!dragging || dragging === t) return;
      e.preventDefault();
      var r = t.getBoundingClientRect(), before = (e.clientX - r.left) < r.width / 2 || (e.clientY - r.top) < r.height / 3;
      t.parentNode.insertBefore(dragging, before ? t : t.nextSibling);
    });
    t.addEventListener('drop', function (e) { e.preventDefault(); });
  }

  function tick() {
    ensureBar();
    tiles().forEach(wireDrag);
    applyOrder();
  }
  var timer = null;
  function later() { clearTimeout(timer); timer = setTimeout(tick, 150); }

  function boot() {
    tick();
    var g = tileGrid();
    if (g && window.MutationObserver) new MutationObserver(later).observe(g, { childList: true });   // only cards coming and going, not their live text
    var n = 0, iv = setInterval(function () { tick(); if (++n > 40) clearInterval(iv); }, 1500);   // cards are added a little after load
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
