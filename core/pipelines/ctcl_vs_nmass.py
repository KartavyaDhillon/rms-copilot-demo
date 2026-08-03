"""
ctcl_vs_nmass.py — CTCL vs NMASS Limit Report (wrapped as a function)
======================================================================
This is your CTCL_VS_NMASS script, wrapped in run_ctcl_vs_nmass() so the
platform can call it. ONLY changes from your original:
  • Folder paths        → .env  (CTCL_NMASS_FOLDER, MARGIN_TOP20_FOLDER, etc.)
  • SMTP credentials    → .env  (SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD)
  • Zone / TO / CC email recipients → .env  (change recipients without code edits)
  • External script paths (PAYOUT, CODE2) → .env
  • raise SystemExit(1) → return {"status": "prerun_failed", ...}
  • Wrapped the whole script in def run_ctcl_vs_nmass()
  • Everything else (run counter, TOP20, zone files, backup, win32com password) is identical.

NOTE: this script calls external scripts via subprocess (PAYOUT, CODE2) and uses
win32com for Excel password protection — both require Windows + Excel + those files present.
"""
import os
import polars as pl
from datetime import datetime
import sys
import contextlib
import io
import glob
import re
import shutil
import smtplib
import time
import subprocess
from dotenv import load_dotenv

load_dotenv()

pl.Config.set_tbl_rows(0)
import warnings
warnings.filterwarnings("ignore", message="Workbook contains no default style")
pl.Config.set_verbose(False)


@contextlib.contextmanager
def suppress_stderr():
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        yield
    finally:
        sys.stderr = old_stderr


def _emails(key):
    return [e.strip() for e in os.getenv(key, "").split(",") if e.strip()]


# Same classification rule backup_and_cleanup() uses below — pulled to module
# level so backfill_run_to_db() can reuse it without duplicating the list.
INPUT_PREFIXES = ("CM_Client", "FO_Client", "CO_Client", "ClientNet", "Update Limits", "RISK_REPORT")


# ── KAMBALA reference file ───────────────────────────────────────────────────
# A PERMANENT lookup workbook that lives in CTCL_NMASS_FOLDER alongside the
# daily data. Everything below treats it as a fixture, never as run data:
#   · backup_and_cleanup() skips it (never copied to the run folder, never deleted)
#   · the MCX 90% "any .xlsx that isn't Update Limits" detection excludes it
#   · the pre-run check requires it PRESENT but does NOT date-check it
def kambala_name():
    return os.getenv("CTCL_KAMBALA_FILE", "KAMBALA_ALLOCATION_LIST.xlsx")


def kambala_sheets():
    return (os.getenv("CTCL_KAMBALA_SHEET", "Sheet1"),
            os.getenv("CTCL_KAMBALA_VIRTUAL_SHEET", "VIRTUAL ID"))


def normalize_account_id(value):
    """Account IDs arrive in mixed types and must compare reliably.

    The reference workbook stores some IDs as numbers (77110026) and some as
    text ('09110032'), and Excel/pandas renders whole numbers as floats
    (499 → '499.0'). Without this, '499.0' would never match '499' and the
    account would silently be marked N / not-virtual — a wrong answer that
    looks like a correct one. Used only to build a separate match key; the
    ACCOUNTID actually printed in the report is never rewritten.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none", "nat"):
        return ""
    if text.endswith(".0"):
        stripped = text[:-2]
        if stripped.lstrip("-").isdigit():
            text = stripped
    return text.upper()


def load_kambala_reference(path):
    """→ (kambala_ids, virtual_ids) or raises ValueError with a precise reason.

    Raises rather than returning empty sets on purpose: an empty virtual-ID
    list would not fail loudly, it would just stop marking VIRTUAL ID rows and
    silently reclassify them as LIMIT EXCESS/OK in a report that goes straight
    out by email. The caller turns this into a pre-run check failure.
    """
    import pandas as _pd
    sheet1_name, virtual_name = kambala_sheets()
    if not os.path.isfile(path):
        raise ValueError(f"KAMBALA reference file not found: {path}")
    try:
        sheets = _pd.read_excel(path, sheet_name=None, dtype=str)
    except Exception as e:
        raise ValueError(f"KAMBALA reference file could not be read: "
                         f"{type(e).__name__}: {e}")

    def extract(sheet_name):
        if sheet_name not in sheets:
            raise ValueError(f"Sheet '{sheet_name}' missing from {os.path.basename(path)} "
                             f"(sheets found: {list(sheets.keys())})")
        sdf = sheets[sheet_name]
        sdf.columns = [str(c).strip() for c in sdf.columns]
        if "ACCOUNTID" not in sdf.columns:
            raise ValueError(f"Column 'ACCOUNTID' missing in sheet '{sheet_name}' "
                             f"(columns found: {list(sdf.columns)})")
        ids = {normalize_account_id(v) for v in sdf["ACCOUNTID"].tolist()}
        ids.discard("")
        if not ids:
            raise ValueError(f"No ACCOUNTIDs found in sheet '{sheet_name}'")
        return ids

    return extract(sheet1_name), extract(virtual_name)


# ── Core calculation, kept at module level so it can be TESTED ──────────────
# These two are pure: DataFrame in, DataFrame out, no file/network/email. That
# is the whole point — the REMARKS/DIFFERENCE rules are the part with real
# money riding on them, and buried inside the 400-line run function they could
# only ever be exercised by doing a full live run. tools/test_ctcl_logic.py
# calls these directly.
def compute_difference(df):
    """Round the two deposit figures, then derive the sign-aware DIFFERENCE.

    ROUNDING FIRST IS LOAD-BEARING, not cosmetic. DEPOSIT CTCL decides which
    branch applies here, and apply_remarks() re-tests the same sign. If a
    client's CTCL sums to a float residue like -0.0000001, the unrounded value
    takes the "negative" branch while the rounded 0.00 reads as non-negative
    later — the two stages disagree and the row gets a remark that contradicts
    its own DIFFERENCE. Rounding before either test makes both see one value.

        DEPOSIT CTCL >= 0 → CTCL - NMASS  (limit against deposit; +ve = excess)
        DEPOSIT CTCL <  0 → CTCL + NMASS  (debit balance: no limit was granted
                                           against deposit, so this is shown
                                           for reference and never an excess)
    """
    import polars as _pl
    df = df.with_columns([
        _pl.col("DEPOSIT NMASS").round(2),
        _pl.col("DEPOSIT CTCL").round(2),
    ])
    return df.with_columns(
        _pl.when(_pl.col("DEPOSIT CTCL") >= 0)
           .then(_pl.col("DEPOSIT CTCL") - _pl.col("DEPOSIT NMASS"))
           .otherwise(_pl.col("DEPOSIT CTCL") + _pl.col("DEPOSIT NMASS"))
           .round(2)
           .alias("DIFFERENCE(I-H)")
    )


def apply_remarks(df, kambala_ids, virtual_ids):
    """Add ACCOUNTID_KEY, KAMBALA EXCLUDE ALLOCATION and REMARKS.

    REMARKS priority — each step writes ONLY where REMARKS is still blank, so
    the first rule to match wins and nothing downstream can overwrite it:
        0. CPCODE       — CPCode has any value. Overrides everything: a
                          custodian-settled account's limit is not measured
                          against its own deposit, so neither the virtual-ID
                          nor the excess test means anything for it.
        1. VIRTUAL ID   — blank Zone, or listed on the reference workbook's
                          'VIRTUAL ID' sheet.
        2. (un-mark)    — a VIRTUAL ID with DEPOSIT NMASS > 0 is a real
                          account whatever the zone or the list says.
        3. CTCL >= 0    — DIFFERENCE > 0 → LIMIT EXCESS, else OK.
        4. CTCL <  0    — ALWAYS OK, however large the debit.
    """
    import polars as _pl

    for _c in ["Zone", "ACCOUNTID", "DEPOSIT NMASS", "DIFFERENCE(I-H)", "CPCode", "MTF_STATUS"]:
        if _c not in df.columns:
            df = df.with_columns(_pl.lit("").alias(_c))

    def _clean(col):
        return (_pl.when(_pl.col(col).is_null()).then(_pl.lit(""))
                   .when(_pl.col(col).cast(_pl.Utf8).is_in(["None", "nan", "NaN"])).then(_pl.lit(""))
                   .otherwise(_pl.col(col).cast(_pl.Utf8).str.strip_chars())
                   .alias(col))

    df = df.with_columns([_clean("Zone"), _clean("CPCode"), _clean("MTF_STATUS")])
    df = df.with_columns(_pl.col("ACCOUNTID").cast(_pl.Utf8).str.strip_chars().alias("ACCOUNTID"))

    # Normalised match key so '499.0' finds '499'. Separate column on purpose —
    # the ACCOUNTID actually printed in the report is never rewritten.
    df = df.with_columns(
        _pl.col("ACCOUNTID").map_elements(normalize_account_id, return_dtype=_pl.Utf8)
           .alias("ACCOUNTID_KEY"))

    df = df.with_columns(
        _pl.when(_pl.col("ACCOUNTID_KEY").is_in(list(kambala_ids)))
           .then(_pl.lit("Y")).otherwise(_pl.lit("N"))
           .alias("KAMBALA EXCLUDE ALLOCATION"))

    df = df.with_columns(_pl.lit("").alias("REMARKS"))

    def _mark(cond, label):
        return _pl.when((_pl.col("REMARKS") == "") & cond).then(_pl.lit(label)) \
                  .otherwise(_pl.col("REMARKS")).alias("REMARKS")

    df = df.with_columns(_mark(_pl.col("CPCode") != "", "CPCODE"))
    df = df.with_columns(_mark(
        (_pl.col("Zone") == "") | (_pl.col("Zone").str.to_lowercase() == "nan") |
        _pl.col("ACCOUNTID_KEY").is_in(list(virtual_ids)), "VIRTUAL ID"))
    df = df.with_columns(
        _pl.when((_pl.col("REMARKS") == "VIRTUAL ID") & (_pl.col("DEPOSIT NMASS") > 0))
           .then(_pl.lit("")).otherwise(_pl.col("REMARKS")).alias("REMARKS"))
    df = df.with_columns(_mark(
        (_pl.col("DEPOSIT CTCL") >= 0) & (_pl.col("DIFFERENCE(I-H)") > 0), "LIMIT EXCESS"))
    df = df.with_columns(_mark(
        (_pl.col("DEPOSIT CTCL") >= 0) & (_pl.col("DIFFERENCE(I-H)") <= 0), "OK"))
    df = df.with_columns(_mark(_pl.col("DEPOSIT CTCL") < 0, "OK"))
    return df


def backfill_run_to_db(date_str, run_number):
    """Push an ALREADY-backed-up run folder's files into PostgreSQL.

    For when a run genuinely completed OUTSIDE this dashboard (e.g. run by
    hand on a different machine) and its output files were then copied by
    hand into THIS machine's BACKUPFILES AUTOMATED\\<date>\\<run> folder —
    _archive_to_db() only ever fires automatically inside backup_and_cleanup()
    at the end of a dashboard-triggered run, so files that arrive by any other
    path never get archived unless this is called for them explicitly.

    `date_str` — folder name exactly as it appears on disk, e.g. "21-07-2026"
    (DD-MM-YYYY, matching backup_and_cleanup's own datetime.now().strftime).
    `run_number` — the two-digit run folder, e.g. 1 for ".../01".

    Safe to call more than once: re-archiving a file that's already in
    Postgres just adds another row (save_run_file does not de-dupe), it never
    overwrites or deletes anything on disk.
    """
    base_dir = os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    run_folder = os.path.join(base_dir, "BACKUPFILES AUTOMATED", date_str, f"{int(run_number):02d}")
    if not os.path.isdir(run_folder):
        return {"ok": False, "error": f"Folder not found: {run_folder}"}
    db_tag = f"{date_str}/{int(run_number):02d}"
    archived, skipped = [], []
    for fname in sorted(os.listdir(run_folder)):
        fpath = os.path.join(run_folder, fname)
        if not os.path.isfile(fpath):
            continue
        kind = "input" if any(fname.startswith(p) for p in INPUT_PREFIXES) else "output"
        if _archive_to_db(db_tag, kind, fpath):
            archived.append(fname)
        else:
            skipped.append(fname)
    return {"ok": True, "folder": run_folder, "tag": db_tag, "archived": archived, "skipped": skipped}


def _archive_to_db(tag, kind, filepath, max_retries=3, retry_delay=2):
    """Copy a file's bytes into PostgreSQL with retry.  Never raises — a DB
    hiccup must not break the run.  Identical pattern to short_allocation._archive_to_db."""
    if not filepath or not os.path.exists(filepath):
        return False
    use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
    if not use_pg:
        print(f"[ctcl_vs_nmass] ⚠️  USE_POSTGRES=false — {os.path.basename(filepath)} NOT archived.")
        return False
    import time as _time
    fname = os.path.basename(filepath)
    try:
        from core.db_postgres import save_run_file
        with open(filepath, "rb") as f:
            content = f.read()
    except Exception as e:
        print(f"[ctcl_vs_nmass] WARNING could not read {filepath} for archiving: {e}")
        return False
    for attempt in range(1, max_retries + 1):
        try:
            save_run_file("ctcl_vs_nmass", kind, fname, content, tag=tag)
            print(f"[ctcl_vs_nmass] archived {fname} ({len(content):,} bytes) → PostgreSQL")
            return True
        except Exception as e:
            if attempt < max_retries:
                print(f"[ctcl_vs_nmass] archive attempt {attempt}/{max_retries} failed for {fname}: {e} — retrying in {retry_delay}s")
                _time.sleep(retry_delay)
            else:
                print(f"[ctcl_vs_nmass] WARNING: {fname} could NOT be archived after {max_retries} attempts: {e}")
    return False


def run_ctcl_vs_nmass(run_number_override=None):
    """Thin wrapper around _run_ctcl_vs_nmass_impl — adds the same AI (Groq) plain-English
    fallback translation the download scripts already have (nmass_download.py, mcx90_download.py,
    mcx_deposit_download.py), for the PROCESSING step itself. Previously, if CTCL vs NMASS
    processing (as opposed to the NMASS download) hit an error we'd never seen before, the
    dashboard had nothing to show except a raw exit code — this makes an unrecognized processing
    failure print a [FRIENDLY_MSG] tag too, same as everywhere else, so it shows up as the amber
    'note' event in the live console instead of silence."""
    try:
        return _run_ctcl_vs_nmass_impl(run_number_override=run_number_override)
    except SystemExit:
        raise
    except Exception:
        import traceback
        from core.error_translator import translate_error
        tb_lines = traceback.format_exc().splitlines()
        for _l in tb_lines:
            print(_l, flush=True)
        _ai_msg = translate_error("CTCL vs NMASS processing", tb_lines)
        print(f"[FRIENDLY_MSG] {_ai_msg or 'CTCL vs NMASS processing failed for an unrecognized reason — check the Logs button or contact tech support.'}", flush=True)
        # Re-raise the ORIGINAL exception (not SystemExit) — some callers (the legacy
        # /ctcl-vs-nmass-stream tile) specifically catch `except Exception`, which SystemExit
        # would skip right past, leaving them with no result at all instead of a clean error.
        raise


def _run_ctcl_vs_nmass_impl(run_number_override=None):
    """CTCL vs NMASS full pipeline: validate → compute → zone files → emails →
    (run1: TOP20) → (Tue/Thu: CODE2) → PAYOUT → backup → commit run number.
    run_number_override: if set (int 1-5), use this run number instead of counter+1."""

    # =====================================================
    # CONFIG  (folders from .env)
    # =====================================================
    BASE_DIR    = os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    COMMON_DIR  = os.getenv("SA_COMMON_DIR",     r"C:\COMMON FILES")
    OUTPUT_FILE = None
    today_ddmmyyyy = datetime.now().strftime("%d%m%Y")
    today_yyyymmdd = datetime.now().strftime("%Y%m%d")

    MARGIN_TOP10_FOLDER = os.getenv("MARGIN_TOP20_FOLDER", r"D:\MARGIN TOP20")
    os.makedirs(MARGIN_TOP10_FOLDER, exist_ok=True)

    # custom copy destinations (run 1)
    RUN1_COPY_CTCL_DESTINATIONS = _emails("RUN1_COPY_CTCL_DESTINATIONS") or [r"C:\AMIT SIR"]
    RUN1_COPY_CTCL_AND_RISK_DESTINATION = os.getenv("RUN1_COPY_CTCL_AND_RISK_DESTINATION", r"C:\Ashutosh")

    # external scripts (subprocess) — paths from .env
    PAYOUT_DOWNLOAD_SCRIPT = os.getenv("PAYOUT_DOWNLOAD_SCRIPT", r"D:\PAYOUT ASHISH\CODES\PAYMENT_REQUEST_LOG_FILE.py")
    PAYOUT_CHECK_SCRIPT    = os.getenv("PAYOUT_CHECK_SCRIPT",    r"D:\PAYOUT ASHISH\CODES\ctcl working\CTCL_PAYOUT_FINAL.py")

    RUN_CODE2_ENABLED = os.getenv("RUN_CODE2_ENABLED", "true").lower() == "true"
    CODE2_FOLDER = os.getenv("CODE2_FOLDER", r"D:\TUESDAY THURSDAY")
    RUN_CODE2_DAYS = ["Tuesday", "Thursday"]

    COPY_RUN01_ENABLED = os.getenv("COPY_RUN01_ENABLED", "true").lower() == "true"
    RUN01_DESTINATION_FOLDER = os.getenv("RUN01_DESTINATION_FOLDER", r"D:\NEW AMIT SIR")

    # =====================================================
    # MULTI ZONE CONFIG  (recipients from .env)
    # =====================================================
    ZONE_CONFIG = {
        "CHND": {"zones": ["ZOCHND"],
                 "to": _emails("CTCL_CHND_TO"), "cc": _emails("CTCL_CHND_CC")},
        "LUDH": {"zones": ["ZO", "ZOLUDH"],
                 "to": _emails("CTCL_LUDH_TO"), "cc": _emails("CTCL_LUDH_CC")},
        "MUMB": {"zones": ["ZOMUMB"],
                 "to": _emails("CTCL_MUMB_TO"), "cc": _emails("CTCL_MUMB_CC")},
        "DELH": {"zones": ["ZODELH", "ZOMT", "ZOCDR"],
                 "to": _emails("CTCL_DELH_TO"), "cc": _emails("CTCL_DELH_CC")},
    }

    # =====================================================
    # RUN COUNTER (DATE-AWARE, SAFE)
    # =====================================================
    RUN_COUNTER_FILE = os.path.join(BASE_DIR, "run_counter.txt")
    today_str = datetime.now().strftime("%Y-%m-%d")

    last_date = None
    last_run = 0
    if os.path.exists(RUN_COUNTER_FILE):
        try:
            with open(RUN_COUNTER_FILE, "r") as f:
                content = f.read().strip()
                if content:
                    last_date, last_run = content.split("|")
                    last_run = int(last_run)
        except Exception:
            last_date = None
            last_run = 0

    if run_number_override is not None:
        # Retry from dashboard — use the exact run number requested
        run_number = int(run_number_override)
        print(f"ℹ️  run_number_override={run_number} — using requested run number instead of counter")
    elif last_date == today_str:
        run_number = last_run + 1
    else:
        run_number = 1
    if run_number > 5:
        run_number = 1

    def extract_date_from_filename(filename):
        m_ddmmyyyy = re.search(r"(?<!\d)(\d{2}\d{2}20\d{2})", filename)
        if m_ddmmyyyy:
            return m_ddmmyyyy.group(1)
        m_yyyymmdd = re.search(r"(20\d{6})", filename)
        if m_yyyymmdd:
            return m_yyyymmdd.group(1)
        return None

    def is_today_file(filename, expected_format):
        date_str = extract_date_from_filename(filename)
        if not date_str:
            return False
        if expected_format == "DDMMYYYY":
            return date_str == datetime.now().strftime("%d%m%Y")
        if expected_format == "YYYYMMDD":
            return date_str == datetime.now().strftime("%Y%m%d")
        return False

    def is_modified_within_minutes(filepath, minutes=30):
        modified_time = datetime.fromtimestamp(os.path.getmtime(filepath))
        return (datetime.now() - modified_time).total_seconds() <= minutes * 60

    def pre_run_file_check(base_dir):
        required_files = {
            "CM_CLIENT_LEVEL": {"patterns": ["CM_ClientLevelCollaterals_*.csv","CM_ClientLevelCollaterals_*.xlsx","CM_ClientLevelCollaterals_*.csv.crdownload"], "date_format": "DDMMYYYY"},
            "FO_CLIENT_LEVEL": {"patterns": ["FO_ClientLevelCollaterals_*.csv","FO_ClientLevelCollaterals_*.xlsx","FO_ClientLevelCollaterals_*.csv.crdownload"], "date_format": "DDMMYYYY"},
            "CO_CLIENT_LEVEL": {"patterns": ["CO_ClientLevelCollaterals_*.csv","CO_ClientLevelCollaterals_*.xlsx","CO_ClientLevelCollaterals_*.csv.crdownload"], "date_format": "DDMMYYYY"},
            "CLIENT_NET_EFFECTIVE_DEPOSITS": {"patterns": ["ClientNetEffectiveDeposits_*.csv","ClientNetEffectiveDeposits_*.xlsx"], "date_format": "DDMMYYYY"},
            "UPDATE_LIMITS_FILE": {"patterns": ["Update Limits*.xlsx"], "date_format": "DDMMYYYY", "check_modified_minutes": 30},
            "RISK_REPORT": {"patterns": ["RISK_REPORT_*.csv"], "date_format": "DDMMYYYY"},
            # MCX 90% arrives with a random prefix + YYYYMMDD stamp, so it is
            # identified as "any .xlsx that isn't one of ours". Every permanent
            # or generated .xlsx in this folder must be excluded here or it will
            # be picked up as the margin file — KAMBALA (permanent reference)
            # and FULL_FILE_CTCL_VS_NMASS (this run's own output, present when
            # a run is retried) are exactly those cases.
            "MCX_RANDOM_NAME_FILE": {"patterns": ["*.xlsx"], "date_format": "YYYYMMDD",
                                     "exclude_contains": ["Update Limits", "KAMBALA",
                                                          "FULL_FILE_CTCL_VS_NMASS"]},
        }
        missing = []; wrong_date = []; found = {}
        for name, cfg in required_files.items():
            files = []
            for pattern in cfg["patterns"]:
                files.extend(glob.glob(os.path.join(base_dir, pattern)))
                # RISK_REPORT is shared — also search SA_COMMON_DIR
                if name == "RISK_REPORT" and os.path.isdir(COMMON_DIR):
                    files.extend(glob.glob(os.path.join(COMMON_DIR, pattern)))
            if "exclude_contains" in cfg:
                files = [f for f in files if not any(x.lower() in os.path.basename(f).lower() for x in cfg["exclude_contains"])]
            if not files:
                missing.append(name); continue
            latest_file = max(files, key=os.path.getmtime)
            fname = os.path.basename(latest_file)
            if "date_format" in cfg:
                if not is_today_file(fname, cfg["date_format"]):
                    wrong_date.append(f"{name} → {fname}"); continue
            if "check_modified_minutes" in cfg:
                if not is_modified_within_minutes(latest_file, cfg["check_modified_minutes"]):
                    modified_at = datetime.fromtimestamp(os.path.getmtime(latest_file)).strftime("%d-%m-%Y %H:%M:%S")
                    wrong_date.append(f"{name} → {fname} (Last Modified: {modified_at}, older than {cfg['check_modified_minutes']} minutes)"); continue
            found[name] = latest_file

        # ── KAMBALA reference — checked HERE, not above ──────────────────────
        # It gets its own block because it breaks every rule the loop applies:
        # it is a permanent fixture, so there is no date to validate and no
        # freshness rule; and being merely PRESENT is not enough — the sheets
        # and the ACCOUNTID column have to be readable or the VIRTUAL ID
        # marking silently degrades. So it is validated by actually loading it.
        kambala_path = os.path.join(base_dir, kambala_name())
        try:
            kb_ids, kb_virtual = load_kambala_reference(kambala_path)
            found["KAMBALA_REFERENCE"] = kambala_path
        except ValueError as _ke:
            missing.append(f"KAMBALA_REFERENCE → {_ke}")
            kb_ids, kb_virtual = set(), set()

        if missing or wrong_date:
            print("\n❌ PRE-RUN FILE CHECK FAILED\n")
            if missing:
                print("Missing required files:")
                for m in missing: print(f"   - {m}")
            if wrong_date:
                print("\nFiles not matching CURRENT DATE / RULES:")
                for w in wrong_date: print(f"   - {w}")
            print("\n❌ Aborting execution due to pre-run validation failure")
            return {"status": "prerun_failed", "missing": missing, "wrong_date": wrong_date}
        print("\n✅ PRE-RUN FILE CHECK PASSED")
        print("Files to be used:")
        for k, v in found.items():
            print(f"   {k}: {os.path.basename(v)}")
        print(f"   KAMBALA lists: {len(kb_ids)} allocation IDs, {len(kb_virtual)} virtual IDs")
        print("--------------------------------------------------\n")
        return {"status": "ok", "kambala_ids": kb_ids, "virtual_ids": kb_virtual}

    prerun = pre_run_file_check(BASE_DIR)
    if prerun.get("status") != "ok":
        return prerun
    # Loaded once during validation — reused below rather than re-read.
    KAMBALA_ID_LIST = prerun["kambala_ids"]
    VIRTUAL_ID_LIST = prerun["virtual_ids"]

    OUTPUT_FILE = os.path.join(BASE_DIR, f"FULL_FILE_CTCL_VS_NMASS_{run_number:02d}.xlsx")

    # =====================================================
    # HELPERS
    # =====================================================
    def find_file(starts_with=None, contains=None):
        # Search BASE_DIR first, then SA_COMMON_DIR (for shared files like RISK_REPORT)
        for search_dir in [BASE_DIR, COMMON_DIR]:
            if not os.path.isdir(search_dir):
                continue
            for f in os.listdir(search_dir):
                if starts_with and not f.startswith(starts_with):
                    continue
                if contains and contains not in f:
                    continue
                if f.endswith((".csv", ".xlsx", ".csv.crdownload")):
                    return os.path.join(search_dir, f)
        return None

    # ── POLARS helpers ────────────────────────────────────────────────────────
    def read_any_file(path):
        """Read CSV or Excel into a polars DataFrame."""
        if path.endswith(".xlsx"):
            with suppress_stderr():
                return pl.read_excel(path)
        else:
            return pl.read_csv(path, infer_schema_length=0, ignore_errors=True)

    def detect_header_excel(path, required_columns):
        """Scan first 10 rows to find the header, return a polars DataFrame.
        Uses pandas for the scan (identical to original logic that worked)."""
        import pandas as _pd
        raw = _pd.read_excel(path, header=None)
        for i in range(10):
            row = raw.iloc[i].astype(str).str.strip()
            if all(col in row.values for col in required_columns):
                return pl.from_pandas(_pd.read_excel(path, header=i))
        raise Exception(f"Header row not found in first 10 rows of {path}")

    def _num(df, col):
        """Cast column to Float64 (handles strings with commas), fill null/nan with 0."""
        return df.with_columns(
            pl.col(col).cast(pl.Utf8, strict=False)
              .str.replace_all(",", "")
              .cast(pl.Float64, strict=False)
              .fill_null(0).fill_nan(0)
        )

    def full_join(base, other, on, new_col):
        """Full outer join two polars DataFrames; coalesce key; fill new_col nulls with 0."""
        try:
            result = base.join(other, on=on, how="full", coalesce=True)
        except TypeError:
            result = base.join(other, on=on, how="outer")
            rk = on + "_right"
            if rk in result.columns:
                result = result.with_columns(
                    pl.coalesce([on, rk]).alias(on)
                ).drop(rk)
        return result.with_columns(pl.col(new_col).fill_null(0).fill_nan(0))

    # 1 CM
    cm_file = find_file("CM_ClientLevelCollaterals", today_ddmmyyyy)
    cm_df = read_any_file(cm_file)
    for _c in ["Cash allocated (b)", "Cash-Others (c)", "Non-cash Securities (d)"]:
        cm_df = _num(cm_df, _c)
    cm_df = cm_df.with_columns(
        (pl.col("Cash allocated (b)") + pl.col("Cash-Others (c)") + pl.col("Non-cash Securities (d)")).alias("CM_SUM")
    )
    cm_final = cm_df.group_by("Client Code").agg(pl.col("CM_SUM").sum())

    # 2 FO
    fo_file = find_file("FO_ClientLevelCollaterals", today_ddmmyyyy)
    fo_df = read_any_file(fo_file)
    for _c in ["Cash allocated (b)", "Cash-others (c)", "Non-cash Securities (d)"]:
        fo_df = _num(fo_df, _c)
    fo_df = fo_df.with_columns(
        (pl.col("Cash allocated (b)") + pl.col("Cash-others (c)") + pl.col("Non-cash Securities (d)")).alias("FO_SUM")
    )
    fo_final = fo_df.group_by("Client Code").agg(pl.col("FO_SUM").sum())

    # 2B CO
    co_file = find_file("CO_ClientLevelCollaterals", today_ddmmyyyy)
    co_df = read_any_file(co_file)
    for _c in ["Cash allocated (b)", "Cash-others (c)", "Non-cash Securities (d)"]:
        co_df = _num(co_df, _c)
    co_df = co_df.with_columns(
        (pl.col("Cash allocated (b)") + pl.col("Cash-others (c)") + pl.col("Non-cash Securities (d)")).alias("CO_SUM")
    )
    co_final = co_df.group_by("Client Code").agg(pl.col("CO_SUM").sum())

    # 3 DEPOSITS
    deposit_file = find_file("ClientNetEffectiveDeposits", today_ddmmyyyy)
    dep_df = read_any_file(deposit_file)
    dep_df = _num(dep_df, "Total Collateral (Cash and Non Cash)")
    dep_final = (dep_df.group_by("Client/CP Code")
                       .agg(pl.col("Total Collateral (Cash and Non Cash)").sum().alias("DEP_SUM"))
                       .rename({"Client/CP Code": "Client Code"}))

    # 4 MARGIN LIMIT
    margin_file = find_file(contains=today_yyyymmdd)
    margin_df = detect_header_excel(margin_file, required_columns=["Client Code", "Client Margin Limit"])
    margin_df = _num(margin_df, "Client Margin Limit")
    margin_final = margin_df.group_by("Client Code").agg(pl.col("Client Margin Limit").sum().alias("MARGIN_LIMIT"))

    # 5 UPDATE LIMITS → CTCL
    update_limits_file = find_file(starts_with="Update Limits", contains=today_ddmmyyyy)
    with suppress_stderr():
        pl_df = pl.read_excel(update_limits_file)
    # Keep an untouched reference to the freshly-parsed file (before any of the casting/column
    # work below) so Payout Checking — which runs later in this same process and needs this
    # exact same file — can reuse it instead of re-parsing a 100MB+ .xlsx from disk a second
    # time. Polars DataFrames are immutable (with_columns returns a NEW object), so this
    # reference is unaffected by every transformation applied to pl_df afterward.
    _update_limits_preload = (update_limits_file, pl_df)
    update_cols = ["Payin","Payout","Collateral(Total)","Cash","DaylongCash","MtfDaylongCash","CacDaylongCash",
                   "UnclearedCash","BrokerCollateral","EQT Collateral","DER Collateral","COM Collateral",
                   "FX Collateral","AUXDaylongCash","AuxUnclearedCash","AuxBrokerCollateral","Remarks Amount","Remarks Text"]
    pl_df = pl_df.with_columns([pl.col(c).cast(pl.Float64, strict=False).fill_null(0) for c in update_cols])

    # UnrealizedEqMargin — DEDUCTED from the CTCL total, never added.
    # A negative value here means an unrealised GAIN; it must not inflate the
    # limit, so negatives are floored to 0 and ignored. Only positive values
    # (an unrealised loss already eating into the limit) are subtracted.
    if "UnrealizedEqMargin" in pl_df.columns:
        pl_df = pl_df.with_columns(
            pl.col("UnrealizedEqMargin").cast(pl.Float64, strict=False).fill_null(0))
        pl_df = pl_df.with_columns(
            pl.when(pl.col("UnrealizedEqMargin") < 0).then(0.0)
              .otherwise(pl.col("UnrealizedEqMargin"))
              .alias("UNREALIZED_EQ_MARGIN_DEDUCTION"))
    else:
        # Older Update Limits files predate this column. Deducting 0 keeps the
        # run alive and matches the previous behaviour exactly, but it is
        # announced — a silently-skipped deduction would overstate every
        # client's CTCL limit and nobody would know to look.
        print("⚠️ UnrealizedEqMargin column not found in Update Limits — "
              "no deduction applied to DEPOSIT CTCL for this run")
        pl_df = pl_df.with_columns(pl.lit(0.0).alias("UNREALIZED_EQ_MARGIN_DEDUCTION"))

    pl_df = pl_df.with_columns(
        (pl.sum_horizontal(update_cols) - pl.col("UNREALIZED_EQ_MARGIN_DEDUCTION")).alias("ROW_CTCL"))
    ctcl_final = (pl_df.group_by("Entity").agg(pl.col("ROW_CTCL").sum().alias("DEPOSIT_CTCL"))
                  .rename({"Entity": "Client Code"}))

    # 6 MERGE (full outer joins — pure polars)
    # Fill CM_SUM nulls first (rows that exist only in other frames)
    final_pl = cm_final.with_columns(pl.col("CM_SUM").fill_null(0).fill_nan(0))
    final_pl = full_join(final_pl, fo_final,     "Client Code", "FO_SUM")
    final_pl = full_join(final_pl, co_final,     "Client Code", "CO_SUM")
    final_pl = full_join(final_pl, dep_final,    "Client Code", "DEP_SUM")
    final_pl = full_join(final_pl, margin_final, "Client Code", "MARGIN_LIMIT")
    final_pl = full_join(final_pl, ctcl_final,   "Client Code", "DEPOSIT_CTCL")
    # Fill ALL intermediate sum columns before adding.
    # Full outer joins add rows from each right-side file; those new rows get nulls
    # in every LEFT-side column. Each full_join only fills its own new_col, so after
    # 5 joins a client that appeared only in DEP or MARGIN files still has
    # CM_SUM=null, FO_SUM=null, etc. → null+anything = null → DEPOSIT NMASS blank.
    for _fc in ["CM_SUM", "FO_SUM", "CO_SUM", "DEP_SUM", "MARGIN_LIMIT", "DEPOSIT_CTCL"]:
        final_pl = final_pl.with_columns(pl.col(_fc).fill_null(0).fill_nan(0))

    # 7 FINAL CALC
    final_pl = final_pl.with_columns(
        (pl.col("CM_SUM") + pl.col("FO_SUM") + pl.col("CO_SUM") +
         pl.col("DEP_SUM") + pl.col("MARGIN_LIMIT")).alias("DEPOSIT NMASS")
    ).select(["Client Code", "DEPOSIT NMASS", "DEPOSIT_CTCL"]
    ).rename({"Client Code": "ACCOUNTID", "DEPOSIT_CTCL": "DEPOSIT CTCL"})

    # 8 RISK REPORT + DIFF + REMARKS (all polars)
    risk_file = find_file(starts_with="RISK_REPORT", contains=today_ddmmyyyy)
    if not risk_file:
        raise FileNotFoundError("❌ RISK_REPORT file not found for today")
    risk_pl = pl.read_csv(risk_file, infer_schema_length=0, ignore_errors=True)
    # MTF_STATUS is carried through from the RISK_REPORT against ACCOUNTID.
    # An older/short risk file that lacks the column must not kill the run, so
    # it is created empty — and said out loud, since a silently blank column
    # looks identical to "every account has no MTF status".
    if "MTF_STATUS" not in risk_pl.columns:
        print("⚠️ MTF_STATUS column not found in RISK_REPORT — column will be blank in the output")
        risk_pl = risk_pl.with_columns(pl.lit("").alias("MTF_STATUS"))
    risk_pl = risk_pl.select(["Zone", "TRADING_SOFTWARE_TYPE", "CPCode", "PISAccount",
                              "MTF_STATUS", "ACCOUNTID"])
    final_pl = final_pl.join(risk_pl, on="ACCOUNTID", how="left")

    # Round + sign-aware DIFFERENCE — see compute_difference()'s docstring for
    # why the rounding must happen before the sign test.
    final_pl = compute_difference(final_pl)
    final_pl = final_pl.filter(~pl.col("ACCOUNTID").cast(pl.Utf8).str.to_uppercase().str.starts_with("SUB"))
    final_pl = final_pl.sort("DIFFERENCE(I-H)", descending=True, nulls_last=True)

    # KAMBALA EXCLUDE ALLOCATION + REMARKS — the priority rules live in
    # apply_remarks() at module level so they can be unit-tested directly
    # (tools/test_ctcl_logic.py) instead of only via a full live run.
    final_pl = apply_remarks(final_pl, KAMBALA_ID_LIST, VIRTUAL_ID_LIST)

    # ACCOUNTID_KEY is an internal match helper — not exported.
    final_pl = final_pl.select(["Zone", "TRADING_SOFTWARE_TYPE", "CPCode", "PISAccount",
                                "MTF_STATUS", "ACCOUNTID", "KAMBALA EXCLUDE ALLOCATION",
                                "DEPOSIT NMASS", "DEPOSIT CTCL", "DIFFERENCE(I-H)", "REMARKS"])
    # Use pandas to_excel (not polars write_excel) — polars adds red conditional formatting
    # for negative values and other default styling. pandas produces a plain xlsx that
    # matches the reference output exactly.
    import pandas as _pd_xl
    final_pl.to_pandas().to_excel(OUTPUT_FILE, index=False)

    from openpyxl import load_workbook
    wb = load_workbook(OUTPUT_FILE); ws = wb.active
    for col in ws.columns:
        col_letter = col[0].column_letter
        header = str(col[0].value)
        if header == "CPCode":
            ws.column_dimensions[col_letter].width = len(header) + 2
            continue
        max_length = len(header)
        for cell in col[1:]:
            if cell.value is not None:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max_length + 2
    wb.save(OUTPUT_FILE)
    print("✅ OUTPUT FILE created successfully")

    # COPY FULL_FILE_01
    if COPY_RUN01_ENABLED and run_number == 1:
        try:
            os.makedirs(RUN01_DESTINATION_FOLDER, exist_ok=True)
            shutil.copy2(OUTPUT_FILE, os.path.join(RUN01_DESTINATION_FOLDER, os.path.basename(OUTPUT_FILE)))
            print(f"✅ FULL_FILE_CTCL_VS_NMASS_01 copied to:\n{RUN01_DESTINATION_FOLDER}")
        except Exception as e:
            print(f"❌ Copy failed: {e}")

    # ZONE FILES
    ZONE_OUTPUT_FILES = {}
    for zone_key, cfg in ZONE_CONFIG.items():
        zone_pl = final_pl.filter(pl.col("Zone").is_in(cfg["zones"]))
        if zone_pl.is_empty():
            print(f"⚠️ No records found for {zone_key}"); continue
        output_path = os.path.join(BASE_DIR, f"FULL_FILE_CTCL_VS_NMASS_{zone_key}_{run_number:02d}.xlsx")
        zone_pl.to_pandas().to_excel(output_path, index=False)
        wb = load_workbook(output_path); ws = wb.active
        for col in ws.columns:
            max_length = len(str(col[0].value))
            for cell in col[1:]:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            ws.column_dimensions[col[0].column_letter].width = max_length + 2
        wb.save(output_path)
        ZONE_OUTPUT_FILES[zone_key] = output_path
        print(f"✅ {zone_key} file created: {output_path}")

    # SMTP from .env
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_EMAIL = os.getenv("SENDER_EMAIL")
    SMTP_PASSWORD = os.getenv("SENDER_PASSWORD")

    def send_email_with_retry(subject, body, to_list, cc_list, attachment_path, label=""):
        from email.message import EmailMessage
        while True:
            try:
                msg = EmailMessage()
                msg["From"] = SMTP_EMAIL
                msg["To"] = ", ".join(to_list)
                msg["Cc"] = ", ".join(cc_list)
                msg["Subject"] = subject
                msg.set_content(body, subtype="html")
                with open(attachment_path, "rb") as f:
                    msg.add_attachment(f.read(), maintype="application", subtype="octet-stream",
                                       filename=os.path.basename(attachment_path))
                all_recipients = to_list + cc_list
                with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60) as server:
                    server.ehlo(); server.starttls(); server.ehlo()
                    server.login(SMTP_EMAIL, SMTP_PASSWORD)
                    server.send_message(msg, to_addrs=all_recipients)
                print(f"✅ {label+' ' if label else ''}email sent successfully")
                break
            except Exception as e:
                print(f"❌ {label+' ' if label else ''}email failed: {e}")
                print("🔁 Retrying in 10 seconds...")
                time.sleep(10)

    TO_LIST = _emails("CTCL_MAIN_TO") or ["rms_del@mastertrust.co.in"]
    CC_LIST = _emails("CTCL_MAIN_CC")
    ATTACHMENT = OUTPUT_FILE
    SUBJECT = f"CTCL VS NMASS LIMIT REPORT {run_number}"
    BODY = f"""
Dear Team,<br><br>

Please find attached the <b>CTCL vs NMASS Limit Report</b>.<br><br>

<b>Note:</b><br>
This report has been generated based on the following logic:
<ul>
    <li>
        <b>DEPOSIT NMASS</b> is calculated as the sum of:
        <ul>
            <li>
                <b>Cash allocated (b)</b>, <b>Cash-Others (c)</b>, and
                <b>Non-cash Securities (d)</b> from
                <b>CM_ClientLevelCollaterals</b>,
                <b>FO_ClientLevelCollaterals</b> and
                <b>CO_ClientLevelCollaterals</b>.
            </li>
            <li>
                <b>Total Collateral (Cash and Non Cash)</b> from
                <b>ClientNetEffectiveDeposits</b>,
            </li>
            <li>
                <b>Margin Limit</b> from the <b>MCX Margin Limit file 90%</b>.
            </li>
        </ul>
    </li>

    <li>
        <b>DEPOSIT CTCL</b> is derived from the <b>Update Limits file</b>
        using the aggregated sum of the following columns:
        <ul>
            <li>
                Payin, Payout, Collateral(Total), Cash,
                DaylongCash, MtfDaylongCash, CacDaylongCash,
                UnclearedCash, BrokerCollateral,
                EQT Collateral, DER Collateral, COM Collateral,
                FX Collateral, AUXDaylongCash, AuxUnclearedCash,
                AuxBrokerCollateral, Remarks Amount & Remarks Text
            </li>
            <li>
                <b>UnrealizedEqMargin</b> is then deducted from this total
                (negative values are treated as 0 and ignored; positive
                values are subtracted).
            </li>
        </ul>
    </li>

    <li>
        <b>Remarks</b> are assigned based on the following priority:
        <ul>
            <li>
                <b>CPCODE</b> – whenever the <b>CPCode</b> column has any
                value, marked as CPCODE with no other rule applied.
            </li>
            <li>
                <b>VIRTUAL ID</b> – Blank Zone or predefined Virtual ID list
                (also removed if <b>DEPOSIT NMASS &gt; 0</b>),
            </li>
            <li>
                <b>When DEPOSIT CTCL is 0 or above</b> &ndash; the two figures
                are subtracted (DEPOSIT CTCL &minus; DEPOSIT NMASS).
                If the result is above 0 the account has more limit than
                deposit, so it is marked <b>LIMIT EXCESS</b>.
                If the result is 0 or below, it is marked <b>OK</b>.<br>
                Example: NMASS 100 and CTCL 130 &rarr; 130 &minus; 100 = 30
                &rarr; <b>LIMIT EXCESS</b>.
                NMASS 100 and CTCL 80 &rarr; 80 &minus; 100 = &minus;20
                &rarr; <b>OK</b>.
            </li>
            <li>
                <b>When DEPOSIT CTCL is below 0</b> (debit balance) &ndash;
                the two figures are <b>added together instead of subtracted</b>,
                and the account is <b>always marked OK</b>. A debit balance
                means no limit was actually given against deposit, so it is
                never treated as LIMIT EXCESS however large the debit is.<br>
                Example: NMASS 15,474.71 and CTCL &minus;11,533.71 &rarr;
                &minus;11,533.71 + 15,474.71 = 3,941.00 shown in the
                Difference column, remark stays <b>OK</b>.
            </li>
        </ul>
    </li>

    <li>
        <b>KAMBALA EXCLUDE ALLOCATION</b> shows <b>Y</b> when the ACCOUNTID is
        present in the Kambala allocation list, and <b>N</b> when it is not.
    </li>

    <li>
        <b>MTF_STATUS</b> is picked from the <b>RISK_REPORT</b> file against
        the ACCOUNTID.
    </li>

    <li>
        Accounts starting with <b>SUB</b> have been excluded from the report.
    </li>
    <br>
    <b>Note:</b> <b>Margin for Cash (CM), F&O (FO) and Currency (CO) segments is considered as per SPAN File No. 1 & MCX margins are updated based on Live Margin.</b>
<br><br>
</ul>

<br>

Best Regards,<br>
<b>Kartavya Dhillon</b><br>
Master Capital Services Ltd<br>
📞 011-66331000 | Ext. 84
"""
    send_email_with_retry(subject=SUBJECT, body=BODY, to_list=TO_LIST, cc_list=CC_LIST, attachment_path=ATTACHMENT, label="Full file")

    for zone_key, file_path in ZONE_OUTPUT_FILES.items():
        cfg = ZONE_CONFIG[zone_key]
        subject = f"CTCL VS NMASS {zone_key} ZONE LIMIT REPORT {run_number}"
        print(f"\n📤 Sending {zone_key} email...")
        send_email_with_retry(subject=subject, body=BODY, to_list=cfg["to"], cc_list=cfg["cc"], attachment_path=file_path, label=f"{zone_key} zone")

    # ── CTCL main emails done — phase marker for JS pill transition ──────────
    # JS sees this and marks the CTCL vs NMASS pill ✓ done, then starts
    # MarginTop20 pill blinking (run 1) or Payout pill blinking (runs 2-5).
    print("\n✅ CTCL EMAILS COMPLETED\n")

    def apply_excel_password(file_path, password="master123"):
        """Apply password: tries win32com first, falls back to msoffcrypto-tool."""
        # ── Attempt 1: win32com ───────────────────────────────────────────
        try:
            import tempfile, pythoncom
            import win32com.client as win32
            def _rebuild():
                try:
                    gen_py = os.path.join(tempfile.gettempdir(), "gen_py")
                    if os.path.exists(gen_py):
                        shutil.rmtree(gen_py, ignore_errors=True)
                    win32.gencache.is_readonly = False; win32.gencache.Rebuild()
                except Exception: pass
            def _open_xl():
                pythoncom.CoInitialize()
                try:    return win32.gencache.EnsureDispatch("Excel.Application")
                except: return win32.Dispatch("Excel.Application")
            try:
                xl = _open_xl(); xl.Visible = False; xl.DisplayAlerts = False
                wb = xl.Workbooks.Open(file_path); wb.Password = password; wb.Save(); wb.Close(); xl.Quit()
                print(f"🔐 Password applied (COM): {os.path.basename(file_path)}"); return
            except Exception as e:
                print(f"⚠️ COM attempt 1 failed: {e} — retrying after cache rebuild…")
                _rebuild()
                xl = _open_xl(); xl.Visible = False; xl.DisplayAlerts = False
                wb = xl.Workbooks.Open(file_path); wb.Password = password; wb.Save(); wb.Close(); xl.Quit()
                print(f"🔐 Password applied (COM repair): {os.path.basename(file_path)}"); return
        except ImportError:
            print("ℹ️ win32com not available — trying msoffcrypto-tool…")
        except Exception as e:
            print(f"⚠️ COM failed entirely: {e} — trying msoffcrypto-tool…")
        # ── Attempt 2: msoffcrypto-tool (no Excel required) ──────────────
        try:
            import msoffcrypto, io
            with open(file_path, "rb") as _f:
                _office = msoffcrypto.OfficeFile(_f)
                _enc = io.BytesIO()
                _office.encrypt(password, _enc)
            _tmp = file_path + ".pwdtmp"
            with open(_tmp, "wb") as _f:
                _f.write(_enc.getvalue())
            os.replace(_tmp, file_path)
            print(f"🔐 Password applied (msoffcrypto): {os.path.basename(file_path)}"); return
        except ImportError:
            print("⚠️ msoffcrypto-tool not installed — run: pip install msoffcrypto-tool")
            print("⚠️ Excel password protection SKIPPED for this run.")
        except Exception as e:
            print(f"❌ msoffcrypto failed: {e} — password NOT applied.")

    # TOP20 (run 1 only)
    if run_number == 1:
        try:
            print("\n📁 RUN 1 → COPYING FILES (CONFIG DRIVEN)...\n")
            ctcl_file = OUTPUT_FILE
            # RISK_REPORT is in the shared common folder
            risk_files = glob.glob(os.path.join(COMMON_DIR, "RISK_REPORT_*.csv"))
            if not risk_files:  # fallback to BASE_DIR for manual-placement compatibility
                risk_files = glob.glob(os.path.join(BASE_DIR, "RISK_REPORT_*.csv"))
            risk_file_c = risk_files[0] if risk_files else None
            for folder in RUN1_COPY_CTCL_DESTINATIONS:
                os.makedirs(folder, exist_ok=True)
            os.makedirs(RUN1_COPY_CTCL_AND_RISK_DESTINATION, exist_ok=True)
            if os.path.exists(ctcl_file):
                for folder in RUN1_COPY_CTCL_DESTINATIONS:
                    shutil.copy2(ctcl_file, folder)
                print("✅ CTCL copied to configured folders")
            else:
                print("❌ CTCL file not found")
            if os.path.exists(ctcl_file):
                shutil.copy2(ctcl_file, RUN1_COPY_CTCL_AND_RISK_DESTINATION)
            if risk_file_c and os.path.exists(risk_file_c):
                shutil.copy2(risk_file_c, RUN1_COPY_CTCL_AND_RISK_DESTINATION)
            print("✅ CTCL + Risk copied to Ashutosh folder")
            print("\n🎯 CONFIG COPY COMPLETED\n")
        except Exception as e:
            print(f"❌ Config copy failed: {e}")

        print("\n🚀 RUN 1 DETECTED → STARTING TOP20 PROCESS...\n")
        try:
            risk_files = glob.glob(os.path.join(COMMON_DIR, "RISK_REPORT_*.csv"))
            if not risk_files:  # fallback to BASE_DIR
                risk_files = glob.glob(os.path.join(BASE_DIR, "RISK_REPORT_*.csv"))
            if not risk_files:
                raise FileNotFoundError("❌ RISK_REPORT not found in SA_COMMON_DIR or BASE_DIR")
            risk_src = risk_files[0]
            shutil.copy2(risk_src, os.path.join(MARGIN_TOP10_FOLDER, os.path.basename(risk_src)))
            shutil.copy2(OUTPUT_FILE, os.path.join(MARGIN_TOP10_FOLDER, "FULL_FILE_CTCL_VS_NMASS_01.xlsx"))
            print("✅ Files moved to MARGIN TOP10 folder")
        except Exception as e:
            print(f"❌ File move failed: {e}"); raise

        # TOP20 compute + email + backup is now a pipeline module (margin_top20.py).
        # It reads the files we just moved into MARGIN_TOP20_FOLDER, builds the 3 files,
        # password-protects, emails, then backs up + cleans that folder.
        from core.pipelines.margin_top20 import run_margin_top20
        run_margin_top20()
        print("\n🎯 TOP20 PROCESS COMPLETED\n")
    else:
        print(f"\n⏭ Skipping TOP20 process (Run = {run_number})\n")

    # CODE2 (Tue/Thu)
    today_day = datetime.now().strftime("%A")
    if RUN_CODE2_ENABLED and today_day in RUN_CODE2_DAYS:
        print(f"🚀 {today_day} detected → Running SHORTAGE(TUES/THURS)...")
        try:
            file_name = f"FULL_FILE_CTCL_VS_NMASS_{run_number:02d}.xlsx"
            file_path = os.path.join(BASE_DIR, file_name)
            if os.path.exists(file_path):
                shutil.copy2(file_path, CODE2_FOLDER); print(f"✅ Copied: {file_name}")
            else:
                print(f"❌ File not found: {file_name}")
            update_files = glob.glob(os.path.join(BASE_DIR, "Update Limits*.xlsx"))
            if update_files:
                latest_update = max(update_files, key=os.path.getmtime)
                shutil.copy2(latest_update, CODE2_FOLDER); print(f"✅ Copied: {os.path.basename(latest_update)}")
            else:
                print("⚠️ No Update Limits file found")
            print("🚀 Executing Code 2...")
            from core.pipelines.code2_shortage import run_code2_shortage
            run_code2_shortage()
            print("✅ SHORTAGE(TUES/THURS) executed successfully")
        except Exception as e:
            print(f"❌ SHORTAGE(TUES/THURS) failed: {e}")
    else:
        print(f"⏭ SHORTAGE(TUES/THURS) skipped (Today: {today_day})")

    # ── PAYOUT (must run BEFORE backup so Update Limits file is still present) ─
    print("\n🚀 STARTING PAYOUT DOWNLOAD\n")
    try:
        from core.pipelines.payout_download import run_payout_download
        run_payout_download()
        print("✅ Payout download completed")
    except Exception as e:
        print(f"❌ Payout download failed: {e}")

    try:
        from core.pipelines.payout_check import run_payout_check
        # Pass the Update Limits file this run already parsed above — run_payout_check()
        # only reuses it if its own (unchanged) file search resolves to that exact same
        # path; otherwise it re-reads from disk exactly as before. Saves parsing a
        # 100MB+ .xlsx a second time with no change to what gets computed.
        run_payout_check(_preloaded_update=_update_limits_preload)
    except Exception as e:
        print(f"❌ Payout check failed: {e}")
    print("\n✅ PAYOUT CHECKING COMPLETED\n")

    # ── BASE_DIR backup+cleanup — runs AFTER payout so all files are still present ──
    def backup_and_cleanup(base_dir, run_number, today_str):
        backup_root = os.path.join(base_dir, "BACKUPFILES AUTOMATED")
        date_folder = os.path.join(backup_root, today_str)
        run_folder  = os.path.join(date_folder, f"{run_number:02d}")
        os.makedirs(run_folder, exist_ok=True)
        db_tag = f"{today_str}/{run_number:02d}"

        # Classify files: inputs vs outputs (for Postgres kind label) —
        # INPUT_PREFIXES now defined once at module level (see backfill_run_to_db above).
        moved, errors = [], []
        _kambala = kambala_name().lower()
        for fname in os.listdir(base_dir):
            fpath = os.path.join(base_dir, fname)
            if not os.path.isfile(fpath): continue
            if fname.startswith(".") or fname == "run_counter.txt": continue
            if fname.lower().endswith((".bat", ".py", ".ini", ".cfg")): continue  # keep scripts in folder
            # NEVER move or delete the KAMBALA reference — it is a permanent
            # fixture, not run data. Copying it into every run folder would
            # bloat the backups; deleting it would break the NEXT run's
            # pre-run check, which is the failure that would actually hurt.
            if fname.lower() == _kambala: continue
            dst = os.path.join(run_folder, fname)
            kind = "input" if any(fname.startswith(p) for p in INPUT_PREFIXES) else "output"
            try:
                shutil.copy2(fpath, dst)
                moved.append(fname)
                _archive_to_db(db_tag, kind, dst)
                try: os.remove(fpath)
                except Exception: pass
            except Exception as e:
                errors.append(f"{fname}: {e}")
        if moved:
            print(f"📦 Backup: {len(moved)} files → {run_folder}")
        for err in errors:
            print(f"⚠️ Backup error: {err}")

    backup_and_cleanup(BASE_DIR, run_number, datetime.now().strftime("%d-%m-%Y"))
    print("✅ Backup and cleanup completed")

    # ── Write run counter ────────────────────────────────────────────────────
    counter_file = os.path.join(BASE_DIR, "run_counter.txt")
    today_iso = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(counter_file, "w", encoding="utf-8") as _cf:
            _cf.write(f"{today_iso}|{run_number}")
        print("Run counter updated: Run " + str(run_number) + " complete.")
    except Exception as e:
        print("Could not update run counter: " + str(e))

    print("\nCTCL VS NMASS RUN " + str(run_number) + " COMPLETED SUCCESSFULLY\n")

    return {"status": "ok", "run_number": run_number}
