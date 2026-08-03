/*
  process-probable-box.js — builds the ENTIRE "PROBABLE" pipeline
  box inside the embedded Process Center via DOM injection at serve time.
  dashboard.html on disk is NEVER edited (standing rule) — this is the ONLY
  mechanism by which the new pipe gets a UI at all, since the file it would
  otherwise live in can't be touched.

  Reuses dashboard.html's OWN generic, already-pipe-parameterized globals
  wherever they're safe to call directly with pipe="probable":
    pxStatus(pipe), pxRenderRuns(pipe,s)   — run-tile rendering + polling
    pxConfig(pipe), pxCredentials(pipe)    — Email To/CC + Credentials modals
    pxUpload(pipe), pxBackup(pipe)         — Upload files + Backup files modals
    pxLogs(pipe)                           — Logs modal
    saShowModal(title, html)               — generic modal shell (used for the
                                              market-time picker + captcha)
  and the EXISTING RSA token poller (already polls /sa/token/pending on an
  interval, unconditionally) — probable_tcs_bancs.py shares that exact same
  .token_state file/mechanism (MCX_DEPOSIT_CALLER="probable"), so the token
  popup works with ZERO new frontend code for that part.

  Does NOT reuse pxRun()/pxStop() directly for Run/Stop, because:
    - pxRun('probable') would fall into its generic path (correct), but there
      is no hook there to first collect the operator's market-time choice —
      so window.pxRun is monkey-patched (not edited on disk) to intercept
      pipe==="probable" and show the market-time modal first.
    - pxStop()'s generic fallback branch builds the URL '/px/stop' with NO
      ?pipe= query param at all (a latent bug for any pipe beyond
      ctcl/cli/ms15) — so this box's own Stop button calls a local
      probableStop() instead, which posts /px/stop?pipe=probable correctly.
*/
(function () {
  if (window.__probableBoxInjected) return;
  window.__probableBoxInjected = true;

  var API = (typeof window.API !== 'undefined' && window.API) ? window.API : '';
  var PIPE = 'probable';
  var _es = null;          // active EventSource for whichever action is running
  var _busy = false;       // local guard for THIS box's own download buttons

  // ── wait for the real DOM the rest of dashboard.html builds ──────────────
  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }

  ready(init);

  function init() {
    var host = document.getElementById('view-dashboard');
    if (!host) { setTimeout(init, 200); return; } // page still assembling
    buildBox(host);
    wireButtons();
    patchGlobals();
    injectAdvanceRunButtons();
    injectMs15WaButton();
    removeMs15TestButton();
    purgeStaleMs15TestLog();
    injectMs15SafeTestButton();
    // Keep whatever is running (or just finished) at the top of the page.
    // Polled rather than event-driven so it also reacts to runs started on
    // another screen; the DOM is only touched when the order actually changes.
    _orderTick();
    setInterval(_orderTick, 3000);
    // Identity badge removed — the signed session cookie + platform avatar
    // handle identity now. The old _identityBadge / _identityPrompt / rms_user
    // cookie mechanism is no longer needed.
    // Initial status + polling — dashboard.html only auto-polls ctcl/cli/ms15
    // on load (hardcoded), so this pipe needs its own kick-off, same idea.
    if (typeof window.pxStatus === 'function') {
      window.pxStatus(PIPE);
      setInterval(function () { if (!window._pxRunning) window.pxStatus(PIPE); }, 5000);
    }
    probableLoadReady();
    setInterval(probableLoadReady, 20000);
    // Safe test run — replaces the old PROBABLE_TEST_MODE banner. Its own
    // 4s poller is started inside the injector (matching MS15).
    injectProbableSafeTestButton();
  }

  // ── "⏭ Mark run done" — for when a run genuinely happened OUTSIDE this
  // ══════════════════════════════════════════════════════════════════════════
  // OVERRIDE TRACKING — remember which runs were manually marked done or
  // backfilled, so tiles and View modals can tell them apart from real runs.
  //
  // Stored in localStorage keyed by date + pipe + run, e.g.:
  //   rms_override_2026-07-31_ms15_1 → { type:"mark_done", at:"09:41" }
  //   rms_override_2026-07-31_ctcl_2 → { type:"backfill", at:"10:02", files:3 }
  //
  // Both mark_done and backfill can coexist for the same run (mark done first,
  // then backfill). In that case both keys exist and the View modal mentions
  // both. Entries are ephemeral — they only matter for today's display and
  // harmlessly expire when tomorrow's tiles render different run numbers.
  // ══════════════════════════════════════════════════════════════════════════
  var _OVERRIDE_PREFIX = 'rms_override_';

  function _overrideKey(pipe, runNum) {
    var d = new Date();
    var ds = d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
    return _OVERRIDE_PREFIX + ds + '_' + pipe + '_' + runNum;
  }

  function _overrideSave(pipe, runNum, info) {
    try { localStorage.setItem(_overrideKey(pipe, runNum), JSON.stringify(info)); } catch (e) {}
  }

  function _overrideGet(pipe, runNum) {
    try {
      var raw = localStorage.getItem(_overrideKey(pipe, runNum));
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  // Check if a given run was manually overridden (mark_done and/or backfill).
  // Returns { mark_done: {...}|null, backfill: {...}|null, isOverride: bool }.
  function _overrideInfo(pipe, runNum) {
    var md = null, bf = null;
    try {
      var d = new Date();
      var ds = d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
      var base = _OVERRIDE_PREFIX + ds + '_' + pipe + '_' + runNum;
      var raw1 = localStorage.getItem(base);
      if (raw1) {
        var parsed = JSON.parse(raw1);
        if (parsed.type === 'mark_done') md = parsed;
        else if (parsed.type === 'backfill') bf = parsed;
      }
      // Also check with _backfill suffix (when both exist)
      var raw2 = localStorage.getItem(base + '_backfill');
      if (raw2) bf = JSON.parse(raw2);
    } catch (e) {}
    return { mark_done: md, backfill: bf, isOverride: !!(md || bf) };
  }

  function _overrideSaveBackfill(pipe, runNum, info) {
    try {
      var d = new Date();
      var ds = d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
      var base = _OVERRIDE_PREFIX + ds + '_' + pipe + '_' + runNum;
      // If a mark_done already exists on the base key, store backfill on _backfill suffix
      var existing = localStorage.getItem(base);
      if (existing) {
        localStorage.setItem(base + '_backfill', JSON.stringify(info));
      } else {
        localStorage.setItem(base, JSON.stringify(info));
      }
    } catch (e) {}
  }

  // Clean up yesterday's override entries on load (they're date-keyed, so
  // stale entries won't match today's tiles, but this keeps localStorage tidy).
  (function () {
    try {
      var d = new Date();
      var today = d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
      for (var i = localStorage.length - 1; i >= 0; i--) {
        var k = localStorage.key(i);
        if (k && k.indexOf(_OVERRIDE_PREFIX) === 0 && k.indexOf(today) === -1) {
          localStorage.removeItem(k);
        }
      }
    } catch (e) {}
  })();


  // dashboard (e.g. run manually on a different machine pointed at the same
  // output folder) and the run counter here never saw it, so the dashboard
  // keeps insisting on Run 01 before it unlocks Run 02. Injected into every
  // pipe's tools row (ctcl/ms15/cli/probable) — none of those boxes' own
  // markup (native to dashboard.html for ctcl/ms15/cli) has a control for
  // this, and dashboard.html can't be edited to add one directly. Backed by
  // the new generic /px/advance-run endpoint (generalizes the CLI-only
  // /cli/advance-run that already existed). ONLY changes which tile the
  // dashboard treats as "next" — never touches files or re-runs anything. ──
  function injectAdvanceRunButtons() {
    // NOTE: Short Allocation ('sa') doesn't follow the #pxBox-<pipe> convention
    // the other four boxes use — its wrapper is #saBox (dashboard.html, native
    // markup) — so it needs its own selector here rather than joining the
    // ['ctcl','ms15','cli','probable'] pipe-key loop below.
    //
    // Mark-run-done and Backfill-to-DB used to be two SEPARATE injected
    // buttons — on boxes with a lot of native buttons already (e.g. Short
    // Allocation's 8), adding 2 more pushed the row onto a second line. They
    // now share ONE button ("🛠 Manual overrides") that opens a small menu
    // offering both — same two actions, same password gate, one button's
    // worth of extra width instead of two. ──────────────────────────────────
    [
      { pipe: 'ctcl', sel: '#pxBox-ctcl .sa-tools' },
      { pipe: 'ms15', sel: '#pxBox-ms15 .sa-tools' },
      { pipe: 'cli', sel: '#pxBox-cli .sa-tools' },
      { pipe: 'probable', sel: '#pxBox-probable .sa-tools' },
      { pipe: 'sa', sel: '#saBox .sa-tools' },
    ].forEach(function (t) {
      var tools = document.querySelector(t.sel);
      if (!tools || tools.querySelector('.px-overrides-btn')) return;
      var btn = document.createElement('button');
      btn.className = 'sa-tool px-overrides-btn';
      btn.textContent = '🛠 Manual overrides';
      btn.title = 'Mark a run done, or push already-on-disk backup files into Postgres — for when a run genuinely happened OUTSIDE this dashboard (e.g. run manually on another machine).';
      btn.style.borderColor = 'rgba(167,139,250,.45)';
      btn.style.color = 'var(--purple, #a78bfa)';
      btn.onclick = function () {
        // Same password as the 🔑 Credentials button (SA_CONFIG_PASSWORD) —
        // reuses the identical modal, checked again server-side in whichever
        // endpoint the chosen action actually calls, so it can't be skipped
        // by calling the API directly.
        window.saAskPassword('Manual overrides', function (pw) { manualOverridesMenu(t.pipe, pw); });
      };
      tools.appendChild(btn);
    });
  }

  function manualOverridesMenu(pipe, pw) {
    var label = (typeof window._pipeBoxLabel === 'function') ? window._pipeBoxLabel(pipe) : pipe;
    var html =
      '<div style="font-size:13px;color:var(--muted);margin-bottom:16px">' +
      'Both of these are ONLY for a run that genuinely already happened OUTSIDE this dashboard ' +
      '(e.g. run by hand on a different machine). Neither re-runs anything or touches files that ' +
      'aren\'t already there.</div>' +
      '<button class="sa-save" style="width:100%;margin-bottom:10px;text-align:left" onclick="_pxOverrideGo1()">' +
        '⏭ Mark run done<div style="font-weight:400;font-size:11.5px;opacity:.85;margin-top:3px">Tells the dashboard which run number is already done, so the next tile unlocks.</div>' +
      '</button>' +
      '<button class="sa-save" style="width:100%;text-align:left" onclick="_pxOverrideGo2()">' +
        '☁ Backfill to DB<div style="font-weight:400;font-size:11.5px;opacity:.85;margin-top:3px">Pushes an already-on-disk backup folder\'s files into Postgres.</div>' +
      '</button>';
    if (typeof window.saShowModal !== 'function') { return; }
    window.saShowModal('🛠 Manual overrides — ' + label, html);
    window._pxOverrideGo1 = function () { advanceRunPrompt(pipe, pw); };
    window._pxOverrideGo2 = function () { backfillPrompt(pipe, pw); };
  }

  // ── MS15-only: "📱 WhatsApp numbers" for the MARGIN SHORTAGE ALERT image
  // (an internal RM/branch team list — separate from the per-client Gupshup
  // alerts, which look up each client's own mobile number automatically and
  // have no fixed list to edit). MS15's box is native to dashboard.html
  // (can't be edited on disk), so this is injected the same way every other
  // extra button this session was. Backed by /ms15/shortage-wa-config. ────
  function injectMs15WaButton() {
    var tools = document.querySelector('#pxBox-ms15 .sa-tools');
    if (!tools || tools.querySelector('.ms15-wa-btn')) return;
    var btn = document.createElement('button');
    btn.className = 'sa-tool ms15-wa-btn';
    btn.textContent = '📱 WhatsApp numbers';
    btn.title = 'Change who receives the MARGIN SHORTAGE ALERT image (an internal team list — not clients).';
    btn.onclick = function () {
      window.saAskPassword('WhatsApp numbers', function (pw) { ms15WaConfigPrompt(pw); });
    };
    // Front of the row, matching Short Allocation's native toolbar (its own
    // "📱 WhatsApp numbers" button is the first one, per explicit request that
    // every box's layout match) — not appended at the end like the other
    // injected buttons.
    tools.insertBefore(btn, tools.firstChild);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // "📋 View" ON THE DONE TILE — matching Short Allocation.
  //
  // SA's own tiles turn their button into "📄 View" once that run is done
  // (dashboard.html's saRenderRuns: label='📄 View', onclick=saOpenSummary(id)),
  // and CTCL does the same with ctclOpenView(). The GENERIC px path used by
  // Margin Shortage and PROBABLE never got that treatment — its done tiles show
  // a dead, disabled "✓ Done" and the View button was appended as a separate
  // floating row underneath the whole box, far from the run it describes. This
  // brings those pipes in line with SA.
  //
  // Only the MOST RECENT completed tile becomes a View button, deliberately.
  // The generic path keeps ONE run summary per pipe (_pxRunLog[pipe], the last
  // run's outcome) — there is no per-run history behind it. Putting a View
  // button on every done tile would show run 1's tile a summary that actually
  // belongs to run 5, which is worse than showing nothing. Older tiles keep
  // their plain "✓ Done"; their full output is still in 🗂️ Logs.
  //
  // Re-applied after every pxRenderRuns() because the status poller re-renders
  // the tiles from scratch on each tick and would otherwise wipe this out.
  // ══════════════════════════════════════════════════════════════════════════
  function _pxViewOnTile(pipe) {
    var wrap = document.getElementById('pxRuns-' + pipe);
    if (!wrap) return;

    var info = {};
    try { if (typeof _pxRunLog !== 'undefined' && _pxRunLog) info = _pxRunLog[pipe] || {}; } catch (e) {}
    // Nothing recorded yet (never run, or log cleared) — leave the native tiles
    // exactly as they are rather than offering a View with nothing behind it.
    if (!info.ts && !info.log) { _pxViewRowSet(pipe, false); return; }

    var tiles = wrap.querySelectorAll('.sa-run.done');
    if (!tiles.length) { _pxViewRowSet(pipe, true); return; }

    // EVERY completed tile gets a View button, not just the last one.
    //
    // One caveat, handled honestly rather than hidden: the generic px path keeps
    // only ONE run summary per pipe (_pxRunLog[pipe] — the most recent run's
    // outcome). It has no per-run history. So the LAST done tile opens that
    // summary, while earlier tiles open 🗂️ Logs, which genuinely does hold a
    // separate saved log per run. Both answer "what happened on this run"; they
    // just come from different places. Pretending an older tile had a summary it
    // doesn't would show run 1 the results of run 5 — worse than sending you to
    // the logs.
    var lastIdx = tiles.length - 1;
    tiles.forEach(function (tile, idx) {
      var btn = tile.querySelector('.sa-run-btn');
      if (!btn || btn.getAttribute('data-px-view') === '1') return;
      var isLatest = (idx === lastIdx);

      btn.setAttribute('data-px-view', '1');
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.style.cursor = 'pointer';

      if (isLatest && info.hasError) {
        btn.style.borderColor = 'rgba(248,113,113,.55)';
        btn.style.color = 'var(--red)';
        btn.innerHTML = '📋 View · Why it failed';
        btn.setAttribute('data-tip', "See this run's outcome and exactly what went wrong.");
      } else {
        btn.innerHTML = '📋 View';
        btn.setAttribute('data-tip', isLatest
          ? "See this run's outcome."
          : "Open the saved logs to see what this run did.");
      }

      var runId = (tile.querySelector('.sa-run-id') || {}).textContent || '';
      btn.onclick = isLatest
        ? function () { if (typeof window.pxOpenView === 'function') window.pxOpenView(pipe); }
        : function () { _pxOpenOlderRunView(pipe, runId.trim()); };
    });

    // The tiles now carry it, so the floating duplicate underneath goes away.
    _pxViewRowSet(pipe, false);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // SHOW THE START TIME ONLY — "09:30", not "09:30-09:45".
  //
  // The windows are stored as ranges because the END time drives one cosmetic
  // decision: whether a tile reads "Ready — run" or "Overdue — run". It is NOT
  // a deadline — the Run button stays enabled long after the end time, and a
  // missed slot can always still be run. But displaying the range made it look
  // like a window you could miss and then be locked out of, which is the
  // opposite of how it actually behaves and caused real hesitation about
  // whether a late run was still allowed.
  //
  // So the range keeps driving the label, and the tile shows just the start.
  // Display-only: nothing about when a run is permitted changes.
  // ══════════════════════════════════════════════════════════════════════════
  function _pxWinStartOnly(pipe) {
    var wrap = document.getElementById('pxRuns-' + pipe);
    if (!wrap) return;
    wrap.querySelectorAll('.sa-run-win').forEach(function (el) {
      var txt = (el.textContent || '').trim();
      // Matches "09:30-09:45" / "09:30 - 09:45"; leaves an already-trimmed
      // "09:30" and the em-dash placeholder ("—:— · —:—") untouched.
      var m = txt.match(/^(\d{1,2}:\d{2})\s*-\s*\d{1,2}:\d{2}$/);
      if (m) el.textContent = m[1];
    });
  }

  // ── "MS15-010" → "MS15-10". dashboard.html builds run ids by string
  // concatenation: pipe.toUpperCase()+'-0'+i. That's correct for runs 1-9 but
  // produces a three-digit "010"/"011" from run 10 onward — which only started
  // showing when Margin Shortage went from 9 runs to 11. Same bug in the
  // "After MS15-0N" caption on locked tiles. Cosmetic only (nothing parses
  // these strings), so it's corrected in the rendered DOM.
  function _pxFixRunIdPadding(pipe) {
    var wrap = document.getElementById('pxRuns-' + pipe);
    if (!wrap) return;
    var PFX = pipe.toUpperCase();
    // MS15 ONLY: drop the prefix entirely and show just the number — the box
    // heading directly above already says "Margin Shortage (15-min)", so
    // repeating "MS15-" on all eleven tiles is noise. Deliberately scoped to
    // this one pipe; every other box keeps its full "CTCL-01" style ids.
    var bare = (pipe === 'ms15');
    wrap.querySelectorAll('.sa-run-id').forEach(function (el) {
      var t = (el.textContent || '').trim();
      var m = t.match(/^(.*?-)0?(\d+)$/);
      if (!m) return;
      var n = m[2].replace(/^0+/, '') || '0';
      if (n.length < 2) n = '0' + n;                 // 1 → 01, 10 stays 10
      el.textContent = bare ? n : (m[1] + n);
    });
    wrap.querySelectorAll('.sa-run-state').forEach(function (el) {
      var t = el.textContent || '';
      if (t.indexOf(PFX + '-') === -1) return;
      // "After MS15-06" → "After 06" for ms15, "After MS15-6" padding fix only
      // for everything else.
      var fixed = t.replace(new RegExp(PFX + '-0?(\\d+)', 'g'), function (_m, d) {
        var n = d.replace(/^0+/, '') || '0';
        if (n.length < 2) n = '0' + n;
        return bare ? n : (PFX + '-' + n);
      });
      if (fixed !== t) el.textContent = fixed;
    });
  }

  // ── The SAME summary modal for an older run.
  //
  // Previously an older tile's View jumped straight to the raw Logs list, which
  // looked and behaved nothing like the latest run's tidy summary — same button,
  // two completely different screens. This gives every run the identical modal.
  //
  // What it can and can't say, honestly:
  //   • "✓ Run complete" is ALWAYS accurate here. A tile only turns .done when
  //     the run counter advanced, and that only happens on success — so a done
  //     tile is, by definition, a completed run.
  //   • There is no stored per-run timestamp (the console keeps one summary per
  //     pipe, not per run), so the "last updated …" line is simply omitted rather
  //     than filled with a time borrowed from a different run.
  //   • "↻ Run again" does EXACTLY what the latest run's button does — both call
  //     pxRun(pipe), which starts the next pending slot. Runs here are sequential
  //     slots, not re-runnable snapshots, so this is the same action, not a
  //     weaker version of it.
  function _pxOpenOlderRunView(pipe, runId) {
    if (typeof window.saShowModal !== 'function') return;
    var label = runId || pipe.toUpperCase();
    var body =
      '<div style="font-size:12.5px;color:var(--muted);margin-bottom:14px">' +
        '<span style="color:var(--green)">✓ Run complete</span>' +
        (runId ? ' · <b style="color:var(--ink)">' + label + '</b>' : '') +
      '</div>' +
      '<div style="margin-bottom:14px">' +
        '<a href="javascript:void(0)" onclick="saCloseModal();pxLogs(\'' + pipe + '\')" ' +
        'style="font-size:11px;color:var(--dim);text-decoration:underline;cursor:pointer">' +
        '🗂️ View the full raw output in Logs</a>' +
      '</div>' +
      '<button class="sa-tool" style="width:100%;border-color:rgba(96,165,250,.5);color:var(--blue)" ' +
      'onclick="saCloseModal();pxRun(\'' + pipe + '\')">↻ Run again</button>';
    window.saShowModal(pipe.toUpperCase() + ' — run summary' + (runId ? ' · ' + label : ''), body);
  }

  // Show/hide the native floating "📋 View" row. Kept (rather than deleted) for
  // the case where there is a run summary but no done tile to hang it on — e.g.
  // a run that failed before the completion counter was written, where the tile
  // never flips to .done but the operator still needs to see what happened.
  function _pxViewRowSet(pipe, show) {
    var row = document.getElementById('pxViewRow-' + pipe);
    if (row) row.style.display = show ? '' : 'none';
  }

  // ══════════════════════════════════════════════════════════════════════════
  // OVERRIDE TILE STYLING — runs after _pxViewOnTile on every render cycle.
  //
  // For each done tile, extract the run number and check localStorage for
  // override metadata. If found, restyle the tile so the operator can tell at
  // a glance that this run was NOT executed from this console:
  //   • State label:  "⏭ DONE EXTERNALLY" (purple) instead of "✓ DONE TODAY"
  //   • Button:       "⏭ Done externally" (or "⏭ Done externally · ☁ DB")
  //   • View modal:   explains the override, omits "raw logs" link, mentions
  //                   backfill if applicable
  //
  // Runs for ALL pipes including ctcl and cli (which have their own native
  // View buttons that get overridden if an override entry exists).
  // ══════════════════════════════════════════════════════════════════════════

  // Extract the numeric run number from a tile's .sa-run-id text.
  // "CTCL-01"→1, "02"→2, "MS15-10"→10, etc.
  function _tileRunNumber(tile) {
    var el = tile.querySelector('.sa-run-id');
    if (!el) return 0;
    var txt = (el.textContent || '').trim();
    var m = txt.match(/(\d+)\s*$/);
    return m ? parseInt(m[1], 10) : 0;
  }

  function _pxOverrideTiles(pipe) {
    var wrap = document.getElementById('pxRuns-' + pipe);
    if (!wrap) return;

    var tiles = wrap.querySelectorAll('.sa-run.done');
    tiles.forEach(function (tile) {
      var runNum = _tileRunNumber(tile);
      if (!runNum) return;

      var ov = _overrideInfo(pipe, runNum);
      if (!ov.isOverride) return;

      // Already styled on this render cycle
      if (tile.getAttribute('data-px-ovr') === '1') return;
      tile.setAttribute('data-px-ovr', '1');

      var btn = tile.querySelector('.sa-run-btn');
      if (!btn) return;

      btn.disabled = false;
      btn.style.cursor = 'pointer';

      // The tile button's DEFAULT enabled look is a solid bright-blue fill
      // (same as the "Run" button). Setting only a text colour on top of that
      // gave purple-on-blue — effectively invisible. So the background is
      // replaced outright with a dark translucent amber panel and the text set
      // to a bright, high-contrast amber. Amber (not purple) because purple on
      // this dark navy panel is genuinely low-contrast, while amber reads as
      // "attention / not a normal run" at a glance and is legible in both the
      // dark and light console themes. setProperty(..., 'important') is used
      // because dashboard.html's own rules for .sa-run-btn are !important.
      btn.style.setProperty('background', 'rgba(251,191,36,.16)', 'important');
      btn.style.setProperty('background-image', 'none', 'important');
      btn.style.setProperty('border', '1px solid rgba(251,191,36,.65)', 'important');
      btn.style.setProperty('color', '#fbbf24', 'important');
      btn.style.setProperty('opacity', '1', 'important');
      btn.style.setProperty('text-shadow', 'none', 'important');
      btn.style.setProperty('font-weight', '700', 'important');

      if (ov.backfill) {
        btn.innerHTML = '⏭ Ran elsewhere · ☁ in DB';
        btn.setAttribute('data-tip', 'This run was done outside this console. Its backup files are archived in Postgres.');
      } else {
        btn.innerHTML = '⏭ Ran elsewhere';
        btn.setAttribute('data-tip', 'This run was done outside this console and marked done manually. No logs here.');
      }

      var runId = (tile.querySelector('.sa-run-id') || {}).textContent || '';
      btn.onclick = function () { _pxOpenOverrideView(pipe, runId.trim(), ov); };

      // Replace the green "✓ DONE TODAY" caption — same amber, so the tile
      // reads as one consistent "not run from here" unit.
      var stateEl = tile.querySelector('.sa-run-state');
      if (stateEl) {
        var stTxt = (stateEl.textContent || '').toUpperCase();
        if (stTxt.indexOf('DONE') !== -1) {
          stateEl.textContent = '⏭ RAN ELSEWHERE';
          stateEl.style.setProperty('color', '#fbbf24', 'important');
        }
      }

      // A soft amber edge on the tile itself, so it's distinguishable from a
      // genuinely-run-here tile even without reading the words.
      tile.style.setProperty('border-color', 'rgba(251,191,36,.4)', 'important');
    });
  }

  // ── View modal for a manually-overridden run: no logs, no "Run complete",
  // just an honest account of what happened and a Run button if they want
  // to actually run it from here. ──
  function _pxOpenOverrideView(pipe, runId, ov) {
    if (typeof window.saShowModal !== 'function') return;
    var label = runId || pipe.toUpperCase();
    var mdAt = (ov.mark_done && ov.mark_done.at) ? ov.mark_done.at : '';
    var bfAt = (ov.backfill && ov.backfill.at) ? ov.backfill.at : '';
    var bfFiles = (ov.backfill && ov.backfill.files) || 0;

    var body =
      '<div style="font-size:12.5px;color:var(--muted);margin-bottom:14px">' +
        '<span style="color:#fbbf24;font-weight:700">⏭ Ran elsewhere</span>' +
        (runId ? ' · <b style="color:var(--ink)">' + label + '</b>' : '') +
      '</div>' +
      '<div style="font-size:13px;color:var(--muted);line-height:1.65;margin-bottom:16px">' +
        'This run was <b style="color:var(--ink)">not executed from this console</b>. It was marked done ' +
        'through Manual overrides' + (mdAt ? ' at <b style="color:var(--ink)">' + mdAt + '</b>' : '') + ', ' +
        'which only unlocks the next tile — so there is <b style="color:var(--ink)">no log and no output</b> ' +
        'stored here for it.' +
      '</div>';

    if (ov.backfill) {
      body +=
        '<div style="margin-bottom:16px;padding:10px 14px;border-radius:10px;font-size:12.5px;line-height:1.6;' +
          'background:rgba(96,165,250,.06);border:1px solid rgba(96,165,250,.25);color:var(--muted)">' +
          '☁ <b style="color:var(--ink)">Backup archived to Postgres</b>' +
          (bfFiles ? ' — ' + bfFiles + ' file(s)' : '') +
          (bfAt ? ' at ' + bfAt : '') + '.' +
        '</div>';
    }

    body +=
      '<button class="sa-tool" style="width:100%;border-color:rgba(96,165,250,.5);color:var(--blue)" ' +
      'onclick="saCloseModal();pxRun(\'' + pipe + '\')">▶ Run from this console</button>';

    window.saShowModal(pipe.toUpperCase() + ' — ' + label, body);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // Identity badge REMOVED — replaced by the signed session cookie + the
  // platform shell's avatar button (core.js → RMS.initUser). The old
  // _identityBadge / _identityPrompt / rms_user cookie / localStorage
  // mechanism is no longer needed. Cleanup: remove the #rmsWhoBadge element
  // if it exists from a previous page load's cached DOM.
  // ══════════════════════════════════════════════════════════════════════════
  (function () {
    var old = document.getElementById('rmsWhoBadge');
    if (old) old.remove();
  })();

  // ══════════════════════════════════════════════════════════════════════════
  // KEEP THE PROCESS YOU'RE WORKING ON AT THE TOP.
  //
  // The console lists its boxes in a fixed order, so the one you just started
  // can sit anywhere — often below the fold. You press Run, the page carries on
  // as it was, and you have to scroll back down hunting for the box to see
  // whether anything is happening.
  //
  // Rules, in priority order:
  //   0. RUNNING NOW  — pinned to the top, and stays pinned for the whole run.
  //   1. JUST FINISHED — stays at the top for GRACE_MS after it completes, so
  //      the result doesn't slide away the instant it appears.
  //   2. DUE NOW      — a run window is open (the box has the console's own
  //      .due class), so it's the natural next thing to do.
  //   3. everything else, ordered by each box's OWN first run time — so the
  //      page reads top-to-bottom in the order the day actually happens
  //      (PROBABLE 08:00 → CTCL 08:40 → Margin Shortage 09:30 → …), not in
  //      the arbitrary order the boxes were written into dashboard.html.
  //
  // The two rules that matter for not being annoying:
  //   • A box NEVER moves while it is running or inside its grace period. That's
  //     the whole complaint — things shifting under you mid-task.
  //   • The DOM is only touched when the resulting order actually CHANGES.
  //     Re-sorting on every status poll (every few seconds) would reintroduce
  //     exactly the flicker this is meant to remove.
  // ══════════════════════════════════════════════════════════════════════════
  var _ORDER_TARGETS = ['sa', 'ctcl', 'ms15', 'cli', 'probable'];
  var _ORDER_GRACE_MS = 2 * 60 * 1000;   // "stays there ~2 minutes after it finishes"
  var _orderBaseline = null;             // original DOM order, captured once
  var _orderSig = '';                    // last applied order, to avoid churn
  var _orderFinishedAt = {};             // target -> ms when its run ended
  var _orderWasRunning = {};             // target -> was it running last tick

  function _orderSection(target) {
    var box = document.getElementById(target === 'sa' ? 'saBox' : 'pxBox-' + target);
    if (!box) return null;
    return box.closest ? box.closest('.sec') : null;
  }

  // First run-window start for a box, as minutes-since-midnight, read from its
  // FIRST tile's .sa-run-win caption ("08:00" — already trimmed to the start
  // time by _pxWinStartOnly, but a raw "08:00-09:00" range is handled too).
  // Returns null when the tiles haven't rendered yet, so the caller can fall
  // back to DOM order rather than guessing.
  function _orderFirstRunMinutes(target) {
    var wrap = document.getElementById(target === 'sa' ? 'saRuns' : 'pxRuns-' + target);
    if (!wrap) {
      // Short Allocation's tile wrapper isn't id'd the same way — find it
      // inside its box instead.
      var box = document.getElementById(target === 'sa' ? 'saBox' : 'pxBox-' + target);
      wrap = box ? box.querySelector('.sa-runs') : null;
    }
    if (!wrap) return null;
    var wins = wrap.querySelectorAll('.sa-run-win');
    for (var i = 0; i < wins.length; i++) {
      var m = (wins[i].textContent || '').trim().match(/^(\d{1,2}):(\d{2})/);
      if (m) {
        var mins = parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
        if (!isNaN(mins)) return mins;
      }
    }
    return null;   // em-dash placeholder ("—:—") or not rendered yet
  }

  // Is this box running — for ANYONE, not just this tab? Local flags cover the
  // tab that pressed Run; the lock strip injected by process-locks.js covers a
  // run started on someone else's screen.
  function _orderIsRunning(target) {
    try {
      if (target === 'sa') {
        if (typeof _saRunning !== 'undefined' && _saRunning) return true;
      } else if (typeof _pxRunning !== 'undefined' && _pxRunning) {
        // dashboard.html tracks WHICH box is running in _pxRunningPipe
        // ('ctcl' | 'ms15' | 'cli' | …) — use it rather than guessing from the
        // rendered tile text, which also matches "Running…" left over mid-render.
        if (typeof _pxRunningPipe !== 'undefined' && _pxRunningPipe) {
          if (_pxRunningPipe === target) return true;
        } else {
          var w = document.getElementById('pxRuns-' + target);
          if (w && /Running/i.test(w.textContent || '')) return true;
        }
      }
    } catch (e) {}
    var box = document.getElementById(target === 'sa' ? 'saBox' : 'pxBox-' + target);
    return !!(box && box.querySelector('.lm-strip'));
  }

  function _orderRank(target, now) {
    if (_orderIsRunning(target)) return 0;
    var fin = _orderFinishedAt[target];
    if (fin && (now - fin) < _ORDER_GRACE_MS) return 1;
    var box = document.getElementById(target === 'sa' ? 'saBox' : 'pxBox-' + target);
    if (box && box.classList.contains('due')) return 2;
    return 3;
  }

  function _orderTick() {
    var secs = {}, present = [];
    _ORDER_TARGETS.forEach(function (t) {
      var s = _orderSection(t);
      if (s) { secs[t] = s; present.push(t); }
    });
    if (present.length < 2) return;

    var parent = secs[present[0]].parentNode;
    if (!parent) return;

    // ── BASELINE = THE ORDER THE DAY ACTUALLY HAPPENS IN. ────────────────────
    //
    // The console's own DOM order is arbitrary — it's just the order the boxes
    // were written into dashboard.html, which put PROBABLE last simply because
    // it was added last. But PROBABLE's first run is at 08:00, earlier than
    // CTCL (08:40) and Margin Shortage (09:30), so the box you need first sat
    // at the bottom of the page every morning.
    //
    // So the baseline is now each box's OWN first run-window time, read
    // straight from its first tile's .sa-run-win caption (which is rendered by
    // the console from that pipe's real configured windows — no second copy of
    // the schedule to drift out of sync). Boxes whose time can't be read yet
    // (tiles not rendered on the very first tick) keep their DOM position, so
    // nothing jumps around during page assembly.
    //
    // This only sets the tie-break for rank 3. Running / just-finished / due
    // boxes still outrank it — what's happening NOW always beats the schedule.
    if (!_orderBaseline || _orderBaseline.__partial) {
      var kids = Array.prototype.slice.call(parent.children);
      _orderBaseline = {};
      var anyUnknown = false;
      present.forEach(function (t) {
        var mins = _orderFirstRunMinutes(t);
        if (mins === null) {
          anyUnknown = true;
          // Fall back to DOM position, pushed past any real time (24h = 1440)
          // so a box with a known time always sorts ahead of an unknown one.
          _orderBaseline[t] = 1440 + kids.indexOf(secs[t]);
        } else {
          _orderBaseline[t] = mins;
        }
      });
      // Re-derive on the next tick if some tiles hadn't rendered yet.
      if (anyUnknown) _orderBaseline.__partial = true;
    }

    var now = Date.now();
    // Note the moment a run ends, to start its grace period.
    present.forEach(function (t) {
      var run = _orderIsRunning(t);
      if (_orderWasRunning[t] && !run) _orderFinishedAt[t] = now;
      _orderWasRunning[t] = run;
    });

    var ranked = present.slice().sort(function (a, b) {
      var ra = _orderRank(a, now), rb = _orderRank(b, now);
      if (ra !== rb) return ra - rb;
      return (_orderBaseline[a] || 0) - (_orderBaseline[b] || 0);
    });

    var sig = ranked.join('|');
    if (sig === _orderSig) return;    // nothing actually moved — leave the DOM alone
    _orderSig = sig;

    // Preserve scroll position relative to the box the user is most likely
    // looking at (the top-ranked one), so promoting it doesn't yank the page.
    var top = secs[ranked[0]];
    var before = top.getBoundingClientRect().top;
    ranked.forEach(function (t) { parent.appendChild(secs[t]); });
    var after = top.getBoundingClientRect().top;
    var delta = after - before;
    if (delta) {
      try { window.scrollBy(0, delta); } catch (e) {}
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // Run-status strip helpers (used by the pxHandle wrapper in patchGlobals).
  // ══════════════════════════════════════════════════════════════════════════
  var _pxEvtTimers = {};   // pipe -> {collapse, clear}

  // The backend hands the whole printed line through as d.msg. The pipelines
  // print "<emoji> Email sent successfully — MAIN summary → a@b.com [file.xlsx]";
  // everything after the first em-dash is the part worth showing. Returns ''
  // for an older/plain line so the caller falls back to the generic wording
  // rather than rendering something empty.
  function _pxEvtDetail(msg) {
    if (!msg) return '';
    var i = String(msg).indexOf('—');
    if (i === -1) return '';
    return String(msg).slice(i + 1).replace(/[<>]/g, '').trim();
  }

  function _pxEvtCancelTimers(pipe) {
    var t = _pxEvtTimers[pipe];
    if (!t) return;
    if (t.collapse) clearTimeout(t.collapse);
    if (t.clear) clearTimeout(t.clear);
    delete _pxEvtTimers[pipe];
  }

  function _pxEvtScheduleCollapse(pipe, ok) {
    _pxEvtCancelTimers(pipe);
    var COLLAPSE_MS = 60 * 1000;        // 1 minute — hide the detail
    var CLEAR_MS = 5 * 60 * 1000;       // then 5 more — hide everything
    var t = {};
    t.collapse = setTimeout(function () {
      var box = document.getElementById('pxEvents-' + pipe);
      if (!box) return;
      var when = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      box.innerHTML =
        '<div class="sa-ev ' + (ok ? 'ok' : 'bad') + '">' +
          '<span class="ic">' + (ok ? '✓' : '!') + '</span>' +
          '<span>' + (ok ? 'Process done' : 'Process finished with errors') +
          ' · ' + when + '</span>' +
          '<span class="what">' + (ok ? 'done' : 'errors') + '</span>' +
        '</div>';
      // The full detail is never actually lost — "📋 View" and "🗂️ Logs" still
      // have it. This only clears the transient on-screen strips.
      t.clear = setTimeout(function () {
        var b = document.getElementById('pxEvents-' + pipe);
        if (b) b.innerHTML = '';
        delete _pxEvtTimers[pipe];
      }, CLEAR_MS);
    }, COLLAPSE_MS);
    _pxEvtTimers[pipe] = t;
  }

  // ══════════════════════════════════════════════════════════════════════════
  // MS15 "🧪 Safe test run" — arms the NEXT run as a one-shot rehearsal where
  // every message is really sent but every destination is redirected to the
  // tester. Distinct from the old "Test run" (removed) which merely SKIPPED
  // sends and so proved nothing. The arm lives server-side as a marker file
  // the pipeline deletes on use, so it is shared across every browser/screen —
  // hence the state is polled, not kept in this tab's memory.
  // ══════════════════════════════════════════════════════════════════════════
  function injectMs15SafeTestButton() {
    var tools = document.querySelector('#pxBox-ms15 .sa-tools');
    if (!tools || tools.querySelector('.ms15-safetest-btn')) return;
    var btn = document.createElement('button');
    btn.className = 'sa-tool ms15-safetest-btn';
    btn.textContent = '🧪 Safe test run';
    btn.title = 'Arm the next run as a one-shot test: everything really sends, but only to you.';
    btn.onclick = function () { ms15SafeTestPrompt(); };
    tools.appendChild(btn);
    _ms15SafeTestTick();
    setInterval(_ms15SafeTestTick, 4000);
  }

  // Persistent, impossible-to-miss strip on the MS15 box whenever a test is
  // armed. Without this the operator has no way to tell an armed box from a
  // live one, and "is the next Run live?" is exactly the question that must
  // never be guessed at.
  function _ms15SafeTestRenderBanner(st) {
    var box = document.getElementById('pxBox-ms15');
    if (!box) return;
    var strip = document.getElementById('ms15SafeTestStrip');
    if (!st || !st.armed) { if (strip) strip.remove(); return; }
    if (!strip) {
      strip = document.createElement('div');
      strip.id = 'ms15SafeTestStrip';
      strip.style.cssText =
        'margin:10px 0 4px;padding:10px 13px;border-radius:10px;font-size:12px;line-height:1.55;' +
        'background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.45);color:var(--ink)';
      var tools = box.querySelector('.sa-tools');
      if (tools && tools.parentNode) tools.parentNode.insertBefore(strip, tools.nextSibling);
      else box.appendChild(strip);
    }
    strip.innerHTML =
      '<b style="color:#fbbf24">🧪 NEXT RUN IS A SAFE TEST — nothing will reach any client, branch or RM.</b><br>' +
      'WhatsApp (clients <i>and</i> the shortage image) → <b>' + (st.wa_to || '') + '</b> · ' +
      'All emails → <b>' + ((st.email_to || []).join(', ')) + '</b> (CC dropped, subject tagged [TEST]).<br>' +
      '<span style="color:var(--muted)">The alert tracker will not be written, so the next real run is unaffected. ' +
      'This arm is spent by one run.</span> ' +
      '<button onclick="_ms15SafeTestDisarm()" style="margin-left:6px;padding:4px 10px;border-radius:7px;border:1px solid var(--line-2);background:transparent;color:var(--muted);cursor:pointer;font-size:11px">Cancel test</button>';
  }

  function _ms15SafeTestTick() {
    fetch(API + '/ms15/safe-test').then(function (r) { return r.json(); })
      .then(function (st) {
        _ms15SafeTestRenderBanner(st);
        var b = document.querySelector('.ms15-safetest-btn');
        if (b) {
          b.textContent = st && st.armed ? '🧪 Test ARMED' : '🧪 Safe test run';
          b.style.borderColor = st && st.armed ? 'rgba(251,191,36,.6)' : '';
          b.style.color = st && st.armed ? '#fbbf24' : '';
        }
      }).catch(function () {});
  }

  window._ms15SafeTestDisarm = function () {
    fetch(API + '/ms15/safe-test/disarm', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function () { _ms15SafeTestTick(); })
      .catch(function () {});
  };

  function ms15SafeTestPrompt() {
    fetch(API + '/ms15/safe-test').then(function (r) { return r.json(); }).then(function (st) {
      if (typeof window.saShowModal !== 'function') return;
      if (st && st.armed) {
        window.saShowModal('🧪 Safe test run',
          '<div style="font-size:13px;color:var(--muted);line-height:1.6;margin-bottom:16px">' +
          'A safe test is <b style="color:#fbbf24">already armed</b>. The next time you press ' +
          '<b>Run</b> on this box it will run as a test, then disarm itself automatically.</div>' +
          '<button class="sa-save" style="width:100%" onclick="_ms15SafeTestDisarm();saCloseModal()">Cancel the armed test</button>');
        return;
      }
      var waTo = (st && st.wa_to) || '';
      var mailTo = ((st && st.email_to) || []).join(', ');
      var html =
        '<div style="font-size:13px;color:var(--muted);line-height:1.6;margin-bottom:14px">' +
        'This runs the <b style="color:var(--ink)">complete, real</b> Margin Shortage process — the same files, ' +
        'the same Excel outputs, the same shortage image, the same Gupshup / WhatsApp / email calls. ' +
        'Only the <b style="color:var(--ink)">destinations</b> change:</div>' +
        '<div style="border:1px solid var(--line);border-radius:11px;padding:13px 15px;margin-bottom:14px;font-size:12.5px;line-height:1.9">' +
          '<div>📲 Every per-client WhatsApp → <b>' + waTo + '</b><div style="font-size:11px;color:var(--dim);margin-top:-2px">No client receives anything.</div></div>' +
          '<div style="margin-top:8px">🖼️ Shortage alert image → <b>' + waTo + '</b><div style="font-size:11px;color:var(--dim);margin-top:-2px">Not the RM/branch list.</div></div>' +
          '<div style="margin-top:8px">✉️ Every email, main + all 4 zones → <b>' + mailTo + '</b><div style="font-size:11px;color:var(--dim);margin-top:-2px">CC dropped, subject tagged “[TEST — IGNORE]”.</div></div>' +
          '<div style="margin-top:8px">🗂️ Alert tracker → <b>not written</b><div style="font-size:11px;color:var(--dim);margin-top:-2px">So the next real run behaves as if this never happened.</div></div>' +
        '</div>' +
        '<div style="font-size:11.5px;color:var(--dim);margin-bottom:16px">' +
        'Valid for <b style="color:var(--ink)">one run only</b> — the pipeline discards the arm the moment the run starts, ' +
        'so the run after this one is fully live again. Change the targets in <code>.env</code> ' +
        '(<code>MS15_TEST_WA_TO</code> / <code>MS15_TEST_EMAIL_TO</code>).</div>' +
        '<div class="sa-field"><label>Authorisation password</label>' +
        '<input type="password" id="ms15StPw" placeholder="enter password" onkeydown="if(event.keyCode===13)_ms15SafeTestArm()"></div>' +
        '<button class="sa-save" style="width:100%" onclick="_ms15SafeTestArm()">Arm the next run as a safe test</button>' +
        '<div class="sa-msg" id="ms15StMsg"></div>';
      window.saShowModal('🧪 Safe test run — one run only', html);
      setTimeout(function () { var el = document.getElementById('ms15StPw'); if (el) el.focus(); }, 60);
    }).catch(function () { alert('Could not reach backend'); });
  }

  window._ms15SafeTestArm = function () {
    var pw = (document.getElementById('ms15StPw') || {}).value || '';
    var msg = document.getElementById('ms15StMsg');
    if (msg) msg.innerHTML = '<span style="color:var(--muted)">Arming…</span>';
    fetch(API + '/ms15/safe-test/arm?password=' + encodeURIComponent(pw), { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) { if (msg) msg.innerHTML = '<span style="color:var(--red)">✕ ' + ((d && d.error) || 'Failed') + '</span>'; return; }
        _ms15SafeTestTick();
        window.saShowModal('🧪 Safe test armed',
          '<div style="text-align:center;padding:12px 0 4px">' +
          '<div style="width:56px;height:56px;border-radius:50%;margin:0 auto 16px;display:grid;place-items:center;font-size:26px;background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.45)">🧪</div>' +
          '<div style="font-size:15px;color:var(--ink);font-weight:650;margin-bottom:6px">The next run is a safe test</div>' +
          '<div style="font-size:13px;color:var(--muted);line-height:1.6">Press <b style="color:var(--ink)">Run</b> on the Margin Shortage box whenever you\'re ready.<br>' +
          'It disarms itself as soon as that run starts.</div></div>' +
          '<button class="sa-save" style="width:100%;margin-top:18px" onclick="saCloseModal()">OK</button>');
      })
      .catch(function () { if (msg) msg.innerHTML = '<span style="color:var(--red)">✕ Could not reach backend.</span>'; });
  };

  // ── Remove MS15's "🧪 Test run" button — the Test Run feature was removed
  // per explicit request (core/pipelines/margin_shortage_15min.py no longer
  // accepts a test_mode argument at all — WhatsApp/image alerts always send
  // for real now, matching the reference script — and the backend route it
  // posted to, /ms15/test-run-stream, has been deleted). The button itself
  // is baked into dashboard.html's native markup (id="ms15TestBtn") and
  // can't be edited out on disk, so this removes the DOM node entirely at
  // box-render time instead. The toolbar row is flexbox
  // (.sa-tools{display:flex;flex-wrap:wrap}), so the remaining buttons
  // reflow cleanly on their own the instant it's gone — no gap, no separate
  // layout fix needed. ─────────────────────────────────────────────────────
  function removeMs15TestButton() {
    var btn = document.getElementById('ms15TestBtn');
    if (btn) btn.remove();
  }

  // ── Purge a STALE "📋 View (TEST)" leftover — dashboard.html persists the
  // last run's summary per pipe to localStorage (key "rms_lastlog_<pipe>")
  // and re-injects its "📋 View" button on every page load
  // (Object.keys(_pxRunLog).forEach(p=>pxInjectViewBtn(p)), near the bottom
  // of its own inline script) — including the "(TEST)" tag, if that cached
  // entry has isTest:true. Removing the Test Run button/route doesn't touch
  // this cached entry at all, so anyone who ran a Test before the removal
  // keeps seeing "📋 View (TEST)" forever, on every future page load, until
  // a real run overwrites it. Since dashboard.html can't be edited to stop
  // writing/reading that cache, this clears it out directly: drops the
  // localStorage entry (so it can never be loaded again), clears the
  // in-memory copy, and removes the already-rendered button for the page
  // that's open right now. A genuine future run's own log write (line
  // ~4843) always overwrites this immediately after, so this only ever
  // fires for a truly stale, no-longer-reachable test entry. ──────────────
  function purgeStaleMs15TestLog() {
    try {
      var raw = localStorage.getItem('rms_lastlog_ms15');
      if (!raw) return;
      var parsed = JSON.parse(raw);
      if (parsed && parsed.isTest) {
        localStorage.removeItem('rms_lastlog_ms15');
        try { if (typeof _pxRunLog !== 'undefined' && _pxRunLog) delete _pxRunLog['ms15']; } catch (e) {}
        var row = document.getElementById('pxViewRow-ms15');
        if (row) row.remove();
      }
    } catch (e) {}
  }

  // A user only ever types the 10 local digits ("9711667470"); this fills in
  // the +91 automatically. Left untouched if they already typed a "+" (some
  // other country code, or +91 itself) so nothing gets double-prefixed.
  function _ms15NormalizePhone(v) {
    v = (v || '').trim();
    if (!v) return '';
    if (v.charAt(0) === '+') return v;
    var digits = v.replace(/\D/g, '');
    if (digits.length > 10 && digits.slice(0, 2) === '91') digits = digits.slice(2);
    return digits ? '+91' + digits : '';
  }

  var MS15_WA_KEY = 'MS15_SHORTAGE_WA_TO';
  var MS15_WA_MAX = 10;

  // Same collapsed named-field list editor Short Allocation's own "WhatsApp
  // numbers" / "Email recipients" modals use (saListFieldCollapsed /
  // saListRow / saMaskValue, all native to dashboard.html) — reused as-is so
  // MS15's single recipient list looks and behaves identically: masked
  // "•••last4" entries, a "+" to reveal/add, a "−" per row to remove one.
  function ms15WaConfigPrompt(pw) {
    fetch(API + '/ms15/shortage-wa-config').then(function (r) { return r.json(); }).then(function (cfg) {
      if (typeof window.saShowModal !== 'function' || typeof window.saListFieldCollapsed !== 'function') { return; }
      window._saListState = window._saListState || {};
      window._saListState[MS15_WA_KEY] = (cfg.to || '').split(',').map(function (s) { return s.trim(); }).filter(function (s) { return s; });

      var rows = window.saListFieldCollapsed(MS15_WA_KEY, 'MARGIN SHORTAGE IMAGE RECIPIENTS', '9811122233 (10-digit number)', 'phone');
      rows += '<div style="font-size:11px;color:var(--dim);margin:-6px 0 16px">Up to ' + MS15_WA_MAX + ' numbers. Type just the 10-digit number — +91 is added automatically. ' +
              'Not the same list as the per-client alerts (those look up each client\'s own mobile automatically).</div>';
      rows += '<button class="sa-save" style="width:100%" onclick="_ms15SaveWaNumbers()">Save changes</button><div class="sa-msg" id="ms15WaMsg"></div>';

      window.saShowModal('📱 WhatsApp numbers — Margin Shortage Alert', rows);
      window.saListUpdateSummary(MS15_WA_KEY);

      window._ms15SaveWaNumbers = function () {
        var msg = document.getElementById('ms15WaMsg');
        var raw = (window._saListState[MS15_WA_KEY] || []).map(function (v) { return (v || '').trim(); }).filter(function (v) { return v !== ''; });
        if (raw.length > MS15_WA_MAX) {
          if (msg) msg.innerHTML = '<span style="color:var(--red)">✕ Maximum ' + MS15_WA_MAX + ' numbers allowed — remove ' + (raw.length - MS15_WA_MAX) + ' before saving.</span>';
          return;
        }
        var to = raw.map(_ms15NormalizePhone).filter(function (v) { return v; }).join(',');
        if (msg) msg.innerHTML = '<span style="color:var(--muted)">Saving…</span>';
        fetch(API + '/ms15/shortage-wa-config?to=' + encodeURIComponent(to) +
              '&password=' + encodeURIComponent(pw || ''), { method: 'POST' })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d && d.ok === false) { if (msg) msg.innerHTML = '<span style="color:var(--red)">✕ ' + (d.error || 'Failed') + '</span>'; return; }
            if (typeof window.saCloseModal === 'function') window.saCloseModal();
          }).catch(function () {
            if (msg) msg.innerHTML = '<span style="color:var(--red)">✕ Could not reach backend.</span>';
          });
      };
    }).catch(function () { alert('Could not load current config'); });
  }

  function backfillPrompt(pipe, pw) {
    function todayDDMMYYYY() {
      var d = new Date(), p2 = function (n) { return (n < 10 ? '0' : '') + n; };
      return p2(d.getDate()) + '-' + p2(d.getMonth() + 1) + '-' + d.getFullYear();
    }
    function todayProbableFmt() {
      var months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
      var d = new Date(), p2 = function (n) { return (n < 10 ? '0' : '') + n; };
      return p2(d.getDate()) + ' ' + months[d.getMonth()] + ' ' + d.getFullYear();
    }
    var label = (typeof window._pipeBoxLabel === 'function') ? window._pipeBoxLabel(pipe) : pipe;
    var needsRun = (pipe === 'ctcl' || pipe === 'ms15' || pipe === 'sa');
    var isProbable = (pipe === 'probable');
    var dateNote = isProbable
      ? 'Probable\'s backup folder is named differently from the others — e.g. "21 JUL 2026", not 21-07-2026.'
      : 'Folder name exactly as on disk, e.g. 21-07-2026.';
    var html =
      '<div style="font-size:13px;color:var(--muted);margin-bottom:14px">' +
      'Pushes an ALREADY-on-disk <b>' + label + '</b> backup folder into Postgres — for a run that ' +
      'completed on a different machine and whose files were then copied by hand into this machine\'s ' +
      'own backup folder. Does NOT move or delete anything on disk, and it\'s safe to run more than once.</div>' +
      '<div style="margin-bottom:14px">' +
        '<label style="font-size:12px;color:var(--muted);display:block;margin-bottom:6px">Date folder — ' + dateNote + '</label>' +
        '<input id="pxBackfillDate" type="text" value="' + (isProbable ? todayProbableFmt() : todayDDMMYYYY()) + '" style="width:100%;box-sizing:border-box;padding:9px;border-radius:8px;background:var(--panel-2);color:var(--ink);border:1px solid var(--line)">' +
      '</div>' +
      (needsRun ?
        ('<div style="margin-bottom:14px">' +
          '<label style="font-size:12px;color:var(--muted);display:block;margin-bottom:6px">Run number:</label>' +
          '<input id="pxBackfillRun" type="number" min="1" value="1" style="width:100%;box-sizing:border-box;padding:9px;border-radius:8px;background:var(--panel-2);color:var(--ink);border:1px solid var(--line)">' +
        '</div>') : '') +
      (isProbable ?
        ('<div style="margin-bottom:14px">' +
          '<label style="font-size:12px;color:var(--muted);display:block;margin-bottom:6px">Which part (leave as Both if unsure):</label>' +
          '<select id="pxBackfillSubfolder" style="width:100%;box-sizing:border-box;padding:9px;border-radius:8px;background:var(--panel-2);color:var(--ink);border:1px solid var(--line)">' +
            '<option value="">Both (Reconciliation + Shortage)</option>' +
            '<option value="RECONCILIATION">Reconciliation only</option>' +
            '<option value="SHORTAGE">Shortage only</option>' +
          '</select>' +
        '</div>') : '') +
      '<div style="display:flex;justify-content:flex-end;gap:10px">' +
        '<button onclick="saCloseModal()" style="padding:9px 16px;border-radius:9px;border:1px solid var(--line);background:transparent;color:var(--muted);cursor:pointer">Cancel</button>' +
        '<button id="pxBackfillGo" style="padding:9px 18px;border-radius:9px;border:none;background:var(--blue);color:#04101f;font-weight:700;cursor:pointer">Confirm</button>' +
      '</div>';
    if (typeof window.saShowModal !== 'function') { return; }
    window.saShowModal('☁ Backfill to DB — ' + label, html);
    var go = document.getElementById('pxBackfillGo');
    if (go) go.onclick = function () {
      var date = (document.getElementById('pxBackfillDate').value || '').trim();
      var run = needsRun ? (parseInt(document.getElementById('pxBackfillRun').value, 10) || 0) : 0;
      var subfolder = isProbable ? (document.getElementById('pxBackfillSubfolder').value || '') : '';
      var url = (pipe === 'sa')
        ? (API + '/sa/backfill-db?date=' + encodeURIComponent(date) + '&run=' + run + '&password=' + encodeURIComponent(pw || ''))
        : (API + '/px/backfill-db?pipe=' + encodeURIComponent(pipe) + '&date=' + encodeURIComponent(date) +
           '&run=' + run + '&subfolder=' + encodeURIComponent(subfolder) + '&password=' + encodeURIComponent(pw || ''));
      fetch(url, { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.ok === false) { alert(d.error || 'Failed'); return; }
          // Record the backfill so tiles/View can show "DB synced" status
          var now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          var fileCount = (d.archived || []).length;
          if (run) _overrideSaveBackfill(pipe, run, { type: 'backfill', at: now, files: fileCount });
          if (typeof window.saCloseModal === 'function') window.saCloseModal();
          var n = (d.archived || []).length, s = (d.skipped || []).length;
          alert('Archived ' + n + ' file(s) to Postgres' + (s ? (', ' + s + ' skipped (see server log).') : '.'));
        }).catch(function () {
          if (typeof window.saCloseModal === 'function') window.saCloseModal();
        });
    };
  }

  function advanceRunPrompt(pipe, pw) {
    if (pipe === 'sa') { return advanceRunPromptSA(pw); }
    var label = (typeof window._pipeBoxLabel === 'function') ? window._pipeBoxLabel(pipe) : pipe;
    var html =
      '<div style="font-size:13px;color:var(--muted);margin-bottom:14px">' +
      'Use this ONLY if a run for <b>' + label + '</b> genuinely already completed OUTSIDE this dashboard ' +
      '(e.g. run by hand on a different machine pointed at the same output folder). ' +
      'This does NOT re-run anything or touch any files — it only tells the dashboard which run number ' +
      'is done, so the next one\'s tile unlocks.</div>' +
      '<div style="margin-bottom:14px">' +
        '<label style="font-size:12px;color:var(--muted);display:block;margin-bottom:6px">Mark run number as DONE:</label>' +
        '<input id="pxAdvanceInput" type="number" min="0" value="1" style="width:100%;box-sizing:border-box;padding:9px;border-radius:8px;background:var(--panel-2);color:var(--ink);border:1px solid var(--line)">' +
      '</div>' +
      '<div style="display:flex;justify-content:flex-end;gap:10px">' +
        '<button onclick="saCloseModal()" style="padding:9px 16px;border-radius:9px;border:1px solid var(--line);background:transparent;color:var(--muted);cursor:pointer">Cancel</button>' +
        '<button id="pxAdvanceGo" style="padding:9px 18px;border-radius:9px;border:none;background:var(--blue);color:#04101f;font-weight:700;cursor:pointer">Confirm</button>' +
      '</div>';
    if (typeof window.saShowModal !== 'function') { return; }
    window.saShowModal('⏭ Mark run done — ' + label, html);
    var go = document.getElementById('pxAdvanceGo');
    if (go) go.onclick = function () {
      var n = parseInt(document.getElementById('pxAdvanceInput').value, 10) || 0;
      fetch(API + '/px/advance-run?pipe=' + encodeURIComponent(pipe) + '&to=' + n +
            '&password=' + encodeURIComponent(pw || ''), { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.ok === false) { alert(d.error || 'Failed'); return; }
          // Record that this run was manually marked done (not run from console)
          var now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          _overrideSave(pipe, n, { type: 'mark_done', at: now });
          if (typeof window.saCloseModal === 'function') window.saCloseModal();
          if (typeof window.pxStatus === 'function') window.pxStatus(pipe);
        }).catch(function () {
          if (typeof window.saCloseModal === 'function') window.saCloseModal();
        });
    };
  }

  // ── Short Allocation's version of Mark-run-done. SA doesn't track "next
  // run" via a run_counter.txt like ctcl/ms15/cli/probable — it derives the
  // next unlocked tile from each SA-0N's OWN outcome record (code2/overall
  // in short_allocation's _run_status.json, read by dashboard.html's
  // saRenderRuns()), so this picks a specific SA-0N tile by name instead of
  // an arbitrary run number, and posts to /sa/mark-run-done instead of
  // /px/advance-run. ────────────────────────────────────────────────────
  function advanceRunPromptSA(pw) {
    var html =
      '<div style="font-size:13px;color:var(--muted);margin-bottom:14px">' +
      'Use this ONLY if an SA run genuinely already completed OUTSIDE this dashboard ' +
      '(e.g. run by hand on a different machine). This does NOT re-run anything or touch ' +
      'any files — it only marks that run\'s outcome as done, so the next tile unlocks.</div>' +
      '<div style="margin-bottom:14px">' +
        '<label style="font-size:12px;color:var(--muted);display:block;margin-bottom:6px">Mark run as DONE:</label>' +
        '<select id="saAdvanceSelect" style="width:100%;box-sizing:border-box;padding:9px;border-radius:8px;background:var(--panel-2);color:var(--ink);border:1px solid var(--line)">' +
          '<option value="1">SA-01</option><option value="2">SA-02</option>' +
          '<option value="3">SA-03</option><option value="4">SA-04</option>' +
        '</select>' +
      '</div>' +
      '<div style="display:flex;justify-content:flex-end;gap:10px">' +
        '<button onclick="saCloseModal()" style="padding:9px 16px;border-radius:9px;border:1px solid var(--line);background:transparent;color:var(--muted);cursor:pointer">Cancel</button>' +
        '<button id="saAdvanceGo" style="padding:9px 18px;border-radius:9px;border:none;background:var(--blue);color:#04101f;font-weight:700;cursor:pointer">Confirm</button>' +
      '</div>';
    if (typeof window.saShowModal !== 'function') { return; }
    window.saShowModal('⏭ Mark run done — Short Allocation', html);
    var go = document.getElementById('saAdvanceGo');
    if (go) go.onclick = function () {
      var n = parseInt(document.getElementById('saAdvanceSelect').value, 10) || 1;
      fetch(API + '/sa/mark-run-done?run=' + n + '&password=' + encodeURIComponent(pw || ''), { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.ok === false) { alert(d.error || 'Failed'); return; }
          var now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          _overrideSave('sa', n, { type: 'mark_done', at: now });
          if (typeof window.saCloseModal === 'function') window.saCloseModal();
          if (typeof window.saLoadStatus === 'function') window.saLoadStatus();
        }).catch(function () {
          if (typeof window.saCloseModal === 'function') window.saCloseModal();
        });
    };
  }

  function buildBox(host) {
    var secTitle = document.createElement('div');
    secTitle.className = 'sec';
    secTitle.textContent = 'PROBABLE';
    host.appendChild(secTitle);

    var box = document.createElement('div');
    box.className = 'sa-box';
    box.id = 'pxBox-' + PIPE;
    box.innerHTML =
      '<div class="sa-head" style="flex-direction:column;gap:10px">' +
        '<div class="sa-title-wrap"><div class="sa-logo">PD</div><div>' +
          '<div class="sa-title">PROBABLE</div>' +
          '<div class="sa-sub">Reconcile exchange margin vs internal exposure → MATCHED/MISMATCH report → zone emails → client WhatsApp margin-call alerts. Runs once/day. Download NMASS/NCDEX + TCS BANCS ahead of time, upload today’s Probable_Fullfile manually, then Run.</div>' +
        '</div></div>' +
        '<div class="sa-tools">' +
          '<button class="sa-tool" onclick="pxConfig(\'probable\')" data-tip="Change which email addresses get the reconciliation report, each zone\'s shortage report, the final summary, and (optionally) an override for every branch/region email.">✉️ Email To / CC</button>' +
          '<button class="sa-tool" onclick="pxCredentials(\'probable\')" data-tip="Change logins/passwords: NSE Delivery Margin, NMASS/NCDEX (shared with CTCL vs NMASS), TCS BANCS RSK383/RSK335 (shared with Short Allocation / Client Prudential Limit), both SMTP senders, WhatsApp (Tata) token, webpayout OAuth, and the zone-file Excel password.">🔑 Credentials</button>' +
          '<button class="sa-tool" onclick="pxUpload(\'probable\')" data-tip="Upload today’s Probable_Fullfile (downloaded manually each day) or any input file the automatic download couldn’t get. Only recognised filenames are accepted.">⬆️ Upload files</button>' +
          '<button class="sa-tool" onclick="pxBackup(\'probable\')" data-tip="Browse/restore past days’ reconciliation and alert files.">📦 Backup files</button>' +
          '<button class="sa-tool" onclick="probableStop()" data-tip="Stops whatever is currently running for PROBABLE — download, reconcile, or alerts." style="border-color:rgba(248,113,113,.45);color:var(--red)">⛔ Stop</button>' +
          '<button class="sa-tool" onclick="pxLogs(\'probable\')" data-tip="View the full saved output of past downloads/runs.">🗂️ Logs</button>' +
          '<button class="sa-tool" id="probableNmassBtn" onclick="probableNmassDownload()" data-tip="Downloads CM/FO/CO ClientLevelCollaterals + ClientNetEffectiveDeposits into this box’s own folder — same NMASS/NCDEX login as CTCL vs NMASS / Margin Shortage." style="border-color:rgba(96,165,250,.45);color:var(--blue)">📥 NMASS/NCDEX</button>' +
          '<button class="sa-tool" id="probableTcsBtn" onclick="probableTcsBancsDownload()" data-tip="Logs in to TCS BANCS and downloads BOTH RSK383 (Cash Collateral Deposit Limit) and RSK335 (Margin Utilization View / 90% file) — same account as Short Allocation’s MCX Deposit / Client Prudential Limit’s MCX Web Allocation buttons; shares that same session lock." style="border-color:rgba(96,165,250,.45);color:var(--blue)">📥 TCS (DEPOSIT+90%)</button>' +
        '</div>' +
      '</div>' +
      '<div class="sa-banner" id="pxBanner-probable"><span class="dot"></span><span class="t"></span></div>' +
      '<div id="probableTestModeBanner" style="display:none;font-size:12px;font-weight:700;margin:0 0 10px;padding:9px 14px;border-radius:9px"></div>' +
      '<div class="sa-ready" id="probableReady" style="display:none"></div>' +
      '<div id="probableNmassStrip" style="display:none;font-size:10.5px;font-family:var(--mono);margin:0 0 10px;padding:8px 14px;border:1px solid rgba(96,165,250,.2);border-radius:9px;background:rgba(96,165,250,.04);line-height:1.7"></div>' +
      '<div id="probableTcsStrip" style="display:none;font-size:10.5px;font-family:var(--mono);margin:0 0 10px;padding:8px 14px;border:1px solid rgba(96,165,250,.2);border-radius:9px;background:rgba(96,165,250,.04);line-height:1.7"></div>' +
      '<div class="sa-runs" id="pxRuns-probable"></div>' +
      '<div class="sa-status" id="pxStatus-probable"><div class="sa-status-empty">Run status — email/WhatsApp confirmations — appears here. Full output is saved to Logs.</div></div>';
    host.appendChild(box);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // PROBABLE "🧪 Safe test run" — the same one-shot rehearsal MS15 has.
  //
  // Replaces the old PROBABLE_TEST_MODE banner, which reported a .env flag that
  // merely SKIPPED WhatsApp sends. Skipping proved nothing: a dead token or a
  // wrong template name looked identical to a passing test. A safe test really
  // sends everything and only redirects the destination, so a broken send path
  // actually fails visibly — which is the entire point of testing it.
  //
  // The arm lives server-side as a marker file the pipeline deletes on use, so
  // the state is shared across every browser/screen and is polled rather than
  // held in this tab's memory. ──────────────────────────────────────────────
  function injectProbableSafeTestButton() {
    var tools = document.querySelector('#pxBox-' + PIPE + ' .sa-tools');
    if (!tools || tools.querySelector('.probable-safetest-btn')) return;
    var btn = document.createElement('button');
    btn.className = 'sa-tool probable-safetest-btn';
    btn.textContent = '🧪 Safe test run';
    btn.title = 'Arm the next run as a one-shot test: everything really sends, but only to you.';
    btn.onclick = function () { probableSafeTestPrompt(); };
    tools.appendChild(btn);
    _probableSafeTestTick();
    setInterval(_probableSafeTestTick, 4000);
  }

  // Persistent, impossible-to-miss strip whenever a test is armed. Without this
  // there is no way to tell an armed box from a live one, and "is the next Run
  // live?" is exactly the question that must never be guessed at.
  function _probableSafeTestRenderBanner(st) {
    var el = document.getElementById('probableTestModeBanner');
    if (!el) return;
    if (!st || !st.armed) { el.style.display = 'none'; el.innerHTML = ''; return; }
    el.style.display = 'block';
    el.style.color = 'var(--ink)';
    el.style.border = '1px solid rgba(251,191,36,.45)';
    el.style.background = 'rgba(251,191,36,.10)';
    el.style.lineHeight = '1.55';
    el.style.fontWeight = '400';
    el.innerHTML =
      '<b style="color:#fbbf24">🧪 NEXT RUN IS A SAFE TEST — nothing will reach any client, branch or RM.</b><br>' +
      'Every client WhatsApp → <b>' + (st.wa_to || '') + '</b> · ' +
      'All emails (reconciliation, zones, branch/region, summary) → <b>' + ((st.email_to || []).join(', ')) + '</b> ' +
      '(CC dropped, subject tagged [TEST]).<br>' +
      '<span style="color:var(--muted)">Backup &amp; cleanup is skipped, so today\'s files stay in place and your ' +
      'next real run will still find them. This arm is spent by one run.</span> ' +
      '<button onclick="_probableSafeTestDisarm()" style="margin-left:6px;padding:4px 10px;border-radius:7px;border:1px solid var(--line-2);background:transparent;color:var(--muted);cursor:pointer;font-size:11px">Cancel test</button>';
  }

  function _probableSafeTestTick() {
    fetch(API + '/probable/safe-test').then(function (r) { return r.json(); })
      .then(function (st) {
        _probableSafeTestRenderBanner(st);
        var b = document.querySelector('.probable-safetest-btn');
        if (b) {
          b.textContent = st && st.armed ? '🧪 Test ARMED' : '🧪 Safe test run';
          b.style.borderColor = st && st.armed ? 'rgba(251,191,36,.6)' : '';
          b.style.color = st && st.armed ? '#fbbf24' : '';
        }
      }).catch(function () {});
  }

  window._probableSafeTestDisarm = function () {
    fetch(API + '/probable/safe-test/disarm', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function () { _probableSafeTestTick(); })
      .catch(function () {});
  };

  function probableSafeTestPrompt() {
    fetch(API + '/probable/safe-test').then(function (r) { return r.json(); }).then(function (st) {
      if (typeof window.saShowModal !== 'function') return;
      if (st && st.armed) {
        window.saShowModal('🧪 Safe test run',
          '<div style="font-size:13px;color:var(--muted);line-height:1.6;margin-bottom:16px">' +
          'A safe test is <b style="color:#fbbf24">already armed</b>. The next time you press ' +
          '<b>Run</b> on this box it will run as a test, then disarm itself automatically.</div>' +
          '<button class="sa-save" style="width:100%" onclick="_probableSafeTestDisarm();saCloseModal()">Cancel the armed test</button>');
        return;
      }
      var waTo = (st && st.wa_to) || '';
      var mailTo = ((st && st.email_to) || []).join(', ');
      var html =
        '<div style="font-size:13px;color:var(--muted);line-height:1.6;margin-bottom:14px">' +
        'This runs the <b style="color:var(--ink)">complete, real</b> Probable Defaulter process — the same ' +
        'reconciliation, the same Excel outputs, the same zone files, the same Tata WhatsApp and SMTP calls. ' +
        'Only the <b style="color:var(--ink)">destinations</b> change:</div>' +
        '<div style="border:1px solid var(--line);border-radius:11px;padding:13px 15px;margin-bottom:14px;font-size:12.5px;line-height:1.9">' +
          '<div>📲 Every per-client WhatsApp → <b>' + waTo + '</b><div style="font-size:11px;color:var(--dim);margin-top:-2px">Really sent, so a bad token or template will surface. No client receives anything.</div></div>' +
          '<div style="margin-top:8px">✉️ Reconciliation report → <b>' + mailTo + '</b></div>' +
          '<div style="margin-top:8px">✉️ All 3 zone emails (CHND/LUDH/MUMB) → <b>' + mailTo + '</b></div>' +
          '<div style="margin-top:8px">✉️ Every branch &amp; region email → <b>' + mailTo + '</b><div style="font-size:11px;color:var(--dim);margin-top:-2px">Overrides MAIL LIST.xlsx entirely — no branch is emailed.</div></div>' +
          '<div style="margin-top:8px">✉️ Final summary → <b>' + mailTo + '</b><div style="font-size:11px;color:var(--dim);margin-top:-2px">CC dropped everywhere, subjects tagged “[TEST — IGNORE]”.</div></div>' +
          '<div style="margin-top:8px">📦 Backup &amp; cleanup → <b>skipped</b><div style="font-size:11px;color:var(--dim);margin-top:-2px">Today\'s downloaded files stay in the folder, so your real run still finds them.</div></div>' +
        '</div>' +
        '<div style="font-size:11.5px;color:var(--dim);margin-bottom:16px">' +
        'Valid for <b style="color:var(--ink)">one run only</b> — the arm is discarded the moment the run\'s ' +
        'file check passes, so the run after this one is fully live again. Change the targets in <code>.env</code> ' +
        '(<code>PROBABLE_TEST_WA_TO</code> / <code>PROBABLE_TEST_EMAIL_TO</code>).</div>' +
        '<div class="sa-field"><label>Authorisation password</label>' +
        '<input type="password" id="probableStPw" placeholder="enter password" onkeydown="if(event.keyCode===13)_probableSafeTestArm()"></div>' +
        '<button class="sa-save" style="width:100%" onclick="_probableSafeTestArm()">Arm the next run as a safe test</button>' +
        '<div class="sa-msg" id="probableStMsg"></div>';
      window.saShowModal('🧪 Safe test run — one run only', html);
      setTimeout(function () { var el = document.getElementById('probableStPw'); if (el) el.focus(); }, 60);
    }).catch(function () { alert('Could not reach backend'); });
  }

  window._probableSafeTestArm = function () {
    var pw = (document.getElementById('probableStPw') || {}).value || '';
    var msg = document.getElementById('probableStMsg');
    if (msg) msg.innerHTML = '<span style="color:var(--muted)">Arming…</span>';
    fetch(API + '/probable/safe-test/arm?password=' + encodeURIComponent(pw), { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) { if (msg) msg.innerHTML = '<span style="color:var(--red)">✕ ' + ((d && d.error) || 'Failed') + '</span>'; return; }
        _probableSafeTestTick();
        window.saShowModal('🧪 Safe test armed',
          '<div style="text-align:center;padding:12px 0 4px">' +
          '<div style="width:56px;height:56px;border-radius:50%;margin:0 auto 16px;display:grid;place-items:center;font-size:26px;background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.45)">🧪</div>' +
          '<div style="font-size:15px;color:var(--ink);font-weight:650;margin-bottom:6px">The next run is a safe test</div>' +
          '<div style="font-size:13px;color:var(--muted);line-height:1.6">Press <b style="color:var(--ink)">Run</b> on the PROBABLE box whenever you\'re ready.<br>' +
          'It disarms itself as soon as that run starts.</div></div>' +
          '<button class="sa-save" style="width:100%;margin-top:18px" onclick="saCloseModal()">OK</button>');
      })
      .catch(function () { if (msg) msg.innerHTML = '<span style="color:var(--red)">✕ Could not reach backend.</span>'; });
  };

  // ── readiness badge (missing-file warning), same idea as ctcl/ms15 ───────
  function probableLoadReady() {
    fetch(API + '/probable/readiness').then(function (r) { return r.json(); }).then(function (d) {
      var el = document.getElementById('probableReady');
      if (!el) return;
      var missing = (d.items || []).filter(function (it) { return !it.ok; });
      if (!missing.length) { el.style.display = 'none'; return; }
      el.style.display = 'block';
      el.innerHTML = '<b>Files needed before running:</b> ' +
        missing.map(function (m) { return m.key; }).join(', ');
    }).catch(function () {});
  }

  // ── market-time modal (replaces code2.py's desktop customtkinter popup) ──
  // Per instruction: never silently default to 9:30 — the operator must
  // actually confirm or change it, every single run.
  function showMarketTimeModal(onConfirm) {
    var hours = []; for (var h = 1; h <= 12; h++) hours.push(h);
    var mins = ['00', '15', '30', '45'];
    var html =
      '<div style="font-size:13px;color:var(--muted);margin-bottom:14px">' +
      'Confirm today’s market session time — used in the WhatsApp margin-call message sent to clients. ' +
      'Defaults to the usual 9:30 AM, but change it below if today’s session differs.</div>' +
      '<div style="display:flex;gap:8px;align-items:center;margin-bottom:18px">' +
        '<select id="probableMtHour" style="padding:8px;border-radius:8px;background:var(--panel-2);color:var(--ink);border:1px solid var(--line)">' +
          hours.map(function (x) { return '<option value="' + x + '"' + (x === 9 ? ' selected' : '') + '>' + x + '</option>'; }).join('') +
        '</select><span style="color:var(--muted)">:</span>' +
        '<select id="probableMtMin" style="padding:8px;border-radius:8px;background:var(--panel-2);color:var(--ink);border:1px solid var(--line)">' +
          mins.map(function (x) { return '<option value="' + x + '"' + (x === '30' ? ' selected' : '') + '>' + x + '</option>'; }).join('') +
        '</select>' +
        '<select id="probableMtAmpm" style="padding:8px;border-radius:8px;background:var(--panel-2);color:var(--ink);border:1px solid var(--line)">' +
          '<option value="AM" selected>AM</option><option value="PM">PM</option>' +
        '</select>' +
      '</div>' +
      '<div style="display:flex;justify-content:flex-end;gap:10px">' +
        '<button onclick="saCloseModal()" style="padding:9px 16px;border-radius:9px;border:1px solid var(--line);background:transparent;color:var(--muted);cursor:pointer">Cancel</button>' +
        '<button id="probableMtGo" style="padding:9px 18px;border-radius:9px;border:none;background:var(--blue);color:#04101f;font-weight:700;cursor:pointer">▶ Start Run</button>' +
      '</div>';
    if (typeof window.saShowModal === 'function') {
      window.saShowModal('📈 Market Session Setup — PROBABLE', html);
    } else {
      // extremely defensive fallback if saShowModal isn't available for some reason
      var mt = window.prompt('Market session time (e.g. 9:30 AM):', '9:30 AM');
      if (mt) onConfirm(mt);
      return;
    }
    var goBtn = document.getElementById('probableMtGo');
    if (goBtn) goBtn.onclick = function () {
      var h = document.getElementById('probableMtHour').value;
      var m = document.getElementById('probableMtMin').value;
      var ap = document.getElementById('probableMtAmpm').value;
      var marketTime = h + ':' + m + ' ' + ap;
      if (typeof window.saCloseModal === 'function') window.saCloseModal();
      onConfirm(marketTime);
    };
  }

  // ── shared helper: keep NMASS/NCDEX + TCS BANCS buttons disabled while
  // EITHER a full Run or either download is in progress — a Run and a
  // download must never overlap, and the two downloads share one session
  // lock (TCS/NCDEX logins), so all three actions gate both buttons.
  // .sa-tool has no built-in :disabled styling in dashboard.html's CSS
  // (unlike the tile-level Run button, which already dims/relabels itself
  // via .sa-run.running/.locked), and these two buttons also hardcode an
  // inline color/border that would otherwise mask the disabled state — so
  // this sets opacity/cursor directly and swaps the button's own text to a
  // "working" label whenever IT is the one that's busy, restoring the
  // original label once done. ──────────────────────────────────────────
  var _PROBABLE_BTN_LABEL = {
    probableNmassBtn: '📥 NMASS/NCDEX',
    probableTcsBtn:   '📥 TCS (DEPOSIT+90%)'
  };
  function _probableSetDlButtonsDisabled(disabled, activeId, busyLabel) {
    ['probableNmassBtn', 'probableTcsBtn'].forEach(function (id) {
      var b = document.getElementById(id); if (!b) return;
      b.disabled = disabled;
      b.style.opacity = disabled ? '0.55' : '';
      b.style.cursor = disabled ? 'not-allowed' : 'pointer';
      if (id === activeId) {
        b.textContent = disabled ? (busyLabel || '⏳ Working…') : _PROBABLE_BTN_LABEL[id];
      } else if (!disabled) {
        b.textContent = _PROBABLE_BTN_LABEL[id];
      }
    });
  }

  // ── Run (market-time modal → generic SSE run, mirroring _pxRunReal) ──
  function probableRunReal(marketTime) {
    window._pxRunning = true; window._pxRunningPipe = PIPE;
    _probableSetDlButtonsDisabled(true);
    var st = document.getElementById('pxStatus-' + PIPE);
    if (st) st.innerHTML = '<div class="sa-prog"><span class="sa-pill running" id="pxP-' + PIPE + '">running…</span></div><div id="pxEvents-' + PIPE + '"></div>';
    var url = API + '/px/run-stream?pipe=' + encodeURIComponent(PIPE) + '&market_time=' + encodeURIComponent(marketTime);
    var ev = new EventSource(url); _es = ev; window._pxES = ev;
    ev.onmessage = function (m) {
      var d; try { d = JSON.parse(m.data); } catch (e) { return; }
      if (typeof window.pxHandle === 'function') window.pxHandle(PIPE, d);
      if (d.type === 'done') {
        window._pxRunLog = window._pxRunLog || {};
        window._pxRunLog[PIPE] = { log: d.log || null, hasError: d.ok === false, err: d.err || '', ts: new Date().toISOString() };
        if (typeof window.pxInjectViewBtn === 'function') window.pxInjectViewBtn(PIPE);
      }
      if (d.type === 'end' || d.type === 'done') {
        ev.close(); _es = null; window._pxES = null; window._pxRunning = false; window._pxRunningPipe = null;
        _probableSetDlButtonsDisabled(false);
        if (typeof window.pxStatus === 'function') window.pxStatus(PIPE);
        var pp = document.getElementById('pxP-' + PIPE);
        if (pp) { pp.className = 'sa-pill ' + (d.ok === false ? 'error' : 'done'); pp.textContent = (d.ok === false ? 'finished with errors' : 'done'); }
        probableLoadReady();
      }
    };
    ev.onerror = function () {
      ev.close(); _es = null; window._pxES = null; window._pxRunning = false; window._pxRunningPipe = null;
      _probableSetDlButtonsDisabled(false);
      if (typeof window.pxEv === 'function') window.pxEv(PIPE, false, 'Connection lost — is the backend running?', 'stream');
    };
  }

  // ── Stop — correctly includes ?pipe=probable (generic pxStop's fallback
  // branch omits it entirely, a latent bug for any pipe past ctcl/cli/ms15) ──
  window.probableStop = function () {
    try { if (_es) { _es.close(); _es = null; } } catch (e) {}
    try { if (window._pxES) { window._pxES.close(); window._pxES = null; } } catch (e) {}
    fetch(API + '/px/stop?pipe=' + PIPE, { method: 'POST' }).catch(function () {});
    _probableSetDlButtonsDisabled(false);
    _busy = false;
    window._pxRunning = false; window._pxRunningPipe = null;
    if (typeof window.pxEv === 'function') window.pxEv(PIPE, false, 'Process terminated by user', 'stopped');
    if (typeof window.pxStatus === 'function') window.pxStatus(PIPE);
    probableCaptchaStopPoller();
  };

  // ── NMASS/NCDEX download (own strip, own tiny captcha modal) ─────────────
  // NOTE: the strip shows ONE line at a time (replaced, never appended) —
  // it's a clean status indicator, not a terminal. Raw 'log' events (every
  // "Waiting for NMASS download...", "Status: 200", library warnings, etc.
  // the underlying downloader scripts print) are intentionally NOT displayed
  // here at all — same convention CTCL vs NMASS's native download handler
  // already uses (dashboard.html's _ctclAddEv only ever shows curated
  // error/done events, never raw log noise). Full raw output still lands in
  // the 🗂️ Logs button for whoever actually needs to see it.
  window.probableNmassDownload = function (cb) {
    if (_busy || window._pxRunning) return;
    _busy = true;
    _probableSetDlButtonsDisabled(true, 'probableNmassBtn', '⏳ Downloading…');
    var strip = document.getElementById('probableNmassStrip');
    if (strip) { strip.style.display = 'block'; strip.innerHTML = '<div style="color:var(--muted)">⏳ Downloading NMASS/NCDEX… (a captcha may appear)</div>'; }
    probableCaptchaStartPoller();
    var ev = new EventSource(API + '/probable/download/nmass-stream'); _es = ev;
    var ok = false;
    ev.onmessage = function (m) {
      var d; try { d = JSON.parse(m.data); } catch (e) { return; }
      if (d.type === 'done') { ok = !!d.ok; }
      if (d.type === 'error' && strip) strip.innerHTML = '<div style="font-weight:700;color:var(--red)">✗ ' + String(d.msg || 'Download failed').replace(/[<>]/g, '') + '</div>';
      if (d.type === 'end') {
        ev.close(); _es = null; _busy = false;
        _probableSetDlButtonsDisabled(false);
        if (strip) strip.innerHTML = '<div style="font-weight:700;color:' + (ok ? 'var(--green)' : 'var(--red)') + '">' + (ok ? '✓ Done' : '✗ Failed — see 🗂️ Logs for details') + '</div>';
        probableCaptchaStopPoller();
        probableLoadReady();
        if (typeof cb === 'function') cb(ok);
      }
    };
    ev.onerror = function () { ev.close(); _es = null; _busy = false; _probableSetDlButtonsDisabled(false); probableCaptchaStopPoller(); if (typeof cb === 'function') cb(false); };
  };

  // ── TCS BANCS RSK383+335 download (own strip; RSA token popup reuses the
  // EXISTING /sa/token/pending poller already running globally in
  // dashboard.html — no extra code needed for that part) ────────────────
  window.probableTcsBancsDownload = function () {
    if (_busy || window._pxRunning) return;
    _busy = true;
    _probableSetDlButtonsDisabled(true, 'probableTcsBtn', '⏳ Logging in…');
    var strip = document.getElementById('probableTcsStrip');
    if (strip) { strip.style.display = 'block'; strip.innerHTML = '<div style="color:var(--muted)">⏳ Logging in to TCS BANCS… an RSA token prompt may appear.</div>'; }
    var ev = new EventSource(API + '/probable/download/tcs-bancs-stream'); _es = ev;
    var ok = false;
    ev.onmessage = function (m) {
      var d; try { d = JSON.parse(m.data); } catch (e) { return; }
      if (d.type === 'done') { ok = !!d.ok; }
      if (d.type === 'error' && strip) strip.innerHTML = '<div style="font-weight:700;color:var(--red)">✗ ' + String(d.msg || 'Download failed').replace(/[<>]/g, '') + '</div>';
      if (d.type === 'end') {
        ev.close(); _es = null; _busy = false; _probableSetDlButtonsDisabled(false);
        if (strip) strip.innerHTML = '<div style="font-weight:700;color:' + (ok ? 'var(--green)' : 'var(--red)') + '">' + (ok ? '✓ Done' : '✗ Failed — see 🗂️ Logs for details') + '</div>';
        probableLoadReady();
      }
    };
    ev.onerror = function () { ev.close(); _es = null; _busy = false; _probableSetDlButtonsDisabled(false); };
  };

  // ── tiny self-contained captcha modal for the NMASS/NCFE step —
  // dashboard.html's own captcha poller only checks ctcl/ms15/sa sources, so
  // this pipe needs its own small one rather than editing that file. ───────
  var _capTimer = null, _capVisible = false;
  function probableCaptchaStartPoller() {
    if (_capTimer) return;
    _capTimer = setInterval(function () {
      if (_capVisible) return;
      fetch(API + '/probable/captcha/pending').then(function (r) { return r.json(); }).then(function (d) {
        if (d.pending) probableCaptchaShow(d.image_b64, d.reason || '');
      }).catch(function () {});
    }, 2000);
  }
  function probableCaptchaStopPoller() {
    if (_capTimer) { clearInterval(_capTimer); _capTimer = null; }
    _capVisible = false;
    var m = document.getElementById('probableCaptchaModal'); if (m) m.remove();
  }
  function probableCaptchaShow(imgB64, reason) {
    _capVisible = true;
    var old = document.getElementById('probableCaptchaModal'); if (old) old.remove();
    var wrap = document.createElement('div');
    wrap.id = 'probableCaptchaModal';
    wrap.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center';
    wrap.innerHTML =
      '<div style="background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:24px;width:360px;box-shadow:var(--sh-3)">' +
        '<div style="font-weight:800;font-size:15px;margin-bottom:10px;color:var(--ink)">🔒 NMASS/NCDEX Captcha — PROBABLE</div>' +
        (reason ? '<div style="color:var(--amber);font-size:12px;margin-bottom:10px">' + String(reason).replace(/[<>]/g, '') + '</div>' : '') +
        (imgB64 ? '<img src="data:image/png;base64,' + imgB64 + '" style="width:100%;border-radius:8px;margin-bottom:12px;border:1px solid var(--line)"/>' : '<div style="color:var(--muted);font-size:12px;margin-bottom:12px">Loading image…</div>') +
        '<input id="probableCaptchaInput" autofocus style="width:100%;box-sizing:border-box;padding:10px;border-radius:9px;border:1px solid var(--line);background:var(--panel-2);color:var(--ink);margin-bottom:12px" placeholder="Type the captcha text">' +
        '<div style="display:flex;justify-content:flex-end;gap:10px">' +
          '<button id="probableCaptchaSubmit" style="padding:9px 18px;border-radius:9px;border:none;background:var(--blue);color:#04101f;font-weight:700;cursor:pointer">Submit</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(wrap);
    var input = document.getElementById('probableCaptchaInput');
    var submit = function () {
      var text = (input.value || '').trim();
      if (!text) return;
      fetch(API + '/probable/captcha/answer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: text })
      }).then(function () { _capVisible = false; wrap.remove(); }).catch(function () { _capVisible = false; wrap.remove(); });
    };
    document.getElementById('probableCaptchaSubmit').onclick = submit;
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
  }

  // ── monkey-patch: intercept the generic run-tile's onclick="pxRun('probable')"
  // to collect market-time FIRST; every other pipe's behaviour is untouched. ──
  function patchGlobals() {
    if (typeof window.pxRun === 'function' && !window.pxRun.__probablePatched) {
      var orig = window.pxRun;
      var wrapped = function (pipe) {
        if (pipe === PIPE) { if (window._pxRunning || _busy) return; showMarketTimeModal(probableRunReal); return; }
        return orig(pipe);
      };
      wrapped.__probablePatched = true;
      window.pxRun = wrapped;
    }
    // Friendlier caller label for the shared RSA token modal + lock strips —
    // dashboard.html's own _pipeBoxLabel() falls back to the raw string
    // ("probable") for any caller it doesn't recognise, which already reads
    // fine, but this makes it match the box's actual display name exactly.
    if (typeof window._pipeBoxLabel === 'function' && !window._pipeBoxLabel.__probablePatched) {
      var origLabel = window._pipeBoxLabel;
      var wrappedLabel = function (src) { return src === PIPE ? 'PROBABLE' : origLabel(src); };
      wrappedLabel.__probablePatched = true;
      window._pipeBoxLabel = wrappedLabel;
    }

    // ── fix a pre-existing dashboard.html bug (not introduced by PROBABLE,
    // but hit by every pipe past ctcl): pxUpload(pipe)'s "Upload files" modal
    // hardcodes the literal text "CTCL VS NMASS" for its main-folder title/
    // button/warning/expected-files strings NO MATTER which pipe called it —
    // so MS15, Client Prudential Limit, and PROBABLE all showed "CTCL VS
    // NMASS" in their own Upload files modal. "COMMON FILES" is untouched —
    // that label is genuinely correct for every pipe (same shared folder).
    // Since dashboard.html can't be edited, this patches pxUpload to relabel
    // the rendered modal right after dashboard.html's own version fills it in. ──
    if (typeof window.pxUpload === 'function' && !window.pxUpload.__labelPatched) {
      var origUpload = window.pxUpload;
      var wrapped2 = function (pipe) {
        var ret = origUpload(pipe);
        if (pipe !== 'ctcl') {
          var fixLabel = function () {
            var body = document.getElementById('saModalBody');
            if (!body) return;
            var label = (typeof window._pipeBoxLabel === 'function' ? window._pipeBoxLabel(pipe) : pipe).toUpperCase();
            body.innerHTML = body.innerHTML.split('CTCL VS NMASS').join(label);
            // ── PROBABLE-only: a THIRD destination, MASTER (MAIL LIST.xlsx /
            // Branch_AND_Region_LIST.xlsx — updated by hand roughly once a
            // month, per core.pipeline_box PIPELINES["probable"]["master_*"]).
            // dashboard.html's native modal only ever offers 2 buttons
            // (main/common), so it's spliced in here. ─────────────────────
            if (pipe === 'probable') {
              var row = body.querySelector('div[style*="flex-wrap:wrap"]');
              if (row && !row.querySelector('.probable-master-btn')) {
                var mbtn = document.createElement('button');
                mbtn.className = 'sa-save probable-master-btn';
                mbtn.style.flex = '1'; mbtn.style.minWidth = '170px';
                mbtn.style.background = 'var(--violet)'; mbtn.style.color = '#1a0033';
                mbtn.textContent = '📁 MASTER';
                mbtn.title = 'MAIL LIST.xlsx / Branch_AND_Region_LIST.xlsx — updated roughly once a month. Uploading here REPLACES the existing file, it never says "already present".';
                mbtn.onclick = function () { window.pxPickFiles('master'); };
                row.appendChild(mbtn);
                var hint = document.createElement('div');
                hint.style.cssText = 'font-size:10px;color:var(--dim);margin:-2px 0 12px;font-family:var(--mono)';
                hint.textContent = 'MASTER → PROBABLE_FOLDER\\MASTER (MAIL LIST.xlsx, Branch_AND_Region_LIST.xlsx — replaces existing, never skipped)';
                row.parentNode.insertBefore(hint, row.nextSibling);
              }
            }
          };
          if (ret && typeof ret.then === 'function') ret.then(fixLabel); else setTimeout(fixLabel, 300);
        }
        return ret;
      };
      wrapped2.__labelPatched = true;
      window.pxUpload = wrapped2;
    }

    // ── PROBABLE-only: intercept the upload itself when target==='master' so
    // the result wording says "replaced with new version" instead of the
    // generic pxDoUpload()'s "already in folder"/"copied" tags — dashboard.
    // html's pxDoUpload() can't be edited, and its hardcoded folder label
    // ("CTCL VS NMASS") is wrong for a master upload too, so this fully
    // takes over for that one case rather than trying to patch pieces of it. ──
    if (typeof window.pxDoUpload === 'function' && !window.pxDoUpload.__masterPatched) {
      var origDoUpload = window.pxDoUpload;
      var wrappedDoUpload = function (inp) {
        if (window._pxUpPipe === 'probable' && window._pxUpTarget === 'master') {
          return probableDoMasterUpload(inp);
        }
        return origDoUpload(inp);
      };
      wrappedDoUpload.__masterPatched = true;
      window.pxDoUpload = wrappedDoUpload;
    }

    // ── PROBABLE-only: hide the "Client data" lookup tab in Backup Files —
    // that tab looks up a client ID across CTCL/SA-style multi-run backup
    // folders, which doesn't apply to PROBABLE (its RECONCILIATION/SHORTAGE
    // backup structure has no per-client record to look up). dashboard.html
    // already has this exact precedent for CLI: `if(pipe==='cli' && tab===
    // 'client') tab='files';` inside its native pxBackup(pipe, tab) — since
    // that function can't be edited, mirror the same redirect for 'probable'. ──
    if (typeof window.pxBackup === 'function' && !window.pxBackup.__probablePatched) {
      var origBackup = window.pxBackup;
      var wrappedBackup = function (pipe, tab) {
        if (pipe === 'probable' && tab === 'client') tab = 'files';
        var ret = origBackup(pipe, tab);
        if (pipe === 'probable') {
          var hideClientTab = function () {
            var body = document.getElementById('saModalBody');
            if (!body) return;
            var btns = body.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
              var oc = btns[i].getAttribute('onclick') || '';
              if (oc.indexOf("pxBackup('probable','client')") !== -1) btns[i].style.display = 'none';
            }
          };
          if (ret && typeof ret.then === 'function') ret.then(hideClientTab); else setTimeout(hideClientTab, 0);
        }
        return ret;
      };
      wrappedBackup.__probablePatched = true;
      window.pxBackup = wrappedBackup;
    }

    // ── Expose the "FILE MISSING EMAIL" recipients (SA_DL_EMAIL_TO/CC) inside
    // the existing "✉️ Email To / CC" modal for Short Allocation. These two env
    // keys already drive the real alert email sa_codes/newshortallocationfile
    // downloader.py sends while it waits (in 5-min cycles) for an expected
    // input file — they just weren't wired into SA_KEYS_MAIL/the modal before,
    // so there was no way to see or change them without editing .env by hand.
    // Since dashboard.html's saOpenEditor() can't be edited, this wraps it:
    // let the native version build its usual sections, then splice one more
    // section in — right after "SA Processing emails" — using the SAME
    // saListFieldCollapsed()/saListUpdateSummary() helpers dashboard.html
    // already defines, so it saves through the existing saSaveConfig()
    // unchanged (it just serializes every window._saListState key). ──
    if (typeof window.saOpenEditor === 'function' && !window.saOpenEditor.__fileMissingPatched) {
      var origSaOpenEditor = window.saOpenEditor;
      var wrappedEditor = function (kind, pw) {
        var ret = origSaOpenEditor(kind, pw);
        if (kind !== 'wa' && ret && typeof ret.then === 'function') {
          ret.then(function () {
            fetch(API + '/sa/config').then(function (r) { return r.json(); }).then(function (cfg) {
              injectFileMissingEmailSection(cfg);
            }).catch(function () {});
          });
        }
        return ret;
      };
      wrappedEditor.__fileMissingPatched = true;
      window.saOpenEditor = wrappedEditor;
    }

    // ── A "waiting" event is not a success. dashboard.html's saAddEvent() only
    // has two states, ok (green ✓) or not-ok (red !), so "⏳ Waiting for exchange
    // upload — will retry in 5 min" was passed as ok=true and rendered as a green
    // tick. Nothing has succeeded at that point: the run is parked, waiting on a
    // file the exchange hasn't published, and a green tick next to it reads as
    // "done, all good" at a glance. Repainted amber with an hourglass — the same
    // colour the console already uses for "already in folder" notices.
    if (typeof window.saAddEvent === 'function' && !window.saAddEvent.__waitPatched) {
      var origAddEvent = window.saAddEvent;
      var wrappedAddEvent = function (ok, msg, what) {
        var ret = origAddEvent(ok, msg, what);
        try {
          var isWait = /^\s*⏳/.test(String(msg || '')) || /waiting|will retry/i.test(String(msg || ''));
          if (ok && isWait) {
            var box = document.getElementById('saEvents');
            var el = box && box.lastElementChild;
            if (el) {
              el.classList.remove('ok');
              el.style.background = 'rgba(251,191,36,.08)';
              el.style.border = '1px solid rgba(251,191,36,.35)';
              el.style.color = 'var(--amber)';
              var ic = el.querySelector('.ic');
              if (ic) {
                ic.textContent = '⏳';
                ic.style.background = 'transparent';
                ic.style.color = 'var(--amber)';
              }
            }
          }
        } catch (e) {}
        return ret;
      };
      wrappedAddEvent.__waitPatched = true;
      window.saAddEvent = wrappedAddEvent;
    }

    // ── "📋 View" onto the done tile (see _pxViewOnTile above). Hooked to BOTH
    // the tile renderer and the View-button injector, because either can fire
    // last: pxRenderRuns() runs on every status poll and rebuilds the tiles from
    // scratch (wiping the upgrade), while pxInjectViewBtn() fires once when a run
    // finishes and on page load from the cached log. ctcl and cli are excluded —
    // both already render their own per-run View/Retry buttons natively.
    if (typeof window.pxRenderRuns === 'function' && !window.pxRenderRuns.__viewTilePatched) {
      var origRenderRuns = window.pxRenderRuns;
      var wrappedRenderRuns = function (pipe, s) {
        var ret = origRenderRuns(pipe, s);
        try { _pxWinStartOnly(pipe); } catch (e) {}
        try { _pxFixRunIdPadding(pipe); } catch (e) {}
        if (pipe !== 'ctcl' && pipe !== 'cli') { try { _pxViewOnTile(pipe); } catch (e) {} }
        // Override tiles — runs for ALL pipes (including ctcl/cli) AFTER the
        // native View buttons, so overrides win when they apply.
        try { _pxOverrideTiles(pipe); } catch (e) {}
        return ret;
      };
      wrappedRenderRuns.__viewTilePatched = true;
      window.pxRenderRuns = wrappedRenderRuns;
    }

    if (typeof window.pxInjectViewBtn === 'function' && !window.pxInjectViewBtn.__viewTilePatched) {
      var origInjectView = window.pxInjectViewBtn;
      var wrappedInjectView = function (pipe) {
        var ret = origInjectView(pipe);
        if (pipe !== 'ctcl' && pipe !== 'cli') { try { _pxViewOnTile(pipe); } catch (e) {} }
        try { _pxOverrideTiles(pipe); } catch (e) {}
        return ret;
      };
      wrappedInjectView.__viewTilePatched = true;
      window.pxInjectViewBtn = wrappedInjectView;
    }

    // ══════════════════════════════════════════════════════════════════════
    // Two fixes to the run-status strips, both in one wrapper around the
    // dashboard's own generic pxHandle(pipe, d) event dispatcher (native to
    // dashboard.html, so it can't be edited on disk):
    //
    //  1. SAY WHICH EMAIL. pxHandle hardcoded the text 'Email sent
    //     successfully' and threw away d.msg — so a Margin Shortage run that
    //     sends a main summary plus up to four zone files produced several
    //     identical green strips with no way to tell which zone had gone out,
    //     or on a partial failure which one hadn't. The backend already put
    //     the full printed line in d.msg; the pipelines now print a
    //     "— <label> → <recipients>" detail after the anchor phrase, and this
    //     surfaces it.
    //
    //  2. DON'T LEAVE THEM UP FOREVER. Once a run is done the strips are
    //     history, but they sat on screen until the next run or a reload,
    //     pushing the run tiles off the top of the box. One minute after
    //     'done' they collapse to a single summary line, and five minutes
    //     after that it clears itself too. Any new event cancels both timers,
    //     so a fresh run always starts from a clean, fully-visible list.
    // ══════════════════════════════════════════════════════════════════════
    if (typeof window.pxHandle === 'function' && !window.pxHandle.__evtPatched) {
      var origPxHandle = window.pxHandle;
      var wrappedPxHandle = function (pipe, d) {
        _pxEvtCancelTimers(pipe);
        var detail = _pxEvtDetail(d && d.msg);
        if (d && (d.type === 'email' || d.type === 'whatsapp') && detail && typeof window.pxEv === 'function') {
          var base = d.type === 'email'
            ? (d.ok ? 'Email sent' : 'Email FAILED')
            : (d.ok ? 'WhatsApp submitted' : 'WhatsApp FAILED');
          window.pxEv(pipe, d.ok, base + ' — ' + detail, d.type);
        } else {
          origPxHandle(pipe, d);
        }
        if (d && d.type === 'done') _pxEvtScheduleCollapse(pipe, d.ok !== false);
      };
      wrappedPxHandle.__evtPatched = true;
      window.pxHandle = wrappedPxHandle;
    }

    // ── Cap MS15's shortage-image WhatsApp list at 10 numbers. saListToggleAdd
    // is the native "+" handler behind every collapsed list field (SA's own
    // zone lists have no such limit), so this wraps it and only intervenes for
    // MS15_SHORTAGE_WA_TO specifically — every other field's "+" is untouched.
    if (typeof window.saListToggleAdd === 'function' && !window.saListToggleAdd.__ms15CapPatched) {
      var origToggleAdd = window.saListToggleAdd;
      var wrappedToggleAdd = function (key, placeholder) {
        if (key === MS15_WA_KEY) {
          var cur = (window._saListState && window._saListState[key]) || [];
          var alreadyOpen = typeof window.saListIsOpen === 'function' ? window.saListIsOpen(key) : true;
          // First click just reveals the list — never block that. Only a
          // second+ click (which appends a new blank row) is capped.
          if (alreadyOpen && cur.length >= MS15_WA_MAX) {
            alert('Maximum ' + MS15_WA_MAX + ' numbers allowed for the Margin Shortage image list.');
            return;
          }
        }
        return origToggleAdd(key, placeholder);
      };
      wrappedToggleAdd.__ms15CapPatched = true;
      window.saListToggleAdd = wrappedToggleAdd;
    }
  }

  function injectFileMissingEmailSection(cfg) {
    var body = document.getElementById('saModalBody');
    if (!body || !cfg || !cfg.mail || body.querySelector('.fme-section')) return;
    if (typeof window.saListFieldCollapsed !== 'function') return;
    if (!window._saListState) window._saListState = {};
    window._saListState['SA_DL_EMAIL_TO'] = (cfg.mail.SA_DL_EMAIL_TO || []).slice();
    window._saListState['SA_DL_EMAIL_CC'] = (cfg.mail.SA_DL_EMAIL_CC || []).slice();
    var hdr = '<div class="fme-section" style="font-size:10px;font-weight:700;letter-spacing:.08em;color:var(--teal);text-transform:uppercase;padding:10px 0 6px;border-top:1px solid var(--line);margin-top:4px">FILE MISSING EMAIL</div>';
    var html = hdr +
      window.saListFieldCollapsed('SA_DL_EMAIL_TO', 'FILE MISSING · TO', 'name@mastertrust.co.in', 'email') +
      window.saListFieldCollapsed('SA_DL_EMAIL_CC', 'FILE MISSING · CC', 'name@mastertrust.co.in', 'email');
    var wrap = document.createElement('div');
    wrap.innerHTML = html;
    // Land it right after "SA Processing emails" (before "Scenario & Report
    // emails (LAS step)") so it's grouped with the SA-side emails, not LAS —
    // falls back to just before the Save button if that section isn't found.
    var divs = body.querySelectorAll('div');
    var target = null;
    for (var i = 0; i < divs.length; i++) {
      if (divs[i].textContent.trim() === 'Scenario & Report emails (LAS step)') { target = divs[i]; break; }
    }
    if (!target) target = body.querySelector('button.sa-save');
    var frag = Array.prototype.slice.call(wrap.childNodes);
    frag.forEach(function (node) {
      if (target && target.parentNode === body) body.insertBefore(node, target);
      else body.appendChild(node);
    });
    if (typeof window.saListUpdateSummary === 'function') {
      window.saListUpdateSummary('SA_DL_EMAIL_TO');
      window.saListUpdateSummary('SA_DL_EMAIL_CC');
    }
  }

  function wireButtons() { /* all onclick handlers are inline in buildBox()'s HTML */ }

  // ── MASTER upload (MAIL LIST.xlsx / Branch_AND_Region_LIST.xlsx) — always
  // REPLACES, and says so, instead of dashboard.html's generic "already in
  // folder" skip wording (which is correct for daily input files but wrong
  // for a reference file that's meant to be replaced whenever it changes). ──
  function probableDoMasterUpload(inp) {
    if (!inp.files.length) return;
    var fileList = Array.prototype.slice.call(inp.files);   // captured now — a
                                                              // password retry needs these same files again
    inp.value = '';
    probableDoMasterUploadFiles(fileList, '');
  }

  function probableDoMasterUploadFiles(fileList, password) {
    var pipe = window._pxUpPipe || PIPE;
    window.saShowModal('Uploading…', '<div style="color:var(--muted);font-size:13px">Copying ' + fileList.length + ' file(s) into the MASTER folder…</div>');
    var fd = new FormData();
    for (var i = 0; i < fileList.length; i++) fd.append('files', fileList[i]);
    fd.append('password', password || '');
    var ctl = new AbortController();
    window._saUploadCtl = ctl;
    var to = setTimeout(function () { ctl.abort(); }, 180000);
    fetch(API + '/px/upload?pipe=' + encodeURIComponent(pipe) + '&target=master', { method: 'POST', body: fd, signal: ctl.signal })
      .then(function (r) {
        clearTimeout(to);
        if (ctl.signal.aborted) return null;
        if (!r.ok) throw new Error('Server ' + r.status);
        return r.json();
      })
      .then(function (d) {
        if (!d) return;
        if ((d.password_required || []).length) {
          window.saAskPassword('Replace protected file — ' + d.password_required.join(', '), function (pw) {
            probableDoMasterUploadFiles(fileList, pw);
          });
          return;
        }
        var html = '<div style="font-size:13px;color:var(--muted);margin-bottom:10px">Folder: <span style="font-family:var(--mono);color:var(--ink)">' + (d.dest || '') + '</span></div>';
        (d.ok || []).forEach(function (m) {
          html += '<div class="sa-ev ok"><span class="ic">✓</span><span>' + m + '</span><span class="what">replaced with new version</span></div>';
        });
        (d.rejected || []).forEach(function (m) {
          html += '<div class="sa-ev bad"><span class="ic">✕</span><span>' + m + '</span><span class="what">not recognised</span></div>';
        });
        if ((d.rejected || []).length) {
          html += '<div style="margin-top:12px;padding:13px;border:1px dashed var(--line-2);border-radius:10px;font-size:12px;color:var(--muted)"><b style="color:var(--ink)">Valid MASTER files:</b><br><br>' +
            (d.valid || []).map(function (v) { return '<span style="font-family:var(--mono);color:var(--teal)">' + v + '</span>'; }).join('<br>') + '</div>';
        }
        html += '<button class="sa-save" style="width:100%;margin-top:14px" onclick="saCloseModal()">Done</button>';
        window.saShowModal((d.rejected || []).length ? 'Upload result' : '✓ Upload complete', html);
      })
      .catch(function (e) {
        clearTimeout(to);
        if (ctl.signal.aborted) return;
        var msg = (e && e.name === 'AbortError') ? 'Timed out — is the backend running?' : ((e && e.message) ? e.message : String(e));
        window.saShowModal('Upload failed', '<div style="color:var(--red)">' + msg + '</div>');
      })
      .finally(function () { window._saUploadCtl = null; });
  }
})();
