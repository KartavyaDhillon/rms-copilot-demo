"""
ftp_download.py — Daily FTP file download + distribution (wrapped as a function)
==================================================================================
Rebuilt from Document 14 (corrected version). Connects to the FTP server, downloads
all today's files, distributes to destination folders by filename pattern, logs, cleans.

ONLY changes from the original:
  • All paths and FTP credentials → .env
  • Wrapped in def run_ftp_download(); everything else identical to Document 14.

.env keys:
  FTP_HOST, FTP_USER, FTP_PASS
  FTP_BASE_LOCAL=C:\\FTP FILES
  FTP_BANK_ALERT=C:\\BANK ALERT
  FTP_POSITIONBOOK=C:\\FILES CHECKING POSITIONBOOK
  FTP_T1_FINAL=C:\\T1 HOLDING
  FTP_O319_FINAL=C:\\KAMBALA MCX CHECKING\\Only 3.19
  FTP_KAMB_FINAL=C:\\KAMBALA MCX CHECKING\\Kambala
  FTP_FULL_RECO=C:\\KARTAVYA\\FULL RECO REPORT
  FTP_COMM_LIMIT_DIR=C:\\KARTAVYA\\MCX COMM LIMIT
  FTP_NOREN_FINAL=C:\\KAMBALA MCX CHECKING\\NOREN
"""
import os
import shutil
from ftplib import FTP
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

FTP_HOST       = os.getenv("FTP_HOST",       "202.58.232.124")
FTP_USER       = os.getenv("FTP_USER",       "TRADING")
FTP_PASS       = os.getenv("FTP_PASS",       "")

BASE_LOCAL     = os.getenv("FTP_BASE_LOCAL",     r"C:\FTP FILES")
BANK_ALERT     = os.getenv("FTP_BANK_ALERT",     r"C:\BANK ALERT")
POSITIONBOOK   = os.getenv("FTP_POSITIONBOOK",   r"C:\FILES CHECKING POSITIONBOOK")
T1_FINAL       = os.getenv("FTP_T1_FINAL",       r"C:\T1 HOLDING")
O319_FINAL     = os.getenv("FTP_O319_FINAL",     r"C:\KAMBALA MCX CHECKING\Only 3.19")
KAMB_FINAL     = os.getenv("FTP_KAMB_FINAL",     r"C:\KAMBALA MCX CHECKING\Kambala")
FULL_RECO      = os.getenv("FTP_FULL_RECO",      r"C:\KARTAVYA\FULL RECO REPORT")
COMM_LIMIT_DIR = os.getenv("FTP_COMM_LIMIT_DIR", r"C:\KARTAVYA\MCX COMM LIMIT")
NOREN_FINAL    = os.getenv("FTP_NOREN_FINAL",    r"C:\KAMBALA MCX CHECKING\NOREN")


def run_ftp_download():
    """Download today's FTP files, distribute to destination folders, log + clean up."""
    today     = datetime.today().strftime("%d%m%Y")
    timestamp = datetime.now().strftime("%H%M%S")

    BACKUP_ROOT = os.path.join(BASE_LOCAL, "BACKUPFILES AUTOMATED")
    DAY_ROOT    = os.path.join(BACKUP_ROOT, today)
    POSI_KAMB   = os.path.join(DAY_ROOT, "POSI_KAMB")
    DEPO_KAMB   = os.path.join(DAY_ROOT, "DEPO_KAMB")
    DEPO_O319   = os.path.join(DAY_ROOT, "DEPO_O319")
    O319_POSI   = os.path.join(DAY_ROOT, "O319 POSI")
    T1_TEMP     = os.path.join(DAY_ROOT, "T1 HOLDING")

    ALL_DIRS = [
        BASE_LOCAL, BACKUP_ROOT, DAY_ROOT,
        POSI_KAMB, DEPO_KAMB, DEPO_O319,
        O319_POSI, T1_TEMP,
        BANK_ALERT, POSITIONBOOK, T1_FINAL,
        O319_FINAL, KAMB_FINAL, FULL_RECO,
        COMM_LIMIT_DIR, NOREN_FINAL
    ]
    for d in ALL_DIRS:
        os.makedirs(d, exist_ok=True)

    log_lines = []
    def log(msg):
        print(msg)
        log_lines.append(msg)

    DOWNLOAD_MAP = {
        "/TRADING/CTCL_FILES/KORP_LIVE/SYMF": [
            f"SYMPHONY_RMS_LIMIT_{today}.txt",
            f"T1_HOLDING_SYMPHONY_{today}.TXT",
        ],
        "/TRADING/CTCL_FILES/KORP_LIVE/NOREN/HOLD": [
            f"T1_HOLDING_NOREN_{today}.TXT",
            f"MF_NOREN_HOLDING_{today}.TXT",
        ],
        "/TRADING/CTCL_FILES/KORP_LIVE/KAMB/HOLD": [
            f"T1_HOLDING_KAMBALA_{today}.TXT",
            f"MF_KAMB_HOLDING_{today}.TXT",
        ],
        "/TRADING/CTCL_FILES/KORP_LIVE/O319/HOLD": [
            f"T1_HOLDING_ISIN_{today}.TXT",
        ],
        "/TRADING/CTCL_FILES/KORP_LIVE/KAMB/POSI": [
            f"BSEF_POSITION_KAMB_{today}.txt",
            f"fo_position_kambala_{today}.txt",
            f"KAMB_CASH_POS_NRML_{today}.txt",
            f"MCX_POSITION_56720_{today}.csv",
        ],
        "/TRADING/CTCL_FILES/KORP_LIVE/O319/POSI": [
            f"MCX_POSITION_56720_{today}.csv",
        ],
        "/TRADING/CTCL_FILES/KORP_LIVE/KAMB/DEPO": [
            f"Kambala_Omny_Depo_Adhoc_All_level.txt_{today}.txt",
            f"Kambala_OMNY_COM_DEPO_ADHOC_{today}.TXT",
            f"Kambala_MTF_DEPO_{today}.TXT",
        ],
        "/TRADING/CTCL_FILES/KORP_LIVE/O319/DEPO": [
            f"Omni3.19_Depo_Adhoc_All_level_{today}.txt",
        ],
        "/TRADING/CTCL_FILES/KORP_LIVE/NOREN/POSI": [
            f"MCX_POSITION_56720_{today}.csv",
        ],
    }

    # ── FTP DOWNLOAD (your original logic with try/except per Doc 14) ────────
    ftp = FTP(FTP_HOST)
    ftp.login(FTP_USER, FTP_PASS)
    log("Connected to FTP")

    for folder, files in DOWNLOAD_MAP.items():
        try:
            ftp.cwd(folder)
            remote_files = ftp.nlst()
        except Exception as e:
            log(f"❌ Folder Error: {folder}")
            log(f"Reason: {str(e)}")
            log("Skipping...\n")
            continue

        for fname in files:
            try:
                found = False
                for rf in remote_files:
                    if rf.lower() == fname.lower():
                        if folder.endswith("/KAMB/POSI") and rf.lower().startswith("mcx_position_56720_"):
                            local_path = os.path.join(POSI_KAMB, rf)
                        elif folder.endswith("/O319/POSI") and rf.lower().startswith("mcx_position_56720_"):
                            local_path = os.path.join(O319_POSI, rf)
                        elif folder.endswith("/NOREN/POSI") and rf.lower().startswith("mcx_position_56720_"):
                            local_path = os.path.join(DAY_ROOT, rf)
                        else:
                            local_path = os.path.join(DAY_ROOT, rf)
                        with open(local_path, "wb") as f:
                            ftp.retrbinary("RETR " + rf, f.write)
                        log(f"✔ Downloaded {rf} from {folder}")
                        found = True
                        break
                if not found:
                    log(f"❌ NOT FOUND: {fname} in {folder}")
            except Exception as e:
                log(f"❌ Error while downloading {fname}")
                log(f"Reason: {str(e)}")
                log("Skipping...\n")
                continue

    try:
        ftp.quit()
    except Exception:
        pass

    # ── DISTRIBUTION (Doc 14 logic, unchanged) ────────────────────────────────
    for f in os.listdir(POSI_KAMB):
        if f.lower().startswith("mcx_position_56720_"):
            shutil.copy2(os.path.join(POSI_KAMB, f), KAMB_FINAL)

    for f in os.listdir(O319_POSI):
        if f.lower().startswith("mcx_position_56720_"):
            shutil.copy2(os.path.join(O319_POSI, f), O319_FINAL)

    for f in os.listdir(DAY_ROOT):
        if f.lower().startswith("mcx_position_56720_"):
            shutil.copy2(os.path.join(DAY_ROOT, f), NOREN_FINAL)

    for f in os.listdir(DAY_ROOT):
        src = os.path.join(DAY_ROOT, f)
        if not os.path.isfile(src):
            continue
        fl = f.lower()

        if fl.startswith("symphony_rms_limit_"):
            shutil.copy2(src, BANK_ALERT)
            shutil.copy2(src, FULL_RECO)

        elif fl.startswith(("t1_holding_symphony_", "t1_holding_kambala_", "t1_holding_isin_")):
            shutil.copy2(src, T1_TEMP)
            shutil.copy2(src, T1_FINAL)
            shutil.copy2(src, FULL_RECO)

        elif fl.startswith("t1_holding_noren_"):
            shutil.copy2(src, T1_TEMP)
            shutil.copy2(src, T1_FINAL)

        elif fl.startswith("mf_kamb_holding_"):
            shutil.copy2(src, FULL_RECO)
            shutil.copy2(src, T1_FINAL)

        elif fl.startswith("mf_noren_holding_"):
            shutil.copy2(src, T1_FINAL)

        elif fl.startswith(("bsef_position_kamb_", "fo_position_kambala_", "kamb_cash_pos_nrml_")):
            shutil.copy2(src, POSITIONBOOK)
            shutil.move(src, os.path.join(POSI_KAMB, f))

        elif fl.startswith("kambala_omny_com_depo_adhoc"):
            shutil.copy2(src, COMM_LIMIT_DIR)
            shutil.move(src, os.path.join(DEPO_KAMB, f))

        elif fl.startswith("kambala_omny_depo_adhoc_all_level"):
            shutil.copy2(src, BANK_ALERT)
            shutil.copy2(src, FULL_RECO)
            shutil.move(src, os.path.join(DEPO_KAMB, f))

        elif fl.startswith("kambala_mtf_depo_"):
            shutil.move(src, os.path.join(DEPO_KAMB, f))

        elif fl.startswith("omni3.19_depo_adhoc_all_level"):
            shutil.copy2(src, FULL_RECO)
            shutil.move(src, os.path.join(DEPO_O319, f))

    # ── LOG ──────────────────────────────────────────────────────────────────
    log_file = os.path.join(DAY_ROOT, f"download_log_{today}_{timestamp}.txt")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    log(f"📝 Log saved: {log_file}")

    # ── CLEAN BASE ROOT ───────────────────────────────────────────────────────
    for item in os.listdir(BASE_LOCAL):
        path = os.path.join(BASE_LOCAL, item)
        if os.path.isdir(path): continue
        if os.path.splitext(item)[1].lower() in (".py", ".bat"): continue
        try: os.remove(path)
        except Exception: pass

    # ── CLEAN DAY_ROOT ────────────────────────────────────────────────────────
    for item in os.listdir(DAY_ROOT):
        path = os.path.join(DAY_ROOT, item)
        if os.path.isdir(path): continue
        if item.lower().startswith("download_log_"): continue
        try: os.remove(path)
        except Exception: pass

    print("\n✅ DAILY FTP JOB COMPLETED SUCCESSFULLY")
    downloaded = sum(1 for l in log_lines if l.startswith("✔"))
    not_found  = sum(1 for l in log_lines if l.startswith("❌ NOT FOUND"))
    return {"status": "ok", "downloaded": downloaded, "not_found": not_found}


if __name__ == "__main__":
    print(run_ftp_download())
