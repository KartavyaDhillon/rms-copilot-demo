"""
core/pipelines/cli_upload.py — Client Prudential Limit's FTP-PUSH + BACKUP step.

This used to also download+convert the MCX RSK383 file — that's now its own dedicated
"MCX Web Allocation" button/step (core/pipelines/cli_mcx_download.py), same pattern as
CTCL's standalone "📥 MCX 90%" button. This module only pushes whatever's currently
sitting in CLI_FOLDER up to the FTP server, then backs everything up.

Rewritten to run as a genuine subprocess via core.pipeline_box._stream_code() (like
every other pipeline's main Run) so Stop can kill it instantly regardless of which
blocking call (the FTP transfer itself) is in progress.

STEP 1 — FTP upload: same ALWAYS_FILES + DATE_BASED_FILES rules as the original
         MCX_UPLOAD_BACKUP.py, but with a real file-existence check — any expected
         file that's missing is reported by name (via [MISSING_FILE]) instead of being
         silently skipped, and the upload continues with whatever IS present.
STEP 2 — backup + cleanup (move everything except .py/.bat into
         BACKUPFILES AUTOMATED/<DD-MM-YYYY>/, same as the original script).

FTP: FTP_HOST/FTP_USER/FTP_PASS (shared with the Download step and other downloaders).

Tags printed for core.pipeline_box._status():
  [MISSING_FILE] <name>       — an expected file wasn't uploaded
  [FRIENDLY_MSG] <text>       — plain-English AI fallback for an unrecognized failure
"""
import datetime
import os
import shutil
import time

from core.error_translator import translate_error


def _archive_to_db(tag, kind, filepath, max_retries=3, retry_delay=2):
    """Copy a file's bytes into PostgreSQL (or SQLite fallback) with retry.
    Never raises. Same dual-path pattern already used inline further down in
    this file's normal backup step — pulled out so backfill_date_to_db() can
    reuse it without duplicating the USE_POSTGRES/save_run_file dance."""
    if not filepath or not os.path.exists(filepath):
        return False
    fname = os.path.basename(filepath)
    try:
        use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
        _sf = (__import__("core.db_postgres", fromlist=["save_run_file"]).save_run_file if use_pg
               else __import__("core.db", fromlist=["save_run_file"]).save_run_file)
        with open(filepath, "rb") as f:
            content = f.read()
    except Exception as e:
        print(f"[cli_upload] WARNING could not read {filepath} for archiving: {e}")
        return False
    for attempt in range(1, max_retries + 1):
        try:
            _sf("cli", kind, fname, content, tag=tag)
            print(f"[cli_upload] archived {fname} ({len(content):,} bytes) -> DB")
            return True
        except Exception as e:
            if attempt < max_retries:
                print(f"[cli_upload] archive attempt {attempt}/{max_retries} failed for {fname}: {e} — retrying in {retry_delay}s")
                time.sleep(retry_delay)
            else:
                print(f"[cli_upload] WARNING: {fname} could NOT be archived after {max_retries} attempts: {e}")
    return False


def backfill_date_to_db(date_str):
    """Push an ALREADY-backed-up CLI (Client Prudential Limit) date folder
    into the DB — for when the upload genuinely happened OUTSIDE this
    dashboard and its files were then copied by hand into this machine's
    BACKUPFILES AUTOMATED\\<date> folder. Unlike ctcl/ms15, CLI's backup has
    NO two-digit run subfolder — everything for a day lands directly in the
    date folder — so this takes just a date, not a run number.

    `date_str` — folder name exactly as on disk, DD-MM-YYYY (e.g. "21-07-2026").
    Safe to call more than once — re-archiving just adds another row."""
    base_folder = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    date_folder = os.path.join(base_folder, "BACKUPFILES AUTOMATED", date_str)
    if not os.path.isdir(date_folder):
        return {"ok": False, "error": f"Folder not found: {date_folder}"}
    archived, skipped = [], []
    for fname in sorted(os.listdir(date_folder)):
        fpath = os.path.join(date_folder, fname)
        if not os.path.isfile(fpath):
            continue
        if _archive_to_db(date_str, "backup", fpath):
            archived.append(fname)
        else:
            skipped.append(fname)
    return {"ok": True, "folder": date_folder, "tag": date_str, "archived": archived, "skipped": skipped}


def run_cli_upload():
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    base_folder = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    os.makedirs(base_folder, exist_ok=True)
    today_str = datetime.datetime.now().strftime("%d%m%Y")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    upload_log_file = os.path.join(base_folder, "upload_log.txt")
    _log_buffer = []

    def _p(msg):
        print(msg, flush=True)
        _log_buffer.append(str(msg))

    # ── STEP 1: FTP upload with a real missing-file check ────────────────────
    _p("🚀 STEP 1: FTP upload")
    expected = [
        ("CLI_PRUDENTIAL_LIMIT",              "CLI_PRUDENTIAL_LIMIT.txt"),
        ("MCX_WebAllocationDeallocation",     "MCX_WebAllocationDeallocation.csv"),
        ("CM_ClientLevelCollaterals",         f"CM_ClientLevelCollaterals_{today_str}"),
        ("FO_ClientLevelCollaterals",         f"FO_ClientLevelCollaterals_{today_str}"),
        ("CO_ClientLevelCollaterals",         f"CO_ClientLevelCollaterals_{today_str}"),
        ("ClientNetEffectiveDeposits",        f"ClientNetEffectiveDeposits_{today_str}"),
    ]
    valid_ext = (".txt", ".csv", ".xlsx")
    try:
        local_files = os.listdir(base_folder)
    except Exception:
        local_files = []

    files_to_upload, missing_names = [], []
    for label, pattern in expected:
        match = next((f for f in local_files
                      if pattern.lower() in f.lower() and f.lower().endswith(valid_ext)), None)
        if match:
            files_to_upload.append(os.path.join(base_folder, match))
        else:
            missing_names.append(label)
            _p(f"[MISSING_FILE] {label}")

    hard_error = None
    if not files_to_upload:
        _p("⚠️ No files found to upload")
    else:
        try:
            from ftplib import FTP
            ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
            ftp_user = os.getenv("FTP_USER", "TRADING")
            ftp_pass = os.getenv("FTP_PASS", "")
            remote_folder = "/TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION"
            ftp = FTP(ftp_host)
            ftp.login(ftp_user, ftp_pass)
            ftp.cwd("/")
            for d in remote_folder.strip("/").split("/"):
                try: ftp.cwd(d)
                except Exception: ftp.mkd(d); ftp.cwd(d)

            for file_path in files_to_upload:
                filename = os.path.basename(file_path)
                success, error_msg = False, ""
                for attempt in range(3):
                    try:
                        with open(file_path, "rb") as f:
                            ftp.storbinary(f"STOR {filename}", f)
                        success = True
                        break
                    except Exception as e:
                        error_msg = str(e)
                        time.sleep(2)
                with open(upload_log_file, "a") as ulog:
                    if success:
                        ulog.write(f"[{now_str}] SUCCESS | {filename}\n")
                        _p(f"⬆ Uploaded: {filename}")
                    else:
                        ulog.write(f"[{now_str}] FAILED  | {filename} | {error_msg}\n")
                        _p(f"⚠️ Upload failed: {filename} — {error_msg}")
            ftp.quit()
        except Exception as e:
            hard_error = f"FTP upload failed: {e}"
            _p(f"❌ {hard_error}")

    # ── STEP 2: backup + cleanup (still runs even if upload had trouble) ─────
    _p("🚀 STEP 2: Backup + cleanup")
    try:
        today_folder = datetime.datetime.now().strftime("%d-%m-%Y")
        backup_path = os.path.join(base_folder, "BACKUPFILES AUTOMATED", today_folder)
        os.makedirs(backup_path, exist_ok=True)
        valid_ext2 = (".csv", ".txt", ".xlsx")
        exclude_ext = (".py", ".bat")
        moved = []
        for f in os.listdir(base_folder):
            full = os.path.join(base_folder, f)
            if not os.path.isfile(full) or f.endswith(exclude_ext) or not f.endswith(valid_ext2):
                continue
            dest = os.path.join(backup_path, f)
            if os.path.exists(dest):
                os.remove(dest)
            shutil.move(full, dest)
            moved.append(f)
            # Also archive a durable copy into Postgres/SQLite — same table the
            # manual "Upload files" button already uses (pb_upload in
            # pipeline_box.py) — so the backup isn't only living on whichever
            # machine ran this. Local move above still happens regardless (the
            # exchange-login scripts need the local folder), this is additive.
            try:
                use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
                _sf = (__import__("core.db_postgres", fromlist=["save_run_file"]).save_run_file if use_pg
                       else __import__("core.db", fromlist=["save_run_file"]).save_run_file)
                with open(dest, "rb") as fb:
                    _sf("cli", "backup", f, fb.read(), tag=today_folder)
            except Exception as _dbe:
                _p(f"  DB archive skipped for {f}: {_dbe}")
        _p(f"📦 Backup done — {len(moved)} file(s) moved to {backup_path} + Postgres/SQLite")
    except Exception as e:
        _p(f"⚠️ Backup/cleanup failed: {e}")

    if hard_error:
        _ai = translate_error("Client Prudential Limit upload", _log_buffer)
        _p(f"[FRIENDLY_MSG] {_ai or hard_error}")
        raise SystemExit(1)
    _p("✅ Client Prudential Limit upload step finished"
       + (f" ({len(missing_names)} file(s) skipped — see popup)" if missing_names else ""))


if __name__ == "__main__":
    run_cli_upload()
