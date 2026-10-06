"""
core/pipelines/reconciliation.py — the RECONCILIATION box (4 daily processes).

    HOLDING            yesterday's holding + yesterday's NSE trades  vs  today's holding
    T1 HOLDING         T1 holdings (Kambala/Symphony/Noren) vs NSE SEC_PLEDGE + MCX SearchResults
    NFO BFO MTFS       PositionBook vs Kambala FO / BSE-F / cash position files
    KAMBALA POSITION   MCX part of PositionBook vs Kambala MCX position file

The four reconciliation scripts themselves (HOLDING_final.py, T1_HOLDING_final.py,
FILES CHECKING POSITIONBOOK_final.py, KAMBALA MCX CHECKING KAMBALA_final.py) and
search_results.py are the desk's own scripts and are NEVER edited. They live in
RECO_CODES_DIR (default <repo>\\recon_codes). For every run this module writes a
patched COPY into <RECO_FOLDER>\\_runtime (folders, e-mail To/CC, sender login, holiday
list location and — for search_results.py — the desktop pop-ups swapped for the
dashboard's RSA-token pop-up), then runs that copy exactly like the desk did by hand.

What this module adds around the scripts
    * STRICT pre-check: every required file must be present, otherwise nothing runs,
      nothing is downloaded and nothing is e-mailed.
    * HOLDING / RISK_REPORT live in COMMON FILES (uploaded there from the console);
      "previous HOLDING" is read from COMMON FILES\\BACKUP FILES AUTOMATED\\<date>.
      They are never copied into the reconciliation backups.
    * EXCEPTION file is kept once in <RECO_FOLDER>\\EXCEPTION and is never deleted
      by any backup; replacing it archives the old one.
    * Holidays: holidays_list.csv (editable from the console, 📅 Holidays). If NSE has no
      Trade file for a day AND we have no HOLDING for that day, the run STOPS with a plain
      warning ("looks like a holiday — add the date in 📅 Holidays and run again"). Holidays
      are never added automatically; the operator confirms.
    * FTP (Kambala/Symphony/Noren files) + NSE (C/F SEC_PLEDGE) downloads, MCX
      SearchResults via the dashboard RSA-token pop-up.
    * Each process runs once per day (state file); "Run all" skips what is already done.
"""
import base64
import csv
import glob
import gzip
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

DEFAULT_EMAIL = "kartavya.dhillon@mastertrust.co.in"

PROCS = ["HOLDING", "T1 HOLDING", "NFO BFO MTFS", "KAMBALA POSITION"]
PKEY = {"HOLDING": "HOLDING", "T1 HOLDING": "T1", "NFO BFO MTFS": "NFO", "KAMBALA POSITION": "KAMB"}
KEY2PROC = {v: k for k, v in PKEY.items()}

SCRIPT_NAMES = {
    "HOLDING": "HOLDING_final.py",
    "T1 HOLDING": "T1_HOLDING_final.py",
    "NFO BFO MTFS": "FILES CHECKING POSITIONBOOK_final.py",
    "KAMBALA POSITION": "KAMBALA MCX CHECKING KAMBALA_final.py",
}
SEARCH_SCRIPT_NAME = "search_results.py"

REQUIRED_PB_COLS = ["Exch", "ClientID", "Symbol", "ExpDate", "StrikePrice", "OpType",
                    "CFBuyQty", "CFSellQty", "NetQty"]
FAIL_MARKERS = ["Traceback (most recent call last)", "Program stopped", "PROGRAM STOPPED"]
EMAIL_FAIL_MARKERS = ["Failed to send email", "Email failed"]
KEEP_EXT = (".py", ".bat")

PROCESS_TIMEOUT_MIN = int(os.getenv("RECO_PROCESS_TIMEOUT_MIN", "30"))
SEARCH_TIMEOUT_MIN = int(os.getenv("RECO_SEARCH_TIMEOUT_MIN", "40"))
MAX_LOOKBACK_DAYS = 10

# FTP: key -> (folder, file template, group, required)
FTP_FILES = {
    "T1_SYMPHONY": ("/TRADING/CTCL_FILES/KORP_LIVE/SYMF",       "T1_HOLDING_SYMPHONY_{d}.TXT", "T1",   True),
    "T1_NOREN":    ("/TRADING/CTCL_FILES/KORP_LIVE/NOREN/HOLD", "T1_HOLDING_NOREN_{d}.TXT",    "T1",   False),
    "MF_NOREN":    ("/TRADING/CTCL_FILES/KORP_LIVE/NOREN/HOLD", "MF_NOREN_HOLDING_{d}.TXT",    "T1",   False),
    "T1_KAMBALA":  ("/TRADING/CTCL_FILES/KORP_LIVE/KAMB/HOLD",  "T1_HOLDING_KAMBALA_{d}.TXT",  "T1",   True),
    "MF_KAMB":     ("/TRADING/CTCL_FILES/KORP_LIVE/KAMB/HOLD",  "MF_KAMB_HOLDING_{d}.TXT",     "T1",   True),
    "BSEF_POS":    ("/TRADING/CTCL_FILES/KORP_LIVE/KAMB/POSI",  "BSEF_POSITION_KAMB_{d}.txt",  "NFO",  True),
    "FO_POS":      ("/TRADING/CTCL_FILES/KORP_LIVE/KAMB/POSI",  "fo_position_kambala_{d}.txt", "NFO",  True),
    "CASH_POS":    ("/TRADING/CTCL_FILES/KORP_LIVE/KAMB/POSI",  "KAMB_CASH_POS_NRML_{d}.txt",  "NFO",  True),
    "MCX_POS":     ("/TRADING/CTCL_FILES/KORP_LIVE/KAMB/POSI",  "MCX_POSITION_56720_{d}.csv",  "KAMB", True),
}


class RecoStop(Exception):
    """Raised when the run must stop (nothing has been e-mailed) — message is plain English."""


# ─────────────────────────────────────────────────────────────────────────────
# Folders / settings
# ─────────────────────────────────────────────────────────────────────────────
def log(msg=""):
    print(msg, flush=True)


def reco_base():
    v = os.getenv("RECO_FOLDER", "").strip()
    if v:
        return Path(v)
    sc = os.getenv("SA_COMMON_DIR", "").strip()
    if sc:      # sit next to COMMON FILES, e.g. D:\CONSOLE\data\COMMON FILES -> D:\CONSOLE\data\RECONCILIATION
        return Path(sc).parent / "RECONCILIATION"
    return _REPO / "data" / "RECONCILIATION"


def common_dir():
    return Path(os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES"))


def common_backup_root():
    return common_dir() / "BACKUP FILES AUTOMATED"


def codes_dir():
    return Path(os.getenv("RECO_CODES_DIR", str(_REPO / "recon_codes")))


def work_dirs():
    b = reco_base()
    return {
        "HOLDING": b / "HOLDING",
        "T1 HOLDING": b / "T1 HOLDING",
        "NFO BFO MTFS": b / "FILES CHECKING POSITIONBOOK",
        "KAMBALA POSITION": b / "KAMBALA MCX CHECKING" / "Kambala",
    }


def uploads_dir():
    return reco_base() / "UPLOADS"          # PositionBook.xlsx / SearchResults.xlsx (today only)


def exception_dir():
    return reco_base() / "EXCEPTION"        # kept permanently — never part of any backup/cleanup


def holidays_file():
    return reco_base() / "holidays_list.csv"


def state_dir():
    return reco_base() / "state"


def runtime_dir():
    return reco_base() / "_runtime"


def downloads_dir():
    return reco_base() / "DOWNLOADS"


def leftovers_dir():
    return reco_base() / "_leftovers"


def backup_root():
    return reco_base() / "Backup"


def ensure_dirs():
    for d in [reco_base(), uploads_dir(), exception_dir(), state_dir(), runtime_dir(),
              downloads_dir(), leftovers_dir(), backup_root()] + list(work_dirs().values()):
        d.mkdir(parents=True, exist_ok=True)
    try:                                   # empty list on day one → the scripts stop warning "file not found"
        if not holidays_file().exists():
            holidays_file().write_text("Date,Note\n", encoding="utf-8")
    except Exception:
        pass


def emails_for(proc):
    k = PKEY[proc]

    def _l(name):
        v = os.getenv(name)
        v = DEFAULT_EMAIL if v is None else v
        return [e.strip() for e in v.split(",") if e.strip()]
    return _l(f"RECO_{k}_TO"), _l(f"RECO_{k}_CC")


def dmy(d):
    return d.strftime("%d%m%Y")


def nice(d):
    return d.strftime("%d-%m-%Y")


def parse_dmy(s):
    try:
        return datetime.strptime(s, "%d%m%Y").date()
    except Exception:
        return None


def script_path(name):
    d = codes_dir()
    p = d / name
    if p.exists():
        return p
    if d.is_dir():
        for f in d.iterdir():
            if f.name.lower() == name.lower():
                return f
    return None


# ─────────────────────────────────────────────────────────────────────────────
# State (which process is done today)
# ─────────────────────────────────────────────────────────────────────────────
def load_state(d):
    f = state_dir() / f"{dmy(d)}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(d, st):
    state_dir().mkdir(parents=True, exist_ok=True)
    (state_dir() / f"{dmy(d)}.json").write_text(json.dumps(st, indent=2), encoding="utf-8")


def done_keys(d=None):
    d = d or date.today()
    st = load_state(d)
    return [PKEY[p] for p in PROCS if st.get(p, {}).get("status") == "DONE"]


# ─────────────────────────────────────────────────────────────────────────────
# Holidays  (holidays_list.csv — "Date,Note", Date = DDMMYYYY; the desk's own scripts
# read column 1 of this same file)
# ─────────────────────────────────────────────────────────────────────────────
def load_holidays():
    rows = {}
    f = holidays_file()
    if not f.exists():
        return rows
    try:
        with open(f, "r", newline="", encoding="utf-8-sig") as fh:
            for r in csv.reader(fh):
                if not r:
                    continue
                raw = r[0].strip().strip('"').strip("'")
                if not raw or raw.lower() == "date":
                    continue
                if raw.isdigit() and len(raw) == 7:
                    raw = raw.zfill(8)
                d = parse_dmy(raw)
                if d:
                    rows[d] = (r[1].strip() if len(r) > 1 else "manual")
    except Exception:
        pass
    return rows


def _save_holidays(rows):
    ensure_dirs()
    with open(holidays_file(), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Note"])
        for d in sorted(rows):
            w.writerow([dmy(d), rows[d]])


def add_holiday(d, note="manual"):
    rows = load_holidays()
    rows[d] = note
    _save_holidays(rows)


def remove_holiday(d):
    rows = load_holidays()
    rows.pop(d, None)
    _save_holidays(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Shared files in COMMON FILES (HOLDING / RISK_REPORT) — never backed up here
# ─────────────────────────────────────────────────────────────────────────────
def find_holding(d):
    """HOLDING_<DDMMYYYY>*.csv for date d — in COMMON FILES, or in its dated backup."""
    pat = f"HOLDING_{dmy(d)}*.csv"
    for p in sorted(common_dir().glob(pat)):
        return p
    root = common_backup_root()
    if root.is_dir():
        try:
            for p in sorted(root.rglob(pat), reverse=True):     # any depth, any sub-folder name
                return p
        except OSError:
            pass
    return None


def latest_previous_holding(today):
    """Newest HOLDING dated before `today` (within the look-back window) — readiness only."""
    best = None
    cands = list(common_dir().glob("HOLDING_*.csv"))
    root = common_backup_root()
    if root.is_dir():
        try:
            cands += list(root.rglob("HOLDING_*.csv"))
        except OSError:
            pass
    for p in cands:
        m = re.match(r"^holding_(\d{8})", p.name.lower())
        d = parse_dmy(m.group(1)) if m else None
        if d and d < today and (today - d).days <= 20 and (best is None or d > best[0]):
            best = (d, p)
    return best


def find_risk(d):
    for p in sorted(common_dir().glob(f"RISK_REPORT_{dmy(d)}*.csv")):
        return p
    return None


def find_exception():
    d = exception_dir()
    if not d.is_dir():
        return None
    c = [p for p in d.iterdir() if p.is_file() and p.name.lower().startswith("exception")
         and p.suffix.lower() in (".xls", ".xlsx", ".xlsm")]
    return max(c, key=lambda p: p.stat().st_mtime) if c else None


_PB_CACHE = {}


def _posbook_info(p):
    """(has_required_columns, missing_cols, mcx_rows) — cached per file+mtime."""
    key = (str(p), p.stat().st_mtime, p.stat().st_size)
    if key in _PB_CACHE:
        return _PB_CACHE[key]
    from openpyxl import load_workbook
    miss, mcx = [], 0
    try:
        wb = load_workbook(str(p), read_only=True, data_only=True)
        try:
            ws = wb.active
            rows = ws.iter_rows(values_only=True)
            header = next(rows)
            hdr = [("" if v is None else str(v).strip()) for v in header]
            miss = [c for c in REQUIRED_PB_COLS if c not in hdr]
            if not miss:
                idx = [h.lower() for h in hdr].index("exch")
                for r in rows:
                    if r and len(r) > idx and r[idx] is not None and str(r[idx]).strip().upper() == "MCX":
                        mcx += 1
        finally:
            wb.close()
    except Exception as e:
        miss = [f"file could not be opened ({e})"]
    _PB_CACHE.clear()
    _PB_CACHE[key] = (not miss, miss, mcx)
    return _PB_CACHE[key]


def find_uploaded(kind):
    """kind='posbook' | 'search' — files in UPLOADS (newest first)."""
    d = uploads_dir()
    if not d.is_dir():
        return []
    out = []
    for p in d.iterdir():
        if not p.is_file() or p.name.startswith("~$") or p.suffix.lower() != ".xlsx":
            continue
        n = p.name.lower()
        if kind == "posbook" and "position" in n:
            out.append(p)
        elif kind == "search" and n.startswith("searchresults"):
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Pre-check (STRICT)
# ─────────────────────────────────────────────────────────────────────────────
def check_files(procs, today=None):
    """Readiness list for the given processes. Every item must be ok to run."""
    today = today or date.today()
    procs = list(procs)
    items = []

    def add(key, label, ok, example, file="", why="", needed=""):
        items.append({"key": key, "label": label, "ok": bool(ok), "example": example,
                      "file": file, "why": why, "needed_by": needed})

    need_hold = any(p in procs for p in ("HOLDING", "T1 HOLDING"))
    if need_hold:
        h = sorted(common_dir().glob(f"HOLDING_{dmy(today)}*.csv"))
        add("HOLDING_TODAY", "Today's HOLDING file (COMMON FILES)", bool(h),
            f"HOLDING_{dmy(today)}.csv", h[0].name if h else "",
            "" if h else "upload it with ⬆️ Upload files", "HOLDING, T1 HOLDING")
    if "HOLDING" in procs:
        prev = latest_previous_holding(today)
        add("HOLDING_PREV", "Previous trading day's HOLDING (COMMON FILES backup)", bool(prev),
            "HOLDING_<previous day>.csv", prev[1].name if prev else "",
            "" if prev else "first day only: upload yesterday's HOLDING too — afterwards it is picked up automatically",
            "HOLDING")
    r = find_risk(today)
    add("RISK_REPORT", "Today's RISK_REPORT (COMMON FILES)", bool(r),
        f"RISK_REPORT_{dmy(today)}.csv", r.name if r else "",
        "" if r else "upload it with ⬆️ Upload files", "all four")
    if "T1 HOLDING" in procs:
        e = find_exception()
        add("EXCEPTION", "EXCEPTION file (kept once)", bool(e), "EXCEPTION_<date>.xlsx",
            e.name if e else "", "" if e else "upload once with ⬆️ Upload files — it stays", "T1 HOLDING")
    if any(p in procs for p in ("NFO BFO MTFS", "KAMBALA POSITION")):
        pbs = find_uploaded("posbook")
        ok, why, fn = False, "", ""
        if not pbs:
            why = "upload today's PositionBook.xlsx (exported WITH MCX included)"
        elif len(pbs) > 1:
            why = f"{len(pbs)} PositionBook files found — keep only one"
        else:
            p = pbs[0]
            fn = p.name
            mdate = datetime.fromtimestamp(p.stat().st_mtime).date()
            good, miss, mcx = _posbook_info(p)
            if mdate != today:
                why = f"this PositionBook is from {nice(mdate)} — upload today's"
            elif not good:
                why = "PositionBook is not correct — missing: " + ", ".join(miss)
            elif mcx == 0:
                why = "PositionBook has no MCX rows (export it with MCX included)"
            else:
                ok = True
        add("POSITIONBOOK", "Today's PositionBook (with MCX rows)", ok, "PositionBook.xlsx", fn, why,
            "NFO BFO MTFS, KAMBALA POSITION")
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Upload routing  (one "Upload files" button for everything)
# ─────────────────────────────────────────────────────────────────────────────
def route_uploads(paths, target=""):
    """paths = temp files saved by the API. Routes each by name and returns a result dict.
    target='common' → only HOLDING_ / RISK_REPORT_ files are accepted (COMMON FILES);
    target='main'   → only EXCEPTION / PositionBook / SearchResults files (RECONCILIATION folder);
    target=''       → anything recognised."""
    ensure_dirs()
    common_items, ok, rejected, notes = [], [], [], []
    for fp in paths:
        base = os.path.basename(fp)
        n = base.lower()
        is_common = bool(re.match(r"^(risk_report|holding)_\d{8}.*\.csv$", n))
        if target == "common" and not is_common:
            rejected.append(f"{base}: this button is for HOLDING_DDMMYYYY.csv / RISK_REPORT_DDMMYYYY.csv only")
            try:
                os.remove(fp)
            except Exception:
                pass
            continue
        if target == "main" and is_common:
            rejected.append(f"{base}: HOLDING / RISK_REPORT files go in the COMMON FILES button")
            try:
                os.remove(fp)
            except Exception:
                pass
            continue
        try:
            if re.match(r"^(risk_report|holding)_\d{8}.*\.csv$", n):
                common_items.append(fp)
            elif n.startswith("exception") and n.endswith((".xls", ".xlsx", ".xlsm")):
                old = find_exception()
                dest = exception_dir() / base
                if old:
                    arch = exception_dir() / "old"
                    arch.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.move(str(old), str(arch / f"{datetime.now():%Y%m%d_%H%M%S}_{old.name}"))
                        notes.append(f"previous EXCEPTION file ({old.name}) was archived in EXCEPTION\\old")
                    except Exception as e:
                        notes.append(f"could not archive old EXCEPTION file: {e}")
                shutil.move(fp, str(dest))
                ok.append(base)
            elif n.endswith(".xlsx") and "position" in n:
                for old in find_uploaded("posbook"):
                    try:
                        old.unlink()
                    except Exception:
                        pass
                shutil.move(fp, str(uploads_dir() / base))
                ok.append(base)
            elif n.startswith("searchresults") and n.endswith(".xlsx"):
                for old in find_uploaded("search"):
                    try:
                        old.unlink()
                    except Exception:
                        pass
                shutil.move(fp, str(uploads_dir() / base))
                ok.append(base)
            else:
                rejected.append(base)
                try:
                    os.remove(fp)
                except Exception:
                    pass
        except Exception as e:
            rejected.append(f"{base}: {e}")
    if common_items:
        from core.pipeline_box import pb_upload
        res = pb_upload("reco", common_items, "common", "")
        ok += res.get("ok", [])
        rejected += res.get("rejected", [])
    return {"ok": ok, "rejected": rejected, "notes": notes}


# ─────────────────────────────────────────────────────────────────────────────
# NSE extranet (holiday detection + C/F pledge files)
# ─────────────────────────────────────────────────────────────────────────────
NSE_LOGIN_URL = "https://www.connect2nse.com/extranet-api/login/2.0"
NSE_DOWNLOAD_URL = "https://www.connect2nse.com/extranet-api/member/file/download/2.0"


def nse_creds():
    env = {"memberCode": os.getenv("RECO_NSE_MEMBER", "").strip(),
           "loginId": os.getenv("RECO_NSE_LOGIN_ID", "").strip(),
           "password": os.getenv("RECO_NSE_PASSWORD", "").strip()}
    if all(env.values()):
        return env
    p = script_path(SCRIPT_NAMES["HOLDING"])
    txt = p.read_text(encoding="utf-8", errors="replace") if p else ""
    out = dict(env)
    for k in ("memberCode", "loginId", "password"):
        if not out[k]:
            m = re.search(r'"%s"\s*:\s*"([^"]+)"' % k, txt)
            out[k] = m.group(1) if m else ""
    if not all(out.values()):
        raise RecoStop("NSE login details not found — set them in 🔑 Credentials (NSE extranet).")
    return out


class NseClient:
    def __init__(self):
        import requests
        self.s = requests.Session()
        self.token = None
        self.creds = nse_creds()

    def login(self, attempts=10):
        last = None
        for i in range(1, attempts + 1):
            try:
                r = self.s.post(NSE_LOGIN_URL, json=self.creds, timeout=15)
                r.raise_for_status()
                tok = r.json().get("token")
                if tok:
                    self.token = tok
                    return
                last = "no token in response"
            except Exception as e:
                last = e
            log(f"   NSE login attempt {i}/{attempts} failed ({last}), retrying…")
            time.sleep(3)
        raise RecoStop(f"Could not log in to NSE extranet: {last}")

    def fetch(self, segment, filename, dest, attempts=10):
        """Path if downloaded; None if NSE has no such file (4xx / non-gzip reply)."""
        if not self.token:
            self.login()
        params = {"segment": segment, "folderPath": "Reports", "filename": filename}
        for i in range(1, attempts + 1):
            try:
                r = self.s.get(NSE_DOWNLOAD_URL, params=params,
                               headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
            except Exception as e:
                log(f"   network error ({e}), retry {i}/{attempts}")
                time.sleep(3)
                continue
            if r.status_code == 401:
                self.login()
                continue
            if r.status_code in (500, 502, 503, 504):
                log(f"   NSE busy ({r.status_code}), retry {i}/{attempts}")
                time.sleep(3)
                continue
            if 400 <= r.status_code < 500:
                return None
            if r.status_code == 200:
                if not r.content.startswith(b"\x1f\x8b"):
                    return None
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Path(dest).write_bytes(r.content)
                return Path(dest)
            time.sleep(3)
        raise RecoStop(f"NSE did not respond properly for {filename} — try again in a few minutes.")


def _gunzip(gz):
    """Extract next to the .gz. The .gz is KEPT — it is the cache the scripts reuse."""
    gz = Path(gz)
    out = gz.with_suffix("")
    with gzip.open(gz, "rb") as fi, open(out, "wb") as fo:
        fo.write(fi.read())
    return out


def nse_dir(today=None):
    return downloads_dir() / dmy(today or date.today()) / "nse"


def get_trade_day(today, nse=None):
    """Previous trading day. Remembered for the day, so the NSE probe runs only once."""
    f = downloads_dir() / dmy(today) / "trade_day.txt"
    try:
        d = parse_dmy(f.read_text(encoding="utf-8").strip())
        if d and find_holding(d):
            return d, nse
    except Exception:
        pass
    if nse is None:
        nse = NseClient()
        nse.login()
    d = resolve_trade_day(today, nse)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(dmy(d), encoding="utf-8")
    except Exception:
        pass
    return d, nse


def ensure_trade_file(trade_day, nse=None):
    """CM trade file of the trading day → NSE folder (skipped when already there)."""
    member = nse_creds()["memberCode"]
    name = trade_name(member, trade_day)
    p = nse_dir() / name
    if p.exists() and p.stat().st_size > 0:
        log(f"   ✔ already downloaded: {name[:-3]}")
        return nse
    if nse is None:
        nse = NseClient()
        nse.login()
    if not nse.fetch("CM", name, p):
        log(f"[MISSING_FILE] {name[:-3]}")
        raise RecoStop(f"NSE does not have {name[:-3]} yet — try again later.")
    log(f"   ✔ {name[:-3]}")
    return nse


def fetch_pledge_files(trade_day, nse=None):
    """C and F SEC_PLEDGE csv files. Reuses what is already in the NSE folder."""
    member = nse_creds()["memberCode"]
    out = nse_dir()
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    for prefix, seg in (("C", "CM"), ("F", "FO")):
        name = f"{prefix}_{member}_SEC_PLEDGE_{trade_day:%d%m%Y}_02.csv.gz"
        csvp = out / name[:-3]
        if csvp.exists() and csvp.stat().st_size > 0:
            res[prefix] = csvp
            log(f"   ✔ already downloaded: {csvp.name}")
            continue
        if nse is None:
            nse = NseClient()
            nse.login()
        gz = nse.fetch(seg, name, out / name)
        if not gz:
            log(f"[MISSING_FILE] {name[:-3]}")
            raise RecoStop(f"NSE does not have {name[:-3]} yet — try again later.")
        res[prefix] = _gunzip(gz)
        log(f"   ✔ {res[prefix].name}")
    return res, nse


def trade_name(member, d):
    return f"Trade_NCL_CM_0_CM_{member}_{d:%Y%m%d}_F_0000.csv.gz"


def resolve_trade_day(today, nse):
    """Previous trading day, found by asking NSE and cross-checking our own HOLDING archive.
    Auto-adds genuine holidays to holidays_list.csv. Raises RecoStop with a plain message."""
    member = nse.creds["memberCode"]
    hol = load_holidays()
    d = today - timedelta(days=1)
    tried = 0
    suspects = []          # days with neither an NSE trade file nor our HOLDING — probably holidays
    tmp = nse_dir(today)        # the trade file found here is KEPT (the HOLDING script reuses it)

    def _stop_for_suspects():
        names = ", ".join(f"{x:%A %d-%m-%Y}" for x in suspects)
        iso = ", ".join(x.isoformat() for x in suspects)
        raise RecoStop(
            f"No NSE trade file and no HOLDING file found for {names}. This looks like a market HOLIDAY, "
            f"so nothing was run. If it was a holiday, add the date in 📅 Holidays and run again "
            f"(dates to add: {iso}). If it was a normal trading day, upload that day's HOLDING file instead.")

    while tried < MAX_LOOKBACK_DAYS:
        if d.weekday() >= 5 or d in hol:
            d -= timedelta(days=1)
            continue
        tried += 1
        _tp = tmp / trade_name(member, d)
        if _tp.exists() and _tp.stat().st_size > 0:
            gz = _tp
        else:
            gz = nse.fetch("CM", trade_name(member, d), _tp)
        has_trade = bool(gz)
        has_hold = find_holding(d) is not None
        if has_trade and has_hold:
            if suspects:
                _stop_for_suspects()
            return d
        if has_trade and not has_hold:
            raise RecoStop(
                f"NSE has the trade file for {nice(d)}, but HOLDING_{dmy(d)}.csv is not in COMMON FILES "
                f"(or its backup). Upload that day's HOLDING file and run again.")
        if (not has_trade) and has_hold:
            raise RecoStop(
                f"NSE has no trade file for {nice(d)} yet, but we do have that day's HOLDING. "
                f"The NSE file may be late — NOT treating {nice(d)} as a holiday. Try again later.")
        # neither → probably a market holiday. NOT added automatically — the operator confirms.
        log(f"   ⚠ {d:%A %d-%m-%Y}: no NSE trade file and no HOLDING file — could be a holiday.")
        suspects.append(d)
        d -= timedelta(days=1)
    if suspects:
        _stop_for_suspects()
    raise RecoStop(f"No trading day found in the last {MAX_LOOKBACK_DAYS} days — check the holiday list.")


# ─────────────────────────────────────────────────────────────────────────────
# FTP
# ─────────────────────────────────────────────────────────────────────────────
def ftp_download(day, dest_dir, groups):
    from ftplib import FTP
    host = os.getenv("FTP_HOST", "").strip()
    user = os.getenv("FTP_USER", "").strip()
    pw = os.getenv("FTP_PASS", "")
    if not (host and user):
        raise RecoStop("FTP login is not set (FTP_HOST / FTP_USER / FTP_PASS in .env — same FTP the other boxes use).")
    d = day.strftime("%d%m%Y")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    wanted = {k: v for k, v in FTP_FILES.items() if v[2] in groups}
    result = {k: None for k in wanted}
    # already downloaded today (e.g. with the FTP button) → reuse, don't download again
    existing = {p.name.lower(): p for p in dest_dir.iterdir() if p.is_file() and p.stat().st_size > 0}
    for k, v in wanted.items():
        _fn = v[1].format(d=d).lower()
        if _fn in existing:
            result[k] = existing[_fn]
            log(f"   ✔ already downloaded: {existing[_fn].name}")
    wanted = {k: v for k, v in wanted.items() if result[k] is None}
    if not wanted:
        return result
    ftp, last = None, None
    for i in range(1, 4):
        try:
            ftp = FTP(host, timeout=60)
            ftp.login(user, pw)
            break
        except Exception as e:
            last = e
            log(f"   FTP connect attempt {i} failed: {e}")
            time.sleep(5)
    if ftp is None:
        raise RecoStop(f"Could not connect to the FTP server: {last}")
    cache = {}
    try:
        for key, (folder, tmpl, _g, required) in wanted.items():
            fname = tmpl.format(d=d)
            try:
                if folder not in cache:
                    ftp.cwd(folder)
                    cache[folder] = ftp.nlst()
                else:
                    ftp.cwd(folder)
                remote = cache[folder]
            except Exception as e:
                log(f"   ✘ FTP folder not reachable: {folder} ({e})")
                continue
            match = next((rf for rf in remote if rf.lower() == fname.lower()), None)
            if not match:
                log(f"   {'✘ MISSING' if required else '• optional, not on FTP'}: {fname}")
                continue
            local = dest_dir / match
            try:
                with open(local, "wb") as f:
                    ftp.retrbinary("RETR " + match, f.write)
                if local.stat().st_size == 0:
                    log(f"   ✘ EMPTY file on FTP: {match}")
                    local.unlink(missing_ok=True)
                    continue
                result[key] = local
                log(f"   ✔ {match}")
            except Exception as e:
                log(f"   ✘ Download error {match}: {e}")
                local.unlink(missing_ok=True)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Script patching (the original scripts are never edited — a COPY is patched)
# ─────────────────────────────────────────────────────────────────────────────
def _pylit(v):
    return repr(v)


def patch_script(proc, src, dest):
    txt = Path(src).read_text(encoding="utf-8", errors="replace")
    wd = work_dirs()
    b = reco_base()
    # 1) folders
    if proc == "HOLDING":
        rep = {r"C:\T1 HOLDING": str(b / "HOLDING_DOWNLOADS"),     # NSE C/F downloads of this script
               r"C:\HOLDING": str(wd["HOLDING"])}
    elif proc == "T1 HOLDING":
        rep = {r"C:\T1 HOLDING": str(wd["T1 HOLDING"])}
    elif proc == "NFO BFO MTFS":
        rep = {r"C:\FILES CHECKING POSITIONBOOK": str(wd["NFO BFO MTFS"])}
    else:
        rep = {r"C:\KAMBALA MCX CHECKING\Kambala": str(wd["KAMBALA POSITION"])}
    for a, c in rep.items():
        txt = txt.replace(a, c)
    # T1 copies RISK_REPORT to folders of the desk's old PC — not wanted on the console
    txt = re.sub(r"^RISK_COPY_PATHS\s*=\s*\[.*?\]", "RISK_COPY_PATHS = []", txt, flags=re.S | re.M)
    # 2) holiday list location (HOLDING + T1 scripts)
    _hf = "HOLIDAYS_FILE = " + _pylit(str(holidays_file()))      # lambda: backslashes must not be read as escapes
    txt = re.sub(r"^HOLIDAYS_FILE\s*=.*$", lambda m: _hf, txt, flags=re.M)
    # 3) e-mail To / CC
    to, cc = emails_for(proc)
    txt = re.sub(r"^(\s*)(TO_LIST|to_emails)\s*=\s*\[[^\]]*\]",
                 lambda m: f"{m.group(1)}{m.group(2)} = {_pylit(to)}", txt, flags=re.M)
    txt = re.sub(r"^(\s*)(CC_LIST|cc_emails)\s*=\s*\[[^\]]*\]",
                 lambda m: f"{m.group(1)}{m.group(2)} = {_pylit(cc)}", txt, flags=re.M)
    # 4) sender login (only when set in Credentials; otherwise the script's own stays)
    se, sp = os.getenv("SENDER_EMAIL", "").strip(), os.getenv("SENDER_PASSWORD", "")
    if se and sp:
        txt = re.sub(r"^(\s*)(SMTP_USER|sender_email)\s*=\s*([\"']).*?\3",
                     lambda m: f"{m.group(1)}{m.group(2)} = {_pylit(se)}", txt, flags=re.M)
        txt = re.sub(r"^(\s*)(SMTP_PASS|password)\s*=\s*([\"']).*?\3",
                     lambda m: f"{m.group(1)}{m.group(2)} = {_pylit(sp)}", txt, flags=re.M)
    # 5) NSE login of the HOLDING script's own downloader
    if proc == "HOLDING":
        for k, envk in (("memberCode", "RECO_NSE_MEMBER"), ("loginId", "RECO_NSE_LOGIN_ID"),
                        ("password", "RECO_NSE_PASSWORD")):
            v = os.getenv(envk, "").strip()
            if v:
                txt = re.sub(r'("%s"\s*:\s*)"[^"]*"' % k, lambda m: m.group(1) + json.dumps(v), txt, count=1)
    # 6) HOLDING script: reuse NSE files the console already downloaded today (no second download)
    if proc == "HOLDING":
        cache = _pylit(str(nse_dir()))
        a = "    local_gz = os.path.join(INPUT_FOLDER, filename)\n"
        if a in txt:
            txt = txt.replace(a, a +
                f"    _cg = os.path.join({cache}, filename)\n"
                "    if os.path.exists(_cg) and os.path.getsize(_cg) > 0:\n"
                "        import shutil as _shc\n"
                "        os.makedirs(INPUT_FOLDER, exist_ok=True)\n"
                "        _shc.copy2(_cg, local_gz)\n"
                "        print(f'✅ Already downloaded today — using {_cg}')\n"
                "        return local_gz, token\n", 1)
        b = '            print(f"✅ Downloaded → {local_gz}")\n'
        if b in txt:
            txt = txt.replace(b, b +
                "            try:\n"
                "                import shutil as _shc2\n"
                f"                os.makedirs({cache}, exist_ok=True)\n"
                f"                _shc2.copy2(local_gz, os.path.join({cache}, filename))\n"
                "            except Exception:\n"
                "                pass\n", 1)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_text(txt, encoding="utf-8")
    return Path(dest)


_SEARCH_OVERRIDES = '''
# ───── console overrides (desktop windows replaced by the dashboard's RSA-token pop-up) ─────
def get_token_popup(attempt, max_attempts, error_msg=None):
    import json as _tj, os as _tos, time as _tt
    _dir = _tos.getenv("SA_OUTPUT_DIR", r"C:\\SHORT ALLOCATION")
    _tos.makedirs(_dir, exist_ok=True)
    _sf = _tos.path.join(_dir, ".token_state")
    _af = _tos.path.join(_dir, ".token_answer")
    for _f in (_sf, _af):
        try: _tos.remove(_f)
        except OSError: pass
    _tj.dump({"state": "PENDING", "attempt": attempt, "max_attempts": max_attempts or 3,
              "error_msg": error_msg, "label": "MCX SearchResults (Reconciliation)", "caller": "reco"},
             open(_sf, "w", encoding="utf-8"))
    print("🔐 Token required — enter RSA SecurID token in the dashboard "
          "(Attempt %s) …" % attempt, flush=True)
    _end = _tt.time() + 180
    while _tt.time() < _end:
        _tt.sleep(1)
        try:
            _st = _tj.load(open(_sf, encoding="utf-8"))
            if _st.get("state") == "ANSWERED":
                _a = open(_af, encoding="utf-8").read().strip()
                for _f in (_sf, _af):
                    try: _tos.remove(_f)
                    except OSError: pass
                print("✅ Token received via dashboard.", flush=True)
                return _a
        except Exception:
            pass
    for _f in (_sf, _af):
        try: _tos.remove(_f)
        except OSError: pass
    raise RuntimeError("Token entry timed out (180 s) — no answer from dashboard.")

def get_password_popup(attempt, max_attempts, error_msg=None):
    print("[MCX_DEPOSIT_AUTH_FAIL] MCX password rejected — update 'MCX RSK136 login' in 🔑 Credentials", flush=True)
    raise RuntimeError("MCX password rejected — update it in 🔑 Credentials (MCX RSK136 login).")

def session_expired_popup():
    print("⚠️ MCX session expired — re-authenticating …", flush=True)

def update_password_in_file(new_password):
    pass
# ───── end overrides ─────
'''


def patch_search_script(src, dest, save_dir):
    txt = Path(src).read_text(encoding="utf-8", errors="replace")
    _sf = "SEARCH_FOLDER = " + _pylit(str(save_dir))
    txt = re.sub(r"^SEARCH_FOLDER\s*=.*$", lambda m: _sf, txt, flags=re.M)
    u, pw = os.getenv("RECO_MCX_USER", "").strip(), os.getenv("RECO_MCX_PASSWORD", "")
    if u:
        _u = "USER_ID = " + _pylit(u)
        txt = re.sub(r"^USER_ID\s*=.*$", lambda m: _u, txt, flags=re.M)
    if pw:
        _p = "PASSWORD = " + _pylit(pw)
        txt = re.sub(r"^PASSWORD\s*=.*$", lambda m: _p, txt, flags=re.M)
    m = re.search(r"^session = requests\.Session\(\)", txt, flags=re.M)
    if not m:
        raise RecoStop("search_results.py has a different layout than expected — cannot patch it.")
    txt = txt[:m.start()] + _SEARCH_OVERRIDES + "\n" + txt[m.start():]
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_text(txt, encoding="utf-8")
    return Path(dest)


# ─────────────────────────────────────────────────────────────────────────────
# Running a script (live output, timeout)
# ─────────────────────────────────────────────────────────────────────────────
def run_script(script, cwd, timeout_min):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    p = subprocess.Popen([script_python(), "-u", str(script)], cwd=str(cwd),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    q = queue.Queue()

    def reader():
        for raw in iter(p.stdout.readline, b""):
            q.put(raw)
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    lines, timed_out = [], False
    deadline = time.time() + timeout_min * 60
    while True:
        try:
            raw = q.get(timeout=1)
        except queue.Empty:
            if time.time() > deadline:
                timed_out = True
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                except Exception:
                    p.kill()
                break
            continue
        if raw is None:
            break
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        lines.append(line)
        log(f"   │ {line}")
    try:
        p.wait(timeout=30)
    except Exception:
        pass
    return p.returncode, lines, timed_out


def judge(rc, lines, timed_out, timeout_min):
    text = "\n".join(lines)
    if timed_out:
        return "FAILED", f"stopped - still running after {timeout_min} minutes"
    if rc != 0 or any(m in text for m in FAIL_MARKERS):
        last = next((l.strip() for l in reversed(lines) if l.strip()), "")
        return "FAILED", last[:200]
    if any(m in text for m in EMAIL_FAIL_MARKERS):
        return "EMAIL FAILED", "report was created but the email was NOT sent"
    return "DONE", ""


# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────
def copy_to(src, dest_dir, name=None, age_seconds=None):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (name or Path(src).name)
    shutil.copy2(src, dest)
    if age_seconds is not None:
        t = time.time() - age_seconds
        os.utime(dest, (t, t))
    return dest


def park_leftovers(folder, tag, stamp):
    """Move every file (except .py/.bat) out of a work folder so a script can never pick an
    old or duplicate file. (Parked copies are purged after 45 days.)"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for p in folder.iterdir():
        if not p.is_file() or p.suffix.lower() in KEEP_EXT or p.name.lower() == "desktop.ini":
            continue
        dest_dir = leftovers_dir() / stamp / tag
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / p.name
        if dest.exists():
            dest = dest_dir / f"{p.stem}_{int(time.time() * 1000)}{p.suffix}"
        try:
            shutil.move(str(p), str(dest))
        except Exception as e:
            log(f"   ⚠ Could not move old file {p.name}: {e}")


def purge_old(days=45):
    limit = time.time() - days * 86400
    for base in (downloads_dir(), leftovers_dir()):
        if not base.exists():
            continue
        for p in base.iterdir():
            try:
                if p.stat().st_mtime < limit:
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
            except Exception:
                pass


_SHARED_PAT = re.compile(r"^(holding_|risk_report_|exception)", re.I)


def collect_backup(proc, t0, today):
    """After a process: HOLDING / RISK_REPORT / EXCEPTION copies are removed from the script's own
    backup folder (they are kept once, elsewhere); the remaining output files are copied into
    <RECO_FOLDER>\\Backup\\<date>\\<process>\\ (and archived in the database)."""
    work = work_dirs()[proc]
    broot = work / "BACKUP FILES AUTOMATED"
    if not broot.is_dir():
        return
    dest = backup_root() / nice(today) / proc
    dest.mkdir(parents=True, exist_ok=True)
    for sub in broot.iterdir():
        try:
            if not sub.is_dir() or sub.stat().st_mtime < t0 - 60:
                continue
        except Exception:
            continue
        for f in list(sub.iterdir()):
            if not f.is_file():
                continue
            if _SHARED_PAT.match(f.name):
                try:
                    f.unlink()
                except Exception:
                    pass
                continue
            try:
                shutil.copy2(f, dest / f.name)
            except Exception:
                continue
            try:
                use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
                mod = __import__("core.db_postgres" if use_pg else "core.db", fromlist=["save_run_file"])
                if f.stat().st_size < 40 * 1024 * 1024:
                    mod.save_run_file("reco", "backup", f.name, f.read_bytes(), tag=proc)
            except Exception:
                pass


def script_python():
    """Interpreter used to run the reconciliation scripts (they need pandas 2.x).
    RECO_PYTHON in .env, else <RECO_CODES>\\.venv, else the console's own Python."""
    p = os.getenv("RECO_PYTHON", "").strip().strip('"')
    if p and Path(p).exists():
        return p
    v = codes_dir() / ".venv" / "Scripts" / "python.exe"
    if v.exists():
        return str(v)
    return sys.executable


_PKG_CHECK = r'''
import sys
miss = []
for mod, pipn in [("requests","requests"),("pandas","pandas"),("polars","polars"),("openpyxl","openpyxl"),
                  ("xlsxwriter","xlsxwriter"),("Crypto","pycryptodome"),("bs4","beautifulsoup4"),
                  ("pyarrow","pyarrow"),("xlrd","xlrd")]:
    try:
        __import__(mod)
    except Exception:
        miss.append(pipn)
try:
    import pandas as pd
    if int(pd.__version__.split(".")[0]) >= 3:
        miss.append("pandas 2.x (found " + pd.__version__ + ")")
except Exception:
    pass
print("MISSING:" + ",".join(miss))
'''


def strip_shared_from_script_backups(proc):
    """HOLDING_ / RISK_REPORT_ files are backed up once, by COMMON FILES. The desk's scripts also
    move them into their own 'BACKUP FILES AUTOMATED\\<date>' folder — remove those copies."""
    root = work_dirs()[proc] / "BACKUP FILES AUTOMATED"
    if not root.is_dir():
        return
    pat = re.compile(r"^(holding_|risk_report_)", re.I)
    n = 0
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        if datetime.fromtimestamp(sub.stat().st_mtime).date() != date.today():
            continue
        for f in sub.rglob("*"):
            if f.is_file() and pat.match(f.name):
                try:
                    f.unlink()
                    n += 1
                except Exception:
                    pass
    if n:
        log(f"   (HOLDING / RISK_REPORT removed from this folder's backup — COMMON FILES keeps them: {n})")


def set_current(key, phase, pending=None):
    """Live progress for the dashboard tiles."""
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        (state_dir() / "current.json").write_text(json.dumps({
            "date": date.today().isoformat(), "proc": key, "phase": phase,
            "pending": [PKEY[p] for p in (pending or [])]}), encoding="utf-8")
    except Exception:
        pass


def get_current():
    try:
        d = json.loads((state_dir() / "current.json").read_text(encoding="utf-8"))
        return d if d.get("date") == date.today().isoformat() else None
    except Exception:
        return None


def clear_current():
    try:
        (state_dir() / "current.json").unlink()
    except Exception:
        pass


def _missing_pkgs():
    py = script_python()
    try:
        r = subprocess.run([py, "-c", _PKG_CHECK], capture_output=True, text=True, timeout=120)
        line = [l for l in r.stdout.splitlines() if l.startswith("MISSING:")]
        miss = [x for x in (line[-1][8:].split(",") if line else []) if x]
    except Exception as e:
        return [f"could not start Python ({py}): {e}"]
    if miss and any("pandas 2.x" in m for m in miss):
        miss.append("-> run recon_codes\\setup_reco_python.bat once, then set RECO_PYTHON in .env")
    return miss


def make_mcx_positionbook(src, dest):
    from openpyxl import load_workbook, Workbook
    wb = load_workbook(src, read_only=True, data_only=True)
    out = Workbook(write_only=True)
    try:
        ws = wb.active
        ows = out.create_sheet(ws.title or "Sheet")
        rows = ws.iter_rows(values_only=True)
        header = next(rows)
        idx = [str(h).strip().lower() if h is not None else "" for h in header].index("exch")
        ows.append(list(header))
        n = 0
        for r in rows:
            if r and len(r) > idx and r[idx] is not None and str(r[idx]).strip().upper() == "MCX":
                ows.append(list(r))
                n += 1
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        out.save(dest)
        return n
    finally:
        wb.close()


def search_rows(path):
    import pandas as pd
    df = pd.read_excel(path, header=None, dtype=str)
    for i in range(min(30, len(df))):
        if "UCC CODE" in [str(x).upper().strip() for x in df.iloc[i].tolist()]:
            return len(df) - i - 1
    return 0


def fetch_search_results(t1_dir, stamp):
    """MCX SearchResults.xlsx for T1 HOLDING — a SearchResults file uploaded today skips the login."""
    up = find_uploaded("search")
    if up:
        p = up[0]
        if datetime.fromtimestamp(p.stat().st_mtime).date() == date.today():
            try:
                n = search_rows(p)
            except Exception:
                n = 0
            if n > 0:
                copy_to(p, t1_dir, "SearchResults.xlsx")
                log(f"   ✔ Using the SearchResults you uploaded ({n} rows) — MCX login skipped")
                return
    cached = downloads_dir() / dmy(date.today()) / "bancs" / "SearchResults.xlsx"
    if cached.exists() and datetime.fromtimestamp(cached.stat().st_mtime).date() == date.today():
        try:
            n = search_rows(cached)
        except Exception:
            n = 0
        if n > 0:
            copy_to(cached, t1_dir, "SearchResults.xlsx")
            log(f"   ✔ Using today's already-downloaded SearchResults ({n} rows) — MCX login skipped")
            return
    sr = download_search()
    copy_to(sr, t1_dir, "SearchResults.xlsx")


def download_search():
    """Download MCX SearchResults.xlsx (RSA token) into DOWNLOADS\\<ddmmyyyy>\\. Returns the file path."""
    src = script_path(SEARCH_SCRIPT_NAME)
    if not src:
        raise RecoStop(f"{SEARCH_SCRIPT_NAME} not found in {codes_dir()}")
    save_dir = downloads_dir() / dmy(date.today()) / "bancs"
    save_dir.mkdir(parents=True, exist_ok=True)
    patched = patch_search_script(src, runtime_dir() / "search_results_run.py", save_dir)
    log("   A token pop-up will appear in the dashboard — enter the RSA token.")
    t0 = time.time()
    rc, lines, to = run_script(patched, runtime_dir(), SEARCH_TIMEOUT_MIN)
    sr = save_dir / "SearchResults.xlsx"
    if to:
        raise RecoStop(f"SearchResults download took longer than {SEARCH_TIMEOUT_MIN} minutes.")
    if not sr.exists() or sr.stat().st_mtime < t0 - 5:
        raise RecoStop("SearchResults.xlsx was not downloaded (token / login / export problem) — see the log.")
    n = search_rows(sr)
    if n <= 0:
        raise RecoStop("SearchResults.xlsx has no data rows.")
    log(f"   ✔ SearchResults.xlsx downloaded ({n} rows)  →  {sr}")
    return sr


def download_nse():
    """Previous trading day (NSE + holiday list) and its C / F SEC_PLEDGE files."""
    today = date.today()
    ensure_dirs()
    trade_day, nse = get_trade_day(today)
    log(f"   ✔ Previous trading day = {trade_day:%A %d-%m-%Y}")
    nse = ensure_trade_file(trade_day, nse)
    fetch_pledge_files(trade_day, nse)
    log(f"   Saved in: {nse_dir()}")


def download_ftp():
    today = date.today()
    ensure_dirs()
    out = downloads_dir() / dmy(today) / "ftp"
    res = ftp_download(today, out, ["T1", "NFO", "KAMB"])
    lacking = [FTP_FILES[k][1].format(d=dmy(today)) for k, v in FTP_FILES.items() if v[3] and not res.get(k)]
    log(f"   Saved in: {out}")
    if lacking:
        for n in lacking:
            log(f"[MISSING_FILE] {n}")
        raise RecoStop("Today's FTP file(s) are not available yet:\n" + "\n".join(f"    ✘ {n}" for n in lacking))


def _dl_state_file(kind):
    return state_dir() / f"download_{kind}.json"


def write_dl_state(kind, status, msg=""):
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        _dl_state_file(kind).write_text(json.dumps(
            {"status": status, "msg": msg, "date": date.today().isoformat(),
             "time": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    except Exception:
        pass


def read_dl_states():
    out = {}
    for k in ("nse", "ftp", "search"):
        try:
            out[k] = json.loads(_dl_state_file(k).read_text(encoding="utf-8"))
        except Exception:
            out[k] = None
    return out


def run_download(kind):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    write_dl_state(kind, "running")
    try:
        _title({"nse": "NSE MEMBER-PORTAL FILES", "ftp": "FTP FILES", "search": "SEARCH RESULTS (TCS BANCS / MCX)"}[kind]
               + f"  -  {nice(date.today())}")
        if kind == "search":
            miss = _missing_pkgs()
            if miss:
                raise RecoStop("Some required Python packages are not installed: " + ", ".join(miss))
        {"nse": download_nse, "ftp": download_ftp, "search": download_search}[kind]()
        log("\n  DONE.")
    except RecoStop as e:
        log("")
        log(f"  STOPPED — {e}")
        log("[FRIENDLY_MSG] " + " ".join(str(e).split()))
        write_dl_state(kind, "failed", " ".join(str(e).split())[:300])
        sys.exit(1)
    except Exception as e:
        write_dl_state(kind, "failed", str(e)[:300])
        raise
    write_dl_state(kind, "done")
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def run_reco(procs=None):
    """Run the given processes (default: all four). Exit code 0 only when all of them are DONE."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        try:
            ok = _run(list(procs) if procs else list(PROCS))
        finally:
            clear_current()
    except RecoStop as e:
        log("")
        log("=" * 68)
        log("  STOPPED — nothing was e-mailed")
        log("=" * 68)
        log(f"  {e}")
        log("[FRIENDLY_MSG] " + " ".join(str(e).split()))   # shown in the dashboard strip
        sys.exit(1)
    sys.exit(0 if ok else 1)


def _title(msg):
    log("")
    log("=" * 68)
    log(f"  {msg}")
    log("=" * 68)


def _run(procs):
    today = date.today()
    stamp = datetime.now().strftime("%d%m%Y_%H%M%S")
    ensure_dirs()
    _title(f"RECONCILIATION  -  {nice(today)}  ({today:%A})")

    miss = _missing_pkgs()
    if miss:
        raise RecoStop("Some required Python packages are not installed on this machine: " + ", ".join(miss))
    for p in procs:
        if not script_path(SCRIPT_NAMES[p]):
            raise RecoStop(f"Script missing: {SCRIPT_NAMES[p]} — put it in {codes_dir()}")
    if "T1 HOLDING" in procs and not script_path(SEARCH_SCRIPT_NAME):
        raise RecoStop(f"Script missing: {SEARCH_SCRIPT_NAME} — put it in {codes_dir()}")
    purge_old()

    state = load_state(today)
    done = [p for p in procs if state.get(p, {}).get("status") == "DONE"]
    pending = [p for p in procs if p not in done]
    if done:
        log(f"Already done today (skipped): {', '.join(done)}")
    if not pending:
        log("Everything selected is already DONE for today. Nothing to do.")
        return True

    # ── STEP 1: strict file check ──
    _title("STEP 1 of 5 — Checking every required file")
    items = check_files(pending, today)
    bad = [i for i in items if not i["ok"]]
    for i in items:
        log(f"   {'✔' if i['ok'] else '✘'} {i['label']}" + (f"  →  {i['file']}" if i['file'] else "")
            + (f"   ({i['why']})" if i['why'] and not i['ok'] else ""))
    if bad:
        for i in bad:
            log(f"[MISSING_FILE] {i['example']}")
        raise RecoStop("These files are missing, so the reconciliation did NOT run:\n"
                       + "\n".join(f"    ✘ {i['label']}" + (f" — {i['why']}" if i['why'] else "") for i in bad)
                       + "\n  Upload them with ⬆️ Upload files and run again.")

    need_hold = "HOLDING" in pending
    need_t1 = "T1 HOLDING" in pending

    # ── STEP 2: trading day (NSE + holiday list) ──
    trade_day = None
    nse = None
    if need_hold or need_t1:
        _title("STEP 2 of 5 — Finding the previous trading day (NSE + holiday list)")
        set_current(None, "Finding trading day", pending)
        trade_day, nse = get_trade_day(today)
        log(f"   ✔ Previous trading day = {trade_day:%A %d-%m-%Y}")
        if need_hold:
            nse = ensure_trade_file(trade_day, nse)      # the HOLDING script reuses this file
    else:
        _title("STEP 2 of 5 — (not needed)")

    # ── STEP 3: FTP ──
    _title("STEP 3 of 5 — Downloading today's Kambala / Symphony / Noren files from FTP")
    groups = []
    if need_t1:
        groups.append("T1")
    if "NFO BFO MTFS" in pending:
        groups.append("NFO")
    if "KAMBALA POSITION" in pending:
        groups.append("KAMB")
    ftp_files = {}
    if groups:
        set_current(None, "Downloading FTP files", pending)
        ftp_files = ftp_download(today, downloads_dir() / dmy(today) / "ftp", groups)
        lacking = [FTP_FILES[k][1].format(d=dmy(today)) for k, v in FTP_FILES.items()
                   if v[2] in groups and v[3] and not ftp_files.get(k)]
        if lacking:
            for n in lacking:
                log(f"[MISSING_FILE] {n}")
            raise RecoStop("Today's FTP file(s) are not available yet:\n"
                           + "\n".join(f"    ✘ {n}" for n in lacking)
                           + "\n  Wait for them to arrive and run again — nothing was run or e-mailed.")
    else:
        log("   (nothing needed)")

    # ── STEP 4: NSE C/F pledge files for T1 ──
    cf = {}
    if need_t1:
        _title("STEP 4 of 5 — NSE SEC_PLEDGE files (C and F) for T1 HOLDING")
        set_current(None, "Downloading NSE files", pending)
        cf, nse = fetch_pledge_files(trade_day, nse)
    else:
        _title("STEP 4 of 5 — (not needed)")

    # ── STEP 5: MCX SearchResults (token) for T1 ──
    wd = work_dirs()
    if need_t1:
        _title("STEP 5 of 5 — MCX SearchResults for T1 HOLDING (RSA token needed)")
        set_current(None, "Search Result (BANCS) — token needed", pending)
        park_leftovers(wd["T1 HOLDING"], "T1 HOLDING", stamp)
        fetch_search_results(wd["T1 HOLDING"], stamp)
    else:
        _title("STEP 5 of 5 — (not needed)")

    # ── run the reconciliations ──
    _title("Running the reconciliations")
    risk = find_risk(today)
    today_hold = sorted(common_dir().glob(f"HOLDING_{dmy(today)}*.csv"))[0] if (need_hold or need_t1) else None
    exception = find_exception()
    posbook = None
    mcx_book = None
    if any(p in pending for p in ("NFO BFO MTFS", "KAMBALA POSITION")):
        posbook = find_uploaded("posbook")[0]
        if "KAMBALA POSITION" in pending:
            mcx_book = downloads_dir() / dmy(today) / f"PositionBook_{dmy(today)}.xlsx"
            n = make_mcx_positionbook(str(posbook), str(mcx_book))
            log(f"   MCX rows separated for KAMBALA POSITION: {n}")

    for proc in PROCS:
        if proc not in pending:
            continue
        log(f"\n▶ {proc}")
        set_current(PKEY[proc], "Running", pending)
        t0 = time.time()
        try:
            if proc == "HOLDING":
                park_leftovers(wd["HOLDING"], "HOLDING", stamp)
                park_leftovers(reco_base() / "HOLDING_DOWNLOADS", "HOLDING_DL", stamp)
                old_h = find_holding(trade_day)
                copy_to(old_h, wd["HOLDING"], f"HOLDING_{dmy(trade_day)}.csv", age_seconds=7200)
                copy_to(today_hold, wd["HOLDING"], f"HOLDING_{dmy(today)}.csv", age_seconds=3600)
                copy_to(risk, wd["HOLDING"], f"RISK_REPORT_{dmy(today)}.csv")
            elif proc == "T1 HOLDING":
                copy_to(cf["C"], wd[proc])
                copy_to(cf["F"], wd[proc])
                copy_to(today_hold, wd[proc], f"HOLDING_{dmy(today)}.csv")
                copy_to(risk, wd[proc], f"RISK_REPORT_{dmy(today)}.csv")
                exc_name = (exception.name if exception.name.upper().startswith("EXCEPTION_")
                            else f"EXCEPTION_{dmy(today)}{exception.suffix}")
                copy_to(exception, wd[proc], exc_name)
                for k in ("T1_SYMPHONY", "T1_KAMBALA", "MF_KAMB", "T1_NOREN", "MF_NOREN"):
                    if ftp_files.get(k):
                        copy_to(ftp_files[k], wd[proc])
            elif proc == "NFO BFO MTFS":
                park_leftovers(wd[proc], proc, stamp)
                copy_to(posbook, wd[proc], "PositionBook.xlsx")
                for k in ("FO_POS", "BSEF_POS", "CASH_POS"):
                    copy_to(ftp_files[k], wd[proc])
                copy_to(risk, wd[proc], f"RISK_REPORT_{dmy(today)}.csv")
            else:
                park_leftovers(wd[proc], proc, stamp)
                copy_to(mcx_book, wd[proc])
                copy_to(ftp_files["MCX_POS"], wd[proc])
                copy_to(risk, wd[proc], f"RISK_REPORT_{dmy(today)}.csv")
            patched = patch_script(proc, script_path(SCRIPT_NAMES[proc]),
                                   runtime_dir() / (re.sub(r"[^A-Za-z0-9]+", "_", proc) + "_run.py"))
        except Exception as e:
            log(f"   ✘ Could not prepare files: {e}")
            state[proc] = {"status": "FAILED", "reason": f"could not prepare files: {e}", "time": stamp}
            save_state(today, state)
            continue

        rc, lines, to = run_script(patched, runtime_dir(), PROCESS_TIMEOUT_MIN)
        status, reason = judge(rc, lines, to, PROCESS_TIMEOUT_MIN)
        if proc == "HOLDING":
            park_leftovers(reco_base() / "HOLDING_DOWNLOADS", "HOLDING_DL_after", stamp)
        try:
            strip_shared_from_script_backups(proc)
        except Exception as e:
            log(f"   ⚠ could not tidy the script's backup folder: {e}")
        try:
            collect_backup(proc, t0, today)
        except Exception as e:
            log(f"   ⚠ backup copy problem: {e}")
        state[proc] = {"status": status, "reason": reason, "time": datetime.now().strftime("%H:%M"), "stamp": stamp}
        save_state(today, state)
        log(f"   => {proc}: {status}{(' - ' + reason) if reason else ''}")

    # ── summary ──
    _title(f"SUMMARY  -  {nice(today)}")
    all_done = True
    for p in PROCS:
        st = state.get(p, {})
        s = st.get("status", "NOT RUN")
        all_done &= (s == "DONE")
        log(f"  {'✔' if s == 'DONE' else '✘'} {p:<18} {s}" + (f"  -  {st['reason']}" if st.get("reason") and s != "DONE" else ""))
    if all_done:
        # today's PositionBook / SearchResults are used up — keep one dated copy, clear the drop folder.
        # (EXCEPTION stays. HOLDING / RISK_REPORT live in COMMON FILES and are never touched here.)
        keep = backup_root() / nice(today) / "_uploaded_inputs"
        for f in list(uploads_dir().iterdir()):
            if f.is_file():
                try:
                    keep.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, keep / f.name)
                    f.unlink()
                except Exception:
                    pass
        log("\n  ALL DONE. All emails have been sent.")
    done_now = [p for p in pending if state.get(p, {}).get("status") == "DONE"]
    return len(done_now) == len(pending)


def run_reco_all():
    run_reco(list(PROCS))


def run_reco_one(key):
    proc = KEY2PROC.get(key)
    if not proc:
        print(f"Unknown process {key!r}")
        sys.exit(1)
    run_reco([proc])


if __name__ == "__main__":
    run_reco(None)
