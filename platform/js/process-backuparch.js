/* BACKUP ARCHIVE - small tile of the Process Center. Every process keeps one folder per day in its BACKUPFILES AUTOMATED folder;
   when a month is over the day folders are filed into "OCT 2026" / dd-mm-yyyy. Window: status, what would move, Archive now, Settings.
   Backend: /backup-archive/* (api.py, core/backup_archive.py). Injected at serve time (api.py /legacy). */
(function () {
  'use strict';
  if (window.__baInjected) return;
  window.__baInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var TITLE = 'BACKUP ARCHIVE';
  var S = { data: null, timer: null, pw: '' };

  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function $(id) { return document.getElementById(id); }
  function get(p) { return fetch(API + p, { cache: 'no-store' }).then(function (r) { return r.json(); }); }
  function post(p, b) { return fetch(API + p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) }).then(function (r) { return r.json(); }); }
  function ask(what, cb) { if (typeof window.saAskPassword === 'function') window.saAskPassword(what, cb); }
  function modal(t, h) { var tip = $('globalTip'); if (tip) tip.style.display = 'none'; if (typeof window.saShowModal === 'function') window.saShowModal(t, h); }

  function toast(text, kind) {
    var old = $('baToast'); if (old) old.remove();
    var el = document.createElement('div'); el.id = 'baToast';
    var col = kind === 'err' ? 'var(--red,#f87171)' : kind === 'warn' ? 'var(--amber,#fbbf24)' : 'var(--green,#34d399)';
    el.style.cssText = 'position:fixed;top:22px;left:50%;transform:translateX(-50%);z-index:100000;max-width:min(560px,92vw);padding:12px 18px;border-radius:12px;background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid ' + col + ';box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:13px;line-height:1.45;display:flex;gap:10px;align-items:flex-start';
    el.innerHTML = '<span style="color:' + col + ';font-weight:800">' + (kind === 'err' ? '✕' : kind === 'warn' ? '!' : '✓') + '</span><span>' + esc(text) + '</span>';
    document.body.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, kind === 'err' ? 7000 : 3800);
  }
  function confirmBox(title, text, okLabel, cb) {
    var old = $('baConfirm'); if (old) old.remove();
    var bg = document.createElement('div'); bg.id = 'baConfirm';
    bg.style.cssText = 'position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    bg.innerHTML = '<div style="background:var(--panel,#141821);color:var(--ink,#e8ecf3);border:1px solid var(--line-2,#2a3342);border-radius:14px;padding:20px 22px;width:min(440px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.6)">' +
      '<div style="font-size:15px;font-weight:700;margin-bottom:8px">' + esc(title) + '</div><div style="font-size:13px;color:var(--muted,#9aa4b5);line-height:1.5;margin-bottom:16px">' + esc(text) + '</div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end"><button class="sa-tool" id="baNo" style="min-width:90px">Cancel</button><button class="sa-tool" id="baYes" style="min-width:90px;background:var(--blue,#60a5fa);color:#08131f;border-color:transparent">' + esc(okLabel || 'OK') + '</button></div></div>';
    document.body.appendChild(bg);
    $('baNo').onclick = function () { bg.remove(); };
    $('baYes').onclick = function () { bg.remove(); cb(); };
    bg.onclick = function (e) { if (e.target === bg) bg.remove(); };
  }

  // ---- tile (next to the other small tiles, before MTF CONCENTRATION) ----
  function grid() {
    var t = $('task-mtfconc') || $('task-ms15card') || $('task-mcxcomm') || $('task-liveallocation');
    return t && t.parentNode ? t.parentNode : null;
  }
  function build() {
    if ($('task-backuparch')) return true;
    var g = grid(); if (!g) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-backuparch';
    tile.style.cssText = '--tile-accent:#34d399;cursor:pointer';
    tile.onclick = function () { baOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">🗄️</div>' +
      '<div class="tile-name">BACKUP ARCHIVE <span class="st" id="baChip">idle</span></div>' +
      '<div class="tile-desc">Files every finished month of every process backup folder into one month folder (OCT 2026 → day folders), by itself on the first weekday of the month.</div>' +
      '<div class="tile-desc" id="baMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:#34d399;color:#06251a" onclick="event.stopPropagation();baOpen()" data-tip="Open BACKUP ARCHIVE: what is waiting to be filed, Archive now, settings.">Open</button></div>';
    g.appendChild(tile);
    return true;
  }
  function modalOpen() { var t = $('saModalTitle'), m = $('saModal'); return !!(t && m && m.classList.contains('show') && t.textContent === TITLE && $('baDyn')); }

  function kpi(k, v, f) { return '<div class="cq-kpi" style="border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--panel-2,rgba(255,255,255,.03));min-width:0"><div style="font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim)">' + k + '</div><div style="font-size:22px;font-weight:750;margin-top:3px">' + v + '</div><div style="font-size:11px;color:var(--muted);margin-top:2px">' + esc(f) + '</div></div>'; }
  function paintDyn() {
    var d = S.data, el = $('baDyn'); if (!el || !d || d.error) { if (el && d && d.error) el.innerHTML = '<div style="color:var(--red)">' + esc(d.error) + '</div>'; return; }
    var lr = d.last_result, h = '';
    h += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px">' +
      kpi('Waiting to be filed', d.pending, d.pending ? (d.pending_months || []).length + ' month folder(s) to fill' : 'everything is in order') +
      kpi('Backup folders found', (d.roots || []).length, 'under ' + d.data_root) +
      kpi('Daily pass', d.enabled ? 'ON' : 'OFF', 'weekdays at ' + d.run_time + ' · keep last ' + d.grace_days + ' days in place') + '</div>';
    if (lr) h += '<div style="font-size:12.5px;padding:9px 12px;border:1px solid ' + (lr.failed ? 'rgba(251,191,36,.5)' : 'rgba(52,211,153,.4)') + ';border-radius:10px;margin-bottom:12px"><b>Last pass</b> ' + esc(String(lr.at).replace('T', ' ')) + (lr.manual ? ' (button)' : ' (automatic)') + ' - moved <b>' + lr.moved + '</b> day folder(s)' + (lr.merged ? ' (' + lr.merged + ' merged into an existing one)' : '') + (lr.failed ? ', <span style="color:var(--amber)">' + lr.failed + ' skipped</span>' : '') + (lr.months && lr.months.length ? ' · ' + esc(lr.months.join(', ')) : '') +
      ((lr.errors || []).length ? '<div style="font-family:var(--mono);font-size:10.5px;color:var(--amber);margin-top:6px;white-space:pre-wrap">' + lr.errors.map(esc).join('\n') + '</div>' : '') + '</div>';
    if ((d.pending_months || []).length) h += '<div style="font-size:11px;letter-spacing:.5px;text-transform:uppercase;color:var(--dim);margin-bottom:5px">Waiting</div>' + d.pending_months.map(function (m) {
      return '<div style="display:flex;gap:8px;font-size:12px;padding:3px 0;border-bottom:1px solid var(--line)"><b style="width:90px">' + esc(m.month) + '</b><span style="flex:1;font-family:var(--mono);font-size:11px;color:var(--muted);overflow-wrap:anywhere">' + esc(m.root) + '</span><span>' + m.days + ' day(s)</span></div>';
    }).join('');
    h += '<div style="font-size:11px;color:var(--dim);line-height:1.6;margin-top:12px">Layout after filing: <span style="font-family:var(--mono)">BACKUPFILES AUTOMATED / OCT 2026 / 31-10-2026 / …</span>. Only finished months move, nothing is deleted or renamed, a day whose files are open is skipped and tried again next pass. The running month is never touched.</div>';
    el.innerHTML = h;
  }
  function paintTile(d) {
    var chip = $('baChip'), mini = $('baMini'); if (!chip || !d || d.error) return;
    chip.textContent = d.running ? 'running' : !d.enabled ? 'auto off' : d.pending ? 'waiting' : 'in order';
    chip.style.color = d.running ? 'var(--blue)' : !d.enabled ? 'var(--amber)' : d.pending ? 'var(--amber)' : 'var(--green)';
    mini.textContent = d.running ? 'Filing…' : d.pending + ' day folder(s) waiting · ' + (d.last_run ? 'last pass ' + String(d.last_run).slice(0, 16).replace('T', ' ') : 'not run yet');
  }
  function load() {
    return get('/backup-archive/status').then(function (d) { S.data = d; paintTile(d); if (modalOpen()) paintDyn(); })
      .catch(function () { }).then(function () { clearTimeout(S.timer); S.timer = setTimeout(function () { if (document.hidden) load(); else load(); }, (S.data && S.data.running) ? 2000 : 30000); });
  }

  function tb(label, fn, tip, extra) { return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '" data-tip="' + esc(tip) + '">' + label + '</button>'; }
  window.baOpen = function () {
    window._saReturn = null;
    modal(TITLE,
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 12px">' +
      tb('🗄️ Archive now', 'baRun()', 'Files every finished month now (same rules as the daily pass).', ';background:var(--blue);color:var(--blue-d);border-color:transparent') +
      tb('🔍 Preview', 'baPreview()', 'Shows exactly which day folders would move, without moving anything.') +
      tb('⚙ Settings', 'baSettings()', 'Daily pass on/off, time, how many recent days stay in place.') + '</div>' +
      '<div id="baMsg"></div><div id="baDyn"><div style="color:var(--muted);font-size:12px">Checking…</div></div>');
    paintDyn(); load();
  };
  function msg(t, kind) { toast(t, kind); }
  window.baRun = function () {
    var n = S.data ? S.data.pending : 0;
    confirmBox('Archive now?', n ? n + ' day folder(s) of finished months will be moved into their month folders (e.g. OCT 2026). Nothing is deleted.' : 'Nothing is waiting - every finished month is already filed.', n ? 'Archive' : 'OK', function () {
      if (!n) return;
      toast('Filing started…', 'warn');
      post('/backup-archive/run').then(function (j) {
        if (j && j.ok) { var r = j.result; msg('Done - moved ' + r.moved + ' day folder(s)' + (r.failed ? ', ' + r.failed + ' skipped (see the list)' : '') + '.', r.failed ? 'warn' : 'ok'); }
        else msg((j && (j.error || j.detail)) || 'Could not run.', 'err');
        load();
      }).catch(function (e) { msg('Failed: ' + (e.message || e), 'err'); });
    });
  };
  window.baPreview = function () {
    get('/backup-archive/preview').then(function (p) {
      var rows = (p.items || []).map(function (it) { return '<div style="display:flex;gap:8px;font-size:11.5px;padding:2px 0;border-bottom:1px solid var(--line)"><span style="width:88px">' + esc(it.folder) + '</span><span style="width:78px;color:var(--teal)">→ ' + esc(it.month) + '</span><span style="flex:1;font-family:var(--mono);font-size:10.5px;color:var(--dim);overflow-wrap:anywhere">' + esc(it.root) + '</span></div>'; }).join('');
      window._saReturn = window.baOpen;
      modal('🔍 ' + TITLE + ' - preview', '<div style="font-size:12.5px;margin-bottom:10px"><b>' + p.to_move + '</b> day folder(s) would move' + (p.to_move > 300 ? ' (first 300 shown)' : '') + '. Searched ' + (p.roots || []).length + ' backup folder(s).</div>' + (rows || '<div style="color:var(--dim)">Nothing to move.</div>'));
    });
  };
  window.baSettings = function () {
    ask('Backup archive settings', function (pw) {
      S.pw = pw; var d = S.data || {};
      window._saReturn = window.baOpen;
      modal('⚙ ' + TITLE + ' - settings',
        '<label style="display:flex;gap:8px;align-items:center;font-size:12.5px;margin-bottom:12px"><input type="checkbox" id="baEn" ' + (d.enabled ? 'checked' : '') + '> Daily pass on (weekdays)</label>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px"><div><div style="font-size:10.5px;text-transform:uppercase;color:var(--dim);margin-bottom:5px">Run at</div><input type="time" id="baTime" class="cq-in" style="width:100%;background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px" value="' + esc(d.run_time || '06:30') + '"></div>' +
        '<div><div style="font-size:10.5px;text-transform:uppercase;color:var(--dim);margin-bottom:5px">Keep the last N days in place</div><input type="number" min="0" max="31" id="baGrace" style="width:100%;background:transparent;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);padding:7px 9px" value="' + esc(d.grace_days == null ? 3 : d.grace_days) + '"></div></div>' +
        '<div style="font-size:11.5px;color:var(--dim);line-height:1.55;margin-bottom:12px">Keeping the last few days in place means a job that looks at "yesterday" on the 1st still finds it; the remaining days move on the next daily pass.</div>' +
        '<button class="sa-save" id="baSave" data-no-busy="1" style="width:100%" onclick="baSave()">Save settings</button>');
    });
  };
  window.baSave = function () {
    var b = $('baSave'); if (b) { b.disabled = true; b.textContent = 'Saving…'; }
    post('/backup-archive/settings', { password: S.pw, enabled: $('baEn').checked, run_time: $('baTime').value, grace_days: parseInt($('baGrace').value, 10) }).then(function (j) {
      if (b) { b.disabled = false; b.textContent = j && j.ok ? '✓ Saved' : 'Save settings'; }
      if (j && j.ok) { S.data = Object.assign(S.data || {}, j.status); toast('Settings saved.'); load(); } else toast((j && j.error) || 'Could not save.', 'err');
    });
  };

  function start() {
    if (build()) { load(); var n = 0, iv = setInterval(function () { var el = $('task-backuparch'); if (el && el.parentNode && el.parentNode.lastElementChild !== el) el.parentNode.appendChild(el); if (++n > 26) clearInterval(iv); }, 1500); } else setTimeout(start, 700);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
