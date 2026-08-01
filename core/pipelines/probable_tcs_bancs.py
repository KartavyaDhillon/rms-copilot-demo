"""
core/pipelines/probable_tcs_bancs.py — TCS BANCS downloads for the Probable
Defaulter pipeline: RSK383 (Cash Collateral Deposit Limit) + RSK335 (Margin
Utilization View / "Clientwise MCX Margin").

Ported from the reference script codes/API HITTING/tcs_bancs_probable.py in
the user-supplied "probable for claude" / "PROBABLE FINAL NEW" folders (READ
ONLY sources — nothing there is ever modified; this file is the RMS Copilot
port). The reference script logs in ONCE and fetches both reports in the same
session; here the two are fetched with two separate calls instead, so RSK383
can reuse the EXISTING, already-proven sa_codes/mcx_deposit_downloader.py
function untouched (same account, same dashboard RSA-token popup, same
cross-process TCS BANCS session lock already shared by Short Allocation's
"MCX Deposit" button and Client Prudential Limit's "MCX Web Allocation"
button — see core/pipelines/cli_mcx_download.py for the identical reuse
pattern). RSK335 has no existing downloader anywhere in this repo, so it is
implemented here, reusing the SAME crypto/entity/token-popup/lock helpers
from mcx_deposit_downloader.py rather than duplicating them.

Credentials: SA_MCX_DEPOSIT_USER / SA_MCX_DEPOSIT_PASSWORD — confirmed (via
the reference script's own hardcoded USER_ID="56720CMMKR014139" / PASSWORD=
"Mcx@2907") to be the EXACT SAME eclear.mcxccl.com account already used for
RSK383 elsewhere in this repo. No new credential pair is introduced.

Output (into PROBABLE_FOLDER):
  RSK383 → whatever filename TCS BANCS itself generates (download_mcx_deposit
           saves it as MCX_DEPOSIT_HH.MM.xlsx)
  RSK335 → Clientwise_mcx_margin.xlsx  (matches the reference script's own
           save_path naming exactly, so probable_reconcile.py's file lookups
           can use the identical filename the original code1.py expects)
"""
import os
import re
import sys
import time
import datetime as _dt

import requests
from bs4 import BeautifulSoup

_AUTH_BAD = re.compile(r"\[MCX_DEPOSIT_(AUTH_FAIL|LOCKED)\]", re.I)


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


# ── RSK383 — reuse the existing, already-hardened downloader untouched ──────

def download_rsk383(output_dir):
    """Same report / same account as the SA "MCX Deposit" button and CLI's
    "MCX Web Allocation" button. Returns the saved xlsx path, or None on
    failure. Raises MCXDepositBusyError (from mdd) if another box is using
    the shared TCS BANCS session lock right now — caller should catch this
    specifically, exactly like cli_mcx_download.py does."""
    mdd = _mdd()
    os.environ["MCX_DEPOSIT_CALLER"] = "probable"
    return mdd.download_mcx_deposit(output_dir=output_dir)


# ── RSK335 — new: Margin Utilization View / "Clientwise MCX Margin" ─────────

def download_rsk335(output_dir):
    """Downloads RSK335 ("Margin Utilization View") and saves it as
    Clientwise_mcx_margin.xlsx in output_dir — the exact filename the ported
    reconciliation logic (probable_reconcile.py, following code1.py's
    read_mcx_file()) expects. Returns the saved path, or None on failure.

    Uses its OWN full login (separate from download_rsk383's), but shares
    the SAME cross-process lock file and the SAME dashboard RSA-token popup
    mechanism (_acquire_mcx_lock / _token_popup, imported from
    mcx_deposit_downloader.py) so it can never race against RSK383, SA's MCX
    Deposit, or CLI's MCX Web Allocation — all four ultimately serialize
    against the one physical TCS BANCS account/session.
    """
    mdd = _mdd()
    USER_ID  = os.getenv("SA_MCX_DEPOSIT_USER",    "56720CMMKR014139").strip()
    PASSWORD = os.getenv("SA_MCX_DEPOSIT_PASSWORD", "").strip()
    if not PASSWORD:
        print("❌ SA_MCX_DEPOSIT_PASSWORD not set in .env — cannot download RSK335")
        return None

    BASE_URL = "https://eclear.mcxccl.com"
    LOGIN_URL       = BASE_URL + "/Bancs/login.do?method=authenticatePassward"
    TOKEN_URL       = BASE_URL + "/Bancs/login.do?method=authenticateToken"
    FINAL_LOGIN_URL = BASE_URL + "/Bancs/login.do?method=Login"
    RSK335_URL      = BASE_URL + "/Bancs/RSK/RSK335.do"
    ARCH_URL        = BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do"

    AJAX_H = {"User-Agent": "Mozilla/5.0", "Accept": "*/*",
              "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
              "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL,
              "Referer": BASE_URL + "/Bancs/common/dologin.jsp?method=authMode&JSLoadingMode=null"}
    LOGIN_H = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml",
               "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
               "Referer": BASE_URL + "/Bancs/common/dologin.jsp"}
    NAV_H = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded",
             "Origin": BASE_URL, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "Referer": BASE_URL + "/Bancs/login.do?method=Login&availHeight=744&availWidth=1522"}
    AJAX2_H = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded",
               "Origin": BASE_URL, "X-Requested-With": "XMLHttpRequest", "Accept": "*/*",
               "Referer": BASE_URL + "/Bancs/common/welcome.jsp"}
    FETCH_H = {"User-Agent": "Mozilla/5.0", "Accept": "*/*",
               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
               "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL, "Referer": RSK335_URL}

    _mcx_caller = "probable"
    mdd._acquire_mcx_lock(_mcx_caller)
    try:
        sess = requests.Session()
        print("\n🌐 Opening session (RSK335)...")
        sess.get(BASE_URL)
        sess.post(BASE_URL + "/Bancs/login.do?method=authMode", headers=AJAX_H)

        print("🔐 PASSWORD STEP")
        pw_enc, ign = mdd._encrypt_value(PASSWORD)
        pw_resp = sess.post(LOGIN_URL, headers=AJAX_H,
                             data={"userId": USER_ID + "\t", "password": pw_enc,
                                   "lng": "1", "ignoreFlag": ign})
        if "INVALID_LOGIN_OR_PASSWORD" in pw_resp.text or "error_div1" in pw_resp.text:
            print("[MCX_DEPOSIT_AUTH_FAIL] wrong password — update SA_MCX_DEPOSIT_PASSWORD in Credentials")
            return None

        print("🔴 TOKEN STEP")
        entity = entity_codes = None
        err_msg = None
        for attempt in range(1, 4):
            token = mdd._token_popup(attempt, 3, err_msg)
            t_enc, t_ign = mdd._encrypt_value(token)
            resp = sess.post(TOKEN_URL, headers=AJAX_H,
                              data={"userId": USER_ID + "\t", "password": t_enc,
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
        fp_enc, f_ign = mdd._encrypt_value(PASSWORD)
        resp3 = sess.post(FINAL_LOGIN_URL, headers=LOGIN_H,
                           data={"method": "Login", "availHeight": "744", "availWidth": "1522",
                                 "isArchival": "false", "userEntityCodes": entity_codes,
                                 "lng": "1", "userId": USER_ID, "ignoreFlag": f_ign,
                                 "password": fp_enc, "dummy": "FltM/sA2fPfqsjtM55oLOQ==",
                                 "entity": entity})
        rndaak = None
        for pat in [r"name=['\"]rndaak['\"][^>]*value=['\"]([^'\"]+)",
                    r"rndaak['\"]?\s*:\s*['\"]([^'\"]+)"]:
            m = re.search(pat, resp3.text)
            if m:
                rndaak = m.group(1); break
        if not rndaak:
            raise RuntimeError("rndaak not found in final-login response")
        print("✅ rndaak:", rndaak)

        sess.post(BASE_URL + "/Bancs/common/CommonImpl.jsp", headers=NAV_H, data={"rndaak": rndaak})
        sess.post(BASE_URL + "/Bancs/common/welcome.jsp",   headers=NAV_H, data={"rndaak": rndaak})
        sess.post(BASE_URL + "/Bancs/common/layout.save",   headers=AJAX2_H, data={"events": "", "rndaak": rndaak})
        sess.post(BASE_URL + "/Bancs/common/welcome.jsp",   headers=NAV_H, data={"rndaak": rndaak})

        print("\n🌐 Opening RSK335...")
        sess.post(RSK335_URL, headers={"User-Agent": "Mozilla/5.0",
                  "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
                  "Referer": BASE_URL + "/Bancs/common/PageLoader.html"},
                  data={"app": "bancsapp?prefix=/RSK", "page": "/RSK335.do", "confViewSize": "50",
                        "reqType": "0", "mode": "DEF_MODE", "parentName": "blank", "browserType": "IE55",
                        "GuiWinName": "RSK335", "compname": "RSK", "over_ride_button": "Cancel",
                        "AvlHt": "744", "AvlWd": "1481", "tblWd": "1465", "GuiBrowserInst": "0",
                        "theme": "black", "pageId": "RSK335", "wintitle": "Margin Utilization View",
                        "availHeight": "816", "availWidth": "1536", "tabId": "panel3", "rndaak": rndaak})

        sess.post(RSK335_URL, headers=FETCH_H,
                  data={"xmlReq": "YES", "reqType": "19", "GuiWinName": "RSK335", "GuiBrowserInst": "0",
                        "inParam1": "56720", "inParam2": "DFLT", "tabId": "panel3",
                        "functionName": "funcname", "ifCallPaint": "false",
                        "ctrlId": "MCB_SearchWC_wca_associatedcm", "filterCombo": "NO",
                        "filterS": "sample", "rndaak": rndaak})

        search_payload = {
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
            "GRID_RESPONSE": "RSK335_Table", "operationType": "ET", "searchClicked": "true", "PageNum": "0",
            "service": "qryMarginUtilView", "windowName": "RSK335", "MODE": "DEF_MODE", "compName": "RSK",
            "lgbinst": "0", "WRITE_APP": "true", "REQ_TYPE": "0", "reqType": "1",
            "sQuery": "Client Code  Equals  DFLT AND TM / CP  Equals  DFLT AND CM  Equals  DFLT AND CM  Equals  56720",
            "AvlWd": "1048", "tblWd": "1026",
            "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
            "IXHRts": str(int(time.time() * 1000)), "XHR": "true", "rndaak": rndaak, "theme": "black",
            "tabId": "panel3", "mode": "DEF_MODE", "GuiBrowserInst": "0", "parentName": "blank",
            "browserType": "IE55", "GuiWinName": "RSK335", "compname": "RSK", "confViewSize": "50",
        }
        print("🔍 Running RSK335 search...")
        sr = sess.post(RSK335_URL, headers=FETCH_H, data=search_payload)
        if "RSK335_Table" not in sr.text:
            raise RuntimeError("RSK335 search failed — RSK335_Table not in response")
        print("✅ Search successful")

        export_trigger_time = _dt.datetime.now()
        export_payload = {**search_payload, "Export": "Excel", "enableBatchForExport": "yes",
                           "noPgsToExport": "50", "PageNum": "1", "undefined": "",
                           "MCBrowserEventInformation":
                               "sourceElementName~|~undefined#*#eventType~|~callListExport#*#butId~|~null",
                           "IXHRts": str(int(time.time() * 1000))}
        er = sess.post(RSK335_URL, headers=FETCH_H, data=export_payload)
        if "ExportBatchSuccess" not in er.text:
            raise RuntimeError("RSK335 export trigger failed")
        print("✅ Export triggered")

        # ── init ARCH download page ──────────────────────────────────────────
        resp_arch = sess.post(ARCH_URL, headers={"User-Agent": "Mozilla/5.0",
                    "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
                    "Referer": BASE_URL + "/Bancs/common/PageLoader.html"},
                    data={"app": "bancsapp?prefix=/ARCHGUI", "page": "/ar_w_DataExport.do",
                          "confViewSize": "50", "reqType": "0", "mode": "DEF_MODE", "parentName": "blank",
                          "browserType": "IE55", "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI",
                          "over_ride_button": "Cancel", "AvlHt": "678", "AvlWd": "914", "tblWd": "898",
                          "GuiBrowserInst": "0", "theme": "black", "pageId": "ar_w_DataExport",
                          "wintitle": "Download Exported Files", "availHeight": "816", "availWidth": "1536",
                          "tabId": "panel4", "rndaak": rndaak, "reqtoken": "", "parentToChildData": ""})
        tab_m = re.search(r'id="tabId"[^>]*value="([^"]+)"', resp_arch.text)
        if not tab_m:
            raise RuntimeError("tabId not found in ARCH response")
        tab_id = tab_m.group(1)
        print(f"✅ TAB ID: {tab_id}")

        DL_H = {"User-Agent": "Mozilla/5.0", "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL,
                "Referer": BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do?app=bancsapp?prefix=/ARCHGUI"}

        target_file = None
        ready = False
        max_wait, interval, elapsed = 900, 15, 0
        while elapsed < max_wait:
            print(f"\n🔄 Checking RSK335 export queue... (elapsed: {elapsed}s)")
            poll = {
                "MCB_SearchWC_wca_EXPORT_WINDOW_NAME": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_WINDOW_NAME_op)": "=",
                "MCB_SearchWC_wca_EXPORT_TABLE_NAME": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_TABLE_NAME_op)": "=",
                "MCB_SearchWC_wca_EXPORT_DATE_FROM": "",
                "propertyMap(MCB_SearchWC_wca_EXPORT_DATE_FROM_From)": export_trigger_time.strftime("%d/%m/%Y"),
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
                "MODE": "DEF_MODE", "compName": "ARCHGUI", "lgbinst": "0", "WRITE_APP": "true",
                "REQ_TYPE": "0", "reqType": "1",
                "sQuery": "  Equals  " + export_trigger_time.strftime("%Y%m%d"),
                "AvlWd": "1280", "tblWd": "1280",
                "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
                "IXHRts": str(int(time.time() * 1000)), "XHR": "true",
                "rndaak": rndaak, "theme": "black", "tabId": tab_id, "mode": "DEF_MODE",
                "GuiBrowserInst": "0", "parentName": "blank", "confViewSize": "50", "browserType": "IE55",
                "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI", "over_override_button": "Cancel",
            }
            res_poll = sess.post(ARCH_URL, headers=DL_H, data=poll)
            if mdd._is_expired(res_poll.text):
                raise RuntimeError("RSK335 session expired mid-poll — re-run this download")
            soup = BeautifulSoup(res_poll.text, "html.parser")
            for row in soup.select("table tbody tr"):
                cols = row.find_all("td")
                if len(cols) < 7:
                    continue
                status, timestamp_str, file_name = cols[4].text.strip(), cols[5].text.strip(), cols[6].text.strip()
                query = cols[2].text.strip().replace("%2F", "/") if len(cols) > 2 else ""
                print(f"  🧾 {timestamp_str} | {status} | {file_name}")
                try:
                    ft = _dt.datetime.strptime(timestamp_str, "%d/%m/%Y %H:%M:%S")
                    if (not target_file and abs((ft - export_trigger_time).total_seconds()) <= 30
                            and "Client Code" in query):
                        target_file = file_name
                        print(f"  🎯 LOCKED: {target_file}")
                    if file_name == target_file and "Generated" in status:
                        ready = True
                except Exception:
                    continue
            if ready:
                print("🎉 FILE READY"); break
            time.sleep(interval); elapsed += interval

        if not target_file:
            raise RuntimeError(f"RSK335 file never appeared in export list (after {elapsed}s)")
        if not ready:
            raise RuntimeError(f"RSK335 not generated within {max_wait}s (waited {elapsed}s)")

        check_url = f"{BASE_URL}/Bancs/DownloadFileAction1.do?fileName={target_file}&action=checkForFile"
        file_ready = False
        for i in range(10):
            print(f"🔍 Checking download availability ({i+1}/10)...")
            rc = sess.post(check_url, headers=DL_H, data={"rndaak": rndaak})
            if "FileExists" in rc.text:
                file_ready = True; break
            time.sleep(5)
        if not file_ready:
            raise RuntimeError("RSK335 file not available for download after 10 checks")

        dl_url = f"{BASE_URL}/Bancs/DownloadFileAction1.do?fileName={target_file}&action=downloadFile"
        print("⬇️ Downloading RSK335...")
        dr = sess.post(dl_url, headers=DL_H, data={"rndaak": rndaak}, stream=True)
        if dr.status_code != 200:
            raise RuntimeError(f"RSK335 download HTTP {dr.status_code}")

        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, "Clientwise_mcx_margin.xlsx")
        if os.path.exists(out_path):
            os.remove(out_path)
        with open(out_path, "wb") as f:
            for chunk in dr.iter_content(8192):
                if chunk:
                    f.write(chunk)
        print(f"🎉 RSK335 DOWNLOADED: {out_path}")
        return out_path

    except Exception as exc:
        print(f"❌ RSK335 download failed: {exc}")
        return None
    finally:
        mdd._release_mcx_lock()


def download_both(output_dir):
    """Convenience entry point used by probable_download.py's button: fetch
    RSK383 first (existing downloader), then RSK335 (new). Each call claims
    and releases the shared lock independently — a MCXDepositBusyError from
    the FIRST call propagates uncaught (same convention cli_mcx_download.py
    uses) so the caller can show a clean "already in use" message instead of
    a generic failure."""
    r383 = download_rsk383(output_dir)
    r335 = download_rsk335(output_dir)
    return {"rsk383": r383, "rsk335": r335}


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    result = download_both(out_dir)
    ok = bool(result.get("rsk383")) and bool(result.get("rsk335"))
    print(f"\n{'✅' if ok else '❌'} RSK383: {result.get('rsk383')}")
    print(f"{'✅' if ok else '❌'} RSK335: {result.get('rsk335')}")
    sys.exit(0 if ok else 1)
