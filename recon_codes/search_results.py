import requests
import base64
import re
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad
import xml.etree.ElementTree as ET
import tkinter as tk
from datetime import datetime, timedelta
import time
from bs4 import BeautifulSoup
import shutil
import os

# =========================
# 📁 SAVE FOLDER CONFIG
# =========================

SEARCH_FOLDER = r"C:\Users\Nitin_RMS\Desktop\RECONCILIATION\T1 HOLDING"

os.makedirs(SEARCH_FOLDER, exist_ok=True)




def update_password_in_file(new_password):
    import os
    import re
    import json

    file_path = os.path.abspath(__file__)

    # keep the file's own line endings / encoding
    with open(file_path, "r", encoding="utf-8", newline="") as f:
        content = f.read()

    # replace ONLY the top-level  PASSWORD = "..."  line with the NEW password
    new_line = "PASSWORD = " + json.dumps(new_password)
    updated_content, n = re.subn(
        r'^PASSWORD\s*=\s*(["\']).*?\1[ \t]*(?=\r?$)',
        lambda m: new_line,
        content,
        count=1,
        flags=re.MULTILINE
    )

    if n != 1:
        print("⚠ Could not find the PASSWORD line - password NOT saved in the script")
        return

    # write safely (temp file, then replace) so the script can never be left half-written
    tmp_path = file_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        f.write(updated_content)
    os.replace(tmp_path, file_path)

    print("💾 New password saved in script file")

def _password_window(attempt, max_attempts, error_msg=None):
    import tkinter as tk

    root = tk.Tk()
    root.title("🚨 Password Error")
    root.geometry("420x300")
    root.configure(bg="#1e1e2f")
    root.resizable(False, False)
    root.eval('tk::PlaceWindow . center')
    root.attributes('-topmost', True)
    root.lift()
    root.focus_force()

    password_var = tk.StringVar()
    show_password = tk.BooleanVar(value=False)

    # =========================
    # 🚨 TITLE (RED WARNING)
    # =========================
    tk.Label(
        root,
        text="⚠️ Invalid Password",
        font=("Segoe UI", 14, "bold"),
        fg="#ff4d4d",
        bg="#1e1e2f"
    ).pack(pady=(15, 5))

    tk.Label(
        root,
        text=(f"Attempt {attempt} of {max_attempts}" if max_attempts else f"Attempt {attempt}"),
        font=("Segoe UI", 9),
        fg="#aaaaaa",
        bg="#1e1e2f"
    ).pack()

    # =========================
    # 🔴 ERROR MESSAGE (FLASHING)
    # =========================
    error_label = tk.Label(
        root,
        text="❌ Password incorrect!\nPlease enter correct password",
        font=("Segoe UI", 10, "bold"),
        fg="#ff4d4d",
        bg="#1e1e2f",
        wraplength=350,
        justify="center"
    )
    error_label.pack(pady=(10, 5))

    if error_msg:
        error_label.config(text=error_msg)

    # 🔥 FLASH EFFECT
    def flash(count=0):
        colors = ["#ff4d4d", "#1e1e2f"]
        error_label.config(fg=colors[count % 2])
        if count < 30:
            root.after(200, flash, count + 1)

    flash()

    # =========================
    # 🔐 PASSWORD FIELD
    # =========================
    entry = tk.Entry(
        root,
        textvariable=password_var,
        font=("Segoe UI", 11),
        show="*",
        width=30,
        bd=0
    )
    entry.pack(pady=12, ipady=6)
    entry.focus()

    # =========================
    # 👁 SHOW/HIDE PASSWORD
    # =========================
    def toggle_password():
        entry.config(show="" if show_password.get() else "*")

    tk.Checkbutton(
        root,
        text="Show Password",
        variable=show_password,
        command=toggle_password,
        fg="#dddddd",
        bg="#1e1e2f",
        activebackground="#1e1e2f",
        selectcolor="#1e1e2f"
    ).pack()

    # =========================
    # 🚀 SUBMIT BUTTON
    # =========================
    def submit(event=None):
        root.destroy()

    root.bind('<Return>', submit)

    tk.Button(
        root,
        text="Submit",
        command=submit,
        bg="#ff4d4d",
        fg="white",
        font=("Segoe UI", 10, "bold"),
        width=14,
        bd=0
    ).pack(pady=15)

    root.mainloop()

    return password_var.get().strip()


def get_password_popup(attempt, max_attempts, error_msg=None):
    # keep asking until something is entered (closing the window asks again)
    while True:
        password = _password_window(attempt, max_attempts, error_msg)
        if password:
            return password
        error_msg = "⚠ Password is required - please enter the password"


# =========================
# 🎨 TOKEN POPUP
# =========================
def _token_window(attempt, max_attempts, error_msg=None):
    root = tk.Tk()
    root.title("🔐 Secure Authentication")
    root.geometry("420x260")
    root.configure(bg="#1e1e2f")
    root.resizable(False, False)
    root.eval('tk::PlaceWindow . center')
    root.attributes('-topmost', True)
    root.lift()
    root.focus_force()

    token_var = tk.StringVar()

    tk.Label(root, text="Authentication Required",
             font=("Segoe UI", 14, "bold"),
             fg="#ffffff", bg="#1e1e2f").pack(pady=(15, 5))

    tk.Label(root, text=(f"Attempt {attempt} of {max_attempts}" if max_attempts else f"Attempt {attempt}"),
             font=("Segoe UI", 9),
             fg="#aaaaaa", bg="#1e1e2f").pack()

    if error_msg:
        tk.Label(root, text=error_msg,
                 font=("Segoe UI", 9),
                 fg="#ff4d4d", bg="#1e1e2f").pack(pady=(5, 5))

    entry = tk.Entry(root, textvariable=token_var,
                     font=("Segoe UI", 11),
                     show="*", width=30, bd=0)
    entry.pack(pady=15, ipady=6)
    entry.focus()

    def submit(event=None):
        root.destroy()

    root.bind('<Return>', submit)

    tk.Button(root, text="Submit", command=submit,
              bg="#4CAF50", fg="white",
              font=("Segoe UI", 10, "bold"),
              width=12, bd=0).pack(pady=10)

    root.mainloop()

    return token_var.get().strip()


def get_token_popup(attempt, max_attempts, error_msg=None):
    # the token is mandatory: keep asking until something is entered
    while True:
        token = _token_window(attempt, max_attempts, error_msg)
        if token:
            return token
        error_msg = "⚠ Token is required - please enter the token"


# =========================
# 🚨 SESSION POPUP
# =========================
def session_expired_popup():
    root = tk.Tk()
    root.title("⚠️ Session Expired")
    root.geometry("420x220")
    root.configure(bg="#1e1e2f")
    root.resizable(False, False)
    root.eval('tk::PlaceWindow . center')
    root.attributes('-topmost', True)
    root.lift()
    root.focus_force()

    tk.Label(root, text="Session Expired",
             font=("Segoe UI", 14, "bold"),
             fg="#ffcc00", bg="#1e1e2f").pack(pady=(20, 10))

    tk.Label(root,
             text="Your session has ended.\n\n"
                  "• Logged in from another system\n"
                  "• Session timeout\n\n"
                  "Please re-authenticate to continue.",
             font=("Segoe UI", 9),
             fg="#dddddd", bg="#1e1e2f",
             justify="center").pack(pady=5)

    tk.Button(root, text="Continue",
              command=root.destroy,
              bg="#ff9800", fg="white",
              font=("Segoe UI", 10, "bold"),
              width=12, bd=0).pack(pady=15)

    root.mainloop()

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

USER_ID = "56720CMMKR014139"
PASSWORD = "Mcx@2907"


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

MAX_TOKEN_RETRY = None   # keep asking until the correct token is entered
token_attempt = 0
error_msg = None

while True:
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





from datetime import datetime

export_time = datetime.now()
print(f"⏱ Export Trigger Time: {export_time.strftime('%Y%m%d%H%M%S')}")


# extracting timestamp from filename function

def extract_timestamp(filename):
    try:
        ts = filename.split("-")[1].replace(".xlsx", "")
        return datetime.strptime(ts, "%Y%m%d%H%M%S")
    except:
        return None


# ==================================================
# 🔄 STEP 1.1 OPENING DOWNLOAD MENU
# ==================================================


print("\n🌐 Opening Export Download Page (MANDATORY)...")

# STEP 1: PageLoader (same as browser)
session.get(
    BASE_URL + "/Bancs/common/PageLoader.html",
    headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html",
        "Referer": BASE_URL + "/Bancs/login.do?method=Login"
    }
)

# STEP 2: ARCHGUI INIT (VERY IMPORTANT)
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

    # 🔥 MUST
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
print("\n🔎 ARCH INIT RESPONSE CHECK:")
print("Length:", len(resp_arch_init.text))
print("First 300 chars:\n", resp_arch_init.text[:300])

print("\n🍪 COOKIES AFTER ARCH INIT:")
for k, v in session.cookies.get_dict().items():
    print(k, "=", v)







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


# =========================
# 🌐 INIT HEADERS (REQUIRED FOR PAGE LOAD)
# =========================
INIT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/Bancs/common/PageLoader.html",
}


# =========================
# 📡 HEADERS (AJAX) — REQUIRED FOR RSK136
# =========================
FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
}

# =========================
# 📅 DATE VARIABLES (REQUIRED)
# =========================
from datetime import datetime

today_api = datetime.now().strftime("%Y%m%d")          # 20260420
today_ui = datetime.now().strftime("%d/%m/%Y")         # 20/04/2026
today_encoded = today_ui.replace("/", "%2F")           # 20%2F04%2F2026

print("\n🌐 Opening SEARCH RESULT page...")

RSK136_URL = BASE_URL + "/Bancs/RSK/RSK136.do"

FETCH_HEADERS["Referer"] = RSK136_URL

init_payload_136 = {
    "app": "bancsapp?prefix=/RSK",
    "page": "/RSK136.do",
    "confViewSize": "50",
    "reqType": "0",
    "mode": "DEF_MODE",
    "parentName": "blank",
    "browserType": "IE55",
    "GuiWinName": "RSK136",
    "compname": "RSK",
    "over_ride_button": "Cancel",
    "AvlHt": "744",
    "AvlWd": "1481",
    "tblWd": "1465",
    "GuiBrowserInst": "0",
    "theme": "black",
    "pageId": "RSK136",
    "wintitle": "Query Security Collateral Valuation",
    "availHeight": "816",
    "availWidth": "1536",
    "tabId": "panel3",
    "rndaak": rndaak
}


res_init_136 = session.post(RSK136_URL, headers=INIT_HEADERS, data=init_payload_136)

print("\n📥 RSK136 INIT STATUS:", res_init_136.status_code)
print("📄 INIT LENGTH:", len(res_init_136.text))

print("\n🔎 INIT FIRST 500:\n", res_init_136.text[:500])

# 🔍 Check if any function/hidden fields present
matches = re.findall(r'name="([^"]+)" value="([^"]*)"', res_init_136.text)

print("\n🧠 HIDDEN FIELDS (FIRST 20):")
for m in matches[:20]:
    print(m)

print("\n📡 FINAL HEADERS USED FOR RSK136:")
for k, v in FETCH_HEADERS.items():
    print(f"{k}: {v}")



search_payload_136 = {
    "propertyMap(MCB_SearchWC_wca_exchange_Cmb)": "DFLT",
    "MCB_SearchWC_wca_exchange": "",
    "propertyMap(MCB_SearchWC_wca_exchange_op)": "=",

    "MCB_SearchWC_wca_market": "EXCH",
    "propertyMap(MCB_SearchWC_wca_market_op)": "=",

    "MCB_SearchWC_wca_segmentNo": "CMDE",
    "propertyMap(MCB_SearchWC_wca_segmentNo_op)": "=",

    "propertyMap(MCB_SearchWC_wca_BPID_Cmb)": "DFLT",
    "MCB_SearchWC_wca_BPID": "",
    "propertyMap(MCB_SearchWC_wca_BPID_op)": "=",

    "MCB_SearchWC_wca_AccountNo": "",
    "propertyMap(MCB_SearchWC_wca_AccountNo_op)": "=",

    "propertyMap(MCB_SearchWC_wca_instrument_Cmb)": "ISIN",
    "MCB_SearchWC_wca_instrument": "",
    "propertyMap(MCB_SearchWC_wca_instrument_op)": "=",

    "MCB_SearchWC_wca_collateralGroupId": "",
    "propertyMap(MCB_SearchWC_wca_collateralGroupId_op)": "=",

    "MCB_SearchWC_wca_blockingType": "",
    "propertyMap(MCB_SearchWC_wca_blockingType_op)": "=",

    "MCB_SearchWC_wca_cmcode": "",
    "propertyMap(MCB_SearchWC_wca_cmcode_op)": "=",

    "MCB_SearchWC_wca_cpcode": "",
    "propertyMap(MCB_SearchWC_wca_cpcode_op)": "=",

    "propertyMap(MCB_SearchWC_wca_tmcode_Cmb)": "DFLT",
    "MCB_SearchWC_wca_tmcode": "",
    "propertyMap(MCB_SearchWC_wca_tmcode_op)": "=",

    "MCB_SearchWC_wca_ucccode": "",
    "propertyMap(MCB_SearchWC_wca_ucccode_op)": "=",

    "MCB_SearchWC_wca_depositDate": today_api,
    "propertyMap(MCB_SearchWC_wca_depositDate_op)": "=",

    "MCB_SearchWC_wca_MaturityDate": today_api,
    "propertyMap(MCB_SearchWC_wca_MaturityDate_op)": "=",

    "MCB_SearchWC_wca_collateralPurpose": "",
    "propertyMap(MCB_SearchWC_wca_collateralPurpose_op)": "=",

    "MCB_SearchWC_wca_dummy11": "",
    "propertyMap(MCB_SearchWC_wca_dummy11_op)": "=",

    "MCB_SearchWC_wca_segregationType": "",
    "propertyMap(MCB_SearchWC_wca_segregationType_op)": "=",

    "MCB_SearchWC_wca_underlying": "",
    "propertyMap(MCB_SearchWC_wca_underlying_op)": "=",

    "MCB_SearchWC_wca_datedum": "",
    "propertyMap(MCB_SearchWC_wca_datedum_op)": "=",

    "MCB_SearchWC_wca_expiryDate": "",
    "propertyMap(MCB_SearchWC_wca_expiryDate_op)": "=",

    "GRID_RESPONSE": "RSK136_Table",
    "operationType": "ET",
    "searchClicked": "true",
    "PageNum": "0",

    "service": "querySecValuation",
    "windowName": "RSK136",
    "MODE": "DEF_MODE",
    "compName": "RSK",

    "lgbinst": "0",
    "WRITE_APP": "true",

    "REQ_TYPE": "0",
    "reqType": "1",

    "sQuery": f"Exchange  Equals  DFLT AND Market  Equals  EXCH AND Segment  Equals  CMDE AND CM ID  Equals  DFLT AND Instrument  Equals  ISIN AND TM Code  Equals  DFLT AND From Date  Equals  {today_encoded} AND To Date  Equals  {today_encoded}",

    "AvlWd": "1280",
    "tblWd": "1280",

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
    "GuiWinName": "RSK136",
    "compname": "RSK",

    "confViewSize": "50"
}


res_136 = session.post(RSK136_URL, headers=FETCH_HEADERS, data=search_payload_136)

print("\n📥 RSK136 STATUS:", res_136.status_code)
print("📄 RESPONSE LENGTH:", len(res_136.text))

# 🔍 CHECK COMMON FAILURE PATTERNS
if "sessionExpired" in res_136.text or "YOUR SESSION HAS EXPIRED" in res_136.text:
    print("🚨 SESSION EXPIRED DETECTED IN RSK136")
    print(res_136.text[:500])
    raise Exception("❌ SESSION EXPIRED DURING RSK136")

if "Exception" in res_136.text and "RSK136_Table" not in res_136.text:
    print("🚨 REAL SERVER ERROR")
    print(res_136.text[:1000])


if "RSK136_Table" not in res_136.text:
    print("\n❌ RSK136 TABLE NOT FOUND — FULL DEBUG BELOW:")
    
    print("\n🔎 FIRST 1000 CHARS:\n")
    print(res_136.text[:1000])

    print("\n🔎 LAST 1000 CHARS:\n")
    print(res_136.text[-1000:])

    # 🔍 Extract hidden messages if any
    hidden_msgs = re.findall(r"<span.*?>(.*?)</span>", res_136.text)
    if hidden_msgs:
        print("\n🧠 HIDDEN MESSAGES:")
        for msg in hidden_msgs[:10]:
            print("👉", msg.strip())

    raise Exception("❌ RSK136 search failed")

print("✅ RSK136 search successful")




export_payload_136 = search_payload_136.copy()

export_payload_136.update({
    "Export": "Excel",
    "enableBatchForExport": "yes",
    "noPgsToExport": "100",
    "PageNum": "1",
    "undefined": "",
    "MCBrowserEventInformation": "sourceElementName~|~undefined#*#eventType~|~callListExport#*#butId~|~null",
    "IXHRts": str(int(time.time() * 1000)),
})

res_export_136 = session.post(RSK136_URL, headers=FETCH_HEADERS, data=export_payload_136)

if "ExportBatchSuccess" not in res_export_136.text:
    raise Exception("❌ RSK136 export failed")

print("✅ RSK136 export triggered")
export_trigger_time_136 = datetime.now()
print(f"⏱ RSK136 Export Time: {export_trigger_time_136.strftime('%Y%m%d%H%M%S')}")



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

    MAX_TOKEN_RETRY = None   # keep asking until the correct token is entered
    token_attempt = 0

    session_expired_popup()
    error_msg = None

    while True:
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
# 🔗 EXPORT SEARCH URL (REQUIRED FOR POLLING)
# =========================
EXPORT_SEARCH_URL = BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do"

# =========================
# 🔄 STEP 2: POLLING (FINAL FIXED WITH REAL PAGINATION + AUTO RETRY)
# =========================

print("\n⏳ WAITING FOR SEARCH RESULT FILE...")
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

last_time_136 = None

target_file_136 = None
ready_136 = False
file_locked_136 = False

latest_export_time = export_trigger_time_136


while elapsed < max_wait:

    print(f"\n🔄 Checking export files... (elapsed: {elapsed}s)")

   

    payload = {
        "MCB_SearchWC_wca_EXPORT_WINDOW_NAME": "",
        "propertyMap(MCB_SearchWC_wca_EXPORT_WINDOW_NAME_op)": "=",

        "MCB_SearchWC_wca_EXPORT_TABLE_NAME": "",
        "propertyMap(MCB_SearchWC_wca_EXPORT_TABLE_NAME_op)": "=",

        "MCB_SearchWC_wca_EXPORT_DATE_FROM": "",
        "propertyMap(MCB_SearchWC_wca_EXPORT_DATE_FROM_From)": export_trigger_time_136.strftime("%d/%m/%Y"),
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


        "sQuery": "  Equals  " + export_trigger_time_136.strftime("%Y%m%d"),

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
    # 📄 PAGE 1 (WITH AUTO RETRY)
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
            print("🚨 Session expired during Page 1 → Re-login")
            do_relogin()
            continue
        else:
            raise

    soup = BeautifulSoup(res_first.text, "html.parser")
    rows = soup.select("table tbody tr")

    print(f"📄 Page 1 → {len(rows)} rows")

    if len(rows) == 0:
        print("⚠️ Empty response — possible session issue")

    all_rows.extend(rows)

    # 🔥 TOTAL RECORDS
    tot_match = re.search(r'id="totNoOfRec".*value="(\d+)"', res_first.text)
    total_records = int(tot_match.group(1)) if tot_match else 0

    print("📊 TOTAL RECORDS:", total_records)

    # =========================
    # 📄 PAGE 2 (WITH AUTO RETRY — NO LOGIC CHANGE)
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

        "sQuery": "  Equals  " + export_trigger_time_136.strftime("%Y%m%d"),

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
            print("🚨 Session expired during Page 2 → Re-login")
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
    # 🔍 PROCESS ROWS (UNCHANGED)
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

            buffer = timedelta(seconds=2)

            
            # =========================
            # 🔒 RSK136 LOCK LOGIC
            # =========================
            if (
                not file_locked_136
                and abs((file_time - export_trigger_time_136).total_seconds()) <= 50
                and "SECURITY VALUATION" in cols[0].text
            ):
                target_file_136 = file_name
                last_time_136 = file_time
                file_locked_136 = True
                print(f"🔒 LOCKED RSK136 FILE: {target_file_136}")

            if file_locked_136 and file_name == target_file_136:
                if "Generated" in status:
                    ready_136 = True    

            
        except:
            continue

    if ready_136:
        print("\n🎉 RSK136 FILE READY")
        break

    time.sleep(interval)
    elapsed += interval


# =========================
# VALIDATION (UNCHANGED)
# =========================

if not target_file_136:
    raise Exception("❌ RSK136 not found")

if not ready_136:
    raise Exception("❌ RSK136 file not generated in time")


# =========================
# ⬇️ STEP 3: DOWNLOAD FILES (383 + 335)
# =========================

print("\n⬇️ STARTING DOWNLOAD PROCESS FOR BOTH FILES...")

files_to_download = [
    ("RSK136", target_file_136),
]

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do?app=bancsapp?prefix=/ARCHGUI"
}

for label, file_name in files_to_download:

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
    save_path = os.path.join(SEARCH_FOLDER, "SearchResults.xlsx")

    with open(save_path, "wb") as f:
        for chunk in download_res.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    print(f"✅ {label} DOWNLOADED: {save_path}")




print("\n🎉 ALL FILES DOWNLOADED SUCCESSFULLY")
print("\n🛑 Script finished — closing now")