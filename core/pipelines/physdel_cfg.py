"""PHYSICAL DELIVERY — settings, file picking, run-window dates and the Safe test marker.

Everything the PHYSICAL DELIVERY pipeline needs that is NOT the report logic itself lives here, so the two report
scripts (physdel_codes/ltp.py and physdel_codes/send.py) only read plain values from this one module.

Folders (all inside the console's own data unless .env says otherwise):
    PHYSICAL_DELIVERY_FOLDER      base folder            default  <console>\\data\\PHYSICAL DELIVERY
    PHYSICAL_DELIVERY_INPUT       today's PositionBook   default  <base>\\INPUT
    <base>\\MAIN_FOLDER            master files (mail list, branch/region list, BRM list) - updated now and then
    <base>\\BACKUPFILES AUTOMATED\\<DD-MM-YYYY>\\<HH-MM-SS>   everything a finished run moves out of the way

Files a run takes from OTHER places (read only, never moved):
    CTCL vs NMASS output   the latest backup of CTCL vs NMASS  (CTCL_NMASS_FOLDER\\BACKUPFILES AUTOMATED\\<date>\\<run>)
    RISK_REPORT            today's file in COMMON FILES        (SA_COMMON_DIR)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]


# ───────────────────────── folders ─────────────────────────
def base() -> str:
    return os.getenv("PHYSICAL_DELIVERY_FOLDER") or str(REPO / "data" / "PHYSICAL DELIVERY")


def input_dir() -> str:
    return os.getenv("PHYSICAL_DELIVERY_INPUT") or os.path.join(base(), "INPUT")


def main_dir() -> str:
    return os.path.join(base(), "MAIN_FOLDER")


def backup_dir() -> str:
    return os.path.join(base(), "BACKUPFILES AUTOMATED")


def common_dir() -> str:
    return os.getenv("SA_COMMON_DIR") or r"C:\COMMON FILES"


def ctcl_base() -> str:
    return os.getenv("CTCL_NMASS_FOLDER") or r"D:\CTCL VS NMASS"


# ───────────────────────── settings kept in a small json next to the data ─────────────────────────
def _state_path() -> Path:
    return Path(base()) / "pd_state.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def parse_percent(v) -> float:
    """'2' / '2.5' / ' 3 % ' -> 2.0 / 2.5 / 3.0.  Raises ValueError for anything that is not a plain number above 0."""
    s = str(v if v is not None else "").strip().replace("%", "").strip()
    if s == "":
        raise ValueError("Please enter the CASH percentage.")
    x = float(s)                                    # ValueError for text
    if not (0 < x <= 100):
        raise ValueError("The percentage must be more than 0 and at most 100.")
    return x


def get_percent() -> float:
    """The CASH % chosen in the Run popup (default 2)."""
    try:
        return parse_percent(_load_state().get("percent", 2))
    except Exception:
        return 2.0


def set_percent(v) -> float:
    x = parse_percent(v)
    st = _load_state()
    st["percent"] = x
    st["percent_set_at"] = dt.datetime.now().isoformat(timespec="seconds")
    _save_state(st)
    return x


# ───────────────────────── recipients / numbers (read from .env, original values are the defaults) ─────────────────────────
def email_list(key: str, default: Optional[List[str]] = None) -> List[str]:
    raw = os.getenv(key)
    if raw is None:
        return list(default or [])
    return [e.strip().strip("'\"") for e in re.split(r"[,;\n]+", raw) if e.strip()]


def wa_numbers(list_name: str, default: Optional[List[str]] = None) -> List[str]:
    """PD_WA_<LIST> = '9899547339 #Sumit, 9711667470' -> ['9899547339', '9711667470'] (names after # are ignored)."""
    raw = os.getenv(f"PD_WA_{list_name.upper()}")
    if raw is None:
        return list(default or [])
    out: List[str] = []
    for part in re.split(r"[,;\n]+", raw):
        digits = "".join(ch for ch in part.split("#")[0] if ch.isdigit())
        if len(digits) >= 10:
            n = digits[-10:] if len(digits) > 10 else digits
            if n not in out:
                out.append(n)
    return out


# ───────────────────────── the three inputs that come from other places ─────────────────────────
_CTCL_MAIN = re.compile(r"^FULL_FILE_CTCL_VS_NMASS_(\d{2})\.xlsx$", re.I)


def _date_from(part: str) -> Optional[dt.date]:
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", part.strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def pick_ctcl_file(root: Optional[str] = None) -> Optional[dict]:
    """The CTCL vs NMASS MAIN output (FULL_FILE_CTCL_VS_NMASS_<NN>.xlsx - not the zone variants) from the LATEST backup:
    the newest date folder, and inside it the highest run number.  Returns None when there is none."""
    rootp = Path(root or os.path.join(ctcl_base(), "BACKUPFILES AUTOMATED"))
    if not rootp.is_dir():
        return None
    best = None
    for dp, _dirs, files in os.walk(rootp):
        for fn in files:
            m = _CTCL_MAIN.match(fn)
            if not m:
                continue
            rel = Path(dp).relative_to(rootp).parts
            d = next((x for x in (_date_from(p) for p in rel) if x), None)
            if d is None:
                continue
            run = next((int(p) for p in rel if p.isdigit() and len(p) <= 2), int(m.group(1)))
            full = os.path.join(dp, fn)
            try:
                mt = os.path.getmtime(full)
            except OSError:
                continue
            key = (d, run, mt)
            if best is None or key > best[0]:
                best = (key, full, d, run, mt)
    if best is None:
        return None
    _k, full, d, run, mt = best
    return {"path": full, "name": os.path.basename(full), "date": d.isoformat(), "run": run,
            "mtime": dt.datetime.fromtimestamp(mt).isoformat(timespec="seconds"),
            "where": f"CTCL vs NMASS backup {d:%d-%m-%Y} / run {run:02d}"}


def risk_report(today: Optional[dt.date] = None) -> Optional[dict]:
    """Today's RISK_REPORT_<DDMMYYYY>.csv in COMMON FILES (case-insensitive name match)."""
    today = today or dt.date.today()
    want = f"RISK_REPORT_{today:%d%m%Y}.csv".lower()
    d = Path(common_dir())
    try:
        for fn in os.listdir(d):
            if fn.lower() == want:
                full = str(d / fn)
                return {"path": full, "name": fn,
                        "mtime": dt.datetime.fromtimestamp(os.path.getmtime(full)).isoformat(timespec="seconds"),
                        "where": "COMMON FILES"}
    except OSError:
        pass
    return None


def positionbook_max_minutes() -> int:
    try:
        return max(1, int(os.getenv("PD_POSITIONBOOK_MAX_MIN", "15")))
    except ValueError:
        return 15


def _newest_positionbook() -> Optional[str]:
    d = Path(input_dir())
    try:
        cands = [d / f for f in os.listdir(d)
                 if re.fullmatch(r"positionbook.*\.(csv|xlsx)", f, re.I) and not f.startswith("~$")]
    except OSError:
        return None
    return str(max(cands, key=lambda p: p.stat().st_mtime)) if cands else None


def positionbook(now: Optional[float] = None) -> Optional[str]:
    """Newest PositionBook*.csv / *.xlsx in INPUT - but ONLY if it was saved in the last 15 minutes
    (PD_POSITIONBOOK_MAX_MIN).  A PositionBook is a live snapshot; an older one is never picked."""
    p = _newest_positionbook()
    if not p:
        return None
    import time
    age_min = ((now if now is not None else time.time()) - os.path.getmtime(p)) / 60.0
    return p if age_min <= positionbook_max_minutes() else None


def positionbook_stale() -> Optional[dict]:
    """The newest PositionBook when it exists but is too old to use (else None) - so the screen can say why."""
    p = _newest_positionbook()
    if not p or positionbook():
        return None
    mt = os.path.getmtime(p)
    return {"name": os.path.basename(p), "mtime": dt.datetime.fromtimestamp(mt).isoformat(timespec="seconds"),
            "age_min": int((dt.datetime.now().timestamp() - mt) // 60), "max_min": positionbook_max_minutes()}


def positionbook_info() -> Optional[dict]:
    p = positionbook()
    if not p:
        return None
    return {"path": p, "name": os.path.basename(p),
            "mtime": dt.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds"),
            "where": f"uploaded (INPUT folder), must be under {positionbook_max_minutes()} min old"}


MASTER_FILES = [      # (label, accepted names - first one that exists is used)
    ("Mail list", ["Copy of T1 MAIL LIST.xlsx"]),
    ("Branch & Region list", ["Branch_AND_Region_LIST.xlsx"]),
    ("BRM list", ["BRM LIST_File.xlsx", "BRM LIST_File.csv"]),
]
MASTER_GLOBS = [n for _l, names in MASTER_FILES for n in names]


def master_status() -> List[dict]:
    out = []
    for label, names in MASTER_FILES:
        hit = next((n for n in names if os.path.isfile(os.path.join(main_dir(), n))), None)
        out.append({"label": label, "name": hit or names[0], "present": bool(hit),
                    "mtime": (dt.datetime.fromtimestamp(os.path.getmtime(os.path.join(main_dir(), hit)))
                              .isoformat(timespec="seconds") if hit else None)})
    return out


def inputs_report(today: Optional[dt.date] = None) -> dict:
    """Everything the next run will use, in plain terms - shown on the card and printed at the start of a run."""
    return {"positionbook": positionbook_info(), "positionbook_stale": positionbook_stale(), "ctcl": pick_ctcl_file(), "risk": risk_report(today),
            "masters": master_status(), "percent": get_percent(),
            "folders": {"base": base(), "input": input_dir(), "master": main_dir(),
                        "common": common_dir(), "ctcl_backup": os.path.join(ctcl_base(), "BACKUPFILES AUTOMATED")}}


def missing_inputs(rep: Optional[dict] = None) -> List[str]:
    rep = rep or inputs_report()
    miss: List[str] = []
    if not rep["positionbook"]:
        st = rep.get("positionbook_stale")
        if st:
            miss.append(f"PositionBook '{st['name']}' is {st['age_min']} min old - only a file saved in the last "
                        f"{st['max_min']} minutes is used. Upload a fresh PositionBook with 'Upload files'.")
        else:
            miss.append(f"PositionBook (upload it with 'Upload files' - expected in {input_dir()})")
    if not rep["ctcl"]:
        miss.append("CTCL vs NMASS output FULL_FILE_CTCL_VS_NMASS_<NN>.xlsx (no CTCL vs NMASS backup has it yet)")
    if not rep["risk"]:
        miss.append(f"RISK_REPORT_{dt.date.today():%d%m%Y}.csv (not in COMMON FILES: {common_dir()})")
    for m in rep["masters"]:
        if not m["present"]:
            miss.append(f"Master file '{m['name']}' ({m['label']}) - upload it with 'Upload files > MASTER'")
    return miss


# ───────────────────────── run window: last Tuesday of the month and the 5 trading days before it ─────────────────────────
WINDOW_DAYS = 5


def _holidays() -> set:
    try:
        from core.scheduler import helpers as H
        return set(H.holidays())
    except Exception:
        return set()


def _is_td(d: dt.date, hol: set) -> bool:
    return d.weekday() < 5 and d not in hol


def last_tuesday(year: int, month: int) -> dt.date:
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - dt.timedelta(days=1)
    while d.weekday() != 1:
        d -= dt.timedelta(days=1)
    return d


def expiry_date(year: int, month: int, hol: Optional[set] = None) -> dt.date:
    """The month's physical-delivery expiry day: the last Tuesday, or the trading day before it if that is a holiday."""
    hol = _holidays() if hol is None else hol
    d = last_tuesday(year, month)
    while not _is_td(d, hol):
        d -= dt.timedelta(days=1)
    return d


def _back_trading_days(d: dt.date, n: int, hol: set) -> dt.date:
    while n > 0:
        d -= dt.timedelta(days=1)
        if _is_td(d, hol):
            n -= 1
    return d


def runs_today(today: Optional[dt.date] = None) -> int:
    today = today or dt.date.today()
    return sum(1 for r in _load_state().get("runs", []) if r.get("date") == today.isoformat() and r.get("ok") and not r.get("test"))


def window_info(today: Optional[dt.date] = None, hol: Optional[set] = None) -> dict:
    """Where today sits relative to the next expiry.  Information only - the Run button is never blocked.
    phase: 'expiry_day' | 'window' | 'before' (the next window has not opened yet)."""
    today = today or dt.date.today()
    hol = _holidays() if hol is None else hol
    exp = expiry_date(today.year, today.month, hol)
    if today > exp:
        y, m = (today.year + (today.month == 12), today.month % 12 + 1)
        exp = expiry_date(y, m, hol)
    start = _back_trading_days(exp, WINDOW_DAYS, hol)
    n = runs_today(today)
    if today == exp:
        phase = "expiry_day"
        text = f"Expiry day ({exp:%a %d %b}) - run as many times as needed. Runs today: {n}."
    elif start <= today < exp:
        phase = "window"
        left = sum(1 for k in range(1, (exp - today).days) if _is_td(today + dt.timedelta(days=k), hol))
        td = "" if _is_td(today, hol) else " (market holiday today)"
        text = (f"Window open{td} - run once a day, {left + 1} trading day(s) to expiry ({exp:%a %d %b}). "
                + ("Already run today." if n else "Not run yet today."))
    else:
        phase = "before"
        text = f"Not due. Next window opens {start:%a %d %b}; expiry {exp:%a %d %b}."
    return {"phase": phase, "text": text, "expiry": exp.isoformat(), "window_start": start.isoformat(),
            "runs_today": n, "today_is_trading_day": _is_td(today, hol)}


def record_run(ok: bool, percent: float, test: bool = False, by: str = "") -> None:
    st = _load_state()
    runs = [r for r in st.get("runs", []) if r.get("date", "") >= (dt.date.today() - dt.timedelta(days=45)).isoformat()]
    runs.append({"date": dt.date.today().isoformat(), "time": dt.datetime.now().strftime("%H:%M:%S"),
                 "ok": bool(ok), "percent": percent, "test": bool(test), "by": by})
    st["runs"] = runs
    _save_state(st)


def last_run() -> Optional[dict]:
    runs = _load_state().get("runs", [])
    return runs[-1] if runs else None


# ───────────────────────── Safe test (the same one-shot idea as MS15 / PROBABLE) ─────────────────────────
def _marker() -> Path:
    return Path(base()) / ".pd_safe_test_armed"


def arm_safe_test() -> None:
    p = _marker()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


def disarm_safe_test() -> bool:
    p = _marker()
    was = p.exists()
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    return was


def is_safe_test_armed() -> bool:
    return _marker().exists()


def consume_safe_test() -> bool:
    """Called once at the start of a run: spends the arm and switches this process into test mode."""
    if disarm_safe_test():
        os.environ["PD_TEST_MODE"] = "1"
        return True
    return False


def test_mode() -> bool:
    return os.getenv("PD_TEST_MODE", "") == "1"


def test_email_to() -> List[str]:
    raw = os.getenv("PD_TEST_EMAIL_TO") or os.getenv("PROBABLE_TEST_EMAIL_TO") or os.getenv("MS15_TEST_EMAIL_TO") or ""
    return [e.strip() for e in re.split(r"[,;]+", raw) if e.strip()]


def test_wa_to() -> str:
    raw = os.getenv("PD_TEST_WA_TO") or os.getenv("PROBABLE_TEST_WA_TO") or os.getenv("MS15_TEST_WA_TO") or ""
    return raw.split(",")[0].strip()


def test_max_clients() -> int:
    try:
        return max(1, int(os.getenv("PD_TEST_MAX_CLIENTS", "3")))
    except ValueError:
        return 3


def safe_test_targets() -> Dict[str, object]:
    return {"armed": is_safe_test_armed(), "email_to": test_email_to(), "wa_to": test_wa_to(),
            "max_clients": test_max_clients()}


# ───────────────────────── defaults shown in the Email / Credentials windows ─────────────────────────
DEFAULT_ENV = {      # only set when .env does not have them (so the windows show what really gets used)
    "PD_ZONE_CHND_TO": "rsharda@mastertrust.co.in,rms_chd@mastertrust.co.in,parveenrawat1910@mastertrust.co.in,"
                       "surinder.kumar@mastertrust.co.in,kulbeersingh@mastertrust.co.in",
    "PD_ZONE_CHND_CC": "RMS_DEL@mastertrust.co.in",
    "PD_ZONE_LUDH_TO": "ludhianacompliance@mastertrust.co.in,kuljit.singh@mastertrust.co.in,amandeep.singh2@mastertrust.co.in",
    "PD_ZONE_LUDH_CC": "rms_del@mastertrust.co.in",
    "PD_ZONE_MUMB_TO": "pradip.mandavkar@mastertrust.co.in",
    "PD_ZONE_MUMB_CC": "RMS_DEL@mastertrust.co.in",
    "PD_ZONE_DELH_TO": "RMS_DEL@mastertrust.co.in",
    "PD_ZONE_DELH_CC": "",
    "PD_LOG_TO": "rms_del@mastertrust.co.in",
    "PD_LOG_CC": "",
    "PD_WA_DELHI": "9899547339, 9711667470, 9871410617",
    "PD_WA_LUDHIANA": "9888556665",
    "PD_WA_CHANDIGARH": "9914122055, 9814566866",
    "PD_WA_MUMBAI": "8655253655",
    "PD_WA_FULL": "7889026088, 9899547339, 9711667470, 9871410617",
}


def apply_defaults() -> None:
    for k, v in DEFAULT_ENV.items():
        os.environ.setdefault(k, v)
    os.environ.setdefault("PHYSICAL_DELIVERY_FOLDER", base())
    os.environ.setdefault("PHYSICAL_DELIVERY_INPUT", input_dir())
