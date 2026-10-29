"""
sa_codes/mcx_deposit_downloader.py
MCX Cash Collateral Deposit Limit (RSK383) downloader.

Credentials (from .env or environment):
  SA_MCX_DEPOSIT_USER     — eclear user ID  (default: 56720CMMKR014139)
  SA_MCX_DEPOSIT_PASSWORD — eclear password

Output: MCX_DEPOSIT_HH.MM.xlsx in output_dir.
        HH.MM is the 12-hour local time when the file is saved (e.g. 01.20).

Can be used two ways:
  1. Imported: result = download_mcx_deposit(output_dir="/some/path")
  2. Standalone: python mcx_deposit_downloader.py [output_dir]
"""

import os
import sys
import re
import base64
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad

# Load .env so credentials are available when imported by other scripts
try:
    from dotenv import load_dotenv as _ld
    _ld(os.getenv("SA_ENV_FILE", r"D:\CLAUDE\rms-copilot\.env"))
except Exception:
    pass

BASE_URL = "https://eclear.mcxccl.com"
LOGIN_URL      = BASE_URL + "/Bancs/login.do?method=authenticatePassward"
TOKEN_URL      = BASE_URL + "/Bancs/login.do?method=authenticateToken"
FINAL_LOGIN_URL = BASE_URL + "/Bancs/login.do?method=Login"


# ── helpers ──────────────────────────────────────────────────────────────────

def _encrypt_value(value: str):
    key = get_random_bytes(16)
    iv  = get_random_bytes(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(value.encode(), AES.block_size))
    return (base64.b64encode(encrypted).decode(),
            base64.b64encode(key + iv).decode())


def _extract_entity(text):
    m = re.search(r"<EntityList>.*</EntityList>", text, re.DOTALL)
    if not m:
        raise ValueError("EntityList missing in response")
    root = ET.fromstring(m.group(0))
    for ent in root.findall(".//EntityDetail"):
        code = ent.find("entityCode").text
        desc = ent.find("shortDescription").text
        if code != "DefaultEntity":
            return code, f"{code}:{desc},"
    raise ValueError("No valid entity found")


def _is_expired(text):
    if ("YOUR SESSION HAS EXPIRED" in text
            or "sessionExpired" in text
            or 'checkType" value="sessionExpired"' in text):
        return True
    # Broader net — the exact strings above cover a plain timeout, but they
    # do NOT reliably fire when someone else logs into the SAME TCS BANCS
    # account elsewhere while this session is mid-poll: the exchange just
    # silently invalidates this session instead of echoing one of those
    # phrases back on the poll's AJAX endpoint. Without this, the poll loop
    # kept treating that bounced response as "no rows yet" and quietly
    # re-polled it for the full 10-minute max_wait, never showing the RSA
    # token popup at all, then just gave up with a generic timeout error.
    # A legitimate poll response is always a small AJAX HTML FRAGMENT (just
    # the grid table) — never a full page shell. If the session got kicked,
    # the request gets bounced straight to the actual login page instead,
    # which starts with <!DOCTYPE>/<html> and carries the login form's own
    # markers — neither of which a genuine grid fragment would ever contain.
    # Catching that here means a concurrent-login kick-out is detected on
    # the very next poll (within `interval` seconds), not after 10 minutes.
    low = text.lower()
    if "<!doctype" in low or "<html" in low:
        return True
    if "dologin.jsp" in low or "method=authmode" in low or 'name="userid"' in low:
        return True
    return False


_AUTO_TOK = None
_LAST_CODE = None


def _auto_mode():
    """Generated RSA code instead of a popup: the dashboard switch must be ON, and the caller unattended or the seed present."""
    try:
        import sys as _sys
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # this script runs from sa_codes/: 'core' is one level up
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from core import mcx_soft_token as _m
        return _m.auto_mode()
    except Exception as _e:
        print(f"[token] automatic code not available ({type(_e).__name__}: {_e}) - the popup will be used.", flush=True)
        return os.getenv("MCX_TOKEN_MODE", "").strip().lower() == "auto"


def _token_popup(attempt, max_attempts, error_msg=None):
    """RSA SecurID token entry — web-based (dashboard UI, no tkinter).
    Writes .token_state to SA_OUTPUT_DIR, polls until dashboard POSTs answer.

    This state file is ALWAYS under SA_OUTPUT_DIR, no matter who calls
    download_mcx_deposit() — Client Prudential Limit's own "MCX Web Allocation"
    button (core/pipelines/cli_mcx_download.py) reuses this exact same function.
    Without a "caller" tag, the dashboard's /sa/token/pending poller had no way
    to tell CLI's request apart from SA's own — it always showed "Short
    Allocation" in the popup even when CLI triggered it. The caller sets
    MCX_DEPOSIT_CALLER ("sa" or "cli") before invoking download_mcx_deposit();
    it's stamped into the state file here so the dashboard can label the popup
    correctly, purely for display — routing (which endpoint to POST the
    answer to) is unaffected, since the file location itself never changes."""
    import json as _tj, os as _tos
    if _auto_mode():
        # unattended (LIVE ALLOCATION): the code comes from the seed, never from a popup - always the NEXT code
        try:
            from core import mcx_soft_token as _mst
            global _AUTO_TOK
            if _AUTO_TOK is None:
                _AUTO_TOK = _mst.load_token()
            global _LAST_CODE
            code = _mst.next_code(_AUTO_TOK, attempt, log=print)
            if code == _LAST_CODE:                  # an instant re-login would send the same code again: let that window pass
                print("[token] that code was already used a moment ago - taking the code of the window after it.", flush=True)
                code = _mst.next_code(_AUTO_TOK, 2, log=print)
            _LAST_CODE = code
            return code
        except Exception as _e:
            print(f"[MCX_TOKEN_FAIL] could not generate the RSA code: {_e}")
            raise RuntimeError(f"RSA code could not be generated: {_e}")
    _dir = _tos.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    _tos.makedirs(_dir, exist_ok=True)
    _sf  = _tos.path.join(_dir, ".token_state")
    _af  = _tos.path.join(_dir, ".token_answer")
    for _f in [_sf, _af]:
        try: _tos.remove(_f)
        except OSError: pass
    _caller = _tos.getenv("MCX_DEPOSIT_CALLER", "sa").strip().lower() or "sa"
    _tj.dump({"state": "PENDING", "attempt": attempt, "max_attempts": max_attempts,
              "error_msg": error_msg, "label": "MCX Deposit", "caller": _caller},
             open(_sf, "w", encoding="utf-8"))
    print(f"🔐 Token required — enter RSA SecurID token in the dashboard "
          f"(Attempt {attempt}/{max_attempts}) …")
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(1)
        try:
            st = _tj.load(open(_sf, encoding="utf-8"))
            if st.get("state") == "ANSWERED":
                answer = open(_af, encoding="utf-8").read().strip()
                for _f in [_sf, _af]:
                    try: _tos.remove(_f)
                    except OSError: pass
                print("✅ Token received via dashboard.")
                return answer
        except Exception:
            pass
    for _f in [_sf, _af]:
        try: _tos.remove(_f)
        except OSError: pass
    raise RuntimeError("Token entry timed out (180 s) — no answer from dashboard.")


def _session_expired_popup():
    """Console-only notice — no tkinter."""
    print("⚠️ MCX Deposit session expired — re-authenticating …")


# ── cross-process lock: only for the TWO callers that share this exact
# TCS BANCS session/.token_state file ──────────────────────────────────────
#
# Short Allocation's own "MCX Deposit" button (core/pipelines/mcx_deposit_download.py)
# and Client Prudential Limit's own "MCX Web Allocation" button
# (core/pipelines/cli_mcx_download.py) BOTH call download_mcx_deposit() below —
# same TCS BANCS login, same .token_state file in SA_OUTPUT_DIR. If both run at
# once they'd genuinely stomp on each other's RSA token popup/session. Nothing
# else needs to know or care about this — NOT Short Allocation's own main
# SA-01..04 run (it never calls this function at all), not CTCL, not Margin
# Shortage. A plain threading.Lock() would NOT work here: each caller runs this
# module in its OWN separate OS subprocess (SA's button runs this file
# standalone via `python mcx_deposit_downloader.py`; CLI's button imports and
# calls this function inside ITS OWN subprocess spawned by
# core.pipeline_box._stream_code()) — a Lock object only guards one process's
# memory, a second process can't see it at all. A lock FILE (created with
# O_EXCL, so the create itself is atomically exclusive across processes) is
# the same mechanism .token_state/.token_answer already use for cross-process
# handoff, just repurposed here as a mutex.
import json as _mcx_json

_MCX_LOCK_FILE = ".mcx_deposit_lock"
_MCX_LOCK_STALE_SECS = 600  # a real download finishes in well under this; if a
                             # previous run crashed/was killed without cleaning
                             # up, don't let its leftover lock wedge things forever


class MCXDepositBusyError(Exception):
    """Raised when the OTHER caller (sa/cli) already has an MCX Deposit
    download in progress. Callers should catch this SPECIFICALLY (before any
    generic Exception handler) so the dashboard shows a clear "already in use"
    message instead of a vague AI-guessed failure."""
    def __init__(self, owner):
        self.owner = owner
        super().__init__(f"MCX Deposit download already in progress (started by '{owner}')")


# Every box that can drive a TCS BANCS download. Each gets its OWN lock file.
_MCX_OWNERS = ("sa", "cli", "probable")


def _mcx_lock_path(owner="sa"):
    """PER-OWNER lock file: .mcx_deposit_lock_sa / _cli / _probable.

    WAS a single shared .mcx_deposit_lock, which made the three boxes mutually
    exclusive — Short Allocation downloading meant PROBABLE's TCS button failed
    outright with "already in progress". Per explicit instruction that is no
    longer wanted: the three download DIFFERENT files, and the TCS
    session-expiry path is already handled in each downloader, so they may run
    at the same time.

    The lock is still worth keeping PER BOX: it stops the same button being
    double-clicked (from two tabs, or two people on the same box) into two
    concurrent downloads writing the same output file."""
    _dir = os.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    os.makedirs(_dir, exist_ok=True)
    return os.path.join(_dir, f"{_MCX_LOCK_FILE}_{(owner or 'sa').strip().lower()}")


def _pid_alive(pid):
    """Is this PID still running? Used to spot an ORPHANED lock instantly.

    Time-based staleness alone was not enough: a killed/crashed owner left the
    lock behind and every other box was refused for the full 10-minute window
    even though nothing was actually downloading. Checking the recorded PID
    answers "is the owner really still there?" directly.

    Fails SAFE (returns True) if it can't tell — an unknown state must be
    treated as "still held", never silently stolen from a live download."""
    try:
        pid = int(pid)
    except Exception:
        return True          # no/bad PID recorded — fall back to time-based staleness
    if pid <= 0:
        return True
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False          # can't open → process is gone
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            # STILL_ACTIVE (259) means running; anything else means it exited
            return bool(ok) and code.value == 259
        os.kill(pid, 0)               # POSIX: signal 0 = existence check only
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                   # exists, just not ours to signal
    except Exception:
        return True                   # unknown → assume held


def _lock_is_dead(path):
    """True when the lock file exists but its owner clearly isn't running:
    either the recorded PID is gone, or the file is older than the stale
    window. Either way it is safe to remove and re-claim."""
    try:
        if not os.path.isfile(path):
            return False
        if (time.time() - os.path.getmtime(path)) > _MCX_LOCK_STALE_SECS:
            return True
        data = _mcx_json.load(open(path, encoding="utf-8"))
        return not _pid_alive(data.get("pid"))
    except Exception:
        return False                  # unreadable → leave it alone, fail safe


def _acquire_mcx_lock(owner):
    """Claim THIS box's own lock. Only ever conflicts with the SAME box — a
    Short Allocation download no longer blocks PROBABLE or CLI."""
    path = _mcx_lock_path(owner)
    # One-time cleanup of the pre-per-owner shared lock file. If an old build
    # left it behind it is now meaningless, but leaving it on disk is untidy
    # and confusing to anyone looking in the folder.
    try:
        _legacy = os.path.join(os.path.dirname(path), _MCX_LOCK_FILE)
        if os.path.isfile(_legacy):
            os.remove(_legacy)
    except OSError:
        pass
    try:
        if _lock_is_dead(path):
            os.remove(path)
    except OSError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise MCXDepositBusyError(owner)
    with os.fdopen(fd, "w") as f:
        _mcx_json.dump({"owner": owner, "pid": os.getpid(),
                         "started_at": datetime.now().isoformat()}, f)


def _release_mcx_lock(owner=None):
    """Release THIS box's lock. `owner` defaults to whatever this process was
    invoked as (MCX_DEPOSIT_CALLER), so the existing no-arg call sites in
    download_mcx_deposit()'s finally still release the right file."""
    if owner is None:
        owner = os.getenv("MCX_DEPOSIT_CALLER", "sa")
    try:
        os.remove(_mcx_lock_path(owner))
    except OSError:
        pass


def force_release_mcx_lock_if_owner(owner):
    """Called by the Stop handlers (api.py's /cli/stop and /sa/mcx-deposit-stop)
    right after force-killing a subprocess (taskkill /F /T). A killed process
    never gets to run its own `finally`/_release_mcx_lock() — without this, a
    Stop click would leave the lock file behind and the NEXT click of the same
    button would wrongly report "already in use" for up to _MCX_LOCK_STALE_SECS,
    even though nothing is actually running anymore.

    Now that locks are per-owner the "is it really theirs?" check is inherent —
    each box has its own file and can only ever delete its own."""
    try:
        os.remove(_mcx_lock_path(owner))
    except OSError:
        pass


def mcx_lock_status(owner=None):
    """Read-only peek — used by the dashboard to grey out a button live while
    ITS OWN box is mid-download.

    owner given  → status of just that box's lock.
    owner=None   → first busy box found (back-compat for core.lock_manager's
                   snapshot(), which reports one 'TCS BANCS session' row).

    Since the lock became per-box, a busy result here means "THIS box is
    downloading", not "somebody somewhere is" — the three no longer block each
    other (they fetch different files and each handles TCS session-expiry on
    its own)."""
    def _one(o):
        path = _mcx_lock_path(o)
        try:
            os.stat(path)
            # Same liveness test _acquire_mcx_lock() uses, so the dashboard never
            # greys a button out for a lock that acquiring would have just cleared.
            if _lock_is_dead(path):
                return {"busy": False}
            data = _mcx_json.load(open(path, encoding="utf-8"))
            return {"busy": True, "owner": data.get("owner", o),
                    "started_at": data.get("started_at")}
        except FileNotFoundError:
            return {"busy": False}
        except Exception:
            return {"busy": False}

    if owner:
        return _one(owner)
    for o in _MCX_OWNERS:
        st = _one(o)
        if st.get("busy"):
            return st
    return {"busy": False}


# ── main download function ────────────────────────────────────────────────────

def download_mcx_deposit(output_dir=None):
    """Whole MCX login + download. A broken connection ("connection forcibly closed", reset, timeout) starts the
    whole login again from the beginning after 5 s, up to 5 times. A refused password is NOT retried."""
    import importlib.util as _iu          # load core/net_retry.py by path — works however this script is started
    _spec = _iu.spec_from_file_location("net_retry_standalone", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core", "net_retry.py"))
    net_retry = _iu.module_from_spec(_spec); _spec.loader.exec_module(net_retry)
    return net_retry.run(lambda: _download_mcx_deposit_once(output_dir), "MCX deposit", print)


def _download_mcx_deposit_once(output_dir=None):
    """
    Download RSK383 (Cash Collateral Deposit Limit) from eclear.mcxccl.com.

    Reads credentials from env:
      SA_MCX_DEPOSIT_USER, SA_MCX_DEPOSIT_PASSWORD

    Saves to output_dir as MCX_DEPOSIT_HH.MM.xlsx (12-hr local time).

    Returns the saved file path on success, None on failure.
    """
    USER_ID  = os.getenv("SA_MCX_DEPOSIT_USER",     "56720CMMKR014139").strip()
    PASSWORD = os.getenv("SA_MCX_DEPOSIT_PASSWORD",  "").strip()

    if not PASSWORD:
        print("❌ SA_MCX_DEPOSIT_PASSWORD not set in .env — cannot download MCX Deposit")
        return None

    # Claim the cross-process lock BEFORE touching the network/session — raises
    # MCXDepositBusyError uncaught (deliberately, so it does NOT fall into the
    # generic `except Exception` further below and get mistaken for a real
    # failure) if SA's own button and CLI's button are both trying to use this
    # at once. See the lock helpers' comment above for why this is scoped to
    # ONLY these two callers, not any other pipeline/box.
    _mcx_caller = os.getenv("MCX_DEPOSIT_CALLER", "sa").strip().lower() or "sa"
    _acquire_mcx_lock(_mcx_caller)

    if output_dir is None:
        output_dir = os.getenv("SA_OUTPUT_DIR", os.getcwd())
    os.makedirs(output_dir, exist_ok=True)

    # File name uses the current local time (12-hour clock, no AM/PM suffix)
    out_filename = f"MCX_DEPOSIT_{datetime.now().strftime('%I.%M')}.xlsx"
    out_path = os.path.join(output_dir, out_filename)

    AJAX_H = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE_URL,
        "Referer": BASE_URL + "/Bancs/common/dologin.jsp?method=authMode&JSLoadingMode=null",
    }
    LOGIN_H = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": BASE_URL,
        "Referer": BASE_URL + "/Bancs/common/dologin.jsp",
    }
    NAV_H = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": BASE_URL,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": BASE_URL + "/Bancs/login.do?method=Login&availHeight=744&availWidth=1522",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "iframe",
        "Upgrade-Insecure-Requests": "1",
    }
    AJAX2_H = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": BASE_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": BASE_URL + "/Bancs/common/welcome.jsp",
    }

    def _do_login(sess, initial_err_msg=None):
        """Full login flow. Returns rndaak string.
        `initial_err_msg` seeds the message shown on the FIRST token popup —
        used by _do_relogin() so a mid-poll session-expiry re-login shows
        "session expired, someone else may have logged in" instead of a bare
        "Attempt 1 of 3" that looks identical to a brand-new login, which was
        confusing (looked like the very first request, not a re-auth caused by
        the session dying underneath a run that was already in progress)."""
        print("\n🌐 Opening session...")
        sess.get(BASE_URL)
        sess.post(BASE_URL + "/Bancs/login.do?method=authMode", headers=AJAX_H)

        print("🔐 PASSWORD STEP")
        pw_enc, ign = _encrypt_value(PASSWORD)
        pw_resp = sess.post(LOGIN_URL, headers=AJAX_H, data={
            "userId": USER_ID + "\t", "password": pw_enc,
            "lng": "1", "ignoreFlag": ign,
        })
        # Confirmed via test_mcx_deposit_login.py: a wrong password shows up
        # right here as error_div1 / "INVALID_LOGIN_OR_PASSWORD" — the code
        # used to ignore this response entirely and just carry on to ask for
        # an RSA token even when the password was already known to be wrong.
        if "INVALID_LOGIN_OR_PASSWORD" in pw_resp.text or "error_div1" in pw_resp.text:
            print("[MCX_DEPOSIT_AUTH_FAIL] wrong password — update SA_MCX_DEPOSIT_PASSWORD in Credentials")
            raise RuntimeError("MCX Deposit login failed — wrong password (INVALID_LOGIN_OR_PASSWORD)")

        print("🔴 TOKEN STEP")
        entity = entity_codes = None
        err_msg = initial_err_msg
        # unattended mode: at most 2 wrong codes, then stop (a 3rd could lock the account)
        MAX_RETRY = 2 if _auto_mode() else 3
        for attempt in range(1, MAX_RETRY + 1):
            token = _token_popup(attempt, MAX_RETRY, err_msg)
            t_enc, t_ign = _encrypt_value(token)
            resp = sess.post(TOKEN_URL, headers=AJAX_H, data={
                "userId": USER_ID + "\t", "password": t_enc,
                "lng": "1", "ignoreFlag": t_ign,
            })
            try:
                entity, entity_codes = _extract_entity(resp.text)
                print(f"✅ Entity: {entity}")
                break
            except Exception:
                err_msg = "❌ Invalid token. Please try again."
                if attempt == MAX_RETRY:
                    if MAX_RETRY == 2:
                        print("[MCX_TOKEN_FAIL] the RSA code was refused 2 times - stopped (no more tries, to avoid locking the account)")
                    raise RuntimeError("Max token attempts reached")

        print("🚀 FINAL LOGIN")
        fp_enc, f_ign = _encrypt_value(PASSWORD)
        resp3 = sess.post(FINAL_LOGIN_URL, headers=LOGIN_H, data={
            "method": "Login", "availHeight": "744", "availWidth": "1522",
            "isArchival": "false", "userEntityCodes": entity_codes,
            "lng": "1", "userId": USER_ID,
            "ignoreFlag": f_ign, "password": fp_enc,
            "dummy": "FltM/sA2fPfqsjtM55oLOQ==", "entity": entity,
        })
        print(f"📥 Login: {resp3.status_code}")

        rndaak = None
        for pat in [r"name=['\"]rndaak['\"][^>]*value=['\"]([^'\"]+)",
                    r"rndaak['\"]?\s*:\s*['\"]([^'\"]+)"]:
            m = re.search(pat, resp3.text)
            if m:
                rndaak = m.group(1)
                break
        if not rndaak:
            raise RuntimeError("rndaak not found in final-login response")
        print("✅ rndaak:", rndaak)
        return rndaak

    def _post_login_flow(sess, rndaak):
        sess.post(BASE_URL + "/Bancs/common/CommonImpl.jsp", headers=NAV_H, data={"rndaak": rndaak})
        sess.post(BASE_URL + "/Bancs/common/welcome.jsp",   headers=NAV_H, data={"rndaak": rndaak})
        sess.post(BASE_URL + "/Bancs/common/layout.save",   headers=AJAX2_H, data={"events": "", "rndaak": rndaak})
        r = sess.post(BASE_URL + "/Bancs/common/welcome.jsp", headers=NAV_H, data={"rndaak": rndaak})
        print(f"🎉 Session active: {r.status_code}")

    def _init_arch(sess, rndaak):
        ARCH_URL = BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do"
        resp = sess.post(ARCH_URL, headers={
            "User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded",
            "Origin": BASE_URL, "Referer": BASE_URL + "/Bancs/common/PageLoader.html",
        }, data={
            "app": "bancsapp?prefix=/ARCHGUI", "page": "/ar_w_DataExport.do",
            "confViewSize": "50", "reqType": "0", "mode": "DEF_MODE",
            "parentName": "blank", "browserType": "IE55",
            "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI",
            "over_ride_button": "Cancel", "AvlHt": "678", "AvlWd": "914",
            "tblWd": "898", "GuiBrowserInst": "0", "theme": "black",
            "pageId": "ar_w_DataExport", "wintitle": "Download Exported Files",
            "availHeight": "816", "availWidth": "1536", "tabId": "panel4",
            "rndaak": rndaak, "reqtoken": "", "parentToChildData": "",
        })
        print(f"📥 ARCH INIT: {resp.status_code}")
        return resp

    def _do_relogin(sess, rndaak_ref, wait_s=None):
        """Re-login when session expires. Returns (new_sess, new_rndaak, new_tab_id).

        Was: only re-did login + post_login_flow, then handed the OLD tab_id
        (from before the session died) straight back to the poll loop. The poll
        request kept succeeding (200 OK, no [MCX_DEPOSIT_AUTH_FAIL]) but the
        export-list grid came back completely empty every time afterward — the
        ARCH DataExport grid's "tabId" is server-side state scoped to a specific
        login session, so re-using the previous session's tabId against a
        brand-new session context never had any rows to return. The fix:
        re-run _init_arch() with the fresh rndaak, exactly like the very first
        login does, and hand the poll loop the tabId THAT call returns."""
        print("\n🚨 SESSION EXPIRED — RE-LOGIN")
        _session_expired_popup()
        if _auto_mode() and os.getenv("MCX_TOKEN_MODE", "").strip().lower() == "auto":
            # UNATTENDED run only (Short Allocation auto run, Live Allocation, MCX Cash): somebody else logged in and kicked
            # this session out - do NOT fill a code instantly, wait (default 4 min). A run started by hand fills the next code at once.
            from core import mcx_soft_token as _mst
            if wait_s is None or wait_s > 0:       # wait_s=0: kicked out BEFORE the export - log in again at once with the next code
                _mst.relogin_wait(log=print, seconds=wait_s)
        new_sess = requests.Session()
        new_rndaak = _do_login(
            new_sess,
            initial_err_msg="⚠️ Session expired (it may have timed out, or someone else "
                            "logged in with this account elsewhere) — please enter your "
                            "RSA token again to retry.")
        _post_login_flow(new_sess, new_rndaak)
        # replace caller's session object contents
        sess.cookies.update(new_sess.cookies)
        sess.headers.update(new_sess.headers)
        # Re-establish the ARCH DataExport grid context for the new session —
        # without this the very next poll call succeeds but returns zero rows.
        resp_arch = _init_arch(new_sess, new_rndaak)
        tab_m = re.search(r'id="tabId"[^>]*value="([^"]+)"', resp_arch.text)
        new_tab_id = tab_m.group(1) if tab_m else None
        if new_tab_id:
            print(f"✅ Re-acquired TAB ID after relogin: {new_tab_id}")
        else:
            print("⚠️ Could not find a fresh tabId after relogin — reusing the previous one "
                  "(the export list may come back empty if the server doesn't recognize it)")
        return new_sess, new_rndaak, new_tab_id

    try:
        # ── state: which export is ours (so a retry NEVER presses Export again for the same capture) ──
        _STATE_PATH = os.getenv("MCX_EXPORT_STATE_FILE", "").strip()

        def _state_read():
            if not _STATE_PATH:
                return None
            try:
                st = _mcx_json.load(open(_STATE_PATH, encoding="utf-8"))
                age_h = (time.time() - float(st.get("saved_at", 0))) / 3600.0
                if (st.get("date") == datetime.now().strftime("%Y%m%d") and not st.get("done")
                        and st.get("stamp") and age_h < 4):
                    return st
            except Exception:
                pass
            return None

        def _state_write(**kw):
            if not _STATE_PATH:
                return
            try:
                st = {}
                try:
                    st = _mcx_json.load(open(_STATE_PATH, encoding="utf-8"))
                except Exception:
                    st = {}
                st.update(kw)
                st["saved_at"] = time.time()
                st.setdefault("date", datetime.now().strftime("%Y%m%d"))
                os.makedirs(os.path.dirname(_STATE_PATH) or ".", exist_ok=True)
                with open(_STATE_PATH, "w", encoding="utf-8") as _f:
                    _mcx_json.dump(st, _f)
            except Exception:
                pass

        S = {"sess": None, "rndaak": None, "tab": None}
        S["sess"] = requests.Session()
        S["rndaak"] = _do_login(S["sess"])
        _post_login_flow(S["sess"], S["rndaak"])

        RSK_URL = BASE_URL + "/Bancs/RSK/RSK383.do"
        EXPORT_SEARCH_URL = BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do"
        FETCH_H = {
            "User-Agent": "Mozilla/5.0", "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL, "Referer": RSK_URL,
        }
        DL_HEADERS = {
            "User-Agent": "Mozilla/5.0", "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
            "Referer": BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do?app=bancsapp?prefix=/ARCHGUI",
        }
        auto_wait = os.getenv("MCX_TOKEN_MODE", "").strip().lower() == "auto"
        try:
            AFTER_EXPORT_WAIT = int(os.getenv("MCX_EXPORT_RELOGIN_WAIT_SEC", "600"))
            POLL_BUDGET = int(os.getenv("MCX_EXPORT_POLL_SEC", "600"))
        except ValueError:
            AFTER_EXPORT_WAIT, POLL_BUDGET = 600, 600
        INTERVAL = 15

        def relogin(wait_s):
            ns, nr, nt = _do_relogin(S["sess"], S["rndaak"], wait_s=wait_s)
            S["sess"], S["rndaak"] = ns, nr
            if nt:
                S["tab"] = nt

        class _Kicked(Exception):
            """Our session was ended (someone else logged in with this account)."""

        class _Exp:                       # what we know about OUR export
            trigger = None                # datetime the Export button was pressed
            stamp = ""                    # YYYYMMDDHHMMSS - the last part of OUR file's name
            target = None                 # file name, once seen in the list

        # ── step 1: search + press Export. Kicked out BEFORE the export is made: log in again AT ONCE (next code), redo it ──
        def search_and_export():
            for attempt in range(1, 4):
                try:
                    print("\n🌐 Opening RSK383...")
                    today_api = datetime.now().strftime("%Y%m%d")
                    today_ui = datetime.now().strftime("%d/%m/%Y")
                    today_encoded = today_ui.replace("/", "%2F")
                    r0 = S["sess"].post(RSK_URL, headers={
                        "User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": BASE_URL, "Referer": BASE_URL + "/Bancs/common/PageLoader.html",
                    }, data={
                        "app": "bancsapp?prefix=/RSK", "page": "/RSK383.do",
                        "confViewSize": "50", "reqType": "0", "mode": "DEF_MODE",
                        "parentName": "blank", "browserType": "IE55",
                        "GuiWinName": "RSK383", "compname": "RSK",
                        "over_ride_button": "Cancel", "AvlHt": "744", "AvlWd": "1481",
                        "tblWd": "1465", "GuiBrowserInst": "0", "theme": "black",
                        "pageId": "RSK383", "wintitle": "Query Detailed Limit",
                        "availHeight": "816", "availWidth": "1536", "tabId": "panel2",
                        "rndaak": S["rndaak"], "reqtoken": "", "parentToChildData": "",
                    })
                    search_base = {
                        "MCB_SearchWC_wca_clearingMemberCode": "",
                        "propertyMap(MCB_SearchWC_wca_clearingMemberCode_op)": "=",
                        "MCB_SearchWC_wca_tradingMemberCode": "",
                        "propertyMap(MCB_SearchWC_wca_tradingMemberCode_op)": "=",
                        "MCB_SearchWC_wca_uCCcode": "",
                        "propertyMap(MCB_SearchWC_wca_uCCcode_op)": "=",
                        "MCB_SearchWC_wca_clientName": "",
                        "propertyMap(MCB_SearchWC_wca_clientName_op)": "=",
                        "MCB_SearchWC_wca_fromDate": today_api,
                        "propertyMap(MCB_SearchWC_wca_fromDate_op)": "=",
                        "MCB_SearchWC_wca_toDate": today_api,
                        "propertyMap(MCB_SearchWC_wca_toDate_op)": "=",
                        "MCB_SearchWC_wca_clearingMemberName": "",
                        "propertyMap(MCB_SearchWC_wca_clearingMemberName_op)": "=",
                        "MCB_SearchWC_wca_tradingMemberName": "",
                        "propertyMap(MCB_SearchWC_wca_tradingMemberName_op)": "=",
                        "MCB_SearchWC_wca_currency": "",
                        "propertyMap(MCB_SearchWC_wca_currency_op)": "=",
                        "GRID_RESPONSE": "RSK383_Table", "operationType": "ET",
                        "searchClicked": "true", "PageNum": "0",
                        "service": "queryCashCollDetLimit", "windowName": "RSK383",
                        "MODE": "DEF_MODE", "compName": "RSK",
                        "lgbinst": "0", "WRITE_APP": "true",
                        "REQ_TYPE": "0", "reqType": "1", "butvalue": "50",
                        "browserType": "IE55", "GuiWinName": "ar_w_DataExport",
                        "compname": "ARCHGUI",
                        "sQuery": (f"Transaction From Date  Equals  {today_encoded}"
                                   f" AND Transaction To Date  Equals  {today_encoded}"),
                        "MCBrowserEventInformation":
                            "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
                        "IXHRts": str(int(time.time() * 1000)), "XHR": "true",
                        "rndaak": S["rndaak"], "theme": "black", "tabId": "panel2",
                        "mode": "DEF_MODE", "GuiBrowserInst": "0",
                        "parentName": "blank", "confViewSize": "50",
                    }
                    print("🔍 Running search...")
                    sr = S["sess"].post(RSK_URL, headers=FETCH_H, data=search_base)
                    if _is_expired(sr.text):
                        raise _Kicked()
                    if "RSK383_Table" not in sr.text:
                        raise RuntimeError("Search failed — RSK383_Table not in response")
                    print("✅ Search successful")
                    trig = datetime.now()
                    export_payload = {**search_base}
                    export_payload.update({
                        "Export": "Excel", "enableBatchForExport": "yes", "noPgsToExport": "100",
                        "dateStamp": trig.strftime("%Y%m%d"),
                        "timeStamp": trig.strftime("%H:%M:%S"),
                        "PageNum": "1", "undefined": "",
                        "MCBrowserEventInformation":
                            "sourceElementName~|~undefined#*#eventType~|~callListExport#*#butId~|~null",
                        "IXHRts": str(int(time.time() * 1000)), "XHR": "true",
                    })
                    er = S["sess"].post(RSK_URL, headers=FETCH_H, data=export_payload)
                    if _is_expired(er.text):
                        raise _Kicked()
                    if "ExportBatchSuccess" not in er.text:
                        raise RuntimeError("Export trigger failed — ExportBatchSuccess not in response")
                    _Exp.trigger = trig
                    _Exp.stamp = trig.strftime("%Y%m%d%H%M%S")
                    _Exp.target = None
                    _state_write(stamp=_Exp.stamp, target="", done=False, date=trig.strftime("%Y%m%d"))
                    print(f"✅ Export triggered [MCX_EXPORT] time {_Exp.stamp} - this is the file we will wait for")
                    return
                except _Kicked:
                    print(f"🚨 Session ended BEFORE the export was made (try {attempt}/3) — logging in again at once with the next code")
                    relogin(0)
            raise RuntimeError("Session kept ending before the export could be made (3 tries)")

        # ── step 2: the list of exported files - EVERY page, not only the first ──
        def _poll_payload(tab_id):
            d = _Exp.trigger or datetime.now()
            return {
                "MCB_SearchWC_wca_EXPORT_WINDOW_NAME": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_WINDOW_NAME_op)": "=",
                "MCB_SearchWC_wca_EXPORT_TABLE_NAME": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TABLE_NAME_op)": "=",
                "MCB_SearchWC_wca_EXPORT_DATE_FROM": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_DATE_FROM_From)": d.strftime("%d/%m/%Y"),
                "propertyMap(MCB_SearchWC_wca_EXPORT_DATE_FROM_To)": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_DATE_FROM_op)": "=",
                "MCB_SearchWC_wca_EXPORT_TIME_FROM": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TIME_FROM_From)": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TIME_FROM_To)": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TIME_FROM_op)": "=",
                "MCB_SearchWC_wca_EXPORT_DATE_TO": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_DATE_TO_From)": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_DATE_TO_To)": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_DATE_TO_op)": "=",
                "MCB_SearchWC_wca_EXPORT_TIME_TO": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TIME_TO_From)": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TIME_TO_To)": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TIME_TO_op)": "=",
                "MCB_SearchWC_wca_EXPORT_QUERY": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_QUERY_op)": "=",
                "MCB_SearchWC_wca_EXPORT_TYPE": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TYPE_op)": "=",
                "MCB_SearchWC_wca_EXPORT_TIMESTAMP": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TIMESTAMP_From)": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TIMESTAMP_To)": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TIMESTAMP_op)": "=",
                "MCB_SearchWC_wca_EXPORT_STATUS": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_STATUS_op)": "=",
                "GRID_RESPONSE": "ar_w_DataExport_Table", "operationType": "ET",
                "searchClicked": "true", "PageNum": "0",
                "service": "ARET8001GetExportData", "windowName": "ar_w_DataExport",
                "MODE": "DEF_MODE", "compName": "ARCHGUI",
                "lgbinst": "0", "WRITE_APP": "true",
                "REQ_TYPE": "0", "reqType": "1",
                "sQuery": "  Equals  " + d.strftime("%Y%m%d"),
                "AvlWd": "1280", "tblWd": "1280",
                "MCBrowserEventInformation":
                    "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
                "IXHRts": str(int(time.time() * 1000)), "XHR": "true",
                "rndaak": S["rndaak"], "theme": "black", "tabId": tab_id,
                "mode": "DEF_MODE", "GuiBrowserInst": "0", "parentName": "blank",
                "confViewSize": "50", "browserType": "IE55",
                "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI",
                "over_override_button": "Cancel",
            }

        def _next_payload(tab_id, page):
            d = _Exp.trigger or datetime.now()
            return {
                "tabId": tab_id, "GuiBrowserInst": "0", "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI",
                "xmlReq": "YES", "reqType": "1", "confViewSize": "50", "PageNum": "0",
                "butvalue": "8", "selectedRow_ar_w_DataExport": str(49 + 50 * (page - 1)),
                "tblurl": "ar_w_DataExport_Table",
                "MCBrowserEventInformation": "sourceElementName~|~next#*#eventType~|~click#*#butId~|~8",
                "rndaak": S["rndaak"], "theme": "black",
                "sQuery": "  Equals  " + d.strftime("%Y%m%d"),
                "AvlWd": "1208", "tblWd": "1186",
                "RowNumKeyForPrevNxt": str(page), "PrevOrNext": "N",
            }

        def _parse_rows(html):
            out = []
            for tr in BeautifulSoup(html, "html.parser").select("table tbody tr"):
                tds = tr.find_all("td")
                if len(tds) < 7:
                    continue
                out.append({"query": tds[2].text.strip().replace("%2F", "/"), "status": tds[4].text.strip(),
                            "ts": tds[5].text.strip(), "name": tds[6].text.strip()})
            return out

        def fetch_all_rows():
            """Every row of the export list (page 1, 2, 3 ... until a page brings nothing new). Raises _Kicked if the session ended."""
            r1 = S["sess"].post(EXPORT_SEARCH_URL, headers=DL_HEADERS, data=_poll_payload(S["tab"]))
            if _is_expired(r1.text):
                raise _Kicked()
            rows = _parse_rows(r1.text)
            seen = {(r["ts"], r["name"]) for r in rows}
            pages = [len(rows)]
            page = 1
            while page < 20:
                rn = S["sess"].post(EXPORT_SEARCH_URL, headers=DL_HEADERS, data=_next_payload(S["tab"], page))
                if "YOUR SESSION HAS EXPIRED" in rn.text or "sessionExpired" in rn.text:
                    raise _Kicked()
                new = [r for r in _parse_rows(rn.text) if (r["ts"], r["name"]) not in seen]
                if not new:
                    break
                for r in new:
                    seen.add((r["ts"], r["name"]))
                rows.extend(new)
                pages.append(len(new))
                page += 1
            return rows, pages

        def find_ours(rows):
            """OUR file: its name ends with the time we pressed Export. Falls back to the time column (+-15 s)."""
            if _Exp.target:
                for r in rows:
                    if r["name"] == _Exp.target:
                        return r
            cands = [r for r in rows if _Exp.stamp and _Exp.stamp in r["name"]]
            good = [r for r in cands if "Transaction From Date" in r["query"]]
            if good:
                return good[0]
            if len(cands) == 1:
                return cands[0]
            for r in rows:                                   # older rule (time column), same as before
                try:
                    ft = datetime.strptime(r["ts"], "%d/%m/%Y %H:%M:%S")
                    if abs((ft - _Exp.trigger).total_seconds()) <= 15 and "Transaction From Date" in r["query"]:
                        return r
                except Exception:
                    continue
            return None

        def wait_for_file(budget_s):
            """Look every 15 s (all pages) until OUR file says Generated. Returns 'ready' | 'timeout'; raises _Kicked."""
            elapsed, last_msg = 0, ""
            while elapsed <= budget_s:
                print(f"\n🔄 Checking export queue... (elapsed: {elapsed}s)")
                rows, pages = fetch_all_rows()
                ours = find_ours(rows)
                if ours:
                    if not _Exp.target:
                        _Exp.target = ours["name"]
                        _state_write(target=_Exp.target)
                        print(f"  🎯 LOCKED: {_Exp.target}")
                    msg = f"[MCX_LIST] {len(rows)} files on {len(pages)} page(s) {pages} - our file {ours['name']}: {ours['status']}"
                    if "Generated" in ours["status"]:
                        print(msg)
                        print("🎉 FILE READY")
                        return "ready"
                else:
                    msg = f"[MCX_LIST] {len(rows)} files on {len(pages)} page(s) {pages} - our file (export {_Exp.stamp}) is not in the list yet"
                if msg != last_msg or elapsed % 60 == 0:
                    print(msg)
                    last_msg = msg
                time.sleep(INTERVAL)
                elapsed += INTERVAL
            return "timeout"

        def open_download_page():
            resp = _init_arch(S["sess"], S["rndaak"])
            tm = re.search(r'id="tabId"[^>]*value="([^"]+)"', resp.text)
            if not tm:
                raise RuntimeError("tabId not found in ARCH response")
            S["tab"] = tm.group(1)
            print(f"✅ TAB ID: {S['tab']}")

        def download_to(file_name, dest):
            """checkForFile (up to 10 x 5 s) then download `file_name` to `dest`. Raises _Kicked if the session ended."""
            check_url = (f"{BASE_URL}/Bancs/DownloadFileAction1.do"
                         f"?fileName={file_name}&action=checkForFile")
            file_ready = False
            for i in range(10):
                print(f"🔍 Checking download availability ({i+1}/10)...")
                rc = S["sess"].post(check_url, headers=DL_HEADERS, data={"rndaak": S["rndaak"]})
                if _is_expired(rc.text):
                    raise _Kicked()
                if "FileExists" in rc.text:
                    file_ready = True
                    break
                time.sleep(5)
            if not file_ready:
                raise RuntimeError("File not available for download after 10 checks")
            dl_url = (f"{BASE_URL}/Bancs/DownloadFileAction1.do"
                      f"?fileName={file_name}&action=downloadFile")
            print("⬇️ Downloading...")
            dr = S["sess"].post(dl_url, headers=DL_HEADERS, data={"rndaak": S["rndaak"]}, stream=True)
            if dr.status_code != 200:
                raise RuntimeError(f"Download HTTP {dr.status_code}")
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                for chunk in dr.iter_content(8192):
                    if chunk:
                        f.write(chunk)
            if os.path.exists(dest):
                os.remove(dest)
            os.replace(tmp, dest)

        def catch_up_earlier_exports():
            """Files exported by EARLIER captures of today that were still 'In Queue' when they gave up: if they are Generated now,
            download them into their own folder (the caller passes MCX_PENDING_JSON). One look, no waiting."""
            raw = os.getenv("MCX_PENDING_JSON", "").strip()
            if not raw:
                return
            try:
                pend = _mcx_json.loads(raw)
            except Exception:
                return
            pend = [p for p in pend if p.get("stamp") and p.get("out") and not os.path.exists(p["out"])]
            if not pend:
                return
            try:
                open_download_page()
                rows, pages = fetch_all_rows()
            except _Kicked:
                print("[MCX_CATCHUP] session ended while looking for earlier files - they stay pending")
                return
            for p in pend:
                stamp, out = p["stamp"], p["out"]
                cands = [r for r in rows if stamp in r["name"] and "Transaction From Date" in r["query"]] \
                    or [r for r in rows if stamp in r["name"]]
                if len(cands) != 1 and not any("Transaction From Date" in r["query"] for r in cands):
                    print(f"[MCX_CATCHUP] folder {p.get('slot')}: its earlier export ({stamp}) is not in the list")
                    continue
                r = cands[0]
                if "Generated" not in r["status"]:
                    print(f"[MCX_CATCHUP] folder {p.get('slot')}: earlier file {r['name']} is still '{r['status']}' - will look again next time")
                    continue
                try:
                    download_to(r["name"], out)
                    print(f"[MCX_CATCHUP] folder {p.get('slot')}: earlier file {r['name']} is ready - saved to {out}")
                    try:
                        sp = os.path.join(os.path.dirname(out), ".mcx_export_state.json")
                        st = _mcx_json.load(open(sp, encoding="utf-8"))
                        st["done"] = True
                        st["target"] = r["name"]
                        _mcx_json.dump(st, open(sp, "w", encoding="utf-8"))
                    except Exception:
                        pass
                except _Kicked:
                    print("[MCX_CATCHUP] session ended while downloading an earlier file - it stays pending")
                    return
                except Exception as e:
                    print(f"[MCX_CATCHUP] folder {p.get('slot')}: could not download {r['name']}: {e}")

        # ── step 3: get our export made (or pick up the one an earlier try already made) ──
        catch_up_earlier_exports()
        resume = _state_read()
        if resume:
            _Exp.stamp = resume["stamp"]
            _Exp.target = resume.get("target") or None
            _Exp.trigger = datetime.strptime(_Exp.stamp, "%Y%m%d%H%M%S")
            print(f"♻️ [MCX_EXPORT] an earlier try already exported this capture (export time {_Exp.stamp}) - NOT exporting again, looking for that file")
            open_download_page()
            try:
                rows0, pages0 = fetch_all_rows()
            except _Kicked:
                rows0, pages0 = [], []
            if rows0 and find_ours(rows0) is None:
                print("⚠️ [MCX_EXPORT] that earlier export is not in the list any more - making a new one")
                _state_write(done=True)
                resume = None
                _Exp.target = None
        if not resume:
            search_and_export()
            open_download_page()

        # ── step 4: wait for it. Kicked out AFTER the export: wait, log in again (next code), look for the SAME file ──
        kicks = 0
        while True:
            try:
                res = wait_for_file(POLL_BUDGET)
            except _Kicked:
                kicks += 1
                if kicks > 3:
                    raise RuntimeError(f"session kept expiring after {kicks - 1} re-logins — our export "
                                       f"({_Exp.stamp}) is still on MCX; the next try will look for it again")
                print(f"🚨 Session ended AFTER the export (kick {kicks}/3) — the file we exported ({_Exp.stamp}) stays on MCX. "
                      f"Waiting, then logging in again to fetch THAT file (no new export).")
                relogin(AFTER_EXPORT_WAIT)
                continue
            if res == "ready":
                break
            if not _Exp.target:
                raise RuntimeError(f"RSK383 file never appeared in export list (export {_Exp.stamp}, after {POLL_BUDGET}s, all pages checked)")
            raise RuntimeError(f"RSK383 not generated within {POLL_BUDGET}s (file {_Exp.target})")
        target_file = _Exp.target

        # ── step 5: download that exact file (if the session ends here too: wait, log in, try again) ──
        dl_try = 0
        while True:
            try:
                download_to(target_file, out_path)
                break
            except _Kicked:
                dl_try += 1
                if dl_try > 3:
                    raise RuntimeError("session kept expiring while downloading — the file is still on MCX; the next try will fetch it")
                print(f"🚨 Session ended while downloading (try {dl_try}/3) — waiting, then logging in again")
                relogin(AFTER_EXPORT_WAIT)

        _state_write(done=True)
        print(f"🎉 MCX DEPOSIT DOWNLOADED: {out_path}")
        _release_mcx_lock()
        return out_path

    except Exception as exc:
        print(f"❌ MCX Deposit download failed: {exc}")
        _release_mcx_lock()
        return None


# ── standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    out_dir = (sys.argv[1] if len(sys.argv) > 1
               else os.getenv("SA_OUTPUT_DIR", os.getcwd()))
    try:
        result = download_mcx_deposit(output_dir=out_dir)
    except MCXDepositBusyError as busy:
        # Tagged distinctly (WITH the actual owner) so
        # core/pipelines/mcx_deposit_download.py's stream can recognize this
        # specific case and show a clean, ACCURATE "already in use by X"
        # message instead of guessing via the AI fallback or hardcoding a
        # blame that might be wrong (e.g. a stale lock left by THIS SAME
        # button's own earlier killed run, not the other box at all).
        print(f"[MCX_DEPOSIT_BUSY:{busy.owner}] {busy}")
        sys.exit(2)
    if result:
        print(f"\n✅ Saved: {result}")
        sys.exit(0)
    else:
        print("\n❌ MCX Deposit download failed")
        sys.exit(1)
