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

import pandas as pd
import glob
import os
from datetime import datetime
import re
import sys
import sys as _la_sys
_la_sys.path.insert(0, _sa_os.path.dirname(_sa_os.path.abspath(__file__)))           # sa_codes/
_la_sys.path.insert(0, _sa_os.path.dirname(_sa_os.path.dirname(_sa_os.path.abspath(__file__))))  # project root (has las_image_generator.py)
import _checkpoint as _ckpt
from las_image_generator import xlsx_to_image as _las_img


# ==================================================
# BASE CONFIG
# ==================================================
BASE_FOLDER  = _SAENV("SA_LAS_FUNDING_DIR", r"C:\KARTAVYA\LAS FUNDING")
COMMON_DIR   = _SAENV("SA_COMMON_DIR",      r"C:\COMMON FILES")


# ==================================================
# RUN STATE GUARD — CODE 4 (RUN ONLY ON 4TH SUCCESS)
# ==================================================
import sys
from datetime import datetime

STATE_FILE = _SAENV("SA_STATE_FILE", r"C:\KARTAVYA\SHORT ALLOCATION\run_state.txt")

if not os.path.exists(STATE_FILE):
    print("❌ run_state.txt not found. SHORT ALLOCATION has not run.")
    sys.exit(1)

state = {}
with open(STATE_FILE) as f:
    for line in f:
        if "=" in line:
            k, v = line.strip().split("=", 1)
            state[k] = v

today = datetime.today().strftime("%Y-%m-%d")
state_date = state.get("DATE")
state_run  = int(state.get("RUN", 1))

completed_run = state_run - 1
if os.getenv("SA_RUN_NO", "").strip().isdigit():      # console says which run this is
    completed_run = int(os.getenv("SA_RUN_NO"))

# ---------------- HARD BLOCKS ----------------

# 1️⃣ Code-2 must have run today
if state_date != today:
    print("❌ SA04 not executed today. LAS FUNDING blocked.")
    sys.exit(0)

# 2️⃣ Code-4 allowed ONLY after 4th successful run
if completed_run != 4:
    print(
        f"⏭️ Code-4 skipped. "
        f"Completed run = {completed_run} (waiting for run 4)"
    )
    sys.exit(0)

print("✅ LAS FUNDED 4 authorised (4th run completed)")




# ==================================================
# PRE-FLIGHT FILE VALIDATION (SIMPLE & CLEAN)
# ==================================================

# SA_SKIP_PREFLIGHT=1 is set by the orchestrator (short_allocation.py) when
# running via the automated pipeline.  Code-2 already verified all inputs
# before it ran, and its backup_and_cleanup() moves files away from BASE_FOLDER,
# so checking here would produce spurious "missing" errors.
if _SAENV("SA_SKIP_PREFLIGHT", "") == "1":
    print("⏭️  SA_SKIP_PREFLIGHT=1 — skipping pre-flight validation (automated run)")
else:
    print("🔍 Checking required input files...")

    all_files = os.listdir(BASE_FOLDER)

    missing = []

    # ---------- FIXED FILES ----------
    if "MAIN.csv" not in all_files:
        missing.append("main.csv")

    if "SCENARIO.csv" not in all_files:
        missing.append("scenario.csv")

    # ---------- DATE-BASED FILES ----------
    if not any(f.startswith("LAS Debit Balances-") and f.endswith(".xlsx") for f in all_files):
        missing.append("LAS Debit Balances file")

    _sa_out_dir = _SAENV("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    _common_files = (os.listdir(COMMON_DIR) if os.path.isdir(COMMON_DIR) else []) + \
                    (os.listdir(_sa_out_dir) if os.path.isdir(_sa_out_dir) else [])
    if not any(f.startswith("RISK_REPORT_") and f.endswith(".csv") for f in all_files + _common_files):
        missing.append("RISK_REPORT file (checked LAS FUNDING + SA_COMMON_DIR + SHORT ALLOCATION)")

    # ---------- SA0X FILES ----------
    if not any(f.startswith("SA0") and f.endswith("_OUTPUT FILE.xlsx") for f in all_files):
        missing.append("SA0X OUTPUT FILE")

    # ---------- FINAL DECISION ----------
    if missing:
        print("\n❌ REQUIRED FILES MISSING ❌")
        for f in missing:
            print(f"   ❌ {f} missing")

        print("\n🛑 Script stopped.")
        sys.exit(1)

    print("✅ All required input files found")
    print("🚀 Starting main execution...\n")



# ==================================================
# FILE FRESHNESS CHECK (45 MIN RULE)
# ==================================================

def is_file_fresh(file_path, minutes=45):
    if not os.path.exists(file_path):
        return False

    file_time = os.path.getmtime(file_path)
    current_time = datetime.now().timestamp()

    age_minutes = (current_time - file_time) / 60
    return age_minutes <= minutes

if _SAENV("SA_SKIP_PREFLIGHT", "") == "1":
    print("⏭️  SA_SKIP_PREFLIGHT=1 — skipping freshness check (automated run)")
else:
    print("⏱️ Checking file freshness (45 min rule)")

    main_csv = os.path.join(BASE_FOLDER, "main.csv")
    scenario_csv = os.path.join(BASE_FOLDER, "scenario.csv")

    main_fresh = is_file_fresh(main_csv)
    scenario_fresh = is_file_fresh(scenario_csv)

    if not main_fresh or not scenario_fresh:
        print("\n❌ FILE FRESHNESS CHECK FAILED ❌")

        if not main_fresh:
            print("   ❌ main.csv is older than 45 minutes")

        if not scenario_fresh:
            print("   ❌ scenario.csv is older than 45 minutes")

        print("\n🛑 Script stopped due to stale files.")
        sys.exit(1)

    print("✅ File freshness check passed (both files within 45 minutes)\n")




main_csv = os.path.join(BASE_FOLDER, "main.csv")


today_excel = datetime.today().strftime("%d-%m-%Y")
today_csv = datetime.today().strftime("%d%m%Y")

las_file = os.path.join(
    BASE_FOLDER, f"LAS Debit Balances-{today_excel}.xlsx"
)

# RISK_REPORT lookup order:
#   1. SA_COMMON_DIR  (C:\COMMON FILES)     ← preferred shared location
#   2. SA_OUTPUT_DIR  (C:\SHORT ALLOCATION) ← where user currently places it
#   3. BASE_FOLDER    (C:\LAS FUNDING)      ← legacy fallback
_SA_OUTPUT_DIR = _SAENV("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
_risk_candidates = [
    os.path.join(COMMON_DIR,     f"RISK_REPORT_{today_csv}.csv"),
    os.path.join(_SA_OUTPUT_DIR, f"RISK_REPORT_{today_csv}.csv"),
    os.path.join(BASE_FOLDER,    f"RISK_REPORT_{today_csv}.csv"),
]
risk_file = next((p for p in _risk_candidates if os.path.isfile(p)), _risk_candidates[-1])
print(f"📂 RISK_REPORT path resolved → {risk_file}")

final_output = os.path.join(
    BASE_FOLDER, "LAS_FUNDING_OUTPUT.xlsx"
)





# ==================================================
# STEP 1: LAS FUNDING BASE
# ==================================================
if _ckpt.las_should_run("DATA_LOAD"):
    _ckpt.las_step("DATA_LOAD", script="LAS4")
print("📘 Reading LAS Debit Balance file")

df = pd.read_excel(las_file, dtype=str)
df["Net Debit"] = pd.to_numeric(df["Net Debit"], errors="coerce")
df = df[df["Net Debit"] > 0]

master_df = df[["Zone", "Trading Code", "Name", "Net Debit"]].copy()
master_df.rename(columns={
    "Zone": "ZONE",
    "Trading Code": "TradingCode",
    "Name": "client_name",
    "Net Debit": "LAS FUNDING"
}, inplace=True)

master_df["LAS FUNDING"] = (
    pd.to_numeric(master_df["LAS FUNDING"], errors="coerce").fillna(0) * -1
)

# ==================================================
# STEP 2: SA0X FILES (STRICT)
# ==================================================
if _ckpt.las_should_run("SA_COMPUTE"):
    _ckpt.las_step("SA_COMPUTE", script="LAS4")
print("📂 Reading SA0X files")

sa_files = glob.glob(os.path.join(BASE_FOLDER, "SA0*_OUTPUT FILE.xlsx"))
deposit_data = []

for f in sa_files:
    print(f"🔍 {os.path.basename(f)}")

    df_sa = pd.read_excel(f, dtype=str)
    df_sa.columns = df_sa.columns.str.strip().str.replace("\u00A0", " ", regex=False)

    if "Client/UCC Code" not in df_sa.columns:
        raise ValueError(f"❌ Client/UCC Code missing in {os.path.basename(f)}")

    if "Total value of Collateral+fund" not in df_sa.columns:
        raise ValueError(
            f"❌ Total value of Collateral+fund missing in {os.path.basename(f)}"
        )

    temp = df_sa[
        ["Client/UCC Code", "Total value of Collateral+fund"]
    ].copy()

    temp["Total value of Collateral+fund"] = pd.to_numeric(
        temp["Total value of Collateral+fund"], errors="coerce"
    ).fillna(0)

    temp.rename(columns={
        "Client/UCC Code": "TradingCode"
    }, inplace=True)

    deposit_data.append(temp)

if deposit_data:
    deposit_df = (
        pd.concat(deposit_data, ignore_index=True)
        .groupby("TradingCode", as_index=False)
        ["Total value of Collateral+fund"]
        .sum()
    )
else:
    deposit_df = pd.DataFrame(
        columns=["TradingCode", "Total value of Collateral+fund"]
    )

final_df = master_df.merge(
    deposit_df,
    on="TradingCode",
    how="left"
)

final_df["Total value of Collateral+fund"] = (
    pd.to_numeric(
        final_df["Total value of Collateral+fund"], errors="coerce"
    ).fillna(0)
)

# ==================================================
# STEP 3: Deposit (Own Client Fund)
# ==================================================
final_df["Deposit(Own Client Fund)"] = (
    final_df["Total value of Collateral+fund"] +
    final_df["LAS FUNDING"]
)

# ==================================================
# STEP 4: RISK FILE (YOUR LOGIC MERGED)
# ==================================================
print("📄 Reading RISK_REPORT")

if not os.path.exists(risk_file):
    raise FileNotFoundError(f"❌ RISK_REPORT not found: {risk_file}")

df_risk = pd.read_csv(risk_file, dtype=str)
df_risk.columns = df_risk.columns.str.strip()

df_risk["FNONOV"] = pd.to_numeric(
    df_risk.get("FNONOV", 0), errors="coerce"
).fillna(0)

df_risk["COMNOV"] = pd.to_numeric(
    df_risk.get("COMNOV", 0), errors="coerce"
).fillna(0)

df_risk["NOV"] = df_risk["FNONOV"] + df_risk["COMNOV"]

df_risk_merge = df_risk[["ACCOUNTID", "NOV"]].rename(
    columns={"ACCOUNTID": "TradingCode"}
)

final_df = final_df.merge(
    df_risk_merge,
    how="left",
    on="TradingCode"
)

final_df["NOV"] = pd.to_numeric(
    final_df["NOV"], errors="coerce"
).fillna(0)

# ==================================================
# STEP 5: ADJUST NOV (KEEP ONLY NEGATIVE VALUES)
# ==================================================
final_df["NOV"] = pd.to_numeric(final_df["NOV"], errors="coerce")

# Set positive NOV values to 0, keep negatives as-is
final_df.loc[final_df["NOV"] > 0, "NOV"] = 0

# ==================================================
# STEP 5.1: Deposit (After adjustment Short NOV)
# ==================================================
final_df["Deposit(Own Client Fund)"] = pd.to_numeric(
    final_df["Deposit(Own Client Fund)"], errors="coerce"
).fillna(0)

final_df["NOV"] = pd.to_numeric(
    final_df["NOV"], errors="coerce"
).fillna(0)

final_df["Deposit (After adjustment Short NOV)"] = (
    final_df["Deposit(Own Client Fund)"] +
    final_df["NOV"]
)


# ==================================================
# STEP 5.2 : FILTER main.csv (Product contains '-')
# ==================================================

print("🔍 Filtering main.csv where Product contains '-'")

main_file_original = os.path.join(BASE_FOLDER, "main.csv")
main_file_filtered = os.path.join(BASE_FOLDER, "main_filtered.csv")

# ---- Safety check ----
if not os.path.exists(main_file_original):
    raise FileNotFoundError(f"❌ main.csv not found: {main_file_original}")

# ---- Read main.csv ----
df_main_raw = pd.read_csv(main_file_original, dtype=str)

# ---- Ensure Product column exists ----
if "Product" not in df_main_raw.columns:
    raise KeyError("❌ 'Product' column missing in main.csv")

# ---- Apply filter ----
df_main_filtered = df_main_raw[
    df_main_raw["Product"].astype(str).str.contains("-", na=False)
]

print(
    f"✅ main.csv filtered: {len(df_main_filtered)} / {len(df_main_raw)} rows kept"
)

# ---- Save filtered file in SAME folder ----
df_main_filtered.to_csv(main_file_filtered, index=False)

print(f"📁 Filtered main file created: {main_file_filtered}")



# ==================================================
# STEP 5.3 : TENTATIVE OBLIGATION FILE (SIDE OUTPUT)
# ==================================================

print("📊 Creating Tentative Obligation File")

today = datetime.today().strftime("%d%m%Y")

input_file = os.path.join(BASE_FOLDER, "main_filtered.csv")
output_file = os.path.join(
    BASE_FOLDER,
    f"Tentative Obligation File_{today}.xlsx"
)

# ---- Safety check ----
if not os.path.exists(input_file):
    raise FileNotFoundError(f"❌ main_filtered.csv not found: {input_file}")

# ---- Read filtered main file ----
df = pd.read_csv(input_file, dtype=str)

# ---- Remove unwanted first column (Unnamed / index) ----
if df.columns[0].lower().startswith(("unnamed", "0")):
    df = df.drop(df.columns[0], axis=1)

# ---- Helpers ----
def clean_numeric(series):
    s = pd.to_numeric(series, errors="coerce")
    s = s.dropna().round(0).astype(int)
    return s[s != 0]

def add_total_row(df, col_name):
    if not df.empty:
        total = df[col_name].sum()
        total_row = pd.DataFrame(
            {"ClientID": ["TOTAL"], col_name: [total]}
        )
        df = pd.concat([df, total_row], ignore_index=True)
    return df

def process_sheet(df, col_name, writer):
    if {"ClientID", col_name}.issubset(df.columns):
        temp = df[["ClientID", col_name]].copy()
        temp[col_name] = clean_numeric(temp[col_name])
        temp = temp.dropna(subset=[col_name])
        temp = temp.sort_values(by=col_name, ascending=True)
        temp = add_total_row(temp, col_name)
        temp.to_excel(writer, sheet_name=col_name, index=False)

# ---- Write Excel ----
with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    process_sheet(df, "DayNetPremium", writer)
    process_sheet(df, "EquityAmount", writer)
    process_sheet(df, "FuturesMTM", writer)

print(f"✅ Tentative Obligation File created: {output_file}")





# ==================================================
# STEP 6: MAIN FILE READ
# ==================================================

main_df = pd.read_csv(main_csv, dtype=str)
main_df.columns = main_df.columns.str.strip()

main_df["MTM"] = pd.to_numeric(
    main_df.get("MTM", 0), errors="coerce"
).fillna(0)

main_df["MCXMTM"] = pd.to_numeric(
    main_df.get("MCXMTM", 0), errors="coerce"
).fillna(0)

main_df["MTM."] = main_df["MTM"] + main_df["MCXMTM"]



# ==================================================
# STEP 6.1: MERGE MAIN.CSV INTO FINAL_DF
# ==================================================

# Ensure required columns exist
required_cols = {"ClientID", "MTM.", "Total Margin Utill"}
missing = required_cols - set(main_df.columns)

if missing:
    raise ValueError(f"❌ Missing columns in main.csv: {missing}")

# Convert numeric safely
main_df["MTM."] = pd.to_numeric(
    main_df["MTM."], errors="coerce"
).fillna(0)

main_df["Total Margin Utill"] = pd.to_numeric(
    main_df["Total Margin Utill"], errors="coerce"
).fillna(0)

# Keep only required columns
main_merge_df = main_df[
    ["ClientID", "MTM.", "Total Margin Utill"]
].copy()

# Merge into final_df
final_df = final_df.merge(
    main_merge_df,
    left_on="TradingCode",
    right_on="ClientID",
    how="left"
)

# Drop duplicate ClientID column
final_df.drop(columns=["ClientID"], inplace=True)


# ==================================================
# STEP 6.2: SCENARIO FILE MERGE
# ==================================================

scenario_csv = os.path.join(BASE_FOLDER, "scenario.csv")

if not os.path.exists(scenario_csv):
    raise FileNotFoundError(f"❌ scenario.csv not found: {scenario_csv}")

scenario_df = pd.read_csv(scenario_csv, dtype=str)
scenario_df.columns = scenario_df.columns.str.strip()

# Required scenario columns
scenario_cols = [
    "Client ID",
    "UP_2%", "DOWN_2%",
    "UP_4%", "DOWN_4%",
    "UP_6%", "DOWN_6%"
]

missing_cols = set(scenario_cols) - set(scenario_df.columns)
if missing_cols:
    raise ValueError(f"❌ Missing columns in scenario.csv: {missing_cols}")

# Keep only required columns
scenario_df = scenario_df[scenario_cols].copy()

# Merge scenario data into final_df
final_df = final_df.merge(
    scenario_df,
    left_on="TradingCode",
    right_on="Client ID",
    how="left"
)

# Drop duplicate Client ID column
final_df.drop(columns=["Client ID"], inplace=True)


final_df = final_df.fillna("0")




# ==================================================
# STEP 7: SAVE FINAL OUTPUT
# ==================================================
final_df.to_excel(final_output, index=False)

print("\n🎉 FULL LAS + SA + RISK PIPELINE COMPLETED")
print(f"📁 Final Output: {final_output}")


# ==================================================
# STEP 8: CREATE FILTERED & SCALED OUTPUT FILE
# ==================================================

print("📊 Creating filtered & scaled output file")

filtered_output = os.path.join(
    BASE_FOLDER, "LAS_FUNDING_OUTPUT_FILTERED.xlsx"
)

# Work on a copy so final_df remains unchanged
df_filtered = final_df.copy()

# --------------------------------------------------
# 8.1 Ensure numeric for filtering & sorting
# --------------------------------------------------
df_filtered["Total Margin Utill"] = pd.to_numeric(
    df_filtered.get("Total Margin Utill", 0),
    errors="coerce"
).fillna(0)

# --------------------------------------------------
# 8.2 Filter Total Margin Utill > 0
# --------------------------------------------------
df_filtered = df_filtered[df_filtered["Total Margin Utill"] > 0]

# --------------------------------------------------
# 8.3 Filter required ZONES
# --------------------------------------------------
zones_to_keep = ["DELH", "MT", "CDR"]
df_filtered = df_filtered[df_filtered["ZONE"].isin(zones_to_keep)]

# --------------------------------------------------
# 8.4 Sort by Total Margin Utill (Descending)
# --------------------------------------------------
df_filtered = df_filtered.sort_values(
    by="Total Margin Utill",
    ascending=False
)

# --------------------------------------------------
# 8.5 Drop unwanted columns (safe)
# --------------------------------------------------
cols_to_drop = ["NOV","Total value of Collateral+fund"]
df_filtered = df_filtered.drop(
    columns=cols_to_drop,
    errors="ignore"
)

# --------------------------------------------------
# 8.6 Scale selected columns by 1,00,000
# --------------------------------------------------
cols_to_scale = [
    "LAS FUNDING",
    "Deposit(Own Client Fund)",
    "Deposit (After adjustment Short NOV)",
    "MTM.",
    "Total Margin Utill",
    "UP_2%",
    "DOWN_2%",
    "UP_4%",
    "DOWN_4%",
    "UP_6%",
    "DOWN_6%"
]

for col in cols_to_scale:
    if col in df_filtered.columns:
        df_filtered[col] = pd.to_numeric(
            df_filtered[col]
            .astype(str)
            .str.replace(",", "")
            .str.strip(),
            errors="coerce"
        ).fillna(0)

        # Always divide by 1,00,000
        df_filtered[col] = (df_filtered[col] / 100000).round(2)

# --------------------------------------------------
# 8.7 Save filtered & scaled file
# --------------------------------------------------
df_filtered.to_excel(
    filtered_output,
    index=False,
    float_format="%.2f"
)

print(f"✅ Filtered & scaled file created successfully: {filtered_output}")


# ==================================================
# STEP 8.8 : CREATE vertical_1 FILE
# ==================================================

print("📄 Creating vertical_1 file")

vertical_output = os.path.join(
    BASE_FOLDER, "vertical_1.xlsx"
)

# Codes to remove completely (case-insensitive)
codes_to_remove = ["sn120", "sn343", "pcg011"]

vertical_df = df_filtered[
    (~df_filtered["TradingCode"].astype(str).str.startswith("8")) &
    (~df_filtered["TradingCode"].astype(str).str.lower().isin(codes_to_remove))
]

vertical_created = False   # 👈 ADD THIS LINE

if not vertical_df.empty:
    vertical_df.to_excel(vertical_output, index=False)
    vertical_created = True   # 👈 ADD THIS
    print(f"✅ vertical_1 file created successfully: {vertical_output}")
else:
    print("⚠️ No data after filtering. vertical_1 file not created.")

# ==================================================
# STEP 8.9 : FORMAT vertical_1 EXCEL FILE
# ==================================================

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

vertical_file = os.path.join(BASE_FOLDER, "vertical_1.xlsx")

if not os.path.exists(vertical_file):
    print("⚠️ vertical_1 not found — skipping formatting.")
else:
    wb = load_workbook(vertical_file)
    ws = wb.active

    title_text = "Scenario Analysis LAS Account (in lac.)-Vertical 1"

    # Insert title row
    ws.insert_rows(1)
    ws["A1"] = title_text

    max_col = ws.max_column
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)

    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].fill = PatternFill(start_color="ADD8E6", end_color="ADD8E6", fill_type="solid")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Header styling (row 2)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin")
    )

    for col in range(1, max_col + 1):
        cell = ws.cell(row=2, column=col)
        cell.font = Font(bold=True)
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Align data rows
    text_cols = ["ZONE", "TradingCode", "client_name"]

    for row in ws.iter_rows(min_row=3, max_row=ws.max_row, max_col=max_col):
        for cell in row:
            col_name = ws.cell(row=2, column=cell.column).value
            if col_name in text_cols:
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            else:
                cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)

    # Widen client_name column
    ws.column_dimensions["C"].width = 25

    wb.save(vertical_file)

    print(f"✅ vertical_1.xlsx formatted successfully: {vertical_file}")

if _ckpt.las_should_run("SCREENSHOTS"):
    _ckpt.las_step("SCREENSHOTS", script="LAS4")


# ==================================================
# STEP 8.9.1 : SCREENSHOT vertical_1 FILE
# ==================================================

excel_path = os.path.join(BASE_FOLDER, "vertical_1.xlsx")
image_path = os.path.join(BASE_FOLDER, "VERTICAL_1_SS.JPG")
_las_img(excel_path, image_path)



# ==================================================
# STEP 8.10 : CREATE VERTICAL_2 FILE
# ==================================================

print("📄 Creating VERTICAL_2 file")

vertical2_output = os.path.join(
    BASE_FOLDER, "VERTICAL_2.xlsx"
)

codes_to_remove = ["sn120", "sn343", "pcg011"]

vertical2_df = df_filtered[
    (df_filtered["TradingCode"].astype(str).str.startswith("8")) &
    (~df_filtered["TradingCode"].astype(str).str.lower().isin(codes_to_remove))
]

vertical2_df.to_excel(vertical2_output, index=False)

print(f"✅ VERTICAL_2 file created successfully: {vertical2_output}")

# ==================================================
# STEP 8.11: FORMAT VERTICAL_2 EXCEL FILE
# ==================================================

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

vertical2_file = os.path.join(BASE_FOLDER, "VERTICAL_2.xlsx")

wb = load_workbook(vertical2_file)
ws = wb.active

title_text = "Scenario Analysis LAS Account (in lac.)-Vertical 2"

ws.insert_rows(1)
ws["A1"] = title_text

max_col = ws.max_column
ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)

ws["A1"].font = Font(bold=True, size=14)
ws["A1"].fill = PatternFill(start_color="ADD8E6", end_color="ADD8E6", fill_type="solid")
ws["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

thin_border = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin")
)

for col in range(1, max_col + 1):
    cell = ws.cell(row=2, column=col)
    cell.font = Font(bold=True)
    cell.border = thin_border
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

text_cols = ["ZONE", "TradingCode", "client_name"]

for row in ws.iter_rows(min_row=3, max_row=ws.max_row, max_col=max_col):
    for cell in row:
        col_name = ws.cell(row=2, column=cell.column).value
        if col_name in text_cols:
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        else:
            cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)

ws.column_dimensions["C"].width = 25

wb.save(vertical2_file)

print(f"✅ VERTICAL_2.xlsx formatted successfully: {vertical2_file}")


# ==================================================
# STEP 8.11.1: SCREENSHOT EXPORT – VERTICAL_2
# ==================================================
print("📸 Taking screenshot for VERTICAL_2")
excel_path = os.path.join(BASE_FOLDER, "VERTICAL_2.xlsx")
image_path = os.path.join(BASE_FOLDER, "VERTICAL_2_SS.JPG")
_las_img(excel_path, image_path)




# ==================================================
# STEP 8.12: FORMAT LAS_FUNDING_OUTPUT_FILTERED EXCEL
# ==================================================

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

filtered_file = os.path.join(
    BASE_FOLDER, "LAS_FUNDING_OUTPUT_FILTERED.xlsx"
)

wb = load_workbook(filtered_file)
ws = wb.active

title_text = "Scenario Analysis LAS Account (in lac.)-Full File of Delhi Zone"

# Insert title row
ws.insert_rows(1)
ws["A1"] = title_text

max_col = ws.max_column
ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)

ws["A1"].font = Font(bold=True, size=14)
ws["A1"].fill = PatternFill(
    start_color="ADD8E6",
    end_color="ADD8E6",
    fill_type="solid"
)
ws["A1"].alignment = Alignment(
    horizontal="center",
    vertical="center",
    wrap_text=True
)

# Header styling (row 2)
thin_border = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin")
)

for col in range(1, max_col + 1):
    cell = ws.cell(row=2, column=col)
    cell.font = Font(bold=True)
    cell.border = thin_border
    cell.alignment = Alignment(
        horizontal="center",
        vertical="center",
        wrap_text=True
    )

# Alignment rules
text_cols = ["ZONE", "TradingCode", "client_name"]

for row in ws.iter_rows(min_row=3, max_row=ws.max_row, max_col=max_col):
    for cell in row:
        col_name = ws.cell(row=2, column=cell.column).value
        if col_name in text_cols:
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        else:
            cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)

# Widen client_name column
ws.column_dimensions["C"].width = 25

wb.save(filtered_file)

print(f"✅ LAS_FUNDING_OUTPUT_FILTERED.xlsx formatted successfully: {filtered_file}")


# ==================================================
# STEP 8.12.1: SCREENSHOT – DELHI (FUNDING_SS.JPG)
# ==================================================

excel_path = os.path.join(BASE_FOLDER, "LAS_FUNDING_OUTPUT_FILTERED.xlsx")
image_path = os.path.join(BASE_FOLDER, "FUNDING_SS.JPG")
_las_img(excel_path, image_path)



# ==================================================
# STEP 9: CREATE CLIENT-SPECIFIC FILTER FILE (6NBB707)
# ==================================================

client_code = "6NBB707"

client_output = os.path.join(
    BASE_FOLDER, f"FILTERED_{client_code}.xlsx"
)

# Filter from already prepared df_filtered
client_df = df_filtered[
    df_filtered["TradingCode"].astype(str).str.upper() == client_code
]

if not client_df.empty:
    client_df.to_excel(client_output, index=False)
    print(f"✅ Client file created successfully: {client_output}")
else:
    print(f"⚠️ No data found for TradingCode = {client_code}. File not created.")


# ==================================================
# STEP 9.1: FORMAT CLIENT FILE (6NBB707)
# ==================================================

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

file_path = os.path.join(
    BASE_FOLDER, "FILTERED_6NBB707.xlsx"
)

# ---- Safety check ----
if not os.path.exists(file_path):
    print("⚠️ FILTERED_6NBB707.xlsx not found, skipping formatting.")
else:
    wb = load_workbook(file_path)
    ws = wb.active

    title_text = "Scenario Analysis LAS Account (in lac.)-6NBB"

    # Insert title row
    ws.insert_rows(1)
    ws["A1"] = title_text

    max_col = ws.max_column
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)

    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].fill = PatternFill(
        start_color="ADD8E6",
        end_color="ADD8E6",
        fill_type="solid"
    )
    ws["A1"].alignment = Alignment(
        horizontal="center",
        vertical="center",
        wrap_text=True
    )

    # Header styling (row 2)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin")
    )

    for col in range(1, max_col + 1):
        cell = ws.cell(row=2, column=col)
        cell.font = Font(bold=True)
        cell.border = thin_border
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True
        )

    # Alignment rules
    text_cols = ["ZONE", "TradingCode", "client_name"]

    for row in ws.iter_rows(min_row=3, max_row=ws.max_row, max_col=max_col):
        for cell in row:
            col_name = ws.cell(row=2, column=cell.column).value
            if col_name in text_cols:
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            else:
                cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)

    # Widen client_name column
    ws.column_dimensions["C"].width = 25

    wb.save(file_path)

    print(f"✅ FILTERED_6NBB707.xlsx formatted successfully: {file_path}")


# ==================================================
# STEP 9.2: SCREENSHOT CLIENT FILE (6NBB707)
# ==================================================

excel_path = os.path.join(BASE_FOLDER, "FILTERED_6NBB707.xlsx")
image_path = os.path.join(BASE_FOLDER, "6NBB.JPG")
_las_img(excel_path, image_path)


# ==================================================
# STEP 10: CREATE SECT_CODE FILE (MULTIPLE CLIENT CODES)
# ==================================================

sect_codes = ["SN343", "SN120", "PCG011"]

sect_output = os.path.join(
    BASE_FOLDER, "SECT_CODE.xlsx"
)

sect_df = df_filtered[
    df_filtered["TradingCode"]
    .astype(str)
    .str.upper()
    .isin(sect_codes)
]

if not sect_df.empty:
    sect_df.to_excel(sect_output, index=False)
    print(f"✅ SECT_CODE file created successfully: {sect_output}")
else:
    print("⚠️ No matching TradingCode found for SECT_CODE.xlsx")



# ==================================================
# STEP 11: FORMAT SECT_CODE EXCEL FILE
# ==================================================

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

sect_file_path = os.path.join(BASE_FOLDER, "SECT_CODE.xlsx")

if not os.path.exists(sect_file_path):
    print("⚠️ SECT_CODE.xlsx not found — skipping formatting.")

else:
    wb = load_workbook(sect_file_path)
    ws = wb.active

    # --------------------------------------------------
    # 11.1 Insert title row
    # --------------------------------------------------
    title_text = "Scenario Analysis LAS Account (in lac.) (NONSALES)"

    ws.insert_rows(1)
    ws["A1"] = title_text

    max_col = ws.max_column

    ws.merge_cells(
        start_row=1,
        start_column=1,
        end_row=1,
        end_column=max_col
    )

    ws["A1"].font = Font(
        bold=True,
        size=14
    )

    ws["A1"].fill = PatternFill(
        start_color="ADD8E6",
        end_color="ADD8E6",
        fill_type="solid"
    )

    ws["A1"].alignment = Alignment(
        horizontal="center",
        vertical="center",
        wrap_text=True
    )

    # --------------------------------------------------
    # 11.2 Style header row
    # --------------------------------------------------
    header_row = 2

    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin")
    )

    for col in range(1, max_col + 1):
        cell = ws.cell(
            row=header_row,
            column=col
        )

        cell.font = Font(bold=True)
        cell.border = thin_border

        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True
        )

    # --------------------------------------------------
    # 11.3 Align data rows
    # --------------------------------------------------
    text_cols = [
        "ZONE",
        "TradingCode",
        "client_name"
    ]

    for row in ws.iter_rows(
        min_row=3,
        max_row=ws.max_row,
        max_col=max_col
    ):
        for cell in row:

            col_name = ws.cell(
                row=2,
                column=cell.column
            ).value

            if col_name in text_cols:
                cell.alignment = Alignment(
                    horizontal="left",
                    vertical="center",
                    wrap_text=True
                )

            else:
                cell.alignment = Alignment(
                    horizontal="right",
                    vertical="center",
                    wrap_text=True
                )

    # --------------------------------------------------
    # 11.4 Adjust column widths
    # --------------------------------------------------
    ws.column_dimensions["C"].width = 25

    # --------------------------------------------------
    # 11.5 Save workbook
    # --------------------------------------------------
    wb.save(sect_file_path)

    print(
        f"✅ SECT_CODE.xlsx formatted successfully: {sect_file_path}"
    )


# ==================================================
# STEP 11.1 : RISK ASSESSMENT FINAL FILE (V1 + NONSALES + V2)
# ==================================================

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from datetime import datetime
import os

print("📊 Creating Risk Assessment Final File")

today = datetime.today().strftime("%d%m%Y")
today_date = datetime.today().strftime("%d-%m-%Y")

# ---------- FILE PATHS (STRICT BASE_FOLDER) ----------
file_v1   = os.path.join(BASE_FOLDER, "vertical_1.xlsx")
file_v2   = os.path.join(BASE_FOLDER, "VERTICAL_2.xlsx")
file_sect = os.path.join(BASE_FOLDER, "SECT_CODE.xlsx")

output_file = os.path.join(
    BASE_FOLDER,
    f"Risk_Assessment_File_Final-{today}.xlsx"
)

# ---------- SAFETY CHECK ----------
missing_files = []

if not os.path.exists(file_v1):
    print("⚠️ vertical_1 missing — skipping V1 block")
else:
    missing_files.append("V1")

if not os.path.exists(file_v2):
    print("⚠️ vertical_2 missing — skipping V2 block")
else:
    missing_files.append("V2")

if not os.path.exists(file_sect):
    print("⚠️ SECT_CODE missing — skipping NONSALES block")
else:
    missing_files.append("NONSALES")
# ---------- FIXED LAS & ROI MAPPING ----------
fixed_values = {
    "V1": {
        "6NBB707": ("LAS 1:1", 9),
        "6KNH03": ("LAS 1:1", 14),
        "6KNH08": ("Funding On Stock", 14),
        "BCBY01": ("Funding On Stock", 18),
        "6KZZ62294": ("LAS 1:1", 12),
        "6KIA01": ("LAS 1:1", 14),
        "NDTT46839": ("LAS 1:1", 15),
        "6KAA167": ("Funding On Stock", 18),
        "ROHN2176": ("LAS 1:1", 15),
    },
    "NONSALES": {
        "SN343": ("LAS 1:1", 12),
        "SN120": ("LAS 1:1", 10),
        "PCG011": ("LAS 1:1", 9),
    },
    "V2": {
        "8LAA2089": ("LAS 1:1", 16),
        "8HAC10004": ("LAS 1:2", 18),
        "8HAA2090": ("LAS 1:2", 18),
    }
}

# ---------- READ FILES (HEADER AT ROW 2) ----------
df_v1 = pd.read_excel(file_v1, dtype=str, header=1) if os.path.exists(file_v1) else pd.DataFrame()
df_v2 = pd.read_excel(file_v2, dtype=str, header=1) if os.path.exists(file_v2) else pd.DataFrame()
df_sect = pd.read_excel(file_sect, dtype=str, header=1) if os.path.exists(file_sect) else pd.DataFrame()


def normalize(df):
    if df.empty:
        return df

    df.columns = df.columns.astype(str) \
        .str.strip() \
        .str.replace(" ", "", regex=False) \
        .str.upper()

    return df

df_v1   = normalize(df_v1)
df_v2   = normalize(df_v2)
df_sect = normalize(df_sect)

# ---------- NUMERIC CONVERSION ----------
# pd.to_numeric(..., errors="ignore") was removed in newer pandas (this
# machine's fresh `pip install` pulled a version where it no longer works —
# raises "ValueError: invalid error value specified" instead of silently
# leaving non-numeric columns alone like it used to). try/except reproduces
# the exact old behavior: convert if the WHOLE column parses as numeric,
# otherwise leave that column completely untouched.
for df in [df_v1, df_v2, df_sect]:
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col])
        except (ValueError, TypeError):
            pass

# ---------- FINAL COLUMN ORDER ----------
cols = [
    "DATE","ZONE","TRADINGCODE","CLIENT_NAME","LASFUNDING",
    "DEPOSIT(OWNCLIENTFUND)","DEPOSIT(AFTERADJUSTMENTSHORTNOV)",
    "MTM.","TOTALMARGINUTILL",
    "UP_2%","DOWN_2%","UP_4%","DOWN_4%","UP_6%","DOWN_6%",
    "LAS_RATIO","ROI","30%VALUE","RISKASSESS"
]

# ---------- APPLY LAS / ROI ----------
def apply_fixed(block, key):
    # fixed_values holds a MIX of numbers and text labels (e.g. "LAS 1:2") per
    # code. If the earlier numeric-conversion pass happened to turn LAS_RATIO/
    # ROI fully float64 (e.g. all-blank at that point), writing a text label
    # into a single cell used to silently widen the whole column to object —
    # newer pandas raises TypeError("Invalid value ... for dtype 'float64'")
    # instead. Force both columns to object dtype up front so they can hold
    # whatever fixed_values actually has, exactly like the old behavior did.
    block["LAS_RATIO"] = block["LAS_RATIO"].astype(object)
    block["ROI"] = block["ROI"].astype(object)
    for i, r in block.iterrows():
        code = r["TRADINGCODE"]
        if code in fixed_values.get(key, {}):
            block.at[i, "LAS_RATIO"] = fixed_values[key][code][0]
            block.at[i, "ROI"] = fixed_values[key][code][1]
    return block

# ---------- BUILD SECTION ----------
def make_block(df, title, key):
    block = df.reindex(columns=cols)
    block["DATE"] = today_date

    block["30%VALUE"] = block["DEPOSIT(AFTERADJUSTMENTSHORTNOV)"].apply(
        lambda x: round(0.3 * x, 2) if pd.notna(x) else 0
    )

    def risk(row):
        for c in ["UP_2%","DOWN_2%","UP_4%","DOWN_4%","UP_6%","DOWN_6%"]:
            if row[c] < 0 and abs(row[c]) > row["30%VALUE"]:
                return "Yes"
        return "No"

    block["RISKASSESS"] = block.apply(risk, axis=1)
    block = apply_fixed(block, key)

    total = {c: "" for c in cols}
    total["TRADINGCODE"] = "Total"
    for c in block.select_dtypes(include="number").columns:
        total[c] = block[c].sum()

    block = pd.concat([block, pd.DataFrame([total])], ignore_index=True)
    return title, block

sections = []

if not df_v1.empty:
    sections.append(make_block(df_v1, "Scenario Analysis LAS Account (in lac.) (V1)", "V1"))

if not df_sect.empty:
    sections.append(make_block(df_sect, "Scenario Analysis LAS Account (in lac.) (NONSALES)", "NONSALES"))

if not df_v2.empty:
    sections.append(make_block(df_v2, "Scenario Analysis LAS Account (in lac.) (V2)", "V2"))


# ---------- WRITE EXCEL ----------
with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    row = 0
    for title, block in sections:
        pd.DataFrame([[title]]).to_excel(
            writer, index=False, header=False,
            startrow=row, sheet_name="ALL ACCOUNTS-DELHI"
        )
        row += 1
        block.to_excel(writer, index=False, startrow=row, sheet_name="ALL ACCOUNTS-DELHI")
        row += len(block) + 2

# ---------- FORMAT ----------
wb = load_workbook(output_file)
ws = wb.active
max_col = ws.max_column

red_fill = PatternFill("solid", fgColor="FF6347")

for r in ws.iter_rows():
    if r[0].value and "Scenario Analysis" in str(r[0].value):
        ws.merge_cells(start_row=r[0].row, start_column=1, end_row=r[0].row, end_column=max_col)
        r[0].alignment = Alignment(horizontal="center")
        r[0].font = Font(bold=True)

# Highlight risk = Yes
header_row = 2
risk_col = None
for c in range(1, max_col+1):
    if ws.cell(row=header_row, column=c).value == "RISKASSESS":
        risk_col = c

if risk_col:
    for r in ws.iter_rows(min_row=header_row+1):
        if r[risk_col-1].value == "Yes":
            r[risk_col-1].fill = red_fill

wb.save(output_file)

print(f"✅ Risk Assessment file created successfully: {output_file}")



if _ckpt.las_should_run("ZONE_PROCESS"):
    _ckpt.las_step("ZONE_PROCESS", script="LAS4")

# ==================================================
# STEP 12: ZONE-WISE FILE CREATION (AS PER YOUR SHARED LOGIC)
# ==================================================

import pandas as pd
import os

print("📁 Creating zone-wise files (as per original logic)")

zone_output_folder = os.path.join(BASE_FOLDER, "ZONEWISE")
os.makedirs(zone_output_folder, exist_ok=True)

# IMPORTANT:
# Using FINAL OUTPUT (NOT df_filtered, NOT scaled data)
df_zone_base = final_df.copy()

# Ensure numeric for filtering & sorting
df_zone_base["Total Margin Utill"] = pd.to_numeric(
    df_zone_base["Total Margin Utill"], errors="coerce"
).fillna(0)

# 1️⃣ Filter Total Margin Utill > 0  ✅ (AS IN YOUR SHARED CODE)
df_zone_base = df_zone_base[df_zone_base["Total Margin Utill"] > 0]

# 2️⃣ Sort by Total Margin Utill descending
df_zone_base = df_zone_base.sort_values(
    by="Total Margin Utill",
    ascending=False
)

# 3️⃣ Filter only required zones
zones_to_keep = ["CHND", "LUDH", "MUMB"]
df_zone_base = df_zone_base[df_zone_base["ZONE"].isin(zones_to_keep)]

# 4️⃣ Drop unwanted columns (exactly as shared)
cols_to_drop = ["NOV","Total value of Collateral+fund"]
df_zone_base = df_zone_base.drop(columns=cols_to_drop, errors="ignore")

# 5️⃣ Save zone-wise files
for zone in zones_to_keep:
    zone_df = df_zone_base[df_zone_base["ZONE"] == zone]

    if not zone_df.empty:
        zone_file = os.path.join(
            zone_output_folder,
            f"LAS_FUNDING_{zone}.xlsx"
        )
        zone_df.to_excel(zone_file, index=False)
        print(f"✅ Zone file created: {zone_file}")
    else:
        print(f"⚠️ No data for zone: {zone}")

# ==================================================
# STEP 13: SCALE ZONEWISE FILES BY 1,00,000
# ==================================================

print("📐 Scaling zone-wise files (in lacs)")

zone_output_folder = os.path.join(BASE_FOLDER, "ZONEWISE")

zones_to_scale = ["CHND", "LUDH", "MUMB"]

cols_to_scale = [
    "LAS FUNDING",
    "Deposit(Own Client Fund)",
    "Deposit (After adjustment Short NOV)",
    "MTM.",
    "Total Margin Utill",
    "UP_2%",
    "DOWN_2%",
    "UP_4%",
    "DOWN_4%",
    "UP_6%",
    "DOWN_6%"
]

for zone in zones_to_scale:
    zone_file = os.path.join(
        zone_output_folder,
        f"LAS_FUNDING_{zone}.xlsx"
    )

    if not os.path.exists(zone_file):
        print(f"⚠️ Zone file not found, skipping: {zone_file}")
        continue

    df_zone = pd.read_excel(zone_file)

    for col in cols_to_scale:
        if col in df_zone.columns:
            df_zone[col] = pd.to_numeric(
                df_zone[col]
                .astype(str)
                .str.replace(",", "")
                .str.strip(),
                errors="coerce"
            ).fillna(0)

            # Always divide by 1,00,000
            df_zone[col] = (df_zone[col] / 100000).round(2)

    df_zone.to_excel(
        zone_file,
        index=False,
        float_format="%.2f"
    )

    print(f"✅ Zone file scaled successfully: {zone_file}")

# ==================================================
# STEP 13.1: FORMAT ZONEWISE FILES (TITLE + STYLE)
# ==================================================

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

zone_folder = os.path.join(BASE_FOLDER, "ZONEWISE")

zones = {
    "CHND": "Scenario Analysis LAS Account (in lac.)-CHND",
    "LUDH": "Scenario Analysis LAS Account (in lac.)-LUDH",
    "MUMB": "Scenario Analysis LAS Account (in lac.)-MUMB",
}

for zone, title_text in zones.items():
    file_path = os.path.join(zone_folder, f"LAS_FUNDING_{zone}.xlsx")

    if not os.path.exists(file_path):
        print(f"⚠️ Zone file missing, skipping formatting: {file_path}")
        continue

    wb = load_workbook(file_path)
    ws = wb.active

    # Insert title
    ws.insert_rows(1)
    ws["A1"] = title_text

    max_col = ws.max_column
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)

    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].fill = PatternFill(
        start_color="ADD8E6",
        end_color="ADD8E6",
        fill_type="solid"
    )
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Header styling (row 2)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin")
    )

    for col in range(1, max_col + 1):
        cell = ws.cell(row=2, column=col)
        cell.font = Font(bold=True)
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Alignment rules
    text_cols = ["ZONE", "TradingCode", "client_name"]

    for row in ws.iter_rows(min_row=3, max_row=ws.max_row, max_col=max_col):
        for cell in row:
            col_name = ws.cell(row=2, column=cell.column).value
            if col_name in text_cols:
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            else:
                cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)

    # Widen client_name column
    ws.column_dimensions["C"].width = 25

    wb.save(file_path)
    print(f"✅ Zone file formatted: {file_path}")


# ==================================================
# STEP 13.3: SCREENSHOT EXPORT – LUDH ZONE
# ==================================================

print("📸 Taking screenshot for LUDH zone")
excel_path = os.path.join(BASE_FOLDER, "ZONEWISE", "LAS_FUNDING_LUDH.xlsx")
image_path = os.path.join(BASE_FOLDER, "LUDH_SS.JPG")
_las_img(excel_path, image_path)



if _ckpt.las_should_run("RISK_NOV"):
    _ckpt.las_step("RISK_NOV", script="LAS4")

# ==================================================
# STEP 14: MERGE FILTERED OUTPUT + ZONE FILES
# ==================================================

import sys

print("🧩 Merging filtered output with zone-wise files")

# ---- Paths (BASE_FOLDER only) ----
zone_folder = os.path.join(BASE_FOLDER, "ZONEWISE")

file_chnd = os.path.join(zone_folder, "LAS_FUNDING_CHND.xlsx")
file_ludh = os.path.join(zone_folder, "LAS_FUNDING_LUDH.xlsx")
file_mumb = os.path.join(zone_folder, "LAS_FUNDING_MUMB.xlsx")

filtered_file = os.path.join(BASE_FOLDER, "LAS_FUNDING_OUTPUT_FILTERED.xlsx")
merged_file = os.path.join(BASE_FOLDER, "LAS_FUNDING_MERGED.xlsx")

# ---- Ensure filtered file exists ----
if not os.path.exists(filtered_file):
    print(f"❌ Missing base filtered file: {filtered_file}")
    sys.exit(1)

# ---- Load existing filtered data ----
print(f"📂 Loading filtered file: {filtered_file}")
existing_df = pd.read_excel(filtered_file, dtype=str, header=1)
existing_df = existing_df.dropna(how="all")

# ---- Cleaner function for formatted Excel ----
def clean_excel(file_path):
    try:
        df_full = pd.read_excel(file_path, dtype=str, header=None)
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return pd.DataFrame()

    header_row_idx = None
    for i, row in df_full.iterrows():
        if (
            row.astype(str).str.contains("ZONE", case=False).any()
            and row.astype(str).str.contains("Trading", case=False).any()
        ):
            header_row_idx = i
            break

    if header_row_idx is None:
        print(f"⚠️ Header not found in {file_path}, skipping.")
        return pd.DataFrame()

    headers = df_full.iloc[header_row_idx].fillna("").tolist()
    df = pd.DataFrame(
        df_full.iloc[header_row_idx + 1:].values,
        columns=headers
    )

    # Remove duplicate header rows inside data
    if "TradingCode" in df.columns:
        df = df[
            ~df["TradingCode"]
            .astype(str)
            .str.contains("Trading", case=False, na=False)
        ]

    return df.dropna(how="all")

# ---- Files to merge ----
zone_files = [file_chnd, file_ludh, file_mumb]

merged_data = [existing_df]

for f in zone_files:
    if os.path.exists(f):
        print(f"✅ Cleaning & merging: {os.path.basename(f)}")
        df_zone = clean_excel(f)
        if not df_zone.empty:
            merged_data.append(df_zone)
    else:
        print(f"⚠️ Zone file missing, skipped: {f}")

# ---- Final merge ----
final_merged_df = pd.concat(merged_data, ignore_index=True)

# ---- Save to NEW file ----
final_merged_df.to_excel(merged_file, index=False)

print(f"\n🎉 FINAL MERGED FILE CREATED: {merged_file}")


# ==================================================
# STEP 14.1 : SCENARIO ANALYSIS LAS ACCOUNT (DAY NET PREMIUM)
# ==================================================


print("📊 Creating Scenario Analysis LAS Account file")

today = datetime.today().strftime("%d%m%Y")

# ---------- FILE PATHS (STRICT BASE_FOLDER) ----------
merged_file = os.path.join(
    BASE_FOLDER,
    "LAS_FUNDING_MERGED.xlsx"
)

main_file = os.path.join(
    BASE_FOLDER,
    "MAIN.csv"
)

final_file = os.path.join(
    BASE_FOLDER,
    "Scenario Analysis Las Account.xlsx"
)

# ---------- SAFETY CHECK ----------
for f in [merged_file, main_file]:
    if not os.path.exists(f):
        raise FileNotFoundError(f"❌ Required file missing: {f}")

# ---------- LOAD FILES ----------
print("📂 Loading merged LAS file...")
df_merged = pd.read_excel(merged_file, dtype=str)

print("📂 Loading MAIN.csv...")
df_main = pd.read_csv(main_file, dtype=str)

# ---------- REQUIRED COLUMN CHECK ----------
required_merged = {"TradingCode"}
required_main   = {"ClientID", "DayNetPremium"}

if not required_merged.issubset(df_merged.columns):
    raise KeyError("❌ TradingCode missing in merged file")

if not required_main.issubset(df_main.columns):
    raise KeyError("❌ ClientID / DayNetPremium missing in MAIN.csv")

# ---------- MERGE DayNetPremium ----------
df_main_sub = df_main[["ClientID", "DayNetPremium"]].copy()

df_final = df_merged.merge(
    df_main_sub,
    left_on="TradingCode",
    right_on="ClientID",
    how="left"
)

df_final.drop(columns=["ClientID"], inplace=True, errors="ignore")

# ---------- COLUMN NORMALIZATION ----------
if "Total Margin Utill" in df_final.columns:
    df_final.rename(
        columns={"Total Margin Utill": "Margin Utill"},
        inplace=True
    )

# ---------- NUMERIC CONVERSION ----------
num_cols = [
    "LAS FUNDING",
    "Deposit(Own Client Fund)",
    "Deposit (After adjustment Short NOV)",
    "MTM.",
    "DayNetPremium",
    "Margin Utill"
]

for col in num_cols:
    if col in df_final.columns:
        df_final[col] = pd.to_numeric(
            df_final[col],
            errors="coerce"
        ).fillna(0)

# ---------- SCALE DayNetPremium (÷1,00,000) ----------
df_final["DayNetPremium"] = (
    df_final["DayNetPremium"] / 100000
).round(2)

# ---------- ADJUSTED FORMULA ----------
# Adjusted = IF(DayNetPremium > 0,
#               Deposit (After adjustment Short NOV) - DayNetPremium,
#               0)

df_final["Adjusted"] = df_final.apply(
    lambda r: r["Deposit (After adjustment Short NOV)"] - r["DayNetPremium"]
    if r["DayNetPremium"] > 0 else 0,
    axis=1
)

# ---------- FINAL COLUMN ORDER ----------
final_columns = [
    "ZONE",
    "TradingCode",
    "client_name",
    "LAS FUNDING",
    "Deposit(Own Client Fund)",
    "Deposit (After adjustment Short NOV)",
    "MTM.",
    "DayNetPremium",
    "Margin Utill",
    "UP_2%",
    "DOWN_2%",
    "UP_4%",
    "DOWN_4%",
    "Adjusted"
]

df_final = df_final[
    [c for c in final_columns if c in df_final.columns]
]

# ---------- SORT ----------
if "Margin Utill" in df_final.columns:
    df_final = df_final.sort_values(
        by="Margin Utill",
        ascending=False
    )

# ---------- SAVE ----------
df_final.to_excel(final_file, index=False)

print(f"✅ Scenario Analysis LAS Account created: {final_file}")

# ==================================================
# STEP 14.1.1 : CREATE SCENARIO ANALYSIS EXPIRY CLIENT FILE
# ==================================================

print("📊 Creating Scenario Analysis Las Account Expiry Client file")

merged_file = os.path.join(BASE_FOLDER, "LAS_FUNDING_MERGED.xlsx")
main_file   = os.path.join(BASE_FOLDER, "MAIN.csv")
final_file  = os.path.join(
    BASE_FOLDER,
    "Scenario Analysis Las Account  Expiry_Client.xlsx"
)

# ---------- LOAD FILES ----------
df_merged = pd.read_excel(merged_file, dtype=str)
df_main   = pd.read_csv(main_file, dtype=str)

# ---------- VALIDATION ----------
required_main = {"ClientID", "DayNetPremium", "T1MarginUtil"}
if not required_main.issubset(df_main.columns):
    raise KeyError(f"❌ Missing columns in MAIN.csv: {required_main - set(df_main.columns)}")

# ---------- MERGE ----------
df_main_subset = df_main[["ClientID", "DayNetPremium", "T1MarginUtil"]]

df_expiry = df_merged.merge(
    df_main_subset,
    left_on="TradingCode",
    right_on="ClientID",
    how="left"
)

df_expiry.drop(columns=["ClientID"], inplace=True, errors="ignore")

# ---------- RENAME ----------
if "Total Margin Utill" in df_expiry.columns:
    df_expiry.rename(columns={"Total Margin Utill": "Margin Utill"}, inplace=True)

# ---------- NUMERIC ----------
num_cols = [
    "LAS FUNDING",
    "Deposit(Own Client Fund)",
    "Deposit (After adjustment Short NOV)",
    "MTM.",
    "DayNetPremium",
    "T1MarginUtil",
    "Margin Utill"
]

for col in num_cols:
    if col in df_expiry.columns:
        df_expiry[col] = pd.to_numeric(df_expiry[col], errors="coerce").fillna(0)

# ---------- SCALING ----------
df_expiry["DayNetPremium"] = (df_expiry["DayNetPremium"] / 100000).round(2)
df_expiry["T1MarginUtil"]  = (df_expiry["T1MarginUtil"]  / 100000).round(2)

# ---------- ADJUSTED ----------
df_expiry["Adjusted"] = df_expiry.apply(
    lambda r: r["Deposit (After adjustment Short NOV)"] - r["DayNetPremium"]
    if r["DayNetPremium"] > 0 else 0,
    axis=1
)

# ---------- FINAL ORDER ----------
final_columns = [
    "ZONE", "TradingCode", "client_name", "LAS FUNDING",
    "Deposit(Own Client Fund)", "Deposit (After adjustment Short NOV)",
    "MTM.", "DayNetPremium", "T1MarginUtil",
    "Margin Utill", "UP_2%", "DOWN_2%", "UP_4%", "DOWN_4%", "Adjusted"
]

df_expiry = df_expiry[[c for c in final_columns if c in df_expiry.columns]]

# ---------- SORT & SAVE ----------
df_expiry = df_expiry.sort_values(by="Margin Utill", ascending=False)
df_expiry.to_excel(final_file, index=False)

print(f"✅ Expiry Client file CREATED CORRECTLY: {final_file}")


# ==================================================
# FINAL PASSWORD PROTECTION (DATE-SUFFIX FILES)
# ==================================================

import win32com.client as win32
import os
import time
from datetime import datetime

def password_protect_excel_force(input_file):
    today = datetime.today().strftime("%d%m%Y")
    password = f"Scenario@{today}"

    input_file = os.path.abspath(input_file)

    base, ext = os.path.splitext(input_file)
    dated_file = f"{base}_{today}{ext}"

    
    # 🔴 Force NEW Excel instance
    excel = win32.DispatchEx("Excel.Application")
    excel.DisplayAlerts = False
    excel.Visible = False

    # 🔴 Open original file
    wb = excel.Workbooks.Open(
        input_file,
        False,   # UpdateLinks
        False    # ReadOnly = False
    )

    # 🔴 Save NEW dated file WITH PASSWORD
    wb.SaveAs(
        dated_file,
        FileFormat=51,   # XLSX
        Password=password
    )

    wb.Close(False)
    excel.Quit()

    print(f"🔐 PASSWORD APPLIED → {os.path.basename(dated_file)}")
    print(f"🔑 Password: {password}")

    return dated_file


# ==================================================
# APPLY PASSWORD TO FINAL FILES (LAST STEP ONLY)
# ==================================================

password_protect_excel_force(
    os.path.join(BASE_FOLDER, "Scenario Analysis Las Account.xlsx")
)

password_protect_excel_force(
    os.path.join(BASE_FOLDER, "Scenario Analysis Las Account  Expiry_Client.xlsx")
)




if _ckpt.las_should_run("WA_IMAGES"):
    _ckpt.las_step("WA_IMAGES", script="LAS4")

# ==================================================
# STEP 15: SCREENSHOT EXPORT FOR WHATSAPP / MAIL
# ==================================================

zone_folder = os.path.join(BASE_FOLDER, "ZONEWISE")

zones = {
    "CHND": "CHND_SS.JPG",
    "LUDH": "LUDH_SS.JPG",
    "MUMB": "MUMB_SS.JPG"
}

for zone, image_name in zones.items():
    excel_path = os.path.join(zone_folder, f"LAS_FUNDING_{zone}.xlsx")
    image_path = os.path.join(BASE_FOLDER, image_name)
    _las_img(excel_path, image_path)


if _ckpt.las_should_run("WA_SEND"):
    _ckpt.las_step("WA_SEND", script="LAS4")

# ==================================================
# STEP 16: WHATSAPP IMAGE SENDING (SINGLE CODE)
# ==================================================

import requests
import os
from datetime import datetime

print("📲 Starting WhatsApp sending module")

# --------------------------------------------------
# AUTH & URL CONFIG
# --------------------------------------------------
AUTH_TOKEN = _SAENV("SA_WA_TOKEN", "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJwaG9uZU51bWJlciI6Iis5MTg1OTU5MDkyOTQiLCJwaG9uZU51bWJlcklkIjoiNTYzNjExMDA2ODQzNzQ2IiwiaWF0IjoxNzYzNDYxMzExfQ.jUNJ_lDPJtueMU4vXMYy_JpjdV0cpKlE_2Rgv9qztPeKbCCUV3sIslRSGV5scu4ZiUpxv6rIICnvrJ7fzcLkdS3LVHlNFWFCTcP4aJLMccEf3z4X1MpaphoVVhuPufs5ouMHzRHVIjOpDSf0psZJi_dmUelui3jEPq7DE-nKLS0YFBBCNqcMDlP4Vts4WFoQzG9ssLC7WWsd0Ae_AXc0e0r3TPR-V0x14bOChW88uXsMmRCnW4RFOeguUO6NDmhK1ie-gbqk7Ov6w59CZU5wtQ1K2TNe5feM0Q0g8KUzQPIz7LfEEpxyumZplvCYZqBhsSvAm7-rpdVkvktlkH_2GlWPBAqiic9pDpGXvTOb0tZBFSqpE28fC9Wb-fh_YmHJC9GjJNWl8bz00-8P0HMtpwF28uL2uPpmAUca9CAXyzLSXP9eQFCYfz_PwfHsiGQrBpxrSAgou0VDsUCa7ofT9PgMQfMyHg26f0B-eZuxueYFJPHr1uohMuw7X2mapwioXWDvB-S1NkzoQt0vOsyXW6RtghXAeWJi7gOcVpgLEuBHfYNiXWMWrJ-odi3eU0eLQfUT9qD8049rH1GFgKLLEDMIHYsoExhfEFRCdaM8uAV222ib-bqB_9sX7E-tteCjIg8ntStslw9H_qU74ouM_CQR5thb47oLesp4BD60bZs")

UPLOAD_URL = _SAENV("SA_WA_UPLOAD_URL", "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/media")
SEND_URL = _SAENV("SA_WA_SEND_URL", "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/messages")

TODAY = datetime.now().strftime("%d-%m-%Y")

SEND_MAP = {
    "DELHI": {
        "image": os.path.join(BASE_FOLDER, "FUNDING_SS.JPG"),
        "title": "LAS FUNDING DELHI ZONE",
        "recipients": _SALIST("SA_WA_DELHI", ["+917889026088"])
    },
    "CHND": {
        "image": os.path.join(BASE_FOLDER, "CHND_SS.JPG"),
        "title": "LAS FUNDING CHANDIGARH",
        "recipients": _SALIST("SA_WA_CHND", ["+917889026088"])
    },
    "LUDH": {
        "image": os.path.join(BASE_FOLDER, "LUDH_SS.JPG"),
        "title": "LAS FUNDING LUDHIANA",
        "recipients": _SALIST("SA_WA_LUDH", ["+917889026088"])
    },
    "MUMB": {
        "image": os.path.join(BASE_FOLDER, "MUMB_SS.JPG"),
        "title": "LAS FUNDING MUMBAI",
        "recipients": _SALIST("SA_WA_MUMB", ["+917889026088"])
    },
    "V1": {
        "image": os.path.join(BASE_FOLDER, "VERTICAL_1_SS.JPG"),
        "title": "LAS FUNDING V1",
        "recipients": _SALIST("SA_WA_V1", ["+917889026088"])
    },
    "V2": {
        "image": os.path.join(BASE_FOLDER, "VERTICAL_2_SS.JPG"),
        "title": "LAS FUNDING V2",
        "recipients": _SALIST("SA_WA_V2", ["+917889026088"])
    },
    "6NBB707": {
        "image": os.path.join(BASE_FOLDER, "6NBB.JPG"),
        "title": "LAS FUNDING 6NBB707",
        "recipients": _SALIST("SA_WA_6NBB707", ["+917889026088"])
                }
}



# --------------------------------------------------
# FUNCTION: UPLOAD IMAGE
# --------------------------------------------------
import time
import requests

def upload_image(image_path):
    attempt = 0

    while True:  # 🔁 INFINITE RETRY
        attempt += 1
        try:
            print(f"⏳ Uploading image (attempt {attempt}) → {os.path.basename(image_path)}")

            with open(image_path, "rb") as f:
                files = {"file": ("report.jpg", f, "image/jpeg")}
                headers = {
                    "Authorization": f"Bearer {AUTH_TOKEN}",
                    "accept": "application/json"
                }

                r = requests.post(
                    UPLOAD_URL,
                    headers=headers,
                    files=files,
                    timeout=30  # 🔒 IMPORTANT
                )

            if r.status_code in (200, 201):
                media_id = r.json().get("id") or r.json().get("mediaHandle")
                print("✅ Upload successful")
                return media_id

            print(f"⚠️ Upload failed (status {r.status_code}). Retrying...")

        except requests.exceptions.Timeout:
            print("⏱️ Server timeout. Retrying...")

        except requests.exceptions.ConnectionError:
            print("🌐 Server not reachable. Retrying...")

        except requests.exceptions.RequestException as e:
            print(f"⚠️ Network error: {e}. Retrying...")

        time.sleep(5)  # 🧊 cool-down before next retry

# --------------------------------------------------
# FUNCTION: SEND WHATSAPP MESSAGE
# --------------------------------------------------
def send_whatsapp(media_id, title, recipients):
    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json"
    }

    for phone in recipients:
        payload = {
            "to": phone,
            "type": "template",
            "source": "external",
            "template": {
                "name": "daily_report",
                "language": {"code": "en"},
                "components": [
                    {
                        "type": "header",
                        "parameters": [{
                            "type": "image",
                            "image": {"id": media_id}
                        }]
                    },
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": title},
                            {"type": "text", "text": TODAY}
                        ]
                    }
                ]
            }
        }

        r = requests.post(SEND_URL, headers=headers, json=payload)

        if r.status_code == 200:
            print(f"✅ Sent to {phone}")
        else:
            print(f"❌ Failed for {phone}: {r.text}")

# --------------------------------------------------
# MAIN LOOP
# --------------------------------------------------
_wa_manifest = {}
for zone, cfg in SEND_MAP.items():
    print(f"\n📤 Processing WhatsApp for {zone}")

    _img_exists = os.path.exists(cfg["image"])
    _wa_manifest[zone] = {
        "image":      cfg["image"],
        "image_name": os.path.basename(cfg["image"]),
        "title":      cfg["title"],
        "recipients": cfg["recipients"],
        "sent":       False,
        "skipped":    not _img_exists,
    }

    if not _img_exists:
        print(f"⚠️ Image not found, skipping: {cfg['image']}")
        continue

    media_id = upload_image(cfg["image"])
    if not media_id:
        print("❌ Upload failed, skipping send")
        continue

    send_whatsapp(media_id, cfg["title"], cfg["recipients"])
    _wa_manifest[zone]["sent"] = True

# Save manifest so the dashboard can offer selective resend
try:
    import json as _waj
    _mf_path = os.path.join(BASE_FOLDER, ".las_wa_manifest.json")
    with open(_mf_path, "w", encoding="utf-8") as _mf:
        _waj.dump(_wa_manifest, _mf, indent=2)
    print(f"📝 WA manifest saved → {_mf_path}")
except Exception as _mae:
    print(f"⚠️ Could not save WA manifest: {_mae}")

_wa_zones_sent  = sum(1 for _z in _wa_manifest.values() if _z.get("sent"))
_wa_total_recip = sum(len(_z.get("recipients") or []) for _z in _wa_manifest.values() if _z.get("sent"))
print(f"\n✅ WhatsApp sent — {_wa_zones_sent} zone(s), {_wa_total_recip} number(s) — all messages processed")

if _ckpt.las_should_run("EMAILS"):
    _ckpt.las_step("EMAILS", script="LAS4")

# ==================================================
# FINAL STEP: ALL EMAILS (SCENARIO + RISK + TENTATIVE)
# ==================================================

print("📧 Starting ALL Email Modules")
import os
import time
import smtplib
import pandas as pd

from datetime import datetime

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders


# ==================================================
# COMMON DATE
# ==================================================
EMAIL_DATE = datetime.now().strftime("%d%m%Y")

# ==================================================
# COMMON SMTP CONFIG
# ==================================================
SMTP_SERVER = "smtp3.netcore.co.in"
SMTP_PORT = 587

EMAIL_ADDRESS = _SAENV("SA_SMTP_EMAIL", "Sandeep.kumar2@mastertrust.co.in")
EMAIL_PASSWORD = _SAENV("SA_SMTP_PASSWORD", "$Sandeep24*")



# ==================================================
# COMMON EMAIL SENDER (INFINITE RETRY)
# ==================================================
def send_email_infinite_retry(
    subject,
    body,
    to_recipients,
    cc_recipients,
    attachments,
    is_html=False
):
    while True:
        try:
            msg = MIMEMultipart()
            msg["From"] = EMAIL_ADDRESS
            msg["To"] = ", ".join(to_recipients)
            msg["Cc"] = ", ".join(cc_recipients)
            msg["Subject"] = subject

            msg.attach(MIMEText(body, "html" if is_html else "plain"))

            files_attached = 0
            for fp in attachments:
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header(
                            "Content-Disposition",
                            f'attachment; filename="{os.path.basename(fp)}"'
                        )
                        msg.attach(part)
                        files_attached += 1
                        print(f"📎 Attached: {fp}")
                else:
                    print(f"⚠️ Missing attachment: {fp}")

            # Only treat "0 files attached" as a problem worth retrying over
            # when the caller actually asked for attachments (a real file went
            # missing). An explicit attachments=[] call (e.g. the Tentative
            # Obligation email, sent deliberately without its attachment) must
            # not trip this guard, or it retries forever.
            if attachments and files_attached == 0:
                print("❌ No attachments — retrying in 3 seconds...")
                time.sleep(3)
                continue

            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.sendmail(
                    EMAIL_ADDRESS,
                    to_recipients + cc_recipients,
                    msg.as_string()
                )

            print(f"✅ Email sent successfully: {subject}")
            break

        except Exception as e:
            print(f"❌ Email error ({subject}): {e}")
            print("🔁 Retrying in 3 seconds...")
            time.sleep(3)


# ==================================================
# EMAIL 1: SCENARIO – ALL ZONE
# ==================================================
send_email_infinite_retry(
    subject="SCENARIO-ALL ZONE",
    body="""Dear Sir,

Please find above subjected file.

Best regards,
Sandeep Kumar
011-42111079
""",
    to_recipients=_SALIST("SA_MAIL_SCENARIO_ALL_TO", ["kartavya.dhillon@mastertrust.co.in"]),
    cc_recipients=_SALIST("SA_MAIL_SCENARIO_ALL_CC", ["kartavya.dhillon@mastertrust.co.in"]),
    attachments=[
        os.path.join(
            BASE_FOLDER,
            f"Scenario Analysis Las Account  Expiry_Client_{EMAIL_DATE}.xlsx"
        ),
        os.path.join(
            BASE_FOLDER,
            f"Scenario Analysis Las Account_{EMAIL_DATE}.xlsx"
        )
    ],
    is_html=False
)


# ==================================================
# EMAIL 2: RISK ASSESSMENT – DELHI
# ==================================================
send_email_infinite_retry(
    subject="SCENARIO ANALYSIS LAS ACCOUNT-DELHI ZONE",
    body="""Dear Bhumika,

Please find above subjected file.

Best regards,
Sandeep Kumar
011-42111079
""",
    to_recipients=_SALIST("SA_MAIL_DELHI_TO", ["kartavya.dhillon@mastertrust.co.in"]),
    cc_recipients=_SALIST("SA_MAIL_DELHI_CC", ["kartavya.dhillon@mastertrust.co.in"]),
    attachments=[
        os.path.join(
            BASE_FOLDER,
            f"Risk_Assessment_File_Final-{EMAIL_DATE}.xlsx"
        )
    ],
    is_html=False
)


# ==================================================
# EMAIL 3: TENTATIVE OBLIGATION SUMMARY — NARRATION.py-style, self-contained
# ==================================================
# Per explicit request: forget the old Product=='-' TOTAL-row summary-table
# email entirely and send the SAME dashboard-style email NARRATION.py (the
# user's own PC, D:\CLAUDE\NARRATION.py) already sends elsewhere — CASH /
# FUTURE / MCX PAYIN-PAYOUT narration table rendered as an inline PNG
# dashboard + a formatted, password-protected .xlsx attachment.
#
# IMPORTANT: this is now fully self-contained — NOT loaded from an external
# file path. The first version of this change imported NARRATION.py from a
# hardcoded D:\CLAUDE\NARRATION.py path, which only exists on the user's own
# PC; the very first run on a different machine (the console server, where
# this LAS4.py actually runs in production) failed with FileNotFoundError
# because that file isn't there. Every bit of NARRATION.py's logic this
# needs (build_sheet_df/compute_narration/write_excel/write_dashboard/the
# HTML email body) is reproduced inline in _las_narration_tentative_email()
# below instead, so this runs identically on any machine, no matter what
# else is or isn't on that machine's D: drive.

print("📊 Preparing Tentative Obligation Summary (NARRATION-style, self-contained)")

def _las_narration_tentative_email(df_main_raw):
    """Builds + sends the NARRATION.py-style Pay-In/Pay-Out dashboard email
    for the Tentative Obligation step — no external file dependency, no
    import of any other script. Password-protects the xlsx attachment with
    the same Scenario@<DDMMYYYY> scheme every other LAS4 file uses, and
    retries sending forever on SMTP failure, same as send_email_infinite_retry
    elsewhere in this file."""
    import time as _nt_time
    import smtplib as _nt_smtplib
    from email.mime.multipart import MIMEMultipart as _NtMulti
    from email.mime.text import MIMEText as _NtText
    from email.mime.image import MIMEImage as _NtImage
    from email.mime.base import MIMEBase as _NtBase
    from email import encoders as _nt_encoders
    import win32com.client as _nt_win32
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _nt_plt
    from matplotlib.patches import FancyBboxPatch as _NtBox
    from openpyxl import Workbook as _NtWorkbook
    from openpyxl.styles import Font as _NtFont, PatternFill as _NtFill, Alignment as _NtAlign, Border as _NtBorder, Side as _NtSide
    from openpyxl.utils import get_column_letter as _nt_col_letter

    _TITLE = "Pay-In / Pay-Out Summary"
    _SUBTITLE = "Cash  |  Futures  |  MCX"
    _SUBJECT_PREFIX = "Tentative Obligation File"
    _FILE_PREFIX = "PayIn_PayOut_Summary"
    _DATA_SHEETS = [
        ("FuturesMTM", "FuturesMTM"),
        ("MCXFuturesMTM", "MCXFuturesMTM"),
        ("DayNetPremium", "DayNetPremium"),
        ("MCXDayNetPremium", "MCXDayNetPremium"),
        ("EquityAmount", "EquityAmount"),
    ]

    def _nt_indian(n):
        n = int(round(n))
        sign_ = "-" if n < 0 else ""
        s = str(abs(n))
        if len(s) > 3:
            head, tail = s[:-3], s[-3:]
            parts = []
            while len(head) > 2:
                parts.insert(0, head[-2:]); head = head[:-2]
            if head: parts.insert(0, head)
            s = ",".join(parts) + "," + tail
        return sign_ + s

    def _nt_sign(x):
        x = float(x)
        return 1 if x > 0 else (-1 if x < 0 else 0)

    def _nt_build_sheet_df(df, col):
        d = df[["ClientID", "Product", col]].copy()
        d["Product"] = d["Product"].astype(str).str.strip()
        d[col] = pd.to_numeric(d[col].astype(str).str.replace(",", "").str.strip(), errors="coerce").fillna(0)
        d = d[(d["Product"] == "-") & (d[col] != 0)]
        d = d[["ClientID", col]].rename(columns={"ClientID": "CLIENT CODE"})
        return d.reset_index(drop=True)

    def _nt_compute_narration(sheets):
        fut = sheets["FuturesMTM"]["FuturesMTM"].sum()
        mcx = sheets["MCXFuturesMTM"]["MCXFuturesMTM"].sum()
        dnp = sheets["DayNetPremium"]["DayNetPremium"].sum()
        mdnp = sheets["MCXDayNetPremium"]["MCXDayNetPremium"].sum()
        eq = sheets["EquityAmount"]["EquityAmount"]
        buying = eq[eq > 0].sum()
        selling = eq[eq < 0].sum()
        fo_premium = -_nt_sign(dnp) * (abs(dnp) - abs(mdnp))
        m_sc = -mdnp
        def _lbl(base, total, positive_is_in):
            if abs(total) < 0.005: return f"{base} PAYIN PAYOUT"
            is_in = (total > 0) == positive_is_in
            return f"{base} {'PAYIN' if is_in else 'PAYOUT'}"
        rows = [
            ["CASH", buying, selling, 0.0, 0.0, True],
            ["FUTURE", 0.0, 0.0, fut, fo_premium, False],
            ["MCX", 0.0, 0.0, mcx, m_sc, False],
        ]
        out = []
        for base, b, s_, f, p, pos_in in rows:
            total = b + s_ + f + p
            out.append([_lbl(base, total, pos_in), total, b, s_, f, p])
        return out

    def _nt_write_excel(path, narr_rows, sheets):
        wb = _NtWorkbook()
        hdr_fill = _NtFill("solid", fgColor="1F3864")
        hdr_font = _NtFont(bold=True, color="FFFFFF")
        tot_fill = _NtFill("solid", fgColor="D9E1F2")
        thin = _NtSide(style="thin", color="999999")
        border = _NtBorder(left=thin, right=thin, top=thin, bottom=thin)
        num_fmt = '#,##0.00;[Red]-#,##0.00'
        ws = wb.active
        ws.title = "NARRATION"
        heads = ["Narration", "Total", "Buying", "Selling", "Future/MCX MTM", "Premium"]
        ws.append(heads)
        for r in narr_rows: ws.append(r)
        for c in ws[1]:
            c.fill, c.font, c.border = hdr_fill, hdr_font, border
            c.alignment = _NtAlign(horizontal="center")
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            for i, c in enumerate(row):
                c.border = border
                if i: c.number_format = num_fmt
        for i, w in enumerate([26, 20, 20, 20, 20, 20], 1):
            ws.column_dimensions[_nt_col_letter(i)].width = w
        for name, col in _DATA_SHEETS:
            d = sheets[name]
            w = wb.create_sheet(name)
            w.append(["CLIENT CODE", col])
            for rec in d.itertuples(index=False): w.append(list(rec))
            last = w.max_row
            w.append(["TOTAL", f"=SUM(B2:B{last})" if last >= 2 else 0])
            for c in w[1]:
                c.fill, c.font, c.border = hdr_fill, hdr_font, border
                c.alignment = _NtAlign(horizontal="center")
            for row in w.iter_rows(min_row=2, max_row=w.max_row):
                row[0].border = row[1].border = border
                row[1].number_format = num_fmt
            for c in w[w.max_row]:
                c.font, c.fill = _NtFont(bold=True), tot_fill
            w.column_dimensions["A"].width = 18
            w.column_dimensions["B"].width = 24
            w.freeze_panes = "A2"
        wb.save(path)

    def _nt_write_dashboard(path, narr_rows, stamp):
        heads = ["Narration", "Total", "Buying", "Selling", "Future/MCX MTM", "Premium"]
        col_w = [3.3, 2.3, 1.9, 1.9, 2.1, 2.2]
        W, H = 11.5, 6.3
        fig = _nt_plt.figure(figsize=(W, H), dpi=150)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
        fig.patch.set_facecolor("#F4F6FA")
        NAVY, LINE, INK, MUTE = "#14284B", "#E3E8F0", "#1B2230", "#8A94A6"
        RED, GREEN = "#C62828", "#1B7F3B"
        ACCENT = ["#3B6FD4", "#1C9C8C", "#E09A1A"]
        ax.add_patch(_nt_plt.Rectangle((0, H - 1.3), W, 1.3, fc=NAVY, ec="none"))
        ax.text(0.5, H - 0.55, _TITLE, fontsize=25, fontweight="bold", color="white", va="center")
        ax.text(0.5, H - 0.98, _SUBTITLE, fontsize=13, color="#AFC0E0", va="center")
        ax.text(W - 0.5, H - 0.55, stamp, fontsize=13, color="white", va="center", ha="right")
        ax.text(W - 0.5, H - 0.98, "All figures in \u20b9", fontsize=13, color="#AFC0E0", va="center", ha="right")
        x0, x1 = 0.4, W - 0.4
        scale = (x1 - x0) / sum(col_w)
        xs = [x0]
        for w in col_w: xs.append(xs[-1] + w * scale)
        top = H - 1.3 - 0.4
        hh, rh = 0.65, 0.95
        card_h = hh + rh * len(narr_rows)
        ax.add_patch(_NtBox((x0, top - card_h), x1 - x0, card_h, boxstyle="round,pad=0,rounding_size=0.12", fc="white", ec=LINE, lw=1.2))
        ax.add_patch(_nt_plt.Rectangle((x0 + 0.01, top - hh), x1 - x0 - 0.02, hh - 0.01, fc="#EAEFF8", ec="none"))
        for i, h in enumerate(heads):
            if i == 0:
                ax.text(xs[0] + 0.4, top - hh / 2, h.upper(), color=NAVY, fontsize=12, fontweight="bold", va="center", ha="left")
            else:
                ax.text((xs[i] + xs[i + 1]) / 2, top - hh / 2, h.upper(), color=NAVY, fontsize=12, fontweight="bold", va="center", ha="center")
        for r, row in enumerate(narr_rows):
            y1 = top - hh - r * rh
            y0 = y1 - rh
            yc = (y0 + y1) / 2
            if r < len(narr_rows) - 1:
                ax.plot([x0 + 0.3, x1 - 0.3], [y0, y0], color=LINE, lw=1)
            ax.add_patch(_nt_plt.Rectangle((x0 + 0.01, y0 + 0.2), 0.09, rh - 0.4, fc=ACCENT[r % 3], ec="none"))
            ax.add_patch(_nt_plt.Rectangle((xs[1] + 0.1, y0 + 0.12), xs[2] - xs[1] - 0.2, rh - 0.24, fc="#F5F7FB", ec="none"))
            ax.text(xs[0] + 0.4, yc, row[0], fontsize=14.5, fontweight="bold", color=INK, va="center", ha="left")
            is_cash_row = str(row[0]).strip().upper().startswith("CASH")
            for i in range(1, 6):
                v = row[i]
                if is_cash_row and i == 2:            # Buying -> always RED, sign dropped
                    disp, colr = _nt_indian(abs(v)), RED
                elif is_cash_row and i == 3:          # Selling -> always GREEN, sign dropped
                    disp, colr = _nt_indian(abs(v)), GREEN
                elif is_cash_row and i == 1:          # Total -> magnitude only, inverted color rule
                    buying, selling = row[2], row[3]  #   selling > buying (signed total -ve) -> GREEN
                    if abs(selling) > abs(buying):    #   buying > selling (signed total +ve) -> RED
                        colr = GREEN
                    elif abs(buying) > abs(selling):
                        colr = RED
                    else:
                        colr = MUTE
                    disp = _nt_indian(abs(v))
                else:
                    colr = RED if v < -0.5 else (GREEN if v > 0.5 else MUTE)
                    disp = _nt_indian(v)
                ax.text((xs[i] + xs[i + 1]) / 2, yc, disp, fontsize=14.5 if i == 1 else 14, fontweight="bold" if i == 1 else "normal", color=colr, va="center", ha="center")
        ax.add_patch(_nt_plt.Rectangle((0, 0), W, 0.65, fc="#E9EDF4", ec="none"))
        ax.text(0.5, 0.325, "Cash  |  Futures  |  MCX  |  Daily Pay-In / Pay-Out", fontsize=12, color="#5B6577", va="center")
        ax.text(W - 0.5, 0.325, "Master Capital Services Ltd", fontsize=12, color="#5B6577", va="center", ha="right", fontweight="bold")
        fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
        _nt_plt.close(fig)

    def _nt_password_protect(input_file):
        _today = datetime.now().strftime("%d%m%Y")
        password = f"Scenario@{_today}"
        input_file = os.path.abspath(input_file)
        base, ext = os.path.splitext(input_file)
        dated_file = f"{base}_{_today}{ext}"
        excel = _nt_win32.DispatchEx("Excel.Application")
        excel.DisplayAlerts = False
        excel.Visible = False
        wb = excel.Workbooks.Open(input_file, False, False)
        wb.SaveAs(dated_file, FileFormat=51, Password=password)
        wb.Close(False)
        excel.Quit()
        print(f"🔐 PASSWORD APPLIED → {os.path.basename(dated_file)}")
        print(f"🔑 Password: {password}")
        return dated_file

    def _nt_build_email_html(stamp):
        return f"""\
<html><body style="margin:0;padding:0;background:#EEF1F6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#EEF1F6;padding:24px 0;">
<tr><td align="center">
  <table role="presentation" width="920" cellpadding="0" cellspacing="0"
         style="max-width:920px;width:100%;background:#FFFFFF;border-radius:10px;overflow:hidden;
                font-family:'Segoe UI',Calibri,Arial,sans-serif;color:#1B2230;">
    <tr><td style="padding:26px 28px 6px 28px;font-size:15px;line-height:1.6;">
        Dear Sir,<br>
        Please find the Tentative Obligation File Summary below:-
    </td></tr>
    <tr><td style="padding:14px 28px 6px 28px;">
        <img src="cid:dashboard" alt="{_TITLE}" width="860"
             style="display:block;width:860px;max-width:100%;height:auto;border:1px solid #E3E8F0;border-radius:6px;">
    </td></tr>
    <tr><td style="padding:0 28px 14px 28px;font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td style="padding:0 28px 16px 28px;font-size:13px;line-height:1.5;color:#3B4A63;">
        <b>NOTE:</b> Custodian Accounts have been removed from this report.<br>
        The Day Net Premium includes FO Day Net Premium and MCX Day Net Premium.
    </td></tr>
    <tr><td style="padding:0 28px;"><div style="border-top:1px solid #E3E8F0;"></div></td></tr>
    <tr><td style="padding:18px 28px 26px 28px;font-size:14px;line-height:1.65;">
        Best regards,<br>
        Sandeep Kumar<br>
        011-42111079
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""

    sheets = {name: _nt_build_sheet_df(df_main_raw, col) for name, col in _DATA_SHEETS}
    narr_rows = _nt_compute_narration(sheets)
    stamp = datetime.now().strftime("%d-%b-%Y %H:%M")
    # NOTE: xlsx_path deliberately has NO date suffix here — _nt_password_protect()
    # below adds its own "_<DDMMYYYY>" suffix when it saves the protected copy.
    # Adding EMAIL_DATE here too used to produce a double-dated filename like
    # "PayIn_PayOut_Summary_01102026_01102026.xlsx" — fixed, single date now.
    xlsx_path = os.path.join(BASE_FOLDER, f"{_FILE_PREFIX}.xlsx")
    png_path = os.path.join(BASE_FOLDER, f"{_FILE_PREFIX}_{EMAIL_DATE}.png")
    _nt_write_excel(xlsx_path, narr_rows, sheets)
    _nt_write_dashboard(png_path, narr_rows, stamp)
    protected_xlsx = _nt_password_protect(xlsx_path)

    # The Tentative Obligation e-mail is switched OFF for now (no e-mail, no WhatsApp). The Excel + PNG above are
    # still created and kept in the folder. To start sending again, set SA_SEND_TENTATIVE=true in .env.
    if (_SAENV("SA_SEND_TENTATIVE", "false") or "").strip().lower() not in ("1", "true", "yes", "on"):
        print(f"⏸️ Tentative Obligation e-mail is OFF (SA_SEND_TENTATIVE not true) — NOT sent. "
              f"Files kept: {protected_xlsx} and {png_path}")
        return False

    to_list = _SALIST("SA_MAIL_TENTATIVE_TO", ["kartavya.dhillon@mastertrust.co.in"])
    cc_list = _SALIST("SA_MAIL_TENTATIVE_CC", ["kartavya.dhillon@mastertrust.co.in"])

    while True:
        try:
            msg = _NtMulti("mixed")
            msg["From"] = EMAIL_ADDRESS
            msg["To"] = ", ".join(to_list)
            if cc_list: msg["Cc"] = ", ".join(cc_list)
            msg["Subject"] = f"{_SUBJECT_PREFIX}-{EMAIL_DATE}"
            alt = _NtMulti("alternative")
            msg.attach(alt)
            alt.attach(_NtText("Dear Sir,\nPlease find the Tentative Obligation File Summary below:-\n\n"
                                "(View this email in HTML to see the summary.)\n\nBest regards,\nSandeep Kumar\n011-42111079", "plain", "utf-8"))
            related = _NtMulti("related")
            alt.attach(related)
            related.attach(_NtText(_nt_build_email_html(stamp), "html", "utf-8"))
            with open(png_path, "rb") as f:
                img = _NtImage(f.read(), _subtype="png")
            img.add_header("Content-ID", "<dashboard>")
            img.add_header("Content-Disposition", "inline", filename=os.path.basename(png_path))
            related.attach(img)
            with open(protected_xlsx, "rb") as f:
                part = _NtBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                part.set_payload(f.read())
            _nt_encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=os.path.basename(protected_xlsx))
            msg.attach(part)
            with _nt_smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.sendmail(EMAIL_ADDRESS, to_list + cc_list, msg.as_string())
            print(f"✅ Email sent successfully: {_SUBJECT_PREFIX}-{EMAIL_DATE}")
            break
        except Exception as e:
            print(f"❌ Email error ({_SUBJECT_PREFIX}-{EMAIL_DATE}): {e}")
            print("🔁 Retrying in 3 seconds...")
            _nt_time.sleep(3)
    return True

if _las_narration_tentative_email(df_main_raw):
    print("✅ NARRATION-style Tentative Obligation email sent (password-protected attachment)")

print("🎉 ALL EMAILS SENT SUCCESSFULLY")




# ==================================================
# POST-EMAIL SAFE ADD-ON BLOCK (SCENARIO + LAS REF)
# ==================================================

# Checkpoint: from here on, a crash (e.g. a missing column in scenario.csv)
# is recorded as POST_EMAIL — so "Resume" restarts HERE and does NOT re-send the
# WhatsApp messages / emails that already went out above.
_ckpt.las_step("POST_EMAIL", script="LAS4")
print("\n📊 Starting POST-EMAIL Scenario & LAS reference steps")

import pandas as pd
import os
from datetime import datetime

# ==================================================
# COMMON DATES
# ==================================================
today_ddmmyyyy = datetime.today().strftime("%d%m%Y")
today_dd_mm_yyyy = datetime.today().strftime("%d-%m-%Y")

# ==================================================
# STEP A: CREATE SCENARIO_Filtered.xlsx
# ==================================================

print("📄 Creating SCENARIO_Filtered.xlsx")

scenario_file = os.path.join(BASE_FOLDER, "scenario.csv")
scenario_filtered_file = os.path.join(BASE_FOLDER, "SCENARIO_Filtered.xlsx")

if not os.path.exists(scenario_file):
    raise FileNotFoundError(f"❌ scenario.csv not found: {scenario_file}")

df_scn = pd.read_csv(scenario_file, dtype=str)

# --------------------------------------------------
# EXCLUDE INTERNAL / DUMMY CLIENT CODES (GLOBAL SCENARIO CLEAN)
# --------------------------------------------------

EXCLUDE_CLIENTS = {
    "OWN",
    "*OWN*",
    "01292",
    "PRO_06436",
    "06436"
}

# Normalize Client ID
df_scn["Client ID"] = (
    df_scn["Client ID"]
    .astype(str)
    .str.strip()
    .str.upper()
)

# 🔹 Capture BEFORE cleanup
clients_before = set(df_scn["Client ID"].unique())

# 🔹 Apply exclusions (exact match)
df_scn = df_scn[
    ~df_scn["Client ID"].isin(EXCLUDE_CLIENTS)
]

# 🔹 Apply pattern-based exclusion (OWN anywhere)
df_scn = df_scn[
    ~df_scn["Client ID"].str.contains("OWN", case=False, na=False)
]

# 🔹 Capture AFTER cleanup
clients_after = set(df_scn["Client ID"].unique())

# 🔹 Find removed clients
removed_clients = sorted(clients_before - clients_after)

# 🔹 Print audit log
if removed_clients:
    print("🧹 Scenario cleanup applied — removed internal client codes:")
    for c in removed_clients:
        print(f"   ❌ {c}")
    print(f"🔢 Total removed: {len(removed_clients)}")
else:
    print("🧹 Scenario cleanup applied — no internal client codes found")


required_cols = [
    "Product", "Zone", "Client ID", "Name",
    "UP_2%", "DOWN_2%", "UP_4%", "DOWN_4%",
    "UP_6%", "DOWN_6%", "UP_8%", "DOWN_8%"
]

missing = [c for c in required_cols if c not in df_scn.columns]
if missing:
    raise ValueError(f"❌ Missing columns in SCENARIO.csv: {missing}")

df_scn_filtered = df_scn[required_cols].copy()
df_scn_filtered.to_excel(scenario_filtered_file, index=False)

print(f"✅ Created: {scenario_filtered_file}")

# ==================================================
# STEP B: FILTER Product == '-'
# ==================================================

print("🔍 Creating SCENARIO_Filtered_ProductDash.xlsx")

scenario_product_dash_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Filtered_ProductDash.xlsx"
)

df_dash = df_scn_filtered[
    df_scn_filtered["Product"].astype(str).str.strip() == "-"
].copy()

df_dash.to_excel(scenario_product_dash_file, index=False)

print(f"✅ Created: {scenario_product_dash_file}")

# ==================================================
# STEP C: CREATE SCENARIO_Top100.xlsx (UP / DOWN SPLIT)
# ==================================================

print("📈 Creating SCENARIO_Top100.xlsx")

scenario_top100_file = os.path.join(BASE_FOLDER, "SCENARIO_Top100.xlsx")

xls = pd.ExcelFile(scenario_product_dash_file)
sheets = {sheet: xls.parse(sheet) for sheet in xls.sheet_names}
xls.close()  # release the OS file handle immediately (avoids Windows file-lock
             # issues later if this file gets deleted/rewritten during cleanup)

with pd.ExcelWriter(scenario_top100_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():

        # Original sheet
        df.to_excel(writer, sheet_name=sheet_name, index=False)

        # UP sheet
        up_cols = [
            "Product", "Zone", "Client ID", "Name",
            "UP_2%", "UP_4%", "UP_6%", "UP_8%"
        ]
        df[up_cols].copy().to_excel(
            writer,
            sheet_name="Top_Market_UP_100",
            index=False
        )

        # DOWN sheet
        down_cols = [
            "Product", "Zone", "Client ID", "Name",
            "DOWN_2%", "DOWN_4%", "DOWN_6%", "DOWN_8%"
        ]
        df[down_cols].copy().to_excel(
            writer,
            sheet_name="Top_Market_DOWN_100",
            index=False
        )

print(f"✅ Created: {scenario_top100_file}")

# ==================================================
# STEP D: CREATE LAS DEBIT REFERENCE FILE WITH LAS FUNDING
# ==================================================

print("📄 Creating LAS Debit reference file with LAS FUNDING")

las_source_file = os.path.join(
    BASE_FOLDER,
    f"LAS Debit Balances-{today_dd_mm_yyyy}.xlsx"
)

if os.path.exists(las_source_file):
    las_output_file = las_source_file.replace(
        ".xlsx",
        "_With_LAS_FUNDING.xlsx"
    )

    df_las = pd.read_excel(las_source_file, engine="openpyxl")

    if "Net Debit" not in df_las.columns:
        raise ValueError("❌ 'Net Debit' column not found in LAS Debit file")

    df_las["Net Debit"] = pd.to_numeric(df_las["Net Debit"], errors="coerce")
    df_las["LAS FUNDING"] = -df_las["Net Debit"].abs()

    df_las.to_excel(las_output_file, index=False)

    print(f"✅ Created LAS reference file: {las_output_file}")
else:
    print(f"⚠️ LAS Debit file not found, skipped: {las_source_file}")

print("\n🎉 POST-EMAIL Scenario & LAS reference steps COMPLETED")




# ==================================================
# STEP E: ADD LAS FUNDING INTO SCENARIO_Top100 (ALL SHEETS)
# ==================================================

print("💰 Merging LAS FUNDING into SCENARIO_Top100")

today_dd_mm_yyyy = datetime.today().strftime("%d-%m-%Y")

scenario_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100.xlsx"
)

funding_file = os.path.join(
    BASE_FOLDER,
    f"LAS Debit Balances-{today_dd_mm_yyyy}_With_LAS_FUNDING.xlsx"
)

output_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_WithFunding.xlsx"
)

# ---- Safety checks ----
if not os.path.exists(scenario_file):
    raise FileNotFoundError(f"❌ SCENARIO_Top100.xlsx not found: {scenario_file}")

if not os.path.exists(funding_file):
    raise FileNotFoundError(f"❌ LAS FUNDING reference file not found: {funding_file}")

# ---- Read LAS FUNDING mapping ----
df_funding = pd.read_excel(funding_file, dtype=str)

df_funding = df_funding[["Trading Code", "LAS FUNDING"]].copy()
df_funding.rename(columns={"Trading Code": "Client ID"}, inplace=True)

df_funding["LAS FUNDING"] = pd.to_numeric(
    df_funding["LAS FUNDING"],
    errors="coerce"
)

# ---- Read Scenario Top100 (all sheets) ----
xls = pd.ExcelFile(scenario_file)
sheets = {
    sheet: xls.parse(sheet, dtype=str)
    for sheet in xls.sheet_names
}
xls.close()  # release the OS file handle immediately

# ---- Merge LAS FUNDING into each sheet ----
updated_sheets = {}

for sheet_name, df in sheets.items():
    if "Client ID" in df.columns:
        df = df.merge(
            df_funding,
            how="left",
            on="Client ID"
        )
    updated_sheets[sheet_name] = df

# ---- Save final enriched Scenario file ----
with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in updated_sheets.items():
        df.to_excel(
            writer,
            sheet_name=sheet_name,
            index=False
        )

print(f"✅ SCENARIO_Top100_WithFunding created: {output_file}")

# ==================================================
# STEP F: ADD SA0X Total value of Collateral+fund INTO SCENARIO TOP100
# ==================================================

print("💼 Merging SA0X Total value of Collateral+fund into SCENARIO_Top100_WithFunding")

import glob

scenario_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_WithFunding.xlsx"
)

input_folder = os.path.join(
    BASE_FOLDER
)

output_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_WithFunding_Deposit.xlsx"
)

# ---- Step 1: Read all SA0X files ----
all_files = glob.glob(
    os.path.join(input_folder, "SA0*_OUTPUT FILE.xlsx")
)

deposit_list = []

for file in all_files:
    try:
        df_sa = pd.read_excel(file, dtype=str)
        if (
            "Client/UCC Code" in df_sa.columns and
            "Total value of Collateral+fund" in df_sa.columns
        ):
            deposit_list.append(
                df_sa[
                    ["Client/UCC Code", "Total value of Collateral+fund"]
                ]
            )
        else:
            print(f"⚠️ Required columns missing in {file}")
    except Exception as e:
        print(f"⚠️ Could not read {file}: {e}")

if not deposit_list:
    raise ValueError(
        "❌ No valid SA0X files found with required columns."
    )

deposit_df = (
    pd.concat(deposit_list, ignore_index=True)
    .drop_duplicates()
)

# ---- Step 2: Read scenario file (all sheets) ----
xls = pd.ExcelFile(scenario_file)
sheets = {
    sheet: xls.parse(sheet, dtype=str)
    for sheet in xls.sheet_names
}
xls.close()  # release the OS file handle immediately

# ---- Step 3: Merge deposit into each sheet ----
for sheet_name, df in sheets.items():
    if "Client ID" in df.columns:
        merged_df = df.merge(
            deposit_df,
            left_on="Client ID",
            right_on="Client/UCC Code",
            how="left"
        )
        merged_df.drop(
            columns=["Client/UCC Code"],
            inplace=True,
            errors="ignore"
        )
        sheets[sheet_name] = merged_df
    else:
        print(
            f"⚠️ Skipped sheet '{sheet_name}' (no Client ID column)."
        )

# ---- Step 4: Save final file ----
with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(
            writer,
            sheet_name=sheet_name,
            index=False
        )

print(f"✅ SCENARIO_Top100_WithFunding_Deposit created: {output_file}")


# ==================================================
# STEP G: MERGE RISK NOV INTO SCENARIO TOP100 (ALL SHEETS)
# ==================================================

print("⚠️ Merging RISK NOV into SCENARIO_Top100_WithFunding_Deposit")

import glob

# --------------------------------------------------
# DATE (DDMMYYYY)
# --------------------------------------------------
today = datetime.now().strftime("%d%m%Y")

# --------------------------------------------------
# FILE PATHS (STRICTLY FROM BASE_FOLDER)
# --------------------------------------------------

scenario_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_WithFunding_Deposit.xlsx"
)

# --------------------------------------------------
# LOCATE RISK FILE  (3-way fallback: COMMON → SA_OUTPUT → BASE_FOLDER)
# --------------------------------------------------
_SA_OUTPUT_DIR2   = _SAENV("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
_risk_candidates2 = [
    os.path.join(COMMON_DIR,      f"RISK_REPORT_{today}.csv"),
    os.path.join(_SA_OUTPUT_DIR2, f"RISK_REPORT_{today}.csv"),
    os.path.join(BASE_FOLDER,     f"RISK_REPORT_{today}.csv"),
]
risk_file = next((p for p in _risk_candidates2 if os.path.isfile(p)), None)
if not risk_file:
    raise FileNotFoundError(
        f"❌ No RISK_REPORT file found for date {today}\n"
        f"   Searched: {_risk_candidates2}"
    )
print(f"📂 Reading risk report: {risk_file}")

# --------------------------------------------------
# READ RISK CSV
# --------------------------------------------------
df_risk = pd.read_csv(risk_file, dtype=str)
df_risk.columns = df_risk.columns.str.strip()

# --------------------------------------------------
# NUMERIC CONVERSION (NO LOGIC CHANGE)
# --------------------------------------------------
df_risk["FNONOV"] = pd.to_numeric(
    df_risk.get("FNONOV", 0),
    errors="coerce"
).fillna(0)

df_risk["COMNOV"] = pd.to_numeric(
    df_risk.get("COMNOV", 0),
    errors="coerce"
).fillna(0)

# --------------------------------------------------
# CALCULATE NOV
# --------------------------------------------------
df_risk["NOV"] = df_risk["FNONOV"] + df_risk["COMNOV"]

df_risk_merge = df_risk[["ACCOUNTID", "NOV"]].copy()

# --------------------------------------------------
# READ SCENARIO FILE (ALL SHEETS)
# --------------------------------------------------
if not os.path.exists(scenario_file):
    raise FileNotFoundError(
        f"❌ Scenario file not found: {scenario_file}"
    )

xls = pd.ExcelFile(scenario_file)
sheets = {
    sheet: xls.parse(sheet, dtype=str)
    for sheet in xls.sheet_names
}
xls.close()  # release the OS file handle immediately

# --------------------------------------------------
# MERGE NOV INTO EACH SHEET
# --------------------------------------------------
updated_sheets = {}

for sheet_name, df in sheets.items():
    if "Client ID" in df.columns:
        df = df.merge(
            df_risk_merge,
            how="left",
            left_on="Client ID",
            right_on="ACCOUNTID"
        )
        df.drop(
            columns=["ACCOUNTID"],
            inplace=True,
            errors="ignore"
        )

    updated_sheets[sheet_name] = df

# --------------------------------------------------
# SAVE BACK (OVERWRITE SAME FILE – INTENTIONAL)
# --------------------------------------------------
with pd.ExcelWriter(
    scenario_file,
    engine="openpyxl",
    mode="w"
) as writer:
    for sheet_name, df in updated_sheets.items():
        df.to_excel(
            writer,
            sheet_name=sheet_name,
            index=False
        )

print(f"✅ NOV column merged successfully into all sheets: {scenario_file}")


# ==================================================
# STEP H: FINAL DEPOSIT CALCULATION (POST EMAIL)
# ==================================================

print("🧮 Creating final Scenario file with deposit calculations")

# --------------------------------------------------
# DATE
# --------------------------------------------------
today = datetime.now().strftime("%d%m%Y")

# --------------------------------------------------
# FILE PATHS (STRICT BASE_FOLDER ONLY)
# --------------------------------------------------
input_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_WithFunding_Deposit.xlsx"
)

output_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_Final.xlsx"
)

# --------------------------------------------------
# LOAD ALL SHEETS
# --------------------------------------------------
xls = pd.ExcelFile(input_file)
sheets = {
    sheet: xls.parse(sheet)
    for sheet in xls.sheet_names
}
xls.close()  # release the OS file handle immediately

# --------------------------------------------------
# PROCESS EACH SHEET
# --------------------------------------------------
updated_sheets = {}

for sheet_name, df in sheets.items():

    if all(
        col in df.columns
        for col in [
            "LAS FUNDING",
            "Total value of Collateral+fund",
            "NOV"
        ]
    ):
        # ---- Numeric conversion (NO LOGIC CHANGE) ----
        df["LAS FUNDING"] = pd.to_numeric(
            df["LAS FUNDING"],
            errors="coerce"
        ).fillna(0)

        df["Total value of Collateral+fund"] = pd.to_numeric(
            df["Total value of Collateral+fund"],
            errors="coerce"
        ).fillna(0)

        df["NOV"] = pd.to_numeric(
            df["NOV"],
            errors="coerce"
        ).fillna(0)
        
        df.loc[df["NOV"] > 0, "NOV"] = 0   #force +ve values of nov to 0

        # ---- Apply formulas ----
        df["Deposit(Own Client Fund)"] = (
            df["Total value of Collateral+fund"] +
            df["LAS FUNDING"]
        )

        df["Deposit (After adjustment Short NOV)"] = (
            df["Deposit(Own Client Fund)"] +
            df["NOV"]
        )

    # ---- Fill remaining blanks ----
    df = df.fillna(0)

    updated_sheets[sheet_name] = df

# --------------------------------------------------
# SAVE FINAL OUTPUT (NEW FILE)
# --------------------------------------------------
with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in updated_sheets.items():
        df.to_excel(
            writer,
            sheet_name=sheet_name,
            index=False
        )

print(
    f"✅ Final Scenario file created successfully: {output_file}"
)

# ==================================================
# STEP I: FINAL TOP-100 FILTERING & SORTING
# ==================================================

print("📊 Creating Top-100 filtered scenario file")

# --------------------------------------------------
# FILE PATHS (STRICT BASE_FOLDER ONLY)
# --------------------------------------------------
file_path = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_Final.xlsx"
)

output_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_Final_Top100_Filtered.xlsx"
)

# --------------------------------------------------
# SORTING MODE
#   most_negative_first  -> -318, -306, -95
#   closest_to_zero      -> -1, -5, -20
# --------------------------------------------------
mode = "most_negative_first"

# --------------------------------------------------
# TARGET COLUMNS
# --------------------------------------------------
target_cols_up = [
    "UP_2%", "UP_4%", "UP_6%", "UP_8%",
    "LAS FUNDING",
    "Total value of Collateral+fund",
    "NOV",
    "Deposit(Own Client Fund)",
    "Deposit (After adjustment Short NOV)"
]

target_cols_down = [
    "DOWN_2%", "DOWN_4%", "DOWN_6%", "DOWN_8%",
    "LAS FUNDING",
    "Total value of Collateral+fund",
    "NOV",
    "Deposit(Own Client Fund)",
    "Deposit (After adjustment Short NOV)"
]

# --------------------------------------------------
# HELPER: CLEAN TO NUMERIC
# --------------------------------------------------
def to_numeric_series(s):
    return pd.to_numeric(
        s.astype(str)
         .str.replace("%", "", regex=False)
         .str.replace(",", "", regex=False)
         .str.replace("−", "-", regex=False)
         .str.replace("–", "-", regex=False)
         .str.replace("—", "-", regex=False)
         .str.strip(),
        errors="coerce"
    )

# --------------------------------------------------
# LOAD ALL SHEETS
# --------------------------------------------------
sheets = pd.read_excel(file_path, sheet_name=None)

# --------------------------------------------------
# PROCESS SHEETS
# --------------------------------------------------
for sheet_name in list(sheets.keys()):

    # Remove default Sheet1 if present
    if sheet_name.strip().lower() == "sheet1":
        del sheets[sheet_name]
        continue

    df = sheets[sheet_name].copy()

    # ---------- TOP MARKET UP ----------
    if sheet_name == "Top_Market_UP_100" and "UP_2%" in df.columns:

        df["UP_2%"] = to_numeric_series(df["UP_2%"])
        df = df[df["UP_2%"] < 0].copy()

        ascending = True if mode == "most_negative_first" else False
        df = (
            df.sort_values(by="UP_2%", ascending=ascending)
              .head(100)
              .reset_index(drop=True)
        )

        for col in target_cols_up:
            if col in df.columns:
                df[col] = (
                    to_numeric_series(df[col])
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )

        sheets[sheet_name] = df

    # ---------- TOP MARKET DOWN ----------
    elif sheet_name == "Top_Market_DOWN_100" and "DOWN_2%" in df.columns:

        df["DOWN_2%"] = to_numeric_series(df["DOWN_2%"])
        df = df[df["DOWN_2%"] < 0].copy()

        ascending = True if mode == "most_negative_first" else False
        df = (
            df.sort_values(by="DOWN_2%", ascending=ascending)
              .head(100)
              .reset_index(drop=True)
        )

        for col in target_cols_down:
            if col in df.columns:
                df[col] = (
                    to_numeric_series(df[col])
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )

        sheets[sheet_name] = df

    # ---------- OTHER SHEETS ----------
    else:
        for col in (target_cols_up + target_cols_down):
            if col in df.columns:
                df[col] = (
                    to_numeric_series(df[col])
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )
        sheets[sheet_name] = df

# --------------------------------------------------
# WRITE OUTPUT
# --------------------------------------------------
with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)

print("✅ Top-100 filtered scenario file created:", output_file)
print("🔀 Sorting mode:", mode)


# ==================================================
# STEP J: REARRANGE COLUMNS (TOP MARKET UP / DOWN)
# ==================================================

print("🔀 Rearranging columns for Top Market sheets")

# --------------------------------------------------
# FILE PATHS (STRICT BASE_FOLDER ONLY)
# --------------------------------------------------
file_path = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_Final.xlsx"
)

output_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_Final_Rearranged.xlsx"
)

# --------------------------------------------------
# READ ALL SHEETS
# --------------------------------------------------
sheets = pd.read_excel(file_path, sheet_name=None)

# --------------------------------------------------
# PRIORITY COLUMN ORDERS
# --------------------------------------------------
up_order = [
    "Zone", "Client ID", "Name",
    "LAS FUNDING",
    "Total value of Collateral+fund",
    "Deposit(Own Client Fund)",
    "Deposit (After adjustment Short NOV)",
    "UP_2%", "UP_4%", "UP_6%", "UP_8%"
]

down_order = [
    "Zone", "Client ID", "Name",
    "LAS FUNDING",
    "Total value of Collateral+fund",
    "Deposit(Own Client Fund)",
    "Deposit (After adjustment Short NOV)",
    "DOWN_2%", "DOWN_4%", "DOWN_6%", "DOWN_8%"
]

# --------------------------------------------------
# PROCESS SHEETS
# --------------------------------------------------
for sheet_name, df in sheets.items():

    if sheet_name == "Top_Market_UP_100":
        remaining_cols = [
            col for col in df.columns
            if col not in up_order
        ]
        df = (
            [df[col] for col in up_order if col in df.columns]
        )
        df = pd.concat(df + [sheets[sheet_name][remaining_cols]], axis=1)
        sheets[sheet_name] = df

    elif sheet_name == "Top_Market_DOWN_100":
        remaining_cols = [
            col for col in df.columns
            if col not in down_order
        ]
        df = (
            [df[col] for col in down_order if col in df.columns]
        )
        df = pd.concat(df + [sheets[sheet_name][remaining_cols]], axis=1)
        sheets[sheet_name] = df

    # Other sheets remain unchanged

# --------------------------------------------------
# SAVE REARRANGED FILE
# --------------------------------------------------
with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(
            writer,
            sheet_name=sheet_name,
            index=False
        )

print(f"✅ Rearranged file created successfully: {output_file}")


# ==================================================
# STEP K: FINAL SCENARIO ANALYSIS (TOP 100 WITH MARGIN)
# ==================================================

print("📘 Creating final Scenario Analysis Top 100 file")

# --------------------------------------------------
# DATE
# --------------------------------------------------
today = datetime.today().strftime("%d%m%Y")

# --------------------------------------------------
# FILE PATHS (STRICT BASE_FOLDER ONLY)
# --------------------------------------------------
input_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_Final_Rearranged.xlsx"
)

output_file = os.path.join(
    BASE_FOLDER,
    "Scenario Analysis_Top_100_Clients.xlsx"
)

main_file = os.path.join(
    BASE_FOLDER,
    "MAIN.csv"
)

# --------------------------------------------------
# LOAD ALL SHEETS
# --------------------------------------------------
xls = pd.ExcelFile(input_file)
sheets = {
    sheet: xls.parse(sheet)
    for sheet in xls.sheet_names
}
xls.close()  # release the OS file handle immediately — this file
             # (SCENARIO_Top100_Final_Rearranged.xlsx) gets deleted during
             # the later cleanup step, and Windows keeps it locked until this
             # handle is explicitly closed, which was causing "file locked"
             # retries/failure there.

# --------------------------------------------------
# LOAD MAIN.CSV (FOR MARGIN)
# --------------------------------------------------
df_main = pd.read_csv(main_file, dtype=str)
df_main.columns = df_main.columns.str.strip()

# Detect Client column safely
if "ClientID" in df_main.columns:
    client_col = "ClientID"
elif "Client ID" in df_main.columns:
    client_col = "Client ID"
else:
    raise ValueError("❌ MAIN.csv missing ClientID / Client ID column")

df_main[client_col] = (
    df_main[client_col]
    .astype(str)
    .str.strip()
    .str.upper()
)

df_main["Total Margin Utill"] = (
    pd.to_numeric(
        df_main["Total Margin Utill"],
        errors="coerce"
    )
    .fillna(0)
    .astype(int)
)

# --------------------------------------------------
# HELPER: CLEAN TO INT
# --------------------------------------------------
def clean_to_int(series):
    return (
        pd.to_numeric(
            series.astype(str)
                  .str.replace(",", "")
                  .str.strip(),
            errors="coerce"
        )
        .fillna(0)
        .round(0)
        .astype(int)
    )

# --------------------------------------------------
# PROCESS TOP MARKET UP
# --------------------------------------------------
if "Top_Market_UP_100" in sheets:
    df_up = sheets["Top_Market_UP_100"].copy()

    for col in ["UP_2%", "UP_4%", "UP_6%", "UP_8%"]:
        if col in df_up.columns:
            df_up[col] = clean_to_int(df_up[col])

    for col in [
        "Deposit(Own Client Fund)",
        "Deposit (After adjustment Short NOV)"
    ]:
        if col in df_up.columns:
            df_up[col] = clean_to_int(df_up[col])

    top_up_100 = (
        df_up
        .sort_values(by="UP_2%", ascending=True)
        .head(100)
    )

    top_up_100 = top_up_100.drop(
        columns=[
            "Total value of Collateral+fund",
            "Product",
            "NOV"
        ],
        errors="ignore"
    )

    top_up_100 = top_up_100.merge(
        df_main[[client_col, "Total Margin Utill"]]
        .rename(columns={client_col: "Client ID"}),
        on="Client ID",
        how="left"
    )

    top_up_100.rename(
        columns={"Total Margin Utill": "MARGIN"},
        inplace=True
    )

    cols = top_up_100.columns.tolist()
    if "MARGIN" in cols and "Deposit (After adjustment Short NOV)" in cols:
        cols.insert(
            cols.index("Deposit (After adjustment Short NOV)") + 1,
            cols.pop(cols.index("MARGIN"))
        )
        top_up_100 = top_up_100[cols]

    sheets["Top_Market_UP_100"] = top_up_100

# --------------------------------------------------
# PROCESS TOP MARKET DOWN
# --------------------------------------------------
if "Top_Market_DOWN_100" in sheets:
    df_down = sheets["Top_Market_DOWN_100"].copy()

    for col in ["DOWN_2%", "DOWN_4%", "DOWN_6%", "DOWN_8%"]:
        if col in df_down.columns:
            df_down[col] = clean_to_int(df_down[col])

    for col in [
        "Deposit(Own Client Fund)",
        "Deposit (After adjustment Short NOV)"
    ]:
        if col in df_down.columns:
            df_down[col] = clean_to_int(df_down[col])

    top_down_100 = (
        df_down
        .sort_values(by="DOWN_2%", ascending=True)
        .head(100)
    )

    top_down_100 = top_down_100.drop(
        columns=[
            "Total value of Collateral+fund",
            "Product",
            "NOV"
        ],
        errors="ignore"
    )

    top_down_100 = top_down_100.merge(
        df_main[[client_col, "Total Margin Utill"]]
        .rename(columns={client_col: "Client ID"}),
        on="Client ID",
        how="left"
    )

    top_down_100.rename(
        columns={"Total Margin Utill": "MARGIN"},
        inplace=True
    )

    cols = top_down_100.columns.tolist()
    if "MARGIN" in cols and "Deposit (After adjustment Short NOV)" in cols:
        cols.insert(
            cols.index("Deposit (After adjustment Short NOV)") + 1,
            cols.pop(cols.index("MARGIN"))
        )
        top_down_100 = top_down_100[cols]

    sheets["Top_Market_DOWN_100"] = top_down_100

# --------------------------------------------------
# DROP DEFAULT SHEET
# --------------------------------------------------
sheets.pop("Sheet1", None)

# --------------------------------------------------
# SAVE FINAL OUTPUT
# --------------------------------------------------
with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(
            writer,
            sheet_name=sheet_name,
            index=False
        )

print("✅ Final Scenario Analysis file created:")
print(output_file)


# ==================================================
# STEP L: FINAL EXCEL FORMATTING (BORDERS + ALIGNMENT)
# ==================================================

print("🎨 Applying final formatting to Scenario Analysis file")

import openpyxl
from openpyxl.styles import Alignment, Border, Side
from openpyxl.utils import get_column_letter

# --------------------------------------------------
# FILE PATH (STRICT BASE_FOLDER ONLY)
# --------------------------------------------------
file_path = os.path.join(
    BASE_FOLDER,
    "Scenario Analysis_Top_100_Clients.xlsx"
)

# --------------------------------------------------
# LOAD WORKBOOK
# --------------------------------------------------
wb = openpyxl.load_workbook(file_path)

# --------------------------------------------------
# BORDER STYLE
# --------------------------------------------------
thin_border = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin")
)

# --------------------------------------------------
# APPLY FORMATTING TO ALL SHEETS
# --------------------------------------------------
for ws in wb.worksheets:

    # Apply alignment + borders
    for row in ws.iter_rows(
        min_row=1,
        max_row=ws.max_row,
        max_col=ws.max_column
    ):
        for cell in row:
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
                wrap_text=True
            )
            cell.border = thin_border

    # Auto-adjust column widths
    for col in ws.columns:
        max_length = 0
        col_letter = get_column_letter(col[0].column)

        for cell in col:
            try:
                if cell.value is not None:
                    max_length = max(
                        max_length,
                        len(str(cell.value))
                    )
            except Exception:
                pass

        ws.column_dimensions[col_letter].width = max_length + 2

# --------------------------------------------------
# SAVE FORMATTED FILE
# --------------------------------------------------
wb.save(file_path)

print(
    f"✅ Formatting applied successfully "
    f"(borders + center alignment): {file_path}"
)


# ==================================================
# password protection SCENARIO ANALYSIS – TOP 100 CLIENTS
# ==================================================


password_protect_excel_force(
    os.path.join(BASE_FOLDER, "Scenario Analysis_Top_100_Clients.xlsx")
)






# ==================================================
# STEP N: SCENARIO ANALYSIS – TOP 15 CLIENTS
# ==================================================

print("📘 Creating Scenario Analysis Top 15 Clients file")

from datetime import datetime

# --------------------------------------------------
# DATE
# --------------------------------------------------
today = datetime.today().strftime("%d%m%Y")

# --------------------------------------------------
# FILE PATHS (STRICT BASE_FOLDER ONLY)
# --------------------------------------------------
input_file = os.path.join(
    BASE_FOLDER,
    "SCENARIO_Top100_Final_Rearranged.xlsx"
)

output_file = os.path.join(
    BASE_FOLDER,
    "Scenario Analysis_Top_15_Clients.xlsx"
)

main_file = os.path.join(
    BASE_FOLDER,
    "MAIN.csv"
)

# --------------------------------------------------
# LOAD ALL SHEETS
# --------------------------------------------------
xls = pd.ExcelFile(input_file)
sheets = {
    sheet: xls.parse(sheet)
    for sheet in xls.sheet_names
}
xls.close()  # release the OS file handle immediately — this file
             # (SCENARIO_Top100_Final_Rearranged.xlsx) gets deleted during
             # the later cleanup step, and Windows keeps it locked until this
             # handle is explicitly closed, which was causing "file locked"
             # retries/failure there.

# --------------------------------------------------
# LOAD MAIN.CSV (FOR MARGIN)
# --------------------------------------------------
df_main = pd.read_csv(main_file, dtype=str)
df_main.columns = df_main.columns.str.strip()

if "ClientID" in df_main.columns:
    client_col = "ClientID"
elif "Client ID" in df_main.columns:
    client_col = "Client ID"
else:
    raise ValueError("❌ MAIN.csv missing ClientID / Client ID column")

df_main[client_col] = (
    df_main[client_col]
    .astype(str)
    .str.strip()
    .str.upper()
)

df_main["Total Margin Utill"] = (
    pd.to_numeric(
        df_main["Total Margin Utill"],
        errors="coerce"
    )
    .fillna(0)
    .astype(int)
)

# --------------------------------------------------
# HELPER: CLEAN TO INT
# --------------------------------------------------
def clean_to_int(series):
    return (
        pd.to_numeric(
            series.astype(str)
                  .str.replace(",", "")
                  .str.strip(),
            errors="coerce"
        )
        .fillna(0)
        .round(0)
        .astype(int)
    )

# --------------------------------------------------
# PROCESS TOP MARKET UP (TOP 15)
# --------------------------------------------------
if "Top_Market_UP_100" in sheets:
    df_up = sheets["Top_Market_UP_100"].copy()

    for col in ["UP_2%", "UP_4%", "UP_6%", "UP_8%"]:
        if col in df_up.columns:
            df_up[col] = clean_to_int(df_up[col])

    for col in [
        "Deposit(Own Client Fund)",
        "Deposit (After adjustment Short NOV)"
    ]:
        if col in df_up.columns:
            df_up[col] = clean_to_int(df_up[col])

    top_up_15 = (
        df_up
        .sort_values(by="UP_2%", ascending=True)
        .head(15)
    )

    top_up_15 = top_up_15.drop(
        columns=[
            "Total value of Collateral+fund",
            "Product",
            "NOV"
        ],
        errors="ignore"
    )

    top_up_15 = top_up_15.merge(
        df_main[[client_col, "Total Margin Utill"]]
        .rename(columns={client_col: "Client ID"}),
        on="Client ID",
        how="left"
    )

    top_up_15.rename(
        columns={"Total Margin Utill": "MARGIN"},
        inplace=True
    )

    cols = top_up_15.columns.tolist()
    if "MARGIN" in cols and "Deposit (After adjustment Short NOV)" in cols:
        cols.insert(
            cols.index("Deposit (After adjustment Short NOV)") + 1,
            cols.pop(cols.index("MARGIN"))
        )
        top_up_15 = top_up_15[cols]

    sheets["Top_Market_UP_15"] = top_up_15
    sheets.pop("Top_Market_UP_100", None)


# --------------------------------------------------
# PROCESS TOP MARKET DOWN (TOP 15)
# --------------------------------------------------
if "Top_Market_DOWN_100" in sheets:
    df_down = sheets["Top_Market_DOWN_100"].copy()

    for col in ["DOWN_2%", "DOWN_4%", "DOWN_6%", "DOWN_8%"]:
        if col in df_down.columns:
            df_down[col] = clean_to_int(df_down[col])

    for col in [
        "Deposit(Own Client Fund)",
        "Deposit (After adjustment Short NOV)"
    ]:
        if col in df_down.columns:
            df_down[col] = clean_to_int(df_down[col])

    top_down_15 = (
        df_down
        .sort_values(by="DOWN_2%", ascending=True)
        .head(15)
    )

    top_down_15 = top_down_15.drop(
        columns=[
            "Total value of Collateral+fund",
            "Product",
            "NOV"
        ],
        errors="ignore"
    )

    top_down_15 = top_down_15.merge(
        df_main[[client_col, "Total Margin Utill"]]
        .rename(columns={client_col: "Client ID"}),
        on="Client ID",
        how="left"
    )

    top_down_15.rename(
        columns={"Total Margin Utill": "MARGIN"},
        inplace=True
    )

    cols = top_down_15.columns.tolist()
    if "MARGIN" in cols and "Deposit (After adjustment Short NOV)" in cols:
        cols.insert(
            cols.index("Deposit (After adjustment Short NOV)") + 1,
            cols.pop(cols.index("MARGIN"))
        )
        top_down_15 = top_down_15[cols]

    sheets["Top_Market_DOWN_15"] = top_down_15
    sheets.pop("Top_Market_DOWN_100", None)


# --------------------------------------------------
# DROP DEFAULT SHEET
# --------------------------------------------------
sheets.pop("Sheet1", None)

# --------------------------------------------------
# SAVE FINAL OUTPUT
# --------------------------------------------------
with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(
            writer,
            sheet_name=sheet_name,
            index=False
        )

print("✅ Scenario Analysis Top 15 file created:")
print(output_file)


# ==================================================
# STEP O: FINAL FORMATTING – TOP 15 SCENARIO FILE
# ==================================================

print("🎨 Applying formatting to Scenario Analysis Top 15 file")

import openpyxl
from openpyxl.styles import Alignment, Border, Side
from openpyxl.utils import get_column_letter

# --------------------------------------------------
# FILE PATH (STRICT BASE_FOLDER ONLY)
# --------------------------------------------------
file_path = os.path.join(
    BASE_FOLDER,
    "Scenario Analysis_Top_15_Clients.xlsx"
)

# --------------------------------------------------
# LOAD WORKBOOK
# --------------------------------------------------
wb = openpyxl.load_workbook(file_path)

# --------------------------------------------------
# BORDER STYLE
# --------------------------------------------------
thin_border = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin")
)

# --------------------------------------------------
# APPLY FORMATTING TO ALL SHEETS
# --------------------------------------------------
for ws in wb.worksheets:

    # Apply alignment + borders
    for row in ws.iter_rows(
        min_row=1,
        max_row=ws.max_row,
        max_col=ws.max_column
    ):
        for cell in row:
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
                wrap_text=True
            )
            cell.border = thin_border

    # Auto-adjust column widths
    for col in ws.columns:
        max_length = 0
        col_letter = get_column_letter(col[0].column)

        for cell in col:
            try:
                if cell.value is not None:
                    max_length = max(
                        max_length,
                        len(str(cell.value))
                    )
            except Exception:
                pass

        ws.column_dimensions[col_letter].width = max_length + 2

# --------------------------------------------------
# SAVE FORMATTED FILE
# --------------------------------------------------
wb.save(file_path)

print(
    f"✅ Formatting applied successfully "
    f"(borders + center alignment): {file_path}"
)


# ==================================================
# password protection Scenario Analysis - Top 15 file 
# ==================================================

password_protect_excel_force(
    os.path.join(BASE_FOLDER, "Scenario Analysis_Top_15_Clients.xlsx")
)



# ==================================================
# NEW EMAIL Scenario Analysis - Top 100 ALL ZONE
# ==================================================

send_email_infinite_retry(
    subject="Scenario Analysis - Top 100 ALL ZONE",
    body="""Dear Sir,

Please find above subjected file.

Best regards,
Sandeep Kumar
011-42111079
""",
    to_recipients=_SALIST("SA_MAIL_TOP100_TO", ["va@mastertrust.co.in"]),
    cc_recipients=_SALIST("SA_MAIL_TOP100_CC", ["abhishek.dhiman@mastertrust.co.in","priyanshu.karna@mastertrust.co.in","sumit.aggarwal@mastertrust.co.in","sandeep.kumar2@mastertrust.co.in"]),
    attachments=[
        os.path.join(
            BASE_FOLDER,
            f"Scenario Analysis_Top_100_Clients_{EMAIL_DATE}.xlsx"
        )
    ],
    is_html=False
)

# ==================================================
# NEW EMAIL Scenario Analysis - Top 15 ALL ZONE
# ==================================================

send_email_infinite_retry(
    subject="Scenario Analysis - Top 15 ALL ZONE",
    body="""Dear Sir,

Please find above subjected file.

Best regards,
Sandeep Kumar
011-42111079
""",
    to_recipients=_SALIST("SA_MAIL_TOP15_TO", ["rks@mastertrust.co.in"]),
    cc_recipients=_SALIST("SA_MAIL_TOP15_CC", ["abhishek.dhiman@mastertrust.co.in","priyanshu.karna@mastertrust.co.in","sumit.aggarwal@mastertrust.co.in","sandeep.kumar2@mastertrust.co.in"]),
    attachments=[
        os.path.join(
            BASE_FOLDER,
            f"Scenario Analysis_Top_15_Clients_{EMAIL_DATE}.xlsx"
        )
    ],
    is_html=False
)

# ==================================================
# FORCE CLOSE ALL EXCEL INSTANCES (CRITICAL FIX)
# ==================================================
import psutil

for proc in psutil.process_iter(['pid', 'name']):
    try:
        if proc.info['name'] and "EXCEL" in proc.info['name'].upper():
            proc.kill()
            print(f"🛑 Killed Excel process: {proc.info['pid']}")
    except Exception:
        pass

time.sleep(2)



import time

def safe_delete(file_path, retries=10, delay=2):
    """
    Delete file with retry if locked by Excel or another process.
    """
    for i in range(retries):
        try:
            if os.path.exists(file_path):

                # Force permission reset
                os.chmod(file_path, 0o777)

                os.remove(file_path)
                print(f"🗑️ Deleted: {file_path}")
                return True

        except PermissionError:
            print(f"⚠️ File locked, retry {i+1}/{retries}: {file_path}")
            time.sleep(delay)

        except Exception as e:
            print(f"⚠️ Delete error: {e}")
            time.sleep(delay)

    print(f"❌ Could not delete after retries: {file_path}")
    return False


# ==================================================
# FINAL STEP: BACKUP + CLEANUP (SAFE & COMPLETE)
# ==================================================

import os
import shutil
from datetime import datetime

print("📦 Starting BACKUP + CLEANUP")

# --------------------------------------------------
# BASE PATHS
# --------------------------------------------------
BASE = BASE_FOLDER
BACKUP_ROOT = os.path.join(BASE, "BACKUP FILES AUTOMATED")
TODAY = datetime.now().strftime("%d-%m-%Y")
RUN_NO = "4"   # fixed run number as per your logic

RUN_PATH = os.path.join(BACKUP_ROOT, TODAY, RUN_NO)

PICTURES_PATH   = os.path.join(RUN_PATH, "PICTURES")
MAIN_FILES_PATH = os.path.join(RUN_PATH, "MAIN FILES")
OUTPUT_PATH     = os.path.join(RUN_PATH, "OUTPUT")

for p in [PICTURES_PATH, MAIN_FILES_PATH, OUTPUT_PATH]:
    os.makedirs(p, exist_ok=True)

print(f"📂 Using backup folder: {RUN_PATH}")

# ==================================================
# TRACK WHAT WE BACK UP (FOR SAFE DELETE)
# ==================================================
backed_up_files = set()

# ==================================================
# 1️⃣ BACKUP IMAGES
# ==================================================
image_exts = (".jpg", ".jpeg", ".png")

for f in os.listdir(BASE):
    src = os.path.join(BASE, f)

    if not os.path.isfile(src):
        continue

    if f.lower().endswith(image_exts):
        shutil.copy2(src, os.path.join(PICTURES_PATH, f))
        backed_up_files.add(src)

print("🖼️ Images backed up")

# ==================================================
# 2️⃣ BACKUP MAIN INPUT FILES (INCLUDING RISK REPORT)
# ==================================================

# Fixed input files
fixed_inputs = ["main.csv", "scenario.csv"]

for f in fixed_inputs:
    src = os.path.join(BASE, f)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(MAIN_FILES_PATH, f))
        backed_up_files.add(src)

# LAS Debit files (any date)
for f in os.listdir(BASE):
    if f.startswith("LAS Debit Balances-") and f.endswith(".xlsx"):
        src = os.path.join(BASE, f)
        shutil.copy2(src, os.path.join(MAIN_FILES_PATH, f))
        backed_up_files.add(src)

# SA0X files
for f in os.listdir(BASE):
    if f.startswith("SA0") and f.endswith("_OUTPUT FILE.xlsx"):
        src = os.path.join(BASE, f)
        shutil.copy2(src, os.path.join(MAIN_FILES_PATH, f))
        backed_up_files.add(src)

# RISK_REPORT — back up from SA_COMMON_DIR (single shared copy); never delete from there.
_rr_common_files = [f for f in (os.listdir(COMMON_DIR) if os.path.isdir(COMMON_DIR) else [])
                    if f.startswith("RISK_REPORT_") and f.endswith(".csv")]
for f in _rr_common_files:
    src = os.path.join(COMMON_DIR, f)
    dst = os.path.join(MAIN_FILES_PATH, f)
    if not os.path.exists(dst):   # don't overwrite if already there from a prior run
        shutil.copy2(src, dst)
    print(f"📂 RISK_REPORT backed up from common folder → {f}")
if not _rr_common_files:
    print("ℹ️  RISK_REPORT not found in SA_COMMON_DIR — skipping common backup")

print("📥 Main input files (including RISK REPORT) backed up")

# ==================================================
# 3️⃣ BACKUP ALL GENERATED OUTPUT CSV / XLSX
# ==================================================
for f in os.listdir(BASE):
    src = os.path.join(BASE, f)

    if not os.path.isfile(src):
        continue

    # NEVER touch code
    if f.lower().endswith((".py", ".bat")):
        continue

    if f.lower().endswith((".xlsx", ".csv")):
        shutil.copy2(src, os.path.join(OUTPUT_PATH, f))
        backed_up_files.add(src)

print("📤 All CSV/XLSX output files backed up")


# ==================================================
# 3.5️⃣ BACKUP ZONEWISE FILES (NEW ADDITION)
# ==================================================

zonewise_path = os.path.join(BASE, "ZONEWISE")

if os.path.exists(zonewise_path) and os.path.isdir(zonewise_path):
    for f in os.listdir(zonewise_path):
        src = os.path.join(zonewise_path, f)

        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(OUTPUT_PATH, f))
            backed_up_files.add(src)

    print("📁 ZONEWISE files backed up")

else:
    print("ℹ️ ZONEWISE folder not found, skipping backup")


import psutil
import time

# Kill Excel again right before deletion
for proc in psutil.process_iter(['pid', 'name']):
    try:
        if proc.info['name'] and "EXCEL" in proc.info['name'].upper():
            proc.kill()
    except:
        pass

time.sleep(2)


import gc
import time

# Release any file handles still held by Python
gc.collect()

# Give Windows time to release locks
time.sleep(3)
# ==================================================
# 4️⃣ SAFE CLEANUP — DELETE ONLY WHAT WAS BACKED UP
# ==================================================
# ==================================================
# SAFE CLEANUP — DELETE ALL BACKED UP CSV/XLSX
# ==================================================

print("🧹 Starting SAFE cleanup")

for f in os.listdir(BASE):
    full_path = os.path.join(BASE, f)

    if not os.path.isfile(full_path):
        continue

    # Never delete code files
    if f.lower().endswith((".py", ".bat")):
        continue

    # Delete all Excel and CSV files (they are already backed up)
    # Never touch RISK_REPORT — it lives in SA_COMMON_DIR, not here
    if f.startswith("RISK_REPORT_") and f.endswith(".csv"):
        continue
    if f.lower().endswith((".xlsx", ".csv", ".jpg", ".jpeg", ".png")):
                   safe_delete(full_path)

print("✅ BACKUP + CLEANUP COMPLETED SUCCESSFULLY")

# ==================================================
# FINAL CLEANUP: DELETE ZONEWISE FOLDER (AFTER BACKUP)
# ==================================================

zonewise_path = os.path.join(BASE_FOLDER, "ZONEWISE")

if os.path.exists(zonewise_path):
    try:
        shutil.rmtree(zonewise_path)
        print("🗑️ ZONEWISE folder deleted after backup")
    except Exception as e:
        print(f"⚠️ Could not delete ZONEWISE folder: {e}")

# ==================================================
# LAS4 COMPLETE
# ==================================================
_ckpt.las_done(run_no=4)
print("✅ LAS4 complete")
