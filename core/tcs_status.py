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


# Standalone download buttons that log in to TCS BANCS (pipeline_box action slots). They do not use the lock files, so
# their own "running" slot is what tells us the login is in use.
_ACTION_USERS = {("ctcl", "mcx90"): "CTCL vs NMASS (MCX 90% download)", ("ms15", "mcx90"): "MARGIN SHORTAGE 15-min (MCX 90% download)",
                 ("probable", "tcs"): "PROBABLE (TCS button)"}


def _action_holders():
    out = []
    try:
        from core import pipeline_box as PB
        for pipe, action, started in PB.running_actions():
            name = _ACTION_USERS.get((pipe, action))
            if not name:
                continue
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

    # 3) Manual boxes: a person presses their buttons around the run windows, and the run needs the MCX file by then
    try:
        for pipe, label in (("ms15", "MARGIN SHORTAGE 15-min"), ("ctcl", "CTCL vs NMASS")):
            for i in range(1, 40):
                v = os.getenv(f"{pipe.upper()}_RUN{i}_WINDOW", "").strip()
                if not v:
                    continue
                m = v.split("-")[0]
                add(f"{label} run {i} (manual, needs the MCX file)", m, kind="manual", note=f"window {v}")
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
    seen = {h["name"] for h in holders}
    for h in _box_holders() + _action_holders():
        if h["name"] not in seen:                       # the same download can show up in two places
            holders.append(h)
            seen.add(h["name"])
    state = "free" if not holders else ("busy" if len(holders) == 1 else "clash")
    nxt = _next_users(now)
    return {"state": state, "holders": holders, "next": nxt[0] if nxt else None, "upcoming": nxt[:12],
            "now": now.strftime("%H:%M:%S")}
