import os as _sa_os
try:
    from dotenv import load_dotenv as _sa_ld
    _sa_ld(_sa_os.getenv("SA_ENV_FILE", r"D:\CLAUDE\rms-copilot\.env"))
except Exception:
    pass
def _SAENV(_k, _d):
    _v = _sa_os.getenv(_k)
    return _v if (_v is not None and _v != "") else _d
def _SALIST(_k, _d):
    _v = _sa_os.getenv(_k)
    return [x.strip() for x in _v.split(",") if x.strip()] if _v else _d
# ---- end SA env shim ----

# ---- date override: set SA_OVERRIDE_DATE=YYYY-MM-DD to run for a past date ----
def _sa_today():
    """Return the effective 'today' as a datetime object. Checks SA_OVERRIDE_DATE
    env var first (format YYYY-MM-DD); falls back to real today."""
    from datetime import datetime as _dt
    ov = _sa_os.getenv("SA_OVERRIDE_DATE", "").strip()
    if ov:
        try:
            return _dt.strptime(ov, "%Y-%m-%d")
        except ValueError:
            pass
    return _dt.today()
# ---- end date override ----




import time
import requests
import gzip
import os
from datetime import datetime

from datetime import timedelta
import sys


# =====================================================
# SMTP EMAIL CONFIG
# =====================================================
import smtplib
import time
from email.message import EmailMessage

SMTP_SERVER = _SAENV("SA_DL_SMTP_SERVER", "smtp3.netcore.co.in")
SMTP_PORT = int(_SAENV("SA_DL_SMTP_PORT", "587"))
SMTP_EMAIL = _SAENV("SA_DL_SMTP_EMAIL", "kartavya.dhillon@mastertrust.co.in")
SMTP_PASSWORD = _SAENV("SA_DL_SMTP_PASSWORD", "$Kartavya25*")

# ------------------------------
# EMAIL RECIPIENTS
# ------------------------------
EMAIL_TO = _SALIST("SA_DL_EMAIL_TO", ["rms_del@mastertrust.co.in"])
EMAIL_CC = _SALIST("SA_DL_EMAIL_CC", [])




def send_failure_email(missing_files):
    """
    Sends failure email.
    Retries infinitely if SMTP / network / auth fails.
    """

    while True:
        try:
            msg = EmailMessage()

            msg["From"] = SMTP_EMAIL
            msg["To"] = ", ".join(EMAIL_TO)
            msg["Cc"] = ", ".join(EMAIL_CC)
            msg["Subject"] = "🚨 FILE DOWNLOAD FAILURE ALERT OF SHORT ALLOCATION PROCESS"

            body = (
                "Hi Priyanshu,\n\n"
                "The following file(s) were NOT downloaded successfully:\n\n"
                + "\n".join(missing_files)
                + "\n\n"
                "The script execution has been STOPPED.\n"
                "It will retry automatically after 5 minutes.\n\n"
                "Regards,\n"
                "Kartavya Dhillon\n"
                "Master Capital Services Ltd\n"
                "📞 011-66331000 | Ext. 84"
            )

            msg.set_content(body)

            all_recipients = EMAIL_TO + EMAIL_CC

            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
                server.starttls()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg, to_addrs=all_recipients)

            print("📧 FAILURE EMAIL SENT SUCCESSFULLY")
            break

        except Exception as e:
            print(f"⚠️ EMAIL ERROR: {e}")
            print("🔁 Retrying email in 3 seconds...")
            time.sleep(3)









STATE_FILE = _SAENV("SA_STATE_FILE", r"C:\KARTAVYA\SHORT ALLOCATION\run_state.txt")

if os.path.exists(STATE_FILE):
    with open(STATE_FILE) as f:
        content = f.read()
    print("📄 RUN STATE FILE:\n", content)


ALL_FILES_OK = True
MISSING_FILES = []

# Safety: ensure clean state on internal retry
MISSING_FILES.clear()
ALL_FILES_OK = True
NSE_OK = False
MCX_OK = False
NCDEX_OK = False




def send_manual_intervention_email(missing_files):
    while True:
        try:
            msg = EmailMessage()
            msg["From"] = SMTP_EMAIL
            msg["To"] = ", ".join(EMAIL_TO)
            msg["Cc"] = ", ".join(EMAIL_CC)
            msg["Subject"] = "⚠️ MANUAL FILE PLACEMENT REQUIRED FOR SHORT ALLOCATION"

            body = (
                "Hi Priyanshu,\n\n"
                "The following file(s) are STILL not available after retry:\n\n"
                + "\n".join(missing_files)
                + "\n\n"
                "Please place these files MANUALLY in:\n"
                "C:\\KARTAVYA\\SHORT ALLOCATION\n\n"
                "The system is waiting for confirmation.\n\n"
                "Regards,\n"
                "Kartavya Dhillon\n"
                "Master Capital Services Ltd\n"
                "📞 011-66331000 | Ext. 84"

            )

            msg.set_content(body)

            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
                server.starttls()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg, to_addrs=EMAIL_TO + EMAIL_CC)

            print("📧 MANUAL INTERVENTION EMAIL SENT")
            break

        except Exception as e:
            print(f"⚠️ EMAIL ERROR: {e}")
            print("📧 SMTP unavailable — retrying manual intervention email delivery...")
            time.sleep(3)



def _create_blank_file(filename, out_dir):
    """
    Generate a structurally correct blank placeholder for a missing exchange file.
    Uses the same format as the sample templates (EMP62 client, all-zero values).
    Returns the full path on success, None if the pattern is unrecognised.
    """
    import re as _re, datetime as _dt2

    out_path = os.path.join(out_dir, filename)

    # ── MCX Peak: MCX_PeakMargin56720_20260702_01.csv ──────────────────────
    m = _re.match(r"MCX_PeakMargin(\d+)_(\d{8})_(\d{2})\.csv$", filename, _re.I)
    if m:
        member_id = m.group(1)   # e.g. "56720"
        date_str  = m.group(2)   # e.g. "20260702"
        dt = _dt2.datetime.strptime(date_str, "%Y%m%d")
        date_fmt = dt.strftime("%d-%b-%y")  # "02-Jul-26"
        # 12 columns, no header row (matches exchange format exactly)
        line = f"{date_fmt},09:00:00,{member_id},{member_id},EMP62,0,0,0,0,0,0,0\n"
        with open(out_path, "w", newline="\r\n", encoding="utf-8") as f:
            f.write(line)
        print(f"📄 Blank MCX Peak file created: {filename}")
        return out_path

    # ── NCCL NCDEX: NCCL_MARGIN_REP_01292_20260702_03.csv ─────────────────
    m = _re.match(r"NCCL_MARGIN_REP_(\d+)_(\d{8})_(\d{2})\.csv$", filename, _re.I)
    if m:
        date_str = m.group(2)
        run_nn   = m.group(3)   # e.g. "03"
        dt = _dt2.datetime.strptime(date_str, "%Y%m%d")
        date_fmt = dt.strftime("%d-%b-%y")  # "02-Jul-26"
        snap_no  = str(int(run_nn))
        header = (
            "Date,Client Code,Portfolio based Margin,Net Buy Premium,"
            "Initial Margin,ELM Margin,Pre-Expiry Margin,Delivery Margin,"
            "Unidirectional Margin,Concentration Margin,Adhoc Margin,Cash Margin,"
            "Crystallized Loss Margin,Mark to Market Profit/Loss,Cli/Prop,"
            "SNAPSHOT_NUMBER,SNAPSHOT_TIME,ED,Collateral_Value_Snapshot,"
            "Short_allocation_SnapShot"
        )
        data = f"{date_fmt},EMP62,0,0,0,0,0,0,0,0,0,0,0,0,P,{snap_no},14:30:00,0,0,0"
        with open(out_path, "w", newline="\r\n", encoding="utf-8") as f:
            f.write(header + "\n")
            f.write(data + "\n")
        print(f"📄 Blank NCCL/NCDEX file created: {filename}")
        return out_path

    # ── NSE C/F LIS (gzipped): F_SA01_P_06436_04072026_i01.lis.gz ────────────
    # Strip .gz — create a plain .lis placeholder so SA-02 reads it directly
    m = _re.match(r"[CF]_SA01_P_(\d+)_(\d{8})_(i?\d+)\.lis\.gz$", filename, _re.I)
    if m:
        lis_filename = filename[:-3]   # strip ".gz"
        lis_path     = os.path.join(out_dir, lis_filename)
        date_str = m.group(2)   # DDMMYYYY, e.g. "04072026"
        dt = _dt2.datetime.strptime(date_str, "%d%m%Y")
        date_fmt = dt.strftime("%d-%b-%Y").upper()  # "04-JUL-2026"
        line = f"{date_fmt},EMP62,0.00,0.00,0.00,0.00,0.00\n"
        with open(lis_path, "w", newline="\r\n", encoding="utf-8") as f:
            f.write(line)
        print(f"📄 Blank NSE LIS file created (from .lis.gz): {lis_filename}")
        return lis_path

    # ── NSE C/F LIS: C_SA01_P_06436_02072026_i01.lis ──────────────────────
    m = _re.match(r"[CF]_SA01_P_(\d+)_(\d{8})_(i?\d+)\.lis$", filename, _re.I)
    if m:
        date_str = m.group(2)   # DDMMYYYY, e.g. "02072026"
        dt = _dt2.datetime.strptime(date_str, "%d%m%Y")
        date_fmt = dt.strftime("%d-%b-%Y").upper()  # "02-JUL-2026"
        # one row, no header (matches exchange format exactly)
        line = f"{date_fmt},EMP62,0.00,0.00,0.00,0.00,0.00\n"
        with open(out_path, "w", newline="\r\n", encoding="utf-8") as f:
            f.write(line)
        print(f"📄 Blank NSE LIS file created: {filename}")
        return out_path

    print(f"⚠️  Unknown file pattern — cannot auto-create blank: {filename}")
    return None


def _web_missing_decision(missing_files, out_dir, context="failure", timeout_secs=300):
    """
    Signal the browser dashboard that files are missing and wait for operator decision.
    Shows a modal with two choices: 'Wait & retry' or 'Run without it (blank file)'.
    Returns: "RUN_BLANK" | "WAIT"
    Falls back to "WAIT" on timeout or any I/O error.
    """
    import json as _json2
    state_path = os.path.join(out_dir, ".missing_decision.json")
    try:
        payload = {
            "state": "PENDING",
            "missing": missing_files,
            "context": context,
            "timeout_at": (datetime.now() + timedelta(seconds=timeout_secs)).isoformat(),
        }
        with open(state_path, "w", encoding="utf-8") as f:
            _json2.dump(payload, f)

        print(f"\n  [MISSING FILE] Dashboard decision modal shown — waiting up to {timeout_secs}s…")
        deadline = time.time() + timeout_secs
        while time.time() < deadline:
            time.sleep(2)
            try:
                with open(state_path, encoding="utf-8") as f:
                    data = _json2.load(f)
                st = data.get("state", "PENDING")
                if st in ("WAIT", "RUN_BLANK"):
                    print(f"  [MISSING FILE] Dashboard decision: {st}")
                    try: os.remove(state_path)
                    except Exception: pass
                    return st
            except Exception:
                pass

        print("  [MISSING FILE] Decision timed out — defaulting to WAIT (retry)")
        try: os.remove(state_path)
        except Exception: pass

    except Exception as e:
        print(f"  [MISSING FILE] Web decision error ({e}) — defaulting to WAIT")

    return "WAIT"


def _apply_blank_files(missing_files, out_dir):
    """
    Create blank placeholder files for every missing exchange file.
    Returns the list of filenames that were successfully blanked.
    """
    import json as _json3
    blanked = []
    for fname in missing_files:
        result = _create_blank_file(fname, out_dir)
        if result:
            blanked.append(fname)
        else:
            print(f"  ⚠️  Could not auto-blank: {fname} — you may need to place it manually")

    if blanked:
        # Persist for SA-processing email NOTE
        blanked_path = os.path.join(out_dir, ".blanked_files.json")
        try:
            with open(blanked_path, "w", encoding="utf-8") as f:
                _json3.dump(blanked, f)
            print(f"  📝 Blanked file list saved to {blanked_path}")
        except Exception as e:
            print(f"  ⚠️  Could not save blanked file list: {e}")
    return blanked


def manual_files_popup(missing_files):
    """Legacy function — kept so any external callers don't break.
    Now delegates to the web-based decision flow."""
    pass   # replaced by _web_missing_decision — call-site logic below handles it


def all_missing_files_now_exist(missing_files):
    for f in missing_files:
        if not os.path.exists(os.path.join(OUTPUT_DIR, f.replace(".gz", ""))):
            return False
    return True



def get_retry_flag():
    retry = 0
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            for line in f:
                if line.startswith("RETRY="):
                    try:
                        retry = int(line.strip().split("=")[1])
                    except:
                        retry = 0
    return retry


def set_retry_flag(value):
    today = _sa_today().strftime("%Y-%m-%d")

    stored_date = None
    stored_run = 1

    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            for line in f:
                if line.startswith("DATE="):
                    stored_date = line.strip().split("=")[1]
                elif line.startswith("RUN="):
                    try:
                        stored_run = int(line.strip().split("=")[1])
                    except:
                        stored_run = 1

    # NEW DAY → RESET RUN
    if stored_date != today:
        stored_run = 1

    with open(STATE_FILE, "w") as f:
        f.write(f"DATE={today}\nRUN={stored_run}\nRETRY={value}\n")



def get_trading_date():
    today = _sa_today()
    if today.weekday() == 0:
        return (today - timedelta(days=3)).strftime("%Y-%m-%d")
    elif today.weekday() == 6:
        return (today - timedelta(days=2)).strftime("%Y-%m-%d")
    else:
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")

def get_run_suffix():
    today = _sa_today().strftime("%Y-%m-%d")

    run_no = 1
    stored_date = None
    retry = 0

    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            for line in f:
                if line.startswith("DATE="):
                    stored_date = line.strip().split("=")[1]
                elif line.startswith("RUN="):
                    try:
                        run_no = int(line.strip().split("=")[1])
                    except:
                        run_no = 1
                elif line.startswith("RETRY="):
                    try:
                        retry = int(line.strip().split("=")[1])
                    except:
                        retry = 0

    # 🔁 NEW DAY → HARD RESET
    if stored_date != today:
        run_no = 1
        retry = 0

    # The console tells us exactly which run this is (SA_RUN_NO). Use it, so this
    # run always downloads ITS OWN files whatever the counter file says.
    _forced = _sa_os.getenv("SA_RUN_NO", "").strip()
    if _forced.isdigit():
        run_no = int(_forced)

    # 🛑 MAX 8 RUNS PER DAY (runs 5-8: Short Allocation only, no LAS Funding)
    if run_no > 8:
        print("🛑 All 8 runs already completed for today.")
        sys.exit(0)

    # SAVE STATE (before execution)
    with open(STATE_FILE, "w") as f:
        f.write(
            f"DATE={today}\n"
            f"RUN={run_no}\n"
            f"RETRY={retry}\n"
        )

    print(f"▶ RUN {run_no}/8")
    return run_no, f"i{run_no:02d}", f"{run_no:02d}"


def mark_run_success():
    today = _sa_today().strftime("%Y-%m-%d")

    stored_date = None
    stored_run = 0   # important: start from 0

    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            for line in f:
                if line.startswith("DATE="):
                    stored_date = line.strip().split("=")[1]
                elif line.startswith("RUN="):
                    try:
                        stored_run = int(line.strip().split("=")[1])
                    except:
                        stored_run = 0

    # ✅ CORE LOGIC (THIS FIXES YOUR ISSUE)
    if stored_date == today:
        next_run = stored_run + 1
    else:
        next_run = 1

    with open(STATE_FILE, "w") as f:
        f.write(
            f"DATE={today}\n"
            f"RUN={next_run}\n"
            f"RETRY=0\n"
        )



run_no, nse_i, mcx_i = get_run_suffix()

# Which peak-file number each segment uses (runs 5-8 reuse the exchange's 4th CASH/FO file) - see sa_peak.py
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)
import sa_peak as _sp
nse_i_cf, nse_i_co, mcx_i = _sp.suffixes(run_no)
SA_AUTO = os.getenv("SA_AUTO_RUN", "").strip() == "1"   # fully automatic run: no pop-ups, no waiting for a person



# =====================================================
# CONFIG (WORKING VALUES)
# =====================================================
LOGIN_URL = "https://www.connect2nse.com/extranet-api/login/2.0"
DOWNLOAD_URL = "https://www.connect2nse.com/extranet-api/member/file/download/2.0"

CREDENTIALS ={
    "memberCode": _SAENV("SA_NSE_MEMBER", "06436"),
    "loginId": _SAENV("SA_NSE_LOGINID", "M0213815"),
    "password": _SAENV("SA_NSE_PASSWORD", "Yxs2Hg1/h0KUKKoF5SqEGQ==")
}
   


OUTPUT_DIR = _SAENV("SA_OUTPUT_DIR", r"C:\KARTAVYA\SHORT ALLOCATION")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Clear any stale .missing_decision.json from a previous crashed/killed run.
# If left as PENDING it causes the dashboard popup to re-appear on every poll.
_stale_decision = os.path.join(OUTPUT_DIR, ".missing_decision.json")
try:
    if os.path.exists(_stale_decision):
        os.remove(_stale_decision)
except Exception:
    pass

FOLDER_PATH = "Reports"

# =====================================================
# LOGIN (INFINITE RETRY)
# =====================================================
def get_token():
    while True:
        try:
            r = requests.post(LOGIN_URL, json=CREDENTIALS, timeout=15)
            r.raise_for_status()
            token = r.json().get("token")
            if token:
                print("🔑 Login successful")
                return token
        except Exception as e:
            print(f"⚠ Login error: {e}")
        time.sleep(3)

# =====================================================
# SAFE DOWNLOAD + EXTRACT (.LIS.GZ)
# =====================================================
def safe_download_and_extract(segment, filename, token):
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "segment": segment,
        "folderPath": FOLDER_PATH,
        "filename": filename
    }

    gz_path = os.path.join(OUTPUT_DIR, filename)
    out_path = gz_path.replace(".gz", "")

    # Skip if already extracted
    if os.path.exists(out_path):
        print(f"✅ Already exists: {out_path}")
        return token

    attempt = 1
    while True:
        try:
            print(f"⬇️ Downloading {filename} (try {attempt})")
            r = requests.get(DOWNLOAD_URL, headers=headers, params=params, timeout=15)

            if r.status_code == 401:
                print("🚫 401 → Refreshing token")
                token = get_token()
                headers["Authorization"] = f"Bearer {token}"
                continue

            if r.status_code == 404:
                print(f"❌ File not found on NSE server: {filename}")
                MISSING_FILES.append(filename)
                global ALL_FILES_OK
                ALL_FILES_OK = False

                return token

            r.raise_for_status()

            # Save .gz
            with open(gz_path, "wb") as f:
                f.write(r.content)
            print(f"✅ Downloaded: {gz_path}")

            # Extract .lis
            with gzip.open(gz_path, "rb") as f_in:
                with open(out_path, "wb") as f_out:
                    f_out.write(f_in.read())
            print(f"📂 Extracted: {out_path}")

            os.remove(gz_path)
            print(f"🗑️ Deleted: {gz_path}")

            return token

        except Exception as e:
            print(f"🌐 Network error: {e}")
            time.sleep(3)
            attempt += 1

def nse_files_already_done(ddmmyyyy, nse_i):
    cm_out = os.path.join(OUTPUT_DIR, f"C_SA01_P_06436_{ddmmyyyy}_{nse_i}.lis")
    fo_out = os.path.join(OUTPUT_DIR, f"F_SA01_P_06436_{ddmmyyyy}_{nse_i}.lis")

    if os.path.exists(cm_out) and os.path.exists(fo_out):
        print("⏭️ NSE CM & FO files already exist locally, skipping NSE download")
        return True

    return False


# =====================================================
# MAIN
# =====================================================
def main():
    # 🔥 SA01 USES DDMMYYYY
    ddmmyyyy = _sa_today().strftime("%d%m%Y")
    print(f"📅 Date used: {ddmmyyyy}")

    # ---------------------------------------
    # 🔁 SKIP NSE IF FILES ALREADY EXIST
    # ---------------------------------------
    global NSE_OK
    if _sp.reuses_cf(run_no):
        # run 4's processing moved the 4th C/F files into its backup folder: bring them back (copy) - nothing to download for C/F
        _sp.restore_cf_from_backup(OUTPUT_DIR, nse_i_cf, _sa_today().strftime("%d-%m-%Y"), log=print)
    if nse_files_already_done(ddmmyyyy, nse_i_cf) and not _sp.reuses_cf(run_no):
        token = None
        NSE_OK = True
    else:
        token = get_token()

    # ===============================
    # CM + FO SA01 DOWNLOAD
    # ===============================
    if token:
        # CM SA01
        cm_file = f"C_SA01_P_06436_{ddmmyyyy}_{nse_i_cf}.lis.gz"
        token = safe_download_and_extract(
            segment="CM",
            filename=cm_file,
            token=token
        )

        # FO SA01
        fo_file = f"F_SA01_P_06436_{ddmmyyyy}_{nse_i_cf}.lis.gz"
        token = safe_download_and_extract(
            segment="FO",
            filename=fo_file,
            token=token
        )

        if ALL_FILES_OK:
            print("\n🎉 CM + FO SA01 FILES DOWNLOADED & EXTRACTED")
            NSE_OK = True
        else:
            print("\n⚠️ NSE CM/FO incomplete")

        # =====================================
        # OPTIONAL CO SA01 FILE
        # =====================================
        co_file = f"O_SA01_P_06436_{ddmmyyyy}_{nse_i_co}.lis.gz"

        print(f"🔎 Checking optional CO file: {co_file}")

        headers = {"Authorization": f"Bearer {token}"}
        params = {
            "segment": "CO",
            "folderPath": FOLDER_PATH,
            "filename": co_file
        }

        gz_path = os.path.join(OUTPUT_DIR, co_file)
        out_path = gz_path.replace(".gz", "")

        # Skip if already extracted
        if os.path.exists(out_path):
            print(f"⏭️ CO file already exists locally: {out_path}")

        else:

            attempt = 1

            while True:
                try:
                    print(f"⬇️ Downloading optional CO file (try {attempt})")

                    r = requests.get(
                        DOWNLOAD_URL,
                        headers=headers,
                        params=params,
                        timeout=15
                    )

                    if r.status_code == 401:
                        print("🚫 401 → Refreshing token")
                        token = get_token()
                        headers["Authorization"] = f"Bearer {token}"
                        continue

                    if r.status_code == 404:
                        print("ℹ️ Optional CO file not available — skipping")
                        break

                    r.raise_for_status()

                    with open(gz_path, "wb") as f:
                        f.write(r.content)

                    print(f"✅ CO file downloaded: {gz_path}")

                    with gzip.open(gz_path, "rb") as f_in:
                        with open(out_path, "wb") as f_out:
                            f_out.write(f_in.read())

                    print(f"📂 CO file extracted: {out_path}")

                    os.remove(gz_path)
                    break

                except Exception as e:
                    print(f"🌐 CO download network error: {e}")
                    print("🔁 Retrying CO download in 3 seconds...")
                    time.sleep(3)
                    attempt += 1


# =====================================================
# RUN
# =====================================================
if __name__ == "__main__":
    main()





#----------------------------------#
# MCX FILE DOWNLOAD  (requests-based, no Selenium)
#----------------------------------#

import time
import os
import requests as _mcx_requests
import urllib3 as _mcx_urllib3
import xml.etree.ElementTree as _mcx_ET
# Requires: pip install pyotp — MCX now sits behind TOTP-based 2FA on top of
# the password, same as MCX_BLOCKING_FINAL.py already handles.
import pyotp as _pyotp

_mcx_urllib3.disable_warnings(_mcx_urllib3.exceptions.InsecureRequestWarning)

# ===============================
# CONFIG
# ===============================
_MCX_BASE      = "https://sftp.mcxindia.com"
_mcx_username  = _SAENV("SA_MCX_USER", "56720")
_mcx_passwords = _SALIST("SA_MCX_PASSWORDS", ["M56720@789", "M56720@123"])

# Base32 secret from the MCX authenticator (TOTP) enrollment - the same
# secret the Google Authenticator/Authy app already uses to show a 6-digit
# code at manual login. Using it here does NOT disable or replace the phone
# app; both always show the same code, since both are driven by this same
# secret. Taken from MCX_BLOCKING_FINAL.py's MFA_TOTP_SECRET (that script
# already had to solve this same 2FA requirement for its own MCX login).
# Anyone with this string can generate valid MFA codes for this account
# indefinitely - treat it like a password. If MFA is ever reset/re-enrolled
# on the MCX portal, this value changes and must be updated here too.
_mcx_totp_secret = _SAENV("SA_MCX_TOTP_SECRET", "Q5BDU3WFOB4OWY5G7ISD3KYTFPY2N2IY")

download_dir = _SAENV("SA_OUTPUT_DIR", r"C:\KARTAVYA\SHORT ALLOCATION")
os.makedirs(download_dir, exist_ok=True)

def _parse_mcx_xml_field(xml_text, tag):
    """Pull a single tag's text out of MCX's flat <response>...</response> XML
    (same helper as MCX_BLOCKING_FINAL.py's parse_xml_field)."""
    try:
        root = _mcx_ET.fromstring(xml_text)
        el = root.find(tag)
        return el.text if el is not None else None
    except _mcx_ET.ParseError:
        return None

# ===============================
# CURRENT DATE LOGIC
# ===============================
today         = _sa_today()
today_date    = today.strftime("%Y%m%d")
today_display = today.strftime("%d-%m-%Y")   # also the MCX folder name

print(f"📅 Using CURRENT date: {today_display}")

# ONLY ONE FILE — run number matches SA run (mcx_i already set above)
file_name = f"MCX_PeakMargin{_mcx_username}_{today_date}_{mcx_i}.csv"

# ----------------------------------------
# 🔁 HARD SKIP if file already downloaded
# ----------------------------------------
mcx_local_path = os.path.join(download_dir, file_name)

def _attempt_mcx_download(file_name, download_dir, today_display, today_date):
    """Runs one full MCX Peak Margin login+download attempt.
    Sets globals MCX_OK / ALL_FILES_OK / MISSING_FILES exactly as the
    original single-attempt inline code did. Made into a function (instead
    of one-shot inline code) so the second-failure retry loop below can
    call it again if the file arrives on the exchange server later."""
    global MCX_OK, ALL_FILES_OK, MISSING_FILES

    _mcx_session = _mcx_requests.Session()

    # ---- TOTP (2FA) COMPLETION ----
    def _mcx_complete_totp(login_csrf):
        """Submit the TOTP code MCX now requires right after password
        acceptance - mirrors MCX_BLOCKING_FINAL.py's complete_totp(), just
        posting through this function's own _mcx_session instead of a
        module-level one."""
        if not _mcx_totp_secret:
            print("❌ MCX now requires a TOTP code and SA_MCX_TOTP_SECRET is "
                  "not set - fill it in (see the MCX CONFIG section above).")
            return False
        code = _pyotp.TOTP(_mcx_totp_secret).now()
        print("🔑 MCX TOTP required — submitting code")
        try:
            res = _mcx_session.post(
                _MCX_BASE + f"/Web%20Client/Login.xml?Command=TOTPPassin&Sync={int(time.time()*1000)}",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": _MCX_BASE,
                    "Referer": _MCX_BASE + "/?command=logout",
                    "X-Csrf-Token": login_csrf,
                },
                data={"passcode": code, "role": "validation"},
                verify=False, timeout=15,
            )
        except Exception as _e:
            print(f"⚠️ MCX TOTP submission error: {_e}")
            return False
        if "successful" not in res.text.lower():
            print(f"❌ MCX TOTP validation failed: {res.text[:200]}")
            return False
        print("✅ MCX TOTP accepted")
        return True

    # ---- LOGIN ----
    def _mcx_login():
        for pwd in _mcx_passwords:
            print(f"🔐 MCX trying: {pwd}")
            try:
                res = _mcx_session.post(
                    _MCX_BASE + f"/Web%20Client/Login.xml?Command=Login&Sync={int(time.time()*1000)}",
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": _MCX_BASE,
                        "Referer": _MCX_BASE + "/?command=logout",
                    },
                    data={"user": _mcx_username, "pword": pwd, "language": "en,US"},
                    verify=False, timeout=15,
                )
                if "successful" in res.text.lower():
                    print("✅ MCX password accepted")
                    # A successful password response no longer means the
                    # session is fully authenticated by itself - MCX now
                    # sits behind TOTP-based 2FA on top of it. Check
                    # MFATOTPStatus the same way MCX_BLOCKING_FINAL.py does,
                    # and complete the TOTP step if required before treating
                    # this as a real login success.
                    login_csrf = _parse_mcx_xml_field(res.text, "CsrfToken")
                    mfa_status = _parse_mcx_xml_field(res.text, "MFATOTPStatus")
                    tries_left = _parse_mcx_xml_field(res.text, "TOTPLoginTriesLeft")
                    if mfa_status == "USES":
                        print(f"🔑 MCX TOTP required (tries left: {tries_left})")
                        if not _mcx_complete_totp(login_csrf):
                            # Password was correct - a TOTP failure isn't
                            # fixed by trying a different password, so stop
                            # here rather than burning the next password
                            # attempt on an already-known-good one.
                            return False
                    print("✅ MCX login successful")
                    return True
                print(f"   Response snippet: {res.text[:80]}")
            except Exception as _e:
                print(f"⚠️ MCX login error: {_e}")
        return False

    # ---- SESSION INIT (CSRF) ----
    def _mcx_init_session():
        res = _mcx_session.get(
            _MCX_BASE + "/WebClientApi/SessionInfo.json",
            verify=False, timeout=30,
        )
        csrf = res.json()["data"]["csrfToken"]
        _hdrs = {"X-Csrf-Token": csrf}
        _mcx_session.get(_MCX_BASE + "/WebClientApi/UserInfo.json", headers=_hdrs, verify=False, timeout=30)
        _mcx_session.get(_MCX_BASE + "/WebClientApi/Mfa.json",      headers=_hdrs, verify=False, timeout=30)
        _mcx_session.get(
            _MCX_BASE + "/",
            params={"Command": "List", "Dir": "~", "ListFile": "List.json", "NewWebClient": "1"},
            headers=_hdrs, verify=False, timeout=30,
        )
        print("✅ MCX CSRF token acquired")
        return csrf

    # ---- LIST FOLDER (navigates into date folder — required by Serv-U before download) ----
    def _mcx_list_folder(folder, csrf):
        """Navigate into the date folder. Serv-U requires a List before Download
        or it returns an HTML session-error page instead of the file."""
        res = _mcx_session.get(
            _MCX_BASE + "/",
            params={"Command": "List", "Dir": f"/{folder}", "ListFile": "List.json", "NewWebClient": "1"},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "X-Csrf-Token": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{_MCX_BASE}/WebClientNew/Browser/%2F{folder}",
            },
            verify=False, timeout=30,
        )
        try:
            data = res.json()
            files = [f["name"] for f in data.get("files", []) if f.get("name")]
            print(f"📂 MCX folder /{folder}: {len(files)} file(s) found")
            return files
        except Exception:
            print(f"⚠️ MCX folder list failed — will try download anyway")
            return []

    # ---- DOWNLOAD ONE FILE DIRECTLY ----
    def _mcx_download_file(folder, filename, csrf, dest_dir):
        dest = os.path.join(dest_dir, filename)
        res = _mcx_session.get(
            _MCX_BASE + "/",
            params={"Command": "Download", "File": f"/{folder}/{filename}", "CsrfToken": csrf},
            headers={
                "User-Agent": "Mozilla/5.0",
                "X-Csrf-Token": csrf,
                "Referer": f"{_MCX_BASE}/WebClientNew/Browser/%2F{folder}",
            },
            verify=False, timeout=60, stream=True,
        )
        if res.status_code == 404:
            print(f"❌ File not on MCX server yet: {filename}")
            return False
        if res.status_code != 200:
            print(f"❌ MCX download HTTP {res.status_code}: {filename}")
            return False
        with open(dest, "wb") as _f:
            for chunk in res.iter_content(8192):
                _f.write(chunk)
        # ── CRITICAL: server sometimes returns HTTP 200 with an HTML error page
        # (e.g. "Your session may have timed out") instead of the actual CSV.
        # Detect this by checking the file header bytes. If HTML, reject it.
        try:
            with open(dest, "rb") as _fchk:
                header = _fchk.read(128).lower()
            html_markers = (b"<!doctype", b"<html", b"<head>", b"your session", b"error occurred")
            if any(m in header for m in html_markers):
                os.remove(dest)
                print(f"🚨 SESSION EXPIRED (MCX Peak Margin) — server returned an HTML session "
                      f"page instead of the CSV (could also mean the password changed).")
                print(f"   File deleted: {filename}. MCX will be treated as missing — this "
                      f"downloader doesn't retry/relogin, so a later scheduled run will need "
                      f"to fetch it fresh.")
                return False
        except Exception as _ve:
            print(f"⚠️  Could not validate {filename}: {_ve}")
        kb = os.path.getsize(dest) / 1024
        print(f"✅ MCX downloaded: {filename}  ({kb:.1f} KB)")
        return True

    # ---- EXECUTE ----
    _mcx_dl_ok = False
    try:
        if _mcx_login():
            _csrf = _mcx_init_session()
            # ══════════════════════════════════════════════════════════════
            # EXACT FILENAME ONLY — NO SUBSTITUTION, EVER.
            #
            # Run NN downloads MCX_PeakMargin<user>_<date>_<NN>.csv and nothing
            # else. If that exact file is not on the exchange, it is reported
            # missing and we WAIT for it. That is the only correct behaviour.
            #
            # Why this is written so emphatically — 2026-08-18 incident:
            # this block used to "helpfully" fall back to any file matching
            # member+date when the exact name wasn't in the listing, with the
            # run number deliberately left OUT of the match. SA-03 ran, the
            # exchange had only published _01, and the code silently:
            #   1. downloaded MCX_PeakMargin56720_20260818_01.csv, then
            #   2. RENAMED it locally to ..._03.csv (os.replace),
            #   3. reported "🎉 MCX file ready" and carried on.
            # Run 01's intraday snapshot was then used as run 03's data through
            # every calculation, email, WhatsApp alert and DB archive — with a
            # filename on disk that said _03. It was undetectable after the
            # fact. The rename is what made it undetectable; the loose match is
            # what made it happen.
            #
            # Each _NN is a DIFFERENT point-in-time snapshot of peak margin.
            # A different run number is not a naming quirk, a near-enough
            # match, or a usable stand-in — it is different data. There is no
            # scenario in which substituting one for another is preferable to
            # waiting, because waiting is visible and wrong numbers are not.
            #
            # DO NOT reintroduce a fallback here. If a future exchange-side
            # naming change breaks this, fix the expected-name construction
            # (file_name, above) so it is exact again — never widen the match.
            # ══════════════════════════════════════════════════════════════
            _remote_files = _mcx_list_folder(today_display, _csrf)
            if _remote_files and file_name not in _remote_files:
                _same_day = sorted(
                    f for f in _remote_files
                    if f.upper().startswith(
                        f"MCX_PeakMargin{_mcx_username}_{today_date}".upper())
                )
                print(f"📂 MCX folder has {len(_remote_files)} file(s), but run {mcx_i}'s "
                      f"file '{file_name}' is NOT among them — not published by the "
                      f"exchange yet.")
                if _same_day:
                    print(f"   Other runs present today: {', '.join(_same_day)}")
                    print(f"   These are NOT used — a different run number is a different "
                          f"intraday snapshot, not a substitute. Waiting for run {mcx_i}'s "
                          f"own file.")
                _mcx_dl_ok = False
            else:
                # Either the exact file is listed, or the listing itself was
                # unavailable — in both cases request the EXACT expected name
                # and let the server 404 if it isn't there.
                print(f"⬇️  Downloading MCX: {file_name}")
                _mcx_dl_ok = _mcx_download_file(today_display, file_name, _csrf, download_dir)
        else:
            print("❌ MCX login failed — all passwords exhausted")
    except Exception as _ex:
        print(f"❌ MCX download exception: {_ex}")

    if _mcx_dl_ok:
        MCX_OK = True
        print("🎉 MCX file ready")
    else:
        if file_name not in MISSING_FILES:
            MISSING_FILES.append(file_name)
        ALL_FILES_OK = False
        print(f"⚠️ MCX file missing: {file_name}")
        _log_file = os.path.join(download_dir, f"MCX_Peak_MissingFiles_{today_date}.log")
        with open(_log_file, "w") as _lf:
            _lf.write(f"Missing MCX Files for {today_display}\n")
            _lf.write("-" * 50 + "\n")
            _lf.write(file_name + "\n")

    return _mcx_dl_ok


if os.path.exists(mcx_local_path):
    print(f"⏭️ MCX file already exists, skipping: {file_name}")
    MCX_OK = True
else:
    _attempt_mcx_download(file_name, download_dir, today_display, today_date)

if not (NSE_OK and MCX_OK):
    print("⚠️ NSE or MCX incomplete — will handle at final decision")
    ALL_FILES_OK = False



# ==============================
# NCDEX FILE DOWNLOAD
# ==============================

goto_ncdex = True

today_yyyymmdd = _sa_today().strftime("%Y%m%d")
ncdex_file = f"NCCL_MARGIN_REP_01292_{today_yyyymmdd}_{mcx_i}.csv"
ncdex_local = os.path.join(OUTPUT_DIR, ncdex_file)

# ---------- HARD SKIP ----------
if os.path.exists(ncdex_local):
    print(f"⏭️ NCDEX file already exists locally, skipping NCDEX download")
    NCDEX_OK = True
    goto_ncdex = False


if goto_ncdex:
    # ── requests-based downloader (no Selenium, CAPTCHA shown in terminal) ──
    import sys as _sys
    # Try the script's own directory first; fall back to the known sa_codes location.
    # This means the script works whether run from sa_codes/ or any other directory.
    _sa_codes = os.path.dirname(os.path.abspath(__file__))
    _sa_codes_fallback = _SAENV("SA_CODES_DIR", r"D:\CLAUDE\rms-copilot\sa_codes")
    for _p in [_sa_codes, _sa_codes_fallback]:
        if os.path.isdir(_p) and _p not in _sys.path:
            _sys.path.insert(0, _p)
    from ncdex_downloader import download_ncdex

    _ncdex_result = download_ncdex(output_dir=OUTPUT_DIR, run_suffix=mcx_i)
    if _ncdex_result:
        print(f"✅ NCDEX downloaded: {_ncdex_result}")
        NCDEX_OK = True
    else:
        print(f"❌ NCDEX download failed: {ncdex_file}")
        MISSING_FILES.append(ncdex_file)
        ALL_FILES_OK = False



ALL_FILES_OK = NSE_OK and MCX_OK and NCDEX_OK

if ALL_FILES_OK:
    print("✅ All required files present (downloaded OR already existing)")
    mark_run_success()
    sys.exit(0)

else:
    if SA_AUTO:
        # AUTOMATIC run: never a pop-up, never a person to wait for. Say exactly what is missing and stop; the auto-runner
        # waits the configured minutes, tries again, and sends the WhatsApp alert when the retries are used up.
        print("[SA_PEAK_MISSING] " + ", ".join(MISSING_FILES), flush=True)
        sys.exit(3)

    retry_count = get_retry_flag()

    # ==============================
    # FIRST FAILURE → EMAIL + WEB DECISION MODAL
    # ==============================
    if retry_count == 0:
        print("⚠️ First failure detected — emailing team and showing decision modal")

        send_failure_email(MISSING_FILES)
        set_retry_flag(1)

        decision = _web_missing_decision(
            MISSING_FILES, OUTPUT_DIR,
            context="first_failure",
            timeout_secs=300   # 5-min window to decide (same as old silent wait)
        )

        if decision == "RUN_BLANK":
            blanked = _apply_blank_files(MISSING_FILES, OUTPUT_DIR)
            if blanked:
                print("✅ Blank files created — continuing run without missing exchange files.")
                mark_run_success()
                sys.exit(0)
            else:
                print("⚠️ Could not create blank files — falling back to retry")

        # WAIT decision (or blank creation failed) → do the regular retry
        print("⏳ Waiting 5 minutes before retrying downloads...")
        time.sleep(300)
        print("🔁 Restarting Code-1 internally (batch will NOT see failure)")
        os.execv(sys.executable, [sys.executable] + sys.argv)


    # ==============================
    # SECOND FAILURE → EMAIL + WEB DECISION MODAL (manual mode)
    # ==============================
    else:
        print("🚨 Second failure — manual intervention required")
        send_manual_intervention_email(MISSING_FILES)

        while True:
            decision = _web_missing_decision(
                MISSING_FILES, OUTPUT_DIR,
                context="manual_required",
                timeout_secs=600   # 10-min window; loops if WAIT is chosen
            )

            if decision == "RUN_BLANK":
                blanked = _apply_blank_files(MISSING_FILES, OUTPUT_DIR)
                if blanked:
                    print("✅ Blank files created — continuing run without missing exchange files.")
                    mark_run_success()
                    sys.exit(0)
                else:
                    print("⚠️ Could not auto-blank — falling back to manual placement check")

            # WAIT — actively re-attempt the exchange downloads (the file may
            # have simply arrived late on the exchange server since the last
            # try), THEN fall back to checking if it was placed manually.
            if file_name in MISSING_FILES and not os.path.exists(mcx_local_path):
                print("🔁 Re-attempting MCX Peak Margin download before re-checking…")
                if _attempt_mcx_download(file_name, download_dir, today_display, today_date):
                    if file_name in MISSING_FILES:
                        MISSING_FILES.remove(file_name)
                    print("🎉 MCX file ready on retry")

            if ncdex_file in MISSING_FILES and not os.path.exists(ncdex_local):
                print("🔁 Re-attempting NCDEX download before re-checking…")
                _ncdex_retry = download_ncdex(output_dir=OUTPUT_DIR, run_suffix=mcx_i)
                if _ncdex_retry:
                    print(f"✅ NCDEX downloaded on retry: {_ncdex_retry}")
                    if ncdex_file in MISSING_FILES:
                        MISSING_FILES.remove(ncdex_file)

            # WAIT — check if files were placed manually in the meantime
            if all_missing_files_now_exist(MISSING_FILES):
                print("✅ Files placed manually. Continuing as SUCCESS.")
                mark_run_success()
                sys.exit(0)
            else:
                print("⏳ Waiting 5 minutes before re-showing the decision modal…")
                time.sleep(300)
                print("⚠️ Files still missing — re-showing decision modal.")



