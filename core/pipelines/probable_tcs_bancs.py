"""
core/pipelines/probable_tcs_bancs.py — TCS BANCS downloads for the Probable
Defaulter pipeline: RSK383 (Cash Collateral Deposit Limit / "Query Detailed
Limit") + RSK335 (Margin Utilization View / "Clientwise MCX Margin").

Ported from the reference script codes/API HITTING/tcs_bancs_probable.py in
the user-supplied "probable for claude" / "PROBABLE FINAL NEW" folders (READ
ONLY sources — nothing there is ever modified; this file is the RMS Copilot
port).

════════════════════════════════════════════════════════════════════════════
FLOW — matches the reference script:
    1. ONE login (password → RSA token → final login → post-login nav)
    2. RSK383: open → search → TRIGGER export       (do NOT wait)
    3. ARCH init (Download Exported Files page, grab tabId)
    4. RSK335: open → pre-call → search → TRIGGER export   (do NOT wait)
    5. ONE polling loop that waits for BOTH files to reach "Generated",
       with AUTO RE-LOGIN if the session dies mid-wait (see below)
    6. Download both

Both exports are queued BEFORE either is waited on, because TCS BANCS takes
minutes to generate each one server-side — queuing both up front lets the
second generate while the first is still baking. (An earlier version of this
file ran a full login→export→wait→download cycle for RSK383 and then another
complete one for RSK335, which meant two RSA token prompts and two waits
running back to back instead of overlapping.)

AUTO RE-LOGIN — this is why the wait loop is written the way it is. The
eclear account tolerates only ONE live session, so if anybody else logs in
to the same account (or the session simply times out) while we're sitting in
the 15s poll loop, every subsequent poll comes back as "session expired".
The reference script handles this with do_relogin()/reinitialize_arch():
re-authenticate, re-init the ARCH page for a fresh tabId, and RESUME the
same wait — the exports are still queued server-side and still generating,
so there is nothing to redo. What must NOT happen is aborting the whole
download, because the operator then has to trigger two fresh exports and
wait all over again for files that were already nearly ready.

Credentials: SA_MCX_DEPOSIT_USER / SA_MCX_DEPOSIT_PASSWORD — the EXACT SAME
eclear.mcxccl.com account already used for RSK383 elsewhere in this repo
(Short Allocation's "MCX Deposit" button, Client Prudential Limit's "MCX Web
Allocation" button). No new credential pair is introduced. The shared
cross-process TCS BANCS session lock (_acquire_mcx_lock/_release_mcx_lock in
sa_codes/mcx_deposit_downloader.py) is claimed ONCE for the whole flow, so
this can never race against those other two buttons.

Output (into PROBABLE_FOLDER):
  RSK383 → saved under the RAW filename TCS BANCS itself generates
           (<random>-<YYYYMMDDHHMMSS>.xlsx). This matters: probable_reconcile.py's
           read_mcx_file() globs for "*<todayYYYYMMDD>*.xlsx", and
           /probable/readiness checks MCX_RSK383 with the same pattern.
  RSK335 → Clientwise_mcx_margin.xlsx  (matches the reference script's own
           save_path naming exactly, so probable_reconcile.py's file lookups
           can use the identical filename the original code1.py expects)

Progress reporting: stage changes are printed as "[FRIENDLY_MSG] …" lines,
which core/pipeline_box.py's _status() turns into {"type":"note"} SSE events
that the PROBABLE box's status strip renders live. Without these the strip
sat on "Logging in to TCS BANCS… an RSA token prompt may appear." for the
entire multi-minute run, long after the token had been entered.
"""
import os
import re
import sys
import time
import datetime as _dt

import requests
from bs4 import BeautifulSoup

_AUTH_BAD = re.compile(r"\[MCX_DEPOSIT_(AUTH_FAIL|LOCKED)\]", re.I)

BASE_URL = "https://eclear.mcxccl.com"

LOGIN_URL       = BASE_URL + "/Bancs/login.do?method=authenticatePassward"
TOKEN_URL       = BASE_URL + "/Bancs/login.do?method=authenticateToken"
FINAL_LOGIN_URL = BASE_URL + "/Bancs/login.do?method=Login"
RSK383_URL      = BASE_URL + "/Bancs/RSK/RSK383.do"
RSK335_URL      = BASE_URL + "/Bancs/RSK/RSK335.do"
ARCH_URL        = BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do"

# Shown inside the dashboard's RSA token modal when the session died mid-run
# and we're asking for a fresh token to resume (the reference script pops its
# own separate "Session Expired" window first; the web modal has no equivalent,
# so the explanation is carried in the token prompt's own error_msg slot).
_SESSION_EXPIRED_MSG = (
    "⚠️ Session expired — someone else logged in to this TCS BANCS account, "
    "or the session timed out. Enter a fresh RSA SecurID token to continue; "
    "the download will resume from where it stopped."
)


def _mdd():
    """Import sa_codes/mcx_deposit_downloader.py the same way
    cli_mcx_download.py and core/lock_manager.py already do (sa_codes has no
    __init__.py, so it's added to sys.path rather than imported as a
    sub-package)."""
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sa_dir = os.path.join(_repo, "sa_codes")
    if sa_dir not in sys.path:
        sys.path.insert(0, sa_dir)
    import importlib
    return importlib.import_module("mcx_deposit_downloader")


# ═══════════════════════════════════════════════════════════════════════════
# "ALREADY FRESH — SKIP THE LOGIN" PRE-CHECK
#
# Same precedent as core/pipelines/nmass_download.py's check_nmass_files_fresh()
# (NMASS/NCDEX skips its login if today's 4 files are already on disk and
# recent — 30 min for PROBABLE) and core/pipelines/mcx90_download.py's
# _today_mcx_file() (CTCL/MS15's MCX 90% button skips if today's file is
# already in its folder) — this file didn't have the equivalent, so clicking
# TCS BANCS always re-logged-in and re-downloaded both reports even when both
# were already sitting there from minutes ago. Every TCS BANCS round trip
# costs a full login + RSA token prompt + minutes of waiting for the exchange
# to generate the reports, so skipping it when nothing would actually change
# is worth doing.
#
# 1 hour, not "any time today" — TCS BANCS margin figures move through the
# day, so a file from first thing this morning being silently reused at 3pm
# would be stale data wearing a fresh-looking filename. RSK383 carries its own
# generation timestamp in the exported filename (<random>-YYYYMMDDHHMMSS.xlsx)
# — same pattern read_mcx_file()/probable/readiness use to find it, so age is
# read straight from that stamp. RSK335 is renamed to the fixed
# Clientwise_mcx_margin.xlsx with no date in the name, so its age falls back
# to the file's modified time (same fallback check_nmass_files_fresh() uses
# for ClientNetEffectiveDeposits).
# ═══════════════════════════════════════════════════════════════════════════

_TCS_MAX_AGE_MIN = 60.0  # 1 hour

_RSK383_STAMP_RE = re.compile(r"-(\d{8})(\d{6})\.xlsx$", re.I)


def _fresh_rsk383_file(output_dir, max_age_min=_TCS_MAX_AGE_MIN):
    """RSK383 file in output_dir generated within the last `max_age_min`
    minutes (per its own filename stamp), or None."""
    now = _dt.datetime.now()
    try:
        for f in os.listdir(output_dir):
            if not f.lower().endswith(".xlsx") or f == "Clientwise_mcx_margin.xlsx":
                continue
            m = _RSK383_STAMP_RE.search(f)
            if not m:
                continue
            try:
                stamp = _dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            except ValueError:
                continue
            age_min = (now - stamp).total_seconds() / 60.0
            if 0 <= age_min <= max_age_min:
                return os.path.join(output_dir, f)
    except Exception:
        pass
    return None


def _fresh_rsk335_file(output_dir, max_age_min=_TCS_MAX_AGE_MIN):
    """Clientwise_mcx_margin.xlsx in output_dir, if modified within the last
    `max_age_min` minutes (no date in its own name, so age comes from mtime),
    or None."""
    path = os.path.join(output_dir, "Clientwise_mcx_margin.xlsx")
    if not os.path.isfile(path):
        return None
    try:
        mtime = _dt.datetime.fromtimestamp(os.path.getmtime(path))
    except Exception:
        return None
    age_min = (_dt.datetime.now() - mtime).total_seconds() / 60.0
    return path if 0 <= age_min <= max_age_min else None


def _stage(msg):
    """Print a stage change for the dashboard status strip (see module
    docstring) AND the plain log line, so the saved log still reads normally."""
    print(f"[FRIENDLY_MSG] {msg}")


# ── header sets (lifted verbatim from the reference script) ────────────────

def _headers():
    ajax = {"User-Agent": "Mozilla/5.0", "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL,
            "Referer": BASE_URL + "/Bancs/common/dologin.jsp?method=authMode&JSLoadingMode=null"}
    login = {"User-Agent": "Mozilla/5.0",
             "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
             "Referer": BASE_URL + "/Bancs/common/dologin.jsp?method=authMode&JSLoadingMode=null",
             "Upgrade-Insecure-Requests": "1"}
    common = {"User-Agent": "Mozilla/5.0",
              "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
              "Referer": BASE_URL + "/Bancs/login.do?method=Login&availHeight=744&availWidth=1522",
              "Sec-Fetch-Site": "same-origin"}
    nav = {**common, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe",
           "Upgrade-Insecure-Requests": "1"}
    ajax2 = {**common, "X-Requested-With": "XMLHttpRequest", "Accept": "*/*",
             "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
             "Referer": BASE_URL + "/Bancs/common/welcome.jsp"}
    init = {"User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
            "Referer": BASE_URL + "/Bancs/common/PageLoader.html"}
    dl = {"User-Agent": "Mozilla/5.0", "Accept": "*/*",
          "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
          "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL,
          "Referer": BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do?app=bancsapp?prefix=/ARCHGUI"}
    return ajax, login, nav, ajax2, init, dl


# ── login (used for BOTH the first login and every auto re-login) ──────────

class _AuthFailed(Exception):
    """Wrong password — not retryable, and distinct from a session expiry."""


def _login(sess, user_id, password, mdd, hdrs, session_expired=False):
    """Full authentication: password → RSA token (3 attempts) → final login →
    post-login navigation. Returns the fresh rndaak.

    session_expired=True only changes the message shown in the token modal —
    the actual sequence is identical, which is exactly why the reference
    script's do_relogin() can reuse it verbatim."""
    AJAX_H, LOGIN_H, NAV_H, AJAX2_H, _INIT_H, _DL_H = hdrs

    sess.get(BASE_URL)
    sess.post(BASE_URL + "/Bancs/login.do?method=authMode", headers=AJAX_H)

    print("🔐 PASSWORD STEP")
    pw_enc, ign = mdd._encrypt_value(password)
    pw_resp = sess.post(LOGIN_URL, headers=AJAX_H,
                        data={"userId": user_id + "\t", "password": pw_enc,
                              "lng": "1", "ignoreFlag": ign})
    if "INVALID_LOGIN_OR_PASSWORD" in pw_resp.text or "error_div1" in pw_resp.text:
        print("[MCX_DEPOSIT_AUTH_FAIL] wrong password — update "
              "SA_MCX_DEPOSIT_PASSWORD in Credentials")
        raise _AuthFailed("wrong password")

    print("🔴 TOKEN STEP")
    if session_expired:
        _stage("🔑 Session expired — waiting for a fresh RSA SecurID token…")
    else:
        _stage("🔑 Waiting for the RSA SecurID token…")

    entity = entity_codes = None
    err_msg = _SESSION_EXPIRED_MSG if session_expired else None
    for attempt in range(1, 4):
        token = mdd._token_popup(attempt, 3, err_msg)
        t_enc, t_ign = mdd._encrypt_value(token)
        resp = sess.post(TOKEN_URL, headers=AJAX_H,
                         data={"userId": user_id + "\t", "password": t_enc,
                               "lng": "1", "ignoreFlag": t_ign})
        try:
            entity, entity_codes = mdd._extract_entity(resp.text)
            print(f"✅ Entity: {entity}")
            break
        except Exception:
            err_msg = "❌ Invalid token. Please try again."
            if attempt == 3:
                raise RuntimeError("Max token attempts reached")

    print("🚀 FINAL LOGIN")
    fp_enc, f_ign = mdd._encrypt_value(password)
    resp3 = sess.post(FINAL_LOGIN_URL, headers=LOGIN_H,
                      data={"method": "Login", "availHeight": "744", "availWidth": "1522",
                            "isArchival": "false", "userEntityCodes": entity_codes,
                            "lng": "1", "userId": user_id, "ignoreFlag": f_ign,
                            "ArchivalignoreFlag": "", "password": fp_enc,
                            "dummy": "FltM/sA2fPfqsjtM55oLOQ==", "entity": entity})
    rndaak = None
    for pat in [r"name=['\"]rndaak['\"][^>]*value=['\"]([^'\"]+)",
                r"rndaak['\"]?\s*:\s*['\"]([^'\"]+)"]:
        m = re.search(pat, resp3.text)
        if m:
            rndaak = m.group(1); break
    if not rndaak:
        raise RuntimeError("rndaak not found in final-login response")
    print("✅ rndaak:", rndaak)

    # Reference script sleeps briefly here before the post-login flow on a
    # relogin — the server rejects the nav sequence if it arrives too fast.
    if session_expired:
        time.sleep(2)

    sess.post(BASE_URL + "/Bancs/common/CommonImpl.jsp", headers=NAV_H,   data={"rndaak": rndaak})
    sess.post(BASE_URL + "/Bancs/common/welcome.jsp",    headers=NAV_H,   data={"rndaak": rndaak})
    sess.post(BASE_URL + "/Bancs/common/layout.save",    headers=AJAX2_H, data={"events": "", "rndaak": rndaak})
    sess.post(BASE_URL + "/Bancs/common/welcome.jsp",    headers=NAV_H,   data={"rndaak": rndaak})
    print("🎉 Session fully active")
    return rndaak


def _arch_init(sess, init_h, rndaak):
    """Open the "Download Exported Files" (ARCHGUI) page and return its tabId.
    Re-run after every relogin — the old tabId belongs to the dead session."""
    sess.get(BASE_URL + "/Bancs/common/PageLoader.html",
             headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html",
                      "Referer": BASE_URL + "/Bancs/login.do?method=Login"})
    resp_arch = sess.post(ARCH_URL, headers=init_h,
                          data={"app": "bancsapp?prefix=/ARCHGUI", "page": "/ar_w_DataExport.do",
                                "confViewSize": "50", "reqType": "0", "mode": "DEF_MODE",
                                "parentName": "blank", "browserType": "IE55",
                                "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI",
                                "over_ride_button": "Cancel", "AvlHt": "678", "AvlWd": "914",
                                "tblWd": "898", "GuiBrowserInst": "0", "theme": "black",
                                "pageId": "ar_w_DataExport", "wintitle": "Download Exported Files",
                                "availHeight": "816", "availWidth": "1536", "tabId": "panel4",
                                "rndaak": rndaak, "reqtoken": "", "parentToChildData": ""})
    if "sessionExpired" in resp_arch.text:
        raise RuntimeError("ARCH init failed — session still not valid after login")
    tab_m = re.search(r'id="tabId"[^>]*value="([^"]+)"', resp_arch.text)
    if not tab_m:
        raise RuntimeError("tabId not found in ARCH response")
    return tab_m.group(1)


# ── payload builders (lifted verbatim from the reference script) ───────────

def _rsk383_search_payload(rndaak):
    today_api = _dt.datetime.now().strftime("%Y%m%d")
    today_enc = _dt.datetime.now().strftime("%d/%m/%Y").replace("/", "%2F")
    return {
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
        "sQuery": f"Transaction From Date  Equals  {today_enc} AND Transaction To Date  Equals  {today_enc}",
        "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
        "IXHRts": str(int(time.time() * 1000)), "XHR": "true",
        "rndaak": rndaak, "theme": "black", "tabId": "panel2",
        "mode": "DEF_MODE", "GuiBrowserInst": "0", "parentName": "blank",
        "browserType": "IE55", "GuiWinName": "RSK383", "compname": "RSK",
        "confViewSize": "50",
    }


def _rsk335_search_payload(rndaak):
    return {
        "propertyMap(MCB_SearchWC_wca_bpid_Cmb)": "DFLT", "MCB_SearchWC_wca_bpid": "",
        "propertyMap(MCB_SearchWC_wca_bpid_op)": "=", "MCB_SearchWC_wca_actype": "",
        "propertyMap(MCB_SearchWC_wca_actype_op)": "=",
        "propertyMap(MCB_SearchWC_wca_associatedtm_Cmb)": "DFLT", "MCB_SearchWC_wca_associatedtm": "",
        "propertyMap(MCB_SearchWC_wca_associatedtm_op)": "=",
        "propertyMap(MCB_SearchWC_wca_associatedcm_Cmb)": "DFLT", "MCB_SearchWC_wca_associatedcm": "56720",
        "propertyMap(MCB_SearchWC_wca_associatedcm_op)": "=", "MCB_SearchWC_wca_valuedate": "",
        "propertyMap(MCB_SearchWC_wca_valuedate_op)": "=", "MCB_SearchWC_wca_category": "",
        "propertyMap(MCB_SearchWC_wca_category_op)": "=", "MCB_SearchWC_wca_CMName": "DFLT",
        "propertyMap(MCB_SearchWC_wca_CMName_op)": "=", "MCB_SearchWC_wca_TMName": "DFLT",
        "propertyMap(MCB_SearchWC_wca_TMName_op)": "=", "MCB_SearchWC_wca_datedummy1": "",
        "propertyMap(MCB_SearchWC_wca_datedummy1_op)": "=", "MCB_SearchWC_wca_datedummy2": "",
        "propertyMap(MCB_SearchWC_wca_datedummy2_op)": "=",
        "GRID_RESPONSE": "RSK335_Table", "operationType": "ET",
        "searchClicked": "true", "PageNum": "0",
        "service": "qryMarginUtilView", "windowName": "RSK335",
        "MODE": "DEF_MODE", "compName": "RSK",
        "lgbinst": "0", "WRITE_APP": "true", "REQ_TYPE": "0", "reqType": "1",
        "sQuery": "Client Code  Equals  DFLT AND TM / CP  Equals  DFLT AND CM  Equals  DFLT AND CM  Equals  56720",
        "AvlWd": "1048", "tblWd": "1026",
        "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
        "IXHRts": str(int(time.time() * 1000)), "XHR": "true",
        "rndaak": rndaak, "theme": "black", "tabId": "panel3",
        "mode": "DEF_MODE", "GuiBrowserInst": "0", "parentName": "blank",
        "browserType": "IE55", "GuiWinName": "RSK335", "compname": "RSK",
        "confViewSize": "50",
    }


def _export_queue_payload(rndaak, tab_id, trigger_time):
    """Payload for the "Download Exported Files" (ARCHGUI) grid — the queue
    both exports land in. Filtered to today, same as the reference."""
    return {
        "MCB_SearchWC_wca_EXPORT_WINDOW_NAME": "",
        "propertyMap(MCB_SearchWC_wca_EXPORT_WINDOW_NAME_op)": "=",
        "MCB_SearchWC_wca_EXPORT_TABLE_NAME": "",
        "propertyMap(MCB_SearchWC_wca_EXPORT_TABLE_NAME_op)": "=",
        "MCB_SearchWC_wca_EXPORT_DATE_FROM": "",
        "propertyMap(MCB_SearchWC_wca_EXPORT_DATE_FROM_From)": trigger_time.strftime("%d/%m/%Y"),
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
        "lgbinst": "0", "WRITE_APP": "true", "REQ_TYPE": "0", "reqType": "1",
        "sQuery": "  Equals  " + trigger_time.strftime("%Y%m%d"),
        "AvlWd": "1280", "tblWd": "1280",
        "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
        "IXHRts": str(int(time.time() * 1000)), "XHR": "true",
        "rndaak": rndaak, "theme": "black", "tabId": tab_id,
        "mode": "DEF_MODE", "GuiBrowserInst": "0", "parentName": "blank",
        "confViewSize": "50", "browserType": "IE55",
        "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI",
        "over_ride_button": "Cancel",
    }


def _export_queue_page2_payload(rndaak, tab_id, trigger_time):
    """Page 2 of the export queue. The reference script always checks a second
    page — with TWO exports queued plus whatever else the account generated
    today, the pair can straddle the 50-row page boundary, and missing one
    would make this loop wait out the full timeout for a file already sitting
    there marked Generated."""
    return {
        "tabId": tab_id, "GuiBrowserInst": "0",
        "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI",
        "xmlReq": "YES", "reqType": "1", "confViewSize": "50", "PageNum": "0",
        "butvalue": "8", "selectedRow_ar_w_DataExport": "49",
        "tblurl": "ar_w_DataExport_Table",
        "MCBrowserEventInformation": "sourceElementName~|~next#*#eventType~|~click#*#butId~|~8",
        "rndaak": rndaak, "theme": "black",
        "sQuery": "  Equals  " + trigger_time.strftime("%Y%m%d"),
        "AvlWd": "1208", "tblWd": "1186",
        "RowNumKeyForPrevNxt": "1", "PrevOrNext": "N",
    }


# ── the one and only entry point ───────────────────────────────────────────

def download_both(output_dir, force=False):
    """Log in ONCE, trigger BOTH exports, wait for both together (re-logging
    in automatically if the session dies mid-wait), download both.
    Returns {"rsk383": path|None, "rsk335": path|None}.

    Skips the ENTIRE flow — no login, no RSA token prompt — if BOTH RSK383 and
    RSK335 in output_dir were generated within the last hour (pass force=True
    to override and re-download regardless). If only ONE of the two is fresh,
    both are still re-downloaded anyway: they share one login and one
    session, so there's no cheaper way to fetch just the missing/stale one,
    and re-fetching the other costs nothing extra once you're already logged
    in.

    Raises MCXDepositBusyError (from mcx_deposit_downloader) if another box
    is holding the shared TCS BANCS session lock right now — the caller
    should catch that specifically, exactly like cli_mcx_download.py does,
    so the dashboard can show a clean "already in use" message."""
    mdd = _mdd()
    os.environ["MCX_DEPOSIT_CALLER"] = "probable"

    if not force:
        _have_383 = _fresh_rsk383_file(output_dir)
        _have_335 = _fresh_rsk335_file(output_dir)
        if _have_383 and _have_335:
            print(f"✅ RSK383 already fresh (< {_TCS_MAX_AGE_MIN:.0f} min): {os.path.basename(_have_383)}")
            print(f"✅ RSK335 already fresh (< {_TCS_MAX_AGE_MIN:.0f} min): {os.path.basename(_have_335)}")
            _stage(f"✅ Both files already downloaded within the last hour — "
                   f"skipping TCS BANCS login entirely.")
            return {"rsk383": _have_383, "rsk335": _have_335}
        elif _have_383 or _have_335:
            _which = "RSK383" if _have_383 else "RSK335"
            _missing = "RSK335" if _have_383 else "RSK383"
            print(f"ℹ️ {_which} is fresh, but {_missing} is missing or older than "
                  f"{_TCS_MAX_AGE_MIN:.0f} min — both share one login, so both "
                  f"will be (re-)downloaded.")

    USER_ID  = os.getenv("SA_MCX_DEPOSIT_USER",     "56720CMMKR014139").strip()
    PASSWORD = os.getenv("SA_MCX_DEPOSIT_PASSWORD", "").strip()
    if not PASSWORD:
        print("[MCX_DEPOSIT_AUTH_FAIL] SA_MCX_DEPOSIT_PASSWORD not set in .env "
              "— cannot download RSK383/RSK335")
        return {"rsk383": None, "rsk335": None}

    hdrs = _headers()
    AJAX_H, LOGIN_H, NAV_H, AJAX2_H, INIT_H, DL_H = hdrs

    mdd._acquire_mcx_lock("probable")
    try:
        # ── 1. LOGIN (once) ────────────────────────────────────────────────
        _stage("🔐 Logging in to TCS BANCS…")
        print("\n🌐 Opening session (TCS BANCS)...")
        sess = requests.Session()
        rndaak = _login(sess, USER_ID, PASSWORD, mdd, hdrs)

        # ── 2. RSK383: open → search → TRIGGER export (no waiting) ─────────
        _stage("📤 Logged in — queuing the RSK383 export…")
        print("\n🌐 Opening RSK383 (Query Detailed Limit)...")
        sess.post(RSK383_URL, headers=INIT_H,
                  data={"app": "bancsapp?prefix=/RSK", "page": "/RSK383.do", "confViewSize": "50",
                        "reqType": "0", "mode": "DEF_MODE", "parentName": "blank",
                        "browserType": "IE55", "GuiWinName": "RSK383", "compname": "RSK",
                        "over_ride_button": "Cancel", "AvlHt": "744", "AvlWd": "1481",
                        "tblWd": "1465", "GuiBrowserInst": "0", "theme": "black",
                        "pageId": "RSK383", "wintitle": "Query Detailed Limit",
                        "availHeight": "816", "availWidth": "1536", "tabId": "panel2",
                        "rndaak": rndaak, "reqtoken": "", "parentToChildData": ""})

        FETCH383_H = {"User-Agent": "Mozilla/5.0", "Accept": "*/*",
                      "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                      "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL,
                      "Referer": RSK383_URL}

        print("🔍 Running RSK383 search...")
        search383 = _rsk383_search_payload(rndaak)
        sr383 = sess.post(RSK383_URL, headers=FETCH383_H, data=search383)
        if "RSK383_Table" not in sr383.text:
            raise RuntimeError("RSK383 search failed — RSK383_Table not in response")
        print("✅ RSK383 search successful")

        trigger_383 = _dt.datetime.now()
        export383 = {**search383,
                     "Export": "Excel", "enableBatchForExport": "yes", "noPgsToExport": "50",
                     "dateStamp": trigger_383.strftime("%Y%m%d"),
                     "timeStamp": trigger_383.strftime("%H:%M:%S"),
                     "PageNum": "1", "undefined": "",
                     "MCBrowserEventInformation":
                         "sourceElementName~|~undefined#*#eventType~|~callListExport#*#butId~|~null",
                     "IXHRts": str(int(time.time() * 1000)), "XHR": "true"}
        er383 = sess.post(RSK383_URL, headers=FETCH383_H, data=export383)
        if "ExportBatchSuccess" not in er383.text:
            raise RuntimeError("RSK383 export trigger failed")
        print(f"✅ RSK383 export QUEUED at {trigger_383.strftime('%H:%M:%S')} "
              f"— not waiting, queuing RSK335 next")

        # ── 3. ARCH init (Download Exported Files page → tabId) ────────────
        print("\n🌐 Opening Export Download page...")
        tab_id = _arch_init(sess, INIT_H, rndaak)
        print(f"✅ TAB ID: {tab_id}")

        # ── 4. RSK335: open → pre-call → search → TRIGGER export ───────────
        _stage("📤 RSK383 queued — queuing the RSK335 export…")
        print("\n🌐 Opening RSK335 (Margin Utilization View)...")
        sess.post(RSK335_URL, headers=INIT_H,
                  data={"app": "bancsapp?prefix=/RSK", "page": "/RSK335.do", "confViewSize": "50",
                        "reqType": "0", "mode": "DEF_MODE", "parentName": "blank",
                        "browserType": "IE55", "GuiWinName": "RSK335", "compname": "RSK",
                        "over_ride_button": "Cancel", "AvlHt": "744", "AvlWd": "1481",
                        "tblWd": "1465", "GuiBrowserInst": "0", "theme": "black",
                        "pageId": "RSK335", "wintitle": "Margin Utilization View",
                        "availHeight": "816", "availWidth": "1536", "tabId": "panel3",
                        "rndaak": rndaak})

        FETCH335_H = {"User-Agent": "Mozilla/5.0", "Accept": "*/*",
                      "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                      "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL,
                      "Referer": RSK335_URL}

        print("⚙️ Running RSK335 pre-call...")
        sess.post(RSK335_URL, headers=FETCH335_H,
                  data={"xmlReq": "YES", "reqType": "19", "GuiWinName": "RSK335",
                        "GuiBrowserInst": "0", "inParam1": "56720", "inParam2": "DFLT",
                        "tabId": "panel3", "functionName": "funcname", "ifCallPaint": "false",
                        "ctrlId": "MCB_SearchWC_wca_associatedcm", "filterCombo": "NO",
                        "filterS": "sample", "rndaak": rndaak})

        print("🔍 Running RSK335 search...")
        search335 = _rsk335_search_payload(rndaak)
        sr335 = sess.post(RSK335_URL, headers=FETCH335_H, data=search335)
        if "RSK335_Table" not in sr335.text:
            raise RuntimeError("RSK335 search failed — RSK335_Table not in response")
        print("✅ RSK335 search successful")

        trigger_335 = _dt.datetime.now()
        export335 = {**search335,
                     "Export": "Excel", "enableBatchForExport": "yes", "noPgsToExport": "50",
                     "PageNum": "1", "undefined": "",
                     "MCBrowserEventInformation":
                         "sourceElementName~|~undefined#*#eventType~|~callListExport#*#butId~|~null",
                     "IXHRts": str(int(time.time() * 1000))}
        er335 = sess.post(RSK335_URL, headers=FETCH335_H, data=export335)
        if "ExportBatchSuccess" not in er335.text:
            raise RuntimeError("RSK335 export trigger failed")
        print(f"✅ RSK335 export QUEUED at {trigger_335.strftime('%H:%M:%S')}")

        # ── 5. ONE wait for BOTH (with auto re-login) ──────────────────────
        _stage("⏳ Both exports queued — waiting for TCS BANCS to generate them "
               "(usually a few minutes)…")
        print("\n⏳ WAITING FOR BOTH FILES (both exports are generating in parallel)...")
        target_383 = target_335 = None
        ready_383 = ready_335 = False
        max_wait, interval, elapsed = 900, 15, 0
        relogins = 0
        MAX_RELOGINS = 3

        while elapsed < max_wait:
            print(f"\n🔄 Checking export queue... (elapsed: {elapsed}s)")
            rows = []

            res_p1 = sess.post(ARCH_URL, headers=DL_H,
                               data=_export_queue_payload(rndaak, tab_id, trigger_383))

            # ── AUTO RE-LOGIN ──────────────────────────────────────────────
            # The exports are already queued server-side and keep generating
            # regardless of our session, so a expiry here is recoverable:
            # re-authenticate, get a fresh tabId, and resume this same loop
            # with target_383/target_335/ready_* untouched. Aborting instead
            # (what this used to do) threw away a wait that was often nearly
            # finished and forced the operator to start two fresh exports.
            if mdd._is_expired(res_p1.text):
                relogins += 1
                if relogins > MAX_RELOGINS:
                    raise RuntimeError(
                        f"TCS BANCS session expired {MAX_RELOGINS}x — giving up. "
                        f"Someone else is very likely logged in to this account "
                        f"right now; ask them to log out, then re-run.")
                print(f"\n🚨 SESSION EXPIRED during poll — re-login "
                      f"({relogins}/{MAX_RELOGINS})")
                _stage("🚨 Session expired (someone else logged in, or it timed out) "
                       "— enter a fresh RSA token to resume. Your exports are still "
                       "generating; nothing is lost.")
                sess = requests.Session()
                rndaak = _login(sess, USER_ID, PASSWORD, mdd, hdrs, session_expired=True)
                tab_id = _arch_init(sess, INIT_H, rndaak)
                print(f"✅ ARCH ready again | tab_id: {tab_id}")
                _stage("✅ Session restored — resuming the wait for both files…")
                continue

            rows.extend(BeautifulSoup(res_p1.text, "html.parser").select("table tbody tr"))
            print(f"  📄 Page 1 → {len(rows)} rows")

            res_p2 = sess.post(ARCH_URL, headers=DL_H,
                               data=_export_queue_page2_payload(rndaak, tab_id, trigger_383))
            if not mdd._is_expired(res_p2.text):
                p2 = BeautifulSoup(res_p2.text, "html.parser").select("table tbody tr")
                if p2:
                    print(f"  📄 Page 2 → {len(p2)} rows")
                    rows.extend(p2)

            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 7:
                    continue
                status        = cols[4].text.strip()
                timestamp_str = cols[5].text.strip()
                file_name     = cols[6].text.strip()
                query         = cols[2].text.strip().replace("%2F", "/")
                try:
                    ft = _dt.datetime.strptime(timestamp_str, "%d/%m/%Y %H:%M:%S")
                except Exception:
                    continue
                print(f"  🧾 {timestamp_str} | {status} | {file_name}")

                # Each export is identified by its OWN trigger time AND the
                # query text its own search produced — RSK383's query always
                # contains "Transaction From Date", RSK335's contains
                # "Client Code". Without both checks the two exports (queued
                # seconds apart) could be mistaken for each other.
                if (not target_383
                        and abs((ft - trigger_383).total_seconds()) <= 30
                        and "Transaction From Date" in query):
                    target_383 = file_name
                    print(f"  🔒 LOCKED RSK383: {target_383}")
                if target_383 and file_name == target_383 and "Generated" in status:
                    ready_383 = True

                if (not target_335
                        and abs((ft - trigger_335).total_seconds()) <= 30
                        and "Client Code" in query):
                    target_335 = file_name
                    print(f"  🔒 LOCKED RSK335: {target_335}")
                if target_335 and file_name == target_335 and "Generated" in status:
                    ready_335 = True

            if ready_383 and ready_335:
                print("\n🎉 BOTH FILES READY")
                break

            time.sleep(interval); elapsed += interval

        if not target_383:
            raise RuntimeError(f"RSK383 never appeared in the export list (after {elapsed}s)")
        if not target_335:
            raise RuntimeError(f"RSK335 never appeared in the export list (after {elapsed}s)")
        if not (ready_383 and ready_335):
            missing = []
            if not ready_383: missing.append("RSK383")
            if not ready_335: missing.append("RSK335")
            raise RuntimeError(f"{' + '.join(missing)} not generated within {max_wait}s")

        # ── 6. Download both ───────────────────────────────────────────────
        _stage("⬇️ Both files generated — downloading…")
        os.makedirs(output_dir, exist_ok=True)
        saved = {"rsk383": None, "rsk335": None}
        print("\n⬇️ DOWNLOADING BOTH FILES...")

        for key, label, remote_name in [("rsk383", "RSK383", target_383),
                                        ("rsk335", "RSK335", target_335)]:
            print(f"\n🔽 {label} → {remote_name}")
            check_url = (f"{BASE_URL}/Bancs/DownloadFileAction1.do"
                         f"?fileName={remote_name}&action=checkForFile")
            file_ready = False
            for i in range(10):
                print(f"  🔍 Checking download availability ({i + 1}/10)...")
                rc = sess.post(check_url, headers=DL_H, data={"rndaak": rndaak})
                if "FileExists" in rc.text:
                    file_ready = True; break
                time.sleep(5)
            if not file_ready:
                raise RuntimeError(f"{label} file not available for download after 10 checks")

            dl_url = (f"{BASE_URL}/Bancs/DownloadFileAction1.do"
                      f"?fileName={remote_name}&action=downloadFile")
            dr = sess.post(dl_url, headers=DL_H, data={"rndaak": rndaak}, stream=True)
            if dr.status_code != 200:
                raise RuntimeError(f"{label} download HTTP {dr.status_code}")

            # RSK335 is renamed to the fixed name probable_reconcile.py globs
            # for; RSK383 keeps the raw exported <random>-<YYYYMMDDHHMMSS>.xlsx
            # name, because that same module globs for "*<todayYYYYMMDD>*.xlsx"
            # to find it (see this module's docstring).
            out_name = "Clientwise_mcx_margin.xlsx" if key == "rsk335" else remote_name
            out_path = os.path.join(output_dir, out_name)
            if os.path.exists(out_path):
                os.remove(out_path)
            with open(out_path, "wb") as f:
                for chunk in dr.iter_content(8192):
                    if chunk:
                        f.write(chunk)
            saved[key] = out_path
            print(f"  ✅ {label} DOWNLOADED: {out_path}")

        print("\n🎉 ALL FILES DOWNLOADED SUCCESSFULLY")
        return saved

    except _AuthFailed:
        # [MCX_DEPOSIT_AUTH_FAIL] was already printed by _login(); the
        # dashboard turns that tag into its own "wrong password" popup.
        return {"rsk383": None, "rsk335": None}
    except Exception as exc:
        # MCXDepositBusyError is raised BEFORE this try block (by
        # _acquire_mcx_lock), so it propagates uncaught on purpose — the
        # caller shows a clean "already in use" message for it.
        print(f"❌ TCS BANCS download failed: {exc}")
        return {"rsk383": None, "rsk335": None}
    finally:
        # Explicit owner — locks are per-box now, so release PROBABLE's own.
        mdd._release_mcx_lock("probable")


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    result = download_both(out_dir, force="--force" in sys.argv)
    ok = bool(result.get("rsk383")) and bool(result.get("rsk335"))
    print(f"\n{'✅' if ok else '❌'} RSK383: {result.get('rsk383')}")
    print(f"{'✅' if ok else '❌'} RSK335: {result.get('rsk335')}")
    sys.exit(0 if ok else 1)
