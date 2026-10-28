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
                if time.time() - p.stat().st_mtime > STALE_SEC:
                    p.unlink()                              # abandoned
                    continue
            except OSError:
                continue
        except OSError:
            pass
        if cancel() or time.time() - t0 > wait_s:
            return False
        if time.time() - noted > 60:
            h = holder() or {}
            log(f"MCX eclear login is in use by '{h.get('owner', 'another download')}' - waiting for it to finish...")
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
