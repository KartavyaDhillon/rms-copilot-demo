"""
core/live_allocation.py - LIVE ALLOCATION CAPTURE (replaces the hand-run nn.py + ss.py).

Eight times a day (default 10:12 12:10 13:27 15:10 16:53 18:55 20:55 22:55) it downloads, into numbered folders,

    <data>/LIVE ALLOCATION CAPTURE/BACKUPFILES AUTOMATED/<dd-mm-yyyy>/<1..8>/
        CM_ClientLevelCollaterals_<date>.csv  FO_...  CO_...      (NMASS - shared session, no captcha while it is alive)
        ClientNetEffectiveDeposits_<date>.csv                       (NCDEX - logs out afterwards)
        MCX_Web_Allocation_<NN>.xlsx                                (MCX eclear RSK383 - RSA code from the seed)

The folder layout and file names are exactly what nn.py / ss.py produced, so whatever picks the folder up the next day
needs no change.  Slot N is the Nth timing of the day (not a counter that moves on every start), so a retry of slot 3
fills folder 3 again and there is never a folder 9.

Both the scheduler (process "LIVE ALLOCATION CAPTURE") and the Process Center card call run_slot().  Per-slot results are
kept in <data>/LIVE ALLOCATION CAPTURE/_state/<dd-mm-yyyy>.json - outside the date folders, so the folders stay clean.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

NAME = "LIVE ALLOCATION CAPTURE"
FOLDER_NAME = "LIVE ALLOCATION CAPTURE"                 # the folder inside the console's data folder
PROCESS_KEY = "live_allocation_capture"
DEFAULT_TIMES = "10:12,12:10,13:27,15:10,16:53,18:55,20:55,22:55"
REPO = Path(__file__).resolve().parent.parent
SUB = "BACKUPFILES AUTOMATED"

# (label shown on the card, file-name pattern)
EXPECTED = [
    ("CM collaterals", re.compile(r"^CM_ClientLevelCollaterals_.*\.(csv|xlsx)$", re.I)),
    ("FO collaterals", re.compile(r"^FO_ClientLevelCollaterals_.*\.(csv|xlsx)$", re.I)),
    ("CO collaterals", re.compile(r"^CO_ClientLevelCollaterals_.*\.(csv|xlsx)$", re.I)),
    ("NCDEX net deposits", re.compile(r"^ClientNetEffectiveDeposits.*\.(csv|xlsx)$", re.I)),
    ("MCX web allocation", re.compile(r"^MCX_Web_Allocation_\d+\.xlsx$", re.I)),
]
STEP_FILES = {"nmass_ncdex": [0, 1, 2, 3], "mcx": [4]}
STEP_LABEL = {"nmass_ncdex": "NMASS + NCDEX", "mcx": "MCX web allocation"}

_AUTH_TAG = re.compile(r"\[(NMASS|MCX90|NCFE|MCX_DEPOSIT)_(AUTH_FAIL|LOCKED)\]", re.I)
_INTEREST = re.compile(r"\[[A-Z0-9_:]+\]|✅|❌|⚠️|🔴|🟢|===== STEP|saved|login|logout|signed|captcha|token|session|download|"
                       r"re-?login|expired|waiting|file ready|export|error|failed", re.I)

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_ACTIVE: dict = {}
_LOG = collections.deque(maxlen=300)          # (time, slot, line) - what the card shows while a capture runs


class Busy(Exception):
    pass


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("LIVE_ALLOC_DIR", "").strip()
    if v:
        p = Path(v)
    else:
        try:
            from core.scheduler import paths
            base = paths.data_root()
        except Exception:
            base = REPO / "data"
        p, old = base / FOLDER_NAME, base / "live_allocation"
        if old.is_dir() and not p.exists():                    # the folder used to be called "live_allocation"
            try:
                os.replace(old, p)
            except OSError:
                p = old                                        # in use right now: keep working there, rename next start
    p.mkdir(parents=True, exist_ok=True)
    return p


def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def parse_dstr(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%d-%m-%Y").date()


def day_dir(d: dt.date, create=False) -> Path:
    p = root() / SUB / dstr(d)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def slot_dir(d: dt.date, slot: int, create=False) -> Path:
    p = day_dir(d) / str(int(slot))
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def _state_dir() -> Path:
    p = root() / "_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cancel_flag() -> Path:
    return _state_dir() / ".cancel"


# ───────────────────────────── slot times ─────────────────────────────
def _parse_times(s: str):
    out = []
    for part in re.split(r"[,\s;]+", (s or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", part)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError(f"'{part}' is not a time like 13:27")
        out.append(int(m.group(1)) * 60 + int(m.group(2)))
    return out


def _settings_path() -> Path:
    return _state_dir() / "settings.json"


def get_settings() -> dict:
    """{'times': 'HH:MM,...' or '', 'auto': bool} saved from the card's Timings window."""
    try:
        s = json.loads(_settings_path().read_text(encoding="utf-8"))
    except Exception:
        s = {}
    return {"times": str(s.get("times") or "").strip(), "auto": bool(s.get("auto", True))}


def save_settings(times=None, auto=None, by="") -> dict:
    """Change the timings and/or the auto switch. Takes effect on the next tick (a few seconds) - nothing to restart."""
    cur = get_settings()
    if times is not None:
        mins = sorted(set(_parse_times(times)))
        if not mins:
            raise ValueError("enter at least one time like 10:12")
        if len(mins) > 12:
            raise ValueError("at most 12 timings")
        cur["times"] = ",".join(hhmm(m) for m in mins)
    if auto is not None:
        cur["auto"] = bool(auto)
    cur["by"], cur["at"] = by, _iso()
    with _STATE_LOCK:
        tmp = _settings_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        os.replace(tmp, _settings_path())
    return get_settings()


def auto_enabled() -> bool:
    return get_settings()["auto"]


def slot_times() -> list:
    """Minutes-after-midnight of the slots. From the card's Timings window (settings.json), else the process setting
    'slot_times' (Scheduler > Processes), else LIVE_ALLOC_TIMES in .env, else the default."""
    raw = get_settings()["times"]
    if raw:
        try:
            t = sorted(set(_parse_times(raw)))
            if t:
                return t
        except ValueError:
            pass
    try:
        from core.scheduler import catalog, proc_settings
        pdef = catalog.get(PROCESS_KEY)
        if pdef:
            raw = (proc_settings.view(pdef) or {}).get("slot_times", "")
    except Exception:
        pass
    raw = raw or os.getenv("LIVE_ALLOC_TIMES", "") or DEFAULT_TIMES
    try:
        t = _parse_times(raw)
    except ValueError:
        t = _parse_times(DEFAULT_TIMES)
    return t or _parse_times(DEFAULT_TIMES)


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def pick_slot(now: dt.datetime = None, times=None, early_grace_min=10) -> int:
    """Which numbered folder a run that starts at `now` belongs to: the latest slot whose time is already here
    (or within `early_grace_min` minutes), never below 1."""
    now = now or dt.datetime.now()
    times = times or slot_times()
    cur = now.hour * 60 + now.minute
    chosen = 1
    for i, t in enumerate(times, start=1):
        if t <= cur + early_grace_min:
            chosen = i
    return chosen


# ───────────────────────────── state ─────────────────────────────
def _state_path(d: dt.date) -> Path:
    return _state_dir() / f"{dstr(d)}.json"


def read_state(d: dt.date) -> dict:
    with _STATE_LOCK:
        try:
            return json.loads(_state_path(d).read_text(encoding="utf-8"))
        except Exception:
            return {"slots": {}}


def _write_state(d: dt.date, st: dict):
    with _STATE_LOCK:
        p = _state_path(d)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)


def _update(d: dt.date, slot: int, **kw) -> dict:
    with _STATE_LOCK:
        st = read_state(d)
        rec = st["slots"].setdefault(str(slot), {})
        rec.update(kw)
        _write_state(d, st)
        return rec


def _iso(t=None):
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


# ───────────────────────────── files ─────────────────────────────
def check_files(d: dt.date, slot: int) -> dict:
    """{'found': {label: filename}, 'missing': [labels]} - only real, non-empty files of THIS day's folder."""
    folder = slot_dir(d, slot)
    names = []
    try:
        names = [f for f in os.listdir(folder) if (folder / f).is_file()]
    except OSError:
        pass
    found, missing = {}, []
    for label, rx in EXPECTED:
        hit = next((n for n in sorted(names) if rx.match(n) and (folder / n).stat().st_size > 0), None)
        if hit:
            found[label] = hit
        else:
            missing.append(label)
    return {"found": found, "missing": missing}


def list_files(d: dt.date, slot: int) -> list:
    folder = slot_dir(d, slot)
    out = []
    try:
        for f in sorted(os.listdir(folder)):
            p = folder / f
            if p.is_file():
                s = p.stat()
                out.append({"name": f, "size": s.st_size, "mtime": _iso(dt.datetime.fromtimestamp(s.st_mtime))})
    except OSError:
        pass
    return out


def file_path(d: dt.date, slot: int, name: str) -> Path:
    """Resolve a file inside a slot folder; refuses anything that escapes it."""
    if not name or name != os.path.basename(name) or name.startswith("."):
        raise ValueError("bad file name")
    p = slot_dir(d, slot) / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return p


def list_dates() -> list:
    base = root() / SUB
    out = []
    try:
        for n in os.listdir(base):
            try:
                out.append(parse_dstr(n))
            except ValueError:
                continue
    except OSError:
        pass
    return sorted(out, reverse=True)


def zip_folder(d: dt.date, slot: int = None) -> Path:
    """A zip of the whole date folder (arcnames '<dd-mm-yyyy>/<slot>/<file>') or of one slot. Returns the temp file."""
    base = day_dir(d)
    if not base.is_dir():
        raise FileNotFoundError(dstr(d))
    tmpdir = _state_dir() / "_tmp"
    tmpdir.mkdir(exist_ok=True)
    for old in tmpdir.glob("*.zip"):                       # tidy up anything older than an hour
        try:
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink()
        except OSError:
            pass
    fd, path = tempfile.mkstemp(suffix=".zip", prefix=f"live_{dstr(d)}_", dir=tmpdir)
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        slots = [str(slot)] if slot else sorted(os.listdir(base), key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0))
        for s in slots:
            sd = base / s
            if not sd.is_dir():
                continue
            for f in sorted(os.listdir(sd)):
                if (sd / f).is_file():
                    z.write(sd / f, f"{dstr(d)}/{s}/{f}")
    return Path(path)


# ───────────────────────────── running a slot ─────────────────────────────
def cancel_requested() -> bool:
    return _cancel_flag().exists()


def request_cancel():
    _cancel_flag().write_text(_iso(), encoding="utf-8")
    p = _ACTIVE.get("proc")
    if p is not None:
        _kill(p)


def _kill(p):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=15)
        else:
            p.kill()
    except Exception:
        pass


def _python() -> str:
    return os.getenv("CTCL_PYTHON") or os.getenv("SA_PYTHON") or sys.executable


def _run_child(cmd, env, log, timeout_s, cancel) -> (int, list):
    """Run a child process, copy its interesting lines to `log`, stop it on cancel / timeout. -> (returncode, last lines)"""
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(REPO), text=True,
                         encoding="utf-8", errors="replace", creationflags=flags)
    _ACTIVE["proc"] = p
    tail = []

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
            if cancel():
                log("Stopped by request.")
                _kill(p)
                break
            if time.time() - t0 > timeout_s:
                log(f"Stopped: no result after {int(timeout_s // 60)} minutes.")
                _kill(p)
                break
            time.sleep(0.5)
        p.wait(timeout=20)
    finally:
        _ACTIVE.pop("proc", None)
    t.join(timeout=5)
    return (p.returncode if p.returncode is not None else -1), tail


def _auth_message(tail) -> str:
    for ln in tail:
        m = _AUTH_TAG.search(ln)
        if m:
            who = {"NMASS": "NMASS", "MCX90": "MCX", "NCFE": "NCDEX", "MCX_DEPOSIT": "MCX"}.get(m.group(1).upper(), m.group(1))
            if m.group(2).upper() == "LOCKED":
                return f"{who} account is LOCKED - get it unlocked, then run this slot again."
            return f"{who} login failed - the password looks wrong. Update it in Credentials."
        if "[MCX_TOKEN_FAIL]" in ln:
            return "MCX: the RSA code was not accepted (stopped after the allowed tries, nothing more was sent)."
    return ""


def step_nmass_ncdex(folder: Path, slot: int, log, cancel) -> dict:
    """NMASS (CM/FO/CO) + NCDEX net deposits, by the console's own NMASS_NCFE_API_MIX.PY: shared NMASS session (no
    captcha while it is alive, NMASS is NOT logged out), captcha auto-fill, NCDEX logs out when finished."""
    script = os.getenv("CTCL_NMASS_SCRIPT", str(REPO / "ctcl_codes" / "NMASS_NCFE_API_MIX.PY"))
    if not os.path.isfile(script):
        return {"ok": False, "msg": f"NMASS script not found: {script}"}
    from core import dl_lock
    waited = 0
    while True:                                           # the NMASS login is shared with CTCL / 15-minute - wait our turn
        try:
            dl_lock.acquire("liveallocation")
            break
        except dl_lock.NmassLoginBusyError as e:
            if cancel() or waited > 300:
                return {"ok": False, "msg": f"The NMASS login stayed busy (used by '{e.owner}') - try again."}
            if waited % 30 == 0:
                log(f"NMASS login is in use by '{e.owner}' - waiting...")
            time.sleep(5)
            waited += 5
    try:
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                    "NMASS_SAVE_DIR": str(folder), "NCFE_LOGOUT_AFTER": "1"})
        env.pop("NMASS_RUN_MODE", None)
        log(f"NMASS + NCDEX download into folder {slot} ...")
        alive = threading.Event()

        def keep_lock_fresh():                           # dl_lock treats a lock older than 3 min as abandoned
            while not alive.wait(45):
                try:
                    os.utime(dl_lock._LOCK_PATH, None)
                except OSError:
                    pass
        threading.Thread(target=keep_lock_fresh, daemon=True).start()
        try:
            rc, tail = _run_child([_python(), script], env, log, int(os.getenv("LIVE_ALLOC_NMASS_TIMEOUT_MIN", "20")) * 60, cancel)
        finally:
            alive.set()
    finally:
        dl_lock.release()
    chk = check_files(dt.date.today(), slot)
    need = [EXPECTED[i][0] for i in STEP_FILES["nmass_ncdex"]]
    miss = [n for n in need if n in chk["missing"]]
    if not miss:
        return {"ok": True, "msg": "CM, FO, CO and NCDEX deposits downloaded."}
    why = _auth_message(tail) or ("Stopped." if cancel() else f"missing: {', '.join(miss)} (script ended with code {rc}).")
    return {"ok": False, "msg": why}


def step_mcx(folder: Path, slot: int, log, cancel) -> dict:
    """MCX eclear RSK383 -> MCX_Web_Allocation_<NN>.xlsx, RSA code from the seed (always the next code, 4-min wait after
    a session expiry, stop after 2 wrong codes)."""
    env = os.environ.copy()
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "MCX_TOKEN_MODE": "auto",
                "MCX_DEPOSIT_CALLER": "liveallocation", "PYTHONPATH": str(REPO) + os.pathsep + env.get("PYTHONPATH", "")})
    cli = str(REPO / "core" / "live_allocation_mcx_cli.py")
    from core import tcs_session_lock as _L                  # the same eclear account is used by MCX CASH MAINTENANCE
    if not _L.acquire("liveallocation", wait_s=int(os.getenv("LIVE_ALLOC_TCS_WAIT_MIN", "20")) * 60, cancel=cancel, log=log):
        return {"ok": False, "msg": "The MCX eclear login stayed busy (MCX CASH MAINTENANCE) - try again."}
    log(f"MCX web allocation download into folder {slot} ...")
    try:
        rc, tail = _run_child([_python(), cli, "--out", str(folder), "--slot", str(int(slot))], env, log,
                              int(os.getenv("LIVE_ALLOC_MCX_TIMEOUT_MIN", "30")) * 60, cancel)
    finally:
        _L.release("liveallocation")
    chk = check_files(dt.date.today(), slot)
    if "MCX web allocation" not in chk["missing"]:
        return {"ok": True, "msg": "MCX web allocation downloaded."}
    if any("MCX_DEPOSIT_BUSY" in ln for ln in tail):
        return {"ok": False, "msg": "MCX is being used by another download right now - try again in a few minutes."}
    why = _auth_message(tail) or ("Stopped." if cancel() else f"no file was produced (code {rc}).")
    return {"ok": False, "msg": why}


STEPS = {"nmass_ncdex": step_nmass_ncdex, "mcx": step_mcx}      # tests replace these


def log_dir() -> Path:
    """Where the run logs go: logs/liveallocation - the same place the shared Logs window of the other cards reads."""
    v = os.getenv("LIVE_ALLOC_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / "liveallocation"
    except Exception:
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _open_run_log(slot, trigger="", by="", force=False):
    """One log file per capture: liveallocation_f<folder>__<yyyymmdd>_<hhmmss>.log. Never raises."""
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"liveallocation_f{int(slot)}__{now:%Y%m%d_%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== LIVE ALLOCATION CAPTURE - folder {slot} - {now:%d-%m-%Y %H:%M:%S} =====\n"
                f"started by: {by or trigger or 'timer'} ({trigger or 'auto'}){' - capture again (force)' if force else ''}\n\n")
        f.flush()
        return f
    except Exception:
        return None


def _stash_files(d, slot, labels, log=print):
    """'Capture again': move the old files of these labels out of the folder (to _state/_replaced, nothing is deleted)
    so the folder never holds two copies of the same file."""
    have = check_files(d, slot)["found"]
    names = [have[l] for l in labels if l in have]
    if not names:
        return
    dest = _state_dir() / "_replaced" / dstr(d) / str(slot) / dt.datetime.now().strftime("%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    try:                                                      # the aside copies are only a safety net: keep a week
        import shutil
        for old in (_state_dir() / "_replaced").iterdir():
            try:
                if (d - parse_dstr(old.name)).days > 7:
                    shutil.rmtree(old, ignore_errors=True)
            except ValueError:
                pass
    except OSError:
        pass
    for n in names:
        try:
            os.replace(slot_dir(d, slot) / n, dest / n)
        except OSError:
            pass
    log(f"Old copies moved aside ({len(names)} file(s)) before downloading again.")


PG_PIPELINE = "liveallocation"


def archive_slot(d, slot, log=print) -> int:
    """Copy this folder's files into PostgreSQL (table run_files, pipeline 'liveallocation', tag '<date>/<folder>') like the
    other processes do. Only when USE_POSTGRES=true; each file once; never raises. Returns how many were stored."""
    if os.getenv("USE_POSTGRES", "false").strip().lower() != "true":
        return 0
    try:
        from core.db_postgres import save_run_file
    except Exception as e:
        log(f"Postgres archive skipped: {e}")
        return 0
    rec = read_state(d)["slots"].get(str(slot), {})
    done = set(rec.get("archived") or [])
    n = 0
    for f in list_files(d, slot):
        key = f"{f['name']}|{f['size']}"
        if key in done:
            continue
        try:
            save_run_file(PG_PIPELINE, "backup", f["name"], file_path(d, slot, f["name"]).read_bytes(),
                          tag=f"{dstr(d)}/{slot}", run_date=d.isoformat())
            done.add(key)
            n += 1
        except Exception as e:                                   # noqa: BLE001 - a database hiccup must not fail a capture
            log(f"Postgres archive failed for {f['name']}: {type(e).__name__}: {e}")
    if n:
        _update(d, slot, archived=sorted(done))
        log(f"{n} file(s) archived to PostgreSQL.")
    return n


def label_of(name: str):
    for label, rx in EXPECTED:
        if rx.match(name):
            return label
    return None


def accept_upload(d, slot: int, name: str, data: bytes, by="", log=print) -> dict:
    """Put one file you captured by hand into folder `slot` of day `d`. Only the 5 known file names are accepted; a file
    of the same kind already there is moved aside (not deleted). Then the folder is archived to PostgreSQL."""
    name = os.path.basename(name or "")
    label = label_of(name)
    if not label:
        raise ValueError(f"'{name}' is not a file this capture makes (CM_/FO_/CO_ClientLevelCollaterals_*, "
                         f"ClientNetEffectiveDeposits*, MCX_Web_Allocation_<n>.xlsx)")
    if not data:
        raise ValueError(f"'{name}' is empty")
    folder = slot_dir(d, slot, create=True)
    _stash_files(d, slot, [label], log)
    tmp = folder / (name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, folder / name)
    return {"name": name, "label": label}


def finish_upload(d, slot: int, by="", log=print) -> dict:
    """After files were added by hand: archive them, and if the folder is complete record it as done."""
    chk = check_files(d, slot)
    if not chk["missing"]:
        rec = _update(d, slot, status="done", skipped=False, redo=False, msg=f"Files added by {by or 'a user'} (by hand).",
                      files=chk["found"], ended=_iso(), marked_by=by, marked_at=_iso())
    else:
        rec = _update(d, slot, files=chk["found"])
    archive_slot(d, slot, log)
    return {"complete": not chk["missing"], "missing": chk["missing"], "found": chk["found"], "state": rec.get("status", "")}


def is_running() -> bool:
    return _RUN_LOCK.locked()


def run_slot(slot: int, trigger="schedule", by="", log=print, cancelled=lambda: False, force=False) -> dict:
    """Capture one slot. `force=False` only fetches what is still missing (a retry never re-downloads what is there).
    Raises Busy if another capture is running. Returns the slot record."""
    slot = int(slot)
    _out = log
    fh = {}

    def log(m, _slot=slot):                                  # every line also goes to the card's live strip + the log file
        _LOG.append((dt.datetime.now().strftime("%H:%M:%S"), _slot, str(m)))
        if fh.get("f"):
            try:
                fh["f"].write(f"{dt.datetime.now():%H:%M:%S}  {m}\n")
                fh["f"].flush()
            except Exception:
                pass
        _out(m)
    n = len(slot_times())
    if not 1 <= slot <= max(n, 8):
        raise ValueError(f"slot must be 1..{max(n, 8)}")
    d = dt.date.today()
    prev0 = read_state(d)["slots"].get(str(slot), {})
    if prev0.get("skipped") and trigger != "manual":      # somebody pressed Skip: the timer must not start it again
        return prev0
    if not _RUN_LOCK.acquire(blocking=False):
        a = _ACTIVE.get("slot")
        raise Busy(f"Slot {a} is still being captured" if a else "Another capture is running")
    try:
        try:
            _cancel_flag().unlink()
        except OSError:
            pass
        _ACTIVE.update({"slot": slot, "date": dstr(d), "started": _iso()})
        folder = slot_dir(d, slot, create=True)
        fh["f"] = _open_run_log(slot, trigger, by, force)
        rec = _update(d, slot, status="running", started=_iso(), ended="", trigger=trigger, by=by, msg="",
                      steps={}, skipped=False, redo=False, override_done=False)
        stop = lambda: bool(cancelled()) or cancel_requested()
        steps = {}
        for key in ("nmass_ncdex", "mcx"):
            if stop():
                break
            have = check_files(d, slot)
            if not force and not any(EXPECTED[i][0] in have["missing"] for i in STEP_FILES[key]):
                steps[key] = {"ok": True, "msg": "already there - not fetched again"}
            else:
                if force:
                    _stash_files(d, slot, [EXPECTED[i][0] for i in STEP_FILES[key]], log)
                try:
                    steps[key] = STEPS[key](folder, slot, log, stop)
                except Exception as e:                   # one portal failing must not stop the next one
                    steps[key] = {"ok": False, "msg": f"{type(e).__name__}: {e}"[:300]}
                    log(f"{STEP_LABEL[key]} FAILED - {steps[key]['msg']}")
            _update(d, slot, steps=dict(steps))
        chk = check_files(d, slot)
        cur = read_state(d)["slots"].get(str(slot), {})
        if cur.get("skipped"):
            status, msg = "skipped", "Skipped by user."
        elif stop() and chk["missing"]:
            status, msg = "cancelled", "Stopped before it finished."
        elif not chk["missing"] and all(s.get("ok") for s in steps.values()):
            status, msg = "done", "All 5 files captured."
        else:
            bad = [f"{STEP_LABEL[k]}: {v.get('msg')}" for k, v in steps.items() if not v.get("ok")]
            status, msg = "failed", " | ".join(bad) or ("Missing: " + ", ".join(chk["missing"]))
        log(f"{'✅' if status in ('done', 'skipped') else '❌' if status == 'failed' else '⛔'} === Folder {slot}: {status.upper()} - {msg}")
        try:
            archive_slot(d, slot, log)
        except Exception:                                        # noqa: BLE001
            pass
        return _update(d, slot, status=status, ended=_iso(), msg=msg, files=chk["found"])
    finally:
        if fh.get("f"):
            try:
                fh["f"].close()
            except Exception:
                pass
        _ACTIVE.clear()
        _RUN_LOCK.release()


def skip_slot(slot: int, by="") -> dict:
    """Mark a slot as skipped. If it is the one running (stuck), stop it so the next slot is not blocked."""
    d = dt.date.today()
    running = _RUN_LOCK.locked() and _ACTIVE.get("slot") == int(slot)
    rec = _update(d, slot, skipped=True, skipped_by=by, skipped_at=_iso())
    if running:
        request_cancel()
        return rec
    return _update(d, slot, status="skipped", msg="Skipped by user.", ended=_iso())


def unskip_slot(slot: int) -> dict:
    d = dt.date.today()
    rec = read_state(d)["slots"].get(str(slot), {})
    if rec.get("status") == "skipped":
        return _update(d, slot, skipped=False, status="", msg="")
    return _update(d, slot, skipped=False)


def stop_running(by="") -> bool:
    if not _RUN_LOCK.locked():
        return False
    request_cancel()
    return True


def mark_undone(slot: int, by="") -> dict:
    """Override: treat this folder as NOT captured. At its time (now, if that has passed) the timer captures it again
    from scratch (old files are moved aside, not deleted)."""
    d = dt.date.today()
    return _update(d, slot, status="", skipped=False, redo=True, override_done=False, auto_attempts=0, last_auto="",
                   msg=f"Marked not done by {by or 'a user'} - will be captured again.", steps={}, marked_by=by,
                   marked_at=_iso())


def mark_done(slot: int, by="") -> dict:
    """Override: treat this folder as captured (e.g. the files were put there by hand). The timer leaves it alone."""
    d = dt.date.today()
    return _update(d, slot, status="done", skipped=False, redo=False, override_done=True,
                   msg=f"Marked done by {by or 'a user'} (override).", marked_by=by, marked_at=_iso())


# ───────────────────────────── the timer ─────────────────────────────
_TICK = {"thread": None, "stop": threading.Event(), "last": ""}


def _trading_day(d: dt.date) -> bool:
    try:
        from core.scheduler import helpers as H
        return bool(H.is_trading_day(d))
    except Exception:
        return d.weekday() < 5


def _grace_min() -> int:
    try:
        return max(5, int(os.getenv("LIVE_ALLOC_GRACE_MIN", "60")))
    except ValueError:
        return 60


def _retry_cfg():
    try:
        n = max(0, int(os.getenv("LIVE_ALLOC_RETRIES", "2")))
    except ValueError:
        n = 2
    try:
        gap = max(1, int(os.getenv("LIVE_ALLOC_RETRY_GAP_MIN", "5")))
    except ValueError:
        gap = 5
    return n, gap


def due_slot(now: dt.datetime = None):
    """-> (slot, force) the timer should start right now, or None.
    A slot is due when its time has come, it is not captured / skipped, and it is still inside its window (until the next
    timing starts, at most LIVE_ALLOC_GRACE_MIN after its own). A failed capture is retried (LIVE_ALLOC_RETRIES times,
    LIVE_ALLOC_RETRY_GAP_MIN apart). A folder marked 'not done' runs at its time whenever that is."""
    now = now or dt.datetime.now()
    d = now.date()
    if not _trading_day(d):
        return None
    times = slot_times()
    st = read_state(d)["slots"]
    cur = now.hour * 60 + now.minute
    retries, gap = _retry_cfg()
    for i, t in enumerate(times, start=1):
        if t > cur:
            break
        rec = st.get(str(i), {})
        if rec.get("skipped") or rec.get("override_done"):
            continue
        redo = bool(rec.get("redo"))
        if not redo:
            if not check_files(d, i)["missing"]:
                continue
            nxt = times[i] if i < len(times) else 24 * 60
            if cur >= min(nxt, t + _grace_min()):
                continue                                       # its window is over - the next timing takes over
            n = int(rec.get("auto_attempts") or 0)
            if n:
                if n > retries:
                    continue
                last = rec.get("last_auto") or ""
                try:
                    if (now - dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() < gap * 60:
                        continue
                except ValueError:
                    pass
        return i, redo
    return None


def tick(now: dt.datetime = None, runner=None):
    """One look at the clock (the timer calls this every ~20 s). Starts the due slot in the background.
    `runner(slot, force)` is replaceable for tests. Returns the slot started, or None."""
    if not auto_enabled() or _RUN_LOCK.locked():
        return None
    now = now or dt.datetime.now()
    hit = due_slot(now)
    if not hit:
        return None
    slot, force = hit
    d = now.date()
    rec = read_state(d)["slots"].get(str(slot), {})
    _update(d, slot, auto_attempts=int(rec.get("auto_attempts") or 0) + 1, last_auto=now.strftime("%Y-%m-%d %H:%M:%S"))

    def go():
        try:
            run_slot(slot, trigger="auto", by="timer", force=force)
        except Busy:
            pass
        except Exception as e:                                  # noqa: BLE001 - the timer must never die
            print(f"[live-alloc] timer run of folder {slot} failed: {type(e).__name__}: {e}", flush=True)
    if runner:
        runner(slot, force)
    else:
        threading.Thread(target=go, name=f"live-alloc-auto-{slot}", daemon=True).start()
    return slot


def start_ticker(interval_s: int = 20):
    """Start the background timer (idempotent). Called once when the server starts."""
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return False
    _TICK["stop"].clear()

    def loop():
        print("[live-alloc] timer started - folders are captured at their times by themselves.", flush=True)
        while not _TICK["stop"].wait(interval_s):
            try:
                tick()
            except Exception as e:                              # noqa: BLE001
                print(f"[live-alloc] timer error: {type(e).__name__}: {e}", flush=True)
    th = threading.Thread(target=loop, name="live-alloc-timer", daemon=True)
    _TICK["thread"] = th
    th.start()
    return True


def stop_ticker():
    _TICK["stop"].set()


# ───────────────────────────── downloads across days ─────────────────────────────
def dates_between(d_from: dt.date, d_to: dt.date) -> list:
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    return [x for x in sorted(list_dates()) if d_from <= x <= d_to]


def range_listing(d_from: dt.date, d_to: dt.date) -> list:
    """[{date, folders:[{slot, files:[{name,size,mtime}]}]}] for every dated folder in the range that exists."""
    out = []
    for d in dates_between(d_from, d_to):
        base = day_dir(d)
        slots = []
        try:
            names = sorted((n for n in os.listdir(base) if n.isdigit() and (base / n).is_dir()), key=int)
        except OSError:
            names = []
        for n in names:
            slots.append({"slot": int(n), "files": list_files(d, int(n))})
        out.append({"date": dstr(d), "folders": slots, "files": sum(len(s["files"]) for s in slots)})
    return out


def zip_range(d_from: dt.date, d_to: dt.date, slots=None) -> Path:
    """One zip for a range of days: every date folder, every numbered folder inside it, exactly the on-disk layout
    (<dd-mm-yyyy>/<slot>/<file>). `slots` limits it to some folder numbers (None = all). Raises FileNotFoundError if empty."""
    tmpdir = _state_dir() / "_tmp"
    tmpdir.mkdir(exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".zip", prefix="live_range_", dir=tmpdir)
    os.close(fd)
    want = {int(s) for s in slots} if slots else None
    n = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for d in dates_between(d_from, d_to):
            base = day_dir(d)
            try:
                names = sorted((x for x in os.listdir(base) if x.isdigit() and (base / x).is_dir()), key=int)
            except OSError:
                continue
            for s in names:
                if want is not None and int(s) not in want:
                    continue
                for f in sorted(os.listdir(base / s)):
                    if (base / s / f).is_file():
                        z.write(base / s / f, f"{dstr(d)}/{s}/{f}")
                        n += 1
    if not n:
        os.unlink(path)
        raise FileNotFoundError("no files in that range")
    return Path(path)


# ───────────────────────────── status for the card ─────────────────────────────
def status(d: dt.date = None, now: dt.datetime = None) -> dict:
    now = now or dt.datetime.now()
    d = d or now.date()
    today = d == now.date()
    times = slot_times()
    st = read_state(d)["slots"]
    cur_min = now.hour * 60 + now.minute
    live = _ACTIVE.get("slot") if (_RUN_LOCK.locked() and _ACTIVE.get("date") == dstr(d)) else None
    slots, count = [], {"done": 0, "failed": 0, "running": 0, "skipped": 0, "missed": 0, "upcoming": 0, "due": 0, "partial": 0}
    for i, t in enumerate(times, start=1):
        rec = dict(st.get(str(i), {}))
        chk = check_files(d, i)
        complete = not chk["missing"]
        s = rec.get("status") or ""
        if live == i:
            state = "running"
        elif rec.get("override_done"):
            state = "done"
        elif rec.get("redo"):                              # marked "not done": it will be captured again at its time
            state = "due" if (today and cur_min >= t) else "upcoming"
        elif s == "running":                              # recorded as running but nothing is: the server was restarted
            state = "failed"
            rec["msg"] = "Interrupted (the server restarted while it was running)."
        elif s == "skipped" or rec.get("skipped"):
            state = "skipped"
        elif complete:
            state = "done"
        elif s in ("failed", "cancelled"):
            state = "failed"
        elif chk["found"]:
            state = "partial" if not today or cur_min >= t else "upcoming"
        elif not today:
            state = "missed"
        elif cur_min < t:
            state = "upcoming"
        elif cur_min < t + 5:
            state = "due"
        else:
            state = "missed"
        count[state] = count.get(state, 0) + 1
        slots.append({"slot": i, "time": hhmm(t), "state": state, "msg": rec.get("msg", ""),
                      "started": rec.get("started", ""), "ended": rec.get("ended", ""), "by": rec.get("by", ""),
                      "trigger": rec.get("trigger", ""), "steps": rec.get("steps", {}),
                      "attempts": int(rec.get("auto_attempts") or 0), "redo": bool(rec.get("redo")),
                      "override": bool(rec.get("override_done")),
                      "found": chk["found"], "missing": chk["missing"], "files": len(chk["found"]),
                      "total": len(EXPECTED), "folder": str(slot_dir(d, i))})
    nxt = next((s for s in slots if s["state"] in ("due", "upcoming")), None)
    lg = [{"t": t, "slot": sl, "msg": m} for t, sl, m in list(_LOG)[-40:]]
    return {"log": lg, "date": dstr(d), "today": today, "now": now.strftime("%H:%M"), "running": live, "slots": slots,
            "auto": auto_enabled(), "times": ",".join(hhmm(x) for x in times), "trading_day": _trading_day(d),
            "grace_min": _grace_min(), "retries": _retry_cfg()[0],
            "counts": count, "next": ({"slot": nxt["slot"], "time": nxt["time"]} if nxt else None),
            "root": str(root()), "dates": [dstr(x) for x in list_dates()[:30]]}
