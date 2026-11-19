"""
core/ops_sched.py - today's SCHEDULER slots for the Dashboard (read-only).

The Dashboard's "runs done" used to count only the four manual boxes (CTCL, SA, MS15, CLI).
Every enabled schedule also fires on its own timetable, so each firing time today is a "slot":

    done      the run finished (Success, or checked-and-nothing-to-do)
    failed    the run failed
    running   running / queued / waiting for a retry
    missed    its time passed and nothing ran
    due       its time has just arrived - the scheduler is about to start it
    upcoming  later today

Never raises: on any problem it returns empty lists so the Dashboard still renders.
"""
import datetime as dt

UTC = dt.timezone.utc
_DUE_GRACE_MIN = 5          # a slot this fresh with no run yet = "starting", beyond it = "missed"


def _classify(execs, occ, now):
    ex = execs.get(occ)
    if ex:
        st = (ex.get("status") or "").upper()
        if st in ("SUCCESS", "SKIPPED"):
            return "done"
        if st == "FAILED":
            return "failed"
        if st in ("RUNNING", "QUEUED", "RETRY_WAIT"):
            return "running"
        if st == "MISSED":
            return "missed"
        if st == "CANCELLED":
            return "missed"
    if occ > now:
        return "upcoming"
    return "due" if (now - occ).total_seconds() <= _DUE_GRACE_MIN * 60 else "missed"


_SLOT_STATE = {"done": "done", "skipped": "done", "failed": "failed", "stopped": "failed", "partial": "failed", "missed": "missed",
               "running": "running", "due": "due", "waiting": "due", "attention": "missed", "upcoming": "upcoming"}

# Cards with ONE timed auto-run a day: (name, module, status-key of the time, default time)
_SINGLE_CARDS = [("ENTERPRISE RISK REPORT", "core.enterprise_risk", "run_at", "10:30"),
                 ("MTF ORDER FILE", "core.mtf_order_file", "run_at", "10:00"),
                 ("MTF CONCENTRATION", "core.mtf_concentration", "run_at", "09:30")]


def _hm(text, default):
    try:
        h, m = str(text or default).strip().replace(".", ":").split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        h, m = default.split(":")
        return int(h) * 60 + int(m)


def _is_today(v, d):
    return str(v or "")[:10] in (d.isoformat(), d.strftime("%d-%m-%Y"))


def single_item(name, st, run_min, now):
    """One daily slot of a card from its status(): done / failed / running by the card's own state, else by the clock."""
    if not st.get("auto"):
        return None                                          # auto OFF = it does not run by itself = not a daily slot
    s = str(st.get("status") or "").lower() if _is_today(st.get("date"), now.date()) else ""
    cur = now.hour * 60 + now.minute
    if st.get("running"):
        state = "running"
    elif s in ("done", "ok", "success"):
        state = "done"
    elif s == "failed":
        state = "failed"
    elif cur < run_min:
        state = "upcoming"
    else:
        state = "due" if cur - run_min <= _DUE_GRACE_MIN else "missed"
    return {"id": name, "pipe": "sched", "pipe_name": name, "source": "scheduler", "window": f"{run_min // 60:02d}:{run_min % 60:02d}",
            "start_min": run_min, "status": state, "schedule_id": name.lower().replace(" ", "-")}


def _files70_ran_today(f, today):
    """The newest 76 FILES result that FINISHED today. It downloads the PREVIOUS trading day's files, so its result file is
    named after that earlier day (last_<that day>.json) - looking only for today's file name made a good run show 'Missed'."""
    import json
    best, best_at = {}, ""
    try:
        for p in f.LOG_DIR.glob("last_*.json"):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            at = str(r.get("finished") or "")
            if at[:10] == today.isoformat() and at > best_at:
                best, best_at = r, at
    except Exception:
        pass
    return best


def card_items(now):
    """Daily slots of the KPI cards that have their own timer and were not counted before (never raises)."""
    import importlib
    out = []
    try:                                                     # OBLIGATION: 8 timed slots (its own slot table)
        from core import obligation_card as oc
        st = oc.status(now)
        if st.get("auto") and st.get("trading_day"):
            for sl in st.get("slots") or []:
                h, m = str(sl["time"]).split(":")
                out.append({"id": f"OBLIGATION REPORT - {sl['time']}", "pipe": "sched", "pipe_name": "OBLIGATION REPORT",
                            "source": "scheduler", "window": sl["time"], "start_min": int(h) * 60 + int(m),
                            "status": _SLOT_STATE.get(sl.get("state"), "upcoming"), "schedule_id": "obligation"})
    except Exception:
        pass
    for name, mod, key, default in _SINGLE_CARDS:
        try:
            m = importlib.import_module(mod)
            if not m._trading_day(now.date()):
                continue
            st = m.status()
            it = single_item(name, st, _hm((st.get("settings") or {}).get(key), default), now)
            if it:
                out.append(it)
        except Exception:
            continue
    try:                                                     # 76 FILES DOWNLOADER: one auto run from F76_AUTO_TIME
        from core import files70 as f
        if f.auto_on() and f._trading_day(now.date()):
            last = _files70_ran_today(f, now.date())
            ok = last.get("ok")
            st = {"auto": True, "date": now.date().isoformat(), "running": f.is_running(),
                  "status": "done" if (ok is True or str(last.get("status", "")).lower() in ("done", "ok", "success")) else
                            ("failed" if (ok is False or str(last.get("status", "")).lower() == "failed") else "")}
            it = single_item("76 FILES DOWNLOADER", st, _hm(f.auto_time(), "09:00"), now)
            if it:
                out.append(it)
    except Exception:
        pass
    return out


def today(now=None):
    """-> {"items": [...], "done": n, "total": n, "failed": n, "running": n, "missed": n}"""
    empty = {"items": [], "done": 0, "total": 0, "failed": 0, "running": 0, "missed": 0}
    try:
        from core.scheduler import store, rules, holidays, catalog
        now = now or dt.datetime.now(UTC)
        tz = rules.get_tz(rules.DEFAULT_TZ)
        local = now.astimezone(tz)
        day0 = dt.datetime.combine(local.date(), dt.time(0, 0), tzinfo=tz).astimezone(UTC)
        day1 = day0 + dt.timedelta(days=1)

        by_sched = {}
        for r in store.list_executions(date_from=day0 - dt.timedelta(hours=6), date_to=day1, limit=3000):
            by_sched.setdefault(r["schedule_id"], []).append(r)

        items = []
        try:
            from core import live_allocation as _la
            la_auto = _la.auto_enabled()
        except Exception:
            _la, la_auto = None, False
        for s in store.list_schedules("enabled"):
            if la_auto and s["process_key"] == _la.PROCESS_KEY:
                continue                                     # the card's own timer owns these slots (added below)
            pdef = catalog.get(s["process_key"])
            pname = pdef.name if pdef else s["process_key"]
            stz = rules.get_tz(s.get("tz"))
            try:
                occs = rules.between(s, day0 - dt.timedelta(seconds=1), day1 - dt.timedelta(seconds=1), holidays.is_holiday)
            except Exception:
                continue
            execs = {}
            for r in sorted(by_sched.get(s["id"], []), key=lambda x: x.get("created_utc") or ""):   # latest attempt wins
                u = store.parse_ts(r.get("scheduled_for_utc"))
                if u:
                    execs[u] = r
            manual_ok = [store.parse_ts(r.get("started_utc") or r.get("created_utc"))
                         for r in by_sched.get(s["id"], [])
                         if (r.get("trigger_type") or "").lower() == "manual" and r.get("status") == "SUCCESS"]
            manual_ok = sorted(m for m in manual_ok if m)
            for occ in occs:
                st = _classify(execs, occ, now)
                if st == "missed" and occ not in execs:
                    hit = next((m for m in manual_ok if m >= occ - dt.timedelta(minutes=30)), None)
                    if hit:                                  # somebody ran it by hand around that time
                        manual_ok.remove(hit)
                        st = "done"
                lt = occ.astimezone(stz)
                items.append({"id": s["name"], "pipe": "sched", "pipe_name": pname, "source": "scheduler",
                              "window": lt.strftime("%H:%M"), "start_min": lt.hour * 60 + lt.minute,
                              "status": st, "schedule_id": s["id"]})
        if la_auto:                                          # LIVE ALLOCATION CAPTURE: one item per folder, from its card
            try:
                st = _la.status(local.date(), local.replace(tzinfo=None))
                if st.get("trading_day"):
                    cmap = {"done": "done", "skipped": "done", "failed": "failed", "partial": "failed", "missed": "missed",
                            "running": "running", "due": "due", "upcoming": "upcoming"}
                    for sl in st.get("slots", []):
                        h, m = sl["time"].split(":")
                        items.append({"id": f"LIVE ALLOCATION - folder {sl['slot']}", "pipe": "sched",
                                      "pipe_name": "LIVE ALLOCATION CAPTURE", "source": "scheduler", "window": sl["time"],
                                      "start_min": int(h) * 60 + int(m), "status": cmap.get(sl["state"], "upcoming"),
                                      "schedule_id": "live-allocation"})
            except Exception:
                pass
        try:                                                 # MCX CASH MAINTENANCE: one item per time, from its card
            from core import mcx_cash_maintenance as _mc
            if _mc.auto_enabled():
                st = _mc.status(local.date(), local.replace(tzinfo=None))
                if st.get("trading_day"):
                    cmap = {"done": "done", "skipped": "done", "failed": "failed", "partial": "failed", "missed": "missed",
                            "running": "running", "due": "due", "upcoming": "upcoming"}
                    for sl in st.get("slots", []):
                        h, m = sl["time"].split(":")
                        items.append({"id": f"MCX CASH MAINTENANCE - {sl['time']}", "pipe": "sched",
                                      "pipe_name": "MCX CASH MAINTENANCE", "source": "scheduler", "window": sl["time"],
                                      "start_min": int(h) * 60 + int(m), "status": cmap.get(sl["state"], "upcoming"),
                                      "schedule_id": "mcx-cash"})
        except Exception:
            pass
        try:                                                 # every OTHER card that runs by its own timer
            items.extend(card_items(local.replace(tzinfo=None)))
        except Exception:
            pass
        items.sort(key=lambda x: (x["start_min"], x["id"]))
        c = {k: sum(1 for i in items if i["status"] == k) for k in ("done", "failed", "running", "missed", "due")}
        return {"items": items, "done": c["done"], "total": len(items), "failed": c["failed"],
                "running": c["running"], "missed": c["missed"], "due": c["due"]}
    except Exception as e:                                   # noqa: BLE001 - dashboard must still render
        out = dict(empty)
        out["error"] = str(e)
        return out
