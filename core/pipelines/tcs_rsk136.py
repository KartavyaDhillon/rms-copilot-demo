"""
core/pipelines/tcs_rsk136.py - TCS BANCS RSK136 ("Query Security Collateral Valuation") = the "Search Results" file of MCX COMM LIMIT.

Two ways it is used:
  1. As a THIRD export queued by core/pipelines/probable_tcs_bancs.py in the same login as RSK383 + RSK335
     (queue() / is_target() / save_name() below) - so PROBABLE's TCS button brings it for free.
  2. On its own, when MCX COMM LIMIT finds the file missing at its time:
         python core/pipelines/tcs_rsk136.py --out <folder>
     (one login -> export -> wait -> download -> saved as SearchResults_<ddmmyyyy>.xlsx).

Payloads are the ones of the hand-run Search_result_api.py (MCX COMM LIMIT folder), unchanged.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
import time

BASE_URL = "https://eclear.mcxccl.com"
RSK136_URL = BASE_URL + "/Bancs/RSK/RSK136.do"
TAB = "panel5"                     # RSK383 = panel2, RSK335 = panel3, "Download Exported Files" = panel4
MATCH_SECONDS = 50                 # an export belongs to us when it was queued within this many seconds of our click
SEARCH_PREFIX = "SearchResults"


def save_name(today: _dt.date | None = None) -> str:
    return f"{SEARCH_PREFIX}_{(today or _dt.date.today()):%d%m%Y}.xlsx"


def _init_payload(rndaak: str) -> dict:
    return {
        "app": "bancsapp?prefix=/RSK", "page": "/RSK136.do", "confViewSize": "50", "reqType": "0", "mode": "DEF_MODE",
        "parentName": "blank", "browserType": "IE55", "GuiWinName": "RSK136", "compname": "RSK",
        "over_ride_button": "Cancel", "AvlHt": "744", "AvlWd": "1481", "tblWd": "1465", "GuiBrowserInst": "0",
        "theme": "black", "pageId": "RSK136", "wintitle": "Query Security Collateral Valuation",
        "availHeight": "816", "availWidth": "1536", "tabId": TAB, "rndaak": rndaak,
    }


def _search_payload(rndaak: str, today: _dt.date | None = None) -> dict:
    d = today or _dt.date.today()
    api, enc = d.strftime("%Y%m%d"), d.strftime("%d/%m/%Y").replace("/", "%2F")
    p = {
        "propertyMap(MCB_SearchWC_wca_exchange_Cmb)": "DFLT", "MCB_SearchWC_wca_exchange": "",
        "propertyMap(MCB_SearchWC_wca_exchange_op)": "=",
        "MCB_SearchWC_wca_market": "EXCH", "propertyMap(MCB_SearchWC_wca_market_op)": "=",
        "MCB_SearchWC_wca_segmentNo": "CMDE", "propertyMap(MCB_SearchWC_wca_segmentNo_op)": "=",
        "propertyMap(MCB_SearchWC_wca_BPID_Cmb)": "DFLT", "MCB_SearchWC_wca_BPID": "",
        "propertyMap(MCB_SearchWC_wca_BPID_op)": "=",
        "MCB_SearchWC_wca_AccountNo": "", "propertyMap(MCB_SearchWC_wca_AccountNo_op)": "=",
        "propertyMap(MCB_SearchWC_wca_instrument_Cmb)": "ISIN", "MCB_SearchWC_wca_instrument": "",
        "propertyMap(MCB_SearchWC_wca_instrument_op)": "=",
        "MCB_SearchWC_wca_collateralGroupId": "", "propertyMap(MCB_SearchWC_wca_collateralGroupId_op)": "=",
        "MCB_SearchWC_wca_blockingType": "", "propertyMap(MCB_SearchWC_wca_blockingType_op)": "=",
        "MCB_SearchWC_wca_cmcode": "", "propertyMap(MCB_SearchWC_wca_cmcode_op)": "=",
        "MCB_SearchWC_wca_cpcode": "", "propertyMap(MCB_SearchWC_wca_cpcode_op)": "=",
        "propertyMap(MCB_SearchWC_wca_tmcode_Cmb)": "DFLT", "MCB_SearchWC_wca_tmcode": "",
        "propertyMap(MCB_SearchWC_wca_tmcode_op)": "=",
        "MCB_SearchWC_wca_ucccode": "", "propertyMap(MCB_SearchWC_wca_ucccode_op)": "=",
        "MCB_SearchWC_wca_depositDate": api, "propertyMap(MCB_SearchWC_wca_depositDate_op)": "=",
        "MCB_SearchWC_wca_MaturityDate": api, "propertyMap(MCB_SearchWC_wca_MaturityDate_op)": "=",
        "MCB_SearchWC_wca_collateralPurpose": "", "propertyMap(MCB_SearchWC_wca_collateralPurpose_op)": "=",
        "MCB_SearchWC_wca_dummy11": "", "propertyMap(MCB_SearchWC_wca_dummy11_op)": "=",
        "MCB_SearchWC_wca_segregationType": "", "propertyMap(MCB_SearchWC_wca_segregationType_op)": "=",
        "MCB_SearchWC_wca_underlying": "", "propertyMap(MCB_SearchWC_wca_underlying_op)": "=",
        "MCB_SearchWC_wca_datedum": "", "propertyMap(MCB_SearchWC_wca_datedum_op)": "=",
        "MCB_SearchWC_wca_expiryDate": "", "propertyMap(MCB_SearchWC_wca_expiryDate_op)": "=",
        "GRID_RESPONSE": "RSK136_Table", "operationType": "ET", "searchClicked": "true", "PageNum": "0",
        "service": "querySecValuation", "windowName": "RSK136", "MODE": "DEF_MODE", "compName": "RSK",
        "lgbinst": "0", "WRITE_APP": "true", "REQ_TYPE": "0", "reqType": "1",
        "sQuery": (f"Exchange  Equals  DFLT AND Market  Equals  EXCH AND Segment  Equals  CMDE AND CM ID  Equals  DFLT "
                   f"AND Instrument  Equals  ISIN AND TM Code  Equals  DFLT AND From Date  Equals  {enc} AND To Date  Equals  {enc}"),
        "AvlWd": "1280", "tblWd": "1280",
        "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
        "IXHRts": str(int(time.time() * 1000)), "XHR": "true",
        "rndaak": rndaak, "theme": "black", "tabId": TAB, "mode": "DEF_MODE", "GuiBrowserInst": "0",
        "parentName": "blank", "browserType": "IE55", "GuiWinName": "RSK136", "compname": "RSK", "confViewSize": "50",
    }
    return p


def queue(sess, rndaak: str, init_h: dict) -> _dt.datetime:
    """Open RSK136, search today's collateral valuation and CLICK Export (no waiting). Returns the click time.
    Raises RuntimeError with a plain message."""
    sess.post(RSK136_URL, headers=init_h, data=_init_payload(rndaak))
    fetch_h = {"User-Agent": "Mozilla/5.0", "Accept": "*/*",
               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
               "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL, "Referer": RSK136_URL}
    search = _search_payload(rndaak)
    r = sess.post(RSK136_URL, headers=fetch_h, data=search)
    if "sessionExpired" in r.text or "YOUR SESSION HAS EXPIRED" in r.text:
        raise RuntimeError("RSK136: session expired while searching")
    if "RSK136_Table" not in r.text:
        raise RuntimeError("RSK136 search failed - RSK136_Table not in the reply")
    export = {**search, "Export": "Excel", "enableBatchForExport": "yes", "noPgsToExport": "100", "PageNum": "1",
              "undefined": "",
              "MCBrowserEventInformation": "sourceElementName~|~undefined#*#eventType~|~callListExport#*#butId~|~null",
              "IXHRts": str(int(time.time() * 1000))}
    t = _dt.datetime.now()
    e = sess.post(RSK136_URL, headers=fetch_h, data=export)
    if "ExportBatchSuccess" not in e.text:
        raise RuntimeError("RSK136 export was not accepted")
    return t


def is_target(cols, trigger: _dt.datetime, file_time: _dt.datetime) -> bool:
    """True when this row of the export list is OUR RSK136 export (title says SECURITY VALUATION, queued at our click)."""
    try:
        title = cols[0].text.upper()
    except Exception:
        return False
    return "SECURITY VALUATION" in title and abs((file_time - trigger).total_seconds()) <= MATCH_SECONDS


def fetch_to(sess, rndaak: str, dl_h: dict, remote_name: str, dest: str, retries: int = 10) -> str:
    """checkForFile -> downloadFile -> <dest>.part -> rename. Returns dest. Raises RuntimeError."""
    chk = f"{BASE_URL}/Bancs/DownloadFileAction1.do?fileName={remote_name}&action=checkForFile"
    for _ in range(retries):
        if "FileExists" in sess.post(chk, headers=dl_h, data={"rndaak": rndaak}).text:
            break
        time.sleep(5)
    else:
        raise RuntimeError("RSK136 file is not available for download")
    url = f"{BASE_URL}/Bancs/DownloadFileAction1.do?fileName={remote_name}&action=downloadFile"
    r = sess.post(url, headers=dl_h, data={"rndaak": rndaak}, stream=True)
    if r.status_code != 200:
        raise RuntimeError(f"RSK136 download HTTP {r.status_code}")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    with open(part, "wb") as f:
        for chunk in r.iter_content(8192):
            if chunk:
                f.write(chunk)
    os.replace(part, dest)
    return dest


# ───────────────────────── stand-alone: RSK136 only ─────────────────────────
def download(out_dir: str, max_wait: int = 900, interval: int = 15) -> str | None:
    """One login (the RSA code comes from MCX_TOKEN_MODE=auto when set), export RSK136, wait, download, rename."""
    import requests
    from bs4 import BeautifulSoup
    from core.pipelines import probable_tcs_bancs as P
    mdd = P._mdd()
    os.environ.setdefault("MCX_DEPOSIT_CALLER", "mcxcomm")
    user = os.getenv("SA_MCX_DEPOSIT_USER", "56720CMMKR014139").strip()
    pwd = os.getenv("SA_MCX_DEPOSIT_PASSWORD", "").strip()
    if not pwd:
        print("[MCX_DEPOSIT_AUTH_FAIL] SA_MCX_DEPOSIT_PASSWORD is not set - cannot download the Search Results file")
        return None
    hdrs = P._headers()
    _ajax, _login, _nav, _ajax2, init_h, dl_h = hdrs
    mdd._acquire_mcx_lock("mcxcomm")
    try:
        print("🔐 Logging in to TCS BANCS (RSK136 only)...", flush=True)
        sess = requests.Session()
        rndaak = P._login(sess, user, pwd, mdd, hdrs)
        trigger = queue(sess, rndaak, init_h)
        print(f"✅ RSK136 export QUEUED at {trigger:%H:%M:%S}", flush=True)
        tab_id = P._arch_init(sess, init_h, rndaak)
        target, elapsed, relogins = None, 0, 0
        while elapsed < max_wait:
            try:
                res = sess.post(P.ARCH_URL, headers=dl_h, data=P._export_queue_payload(rndaak, tab_id, trigger))
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                print(f"⚠️ network hiccup ({type(e).__name__}) - trying again in 10s", flush=True)
                time.sleep(10)
                elapsed += 10
                continue
            if mdd._is_expired(res.text):
                relogins += 1
                if relogins > 3:
                    raise RuntimeError("TCS BANCS session expired 3x - someone else is logged in to this account")
                print("🚨 session expired - logging in again and resuming the same export", flush=True)
                sess = requests.Session()
                rndaak = P._login(sess, user, pwd, mdd, hdrs, session_expired=True)
                tab_id = P._arch_init(sess, init_h, rndaak)
                continue
            rows = BeautifulSoup(res.text, "html.parser").select("table tbody tr")
            p2 = sess.post(P.ARCH_URL, headers=dl_h, data=P._export_queue_page2_payload(rndaak, tab_id, trigger))
            if not mdd._is_expired(p2.text):
                rows += BeautifulSoup(p2.text, "html.parser").select("table tbody tr")
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 7:
                    continue
                try:
                    ft = _dt.datetime.strptime(cols[5].text.strip(), "%d/%m/%Y %H:%M:%S")
                except Exception:
                    continue
                name, status = cols[6].text.strip(), cols[4].text.strip()
                if not target and is_target(cols, trigger, ft):
                    target = name
                    print(f"🔒 LOCKED RSK136: {target}", flush=True)
                if target and name == target and "Generated" in status:
                    dest = fetch_to(sess, rndaak, dl_h, target, os.path.join(out_dir, save_name()))
                    print(f"✅ RSK136 DOWNLOADED: {dest}", flush=True)
                    return dest
            print(f"⏳ RSK136 not generated yet ({elapsed}s)", flush=True)
            time.sleep(interval)
            elapsed += interval
        raise RuntimeError(f"RSK136 was not generated within {max_wait}s")
    finally:
        mdd._release_mcx_lock("mcxcomm")


if __name__ == "__main__":
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo not in sys.path:
        sys.path.insert(0, repo)
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(repo, ".env"))
    except Exception:
        pass
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.getcwd()
    try:
        path = download(out)
    except Exception as exc:                       # noqa: BLE001
        print(f"❌ RSK136 download failed: {exc}", flush=True)
        path = None
    sys.exit(0 if path else 1)
