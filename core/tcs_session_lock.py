"""core/tcs_session_lock.py - one TCS BANCS (eclear.mcxccl.com) login at a time for the unattended cards.

LIVE ALLOCATION CAPTURE (RSK383) and MCX CASH MAINTENANCE (RSK383 + RSK335) use the SAME eclear account. A second login
kicks the first one out in the middle of its export, so the second card waits its turn here instead.

A lock FILE (created with O_EXCL) works across the card's own threads and its child processes alike. A lock whose
owner stopped refreshing it for STALE_SEC (default 20 min) is treated as abandoned.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

STALE_SEC = int(os.getenv("TCS_SESSION_LOCK_STALE_SEC", "1200"))


def _path() -> Path:
    base = os.getenv("TCS_SESSION_LOCK_DIR", "").strip() or tempfile.gettempdir()
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p / "rms_tcs_session.lock"


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import psutil                                        # type: ignore
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259             # STILL_ACTIVE
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


NAMES = {"liveallocation": "LIVE ALLOCATION CAPTURE", "mcxcash": "MCX CASH MAINTENANCE"}


def holder():
    """-> {'owner','since'} of the current holder, or None."""
    p = _path()
    try:
        age = time.time() - p.stat().st_mtime
        if age > STALE_SEC:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def holder_raw(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def acquire(owner: str, wait_s: int = 900, cancel=lambda: False, log=print, sleep=time.sleep) -> bool:
    """Take the lock, waiting up to wait_s for whoever has it. False = could not get it (timed out / cancelled)."""
    p = _path()
    t0 = time.time()
    noted = 0
    while True:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"owner": owner, "pid": os.getpid(), "since": time.strftime("%H:%M:%S")}, fh)
            return True
        except FileExistsError:
            try:
                h = holder_raw(p)
                if time.time() - p.stat().st_mtime > STALE_SEC or (h and not _pid_alive(h.get("pid"))):
                    p.unlink()                              # abandoned: too old, or the server that made it is gone (restart)
                    continue
                if h and h.get("owner") == owner and h.get("pid") == os.getpid():
                    p.unlink()                              # left over from an earlier run of this same card (it can only run once at a time)
                    continue
            except OSError:
                continue
        except OSError:
            pass
        if cancel() or time.time() - t0 > wait_s:
            return False
        if time.time() - noted > 60:
            h = holder() or {}
            who = NAMES.get(h.get("owner"), "another download")
            log(f"Waiting: the MCX login is being used by {who} right now (started {h.get('since', '?')}). This run continues by itself when that one finishes.")
            noted = time.time()
        sleep(3)


def touch():
    try:
        os.utime(_path(), None)
    except OSError:
        pass


def release(owner: str = ""):
    p = _path()
    try:
        h = json.loads(p.read_text(encoding="utf-8"))
        if owner and h.get("owner") != owner:
            return
    except Exception:
        pass
    try:
        p.unlink()
    except OSError:
        pass
