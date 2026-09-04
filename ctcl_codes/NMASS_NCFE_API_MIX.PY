import requests
import bcrypt
import base64
import pickle
import os
import json
import uuid
import threading
import time
from datetime import datetime

import tkinter as tk
from tkinter import simpledialog
from PIL import Image, ImageTk

from colorama import Fore, Style, init
init(autoreset=True)

import cv2
import numpy as np
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

try:
    from dotenv import load_dotenv as _ld
    # CTCL_ENV_FILE lets a caller point this at a specific .env explicitly.
    # The fallback used to be a hardcoded D:\CLAUDE\rms-copilot\.env — this
    # machine's own repo path, which is wrong on any OTHER machine and,
    # if that path happened to exist there too, could load a stranger's
    # config. Now derived from where THIS script actually lives (its repo's
    # own .env), so it's correct on every machine without needing an
    # override — override=False anyway, so it can never clobber whatever
    # the caller already put in this process's environment (e.g.
    # NMASS_SAVE_DIR, set explicitly by core/pipelines/nmass_download.py
    # before this subprocess is even started).
    _default_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    _ld(os.getenv("CTCL_ENV_FILE", _default_env))
except Exception:
    pass

BASE = "https://www.connect2nsccl.com"

# Read from .env (Credentials button on the dashboard writes these) — the
# literal defaults below only apply if CTCL_NMASS_* is ever missing from .env.
USERNAME = os.getenv("CTCL_NMASS_USER", "M0212193")
PASSWORD = os.getenv("CTCL_NMASS_PASSWORD", "$$Sumit2032$$")
CONSCODE = os.getenv("CTCL_NMASS_CONSCODE", "06436")


DOWNLOAD_FOLDER = os.getenv("NMASS_SAVE_DIR", r"D:\CTCL VS NMASS")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)


# THE ACTUAL BUG a fresh-machine deploy hits: this used to be a bare literal,
# r"D:\15 minutes\codes\CAPTCHA & SESSION" — no os.getenv() at all, so no
# amount of fixing MARGIN_SHORTAGE_FOLDER/.env.local could ever move it. Every
# login, on every pipe, on every machine, wrote the captcha image + session
# cookie there regardless of any config — the one file this script touches
# that .env.local's rebase could never reach.
#
# Deliberately still ONE FIXED path, not per-pipe (core/dl_lock.py's whole
# job is guarding this exact shared file from ctcl/ms15/cli colliding at the
# same instant) — just no longer a hardcoded drive letter. NMASS_SESSION_DIR
# lets it be pointed anywhere explicitly; the default derives from
# RMS_DATA_ROOT, which core/env_boot.py already puts in this process's
# environment before ANY subprocess using this script is launched (see
# core/pipelines/nmass_download.py / cli_download.py — both do
# `env = os.environ.copy()`, so RMS_DATA_ROOT rides along automatically).
# The literal old path is now only the last-resort fallback if RMS_DATA_ROOT
# itself is somehow unset — matches this repo's own default-if-missing
# convention (core/env_boot.py's own data_root()).
_rms_data_root = os.getenv("RMS_DATA_ROOT")
TEMP_FOLDER = os.getenv(
    "NMASS_SESSION_DIR",
    os.path.join(_rms_data_root, "15 minutes", "codes", "CAPTCHA & SESSION") if _rms_data_root
    else r"D:\15 minutes\codes\CAPTCHA & SESSION",
)
os.makedirs(TEMP_FOLDER, exist_ok=True)
COOKIE_FILE = os.path.join(TEMP_FOLDER, "session_cookies.pkl")


FP = uuid.uuid4().hex

session = requests.Session()

session_lock = threading.Lock()

authcode = None
conskey = None

print_lock = threading.Lock()


def log_info(msg):
    with print_lock:
        print(Fore.CYAN + "ℹ️  " + msg + Style.RESET_ALL)

def log_success(msg):
    with print_lock:
        print(Fore.GREEN + "🟢 " + msg + Style.RESET_ALL)

def log_warning(msg):
    with print_lock:
        print(Fore.YELLOW + "⚠️  " + msg + Style.RESET_ALL)

def log_error(msg):
    with print_lock:
        print(Fore.RED + "🔴 " + msg + Style.RESET_ALL)

def log_header(msg):
    with print_lock:
        print(Fore.MAGENTA + "\n" + "="*60)
        print("🚀 " + msg)
        print("="*60 + Style.RESET_ALL)


def debug_response(r):

    print("\nStatus Code:", r.status_code)

    print("\nResponse Headers:")
    for k, v in r.headers.items():
        print(k, ":", v)

    print("\nRaw Response:")
    print(r.text[:2000])

    print("\nCookies:", session.cookies.get_dict())


COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Origin": "https://www.connect2nsccl.com",
    "ufp": FP
}





# -----------------------------
# MAKING POPUP APPEARS IN CENTER
# -----------------------------


def center_window(win, width=400, height=300):

    win.update_idletasks()

    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()

    x = int((screen_width/2) - (width/2))
    y = int((screen_height/2) - (height/2))

    win.geometry(f"{width}x{height}+{x}+{y}")




# -----------------------------
# LOAD SESSION
# -----------------------------

def load_session():

    global authcode, conskey

    if not os.path.exists(COOKIE_FILE):
        return False

    print("\n==============================")
    print("LOADING SAVED SESSION")
    print("==============================")

    with open(COOKIE_FILE, "rb") as f:
        data = pickle.load(f)

    # 🔥 HANDLE OLD FORMAT (cookies only)
    if not isinstance(data, dict):
        print("⚠️ Old session format detected — deleting and forcing login")

        try:
            os.remove(COOKIE_FILE)
        except:
            pass

        return False

    # ✅ New format
    session.cookies.update(data["cookies"])
    authcode = data.get("authcode")
    conskey = data.get("conskey")

    # 🔥 extra safety
    if not authcode or not conskey:
        print("⚠️ Missing auth data — forcing login")
        return False

    # 🔥 regenerate token
    regenerate_token()

    # 🔥 validate session
    test = session.post(
        f"{BASE}/nmass/collateral-management/cm-coll-client-dtls",
        headers={
            **COMMON_HEADERS,
            "Content-Type": "application/json",
        },
        json=cm_payload,
        timeout=15
    )

    print("Session test:", test.status_code)

    if test.status_code == 200:
        print("🟢 SESSION VALID (REAL)")
        return True
    else:
        print("🔴 SESSION INVALID — clearing old session")

        session.cookies.clear()
        authcode = None
        conskey = None

        try:
            os.remove(COOKIE_FILE)
        except:
            pass

        return False




# -----------------------------
# REGENERATE TOKEN
# -----------------------------

def regenerate_token():

    global authcode, conskey

    headers = {
        **COMMON_HEADERS,
        "Content-Type": "application/json"
    }

    payload = {
        "version": "2.0",
        "data": {
            "userName": USERNAME,
            "consCode": CONSCODE,
            "authCode": authcode,
            "consKey": conskey
        }
    }

    r = session.post(
        f"{BASE}/nmass/generate-token",
        headers=headers,
        json=payload,
        timeout=15
    )

    if r.status_code == 200:
        log_success("Token refreshed ✅")
    elif r.status_code == 401:
        # 🔥 silently ignore (expected behavior)
        pass
    else:
        log_warning(f"Token issue (status {r.status_code})")
# -----------------------------
# SAVE SESSION
# -----------------------------

def save_session():

    print("\n==============================")
    print("SAVE SESSION")
    print("==============================")

    session_data = {
        "cookies": session.cookies,
        "authcode": authcode,
        "conskey": conskey
    }

    with open(COOKIE_FILE, "wb") as f:
        pickle.dump(session_data, f)

    print("Session fully saved (cookies + auth)")

# -----------------------------
# REFRESH TOKEN
# -----------------------------


def refresh_token():
    regenerate_token()


# -----------------------------
# KEEP SESSION ACTIVE
# -----------------------------



def keep_session_alive():

    while True:
        try:
            log_header("SESSION CHECK")

            log_info("Checking session... Please keep terminal open 🖥️")

            with session_lock:

                # 🔥 Refresh token
                regenerate_token()

                # 🔥 Validate session with real API
                r = session.post(
                    f"{BASE}/nmass/collateral-management/cm-coll-client-dtls",
                    headers={
                        **COMMON_HEADERS,
                        "Content-Type": "application/json"
                    },
                    json=cm_payload,
                    timeout=15
                )

            print(Fore.WHITE + "📡 Status Code: " + str(r.status_code) + Style.RESET_ALL)

            if r.status_code == 200:
                log_success("SESSION ACTIVE ✅")
                log_warning("DO NOT CLOSE TERMINAL — Bot is running 🚀")

            else:
                log_error("SESSION EXPIRED ❌")
                log_warning("Reconnecting... Please wait")

                login()

                log_success("SESSION RESTORED ✅")

        except Exception as e:
            log_error(f"ERROR: {e}")

        log_info("Next check in 10 minutes...\n")

        time.sleep(600)

# -----------------------------
# CAPTCHA POPUP
# -----------------------------


def captcha_popup(image_path, reason=None):
    """Route captcha through the dashboard web UI instead of a Tkinter popup.
    Writes state files to CTCL_NMASS_FOLDER, then polls for the answer submitted
    via /ctcl/captcha/answer on the dashboard server.
    reason: optional string shown in modal on retry attempts
            e.g. "CAPTCHA INCORRECT" or "USER ALREADY LOGGED IN"
    """
    import shutil as _shutil
    import json as _cjson

    detected = detect_captcha_text(image_path)

    _state_dir  = os.getenv("NMASS_SAVE_DIR", os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS"))
    os.makedirs(_state_dir, exist_ok=True)
    _state_file = os.path.join(_state_dir, ".captcha_state")
    _img_dst    = os.path.join(_state_dir, ".captcha_nmass.png")
    _ans_file   = os.path.join(_state_dir, ".captcha_answer")

    # Copy captcha image to accessible location
    try:
        _shutil.copy2(image_path, _img_dst)
    except Exception as _e:
        print(f"⚠️ Could not copy captcha image: {_e}")

    # Clear stale files then signal PENDING
    for _f in [_ans_file, _state_file]:
        try: os.remove(_f)
        except OSError: pass

    _st = {"state": "PENDING", "detected": detected}
    if reason: _st["reason"] = reason
    with open(_state_file, "w", encoding="utf-8") as _sf:
        _cjson.dump(_st, _sf)

    if reason:
        print(f"⏳ Captcha shown in dashboard UI [{reason}] — waiting for answer (pre-filled: '{detected}') …")
    else:
        print(f"⏳ Captcha shown in dashboard UI — waiting for answer (pre-filled: '{detected}') …")

    deadline = time.time() + 180   # 3-minute window
    while time.time() < deadline:
        time.sleep(1)
        try:
            with open(_state_file, encoding="utf-8") as _sf:
                _st = _cjson.load(_sf)
            if _st.get("state") == "ANSWERED":
                answer = open(_ans_file, encoding="utf-8").read().strip()
                for _f in [_ans_file, _state_file]:
                    try: os.remove(_f)
                    except OSError: pass
                print(f"✅ Captcha answered via dashboard: {answer}")
                return answer
        except Exception:
            pass

    print(f"⚠️ Captcha timed out — using auto-detected text: '{detected}'")
    for _f in [_ans_file, _state_file]:
        try: os.remove(_f)
        except OSError: pass
    return detected


# -----------------------------
# READING POPUP
# -----------------------------


def preprocess_captcha(image_path):

    img = cv2.imread(image_path)

    if img is None:
        raise Exception(f"❌ OpenCV failed to read image: {image_path}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # upscale image (better OCR)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

    # blur
    blur = cv2.GaussianBlur(gray, (3,3), 0)

    # detect edges
    edges = cv2.Canny(blur, 50, 150)

    # detect lines
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi/180,
        threshold=80,
        minLineLength=80,
        maxLineGap=10
    )

    # remove the diagonal line
    if lines is not None:
        for line in lines:
            x1,y1,x2,y2 = line[0]
            cv2.line(gray,(x1,y1),(x2,y2),(255,255,255),3)

    # threshold after line removal
    _, thresh = cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    processed_path = os.path.join(TEMP_FOLDER, "processed_captcha.png")
    cv2.imwrite(processed_path, thresh)

    return thresh


def split_characters(img):

    contours,_ = cv2.findContours(
        img,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    letters = []

    for cnt in contours:

        x,y,w,h = cv2.boundingRect(cnt)

        if w > 10 and h > 20:
            letter = img[y:y+h, x:x+w]
            letters.append((x, letter))

    # sort left to right
    letters = sorted(letters, key=lambda x:x[0])

    # keep only first 5 characters
    letters = letters[:5]

    return [l[1] for l in letters]


# -----------------------------
# EASY OCR
# -----------------------------


import easyocr

reader = easyocr.Reader(['en'], gpu=False)

def detect_captcha_text(image_path):

    img = preprocess_captcha(image_path)

    characters = split_characters(img)

    captcha_text = ""

    for char in characters:

        result = reader.readtext(
            char,
            detail=0,
            allowlist='abcdefghijklmnopqrstuvwxyz0123456789'
        )

        if result:
            captcha_text += result[0]

    captcha_text = captcha_text.lower().replace(" ", "")
   
    # fix common OCR mistakes
    captcha_text = captcha_text.replace("o","0")
    captcha_text = captcha_text.replace("l","1")
    captcha_text = captcha_text.replace("i","1")
    captcha_text = captcha_text.replace("s","5")

    # ensure exactly 5 characters
    captcha_text = captcha_text[:5]

    return captcha_text



# -----------------------------
# LOGIN
# -----------------------------

# Module-level: reason to show on the NEXT captcha popup when login() is called recursively
# (e.g. set to "USER ALREADY LOGGED IN" before `return login()` for 423 responses)
_login_carry_reason = None

# ═══════════════════════════════════════════════════════════════════════════
# CAPTCHA LOOP CAPS — added after a run sat asking for the captcha over and
# over with no way out. There were TWO unbounded loops here:
#
#   1. the `while True:` captcha/validate-user loop below, which `continue`d
#      on EVERY failure — wrong captcha, server error, "already logged in" —
#      forever, presenting a fresh captcha each time;
#   2. `return login()` on the HTTP 423 "existing session" path, which
#      recursed with no depth limit and re-entered that same while-loop.
#
# NMASS allows ONE active session per account, and this whole team shares one
# account. So if a colleague is logged in (or a previous run's session is
# still open), the logout-existing-user call can keep losing the race and the
# script recurses indefinitely — the operator just sees captcha after captcha
# and eventually gives up and presses Stop. Nothing was wrong with their
# input; there was simply no attempt limit and no message explaining it.
#
# Both loops are now capped and exit with an [NMASS_AUTH_FAIL]-style tag the
# dashboard already knows how to surface, so a stuck login fails in about a
# minute with a message naming the real cause instead of hanging forever.
# ═══════════════════════════════════════════════════════════════════════════
MAX_CAPTCHA_ATTEMPTS = 8     # wrong captcha / server error retries
MAX_SESSION_RESTARTS = 3     # 423 "existing session" logout-and-retry cycles
_login_restart_count = 0     # reset by the first non-recursive login() call


def login(_is_restart=False):

    global authcode, conskey, _login_carry_reason, _login_restart_count

    # A fresh (non-recursive) login attempt resets the 423 restart budget, so a
    # later, unrelated login in the same process isn't penalised by an earlier one.
    if not _is_restart:
        _login_restart_count = 0
    
    # ✅ Only skip if session is REALLY valid
    if authcode and conskey:
        try:
            test = session.post(
                f"{BASE}/nmass/collateral-management/cm-coll-client-dtls",
                headers={
                    **COMMON_HEADERS,
                    "Content-Type": "application/json"
                },
                json=cm_payload,
                timeout=20
            )

            if test.status_code == 200:
                log_success("Session still valid — skipping login ✅")
                return
            else:
                log_warning("Old session invalid — forcing fresh login 🔄")
        except:
            log_warning("Session check failed — forcing login 🔄")

        # 🔥 CLEAR OLD SESSION
        session.cookies.clear()
        authcode = None
        conskey = None
 

    print("\nLOGIN REQUIRED")

    r = session.get(f"{BASE}/auth/is-new-login", headers=COMMON_HEADERS)
    with print_lock:
        print("Status:", r.status_code)

    r = session.get(f"{BASE}/auth/get-application-name", headers=COMMON_HEADERS)
    with print_lock:
        print("Status:", r.status_code)


    _nmass_retry_reason = _login_carry_reason  # carry reason from recursive call (e.g. "USER ALREADY LOGGED IN")
    _login_carry_reason = None                  # clear after consuming
    _captcha_attempt = 0
    while True:

        _captcha_attempt += 1
        if _captcha_attempt > MAX_CAPTCHA_ATTEMPTS:
            # Give up rather than asking a tenth time. Whatever is wrong is not
            # going to be fixed by another captcha, and the operator deserves to
            # be told that instead of being kept in the loop.
            print(f"[NMASS_AUTH_FAIL] Login gave up after {MAX_CAPTCHA_ATTEMPTS} captcha "
                  f"attempts — last reason: {_nmass_retry_reason or 'captcha rejected'}", flush=True)
            raise RuntimeError(
                f"NMASS login failed after {MAX_CAPTCHA_ATTEMPTS} captcha attempts "
                f"({_nmass_retry_reason or 'captcha rejected'}). If this says USER ALREADY "
                f"LOGGED IN, someone else on the team is signed in to NMASS — ask them to "
                f"finish or close their session, then run this again."
            )
        if _captcha_attempt > 1:
            print(f"   ↻ captcha attempt {_captcha_attempt} of {MAX_CAPTCHA_ATTEMPTS}"
                  f" — {_nmass_retry_reason or 'retrying'}", flush=True)

        captcha = session.get(
            f"{BASE}/auth/get-captcha",
            headers={
                **COMMON_HEADERS,
                "Accept": "*/*",
                "bowfp": FP
            }
        )

        captcha_path = os.path.join(TEMP_FOLDER, "captcha.png")

        # 🔥 CHECK RESPONSE TYPE
        if "image" not in captcha.headers.get("Content-Type", ""):
            print("❌ Captcha not image — retrying...")
            continue

        with open(captcha_path, "wb") as f:
            f.write(captcha.content)

        print("✅ Captcha saved:", captcha_path)

        captcha_answer = captcha_popup(captcha_path, reason=_nmass_retry_reason)

        payload = {
            "version": "2.0",
            "data": {
                "userName": USERNAME,
                "consCode": CONSCODE,
                "captchaAnswer": captcha_answer
            }
        }

        r = session.post(
            f"{BASE}/auth/validate-user",
            headers=COMMON_HEADERS,
            json=payload,
            timeout=15
        )

        with print_lock:
            print("Status:", r.status_code)

        try:
            resp = r.json()
        except:
            _nmass_retry_reason = "SERVER ERROR — try again"
            continue

        if "data" not in resp:
            # Determine specific reason from server response
            _msg = ""
            try: _msg = (resp.get("messages", {}) or {}).get("message", "").lower()
            except Exception: pass
            if "already" in _msg and "login" in _msg:
                _nmass_retry_reason = "USER ALREADY LOGGED IN — try again"
            elif "captcha" in _msg or "invalid" in _msg:
                _nmass_retry_reason = "CAPTCHA INCORRECT — try again"
            elif "lock" in _msg:
                _nmass_retry_reason = "ACCOUNT LOCKED — contact support"
            elif _msg:
                _nmass_retry_reason = f"SERVER ERROR: {_msg}"
            else:
                _nmass_retry_reason = "CAPTCHA INCORRECT — try again"
            print(f"Login failed: {_nmass_retry_reason}")
            continue

        salt = resp["data"]["map"]["salt"]
        break


    hashed = bcrypt.hashpw(PASSWORD.encode(), salt.encode())

    auth_string = f"{USERNAME}:{hashed.decode()}"
    auth_header = base64.b64encode(auth_string.encode()).decode()

    headers = {
        **COMMON_HEADERS,
        "Authorization": f"Basic {auth_header}",
        "consCode": CONSCODE,
        "Content-Type": "application/json"
    }

    encoded_string = base64.b64encode(os.urandom(16)).decode()

    payload = {
        "version": "2.0",
        "data": {
            "encodedString": encoded_string
        }
    }

    r = session.post(
        f"{BASE}/auth/get-code",
        headers=headers,
        json=payload,
        timeout=15
    )

    with print_lock:
        print("Status:", r.status_code)

    resp = r.json()

    # Handle "already logged in" case
    if r.status_code == 423 or resp.get("messages", {}).get("code") == 423:

        _login_restart_count += 1

        # NMASS permits ONE session per account and the whole team shares one.
        # "Log the old session out and try again" works when the old session is
        # this machine's own leftover, but NOT when a colleague is actively
        # using it — they simply log back in and win the race again. Before this
        # cap, that produced an endless captcha prompt with no explanation; the
        # operator's only escape was Stop.
        if _login_restart_count > MAX_SESSION_RESTARTS:
            print(f"[NMASS_AUTH_FAIL] Another NMASS session keeps taking over — gave up after "
                  f"{MAX_SESSION_RESTARTS} attempts to clear it.", flush=True)
            raise RuntimeError(
                "NMASS says the account is already logged in somewhere else, and "
                f"{MAX_SESSION_RESTARTS} attempts to log that session out were each "
                "immediately overridden. This almost always means a colleague is signed "
                "in to NMASS right now (the team shares one account). Ask them to close "
                "it, then run this again — entering more captchas will not help."
            )

        print(f"\nEXISTING SESSION DETECTED — LOGGING OUT OLD SESSION "
              f"(attempt {_login_restart_count} of {MAX_SESSION_RESTARTS})", flush=True)

        logout_payload = {
            "version": "2.0"
        }

        r2 = session.post(
            f"{BASE}/auth/logout-existing-user",
             headers=headers,
             json=logout_payload,
             timeout=15
        )

        # Give the exchange a moment to actually drop the old session before
        # racing it again — retrying instantly is part of why this used to spin.
        time.sleep(2)

        print("\nRestarting login flow with new captcha...\n", flush=True)

        _login_carry_reason = "USER ALREADY LOGGED IN — enter captcha to continue"
        return login(_is_restart=True)

    # Confirmed via test_nmass_login.py: a wrong password shows up here as
    # HTTP 401 with {"status": "The username and/or password is incorrect..."}
    # — previously unhandled, so this fell through to a raw KeyError below
    # instead of a clear "password changed" message.
    if r.status_code == 401 or "incorrect" in str(resp.get("status", "")).lower():
        _msg = resp.get("status", "Invalid credentials")
        print(f"[NMASS_AUTH_FAIL] {_msg}")
        raise RuntimeError(f"NMASS login failed — wrong password: {_msg}")

    data = resp["data"]["map"]

    authcode = data["authcode"]
    conskey = data["conskey"]

    payload = {
        "version": "2.0",
        "data": {
            "service": "UNIFIED_PORTAL",
            "consCode": CONSCODE,
            "userName": USERNAME
        }
    }

    session.post(
        f"{BASE}/auth/display",
        headers=headers,
        json=payload,
        timeout=15
    )

    
    payload = {
        "version": "2.0",
        "data": {
            "userName": USERNAME,
            "consCode": CONSCODE,
            "authCode": authcode,
            "consKey": conskey
        }
    }

    session.post(
        f"{BASE}/nmass/generate-token",
        headers=headers,
        json=payload,
        timeout=15
    )

    save_session()


# -----------------------------
# DOWNLOAD FILE FUNCTION
# -----------------------------

def download_file(api_url, payload, retry=0):

    print("\nDOWNLOADING:", api_url)

    try:
        with session_lock:
            r = session.post(
                f"{BASE}{api_url}",
                headers={
                    **COMMON_HEADERS,
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache"
                },
                json=payload,
                timeout=15
            )

        with print_lock:
            print("Status:", r.status_code)

        # ✅ HANDLE FAILURE ONLY
        if r.status_code != 200:

            print("Download failed. Status:", r.status_code)

            # 🔥 If session expired
            if r.status_code in [401, 403, 404]:
                print(f"🚨 SESSION EXPIRED — RE-LOGIN (NMASS, HTTP {r.status_code}, "
                      f"possibly logged in elsewhere or timed out)")

                global authcode, conskey
                session.cookies.clear()
                authcode = None
                conskey = None

                try:
                    os.remove(COOKIE_FILE)
                except:
                    pass

            # 🔁 Retry max 10 times
            if retry < 10:
                print("Trying to re-login...")
                login()
                return download_file(api_url, payload, retry + 1)
            else:
                print("Max retries reached ❌")
                return

        # ✅ ONLY RUN THIS IF STATUS = 200
        if not r.text:
            print("Empty response — retrying")
            return download_file(api_url, payload, retry + 1)

        resp = r.json()

        if resp.get("status") != "success":
            print("Server returned error:", resp)

            if retry < 3:
                print("Retrying...")
                return download_file(api_url, payload, retry + 1)
            else:
                print("Max retries reached ❌")
                return

        file_name = resp["data"]["result"]["fileName"]
        base64_data = resp["data"]["result"]["base64str"]

        file_bytes = base64.b64decode(base64_data)

        file_path = os.path.join(DOWNLOAD_FOLDER, file_name)

        with open(file_path, "wb") as f:
            f.write(file_bytes)

        with print_lock:
            print("Saved:", file_path)

    except Exception as e:

        print("Download crashed:", e)

        if retry < 3:
            print("Retrying after crash...")
            login()
            return download_file(api_url, payload, retry + 1)
        else:
            print("Max retries reached after crash ❌")
            return


# -----------------------------
# PAYLOADS 
# -----------------------------

cm_payload = {
    "version": "2.0",
    "data": {
        "memType": "CM",
        "memCode": CONSCODE,
        "tmCode": "ALL",
        "cliCode": "",
        "dataFormat": "JSON:CSV"
    }
}

fo_payload = {
    "version": "2.0",
    "data": {
        "memType": "CM",
        "memCode": "M50154",
        "tmCode": "ALL",
        "cliCode": "",
        "dataFormat": "JSON:CSV"
    }
}

co_payload = {
    "version": "2.0",
    "data": {
        "memType": "CM",
        "memCode": "M50154",
        "tmCode": "ALL",
        "cliCode": "",
        "dataFormat": "JSON:CSV"
    }
}


# -----------------------------
# DOWNLOAD THREAD FUNCTION
# -----------------------------

def run_downloads():

    print("\n" + "-"*60)
    log_header("DOWNLOAD STARTED")

    download_file(
        "/nmass/collateral-management/cm-coll-client-dtls",
        cm_payload
    )

    download_file(
        "/nmass/collateral-management/fo-coll-client-dtls",
        fo_payload
    )

    download_file(
        "/nmass/collateral-management/co-coll-client-dtls",
        co_payload
    )

    log_success("ALL DOWNLOADS COMPLETED ✅")


# -----------------------------
# MAIN
# -----------------------------

if not load_session():
    login()
else:
    log_header("BOT STARTED")
    log_success("Using saved session — no captcha needed 🚀🔥")
    log_success("Session is ACTIVE and stable ✅")
    log_warning("DO NOT CLOSE THIS TERMINAL ❗")


# ✅ Start session checker in background
threading.Thread(target=keep_session_alive, daemon=True).start()


#---------------
# NCDEX CODE 
#---------------


    
def run_ncdex():
    import requests
    from bs4 import BeautifulSoup
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    import base64
    import urllib.parse

    import pytesseract
    import tkinter as tk
    from PIL import Image, ImageTk

    import cv2
    import numpy as np

    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    # =========================
    # 🔐 AES ENCRYPTION (FINAL FIX)
    # =========================
    def encrypt_password(password, hdn_value):
        key_str, iv_str = hdn_value.split(":")

        key = key_str.encode("utf-8")
        iv = iv_str.encode("utf-8")

        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(pad(password.encode("utf-8"), 16))

        return base64.b64encode(encrypted).decode()


    # =========================
    # HELPERS CAPTCHA
    # =========================
    def detect_captcha_text(image_path):
        img = cv2.imread(image_path)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 🔥 better contrast
        gray = cv2.equalizeHist(gray)

        # 🔥 adaptive threshold (KEY CHANGE)
        thresh = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_MEAN_C,
            cv2.THRESH_BINARY_INV,
            11, 2
        )

        # 🔥 remove noise (light)
        kernel = np.ones((2,2), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

        # 🔥 enlarge
        thresh = cv2.resize(thresh, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)

        processed_path = os.path.join(TEMP_FOLDER, "processed_captcha.png")
        cv2.imwrite(processed_path, thresh)

        # 🔥 BETTER CONFIG
        custom_config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'

        text = pytesseract.image_to_string(thresh, config=custom_config)

        text = text.strip().replace(" ", "").upper()
        text = ''.join(c for c in text if c.isalnum())

        return text[:6]


    def captcha_popup(image_path, reason=None):
        """Route NCDEX captcha through dashboard web UI (state-file pattern).
        reason: optional string shown in modal explaining why captcha appears again
                e.g. "❌ Captcha incorrect — try again" or "⚠️ Already logged in"
        """
        import shutil as _nsh, json as _ncj, time as _nt
        detected = detect_captcha_text(image_path)

        _state_dir  = os.getenv("NMASS_SAVE_DIR", os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS"))
        os.makedirs(_state_dir, exist_ok=True)
        _state_file = os.path.join(_state_dir, ".captcha_state")
        _img_dst    = os.path.join(_state_dir, ".captcha_nmass.png")
        _ans_file   = os.path.join(_state_dir, ".captcha_answer")

        try: _nsh.copy2(image_path, _img_dst)
        except Exception as _e: print(f"⚠️ Could not copy captcha image: {_e}")

        for _f in [_ans_file, _state_file]:
            try: os.remove(_f)
            except OSError: pass

        _st = {"state": "PENDING", "detected": detected, "source": "ncdex"}
        if reason: _st["reason"] = reason
        with open(_state_file, "w", encoding="utf-8") as _sf:
            _ncj.dump(_st, _sf)

        if reason:
            print(f"⏳ Captcha shown in dashboard UI [{reason}] — waiting for answer (pre-filled: '{detected}') …")
        else:
            print(f"⏳ Captcha shown in dashboard UI — waiting for answer (pre-filled: '{detected}') …")

        deadline = _nt.time() + 180
        while _nt.time() < deadline:
            _nt.sleep(1)
            try:
                with open(_state_file, encoding="utf-8") as _sf:
                    _cst = _ncj.load(_sf)
                if _cst.get("state") == "ANSWERED":
                    answer = open(_ans_file, encoding="utf-8").read().strip()
                    for _f in [_ans_file, _state_file]:
                        try: os.remove(_f)
                        except OSError: pass
                    print(f"✅ Captcha answered via dashboard: {answer}")
                    return answer
            except Exception:
                pass

        print(f"⚠️ Captcha timed out — using auto-detected text: '{detected}'")
        for _f in [_ans_file, _state_file]:
            try: os.remove(_f)
            except OSError: pass
        return detected


    # =========================
    # 🌐 SESSION START
    # =========================
    session = requests.Session()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://ncfe.ncdex.com",
        "Referer": "https://ncfe.ncdex.com/Login/NCFEMain.aspx",
        "Connection": "keep-alive"
    }

    login_url = "https://ncfe.ncdex.com/Login/NCFEMain.aspx"


    session = requests.Session()
    session.cookies.clear()

    # =========================
    # 🔹 STEP 1: GET LOGIN PAGE
    # =========================
    print("\n===== STEP 1: GET LOGIN PAGE =====")

    resp = session.get(login_url, headers=headers)
    print("Status Code:", resp.status_code)

    soup = BeautifulSoup(resp.text, "html.parser")

    viewstate = soup.find(id="__VIEWSTATE")["value"]
    eventvalidation = soup.find(id="__EVENTVALIDATION")["value"]
    viewstategenerator = soup.find(id="__VIEWSTATEGENERATOR")["value"]

    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    token = token_input["value"] if token_input else ""

    # ✅ GET HDN VALUE (IMPORTANT)
    hdn_value = soup.find(id="hdn")["value"]

    print("✅ Tokens extracted")
    print("HDN VALUE:", hdn_value)

    print("\n===== STEP 2: CAPTCHA =====")

    img = soup.find("img", src=lambda x: x and "Captcha.aspx" in x)

    captcha_src = img["src"]

    # 🔴 FIX URL ENCODING
    captcha_url = "https://ncfe.ncdex.com/Login/" + urllib.parse.quote(captcha_src, safe=":/?=&")

    print("Captcha URL:", captcha_url)

    captcha_headers = {
        "User-Agent": headers["User-Agent"],
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": login_url,
        "Connection": "keep-alive"
    }

    # The NCDEX captcha endpoint sometimes returns a 404/HTML error page instead
    # of the actual image (site hiccup) — saving that as "captcha.png" and
    # feeding it to cv2.imread() gets back None, and cv2.cvtColor(None, ...)
    # then crashes with "!_src.empty()" and kills the whole run instead of just
    # retrying the download. Now: validate the response actually looks like an
    # image before accepting it, retrying a few times if not.
    captcha_resp = None
    for _cap_attempt in range(1, 6):
        captcha_resp = session.get(
            captcha_url,
            headers=captcha_headers,
            cookies=session.cookies
        )
        print("Captcha Response Status:", captcha_resp.status_code)
        print("Captcha Content-Type:", captcha_resp.headers.get("Content-Type"))
        print("Captcha size:", len(captcha_resp.content))
        # Sniff the body instead of trusting Content-Type — NCDEX doesn't always
        # send an "image/*" header for a genuine captcha, and requiring it here
        # rejected good downloads and spun uselessly. Only reject bodies that
        # actually look like an HTML/XML error page (always starts with '<').
        _body = captcha_resp.content
        _looks_like_html = _body.lstrip()[:20].lower().startswith(b"<")
        if captcha_resp.status_code == 200 and len(_body) > 100 and not _looks_like_html:
            break
        print(f"⚠️ Captcha download looked invalid (attempt {_cap_attempt}/5) — "
              f"site may be having a hiccup, retrying in 2s...")
        time.sleep(2)
    else:
        raise RuntimeError("NCDEX captcha image never downloaded successfully after 5 attempts "
                            "(kept getting a non-image response — the site was likely down/erroring)")

    captcha_path = os.path.join(TEMP_FOLDER, "captcha.png")

    with open(captcha_path, "wb") as f:
        f.write(captcha_resp.content)
    time.sleep(0.5)

    print("🖼️ Captcha saved as captcha.png")

    # 🔴 ADD BACK INPUT
    captcha_text = captcha_popup(captcha_path)

    print("Final Captcha:", captcha_text)

    hdnCaptchaVal = captcha_src.split("cpt=")[-1]
    hdnCaptchaVal = urllib.parse.unquote(hdnCaptchaVal)

    # =========================
    # 🔹 STEP 3: ENCRYPT PASSWORD
    # =========================
    print("\n===== STEP 3: ENCRYPT PASSWORD =====")

    # Read from .env (Credentials button on the dashboard writes these) — the
    # literal defaults below only apply if CTCL_NCDEX_* is ever missing from
    # .env. This was previously hardcoded and ignored .env entirely, which is
    # why changing the password in Credentials had no effect on this login.
    username = os.getenv("CTCL_NCDEX_USER", "F01292")
    password = os.getenv("CTCL_NCDEX_PASSWORD", "aSKUS%2222")

    enc_password = encrypt_password(password, hdn_value)

    print("Encrypted:", enc_password)

    # =========================
    # 🔹 STEP 4: LOGIN POST
    # =========================
    print("\n===== STEP 4: LOGIN =====")

    payload = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": viewstategenerator,
        "__EVENTVALIDATION": eventvalidation,
        "__RequestVerificationToken": token,

        "txtUserNm": username,
        "txtPassword": enc_password,
        "txtCaptcha": captcha_text,

        "hdn": "",  # IMPORTANT (must be empty)
        "hdnCaptchaVal": hdnCaptchaVal,
        "hdnEnabledCaptcha": "1",

        "hdnUserAgent": "",
        "hidProId": "",
        "hidFile": "",

        "imgBtnLogin.x": "18",
        "imgBtnLogin.y": "1",
    }



    headers["Referer"] = login_url
    captcha_headers["Referer"] = login_url

    # =========================
    # 🔹 LOGIN WITH CAPTCHA RETRY
    # =========================
    print("\n===== LOGIN WITH CAPTCHA RETRY =====")

    _retry_reason = None   # filled per-branch; passed into captcha_popup on retries
    while True:
        login_resp = session.post(
            login_url,
            data=payload,
            headers=headers,
            allow_redirects=False
        )

        print("\n===== LOGIN RESPONSE =====")
        print("Status:", login_resp.status_code)
        print("Redirect:", login_resp.headers.get("Location"))
  
                # 🔥 SMART RESPONSE HANDLING (FIXED)

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(login_resp.text, "html.parser")

        error_box = soup.find(id="lblMsgBox")
        error_msg = error_box.text.strip().lower() if error_box else ""

        print("🔍 Server message:", error_msg)

        if login_resp.status_code == 302:
            print("✅ Login success (redirect)")
            break

        elif "already" in error_msg and "login" in error_msg:
            print("⚠️ USER ALREADY LOGGED IN — RETRYING SAME SESSION")

            # 🔥 DO NOT RESET SESSION

            # 🔥 RELOAD LOGIN PAGE
            resp = session.get(login_url, headers=headers)
            soup = BeautifulSoup(resp.text, "html.parser")

            viewstate = soup.find(id="__VIEWSTATE")["value"]
            eventvalidation = soup.find(id="__EVENTVALIDATION")["value"]
            viewstategenerator = soup.find(id="__VIEWSTATEGENERATOR")["value"]

            token_input = soup.find("input", {"name": "__RequestVerificationToken"})
            token = token_input["value"] if token_input else ""

            hdn_value = soup.find(id="hdn")["value"]

            # 🔥 NEW CAPTCHA
            img = soup.find("img", src=lambda x: x and "Captcha.aspx" in x)
            captcha_src = img["src"]

            captcha_url = "https://ncfe.ncdex.com/Login/" + urllib.parse.quote(captcha_src, safe=":/?=&")

            captcha_resp = None
            for _cap_attempt in range(1, 6):
                captcha_resp = session.get(captcha_url, headers=headers)
                _body = captcha_resp.content
                _looks_like_html = _body.lstrip()[:20].lower().startswith(b"<")
                if captcha_resp.status_code == 200 and len(_body) > 100 and not _looks_like_html:
                    break
                print(f"⚠️ Captcha download looked invalid (status {captcha_resp.status_code}, "
                      f"attempt {_cap_attempt}/5) — retrying in 2s...")
                time.sleep(2)
            else:
                raise RuntimeError("NCDEX captcha image never downloaded successfully after 5 attempts "
                                    "(kept getting a non-image response — the site was likely down/erroring)")

            captcha_path = os.path.join(TEMP_FOLDER, "captcha.png")

            with open(captcha_path, "wb") as f:
                f.write(captcha_resp.content)

            print("🔄 New captcha (same session)")

            captcha_text = captcha_popup(captcha_path, reason="⚠️ Already logged in — enter new captcha")

            # 🔥 REBUILD PAYLOAD
            payload = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": viewstate,
                "__VIEWSTATEGENERATOR": viewstategenerator,
                "__EVENTVALIDATION": eventvalidation,
                "__RequestVerificationToken": token,

                "txtUserNm": username,
                "txtPassword": encrypt_password(password, hdn_value),
                "txtCaptcha": captcha_text,

                "hdn": "",
                "hdnCaptchaVal": urllib.parse.unquote(captcha_src.split("cpt=")[-1]),
                "hdnEnabledCaptcha": "1",

                "hdnUserAgent": "",
                "hidProId": "",
                "hidFile": "",

                "imgBtnLogin.x": "18",
                "imgBtnLogin.y": "1",
            }

            continue

        elif "locked" in error_msg:
            # Account is already locked out — retrying with a new captcha
            # would just be another wrong-password attempt against a locked
            # account. Stop immediately instead of silently returning (a bare
            # `return` here used to make run_ncdex() look like it finished
            # normally, hiding the failure).
            print("🔴 USER LOCKED — STOPPING BOT")
            print(f"[NCFE_LOCKED] {error_msg}")
            raise RuntimeError(f"NCDEX (NCFE) account locked: {error_msg}")

        elif "invalid" in error_msg and ("username" in error_msg or "password" in error_msg):
            # Wrong password. This used to fall into the generic "retry with
            # a new captcha" branch below, which is exactly what burned
            # through the exchange's login-attempt limit and got the account
            # locked — a new captcha does nothing to fix a wrong password.
            print("🔴 WRONG PASSWORD — STOPPING BOT (not retrying)")
            print(f"[NCFE_AUTH_FAIL] {error_msg}")
            raise RuntimeError(f"NCDEX (NCFE) login failed — wrong password: {error_msg}")

        elif "captcha" in error_msg:
            print("❌ Captcha incorrect — retrying...")
            _retry_reason = "❌ Captcha incorrect — try again"

        elif error_msg:
            print(f"⚠️ Server error: {error_msg}")
            _retry_reason = f"⚠️ Server error: {error_msg}"

        else:
            print("⚠️ Unknown response — retrying...")
            _retry_reason = "⚠️ Unknown response — try again"

        # 🔍 DEBUG SERVER MESSAGE
        soup_msg = BeautifulSoup(login_resp.text, "html.parser")


        # 🔥 Reload login page
        resp = session.get(login_url, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")

        viewstate = soup.find(id="__VIEWSTATE")["value"]
        eventvalidation = soup.find(id="__EVENTVALIDATION")["value"]
        viewstategenerator = soup.find(id="__VIEWSTATEGENERATOR")["value"]

        token_input = soup.find("input", {"name": "__RequestVerificationToken"})
        token = token_input["value"] if token_input else ""

        hdn_value = soup.find(id="hdn")["value"]

        # 🔥 Get new captcha
        img = soup.find("img", src=lambda x: x and "Captcha.aspx" in x)
        captcha_src = img["src"]

        captcha_url = "https://ncfe.ncdex.com/Login/" + urllib.parse.quote(captcha_src, safe=":/?=&")

        captcha_resp = None
        for _cap_attempt in range(1, 6):
            captcha_resp = session.get(captcha_url, headers=headers)
            _body = captcha_resp.content
            _looks_like_html = _body.lstrip()[:20].lower().startswith(b"<")
            if captcha_resp.status_code == 200 and len(_body) > 100 and not _looks_like_html:
                break
            print(f"⚠️ Captcha download looked invalid (status {captcha_resp.status_code}, "
                  f"attempt {_cap_attempt}/5) — retrying in 2s...")
            time.sleep(2)
        else:
            raise RuntimeError("NCDEX captcha image never downloaded successfully after 5 attempts "
                                "(kept getting a non-image response — the site was likely down/erroring)")

        captcha_path = os.path.join(TEMP_FOLDER, "captcha.png")

        with open(captcha_path, "wb") as f:
            f.write(captcha_resp.content)

        print("🔄 New captcha loaded")

        captcha_text = captcha_popup(captcha_path, reason=_retry_reason)

        print("New Captcha:", captcha_text)

        # 🔥 REBUILD PAYLOAD
        payload = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstategenerator,
            "__EVENTVALIDATION": eventvalidation,
            "__RequestVerificationToken": token,

            "txtUserNm": username,
            "txtPassword": encrypt_password(password, hdn_value),
            "txtCaptcha": captcha_text,

            "hdn": "",
            "hdnCaptchaVal": urllib.parse.unquote(captcha_src.split("cpt=")[-1]),
            "hdnEnabledCaptcha": "1",

            "hdnUserAgent": "",
            "hidProId": "",
            "hidFile": "",

            "imgBtnLogin.x": "18",
            "imgBtnLogin.y": "1",
        }


    # =========================
    # 🔹 STEP 5: SECURITY QUESTION
    # =========================
    if login_resp.status_code == 302 and "SecurityQs" in str(login_resp.headers.get("Location")):
        print("\n===== STEP 5: SECURITY QUESTION =====")

        sec_url = "https://ncfe.ncdex.com" + login_resp.headers["Location"]

        sec_page = session.get(sec_url, headers=headers)
        soup = BeautifulSoup(sec_page.text, "html.parser")

        viewstate = soup.find(id="__VIEWSTATE")["value"]
        eventvalidation = soup.find(id="__EVENTVALIDATION")["value"]
        viewstategenerator = soup.find(id="__VIEWSTATEGENERATOR")["value"]

        answer = "a"

        sec_payload = {
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstategenerator,
            "__EVENTVALIDATION": eventvalidation,

            "ctl00$ContentPlaceHolderCentre$txtanswers0": answer,
            "ctl00$ContentPlaceHolderCentre$imgBtnSubmit.x": "9",
            "ctl00$ContentPlaceHolderCentre$imgBtnSubmit.y": "14",
            "ctl00$ContentPlaceHolderCentre$hdnType": "ACTIVE",
            "ctl00$ContentPlaceHolderCentre$hidDate": "",
            "ctl00$ContentPlaceHolderCentre$hdnCounter": "",
            "ctl00$ContentPlaceHolderCentre$Hidden1": ""
        }

        sec_resp = session.post(sec_url, data=sec_payload, headers=headers, allow_redirects=False)

        print("Security Status:", sec_resp.status_code)
        print("Redirect:", sec_resp.headers.get("Location"))

        if sec_resp.status_code == 302:
            print("\n✅ FULL LOGIN SUCCESS 🎉")
        else:
            print("\n❌ Security step failed")
            print(sec_resp.text[:1000])

    else:
        print("\n❌ Login did not reach security step")

    # =========================
    # 🔹 STEP 6: DASHBOARD
    # =========================
    dashboard_url = "https://ncfe.ncdex.com/Common/Dashboard.aspx"
    dash_resp = session.get(dashboard_url, headers=headers)

    print("\nDashboard status:", dash_resp.status_code)

    # =========================
    # 🔹 STEP 7: API
    # =========================
    print("\n===== STEP 7: API =====")

    api_url = "https://ncfe.ncdex.com/Common/MemberContact.ashx"

    api_resp = session.post(api_url, data={"MethodName": "GetData"}, headers=headers)

    print("API Status:", api_resp.status_code)
    print("Response:", api_resp.text[:500])


    import requests
    from bs4 import BeautifulSoup

    print("\n===== STEP 8: OPEN DEPOSIT PAGE =====")

    page_url = "https://ncfe.ncdex.com/CMS/ClientNetEffectiveDeposits.aspx"

    page_resp = session.get(page_url, headers=headers)
    print("Page Status:", page_resp.status_code)

    soup = BeautifulSoup(page_resp.text, "html.parser")

    viewstate = soup.find(id="__VIEWSTATE")["value"]
    eventvalidation = soup.find(id="__EVENTVALIDATION")["value"]
    viewstategenerator = soup.find(id="__VIEWSTATEGENERATOR")["value"]

    # 🔥 SAFE TOKEN HANDLING
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    token = token_input["value"] if token_input else ""

    print("Token present:", bool(token))
    print("✅ Page tokens extracted")

    import re

    print("\n===== STEP 9: DOWNLOAD FILE =====")

    download_headers = headers.copy()
    download_headers["Content-Type"] = "application/x-www-form-urlencoded"
    download_headers["Referer"] = page_url
    download_headers["Origin"] = "https://ncfe.ncdex.com"

    payload = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": viewstategenerator,
        "__EVENTVALIDATION": eventvalidation,

        **({"__RequestVerificationToken": token} if token else {}),

        "ctl00$ContentFooter$lstUCC": "",
        "ctl00$txtSubject": "Problem in: Client Net Effective Deposits",
        "ctl00$txtProbDesc": "",

        "ctl00$ContentFooter$imgBtnExport.x": "8",
        "ctl00$ContentFooter$imgBtnExport.y": "11",
    }

    download_resp = session.post(page_url, data=payload, headers=download_headers)

    print("Download Status:", download_resp.status_code)
    print("Content-Type:", download_resp.headers.get("Content-Type"))
    print("Content-Disposition:", download_resp.headers.get("Content-Disposition"))

    # =========================
    # 🔥 CORRECT FILE NAME FROM SERVER
    # =========================
    content_disp = download_resp.headers.get("Content-Disposition", "")

    file_name = "ClientNetEffectiveDeposits.csv"  # fallback

    if "filename=" in content_disp:
        file_name = re.findall(r'filename="?(.+?)"?$', content_disp)[0]

    print("Server File Name:", file_name)

    # =========================
    # 🔥 SAVE FILE CORRECTLY
    # =========================
    with open(os.path.join(DOWNLOAD_FOLDER, file_name), "wb") as f:
        f.write(download_resp.content)

    print(f"✅ File downloaded exactly like browser: {file_name}")







# =========================================================================
# ALLOCATION FILE UPLOAD MODE
# =========================================================================
# Deliberately lives in THIS script rather than a standalone one: the whole
# captcha/login/session-restart/423-handling stack above is what makes an
# authenticated NMASS call possible, and duplicating it in a second file is
# exactly how the two copies drift apart. Everything above (load_session /
# login / COMMON_HEADERS / session) already ran by the time we get here, so
# these two calls are just ordinary authenticated POSTs.
#
# Driven by env vars set by the caller (core/pipelines/nmass_alloc_upload.py):
#   NMASS_RUN_MODE=alloc_upload   → run this instead of the downloads
#   NMASS_ALLOC_FILE              → full path to the local file to upload
#   NMASS_ALLOC_REQUEST_TYPE      → "I" = Immediate/Intraday (default),
#                                   "E" = EOD  (the two buttons on the NMASS
#                                   Allocation File Upload page)

def list_alloc_batches():
    """Return the sorted list of batch numbers already present in NMASS's
    ALLOCATION *upload* folder for today, e.g. [1, 2, ... 21].

    This is the "which batch is currently the last" lookup — the upload folder
    is the authoritative one (it holds the .T#### files we submit); the
    download folder holds NSE's matching .S#### return/status files.
    """
    payload = {
        "version": "2.0",
        "data": {"reqVO": {
            "fileType": "ALLOCATION",
            "folderType": "upload",
            "memberCode": CONSCODE,
        }},
    }
    r = session.post(
        f"{BASE}/nmass/collateral-management/files-list",
        headers={**COMMON_HEADERS, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"files-list failed: HTTP {r.status_code} — {r.text[:300]}")

    body = r.json()
    if str(body.get("status", "")).upper() != "SUCCESS":
        raise RuntimeError(f"files-list rejected: {body.get('messages')}")

    today_compact = datetime.now().strftime("%d%m%Y")
    prefix = f"{CONSCODE}_ALLOC_{today_compact}.T"
    batches = []
    for item in (body.get("data") or {}).get("fileList") or []:
        name = str(item.get("fileName") or "")
        if name.startswith(prefix):
            tail = name[len(prefix):]
            if tail.isdigit():
                batches.append(int(tail))
    batches.sort()
    log_info(f"NMASS upload folder has {len(batches)} allocation file(s) for {today_compact}"
             + (f" — last batch T{batches[-1]:04d}" if batches else " — none yet"))
    return batches


def next_alloc_batch():
    """Next free batch number = last existing + 1 (1 if today has none yet)."""
    batches = list_alloc_batches()
    return (batches[-1] + 1) if batches else 1


def run_alloc_upload():
    src_path = os.getenv("NMASS_ALLOC_FILE", "").strip()
    if not src_path:
        raise RuntimeError("NMASS_ALLOC_FILE is not set — nothing to upload")
    if not os.path.exists(src_path):
        raise RuntimeError(f"Allocation file not found: {src_path}")

    request_type = (os.getenv("NMASS_ALLOC_REQUEST_TYPE", "I").strip().upper() or "I")

    log_header("ALLOCATION FILE UPLOAD")
    log_info(f"Local file : {src_path}")

    # 1. Ask NMASS which batch is currently last, and claim the next one.
    #    This is the whole reason the batch number can't be hardcoded: other
    #    people/systems upload allocation files for the same member code all
    #    day, so the correct number is only knowable at upload time.
    batch = next_alloc_batch()
    today_compact = datetime.now().strftime("%d%m%Y")
    final_name = f"{CONSCODE}_ALLOC_{today_compact}.T{batch:04d}"
    log_success(f"Uploading as batch T{batch:04d}  →  {final_name}")

    # 2. Read the file EXACTLY as bytes (no text round-trip) so the CRLF line
    #    endings the file was written with survive into the base64 payload.
    with open(src_path, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw).decode("ascii")

    payload = {
        "version": "2.0",
        "data": {"reqVO": {
            "fileType": "ALLOCATION",
            "folderType": "upload",
            "memberCode": CONSCODE,
            "requestType": request_type,
            "fileList": [{"fileName": final_name, "base64str": b64}],
        }},
    }

    r = session.post(
        f"{BASE}/nmass/collateral-management/upload-files",
        headers={**COMMON_HEADERS, "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"upload-files failed: HTTP {r.status_code} — {r.text[:300]}")

    body = r.json()
    msgs = body.get("messages") or {}
    if str(body.get("status", "")).upper() != "SUCCESS":
        raise RuntimeError(f"upload-files rejected: {msgs}")

    log_success("NMASS response: " + "; ".join(str(v) for v in msgs.values()))

    # 3. Rename the local copy to the name it was actually accepted under, so
    #    the file on disk and the file in NMASS can never disagree about which
    #    batch this was. Best-effort only — the upload already succeeded, and
    #    a rename failure must not make it look like it didn't.
    try:
        dst_path = os.path.join(os.path.dirname(src_path), final_name)
        if os.path.abspath(dst_path) != os.path.abspath(src_path):
            if os.path.exists(dst_path):
                os.remove(dst_path)
            os.rename(src_path, dst_path)
            log_info(f"Local file renamed to match: {dst_path}")
    except Exception as e:
        log_warning(f"Uploaded fine, but local rename failed: {e}")

    # Machine-readable line for the caller to parse out of the log stream.
    print(f"[ALLOC_UPLOADED] {final_name}")
    log_success("ALLOCATION FILE UPLOADED ✅")

    # 4. Collect NMASS's return/status file for this batch.
    #    Every upload produces one in the DOWNLOAD folder with the same number
    #    but an S prefix (T0022 -> S0022). It is not written instantly — on the
    #    website you have to press refresh — so this polls rather than checking
    #    once and reporting it missing.
    if os.getenv("NMASS_ALLOC_FETCH_STATUS", "1").strip() != "0":
        try:
            fetch_alloc_status_file(batch)
        except Exception as e:
            log_warning(f"Could not collect the status file: {e}")
            log_info("The upload itself succeeded — check the NMASS page directly.")


def fetch_alloc_status_file(batch, wait_seconds=None, poll_every=None):
    """Wait for, download and save the .S#### status file for one batch."""
    import base64 as _b64

    wait_seconds = int(wait_seconds or os.getenv("NMASS_ALLOC_STATUS_WAIT", "240"))
    poll_every   = int(poll_every   or os.getenv("NMASS_ALLOC_STATUS_POLL", "15"))
    save_dir = os.getenv(
        "NMASS_ALLOC_STATUS_DIR",
        os.path.join(os.path.dirname(os.getenv("NMASS_ALLOC_FILE", ".")), "STATUS"))
    os.makedirs(save_dir, exist_ok=True)

    today_compact = datetime.now().strftime("%d%m%Y")
    wanted = f"{CONSCODE}_ALLOC_{today_compact}.S{batch:04d}"

    log_header("WAITING FOR NMASS RETURN / STATUS FILE")
    log_info(f"Expecting: {wanted}")

    deadline = time.time() + wait_seconds
    info = None
    while time.time() < deadline:
        try:
            r = session.post(
                f"{BASE}/nmass/collateral-management/files-list",
                headers={**COMMON_HEADERS, "Content-Type": "application/json"},
                json={"version": "2.0", "data": {"reqVO": {
                    "fileType": "ALLOCATION",
                    "folderType": "download",
                    "memberCode": CONSCODE,
                }}},
                timeout=30,
            )
            listing = {str(it.get("fileName")): it
                       for it in ((r.json().get("data") or {}).get("fileList") or [])}
        except Exception as e:
            log_warning(f"could not read the download folder: {e}")
            listing = {}

        if wanted in listing:
            info = listing[wanted]
            break

        left = int(deadline - time.time())
        if left <= 0:
            break
        log_info(f"not there yet — checking again in {poll_every}s ({left}s left)")
        time.sleep(poll_every)

    if info is None:
        log_warning(f"{wanted} had not appeared within {wait_seconds}s. The upload "
                    f"itself already succeeded — check the NMASS page in a few minutes.")
        return None

    log_success(f"{wanted} is ready ({info.get('fileLength', '?')}, "
                f"{info.get('lastModified', '')})")

    # The folder listing returns base64str as an empty string — it only says
    # what exists. download-file is what actually returns the bytes.
    r = session.post(
        f"{BASE}/nmass/collateral-management/download-file",
        headers={**COMMON_HEADERS, "Content-Type": "application/json"},
        json={"version": "2.0", "data": {"reqVO": {
            "fileType": "ALLOCATION",
            "folderType": "download",
            "memberCode": CONSCODE,
            "downloadFileName": wanted,
        }}},
        timeout=90,
    )
    if r.status_code != 200:
        raise RuntimeError(f"download-file failed: HTTP {r.status_code} — {r.text[:200]}")
    body = r.json()
    if str(body.get("status", "")).upper() != "SUCCESS":
        raise RuntimeError(f"download-file rejected: {body.get('messages')}")

    b64 = (((body.get("data") or {}).get("file") or {}).get("byteArr")) or ""
    if not b64:
        raise RuntimeError("download-file returned no content")

    content = _b64.b64decode(b64)
    path = os.path.join(save_dir, wanted)
    with open(path, "wb") as f:
        f.write(content)
    log_success(f"Status file saved: {path}")

    # NMASS echoes each row back with an extra trailing field — the per-row
    # status code. A file where every row carries the same code went through
    # cleanly; a MIX means some rows were treated differently, which is worth
    # noticing now rather than a week later.
    codes, rows = {}, 0
    for line in content.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        rows += 1
        code = line.split(",")[-1].strip()
        codes[code] = codes.get(code, 0) + 1
    log_info(f"{rows} row(s) returned")
    for code, count in sorted(codes.items(), key=lambda kv: -kv[1]):
        log_info(f"   status {code or '(blank)'} : {count} row(s)")
    if len(codes) > 1:
        log_warning("MIXED status codes — not every row was treated the same. "
                    "Open the file and check before assuming this went through cleanly.")

    print(f"[ALLOC_STATUS_SAVED] {wanted}")
    return path


if os.getenv("NMASS_RUN_MODE", "").strip().lower() == "alloc_upload":
    try:
        run_alloc_upload()
    except Exception as e:
        log_error(f"Allocation upload failed: {e}")
        import sys as _alloc_sys
        _alloc_sys.exit(1)
    raise SystemExit(0)


# ── Single-run mode: run once and exit (used as subprocess by dashboard) ──
log_header("SINGLE RUN STARTED 🚀")

# 🔹 NMASS DOWNLOADS (CM / FO / CO)
run_downloads()

# 🔹 NCFE DOWNLOAD (ClientNetEffectiveDeposits)
# This used to swallow the exception and print "ALL DOWNLOADS COMPLETE"
# regardless — so a locked/wrong-password NCDEX account looked identical to
# a real success: no error, exit code 0, dashboard says "downloaded
# successfully". Now it actually reports the failure and exits non-zero.
_ncdex_failed = False
try:
    run_ncdex()
except Exception as e:
    log_error(f"NCDEX error: {e}")
    _ncdex_failed = True

if _ncdex_failed:
    log_error("⚠️ ALL DOWNLOADS DID NOT COMPLETE — NCDEX/NCFE step failed (see above)")
    import sys as _mix_sys
    _mix_sys.exit(1)
else:
    log_success("✅ ALL DOWNLOADS COMPLETE — exiting")
