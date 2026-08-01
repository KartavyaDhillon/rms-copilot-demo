r"""
NSE FO -> Internal Upload API  --  CATCH-UP / BACKFILL VERSION
====================================================================
Same job as before (download two NSE files, upload them to the internal
customFileImport API, back everything up), but the DATE LOGIC is now
holiday-proof and gap-proof.

WHY THE OLD VERSION SKIPPED DAYS
--------------------------------
The old code did:  trade_date = get_previous_trading_day(today)
That computes exactly ONE date and only skips Sat/Sun. Two failure modes:
  * If T-1 is an NSE holiday, NSE publishes no file for it -> 404 ->
    nothing uploads, and the code never falls back to the real last
    trading day (T-2, T-3, ...).
  * If the PC was off for several days (long weekend / festival cluster),
    every missed trading day is lost forever -- the code only ever looks
    at a single day.

THE NEW APPROACH (two ideas do all the work)
--------------------------------------------
1. NSE IS THE HOLIDAY CALENDAR. We do NOT hardcode a holiday list.
   If a date was a real trading day, the file exists on NSE. If it was a
   holiday, NSE returns 404. download_file() already returns False on 404.
   So: just try the date. File present -> upload it. 404 -> it was a
   holiday (or not published yet), skip it harmlessly.

2. A LEDGER OF WHAT'S ALREADY UPLOADED. Every run sweeps a WINDOW of the
   last LOOKBACK_DAYS calendar days (oldest -> newest). For each weekday
   in that window that isn't already in the ledger, we probe NSE:
       - file found  -> upload -> record the date in the ledger
       - 404         -> record as "no file" (holiday) so we stop re-probing
   Because we loop over the whole window instead of one day:
       - Coming back after a 4-day festival break uploads EVERY pending
         trading day it finds, in order.
       - A holiday in the middle of the window is skipped but the valid
         days around it are still processed.
       - Re-running the same day never double-uploads (ledger blocks it).

RECENT-DATE RE-CHECK
--------------------
The most recent day's file can be genuinely late (you ran early, before
NSE published). So a 404 on a very recent date must NOT be cached as a
permanent "holiday". Any date within RECHECK_RECENT_DAYS is always
re-probed next run even if it was 404 last time. Only older, settled dates
get their "no file" status trusted.

Everything else (NSE login, upload payload shape, backup) is UNCHANGED
from the confirmed-working version.
"""
# ---------------------------------------------------------------
# IMPORTS  (unchanged from the working version)
# ---------------------------------------------------------------
import base64
import gzip
import json
import logging
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# =====================================================
# CONFIG -- NSE extranet (the file DOWNLOAD side)  (unchanged)
# =====================================================
NSE_BASE_URL = "https://www.connect2nse.com/extranet-api"
NSE_LOGIN_ENDPOINT = f"{NSE_BASE_URL}/login/2.0"
NSE_MEMBER_DOWNLOAD_ENDPOINT = f"{NSE_BASE_URL}/member/file/download/2.0"

MEMBER_CODE = "06436"
NSE_LOGIN_ID = "M0213815"
NSE_PASSWORD = "$$Master2025$$"
NSE_AES_SECRET_KEY = "25sxL4bymyGFsPIVNvU/lZtBw/rR6Ip9jeX1pV7i+Vo="

# =====================================================
# CONFIG -- internal upload API  (unchanged)
# =====================================================
UPLOAD_LOGIN_ENDPOINT = "https://service.mastertrust.co.in/api/auth/login"
UPLOAD_FILE_ENDPOINT = "https://service.mastertrust.co.in/api/dataservice/customFileImport"
UPLOAD_USER_ID = "RMSDELH"
UPLOAD_PASSWORD = "rmsdelh@123"

# =====================================================
# CONFIG -- folders  (unchanged)
# =====================================================
ROOT_DIR = r"D:\UPINDER SIR API UPLOAD"
WORKING_DIR = os.path.join(ROOT_DIR, "downloads")
LOG_FOLDER = os.path.join(ROOT_DIR, "logs")
BACKUP_DIR = os.path.join(ROOT_DIR, "backup")

SCRIPT_DIR = Path(__file__).resolve().parent
NSE_TOKEN_FILE = SCRIPT_DIR / "token_nse_TEST.txt"
UPLOAD_TOKEN_FILE = SCRIPT_DIR / "token_uploadapi_TEST.txt"

# NEW: the ledger of what has already been handled, per file type.
STATE_FILE = SCRIPT_DIR / "upload_state_TEST.json"

os.makedirs(WORKING_DIR, exist_ok=True)
os.makedirs(LOG_FOLDER, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# =====================================================
# CONFIG -- catch-up window  (NEW)
# =====================================================
# How many calendar days back each run should sweep. 12 comfortably
# covers a Fri+long-weekend+Diwali-cluster gap. Increase if you ever
# expect the PC to be off longer than ~10 trading days in a row.
LOOKBACK_DAYS = 12
# Dates newer than this (in calendar days) are ALWAYS re-probed even if
# they 404'd before, because a fresh file may simply not be published yet.
RECHECK_RECENT_DAYS = 3

# FLOOR DATE -- the earliest date the script is EVER allowed to process.
# This is what stops the very first run from backfilling 12 days of old
# files your old process already uploaded.
#
#   * Leave it as None (recommended): on the FIRST run only, the script
#     pins the floor to the previous trading day and saves it in the
#     ledger. So the first run uploads ONLY that day (today's target) and
#     nothing older. From then on the floor never moves, so any gap AFTER
#     that first day is still caught up automatically.
#   * OR set it explicitly to force a start date, e.g. "2026-07-17".
#     Anything before this date is ignored forever.
FIRST_UPLOAD_DATE = "2026-07-20"   # start from today; Friday 17 was already uploaded manually at 8 AM

# Logical keys for the two file types in the ledger.
KEY_F_OPT = "F_OPTCSHDTLS01"
KEY_COLLDTLS = "COLLDTLS"

# =====================================================
# CONFIG -- logging  (unchanged)
# =====================================================
log_file_path = os.path.join(LOG_FOLDER, f"catchup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[
        logging.FileHandler(log_file_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


class SSLTrustError(RuntimeError):
    """SSL trust config issue -- not fixed by retrying, so we stop."""
    pass


def _log_ssl_trust_error(url, exc):
    log.error("🔒 SSL certificate verification failed connecting to: %s", url)
    log.error("   Not a transient error -- won't fix itself by retrying.")
    log.error("   Underlying error: %s", exc)
    log.error("   🛠️  FIX: pip install pip-system-certs, then re-run.")


# =====================================================
# STATE LEDGER  (NEW)  -- remembers which dates are done / are holidays
# =====================================================
# Shape on disk:
#   {
#     "uploaded": { "F_OPTCSHDTLS01": ["2026-07-17", ...],
#                   "COLLDTLS":       ["2026-07-17", ...] },
#     "no_file":  { "F_OPTCSHDTLS01": ["2026-07-15", ...],   # holidays
#                   "COLLDTLS":       ["2026-07-15", ...] }
#   }
def load_state():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
        except Exception as e:
            log.warning("State: failed to read %s (%s) -- starting fresh", STATE_FILE, e)
            data = {}
    else:
        data = {}
    # Normalise into sets for easy membership tests.
    def as_sets(section):
        section = data.get(section, {})
        return {
            KEY_F_OPT: set(section.get(KEY_F_OPT, [])),
            KEY_COLLDTLS: set(section.get(KEY_COLLDTLS, [])),
        }
    return {
        "uploaded": as_sets("uploaded"),
        "no_file": as_sets("no_file"),
        # "floor" = earliest date ever allowed (ISO string), pinned on first run.
        "floor": data.get("floor"),
    }


def save_state(state):
    def as_lists(section):
        return {
            KEY_F_OPT: sorted(state[section][KEY_F_OPT]),
            KEY_COLLDTLS: sorted(state[section][KEY_COLLDTLS]),
        }
    try:
        STATE_FILE.write_text(json.dumps(
            {
                "floor": state.get("floor"),
                "uploaded": as_lists("uploaded"),
                "no_file": as_lists("no_file"),
            },
            indent=2,
        ))
    except Exception as e:
        log.warning("State: failed to save %s: %s", STATE_FILE, e)


# =====================================================
# DATE LOGIC  (REWRITTEN)  -- yield every candidate trading day in window
# =====================================================
def get_previous_trading_day(run_date: date) -> date:
    """Nearest weekday before run_date (Sat/Sun skipped). Used only to
    auto-pin the floor on the very first run; NSE 404s still handle any
    holiday that happens to land on this date."""
    prev = run_date - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


def resolve_floor(state, run_date: date) -> date:
    """Decide the earliest date the script may process, and persist it.
    Priority: explicit FIRST_UPLOAD_DATE config > already-pinned floor in
    the ledger > auto-pin to the previous trading day on this first run."""
    if FIRST_UPLOAD_DATE:
        floor = date.fromisoformat(FIRST_UPLOAD_DATE)
        state["floor"] = floor.isoformat()
        return floor
    if state.get("floor"):
        return date.fromisoformat(state["floor"])
    floor = get_previous_trading_day(run_date)
    state["floor"] = floor.isoformat()
    log.info("📌 First run -- pinning floor date to %s. Nothing before this "
             "will ever be uploaded.", floor.isoformat())
    return floor


def candidate_dates(run_date: date, lookback_days: int):
    """
    Yields weekday dates from oldest to newest within the lookback window,
    EXCLUDING today itself (today's NSE file isn't ready yet -- same T-1
    assumption as before). Weekends are skipped here because NSE never
    publishes on them; NSE holidays are NOT skipped here on purpose --
    we let NSE's 404 tell us they were holidays (see module docstring).
    Oldest-first ordering means the upload API receives days in
    chronological order, which matches how a person would upload them.
    """
    for n in range(lookback_days, 0, -1):  # lookback_days .. 1
        d = run_date - timedelta(days=n)
        if d.weekday() >= 5:  # 5=Sat, 6=Sun
            continue
        yield d


# =====================================================
# AES PASSWORD ENCRYPTION  (unchanged)
# =====================================================
def encrypt_password(password: str, base64_key: str) -> str:
    key_bytes = base64.standard_b64decode(base64_key)
    backend = default_backend()
    cipher = Cipher(algorithms.AES(key_bytes), modes.ECB(), backend)
    encryptor = cipher.encryptor()
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(password.encode("utf-8")) + padder.finalize()
    encrypted = encryptor.update(padded_data) + encryptor.finalize()
    return base64.standard_b64encode(encrypted).decode("utf-8")


# =====================================================
# NSE AUTH  (unchanged)
# =====================================================
class NSEAuthManager:
    def __init__(self):
        self.token = None
        self.token_time = None
        self._load_from_file()

    def _load_from_file(self):
        if NSE_TOKEN_FILE.exists():
            try:
                data = json.loads(NSE_TOKEN_FILE.read_text())
                self.token = data.get("token")
                self.token_time = datetime.fromisoformat(data.get("time"))
            except Exception as e:
                log.warning("NSE: failed to load cached token: %s", e)

    def _save_to_file(self):
        try:
            NSE_TOKEN_FILE.write_text(json.dumps({"token": self.token, "time": self.token_time.isoformat()}))
        except Exception as e:
            log.warning("NSE: failed to cache token: %s", e)

    def _is_expired(self):
        if not self.token_time:
            return True
        return datetime.now() > self.token_time + timedelta(minutes=55)

    def login(self):
        while True:
            try:
                encrypted_password = encrypt_password(NSE_PASSWORD, NSE_AES_SECRET_KEY)
                payload = {"memberCode": MEMBER_CODE, "loginId": NSE_LOGIN_ID, "password": encrypted_password}
                r = requests.post(NSE_LOGIN_ENDPOINT, json=payload, timeout=10)
                log.info("🔐 NSE login HTTP status: %s", r.status_code)
                if r.status_code in (500, 502, 503, 504):
                    time.sleep(5)
                    continue
                r.raise_for_status()
                data = r.json()
                token = data.get("token")
                status = data.get("status")
                if token and (status == "success" or status is None):
                    self.token = token
                    self.token_time = datetime.now()
                    self._save_to_file()
                    log.info("🔑 NSE login successful")
                    return self.token
                log.warning("⚠️  NSE login response missing token: %s -> retry in 3s", data)
            except requests.exceptions.SSLError as e:
                _log_ssl_trust_error(NSE_LOGIN_ENDPOINT, e)
                raise SSLTrustError() from e
            except requests.exceptions.RequestException as e:
                log.warning("🌐 NSE login network error: %s -> retry in 5s", e)
                time.sleep(5)
                continue
            time.sleep(3)

    def get_token(self):
        if self.token is None or self._is_expired():
            return self.login()
        return self.token

    def force_relogin(self):
        return self.login()


# =====================================================
# UPLOAD API AUTH  (unchanged)
# =====================================================
class UploadAPIAuthManager:
    def __init__(self):
        self.token = None
        self.token_expires_at = None
        self._load_from_file()

    def _load_from_file(self):
        if UPLOAD_TOKEN_FILE.exists():
            try:
                data = json.loads(UPLOAD_TOKEN_FILE.read_text())
                self.token = data.get("token")
                self.token_expires_at = datetime.fromisoformat(data.get("expires_at"))
            except Exception as e:
                log.warning("Upload API: failed to load cached token: %s", e)

    def _save_to_file(self):
        try:
            UPLOAD_TOKEN_FILE.write_text(json.dumps({"token": self.token, "expires_at": self.token_expires_at.isoformat()}))
        except Exception as e:
            log.warning("Upload API: failed to cache token: %s", e)

    def _is_expired(self):
        if not self.token_expires_at:
            return True
        return datetime.now() > self.token_expires_at - timedelta(minutes=5)

    def login(self):
        while True:
            try:
                payload = {"userId": UPLOAD_USER_ID, "password": UPLOAD_PASSWORD}
                r = requests.post(UPLOAD_LOGIN_ENDPOINT, json=payload, timeout=10)
                log.info("🔐 Upload API login HTTP status: %s", r.status_code)
                log.info("   📝 Login response body: %s", r.text)
                if r.status_code in (500, 502, 503, 504):
                    time.sleep(5)
                    continue
                r.raise_for_status()
                body = r.json()
                if not body.get("success"):
                    log.warning("⚠️  Upload API login not successful: %s -> retry in 3s", body)
                    time.sleep(3)
                    continue
                data = body.get("data", {})
                token = data.get("accessToken")
                expires_in = data.get("accessTokenExpiresIn", 3600)
                if not token:
                    time.sleep(3)
                    continue
                self.token = token
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                self._save_to_file()
                log.info("🔑 Upload API login successful (userId=%s, tokenType=%s, expires in %ds)",
                         data.get("userId"), data.get("tokenType"), expires_in)
                return self.token
            except requests.exceptions.SSLError as e:
                _log_ssl_trust_error(UPLOAD_LOGIN_ENDPOINT, e)
                raise SSLTrustError() from e
            except requests.exceptions.RequestException as e:
                log.warning("🌐 Upload API login network error: %s -> retry in 5s", e)
                time.sleep(5)
                continue

    def get_token(self):
        if self.token is None or self._is_expired():
            return self.login()
        return self.token

    def force_relogin(self):
        self.token = None
        return self.login()


# =====================================================
# NSE FILE DOWNLOAD  (unchanged except: 404 now clearly means "holiday /
# not published", which the caller uses as the holiday signal)
# =====================================================
def download_file(auth, segment, folder_path, filename, save_dir):
    params = {"segment": segment, "folderPath": folder_path, "filename": filename}
    attempt = 1
    while True:
        token = auth.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            log.info("📥 Downloading [member] %s/%s (attempt %d)", folder_path, filename, attempt)
            r = requests.get(NSE_MEMBER_DOWNLOAD_ENDPOINT, headers=headers, params=params, timeout=20)
            if r.status_code in (401, 403):
                auth.force_relogin()
                attempt += 1
                continue
            if r.status_code in (500, 502, 503, 504):
                time.sleep(5)
                attempt += 1
                continue
            if r.status_code == 404:
                # Not an error for us -- means this date had no file
                # (holiday, or not yet published for a very recent date).
                log.info("🚫 404 Not Found (no file for this date): %s", filename)
                return False
            if 400 <= r.status_code < 500:
                log.warning("❌ HTTP %s for %s -> giving up", r.status_code, filename)
                return False
            r.raise_for_status()
            if len(r.content) == 0 or "application/json" in r.headers.get("Content-Type", ""):
                time.sleep(3)
                attempt += 1
                continue
            save_path = os.path.join(save_dir, filename)
            with open(save_path, "wb") as f:
                f.write(r.content)
            log.info("✅ Downloaded: %s (%d bytes)", filename, len(r.content))
            return True
        except requests.exceptions.RequestException as e:
            log.warning("🌐 Network error: %s -> retry in 5s", e)
            time.sleep(5)
            attempt += 1


def extract_gz(gz_path):
    if not gz_path or not gz_path.lower().endswith(".gz"):
        return gz_path
    out_path = gz_path[:-3]
    try:
        with gzip.open(gz_path, "rb") as f_in, open(out_path, "wb") as f_out:
            f_out.write(f_in.read())
        os.remove(gz_path)
        log.info("📦 Extracted: %s", os.path.basename(out_path))
        return out_path
    except Exception as e:
        log.error("❌ Extraction error: %s", e)
        return None


def download_if_missing(auth, segment, folder_path, gz_filename, extracted_filename, save_dir):
    """Reuse an already-extracted file on disk; otherwise download+extract.
    Returns the extracted path, or None if NSE had no file (404)."""
    extracted_path = os.path.join(save_dir, extracted_filename)
    if os.path.exists(extracted_path) and os.path.getsize(extracted_path) > 0:
        log.info("📂 Already on disk, skipping NSE download: %s", extracted_path)
        return extracted_path
    ok = download_file(auth, segment, folder_path, gz_filename, save_dir)
    if not ok:
        return None
    return extract_gz(os.path.join(save_dir, gz_filename))


# =====================================================
# BACKUP  (unchanged)
# =====================================================
def backup_run(trade_date: date, file_paths, this_run_log_path):
    dated_folder = os.path.join(BACKUP_DIR, trade_date.strftime("%Y-%m-%d"))
    os.makedirs(dated_folder, exist_ok=True)
    for p in file_paths:
        if not p or not os.path.exists(p):
            continue
        try:
            shutil.copy2(p, dated_folder)
            log.info("🗄️  Backed up: %s -> %s", os.path.basename(p), dated_folder)
        except Exception as e:
            log.error("❌ Backup failed for %s: %s", p, e)
    for handler in logging.getLogger().handlers:
        handler.flush()
    try:
        shutil.copy2(this_run_log_path, dated_folder)
        log.info("🗄️  Backed up log file: %s -> %s", os.path.basename(this_run_log_path), dated_folder)
    except Exception as e:
        log.error("❌ Backup failed for log file: %s", e)


# =====================================================
# UPLOAD  (unchanged -- confirmed-working payload shape)
# =====================================================
COLLDTLS_TEMPLATE_NAME = f"COLLDTLS_{MEMBER_CODE}_YYYYMMDD_01.csv"   # STATIC
F_OPT_TEMPLATE_NAME = f"F_OPTCSHDTLS01_{MEMBER_CODE}_DDMMYYYY.csv"   # STATIC


def upload_one(auth: UploadAPIAuthManager, file_path, file_part_filename, fileName_field_value, date_str, max_attempts=3):
    attempt = 1
    retried_401 = False
    while attempt <= max_attempts:
        token = auth.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_part_filename, f, "text/csv")}
                data = {"fileName": fileName_field_value, "date": date_str}
                r = requests.post(UPLOAD_FILE_ENDPOINT, headers=headers, files=files, data=data, timeout=60)
            status_emoji = "✅" if 200 <= r.status_code < 300 else "❌"
            log.info("📤 POST customFileImport -> %s HTTP %s  (file=%r, fileName=%r, date=%r)",
                     status_emoji, r.status_code, file_part_filename, fileName_field_value, date_str)
            log.info("   📝 Response body: %s", r.text)
            if r.status_code == 401 and not retried_401:
                log.warning("🚫 401 Unauthorized -> re-authenticating and retrying once")
                auth.force_relogin()
                retried_401 = True
                attempt += 1
                continue
            if r.status_code in (500, 502, 503, 504):
                log.warning("⏳ Server error %s -> retry in 5s", r.status_code)
                time.sleep(5)
                attempt += 1
                continue
            return r.status_code, r.text
        except requests.exceptions.SSLError as e:
            _log_ssl_trust_error(UPLOAD_FILE_ENDPOINT, e)
            raise SSLTrustError() from e
        except requests.exceptions.RequestException as e:
            log.warning("🌐 Network error during upload: %s -> retry in 5s", e)
            time.sleep(5)
            attempt += 1
    return None, "gave up after retries"


# =====================================================
# PER-DATE PROCESSING  (NEW)  -- handle one trading-day candidate
# =====================================================
def process_one_file(nse_auth, upload_auth, state, run_date, d,
                     ledger_key, gz_name, extracted_name, template_name, date_field_value):
    """
    Handle ONE file type for ONE candidate date. Returns:
      ("uploaded", path)   file found + upload succeeded
      ("failed",   path)   file found but upload did NOT succeed (will retry next run)
      ("holiday",  None)   NSE had no file (404) -> treat as non-trading day
      ("skip",     None)   already uploaded, or a settled holiday -> nothing to do
    The caller uses the returned path (if any) for the daily backup.
    """
    key = d.isoformat()
    is_recent = (run_date - d).days <= RECHECK_RECENT_DAYS

    # Already done -> never touch it again.
    if key in state["uploaded"][ledger_key]:
        return "skip", None

    # Known holiday AND old enough to trust -> don't re-probe NSE.
    if key in state["no_file"][ledger_key] and not is_recent:
        return "skip", None

    path = download_if_missing(nse_auth, "FO", "Reports", gz_name, extracted_name, WORKING_DIR)

    if path is None:
        # 404 -> no file for this date. Record it as a holiday. If it's
        # recent, it stays eligible for a re-probe next run (a late file
        # may appear); if it's old, this permanently marks it a holiday.
        state["no_file"][ledger_key].add(key)
        return "holiday", None

    # File exists -> this WAS a trading day. Upload it.
    log.info("📤 UPLOAD %s -- date=%s | file=%s | fileName=%s | dateField=%s",
             ledger_key, key, extracted_name, template_name, date_field_value)
    status, body = upload_one(upload_auth, path, extracted_name, template_name, date_field_value)
    ok = status is not None and 200 <= status < 300
    if ok:
        state["uploaded"][ledger_key].add(key)
        state["no_file"][ledger_key].discard(key)  # in case it was ever mislabelled
        return "uploaded", path
    return "failed", path


# =====================================================
# MAIN  (REWRITTEN)  -- sweep the whole window, upload every pending day
# =====================================================
def main():
    log.info("🚀" + "=" * 68)
    log.info("🚀 RUN START -- %s", datetime.now().isoformat())
    log.info("🚀 Catch-up window: last %d calendar days (recheck recent %d days)",
             LOOKBACK_DAYS, RECHECK_RECENT_DAYS)
    log.info("🚀" + "=" * 68)

    run_date = date.today()
    state = load_state()
    floor = resolve_floor(state, run_date)
    log.info("📌 Floor date (earliest allowed): %s", floor.isoformat())

    nse_auth = NSEAuthManager()
    upload_auth = UploadAPIAuthManager()
    # Log in to the upload API once up front (only really needed if there's
    # work to do, but harmless and keeps the flow simple).
    upload_auth.get_token()

    results = []          # (label, date, status, http, body) for the summary
    processed_any = False

    for d in candidate_dates(run_date, LOOKBACK_DAYS):
        # Never touch anything before the pinned floor date -- this is what
        # stops the first run from backfilling old, already-handled files.
        if d < floor:
            continue
        ddmmyyyy = d.strftime("%d%m%Y")
        yyyymmdd = d.strftime("%Y%m%d")
        date_field_value = d.strftime("%d-%m-%Y")

        log.info("📅 --- Candidate trading day: %s ---", d.isoformat())

        # ---- COLLDTLS ----
        c_gz = f"COLLDTLS_{MEMBER_CODE}_{yyyymmdd}_01.CSV.gz"
        c_ext = f"COLLDTLS_{MEMBER_CODE}_{yyyymmdd}_01.CSV"
        c_outcome, c_path = process_one_file(
            nse_auth, upload_auth, state, run_date, d,
            KEY_COLLDTLS, c_gz, c_ext, COLLDTLS_TEMPLATE_NAME, date_field_value)

        # ---- F_OPTCSHDTLS01 ----
        f_gz = f"F_OPTCSHDTLS01_{MEMBER_CODE}_{ddmmyyyy}.CSV.gz"
        f_ext = f"F_OPTCSHDTLS01_{MEMBER_CODE}_{ddmmyyyy}.CSV"
        f_outcome, f_path = process_one_file(
            nse_auth, upload_auth, state, run_date, d,
            KEY_F_OPT, f_gz, f_ext, F_OPT_TEMPLATE_NAME, date_field_value)

        # Record anything that actually resulted in an upload attempt.
        if c_outcome in ("uploaded", "failed"):
            results.append((KEY_COLLDTLS, d.isoformat(), c_outcome))
        if f_outcome in ("uploaded", "failed"):
            results.append((KEY_F_OPT, d.isoformat(), f_outcome))

        # If either file for this date was actually handled (not a pure
        # skip), back up whatever we have for that date.
        if c_outcome != "skip" or f_outcome != "skip":
            processed_any = True
            if c_path or f_path:
                backup_run(d, [c_path, f_path], log_file_path)

        # Persist the ledger after each date so an interruption never
        # loses progress or causes a duplicate upload on the next run.
        save_state(state)

    # ---- Summary ----
    log.info("📊" + "=" * 68)
    log.info("📊 SUMMARY")
    log.info("📊" + "=" * 68)
    if not results:
        if processed_any:
            log.info("ℹ️  Files were checked but none needed a new upload this run.")
        else:
            log.info("ℹ️  Nothing new to do -- all trading days in the window are already uploaded.")
    else:
        for label, day, outcome in results:
            mark = "✅" if outcome == "uploaded" else "❌"
            log.info("%s %s | %s | %s", mark, day, label, outcome)
    log.info("📊" + "=" * 68)

    save_state(state)
    log.info("🏁 RUN END -- %s", datetime.now().isoformat())


if __name__ == "__main__":
    try:
        main()
    except SSLTrustError:
        log.error("🛑 RUN STOPPED -- SSL trust issue, see instructions above.")
        sys.exit(1)
