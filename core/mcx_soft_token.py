"""core/mcx_soft_token.py - the MCX eclear RSA SecurID code, generated from the seed (.sdtid) instead of being typed.

Same seed the MCX HOURLY SURVEILLANCE process uses (Connections > MCX eclear > RSA token zip / .sdtid path).

Rules the LIVE ALLOCATION MCX download follows (set by the operations team):
  * ALWAYS fill the NEXT code, never the one that is on screen right now.
  * After a session expires in the middle of a run, wait RELOGIN_WAIT_SEC (default 4 minutes) before filling a code.
  * At most MAX_TOKEN_TRIES (2) wrong codes, then stop - more wrong codes would lock the account.
"""
import datetime as dt
import os
import tempfile
import time
import zipfile

import json
from pathlib import Path

MAX_TOKEN_TRIES = 2
RELOGIN_WAIT_SEC = int(os.getenv("MCX_RELOGIN_WAIT_SEC", "240"))


def load_token():
    """-> the securid token object for the configured seed. Raises RuntimeError with a plain message."""
    try:
        from securid.sdtid import SdtidFile
    except Exception:
        raise RuntimeError("The 'python-securid' package is not installed on this PC (pip install python-securid).")
    from core.scheduler import connections
    c = connections.get("mcx_eclear")
    path = c.get("rsa_sdtid")
    if not (path and os.path.exists(path)):
        zp = c.get("rsa_zip")
        if not zp or not os.path.exists(zp):
            raise RuntimeError("MCX eclear: the RSA token zip / .sdtid path is not set (or the file is missing on this "
                               "PC) - set it in Connections > MCX eclear.")
        with zipfile.ZipFile(zp) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".sdtid")]
            if not names:
                raise RuntimeError("No .sdtid seed found inside the RSA zip.")
            tmp = tempfile.mkdtemp(prefix="rsa_")
            z.extract(names[0], tmp)
            path = os.path.join(tmp, names[0])
    return SdtidFile(path).get_token()


def next_code(tok, attempt=1, sleep=time.sleep, now_fn=lambda: dt.datetime.now(dt.timezone.utc), log=print):
    """The code of the NEXT time window.

    attempt 1: the next window's code right away.
    attempt 2 (the first one was refused): let that window pass, then take the next one again - a refused code is
               never sent twice, and the current code is never used.
    """
    if attempt > 1:
        wait = tok.time_left(now_fn()) + 2
        log(f"Token was refused - waiting {int(wait)}s for the next code window (no code is ever sent twice).")
        sleep(wait)
    return tok.at(now_fn() + dt.timedelta(seconds=tok.interval))


def relogin_wait(cancelled=lambda: False, sleep=time.sleep, log=print, seconds=None):
    """Hold for RELOGIN_WAIT_SEC after a session expired mid-run, so the fresh code is a clean one and a login storm
    cannot lock the account. Returns False if the run was cancelled while waiting."""
    total = RELOGIN_WAIT_SEC if seconds is None else seconds
    left = total
    log(f"MCX session expired - waiting {total // 60} min {total % 60}s before filling the next token.")
    while left > 0:
        if cancelled():
            return False
        step = min(5, left)
        sleep(step)
        left -= step
        if left > 0 and left % 60 < 5:
            log(f"  ...{left // 60 + (1 if left % 60 else 0)} min to go")
    return True


# ── the dashboard switch "Token auto-fill" (Process Center > Auto-fill CAPTCHA / TOKEN), same idea as the captcha switch ──
def _switch_path() -> Path:
    v = os.getenv("MCX_TOKEN_SWITCH_FILE", "").strip()
    if v:
        return Path(v)
    try:
        from core.scheduler import paths
        base = Path(paths.data_root())
    except Exception:
        base = Path(__file__).resolve().parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "mcx_token_auto.json"


def switch_state() -> dict:
    """{'enabled': bool, 'by', 'at'} - default ON (the unattended cards need it)."""
    try:
        d = json.loads(_switch_path().read_text(encoding="utf-8"))
        return {"enabled": bool(d.get("enabled", True)), "by": str(d.get("by", "")), "at": str(d.get("at", ""))}
    except Exception:
        return {"enabled": True, "by": "", "at": ""}


def set_switch(enabled: bool, who: str = "") -> dict:
    st = {"enabled": bool(enabled), "by": who, "at": dt.datetime.now().isoformat(timespec="seconds")}
    _switch_path().write_text(json.dumps(st), encoding="utf-8")
    return st


def seed_ok() -> bool:
    try:
        load_token()
        return True
    except Exception:
        return False


def auto_mode() -> bool:
    """True = the RSA code is generated, not typed. Needs the switch ON, and either an unattended caller
    (MCX_TOKEN_MODE=auto) or a seed that can be loaded."""
    if not switch_state()["enabled"]:
        return False
    if os.getenv("MCX_TOKEN_MODE", "").strip().lower() == "auto":
        return True
    return seed_ok()
