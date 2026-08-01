"""
core/dl_lock.py — narrow cross-process lock for the ONE genuine shared
resource between the ctcl / ms15 / cli pipelines: NMASS_NCFE_API_MIX.PY's
captcha image + session-cookie pickle, written to a FIXED, non-pipe-specific
path (D:\\15 minutes\\codes\\CAPTCHA & SESSION\\...) regardless of which pipe
invoked it. Two logins landing at the same instant would race on that same
captcha.png / processed_captcha.png / session_cookies.pkl, corrupting OCR
routing and clobbering each other's NSCCL session — a real conflict, unlike
the rest of each pipeline's work (which reads/writes pipe-specific folders
via NMASS_SAVE_DIR and doesn't need any lock at all).

Same pattern as sa_codes/mcx_deposit_downloader.py's _acquire_mcx_lock —
a lock FILE, not a threading.Lock(), because the three callers run in
different OS processes (ctcl/ms15's manual Download buttons run the
subprocess directly from the main server process, but CLI's download runs
INSIDE its own child subprocess spawned by pipeline_box.stream_pipeline —
an in-memory lock in the main process is invisible to that child).

This lock guards ONLY the ~40-50 second NMASS_NCFE_API_MIX.PY call itself —
it must NEVER be confused with (or replace) each pipe's own independent
run-lock in pipeline_box.py. A pipe's Run button is blocked by this only for
the brief moment it's actually logging in; the rest of its multi-minute
compute/email/WhatsApp phase never touches this at all.
"""
import os
import json
import time
import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCK_PATH = os.path.join(REPO, "logs", ".nmass_login_lock")
_STALE_SECS = 180   # a real login+download finishes in ~40-50s; generous buffer
                     # before a crashed holder's leftover lock is treated as gone


class NmassLoginBusyError(Exception):
    """Raised when another pipe's NMASS/NCFE login is genuinely in progress
    right now. Callers should catch this SPECIFICALLY (before any generic
    Exception handler) so the UI shows a clear, short-wait message instead of
    a vague failure — this is a ~1-minute wait, not a real error."""
    def __init__(self, owner, started_at=None):
        self.owner = owner
        self.started_at = started_at
        super().__init__(f"NMASS/NCDEX login already in progress (started by '{owner}')")


def _clear_stale():
    try:
        if os.path.isfile(_LOCK_PATH) and (time.time() - os.path.getmtime(_LOCK_PATH)) > _STALE_SECS:
            os.remove(_LOCK_PATH)
    except OSError:
        pass


def acquire(owner):
    """owner: 'ctcl' | 'ms15' | 'cli' — which pipe is about to log in.
    Raises NmassLoginBusyError if another pipe already holds it."""
    os.makedirs(os.path.dirname(_LOCK_PATH), exist_ok=True)
    _clear_stale()
    try:
        fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        other, started = owner, None
        try:
            data = json.load(open(_LOCK_PATH, encoding="utf-8"))
            other = data.get("owner", owner)
            started = data.get("started_at")
        except Exception:
            pass
        raise NmassLoginBusyError(other, started)
    with os.fdopen(fd, "w") as f:
        json.dump({"owner": owner, "pid": os.getpid(),
                   "started_at": datetime.datetime.now().isoformat()}, f)


def release():
    try:
        os.remove(_LOCK_PATH)
    except OSError:
        pass


def status():
    """Read-only peek — used by lock_manager.snapshot() so the UI can show a
    brief 'NMASS login in progress' note without disabling anything else."""
    try:
        st = os.stat(_LOCK_PATH)
        if (time.time() - st.st_mtime) > _STALE_SECS:
            return {"busy": False}
        data = json.load(open(_LOCK_PATH, encoding="utf-8"))
        return {"busy": True, "owner": data.get("owner"), "started_at": data.get("started_at")}
    except FileNotFoundError:
        return {"busy": False}
    except Exception:
        return {"busy": False}
