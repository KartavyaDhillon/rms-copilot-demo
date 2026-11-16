"""core/mcx_grid.py - the FAST way to get the two MCX files from TCS BANCS (eclear.mcxccl.com): read the rows straight
out of the search grid instead of queueing an Excel export (which can sit in the portal queue for an hour or never come).

  MCX Deposit  (RSK383)            -> MCX_DEPOSIT_HH.MM.xlsx       (12-hour clock, as the console always saved it)
  MCX 90% / Margin Utilization View
               (RSK335)            -> <10 random letters/digits>-<YYYYMMDDHHMMSS>.xlsx   (the portal's own export name)

The names are exactly what the rest of the console already looks for (Short Allocation, CTCL vs NMASS, Margin Shortage ...),
so nothing downstream changes.

How it works: the grid answers one Search request with up to `rows per page` rows. The portal ignores PageNum, but it
honours a bigger rows-per-page value, so ONE request returns the whole result (about 30-40 s for 3,700 rows). The request
is read-only - it is the same Search the button sends.

Retry rule (5 tries by default): the session can end (timeout, or someone else logged in with the same user). Then the whole
login is done again with the NEXT RSA code and only the file(s) still missing are fetched - a file already saved is never
downloaded twice. A wrong password or a refused RSA code (a 3rd wrong code can lock the account) stops at once.

The old method (queue an export and wait for it) is NOT touched. Which method runs is one switch for everybody:
mode "grid" (this module, default) or "queue" (the previous code), kept in .mcx_download_mode.json next to the console
and changeable from the TCS BANCS button in the Process Center (/ops/mcx-download-mode).

This module needs no console objects: the caller hands in the credentials and a `token_fn(attempt, max_attempts,
err_msg) -> str` (each downloader already has one: dashboard popup or the automatic code).
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import string
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from pathlib import Path

BASE_URL = "https://eclear.mcxccl.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"

MODES = ("grid", "queue")
LABELS = {"grid": "Grid view", "queue": "Queue + export (previous)"}
_ROOT = Path(__file__).resolve().parents[1]
MODE_FILE = Path(os.getenv("MCX_DOWNLOAD_MODE_FILE") or (_ROOT / ".mcx_download_mode.json"))

# What the consumers look for in the header row (core/ctcl_mcx_source.REQUIRED_COLUMNS)
REQUIRED_COLUMNS = {"margin": ["Client Code", "Client Margin Limit"], "deposit": ["UCC Code", "Margin Limit"]}
KEYS = ("deposit", "margin")


class GridAuthError(Exception):
    """Wrong password: retrying with the same password cannot help."""


class GridTokenError(Exception):
    """The RSA code could not be produced or was refused too often: stop (more wrong codes could lock the account)."""


# --------------------------------------------------------------------------------- the switch (grid / queue)
def get_mode(path: Path | None = None) -> str:
    """"grid" (default) or "queue". The env var MCX_DOWNLOAD_MODE overrides the file."""
    env = (os.getenv("MCX_DOWNLOAD_MODE") or "").strip().lower()
    if env in MODES:
        return env
    try:
        v = json.loads(Path(path or MODE_FILE).read_text(encoding="utf-8")).get("mode", "grid")
    except (OSError, ValueError):
        return "grid"
    return v if v in MODES else "grid"


def set_mode(mode: str, who: str = "", path: Path | None = None) -> dict:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    state = {"mode": mode, "by": who, "at": datetime.now().isoformat(timespec="seconds")}
    Path(path or MODE_FILE).write_text(json.dumps(state), encoding="utf-8")
    return state


def describe(path: Path | None = None) -> dict:
    mode = get_mode(path)
    try:
        saved = json.loads(Path(path or MODE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {"mode": mode, "label": LABELS[mode], "by": saved.get("by", ""), "at": saved.get("at", ""),
            "env_override": bool((os.getenv("MCX_DOWNLOAD_MODE") or "").strip())}


def enabled() -> bool:
    return get_mode() == "grid"


# --------------------------------------------------------------------------------- file names
def deposit_name(now: datetime | None = None) -> str:
    return f"MCX_DEPOSIT_{(now or datetime.now()).strftime('%I.%M')}.xlsx"


def portal_name(now: datetime | None = None) -> str:
    """Like the portal's own export names, e.g. VFGrGQDoaM-20261109082512.xlsx (10 random chars, '-', 14 digits)."""
    rnd = "".join(random.choices(string.ascii_letters + string.digits, k=10))
    return f"{rnd}-{(now or datetime.now()).strftime('%Y%m%d%H%M%S')}.xlsx"


# --------------------------------------------------------------------------------- login (same flow as the downloaders)
def _encrypt(value: str):
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes
    from Crypto.Util.Padding import pad
    key, iv = get_random_bytes(16), get_random_bytes(16)
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(value.encode(), AES.block_size))
    return base64.b64encode(ct).decode(), base64.b64encode(key + iv).decode()


def _entity(text: str):
    m = re.search(r"<EntityList>.*</EntityList>", text, re.DOTALL)
    if not m:
        raise ValueError("EntityList missing")
    for e in ET.fromstring(m.group(0)).findall(".//EntityDetail"):
        code = e.find("entityCode").text
        if code != "DefaultEntity":
            return code, f"{code}:{e.find('shortDescription').text},"
    raise ValueError("no entity")


def _expired(text: str, table: str = "") -> bool:
    up = text.upper()
    if "SESSION HAS EXPIRED" in up or "sessionExpired" in text:
        return True
    low = text.lower()
    # a real grid answer is a fragment that carries its table; a bounce to the login page is a full page without it
    if table and table not in text and ("<!doctype" in low or "<html" in low or "dologin.jsp" in low or 'name="userid"' in low):
        return True
    return False


def login(sess, user: str, password: str, token_fn, log=print, token_attempts: int = 3, auth_tag: str = "MCX_DEPOSIT"):
    """Full TCS BANCS login. Returns the session token (rndaak)."""
    ajax = {"User-Agent": _UA, "Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL,
            "Referer": BASE_URL + "/Bancs/common/dologin.jsp?method=authMode&JSLoadingMode=null"}
    nav = {"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Referer": BASE_URL + "/Bancs/login.do?method=Login&availHeight=744&availWidth=1522"}
    ajax2 = {"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
             "X-Requested-With": "XMLHttpRequest", "Accept": "*/*", "Referer": BASE_URL + "/Bancs/common/welcome.jsp"}
    log("Opening session ...")
    sess.get(BASE_URL, timeout=60)
    sess.post(BASE_URL + "/Bancs/login.do?method=authMode", headers=ajax, timeout=60)
    p, ig = _encrypt(password)
    r = sess.post(BASE_URL + "/Bancs/login.do?method=authenticatePassward", headers=ajax, timeout=60,
                  data={"userId": user + "\t", "password": p, "lng": "1", "ignoreFlag": ig})
    if "INVALID_LOGIN_OR_PASSWORD" in r.text or "error_div1" in r.text:
        log(f"[{auth_tag}_AUTH_FAIL] wrong password - update it in Credentials")
        raise GridAuthError("wrong password")
    entity = codes = None
    err = None
    for attempt in range(1, token_attempts + 1):
        try:
            code = token_fn(attempt, token_attempts, err)
        except Exception as e:                                   # the callers' token functions raise on timeout / failure
            raise GridTokenError(str(e)) from e
        t, tig = _encrypt(str(code))
        r = sess.post(BASE_URL + "/Bancs/login.do?method=authenticateToken", headers=ajax, timeout=60,
                      data={"userId": user + "\t", "password": t, "lng": "1", "ignoreFlag": tig})
        try:
            entity, codes = _entity(r.text)
            break
        except Exception:
            err = "Invalid token. Please try again."
            log("RSA code refused.")
    if not entity:
        log("[MCX_TOKEN_FAIL] the RSA code was refused - stopped (more wrong codes could lock the account)")
        raise GridTokenError("RSA code refused")
    p2, ig2 = _encrypt(password)
    r = sess.post(BASE_URL + "/Bancs/login.do?method=Login", timeout=60, headers={
        "User-Agent": _UA, "Accept": "text/html,application/xhtml+xml", "Content-Type": "application/x-www-form-urlencoded",
        "Origin": BASE_URL, "Referer": BASE_URL + "/Bancs/common/dologin.jsp"},
        data={"method": "Login", "availHeight": "744", "availWidth": "1522", "isArchival": "false", "userEntityCodes": codes,
              "lng": "1", "userId": user, "ignoreFlag": ig2, "password": p2, "dummy": "FltM/sA2fPfqsjtM55oLOQ==",
              "entity": entity})
    rnd = None
    for pat in (r"name=['\"]rndaak['\"][^>]*value=['\"]([^'\"]+)", r"rndaak['\"]?\s*:\s*['\"]([^'\"]+)"):
        m = re.search(pat, r.text)
        if m:
            rnd = m.group(1)
            break
    if not rnd:
        raise RuntimeError("rndaak not found after login")
    sess.post(BASE_URL + "/Bancs/common/CommonImpl.jsp", headers=nav, data={"rndaak": rnd}, timeout=60)
    sess.post(BASE_URL + "/Bancs/common/welcome.jsp", headers=nav, data={"rndaak": rnd}, timeout=60)
    sess.post(BASE_URL + "/Bancs/common/layout.save", headers=ajax2, data={"events": "", "rndaak": rnd}, timeout=60)
    sess.post(BASE_URL + "/Bancs/common/welcome.jsp", headers=nav, data={"rndaak": rnd}, timeout=60)
    log("Logged in.")
    return rnd


# --------------------------------------------------------------------------------- the two reports
_FETCH_H = {"User-Agent": _UA, "Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest", "Origin": BASE_URL}
_EV_SEARCH = "sourceElementName~|~Search#*#eventType~|~click#*#butId~|~null"
_RSK383_URL = BASE_URL + "/Bancs/RSK/RSK383.do"
_RSK335_URL = BASE_URL + "/Bancs/RSK/RSK335.do"
_REFERER_383 = (_RSK383_URL + "?app=bancsapp?prefix=/RSK&page=/RSK383.do&confViewSize=50&reqType=0&mode=DEF_MODE&cParent="
                "&parentName=blank&browserType=IE55&GuiWinName=RSK383&compname=RSK&locale=&winlocale=&over_ride_button=Cancel"
                "&AvlHt=744&AvlWd=1481&tblWd=1465&GuiBrowserInst=0&theme=black&confViewSize=50&pageId=RSK383"
                "&wintitle=Query%20Detailed%20Limit&availHeight=816&availWidth=1536&tabid=2&tabId=panel2")

_F383 = ("clearingMemberCode", "tradingMemberCode", "uCCcode", "clientName", "fromDate", "toDate", "clearingMemberName",
         "tradingMemberName", "currency", "allocatedCollateral", "deallocatedCollateral", "ownCollateral",
         "ownCollateraldday", "ownothrCollateral", "ownothrCollateraldday", "totalCollateral", "elligibleCollateral",
         "cmtmUnuse", "tmclient", "marginUtilization", "dumamt1", "dumamt2", "dumamt3", "dumvarchar1", "dumvarchar2",
         "dumvarchar3", "settleDate", "dumDate", "settleTime", "dumTime", "ownbod", "owneod", "dummyamt1", "dummyamt2",
         "dummyvarchar1", "dummyvarchar2", "dumnewamt1", "dumnewamt2", "dumnewamt3", "dumnewamt4", "dumnewamt5",
         "dumnewamt6")


def _page_init(sess, rnd, name, prefix, wintitle, tab, avl=("744", "1481", "1465")):
    return sess.post(BASE_URL + f"/Bancs/{prefix}/{name}.do", timeout=60,
                     headers={"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE_URL,
                              "Referer": BASE_URL + "/Bancs/common/PageLoader.html"},
                     data={"app": f"bancsapp?prefix=/{prefix}", "page": f"/{name}.do", "confViewSize": "50", "reqType": "0",
                           "mode": "DEF_MODE", "parentName": "blank", "browserType": "IE55", "GuiWinName": name,
                           "compname": prefix, "over_ride_button": "Cancel", "AvlHt": avl[0], "AvlWd": avl[1], "tblWd": avl[2],
                           "GuiBrowserInst": "0", "theme": "black", "pageId": name, "wintitle": wintitle,
                           "availHeight": "816", "availWidth": "1536", "tabId": tab, "rndaak": rnd, "reqtoken": "",
                           "parentToChildData": ""})


def _body_383(rnd: str, date_api: str, size: int | None) -> str:
    """The exact field order the browser sends for the RSK383 Search; `size` = rows per page (None = the portal's 50)."""
    from urllib.parse import quote
    d = datetime.strptime(date_api, "%Y%m%d").strftime("%d/%m/%Y").replace("/", "%2F")
    parts = []
    for f in _F383:
        parts.append(f"{quote('MCB_SearchWC_wca_' + f, safe='()_')}={date_api if f in ('fromDate', 'toDate') else ''}")
        parts.append(f"{quote('propertyMap(MCB_SearchWC_wca_' + f + '_op)', safe='()_')}=%3D")
    vs = str(size or 50)
    tail = [("GRID_RESPONSE", "RSK383_Table"), ("operationType", "ET"), ("searchClicked", "true"), ("PageNum", "0"),
            ("service", "queryCashCollDetLimit"), ("windowName", "RSK383"), ("MODE", "DEF_MODE"), ("compName", "RSK"),
            ("lgbinst", "0"), ("WRITE_APP", "true"), ("REQ_TYPE", "0"),
            ("sQuery", f"Transaction From Date  Equals  {d} AND Transaction To Date  Equals  {d}"),
            ("AvlWd", "1472"), ("tblWd", "1450"), ("MCBrowserEventInformation", _EV_SEARCH),
            ("IXHRts", str(int(time.time() * 1000))), ("XHR", "true"), ("rndaak", rnd), ("theme", "black"),
            ("tabId", "panel2"), ("mode", "DEF_MODE"), ("GuiBrowserInst", "0"), ("parentName", "blank"),
            ("parentToChildCopyData", ""), ("childToParentCopyData", ""), ("parentTabId", ""),
            ("childToParentJavaCopyFlag", ""), ("openedFromBtn", ""), ("confViewSize", vs), ("favouriteData", ""),
            ("ExtnAttrDropDownValueDesc", "{}"), ("ExtnRadioValues", "{}"), ("butvalue", vs if size else ""),
            ("reqType", "1"), ("childTabId", ""), ("popupctrl", ""), ("callPopUp", ""), ("frombsle", "NO"),
            ("fromTblBtn", "NO"), ("functionName", ""), ("ifCallPaint", "0"), ("controlName", ""), ("param", ""),
            ("WindowName", ""), ("parentSelectedRow", ""), ("browserType", "IE55"), ("GuiWinName", "RSK383"),
            ("compname", "RSK"), ("locale", ""), ("winlocale", ""), ("MCBrowserEventInformation", _EV_SEARCH),
            ("SortedColumnName", ""), ("over_ride_button", "Cancel"), ("confViewSize", ""), ("parentToChildData", "")]
    parts += [f"{quote(k, safe='()_')}={quote(v, safe='*')}" for k, v in tail]
    return "&".join(parts)


def _data_335(rnd: str, cm: str, size: int | None) -> dict:
    d = {"propertyMap(MCB_SearchWC_wca_bpid_Cmb)": "DFLT", "MCB_SearchWC_wca_bpid": "",
         "propertyMap(MCB_SearchWC_wca_bpid_op)": "=", "MCB_SearchWC_wca_actype": "",
         "propertyMap(MCB_SearchWC_wca_actype_op)": "=", "propertyMap(MCB_SearchWC_wca_associatedtm_Cmb)": "DFLT",
         "MCB_SearchWC_wca_associatedtm": "", "propertyMap(MCB_SearchWC_wca_associatedtm_op)": "=",
         "propertyMap(MCB_SearchWC_wca_associatedcm_Cmb)": "DFLT", "MCB_SearchWC_wca_associatedcm": cm,
         "propertyMap(MCB_SearchWC_wca_associatedcm_op)": "=", "MCB_SearchWC_wca_valuedate": "",
         "propertyMap(MCB_SearchWC_wca_valuedate_op)": "=", "MCB_SearchWC_wca_category": "",
         "propertyMap(MCB_SearchWC_wca_category_op)": "=", "MCB_SearchWC_wca_CMName": "DFLT",
         "propertyMap(MCB_SearchWC_wca_CMName_op)": "=", "MCB_SearchWC_wca_TMName": "DFLT",
         "propertyMap(MCB_SearchWC_wca_TMName_op)": "=", "MCB_SearchWC_wca_datedummy1": "",
         "propertyMap(MCB_SearchWC_wca_datedummy1_op)": "=", "MCB_SearchWC_wca_datedummy2": "",
         "propertyMap(MCB_SearchWC_wca_datedummy2_op)": "=", "GRID_RESPONSE": "RSK335_Table", "operationType": "ET",
         "searchClicked": "true", "PageNum": "0", "service": "qryMarginUtilView", "windowName": "RSK335",
         "MODE": "DEF_MODE", "compName": "RSK", "lgbinst": "0", "WRITE_APP": "true", "REQ_TYPE": "0", "reqType": "1",
         "sQuery": f"Client Code  Equals  DFLT AND TM / CP  Equals  DFLT AND CM  Equals  DFLT AND CM  Equals  {cm}",
         "AvlWd": "1048", "tblWd": "1026", "MCBrowserEventInformation": _EV_SEARCH,
         "IXHRts": str(int(time.time() * 1000)), "XHR": "true", "rndaak": rnd, "theme": "black", "tabId": "panel3",
         "mode": "DEF_MODE", "GuiBrowserInst": "0", "parentName": "blank", "browserType": "IE55",
         "GuiWinName": "RSK335", "compname": "RSK", "confViewSize": str(size or 50)}
    if size:
        d["butvalue"] = str(size)
    return d


def _open_335(sess, rnd, cm):
    _page_init(sess, rnd, "RSK335", "RSK", "Margin Utilization View", "panel3")
    sess.post(_RSK335_URL, timeout=60, headers=dict(_FETCH_H, Referer=_RSK335_URL), data={
        "xmlReq": "YES", "reqType": "19", "GuiWinName": "RSK335", "GuiBrowserInst": "0", "inParam1": cm, "inParam2": "DFLT",
        "tabId": "panel3", "functionName": "funcname", "ifCallPaint": "false", "ctrlId": "MCB_SearchWC_wca_associatedcm",
        "filterCombo": "NO", "filterS": "sample", "rndaak": rnd})


def parse_grid(html: str, table: str):
    """-> (headers, rows, amount_headers). Value = svrVal (the raw value) else the shown text. Columns the portal hides
    (td class 'hide') are dropped - exactly like the portal's own Excel."""
    import html as _h
    ths = re.findall(r"<th[^>]*columnLabel=\"([^\"]*)\"[^>]*>(.*?)</th>", html, re.S)
    texts = [re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", t))).strip() for _, t in ths]
    raw = []
    for tr in re.findall(r'<tr[^>]*tableId="%s"[^>]*>(.*?)</tr>' % re.escape(table), html, re.S):
        cells = []
        for attrs, inner in re.findall(r"<td([^>]*)>(.*?)</td>", tr, re.S):
            m = re.search(r'svrVal="([^"]*)"', attrs)
            shown = re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", "", inner)).replace("\xa0", " ")).strip()
            cells.append((bool(re.search(r'class="[^"]*\bhide\b', attrs)), 'isAmountField="true"' in attrs,
                          _h.unescape(m.group(1)).strip() if m and m.group(1) != "" else shown))
        raw.append(cells)
    keep = list(range(len(texts)))
    if raw:
        keep = [i for i in keep if i < len(raw[0]) and not raw[0][i][0]]
    heads = [texts[i] for i in keep]
    amounts = {heads[k] for k, i in enumerate(keep) if raw and raw[0][i][1]}
    rows = [[(r[i][2] if i < len(r) else "") for i in keep] for r in raw]
    return heads, rows, amounts


def _fetch_whole(post, table: str, log):
    """One request for the whole result. `post(size)` returns the HTML. Raises when the rows are short."""
    size = 6000
    rows, total = [], 0
    for _ in range(3):
        t0 = time.time()
        html = post(size)
        heads, rows, amounts = parse_grid(html, table)
        m = re.search(r"tableRecSize=.?(\d+)", html)
        total = int(m.group(1)) if m else 0
        log(f"  got {len(rows)} rows (portal total {total or '?'}) in {time.time() - t0:.0f}s")
        if not total:
            raise RuntimeError("no total in the answer - the search returned nothing")
        if len(rows) >= total:
            return heads, rows, amounts, total
        size = total + 500
    raise RuntimeError(f"only {len(rows)} of {total} rows came back")


def write_excel(heads, rows, amounts, path):
    """Real numbers / dates, readable widths, styled header - written to a temp name, then moved into place so a reader
    never sees a half-written file."""
    import pandas as pd
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    def num(v):
        s = str(v).strip().replace(",", "")
        return None if s == "" else float(Decimal(s))             # 2.67861904855E9 -> 2678619048.55

    rows = [(r + [""] * len(heads))[:len(heads)] for r in rows]
    df = pd.DataFrame(rows, columns=heads)
    numeric, dates = set(), set()
    for c in df.columns:
        col = df[c]
        nonblank = col.astype(str).str.strip() != ""
        if "date" in str(c).lower() and nonblank.any() and col[nonblank].astype(str).str.fullmatch(r"\d{8}").all():
            df[c] = [datetime.strptime(str(v), "%Y%m%d") if str(v).strip() else None for v in col]
            dates.add(c)
        elif c in amounts:
            try:
                df[c] = [num(v) for v in col]
                numeric.add(c)
            except Exception:
                pass
    path = str(path)
    tmpdir = os.path.join(os.path.dirname(path) or ".", ".tmp_mcx")
    os.makedirs(tmpdir, exist_ok=True)
    tmp = os.path.join(tmpdir, os.path.basename(path))
    with pd.ExcelWriter(tmp, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="Data")
        ws = xw.sheets["Data"]
        fill = PatternFill("solid", fgColor="1F4E78")
        for j, c in enumerate(df.columns, start=1):
            h = ws.cell(row=1, column=j)
            h.font, h.fill = Font(bold=True, color="FFFFFF"), fill
            h.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
            if c in numeric:
                fmt, width = "#,##0.00", max(14, min(22, len(str(c)) // 2 + 4))
            elif c in dates:
                fmt, width = "dd-mm-yyyy", 12
            else:
                fmt = None
                width = max(10, min(40, max([len(str(v)) for v in df[c].head(500)] + [len(str(c)) // 2]) + 2))
            ws.column_dimensions[get_column_letter(j)].width = width
            if fmt:
                for r in range(2, len(df) + 2):
                    ws.cell(row=r, column=j).number_format = fmt
        ws.row_dimensions[1].height = 45
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    os.replace(tmp, path)
    return path


def fetch_one(sess, rnd, key: str, out_dir: str, log=print, cm: str = "56720", date_api: str | None = None,
              names: dict | None = None) -> tuple[str, int]:
    """Download ONE report with an already logged-in session -> (saved path, rows). Raises on any problem.
    `names` = {key: file name or a function returning one} when a caller needs its own name (PROBABLE keeps
    'Clientwise_mcx_margin.xlsx'); the defaults are deposit_name() / portal_name()."""
    date_api = date_api or datetime.now().strftime("%Y%m%d")
    if key == "deposit":
        _page_init(sess, rnd, "RSK383", "RSK", "Query Detailed Limit", "panel2")
        table = "RSK383_Table"

        def post(size):
            r = sess.post(_RSK383_URL, headers=dict(_FETCH_H, Referer=_REFERER_383), timeout=300,
                          data=_body_383(rnd, date_api, size))
            if _expired(r.text, table):
                raise _SessionLost()
            return r.text
    else:
        _open_335(sess, rnd, cm)
        table = "RSK335_Table"

        def post(size):
            r = sess.post(_RSK335_URL, headers=dict(_FETCH_H, Referer=_RSK335_URL), timeout=300, data=_data_335(rnd, cm, size))
            if _expired(r.text, table):
                raise _SessionLost()
            return r.text
    heads, rows, amounts, total = _fetch_whole(post, table, log)
    norm = {re.sub(r"\s+", " ", str(h)).strip().lower() for h in heads}
    missing = [c for c in REQUIRED_COLUMNS[key] if c.lower() not in norm]
    if missing:
        log(f"[MCX_GRID_COLUMNS_MISSING] {missing} not in the {key} file - the console would not accept it. Found: {heads}")
        raise RuntimeError(f"column(s) {missing} missing")
    os.makedirs(out_dir, exist_ok=True)
    nm = (names or {}).get(key)
    nm = nm() if callable(nm) else nm
    path = os.path.join(out_dir, nm or (deposit_name() if key == "deposit" else portal_name()))
    write_excel(heads, rows, amounts, path)
    return path, len(rows)


class _SessionLost(Exception):
    """The portal says the session has ended."""


def run_grid(want, out_dir: str, user: str, password: str, token_fn, log=print, tries: int = 5, cm: str = "56720",
             date_api: str | None = None, token_attempts: int = 3, auth_tag: str = "MCX_DEPOSIT", before_relogin=None,
             sleep=time.sleep, session_factory=None, outdirs: dict | None = None, names: dict | None = None) -> dict:
    """Download the wanted files ("deposit", "margin") with the grid method, up to `tries` attempts.

    Every attempt = one fresh login (the next RSA code) + only the files still missing. Returns {key: path} for what was
    saved - when it is shorter than `want`, the rest failed (the caller decides: fail, or fall back to the previous method).
    Raises GridAuthError / GridTokenError at once (retrying those is useless or dangerous)."""
    import requests
    need = [k for k in KEYS if k in set(want)]
    done: dict = {}
    for attempt in range(1, tries + 1):
        if not need:
            break
        log(f"=== MCX grid download - attempt {attempt}/{tries} - still needed: {', '.join(need)} ===")
        if attempt > 1 and before_relogin:
            try:
                before_relogin(attempt)
            except Exception as e:                                   # a helper must never stop the retry
                log(f"  (pause before login skipped: {e})")
        sess = session_factory() if session_factory else requests.Session()
        try:
            rnd = login(sess, user, password, token_fn, log, token_attempts, auth_tag)
            for key in list(need):
                t0 = time.time()
                log(f"[{attempt}/{tries}] {'MCX Deposit (RSK383)' if key == 'deposit' else 'MCX 90% (RSK335)'} ...")
                path, n = fetch_one(sess, rnd, key, (outdirs or {}).get(key) or out_dir, log, cm, date_api, names)
                done[key] = path
                need.remove(key)
                log(f"OK {key}: {n} rows -> {os.path.basename(path)} ({time.time() - t0:.0f}s)")
        except (GridAuthError, GridTokenError):
            raise
        except _SessionLost:
            log("Session ended (timeout, or someone else logged in with this user) - logging in again with the next RSA code.")
        except Exception as e:
            log(f"Attempt {attempt}/{tries} failed: {type(e).__name__}: {e}")
        if need and attempt < tries:
            sleep(3)
    if need:
        log(f"[MCX_GRID_FAILED] not downloaded after {tries} tries: {', '.join(need)}")
    return done
