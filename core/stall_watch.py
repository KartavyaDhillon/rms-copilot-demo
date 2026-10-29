"""core/stall_watch.py - find out WHY the console hangs, without guessing.

Two simple tools, both write to the server window AND to logs/stall_watch.log:

* [SLOW]  - every request that took longer than SLOW_SECONDS (default 3) is printed with its path and how long it took.
* [STALL] - the console's main "lane" (the event loop that answers every page and button) was blocked for more than
            STALL_SECONDS (default 3). The exact line of code it was stuck on is printed, plus what every other thread was doing.

Nothing here changes behaviour; it only watches. A stall report names the culprit (for example a Backup-files merge).
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import threading
import time
import traceback

SLOW_SECONDS = float(os.getenv("RMS_SLOW_REQUEST_S", "3"))
STALL_SECONDS = float(os.getenv("RMS_STALL_S", "3"))
_LOCK = threading.Lock()
_STATE = {"beat": time.monotonic(), "loop_tid": None, "reported": False}


def _log_path() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "logs")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, "stall_watch.log")


def _emit(text: str) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {text}"
    print(line, flush=True)
    try:
        with _LOCK, open(_log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def log_slow(method: str, path: str, query: str, seconds: float) -> None:
    if seconds >= SLOW_SECONDS:
        _emit(f"[SLOW] {seconds:.1f}s  {method} {path}{('?' + query[:120]) if query else ''}")


def _tick(loop) -> None:
    _STATE["beat"] = time.monotonic()
    _STATE["loop_tid"] = threading.get_ident()
    _STATE["reported"] = False
    loop.call_later(0.5, _tick, loop)


def _watch() -> None:
    while True:
        time.sleep(1.0)
        late = time.monotonic() - _STATE["beat"]
        tid = _STATE["loop_tid"]
        if late < STALL_SECONDS or tid is None or _STATE["reported"]:
            continue
        _STATE["reported"] = True
        frames = sys._current_frames()
        out = [f"[STALL] the console's main lane has been BLOCKED for {late:.0f}s - it is stuck here:"]
        fr = frames.get(tid)
        if fr is not None:
            out.append("".join(traceback.format_stack(fr)[-14:]).rstrip())
        others = []
        for t in threading.enumerate():
            if t.ident == tid or t.ident not in frames or t.daemon and t.name.startswith("asyncio"):
                continue
            top = traceback.extract_stack(frames[t.ident])[-1]
            others.append(f"   thread {t.name}: {os.path.basename(top.filename)}:{top.lineno} in {top.name}")
        if others:
            out.append("other threads right now:\n" + "\n".join(others[:25]))
        _emit("\n".join(out))


def start(loop) -> None:
    """Call once from a startup hook (inside the running event loop)."""
    _STATE["beat"] = time.monotonic()
    loop.call_soon(_tick, loop)
    threading.Thread(target=_watch, name="stall-watch", daemon=True).start()
    _emit(f"[watch] stall watch started (reports requests over {SLOW_SECONDS:g}s and lane blocks over {STALL_SECONDS:g}s).")
