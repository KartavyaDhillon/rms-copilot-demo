"""core/mtf_concentration.py - MTF CONCENTRATION card (the KORP downloader + index-file downloader + CONCENTRATION_FINAL process as ONE job).

Daily timeline (trading days, all times editable in the card's Settings):
    08:40  downloads   KORP MTF CRR report 5 (<ddmmyyyyHHMMSS>_SHORTAGE.xlsx) and the 3 index files (NIFTY 50, BSE 200, BSE 500)
    09:15  MTFPOSITION_<ddmmyyyy>.csv is fetched from the same FTP folder as HOLDING / RISK_REPORT (core/common_ftp.py)
    09:30  the run: stage inputs -> calculation (core/mtf_conc_calc.py = CONCENTRATION_FINAL.py) -> e-mails -> backup

Inputs:  RISK_REPORT + MTFPOSITION are READ from COMMON FILES (copied for the run, the originals stay);
         EXCEPTION.xlsx lives in this card's folder (or COMMON FILES\\OTHER FILES - the "Other files" upload);
         SHORTAGE + the 3 index files are downloaded here.
Outputs: FULLFILE_CONCENTRATION_<d>.xlsx (+ copy to the Enterprise Risk folder) and four zone files, e-mailed as before.
Nothing secret is stored in code: the back-office login is Scheduler > Connections > Back-office API; the sender is the
card's Credentials window (MTFCONC_SENDER_*) or the shared one.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

NAME = "MTF CONCENTRATION"
FOLDER_NAME = "CONCENTRATION"
PIPE = "mtfconc"
BACKUP_SUB = "BACKUPFILES AUTOMATED"
REPO = Path(__file__).resolve().parent.parent
CALC_SCRIPT = Path(__file__).resolve().parent / "mtf_conc_calc.py"

NIFTY_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
BSE_API = "https://api.bseindia.com/BseIndiaAPI/api"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
# (label, BSE iname, BSE index_Code, final file name, columns the calculation needs)
BSE_FILES = [("BSE 500", "BSE500", "17", "BSE 500index_Constituents.csv"),
             ("BSE 200", "BSE200", "23", "BSE 200index_Constituents.csv")]
NIFTY_FILE = "ind_nifty50list.csv"
BSE_NEED = ("Scrip Code", "ISIN No.")
NIFTY_NEED = ("ISIN Code",)

SHORTAGE_GLOB = "*_SHORTAGE.xlsx"
EXCEPTION = "EXCEPTION.xlsx"
ZONES = ("ZODELH", "ZOLUDH", "ZOCHND", "ZOMUMB")
LATE_CUTOFF = dt.time(15, 0)          # the timer never starts a job later than this (a restarted server must not run it at night)

# who gets what (the defaults of CONCENTRATION_FINAL.py; the Email To / CC window edits them)
_CC4 = "sandeep.kumar2@mastertrust.co.in,amit.pundir@mastertrust.co.in,sumit.aggarwal@mastertrust.co.in,vipin.rai@mastertrust.co.in"
MAIL_DEFAULTS = {
    "FULL": ("rms_del@mastertrust.co.in", ""),
    "ZODELH": ("rms_del@mastertrust.co.in", ""),
    "ZOLUDH": ("ludhianacompliance@mastertrust.co.in,amandeep.singh2@mastertrust.co.in,kuljit.singh@mastertrust.co.in", _CC4),
    "ZOCHND": ("rms_chd@mastertrust.co.in", _CC4),
    "ZOMUMB": ("pradip.mandavkar@mastertrust.co.in,santosh.tajane@mastertrust.co.in", _CC4),
}
MAIL_LABEL = {"FULL": "Full file", "ZODELH": "Zone ZODELH", "ZOLUDH": "Zone ZOLUDH", "ZOCHND": "Zone ZOCHND", "ZOMUMB": "Zone ZOMUMB"}

STEP_LABEL = {"korp": "KORP shortage file", "index": "Index files", "mtfpos": "MTFPOSITION", "risk": "Risk report",
              "exception": "Exception file", "calc": "Calculation", "email": "E-mails", "backup": "Backup"}
STEP_ORDER = ("korp", "index", "mtfpos", "risk", "exception", "calc", "email", "backup")

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_LOG = collections.deque(maxlen=400)
_ACTIVE = {"procs": [], "cancel": False, "log_file": None}
_STEP_LOCKS = collections.defaultdict(threading.Lock)
_TICK = {"thread": None, "stop": threading.Event()}


class Busy(Exception):
    pass


class PreRunError(Exception):
    pass


class Cancelled(Exception):
    pass


# ───────────────────────────── env defaults for the shared Email To / CC window ─────────────────────────────
def mail_keys(k: str) -> tuple:
    return (f"MTFCONC_{k}_TO", f"MTFCONC_{k}_CC")


def apply_defaults() -> None:
    """Fill (only what .env lacks) the To / CC of the five mails so the Email window shows what is really used."""
    for k, (to, cc) in MAIL_DEFAULTS.items():
        tk, ck = mail_keys(k)
        os.environ.setdefault(tk, to)
        os.environ.setdefault(ck, cc)


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("MTF_CONC_DIR", "").strip()
    if v:
        p = Path(v)
    else:
        try:
            from core.scheduler import paths
            base = paths.data_root()
        except Exception:
            base = REPO / "data"
        p = base / FOLDER_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def common_dir() -> Path:
    return Path(os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES"))


def other_dir() -> Path:
    from core import common_ftp
    return Path(common_ftp.other_dir())


def _state_dir() -> Path:
    p = root() / "_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_root() -> Path:
    return root() / BACKUP_SUB


def log_dir() -> Path:
    v = os.getenv("MTF_CONC_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / "mtfconc"
    except Exception:
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _today() -> dt.date:
    return dt.date.today()


def _iso(t=None) -> str:
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def dstr(d: dt.date) -> str:
    """Backup folder name, in the style already used in BACKUPFILES AUTOMATED: '16th Nov 2026'."""
    suffix = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return d.strftime(f"%d{suffix} %b %Y")


_BNAME = re.compile(r"^(\d{1,2})(st|nd|rd|th) ([A-Za-z]{3}) (\d{4})$")


def parse_bname(s: str):
    m = _BNAME.match(s or "")
    if not m:
        return None
    try:
        return dt.datetime.strptime(f"{int(m.group(1)):02d} {m.group(3).title()} {m.group(4)}", "%d %b %Y").date()
    except ValueError:
        return None


def _check_date(s: str) -> str:
    if parse_bname(s) is None:
        raise ValueError("bad backup folder name")
    return s


# ───────────────────────────── settings / state ─────────────────────────────
def _hhmm(s, default: str) -> str:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(s or ""))
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return default


def _settings_path() -> Path:
    return _state_dir() / "settings.json"


def get_settings() -> dict:
    d = {"auto": True, "download_at": "08:40", "mtf_at": "09:15", "run_at": "09:30", "send_mail": True,
         "copy_dir": os.getenv("MTF_CONC_COPY_DIR", r"D:\ENTERPRISE RISK"), "alert_numbers": ""}
    try:
        d.update(json.loads(_settings_path().read_text(encoding="utf-8")))
    except Exception:
        pass
    d["download_at"], d["mtf_at"], d["run_at"] = (_hhmm(d.get("download_at"), "08:40"), _hhmm(d.get("mtf_at"), "09:15"),
                                                 _hhmm(d.get("run_at"), "09:30"))
    return d


def save_settings(update: dict, by: str = "") -> dict:
    cur = get_settings()
    for k in ("auto", "send_mail"):
        if k in update:
            cur[k] = bool(update[k])
    for k, dflt in (("download_at", "08:40"), ("mtf_at", "09:15"), ("run_at", "09:30")):
        if k in update:
            v = _hhmm(update[k], "")
            if not v:
                raise ValueError(f"{update[k]!r} is not a time like 09:30")
            cur[k] = v
    if "copy_dir" in update:
        cur["copy_dir"] = str(update["copy_dir"] or "").strip()
    if "alert_numbers" in update:
        raw = str(update["alert_numbers"] or "").strip()
        if raw:
            from core.scheduler import failure_alert
            if not failure_alert.parse_numbers(raw):
                raise ValueError("those WhatsApp numbers are not valid")
        cur["alert_numbers"] = raw
    cur["by"], cur["at"] = by, _iso()
    with _STATE_LOCK:
        _settings_path().write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return cur


def auto_enabled() -> bool:
    return bool(get_settings().get("auto"))


def _state_path() -> Path:
    return _state_dir() / "state.json"


def read_state() -> dict:
    """Today's record; a record of an earlier day is ignored (a new day starts clean)."""
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        st = {}
    if st.get("date") != _today().isoformat():
        return {"date": _today().isoformat(), "status": "idle", "steps": {}, "sched": {}}
    st.setdefault("steps", {})
    st.setdefault("status", "idle")
    st.setdefault("sched", {})
    return st


def _write_state(st: dict):
    with _STATE_LOCK:
        tmp = _state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        tmp.replace(_state_path())


def _update(**kw) -> dict:
    with _STATE_LOCK:
        st = read_state()
        st.update(kw)
        _write_state(st)
        return st


def _sched_mark(key: str, **kw):
    with _STATE_LOCK:
        st = read_state()
        st["sched"].setdefault(key, {}).update(kw)
        _write_state(st)


def _set_step(name: str, status: str, msg: str = ""):
    with _STATE_LOCK:
        st = read_state()
        st["steps"][name] = {"status": status, "msg": msg[:300], "at": _iso()}
        _write_state(st)


# ───────────────────────────── logging ─────────────────────────────
def log(msg: str):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    _LOG.append(line)
    f = _ACTIVE.get("log_file")
    if f:
        try:
            f.write(line + "\n")
            f.flush()
        except Exception:
            pass
    print("[mtfconc] " + msg, flush=True)


def _file_only(msg: str):
    f = _ACTIVE.get("log_file")
    if f:
        try:
            f.write(f"[{dt.datetime.now():%H:%M:%S}]   {msg}\n")
            f.flush()
        except Exception:
            pass


def recent_log(n: int = 120) -> list:
    return list(_LOG)[-n:]


def _open_run_log(trigger, by):
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"mtfconc__{now:%d-%m-%Y}__{now:%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== {NAME} - {now:%d-%m-%Y %H:%M:%S} =====\nstarted by: {by or trigger} ({trigger})\n\n")
        f.flush()
        return f
    except Exception:
        return None


def _cancel() -> bool:
    return bool(_ACTIVE.get("cancel"))


def _check_cancel():
    if _cancel():
        raise Cancelled("Stopped.")


# ───────────────────────────── inputs ─────────────────────────────
def _saved_day(p: Path) -> dt.date:
    """The day a file was put here: later of modified / created (a copied-in old file keeps its old mtime) - Windows only."""
    t = p.stat().st_mtime
    if os.name == "nt":
        t = max(t, p.stat().st_ctime)
    return dt.datetime.fromtimestamp(t).date()


def _dd(d=None) -> str:
    return (d or _today()).strftime("%d%m%Y")


def find_shortage(base: Path = None, today: dt.date = None):
    """Newest *_SHORTAGE.xlsx whose name starts with today's ddmmyyyy (the back office stamps it)."""
    base = Path(base or root())
    t = _dd(today)
    c = [p for p in base.glob(SHORTAGE_GLOB) if p.name.startswith(t) and p.is_file()]
    return max(c, key=lambda p: p.stat().st_mtime) if c else None


def _csv_header(p: Path) -> list:
    with open(p, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return next(csv.reader(f), [])


def _fresh(p: Path, today: dt.date) -> bool:
    return p.is_file() and p.stat().st_size > 0 and _saved_day(p) == today


def input_status(base: Path = None, today: dt.date = None) -> list:
    base = Path(base or root())
    today = today or _today()
    d = _dd(today)
    rows = []

    def add(key, label, p, ok, why="", name=None):
        found = bool(p and Path(p).is_file())
        row = {"key": key, "label": label, "name": name or (Path(p).name if p else ""), "found": found, "ok": bool(found and ok),
               "why": "" if (found and ok) else why, "size": 0, "time": "", "where": ""}
        if found:
            st = Path(p).stat()
            row.update(size=st.st_size, time=dt.datetime.fromtimestamp(st.st_mtime).strftime("%H:%M:%S"), where=str(Path(p).parent))
        rows.append(row)

    for key, label, nm in (("MTFPOSITION", "MTFPOSITION (COMMON FILES)", f"MTFPOSITION_{d}.csv"),
                           ("RISK_REPORT", "RISK_REPORT (COMMON FILES)", f"RISK_REPORT_{d}.csv")):
        p = common_dir() / nm
        ok = p.is_file() and p.stat().st_size > 0
        add(key, label, p if p.is_file() else None, ok, "empty" if p.is_file() else "", nm)
    sh = find_shortage(base, today)
    add("SHORTAGE", "KORP shortage file", sh, bool(sh), "", f"{d}<time>_SHORTAGE.xlsx")
    for key, label, fn, need in (("NIFTY50", "NIFTY 50 list", NIFTY_FILE, NIFTY_NEED),
                                 ("BSE200", "BSE 200 list", BSE_FILES[1][3], BSE_NEED),
                                 ("BSE500", "BSE 500 list", BSE_FILES[0][3], BSE_NEED)):
        p = base / fn
        ok, why = True, ""
        if p.is_file():
            if not _fresh(p, today):
                ok, why = False, "not saved today"
            else:
                try:
                    miss = [c for c in need if c not in _csv_header(p)]
                    if miss:
                        ok, why = False, "columns missing: " + ", ".join(miss)
                except Exception as e:                           # noqa: BLE001
                    ok, why = False, f"unreadable ({e})"
        add(key, label, p if p.is_file() else None, ok, why, fn)
    ex = base / EXCEPTION
    if not ex.is_file():
        alt = other_dir() / EXCEPTION
        ex = alt if alt.is_file() else None
    add("EXCEPTION", "Exception file (permanent)", ex, True, "", EXCEPTION)
    return rows


def pre_run_check(base: Path = None) -> dict:
    rows = input_status(base)
    bad = [r for r in rows if not r["ok"]]
    if bad:
        raise PreRunError("Missing / not ready: " + "; ".join(f"{r['label']}" + (f" ({r['why']})" if r["why"] else "") for r in bad))
    return {"inputs": rows}


# ───────────────────────────── download: KORP report 5 ─────────────────────────────
def _fy(d: dt.date) -> str:
    return f"{d.year}-{d.year + 1}" if d.month >= 4 else f"{d.year - 1}-{d.year}"


def step_korp(base: Path, force: bool = False) -> dict:
    """MTF CRR report 5 from the back-office API -> <ddmmyyyyHHMMSS>_SHORTAGE.xlsx (same columns / order as the hand script)."""
    if not force and find_shortage(base):
        return {"ok": True, "skipped": True, "msg": "already in the folder"}
    import base64
    import requests
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from core import korp_api
    from core.scheduler import connections
    c = connections.require("backoffice", "base_url", "username", "password", "client_ip", "client_mac")
    b = korp_api.pick_base(c["base_url"], log=log)
    s = requests.Session()
    key = korp_api.parse_session_key(s.get(f"{b}/api/Common/SessionKey/Get", timeout=30).text).encode()
    pdr = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    pw = base64.b64encode(enc.update(pdr.update(c["password"].encode()) + pdr.finalize()) + enc.finalize()).decode()
    lg = s.post(f"{b}/api/token", data={"CallFrom": "FINKORP", "UserName": c["username"], "Password": pw, "Grant_type": "password",
                                       "ClientIP": c["client_ip"], "ClientMAC": c["client_mac"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
    try:
        token = lg.json()["access_token"]
    except Exception:
        return {"ok": False, "msg": f"Back-office login failed (HTTP {lg.status_code}) - check Connections > Back-office API."}
    now = dt.datetime.now()
    hdr = {"Authorization": f"Bearer {token}", "FIRMID": "1001", "FINANCIALYEAR": _fy(now.date()), "Content-Type": "application/json"}
    payload = {"RecordDate": now.astimezone().isoformat(), "ReportType": "5", "ActReportType": None, "FirmID": None, "AccountID": None,
               "Branch": None, "SubBranch": None, "RM": None, "FamilyGroup": None, "CustomFilter": None, "ExportFormat": 0,
               "RequestType": 0,
               "EmailAndFileParam": {"FromID": None, "IsEmailRequest": False, "HighRequestPriority": False, "EmailPriority": 0,
                                     "DigitalSign": False, "PasswordProtected": False, "IsBackgroundProcess": False,
                                     "BackgroundProcessToken": None},
               "TradeDate": None, "Zone": None, "Region": None, "DeleteFlag": None}
    last, data = "", None
    for attempt in range(1, 4):
        _check_cancel()
        try:
            r = s.post(f"{b}/api/Reports/MTFCRRReport/Post", headers=hdr, json=payload, timeout=(10, 900))
            if r.status_code == 200:
                data = r.json()
                if data:
                    break
                last = "the report came back empty"
            else:
                last = f"HTTP {r.status_code} {r.text[:150]}"
        except Exception as e:                                   # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        log(f"MTF CRR report 5: try {attempt}/3 failed: {last}")
        time.sleep(3 * attempt)
    if not data:
        return {"ok": False, "msg": f"MTF CRR report 5 failed: {last}"}
    import pandas as pd
    df = pd.DataFrame(data).rename(columns={
        "GroupID": "Group ID", "AccountID": "Account ID", "LeastFundingValue": "Least Funding Value",
        "IntransitFundedValue": "Intransit Funded Value", "MTFColletral": "MTF Colletral", "ApplicableMargin": "Applicable Margin",
        "NetColletral": "Net Colletral", "MTFLedger": "MTF Ledger", "NonMTFledger": "Non MTF Ledger", "MTFRisk": "MTF Risk",
        "NonMtfToMtf": "Non MTF To MTF", "MtfToNonMtf": "MTF To Non MTF"})
    cols = ["TradeDate", "Zone", "Region", "Branch", "SubBranch", "Group ID", "Account ID", "Least Funding Value",
            "Intransit Funded Value", "MTF Colletral", "Applicable Margin", "Net Colletral", "MTF Ledger", "Non MTF Ledger",
            "MTF Risk", "Non MTF To MTF", "MTF To Non MTF"]
    df = df.reindex(columns=cols)
    out = base / f"{now:%d%m%Y%H%M%S}_SHORTAGE.xlsx"
    tmp = base / (out.name + ".part")
    with pd.ExcelWriter(tmp, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Sheet1", index=False)
    tmp.replace(out)
    return {"ok": True, "msg": f"saved {out.name} ({len(df)} rows)"}


# ───────────────────────────── download: the 3 index files (plain requests, no browser) ─────────────────────────────
def _valid_csv(content: bytes, need, min_rows: int) -> str:
    """'' if the bytes are a CSV holding the needed columns and enough rows, else the reason."""
    if b"<html" in content[:400].lower():
        return "an HTML page, not a csv"
    try:
        rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig", "replace"))))
    except Exception as e:                                       # noqa: BLE001
        return f"unreadable ({e})"
    if not rows:
        return "empty"
    miss = [c for c in need if c not in rows[0]]
    if miss:
        return "columns missing: " + ", ".join(miss)
    if len(rows) - 1 < min_rows:
        return f"only {len(rows) - 1} rows"
    return ""


def _save_bytes(base: Path, name: str, content: bytes):
    tmp = base / (name + ".part")
    tmp.write_bytes(content)
    tmp.replace(base / name)


def _bse_one(s, label, iname, code, final, base: Path) -> str:
    """Returns '' when saved, else the reason."""
    meta = s.get(f"{BSE_API}/indiceswatch_weight_ng/w?flag={code}", timeout=40).json()
    idx = ((meta.get("Table") or [{}])[0].get("IndexName") or label).strip()
    r = s.get(f"{BSE_API}/NS_IndexWeight_SPDJ_Download_ng/w?iname={iname}&fName={idx}Index_Constituents", timeout=60)
    why = _valid_csv(r.content, BSE_NEED, 100) if r.status_code == 200 else f"HTTP {r.status_code}"
    if not why:
        _save_bytes(base, final, r.content)
        return ""
    log(f"{label}: download call gave {why} - trying the table call")
    t = s.get(f"{BSE_API}/NS_IndexWeight_SPDJ_ng/w?iname={iname}", timeout=60).json().get("Table") or []
    if not t:
        return f"both BSE calls failed ({why})"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(t[0].keys()))
    w.writeheader()
    w.writerows(t)
    data = buf.getvalue().encode("utf-8-sig")
    why2 = _valid_csv(data, BSE_NEED, 100)
    if why2:
        return f"the table call is not usable: {why2}"
    _save_bytes(base, final, data)
    log(f"{label}: built from the BSE table (the download call was refused)")
    return ""


def step_index(base: Path, force: bool = False) -> dict:
    import requests
    today = _today()
    todo = []
    for label, _i, _c, final in [("NIFTY 50", "", "", NIFTY_FILE)] + BSE_FILES:
        p = base / final
        if force or not _fresh(p, today):
            todo.append((label, final))
    if not todo:
        return {"ok": True, "skipped": True, "msg": "all 3 are already in the folder"}
    bad, got = [], []
    for label, final in todo:
        _check_cancel()
        try:
            if final == NIFTY_FILE:
                r = requests.get(NIFTY_URL, headers={"User-Agent": UA}, timeout=40)
                why = _valid_csv(r.content, NIFTY_NEED, 40) if r.status_code == 200 else f"HTTP {r.status_code}"
                if not why:
                    _save_bytes(base, final, r.content)
            else:
                s = requests.Session()
                s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Origin": "https://www.bseindia.com",
                                  "Referer": "https://www.bseindia.com/"})
                s.get("https://www.bseindia.com/", timeout=30)
                spec = next(x for x in BSE_FILES if x[3] == final)
                why = _bse_one(s, spec[0], spec[1], spec[2], final, base)
        except Exception as e:                                   # noqa: BLE001
            why = f"{type(e).__name__}: {e}"
        if why:
            bad.append(f"{label}: {why}")
            log(f"{label}: FAILED - {why}")
        else:
            got.append(label)
            log(f"{label}: saved")
    if bad:
        return {"ok": False, "msg": "; ".join(bad)}
    return {"ok": True, "msg": "saved " + ", ".join(got)}


# ───────────────────────────── MTFPOSITION (COMMON FILES FTP) ─────────────────────────────
def step_mtfpos(base: Path, force: bool = False) -> dict:
    from core import common_ftp
    r = common_ftp.fetch_mtfposition()
    return {"ok": bool(r["ok"]), "skipped": r.get("status") == "present", "msg": r.get("detail", "")}


def step_risk(base: Path, force: bool = False) -> dict:
    p = common_dir() / f"RISK_REPORT_{_dd()}.csv"
    if p.is_file() and p.stat().st_size > 0:
        return {"ok": True, "skipped": True, "msg": "in COMMON FILES"}
    return {"ok": False, "msg": "RISK_REPORT is not in COMMON FILES yet - upload it with Upload Common Files (or wait for the 07:30 FTP fetch)"}


def step_exception(base: Path, force: bool = False) -> dict:
    if (base / EXCEPTION).is_file():
        return {"ok": True, "skipped": True, "msg": "in the card folder"}
    alt = other_dir() / EXCEPTION
    if alt.is_file():
        return {"ok": True, "skipped": True, "msg": "in COMMON FILES\\OTHER FILES"}
    return {"ok": False, "msg": "EXCEPTION.xlsx is missing - upload it here (Upload files) or in Upload Common Files > Other files"}


STEPS = {"korp": step_korp, "index": step_index, "mtfpos": step_mtfpos, "risk": step_risk, "exception": step_exception}
DOWNLOADS = ("korp", "index")                 # the 08:40 job


def _do_step(name: str, base: Path, force: bool = False, have_lock: bool = False) -> dict:
    lk = _STEP_LOCKS[name]
    if not have_lock:
        lk.acquire()
    try:
        _set_step(name, "running")
        try:
            r = STEPS[name](base, force) if name in ("korp", "index") else STEPS[name](base)
        except Cancelled:
            _set_step(name, "stopped", "Stopped.")
            return {"ok": False, "msg": "Stopped."}
        except Exception as e:                                   # noqa: BLE001
            r = {"ok": False, "msg": f"{type(e).__name__}: {e}"}
        _set_step(name, "skipped" if r.get("skipped") else ("ok" if r.get("ok") else "failed"), r.get("msg", ""))
        log(f"{STEP_LABEL[name]}: {'OK' if r.get('ok') else 'FAILED'} - {r.get('msg', '')}")
        return r
    finally:
        if not have_lock:
            lk.release()


# ───────────────────────────── calculation, e-mail, backup ─────────────────────────────
def _python() -> str:
    return os.getenv("CTCL_PYTHON") or os.getenv("SA_PYTHON") or sys.executable


def _kill(p):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=15)
        else:
            p.kill()
    except Exception:
        pass


def stage_inputs(base: Path):
    """Copy RISK_REPORT / MTFPOSITION from COMMON FILES (originals stay there) and EXCEPTION.xlsx from OTHER FILES if needed."""
    d = _dd()
    for nm in (f"MTFPOSITION_{d}.csv", f"RISK_REPORT_{d}.csv"):
        shutil.copy2(common_dir() / nm, base / nm)
        log(f"staged {nm} from COMMON FILES")
    if not (base / EXCEPTION).is_file():
        shutil.copy2(other_dir() / EXCEPTION, base / EXCEPTION)
        log("staged EXCEPTION.xlsx from COMMON FILES\\OTHER FILES")


def outputs(today: dt.date = None) -> dict:
    d = _dd(today)
    o = {"FULL": f"FULLFILE_CONCENTRATION_{d}.xlsx"}
    for z in ZONES:
        o[z] = f"CONCENTRATION_{z}_{d}.xlsx"
    return o


def step_calc(base: Path) -> dict:
    try:
        import polars  # noqa: F401
    except ImportError:
        raise RuntimeError("the 'polars' package is not installed in the console's Python (pip install polars)")
    for nm in outputs().values():                                # a stale file of today can never be e-mailed by mistake
        try:
            (base / nm).unlink()
        except FileNotFoundError:
            pass
    env = dict(os.environ, CONC_BASE_FOLDER=str(base), CONC_COPY_FOLDER=get_settings().get("copy_dir", ""),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    flags = 0x08000000 if os.name == "nt" else 0
    p = subprocess.Popen([_python(), str(CALC_SCRIPT)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(base),
                         text=True, encoding="utf-8", errors="replace", creationflags=flags)
    _ACTIVE["procs"].append(p)
    tail = []

    def pump():
        for line in p.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                del tail[:-40]
                (log if re.search(r"✅|❌|⚠|Error|Traceback", line) else _file_only)(line[:300])
    th = threading.Thread(target=pump, daemon=True)
    th.start()
    t0 = time.time()
    try:
        while p.poll() is None:
            if _cancel():
                _kill(p)
                raise Cancelled("Stopped.")
            if time.time() - t0 > 1800:
                _kill(p)
                raise RuntimeError("the calculation did not finish in 30 minutes")
            time.sleep(0.5)
    finally:
        try:
            _ACTIVE["procs"].remove(p)
        except ValueError:
            pass
    th.join(timeout=5)
    try:
        p.stdout.close()
    except Exception:
        pass
    if p.returncode != 0 or not any("CONC_CALC_DONE" in x for x in tail):
        raise RuntimeError(f"the calculation failed (exit {p.returncode}): " + " | ".join(tail[-4:]))
    full = base / outputs()["FULL"]
    if not full.is_file():
        raise RuntimeError("the calculation finished but the full file was not created")
    made = [k for k, v in outputs().items() if (base / v).is_file()]
    copy_failed = any("COPY_FAILED" in x for x in tail)
    return {"made": made, "copy_failed": copy_failed, "zones": [k for k in made if k != "FULL"]}


def recipients(key: str):
    from core.mcx_cash_report import split_addrs
    tk, ck = mail_keys(key)
    d_to, d_cc = MAIL_DEFAULTS[key]
    to = split_addrs(os.environ.get(tk, d_to))
    cc = split_addrs(os.environ.get(ck, d_cc))
    return to, cc


def _sig() -> str:
    return ("Best regards,<br>\n<b>Kartavya Dhillon</b><br>\nMaster Capital Services Ltd<br>\n📞 011-66331000 | Ext. 84")


def full_body(d: str) -> str:
    return f"""Dear Team,<br><br>

Please find attached the Consolidated MTF Concentration Report dated <b>{d}</b>.<br><br>

This report highlights accounts based on the following monitoring parameters:<br><br>

<b>1. VIOLATION BASED ON SCRIP LEVEL (Scrip-Level Check):</b><br>
• For stocks classified under Nifty50,BSE500,BSE200 indices, exposure above <b>₹1 Crore</b> is treated as a breach.<br>
• For stocks classified under <b>Others</b>, exposure above <b>₹50 Lakhs</b> is treated as a breach.<br><br>

<b>2. VIOLATION BASED ON PORTFOLIO (Account-Level Basket Check):</b><br>
• Total exposure in <b><u>Nifty50,BSE500,BSE200 basket</u></b> exceeding <b>₹1 Crore</b> is marked as Violation.<br>
• Total exposure in <b><u>Others basket</u></b> exceeding <b>₹1 Crore</b> is marked as Violation.<br><br>

<b>Note:</b><br>
• Designated family accounts are monitored under customized basket-level exposure limits (10Cr – NIFTY50/BSE200, 6Cr – BSE500, 3Cr – OTHERS) as per the approved risk framework.<br>
• Special approved limits from the Exception file have been applied wherever applicable.<br>
• MTF Collateral is sourced from <b>MTF CRR Report 5</b> and it has been considered while computing the revised portfolio exposure and concentration metrics.<br><br>
Any account marked as <b>"Violation"</b> under either of the above columns requires immediate review and appropriate action.<br><br>

Kindly review the attached report and take necessary corrective measures wherever applicable.<br><br>

{_sig()}
"""


def zone_body(zone: str, d: str) -> str:
    return f"""Dear Team,<br><br>

Please find attached the MTF Concentration Report for <b>{zone}</b> dated <b>{d}</b>.<br><br>

This report includes only those accounts where either of the following conditions is marked as <b>"Violation"</b>:<br><br>

<b>• VIOLATION BASED ON SCRIP LEVEL:</b> Indicates scrip-level exposure exceeding the prescribed limits (₹1 Crore for index stocks<b><u>(Nifty50,BSE500,BSE200)</u></b> and ₹50 Lakhs for <b><u>Others</u></b>).<br><br>
<b>• VIOLATION BASED ON PORTFOLIO:</b> Indicates aggregate basket-level exposure exceeding ₹1 Crore <b><u>(Nifty50,BSE500,BSE200)</u></b> or ₹1 Crore <b><u>(Others)</u></b>.<br>
   • MTF Collateral is sourced from <b>MTF CRR Report 5</b> and it has been considered while computing the revised portfolio exposure and concentration metrics.<br><br>

Accounts marked as <b>"Violation"</b> require review to assess exposure levels and ensure adherence to internal risk and concentration guidelines.<br><br>

Kindly examine the highlighted accounts and initiate necessary corrective action wherever applicable.<br><br>

{_sig()}
"""


def send_one(subject, body, path: Path, to, cc, attempts=5, gap=10, sleep=time.sleep, smtp_factory=None):
    """One mail with the file attached; retries. Raises RuntimeError if it never goes."""
    import smtplib
    from email import encoders
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from core import sender_override
    user, pwd = sender_override.sender(PIPE)
    host, port = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in"), int(os.getenv("SMTP_PORT", "587") or 587)
    if not to:
        raise RuntimeError("no 'To' address set - use the Email To / CC button")
    if not (user and pwd):
        raise RuntimeError("the sender login is not set (Credentials > E-mail sender, or SENDER_EMAIL / SENDER_PASSWORD in .env)")
    last = None
    for n in range(1, attempts + 1):
        try:
            msg = MIMEMultipart()
            msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to), subject
            if cc:
                msg["Cc"] = ", ".join(cc)
            msg.attach(MIMEText(body, "html"))
            part = MIMEBase("application", "octet-stream")
            part.set_payload(Path(path).read_bytes())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{Path(path).name}"')
            msg.attach(part)
            srv = (smtp_factory or smtplib.SMTP)(host, port, timeout=60)
            try:
                srv.starttls()
                srv.login(user, pwd)
                srv.send_message(msg, to_addrs=list(to) + list(cc))
            finally:
                try:
                    srv.quit()
                except Exception:
                    pass
            log(f"E-mail sent: {Path(path).name} -> {', '.join(to)}" + (f" (cc {', '.join(cc)})" if cc else ""))
            return
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(f"the mail login was refused: {e}")
        except Exception as e:                                   # noqa: BLE001
            last = e
            log(f"E-mail failed (try {n}/{attempts}): {type(e).__name__}: {e}")
            if n < attempts:
                sleep(gap)
    raise RuntimeError(f"e-mail did not go: {type(last).__name__}: {last}")


def step_email(base: Path, made: list, sleep=time.sleep, smtp_factory=None) -> dict:
    d = _today().strftime("%d%m%Y")
    outs = outputs()
    sent, failed = [], []
    for key in ("FULL",) + ZONES:
        if key not in made:                                      # a zone without violations has no file - nothing to send
            continue
        to, cc = recipients(key)
        subject = f"MTF Concentration Report - {'FULL FILE' if key == 'FULL' else key} - {d}"
        body = full_body(d) if key == "FULL" else zone_body(key, d)
        try:
            send_one(subject, body, base / outs[key], to, cc, sleep=sleep, smtp_factory=smtp_factory)
            sent.append(key)
        except Exception as e:                                   # noqa: BLE001
            failed.append(f"{key}: {e}")
            log(f"E-mail {key} FAILED: {e}")
    return {"sent": sent, "failed": failed}


def backup_and_cleanup(base: Path = None) -> Path:
    """Same rules as the hand script: inputs + outputs move to BACKUPFILES AUTOMATED\\<16th Nov 2026>; EXCEPTION.xlsx is only copied."""
    base = Path(base or root())
    dest = backup_root() / dstr(_today())
    dest.mkdir(parents=True, exist_ok=True)
    d = _dd()
    names = [f"MTFPOSITION_{d}.csv", f"RISK_REPORT_{d}.csv", NIFTY_FILE, BSE_FILES[1][3], BSE_FILES[0][3], EXCEPTION]
    names += [p.name for p in base.glob(SHORTAGE_GLOB)]
    names += list(outputs().values())
    for n in names:
        p = base / n
        if not p.is_file():
            continue
        if n == EXCEPTION:
            shutil.copy2(p, dest / n)
            log(f"copied to backup (kept in the folder): {n}")
        else:
            shutil.move(str(p), str(dest / n))
            log(f"moved to backup: {n}")
    return dest


# ───────────────────────────── failure alert ─────────────────────────────
def send_alert(headline, detail, sender=None) -> dict:
    """WhatsApp FAILURE ALERT picture (same as the other cards). Never raises."""
    rec = {"sent": False, "reason": ""}
    try:
        from core.scheduler import connections, failure_alert, helpers as H, paths
        raw = get_settings().get("alert_numbers") or ""
        if not raw.strip():
            try:
                from core import ms15_auto
                raw = ms15_auto.get_settings().get("alert_numbers", "")
            except Exception:
                raw = ""
        c = connections.get("whatsapp")
        numbers = failure_alert.parse_numbers(raw) or failure_alert.parse_numbers(c.get("alert_numbers", ""))
        if not numbers:
            rec["reason"] = "no alert number set"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        if sender is not None:
            sender(numbers, headline, detail)
            rec.update(sent=True, reason="sent")
            return rec
        if not c.get("auth_token"):
            rec["reason"] = "WhatsApp auth token is not set"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        now = dt.datetime.now()
        img = failure_alert.render_image(paths.sched_root() / "alerts" / f"mtfconc_{int(time.time())}.png", process=NAME,
                                         schedule="Auto run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-",
                                         trigger="Auto run", attempt_text="final", headline=headline, detail=detail,
                                         run_id="MTFCONC", kind="FAILED")

        class _C:
            def log(self, m):
                log(m)
        ok, bad = [], []
        for num in numbers:
            try:
                H.whatsapp_send_image(img, num, f"FAILURE ALERT - {NAME}", now.strftime("%d-%m-%Y %H:%M"), _C(),
                                      template_name=(c.get("alert_template") or "").strip() or None)
                ok.append(num[-4:])
            except Exception as e:                               # noqa: BLE001
                bad.append(f"{num[-4:]}: {type(e).__name__}: {str(e)[:100]}")
        rec.update(sent=bool(ok), reason=("sent to ..." + ", ...".join(ok)) if ok else "all sends failed: " + "; ".join(bad))
        log("WhatsApp failure alert " + rec["reason"])
    except Exception as e:                                       # noqa: BLE001
        rec["reason"] = f"{type(e).__name__}: {e}"
        log("WhatsApp failure alert error: " + rec["reason"])
    return rec


# ───────────────────────────── the run ─────────────────────────────
def is_running() -> bool:
    return _RUN_LOCK.locked()


def needs_password() -> bool:
    return read_state().get("status") == "done"


def run(trigger="manual", by="", alert=None) -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy(f"{NAME} is already running.")
    alert = alert or send_alert
    base = root()
    _ACTIVE["cancel"] = False
    _ACTIVE["log_file"] = _open_run_log(trigger, by)
    _update(status="running", started=_iso(), finished="", error="", trigger=trigger, by=by, steps={}, mailed=False, sent=[],
            failed_mail=[], rows_zone=[])
    result = {"ok": False}
    try:
        log(f"=== {NAME} started ({trigger}{', ' + by if by else ''}) ===")
        # 1) make sure every input is there: only what is missing is fetched / downloaded
        outs = {}
        for n in ("korp", "index", "mtfpos", "risk", "exception"):
            _check_cancel()
            outs[n] = _do_step(n, base)
        bad = [f"{STEP_LABEL[k]}: {v.get('msg', '')}" for k, v in outs.items() if not v.get("ok")]
        _set_step("calc", "running")
        try:
            pre_run_check(base)
        except PreRunError as e:
            raise PreRunError(f"{e}" + (f"  [{'; '.join(bad)}]" if bad else ""))
        stage_inputs(base)
        _check_cancel()
        res = step_calc(base)
        _set_step("calc", "ok", f"full file + {len(res['zones'])} zone file(s)" + (" · COPY TO ENTERPRISE RISK FAILED" if res["copy_failed"] else ""))
        _update(made=res["made"], copy_failed=res["copy_failed"])
        _check_cancel()
        # 2) e-mails
        if get_settings().get("send_mail", True):
            _set_step("email", "running")
            m = step_email(base, res["made"])
            _update(sent=m["sent"], failed_mail=m["failed"], mailed=bool(m["sent"]))
            _set_step("email", "failed" if m["failed"] else "ok", ("sent: " + ", ".join(m["sent"])) + ("; FAILED: " + "; ".join(m["failed"]) if m["failed"] else ""))
            if m["failed"]:
                raise RuntimeError("some e-mails did not go (files are kept, not backed up): " + "; ".join(m["failed"]))
        else:
            _set_step("email", "skipped", "e-mail is switched off in Settings")
        # 3) backup
        _set_step("backup", "running")
        dest = backup_and_cleanup(base)
        _set_step("backup", "ok", str(dest))
        _update(status="done", finished=_iso(), error="")
        log(f"=== {NAME} finished ===")
        result = {"ok": True, "made": res["made"]}
        if res["copy_failed"]:
            alert("The full file was e-mailed but could not be copied to the Enterprise Risk folder.", get_settings().get("copy_dir", ""))
    except Cancelled:
        _update(status="stopped", finished=_iso(), error="Stopped.")
        log("Stopped.")
        result = {"ok": False, "stopped": True}
    except Exception as e:                                       # noqa: BLE001
        msg = f"{type(e).__name__}: {e}" if not isinstance(e, (PreRunError,)) else str(e)
        _update(status="failed", finished=_iso(), error=msg[:600])
        log("FAILED: " + msg)
        rec = alert(f"The {NAME} run failed.", msg[:600])
        _update(alert_sent=bool(rec.get("sent")), alert_reason=rec.get("reason", ""))
        result = {"ok": False, "error": msg}
    finally:
        f = _ACTIVE.get("log_file")
        _ACTIVE["log_file"] = None
        if f:
            try:
                f.close()
            except Exception:
                pass
        _RUN_LOCK.release()
    return result


def request_run(by="", password=None, background=True) -> dict:
    if is_running():
        raise Busy(f"{NAME} is already running.")
    if needs_password():
        from core import short_allocation
        if not password or not short_allocation.check_password(password):
            return {"ok": False, "need_password": True, "msg": "Today's run already succeeded - enter the password to run it again."}
    if background:
        threading.Thread(target=lambda: run("manual", by), name="mtfconc-manual", daemon=True).start()
        return {"ok": True, "started": True}
    return run("manual", by)


FETCH_KINDS = {"korp": "korp", "index": "index", "mtfpos": "mtfpos"}


def any_fetching(exclude: str = "") -> bool:
    return any(k != exclude and l.locked() for k, l in list(_STEP_LOCKS.items()))


def fetch_one(what: str, by="") -> dict:
    """The single-download buttons. A button always downloads fresh (korp / index); MTFPOSITION skips a good copy."""
    if what not in FETCH_KINDS:
        raise ValueError("unknown download")
    if is_running():
        raise Busy("A full run is going - wait for it or Stop it.")
    lk = _STEP_LOCKS[what]
    if not lk.acquire(blocking=False):
        raise Busy(f"{STEP_LABEL[what]} is already downloading - wait for it to finish.")

    def go():
        if not any_fetching(exclude=what):
            _ACTIVE["cancel"] = False
        try:
            log(f"{STEP_LABEL[what]}: started by {by or 'user'}")
            _do_step(what, root(), force=True, have_lock=True)
        finally:
            lk.release()
    threading.Thread(target=go, name=f"mtfconc-fetch-{what}", daemon=True).start()
    return {"ok": True, "started": what}


def stop_running(by="") -> bool:
    if not is_running() and not any_fetching():
        return False
    _ACTIVE["cancel"] = True
    for p in list(_ACTIVE["procs"]):
        _kill(p)
    log(f"Stop requested by {by or 'user'}.")
    return True


# ───────────────────────────── the timer: 08:40 downloads, 09:15 MTFPOSITION, 09:30 run ─────────────────────────────
def _at(hhmm: str) -> dt.time:
    h, m = hhmm.split(":")
    return dt.time(int(h), int(m))


def _trading_day(d: dt.date) -> bool:
    try:
        from core.scheduler import helpers as H
        return bool(H.is_trading_day(d))
    except Exception:
        return d.weekday() < 5


def _inputs_ok(keys, base=None) -> bool:
    by = {r["key"]: r for r in input_status(base)}
    return all(by[k]["ok"] for k in keys)


RETRY_S = 600           # a failed automatic job is tried again every 10 minutes ...
MAX_TRIES = 4           # ... 4 times, then ONE failure alert
MTF_RETRY_S = 300


def tick(now: dt.datetime = None, alert=None):
    """One look at the clock (called every ~20 s). Every job is started at most once per attempt window, never twice at once."""
    now = now or dt.datetime.now()
    s = get_settings()
    if not s.get("auto") or not _trading_day(now.date()) or now.time() >= LATE_CUTOFF:
        return
    if is_running() or any_fetching():
        return
    alert = alert or send_alert
    st = read_state()
    sc = st["sched"]
    base = root()

    def due(key, at_s, every):
        j = sc.get(key, {})
        if j.get("done") or j.get("gave_up"):
            return False
        if now.time() < _at(at_s):
            return False
        last = j.get("last")
        return not last or (now - dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() >= every

    def failed_out(key, headline, detail):
        n = int(read_state()["sched"].get(key, {}).get("tries", 0))
        if n >= MAX_TRIES:
            _sched_mark(key, gave_up=True)
            rec = alert(headline, detail[:600])
            log(f"{headline} (gave up after {n} tries; alert {'sent' if rec.get('sent') else 'not sent'})")

    # 08:40 - KORP shortage file + the 3 index files
    if due("dl", s["download_at"], RETRY_S):
        _sched_mark("dl", last=_iso(now), tries=int(sc.get("dl", {}).get("tries", 0)) + 1)
        log(f"auto download (try {sc.get('dl', {}).get('tries', 0) + 1})")
        _ACTIVE["cancel"] = False
        res = {n: _do_step(n, base) for n in DOWNLOADS}
        if all(r.get("ok") for r in res.values()):
            _sched_mark("dl", done=True)
        else:
            failed_out("dl", f"{NAME}: the 08:40 downloads failed.",
                       "; ".join(f"{STEP_LABEL[k]}: {v.get('msg', '')}" for k, v in res.items() if not v.get("ok")))
        return
    # 09:15 - MTFPOSITION from the FTP
    if due("mtf", s["mtf_at"], MTF_RETRY_S):
        _sched_mark("mtf", last=_iso(now), tries=int(sc.get("mtf", {}).get("tries", 0)) + 1)
        r = _do_step("mtfpos", base)
        if r.get("ok"):
            _sched_mark("mtf", done=True)
        elif int(read_state()["sched"].get("mtf", {}).get("tries", 0)) >= 18:       # ~90 minutes of trying
            _sched_mark("mtf", gave_up=True)
            alert(f"{NAME}: MTFPOSITION is still not on the FTP.", r.get("msg", ""))
        return
    # 09:30 - the run (waits for MTFPOSITION / downloads that are still coming, up to the give-up point)
    if due("run", s["run_at"], RETRY_S // 2) and st.get("status") not in ("running", "done"):
        ready = _inputs_ok(("MTFPOSITION", "RISK_REPORT", "SHORTAGE", "NIFTY50", "BSE200", "BSE500", "EXCEPTION"), base)
        n = int(sc.get("run", {}).get("tries", 0))
        late = now.time() >= dt.time(11, 30)
        if not ready and not late:
            _sched_mark("run", last=_iso(now))                        # not all here yet: look again in a few minutes
            log("09:30 run is waiting for its input files…")
            return
        _sched_mark("run", last=_iso(now), tries=n + 1)
        r = run("auto", "auto (timer)", alert=alert)
        if r.get("ok") or r.get("stopped"):
            _sched_mark("run", done=True)
        else:
            _sched_mark("run", gave_up=True)                      # run() already sent its own failure alert


def start_ticker(interval_s: int = 20):
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return False
    _TICK["stop"].clear()

    def loop():
        log("timer started (downloads / MTFPOSITION / run).")
        while not _TICK["stop"].wait(interval_s):
            try:
                tick()
            except Exception as e:                               # noqa: BLE001
                print(f"[mtfconc] timer error: {type(e).__name__}: {e}", flush=True)
    th = threading.Thread(target=loop, name="mtfconc-timer", daemon=True)
    _TICK["thread"] = th
    th.start()
    return True


def stop_ticker():
    _TICK["stop"].set()


# ───────────────────────────── files / backup listing ─────────────────────────────
def list_files(sub: str = "") -> list:
    base = root() if not sub else backup_root() / sub
    out = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".part"):
                out.append({"name": p.name, "size": p.stat().st_size,
                            "time": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S")})
    return out


def list_backup_dates() -> list:
    b = backup_root()
    if not b.is_dir():
        return []
    ds = [(parse_bname(p.name), p.name) for p in b.iterdir() if p.is_dir() and parse_bname(p.name)]
    return [n for _d, n in sorted(ds, reverse=True)]


def file_path(name: str, sub: str = "") -> Path:
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("bad file name")
    p = (root() if not sub else backup_root() / _check_date(sub)) / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return p


def dates_between(a: dt.date, b: dt.date) -> list:
    out, d = [], a
    while d <= b:
        out.append(d)
        d += dt.timedelta(days=1)
    return out


def range_listing(a: dt.date, b: dt.date) -> list:
    rows = []
    for d in dates_between(a, b):
        for f in list_files(dstr(d)):
            rows.append(dict(f, date=dstr(d)))
    return rows


def zip_range(a: dt.date, b: dt.date) -> Path:
    import tempfile
    z = Path(tempfile.gettempdir()) / f"MTF_CONCENTRATION_{a:%d%m%Y}_{b:%d%m%Y}_{int(time.time())}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dates_between(a, b):
            for f in list_files(dstr(d)):
                zf.write(backup_root() / dstr(d) / f["name"], f"{dstr(d)}/{f['name']}")
    return z


def accept_upload(name: str, data: bytes, by="") -> dict:
    """Upload files: kept under their own name in this folder (EXCEPTION.xlsx replaces the permanent list)."""
    name = os.path.basename(name or "")
    if not name or name.startswith(".") or name.lower().endswith((".py", ".bat", ".exe", ".part")):
        raise ValueError("this file name is not allowed")
    if not data:
        raise ValueError("the file is empty")
    if re.match(r"^(RISK_REPORT|MTFPOSITION)_", name, re.I):
        raise ValueError(f"{name}: this file is read from COMMON FILES - put it there (Upload Common Files), not here")
    part = root() / (name + ".part")
    part.write_bytes(data)
    part.replace(root() / name)
    log(f"Uploaded {name} ({len(data):,} bytes) by {by or 'user'}")
    return {"ok": True, "name": name, "size": len(data)}


def status() -> dict:
    st = read_state()
    running = is_running()
    s = st.get("status", "idle")
    if s == "running" and not running:
        s = "failed"
        st["error"] = st.get("error") or "Interrupted (the server restarted while it was running)."
    set_ = get_settings()
    inputs = input_status()
    steps = {k: dict(v) for k, v in (st.get("steps") or {}).items()}
    by = {r["key"]: r for r in inputs}
    if not running:        # a step shown as failed but whose file is now there (uploaded by hand) is shown as present
        for step, keys in (("korp", ("SHORTAGE",)), ("index", ("NIFTY50", "BSE200", "BSE500")), ("mtfpos", ("MTFPOSITION",)),
                           ("risk", ("RISK_REPORT",)), ("exception", ("EXCEPTION",))):
            cur = (steps.get(step) or {}).get("status", "")
            if cur in ("failed", "") and all(by[k]["ok"] for k in keys):
                steps[step] = {"status": "done", "msg": "file(s) in place: " + ", ".join(by[k]["name"] for k in keys), "at": (steps.get(step) or {}).get("at", "")}
    sc = st.get("sched", {})
    return {"name": NAME, "date": st.get("date"), "status": "running" if running else s, "running": running,
            "fetching": any_fetching(), "started": st.get("started", ""), "finished": st.get("finished", ""),
            "error": st.get("error", ""), "trigger": st.get("trigger", ""), "by": st.get("by", ""),
            "made": st.get("made", []), "sent": st.get("sent", []), "failed_mail": st.get("failed_mail", []),
            "copy_failed": bool(st.get("copy_failed")), "alert_sent": bool(st.get("alert_sent")), "alert_reason": st.get("alert_reason", ""),
            "auto": bool(set_["auto"]), "needs_password": s == "done" and not running,
            "settings": {k: set_[k] for k in ("auto", "download_at", "mtf_at", "run_at", "send_mail", "copy_dir", "alert_numbers")},
            "sched": {k: {"done": bool(v.get("done")), "gave_up": bool(v.get("gave_up")), "tries": v.get("tries", 0)} for k, v in sc.items()},
            "steps": [dict(key=k, label=STEP_LABEL[k], **steps.get(k, {"status": "", "msg": ""})) for k in STEP_ORDER],
            "inputs": inputs, "folder": str(root()), "log": recent_log(60)}
