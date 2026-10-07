"""Scheduler service layer — validation, create/edit/pause/…, and the JSON views the UI shows.
No FastAPI in here, so it is unit-testable and reusable (e.g. by an AI-agent tool later)."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from core.scheduler import catalog, holidays, rules, store

UTC = dt.timezone.utc


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


# ─────────────────────────────────────────────────────────────────────────────
# validation / normalisation
# ─────────────────────────────────────────────────────────────────────────────
def _clean_rule(rule: dict) -> dict:
    r = dict(rule or {})
    t = r.get("type")
    if t in ("times", "monthly"):
        r["times"] = sorted({str(x).strip() for x in (r.get("times") or [])})
    if t in ("times", "interval") and r.get("days") is not None:
        r["days"] = sorted({int(x) for x in r["days"]}) if r["days"] else []
        if r["days"] == list(range(7)):
            r["days"] = None                          # "every day" is stored as no restriction
        if r["days"] == []:
            r["days"] = [] if "days" in r else None
    if t == "once":
        r["at"] = sorted({str(x).strip() for x in (r.get("at") or [])})
    if t == "interval":
        try:
            r["every"] = int(r.get("every"))
        except (TypeError, ValueError):
            pass
    if t == "monthly" and r.get("day") != "last":
        try:
            r["day"] = int(r.get("day"))
        except (TypeError, ValueError):
            pass
    return r


def validate_payload(data: dict) -> (list, dict):
    errs: list = []
    data = data or {}
    name = str(data.get("name") or "").strip()
    if not name:
        errs.append("Give the schedule a name.")
    if len(name) > 120:
        errs.append("The name is too long (max 120 characters).")
    pkey = str(data.get("process_key") or "").strip()
    pdef = catalog.get(pkey) if pkey else None
    if not pkey:
        errs.append("Choose a process.")
    elif pdef is None:
        errs.append("That process is not registered.")
    params = {}
    if pdef is not None:
        perr, params = catalog.validate_params(pdef, data.get("params") or {})
        errs += perr

    rule = _clean_rule(data.get("rule") or {})
    timing = {"rule": rule, "tz": data.get("tz") or rules.DEFAULT_TZ,
              "start_date": data.get("start_date") or None, "end_date": data.get("end_date") or None,
              "skip_holidays": bool(data.get("skip_holidays", True))}
    errs += rules.validate_schedule_timing(timing)

    missed = data.get("missed_policy") or "latest"
    if missed not in rules.MISSED_POLICIES:
        errs.append("Choose what to do with a missed run.")
    try:
        grace = int(data.get("missed_grace_min") or 30)
        if not 1 <= grace <= 1440:
            raise ValueError
    except (TypeError, ValueError):
        errs.append("'Run only if missed by less than' must be between 1 and 1440 minutes.")
        grace = 30

    overlap = data.get("overlap_policy") or "skip"
    if overlap not in rules.OVERLAP_POLICIES:
        errs.append("Choose what to do if the process is already running.")
    elif overlap == "parallel" and pdef is not None and not pdef.parallel_safe:
        errs.append("This process is not marked safe to run in parallel — choose Skip or Queue.")

    retry_in = data.get("retry") or {}
    retry = {"enabled": bool(retry_in.get("enabled")), "max": 3, "delay_min": 5, "backoff": bool(retry_in.get("backoff"))}
    if retry["enabled"]:
        try:
            retry["max"] = int(retry_in.get("max") or 3)
            retry["delay_min"] = float(retry_in.get("delay_min") or 5)
            if not (1 <= retry["max"] <= 10) or not (1 <= retry["delay_min"] <= 1440):
                raise ValueError
        except (TypeError, ValueError):
            errs.append("Retry: maximum must be 1–10 and the wait 1–1440 minutes.")
        if pdef is not None and not pdef.retry_safe:
            errs.append("This process is not marked safe to retry (running it twice could repeat its effects).")

    cleaned = {"name": name, "description": str(data.get("description") or "")[:500], "process_key": pkey,
               "params": params, "rule": rule, "tz": timing["tz"], "start_date": timing["start_date"],
               "end_date": timing["end_date"], "skip_holidays": timing["skip_holidays"],
               "missed_policy": missed, "missed_grace_min": grace, "overlap_policy": overlap,
               "retry": retry, "notify": data.get("notify") or {}}
    return errs, cleaned


def _timing(c: dict) -> dict:
    return {"rule": c["rule"], "tz": c["tz"], "start_date": c.get("start_date"), "end_date": c.get("end_date"),
            "skip_holidays": c.get("skip_holidays")}


def preview(data: dict, now: Optional[dt.datetime] = None, n: int = 5) -> dict:
    """Next `n` run times — computed by the SAME function the engine uses."""
    now = now or _now()
    errs, c = validate_payload(data)
    timing_errs = rules.validate_schedule_timing(_timing(c)) if c.get("rule") else errs
    if timing_errs:
        return {"errors": errs or timing_errs, "upcoming": []}
    ups = rules.upcoming(_timing(c), now, n, holidays.is_holiday)
    out = {"errors": [e for e in errs if e], "tz": c["tz"],
           "upcoming": [{"utc": store.ts(u), "text": rules.fmt_local(u, c["tz"], True)} for u in ups]}
    if not ups and not out["errors"]:
        out["errors"] = ["No future run exists for these settings (dates in the past, or the end date has passed)."]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# actions
# ─────────────────────────────────────────────────────────────────────────────
class ValidationFailed(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def create(data: dict, user: str, now: Optional[dt.datetime] = None) -> dict:
    now = now or _now()
    errs, c = validate_payload(data)
    if errs:
        raise ValidationFailed(errs)
    nxt = rules.next_after(_timing(c), now, holidays.is_holiday)
    if nxt is None:
        raise ValidationFailed(["No future run exists for these settings."])
    return store.create_schedule(c, user, now, nxt)


def update(sid: str, data: dict, user: str, now: Optional[dt.datetime] = None) -> Optional[dict]:
    now = now or _now()
    errs, c = validate_payload(data)
    if errs:
        raise ValidationFailed(errs)
    cur = store.get_schedule(sid)
    if not cur:
        return None
    nxt = rules.next_after(_timing(c), now, holidays.is_holiday)
    if nxt is None:
        raise ValidationFailed(["No future run exists for these settings."])
    out = store.update_schedule(sid, c, user, now, nxt)
    if cur["status"] == "completed":                  # editing a finished schedule re-opens it
        out = store.set_status(sid, "enabled", user, now, nxt, "reopened")
    return out


def _next_for(s: dict, now: dt.datetime):
    return rules.next_after(_timing(s), now, holidays.is_holiday)


def pause(sid, user, now=None):
    now = now or _now()
    return store.set_status(sid, "paused", user, now, None, "paused", reset_cursor=False)


def resume(sid, user, now=None):
    now = now or _now()
    s = store.get_schedule(sid)
    if not s:
        return None
    return store.set_status(sid, "enabled", user, now, _next_for(s, now), "resumed", reset_cursor=True)


def disable(sid, user, now=None):
    now = now or _now()
    return store.set_status(sid, "disabled", user, now, None, "disabled", reset_cursor=False)


def enable(sid, user, now=None):
    now = now or _now()
    s = store.get_schedule(sid)
    if not s:
        return None
    return store.set_status(sid, "enabled", user, now, _next_for(s, now), "enabled", reset_cursor=True)


def delete(sid, user, now=None):
    return store.delete_schedule(sid, user, now or _now())


# ─────────────────────────────────────────────────────────────────────────────
# views
# ─────────────────────────────────────────────────────────────────────────────
def view(s: dict, now: Optional[dt.datetime] = None) -> dict:
    now = now or _now()
    pdef = catalog.get(s["process_key"])
    desc = rules.describe(s)
    nxt = _next_for(s, now) if s["status"] == "enabled" else None
    running = bool(store.running_for_schedule(s["id"]))
    last = store.last_execution(s["id"])
    if s["status"] in ("paused", "disabled", "completed"):
        disp = s["status"]
    elif running:
        disp = "running"
    elif last and last["status"] == "FAILED":
        disp = "failed"
    elif last and last["status"] == "MISSED":
        disp = "missed"
    else:
        disp = "enabled"
    ls = store.parse_ts(last["started_utc"]) if last and last.get("started_utc") else None
    out = {k: s.get(k) for k in ("id", "name", "description", "process_key", "params", "rule", "tz", "start_date",
                                 "end_date", "skip_holidays", "missed_policy", "missed_grace_min", "overlap_policy",
                                 "retry", "notify", "status", "created_by", "updated_by", "version")}
    out.update({
        "process_name": pdef.name if pdef else f"{s['process_key']} (not registered)",
        "process_missing": pdef is None,
        "trigger_text": desc["trigger"], "days_text": desc["days"],
        "display_status": disp,
        "next_run_utc": store.ts(nxt), "next_run_text": rules.fmt_local(nxt, s["tz"]) if nxt else "—",
        "last_run_utc": store.ts(ls), "last_run_text": rules.fmt_local(ls, s["tz"]) if ls else "—",
        "last_result": (last or {}).get("status", "—"),
        "last_duration_sec": (last or {}).get("duration_sec"),
        "last_error": (last or {}).get("error_msg"),
    })
    return out


def summary(now: Optional[dt.datetime] = None, health: Optional[dict] = None) -> dict:
    now = now or _now()
    tz = rules.get_tz(rules.DEFAULT_TZ)
    local = now.astimezone(tz)
    day0 = dt.datetime.combine(local.date(), dt.time(0, 0), tzinfo=tz).astimezone(UTC)
    day1 = day0 + dt.timedelta(days=1)
    all_s = store.list_schedules()
    counts = store.counts_between(day0, day1)
    nxt_best, nxt_s = None, None
    for s in all_s:
        if s["status"] != "enabled":
            continue
        n = _next_for(s, now)
        if n and (nxt_best is None or n < nxt_best):
            nxt_best, nxt_s = n, s
    return {
        "active": sum(1 for s in all_s if s["status"] == "enabled"),
        "paused": sum(1 for s in all_s if s["status"] in ("paused", "disabled")),
        "running": store.count_running(),
        "failed_today": counts.get("FAILED", 0),
        "success_today": counts.get("SUCCESS", 0),
        "next_run": ({"schedule": nxt_s["name"], "process": (catalog.get(nxt_s["process_key"]).name
                                                            if catalog.get(nxt_s["process_key"]) else nxt_s["process_key"]),
                      "utc": store.ts(nxt_best), "text": rules.fmt_local(nxt_best, nxt_s["tz"])} if nxt_best else None),
        "total": len(all_s),
        "health": health or {},
    }


def execution_view(r: dict) -> dict:
    tz = rules.DEFAULT_TZ

    def loc(v, year=False):
        d = store.parse_ts(v)
        return rules.fmt_local(d, tz, year) if d else "—"
    out = dict(r)
    out["scheduled_text"] = loc(r.get("scheduled_for_utc"), True)
    out["started_text"] = loc(r.get("started_utc"), True)
    out["ended_text"] = loc(r.get("ended_utc"), True)
    return out
