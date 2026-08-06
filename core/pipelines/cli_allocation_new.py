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
EVENING RUN (Run NEW only — Run OLD / core/pipelines/cli_allocation.py is
NEVER touched by any of this, at any time of day, per explicit instruction:
"we are not touching the old run here at all all these changes should be
done inside the new run").

CLI has two daily run windows (CLI_RUN1_WINDOW=08:00-09:00,
CLI_RUN2_WINDOW=19:00-20:00 by default), but until now the pipeline code
itself had NO idea which window it was being run under — Run NEW behaved
identically at 08:30 and at 19:30. The evening slot needs different
behaviour:
  - it must NOT re-download a fresh Allocation_Detail from the exchange —
    it reuses the SAME file the morning already fetched.
  - it must NOT touch CLI_PRUDENTIAL_LIMIT.txt / Intermediate.csv at all —
    that's a once-a-day, morning-only number.
  - the FTP folder Client Prudential Limit reads from AND uploads to are the
    SAME physical folder (/TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION — see
    core.pipeline_box._pb_cleanup_allocation_ftp_folder(), which deletes
    everything else in it down to just today's Allocation_Detail file). If
    the evening run touched that folder the normal way, its own cleanup
    step would delete everything the morning run just uploaded there.

_is_evening_run() below is the ONE switch: once CLI_RUN2_WINDOW's own start
time has passed, Run NEW takes the evening branch. Deliberately reads that
SAME env var (not a second hardcoded "6 PM") so the evening behaviour can
never drift out of sync with the visible "opens after HH:MM" window the
operator already sees on the CLI-02 tile.

The evening branch (_resolve_evening_allocation() below):
  1. If /TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION(EVENING) already exists on
     FTP (an earlier click already set it up today), just read today's
     Allocation_Detail from there (local-first, same as always) — done.
  2. Otherwise (first Run NEW click this evening): fetch today's
     Allocation_Detail from the still-plain ALLOCATION folder (local-first;
     a bare RETR only — NEVER _pb_fetch_allocation_detail(), whose cleanup
     side-effect would delete the morning's uploads before they're archived),
     COPY (not move) that file into a newly-created ALLOCATION(EVENING)
     folder, THEN rename ALLOCATION -> ALLOCATION(MORNING) — by which point
     the file this run needs is already safely sitting in ALLOCATION(EVENING),
     so nothing is lost.
  3. A marker file (.cli_evening_upload) tells cli_upload.py (which runs
     later in the same Run NEW chain, unchanged code otherwise) to upload
     into ALLOCATION(EVENING) instead of the default ALLOCATION, and to not
     flag CLI_PRUDENTIAL_LIMIT.txt as a [MISSING_FILE] — it's SUPPOSED to be
     absent on an evening run.

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
_EVENING_MARKER = ".cli_evening_upload"


def _is_evening_run():
    """True once CLI_RUN2_WINDOW's own configured start time has passed today
    — see the long comment block above. Falls back to 18:00 if the env var is
    missing or malformed, never crashes."""
    win = os.getenv("CLI_RUN2_WINDOW", "19:00-20:00").strip()
    start = (win.split("-", 1)[0].strip() if win else "") or "18:00"
    try:
        h, m = (int(x) for x in start.split(":", 1))
    except Exception:
        h, m = 18, 0
    return datetime.datetime.now().time() >= datetime.time(h, m)


def _resolve_evening_allocation(base_folder, today_str, _p):
    """Evening-only Run NEW allocation resolution — see the module docstring.
    Returns the local Allocation_Detail path. Raises on failure (caller wraps
    this in the same try/except pattern every other step here uses)."""
    from core.pipeline_box import (_pb_ftp_folder_exists, _pb_fetch_file_from_ftp_folder,
                                    _pb_push_file_to_ftp_folder, _pb_ftp_rename_folder)
    alloc_filename = f"Allocation_Detail_{today_str}.csv"
    alloc_path = os.path.join(base_folder, alloc_filename)
    morning_folder = _ALLOC_REMOTE_BASE
    evening_folder = _ALLOC_REMOTE_BASE + "(EVENING)"
    archived_folder = _ALLOC_REMOTE_BASE + "(MORNING)"

    if _pb_ftp_folder_exists(evening_folder):
        _p(f"✅ {evening_folder} already exists — not the first Run NEW this evening.")
        if os.path.exists(alloc_path):
            _p(f"⏭ Using existing local file: {alloc_filename} — skipping download")
        else:
            _p(f"⬇️  Fetching {alloc_filename} from {evening_folder}")
            _pb_fetch_file_from_ftp_folder(evening_folder, alloc_path, alloc_filename)
            _p(f"✅ Downloaded: {alloc_filename}")
        return alloc_path

    _p(f"🌙 First Run NEW this evening — {evening_folder} doesn't exist yet. Setting it up.")
    if os.path.exists(alloc_path):
        _p(f"⏭ Using existing local file: {alloc_filename} (already on disk)")
    else:
        _p(f"⬇️  Fetching {alloc_filename} from {morning_folder} (this morning's folder — "
           f"not yet archived)")
        _pb_fetch_file_from_ftp_folder(morning_folder, alloc_path, alloc_filename)
        _p(f"✅ Downloaded: {alloc_filename}")

    _p(f"📋 Copying {alloc_filename} into a new {evening_folder} "
       f"(the copy in {morning_folder} is left exactly as it is)")
    _pb_push_file_to_ftp_folder(evening_folder, alloc_path, alloc_filename)
    _p(f"✅ {evening_folder} created with today's Allocation Detail")

    _p(f"🏷️  Archiving this morning's folder: {morning_folder} → {archived_folder} — "
       f"everything Run 1 uploaded there (CLI_PRUDENTIAL_LIMIT.txt, MCX file, "
       f"collaterals) stays exactly as it is, just under the archived name")
    try:
        _pb_ftp_rename_folder(morning_folder, archived_folder)
        _p(f"✅ Renamed to {archived_folder}")
    except Exception as _re:
        _p(f"⚠️ Could not rename {morning_folder} → {archived_folder} (non-fatal — "
           f"{evening_folder} is already correctly set up; only the archive-rename "
           f"failed, safe to fix by hand on the FTP server): {_re}")

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

    # ── EVENING RUN — everything below this block is the pre-existing morning
    # behaviour, completely unchanged. See the long module-docstring comment
    # for the full picture. ──────────────────────────────────────────────────
    if _is_evening_run():
        _p(f"🌙 Evening run detected (CLI_RUN2_WINDOW={os.getenv('CLI_RUN2_WINDOW', '19:00-20:00')}) "
           f"— Run NEW's evening flow: reuse this morning's Allocation Detail, skip "
           f"CLI_PRUDENTIAL_LIMIT.txt, upload to ALLOCATION(EVENING).")
        try:
            _resolve_evening_allocation(base_folder, today_str, _p)
        except Exception as e:
            _p(f"❌ Evening Allocation Detail step failed: {e}")
            _ai = translate_error("Client Prudential Limit (NEW, evening) — Allocation Detail", _log_buffer)
            _p(f"[FRIENDLY_MSG] {_ai or ('Evening Allocation Detail step failed: ' + str(e))}")
            raise SystemExit(1)
        marker_path = os.path.join(base_folder, _EVENING_MARKER)
        try:
            with open(marker_path, "w", encoding="utf-8") as _mf:
                _mf.write(_ALLOC_REMOTE_BASE + "(EVENING)")
        except Exception as _we:
            _p(f"⚠️ Could not write evening marker file (non-fatal, but the upload step "
               f"may not know to target ALLOCATION(EVENING)): {_we}")
        _p("⏭ Skipping CLI_PRUDENTIAL_LIMIT.txt / Intermediate.csv — evening run only "
           "reuses this morning's number, doesn't rebuild it.")
        _p("✅ Allocation Detail (NEW, evening) step finished")
        return

    # ── STEP 0: pre-file check — RISK_REPORT + Kambala must exist for TODAY ───
    _p("🚀 STEP 0: Pre-file check (RISK_REPORT + Kambala)")
    risk_name = f"RISK_REPORT_{today_str}.csv"
    kambala_name = f"Kambala_Omny_Depo_Adhoc_All_level.txt_{today_str}.txt"
    risk_path = os.path.join(base_folder, risk_name)
    kambala_path = os.path.join(base_folder, kambala_name)

    if not os.path.exists(risk_path):
        common_risk_path = os.path.join(common_dir, risk_name)
        if os.path.exists(common_risk_path):
            try:
                shutil.copy2(common_risk_path, risk_path)
                _p(f"✅ Found: {risk_name} (copied from COMMON FILES)")
            except Exception as _ce:
                _p(f"⚠️ Found {risk_name} in COMMON FILES but couldn't copy it in: {_ce}")

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
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0) if col in df.columns else 0
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
        with open(os.path.join(base_folder, "CLI_PRUDENTIAL_LIMIT.txt"), "w") as f:
            f.write("ClientPrudentialLimit\n")
            for _, row in df_final.iterrows():
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
