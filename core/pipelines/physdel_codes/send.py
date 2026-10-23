"""PHYSICAL DELIVERY - step 2: split by branch / region / zone, e-mail them, WhatsApp the clients and the ALERT 1 CR
dashboards, e-mail the logs, then back everything up.

Moved into the console from FINAL_SMS_EMAIL_updated.PY.  The report logic is unchanged.  What changed:
  * folders come from core.pipelines.physdel_cfg (inside the console), not from C:\\KARTAVYA
  * e-mail account, WhatsApp token, zone recipients and WhatsApp numbers come from the console's Credentials /
    Email To-CC / WhatsApp numbers buttons (.env)
  * a "Safe test run" sends everything to the tester only and skips the backup
  * every step that fails is remembered in STEP_ERRORS so the run can say it finished with errors
  * the backup folder is  BACKUPFILES AUTOMATED\\<DD-MM-YYYY>\\<HH-MM-SS>
"""
# ==========================================================
#  PHYSICAL DELIVERY — NEXT STEPS PIPELINE  (updated)
# ==========================================================
#
#  WHAT CHANGED vs your previous version
#  -------------------------------------
#  1. ONE base folder. Every input/output path is built from
#     BASE_FOLDER below. No more bare filenames that depend on
#     "wherever the script happened to be launched from".
#  2. All 82 branch codes (the 66 from 10BRANCH.py + the 16 that
#     only existed as separate 10.x scripts). Nothing left behind.
#  3. The 2% is now DYNAMIC. It is read straight out of the
#     PositionBook column names (e.g. "CASH 2.0% UP") that the
#     first script writes, so the emails and the WhatsApp message
#     always match the percentage chosen in the popup.
#  4. Each step is isolated. If one step errors (e.g. Excel hiccup
#     on zone encryption) the remaining steps still run.
#  5. Backup no longer moves your inputs or the scripts.
#
#  EXPECTED FOLDER LAYOUT
#  ----------------------
#  BASE_FOLDER/
#      MAIN_FOLDER/
#          Copy of T1 MAIL LIST.xlsx      <- you place this here
#          Branch_AND_Region_LIST.xlsx    <- you place this here
#      PositionBook_With_LTP.xlsx         <- produced by Script 1
#      BRANCH/  REGION/  ZONE/            <- created automatically
#      *_log.xlsx, sms_log.xlsx           <- created automatically
#      BACKUP/<timestamp>/                <- archive of each run
#
#  IMPORTANT: Script 1 (the NIFTY500/PositionBook script) must
#  save PositionBook_With_LTP.xlsx into BASE_FOLDER (or the folder
#  you launch it from). This script looks in BASE_FOLDER first,
#  then in the current working directory.
# ==========================================================

import os
import re
import shutil
import smtplib
import requests
import pandas as pd
import time
import calendar
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from PIL import Image, ImageDraw, ImageFont

from threading import Lock

sms_log_lock = Lock()

sent_clients = set()
sent_lock = Lock()

# win32com (Excel) is only needed to password-protect the zone files.
# If it is unavailable, the zone files are still produced and emailed
# as plain CSV (same as the old behaviour) instead of crashing.
try:
    import win32com.client as win32
    HAVE_WIN32 = True
except Exception:
    HAVE_WIN32 = False


# ==========================================================
# 1) BASE FOLDER  —  SET THIS ONE PATH
# ==========================================================
# Everything else is built from here. You can also override it
# without editing the file by setting an environment variable
# named PHY_DELIVERY_BASE.

from core.pipelines import physdel_cfg as C      # all folders / settings come from the console's own config

BASE_FOLDER = C.base()

MAIN_FOLDER   = os.path.join(BASE_FOLDER, "MAIN_FOLDER")   # inputs live here
BRANCH_FOLDER = os.path.join(BASE_FOLDER, "BRANCH")
REGION_FOLDER = os.path.join(BASE_FOLDER, "REGION")
ZONE_FOLDER   = os.path.join(BASE_FOLDER, "ZONE")
BACKUP_FOLDER = os.path.join(BASE_FOLDER, "BACKUPFILES AUTOMATED")
INPUT_FOLDER = C.input_dir()

# ==========================================================
# INPUT FILES
# ==========================================================

POSITIONBOOK_FILE = os.path.join(
    INPUT_FOLDER,
    "PositionBook.xlsx"
)

RISK_REPORT_FILE = os.path.join(
    INPUT_FOLDER,
    f"RISK_REPORT_{datetime.now().strftime('%d%m%Y')}.csv"
)

POSITIONBOOK_WITH_LTP_FILE = os.path.join(
    BASE_FOLDER,
    "PositionBook_With_LTP.xlsx"
)

NIFTY500_FILE = os.path.join(
    BASE_FOLDER,
    "NIFTY500_CONSTITUENTS.xlsx"
)


def find_file(filename, *search_dirs):
    """Return the first existing path for filename across search_dirs.
    If none exists, return the path in the first dir (for a clear error)."""
    for d in search_dirs:
        candidate = os.path.join(d, filename)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(search_dirs[0], filename)


# Inputs (resolved now; they already exist by the time this runs)
INPUT_FILE = find_file(
    "PositionBook_With_LTP.xlsx", BASE_FOLDER
)
MAIL_LIST_FILE = find_file(
    "Copy of T1 MAIL LIST.xlsx", MAIN_FOLDER, BASE_FOLDER
)
BRANCH_REGION_REF_FILE = find_file(
    "Branch_AND_Region_LIST.xlsx", MAIN_FOLDER, BASE_FOLDER
)

# Outputs (always under BASE_FOLDER)
UNIQUE_CLIENT_FILE = os.path.join(ZONE_FOLDER, "Unique_ClientID.csv")
SMS_LOG_FILE       = os.path.join(BASE_FOLDER, "sms_log.csv")
BRANCH_EMAIL_LOG   = os.path.join(BASE_FOLDER, "BRANCH_email_log.xlsx")
REGION_EMAIL_LOG   = os.path.join(BASE_FOLDER, "REGION_MAIL_log.xlsx")


# ==========================================================
# 2) CREDENTIALS  —  CHANGE THESE (the only secret lines)
# ==========================================================

SMTP_SERVER    = os.getenv("PD_SMTP_SERVER", "smtp3.netcore.co.in")
SMTP_PORT      = int(os.getenv("PD_SMTP_PORT", "587") or 587)
# Sender account = the PROBABLE alerts sender (the same raj.kumari account this script always used).
EMAIL_ADDRESS  = os.getenv("PROBABLE_ALERTS_SMTP_EMAIL", "")
EMAIL_PASSWORD = os.getenv("PROBABLE_ALERTS_SMTP_PASSWORD", "")

EMAIL_REGEX = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,10}$"

# WhatsApp / mobile-lookup APIs
OAUTH_TOKEN_URL   = "https://webpayout.mastertrust.co.in/api/Tradingdata/OAUTH_TOKEN"
CLIENT_MOBILE_URL = "https://webpayout.mastertrust.co.in/api/Codecheck/LIV_CHECKMOBILE?clientcode={client_code}"
WHATSAPP_URL      = "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/messages"

WHATSAPP_AUTH_TOKEN = os.getenv("WA_AUTH_TOKEN", "")     # the Tata WhatsApp token (Credentials button)

OAUTH_PAYLOAD = {
    "grant_type": "password",
    "username": os.getenv("MT_WEBPAYOUT_USER", ""),
    "password": os.getenv("MT_WEBPAYOUT_PASSWORD", "")
}


WHATSAPP_HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_AUTH_TOKEN}",
    "Content-Type":  "application/json"
}

# ==========================================================
# ALERT 1 CR IMAGES  -  CONFIG
# ==========================================================
# 5 SEND LISTS: which PIVOT zone codes go in each image + who receives it.
# Numbers: 10-digit or +91..., add / remove freely.
# "zones": None  ->  ALL zones (the FULL image).
ALERT_LISTS = {
    "DELHI": {
        "zones": ["ZODELH", "ZOMT", "ZOCDR", "ZO"],
        "numbers": C.wa_numbers("DELHI", ["9899547339", "9711667470", "9871410617"]),
    },
    "LUDHIANA": {
        "zones": ["ZOLUDH"],
        "numbers": C.wa_numbers("LUDHIANA", ["9888556665"]),
    },
    "CHANDIGARH": {
        "zones": ["ZOCHND"],
        "numbers": C.wa_numbers("CHANDIGARH", ["9914122055", "9814566866"]),
    },
    "MUMBAI": {
        "zones": ["ZOMUMB"],
        "numbers": C.wa_numbers("MUMBAI", ["8655253655"]),
    },
    "FULL": {
        "zones": None,
        "numbers": C.wa_numbers("FULL", ["7889026088", "9899547339", "9711667470", "9871410617"]),
    },
}

# zone code -> list name (groups the summary chips on the FULL image)
ALERT_ZONE_TO_GROUP = {z: name for name, cfg in ALERT_LISTS.items()
                       if cfg["zones"] for z in cfg["zones"]}

ALERT_IMAGE_SUBFOLDER = "ALERT_1CR_IMAGES"    # inside BASE_FOLDER (backup does not touch it)
ALERT_MEDIA_URL       = "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/media"
ALERT_SEND_URL        = WHATSAPP_URL          # same messages endpoint as above
ALERT_TEMPLATE_NAME   = "daily_report"        # image-header template, 2 text variables
ALERT_REPORT_LABEL    = "ALERT_1CR"           # body variable {{1}} = ALERT_1CR_<LIST NAME>
ALERT_REMARK_KEY      = "ALERT 1 CR"          # picks "ALERT 1 CR", "ALERT 1 CR, ALERT", ...
ALERT_MAX_ROWS        = 40                    # ONE image -> top N clients by delivery value
ALERT_IMAGE_FORCE     = C.test_mode()                # True = skip the expiry-day check (testing only)

ALERT_NAVY  = (15, 35, 70)
ALERT_TEAL  = (25, 110, 140)
ALERT_LIGHT = (245, 247, 250)
ALERT_ZEBRA = (234, 241, 248)
ALERT_BORDER = (200, 208, 218)
ALERT_RED   = (176, 0, 32)
ALERT_GREEN = (20, 120, 70)
ALERT_BLACK = (25, 25, 25)
ALERT_WHITE = (255, 255, 255)
ALERT_GREY  = (110, 120, 135)


# Password applied to the zone Excel files
ZONE_FILE_PASSWORD = os.getenv("PROBABLE_ZONE_XLSX_PASSWORD", "Master@2024")


# ==========================================================
# PRE RUN VALIDATION
# ==========================================================

def validate_required_files():

    print("\n" + "=" * 60)
    print("PRE RUN VALIDATION")
    print("=" * 60)

    required_files = {

        "PositionBook_With_LTP":
            INPUT_FILE,

        "Mail List":
            MAIL_LIST_FILE,

        "Branch Region Master":
            BRANCH_REGION_REF_FILE

    }

    missing_files = []

    for file_name, file_path in required_files.items():

        if os.path.exists(file_path):

            print(f"✅ {file_name}")
            print(f"   {file_path}")

        else:

            print(f"❌ {file_name}")
            print(f"   {file_path}")

            missing_files.append(file_name)

    print("-" * 60)

    if missing_files:

        print("\n❌ VALIDATION FAILED")

        print("\nMissing Files:")

        for file_name in missing_files:

            print(f"   • {file_name}")

        raise SystemExit

    print("\n✅ ALL REQUIRED FILES FOUND")
    print("=" * 60)


# ==========================================================
# 3) ZONE EMAIL RECIPIENTS  (unchanged — matches old EE/FF/GG/HH)
# ==========================================================

ZONE_EMAILS = {
    "ZOCHND": {
        "to": C.email_list("PD_ZONE_CHND_TO"),
        "cc": C.email_list("PD_ZONE_CHND_CC"),
        "file": os.path.join(ZONE_FOLDER, "Physical_Delivery_CHND_ZONE_Data.csv")
    },
    "ZOLUDH": {
        "to": C.email_list("PD_ZONE_LUDH_TO"),
        "cc": C.email_list("PD_ZONE_LUDH_CC"),
        "file": os.path.join(ZONE_FOLDER, "Physical_Delivery_ZOLUDH_ZONE_Data.csv")
    },
    "ZOMUMB": {
        "to": C.email_list("PD_ZONE_MUMB_TO"),
        "cc": C.email_list("PD_ZONE_MUMB_CC"),
        "file": os.path.join(ZONE_FOLDER, "Physical_Delivery_ZOMUMB_ZONE_Data.csv")
    },
    "ZODELH": {
        "to": C.email_list("PD_ZONE_DELH_TO"),
        "cc": C.email_list("PD_ZONE_DELH_CC"),
        # Delhi zone also attaches mismatch file + FULL_FILE - same as the old HH script
        "file": os.path.join(ZONE_FOLDER, "Physical_Delivery_DELHI_ZONE_Data.csv"),
        "extra_files": [
            os.path.join(ZONE_FOLDER, "DELHI_Mismatched_Branch_And_Region_IDs.xlsx"),
            os.path.join(ZONE_FOLDER, "FULL_FILE.xlsx")
        ]
    }
}


# ==========================================================
# 4) BRANCH LIST  (66 from 10BRANCH.py + 16 from the 10.x scripts)
# ==========================================================

ALL_BRANCHES = [
    # --- original master list (10BRANCH.py) ---
    "BRDX", "BJAL", "BTAN", "BHS", "BDL", "BKHN", "B8MAA", "BRC", "BKD", "BDNR", "BMKT",
    "B6DDA", "BS7", "BTAE", "B6DID", "B6DYW", "B6DDZ", "B6DBU", "BKC", "BV",
    "BUNA", "BPAL", "BPATE", "BTCS", "BVTMD", "BRBH", "BVTGP", "BKG", "B7AVW", "BHF", "BK",
    "B6DIV", "B6DXZ", "BTBJ", "B6DHQ", "B6DIH", "B6DIE", "BCAP", "BKA", "BALKA", "BCDOB", "BRN",
    "B6DZC", "B6DSA", "B6GAG", "BTALW", "B6DDE", "BTAG", "BMKAC", "BOM", "B6DPB", "BNRN", "BNG",
    "BBAJA", "BCRAA", "BTBA", "BRQ", "BPAR", "B6DMB", "BMI", "B6DIW", "BTBPC", "B6QNT",
    "B6DIT", "B6DIF", "BDC",
    # --- ADDED: branches that previously only had their own 10.x script ---
    "B6DEU", "B8MSD", "B8MSB", "B8MSS", "B6DIJ", "BGURD", "BSHAR", "BBATA",
    "BROH4", "BA", "BAMB1", "BNSPD", "B6DIX", "B6DIY", "BVTSM", "BRV","B6DSC","B6RBB", "BGH",
]


# ==========================================================
# 5) REGION GROUPS  (unchanged — matches old 12..18.4)
# ==========================================================

REGION_GROUPS = {
    "RAL":    ["RAL"],
    "RMT_R":  ["RMT"],
    "RJD":    ["RJD"],
    "RLKO":   ["RLKO"],
    "RND":    ["RND"],
    "ROD":    ["ROD"],
    "RBARO":  ["RBARO"],
    "RBD":    ["RBD"],
    "RWA":    ["RWA", "RWA1", "RWA2", "RWA3", "RBHUB"],
    "RHYD":   ["RHYD", "RHYD2"],
    "RDLNA":  ["RDLNA", "RHYD3", "RBGLR", "RCHAI"],
    "RPCG":   ["RPCG"],
    "RCP":    ["RCP"],
    "RCDSK":  ["RCDSK"],
    "DSA_RO": ["RODSA"],
    "RCDRR":  ["RCDRR", "RTTDZ", "BTTCH"],
    "RMT":    ["RMT", "RDLTT", "RLDTT", "RMMTT", "RCHTT"],
    "RKANP":  ["RKANP"],
    "RNCR" :   ["RNCR"],
}


# ==========================================================
# 6) ZONE FILTER MAP  (unchanged — matches old E/F/G/H)
# ==========================================================

ZONE_FILTER_MAP = {
    "Physical_Delivery_CHND_ZONE_Data.csv":   ["ZOCHND"],
    "Physical_Delivery_ZOLUDH_ZONE_Data.csv": ["ZOLUDH", "ZO"],
    "Physical_Delivery_ZOMUMB_ZONE_Data.csv": ["ZOMUMB"],
    "Physical_Delivery_DELHI_ZONE_Data.csv":  ["ZOCDR", "ZODELH", "ZOMT"],
}


# ==========================================================
# DYNAMIC PERCENTAGE  —  read from the PositionBook columns
# ==========================================================
# Script 1 creates columns named like "CASH 2% UP"/ "CASH 2% DOWN"
# We read the number out of that column name so every message uses the
# same percentage that was chosen in the popup.

_PERCENT_PATTERN = re.compile(r"CASH\s+([0-9]+(?:\.[0-9]+)?)\s*%\s*UP", re.IGNORECASE)


def detect_percentage(df, default="2"):
    """Return the cash percentage as a clean display string, e.g. '2' or '2.5'."""
    for col in df.columns:
        m = _PERCENT_PATTERN.search(str(col))
        if m:
            disp = f"{float(m.group(1)):g}"   # 2.0 -> '2', 2.5 -> '2.5'
            print(f"📈 Percentage Detected : {disp}%")
            return disp
    print(f"  [PERCENT][WARN] Could not detect percentage from columns; "
          f"falling back to {default}%.")
    return default


def build_email_body(pct):
    return (
        "Dear Team,\n\n"
        f"Please find the enclosed attachment of Tentative Physical Delivery "
        f"positions with cash rate {pct}% up and {pct}% down.\n\n"
        "Best regards,\n"
        "Raj Kumari\n"
        "011-66331033 / 37 / 38"
    )


EMAIL_SUBJECT = "Tentative Physical Delivery Codes"


# ==========================================================
# HELPERS
# ==========================================================

def clean_email_addresses(email_string):
    if pd.isna(email_string) or not str(email_string).strip():
        return [], []
    emails  = [e.strip().strip("'\"") for e in str(email_string).split(",") if e.strip()]
    valid   = [e for e in emails if re.match(EMAIL_REGEX, e)]
    invalid = [e for e in emails if e not in valid]
    return valid, invalid


def log_email(log_file_path, timestamp, receiver, entity_name, entity_col, status, smtp_status):
    entry = pd.DataFrame([{
        "Timestamp":          timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "Receiver":           receiver if receiver else "No Receiver",
        entity_col:           entity_name if entity_name else "N/A",
        "Status":             status,
        "SMTP_SERVER_STATUS": smtp_status,
    }])
    try:
        if os.path.exists(log_file_path):
            existing = pd.read_excel(log_file_path)
            entry    = pd.concat([existing, entry], ignore_index=True)
        entry.to_excel(log_file_path, index=False)
    except Exception as e:
        print(f"  [LOG ERROR] {e}")


def send_email_with_attachments(to_list, cc_list, subject, body, file_paths):

    if C.test_mode():                      # SAFE TEST: nothing reaches a branch / region / zone, CC dropped
        to_list, cc_list = C.test_email_to(), []
        subject = "[TEST - IGNORE] " + subject

    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = ", ".join(to_list)
    msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    for fp in file_paths:
        if not os.path.exists(fp):
            print(f"  [ATTACH SKIP] File not found: {fp}")
            continue

        with open(fp, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())

        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(fp)}"'
        )
        msg.attach(part)

    attempt = 1

    while True:
        try:
            print(f"📧 Sending Mail | Attempt #{attempt}")

            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()

                server.login(
                    EMAIL_ADDRESS,
                    EMAIL_PASSWORD
                )

                server.sendmail(
                    EMAIL_ADDRESS,
                    to_list + cc_list,
                    msg.as_string()
                )

            print("✅ Mail Delivered")
            return True

        except Exception as e:

            print(f"❌ Mail Failed | Attempt #{attempt}")
            print(f"❌ Reason : {e}")

            print("🔄 Retrying after 10 seconds...")

            attempt += 1
            time.sleep(10)

def log_sms_status(client_code, client_number, status):

    with sms_log_lock:

        masked = (
            "******" + str(client_number)[-4:]
        ) if client_number else "N/A"

        entry = pd.DataFrame(
            [[
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                client_code,
                masked,
                status
            ]],
            columns=[
                "Timestamp",
                "Client Code",
                "Client Number",
                "Status"
            ]
        )

        entry.to_csv(
            SMS_LOG_FILE,
            mode="a",
            header=not os.path.exists(SMS_LOG_FILE),
            index=False
        )


def load_mail_list():
    """Read the mail list and normalise its column names (strips the
    trailing space in 'TO ' etc., which otherwise silently kills sends)."""
    mail_df = pd.read_excel(MAIL_LIST_FILE)
    mail_df.columns = [str(c).strip() for c in mail_df.columns]
    return mail_df


# ==========================================================
# STEP 1 — SPLIT BY BRANCH
# ==========================================================

def step_split_by_branch(df):
    print("\n" + "=" * 60)
    print("STEP 1 : SPLITTING BY BRANCH")
    print("=" * 60)

    os.makedirs(BRANCH_FOLDER, exist_ok=True)

    if "Branch" not in df.columns:
        print("  [ERROR] 'Branch' column not found. Skipping branch split.")
        return

    created = 0
    for branch in ALL_BRANCHES:
        branch_data = df[df["Branch"] == branch]
        if branch_data.empty:
            print(f"⚪ {branch} | No Data")
            continue
        out_path = os.path.join(BRANCH_FOLDER, f"{branch}_Branch_Data.csv")
        branch_data.to_csv(out_path, index=False)
        print(f"✅ {branch} | {len(branch_data)} rows")
        created += 1

    print(f"\n  Total branch files created: {created}")


# ==========================================================
# STEP 2 — SEND BRANCH EMAILS
# ==========================================================

def step_send_branch_emails(pct):
    print("\n" + "=" * 60)
    print("STEP 2 : SENDING BRANCH EMAILS")
    print("=" * 60)

    if not os.path.exists(MAIL_LIST_FILE):
        print(f"  [ERROR] Mail list not found: {MAIL_LIST_FILE}")
        return

    mail_df = load_mail_list()
    body = build_email_body(pct)

    for _, row in mail_df.iterrows():
        to_list, invalid_to = clean_email_addresses(row.get("TO", ""))
        cc_list, invalid_cc = clean_email_addresses(row.get("CC", ""))
        branch_name = str(row.get("BRANCH", "")).strip()

        if not branch_name or branch_name.lower() == "nan":
            print("  [SKIP] Empty branch name in row.")
            log_email(BRANCH_EMAIL_LOG, datetime.now(), "No Receiver", "N/A",
                      "Branch", "Failed - No branch name provided", "N/A")
            continue

        if not to_list and not invalid_to:
            print(f"  [SKIP] No recipient for branch: {branch_name}")
            log_email(BRANCH_EMAIL_LOG, datetime.now(), "No Receiver", branch_name,
                      "Branch", "Failed - No recipient email found", "N/A")
            continue

        branch_file = None
        for f in os.listdir(BRANCH_FOLDER):
            name_no_ext = os.path.splitext(f)[0]
            if name_no_ext.startswith(branch_name + "_") or name_no_ext == branch_name:
                branch_file = os.path.join(BRANCH_FOLDER, f)
                break

        if not branch_file:
            print(f"  [SKIP] No file found for branch: {branch_name}")
            log_email(BRANCH_EMAIL_LOG, datetime.now(), ", ".join(to_list), branch_name,
                      "Branch", f"Failed - No file found for branch {branch_name}", "N/A")
            continue

        if invalid_to or invalid_cc:
            bad = ", ".join(invalid_to + invalid_cc)
            print(f"  [SKIP] Invalid emails for branch {branch_name}: {bad}")
            log_email(BRANCH_EMAIL_LOG, datetime.now(), bad, branch_name,
                      "Branch", "Failed - Invalid email format but data exists", "N/A")
            continue

        try:
            send_email_with_attachments(to_list, cc_list, EMAIL_SUBJECT, body, [branch_file])
            print(f"📧✅ Branch Mail Sent : {branch_name}")
            log_email(BRANCH_EMAIL_LOG, datetime.now(), ", ".join(to_list), branch_name,
                      "Branch", "Success", "Connected")
        except smtplib.SMTPException as e:
            print(f"  [FAILED]  Branch: {branch_name} - {e}")
            log_email(BRANCH_EMAIL_LOG, datetime.now(), ", ".join(to_list), branch_name,
                      "Branch", "Failed - SMTP Error", "FAILED")


# ==========================================================
# STEP 3 — SPLIT BY REGION
# ==========================================================

def step_split_by_region(df):
    print("\n" + "=" * 60)
    print("STEP 3 : SPLITTING BY REGION")
    print("=" * 60)

    os.makedirs(REGION_FOLDER, exist_ok=True)

    if "Region" not in df.columns:
        print("  [ERROR] 'Region' column not found. Skipping region split.")
        return

    for output_prefix, region_codes in REGION_GROUPS.items():
        filtered = df[df["Region"].isin(region_codes)]
        if filtered.empty:
            print(f"  [SKIP]    No data for regions: {region_codes}")
            continue
        out_path = os.path.join(REGION_FOLDER, f"{output_prefix}_Region_Data.csv")
        filtered.to_csv(out_path, index=False)
        print(f"✅ {output_prefix} | {len(filtered)} rows")


# ==========================================================
# STEP 4 — SEND REGION EMAILS
# ==========================================================

def step_send_region_emails(pct):
    print("\n" + "=" * 60)
    print("STEP 4 : SENDING REGION EMAILS")
    print("=" * 60)

    if not os.path.exists(MAIL_LIST_FILE):
        print(f"  [ERROR] Mail list not found: {MAIL_LIST_FILE}")
        return

    mail_df = load_mail_list()
    body = build_email_body(pct)

    for _, row in mail_df.iterrows():
        to_list, invalid_to = clean_email_addresses(row.get("TO", ""))
        cc_list, invalid_cc = clean_email_addresses(row.get("CC", ""))
        region_name = str(row.get("REGIONS", "")).strip()

        if not region_name or region_name.lower() == "nan":
            print("  [SKIP] Empty region name in row.")
            log_email(REGION_EMAIL_LOG, datetime.now(), "No Receiver", "N/A",
                      "Region", "Failed - No region name provided", "N/A")
            continue

        if not to_list and not invalid_to:
            print(f"  [SKIP] No recipient for region: {region_name}")
            log_email(REGION_EMAIL_LOG, datetime.now(), "No Receiver", region_name,
                      "Region", "Failed - No recipient email found", "N/A")
            continue

        region_file = None
        for f in os.listdir(REGION_FOLDER):
            name_no_ext = os.path.splitext(f)[0]
            if (name_no_ext.upper().startswith(region_name.upper() + "_")
                    or name_no_ext.upper() == region_name.upper()):
                region_file = os.path.join(REGION_FOLDER, f)
                break

        if not region_file:
            print(f"  [SKIP] No file for region: {region_name}")
            log_email(REGION_EMAIL_LOG, datetime.now(), ", ".join(to_list), region_name,
                      "Region", f"Failed - No file found for region {region_name}", "N/A")
            continue

        if invalid_to or invalid_cc:
            bad = ", ".join(invalid_to + invalid_cc)
            print(f"  [SKIP] Invalid emails for region {region_name}: {bad}")
            log_email(REGION_EMAIL_LOG, datetime.now(), bad, region_name,
                      "Region", "Failed - Invalid email format but data exists", "N/A")
            continue

        try:
            send_email_with_attachments(to_list, cc_list, EMAIL_SUBJECT, body, [region_file])
            print(f"📧✅ Region Mail Sent : {region_name}")
            log_email(REGION_EMAIL_LOG, datetime.now(), ", ".join(to_list), region_name,
                      "Region", "Success", "Connected")
        except smtplib.SMTPException as e:
            print(f"  [FAILED]  Region: {region_name} - {e}")
            log_email(REGION_EMAIL_LOG, datetime.now(), ", ".join(to_list), region_name,
                      "Region", "Failed - SMTP Error", "FAILED")


# ==========================================================
# STEP 5 — MISMATCH CHECK (ALL DATA)   (faithful to 20MISSMATACH.py)
# ==========================================================

def _compute_mismatch(extracted):
    ref_df = pd.read_excel(BRANCH_REGION_REF_FILE, engine="openpyxl")[["Branch1", "Region ID1"]]
    ref_df = ref_df.rename(columns={"Branch1": "Branch", "Region ID1": "Region"})
    for col in ("Branch", "Region"):
        ref_df[col] = ref_df[col].astype(str).str.strip().str.upper()
    ref_df = ref_df.drop_duplicates()

    unmatched = extracted.merge(ref_df, on=["Branch", "Region"], how="left", indicator=True)
    unmatched = unmatched[unmatched["_merge"] == "left_only"]
    unmatched = unmatched[~unmatched["Branch"].isin(ref_df["Branch"])]
    unmatched = unmatched[~unmatched["Region"].isin(ref_df["Region"])]
    return unmatched.drop(columns=["_merge"])


def step_mismatch_check(df):
    print("\n" + "=" * 60)
    print("STEP 5 : MISMATCH CHECK (ALL ZONES)")
    print("=" * 60)

    if not os.path.exists(BRANCH_REGION_REF_FILE):
        print(f"  [ERROR] Reference file not found: {BRANCH_REGION_REF_FILE}")
        return

    os.makedirs(ZONE_FOLDER, exist_ok=True)

    extracted = df[["Branch", "Region"]].copy()
    for col in ("Branch", "Region"):
        extracted[col] = extracted[col].astype(str).str.strip().str.upper()
    extracted = extracted.drop_duplicates()

    unmatched = _compute_mismatch(extracted)
    out_path = os.path.join(ZONE_FOLDER, "Mismatched_Branch_And_Region_IDs.xlsx")
    unmatched.to_excel(out_path, index=False, engine="openpyxl")
    print(f"  [SAVED]   {out_path}  ({len(unmatched)} mismatches)")


# ==========================================================
# STEP 6 — FILTER DELHI ZONE + DELHI MISMATCH CHECK
# ==========================================================

def step_filter_delhi_zone(df):
    print("\n" + "=" * 60)
    print("STEP 6 : DELHI ZONE FILTER + MISMATCH CHECK")
    print("=" * 60)

    os.makedirs(ZONE_FOLDER, exist_ok=True)

    delhi_codes = ["ZO", "ZOCDR", "ZODELH", "ZOMT"]
    delhi_df = df[df["Zone"].isin(delhi_codes)]

    delhi_out = os.path.join(ZONE_FOLDER, "DELHI_Zone_ID.csv")
    delhi_df.to_csv(delhi_out, index=False)
    print(f"  [SAVED]   {delhi_out}  ({len(delhi_df)} rows)")

    if not os.path.exists(BRANCH_REGION_REF_FILE):
        print("  [SKIP] Reference file not found, skipping Delhi mismatch check.")
        return

    extracted = delhi_df[["Branch", "Region"]].copy()
    for col in ("Branch", "Region"):
        extracted[col] = extracted[col].astype(str).str.strip().str.upper()
    extracted = extracted.drop_duplicates()

    unmatched = _compute_mismatch(extracted)
    delhi_mismatch_out = os.path.join(ZONE_FOLDER, "DELHI_Mismatched_Branch_And_Region_IDs.xlsx")
    unmatched.to_excel(delhi_mismatch_out, index=False, engine="openpyxl")
    print(f"  [SAVED]   {delhi_mismatch_out}  ({len(unmatched)} mismatches)")


# ==========================================================
# EXCEL PASSWORD PROTECTION  (Windows + Excel only, via win32com)
# ==========================================================

def protect_excel_with_password(input_xlsx, password):
    """Re-save the xlsx with an open-password using Excel COM.
    Requires Windows + Excel + pywin32. Raises on failure."""
    excel = win32.DispatchEx("Excel.Application")
    excel.DisplayAlerts = False
    excel.Visible = False
    wb = None
    try:
        wb = excel.Workbooks.Open(os.path.abspath(input_xlsx))
        wb.SaveAs(os.path.abspath(input_xlsx), FileFormat=51, Password=password)
    finally:
        if wb:
            wb.Close(False)
        excel.Quit()


# ==========================================================
# STEP 7 — SPLIT BY ZONE
# ==========================================================

def slice_pivot_for_zone(pivot_df, zone_data):
    """Return the PIVOT rows belonging to one zone.

    Selection is driven by the ClientIDs that actually appear in that
    zone's Data sheet, NOT by the pivot's own Zone column. The pivot's
    Zone is 'first occurrence per client', so a client whose first row
    sat in another zone would otherwise go missing from this file even
    though their positions are listed in it. Matching on ClientID keeps
    every zone workbook internally consistent - every client in Data has
    a PIVOT row.
    """

    if pivot_df is None or pivot_df.empty:
        return None

    if "ClientID" not in pivot_df.columns:
        print("  [WARN]    'ClientID' not in PIVOT sheet - PIVOT skipped.")
        return None

    if "ClientID" not in zone_data.columns:
        print("  [WARN]    'ClientID' not in Data - PIVOT skipped.")
        return None

    zone_clients = set(
        zone_data["ClientID"].astype(str).str.strip()
    )

    mask = (
        pivot_df["ClientID"].astype(str).str.strip().isin(zone_clients)
    )

    return pivot_df[mask].copy()


def step_split_by_zone(df, pivot_df=None):
    print("\n" + "=" * 60)
    print("STEP 7 : SPLITTING BY ZONE")
    print("=" * 60)

    os.makedirs(ZONE_FOLDER, exist_ok=True)

    for filename, zone_codes in ZONE_FILTER_MAP.items():
        filtered = df[df["Zone"].isin(zone_codes)]
        if filtered.empty:
            print(f"  [SKIP]    No data for zones: {zone_codes}")
            continue

        csv_path = os.path.join(ZONE_FOLDER, filename)
        filtered.to_csv(csv_path, index=False)
        print(f"🌍 Zone File Created : {filename} | {len(filtered)} rows")

        # The CSV can only hold one sheet, so the zone PIVOT lives in the
        # xlsx. The email step prefers the xlsx whenever it exists.
        zone_pivot = slice_pivot_for_zone(filtered, pivot_df) \
            if pivot_df is not None else None

        xlsx_path = csv_path.replace(".csv", ".xlsx")

        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:

            filtered.to_excel(writer, index=False, sheet_name="Data")

            if zone_pivot is not None and not zone_pivot.empty:
                zone_pivot.to_excel(
                    writer, index=False, sheet_name="PIVOT"
                )
                print(
                    f"   📊 PIVOT sheet added : "
                    f"{len(zone_pivot)} clients"
                )
            else:
                print("   ⚪ No PIVOT rows for this zone")

            for sheet_name in ("Data", "PIVOT"):
                if sheet_name not in writer.sheets:
                    continue
                ws = writer.sheets[sheet_name]
                ws.freeze_panes = "A2"
                for cell in ws[1]:
                    cell.font = Font(bold=True)
                autofit_full_file_columns(ws)

        # Password-protect if Excel/pywin32 is available. If it is not,
        # the xlsx is KEPT (unprotected) rather than deleted - dropping
        # back to the CSV would silently lose the PIVOT sheet, and the
        # CSV is unprotected anyway, so there is no privacy gain in it.
        if not HAVE_WIN32:
            print(
                "  [WARN]    pywin32/Excel not available - zone xlsx "
                "sent UNLOCKED (PIVOT preserved)."
            )
            continue

        try:
            protect_excel_with_password(xlsx_path, ZONE_FILE_PASSWORD)
            print(f"🔒 Password Protected : {os.path.basename(xlsx_path)}")
        except Exception as e:
            print(f"  [WARN]    Could not password-protect {xlsx_path}: {e}")
            print(
                "            Sending the UNLOCKED xlsx so the PIVOT "
                "sheet is not lost."
            )


# ==========================================================
# STEP 8 — SEND ZONE EMAILS
# ==========================================================

def step_send_zone_emails(pct):
    print("\n" + "=" * 60)
    print("STEP 8 : SENDING ZONE EMAILS")
    print("=" * 60)

    body = build_email_body(pct)

    for zone_key, config in ZONE_EMAILS.items():
        to_list   = config["to"]
        cc_list   = config["cc"]
        zone_file = config["file"]

        # Prefer the locked xlsx if it exists, else the CSV
        xlsx_zone_file = zone_file.replace(".csv", ".xlsx")
        attach_file = xlsx_zone_file if os.path.exists(xlsx_zone_file) else zone_file

        all_files = [attach_file]
        if "extra_files" in config:
            all_files += config.get("extra_files", [])

        missing = [f for f in all_files if not os.path.exists(f)]
        if missing:
            print(f"  [SKIP] File(s) missing for zone {zone_key}: {missing}")
            continue

        try:
            send_email_with_attachments(to_list, cc_list, EMAIL_SUBJECT, body, all_files)
            print(f"📧✅ Zone Mail Sent : {zone_key}")
        except smtplib.SMTPException as e:
            print(f"  [FAILED] Zone: {zone_key} - {e}")


# ==========================================================
# STEP 9 — FILTER SMS FLAG = YES + UNIQUE CLIENT IDS
# ==========================================================

def step_filter_sms_clients(df):
    print("\n" + "=" * 60)
    print("STEP 9 : FILTERING SMS CLIENTS")
    print("=" * 60)

    os.makedirs(ZONE_FOLDER, exist_ok=True)

    if "SMS FLAG" not in df.columns:
        print("  [ERROR] 'SMS FLAG' column not found.")
        return

    sms_yes = df[df["SMS FLAG"] == "YES"]
    sms_yes_path = os.path.join(ZONE_FOLDER, "Filtered_SMS_FLAG_Yes.csv")
    sms_yes.to_csv(sms_yes_path, index=False)
    print(f"  [SAVED]   {sms_yes_path}  ({len(sms_yes)} rows where SMS FLAG = YES)")

    if "ClientID" not in sms_yes.columns:
        print("  [ERROR] 'ClientID' column not found.")
        return

    unique_clients = sms_yes["ClientID"].drop_duplicates()
    unique_clients.to_csv(UNIQUE_CLIENT_FILE, index=False, header=["ClientID"])
    print(f"  [SAVED]   {UNIQUE_CLIENT_FILE}  ({len(unique_clients)} unique clients)")


# ==========================================================
# STEP 10 — SEND WHATSAPP MESSAGES
# ==========================================================

def get_oauth_token():
    try:
        resp = requests.post(OAUTH_TOKEN_URL,
                             headers={"Content-Type": "application/json"},
                             json=OAUTH_PAYLOAD, timeout=30)
        resp.raise_for_status()
        token = resp.json()["body"]["data"][0]["ACCESS_TOKEN"]
        print("🔑 OAuth Token Generated")
        return token
    except Exception as e:
        print(f"  [ERROR] OAuth token failed: {e}")
        return None


def get_client_mobile(client_code, token):

    

    MAX_RETRIES = 10
    attempt = 1

    while attempt <= MAX_RETRIES:

        try:

            url = CLIENT_MOBILE_URL.format(
                client_code=client_code
            )

            resp = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}"
                },
                timeout=30
            )

            # ----------------------------
            # AUTH ERROR
            # ----------------------------

            if resp.status_code == 401:

                print(
                    f"🔑 Token Expired | Client {client_code}"
                )

                token = get_oauth_token()

                if not token:
                    time.sleep(10)
                    continue

                continue

            # ----------------------------
            # SERVER ERRORS
            # ----------------------------

            if resp.status_code in [400,408,429,500,502,503,504]:

                print(
                    f"⚠ API Error {resp.status_code} "
                    f"| Client {client_code}"
                )

                time.sleep(10)
                attempt += 1
                continue

            resp.raise_for_status()

            data = resp.json()

            records = (
                data.get("body", {})
                .get("data", {})
                .get("data", [])
            )

            # NUMBER NOT FOUND
            if not records:

                print(
                    f"❌ Mobile Not Found "
                    f"| {client_code}"
                )

                return None

            mobile = records[0].get("MOBILE")

            if not mobile:

                print(
                    f"❌ Mobile Empty "
                    f"| {client_code}"
                )

                return None

            return mobile

        except requests.exceptions.Timeout:

            print(
                f"⏳ Timeout "
                f"| Client {client_code}"
            )

            time.sleep(10)

        except requests.exceptions.ConnectionError:

            print(
                f"🌐 Connection Error "
                f"| Client {client_code}"
            )

            time.sleep(10)

        except Exception as e:

            print(
                f"⚠ Lookup Error "
                f"| {client_code} | {e}"
            )

            time.sleep(10)

        attempt += 1
    print(
        f"❌ Max Retries Exceeded | {client_code}"
    )

    return None

def send_whatsapp(client_code, mobile, message):

    number = str(mobile).strip()

    if not number.startswith("91"):
        number = "91" + number

    number = "+" + number
    if C.test_mode():                      # SAFE TEST: every client message goes to the tester
        number = "+" + "".join(ch for ch in C.test_wa_to() if ch.isdigit())

    payload = {
        "source": "internal",
        "template": {
            "name": "physical_delivery",
            "language": {
                "code": "en"
            },
            "components": [{
                "type": "body",
                "parameters": [{
                    "type": "text",
                    "text": message
                }]
            }]
        },
        "to": number,
        "type": "template"
    }

    MAX_RETRIES = 30
    attempt = 1

    while attempt <= MAX_RETRIES:
        try:

            resp = requests.post(
                WHATSAPP_URL,
                headers=WHATSAPP_HEADERS,
                json=payload,
                timeout=30
            )

            # ----------------------------
            # SUCCESS
            # ----------------------------

            if resp.status_code == 200:

              try:

                  log_sms_status(
                      client_code,
                      mobile,
                      "Sent"
                  )
            
                  print(
                       f"📱 {client_code} | 📞 {mobile} | ✅ WhatsApp Sent"
                  )

              except Exception as e:

                  print(
                      f"Log Error : {e}"
                  )

              return True

            # ----------------------------
            # TOKEN ISSUE
            # ----------------------------

            if resp.status_code == 401:

                print(
                    f"🔑 WhatsApp Token Invalid"
                )

                log_sms_status(
                    client_code,
                    mobile,
                    "Failed - Invalid WhatsApp Token"
                )

                return False

            # ----------------------------
            # TEMPORARY ERRORS
            # ----------------------------

            if resp.status_code in [400,408,429,500,502,503,504]:

                print(
                    f"⚠ WhatsApp API Error "
                    f"{resp.status_code}"
                )

                time.sleep(10)
                attempt += 1
                continue

            # ----------------------------
            # PERMANENT FAILURE
            # ----------------------------

            print(
                f"❌ Permanent Failure "
                f"{resp.status_code}"
            )

            log_sms_status(
                client_code,
                mobile,
                f"Failed ({resp.status_code})"
            )

            return False

        except requests.exceptions.Timeout:

            print(
                f"⏳ Timeout "
                f"| {client_code}"
            )

            time.sleep(10)

        except requests.exceptions.ConnectionError:

            print(
                f"🌐 Connection Error "
                f"| {client_code}"
            )

            time.sleep(10)

        except Exception as e:

            print(
                f"⚠ WhatsApp Error "
                f"| {client_code} | {e}"
            )

            time.sleep(10)

        attempt += 1
    print(
        f"❌ Max Retries Exceeded | {client_code}"
    )

    log_sms_status(
        client_code,
        mobile,
        "Failed - Max Retries Exceeded"
    )

    return False

def process_client(client_code, token, whatsapp_message):

    with sent_lock:

        if client_code in sent_clients:

            print(
                f"⚠ Duplicate Client Skipped | {client_code}"
            )

            return

        sent_clients.add(client_code)

    print(f"📱 Processing Client : {client_code}")

    mobile = get_client_mobile(
        client_code,
        token
    )

    if not mobile:

        print(
           f"📱 {client_code} | ❌ No Mobile Found"
      )

        log_sms_status(
            client_code,
            "N/A",
            "Failed - No client number"
        )

        return

    send_whatsapp(
        client_code,
        mobile,
        whatsapp_message
    )

def step_send_whatsapp_messages(pct):

    print("\n" + "=" * 60)
    print("STEP 10 : SENDING WHATSAPP MESSAGES TO CLIENTS")
    print("=" * 60)

    if not os.path.exists(UNIQUE_CLIENT_FILE):

        print(
            f"  [ERROR] Unique client file not found: {UNIQUE_CLIENT_FILE}"
        )

        return

    client_df = pd.read_csv(
        UNIQUE_CLIENT_FILE
    )

    if C.test_mode():                      # SAFE TEST: only the first few clients, all to the tester
        client_df = client_df.head(C.test_max_clients())
        print(f"  [SAFE TEST] only {len(client_df)} client(s) are sent, all to the test number.")

    if "ClientID" not in client_df.columns:

        print(
            "  [ERROR] 'ClientID' column not found."
        )

        return

    token = get_oauth_token()

    if not token:

        print(
            "  [ABORT] Cannot proceed without OAuth token."
        )

        return

    whatsapp_message = f"{pct}%"

    print(
        f"  [INFO] WhatsApp message parameter: {whatsapp_message}"
    )

    MAX_WORKERS = 50


    futures = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        for _, row in client_df.iterrows():

            client_code = str(
                row["ClientID"]
            ).strip()

            futures.append(

                executor.submit(
                    process_client,
                    client_code,
                    token,
                    whatsapp_message
                )

            )

        for future in as_completed(futures):

            try:
                future.result()

            except Exception as e:

                print(
                    f"❌ Thread Error : {e}"
                )

    print(
        "\n✅ All WhatsApp jobs completed"
    )

from openpyxl import load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter


def autofit_full_file_columns(ws):
    """Auto-size every column in a FULL_FILE.xlsx sheet to fit its
    widest cell (header included), same approach used for the other
    generated workbooks."""

    for column in ws.columns:

        max_length = 0
        column_letter = get_column_letter(column[0].column)

        for cell in column:
            try:
                length = len(str(cell.value))
                if length > max_length:
                    max_length = length
            except Exception:
                pass

        ws.column_dimensions[column_letter].width = max_length + 4

def create_sms_log_excel():

    if not os.path.exists(SMS_LOG_FILE):
        return None

    excel_file = SMS_LOG_FILE.replace(".csv", ".xlsx")

    df = pd.read_csv(SMS_LOG_FILE)

    with pd.ExcelWriter(
        excel_file,
        engine="openpyxl"
    ) as writer:

        df.to_excel(
            writer,
            index=False
        )

    wb = load_workbook(excel_file)
    ws = wb.active

    ws.freeze_panes = "A2"

    for cell in ws[1]:
        cell.font = Font(bold=True)

    for column in ws.columns:

        max_length = 0

        column_letter = column[0].column_letter

        for cell in column:

            try:
                max_length = max(
                    max_length,
                    len(str(cell.value))
                )
            except:
                pass

        ws.column_dimensions[
            column_letter
        ].width = max_length + 2

    wb.save(excel_file)

    return excel_file


# ==========================================================
# STEP 10B — ALERT 1 CR DASHBOARD IMAGES -> WHATSAPP (5 lists)
# ==========================================================
# Runs right after the client WhatsApp step and BEFORE log-email/backup,
# so FULL_FILE / PIVOT data are still in memory and nothing is moved yet.
# Only on the expiry day (last Tuesday of the month, or the Monday if
# Tuesday was a holiday). One image per zone list + one FULL image, each
# sent to its own numbers. A zone with no ALERT 1 CR client gets no image
# and no WhatsApp.

# ==========================================================
# DATE LOGIC  (pipeline mode)
# ==========================================================

def alert_last_tuesday(year, month):
    d = date(year, month, calendar.monthrange(year, month)[1])
    while d.weekday() != 1:
        d -= timedelta(days=1)
    return d


def alert_get_expiry_date(df):
    """The expiry date present in the Data sheet (already filtered upstream)."""
    if df is None or "ExpDate" not in df.columns or df.empty:
        return None
    s = df["ExpDate"].astype(str).str.strip()
    parsed = pd.to_datetime(s, format="%d-%b-%Y", errors="coerce")
    miss = parsed.isna()
    if miss.any():
        parsed[miss] = pd.to_datetime(s[miss], format="%d-%b-%y", errors="coerce")
    parsed = parsed.dropna()
    if parsed.empty:
        return None
    return parsed.dt.date.mode().iloc[0]


def alert_should_generate(today, expiry, force=False):
    if force:
        return True, "FORCE / TEST mode - date check skipped"
    if expiry is None:
        return False, "no expiry date found in Data sheet"
    lt = alert_last_tuesday(expiry.year, expiry.month)
    if not (lt - timedelta(days=3) <= expiry <= lt):
        return False, f"expiry {expiry} is not the month-end (last Tuesday = {lt})"
    if today != expiry:
        return False, f"today {today} is not the expiry day {expiry}"
    return True, f"today is the expiry day ({expiry}; last Tuesday = {lt})"


# ==========================================================
# DATA PICK
# ==========================================================

def _alert_find_col(columns, startswith):
    for c in columns:
        if " ".join(str(c).upper().split()).startswith(startswith):
            return c
    return None


def alert_pick_rows(pivot_df):
    """Rows whose REMARKS contain 'ALERT 1 CR', sorted by delivery value
    HIGHEST -> LOWEST. Returns (df with 6 standard columns, total_count)."""
    remarks = pivot_df["REMARKS"].fillna("").astype(str).str.upper()
    sub = pivot_df[remarks.str.contains(ALERT_REMARK_KEY, regex=False)].copy()

    prefixes = {
        "Zone": "ZONE",
        "ClientID": "CLIENTID",
        "DELIVERY": "SUM OF DELIVERY VALUE",
        "LEDGER": "NET LEDGER",
        "CTCL": "DEPOSIT CTCL",      # "(File 04)" suffix changes -> prefix match
        "NMASS": "DEPOSIT NMASS",    # "( AFTER ADJUSTING NET LEDGER)"
    }
    src = {}
    for key, prefix in prefixes.items():
        c = _alert_find_col(pivot_df.columns, prefix)
        if c is None:
            raise KeyError(f"PIVOT column starting with '{prefix}' not found")
        src[key] = c

    out = pd.DataFrame({
        "Zone": sub[src["Zone"]].astype(str),
        "ClientID": sub[src["ClientID"]].astype(str),
        "DELIVERY": pd.to_numeric(sub[src["DELIVERY"]], errors="coerce").fillna(0),
        "LEDGER": pd.to_numeric(sub[src["LEDGER"]], errors="coerce").fillna(0),
        "CTCL": pd.to_numeric(sub[src["CTCL"]], errors="coerce").fillna(0),
        "NMASS": pd.to_numeric(sub[src["NMASS"]], errors="coerce").fillna(0),
    })
    out = out.sort_values("DELIVERY", ascending=False).reset_index(drop=True)
    return out


# ==========================================================
# FORMAT HELPERS
# ==========================================================

def alert_indian(n):
    """Whole number, comma separated (lakh/crore style): 12345678 -> 1,23,45,678"""
    try:
        n = int(round(float(n)))
    except Exception:
        return str(n)
    neg = n < 0
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + s


def alert_load_fonts():
    def try_fonts(names, size):
        for n in names:
            try:
                return ImageFont.truetype(n, size)
            except Exception:
                continue
        return ImageFont.load_default()

    reg = ["C:/Windows/Fonts/segoeui.ttf", "segoeui.ttf", "DejaVuSans.ttf", "arial.ttf"]
    bold = ["C:/Windows/Fonts/segoeuib.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"]
    return {
        "title": try_fonts(bold, 40),
        "sub": try_fonts(reg, 22),
        "kpi_lbl": try_fonts(bold, 18),
        "kpi_val": try_fonts(bold, 36),
        "th": try_fonts(bold, 21),
        "td": try_fonts(reg, 24),
        "tdb": try_fonts(bold, 24),
        "chip_lbl": try_fonts(bold, 18),
        "chip_val": try_fonts(reg, 20),
        "foot": try_fonts(reg, 18),
    }


def alert_fit_bold(text, max_w, start=36, minimum=20):
    """Largest bold font (<= start) whose rendering of text fits max_w."""
    names = ["C:/Windows/Fonts/segoeuib.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"]
    for size in range(start, minimum - 1, -2):
        for n in names:
            try:
                f = ImageFont.truetype(n, size)
            except Exception:
                continue
            if f.getlength(text) <= max_w:
                return f
            break
    return f if 'f' in locals() else ImageFont.load_default()


def alert_wrap_text(text, font, max_w):
    """Word-wrap; a single word wider than the cell is broken by character."""
    text = str(text)
    lines, cur = [], ""
    for w in text.split(" "):
        trial = (cur + " " + w).strip()
        if font.getlength(trial) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        if font.getlength(w) <= max_w:
            cur = w
        else:
            chunk = ""
            for ch in w:
                if font.getlength(chunk + ch) <= max_w:
                    chunk += ch
                else:
                    lines.append(chunk)
                    chunk = ch
            cur = chunk
    if cur:
        lines.append(cur)
    return lines or [""]


# ==========================================================
# DASHBOARD IMAGE
# ==========================================================

def alert_build_dashboard(data, total_count, expiry, out_path, heading="PHYSICAL DELIVERY DASHBOARD", group_chips=False):
    F = alert_load_fonts()
    W = 1500
    M = 40                       # outer margin
    CW = W - 2 * M               # content width
    PAD_X, PAD_Y = 14, 12

    shown = data.head(ALERT_MAX_ROWS)

    # ---------- summary numbers (over ALL alert rows, not just shown) ----------
    tot_del = data["DELIVERY"].sum()
    tot_led = data["LEDGER"].sum()
    tot_ctcl = data["CTCL"].sum()
    neg_nmass = int((data["NMASS"] < 0).sum())

    # ---------- table columns ----------
    headers = ["Zone", "Client ID", "Sum of Delivery Value", "Net Ledger",
               "Deposit CTCL", "Deposit NMASS (after adjusting Net Ledger)"]
    fr = [0.12, 0.14, 0.20, 0.17, 0.17, 0.20]
    col_w = [int(CW * f) for f in fr]
    col_w[-1] = CW - sum(col_w[:-1])
    col_x = [M]
    for w in col_w[:-1]:
        col_x.append(col_x[-1] + w)

    d0 = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lh_th = int(F["th"].size * 1.3)
    lh_td = int(F["td"].size * 1.3)

    # header row height (wrapped)
    hdr_lines = [alert_wrap_text(h, F["th"], col_w[i] - 2 * PAD_X) for i, h in enumerate(headers)]
    hdr_h = max(len(l) for l in hdr_lines) * lh_th + 2 * PAD_Y

    # body rows (wrapped)
    rows = []
    for _, r in shown.iterrows():
        cells = [r["Zone"], r["ClientID"], alert_indian(r["DELIVERY"]), alert_indian(r["LEDGER"]),
                 alert_indian(r["CTCL"]), alert_indian(r["NMASS"])]
        num_vals = [None, None, r["DELIVERY"], r["LEDGER"], r["CTCL"], r["NMASS"]]
        wrapped = [alert_wrap_text(c, F["td"], col_w[i] - 2 * PAD_X) for i, c in enumerate(cells)]
        h = max(len(w) for w in wrapped) * lh_td + 2 * PAD_Y
        rows.append((wrapped, num_vals, h))

    # zone summary chips
    chip_key = data["Zone"].map(ALERT_ZONE_TO_GROUP).fillna(data["Zone"]) if group_chips else data["Zone"]
    zg = (data.groupby(chip_key)["DELIVERY"].agg(["count", "sum"])
          .sort_values("sum", ascending=False))
    chip_w, chip_h, chip_gap = 300, 92, 18
    per_row = max(1, (CW + chip_gap) // (chip_w + chip_gap))
    chip_rows = (len(zg) + per_row - 1) // per_row

    # ---------- vertical layout ----------
    header_band_h = 120
    kpi_h = 128
    y_kpi = header_band_h + 28
    y_zone_title = y_kpi + kpi_h + 28
    y_chips = y_zone_title + 34
    y_table_title = y_chips + chip_rows * (chip_h + chip_gap) + 8
    y_table = y_table_title + 40
    body_h = sum(r[2] for r in rows)
    foot_h = 60 if total_count > len(shown) else 20
    H = y_table + hdr_h + body_h + 24 + foot_h

    img = Image.new("RGB", (W, H), (250, 251, 253))
    d = ImageDraw.Draw(img)

    # ---------- header band ----------
    d.rectangle([0, 0, W, header_band_h], fill=ALERT_NAVY)
    d.text((M, 22), f"ALERT 1 CR  -  {heading}", font=F["title"], fill=ALERT_WHITE)
    sub = []
    if expiry:
        sub.append(f"Expiry: {expiry:%d-%b-%Y}")
    sub.append(f"Generated: {datetime.now():%d-%m-%Y %H:%M}")
    d.text((M, 74), "   |   ".join(sub), font=F["sub"], fill=(190, 205, 225))

    # ---------- KPI cards ----------
    kpis = [
        ("CLIENTS ON ALERT", str(total_count), ALERT_NAVY),
        ("TOTAL DELIVERY VALUE", alert_indian(tot_del), ALERT_TEAL),
        ("TOTAL NET LEDGER", alert_indian(tot_led), ALERT_RED if tot_led < 0 else ALERT_GREEN),
        ("TOTAL DEPOSIT CTCL", alert_indian(tot_ctcl), ALERT_NAVY),
    ]
    gap = 20
    kw = (CW - gap * (len(kpis) - 1)) // len(kpis)
    for i, (lbl, val, col) in enumerate(kpis):
        x = M + i * (kw + gap)
        d.rounded_rectangle([x, y_kpi, x + kw, y_kpi + kpi_h], radius=14,
                            fill=ALERT_WHITE, outline=ALERT_BORDER, width=2)
        d.rounded_rectangle([x, y_kpi, x + 10, y_kpi + kpi_h], radius=5, fill=col)
        d.text((x + 28, y_kpi + 18), lbl, font=F["kpi_lbl"], fill=ALERT_GREY)
        fnt = alert_fit_bold(val, kw - 28 - 24)
        d.text((x + 28, y_kpi + 58), val, font=fnt, fill=col)

    # ---------- zone chips ----------
    d.text((M, y_zone_title), "ZONE-WISE SUMMARY" if len(zg) > 1 or group_chips else "ZONE", font=F["kpi_lbl"], fill=ALERT_NAVY)
    for i, (zone, r) in enumerate(zg.iterrows()):
        cx = M + (i % per_row) * (chip_w + chip_gap)
        cy = y_chips + (i // per_row) * (chip_h + chip_gap)
        d.rounded_rectangle([cx, cy, cx + chip_w, cy + chip_h], radius=12,
                            fill=ALERT_LIGHT, outline=ALERT_BORDER, width=2)
        d.text((cx + 18, cy + 10), f"{zone}  -  {int(r['count'])} client(s)",
               font=F["chip_lbl"], fill=ALERT_NAVY)
        d.text((cx + 18, cy + 44), alert_indian(r["sum"]), font=F["chip_val"], fill=ALERT_BLACK)

    # ---------- table ----------
    title = "CLIENT DETAILS  (highest to lowest delivery value)"
    d.text((M, y_table_title), title, font=F["kpi_lbl"], fill=ALERT_NAVY)

    y = y_table
    d.rectangle([M, y, M + CW, y + hdr_h], fill=ALERT_NAVY)
    for i, lines in enumerate(hdr_lines):
        ty = y + PAD_Y
        for ln in lines:
            tw = F["th"].getlength(ln)
            tx = col_x[i] + (col_w[i] - tw) / 2          # header centred
            d.text((tx, ty), ln, font=F["th"], fill=ALERT_WHITE)
            ty += lh_th
    y += hdr_h

    for ri, (wrapped, num_vals, h) in enumerate(rows):
        d.rectangle([M, y, M + CW, y + h], fill=ALERT_ZEBRA if ri % 2 else ALERT_WHITE)
        for i, lines in enumerate(wrapped):
            ty = y + PAD_Y
            colr = ALERT_BLACK
            fnt = F["td"]
            if num_vals[i] is not None and num_vals[i] < 0:
                colr = ALERT_RED
            if i == 2:
                fnt = F["tdb"]
            for ln in lines:
                tw = fnt.getlength(ln)
                if i < 2:
                    tx = col_x[i] + (col_w[i] - tw) / 2     # text centred
                else:
                    tx = col_x[i] + col_w[i] - PAD_X - tw   # numbers right
                d.text((tx, ty), ln, font=fnt, fill=colr)
                ty += lh_td
        d.line([M, y + h, M + CW, y + h], fill=ALERT_BORDER, width=1)
        y += h

    # vertical grid lines + outer border
    for x in col_x[1:]:
        d.line([x, y_table, x, y], fill=ALERT_BORDER, width=1)
    d.rectangle([M, y_table, M + CW, y], outline=ALERT_BORDER, width=2)

    # ---------- footer ----------
    fy = y + 18
    if total_count > len(shown):
        d.text((M, fy), f"Showing top {len(shown)} of {total_count} clients by delivery value. "
                        f"Totals above include all {total_count}.", font=F["foot"], fill=ALERT_RED)
        fy += 26

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "PNG", optimize=True)

    # WhatsApp images must be <= 5 MB
    if os.path.getsize(out_path) > 4_500_000:
        jpg = out_path.replace(".png", ".jpg")
        img.convert("RGB").save(jpg, "JPEG", quality=90)
        return jpg
    return out_path


# ==========================================================
# WHATSAPP
# ==========================================================

def alert_normalise_number(num):
    digits = "".join(ch for ch in str(num) if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return "+" + digits


def alert_upload_media(image_path):
    mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
    with open(image_path, "rb") as f:
        resp = requests.post(
            ALERT_MEDIA_URL,
            headers={"Authorization": f"Bearer {WHATSAPP_AUTH_TOKEN}", "accept": "application/json"},
            files={"file": (os.path.basename(image_path), f, mime)},
            timeout=60,
        )
    if resp.status_code not in (200, 201):
        print(f"  [UPLOAD FAILED] {resp.status_code} | {resp.text}")
        return None
    j = resp.json()
    return j.get("id") or j.get("mediaHandle")


def alert_send_image_to(number, media_id, label, retries=3):
    payload = {
        "to": alert_normalise_number(number),
        "type": "template",
        "source": "external",
        "template": {
            "name": ALERT_TEMPLATE_NAME,
            "language": {"code": "en"},
            "components": [
                {"type": "header",
                 "parameters": [{"type": "image", "image": {"id": media_id}}]},
                {"type": "body",
                 "parameters": [
                     {"type": "text", "text": label},
                     {"type": "text", "text": datetime.now().strftime("%d-%m-%Y")},
                 ]},
            ],
        },
    }
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                ALERT_SEND_URL,
                headers={"Authorization": f"Bearer {WHATSAPP_AUTH_TOKEN}",
                         "Content-Type": "application/json"},
                json=payload, timeout=60,
            )
            if resp.status_code == 200:
                print(f"  [SENT]   {payload['to']}")
                return True
            print(f"  [FAILED] {payload['to']} | attempt {attempt} | "
                  f"{resp.status_code} | {resp.text}")
            if resp.status_code in (401, 403):
                return False
        except Exception as e:
            print(f"  [ERROR]  {payload['to']} | attempt {attempt} | {e}")
        time.sleep(5)
    return False


def alert_send_image_whatsapp(image_path, numbers, label):
    if C.test_mode():                      # SAFE TEST: the picture goes to the tester only
        numbers = [C.test_wa_to()] if C.test_wa_to() else []
    if not numbers:
        print("  [WHATSAPP] No numbers configured for this list.")
        return
    media_id = alert_upload_media(image_path)          # upload ONCE per image, reuse for every number
    if not media_id:
        return
    ok = sum(1 for n in numbers if alert_send_image_to(n, media_id, label))
    print(f"  [WHATSAPP] {label}: delivered to {ok}/{len(numbers)} number(s)")


# ==========================================================
# PIPELINE STEP
# ==========================================================

def step_alert_1cr_image(df, pivot_df, today=None, force=None, send=True, out_folder=None):
    print("\n" + "=" * 60)
    print("STEP : ALERT 1 CR DASHBOARD IMAGE")
    print("=" * 60)

    if force is None:
        force = ALERT_IMAGE_FORCE
    out_folder = out_folder or os.path.join(BASE_FOLDER, ALERT_IMAGE_SUBFOLDER)
    today = today or datetime.now().date()
    expiry = alert_get_expiry_date(df)

    ok, why = alert_should_generate(today, expiry, force)
    print(f"  [CHECK] {why}")
    if not ok:
        print("  [SKIP]  Image not generated today.")
        return None

    if pivot_df is None or pivot_df.empty:
        print("  [SKIP]  PIVOT is empty.")
        return None

    data = alert_pick_rows(pivot_df)
    if data.empty:
        print(f"  [SKIP]  No client has REMARKS containing '{ALERT_REMARK_KEY}'.")
        return None
    print(f"  [DATA]  {len(data)} client(s) with '{ALERT_REMARK_KEY}'")

    stamp = (expiry or today).strftime("%Y%m%d")
    all_zones = set(z for cfg in ALERT_LISTS.values() if cfg["zones"] for z in cfg["zones"])
    stray = sorted(set(data["Zone"].str.strip().str.upper()) - {z.upper() for z in all_zones})
    if stray:
        print(f"  [NOTE]  Zones not in any zone list (appear only in FULL image): {stray}")

    made = []
    for name, cfg in ALERT_LISTS.items():
        zones = cfg["zones"]
        if zones is None:
            part = data
            heading = "ALL ZONES"
        else:
            wanted = {z.upper() for z in zones}
            part = data[data["Zone"].str.strip().str.upper().isin(wanted)]
            heading = f"{name} ZONE"

        if part.empty:
            print(f"  [SKIP]  {name}: no ALERT 1 CR client in this zone - nothing sent.")
            continue

        part = part.sort_values("DELIVERY", ascending=False).reset_index(drop=True)
        path = alert_build_dashboard(part, len(part), expiry, os.path.join(out_folder, f"ALERT_1CR_{name}_{stamp}.png"),
                               heading=heading, group_chips=(zones is None))
        print(f"  [SAVED] {name}: {len(part)} client(s) -> {path}")
        made.append(path)

        if send:
            alert_send_image_whatsapp(path, cfg["numbers"], f"{ALERT_REPORT_LABEL}_{name}")
    return made


# ==========================================================
# STEP 11 — EMAIL ALL LOGS TO RMS TEAM
# ==========================================================

def step_send_log_email():
    print("\n" + "=" * 60)
    print("STEP 11 : EMAILING LOGS TO RMS TEAM")
    print("=" * 60)

    LOG_TO  = C.email_list("PD_LOG_TO", ["rms_del@mastertrust.co.in"])
    SUBJECT = "Tentative Physical Delivery SMS/MAIL LOGS"
    BODY    = ("Dear Team,\n\nPFA of SMS and Mail LOGS\n\n"
               "Best regards,\nRaj Kumari\n011-66331033 / 37 / 38")
    sms_excel = create_sms_log_excel()
    log_files = [sms_excel,REGION_EMAIL_LOG,BRANCH_EMAIL_LOG]

    existing = [f for f in log_files if f and os.path.exists(f)]

    missing   = [f for f in log_files if not os.path.exists(f)]

    if missing:
        print(f"  [WARN] Some log files not found (will skip): {missing}")
    if not existing:
        print("  [ERROR] No log files found. Skipping log email.")
        return

    try:
        send_email_with_attachments(LOG_TO, C.email_list("PD_LOG_CC"), SUBJECT, BODY, existing)
        print(f"  [SENT] Log email sent to: {', '.join(LOG_TO)}")
    except smtplib.SMTPException as e:
        print(f"  [FAILED] Log email: {e}")


# ==========================================================
# STEP 12 — BACKUP  (never moves inputs or the scripts)
# ==========================================================

def step_backup():
    print("\n" + "=" * 60)
    print("STEP 12 : BACKUP")
    print("=" * 60)

    if C.test_mode():
        print("  [SAFE TEST] Backup skipped - today's files stay where they are for the real run.")
        return
    _now = datetime.now()
    timestamp     = _now.strftime("%H-%M-%S")
    backup_folder = os.path.join(BACKUP_FOLDER, _now.strftime("%d-%m-%Y"), timestamp)
    branch_backup = os.path.join(backup_folder, "BRANCH FILES")
    region_backup = os.path.join(backup_folder, "REGION FILES")
    zone_backup = os.path.join(backup_folder, "ZONE FILES")
    log_backup = os.path.join(backup_folder, "LOG FILES")
    input_backup = os.path.join(backup_folder,"INPUT FILES")

    os.makedirs(branch_backup, exist_ok=True)
    os.makedirs(region_backup, exist_ok=True)
    os.makedirs(zone_backup, exist_ok=True)
    os.makedirs(log_backup, exist_ok=True)
    os.makedirs(input_backup,exist_ok=True)

    # NOTE: MAIN_FOLDER is intentionally NOT in this list, so the mail
    # list and reference file are never moved (same as the old BACKUP.PY).
    folders_to_backup = [BASE_FOLDER, BRANCH_FOLDER, REGION_FOLDER, ZONE_FOLDER]

    # Never move these from the BASE_FOLDER root:
    keep_at_root = {
        os.path.basename(MAIL_LIST_FILE).lower(),
        os.path.basename(BRANCH_REGION_REF_FILE).lower(),
    }

    def skip(folder, fname):
        if os.path.abspath(folder) == os.path.abspath(BASE_FOLDER):
            low = fname.lower()
            if low in keep_at_root:
                return True
            if low.endswith(".py") or low.endswith(".bat"):
                return True
        return False

    total_moved = 0

    # ==========================================
    # INPUT FILES
    # ==========================================

    if os.path.exists(INPUT_FOLDER):

        files = [
            f for f in os.listdir(INPUT_FOLDER)
            if os.path.isfile(
                os.path.join(INPUT_FOLDER, f)
            )
        ]

        for fname in files:

            shutil.move(
                os.path.join(INPUT_FOLDER, fname),
                os.path.join(input_backup, fname)
            )

            total_moved += 1

        print(
            f"📂 INPUT FOLDER FILES backed up : {len(files)}"
        )

    # ==========================================
    # SCRIPT 1 OUTPUT FILES
    # ==========================================

    generated_files = [
        POSITIONBOOK_WITH_LTP_FILE,
        NIFTY500_FILE
    ]

    moved_generated = 0

    for file_path in generated_files:

        if os.path.exists(file_path):

            shutil.move(
                file_path,
                os.path.join(
                    input_backup,
                    os.path.basename(file_path)
                )
            )

            total_moved += 1
            moved_generated += 1

    print(
        f"📊 GENERATED FILES backed up : {moved_generated}"
    )
    # ==========================================
    # LOG FILES
    # ==========================================
    sms_excel_file = SMS_LOG_FILE.replace(
                      ".csv",
                      ".xlsx"
    )

    log_files = [
        SMS_LOG_FILE,
        sms_excel_file,
        BRANCH_EMAIL_LOG,
        REGION_EMAIL_LOG
    ]
    
    for file_path in log_files:

        if os.path.exists(file_path):

            shutil.move(
                file_path,
                os.path.join(
                    log_backup,
                    os.path.basename(file_path)
                )
            )

            total_moved += 1

    print("📋 LOG FILES backed up")

    # ==========================================
    # BRANCH FILES
    # ==========================================

    if os.path.exists(BRANCH_FOLDER):

        files = [
            f for f in os.listdir(BRANCH_FOLDER)
            if os.path.isfile(os.path.join(BRANCH_FOLDER, f))
        ]

        for fname in files:

            shutil.move(
                os.path.join(BRANCH_FOLDER, fname),
                os.path.join(branch_backup, fname)
            )

            total_moved += 1

        print(f"📂 BRANCH FILES backed up : {len(files)}")

    # ==========================================
    # REGION FILES
    # ==========================================

    if os.path.exists(REGION_FOLDER):

        files = [
            f for f in os.listdir(REGION_FOLDER)
            if os.path.isfile(os.path.join(REGION_FOLDER, f))
        ]

        for fname in files:

            shutil.move(
                os.path.join(REGION_FOLDER, fname),
                os.path.join(region_backup, fname)
            )

            total_moved += 1

        print(f"📂 REGION FILES backed up : {len(files)}")

    # ==========================================
    # ZONE FILES
    # ==========================================

    if os.path.exists(ZONE_FOLDER):

        files = [
            f for f in os.listdir(ZONE_FOLDER)
            if os.path.isfile(os.path.join(ZONE_FOLDER, f))
        ]

        for fname in files:

            shutil.move(
                os.path.join(ZONE_FOLDER, fname),
                os.path.join(zone_backup, fname)
            )

            total_moved += 1

        print(f"🌍 ZONE FILES backed up : {len(files)}")


    if total_moved > 0:
        print(f"🎉 Backup Completed | Files Moved : {total_moved}")
        print(f"  Backup location: {backup_folder}")
    else:
        if os.path.exists(backup_folder) and not os.listdir(backup_folder):
            os.rmdir(backup_folder)
        print("  No files found to back up.")

START_TIME = datetime.now()

# ==========================================================
# MAIN
# ==========================================================

STEP_ERRORS = []
STEP_TEXT = {
    "Split by branch": "Splitting the data branch-wise",
    "Branch emails": "E-mailing every branch",
    "Split by region": "Splitting the data region-wise",
    "Region emails": "E-mailing every region",
    "Mismatch check": "Checking branch / region mismatches",
    "Delhi zone filter": "Preparing the Delhi zone file",
    "Split by zone": "Splitting the data zone-wise",
    "Zone emails": "E-mailing the zones",
    "Filter SMS clients": "Picking the clients to message",
    "WhatsApp messages": "Sending WhatsApp messages to clients",
    "ALERT 1 CR image": "Sending the ALERT 1 CR pictures on WhatsApp",
    "Log email": "E-mailing the run log",
    "Backup": "Backing up today's files",
}


def run_step(label, func, *args):
    """Run a step; if it errors, report and continue with the rest."""
    print(f"[FRIENDLY_MSG] {STEP_TEXT.get(label, label)}", flush=True)
    try:
        func(*args)
    except Exception as e:
        STEP_ERRORS.append(f"{label}: {e}")
        print(f"\n[STEP ERROR] '{label}' failed: {e}")
        print("  Continuing with the next step...")


def main():
    print("🚀" + "=" * 70)
    print("🚀 PHYSICAL DELIVERY - NEXT STEPS PIPELINE")
    print("🚀" + "=" * 70)    
    print(f"Base folder : {BASE_FOLDER}")
    validate_required_files()

    if not os.path.exists(INPUT_FILE):
        print("\n[ABORT] Input file not found.")
        print(f"  Looked in : {BASE_FOLDER}")
        print("  Run the NIFTY500 / PositionBook script first, and make sure")
        print("  it saves PositionBook_With_LTP.xlsx into the base folder.")
        return

    print(f"\nLoading: {INPUT_FILE}")
    df = pd.read_excel(INPUT_FILE, sheet_name="Data")
    print(f"Rows loaded: {len(df)}")

    try:
        pivot_df = pd.read_excel(INPUT_FILE, sheet_name="PIVOT")
    except Exception:
        pivot_df = None

    # Read the percentage straight from the column names (e.g. "CASH 2.0% UP")
    pct = detect_percentage(df)

    # FULL_FILE.xlsx (used by the Delhi zone email) - now includes PIVOT sheet
    os.makedirs(ZONE_FOLDER, exist_ok=True)
    full_file_path = os.path.join(ZONE_FOLDER, "FULL_FILE.xlsx")

    with pd.ExcelWriter(full_file_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Data", index=False)
        if pivot_df is not None and not pivot_df.empty:
            pivot_df.to_excel(writer, sheet_name="PIVOT", index=False)

        for sheet_name in ["Data", "PIVOT"]:
            if sheet_name not in writer.sheets:
                continue
            ws = writer.sheets[sheet_name]
            for cell in ws[1]:
                cell.font = Font(bold=True)
            autofit_full_file_columns(ws)

    print(f"FULL_FILE.xlsx saved to: {full_file_path}")

    # Run every step (isolated)
    run_step("Split by branch",    step_split_by_branch,        df)
    run_step("Branch emails",      step_send_branch_emails,     pct)
    run_step("Split by region",    step_split_by_region,        df)
    run_step("Region emails",      step_send_region_emails,     pct)
    run_step("Mismatch check",     step_mismatch_check,         df)
    run_step("Delhi zone filter",  step_filter_delhi_zone,      df)
    run_step("Split by zone",      step_split_by_zone,          df, pivot_df)
    run_step("Zone emails",        step_send_zone_emails,       pct)
    run_step("Filter SMS clients", step_filter_sms_clients,     df)
    run_step("WhatsApp messages",  step_send_whatsapp_messages, pct)
    run_step("ALERT 1 CR image",   step_alert_1cr_image,        df, pivot_df)
    run_step("Log email",          step_send_log_email)
    run_step("Backup",             step_backup)

    runtime = datetime.now() - START_TIME

    print(f"\n⏱ Total Runtime : {runtime}")

    print("\n" + "=" * 60)
    print("ALL STEPS COMPLETED")
    print("=" * 60)


if __name__ == "__main__":
    main()