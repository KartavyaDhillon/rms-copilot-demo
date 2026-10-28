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
        for s in store.list_schedules("enabled"):
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
        items.sort(key=lambda x: (x["start_min"], x["id"]))
        c = {k: sum(1 for i in items if i["status"] == k) for k in ("done", "failed", "running", "missed", "due")}
        return {"items": items, "done": c["done"], "total": len(items), "failed": c["failed"],
                "running": c["running"], "missed": c["missed"], "due": c["due"]}
    except Exception as e:                                   # noqa: BLE001 - dashboard must still render
        out = dict(empty)
        out["error"] = str(e)
        return out
