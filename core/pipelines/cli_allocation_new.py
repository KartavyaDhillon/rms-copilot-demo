"""
core/pipelines/cli_allocation_new.py — Client Prudential Limit's ALLOCATION DETAIL
step for the NEW pipeline. This is the user's uploaded "new" script
(ALLOCATION_CLI_NMASS_NCDEX-2841612e.py — explicitly described as "the new one is
this") ported in full: a pre-file check plus an updated CLI_PRUDENTIAL_LIMIT.txt
formula that layers RISK_REPORT + the Kambala deposit file on top of the base
allocation sum. This is Step 1 of Run NEW — Download / MCX Web Allocation / Upload
after this are unchanged from OLD (the user only described a new Allocation step),
so Run NEW reuses cli_download.py / cli_mcx_download.py / cli_upload.py as-is.

  STEP 0 — pre-file check (precheck_required_inputs() in the original script):
           today's RISK_REPORT_<DDMMYYYY>.csv and
           Kambala_Omny_Depo_Adhoc_All_level.txt_<DDMMYYYY>.txt MUST both be present
           before anything else runs — this stops the whole Run NEW chain before
           even the Allocation Detail download, exactly like the original script.
           RISK_REPORT: checked in the CLI base folder first, then copied in from
           the shared COMMON FILES folder if it's sitting there instead (the
           dashboard's "Common files" upload button places it there).
           Kambala: fetched straight from FTP (KAMB/DEPO folder) if not already
           local — see core.pipeline_box._pb_download_kambala_from_ftp(), the
           single-file extraction from the user's ftpfiles_newfinal.py.
           If either is still missing after that, Run NEW stops here.
  STEP 1 — Allocation Detail fetch (local-first, FTP fallback) — identical to OLD's
           Step 1, shared via core.pipeline_box._pb_fetch_allocation_detail().
  STEP 2 — processing (run_processing() [UPDATED] in the original script):
             1) base allocation: group by AccountID, sum collateral columns, drop
                zero-sum codes (same columns/math as OLD)
             2) RISK_REPORT: accounts where MTF_STATUS == 'Y' (matched on ACCOUNTID)
             3) Kambala TXT: '|'-delimited, header row skipped, col 0 = AccountID,
                col 25 (Excel column Z) = value; abs(that value) computed per code
             4) abs(Z) is added ONLY to accounts that are both in the base CLI file
                AND in the 'Y' set from RISK_REPORT
           writes Intermediate.csv + CLI_PRUDENTIAL_LIMIT.txt (same filenames as
           OLD — Run NEW overwrites the same output files OLD would have produced).

This is Step 1 of the "Run NEW" chain (core/pipelines/cli_allocation_new.py ->
cli_download.py -> cli_mcx_download.py -> cli_upload.py), called by api.py's
/cli/run-new-stream. Not exposed as its own dashboard button — it's an internal
first step of Run NEW.

Tags printed for core.pipeline_box._status():
  [MISSING_FILE] <name>   — a required pre-check file is absent for today
  [FRIENDLY_MSG] <text>   — plain-English AI fallback for an unrecognized failure

═══════════════════════════════════════════════════════════════════════════
THREE RUN WINDOWS (Run NEW only — Run OLD / core/pipelines/cli_allocation.py
is NEVER touched by any of this, at any time of day, per explicit
instruction: "we are not touching the old run here at all all these changes
should be done inside the new run").

Client Prudential Limit now runs THREE times a day, and which window you are
in decides what Run NEW actually does:

  MORNING    07:00-10:00   FULL run. Pre-check (RISK_REPORT + Kambala),
                           fetch a fresh Allocation_Detail, BUILD
                           CLI_PRUDENTIAL_LIMIT.txt + Intermediate.csv,
                           upload to the plain ALLOCATION folder.
  AFTERNOON  15:30-17:15   Collateral files ONLY (NSCCL/NCDEX + MCX).
                           Upload to ALLOCATION(AFTERNOON).
  EVENING    19:00-21:30   Same as afternoon, but ALLOCATION(EVENING).

  Each window is overridable per-env without touching code — see
  CLI_RUN1_WINDOW / CLI_RUN2_WINDOW / CLI_RUN3_WINDOW below (_WINDOWS).

ALLOCATION_DETAIL IS MORNING-ONLY, END TO END
─────────────────────────────────────────────
The afternoon and evening runs do not download it, do not read it, and do not
upload it anywhere. It exists purely as the input to CLI_PRUDENTIAL_LIMIT.txt,
and that is a once-a-day morning number — so on the later two slots there is
nothing for it to feed and no reason to move it around.

Consequently THIS step does nothing on the later slots beyond writing the
upload-target marker. The Run NEW chain then continues to cli_download.py
(NSCCL/NCDEX) -> cli_mcx_download.py -> cli_upload.py as normal.

THE FOLDER CHAIN, IN ORDER
──────────────────────────
  morning    ALLOCATION             ← Allocation_Detail fetched here, and the
                                      morning's outputs uploaded here
  afternoon  ALLOCATION(AFTERNOON)  ← this slot's collateral + MCX files only
  evening    ALLOCATION(EVENING)    ← same, for the evening slot

Each folder is independent — nothing is copied from one into the next, and
there is no archive rename of ALLOCATION -> ALLOCATION(MORNING) any more.

WINDOWS ARE STRICT
──────────────────
_current_window() returns "morning" / "afternoon" / "evening", or None if the
clock is outside all three (e.g. 12:00 noon, or 22:30). Outside a window the
run REFUSES and exits non-zero rather than guessing — a mistimed run that
silently picked the "nearest" window would either overwrite the day's
prudential-limit number or upload into the wrong slot folder, and neither is
visible until someone goes looking on the FTP server.

All three windows are read from env vars (CLI_RUN1_WINDOW / CLI_RUN2_WINDOW /
CLI_RUN3_WINDOW) rather than hardcoded here, so the code can never drift out
of sync with the "opens HH:MM" text the operator already sees on the CLI
tiles.

A marker file (.cli_slot_upload) tells cli_upload.py (which runs later in the
same Run NEW chain) which folder to upload into, and to not flag
CLI_PRUDENTIAL_LIMIT.txt as a [MISSING_FILE] — it is SUPPOSED to be absent on
any non-morning run.

Per explicit instruction, nothing pre-creates a fresh empty ALLOCATION
folder after the rename — cli_allocation.py's / _pb_fetch_allocation_detail's
existing "create the folder if it's missing" fallback already covers
tomorrow morning's Run 1 without needing anything done here tonight.
═══════════════════════════════════════════════════════════════════════════
"""
import datetime
import os
import shutil

from core.error_translator import translate_error

_ALLOC_REMOTE_BASE = "/TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION"
# Written by a non-morning run, read (and deleted) by cli_upload.py. Named
# generically — it used to be ".cli_evening_upload" back when evening was the
# only special slot. BOTH names are handled everywhere: the old one is still
# accepted on read (so a marker left by a pre-upgrade run is not silently
# ignored) and still cleared on write (so a stale one can never redirect a
# later upload into the wrong folder).
_SLOT_MARKER = ".cli_slot_upload"
_ALL_MARKERS = (".cli_slot_upload", ".cli_evening_upload")

# slot -> (env var, default window, FTP folder suffix). Order matters only for
# the human-readable error message when the clock is outside all of them.
_WINDOWS = [
    ("morning",   "CLI_RUN1_WINDOW", "07:00-10:00", ""),
    ("afternoon", "CLI_RUN2_WINDOW", "15:30-17:15", "(AFTERNOON)"),
    ("evening",   "CLI_RUN3_WINDOW", "19:00-21:30", "(EVENING)"),
]


def _parse_window(raw, fallback):
    """'15:30-17:00' -> (datetime.time(15,30), datetime.time(17,0)).

    Falls back to the hardcoded default rather than raising: a typo in .env
    must not take the whole pipeline down, and the default is the documented
    window anyway. Returns (start, end)."""
    def _t(text, default_h, default_m):
        try:
            h, m = (int(x) for x in text.strip().split(":", 1))
            return datetime.time(h, m)
        except Exception:
            return datetime.time(default_h, default_m)

    fb_start, _, fb_end = fallback.partition("-")
    raw = (raw or "").strip() or fallback
    start_s, _, end_s = raw.partition("-")
    fh, fm = (int(x) for x in fb_start.split(":"))
    eh, em = (int(x) for x in fb_end.split(":"))
    return _t(start_s, fh, fm), _t(end_s, eh, em)


def _current_window(now=None):
    """Which run window are we in RIGHT NOW? Returns (slot, folder_suffix),
    or (None, None) if the clock is outside all three.

    Deliberately strict — see the module docstring. `now` is injectable so
    this can be unit-tested at any hour without touching the system clock."""
    t = (now or datetime.datetime.now()).time()
    for slot, env_key, default, suffix in _WINDOWS:
        start, end = _parse_window(os.getenv(env_key), default)
        if start <= t <= end:
            return slot, suffix
    return None, None


def _windows_description():
    """Human-readable 'morning 07:30-10:00, afternoon 15:30-17:00, ...' using
    the CURRENT env values — so the refusal message always names the windows
    that were actually checked, not the defaults."""
    parts = []
    for slot, env_key, default, _suffix in _WINDOWS:
        start, end = _parse_window(os.getenv(env_key), default)
        parts.append(f"{slot} {start.strftime('%H:%M')}-{end.strftime('%H:%M')}")
    return ", ".join(parts)


def _resolve_slot_allocation(slot, suffix, base_folder, today_str, _p):
    """⚠️ DEAD CODE — NOT CALLED ANY MORE. Kept only for reference.

    Allocation_Detail is now morning-only end to end: the afternoon and
    evening runs never download it, read it or upload it, so there is no
    copy-forward chain between slot folders and no archive rename. This
    function implemented that old chain.

    Original docstring follows.

    Non-morning (afternoon / evening) Run NEW allocation resolution — see
    the module docstring's folder-chain section. Returns the local
    Allocation_Detail path. Raises on failure (caller wraps this in the same
    try/except pattern every other step here uses).

    The two non-morning slots differ in exactly two ways, both handled here:
      - WHERE they read from. Afternoon reads the still-plain ALLOCATION;
        evening reads ALLOCATION(AFTERNOON), because by evening the plain
        folder has already been renamed away and the afternoon copy is the
        most recent one.
      - WHETHER they archive. Only afternoon renames ALLOCATION →
        ALLOCATION(MORNING). Evening never renames anything — the archiving
        was already done hours earlier.
    """
    from core.pipeline_box import (_pb_ftp_folder_exists, _pb_fetch_file_from_ftp_folder,
                                    _pb_push_file_to_ftp_folder, _pb_ftp_rename_folder)
    alloc_filename = f"Allocation_Detail_{today_str}.csv"
    alloc_path = os.path.join(base_folder, alloc_filename)

    target_folder = _ALLOC_REMOTE_BASE + suffix
    archived_folder = _ALLOC_REMOTE_BASE + "(MORNING)"
    if slot == "afternoon":
        source_folder = _ALLOC_REMOTE_BASE                       # still plain at this hour
        do_archive = True
    else:                                                         # evening
        source_folder = _ALLOC_REMOTE_BASE + "(AFTERNOON)"
        do_archive = False

    # Already set up by an earlier click in this same slot today — nothing to
    # create, nothing to archive, just read the file back.
    if _pb_ftp_folder_exists(target_folder):
        _p(f"✅ {target_folder} already exists — not the first Run NEW this {slot}.")
        if os.path.exists(alloc_path):
            _p(f"⏭ Using existing local file: {alloc_filename} — skipping download")
        else:
            _p(f"⬇️  Fetching {alloc_filename} from {target_folder}")
            _pb_fetch_file_from_ftp_folder(target_folder, alloc_path, alloc_filename)
            _p(f"✅ Downloaded: {alloc_filename}")
        return alloc_path

    _p(f"🕒 First Run NEW this {slot} — {target_folder} doesn't exist yet. Setting it up.")

    if os.path.exists(alloc_path):
        _p(f"⏭ Using existing local file: {alloc_filename} (already on disk)")
    else:
        _p(f"⬇️  Fetching {alloc_filename} from {source_folder}")
        try:
            _pb_fetch_file_from_ftp_folder(source_folder, alloc_path, alloc_filename)
            _p(f"✅ Downloaded: {alloc_filename}")
        except Exception as _fe:
            # Evening's expected source is ALLOCATION(AFTERNOON) — if the
            # afternoon run never happened today, that folder simply is not
            # there. Say so plainly instead of surfacing a bare FTP error,
            # and fall back to the archived morning folder so the evening
            # upload is still possible rather than dead in the water.
            if slot == "evening":
                _p(f"⚠️ Could not read from {source_folder} ({_fe}) — this usually means "
                   f"today's AFTERNOON run never ran. Falling back to {archived_folder}.")
                _pb_fetch_file_from_ftp_folder(archived_folder, alloc_path, alloc_filename)
                _p(f"✅ Downloaded from the archived morning folder: {alloc_filename}")
            else:
                raise

    _p(f"📋 Copying {alloc_filename} into a new {target_folder} "
       f"(the copy in {source_folder} is left exactly as it is)")
    _pb_push_file_to_ftp_folder(target_folder, alloc_path, alloc_filename)
    _p(f"✅ {target_folder} created with today's Allocation Detail")

    if do_archive:
        # Only ever runs on the FIRST afternoon click of the day. The copy
        # above has already succeeded by this point, so even if the rename
        # fails the file this run needs is safe.
        _p(f"🏷️  Archiving this morning's folder: {_ALLOC_REMOTE_BASE} → {archived_folder} — "
           f"everything Run 1 uploaded there (CLI_PRUDENTIAL_LIMIT.txt, MCX file, "
           f"collaterals) stays exactly as it is, just under the archived name")
        try:
            _pb_ftp_rename_folder(_ALLOC_REMOTE_BASE, archived_folder)
            _p(f"✅ Renamed to {archived_folder}")
        except Exception as _re:
            _p(f"⚠️ Could not rename {_ALLOC_REMOTE_BASE} → {archived_folder} (non-fatal — "
               f"{target_folder} is already correctly set up; only the archive-rename "
               f"failed, safe to fix by hand on the FTP server): {_re}")
    else:
        _p(f"ℹ️  No folder rename this {slot} — the morning folder was already archived "
           f"by today's afternoon run.")

    return alloc_path


def run_cli_allocation_new():
    base_folder = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    common_dir = os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    os.makedirs(base_folder, exist_ok=True)
    today_str = datetime.datetime.now().strftime("%d%m%Y")
    _log_buffer = []

    def _p(msg):
        print(msg, flush=True)
        _log_buffer.append(str(msg))

    # ── WHICH WINDOW ARE WE IN? ──────────────────────────────────────────────
    # Strict by design — outside all three windows this refuses rather than
    # guessing. See the module docstring.
    slot, suffix = _current_window()
    if slot is None:
        _now = datetime.datetime.now().strftime("%H:%M")
        _p(f"⛔ It is {_now} — outside every Client Prudential Limit run window.")
        _p(f"   Valid windows: {_windows_description()}")
        _p("   Nothing has been downloaded, built or uploaded. Run again inside a window.")
        _p(f"[FRIENDLY_MSG] Client Prudential Limit can only run inside its scheduled "
           f"windows ({_windows_description()}). It is currently {_now}, so the run was "
           f"stopped before doing anything.")
        raise SystemExit(1)

    # ── AFTERNOON / EVENING — nothing to do in THIS step. Everything below
    # this block is the pre-existing morning behaviour, unchanged. The later
    # slots go straight on to cli_download.py (NSCCL/NCDEX) → cli_mcx_download
    # → cli_upload in the Run NEW chain. ─────────────────────────────────────
    if slot != "morning":
        _p(f"🕒 {slot.upper()} run detected — Run NEW's {slot} flow: collateral files "
           f"only. Allocation Detail is NOT downloaded, read or uploaded, and "
           f"CLI_PRUDENTIAL_LIMIT.txt is not rebuilt. Upload target: ALLOCATION{suffix}.")
        # NOTE: _resolve_slot_allocation() is deliberately NOT called any more.
        # It used to fetch this morning's Allocation_Detail and copy it into
        # ALLOCATION(AFTERNOON)/(EVENING), archiving the morning folder on the
        # way. Allocation_Detail is now morning-only end to end — it feeds
        # nothing on these slots — so there is nothing to copy forward.
        marker_path = os.path.join(base_folder, _SLOT_MARKER)
        try:
            with open(marker_path, "w", encoding="utf-8") as _mf:
                _mf.write(_ALLOC_REMOTE_BASE + suffix)
        except Exception as _we:
            _p(f"⚠️ Could not write the {slot} marker file (non-fatal, but the upload step "
               f"may not know to target ALLOCATION{suffix}): {_we}")
        _p(f"⏭ Skipping Allocation Detail, CLI_PRUDENTIAL_LIMIT.txt and Intermediate.csv "
           f"— all three are morning-only. This slot uploads collateral + MCX files only.")
        _p(f"✅ Allocation step (NEW, {slot}) finished — nothing to do")
        return

    _p(f"🌅 MORNING run detected — full flow: pre-check, fresh Allocation Detail, "
       f"rebuild CLI_PRUDENTIAL_LIMIT.txt, upload to {_ALLOC_REMOTE_BASE}.")

    # Clear any leftover slot marker before doing anything else. Normally
    # cli_upload.py deletes the marker as it reads it, so there is nothing
    # here — but if yesterday's evening chain died BETWEEN the allocation step
    # and the upload step, the marker survives, and this morning's upload would
    # then silently go to ALLOCATION(EVENING) instead of ALLOCATION. That is
    # the single worst outcome available to this pipeline: the day's real
    # prudential-limit file uploaded to a folder nobody reads.
    for _stale in _ALL_MARKERS:
        _stale_path = os.path.join(base_folder, _stale)
        if os.path.exists(_stale_path):
            try:
                os.remove(_stale_path)
                _p(f"🧹 Cleared a leftover slot marker ({_stale}) from a previous run — "
                   f"this morning's upload goes to {_ALLOC_REMOTE_BASE}, as it should.")
            except OSError as _oe:
                _p(f"⚠️ Could not remove stale marker {_stale} ({_oe}) — check that "
                   f"today's upload lands in {_ALLOC_REMOTE_BASE}, not a slot folder.")

    # ── STEP 0: pre-file check — RISK_REPORT + Kambala must exist for TODAY ───
    _p("🚀 STEP 0: Pre-file check (RISK_REPORT + Kambala)")
    risk_name = f"RISK_REPORT_{today_str}.csv"
    kambala_name = f"Kambala_Omny_Depo_Adhoc_All_level.txt_{today_str}.txt"
    risk_path = os.path.join(base_folder, risk_name)
    kambala_path = os.path.join(base_folder, kambala_name)

    # RISK_REPORT is sourced from COMMON FILES ONLY — never trusted from
    # whatever might already be sitting in the CLI base folder (that could be
    # a stale leftover from a previous day). Every morning run pulls a fresh
    # copy in from common_dir, overwriting anything already at risk_path.
    common_risk_path = os.path.join(common_dir, risk_name)
    if os.path.exists(common_risk_path):
        try:
            shutil.copy2(common_risk_path, risk_path)
            _p(f"✅ Found: {risk_name} (copied fresh from COMMON FILES)")
        except Exception as _ce:
            _p(f"⚠️ Found {risk_name} in COMMON FILES but couldn't copy it in: {_ce}")
    else:
        _p(f"⚠️  {risk_name} not found in COMMON FILES ({common_dir})")

    if not os.path.exists(kambala_path):
        _p(f"ℹ️  {kambala_name} not found locally — checking FTP (KAMB/DEPO)")
        try:
            from core.pipeline_box import _pb_download_kambala_from_ftp
            if _pb_download_kambala_from_ftp(kambala_path, kambala_name):
                _p(f"✅ Downloaded from FTP: {kambala_name}")
            else:
                _p(f"⚠️  {kambala_name} not on FTP yet either — place it manually or wait for it to land")
        except Exception as _kfe:
            _p(f"⚠️  Kambala FTP check failed (non-fatal, will fall back to manual): {_kfe}")

    missing_pre = []
    if os.path.exists(risk_path):
        _p(f"✅ Found: {risk_name}")
    else:
        _p(f"❌ Missing: {risk_name}")
        missing_pre.append(risk_name)
    if os.path.exists(kambala_path):
        _p(f"✅ Found: {kambala_name}")
    else:
        _p(f"❌ Missing: {kambala_name}")
        missing_pre.append(kambala_name)

    if missing_pre:
        for m in missing_pre:
            _p(f"[MISSING_FILE] {m}")
        _p(f"🔴 Required file(s) not present — stopping before Allocation Detail. "
           f"Place them in {base_folder} (RISK_REPORT can also be uploaded via the "
           f"COMMON FILES button) and run again.")
        _p(f"[FRIENDLY_MSG] Can't start Run NEW — {', '.join(missing_pre)} missing for "
           f"today ({today_str}). Upload them first, then run again.")
        raise SystemExit(1)
    _p("🟢 All required pre-check files present — continuing")

    # ── STEP 1: FTP download of Allocation_Detail (local-first, shared w/ OLD) ─
    _p("🚀 STEP 1: Allocation Detail (local-first, FTP fallback)")
    try:
        from core.pipeline_box import _pb_fetch_allocation_detail
        alloc_path = _pb_fetch_allocation_detail(base_folder, today_str, _p)
    except Exception as e:
        _p(f"❌ Allocation Detail step failed: {e}")
        _ai = translate_error("Client Prudential Limit (NEW) — Allocation Detail", _log_buffer)
        _p(f"[FRIENDLY_MSG] {_ai or ('Allocation Detail step failed: ' + str(e))}")
        raise SystemExit(1)

    # ── STEP 2: processing — base allocation + RISK_REPORT + Kambala ─────────
    _p("🚀 STEP 2: Building CLI_PRUDENTIAL_LIMIT.txt (base + RISK_REPORT + Kambala)")
    try:
        import pandas as pd

        # 1) base allocation processing
        df = pd.read_csv(alloc_path, dtype={"AccountID": str})
        df.columns = df.columns.str.strip()
        cols = ["CMCreditObligation", "StockDiff", "EpiCredit", "MtfCashColl",
                "ExchRepledgeAdj", "FnoDebitBill", "EPNLimit", "HoldCashMgn", "MTFExcess"]
        for col in cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        df["AccountID"] = df["AccountID"].astype(str).str.strip()
        df_grouped = df.groupby("AccountID")[cols].sum().reset_index()
        df_grouped["Sum"] = df_grouped[cols].sum(axis=1)
        df_final = df_grouped[["AccountID", "Sum"]].copy()
        df_final = df_final[df_final["Sum"] != 0].copy()

        # 2) RISK_REPORT -> accounts where MTF_STATUS == 'Y' (match col ACCOUNTID)
        y_accounts = set()
        if os.path.exists(risk_path):
            rdf = pd.read_csv(risk_path, dtype=str)
            rdf.columns = rdf.columns.str.strip()
            rdf["ACCOUNTID"] = rdf["ACCOUNTID"].astype(str).str.strip()
            rdf["MTF_STATUS"] = rdf["MTF_STATUS"].astype(str).str.strip().str.upper()
            y_accounts = set(rdf.loc[rdf["MTF_STATUS"] == "Y", "ACCOUNTID"])
            _p(f"✅ Risk report loaded — {len(y_accounts)} codes with MTF_STATUS = Y")
        else:
            _p(f"⚠️ Risk report not found at processing time: {risk_path} — skipping MTF addition")

        # 3) Kambala TXT -> abs(Z column, index 25) per code, '|' delimited, header skipped
        kambala_z = {}
        Z_INDEX = 25
        if os.path.exists(kambala_path):
            with open(kambala_path, "r", encoding="utf-8", errors="ignore") as kf:
                lines = kf.read().splitlines()
            for line in lines[1:]:
                if not line.strip():
                    continue
                fields = line.split("|")
                if len(fields) <= Z_INDEX:
                    continue
                acc = fields[0].strip()
                raw = fields[Z_INDEX].strip()
                try:
                    zval = abs(float(raw)) if raw != "" else 0.0
                except ValueError:
                    zval = 0.0
                kambala_z[acc] = zval
            _p(f"✅ Kambala file loaded — {len(kambala_z)} codes (abs Z column)")
        else:
            _p(f"⚠️ Kambala file not found at processing time: {kambala_path} — skipping MTF addition")

        # 4) add abs(Z) only to 'Y' codes that are already in the CLI file
        added_count, added_total = 0, 0.0

        def _adjust(row):
            nonlocal added_count, added_total
            acc = row["AccountID"]
            base = row["Sum"]
            if acc in y_accounts and acc in kambala_z:
                z = kambala_z[acc]
                if z != 0:
                    added_count += 1
                    added_total += z
                return base + z
            return base

        df_final["Sum"] = df_final.apply(_adjust, axis=1)
        _p(f"✅ Z-value added to {added_count} 'Y' codes (total added = {round(added_total, 2)})")
        df_final["Sum"] = df_final["Sum"].round(2)

        # 5) save
        df_final.to_csv(os.path.join(base_folder, "Intermediate.csv"), index=False)

        # 🛑 NO NEGATIVE LIMITS — final gate before the file is written.
        # Mirrors the identical clamp in the manual script
        # (CLI_PRUDENTIAL_LIMIT\codes\WORKING\ALLOCATION_CLI_NMASS_NCDEX.py) so
        # the console and the manual run can never produce different files.
        #
        # A negative prudential limit is meaningless, so anything below 0 is
        # raised to 0 at the last point before the values become the .txt that
        # gets uploaded. Formatting is unchanged: the values were already
        # rounded to 2dp above, and clamping preserves that exactly — a clamped
        # -0.02 is written as 0.0, same style as every other value.
        #
        # Intermediate.csv above is deliberately left UNCLAMPED: it is the
        # working file, and the true computed value is what lets anyone work
        # out afterwards WHY a code went negative.
        df_out = df_final.copy()
        _neg = df_out[df_out["Sum"] < 0]
        if not _neg.empty:
            _p(f"🛑 {len(_neg)} negative value(s) clamped to 0 in CLI_PRUDENTIAL_LIMIT.txt:")
            for _, _nr in _neg.iterrows():
                _p(f"   {_nr['AccountID']}: {_nr['Sum']} → 0.0")
            _p("   (Intermediate.csv still holds the original computed values)")
        else:
            _p("✅ No negative values found — nothing needed clamping")
        # The trailing "+ 0.0" is NOT decorative. A tiny negative like -0.001
        # rounds to NEGATIVE ZERO, which is not < 0 and so slips straight
        # through clip() — but Python writes it as the string "-0.0", putting a
        # minus sign in the uploaded file anyway. Adding 0.0 turns -0.0 into
        # 0.0 (IEEE-754) and leaves every other value untouched.
        df_out["Sum"] = df_out["Sum"].clip(lower=0).round(2) + 0.0

        with open(os.path.join(base_folder, "CLI_PRUDENTIAL_LIMIT.txt"), "w") as f:
            f.write("ClientPrudentialLimit\n")
            for _, row in df_out.iterrows():
                f.write(f"{row['AccountID']},{row['Sum']}\n")
        _p("✅ Processing done — Intermediate.csv + CLI_PRUDENTIAL_LIMIT.txt written")
    except Exception as e:
        _p(f"❌ Processing failed: {e}")
        _ai = translate_error("Client Prudential Limit (NEW) — Allocation Detail processing", _log_buffer)
        _p(f"[FRIENDLY_MSG] {_ai or ('Processing failed: ' + str(e))}")
        raise SystemExit(1)

    _p("✅ Allocation Detail (NEW) step finished")


if __name__ == "__main__":
    run_cli_allocation_new()
