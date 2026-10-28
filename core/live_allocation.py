"""
core/live_allocation.py - LIVE ALLOCATION CAPTURE (replaces the hand-run nn.py + ss.py).

Eight times a day (default 10:12 12:10 13:27 15:10 16:53 18:55 20:55 22:55) it downloads, into numbered folders,

    <data>/live_allocation/BACKUPFILES AUTOMATED/<dd-mm-yyyy>/<1..8>/
        CM_ClientLevelCollaterals_<date>.csv  FO_...  CO_...      (NMASS - shared session, no captcha while it is alive)
        ClientNetEffectiveDeposits_<date>.csv                       (NCDEX - logs out afterwards)
        MCX_Web_Allocation_<NN>.xlsx                                (MCX eclear RSK383 - RSA code from the seed)

The folder layout and file names are exactly what nn.py / ss.py produced, so whatever picks the folder up the next day
needs no change.  Slot N is the Nth timing of the day (not a counter that moves on every start), so a retry of slot 3
fills folder 3 again and there is never a folder 9.

Both the scheduler (process "LIVE ALLOCATION CAPTURE") and the Process Center card call run_slot().  Per-slot results are
kept in <data>/live_allocation/_state/<dd-mm-yyyy>.json - outside the date folders, so the folders stay clean.
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
            p = paths.data_root() / "live_allocation"
        except Exception:
            p = REPO / "data" / "live_allocation"
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


def slot_times() -> list:
    """Minutes-after-midnight of the 8 slots. From the process setting 'slot_times' (Scheduler > Processes), else
    LIVE_ALLOC_TIMES in .env, else the default."""
    raw = ""
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
    log(f"MCX web allocation download into folder {slot} ...")
    rc, tail = _run_child([_python(), cli, "--out", str(folder), "--slot", str(int(slot))], env, log,
                          int(os.getenv("LIVE_ALLOC_MCX_TIMEOUT_MIN", "30")) * 60, cancel)
    chk = check_files(dt.date.today(), slot)
    if "MCX web allocation" not in chk["missing"]:
        return {"ok": True, "msg": "MCX web allocation downloaded."}
    if any("MCX_DEPOSIT_BUSY" in ln for ln in tail):
        return {"ok": False, "msg": "MCX is being used by another download right now - try again in a few minutes."}
    why = _auth_message(tail) or ("Stopped." if cancel() else f"no file was produced (code {rc}).")
    return {"ok": False, "msg": why}


STEPS = {"nmass_ncdex": step_nmass_ncdex, "mcx": step_mcx}      # tests replace these


def is_running() -> bool:
    return _RUN_LOCK.locked()


def run_slot(slot: int, trigger="schedule", by="", log=print, cancelled=lambda: False, force=False) -> dict:
    """Capture one slot. `force=False` only fetches what is still missing (a retry never re-downloads what is there).
    Raises Busy if another capture is running. Returns the slot record."""
    slot = int(slot)
    _out = log

    def log(m, _slot=slot):                                  # every line also goes to the card's live strip
        _LOG.append((dt.datetime.now().strftime("%H:%M:%S"), _slot, str(m)))
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
        rec = _update(d, slot, status="running", started=_iso(), ended="", trigger=trigger, by=by, msg="",
                      steps={}, skipped=False)
        stop = lambda: bool(cancelled()) or cancel_requested()
        steps = {}
        for key in ("nmass_ncdex", "mcx"):
            if stop():
                break
            have = check_files(d, slot)
            if not force and not any(EXPECTED[i][0] in have["missing"] for i in STEP_FILES[key]):
                steps[key] = {"ok": True, "msg": "already there - not fetched again"}
            else:
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
        return _update(d, slot, status=status, ended=_iso(), msg=msg, files=chk["found"])
    finally:
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
                      "found": chk["found"], "missing": chk["missing"], "files": len(chk["found"]),
                      "total": len(EXPECTED), "folder": str(slot_dir(d, i))})
    nxt = next((s for s in slots if s["state"] in ("due", "upcoming")), None)
    lg = [{"t": t, "slot": sl, "msg": m} for t, sl, m in list(_LOG)[-14:] if live is None or sl == live]
    return {"log": lg, "date": dstr(d), "today": today, "now": now.strftime("%H:%M"), "running": live, "slots": slots,
            "counts": count, "next": ({"slot": nxt["slot"], "time": nxt["time"]} if nxt else None),
            "root": str(root()), "dates": [dstr(x) for x in list_dates()[:30]]}
