"""sa_codes/mcx_tcs_pair_downloader.py - MCX eclear (TCS BANCS): the two files MCX CASH MAINTENANCE needs, in ONE login.

    RSK383  Cash Collateral Deposit Limit   -> the "MCX deposit" file   (UCC Code, Allocation at EOD, ...)
    RSK335  Margin Utilization View         -> the "MCX 90%" file        (Client Code, Cli / TM Mrgn 90% (Rs), ...)

Order (as the operations team asked, same as the hand-run FINAL.py): log in once -> trigger the export of BOTH files ->
only then wait for them -> download both.  NOT "export one, wait, download, export the other".

Unattended rules (core/mcx_soft_token.py, MCX_TOKEN_MODE=auto): the RSA code always comes from the seed and is always the
NEXT code, at most 2 wrong codes, and when the session dies in the middle the next login waits MCX_RELOGIN_WAIT_SEC
(4 minutes) first.  A "token is filled" (code refused / session kicked) therefore retries after 4 minutes, never at once.

Credentials: SA_MCX_DEPOSIT_USER / SA_MCX_DEPOSIT_PASSWORD (the same eclear account as the MCX deposit download).
Prints tags the parent understands: [MCX_DEPOSIT_AUTH_FAIL], [MCX_TOKEN_FAIL], [PAIR_RESULT] <label>=<path>.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import time
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("MCX_TOKEN_MODE", "auto")

try:
    from dotenv import load_dotenv as _ld
    _ld(os.getenv("SA_ENV_FILE", os.path.join(REPO, ".env")))
except Exception:
    pass

import requests
from bs4 import BeautifulSoup


def _load_base():
    spec = importlib.util.spec_from_file_location("mcx_deposit_downloader", os.path.join(REPO, "sa_codes", "mcx_deposit_downloader.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _load_base()
BASE_URL = B.BASE_URL
CM_CODE = os.getenv("MCX_CASH_CM_CODE", "56720")

AJAX_H = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
          "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL,
          "Referer": BASE_URL + "/Bancs/common/dologin.jsp?method=authMode&JSLoadingMode=null"}
LOGIN_H = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml",
           "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL, "Referer": BASE_URL + "/Bancs/common/dologin.jsp"}
NAV_H = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
         "Referer": BASE_URL + "/Bancs/login.do?method=Login&availHeight=744&availWidth=1522",
         "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe", "Upgrade-Insecure-Requests": "1"}
AJAX2_H = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
           "X-Requested-With": "XMLHttpRequest", "Accept": "*/*", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
           "Referer": BASE_URL + "/Bancs/common/welcome.jsp"}
INIT_H = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
          "Referer": BASE_URL + "/Bancs/common/PageLoader.html"}
EXPORT_URL = BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do"
DL_H = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL,
        "Referer": BASE_URL + "/Bancs/ARCHGUI/ar_w_DataExport.do?app=bancsapp?prefix=/ARCHGUI"}


class PairError(Exception):
    pass


class _Session:
    """One logged-in eclear session (re-login keeps the same object, the cookies and rndaak are replaced)."""

    def __init__(self, user, password, log=print, cancel=lambda: False):
        self.user, self.password, self.log, self.cancel = user, password, log, cancel
        self.s = requests.Session()
        self.rndaak = ""
        self.tab_id = ""

    # ---- login --------------------------------------------------------------------------------
    def login(self):
        s, log = self.s, self.log
        log("Opening the MCX eclear session...")
        s.get(BASE_URL, timeout=60)
        s.post(BASE_URL + "/Bancs/login.do?method=authMode", headers=AJAX_H, timeout=60)
        pw_enc, ign = B._encrypt_value(self.password)
        r = s.post(B.LOGIN_URL, headers=AJAX_H, timeout=60, data={"userId": self.user + "\t", "password": pw_enc, "lng": "1", "ignoreFlag": ign})
        if "INVALID_LOGIN_OR_PASSWORD" in r.text or "error_div1" in r.text:
            print("[MCX_DEPOSIT_AUTH_FAIL] wrong password - update SA_MCX_DEPOSIT_PASSWORD in Credentials")
            raise PairError("MCX login failed - wrong password")
        entity = codes = None
        max_try = 2 if B._auto_mode() else 3
        for attempt in range(1, max_try + 1):
            token = B._token_popup(attempt, max_try, None)
            t_enc, t_ign = B._encrypt_value(token)
            r = s.post(B.TOKEN_URL, headers=AJAX_H, timeout=60, data={"userId": self.user + "\t", "password": t_enc, "lng": "1", "ignoreFlag": t_ign})
            try:
                entity, codes = B._extract_entity(r.text)
                log(f"Token accepted (entity {entity}).")
                break
            except Exception:
                log(f"Token was not accepted (try {attempt}/{max_try}).")
                if attempt == max_try:
                    print("[MCX_TOKEN_FAIL] the RSA code was refused 2 times - stopped (no more tries, to avoid locking the account)")
                    raise PairError("MCX RSA code refused after the allowed tries")
        fp, fi = B._encrypt_value(self.password)
        r3 = s.post(B.FINAL_LOGIN_URL, headers=LOGIN_H, timeout=60, data={
            "method": "Login", "availHeight": "744", "availWidth": "1522", "isArchival": "false", "userEntityCodes": codes,
            "lng": "1", "userId": self.user, "ignoreFlag": fi, "password": fp, "dummy": "FltM/sA2fPfqsjtM55oLOQ==", "entity": entity})
        m = None
        for pat in (r"name=['\"]rndaak['\"][^>]*value=['\"]([^'\"]+)", r"rndaak['\"]?\s*:\s*['\"]([^'\"]+)"):
            m = re.search(pat, r3.text)
            if m:
                break
        if not m:
            raise PairError("rndaak not found after the final login")
        self.rndaak = m.group(1)
        for url, hd, extra in ((BASE_URL + "/Bancs/common/CommonImpl.jsp", NAV_H, {}), (BASE_URL + "/Bancs/common/welcome.jsp", NAV_H, {}),
                               (BASE_URL + "/Bancs/common/layout.save", AJAX2_H, {"events": ""}), (BASE_URL + "/Bancs/common/welcome.jsp", NAV_H, {})):
            s.post(url, headers=hd, data={"rndaak": self.rndaak, **extra}, timeout=60)
        log("Logged in to MCX eclear.")

    def relogin(self):
        from core import mcx_soft_token as mst
        self.log("MCX session expired / kicked - re-login.")
        if not mst.relogin_wait(cancelled=self.cancel, log=self.log):
            raise PairError("Stopped while waiting to log in again")
        self.s = requests.Session()
        self.login()
        self.init_arch()

    # ---- pages --------------------------------------------------------------------------------
    def init_arch(self):
        self.s.get(BASE_URL + "/Bancs/common/PageLoader.html", headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html",
                   "Referer": BASE_URL + "/Bancs/login.do?method=Login"}, timeout=60)
        r = self.s.post(EXPORT_URL, headers=INIT_H, timeout=60, data={
            "app": "bancsapp?prefix=/ARCHGUI", "page": "/ar_w_DataExport.do", "confViewSize": "50", "reqType": "0", "mode": "DEF_MODE",
            "parentName": "blank", "browserType": "IE55", "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI",
            "over_ride_button": "Cancel", "AvlHt": "678", "AvlWd": "914", "tblWd": "898", "GuiBrowserInst": "0", "theme": "black",
            "pageId": "ar_w_DataExport", "wintitle": "Download Exported Files", "availHeight": "816", "availWidth": "1536",
            "tabId": "panel4", "rndaak": self.rndaak, "reqtoken": "", "parentToChildData": ""})
        m = re.search(r'id="tabId"[^>]*value="([^"]+)"', r.text)
        if not m:
            raise PairError("tabId not found on the export-download page")
        self.tab_id = m.group(1)

    def _init_rsk(self, page, comp, title, tab):
        url = f"{BASE_URL}/Bancs/RSK/{page}.do"
        self.s.post(url, headers=INIT_H, timeout=60, data={
            "app": "bancsapp?prefix=/RSK", "page": f"/{page}.do", "confViewSize": "50", "reqType": "0", "mode": "DEF_MODE",
            "parentName": "blank", "browserType": "IE55", "GuiWinName": page, "compname": comp, "over_ride_button": "Cancel",
            "AvlHt": "744", "AvlWd": "1481", "tblWd": "1465", "GuiBrowserInst": "0", "theme": "black", "pageId": page,
            "wintitle": title, "availHeight": "816", "availWidth": "1536", "tabId": tab, "rndaak": self.rndaak,
            "reqtoken": "", "parentToChildData": ""})
        return url

    def _fetch_h(self, url):
        return {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL, "Referer": url}

    @staticmethod
    def _export_fields(payload):
        p = dict(payload)
        now = datetime.now()
        p.update({"Export": "Excel", "enableBatchForExport": "yes", "noPgsToExport": "100", "dateStamp": now.strftime("%Y%m%d"),
                  "timeStamp": now.strftime("%H:%M:%S"), "PageNum": "1", "undefined": "",
                  "MCBrowserEventInformation": "sourceElementName~|~undefined#*#eventType~|~callListExport#*#butId~|~null",
                  "IXHRts": str(int(time.time() * 1000)), "XHR": "true"})
        return p

    def export_383(self):
        """RSK383: search today, then click Export. -> trigger time."""
        url = self._init_rsk("RSK383", "RSK", "Query Detailed Limit", "panel2")
        today_api, today_ui = datetime.now().strftime("%Y%m%d"), datetime.now().strftime("%d/%m/%Y").replace("/", "%2F")
        pl = {"MCB_SearchWC_wca_clearingMemberCode": "", "propertyMap(MCB_SearchWC_wca_clearingMemberCode_op)": "=",
              "MCB_SearchWC_wca_tradingMemberCode": "", "propertyMap(MCB_SearchWC_wca_tradingMemberCode_op)": "=",
              "MCB_SearchWC_wca_uCCcode": "", "propertyMap(MCB_SearchWC_wca_uCCcode_op)": "=",
              "MCB_SearchWC_wca_clientName": "", "propertyMap(MCB_SearchWC_wca_clientName_op)": "=",
              "MCB_SearchWC_wca_fromDate": today_api, "propertyMap(MCB_SearchWC_wca_fromDate_op)": "=",
              "MCB_SearchWC_wca_toDate": today_api, "propertyMap(MCB_SearchWC_wca_toDate_op)": "=",
              "MCB_SearchWC_wca_clearingMemberName": "", "propertyMap(MCB_SearchWC_wca_clearingMemberName_op)": "=",
              "MCB_SearchWC_wca_tradingMemberName": "", "propertyMap(MCB_SearchWC_wca_tradingMemberName_op)": "=",
              "MCB_SearchWC_wca_currency": "", "propertyMap(MCB_SearchWC_wca_currency_op)": "=",
              "GRID_RESPONSE": "RSK383_Table", "operationType": "ET", "searchClicked": "true", "PageNum": "0",
              "service": "queryCashCollDetLimit", "windowName": "RSK383", "MODE": "DEF_MODE", "compName": "RSK", "lgbinst": "0",
              "WRITE_APP": "true", "REQ_TYPE": "0", "reqType": "1", "butvalue": "50", "browserType": "IE55",
              "GuiWinName": "RSK383", "compname": "RSK",
              "sQuery": f"Transaction From Date  Equals  {today_ui} AND Transaction To Date  Equals  {today_ui}",
              "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
              "IXHRts": str(int(time.time() * 1000)), "XHR": "true", "rndaak": self.rndaak, "theme": "black", "tabId": "panel2",
              "mode": "DEF_MODE", "GuiBrowserInst": "0", "parentName": "blank", "confViewSize": "50"}
        h = self._fetch_h(url)
        sr = self.s.post(url, headers=h, data=pl, timeout=120)
        if B._is_expired(sr.text):
            raise _Expired()
        if "RSK383_Table" not in sr.text:
            raise PairError("RSK383 search failed (the deposit table did not load)")
        t = datetime.now()
        er = self.s.post(url, headers=h, data=self._export_fields(pl), timeout=120)
        if "ExportBatchSuccess" not in er.text:
            raise PairError("RSK383 export was not accepted")
        self.log("RSK383 (MCX deposit) export clicked.")
        return t

    def export_335(self):
        """RSK335: pre-call, search, Export. -> trigger time."""
        url = self._init_rsk("RSK335", "RSK", "Margin Utilization View", "panel3")
        h = self._fetch_h(url)
        self.s.post(url, headers=h, timeout=60, data={"xmlReq": "YES", "reqType": "19", "GuiWinName": "RSK335", "GuiBrowserInst": "0",
                    "inParam1": CM_CODE, "inParam2": "DFLT", "tabId": "panel3", "functionName": "funcname", "ifCallPaint": "false",
                    "ctrlId": "MCB_SearchWC_wca_associatedcm", "filterCombo": "NO", "filterS": "sample", "rndaak": self.rndaak})
        pl = {"propertyMap(MCB_SearchWC_wca_bpid_Cmb)": "DFLT", "MCB_SearchWC_wca_bpid": "", "propertyMap(MCB_SearchWC_wca_bpid_op)": "=",
              "MCB_SearchWC_wca_actype": "", "propertyMap(MCB_SearchWC_wca_actype_op)": "=",
              "propertyMap(MCB_SearchWC_wca_associatedtm_Cmb)": "DFLT", "MCB_SearchWC_wca_associatedtm": "",
              "propertyMap(MCB_SearchWC_wca_associatedtm_op)": "=",
              "propertyMap(MCB_SearchWC_wca_associatedcm_Cmb)": "DFLT", "MCB_SearchWC_wca_associatedcm": CM_CODE,
              "propertyMap(MCB_SearchWC_wca_associatedcm_op)": "=",
              "MCB_SearchWC_wca_valuedate": "", "propertyMap(MCB_SearchWC_wca_valuedate_op)": "=",
              "MCB_SearchWC_wca_category": "", "propertyMap(MCB_SearchWC_wca_category_op)": "=",
              "MCB_SearchWC_wca_CMName": "DFLT", "propertyMap(MCB_SearchWC_wca_CMName_op)": "=",
              "MCB_SearchWC_wca_TMName": "DFLT", "propertyMap(MCB_SearchWC_wca_TMName_op)": "=",
              "MCB_SearchWC_wca_datedummy1": "", "propertyMap(MCB_SearchWC_wca_datedummy1_op)": "=",
              "MCB_SearchWC_wca_datedummy2": "", "propertyMap(MCB_SearchWC_wca_datedummy2_op)": "=",
              "GRID_RESPONSE": "RSK335_Table", "operationType": "ET", "searchClicked": "true", "PageNum": "0",
              "service": "qryMarginUtilView", "windowName": "RSK335", "MODE": "DEF_MODE", "compName": "RSK", "lgbinst": "0",
              "WRITE_APP": "true", "REQ_TYPE": "0", "reqType": "1",
              "sQuery": f"Client Code  Equals  DFLT AND TM / CP  Equals  DFLT AND CM  Equals  DFLT AND CM  Equals  {CM_CODE}",
              "AvlWd": "1048", "tblWd": "1026",
              "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
              "IXHRts": str(int(time.time() * 1000)), "XHR": "true", "rndaak": self.rndaak, "theme": "black", "tabId": "panel3",
              "mode": "DEF_MODE", "GuiBrowserInst": "0", "parentName": "blank", "browserType": "IE55", "GuiWinName": "RSK335",
              "compname": "RSK", "confViewSize": "50"}
        sr = self.s.post(url, headers=h, data=pl, timeout=120)
        if B._is_expired(sr.text):
            raise _Expired()
        if "RSK335_Table" not in sr.text:
            raise PairError("RSK335 search failed (the margin table did not load)")
        t = datetime.now()
        er = self.s.post(url, headers=h, data=self._export_fields(pl), timeout=120)
        if "ExportBatchSuccess" not in er.text:
            raise PairError("RSK335 export was not accepted")
        self.log("RSK335 (MCX 90%) export clicked.")
        return t

    # ---- the export list -----------------------------------------------------------------------
    def _list_payload(self, day):
        p = {}
        for f in ("EXPORT_WINDOW_NAME", "EXPORT_TABLE_NAME", "EXPORT_QUERY", "EXPORT_TYPE", "EXPORT_STATUS"):
            p[f"MCB_SearchWC_wca_{f}"] = ""
            p[f"propertyMap(MCB_SearchWC_wca_{f}_op)"] = "="
        for f in ("EXPORT_DATE_FROM", "EXPORT_TIME_FROM", "EXPORT_DATE_TO", "EXPORT_TIME_TO", "EXPORT_TIMESTAMP"):
            p[f"MCB_SearchWC_wca_{f}"] = ""
            p[f"propertyMap(MCB_SearchWC_wca_{f}_From)"] = day.strftime("%d/%m/%Y") if f == "EXPORT_DATE_FROM" else ""
            p[f"propertyMap(MCB_SearchWC_wca_{f}_To)"] = ""
            p[f"propertyMap(MCB_SearchWC_wca_{f}_op)"] = "="
        p.update({"GRID_RESPONSE": "ar_w_DataExport_Table", "operationType": "ET", "searchClicked": "true", "PageNum": "0",
                  "service": "ARET8001GetExportData", "windowName": "ar_w_DataExport", "MODE": "DEF_MODE", "compName": "ARCHGUI",
                  "lgbinst": "0", "WRITE_APP": "true", "REQ_TYPE": "0", "reqType": "1", "sQuery": "  Equals  " + day.strftime("%Y%m%d"),
                  "AvlWd": "1280", "tblWd": "1280",
                  "MCBrowserEventInformation": "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null",
                  "IXHRts": str(int(time.time() * 1000)), "XHR": "true", "rndaak": self.rndaak, "theme": "black", "tabId": self.tab_id,
                  "mode": "DEF_MODE", "GuiBrowserInst": "0", "parentName": "blank", "confViewSize": "50", "browserType": "IE55",
                  "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI", "over_ride_button": "Cancel"})
        return p

    def export_rows(self, day):
        """All rows of the export list (page 1 and, when there is one, page 2). Raises _Expired if the session died."""
        r = self.s.post(EXPORT_URL, headers=DL_H, data=self._list_payload(day), timeout=120)
        if B._is_expired(r.text):
            raise _Expired()
        rows = BeautifulSoup(r.text, "html.parser").select("table tbody tr")
        m = re.search(r'id="totNoOfRec".*value="(\d+)"', r.text)
        total = int(m.group(1)) if m else 0
        if total > 50 or len(rows) >= 50:
            r2 = self.s.post(EXPORT_URL, headers=DL_H, timeout=120, data={
                "tabId": self.tab_id, "GuiBrowserInst": "0", "GuiWinName": "ar_w_DataExport", "compname": "ARCHGUI", "xmlReq": "YES",
                "reqType": "1", "confViewSize": "50", "PageNum": "0", "butvalue": "8", "selectedRow_ar_w_DataExport": "49",
                "tblurl": "ar_w_DataExport_Table",
                "MCBrowserEventInformation": "sourceElementName~|~next#*#eventType~|~click#*#butId~|~8", "rndaak": self.rndaak,
                "theme": "black", "sQuery": "  Equals  " + day.strftime("%Y%m%d"), "AvlWd": "1208", "tblWd": "1186",
                "RowNumKeyForPrevNxt": "1", "PrevOrNext": "N"})
            if B._is_expired(r2.text):
                raise _Expired()
            rows += BeautifulSoup(r2.text, "html.parser").select("table tbody tr")
        return rows

    def download(self, file_name, out_dir):
        chk = f"{BASE_URL}/Bancs/DownloadFileAction1.do?fileName={file_name}&action=checkForFile"
        for i in range(10):
            if "FileExists" in self.s.post(chk, headers=DL_H, data={"rndaak": self.rndaak}, timeout=120).text:
                break
            time.sleep(5)
        else:
            raise PairError(f"{file_name} is not available for download")
        dl = f"{BASE_URL}/Bancs/DownloadFileAction1.do?fileName={file_name}&action=downloadFile"
        r = self.s.post(dl, headers=DL_H, data={"rndaak": self.rndaak}, stream=True, timeout=300)
        if r.status_code != 200:
            raise PairError(f"download of {file_name} failed (HTTP {r.status_code})")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, os.path.basename(file_name))
        with open(path + ".part", "wb") as fh:
            for ch in r.iter_content(8192):
                if ch:
                    fh.write(ch)
        os.replace(path + ".part", path)
        return path


class _Expired(Exception):
    pass


def pick_targets(rows, t383, t335):
    """Which export-list rows are OURS: the file created within 15 s of each export click (RSK383: query has
    'Transaction From Date', RSK335: 'Client Code'). -> {'RSK383': (name, generated?), 'RSK335': (...)}"""
    out = {}
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 7:
            continue
        status, ts, name = cols[4].text.strip(), cols[5].text.strip(), cols[6].text.strip()
        query = cols[2].text.strip().replace("%2F", "/")
        try:
            ft = datetime.strptime(ts, "%d/%m/%Y %H:%M:%S")
        except ValueError:
            continue
        for label, t, key in (("RSK383", t383, "Transaction From Date"), ("RSK335", t335, "Client Code")):
            if label in out and out[label][0] != name:
                continue
            if abs((ft - t).total_seconds()) <= 15 and key in query:
                out[label] = (name, "Generated" in status)
            elif label in out and out[label][0] == name:
                out[label] = (name, "Generated" in status)
    return out


def download_pair(out_dir, log=print, cancel=lambda: False, max_wait=None, interval=None):
    """-> {'RSK383': path, 'RSK335': path}. Raises PairError (message is shown on the card)."""
    user = os.getenv("SA_MCX_DEPOSIT_USER", "56720CMMKR014139").strip()
    password = os.getenv("SA_MCX_DEPOSIT_PASSWORD", "").strip()
    if not password:
        raise PairError("SA_MCX_DEPOSIT_PASSWORD is not set - add it in Credentials")
    max_wait = max_wait or int(os.getenv("MCX_CASH_EXPORT_WAIT_SEC", "900"))
    interval = interval or int(os.getenv("MCX_CASH_EXPORT_POLL_SEC", "30"))
    ses = _Session(user, password, log, cancel)
    ses.login()
    day = datetime.now()
    t383 = t335 = None
    for attempt in range(1, 4):                                  # both exports, one after the other, BEFORE any waiting
        try:
            t383 = ses.export_383()
            t335 = ses.export_335()
            ses.init_arch()
            break
        except _Expired:
            if attempt == 3:
                raise PairError("The MCX session kept expiring while exporting")
            ses.relogin()
    log("Both exports clicked - now waiting for MCX to generate them.")
    targets, relog, elapsed = {}, 0, 0
    while elapsed <= max_wait:
        if cancel():
            raise PairError("Stopped")
        try:
            targets = pick_targets(ses.export_rows(day), t383, t335)
        except _Expired:
            relog += 1
            if relog > 3:
                raise PairError("The MCX session kept expiring while waiting for the files")
            ses.relogin()
            continue
        ready = [k for k, v in targets.items() if v[1]]
        desc = ", ".join("%s %s" % (k, "ready" if v[1] else "generating") for k, v in targets.items())
        log("Export list: " + (desc or "not listed yet"))
        if len(ready) == 2:
            break
        time.sleep(interval)
        elapsed += interval
    if "RSK383" not in targets or "RSK335" not in targets:
        raise PairError("MCX never listed " + " and ".join(k for k in ("RSK383", "RSK335") if k not in targets))
    if not (targets["RSK383"][1] and targets["RSK335"][1]):
        raise PairError(f"MCX did not finish generating the files within {max_wait // 60} minutes")
    out = {}
    for label in ("RSK383", "RSK335"):
        try:
            out[label] = ses.download(targets[label][0], out_dir)
        except requests.RequestException as e:
            raise PairError(f"{label} download failed: {e}")
        log(f"{label} downloaded: {os.path.basename(out[label])}")
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        res = download_pair(a.out)
    except PairError as e:
        print(f"[PAIR_FAIL] {e}")
        return 2
    except Exception as e:                                       # noqa: BLE001
        print(f"[PAIR_FAIL] {type(e).__name__}: {e}")
        return 1
    for k, v in res.items():
        print(f"[PAIR_RESULT] {k}={v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
