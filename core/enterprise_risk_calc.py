"""core/enterprise_risk_calc.py - the ENTERPRISE RISK REPORT calculation (FINAL.py / FINAL_REDFLAG.py, one code, two variants).

No mail, no backup, no hard-coded folder or login: it reads files, builds the two report workbooks, adds the screener.in data and
(optionally) password-protects the outputs. The card (core/enterprise_risk.py) feeds it.

Inputs  (all in one folder)
    EnterpriseRiskReport_Without_Margin[_REDFLAG]_<yyyy-mm-dd>.xlsx   mail attachment (plain or password protected)
    EnterpriseRiskReport_With_Margin[_REDFLAG]_<yyyy-mm-dd>.xlsx      mail attachment (plain or password protected)
    FULLFILE_CONCENTRATION_<ddmmyyyy>.xlsx                            output of MTF CONCENTRATION
    Concentration_abv20percentage.csv                                 mail attachment (an encrypted workbook with a .csv name)
Outputs
    ENTERPRISE_WITHOUT_MARGIN[_REDFLAG]_<ddmmyyyy>.xlsx , ENTERPRISE_WITH_MARGIN[_REDFLAG]_<ddmmyyyy>.xlsx
        sheets: <risk sheet> / MTF CONCENTRATION / CONCENTRATION_ABV20 / SCREENER
    screener_output[_REDFLAG].xlsx   (one row per symbol of both reports)
"""
from __future__ import annotations

import io
import os
import re
import time
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

VARIANTS = {
    "redflag": {
        "label": "REDFLAG",
        "tag": "_REDFLAG",
        "mail_key": {"without": "enterpriseriskreportwithoutmarginredflag", "with": "enterpriseriskreportwithmarginredflag"},
        "must_have": "redflag", "must_not_have": "",
        "sheet": {"without": "REDFLAG_WITHOUT_MARGIN", "with": "REDFLAG_WITH_MARGIN"},
        "screener_file": "screener_output_REDFLAG.xlsx",
        "subject": "Enterprise Risk Red Flag Report Automation | {d}",
        "intro": ("Please find attached the automated Enterprise Risk Red Flag reports generated for today's run.<br><br>\n"
                  "<b>Note:</b> These reports have been prepared using the Enterprise Red Flag files (With Margin and Without Margin) "
                  "received from <b>noreply@mastertrust.co.in</b>.<br><br>"),
        "act": "Enterprise Risk Red Flag report",
    },
    "simple": {
        "label": "SIMPLE",
        "tag": "",
        "mail_key": {"without": "enterpriseriskreportwithoutmargin", "with": "enterpriseriskreportwithmargin"},
        "must_have": "", "must_not_have": "redflag",
        "sheet": {"without": "ENTERPRISERISK_WITHOUT_MARGIN", "with": "ENTERPRISERISK_WITH_MARGIN"},
        "screener_file": "screener_output.xlsx",
        "subject": "Enterprise Risk Report Automation | {d}",
        "intro": "Please find attached the automated Enterprise reports generated for today's run.<br><br>",
        "act": "Enterprise Risk report",
    },
}
SHARED = {"concentration_file": "Concentration_abv20percentage.csv"}
SHEET_MTF, SHEET_ABV = "MTF CONCENTRATION", "CONCENTRATION_ABV20"
SCREENER_COLUMNS = ["SYMBOL", "Market Cap", "Current Price", "High / Low", "Stock P/E", "Book Value", "Dividend Yield", "ROCE", "ROE",
                    "Face Value", "OPM", "Profit after tax", "Price to book value", "EPS", "Promoter holding", "Industry PE",
                    "PEG Ratio", "Debt to equity"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class CalcError(Exception):
    """A file is not what the calculation needs (the message says what)."""


class Cancelled(Exception):
    pass


def variant(name: str) -> dict:
    if name not in VARIANTS:
        raise CalcError(f"unknown variant {name!r}")
    return VARIANTS[name]


# ───────────────────────────── file names ─────────────────────────────
def input_name(var: str, kind: str, d2: str) -> str:
    """Name (prefix) of the Enterprise mail attachment of a day: kind 'with' / 'without', d2 = yyyy-mm-dd."""
    return f"EnterpriseRiskReport_{'With' if kind == 'with' else 'Without'}_Margin{variant(var)['tag']}_{d2}"


def output_name(var: str, kind: str, d1: str) -> str:
    return f"ENTERPRISE_{'WITH' if kind == 'with' else 'WITHOUT'}_MARGIN{variant(var)['tag']}_{d1}.xlsx"


def find_file(folder, prefix: str):
    folder = Path(folder)
    for ext in ("xlsx", "xls", "csv"):
        for p in sorted(folder.glob(f"{prefix}*.{ext}")):
            if not p.name.startswith("~$"):
                return p
    return None


# ───────────────────────────── reading (plain or password protected) ─────────────────────────────
ALIASES = {
    "ISIN": ["ISIN", "ISINCODE", "ISINNO"],
    "BOSCRIP": ["BOSCRIP", "BSESCRIP", "BSESCRIPCODE", "SCRIPCODE", "BOSCRIPCODE"],
    "SYMBOL": ["SYMBOL", "NSESYMBOL", "SCRIPSYMBOL", "SYMBOLNAME"],
    "SCRIPNAME": ["SCRIPNAME", "SCRIPTNAME", "SECURITYNAME", "COMPANYNAME", "NAMEOFSCRIP"],
    "NET_EXPOSURE": ["NETEXPOSURE", "NETEXP", "NETEXPOSUREAMT", "NETEXPOSUREVALUE"],
    "NET_EXP_GROSS": ["NETEXPGROSS", "NETEXPOSUREGROSS", "GROSSNETEXPOSURE", "NETEXPGROSSAMT"],
    "SECTOR": ["SECTOR", "SECTORNAME"],
    "INDUSTRY": ["INDUSTRY", "INDUSTRYNAME"],
    "COMB_NSE_BSE_3MONTH_VAL": ["COMBNSEBSE3MONTHVAL", "COMBNSEBSE3MONTHVALUE", "NSEBSE3MONTHVAL", "COMBINED3MONTHVAL", "3MONTHVAL",
                                "COMBNSEBSE3MVAL"],
    "DAYSTOLIQUIDATE_5PER_VOL": ["DAYSTOLIQUIDATE5PERVOL", "DAYSTOLIQUIDATE", "DAYSTOLIQUIDATE5PCTVOL", "DAYSTOLIQUIDATE5PERCENTVOL",
                                 "DAYSTOLIQ", "DAYSTOLIQUIDATE5VOL"],
    "FLAG": ["FLAG", "REDFLAG", "RISKFLAG", "FLAGS"],
}


def _ckey(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x).upper())


_LOOKUP = {_ckey(a): std for std, alts in ALIASES.items() for a in alts + [std]}


def standardise_columns(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    new, seen = [], set()
    for col in df.columns:
        std = _LOOKUP.get(_ckey(str(col).strip()), str(col).strip())
        if std in seen:
            std = f"{std}_{sum(1 for x in seen if x.startswith(std)) + 1}"
        seen.add(std)
        new.append(std)
    df.columns = new
    miss = [c for c in ("SYMBOL", "NET_EXPOSURE", "DAYSTOLIQUIDATE_5PER_VOL") if c not in df.columns]
    if miss:
        raise CalcError(f"{filename}: column(s) {miss} not found. Columns present: {list(df.columns)}")
    return df


def unlock(path, passwords) -> tuple:
    """(BytesIO of an openable workbook, how). A plain workbook needs no password."""
    path = Path(path)
    head = path.read_bytes()[:8]
    if not head.startswith(b"\xd0\xcf\x11\xe0"):
        return io.BytesIO(path.read_bytes()), "no password"
    try:
        import msoffcrypto
    except ImportError:
        raise CalcError("the 'msoffcrypto-tool' package is not installed in the console's Python (pip install msoffcrypto-tool)")
    with open(path, "rb") as f:
        if not msoffcrypto.OfficeFile(f).is_encrypted():
            return io.BytesIO(path.read_bytes()), "no password (legacy xls)"
    pw = [p for p in passwords if p]
    if not pw:
        raise CalcError(f"{path.name} is password protected - set the password in Credentials")
    for p in pw:
        try:
            with open(path, "rb") as f:
                office = msoffcrypto.OfficeFile(f)
                office.load_key(password=p)
                out = io.BytesIO()
                office.decrypt(out)
            out.seek(0)
            pd.ExcelFile(out)                                    # proves the password was right
            out.seek(0)
            return out, "password protected - unlocked"
        except Exception:                                        # noqa: BLE001
            continue
    raise CalcError(f"{path.name} is password protected and the password in Credentials did not open it")


def _read_enterprise_excel(source, filename: str) -> pd.DataFrame:
    """Sheet + header row with the most recognisable column names (handles title rows)."""
    book = pd.ExcelFile(source)
    best = (-1, book.sheet_names[0], 0)
    for sheet in book.sheet_names:
        raw = book.parse(sheet, header=None, nrows=25)
        for r in range(len(raw)):
            score = sum(1 for v in raw.iloc[r].tolist() if _ckey(v) in _LOOKUP)
            if score > best[0]:
                best = (score, sheet, r)
    score, sheet, hdr = best
    if score < 2:
        raise CalcError(f"{filename}: no header row with recognisable column names (sheets: {book.sheet_names})")
    return book.parse(sheet, header=hdr)


def read_enterprise(path, passwords) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        for enc in ("utf-8", "latin1", "cp1252"):
            for sep in (",", ";", "\t"):
                try:
                    df = pd.read_csv(path, encoding=enc, sep=sep, low_memory=False)
                    if len(df.columns) > 2:
                        return standardise_columns(df, path.name)
                except CalcError:
                    raise
                except Exception:                                # noqa: BLE001
                    pass
    src, _how = unlock(path, passwords)
    return standardise_columns(_read_enterprise_excel(src, path.name), path.name)


def read_other(path, passwords) -> pd.DataFrame:
    """FULLFILE_CONCENTRATION / Concentration_abv20percentage (the latter is an encrypted workbook with a .csv name)."""
    path = Path(path)
    if path.suffix.lower() == ".csv" and not path.read_bytes()[:4] == b"\xd0\xcf\x11\xe0" and not path.read_bytes()[:2] == b"PK":
        for enc in ("utf-8", "latin1", "cp1252"):
            for sep in (",", ";", "\t"):
                try:
                    df = pd.read_csv(path, encoding=enc, sep=sep, low_memory=False)
                    if len(df.columns) > 2:
                        return df
                except Exception:                                # noqa: BLE001
                    pass
    src, _how = unlock(path, passwords)
    return pd.read_excel(src)


# ───────────────────────────── selection ─────────────────────────────
def pick_symbols(df: pd.DataFrame, top_n) -> list:
    """Top N symbols by NET_EXPOSURE, then top N by DAYSTOLIQUIDATE_5PER_VOL, merged without duplicates (top_n None = all rows)."""
    d = df.copy()
    d["SYMBOL"] = d["SYMBOL"].astype(str).str.strip()
    d["NET_EXPOSURE"] = pd.to_numeric(d["NET_EXPOSURE"], errors="coerce")
    d["DAYSTOLIQUIDATE_5PER_VOL"] = pd.to_numeric(d["DAYSTOLIQUIDATE_5PER_VOL"], errors="coerce")

    def top(col):
        lst = list(dict.fromkeys(d.sort_values(col, ascending=False)["SYMBOL"].dropna().tolist()))
        return lst[:top_n] if top_n else lst
    out = []
    for s in top("NET_EXPOSURE") + top("DAYSTOLIQUIDATE_5PER_VOL"):
        if s not in out:
            out.append(s)
    return out


def ordered_filter(df: pd.DataFrame, column: str, symbols: list) -> pd.DataFrame:
    df = df.copy()
    df[column] = df[column].astype(str).str.strip()
    parts = [df[df[column] == s] for s in symbols]
    return pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0]


def prepare_concentration(df: pd.DataFrame) -> pd.DataFrame:
    for c in ("ScripName", "FIN_UPTODATE_MTF", "Scrip_Concentration[(T/U)*100]"):
        if c not in df.columns:
            raise CalcError(f"FULLFILE_CONCENTRATION has no '{c}' column")
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()
    df["FIN_UPTODATE_MTF"] = pd.to_numeric(df["FIN_UPTODATE_MTF"], errors="coerce")
    df["Scrip_Concentration[(T/U)*100]"] = pd.to_numeric(df["Scrip_Concentration[(T/U)*100]"], errors="coerce")
    return df[(df["FIN_UPTODATE_MTF"].abs() > 500000) & (df["Scrip_Concentration[(T/U)*100]"] > 10)]


def prepare_abv20(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()
    for c in ("symbol", "net ledger"):
        if c not in df.columns:
            raise CalcError(f"Concentration_abv20percentage has no '{c}' column")
    df["net ledger"] = pd.to_numeric(df["net ledger"], errors="coerce")
    return df[df["net ledger"] > 500000]


def _autofit(ws, cap=50, pad=3):
    for column in ws.columns:
        longest = 0
        for cell in column:
            if cell.value is not None:
                longest = max(longest, len(str(cell.value)))
        ws.column_dimensions[column[0].column_letter].width = min(longest + pad, cap) if cap else longest + pad


def write_report(path, risk: pd.DataFrame, mtf: pd.DataFrame, abv: pd.DataFrame, risk_sheet: str) -> None:
    for d in (risk, mtf, abv):
        d.fillna("", inplace=True)
        d.replace([float("inf"), float("-inf")], "", inplace=True)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        risk.to_excel(w, sheet_name=risk_sheet, index=False)
        mtf.to_excel(w, sheet_name=SHEET_MTF, index=False)
        abv.to_excel(w, sheet_name=SHEET_ABV, index=False)
        for ws in w.sheets.values():
            _autofit(ws)


# ───────────────────────────── screener.in ─────────────────────────────
def screener_login(user: str, password: str, session=None, log=print):
    """Logged-in requests.Session (raises CalcError when the login is refused)."""
    if not (user and password):
        raise CalcError("the screener.in login is not set - use the Credentials button")
    import requests
    s = session or requests.Session()
    hdr = {"User-Agent": UA, "Referer": "https://www.screener.in/login/"}
    s.get("https://www.screener.in/login/", headers=hdr, timeout=40)
    s.post("https://www.screener.in/login/", data={"csrfmiddlewaretoken": s.cookies.get("csrftoken"), "next": "",
                                                    "username": user, "password": password}, headers=hdr, allow_redirects=True, timeout=40)
    dash = s.get("https://www.screener.in/dash/", headers=hdr, timeout=40)
    if "/login" in str(getattr(dash, "url", "")):
        raise CalcError("screener.in login FAILED - check the user and password in Credentials")
    log("screener.in login OK")
    return s


def _pairs(container, data: dict) -> None:
    if not container:
        return
    for li in container.find_all("li"):
        name, value = li.find("span", class_="name"), li.find("span", class_="value")
        if name and value:
            data[" ".join(name.text.split())] = " ".join(value.text.split())


def _num(v):
    try:
        return float(re.sub(r"[^\d\.\-]", "", str(v)))
    except ValueError:
        return None


def scrape_symbol(session, symbol: str, sleep=time.sleep, log=print, check=lambda: None) -> dict:
    """One company's ratios from screener.in (the newer any_screener.py logic: consolidated first, quick-ratio API, fallbacks)."""
    from urllib.parse import quote
    from bs4 import BeautifulSoup
    hdr = {"User-Agent": UA, "Referer": "https://www.screener.in/login/"}
    q = quote(symbol, safe="")

    def soup_of(url):
        check()
        return BeautifulSoup(session.get(url, headers=hdr, timeout=60).text, "html.parser")
    data = {"SYMBOL": symbol}
    soup = soup_of(f"https://www.screener.in/company/{q}/consolidated/")
    use_cons = False
    cdiv = soup.find("div", id="company-info")
    if cdiv and cdiv.get("data-warehouse-id"):
        rt = soup.find("ul", id="top-ratios")
        if rt and "₹ Cr." not in rt.text:
            use_cons = True
    if not use_cons:
        soup = soup_of(f"https://www.screener.in/company/{q}/")
    cdiv = soup.find("div", id="company-info")
    company_id = cdiv.get("data-warehouse-id") if cdiv else None
    _pairs(soup.find("ul", id="top-ratios"), data)
    if company_id:
        qr = None
        for _ in range(5):
            check()
            qr = session.get(f"https://www.screener.in/api/company/{company_id}/quick_ratios/",
                             headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.screener.in/", "X-Requested-With": "XMLHttpRequest"},
                             timeout=60)
            if qr.status_code == 429:
                log(f"{symbol}: rate limit - waiting 5 s")
                sleep(5)
                continue
            break
        if qr is not None and qr.status_code == 200:
            _pairs(BeautifulSoup(qr.text, "html.parser"), data)
    meta = soup.find("meta", attrs={"name": "description"})
    if meta:
        text = meta.get("content", "")
        m = re.search(r"Profit:\s*(-?[\d,\.]+)\s*Cr", text)
        if m:
            data["Profit after tax"] = f"₹ {m.group(1)} Cr."
        m = re.search(r"Promoter Holding:\s*([\d\.]+%)", text)
        if m:
            data["Promoter holding"] = m.group(1)
    sec = soup.find("section", id="profit-loss")
    if sec:
        for tr in sec.find_all("tr"):
            cols = tr.find_all("td")
            if len(cols) > 1:
                metric, latest = " ".join(cols[0].text.split()), " ".join(cols[-1].text.split())
                if metric == "OPM %":
                    data["OPM"] = latest
                elif metric.startswith("Net Profit"):
                    data["_np"] = latest
                elif metric == "EPS in Rs":
                    data["EPS"] = "₹ " + latest
    for w in ("Price to book value", "PEG Ratio", "Profit after tax", "Debt to equity", "Industry PE"):
        if not data.get(w):
            for k in list(data):
                if k.lower().replace(" ", "") == w.lower().replace(" ", ""):
                    data[w] = data[k]
    npl = data.pop("_np", "")
    if not data.get("Profit after tax") and npl:
        data["Profit after tax"] = f"₹ {npl} Cr."
    if not data.get("Price to book value"):
        cp, bv = _num(data.get("Current Price")), _num(data.get("Book Value"))
        if cp and bv:
            data["Price to book value"] = f"{cp / bv:.2f}"
    for c in SCREENER_COLUMNS:
        data.setdefault(c, "")
    return data


def screener_rows(session, symbols, sleep=time.sleep, log=print, check=lambda: None, pause=2.0, cache=None) -> list:
    """Rows (in SCREENER_COLUMNS order) for the symbols; a symbol that fails gets an empty row (as the hand script did)."""
    cache = {} if cache is None else cache
    rows = []
    for sym in symbols:
        check()
        try:
            if sym not in cache:
                log(f"screener: {sym}")
                cache[sym] = scrape_symbol(session, sym, sleep=sleep, log=log, check=check)
                sleep(pause)
            row = cache[sym]
        except Exception as e:                                   # noqa: BLE001
            if isinstance(e, Cancelled) or getattr(e, "is_cancel", False):
                raise                                            # Stop pressed: not a failed symbol
            log(f"screener: {sym} failed - {type(e).__name__}: {e}")
            row = {"SYMBOL": sym}
        rows.append([row.get(c, "") for c in SCREENER_COLUMNS])
    return rows


def add_screener_sheet(path, rows: list) -> None:
    from openpyxl import load_workbook
    df = pd.DataFrame(rows, columns=SCREENER_COLUMNS)
    with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
        df.to_excel(w, sheet_name="SCREENER", index=False)
    wb = load_workbook(path)
    ws = wb["SCREENER"]
    ws.freeze_panes = "A2"
    _autofit(ws, cap=None)
    wb.save(path)


def write_screener_file(path, rows: list, sheet="Sheet1", dedupe=True, cap=50) -> None:
    from openpyxl import load_workbook
    df = pd.DataFrame(rows, columns=SCREENER_COLUMNS)
    if dedupe:
        df = df.drop_duplicates(subset=["SYMBOL"], keep="first")
    df.to_excel(path, index=False, sheet_name=sheet)
    wb = load_workbook(path)
    ws = wb.active
    ws.freeze_panes = "A2"
    _autofit(ws, cap=cap)
    wb.save(path)


# ───────────────────────────── password protection ─────────────────────────────
def protect_xlsx(path, password: str) -> None:
    """Password-protect a workbook in place (msoffcrypto-tool; Excel itself is the fallback on Windows)."""
    if not password:
        raise CalcError("no password to protect the files with - set it in Credentials")
    path = Path(path)
    last = None
    try:
        import msoffcrypto
        tmp = path.with_suffix(".protected.tmp")
        with open(path, "rb") as f, open(tmp, "wb") as out:
            office = msoffcrypto.OfficeFile(f)
            office.encrypt(password, out)
        os.replace(tmp, path)
        return
    except ImportError:
        pass
    except Exception as e:                                       # noqa: BLE001  (old msoffcrypto without encrypt support)
        try:
            tmp.unlink()
        except Exception:                                        # noqa: BLE001
            pass
        last = e
    try:
        import win32com.client as win32
    except ImportError:
        raise CalcError(f"cannot password-protect {path.name}: install msoffcrypto-tool (or Excel + pywin32)" + (f" [{last}]" if last else ""))
    excel = win32.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        wb = excel.Workbooks.Open(str(path.resolve()))
        wb.SaveAs(Filename=str(path.resolve()), FileFormat=51, Password=password)
        wb.Close(False)
    finally:
        excel.Quit()


# ───────────────────────────── the whole calculation ─────────────────────────────
def build_reports(folder, var: str, d1: str, d2: str, enterprise_passwords, abv_passwords, top_n=10, log=print) -> dict:
    """Both report workbooks (without the screener sheet). Returns {'files': {kind: Path}, 'symbols': {kind: [...]}, 'counts': {...}}."""
    folder = Path(folder)
    v = variant(var)
    conc_path = find_file(folder, f"FULLFILE_CONCENTRATION_{d1}")
    abv_path = find_file(folder, "Concentration_abv20percentage")
    paths = {k: find_file(folder, input_name(var, k, d2)) for k in ("without", "with")}
    missing = [n for n, p in (("FULLFILE_CONCENTRATION", conc_path), ("Concentration_abv20percentage", abv_path),
                              (input_name(var, "without", d2), paths["without"]), (input_name(var, "with", d2), paths["with"])) if p is None]
    if missing:
        raise CalcError("missing file(s): " + ", ".join(missing))
    log("reading the concentration files…")
    mtf = prepare_concentration(read_other(conc_path, []))
    abv = prepare_abv20(read_other(abv_path, abv_passwords))
    out = {"files": {}, "symbols": {}, "counts": {}}
    for kind in ("without", "with"):
        log(f"reading {paths[kind].name}")
        risk = read_enterprise(paths[kind], enterprise_passwords)
        syms = pick_symbols(risk, top_n)
        log(f"{kind} margin: {len(syms)} symbol(s)")
        r = ordered_filter(risk.assign(SYMBOL=risk["SYMBOL"].astype(str).str.strip()), "SYMBOL", syms)
        m = ordered_filter(mtf, "ScripName", syms)
        a = ordered_filter(abv, "symbol", syms)
        target = folder / output_name(var, kind, d1)
        write_report(target, r, m, a, v["sheet"][kind])
        out["files"][kind], out["symbols"][kind] = target, syms
        out["counts"][kind] = {"symbols": len(syms), "risk": len(r), "mtf": len(m), "abv20": len(a)}
    return out


def add_screener(folder, var: str, files: dict, session, sleep=time.sleep, log=print, check=lambda: None, pause=2.0) -> dict:
    """SCREENER sheet in both reports + the combined screener file. Returns {'master': Path, 'symbols': n}."""
    v = variant(var)
    cache, master = {}, []
    for kind in ("without", "with"):
        df = pd.read_excel(files[kind], sheet_name=v["sheet"][kind])
        syms = df["SYMBOL"].dropna().astype(str).str.strip().unique().tolist() if "SYMBOL" in df.columns else []
        rows = screener_rows(session, syms, sleep=sleep, log=log, check=check, pause=pause, cache=cache)
        add_screener_sheet(files[kind], rows)
        master += rows
    path = Path(folder) / v["screener_file"]
    write_screener_file(path, master)
    return {"master": path, "symbols": len(cache)}


def one_symbol_file(path, symbol: str, session, sleep=time.sleep, log=print, check=lambda: None) -> Path:
    """The on-demand screener: one company -> <SYMBOL>_SCREENER.xlsx (sheet SCREENER)."""
    from openpyxl import load_workbook
    row = scrape_symbol(session, symbol, sleep=sleep, log=log, check=check)
    ordered = {c: row.get(c, "") for c in SCREENER_COLUMNS}
    ordered.update({k: v for k, v in row.items() if k not in ordered})
    path = Path(path)
    pd.DataFrame([ordered]).to_excel(path, sheet_name="SCREENER", index=False)
    wb = load_workbook(path)
    ws = wb["SCREENER"]
    ws.freeze_panes = "A2"
    _autofit(ws)
    wb.save(path)
    return path


def valid_symbol(s: str) -> str:
    """Upper-case NSE symbol, letters / digits / & - _ only (it goes into a web address and a file name)."""
    s = (s or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9&\-_]{0,24}", s):
        raise CalcError("enter a company symbol like TCS, M&M or BAJAJ-AUTO")
    return s
