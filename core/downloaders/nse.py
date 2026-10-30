"""
NSE extranet downloader  (the I/O layer - the only part that talks to NSE).

STATELESS + SCHEDULE-AWARE: you tell it WHICH pair index to fetch.
EFFICIENT: if both files for that pair are ALREADY on disk, it skips the NSE
login and download entirely and just returns the local paths. Login only
happens when there is actually something to fetch.

Credentials from .env. Token-refresh-on-401, exponential backoff, stop-on-400.

.env:
    NSE_MEMBER_CODE=06436
    NSE_LOGIN_ID=M0213815
    NSE_PASSWORD=Yxs2Hg1/h0KUKKoF5SqEGQ==
"""
import os
import gzip
import time
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

LOGIN_URL = "https://www.connect2nse.com/extranet-api/login/2.0"
DOWNLOAD_URL = "https://www.connect2nse.com/extranet-api/member/file/download/2.0"

CREDENTIALS = {
    "memberCode": os.getenv("NSE_MEMBER_CODE"),
    "loginId": os.getenv("NSE_LOGIN_ID"),
    "password": os.getenv("NSE_PASSWORD"),
}
VALID_INDEXES = ["i01", "i02", "i03", "i04"]


def _check_config():
    missing = [k for k, v in CREDENTIALS.items() if not v]
    if missing:
        names = {"memberCode": "NSE_MEMBER_CODE", "loginId": "NSE_LOGIN_ID",
                 "password": "NSE_PASSWORD"}
        raise RuntimeError(
            f"Missing NSE credential(s) in .env: {', '.join(names[m] for m in missing)}."
        )


def get_token(on_event=None) -> str:
    """Log in; retry on server/network errors, STOP on 400 (bad credential).
    on_event(msg) is called live for each step so the UI can stream it."""
    _check_config()
    def emit(m):
        (on_event or print)(m)
    retry_delay = 3
    while True:
        try:
            emit("Attempting NSE login...")
            resp = requests.post(LOGIN_URL, json=CREDENTIALS, timeout=15)
            if resp.status_code == 200:
                token = resp.json().get("token")
                if token:
                    emit("NSE login successful.")
                    return token
                emit("No token received, retrying...")
            elif resp.status_code == 400:
                raise RuntimeError(
                    "NSE login returned 400 (Bad Request) - credentials likely "
                    "wrong/empty. Check NSE_PASSWORD in .env matches your working "
                    "encrypted password exactly (including '==')."
                )
            elif resp.status_code in (500, 502, 503, 504):
                emit(f"Server error {resp.status_code}, retrying in {retry_delay}s...")
            else:
                emit(f"Login failed: {resp.status_code}, retrying...")
        except requests.exceptions.RequestException as e:
            emit(f"Network error during login: {e}")
        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 30)


def _download_one(segment, filename, folder, token, headers, attempts=3, emit=print):
    """Download a single .lis.gz, unzip -> .xlsx, return path. None if not published."""
    params = {"segment": segment, "folderPath": "Reports", "filename": filename}
    path_gz = os.path.join(folder, filename)
    path_xlsx = path_gz.replace(".lis.gz", ".xlsx")
    for _ in range(attempts):
        try:
            r = requests.get(DOWNLOAD_URL, headers=headers, params=params, timeout=30)
            if r.status_code == 401:
                emit("401 Unauthorized -> refreshing token...")
                token = get_token(emit)
                headers["Authorization"] = f"Bearer {token}"
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 504:
                emit("504 -> retrying in 3s...")
                time.sleep(3)
                continue
            r.raise_for_status()
            with open(path_gz, "wb") as f:
                f.write(r.content)
            with gzip.open(path_gz, "rt") as f:
                df = pd.read_csv(f, sep=",", header=None)
            df.to_excel(path_xlsx, index=False, header=False)
            os.remove(path_gz)
            emit(f"Downloaded & converted: {filename}")
            return path_xlsx
        except requests.exceptions.RequestException as e:
            emit(f"Network error: {e}. Retrying in 3s...")
            time.sleep(3)
    return None


def download_pair(index: str, folder: str, on_event=None) -> dict:
    """Get ONE FO+CM snapshot pair for `index` ('i01'..'i04').

    Fast path: if BOTH .xlsx already exist locally, skip NSE login/download
    entirely and return them. Otherwise log in and fetch what's missing.
    """
    emit = on_event or print
    if index not in VALID_INDEXES:
        return {"status": "bad_index", "index": index,
                "message": f"index must be one of {VALID_INDEXES}"}

    os.makedirs(folder, exist_ok=True)
    today = datetime.now().strftime("%d%m%Y")
    member = CREDENTIALS["memberCode"] or os.getenv("NSE_MEMBER_CODE")

    fo_xlsx = os.path.join(folder, f"F_SA01_P_{member}_{today}_{index}.xlsx")
    cm_xlsx = os.path.join(folder, f"C_SA01_P_{member}_{today}_{index}.xlsx")

    # --- FAST PATH: both files already present -> no NSE login needed ---
    if os.path.exists(fo_xlsx) and os.path.exists(cm_xlsx):
        emit(f"Already have: {os.path.basename(fo_xlsx)}")
        emit(f"Already have: {os.path.basename(cm_xlsx)}")
        return {"status": "ok", "index": index, "fo_path": fo_xlsx, "cm_path": cm_xlsx}

    # --- Otherwise we must fetch -> NOW log in ---
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    fo = fo_xlsx if os.path.exists(fo_xlsx) else \
        _download_one("FO", f"F_SA01_P_{member}_{today}_{index}.lis.gz", folder, token, headers, emit=emit)
    if os.path.exists(fo_xlsx):
        fo = fo_xlsx
    cm = cm_xlsx if os.path.exists(cm_xlsx) else \
        _download_one("CM", f"C_SA01_P_{member}_{today}_{index}.lis.gz", folder, token, headers, emit=emit)
    if os.path.exists(cm_xlsx):
        cm = cm_xlsx

    if fo and cm:
        return {"status": "ok", "index": index, "fo_path": fo, "cm_path": cm}

    missing = []
    if not fo:
        missing.append("FO")
    if not cm:
        missing.append("CM")
    return {"status": "not_arrived", "index": index, "missing": missing,
            "message": f"Pair {index}: {', '.join(missing)} file(s) not on NSE yet. "
                       f"Try again at the next scheduled run."}
