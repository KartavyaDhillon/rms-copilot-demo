"""Scheduler engine — leader-elected tick loop.

Guarantees (and the one deliberate trade-off)
---------------------------------------------
* A run is started AT MOST ONCE per (schedule, scheduled time): the time window is claimed with a
  compare-and-swap on the schedule row, and every execution row has a UNIQUE key.  Two backend
  instances, a double-started service or a race between ticks cannot both start it.
* Trade-off: if the process dies after claiming a window but before starting the run, that run is
  LOST (recorded as nothing / shown as missed on the next evaluation of the history), never
  duplicated.  For financial / messaging processes a missed run is recoverable, a duplicate is not.
* A process failure is caught, stored and isolated — it never stops the tick loop or other schedules.
* Runs left RUNNING by a crashed instance are marked FAILED ('Interrupted'), never re-run.
* Missed runs after downtime follow the schedule's policy; interval schedules never replay history.
* Run Now never moves the schedule's cursor, so the normal next run stays exactly as it was.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import socket
import threading
import time
import traceback
from typing import Callable, Optional

from core.scheduler import catalog, holidays, rules, store

UTC = dt.timezone.utc
TICK_SECONDS = 10
LEASE_SECONDS = 45
LEASE_NAME = "scheduler"
LATE_TOLERANCE_S = 120          # up to this late = on time; later = "missed"
MAX_BACKLOG_DAYS = 2            # never look further back than this when recovering
STALE_EXEC_SECONDS = 180        # RUNNING rows nobody has touched for this long are orphans
HEALTH_STALE_S = 60
AGGREGATE_MISSED_OVER = 5       # more missed slots than this are stored as ONE summary row

_SECRET_RE = re.compile(r"(?i)\b(pass(?:word|wd)?|secret|token|api[_-]?key|authorization)\b(\s*[:=]\s*)(\S+)")


def _scrub(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text or "")


class BusyError(Exception):
    pass


class ExecContext:
    """Handed to every process run function."""

    def __init__(self, execution_id: str, schedule: dict, trigger: str):
        self.execution_id = execution_id
        self.schedule_id = schedule["id"]
        self.schedule_name = schedule["name"]
        self.trigger = trigger
        self._lines = []
        self.cancelled = False

    def log(self, msg: str):
        line = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {_scrub(str(msg))}"
        self._lines.append(line)
        print(f"[sched {self.schedule_name}] {line}", flush=True)

    def text(self) -> str:
        return "\n".join(self._lines)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(UTC)


class SchedulerEngine:
    def __init__(self, clock: Callable[[], dt.datetime] = _utcnow, synchronous: bool = False,
                 worker_id: Optional[str] = None, is_holiday=None):
        self.clock = clock
        self.synchronous = synchronous            # tests: run processes inline
        self.worker = worker_id or f"{socket.gethostname()}:{os.getpid()}:{os.urandom(2).hex()}"
        self.is_holiday = is_holiday if is_holiday is not None else holidays.is_holiday
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._started_ok = False
        self.last_error = ""

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        store.ensure_schema()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="rms-scheduler", daemon=True)
        self._thread.start()
        print(f"[scheduler] started (worker {self.worker})", flush=True)

    def stop(self):
        self._stop.set()
        store.release_lease(LEASE_NAME, self.worker)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick(self.clock())
            except Exception as e:                          # nothing may kill this loop
                self.last_error = f"{type(e).__name__}: {e}"
                print(f"[scheduler] tick failed: {self.last_error}", flush=True)
            self._stop.wait(TICK_SECONDS)

    # ── one tick (public + deterministic: tests drive it with a fake clock) ─
    def tick(self, now: dt.datetime) -> dict:
        now = now.astimezone(UTC)
        try:
            store.touch_running(self.worker, now)           # proof of life for runs on THIS instance
        except Exception as e:
            self.last_error = str(e)
        if not store.acquire_lease(LEASE_NAME, self.worker, now, LEASE_SECONDS):
            return {"leader": False}
        store.fail_stale_running(now, STALE_EXEC_SECONDS)
        self._run_due_retries(now)
        self._run_queued(now)
        evaluated = 0
        for s in store.list_schedules("enabled"):
            try:
                self._evaluate(s, now)
                evaluated += 1
            except Exception as e:                          # one broken schedule never stops the others
                self.last_error = f"{s.get('name')}: {type(e).__name__}: {e}"
                print(f"[scheduler] evaluating '{s.get('name')}' failed: {e}", flush=True)
        return {"leader": True, "evaluated": evaluated}

    # ── evaluation of one schedule ──────────────────────────────────────────
    def _timing(self, s: dict) -> dict:
        return {"rule": s["rule"], "tz": s["tz"], "start_date": s.get("start_date"),
                "end_date": s.get("end_date"), "skip_holidays": s.get("skip_holidays")}

    def _evaluate(self, s: dict, now: dt.datetime):
        timing = self._timing(s)
        last = store.parse_ts(s.get("last_eval_utc")) or now
        after = max(last, now - dt.timedelta(days=MAX_BACKLOG_DAYS))
        due = rules.between(timing, after, now, self.is_holiday)
        nxt = rules.next_after(timing, now, self.is_holiday)
        if not due:
            if nxt is None and not rules.next_after(timing, last, self.is_holiday):
                store.set_status(s["id"], "completed", "scheduler", now, None, "completed", reset_cursor=False)
            elif store.ts(nxt) != s.get("next_fire_utc"):
                store.touch_next_fire(s["id"], nxt)
            return
        # claim the window — only one instance/tick can pass this line for these occurrences
        if not store.advance_cursor(s["id"], s.get("last_eval_utc"), now, nxt):
            return
        on_time, missed = rules.classify(due, now, LATE_TOLERANCE_S)
        if missed and on_time and s["rule"].get("type") == "interval":
            # an on-time run is happening right now — replaying an older interval tick would double up
            run_missed, skipped = None, list(missed)
        else:
            run_missed, skipped = rules.decide_missed(s.get("missed_policy", "latest"),
                                                      s.get("missed_grace_min", 30), missed, now)
        self._record_missed(s, skipped, now)
        if run_missed:
            self._fire(s, run_missed, "MISSED_RUN_RECOVERY", now)
        for occ in on_time:
            self._fire(s, occ, "SCHEDULED", now)
        if nxt is None:
            store.set_status(s["id"], "completed", "scheduler", now, None, "completed", reset_cursor=False)

    def _record_missed(self, s: dict, skipped: list, now: dt.datetime):
        if not skipped:
            return
        if len(skipped) > AGGREGATE_MISSED_OVER:
            store.create_execution(s, skipped[-1], "SCHEDULED", "MISSED", now,
                                   error_type="Missed",
                                   error_msg=f"{len(skipped)} scheduled runs were missed while the scheduler was not running "
                                             f"(latest: {rules.fmt_local(skipped[-1], s['tz'], True)}).")
            return
        for o in skipped:
            store.create_execution(s, o, "SCHEDULED", "MISSED", now, error_type="Missed",
                                   error_msg="Missed (scheduler was not running) — not run, per the missed-run setting.")

    # ── starting a run ──────────────────────────────────────────────────────
    def _external_busy(self, pdef) -> Optional[str]:
        """Existing Process Center lock (a pipe's run slot) that must be idle before this process may start."""
        if not pdef or not pdef.lock_pipe:
            return None
        try:
            from core.pipeline_box import _PB
            if (_PB.get(pdef.lock_pipe) or {}).get("running"):
                return f"Process Center pipe '{pdef.lock_pipe}' is running"
        except Exception:
            pass
        return None

    def _busy_reason(self, s: dict, pdef) -> Optional[str]:
        if store.running_for_schedule(s["id"]):
            return "the previous run of this schedule is still running"
        if pdef is not None and not pdef.parallel_safe and store.running_for_process(s["process_key"]):
            return "another schedule is already running this process"
        return self._external_busy(pdef)

    def _fire(self, s: dict, scheduled_for: dt.datetime, trigger: str, now: dt.datetime,
              attempt: int = 1, triggered_by: str = "", manual: bool = False):
        pdef = catalog.get(s["process_key"])
        if pdef is None:
            store.create_execution(s, scheduled_for, trigger, "FAILED", now, attempt=attempt,
                                   error_type="ProcessNotRegistered",
                                   error_msg=f"Process '{s['process_key']}' is not registered any more.",
                                   triggered_by=triggered_by)
            return None
        reason = self._busy_reason(s, pdef)
        if reason:
            policy = s.get("overlap_policy", "skip")
            if manual:
                raise BusyError(f"Not started: {reason}.")
            if policy == "queue" and store.count_status(s["id"], "QUEUED") == 0:
                store.create_execution(s, scheduled_for, trigger, "QUEUED", now, attempt=attempt,
                                       error_msg=f"Queued: {reason}.")
            else:
                store.create_execution(s, scheduled_for, trigger, "SKIPPED", now, attempt=attempt,
                                       error_type="Overlap", error_msg=f"Skipped: {reason}.")
            return None
        eid = store.create_execution(s, scheduled_for, trigger, "RUNNING", now, attempt=attempt,
                                     worker=self.worker, triggered_by=triggered_by)
        if eid is None:
            return None                                # someone else already created this exact run
        self._dispatch(eid, s, pdef, trigger, attempt)
        return eid

    def _dispatch(self, eid: str, s: dict, pdef, trigger: str, attempt: int):
        if self.synchronous:
            self._execute(eid, s, pdef, trigger, attempt)
        else:
            threading.Thread(target=self._execute, args=(eid, s, pdef, trigger, attempt),
                             name=f"sched-run-{eid[:6]}", daemon=True).start()

    def _execute(self, eid: str, s: dict, pdef, trigger: str, attempt: int):
        ctx = ExecContext(eid, s, trigger)
        status, etype, emsg = "SUCCESS", "", ""
        try:
            result = pdef.run(catalog.effective_params(pdef, s.get("params") or {}), ctx)
            if result:
                ctx.log(f"Result: {result}")
        except catalog.Skipped as e:                        # deliberate no-op: recorded as SKIPPED, with the reason
            status, etype, emsg = "SKIPPED", "Skipped", _scrub(str(e))[:2000]
            ctx.log("Skipped — " + emsg)
        except Exception as e:                              # isolated: stored, never propagated
            status, etype = "FAILED", type(e).__name__
            emsg = _scrub(str(e))[:2000]
            ctx.log("FAILED — " + _scrub("".join(traceback.format_exception_only(type(e), e)).strip()))
            ctx.log(_scrub(traceback.format_exc())[-6000:])
        end = self.clock()
        try:
            store.finish_execution(eid, status, end, etype, emsg, ctx.text())
        except Exception as e:
            print(f"[scheduler] could not store result of {eid}: {e}", flush=True)
        if status == "FAILED":
            try:
                self._maybe_retry(eid, s, pdef, trigger, attempt, end)
            except Exception as e:
                print(f"[scheduler] retry scheduling failed: {e}", flush=True)

    def _maybe_retry(self, eid: str, s: dict, pdef, trigger: str, attempt: int, now: dt.datetime):
        r = s.get("retry") or {}
        if not r.get("enabled") or not pdef.retry_safe:
            return
        if attempt > int(r.get("max") or 0):
            return
        delay = float(r.get("delay_min") or 5) * 60
        if r.get("backoff"):
            delay *= 2 ** (attempt - 1)
        row = store.get_execution(eid)
        sched_for = store.parse_ts(row["scheduled_for_utc"])
        store.create_execution(s, sched_for, trigger, "RETRY_WAIT", now, attempt=attempt + 1,
                               retry_at=now + dt.timedelta(seconds=delay),
                               error_msg=f"Retry {attempt} of {r.get('max')} waiting.")

    def _run_due_retries(self, now: dt.datetime):
        for row in store.due_retries(now):
            s = store.get_schedule(row["schedule_id"])
            pdef = catalog.get(row["process_key"]) if s else None
            if not s or not pdef or s["status"] not in ("enabled", "paused"):
                store.claim_row(row["id"], "RETRY_WAIT", now, self.worker)
                store.finish_execution(row["id"], "CANCELLED", now, "Cancelled", "Schedule removed / disabled before the retry.")
                continue
            if self._busy_reason(s, pdef):
                continue                                    # try again next tick
            if store.claim_row(row["id"], "RETRY_WAIT", now, self.worker):
                self._dispatch(row["id"], s, pdef, row["trigger_type"], int(row["attempt"] or 1))

    def _run_queued(self, now: dt.datetime):
        for row in store.queued_rows():
            s = store.get_schedule(row["schedule_id"])
            pdef = catalog.get(row["process_key"]) if s else None
            if not s or not pdef:
                if store.claim_row(row["id"], "QUEUED", now, self.worker):
                    store.finish_execution(row["id"], "CANCELLED", now, "Cancelled", "Schedule removed.")
                continue
            if self._busy_reason(s, pdef):
                continue
            if store.claim_row(row["id"], "QUEUED", now, self.worker):
                self._dispatch(row["id"], s, pdef, row["trigger_type"], int(row["attempt"] or 1))

    # ── Run Now (never touches the schedule's cursor) ───────────────────────
    def run_now(self, schedule_id: str, user: str) -> str:
        s = store.get_schedule(schedule_id)
        if not s:
            raise KeyError("schedule not found")
        if catalog.get(s["process_key"]) is None:
            raise KeyError(f"process '{s['process_key']}' is not registered")
        now = self.clock()
        store.audit(user, "run_now", s, None, {"requested": True}, now)
        eid = self._fire(s, now, "MANUAL_RUN_NOW", now, triggered_by=user or "", manual=True)
        if eid is None:
            raise BusyError("Not started (duplicate request).")
        return eid

    # ── health ──────────────────────────────────────────────────────────────
    def health(self, now: Optional[dt.datetime] = None) -> dict:
        now = (now or self.clock()).astimezone(UTC)
        lease = store.read_lease(LEASE_NAME)
        hb = store.parse_ts(lease["heartbeat_utc"]) if lease else None
        age = (now - hb).total_seconds() if hb else None
        enabled = os.getenv("SCHEDULER_ENABLED", "false").strip().lower() == "true"
        return {"enabled_in_env": enabled, "holder": (lease or {}).get("holder"),
                "heartbeat_utc": store.ts(hb), "heartbeat_age_s": age,
                "stale": (age is None) or age > HEALTH_STALE_S,
                "last_error": self.last_error, "this_worker": self.worker}


# singleton used by the API
_ENGINE: Optional[SchedulerEngine] = None


def get_engine() -> SchedulerEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = SchedulerEngine()
    return _ENGINE


def start_if_enabled():
    """Called from the API startup hook. OFF unless SCHEDULER_ENABLED=true — the schema is still created."""
    try:
        store.ensure_schema()
    except Exception as e:
        print(f"[scheduler] schema setup failed: {e}", flush=True)
        return
    if os.getenv("SCHEDULER_ENABLED", "false").strip().lower() == "true":
        get_engine().start()
    else:
        print("[scheduler] installed but OFF (set SCHEDULER_ENABLED=true in .env to run schedules)", flush=True)


def shutdown():
    if _ENGINE is not None:
        _ENGINE.stop()
