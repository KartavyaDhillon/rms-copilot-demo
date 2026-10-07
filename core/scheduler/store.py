"""Scheduler persistence — works on sqlite (core/db.py) and Postgres (core/db_postgres.py)
exactly like core/auth.py does.  One SQL dialect-neutral schema: TEXT ids (uuid hex), ISO-8601 UTC
timestamps stored as TEXT (they sort correctly), JSON stored as TEXT.

Duplicate-run protection lives HERE, in the database, not in memory:
  * advance_cursor()  compare-and-swap on the schedule row: only ONE instance wins a time window
  * create_execution() UNIQUE(schedule_id, scheduled_for, trigger, attempt) + ON CONFLICT DO NOTHING
  * claim_*()          UPDATE … WHERE status=… : only one instance can take a queued / retry row
  * acquire_lease()    one leader instance runs the tick loop
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from typing import Any, Dict, List, Optional

UTC = dt.timezone.utc
_CONN_FACTORY = None          # tests plug a sqlite connection factory in here

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS sched_schedules (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        process_key TEXT NOT NULL,
        params_json TEXT,
        rule_json TEXT NOT NULL,
        tz TEXT NOT NULL,
        start_date TEXT,
        end_date TEXT,
        skip_holidays INTEGER DEFAULT 1,
        missed_policy TEXT DEFAULT 'latest',
        missed_grace_min INTEGER DEFAULT 30,
        overlap_policy TEXT DEFAULT 'skip',
        retry_json TEXT,
        notify_json TEXT,
        status TEXT NOT NULL,
        last_eval_utc TEXT,
        next_fire_utc TEXT,
        created_by TEXT,
        created_utc TEXT,
        updated_by TEXT,
        updated_utc TEXT,
        version INTEGER DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS sched_executions (
        id TEXT PRIMARY KEY,
        schedule_id TEXT NOT NULL,
        schedule_name TEXT,
        process_key TEXT,
        scheduled_for_utc TEXT,
        started_utc TEXT,
        ended_utc TEXT,
        duration_sec REAL,
        trigger_type TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt INTEGER DEFAULT 1,
        retry_at_utc TEXT,
        error_type TEXT,
        error_msg TEXT,
        log_text TEXT,
        worker TEXT,
        heartbeat_utc TEXT,
        created_utc TEXT,
        triggered_by TEXT,
        UNIQUE (schedule_id, scheduled_for_utc, trigger_type, attempt)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sched_exec_status ON sched_executions(status)",
    "CREATE INDEX IF NOT EXISTS idx_sched_exec_sched ON sched_executions(schedule_id, started_utc)",
    """CREATE TABLE IF NOT EXISTS sched_audit (
        id TEXT PRIMARY KEY,
        ts_utc TEXT NOT NULL,
        user_name TEXT,
        action TEXT NOT NULL,
        schedule_id TEXT,
        schedule_name TEXT,
        before_json TEXT,
        after_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS sched_lease (
        name TEXT PRIMARY KEY,
        holder TEXT,
        heartbeat_utc TEXT,
        lease_until_utc TEXT
    )""",
]

SECRET_KEYS = ("password", "passwd", "secret", "token", "api_key", "apikey", "authorization")


# ─────────────────────────────────────────────────────────────────────────────
# plumbing
# ─────────────────────────────────────────────────────────────────────────────
def new_id() -> str:
    return uuid.uuid4().hex


def ts(d: Optional[dt.datetime]) -> Optional[str]:
    if d is None:
        return None
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(UTC)


def _use_pg() -> bool:
    return _CONN_FACTORY is None and os.getenv("USE_POSTGRES", "false").strip().lower() == "true"


def _connect():
    if _CONN_FACTORY is not None:
        return _CONN_FACTORY()
    if _use_pg():
        from core.db_postgres import _conn as c
    else:
        from core.db import _conn as c
    return c()


def _q(sql: str) -> str:
    return sql.replace("?", "%s") if _use_pg() else sql


def _write(sql: str, params=()) -> int:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(_q(sql), tuple(params))
        n = cur.rowcount
        conn.commit()
        return n
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _rows(sql: str, params=()) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(_q(sql), tuple(params))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _one(sql: str, params=()) -> Optional[Dict[str, Any]]:
    r = _rows(sql, params)
    return r[0] if r else None


def ensure_schema():
    conn = _connect()
    try:
        cur = conn.cursor()
        for stmt in SCHEMA:
            cur.execute(stmt)
        conn.commit()
        # columns added after first release (idempotent)
        for tbl, col, decl in (("sched_executions", "triggered_by", "TEXT"),):
            try:
                cur.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
    finally:
        conn.close()


def scrub(obj):
    """Remove anything that looks like a secret before it is written to an audit/log record."""
    if isinstance(obj, dict):
        return {k: ("***" if any(s in str(k).lower() for s in SECRET_KEYS) else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# schedules
# ─────────────────────────────────────────────────────────────────────────────
def _decode(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    out = dict(r)
    for src, dst, dflt in (("params_json", "params", {}), ("rule_json", "rule", {}),
                           ("retry_json", "retry", {}), ("notify_json", "notify", {})):
        try:
            out[dst] = json.loads(out.get(src) or "null") or dflt
        except Exception:
            out[dst] = dflt
    out["skip_holidays"] = bool(out.get("skip_holidays"))
    return out


def get_schedule(sid: str) -> Optional[dict]:
    return _decode(_one("SELECT * FROM sched_schedules WHERE id = ?", (sid,)))


def list_schedules(status: Optional[str] = None) -> List[dict]:
    if status:
        rows = _rows("SELECT * FROM sched_schedules WHERE status = ? ORDER BY name", (status,))
    else:
        rows = _rows("SELECT * FROM sched_schedules ORDER BY name")
    return [_decode(r) for r in rows]


def _public_snapshot(s: Optional[dict]) -> Optional[dict]:
    if not s:
        return None
    keep = ("id", "name", "description", "process_key", "params", "rule", "tz", "start_date", "end_date",
            "skip_holidays", "missed_policy", "missed_grace_min", "overlap_policy", "retry", "notify", "status")
    return scrub({k: s.get(k) for k in keep})


def audit(user: str, action: str, schedule: Optional[dict] = None, before=None, after=None,
          now: Optional[dt.datetime] = None):
    try:
        _write("INSERT INTO sched_audit (id, ts_utc, user_name, action, schedule_id, schedule_name, before_json, after_json) "
               "VALUES (?,?,?,?,?,?,?,?)",
               (new_id(), ts(now or dt.datetime.now(UTC)), user or "system", action,
                (schedule or {}).get("id"), (schedule or {}).get("name"),
                json.dumps(before, default=str) if before is not None else None,
                json.dumps(after, default=str) if after is not None else None))
    except Exception as e:                                  # an audit write must never break the action itself
        print(f"[scheduler] audit write failed: {e}", flush=True)


def create_schedule(data: dict, user: str, now: dt.datetime, next_fire: Optional[dt.datetime]) -> dict:
    sid = new_id()
    _write(
        "INSERT INTO sched_schedules (id,name,description,process_key,params_json,rule_json,tz,start_date,end_date,"
        "skip_holidays,missed_policy,missed_grace_min,overlap_policy,retry_json,notify_json,status,last_eval_utc,"
        "next_fire_utc,created_by,created_utc,updated_by,updated_utc,version) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (sid, data["name"], data.get("description") or "", data["process_key"],
         json.dumps(data.get("params") or {}), json.dumps(data["rule"]), data["tz"],
         data.get("start_date"), data.get("end_date"), 1 if data.get("skip_holidays") else 0,
         data.get("missed_policy", "latest"), int(data.get("missed_grace_min") or 30),
         data.get("overlap_policy", "skip"), json.dumps(data.get("retry") or {}),
         json.dumps(data.get("notify") or {}), data.get("status", "enabled"),
         ts(now), ts(next_fire), user, ts(now), user, ts(now)))
    s = get_schedule(sid)
    audit(user, "created", s, None, _public_snapshot(s), now)
    return s


def update_schedule(sid: str, data: dict, user: str, now: dt.datetime, next_fire: Optional[dt.datetime]) -> Optional[dict]:
    before = get_schedule(sid)
    if not before:
        return None
    _write(
        "UPDATE sched_schedules SET name=?, description=?, process_key=?, params_json=?, rule_json=?, tz=?, "
        "start_date=?, end_date=?, skip_holidays=?, missed_policy=?, missed_grace_min=?, overlap_policy=?, "
        "retry_json=?, notify_json=?, last_eval_utc=?, next_fire_utc=?, updated_by=?, updated_utc=?, version=version+1 "
        "WHERE id=?",
        (data["name"], data.get("description") or "", data["process_key"], json.dumps(data.get("params") or {}),
         json.dumps(data["rule"]), data["tz"], data.get("start_date"), data.get("end_date"),
         1 if data.get("skip_holidays") else 0, data.get("missed_policy", "latest"),
         int(data.get("missed_grace_min") or 30), data.get("overlap_policy", "skip"),
         json.dumps(data.get("retry") or {}), json.dumps(data.get("notify") or {}),
         ts(now), ts(next_fire), user, ts(now), sid))
    after = get_schedule(sid)
    audit(user, "edited", after, _public_snapshot(before), _public_snapshot(after), now)
    return after


def set_status(sid: str, status: str, user: str, now: dt.datetime, next_fire: Optional[dt.datetime],
               action: str, reset_cursor: bool = True) -> Optional[dict]:
    before = get_schedule(sid)
    if not before:
        return None
    if reset_cursor:       # resumed / enabled: the time it was off is NOT "missed"
        _write("UPDATE sched_schedules SET status=?, last_eval_utc=?, next_fire_utc=?, updated_by=?, updated_utc=?, "
               "version=version+1 WHERE id=?", (status, ts(now), ts(next_fire), user, ts(now), sid))
    else:
        _write("UPDATE sched_schedules SET status=?, next_fire_utc=?, updated_by=?, updated_utc=?, version=version+1 "
               "WHERE id=?", (status, ts(next_fire), user, ts(now), sid))
    after = get_schedule(sid)
    audit(user, action, after, {"status": before["status"]}, {"status": status}, now)
    return after


def delete_schedule(sid: str, user: str, now: dt.datetime) -> bool:
    before = get_schedule(sid)
    if not before:
        return False
    _write("DELETE FROM sched_schedules WHERE id = ?", (sid,))
    audit(user, "deleted", before, _public_snapshot(before), None, now)
    return True


def advance_cursor(sid: str, expected_last_eval: Optional[str], new_eval: dt.datetime,
                   next_fire: Optional[dt.datetime]) -> bool:
    """Compare-and-swap. True only for the ONE caller that moved the cursor from `expected` — that caller
    owns every occurrence in the window it just closed."""
    if expected_last_eval is None:
        n = _write("UPDATE sched_schedules SET last_eval_utc=?, next_fire_utc=? WHERE id=? AND last_eval_utc IS NULL",
                   (ts(new_eval), ts(next_fire), sid))
    else:
        n = _write("UPDATE sched_schedules SET last_eval_utc=?, next_fire_utc=? WHERE id=? AND last_eval_utc=?",
                   (ts(new_eval), ts(next_fire), sid, expected_last_eval))
    return n == 1


def touch_next_fire(sid: str, next_fire: Optional[dt.datetime]):
    _write("UPDATE sched_schedules SET next_fire_utc=? WHERE id=?", (ts(next_fire), sid))


# ─────────────────────────────────────────────────────────────────────────────
# executions
# ─────────────────────────────────────────────────────────────────────────────
def create_execution(schedule: dict, scheduled_for: dt.datetime, trigger: str, status: str, now: dt.datetime,
                     attempt: int = 1, worker: str = "", error_type: str = "", error_msg: str = "",
                     retry_at: Optional[dt.datetime] = None, triggered_by: str = "") -> Optional[str]:
    """Insert one execution row. Returns its id, or None if that exact (schedule, time, trigger, attempt)
    already exists — i.e. someone else got there first."""
    eid = new_id()
    started = now if status == "RUNNING" else None
    ended = now if status in ("SKIPPED", "MISSED", "CANCELLED") else None
    n = _write(
        "INSERT INTO sched_executions (id,schedule_id,schedule_name,process_key,scheduled_for_utc,started_utc,ended_utc,"
        "duration_sec,trigger_type,status,attempt,retry_at_utc,error_type,error_msg,worker,heartbeat_utc,created_utc,"
        "triggered_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        (eid, schedule["id"], schedule["name"], schedule["process_key"], ts(scheduled_for), ts(started), ts(ended),
         0.0 if ended else None, trigger, status, attempt, ts(retry_at), error_type or None, error_msg or None,
         worker or None, ts(now) if status == "RUNNING" else None, ts(now), triggered_by or None))
    return eid if n == 1 else None


def get_execution(eid: str) -> Optional[dict]:
    return _one("SELECT * FROM sched_executions WHERE id = ?", (eid,))


def finish_execution(eid: str, status: str, now: dt.datetime, error_type: str = "", error_msg: str = "",
                     log_text: str = "") -> None:
    row = get_execution(eid)
    started = parse_ts(row["started_utc"]) if row else None
    dur = (now - started).total_seconds() if started else 0.0
    _write("UPDATE sched_executions SET status=?, ended_utc=?, duration_sec=?, error_type=?, error_msg=?, log_text=? "
           "WHERE id=?", (status, ts(now), round(dur, 3), error_type or None, (error_msg or None),
                          (log_text or "")[:200000] or None, eid))


def running_for_schedule(sid: str) -> List[dict]:
    return _rows("SELECT * FROM sched_executions WHERE schedule_id=? AND status='RUNNING'", (sid,))


def running_for_process(process_key: str) -> List[dict]:
    return _rows("SELECT * FROM sched_executions WHERE process_key=? AND status='RUNNING'", (process_key,))


def count_status(sid: str, status: str) -> int:
    r = _one("SELECT COUNT(*) AS n FROM sched_executions WHERE schedule_id=? AND status=?", (sid, status))
    return int(r["n"]) if r else 0


def touch_running(worker: str, now: dt.datetime):
    _write("UPDATE sched_executions SET heartbeat_utc=? WHERE status='RUNNING' AND worker=?", (ts(now), worker))


def fail_stale_running(now: dt.datetime, stale_seconds: int) -> int:
    """RUNNING rows nobody has touched for a while belong to a crashed / restarted instance."""
    cutoff = ts(now - dt.timedelta(seconds=stale_seconds))
    return _write(
        "UPDATE sched_executions SET status='FAILED', ended_utc=?, error_type='Interrupted', "
        "error_msg='The server stopped or restarted while this was running. NOT re-run automatically — check what it had already done.' "
        "WHERE status='RUNNING' AND (heartbeat_utc IS NULL OR heartbeat_utc < ?)", (ts(now), cutoff))


def due_retries(now: dt.datetime) -> List[dict]:
    return _rows("SELECT * FROM sched_executions WHERE status='RETRY_WAIT' AND retry_at_utc <= ?", (ts(now),))


def queued_rows() -> List[dict]:
    return _rows("SELECT * FROM sched_executions WHERE status='QUEUED' ORDER BY created_utc")


def claim_row(eid: str, from_status: str, now: dt.datetime, worker: str) -> bool:
    """Atomically move a QUEUED / RETRY_WAIT row to RUNNING. Exactly one caller gets True."""
    n = _write("UPDATE sched_executions SET status='RUNNING', started_utc=?, heartbeat_utc=?, worker=? "
               "WHERE id=? AND status=?", (ts(now), ts(now), worker, eid, from_status))
    return n == 1


def list_executions(date_from: Optional[dt.datetime] = None, date_to: Optional[dt.datetime] = None,
                    process_key: str = "", schedule_id: str = "", status: str = "", limit: int = 200) -> List[dict]:
    where, params = [], []
    if date_from:
        where.append("created_utc >= ?"); params.append(ts(date_from))
    if date_to:
        where.append("created_utc < ?"); params.append(ts(date_to))
    if process_key:
        where.append("process_key = ?"); params.append(process_key)
    if schedule_id:
        where.append("schedule_id = ?"); params.append(schedule_id)
    if status:
        where.append("status = ?"); params.append(status)
    sql = ("SELECT id,schedule_id,schedule_name,process_key,scheduled_for_utc,started_utc,ended_utc,duration_sec,"
           "trigger_type,status,attempt,error_type,error_msg,worker,created_utc,triggered_by FROM sched_executions"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_utc DESC LIMIT " + str(int(limit)))
    return _rows(sql, params)


def last_execution(sid: str, real_only: bool = True) -> Optional[dict]:
    extra = " AND status IN ('SUCCESS','FAILED','RUNNING','MISSED')" if real_only else ""
    return _one("SELECT id,status,started_utc,ended_utc,duration_sec,trigger_type,error_msg FROM sched_executions "
                "WHERE schedule_id=?" + extra + " ORDER BY created_utc DESC LIMIT 1", (sid,))


def counts_between(date_from: dt.datetime, date_to: dt.datetime) -> Dict[str, int]:
    rows = _rows("SELECT status, COUNT(*) AS n FROM sched_executions WHERE created_utc >= ? AND created_utc < ? "
                 "GROUP BY status", (ts(date_from), ts(date_to)))
    return {r["status"]: int(r["n"]) for r in rows}


def count_running() -> int:
    r = _one("SELECT COUNT(*) AS n FROM sched_executions WHERE status='RUNNING'")
    return int(r["n"]) if r else 0


def list_audit(limit: int = 200) -> List[dict]:
    return _rows("SELECT * FROM sched_audit ORDER BY ts_utc DESC LIMIT " + str(int(limit)))


# ─────────────────────────────────────────────────────────────────────────────
# leader lease  (one instance runs the tick loop; heartbeat doubles as the health signal)
# ─────────────────────────────────────────────────────────────────────────────
def acquire_lease(name: str, holder: str, now: dt.datetime, lease_seconds: int) -> bool:
    until = ts(now + dt.timedelta(seconds=lease_seconds))
    n = _write(
        "INSERT INTO sched_lease (name, holder, heartbeat_utc, lease_until_utc) VALUES (?,?,?,?) "
        "ON CONFLICT (name) DO UPDATE SET holder=EXCLUDED.holder, heartbeat_utc=EXCLUDED.heartbeat_utc, "
        "lease_until_utc=EXCLUDED.lease_until_utc "
        "WHERE sched_lease.holder = EXCLUDED.holder OR sched_lease.lease_until_utc < ?",
        (name, holder, ts(now), until, ts(now)))
    return n == 1


def release_lease(name: str, holder: str):
    try:
        _write("DELETE FROM sched_lease WHERE name=? AND holder=?", (name, holder))
    except Exception:
        pass


def read_lease(name: str) -> Optional[dict]:
    return _one("SELECT * FROM sched_lease WHERE name = ?", (name,))
