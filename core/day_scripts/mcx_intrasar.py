import os
import sys
import pandas as pd
import re

# ============================================================================
# NCDEX 5 -> 8 PEAK FILE CHANGE : SUMMARY OF EDITS IN THIS FILE
# ============================================================================
#  1. Presence check regex        : _0[1-5]  ->  _0[1-8]
#  2. pattern_ncdex               : _0([1-5]) ->  _0([1-8])
#  3. sheet-number extraction     : _0([1-5]) ->  _0([1-8])
#  4. final missing-file report   : one line  ->  8 individual NCDEX entries
#  5. END-FILE MARGIN BLOCK       : NCDEX end-of-day (01292_MGN_*) is now a
#                                   FALLBACK ONLY. Peak margin has priority.
#                                   For indicator 06/07/08 the EOD value is
#                                   added ONLY when the client has no row in
#                                   the corresponding NCDEX peak file
#                                   (NCCL_MARGIN_REP_01292_<date>_06/07/08).
#                                   This prevents double-counting now that
#                                   Master supplies real NCDEX6/7/8 values
#                                   through the Live Allocation report.
#     FA END / CM END logic is UNCHANGED - CM & FO still have only 4 peak
#     files (i01-i04), so peaks 05-08 genuinely have no CM/FO peak data.
# ============================================================================

# Folder and file prefix
folder = r"C:\MCX_INTRA_SAR"
prefix = "MCX_INTRASAR_"
MARGIN_SHORTFALL_TRACK_DIR = r"D:\MARGIN SHORTFALL TRACK"

# ------------------ FILE PRESENCE CHECK ------------------

required_checks = {
    "MCX Intra CSV": lambda f: f.startswith(prefix) and f.endswith(".csv"),

    # FA peak files
    "FA files": lambda f: re.match(r"^F_SA01_P_06436_\d{8}_i0[1-4]\.lis$", f),
    "FA End file": lambda f: re.match(r"^F_SA01_06436_\d{8}\.lis$", f),

    # CM peak files
    "CM files": lambda f: re.match(r"^C_SA01_P_06436_\d{8}_i0[1-4]\.lis$", f),
    "CM End file": lambda f: re.match(r"^C_SA01_06436_\d{8}\.lis$", f),

    # MCX peak files
    "MCX Peak files": lambda f: re.match(r"^MCX_PeakMargin56720_\d{8}_0[1-8]\.csv$", f),

    # NCDEX peak files   ===== CHANGED: 0[1-5] -> 0[1-8] =====
    "NCDEX files": lambda f: re.match(r"^NCCL_MARGIN_REP_01292_\d{8}_0[1-8]\.csv$", f),
    "NCDEX End file": lambda f: re.match(r"^01292_MGN_\d+\.CSV$", f),

    # Live Allocation file
    "Live Allocation file": lambda f: f.startswith("Report_Live") and f.endswith(".xlsx"),

    # ===== NEW: MASTER file (copied here automatically by Master_File_final.py) =====
    # Mandatory - it is the ONLY source for CM PEAK / FO PEAK / NSE COMM PEAK /
    # NCDEX MARGIN, so without it the new coverage columns are meaningless.
    "Master file (*_MASTER.xlsx)": lambda f: re.match(r"^\d{8}_MASTER\.xlsx$", f)
}
missing = []

files = os.listdir(folder)

for name, rule in required_checks.items():
    if not any(rule(f) for f in files):
        missing.append(name)

if missing:
    print("❌ ERROR: Below required files are missing in folder:", folder)
    for m in missing:
        print("   -", m)
    print("\n⛔ Script stopped. Please place missing files and rerun.")
    sys.exit(1)
else:
    print("✅ All required files found. Proceeding...\n")


# Find the CSV file by prefix
csv_file = None
for f in os.listdir(folder):
    if f.startswith(prefix) and f.endswith(".csv"):
        csv_file = os.path.join(folder, f)
        break

if not csv_file:
    print("❌ CSV file not found with prefix:", prefix)
    sys.exit(1)

print("📂 Found file:", csv_file)

# Read CSV (no header)
df = pd.read_csv(csv_file, header=None)

# Excel headers required
cols = [
    "Business Date", "Indicator", "Client Code", "Margin", "Cash", "Non-Cash",
    "Other segment", "Total Value(E + F + G)", "Short(H - D)"
]

# Create new DataFrame with required columns
output_df = pd.DataFrame(columns=cols)

# Map fields
output_df["Business Date"] = df[0]  # Column A -> Business Date
output_df["Client Code"] = df[3]     # Column D -> Client Code
output_df["Short(H - D)"] = df[4]    # Column E -> Short(H - D)

# ============================================================================
# NEW: PEAK INDICATOR SUPPLIED DIRECTLY IN MCX_INTRASAR FILE (COLUMN P)
# ============================================================================
# MCX has started sending the peak-file indicator (1-8) directly inside the
# MCX_INTRASAR csv, in Column P (0-based index 15). When present, THIS TAKES
# PRIORITY over the old "match Short(H-D) against MCX peak files" logic below.
# Any row where Column P is blank/missing falls back to the old matching
# logic automatically (Indicator stays "" and flows into the existing loops).
# We don't know how long MCX will keep sending this column, so both paths
# are kept alive - this one is just checked first.
# ============================================================================
INTRASAR_INDICATOR_COL = 15  # Excel column P, 0-based

def norm_file_indicator(v):
    if pd.isna(v):
        return ""
    s = str(v).strip()
    m = re.match(r"^0?([1-8])$", s)
    if not m:
        return ""
    return m.group(1).zfill(2)

if df.shape[1] > INTRASAR_INDICATOR_COL:
    output_df["Indicator"] = df[INTRASAR_INDICATOR_COL].apply(norm_file_indicator)
else:
    output_df["Indicator"] = ""

intrasar_indicator_count = int((output_df["Indicator"] != "").sum())
peak_indicator_missing_for_day = intrasar_indicator_count == 0

print(f"📌 Indicator taken directly from MCX_INTRASAR file (Column P): {intrasar_indicator_count} of {len(output_df)} rows")
if peak_indicator_missing_for_day:
    print("⚠️ No peak indicator found in MCX_INTRASAR Column P today - falling back to old peak-file matching logic for all rows")
else:
    print(f"↳ Remaining {len(output_df) - intrasar_indicator_count} row(s) without a file indicator will use the old peak-file matching logic")

print("✅ Base MCX Intra sheet created")

# --- Generic numeric cleaner (used everywhere below: end-file margins,
# Master-file lookups, NSE COMM/CM/FO deposit lookups, NCDEX lookups) ---
def clean_num(x):
    if pd.isna(x): return 0
    return float(str(x).replace("'", "").replace(",", "").strip() or 0)

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
    sheet_num = int(re.search(r"i0([1-4])\.lis$", f).group(1))
    fa_dfs[sheet_num] = df


#----------- FA_END FILE --------------
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
cm_headers = fa_headers
cm_dfs = {}
for f in cm_files:
    df = pd.read_csv(os.path.join(folder, f), header=None, dtype=str)
    df.columns = cm_headers[:len(df.columns)]
    sheet_num = int(re.search(r"i0([1-4])\.lis$", f).group(1))
    cm_dfs[sheet_num] = df


#------------ CM END FILE ---------------------------
# CM END file
pattern_cm_end = r"^C_SA01_06436_\d{8}\.lis$"
cm_end_files = [f for f in os.listdir(folder) if re.match(pattern_cm_end, f)]
if not cm_end_files:
    raise FileNotFoundError("No CM last-day file found!")
cm_end_df = pd.read_csv(os.path.join(folder, cm_end_files[0]), header=None, dtype=str)
cm_end_df.columns = cm_headers[:len(cm_end_df.columns)]


# ---------------- NSE COMM FILES (O_SA01) — OPTIONAL, NOT IN PRE-CHECK ----------------
# These sometimes arrive, sometimes don't (unlike FA/CM which are mandatory).
# Same 7-column headerless layout as the FA/CM peak files, but numbered i01-i08
# instead of i01-i04. Whatever is missing today is simply left out of
# ncomm_dfs - NSE COMM DEPOSIT stays 0 for that peak and a note goes in the
# email. This must NOT stop the script (mirrors how Master_File_final.py
# treats its own optional NSE_COMM files).
pattern_ncomm = r"^O_SA01_P_06436_\d{8}_i0[1-8]\.lis$"
ncomm_files = sorted([f for f in os.listdir(folder) if re.match(pattern_ncomm, f)])
ncomm_headers = fa_headers
ncomm_dfs = {}
for f in ncomm_files:
    df = pd.read_csv(os.path.join(folder, f), header=None, dtype=str)
    df.columns = ncomm_headers[:len(df.columns)]
    sheet_num = int(re.search(r"i0([1-8])\.lis$", f).group(1))
    ncomm_dfs[sheet_num] = df

ncomm_missing = [str(i).zfill(2) for i in range(1, 9) if i not in ncomm_dfs]
if ncomm_missing:
    print(f"⚠️ NSE COMM (O_SA01) peak file(s) not received today: {', '.join(ncomm_missing)} — NSE COMM DEPOSIT will be 0 for these peaks")
else:
    print("✅ All 8 NSE COMM (O_SA01) peak files found")


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
    sheet_num = int(re.search(r"_0([1-8])\.csv$", f).group(1))
    mcx_dfs[sheet_num] = df

# ------------- NCDEX FILES ----------------
# ===== CHANGED: 0([1-5]) -> 0([1-8]) in BOTH the pattern and the extraction =====
pattern_ncdex = r"^NCCL_MARGIN_REP_01292_\d{8}_0([1-8])\.csv$"
ncdex_files = sorted([f for f in os.listdir(folder) if re.match(pattern_ncdex, f)])
ncdex_dfs = {}
for f in ncdex_files:
    df = pd.read_csv(os.path.join(folder, f), header=0, dtype=str)
    sheet_num = int(re.search(r"_0([1-8])\.csv$", f).group(1))
    ncdex_dfs[sheet_num] = df

print(f"📑 NCDEX peak files loaded: {sorted(ncdex_dfs.keys())}")


#--------------NCDEX_END FILE --------------------
# NCDEX_END sheet
pattern_ncdex_end = r"^01292_MGN_\d+\.CSV$"
ncdex_end_files = [f for f in os.listdir(folder) if re.match(pattern_ncdex_end, f)]
if ncdex_end_files:
    ncdex_end_df = pd.read_csv(os.path.join(folder, ncdex_end_files[0]), header=0, dtype=str)
else:
    ncdex_end_df = pd.DataFrame()


# ---------------- MASTER FILE (peak-margin reference, MANDATORY) ----------------
# Built by Master_File_final.py, which copies it into this folder automatically
# right after renaming it. Main sheet holds one row per ClntId and supplies the
# CM/FO/NSE COMM/NCDEX PEAK columns further down. Its presence is already
# enforced by the pre-check at the top of this script, so by the time we get
# here the file is guaranteed to exist.
pattern_master = r"^\d{8}_MASTER\.xlsx$"
master_files = sorted([f for f in os.listdir(folder) if re.match(pattern_master, f)])
master_lookup = {}

# indicator (1-8) -> Master column name, per deposit category
MASTER_PEAK_COLS = {
    "CM":      {1: "CM1", 2: "CM2", 3: "CM3", 4: "CM4",
                5: "Final Cash", 6: "Final Cash", 7: "Final Cash", 8: "Final Cash"},
    "FO":      {1: "FM1", 2: "FM2", 3: "FM3", 4: "FM4",
                5: "Final Fut", 6: "Final Fut", 7: "Final Fut", 8: "Final Fut"},
    "NSECOMM": {i: f"NSE_COMM_{i}" for i in range(1, 9)},
    "NCDEX":   {i: f"NCDEX{i}" for i in range(1, 9)},
}

if master_files:
    master_file = os.path.join(folder, master_files[-1])
    master_main_df = pd.read_excel(master_file, sheet_name="Main", dtype=str)
    master_main_df["ClntId"] = master_main_df["ClntId"].astype(str).str.strip()
    master_main_df = master_main_df.drop_duplicates(subset=["ClntId"], keep="last")

    master_value_cols = sorted({v for d in MASTER_PEAK_COLS.values() for v in d.values()})
    for col in master_value_cols:
        if col in master_main_df.columns:
            master_main_df[col] = pd.to_numeric(master_main_df[col], errors="coerce").fillna(0)
        else:
            master_main_df[col] = 0

    master_lookup = master_main_df.set_index("ClntId")[master_value_cols].to_dict(orient="index")
    print(f"✅ Master file found for peak-margin lookup: {os.path.basename(master_file)}")
else:
    # Should be unreachable - the pre-check at the top already stops the run.
    print("❌ No *_MASTER.xlsx found — stopping.")
    sys.exit(1)

def get_master_value(client_code, category, indicator):
    """category in CM/FO/NSECOMM/NCDEX; indicator 1-8 (int or numeric string)."""
    if not master_lookup:
        return 0
    try:
        ind = int(indicator)
    except (TypeError, ValueError):
        return 0
    col = MASTER_PEAK_COLS.get(category, {}).get(ind)
    if col is None:
        return 0
    row = master_lookup.get(str(client_code).strip())
    if row is None:
        return 0
    return clean_num(row.get(col, 0))


# ✅ MATCH EXACT VALUES FIRST (fallback only - skip rows that already got
# an indicator directly from the MCX_INTRASAR file, Column P)
for idx, row in output_df.iterrows():
    if row["Indicator"] != "":
        continue
    client = row["Client Code"]
    short_val = row["Short(H - D)"]
    try: short_float = float(short_val)
    except: short_float = None

    for file_num, mcx_df in mcx_dfs.items():
        match = mcx_df[(mcx_df["Client Code"] == client) & (mcx_df["Short Allocation"] == short_val)]

        if match.empty and short_float is not None:
            try:
                match = mcx_df[
                    (mcx_df["Client Code"] == client) &
                    (mcx_df["Short Allocation"].astype(float) == short_float)
                ]
            except: pass

        if not match.empty:
            output_df.at[idx, "Indicator"] = str(file_num).zfill(2)
            break

# ✅ Populate Margin/Cash/Other/Non-Cash for matched
for idx, row in output_df.iterrows():
    indicator = row["Indicator"]
    if indicator == "": continue
    file_num = int(indicator)
    mcx_df = mcx_dfs.get(file_num)
    if mcx_df is None:
        # Indicator came from MCX_INTRASAR Column P but the matching MCX peak
        # file wasn't supplied today - nothing to populate from.
        continue
    match = mcx_df[mcx_df["Client Code"] == row["Client Code"]]

    if not match.empty:
        output_df.at[idx, "Margin"] = match.iloc[0]["Total Upfront Margin"]
        output_df.at[idx, "Cash"] = match.iloc[0]["Client Margin Allocation"]
        output_df.at[idx, "Other segment"] = match.iloc[0]["Client Other Collaterals"]
        output_df.at[idx, "Non-Cash"] = "0"

# ✅ Compute Total Value
for idx, row in output_df.iterrows():
    try: cash = float(row["Cash"])
    except: cash = 0
    try: other = float(row["Other segment"])
    except: other = 0
    output_df.at[idx, "Total Value(E + F + G)"] = str(cash + other)

# ✅ SECOND PASS +0.01 then -0.01 (your logic preserved)
for UPDOWN, adjust in [("UP",0.01), ("DOWN",-0.01)]:
    for idx, row in output_df.iterrows():
        if row["Indicator"] != "": continue
        try: rounded = round(float(row["Short(H - D)"]) + adjust, 2)
        except: continue

        for file_num, mcx_df in mcx_dfs.items():
            match = mcx_df[
                (mcx_df["Client Code"] == row["Client Code"]) &
                (mcx_df["Short Allocation"].astype(float) == rounded)
            ]
            if not match.empty:
                output_df.at[idx, "Indicator"] = str(file_num).zfill(2)
                output_df.at[idx, "Margin"] = match.iloc[0]["Total Upfront Margin"]
                output_df.at[idx, "Cash"] = match.iloc[0]["Client Margin Allocation"]
                output_df.at[idx, "Other segment"] = match.iloc[0]["Client Other Collaterals"]
                output_df.at[idx, "Non-Cash"] = "0"
                output_df.at[idx, "Total Value(E + F + G)"] = str(
                    float(output_df.at[idx, "Cash"]) + float(output_df.at[idx, "Other segment"])
                )
                break

# ✅ Process Live Allocation file
# ✅ Process Live Allocation file
live_file = None
for f in os.listdir(folder):
    if f.startswith("Report_Live") and f.endswith(".xlsx"):
        live_file = os.path.join(folder, f)
        break

if live_file is None:
    print("❌ Live Report file not found!")
else:
    live_df = pd.read_excel(live_file)

    # ✅ Rename only if columns exist
    if "ACCOUNTID" in live_df.columns:
        live_df = live_df.rename(columns={"ACCOUNTID": "Client Code"})
    if "Peak Files Number" in live_df.columns:
        live_df = live_df.rename(columns={"Peak Files Number": "Indicator"})

    # ---------- STRICT NORMALIZATION FOR KEYS ----------
    # Ensure keys exist
    if "Client Code" not in live_df.columns:
        live_df["Client Code"] = ""
    if "Indicator" not in live_df.columns:
        live_df["Indicator"] = ""

    if "Client Code" not in output_df.columns:
        output_df["Client Code"] = ""
    if "Indicator" not in output_df.columns:
        output_df["Indicator"] = ""

    # String-clean Client Code (trim)
    live_df["Client Code"] = live_df["Client Code"].astype(str).str.strip()
    output_df["Client Code"] = output_df["Client Code"].astype(str).str.strip()

    # Indicator: keep as 2-digit zero-padded string "01".."08"
    def norm_indicator(s):
        s = ("" if pd.isna(s) else str(s)).strip()
        # Extract first run of digits; if none, return empty
        m = re.search(r"\d+", s)
        if not m:
            return ""
        return m.group(0).zfill(2)

    live_df["Indicator"] = live_df["Indicator"].apply(norm_indicator)
    output_df["Indicator"] = output_df["Indicator"].apply(norm_indicator)
    # ---------- END NORMALIZATION ----------
    # === Extract NCDEX Live Column & Merge ===
        # Rename for merge
    live_df = live_df.rename(columns={"Total_Deposit_NCDEX (U + V)": "Total_Deposit_NCDEX_LIVE"})

    # Keep only required columns
    live_ncdex = live_df[["Client Code", "Indicator", "Total_Deposit_NCDEX_LIVE"]].drop_duplicates()

    # Merge into output_df
    output_df = output_df.merge(
    live_ncdex,
    on=["Client Code", "Indicator"],
    how="left")

    # Final rename as requested
    output_df = output_df.rename(columns={
    "Total_Deposit_NCDEX_LIVE": "Total_Deposit_NCDEX [ (U + V) LIVE ALLOCATION ]"})

    # ✅ Ensure required numeric columns exist
    cols = [
        "Total Deposit CM (L + M)",
        "Total Deposit FO (O + P)",
        "Total_Deposit_NCDEX (U + V)",
        "CM", "FM", "NCDEX"
    ]
    for c in cols:
        if c not in live_df.columns:
            live_df[c] = 0

    # ✅ Live Allocation Calculations
    live_df["Total Allocated from (Live Allocation), excluding MCX"] = (
        pd.to_numeric(live_df["Total Deposit CM (L + M)"], errors="coerce").fillna(0) +
        pd.to_numeric(live_df["Total Deposit FO (O + P)"], errors="coerce").fillna(0) +
        pd.to_numeric(live_df["Total_Deposit_NCDEX_LIVE"], errors="coerce").fillna(0)
    )

    # NOTE: live_df["NCDEX"] below is the Master NCDEX{peak} peak margin.
    # For peaks 06/07/08 this is now a REAL value (previously always 0).
    live_df["Sum of Peak Margin from (Live Allocation), excluding MCX"] = (
        pd.to_numeric(live_df["CM"], errors="coerce").fillna(0) +
        pd.to_numeric(live_df["FM"], errors="coerce").fillna(0) +
        pd.to_numeric(live_df["NCDEX"], errors="coerce").fillna(0)
    )

    live_df["Short/Excess from (Live allocation) (AA-AB)"] = (
        live_df["Total Allocated from (Live Allocation), excluding MCX"] -
        live_df["Sum of Peak Margin from (Live Allocation), excluding MCX"]
    )

    # ===== NEW: carry the raw NCDEX peak value through, so the END-file block
    # below can decide whether a peak value already exists for this client. =====
    live_df["_NCDEX_PEAK_VALUE"] = pd.to_numeric(live_df["NCDEX"], errors="coerce").fillna(0)

    # ✅ Save updated Live file
    live_output = live_file.replace(".xlsx", "_Updated.xlsx")
    live_df.to_excel(live_output, index=False)
    print("✅ Live Allocation file updated")

    # ✅ Keep one row per (Client Code, Indicator) from live_df to avoid duplication on merge
    keep_cols = [
        "Client Code", "Indicator",
        "Total Allocated from (Live Allocation), excluding MCX",
        "Sum of Peak Margin from (Live Allocation), excluding MCX",
        "Short/Excess from (Live allocation) (AA-AB)",
        "_NCDEX_PEAK_VALUE",
        # NEW: carried through so indicators 05-08 can source CM DEPOSIT /
        # FO DEPOSIT from here (CM/FO peak files only go up to i04).
        "Total Deposit CM (L + M)",
        "Total Deposit FO (O + P)",
    ]
    live_keyed = (
        live_df[keep_cols]
        .drop_duplicates(subset=["Client Code", "Indicator"], keep="last")
    )

    # ✅ Merge on BOTH keys
    before_rows = len(output_df)
    output_df = output_df.merge(
        live_keyed,
        on=["Client Code", "Indicator"],
        how="left"
    )
    after_rows = len(output_df)

    print(f"✅ Live Allocation file values merged on Client Code + Indicator (rows: {before_rows} → {after_rows})")

    # === Ensure NCDEX Live column persists even after second merge ===
    if "Total_Deposit_NCDEX [ (U + V) LIVE ALLOCATION ]" not in output_df.columns:
        output_df["Total_Deposit_NCDEX [ (U + V) LIVE ALLOCATION ]"] = None

# --- Safety check: ensure the two 05-08 deposit source columns exist even if
# the Live Allocation file was missing entirely today ---
for _c in ("Total Deposit CM (L + M)", "Total Deposit FO (O + P)"):
    if _c not in output_df.columns:
        output_df[_c] = 0

# ✅ Add Final Remarks (L-I)

# L = Short/Excess from (Live allocation) (AA-AB)
# I = Short(H - D)
L = pd.to_numeric(output_df["Short/Excess from (Live allocation) (AA-AB)"], errors="coerce").fillna(0)
I = pd.to_numeric(output_df["Short(H - D)"], errors="coerce").fillna(0)

diff = L - I

output_df["Final Remarks (L-I)"] = diff.apply(
    lambda x: "COVERED" if x >= 0 else f"{round(x,2)}_SHORT"
)



# ============================================================
# APPLY END FILE MARGINS BEFORE SAVE (INDICATORS 05–08 ONLY)
# ============================================================
# ===== CHANGED (NCDEX 5 -> 8) =====
# PEAK MARGIN HAS PRIORITY. THE NCDEX END-OF-DAY FILE IS A FALLBACK ONLY.
#
#   FA END  : still added for 05,06,07,08  (CM/FO have only i01-i04 peaks)
#   CM END  : still added for 05,06,07,08  (same reason)
#   NCDEX   : for 06,07,08 the END value (01292_MGN_*) is added ONLY IF the
#             client has NO row in the matching NCDEX peak file
#             (NCCL_MARGIN_REP_01292_<date>_06/07/08) AND the peak value that
#             came through Live Allocation is 0.
#             If the peak file exists and covers the client, the peak value is
#             already inside "Sum of Peak Margin ..." and adding the EOD value
#             again would DOUBLE COUNT.
# ============================================================

# Normalize client codes
output_df["Client Code"] = output_df["Client Code"].astype(str).str.strip()

# Prepare FA end
fa_end_df["Client Code"] = fa_end_df["Client Code"].astype(str).str.strip()
fa_end_df["Min Margin(A)"] = fa_end_df["Min Margin(A)"].apply(clean_num)

# Prepare CM end
cm_end_df["Client Code"] = cm_end_df["Client Code"].astype(str).str.strip()
cm_end_df["Min Margin(A)"] = cm_end_df["Min Margin(A)"].apply(clean_num)

# Prepare NCDEX end
if not ncdex_end_df.empty:
    ncdex_end_df["Client ID"] = (
        ncdex_end_df["Client ID"].astype(str).str.replace("'","").str.strip()
    )
    if "Initial Mrgin (Initial + Extreme Loss)" in ncdex_end_df.columns:
        ncdex_end_df["Initial Mrgin (Initial + Extreme Loss)"] = (
            ncdex_end_df["Initial Mrgin (Initial + Extreme Loss)"].apply(clean_num)
        )

# ===== NEW: build a per-peak lookup of client codes present in each NCDEX
# peak file (01..08). Used to decide peak-vs-fallback. =====
ncdex_peak_clients = {}
for _k, _dfp in ncdex_dfs.items():
    _colmap = {str(c).strip().lower(): c for c in _dfp.columns}
    _cc_col = _colmap.get("client code")
    if _cc_col is None:
        ncdex_peak_clients[_k] = set()
        continue
    ncdex_peak_clients[_k] = set(
        _dfp[_cc_col].astype(str).str.replace("'", "").str.strip().str.upper()
    )

# Make sure the helper column exists even if the Live file was missing
if "_NCDEX_PEAK_VALUE" not in output_df.columns:
    output_df["_NCDEX_PEAK_VALUE"] = 0

# Update values
updated = 0
ncdex_peak_used = 0
ncdex_fallback_used = 0

for idx, row in output_df.iterrows():

    indicator = row["Indicator"]
    if indicator not in ("05","06","07","08"):
        continue

    cc = row["Client Code"]
    add = 0

    # --- FA END (always for 05,06,07,08) ---
    v = fa_end_df.loc[fa_end_df["Client Code"] == cc, "Min Margin(A)"]
    if not v.empty:
        add += float(v.iloc[0])

    # --- CM END (always for 05,06,07,08) ---
    v = cm_end_df.loc[cm_end_df["Client Code"] == cc, "Min Margin(A)"]
    if not v.empty:
        add += float(v.iloc[0])

    # --- NCDEX (ONLY for 06,07,08) : PEAK FIRST, END FILE AS FALLBACK ---
    if indicator in ("06","07","08"):
        peak_num = int(indicator)

        # Does the NCDEX peak file for this indicator cover this client?
        client_in_peak_file = cc.upper() in ncdex_peak_clients.get(peak_num, set())

        # Did a non-zero peak value actually flow in via Live Allocation?
        peak_value = clean_num(row.get("_NCDEX_PEAK_VALUE", 0))

        if client_in_peak_file or peak_value != 0:
            # ✅ Peak margin already counted -> do NOT add the end-of-day value
            ncdex_peak_used += 1
        elif not ncdex_end_df.empty:
            # ↩️ Fallback: no peak data for this client/indicator, use EOD file
            v = ncdex_end_df.loc[
                ncdex_end_df["Client ID"] == cc,
                "Initial Mrgin (Initial + Extreme Loss)"
            ]
            if not v.empty:
                add += float(v.iloc[0])
                ncdex_fallback_used += 1

    # --- APPLY ADDITIONAL MARGIN ---
    if add > 0:
        base = clean_num(row["Sum of Peak Margin from (Live Allocation), excluding MCX"])
        output_df.at[idx, "Sum of Peak Margin from (Live Allocation), excluding MCX"] = base + add
        updated += 1

print(f"✔ Peak Margin updated for {updated} clients (05–08)")
print(f"   ↳ NCDEX peak value used (no EOD added) : {ncdex_peak_used}")
print(f"   ↳ NCDEX EOD fallback applied           : {ncdex_fallback_used}")

# Drop the internal helper column before saving
output_df = output_df.drop(columns=["_NCDEX_PEAK_VALUE"], errors="ignore")

# --- FINAL SAFETY CHECK: ensure NCDEX Live column exists ---
col_name = "Total_Deposit_NCDEX [ (U + V) LIVE ALLOCATION ]"
if col_name not in output_df.columns:
    output_df[col_name] = ""

# === Move NCDEX LIVE column after Final Remarks ===
col = "Total_Deposit_NCDEX [ (U + V) LIVE ALLOCATION ]"
if col in output_df.columns:
    cols = list(output_df.columns)
    cols.insert(cols.index("Final Remarks (L-I)") + 1, cols.pop(cols.index(col)))
    output_df = output_df[cols]




# ============================================================
# FINAL RECALCULATION (AFTER ALL UPDATES + END-FILE MARGINS)
# ============================================================

# ============================================================
# FINAL RECALCULATION AFTER ALL UPDATES (END-FILE MARGINS APPLIED)
# ============================================================

# --- Step 0: Rename Original Column to New Name ---
old_col = "Short/Excess from (Live allocation) (AA-AB)"
new_col = "Short/Excess (J-K)"

if old_col in output_df.columns:
    output_df = output_df.rename(columns={old_col: new_col})
elif new_col not in output_df.columns:
    # Create column if missing
    output_df[new_col] = 0

# --- Step 1: Reset values to 0 ---
output_df[new_col] = 0

# --- Step 2: Recalculate (J-K) = AA - AB ---
AA = pd.to_numeric(output_df["Total Allocated from (Live Allocation), excluding MCX"],
                   errors="coerce").fillna(0)

AB = pd.to_numeric(output_df["Sum of Peak Margin from (Live Allocation), excluding MCX"],
                   errors="coerce").fillna(0)

output_df[new_col] = AA - AB

# --- Step 3: Recalculate Final Remarks (L - I) ---
# L = new recalculated Short/Excess (J-K)
L = output_df[new_col]

# I = Original Shortage Column
I = pd.to_numeric(output_df["Short(H - D)"], errors="coerce").fillna(0)

output_df["Final Remarks (L-I)"] = (L - I).apply(
    lambda x: "COVERED" if x >= 0 else f"{round(x, 2)}_SHORT"
)




# ============================================================
# NEW BLOCK : CM / FO / NSE COMM / NCDEX  DEPOSIT vs PEAK MARGIN
# ============================================================
# DEPOSIT SOURCES
#   CM DEPOSIT       ind 01-04 -> SA_CM_0X sheet   , col "Total Value of Collateral(D)"
#                    ind 05-08 -> Live Allocation  , col "Total Deposit CM (L + M)"
#   FO DEPOSIT       ind 01-04 -> SA_FO_0X sheet   , col "Total Value of Collateral(D)"
#                    ind 05-08 -> Live Allocation  , col "Total Deposit FO (O + P)"
#   NSE COMM DEPOSIT ind 01-08 -> SA_NSECOMM_0X    , col "Total Value of Collateral(D)"
#                    (O_SA01 files run 1-8, so no 05-08 fallback is needed;
#                     any file that didn't arrive simply gives 0)
#   NCDEX DEPOSIT    ind 01-08 -> SA_NCDEX_0X      , col "Collateral_Value_Snapshot"
#
# PEAK / MARGIN SOURCES  (all from <date>_MASTER.xlsx, sheet "Main", by ClntId)
#   CM PEAK        ind 01-04 -> CM1..CM4        | ind 05-08 -> Final Cash
#   FO PEAK        ind 01-04 -> FM1..FM4        | ind 05-08 -> Final Fut
#   NSE COMM PEAK  ind 01-08 -> NSE_COMM_1..8
#   NCDEX MARGIN   ind 01-08 -> NCDEX1..NCDEX8
#
# CM/FO use "Final Cash"/"Final Fut" for peaks 05-08 because the CM & FO peak
# files only ever go up to i04 - there is no CM5..CM8 / FM5..FM8 in Master.
# ============================================================

def lookup_peak_deposit(dfs_dict, client_code, indicator, value_col):
    """Read `value_col` for a client from the peak sheet matching `indicator`."""
    try:
        ind = int(indicator)
    except (TypeError, ValueError):
        return 0
    dfp = dfs_dict.get(ind)
    if dfp is None or dfp.empty:
        return 0
    colmap = {str(c).strip().lower(): c for c in dfp.columns}
    cc_col = colmap.get("client code")
    val_col = colmap.get(value_col.strip().lower())
    if cc_col is None or val_col is None:
        return 0
    cc = str(client_code).strip().upper()
    match = dfp[dfp[cc_col].astype(str).str.replace("'", "").str.strip().str.upper() == cc]
    if match.empty:
        return 0
    return clean_num(match.iloc[0][val_col])

COLLATERAL_COL = "Total Value of Collateral(D)"
NCDEX_COLLATERAL_COL = "Collateral_Value_Snapshot"

cm_dep_vals, fo_dep_vals, ncomm_dep_vals, ncdex_dep_vals = [], [], [], []
cm_peak_vals, fo_peak_vals, ncomm_peak_vals, ncdex_mgn_vals = [], [], [], []

for _, row in output_df.iterrows():
    cc = str(row["Client Code"]).strip()
    ind_raw = row["Indicator"]
    try:
        ind = int(ind_raw)
    except (TypeError, ValueError):
        ind = None

    # ---------- DEPOSITS ----------
    if ind in (1, 2, 3, 4):
        cm_dep = lookup_peak_deposit(cm_dfs, cc, ind, COLLATERAL_COL)
        fo_dep = lookup_peak_deposit(fa_dfs, cc, ind, COLLATERAL_COL)
    elif ind in (5, 6, 7, 8):
        cm_dep = clean_num(row.get("Total Deposit CM (L + M)", 0))
        fo_dep = clean_num(row.get("Total Deposit FO (O + P)", 0))
    else:
        cm_dep = fo_dep = 0

    ncomm_dep = lookup_peak_deposit(ncomm_dfs, cc, ind, COLLATERAL_COL) if ind else 0
    ncdex_dep = lookup_peak_deposit(ncdex_dfs, cc, ind, NCDEX_COLLATERAL_COL) if ind else 0

    # ---------- PEAK MARGINS (from Master) ----------
    cm_peak_vals.append(get_master_value(cc, "CM", ind))
    fo_peak_vals.append(get_master_value(cc, "FO", ind))
    ncomm_peak_vals.append(get_master_value(cc, "NSECOMM", ind))
    ncdex_mgn_vals.append(get_master_value(cc, "NCDEX", ind))

    cm_dep_vals.append(cm_dep)
    fo_dep_vals.append(fo_dep)
    ncomm_dep_vals.append(ncomm_dep)
    ncdex_dep_vals.append(ncdex_dep)

output_df["CM DEPOSIT"] = cm_dep_vals
output_df["FO DEPOSIT"] = fo_dep_vals
output_df["NSE COMM DEPOSIT"] = ncomm_dep_vals
output_df["CM PEAK"] = cm_peak_vals
output_df["FO PEAK"] = fo_peak_vals
output_df["NSE COMM PEAK"] = ncomm_peak_vals

# --- Roll-ups for the three NCL segments ---
output_df["Total Deposit (CM + FO + NSE COMM)"] = (
    output_df["CM DEPOSIT"] + output_df["FO DEPOSIT"] + output_df["NSE COMM DEPOSIT"]
)
output_df["Total Peak Margin (CM + FO + NSE COMM)"] = (
    output_df["CM PEAK"] + output_df["FO PEAK"] + output_df["NSE COMM PEAK"]
)
output_df["EXCESS(NCL)"] = (
    output_df["Total Deposit (CM + FO + NSE COMM)"] -
    output_df["Total Peak Margin (CM + FO + NSE COMM)"]
)

# --- NCDEX pool (kept separate from the NCL pool) ---
output_df["NCDEX DEPOSIT"] = ncdex_dep_vals
output_df["NCDEX MARGIN"] = ncdex_mgn_vals
output_df["EXCESS(NCDEX)"] = output_df["NCDEX DEPOSIT"] - output_df["NCDEX MARGIN"]

# ============================================================
# FINAL COVERAGE WATERFALL : Short(H - D)  vs  EXCESS(NCL) then EXCESS(NCDEX)
# ============================================================
# The MCX shortfall is covered first out of the NCL excess (CM + FO + NSE COMM),
# and whatever is still uncovered is then covered out of the NCDEX excess.
# A NEGATIVE excess in either pool is treated as ZERO - that segment simply
# contributes nothing towards covering the MCX shortfall; it does not make the
# MCX shortfall bigger.
# ============================================================

def coverage_remark(short_val, excess_ncl, excess_ncdex):
    short_amt = clean_num(short_val)
    ncl = max(clean_num(excess_ncl), 0)      # floor negatives at 0
    ncdex = max(clean_num(excess_ncdex), 0)  # floor negatives at 0

    if short_amt <= 0:
        return "NO SHORT"
    if ncl >= short_amt:
        return "COVERED WITH NCL"
    remaining = short_amt - ncl
    if ncdex >= remaining:
        return "COVERED WITH NCDEX"
    return f"{round(remaining - ncdex, 2)}_SHORT"

output_df["Final Coverage (NCL - NCDEX)"] = [
    coverage_remark(r["Short(H - D)"], r["EXCESS(NCL)"], r["EXCESS(NCDEX)"])
    for _, r in output_df.iterrows()
]

_cov = output_df["Final Coverage (NCL - NCDEX)"]
print("\n📊 Final coverage summary:")
print(f"   ↳ NO SHORT           : {(_cov == 'NO SHORT').sum()}")
print(f"   ↳ COVERED WITH NCL   : {(_cov == 'COVERED WITH NCL').sum()}")
print(f"   ↳ COVERED WITH NCDEX : {(_cov == 'COVERED WITH NCDEX').sum()}")
print(f"   ↳ STILL SHORT        : {_cov.str.endswith('_SHORT').sum()}")

# --- Drop the two Live-Allocation helper columns used only as 05-08 sources ---
output_df = output_df.drop(
    columns=["Total Deposit CM (L + M)", "Total Deposit FO (O + P)"], errors="ignore"
)

# --- Drop the old Live-Allocation columns from the final output (no longer
# wanted in the report). They stay computed internally above because the
# 05-08 END-FILE MARGIN top-up still updates "Sum of Peak Margin from (Live
# Allocation), excluding MCX" - we just don't show any of this in the sheet
# anymore, in favour of the new CM/FO/NSE COMM/NCDEX DEPOSIT & PEAK columns. ---
output_df = output_df.drop(
    columns=[
        "Total Allocated from (Live Allocation), excluding MCX",
        "Sum of Peak Margin from (Live Allocation), excluding MCX",
        "Short/Excess (J-K)",
        "Final Remarks (L-I)",
        "Total_Deposit_NCDEX [ (U + V) LIVE ALLOCATION ]",
    ],
    errors="ignore",
)


# ✅ SAVE FINAL WORKBOOK
out_file = os.path.join(folder,"MCX_Intra_Output.xlsx")


with pd.ExcelWriter(out_file,engine='xlsxwriter') as writer:
    output_df.to_excel(excel_writer=writer, sheet_name="Final_Shortage_MCX_INTRA", index=False)
    for key,df in fa_dfs.items(): df.to_excel(excel_writer=writer, sheet_name=f"SA_FO_0{key}", index=False)
    fa_end_df.to_excel(excel_writer=writer, sheet_name="SA_FO_END", index=False)
    for key,df in cm_dfs.items(): df.to_excel(excel_writer=writer, sheet_name=f"SA_CM_0{key}", index=False)
    cm_end_df.to_excel(excel_writer=writer, sheet_name="SA_CM_END", index=False)
    # ===== NEW: NSE COMM (O_SA01) peak sheets - only the ones that arrived =====
    for key,df in ncomm_dfs.items(): df.to_excel(excel_writer=writer, sheet_name=f"SA_NSECOMM_0{key}", index=False)
    for key,df in mcx_dfs.items(): df.to_excel(excel_writer=writer, sheet_name=f"SA_MCX_0{key}", index=False)
    # ===== CHANGED: now writes SA_NCDEX_01 .. SA_NCDEX_08 =====
    for key,df in ncdex_dfs.items(): df.to_excel(excel_writer=writer, sheet_name=f"SA_NCDEX_0{key}", index=False)
    if not ncdex_end_df.empty:
        ncdex_end_df.to_excel(excel_writer=writer, sheet_name="NCDEX_END", index=False)



    # ✅ Auto width Final sheet
    ws = writer.sheets["Final_Shortage_MCX_INTRA"]
    for col_idx,col in enumerate(output_df.columns):
        values = output_df[col].astype(str).tolist()
        width = max([len(col)] + [len(v) for v in values]) + 2
        ws.set_column(col_idx,col_idx,width)

print("✅ All sheets saved with auto-width!")

# ------------------ FILE PRESENCE CHECK ------------------

file_patterns = {
    "MCX Intra File": lambda f: f.startswith(prefix) and f.endswith(".csv"),

    # FA Peak Files
    "FA i01": lambda f: re.match(r"^F_SA01_P_06436_\d{8}_i01\.lis$", f),
    "FA i02": lambda f: re.match(r"^F_SA01_P_06436_\d{8}_i02\.lis$", f),
    "FA i03": lambda f: re.match(r"^F_SA01_P_06436_\d{8}_i03\.lis$", f),
    "FA i04": lambda f: re.match(r"^F_SA01_P_06436_\d{8}_i04\.lis$", f),

    "FA_END": lambda f: re.match(r"^F_SA01_06436_\d{8}\.lis$", f),

    "CM i01": lambda f: re.match(r"^C_SA01_P_06436_\d{8}_i01\.lis$", f),
    "CM i02": lambda f: re.match(r"^C_SA01_P_06436_\d{8}_i02\.lis$", f),
    "CM i03": lambda f: re.match(r"^C_SA01_P_06436_\d{8}_i03\.lis$", f),
    "CM i04": lambda f: re.match(r"^C_SA01_P_06436_\d{8}_i04\.lis$", f),

    "CM_END": lambda f: re.match(r"^C_SA01_06436_\d{8}\.lis$", f),

    # Mcx peak files
    "MCX_01 Peak File": lambda f: re.match(r"^MCX_PeakMargin56720_\d{8}_01\.csv$", f),
    "MCX_02 Peak File": lambda f: re.match(r"^MCX_PeakMargin56720_\d{8}_02\.csv$", f),
    "MCX_03 Peak File": lambda f: re.match(r"^MCX_PeakMargin56720_\d{8}_03\.csv$", f),
    "MCX_04 Peak File": lambda f: re.match(r"^MCX_PeakMargin56720_\d{8}_04\.csv$", f),
    "MCX_05 Peak File": lambda f: re.match(r"^MCX_PeakMargin56720_\d{8}_05\.csv$", f),
    "MCX_06 Peak File": lambda f: re.match(r"^MCX_PeakMargin56720_\d{8}_06\.csv$", f),
    "MCX_07 Peak File": lambda f: re.match(r"^MCX_PeakMargin56720_\d{8}_07\.csv$", f),
    "MCX_08 Peak File": lambda f: re.match(r"^MCX_PeakMargin56720_\d{8}_08\.csv$", f),

    # NCDEX Peak Files
    # ===== CHANGED: split into 8 individual entries so the email names the
    # exact NCDEX file that did not arrive (was one combined 0[1-5] check) =====
    "NCDEX_01 Peak File": lambda f: re.match(r"^NCCL_MARGIN_REP_01292_\d{8}_01\.csv$", f),
    "NCDEX_02 Peak File": lambda f: re.match(r"^NCCL_MARGIN_REP_01292_\d{8}_02\.csv$", f),
    "NCDEX_03 Peak File": lambda f: re.match(r"^NCCL_MARGIN_REP_01292_\d{8}_03\.csv$", f),
    "NCDEX_04 Peak File": lambda f: re.match(r"^NCCL_MARGIN_REP_01292_\d{8}_04\.csv$", f),
    "NCDEX_05 Peak File": lambda f: re.match(r"^NCCL_MARGIN_REP_01292_\d{8}_05\.csv$", f),
    "NCDEX_06 Peak File": lambda f: re.match(r"^NCCL_MARGIN_REP_01292_\d{8}_06\.csv$", f),
    "NCDEX_07 Peak File": lambda f: re.match(r"^NCCL_MARGIN_REP_01292_\d{8}_07\.csv$", f),
    "NCDEX_08 Peak File": lambda f: re.match(r"^NCCL_MARGIN_REP_01292_\d{8}_08\.csv$", f),


    "NCDEX_END": lambda f: re.match(r"^01292_MGN_\d+\.CSV$", f),

    # Live File
    "Live File": lambda f: f.startswith("Report_Live") and f.endswith(".xlsx"),

    # Master File (mandatory - auto-copied here by Master_File_final.py)
    "Master File": lambda f: re.match(r"^\d{8}_MASTER\.xlsx$", f)
}

files = os.listdir(folder)
missing_files = []

for desc, rule in file_patterns.items():
    if not any(rule(f) for f in files):
        missing_files.append(desc)






# ---------- Print Missing Files Info ----------
if missing_files:
    print("\n⚠️ The MCX_INTRA file is generated but the following files are missing:")
    print(", ".join(missing_files))
else:
    print("\n✅ All files present. MCX_INTRA file generated successfully.")

# Create note line for email (if any files are missing)
if missing_files:
    missing_note = (
        f"<br><b>⚠️ The MCX_INTRA file is generated but the following files are missing:</b><br>"
        + ", ".join(missing_files)
        + "<br><br>"
    )
else:
    missing_note = ""

# Note for email when MCX_INTRASAR file did not supply a peak indicator
# (Column P) for ANY client today -> whole file fell back to old logic
if peak_indicator_missing_for_day:
    intrasar_indicator_note = (
        "<br><b>⚠️ PEAK FILE INDICATOR NOT GIVEN IN MCX INTRASAR FILE. "
        "PLEASE CHECK. DATA IS CREATED WITH THE OLD LOGIC.</b><br><br>"
    )
else:
    intrasar_indicator_note = ""

# ===== NOTE FOR THE OPTIONAL NSE COMM FILES (these never stop the run) =====
# The O_SA01 (NSE COMM) peak files are deliberately kept OUT of the hard
# pre-check because they don't arrive every day. When some are absent the
# report is still produced - NSE COMM DEPOSIT is just 0 for those peaks - and
# the recipients are told so in the email body.
if ncomm_missing:
    optional_notes = (
        "<br><b>⚠️ The data is generated WITHOUT the following NSE COMM (O_SA01) peak file(s): </b>"
        + ", ".join(f"i{n}" for n in ncomm_missing)
        + ". NSE COMM DEPOSIT is 0 for these peaks.<br><br>"
    )
else:
    optional_notes = ""

# ===== Email Sending =====
import pandas as pd
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

# ===== File & Data =====
file_path = r"C:\MCX_INTRA_SAR\MCX_Intra_Output.xlsx"
df = pd.read_excel(file_path, sheet_name="Final_Shortage_MCX_INTRA")

# Get Business Date & convert format -> 24OCT2025
date_val = pd.to_datetime(df["Business Date"].iloc[0], errors="coerce")
email_date = date_val.strftime("%d%b%Y").upper()

# -------- COPY FINAL OUTPUT TO SHARED MARGIN SHORTFALL TRACK FOLDER --------
import shutil as _mst_shutil
try:
    _mst_date = date_val.strftime("%d%m%Y")
    _mst_dir = os.path.join(MARGIN_SHORTFALL_TRACK_DIR, _mst_date)
    os.makedirs(_mst_dir, exist_ok=True)
    _mst_shutil.copy2(out_file, os.path.join(_mst_dir, os.path.basename(out_file)))
    print(f"📤 Copied output to shared tracking folder: {_mst_dir}")
except Exception as e:
    print(f"⚠️ Could not copy output to shared tracking folder: {e}")

# ===== SMTP Credentials =====
SMTP_SERVER = "smtp3.netcore.co.in"
SMTP_PORT = 587
SMTP_USER = "kartavya.dhillon@mastertrust.co.in"
SMTP_PASS =  ""

# ===== Recipients =====
to_emails = [
   "rajeev.kumar2@mastertrust.co.in",
    "ashutosh.yadav@mastertrust.co.in",
    "rms_del@mastertrust.co.in"
]

cc_emails = [
    "sandeep.kumar2@mastertrust.co.in",
    "sumit.aggarwal@mastertrust.co.in"
]
    


# ===== Email Content =====
subject = "Final Data MCX_INTRA"
body = f"""
Dear Rajeev/Ashutosh,<br><br>

Please find the attached files of <b>{email_date}</b>.<br><br>


{intrasar_indicator_note}{optional_notes}{missing_note}

Warm regards,<br>
Kartavya Dhillon<br>
Master Capital Services Ltd<br>
📞 011-66331000 | Ext. 84
"""

# ===== Create Email =====
msg = MIMEMultipart()
msg["From"] = SMTP_USER
msg["To"] = ", ".join(to_emails)
msg["Cc"] = ", ".join(cc_emails)
msg["Subject"] = subject
msg.attach(MIMEText(body, "html"))

# ===== Attach File =====
with open(file_path, "rb") as f:
    part = MIMEBase("application", "octet-stream")
    part.set_payload(f.read())
encoders.encode_base64(part)
part.add_header("Content-Disposition", f"attachment; filename=MCX_Intra_Output.xlsx")
msg.attach(part)

# ===== Send Email =====
all_recipients = to_emails + cc_emails

import time
import smtplib

print("📨 Sending email with infinite retry (3 sec delay)...")

while True:
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, all_recipients, msg.as_string())

        print(f"✅ Email Sent Successfully for date {email_date}")
        break   # EXIT ONLY AFTER SUCCESS

    except smtplib.SMTPException as e:
        print(f"⚠️ SMTP error: {e}")
        print("🔁 Retrying in 3 seconds...\n")
        time.sleep(2)

    except Exception as e:
        print(f"⚠️ General error: {e}")
        print("🔁 Retrying in 3 seconds...\n")
        time.sleep(2)








import shutil
from datetime import datetime

# ---------------- BACKUP & CLEANUP PROCESS ----------------

# Get business date from final output excel
df_final = pd.read_excel(r"C:\MCX_INTRA_SAR\MCX_Intra_Output.xlsx", sheet_name="Final_Shortage_MCX_INTRA")
raw_date = df_final["Business Date"].iloc[0]

# Handle various formats safely
try:
    date_obj = pd.to_datetime(raw_date)
except:
    # if format like 24OCT2025
    date_obj = datetime.strptime(raw_date, "%d%b%Y")

backup_folder_name = date_obj.strftime("%d-%m-%Y")  # 24-10-2025 format
backup_root = r"C:\MCX_INTRA_SAR\BACKUP FILES AUTOMATED"

backup_path = os.path.join(backup_root, backup_folder_name)

# Create folder if not exists
os.makedirs(backup_path, exist_ok=True)

# File extensions to backup
file_patterns_to_backup = (".csv", ".CSV", ".lis", ".xlsx")

folder = r"C:\MCX_INTRA_SAR"

print(f"\n📦 Backing up files to: {backup_path}\n")

for file in os.listdir(folder):
    full_path = os.path.join(folder, file)

    # Skip folders and .py files
    if os.path.isdir(full_path) or file.endswith(".py") or file.endswith(".bat"):
        continue

    # Backup only relevant input files & output excel
    if file.endswith(file_patterns_to_backup):
        try:
            shutil.move(full_path, os.path.join(backup_path, file))
            print(f"✅ Moved: {file}")
        except Exception as e:
            print(f"⚠️ Could not move {file}: {e}")

print("\n🧹 Cleaning up complete! Only .py files kept in main directory.\n")
