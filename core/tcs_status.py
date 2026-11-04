"""core/tcs_status.py - ONE answer to "who is using the TCS BANCS (MCX eclear) login right now, and who is next?".

Read-only. It only LOOKS at what already exists, it never takes or releases a lock:
  * core/tcs_session_lock.py        - the unattended cards (LIVE ALLOCATION, MCX CASH MAINTENANCE, SHORT ALLOCATION auto)
  * sa_codes/mcx_deposit_downloader - the per-box lock files of the manual boxes (Short Allocation, CLI, Probable)
Next user (when nobody holds it): the earliest automatic start among LIVE ALLOCATION, MCX CASH MAINTENANCE and the
SHORT ALLOCATION auto run (its MCX deposit starts a few minutes before its run time).

state: 'free' | 'busy' (one holder) | 'clash' (two holders at the same time - the second login kicks out the first).
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOX_NAMES = {"sa": "SHORT ALLOCATION (manual MCX deposit)", "cli": "CLI PRUDENTIAL (MCX Web Allocation)",
             "probable": "PROBABLE (TCS button)"}


def _elapsed(started_epoch):
    try:
        return max(0, int(time.time() - float(started_epoch)))
    except Exception:
        return None


def _card_holder():
    """The unattended-card lock -> a holder dict or None."""
    try:
        from core import tcs_session_lock as L
        h = L.holder()
        if not h:
            return None
        try:
            started = L._path().stat().st_ctime
        except Exception:
            started = None
        return {"name": L.NAMES.get(h.get("owner"), h.get("owner") or "an automatic card"), "since": h.get("since", ""),
                "elapsed_s": _elapsed(started), "source": "card"}
    except Exception:
        return None


def _box_holders():
    """The manual boxes' own lock files -> a list of holder dicts."""
    out = []
    try:
        sa_dir = os.path.join(REPO, "sa_codes")
        if sa_dir not in sys.path:
            sys.path.insert(0, sa_dir)
        import importlib
        mdd = importlib.import_module("mcx_deposit_downloader")
        for o in getattr(mdd, "_MCX_OWNERS", ("sa", "cli", "probable")):
            st = mdd.mcx_lock_status(o) or {}
            if st.get("busy"):
                since = st.get("started_at") or ""
                el = None
                try:
                    el = max(0, int((dt.datetime.now() - dt.datetime.fromisoformat(str(since))).total_seconds()))
                except Exception:
                    pass
                out.append({"name": BOX_NAMES.get(o, o), "since": str(since)[11:19] if len(str(since)) >= 19 else str(since),
                            "elapsed_s": el, "source": "box"})
    except Exception:
        pass
    return out


def _next_users(now: dt.datetime) -> list:
    """[{'name', 'at': 'HH:MM', 'minutes': n}] for every automatic start still to come today, soonest first."""
    cur = now.hour * 60 + now.minute
    found = []

    def add(name, hhmm_text, lead=0):
        try:
            h, m = (int(x) for x in str(hhmm_text).split(":")[:2])
        except Exception:
            return
        t = h * 60 + m - lead
        if t < 0:
            t = 0
        if t >= cur:
            found.append({"name": name, "at": f"{t // 60:02d}:{t % 60:02d}", "minutes": t - cur})

    for mod, label in (("core.live_allocation", "LIVE ALLOCATION CAPTURE"), ("core.mcx_cash_maintenance", "MCX CASH MAINTENANCE")):
        try:
            import importlib
            m = importlib.import_module(mod)
            st = m.status()
            if not st.get("auto", True) or not st.get("trading_day", True):
                continue
            for s in st.get("slots", []):
                if s.get("state") in ("due", "upcoming"):
                    add(label, s.get("time"))
                    break
        except Exception:
            continue
    try:
        from core import sa_auto
        st = sa_auto.get_settings()
        state = sa_auto.read_state()
        for n in range(1, sa_auto.MAX_RUN + 1):
            cfg = st["runs"].get(str(n)) or {}
            rs = state["runs"].get(str(n), {})
            if not cfg.get("auto") or not cfg.get("time") or rs.get("status") in ("done", "failed", "stopped", "missed", "skipped", "running"):
                continue
            if n <= sa_auto.DEPOSIT_RUNS and not rs.get("deposit"):
                add(f"SHORT ALLOCATION run {n} (MCX deposit)", cfg["time"], lead=int(st.get("predl_min", 15)))
    except Exception:
        pass
    found.sort(key=lambda x: x["minutes"])
    return found


def status(now: dt.datetime = None) -> dict:
    now = now or dt.datetime.now()
    holders = []
    c = _card_holder()
    if c:
        holders.append(c)
    holders += _box_holders()
    state = "free" if not holders else ("busy" if len(holders) == 1 else "clash")
    nxt = _next_users(now)
    return {"state": state, "holders": holders, "next": nxt[0] if nxt else None, "upcoming": nxt[:5],
            "now": now.strftime("%H:%M:%S")}
