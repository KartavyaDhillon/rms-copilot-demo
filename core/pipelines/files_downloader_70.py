"""
files_downloader_70.py — 70 Files Downloader (wrapped as a function)
======================================================================
Document 16, wrapped in run_files_downloader_70(trade_date). The tkinter date
picker popup is removed — trade_date comes from the caller/dashboard. CAPTCHA
popups for NCDEX are preserved (human-in-the-loop).

ONLY changes from the original:
  • get_date_from_popup() removed → trade_date parameter (YYYY-MM-DD string)
  • All credentials/paths → .env
  • Wrapped in run_files_downloader_70(); everything else identical.

.env keys:
  FILES_DOWNLOADER_DIR=D:\\70 FILES DOWNLOADER
  NSE_MEMBER_CODE=06436 (already exists)
  NSE_LOGIN_ID=M0213815 (already exists)
  NSE_PASSWORD=Yxs2Hg1/h0KUKKoF5SqEGQ== (already exists)
  MCX_SFTP_HOST=https://sftp.mcxindia.com
  MCX_USERNAME=56720
  MCX_PASSWORDS=M56720@123,M56720@789
  (EPI reuses KORP_USERNAME, KORP_PASSWORD, KORP_CLIENT_IP, KORP_CLIENT_MAC)
  (NCDEX reuses NCDEX_USERNAME, NCDEX_PASSWORD, NCDEX_SECURITY_ANSWER)
  SA_CM_DIR=C:\\PRIYANKA\\SA_01_CM
  SA_FO_DIR=C:\\PRIYANKA\\SA_01_FO
  MCX_INTRA_SAR_DIR=C:\\MCX_INTRA_SAR
  RAJEEV_DIR=C:\\Rajeev Ji
  CAPTCHA_SAVE_DIR=D:\\15 minutes\\codes\\CAPTCHA & SESSION\\captcha
  MCX_EODSAR_DIR=C:\\PRIYANKA\\MCX_EODSAR
  NCDEX_EODSAR_DIR=C:\\PRIYANKA\\NCDEX_EODSAR
"""
import os
import time
import gzip
import shutil
import requests
import urllib3
import urllib.parse
import re
from datetime import datetime, timedelta, date
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import base64
import tkinter as tk
from PIL import Image, ImageTk
import cv2
import numpy as np
import pytesseract
import pandas as pd
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

# ── Paths from .env ────────────────────────────────────────────────────────
OUTPUT_DIR      = os.getenv("FILES_DOWNLOADER_DIR",  r"D:\70 FILES DOWNLOADER")
SA_CM_DIR       = os.getenv("SA_CM_DIR",             r"C:\PRIYANKA\SA_01_CM")
SA_FO_DIR       = os.getenv("SA_FO_DIR",             r"C:\PRIYANKA\SA_01_FO")
MCX_INTRA_SAR_DIR = os.getenv("MCX_INTRA_SAR_DIR",  r"C:\MCX_INTRA_SAR")
RAJEEV_DIR      = os.getenv("RAJEEV_DIR",            r"C:\Rajeev Ji")
CAPTCHA_DIR     = os.getenv("CAPTCHA_SAVE_DIR",      r"D:\15 minutes\codes\CAPTCHA & SESSION\captcha")
MCX_EODSAR_DIR  = os.getenv("MCX_EODSAR_DIR",        r"C:\PRIYANKA\MCX_EODSAR")
NCDEX_EODSAR_DIR = os.getenv("NCDEX_EODSAR_DIR",    r"C:\PRIYANKA\NCDEX_EODSAR")

# ── NSE credentials ────────────────────────────────────────────────────────
LOGIN_URL    = "https://www.connect2nse.com/extranet-api/login/2.0"
DOWNLOAD_URL = "https://www.connect2nse.com/extranet-api/member/file/download/2.0"
CREDENTIALS  = {
    "memberCode": os.getenv("NSE_MEMBER_CODE", "06436"),
    "loginId":    os.getenv("NSE_LOGIN_ID",    "M0213815"),
    "password":   os.getenv("NSE_PASSWORD",    "Yxs2Hg1/h0KUKKoF5SqEGQ=="),
}

# ── MCX credentials ────────────────────────────────────────────────────────
MCX_SFTP_BASE = os.getenv("MCX_SFTP_HOST", "https://sftp.mcxindia.com")
MCX_USERNAME  = os.getenv("MCX_USERNAME", "56720")
MCX_PASSWORDS = [p.strip() for p in os.getenv("MCX_PASSWORDS", "M56720@123,M56720@789").split(",") if p.strip()]

# ── EPI credentials (reuse KORP keys) ──────────────────────────────────────
EPI_BASE       = os.getenv("KORP_BASE_URL",   "http://boapi.mastertrust.co.in:15000")
EPI_USERNAME   = os.getenv("KORP_USERNAME")
EPI_PASSWORD   = os.getenv("KORP_PASSWORD")
EPI_CLIENT_IP  = os.getenv("KORP_CLIENT_IP")
EPI_CLIENT_MAC = os.getenv("KORP_CLIENT_MAC")

# ── NCDEX credentials (reuse existing keys) ────────────────────────────────
NCDEX_USERNAME = os.getenv("NCDEX_USERNAME")
NCDEX_PASSWORD = os.getenv("NCDEX_PASSWORD")
NCDEX_SECURITY_ANSWER = os.getenv("NCDEX_SECURITY_ANSWER", "a")

# ── NSE Holidays ───────────────────────────────────────────────────────────
NSE_HOLIDAYS = {
    "2026-01-26", "2025-03-14", "2025-04-18", "2025-05-01",
    "2025-08-15", "2025-10-02", "2025-11-01",
}

# ── Peak slots ──────────────────────────────────────────────────────────────
COMM_PEAK_SLOTS = ["1100", "1230", "1430", "1530", "1700", "1900", "2030", "2230"]
CM_PEAK_SLOTS   = ["1100", "1230", "1430", "1530"]
FO_PEAK_SLOTS   = ["1100", "1230", "1430", "1530"]

# Module-level dir vars (set per call in run_files_downloader_70)
CM_DIR = FO_DIR = CO_DIR = CD_DIR = MCX_DIR = NCDEX_DIR = ""
mcx_session = requests.Session()


def _make_dirs():
    for d in [OUTPUT_DIR, SA_CM_DIR, SA_FO_DIR, MCX_INTRA_SAR_DIR,
              RAJEEV_DIR, CAPTCHA_DIR, MCX_EODSAR_DIR, NCDEX_EODSAR_DIR]:
        os.makedirs(d, exist_ok=True)


def is_extracted_present(filename_gz):
    filename = filename_gz.replace(".gz", "")
    return any(os.path.exists(os.path.join(d, filename))
               for d in [CM_DIR, FO_DIR, CO_DIR, CD_DIR, MCX_DIR] if d)


def copy_to_eodsar_folder(file_path):
    filename = os.path.basename(file_path).upper()
    # ===== CHANGED (ported from the manual script): added MCX_MARGIN_ — it was
    # being downloaded but never reached either EODSAR folder because it didn't
    # match any prefix below. =====
    if (filename.startswith("X_CC02_") or filename.startswith("C_CC02_") or
            filename.startswith("O_CC02_") or filename.startswith("F_CC02_") or
            "01292_MGN_" in filename or
            filename.startswith("MCX_MARGIN_")):
        eodsar_dir = os.path.join(os.path.dirname(CM_DIR), "MCX & NCDEX EODSAR")
        os.makedirs(eodsar_dir, exist_ok=True)
        base_name = os.path.basename(file_path)
        shutil.copy2(file_path, os.path.join(eodsar_dir, base_name))
        # ===== CHANGED (ported from the manual script): no more MCX-vs-NCDEX
        # split on "01292". Every file that qualifies above goes into BOTH
        # MCX_EODSAR and NCDEX_EODSAR. =====
        shutil.copy2(file_path, os.path.join(MCX_EODSAR_DIR, base_name))
        shutil.copy2(file_path, os.path.join(NCDEX_EODSAR_DIR, base_name))
        print(f"📁 EODSAR COPY (MCX + NCDEX): {filename}")


def copy_to_rajeev_folder(file_path):
    filename = os.path.basename(file_path)
    if any(x in filename.upper() for x in ["MG13", "MGN", "01292", "MARGIN_NCL_CM",
                                             "MARGIN_NCL_CO", "MARGIN_NCL_FO",
                                             "MCX_PEAKMARGIN", "NCCL_MARGIN_REP"]):
        shutil.copy2(file_path, os.path.join(RAJEEV_DIR, filename))
        print(f"📂 Copied to Rajeev folder: {filename}")


def handle_sa_file(extracted_file_path):
    filename = os.path.basename(extracted_file_path)
    # ===== CHANGED (ported from the manual script): added "O_SA" so the
    # O_SA01_P_06436_*_i01-i08.lis (NSE COMM) files also reach MCX_INTRA_SAR —
    # MCX_INTRSAR_final.py reads these for the NSE COMM DEPOSIT column and was
    # getting nothing because this check only recognized C_SA/F_SA. =====
    if filename.startswith(("C_SA", "F_SA", "O_SA")):
        shutil.copy2(extracted_file_path, os.path.join(SA_CM_DIR, filename))
        shutil.copy2(extracted_file_path, os.path.join(SA_FO_DIR, filename))
        shutil.copy2(extracted_file_path, os.path.join(MCX_INTRA_SAR_DIR, filename))
        print(f"📦 SA file copied to SA folders + MCX_INTRA_SAR: {filename}")


def organize_base_folder(file_path):
    filename = os.path.basename(file_path)
    if filename.startswith("Margin_"):
        parts = filename.split("_")
        segment = parts[2]
        if segment == "CM":   target = CM_DIR
        elif segment == "FO": target = FO_DIR
        elif segment == "CO": target = CO_DIR
        else: return
    elif filename.startswith("C_"): target = CM_DIR
    elif filename.startswith("F_"): target = FO_DIR
    elif filename.startswith("O_"): target = CO_DIR
    elif filename.startswith("X_"): target = CD_DIR
    # ===== ADDED: Delivery_NCL_CM_EquityT1_... downloads fine (segment="CM"
    # in the call) but never matched any prefix above, so it was left sitting
    # in the base backup folder instead of CM_DIR — "Could not find moved
    # file" plus a re-download on every single run (is_extracted_present only
    # checks CM_DIR/FO_DIR/CO_DIR/CD_DIR/MCX_DIR, never the base folder).
    elif filename.startswith("Delivery_"): target = CM_DIR
    else: return
    shutil.move(file_path, os.path.join(target, filename))
    print(f"📂 Moved → {target}\\{filename}")


def get_token():
    while True:
        try:
            r = requests.post(LOGIN_URL, json=CREDENTIALS, timeout=30)
            r.raise_for_status()
            data = r.json()
            token = data.get("token")
            if token:
                print("🔑 Login successful"); return token
            print("❌ Token missing, retrying...")
        except Exception as e:
            print(f"⚠ Login error: {e} → retrying in 3 sec")
        time.sleep(3)


def safe_download_and_extract(segment, folder, filename, token):
    gz_path = os.path.join(os.path.dirname(CM_DIR), filename)
    out_path = gz_path.replace(".gz", "")
    if is_extracted_present(filename):
        print(f"⏭️ Already exists, skipping: {filename}"); return token
    headers = {"Authorization": f"Bearer {token}"}
    params = {"segment": segment, "folderPath": folder, "filename": filename}
    attempt = 1
    while True:
        try:
            print(f"⬇️ Downloading {filename} (try {attempt})")
            r = requests.get(DOWNLOAD_URL, headers=headers, params=params, timeout=10)
            if r.status_code == 401:
                print("🚫 401 → Refreshing token")
                token = get_token(); headers["Authorization"] = f"Bearer {token}"; attempt += 1; continue
            if r.status_code == 504:
                print("⏳ 504 → Retrying"); time.sleep(3); attempt += 1; continue
            if r.status_code == 404:
                print(f"❌ File not found: {filename}"); return token
            r.raise_for_status()
            with open(gz_path, "wb") as f: f.write(r.content)
            with gzip.open(gz_path, "rb") as f_in:
                with open(out_path, "wb") as f_out: f_out.write(f_in.read())
            print(f"📂 Extracted: {out_path}")
            handle_sa_file(out_path)
            copy_to_rajeev_folder(out_path)
            if os.path.exists(out_path):
                organize_base_folder(out_path)
                filename_only = os.path.basename(out_path)
                new_path = next((os.path.join(d, filename_only) for d in [CM_DIR, FO_DIR, CO_DIR, CD_DIR]
                                 if os.path.exists(os.path.join(d, filename_only))), None)
                if new_path: copy_to_eodsar_folder(new_path)
            os.remove(gz_path); return token
        except Exception as e:
            print(f"🌐 Network error: {e} → retrying"); time.sleep(3); attempt += 1


def encrypt_password(password, hdn_value):
    key_str, iv_str = hdn_value.split(":")
    cipher = AES.new(key_str.encode("utf-8"), AES.MODE_CBC, iv_str.encode("utf-8"))
    return base64.b64encode(cipher.encrypt(pad(password.encode("utf-8"), 16))).decode()


def detect_captcha_text(image_path):
    img = cv2.imread(image_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 11, 2)
    kernel = np.ones((2,2), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    thresh = cv2.resize(thresh, None, fx=2, fy=2)
    custom_config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    text = pytesseract.image_to_string(thresh, config=custom_config)
    return ''.join(c for c in text.strip().replace(" ", "").upper() if c.isalnum())[:6]


def captcha_popup(image_path):
    detected = detect_captcha_text(image_path)
    root = tk.Tk(); root.title("Captcha Verification"); root.geometry("520x320")
    img = Image.open(image_path); img = img.resize((300, 100)); tk_img = ImageTk.PhotoImage(img)
    tk.Label(root, image=tk_img).pack(pady=10)
    tk.Label(root, text=f"Detected CAPTCHA: {detected.upper()}", font=("Arial", 14, "bold"), fg="blue").pack(pady=5)
    tk.Label(root, text="Press OK if correct or edit below", font=("Arial", 10)).pack()
    entry = tk.Entry(root, font=("Arial", 18), justify="center")
    entry.delete(0, tk.END); entry.insert(0, detected.upper()); entry.pack(pady=10); entry.focus_set()
    result = {"value": None}
    def submit(event=None):
        val = entry.get().strip()
        if len(val) not in [5, 6]: print("⚠️ Captcha must be 5 or 6 characters"); return
        result["value"] = val; root.destroy()
    tk.Button(root, text="OK (Confirm)", command=submit, width=15).pack(pady=10)
    root.bind('<Return>', submit); root.mainloop()
    return result["value"]


def mcx_login():
    for pwd in MCX_PASSWORDS:
        print(f"🔐 Trying MCX: {pwd}")
        try:
            res = mcx_session.post(
                MCX_SFTP_BASE + f"/Web%20Client/Login.xml?Command=Login&Sync={int(time.time()*1000)}",
                headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded",
                         "Origin": MCX_SFTP_BASE, "Referer": MCX_SFTP_BASE + "/?command=logout"},
                data={"user": MCX_USERNAME, "pword": pwd, "language": "en,US"},
                verify=False, timeout=15)
            if "successful" in res.text.lower():
                print("✅ MCX Login success"); return
        except Exception as e:
            print("⚠️", e)
    raise Exception("❌ MCX Login failed")


def mcx_init_session():
    res = mcx_session.get(MCX_SFTP_BASE + "/WebClientApi/SessionInfo.json", verify=False, timeout=30)
    _mcx_info = res.json()
    _mcx_data = _mcx_info.get("data") if isinstance(_mcx_info, dict) else None
    if not _mcx_data:
        raise RuntimeError("MCX session info did not return the expected data — login/session likely failed.")
    csrf = _mcx_data["csrfToken"]
    mcx_session.get(MCX_SFTP_BASE + "/WebClientApi/UserInfo.json", headers={"X-Csrf-Token": csrf}, verify=False, timeout=30)
    mcx_session.get(MCX_SFTP_BASE + "/WebClientApi/Mfa.json", headers={"X-Csrf-Token": csrf}, verify=False, timeout=30)
    mcx_session.get(MCX_SFTP_BASE + "/", params={"Command": "List", "Dir": "~", "ListFile": "List.json", "NewWebClient": "1"},
                    headers={"X-Csrf-Token": csrf}, verify=False, timeout=30)
    return csrf


def mcx_get_all_peak_files(folder, csrf):
    res = mcx_session.get(MCX_SFTP_BASE + "/", params={"Command": "List", "Dir": f"/{folder}", "ListFile": "List.json", "NewWebClient": "1"},
                          headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "X-Csrf-Token": csrf,
                                   "X-Requested-With": "XMLHttpRequest", "Referer": MCX_SFTP_BASE + f"/WebClientNew/Browser/%2F{folder}"},
                          verify=False, timeout=30)
    files = [f.get("name") for f in res.json().get("files", []) if f.get("name") and "PeakMargin" in f.get("name", "")]
    if not files: raise Exception("❌ No PeakMargin files found")
    files.sort(); print("\n📂 ALL Peak Files:"); [print("   ", f) for f in files]; return files


def mcx_download(folder, filename, csrf):
    file_path = os.path.join(MCX_DIR, filename)
    if os.path.exists(file_path): print(f"⏭️ MCX already exists, skipping: {filename}"); return
    res = mcx_session.get(MCX_SFTP_BASE + "/", params={"Command": "Download", "File": f"/{folder}/{filename}", "CsrfToken": csrf},
                          headers={"User-Agent": "Mozilla/5.0", "X-Csrf-Token": csrf}, verify=False, timeout=30, stream=True)
    with open(file_path, "wb") as f:
        for chunk in res.iter_content(8192): f.write(chunk)
    print(f"✅ Downloaded: {filename}")
    copy_to_rajeev_folder(file_path); copy_to_eodsar_folder(file_path)
    intra_target = os.path.join(MCX_INTRA_SAR_DIR, filename)
    shutil.copy2(file_path, intra_target)
    if "MCX_INTRASAR" in filename.upper() or "MCX_MARGIN" in filename.upper():
        print(f"📦 MCX file kept ONLY in MCX_INTRA_SAR: {filename}")
    else:
        shutil.copy2(file_path, os.path.join(SA_CM_DIR, filename))
        shutil.copy2(file_path, os.path.join(SA_FO_DIR, filename))
        print(f"📦 MCX copied to SA folders + MCX_INTRA_SAR: {filename}")


def are_all_mcx_files_present(trade_date):
    mcx_date = trade_date.strftime("%Y%m%d")
    for i in range(1, 9):
        if not os.path.exists(os.path.join(MCX_DIR, f"MCX_PeakMargin{MCX_USERNAME}_{mcx_date}_{i:02d}.csv")):
            return False
    for fname in [f"MCX_INTRASAR_{MCX_USERNAME}_{mcx_date}.csv",
                  f"MCX_MARGIN_{MCX_USERNAME}_{mcx_date}.csv",
                  f"MCX_EODSAR_{MCX_USERNAME}_{mcx_date}.csv"]:
        if not os.path.exists(os.path.join(MCX_DIR, fname)): return False
    return True


def is_ncdex_file_present(trade_date):
    date_str = trade_date.strftime("%d%m%Y")
    return any(date_str in f and f.endswith(".CSV") for f in os.listdir(NCDEX_DIR))


def _attr_or_raise(soup, *args, attr="value", **kwargs):
    """soup.find(...)[attr], but raises a clear, actionable error instead of the
    cryptic 'NoneType object is not subscriptable' when the element is missing.
    On these exchange login pages a missing element almost always means an expired
    session, a captcha, or that the site markup changed."""
    el = soup.find(*args, **kwargs)
    if el is None:
        what = kwargs.get("id") or (args[0] if args else kwargs)
        raise RuntimeError(
            f"Login page element '{what}' not found — the exchange did not return "
            f"the expected login form (likely expired session, captcha, or a site "
            f"markup change). Re-run after refreshing the session; if it persists the "
            f"page layout changed.")
    return el[attr]


class _NcdexCaptchaUnavailable(RuntimeError):
    """NCDEX gave no usable captcha image — the whole login is started again from the beginning."""


def download_ncdex_file(trade_date):
    """Whole NCDEX login + download. A broken connection or a missing captcha image restarts it from the very
    beginning (new session, new login page) after 5 s, up to 5 times. A wrong password is NOT retried."""
    from core import net_retry
    last = None
    for n in range(1, 6):
        try:
            return _download_ncdex_file_once(trade_date)
        except Exception as e:
            if not (isinstance(e, _NcdexCaptchaUnavailable) or net_retry.is_network_error(e)) or n >= 5:
                raise
            last = e
            print(f"⚠️ NCDEX: {type(e).__name__}: {str(e)[:140]} — starting the whole login again in 5s "
                  f"(restart {n}/4)...", flush=True)
            time.sleep(5)
    raise last


def _download_ncdex_file_once(trade_date):
    if is_ncdex_file_present(trade_date):
        print("⏭️ NCDEX file already exists — skipping"); return
    print("\n📦 STARTING NCDEX DOWNLOAD...\n")
    today_str = trade_date.strftime("%d-%b-%Y")
    ncdex_session = requests.Session(); ncdex_session.cookies.clear()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.5", "Content-Type": "application/x-www-form-urlencoded",
               "Origin": "https://ncfe.ncdex.com", "Referer": "https://ncfe.ncdex.com/Login/NCFEMain.aspx", "Connection": "keep-alive"}
    login_url = "https://ncfe.ncdex.com/Login/NCFEMain.aspx"

    def get_login_tokens(sess):
        resp = sess.get(login_url, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")
        return (_attr_or_raise(soup, id="__VIEWSTATE"), _attr_or_raise(soup, id="__EVENTVALIDATION"),
                _attr_or_raise(soup, id="__VIEWSTATEGENERATOR"),
                (soup.find("input", {"name": "__RequestVerificationToken"}) or {}).get("value", ""),
                _attr_or_raise(soup, id="hdn"),
                _attr_or_raise(soup, "img", src=lambda x: x and "Captcha.aspx" in x, attr="src"))

    viewstate, eventvalidation, viewstategenerator, token, hdn_value, captcha_src = get_login_tokens(ncdex_session)
    captcha_url = "https://ncfe.ncdex.com/Login/" + urllib.parse.quote(captcha_src, safe=":/?=&")
    # NCDEX's captcha endpoint sometimes returns a 404/HTML error page instead of
    # the actual image (site hiccup) — saving that as captcha.png and handing it
    # to captcha_popup() (which runs it through cv2.imread + cv2.cvtColor for the
    # OCR pre-fill) crashes with a cv2 "!_src.empty()" assertion because cv2.imread
    # silently returns None for a non-image file. Validate before accepting it,
    # retrying the download a few times instead of crashing the whole run.
    captcha_resp = None
    for _cap_attempt in range(1, 4):                          # captcha download: 3 tries
        captcha_resp = ncdex_session.get(captcha_url, headers=headers)
        # Sniff the body instead of trusting Content-Type — NCDEX doesn't always
        # send an "image/*" header for a real captcha, and requiring it rejected
        # good downloads. Only reject bodies that look like an HTML/XML error page.
        _body = captcha_resp.content
        _looks_like_html = _body.lstrip()[:20].lower().startswith(b"<")
        if captcha_resp.status_code == 200 and len(_body) > 100 and not _looks_like_html:
            break
        print(f"⚠️ Captcha download looked invalid (status {captcha_resp.status_code}, "
              f"attempt {_cap_attempt}/3) — retrying in 2s...")
        time.sleep(2)
    else:
        raise _NcdexCaptchaUnavailable("NCDEX captcha image could not be downloaded in 3 attempts "
                                       "(kept getting a non-image response — the site was likely down/erroring)")
    captcha_path = os.path.join(CAPTCHA_DIR, "captcha.png")
    with open(captcha_path, "wb") as f: f.write(captcha_resp.content)
    captcha_text = captcha_popup(captcha_path)
    enc_password = encrypt_password(NCDEX_PASSWORD, hdn_value)
    hdnCaptchaVal = urllib.parse.unquote(captcha_src.split("cpt=")[-1])

    while True:
        payload = {"__EVENTTARGET": "", "__EVENTARGUMENT": "", "__VIEWSTATE": viewstate,
                   "__VIEWSTATEGENERATOR": viewstategenerator, "__EVENTVALIDATION": eventvalidation,
                   "__RequestVerificationToken": token, "txtUserNm": NCDEX_USERNAME,
                   "txtPassword": enc_password, "txtCaptcha": captcha_text,
                   "hdn": "", "hdnCaptchaVal": hdnCaptchaVal, "hdnEnabledCaptcha": "1",
                   "hdnUserAgent": "", "hidProId": "", "hidFile": "",
                   "imgBtnLogin.x": "18", "imgBtnLogin.y": "1"}
        login_resp = ncdex_session.post(login_url, data=payload, headers=headers, allow_redirects=False)
        soup2 = BeautifulSoup(login_resp.text, "html.parser")
        error_box = soup2.find(id="lblMsgBox")
        error_msg = error_box.text.strip().lower() if error_box else ""
        if login_resp.status_code == 302: print("✅ NCDEX Login success"); break
        elif "locked" in error_msg: print("🔴 USER LOCKED — STOPPING"); return
        viewstate, eventvalidation, viewstategenerator, token, hdn_value, captcha_src = get_login_tokens(ncdex_session)
        captcha_url = "https://ncfe.ncdex.com/Login/" + urllib.parse.quote(captcha_src, safe=":/?=&")
        with open(captcha_path, "wb") as f: f.write(ncdex_session.get(captcha_url, headers=headers).content)
        captcha_text = captcha_popup(captcha_path)
        enc_password = encrypt_password(NCDEX_PASSWORD, hdn_value)
        hdnCaptchaVal = urllib.parse.unquote(captcha_src.split("cpt=")[-1])

    if "SecurityQs" in str(login_resp.headers.get("Location", "")):
        sec_url = "https://ncfe.ncdex.com" + login_resp.headers["Location"]
        sec_page = ncdex_session.get(sec_url, headers=headers)
        soup3 = BeautifulSoup(sec_page.text, "html.parser")
        sec_payload = {"__VIEWSTATE": _attr_or_raise(soup3, id="__VIEWSTATE"),
                       "__VIEWSTATEGENERATOR": _attr_or_raise(soup3, id="__VIEWSTATEGENERATOR"),
                       "__EVENTVALIDATION": _attr_or_raise(soup3, id="__EVENTVALIDATION"),
                       "ctl00$ContentPlaceHolderCentre$txtanswers0": NCDEX_SECURITY_ANSWER,
                       "ctl00$ContentPlaceHolderCentre$imgBtnSubmit.x": "9",
                       "ctl00$ContentPlaceHolderCentre$imgBtnSubmit.y": "14",
                       "ctl00$ContentPlaceHolderCentre$hdnType": "ACTIVE",
                       "ctl00$ContentPlaceHolderCentre$hidDate": "",
                       "ctl00$ContentPlaceHolderCentre$hdnCounter": "",
                       "ctl00$ContentPlaceHolderCentre$Hidden1": ""}
        sec_resp = ncdex_session.post(sec_url, data=sec_payload, headers=headers, allow_redirects=False)
        print("✅ FULL LOGIN SUCCESS" if sec_resp.status_code == 302 else "❌ Security step failed")

    ncdex_session.get("https://ncfe.ncdex.com/Common/Dashboard.aspx", headers=headers)

    margin_url = "https://ncfe.ncdex.com/TradeListener/ViewUpdateMargins.aspx"
    margin_page = ncdex_session.get(margin_url, headers=headers)
    soup4 = BeautifulSoup(margin_page.text, "html.parser")
    token_input = soup4.find("input", {"name": "__RequestVerificationToken"})
    margin_payload = {"__EVENTTARGET": "ctl00$ContentPlaceHolderCentre$imgBtnDownload", "__EVENTARGUMENT": "",
                      "__VIEWSTATE": _attr_or_raise(soup4, id="__VIEWSTATE"),
                      "__VIEWSTATEGENERATOR": _attr_or_raise(soup4, id="__VIEWSTATEGENERATOR"),
                      "__EVENTVALIDATION": _attr_or_raise(soup4, id="__EVENTVALIDATION"),
                      "ctl00$ContentPlaceHolderCentre$txtDate": today_str,
                      "ctl00$ContentPlaceHolderCentre$ddlDateType": "BD",
                      "ctl00$ContentPlaceHolderCentre$txtPSADate": "",
                      "ctl00$ContentPlaceHolderCentre$hdnUpldPath": "",
                      "ctl00$ContentPlaceHolderCentre$hdnUpldPathPSA": "",
                      "ctl00$ContentPlaceHolderCentre$ddlFilterBy": "0",
                      "ctl00$txtSubject": "Problem in: Client Margin Report",
                      "ctl00$txtProbDesc": ""}
    if token_input: margin_payload["__RequestVerificationToken"] = token_input["value"]
    margin_headers = headers.copy(); margin_headers.pop("Content-Type", None)
    margin_headers["Referer"] = margin_url; margin_headers["Origin"] = "https://ncfe.ncdex.com"
    margin_resp = ncdex_session.post(margin_url, files={k: (None, v) for k, v in margin_payload.items()}, headers=margin_headers)
    content_disp = margin_resp.headers.get("Content-Disposition", "")
    if "filename=" in content_disp:
        file_name = re.findall(r'filename="?(.+?)"?$', content_disp)[0]
        file_path = os.path.join(NCDEX_DIR, file_name)
        with open(file_path, "wb") as f: f.write(margin_resp.content)
        print(f"✅ NCDEX file saved: {file_path}")
        copy_to_eodsar_folder(file_path)
        for d in [SA_CM_DIR, SA_FO_DIR]:
            shutil.copy2(file_path, os.path.join(d, file_name))
        copy_to_rajeev_folder(file_path)
        shutil.copy2(file_path, os.path.join(MCX_INTRA_SAR_DIR, file_name))
    else:
        print("❌ NCDEX download failed")


def is_epi_file_present():
    return os.path.exists(os.path.join(os.path.dirname(CM_DIR), "EPI.xlsx"))


def get_financial_year(dt):
    if dt.month >= 4: return f"{dt.year}-{dt.year+1}"
    return f"{dt.year-1}-{dt.year}"


def download_epi_file(trade_date):
    if is_epi_file_present(): print("⏭️ EPI file already exists — skipping"); return
    print("\n📦 STARTING EPI DOWNLOAD...\n")
    epi_session = requests.Session()
    r = epi_session.get(f"{EPI_BASE}/api/Common/SessionKey/Get", timeout=30)
    session_key = r.text.replace('"', '')
    cipher = AES.new(session_key.encode(), AES.MODE_CBC, session_key.encode())
    encrypted_password = base64.b64encode(cipher.encrypt(pad(EPI_PASSWORD.encode(), 16))).decode()
    payload = {"CallFrom": "FINKORP", "UserName": EPI_USERNAME, "Password": encrypted_password,
               "Grant_type": "password", "ClientIP": EPI_CLIENT_IP, "ClientMAC": EPI_CLIENT_MAC}
    login = epi_session.post(f"{EPI_BASE}/api/token", data=payload,
                             headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    _epi_login = login.json()
    if not isinstance(_epi_login, dict) or "access_token" not in _epi_login:
        raise RuntimeError("EPI login did not return an access_token — credentials/session likely failed.")
    token = _epi_login["access_token"]
    print("🔑 EPI Login Successful")
    financial_year = get_financial_year(trade_date)
    r = epi_session.get(f"{EPI_BASE}/api/Vouchers/EarlyPayinMargin/Get",
                        headers={"Authorization": f"Bearer {token}", "FIRMID": "1001", "FINANCIALYEAR": financial_year},
                        params={"Exchange": "NSE", "TradeDate": trade_date.strftime("%Y-%m-%d")}, timeout=60)
    if r.status_code != 200: print("❌ EPI API Error:", r.text); return
    data = r.json()
    if not data: print("❌ No EPI data received"); return
    df = pd.DataFrame(data)
    df.drop(columns=["FirmID", "Segment"], errors="ignore", inplace=True)
    df.rename(columns={"Exchange": "Exchange", "TradeDate": "Trade Date", "UCICode": "UCI Code",
                       "AccountID": "Account ID", "AccountName": "Account Name",
                       "SettlementNo": "Settlement No", "SettlementType": "Settlement Type",
                       "ScripCode": "Scrip Code", "ScripName": "Scrip Name", "ISIN": "ISIN",
                       "Symbol": "Symbol", "Series": "Series", "RequestedQuantity": "Requested Quantity",
                       "AllocatedQuantity": "Allocated Quantity", "Rate": "Rate", "Amount": "Amount",
                       "VarPer": "Var %", "FinalAmount": "Final Amount"}, inplace=True)
    if "Trade Date" in df.columns:
        df["Trade Date"] = pd.to_datetime(df["Trade Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    column_order = ["Exchange", "Trade Date", "UCI Code", "Account ID", "Account Name",
                    "Settlement No", "Settlement Type", "Scrip Code", "Scrip Name",
                    "ISIN", "Symbol", "Series", "Requested Quantity", "Allocated Quantity",
                    "Rate", "Amount", "Var %", "Final Amount"]
    df = df[[col for col in column_order if col in df.columns]]
    filename = "EPI.xlsx"
    file_path = os.path.join(os.path.dirname(CM_DIR), filename)
    with pd.ExcelWriter(file_path, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
        worksheet = writer.sheets["Sheet1"]
        for i, col in enumerate(df.columns):
            worksheet.set_column(i, i, max(df[col].astype(str).map(len).max(), len(col)) + 2)
    print(f"✅ EPI File Saved: {file_path}")
    for d in [SA_CM_DIR, SA_FO_DIR]:
        shutil.copy2(file_path, os.path.join(d, filename))
    print(f"📦 EPI copied to BOTH SA folders: {filename}")


# =====================================================
# NCDEX PEAK FILES 01-08   (requests based - NO SELENIUM)
# =====================================================
# Ported verbatim from the manual script (new.py), which had this and the
# console did not — the console's 70-files run was silently ending after the
# EPI step with the eight NCCL_MARGIN_REP peak files never downloaded.
#
# Two notes vs the older Selenium version this replaced:
#   1. No chromedriver. Pure requests + BeautifulSoup, same ASP.NET
#      postback flow as ncdex.py.
#   2. Downloads snapshots 01-08 instead of 01-05. The exchange does
#      publish 06/07/08.
#
# Downloaded files are distributed to exactly the same folders the
# Selenium version used: NCDEX_DIR, SA_CM_DIR, SA_FO_DIR, Rajeev,
# EODSAR and MCX_INTRA_SAR.

NCDEX_WEB_BASE  = "https://webextranet.ncdex.com"
NCDEX_WEB_LOGIN = NCDEX_WEB_BASE + "/Login.aspx"
NCDEX_WEB_DNLD  = NCDEX_WEB_BASE + "/LoginSuccessDnld.aspx"

NCDEX_PEAK_SNAPSHOTS = ["01", "02", "03", "04", "05", "06", "07", "08"]
NCDEX_MAX_LOGIN_TRIES = 5

_NCDEX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)
_NCDEX_HEADS = {
    "User-Agent": _NCDEX_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_NCDEX_POST_H = {
    **_NCDEX_HEADS,
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "null",
}

_NCDEX_POSTBACK_RE = re.compile(r"__doPostBack\('([^']+)'", re.I)


def _ncdex_aes(password, hdn):
    """AES-128-CBC encrypt the password using the KEY:IV from the hdn field."""
    k, iv = hdn.split(":", 1)
    cipher = AES.new(k.encode(), AES.MODE_CBC, iv.encode())
    return base64.b64encode(cipher.encrypt(pad(password.encode(), 16))).decode()


def _ncdex_val(soup, name):
    tag = soup.find("input", {"id": name}) or soup.find("input", {"name": name})
    return (tag.get("value") or "") if tag else ""


def _ncdex_field_name(soup, elem_id, default=None):
    """ASP.NET posts by `name`, often ctl00$...$id. Resolve id -> name."""
    tag = soup.find(attrs={"id": elem_id})
    if tag and tag.get("name"):
        return tag["name"]
    return default or elem_id


def _ncdex_hidden(soup):
    keys = [
        "_AntiForgeryToken", "__ANTIFORGERY",
        "__EVENTTARGET", "__EVENTARGUMENT",
        "__VIEWSTATE", "__VIEWSTATEGENERATOR",
        "__VIEWSTATEENCRYPTED", "__EVENTVALIDATION",
        "__RequestVerificationToken",
    ]
    d = {k: _ncdex_val(soup, k) for k in keys}
    d.setdefault("__EVENTTARGET", "")
    d.setdefault("__EVENTARGUMENT", "")
    d.setdefault("__VIEWSTATEENCRYPTED", "")
    return d


def _ncdex_find_link(soup, text):
    for a in soup.find_all("a", href=True):
        if text.lower() in a.get_text(strip=True).lower():
            m = _NCDEX_POSTBACK_RE.search(a["href"])
            if m:
                return m.group(1)
    return None


def _ncdex_all_file_links(soup):
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True)
        m = _NCDEX_POSTBACK_RE.search(a["href"])
        if m and txt and txt not in seen:
            seen.add(txt)
            out.append((txt, m.group(1)))
    return out


def _ncdex_nav(sess, soup, target):
    data = {**_ncdex_hidden(soup), "__EVENTTARGET": target}
    r = sess.post(NCDEX_WEB_DNLD, data=data,
                  headers={**_NCDEX_POST_H, "Referer": NCDEX_WEB_DNLD}, timeout=45)
    return BeautifulSoup(r.text, "html.parser")


def _ncdex_looks_like_csv(content):
    """A timed-out session returns an HTML error page with HTTP 200.
    Without this guard that page gets saved as a perfectly valid-looking .csv."""
    if len(content) < 40:
        return False
    head = content[:400].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html") or b"<head" in head:
        return False
    return True


def _new_auto_session():
    """A fresh automatic-captcha session (2Captcha) for one NCDEX login; None if the solver cannot be loaded."""
    try:
        from core import captcha_solver
        return captcha_solver.AutoSession("ncdex")
    except Exception as exc:          # auto-fill is optional: the manual popup must keep working without it
        print(f"⚠️ Captcha auto-fill unavailable ({exc}) — manual popup only", flush=True)
        return None


def _ncdex_captcha(image_bytes, auto=None):
    """Answer the NCDEX captcha: 2Captcha first (45 s per try, 5 tries), then the existing popup."""
    if auto is not None:
        try:
            text = auto.solve(image_bytes)
        except Exception as exc:      # never let auto-fill break a login
            print(f"⚠️ Captcha auto-fill error ({type(exc).__name__}: {exc}) — manual popup", flush=True)
            text = None
        if text:
            return text
        note = auto.manual_note()
        if note:
            print(f"⚠️ {note}", flush=True)
    os.makedirs(CAPTCHA_DIR, exist_ok=True)
    path = os.path.join(CAPTCHA_DIR, "captcha_ncdex_web.png")
    with open(path, "wb") as f:
        f.write(image_bytes)
    return captcha_popup(path)


def _ncdex_login(sess):
    """Login -> security question -> download landing page."""
    auto = _new_auto_session()
    # The automatic tries (max 5) come on top of the manual attempts, so the person still gets the full
    # NCDEX_MAX_LOGIN_TRIES once auto-fill has given up (unchanged limit when auto-fill is off).
    login_cap = NCDEX_MAX_LOGIN_TRIES + (auto.max_tries if (auto is not None and auto.enabled_fn()) else 0)
    for attempt in range(1, login_cap + 1):
        print(f"🔐 NCDEX web login attempt {attempt}")

        r = sess.get(NCDEX_WEB_LOGIN, headers=_NCDEX_HEADS, timeout=30)

        # Bot-fingerprint challenge, if the site asks for it
        if "__verify/fp" in r.text:
            sess.post(
                NCDEX_WEB_BASE + "/__verify/fp?url=%2FLogin.aspx",
                json={"version": 1, "src": "", "userAgent": _NCDEX_UA, "checks": {}},
                headers=_NCDEX_HEADS, timeout=15,
            )
            r = sess.get(NCDEX_WEB_LOGIN, headers=_NCDEX_HEADS, timeout=30)

        soup = BeautifulSoup(r.text, "html.parser")
        hdn_raw = _ncdex_val(soup, "hdn")
        cap_val = _ncdex_val(soup, "hdnCaptchaVal")

        if not hdn_raw or ":" not in hdn_raw:
            print("⚠️ Could not read AES key from NCDEX login page")
            time.sleep(2)
            continue

        cap_r = None
        for _c in range(1, 4):                                # captcha download: 3 tries, then start the login again
            cap_r = sess.get(
                f"{NCDEX_WEB_BASE}/Captcha2.aspx?cpt={cap_val}&t={int(time.time()*1000)}",
                headers=_NCDEX_HEADS, timeout=20,
            )
            _b = cap_r.content
            if cap_r.status_code == 200 and len(_b) > 100 and not _b.lstrip()[:20].lower().startswith(b"<"):
                break
            print(f"⚠️ NCDEX captcha download looked invalid (status {cap_r.status_code}, attempt {_c}/3)")
            time.sleep(2)
        else:
            print("🔁 No captcha image after 3 tries — starting the login again from the beginning in 5s")
            time.sleep(5)
            continue
        captcha = _ncdex_captcha(cap_r.content, auto)
        if not captcha:
            print("❌ Captcha cancelled")
            return False

        payload = {
            **_ncdex_hidden(soup),
            "hdn": "",
            "hdnCaptchaVal": cap_val,
            "txtUserNm": NCDEX_USERNAME,
            "txtPassword": _ncdex_aes(NCDEX_PASSWORD, hdn_raw),
            "txtCaptcha": captcha,
            "cmdSubmit": "Login",
        }
        lr = sess.post(NCDEX_WEB_LOGIN, data=payload,
                       headers={"User-Agent": _NCDEX_UA},
                       allow_redirects=False, timeout=30)
        redir = lr.headers.get("Location", "")

        if "AuthenticateQs.aspx" not in redir:
            err = BeautifulSoup(lr.text, "html.parser")
            msg = err.find(id="lblMsgBox") or err.find(id="lblMessage")
            note = msg.get_text(strip=True) if msg else "(no message)"
            print(f"⚠️ NCDEX login rejected: {note}")
            if auto is not None:
                # A locked account is not the captcha's fault; every other rejection counts as a wrong captcha.
                (auto.accepted if "lock" in note.lower() else auto.rejected)()
            if "lock" in note.lower():
                print("🔴 ACCOUNT LOCKED — stopping NCDEX peak download")
                return False
            continue
        if auto is not None:
            auto.accepted()                      # captcha accepted: feedback to 2Captcha

        # ---- security question ----
        ar = sess.get(NCDEX_WEB_BASE + "/AuthenticateQs.aspx",
                      headers=_NCDEX_HEADS, timeout=30)
        asup = BeautifulSoup(ar.text, "html.parser")
        sr = sess.post(
            NCDEX_WEB_BASE + "/AuthenticateQs.aspx",
            data={
                **_ncdex_hidden(asup),
                "__EVENTTARGET": "btnSubmit",
                "txtanswers0": NCDEX_SECURITY_ANSWER,
                "hdnType": "ACTIVE",
                "hidDate": "",
                "hdnCounter": "",
                "Hidden1": "",
            },
            headers={**_NCDEX_POST_H,
                     "Referer": NCDEX_WEB_BASE + "/AuthenticateQs.aspx"},
            allow_redirects=False, timeout=30,
        )
        if "LoginSuccessDnld.aspx" not in sr.headers.get("Location", ""):
            print("❌ NCDEX security answer rejected")
            return False

        print("✅ NCDEX web login successful")
        return True

    print("❌ NCDEX login: out of attempts")
    return False


def _ncdex_open_dnld(sess):
    """LoginSuccessDnld.aspx -> Reports -> dnld. Returns soup or None."""
    dr = sess.get(NCDEX_WEB_DNLD, headers=_NCDEX_HEADS, timeout=30)
    soup = BeautifulSoup(dr.text, "html.parser")

    tgt = _ncdex_find_link(soup, "reports")
    if not tgt:
        print("❌ 'Reports' folder not found")
        return None
    soup = _ncdex_nav(sess, soup, tgt)

    tgt = _ncdex_find_link(soup, "dnld")
    if not tgt:
        print("❌ 'dnld' subfolder not found")
        return None
    return _ncdex_nav(sess, soup, tgt)


def _ncdex_apply_date(sess, soup, trade_date):
    """Re-post the date filter. Returns the file-listing soup, or None."""
    if not soup or not soup.find(attrs={"id": "txtDate"}):
        return None

    dmy = trade_date.strftime("%d-%b-%Y")
    n_from = _ncdex_field_name(soup, "txtDate", "txtDate")
    n_to = _ncdex_field_name(soup, "txtToDate", "txtToDate")

    form = {**_ncdex_hidden(soup), n_from: dmy, n_to: dmy}

    btn = soup.find(attrs={"id": "btnShowFiles"})
    if btn is not None and (btn.get("type") or "").lower() in ("submit", "button", "image"):
        form[_ncdex_field_name(soup, "btnShowFiles", "btnShowFiles")] = btn.get("value", "Show Files")
        form["__EVENTTARGET"] = ""
    else:
        form["__EVENTTARGET"] = _ncdex_field_name(soup, "btnShowFiles", "btnShowFiles")

    r = sess.post(NCDEX_WEB_DNLD, data=form,
                  headers={**_NCDEX_POST_H, "Referer": NCDEX_WEB_DNLD}, timeout=60)
    return BeautifulSoup(r.text, "html.parser")


def _ncdex_distribute(src):
    """Same fan-out the Selenium version did for every NCDEX peak file."""
    fname = os.path.basename(src)
    try:
        shutil.copy2(src, os.path.join(SA_CM_DIR, fname))
        shutil.copy2(src, os.path.join(SA_FO_DIR, fname))
        shutil.copy2(src, os.path.join(MCX_INTRA_SAR_DIR, fname))
        copy_to_rajeev_folder(src)
        copy_to_eodsar_folder(src)
        print(f"📦 NCDEX peak distributed: {fname}")
    except Exception as e:
        print(f"⚠️ NCDEX distribute failed for {fname}: {e}")


def download_ncdex_peak_files(trade_date):
    """
    Download NCDEX peak margin files 01-08 for trade_date into NCDEX_DIR,
    then copy them to the SA / Rajeev / EODSAR / MCX_INTRA_SAR folders.

    Skips entirely if all eight are already on disk.
    Logs and continues if the exchange has not published a snapshot.
    """
    yyyymmdd = trade_date.strftime("%Y%m%d")

    expected = {
        snap: f"NCCL_MARGIN_REP_01292_{yyyymmdd}_{snap}.csv"
        for snap in NCDEX_PEAK_SNAPSHOTS
    }

    os.makedirs(NCDEX_DIR, exist_ok=True)

    missing = {
        snap: name for snap, name in expected.items()
        if not os.path.exists(os.path.join(NCDEX_DIR, name))
    }

    if not missing:
        print("⏭️ All 8 NCDEX peak files already exist — skipping FULL login")
        # still make sure the copies exist elsewhere
        for name in expected.values():
            src = os.path.join(NCDEX_DIR, name)
            if os.path.exists(src):
                _ncdex_distribute(src)
        return

    print(f"\n📦 NCDEX PEAK DOWNLOAD — need {', '.join(sorted(missing))}\n")

    sess = requests.Session()

    if not _ncdex_login(sess):
        print("❌ NCDEX peak download aborted (login failed)")
        return

    soup = _ncdex_open_dnld(sess)
    if soup is None:
        print("❌ NCDEX peak download aborted (could not open Reports\\dnld)")
        return

    listing = _ncdex_apply_date(sess, soup, trade_date)
    if listing is None:
        print("❌ No date filter on the NCDEX download page")
        return

    links = {t.strip().lower(): g for t, g in _ncdex_all_file_links(listing)}

    got, absent = [], []

    for snap in sorted(missing):
        fname = missing[snap]
        tgt = links.get(fname.lower())

        if not tgt:
            absent.append(fname)
            print(f"   ⏭️ not published by exchange: {fname}")
            continue

        try:
            dl = sess.post(
                NCDEX_WEB_DNLD,
                data={**_ncdex_hidden(listing), "__EVENTTARGET": tgt},
                headers={**_NCDEX_POST_H, "Referer": NCDEX_WEB_DNLD},
                timeout=120,
            )
            content = dl.content

            if not _ncdex_looks_like_csv(content):
                print(f"   ❌ {fname}: server returned a page, not a file")
                continue

            dest = os.path.join(NCDEX_DIR, fname)
            with open(dest, "wb") as fh:
                fh.write(content)

            print(f"   ✅ {fname}  ({len(content)/1024:.1f} KB)")
            _ncdex_distribute(dest)
            got.append(fname)

        except Exception as e:
            print(f"   ❌ {fname}: {e}")

        # Refresh the listing so the next postback has a live viewstate.
        refreshed = _ncdex_apply_date(sess, soup, trade_date)
        if refreshed is not None:
            listing = refreshed
            links = {t.strip().lower(): g for t, g in _ncdex_all_file_links(listing)}

    print(f"\n📊 NCDEX peak: {len(got)} downloaded, {len(absent)} not published")


def run_files_downloader_70(trade_date_str: str):
    """Download all 70 files (NSE + MCX + NCDEX + EPI) for the given trade date.
    trade_date_str: YYYY-MM-DD format (e.g. '2026-06-06')
    """
    global CM_DIR, FO_DIR, CO_DIR, CD_DIR, MCX_DIR, NCDEX_DIR, mcx_session
    _make_dirs()
    try:
        trade_date = datetime.strptime(trade_date_str, "%Y-%m-%d").date()
    except ValueError:
        return {"status": "error", "message": f"Invalid date format: {trade_date_str}. Use YYYY-MM-DD."}

    yyyymmdd = trade_date.strftime("%Y%m%d")
    ddmmyyyy = trade_date.strftime("%d%m%Y")
    print(f"\n📅 Processing Trading Date: {yyyymmdd}")

    # ── Set per-date dirs ─────────────────────────────────────────────────
    backup_root     = os.path.join(OUTPUT_DIR, "BACKUPFILES AUTOMATED")
    date_folder     = trade_date.strftime("%d-%m-%Y")
    backup_date_dir = os.path.join(backup_root, date_folder)
    CM_DIR    = os.path.join(backup_date_dir, "CM")
    FO_DIR    = os.path.join(backup_date_dir, "FO")
    CO_DIR    = os.path.join(backup_date_dir, "CO")
    CD_DIR    = os.path.join(backup_date_dir, "CD")
    MCX_DIR   = os.path.join(backup_date_dir, "MCX")
    NCDEX_DIR = os.path.join(backup_date_dir, "NCDEX")
    for d in [CM_DIR, FO_DIR, CO_DIR, CD_DIR, MCX_DIR, NCDEX_DIR]:
        os.makedirs(d, exist_ok=True)
    mcx_session = requests.Session()

    # ── Expected gz files ─────────────────────────────────────────────────
    expected_gz_files = []
    expected_gz_files += [f"C_CC02_06436_{ddmmyyyy}.gz", f"Delivery_NCL_CM_EquityT1_CM_06436_{yyyymmdd}_P_0000.csv.gz",  
                          f"C_06436_SEC_PLEDGE_{ddmmyyyy}_01.csv.gz", f"C_06436_SEC_PLEDGE_{ddmmyyyy}_02.csv.gz",
                          f"F_CC02_06436_{ddmmyyyy}CSV.gz", f"F_06436_SEC_PLEDGE_{ddmmyyyy}_01.csv.gz",
                          f"F_06436_SEC_PLEDGE_{ddmmyyyy}_02.csv.gz", f"F_MG13_06436_{ddmmyyyy}.lis.gz",
                          f"O_CC02_06436_{ddmmyyyy}.CSV.gz", f"O_06436_SEC_PLEDGE_{ddmmyyyy}_01.csv.gz",
                          f"O_06436_SEC_PLEDGE_{ddmmyyyy}_02.csv.gz", f"O_SA01_06436_{ddmmyyyy}.lis.gz",
                          f"O_SA04_06436_{ddmmyyyy}.lis.gz", f"X_CC02_06436_{ddmmyyyy}.CSV.gz",
                          f"X_SA01_06436_{ddmmyyyy}.lis.gz", f"C_SA01_06436_{ddmmyyyy}.lis.gz",
                          f"C_SA04_06436_{ddmmyyyy}.lis.gz", f"F_SA01_06436_{ddmmyyyy}.lis.gz",
                          f"F_SA04_06436_{ddmmyyyy}.lis.gz", f"C_MG13_06436_{ddmmyyyy}.lis.gz",
                          f"Margin_NCL_CM_0_CM_06436_{yyyymmdd}_F_0000.csv.gz",
                          f"Margin_NCL_CO_0_CM_06436_{yyyymmdd}_F_0000.csv.gz",
                          f"Margin_NCL_FO_0_CM_06436_{yyyymmdd}_F_0000.csv.gz"]
    for i in range(1, 9): expected_gz_files.append(f"O_SA01_P_06436_{ddmmyyyy}_i0{i}.lis.gz")
    for i in range(1, 5): expected_gz_files += [f"C_SA01_P_06436_{ddmmyyyy}_i0{i}.lis.gz", f"F_SA01_P_06436_{ddmmyyyy}_i0{i}.lis.gz"]
    for slot in COMM_PEAK_SLOTS: expected_gz_files.append(f"Margin_NCL_CO_0_CM_06436_{yyyymmdd}_P_{slot}.csv.gz")
    for slot in CM_PEAK_SLOTS:   expected_gz_files.append(f"Margin_NCL_CM_0_CM_06436_{yyyymmdd}_P_{slot}.csv.gz")
    for slot in FO_PEAK_SLOTS:   expected_gz_files.append(f"Margin_NCL_FO_0_CM_06436_{yyyymmdd}_P_{slot}.csv.gz")

    missing_files = [f for f in expected_gz_files if not is_extracted_present(f)]
    if not missing_files:
        print("✅ All NSE files already extracted — skipping API login")
    else:
        token = get_token()
        for slot in COMM_PEAK_SLOTS:
            token = safe_download_and_extract("CO", "Reports/Standard report", f"Margin_NCL_CO_0_CM_06436_{yyyymmdd}_P_{slot}.csv.gz", token)
        token = safe_download_and_extract("CO", "Reports/Standard report", f"Margin_NCL_CO_0_CM_06436_{yyyymmdd}_F_0000.csv.gz", token)
        for slot in CM_PEAK_SLOTS:
            token = safe_download_and_extract("CM", "Reports/Standard report", f"Margin_NCL_CM_0_CM_06436_{yyyymmdd}_P_{slot}.csv.gz", token)
        token = safe_download_and_extract("CM", "Reports/Standard report", f"Margin_NCL_CM_0_CM_06436_{yyyymmdd}_F_0000.csv.gz", token)
        token = safe_download_and_extract("CM", "Reports", f"C_MG13_06436_{ddmmyyyy}.lis.gz", token)
        for f in [f"C_SA01_06436_{ddmmyyyy}.lis.gz"] + [f"C_SA01_P_06436_{ddmmyyyy}_i0{i}.lis.gz" for i in range(1,5)] + [f"C_SA04_06436_{ddmmyyyy}.lis.gz"]:
            token = safe_download_and_extract("CM", "Reports", f, token)
        token = safe_download_and_extract("FO", "Reports/Standard report", f"Margin_NCL_FO_0_CM_06436_{yyyymmdd}_F_0000.csv.gz", token)
        for slot in FO_PEAK_SLOTS:
            token = safe_download_and_extract("FO", "Reports/Standard report", f"Margin_NCL_FO_0_CM_06436_{yyyymmdd}_P_{slot}.csv.gz", token)
        for f in [f"F_SA01_06436_{ddmmyyyy}.lis.gz"] + [f"F_SA01_P_06436_{ddmmyyyy}_i0{i}.lis.gz" for i in range(1,5)] + [f"F_SA04_06436_{ddmmyyyy}.lis.gz"]:
            token = safe_download_and_extract("FO", "Reports", f, token)
        # ⚠️ Delivery_NCL_CM_EquityT1 lives under folderPath "Reports", NOT
        # "Reports/Standard report". The manual script had it under the latter
        # and got a daily 404 ("File not found") for this one file while the
        # console fetched it fine — this line is the working value and the
        # manual script has now been corrected to match it. Do not "fix" this
        # to Standard report to match the other Margin_NCL_* files.
        for f in [f"C_CC02_06436_{ddmmyyyy}.csv.gz", f"Delivery_NCL_CM_EquityT1_CM_06436_{yyyymmdd}_P_0000.csv.gz",
                  f"C_06436_SEC_PLEDGE_{ddmmyyyy}_01.csv.gz", f"C_06436_SEC_PLEDGE_{ddmmyyyy}_02.csv.gz", f"C_MG13_06436_{ddmmyyyy}.lis.gz"]:
            token = safe_download_and_extract("CM", "Reports", f, token)
        for f in [f"F_CC02_06436_{ddmmyyyy}.CSV.gz", f"F_06436_SEC_PLEDGE_{ddmmyyyy}_01.csv.gz",
                  f"F_06436_SEC_PLEDGE_{ddmmyyyy}_02.csv.gz", f"F_MG13_06436_{ddmmyyyy}.lis.gz"]:
            token = safe_download_and_extract("FO", "Reports", f, token)
        for f in [f"O_CC02_06436_{ddmmyyyy}.CSV.gz", f"O_06436_SEC_PLEDGE_{ddmmyyyy}_01.csv.gz",
                  f"O_06436_SEC_PLEDGE_{ddmmyyyy}_02.csv.gz", f"O_SA01_06436_{ddmmyyyy}.lis.gz"] + \
                 [f"O_SA01_P_06436_{ddmmyyyy}_i0{i}.lis.gz" for i in range(1,9)] + [f"O_SA04_06436_{ddmmyyyy}.lis.gz"]:
            token = safe_download_and_extract("CO", "Reports", f, token)
        for f in [f"X_CC02_06436_{ddmmyyyy}.CSV.gz", f"X_SA01_06436_{ddmmyyyy}.lis.gz"]:
            token = safe_download_and_extract("CD", "Reports", f, token)

    # ── MCX ───────────────────────────────────────────────────────────────
    print("\n\n📦 STARTING MCX DOWNLOAD...\n")
    if are_all_mcx_files_present(trade_date):
        print("⏭️ All MCX files already exist — skipping")
    else:
        folder   = trade_date.strftime("%d-%m-%Y")
        mcx_date = trade_date.strftime("%Y%m%d")
        mcx_login(); csrf = mcx_init_session(); files = mcx_get_all_peak_files(folder, csrf)
        for fname in files: mcx_download(folder, fname, csrf)
        for fname in [f"MCX_INTRASAR_{MCX_USERNAME}_{mcx_date}.csv",
                      f"MCX_MARGIN_{MCX_USERNAME}_{mcx_date}.csv",
                      f"MCX_EODSAR_{MCX_USERNAME}_{mcx_date}.csv"]:
            mcx_download(folder, fname, csrf)

    # ── NCDEX + EPI ───────────────────────────────────────────────────────
    download_ncdex_file(trade_date)
    download_epi_file(trade_date)

    # ── NCDEX PEAK 01-08 ──────────────────────────────────────────────────
    # ===== ADDED (ported from the manual script): the console used to end
    # right after EPI, so the eight NCCL_MARGIN_REP peak files were never
    # fetched — this is the step the run appeared to "stop in between" at. =====
    print("\n\n📦 STARTING NCDEX PEAK 01-08 DOWNLOAD...\n")
    download_ncdex_peak_files(trade_date)

    print("\n\n🎉 ALL CO + CM + FO + CD + MCX + NCDEX FILES DOWNLOADED, EXTRACTED & CLEANED SUCCESSFULLY")
    return {"status": "ok", "trade_date": trade_date_str}


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else datetime.today().strftime("%Y-%m-%d")
    print(run_files_downloader_70(d))
