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

# ── Automatic captcha solving (2Captcha) ─────────────────────────────────────
# core/captcha_solver.py answers the NMASS and NCDEX captchas itself: 45 s per try, at most 5 tries per login,
# then the normal dashboard popup takes over. The dashboard switch "Captcha auto-fill" turns it on/off.
# This script runs as its own process, so the repo root is put on sys.path to import `core`.
import sys as _sys
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
try:
    from core import captcha_solver as _cs
except Exception as _cs_err:      # auto-fill is optional: the manual popup must keep working without it
    _cs = None
    print(f"⚠️ Captcha auto-fill unavailable ({_cs_err}) — manual popup only", flush=True)

_AUTO_SESSIONS = {}


def _auto(portal):
    """One AutoSession per portal for the life of this process; None when the solver module could not load."""
    if _cs is None:
        return None
    if portal not in _AUTO_SESSIONS:
        _AUTO_SESSIONS[portal] = _cs.AutoSession(portal)
    return _AUTO_SESSIONS[portal]


def _auto_answer(portal, image_path):
    """Try to solve the captcha picture automatically; returns the text, or None to show the manual popup."""
    session_ = _auto(portal)
    if session_ is None:
        return None
    try:
        with open(image_path, "rb") as _fh:
            return session_.solve(_fh.read())
    except Exception as _e:       # never let auto-fill break a login: fall back to the person
        print(f"⚠️ Captcha auto-fill error ({type(_e).__name__}: {_e}) — manual popup", flush=True)
        return None


def _auto_note(portal):
    """Sentence shown in the manual popup explaining why auto-fill stopped (empty when it did not run)."""
    session_ = _auto(portal)
    return session_.manual_note() if session_ is not None else ""


def _auto_feedback(portal, accepted):
    """Tell the solver whether the portal accepted the last automatic answer (resets / spends the try budget)."""
    session_ = _auto(portal)
    if session_ is None:
        return False
    return session_.accepted() if accepted else session_.rejected()


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

    # Automatic answer first (2Captcha). None means: switch off, no key, or the 5 tries are used up.
    _auto_text = _auto_answer("nmass", image_path)
    if _auto_text:
        return _auto_text
    _note = _auto_note("nmass")
    if _note:
        reason = f"{reason} | {_note}" if reason else _note

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
        # The automatic tries (max 5, see core/captcha_solver.py) come on top of the manual attempts, so the person
        # still gets the full MAX_CAPTCHA_ATTEMPTS after auto-fill has given up.
        if _captcha_attempt > MAX_CAPTCHA_ATTEMPTS + (_auto("nmass").tries_used if _auto("nmass") else 0):
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
            _auto_feedback("nmass", False)
            _nmass_retry_reason = "SERVER ERROR — try again"
            continue

        if "data" not in resp:
            # Determine specific reason from server response
            _msg = ""
            try: _msg = (resp.get("messages", {}) or {}).get("message", "").lower()
            except Exception: pass
            # "Already logged in" means the captcha itself was fine; every other failure counts against the
            # automatic try budget (and is reported to 2Captcha as a wrong answer).
            _auto_feedback("nmass", "already" in _msg and "login" in _msg)
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

        _auto_feedback("nmass", True)          # captcha accepted: reports it to 2Captcha, resets the try budget
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
# File format requested from NMASS. CTCL / Margin Shortage use CSV (default).
# Client Prudential Limit's original script requests TXT, so cli_download.py
# sets NMASS_DATA_FORMAT=TXT for its run — the exchange then names the CM/FO/CO
# files .txt natively (no renaming anywhere).
_DATA_FORMAT = "JSON:" + (os.getenv("NMASS_DATA_FORMAT", "CSV").strip().upper() or "CSV")

cm_payload = {
    "version": "2.0",
    "data": {
        "memType": "CM",
        "memCode": CONSCODE,
        "tmCode": "ALL",
        "cliCode": "",
        "dataFormat": _DATA_FORMAT
    }
}

fo_payload = {
    "version": "2.0",
    "data": {
        "memType": "CM",
        "memCode": "M50154",
        "tmCode": "ALL",
        "cliCode": "",
        "dataFormat": _DATA_FORMAT
    }
}

co_payload = {
    "version": "2.0",
    "data": {
        "memType": "CM",
        "memCode": "M50154",
        "tmCode": "ALL",
        "cliCode": "",
        "dataFormat": _DATA_FORMAT
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


    
class NcdexCaptchaUnavailable(RuntimeError):
    """NCDEX served no usable captcha image — safe to retry from the very start of the login."""


# NCFE allows ONE login per user. If a run leaves its session open, the next login is told "user already logged in"
# and burns an extra captcha + a retry. With NCFE_LOGOUT_AFTER=1 (set by the LIVE ALLOCATION process) the run signs
# out of NCFE when it is finished - also when the download failed. Off by default so the other boxes behave as before.
_NCFE_HOLD = {}


def _ncfe_logout():
    fn = _NCFE_HOLD.pop("fn", None)
    if fn is None or os.getenv("NCFE_LOGOUT_AFTER", "0").strip() != "1":
        return
    try:
        fn()
    except Exception as _e:                      # a logout problem must never hide the real result of the run
        print(f"⚠️ NCFE logout did not complete: {type(_e).__name__}: {str(_e)[:160]}", flush=True)


def run_ncdex():
    try:
        _run_ncdex_inner()
    finally:
        _ncfe_logout()


def _run_ncdex_inner():
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
        # Automatic answer first (2Captcha); None = switch off / no key / 5 tries used up -> manual popup below.
        _auto_text = _auto_answer("ncdex", image_path)
        if _auto_text:
            return _auto_text
        _note = _auto_note("ncdex")
        if _note:
            reason = f"{reason} | {_note}" if reason else _note
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
    # 🌐 SESSION START  (rewritten to match the proven-working manual version —
    # a generic redirect-chain follower + real authentication verification,
    # instead of guessing which of SecurityQs/Dashboard/frmSession comes next
    # and treating any 302 as success. Confirmed working against the live
    # site by the manual copy at D:\15 minutes\codes\API HITTING before being
    # ported here.)
    # =========================
    import re

    session = requests.Session()
    session.cookies.clear()

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")

    session.headers.update({
        "User-Agent": UA,
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,image/apng,*/*;q=0.8"),
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })

    ROOT      = "https://ncfe.ncdex.com"
    login_url = ROOT + "/Login/NCFEMain.aspx"
    FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded", "Origin": ROOT}

    # Read from .env (Credentials button on the dashboard writes these) — the
    # literal defaults below only apply if CTCL_NCDEX_* is ever missing.
    username = os.getenv("CTCL_NCDEX_USER", "F01292")
    password = os.getenv("CTCL_NCDEX_PASSWORD", "Askus@1292")

    MAX_LOGIN_TRIES = 5

    # Every response of this run gets dumped here, in order, on disk — this is
    # what actually found both the missing-antiforgery-token bug and the
    # frmSession.aspx bug (no amount of guessing from status codes alone did).
    # Kept ON permanently rather than removed after the fact: if NCFE changes
    # anything else later, this is the difference between reading the real
    # response immediately vs. another multi-round guessing cycle. Cleared at
    # the start of every run so it never grows unbounded.
    DEBUG_DIR = os.path.join(TEMP_FOLDER, "NCFE_DEBUG")
    os.makedirs(DEBUG_DIR, exist_ok=True)
    for _old in os.listdir(DEBUG_DIR):
        try:
            os.remove(os.path.join(DEBUG_DIR, _old))
        except Exception:
            pass

    def soup_of(r):
        return BeautifulSoup(r.text, "html.parser")

    def page_title(r):
        try:
            t = soup_of(r).find("title")
            return t.text.strip() if t else ""
        except Exception:
            return ""

    def page_msg(r):
        try:
            b = soup_of(r).find(id="lblMsgBox")
            return b.text.strip() if b else ""
        except Exception:
            return ""

    _step = [0]

    def dump(tag, r, body_chars=900, save_body=True):
        """Print EVERYTHING about a response and save the raw body to DEBUG_DIR."""
        _step[0] += 1
        n = _step[0]
        print("\n" + "-" * 78)
        print("[NCFE DEBUG %02d] %s" % (n, tag))
        print("    request          : %s %s" % (r.request.method, r.url))
        print("    status           : %s" % r.status_code)
        print("    location         : %s" % r.headers.get("Location"))
        print("    content-type     : %s" % r.headers.get("Content-Type"))
        print("    content-disp     : %s" % r.headers.get("Content-Disposition"))
        print("    bytes            : %s" % len(r.content))
        try:
            _t = page_title(r)
            _m = page_msg(r)
        except Exception:
            _t, _m = "", ""
        if _t:
            print("    <title>          : %s" % _t)
        if _m:
            print("    lblMsgBox        : %s" % _m)
        if save_body and r.content:
            ctype = (r.headers.get("Content-Type") or "").lower()
            ext = "html" if "html" in ctype else ("txt" if "text" in ctype else "bin")
            safe = re.sub(r"[^A-Za-z0-9]+", "_", tag)[:45]
            p = os.path.join(DEBUG_DIR, "%02d_%s.%s" % (n, safe, ext))
            try:
                with open(p, "wb") as f:
                    f.write(r.content)
                print("    body saved       : %s" % p)
            except Exception as e:
                print("    body save failed : %s" % e)
        if body_chars and r.content[:1] in (b"<", b" ", b"\n", b"\r"):
            print("    ---------------- body (first %d chars) ----------------" % body_chars)
            print(r.text[:body_chars])
            print("    ------------------------------------------------------")
        print("-" * 78)
        return r

    def logged_in(r):
        """True only if the page really is an authenticated NCFE page."""
        try:
            s = soup_of(r)
        except Exception:
            return False
        if s.find(id="txtUserNm") or s.find(id="hdnCaptchaVal"):
            return False                      # this is the login page
        if s.find(id="ctl00_lblUser"):
            return True
        if s.find("input", {"id": "ctl00_imgBtnLogout"}):
            return True
        return False

    def collect_fields(resp_or_soup):
        """Every postable input on the page, in document order, duplicates KEPT
        (NCFE posts __ANTIFORGERY twice — a plain dict would lose one)."""
        s = resp_or_soup if isinstance(resp_or_soup, BeautifulSoup) else soup_of(resp_or_soup)
        out = []
        for inp in s.find_all("input"):
            nm = inp.get("name")
            if not nm:
                continue
            t = (inp.get("type") or "text").lower()
            if t in ("image", "submit", "button", "password", "file"):
                continue
            if t in ("checkbox", "radio"):
                if inp.has_attr("checked"):
                    out.append((nm, inp.get("value", "on")))
                continue
            out.append((nm, inp.get("value", "")))
        for sel in s.find_all("select"):
            nm = sel.get("name")
            if not nm:
                continue
            opt = sel.find("option", selected=True) or sel.find("option")
            out.append((nm, opt.get("value", "") if opt else ""))
        for ta in s.find_all("textarea"):
            nm = ta.get("name")
            if nm:
                out.append((nm, ta.text or ""))
        return out

    def get_af_token(s):
        """NCFE's anti-forgery token — missing this (not the captcha, not the
        password) is what was bouncing every login to frmSession.aspx
        ('Session Expired') no matter how fresh the viewstate/captcha were."""
        el = s.find("input", {"name": "_AntiForgeryToken"})
        if el and el.get("value"):
            return el["value"]
        el2 = s.find("input", {"name": "__ANTIFORGERY"})
        return el2["value"] if el2 and el2.get("value") else ""

    def ncfe_logout():
        """Press the real 'Logout' image button of the NCFE master page (what the browser does), then prove it worked:
        a 302 back to /Login/NCFEMain.aspx, and the dashboard no longer opens as a logged-in page."""
        dash = ROOT + "/Common/Dashboard.aspx"
        pg = session.get(dash, headers={"Referer": ROOT + "/Common/Dashboard.aspx"}, allow_redirects=True)
        if not logged_in(pg):
            print("ℹ️ NCFE: already signed out - nothing to do.", flush=True)
            return
        data = collect_fields(pg)
        data += [("ctl00$imgBtnLogout.x", "17"), ("ctl00$imgBtnLogout.y", "16")]
        r = session.post(dash, data=data, headers={**FORM_HEADERS, "Referer": dash}, allow_redirects=False)
        loc = r.headers.get("Location") or ""
        if r.status_code in (301, 302) and "NCFEMain" in loc:
            chk = session.get(dash, headers={"Referer": dash}, allow_redirects=True)
            if not logged_in(chk):
                print("✅ NCFE: signed out (the next login will not hit 'user already logged in').", flush=True)
                return
        print(f"⚠️ NCFE: logout was not confirmed (HTTP {r.status_code}, location '{loc}').", flush=True)

    _NCFE_HOLD["fn"] = ncfe_logout

    # -------------------------------------------------------------------
    # STEP 1: LOGIN PAGE
    # -------------------------------------------------------------------
    print("\n===== STEP 1: GET LOGIN PAGE =====")
    page = session.get(login_url, headers={"Referer": login_url})
    dump("GET login page", page, body_chars=0)

    def fetch_captcha(page_resp, reason=None):
        """Download the captcha image off `page_resp`, validating it actually
        looks like an image first (NCDEX occasionally serves an HTML error
        page here instead — feeding THAT straight to cv2 used to crash the
        whole run instead of just retrying), then route it through the
        dashboard's own captcha popup (state-file handshake, not a local Tk
        window — this runs headless on the console machine)."""
        s = soup_of(page_resp)
        img = s.find("img", src=lambda x: x and "Captcha.aspx" in x)
        if not img:
            # The site returned an error / half page instead of the login form: restart the whole login (same as a
            # missing captcha image) instead of giving up.
            raise NcdexCaptchaUnavailable("Captcha <img> not on page (status %s) — NCDEX is erroring, see %s"
                                          % (getattr(page_resp, "status_code", "?"), DEBUG_DIR))
        cap_url = ROOT + "/Login/" + urllib.parse.quote(img["src"], safe=":/?=&")
        cap_resp = None
        for _cap_attempt in range(1, 4):                      # captcha download: 3 tries
            cap_resp = session.get(cap_url, headers={
                "Referer": login_url,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            })
            _body = cap_resp.content
            _looks_like_html = _body.lstrip()[:20].lower().startswith(b"<")
            if cap_resp.status_code == 200 and len(_body) > 100 and not _looks_like_html:
                break
            print(f"⚠️ Captcha download looked invalid (status {cap_resp.status_code}, "
                  f"attempt {_cap_attempt}/3) — retrying in 2s...")
            time.sleep(2)
        else:
            # A different exception type on purpose: the caller restarts the WHOLE
            # login (fresh session + fresh login page) instead of giving up.
            raise NcdexCaptchaUnavailable("NCDEX captcha image could not be downloaded in 3 "
                                          "attempts (kept getting a non-image response — the site was "
                                          "likely down/erroring).")
        dump("GET captcha image", cap_resp, body_chars=0)
        p = os.path.join(TEMP_FOLDER, "captcha.png")
        with open(p, "wb") as f:
            f.write(cap_resp.content)
        time.sleep(0.3)
        return captcha_popup(p, reason=reason)

    def build_login_payload(page_resp, captcha_text):
        """Replicates the browser POST field-for-field, including the
        _AntiForgeryToken / __ANTIFORGERY trio the old code never sent."""
        s = soup_of(page_resp)

        def v(name):
            el = s.find("input", {"name": name})
            return el.get("value", "") if el else ""

        af  = get_af_token(s)
        vs  = v("__VIEWSTATE")
        vsg = v("__VIEWSTATEGENERATOR")
        ev  = v("__EVENTVALIDATION")
        rvt = v("__RequestVerificationToken")

        hdn_el = s.find(id="hdn")
        if hdn_el is None:
            raise RuntimeError("hdn (AES key:iv) missing — page is not the login form")
        hdn_value = hdn_el.get("value", "")
        cap_val_el = s.find(id="hdnCaptchaVal")
        cap_val = cap_val_el.get("value", "") if cap_val_el else ""
        cap_enabled_el = s.find(id="hdnEnabledCaptcha")
        cap_enabled = cap_enabled_el.get("value", "1") if cap_enabled_el else "1"

        print("    _AntiForgeryToken: %s" % (af or "(MISSING)"))
        print("    hdn (AES key:iv) : %s" % hdn_value)
        print("    hdnCaptchaVal    : %s" % cap_val)
        print("    captcha typed    : %s" % captcha_text)

        enc = encrypt_password(password, hdn_value)

        return [
            ("_AntiForgeryToken", af),
            ("__ANTIFORGERY", af),
            ("__ANTIFORGERY", af),
            ("__VIEWSTATE", vs),
            ("__VIEWSTATEGENERATOR", vsg),
            ("__EVENTVALIDATION", ev),
            ("__RequestVerificationToken", rvt),
            ("hdn", ""),                      # browser blanks it before posting
            ("hdnCaptchaVal", cap_val),
            ("hdnEnabledCaptcha", cap_enabled),
            ("txtUserNm", username),
            ("txtPassword", enc),
            ("txtCaptcha", captcha_text),
            ("imgBtnLogin.x", "46"),
            ("imgBtnLogin.y", "13"),
            ("hdnUserAgent", UA),             # must equal our UA header
            ("hidProId", ""),
            ("hidFile", ""),
        ]

    # -------------------------------------------------------------------
    # STEP 2-4: CAPTCHA + LOGIN (retry loop, bounded) — same locked /
    # wrong-password immediate-stop behaviour as before: a new captcha never
    # fixes either of those, and retrying anyway is what burns through
    # NCDEX's login-attempt limit and gets the account locked for real.
    # -------------------------------------------------------------------
    login_resp = None
    _retry_reason = None
    # The automatic tries (max 5) come on top of the manual attempts, so the person still gets MAX_LOGIN_TRIES
    # after auto-fill has given up. With auto-fill switched off the limit is exactly what it always was.
    _login_cap = MAX_LOGIN_TRIES + (_cs.MAX_TRIES if (_cs is not None and _cs.is_enabled()) else 0)
    for attempt in range(1, _login_cap + 1):
        print("\n===== LOGIN ATTEMPT %d/%d =====" % (attempt, _login_cap))
        captcha_text = fetch_captcha(page, reason=_retry_reason)
        data = build_login_payload(page, captcha_text)

        login_resp = session.post(
            login_url, data=data,
            headers={**FORM_HEADERS, "Referer": login_url},
            allow_redirects=False,
        )
        dump("POST login (attempt %d)" % attempt, login_resp, body_chars=0)

        if login_resp.status_code >= 500:
            # 502/503/504 = NCDEX's own server failed the login POST. That is NOT a wrong captcha: do not count it
            # against the captcha solver, and do not reuse this error page - start the whole login again.
            raise NcdexCaptchaUnavailable("NCDEX login server error: HTTP %d on the login POST "
                                          "(the site is overloaded or down)" % login_resp.status_code)

        if login_resp.status_code in (301, 302):
            print("✅ login POST accepted → %s" % login_resp.headers.get("Location"))
            _auto_feedback("ncdex", True)
            break

        msg = page_msg(login_resp).lower()
        # Only a "captcha" (or empty/unknown) message means the automatic answer was wrong; "already logged in"
        # proves the captcha itself was accepted. Locked / wrong-password stops below and is not the captcha's fault.
        if "locked" in msg or ("invalid" in msg and ("username" in msg or "password" in msg)) \
                or ("already" in msg and "login" in msg):
            _auto_feedback("ncdex", True)
        else:
            _auto_feedback("ncdex", False)

        if "locked" in msg:
            # Account is already locked out — retrying with a new captcha
            # would just be another wrong-password attempt against a locked
            # account. Stop immediately rather than retrying.
            print("🔴 USER LOCKED — STOPPING BOT")
            print(f"[NCFE_LOCKED] {msg}")
            raise RuntimeError(f"NCDEX (NCFE) account locked: {msg}")

        if "invalid" in msg and ("username" in msg or "password" in msg):
            # Wrong password. Retrying with a new captcha does nothing to fix
            # this and is exactly what burns through the exchange's
            # login-attempt limit and gets the account locked for real.
            print("🔴 WRONG PASSWORD — STOPPING BOT (not retrying)")
            print(f"[NCFE_AUTH_FAIL] {msg}")
            raise RuntimeError(f"NCDEX (NCFE) login failed — wrong password: {msg}")

        if "already" in msg and "login" in msg:
            print("⚠️  '%s'" % msg)
            print("    → NCFE needs a SECOND login to kill the old session. Retrying.")
            _retry_reason = "⚠️ Already logged in — enter new captcha"
        elif "captcha" in msg:
            print("❌ captcha rejected by server — retrying with a new one")
            _retry_reason = "❌ Captcha incorrect — try again"
        elif msg:
            print("⚠️  server message: %s" % msg)
            _retry_reason = f"⚠️ Server error: {msg}"
        else:
            print("⚠️  HTTP 200 with no lblMsgBox — body saved in %s" % DEBUG_DIR)
            _retry_reason = "⚠️ Unknown response — try again"

        # the 200 body IS a fresh login page (new VIEWSTATE + new captcha) — reuse it
        page = login_resp
    else:
        log_error("NCFE login failed after %d attempts — see %s" % (_login_cap, DEBUG_DIR))
        raise RuntimeError(f"NCDEX (NCFE) login failed after {_login_cap} attempts — "
                            f"see {DEBUG_DIR}")

    # -------------------------------------------------------------------
    # STEP 5: FOLLOW THE POST-LOGIN REDIRECT CHAIN
    #         (old code only ever handled /SecurityQs/ and otherwise treated
    #          ANY 302 — including one to /Common/frmSession.aspx — as
    #          success. That silently unauthenticated session is exactly how
    #          a 0KB ClientNetEffectiveDeposits.csv got saved and reported as
    #          downloaded fine.)
    # -------------------------------------------------------------------
    def auto_submit_form(url, resp, label):
        """Submit whatever single form the page carries (used for
        frmSession.aspx — a stale-login-page-session 'Session Expired, click
        OK to go to Login Page' screen, not a concurrent-session conflict).
        Generic on purpose: guessing a specific field/button name here is how
        this exact bug got reintroduced once already."""
        s = soup_of(resp)
        form = s.find("form")
        if not form:
            return None
        action = urllib.parse.urljoin(url, form.get("action") or url)
        data = collect_fields(BeautifulSoup(str(form), "html.parser"))
        for inp in form.find_all("input"):
            nm = inp.get("name")
            if not nm:
                continue
            t = (inp.get("type") or "text").lower()
            if t == "image":
                data.append((nm + ".x", "10"))
                data.append((nm + ".y", "10"))
            elif t in ("submit", "button"):
                data.append((nm, inp.get("value", "")))
        print("    form action      : %s" % action)
        print("    fields posted    : %s" % [k for k, _ in data])
        r = session.post(action, data=data,
                         headers={**FORM_HEADERS, "Referer": url},
                         allow_redirects=False)
        dump("POST %s" % label, r, body_chars=600)
        return r

    def do_security_questions(url, resp):
        print("\n===== STEP 5: SECURITY QUESTION =====")
        s = soup_of(resp)
        af = get_af_token(s)

        def v(name):
            el = s.find("input", {"name": name})
            return el.get("value", "") if el else ""

        data = [
            ("_AntiForgeryToken", af),
            ("__ANTIFORGERY", af),
            ("__ANTIFORGERY", af),
            ("__VIEWSTATE", v("__VIEWSTATE")),
            ("__VIEWSTATEGENERATOR", v("__VIEWSTATEGENERATOR")),
            ("__EVENTVALIDATION", v("__EVENTVALIDATION")),
        ]

        answered = 0
        for inp in s.find_all("input"):
            nm = inp.get("name") or ""
            if "txtanswers" not in nm:
                continue
            idx = nm.rsplit("txtanswers", 1)[-1]
            lbl = s.find(id="ctl00_ContentPlaceHolderCentre_lblQuestion%s" % idx)
            q = lbl.text.strip() if lbl else "(question text not found)"
            ans = "a"   # the site's own security answer — confirmed correct/
                        # accepted against the real site via a manual browser capture
            print("    Q[%s] : %s" % (idx, q))
            print("    A[%s] : %s" % (idx, ans))
            data.append((nm, ans))
            answered += 1

        if answered == 0:
            log_error("No security-answer boxes found — HTML saved in %s" % DEBUG_DIR)
            return None

        btn = s.find("input", {"type": "image", "id": re.compile("imgBtnSubmit")})
        btn_name = btn.get("name") if btn else "ctl00$ContentPlaceHolderCentre$imgBtnSubmit"
        print("    submit button    : %s" % btn_name)
        data.append((btn_name + ".x", "19"))
        data.append((btn_name + ".y", "19"))

        r = session.post(url, data=data,
                         headers={**FORM_HEADERS, "Referer": url},
                         allow_redirects=False)
        dump("POST security answers", r, body_chars=600)
        if r.status_code not in (301, 302):
            log_error("Security step did NOT redirect — answer probably wrong. See %s" % DEBUG_DIR)
        return r

    print("\n===== POST-LOGIN REDIRECT CHAIN =====")
    loc     = login_resp.headers.get("Location")
    referer = login_url
    hops    = 0

    while loc and hops < 8:
        hops += 1
        url = loc if loc.lower().startswith("http") else ROOT + loc
        r = session.get(url, headers={"Referer": referer}, allow_redirects=False)
        dump("HOP %d GET %s" % (hops, url), r, body_chars=0)
        referer = url
        low = url.lower()

        if r.status_code in (301, 302):
            loc = r.headers.get("Location")
            continue

        if "authenticateqs" in low:
            r2 = do_security_questions(url, r)
            if r2 is None:
                raise RuntimeError("NCDEX security question step failed — see " + DEBUG_DIR)
            loc = r2.headers.get("Location") if r2.status_code in (301, 302) else None
            continue

        if "frmsession" in low:
            print("\n⚠️  Server sent /Common/frmSession.aspx (stale login-page session — "
                  "'Session Expired, click OK to go to Login Page'). Auto-submitting its "
                  "own form (whatever fields it actually has) rather than guessing:")
            r2 = auto_submit_form(url, r, "frmSession.aspx")
            if r2 is None:
                log_error("frmSession.aspx has no form — full HTML saved in %s" % DEBUG_DIR)
                raise RuntimeError("NCDEX frmSession.aspx had no form to submit — see " + DEBUG_DIR)
            loc = r2.headers.get("Location") if r2.status_code in (301, 302) else None
            continue

        if "dashboard" in low:
            loc = None
            continue

        print("    ℹ️  landed on an unhandled page: %s" % url)
        loc = None

    # -------------------------------------------------------------------
    # STEP 6: VERIFY WE ARE ACTUALLY LOGGED IN  (old code never checked —
    # ANY 302 anywhere was treated as "FULL LOGIN SUCCESS")
    # -------------------------------------------------------------------
    print("\n===== STEP 6: VERIFY SESSION =====")
    dash = session.get(ROOT + "/Common/Dashboard.aspx", headers={"Referer": referer})
    dump("GET Dashboard", dash, body_chars=0)

    if not logged_in(dash):
        log_error("NOT AUTHENTICATED — every download from here would be an HTML page.")
        log_error("Every response of this run is saved, in order, in: %s" % DEBUG_DIR)
        raise RuntimeError("NCDEX (NCFE) session did not authenticate — see " + DEBUG_DIR)

    log_success("NCFE session authenticated ✅ (RSID=%s)" % session.cookies.get("RSID"))

    # -------------------------------------------------------------------
    # STEP 7: OPEN DEPOSIT PAGE
    # -------------------------------------------------------------------
    print("\n===== STEP 7: OPEN DEPOSIT PAGE =====")
    page_url  = ROOT + "/CMS/ClientNetEffectiveDeposits.aspx"
    page_resp = session.get(page_url, headers={"Referer": ROOT + "/Common/Dashboard.aspx"})
    dump("GET deposit page", page_resp, body_chars=0)

    if not logged_in(page_resp):
        log_error("Deposit page bounced back to login — session died. See %s" % DEBUG_DIR)
        raise RuntimeError("NCDEX (NCFE) deposit page bounced to login — see " + DEBUG_DIR)

    s = soup_of(page_resp)
    export_btn = None
    for inp in s.find_all("input"):
        nm = inp.get("name") or ""
        if "export" in nm.lower():
            export_btn = nm
            break
    print("    export button    : %s" % export_btn)
    if not export_btn:
        log_error("No export control on the page — HTML saved in %s" % DEBUG_DIR)
        raise RuntimeError("NCDEX deposit page has no export control — see " + DEBUG_DIR)

    # -------------------------------------------------------------------
    # STEP 8: EXPORT
    # -------------------------------------------------------------------
    print("\n===== STEP 8: DOWNLOAD FILE =====")
    data = collect_fields(page_resp)
    data.append((export_btn + ".x", "8"))
    data.append((export_btn + ".y", "11"))
    print("    fields posted    : %s" % [k for k, _ in data])

    dl = session.post(page_url, data=data,
                      headers={**FORM_HEADERS, "Referer": page_url},
                      allow_redirects=True)
    dump("POST export", dl, body_chars=400)

    ct   = (dl.headers.get("Content-Type") or "").lower()
    cd   = dl.headers.get("Content-Disposition") or ""
    body = dl.content

    # NCFE sends  Content-Type: application/Excel
    #             Content-Disposition: inline;filename=...csv
    # "inline" is NOT a failure — only an empty body or an HTML page is.
    is_empty = len(body.strip()) == 0
    is_html  = ("html" in ct) or body.lstrip()[:1] == b"<"

    print("    verdict          : empty=%s  html=%s  disposition=%s"
          % (is_empty, is_html, cd.split(";")[0] or "(none)"))

    if is_empty or is_html:
        ext_bad = "html" if is_html else "bin"
        bad = os.path.join(DEBUG_DIR, "EXPORT_FAILED.%s" % ext_bad)
        with open(bad, "wb") as f:
            f.write(body)
        log_error("EXPORT DID NOT RETURN A FILE — refusing to write a 0 KB fake CSV.")
        print("    content-type     : %s" % (ct or "(none)"))
        print("    content-disp     : %s" % (cd or "(none)"))
        print("    bytes            : %s" % len(body))
        m = page_msg(dl)
        if m:
            print("    page message     : %s" % m)
        print("    saved response   : %s" % bad)
        raise RuntimeError("NCDEX export returned no file — see " + bad)

    # ---- server-supplied filename (works for inline; and attachment;) ----
    file_name = "ClientNetEffectiveDeposits.csv"
    m = re.search(r"filename\*?\s*=\s*(?:UTF-8'')?\"?([^\";\r\n]+)\"?", cd, re.I)
    if m:
        file_name = m.group(1).strip()
    print("    server filename  : %s" % file_name)

    out_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    with open(out_path, "wb") as f:
        f.write(body)

    log_success("Saved: %s  (%d bytes, %d lines)"
                % (out_path, len(body), body.count(b"\n") + 1))
    print("    preview:")
    for line in dl.text.splitlines()[:3]:
        print("      " + line[:160])







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

def _is_net_err(e):
    """Connection-level failure (reset / aborted / timeout / cut mid-download) — safe to start again from the beginning."""
    if isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                      requests.exceptions.ChunkedEncodingError, ConnectionError, TimeoutError)):
        return True
    t = str(e).lower()
    return not isinstance(e, RuntimeError) and any(x in t for x in (
        "forcibly closed", "connection aborted", "connection reset", "10054", "10053", "timed out", "remote end closed"))


# 🔹 NMASS DOWNLOADS (CM / FO / CO) — a broken connection restarts the whole login + download after 5 s (max 5 times)
for _dl_try in range(1, 6):
    try:
        run_downloads()
        break
    except Exception as _dl_e:
        if not _is_net_err(_dl_e) or _dl_try >= 5:
            raise
        print(f"⚠️ NMASS download: {type(_dl_e).__name__}: {str(_dl_e)[:140]} — starting the whole login again "
              f"in 5s (restart {_dl_try}/4)...", flush=True)
        time.sleep(5)

# 🔹 NCFE DOWNLOAD (ClientNetEffectiveDeposits)
# This used to swallow the exception and print "ALL DOWNLOADS COMPLETE"
# regardless — so a locked/wrong-password NCDEX account looked identical to
# a real success: no error, exit code 0, dashboard says "downloaded
# successfully". Now it actually reports the failure and exits non-zero.
_ncdex_failed = False
# If NCDEX gives no captcha image, do not just re-fetch the captcha: start the WHOLE login again
# (new session, new login page, new captcha) — up to NCDEX_LOGIN_RESTARTS times. A wrong password or a
# locked account is NOT retried (it raises a different error straight away).
_NCDEX_RESTARTS = int(os.getenv("NCDEX_LOGIN_RESTARTS", "5"))
for _restart in range(1, _NCDEX_RESTARTS + 1):
    try:
        run_ncdex()
        _ncdex_failed = False
        break
    except Exception as e:
        log_error(f"NCDEX error: {e}")
        _ncdex_failed = True
        if not (isinstance(e, NcdexCaptchaUnavailable) or _is_net_err(e)):
            break                                   # wrong password / locked / anything else: never retried
        if _restart < _NCDEX_RESTARTS:
            print(f"🔁 Restarting the whole NCDEX login from the beginning "
                  f"(restart {_restart}/{_NCDEX_RESTARTS - 1}) in 5s...", flush=True)
            time.sleep(5)

if _ncdex_failed:
    log_error("⚠️ ALL DOWNLOADS DID NOT COMPLETE — NCDEX/NCFE step failed (see above)")
    import sys as _mix_sys
    _mix_sys.exit(1)
else:
    log_success("✅ ALL DOWNLOADS COMPLETE — exiting")
