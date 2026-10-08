"""Pure schedule maths — no database, no clock reads (callers pass `now`).

A schedule dict (as stored/validated) looks like:

    {
      "rule": {...},                 # see below
      "tz": "Asia/Kolkata",
      "start_date": "2026-10-01" | None,
      "end_date":   "2026-12-31" | None,
      "skip_holidays": True,         # skip market holidays (weekday-style rules)
    }

Rule types
----------
times     {"type":"times","times":["07:30","12:00"],"days":[0,1,2,3,4]}     (Mon=0 … Sun=6; days optional = every day)
interval  {"type":"interval","every":15,"unit":"minutes"|"hours","start":"09:00","end":"17:00","days":[0..4]}
monthly   {"type":"monthly","day":1..31|"last","times":["08:30"]}
once      {"type":"once","at":["2026-10-09T11:42","2026-10-12T15:17"]}      (local wall-clock, schedule tz)

Everything returned is timezone-aware UTC.  Missed-run decisions are pure functions too.
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Callable, Iterable, Optional

UTC = dt.timezone.utc
DEFAULT_TZ = "Asia/Kolkata"
_IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")      # India has no DST → safe fallback
RULE_TYPES = ("times", "interval", "monthly", "once", "multi")
MISSED_POLICIES = ("latest", "grace", "skip")
OVERLAP_POLICIES = ("skip", "queue", "parallel")
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MAX_DAYS_SCAN = 1100


# ─────────────────────────────────────────────────────────────────────────────
# time zone / parsing helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_tz(name: Optional[str]):
    name = (name or DEFAULT_TZ).strip()
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        if name in ("Asia/Kolkata", "Asia/Calcutta", "IST"):
            return _IST
        raise ValueError(f"Unknown timezone '{name}' (on Windows run: pip install tzdata)")


def parse_hhmm(s) -> dt.time:
    try:
        h, m = str(s).strip().split(":")[:2]
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        return dt.time(h, m)
    except Exception:
        raise ValueError(f"'{s}' is not a valid time (use HH:MM, 24-hour)")


def _date(s) -> Optional[dt.date]:
    if not s:
        return None
    if isinstance(s, dt.date):
        return s
    return dt.date.fromisoformat(str(s)[:10])


def _aware_utc(d: dt.datetime) -> dt.datetime:
    if d.tzinfo is None:
        raise ValueError("naive datetime not allowed")
    return d.astimezone(UTC)


def fmt_local(d: Optional[dt.datetime], tz_name: Optional[str] = None, with_year: bool = False) -> str:
    """'08 Oct 07:45' (or '08 Oct 2026 07:45') in the schedule's own time zone."""
    if d is None:
        return "—"
    local = d.astimezone(get_tz(tz_name))
    return local.strftime("%d %b %Y %H:%M" if with_year else "%d %b %H:%M")


# ─────────────────────────────────────────────────────────────────────────────
# validation
# ─────────────────────────────────────────────────────────────────────────────
def _days_ok(days, errs):
    if days is None:
        return
    if (not isinstance(days, (list, tuple)) or not days
            or any((not isinstance(x, int)) or x < 0 or x > 6 for x in days)):
        errs.append("Pick at least one weekday.")


def validate_rule(rule) -> list:
    errs: list = []
    if not isinstance(rule, dict) or rule.get("type") not in RULE_TYPES:
        return ["Choose how the schedule should repeat."]
    t = rule["type"]
    try:
        if t == "multi":
            parts = rule.get("parts")
            if not isinstance(parts, list) or not 1 <= len(parts) <= 6:
                errs.append("Add between 1 and 6 timings.")
            else:
                for i, p in enumerate(parts, 1):
                    if not isinstance(p, dict) or p.get("type") not in ("times", "interval"):
                        errs.append(f"Timing {i}: choose 'repeat every' or 'at specific times'.")
                        continue
                    errs += [f"Timing {i}: {e}" for e in validate_rule(p)]
        elif t == "times":
            times = rule.get("times") or []
            if not times:
                errs.append("Add at least one time.")
            for x in times:
                parse_hhmm(x)
            if len(set(times)) != len(times):
                errs.append("The same time is listed twice.")
            _days_ok(rule.get("days"), errs)
        elif t == "interval":
            every = rule.get("every")
            if not isinstance(every, int) or every < 1:
                errs.append("'Repeat every' must be a whole number, 1 or more.")
            if rule.get("unit") not in ("minutes", "hours"):
                errs.append("Repeat unit must be minutes or hours.")
            s, e = parse_hhmm(rule.get("start")), parse_hhmm(rule.get("end"))
            if e <= s:
                errs.append("The end time must be after the start time.")
            for x in (rule.get("times") or []):                  # optional extra specific times on top of the repeat
                parse_hhmm(x)
            _days_ok(rule.get("days"), errs)
        elif t == "monthly":
            d = rule.get("day")
            if not (d == "last" or (isinstance(d, int) and 1 <= d <= 31)):
                errs.append("Pick a day of the month (1–31 or Last day).")
            times = rule.get("times") or []
            if not times:
                errs.append("Add at least one time.")
            for x in times:
                parse_hhmm(x)
        elif t == "once":
            at = rule.get("at") or []
            if not at:
                errs.append("Add at least one date and time.")
            for x in at:
                dt.datetime.fromisoformat(str(x))
    except ValueError as e:
        errs.append(str(e))
    return errs


def validate_schedule_timing(s: dict) -> list:
    errs = list(validate_rule(s.get("rule")))
    try:
        get_tz(s.get("tz"))
    except ValueError as e:
        errs.append(str(e))
    try:
        sd, ed = _date(s.get("start_date")), _date(s.get("end_date"))
        if sd and ed and ed < sd:
            errs.append("The end date is before the start date.")
    except ValueError:
        errs.append("Start / end date is not a valid date.")
    if not errs and (s.get("rule") or {}).get("type") == "once":
        # a one-time schedule whose every date is already in the past can never run
        pass
    return errs


# ─────────────────────────────────────────────────────────────────────────────
# occurrence generation
# ─────────────────────────────────────────────────────────────────────────────
def _times_for_day(rule: dict, d: dt.date) -> list:
    t = rule["type"]
    if t == "multi":                      # union of the parts that apply on this weekday; a shared time runs once
        out = set()
        for p in _parts_for_day(rule, d):
            out.update(_times_for_day(p, d))
        return sorted(out)
    if t == "times":
        return sorted(parse_hhmm(x) for x in rule["times"])
    if t == "monthly":
        return sorted(parse_hhmm(x) for x in rule["times"])
    if t == "interval":
        step = rule["every"] * (60 if rule["unit"] == "hours" else 1)
        s, e = parse_hhmm(rule["start"]), parse_hhmm(rule["end"])
        cur, end = s.hour * 60 + s.minute, e.hour * 60 + e.minute
        out = set()
        while cur <= end:
            out.add(dt.time(cur // 60, cur % 60))
            cur += step
        # A single schedule may ALSO run at specific times (e.g. every hour 09:00–23:00 + 12:00 AM): the two lists
        # are merged, and a time that is in both runs only once.
        out.update(parse_hhmm(x) for x in (rule.get("times") or []))
        return sorted(out)
    return []


def _parts_for_day(rule: dict, d: dt.date) -> list:
    return [p for p in rule.get("parts") or [] if _day_matches(p, d)]


def _day_matches(rule: dict, d: dt.date) -> bool:
    t = rule["type"]
    if t == "multi":
        return bool(_parts_for_day(rule, d))
    if t in ("times", "interval"):
        days = rule.get("days")
        return True if not days else d.weekday() in days
    if t == "monthly":
        last = calendar.monthrange(d.year, d.month)[1]
        want = rule["day"]
        return d.day == last if want == "last" else d.day == want
    return False


def occurrences(sched: dict, after_utc: dt.datetime,
                is_holiday: Optional[Callable[[dt.date], bool]] = None) -> Iterable[dt.datetime]:
    """Yield every run time strictly AFTER `after_utc`, in order (UTC, aware)."""
    after_utc = _aware_utc(after_utc)
    rule = sched["rule"]
    tz = get_tz(sched.get("tz"))
    start_date, end_date = _date(sched.get("start_date")), _date(sched.get("end_date"))

    if rule["type"] == "once":
        pts = []
        for x in rule["at"]:
            local = dt.datetime.fromisoformat(str(x)).replace(tzinfo=tz)
            u = local.astimezone(UTC)
            ld = local.date()
            if start_date and ld < start_date:
                continue
            if end_date and ld > end_date:
                continue
            pts.append(u)
        for u in sorted(set(pts)):
            if u > after_utc:
                yield u
        return

    skip = bool(sched.get("skip_holidays")) and is_holiday is not None
    d = after_utc.astimezone(tz).date()
    if start_date and start_date > d:
        d = start_date
    for _ in range(MAX_DAYS_SCAN):
        if end_date and d > end_date:
            return
        if _day_matches(rule, d) and not (skip and is_holiday(d)):
            for tm in _times_for_day(rule, d):
                u = dt.datetime.combine(d, tm, tzinfo=tz).astimezone(UTC)
                if u > after_utc:
                    yield u
        d += dt.timedelta(days=1)


def next_after(sched, after_utc, is_holiday=None) -> Optional[dt.datetime]:
    for u in occurrences(sched, after_utc, is_holiday):
        return u
    return None


def upcoming(sched, after_utc, n=5, is_holiday=None) -> list:
    out = []
    for u in occurrences(sched, after_utc, is_holiday):
        out.append(u)
        if len(out) >= n:
            break
    return out


def between(sched, after_utc, upto_utc, is_holiday=None, cap=5000) -> list:
    """Occurrences in (after_utc, upto_utc]."""
    out = []
    for u in occurrences(sched, after_utc, is_holiday):
        if u > upto_utc or len(out) >= cap:
            break
        out.append(u)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# missed-run policy (pure)
# ─────────────────────────────────────────────────────────────────────────────
def classify(due: list, now: dt.datetime, tolerance_s: int = 120):
    """Split due occurrences into (on_time, missed). On-time = at most `tolerance_s` late."""
    on_time = [o for o in due if (now - o).total_seconds() <= tolerance_s]
    missed = [o for o in due if (now - o).total_seconds() > tolerance_s]
    return on_time, missed


def decide_missed(policy: str, grace_min: int, missed: list, now: dt.datetime):
    """-> (run_this_one or None, [the ones that are only recorded as MISSED]).

    latest : run only the newest missed occurrence (default — safe after any outage)
    grace  : run the newest missed occurrence ONLY if it is at most `grace_min` minutes old
    skip   : run nothing, just record what was missed
    """
    if not missed:
        return None, []
    latest = missed[-1]
    if policy == "skip":
        return None, list(missed)
    if policy == "grace":
        if (now - latest) <= dt.timedelta(minutes=max(0, int(grace_min or 0))):
            return latest, list(missed[:-1])
        return None, list(missed)
    return latest, list(missed[:-1])


# ─────────────────────────────────────────────────────────────────────────────
# human wording
# ─────────────────────────────────────────────────────────────────────────────
def _days_text(days) -> str:
    if not days or sorted(days) == list(range(7)):
        return "Every day"
    s = sorted(days)
    if s == [0, 1, 2, 3, 4]:
        return "Mon–Fri"
    if s == [5, 6]:
        return "Sat–Sun"
    if s == list(range(s[0], s[-1] + 1)) and len(s) > 2:
        return f"{WEEKDAY_NAMES[s[0]]}–{WEEKDAY_NAMES[s[-1]]}"
    return ", ".join(WEEKDAY_NAMES[i] for i in s)


def _t12(x) -> str:
    t = parse_hhmm(x)
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def describe(sched: dict) -> dict:
    """{'trigger': 'At 07:45 AM', 'days': 'Mon–Fri'} for the schedules table."""
    r = sched.get("rule") or {}
    t = r.get("type")
    try:
        if t == "multi":
            ds = [describe({"rule": p}) for p in r["parts"]]
            if len({x["days"] for x in ds}) == 1:
                return {"trigger": " + ".join(x["trigger"] for x in ds), "days": ds[0]["days"]}
            return {"trigger": " + ".join(x["trigger"] for x in ds),
                    "days": " · ".join(f"{x['days']}" for x in ds)}
        if t == "times":
            return {"trigger": "At " + ", ".join(_t12(x) for x in sorted(r["times"])), "days": _days_text(r.get("days"))}
        if t == "interval":
            unit = "minute" if r["unit"] == "minutes" else "hour"
            every = f"Every {r['every']} {unit}{'s' if r['every'] != 1 else ''}"
            extra = "".join(f" + {_t12(x)}" for x in sorted(r.get("times") or []))
            return {"trigger": f"{every}, {_t12(r['start'])}–{_t12(r['end'])}{extra}", "days": _days_text(r.get("days"))}
        if t == "monthly":
            d = "last day" if r["day"] == "last" else f"day {r['day']}"
            return {"trigger": "At " + ", ".join(_t12(x) for x in sorted(r["times"])), "days": f"Monthly · {d}"}
        if t == "once":
            n = len(r["at"])
            return {"trigger": "One-time" + (f" ({n} dates)" if n > 1 else ""), "days": "Specific dates"}
    except Exception:
        pass
    return {"trigger": "—", "days": "—"}
