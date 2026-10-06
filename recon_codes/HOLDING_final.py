# ================================================
#  NEW UPDATED DOWNLOADER WITH FULL RETRY LOGIC
# ================================================
import time
import requests
import gzip
import os
from datetime import datetime, timedelta

login_url = "https://www.connect2nse.com/extranet-api/login/2.0"
download_url = "https://www.connect2nse.com/extranet-api/member/file/download/2.0"

credentials = {
    "memberCode": "06436",
    "loginId": "M0213815",
    "password": "Yxs2Hg1/h0KUKKoF5SqEGQ=="}

INPUT_FOLDER = r"C:\T1 HOLDING"


# ============================================
# FUNCTION TO GET PREVIOUS TRADING DAY
# ============================================

# Add NSE holidays here (format YYYYMMDD)
NSE_HOLIDAYS = {
    "20250126", "20250308", "20250414", "20250418",
    "20250501", "20250815", "20251002", "20251031",
    "20251101", "20251225", "20260126", "20260303",
    "20260327", "20260331", "20260403", "20260414"
}

# =====================================================
# HOLIDAYS LIST — read from holidays_list.csv in THIS script's base folder
# One column only, header "Date", values in DDMMYYYY (e.g. 02102026).
# Any date listed here is skipped and the previous trading day is used.
# =====================================================
HOLIDAYS_FILE_NAME = "holidays_list.csv"
HOLIDAYS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), HOLIDAYS_FILE_NAME)

def load_holidays():
    """Return a set of datetime.date from holidays_list.csv (empty set if missing)."""
    import csv
    holidays = set()
    if not os.path.exists(HOLIDAYS_FILE):
        print(f"  WARNING: {HOLIDAYS_FILE} not found - only Sat/Sun will be skipped")
        return holidays
    try:
        with open(HOLIDAYS_FILE, "r", newline="", encoding="utf-8-sig") as _f:
            for _row in csv.reader(_f):
                if not _row:
                    continue
                raw = _row[0].strip().strip('"').strip("'")
                if not raw or raw.lower() == "date":
                    continue
                if raw.isdigit() and len(raw) == 7:      # Excel dropped the leading zero
                    raw = raw.zfill(8)
                try:
                    holidays.add(datetime.strptime(raw, "%d%m%Y").date())
                except ValueError:
                    print(f"  WARNING: ignoring invalid date in {HOLIDAYS_FILE_NAME}: {raw!r} (use DDMMYYYY)")
    except Exception as _e:
        print(f"  WARNING: could not read {HOLIDAYS_FILE}: {_e}")
    return holidays

def get_previous_trading_day():
    csv_holidays = load_holidays()
    day = datetime.now() - timedelta(days=1)

    while True:
        # Skip Saturday (5) & Sunday (6)
        if day.weekday() >= 5:
            day -= timedelta(days=1)
            continue

        # Skip market holidays (built-in list + holidays_list.csv)
        if day.strftime("%Y%m%d") in NSE_HOLIDAYS or day.date() in csv_holidays:
            day -= timedelta(days=1)
            continue

        return day




# ============================
# TOKEN FUNCTION (RETRY SAFE)
# ============================
def get_token():
    while True:
        try:
            r = requests.post(login_url, json=credentials, timeout=15)
            r.raise_for_status()
            token = r.json().get("token")
            if token:
                print("🔑 Login successful")
                return token
            print("❌ Login failed — retrying...")
        except Exception as e:
            print(f"⚠ Login error: {e} — retrying in 3 sec...")
        time.sleep(3)


# ============================
# GENERIC DOWNLOAD FUNCTION
# ============================
def safe_download(segment, folder, filename, token):
    """
    Handles:
      ✔ 401 → refresh token
      ✔ 504 → retry
      ✔ timeout → retry
      ✔ network error → retry
      ✔ infinite retries
    """
    local_gz = os.path.join(INPUT_FOLDER, filename)
    headers = {"Authorization": f"Bearer {token}"}
    params = {"segment": segment, "folderPath": folder, "filename": filename}

    attempt = 1
    while True:
        try:
            print(f"🔎 Downloading {filename} (try {attempt})")
            r = requests.get(download_url, headers=headers, params=params, timeout=15)

            if r.status_code == 401:
                print("🚫 401 Unauthorized → Refreshing token...")
                token = get_token()
                headers = {"Authorization": f"Bearer {token}"}
                attempt += 1
                continue

            if r.status_code == 504:
                print("⏳ 504 Timeout → Retrying in 3 sec…")
                time.sleep(3)
                attempt += 1
                continue

            if r.status_code == 404:
                print(f"❌ File not found on server: {filename}")
                return None, token

            r.raise_for_status()

            # save.gz
            with open(local_gz, "wb") as f:
                f.write(r.content)

            print(f"✅ Downloaded → {local_gz}")
            return local_gz, token

        except Exception as e:
            print(f"🌐 Network error: {e} → Retrying in 3 sec…")
            time.sleep(3)
            attempt += 1


# ============================
# GZ EXTRACTION
# ============================
def extract_gz(gz_file):
    if not gz_file:
        return None
    out_file = gz_file[:-3]
    try:
        with gzip.open(gz_file, "rb") as f_in:
            with open(out_file, "wb") as f_out:
                f_out.write(f_in.read())
        print(f"📂 Extracted → {out_file}")
        return out_file
    except Exception as e:
        print(f"❌ Extraction failed: {e}")
        return None


# ============================
# MAIN FUNCTION TO CALL
# ============================
def download_all_files():
    token = get_token()

    prev_dt = get_previous_trading_day()    ####(if we want to make any date to find file on any date change this line to this with needy date)
                                            ##### prev_dt = datetime.strptime("20260114", "%Y%m%d")
    



    ddmmyyyy = prev_dt.strftime("%d%m%Y")
    yyyymmdd = prev_dt.strftime("%Y%m%d")

    # --------------------------
    # 1️⃣ Download C File (CM)
    # --------------------------
    print("\n📁 Downloading C file (CM → Reports)")
    c_names = [
        f"C_06436_SEC_PLEDGE_{ddmmyyyy}_02.csv.gz",
        f"C_06436_SEC_PLEDGE_{ddmmyyyy}_02.html.gz"
    ]
    c_gz = None
    for fname in c_names:
        c_gz, token = safe_download("CM", "Reports", fname, token)
        if c_gz:
            c_csv = extract_gz(c_gz)
            break

    # --------------------------
    # 2️⃣ Download Trade File (CM)
    # --------------------------
    print("\n📁 Downloading Trade file (CM → Reports)")
    trade_name = f"Trade_NCL_CM_0_CM_06436_{yyyymmdd}_F_0000.csv.gz"
    trade_gz, token = safe_download("CM", "Reports", trade_name, token)
    if trade_gz:
        trade_csv = extract_gz(trade_gz)

    # --------------------------
    # 3️⃣ Download F File (FO)
    # --------------------------
    print("\n📁 Downloading F file (FO → Reports)")
    f_names = [
        f"F_06436_SEC_PLEDGE_{ddmmyyyy}_02.csv.gz",
        f"F_06436_SEC_PLEDGE_{ddmmyyyy}_02.html.gz"
    ]
    f_gz = None
    for fname in f_names:
        f_gz, token = safe_download("FO", "Reports", fname, token)
        if f_gz:
            f_csv = extract_gz(f_gz)
            break

    # Return results to your main script
    return {
        "c_gz": c_gz,
        "f_gz": f_gz,
        "trade_gz": trade_gz
    }


download_info = download_all_files()

import shutil
import gzip
import os

# ==========================================
# GENERIC EXTRACT + DELETE GZ FUNCTION
# ==========================================
def extract_and_delete_gz(gz_path):
    if not gz_path or not os.path.exists(gz_path):
        return None

    csv_path = gz_path.replace(".gz", "")  # always extract near the moved file

    try:
        with gzip.open(gz_path, "rb") as f_in:
            with open(csv_path, "wb") as f_out:
                f_out.write(f_in.read())
        print(f"📂 Extracted → {csv_path}")
    except Exception as e:
        print(f"❌ Extraction failed for {gz_path}: {e}")
        return None

    # delete only this gz
    try:
        os.remove(gz_path)
        print(f"🗑️ Deleted GZ → {gz_path}")
    except Exception as e:
        print(f"⚠ Could not delete {gz_path}: {e}")

    return csv_path


# ==========================================
# MOVE TRADE FILE + EXTRACT + DELETE .gz
# ==========================================
def move_trade_file(trade_gz_path, target_folder):
    if not trade_gz_path or not os.path.exists(trade_gz_path):
        print("❌ No Trade file found to move.")
        return None

    os.makedirs(target_folder, exist_ok=True)

    # FILE NAMES
    gz_name = os.path.basename(trade_gz_path)
    csv_name = gz_name.replace(".gz", "")

    src_csv = os.path.join(INPUT_FOLDER, csv_name)      # CSV wrongly created here
    dest_gz = os.path.join(target_folder, gz_name)
    dest_csv = os.path.join(target_folder, csv_name)

    # 💣 DELETE wrong CSV leftover in Holding 2.O (GUARANTEED FIX)
    if os.path.exists(src_csv):
        try:
            os.remove(src_csv)
            print(f"🧹 Removed leftover CSV from source: {src_csv}")
        except:
            pass

    # CUT + PASTE GZ FILE
    shutil.move(trade_gz_path, dest_gz)
    print(f"📁 Trade GZ moved → {dest_gz}")

    # EXTRACT TRADE CSV IN TARGET FOLDER ONLY
    try:
        with gzip.open(dest_gz, "rb") as f_in:
            with open(dest_csv, "wb") as f_out:
                f_out.write(f_in.read())
        print(f"📂 Extracted Trade CSV → {dest_csv}")
    except Exception as e:
        print(f"❌ Extraction failed: {e}")
        return None

    # DELETE GZ IN TARGET FOLDER
    try:
        os.remove(dest_gz)
        print(f"🗑️ Deleted GZ → {dest_gz}")
    except:
        pass

    return dest_csv

# ==========================================
# APPLY TO ALL FILES (C, F, TRADE)
# ==========================================
c_gz = download_info.get("c_gz")
f_gz = download_info.get("f_gz")
trade_gz = download_info.get("trade_gz")

print("\n==============================")
print(" CLEANING + MOVING FILES")
print("==============================")

# Extract & delete C.gz (stays in same folder)
c_csv = extract_and_delete_gz(c_gz)

# Extract & delete F.gz (stays in same folder)
f_csv = extract_and_delete_gz(f_gz)

# Move + extract + delete Trade.gz
target_trade_folder = r"C:\HOLDING"   # 🔥 Set your Trade destination here
trade_csv = move_trade_file(trade_gz, target_trade_folder)

print("\n==============================")
print(" FINAL RESULTS")
print("==============================")
print("C CSV:", c_csv)
print("F CSV:", f_csv)
print("TRADE CSV:", trade_csv)






import os
import glob
import polars as pl

# ------------------------------------------------------------
# SET FOLDER
# ------------------------------------------------------------
folder = r"C:\HOLDING"




# ------------------------------------------------------------
# 🔍 FULL FILE CHECK & DATE VALIDATION (STOP IF ANY ISSUE)
# ------------------------------------------------------------
import re
folder = r"C:\HOLDING"

# 1️⃣ CHECK TRADE FILE
trade_files = glob.glob(os.path.join(folder, "Trade_NCL_CM_0_CM_*_F_0000.csv"))
if not trade_files:
    raise FileNotFoundError("❌ ERROR: Trade file missing!")

trade_file = max(trade_files, key=os.path.getmtime)

# Extract TRADE DATE (YYYYMMDD)
trade_date_match = re.findall(r"_(\d{8})_F", trade_file)
if not trade_date_match:
    raise Exception("❌ ERROR: Cannot extract date from trade file!")
trade_date = trade_date_match[0]   # YYYYMMDD


# 2️⃣ CHECK HOLDING FILES
holding_files = sorted(
    glob.glob(os.path.join(folder, "HOLDING_*.csv")),
    key=os.path.getmtime
)

if len(holding_files) < 2:
    raise FileNotFoundError("❌ ERROR: Need at least TWO Holding files!")

old_holding_file = holding_files[-2]
new_holding_file = holding_files[-1]

# Extract DDMMYYYY from filenames HOLDING_DDMMYYYY.csv
old_hold_date = os.path.basename(old_holding_file)[8:16]   # DDMMYYYY
new_hold_date = os.path.basename(new_holding_file)[8:16]   # DDMMYYYY

if not (old_hold_date.isnumeric() and new_hold_date.isnumeric()):
    raise Exception("❌ ERROR: Holding file date format invalid!")


# ⚡ Convert HOLDING date to YYYYMMDD format for comparison
old_hold_date_fmt = old_hold_date[4:8] + old_hold_date[2:4] + old_hold_date[0:2]  # YYYYMMDD
new_hold_date_fmt = new_hold_date[4:8] + new_hold_date[2:4] + new_hold_date[0:2]  # YYYYMMDD


# 3️⃣ OLD HOLDING DATE MUST BE < NEW HOLDING DATE
if not (old_hold_date_fmt < new_hold_date_fmt):
    raise Exception(
        f"❌ ERROR: Old Holding date ({old_hold_date}) is NOT older than New Holding date ({new_hold_date})"
    )


# 4️⃣ TRADING FILE DATE MUST MATCH OLD HOLDING DATE
#    old hold date = DDMMYYYY → convert to YYYYMMDD
if trade_date != old_hold_date_fmt:
    raise Exception(
        f"❌ ERROR: Trading file date ({trade_date}) does NOT match "
        f"Old Holding file date ({old_hold_date})"
    )


# 5️⃣ CHECK RISK REPORT FILE
risk_files = glob.glob(os.path.join(folder, "RISK_REPORT_*.csv"))
if not risk_files:
    raise FileNotFoundError("❌ ERROR: Risk Report file missing!")

risk_file = max(risk_files, key=os.path.getmtime)

# Extract date from RISK_REPORT_DDMMYYYY.csv
risk_date_match = re.findall(r"RISK_REPORT_(\d{8})\.csv", risk_file)
if not risk_date_match:
    raise Exception("❌ ERROR: Risk Report date not found!")

risk_date = risk_date_match[0]  # DDMMYYYY


# 6️⃣ RISK REPORT DATE MUST MATCH NEW HOLDING DATE
if risk_date != new_hold_date:
    raise Exception(
        f"❌ ERROR: Risk Report date ({risk_date}) does NOT match "
        f"New Holding file date ({new_hold_date})"
    )

print("✅ File Check Completed — All Files Present & Dates Verified!\n")


# ------------------------------------------------------------
# 1️⃣ PICK LATEST TRADE FILE
# ------------------------------------------------------------
trade_files = glob.glob(os.path.join(folder, "Trade_NCL_CM_0_CM_*_F_0000.csv"))
if not trade_files:
    raise FileNotFoundError("No TRADE file found!")

trade_file = max(trade_files, key=os.path.getmtime)
print("Using Trade File:", trade_file)


# ------------------------------------------------------------
# 2️⃣ READ TRADE FILE
# ------------------------------------------------------------
trade_df = pl.read_csv(
    trade_file,
    infer_schema_length=0,
    ignore_errors=True
).with_columns([
    pl.col("TradQty").cast(pl.Float64, strict=False).fill_null(0)
])


# ------------------------------------------------------------
# 3️⃣ AGGREGATE TRADE → NETQTY
# ------------------------------------------------------------
trade_summary = (
    trade_df
    .group_by(["ClntId", "ISIN"])
    .agg([
        pl.when(pl.col("BuySellInd") == "B").then(pl.col("TradQty")).otherwise(0).sum().alias("BuyQty"),
        pl.when(pl.col("BuySellInd") == "S").then(pl.col("TradQty")).otherwise(0).sum().alias("SellQty")
    ])
)

trade_summary = trade_summary.with_columns([
    (pl.col("BuyQty") - pl.col("SellQty")).alias("NetQty")
])

trade_summary_file = os.path.join(folder, "TRADE_NETQTY_AGGREGATED.csv")
trade_summary.write_csv(trade_summary_file)

print("Trade summary saved at:", trade_summary_file)


# ------------------------------------------------------------
# 4️⃣ PICK OLD & NEW HOLDING FILES
# ------------------------------------------------------------
holding_files = sorted(
    glob.glob(os.path.join(folder, "HOLDING_*.csv")),
    key=os.path.getmtime
)

if len(holding_files) < 2:
    raise FileNotFoundError("Need at least TWO holding files!")

old_holding_file = holding_files[-2]   # previous date
new_holding_file = holding_files[-1]   # latest date

print("\nOld Holding File:", old_holding_file)
print("New Holding File:", new_holding_file)


# ------------------------------------------------------------
# 5️⃣ READ OLD HOLDING FILE → NetQty_Holding
# ------------------------------------------------------------
holding_df = pl.read_csv(
    old_holding_file,
    infer_schema_length=0,
    ignore_errors=True
).with_columns([
    pl.col("NetQty").cast(pl.Float64, strict=False).fill_null(0).alias("NetQty_Holding")
])

# ------------------------------------------------------------
# SYMBOL SOURCES (3 LEVEL FALLBACK)
# ------------------------------------------------------------

# 1️⃣ SYMBOL from NEW holding (priority 1)
symbol_new = pl.read_csv(
    new_holding_file,
    infer_schema_length=0,
    ignore_errors=True
).select([
    "AccountID",
    "ISIN",
    "Symbol"
])

# 2️⃣ SYMBOL from OLD holding (priority 2)
symbol_old = pl.read_csv(
    old_holding_file,
    infer_schema_length=0,
    ignore_errors=True
).select([
    "AccountID",
    "ISIN",
    "Symbol"
])

# 3️⃣ SYMBOL from TRADE FILE (priority 3 → TckrSymb)
symbol_trade = trade_df.select([
    pl.col("ClntId").alias("AccountID"),
    pl.col("ISIN"),
    pl.col("TckrSymb").alias("Symbol")
]).unique()



# ------------------------------------------------------------
# 6️⃣ READ TRADE SUMMARY FILE
# ------------------------------------------------------------
trade_summary = pl.read_csv(
    trade_summary_file,
    infer_schema_length=0,
    ignore_errors=True
).with_columns([
    pl.col("NetQty").cast(pl.Float64, strict=False).alias("NetQty_Trading")
]).rename({"ClntId": "AccountID"})


# ------------------------------------------------------------
# 7️⃣ MERGE OLD HOLDING + TRADE SUMMARY
# ------------------------------------------------------------
merged = trade_summary.join(
    holding_df,
    on=["AccountID", "ISIN"],
    how="left"
).with_columns([
    pl.col("NetQty_Holding").fill_null(0)
])


# ------------------------------------------------------------
# 8️⃣ CREATE New_NetQty (Old NetQty + Trade NetQty)
# ------------------------------------------------------------
merged = merged.with_columns([
    (pl.col("NetQty_Trading") + pl.col("NetQty_Holding")).alias("New_NetQty")
])


# ------------------------------------------------------------
# 9️⃣ READ NEW HOLDING FILE → NetQty_NewHolding
# ------------------------------------------------------------
new_hold_df = pl.read_csv(
    new_holding_file,
    infer_schema_length=0,
    ignore_errors=True
).with_columns([
    pl.col("NetQty")
        .cast(pl.Float64, strict=False)
        .fill_null(0)
        .fill_nan(0)
        .alias("NetQty_NewHolding")
])







# ------------------------------------------------------------
# 🔟 MERGE NEW HOLDING QTY
# ------------------------------------------------------------
merged = merged.join(
    new_hold_df.select(["AccountID", "ISIN", "NetQty_NewHolding"]),
    on=["AccountID", "ISIN"],
    how="left"
).with_columns([
    pl.col("NetQty_NewHolding").fill_null(0).fill_nan(0)
])

# ------------------------------------------------------------
# 1️⃣1️⃣ CREATE MATCH / MISMATCH COLUMN (simple version)
# ------------------------------------------------------------
merged = merged.with_columns(
    (pl.col("New_NetQty") == pl.col("NetQty_NewHolding"))
        .map_elements(lambda x: "Matched" if x else "Mismatched")
        .alias("Mismatch")
)



# ------------------------------------------------------------
# Extract dates from filenames & format them
# ------------------------------------------------------------

# TRADE FILE DATE → extract YYYYMMDD from: Trade_NCL_CM_0_CM_06436_20251128_F_0000.csv
trade_date_str = trade_file.split("_")[-3]
trade_date_fmt = f"{trade_date_str[6:8]}-{trade_date_str[4:6]}-{trade_date_str[0:4]}"

# OLD HOLDING FILE DATE → from HOLDING_28112025.csv
old_hold_date_str = os.path.basename(old_holding_file)[8:16]  # "28112025"
old_hold_date_fmt = f"{old_hold_date_str[:2]}-{old_hold_date_str[2:4]}-{old_hold_date_str[4:8]}"

# NEW HOLDING FILE DATE → from HOLDING_29112025.csv (example)
new_hold_date_str = os.path.basename(new_holding_file)[8:16]
new_hold_date_fmt = f"{new_hold_date_str[:2]}-{new_hold_date_str[2:4]}-{new_hold_date_str[4:8]}"



# ------------------------------------------------------------
# 1️⃣2️⃣ FINAL OUTPUT
# ------------------------------------------------------------
# ------------------------------------------------------------
# FINAL OUTPUT WITH RENAMED COLUMNS
# ------------------------------------------------------------
# ------------------------------------------------------------
# ADD SYMBOL (POLARS) → MERGE BEFORE SELECTING FINAL COLUMNS
# ------------------------------------------------------------
# ------------------------------------------------------------
# SYMBOL FALLBACK MERGE (new → old → trade)
# ------------------------------------------------------------
merged = merged.join(
    symbol_new.rename({"Symbol": "Symbol_new"}),
    on=["AccountID", "ISIN"],
    how="left"
)

merged = merged.join(
    symbol_old.rename({"Symbol": "Symbol_old"}),
    on=["AccountID", "ISIN"],
    how="left"
)

merged = merged.join(
    symbol_trade.rename({"Symbol": "Symbol_trade"}),
    on=["AccountID", "ISIN"],
    how="left"
)

merged = merged.with_columns([
    pl.when(
        pl.col("Symbol_new").cast(pl.Utf8).str.len_chars() > 0
    ).then(
        pl.col("Symbol_new")
    ).when(
        pl.col("Symbol_old").cast(pl.Utf8).str.len_chars() > 0
    ).then(
        pl.col("Symbol_old")
    ).when(
        pl.col("Symbol_trade").cast(pl.Utf8).str.len_chars() > 0
    ).then(
        pl.col("Symbol_trade")
    ).otherwise(
        pl.lit("")     # final literal
    ).alias("Symbol")
])

# remove temp cols
merged = merged.drop(["Symbol_new", "Symbol_old", "Symbol_trade"])

# ------------------------------------------------------------
# FINAL OUTPUT WITH SYMBOL AFTER ISIN
# ------------------------------------------------------------
output = merged.select([
    pl.col("AccountID").alias("ClientCode"),
    pl.col("ISIN"),
    pl.col("Symbol"),   # NEW COLUMN (AFTER ISIN)
    pl.col("NetQty_Trading").alias(f"Yesterday Trading File {trade_date_fmt}"),
    pl.col("NetQty_Holding").alias(f"Yesterday Holding File {old_hold_date_fmt}"),
    pl.col("New_NetQty").alias("NET QTY ( J + K )"),
    pl.col("NetQty_NewHolding").alias(f"Current Day Holding File {new_hold_date_fmt}"),
    pl.col("Mismatch").alias("Final Remarks")
])
# ------------------------------------------------------------
# SAVE FINAL OUTPUT TO EXCEL WITH AUTO COLUMN WIDTH
# ------------------------------------------------------------
import pandas as pd

out_file = os.path.join(folder, "Holding File Reconciliation.xlsx")

df = output.to_pandas()

# ------------------------------------------------------------
# ADD RISK REPORT COLUMNS (WITHOUT CHANGING EXISTING LOGIC)
# ------------------------------------------------------------

# 1. Load latest RISK_REPORT_*.csv
risk_files = glob.glob(os.path.join(folder, "RISK_REPORT_*.csv"))
if not risk_files:
    raise FileNotFoundError("No RISK_REPORT file found!")

risk_file = max(risk_files, key=os.path.getmtime)
print("Using Risk Report File:", risk_file)

# 2. Read only needed columns
risk_df = pl.read_csv(
    risk_file,
    infer_schema_length=0,
    ignore_errors=True
).select([
    "ACCOUNTID",
    "Zone",
    "Region",
    "Branch",
    "SubBranch",
    "ClientStatus",
    "TRADING_SOFTWARE_TYPE",
    "POAFLAG"
])

# 3. Convert to pandas for merging
risk_pd = risk_df.to_pandas()

# 4. Ensure join columns match type
df["ClientCode"] = df["ClientCode"].astype(str).str.strip()
risk_pd["ACCOUNTID"] = risk_pd["ACCOUNTID"].astype(str).str.strip()

# 5. Merge — LEFT JOIN (add risk columns before ClientCode)
df = risk_pd.merge(
        df,
        left_on="ACCOUNTID",
        right_on="ClientCode",
        how="right"
    )

# 6. Remove duplicate ACCOUNTID column after merge
df = df.drop(columns=["ACCOUNTID"], errors="ignore")

# 7. Reorder columns — put risk fields BEFORE ClientCode
risk_cols = ["Zone","Region","Branch","SubBranch","ClientStatus","TRADING_SOFTWARE_TYPE","POAFLAG"]
final_cols = risk_cols + [col for col in df.columns if col not in risk_cols]
df = df[final_cols]
# 8. Prevent Excel from auto-formatting ClientCode (exact value)
df["ClientCode"] = df["ClientCode"].astype(str)

# ------------------------------------------------------------
# REBUILD MERGE USING CODE-2 LOGIC TO GENERATE MISMATCHED FILE
# ------------------------------------------------------------
import polars as pl
import pandas as pd

print("\n🔄 Rebuilding data using Code-2 JOIN logic for mismatched file...")

# Read old holding
holding_df2 = pl.read_csv(
    old_holding_file,
    infer_schema_length=0,
    ignore_errors=True
).with_columns([
    pl.col("NetQty").cast(pl.Float64, strict=False).fill_null(0).alias("NetQty_Holding")
])

# Read trade summary
trade_summary2 = pl.read_csv(
    trade_summary_file,
    infer_schema_length=0,
    ignore_errors=True
).with_columns([
    pl.col("NetQty").cast(pl.Float64, strict=False).alias("NetQty_Trading")
]).rename({"ClntId": "AccountID"})

# LEFT JOIN → holding first (Code-2 logic)
merged2 = holding_df2.join(
    trade_summary2,
    on=["AccountID", "ISIN"],
    how="left"
).with_columns([
    pl.col("NetQty_Trading").fill_null(0)
])

# Calculate new net qty
merged2 = merged2.with_columns([
    (pl.col("NetQty_Trading") + pl.col("NetQty_Holding")).alias("New_NetQty")
])

# Load NEW holding
new_hold_df2 = pl.read_csv(
    new_holding_file,
    infer_schema_length=0,
    ignore_errors=True
).with_columns([
    pl.col("NetQty").cast(pl.Float64, strict=False).fill_null(0).alias("NetQty_NewHolding")
])

# Add New holding qty
merged2 = merged2.join(
    new_hold_df2.select(["AccountID", "ISIN", "NetQty_NewHolding"]),
    on=["AccountID", "ISIN"],
    how="left"
).with_columns([
    pl.col("NetQty_NewHolding").fill_null(0)
])

# Match/Mismatch flag
merged2 = merged2.with_columns(
    (pl.col("New_NetQty") == pl.col("NetQty_NewHolding"))
        .map_elements(lambda x: "Matched" if x else "Mismatched")
        .alias("Final Remarks")
)

# Add POAFLAG from risk report
risk_pl2 = pl.from_pandas(risk_pd)[["ACCOUNTID", "POAFLAG"]]

merged2 = merged2.join(
    risk_pl2.rename({"ACCOUNTID": "AccountID"}),
    on="AccountID",
    how="left"
)

# ------------------------------------------------------------
# SYMBOL FALLBACK LOGIC FOR MISMATCH
# ------------------------------------------------------------

merged2 = merged2.join(
    symbol_new.rename({"Symbol": "Symbol_new"}),
    on=["AccountID", "ISIN"],
    how="left"
)

merged2 = merged2.join(
    symbol_old.rename({"Symbol": "Symbol_old"}),
    on=["AccountID", "ISIN"],
    how="left"
)

merged2 = merged2.join(
    symbol_trade.rename({"Symbol": "Symbol_trade"}),
    on=["AccountID", "ISIN"],
    how="left"
)

merged2 = merged2.with_columns([
    pl.when(
        pl.col("Symbol_new").cast(pl.Utf8).str.len_chars() > 0
    ).then(
        pl.col("Symbol_new")
    ).when(
        pl.col("Symbol_old").cast(pl.Utf8).str.len_chars() > 0
    ).then(
        pl.col("Symbol_old")
    ).when(
        pl.col("Symbol_trade").cast(pl.Utf8).str.len_chars() > 0
    ).then(
        pl.col("Symbol_trade")
    ).otherwise(
        pl.lit("")
    ).alias("Symbol")
])

merged2 = merged2.drop(["Symbol_new", "Symbol_old", "Symbol_trade"])


# Filter final mismatch
filtered2 = merged2.filter(
    (pl.col("Final Remarks") == "Mismatched") &
    (pl.col("POAFLAG") == "Y")
)

# Keep only non-traded mismatches
filtered2 = filtered2.filter(
    pl.col("NetQty_Trading") == 0
)




# ------------------------------------------------------------
# CLEAN MISMATCH OUTPUT WITH SYMBOL (POLARS)
# ------------------------------------------------------------
filtered2_clean = merged2.filter(
    (pl.col("Final Remarks") == "Mismatched") &
    (pl.col("POAFLAG") == "Y") &
    (pl.col("NetQty_Trading") == 0)
).select([
    pl.col("AccountID").alias("ClientCode"),
    pl.col("ISIN"),
    pl.col("Symbol"),  # NEW → AFTER ISIN
    pl.col("POAFLAG"),
    pl.col("NetQty_Trading").alias(f"Yesterday Trading File {trade_date_fmt}"),
    pl.col("NetQty_Holding").alias(f"Yesterday Holding File {old_hold_date_fmt}"),
    pl.col("New_NetQty").alias("NET QTY ( J + K )"),
    pl.col("NetQty_NewHolding").alias(f"Current Day Holding File {new_hold_date_fmt}"),
    pl.col("Final Remarks")
])

# SAVE ONLY XLSX
mismatch_xlsx = os.path.join(folder, "Non-Traded Accounts Holding Mismatch Report.xlsx")

df_mismatch = filtered2_clean.to_pandas()
with pd.ExcelWriter(mismatch_xlsx, engine="xlsxwriter") as writer:
    df_mismatch.to_excel(writer, index=False, sheet_name="Mismatched")

    worksheet = writer.sheets["Mismatched"]
    for i, col in enumerate(df_mismatch.columns):
        max_len = max(df_mismatch[col].astype(str).map(len).max(), len(col))
        worksheet.set_column(i, i, max_len + 2)

print("📘 Clean Mismatched XLSX created:", mismatch_xlsx)

mismatch_attachment = mismatch_xlsx



with pd.ExcelWriter(out_file, engine='xlsxwriter') as writer:
    df.to_excel(writer, index=False, sheet_name="Final Output")
    worksheet = writer.sheets["Final Output"]

    # Auto-adjust column width
    for i, col in enumerate(df.columns):
        max_len = max(df[col].astype(str).map(len).max(), len(col))
        worksheet.set_column(i, i, max_len + 2)  # padding
print("\n✅ FINAL OUTPUT CREATED!")
print("📁 Saved at:", out_file)




# ------------------------------------------------------------
# 📧 SEND EMAIL WITH FINAL_MATCH_OUTPUT.xlsx ATTACHMENT
# ------------------------------------------------------------
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os

# SMTP CONFIG
SMTP_SERVER = "smtp3.netcore.co.in"
SMTP_PORT = 587
SMTP_USER = "kartavya.dhillon@mastertrust.co.in"
SMTP_PASS = "$Kartavya25*"

# TO / CC LIST
TO_LIST = ["rms_del@mastertrust.co.in"]

CC_LIST = []

# EMAIL SUBJECT & BODY
email_subject = "Reconciliation of Client Trades (NSE/BSE) with Holding File"

email_body = """
Dear Team,<br><br>
We are reconciling our holding file with the clients who have traded today on NSE and BSE. 
Their net quantities are being verified against the next day’s holding file.<br><br>
The second file contains accounts with no trades but holding mismatches due to either approved DIS-based transfers to other DPs or shares received into our demat from another demat account.<br><br>
Warm regards,<br>
Kartavya Dhillon<br>
Master Capital Services Ltd<br>
📞 011-66331000 | Ext. 84
"""

# FILE TO ATTACH
attachment_path = out_file  # Main reconciliation file
attachment_path_2 = mismatch_attachment  # Mismatched XLSX


def send_email():
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    import socket
    import time
    import os

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(TO_LIST)
    msg["Cc"] = ", ".join(CC_LIST)
    msg["Subject"] = email_subject

    # HTML body
    msg.attach(MIMEText(email_body, "html"))

    # ------------------------------------------------------------
    # ATTACH MAIN OUTPUT FILE
    # ------------------------------------------------------------
    if os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(attachment_path)}"'
        )
        msg.attach(part)
    else:
        print("⚠ Main attachment not found:", attachment_path)

    # ------------------------------------------------------------
    # ATTACH MISMATCH FILE
    # ------------------------------------------------------------
    if os.path.exists(attachment_path_2):
        with open(attachment_path_2, "rb") as f:
            part2 = MIMEBase("application", "octet-stream")
            part2.set_payload(f.read())
        encoders.encode_base64(part2)
        part2.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(attachment_path_2)}"'
        )
        msg.attach(part2)
    else:
        print("⚠ Mismatched attachment not found:", attachment_path_2)

    recipients = TO_LIST + CC_LIST

    # ------------------------------------------------------------
    # 📡 INFINITE RETRY SMTP SENDING BLOCK
    # ------------------------------------------------------------
    while True:
        try:
            print("📡 Connecting to SMTP server...")

            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as server:
                server.starttls()
                print("🔐 Logging in...")
                server.login(SMTP_USER, SMTP_PASS)

                print("📨 Sending email...")
                server.sendmail(SMTP_USER, recipients, msg.as_string())

            print("✅ Email sent successfully!")
            break  # exit loop when success

        except (smtplib.SMTPServerDisconnected,
                smtplib.SMTPConnectError,
                smtplib.SMTPDataError,
                smtplib.SMTPHeloError,
                smtplib.SMTPAuthenticationError,
                smtplib.SMTPException,
                TimeoutError,
                socket.timeout,
                socket.error) as e:

            print(f"⚠ SMTP error: {e}")
            print("⏳ Retrying in 5 seconds...\n")
            time.sleep(5)
            continue

# CALL EMAIL FUNCTION
send_email()



# ------------------------------------------------------------
# 📦 BACKUP PROGRAM — AUTO BACKUP & CLEANUP
# ------------------------------------------------------------
import shutil
from datetime import datetime

# 1️⃣ Date from NEW HOLDING file (already extracted earlier)
#     new_hold_date_str format = "02122025" (DDMMYYYY)
backup_day = new_hold_date_str[:2]      # DD
backup_month = new_hold_date_str[2:4]   # MM
backup_year = new_hold_date_str[4:8]    # YYYY

backup_folder_name = f"{int(backup_day)}-{int(backup_month)}-{backup_year}"

# 2️⃣ Base backup directory
backup_base = os.path.join(folder, "BACKUP FILES AUTOMATED")
os.makedirs(backup_base, exist_ok=True)

# 3️⃣ Full dated folder path
backup_path = os.path.join(backup_base, backup_folder_name)
os.makedirs(backup_path, exist_ok=True)

print("📁 Backup folder created:", backup_path)

# 4️⃣ Move files except .py, .bat, and folders
for item in os.listdir(folder):
    full_path = os.path.join(folder, item)

    # Skip subfolders
    if os.path.isdir(full_path):
        if item != "BACKUP FILES AUTOMATED":
            print(f"✔ Keeping folder: {item}")
        continue

    # Skip script files
    if item.lower().endswith(".py") or item.lower().endswith(".bat"):
        print(f"✔ Keeping script: {item}")
        continue

    # Never move the holidays list
    if item.lower() == "holidays_list.csv":
        print(f"✔ Keeping holidays list: {item}")
        continue

    # MOVE everything else
    try:
        shutil.move(full_path, os.path.join(backup_path, item))
        print(f"📦 Moved: {item}")
    except Exception as e:
        print(f"⚠ Error moving {item}: {e}")

print("\n✅ Backup completed successfully!")
print("🧹 Cleaned main folder & preserved .py/.bat files and subfolders.")

