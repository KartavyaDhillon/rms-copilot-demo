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

import os
import sys
import glob
import pandas as pd
from openpyxl import load_workbook
import shutil
from datetime import datetime


# ============================================================
# CONFIG
# ============================================================
BASE_FOLDER = _SAENV("SA_OUTPUT_DIR", r"C:\KARTAVYA\SHORT ALLOCATION")


# ============================================================
# RUN STATE (SLOT-AWARE PROCESSING)
# ============================================================
STATE_FILE = _SAENV("SA_STATE_FILE", r"C:\KARTAVYA\SHORT ALLOCATION\run_state.txt")

def get_current_run_suffix():
    if not os.path.exists(STATE_FILE):
        raise SystemExit("❌ run_state.txt not found. Run downloader  first.")

    data = {}
    with open(STATE_FILE) as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                data[k] = v

    # Code-2 increments RUN after completion
    run_no = int(data.get("RUN", 1)) - 1

    suffix_map = {
        1: ("i01", "01"),
        2: ("i02", "02"),
        3: ("i03", "03"),
        4: ("i04", "04"),
    }

    if run_no not in suffix_map:
        raise SystemExit("❌ Invalid RUN state. Cannot determine processing slot.")

    print(f"🔁 Processing files for RUN {run_no}")
    return run_no, suffix_map[run_no][0], suffix_map[run_no][1]


# ============================================================
# BACKUP & CLEANUP AFTER SUCCESSFUL RUN
# ============================================================
def backup_and_cleanup(files_used, run_no):
    today_str = datetime.today().strftime("%d-%m-%Y")

    base_backup = os.path.join(
        BASE_FOLDER,
        "BACKUP FILES AUTOMATED",
        today_str,
        f"{run_no:02d}"
    )

    os.makedirs(base_backup, exist_ok=True)

    print(f"\n📦 Backing up files to: {base_backup}\n")

    for label, path in files_used.items():
        if not path:
            continue
        # RISK_REPORT lives in SA_COMMON_DIR — never move/delete it here
        if label == "RISK_REPORT":
            print(f"⏭ {label}: skipped (managed by common folder)")
            continue
        if not os.path.exists(path):
            print(f"⚠ Skipping missing file: {path}")
            continue

        dest = os.path.join(base_backup, os.path.basename(path))
        print(f"➡ {label}: {os.path.basename(path)}")

        shutil.move(path, dest)

    print("🧹 Backup & cleanup completed\n")



# ============================================================
# FILE PATTERNS
# ============================================================
run_no, nse_i, mcx_i = get_current_run_suffix()

# ============================================================
# OUTPUT FILE NAME (RUN-WISE)
# ============================================================
OUTPUT_EXCEL = os.path.join(
    BASE_FOLDER,
    f"SA{run_no:02d}_OUTPUT FILE.xlsx"
)

os.makedirs(os.path.dirname(OUTPUT_EXCEL), exist_ok=True)



patterns = {
    "C_LIS FILE": f"C_SA01_P_06436_*_{nse_i}.lis",
    "F_LIS FILE": f"F_SA01_P_06436_*_{nse_i}.lis",
    "CO_LIS FILE": f"O_SA01_P_06436_*_{nse_i}.lis",
    "MCX PEAK FILE": f"MCX_PeakMargin56720_*_{mcx_i}.csv",
    "NCCL PEAK FILE": f"NCCL_MARGIN_REP_*_{mcx_i}.csv",
    "MCX_DEPOSIT_XLSX": "MCX_DEPOSIT_*.xlsx",
    "MCX_DEPOSIT_CSV": "MCX_DEPOSIT_*.csv",
    "RISK_REPORT": "RISK_REPORT_*.csv",
    "BRM_LIST_XLSX": "BRM LIST_File*.xlsx",
    "BRM_LIST_CSV": "BRM LIST_File*.csv",
    "EXCEPTION_FILE": "EXCEPTION.xlsx"
}

COMMON_DIR = _SAENV("SA_COMMON_DIR", r"C:\COMMON FILES")

def find_latest(pattern):
    files = glob.glob(os.path.join(BASE_FOLDER, pattern))
    if not files:
        return None
    return sorted(files)[-1]

def find_latest_common(pattern):
    """Look in the shared COMMON_DIR first (e.g. for RISK_REPORT)."""
    files = glob.glob(os.path.join(COMMON_DIR, pattern))
    if files:
        return sorted(files)[-1]
    return None

files = {k: find_latest(v) for k, v in patterns.items()}

# RISK_REPORT lives in the shared common folder — override the BASE_FOLDER lookup
_rr_common = find_latest_common("RISK_REPORT_*.csv")
if _rr_common:
    files["RISK_REPORT"] = _rr_common
    print(f"📂 RISK_REPORT → common folder: {os.path.basename(_rr_common)}")

BRM_FILE = files["BRM_LIST_XLSX"] or files["BRM_LIST_CSV"]


# pick whichever deposit file exists
MCX_DEPOSIT_FILE = files["MCX_DEPOSIT_XLSX"] or files["MCX_DEPOSIT_CSV"]

required = ["C_LIS FILE", "F_LIS FILE", "MCX PEAK FILE", "NCCL PEAK FILE", "RISK_REPORT"]


missing_files = []

# Required patterned files
for key in required:
    if files[key] is None:
        missing_files.append(f"{key} → pattern: {patterns[key]}")

# MCX Deposit
if MCX_DEPOSIT_FILE is None:
    missing_files.append(
        f"MCX_DEPOSIT → pattern: {patterns['MCX_DEPOSIT_XLSX']} OR {patterns['MCX_DEPOSIT_CSV']}"
    )

# BRM List
if BRM_FILE is None:
    missing_files.append(
        f"BRM_LIST → pattern: {patterns['BRM_LIST_XLSX']} OR {patterns['BRM_LIST_CSV']}"
    )

# EXCEPTION FILE
if files["EXCEPTION_FILE"] is None:
    missing_files.append(
        f"EXCEPTION_FILE → pattern: {patterns['EXCEPTION_FILE']}"
    )

if missing_files:
    print("\n❌ Missing required files (exact expected names/patterns):\n")
    for mf in missing_files:
        print("  -", mf)
    sys.exit(1)

print("✅ All required files found")




# ============================================================
# HEADERS
# ============================================================
lis_headers = [
    "DATE",
    "CLIENT CODE",
    "MINIMUM MARGIN REQ (A)",
    "TOTAL CASH COLLATERAL (B)",
    "TOTAL NON CASH COLLATERAL (C)",
    "TOTAL VALUE OF COLLATERAL (D)",
    "EOD SHORT ALLOCATION (E=MAX(A-D,0))"
]

mcx_headers = [
    "DATE",
    "SNAPSHOT OF INTERVAL",
    "CM ID",
    "TM ID",
    "CLIENT CODE",
    "INITIAL MARGIN",
    "ELM",
    "NET BUY PREMIUM",
    "TOTAL UPFRONT MARGIN",
    "CLIENT MARGIN ALLOCATION",
    "CLIENT OTHER COLLATERALS",
    "SHORT ALLOCATIONS"
]

# ============================================================
# READ FILES
# ============================================================
def read_lis(path):
    return pd.read_csv(path, header=None, names=lis_headers,
                       delimiter=",", dtype=str, engine="python")

def read_csv_no_header(path, headers):
    return pd.read_csv(path, header=None, names=headers, dtype=str)

c_df = read_lis(files["C_LIS FILE"])
f_df = read_lis(files["F_LIS FILE"])

#optional CO file 
if files["CO_LIS FILE"]:
    co_df = read_lis(files["CO_LIS FILE"])
else:
    co_df = pd.DataFrame(columns=lis_headers)

# Track whether CO file is missing
CO_FILE_MISSING = files["CO_LIS FILE"] is None

mcx_df = read_csv_no_header(files["MCX PEAK FILE"], mcx_headers)
nccl_df = pd.read_csv(files["NCCL PEAK FILE"], dtype=str)
risk_df = pd.read_csv(
    files["RISK_REPORT"],
    dtype=str,
    usecols=[
        "ACCOUNTID","Zone","Region","Branch","CATEGORY","CPCode","PISAccount"
    ]
)
# ============================================================
# READ BRM LIST FILE
# ============================================================
if BRM_FILE.lower().endswith(".xlsx"):
    brm_df = pd.read_excel(BRM_FILE, dtype=str)
else:
    brm_df = pd.read_csv(BRM_FILE, dtype=str)

brm_df["Branch"] = brm_df["Branch"].astype(str)
brm_df["BRM"] = brm_df["BRM"].astype(str)


# ============================================================
# READ MCX DEPOSIT WITH AUTO HEADER DETECTION
# ============================================================
def read_mcx_deposit(path):
    if path.lower().endswith(".xlsx"):
        try:
            raw = pd.read_excel(path, header=None, dtype=str)
        except Exception as _e:
            import zipfile as _zf
            if isinstance(_e, _zf.BadZipFile) or "bad magic number" in str(_e).lower() or "zipfile" in type(_e).__module__.lower():
                print(f"[MCX_CORRUPT_FILE]{path}")
            raise
    else:
        raw = pd.read_csv(path, header=None, dtype=str)

    for i in range(10):
        row = raw.iloc[i].astype(str).str.strip().str.upper()
        if "UCC CODE" in row.values and "MARGIN LIMIT" in row.values:
            df = raw.iloc[i+1:].copy()
            df.columns = raw.iloc[i]
            return df

    raise Exception("❌ Header row not found in MCX DEPOSIT file")

mcx_dep_df = read_mcx_deposit(MCX_DEPOSIT_FILE)


# ============================================================
# CLIENT CODE NORMALIZATION
# ============================================================
def normalize_client_code(x):
    if pd.isna(x):
        return x

    s = str(x).strip().upper()

    # Remove special chars
    s = s.replace("*", "").replace("PRO_", "")

    # Map all equivalent codes to ONE canonical code
    if s in {"1292", "01292", "6436", "06436", "OWN"}:
        return "06436"

    return s

# ============================================================
# READ EXCEPTION FILE
# ============================================================
exception_df = pd.read_excel(files["EXCEPTION_FILE"], dtype=str)

# Normalize column
exception_df["AccountID"] = exception_df["AccountID"].apply(normalize_client_code)

# Create lookup set
exception_set = set(exception_df["AccountID"].astype(str))



# ============================================================
# APPLY CLIENT CODE NORMALIZATION (ALL FILES)
# ============================================================

c_df["CLIENT CODE"] = c_df["CLIENT CODE"].apply(normalize_client_code)
f_df["CLIENT CODE"] = f_df["CLIENT CODE"].apply(normalize_client_code)
co_df["CLIENT CODE"] = co_df["CLIENT CODE"].apply(normalize_client_code)
mcx_df["CLIENT CODE"] = mcx_df["CLIENT CODE"].apply(normalize_client_code)
nccl_df["Client Code"] = nccl_df["Client Code"].apply(normalize_client_code)
mcx_dep_df["UCC Code"] = mcx_dep_df["UCC Code"].apply(normalize_client_code)
risk_df["ACCOUNTID"] = risk_df["ACCOUNTID"].apply(normalize_client_code)


# ============================================================
# BUILD VALID CLIENT SET (C_LIS + F_LIS + CO_LIS + NCCL)
# ============================================================
valid_clients = set(
    pd.concat([
        c_df["CLIENT CODE"],
        f_df["CLIENT CODE"],
        co_df["CLIENT CODE"],
        nccl_df["Client Code"]
    ]).astype(str)
)



# ============================================================
# SAFE NUMERIC CONVERSION
# ============================================================
def to_num(s):
    return pd.to_numeric(s, errors="coerce").fillna(0)

# margins
c_df["MINIMUM MARGIN REQ (A)"] = to_num(c_df["MINIMUM MARGIN REQ (A)"])
f_df["MINIMUM MARGIN REQ (A)"] = to_num(f_df["MINIMUM MARGIN REQ (A)"])
co_df["MINIMUM MARGIN REQ (A)"] = to_num(co_df["MINIMUM MARGIN REQ (A)"])
mcx_df["TOTAL UPFRONT MARGIN"] = to_num(mcx_df["TOTAL UPFRONT MARGIN"])
nccl_df["Initial Margin"] = to_num(nccl_df["Initial Margin"])
nccl_df["ELM Margin"] = to_num(nccl_df["ELM Margin"])

# collateral
c_df["TOTAL VALUE OF COLLATERAL (D)"] = to_num(c_df["TOTAL VALUE OF COLLATERAL (D)"])
f_df["TOTAL VALUE OF COLLATERAL (D)"] = to_num(f_df["TOTAL VALUE OF COLLATERAL (D)"])
co_df["TOTAL VALUE OF COLLATERAL (D)"] = to_num(co_df["TOTAL VALUE OF COLLATERAL (D)"])
mcx_df["CLIENT MARGIN ALLOCATION"] = to_num(mcx_df["CLIENT MARGIN ALLOCATION"])
mcx_df["CLIENT OTHER COLLATERALS"] = to_num(mcx_df["CLIENT OTHER COLLATERALS"])
nccl_df["Collateral_Value_Snapshot"] = to_num(nccl_df["Collateral_Value_Snapshot"])
mcx_dep_df["Margin Limit"] = (
    mcx_dep_df["Margin Limit"]
    .astype(str)
    .str.replace(",", "", regex=False)
)

mcx_dep_df["Margin Limit"] = (
    mcx_dep_df["Margin Limit"].astype(str).str.replace(",", "", regex=False)
)

mcx_dep_df["Margin Limit"] = to_num(mcx_dep_df["Margin Limit"])





# ============================================================
# CLIENT SETS
# ============================================================
mcx_clients = set(mcx_df["CLIENT CODE"].astype(str))

deposit_extra = mcx_dep_df[
    (~mcx_dep_df["UCC Code"].astype(str).isin(mcx_clients)) &
    (mcx_dep_df["UCC Code"].astype(str).isin(valid_clients))
]                                     #### Exclude MCX Peak clients, Include ONLY if present in C/F/NCCL

# ============================================================
# FINAL STRUCTURE
# ============================================================
final = pd.DataFrame()

final["Client/UCC Code"] = pd.concat([
    c_df["CLIENT CODE"],
    f_df["CLIENT CODE"],
    co_df["CLIENT CODE"],
    mcx_df["CLIENT CODE"],
    nccl_df["Client Code"],
    deposit_extra["UCC Code"]
]).astype(str).drop_duplicates().reset_index(drop=True)

final["Client/UCC Code"] = final["Client/UCC Code"].apply(normalize_client_code)


# ------------------------------------------------------------
# MAP ZONE / REGION / BRANCH / CATEGORY FROM RISK REPORT
# ------------------------------------------------------------

risk_df["ACCOUNTID"] = risk_df["ACCOUNTID"].astype(str)

final = final.merge(
    risk_df,
    how="left",
    left_on="Client/UCC Code",
    right_on="ACCOUNTID"
)

final.drop(columns=["ACCOUNTID"], inplace=True)


# ============================================================
# MAP BRM USING BRANCH
# ============================================================
final = final.merge(
    brm_df[["Branch", "BRM"]],
    how="left",
    on="Branch"
)


# ============================================================
# MINIMUM MARGIN REQUIRED
# ============================================================
final["Minimum Margin Required"] = (
    c_df.groupby("CLIENT CODE")["MINIMUM MARGIN REQ (A)"].sum()
    .add(f_df.groupby("CLIENT CODE")["MINIMUM MARGIN REQ (A)"].sum(), fill_value=0)
    .add(co_df.groupby("CLIENT CODE")["MINIMUM MARGIN REQ (A)"].sum(), fill_value=0)
    .add(mcx_df.groupby("CLIENT CODE")["TOTAL UPFRONT MARGIN"].sum(), fill_value=0)
    .add(nccl_df.groupby("Client Code")[["Initial Margin", "ELM Margin"]].sum().sum(axis=1), fill_value=0)
    .reindex(final["Client/UCC Code"]).fillna(0).values
)

# ============================================================
# TOTAL VALUE OF COLLATERAL + FUND
# ============================================================
final["Total value of Collateral+fund"] = (
    c_df.groupby("CLIENT CODE")["TOTAL VALUE OF COLLATERAL (D)"].sum()
    .add(f_df.groupby("CLIENT CODE")["TOTAL VALUE OF COLLATERAL (D)"].sum(), fill_value=0)
    .add(co_df.groupby("CLIENT CODE")["TOTAL VALUE OF COLLATERAL (D)"].sum(), fill_value=0)
    .add(
        mcx_df.groupby("CLIENT CODE")["CLIENT MARGIN ALLOCATION"].sum()
        .add(mcx_df.groupby("CLIENT CODE")["CLIENT OTHER COLLATERALS"].sum(), fill_value=0),
        fill_value=0
    )
    .add(nccl_df.groupby("Client Code")["Collateral_Value_Snapshot"].sum(), fill_value=0)
    .add(deposit_extra.groupby("UCC Code")["Margin Limit"].sum(), fill_value=0)
    .reindex(final["Client/UCC Code"]).fillna(0).values
)

# ============================================================
# CM SEGMENT MARGIN (FROM C_LIS ONLY)
# ============================================================
final["CM Segment Margin"] = (
    c_df.groupby("CLIENT CODE")["MINIMUM MARGIN REQ (A)"]
    .sum()
    .reindex(final["Client/UCC Code"])
    .fillna(0)
    .values
)

# ============================================================
# SHORTAGE
# ============================================================
final["Shortage(J-I)"] = (
    final["Total value of Collateral+fund"]
    - final["Minimum Margin Required"]
)



# ============================================================
# FINAL CONSOLIDATION — FORCE SINGLE RECORD PER CLIENT
# ============================================================

# 1️⃣ Fill blank Zone for canonical client 06436 as LUDH
final.loc[
    (final["Client/UCC Code"] == "06436") &
    (final["Zone"].isna() | (final["Zone"].astype(str).str.strip() == "")),
    "Zone"
] = "LUDH"

# 2️⃣ Define aggregation rules
agg_rules = {
    "Zone": "first",
    "Region": "first",
    "Branch": "first",
    "CATEGORY": "first",
    "CPCode": "first",
    "PISAccount": "first",
    "BRM": "first",
    "Minimum Margin Required": "max",
    "Total value of Collateral+fund": "max",
    "Shortage(J-I)": "max",
    "CM Segment Margin": "max",
}

# 3️⃣ Collapse to ONE row per Client/UCC Code
final = (
    final
    .groupby("Client/UCC Code", as_index=False)
    .agg(agg_rules)
)

# ============================================================
# CLEAN FLOATING-POINT NOISE
# Summing many per-segment floats (C/F/CO/MCX/NCCL) can land a value that
# is mathematically exactly 0 (e.g. Total value of Collateral+fund minus
# an identical Minimum Margin Required) on something like
# -1.45519152283669E-11 instead of a clean 0, purely from binary float
# rounding. Round every money column to 2 decimals and snap anything
# within half a paisa of zero to an exact 0 so Shortage(J-I) and friends
# never show that kind of near-zero noise.
# ============================================================
_money_cols = [
    "Minimum Margin Required",
    "Total value of Collateral+fund",
    "Shortage(J-I)",
    "CM Segment Margin",
]
for _col in _money_cols:
    final[_col] = pd.to_numeric(final[_col], errors="coerce").fillna(0).round(2)
    final.loc[final[_col].abs() < 0.005, _col] = 0.0


# ============================================================
# EXCEPTION / OTHER SERVER COLUMN
# ============================================================
final["EXCEPTION/OTHER SERVER"] = final["Client/UCC Code"].apply(
    lambda x: "YES" if str(x) in exception_set else "NO"
)


# ------------------------------------------------------------
# COLUMN ORDERING
# ------------------------------------------------------------
final = final[
    [
        "Client/UCC Code",
        "Zone",
        "Region",
        "Branch",
        "CATEGORY",
        "CPCode",
        "PISAccount",
        "BRM",
        "Minimum Margin Required",
        "Total value of Collateral+fund",
        "Shortage(J-I)",
        "CM Segment Margin",
        "EXCEPTION/OTHER SERVER"
    ]
]


# ============================================================
# TRACK FILES USED IN THIS RUN
# ============================================================
files_used = {
    "C_LIS": files["C_LIS FILE"],
    "F_LIS": files["F_LIS FILE"],
  "CO_LIS": files["CO_LIS FILE"],
    "MCX_PEAK": files["MCX PEAK FILE"],
    "NCCL_PEAK": files["NCCL PEAK FILE"],
    "MCX_DEPOSIT": MCX_DEPOSIT_FILE,
    "SUMMARY": OUTPUT_EXCEL,
}

# Include RISK & BRM only in 4th run
if run_no == 4:
    files_used["RISK_REPORT"] = files["RISK_REPORT"]
    


# ============================================================
# WRITE EXCEL (TEXT SAFE + AUTO COLUMN WIDTH)
# ============================================================
final.to_excel(OUTPUT_EXCEL, index=False, sheet_name="Summary")

wb = load_workbook(OUTPUT_EXCEL)
ws = wb["Summary"]

# ------------------------------------------------------------
# FORCE Client/UCC Code COLUMN AS TEXT
# ------------------------------------------------------------
for cell in ws["A"][1:]:
    cell.number_format = "@"

# ------------------------------------------------------------
# AUTO ADJUST COLUMN WIDTH
# ------------------------------------------------------------
for col in ws.columns:
    max_length = 0
    col_letter = col[0].column_letter  # e.g. 'A', 'B', 'C'

    for cell in col:
        if cell.value is not None:
            max_length = max(max_length, len(str(cell.value)))

    ws.column_dimensions[col_letter].width = min(max_length + 2, 55)  # cap at 55 — prevents corrupt input blowing out columns

wb.save(OUTPUT_EXCEL)

print("🎉 SUCCESS")
print(f"📄 Final Excel file created:{OUTPUT_EXCEL}")

# ============================================================
# COPY FINAL OUTPUT & RISK REPORT TO LAS FUNDING CODE FOLDER
# ============================================================

LAS_FUNDING_FOLDER = _SAENV("SA_LAS_FUNDING_DIR", r"C:\KARTAVYA\LAS FUNDING")
os.makedirs(LAS_FUNDING_FOLDER, exist_ok=True)

# ---- Copy SA0x OUTPUT FILE ----
try:
    dest_sa = os.path.join(
        LAS_FUNDING_FOLDER,
        os.path.basename(OUTPUT_EXCEL)
    )
    shutil.copy2(OUTPUT_EXCEL, dest_sa)
    print(f"📁 SA file copied to LAS funding folder → {dest_sa}")
except Exception as e:
    print(f"⚠ Failed to copy SA output file: {e}")

# RISK_REPORT is now read by LAS directly from SA_COMMON_DIR — no copy needed here.




# ============================================================
# EMAIL SENDING (ZONE-WISE + FULL FILE) WITH INFINITE RETRY
# ============================================================

import smtplib
from email.message import EmailMessage
import time

# ---------------- SMTP CONFIG ----------------
# Was fully hardcoded with no .env escape hatch at all (unlike LAS4.py, which at
# least had _SAENV fallbacks). Wired the same way now: identical defaults, so
# behavior is unchanged unless these env vars are explicitly set in .env.
SMTP_SERVER = _SAENV("SA2_SMTP_SERVER", "smtp3.netcore.co.in")
SMTP_PORT   = int(_SAENV("SA2_SMTP_PORT", "587"))
SMTP_USER   = _SAENV("SA2_SMTP_USER", "kartavya.dhillon@mastertrust.co.in")
SMTP_PASS   = _SAENV("SA2_SMTP_PASS", "$Kartavya25*")

# ---------------- EMAIL CONFIG ----------------
EMAIL_CONFIG = {
    "LUDH": {
        "zones": ["ZO", "ZOLUDH"],
        "to": _SALIST("SA2_MAIL_LUDH_TO", ["kartavya.dhillon@mastertrust.co.in"]),
        "cc": _SALIST("SA2_MAIL_LUDH_CC", []),
        "file": "LUDH_zone.xlsx"
    },
    "DELH": {
        "zones": ["ZOCDR", "ZODELH", "ZOMT"],
        "to": _SALIST("SA2_MAIL_DELH_TO", ["kartavya.dhillon@mastertrust.co.in"]),
        "cc": _SALIST("SA2_MAIL_DELH_CC", []),
        "file": "DELH_zone.xlsx"
    },
    "MUMB": {
        "zones": ["ZOMUMB"],
        "to": _SALIST("SA2_MAIL_MUMB_TO", ["kartavya.dhillon@mastertrust.co.in"]),
        "cc": _SALIST("SA2_MAIL_MUMB_CC", []),
        "file": "MUMB_zone.xlsx"
    },
    "CHND": {
        "zones": ["ZOCHND"],
        "to": _SALIST("SA2_MAIL_CHND_TO", ["kartavya.dhillon@mastertrust.co.in"]),
        "cc": _SALIST("SA2_MAIL_CHND_CC", []),
        "file": "CHND_zone.xlsx"
    }
}

FULL_FILE_EMAIL = {
    "to": _SALIST("SA2_MAIL_FULL_TO", ["kartavya.dhillon@mastertrust.co.in"]),
    "cc": _SALIST("SA2_MAIL_FULL_CC", [])
}

# ---------------- DATE EXTRACTION ----------------
def extract_trade_date_from_filename(filename):
    parts = filename.split("_")
    for p in parts:
        if p.isdigit() and len(p) == 8:
            return f"{p[0:2]}-{p[2:4]}-{p[4:]}"
    return datetime.today().strftime("%d-%m-%Y")

trade_date = extract_trade_date_from_filename(
    os.path.basename(files["C_LIS FILE"])
)

# Expected CO file name
trade_date_raw = trade_date.replace("-", "")
expected_co_file = f"O_SA01_P_06436_{trade_date_raw}_{nse_i}.lis"

# ---------------- WARNING BLOCK ----------------
co_warning_html = ""

if CO_FILE_MISSING:
    co_warning_html = f"""
    <p style="font-weight:bold;">
    NOTE: The CO file {expected_co_file} is not available on the exchange portal today.
    Therefore, the Short Allocation calculations have been performed without this file.
    </p>
    """

# ---------------- BLANK FILE WARNING BLOCK ----------------
# If the downloader created blank placeholder files (exchange didn't upload on time),
# it saves .blanked_files.json in BASE_FOLDER.  Read it here and add a NOTE to the email.
_blanked_files_warning_html = ""
try:
    import json as _bfj
    _bf_path = os.path.join(BASE_FOLDER, ".blanked_files.json")
    if os.path.isfile(_bf_path):
        _blanked = _bfj.load(open(_bf_path, encoding="utf-8"))
        if _blanked:
            import datetime as _bfdt
            _send_time = _bfdt.datetime.now().strftime("%I:%M %p")   # e.g. 10:45 AM
            _bf_lines = "".join(
                f"<li style='margin:3px 0'>{f}</li>"
                for f in _blanked
            )
            _blanked_files_warning_html = f"""
    <p style="font-weight:bold;color:#c0392b;">
    NOTE: This report has been prepared without the following file(s), as they were not provided by the exchange till {_send_time}:
    <ul style="margin:6px 0 0 0;padding-left:20px">{_bf_lines}</ul>
    </p>
    """
        # Remove the state file so subsequent runs don't show stale notes
        try: os.remove(_bf_path)
        except Exception: pass
except Exception as _bfe:
    print(f"⚠️ Could not read .blanked_files.json: {_bfe}")

# ---------------- SHARED EMAIL BODY ----------------
html_email_body = f"""
<html>
<body>

<p>Dear Team,</p>

<p>Please find the attachment of Short Allocation file dated <b>{trade_date}</b>.</p>

{co_warning_html}
{_blanked_files_warning_html}
<br>
<p>
Regards,<br>
Kartavya Dhillon<br>
011-66331000 | Ext. 84
</p>

</body>
</html>
"""

# ---------------- SMTP SEND (INFINITE RETRY) ----------------
def send_email_with_retry(msg, zone_name):
    while True:
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)

            print(f"📧 Email sent successfully → Zone: {zone_name} | Subject: {msg['Subject']}")
            break

        except Exception as e:
            print(f"⚠ SMTP error: {e}")
            print("🔁 Retrying in 10 seconds...")
            time.sleep(10)

# ---------------- ZONE-WISE EMAILS ----------------
for zone_key, cfg in EMAIL_CONFIG.items():

    zone_df = final[final["Zone"].isin(cfg["zones"])].drop(
        columns=["EXCEPTION/OTHER SERVER"],
        errors="ignore"
    )

    if zone_df.empty:
        print(f"⚠ No data for zone {zone_key}, skipping email")
        continue

    zone_file = os.path.join(BASE_FOLDER, cfg["file"])
    zone_df.to_excel(zone_file, index=False)

    msg = EmailMessage()

    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(cfg["to"])
    msg["Cc"] = ", ".join(cfg["cc"])
    msg["Subject"] = f"Short Allocation File- SA{run_no:02d}"

    msg.add_alternative(html_email_body, subtype="html")

    with open(zone_file, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=cfg["file"]
        )

    send_email_with_retry(msg, zone_key)

    files_used[f"ZONE_{zone_key}"] = zone_file


# ---------------- FULL FILE EMAIL ----------------

full_msg = EmailMessage()

full_msg["From"] = SMTP_USER
full_msg["To"] = ", ".join(FULL_FILE_EMAIL["to"])
full_msg["Cc"] = ", ".join(FULL_FILE_EMAIL["cc"])
full_msg["Subject"] = f"SA_{run_no:02d}_OUTPUT FILE"

full_msg.add_alternative(html_email_body, subtype="html")

with open(OUTPUT_EXCEL, "rb") as f:
    full_msg.add_attachment(
        f.read(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=os.path.basename(OUTPUT_EXCEL)
    )

send_email_with_retry(full_msg, "FULL")


# ============================================================
# BACKUP & CLEANUP
# ============================================================
backup_and_cleanup(files_used, run_no)

