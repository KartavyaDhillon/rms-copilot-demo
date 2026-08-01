"""
core/pipelines/margin_shortage_15min.py — Margin Shortage (15-minute) pipeline
================================================================================
Ported from the uploaded 15minutesfinal_whatsapp.PY, wrapped as a function so
the platform can call it (same treatment core/pipelines/ctcl_vs_nmass.py got).
Runs 9x/day (see MS15_RUN1_WINDOW..MS15_RUN9_WINDOW in .env) alongside CTCL vs
NMASS, sharing the same NSCCL/NCFE/MCX90 download credentials and reusing the
same download scripts (core/pipelines/nmass_download.py, mcx90_download.py —
called with pipe="margin_shortage" from the dashboard's own Download buttons,
same as CTCL's box). The MCX 90% file is additionally shared automatically
between the two boxes at 3 confirmed paired run-times (see mcx90_download.py).

ONLY changes from the uploaded script (per explicit instruction — do not
change any calculation/threshold/email/WhatsApp LOGIC):
  • BASE_DIR                          → .env (MARGIN_SHORTAGE_FOLDER)
  • SMTP credentials                  → .env (SENDER_EMAIL/PASSWORD, same account CTCL uses)
  • Zone / TO / CC email recipients   → .env (MS15_MAIN_TO/CC, MS15_<ZONE>_TO/CC)
  • Gupshup WhatsApp credentials      → .env (GUPSHUP_API_KEY, MS15_SOURCE_NUMBER, MS15_SRC_NAME, MS15_TEMPLATE_ID)
  • Mastertrust webpayout OAuth creds → .env (MT_WEBPAYOUT_USER/PASSWORD) — were hardcoded
    USERNAME="M@STER"/PASSWORD="M@$TER@TRUST" in the uploaded script
  • margin_alert_tracker.json (local file) → core/db.py + core/db_postgres.py
    get_margin_alert()/set_margin_alert() — centralized so the per-client-per-day
    WhatsApp dedup state is shared no matter which PC runs the pipeline
  • raise SystemExit(1) on pre-run check failure → return {"status": "prerun_failed", ...}
  • Wrapped the whole script in def run_margin_shortage_15min()
  • Added file-archival to Postgres during backup (same _archive_to_db pattern
    ctcl_vs_nmass.py already uses) — this is additive infrastructure, not a
    change to any calculation/alert/email logic.
  • MARGIN SHORTAGE ALERT image (ported from 15minutesfinal_whatsapp_UPDATED.py):
    ONE aggregate image of every account breaching MS15_SHORTAGE_THRESHOLD,
    sent to an internal RM/branch team list (MS15_SHORTAGE_WA_TO) — separate
    from run_whatsapp_alerts()'s per-client Gupshup messages above. Reuses
    core.notify.send_whatsapp() (the SAME Tata Tele WA_AUTH_TOKEN account
    already used by the Peak Margin report) instead of a second hand-rolled
    upload/send implementation — the uploaded script's own inline Tata Tele
    calls were replaced with that shared helper; nothing about the image's
    layout, the threshold logic, or the WhatsApp payload/template changed.
    Its own hardcoded TEST_MODE constant was dropped entirely — the dashboard's
    separate "🧪 Test run" feature (which used to run everything live except
    WhatsApp) has been removed per explicit request, so this pipeline no
    longer has any test/live branching anywhere: WhatsApp client alerts and
    the shortage alert image always send for real, exactly like the uploaded
    script always did.
Every column, threshold, filter, sort order, and control-flow decision below
is otherwise IDENTICAL to the uploaded script.
"""
import os
import glob
import json
import re
import shutil
import smtplib
import time
import traceback
import warnings
from datetime import datetime, time as dtime
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import polars as pl
import requests
from openpyxl import load_workbook
from PIL import Image, ImageDraw, ImageFont

# Re-read .env at import so a per-run subprocess picks up the CURRENT .env
# (email recipients edited via the dashboard, MS15 test-mode, etc.) instead of
# whatever the long-lived server process loaded at startup. override=True so the
# freshly-saved values win over the stale ones already in the server's environ.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(override=True)
except Exception:
    pass

warnings.filterwarnings("ignore", message="Workbook contains no default style")
pl.Config.set_tbl_rows(0)
pl.Config.set_verbose(False)


def _emails(key, default=None):
    raw = os.getenv(key, "")
    lst = [e.strip() for e in raw.split(",") if e.strip()]
    if lst:
        return lst
    return default or []


# ═══════════════════════════════════════════════════════════════════════════
# SAFE TEST RUN — a ONE-SHOT, self-discarding rehearsal of a full real run.
#
# This is NOT the old "test_mode" flag (that was removed entirely — it worked
# by SKIPPING sends, which proved nothing about whether the sends actually
# work). This does the opposite: every send still really happens, over the
# real Gupshup / Tata Tele / SMTP paths, with the real generated files and
# images attached — only the DESTINATION is swapped:
#
#   • every per-client WhatsApp  → MS15_TEST_WA_TO   (no client is messaged)
#   • the shortage alert image   → MS15_TEST_WA_TO   (not the RM/branch list)
#   • every email, main + zonal  → MS15_TEST_EMAIL_TO, with CC dropped
#   • the alert tracker          → NOT written (see below)
#
# Skipping the tracker write is the critical safety property. The tracker
# records "client X was already alerted at Y% today", and the next REAL run
# uses it to decide who to skip. If a test run wrote to it, every client the
# test "alerted" would be silently skipped by the next genuine run — a real
# margin breach could go out unalerted. So a safe test leaves it untouched
# and the next real run behaves exactly as if the test never happened.
#
# ARMING is one-shot by construction: the dashboard writes a marker file, and
# the pipeline DELETES that file as soon as it commits to the run. There is no
# way for the flag to survive into a second run, and no way to forget to turn
# it off. Deliberately consumed AFTER the pre-run file check passes, not
# before — if the run aborts because an input file is missing, nothing was
# sent anywhere, so the arm must still be there for the retry. Otherwise the
# operator's next click would silently be a LIVE run they believed was a test.
# ═══════════════════════════════════════════════════════════════════════════
def _safe_test_marker_path():
    base = os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    return os.path.join(base, ".ms15_safe_test")


def is_safe_test_armed():
    return os.path.exists(_safe_test_marker_path())


def arm_safe_test():
    p = _safe_test_marker_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat())
    return p


def disarm_safe_test():
    p = _safe_test_marker_path()
    try:
        if os.path.exists(p):
            os.remove(p)
            return True
    except Exception:
        pass
    return False


def _use_postgres():
    return os.getenv("USE_POSTGRES", "false").strip().lower() == "true"


def _get_margin_alert(client_code, alert_date):
    """Read the last MARGIN WARNING % that triggered a WhatsApp alert for this
    client today, from Postgres (or SQLite fallback) — replaces load_tracker()."""
    try:
        if _use_postgres():
            from core.db_postgres import get_margin_alert as _g
        else:
            from core.db import get_margin_alert as _g
        return _g(client_code, alert_date)
    except Exception as e:
        print(f"⚠️ Could not read alert tracker for {client_code}: {e}")
        return None


def _set_margin_alert(client_code, alert_date, pct):
    """Record that a WhatsApp alert was just sent — replaces save_tracker()."""
    try:
        if _use_postgres():
            from core.db_postgres import set_margin_alert as _s
        else:
            from core.db import set_margin_alert as _s
        _s(client_code, alert_date, pct)
    except Exception as e:
        print(f"⚠️ Could not write alert tracker for {client_code}: {e}")


def _log_whatsapp_send(run_date, run_number, client_code, mobile, pct):
    """Append a row to the per-send WhatsApp audit trail (powers the dashboard's
    'WhatsApp check' report). Best-effort — a DB hiccup must not break the run."""
    try:
        if _use_postgres():
            from core.db_postgres import log_whatsapp_send as _l
        else:
            from core.db import log_whatsapp_send as _l
        _l(run_date, run_number, client_code, mobile, pct)
    except Exception as e:
        print(f"⚠️ Could not log WhatsApp send for {client_code}: {e}")


def export_tracker_json(path=None):
    """Write the alert tracker back out as margin_alert_tracker.json — the exact
    format the ORIGINAL batch script reads.

    WHY: the console covers the runs up to 16:50. After that the night-shift
    operator runs the original script on a different machine, which still keeps
    its dedup state in a local margin_alert_tracker.json and has no access to
    this console's database. Without a bridge, that operator starts the evening
    with an EMPTY tracker and re-alerts every client the day shift already
    messaged — the exact problem the shared DB table was introduced to solve,
    reappearing at the handover point.

    So every run re-exports the table to JSON, which means the file sitting on
    disk when the 16:50 run finishes is always current and ready to hand over.
    Written every run rather than only on the last one deliberately: "the last
    run" is not knowable in advance (a run can be skipped, or the shift can end
    early), and an export is cheap.

    Format, matching the original script's load_tracker()/save_tracker():
        { "YYYY-MM-DD": { "CLIENTCODE": last_pct, ... }, ... }

    Best-effort — a failure here must never break a run that already sent its
    emails and alerts successfully.
    """
    path = path or os.getenv("MS15_TRACKER_JSON") or os.path.join(
        os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes"), "margin_alert_tracker.json")
    try:
        if _use_postgres():
            from core.db_postgres import raw_sql as _q
        else:
            from core.db import raw_sql as _q
        rows = _q("SELECT client_code, alert_date, last_pct FROM margin_alert_tracker")
        data = {}
        for r in rows or []:
            d = str(r.get("alert_date") or "")[:10]
            if not d:
                continue
            data.setdefault(d, {})[str(r.get("client_code"))] = r.get("last_pct")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # Write to a temp file then replace, so a reader on the other machine can
        # never pick up a half-written file mid-export.
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, path)
        _today_n = len(data.get(datetime.now().strftime("%Y-%m-%d"), {}))
        print(f"🗒️  Alert tracker exported for night-shift handover → {path} "
              f"({_today_n} client(s) alerted today, {len(data)} date(s) total)", flush=True)
        return {"ok": True, "path": path, "dates": len(data), "today": _today_n}
    except Exception as e:
        print(f"⚠️  Could not export alert tracker JSON to {path}: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"ok": False, "error": str(e), "path": path}


def _count_todays_backup_runs(base_dir, today_str):
    """Count how many backup run subfolders already exist for today. Used to
    derive the CURRENT run number BEFORE backup_and_cleanup creates this run's
    folder (existing count + 1 = this run's number), so WhatsApp sends can be
    tagged with the run they went out in. Matches backup_and_cleanup's own
    counting exactly, so the number is consistent with the backup folder name."""
    date_root = os.path.join(base_dir, "BACKUPFILES AUTOMATED", today_str)
    try:
        existing = [d for d in os.listdir(date_root)
                    if os.path.isdir(os.path.join(date_root, d)) and d.isdigit()]
        return (max(int(d) for d in existing) + 1) if existing else 1
    except Exception:
        return 1


def _archive_to_db(tag, kind, filepath, max_retries=3, retry_delay=2):
    """Copy a file's bytes into PostgreSQL with retry. Never raises — a DB
    hiccup must not break the run. Identical pattern to ctcl_vs_nmass._archive_to_db."""
    if not filepath or not os.path.exists(filepath):
        return False
    if not _use_postgres():
        print(f"[margin_shortage_15min] ⚠️  USE_POSTGRES=false — {os.path.basename(filepath)} NOT archived.")
        return False
    fname = os.path.basename(filepath)
    try:
        from core.db_postgres import save_run_file
        with open(filepath, "rb") as f:
            content = f.read()
    except Exception as e:
        print(f"[margin_shortage_15min] WARNING could not read {filepath} for archiving: {e}")
        return False
    for attempt in range(1, max_retries + 1):
        try:
            save_run_file("margin_shortage", kind, fname, content, tag=tag)
            print(f"[margin_shortage_15min] archived {fname} ({len(content):,} bytes) -> PostgreSQL")
            return True
        except Exception as e:
            if attempt < max_retries:
                print(f"[margin_shortage_15min] archive attempt {attempt}/{max_retries} failed for {fname}: {e} — retrying in {retry_delay}s")
                time.sleep(retry_delay)
            else:
                print(f"[margin_shortage_15min] WARNING: {fname} could NOT be archived after {max_retries} attempts: {e}")
    return False


def backfill_run_to_db(date_str, run_number):
    """Push an ALREADY-backed-up MS15 run folder into PostgreSQL — mirrors
    ctcl_vs_nmass.backfill_run_to_db() exactly, for when a run genuinely
    completed OUTSIDE this dashboard and its output files were then copied by
    hand into this machine's BACKUPFILES AUTOMATED\\<date>\\<run> folder, so
    _archive_to_db() (which only fires automatically inside this pipeline's
    own backup_and_cleanup()) never got a chance to run for those files.

    `date_str` — folder name exactly as on disk, DD-MM-YYYY (e.g. "21-07-2026").
    `run_number` — the two-digit run folder, e.g. 1 for ".../01".
    Safe to call more than once — re-archiving just adds another row."""
    base_dir = os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    run_folder = os.path.join(base_dir, "BACKUPFILES AUTOMATED", date_str, f"{int(run_number):02d}")
    if not os.path.isdir(run_folder):
        return {"ok": False, "error": f"Folder not found: {run_folder}"}
    db_tag = f"{date_str}/{int(run_number):02d}"
    archived, skipped = [], []
    for fname in sorted(os.listdir(run_folder)):
        fpath = os.path.join(run_folder, fname)
        if not os.path.isfile(fpath):
            continue
        kind = "output" if fname.startswith("Shortage_vs_CC_Deposit") else "input"
        if _archive_to_db(db_tag, kind, fpath):
            archived.append(fname)
        else:
            skipped.append(fname)
    return {"ok": True, "folder": run_folder, "tag": db_tag, "archived": archived, "skipped": skipped}


def run_margin_shortage_15min(run_number_override=None):
    """Thin wrapper around _run_impl — same AI (Groq) plain-English fallback
    translation pattern as run_ctcl_vs_nmass(). run_number_override is accepted
    for API symmetry with CTCL's retry mechanism but this pipeline's run number
    is always derived from the backup folder count (see backup_and_cleanup),
    matching the uploaded script's original logic exactly."""
    try:
        return _run_margin_shortage_15min_impl()
    except SystemExit:
        raise
    except Exception:
        from core.error_translator import translate_error
        tb_lines = traceback.format_exc().splitlines()
        for _l in tb_lines:
            print(_l, flush=True)
        _ai_msg = translate_error("Margin Shortage (15-min) processing", tb_lines)
        print(f"[FRIENDLY_MSG] {_ai_msg or 'Margin Shortage processing failed for an unrecognized reason — check the Logs button or contact tech support.'}", flush=True)
        raise


def _run_margin_shortage_15min_impl():
    # =====================================================
    # CONFIG
    # =====================================================
    BASE_DIR = os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    os.makedirs(BASE_DIR, exist_ok=True)
    # RISK_REPORT is a SHARED file dropped once into the common folder (not
    # downloaded per-pipeline-box) - same convention as CTCL vs NMASS
    # (core/pipelines/ctcl_vs_nmass.py's COMMON_DIR). Every other input file
    # (CM/FO/CO/NCDEX/MCX) is still only ever looked up in BASE_DIR.
    COMMON_DIR = os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    OUTPUT_FILE = os.path.join(BASE_DIR, "Shortage_vs_CC_Deposit.xlsx")
    today_ddmmyyyy = datetime.now().strftime("%d%m%Y")
    today_yyyymmdd = datetime.now().strftime("%Y%m%d")
    today_str = datetime.now().strftime("%Y-%m-%d")

    # =====================================================
    # WHATSAPP ALERT CONFIG
    # =====================================================
    TODAY = datetime.now().strftime("%Y-%m-%d")

    OAUTH_URL = "https://webpayout.mastertrust.co.in/api/Tradingdata/OAUTH_TOKEN"
    CLIENT_MOBILE_URL = "https://webpayout.mastertrust.co.in/api/Codecheck/LIV_CHECKMOBILE?clientcode={}"

    USERNAME = os.getenv("MT_WEBPAYOUT_USER", "M@STER")
    PASSWORD = os.getenv("MT_WEBPAYOUT_PASSWORD", "M@$TER@TRUST")

    GUPSHUP_URL = "https://api.gupshup.io/wa/api/v1/template/msg"
    GUPSHUP_API_KEY = os.getenv("GUPSHUP_API_KEY", "")

    SOURCE_NUMBER = os.getenv("MS15_SOURCE_NUMBER", "918054400120")
    SRC_NAME = os.getenv("MS15_SRC_NAME", "uuBSPHCbyQQJ5FyoAOK4kpl8")
    TEMPLATE_ID = os.getenv("MS15_TEMPLATE_ID", "9ad0e116-2b0d-4785-8d26-4dc5ae4a9bf5")

    PLAY_STORE_LINK = os.getenv("MS15_PLAY_STORE_LINK", "https://play.google.com/store/apps/details?id=com.mastertrust.agnik")
    APP_STORE_LINK = os.getenv("MS15_APP_STORE_LINK", "https://apps.apple.com/in/app/agnik/id6677025754")

    # =====================================================
    # MARGIN SHORTAGE IMAGE ALERT CONFIG (WhatsApp - Tata Tele)
    # =====================================================
    # New feature from 15minutesfinal_whatsapp_UPDATED.py: ONE aggregate image
    # (every account currently breaching SHORTAGE_THRESHOLD, worst first) sent
    # to an internal RM/branch team list — separate from run_whatsapp_alerts()
    # above (which messages individual CLIENTS via Gupshup). Reuses the SAME
    # Tata Tele WhatsApp Cloud API account already configured for the Peak
    # Margin report (WA_AUTH_TOKEN/WA_TEMPLATE in .env, via core.notify) —
    # NOT a second/duplicate integration — just a different recipient list
    # and caption, passed explicitly so it never touches WA_RECIPIENTS (that
    # one belongs to the unrelated Peak Margin feature).
    SHORTAGE_THRESHOLD = float(os.getenv("MS15_SHORTAGE_THRESHOLD", "-100000"))
    SHORTAGE_WA_TO = [n.strip() for n in os.getenv("MS15_SHORTAGE_WA_TO", "").split(",") if n.strip()]
    SHORTAGE_IMAGE_PATH = os.path.join(BASE_DIR, "Margin_Shortage_Alert.jpg")

    # =====================================================
    # HELPERS
    # =====================================================
    def extract_date_from_filename(filename):
        m1 = re.search(r"(?<!\d)(\d{2}\d{2}20\d{2})", filename)
        m2 = re.search(r"(20\d{6})", filename)
        return m1.group(1) if m1 else m2.group(1) if m2 else None

    def is_today_file(filename, fmt):
        d = extract_date_from_filename(filename)
        return d == (today_ddmmyyyy if fmt == "DDMMYYYY" else today_yyyymmdd)

    def find_file(starts_with=None, contains=None):
        # Search BASE_DIR first, then SA_COMMON_DIR (for shared files like
        # RISK_REPORT) - same order as ctcl_vs_nmass.py's find_file().
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

    def read_any(path):
        return pd.read_excel(path) if path.endswith(".xlsx") else pd.read_csv(path)

    def detect_header_excel(path, required_cols):
        raw = pd.read_excel(path, header=None)
        for i in range(10):
            row = raw.iloc[i].astype(str).str.strip()
            if all(c in row.values for c in required_cols):
                return pd.read_excel(path, header=i)
        raise Exception("Header not found")

    # =====================================================
    # PRE-RUN CHECK
    # =====================================================
    def is_modified_within_minutes(filepath, minutes):
        modified_time = datetime.fromtimestamp(os.path.getmtime(filepath))
        return (datetime.now() - modified_time).total_seconds() <= minutes * 60

    def pre_run_file_check():
        """
        Pre-run validation for required files.

        MCX file (random name):
          - Must contain today's YYYYMMDD
          - Must be modified within last 60 minutes
        """
        checks = {
            "CM_ClientLevelCollaterals": ("CM_ClientLevelCollaterals_*", "DDMMYYYY", None),
            "FO_ClientLevelCollaterals": ("FO_ClientLevelCollaterals_*", "DDMMYYYY", None),
            "CO_ClientLevelCollaterals": ("CO_ClientLevelCollaterals_*", "DDMMYYYY", None),
            "CLIENTNETEFFECTIVE_DEPOSITS": ("ClientNetEffectiveDeposits_*", "DDMMYYYY", None),
            "RISK_REPORT": ("RISK_REPORT_*", "DDMMYYYY", None),
            "MCX_RANDOM NAME": ("*.xlsx", "YYYYMMDD", 60),  # 60-minute freshness rule
        }

        missing_files = []
        wrong_date_files = []
        stale_files = []

        for name, (pattern, fmt, max_age_minutes) in checks.items():
            files = glob.glob(os.path.join(BASE_DIR, pattern))
            # RISK_REPORT is shared — also search SA_COMMON_DIR (it's dropped
            # once into the common folder, not downloaded per-box).
            if name == "RISK_REPORT" and os.path.isdir(COMMON_DIR):
                files.extend(glob.glob(os.path.join(COMMON_DIR, pattern)))

            if not files:
                missing_files.append(name)
                continue

            latest_file = max(files, key=os.path.getmtime)
            fname = os.path.basename(latest_file)

            if not is_today_file(fname, fmt):
                wrong_date_files.append(f"{name} → {fname}")
                continue

            if max_age_minutes is not None:
                if not is_modified_within_minutes(latest_file, max_age_minutes):
                    modified_at = datetime.fromtimestamp(
                        os.path.getmtime(latest_file)
                    ).strftime("%d-%m-%Y %H:%M:%S")
                    stale_files.append(
                        f"{name} → {fname} (Last Modified: {modified_at})"
                    )

        if missing_files or wrong_date_files or stale_files:
            print("\n❌ PRE-RUN FILE CHECK FAILED ❌\n")
            if missing_files:
                print("❌ Missing required files:")
                for f in missing_files:
                    print(f"   ❌ {f}")
            if wrong_date_files:
                print("\n❌ Files with wrong date:")
                for f in wrong_date_files:
                    print(f"   ❌ {f}")
            if stale_files:
                print("\n❌ Files older than allowed time (MCX must be ≤ 60 minutes):")
                for f in stale_files:
                    print(f"   ❌ {f}")
            print("\n❌ Execution aborted due to pre-run validation failure\n")
            return {"status": "prerun_failed", "missing": missing_files,
                    "wrong_date": wrong_date_files, "stale": stale_files}

        print("✅ PRE-RUN FILE CHECK PASSED")
        return None

    _prerun = pre_run_file_check()
    if _prerun is not None:
        return _prerun

    # ── SAFE TEST RUN: consume the arm (see the block above _safe_test_marker_path).
    # Consumed HERE, past the file check — an aborted run must not burn the arm,
    # or the operator's retry would silently be LIVE while they believed it was
    # a test. From this line on the run always reaches the send stage, so the
    # arm is spent exactly once whatever happens next.
    SAFE_TEST = is_safe_test_armed()
    if SAFE_TEST:
        disarm_safe_test()
    TEST_EMAIL_TO = _emails("MS15_TEST_EMAIL_TO", ["kartavya.dhillon@mastertrust.co.in"])
    # Gupshup wants a bare "91XXXXXXXXXX"; core.notify/Tata Tele wants "+91XXXXXXXXXX".
    _test_wa_raw = (os.getenv("MS15_TEST_WA_TO", "") or "+917889026088").strip()
    _test_wa_digits = "".join(ch for ch in _test_wa_raw if ch.isdigit())
    if len(_test_wa_digits) == 10:
        _test_wa_digits = "91" + _test_wa_digits
    TEST_WA_GUPSHUP = _test_wa_digits          # 917889026088
    TEST_WA_PLUS = "+" + _test_wa_digits       # +917889026088

    # A safe test whose redirect target is unusable is far more dangerous than
    # no test at all — a blank/garbage number could silently fall through to a
    # live destination. Refuse to run instead of guessing. The arm is already
    # consumed above, but nothing has been sent, so this is a clean abort.
    if SAFE_TEST and (len(_test_wa_digits) < 11 or not TEST_EMAIL_TO):
        print("\n❌ SAFE TEST RUN ABORTED — the redirect targets in .env are not usable.", flush=True)
        print(f"   MS15_TEST_WA_TO    = {_test_wa_raw!r}  (need a valid 10-digit or +91 number)", flush=True)
        print(f"   MS15_TEST_EMAIL_TO = {TEST_EMAIL_TO!r}", flush=True)
        print("   Nothing was sent. Fix .env, then arm the test again.\n", flush=True)
        return {"status": "safe_test_misconfigured",
                "wa_to": _test_wa_raw, "email_to": TEST_EMAIL_TO}

    if SAFE_TEST:
        print("", flush=True)
        print("=" * 72, flush=True)
        print("🧪  SAFE TEST RUN — THIS RUN IS ARMED FOR TESTING, NOTHING GOES LIVE", flush=True)
        print("=" * 72, flush=True)
        print(f"   • Per-client WhatsApp  → REDIRECTED to {TEST_WA_PLUS} (no client is messaged)", flush=True)
        print(f"   • Shortage alert image → REDIRECTED to {TEST_WA_PLUS} (not the RM/branch list)", flush=True)
        print(f"   • All emails (main + zonal) → {', '.join(TEST_EMAIL_TO)}  (CC dropped)", flush=True)
        print("   • Alert tracker         → NOT written, so the next real run is unaffected", flush=True)
        print("   • Arm is now SPENT — the next run will be fully LIVE.", flush=True)
        print("=" * 72, flush=True)
        print("", flush=True)

    def _to(key, default=None):
        """TO list for an email — swapped wholesale during a safe test run."""
        return list(TEST_EMAIL_TO) if SAFE_TEST else _emails(key, default)

    def _cc(key, default=None):
        """CC list for an email — dropped entirely during a safe test run, so a
        test can never copy a branch/compliance mailbox by accident."""
        return [] if SAFE_TEST else _emails(key, default)

    def _subj(s):
        return f"[TEST — IGNORE] {s}" if SAFE_TEST else s

    # =====================================================
    # CM
    # =====================================================
    cm_df = read_any(find_file("CM_ClientLevelCollaterals", today_ddmmyyyy))
    cm_df["CM_SUM"] = (
        pd.to_numeric(cm_df["Cash allocated (b)"], errors="coerce") +
        pd.to_numeric(cm_df["Cash-Others (c)"], errors="coerce") +
        pd.to_numeric(cm_df["Non-cash Securities (d)"], errors="coerce")
    )
    cm_df["CM_MIN_MARGIN"] = pd.to_numeric(cm_df["Minimum Margins (a)"], errors="coerce")

    cm_final = (
        cm_df
        .groupby("Client Code", as_index=False)[["CM_SUM", "CM_MIN_MARGIN"]]
        .sum()
    )

    # =====================================================
    # FO
    # =====================================================
    fo_df = read_any(find_file("FO_ClientLevelCollaterals", today_ddmmyyyy))
    fo_df["FO_SUM"] = (
        pd.to_numeric(fo_df["Cash allocated (b)"], errors="coerce") +
        pd.to_numeric(fo_df["Cash-others (c)"], errors="coerce") +
        pd.to_numeric(fo_df["Non-cash Securities (d)"], errors="coerce")
    )
    fo_df["FO_MIN_MARGIN"] = pd.to_numeric(fo_df["Minimum Margins (a)"], errors="coerce")

    fo_final = (
        fo_df
        .groupby("Client Code", as_index=False)[["FO_SUM", "FO_MIN_MARGIN"]]
        .sum()
    )

    # =====================================================
    # CO
    # =====================================================
    co_df = read_any(find_file("CO_ClientLevelCollaterals", today_ddmmyyyy))
    co_df["CO_SUM"] = (
        pd.to_numeric(co_df["Cash allocated (b)"], errors="coerce") +
        pd.to_numeric(co_df["Cash-others (c)"], errors="coerce") +
        pd.to_numeric(co_df["Non-cash Securities (d)"], errors="coerce")
    )
    co_df["CO_MIN_MARGIN"] = pd.to_numeric(
        co_df["Minimum Margins (a)"], errors="coerce"
    )

    co_final = (
        co_df
        .groupby("Client Code", as_index=False)[["CO_SUM", "CO_MIN_MARGIN"]]
        .sum()
    )

    # =====================================================
    # CLIENT NET
    # =====================================================
    dep_df = read_any(find_file("ClientNetEffectiveDeposits", today_ddmmyyyy))
    dep_df["DEP_SUM"] = pd.to_numeric(
        dep_df["Total Collateral (Cash and Non Cash)"], errors="coerce"
    )
    dep_df["CLIENTNET_MARGIN"] = pd.to_numeric(dep_df["Margin"], errors="coerce")

    dep_final = (
        dep_df
        .groupby("Client/CP Code", as_index=False)[["DEP_SUM", "CLIENTNET_MARGIN"]]
        .sum()
    )
    dep_final.columns = ["Client Code", "DEP_SUM", "CLIENTNET_MARGIN"]

    # =====================================================
    # MCX (BOTH VALUES FROM SAME FILE)
    # =====================================================
    def find_mcx_file():
        required_cols = {
            "Client Code",
            "Client Margin Limit",
            "Margin Utilization"
        }

        for f in os.listdir(BASE_DIR):
            if not f.endswith(".xlsx"):
                continue
            if today_yyyymmdd not in f:
                continue

            path = os.path.join(BASE_DIR, f)

            try:
                raw = pd.read_excel(path, header=None)

                for i in range(15):
                    row = raw.iloc[i].astype(str).str.strip()
                    if required_cols.issubset(set(row.values)):
                        return path

            except Exception:
                continue

        raise FileNotFoundError(
            "❌ MCX file with required columns "
            "(Client Code, Client Margin Limit, Margin Utilization) not found"
        )

    mcx_file = find_mcx_file()
    mcx_df = detect_header_excel(
        mcx_file,
        ["Client Code", "Client Margin Limit", "Margin Utilization"]
    )

    mcx_df["Client Margin Limit"] = (
        mcx_df["Client Margin Limit"].astype(str).str.replace(",", "", regex=False)
    )
    mcx_df["Margin Utilization"] = (
        mcx_df["Margin Utilization"].astype(str).str.replace(",", "", regex=False)
    )
    mcx_df["Client Margin Limit"] = pd.to_numeric(
        mcx_df["Client Margin Limit"], errors="coerce"
    )
    mcx_df["Margin Utilization"] = pd.to_numeric(
        mcx_df["Margin Utilization"], errors="coerce"
    )

    mcx_final = (
        mcx_df
        .groupby("Client Code", as_index=False)[
            ["Client Margin Limit", "Margin Utilization"]
        ]
        .sum()
    )
    mcx_final.columns = ["Client Code", "MARGIN_LIMIT", "MCX_MARGIN_UTIL"]

    # =====================================================
    # MERGE & CALCULATIONS
    # =====================================================
    final_df = (
        cm_final
        .merge(fo_final, on="Client Code", how="outer")
        .merge(co_final, on="Client Code", how="outer")
        .merge(dep_final, on="Client Code", how="outer")
        .merge(mcx_final, on="Client Code", how="outer")
    ).fillna(0)

    final_df["DEPOSIT NMASS"] = (
        final_df["CM_SUM"] +
        final_df["FO_SUM"] +
        final_df["CO_SUM"] +
        final_df["DEP_SUM"] +
        final_df["MARGIN_LIMIT"]
    )

    final_df["TOTAL MARGIN"] = (
        final_df["CM_MIN_MARGIN"] +
        final_df["FO_MIN_MARGIN"] +
        final_df["CO_MIN_MARGIN"] +
        final_df["CLIENTNET_MARGIN"] +
        final_df["MCX_MARGIN_UTIL"]
    )

    final_df["DIFFERENCE(F-G)"] = (
        final_df["DEPOSIT NMASS"] - final_df["TOTAL MARGIN"]
    ).round(2)

    final_df = final_df.rename(columns={"Client Code": "ACCOUNTID"})

    final_df["CM SEGMENT MARGIN"] = final_df["CM_MIN_MARGIN"]

    final_df["FINAL SHORT CM ADJ(H+J)"] = (final_df["DIFFERENCE(F-G)"] + final_df["CM_MIN_MARGIN"])

    # =====================================================
    # DERIVATIVE RISK COLUMNS
    # =====================================================
    final_df["DERIVATIVE MARGIN USED"] = (
        final_df["TOTAL MARGIN"] - final_df["CM SEGMENT MARGIN"]
    ).round(2)

    final_df["MARGIN WARNING %"] = np.where(
        final_df["DEPOSIT NMASS"] > 0,
        (final_df["DERIVATIVE MARGIN USED"] / final_df["DEPOSIT NMASS"]) * 100,
        0
    )
    final_df["MARGIN WARNING %"] = final_df["MARGIN WARNING %"].round(2)

    # =====================================================
    # RISK REPORT JOIN
    # =====================================================
    risk_file = find_file(starts_with="RISK_REPORT", contains=today_ddmmyyyy)
    if not risk_file:
        raise FileNotFoundError("❌ RISK_REPORT file not found for today")

    risk_pl = pl.read_csv(risk_file, infer_schema_length=0, ignore_errors=True)
    risk_pl = risk_pl.select([
        pl.col("Zone"),
        pl.col("TRADING_SOFTWARE_TYPE"),
        pl.col("CPCode"),
        pl.col("PISAccount"),
        pl.col("ACCOUNTID"),
    ])

    final_pl = pl.from_pandas(final_df)
    final_pl = final_pl.join(risk_pl, on="ACCOUNTID", how="left")

    final_pl = final_pl.with_columns([
        pl.col("DEPOSIT NMASS").round(2),
        pl.col("TOTAL MARGIN").round(2),
        pl.col("DIFFERENCE(F-G)").round(2),
    ])

    # DROP SUB ACCOUNTS
    final_pl = final_pl.filter(
        ~pl.col("ACCOUNTID").cast(pl.Utf8).str.to_uppercase().str.starts_with("SUB")
    )

    final_df = final_pl.to_pandas()

    # SORT BY MARGIN WARNING % (LARGEST TO SMALLEST)
    final_df = final_df.sort_values(
        by="MARGIN WARNING %",
        ascending=False
    ).reset_index(drop=True)

    # =====================================================
    # REMARKS LOGIC (UNCHANGED)
    # =====================================================
    for col in ["Zone", "ACCOUNTID", "DEPOSIT NMASS", "DIFFERENCE(F-G)"]:
        if col not in final_df.columns:
            final_df[col] = ""

    final_df["Zone"] = (
        final_df["Zone"]
        .replace({None: "", "None": "", "nan": "", "NaN": ""})
        .fillna("")
        .astype(str)
        .str.strip()
    )

    final_df["ACCOUNTID"] = final_df["ACCOUNTID"].astype(str).str.strip()
    final_df["REMARKS"] = ""

    final_df.loc[
        (final_df["REMARKS"] == "") &
        (final_df["DIFFERENCE(F-G)"] < 0),
        "REMARKS"
    ] = "SHORTFALL"

    final_df.loc[
        (final_df["REMARKS"] == "") &
        (final_df["DIFFERENCE(F-G)"] == 0),
        "REMARKS"
    ] = "OK - No Difference"

    final_df.loc[
        (final_df["REMARKS"] == "") &
        (final_df["DIFFERENCE(F-G)"] > 0),
        "REMARKS"
    ] = "OK - Excess Difference"

    # =====================================================
    # FINAL COLUMN ORDER
    # =====================================================
    final_df = final_df[[
        "Zone",
        "TRADING_SOFTWARE_TYPE",
        "CPCode",
        "PISAccount",
        "ACCOUNTID",
        "DEPOSIT NMASS",
        "TOTAL MARGIN",
        "DIFFERENCE(F-G)",
        "REMARKS",
        "CM SEGMENT MARGIN",
        "FINAL SHORT CM ADJ(H+J)",
        "DERIVATIVE MARGIN USED",
        "MARGIN WARNING %"
    ]]

    # =====================================================
    # WHATSAPP SUPPORT FUNCTIONS
    # =====================================================
    def get_oauth_token():
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {
            "grant_type": "password",
            "username": USERNAME,
            "password": PASSWORD
        }
        response = requests.post(OAUTH_URL, headers=headers, data=payload, timeout=30)
        response.raise_for_status()
        token_json = response.json()
        return token_json["body"]["data"][0]["ACCESS_TOKEN"]

    def get_mobile(client_code, token):
        headers = {"Authorization": f"Bearer {token}"}
        url = CLIENT_MOBILE_URL.format(client_code)
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 401:
            token = get_oauth_token()
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(url, headers=headers, timeout=30)

        response.raise_for_status()
        data = response.json()
        mobile = data["body"]["data"]["data"][0]["MOBILE"]

        if not mobile:
            return None

        mobile = str(mobile).strip().replace(" ", "").replace("-", "")
        if not mobile.startswith("91"):
            mobile = "91" + mobile
        return mobile

    def send_whatsapp(client_code, mobile, margin_percent):
        # SAFE TEST RUN: the message is still really built and really sent
        # through Gupshup with this client's real code/margin in the template —
        # only the destination is swapped, so you see exactly what the client
        # would have seen, and the client sees nothing.
        if SAFE_TEST:
            # The client's own number is deliberately NOT printed here either —
            # saying it was suppressed is the useful part; echoing the number
            # into the log would defeat the point of suppressing it.
            print(f"  🧪 [TEST] {client_code} @ {margin_percent}% — client's own number "
                  f"SUPPRESSED, sending to {TEST_WA_GUPSHUP} instead", flush=True)
            mobile = TEST_WA_GUPSHUP
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "apikey": GUPSHUP_API_KEY
        }
        template_payload = {
            "id": TEMPLATE_ID,
            "params": [
                client_code,
                client_code,
                f"{margin_percent}%",
                PLAY_STORE_LINK,
                APP_STORE_LINK
            ]
        }
        payload = {
            "channel": "whatsapp",
            "source": SOURCE_NUMBER,
            "destination": mobile,
            "src.name": SRC_NAME,
            "template": __import__("json").dumps(template_payload)
        }
        try:
            response = requests.post(
                GUPSHUP_URL,
                headers=headers,
                data=urlencode(payload),
                timeout=30)
            response.raise_for_status()
            resp_json = response.json()
            # Keep the exact anchor phrase "WhatsApp submitted for" (core/
            # pipeline_box.py's _WA_OK matches on it), then append the detail
            # after an em-dash so the dashboard strip can name WHO was messaged
            # instead of a bare "WhatsApp submitted successfully".
            # DELIBERATELY NO MOBILE NUMBER in any of these lines. Identifying the
            # client by code is enough to know who was messaged, and these lines go
            # to the on-screen strips AND the saved run log — neither is a place a
            # client's personal phone number belongs. (The internal RM/branch numbers
            # for the shortage image ARE printed, further down: those are our own
            # team's numbers, not client data.)
            if resp_json.get("status") == "submitted":
                _tag = " [TEST → not the client]" if SAFE_TEST else ""
                print(f"WhatsApp submitted for {client_code} — client {client_code} · "
                      f"margin {margin_percent}%{_tag}", flush=True)
                return True
            else:
                print(f"WhatsApp API error for {client_code} — client {client_code} · "
                      f"{resp_json}", flush=True)
                return False
        except Exception as e:
            print(f"WhatsApp error for {client_code} — client {client_code} · "
                  f"{type(e).__name__}: {e}", flush=True)
            return False

    # =====================================================
    # RUN WHATSAPP ALERTS USING final_df
    # =====================================================
    def run_whatsapp_alerts(df, run_number):
        # Byte-for-byte the uploaded script's behaviour, PLUS a per-send
        # audit-log row (powers the dashboard's "WhatsApp check" report).
        print("\n📲 Running WhatsApp Margin Alerts...\n")

        def _send(client_code, margin):
            mobile = get_mobile(client_code, token)
            if not mobile:
                print(f"  ⚠️ No mobile found for {client_code} — skipped.")
                return
            if send_whatsapp(client_code, mobile, margin):
                # SAFE TEST RUN: deliberately do NOT touch the tracker. It drives
                # "who already got an alert today", so a test writing to it would
                # make the next REAL run skip these clients — a genuine margin
                # breach could then go out unalerted. The audit log row is skipped
                # for the same reason (it would show a test send as a real one in
                # the WhatsApp-check report).
                if SAFE_TEST:
                    return
                _set_margin_alert(client_code, TODAY, margin)
                _log_whatsapp_send(TODAY, run_number, client_code, mobile, margin)

        try:
            token = get_oauth_token()
        except Exception as e:
            print("OAuth failed:", e)
            return

        for row in df.to_dict("records"):
            client_code = None
            try:
                client_code = str(row["ACCOUNTID"]).strip()

                try:
                    margin = float(row["MARGIN WARNING %"])
                except Exception:
                    continue

                # 🔥 THRESHOLD (unchanged)
                if margin <= 105:
                    continue

                last_sent = _get_margin_alert(client_code, TODAY)

                # First alert of the day
                if last_sent is None:
                    _send(client_code, margin)

                # Bi-directional ±10% movement logic
                else:
                    if abs(margin - last_sent) >= 10:
                        _send(client_code, margin)

                time.sleep(0.5)

            except Exception as e:
                print(f"Error processing client {client_code}: {e}")

        print("\n✅ WhatsApp Alert Process Completed\n")

    # =====================================================
    # MARGIN SHORTAGE IMAGE ALERT (WhatsApp - Tata Tele, internal RM list)
    # =====================================================
    def _get_font(size, bold=False):
        """Try Windows fonts first, then fall back gracefully."""
        candidates = (
            ["arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"]
            if bold else
            ["arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"]
        )
        for path in candidates:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _fmt_num(v):
        """Format numbers with thousands separator + 2 decimals; leave text as-is."""
        try:
            return f"{float(v):,.2f}"
        except Exception:
            return str(v)

    def generate_shortage_image(rows, output_path):
        """
        Creates a professional, warning-styled alert image.
          - Dark navy header with a warning-triangle icon + title/subtitle
          - Amber banner showing the capture date & time
          - 4-column table: ACCOUNTID, DEPOSIT NMASS, TOTAL MARGIN, DIFFERENCE(F-G)

        rows: list of dicts, each with keys:
              ACCOUNTID, DEPOSIT NMASS, TOTAL MARGIN, DIFFERENCE(F-G)
        """
        NAVY       = "#0F2A43"
        AMBER      = "#F5A623"
        AMBER_SOFT = "#FFF4E0"
        AMBER_LINE = "#E8B04B"
        HEAD_BG    = "#1D3A57"
        ROW_A      = "#FFFFFF"
        ROW_B      = "#F4F7FA"
        BORDER     = "#D0D9E2"
        TEXT_DARK  = "#1B2A38"
        NEG        = "#C0392B"
        RED        = "#C0392B"

        f_title  = _get_font(34, True)
        f_sub    = _get_font(17, False)
        f_warn   = _get_font(17, True)
        f_colhdr = _get_font(18, True)
        f_cell   = _get_font(19, True)

        cols  = ["ACCOUNTID", "DEPOSIT NMASS", "TOTAL MARGIN", "DIFFERENCE(F-G)"]
        col_w = [180, 250, 250, 250]
        W = sum(col_w)

        header_h = 112
        warn_h   = 54
        colhdr_h = 50
        row_h    = 50
        foot_h   = 44

        n = len(rows)
        H = header_h + warn_h + colhdr_h + row_h * n + foot_h

        img = Image.new("RGB", (W, H), "white")
        d = ImageDraw.Draw(img)

        d.rectangle([0, 0, W, header_h], fill=NAVY)
        d.rectangle([0, 0, 10, header_h], fill=AMBER)  # left accent bar

        cx, cy, r = 68, header_h // 2, 28
        d.polygon([(cx, cy - r), (cx - r, cy + r * 0.75), (cx + r, cy + r * 0.75)], fill=AMBER)
        d.rectangle([cx - 3, cy - 11, cx + 3, cy + 5], fill=NAVY)
        d.ellipse([cx - 3, cy + 9, cx + 3, cy + 15], fill=NAVY)

        d.text((116, header_h // 2 - 20), "MARGIN SHORTAGE ALERT", font=f_title, fill="white")
        d.text((118, header_h // 2 + 16),
               "Risk Management  |  Immediate attention required",
               font=f_sub, fill="#AEC3D6")

        y = header_h
        d.rectangle([0, y, W, y + warn_h], fill=AMBER_SOFT)
        d.rectangle([0, y, W, y + 4], fill=AMBER)
        d.rectangle([0, y + warn_h - 1, W, y + warn_h], fill=AMBER_LINE)
        captured_at = datetime.now().strftime("Captured on  %d-%m-%Y  |  %H:%M:%S")
        d.text((W // 2, y + warn_h // 2), captured_at, font=f_warn, fill="#8A5A00", anchor="mm")
        y += warn_h

        d.rectangle([0, y, W, y + colhdr_h], fill=HEAD_BG)
        x = 0
        for j, col in enumerate(cols):
            d.text((x + col_w[j] // 2, y + colhdr_h // 2), col, font=f_colhdr, fill="white", anchor="mm")
            x += col_w[j]
        y += colhdr_h

        table_top = y
        for i, row in enumerate(rows):
            d.rectangle([0, y, W, y + row_h], fill=(ROW_A if i % 2 == 0 else ROW_B))
            d.rectangle([0, y, 6, y + row_h], fill=RED)  # red marker
            vals = [
                str(row["ACCOUNTID"]),
                _fmt_num(row["DEPOSIT NMASS"]),
                _fmt_num(row["TOTAL MARGIN"]),
                _fmt_num(row["DIFFERENCE(F-G)"]),
            ]
            x = 0
            for j, v in enumerate(vals):
                colr = TEXT_DARK
                if j == 3:
                    try:
                        colr = NEG if float(row["DIFFERENCE(F-G)"]) < 0 else TEXT_DARK
                    except Exception:
                        pass
                if j == 0:
                    d.text((x + col_w[j] // 2, y + row_h // 2), v, font=f_cell, fill=colr, anchor="mm")
                else:
                    d.text((x + col_w[j] - 14, y + row_h // 2), v, font=f_cell, fill=colr, anchor="rm")
                x += col_w[j]
            y += row_h

        yy = table_top
        for _ in range(n + 1):
            d.line([0, yy, W, yy], fill=BORDER, width=1)
            yy += row_h
        xx = 0
        for j in range(len(col_w) + 1):
            d.line([xx, table_top, xx, table_top + row_h * n], fill=BORDER, width=1)
            if j < len(col_w):
                xx += col_w[j]
        d.rectangle([0, table_top, W - 1, table_top + row_h * n], outline=BORDER, width=1)

        d.rectangle([0, y, W, y + foot_h], fill="#F0F3F6")
        d.rectangle([0, 0, W - 1, H - 1], outline="#C4CDD6", width=1)

        img.save(output_path, quality=95)
        print(f"Margin shortage alert image created: {output_path} ({n} accounts)")

    def run_shortage_image_alert(df):
        """
        Filters accounts where DIFFERENCE(F-G) <= SHORTAGE_THRESHOLD, builds the
        4-column MARGIN SHORTAGE ALERT image for those accounts, and sends it to
        the internal RM/branch team list (MS15_SHORTAGE_WA_TO) — NOT clients.

        Reuses core.notify.send_whatsapp() (the SAME Tata Tele account already
        wired up for the Peak Margin report) instead of a second, duplicate
        upload/send implementation — this only supplies its own image, caption,
        and recipient list.
        """
        shortage_df = df.loc[
            df["DIFFERENCE(F-G)"] <= SHORTAGE_THRESHOLD,
            ["ACCOUNTID", "DEPOSIT NMASS", "TOTAL MARGIN", "DIFFERENCE(F-G)"]
        ].sort_values("DIFFERENCE(F-G)")   # most negative first

        if shortage_df.empty:
            print(f"No accounts breaching shortage threshold ({SHORTAGE_THRESHOLD}); "
                  "no shortage alert image sent")
            return

        rows = shortage_df.to_dict("records")
        generate_shortage_image(rows, SHORTAGE_IMAGE_PATH)

        # SAFE TEST RUN: the SAME image, built from the SAME live data, is really
        # sent over the real Tata Tele path — just to the test number instead of
        # the RM/branch list, so the image itself can be checked end to end.
        _wa_to = [TEST_WA_PLUS] if SAFE_TEST else SHORTAGE_WA_TO

        if not _wa_to:
            print("⚠️  MS15_SHORTAGE_WA_TO is empty in .env — shortage alert image "
                  "generated but NOT sent (no recipients configured).")
            return

        from core.notify import send_whatsapp as _notify_send_whatsapp
        _caption = "MARGIN SHORTAGE ALERT"
        result = _notify_send_whatsapp(SHORTAGE_IMAGE_PATH, _caption, _wa_to)

        # Report PER RECIPIENT, by number. This send previously printed only
        # "sent to 5 recipient(s)" — a count, not a list — and worse, that line
        # matched none of pipeline_box's WhatsApp patterns, so the aggregate
        # shortage image produced NO dashboard strip at all. If it silently
        # failed for one number out of five, nothing on screen said so.
        # The anchor phrase "WhatsApp submitted for" is what _WA_OK matches;
        # "WhatsApp error for" is what _WA_BAD matches.
        _tag = " [TEST]" if SAFE_TEST else ""
        _results = result.get("results") or []
        if _results:
            for _r in _results:
                _num = _r.get("to", "?")
                if _r.get("ok"):
                    print(f"WhatsApp submitted for {_num} — MARGIN SHORTAGE ALERT image "
                          f"({len(rows)} accounts) → {_num}{_tag}", flush=True)
                else:
                    print(f"WhatsApp error for {_num} — MARGIN SHORTAGE ALERT image → "
                          f"{_num} · {_r.get('detail') or 'send failed'}", flush=True)
        else:
            # No per-recipient breakdown came back (e.g. the image upload itself
            # failed before any send was attempted) — report the whole batch.
            print(f"WhatsApp error for shortage image — MARGIN SHORTAGE ALERT image → "
                  f"{', '.join(_wa_to)} · {result.get('error') or result}", flush=True)

        _okn = sum(1 for _r in _results if _r.get("ok"))
        if result.get("ok"):
            print(f"✅ Margin shortage alert image ({len(rows)} accounts) sent to "
                  f"{_okn}/{len(_wa_to)}: {', '.join(_wa_to)}", flush=True)
        else:
            print(f"⚠️  Margin shortage alert image send had issues: {result}", flush=True)

    # =====================================================
    # WRITE EXCEL
    # =====================================================
    final_df.to_excel(OUTPUT_FILE, index=False)

    wb = load_workbook(OUTPUT_FILE)
    ws = wb.active
    for col in ws.columns:
        col_letter = col[0].column_letter
        header = str(col[0].value)
        if header == "CPCode":
            ws.column_dimensions[col_letter].width = len(header) + 2
            continue
        max_len = len(header)
        for cell in col[1:]:
            if cell.value is not None:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max_len + 2
    wb.save(OUTPUT_FILE)
    print("✅ OUTPUT FILE created successfully")

    run_shortage_image_alert(final_df)   # MARGIN SHORTAGE ALERT image -> WhatsApp (internal RM list)

    cm_file = os.path.basename(find_file("CM_ClientLevelCollaterals", today_ddmmyyyy))
    fo_file = os.path.basename(find_file("FO_ClientLevelCollaterals", today_ddmmyyyy))
    co_file = os.path.basename(find_file("CO_ClientLevelCollaterals", today_ddmmyyyy))
    clientnet_file = os.path.basename(find_file("ClientNetEffectiveDeposits", today_ddmmyyyy))
    risk_file_name = os.path.basename(risk_file)
    mcx_file_name = os.path.basename(mcx_file)

    # =====================================================
    # EMAIL CONFIG & SEND
    # =====================================================
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_EMAIL = os.getenv("SENDER_EMAIL")
    SMTP_PASSWORD = os.getenv("SENDER_PASSWORD")

    def send_email(subject, body, to_list, cc_list, attachment, label="Email"):
        # `label` names WHICH of the five emails this is (main summary, or one of
        # the four zone files). Without it every send printed the identical
        # "✅ Email sent successfully", so the dashboard showed three or four
        # indistinguishable green strips and there was no way to tell which zone
        # had gone out — or, on a partial failure, which one hadn't.
        attempt = 1
        while True:  # Infinite retry (unchanged)
            try:
                msg = MIMEMultipart()
                msg["From"] = SMTP_EMAIL
                msg["To"] = ", ".join(to_list)
                msg["Cc"] = ", ".join(cc_list)
                msg["Subject"] = subject
                msg.attach(MIMEText(body, "html"))

                with open(attachment, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f'attachment; filename="{os.path.basename(attachment)}"'
                )
                msg.attach(part)

                recipients = to_list + cc_list

                server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
                server.starttls()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg, to_addrs=recipients)
                server.quit()

                # Keep the exact phrase "Email sent successfully" — core/pipeline_box.py's
                # _EMAIL_OK regex anchors on it — then append the detail after it.
                _rcpt = ", ".join(to_list) if to_list else "(no TO)"
                _cc_note = f" · cc {', '.join(cc_list)}" if cc_list else ""
                _att = os.path.basename(attachment) if attachment else ""
                print(f"✅ Email sent successfully — {label} → {_rcpt}{_cc_note}"
                      + (f" [{_att}]" if _att else ""), flush=True)
                break
            except Exception as e:
                print(f"❌ Email failed — {label} → {', '.join(to_list) or '(no TO)'} "
                      f"(attempt {attempt})", flush=True)
                print(str(e))
                traceback.print_exc()
                attempt += 1
                print("🔁 Retrying in 5 seconds...\n")
                time.sleep(5)

    email_body = f"""
Dear Team,<br><br>

Please find attached the <b>Margin Shortage Monitoring Report</b> generated as per the latest available data.<br><br>

The report provides a consolidated view of:<br>
• Client-wise deposits across CM, FO, NCDEX & MCX<br>
• Files used:<br>
&nbsp;&nbsp;&nbsp;&nbsp;– <b>CM:</b> {cm_file}<br>
&nbsp;&nbsp;&nbsp;&nbsp;– <b>FO:</b> {fo_file}<br>
&nbsp;&nbsp;&nbsp;&nbsp;– <b>CO:</b> {co_file}<br>
&nbsp;&nbsp;&nbsp;&nbsp;– <b>NCDEX:</b> {clientnet_file}<br>
&nbsp;&nbsp;&nbsp;&nbsp;– <b>MCX 90%:</b> {mcx_file_name}<br>
• Total margin utilization<br>
• Net difference highlighting <b>shortfall / excess</b><br><br>
• <b>Derivative Margin Used</b> – Margin utilized in FO / NCDEX / MCX segments (excluding CM segment margin)<br>
• <b>Margin Warning %</b> – Percentage of total available deposit utilized towards derivative margins<br>
&nbsp;&nbsp;&nbsp;&nbsp;(Calculated as Derivative Margin Used ÷ Total Deposit NMASS × 100)<br><br>
<br><br>
<b>WhatsApp Margin Alert Logic:</b><br>
• Automated WhatsApp alerts are triggered for clients whose <b>Margin Warning % exceeds 105%</b>.<br>
• The first alert is sent when the threshold is breached.<br>
• Subsequent alerts are sent only if the Margin Warning % changes by <b>±10% or more</b> compared to the last alert sent on the same day.<br>
• No alerts are sent if the Margin Warning % is 105% or below.<br>
• This logic ensures timely risk communication while preventing repetitive alerts for minor fluctuations.<br>
<br><br>

Best regards,<br>
<b>Kartavya Dhillon</b><br>
Master Capital Services Ltd<br>
📞 011-66331000 | Ext. 84
"""

    send_email(
        subject=_subj("Margin Shortage Monitoring"),
        body=email_body,
        to_list=_to("MS15_MAIN_TO", ["rms_del@mastertrust.co.in"]),
        cc_list=_cc("MS15_MAIN_CC"),
        attachment=OUTPUT_FILE,
        label="MAIN summary (all zones)"
    )

    # =====================================================
    # ZONE-WISE SHORTFALL FILES & EMAILS
    # =====================================================
    # NOTE: _to()/_cc() (not _emails()) — during a SAFE TEST RUN every zone's TO
    # collapses to the test mailbox and every CC is dropped, so no branch or
    # compliance mailbox can be copied by a test.
    ZONE_MAPPING = {
        "DELH": {
            "zones": ["ZODELH", "ZOMT", "ZOCDR"],
            "suffix": "DELH",
            "to": _to("MS15_DELH_TO", ["rms_del@mastertrust.co.in"]),
            "cc": _cc("MS15_DELH_CC"),
        },
        "LUDH": {
            "zones": ["ZO", "ZOLUDH"],
            "suffix": "LUDH",
            "to": _to("MS15_LUDH_TO", ["ludhianacompliance@mastertrust.co.in", "amandeep.singh2@mastertrust.co.in", "kuljit.singh@mastertrust.co.in"]),
            "cc": _cc("MS15_LUDH_CC", ["sandeep.kumar2@mastertrust.co.in", "amit.pundir@mastertrust.co.in", "sumit.aggarwal@mastertrust.co.in", "vipin.rai@mastertrust.co.in"]),
        },
        "MUMB": {
            "zones": ["ZOMUMB"],
            "suffix": "MUMB",
            "to": _to("MS15_MUMB_TO", ["pradip.mandavkar@mastertrust.co.in", "santosh.tajane@mastertrust.co.in"]),
            "cc": _cc("MS15_MUMB_CC", ["sandeep.kumar2@mastertrust.co.in", "amit.pundir@mastertrust.co.in", "sumit.aggarwal@mastertrust.co.in", "vipin.rai@mastertrust.co.in"]),
        },
        "CHND": {
            "zones": ["ZOCHND"],
            "suffix": "CHND",
            "to": _to("MS15_CHND_TO", ["rms_chd@mastertrust.co.in"]),
            "cc": _cc("MS15_CHND_CC", ["sandeep.kumar2@mastertrust.co.in", "amit.pundir@mastertrust.co.in", "sumit.aggarwal@mastertrust.co.in", "vipin.rai@mastertrust.co.in"]),
        }
    }

    # Filter ONLY FINAL SHORT CM ADJ < 0
    short_df = final_df[final_df["FINAL SHORT CM ADJ(H+J)"] < 0].copy()

    if short_df.empty:
        print("ℹ️ No shortfall data found overall")
    else:
        for zone_key, cfg in ZONE_MAPPING.items():
            zone_df = short_df[short_df["Zone"].isin(cfg["zones"])].copy()

            if zone_df.empty:
                print(f"ℹ️ No data found for {zone_key} zone")
                continue

            zone_file = os.path.join(
                BASE_DIR,
                f"Shortage_vs_CC_Deposit_{cfg['suffix']}.xlsx"
            )
            zone_df.to_excel(zone_file, index=False)

            wb = load_workbook(zone_file)
            ws = wb.active
            for col in ws.columns:
                col_letter = col[0].column_letter
                header = str(col[0].value)
                max_len = len(header)
                for cell in col[1:]:
                    if cell.value is not None:
                        max_len = max(max_len, len(str(cell.value)))
                ws.column_dimensions[col_letter].width = max_len + 2
            wb.save(zone_file)

            print(f"✅ Zone file created: {zone_file}")

            zone_email_body = f"""
Dear Team,<br><br>

Please find attached the <b>Margin Shortage Monitoring Report</b> generated as per the latest available data.<br><br>

The report provides a consolidated view of:<br>
• Client-wise deposits across CM, FO, NCDEX & MCX<br>
• Files used:<br>
&nbsp;&nbsp;&nbsp;&nbsp;– <b>CM:</b> {cm_file}<br>
&nbsp;&nbsp;&nbsp;&nbsp;– <b>FO:</b> {fo_file}<br>
&nbsp;&nbsp;&nbsp;&nbsp;– <b>CO:</b> {co_file}<br>
&nbsp;&nbsp;&nbsp;&nbsp;– <b>NCDEX:</b> {clientnet_file}<br>
&nbsp;&nbsp;&nbsp;&nbsp;– <b>MCX 90% :</b> {mcx_file_name}<br>
• Total margin utilization<br>
• Net difference highlighting <b>shortfall / excess</b><br><br>
• <b>Derivative Margin Used</b> – Margin utilized in FO / NCDEX / MCX segments (excluding CM segment margin)<br>
• <b>Margin Warning %</b> – Percentage of total available deposit utilized towards derivative margins<br>
&nbsp;&nbsp;&nbsp;&nbsp;(Calculated as Derivative Margin Used ÷ Total Deposit NMASS × 100)<br><br>
<br><br>
<b>WhatsApp Margin Alert Logic:</b><br>
• Automated WhatsApp alerts are triggered for clients whose <b>Margin Warning % exceeds 105%</b>.<br>
• The first alert is sent when the threshold is breached.<br>
• Subsequent alerts are sent only if the Margin Warning % changes by <b>±10% or more</b> compared to the last alert sent on the same day.<br>
• No alerts are sent if the Margin Warning % is 105% or below.<br>
• This logic ensures timely risk communication while preventing repetitive alerts for minor fluctuations.<br>
<br><br>

Best regards,<br>
<b>Kartavya Dhillon</b><br>
Master Capital Services Ltd<br>
📞 011-66331000 | Ext. 84
"""
            send_email(
                subject=_subj("Margin Shortage Monitoring"),
                body=zone_email_body,
                to_list=cfg["to"],
                cc_list=cfg["cc"],
                attachment=zone_file,
                label=f"{zone_key} zone ({len(zone_df)} client{'' if len(zone_df) == 1 else 's'})"
            )

    # Derive THIS run's number before backup creates its folder, so each
    # WhatsApp send is tagged with the run it went out in (powers the dashboard
    # "WhatsApp check" report). Same counting method backup_and_cleanup uses, so
    # the number matches the backup folder name.
    _ms_run_number = _count_todays_backup_runs(BASE_DIR, today_str)
    run_whatsapp_alerts(final_df, _ms_run_number)  # sending whatsapp alert

    # =====================================================
    # BACKUP & CLEANUP (UNCHANGED LOGIC, + DB archiving)
    # =====================================================
    def backup_and_cleanup(base_dir):
        now = datetime.now().time()

        is_last_run = now >= dtime(14, 55)  # unchanged threshold

        date_root = os.path.join(
            base_dir,
            "BACKUPFILES AUTOMATED",
            today_str
        )
        os.makedirs(date_root, exist_ok=True)

        existing_runs = [
            d for d in os.listdir(date_root)
            if os.path.isdir(os.path.join(date_root, d)) and d.isdigit()
        ]

        if existing_runs:
            run_number = max(int(d) for d in existing_runs) + 1
        else:
            run_number = 1

        run_folder = f"{run_number:02d}"
        backup_root = os.path.join(date_root, run_folder)
        os.makedirs(backup_root, exist_ok=True)
        db_tag = f"{today_str}/{run_folder}"

        for f in os.listdir(base_dir):
            src = os.path.join(base_dir, f)

            if os.path.isdir(src):
                continue
            if not f.endswith((".csv", ".xlsx", ".csv.crdownload", ".jpg", ".jpeg")):
                continue
            if "RISK_REPORT" in f and not is_last_run:
                print(f"⏭️ Skipping Risk Report (not last run): {f}")
                continue

            dst = os.path.join(backup_root, f)
            shutil.copy2(src, dst)
            kind = "output" if f.startswith(("Shortage_vs_CC_Deposit", "Margin_Shortage_Alert")) else "input"
            _archive_to_db(db_tag, kind, dst)
            os.remove(src)
            print(f"🗂️ Backed up & removed: {f}")

        # Write run_counter.txt (date|run_number) — same format core/pipeline_box.py's
        # _read_counter() already reads for CTCL/CLI, purely so the dashboard's run
        # tiles can show progress. Does not affect run_number itself (still derived
        # from the backup folder count above, exactly as the uploaded script did).
        try:
            with open(os.path.join(base_dir, "run_counter.txt"), "w", encoding="utf-8") as _cf:
                _cf.write(f"{today_str}|{run_number}")
        except Exception as e:
            print(f"⚠️ Could not update run_counter.txt: {e}")

        print(
            f"🧹 Backup & cleanup completed | "
            f"Run: {run_folder} | "
            f"{'LAST RUN' if is_last_run else 'NORMAL RUN'}"
        )
        return run_number

    _run_number = backup_and_cleanup(BASE_DIR)

    # Refresh the JSON tracker for the night shift (see export_tracker_json).
    # Skipped for a safe test run — a test deliberately doesn't write the tracker,
    # so exporting here would hand the night operator a file that disagrees with
    # what was really sent.
    if not SAFE_TEST:
        export_tracker_json()

    print(f"\nMARGIN SHORTAGE (15-MIN) RUN {_run_number} COMPLETED SUCCESSFULLY\n")

    if SAFE_TEST:
        print("=" * 72, flush=True)
        print("🧪  SAFE TEST RUN FINISHED — nothing went to any client, branch or RM.", flush=True)
        print(f"    Check {TEST_WA_PLUS} for the WhatsApp messages + shortage image,", flush=True)
        print(f"    and {', '.join(TEST_EMAIL_TO)} for the emails (subjects tagged [TEST — IGNORE]).", flush=True)
        print("    The alert tracker was NOT written, so the next real run is unaffected.", flush=True)
        print("    ⚠️  The test arm is SPENT. The next Run will be FULLY LIVE.", flush=True)
        print("=" * 72, flush=True)

    return {"status": "ok", "run_number": _run_number, "safe_test": bool(SAFE_TEST)}
