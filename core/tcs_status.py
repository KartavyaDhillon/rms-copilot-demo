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
import threading
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


# Standalone download buttons that log in to TCS BANCS (pipeline_box action slots). They do not use the lock files, so
# their own "running" slot is what tells us the login is in use.
_ACTION_USERS = {("ctcl", "mcx90"): "CTCL vs NMASS (MCX files download)", ("ms15", "mcx90"): "MARGIN SHORTAGE 15-min (MCX files download)",
                 ("probable", "tcs"): "PROBABLE (TCS button: RSK383 + RSK335 + RSK136 search result)"}


def _uses(pipe):
    """Which of the two MCX files that box's run USES (its "MCX file" choice) -> 'MCX 90%' | 'MCX Deposit'."""
    try:
        from core import ctcl_mcx_source as src
        return src.LABELS.get(src.get_source(pipe), "MCX 90%")
    except Exception:
        return "MCX 90%"


def _auto_mcx_user(pipe):
    """The MCX download that is going right now was started BY THE AUTO RUN -> a clear name, else None."""
    try:
        from core import ms15_auto as A
        if pipe == "ms15":
            for (n, what), job in list(A._JOBS.items()):
                if what == "mcx" and A._job_alive(n, "mcx"):
                    return (f"MARGIN SHORTAGE 15-min - AUTO run {int(n):02d}: MCX 90% + MCX Deposit download "
                            f"(the run uses {_uses('ms15')})")
        if pipe == "ctcl":
            for n, v in ((A.read_state().get("ctcl") or {}).items()):
                if v == "running":
                    return (f"CTCL vs NMASS - AUTO MCX download for run {int(n):02d}: MCX 90% + MCX Deposit "
                            f"(the run uses {_uses('ctcl')})")
    except Exception:
        pass
    return None


def _action_holders():
    out = []
    try:
        from core import pipeline_box as PB
        for pipe, action, started in PB.running_actions():
            name = _ACTION_USERS.get((pipe, action))
            if not name:
                continue
            if action == "mcx90":                       # auto run or a person's button? and which file does the run use?
                name = _auto_mcx_user(pipe) or f"{name[:-1]} - started by hand, the run uses {_uses(pipe)})"
            el, since = None, ""
            try:
                t0 = dt.datetime.fromisoformat(str(started))
                el, since = max(0, int((dt.datetime.now() - t0).total_seconds())), t0.strftime("%H:%M:%S")
            except Exception:
                pass
            out.append({"name": name, "since": since, "elapsed_s": el, "source": "button"})
    except Exception:
        pass
    return out


def _next_users(now: dt.datetime) -> list:
    """[{'name', 'at': 'HH:MM', 'minutes': n}] for every automatic start still to come today, soonest first."""
    cur = now.hour * 60 + now.minute
    found = []

    def add(name, hhmm_text, lead=0, kind="auto", note=""):
        try:
            h, m = (int(x) for x in str(hhmm_text).split(":")[:2])
        except Exception:
            return
        t = h * 60 + m - lead
        if t < 0:
            t = 0
        if t >= cur:
            found.append({"name": name, "at": f"{t // 60:02d}:{t % 60:02d}", "minutes": t - cur, "kind": kind, "note": note})

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

    # 2) Scheduler schedules of processes that log in to MCX eclear (e.g. MCX HOURLY SURVEILLANCE, every hour)
    try:
        from core.scheduler import catalog, holidays, rules, service, store
        utc_now = service._now()
        for s in store.list_schedules("enabled"):
            pdef = catalog.get(s.get("process_key"))
            if pdef is None or "mcx_eclear" not in tuple(getattr(pdef, "uses", ()) or ()):
                continue
            tz = rules.get_tz(s.get("tz"))
            today = now.date()
            runs = []
            for u in rules.occurrences({"rule": s["rule"], "tz": s["tz"], "start_date": s.get("start_date"),
                                        "end_date": s.get("end_date"), "skip_holidays": s.get("skip_holidays")}, utc_now, holidays.is_holiday):
                loc = u.astimezone(tz)
                if loc.date() != today:
                    break
                runs.append(loc)
                if len(runs) >= 48:
                    break
            if runs:
                first = runs[0]
                add(f"{s.get('name') or pdef.name} (scheduler)", f"{first.hour}:{first.minute}", kind="scheduler",
                    note=f"{len(runs)} run(s) left today, then {runs[1].strftime('%H:%M')}" if len(runs) > 1 else "last run today")
    except Exception:
        pass

    # 3) MARGIN SHORTAGE auto run: its MCX download (MCX 90% + MCX Deposit) starts by itself a few minutes before each run.
    #    CTCL vs NMASS: the auto MCX download before each run (only when no fresh Margin Shortage file is there).
    auto_pipes = set()
    try:
        from core import ms15_auto as A
        ms = A.status()
        if ms["settings"].get("auto"):
            auto_pipes.add("ms15")
            for r in ms["runs"]:
                if r.get("state") in A.TERMINAL or r.get("state") == "running" or r.get("mcx") in ("done", "running"):
                    continue
                add(f"MARGIN SHORTAGE 15-min - AUTO run {r['run']:02d}: MCX 90% + MCX Deposit download (the run uses {_uses('ms15')})",
                    r["mcx_at"], kind="auto", note=f"run starts by itself at {r['time']}")
        if ms["settings"].get("ctcl_prefetch"):
            auto_pipes.add("ctcl")
            for r in ms.get("ctcl", []):
                if r.get("state"):
                    continue
                add(f"CTCL vs NMASS - AUTO MCX download for run {r['run']:02d} (the run uses {_uses('ctcl')})", r["mcx_at"],
                    kind="auto", note=f"only if no fresh MCX file is there · the run itself is manual ({r['time']})")
    except Exception:
        pass

    # 4) Manual boxes (auto OFF): a person presses their buttons around the run windows, and the run needs the MCX file by then
    try:
        for pipe, label in (("ms15", "MARGIN SHORTAGE 15-min"), ("ctcl", "CTCL vs NMASS")):
            if pipe in auto_pipes:
                continue
            for i in range(1, 40):
                v = os.getenv(f"{pipe.upper()}_RUN{i}_WINDOW", "").strip()
                if not v:
                    continue
                m = v.split("-")[0]
                add(f"{label} run {i} (manual, needs the {_uses(pipe)} file)", m, kind="manual", note=f"window {v}")
    except Exception:
        pass
    found.sort(key=lambda x: x["minutes"])
    return found


_NEXT_CACHE = {"t": 0.0, "min": -1, "val": []}
_STATUS_CACHE = {"t": 0.0, "val": None}
_LOCK = threading.Lock()


def _next_cached(now: dt.datetime) -> list:
    """'Who is next' is heavy (status of several modules + scheduler rules) and only changes by the minute: compute it at
    most once every 30 s for ALL open pages, not once per page per poll."""
    if time.time() - _NEXT_CACHE["t"] < 30 and _NEXT_CACHE["val"] is not None:
        return _NEXT_CACHE["val"]
    val = _next_users(now)
    _NEXT_CACHE.update(t=time.time(), val=val)
    return val


def _build(now: dt.datetime) -> dict:
    holders = []
    c = _card_holder()
    if c:
        holders.append(c)
    seen = {h["name"] for h in holders}
    for h in _box_holders() + _action_holders():
        if h["name"] not in seen:                       # the same download can show up in two places
            holders.append(h)
            seen.add(h["name"])
    state = "free" if not holders else ("busy" if len(holders) == 1 else "clash")
    nxt = _next_cached(now)
    return {"state": state, "holders": holders, "next": nxt[0] if nxt else None, "upcoming": nxt[:12],
            "now": now.strftime("%H:%M:%S")}


def status(now: dt.datetime = None) -> dict:
    """One shared answer for every browser: rebuilt at most every 2 s, and only one request rebuilds it at a time (the
    others wait for that result instead of each doing the same work)."""
    if now is not None:                                  # tests pass their own clock: no caching
        return _build(now)
    v = _STATUS_CACHE["val"]
    if v is not None and time.time() - _STATUS_CACHE["t"] < 2:
        return v
    with _LOCK:
        v = _STATUS_CACHE["val"]
        if v is not None and time.time() - _STATUS_CACHE["t"] < 2:
            return v
        v = _build(dt.datetime.now())
        _STATUS_CACHE.update(t=time.time(), val=v)
        return v
