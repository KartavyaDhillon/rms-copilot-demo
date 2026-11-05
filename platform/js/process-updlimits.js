/* UPDATE LIMITS DATA - small card at the end of the Process Center (after EODSAR). Every Update Limits file (CTCL vs NMASS
   runs, manual uploads, old CTCL backups) is stored as Parquet; "Open" lets you type a client + date and see that client's
   row of every snapshot of the day at once, download only that client (Excel) or the full file.
   Backend: /ulimits/* (api.py) -> core/update_limits.py. Injected at serve time (see api.py /legacy). Uses the dc-* styles
   of process-daycards.js. */
(function () {
  'use strict';
  if (window.__ulInjected) return;
  window.__ulInjected = true;
  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var ST = { data: null, timer: null, date: '', to: '', client: '', times: {}, day: [], res: null, filter: '', hideZero: false, dateUser: false, upl: [], view: 'main', jobSig: '', sel: {}, zfmt: 'orig', zbusy: false };
  var ACC = '#0ea5e9';

  function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function $(id) { return document.getElementById(id); }
  function mb(n) { n = Number(n || 0); return n < 1048576 ? (n / 1024).toFixed(0) + ' KB' : n < 1073741824 ? (n / 1048576).toFixed(1) + ' MB' : (n / 1073741824).toFixed(2) + ' GB'; }
  function dmy(iso) { var p = String(iso || '').split('-'); return p.length === 3 ? p[2] + '-' + p[1] + '-' + p[0] : iso; }
  function isoToday() { var d = new Date(); return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
  function isoAdd(iso, days) { var d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + days); return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
  function get(path) { return fetch(API + path, { cache: 'no-store' }).then(function (r) { return r.json(); }); }
  function post(path, body) {
    return fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }
  function hideTip() { var t = $('globalTip'); if (t) { t.style.display = 'none'; t.classList.remove('show'); } }
  function modal(t, h) { hideTip(); if (typeof window.saShowModal === 'function') window.saShowModal(t, h); }
  function widen() { var m = $('saModal'), c = m && m.querySelector('.sa-modal-card'); if (c) c.style.width = 'min(1080px,97vw)'; }
  function isOpen() { var m = $('saModal'); return !!(m && m.classList.contains('show') && $('ulRoot')); }
  function fmt(v) {
    if (v == null || v === '') return '<span style="color:var(--dim)">-</span>';
    if (typeof v === 'number') return v.toLocaleString('en-IN', { maximumFractionDigits: 2 });
    return esc(v);
  }
  function zero(v) { return v == null || v === '' || v === 0 || v === '0'; }

  var css = document.createElement('style');
  css.textContent =
    '.ul-tbl{width:100%;border-collapse:collapse;font-size:12px}.ul-tbl th,.ul-tbl td{padding:5px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}' +
    '.ul-tbl th:first-child,.ul-tbl td:first-child{text-align:left;position:sticky;left:0;background:var(--panel,#0f1420);z-index:1;font-weight:600;color:var(--muted)}' +
    '.ul-tbl thead th{position:sticky;top:0;background:var(--panel,#0f1420);z-index:2;font-family:var(--mono);font-size:11px;color:var(--ink)}' +
    '.ul-tbl td.chg{color:var(--amber);font-weight:650}.ul-tbl tr.key td:first-child{color:var(--ink)}' +
    '.ul-wrap{max-height:52vh;overflow:auto;border:1px solid var(--line);border-radius:11px}' +
    '.ul-sec{border:1px solid var(--line);border-radius:13px;padding:13px 15px;margin:0 0 12px;background:var(--panel-2,rgba(255,255,255,.03))}' +
    '.ul-h{font-family:var(--display);font-size:14.5px;font-weight:750;margin:0 0 10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}' +
    '.ul-row{display:flex;gap:10px;align-items:end;flex-wrap:wrap}.ul-a{color:var(--blue);text-decoration:none;font-size:12px}.ul-a:hover{text-decoration:underline}';
  document.head.appendChild(css);

  // ───────────────────────── card ─────────────────────────
  var tries = 0;
  function buildTile() {
    if ($('task-ulimits')) return true;
    tries++;
    var after = $('task-dc-eodsar') || (tries > 15 ? ($('task-dc-sa') || $('task-files70new') || $('task-mcxcash')) : null);
    if (!after) return false;
    var tile = document.createElement('div');
    tile.className = 'tile'; tile.id = 'task-ulimits';
    tile.style.cssText = '--tile-accent:' + ACC + ';cursor:pointer';
    tile.onclick = function () { ulOpen(); };
    tile.innerHTML =
      '<div class="tile-icon">🗄️</div>' +
      '<div class="tile-name">UPDATE LIMITS DATA <span class="st" id="ulChip">…</span></div>' +
      '<div class="tile-desc">Every Update Limits file kept as Parquet - type a client and a date and see its limits of every snapshot at once; download that client or the full file.</div>' +
      '<div class="tile-desc" id="ulMini" style="font-family:var(--mono);font-size:10.5px;color:var(--dim)">Checking…</div>' +
      '<div class="tile-actions"><button class="tbtn" style="background:' + ACC + ';color:#fff" onclick="event.stopPropagation();ulOpen()" data-tip="Search a client, download files, upload a file, load old files.">Open</button></div>';
    after.parentNode.insertBefore(tile, after.nextSibling);
    return true;
  }
  function paintTile() {
    var d = ST.data, chip = $('ulChip'), mini = $('ulMini');
    if (!d || !chip) return;
    var j = d.job || {};
    chip.textContent = j.running ? 'storing' : d.today_n ? 'today ✓' : d.count ? 'ready' : 'empty';
    chip.style.color = j.running ? 'var(--blue)' : d.today_n ? 'var(--green)' : d.count ? 'var(--dim)' : 'var(--amber)';
    mini.textContent = j.running ? 'Storing ' + (j.done || 0) + ' / ' + (j.total || 0) + '…'
      : d.latest ? 'Latest ' + dmy(d.latest.date) + ' ' + String(d.latest.time).slice(0, 5) + ' · ' + d.count + ' snapshots · ' + d.days + ' days'
      : 'Nothing stored yet - Open > Load history';
  }

  // ───────────────────────── window ─────────────────────────
  function tb(label, fn, tip, extra) {
    return '<button class="sa-tool" style="width:100%;justify-content:center;text-align:center;white-space:nowrap' + (extra || '') + '" onclick="' + fn + '"' + (tip ? ' data-tip="' + esc(tip) + '"' : '') + '>' + label + '</button>';
  }
  function ulOpen(keep) {
    ST.view = 'main';
    if (keep !== true) {                      // opened from the card = fresh, like for the next person; 'back' keeps the screen
      ST.client = ''; ST.to = ''; ST.times = {}; ST.day = []; ST.res = null; ST.filter = ''; ST.hideZero = false;
      ST.dateUser = false; ST.upl = []; ST.sel = {}; ST.zfmt = 'orig'; ST.zbusy = false; ST.jobSig = '';
    }
    var d = ST.data || {};
    if (!ST.dateUser) ST.date = (d.latest && d.latest.date) || isoToday();      // the newest stored date until you pick one
    modal('🗄️ UPDATE LIMITS DATA',
      '<div id="ulRoot">' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 12px">' +
        tb('⟳ Store today now', 'ulStoreToday()', 'Stores every Update Limits file of today from the CTCL vs NMASS backup folder (the auto store does this every day by itself).') +
        tb('📚 Load history', 'ulHistory()', 'Stores the Update Limits files of old dates (for example the last 2-3 months) from the CTCL backup folders.') +
        tb('⚙️ Auto store', 'ulSettings()', 'When the daily auto store runs, on/off, and which CTCL backup folder is read.') +
        tb('⛔ Stop', 'ulStop()', 'Stops storing (the file being stored now finishes).', ';border-color:rgba(248,113,113,.45);color:var(--red)') +
      '</div>' +
      '<div id="ulMsg"></div><div id="ulSum" style="font-size:11.5px;color:var(--muted);margin:0 0 10px"></div><div id="ulJob"></div>' +
      '<div class="ul-sec"><div class="ul-h">🔍 Find a client</div>' +
        '<div class="ul-row">' +
          '<div style="flex:1;min-width:230px"><div class="dc-lbl">Client code(s) - one, or many separated by commas</div><input id="ulClient" class="dc-in" style="width:100%;text-transform:uppercase" placeholder="7GDW01, 4790, 6WBB2040" value="' + esc(ST.client) + '" onkeydown="if(event.key===\'Enter\')ulSearch()" data-tip="Up to 500 codes, separated by commas, spaces or new lines (you can paste a column from Excel)."></div>' +
          '<div><div class="dc-lbl">Date</div><input type="date" id="ulDate" class="dc-in" value="' + esc(ST.date) + '" onchange="ulSetDate(this.value)"></div>' +
          '<div><div class="dc-lbl">To (optional)</div><input type="date" id="ulTo" class="dc-in" value="' + esc(ST.to) + '" onchange="ulSetTo(this.value)"></div>' +
          '<button class="dc-btn" id="ulGo" onclick="ulSearch()">🔍 Search</button>' +
          '<button class="dc-btn ghost" id="ulXls" onclick="ulClientXlsx()" data-tip="Excel with only these clients\' rows - ALL columns of the file - for the chosen snapshots.">⬇ Client Excel</button>' +
          '<button class="dc-btn ghost" id="ulFull" onclick="ulFullFile()" data-tip="The whole Update Limits file of this date: the snapshot you ticked below, else the latest one of the day. The original .xlsx when it is still there, else a CSV (zip).">⬇ Full file</button>' +
        '</div><div id="ulTimes" style="display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 0"></div>' +
        '<div id="ulRes" style="margin-top:12px"></div></div>' +
      '<div class="ul-sec"><div class="ul-h">📄 Files of <span id="ulDayLbl">' + esc(dmy(ST.date)) + '</span></div><div id="ulDay" style="font-size:12px;color:var(--muted)">Loading…</div></div>' +
      '<div class="dc-drop" onclick="ulPick()" ondragover="event.preventDefault();this.classList.add(\'over\')" ondragleave="this.classList.remove(\'over\')" ondrop="ulDrop(event)">' +
        '<span class="ic">⬆️</span><div style="flex:1;min-width:240px"><div style="font-weight:700;font-size:13.5px">Upload Update Limits files</div>' +
        '<div style="font-size:11.5px;color:var(--muted);margin-top:2px;line-height:1.5">For the files after the last CTCL run (about 3 pm) or old files. The <b>date</b> comes from the name (Update Limits05112026.xlsx), else the Date above. ' +
        'The <b>time</b> from the box on the right, else the name (…_1530.xlsx), else the file\'s modified time when it is on that date.</div></div>' +
        '<div onclick="event.stopPropagation()"><div class="dc-lbl">Time (optional)</div><input id="ulUpTime" class="dc-in" style="width:90px" placeholder="15:30"></div></div>' +
      '<div id="ulUpRes"></div>' +
      '</div>');
    widen();
    paintSum(); paintJob(true); paintRes(); loadDay();
  }
  window.ulOpen = ulOpen;

  function say(txt, kind) {
    var el = $('ulMsg'); if (!el) return;
    el.innerHTML = txt ? '<div class="dc-msg" style="color:' + (kind === 'err' ? 'var(--red)' : 'var(--green)') + '">' + esc(txt) + '</div>' : '';
    clearTimeout(ST.msgT); if (txt) ST.msgT = setTimeout(function () { var e = $('ulMsg'); if (e) e.innerHTML = ''; }, 10000);
  }
  function paintSum() {
    var el = $('ulSum'), d = ST.data; if (!el || !d) return;
    var s = d.settings || {};
    el.innerHTML = '<b style="color:var(--ink)">' + (d.count || 0) + '</b> snapshots over <b style="color:var(--ink)">' + (d.days || 0) + '</b> days · ' + mb(d.bytes) + ' on disk' +
      (d.latest ? ' · latest ' + dmy(d.latest.date) + ' ' + esc(String(d.latest.time).slice(0, 5)) : '') +
      ' · auto store ' + (s.auto ? 'every day at <b style="color:var(--ink)">' + esc(s.time) + '</b>' : '<b style="color:var(--amber)">off</b>') +
      (s.last_auto ? ' (last ' + esc(dmy(s.last_auto)) + ')' : '');
  }
  function paintJob(force) {
    var el = $('ulJob'), d = ST.data; if (!el || !d) return;
    var j = d.job || {}, sig = JSON.stringify(j);
    if (!force && sig === ST.jobSig) return;
    ST.jobSig = sig;
    var recent = j.finished && (Date.now() - new Date(j.finished).getTime() < 60000);
    if (!j.running && !recent) { el.innerHTML = ''; return; }
    var pct = j.total ? Math.round(100 * j.done / j.total) : 0;
    var old = $('ulLog'), stick = !old || (old.scrollHeight - old.scrollTop - old.clientHeight < 30);
    el.innerHTML = '<div class="ul-sec" style="border-color:' + (j.running ? 'var(--amber)' : 'rgba(52,211,153,.4)') + '"><div style="display:flex;gap:10px;align-items:center;font-size:12.5px">' +
      (j.running ? '<span class="dc-spin"></span><b>Storing ' + j.done + ' / ' + j.total + '</b> <span style="color:var(--dim)">' + esc(j.current || '') + '</span>'
        : '<b style="color:var(--green)">✓ Done</b> <span style="color:var(--dim)">' + (j.stored || 0) + ' stored · ' + (j.skipped || 0) + ' already there · ' + ((j.failed || []).length) + ' failed</span>') +
      '</div><div class="dc-bar"><i style="width:' + pct + '%"></i></div>' +
      '<div class="dc-log" id="ulLog" style="margin:6px 0 0;max-height:150px">' + (j.lines || []).map(esc).join('\n') + '</div></div>';
    var nb = $('ulLog'); if (nb && stick) nb.scrollTop = nb.scrollHeight;
  }

  // ───────────────────────── files of the day + time chips ─────────────────────────
  function loadDay() {
    var lbl = $('ulDayLbl'); if (lbl) lbl.textContent = dmy(ST.date) + (ST.to && ST.to !== ST.date ? ' → ' + dmy(ST.to) : '');
    get('/ulimits/day?date=' + encodeURIComponent(ST.date) + (ST.to ? '&to=' + encodeURIComponent(ST.to) : '')).then(function (j) {
      ST.day = (j && j.snapshots) || [];
      paintDay(); paintTimes();
    }).catch(function () { var e = $('ulDay'); if (e) e.innerHTML = '<span style="color:var(--red)">Could not read the list.</span>'; });
  }
  function paintDay() {
    var el = $('ulDay'); if (!el) return;
    paintFullBtn();
    if (!ST.day.length) {
      var near = ((ST.data && ST.data.dates) || []).slice(0, 12);
      el.innerHTML = 'Nothing stored for this date. Use ⟳ Store today now, 📚 Load history or upload the file below.' +
        (near.length ? '<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;align-items:center"><span class="dc-lbl" style="margin:0 4px 0 0">Stored dates</span>' +
          near.map(function (x) { return '<button class="dc-chip2" onclick="ulSetDate(\'' + x.date + '\')">' + esc(dmy(x.date)) + ' <span style="opacity:.6">(' + x.n + ')</span></button>'; }).join('') + '</div>' : '');
      return;
    }
    var n = ST.day.length, ticked = zipIds().length;
    el.innerHTML = '<div class="ul-wrap" style="max-height:260px"><table class="ul-tbl"><thead><tr><th style="width:28px"><input type="checkbox" id="ulAll" ' + (ticked === n && n ? 'checked' : '') + ' onchange="ulSelAll(this.checked)" data-tip="Tick / untick every file of the day"></th><th>Date · time</th><th style="text-align:left">Source</th><th style="text-align:left">Time from</th><th>Clients</th><th>Size</th><th>Full file</th></tr></thead><tbody>' +
      ST.day.slice().reverse().map(function (e) {
        return '<tr><td style="text-align:left"><input type="checkbox" ' + (ST.sel[e.id] ? 'checked' : '') + ' onchange="ulSel(\'' + e.id + '\',this.checked)"></td>' +
          '<td style="font-family:var(--mono);color:var(--ink)">' + esc(dmy(e.date)) + ' ' + esc(e.time) + '</td><td style="text-align:left">' + esc(e.source) + '</td><td style="text-align:left;color:var(--dim)">' + esc(e.time_src) + '</td>' +
          '<td>' + Number(e.rows || 0).toLocaleString('en-IN') + '</td><td style="color:var(--dim)">' + mb(e.bytes) + '</td><td>' +
          (e.orig ? '<a class="ul-a" href="' + API + '/ulimits/full?id=' + encodeURIComponent(e.id) + '&fmt=orig" data-tip="The original Update Limits .xlsx" download>⬇ Original .xlsx</a> · ' : '') +
          '<a class="ul-a" href="' + API + '/ulimits/full?id=' + encodeURIComponent(e.id) + '&fmt=csv" data-tip="The whole file as CSV in a zip (opens in Excel), made from the stored copy - takes a few seconds." download>⬇ CSV</a></td></tr>';
      }).join('') + '</tbody></table></div>' +
      '<div id="ulZipBar" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px"></div>';
    paintZipBar();
  }
  function zipIds() { return ST.day.filter(function (e) { return ST.sel[e.id]; }).map(function (e) { return e.id; }); }
  function paintZipBar() {
    var el = $('ulZipBar'); if (!el) return;
    var ids = zipIds(), n = ST.day.length, use = ids.length || n;
    var fm = [['orig', 'Original .xlsx', 'The original files as they are (fast, the biggest).'], ['csv', 'CSV', 'Made from the stored copy, compressed (a few seconds per file).'], ['parquet', 'Parquet', 'The stored copy - the smallest; opens in Python / Power BI.']];
    el.innerHTML = '<span class="dc-lbl" style="margin:0">Zip of</span>' +
      fm.map(function (f) { return '<button class="dc-chip2' + (ST.zfmt === f[0] ? ' on' : '') + '" data-tip="' + esc(f[2]) + '" onclick="ulZFmt(\'' + f[0] + '\')">' + f[1] + '</button>'; }).join('') +
      '<button class="dc-btn" id="ulZipGo" onclick="ulZip()" ' + (ST.zbusy ? 'disabled' : '') + '>' + (ST.zbusy ? '<span class="dc-spin"></span>Making the zip…' : '⬇ Download zip (' + (ids.length ? ids.length + ' ticked' : 'all ' + n + ' of the day') + ')') + '</button>' +
      '<span style="font-size:11px;color:var(--dim)">' + (ids.length ? '' : 'Nothing ticked = the whole day. ') + 'Tick the boxes to pick some files.</span>';
  }
  window.ulSel = function (id, on) { if (on) ST.sel[id] = true; else delete ST.sel[id]; var a = $('ulAll'); if (a) a.checked = zipIds().length === ST.day.length; paintZipBar(); };
  window.ulSelAll = function (on) { ST.sel = {}; if (on) ST.day.forEach(function (e) { ST.sel[e.id] = true; }); paintDay(); };
  window.ulZFmt = function (f) { ST.zfmt = f; paintZipBar(); };
  window.ulZip = function () {
    var ids = zipIds(); if (!ids.length) ids = ST.day.map(function (e) { return e.id; });
    if (!ids.length || ST.zbusy) return;
    if (ids.length > 60) { say('At most 60 files in one zip - tick fewer.', 'err'); return; }
    ST.zbusy = true; paintZipBar();
    post('/ulimits/zip', { ids: ids, fmt: ST.zfmt }).then(function (j) {
      if (!j || !j.ok) throw new Error((j && (j.error || j.detail)) || 'Could not make the zip.');
      var a = document.createElement('a'); a.href = API + '/ulimits/zip/' + encodeURIComponent(j.id) + '?name=' + encodeURIComponent(j.name); a.download = j.name;
      document.body.appendChild(a); a.click(); setTimeout(function () { a.remove(); }, 2000);
      say('Zip of ' + ids.length + ' file' + (ids.length > 1 ? 's' : '') + ' (' + mb(j.size) + ') is downloading.');
    }).catch(function (e) { say(e.message || String(e), 'err'); })
      .then(function () { ST.zbusy = false; paintZipBar(); });
  };
  function paintTimes() {
    var el = $('ulTimes'); if (!el) return;
    if (!ST.day.length || (ST.to && ST.to !== ST.date)) { el.innerHTML = ''; return; }
    var sel = Object.keys(ST.times).filter(function (k) { return ST.times[k]; });
    el.innerHTML = '<span class="dc-lbl" style="margin:6px 4px 0 0">Snapshots</span><button class="dc-chip2' + (sel.length ? '' : ' on') + '" onclick="ulTime(null)">All (' + ST.day.length + ')</button>' +
      ST.day.map(function (e) { var t = String(e.time).slice(0, 5); return '<button class="dc-chip2' + (ST.times[t] ? ' on' : '') + '" onclick="ulTime(\'' + t + '\')" data-tip="' + esc(e.source + ' · time from ' + e.time_src) + '">' + t + '</button>'; }).join('');
  }
  window.ulTime = function (t) { if (t === null) ST.times = {}; else ST.times[t] = !ST.times[t]; paintTimes(); paintFullBtn(); if (ST.res) ulSearch(); };
  window.ulSetDate = function (v) { if (!v) return; ST.date = v; ST.dateUser = true; ST.times = {}; ST.sel = {}; var i = $('ulDate'); if (i && i.value !== v) i.value = v; loadDay(); if (ST.client) ulSearch(); };
  window.ulSetTo = function (v) { ST.to = v || ''; ST.times = {}; ST.sel = {}; loadDay(); if (ST.client) ulSearch(); };

  function fullPick() {
    if (!ST.day.length || (ST.to && ST.to !== ST.date)) return null;
    var sel = Object.keys(ST.times).filter(function (k) { return ST.times[k]; });
    if (sel.length === 1) return ST.day.filter(function (e) { return String(e.time).slice(0, 5) === sel[0]; })[0] || null;
    return ST.day[ST.day.length - 1];
  }
  function paintFullBtn() {
    var b = $('ulFull'); if (!b) return;
    var e = fullPick();
    b.innerHTML = e ? '⬇ Full file (' + esc(dmy(e.date).slice(0, 5)) + ' ' + esc(String(e.time).slice(0, 5)) + ')' : '⬇ Full file';
    b.style.opacity = e ? '1' : '.5';
  }
  window.ulFullFile = function () {
    if (ST.to && ST.to !== ST.date) { say('Full file is for one date - clear the To date.', 'err'); return; }
    var e = fullPick();
    if (!e) { say('Nothing stored for ' + dmy(ST.date) + ' - pick a stored date (see the list below).', 'err'); return; }
    var a = document.createElement('a');
    a.href = API + '/ulimits/full?id=' + encodeURIComponent(e.id) + '&fmt=' + (e.orig ? 'orig' : 'csv');
    a.download = ''; document.body.appendChild(a); a.click(); setTimeout(function () { a.remove(); }, 2000);
    say('Downloading the full file of ' + dmy(e.date) + ' ' + String(e.time).slice(0, 5) + (e.orig ? ' (original .xlsx)' : ' (CSV in a zip - takes a few seconds to make)') + '.');
  };

  // ───────────────────────── search ─────────────────────────
  function query() {
    var t = Object.keys(ST.times).filter(function (k) { return ST.times[k]; });
    return 'client=' + encodeURIComponent(ST.client) + '&date=' + encodeURIComponent(ST.date) + (ST.to ? '&to=' + encodeURIComponent(ST.to) : '') + (t.length ? '&times=' + encodeURIComponent(t.join(',')) : '');
  }
  window.ulSearch = function () {
    var c = $('ulClient'); ST.client = (c ? c.value : ST.client).trim().toUpperCase();
    if (!ST.client) { say('Type a client code.', 'err'); return; }
    var go = $('ulGo'); if (go) { go.disabled = true; go.innerHTML = '<span class="dc-spin"></span>Searching…'; }
    get('/ulimits/search?' + query()).then(function (j) { ST.res = j; paintRes(); })
      .catch(function () { ST.res = { ok: false, error: 'Server did not answer.' }; paintRes(); })
      .then(function () { var g = $('ulGo'); if (g) { g.disabled = false; g.innerHTML = '🔍 Search'; } });
  };
  function paintRes() {
    var el = $('ulRes'), r = ST.res; if (!el) return;
    if (!r) { el.innerHTML = '<div style="font-size:12px;color:var(--dim)">Type one client code (or many, separated by commas) and press Search (or Enter). One client: every snapshot of the day side by side. Many clients: one line per client per snapshot. All columns of the file are shown; values that changed from the snapshot before are in amber.</div>'; return; }
    if (r.ok === false || r.error) { el.innerHTML = '<div class="dc-msg" style="color:var(--red)">' + esc(r.error || r.detail || 'Search failed') + '</div>'; return; }
    if (!r.rows || !r.rows.length) { el.innerHTML = '<div class="dc-msg" style="color:var(--amber)">' + esc(r.note || 'Not found.') + '</div>'; return; }
    var rows = r.rows, multi = rows.some(function (x) { return x.date !== rows[0].date; });
    var f = (ST.filter || '').toLowerCase();
    var cols = r.columns.filter(function (c) {
      if (c === 'Entity') return false;
      if (f && c.toLowerCase().indexOf(f) < 0) return false;
      if (ST.hideZero && !f) return rows.some(function (x) { return !zero(x.data[c]); });
      return true;
    });
    var many = (r.clients || []).length > 1;
    var first = rows[rows.length - 1].data;
    var head = '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 8px;font-size:12.5px">' +
      (many ? '<b>' + (r.clients.length - (r.missing || []).length) + ' of ' + r.clients.length + ' clients found</b>'
        : '<b style="font-family:var(--mono);font-size:14px">' + esc(first.Entity) + '</b><span>' + esc(first.ClientName || '') + '</span><span class="dc-chip">' + esc(first.Profile || '') + '</span>') +
      '<span style="color:var(--dim)">' + rows.length + ' row' + (rows.length > 1 ? 's' : '') + ' from ' + r.snapshots + ' snapshot' + (r.snapshots > 1 ? 's' : '') + ' · ' + r.ms + ' ms</span>' +
      '<span style="margin-left:auto;display:flex;gap:10px;align-items:center"><input class="dc-in" style="width:170px" placeholder="Find a column…" value="' + esc(ST.filter) + '" oninput="ulFilter(this.value)">' +
      '<label style="font-size:11.5px;display:flex;gap:5px;align-items:center"><input type="checkbox" ' + (ST.hideZero ? 'checked' : '') + ' onchange="ulZero(this.checked)"> hide columns that are 0 in every row</label></span></div>' +
      ((r.missing || []).length ? '<div class="dc-msg" style="color:var(--amber);margin:0 0 8px">Not in these snapshots: ' + r.missing.map(esc).join(', ') + '</div>' : '');
    var body;
    if (!many) {                                           // one client: every snapshot side by side, one line per column
      body = '<div class="ul-wrap"><table class="ul-tbl"><thead><tr><th>Column</th>' + rows.map(function (x) {
        return '<th data-tip="' + esc(x.source) + '">' + (multi ? esc(dmy(x.date).slice(0, 5)) + ' ' : '') + esc(String(x.time).slice(0, 5)) + '</th>';
      }).join('') + '</tr></thead><tbody>' +
      cols.map(function (c) {
        return '<tr><td>' + esc(c) + '</td>' + rows.map(function (x, i) {
          var v = x.data[c], prev = i ? rows[i - 1].data[c] : v;
          return '<td class="' + (i && String(v) !== String(prev) ? 'chg' : '') + '">' + fmt(v) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody></table></div>';
    } else {                                               // many clients: one line per client per snapshot, every column
      var sorted = rows.slice().sort(function (a, b) { return String(a.data.Entity).localeCompare(String(b.data.Entity)) || (a.date + a.time).localeCompare(b.date + b.time); });
      var prevOf = {};
      body = '<div class="ul-wrap"><table class="ul-tbl"><thead><tr><th>Client</th><th>' + (multi ? 'Date · time' : 'Time') + '</th>' + cols.map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>' +
        sorted.map(function (x) {
          var p = prevOf[x.data.Entity]; prevOf[x.data.Entity] = x;
          return '<tr><td style="font-family:var(--mono);color:var(--ink)">' + esc(x.data.Entity) + '</td><td style="font-family:var(--mono)" data-tip="' + esc(x.source) + '">' + (multi ? esc(dmy(x.date).slice(0, 5)) + ' ' : '') + esc(String(x.time).slice(0, 5)) + '</td>' +
            cols.map(function (c) { var v = x.data[c]; return '<td class="' + (p && String(v) !== String(p.data[c]) ? 'chg' : '') + '">' + fmt(v) + '</td>'; }).join('') + '</tr>';
        }).join('') + '</tbody></table></div>';
    }
    el.innerHTML = head + body + '<div style="font-size:11px;color:var(--dim);margin-top:6px">' + cols.length + ' of ' + (r.columns.length - 1) + ' columns shown' + (cols.length < r.columns.length - 1 ? ' (filter / hide zeros is on)' : ' - all columns of the file') + '. ⬇ Client Excel always has every column.</div>';
  }
  window.ulFilter = function (v) { ST.filter = v; paintRes(); var i = document.querySelector('#ulRes input.dc-in'); if (i) { i.focus(); i.setSelectionRange(v.length, v.length); } };
  window.ulZero = function (on) { ST.hideZero = on; paintRes(); };
  window.ulClientXlsx = function () {
    var c = $('ulClient'); ST.client = (c ? c.value : ST.client).trim().toUpperCase();
    if (!ST.client) { say('Type a client code.', 'err'); return; }
    var b = $('ulXls'); if (b) { b.disabled = true; b.innerHTML = '<span class="dc-spin"></span>Making…'; }
    fetch(API + '/ulimits/client.xlsx?' + query()).then(function (r) {
      if (!r.ok) return r.json().then(function (j) { throw new Error(j.detail || ('Server answered ' + r.status)); });
      return r.blob();
    }).then(function (blob) {
      var a = document.createElement('a'); a.href = URL.createObjectURL(blob);
      a.download = 'UPDATE_LIMITS_' + ST.client + '_' + dmy(ST.date) + (ST.to && ST.to !== ST.date ? '_to_' + dmy(ST.to) : '') + '.xlsx';
      document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
    }).catch(function (e) { say(e.message || String(e), 'err'); })
      .then(function () { var x = $('ulXls'); if (x) { x.disabled = false; x.innerHTML = '⬇ Client Excel'; } });
  };

  // ───────────────────────── upload ─────────────────────────
  function sendFiles(files) {
    var list = Array.prototype.slice.call(files || []); if (!list.length) return;
    var t = ($('ulUpTime') || {}).value || '';
    ST.upl = []; paintUp('Sending ' + list.length + ' file' + (list.length > 1 ? 's' : '') + '…');
    var chain = Promise.resolve();
    list.forEach(function (f) {
      chain = chain.then(function () {
        var q = '?name=' + encodeURIComponent(f.name) + '&date=' + encodeURIComponent(ST.date) + '&time=' + encodeURIComponent(t) + '&lastmod=' + (f.lastModified || '');
        return fetch(API + '/ulimits/upload' + q, { method: 'POST', body: f })
          .then(function (r) { return r.json(); })
          .then(function (j) { ST.upl.push(j && j.ok ? j : { ok: false, name: f.name, error: (j && (j.error || j.detail)) || 'not saved' }); paintUp(); })
          .catch(function (e) { ST.upl.push({ ok: false, name: f.name, error: String(e) }); paintUp(); });
      });
    });
    chain.then(function () { refresh(400); setTimeout(loadDay, 2500); });
  }
  function paintUp(busy) {
    var el = $('ulUpRes'); if (!el) return;
    el.innerHTML = (busy || ST.upl.length) ? '<div class="dc-res">' + (busy ? '<div><span class="dc-spin"></span>' + esc(busy) + '</div>' : '') + ST.upl.map(function (r) {
      return r.ok ? '<div class="g">✓ ' + esc(r.name) + ' <span style="color:var(--dim)">→ ' + esc(r.date) + ' ' + esc(r.time) + ' (time from ' + esc(r.time_src) + ') - being stored, see the progress above</span></div>'
        : '<div class="e">✗ ' + esc(r.name) + ' - ' + esc(r.error) + '</div>';
    }).join('') + '</div>' : '';
  }
  window.ulPick = function () {
    var i = document.createElement('input'); i.type = 'file'; i.multiple = true; i.accept = '.xlsx,.csv'; i.style.display = 'none';
    i.onchange = function () { sendFiles(i.files); i.remove(); };
    document.body.appendChild(i); i.click();
  };
  window.ulDrop = function (ev) { ev.preventDefault(); ev.currentTarget.classList.remove('over'); sendFiles(ev.dataTransfer && ev.dataTransfer.files); };

  // ───────────────────────── actions / sub views ─────────────────────────
  window.ulStoreToday = function () {
    post('/ulimits/store-today').then(function (j) {
      say(j.queued ? j.queued + ' file' + (j.queued > 1 ? 's' : '') + ' of today being stored (' + (j.found - j.queued) + ' already there).' : j.found ? 'All ' + j.found + ' Update Limits files of today are already stored.' : 'No Update Limits file of today in the CTCL backup folder yet.', j.found ? '' : 'err');
      refresh(300);
    });
  };
  window.ulStop = function () { post('/ulimits/stop').then(function (j) { say(j.stopped ? 'Stopping…' : 'Nothing is being stored.'); refresh(500); }); };
  function back() { return '<button class="dc-link" style="margin:0 0 12px" onclick="ulOpen(true)">← back</button>'; }
  window.ulHistory = function () {
    var s = (ST.data && ST.data.settings) || {};
    modal('📚 UPDATE LIMITS DATA - load history', back() +
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin:0 0 12px">Stores every Update Limits file of these dates from the CTCL backup folders <span style="font-family:var(--mono)">&lt;folder&gt;\\&lt;dd-mm-yyyy&gt;\\&lt;NN&gt;\\Update Limits*.xlsx</span>, one file after another in the background (about 15-25 s per file; a day has about 5). ' +
      'Files already stored are skipped, so it is safe to run again. The console stays usable; ⛔ Stop ends it.</div>' +
      '<div class="ul-row" style="margin-bottom:10px"><div><div class="dc-lbl">From</div><input type="date" id="ulHf" class="dc-in" value="' + isoAdd(isoToday(), -90) + '"></div>' +
      '<div><div class="dc-lbl">To</div><input type="date" id="ulHt" class="dc-in" value="' + isoToday() + '"></div>' +
      [30, 60, 90].map(function (n) { return '<button class="dc-chip2" onclick="document.getElementById(\'ulHf\').value=\'' + isoAdd(isoToday(), -n) + '\'">last ' + n + ' days</button>'; }).join('') + '</div>' +
      '<div class="dc-lbl">Folder on the server (empty = the CTCL backup folder)</div><input id="ulHr" class="dc-in" style="width:100%;margin-bottom:12px" placeholder="' + esc(s.ctcl_root_used || '') + '">' +
      '<button class="sa-save" style="width:100%" onclick="ulHistoryGo()">▶ Start loading</button><div class="sa-msg" id="ulHMsg"></div>');
    widen();
  };
  window.ulHistoryGo = function () {
    var m = $('ulHMsg');
    post('/ulimits/load', { from: $('ulHf').value, to: $('ulHt').value, root: $('ulHr').value }).then(function (j) {
      if (!j.ok) { if (m) m.innerHTML = '<span style="color:var(--red)">' + esc(j.error || j.detail || 'Could not start') + '</span>'; return; }
      ulOpen(true);
      say(j.queued ? j.queued + ' file' + (j.queued > 1 ? 's' : '') + ' to store (' + (j.found - j.queued) + ' already there) - progress below.' : j.found ? 'All ' + j.found + ' files of these dates are already stored.' : (j.note || 'No files found.'), j.found ? '' : 'err');
      refresh(300);
    });
  };
  window.ulSettings = function () {
    var s = (ST.data && ST.data.settings) || {};
    modal('⚙️ UPDATE LIMITS DATA - auto store', back() +
      '<div style="font-size:12.5px;color:var(--muted);line-height:1.55;margin:0 0 12px">Every day at this time the Update Limits files of today (and of the 5 days before, if one was missed) are read from the CTCL vs NMASS backup folder and stored. Manual uploads older than 45 days are removed then (their stored copy stays).</div>' +
      '<label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:10px"><input type="checkbox" id="ulSa" ' + (s.auto ? 'checked' : '') + '> Auto store is on</label>' +
      '<div class="dc-lbl">Time (HH:MM)</div><input id="ulSt" class="dc-in" style="width:100px;margin-bottom:10px" value="' + esc(s.time || '18:00') + '">' +
      '<div class="dc-lbl">CTCL backup folder (empty = &lt;CTCL folder&gt;\\BACKUPFILES AUTOMATED)</div><input id="ulSr" class="dc-in" style="width:100%;margin-bottom:6px" value="' + esc(s.ctcl_root || '') + '" placeholder="' + esc(s.ctcl_root_used || '') + '">' +
      '<div style="font-size:11px;color:var(--dim);margin-bottom:12px">Now reading: <span style="font-family:var(--mono)">' + esc(s.ctcl_root_used || '') + '</span></div>' +
      '<button class="sa-save" style="width:100%" onclick="ulSettingsSave()">Save</button><div class="sa-msg" id="ulSMsg"></div>');
    widen();
  };
  window.ulSettingsSave = function () {
    var m = $('ulSMsg');
    post('/ulimits/settings', { auto: $('ulSa').checked, time: $('ulSt').value, ctcl_root: $('ulSr').value }).then(function (j) {
      if (!j.ok) { if (m) m.innerHTML = '<span style="color:var(--red)">' + esc(j.error || j.detail || 'Not saved') + '</span>'; return; }
      refresh(0); setTimeout(function () { ulOpen(true); say('Saved.'); }, 300);
    });
  };

  // ───────────────────────── polling ─────────────────────────
  function load() {
    var next = 20000;
    return get('/ulimits/status').then(function (d) {
      if (d && d.ok) {
        var wasRunning = ST.data && ST.data.job && ST.data.job.running;
        ST.data = d; paintTile();
        if (d.job && d.job.running) next = isOpen() ? 2500 : 8000;
        if (isOpen()) { paintSum(); paintJob(); if (wasRunning && !d.job.running) loadDay(); }
      }
    }).catch(function () { next = 30000; }).then(function () {
      clearTimeout(ST.timer);
      ST.timer = setTimeout(function () { if (document.hidden && !isOpen()) { ST.timer = setTimeout(load, next); } else load(); }, next);
    });
  }
  function refresh(ms) { clearTimeout(ST.timer); ST.timer = setTimeout(load, ms || 0); }

  function start() { if (buildTile()) load(); else setTimeout(start, 800); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
