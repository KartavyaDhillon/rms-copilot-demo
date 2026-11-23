"""core/obligation_card.py - OBLIGATION REPORT card (tcs_nmass.py + obligation_new_final.py as one console card).

Eight times a day (default 09:30 10:30 11:30 12:30 14:00 16:30 18:00 19:30 - editable in the card's Credentials window):

    MAIN_<hh.mm>.csv (uploaded in the card)
      + FO client-level collaterals   (NSCCL / NMASS - captcha filled automatically)
      + MCX Margin Utilization View   (eclear RSK335 -> SearchResults xlsx - RSA code filled automatically)
      -> the detailed obligation picture (core/obligation_image.py, drawn in Python - no Excel)
      -> one e-mail (picture inside the mail) + WhatsApp (template daily_report)
      -> BACKUP FILES AUTOMATED\\<dd-mm-yyyy>\\<hh-mm-AM|PM>\\  (MAIN, FO, SearchResults, picture).

The times are RELAXED: a missing or late run is never an alert. It shows "waiting" in the card, and "ATTENTION" in the console
only when a slot is more than `attention_min` (default 60) minutes late. A WhatsApp alert goes out ONLY when a run FAILS.
"Mark done" (override) records the runs that were already done by hand today, so the counter (e.g. 2/8) continues from the next slot.

Nothing secret is in the code: eclear / NMASS logins = Credentials (shared with the other downloads), mail login = this card's
sender or the shared one, To / CC = the Email To / CC window (defaults from the old script), WhatsApp numbers = this card's Settings
or the common list.
"""
from __future__ import annotations

import collections
import datetime as dt
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

from core import cnc_f11_card as _cnc          # name_date / freshness_problem / dates_between are the same rules

NAME = "OBLIGATION REPORT"
FOLDER_NAME = "OBLIGATION REPORT"
PIPE = "obligation"
REPO = Path(__file__).resolve().parent.parent
BACKUP_SUB = "BACKUP FILES AUTOMATED"
DEFAULT_TIMES = "09:30,10:30,11:30,12:30,14:00,16:30,18:00,19:30"
MAX_SLOTS = 12
STEP_LABEL = {"input": "MAIN file", "fo": "FO Level-1 margins (NMASS)", "mcx": "MCX Margin Utilization", "build": "Picture",
              "send": "E-mail + WhatsApp", "backup": "Backup"}
STEP_ORDER = ("input", "fo", "mcx", "build", "send", "backup")
MAIL_DEFAULTS = {"REPORT": ("sukhbirsingh@mastertrust.co.in, hs@mastertrust.co.in, rks@mastertrust.co.in",
                            "rms_del@mastertrust.co.in, sumit.aggarwal@mastertrust.co.in, sandeep.kumar2@mastertrust.co.in"),
                 "LATE": ("", "")}              # blank late list = the same people as the early list
MAIL_LABEL = {"REPORT": "Daily Obligation Report (up to the split time, default 17:00)",
              "LATE": "Daily Obligation Report (after the split time until 00:30)"}
LATE_END_MIN = 30                              # the "late" group runs until 00:30 after midnight
MAIL_TRIES = 5
DL_TRIES = 2                                   # each download is tried this many times inside one run
DL_GAP_S = 60                                  # seconds between the two tries
AUTO_TRIES = 2                                 # a slot the timer started is tried this many times
AUTO_GAP_MIN = 10
EARLY_MIN = 10                                 # a hand run up to this many minutes before a slot's time counts for that slot
SIGNATURE = ("Kartavya Dhillon<br>Master Capital Services Ltd<br>📞 011-66331000 | Ext. 84")

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_LOG = collections.deque(maxlen=400)
_ACTIVE: dict = {"cancel": False, "log_file": None, "thread": None, "proc": None, "slot": None}
_TICK = {"thread": None, "stop": threading.Event()}
_MAIN_RX = re.compile(r"^MAIN[_ -]?(\d{1,2})[._](\d{2})(?!\d).*\.csv$", re.I)
_FO_RX = re.compile(r"^(?:FO_Level1_Margins|FO_ClientLevelCollaterals)_.*\.(csv|txt|xlsx)$", re.I)   # Level-1 row (new) | old full FO file
_AUTH_BAD = ("password", "LOCKED", "RSA code")           # a refused login is never repeated


class Busy(Exception):
    pass


class PreRunError(Exception):
    pass


class Cancelled(Exception):
    pass


# ───────────────────────────── mail keys ─────────────────────────────
def mail_keys(k: str) -> tuple:
    return ("OBLIGATION_TO_LATE", "OBLIGATION_CC_LATE") if k == "LATE" else ("OBLIGATION_TO", "OBLIGATION_CC")


def apply_defaults() -> None:
    for k, (to, cc) in MAIL_DEFAULTS.items():
        tk, ck = mail_keys(k)
        os.environ.setdefault(tk, to)
        os.environ.setdefault(ck, cc)


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("OBLIGATION_DIR", "").strip()
    if v:
        p = Path(v)
    else:
        try:
            from core.scheduler import paths
            base = paths.data_root()
        except Exception:                                        # noqa: BLE001
            base = REPO / "data"
        p = base / FOLDER_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_dir() -> Path:
    p = root() / "_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_root() -> Path:
    return root() / BACKUP_SUB


def log_dir() -> Path:
    v = os.getenv("OBLIGATION_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / PIPE
    except Exception:                                            # noqa: BLE001
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _today() -> dt.date:
    return dt.date.today()


def _iso(t=None) -> str:
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def _check_date(s: str) -> str:
    if not re.fullmatch(r"\d{2}-\d{2}-\d{4}", s or ""):
        raise ValueError("date must be dd-mm-yyyy")
    return s


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


# ───────────────────────────── settings (times, auto, alerts) ─────────────────────────────
def _parse_times(s: str) -> list:
    out = []
    for part in re.split(r"[,\s;]+", (s or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", part)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError(f"'{part}' is not a time like 14:00")
        out.append(int(m.group(1)) * 60 + int(m.group(2)))
    return out


def _settings_path() -> Path:
    return _state_dir() / "settings.json"


def get_settings() -> dict:
    d = {"times": DEFAULT_TIMES, "auto": True, "attention_min": 60, "send_mail": True, "send_whatsapp": True,
         "alert_numbers": "", "report_numbers": "", "report_numbers_late": "", "split_time": "17:00",
         "report_template": "daily_report"}
    try:
        d.update(json.loads(_settings_path().read_text(encoding="utf-8")))
    except Exception:                                            # noqa: BLE001
        pass
    try:
        if not _parse_times(d["times"]):
            d["times"] = DEFAULT_TIMES
    except ValueError:
        d["times"] = DEFAULT_TIMES
    return d


def _numbers_ok(raw: str):
    raw = str(raw or "").strip()
    if raw:
        from core.scheduler import failure_alert
        if not failure_alert.parse_numbers(raw):
            raise ValueError("those WhatsApp numbers are not valid (10 digits, optionally +91; separate several with commas)")
    return raw


def save_settings(update: dict, by: str = "") -> dict:
    cur = get_settings()
    if "times" in update:
        mins = sorted(set(_parse_times(str(update["times"]))))
        if not mins:
            raise ValueError("enter at least one time like 10:30")
        if len(mins) > MAX_SLOTS:
            raise ValueError(f"at most {MAX_SLOTS} timings")
        cur["times"] = ",".join(hhmm(m) for m in mins)
    for k in ("auto", "send_mail", "send_whatsapp"):
        if k in update:
            cur[k] = bool(update[k])
    if "attention_min" in update:
        try:
            n = int(update["attention_min"])
        except (TypeError, ValueError):
            raise ValueError("attention minutes must be a number")
        if not 10 <= n <= 600:
            raise ValueError("attention minutes: between 10 and 600")
        cur["attention_min"] = n
    if "split_time" in update:
        m = _parse_times(str(update["split_time"]))
        if len(m) != 1:
            raise ValueError("split time must be one time like 17:00")
        cur["split_time"] = hhmm(m[0])
    for k in ("alert_numbers", "report_numbers", "report_numbers_late"):
        if k in update:
            cur[k] = _numbers_ok(update[k])
    if "report_template" in update:
        cur["report_template"] = re.sub(r"[^A-Za-z0-9_]", "", str(update["report_template"] or "")) or "daily_report"
    cur["by"], cur["at"] = by, _iso()
    with _STATE_LOCK:
        tmp = _settings_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        os.replace(tmp, _settings_path())
    return cur


def slot_times() -> list:
    return sorted(set(_parse_times(get_settings()["times"])))


# ───────────────────────────── day state (one file per day) ─────────────────────────────
def _day_path(d: dt.date) -> Path:
    return _state_dir() / f"{dstr(d)}.json"


def read_day(d: dt.date) -> dict:
    with _STATE_LOCK:
        try:
            st = json.loads(_day_path(d).read_text(encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            st = {}
    st.setdefault("slots", {})
    st.setdefault("extra", 0)
    return st


def _write_day(d: dt.date, st: dict):
    with _STATE_LOCK:
        tmp = _day_path(d).with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _day_path(d))


def _slot_update(d: dt.date, slot: int, **kw) -> dict:
    with _STATE_LOCK:
        st = read_day(d)
        rec = st["slots"].setdefault(str(slot), {})
        rec.update(kw)
        _write_day(d, st)
        return rec


def _bump_extra(d: dt.date):
    with _STATE_LOCK:
        st = read_day(d)
        st["extra"] = int(st.get("extra", 0)) + 1
        _write_day(d, st)


def _is_done(rec: dict) -> bool:
    return (rec or {}).get("status") == "done"


def done_count(d: dt.date = None) -> int:
    d = d or _today()
    st = read_day(d)["slots"]
    return sum(1 for i in range(1, len(slot_times()) + 1) if _is_done(st.get(str(i))))


def _trading_day(d: dt.date) -> bool:
    try:
        from core.scheduler import helpers as H
        return bool(H.is_trading_day(d))
    except Exception:                                            # noqa: BLE001
        return d.weekday() < 5


def pick_target(d: dt.date, now: dt.datetime, auto: bool = False):
    """The slot a run started at `now` completes: the LATEST not-done slot whose time is here (a hand run: or within EARLY_MIN
    minutes); a hand run before every pending time takes the first pending one. None = nothing pending (an extra run)."""
    times = slot_times()
    st = read_day(d)["slots"]
    pending = [i for i in range(1, len(times) + 1) if not _is_done(st.get(str(i)))]
    cur = now.hour * 60 + now.minute
    ready = [i for i in pending if times[i - 1] <= cur + (0 if auto else EARLY_MIN)]
    if ready:
        return max(ready)
    if auto:
        return None
    return pending[0] if pending else None


def override_slots(slots, by: str = "", now: dt.datetime = None) -> dict:
    """The popup: `slots` = the slot numbers that were already done by hand today. Ticked -> done (counter goes up, the next
    run continues from the next slot); a slot ticked earlier and now un-ticked goes back to 'not done'. Real runs are not touched."""
    now = now or dt.datetime.now()
    d = now.date()
    n = len(slot_times())
    want = set()
    for s in slots or []:
        try:
            i = int(s)
        except (TypeError, ValueError):
            raise ValueError("slot numbers only")
        if not 1 <= i <= n:
            raise ValueError(f"slot {i} does not exist (1 to {n})")
        want.add(i)
    with _STATE_LOCK:
        st = read_day(d)
        for i in range(1, n + 1):
            rec = st["slots"].setdefault(str(i), {})
            if i in want and not _is_done(rec):
                rec.update(status="done", override=True, msg="marked done by hand", by=by, done_at=_iso(now), trigger="override")
            elif i not in want and rec.get("override"):
                for k in ("status", "override", "msg", "done_at", "trigger"):
                    rec.pop(k, None)
        _write_day(d, st)
    log(f"Override by {by or 'user'}: slots done by hand today = {sorted(want) or 'none'} ({done_count(d)}/{n}).")
    return {"ok": True, "done": done_count(d), "total": n}


# ───────────────────────────── run state (the run in progress / the last run) ─────────────────────────────
def _state_path() -> Path:
    return _state_dir() / "state.json"


def read_state() -> dict:
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        st = {}
    if st.get("date") != _today().isoformat():
        return {"date": _today().isoformat(), "status": "idle", "steps": {}, "runs": 0}
    st.setdefault("steps", {})
    st.setdefault("status", "idle")
    st.setdefault("runs", 0)
    return st


def _write_state(st: dict):
    with _STATE_LOCK:
        from core.safe_state import write_json
        write_json(_state_path(), st)


def _update(**kw) -> dict:
    with _STATE_LOCK:
        st = read_state()
        st.update(kw)
        _write_state(st)
        return st


def _set_step(name: str, status: str, msg: str = ""):
    with _STATE_LOCK:
        st = read_state()
        st["steps"][name] = {"status": status, "msg": msg[:300], "at": _iso()}
        _write_state(st)


# ───────────────────────────── logging / stop ─────────────────────────────
def log(msg: str):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    _LOG.append(line)
    f = _ACTIVE.get("log_file")
    if f:
        try:
            f.write(line + "\n")
            f.flush()
        except Exception:                                        # noqa: BLE001
            pass
    sys.__stdout__.write("[obligation] " + msg + "\n") if sys.__stdout__ else None


def recent_log(n: int = 120) -> list:
    return list(_LOG)[-n:]


def _open_run_log(trigger, by):
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"{PIPE}__{now:%d-%m-%Y}__{now:%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== {NAME} - {now:%d-%m-%Y %H:%M:%S} =====\nstarted by: {by or trigger} ({trigger})\n\n")
        f.flush()
        return f
    except Exception:                                            # noqa: BLE001
        return None


def _cancelled() -> bool:
    return bool(_ACTIVE.get("cancel"))


def _check_cancel():
    if _cancelled():
        raise Cancelled("Stopped.")


def _kill(p):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=15)
        else:
            p.kill()
    except Exception:                                            # noqa: BLE001
        pass


def _python() -> str:
    return os.getenv("CTCL_PYTHON") or os.getenv("SA_PYTHON") or sys.executable


_INTEREST = re.compile(r"\[[A-Z0-9_:]+\]|✅|❌|⚠️|saved|login|logout|captcha|token|session|download|rows|attempt|OK:|Saved|RSA|Logged", re.I)


def _run_child(cmd, env, timeout_s) -> tuple:
    """Run a child process; its interesting lines go to the card's log; Stop / timeout kill it. -> (returncode, last lines)"""
    flags = 0x08000000 if os.name == "nt" else 0
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(REPO), text=True,
                         encoding="utf-8", errors="replace", creationflags=flags)
    _ACTIVE["proc"] = p
    tail: list = []

    def pump():
        for line in p.stdout:
            line = line.rstrip()
            if not line:
                continue
            tail.append(line)
            del tail[:-60]
            if _INTEREST.search(line):
                log(line[:300])
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t0 = time.time()
    try:
        while p.poll() is None:
            if _cancelled():
                _kill(p)
                break
            if time.time() - t0 > timeout_s:
                log(f"Stopped: no result after {int(timeout_s // 60)} minutes.")
                _kill(p)
                break
            time.sleep(0.5)
        p.wait(timeout=20)
    finally:
        _ACTIVE["proc"] = None
    t.join(timeout=5)
    return (p.returncode if p.returncode is not None else -1), tail


def _auth_message(tail) -> str:
    try:
        from core import live_allocation as _la
        m = _la._auth_message(tail)
        if m:
            return m
    except Exception:                                            # noqa: BLE001
        pass
    for ln in tail:
        if "[OBL_MCX_FAILED]" in ln:
            return "MCX: " + ln.split("]", 1)[1].strip()
    return ""


# ───────────────────────────── the MAIN file (upload) ─────────────────────────────
def main_path(base: Path = None):
    base = Path(base or root())
    found = [p for p in base.iterdir() if p.is_file() and _MAIN_RX.match(p.name)] if base.is_dir() else []
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def main_time(name: str):
    """'MAIN_11.30.csv' -> (TIME_STR '11:30 AM', TIME_FOLDER '11-30-AM'), exactly like the old script."""
    m = re.search(r"MAIN[_ -]?(\d{1,2})[._](\d{2})", name or "", re.I)
    if not m:
        raise ValueError("the MAIN file name must be like MAIN_11.30.csv")
    hour, minute = int(m.group(1)), m.group(2)
    if hour >= 12:
        disp, ap = (hour - 12 if hour > 12 else 12), "PM"
    else:
        disp, ap = hour, "AM"
    return f"{disp}:{minute} {ap}", f"{disp}-{minute}-{ap}"


def fo_date(name: str):
    """FO_Level1_Margins_<ddmmyyyy><hhmmss>.csv (or the old FO_ClientLevelCollaterals_...) -> the date inside the name (None when it has none)."""
    m = re.search(r"_(\d{2})(\d{2})(\d{4})(?:\d{4,6})?(?!\d)", os.path.basename(name or ""))
    if not m:
        return _cnc.name_date(name)
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _meta_path() -> Path:
    return _state_dir() / "upload.json"


def _read_meta() -> dict:
    try:
        return json.loads(_meta_path().read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return {}


def input_status(base: Path = None, today: dt.date = None) -> list:
    today = today or _today()
    p = main_path(base)
    row = {"key": "MAIN", "label": "MAIN file (upload)", "name": p.name if p else "MAIN_<hh.mm>.csv", "found": bool(p), "ok": False,
           "why": "", "size": 0, "time": "", "where": "", "stale": False}
    if p:
        st = p.stat()
        saved = dt.datetime.fromtimestamp(max(st.st_mtime, st.st_ctime) if os.name == "nt" else st.st_mtime)
        row.update(size=st.st_size, time=saved.strftime("%H:%M:%S"), where=str(p.parent))
        if st.st_size == 0:
            row["why"] = "empty"
        else:
            up = _read_meta()
            if up.get("name") == p.name and up.get("date") and up["date"] != today.isoformat():
                row["stale"], row["why"] = True, f"uploaded {up['date']} - not today"
            else:
                row["ok"] = True
    else:
        row["why"] = "not uploaded"
    return [row]


def pre_run_check(base: Path = None) -> dict:
    rows = input_status(base)
    bad = [r for r in rows if not r["ok"]]
    if bad:
        raise PreRunError("Missing / not ready: " + "; ".join(f"{r['label']}" + (f" ({r['why']})" if r["why"] else "") for r in bad))
    return {"inputs": rows}


# ───────────────────────────── downloads ─────────────────────────────
def step_fo(work: Path) -> Path:
    """FO Level-1 margin row (NMASS Risk Management - FO > Margins > CM) by the console's own NMASS script (shared NMASS session; captcha
    filled automatically). Saved as FO_Level1_Margins_<ddmmyyyy><hhmmss>.csv with the NSE screen's column names."""
    script = os.getenv("CTCL_NMASS_SCRIPT", str(REPO / "ctcl_codes" / "NMASS_NCFE_API_MIX.PY"))
    if not os.path.isfile(script):
        raise RuntimeError(f"NMASS script not found: {script}")
    from core import dl_lock
    waited = 0
    while True:                                                  # the NMASS login is shared with the other cards - wait our turn
        try:
            dl_lock.acquire(PIPE)
            break
        except dl_lock.NmassLoginBusyError as e:
            if _cancelled():
                raise Cancelled("Stopped.")
            if waited > int(os.getenv("OBLIGATION_NMASS_WAIT_SEC", "600")):
                raise RuntimeError(f"The NMASS login stayed busy (used by '{e.owner}') - try again.")
            if waited % 30 == 0:
                log(f"NMASS login is in use by '{e.owner}' - waiting...")
            time.sleep(5)
            waited += 5
    try:
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                    "NMASS_SAVE_DIR": str(work), "NCFE_LOGOUT_AFTER": "1"})
        env["NMASS_RUN_MODE"] = "fo_only"                       # only the FO file: no CM / CO, no NCDEX (NCFE) login
        log("NMASS download (FO Level-1 margin row) ...")
        alive = threading.Event()

        def keep_fresh():                                        # dl_lock treats a lock older than 3 min as abandoned
            while not alive.wait(45):
                try:
                    os.utime(dl_lock._LOCK_PATH, None)
                except OSError:
                    pass
        threading.Thread(target=keep_fresh, daemon=True).start()
        try:
            rc, tail = _run_child([_python(), script], env, int(os.getenv("OBLIGATION_NMASS_TIMEOUT_MIN", "20")) * 60)
        finally:
            alive.set()
    finally:
        dl_lock.release()
    _check_cancel()
    found = sorted((p for p in work.iterdir() if p.is_file() and _FO_RX.match(p.name)), key=lambda p: p.stat().st_mtime) if work.is_dir() else []
    if found:
        return found[-1]
    raise RuntimeError(_auth_message(tail) or f"The FO Level-1 margin row was not downloaded (script ended with code {rc}).")


def step_mcx(work: Path) -> Path:
    """MCX eclear RSK335 Margin Utilization View -> SearchResults xlsx (password from Credentials, RSA code automatic)."""
    from core import tcs_session_lock as L
    log("Waiting for the MCX eclear login (shared with the other MCX downloads)...")
    if not L.acquire(PIPE, wait_s=int(os.getenv("OBLIGATION_TCS_WAIT_MIN", "20")) * 60, cancel=_cancelled, log=log):
        _check_cancel()
        raise RuntimeError("The MCX eclear login stayed busy - try again.")
    stop_beat = threading.Event()

    def beat():
        while not stop_beat.wait(60):
            L.touch()
    threading.Thread(target=beat, daemon=True).start()
    try:
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "MCX_TOKEN_MODE": "auto",
                    "MCX_DEPOSIT_CALLER": PIPE, "PYTHONPATH": str(REPO) + os.pathsep + env.get("PYTHONPATH", "")})
        log("MCX eclear: Margin Utilization View (RSK335) ...")
        rc, tail = _run_child([_python(), str(REPO / "core" / "obligation_mcx_cli.py"), "--out", str(work)], env,
                              int(os.getenv("OBLIGATION_MCX_TIMEOUT_MIN", "30")) * 60)
    finally:
        stop_beat.set()
        L.release(PIPE)
    _check_cancel()
    for ln in reversed(tail):
        if ln.startswith("[OBL_MCX_RESULT]"):
            p = Path(ln.split("]", 1)[1].strip())
            if p.is_file():
                return p
    raise RuntimeError(_auth_message(tail) or f"MCX Margin Utilization file was not downloaded (code {rc}).")


def _with_retries(label: str, fn, tries: int = None, gap_s: int = None, sleep=time.sleep):
    tries = DL_TRIES if tries is None else tries
    gap_s = DL_GAP_S if gap_s is None else gap_s
    last = None
    for n in range(1, tries + 1):
        _check_cancel()
        try:
            return fn()
        except Cancelled:
            raise
        except Exception as e:                                   # noqa: BLE001
            last = e
            msg = str(e)
            log(f"{label} failed (try {n}/{tries}): {msg}")
            if any(w in msg for w in _AUTH_BAD) or n >= tries:
                break
            log(f"{label}: trying once more in {gap_s} s ...")
            for _ in range(int(gap_s)):                          # 1-second steps: Stop works at once
                _check_cancel()
                sleep(1)
    raise last if last else RuntimeError(f"{label} failed")


# ───────────────────────────── picture / mail / WhatsApp ─────────────────────────────
def report_time_text(main_csv) -> str:
    """'Report time: dd-mm-yyyy  HH:MM' - the time is the MAIN file's (MAIN_15.30.csv), the date is the file's own date."""
    p = Path(main_csv)
    m = _MAIN_RX.match(p.name)
    try:
        day = dt.datetime.fromtimestamp(p.stat().st_mtime)
    except OSError:
        day = dt.datetime.now()
    hh, mm = (int(m.group(1)), int(m.group(2))) if m else (day.hour, day.minute)
    return f"Report time: {day:%d-%m-%Y}  {hh:02d}:{mm:02d}"


def step_build(main_csv: Path, fo_file: Path, search_file: Path, work: Path) -> Path:
    from core import obligation_image as OI
    obl, cust, margins = OI.build_data(str(main_csv), str(fo_file), str(search_file))
    out = work / "Obligation_Report_Detailed.png"
    OI.render(str(out), obl, cust, margins, footer=report_time_text(main_csv))
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError("the picture was not created")
    log(f"Picture ready: {out.name} ({out.stat().st_size // 1024} KB)")
    return out


def report_group(now: dt.datetime = None) -> str:
    """'early' = up to the split time (default 17:00) by the system clock; 'late' = after it until 00:30 (and nothing else is later)."""
    now = now or dt.datetime.now()
    cur = now.hour * 60 + now.minute
    try:
        split = _parse_times(get_settings().get("split_time") or "17:00")[0]
    except (ValueError, IndexError):
        split = 17 * 60
    return "late" if (cur > split or cur <= LATE_END_MIN) else "early"


def _addr(env_key: str) -> list:
    try:
        from core.mcx_cash_report import split_addrs
        return split_addrs(os.environ.get(env_key, "")) or []
    except Exception:                                            # noqa: BLE001
        return [a.strip() for a in os.environ.get(env_key, "").replace(";", ",").split(",") if a.strip()]


def _lists(group: str):
    """(To, CC) of the group; a blank late list falls back to the early one."""
    to, cc = _addr(mail_keys("REPORT")[0]), _addr(mail_keys("REPORT")[1])
    if group == "late":
        lto, lcc = _addr(mail_keys("LATE")[0]), _addr(mail_keys("LATE")[1])
        if lto:
            return lto, lcc
    return to, cc


def _to_list() -> list:
    return _lists(report_group())[0] or ["(no To address)"]


def send_report_mail(image: Path, time_str: str, attempts=MAIL_TRIES, gap=10, sleep=time.sleep, smtp_factory=None, now=None):
    """One HTML mail, the picture shown inside it. Retried; a refused login is not retried."""
    import smtplib
    from email.mime.image import MIMEImage
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from core import sender_override
    grp = report_group(now)
    to, cc = _lists(grp)
    log(f"Recipients: {grp} group by the system clock ({dt.datetime.now():%H:%M}).")
    user, pwd = sender_override.sender(PIPE)
    host, port = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in"), int(os.getenv("SMTP_PORT", "587") or 587)
    if not to:
        raise RuntimeError("no 'To' address set - use the Email To / CC button")
    if not (user and pwd):
        raise RuntimeError("the sender login is not set (Credentials > E-mail sender, or the shared mail login)")
    html = (f"Respected Sir,<br><br>Please find the <b>Daily Obligation Report {time_str}</b> attached below:<br><br>"
            f'<img src="cid:detailed_report_img"><br><br>Warm regards,<br>{SIGNATURE}')
    last = None
    for n in range(1, attempts + 1):
        _check_cancel()
        try:
            msg = MIMEMultipart("related")
            msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to), "DAILY OBLIGATION REPORT"
            if cc:
                msg["Cc"] = ", ".join(cc)
            alt = MIMEMultipart("alternative")
            alt.attach(MIMEText(html, "html", "utf-8"))
            msg.attach(alt)
            img = MIMEImage(Path(image).read_bytes(), _subtype="png")
            img.add_header("Content-ID", "<detailed_report_img>")
            img.add_header("Content-Disposition", "inline", filename=Path(image).name)
            msg.attach(img)
            srv = (smtp_factory or smtplib.SMTP)(host, port, timeout=60)
            try:
                srv.starttls()
                srv.login(user, pwd)
                srv.send_message(msg, to_addrs=list(to) + list(cc))
            finally:
                try:
                    srv.quit()
                except Exception:                                # noqa: BLE001
                    pass
            log(f"E-mail sent: DAILY OBLIGATION REPORT {time_str} -> {', '.join(to)}" + (f" (cc {', '.join(cc)})" if cc else ""))
            return
        except Cancelled:
            raise
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(f"the mail login was refused: {e}")
        except Exception as e:                                   # noqa: BLE001
            last = e
            log(f"E-mail failed (try {n}/{attempts}): {type(e).__name__}: {e}")
            if n < attempts:
                for _ in range(int(gap)):
                    _check_cancel()
                    sleep(1)
    raise RuntimeError(f"e-mail did not go: {type(last).__name__}: {last}")


class _Ctx:
    cancelled = False

    def log(self, m):
        log(m)


def send_report_whatsapp(image: Path, report_date: dt.date, sender=None, now=None) -> list:
    """The detailed picture to the report numbers (this card's list, else the common WhatsApp list). -> numbers (last 4 digits) sent."""
    from core.scheduler import connections, failure_alert, helpers as H
    s = get_settings()
    c = connections.get("whatsapp")
    grp = report_group(now)
    mine = (s.get("report_numbers_late") or "") if grp == "late" else ""
    numbers = (failure_alert.parse_numbers(mine) or failure_alert.parse_numbers(s.get("report_numbers") or "")
               or failure_alert.parse_numbers(c.get("alert_numbers", "")))
    log(f"WhatsApp recipients: {grp} group by the system clock ({dt.datetime.now():%H:%M}).")
    if not numbers:
        raise RuntimeError("no WhatsApp number set for the report (Credentials of this card, or the common WhatsApp list)")
    label, date_text = "Daily Obligation", report_date.strftime("%d-%m-%Y")
    ok, bad = [], []
    for num in numbers:
        _check_cancel()
        try:
            if sender is not None:
                sender(num, Path(image), label, date_text)
            else:
                H.whatsapp_send_image(Path(image), num, label, date_text, _Ctx(), template_name=(s.get("report_template") or "daily_report"))
            ok.append(num[-4:])
        except Cancelled:
            raise
        except Exception as e:                                   # noqa: BLE001
            bad.append(f"{num[-4:]}: {type(e).__name__}: {str(e)[:100]}")
    if bad:
        log("WhatsApp problems: " + "; ".join(bad))
    if not ok:
        raise RuntimeError("WhatsApp was not sent: " + "; ".join(bad))
    log("WhatsApp report sent to ..." + ", ...".join(ok))
    return ok


# ───────────────────────────── backup ─────────────────────────────
def backup_and_cleanup(main_csv: Path, fo_file: Path, search_file: Path, image: Path, folder: str, work: Path = None, today=None) -> Path:
    """MAIN + FO + SearchResults + the picture -> BACKUP FILES AUTOMATED\\<dd-mm-yyyy>\\<hh-mm-AM|PM>; the MAIN file of the card's
    folder is consumed (the next run needs its own), the working folder goes."""
    d = fo_date(Path(fo_file).name) or today or _today()
    dest = backup_root() / dstr(d) / folder
    n = 2
    while dest.exists() and any(dest.iterdir()):
        dest = backup_root() / dstr(d) / f"{folder}_{n}"
        n += 1
    dest.mkdir(parents=True, exist_ok=True)
    for p in (fo_file, search_file, image):
        shutil.copy2(p, dest / Path(p).name)
        log(f"saved in backup: {Path(p).name}")
    shutil.move(str(main_csv), str(dest / Path(main_csv).name))
    log(f"moved to backup: {Path(main_csv).name}")
    if work:
        shutil.rmtree(work, ignore_errors=True)
    return dest


# ───────────────────────────── failure alert ─────────────────────────────
def send_alert(headline, detail, sender=None) -> dict:
    """WhatsApp FAILURE ALERT picture (same as the other cards). Only called when a run FAILED. Never raises."""
    rec = {"sent": False, "reason": ""}
    try:
        from core.scheduler import connections, failure_alert, helpers as H, paths
        raw = get_settings().get("alert_numbers") or ""          # blank -> the COMMON list (Credentials > WhatsApp)
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
        img = failure_alert.render_image(paths.sched_root() / "alerts" / f"{PIPE}_{int(time.time())}.png", process=NAME,
                                         schedule="Obligation run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-",
                                         trigger="Obligation run", attempt_text="final", headline=headline, detail=detail,
                                         run_id="OBLIGATION", kind="FAILED")
        ok, bad = [], []
        for num in numbers:
            try:
                H.whatsapp_send_image(img, num, f"FAILURE ALERT - {NAME}", now.strftime("%d-%m-%Y %H:%M"), _Ctx(),
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
    return False                                                 # several runs a day are the normal case


def run(trigger="manual", by="", alert=None, step_fns=None, now: dt.datetime = None, slot: int = None) -> dict:
    """One report. `step_fns` (tests): {'fo','mcx','build','mail','whatsapp'} replacements. A FAILED run alerts - nothing else does."""
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy(f"{NAME} is already running.")
    alert = alert or send_alert
    fns = {"fo": step_fo, "mcx": step_mcx, "build": step_build, "mail": send_report_mail, "whatsapp": send_report_whatsapp}
    fns.update(step_fns or {})
    now = now or dt.datetime.now()
    d = now.date()
    base = root()
    _ACTIVE["cancel"] = False
    _ACTIVE["log_file"] = _open_run_log(trigger, by)
    prev = read_state()
    _update(status="running", started=_iso(), finished="", error="", trigger=trigger, by=by, steps={}, counts={},
            alert_sent=False, alert_reason="", runs=int(prev.get("runs", 0)) + 1)
    old_pipe = os.environ.get("RMS_PIPE")
    os.environ["RMS_PIPE"] = PIPE
    target = slot
    work = None
    result = {"ok": False}
    try:
        log(f"=== {NAME} started ({trigger}{', ' + by if by else ''}) ===")
        _set_step("input", "running")
        pre_run_check(base)
        main = main_path(base)
        time_str, folder = main_time(main.name)
        if target is None:
            target = pick_target(d, now, auto=(trigger == "auto"))
        _ACTIVE["slot"] = target
        times = slot_times()
        if target:
            _slot_update(d, target, status="running", started=_iso(now), trigger=trigger, by=by, msg="")
        _update(slot=target, report_time=time_str)
        _set_step("input", "ok", f"{main.name} · report time {time_str}" + (f" · slot {target} ({hhmm(times[target - 1])})" if target else " · extra run"))
        _check_cancel()
        work = base / "_work" / now.strftime("%H%M%S")
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        _set_step("fo", "running")
        fo_file = _with_retries("FO Level-1 margins", lambda: fns["fo"](work))
        _set_step("fo", "ok", fo_file.name)
        _check_cancel()
        fo_day = fo_date(fo_file.name)
        if fo_day is not None and fo_day != d:
            raise RuntimeError(f"the FO file is dated {dstr(fo_day)} - not today's ({dstr(d)}); NMASS gave an old row")
        _set_step("mcx", "running")
        search_file = _with_retries("MCX Margin Utilization", lambda: fns["mcx"](work))
        _set_step("mcx", "ok", search_file.name)
        _check_cancel()
        _set_step("build", "running")
        image = fns["build"](main, fo_file, search_file, work)
        _set_step("build", "ok", image.name)
        _check_cancel()
        s = get_settings()
        sent = []
        _set_step("send", "running")
        if s.get("send_mail", True):
            fns["mail"](image, time_str)
            sent.append("mail")
        if s.get("send_whatsapp", True):
            fns["whatsapp"](image, d)
            sent.append("whatsapp")
        if not sent:
            raise RuntimeError("both e-mail and WhatsApp are switched off in Settings - nothing was sent")
        _set_step("send", "ok", " + ".join(sent))
        _update(sent=sent)
        _check_cancel()
        _set_step("backup", "running")
        dest = backup_and_cleanup(main, fo_file, search_file, image, folder, work, d)
        work = None
        _set_step("backup", "ok", str(dest))
        if target:
            _slot_update(d, target, status="done", done_at=_iso(), msg=f"report {time_str} sent", override=False, report_time=time_str)
        else:
            _bump_extra(d)
        _update(status="done", finished=_iso(), error="")
        log(f"=== {NAME} finished ({time_str}) ===")
        result = {"ok": True, "slot": target, "time": time_str}
    except Cancelled:
        if target:
            _slot_update(d, target, status="stopped", msg="Stopped.")
        _update(status="stopped", finished=_iso(), error="Stopped.")
        log("Stopped.")
        result = {"ok": False, "stopped": True}
    except Exception as e:                                       # noqa: BLE001
        msg = str(e) if isinstance(e, PreRunError) else f"{type(e).__name__}: {e}"
        if _cancelled():
            if target:
                _slot_update(d, target, status="stopped", msg="Stopped.")
            _update(status="stopped", finished=_iso(), error="Stopped.")
            log("Stopped.")
            result = {"ok": False, "stopped": True}
        else:
            if target:
                _slot_update(d, target, status="failed", msg=msg[:300], failed_at=_iso())
            _update(status="failed", finished=_iso(), error=msg[:600])
            log("FAILED: " + msg)
            rec = {"sent": False, "reason": ""}
            final = not (trigger == "auto" and target and int(read_day(d)["slots"].get(str(target), {}).get("auto_attempts") or 0) < AUTO_TRIES)
            if isinstance(e, PreRunError):
                final = False                                    # nothing was attempted - not a failure of the run
            if final:
                rec = alert(f"The {NAME} run failed.", msg[:600])
            else:
                log("No alert yet - the timer tries this slot again" if not isinstance(e, PreRunError) else "No alert - the run did not start.")
            _update(alert_sent=bool(rec.get("sent")), alert_reason=rec.get("reason", ""))
            result = {"ok": False, "error": msg}
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
        if old_pipe is None:
            os.environ.pop("RMS_PIPE", None)
        else:
            os.environ["RMS_PIPE"] = old_pipe
        _ACTIVE["slot"] = None
        f = _ACTIVE.get("log_file")
        _ACTIVE["log_file"] = None
        if f:
            try:
                f.close()
            except Exception:                                    # noqa: BLE001
                pass
        _RUN_LOCK.release()
    return result


def request_run(by="", password=None, background=True) -> dict:
    if is_running():
        raise Busy(f"{NAME} is already running.")
    if background:
        th = threading.Thread(target=lambda: run("manual", by), name="obligation-manual", daemon=True)
        _ACTIVE["thread"] = th
        th.start()
        return {"ok": True, "started": True}
    return run("manual", by)


def any_fetching(exclude: str = "") -> bool:
    return False


def fetch_one(what: str, by="") -> dict:
    raise ValueError("this card has no separate downloads - upload the MAIN file and press Run")


def stop_running(by="") -> bool:
    if not is_running():
        return False
    _ACTIVE["cancel"] = True
    p = _ACTIVE.get("proc")
    if p is not None:
        _kill(p)
    log(f"Stop requested by {by or 'user'}.")
    return True


# ───────────────────────────── the timer ─────────────────────────────
def due_slot(now: dt.datetime = None):
    """The slot the timer should run now, or None. Relaxed: nothing is due without today's MAIN file, and a slot that was not
    run is NOT an error - it simply waits (and shows ATTENTION after `attention_min`)."""
    now = now or dt.datetime.now()
    s = get_settings()
    d = now.date()
    if not s["auto"] or _RUN_LOCK.locked() or not _trading_day(d):
        return None
    inp = input_status(today=d)[0]
    if not inp["ok"]:
        return None
    slot = pick_target(d, now, auto=True)
    if not slot:
        return None
    rec = read_day(d)["slots"].get(str(slot), {})
    n = int(rec.get("auto_attempts") or 0)
    if n >= AUTO_TRIES:
        return None
    last = rec.get("last_auto") or ""
    if n and last:
        try:
            if (now - dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() < AUTO_GAP_MIN * 60:
                return None
        except ValueError:
            pass
    return slot


def tick(now: dt.datetime = None, runner=None):
    now = now or dt.datetime.now()
    slot = due_slot(now)
    if not slot:
        return None
    d = now.date()
    rec = read_day(d)["slots"].get(str(slot), {})
    _slot_update(d, slot, auto_attempts=int(rec.get("auto_attempts") or 0) + 1, last_auto=_iso(now))

    def go():
        try:
            run("auto", "timer", slot=slot, now=now)
        except Busy:
            pass
        except Exception as e:                                   # noqa: BLE001
            print(f"[obligation] timer run of slot {slot} failed: {type(e).__name__}: {e}", flush=True)
    if runner:
        runner(slot)
    else:
        threading.Thread(target=go, name=f"obligation-auto-{slot}", daemon=True).start()
    return slot


def start_ticker(interval_s: int = 20):
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return False
    _TICK["stop"].clear()

    def loop():
        print("[obligation] timer started - the report runs at its times by itself once the MAIN file is uploaded.", flush=True)
        while not _TICK["stop"].wait(interval_s):
            try:
                tick()
            except Exception as e:                               # noqa: BLE001
                print(f"[obligation] timer error: {type(e).__name__}: {e}", flush=True)
    th = threading.Thread(target=loop, name="obligation-timer", daemon=True)
    _TICK["thread"] = th
    th.start()
    return True


def stop_ticker():
    _TICK["stop"].set()


# ───────────────────────────── files / backup (same shape as the other cards) ─────────────────────────────
def _bk_day(name) -> Path:
    from core import backup_archive
    return backup_archive.day_dir(backup_root(), str(name))


def list_files(sub: str = "") -> list:
    """sub '' = the card's folder; 'dd-mm-yyyy' = that day (every run's folder inside it, shown as <hh-mm-AM>/<file>)."""
    out = []
    if not sub:
        base = root()
        for p in sorted(base.iterdir()) if base.is_dir() else []:
            if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".part"):
                out.append({"name": p.name, "size": p.stat().st_size, "time": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S")})
        return out
    day = _bk_day(sub)
    if day.is_dir():
        for p in sorted(day.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                out.append({"name": p.relative_to(day).as_posix(), "size": p.stat().st_size,
                            "time": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S")})
    return out


def list_backup_dates() -> list:
    from core import backup_archive
    b = backup_root()
    if not b.is_dir():
        return []
    names = [p.name for p in b.iterdir() if p.is_dir() and re.fullmatch(r"\d{2}-\d{2}-\d{4}", p.name)]
    names += [n for n in backup_archive.archived_days(b) if re.fullmatch(r"\d{2}-\d{2}-\d{4}", n)]
    return sorted(set(names), key=lambda s: dt.datetime.strptime(s, "%d-%m-%Y"), reverse=True)


def file_path(name: str, sub: str = "") -> Path:
    if "\\" in name or name.startswith(".") or ".." in name.split("/"):
        raise ValueError("bad file name")
    p = (root() if not sub else _bk_day(_check_date(sub))) / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return p


dates_between = _cnc.dates_between


def range_listing(a: dt.date, b: dt.date) -> list:
    rows = []
    for d in dates_between(a, b):
        for f in list_files(dstr(d)):
            rows.append(dict(f, date=dstr(d)))
    return rows


def zip_range(a: dt.date, b: dt.date) -> Path:
    import tempfile
    z = Path(tempfile.gettempdir()) / f"OBLIGATION_{a:%d%m%Y}_{b:%d%m%Y}_{int(time.time())}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dates_between(a, b):
            for f in list_files(dstr(d)):
                zf.write(_bk_day(dstr(d)) / f["name"], f"{dstr(d)}/{f['name']}")
    return z


# ───────────────────────────── upload ─────────────────────────────
def _event(msg: str):
    log(msg)
    try:
        with open(log_dir() / f"{PIPE}__{dstr(_today())}__uploads.log", "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%d-%m-%Y %H:%M:%S}] {msg}\n")
    except Exception:                                            # noqa: BLE001
        pass


def _refuse(name, why, by=""):
    _event(f"Upload REFUSED: {name} - {why} (by {by or 'user'})")
    raise ValueError(f"{name}: {why}")


def accept_upload(name: str, data: bytes, by="", modified_ms=None, now: dt.datetime = None) -> dict:
    """Only today's MAIN_<hh.mm>.csv: today's date in the name, or - with no date - saved in the last FRESH_MIN minutes.
    One MAIN file at a time (a new one replaces the old)."""
    now = now or dt.datetime.now()
    name = os.path.basename(name or "")
    if not name or name.startswith(".") or name.lower().endswith((".py", ".bat", ".exe", ".part")):
        _refuse(name or "(no name)", "this file name is not allowed", by)
    if not data:
        _refuse(name, "the file is empty", by)
    if not _MAIN_RX.match(name):
        _refuse(name, "only the MAIN file is used here (a name like MAIN_11.30.csv)", by)
    why = _cnc.freshness_problem(name, modified_ms, now)
    if why:
        _refuse(name, why.replace("PositionBook", "MAIN"), by)
    head = data[:4096].decode("utf-8", "ignore")
    if "Product" not in head:
        _refuse(name, "this does not look like the MAIN file (no 'Product' column)", by)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name)
    part = root() / (safe + ".part")
    part.write_bytes(data)
    for old in [p for p in root().iterdir() if p.is_file() and _MAIN_RX.match(p.name) and p.name != safe]:
        old.unlink()
    part.replace(root() / safe)
    with _STATE_LOCK:
        _meta_path().write_text(json.dumps({"name": safe, "date": now.date().isoformat(), "by": by}), encoding="utf-8")
    ts, _ = main_time(safe)
    _event(f"Upload ACCEPTED: {safe} ({len(data):,} bytes) by {by or 'user'} - report time {ts}")
    return {"ok": True, "name": safe, "size": len(data)}


# ───────────────────────────── status for the card ─────────────────────────────
def slot_rows(now: dt.datetime = None) -> list:
    now = now or dt.datetime.now()
    d = now.date()
    times = slot_times()
    st = read_day(d)["slots"]
    attn = int(get_settings().get("attention_min", 60))
    cur = now.hour * 60 + now.minute
    live = _ACTIVE.get("slot") if _RUN_LOCK.locked() else None
    last_done = max([i for i in range(1, len(times) + 1) if _is_done(st.get(str(i)))] or [0])
    running_now = _RUN_LOCK.locked()
    rows = []
    for i, t in enumerate(times, start=1):
        rec = dict(st.get(str(i), {}))
        s = rec.get("status") or ""
        late = cur - t
        if live == i:
            state = "running"
        elif _is_done(rec):
            state = "done"
        elif s == "running" and not running_now:
            state, rec["msg"] = "failed", "Interrupted (the server restarted while it was running)."
        elif s == "failed":
            state = "failed"
        elif s == "stopped":
            state = "stopped"
        elif i < last_done:
            state = "missed"
        elif late < 0:
            state = "upcoming"
        elif late <= attn:
            state = "waiting"
        else:
            state = "attention"
        rows.append({"slot": i, "time": hhmm(t), "state": state, "msg": rec.get("msg", ""), "done_at": rec.get("done_at", ""),
                     "override": bool(rec.get("override")), "by": rec.get("by", ""), "trigger": rec.get("trigger", ""),
                     "report_time": rec.get("report_time", ""), "late_min": max(0, late) if state in ("waiting", "attention") else 0,
                     "attempts": int(rec.get("auto_attempts") or 0)})
    return rows


def attention_notes(rows, today=True) -> list:
    notes = []
    for r in rows:
        if r["state"] == "attention" and today:
            h, m = divmod(int(r["late_min"]), 60)
            notes.append(f"{r['time']} report is {h}h {m:02d}m late - upload the MAIN file / press Run")
    return notes


def status(now: dt.datetime = None) -> dict:
    now = now or dt.datetime.now()
    d = now.date()
    st = read_state()
    running = is_running()
    s = st.get("status", "idle")
    if s == "running" and not running:
        s = "failed"
        st["error"] = st.get("error") or "Interrupted (the server restarted while it was running)."
    set_ = get_settings()
    inputs = input_status(today=d)
    steps = {k: dict(v) for k, v in (st.get("steps") or {}).items()}
    for v in steps.values():
        if v.get("status") == "running" and not running:
            v["status"], v["msg"] = "failed", "interrupted (the server restarted or the step was stopped)"
    if not running and inputs[0]["ok"] and (steps.get("input") or {}).get("status", "") in ("failed", ""):
        steps["input"] = {"status": "done", "msg": "file in place: " + inputs[0]["name"], "at": ""}
    rows = slot_rows(now)
    notes = attention_notes(rows) if (_trading_day(d) and set_["auto"]) else []
    nxt = next((r for r in rows if r["state"] in ("upcoming", "waiting", "attention")), None)
    total = len(rows)
    done = sum(1 for r in rows if r["state"] == "done")
    extra = int(read_day(d).get("extra", 0))
    if running:
        top = "running"
    elif notes:
        top = "attention"
    else:
        top = s
    return {"name": NAME, "date": st.get("date"), "status": top, "run_status": s, "running": running, "fetching": False,
            "stopping": _cancelled() and running, "started": st.get("started", ""), "finished": st.get("finished", ""),
            "error": st.get("error", ""), "trigger": st.get("trigger", ""), "by": st.get("by", ""), "counts": st.get("counts", {}),
            "sent": st.get("sent", []), "alert_sent": bool(st.get("alert_sent")), "alert_reason": st.get("alert_reason", ""),
            "auto": bool(set_["auto"]), "needs_password": False, "runs": st.get("runs", 0), "slots": rows, "done": done, "total": total,
            "extra": extra, "attention": notes, "next": ({"slot": nxt["slot"], "time": nxt["time"]} if nxt else None),
            "report_time": st.get("report_time", ""), "trading_day": _trading_day(d),
            "settings": {k: set_[k] for k in ("times", "auto", "attention_min", "send_mail", "send_whatsapp", "alert_numbers",
                                                "report_numbers", "report_numbers_late", "split_time", "report_template")},
            "steps": [dict(key=k, label=STEP_LABEL[k], **steps.get(k, {"status": "", "msg": ""})) for k in STEP_ORDER],
            "inputs": inputs, "folder": str(root()), "log": recent_log(60)}
