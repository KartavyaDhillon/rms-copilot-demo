import os
import re
import pandas as pd
import numpy as np
from openpyxl.utils import get_column_letter
from openpyxl import load_workbook
from datetime import datetime

# ---------- CONFIG ----------
FOLDER = r"C:\Rajeev Ji"

# ---------- DOWNSTREAM FOLDERS ----------
# The renamed <date>_MASTER.xlsx is copied into both of these automatically
# right after the rename step, so the next two scripts in the chain
# (Live_Allocation_final.py and MCX_INTRSAR_final.py) find it without anyone
# having to move it by hand. Both scripts treat the Master file as MANDATORY.
LIVE_ALLOCATION_FOLDER = r"C:\LIVE ALLOCATION"
MCX_INTRA_FOLDER       = r"C:\MCX_INTRA_SAR"

import gzip
import shutil

# ---------- AUTO-EXTRACT .GZ FILES ----------
for file in os.listdir(FOLDER):
    if file.lower().endswith(".gz"):
        gz_path = os.path.join(FOLDER, file)
        out_filename = file[:-3]  # remove .gz extension (e.g., .csv.gz -> .csv)
        out_path = os.path.join(FOLDER, out_filename)

        # Skip if already extracted
        if os.path.exists(out_path):
            print(f"⏩ Skipped (already exists): {out_filename}")
            continue

        # Extract .gz file
        with gzip.open(gz_path, 'rb') as f_in:
            with open(out_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        # Delete the original .gz after successful extraction
        os.remove(gz_path)
        print(f"✅ Extracted & removed: {file} -> {out_filename}")




import os
import re
import sys

# ---------------- FOLDER PATH ----------------
FOLDER = r"C:\Rajeev Ji"

# ---------------- REQUIRED FILES ----------------
# NOTE (NCDEX 8-file change):
# NCDEX now publishes NCCL_MARGIN_REP_01292_<date>_01 .. _08 (was _01 .. _05).
# NCD6 / NCD7 / NCD8 are HARD-MANDATORY here, exactly like NCD1..NCD5.
# If any of them is missing, strict_file_check() calls sys.exit(1) and the
# whole run stops - no Master file is built and NO email is sent.
required_files_for_check = {
    "CM1": [r"Margin_NCL_CM_0_CM_06436_\d{8}_P_1100\.(csv|xlsx)"],
    "CM2": [r"Margin_NCL_CM_0_CM_06436_\d{8}_P_1230\.(csv|xlsx)"],
    "CM3": [r"Margin_NCL_CM_0_CM_06436_\d{8}_P_1430\.(csv|xlsx)"],
    "CM4": [r"Margin_NCL_CM_0_CM_06436_\d{8}_P_1530\.(csv|xlsx)"],
    "Final_CM": [r"C_MG13_06436_\d{8}\.lis"],
    "FO1": [r"Margin_NCL_FO_0_CM_06436_\d{8}_P_1100\.(csv|xlsx)"],
    "FO2": [r"Margin_NCL_FO_0_CM_06436_\d{8}_P_1230\.(csv|xlsx)"],
    "FO3": [r"Margin_NCL_FO_0_CM_06436_\d{8}_P_1430\.(csv|xlsx)"],
    "FO4": [r"Margin_NCL_FO_0_CM_06436_\d{8}_P_1530\.(csv|xlsx)"],
    "Final_Fut": [r"Margin_NCL_FO_0_CM_06436_\d{8}_F_0000\.(csv|xlsx)"],
    "MCX1": [r"MCX_PeakMargin56720_\d{8}_01\.csv"],
    "MCX2": [r"MCX_PeakMargin56720_\d{8}_02\.csv"],
    "MCX3": [r"MCX_PeakMargin56720_\d{8}_03\.csv"],
    "MCX4": [r"MCX_PeakMargin56720_\d{8}_04\.csv"],
    "MCX5": [r"MCX_PeakMargin56720_\d{8}_05\.csv"],
    "MCX6": [r"MCX_PeakMargin56720_\d{8}_06\.csv"],
    "MCX7": [r"MCX_PeakMargin56720_\d{8}_07\.csv"],
    "MCX8": [r"MCX_PeakMargin56720_\d{8}_08\.csv"],
    "NCD1": [r"NCCL_MARGIN_REP_01292_\d{8}_01\.csv"],
    "NCD2": [r"NCCL_MARGIN_REP_01292_\d{8}_02\.csv"],
    "NCD3": [r"NCCL_MARGIN_REP_01292_\d{8}_03\.csv"],
    "NCD4": [r"NCCL_MARGIN_REP_01292_\d{8}_04\.csv"],
    "NCD5": [r"NCCL_MARGIN_REP_01292_\d{8}_05\.csv"],
    "NCD6": [r"NCCL_MARGIN_REP_01292_\d{8}_06\.csv"],   # NEW - mandatory
    "NCD7": [r"NCCL_MARGIN_REP_01292_\d{8}_07\.csv"],   # NEW - mandatory
    "NCD8": [r"NCCL_MARGIN_REP_01292_\d{8}_08\.csv"],   # NEW - mandatory
    "NCD_FINAL":[r"01292_MGN_\d{14}\.CSV"]
    }

# ---------------- NEW FILE CHECK FUNCTION ----------------
def strict_file_check(folder, required_files):
    """
    Checks for all required files in the folder.
    Stops execution if any file is missing.
    """
    missing_files = []

    all_files = os.listdir(folder)
    for key, patterns in required_files.items():
        found = False
        for pattern in patterns:
            regex = re.compile(pattern, re.IGNORECASE)
            for f in all_files:
                if regex.fullmatch(f):
                    found = True
                    break
            if found:
                break
        if not found:
            missing_files.append(key)

    if missing_files:
        print("❌ Critical: Missing required files. Stopping execution.")
        for f in missing_files:
            print(f" - {f}")
        sys.exit(1)
    else:
        print("✅ All critical files are present. Continuing...")

# ---------------- RUN STRICT CHECK ----------------
strict_file_check(FOLDER, required_files_for_check)





from datetime import datetime, timedelta

# ---------- CALCULATE PREVIOUS TRADING DAY ----------
def get_previous_trading_day(ref_date=None):
    if ref_date is None:
        ref_date = datetime.now()
    weekday = ref_date.weekday()
    # Monday = 0, Sunday = 6
    if weekday == 0:  # Monday
        prev_trading_day = ref_date - timedelta(days=3)  # Friday
    elif weekday == 6:  # Sunday
        prev_trading_day = ref_date - timedelta(days=2)  # Friday
    elif weekday == 5:  # Saturday
        prev_trading_day = ref_date - timedelta(days=1)  # Friday
    else:
        prev_trading_day = ref_date - timedelta(days=1)  # Other weekdays
    return prev_trading_day

prev_trading_day = get_previous_trading_day()
today_str = prev_trading_day.strftime("%d%m%Y")
OUTPUT_FILE = os.path.join(FOLDER, f"{today_str}_MASTER.xlsx")

CM_TIMES = {"CM1": "1100", "CM2": "1230", "CM3": "1430", "CM4": "1530"}
FO_TIMES = {"FO1": "1100", "FO2": "1230", "FO3": "1430", "FO4": "1530"}
MCX_COUNT = 8
# ===== CHANGED: NCDEX peak files moved from 5 to 8 (01..08) =====
# This single constant drives:
#   - the NCD read loop
#   - the NCDEX1..NCDEX8 columns in the "Main" sheet
#   - the NCD1..NCD8 raw sheets in the output workbook
NCD_MAX_SHEETS = 8

# ---------- HELPERS ----------




def normalize_colname(c):
    return str(c).strip().lower()

def parse_numeric_series(s):
    s_clean = s.astype(str).str.replace(r'[,\s₹]', '', regex=True)
    s_clean = s_clean.str.replace(r'[\(\)]', '', regex=True)
    return pd.to_numeric(s_clean, errors='coerce')

def normalize_clntid_series(series):
    return series.astype(str).str.strip()

def safe_read(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in [".xls", ".xlsx"]:
        # Read all columns as string to preserve ClntId exactly
        return pd.read_excel(path, dtype=str)
    else:
        # Read CSV with all columns as string
        return pd.read_csv(path, dtype=str)

def get_column(df, col_name):
    matches = [c for c in df.columns if normalize_colname(c) == col_name.lower()]
    if not matches:
        raise ValueError(f"Column '{col_name}' not found in file.")
    return matches[0]

# --- Dynamic date extractors ---
def extract_date(filename, pattern, fmt="YYYYMMDD"):
    """
    Extracts date from filename using regex pattern.
    fmt can be 'YYYYMMDD', 'DDMMYY', 'DDMMYYYY'.
    """
    m = re.search(pattern, filename)
    if not m:
        return None
    date_str = m.group(1)
    if fmt == "YYYYMMDD":
        return date_str
    elif fmt == "DDMMYY":
        return datetime.strptime(date_str, "%d%m%y").strftime("%Y%m%d")
    elif fmt == "DDMMYYYY":
        return datetime.strptime(date_str, "%d%m%Y").strftime("%Y%m%d")
    return None

def find_file_with_date(folder, prefix, time_suffix=None, regex_pattern=None, fmt="YYYYMMDD"):
    for f in os.listdir(folder):
        if f.startswith(prefix) and "_P_" in f:
            if regex_pattern:
                date = extract_date(f, regex_pattern, fmt)
                if date:
                    if time_suffix is None or time_suffix in f:
                        return os.path.join(folder, f), date
    return None, None
def find_fo_file_exact(folder, time_suffix):
    """
    Strictly find FO P-time file like _P_1230.csv
    Avoids date collisions like 20251230.
    """
    for f in os.listdir(folder):
        if (
            f.startswith("Margin_NCL_FO_0_CM_06436_")
            and f"_P_{time_suffix}." in f
            and f.lower().endswith((".csv", ".xlsx"))
        ):
            return os.path.join(folder, f)
    return None




# ---------- MAIN ----------
cm_dataframes = {}
fo_dataframes = {}
mcx_dataframes = {}
ncd_dataframes = {}
main_df = pd.DataFrame()
final_fo_sheet = None
lis_raw_df = None
final_ncd_df = None

# --- CM FILES ---
for sheet, time_suffix in CM_TIMES.items():
    path, cm_date = find_file_with_date(FOLDER, "Margin_NCL_CM", time_suffix, r"_(\d{8})_", "YYYYMMDD")
    if not path:
        cm_dataframes[sheet] = pd.DataFrame({"Info": [f"No file found for {sheet}"]})
        continue

    df = safe_read(path)
    try:
        clnt_col = get_column(df, "ClntId")
        min_col = get_column(df, "MinMrgn")
    except ValueError as e:
        cm_dataframes[sheet] = pd.DataFrame({"Info": [str(e)]})
        continue

    df_small = df[[clnt_col, min_col]].copy()
    df_small.columns = ["ClntId", sheet]
    df_small["ClntId"] = normalize_clntid_series(df_small["ClntId"])
    df_small[sheet] = parse_numeric_series(df_small[sheet])
    df_small = df_small.drop_duplicates(subset=["ClntId"], keep="first")

    cm_dataframes[sheet] = df.copy()
    if main_df.empty:
        main_df = df_small
    else:
        main_df = pd.merge(main_df, df_small, on="ClntId", how="outer")


# --- FO FILES (Corrected) ---
fo_mapping = { "1100": "FO1", "1230": "FO2", "1430": "FO3", "1530": "FO4" }

for suffix, sheet_name in fo_mapping.items():
    path = find_fo_file_exact(FOLDER, suffix)
    if not path:
        fo_dataframes[sheet_name] = pd.DataFrame({"Info": [f"No file found for {sheet_name}"]})
        main_df["FM" + sheet_name[-1]] = 0
        continue

    df = safe_read(path)


    try:
        clnt_col = get_column(df, "ClntId")
        eod_col = get_column(df, "EndOfDayRqrmntRptd")
    except ValueError as e:
        fo_dataframes[sheet_name] = pd.DataFrame({"Info": [str(e)]})
        main_df["FM" + sheet_name[-1]] = 0
        continue

    df_small = df[[clnt_col, eod_col]].copy()
    df_small.columns = ["ClntId", "FM" + sheet_name[-1]]
    df_small["ClntId"] = normalize_clntid_series(df_small["ClntId"])
    df_small["FM" + sheet_name[-1]] = parse_numeric_series(df_small["FM" + sheet_name[-1]])
    df_small = df_small.drop_duplicates(subset=["ClntId"], keep="first")

    fo_dataframes[sheet_name] = df.copy()
    main_df = pd.merge(main_df, df_small, on="ClntId", how="outer")

# --- LIS FILE ---
for f in os.listdir(FOLDER):
    if f.startswith("C_MG13") and f.lower().endswith(".lis"):
        lis_date = extract_date(f, r"_(\d{6})\.lis", "DDMMYY")
        lis_path = os.path.join(FOLDER, f)
        lis_raw_df = pd.read_csv(lis_path, header=None)
        lis_extract = lis_raw_df.iloc[:, [1, 3]].copy()
        lis_extract.columns = ["ClntId", "Final Cash"]
        lis_extract["ClntId"] = normalize_clntid_series(lis_extract["ClntId"])
        lis_extract["Final Cash"] = parse_numeric_series(lis_extract["Final Cash"])
        main_df = pd.merge(main_df, lis_extract, on="ClntId", how="outer")
        break

# --- FINAL F FILE (Final_FO / Final Fut) ---
import datetime

FINAL_F_PREFIX = "Margin_NCL_FO_0_CM_06436_"
FINAL_F_SUFFIX = "_F_0000"

def find_final_f_file_any_date(folder):
    """
    Find the Final_FO file dynamically based on prefix and suffix,
    ignoring the date in the filename. Returns the first match found.
    """
    for f in os.listdir(folder):
        if f.startswith(FINAL_F_PREFIX) and FINAL_F_SUFFIX in f and f.lower().endswith((".csv", ".xlsx")):
            return os.path.join(folder, f)
    return None

# Usage
final_f_path = find_final_f_file_any_date(FOLDER)
if final_f_path:
    print(f"✅ Final_Fut file found: {final_f_path}")
else:
    print("❌ No Final_Fut file found")


if final_f_path:
    df = safe_read(final_f_path)

    # Filter rows where TckrSymb is empty or NaN
    tckr_col = get_column(df, "TckrSymb")
    df_filtered = df[df[tckr_col].isna() | (df[tckr_col].astype(str).str.strip() == "")].copy()

    # Compute margin
    prm_col = get_column(df_filtered, "PrmAmt")
    span_col = get_column(df_filtered, "SPANMrgnMin")
    xtrm_col = get_column(df_filtered, "XtrmLossMrgnMin")
    clnt_col = get_column(df_filtered, "ClntId")

    df_filtered["Margin"] = (
        parse_numeric_series(df_filtered[prm_col])
        + parse_numeric_series(df_filtered[span_col])
        + parse_numeric_series(df_filtered[xtrm_col])
    )

    # Merge into main_df as Final Fut column
    df_main_final_fut = df_filtered[[clnt_col, "Margin"]].copy()
    df_main_final_fut.columns = ["ClntId", "Final Fut"]
    df_main_final_fut["ClntId"] = normalize_clntid_series(df_main_final_fut["ClntId"])
    main_df = pd.merge(main_df, df_main_final_fut, on="ClntId", how="outer")

    # Store for extra sheet
    df_filtered["ClntId"] = normalize_clntid_series(df_filtered["ClntId"])
    final_fo_sheet = df_filtered.copy()
else:
    # If file not found, create empty Final Fut column
    if "Final Fut" not in main_df.columns:
        main_df["Final Fut"] = 0
    final_fo_sheet = pd.DataFrame({"Info": ["No Final FO file found"]})




# --- FINAL F_NCD FILE ---
for f in os.listdir(FOLDER):
    if f.startswith("01292_MGN_") and f.lower().endswith(".csv"):
        f_date = extract_date(f, r"_(\d{8})\d{6}\.csv", "DDMMYYYY")
        final_ncd_file = os.path.join(FOLDER, f)
        df = pd.read_csv(final_ncd_file)
        final_ncd_df = df.copy()
        try:
            clnt_col = get_column(df, "Client ID")
            val_col = get_column(df, "Initial Mrgin (Initial + Extreme Loss)")
            df_small = df[[clnt_col, val_col]].copy()
            df_small["ClntId"] = df_small[clnt_col].astype(str).str.replace("'", "").str.strip().str.upper()
            df_small[f"FINAL_NCDEX"] = parse_numeric_series(df_small[val_col])
            df_small = df_small[["ClntId", f"FINAL_NCDEX"]]
            main_df = pd.merge(main_df, df_small, on="ClntId", how="outer")
        except ValueError:
            pass
        break

# --- MCX FILES ---
for f in os.listdir(FOLDER):
    if f.startswith("MCX_PeakMargin56720_") and f.lower().endswith(".csv"):
        mcx_date = extract_date(f, r"_(\d{8})_", "YYYYMMDD")
        # loop through 01..08
        for i in range(1, MCX_COUNT + 1):
            filename = f"MCX_PeakMargin56720_{mcx_date}_{str(i).zfill(2)}.csv"
            path = os.path.join(FOLDER, filename)
            if os.path.exists(path):
                df = pd.read_csv(path, header=None)
                mcx_dataframes[f"MCX{i}"] = df.copy()
                df_small = df.iloc[:, [4, 8]].copy()
                df_small.columns = ["ClntId", f"MCX{i}"]
                df_small["ClntId"] = normalize_clntid_series(df_small["ClntId"])
                df_small[f"MCX{i}"] = parse_numeric_series(df_small[f"MCX{i}"])
                main_df = pd.merge(main_df, df_small, on="ClntId", how="outer")
            else:
                mcx_dataframes[f"MCX{i}"] = pd.DataFrame()
                main_df[f"MCX{i}"] = 0
        break

# --- NCD FILES ---
# ===== CHANGED: now loops 01..08 because NCD_MAX_SHEETS = 8 =====
# Any of _06/_07/_08 that has not arrived is safely handled by the else-branch
# (column set to 0 + empty sheet), so the run never breaks on a partial day.
for f in os.listdir(FOLDER):
    if f.startswith("NCCL_MARGIN_REP_01292_") and f.lower().endswith(".csv"):
        ncd_date = extract_date(f, r"_(\d{8})_", "YYYYMMDD")
        for i in range(1, NCD_MAX_SHEETS + 1):
            filename = f"NCCL_MARGIN_REP_01292_{ncd_date}_{str(i).zfill(2)}.csv"
            path = os.path.join(FOLDER, filename)
            if os.path.exists(path):
                df = pd.read_csv(path)
                ncd_dataframes[f"NCD{i}"] = df.copy()
                try:
                    clnt_col = get_column(df, "Client Code")
                    init_col = get_column(df, "Initial Margin")
                    elm_col = get_column(df, "ELM Margin")
                except ValueError:
                    main_df[f"NCDEX{i}"] = 0
                    continue
                df_small = df[[clnt_col, init_col, elm_col]].copy()
                df_small["ClntId"] = normalize_clntid_series(df_small[clnt_col])
                df_small[f"NCDEX{i}"] = parse_numeric_series(df_small[init_col]) + parse_numeric_series(df_small[elm_col])
                df_small = df_small[["ClntId", f"NCDEX{i}"]]
                main_df = pd.merge(main_df, df_small, on="ClntId", how="outer")
                print(f"✅ NCDEX peak file loaded: {filename}")
            else:
                main_df[f"NCDEX{i}"] = 0
                ncd_dataframes[f"NCD{i}"] = pd.DataFrame()
                print(f"⚠️ NCDEX peak file NOT found (column set to 0): {filename}")
        break


# --- NEW: NCL_COMM / NSE_COMM FILES ---
nce_times = ["1100", "1230", "1430", "1530", "1700", "1900", "2030", "2230"]
nce_columns = [f"NSE_COMM_{i}" for i in range(1, 9)]
nce_dataframes = {}

# Loop through 8 regular files
for idx, time_suffix in enumerate(nce_times):
    col_name = nce_columns[idx]
    pattern = f"_{time_suffix}"
    file_found = False

    for f in os.listdir(FOLDER):
        if f.startswith("Margin_NCL_CO_0_CM_06436") and pattern in f and f.lower().endswith((".csv", ".xlsx")):
            path = os.path.join(FOLDER, f)
            df = safe_read(path)

            # Extract EndOfDayRqrmntRptd column into main_df
            try:
                clnt_col = get_column(df, "ClntId")  # or Client Code if needed
                eod_col = get_column(df, "EndOfDayRqrmntRptd")
                df_small = df[[clnt_col, eod_col]].copy()
                df_small.columns = ["ClntId", col_name]
                df_small["ClntId"] = normalize_clntid_series(df_small["ClntId"])
                df_small[col_name] = parse_numeric_series(df_small[col_name])
                df_small = df_small.drop_duplicates(subset=["ClntId"], keep="first")
                main_df = pd.merge(main_df, df_small, on="ClntId", how="outer")
            except ValueError:
                main_df[col_name] = 0

            nce_dataframes[f"NSE_COMM{idx+1}"] = df.copy()
            file_found = True
            break

    if not file_found:
        main_df[col_name] = 0
        nce_dataframes[f"NSE_COMM{idx+1}"] = pd.DataFrame({"Info":[f"No file found for NSE_COMM{idx+1}"]})

# Final NSE_COMM file (0000)
final_file = None
for f in os.listdir(FOLDER):
    if f.startswith("Margin_NCL_CO_0_CM_06436") and "_0000" in f and f.lower().endswith((".csv", ".xlsx")):
        final_file = os.path.join(FOLDER, f)
        break

if final_file:
    df = safe_read(final_file)
    try:
        clnt_col = get_column(df, "ClntId")
        eod_col = get_column(df, "EndOfDayRqrmntRptd")
        df_small = df[[clnt_col, eod_col]].copy()
        df_small.columns = ["ClntId", "NSE_COMM_FINAL"]
        df_small["ClntId"] = normalize_clntid_series(df_small["ClntId"])
        df_small["NSE_COMM_FINAL"] = parse_numeric_series(df_small["NSE_COMM_FINAL"])
        df_small = df_small.drop_duplicates(subset=["ClntId"], keep="first")
        main_df = pd.merge(main_df, df_small, on="ClntId", how="outer")
    except ValueError:
        main_df["NSE_COMM_FINAL"] = 0

    nce_dataframes["NSE_COMM_FINAL"] = df.copy()
else:
    main_df["NSE_COMM_FINAL"] = 0
    nce_dataframes["NSE_COMM_FINAL"] = pd.DataFrame({"Info":["No NSE_COMM_FINAL file found"]})




# --- Ensure column order in MAIN sheet ---
# --- Ensure column order in MAIN sheet (updated with NSE_COMM & NSE_COMM_FINAL) ---
# ===== CHANGED: NCDEX1..NCDEX8 now generated (driven by NCD_MAX_SHEETS) =====
main_columns = (
    ["ClntId"]
    + list(CM_TIMES.keys())
    + ["Final Cash"]
    + [f"FM{i}" for i in range(1,5)]
    + ["Final Fut"]
    + [f"MCX{i}" for i in range(1, MCX_COUNT+1)]
    + [f"NCDEX{i}" for i in range(1, NCD_MAX_SHEETS+1)]
    + ["FINAL_NCDEX"]
    + [f"NSE_COMM_{i}" for i in range(1, 9)]  # New 8 columns
    + ["NSE_COMM_FINAL"]                        # Final column
)

# Add missing columns as zeros
for c in main_columns:
    if c not in main_df.columns:
        main_df[c] = 0

# Reorder columns and fill NaNs with 0
main_df = main_df[main_columns].fillna(0)


# --- CLEAN ClntId FOR EXCEL (KEEP EXACT ORIGINAL VALUE) ---
def clean_clntid_for_excel(df, col="ClntId"):
    # Only try if the column exists
    if df is not None and col in df.columns:
        # Remove hidden/non-printable chars, keep leading zeros, do NOT convert to int
        df[col] = df[col].astype(str).str.strip()
        for _z in ("\u200b", "\u200c", "\u200d", "\ufeff"):          # plain replace: the arrow string type has no \u in its regex
            df[col] = df[col].str.replace(_z, "", regex=False)
    return df

# --- APPLY ClntId CLEANING TO ALL SHEETS ---
main_df = clean_clntid_for_excel(main_df)

for sheet in ["CM1","CM2","CM3","CM4"]:
    cm_dataframes[sheet] = clean_clntid_for_excel(cm_dataframes.get(sheet, pd.DataFrame()))

for sheet in ["FO1","FO2","FO3","FO4"]:
    fo_dataframes[sheet] = clean_clntid_for_excel(fo_dataframes.get(sheet, pd.DataFrame()))

final_fo_sheet = clean_clntid_for_excel(final_fo_sheet)

for i in range(1, MCX_COUNT+1):
    mcx_dataframes[f"MCX{i}"] = clean_clntid_for_excel(mcx_dataframes.get(f"MCX{i}", pd.DataFrame()))

# ===== CHANGED: now cleans NCD1..NCD8 =====
for i in range(1, NCD_MAX_SHEETS+1):
    ncd_dataframes[f"NCD{i}"] = clean_clntid_for_excel(ncd_dataframes.get(f"NCD{i}", pd.DataFrame()))

final_ncd_df = clean_clntid_for_excel(final_ncd_df)




# --- WRITE TO EXCEL ---
with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
    main_df.to_excel(writer, sheet_name="Main", index=False)

    for sheet in ["CM1","CM2","CM3","CM4"]:
        df_to_write = cm_dataframes.get(sheet, pd.DataFrame())
        if df_to_write.empty: df_to_write = pd.DataFrame({"Info":[f"No data for {sheet}"]})
        df_to_write.to_excel(writer, sheet_name=sheet, index=False)

    if lis_raw_df is not None and not lis_raw_df.empty:
        lis_raw_df.to_excel(writer, sheet_name="Final_CM", index=False, header=False)
    else:
        pd.DataFrame({"Info":["No LIS file found"]}).to_excel(writer, sheet_name="Final_CM", index=False)

    for sheet in ["FO1","FO2","FO3","FO4"]:
        df_to_write = fo_dataframes.get(sheet, pd.DataFrame())
        if df_to_write.empty: df_to_write = pd.DataFrame({"Info":[f"No data for {sheet}"]})
        df_to_write.to_excel(writer, sheet_name=sheet, index=False)

    if final_fo_sheet is not None and not final_fo_sheet.empty:
        final_fo_sheet.to_excel(writer, sheet_name="Final_FO", index=False)
    else:
        pd.DataFrame({"Info":["No Final FO file found"]}).to_excel(writer, sheet_name="Final_FO", index=False)

    for i in range(1, MCX_COUNT+1):
        df_to_write = mcx_dataframes.get(f"MCX{i}", pd.DataFrame())
        if df_to_write.empty: df_to_write = pd.DataFrame({"Info":[f"No data for MCX{i}"]})
        df_to_write.to_excel(writer, sheet_name=f"MCX{i}", index=False)

    # ===== CHANGED: writes NCD1..NCD8 sheets =====
    for i in range(1, NCD_MAX_SHEETS+1):
        df_to_write = ncd_dataframes.get(f"NCD{i}", pd.DataFrame())
        if df_to_write.empty: df_to_write = pd.DataFrame({"Info":[f"No data for NCD{i}"]})
        df_to_write.to_excel(writer, sheet_name=f"NCD{i}", index=False)

    if final_ncd_df is not None and not final_ncd_df.empty:
        final_ncd_df.to_excel(writer, sheet_name="F_NCD", index=False)
    else:
        pd.DataFrame({"Info":["No data for F_NCD"]}).to_excel(writer, sheet_name="F_NCD", index=False)

    # --- ADDITION: NSE_COMM EXTRA SHEETS ---
    for sheet_name, df_to_write in nce_dataframes.items():
        if df_to_write.empty:
            df_to_write = pd.DataFrame({"Info":[f"No data for {sheet_name}"]})
        df_to_write.to_excel(writer, sheet_name=sheet_name, index=False)

# --- AUTO-ADJUST COLUMN WIDTHS FOR ALL SHEETS (INCLUDING NSE_COMM) ---
from openpyxl.utils import get_column_letter
from openpyxl import load_workbook

wb = load_workbook(OUTPUT_FILE)

for ws in wb.worksheets:
    for col_cells in ws.columns:
        max_length = 0
        col_letter = get_column_letter(col_cells[0].column)

        # Include header explicitly
        if col_cells[0].value is not None:
            max_length = len(str(col_cells[0].value))

        # Include all cell values
        for cell in col_cells:
            if cell.value is not None:
                max_length = max(max_length, len(str(cell.value)))

        # Extra padding to make headers fully visible
        ws.column_dimensions[col_letter].width = max_length + 5

wb.save(OUTPUT_FILE)

# ✅ Add this print to confirm Master file creation
print(f"✅ MASTER file created successfully: {OUTPUT_FILE}")

# RENAMING THE MASTER FILE ACCORDING TO FILE DATA

# ----------------- RENAME MASTER FILE BASED ON RptgDt -----------------
from openpyxl import load_workbook
import os
from datetime import datetime
import pandas as pd  # Needed for pd.Timestamp check

# Load the current master file
wb = load_workbook(OUTPUT_FILE, data_only=True)

# Read RptgDt from CM1 sheet, first row (assuming header in row 1)
ws_cm1 = wb["CM1"]
rptg_col = None
for idx, cell in enumerate(ws_cm1[1], start=1):
    if str(cell.value).strip().lower() == "rptgdt":
        rptg_col = idx
        break

if rptg_col is None:
    raise ValueError("Column 'RptgDt' not found in CM1 sheet.")

# Take the value from the first data row
rptg_dt_cell = ws_cm1.cell(row=2, column=rptg_col).value
if rptg_dt_cell is None:
    raise ValueError("RptgDt value is empty in CM1 sheet.")

# Format date as DDMMYYYY (previous format)
if isinstance(rptg_dt_cell, str):
    rptg_dt_obj = datetime.strptime(rptg_dt_cell, "%Y-%m-%d")
    rptg_dt = rptg_dt_obj.strftime("%d%m%Y")
elif isinstance(rptg_dt_cell, (datetime, pd.Timestamp)):
    rptg_dt = rptg_dt_cell.strftime("%d%m%Y")
else:
    raise ValueError(f"Unrecognized date format: {rptg_dt_cell}")

# Construct new filename
folder = os.path.dirname(OUTPUT_FILE)
new_master_name = f"{rptg_dt}_MASTER.xlsx"
master_path = os.path.join(folder, new_master_name)

# Rename file (safe overwrite)
if os.path.abspath(OUTPUT_FILE) != os.path.abspath(master_path):
    if os.path.exists(master_path):
        os.remove(master_path)  # Delete existing file if present
    os.rename(OUTPUT_FILE, master_path)
    print(f"✅ Master file renamed successfully: {master_path}")
else:
    print(f"ℹ️ Master file already has the correct name: {master_path}")


# ---------------- COPY RENAMED MASTER TO DOWNSTREAM FOLDERS ----------------
# Done AFTER the rename so both destinations always receive the final
# <RptgDt>_MASTER.xlsx name, which is what the other two scripts look for.
# Any older *_MASTER.xlsx sitting in the destination is removed first so those
# scripts can never accidentally pick up a stale day's file.
import shutil as _shutil

for _dest in (LIVE_ALLOCATION_FOLDER, MCX_INTRA_FOLDER):
    try:
        if not os.path.isdir(_dest):
            print(f"⚠️ Destination folder not found, Master NOT copied: {_dest}")
            continue

        # Remove any stale *_MASTER.xlsx already in the destination
        for _old in os.listdir(_dest):
            if _old.endswith("_MASTER.xlsx") and _old != new_master_name:
                try:
                    os.remove(os.path.join(_dest, _old))
                    print(f"🗑️ Removed stale Master from {_dest}: {_old}")
                except Exception as _e:
                    print(f"⚠️ Could not remove stale Master {_old} from {_dest}: {_e}")

        _shutil.copy2(master_path, os.path.join(_dest, new_master_name))
        print(f"📤 Master file copied to: {os.path.join(_dest, new_master_name)}")

    except Exception as _e:
        print(f"❌ Could not copy Master file to {_dest}: {_e}")


#------CHANGING nan ClntId to 06436---------


from openpyxl import load_workbook

# Load the master file
wb = load_workbook(master_path)
ws = wb["Main"]

# Find the "ClntId" column
clnt_col_idx = None
for idx, cell in enumerate(ws[1], start=1):
    if cell.value == "ClntId":
        clnt_col_idx = idx
        break

# Replace "nan" with "06436" and track if any replacements were done
replaced_any = False
if clnt_col_idx is not None:
    for row in ws.iter_rows(min_row=2, min_col=clnt_col_idx, max_col=clnt_col_idx):
        cell = row[0]
        if str(cell.value).lower() == "nan":
            cell.value = "06436"
            replaced_any = True

# Save the updated master file
wb.save(master_path)

# Print appropriate message
if replaced_any:
    print("✅ 'nan' ClntId values replaced with 06436 in the Master file")
else:
    print("ℹ️ No 'nan' ClntId found, moving ahead")





import os
import re

# ---------- FILE SEQUENCES ----------
# NOTE: NCD6/NCD7/NCD8 are NOT listed here on purpose. They are enforced in the
# STRICT check at the top of this script, so if they are missing the run has
# already stopped and this email/warning list is never reached.
sequences = {
    "CM1": [r"Margin_NCL_CM_0_CM_06436_\d{8}_P_1100\.(csv|xlsx)"],
    "CM2": [r"Margin_NCL_CM_0_CM_06436_\d{8}_P_1230\.(csv|xlsx)"],
    "CM3": [r"Margin_NCL_CM_0_CM_06436_\d{8}_P_1430\.(csv|xlsx)"],
    "CM4": [r"Margin_NCL_CM_0_CM_06436_\d{8}_P_1530\.(csv|xlsx)"],
"Final_CM":[r"C_MG13_06436_\d{8}\.lis"],
    "FO1": [r"Margin_NCL_FO_0_CM_06436_\d{8}_P_1100\.(csv|xlsx)"],
    "FO2": [r"Margin_NCL_FO_0_CM_06436_\d{8}_P_1230\.(csv|xlsx)"],
    "FO3": [r"Margin_NCL_FO_0_CM_06436_\d{8}_P_1430\.(csv|xlsx)"],
    "FO4": [r"Margin_NCL_FO_0_CM_06436_\d{8}_P_1530\.(csv|xlsx)"],
    "Final_Fut": [r"Margin_NCL_FO_0_CM_06436_\d{8}_F_0000\.(csv|xlsx)"],
    "MCX1": [r"MCX_PeakMargin56720_\d{8}_01\.csv"],
    "MCX2": [r"MCX_PeakMargin56720_\d{8}_02\.csv"],
    "MCX3": [r"MCX_PeakMargin56720_\d{8}_03\.csv"],
    "MCX4": [r"MCX_PeakMargin56720_\d{8}_04\.csv"],
    "MCX5": [r"MCX_PeakMargin56720_\d{8}_05\.csv"],
    "MCX6": [r"MCX_PeakMargin56720_\d{8}_06\.csv"],
    "MCX7": [r"MCX_PeakMargin56720_\d{8}_07\.csv"],
    "MCX8": [r"MCX_PeakMargin56720_\d{8}_08\.csv"],
    "NCD1": [r"NCCL_MARGIN_REP_01292_\d{8}_01\.csv"],
    "NCD2": [r"NCCL_MARGIN_REP_01292_\d{8}_02\.csv"],
    "NCD3": [r"NCCL_MARGIN_REP_01292_\d{8}_03\.csv"],
    "NCD4": [r"NCCL_MARGIN_REP_01292_\d{8}_04\.csv"],
    "NCD5": [r"NCCL_MARGIN_REP_01292_\d{8}_05\.csv"],
    "NCD_FINAL":[r"01292_MGN_\d{14}\.CSV"],
    "NSE_COMM1": [r"Margin_NCL_CO_0_CM_06436_\d{8}_P_1100\.(csv|xlsx)"],
    "NSE_COMM2": [r"Margin_NCL_CO_0_CM_06436_\d{8}_P_1230\.(csv|xlsx)"],
    "NSE_COMM3": [r"Margin_NCL_CO_0_CM_06436_\d{8}_P_1430\.(csv|xlsx)"],
    "NSE_COMM4": [r"Margin_NCL_CO_0_CM_06436_\d{8}_P_1530\.(csv|xlsx)"],
    "NSE_COMM5": [r"Margin_NCL_CO_0_CM_06436_\d{8}_P_1700\.(csv|xlsx)"],
    "NSE_COMM6": [r"Margin_NCL_CO_0_CM_06436_\d{8}_P_1900\.(csv|xlsx)"],
    "NSE_COMM7": [r"Margin_NCL_CO_0_CM_06436_\d{8}_P_2030\.(csv|xlsx)"],
    "NSE_COMM8": [r"Margin_NCL_CO_0_CM_06436_\d{8}_P_2230\.(csv|xlsx)"],
    "NSE_COMM_FINAL": [r"Margin_NCL_CO_0_CM_06436_\d{8}_F_0000\.(csv|xlsx)"]
}

# ---------- FUNCTION TO CHECK MISSING FILES ----------
def get_missing_files(folder):
    """
    Checks the folder for required files and returns a list of missing files.
    """
    missing = []
    files_in_folder = os.listdir(folder)

    for key, patterns in sequences.items():
        found = False
        for pattern in patterns:
            for f in files_in_folder:
                if re.fullmatch(pattern, f):
                    found = True
                    break
            if found:
                break
        if not found:
            missing.append(key)

    return missing

# ---------- USAGE ----------
folder_path = r"C:\Rajeev Ji"
missing_files = get_missing_files(folder_path)

if missing_files:
    print("⚠️ Missing files:", ", ".join(missing_files))
else:
    print("✅ All required files are present.")





# ----------------- SEND EMAIL WITH RENAMED MASTER FILE -----------------
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

smtp_server = "smtp3.netcore.co.in"
smtp_port = 587
sender_email = "kartavya.dhillon@mastertrust.co.in"
password = ""

to_emails = ["rms_del@mastertrust.co.in"]
cc_emails = []

all_recipients = to_emails + cc_emails

# Create message
msg = MIMEMultipart()
msg['From'] = sender_email
msg['To'] = ", ".join(to_emails)
if cc_emails:
    msg['Cc'] = ", ".join(cc_emails)
msg['Subject'] = f"Consolidated Peak Margin File"


# --- CREATE PLAIN TEXT EMAIL BODY WITH MISSING FILES LIST AND WARNING SYMBOL ---

# Collect all missing files from your earlier function


missing_files = get_missing_files(FOLDER)          # This should return a list like ['CM1', 'FO3', 'NSE_COMM5']

if missing_files:
    missing_str = ", ".join(missing_files)
    body = f"""Dear Rajeev/Ashutosh,

Please find the attachment of Consolidated Peak Margin File {rptg_dt}.

⚠️The MasterFile is generated but the following files are missing: {missing_str}

Warm regards,
Kartavya Dhillon
Master Capital Services Ltd
📞 011-66331000 | Ext. 84
"""
else:
    body = f"""Dear Rajeev/Ashutosh,

Please find the attachment of Consolidated Peak Margin File {rptg_dt}.

All files are present.

Warm regards,
Kartavya Dhillon
Master Capital Services Ltd
📞 011-66331000 | Ext. 84
"""

# Attach to email as plain text
msg.attach(MIMEText(body, 'plain'))

# Attach the renamed master Excel file
with open(master_path, "rb") as attachment:
    part = MIMEBase("application", "octet-stream")
    part.set_payload(attachment.read())
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition",
        f"attachment; filename= {os.path.basename(master_path)}",
    )
    msg.attach(part)

# Send email
import time

import time

print("📧 Attempting to send email...")

while True:
    try:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        server.starttls()
        server.login(sender_email, password)

        server.sendmail(sender_email, all_recipients, msg.as_string())

        server.quit()
        print(f"✅ Email sent successfully to {', '.join(all_recipients)}")
        break

    except Exception as e:
        print(f"❌ Email sending failed: {e}")
        print("🔁 Retrying in 10 seconds...")
        time.sleep(10)

import os
import shutil
from datetime import datetime

FOLDER = r"C:\Rajeev Ji"
BACKUP_ROOT = os.path.join(FOLDER, "BACKUP AUTOMATED")
MASTER_FILE_PATTERN = "_MASTER.xlsx"

# --- Find the master file (already renamed with RptgDt) ---
master_file = None
for f in os.listdir(FOLDER):
    if f.endswith(MASTER_FILE_PATTERN):
        master_file = os.path.join(FOLDER, f)
        break

if master_file is None:
    raise FileNotFoundError("No MASTER file found to get reporting date.")

# --- Extract reporting date from the filename ---
# Example: '31102025_MASTER.xlsx' -> '31102025'
master_name = os.path.basename(master_file)
rptg_str = master_name.split("_")[0]  # '31102025'
rptg_date = datetime.strptime(rptg_str, "%d%m%Y").strftime("%d-%m-%Y")  # '31-10-2025'

# --- Create backup subfolder according to reporting date ---
backup_path = os.path.join(BACKUP_ROOT, rptg_date)
os.makedirs(backup_path, exist_ok=True)
print(f"📦 Backup folder created: {backup_path}")

# --- List of file types to backup ---
extensions_to_backup = (".csv", ".lis", ".xls", ".xlsx", ".gz")

# --- Backup all files used in the program including MASTER ---
for file in os.listdir(FOLDER):
    file_path = os.path.join(FOLDER, file)
    if os.path.isfile(file_path) and file.lower().endswith(extensions_to_backup):
        try:
            shutil.copy2(file_path, backup_path)
            print(f"✅ Backed up: {file}")
        except Exception as e:
            print(f"❌ Could not backup {file}: {e}")



# List of extensions to clean
extensions_to_backup = (".csv", ".lis", ".xls", ".xlsx", ".gz")

for file in os.listdir(FOLDER):
    file_path = os.path.join(FOLDER, file)

    # Only delete files (not folders) with the specified extensions
    if os.path.isfile(file_path) and file.lower().endswith(extensions_to_backup):
        try:
            os.remove(file_path)
            print(f"🗑️ Deleted: {file}")
        except Exception as e:
            print(f"❌ Could not delete {file}: {e}")
    else:
        # Skip folders and other files (like .py)
        print(f"⏩ Skipped (not a data file): {file}")
