"""Guards against a file that is really there being reported as "missing" for a second or two.

Two parts:
  settle(check)  - ask again (a few times, half a second apart) before believing "not found".  A file that is being
                   replaced / scanned / copied by another program can be invisible for a moment; a real missing file
                   is still reported (after ~2 s).
  start_watch()  - a quiet background watcher that writes logs/file_flicker.log every time today's HOLDING /
                   RISK_REPORT disappears and comes back, with the time, how long, the folder contents and which
                   processes were running - so the real cause can be read from that log.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG = os.path.join(_REPO, "logs", "file_flicker.log")
_T = None


def settle(check, tries: int = 4, delay: float = 0.5):
    """Run check() until it returns something truthy (a found file, ok=True ...). Returns the last result."""
    res = None
    for i in range(max(1, tries)):
        try:
            res = check()
        except Exception:
            res = None
        if res:
            return res
        if i < tries - 1:
            time.sleep(delay)
    return res


def _w(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S.%f}"[:-3] + "  " + msg + "\n")
    except OSError:
        pass


def _running() -> str:
    try:
        from core import lock_manager as lm
        return ", ".join(f"{l.get('label')} (by {l.get('by')})" for l in lm.snapshot().get("locks", [])) or "nothing"
    except Exception:
        return "?"


def _watch() -> None:
    gone = {}                                  # name -> (time gone, size/mtime before)
    seen = {}
    while True:
        try:
            d = os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
            today = dt.date.today().strftime("%d%m%Y")
            for pre in ("HOLDING_", "RISK_REPORT_"):
                name = f"{pre}{today}.csv"
                p = os.path.join(d, name)
                try:
                    st = os.stat(p)
                    ok, info, err = True, f"{st.st_size:,} bytes, modified {dt.datetime.fromtimestamp(st.st_mtime):%H:%M:%S}", ""
                except OSError as e:
                    ok, info, err = False, "", f"{type(e).__name__}: {e}"
                if ok:
                    if name in gone:
                        t0, before = gone.pop(name)
                        _w(f"BACK     {name} - was invisible for {time.time() - t0:.1f}s; now {info} (before: {before}); running: {_running()}")
                    seen[name] = info
                elif name in seen and name not in gone:
                    gone[name] = (time.time(), seen[name])
                    try:
                        listing = ", ".join(sorted(os.listdir(d))[:40])
                    except OSError as e:
                        listing = f"(cannot list folder: {e})"
                    _w(f"MISSING  {name} - {err}; running: {_running()}; folder now: {listing}")
        except Exception:
            pass
        time.sleep(0.4)


def start_watch() -> None:
    global _T
    if _T and _T.is_alive():
        return
    _T = threading.Thread(target=_watch, name="file-flicker-watch", daemon=True)
    _T.start()
