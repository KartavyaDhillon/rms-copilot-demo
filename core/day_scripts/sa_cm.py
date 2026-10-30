import pandas as pd
import os
import re
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

import time

def safe_col_width(series):
    # Column-width helper that survives NaN cells regardless of pandas string backend
    return series.fillna("").astype(str).map(len).max()


# ---------------- FOLDER PATH ----------------
folder = r"C:\PRIYANKA\SA_01_CM"
MARGIN_SHORTFALL_TRACK_DIR = r"D:\MARGIN SHORTFALL TRACK"

script_start = time.time()

def checkpoint(name):
    print(f"⏱ {name}: {round(time.time()-script_start,2)} sec")

# ---------------- FULL FILE CHECK ----------------
# ===== CHANGED (NCDEX 5 -> 8): range(1, 6) -> range(1, 9) =====
# NCDEX peak files 01..08 are now ALL mandatory. This block raises
# FileNotFoundError, so a missing _06/_07/_08 stops the script.
sequences = {
    "Main C_SA04 file": [r"C_SA04.*_\d{8}\.lis"],
    "FA files i01-i04": [f"F_SA01_P_06436_\\d{{8}}_i0{i}\\.lis" for i in range(1, 5)],
    "FA END file": [r"F_SA01_06436_\d{8}\.lis"],
    "CM files i01-i04": [f"C_SA01_P_06436_\\d{{8}}_i0{i}\\.lis" for i in range(1, 5)],
    "CM END file": [r"C_SA01_06436_\d{8}\.lis"],
    "MCX files 01-08": [f"MCX_PeakMargin56720_\\d{{8}}_0{i}\\.csv" for i in range(1, 9)],
   # "MCX END file": [r"MCX_MARGIN_56720_\d+\.csv"],
    "NCDEX files 01-08": [f"NCCL_MARGIN_REP_01292_\\d{{8}}_0{i}\\.csv" for i in range(1, 9)],
    "NCDEX END file": [r"01292_MGN_\d+\.CSV"],
    "MCX Web Allocation 01-04": [f"MCX_Web_Allocation_0{i}\\.xlsx" for i in range(1, 5)],
    "MCX Web Allocation FINAL": [r"MCX_Web_Allocation_08.xlsx"]          }

missing_files = []

for desc, patterns in sequences.items():
    for pattern in patterns:
        found = any(re.match(pattern, f) for f in os.listdir(folder))
        if not found:
            missing_files.append(f"{desc} → missing pattern: {pattern}")

# ===== NEW (MCX EOD): both MCX EOD files are MANDATORY for Indicator = E =====
# Both files live in the BASE folder (same as the other SA files). Checked here together with the other files - if one is missing the script stops below.
MCX_EOD_FOLDER = folder   # base folder - MCX EOD files are placed here together with the other SA files
MCX_EOD_RESPONSE_NAME = "RESPONSE_2_mcx.csv"
if not os.path.isdir(MCX_EOD_FOLDER):
    missing_files.append(f"MCX EOD folder → not found: {MCX_EOD_FOLDER}")
else:
    _eod_present = os.listdir(MCX_EOD_FOLDER)
    # response file name: RESPONSE_2_mcx.csv / RESPONSE_2_MCX.csv - any upper/lower case is accepted
    MCX_EOD_RESPONSE_NAME = next((f for f in _eod_present if f.lower() == MCX_EOD_RESPONSE_NAME.lower()), MCX_EOD_RESPONSE_NAME)
    if not any(re.match(r"^MCX_MARGIN_56720_\d{8}\.csv$", f) for f in _eod_present):
        missing_files.append(f"MCX EOD peak file (no header) → missing pattern: MCX_MARGIN_56720_<YYYYMMDD>.csv in {MCX_EOD_FOLDER}")
    if MCX_EOD_RESPONSE_NAME not in _eod_present:
        missing_files.append(f"MCX EOD response file → missing: {MCX_EOD_RESPONSE_NAME} in {MCX_EOD_FOLDER}")

if missing_files:
    missing_list = "\n".join(missing_files)
    raise FileNotFoundError(f"❌ Missing required files:\n{missing_list}")
else:
    print("✅ All required files are present.")

# ---------------- EXTRA FILE CHECKS: EPI + RISK REPORT ----------------
extra_checks = {
    "EPI file": [r"EPI\.xlsx"],
    "Risk Report file": [r"RISK_REPORT_\d{8}\.csv"]
}

extra_missing = []

all_files = os.listdir(folder)

for desc, patterns in extra_checks.items():
    for pattern in patterns:
        if not any(re.match(pattern, f) for f in all_files):
            extra_missing.append(f"{desc} → missing pattern: {pattern}")

if extra_missing:
    missing_list = "\n".join(extra_missing)
    raise FileNotFoundError(f"❌ Missing required files:\n{missing_list}")
else:
    print("✅ EPI.xlsx and RISK_REPORT file found.")

# ---------------- SAME-DATE FILE CHECK ----------------
# All required input files must belong to the same trading day. Files with
# no date in their filename (EPI.xlsx, MCX Web Allocation files) are
# intentionally skipped here rather than guessed at.
from datetime import datetime as _sdc_datetime

def _same_date_parse(date_str, fmt):
    try:
        if fmt == "YYYYMMDD":
            return _sdc_datetime.strptime(date_str, "%Y%m%d").date()
        elif fmt == "DDMMYYYY":
            return _sdc_datetime.strptime(date_str, "%d%m%Y").date()
    except ValueError:
        return None
    return None

_same_date_groups = [
    ("Main C_SA04 file", r"C_SA04.*_(\d{8})\.lis", "DDMMYYYY"),
    ("FA files i01-i04", r"F_SA01_P_06436_(\d{8})_i0[1-4]\.lis", "DDMMYYYY"),
    ("FA END file", r"F_SA01_06436_(\d{8})\.lis", "DDMMYYYY"),
    ("CM files i01-i04", r"C_SA01_P_06436_(\d{8})_i0[1-4]\.lis", "DDMMYYYY"),
    ("CM END file", r"C_SA01_06436_(\d{8})\.lis", "DDMMYYYY"),
    ("MCX files 01-08", r"MCX_PeakMargin56720_(\d{8})_0[1-8]\.csv", "YYYYMMDD"),
    ("NCDEX files 01-08", r"NCCL_MARGIN_REP_01292_(\d{8})_0[1-8]\.csv", "YYYYMMDD"),
    ("NCDEX END file", r"01292_MGN_(\d{8})\d{6}\.CSV", "DDMMYYYY"),
    ("Risk Report file", r"RISK_REPORT_(\d{8})\.csv", "DDMMYYYY"),
]

_same_date_found = []
for _sdc_label, _sdc_pattern, _sdc_fmt in _same_date_groups:
    _sdc_rx = re.compile(_sdc_pattern, re.IGNORECASE)
    for _sdc_f in all_files:
        _sdc_m = _sdc_rx.match(_sdc_f)
        if _sdc_m:
            _sdc_d = _same_date_parse(_sdc_m.group(1), _sdc_fmt)
            if _sdc_d:
                _same_date_found.append((_sdc_label, _sdc_f, _sdc_d))

if _same_date_found:
    _sdc_anchor_label, _sdc_anchor_file, _sdc_anchor_date = _same_date_found[0]
    _sdc_mismatched = [t for t in _same_date_found if t[2] != _sdc_anchor_date]
    if _sdc_mismatched:
        print("\n❌ Critical: Required files are not all from the same date. Stopping execution.")
        print(f"   Anchor date ({_sdc_anchor_label}): {_sdc_anchor_file} → {_sdc_anchor_date.strftime('%d-%m-%Y')}")
        for _sdc_lbl, _sdc_fn, _sdc_dt in _sdc_mismatched:
            print(f"   Mismatch  ({_sdc_lbl}): {_sdc_fn} → {_sdc_dt.strftime('%d-%m-%Y')}")
        raise FileNotFoundError("Required files are not all from the same date - see mismatch list above.")
    else:
        print("✅ All required files confirmed to be from the same date.")


# Define final paths for backup use
epi_path = os.path.join(folder, "EPI.xlsx")

# Find latest RISK_REPORT file
risk_report_files = [f for f in os.listdir(folder) if re.match(r"RISK_REPORT_\d{8}\.csv", f)]
risk_report_file = sorted(risk_report_files)[-1]
risk_report_path = os.path.join(folder, risk_report_file)








# ===== NEW (MCX EOD): EARLY date check - both MCX EOD files must belong to the SA files date =====
_eod_anchor_date = _same_date_found[0][2] if _same_date_found else None
_eod_problems = []
if _eod_anchor_date:
    _eod_exp_str = _eod_anchor_date.strftime("%Y%m%d")
    _eod_exp_name = f"MCX_MARGIN_56720_{_eod_exp_str}.csv"
    _eod_peak_found = sorted(f for f in os.listdir(MCX_EOD_FOLDER) if re.match(r"^MCX_MARGIN_56720_\d{8}\.csv$", f))
    if _eod_exp_name not in _eod_peak_found:
        _eod_got = ", ".join(
            f"{f} (dated {_same_date_parse(f[-12:-4], 'YYYYMMDD').strftime('%d-%m-%Y')})" for f in _eod_peak_found
        ) or "no MCX_MARGIN_56720_<date>.csv file"
        _eod_problems.append(f"{_eod_exp_name} is NOT present in {MCX_EOD_FOLDER}\n      Found instead: {_eod_got}")
    _eod_resp_path = os.path.join(MCX_EOD_FOLDER, MCX_EOD_RESPONSE_NAME)
    try:
        _eod_resp_dates = pd.to_datetime(
            pd.read_csv(_eod_resp_path, usecols=["Date"], dtype=str)["Date"], format="%d/%m/%Y", errors="coerce"
        ).dropna().dt.date.unique()
    except Exception as _e:
        _eod_resp_dates = []
        _eod_problems.append(f"{MCX_EOD_RESPONSE_NAME}: could not read the Date column ({_e})")
    _eod_resp_bad = [d for d in _eod_resp_dates if d != _eod_anchor_date]
    if _eod_resp_bad:
        _eod_problems.append(
            f"{MCX_EOD_RESPONSE_NAME} is dated {', '.join(d.strftime('%d-%m-%Y') for d in _eod_resp_bad)} "
            f"(expected {_eod_anchor_date.strftime('%d-%m-%Y')})"
        )
if _eod_problems:
    raise FileNotFoundError(
        f"❌ MCX EOD files do not match the SA files date ({_eod_anchor_date.strftime('%d-%m-%Y')}):\n   - "
        + "\n   - ".join(_eod_problems)
    )
print("✅ MCX EOD files confirmed to be from the same date as the SA files.")


# ---------------- MAIN FILE ----------------
pattern_main = r"^C_SA04.*_\d{8}\.lis$"
main_files = [f for f in os.listdir(folder) if re.match(pattern_main, f)]
if not main_files:
    raise FileNotFoundError("No main C_SA04 file found!")
main_file = main_files[0]
df_main = pd.read_csv(os.path.join(folder, main_file), header=None, dtype=str)
headers_main = ["Date", "Indicator", "Client Code", "Margin", "Cash", "Non-Cash",
                "Other Segment", "Total Value(E+F+G)", "Short(H-D)"]
df_main.columns = headers_main[:len(df_main.columns)]

# ---------------- FA FILES ----------------
pattern_fa = r"^F_SA01_P_06436_\d{8}_i0[1-4]\.lis$"
fa_files = sorted([f for f in os.listdir(folder) if re.match(pattern_fa, f)])
fa_headers = ["Date", "Client Code", "Min Margin(A)", "Total Cash Collateral(B)",
              "Total Non-Cash Collateral(C)", "Total Value of Collateral(D)",
              "EOD Short Allocation(E=Max(E-D,0))"]
fa_dfs = {}
for f in fa_files:
    df = pd.read_csv(os.path.join(folder, f), header=None, dtype=str)
    df.columns = fa_headers[:len(df.columns)]
    match = re.search(r"i0([1-4])\.lis$", f)
    if match:
        sheet_num = int(match.group(1))
        fa_dfs[sheet_num] = df

# FA END file
pattern_fa_end = r"^F_SA01_06436_\d{8}\.lis$"
fa_end_files = [f for f in os.listdir(folder) if re.match(pattern_fa_end, f)]
if not fa_end_files:
    raise FileNotFoundError("No FA last-day file found!")
fa_end_df = pd.read_csv(os.path.join(folder, fa_end_files[0]), header=None, dtype=str)
fa_end_df.columns = fa_headers[:len(fa_end_df.columns)]

# ---------------- CM FILES ----------------
pattern_cm = r"^C_SA01_P_06436_\d{8}_i0[1-4]\.lis$"
cm_files = sorted([f for f in os.listdir(folder) if re.match(pattern_cm, f)])
cm_headers = fa_headers  # same as FA
cm_dfs = {}
for f in cm_files:
    df = pd.read_csv(os.path.join(folder, f), header=None, dtype=str)
    df.columns = cm_headers[:len(df.columns)]
    match = re.search(r"i0([1-4])\.lis$", f)
    if match:
        sheet_num = int(match.group(1))
        cm_dfs[sheet_num] = df

# CM END file
pattern_cm_end = r"^C_SA01_06436_\d{8}\.lis$"
cm_end_files = [f for f in os.listdir(folder) if re.match(pattern_cm_end, f)]
if not cm_end_files:
    raise FileNotFoundError("No CM last-day file found!")
cm_end_df = pd.read_csv(os.path.join(folder, cm_end_files[0]), header=None, dtype=str)
cm_end_df.columns = cm_headers[:len(cm_end_df.columns)]

# ---------------- MCX FILES ----------------
pattern_mcx = r"^MCX_PeakMargin56720_\d{8}_0([1-8])\.csv$"
mcx_files = sorted([f for f in os.listdir(folder) if re.match(pattern_mcx, f)])
mcx_headers = ["Date", "Snapshot Time", "CMID", "TMID", "Client Code", "Initial Margin",
               "ELM", "Net Buy Premium", "Total Upfront Margin",
               "Client Margin Allocation", "Client Other Collaterals", "Short Allocation"]
mcx_dfs = {}
for f in mcx_files:
    df = pd.read_csv(os.path.join(folder, f), header=None, dtype=str)
    df.columns = mcx_headers[:len(df.columns)]
    match = re.search(r"_0([1-8])\.csv$", f)
    if match:
        sheet_num = int(match.group(1))
        mcx_dfs[sheet_num] = df

# ---------------- MCX_END FILE ----------------
pattern_mcx_end = r"^MCX_MARGIN_56720_\d+\.csv$"
mcx_end_files = [f for f in os.listdir(folder) if re.match(pattern_mcx_end, f)]
if mcx_end_files:
    mcx_end_df = pd.read_csv(os.path.join(folder, mcx_end_files[0]), header=None, dtype=str)
    mcx_end_headers = ["Date","TM / CP ID","Client ID","Initial Margin","Other Margin","MTM",
                       "Reserved","Reserved2","MTM Collected","Initial Margin Collected",
                       "Other Margin Collected","Peak Margin Threshold %","Peak Margin",
                       "Peak Margin Collected","Peak Margin Shortfall","Intra-day short Allocation",
                       "EOD Short Allocation"]
    extra_count = mcx_end_df.shape[1] - len(mcx_end_headers)
    if extra_count > 0:
        mcx_end_headers += [f"Extra{i+1}" for i in range(extra_count)]
    mcx_end_df.columns = mcx_end_headers
else:
    mcx_end_df = pd.DataFrame()

# ---------------- NCDEX FILES ----------------
# ===== CHANGED (NCDEX 5 -> 8): 0([1-5]) -> 0([1-8]) in BOTH places =====
pattern_ncdex = r"^NCCL_MARGIN_REP_01292_\d{8}_0([1-8])\.csv$"
ncdex_files = sorted([f for f in os.listdir(folder) if re.match(pattern_ncdex, f)])
ncdex_dfs = {}
for f in ncdex_files:
    df = pd.read_csv(os.path.join(folder, f), header=0, dtype=str)  # has headers
    match = re.search(r"_0([1-8])\.csv$", f)
    if match:
        sheet_num = int(match.group(1))
        ncdex_dfs[sheet_num] = df

print(f"📑 NCDEX peak files loaded: {sorted(ncdex_dfs.keys())}")

# ===== NEW: which NCDEX peak file represents END-OF-DAY =====
# Was 05 (it used to be the last file published). NCDEX now publishes up to 08,
# so END-OF-DAY is file 08 - the same file MCX already uses for indicator 'E'.
NCDEX_END_SHEET = 8

# NCDEX_END sheet
pattern_ncdex_end = r"^01292_MGN_\d+\.CSV$"
ncdex_end_files = [f for f in os.listdir(folder) if re.match(pattern_ncdex_end, f)]
if ncdex_end_files:
    ncdex_end_df = pd.read_csv(os.path.join(folder, ncdex_end_files[0]), header=0, dtype=str)
else:
    ncdex_end_df = pd.DataFrame()

# ===================================================================
# ===== NEW (MCX EOD for Indicator = E) ======================================
# For Indicator = E the MCX columns now come from two files in MCX_EOD_FOLDER:
#   1) MCX_MARGIN_56720_<YYYYMMDD>.csv  (NO header)
#        column C = Client Code,  column D = MCX_Peak
#   2) RESPONSE_2_mcx.csv  (has header)
#        UCC Code = Client Code
#        Allocation at EOD                                   -> Cash_Allocated_MCX
#        Own Collateral EOD + Own Others Collateral at EOD   -> NCL_MCX
#        Cash_Allocated_MCX + NCL_MCX                        -> Deposit_MCX (U + V)
# Client not found in a file -> 0.
# Indicators 1-8 (numeric) are NOT changed.
# ===================================================================
MCX_EOD_NA = "0"   # value used when the client is NOT found (was "N/A"). MCX_EOD_FOLDER / MCX_EOD_RESPONSE_NAME are defined in the FULL FILE CHECK at the top
MCX_EOD_NA_COLS = ["MCX_Peak", "Cash_Allocated_MCX", "NCL_MCX", "Deposit_MCX (U + V)"]

if not os.path.isdir(MCX_EOD_FOLDER):
    raise FileNotFoundError(f"❌ MCX EOD folder not found: {MCX_EOD_FOLDER}")

def _mcx_eod_num(series):
    # text -> number (commas removed); blank / bad value -> 0.0
    return pd.to_numeric(series.astype(str).str.replace(",", "").str.strip(), errors="coerce").fillna(0.0)

# ---- 1) MCX_MARGIN_56720_<date>.csv  (no header) ----
# (presence + date were already verified in the EARLY MCX EOD DATE CHECK near the top of the script)
mcx_eod_peak_file = (
    f"MCX_MARGIN_56720_{_eod_anchor_date.strftime('%Y%m%d')}.csv" if _eod_anchor_date
    else sorted(f for f in os.listdir(MCX_EOD_FOLDER) if re.match(r"^MCX_MARGIN_56720_\d{8}\.csv$", f))[-1]
)
mcx_eod_peak_path = os.path.join(MCX_EOD_FOLDER, mcx_eod_peak_file)

mcx_eod_peak_df = pd.read_csv(mcx_eod_peak_path, header=None, dtype=str)
if mcx_eod_peak_df.shape[1] < 4:
    raise ValueError(f"❌ {mcx_eod_peak_file}: expected at least 4 columns (A-D), found {mcx_eod_peak_df.shape[1]}")
_eod_pk = pd.DataFrame({
    "code": mcx_eod_peak_df[2].astype(str).str.replace("'", "").str.strip(),   # column C
    "peak": _mcx_eod_num(mcx_eod_peak_df[3]),                                  # column D
})
_eod_pk = _eod_pk[(_eod_pk["code"] != "") & (_eod_pk["code"].str.lower() != "nan")]
_eod_pk = _eod_pk.drop_duplicates(subset="code", keep="first")
mcx_eod_peak_lookup = dict(zip(_eod_pk["code"], _eod_pk["peak"]))
mcx_eod_peak_df.columns = [chr(65 + i) if i < 26 else f"Col{i + 1}" for i in range(mcx_eod_peak_df.shape[1])]

# ---- 2) RESPONSE_2_mcx.csv  (has header) ----
mcx_eod_resp_path = os.path.join(MCX_EOD_FOLDER, MCX_EOD_RESPONSE_NAME)
if not os.path.exists(mcx_eod_resp_path):
    raise FileNotFoundError(f"❌ {MCX_EOD_RESPONSE_NAME} not found in {MCX_EOD_FOLDER}")

mcx_eod_resp_df = pd.read_csv(mcx_eod_resp_path, dtype=str)
mcx_eod_resp_df.columns = mcx_eod_resp_df.columns.str.strip()
for _c in ["UCC Code", "Allocation at EOD", "Own Collateral EOD", "Own Others Collateral at EOD"]:
    if _c not in mcx_eod_resp_df.columns:
        raise ValueError(f"❌ Column '{_c}' not found in {MCX_EOD_RESPONSE_NAME}")

_eod_rs = mcx_eod_resp_df[mcx_eod_resp_df["UCC Code"].notna()].copy()    # first row of the file is a total row with no UCC
_eod_rs["_ucc"] = _eod_rs["UCC Code"].astype(str).str.replace("'", "").str.strip()
_eod_rs = _eod_rs[_eod_rs["_ucc"] != ""].drop_duplicates(subset="_ucc", keep="first")
mcx_eod_resp_lookup = dict(zip(
    _eod_rs["_ucc"],
    zip(
        _mcx_eod_num(_eod_rs["Allocation at EOD"]),
        _mcx_eod_num(_eod_rs["Own Collateral EOD"]),
        _mcx_eod_num(_eod_rs["Own Others Collateral at EOD"]),
    ),
))

MCX_EOD_FILES_USED = [mcx_eod_peak_path, mcx_eod_resp_path]
print(f"✅ MCX EOD peak file     : {mcx_eod_peak_file} ({len(mcx_eod_peak_lookup)} clients)")
print(f"✅ MCX EOD response file : {MCX_EOD_RESPONSE_NAME} ({len(mcx_eod_resp_lookup)} clients)")

# ---------------- ADD NEW COLUMNS TO MAIN ----------------
new_columns = ["Exchange", "CM_Peak", "FO_Peak", "MCX_Peak", "NCDEX_Peak",
               "Cash_Allocated_CM", "NCL_CM", "Deposit_CM (O+P)",
               "Cash_Allocated_FO", "NCL_FO", "Deposit_CM (R+S)",
               "Cash_Allocated_MCX", "NCL_MCX", "Deposit_MCX (U + V)",
               "Cash_Allocated_NCDEX", "NCL_NCDEX", "Deposit_NCDEX (X+Y)"]
for col in new_columns:
    df_main[col] = ""
df_main["Exchange"] = "CM"


# ---------------- POPULATE FULL DATA ----------------
for idx, row in df_main.iterrows():
    indicator = row["Indicator"]
    client_code = row["Client Code"]

    # Numeric indicators: FA, CM, MCX, NCDEX
    if indicator.isdigit():
        sheet_index = int(indicator)
        # FA
        if sheet_index in fa_dfs:
            df = fa_dfs[sheet_index]
            match = df[df["Client Code"] == client_code]
            if not match.empty:
                df_main.at[idx, "FO_Peak"] = match.iloc[0]["Min Margin(A)"]
                df_main.at[idx, "Cash_Allocated_FO"] = match.iloc[0]["Total Cash Collateral(B)"]
                df_main.at[idx, "NCL_FO"] = match.iloc[0]["Total Non-Cash Collateral(C)"]
                df_main.at[idx, "Deposit_CM (R+S)"] = match.iloc[0]["Total Value of Collateral(D)"]
        # CM
        if sheet_index in cm_dfs:
            df = cm_dfs[sheet_index]
            match = df[df["Client Code"] == client_code]
            if not match.empty:
                df_main.at[idx, "CM_Peak"] = match.iloc[0]["Min Margin(A)"]
                df_main.at[idx, "Cash_Allocated_CM"] = match.iloc[0]["Total Cash Collateral(B)"]
                df_main.at[idx, "NCL_CM"] = match.iloc[0]["Total Non-Cash Collateral(C)"]
                df_main.at[idx, "Deposit_CM (O+P)"] = match.iloc[0]["Total Value of Collateral(D)"]
        # MCX
        if sheet_index in mcx_dfs:
            df = mcx_dfs[sheet_index]
            match = df[df["Client Code"] == client_code]
            if not match.empty:
                df_main.at[idx, "MCX_Peak"] = match.iloc[0]["Total Upfront Margin"]
                df_main.at[idx, "Cash_Allocated_MCX"] = match.iloc[0]["Client Margin Allocation"]
                df_main.at[idx, "NCL_MCX"] = match.iloc[0]["Client Other Collaterals"]
                df_main.at[idx, "Deposit_MCX (U + V)"] = str(
                    float(df_main.at[idx, "Cash_Allocated_MCX"]) +
                    float(df_main.at[idx, "NCL_MCX"])
                )
        # NCDEX
        # NOTE: sheet_index can now be 6, 7 or 8 as well, and ncdex_dfs holds 01..08.
        if sheet_index in ncdex_dfs:
            df = ncdex_dfs[sheet_index]
            df["Client Code Clean"] = df["Client Code"].str.replace("'", "").str.strip()
            match = df[df["Client Code Clean"] == client_code]
            if not match.empty:
                df_main.at[idx, "Cash_Allocated_NCDEX"] = "0"
                df_main.at[idx, "NCL_NCDEX"] = match.iloc[0]["Collateral_Value_Snapshot"]
                cash_val = float(df_main.at[idx, "Cash_Allocated_NCDEX"])
                ncl_val = float(df_main.at[idx, "NCL_NCDEX"])
                df_main.at[idx, "Deposit_NCDEX (X+Y)"] = str(cash_val + ncl_val)
                df_main.at[idx, "NCDEX_Peak"] = str(
                    float(match.iloc[0]["Initial Margin"]) + float(match.iloc[0]["ELM Margin"])
                )

    # E: FA_END, CM_END, MCX from SA_MCX_08, NCDEX from SA_NCDEX_08
    elif indicator.upper() == "E":
        # FA END
        match = fa_end_df[fa_end_df["Client Code"] == client_code]
        if not match.empty:
            df_main.at[idx, "FO_Peak"] = match.iloc[0]["Min Margin(A)"]
            df_main.at[idx, "Cash_Allocated_FO"] = match.iloc[0]["Total Cash Collateral(B)"]
            df_main.at[idx, "NCL_FO"] = match.iloc[0]["Total Non-Cash Collateral(C)"]
            df_main.at[idx, "Deposit_CM (R+S)"] = match.iloc[0]["Total Value of Collateral(D)"]

        # CM END
        match = cm_end_df[cm_end_df["Client Code"] == client_code]
        if not match.empty:
            df_main.at[idx, "CM_Peak"] = match.iloc[0]["Min Margin(A)"]
            df_main.at[idx, "Cash_Allocated_CM"] = match.iloc[0]["Total Cash Collateral(B)"]
            df_main.at[idx, "NCL_CM"] = match.iloc[0]["Total Non-Cash Collateral(C)"]
            df_main.at[idx, "Deposit_CM (O+P)"] = match.iloc[0]["Total Value of Collateral(D)"]

        # ===== CHANGED (MCX EOD): MCX for Indicator = E now comes from the MCX EOD files =====
        # (was: SA_MCX_08 = MCX_PeakMargin56720_<date>_08.csv, then overwritten by MCX_Web_Allocation_08.xlsx)
        _eod_cc = str(client_code).strip()

        # MCX_Peak <- MCX_MARGIN_56720_<date>.csv : column D, matched on column C (Client Code)
        _eod_peak = mcx_eod_peak_lookup.get(_eod_cc)
        df_main.at[idx, "MCX_Peak"] = MCX_EOD_NA if _eod_peak is None else str(round(_eod_peak, 2))

        # Cash / NCL / Deposit <- RESPONSE_2_mcx.csv : matched on UCC Code
        _eod_resp = mcx_eod_resp_lookup.get(_eod_cc)
        if _eod_resp is None:
            df_main.at[idx, "Cash_Allocated_MCX"] = MCX_EOD_NA
            df_main.at[idx, "NCL_MCX"] = MCX_EOD_NA
            df_main.at[idx, "Deposit_MCX (U + V)"] = MCX_EOD_NA
        else:
            _eod_cash, _eod_own, _eod_oth = _eod_resp
            df_main.at[idx, "Cash_Allocated_MCX"] = str(round(_eod_cash, 2))                    # Allocation at EOD
            df_main.at[idx, "NCL_MCX"] = str(round(_eod_own + _eod_oth, 2))                     # Own Collateral EOD + Own Others Collateral at EOD
            df_main.at[idx, "Deposit_MCX (U + V)"] = str(round(_eod_cash + _eod_own + _eod_oth, 2))

        # NCDEX from SA_NCDEX_08
        # ===== CHANGED (NCDEX 5 -> 8): was  if 5 in ncdex_dfs / ncdex_dfs[5] =====
        if NCDEX_END_SHEET in ncdex_dfs:
            ncdex_df = ncdex_dfs[NCDEX_END_SHEET]
            ncdex_df["Client Code Clean"] = ncdex_df["Client Code"].str.replace("'", "").str.strip()
            match = ncdex_df[ncdex_df["Client Code Clean"] == client_code]
            if not match.empty:
                df_main.at[idx, "NCDEX_Peak"] = str(
                    float(match.iloc[0]["Initial Margin"]) + float(match.iloc[0]["ELM Margin"])
                )
                df_main.at[idx, "NCL_NCDEX"] = match.iloc[0]["Collateral_Value_Snapshot"]
                df_main.at[idx, "Cash_Allocated_NCDEX"] = "0"
                df_main.at[idx, "Deposit_NCDEX (X+Y)"] = str(
                    float(df_main.at[idx, "Cash_Allocated_NCDEX"]) +
                    float(df_main.at[idx, "NCL_NCDEX"])
                )

# ---------------- FILL BLANKS WITH 0 FROM CM_Peak ONWARDS ----------------
columns_to_fill = df_main.columns[df_main.columns.get_loc("CM_Peak"):]  # from CM_Peak to end
df_main[columns_to_fill] = df_main[columns_to_fill].replace("", "0")

# ---------------- MCX WEB ALLOCATION FINAL ----------------
import glob
import re


mcx_web_allocation_final = pd.DataFrame()

latest_file = os.path.join(folder, "MCX_Web_Allocation_08.xlsx")

if os.path.exists(latest_file):
    print(f"✅ MCX_WEB_ALLOCATION_FINAL file picked: {os.path.basename(latest_file)}")

    temp_df = pd.read_excel(latest_file, header=None, nrows=10)
    header_row = next((r for r in range(10) if temp_df.iloc[r].astype(str).str.contains("UCC Code", case=False).any()), None)
    if header_row is None:
        raise ValueError("Header row not found in MCX_WEB_ALLOCATION_FINAL within first 10 rows.")

    mcx_web_allocation_final = pd.read_excel(latest_file, header=header_row, dtype=str)
    mcx_web_allocation_final.columns = mcx_web_allocation_final.columns.str.strip()
    mcx_web_allocation_final.dropna(how="all", inplace=True)
    mcx_web_allocation_final.reset_index(drop=True, inplace=True)

    # Required columns
    required_cols = ["UCC Code", "Allocation at EOD", "Own Collateral EOD", "Own Others Collateral at EOD"]
    for col in required_cols:
        if col not in mcx_web_allocation_final.columns:
            raise ValueError(f"Column '{col}' not found in MCX_WEB_ALLOCATION_FINAL file.")

    # Clean numeric
    for col in ["Allocation at EOD", "Own Collateral EOD", "Own Others Collateral at EOD"]:
        mcx_web_allocation_final[col] = mcx_web_allocation_final[col].astype(str).str.replace(",", "").astype(float)

    # ===== CHANGED (MCX EOD): the Indicator == 'E' override from MCX_Web_Allocation_08.xlsx is REMOVED =====
    # E rows now take Cash_Allocated_MCX / NCL_MCX / Deposit_MCX from RESPONSE_2_mcx.csv (see MCX EOD block above).
    # MCX_Web_Allocation_08.xlsx is still loaded here (sheet MCX_WEB_ALLOCATION_FINAL + LIVE ALLOCATION fallback).

# ---------------- MCX WEB ALLOCATION 01-04 ----------------
for i in range(1, 5):
    file_name = f"MCX_Web_Allocation_0{i}.xlsx"
    file_path = os.path.join(folder, file_name)

    if not os.path.exists(file_path):
        print(f"⚠️ File not found: {file_name}")
        continue

    print(f"✅ MCX_WEB_0{i} file picked: {file_name}")

    # Detect header in first 10 rows
    temp_df = pd.read_excel(file_path, header=None, nrows=10)
    header_row = next((r for r in range(10) if temp_df.iloc[r].astype(str).str.contains("UCC Code", case=False).any()), None)
    if header_row is None:
        raise ValueError(f"Header row not found in {file_name} within first 10 rows.")

    # Read full file from header and drop empty rows
    mcx_web_df = pd.read_excel(file_path, header=header_row, dtype=str)
    mcx_web_df.columns = mcx_web_df.columns.str.strip()
    mcx_web_df.dropna(how="all", inplace=True)
    mcx_web_df.reset_index(drop=True, inplace=True)

    required_cols = ["UCC Code", "Allocation at EOD", "Own Collateral EOD", "Own Others Collateral at EOD"]
    for col in required_cols:
        if col not in mcx_web_df.columns:
            raise ValueError(f"Column '{col}' not found in {file_name}")

    # Clean numeric
    for col in ["Allocation at EOD", "Own Collateral EOD", "Own Others Collateral at EOD"]:
        mcx_web_df[col] = mcx_web_df[col].astype(str).str.replace(",", "").astype(float)

    # Apply logic based on Indicator == file number
    for idx, row in df_main.iterrows():
        indicator = str(row["Indicator"]).strip()
        client_code = str(row["Client Code"]).strip()

        if indicator == str(i):
            match = mcx_web_df[mcx_web_df["UCC Code"].astype(str).str.strip() == client_code]
            if not match.empty:
                if str(row["Cash_Allocated_MCX"]) in ["0", "0.0", "0.00"]:
                    df_main.at[idx, "Cash_Allocated_MCX"] = str(match.iloc[0]["Allocation at EOD"])
                if str(row["NCL_MCX"]) in ["0", "0.0", "0.00"]:
                    total_ncl = match.iloc[0]["Own Collateral EOD"] + match.iloc[0]["Own Others Collateral at EOD"]
                    df_main.at[idx, "NCL_MCX"] = str(total_ncl)
                # Always update Deposit
                cash_val = float(df_main.at[idx, "Cash_Allocated_MCX"])
                ncl_val = float(df_main.at[idx, "NCL_MCX"])
                df_main.at[idx, "Deposit_MCX (U + V)"] = str(cash_val + ncl_val)



# ---------------- NORMALIZE ZERO-LIKE VALUES TO '0' ----------------
    for col in ["Cash_Allocated_MCX", "NCL_MCX"]:
        df_main[col] = df_main[col].apply(lambda x: "0" if str(x).strip() in ["0", "0.0", "0.00"] else x)


# ---------------- EPI FILE PROCESSING (ADD AT THE END BEFORE SAVE) ----------------

epi_file = os.path.join(folder, "EPI.xlsx")

if not os.path.exists(epi_file):
    raise FileNotFoundError("❌ EPI.xlsx not found in folder!")

# Read EPI file
df_epi = pd.read_excel(epi_file, dtype=str)

# Clean numeric Amount column
df_epi["Amount"] = df_epi["Amount"].astype(str).str.replace(",", "").astype(float)

# Create 20% column
df_epi["EPI 20%"] = df_epi["Amount"] * 0.20

# Check Account ID column
epi_code_col = "Account ID"
if epi_code_col not in df_epi.columns:
    raise ValueError("❌ 'Account ID' column not found in EPI.xlsx!")

df_epi[epi_code_col] = df_epi[epi_code_col].astype(str).str.strip()

# Aggregate 20% value per Account ID
df_epi_sum = df_epi.groupby(epi_code_col)["EPI 20%"].sum().reset_index()

# Merge into df_main
df_main["Client Code"] = df_main["Client Code"].astype(str).str.strip()
df_main = df_main.merge(df_epi_sum, how="left", left_on="Client Code", right_on=epi_code_col)

# Remove merge duplicate key
df_main.drop(columns=[epi_code_col], inplace=True)

# Fill missing with zero
df_main["EPI 20%"] = df_main["EPI 20%"].fillna(0)

# ---------------- ADD 5 NEW CALCULATED COLUMNS ----------------

# ---------------- ROUNDING + NUMERIC CLEANING + NEW COLUMNS ----------------

# Convert numeric fields used in calculations
numeric_cols = [
    "Deposit_MCX (U + V)", "MCX_Peak",
    "Deposit_NCDEX (X+Y)", "NCDEX_Peak",
    "Short(H-D)", "EPI 20%"
]

for col in numeric_cols:
    df_main[col] = (
        df_main[col]
        .astype(str)
        .str.replace(",", "")
        .replace("", "0")
        .astype(float)
        .round(2)
    )

# 1️⃣ MCX SHORT/EXCESS (W-M)
df_main["MCX SHORT/EXCESS (W-M)"] = (
    df_main["Deposit_MCX (U + V)"] - df_main["MCX_Peak"]
).round(2)

# 2️⃣ NCDEX SHORT/EXCESS (Z-N)
df_main["NCDEX SHORT/EXCESS (Z-N)"] = (
    df_main["Deposit_NCDEX (X+Y)"] - df_main["NCDEX_Peak"]
).round(2)

# 3️⃣ COVERED SHORT WITH EPI 20% (AA-I)
df_main["COVERED SHORT WITH EPI 20% (AA-I)"] = (
    df_main["EPI 20%"] - df_main["Short(H-D)"]
).round(2)

# 4️⃣ COVERED WITH MCX EXCESS (AD+AB)
df_main["COVERED WITH MCX EXCESS (AD+AB)"] = (
    df_main["COVERED SHORT WITH EPI 20% (AA-I)"]
    + df_main["MCX SHORT/EXCESS (W-M)"]
).round(2)


#5 COVERED WITH NCDEX EXCESS
df_main["COVERED WITH NCDEX EXCESS (AE + AC)"] = (
    df_main["COVERED WITH MCX EXCESS (AD+AB)"]
    + df_main["NCDEX SHORT/EXCESS (Z-N)"]
).round(2)


# ---------------- MOVE NEW COLUMNS TO END ----------------
new_cols = [
    "EPI 20%",
    "MCX SHORT/EXCESS (W-M)",
    "NCDEX SHORT/EXCESS (Z-N)",
    "COVERED SHORT WITH EPI 20% (AA-I)",
    "COVERED WITH MCX EXCESS (AD+AB)",
"COVERED WITH NCDEX EXCESS (AE + AC)"
]

df_main = df_main[[c for c in df_main.columns if c not in new_cols] + new_cols]






# ---------------- OPTIMIZED RISK REPORT MERGE (READ ONLY MATCHING CLIENTS) ----------------

# 1️⃣ Pick RISK REPORT file by prefix
risk_files = [f for f in os.listdir(folder) if f.startswith("RISK_REPORT_") and f.endswith(".csv")]
if not risk_files:
    raise FileNotFoundError("❌ No RISK_REPORT_*.csv file found!")

risk_file = sorted(risk_files)[-1]
risk_path = os.path.join(folder, risk_file)

print(f"✅ Using RISK REPORT file: {risk_file}")

# 2️⃣ Take client codes present in df_main
client_list = set(df_main["Client Code"].astype(str).str.strip().tolist())

# 3️⃣ Prepare an empty list to collect matched rows
matched_rows = []

# 4️⃣ Read risk file in chunks (fast & memory efficient)
chunk_size = 50000  # adjust according to file size

for chunk in pd.read_csv(risk_path, dtype=str, chunksize=chunk_size):

    # Keep only necessary columns
    keep_cols = ["ACCOUNTID", "TRADING_SOFTWARE_TYPE", "CATEGORY"]
    chunk = chunk[keep_cols]

    # Clean account id
    chunk["ACCOUNTID"] = chunk["ACCOUNTID"].astype(str).str.strip()

    # Filter only matching ACCOUNTID values
    chunk_match = chunk[chunk["ACCOUNTID"].isin(client_list)]

    if len(chunk_match) > 0:
        matched_rows.append(chunk_match)

# 5️⃣ Combine only matched records
if matched_rows:
    df_risk_small = pd.concat(matched_rows, ignore_index=True)
else:
    df_risk_small = pd.DataFrame(columns=["ACCOUNTID", "TRADING_SOFTWARE_TYPE", "CATEGORY"])

print(f"🔎 Matched rows in RISK REPORT: {len(df_risk_small)}")

# 6️⃣ Merge with df_main
df_main = df_main.merge(
    df_risk_small,
    how="left",
    left_on="Client Code",
    right_on="ACCOUNTID"
)

# 7️⃣ Cleanup
df_main.drop(columns=["ACCOUNTID"], inplace=True)
df_main["TRADING_SOFTWARE_TYPE"] = df_main["TRADING_SOFTWARE_TYPE"].fillna("")
df_main["CATEGORY"] = df_main["CATEGORY"].fillna("")

# 8️⃣ Move these two columns to the end
risk_cols = ["TRADING_SOFTWARE_TYPE", "CATEGORY"]
df_main = df_main[[c for c in df_main.columns if c not in new_cols + risk_cols] + new_cols + risk_cols]



# ---------------- FINAL REMARKS COLUMN ----------------

# Ensure numeric conversion
df_main["EPI 20%"] = df_main["EPI 20%"].astype(float)
df_main["COVERED SHORT WITH EPI 20% (AA-I)"] = df_main["COVERED SHORT WITH EPI 20% (AA-I)"].astype(float)
df_main["COVERED WITH MCX EXCESS (AD+AB)"] = df_main["COVERED WITH MCX EXCESS (AD+AB)"].astype(float)

# Create empty column
df_main["FINAL REMARKS"] = ""

mask1 = ((df_main["FINAL REMARKS"] == "") & (df_main["Short(H-D)"] == 0))
df_main.loc[mask1, "FINAL REMARKS"] = "ZERO SHORTAGE IN I COLUMN"



# Rule 2: AD >= 0 AND AA > 0
mask2 = ((df_main["FINAL REMARKS"] == "") & (df_main["COVERED SHORT WITH EPI 20% (AA-I)"] >= 0) & (df_main["EPI 20%"] > 0))
df_main.loc[mask2, "FINAL REMARKS"] = "COVERED WITH EPI"


# Rule 3: COVERED WITH MCX EXCESS
mask3 = ((df_main["FINAL REMARKS"] == "") & (df_main["COVERED SHORT WITH EPI 20% (AA-I)"] < 0) & (df_main["MCX SHORT/EXCESS (W-M)"] >= 0) &
    (
        df_main["COVERED SHORT WITH EPI 20% (AA-I)"]
        + df_main["MCX SHORT/EXCESS (W-M)"]
        >= 0
    )
)
df_main.loc[mask3, "FINAL REMARKS"] = "COVERED WITH MCX EXCESS"


# Move FINAL REMARKS to end
df_main = df_main[[c for c in df_main.columns if c != "FINAL REMARKS"] + ["FINAL REMARKS"]]



# ---------------- CONVERT ALL NUMERIC COLUMNS TO REAL FLOATS (EXCEPT CLIENT CODE) ----------------

# Columns that should remain text
text_columns = ["Client Code", "Indicator", "Exchange"]

# Detect numeric-looking columns automatically (everything except the text columns)
possible_numeric_cols = [col for col in df_main.columns if col not in text_columns]

for col in possible_numeric_cols:
    # Try converting column to float
    try:
        df_main[col] = (
            df_main[col]
            .astype(str)
            .str.replace(",", "")
            .str.replace(" ", "")
            .replace("", "0")
            .replace("nan", "0")
            .astype(float)
            .round(2)
        )
    except:
        # If conversion fails, leave it as text (safe fallback)
        pass


# ============================================================
# LIVE ALLOCATION FULL DEBUG + OPTIMIZED VERSION
# ============================================================

live_start=time.time()

print("\n================================================")
print("🚀 LIVE ALLOCATION STARTED")
print("================================================\n")

counter=0

cm_time=0
fo_time=0
mcx_time=0
ncdex_time=0


# ============================================================
# CREATE COLUMNS
# ============================================================
# LIVE ALLOCATION 1-4 = peak snapshots, LIVE ALLOCATION 5 = END OF DAY.
# The count stays 5 because CM/FO only publish i01-i04 + END.

for i in range(1,6):
    df_main[f"LIVE ALLOCATION {i}"]=0.0

print("✅ Created LIVE ALLOCATION columns")


# ============================================================
# PREPARE CLEAN COLUMNS
# ============================================================

print("\n🔄 Preparing lookup columns...")

for x in mcx_dfs.values():

    x["Client Code Clean"]=(
        x["Client Code"]
        .astype(str)
        .str.replace("'","")
        .str.strip()
    )

for x in ncdex_dfs.values():

    x["Client Code Clean"]=(
        x["Client Code"]
        .astype(str)
        .str.replace("'","")
        .str.strip()
    )

print("✅ Lookup columns ready")


# ============================================================
# CREATE LOOKUP DICTIONARIES
# ============================================================

print("\n🔄 Creating lookup dictionaries...")

lookup_start=time.time()

cm_lookup={}
fo_lookup={}
ncdex_lookup={}


# ---------------- CM ----------------

for key,df in cm_dfs.items():

    df["Client Code"]=(
        df["Client Code"]
        .astype(str)
        .str.strip()
    )

    cm_lookup[key]=dict(
        zip(
            df["Client Code"],
            pd.to_numeric(
                df["Total Value of Collateral(D)"],
                errors="coerce"
            ).fillna(0)
        )
    )


cm_end_df["Client Code"]=(
    cm_end_df["Client Code"]
    .astype(str)
    .str.strip()
)

cm_lookup[5]=dict(
    zip(
        cm_end_df["Client Code"],
        pd.to_numeric(
            cm_end_df["Total Value of Collateral(D)"],
            errors="coerce"
        ).fillna(0)
    )
)


# ---------------- FO ----------------

for key,df in fa_dfs.items():

    df["Client Code"]=(
        df["Client Code"]
        .astype(str)
        .str.strip()
    )

    fo_lookup[key]=dict(
        zip(
            df["Client Code"],
            pd.to_numeric(
                df["Total Value of Collateral(D)"],
                errors="coerce"
            ).fillna(0)
        )
    )


fa_end_df["Client Code"]=(
    fa_end_df["Client Code"]
    .astype(str)
    .str.strip()
)

fo_lookup[5]=dict(
    zip(
        fa_end_df["Client Code"],
        pd.to_numeric(
            fa_end_df["Total Value of Collateral(D)"],
            errors="coerce"
        ).fillna(0)
    )
)


# ---------------- NCDEX ----------------
# ncdex_lookup now holds keys 1..8 (was 1..5)

for key,df in ncdex_dfs.items():

    ncdex_lookup[key]=dict(
        zip(
            df["Client Code Clean"],
            pd.to_numeric(
                df["Collateral_Value_Snapshot"],
                errors="coerce"
            ).fillna(0)
        )
    )

print(
    f"✅ Lookup dictionaries ready "
    f"in {round(time.time()-lookup_start,2)} sec"
)


# ============================================================
# LOAD WEB FILES
# ============================================================

print("\n🔄 Loading WEB allocation files...\n")

web_dfs={}

for file_no in [1,2,3,4,8]:

    file_start=time.time()

    file_name=f"MCX_Web_Allocation_0{file_no}.xlsx"

    if file_no==8:
        file_name="MCX_Web_Allocation_08.xlsx"

    file_path=os.path.join(
        folder,
        file_name
    )

    if not os.path.exists(file_path):

        print(
            f"⚠ Missing : {file_name}"
        )

        continue


    temp_df=pd.read_excel(
        file_path,
        header=None,
        nrows=10
    )


    header_row=next(
        (
            r for r in range(10)
            if temp_df.iloc[r]
            .astype(str)
            .str.contains(
                "UCC Code",
                case=False
            )
            .any()
        ),
        None
    )


    web_df=pd.read_excel(
        file_path,
        header=header_row,
        dtype=str
    )

    web_df.columns=(
        web_df.columns
        .str.strip()
    )

    web_df.dropna(
        how="all",
        inplace=True
    )


    for col in [
        "Allocation at EOD",
        "Own Collateral EOD",
        "Own Others Collateral at EOD"
    ]:

        web_df[col]=(
            web_df[col]
            .astype(str)
            .str.replace(",","")
            .astype(float)
        )


    web_df["UCC Code"]=(
        web_df["UCC Code"]
        .astype(str)
        .str.strip()
    )


    web_dfs[file_no]=web_df

    print(
        f"✅ Loaded {file_name}"
        f" ({len(web_df)} rows)"
        f" in {round(time.time()-file_start,2)} sec"
    )

print("\n✅ WEB allocation preload completed")


# ============================================================
# MCX HELPER
# ============================================================

def get_mcx_live(client_code,file_no):

    mcx_val=0

    actual_file=8 if file_no==5 else file_no

    if actual_file in mcx_dfs:

        mcx_df=mcx_dfs[actual_file]

        m=mcx_df[
            mcx_df["Client Code Clean"]
            ==client_code
        ]

        if not m.empty:

            alloc=pd.to_numeric(
                m.iloc[0]["Client Margin Allocation"],
                errors="coerce"
            )

            other=pd.to_numeric(
                m.iloc[0]["Client Other Collaterals"],
                errors="coerce"
            )

            alloc=0 if pd.isna(alloc) else alloc
            other=0 if pd.isna(other) else other

            mcx_val=alloc+other


    if mcx_val==0:

        if actual_file in web_dfs:

            w=web_dfs[actual_file]

            m=w[
                w["UCC Code"]
                ==client_code
            ]

            if not m.empty:

                mcx_val=(
                    m.iloc[0]["Allocation at EOD"]
                    +
                    m.iloc[0]["Own Collateral EOD"]
                    +
                    m.iloc[0]["Own Others Collateral at EOD"]
                )

    return mcx_val



# ============================================================
# POPULATE VALUES
# ============================================================

print("\n🔄 Starting client processing...\n")

total_clients=len(df_main)

for idx,row in df_main.iterrows():

    counter+=1

    client_code=str(
        row["Client Code"]
    ).strip()


    for live_no in range(1,6):

        total_live=0


        t=time.time()

        total_live+=cm_lookup[live_no].get(
            client_code,
            0
        )

        cm_time += time.time()-t


        t=time.time()

        total_live+=fo_lookup[live_no].get(
            client_code,
            0
        )

        fo_time += time.time()-t


        t=time.time()

        total_live+=get_mcx_live(
            client_code,
            live_no
        )

        mcx_time += time.time()-t


        t=time.time()

        # ===== CHANGED (NCDEX 5 -> 8) =====
        # LIVE ALLOCATION 5 is the END-OF-DAY column. NCDEX end-of-day is now
        # peak file 08 (was 05). LIVE 1-4 still map straight to NCDEX 01-04.
        lookup_no=NCDEX_END_SHEET if live_no==5 else live_no

        total_live+=ncdex_lookup[
            lookup_no
        ].get(
            client_code,
            0
        )

        ncdex_time += time.time()-t


        df_main.at[
            idx,
            f"LIVE ALLOCATION {live_no}"
        ]=round(total_live,2)


    if counter%250==0:

        total_elapsed=round(
            time.time()-live_start,
            2
        )

        print(
            f"⏱ {counter}/{total_clients}"
            f" | Total={total_elapsed}s"
            f" | CM={round(cm_time,2)}s"
            f" | FO={round(fo_time,2)}s"
            f" | MCX={round(mcx_time,2)}s"
            f" | NCDEX={round(ncdex_time,2)}s"
        )


print("\n================================================")
print("✅ LIVE ALLOCATION COMPLETED")
print("================================================")

print(f"CM total      : {round(cm_time,2)} sec")
print(f"FO total      : {round(fo_time,2)} sec")
print(f"MCX total     : {round(mcx_time,2)} sec")
print(f"NCDEX total   : {round(ncdex_time,2)} sec")

print(
    f"\nTotal LIVE time : "
    f"{round(time.time()-live_start,2)} sec"
)

checkpoint("After LIVE")





# ===== NEW (MCX EOD): how many Indicator = E clients were NOT found in the MCX EOD files (shown as 0) =====
_eod_is_e = df_main["Indicator"].astype(str).str.strip().str.upper() == "E"
_eod_cc_series = df_main["Client Code"].astype(str).str.strip()
print(f"ℹ️ MCX EOD: Indicator E rows = {int(_eod_is_e.sum())} | "
      f"not in MCX_MARGIN file (MCX_Peak = 0) = {int((_eod_is_e & ~_eod_cc_series.isin(set(mcx_eod_peak_lookup))).sum())} | "
      f"not in RESPONSE file (Cash/NCL/Deposit = 0) = {int((_eod_is_e & ~_eod_cc_series.isin(set(mcx_eod_resp_lookup))).sum())}")

excel_start=time.time()
print("🚀 Excel save started")
# ---------------- SAVE TO EXCEL ----------------
output_file = os.path.join(folder, "SA_CM_Full.xlsx")
with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:

    # Full Data
    df_main.to_excel(writer, index=False, sheet_name="SA_CM_04_Full Data")
    ws = writer.sheets["SA_CM_04_Full Data"]

    # ⭐ ADDED BLOCK → NUMERIC FORMAT
    numfmt = writer.book.add_format({'num_format': '#,##0.00'})
    text_cols = ["Client Code", "Indicator", "Exchange"]

    for col in df_main.columns:
        col_idx = df_main.columns.get_loc(col)
        if col not in text_cols:
            ws.set_column(col_idx, col_idx, 15, numfmt)   # numeric
        else:
            ws.set_column(col_idx, col_idx, 15)           # text

    # Auto column width (kept exactly as your code – no change)
    for i, col in enumerate(df_main.columns):
        max_len = max(safe_col_width(df_main[col]), len(str(col))) + 2
        ws.set_column(i, i, max_len)

    # FA sheets
    for sheet_num, df in fa_dfs.items():
        df.to_excel(writer, index=False, sheet_name=f"SA_FO_0{sheet_num}")
        ws = writer.sheets[f"SA_FO_0{sheet_num}"]
        for j, col in enumerate(df.columns):
            max_len = max(safe_col_width(df[col]), len(str(col))) + 2
            ws.set_column(j, j, max_len)

    fa_end_df.to_excel(writer, index=False, sheet_name="SA_FO_END")
    ws = writer.sheets["SA_FO_END"]
    for j, col in enumerate(fa_end_df.columns):
        max_len = max(safe_col_width(fa_end_df[col]), len(str(col))) + 2
        ws.set_column(j, j, max_len)

    # CM sheets
    for sheet_num, df in cm_dfs.items():
        df.to_excel(writer, index=False, sheet_name=f"SA_CM_0{sheet_num}")
        ws = writer.sheets[f"SA_CM_0{sheet_num}"]
        for j, col in enumerate(df.columns):
            max_len = max(safe_col_width(df[col]), len(str(col))) + 2
            ws.set_column(j, j, max_len)

    cm_end_df.to_excel(writer, index=False, sheet_name="SA_CM_END")
    ws = writer.sheets["SA_CM_END"]
    for j, col in enumerate(cm_end_df.columns):
        max_len = max(safe_col_width(cm_end_df[col]), len(str(col))) + 2
        ws.set_column(j, j, max_len)

    # MCX sheets
    for sheet_num, df in mcx_dfs.items():
        df.to_excel(writer, index=False, sheet_name=f"SA_MCX_0{sheet_num}")
        ws = writer.sheets[f"SA_MCX_0{sheet_num}"]
        for j, col in enumerate(df.columns):
            max_len = max(safe_col_width(df[col]), len(str(col))) + 2
            ws.set_column(j, j, max_len)

    if not mcx_end_df.empty:
        mcx_end_df.to_excel(writer, index=False, sheet_name="SA_MCX_END")
        ws = writer.sheets["SA_MCX_END"]
        for j, col in enumerate(mcx_end_df.columns):
            max_len = max(safe_col_width(mcx_end_df[col]), len(str(col))) + 2
            ws.set_column(j, j, max_len)

    # NCDEX sheets  ===== now writes SA_NCDEX_01 .. SA_NCDEX_08 =====
    for sheet_num, df in ncdex_dfs.items():
        df.to_excel(writer, index=False, sheet_name=f"SA_NCDEX_0{sheet_num}")
        ws = writer.sheets[f"SA_NCDEX_0{sheet_num}"]
        for j, col in enumerate(df.columns):
            max_len = max(safe_col_width(df[col]), len(str(col))) + 2
            ws.set_column(j, j, max_len)

    if not ncdex_end_df.empty:
        ncdex_end_df.to_excel(writer, index=False, sheet_name="SA_NCDEX_END")
        ws = writer.sheets["SA_NCDEX_END"]
        for j, col in enumerate(ncdex_end_df.columns):
            max_len = max(safe_col_width(ncdex_end_df[col]), len(str(col))) + 2
            ws.set_column(j, j, max_len)

    # MCX_WEB_ALLOCATION_FINAL sheet
    if not mcx_web_allocation_final.empty:
        mcx_web_allocation_final.to_excel(writer, index=False, sheet_name="MCX_WEB_ALLOCATION_FINAL")
        ws = writer.sheets["MCX_WEB_ALLOCATION_FINAL"]
        for j, col in enumerate(mcx_web_allocation_final.columns):
            max_len = max(safe_col_width(mcx_web_allocation_final[col]), len(str(col))) + 2
            ws.set_column(j, j, max_len)

    # MCX_Web_Allocation_01-04 sheets
    for i in range(1, 5):
        file_name = f"MCX_Web_Allocation_0{i}.xlsx"
        file_path = os.path.join(folder, file_name)
        if os.path.exists(file_path):
            temp_df = pd.read_excel(file_path, header=None, nrows=10)
            header_row = None
            for r in range(10):
                if temp_df.iloc[r].astype(str).str.contains("UCC Code", case=False).any():
                    header_row = r
                    break
            if header_row is None:
                raise ValueError(f"Header row not found in {file_name} within first 10 rows.")

            mcx_web_df = pd.read_excel(file_path, header=header_row, dtype=str).dropna(how="all").reset_index(drop=True)
            mcx_web_df.columns = mcx_web_df.columns.str.strip()

            mcx_web_df.to_excel(writer, index=False, sheet_name=f"MCX_WEB_0{i}")
            ws = writer.sheets[f"MCX_WEB_0{i}"]
            for j, col in enumerate(mcx_web_df.columns):
                max_len = max(safe_col_width(mcx_web_df[col]), len(str(col))) + 2
                ws.set_column(j, j, max_len)


    # MCX EOD source files (NEW) - used for Indicator = E
    mcx_eod_peak_df.to_excel(writer, index=False, sheet_name="MCX_EOD_PEAK")
    ws = writer.sheets["MCX_EOD_PEAK"]
    for j, col in enumerate(mcx_eod_peak_df.columns):
        max_len = max(safe_col_width(mcx_eod_peak_df[col]), len(str(col))) + 2
        ws.set_column(j, j, max_len)

    mcx_eod_resp_df.to_excel(writer, index=False, sheet_name="MCX_EOD_RESPONSE")
    ws = writer.sheets["MCX_EOD_RESPONSE"]
    for j, col in enumerate(mcx_eod_resp_df.columns):
        max_len = max(safe_col_width(mcx_eod_resp_df[col]), len(str(col))) + 2
        ws.set_column(j, j, max_len)

print(f"✅ Excel saved in {round(time.time()-excel_start,2)} sec")
checkpoint("After Excel")
print(f"All files processed and saved as {output_file}.")



import smtplib
from email.message import EmailMessage
import mimetypes
import pandas as pd

# ---------------- EMAIL CONFIG ----------------
smtp_server = "smtp3.netcore.co.in"
smtp_port = 587
sender_email = "kartavya.dhillon@mastertrust.co.in"
password = ""

receiver_email = "rms_del@mastertrust.co.in"



cc_email = ""  # leave empty for no CC

# ---------------- READ DATE FROM FILE ----------------
try:
    df_for_date = pd.read_excel(output_file, sheet_name="SA_CM_04_Full Data", usecols=["Date"], dtype=str)
    file_date_raw = df_for_date["Date"].dropna().iloc[0].strip()
except Exception as e:
    print(f"⚠️ Could not read Date from file: {e}")
    from datetime import datetime
    file_date_raw = datetime.now().strftime("%d-%m-%Y")

# ---------------- FORMAT DATE ----------------
try:
    file_date_obj = pd.to_datetime(file_date_raw, errors="coerce")
    if pd.notnull(file_date_obj):
        day = file_date_obj.day
        suffix = "th" if 4 <= day <= 20 or 24 <= day <= 30 else ["st", "nd", "rd"][day % 10 - 1]
        formatted_date = file_date_obj.strftime(f"%d{suffix} %B %Y")
    else:
        formatted_date = file_date_raw
except Exception:
    formatted_date = file_date_raw

# ---------------- EMAIL MESSAGE ----------------
msg = EmailMessage()
msg["From"] = sender_email
msg["To"] = receiver_email
if cc_email:  # only add CC if not empty
    msg["Cc"] = cc_email
msg["Subject"] = "Final Data - SA04 CM"

body = f"""
<html>
<body>
<p>Dear Priyanka,</p>

<p>Please find the attached files of <b>{formatted_date}</b>.</p>

<br>
<p><b>NOTE:</b> Below are the conditions used for deriving FINAL REMARKS:</p>

<ul>
    <li>ZERO SHORTAGE IN I COLUMN: When I = 0</li>
    <li>COVERED WITH EPI: When AD ≥ 0 and AA > 0 and I>0</li>
    <li>COVERED WITH MCX EXCESS: When AD < 0 and AB ≥ 0 than check AD + AB ≥ 0</li>
</ul>

<br>
<p>Warm regards,<br>
Kartavya Dhillon<br>
Master Capital Services Ltd<br>
📞 011-66331000 | Ext. 84</p>
</body>
</html>
"""

msg.add_alternative(body, subtype="html")

# ---------------- ATTACH FILE ----------------
file_path = output_file
file_name = os.path.basename(file_path)
mime_type, _ = mimetypes.guess_type(file_path)
if mime_type is None:
    mime_type = "application/octet-stream"
main_type, sub_type = mime_type.split("/", 1)

with open(file_path, "rb") as f:
    msg.add_attachment(f.read(),
                       maintype=main_type,
                       subtype=sub_type,
                       filename=file_name)


# ---------------- SEND EMAIL (UNLIMITED RETRY) ----------------
import time
import smtplib

RETRY_DELAY_SECONDS = 5   # retry every 5 seconds
attempt = 1

while True:
    try:
        print(f"📧 Email send attempt #{attempt} ...")

        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(sender_email, password)

            all_recipients = [receiver_email]
            if cc_email:
                all_recipients.append(cc_email)

            server.send_message(msg, to_addrs=all_recipients)

        print(f"✅ Email sent successfully on attempt #{attempt}")
        break   # ✅ EXIT LOOP ON SUCCESS

    except Exception as e:
        print(f"❌ Email send failed on attempt #{attempt}")
        print(f"🔁 Reason: {e}")
        print(f"⏳ Retrying in {RETRY_DELAY_SECONDS} seconds...\n")

        attempt += 1
        time.sleep(RETRY_DELAY_SECONDS)


# -------- BACKUP + CLEANUP SECTION FOR SA_CM --------
import shutil
import os
import re
from datetime import datetime

# --- Convert formatted date like "24th October 2025" → DDMMYYYY ---
try:
    # Remove suffix (st, nd, rd, th)
    clean_date_str = re.sub(r'(st|nd|rd|th)', '', formatted_date)
    # Parse into datetime object
    date_obj = datetime.strptime(clean_date_str.strip(), "%d %B %Y")
    # Format as DDMMYYYY
    backup_date_folder = date_obj.strftime("%d%m%Y")
except Exception as e:
    print(f"⚠️ Could not parse formatted_date: {e}")
    backup_date_folder = formatted_date.replace(" ", "")  # fallback

# -------- COPY FINAL OUTPUT TO SHARED MARGIN SHORTFALL TRACK FOLDER --------
try:
    _mst_dir = os.path.join(MARGIN_SHORTFALL_TRACK_DIR, backup_date_folder)
    os.makedirs(_mst_dir, exist_ok=True)
    shutil.copy2(output_file, os.path.join(_mst_dir, os.path.basename(output_file)))
    print(f"📤 Copied output to shared tracking folder: {_mst_dir}")
except Exception as e:
    print(f"⚠️ Could not copy output to shared tracking folder: {e}")

backup_base = r"C:\PRIYANKA\BACKUP FILES AUTOMATED\SA_CM"
backup_path = os.path.join(backup_base, backup_date_folder)

# Create backup folder for today
os.makedirs(backup_path, exist_ok=True)

# --- Build MCX Web Allocation file list dynamically (final + 01 to 04 if present) ---
MCX_WEB_FILES_USED = []

# Always include the newly created output file
MCX_WEB_FILES_USED.append(output_file)

# Web Allocation 01-04 (only include if file exists)
for i in range(1, 5):
    fp = os.path.join(folder, f"MCX_Web_Allocation_0{i}.xlsx")
    if os.path.exists(fp):
        MCX_WEB_FILES_USED.append(fp)

# MCX Web Allocation FINAL → fixed 08 file
final_file_path = os.path.join(folder, "MCX_Web_Allocation_08.xlsx")
if os.path.exists(final_file_path):
    MCX_WEB_FILES_USED.append(final_file_path)
    print(f"Included MCX Web Allocation FINAL in backup: {os.path.basename(final_file_path)}")
else:
    print(f"⚠️ MCX Web Allocation FINAL file not found: MCX_Web_Allocation_08.xlsx")


# Combine all used files including output_file
# NOTE: ncdex_files now contains 01-08 (was 01-05), so all 8 get backed up.
all_used_files = (
    [main_file]
    + fa_files
    + fa_end_files
    + cm_files
    + cm_end_files
    + mcx_files
    + mcx_end_files
    + ncdex_files
    + ncdex_end_files
    + MCX_EOD_FILES_USED     # MCX EOD files (Indicator = E)
    + MCX_WEB_FILES_USED
    + [epi_path]             # EPI.xlsx
    + [risk_report_path]     # Risk Report file
)

print(f"\nBacking up {len(all_used_files)} files to: {backup_path}\n")

# --- Copy files to backup folder ---
for f in all_used_files:
    src_path = os.path.join(folder, f) if os.path.basename(f) == f else f
    try:
        shutil.copy(src_path, backup_path)
        print(f"Copied: {f}")
    except Exception as e:
        print(f"Failed to copy {f} → {e}")

print("\n✅ Backup Completed Successfully.")

# -------- CLEAN ORIGINAL WORKING FOLDER (except .py files) --------
print("\nCleaning working folder (excluding .py files)...")
extensions_to_clean = (".csv", ".lis", ".xls", ".xlsx", ".gz")
for f in os.listdir(folder):
    f_path = os.path.join(folder, f)
    if os.path.isfile(f_path) and f.lower().endswith(extensions_to_clean):
        try:
            os.remove(f_path)
            print(f"Deleted: {f}")
        except Exception as e:
            print(f"Failed to delete {f} → {e}")

print("\n✅ Cleanup Completed Successfully.")
