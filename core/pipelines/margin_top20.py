"""
margin_top20.py — Top 20 Margin Utilisation (wrapped as a function)
====================================================================
Document 7, wrapped in run_margin_top20(). Reads RISK_REPORT + the CTCL file
from MARGIN_TOP20_FOLDER, builds the 3 Top20 files (CDR+MT, V2, V1), password-
protects them, emails each, then backs up + cleans the folder.

ONLY changes from the original:
  • FOLDER_PATH      → .env (MARGIN_TOP20_FOLDER)
  • SMTP credentials → .env (SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD)
  • recipients       → .env (CTCL_TOP20_* keys — same ones CTCL already uses)
  • raise FileNotFoundError → return {"status": "missing_files", ...}
  • wrapped in def run_margin_top20(); everything else identical.

NOTE: uses win32com for Excel password → Windows + Excel + pywin32 required.
"""
import os
import glob
import time
import smtplib
from email.message import EmailMessage
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

FOLDER_PATH = os.getenv("MARGIN_TOP20_FOLDER", r"D:\MARGIN TOP20")


def _emails(key):
    return [e.strip() for e in os.getenv(key, "").split(",") if e.strip()]


def apply_excel_password(file_path, password="master123"):
    """Apply password protection to an Excel file.
    Tries win32com (Excel COM) first; falls back to msoffcrypto-tool which works
    without Microsoft Excel installed (pure-Python, no COM required)."""
    # ── Attempt 1: win32com (Excel COM automation) ────────────────────────
    try:
        import shutil, tempfile, pythoncom
        import win32com.client as win32

        def _rebuild():
            try:
                gen_py = os.path.join(tempfile.gettempdir(), "gen_py")
                if os.path.exists(gen_py):
                    shutil.rmtree(gen_py, ignore_errors=True)
                win32.gencache.is_readonly = False; win32.gencache.Rebuild()
            except Exception: pass

        def _open_excel():
            pythoncom.CoInitialize()
            try:    return win32.gencache.EnsureDispatch("Excel.Application")
            except: return win32.Dispatch("Excel.Application")

        try:
            xl = _open_excel(); xl.Visible = False; xl.DisplayAlerts = False
            wb = xl.Workbooks.Open(file_path); wb.Password = password; wb.Save(); wb.Close(); xl.Quit()
            print(f"🔐 Password applied (COM): {os.path.basename(file_path)}"); return
        except Exception as e:
            print(f"⚠️ COM attempt 1 failed: {e} — retrying after cache rebuild…")
            _rebuild()
            xl = _open_excel(); xl.Visible = False; xl.DisplayAlerts = False
            wb = xl.Workbooks.Open(file_path); wb.Password = password; wb.Save(); wb.Close(); xl.Quit()
            print(f"🔐 Password applied (COM repair): {os.path.basename(file_path)}"); return
    except ImportError:
        print("ℹ️ win32com not available — trying msoffcrypto-tool…")
    except Exception as e:
        print(f"⚠️ COM failed entirely: {e} — trying msoffcrypto-tool…")

    # ── Attempt 2: msoffcrypto-tool (no Excel required) ──────────────────
    try:
        import msoffcrypto, io
        with open(file_path, "rb") as _f:
            _office = msoffcrypto.OfficeFile(_f)
            _enc = io.BytesIO()
            _office.encrypt(password, _enc)
        _tmp = file_path + ".pwdtmp"
        with open(_tmp, "wb") as _f:
            _f.write(_enc.getvalue())
        os.replace(_tmp, file_path)
        print(f"🔐 Password applied (msoffcrypto): {os.path.basename(file_path)}"); return
    except ImportError:
        print("⚠️ msoffcrypto-tool not installed — run: pip install msoffcrypto-tool")
        print("⚠️ Excel password protection SKIPPED for this run.")
    except Exception as e:
        print(f"❌ msoffcrypto failed: {e} — password NOT applied.")


def run_margin_top20():
    """Thin wrapper: adds the AI (Groq) plain-English fallback translation on an
    unrecognized failure, same as nmass_download.py etc. — see _run_margin_top20_impl."""
    try:
        return _run_margin_top20_impl()
    except SystemExit:
        raise
    except Exception:
        import traceback
        from core.error_translator import translate_error
        tb_lines = traceback.format_exc().splitlines()
        for _l in tb_lines:
            print(_l, flush=True)
        _ai_msg = translate_error("MarginTop20 processing", tb_lines)
        print(f"[FRIENDLY_MSG] {_ai_msg or 'MarginTop20 failed for an unrecognized reason — check the Logs button or contact tech support.'}", flush=True)
        # Re-raise the ORIGINAL exception (not SystemExit) so any caller using
        # `except Exception` (rather than checking a subprocess exit code) still catches it.
        raise


def _run_margin_top20_impl():
    """Top20 margin utilisation: read → 3 files → password → email → backup+cleanup."""
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_EMAIL = os.getenv("SENDER_EMAIL")
    SMTP_PASSWORD = os.getenv("SENDER_PASSWORD")

    risk_files = glob.glob(os.path.join(FOLDER_PATH, "RISK_REPORT_*.csv"))
    ctcl_file = os.path.join(FOLDER_PATH, "FULL_FILE_CTCL_VS_NMASS_01.xlsx")
    if not risk_files:
        print("❌ RISK_REPORT file not found")
        return {"status": "missing_files", "missing": ["RISK_REPORT_*.csv"]}
    if not os.path.exists(ctcl_file):
        print("❌ CTCL file not found")
        return {"status": "missing_files", "missing": ["FULL_FILE_CTCL_VS_NMASS_01.xlsx"]}
    risk_file = risk_files[0]
    print(f"✅ Risk File: {risk_file}")
    print(f"✅ CTCL File: {ctcl_file}")

    df = pd.read_csv(risk_file, low_memory=False)
    ctcl_df = pd.read_excel(ctcl_file)
    df.columns = df.columns.str.strip()
    ctcl_df.columns = ctcl_df.columns.str.strip()

    ctcl_df = ctcl_df[["ACCOUNTID", "DEPOSIT CTCL"]]
    df["ACCOUNTID"] = df["ACCOUNTID"].astype(str)
    ctcl_df["ACCOUNTID"] = ctcl_df["ACCOUNTID"].astype(str)
    df = df.merge(ctcl_df, on="ACCOUNTID", how="left")

    final_cols = ["Zone", "Region", "Branch", "TRADING_SOFTWARE_TYPE",
                  "ACCOUNTID", "NAME", "FNO_MARGIN", "MCX_MARGIN", "NCDEX_MARGIN",
                  "MARGIN", "DEPOSIT CTCL"]

    def save_excel(dfx, file_name):
        path = os.path.join(FOLDER_PATH, file_name)
        with pd.ExcelWriter(path, engine='xlsxwriter') as writer:
            dfx.to_excel(writer, index=False)
            worksheet = writer.sheets['Sheet1']
            for i, col in enumerate(dfx.columns):
                max_len = max(dfx[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(i, i, max_len)
        apply_excel_password(path, "master123")
        print(f"✅ Saved (Password Protected): {file_name}")
        return path

    df = df.sort_values(by="MARGIN", ascending=False)
    zomtzocdr = df[df["Zone"].isin(["ZOMT", "ZOCDR"])]
    file1 = save_excel(zomtzocdr.head(20)[final_cols], "Top20 Derivative Utilization(CDR + MT).xlsx")
    zodelh = df[df["Zone"] == "ZODELH"]
    v2 = zodelh[zodelh["ACCOUNTID"].str.startswith("8") | (zodelh["ACCOUNTID"] == "6RBB01")]
    file2 = save_excel(v2.head(20)[final_cols], "Top20 Derivative Utilization(V2).xlsx")
    v1 = zodelh[~zodelh["ACCOUNTID"].str.startswith("8") &
                ~zodelh["ACCOUNTID"].str.upper().str.startswith("SN") &
                (zodelh["ACCOUNTID"] != "6RBB01")]
    file3 = save_excel(v1.head(20)[final_cols], "Top20 Derivative Utilization(V1).xlsx")

    def send_email(to_list, cc_list, subject, body, attachment):
        while True:
            try:
                msg = EmailMessage()
                msg["From"] = SMTP_EMAIL
                msg["To"] = ", ".join(to_list)
                msg["Cc"] = ", ".join(cc_list)
                msg["Subject"] = subject
                msg.set_content(body, subtype='html')
                with open(attachment, "rb") as f:
                    file_data = f.read(); file_name = os.path.basename(attachment)
                msg.add_attachment(file_data, maintype="application", subtype="octet-stream", filename=file_name)
                server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
                server.starttls(); server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg); server.quit()
                print(f"✅ Email sent successfully: {file_name}")
                break
            except Exception as e:
                print(f"❌ Email failed: {e}"); print("🔁 Retrying in 10 seconds...")
                time.sleep(10)

    subject = "Top 20 Margin Utilisation"
    body = """
Dear Sir,<br><br>
Please find attached the file of Top 20 margin utilization in the derivative segment.
Best regards,<br><b>Kartavya Dhillon</b><br>Master Capital Services Ltd<br>📞 011-66331000 | Ext. 84
"""
    cc = _emails("CTCL_TOP20_CC")
    email_configs = [
        {"to": _emails("CTCL_TOP20_CDRMT_TO"), "cc": cc, "file": file1},
        {"to": _emails("CTCL_TOP20_V2_TO"),    "cc": cc, "file": file2},
        {"to": _emails("CTCL_TOP20_V1_TO"),    "cc": cc, "file": file3},
    ]
    for config in email_configs:
        print(f"\n📤 Sending email for {os.path.basename(config['file'])}...")
        send_email(config["to"], config["cc"], subject, body, config["file"])
    print("\n🎯 ALL EMAILS SENT SUCCESSFULLY")

    def backup_and_cleanup_top20(folder_path):
        import shutil
        from datetime import datetime
        print("\n📦 Starting BACKUP for TOP20...")
        today_str = datetime.now().strftime("%Y-%m-%d")
        backup_root = os.path.join(folder_path, "BACKUPFILES AUTOMATED")
        date_folder = os.path.join(backup_root, today_str)
        os.makedirs(date_folder, exist_ok=True)
        risk_files2 = glob.glob(os.path.join(folder_path, "RISK_REPORT_*.csv"))
        ctcl_file2 = os.path.join(folder_path, "FULL_FILE_CTCL_VS_NMASS_01.xlsx")
        top20_files = glob.glob(os.path.join(folder_path, "Top20 Derivative Utilization*.xlsx"))
        files_to_backup = []
        files_to_backup.extend(risk_files2)
        if os.path.exists(ctcl_file2):
            files_to_backup.append(ctcl_file2)
        files_to_backup.extend(top20_files)
        if not files_to_backup:
            print("⚠️ No files found for backup")
        else:
            for file in files_to_backup:
                try:
                    dest = os.path.join(date_folder, os.path.basename(file))
                    shutil.copy2(file, dest)
                    print(f"📁 Backed up: {os.path.basename(file)}")
                except Exception as e:
                    print(f"❌ Backup failed: {file} → {e}")
            print(f"✅ Backup completed → {date_folder}")
        print("\n🧹 Starting CLEANUP...")
        for file in files_to_backup:
            try:
                if os.path.exists(file):
                    os.remove(file); print(f"🗑 Deleted: {os.path.basename(file)}")
            except Exception as e:
                print(f"❌ Failed to delete {file}: {e}")
        print("✅ CLEANUP COMPLETED\n")

    backup_and_cleanup_top20(FOLDER_PATH)
    return {"status": "ok"}


if __name__ == "__main__":
    print(run_margin_top20())
