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
# RUN STATE SYNC — CODE 3
# ==================================================
STATE_FILE = _SAENV("SA_STATE_FILE", r"C:\KARTAVYA\SHORT ALLOCATION\run_state.txt")

if not os.path.exists(STATE_FILE):
    print("❌ run_state.txt not found. Code-2 has not run.")
    sys.exit(1)

state = {}
with open(STATE_FILE) as f:
    for line in f:
        if "=" in line:
            k, v = line.strip().split("=", 1)
            state[k] = v

today = datetime.today().strftime("%Y-%m-%d")
state_date = state.get("DATE")
state_run = int(state.get("RUN", 1))

# Completed run is always RUN - 1
COMPLETED_RUN = state_run - 1
if os.getenv("SA_RUN_NO", "").strip().isdigit():      # console says which run this is
    COMPLETED_RUN = int(os.getenv("SA_RUN_NO"))

# ---------------- SAFETY CHECKS ----------------

# 1️⃣ Code-2 must have run today
if state_date != today:
    print("❌ Code-2 not executed today. Aborting Code-3.")
    sys.exit(1)

# 2️⃣ At least one successful run required
if COMPLETED_RUN < 1:
    print("❌ No successful Code-2 run yet. Aborting Code-3.")
    sys.exit(1)

# 3️⃣ Allow only first 3 runs
if COMPLETED_RUN > 3:
    print("❌ Code-3 allowed only for runs 1, 2, 3.")
    sys.exit(0)

print(f"✅ Code-3 authorised for RUN {COMPLETED_RUN}")



# ==================================================
# PRE-FLIGHT FILE VALIDATION (SIMPLE & CLEAN)
# ==================================================


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

_common_files_list = os.listdir(COMMON_DIR) if os.path.isdir(COMMON_DIR) else []
if not any(f.startswith("RISK_REPORT_") and f.endswith(".csv") for f in all_files + _common_files_list):
    missing.append("RISK_REPORT file (not found in LAS FUNDING or COMMON FILES)")

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
print("⏱️ Checking file freshness (45 min rule)")

def is_file_fresh(file_path, minutes=45):
    if not os.path.exists(file_path):
        return False
    
    file_time = os.path.getmtime(file_path)
    current_time = datetime.now().timestamp()
    
    age_minutes = (current_time - file_time) / 60
    return age_minutes <= minutes

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
#   1. SA_COMMON_DIR  (C:\COMMON FILES)   ← preferred shared location
#   2. BASE_FOLDER    (C:\LAS FUNDING)    ← legacy fallback
_risk_candidates = [
    os.path.join(COMMON_DIR,   f"RISK_REPORT_{today_csv}.csv"),
    os.path.join(BASE_FOLDER,  f"RISK_REPORT_{today_csv}.csv"),
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
    _ckpt.las_step("DATA_LOAD", script="LASUPTO3")
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
    _ckpt.las_step("SA_COMPUTE", script="LASUPTO3")
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

if not vertical_df.empty:
    vertical_df.to_excel(vertical_output, index=False)
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
    print("⚠️ vertical_1 file not found — skipping formatting & screenshot")

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

    wb.save(vertical_file)

    print(f"✅ vertical_1.xlsx formatted successfully: {vertical_file}")

if _ckpt.las_should_run("SCREENSHOTS"):
    _ckpt.las_step("SCREENSHOTS", script="LASUPTO3")


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

    # --------------------------------------------------
    # 11.2 Style header row (row 2)
    # --------------------------------------------------
    header_row = 2
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin")
    )

    for col in range(1, max_col + 1):
        cell = ws.cell(row=header_row, column=col)
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
    text_cols = ["ZONE", "TradingCode", "client_name"]

    for row in ws.iter_rows(min_row=3, max_row=ws.max_row, max_col=max_col):
        for cell in row:
            col_name = ws.cell(row=2, column=cell.column).value
            if col_name in text_cols:
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            else:
                cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)

    # --------------------------------------------------
    # 11.4 Adjust column widths
    # --------------------------------------------------
    ws.column_dimensions["C"].width = 25

    # --------------------------------------------------
    # 11.5 Save workbook
    # --------------------------------------------------
    wb.save(sect_file_path)

    print(f"✅ SECT_CODE.xlsx formatted successfully: {sect_file_path}")

# ==================================================
if _ckpt.las_should_run("ZONE_PROCESS"):
    _ckpt.las_step("ZONE_PROCESS", script="LASUPTO3")

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



# ==================================================
if _ckpt.las_should_run("RISK_NOV"):
    _ckpt.las_step("RISK_NOV", script="LASUPTO3")

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
if _ckpt.las_should_run("WA_IMAGES"):
    _ckpt.las_step("WA_IMAGES", script="LASUPTO3")

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


# ==================================================
if _ckpt.las_should_run("WA_SEND"):
    _ckpt.las_step("WA_SEND", script="LASUPTO3")

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

# --------------------------------------------------
# SEND MAP (FILE → TITLE → RECIPIENTS)
# --------------------------------------------------
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
for zone, cfg in SEND_MAP.items():
    print(f"\n📤 Processing WhatsApp for {zone}")

    if not os.path.exists(cfg["image"]):
        print(f"⚠️ Image not found, skipping: {cfg['image']}")
        continue

    media_id = upload_image(cfg["image"])
    if not media_id:
        print("❌ Upload failed, skipping send")
        continue

    send_whatsapp(media_id, cfg["title"], cfg["recipients"])

print("\n🎉 ALL WHATSAPP MESSAGES PROCESSED")



# ==================================================
# STEP 17: AUTOMATED BACKUP + CLEANUP (FINAL SAFE)
# ==================================================

import shutil
from datetime import datetime

print("📦 Starting automated backup & cleanup")

# ------------------------------
# BACKUP ROOT
# ------------------------------
BACKUP_ROOT = os.path.join(BASE_FOLDER, "BACKUP FILES AUTOMATED")
TODAY_STR = datetime.now().strftime("%d-%m-%Y")
TODAY_PATH = os.path.join(BACKUP_ROOT, TODAY_STR)

os.makedirs(TODAY_PATH, exist_ok=True)

# ------------------------------
# RUN NUMBER (1 / 2 / 3)
# ------------------------------
existing_runs = [
    d for d in os.listdir(TODAY_PATH)
    if d.isdigit()
]

if len(existing_runs) >= 3:
    print("❌ Already 3 backups today. Skipping backup & cleanup.")
    exit(0)

RUN_NO = str(len(existing_runs) + 1)
RUN_PATH = os.path.join(TODAY_PATH, RUN_NO)

# ------------------------------
# CREATE STRUCTURE
# ------------------------------
PICTURES_PATH = os.path.join(RUN_PATH, "PICTURES")
MAIN_FILES_PATH = os.path.join(RUN_PATH, "MAIN FILES")
OUTPUT_PATH = os.path.join(RUN_PATH, "OUTPUT")

os.makedirs(PICTURES_PATH)
os.makedirs(MAIN_FILES_PATH)
os.makedirs(OUTPUT_PATH)

print(f"📂 Backup folder created: {RUN_PATH}")

# ==================================================
# BACKUP 1️⃣ : JPG FILES
# ==================================================
for root, _, files in os.walk(BASE_FOLDER):
    if "BACKUP FILES AUTOMATED" in root:
        continue

    for f in files:
        if f.lower().endswith(".jpg"):
            src = os.path.join(root, f)
            shutil.copy2(src, os.path.join(PICTURES_PATH, f))

print("🖼️ Pictures backed up")

# ==================================================
# BACKUP 2️⃣ : MAIN INPUT FILES (NO RISK FILE)
# ==================================================

main_inputs = [
    las_file,
    main_csv,
    scenario_csv
]

main_inputs.extend(
    glob.glob(os.path.join(BASE_FOLDER, "SA0*_OUTPUT FILE.xlsx"))
)

for f in main_inputs:
    if os.path.exists(f):
        shutil.copy2(f, os.path.join(MAIN_FILES_PATH, os.path.basename(f)))

print("📥 Main input files backed up (risk excluded)")

# ==================================================
# BACKUP 3️⃣ : OUTPUT FILES (.xlsx / .csv)
# ==================================================

for root, _, files in os.walk(BASE_FOLDER):
    if "BACKUP FILES AUTOMATED" in root:
        continue

    for f in files:
        if f.lower().endswith((".xlsx", ".csv")):
            full = os.path.join(root, f)

            # ❌ never copy risk report
            if os.path.basename(full) == os.path.basename(risk_file):
                continue

            shutil.copy2(full, os.path.join(OUTPUT_PATH, f))

print("📤 Output files backed up")


# ==================================================
# SAFE CLEANUP — DELETE ONLY PROGRAM FILES
# ==================================================

print("🧹 Starting SAFE cleanup (program files only)")

files_to_delete = set()

# ------------------------------
# INPUT FILES (EXCEPT RISK)
# ------------------------------
files_to_delete.update([
    las_file,
    main_csv,
    scenario_csv
])

# SA0X files
files_to_delete.update(
    glob.glob(os.path.join(BASE_FOLDER, "SA0*_OUTPUT FILE.xlsx"))
)

# ------------------------------
# OUTPUT FILES CREATED BY SCRIPT
# ------------------------------
generated_outputs = [
    final_output,
    filtered_output,
    vertical_output,
    vertical2_output,
    merged_file,
    client_output,
    sect_output,
    os.path.join(BASE_FOLDER, "LAS_FUNDING_MERGED.xlsx"),
    os.path.join(BASE_FOLDER, "LAS_FUNDING_OUTPUT_FILTERED.xlsx"),
    os.path.join(BASE_FOLDER, "VERTICAL_1.xlsx"),
    os.path.join(BASE_FOLDER, "VERTICAL_2.xlsx"),
    os.path.join(BASE_FOLDER, "SECT_CODE.xlsx"),
]

# Zone-wise outputs
zone_folder = os.path.join(BASE_FOLDER, "ZONEWISE")
if os.path.exists(zone_folder):
    for f in os.listdir(zone_folder):
        files_to_delete.add(os.path.join(zone_folder, f))

files_to_delete.update(generated_outputs)

# ------------------------------
# IMAGE FILES CREATED
# ------------------------------
image_files = [
    "FUNDING_SS.JPG",
    "CHND_SS.JPG",
    "LUDH_SS.JPG",
    "MUMB_SS.JPG",
    "VERTICAL_1_SS.JPG",
    "VERTICAL_2_SS.JPG",
    "6NBB.JPG"
]

for img in image_files:
    files_to_delete.add(os.path.join(BASE_FOLDER, img))

# ------------------------------
# FINAL DELETE (STRICT)
# ------------------------------
for file_path in files_to_delete:
    if not file_path:
        continue

    if not os.path.exists(file_path):
        continue

    # Absolute safety guards
    if file_path.endswith((".py", ".bat")):
        continue

    # ❌ NEVER delete LAS Debit Balance file
    if os.path.basename(file_path) == os.path.basename(las_file):
        continue

    try:
        os.remove(file_path)
        print(f"🗑️ Deleted: {file_path}")
    except Exception as e:
        print(f"⚠️ Could not delete {file_path}: {e}")

_ckpt.las_done()
print('✅ LASUPTO3 complete')
