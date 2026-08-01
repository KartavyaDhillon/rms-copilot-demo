"""
payout_inspection.py — Payout Request inspection tool (wrapped as functions)
=============================================================================
Document 15, core logic preserved. The tkinter UI and messagebox popups are
removed — parameters come from the API/dashboard instead.

Two entry points:
  • run_payout_inspection_daily()                    → daily ingestion mode
  • run_payout_inspection_client(client_ids, fy_str) → client inspection mode

.env keys:
  INSPECTION_FOLDER=D:\\INSPECTION
"""
import os
import re
import shutil
from datetime import datetime, timedelta
from dotenv import load_dotenv
import polars as pl

load_dotenv()

BASE_DIR   = os.getenv("INSPECTION_FOLDER", r"D:\INSPECTION")
BACKUP_DIR = os.path.join(BASE_DIR, "BACKUP FILES AUTOMATED")

CLIENT_COL       = "ClientId"
INSTRUCTION_COL  = "InstructionNo"
DATETIME_COL     = "InstructionDateTime"
DEDUP_KEYS       = [CLIENT_COL, INSTRUCTION_COL]
MAX_LOOKBACK_DAYS = 30
MAX_ROWS_PER_FILE = 1000000


def debug(msg): print(f"🐞 {msg}")
def log(msg):   print(msg)


def normalize_columns(df):
    col_map = {}
    for c in df.columns:
        key = c.lower().replace(" ", "").replace("_", "")
        if key == "clientid":              col_map[c] = "ClientId"
        elif key == "clientname":          col_map[c] = "ClientName"
        elif key == "instructionno":       col_map[c] = "InstructionNo"
        elif key == "instructiondatetime": col_map[c] = "InstructionDateTime"
        elif key == "sourceuserid":        col_map[c] = "SourceUserId"
        else:                              col_map[c] = c.replace(" ", "")
    return df.rename(col_map)


def read_csv_safe(path):
    return pl.read_csv(path, encoding="latin1", infer_schema_length=0, schema_overrides={})


def historical_files():
    return [os.path.join(BASE_DIR, f) for f in os.listdir(BASE_DIR)
            if f.startswith("PAYOUT REQUEST FROM") and f.endswith(".csv")]


def get_previous_trading_file():
    today = datetime.today()
    for i in range(1, MAX_LOOKBACK_DAYS + 1):
        d = today - timedelta(days=i)
        fname = d.strftime("%d %b %Y").upper() + ".csv"
        path = os.path.join(BASE_DIR, fname)
        if os.path.exists(path):
            log(f"📅 Daily file picked: {fname}")
            return path, d
    return None, None


def parse_range_from_filename(filename):
    pattern = r"FROM (\d{2}[A-Z]+?\d{4}) TO (\d{2}[A-Z]+?\d{4})"
    m = re.search(pattern, filename.upper())
    if not m:
        return None, None
    def parse_safe(s):
        for fmt in ("%d%B%Y", "%d%b%Y"):
            try: return datetime.strptime(s, fmt).date()
            except ValueError: pass
        return None
    return parse_safe(m.group(1)), parse_safe(m.group(2))


def backup_and_delete(file_path, d):
    month_folder = d.strftime("%b-%Y").upper()
    dest = os.path.join(BACKUP_DIR, month_folder)
    os.makedirs(dest, exist_ok=True)
    shutil.copy(file_path, dest)
    os.remove(file_path)
    log(f"💾 Backup created: {dest}\\{os.path.basename(file_path)}")


def parse_date(df):
    df = df.with_columns(pl.col(DATETIME_COL).alias("DT_STR"))
    df = df.with_columns(
        pl.coalesce([
            pl.col("DT_STR").str.strptime(pl.Datetime, "%d/%m/%Y %H:%M", strict=False),
            pl.col("DT_STR").str.strptime(pl.Datetime, "%d/%m/%Y %H:%M:%S", strict=False),
            pl.col("DT_STR").str.strptime(pl.Datetime, "%d-%m-%y %H:%M", strict=False),
            pl.col("DT_STR").str.strptime(pl.Datetime, "%d-%m-%Y %H:%M", strict=False),
            pl.col("DT_STR").str.strptime(pl.Datetime, "%d-%m-%Y %H.%M", strict=False),
        ]).alias("PARSED_DT")
    )
    df = df.with_columns(pl.col("PARSED_DT").dt.date().alias("ONLY_DATE"))
    debug(f"Date parse: {df.filter(pl.col('ONLY_DATE').is_not_null()).height}/{df.height}")
    return df.drop(["DT_STR", "PARSED_DT"])


def get_financial_years(start_year=2021):
    today = datetime.today()
    fy = today.year if today.month >= 4 else today.year - 1
    return [f"{y}-{y+1}" for y in range(fy, start_year - 1, -1)]


def append_to_history(df):
    if "ONLY_DATE" not in df.columns:
        raise ValueError("ONLY_DATE column missing. parse_date() must be called before append.")
    df = df.filter(pl.col("ONLY_DATE").is_not_null())
    if df.height == 0:
        print("⚠️ No valid dates found in daily file. Nothing was ingested.")
        return None, 0

    existing_key_frames = []
    for f in historical_files():
        hist = pl.read_csv(f, encoding="latin1", infer_schema_length=0, schema_overrides={})
        hist = normalize_columns(hist)
        missing = [c for c in DEDUP_KEYS if c not in hist.columns]
        if missing: continue
        existing_key_frames.append(
            hist.select(DEDUP_KEYS).with_columns([pl.col(c).cast(pl.Utf8) for c in DEDUP_KEYS]))

    global_keys = pl.concat(existing_key_frames).unique() if existing_key_frames else None

    df_keys = df.select(DEDUP_KEYS).with_columns([pl.col(c).cast(pl.Utf8) for c in DEDUP_KEYS])
    if global_keys is not None:
        mask = df_keys.join(global_keys, on=DEDUP_KEYS, how="anti")
        new_rows = df.join(mask, on=DEDUP_KEYS, how="inner")
    else:
        new_rows = df

    if new_rows.height == 0:
        print("ℹ️ This file's data already exists in history. Nothing new was appended.")
        return None, 0

    candidates = []
    for f in historical_files():
        start, end = parse_range_from_filename(os.path.basename(f))
        if start and end:
            candidates.append((f, start, end))
    if not candidates:
        raise ValueError("No historical PAYOUT REQUEST files found.")
    candidates.sort(key=lambda x: x[2])
    target_file, _, _ = candidates[-1]

    existing = pl.read_csv(target_file, encoding="latin1", infer_schema_length=0, schema_overrides={})
    existing = normalize_columns(existing)
    existing = parse_date(existing)

    for col in existing.columns:
        if col not in new_rows.columns:
            new_rows = new_rows.with_columns(pl.lit(None).alias(col))
    new_rows = new_rows.select(existing.columns)

    if existing.height + new_rows.height > MAX_ROWS_PER_FILE:
        log("📦 Current file full. Creating new history file.")
        start_date = new_rows.select(pl.col("ONLY_DATE")).min().item()
        end_date   = new_rows.select(pl.col("ONLY_DATE")).max().item()
        new_filename = (f"PAYOUT REQUEST FROM {start_date.strftime('%d%B%Y').upper()} "
                        f"TO {end_date.strftime('%d%B%Y').upper()}.csv")
        new_rows.drop("ONLY_DATE").write_csv(os.path.join(BASE_DIR, new_filename))
        log(f"🆕 New history file created: {new_filename}")
        return new_filename, new_rows.height

    combined = pl.concat([existing, new_rows], how="vertical_relaxed")
    all_dates = combined.select(pl.col("ONLY_DATE")).drop_nulls()
    start_date = all_dates.min().item()
    end_date   = all_dates.max().item()
    new_filename = (f"PAYOUT REQUEST FROM {start_date.strftime('%d%B%Y').upper()} "
                    f"TO {end_date.strftime('%d%B%Y').upper()}.csv")
    new_path = os.path.join(BASE_DIR, new_filename)
    combined.drop("ONLY_DATE").write_csv(new_path)
    if os.path.abspath(target_file) != os.path.abspath(new_path):
        os.remove(target_file)

    print(f"✅ Data appended successfully\n📊 New rows added: {new_rows.height}\n📄 File: {new_filename}")
    log(f"➕ {new_rows.height} new rows appended")
    return new_filename, new_rows.height


def run_payout_inspection_daily():
    """Daily ingestion: find yesterday's file, deduplicate, append to history, backup."""
    print("📅 Running daily ingestion...")
    daily_file, trade_date = get_previous_trading_file()
    if not daily_file:
        print("❌ No daily file found. Place the daily CSV in the INSPECTION folder and retry.")
        return {"status": "missing_files"}
    df = normalize_columns(read_csv_safe(daily_file))
    df = parse_date(df)
    target, rows = append_to_history(df)
    backup_and_delete(daily_file, trade_date)
    print(f"✅ Daily ingestion complete. Rows appended: {rows}")
    return {"status": "ok", "rows_appended": rows, "file": target}


def run_payout_inspection_client(client_ids_str: str, fy_str: str):
    """Client inspection: filter history by client IDs and financial year.
    client_ids_str: comma-separated e.g. 'A001,B002'
    fy_str: e.g. '2025-2026'
    """
    print(f"🔍 Running inspection for clients: {client_ids_str}, FY: {fy_str}")
    client_ids = [c.strip().upper() for c in client_ids_str.split(",") if c.strip()]
    try:
        y1, y2 = map(int, fy_str.split("-"))
        fy_start = datetime(y1, 4, 1).date()
        fy_end   = datetime(y2, 3, 31).date()
    except Exception:
        print(f"❌ Invalid FY format: {fy_str}. Use YYYY-YYYY e.g. 2025-2026")
        return {"status": "error", "message": f"Invalid FY: {fy_str}"}

    frames = []
    for f in historical_files():
        df = normalize_columns(read_csv_safe(f))
        df = parse_date(df)
        frames.append(df)
    if not frames:
        print("❌ No historical PAYOUT REQUEST files found.")
        return {"status": "missing_files"}

    df = pl.concat(frames)
    counts = df.group_by(CLIENT_COL).count()
    sel = pl.DataFrame({CLIENT_COL: client_ids})
    summary = sel.join(counts, on=CLIENT_COL, how="left").with_columns(pl.col("count").fill_null(0))

    clients_ok = summary.filter(pl.col("count") > 0)[CLIENT_COL].to_list()
    clients_no = summary.filter(pl.col("count") == 0)[CLIENT_COL].to_list()

    df = df.filter(pl.col(CLIENT_COL).is_in(clients_ok))
    df = df.filter((pl.col("ONLY_DATE") >= fy_start) & (pl.col("ONLY_DATE") <= fy_end))

    out = f"INSPECTION_OUTPUT_FY_{fy_start.year}_{fy_end.year}.csv"
    df.drop("ONLY_DATE").write_csv(os.path.join(BASE_DIR, out))

    print(f"✅ Inspection file created: {out}")
    print(f"📊 Records: {df.height}")
    if clients_no:
        print(f"❌ No data for: {', '.join(clients_no)}")
    return {"status": "ok", "records": df.height, "file": out, "no_data_clients": clients_no}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "daily":
        print(run_payout_inspection_daily())
    elif len(sys.argv) >= 3:
        print(run_payout_inspection_client(sys.argv[1], sys.argv[2]))
    else:
        print("Usage: python payout_inspection.py daily")
        print("       python payout_inspection.py 'A001,B002' '2025-2026'")
