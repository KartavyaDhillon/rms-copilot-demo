"""
core/pipelines/probable_reconcile.py — headless port of the reference
codes/API HITTING/code1.py (Probable Defaulter reconciliation, STEP 1 of the
pipeline). Reference file is READ ONLY (D:\\xxxx\\probable for claude /
D:\\PROBABLE FINAL NEW) — nothing there is ever modified; this is the ported,
env-driven, dashboard-runnable version.

What it does (unchanged from the reference logic):
  1. Downloads the NSE Delivery Margin file (F_DLMG02_06436_<prev trading
     day>.csv.gz), skipping automatically on the 5th consecutive trading day
     of the exchange's cycle (same skip logic as the reference script).
  2. Verifies all required same-day input files are present (Probable_Fullfile,
     NCDEX/CM/CO/FO ClientLevelCollaterals, MCX 90% file, Clientwise_mcx_margin)
     — aborts with a structured "Missing required files:" block if not
     (recognized by core/pipeline_box.py's extract_known_failure_reason(), same
     as every other pipeline here).
  3. Merges all of those into a MATCHED/MISMATCH reconciliation
     (All_Exchange_Margin_Comparison_<date>.xlsx) and an updated
     Probable_Fullfile_K_<date>.xlsx (feeds probable_alerts.py / code2.py).
  4. Emails both reports.
  5. Backs up + cleans the base folder (BACKUPFILES AUTOMATED/<date>/RECONCILIATION).

Differences from the reference script (deliberate, per instruction):
  - BASE_FOLDER is env-driven (PROBABLE_FOLDER), not a hardcoded C:\\KARTAVYA\\PROBABLE.
  - NSE credentials reuse the EXISTING SA_NSE_MEMBER/SA_NSE_LOGINID/SA_NSE_PASSWORD
    env vars — confirmed identical account (memberCode 06436, loginId M0213815) to
    what was hardcoded in the reference script; no new credential pair introduced.
  - SMTP reuses the existing SENDER_EMAIL/SENDER_PASSWORD (same kartavya account
    the reference script hardcoded). TO/CC come from PROBABLE_RECON_TO/_CC (.env) —
    currently redirected to kartavya for testing, same convention as MS15/CTCL.
  - The reference script's tkinter messagebox.askyesno() popup for a negative
    DELIVERY MARGIN cannot show on a headless server with nobody watching it —
    blocking the whole daily run on an unattended Yes/No dialog would silently
    wedge the pipeline. Instead this prints a loud, structured warning naming
    every affected client and CONTINUES (equivalent to the reference script's
    "Yes" branch) — the run must complete and the report must reach the team;
    the reconciliation report itself already flags every MISMATCH for review.
  - No infinite retry on email send (reference script's `while True` around
    smtplib) — retries a bounded number of times, then reports failure via the
    normal [FRIENDLS_MSG]/exit-code path other pipelines use, so a genuinely
    down SMTP server can't hang this run forever.
"""
import os
import re
import glob
import time
import gzip
import shutil
import smtplib
import warnings
import datetime
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders

import requests
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")


def _base_folder():
    d = os.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    os.makedirs(d, exist_ok=True)
    return d


# =====================================================
# STEP 0 — NSE Delivery Margin (F_DLMG02) download, with the exact same
# 5-consecutive-trading-day cycle skip as the reference script.
# =====================================================
def _download_delivery_margin(base_folder, today_str_prev_trading_day):
    login_url    = "https://www.connect2nse.com/extranet-api/login/2.0"
    download_url = "https://www.connect2nse.com/extranet-api/member/file/download/2.0"
    credentials = {
        "memberCode": os.getenv("SA_NSE_MEMBER",  "06436"),
        "loginId":    os.getenv("SA_NSE_LOGINID", "M0213815"),
        "password":   os.getenv("SA_NSE_PASSWORD", ""),
    }
    segment = "FO"
    filename = f"F_DLMG02_06436_{today_str_prev_trading_day}.csv.gz"
    file_gz_path = os.path.join(base_folder, filename)
    file_csv_path = file_gz_path.replace(".csv.gz", ".csv")

    def get_token(max_tries=6):
        for attempt in range(max_tries):
            try:
                resp = requests.post(login_url, json=credentials, timeout=15)
                resp.raise_for_status()
                token = resp.json().get("token")
                if not token:
                    raise ValueError("Token not returned from NSE.")
                print("✅ NSE login successful.")
                return token
            except Exception as e:
                print(f"⚠️ NSE login failed: {e} — retrying in 5s ({attempt+1}/{max_tries})")
                time.sleep(5)
        raise RuntimeError("NSE login failed after repeated attempts")

    if not credentials["password"]:
        print("⚠️ SA_NSE_PASSWORD not set — skipping Delivery Margin download (DELIVERY MARGIN will be 0)")
        return
    if os.path.exists(file_csv_path):
        print(f"⏭ {os.path.basename(file_csv_path)} already present — skipping download")
        return

    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    params = {"segment": segment, "folderPath": "Reports", "filename": filename}
    try:
        response = requests.get(download_url, headers=headers, params=params, timeout=30)
        if response.status_code == 401:
            token = get_token()
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(download_url, headers=headers, params=params, timeout=60)
        if response.status_code == 404:
            print("❌ Delivery Margin file not given by exchange for this date — skipping.")
            return
        response.raise_for_status()

        # ── consecutive-day cycle check (unchanged logic) ──────────────────
        current_file_date = datetime.datetime.strptime(today_str_prev_trading_day, "%d%m%Y")
        month_start = current_file_date.replace(day=1)
        consecutive_count = 0
        check_date = current_file_date
        while check_date >= month_start:
            if check_date.weekday() >= 5:
                check_date -= datetime.timedelta(days=1)
                continue
            file_date = check_date.strftime("%d%m%Y")
            test_params = {"segment": segment, "folderPath": "Reports",
                            "filename": f"F_DLMG02_06436_{file_date}.csv.gz"}
            test_response = None
            for _ in range(20):
                try:
                    test_response = requests.get(download_url, headers=headers, params=test_params, timeout=15)
                    if test_response.status_code == 401:
                        token = get_token(); headers = {"Authorization": f"Bearer {token}"}
                        continue
                    break
                except requests.exceptions.RequestException as e:
                    print(f"🌐 Network error checking {file_date}: {e} — retrying")
                    time.sleep(3)
            if test_response is None:
                break
            if test_response.status_code == 404:
                break
            if test_response.status_code == 200:
                consecutive_count += 1
                check_date -= datetime.timedelta(days=1)
                continue
            break
        print(f"📌 Consecutive PRESENT trading days this month: {consecutive_count}")

        if consecutive_count % 5 == 0:
            print("⛔ 5th consecutive trading day — skipping Delivery Margin download this cycle.")
            return

        with open(file_gz_path, "wb") as f:
            f.write(response.content)
        with gzip.open(file_gz_path, "rb") as f_in, open(file_csv_path, "wb") as f_out:
            f_out.write(f_in.read())
        os.remove(file_gz_path)
        print(f"✅ Delivery Margin file downloaded + extracted: {file_csv_path}")
    except Exception as e:
        print(f"⚠️ Delivery Margin download failed (non-fatal — DELIVERY MARGIN will default to 0): {e}")


# =====================================================
# Pre-run file check — SAME structured "Missing required files:" format
# core/pipeline_box.py's extract_known_failure_reason() already parses for
# every other pipeline (ctcl_vs_nmass.py / margin_shortage_15min.py).
# =====================================================
def _check_required_files(base_folder, today_ddmmyyyy, today_yyyymmdd):
    required_patterns = {
        "Probable File": f"Probable_Fullfile_{today_ddmmyyyy}*.xlsx",
        "NCDEX File":     f"ClientNetEffectiveDeposits_{today_ddmmyyyy}*",
        "CM File":        f"CM_ClientLevelCollaterals_{today_ddmmyyyy}*",
        "CO File":        f"CO_ClientLevelCollaterals_{today_ddmmyyyy}*",
        "FO File":        f"FO_ClientLevelCollaterals_{today_ddmmyyyy}*",
        "MCX File":       f"*{today_yyyymmdd}*.xlsx",
        "90% File":       "Clientwise_mcx_margin*.xlsx",
    }
    missing = []
    for name, pattern in required_patterns.items():
        if not glob.glob(os.path.join(base_folder, pattern)):
            missing.append(f"{name} (pattern: {pattern})")
    if missing:
        print("\nMissing required files:")
        for m in missing:
            print(f"  - {m}")
        print("Aborting execution.")
        raise SystemExit(1)
    print("✅ All required files are present.\n")

    # ── FRESHNESS GATE ──────────────────────────────────────────────────
    # Existence alone doesn't mean the data is CURRENT — a Run (or Safe Test)
    # fired against yesterday's leftover downloads would sail straight
    # through the check above. Flagged explicitly by the operator: "i ran
    # the safe test with morning files and it ran so time check is not
    # there." NMASS/NCDEX inputs (CM/CO/FO/ClientNetEffectiveDeposits) must
    # be <=30 min old; TCS BANCS inputs (the MCX deposit file + the renamed
    # Clientwise_mcx_margin.xlsx 90% file) must be <=1 hour old — the exact
    # same windows already enforced on the download buttons themselves (see
    # nmass_download.py's _PIPE_MAX_AGE_MIN and probable_tcs_bancs.py's
    # _TCS_MAX_AGE_MIN, kept in sync with these two numbers on purpose).
    from core.pipelines.nmass_download import check_nmass_files_fresh
    from core.pipelines.probable_tcs_bancs import (
        _fresh_rsk383_file, _fresh_rsk335_file, _TCS_MAX_AGE_MIN,
    )
    stale = []
    _nm = check_nmass_files_fresh(base_folder, max_age_min=30.0)
    if not _nm["fresh"]:
        for d in _nm["detail"]:
            if "too old" in d or "not present" in d or "unreadable" in d:
                stale.append(f"NMASS/NCDEX — {d} (must be ≤30 min old)")
    if not _fresh_rsk383_file(base_folder, _TCS_MAX_AGE_MIN):
        stale.append(f"MCX File (TCS BANCS RSK383 deposit file) — older than "
                      f"{_TCS_MAX_AGE_MIN:.0f} min, or its filename stamp is unreadable")
    if not _fresh_rsk335_file(base_folder, _TCS_MAX_AGE_MIN):
        stale.append(f"Clientwise_mcx_margin.xlsx (TCS BANCS RSK335 / 90% file) — older "
                      f"than {_TCS_MAX_AGE_MIN:.0f} min")
    if stale:
        print("\nStale files (older than allowed time):")
        for s in stale:
            print(f"  - {s}")
        print("Re-download NMASS/NCDEX and/or TCS BANCS from the PROBABLE box, then run again.")
        print("Aborting execution.")
        raise SystemExit(1)
    print(f"✅ All required files are fresh enough (NMASS/NCDEX ≤30 min, "
          f"TCS BANCS ≤{_TCS_MAX_AGE_MIN:.0f} min).\n")


def _round_numeric(df, decimals=2):
    cols = df.select_dtypes(include=["float64", "int64"]).columns
    df[cols] = df[cols].round(decimals)
    return df


def _auto_adjust_column_width(filepath):
    from openpyxl import load_workbook
    wb = load_workbook(filepath)
    ws = wb.active
    for column in ws.columns:
        max_len = 0
        letter = column[0].column_letter
        for cell in column:
            try:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            except Exception:
                pass
        ws.column_dimensions[letter].width = max_len + 2
    wb.save(filepath)


def _read_file_dynamic(base_folder, pattern):
    files = glob.glob(os.path.join(base_folder, pattern))
    if not files:
        raise FileNotFoundError(f"No file found for pattern: {pattern}")
    file = files[0]
    print(f"Reading: {file}")
    if file.endswith(".xlsx"):
        return pd.read_excel(file)
    elif file.endswith(".csv") or file.endswith(".csv.crdownload"):
        return pd.read_csv(file, low_memory=False)
    raise Exception("Unsupported file type")


def _read_mcx_file(base_folder, today_yyyymmdd):
    files = glob.glob(os.path.join(base_folder, f"*{today_yyyymmdd}*.xlsx"))
    if not files:
        raise FileNotFoundError(f"No MCX file found containing date: {today_yyyymmdd}")
    file = files[0]
    print(f"Reading MCX File: {file}")
    temp_df = pd.read_excel(file, header=None)
    header_row = None
    for i in range(min(10, len(temp_df))):
        # Collapse any whitespace run (including an embedded newline from a
        # header cell that wraps across two lines in Excel) to one space —
        # plain .str.strip() only trims the ends, so an internal line-break
        # would otherwise make this exact-match search fail silently.
        row_values = [re.sub(r"\s+", " ", str(v)).strip() for v in temp_df.iloc[i].tolist()]
        if "UCC Code" in row_values:
            header_row = i; break
    if header_row is None:
        raise Exception("MCX header row not found")
    df = pd.read_excel(file, header=header_row)
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    df = df[["UCC Code", "Margin Utilization"]].copy()
    df.columns = ["Account Id", "MCX MARGIN"]
    df["MCX MARGIN"] = df["MCX MARGIN"].astype(str).str.replace(",", "", regex=False)
    df["MCX MARGIN"] = pd.to_numeric(df["MCX MARGIN"], errors="coerce").fillna(0)
    return df


def _read_90_file(base_folder):
    files = glob.glob(os.path.join(base_folder, "Clientwise_mcx_margin*.xlsx"))
    if not files:
        raise FileNotFoundError("Clientwise_mcx_margin file not found")
    file = files[0]
    print(f"Reading 90% File: {file}")
    temp_df = pd.read_excel(file, header=None)
    header_row = None
    for i in range(min(10, len(temp_df))):
        # Same whitespace-normalization as _read_mcx_file above — a header
        # cell that visually wraps across two lines in Excel often has an
        # embedded newline, which plain .str.strip() doesn't remove.
        row_values = [re.sub(r"\s+", " ", str(v)).strip() for v in temp_df.iloc[i].tolist()]
        if "Client Code" in row_values:
            header_row = i; break
    if header_row is None:
        raise Exception("90% file header row not found")
    df = pd.read_excel(file, header=header_row)
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    df = df[["Client Code", "ELM", "Span Margin", "Other Margin", "Spread Margin"]].copy()
    for col in ["ELM", "Span Margin", "Other Margin", "Spread Margin"]:
        df[col] = df[col].astype(str).str.replace(",", "", regex=False)
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["90% File Sum"] = df["ELM"] + df["Span Margin"]
    df["MCX OTHER(90% FILE)"] = df["Other Margin"] - df["Spread Margin"]
    df = df[["Client Code", "90% File Sum", "MCX OTHER(90% FILE)"]]
    df.columns = ["Account Id", "90% File Sum", "MCX OTHER(90% FILE)"]
    df["Account Id"] = df["Account Id"].astype(str).str.strip()
    df = df[~df["Account Id"].isin(["", "nan", "None"])]
    df = df[df["Account Id"].notna()]
    return df


def _read_delivery_file(base_folder):
    files = glob.glob(os.path.join(base_folder, "F_DLMG02_06436*.csv"))
    if not files:
        print("⚠ Delivery file not found — DELIVERY MARGIN will default to 0.")
        return None
    file = files[0]
    print(f"Reading Delivery File: {file}")
    df = pd.read_csv(file, header=None)
    df = df[df[0] == 30]
    df = df[[1, 4]].copy()
    df.columns = ["Account Id", "DELIVERY MARGIN"]
    df["Account Id"] = df["Account Id"].astype(str).str.strip()
    df["DELIVERY MARGIN"] = pd.to_numeric(df["DELIVERY MARGIN"], errors="coerce").fillna(0)
    df = df[df["Account Id"].notna()]
    df = df[~df["Account Id"].isin(["", "nan", "None"])]
    return df


def _send_email_with_retry(base_folder, today_ddmmyyyy, today_str_prev, attachments, max_tries=5,
                            safe_test=False):
    smtp_server = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    smtp_port   = int(os.getenv("SMTP_PORT", "587"))
    smtp_email  = os.getenv("SENDER_EMAIL", "")
    smtp_pass   = os.getenv("SENDER_PASSWORD", "")
    if safe_test:
        # Safe test: one destination, no CC. See the SAFE TEST block in
        # core/pipelines/probable_alerts.py — same switch, same reasoning.
        from core.pipelines.probable_alerts import safe_test_targets
        to_emails, cc_emails = list(safe_test_targets()[0]), []
    else:
        to_emails = [e.strip() for e in os.getenv("PROBABLE_RECON_TO", "").split(",") if e.strip()]
        cc_emails = [e.strip() for e in os.getenv("PROBABLE_RECON_CC", "").split(",") if e.strip()]
    if not to_emails:
        print("⚠️ PROBABLE_RECON_TO not set — skipping email send.")
        return

    subject = f"Probable Reconciliation Report | {today_ddmmyyyy}"
    if safe_test:
        subject = f"[TEST — IGNORE] {subject}"
    body = f"""
    <html><body style="font-family: Arial, sans-serif; font-size:14px; color:#000;">
        {'<p style="color:#b45309;font-weight:bold">*** THIS IS A TEST RUN — PLEASE IGNORE ***</p>' if safe_test else ''}
        <p>Dear Team,</p>
        <p>Please find attached the margin reconciliation reports for <b>{today_ddmmyyyy}</b>.</p>
        <p>The reports include:</p>
        <ul>
            <li>All Exchange Margin Comparison</li>
            <li>Probable Fullfile (Updated)</li>
        </ul>
        <p>All margins have been processed and reconciled as per the defined validation logic.</p>
        <p>Delivery Margin file <b>F_DLMG02_06436_{today_str_prev}.csv</b> is downloaded for the
        first four consecutive trading days and automatically skipped on the fifth trading day.</p>
        <p>Kindly review and inform in case of any discrepancies.</p>
        <br><br>
        Best regards,<br><b>RMS Copilot — Probable Defaulter</b>
    </body></html>
    """
    all_recipients = to_emails + cc_emails
    for attempt in range(1, max_tries + 1):
        try:
            msg = MIMEMultipart()
            msg["From"] = smtp_email
            msg["To"] = ", ".join(to_emails)
            msg["Cc"] = ", ".join(cc_emails)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "html"))
            for file_path in attachments:
                with open(file_path, "rb") as attachment:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(attachment.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition",
                                     f'attachment; filename="{os.path.basename(file_path)}"')
                    msg.attach(part)
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(smtp_email, smtp_pass)
            server.sendmail(smtp_email, all_recipients, msg.as_string())
            server.quit()
            print(f"✅ email sent successfully: {subject} ({len(all_recipients)} recipients)")
            return
        except Exception as e:
            print(f"❌ Email sending failed (attempt {attempt}/{max_tries}): {e}")
            if attempt < max_tries:
                time.sleep(5)
    print("[FRIENDLY_MSG] Reconciliation reports were created, but the email could not be sent "
          "after several attempts — check SMTP settings in Credentials and re-send manually.")


def _backup_and_cleanup(base_folder, today_ddmmyyyy):
    print("\n🔄 Starting Backup & Cleanup Process...")
    probable_date = datetime.datetime.strptime(today_ddmmyyyy, "%d%m%Y")
    formatted_date = probable_date.strftime("%d %b %Y").upper()
    backup_date_folder = os.path.join(base_folder, "BACKUPFILES AUTOMATED", formatted_date, "RECONCILIATION")
    os.makedirs(backup_date_folder, exist_ok=True)
    print(f"📁 Backup Folder Created: {backup_date_folder}")

    probable_file = os.path.join(base_folder, f"Probable_Fullfile_K_{today_ddmmyyyy}.xlsx")
    if os.path.exists(probable_file):
        shutil.copy2(probable_file, os.path.join(backup_date_folder, os.path.basename(probable_file)))
        print("✅ Probable_Fullfile_K copied to backup")

    protected = {
        f"Probable_Fullfile_K_{today_ddmmyyyy}.xlsx",   # needed by probable_alerts.py next
        # Safe-test arm marker — see probable_alerts._step_13_backup for why
        # this is guarded even though a real run has normally consumed it.
        ".probable_safe_test",
    }
    moved = []
    for item in os.listdir(base_folder):
        full_path = os.path.join(base_folder, item)
        if os.path.isdir(full_path):
            continue
        if item.lower().endswith((".py", ".bat")):
            continue
        if item in protected:
            continue
        try:
            shutil.move(full_path, os.path.join(backup_date_folder, item))
            moved.append(item)
        except Exception as e:
            print(f"⚠ Could not move {item}: {e}")
    print(f"\n✅ Backup Completed. Total Files Moved: {len(moved)}")
    for f in moved:
        print(f"- {f}")


def run_probable_reconcile(safe_test=False, on_checks_passed=None):
    """safe_test: redirect the reconciliation email to the tester and skip the
    backup step (so the day's files stay in place for the real run that
    follows). See the SAFE TEST block in core/pipelines/probable_alerts.py.

    on_checks_passed: optional callback fired the moment the pre-run file check
    succeeds. probable_full.py uses this to consume (delete) the safe-test arm
    at exactly the right point — late enough that a run aborted for missing
    files leaves the arm intact for the retry, early enough that nothing has
    been sent yet."""
    base_folder = _base_folder()
    today = datetime.datetime.now()
    today_ddmmyyyy = today.strftime("%d%m%Y")
    today_yyyymmdd = today.strftime("%Y%m%d")

    if today.weekday() == 0:
        prev_trading_day = today - datetime.timedelta(days=3)
    elif today.weekday() == 6:
        prev_trading_day = today - datetime.timedelta(days=2)
    elif today.weekday() == 5:
        prev_trading_day = today - datetime.timedelta(days=1)
    else:
        prev_trading_day = today - datetime.timedelta(days=1)
    today_str_prev = prev_trading_day.strftime("%d%m%Y")

    print(f"🔎 Checking Delivery Margin file for: {today_str_prev}")
    _download_delivery_margin(base_folder, today_str_prev)

    _check_required_files(base_folder, today_ddmmyyyy, today_yyyymmdd)

    # Every required file is present, so this run WILL proceed to actually send
    # things. This is the point at which a safe-test arm must be consumed.
    if on_checks_passed:
        try:
            on_checks_passed()
        except Exception as e:
            print(f"⚠️ post-check hook failed (continuing): {e}")

    main_pattern = f"Probable_Fullfile_{today_ddmmyyyy}*.xlsx"
    main_df = _read_file_dynamic(base_folder, main_pattern)
    main_df = main_df[["Account ID", "Initial Exp. Margin"]].copy()
    main_df.columns = ["Account Id", "PROBABLE MARGIN"]
    main_df["PROBABLE MARGIN"] = pd.to_numeric(main_df["PROBABLE MARGIN"], errors="coerce").fillna(0)

    ncdex_df = _read_file_dynamic(base_folder, f"ClientNetEffectiveDeposits_{today_ddmmyyyy}*")
    ncdex_df = ncdex_df[["Client/CP Code", "Margin"]]
    ncdex_df.columns = ["Account Id", "NCDEX MARGIN"]
    ncdex_df["NCDEX MARGIN"] = pd.to_numeric(ncdex_df["NCDEX MARGIN"], errors="coerce").fillna(0)

    cm_df = _read_file_dynamic(base_folder, f"CM_ClientLevelCollaterals_{today_ddmmyyyy}*")
    cm_df = cm_df[["Client Code", "Minimum Margins (a)"]]
    cm_df.columns = ["Account Id", "CM MARGIN"]
    cm_df["CM MARGIN"] = pd.to_numeric(cm_df["CM MARGIN"], errors="coerce").fillna(0)

    co_df = _read_file_dynamic(base_folder, f"CO_ClientLevelCollaterals_{today_ddmmyyyy}*")
    co_df = co_df[["Client Code", "Minimum Margins (a)"]]
    co_df.columns = ["Account Id", "CO MARGIN"]
    co_df["CO MARGIN"] = pd.to_numeric(co_df["CO MARGIN"], errors="coerce").fillna(0)

    fo_df = _read_file_dynamic(base_folder, f"FO_ClientLevelCollaterals_{today_ddmmyyyy}*")
    fo_df = fo_df[["Client Code", "Minimum Margins (a)"]]
    fo_df.columns = ["Account Id", "FO MARGIN"]
    fo_df["FO MARGIN"] = pd.to_numeric(fo_df["FO MARGIN"], errors="coerce").fillna(0)

    mcx_df = _read_mcx_file(base_folder, today_yyyymmdd)

    final_df = (main_df.merge(ncdex_df, on="Account Id", how="left")
                        .merge(cm_df, on="Account Id", how="left")
                        .merge(co_df, on="Account Id", how="left")
                        .merge(fo_df, on="Account Id", how="left")
                        .merge(mcx_df, on="Account Id", how="left"))
    final_df = final_df.fillna(0)

    file90_df = _read_90_file(base_folder)
    probable_ids = set(main_df["Account Id"].astype(str).str.strip())
    file90_ids = set(file90_df["Account Id"].astype(str).str.strip())
    extra_90_clients = sorted(file90_ids - probable_ids)
    print("\n================ DEBUG INFO ================")
    print(f"Total clients in Probable file   : {len(probable_ids)}")
    print(f"Total clients in 90% file        : {len(file90_ids)}")
    print(f"Extra clients added from 90% file: {len(extra_90_clients)}")
    if extra_90_clients:
        print("Extra clients from 90% File: " + ", ".join(extra_90_clients))
    print("============================================\n")

    final_df["Account Id"] = final_df["Account Id"].astype(str).str.strip()
    file90_df["Account Id"] = file90_df["Account Id"].astype(str).str.strip()
    final_df = final_df.merge(file90_df, on="Account Id", how="left")
    final_df["90% File Sum"] = final_df["90% File Sum"].fillna(0)

    combined_ids = pd.Series(pd.concat([final_df["Account Id"], file90_df["Account Id"]])).drop_duplicates()
    final_df = final_df.set_index("Account Id")
    file90_df = file90_df.set_index("Account Id")
    final_df = final_df.reindex(combined_ids)
    final_df["90% File Sum"] = final_df["90% File Sum"].combine_first(file90_df["90% File Sum"])
    final_df["MCX OTHER(90% FILE)"] = final_df["MCX OTHER(90% FILE)"].combine_first(file90_df["MCX OTHER(90% FILE)"])
    final_df = final_df.fillna(0).reset_index()

    final_df["PROBABLE MARGIN"] = (final_df["PROBABLE MARGIN"] - final_df["MCX MARGIN"] + final_df["90% File Sum"])
    final_df["TOTAL(C+D+E+F+H)"] = (final_df["NCDEX MARGIN"] + final_df["CM MARGIN"]
                                     + final_df["CO MARGIN"] + final_df["FO MARGIN"] + final_df["90% File Sum"])

    delivery_df = _read_delivery_file(base_folder)
    if delivery_df is None:
        final_df["DELIVERY MARGIN"] = 0
        print("✅ DELIVERY MARGIN column added with 0 (file not present).")
    else:
        final_df["Account Id"] = final_df["Account Id"].astype(str).str.strip()
        delivery_df["Account Id"] = delivery_df["Account Id"].astype(str).str.strip()
        negative_delivery = delivery_df[delivery_df["DELIVERY MARGIN"] < 0]
        if not negative_delivery.empty:
            # Headless server — no operator to click a Yes/No box. Warn loudly
            # (every affected client named) and CONTINUE, so the scheduled daily
            # run always completes and the reconciliation report (which already
            # flags every MISMATCH) reaches the team instead of silently hanging.
            client_list = ", ".join(negative_delivery["Account Id"].tolist())
            print(f"⚠️ [PROBABLE_NEGATIVE_DELIVERY] Negative DELIVERY MARGIN for: {client_list} "
                  f"— continuing (headless run), please review this in the report.")
        final_df = final_df.merge(delivery_df, on="Account Id", how="outer")
        final_df["DELIVERY MARGIN"] = final_df["DELIVERY MARGIN"].fillna(0)
        final_df = final_df.fillna(0)
        print("✅ DELIVERY MARGIN successfully merged.")

    if "MCX OTHER(90% FILE)" not in final_df.columns:
        raise Exception("Critical Error: MCX OTHER(90% FILE) column missing. 90% file structure may be incorrect.")
    final_df["MCX OTHER(90% FILE)"] = final_df["MCX OTHER(90% FILE)"].fillna(0)

    final_df["REMARKS"] = final_df.apply(
        lambda x: "MATCHED" if round(x["PROBABLE MARGIN"], 2) == round(x["TOTAL(C+D+E+F+H)"], 2) else "MISMATCH",
        axis=1)

    final_df = final_df[["Account Id", "PROBABLE MARGIN", "NCDEX MARGIN", "CM MARGIN", "CO MARGIN",
                          "FO MARGIN", "MCX MARGIN", "90% File Sum", "TOTAL(C+D+E+F+H)",
                          "DELIVERY MARGIN", "MCX OTHER(90% FILE)", "REMARKS"]]

    original_probable_full = _read_file_dynamic(base_folder, main_pattern)
    original_probable_full["Account ID"] = original_probable_full["Account ID"].astype(str).str.strip()
    final_df = final_df.rename(columns={"Account Id": "Account ID"})
    final_df["Account ID"] = final_df["Account ID"].astype(str).str.strip()

    updated_probable = original_probable_full.merge(
        final_df[["Account ID", "PROBABLE MARGIN"]], on="Account ID", how="right")
    updated_probable["Initial Exp. Margin"] = updated_probable["PROBABLE MARGIN"]
    updated_probable = updated_probable.merge(
        final_df[["Account ID", "MCX OTHER(90% FILE)", "DELIVERY MARGIN"]], on="Account ID", how="left")
    updated_probable["Other Margin"] = (updated_probable["MCX OTHER(90% FILE)"].fillna(0)
                                         + updated_probable["DELIVERY MARGIN"].fillna(0))
    updated_probable.drop(columns=["PROBABLE MARGIN", "MCX OTHER(90% FILE)", "DELIVERY MARGIN"],
                           inplace=True, errors="ignore")
    updated_probable = updated_probable.fillna(0)

    for col in ["Allocated Cash", "Cash Repledge", "Non Cash Repledge", "Client Balance",
                "Initial Exp. Margin", "Other Margin"]:
        if col in updated_probable.columns:
            updated_probable[col] = pd.to_numeric(updated_probable[col], errors="coerce").fillna(0)

    updated_probable["Short On Initial Exp. Margin"] = (
        updated_probable["Allocated Cash"] + updated_probable["Cash Repledge"]
        + updated_probable["Non Cash Repledge"] - updated_probable["Initial Exp. Margin"])
    updated_probable["Short On Initial Exp. Other Margin"] = (
        updated_probable["Client Balance"] + updated_probable["Cash Repledge"]
        + updated_probable["Non Cash Repledge"] - updated_probable["Initial Exp. Margin"]
        - updated_probable["Other Margin"])

    updated_probable_file = os.path.join(base_folder, f"Probable_Fullfile_K_{today_ddmmyyyy}.xlsx")
    updated_probable = _round_numeric(updated_probable, 2)
    updated_probable.to_excel(updated_probable_file, index=False)
    _auto_adjust_column_width(updated_probable_file)
    print("✅ Probable_Fullfile_K created with ALL sumcheck clients included.")

    output_file = os.path.join(base_folder, f"All_Exchange_Margin_Comparison_{today_ddmmyyyy}.xlsx")
    final_df = _round_numeric(final_df, 2)
    final_df.to_excel(output_file, index=False)
    _auto_adjust_column_width(output_file)
    print("✅ All_Exchange_Margin_Comparison created successfully with all clients included.")

    _send_email_with_retry(base_folder, today_ddmmyyyy, today_str_prev,
                            [updated_probable_file, output_file], safe_test=safe_test)

    if safe_test:
        # The backup MOVES every input file out of the folder. Doing that after
        # a rehearsal would leave the real run with nothing to work from — and
        # the alerts step still needs Probable_Fullfile_K to exist. Skipped.
        print("🧪 SAFE TEST — reconciliation backup & cleanup SKIPPED on purpose "
              "(the day's input files stay in place for your real run).")
    else:
        _backup_and_cleanup(base_folder, today_ddmmyyyy)


if __name__ == "__main__":
    run_probable_reconcile()
