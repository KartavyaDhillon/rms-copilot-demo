import polars as pl
import os
import re
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

# =====================================================
# CONFIG
# =====================================================
BASE_DIR = r"C:\PRIYANKA\NCDEX_EODSAR"
UCC_CODE = "01292"
CC02_ID = "06436"


# =====================================================
# PRE-CHECK: ALL REQUIRED FILES (NCDEX FLOW)
# =====================================================
def precheck_files():
    missing = []
    files = set(os.listdir(BASE_DIR))

    def exists_pattern(starts):
        return any(
            f.upper().startswith(starts.upper()) and f.upper().endswith(".CSV")
            for f in files
        )

    # ---- BASE MGN FILE (01292_MGN_<ddmmyyyy><time>.CSV)
    base_file = next(
        (
            f for f in files
            if f.startswith(f"{UCC_CODE}_MGN_") and f.upper().endswith(".CSV")
        ),
        None
    )
    if not base_file:
        missing.append(f"{UCC_CODE}_MGN_<ddmmyyyy><time>.CSV")

    # ---- Allocation
    if not exists_pattern("Allocation_Detail_"):
        missing.append("Allocation_Detail_<ddmmyyyy>.CSV")

    # ---- CC02 FILES
    for p in ["C_CC02", "F_CC02", "X_CC02", "O_CC02"]:
        if not exists_pattern(f"{p}_{CC02_ID}_"):
            missing.append(f"{p}_{CC02_ID}_<ddmmyyyy>.CSV")

    # ---- MCX RESPONSE FILE
    if not exists_pattern("RESPONSE_2_mcx"):
        missing.append("RESPONSE_2_mcx.csv")

    if missing:
        print("\n❌ FILE PRE-CHECK FAILED\n")
        for f in missing:
            print("  -", f)
        raise SystemExit(1)

    print("✅ All required NCDEX files found. Proceeding...\n")

    return base_file


# =====================================================
# CALL PRE-CHECK (MANDATORY)
# =====================================================
base_file = precheck_files()


# =====================================================
# SAME-DATE CHECK: all required files must share the same date
# =====================================================
from datetime import datetime as _sdc_datetime

_same_date_groups = [
    ("Base MGN file", rf"{UCC_CODE}_MGN_(\d{{8}})", "%d%m%Y"),
    ("Allocation_Detail file", r"Allocation_Detail_(\d{8})", "%d%m%Y"),
    ("C_CC02 file", rf"C_CC02_{CC02_ID}_(\d{{8}})", "%d%m%Y"),
    ("F_CC02 file", rf"F_CC02_{CC02_ID}_(\d{{8}})", "%d%m%Y"),
    ("X_CC02 file", rf"X_CC02_{CC02_ID}_(\d{{8}})", "%d%m%Y"),
    ("O_CC02 file", rf"O_CC02_{CC02_ID}_(\d{{8}})", "%d%m%Y"),
]

_same_date_found = []  # (label, filename, parsed_date)
for _label, _pattern, _fmt in _same_date_groups:
    for _f in os.listdir(BASE_DIR):
        _m = re.search(_pattern, _f)
        if _m:
            try:
                _parsed = _sdc_datetime.strptime(_m.group(1), _fmt).date()
                _same_date_found.append((_label, _f, _parsed))
            except ValueError:
                pass

if _same_date_found:
    _anchor_label, _anchor_file, _anchor_date = _same_date_found[0]
    _mismatches = [(l, f, d) for (l, f, d) in _same_date_found if d != _anchor_date]
    if _mismatches:
        print("\n❌ FILE DATE MISMATCH DETECTED\n")
        print(f"   - Anchor: {_anchor_label} ({_anchor_file}) → {_anchor_date.strftime('%d-%m-%Y')}")
        for _l, _f, _d in _mismatches:
            print(f"   - Mismatch: {_l} ({_f}) → {_d.strftime('%d-%m-%Y')}")
        print("\n⛔ Process stopped. Please ensure all required files are of the same date and run again.")
        raise SystemExit(1)


# =====================================================
# HELPERS
# =====================================================
def normalize_client(col):
    return (
        pl.col(col)
        .cast(pl.Utf8)
        .str.replace("^'", "", literal=False)
        .str.strip_chars()
    )

# =====================================================
# FIND BASE FILE & DATE
# =====================================================
base_file = next(
    f for f in os.listdir(BASE_DIR)
    if f.startswith(f"{UCC_CODE}_MGN_") and f.upper().endswith(".CSV")
)

m = re.search(r"_MGN_(\d{8})", base_file)
if not m:
    raise ValueError("Date not found")

ddmmyyyy = m.group(1)
output_excel = f"NCDEX_{ddmmyyyy}.xlsx"

# =====================================================
# READ BASE FILE
# =====================================================
df_working = pl.read_csv(os.path.join(BASE_DIR, base_file))

# Keep A → Z
df_working = df_working.select(df_working.columns[:26])

# Normalize Client ID
df_working = df_working.with_columns(
    normalize_client("Client ID").alias("Client ID")
)

# Sort & filter
df_working = (
    df_working
    .sort("Total Shortfall (EOD Short Allocation)", descending=True)
    .filter(pl.col("Total Shortfall (EOD Short Allocation)") != 0)
)

# =====================================================
# CREATE ALL REQUIRED COLUMNS
# =====================================================
working_headers = [
    "Trade Date","Trading Member ID","Client ID",
    "Initial Mrgin (Initial + Extreme Loss)","Peak Initial Mrgin",
    "Other Mrgns","MTM (Gain/Loss)",
    "Upfront initial Mrgn Collected","Peak Initial Mrgn Collected",
    "Other Margins Collected by T+2",
    "MTM (Gains)/ Loss Collected By T+2",
    "Shortfall in Initial Mrgn","Shortfall in Peak Mrgn",
    "Shortfall in Other Mrgn","Shortfall in MTM (Gain)/Loss",
    "Initial Margin at Peak Short Allocation (IM-PSA)",
    "Collateral Value at EOD",
    "Collateral Value at Peak Short Allocation (PSA)",
    "Excess Collateral With Other CCs (ICCL)",
    "Excess Collateral With Other CCs (MCXCCL)",
    "Excess Collateral With Other CCs (NCL)",
    "Excess Collateral With Other CCs (Others)",
    "Delay In Processing Allocation Request",
    "Trade In Wrong Client Code",
    "Total Shortfall (EOD Short Allocation)",
    "Total Shortfall (Peak Short Allocation)",
    ">>","DFF","category","T5 _REMARKS","ref","CHK",
    "mcx 3_allocation file_ ledger",
    "c_cc02 excess","f_cc02 excess","X_cc02 excess",
    "O_CC02 excess","MCX_ excess","remarks"
]

df_working = df_working.with_columns([
    pl.lit(None).alias(c) for c in working_headers if c not in df_working.columns
])

# =====================================================
# ALLOCATION MERGE
# =====================================================
df_alloc = pl.read_csv(
    os.path.join(BASE_DIR, f"Allocation_Detail_{ddmmyyyy}.csv"),
    infer_schema_length=10000
)

# Force numeric columns safely
df_alloc = df_alloc.with_columns([
    pl.when(pl.col(c).cast(pl.Float64, strict=False).is_not_null())
    .then(pl.col(c).cast(pl.Float64, strict=False))
    .otherwise(pl.col(c))
    .alias(c)
    for c in df_alloc.columns
])

alloc_lookup = (
    df_alloc
    .with_columns(normalize_client("AccountID").alias("Client ID"))
    .select(
        "Client ID",
        pl.col("TotalAllocation").cast(pl.Float64, strict=False)
        .alias("mcx 3_allocation file_ ledger_new")
    )
)

df_working = (
    df_working
    .join(alloc_lookup, on="Client ID", how="left")
    .with_columns(
        pl.coalesce([
            pl.col("mcx 3_allocation file_ ledger"),
            pl.col("mcx 3_allocation file_ ledger_new")
        ]).alias("mcx 3_allocation file_ ledger")
    )
    .drop("mcx 3_allocation file_ ledger_new")
)

# =====================================================
# CC02 MERGE
# =====================================================
def merge_cc02(df, prefix):
    raw = pl.read_csv(
        os.path.join(BASE_DIR, f"{prefix}_{CC02_ID}_{ddmmyyyy}.csv"),
        has_header=False,
        schema_overrides={
            "column_1": pl.Utf8
        },
        infer_schema_length=10000
    )

    lookup = (
        raw
        .with_columns(
            normalize_client("column_2").alias("Client ID"),
            (
                pl.col("column_6").cast(pl.Float64, strict=False)
                - pl.col("column_7").cast(pl.Float64, strict=False)
            ).alias("val")
        )
        .select("Client ID", pl.col("val").alias(prefix + "_new"))
    )

    COLUMN_MAP = {
        "C_CC02": "c_cc02 excess",
        "F_CC02": "f_cc02 excess",
        "X_CC02": "X_cc02 excess",
        "O_CC02": "O_CC02 excess",
    }

    target = COLUMN_MAP[prefix]
    temp = prefix + "_new"

    df = (
        df
        .join(lookup, on="Client ID", how="left")
        .with_columns(
            pl.coalesce([pl.col(target), pl.col(temp)]).alias(target)
        )
        .drop(temp)
    )

    return df, raw

df_working, c_raw = merge_cc02(df_working, "C_CC02")
df_working, f_raw = merge_cc02(df_working, "F_CC02")
df_working, x_raw = merge_cc02(df_working, "X_CC02")
df_working, o_raw = merge_cc02(df_working, "O_CC02")

# =====================================================
# MCX RESPONSE MERGE
# =====================================================
# =====================================================
# SAFE MCX RESPONSE READ (FINAL FIX)
# =====================================================

df_mcx = pl.read_csv(
    os.path.join(BASE_DIR, "RESPONSE_2_mcx.csv"),
    infer_schema_length=10000
)

# Force ALL numeric columns to Float64 safely
df_mcx = df_mcx.with_columns([
    pl.when(pl.col(c).cast(pl.Float64, strict=False).is_not_null())
    .then(pl.col(c).cast(pl.Float64, strict=False))
    .otherwise(pl.col(c))
    .alias(c)
    for c in df_mcx.columns
])

mcx_lookup = (
    df_mcx
    .with_columns(
        normalize_client("UCC Code").alias("Client ID"),
        (
            pl.col("Excess Margin").cast(pl.Float64, strict=False)
            - pl.col("Deficit margin").cast(pl.Float64, strict=False)
        ).alias("MCX_new")
    )
    .select("Client ID", "MCX_new")
)

df_working = (
    df_working
    .join(mcx_lookup, on="Client ID", how="left")
    .with_columns(
        pl.coalesce([pl.col("MCX_ excess"), pl.col("MCX_new")]).alias("MCX_ excess")
    )
    .drop("MCX_new")
)

# =====================================================
# FORCE NUMERIC + CHK / T5
# =====================================================
num_cols = [
    "mcx 3_allocation file_ ledger",
    "c_cc02 excess","f_cc02 excess",
    "X_cc02 excess","O_CC02 excess",
    "MCX_ excess",
    "Total Shortfall (EOD Short Allocation)"
]

df_working = df_working.with_columns([
    pl.col(c).cast(pl.Float64, strict=False).fill_null(0)
    for c in num_cols
])

df_working = df_working.with_columns(
    (
        pl.col("c_cc02 excess")
        + pl.col("f_cc02 excess")
        + pl.col("X_cc02 excess")
        + pl.col("O_CC02 excess")
        + pl.col("MCX_ excess")
        - pl.col("Total Shortfall (EOD Short Allocation)")
    ).alias("CHK")
)

df_working = df_working.with_columns(
    pl.when(pl.col("CHK") >= 0)
    .then(pl.lit("covered with CASH/ NFO/ CDS"))
    .otherwise(pl.lit("other"))
    .alias("T5 _REMARKS")
)

df_working = df_working.select(working_headers)

# =====================================================
# WRITE EXCEL
# =====================================================

# 🔁 Convert Polars → Pandas ONCE
df_working_pd = df_working.to_pandas()

import numpy as np

# Short names
TS = df_working_pd["Total Shortfall (EOD Short Allocation)"]
X  = df_working_pd["c_cc02 excess"]
O  = df_working_pd["f_cc02 excess"]
M  = df_working_pd["MCX_ excess"]

# Pre-calc sums
NCL_SUM   = X + O
TOTAL_SUM = X + O + M

# Initialize REMARKS
df_working_pd["REMARKS"] = ""

# 1️⃣ Priority 1 — NCL
mask_ncl = NCL_SUM >= TS
df_working_pd.loc[mask_ncl, "REMARKS"] = "COVERED WITH NCL EXCESS"

# 2️⃣ Priority 2 — MCX
mask_mcx = (~mask_ncl) & (M >= TS)
df_working_pd.loc[mask_mcx, "REMARKS"] = "COVERED WITH MCX EXCESS"

# 3️⃣ Priority 3 — Combined
mask_partial = (~mask_ncl) & (~mask_mcx) & (TOTAL_SUM >= TS)
df_working_pd.loc[mask_partial, "REMARKS"] = "PARTIALLY COVERED WITH NCL & MCX"

# 4️⃣ Shortfall
mask_short = (~mask_ncl) & (~mask_mcx) & (~mask_partial)
df_working_pd.loc[mask_short, "REMARKS"] = (
    "SHORT_BY_" +
    (TS - TOTAL_SUM).round(2).astype(str)
)

# -----------------------------
# EXCEL WRITE (UNCHANGED)
# -----------------------------
wb = Workbook()
ws = wb.active
ws.title = "NCDEX WORKING"

for r in dataframe_to_rows(df_working_pd, index=False, header=True):
    ws.append(r)

for cell in ws[1]:
    cell.font = Font(bold=True)

for i, col in enumerate(ws.columns, start=1):
    width = max(len(str(c.value)) if c.value else 0 for c in col)
    ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 40)

def add_sheet(df, name, header):
    ws = wb.create_sheet(name)
    for r in dataframe_to_rows(df.to_pandas(), index=False, header=header):
        ws.append(r)

add_sheet(df_alloc, "ALLOCATION", True)
add_sheet(df_mcx, "MCX", True)
add_sheet(c_raw, "C_CC02", False)
add_sheet(f_raw, "F_CC02", False)
add_sheet(x_raw, "X_CC02", False)
add_sheet(o_raw, "O_CC02", False)

wb.save(os.path.join(BASE_DIR, output_excel))
print(f"✅ NCDEX FILE GENERATED SUCCESSFULLY → {output_excel}")



# =====================================================
# EMAIL → BACKUP → CLEANUP (FINAL & SAFE)
# =====================================================
import smtplib
import time
import shutil
from email.message import EmailMessage


def send_email_with_infinite_retry(output_excel_path, report_date):
    SMTP_SERVER = "smtp3.netcore.co.in"
    SMTP_PORT = 587
    SMTP_EMAIL = "kartavya.dhillon@mastertrust.co.in"
    SMTP_PASSWORD = ""

    TO_LIST = ["rms_del@mastertrust.co.in"]
    CC_LIST = []

    SUBJECT = f"NCDEX EODSAR REPORT – {report_date}"

    BODY = f"""Dear Priyanka,

Please find attached the NCDEX EODSAR for {report_date}.

Regards,
Kartavya Dhillon
Master Capital Services Ltd
"""

    msg = EmailMessage()
    msg["From"] = SMTP_EMAIL
    msg["To"] = ", ".join(TO_LIST)
    msg["Cc"] = ", ".join(CC_LIST)
    msg["Subject"] = SUBJECT
    msg.set_content(BODY)

    with open(output_excel_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="octet-stream",
            filename=os.path.basename(output_excel_path)
        )

    recipients = TO_LIST + CC_LIST

    attempt = 0

    while True:
        attempt += 1
        try:
            print(f"📧 SMTP attempt {attempt}")

            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
            server.ehlo()              # ✅ REQUIRED
            server.starttls()
            server.ehlo()              # ✅ REQUIRED
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(
                msg,
                from_addr=SMTP_EMAIL,
                to_addrs=recipients
            )
            server.quit()

            print("✅ Email sent successfully")
            break

        except Exception as e:
            wait = min(60, attempt * 5)   # ✅ backoff
            print(f"❌ SMTP error: {e}")
            print(f"🔁 Retrying in {wait} seconds...")
            time.sleep(wait)


def backup_and_cleanup_files(base_dir, output_excel_name, files_used):
   
    # MCX_EODSAR_07012026.xlsx → 07012026
    date_part = output_excel_name.split("_")[-1].split(".")[0]

    # 🔑 FIXED ROOT BACKUP PATH
    BACKUP_ROOT = r"C:\PRIYANKA\BACKUP FILES AUTOMATED"

    backup_dir = os.path.join(
        BACKUP_ROOT,
        "NCDEX_EODSAR",
        date_part
    )
    os.makedirs(backup_dir, exist_ok=True)

    copied_files = []

    # -----------------------------
    # BACKUP
    # -----------------------------
    for file_path in files_used:
        if not os.path.isfile(file_path):
            continue

        filename = os.path.basename(file_path)

        # SAFETY: never touch scripts
        if filename.lower().endswith((".py", ".bat")):
            continue

        dest_path = os.path.join(backup_dir, filename)
        shutil.copy2(file_path, dest_path)
        copied_files.append(file_path)

    # -----------------------------
    # CLEANUP (DELETE)
    # -----------------------------
    for file_path in copied_files:
        try:
            os.remove(file_path)
        except Exception as e:
            print(f"⚠️ Unable to delete {file_path}: {e}")

    print(f"✅ Backup & cleanup completed → {backup_dir}")


# =====================================================
# EXECUTION ORDER (VERY IMPORTANT)
# =====================================================
output_excel_path = os.path.join(BASE_DIR, output_excel)

files_used = [
    os.path.join(BASE_DIR, base_file),
    os.path.join(BASE_DIR, f"Allocation_Detail_{ddmmyyyy}.csv"),
    os.path.join(BASE_DIR, f"C_CC02_{CC02_ID}_{ddmmyyyy}.csv"),
    os.path.join(BASE_DIR, f"F_CC02_{CC02_ID}_{ddmmyyyy}.csv"),
    os.path.join(BASE_DIR, f"X_CC02_{CC02_ID}_{ddmmyyyy}.csv"),
    os.path.join(BASE_DIR, f"O_CC02_{CC02_ID}_{ddmmyyyy}.csv"),
    os.path.join(BASE_DIR, "RESPONSE_2_mcx.csv"),
    output_excel_path
]

# 1️⃣ SEND EMAIL FIRST
send_email_with_infinite_retry(
    output_excel_path=output_excel_path,
    report_date=ddmmyyyy
)

# 2️⃣ BACKUP + DELETE FILES
backup_and_cleanup_files(
    BASE_DIR,
    output_excel,
    files_used
)

