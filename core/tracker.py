"""core/tracker.py - WORK TRACKER: tasks the manager hands out (console work or anything else), a diary of what is due
each day, follow-ups that wait on somebody's reply, comments, pictures / files and a full history.

Storage : the console database (PostgreSQL when USE_POSTGRES=true, otherwise the local SQLite file) - four small tables.
Files   : <data root>\\WORK TRACKER\\files\\<task id>\\<random>_<name>
Who     : the signed-in user of the request ({"sub": employee id, "name": display name, "is_admin": bool}).
Rules   : a manager (console admin, or role "manager" in People) sees every task that is not private;
          everybody else sees the tasks they created or that are assigned to them; a private note is seen by its owner only.
          The creator / a manager may change everything; the assignee may change the status, the waiting details,
          comment and attach.
Alerts  : best effort, never blocks the request - WhatsApp picture + e-mail to the person's contact kept in People.
Timer   : once a day (Settings, default 09:30, Mon-Sat) every person gets one digest of their overdue / due-today /
          follow-up tasks and the managers get one summary.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import os
import re
import threading
import uuid
from pathlib import Path

NAME = "WORK TRACKER"
STATUSES = ("open", "in_progress", "waiting", "review", "done", "cancelled")
STATUS_LABEL = {"open": "Open", "in_progress": "In progress", "waiting": "Waiting for reply", "review": "Review",
                "done": "Done", "cancelled": "Cancelled"}
CLOSED = ("done", "cancelled")
PRIORITIES = ("low", "normal", "high", "urgent")
KINDS = ("task", "followup", "change", "bug", "note")
MAX_FILE_MB = 15
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
BAD_EXT = (".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".js", ".scr", ".dll", ".jar", ".sh")
DEFAULT_SETTINGS = {"enabled": True, "remind_at": "09:30", "manager_summary": True, "notify_whatsapp": True, "notify_email": True,
                    "mailbox_enabled": True, "mailbox_poll_min": 10}
_NEW_COLS = (("replied_at", "TEXT"), ("log_ref", "TEXT"), ("waiting_email", "TEXT"), ("mail_subject", "TEXT"))

_INIT_LOCK = threading.Lock()
_READY = {"ok": False}
_TICK = {"thread": None, "stop": threading.Event()}
_SENDER = {"fn": None}          # tests: fn(channel, to, subject, lines) instead of the real WhatsApp / e-mail
_MAIL = {"fn": None, "pop": None}   # tests: fn(subject, html, to, cc, reply_to) instead of SMTP; pop() -> [(uid, from, subject, date)] instead of POP3
_POLL = {"last": 0.0}
SYSTEM = {"sub": "MAILBOX", "name": "Mailbox"}


class TrackerError(Exception):
    """A message that is shown to the user as it is."""


# ───────────────────────────── database ─────────────────────────────
def _pg() -> bool:
    return os.getenv("USE_POSTGRES", "false").strip().lower() == "true"


def _conn():
    if _pg():
        from core.db_postgres import _conn as c
    else:
        from core.db import _conn as c
    return c()


def _q(sql: str) -> str:
    return sql.replace("?", "%s") if _pg() else sql


def _rows(cur) -> list:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _run(sql: str, params=(), fetch=False, insert=False):
    """One statement in its own transaction. fetch -> list of dicts, insert -> the new id."""
    ensure()
    conn = _conn()
    try:
        cur = conn.cursor()
        if insert and _pg():
            cur.execute(_q(sql) + " RETURNING id", params)
            new_id = cur.fetchone()[0]
        else:
            cur.execute(_q(sql), params)
            new_id = cur.lastrowid if insert else None
        out = _rows(cur) if fetch else new_id
        conn.commit()
        return out
    finally:
        conn.close()


def ensure() -> None:
    """Create the tables once (safe to call any number of times)."""
    if _READY["ok"]:
        return
    with _INIT_LOCK:
        if _READY["ok"]:
            return
        idc = "SERIAL PRIMARY KEY" if _pg() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        ddl = [
            f"""CREATE TABLE IF NOT EXISTS tracker_tasks (
                id {idc}, title TEXT NOT NULL, description TEXT, kind TEXT, priority TEXT, status TEXT,
                created_by TEXT, created_by_name TEXT, assignee TEXT, assignee_name TEXT,
                due TEXT, process TEXT, waiting_on TEXT, waiting_since TEXT, follow_up_days INTEGER,
                last_followup TEXT, private INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT, done_at TEXT)""",
            f"""CREATE TABLE IF NOT EXISTS tracker_activity (
                id {idc}, task_id INTEGER NOT NULL, kind TEXT, by_id TEXT, by_name TEXT, body TEXT, at TEXT)""",
            f"""CREATE TABLE IF NOT EXISTS tracker_files (
                id {idc}, task_id INTEGER NOT NULL, name TEXT, stored TEXT, size INTEGER, is_image INTEGER,
                by_id TEXT, by_name TEXT, at TEXT)""",
            """CREATE TABLE IF NOT EXISTS tracker_people (
                employee_id TEXT PRIMARY KEY, mobile TEXT, email TEXT, role TEXT)""",
            """CREATE TABLE IF NOT EXISTS tracker_meta (k TEXT PRIMARY KEY, v TEXT)""",
            "CREATE INDEX IF NOT EXISTS tracker_activity_task ON tracker_activity (task_id)",
            "CREATE INDEX IF NOT EXISTS tracker_files_task ON tracker_files (task_id)",
        ]
        conn = _conn()
        try:
            cur = conn.cursor()
            for s in ddl:
                cur.execute(s)
            conn.commit()
        finally:
            conn.close()
        for _n, _t in _NEW_COLS:                                 # columns added later - old databases get them here
            c2 = _conn()
            try:
                c2.cursor().execute(f"ALTER TABLE tracker_tasks ADD COLUMN {'IF NOT EXISTS ' if _pg() else ''}{_n} {_t}")
                c2.commit()
            except Exception:                                    # noqa: BLE001  (already there)
                try:
                    c2.rollback()
                except Exception:                                # noqa: BLE001
                    pass
            finally:
                c2.close()
        _READY["ok"] = True


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return dt.date.today().isoformat()


def _meta_get(k: str, default=None):
    r = _run("SELECT v FROM tracker_meta WHERE k = ?", (k,), fetch=True)
    if not r:
        return default
    try:
        return json.loads(r[0]["v"])
    except Exception:                                            # noqa: BLE001
        return default


def _meta_set(k: str, v) -> None:
    s = json.dumps(v)
    if _run("SELECT k FROM tracker_meta WHERE k = ?", (k,), fetch=True):
        _run("UPDATE tracker_meta SET v = ? WHERE k = ?", (s, k))
    else:
        _run("INSERT INTO tracker_meta (k, v) VALUES (?, ?)", (k, s))


# ───────────────────────────── people ─────────────────────────────
def _uid(user) -> str:
    if not user or not (user.get("sub") or user.get("employee_id")):
        raise TrackerError("Sign in to use the Work Tracker.")
    return str(user.get("sub") or user.get("employee_id")).strip().upper()


def _uname(user) -> str:
    return (user.get("name") or user.get("display_name") or _uid(user)).strip()


def _contacts() -> dict:
    return {r["employee_id"].upper(): r for r in _run("SELECT * FROM tracker_people", fetch=True)}


def is_manager(user) -> bool:
    if user and user.get("is_admin"):
        return True
    try:
        return (_contacts().get(_uid(user)) or {}).get("role") == "manager"
    except TrackerError:
        return False


def people() -> list:
    """Every active console user with the contact details kept here."""
    try:
        from core import auth
        users = [u for u in auth.list_users() if u.get("is_active")]
    except Exception:                                            # noqa: BLE001
        users = []
    c = _contacts()
    out = []
    for u in users:
        eid = str(u["employee_id"]).upper()
        x = c.get(eid) or {}
        out.append({"id": eid, "name": u.get("display_name") or eid, "mobile": x.get("mobile") or "", "email": x.get("email") or "",
                    "role": "manager" if (u.get("is_admin") or x.get("role") == "manager") else "member",
                    "admin": bool(u.get("is_admin"))})
    out.sort(key=lambda p: p["name"].lower())
    return out


def _person_name(eid: str) -> str:
    for p in people():
        if p["id"] == eid:
            return p["name"]
    return eid


def save_person(user, employee_id: str, mobile: str = "", email: str = "", role: str = "member") -> dict:
    """A manager edits anybody; everybody may edit their own mobile / e-mail (not their role)."""
    eid = str(employee_id or "").strip().upper()
    me = _uid(user)
    mgr = is_manager(user)
    if not eid:
        raise TrackerError("Pick a person.")
    if not mgr and eid != me:
        raise TrackerError("Only a manager can change another person's details.")
    mobile = re.sub(r"[^\d+]", "", str(mobile or ""))
    if mobile and not re.fullmatch(r"\+?\d{10,13}", mobile):
        raise TrackerError("Mobile number looks wrong - use 10 digits (or +91...).")
    email = str(email or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise TrackerError("E-mail address looks wrong.")
    old = _contacts().get(eid)
    role = role if role in ("manager", "member") else "member"
    if not mgr:
        role = (old or {}).get("role") or "member"
    if old:
        _run("UPDATE tracker_people SET mobile = ?, email = ?, role = ? WHERE employee_id = ?", (mobile, email, role, eid))
    else:
        _run("INSERT INTO tracker_people (employee_id, mobile, email, role) VALUES (?, ?, ?, ?)", (eid, mobile, email, role))
    return {"ok": True}


# ───────────────────────────── settings ─────────────────────────────
def get_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update({k: v for k, v in (_meta_get("settings", {}) or {}).items() if k in DEFAULT_SETTINGS})
    return s


def save_settings(user, **kw) -> dict:
    if not is_manager(user):
        raise TrackerError("Only a manager can change the tracker settings.")
    s = get_settings()
    if kw.get("remind_at") is not None:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(kw["remind_at"])):
            raise TrackerError("Reminder time must be HH:MM (24 hour).")
        s["remind_at"] = str(kw["remind_at"])
    for k in ("enabled", "manager_summary", "notify_whatsapp", "notify_email", "mailbox_enabled"):
        if kw.get(k) is not None:
            s[k] = bool(kw[k])
    if kw.get("mailbox_poll_min") is not None:
        try:
            s["mailbox_poll_min"] = max(2, min(120, int(kw["mailbox_poll_min"])))
        except (TypeError, ValueError):
            raise TrackerError("Mailbox check interval must be a number of minutes.")
    _meta_set("settings", s)
    return s


# ───────────────────────────── tasks ─────────────────────────────
def _clean_date(v) -> str:
    v = str(v or "").strip()
    if not v:
        return ""
    try:
        return dt.date.fromisoformat(v[:10]).isoformat()
    except ValueError:
        raise TrackerError("Due date must be a real date.")


def _visible(t: dict, me: str, mgr: bool) -> bool:
    if t.get("private"):
        return t.get("created_by") == me
    return mgr or t.get("created_by") == me or t.get("assignee") == me


def _can_edit(t: dict, me: str, mgr: bool) -> bool:
    return t.get("created_by") == me or (mgr and not t.get("private"))


def _decorate(t: dict, me: str, mgr: bool) -> dict:
    today = _today()
    open_ = t["status"] not in CLOSED
    t["key"] = f"T-{t['id']}"
    t["private"] = bool(t.get("private"))
    t["overdue"] = bool(open_ and t.get("due") and t["due"] < today)
    t["due_today"] = bool(open_ and t.get("due") == today)
    t["status_label"] = STATUS_LABEL.get(t["status"], t["status"])
    t["waiting_days"] = None
    if t["status"] == "waiting" and t.get("waiting_since"):
        try:
            t["waiting_days"] = (dt.date.today() - dt.date.fromisoformat(t["waiting_since"][:10])).days
        except ValueError:
            pass
    t["can_edit"] = _can_edit(t, me, mgr)
    t["mine"] = t.get("assignee") == me
    try:
        t["log"] = json.loads(t.get("log_ref") or "") or None
    except Exception:                                            # noqa: BLE001
        t["log"] = None
    t["replied"] = bool(t.get("replied_at") and t["status"] != "waiting")
    return t


def _get(task_id: int) -> dict:
    r = _run("SELECT * FROM tracker_tasks WHERE id = ?", (int(task_id),), fetch=True)
    if not r:
        raise TrackerError("That task no longer exists.")
    return r[0]


def _event(task_id: int, user, text: str, kind: str = "event") -> None:
    _run("INSERT INTO tracker_activity (task_id, kind, by_id, by_name, body, at) VALUES (?, ?, ?, ?, ?, ?)",
         (int(task_id), kind, _uid(user), _uname(user), text, _now()))


def list_tasks(user) -> list:
    """Every task this user may see: all open ones + the ones closed in the last 45 days."""
    me, mgr = _uid(user), is_manager(user)
    cut = (dt.date.today() - dt.timedelta(days=45)).isoformat()
    rows = _run("SELECT * FROM tracker_tasks WHERE status NOT IN ('done','cancelled') OR updated_at >= ? ORDER BY id DESC", (cut,), fetch=True)
    rows = [_decorate(t, me, mgr) for t in rows if _visible(t, me, mgr)]
    counts = {r["task_id"]: r["n"] for r in _run(
        "SELECT task_id, COUNT(*) AS n FROM tracker_activity WHERE kind = 'comment' GROUP BY task_id", fetch=True)}
    files = {r["task_id"]: r["n"] for r in _run("SELECT task_id, COUNT(*) AS n FROM tracker_files GROUP BY task_id", fetch=True)}
    for t in rows:
        t["comments"] = counts.get(t["id"], 0)
        t["files"] = files.get(t["id"], 0)
    return rows


def summary(user) -> dict:
    ts = list_tasks(user)
    me = _uid(user)
    mine = [t for t in ts if t["assignee"] == me and t["status"] not in CLOSED]
    return {"mine": len(mine), "overdue": sum(1 for t in mine if t["overdue"]), "today": sum(1 for t in mine if t["due_today"]),
            "waiting": sum(1 for t in ts if t["status"] == "waiting"),
            "review": sum(1 for t in ts if t["status"] == "review" and t["created_by"] == me)}


def get_task(user, task_id: int) -> dict:
    me, mgr = _uid(user), is_manager(user)
    t = _get(task_id)
    if not _visible(t, me, mgr):
        raise TrackerError("You cannot see this task.")
    t = _decorate(t, me, mgr)
    t["activity"] = _run("SELECT * FROM tracker_activity WHERE task_id = ? ORDER BY id", (t["id"],), fetch=True)
    t["file_list"] = _run("SELECT id, name, size, is_image, by_name, at FROM tracker_files WHERE task_id = ? ORDER BY id", (t["id"],), fetch=True)
    return t


def create_task(user, title: str, description: str = "", assignee: str = "", due: str = "", priority: str = "normal",
                kind: str = "task", process: str = "", private: bool = False, follow_up_days=None, waiting_on: str = "",
                log_ref=None, waiting_email: str = "") -> dict:
    me, name = _uid(user), _uname(user)
    title = str(title or "").strip()
    if len(title) < 3:
        raise TrackerError("Write a title of at least 3 letters.")
    if len(title) > 200:
        raise TrackerError("The title is too long (200 letters at most).")
    kind = kind if kind in KINDS else "task"
    priority = priority if priority in PRIORITIES else "normal"
    private = bool(private) or kind == "note"
    assignee = (str(assignee or "").strip().upper() or me)
    if private:
        assignee = me
    ids = {p["id"] for p in people()}
    if assignee != me and ids and assignee not in ids:
        raise TrackerError("That person is not a console user.")
    fud = _days(follow_up_days)
    now = _now()
    lref = ""
    if isinstance(log_ref, dict) and log_ref.get("file"):
        lref = json.dumps({k: str(log_ref.get(k) or "")[:200] for k in ("file", "logdir", "label", "pipe_name", "pipe")})
    wmail = _clean_emails(waiting_email)
    status = "waiting" if (kind == "followup" and str(waiting_on or "").strip()) else "open"
    tid = _run("""INSERT INTO tracker_tasks (title, description, kind, priority, status, created_by, created_by_name, assignee,
                  assignee_name, due, process, waiting_on, waiting_since, follow_up_days, last_followup, private, created_at, updated_at,
                  log_ref, waiting_email)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
               (title, str(description or "").strip(), kind, priority, status, me, name, assignee,
                name if assignee == me else _person_name(assignee), _clean_date(due), str(process or "").strip()[:1000],
                str(waiting_on or "").strip()[:200], now if status == "waiting" else "", fud, "", 1 if private else 0, now, now,
                lref, wmail),
               insert=True)
    _event(tid, user, "Created" + ("" if assignee == me else f" and assigned to {_person_name(assignee)}"))
    t = _get(tid)
    if assignee != me:
        notify("assigned", t, user)
    return get_task(user, tid)


def _clean_emails(v) -> str:
    parts = [x.strip() for x in re.split(r"[,;\s]+", str(v or "")) if x.strip()]
    if len(parts) > 5:
        raise TrackerError("At most 5 e-mail addresses.")
    for x in parts:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", x):
            raise TrackerError(f"'{x}' is not a valid e-mail address.")
    return ", ".join(parts)


def _days(v):
    if v in (None, "", 0, "0"):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise TrackerError("Follow-up days must be a number.")
    if not 1 <= n <= 60:
        raise TrackerError("Follow-up days must be 1 to 60.")
    return n


def update_task(user, task_id: int, **ch) -> dict:
    """Change any of: title, description, assignee, due, priority, kind, process, status, waiting_on, follow_up_days."""
    me, mgr = _uid(user), is_manager(user)
    t = _get(task_id)
    if not _visible(t, me, mgr):
        raise TrackerError("You cannot see this task.")
    full = _can_edit(t, me, mgr)
    own = t["assignee"] == me
    if not (full or own):
        raise TrackerError("Only the person it is assigned to, the creator or a manager can change this task.")
    sets, notes, notif = {}, [], []
    for k in ("title", "description", "process", "kind", "priority", "due", "assignee"):
        if ch.get(k) is None:
            continue
        if not full:
            raise TrackerError("Only the creator or a manager can change that. You can change the status and add comments.")
        v = str(ch[k]).strip()
        if k == "title":
            if len(v) < 3:
                raise TrackerError("Write a title of at least 3 letters.")
            v = v[:200]
        elif k == "kind":
            v = v if v in KINDS else t["kind"]
        elif k == "priority":
            v = v if v in PRIORITIES else t["priority"]
        elif k == "due":
            v = _clean_date(v)
        elif k == "assignee":
            v = v.upper() or me
            if t.get("private") and v != t["created_by"]:
                raise TrackerError("A private note cannot be assigned to somebody else.")
        if v == (t.get(k) or ""):
            continue
        sets[k] = v
        if k == "assignee":
            sets["assignee_name"] = _person_name(v)
            notes.append(f"Assigned to {sets['assignee_name']} (was {t.get('assignee_name') or '-'})")
            notif.append("assigned")
        elif k == "due":
            notes.append(f"Due date: {v or 'none'} (was {t.get('due') or 'none'})")
        elif k == "priority":
            notes.append(f"Priority: {v} (was {t.get('priority')})")
        elif k == "title":
            notes.append("Title changed")
        elif k == "description":
            notes.append("Description changed")
        elif k == "process":
            notes.append(f"Linked process: {v or 'none'}")
        elif k == "kind":
            notes.append(f"Type: {v}")
    if ch.get("waiting_on") is not None:
        v = str(ch["waiting_on"]).strip()[:200]
        if v != (t.get("waiting_on") or ""):
            sets["waiting_on"] = v
            notes.append(f"Waiting on: {v or 'nobody'}")
    if ch.get("waiting_email") is not None:
        v = _clean_emails(ch["waiting_email"])
        if v != (t.get("waiting_email") or ""):
            sets["waiting_email"] = v
    if ch.get("mail_subject") is not None:
        v = str(ch["mail_subject"]).strip()[:200]
        if v != (t.get("mail_subject") or ""):
            sets["mail_subject"] = v
            notes.append(f"Sent mail linked: {v}" if v else "Sent mail link removed")
    if "follow_up_days" in ch and ch["follow_up_days"] is not None:
        v = _days(ch["follow_up_days"])
        if v != t.get("follow_up_days"):
            sets["follow_up_days"] = v
            notes.append(f"Follow up every {v} day(s)" if v else "Follow-up reminders off")
    if ch.get("status") is not None:
        v = str(ch["status"]).strip()
        if v not in STATUSES:
            raise TrackerError("Unknown status.")
        if v != t["status"]:
            if t["status"] in CLOSED and not full:
                raise TrackerError("A finished task can only be reopened by the person who created it or a manager.")
            sets["status"] = v
            notes.append(f"Status: {STATUS_LABEL[v]} (was {STATUS_LABEL.get(t['status'], t['status'])})")
            if v == "waiting":
                sets["waiting_since"] = _now()
                sets["last_followup"] = ""
            if v in CLOSED:
                sets["done_at"] = _now()
            if v in ("done", "review") and t["created_by"] != me:
                notif.append("status")
            if v in ("open", "in_progress") and t["status"] in CLOSED and t["assignee"] != me:
                notif.append("reopened")
    if not sets:
        return get_task(user, task_id)
    sets["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in sets)
    _run(f"UPDATE tracker_tasks SET {cols} WHERE id = ?", tuple(sets.values()) + (int(task_id),))
    for n in notes:
        _event(task_id, user, n)
    new = _get(task_id)
    for n in notif:
        notify(n, new, user)
    return get_task(user, task_id)


def add_comment(user, task_id: int, text: str) -> dict:
    me, mgr = _uid(user), is_manager(user)
    t = _get(task_id)
    if not _visible(t, me, mgr):
        raise TrackerError("You cannot see this task.")
    text = str(text or "").strip()
    if not text:
        raise TrackerError("Write something first.")
    _event(task_id, user, text[:4000], kind="comment")
    _run("UPDATE tracker_tasks SET updated_at = ? WHERE id = ?", (_now(), int(task_id)))
    notify("comment", t, user, extra=text[:300])
    return get_task(user, task_id)


def delete_task(user, task_id: int) -> dict:
    me, mgr = _uid(user), is_manager(user)
    t = _get(task_id)
    if not (user and user.get("is_admin")):
        raise TrackerError("Only an admin can delete a task.")
    for f in _run("SELECT stored FROM tracker_files WHERE task_id = ?", (int(task_id),), fetch=True):
        try:
            (files_root() / str(int(task_id)) / f["stored"]).unlink()
        except OSError:
            pass
    for tb in ("tracker_files", "tracker_activity"):
        _run(f"DELETE FROM {tb} WHERE task_id = ?", (int(task_id),))
    _run("DELETE FROM tracker_tasks WHERE id = ?", (int(task_id),))
    return {"ok": True}


# ───────────────────────────── files / pictures ─────────────────────────────
def files_root() -> Path:
    try:
        from core.env_boot import data_root
        base = Path(data_root())
    except Exception:                                            # noqa: BLE001
        base = Path(os.getenv("RMS_DATA_ROOT", Path(__file__).resolve().parent.parent / "data"))
    p = base / NAME / "files"
    p.mkdir(parents=True, exist_ok=True)
    return p


def add_file(user, task_id: int, filename: str, content: bytes) -> dict:
    me, mgr = _uid(user), is_manager(user)
    t = _get(task_id)
    if not _visible(t, me, mgr):
        raise TrackerError("You cannot see this task.")
    name = re.sub(r"[^\w.\- ()]+", "_", os.path.basename(str(filename or "file")))[:120] or "file"
    ext = os.path.splitext(name)[1].lower()
    if ext in BAD_EXT:
        raise TrackerError(f"{ext} files are not allowed.")
    if not content:
        raise TrackerError("That file is empty.")
    if len(content) > MAX_FILE_MB * 1024 * 1024:
        raise TrackerError(f"The file is bigger than {MAX_FILE_MB} MB.")
    d = files_root() / str(int(task_id))
    d.mkdir(parents=True, exist_ok=True)
    stored = uuid.uuid4().hex[:12] + "_" + name
    (d / stored).write_bytes(content)
    fid = _run("INSERT INTO tracker_files (task_id, name, stored, size, is_image, by_id, by_name, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (int(task_id), name, stored, len(content), 1 if ext in IMG_EXT else 0, me, _uname(user), _now()), insert=True)
    _event(task_id, user, f"Attached {name}")
    _run("UPDATE tracker_tasks SET updated_at = ? WHERE id = ?", (_now(), int(task_id)))
    return {"ok": True, "id": fid, "name": name}


def file_path(user, file_id: int):
    """-> (path, download name, is_image) after the visibility check."""
    r = _run("SELECT * FROM tracker_files WHERE id = ?", (int(file_id),), fetch=True)
    if not r:
        raise TrackerError("That file no longer exists.")
    f = r[0]
    t = _get(f["task_id"])
    if not _visible(t, _uid(user), is_manager(user)):
        raise TrackerError("You cannot see this file.")
    p = files_root() / str(f["task_id"]) / f["stored"]
    if not p.is_file():
        raise TrackerError("That file is missing on the disk.")
    return p, f["name"], bool(f["is_image"])


def delete_file(user, file_id: int) -> dict:
    r = _run("SELECT * FROM tracker_files WHERE id = ?", (int(file_id),), fetch=True)
    if not r:
        return {"ok": True}
    f = r[0]
    t = _get(f["task_id"])
    me, mgr = _uid(user), is_manager(user)
    if not (f["by_id"] == me or _can_edit(t, me, mgr)):
        raise TrackerError("Only the person who attached it, the creator or a manager can remove this file.")
    try:
        (files_root() / str(f["task_id"]) / f["stored"]).unlink()
    except OSError:
        pass
    _run("DELETE FROM tracker_files WHERE id = ?", (int(file_id),))
    _event(f["task_id"], user, f"Removed {f['name']}")
    return {"ok": True}


# ───────────────────────────── alerts (best effort) ─────────────────────────────
def _log(msg: str) -> None:
    print(f"[tracker] {msg}", flush=True)


def _card_image(title: str, lines: list) -> Path:
    """A plain picture for WhatsApp: a title and a few lines."""
    from PIL import Image, ImageDraw
    from core.scheduler import failure_alert as fa, paths
    W = 1000
    f_t, f_l = fa._font(52, True), fa._font(34)
    probe = ImageDraw.Draw(Image.new("RGB", (W, 10)))
    wrapped = []
    for ln in lines[:14]:
        wrapped += fa._wrap(probe, str(ln), f_l, W - 120) or [""]
    h = 190 + 50 * len(wrapped) + 60
    img = Image.new("RGB", (W, h), (247, 249, 252))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 130], fill=(37, 99, 235))
    d.text((60, 36), title[:34], font=f_t, fill=(255, 255, 255))
    y = 170
    for ln in wrapped:
        d.text((60, y), ln, font=f_l, fill=(23, 32, 46))
        y += 50
    out = paths.sched_root() / "alerts" / f"tracker_{uuid.uuid4().hex[:10]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def _send(person: dict, subject: str, lines: list) -> None:
    """One message to one person over the channels that are switched on and have a contact."""
    s = get_settings()
    if _SENDER["fn"] is not None:
        if person.get("mobile") and s["notify_whatsapp"]:
            _SENDER["fn"]("whatsapp", person["mobile"], subject, lines)
        if person.get("email") and s["notify_email"]:
            _SENDER["fn"]("email", person["email"], subject, lines)
        return

    class _C:
        def log(self, m):
            _log(str(m))

    if person.get("mobile") and s["notify_whatsapp"]:
        try:
            from core.scheduler import connections, helpers as H
            c = connections.get("whatsapp")
            if c.get("auth_token"):
                num = person["mobile"] if person["mobile"].startswith("+") else "+91" + person["mobile"][-10:]
                H.whatsapp_send_image(_card_image(subject, lines), num, subject[:60], dt.datetime.now().strftime("%d-%m-%Y %H:%M"), _C(),
                                      attempts=2, delay=5.0, template_name=(c.get("alert_template") or "").strip() or None)
        except Exception as e:                                   # noqa: BLE001
            _log(f"WhatsApp to {person.get('name')} not sent: {type(e).__name__}: {str(e)[:160]}")
    if person.get("email") and s["notify_email"]:
        try:
            from core.scheduler import helpers as H
            body = ("<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.6'><b style='font-size:16px'>"
                    + _html.escape(subject) + "</b><br><br>" + "<br>".join(_html.escape(str(x)) for x in lines)
                    + "<br><br><span style='color:#667'>RMS Copilot - Work Tracker</span></div>")
            H.send_email("[Work Tracker] " + subject, body, [person["email"]], [], [], _C(), html=True, attempts=1)
        except Exception as e:                                   # noqa: BLE001
            _log(f"e-mail to {person.get('name')} not sent: {type(e).__name__}: {str(e)[:160]}")


def notify(kind: str, t: dict, actor, extra: str = "") -> None:
    """Tell the other side about a change - in a thread, never raises."""
    try:
        me, who = _uid(actor), _uname(actor)
        ppl = {p["id"]: p for p in people()}
        key = f"T-{t['id']}"
        due = f"Due: {t['due']}" if t.get("due") else "No due date"
        if kind in ("assigned", "reopened"):
            to = [t["assignee"]]
            subject = ("Task assigned" if kind == "assigned" else "Task reopened") + f" - {key}"
            lines = [t["title"], f"From: {who}", due, f"Priority: {t.get('priority')}"]
            if t.get("description"):
                lines.append(str(t["description"])[:300])
        elif kind == "status":
            to = [t["created_by"]]
            subject = f"{STATUS_LABEL.get(t['status'], t['status'])} - {key}"
            lines = [t["title"], f"By: {who}", due]
        elif kind == "reply":
            to = [x for x in {t["created_by"], t["assignee"]} if x != me]
            subject = f"Reply received - {key}"
            lines = [t["title"], extra or "A reply has come."]
        elif kind == "comment":
            to = [x for x in {t["created_by"], t["assignee"]} if x != me]
            subject = f"New comment - {key}"
            lines = [t["title"], f"{who}: {extra}"]
        else:
            return
        targets = [ppl[x] for x in to if x and x != me and x in ppl and (ppl[x].get("mobile") or ppl[x].get("email"))]
        if not targets:
            return

        def work():
            for p in targets:
                _send(p, subject, lines)

        if _SENDER["fn"] is not None:
            work()
        else:
            threading.Thread(target=work, name="tracker-notify", daemon=True).start()
    except Exception as e:                                       # noqa: BLE001
        _log(f"notify error: {type(e).__name__}: {e}")


# ───────────────────────────── daily digest ─────────────────────────────
def _needs_followup(t: dict, today: dt.date) -> bool:
    if t["status"] != "waiting" or not t.get("follow_up_days"):
        return False
    base = (t.get("last_followup") or t.get("waiting_since") or "")[:10]
    try:
        return (today - dt.date.fromisoformat(base)).days >= int(t["follow_up_days"])
    except ValueError:
        return False


def digest(today: dt.date | None = None) -> dict:
    """Send every person their list (overdue, due today, follow-ups) and the managers one summary. -> counts."""
    today = today or dt.date.today()
    iso = today.isoformat()
    rows = [t for t in _run("SELECT * FROM tracker_tasks WHERE status NOT IN ('done','cancelled')", fetch=True) if not t.get("private")]
    ppl = {p["id"]: p for p in people()}
    per, sent = {}, 0
    for t in rows:
        tag = None
        if _needs_followup(t, today):
            tag = "FOLLOW UP"
        elif t.get("due") and t["due"] < iso:
            tag = "OVERDUE"
        elif t.get("due") == iso:
            tag = "DUE TODAY"
        if tag:
            per.setdefault(t["assignee"], []).append((tag, t))
    for eid, items in per.items():
        p = ppl.get(eid)
        if not p or not (p.get("mobile") or p.get("email")):
            continue
        order = {"OVERDUE": 0, "FOLLOW UP": 1, "DUE TODAY": 2}
        items.sort(key=lambda x: (order[x[0]], x[1].get("due") or ""))
        lines = [f"{tag}: T-{t['id']} {t['title'][:70]}" + (f" (waiting on {t['waiting_on']})" if tag == "FOLLOW UP" and t.get("waiting_on") else "")
                 for tag, t in items[:12]]
        if len(items) > 12:
            lines.append(f"... and {len(items) - 12} more")
        _send(p, f"Your tasks today - {len(items)}", lines)
        sent += 1
        for tag, t in items:
            if tag == "FOLLOW UP":
                _run("UPDATE tracker_tasks SET last_followup = ? WHERE id = ?", (iso, t["id"]))
    mgr_sent = 0
    if get_settings()["manager_summary"] and rows:
        overdue = [t for t in rows if t.get("due") and t["due"] < iso]
        waiting = [t for t in rows if t["status"] == "waiting"]
        if overdue or waiting:
            lines = [f"Open tasks: {len(rows)}", f"Overdue: {len(overdue)}", f"Waiting for a reply: {len(waiting)}", ""]
            lines += [f"OVERDUE: T-{t['id']} {t['title'][:55]} - {t.get('assignee_name') or t['assignee']}" for t in overdue[:8]]
            lines += [f"WAITING: T-{t['id']} {t['title'][:55]} - {t.get('assignee_name') or t['assignee']}" for t in waiting[:6]]
            for p in ppl.values():
                if p["role"] == "manager" and (p.get("mobile") or p.get("email")):
                    _send(p, "Work Tracker summary", lines)
                    mgr_sent += 1
    return {"people": sent, "managers": mgr_sent, "tasks": sum(len(v) for v in per.values())}


def tick(now: dt.datetime | None = None) -> bool:
    """Timer step: Mon-Sat, once a day at / after the reminder time. True when the digest ran."""
    now = now or dt.datetime.now()
    s = get_settings()
    if not s["enabled"] or now.weekday() == 6 or now.strftime("%H:%M") < s["remind_at"]:
        return False
    if _meta_get("last_digest") == now.date().isoformat():
        return False
    _meta_set("last_digest", now.date().isoformat())
    try:
        r = digest(now.date())
        _log(f"daily digest sent: {r}")
    except Exception as e:                                       # noqa: BLE001
        _log(f"daily digest failed: {type(e).__name__}: {e}")
    return True


# ───────────────────────────── replies, follow-up mail, mailbox ─────────────────────────────
def mark_reply(user, task_id: int, note: str = "", by_mailbox: bool = False) -> dict:
    """The reply has come: the task leaves "Waiting for reply" and the time is kept."""
    me, mgr = _uid(user), is_manager(user)
    t = _get(task_id)
    if not by_mailbox:
        if not _visible(t, me, mgr):
            raise TrackerError("You cannot see this task.")
        if not (_can_edit(t, me, mgr) or t["assignee"] == me):
            raise TrackerError("Only the person it is assigned to, the creator or a manager can do this.")
    if t["status"] in CLOSED:
        raise TrackerError("This task is already closed.")
    note = str(note or "").strip()[:300]
    sets = {"replied_at": _now(), "updated_at": _now(), "waiting_since": "", "last_followup": ""}
    if t["status"] == "waiting":
        sets["status"] = "in_progress"
    cols = ", ".join(f"{k} = ?" for k in sets)
    _run(f"UPDATE tracker_tasks SET {cols} WHERE id = ?", tuple(sets.values()) + (int(task_id),))
    _event(task_id, user, "Reply received" + (f" - {note}" if note else "") + (" (found in the tracker mailbox)" if by_mailbox else ""))
    notify("reply", _get(task_id), user, extra=note)
    return get_task(user, task_id) if not by_mailbox else {"ok": True}


def mailbox_cfg() -> dict:
    """The shared tracker mailbox (Scheduler > Connections > Work Tracker mailbox). {} when it is not filled in."""
    try:
        from core.scheduler import connections
        c = connections.get("tracker_pop3")
        return c if c.get("server") and c.get("username") and c.get("password") else {}
    except Exception:                                            # noqa: BLE001
        return {}


def mailbox_status() -> dict:
    cfg = mailbox_cfg()
    st = _meta_get("mailbox", {}) or {}
    return {"configured": bool(cfg), "address": cfg.get("username", ""), "last_checked": st.get("last_checked", ""),
            "last_error": st.get("error", ""), "enabled": get_settings()["mailbox_enabled"]}


def _norm_subject(v: str) -> str:
    v = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", str(v or ""), flags=re.I)
    return re.sub(r"\s+", " ", v).strip().lower()


def _decode_hdr(v) -> str:
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(str(v or ""))))
    except Exception:                                            # noqa: BLE001
        return str(v or "")


def _read_pop() -> list:
    """[(uidl, from address, subject, date)] of every message in the tracker mailbox - read only, nothing is deleted."""
    if _MAIL["pop"] is not None:
        return _MAIL["pop"]()
    import poplib
    import ssl
    from email import message_from_bytes
    from email.utils import parseaddr
    c = mailbox_cfg()
    pop = poplib.POP3_SSL(c["server"], int(c.get("port") or 995), timeout=40, context=ssl.create_default_context())
    out = []
    try:
        pop.user(c["username"])
        pop.pass_(c["password"])
        uidl = {}
        for ln in pop.uidl()[1]:
            a = ln.decode("ascii", "replace").split()
            if len(a) >= 2:
                uidl[int(a[0])] = a[1]
        for num in sorted(uidl)[-300:]:
            top = pop.top(num, 0)[1]
            m = message_from_bytes(b"\r\n".join(top))
            out.append((uidl[num], parseaddr(_decode_hdr(m.get("From")))[1].lower(), _decode_hdr(m.get("Subject")), str(m.get("Date") or "")))
    finally:
        try:
            pop.quit()
        except Exception:                                        # noqa: BLE001
            pass
    return out


def poll_mailbox(force: bool = False) -> dict:
    """Look in the tracker mailbox for replies to waiting tasks. A mail counts as the reply when its subject carries the
    task key ([T-12]) or the subject of the linked sent mail, and it was not sent by the console itself."""
    s = get_settings()
    if not force and (not s["mailbox_enabled"] or not mailbox_cfg()):
        return {"ok": True, "skipped": True}
    try:
        msgs = _read_pop()
    except Exception as e:                                       # noqa: BLE001
        _meta_set("mailbox", {"last_checked": _now(), "error": f"{type(e).__name__}: {str(e)[:160]}"})
        _log(f"mailbox check failed: {type(e).__name__}: {str(e)[:160]}")
        return {"ok": False, "error": str(e)[:160]}
    seen = _meta_get("pop_seen", None)
    ids = [m[0] for m in msgs]
    if seen is None:                                             # first look: older mail is not a reply to anything
        _meta_set("pop_seen", ids[-2000:])
        _meta_set("mailbox", {"last_checked": _now(), "error": ""})
        return {"ok": True, "first": True, "matched": 0}
    seen_set, matched = set(seen), 0
    own = {mailbox_cfg().get("username", "").lower()}
    try:
        from core.scheduler import connections
        own.add((connections.get("smtp").get("email") or "").lower())
    except Exception:                                            # noqa: BLE001
        pass
    waiting = [t for t in _run("SELECT * FROM tracker_tasks WHERE status NOT IN ('done','cancelled')", fetch=True)]
    for uid, frm, subj, _d in msgs:
        if uid in seen_set:
            continue
        seen_set.add(uid)
        if frm in own or not frm:
            continue
        ns, hit = _norm_subject(subj), None
        m = re.search(r"\[?T-(\d+)\]?", subj or "")
        if m:
            hit = next((t for t in waiting if t["id"] == int(m.group(1))), None)
        if hit is None and ns:
            hit = next((t for t in waiting if t.get("mail_subject") and _norm_subject(t["mail_subject"]) == ns), None)
        if hit and hit["status"] == "waiting":
            mark_reply(SYSTEM, hit["id"], f"from {frm}: {subj[:100]}", by_mailbox=True)
            matched += 1
    _meta_set("pop_seen", list(seen_set)[-2000:] if len(seen_set) > 2000 else list(seen_set))
    _meta_set("mailbox", {"last_checked": _now(), "error": ""})
    return {"ok": True, "matched": matched}


def send_followup(user, task_id: int, to: str, subject: str = "", body: str = "") -> dict:
    """E-mail the person the task is waiting on. The subject always carries [T-<id>] so the reply can be found."""
    me, mgr = _uid(user), is_manager(user)
    t = _get(task_id)
    if not _visible(t, me, mgr) or not (_can_edit(t, me, mgr) or t["assignee"] == me):
        raise TrackerError("Only the person it is assigned to, the creator or a manager can send this.")
    rcpt = [x.strip() for x in _clean_emails(to).split(",") if x.strip()]
    if not rcpt:
        raise TrackerError("Enter the e-mail address to send to.")
    key = f"T-{t['id']}"
    subject = str(subject or "").strip() or t["title"]
    if f"[{key}]" not in subject:
        subject = f"[{key}] {subject}"
    body = str(body or "").strip() or f"Dear Sir/Madam,\n\nThis is a gentle reminder regarding: {t['title']}.\nKindly reply at the earliest.\n\nRegards,\n{_uname(user)}"
    cfg = mailbox_cfg()
    reply_to = cfg.get("username", "")
    html = ("<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.6'>" + _html.escape(body).replace("\n", "<br>")
            + "<br><br><span style='color:#889;font-size:12px'>Please keep " + key + " in the subject when you reply.</span></div>")
    cc = [reply_to] if reply_to else []
    if _MAIL["fn"] is not None:
        _MAIL["fn"](subject, html, rcpt, cc, reply_to)
    else:
        class _C:
            def log(self, m):
                _log(str(m))
        try:
            from core.scheduler import helpers as H
            H.send_email(subject, html, rcpt, cc, [], _C(), html=True, attempts=1, reply_to=reply_to)
        except Exception as e:                                   # noqa: BLE001
            raise TrackerError(f"The mail could not be sent: {str(e)[:200]}")
    sets = {"waiting_email": ", ".join(rcpt), "mail_subject": subject, "last_followup": _today(), "updated_at": _now()}
    if not t.get("waiting_on"):
        sets["waiting_on"] = ", ".join(rcpt)[:200]
    if t["status"] != "waiting":
        sets["status"] = "waiting"
        sets["waiting_since"] = _now()
        sets["last_followup"] = _today()
    cols = ", ".join(f"{k} = ?" for k in sets)
    _run(f"UPDATE tracker_tasks SET {cols} WHERE id = ?", tuple(sets.values()) + (int(task_id),))
    _event(task_id, user, f"Follow-up mail sent to {', '.join(rcpt)}: {subject}")
    return get_task(user, task_id)


def _loop():
    while not _TICK["stop"].wait(60):
        try:
            tick()
        except Exception as e:                                   # noqa: BLE001
            _log(f"timer error: {type(e).__name__}: {e}")
        try:
            import time as _t
            if get_settings()["mailbox_enabled"] and mailbox_cfg() and _t.time() - _POLL["last"] >= get_settings()["mailbox_poll_min"] * 60:
                _POLL["last"] = _t.time()
                poll_mailbox()
        except Exception as e:                                   # noqa: BLE001
            _log(f"mailbox timer error: {type(e).__name__}: {e}")


def start_ticker() -> None:
    t = _TICK["thread"]
    if t and t.is_alive():
        return
    _TICK["stop"].clear()
    t = threading.Thread(target=_loop, name="tracker-tick", daemon=True)
    _TICK["thread"] = t
    t.start()


def bootstrap(user) -> dict:
    """Everything the page needs when it opens."""
    me = _uid(user)
    names = set()
    try:                                                         # Process Center / KPI cards
        from core.pipeline_box import PIPELINES
        names |= {str(v.get("name")).strip() for v in PIPELINES.values() if v.get("name")}
    except Exception:                                            # noqa: BLE001
        pass
    try:                                                         # every Scheduler process
        from core.scheduler import catalog as _cat
        names |= {str(d.name).strip() for d in _cat.all_defs() if d.name}
    except Exception as e:                                       # noqa: BLE001
        _log(f"process list: scheduler catalogue not read ({type(e).__name__}: {e})")
    procs = sorted({n for n in names if n}, key=str.upper)
    return {"me": {"id": me, "name": _uname(user), "manager": is_manager(user), "admin": bool(user and user.get("is_admin"))}, "people": people(), "processes": procs,
            "settings": get_settings(), "statuses": [{"id": s, "label": STATUS_LABEL[s]} for s in STATUSES],
            "priorities": list(PRIORITIES), "kinds": list(KINDS), "max_file_mb": MAX_FILE_MB, "mailbox": mailbox_status()}
