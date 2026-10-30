"""
payout_check.py — Payout Checking (CORRECTED: dual-folder Update Limits)
=========================================================================
Document 9 (CTCL_PAYOUT_FINAL.py), wrapped in run_payout_check().
Reads today's base file from PAYOUT_FOLDER, finds "Update Limits" in the CTCL
folder FIRST then the PAYOUT folder, computes TOTAL DEPOSIT, keeps only negative
Available Margin, writes PAYOUT_CHECKING file, emails it, backs up + cleans.

ONLY changes from the original:
  • PAYOUT_DIR / OUTPUT_DIR → .env (PAYOUT_FOLDER)
  • CTCL_DIR                → .env (CTCL_NMASS_FOLDER)
  • SMTP credentials        → .env (SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD)
  • TO/CC                   → .env (PAYOUT_CHECK_TO, PAYOUT_CHECK_CC)
  • raise FileNotFoundError → return {"status": "missing_files", ...}
  • wrapped in def run_payout_check(); everything else identical.
"""
import os
import re
import time
import smtplib
from datetime import datetime
import polars as pl
import pandas as pd
from openpyxl import load_workbook
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

load_dotenv()

PAYOUT_DIR = os.getenv("PAYOUT_FOLDER", r"D:\PAYOUT ASHISH")
CTCL_DIR   = os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
BASE_DIR   = PAYOUT_DIR
OUTPUT_DIR = PAYOUT_DIR


def _emails(key):
    return [e.strip() for e in os.getenv(key, "").split(",") if e.strip()]


def run_payout_check(_preloaded_update=None):
    """Thin wrapper: adds the AI (Groq) plain-English fallback translation on an
    unrecognized failure, same as nmass_download.py etc. — see _run_payout_check_impl.

    _preloaded_update: optional (file_path, polars_df) tuple — see _run_payout_check_impl
    for exactly how/when it's used. Defaults to None, meaning "read from disk as before"."""
    try:
        return _run_payout_check_impl(_preloaded_update=_preloaded_update)
    except SystemExit:
        raise
    except Exception:
        import traceback
        from core.error_translator import translate_error
        tb_lines = traceback.format_exc().splitlines()
        for _l in tb_lines:
            print(_l, flush=True)
        _ai_msg = translate_error("Payout check", tb_lines)
        print(f"[FRIENDLY_MSG] {_ai_msg or 'Payout check failed for an unrecognized reason — check the Logs button or contact tech support.'}", flush=True)
        # Re-raise the ORIGINAL exception (not SystemExit) so any caller using
        # `except Exception` (rather than checking a subprocess exit code) still catches it.
        raise


def _run_payout_check_impl(_preloaded_update=None):
    """Payout checking: base + Update Limits (CTCL→PAYOUT) → negative margin → email → backup."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    TODAY_STR = datetime.now().strftime("%d%m%Y")
    TIME_STR = datetime.now().strftime("%H%M%S")

    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_EMAIL = os.getenv("SENDER_EMAIL")
    SMTP_PASSWORD = os.getenv("SENDER_PASSWORD")
    TO_EMAILS = _emails("PAYOUT_CHECK_TO") or ["rms_del@mastertrust.co.in"]
    CC_EMAILS = _emails("PAYOUT_CHECK_CC")

    # ----- base file (today's payment request log) in PAYOUT folder -----
    def find_file(patterns):
        for file in os.listdir(BASE_DIR):
            for pat in patterns:
                if re.match(pat, file, re.IGNORECASE):
                    return os.path.join(BASE_DIR, file)
        return None

    base_file = find_file([rf"{TODAY_STR}.*\.xlsx", rf"{TODAY_STR}.*\.csv"])
    if not base_file:
        print(f"❌ Base file ({TODAY_STR}*.xlsx/.csv) not found in {BASE_DIR}")
        return {"status": "missing_files", "missing": [f"{TODAY_STR}*.xlsx/.csv (base file)"]}

    # ----- Update Limits: CTCL folder first, then PAYOUT folder -----
    def find_update_limits():
        patterns = [r"update limits", r"update_limits"]
        # 1. CTCL folder first
        ctcl_candidates = []
        if os.path.isdir(CTCL_DIR):
            for file in os.listdir(CTCL_DIR):
                fl = file.lower()
                if any(fl.startswith(p) for p in patterns) and (fl.endswith(".xlsx") or fl.endswith(".csv")):
                    ctcl_candidates.append(os.path.join(CTCL_DIR, file))
        if ctcl_candidates:
            latest = max(ctcl_candidates, key=os.path.getmtime)
            print(f"✅ Using CTCL Update Limits: {os.path.basename(latest)}")
            return latest, "CTCL"
        # 2. PAYOUT folder
        payout_candidates = []
        for file in os.listdir(PAYOUT_DIR):
            fl = file.lower()
            if any(fl.startswith(p) for p in patterns) and (fl.endswith(".xlsx") or fl.endswith(".csv")):
                payout_candidates.append(os.path.join(PAYOUT_DIR, file))
        if payout_candidates:
            latest = max(payout_candidates, key=os.path.getmtime)
            print(f"✅ Using PAYOUT Update Limits: {os.path.basename(latest)}")
            return latest, "PAYOUT"
        return None, None

    update_file, update_source = find_update_limits()
    if not update_file:
        print("❌ Update Limits file not found in CTCL or PAYOUT folder")
        return {"status": "missing_files", "missing": ["Update Limits*.xlsx/.csv (CTCL or PAYOUT folder)"]}

    def read_any(file):
        if file.lower().endswith(".csv"):
            return pl.read_csv(file, infer_schema_length=10000, try_parse_dates=True, ignore_errors=True)
        else:
            return pl.read_excel(file)

    base_df = read_any(base_file)
    # Performance-only optimization, zero logic change: the CTCL vs NMASS run (when this
    # runs as part of that same run, not a standalone "Retry Payout only") already parsed
    # this exact Update Limits file moments ago — a 100MB+ .xlsx is slow to parse, so re-
    # reading it a second time from disk was pure duplicate work. Reuse that already-parsed,
    # untouched-since-read DataFrame ONLY if the file path matches exactly what this
    # function's own find_update_limits() just resolved (same dual-folder search as always,
    # completely unchanged) — otherwise (standalone retry, or a different file got picked)
    # this falls through to the original read_any(update_file) exactly as before.
    if _preloaded_update is not None and _preloaded_update[0] == update_file:
        update_df = _preloaded_update[1]
    else:
        update_df = read_any(update_file)

    def cast_numeric_to_float(df):
        return df.with_columns([pl.col(c).cast(pl.Float64, strict=False)
                                for c, d in zip(df.columns, df.dtypes)
                                if d in (pl.Int64, pl.Int32, pl.Float32)])
    base_df = cast_numeric_to_float(base_df)
    update_df = cast_numeric_to_float(update_df)

    deposit_cols = ["Payin", "Payout", "Collateral(Total)", "Cash", "DaylongCash",
                    "MtfDaylongCash", "CacDaylongCash", "UnclearedCash", "BrokerCollateral",
                    "EQT Collateral", "DER Collateral", "COM Collateral", "FX Collateral",
                    "AUXDaylongCash", "AuxUnclearedCash", "AuxBrokerCollateral", "Remarks Amount"]
    for col in deposit_cols:
        if col not in update_df.columns:
            update_df = update_df.with_columns(pl.lit(0.0).alias(col))

    update_neg = (update_df
                  .with_columns(pl.sum_horizontal([pl.col(c) for c in deposit_cols]).alias("TOTAL DEPOSIT"))
                  .select(["Entity", "Available Margin", "MarginUsed", "TOTAL DEPOSIT"])
                  .filter(pl.col("Available Margin") < 0))

    result = base_df.join(update_neg, left_on="Account ID", right_on="Entity", how="inner")
    final_columns = ["Zone", "Region", "Account ID", "Request Amount", "Release Amount",
                     "Request Date", "Bank Account No", "Bank Code", "Maker", "Approve By",
                     "Approve Date", "Checker Time", "Available Margin", "MarginUsed", "TOTAL DEPOSIT"]
    result = result.select(final_columns)

    output_file = os.path.join(OUTPUT_DIR, f"PAYOUT_CHECKING_{TODAY_STR}_{TIME_STR}.xlsx")
    pdf = result.to_pandas()
    pdf.to_excel(output_file, index=False)

    wb = load_workbook(output_file); ws = wb.active
    for col in ws.columns:
        max_length = 0
        col_letter = col[0].column_letter
        for cell in col:
            if cell.value is not None:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max_length + 2
    wb.save(output_file)

    def send_email_with_retry():
        subject = f"PAYOUT CHECKING FILE | {TODAY_STR}"
        body = f"""
    <html><body>
        <p>Dear Team,</p>
        <p>Please find attached the <b>Payout Checking file</b> generated from the
        <b>Bank Payment Request Log</b>.</p>
        <p>The file includes only those records where:
        <ul><li>Available Margin is negative</li></ul></p>
        <br>Best regards,<br><b>Kartavya Dhillon</b><br>Master Capital Services Ltd<br>📞 011-66331000 | Ext. 84
    </body></html>
    """
        while True:
            try:
                msg = MIMEMultipart()
                msg["From"] = SMTP_EMAIL
                msg["To"] = ", ".join(TO_EMAILS)
                msg["Cc"] = ", ".join(CC_EMAILS)
                msg["Subject"] = subject
                msg.attach(MIMEText(body, "html"))
                with open(output_file, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(output_file)}"')
                msg.attach(part)
                with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                    server.starttls(); server.login(SMTP_EMAIL, SMTP_PASSWORD)
                    server.sendmail(SMTP_EMAIL, TO_EMAILS + CC_EMAILS, msg.as_string())
                print("✅ Email sent successfully")
                break
            except Exception as e:
                print(f"❌ Email failed: {e}"); print("🔁 Retrying in 5 seconds...")
                time.sleep(5)

    send_email_with_retry()
    print(f"✅ Process completed successfully: {output_file}")

    def backup_and_cleanup(base_file, update_file, output_file, update_source):
        import shutil
        backup_root = os.path.join(BASE_DIR, "BACKUPFILES AUTOMATED")
        os.makedirs(backup_root, exist_ok=True)
        base_name = os.path.basename(base_file)
        date_match = re.search(r"(\d{2})(\d{2})(\d{4})", base_name)
        if not date_match:
            raise ValueError("Date not found in base file name")
        day, month, year = date_match.groups()
        date_folder_name = datetime.strptime(f"{day}{month}{year}", "%d%m%Y").strftime("%d %b %Y").upper()
        date_folder = os.path.join(backup_root, date_folder_name)
        os.makedirs(date_folder, exist_ok=True)
        existing_runs = [d for d in os.listdir(date_folder)
                         if os.path.isdir(os.path.join(date_folder, d)) and d.isdigit()]
        next_run = f"{len(existing_runs) + 1:02d}"
        run_folder = os.path.join(date_folder, next_run)
        os.makedirs(run_folder)

        files_to_backup = [base_file, output_file]
        if update_source == "PAYOUT":
            files_to_backup.append(update_file)
        for file in files_to_backup:
            if os.path.exists(file):
                shutil.copy2(file, run_folder)
        print(f"📦 Backup completed: {run_folder}")

        files_to_delete = [base_file, output_file]
        if update_source == "PAYOUT":
            files_to_delete.append(update_file)
        for file in files_to_delete:
            try:
                if os.path.exists(file):
                    os.remove(file); print(f"🧹 Deleted: {os.path.basename(file)}")
            except Exception as e:
                print(f"⚠️ Could not delete {file}: {e}")

    backup_and_cleanup(base_file, update_file, output_file, update_source)
    return {"status": "ok", "output": output_file}


if __name__ == "__main__":
    print(run_payout_check())
