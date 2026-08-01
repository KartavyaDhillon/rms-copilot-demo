r"""
auth.py — employee-ID sign-in for the RMS Copilot console.

WHAT THIS IS FOR
────────────────
Until now every run was attributed to whatever name someone had typed into
Settings (usually nothing, hence "unknown user"). That is fine for "who's got
MCX open?" and useless for "who ran this wrong thing" — anyone can type any
name. This replaces it with credentials: runs are attributed to the account
that signed in, which cannot be set by typing.

Be precise about what that proves: an ACCOUNT was used. Not that a particular
human was at the keyboard. That is the standard every audit accepts, and it is
as far as anything short of biometrics can go.

THE LOCKOUT DECISION — READ THIS BEFORE ENABLING
────────────────────────────────────────────────
This console drives fixed intraday run windows. If sign-in breaks at 09:25 and
the 09:30 run cannot start, that is a worse day than any audit gap: a missed
margin run has immediate financial consequence, an unattributed one is a line
in a log.

So enforcement is OFF by default:

    RMS_AUTH_REQUIRED=false   (default) — signing in WORKS and is recorded, but
                              nobody is blocked. Runs by a signed-in user are
                              attributed to them; runs by anyone else are
                              attributed exactly as they were before.
    RMS_AUTH_REQUIRED=true    — the console redirects to /login and refuses
                              run/config actions without a valid session.

Turn it on deliberately, once accounts exist for everybody and the break-glass
path below has been tested — not on the same day you create the users.

BREAK GLASS
───────────
tools/manage_users.py runs directly on the server and needs no working UI:

    python tools\manage_users.py sheet                  (load Book1.xlsx)
    python tools\manage_users.py add M0214478 "Kartavya Dhillon"
    python tools\manage_users.py reset M0214478
    python tools\manage_users.py disable M0214478
    python tools\manage_users.py list

If sign-in is broken and someone is stuck, set RMS_AUTH_REQUIRED=false and
restart — that always works, because enforcement is a single env flag rather
than something woven through every route.

SESSIONS
────────
A signed token in an HttpOnly cookie. Signed with RMS_SECRET_KEY (HMAC-SHA256)
so it cannot be forged client-side; HttpOnly so page scripts cannot read it.
Default lifetime 12 hours — one shift. Long enough not to interrupt anyone
mid-run, short enough that a walk-away machine is signed out by the next day.

Sessions are stateless (nothing stored server-side) so a server restart does
NOT sign everyone out mid-session — which matters when restarting is the normal
way to pick up code changes here.
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
import secrets

try:
    import bcrypt
    _BCRYPT = True
except Exception:                      # pragma: no cover
    _BCRYPT = False

COOKIE_NAME = "rms_session"


# ── config ──────────────────────────────────────────────────────────────────
def auth_required():
    return os.getenv("RMS_AUTH_REQUIRED", "false").strip().lower() == "true"


def session_hours():
    try:
        return float(os.getenv("RMS_SESSION_HOURS", "12"))
    except Exception:
        return 12.0


def _secret():
    """Signing key. A generated fallback keeps a mis-configured server WORKING
    (sessions simply don't survive a restart) rather than refusing to boot —
    but it is logged once so it doesn't go unnoticed forever."""
    s = os.getenv("RMS_SECRET_KEY", "").strip()
    if s:
        return s.encode()
    global _EPHEMERAL
    try:
        return _EPHEMERAL
    except NameError:
        pass
    _EPHEMERAL = secrets.token_bytes(32)
    print("[auth] WARNING: RMS_SECRET_KEY not set in .env — using a temporary key. "
          "Sessions will not survive a server restart. Set one for production.",
          flush=True)
    return _EPHEMERAL


def _use_postgres():
    return os.getenv("USE_POSTGRES", "false").strip().lower() == "true"


def _conn():
    if _use_postgres():
        from core.db_postgres import _conn as c
    else:
        from core.db import _conn as c
    return c()


def _ph():
    return "%s" if _use_postgres() else "?"


# ── passwords ───────────────────────────────────────────────────────────────
def hash_password(plain):
    if not _BCRYPT:
        raise RuntimeError("bcrypt is not installed — run: pip install bcrypt")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain, hashed):
    if not _BCRYPT or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except Exception:
        return False


# ── user store ──────────────────────────────────────────────────────────────
def get_user(employee_id):
    eid = (employee_id or "").strip().upper()
    if not eid:
        return None
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute(
            f"SELECT employee_id, display_name, password_hash, is_active, must_change "
            f"FROM app_users WHERE UPPER(employee_id) = {_ph()}", (eid,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {"employee_id": row[0], "display_name": row[1], "password_hash": row[2],
                "is_active": bool(row[3]), "must_change": bool(row[4])}
    except Exception as e:
        print(f"[auth] user lookup failed for {eid}: {type(e).__name__}: {e}", flush=True)
        return None


def upsert_user(employee_id, display_name, password, must_change=True):
    eid = (employee_id or "").strip().upper()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    h = hash_password(password)
    conn = _conn(); cur = conn.cursor()
    p = _ph()
    if _use_postgres():
        cur.execute(
            f"INSERT INTO app_users (employee_id, display_name, password_hash, "
            f"is_active, must_change, created_at) VALUES ({p},{p},{p},TRUE,{p},{p}) "
            f"ON CONFLICT (employee_id) DO UPDATE SET display_name=EXCLUDED.display_name, "
            f"password_hash=EXCLUDED.password_hash, must_change=EXCLUDED.must_change",
            (eid, display_name, h, must_change, now))
    else:
        cur.execute(
            f"INSERT INTO app_users (employee_id, display_name, password_hash, "
            f"is_active, must_change, created_at) VALUES ({p},{p},{p},1,{p},{p}) "
            f"ON CONFLICT(employee_id) DO UPDATE SET display_name=excluded.display_name, "
            f"password_hash=excluded.password_hash, must_change=excluded.must_change",
            (eid, display_name, h, 1 if must_change else 0, now))
    conn.commit(); conn.close()
    return {"ok": True, "employee_id": eid}


def update_display_name(employee_id, display_name):
    """Rename an EXISTING account without touching its password or must_change
    flag — safe to call every time the roster sheet is synced, unlike
    upsert_user() which would also rehash whatever password is passed in."""
    eid = (employee_id or "").strip().upper()
    conn = _conn(); cur = conn.cursor(); p = _ph()
    cur.execute(f"UPDATE app_users SET display_name={p} WHERE UPPER(employee_id)={p}",
                (display_name, eid))
    n = cur.rowcount
    conn.commit(); conn.close()
    return {"ok": n > 0, "changed": n}


def set_active(employee_id, active):
    conn = _conn(); cur = conn.cursor(); p = _ph()
    val = True if active else False
    if not _use_postgres():
        val = 1 if active else 0
    cur.execute(f"UPDATE app_users SET is_active={p} WHERE UPPER(employee_id)={p}",
                (val, (employee_id or "").strip().upper()))
    n = cur.rowcount
    conn.commit(); conn.close()
    return {"ok": n > 0, "changed": n}


def list_users():
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("SELECT employee_id, display_name, is_active, must_change, "
                    "last_login_at FROM app_users ORDER BY employee_id")
        rows = cur.fetchall(); conn.close()
        return [{"employee_id": r[0], "display_name": r[1], "is_active": bool(r[2]),
                 "must_change": bool(r[3]), "last_login_at": r[4]} for r in rows]
    except Exception as e:
        print(f"[auth] list_users failed: {type(e).__name__}: {e}", flush=True)
        return []


def _touch_login(employee_id):
    try:
        conn = _conn(); cur = conn.cursor(); p = _ph()
        cur.execute(f"UPDATE app_users SET last_login_at={p} WHERE UPPER(employee_id)={p}",
                    (datetime.datetime.now().isoformat(timespec="seconds"),
                     (employee_id or "").strip().upper()))
        conn.commit(); conn.close()
    except Exception:
        pass


# ── sessions (stateless, HMAC-signed) ───────────────────────────────────────
def _b64e(b):
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_session(employee_id, display_name):
    payload = {"sub": employee_id, "name": display_name,
               "exp": (datetime.datetime.now()
                       + datetime.timedelta(hours=session_hours())).timestamp()}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return body + "." + sig


def read_session(token):
    """Return the payload if the token is intact and unexpired, else None."""
    if not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    try:
        expect = _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        # compare_digest: constant-time, so a forged token can't be probed byte by byte
        if not hmac.compare_digest(sig, expect):
            return None
        data = json.loads(_b64d(body))
        if float(data.get("exp", 0)) < datetime.datetime.now().timestamp():
            return None
        return data
    except Exception:
        return None


def authenticate(employee_id, password):
    """→ {"ok":True,"employee_id","display_name","must_change"} or {"ok":False,"error"}.

    The error text is deliberately the SAME for "no such ID" and "wrong password":
    telling someone which employee IDs exist is free reconnaissance.
    """
    generic = "Invalid Employee ID or password."
    u = get_user(employee_id)
    if not u:
        return {"ok": False, "error": generic}
    if not u["is_active"]:
        return {"ok": False, "error": "This account has been disabled. Contact the RMS tech desk."}
    if not verify_password(password, u["password_hash"]):
        return {"ok": False, "error": generic}
    _touch_login(u["employee_id"])
    return {"ok": True, "employee_id": u["employee_id"],
            "display_name": u["display_name"], "must_change": u["must_change"]}


def current_user(request):
    """Signed-in user for a FastAPI request, or None. Safe to call anywhere."""
    try:
        return read_session(request.cookies.get(COOKIE_NAME, ""))
    except Exception:
        return None
