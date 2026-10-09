# ── Restart supervisor ────────────────────────────────────────────────────────
# If the connection breaks ("An existing connection was forcibly closed by the remote host", reset, aborted,
# timeout) this whole script — login included — is started again from the very beginning after 5 s, up to 5 times.
# A refused / wrong password is NOT retried. Everything the script prints is passed straight through.
import os as _sv_os
import subprocess as _sv_sp
import sys as _sv_sys
import time as _sv_time

if _sv_os.getenv("MCX90_CHILD") != "1":
    _SV_NET = ("forcibly closed", "connection aborted", "connection reset", "remotedisconnected", "remote end closed",
               "10054", "10053", "connectionerror", "readtimeout", "connecttimeout", "chunkedencodingerror")
    _SV_NO = ("wrong password", "auth_fail", "_locked]", "invalid_login", "user locked")
    _env = dict(_sv_os.environ, MCX90_CHILD="1")
    _rc = 1
    for _try in range(1, 6):
        _tail = []
        _p = _sv_sp.Popen([_sv_sys.executable, "-u", _sv_os.path.abspath(__file__)], env=_env,
                          stdout=_sv_sp.PIPE, stderr=_sv_sp.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
        for _line in _p.stdout:
            print(_line.rstrip("\n"), flush=True)
            _tail.append(_line.lower())
            del _tail[:-40]
        _rc = _p.wait()
        if _rc == 0:
            break
        _blob = " ".join(_tail)
        if _try >= 5 or not any(x in _blob for x in _SV_NET) or any(x in _blob for x in _SV_NO):
            break
        print(f"⚠️ Connection problem — starting the whole MCX login again from the beginning in 5s "
              f"(restart {_try}/4)...", flush=True)
        _sv_time.sleep(5)
    _sv_sys.exit(_rc)

import requests
import base64
import re
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad
import xml.etree.ElementTree as ET
from datetime import datetime
import time
from bs4 import BeautifulSoup

# =========================
# 📁 SAVE FOLDER CONFIG
# =========================
import os

try:
    from dotenv import load_dotenv as _ld
    _ld(os.getenv("CTCL_ENV_FILE", r"D:\CLAUDE\rms-copilot\.env"))
except Exception:
    pass

INPUT_FOLDER = os.getenv("MCX90_SAVE_DIR", r"D:\CTCL VS NMASS")
os.makedirs(INPUT_FOLDER, exist_ok=True)


def update_password_in_file(new_password):
    """Write the new MCX 90% password to .env (CTCL_MCX90_PASSWORD) so the
    dashboard Credentials button and this script always stay in sync.
    (Previously this rewrote the PASSWORD literal inside this .py file —
    changed to use .env, the single source of truth for all credentials.)"""
    import re as _re
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(_env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        out, found = [], False
        for ln in lines:
            if _re.match(r"\s*CTCL_MCX90_PASSWORD\s*=", ln):
                out.append(f"CTCL_MCX90_PASSWORD={new_password}\n"); found = True
            else:
                out.append(ln)
        if not found:
            out.append(f"CTCL_MCX90_PASSWORD={new_password}\n")
        with open(_env_path, "w", encoding="utf-8") as f:
            f.writelines(out)
        global PASSWORD
        PASSWORD = new_password
        print("💾 Password updated in .env")
    except Exception as e:
        print(f"⚠️ Could not update .env: {e}")

def get_password_popup(attempt, max_attempts, error_msg=None):
    """Password entry — routed through dashboard UI (web state file).
    Reuses the same token state file/endpoint as get_token_popup."""
    return get_token_popup(attempt, max_attempts, error_msg, label="MCX 90% — Password")


# =========================
# 🎨 TOKEN POPUP  (web-based — no tkinter)
# =========================
def get_token_popup(attempt, max_attempts, error_msg=None, label="MCX 90%"):
    """Write a .token_state file in CTCL_NMASS_FOLDER, then poll until dashboard answers.
    Dashboard polls GET /ctcl/token/pending and POSTs to /ctcl/token/answer."""
    import json as _tj, os as _os
    _dir = _os.getenv("MCX90_SAVE_DIR", _os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS"))
    _os.makedirs(_dir, exist_ok=True)
    _sf  = _os.path.join(_dir, ".token_state")
    _af  = _os.path.join(_dir, ".token_answer")
    for _f in [_sf, _af]:
        try: _os.remove(_f)
        except OSError: pass
    _tj.dump({"state": "PENDING", "attempt": attempt, "max_attempts": max_attempts,
              "error_msg": error_msg, "label": label}, open(_sf, "w", encoding="utf-8"))
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
                    try: _os.remove(_f)
                    except OSError: pass
                print(f"✅ Token received via dashboard.")
                return answer
        except Exception:
            pass
    # Timeout — clean up and raise
    for _f in [_sf, _af]:
        try: _os.remove(_f)
        except OSError: pass
    raise RuntimeError("❌ Token entry timed out (180 s) — no answer from dashboard.")


# =========================
# 🚨 SESSION POPUP  (console-only — no tkinter)
# =========================
def session_expired_popup():
    """Just print a console notice — dashboard shows this in the live log."""
    print("⚠️ MCX session expired — re-authenticating …")

# =========================
# 🔐 ENCRYPT (ONLY FOR STEP 3 & 4)
# =========================
def encrypt_value(value: str):
    key = get_random_bytes(16)
    iv = get_random_bytes(16)

    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(value.encode(), AES.block_size))

    encrypted_b64 = base64.b64encode(encrypted).decode()
    ignore_flag = base64.b64encode(key + iv).decode()

    return encrypted_b64, ignore_flag


# =========================
# 🔍 ENTITY EXTRACTION
# =========================
def extract_entity(text):
    match = re.search(r"<EntityList>.*</EntityList>", text, re.DOTALL)

    if not match:
        print("❌ Entity XML not found")
        raise Exception("Entity missing")

    root = ET.fromstring(match.group(0))

    for entity in root.findall(".//EntityDetail"):
        code = entity.find("entityCode").text
        desc = entity.find("shortDescription").text

        if code != "DefaultEntity":
            return code, f"{code}:{desc},"

    raise Exception("No valid entity")


# =========================
# 🌐 SESSION
# =========================
session = requests.Session()

BASE_URL = "https://eclear.mcxccl.com"

LOGIN_URL = BASE_URL + "/Bancs/login.do?method=authenticatePassward"
TOKEN_URL = BASE_URL + "/Bancs/login.do?method=authenticateToken"
FINAL_LOGIN_URL = BASE_URL + "/Bancs/login.do?method=Login"
EXPORT_SEARCH_URL = BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do"
DOWNLOAD_URL = BASE_URL + "/Bancs/DownloadFileAction1.do"

USER_ID = os.getenv("CTCL_MCX90_USER", "56720CMMKR014139")
PASSWORD = os.getenv("CTCL_MCX90_PASSWORD", "Mcx@2907")


# =========================
# HEADERS
# =========================
AJAX_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/Bancs/common/dologin.jsp?method=authMode&JSLoadingMode=null"
}

LOGIN_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/Bancs/common/dologin.jsp?method=authMode&JSLoadingMode=null",
    "Upgrade-Insecure-Requests": "1"
}


# =========================
# STEP 1: SESSION INIT
# =========================
print("\n🌐 Opening session...")
session.get(BASE_URL)


# =========================
# STEP 2: AUTH MODE
# =========================
session.post(BASE_URL + "/Bancs/login.do?method=authMode", headers=AJAX_HEADERS)


# =========================
# STEP 3: PASSWORD (SMART LOGIC)
# =========================
print("\n🔐 PASSWORD STEP")

MAX_PASS_RETRY = 2
pass_attempt = 0
error_msg = None

# 🔥 TRY SAVED PASSWORD FIRST
print("⚡ Trying saved password...")

password_enc, ignore_flag = encrypt_value(PASSWORD)

payload = {
    "userId": USER_ID + "\t",
    "password": password_enc,
    "lng": "1",
    "ignoreFlag": ignore_flag
}

resp = session.post(LOGIN_URL, headers=AJAX_HEADERS, data=payload)

# ✅ IF WORKS
if (
    "INVALID_LOGIN_OR_PASSWORD" not in resp.text
    and "error_div1" not in resp.text
    and "EntityList" in resp.text
):
    print("✅ Saved password worked — no popup")

else:
    print("❌ Saved password failed — asking user...")

    while pass_attempt < MAX_PASS_RETRY:
        pass_attempt += 1

        entered_password = get_password_popup(
            pass_attempt,
            MAX_PASS_RETRY,
            error_msg
        )

        password_enc, ignore_flag = encrypt_value(entered_password)

        payload = {
            "userId": USER_ID + "\t",
            "password": password_enc,
            "lng": "1",
            "ignoreFlag": ignore_flag
        }

        resp = session.post(LOGIN_URL, headers=AJAX_HEADERS, data=payload)

        # ❌ WRONG PASSWORD
        if (
            "INVALID_LOGIN_OR_PASSWORD" in resp.text
            or "error_div1" in resp.text
        ):
            error_msg = "❌ PASSWORD INCORRECT!\nEnter correct password"

            if pass_attempt == MAX_PASS_RETRY:
                print("[MCX90_AUTH_FAIL] wrong password — update CTCL_MCX90_PASSWORD in Credentials")
                raise Exception("🚫 Max password attempts reached")

            continue

        # ❌ Unexpected
        if "EntityList" not in resp.text:
            print(resp.text[:300])
            raise Exception("❌ Unexpected response")

        # ✅ SUCCESS
        PASSWORD = entered_password

        # 🔥 SAVE PERMANENTLY
        update_password_in_file(entered_password)

        print("✅ Password updated & saved")
        break

# =========================
# STEP 4 TOKEN STEP (WITH RETRY)
# =========================
print("\n🔴 TOKEN STEP")

MAX_TOKEN_RETRY = 3
token_attempt = 0
error_msg = None

while token_attempt < MAX_TOKEN_RETRY:
    token_attempt += 1

    token = get_token_popup(token_attempt, MAX_TOKEN_RETRY, error_msg)

    token_enc, ignore_flag_token = encrypt_value(token)

    payload_token = {
        "userId": USER_ID + "\t",
        "password": token_enc,
        "lng": "1",
        "ignoreFlag": ignore_flag_token
    }

    resp2 = session.post(TOKEN_URL, headers=AJAX_HEADERS, data=payload_token)

    print("📥 Token Status:", resp2.status_code)

    try:
        ENTITY, USER_ENTITY_CODES = extract_entity(resp2.text)
        print(f"✅ ENTITY: {ENTITY}")
        break

    except Exception:
        error_msg = "❌ Invalid token. Please try again."
        print(error_msg)

        # 🔍 DEBUG (optional but recommended)
        print("📄 Response Preview:", resp2.text[:200])

        if token_attempt == MAX_TOKEN_RETRY:
            raise Exception("🚫 Max token attempts reached. Exiting.")

        print("🔁 Please enter token again...\n")

# =========================
# STEP 5: ENTITY
# =========================
ENTITY, USER_ENTITY_CODES = extract_entity(resp2.text)
print("✅ ENTITY:", ENTITY)


# =========================
# STEP 6: FINAL LOGIN (REAL FIX HERE)
# =========================
print("\n🚀 FINAL LOGIN")
final_password_enc, final_ignore_flag = encrypt_value(PASSWORD)

final_payload = {
    "method": "Login",
    "availHeight": "744",
    "availWidth": "1522",
    "isArchival": "false",
    "userEntityCodes": USER_ENTITY_CODES,
    "lng": "1",
    "userId": USER_ID,  # ❗ NO TAB HERE
    "ignoreFlag": final_ignore_flag,
    "ArchivalignoreFlag": "",
    "password": final_password_enc,
    "dummy": "FltM/sA2fPfqsjtM55oLOQ==",
    "entity": ENTITY
}

resp3 = session.post(
    FINAL_LOGIN_URL,
    headers=LOGIN_HEADERS,
    data=final_payload
)

print("📥 FINAL LOGIN STATUS:", resp3.status_code)


# =========================
# 🔍 EXTRACT RNDAAK
# =========================
print("\n🔍 Extracting rndaak...")

rndaak = None

match = re.search(r"name=['\"]rndaak['\"][^>]*value=['\"]([^'\"]+)", resp3.text)
if match:
    rndaak = match.group(1)

if not rndaak:
    match = re.search(r"rndaak['\"]?\s*:\s*['\"]([^'\"]+)", resp3.text)
    if match:
        rndaak = match.group(1)

if not rndaak:
    print("❌ rndaak NOT FOUND")
    print(resp3.text[:2000])
    raise Exception("STOP")

print("✅ rndaak:", rndaak)


# =========================
# 🚀 POST LOGIN FLOW (FIXED EXACTLY LIKE BROWSER)
# =========================

COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/Bancs/login.do?method=Login&availHeight=744&availWidth=1522",
    "Sec-Fetch-Site": "same-origin",
}

NAV_HEADERS = {
    **COMMON_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "iframe",
    "Upgrade-Insecure-Requests": "1"
}

AJAX_HEADERS_2 = {
    **COMMON_HEADERS,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "*/*",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Referer": BASE_URL + "/Bancs/common/welcome.jsp"
}

# -------------------------
# STEP 1: CommonImpl.jsp (FIRST)
# -------------------------
print("\n🌐 STEP 1: CommonImpl.jsp")

resp_common = session.post(
    BASE_URL + "/Bancs/common/CommonImpl.jsp",
    headers=NAV_HEADERS,
    data={"rndaak": rndaak}
)

print("📥 Status:", resp_common.status_code)

# -------------------------
# STEP 2: welcome.jsp (POST NOT GET)
# -------------------------
print("\n🌐 STEP 2: welcome.jsp")

resp_welcome = session.post(
    BASE_URL + "/Bancs/common/welcome.jsp",
    headers=NAV_HEADERS,
    data={"rndaak": rndaak}
)

print("📥 Status:", resp_welcome.status_code)

# -------------------------
# STEP 3: layout.save (AJAX)
# -------------------------
print("\n🌐 STEP 3: layout.save")

resp_layout = session.post(
    BASE_URL + "/Bancs/common/layout.save",
    headers=AJAX_HEADERS_2,
    data={
        "events": "",
        "rndaak": rndaak
    }
)

print("📥 Status:", resp_layout.status_code)

# -------------------------
# FINAL CHECK
# -------------------------
resp_final = session.post(
    BASE_URL + "/Bancs/common/welcome.jsp",
    headers=NAV_HEADERS,
    data={"rndaak": rndaak}
)

print("\n📥 FINAL STATUS:", resp_final.status_code)

if resp_final.status_code == 200:
    print("🎉 SUCCESS: SESSION FULLY ACTIVE")
else:
    print("❌ FAILED")




# =========================
# 📡 HEADERS (REQUIRED FOR RSK335)
# =========================
INIT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/Bancs/common/PageLoader.html",
}

FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": BASE_URL  # will be updated dynamically
}




# =========================
# 🚀 OPEN RSK335 PAGE (CORRECT FLOW)
# =========================
print("\n🌐 Opening RSK335 page...")

RSK335_URL = BASE_URL + "/Bancs/RSK/RSK335.do"

FETCH_HEADERS["Referer"] = RSK335_URL

init_payload_335 = {
    "app": "bancsapp?prefix=/RSK",
    "page": "/RSK335.do",
    "confViewSize": "50",
    "reqType": "0",
    "mode": "DEF_MODE",
    "parentName": "blank",
    "browserType": "IE55",
    "GuiWinName": "RSK335",
    "compname": "RSK",
    "over_ride_button": "Cancel",
    "AvlHt": "744",
    "AvlWd": "1481",
    "tblWd": "1465",
    "GuiBrowserInst": "0",
    "theme": "black",
    "pageId": "RSK335",
    "wintitle": "Margin Utilization View",
    "availHeight": "816",
    "availWidth": "1536",
    "tabId": "panel3",   # 🔥 FIXED
    "rndaak": rndaak
}

session.post(RSK335_URL, headers=INIT_HEADERS, data=init_payload_335)


# =========================
# 🔥 PRE-CALL (MANDATORY)
# =========================
print("⚙️ Running RSK335 pre-call...")

pre_payload_335 = {
    "xmlReq": "YES",
    "reqType": "19",
    "GuiWinName": "RSK335",
    "GuiBrowserInst": "0",
    "inParam1": "56720",
    "inParam2": "DFLT",
    "tabId": "panel3",
    "functionName": "funcname",
    "ifCallPaint": "false",
    "ctrlId": "MCB_SearchWC_wca_associatedcm",
    "filterCombo": "NO",
    "filterS": "sample",
    "rndaak": rndaak
}

session.post(RSK335_URL, headers=FETCH_HEADERS, data=pre_payload_335)


# =========================
# 🔍 SEARCH (FULL CORRECT)
# =========================
print("🔍 Running RSK335 search...")

search_payload_335 = {
    "propertyMap(MCB_SearchWC_wca_bpid_Cmb)": "DFLT",
    "MCB_SearchWC_wca_bpid": "",
    "propertyMap(MCB_SearchWC_wca_bpid_op)": "=",

    "MCB_SearchWC_wca_actype": "",
    "propertyMap(MCB_SearchWC_wca_actype_op)": "=",

    "propertyMap(MCB_SearchWC_wca_associatedtm_Cmb)": "DFLT",
    "MCB_SearchWC_wca_associatedtm": "",
    "propertyMap(MCB_SearchWC_wca_associatedtm_op)": "=",

    "propertyMap(MCB_SearchWC_wca_associatedcm_Cmb)": "DFLT",
    "MCB_SearchWC_wca_associatedcm": "56720",
    "propertyMap(MCB_SearchWC_wca_associatedcm_op)": "=",

    "MCB_SearchWC_wca_valuedate": "",
    "propertyMap(MCB_SearchWC_wca_valuedate_op)": "=",

    "MCB_SearchWC_wca_category": "",
    "propertyMap(MCB_SearchWC_wca_category_op)": "=",

    "MCB_SearchWC_wca_CMName": "DFLT",
    "propertyMap(MCB_SearchWC_wca_CMName_op)": "=",

    "MCB_SearchWC_wca_TMName": "DFLT",
    "propertyMap(MCB_SearchWC_wca_TMName_op)": "=",

    "MCB_SearchWC_wca_datedummy1": "",
    "propertyMap(MCB_SearchWC_wca_datedummy1_op)": "=",

    "MCB_SearchWC_wca_datedummy2": "",
    "propertyMap(MCB_SearchWC_wca_datedummy2_op)": "=",

    "GRID_RESPONSE": "RSK335_Table",
    "operationType": "ET",
    "searchClicked": "true",
    "PageNum": "0",

    "service": "qryMarginUtilView",
    "windowName": "RSK335",
    "MODE": "DEF_MODE",
    "compName": "RSK",

    "lgbinst": "0",
    "WRITE_APP": "true",

    "REQ_TYPE": "0",
    "reqType": "1",

    "sQuery": "Client Code  Equals  DFLT AND TM / CP  Equals  DFLT AND CM  Equals  DFLT AND CM  Equals  56720",

    "AvlWd": "1048",
    "tblWd": "1026",

    "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",

    "IXHRts": str(int(time.time() * 1000)),
    "XHR": "true",

    "rndaak": rndaak,

    "theme": "black",
    "tabId": "panel3",

    "mode": "DEF_MODE",
    "GuiBrowserInst": "0",
    "parentName": "blank",

    "browserType": "IE55",
    "GuiWinName": "RSK335",
    "compname": "RSK",

    "confViewSize": "50"
}

res_335 = session.post(RSK335_URL, headers=FETCH_HEADERS, data=search_payload_335)

print("📥 RSK335 Status:", res_335.status_code)

if "RSK335_Table" not in res_335.text:
    print(res_335.text[:300])
    raise Exception("❌ RSK335 search failed")

print("✅ RSK335 search successful")


# =========================
# 🚀 EXPORT
# =========================
export_payload_335 = search_payload_335.copy()

export_payload_335.update({
    "Export": "Excel",
    "enableBatchForExport": "yes",
    "noPgsToExport": "100",
    "PageNum": "1",
    "undefined": "",
    "MCBrowserEventInformation": "sourceElementName~|~undefined#*#eventType~|~callListExport#*#butId~|~null",
    "IXHRts": str(int(time.time() * 1000)),
})

res_export_335 = session.post(RSK335_URL, headers=FETCH_HEADERS, data=export_payload_335)

if "ExportBatchSuccess" not in res_export_335.text:
    raise Exception("❌ RSK335 export failed")

print("✅ RSK335 export triggered")

export_trigger_time_335 = datetime.now()
print(f"⏱ RSK335 Export Time: {export_trigger_time_335.strftime('%Y%m%d%H%M%S')}")


max_wait = 720      # total wait time in seconds
interval = 15         # check every 5 sec

EXPORT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do"
}





def fetch_export_page(page_num, base_payload, headers):
    payload_page = base_payload.copy()
    payload_page["PageNum"] = str(page_num)
    payload_page["startIndex"] = str(page_num * 50) 

    res_page = session.post(
        EXPORT_SEARCH_URL,
        headers=headers,
        data=payload_page
    )

    soup_page = BeautifulSoup(res_page.text, "html.parser")
    return soup_page.select("table tbody tr")




##RELOGINNN FUNCTIONSSSSS

# =========================
# 🔁 SESSION HANDLING (AUTO RELOGIN)
# =========================

# =========================
# 🔁 SESSION HANDLING (AUTO RELOGIN) — FIXED
# =========================

def is_session_expired(text):
    return (
        "YOUR SESSION HAS EXPIRED" in text
        or "sessionExpired" in text
        or 'checkType" value="sessionExpired"' in text
    )


def reinitialize_arch():
    global tab_id

    print("🔄 Reinitializing ARCH (FULL FLOW)...")

    # STEP 1: PageLoader (MANDATORY)
    session.get(
        BASE_URL + "/Bancs/common/PageLoader.html",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html",
            "Referer": BASE_URL + "/Bancs/login.do?method=Login"
        }
    )

    # STEP 2: ARCH INIT (FULL PAYLOAD)
    resp_arch = session.post(
        BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": BASE_URL,
            "Referer": BASE_URL + "/Bancs/common/PageLoader.html"
        },
        data={
            "app": "bancsapp?prefix=/ARCHGUI",
            "page": "/ar_w_DataExport.do",
            "confViewSize": "50",
            "reqType": "0",
            "mode": "DEF_MODE",
            "parentName": "blank",
            "browserType": "IE55",
            "GuiWinName": "ar_w_DataExport",
            "compname": "ARCHGUI",
            "over_ride_button": "Cancel",
            "AvlHt": "678",
            "AvlWd": "914",
            "tblWd": "898",
            "GuiBrowserInst": "0",
            "theme": "black",
            "pageId": "ar_w_DataExport",
            "wintitle": "Download Exported Files",
            "availHeight": "816",
            "availWidth": "1536",
            "tabId": "panel4",

            # 🔥 MUST
            "rndaak": rndaak,
            "reqtoken": "",
            "parentToChildData": ""
        }
    )

    print("📥 ARCH INIT STATUS:", resp_arch.status_code)

    # 🔍 DEBUG (VERY IMPORTANT)
    if "sessionExpired" in resp_arch.text:
        raise Exception("❌ ARCH INIT FAILED - session still not valid")

    tab_match = re.search(r'id="tabId"[^>]*value="([^"]+)"', resp_arch.text)

    if not tab_match:
        print(resp_arch.text[:500])  # 🔥 DEBUG OUTPUT
        raise Exception("❌ tabId not found after relogin")

    tab_id = tab_match.group(1)

    print("✅ ARCH READY AGAIN | tab_id:", tab_id)


def do_relogin():
    global session, rndaak

    print("\n🚨 SESSION EXPIRED — RE-LOGIN STARTED")

    session = requests.Session()

    # =========================
    # STEP 1: INIT SESSION
    # =========================
    session.get(BASE_URL)

    session.post(
        BASE_URL + "/Bancs/login.do?method=authMode",
        headers=AJAX_HEADERS
    )

    # =========================
    # STEP 2: PASSWORD
    # =========================
    print("🔐 PASSWORD STEP")

    password_enc, ignore_flag = encrypt_value(PASSWORD)

    session.post(
        LOGIN_URL,
        headers=AJAX_HEADERS,
        data={
            "userId": USER_ID + "\t",
            "password": password_enc,
            "lng": "1",
            "ignoreFlag": ignore_flag
        }
    )

    # =========================
    # STEP 3: TOKEN (WITH RETRY)
    # =========================
    print("🔴 TOKEN STEP")

    MAX_TOKEN_RETRY = 3
    token_attempt = 0

    session_expired_popup()
    error_msg = None

    while token_attempt < MAX_TOKEN_RETRY:
        token_attempt += 1

        token = get_token_popup(token_attempt, MAX_TOKEN_RETRY, error_msg)

        token_enc, ignore_flag_token = encrypt_value(token)

        resp2 = session.post(
            TOKEN_URL,
            headers=AJAX_HEADERS,
            data={
                "userId": USER_ID + "\t",
                "password": token_enc,
                "lng": "1",
                "ignoreFlag": ignore_flag_token
            }
        )

        print("📥 Token Status:", resp2.status_code)

        try:
            ENTITY, USER_ENTITY_CODES = extract_entity(resp2.text)
            print(f"✅ ENTITY: {ENTITY}")
            break

        except Exception:
            error_msg = "❌ Invalid token. Please try again."
            print("📄 Response Preview:", resp2.text[:200])

            if token_attempt == MAX_TOKEN_RETRY:
                raise Exception("🚫 Max token attempts reached during relogin")

            print("🔁 Try again...\n")

    # =========================
    # STEP 4: FINAL LOGIN
    # =========================
    print("🚀 FINAL LOGIN")
    final_password_enc, final_ignore_flag = encrypt_value(PASSWORD)
    resp3 = session.post(
        FINAL_LOGIN_URL,
        headers=LOGIN_HEADERS,
        data={
            "method": "Login",
            "availHeight": "744",
            "availWidth": "1522",
            "isArchival": "false",
            "userEntityCodes": USER_ENTITY_CODES,
            "lng": "1",
            "userId": USER_ID,
            "ignoreFlag": final_ignore_flag,
            "password": final_password_enc,
            "dummy": "FltM/sA2fPfqsjtM55oLOQ==",
            "entity": ENTITY
        }
    )

    print("📥 Relogin status:", resp3.status_code)

    # =========================
    # 🔍 EXTRACT RNDAAK
    # =========================
    rndaak_new = None

    match = re.search(r"name=['\"]rndaak['\"][^>]*value=['\"]([^'\"]+)", resp3.text)
    if match:
        rndaak_new = match.group(1)

    if not rndaak_new:
        match = re.search(r"rndaak['\"]?\s*:\s*['\"]([^'\"]+)", resp3.text)
        if match:
            rndaak_new = match.group(1)

    if not rndaak_new:
        print(resp3.text[:1000])
        raise Exception("❌ rndaak not found after relogin")

    rndaak = rndaak_new
    print("✅ NEW RNDAAK:", rndaak)

    # =========================
    # ⏳ STABILITY DELAY (IMPORTANT)
    # =========================
    import time
    time.sleep(2)

    # =========================
    # 🔄 FULL POST-LOGIN FLOW (CRITICAL FIX)
    # =========================
    print("🔄 Running FULL post-login flow...")

    # STEP 1
    session.post(
        BASE_URL + "/Bancs/common/CommonImpl.jsp",
        headers=NAV_HEADERS,
        data={"rndaak": rndaak}
    )

    # STEP 2
    session.post(
        BASE_URL + "/Bancs/common/welcome.jsp",
        headers=NAV_HEADERS,
        data={"rndaak": rndaak}
    )

    # STEP 3
    session.post(
        BASE_URL + "/Bancs/common/layout.save",
        headers=AJAX_HEADERS_2,
        data={
            "events": "",
            "rndaak": rndaak
        }
    )

    # STEP 4
    session.post(
        BASE_URL + "/Bancs/common/welcome.jsp",
        headers=NAV_HEADERS,
        data={"rndaak": rndaak}
    )

    print("✅ SESSION RESTORED (FULL FLOW)")

    # =========================
    # 🔥 RE-INIT ARCH (FIXED)
    # =========================
    reinitialize_arch()

    print("🎯 RELOGIN COMPLETE — RESUMING PROCESS")



# =========================
# 🌐 OPEN EXPORT DOWNLOAD PAGE (MANDATORY)
# =========================
print("\n🌐 Opening Export Download Page...")

# STEP 1: PageLoader
session.get(
    BASE_URL + "/Bancs/common/PageLoader.html",
    headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html",
        "Referer": BASE_URL + "/Bancs/login.do?method=Login"
    }
)

# STEP 2: ARCH INIT
ARCH_INIT_URL = BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do"

arch_payload = {
    "app": "bancsapp?prefix=/ARCHGUI",
    "page": "/ar_w_DataExport.do",
    "confViewSize": "50",
    "reqType": "0",
    "mode": "DEF_MODE",
    "parentName": "blank",
    "browserType": "IE55",
    "GuiWinName": "ar_w_DataExport",
    "compname": "ARCHGUI",
    "over_ride_button": "Cancel",
    "AvlHt": "678",
    "AvlWd": "914",
    "tblWd": "898",
    "GuiBrowserInst": "0",
    "theme": "black",
    "pageId": "ar_w_DataExport",
    "wintitle": "Download Exported Files",
    "availHeight": "816",
    "availWidth": "1536",
    "tabId": "panel4",
    "rndaak": rndaak,
    "reqtoken": "",
    "parentToChildData": ""
}

resp_arch_init = session.post(
    ARCH_INIT_URL,
    headers={
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": BASE_URL,
        "Referer": BASE_URL + "/Bancs/common/PageLoader.html"
    },
    data=arch_payload
)

print("📥 ARCH INIT Status:", resp_arch_init.status_code)



# =========================
# 🔄 STEP 2: POLLING (RSK335 ONLY)
# =========================

print("\n⏳ WAITING FOR RSK335 FILE ONLY...")

import re

# 🔥 Extract TAB ID (MANDATORY)
tab_match = re.search(r'id="tabId"[^>]*value="([^"]+)"', resp_arch_init.text)
if not tab_match:
    raise Exception("❌ tabId not found")

tab_id = tab_match.group(1)
print("✅ TAB ID:", tab_id)

max_wait = 900
interval = 15
elapsed = 0

target_file_335 = None
ready_335 = False

last_time_335 = None
file_locked_335 = False

while elapsed < max_wait:

    print(f"\n🔄 Checking export files... (elapsed: {elapsed}s)")

    payload = {
        "MCB_SearchWC_wca_EXPORT_WINDOW_NAME": "",
        "propertyMap(MCB_SearchWC_wca_EXPORT_WINDOW_NAME_op)": "=",

        "MCB_SearchWC_wca_EXPORT_TABLE_NAME": "",
        "propertyMap(MCB_SearchWC_wca_EXPORT_TABLE_NAME_op)": "=",

        "MCB_SearchWC_wca_EXPORT_DATE_FROM": "",
        "propertyMap(MCB_SearchWC_wca_EXPORT_DATE_FROM_From)": export_trigger_time_335.strftime("%d/%m/%Y"),
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

        "GRID_RESPONSE": "ar_w_DataExport_Table",
        "operationType": "ET",
        "searchClicked": "true",
        "PageNum": "0",

        "service": "ARET8001GetExportData",
        "windowName": "ar_w_DataExport",
        "MODE": "DEF_MODE",
        "compName": "ARCHGUI",

        "lgbinst": "0",
        "WRITE_APP": "true",

        "REQ_TYPE": "0",
        "reqType": "1",

        "sQuery": "  Equals  " + export_trigger_time_335.strftime("%Y%m%d"),

        "AvlWd": "1280",
        "tblWd": "1280",

        "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",

        "IXHRts": str(int(time.time() * 1000)),
        "XHR": "true",

        "rndaak": rndaak,
        "theme": "black",
        "tabId": tab_id,

        "mode": "DEF_MODE",
        "GuiBrowserInst": "0",
        "parentName": "blank",

        "confViewSize": "50",
        "browserType": "IE55",
        "GuiWinName": "ar_w_DataExport",
        "compname": "ARCHGUI",

        "over_ride_button": "Cancel"
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE_URL,
        "Referer": BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do?app=bancsapp?prefix=/ARCHGUI"
    }

    all_rows = []

    # =========================
    # 📄 PAGE 1
    # =========================
    try:
        res_first = session.post(EXPORT_SEARCH_URL, headers=headers, data=payload)

        if (
            "YOUR SESSION HAS EXPIRED" in res_first.text
            or "sessionExpired" in res_first.text
        ):
            raise Exception("SESSION_EXPIRED")

    except Exception as e:
        if "SESSION_EXPIRED" in str(e):
            print("🚨 Session expired → Re-login")
            do_relogin()
            continue
        else:
            raise

    soup = BeautifulSoup(res_first.text, "html.parser")
    rows = soup.select("table tbody tr")

    print(f"📄 Page 1 → {len(rows)} rows")
    all_rows.extend(rows)

    # =========================
    # 📄 PAGE 2
    # =========================
    payload_next = {
        "tabId": tab_id,
        "GuiBrowserInst": "0",
        "GuiWinName": "ar_w_DataExport",
        "compname": "ARCHGUI",

        "xmlReq": "YES",
        "reqType": "1",
        "confViewSize": "50",

        "PageNum": "0",

        "butvalue": "8",
        "selectedRow_ar_w_DataExport": "49",
        "tblurl": "ar_w_DataExport_Table",

        "MCBrowserEventInformation": "sourceElementName~|~next#*#eventType~|~click#*#butId~|~8",

        "rndaak": rndaak,
        "theme": "black",

        "sQuery": "  Equals  " + export_trigger_time_335.strftime("%Y%m%d"),

        "AvlWd": "1208",
        "tblWd": "1186",

        "RowNumKeyForPrevNxt": "1",
        "PrevOrNext": "N"
    }

    try:
        res_next = session.post(EXPORT_SEARCH_URL, headers=headers, data=payload_next)

        if (
            "YOUR SESSION HAS EXPIRED" in res_next.text
            or "sessionExpired" in res_next.text
        ):
            raise Exception("SESSION_EXPIRED")

    except Exception as e:
        if "SESSION_EXPIRED" in str(e):
            print("🚨 Session expired → Re-login")
            do_relogin()
            continue
        else:
            raise

    soup_next = BeautifulSoup(res_next.text, "html.parser")
    rows_next = soup_next.select("table tbody tr")

    print(f"📄 Page 2 → {len(rows_next)} rows")
    if rows_next:
        all_rows.extend(rows_next)

    # =========================
    # 🔍 PROCESS ROWS (ONLY 335)
    # =========================
    for row in all_rows:
        cols = row.find_all("td")
        if len(cols) < 7:
            continue

        status = cols[4].text.strip()
        timestamp_str = cols[5].text.strip()
        file_name = cols[6].text.strip()

        print(f"🧾 {timestamp_str} | {status} | {file_name}")

        try:
            file_time = datetime.strptime(timestamp_str, "%d/%m/%Y %H:%M:%S")
            query = cols[2].text.strip().replace("%2F", "/")

            if (
                not file_locked_335
                and abs((file_time - export_trigger_time_335).total_seconds()) <= 15
                and "Client Code" in query
            ):
                target_file_335 = file_name
                file_locked_335 = True
                print(f"🔒 LOCKED RSK335 FILE: {target_file_335}")

            if file_locked_335 and file_name == target_file_335:
                if "Generated" in status:
                    ready_335 = True

        except:
            continue

    if ready_335:
        print("\n🎉 RSK335 FILE READY")
        break

    time.sleep(interval)
    elapsed += interval


# =========================
# VALIDATION
# =========================
if not target_file_335:
    raise Exception("❌ RSK335 not found")

if not ready_335:
    raise Exception("❌ RSK335 not generated in time")




# =========================
# ⬇️ STEP 3: DOWNLOAD FILE (RSK335 ONLY)
# =========================

print("\n⬇️ STARTING DOWNLOAD PROCESS FOR RSK335...")

label = "RSK335"
file_name = target_file_335

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do?app=bancsapp?prefix=/ARCHGUI"
}

print(f"\n🔽 Processing {label} → {file_name}")

# =========================
# 🔍 CHECK FILE EXISTS (RETRY LOGIC)
# =========================
check_url = f"{BASE_URL}/Bancs/DownloadFileAction1.do?fileName={file_name}&action=checkForFile"

file_ready = False
retry = 0
max_retry = 10

while retry < max_retry:
    retry += 1

    print(f"🔍 Checking file ({retry}/{max_retry})...")

    check_res = session.post(
        check_url,
        headers=DOWNLOAD_HEADERS,
        data={"rndaak": rndaak}
    )

    if "FileExists" in check_res.text:
        file_ready = True
        print("✅ File is ready")
        break

    print("⏳ Not ready yet, waiting 5 sec...")
    time.sleep(5)

if not file_ready:
    raise Exception(f"❌ {label} file not ready for download")

# =========================
# ⬇️ DOWNLOAD FILE
# =========================
download_url = f"{BASE_URL}/Bancs/DownloadFileAction1.do?fileName={file_name}&action=downloadFile"

print("⬇️ Downloading...")

download_res = session.post(
    download_url,
    headers=DOWNLOAD_HEADERS,
    data={"rndaak": rndaak},
    stream=True
)

print("📥 Status:", download_res.status_code)

if download_res.status_code != 200:
    print(download_res.text[:200])
    raise Exception(f"❌ Download failed for {label}")

# =========================
# 💾 SAVE FILE
# =========================
save_path = os.path.join(INPUT_FOLDER, file_name)

with open(save_path, "wb") as f:
    for chunk in download_res.iter_content(chunk_size=8192):
        if chunk:
            f.write(chunk)

print(f"✅ {label} DOWNLOADED: {save_path}")

print("\n🎉 RSK335 FILE DOWNLOADED SUCCESSFULLY")
print("\n🛑 Script finished — closing now")