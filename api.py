"""
Backend API. Start: uvicorn api:app --reload  (docs at /docs)

LIVE STREAMING: /peak-margin-stream uses Server-Sent Events (SSE) to push each
status line the moment it happens, so the UI shows progress like a terminal.

AUTO_SEND in .env:
  false -> stage a pending send; human approves.
  true  -> send automatically after a successful run (for the scheduler).
"""
import os
import re
import json
import uuid
import traceback
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from urllib.parse import quote
from pydantic import BaseModel
from dotenv import load_dotenv
# MUST be the first core.* import. Runs the two-stage .env.local → .env load at
# import time, so every module imported below already sees the resolved
# configuration (including ${RMS_DATA_ROOT}-interpolated folder paths). Any
# core module imported before this would call plain load_dotenv() first and
# read a half-configured environment. See core/env_boot.py.
import core.env_boot  # noqa: F401
from core.compliance import answer_compliance
from core.agent import run_agent_traced
from core.pipelines.peak_margin_run import process_pair_stream, process_pair, cleanup_after_send
from core.pipelines.concentration_run import stream_download, stream_compute, process_concentration
from core.pipelines.ctcl_vs_nmass_run import stream_ctcl_vs_nmass
from core.pipelines.cnc_f11_run import stream_cnc_f11
from core.pipelines.mtf_order_run import stream_mtf_order
from core.pipelines.ftp_download_run import stream_ftp_download
from core.pipelines.payout_inspection_run import stream_daily, stream_client
from core.pipelines.files_downloader_70_run import stream_files_downloader_70
from core.agent_run import stream_agent
from core.pipelines.peak_margin import FOLDER
from core.notify import send_peak_margin_report

load_dotenv()
AUTO_SEND = os.getenv("AUTO_SEND", "false").strip().lower() == "true"

os.environ.setdefault("RMS_REPO_ROOT", os.path.dirname(os.path.abspath(__file__)))   # child scripts (sa_codes/, generated copies) use it to import core.*

app = FastAPI(title="RMS Co-pilot API")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
PENDING = {}

# ── SCHEDULER / AUTOMATION CENTER (core/scheduler/) ─────────────────────────
# Own routes (/sched/*), own tables, own tick loop. Wrapped so that a problem in the
# scheduler can never stop the rest of the console from booting. The tick loop only
# runs when SCHEDULER_ENABLED=true in .env.
try:
    from core.scheduler.routes import router as _sched_router
    app.include_router(_sched_router)
except Exception as _e:
    print(f"⚠️  Scheduler routes not loaded: {type(_e).__name__}: {_e}")


@app.on_event("startup")
async def _more_worker_threads():
    """Long downloads/streams each hold a worker thread; 40 (the default) can run out with 3 PCs open. 100 keeps pages responsive."""
    try:
        import anyio.to_thread as _att
        _att.current_default_thread_limiter().total_tokens = 100
    except Exception as _e:
        print(f"[threads] could not raise worker limit: {_e}", flush=True)


@app.on_event("startup")
def _scheduler_startup():
    try:
        from core.scheduler import engine as _sched_engine
        _sched_engine.start_if_enabled()
    except Exception as _e:
        print(f"⚠️  Scheduler startup failed: {type(_e).__name__}: {_e}")


@app.on_event("startup")
def _file_flicker_watch_startup():
    try:
        from core import file_probe as _fp
        _fp.start_watch()
    except Exception as _e:
        print(f"⚠️  file watcher not started: {_e}")


@app.on_event("startup")
def _common_ftp_startup():
    """Fetch today's HOLDING / RISK_REPORT from the FTP into COMMON FILES (07:30-08:15, every 5 min). Never blocks startup."""
    try:
        from core import common_ftp as _cf
        _cf.start_background()
    except Exception as _e:
        print(f"⚠️  COMMON FILES auto-fetch not started: {type(_e).__name__}: {_e}")


@app.on_event("shutdown")
def _scheduler_shutdown():
    try:
        from core.scheduler import engine as _sched_engine
        _sched_engine.shutdown()
    except Exception:
        pass

# Ensure shared common folder exists on startup
_common_dir = os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
try:
    os.makedirs(_common_dir, exist_ok=True)
    print(f"📂 SA_COMMON_DIR ready: {_common_dir}")
except Exception as _e:
    print(f"⚠️  Could not create SA_COMMON_DIR ({_common_dir}): {_e}")

# When on PostgreSQL, make sure every table in core/db_postgres.py's schema
# actually exists (CREATE TABLE IF NOT EXISTS - safe to run every boot).
# Previously this only ran via a manual `python core/db_postgres.py setup`,
# so a table added to the schema (e.g. margin_whatsapp_log) would silently
# not exist on the live server until someone remembered to run that command
# by hand - this auto-heals that class of bug on every restart from now on.
if os.getenv("USE_POSTGRES", "false").strip().lower() == "true":
    try:
        from core.db_postgres import setup_db as _pg_setup_db
        _pg_setup_db()
    except Exception as _e:
        print(f"⚠️  PostgreSQL schema check failed (tables may be missing): {_e}")


class Question(BaseModel): question: str
class Goal(BaseModel): goal: str
class Approval(BaseModel): token: str


# ── STOP-BUTTON OWNERSHIP ─────────────────────────────────────────────────
# Only the person who started a run (or an admin) may Stop it. Before this,
# every Stop endpoint force-killed the tracked subprocess unconditionally —
# anyone with the dashboard open could stop anyone else's run.
def _actor(request: Request):
    """(identity, is_admin) for THIS request. Identity uses the exact same
    'name (employee_id)' format lock_manager.on_start() stores as a lock's
    "by" field (see /px/run-stream, /sa/run-stream) so the two compare
    directly. Falls back to the free-text rms_user cookie when not signed in
    (RMS_AUTH_REQUIRED=false, or auth not used) — never admin in that case,
    since admin comes only from a real signed session."""
    try:
        from core.auth import current_user as _cu
        u = _cu(request)
        if u:
            return f"{u.get('name') or u.get('sub')} ({u.get('sub')})", bool(u.get("is_admin"))
    except Exception:
        pass
    try:
        from urllib.parse import unquote as _uq
        _nm = _uq(request.cookies.get("rms_user", "")).strip()
        if not _nm:
            from core import lock_manager as _lm5
            _nm = _lm5.ip_identity(request.client.host if request.client else "")
        return (_nm or None), False
    except Exception:
        return None, False


_ACTOR_ID_RE = re.compile(r"\(([^)]+)\)\s*$")

def _actor_id(s):
    """Pull the stable employee-id portion out of a '(name (id))'-style
    identity string, e.g. 'Abhishek (M0214483)' -> 'm0214483'. Lowercased so
    the comparison in _can_stop() is case-insensitive. Returns None if the
    string doesn't match the expected shape (falls back to full-string
    comparison in that case, unchanged from before)."""
    if not s:
        return None
    m = _ACTOR_ID_RE.search(s.strip())
    return m.group(1).strip().lower() if m else None

def _can_stop(lock_key, request: Request):
    """(allowed, denial_message). Ownership comes from
    core.lock_manager._META[lock_key]["by"] — set when the run started (see
    on_start()). If nobody's identity is known on EITHER side (no signed-in
    users at all, or the lock has no "by" recorded), this allows the stop —
    an unenforceable rule must never wedge a run nobody can then stop.

    2026-08-18 fix: the owner comparison used to be a raw `owner == by`
    string equality on the full "Name (ID)" text. That broke the one case
    this check exists to protect — the actual starter being unable to stop
    their own run — whenever the display name changed even slightly between
    starting the run and clicking Stop (a roster edit, trailing whitespace,
    different casing coming through the Book1.xlsx name-sync that runs on
    every server start). The employee ID inside the parentheses is the
    stable, canonical identifier — it doesn't change — so that's compared
    first now, case-insensitively. Full-string equality remains as a
    fallback for the (now rare) case where either side doesn't parse into
    the expected 'Name (ID)' shape at all."""
    by, admin = _actor(request)
    if admin:
        return True, None
    try:
        from core.lock_manager import _META
        owner = (_META.get(lock_key) or {}).get("by")
    except Exception:
        owner = None
    if not owner or not by:
        return True, None
    owner_id, by_id = _actor_id(owner), _actor_id(by)
    if (owner_id and by_id and owner_id == by_id) or owner == by:
        return True, None
    return False, f"Only {owner} (who started this run) or an admin can stop it."


# ── CAPTCHA / TOKEN POPUPS: show only to the person who started the run, never a stale one ──────────────
# Every browser polls the *-pending endpoints, and they all read the same state file — so one person's captcha
# used to pop up on everybody's screen (and an old PENDING file left by a dead run kept popping up for ever).
_POPUP_STALE_S = 3 * 60           # a captcha/token request older than 3 minutes is never shown (and is cleared)
_POPUP_IDLE_S = 120               # PENDING but no run/download of that pipe is active for this long → leftover
# Standalone download buttons that are NOT a pipeline run (so no lock / action slot records who clicked them) register
# their requester here for the duration of the download: {popup pipe name: "name (employee id)"}.
# Without this, the popup of such a button has no recorded owner and is shown to admins only — a colleague who
# clicks the button never gets the RSA token popup and the download times out waiting for a token nobody can enter.
_POPUP_OWNERS: dict = {}


def _popup_state_age(sf):
    import os as _o, time as _t
    try:
        return _t.time() - _o.path.getmtime(sf)
    except Exception:
        return None


def _popup_mark_stale(sf):
    """Neutralise a leftover PENDING file so it cannot come back (JSON state or the SA plain-text state)."""
    import json as _j
    try:
        with open(sf, encoding="utf-8") as f:
            txt = f.read().strip()
        with open(sf, "w", encoding="utf-8") as f:
            if txt.startswith("{"):
                _j.dump({"state": "STALE"}, f)
            else:
                f.write("STALE")
    except Exception:
        pass


def _popup_pipe_active(pipe):
    """True if a run / standalone download / NMASS login of this pipe is alive right now (fail-open on any doubt)."""
    try:
        if _POPUP_OWNERS.get(pipe):          # a standalone button of this pipe (e.g. MCX Deposit) is downloading
            return True
        if pipe == "sa":
            from core.short_allocation import _SA
            return bool(_SA.get("running"))
        from core.pipeline_box import _PB, running_actions
        if (_PB.get(pipe) or {}).get("running"):
            return True
        if any(p == pipe for p, _a, _s in running_actions()):
            return True
        from core import dl_lock
        return bool(dl_lock.status().get("busy"))
    except Exception:
        return True


def _popup_visible(request: Request, sf, pipes):
    """True when THIS browser should show the popup whose state file is `sf`, raised by one of `pipes`.
    • stale (old, or no run of that pipe alive) → hidden and cleared;
    • the run / download was started by somebody (recorded by the lock manager or the action slot) → only that
      person sees it;
    • nobody recorded (e.g. started by the Scheduler) → admins only."""
    age = _popup_state_age(sf)
    if age is not None:
        if age > _POPUP_STALE_S or (age > _POPUP_IDLE_S and not any(_popup_pipe_active(p) for p in pipes)):
            _popup_mark_stale(sf)
            return False
    try:
        from core.lock_manager import _META
        owners = []
        for p in pipes:
            by = (_META.get("sa" if p == "sa" else f"px_{p}") or {}).get("by")
            if by and by != "unknown user":
                owners.append(by)
        try:                                         # standalone download buttons record who clicked them
            from core.pipeline_box import _ACT
            for _k, _slot in list(_ACT.items()):
                if _k.partition(":")[0] in pipes and _slot.get("running") and _slot.get("by") \
                        and _slot["by"] != "unknown user":
                    owners.append(_slot["by"])
        except Exception:
            pass
        for _p in pipes:                             # standalone buttons that register their own requester
            _o = _POPUP_OWNERS.get(_p)
            if _o and _o != "unknown user":
                owners.append(_o)
        me, _admin = _actor(request)
        if not owners:
            # nobody recorded (e.g. a Scheduler run) → admins only; if the console runs without sign-in at all
            # there is no admin, so show it rather than leave the run unanswerable
            from core.auth import current_user as _cu2
            return bool(_admin) or (os.getenv("RMS_AUTH_REQUIRED", "false").strip().lower() != "true" and not _cu2(request))
        if not me:
            return False
        me_id = _actor_id(me)
        return any(o == me or (me_id and _actor_id(o) == me_id) for o in owners)
    except Exception:
        return True


@app.get("/health")
def health():
    return {"status": "ok", "auto_send": AUTO_SEND}


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate",
             "Pragma": "no-cache", "Expires": "0"}
_REPO_DIR = os.path.dirname(os.path.abspath(__file__))


@app.get("/")
def serve_platform():
    """Serve the AI Operations Platform shell (platform/index.html) as the app's
    front door — Dashboard first, with the original console embedded inside it
    as the "Process Center" page. If the platform folder is ever missing, fall
    back to the original console so the team is never locked out."""
    shell = os.path.join(_REPO_DIR, "platform", "index.html")
    if os.path.isfile(shell):
        return FileResponse(shell, media_type="text/html", headers=_NO_CACHE)
    return FileResponse(os.path.join(_REPO_DIR, "dashboard.html"),
                        media_type="text/html", headers=_NO_CACHE)


@app.get("/legacy")
def serve_dashboard(embed: int = 0):
    """The original console (dashboard.html), byte-for-byte untouched on disk.
    Direct visit (/legacy) = exactly the pre-platform experience, kept as a
    safety hatch. With ?embed=1 (how the platform's Process Center iframes it)
    the response gets a <link> to platform/css/process-reskin.css injected at
    serve time — a CSS-only visual upgrade; the file on disk is never edited
    and no JS/logic changes."""
    path = os.path.join(_REPO_DIR, "dashboard.html")
    if not embed:
        return FileResponse(path, media_type="text/html", headers=_NO_CACHE)
    try:
        html = open(path, encoding="utf-8").read()
        # process-theme-sync.js runs BEFORE the stylesheet so data-rms-theme is
        # already set on <html> the instant process-reskin.css starts applying
        # — same "no flash of the wrong theme" reasoning as index.html's own
        # inline prefs script. A classic (non-async) <script src> in <head>
        # blocks parsing until it runs, so this is guaranteed to finish before
        # <body> paints.
        inject = ('<script src="/platform/js/process-theme-sync.js?v=1"></script>'
                  '<link rel="stylesheet" href="/platform/css/process-reskin.css?v=6">'
                  "<script>document.documentElement.classList.add('pc-embedded')</script></head>")
        html = html.replace("</head>", inject, 1)
        # global lock overlay: subscribes to /ops/locks/stream and live-disables
        # the affected boxes' buttons for EVERY user when a run is active
        # anywhere (server is the source of truth). Injected at serve time —
        # dashboard.html on disk is never modified.
        html = html.replace("</body>",
                            '<script src="/platform/js/rms-busy.js?v=3"></script>'
                            '<script src="/platform/js/process-locks.js?v=9"></script>'
                            '<script src="/platform/js/process-probable-box.js?v=3"></script>'
                            '<script src="/platform/js/process-mtfic.js?v=9"></script>'
                            '<script src="/platform/js/process-alloc.js?v=7"></script>'
                            '<script src="/platform/js/process-physdel.js?v=5"></script>'
                            '<script src="/platform/js/process-liveallocation.js?v=6"></script>'
                            '<script src="/platform/js/process-mcxcash.js?v=4"></script>'
                            '<script src="/platform/js/process-saauto.js?v=2"></script></body>', 1)
        from fastapi.responses import HTMLResponse
        return HTMLResponse(html, headers=_NO_CACHE)
    except Exception:
        return FileResponse(path, media_type="text/html", headers=_NO_CACHE)


# Static assets for the platform shell (CSS/JS/icons). Mounted under /platform
# so nothing collides with existing API routes.
try:
    from fastapi.staticfiles import StaticFiles
    _plat_dir = os.path.join(_REPO_DIR, "platform")
    if os.path.isdir(_plat_dir):
        app.mount("/platform", StaticFiles(directory=_plat_dir), name="platform")
except Exception:
    pass


# ── Sign-in gate ───────────────────────────────────────────────────────────
# Runs before every request. Only active when RMS_AUTH_REQUIRED=true.
#
# ESCAPE HATCH, WRITTEN DOWN ON PURPOSE: if anyone is ever locked out — a bad
# deploy, a lost password at 09:25 with a run due — set RMS_AUTH_REQUIRED=false
# in .env and restart. That always works, because enforcement is this ONE
# function and nothing else in the app checks for a session.
#
# The allow-list below is what must stay reachable for signing in to be
# possible at all. Anything omitted from it would make the login page unable
# to load its own stylesheet and lock everyone out permanently.
_AUTH_OPEN_PREFIXES = (
    "/login",             # the sign-in page itself
    "/platform/login/",   # its css/js/logo
    "/auth/",             # login, logout, me, change-password
    "/health",            # uptime checks must never need a session
    "/favicon",
)


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    try:
        from core import auth as _a
        if not _a.auth_required():
            return await call_next(request)

        path = request.url.path or "/"
        if path.startswith(_AUTH_OPEN_PREFIXES):
            return await call_next(request)

        if _a.current_user(request):
            return await call_next(request)

        # Not signed in. A browser asking for a PAGE gets sent to the login
        # screen; anything else (fetch/XHR/SSE) gets a 401 it can handle,
        # because redirecting an API call to HTML produces confusing errors.
        accept = (request.headers.get("accept") or "")
        wants_html = "text/html" in accept and request.method == "GET"
        if wants_html:
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/login", status_code=302)
        return JSONResponse({"ok": False, "error": "Not signed in.",
                             "login": "/login"}, status_code=401)
    except Exception as _e:
        # A bug in the gate must never take the whole console down. Fail OPEN
        # and say so loudly — an unattributed run beats a desk that can't run
        # its 09:30 job. (Same reasoning as core/auth.py's lockout note.)
        print(f"[auth] gate error, allowing request through: {type(_e).__name__}: {_e}",
              flush=True)
        return await call_next(request)


# ── Roster sheet sync (Book1.xlsx) ──────────────────────────────────────────
# Runs once at boot so the sheet is genuinely the source of truth: add a row,
# restart the server (already the normal way to pick up any change — see
# START_SERVER.bat, auto-reload is off), and the account exists next time
# anyone looks — no separate manage_users.py step required. Editing an
# EXISTING row's NAME column also takes effect here (sync_to_db renames
# existing accounts; it only ever refuses to touch a password).
# Never fatal: a malformed sheet must not stop the server from starting.
try:
    from core import user_sheet as _boot_sheet
    _boot_res = _boot_sheet.sync_to_db()
    if _boot_res.get("created") or _boot_res.get("renamed"):
        print(f"[user_sheet] boot sync: {len(_boot_res.get('created', []))} created, "
              f"{len(_boot_res.get('renamed', []))} renamed from {_boot_sheet.sheet_path()}",
              flush=True)
except Exception as _boot_e:
    print(f"[user_sheet] boot sync skipped: {type(_boot_e).__name__}: {_boot_e}", flush=True)


# ── Authentication (employee ID + password) ────────────────────────────────
# See the long note at the top of core/auth.py, especially the lockout section.
# Enforcement is OFF unless RMS_AUTH_REQUIRED=true; signing in still works and
# is still recorded, it just doesn't block anyone. That is deliberate: a missed
# 09:30 run costs more than an unattributed one.
@app.post("/auth/login")
async def auth_login(request: Request):
    from core import auth as _a
    try:
        body = await request.json()
    except Exception:
        body = {}
    from starlette.concurrency import run_in_threadpool as _au_rtp
    res = await _au_rtp(_a.authenticate, body.get("employee_id", ""), body.get("password", ""))
    if not res.get("ok"):
        return JSONResponse(res, status_code=401)
    token = _a.make_session(res["employee_id"], res["display_name"], res.get("is_admin", False))
    out = JSONResponse({"ok": True, "redirect": "/",
                        "display_name": res["display_name"],
                        "must_change": res.get("must_change", False)})
    out.set_cookie(_a.COOKIE_NAME, token,
                   max_age=int(_a.session_hours() * 3600),
                   httponly=True,      # page scripts can't read it
                   samesite="lax",
                   path="/")
    # Keep the existing rms_user cookie in step so lock strips, log headers and
    # the audit trail show the real name without any of them needing to change.
    out.set_cookie("rms_user", quote(res["display_name"]),
                   max_age=int(_a.session_hours() * 3600), samesite="lax", path="/")
    return out


@app.post("/auth/change-password")
async def auth_change_password(request: Request):
    """Set your own password. Requires the CURRENT one — so a walk-away machine
    with a live session still can't be used to lock the real owner out.
    On success the roster sheet's status column is updated (never the password
    itself — see core/user_sheet.py for why)."""
    from core import auth as _a
    from core import user_sheet as _sheet
    try:
        body = await request.json()
    except Exception:
        body = {}

    eid = (body.get("employee_id") or "").strip()
    cur = body.get("current_password") or ""
    new = body.get("new_password") or ""

    # Prefer the signed-in identity over anything the page sent us.
    sess = _a.current_user(request)
    if sess and sess.get("sub"):
        eid = sess["sub"]
    if not eid:
        return JSONResponse({"ok": False, "error": "Not signed in."}, status_code=401)

    if len(new) < 8:
        return JSONResponse({"ok": False, "error": "New password must be at least 8 characters."},
                            status_code=400)
    if new.strip().lower() == _sheet.DEFAULT_PASSWORD:
        return JSONResponse({"ok": False, "error": "Choose something other than the default password."},
                            status_code=400)

    chk = _a.authenticate(eid, cur)
    if not chk.get("ok"):
        return JSONResponse({"ok": False, "error": "Current password is incorrect."},
                            status_code=401)

    u = _a.get_user(eid)
    _a.upsert_user(eid, u["display_name"], new, must_change=False)
    sheet_res = _sheet.mark_changed(eid, new_password=new)

    out = JSONResponse({"ok": True, "sheet": sheet_res})
    token = _a.make_session(eid, u["display_name"], u.get("is_admin", False))
    out.set_cookie(_a.COOKIE_NAME, token, max_age=int(_a.session_hours() * 3600),
                   httponly=True, samesite="lax", path="/")
    return out


@app.post("/auth/logout")
def auth_logout():
    from core import auth as _a
    out = JSONResponse({"ok": True})
    out.delete_cookie(_a.COOKIE_NAME, path="/")
    out.delete_cookie("rms_user", path="/")
    return out


@app.get("/auth/me")
def auth_me(request: Request):
    """Who am I, and is sign-in even being enforced? Used by the UI to decide
    whether to show a sign-out button."""
    from core import auth as _a
    u = _a.current_user(request)
    return {"signed_in": bool(u),
            "employee_id": (u or {}).get("sub"),
            "display_name": (u or {}).get("name"),
            "is_admin": bool((u or {}).get("is_admin")),
            "auth_required": _a.auth_required()}


@app.get("/login")
def login_page():
    """The sign-in page (platform/login/). Currently VIEW-ONLY: the /auth/login
    endpoint it posts to does not exist yet, and nothing redirects here or
    requires a session — the console works exactly as before. The page itself
    says so if someone tries to sign in. Enforcement lands together with the
    employee-ID auth backend (planned alongside server deployment)."""
    from fastapi.responses import FileResponse
    _f = os.path.join(_REPO_DIR, "platform", "login", "index.html")
    if os.path.isfile(_f):
        return FileResponse(_f, media_type="text/html")
    return {"error": "login page not found"}


# ── AI Operations Platform read-only data feeds (see core/ops_metrics.py) ─────
# Imported lazily inside each route so a bug there can degrade gracefully to an
# error payload instead of ever preventing the whole server from booting.

@app.get("/ops/summary")
def ops_summary():
    try:
        from core import ops_metrics
        return ops_metrics.summary()
    except Exception as e:
        return {"error": str(e)}

@app.get("/ops/health")
def ops_health():
    try:
        from core import ops_metrics
        return ops_metrics.health()
    except Exception as e:
        return {"error": str(e)}

@app.get("/ops/timeline")
def ops_timeline():
    try:
        from core import ops_metrics
        return ops_metrics.timeline()
    except Exception as e:
        return {"error": str(e), "items": []}

@app.get("/ops/overview")
def ops_overview():
    try:
        from core import ops_metrics
        return ops_metrics.overview()
    except Exception as e:
        return {"error": str(e), "items": []}

# ── LIVE ALLOCATION CAPTURE card (core/live_allocation.py) ───────────────────
def _la_date(s):
    import datetime as _dt
    s = (s or "").strip()
    if not s:
        return _dt.date.today()
    for f in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    raise HTTPException(400, "bad date")

@app.get("/live-alloc/status")
def la_status(date: str = ""):
    from core import live_allocation as _la
    try:
        return _la.status(_la_date(date))
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e), "slots": []}

@app.get("/live-alloc/files")
def la_files(date: str = ""):
    """Every slot of one day with its files (for the Get files window)."""
    from core import live_allocation as _la
    d = _la_date(date)
    st = _la.status(d)
    return {"date": _la.dstr(d), "dates": st.get("dates", []),
            "slots": [{"slot": s["slot"], "time": s["time"], "state": s["state"], "files": _la.list_files(d, s["slot"])}
                      for s in st["slots"]]}

@app.get("/live-alloc/file")
def la_file(slot: int, name: str, date: str = ""):
    from core import live_allocation as _la
    try:
        p = _la.file_path(_la_date(date), slot, name)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "file not found")
    return FileResponse(str(p), filename=p.name)

@app.get("/live-alloc/zip")
def la_zip(date: str = "", slot: int = 0):
    """The whole date folder (1..8 inside, same layout as on disk) - or one slot folder - as one zip."""
    from core import live_allocation as _la
    d = _la_date(date)
    try:
        z = _la.zip_folder(d, slot or None)
    except FileNotFoundError:
        raise HTTPException(404, "no folder for that date")
    nm = f"LIVE_ALLOCATION_{_la.dstr(d)}" + (f"_folder{slot}" if slot else "") + ".zip"
    return FileResponse(str(z), filename=nm, media_type="application/zip")

@app.post("/live-alloc/run")
async def la_run(request: Request):
    """Start one slot by hand (the timer does the same automatically). {slot, force}"""
    import threading as _th
    from core import live_allocation as _la
    body = await request.json()
    try:
        slot = int(body.get("slot"))
        force = bool(body.get("force"))
    except Exception:
        raise HTTPException(400, "slot is required")
    by = (_actor(request)[0] or "")
    if _la.is_running():
        return JSONResponse({"ok": False, "error": "Another capture is running - wait for it, or press Skip / Stop on it."}, status_code=409)
    def _go():
        try:
            _la.run_slot(slot, trigger="manual", by=by, force=force)
        except Exception as e:
            print(f"[live-alloc] manual run of slot {slot} failed: {e}", flush=True)
    _th.Thread(target=_go, name=f"live-alloc-{slot}", daemon=True).start()
    return {"ok": True}

@app.post("/live-alloc/skip")
async def la_skip(request: Request):
    from core import live_allocation as _la
    body = await request.json()
    slot = int(body.get("slot"))
    by = (_actor(request)[0] or "")
    rec = _la.unskip_slot(slot) if body.get("undo") else _la.skip_slot(slot, by=by)
    return {"ok": True, "state": rec.get("status", "")}

@app.post("/live-alloc/stop")
def la_stop():
    from core import live_allocation as _la
    return {"ok": True, "stopped": _la.stop_running()}

@app.on_event("startup")
def _live_alloc_startup():
    """The built-in timer: captures each numbered folder at its time, by itself (no Scheduler schedule needed)."""
    try:
        from core import live_allocation as _la
        _la.start_ticker()
    except Exception as _e:
        print(f"⚠️  LIVE ALLOCATION timer not started: {type(_e).__name__}: {_e}")

@app.get("/live-alloc/settings")
def la_settings_get():
    from core import live_allocation as _la
    s = _la.get_settings()
    return {"times": ",".join(_la.hhmm(m) for m in _la.slot_times()), "auto": s["auto"], "custom": bool(s["times"]),
            "default": _la.DEFAULT_TIMES, "grace_min": _la._grace_min(), "retries": _la._retry_cfg()[0],
            "retry_gap_min": _la._retry_cfg()[1]}

@app.post("/live-alloc/settings")
async def la_settings_set(request: Request):
    """Change the 8 timings and/or the auto switch (asks the console password, like every other settings window).
    Takes effect within ~20 seconds - nothing to restart."""
    from core import live_allocation as _la
    from core.short_allocation import check_password as _cp
    body = await request.json()
    if not _cp(str(body.get("password") or "")):
        return JSONResponse({"ok": False, "error": "Wrong password"}, status_code=200)
    try:
        s = _la.save_settings(times=body.get("times") if "times" in body else None,
                              auto=body.get("auto") if "auto" in body else None, by=(_actor(request)[0] or ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "times": ",".join(_la.hhmm(m) for m in _la.slot_times()), "auto": s["auto"]}

@app.post("/live-alloc/override")
async def la_override(request: Request):
    """Manual override of one folder today: {slot, mode: 'undone' | 'done'}."""
    from core import live_allocation as _la
    from core.short_allocation import check_password as _cp
    body = await request.json()
    if not _cp(str(body.get("password") or "")):
        return {"ok": False, "error": "Wrong password"}
    try:
        slot = int(body.get("slot"))
    except Exception:
        raise HTTPException(400, "slot is required")
    by = (_actor(request)[0] or "")
    if _la.is_running() and (_la.status().get("running") == slot):
        return JSONResponse({"ok": False, "error": "That folder is running now - Stop or Skip it first."}, status_code=409)
    rec = _la.mark_undone(slot, by) if body.get("mode") == "undone" else _la.mark_done(slot, by)
    return {"ok": True, "state": rec.get("status", "")}

from fastapi import File as _LA_File, UploadFile as _LA_UploadFile
from typing import List as _LA_List

@app.post("/live-alloc/upload")
async def la_upload(request: Request, slot: int = 0, files: _LA_List[_LA_UploadFile] = _LA_File(...)):
    """Add files you captured by hand to one of TODAY's numbered folders (same names as the automatic capture).
    Saved in the folder, shown in Backup files, copied to PostgreSQL, and the folder turns green when all 5 are there."""
    from core import live_allocation as _la
    import datetime as _dt
    n = len(_la.slot_times())
    if not 1 <= slot <= max(n, 8):
        return JSONResponse({"ok": False, "error": "Choose the folder number."}, status_code=400)
    if _la.is_running() and _la.status().get("running") == slot:
        return JSONResponse({"ok": False, "error": "That folder is being captured right now - Stop it first."}, status_code=409)
    by = (_actor(request)[0] or "")
    d = _dt.date.today()
    ok, rejected = [], []
    for f in files:
        try:
            r = _la.accept_upload(d, slot, f.filename or "", await f.read(), by=by)
            ok.append(f"{r['name']} ({r['label']})")
        except ValueError as e:
            rejected.append(str(e))
    res = _la.finish_upload(d, slot, by=by) if ok else {"complete": False, "missing": [], "found": {}, "state": ""}
    return {"ok": ok, "rejected": rejected, "slot": slot, "folder": str(_la.slot_dir(d, slot)), **res}

@app.get("/live-alloc/range")
def la_range(from_date: str = "", to_date: str = ""):
    """Every dated folder between two days with its numbered folders and files (for the Backup files window)."""
    from core import live_allocation as _la
    d1, d2 = _la_date(from_date), _la_date(to_date or from_date)
    return {"from": _la.dstr(d1), "to": _la.dstr(d2), "days": _la.range_listing(d1, d2),
            "all_dates": [_la.dstr(x) for x in _la.list_dates()[:120]]}

@app.get("/live-alloc/zip-range")
def la_zip_range(from_date: str = "", to_date: str = "", slots: str = ""):
    """One zip of every folder in a date range (on-disk layout: <date>/<1..8>/<files>). slots='2,3' keeps only those folders."""
    from core import live_allocation as _la
    d1, d2 = _la_date(from_date), _la_date(to_date or from_date)
    want = [int(x) for x in slots.split(",") if x.strip().isdigit()] or None
    try:
        z = _la.zip_range(d1, d2, want)
    except FileNotFoundError:
        raise HTTPException(404, "no files in that range")
    nm = f"LIVE_ALLOCATION_{_la.dstr(d1)}" + (f"_to_{_la.dstr(d2)}" if d1 != d2 else "") + ".zip"
    return FileResponse(str(z), filename=nm, media_type="application/zip")

# ── MCX CASH MAINTENANCE card (core/mcx_cash_maintenance.py) - same shape as /live-alloc/* ──
# `slot` here is the time folder name, e.g. "09-30".
def _mc_key(s):
    from core import mcx_cash_maintenance as _mc
    try:
        return _mc.check_key(s)
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.get("/mcx-cash/status")
def mc_status(date: str = ""):
    from core import mcx_cash_maintenance as _mc
    try:
        return _mc.status(_la_date(date))
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e), "slots": []}

@app.get("/mcx-cash/files")
def mc_files(date: str = ""):
    from core import mcx_cash_maintenance as _mc
    d = _la_date(date)
    st = _mc.status(d)
    return {"date": _mc.dstr(d), "dates": st.get("dates", []),
            "slots": [{"slot": s["slot"], "time": s["time"], "state": s["state"], "files": _mc.list_files(d, s["slot"])}
                      for s in st["slots"]]}

@app.get("/mcx-cash/file")
def mc_file(slot: str, name: str, date: str = ""):
    from core import mcx_cash_maintenance as _mc
    try:
        p = _mc.file_path(_la_date(date), _mc_key(slot), name)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "file not found")
    return FileResponse(str(p), filename=p.name)

@app.get("/mcx-cash/zip")
def mc_zip(date: str = "", slot: str = ""):
    from core import mcx_cash_maintenance as _mc
    d = _la_date(date)
    try:
        z = _mc.zip_folder(d, _mc_key(slot) if slot else None)
    except FileNotFoundError:
        raise HTTPException(404, "no folder for that date")
    nm = f"MCX_CASH_MAINTENANCE_{_mc.dstr(d)}" + (f"_{slot}" if slot else "") + ".zip"
    return FileResponse(str(z), filename=nm, media_type="application/zip")

@app.post("/mcx-cash/run")
async def mc_run(request: Request):
    """Start one time by hand (the timer does the same by itself). {slot:'09-30', force}"""
    import threading as _th
    from core import mcx_cash_maintenance as _mc
    body = await request.json()
    slot = _mc_key(body.get("slot"))
    force = bool(body.get("force"))
    by = (_actor(request)[0] or "")
    if _mc.is_running():
        return JSONResponse({"ok": False, "error": "Another run is going - wait for it, or press Skip / Stop on it."}, status_code=409)
    def _go():
        try:
            _mc.run_slot(slot, trigger="manual", by=by, force=force)
        except Exception as e:
            print(f"[mcx-cash] manual run of {slot} failed: {e}", flush=True)
    _th.Thread(target=_go, name=f"mcx-cash-{slot}", daemon=True).start()
    return {"ok": True}

@app.post("/mcx-cash/skip")
async def mc_skip(request: Request):
    from core import mcx_cash_maintenance as _mc
    body = await request.json()
    slot = _mc_key(body.get("slot"))
    by = (_actor(request)[0] or "")
    rec = _mc.unskip_slot(slot) if body.get("undo") else _mc.skip_slot(slot, by=by)
    return {"ok": True, "state": rec.get("status", "")}

@app.post("/mcx-cash/stop")
def mc_stop():
    from core import mcx_cash_maintenance as _mc
    return {"ok": True, "stopped": _mc.stop_running()}

# ── SHORT ALLOCATION AUTO RUN (runs start by themselves; see core/sa_auto.py) ──────────────────────────────
@app.get("/sa/auto/status")
def sa_auto_status():
    from core import sa_auto as _au
    return {"ok": True, **_au.status()}

class _SAAutoSave(BaseModel):
    password: str
    settings: dict

@app.post("/sa/auto/settings")
def sa_auto_settings(u: _SAAutoSave, request: Request):
    from core import sa_auto as _au
    from core.short_allocation import check_password as _pw
    if not _pw(u.password):
        return {"ok": False, "error": "Wrong password"}
    try:
        who, _adm = _actor(request)
        _au.save_settings(u.settings or {}, who or "")
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **_au.status()}

@app.get("/sa/auto/preview")
def sa_auto_preview(run: int = 5):
    """The picture the WhatsApp failure alert carries (sample wording, marked TEST)."""
    from fastapi.responses import FileResponse, JSONResponse
    from core import sa_auto as _au
    try:
        return FileResponse(str(_au.render_sample(max(1, min(8, run)))), media_type="image/png", headers={"Cache-Control": "no-store"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)

class _SAAutoTest(BaseModel):
    password: str
    run: int = 5

@app.post("/sa/auto/test-alert")
def sa_auto_test_alert(u: _SAAutoTest):
    """Sends the TEST failure-alert picture to the saved FAILURE ALERT numbers, so you can see what they will get."""
    from core import sa_auto as _au
    from core.short_allocation import check_password as _pw
    if not _pw(u.password):
        return {"ok": False, "error": "Wrong password"}
    r = _au.send_alert(max(1, min(8, u.run)), "THIS IS A TEST - nothing has failed. The exchange peak file has not arrived yet: MCX_PeakMargin56720_" + __import__("datetime").date.today().strftime("%Y%m%d") + "_05.csv.",
                       "Waited 30 minutes over 2 retries; the file was still missing. (Test message - no action needed.)", test=True)
    return {"ok": bool(r.get("sent")), "error": "" if r.get("sent") else r.get("reason", "not sent"), "reason": r.get("reason", "")}

@app.post("/sa/auto/skip")
def sa_auto_skip(run: int, undo: bool = False):
    from core import sa_auto as _au
    if not 1 <= run <= 8:
        return {"ok": False, "error": "run must be 1-8"}
    _au.skip_today(run, undo)
    return {"ok": True, **_au.status()}

@app.on_event("startup")
def _sa_auto_startup():
    try:
        from core import sa_auto as _au
        _au.start_ticker()
    except Exception as _e:
        print(f"⚠️  Short Allocation Auto Run timer not started: {type(_e).__name__}: {_e}")


@app.on_event("startup")
def _mcx_cash_startup():
    """The built-in timer: produces and mails the reports at each time, by itself."""
    try:
        from core import mcx_cash_report as _mr
        _mr.apply_defaults()
        from core import mcx_cash_maintenance as _mc
        _mc.start_ticker()
    except Exception as _e:
        print(f"⚠️  MCX CASH MAINTENANCE timer not started: {type(_e).__name__}: {_e}")

@app.get("/mcx-cash/settings")
def mc_settings_get():
    from core import mcx_cash_maintenance as _mc
    s = _mc.get_settings()
    st = _mc.status()
    return {"times": ",".join(_mc.hhmm(m) for m in _mc.slot_times()), "auto": s["auto"], "custom": bool(s["times"]),
            "default": _mc.DEFAULT_TIMES, "grace_min": _mc._grace_min(), "retries": _mc._retry_cfg()[0],
            "retry_gap_min": _mc._retry_cfg()[1],
            "slots": [{"slot": x["slot"], "time": x["time"], "mode": x["mode"], "ms15_run": x["ms15_run"]} for x in st["slots"]],
            "ms15_runs": st["ms15_runs"], "ms15_override": s["ms15_runs"]}

@app.post("/mcx-cash/settings")
async def mc_settings_set(request: Request):
    """Timings / auto switch / where each time takes its files from (password-gated). Applies within ~20 seconds."""
    from core import mcx_cash_maintenance as _mc
    from core.short_allocation import check_password as _cp
    body = await request.json()
    if not _cp(str(body.get("password") or "")):
        return JSONResponse({"ok": False, "error": "Wrong password"}, status_code=200)
    try:
        s = _mc.save_settings(times=body.get("times") if "times" in body else None,
                              auto=body.get("auto") if "auto" in body else None,
                              modes=body.get("modes") if "modes" in body else None,
                              ms15_runs=body.get("ms15_runs") if "ms15_runs" in body else None,
                              by=(_actor(request)[0] or ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "times": ",".join(_mc.hhmm(m) for m in _mc.slot_times()), "auto": s["auto"]}

@app.post("/mcx-cash/override")
async def mc_override(request: Request):
    """Manual override of one time today: {slot, mode: 'undone' | 'done'}."""
    from core import mcx_cash_maintenance as _mc
    from core.short_allocation import check_password as _cp
    body = await request.json()
    if not _cp(str(body.get("password") or "")):
        return {"ok": False, "error": "Wrong password"}
    slot = _mc_key(body.get("slot"))
    by = (_actor(request)[0] or "")
    if _mc.is_running() and (_mc.status().get("running") == slot):
        return JSONResponse({"ok": False, "error": "That time is running now - Stop or Skip it first."}, status_code=409)
    rec = _mc.mark_undone(slot, by) if body.get("mode") == "undone" else _mc.mark_done(slot, by)
    return {"ok": True, "state": rec.get("status", "")}

@app.post("/mcx-cash/upload")
async def mc_upload(request: Request, slot: str = "", files: _LA_List[_LA_UploadFile] = _LA_File(...)):
    """Add files you got by hand to one of TODAY's time folders (the 4 inputs, recognised by their content)."""
    from core import mcx_cash_maintenance as _mc
    import datetime as _dt
    key = _mc_key(slot)
    if _mc.is_running() and _mc.status().get("running") == key:
        return JSONResponse({"ok": False, "error": "That time is running right now - Stop it first."}, status_code=409)
    by = (_actor(request)[0] or "")
    d = _dt.date.today()
    ok, rejected = [], []
    for f in files:
        try:
            r = _mc.accept_upload(d, key, f.filename or "", await f.read(), by=by)
            ok.append(f"{r['name']} ({r['label']})")
        except ValueError as e:
            rejected.append(str(e))
    res = _mc.finish_upload(d, key, by=by) if ok else {"inputs_complete": False, "missing": [], "found": {}}
    return {"ok": ok, "rejected": rejected, "slot": key, "folder": str(_mc.slot_dir(d, key)), **res}

@app.get("/mcx-cash/range")
def mc_range(from_date: str = "", to_date: str = ""):
    from core import mcx_cash_maintenance as _mc
    d1, d2 = _la_date(from_date), _la_date(to_date or from_date)
    return {"from": _mc.dstr(d1), "to": _mc.dstr(d2), "days": _mc.range_listing(d1, d2),
            "all_dates": [_mc.dstr(x) for x in _mc.list_dates()[:120]]}

@app.get("/mcx-cash/zip-range")
def mc_zip_range(from_date: str = "", to_date: str = "", slots: str = ""):
    from core import mcx_cash_maintenance as _mc
    d1, d2 = _la_date(from_date), _la_date(to_date or from_date)
    want = [x.strip() for x in slots.split(",") if x.strip()] or None
    try:
        z = _mc.zip_range(d1, d2, want)
    except ValueError:
        raise HTTPException(400, "bad time folder in slots")
    except FileNotFoundError:
        raise HTTPException(404, "no files in that range")
    nm = f"MCX_CASH_MAINTENANCE_{_mc.dstr(d1)}" + (f"_to_{_mc.dstr(d2)}" if d1 != d2 else "") + ".zip"
    return FileResponse(str(z), filename=nm, media_type="application/zip")

@app.get("/ops/history")
def ops_history(days: int = 7):
    try:
        from core import ops_metrics
        return {"items": ops_metrics.history(days)}
    except Exception as e:
        return {"error": str(e), "items": []}

@app.get("/ops/brief")
def ops_brief(refresh: int = 0):
    try:
        from core import ops_metrics
        return ops_metrics.brief(refresh=bool(refresh))
    except Exception as e:
        return {"error": str(e), "text": "Briefing unavailable.", "ai": False}


# ── Phase 2: file explorer / download center / AI assistant (all read-only) ──

@app.get("/ops/file-roots")
def ops_file_roots():
    try:
        from core import ops_metrics
        return {"roots": ops_metrics.file_roots()}
    except Exception as e:
        return {"error": str(e), "roots": []}

@app.get("/ops/files")
def ops_files(root: str = "", sub: str = "", q: str = ""):
    try:
        from core import ops_metrics
        return ops_metrics.files_list(root, sub, q)
    except Exception as e:
        return {"error": str(e), "items": []}

@app.get("/ops/file/preview")
def ops_file_preview(root: str = "", sub: str = ""):
    try:
        from core import ops_metrics
        return ops_metrics.file_preview(root, sub)
    except Exception as e:
        return {"error": str(e)}

@app.get("/ops/file/get")
def ops_file_get(root: str = "", sub: str = ""):
    """Download one file. Path safety lives in ops_metrics.file_abspath():
    whitelisted roots only, realpath-prefix checked — raw paths are never
    accepted from the client."""
    try:
        from core import ops_metrics
        fp = ops_metrics.file_abspath(root, sub)
        if not fp:
            return {"error": "File not found or outside allowed folders"}
        return FileResponse(fp, filename=os.path.basename(fp),
                            headers={"Cache-Control": "no-store"})
    except Exception as e:
        return {"error": str(e)}

@app.get("/ops/downloads")
def ops_downloads(days: int = 3):
    try:
        from core import ops_metrics
        return ops_metrics.downloads_index(days)
    except Exception as e:
        return {"error": str(e), "items": []}

# NOTE: pydantic imported here directly — the shared _SA_BaseModel alias is
# only defined much further down this file, after these routes execute at
# import time; referencing it here would crash the server at boot.
from pydantic import BaseModel as _OpsBaseModel

class _OpsAsk(_OpsBaseModel):
    question: str
    logdir: str = ""
    file: str = ""

@app.post("/ops/ask")
def ops_ask(a: _OpsAsk):
    try:
        from core import ops_metrics
        ref = {"logdir": a.logdir, "file": a.file} if a.file else None
        return ops_metrics.ask(a.question, log_ref=ref)
    except Exception as e:
        return {"error": str(e), "answer": "Assistant unavailable: " + str(e), "ai": False}


# ── GLOBAL PROCESS LOCKING (core/lock_manager.py) ────────────────────────────
# The server is the single source of truth for what is running. Lock state is
# DERIVED from the real execution state (_PB/_SA/MCX file lock) so it can never
# go stale; this layer adds who/where metadata, an audit trail, and a live SSE
# broadcast every connected browser subscribes to.

@app.middleware("http")
async def _lock_identity_middleware(request, call_next):
    """Notes WHO and from WHERE issued each run-starting request, so the lock a
    pipeline claims moments later — and the log file it writes — can both be
    attributed. Read-only; never blocks a request.

    Prefers the SIGNED session identity (a real sign-in, cannot be forged
    client-side) and only falls back to the free-text rms_user cookie, which
    anyone can type into. Same 'name (employee_id)' format _actor()/_can_stop()
    and /px/run-stream use, so a log header, a lock strip and a Stop-ownership
    check all show and compare the same string."""
    try:
        path = request.url.path
        _ip0 = request.client.host if request.client else ""
        if not path.startswith("/platform/"):
            # remember who is signed in on this PC, so a later request from it without a live session
            # (expired cookie, other tab) is still attributed to that person instead of "unknown user"
            try:
                from core.auth import current_user as _cu3
                from core import lock_manager as _lm3
                _u3 = _cu3(request)
                if _u3:
                    _lm3.remember_ip(_ip0, f"{_u3.get('name') or _u3.get('sub')} ({_u3.get('sub')})")
            except Exception:
                pass
        if path.endswith("-stream") or path.endswith("/run-stream"):
            from core import lock_manager as _lm
            from urllib.parse import unquote
            _who = ""
            try:
                from core.auth import current_user as _cu2
                _u2 = _cu2(request)
                if _u2:
                    _who = f"{_u2.get('name') or _u2.get('sub')} ({_u2.get('sub')})"
            except Exception:
                pass
            if not _who:
                _who = unquote(request.cookies.get("rms_user", ""))
            if not _who:
                _who = _lm.ip_identity(_ip0)
            _lm.note_request(_who, _ip0, path)
    except Exception:
        pass
    return await call_next(request)


@app.get("/ops/locks")
def ops_locks():
    try:
        from core import lock_manager
        return lock_manager.snapshot()
    except Exception as e:
        return {"error": str(e), "locks": []}


@app.get("/ops/locks/stream")
async def ops_locks_stream():
    """SSE: pushes the lock snapshot whenever it changes (checked every second)
    plus a 15s heartbeat. Every open browser — platform pages AND the embedded
    Process Center overlay — stays in sync without refreshing."""
    import asyncio as _aio

    async def gen():
        last = None
        beat = 0
        while True:
            try:
                from core import lock_manager
                from starlette.concurrency import run_in_threadpool as _lk_rtp
                snap = await _lk_rtp(lock_manager.snapshot)
                # elapsed_s changes every second; compare only the identity part
                sig = json.dumps([{k: l.get(k) for k in ("key", "state", "pipe", "by", "started_at")}
                                   for l in snap["locks"]], sort_keys=True)
            except Exception:
                snap, sig = {"locks": []}, "err"
            beat += 1
            if sig != last or beat >= 15:
                last = sig
                beat = 0
                yield f"data: {json.dumps(snap)}\n\n"
            await _aio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@app.get("/ops/audit")
def ops_audit(limit: int = 120):
    try:
        from core import lock_manager
        return {"events": lock_manager.read_audit(limit)}
    except Exception as e:
        return {"error": str(e), "events": []}


# ── PHASE 3: KNOWLEDGE ENGINE (core/knowledge.py — background SQLite index) ──

@app.get("/kb/status")
def kb_status():
    try:
        from core import knowledge
        return knowledge.status()
    except Exception as e:
        return {"error": str(e)}

@app.get("/kb/search")
def kb_search(q: str = "", limit: int = 24):
    try:
        from core import knowledge
        return knowledge.search(q, limit)
    except Exception as e:
        return {"error": str(e), "hits": []}

@app.get("/kb/client")
def kb_client(code: str = ""):
    try:
        from core import knowledge
        return knowledge.find_client(code)
    except Exception as e:
        return {"error": str(e), "files": [], "total": 0}

@app.get("/kb/processes")
def kb_processes(pipe: str = ""):
    try:
        from core import knowledge
        return knowledge.process_kb(pipe or None)
    except Exception as e:
        return {"error": str(e)}

@app.get("/kb/insights")
def kb_insights():
    try:
        from core import knowledge
        return knowledge.insights()
    except Exception as e:
        return {"error": str(e), "items": []}

@app.post("/kb/reindex")
def kb_reindex():
    """Kick an immediate incremental re-scan (normally runs every 15 min)."""
    try:
        from core import knowledge
        import threading as _th
        knowledge.ensure_indexer()
        _th.Thread(target=knowledge._cycle, daemon=True).start()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/ask")
def ask(q: Question):
    return answer_compliance(q.question)


@app.post("/agent")
def agent(g: Goal):
    return run_agent_traced(g.goal)


@app.get("/peak-margin-stream/{index}")
def peak_margin_stream(index: str):
    """Stream the run live as Server-Sent Events. Each line is flushed as it
    happens. At the end, emits a 'done' (or 'error') event with the result,
    and handles AUTO_SEND."""
    def gen():
        final = None
        for ev in process_pair_stream(index):
            yield f"data: {json.dumps(ev)}\n\n"
            if ev["type"] in ("done", "error"):
                final = ev

        # After the run: if computed and AUTO_SEND, send now and stream the result.
        if final and final.get("type") == "done" and final.get("status") == "computed":
            if AUTO_SEND:
                try:
                    out = send_peak_margin_report(FOLDER)
                    msg = ("📤 Auto-send ON — report dispatched."
                           if out.get("ok") else "⚠️ Auto-send failed — see detail.")
                    yield f"data: {json.dumps({'type':'log','msg':msg})}\n\n"
                    yield f"data: {json.dumps({'type':'send','auto':True,'detail':out})}\n\n"
                    # Cleanup ONLY after a successful send, and only on the final run.
                    if out.get("ok") and final.get("is_final"):
                        deleted = cleanup_after_send()
                        yield f"data: {json.dumps({'type':'log','msg':f'🧹 Cleaned up {len(deleted)} downloaded data files (images kept).'})}\n\n"
                    elif final.get("is_final") and not out.get("ok"):
                        yield f"data: {json.dumps({'type':'log','msg':'⚠️ Send failed — keeping downloaded files for retry (no cleanup).'})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type':'log','msg':f'🔥 Auto-send error: {e}'})}\n\n"
            else:
                token = uuid.uuid4().hex
                PENDING[token] = {"index": index, "is_final": final.get("is_final", False)}
                yield f"data: {json.dumps({'type':'pending','token':token})}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # tell proxies (nginx) not to buffer
            "Connection": "keep-alive",
        },
    )


@app.post("/approve-send")
def approve_send(a: Approval):
    pend = PENDING.get(a.token)
    if not pend:
        return {"status": "rejected", "reason": "invalid or already-used token"}
    is_final = pend.get("is_final", False)
    del PENDING[a.token]
    try:
        out = send_peak_margin_report(FOLDER)
        summary = _send_summary(out)
        # Cleanup only after a successful send, and only on the final (i04) run.
        if out.get("ok") and is_final:
            deleted = cleanup_after_send()
            summary["cleanup"] = f"Removed {len(deleted)} downloaded files (images kept)."
        return summary
    except Exception as e:
        return {"status": "error", "message": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-500:]}


def _send_summary(out):
    """Turn the detailed send result into a clear summary with reasons."""
    def reasons(block):
        if block.get("ok"):
            return "OK"
        if "error" in block:
            return block["error"]
        # per-recipient detail
        bad = [f"{r['to']}: {r.get('detail')}" for r in block.get("results", []) if not r.get("ok")]
        return "; ".join(bad) if bad else "failed"
    summary = {
        "status": "sent" if out.get("ok") else "error",
        "whatsapp_fo": reasons(out["wa_fo"]),
        "whatsapp_cm": reasons(out["wa_cm"]),
        "email": reasons(out["email"]),
        "detail": out,
    }
    if not out.get("ok"):
        summary["message"] = "Nothing sent. See the per-channel reasons."
    return summary


@app.post("/run/{index}")
def run_for_scheduler(index: str):
    """SIMPLE endpoint for N8N / schedulers: run one snapshot, do everything
    (download -> compute -> images -> send if AUTO_SEND), return a final result
    N8N can branch on. No streaming - just a clean success/failure.

    Always returns HTTP 200 with a 'status' field, so N8N can read the outcome
    and decide whether to alert/retry based on the body."""
    result = process_pair(index)            # runs the whole pipeline (no UI stream)
    status = result.get("status")

    if status != "computed":
        return {"ok": False, "status": status, "index": index,
                "message": result.get("error") or "run did not complete",
                "messages": result.get("messages", [])}

    sent = None
    if AUTO_SEND:
        try:
            out = send_peak_margin_report(FOLDER)
            sent = out.get("ok", False)
            # clean up only after a successful send, only on final run
            if sent and result.get("is_final"):
                cleanup_after_send()
        except Exception as e:
            return {"ok": False, "status": "send_error", "index": index,
                    "message": f"{type(e).__name__}: {e}"}

    return {"ok": True, "status": "computed", "index": index,
            "captured": result.get("ran_label"),
            "sent": sent,
            "message": f"{index} processed" + (" and sent" if sent else "")}


@app.get("/concentration-download-stream")
def concentration_download_stream():
    """Stream the file-download step live (INDEX files + KORP shortage)."""
    def gen():
        for ev in stream_download():
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})


@app.get("/concentration-compute-stream")
def concentration_compute_stream():
    """Stream the compute + email + backup step live."""
    def gen():
        for ev in stream_compute():
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})


@app.post("/run/concentration")
def run_concentration_for_scheduler():
    """Simple endpoint for scheduler/N8N: run full concentration check, return result."""
    result = process_concentration()
    if result.get("status") != "computed":
        return {"ok": False, "status": result.get("status"),
                "message": result.get("error", "check did not complete")}
    r = result.get("result", {})
    return {"ok": True, "status": "computed",
            "violations": r.get("violations", 0),
            "unique_accounts": r.get("unique_accounts_in_violation", 0),
            "message": f"Concentration check done. {r.get('violations',0)} violations."}


@app.get("/ctcl-vs-nmass-stream")
def ctcl_vs_nmass_stream():
    """Stream the CTCL vs NMASS run live (compute + zone emails + TOP20 + payout + backup)."""
    def gen():
        for ev in stream_ctcl_vs_nmass():
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})


@app.get("/cnc-f11-stream")
def cnc_f11_stream():
    """Stream the CNC F11 run live (PositionBook → Sheet4 → email → backup)."""
    def gen():
        for ev in stream_cnc_f11():
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})


@app.get("/mtf-order-stream")
def mtf_order_stream():
    """Stream the MTF Order File run live (matching → order files → zone emails → backup)."""
    def gen():
        for ev in stream_mtf_order():
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})


@app.get("/ftp-download-stream")
def ftp_download_stream():
    """Stream the daily FTP file download + distribution live."""
    def gen():
        for ev in stream_ftp_download():
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})


@app.get("/payout-inspection-daily-stream")
def payout_inspection_daily_stream():
    def gen():
        for ev in stream_daily():
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})


@app.get("/payout-inspection-client-stream")
def payout_inspection_client_stream(clients: str = "", fy: str = ""):
    def gen():
        for ev in stream_client(clients, fy):
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})


@app.get("/files-downloader-70-stream")
def files_downloader_70_stream(trade_date: str = ""):
    from datetime import datetime as _dt
    if not trade_date:
        trade_date = _dt.today().strftime("%Y-%m-%d")
    def gen():
        for ev in stream_files_downloader_70(trade_date):
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})


@app.get("/query-file")
def query_file(pipeline: str, group_by: str = "", filter_col: str = "", filter_contains: str = "",
                     sort_by: str = "", sort_order: str = "desc", top_n: int = 0,
                     lookup_col: str = "", lookup_value: str = ""):
    """
    Pure-pandas data query — NO LLM. Returns exact counts/sums.
    - pipeline: which folder (ctcl, payout, concentration, ...)
    - group_by: optional column to group counts by (e.g. 'Zone', 'REMARKS')
    - filter_col + filter_contains: optional row filter (e.g. REMARKS contains SHORTFALL)
    Returns the file read, total rows, and the computed breakdown — guaranteed exact.
    """
    import pandas as pd
    from core.agent import _find_output_file, FOLDER_MAP
    folder = FOLDER_MAP.get(pipeline)
    if not folder or not os.path.isdir(folder):
        return {"error": f"Folder not found for pipeline '{pipeline}'."}
    fpath, display = _find_output_file(folder, pipeline, "latest")
    if fpath is None:
        return {"error": display}
    try:
        if fpath.lower().endswith(".csv"):
            df = pd.read_csv(fpath, low_memory=False)
        else:
            df = pd.read_excel(fpath)
    except Exception as e:
        return {"error": f"Could not read {display}: {e}"}

    def find_col(name):
        for c in df.columns:
            if name.lower() == str(c).lower().strip():
                return c
        for c in df.columns:
            if name.lower() in str(c).lower():
                return c
        return None

    result = {"file_read": display, "total_rows": int(len(df)), "columns": list(df.columns)}
    work = df

    # optional row filter
    if filter_col and filter_contains:
        fc = find_col(filter_col)
        if fc:
            mask = work[fc].astype(str).str.upper().str.contains(filter_contains.upper(), na=False)
            work = work[mask]
            result["filter_applied"] = f"{fc} contains '{filter_contains}'"
            result["rows_after_filter"] = int(len(work))

    # ── LOOKUP mode — find a specific row (e.g. one account) and return EVERYTHING ──
    if lookup_col and lookup_value:
        lc = find_col(lookup_col)
        if not lc:
            result["error"] = f"Column '{lookup_col}' not found. Available: {list(df.columns)}"
            return result
        mask = work[lc].astype(str).str.strip().str.upper() == lookup_value.strip().upper()
        matched = work[mask]
        if len(matched) == 0:
            # try a contains match as fallback
            mask = work[lc].astype(str).str.upper().str.contains(lookup_value.strip().upper(), na=False)
            matched = work[mask]
        result["lookup"] = f"{lc} = '{lookup_value}'"
        result["matches_found"] = int(len(matched))
        # return full rows as a list of dicts (all columns) — capped at 20 rows
        records = []
        for _, row in matched.head(20).iterrows():
            records.append({str(k): (None if pd.isna(v) else
                            (round(float(v),2) if isinstance(v,(int,float)) else str(v)))
                            for k, v in row.items()})
        result["records"] = records
        return result

    # ── TOP-N mode — sort by a numeric column and return the biggest/smallest ──
    if sort_by:
        sc = find_col(sort_by)
        if not sc:
            result["error"] = f"Column '{sort_by}' not found. Available: {list(df.columns)}"
            return result
        tmp = work.copy()
        tmp["_sortval"] = pd.to_numeric(tmp[sc], errors="coerce")
        tmp = tmp.dropna(subset=["_sortval"])
        ascending = (sort_order.lower() == "asc")
        tmp = tmp.sort_values("_sortval", ascending=ascending)
        n = top_n if top_n > 0 else 10
        result["sorted_by"] = sc
        result["order"] = "ascending" if ascending else "descending"
        # which columns to show: account-ish + the sort col + zone + remarks
        show = []
        for want in ["ACCOUNTID","Account ID","Client Code","ClientID","NAME","Zone","REMARKS"]:
            c = find_col(want)
            if c and c not in show:
                show.append(c)
        if sc not in show:
            show.append(sc)
        rows = []
        for _, row in tmp.head(n).iterrows():
            rows.append({str(k): (None if pd.isna(row[k]) else
                         (round(float(row[k]),2) if isinstance(row[k],(int,float)) else str(row[k])))
                         for k in show})
        result["top_rows"] = rows
        return result

    # optional grouping
    if group_by:
        gc = find_col(group_by)
        if gc:
            vc = work[gc].fillna("(blank)").astype(str).str.strip().value_counts()
            result["group_by"] = gc
            result["counts"] = {str(k): int(v) for k, v in vc.items()}
        else:
            result["error"] = f"Column '{group_by}' not found. Available: {list(df.columns)}"
    else:
        result["matched_count"] = int(len(work))

    return result


@app.get("/ask-data")
def ask_data_endpoint(pipeline: str, question: str):
    """
    Plain-English data question -> exact pandas answer (text-to-query).
    The LLM only translates the question into a query spec; pandas computes.
    Returns the computed facts + how the question was understood.
    """
    import pandas as pd
    from core.agent import _find_output_file, _translate_to_query, _execute_query, FOLDER_MAP
    folder = FOLDER_MAP.get(pipeline)
    if not folder or not os.path.isdir(folder):
        return {"error": f"Folder not found for pipeline '{pipeline}'."}
    fpath, display = _find_output_file(folder, pipeline, "latest")
    if fpath is None:
        return {"error": display}
    try:
        df = (pd.read_csv(fpath, low_memory=False) if fpath.lower().endswith(".csv")
              else pd.read_excel(fpath))
    except Exception as e:
        return {"error": f"Could not read {display}: {e}"}
    spec = _translate_to_query(question, list(df.columns))
    if spec.get("_error"):
        return {"error": spec["_error"]}
    result = _execute_query(df, spec)
    result["file_read"] = display
    result["understood_as"] = spec
    return result


@app.get("/indexed-code")
def indexed_code():
    """List every script the agent can read — for verifying code coverage."""
    try:
        from core.code_index import list_indexed_code
        return list_indexed_code()
    except Exception as e:
        return {"error": f"Code index unavailable: {e}"}


@app.post("/rebuild-code-index")
def rebuild_code_index():
    """Force re-scan of CODE_FOLDERS (after adding/editing scripts)."""
    try:
        from core.code_index import rebuild_index
        return rebuild_index()
    except Exception as e:
        return {"error": f"Rebuild failed: {e}"}


@app.get("/daily-summary")
def daily_summary():
    """RMS daily summary — counts from today's real output files (no LLM, no guessing)."""
    try:
        from core.daily_summary import generate_daily_summary
        return generate_daily_summary()
    except Exception as e:
        return {"error": f"Daily summary failed: {e}"}


@app.get("/folder-readiness")
def folder_readiness():
    """Which pipelines can run RIGHT NOW — required input files present today?
    Pure filesystem checks, no LLM. Powers the dashboard readiness card."""
    try:
        from core.readiness import check_all
        return check_all()
    except Exception as e:
        return {"error": f"Readiness check failed: {e}"}


@app.get("/agent-log")
def agent_log(n: int = 50):
    """Last n agent Q&A entries from logs/agent_log.jsonl (newest first)."""
    import json as _j
    log_path = os.path.join(
        os.getenv("AGENT_LOG_DIR", os.path.join(os.getcwd(), "logs")),
        "agent_log.jsonl")
    if not os.path.exists(log_path):
        return {"entries": [], "note": "No agent activity logged yet."}
    try:
        lines = open(log_path, encoding="utf-8", errors="replace").read().splitlines()
        entries = []
        for ln in reversed(lines[-max(1, min(n, 500)):]):
            try:
                entries.append(_j.loads(ln))
            except Exception:
                continue
        return {"entries": entries, "total_logged": len(lines)}
    except Exception as e:
        return {"error": f"Could not read log: {e}"}


@app.post("/upload-file")
def upload_file(file: UploadFile = File(...)):
    """Receive a file from the browser and save it to the uploads folder."""
    import shutil
    uploads = os.getenv("UPLOADS_FOLDER", r"D:\\CLAUDE\\rms-copilot\\uploads")
    os.makedirs(uploads, exist_ok=True)
    safe_name = os.path.basename(file.filename)   # strip any path
    dest = os.path.join(uploads, safe_name)
    try:
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
        return {"status": "saved", "filename": safe_name}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/agent-chat-stream")
async def agent_chat_stream(request: Request):
    """
    Multi-turn streaming agent. Body: {"message": str, "history": list}.
    Streams SSE events: tool_start, tool_done, answer, error, end.
    history is the list of prior turns returned by the previous call.
    """
    body = await request.json()
    user_message = body.get("message", "")
    history      = body.get("history", [])

    def gen():
        for ev in stream_agent(user_message, history):
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"end\"}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache",
                                      "X-Accel-Buffering":"no",
                                      "Connection":"keep-alive"})


@app.get("/report-image/{name}")
def report_image(name: str):
    if name not in ("F_filtered.png", "C_filtered.png"):
        return {"error": "not allowed"}
    path = os.path.join(FOLDER, name)
    if not os.path.exists(path):
        return {"error": "image not generated yet"}
    return FileResponse(path, media_type="image/png")


# === SHORT ALLOCATION ENDPOINTS ===
import json as _sa_json, datetime as _sa_dt, os as _sa_os, tempfile as _sa_tmp
from typing import List as _SA_List
from pydantic import BaseModel as _SA_BaseModel
from fastapi import UploadFile as _SA_UploadFile, File as _SA_File, Form as _SA_Form
from fastapi.responses import StreamingResponse as _SA_Stream
from core.short_allocation import (
    stream_short_allocation as _sa_stream, cfg as _sa_cfg, read_run_state as _sa_rrs,
    get_config as _sa_get_config, check_password as _sa_check_pw, update_env as _sa_update_env,
    move_uploaded as _sa_move, list_logs as _sa_list_logs, read_log as _sa_read_log,
    stop_short_allocation as _sa_stop,
    get_credentials as _sa_get_creds,
    list_backups as _sa_list_backups, copy_backups as _sa_copy_backups, browse_folder as _sa_browse,
    get_all_run_status as _sa_all_status, check_sa_readiness as _sa_readiness,
    save_uploaded_bytes as _sa_save_bytes,
    client_peak_lookup as _sa_client_peak_lookup,
    housekeep_common_files as _sa_housekeep_common,
)

@app.post("/sa/mark-run-done")
def sa_mark_run_done(run: int = 0, password: str = ""):
    """Manually mark an SA-0N run as done — the Short Allocation equivalent of
    /px/advance-run (ctcl/ms15/cli/probable), for when a run genuinely
    completed OUTSIDE this dashboard (e.g. run by hand on another machine).

    SA tracks "done" differently from the other pipes: not a simple
    run_counter.txt, but a per-run outcome record in short_allocation's
    _run_status.json — saRenderRuns() (dashboard.html) unlocks the next tile
    when `code2==='done' || overall==='done'` for the previous SA-0N, so this
    writes exactly those two fields via the same _record() helper the real
    pipeline itself uses to mark a run done.

    Does NOT touch any files or re-run anything — only changes which tile the
    dashboard considers "next". Password-gated with the SAME SA_CONFIG_PASSWORD
    as the 🔑 Credentials button, checked server-side so it can't be bypassed
    by calling this endpoint directly."""
    from core.short_allocation import check_password as _smr_pw, _record as _smr_rec
    if not _smr_pw(password):
        return {"ok": False, "error": "Wrong password"}
    if run not in (1, 2, 3, 4, 5, 6, 7, 8):
        return {"ok": False, "error": "run must be 1 to 8"}
    tag = f"SA-0{run}"
    rec = _smr_rec(tag, overall="done", code2="done")
    return {"ok": True, "tag": tag, "record": rec}


@app.get("/sa/status")
def sa_status():
    c = _sa_cfg()
    # SA is independent of ctcl/ms15/cli — its main SA-01..04 cycle doesn't call
    # sa_codes/mcx_deposit_downloader.py at all and shares no files with them
    # (see core/short_allocation.py's stream_short_allocation docstring note),
    # so it never needs to report THEIR busy state and shouldn't grey out its
    # own Run buttons because e.g. CTCL vs NMASS is running elsewhere.
    # server_busy* fields are kept for API-shape compatibility with older
    # dashboard.html builds that may still read them; they now always reflect
    # "nothing outside SA blocks SA" rather than a stale cross-pipe signal.
    return {"windows": c["windows"], "run_state": _sa_rrs(c["state"]),
            "now": _sa_dt.datetime.now().strftime("%H:%M"),
            "runs": _sa_all_status(),
            "server_busy": False, "server_busy_pipe": None,
            "server_busy_label": None, "server_busy_since": None}

@app.get("/sa/readiness")
def sa_readiness(target: str = "short_allocation"):
    return _sa_readiness(target)

@app.get("/sa/common-housekeep")
def sa_common_housekeep():
    """Auto-backup stale COMMON FILES (yesterday's RISK_REPORT etc.) to a dated
    backup sub-folder inside C:\\COMMON FILES and archive to Postgres.
    Called by the upload modal on open so stale files are cleaned up before the
    user uploads a new one."""
    try:
        moved = _sa_housekeep_common()
        return {"ok": True, "moved": moved, "count": len(moved)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/sa/run-stream")
def sa_run_stream(request: Request, run: str = "01", date: str = "", force: bool = False, skip_las: bool = False, las_only: bool = False, download_only: bool = False):
    override = date.strip() if date else None
    _by = None
    try:
        from core.auth import current_user as _cu
        _u = _cu(request)
        if _u:
            _by = f"{_u.get('name') or _u.get('sub')} ({_u.get('sub')})"
        else:
            from urllib.parse import unquote as _uq
            _by = _uq(request.cookies.get("rms_user", "")).strip() or None
    except Exception:
        _by = None
    _ip = request.client.host if request.client else ""

    # ── server-side double-run guard ─────────────────────────────────────────
    # SA never had the atomic claim ctcl/ms15/cli got in _PB_LOCK — two users on
    # different machines could both click SA-01 and silently start TWO SA runs
    # (interleaving downloads, duplicate emails/WhatsApp). The server is the
    # source of truth: reject the second request with a clear, attributed
    # message. (No queueing by design — SA runs are bound to time-of-day
    # windows; auto-firing a duplicate later would send wrong-time reports.)
    from core.short_allocation import _SA as _sa_flags
    if _sa_flags.get("running"):
        try:
            from core import lock_manager as _lm
            _who = _lm.lock_line("sa")
            _lm.on_reject("sa", "Short Allocation")
        except Exception:
            _who = ""
        def busy_gen():
            yield f"data: {_sa_json.dumps({'type': 'error', 'msg': '⚠ Short Allocation is already running right now — wait for it to finish, or Stop it, before starting a new run.' + _who})}\n\n"
            yield 'data: {"type":"end"}\n\n'
        return _SA_Stream(busy_gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    _sa_clear_popup_state()  # drop any stale unanswered captcha/token from a previous stopped/crashed run
    def gen():
        try:
            from core import lock_manager as _lm2
            _lm2.on_start("sa", f"Short Allocation SA-{run}", pipe="sa", by=_by, ip=_ip)
        except Exception:
            pass
        _sa_outcome = "finished"
        try:
            for ev in _sa_stream(run, override_date=override, force=force, skip_las=skip_las, las_only=las_only, download_only=download_only):
                if isinstance(ev, dict):
                    if ev.get("type") == "stopped":
                        _sa_outcome = "stopped"
                    elif ev.get("type") == "done":
                        _sa_outcome = "completed" if ev.get("ok") else "failed"
                yield f"data: {_sa_json.dumps(ev)}\n\n"
            yield 'data: {"type":"end"}\n\n'
        finally:
            try:
                from core import lock_manager as _lm3
                _lm3.on_end("sa", _sa_outcome)
            except Exception:
                pass
    return _SA_Stream(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

@app.get("/sa/config")
def sa_config_get():
    return _sa_get_config()

class _SAConfig(_SA_BaseModel):
    password: str
    updates: dict

@app.post("/sa/config")
def sa_config_set(u: _SAConfig):
    if not _sa_check_pw(u.password):
        return {"ok": False, "error": "Wrong password"}
    try:
        _sa_update_env(u.updates)
    except ValueError as _e:
        return {"ok": False, "error": str(_e)}
    return {"ok": True}

@app.get("/sa/logs")
def sa_logs():
    return {"logs": _sa_list_logs()}

@app.get("/sa/log")
def sa_log(name: str):
    return {"name": name, "content": _sa_read_log(name)}

import asyncio as _sa_asyncio
_SA_WRITE_TIMEOUT_S = 30  # timeout for the DISK-WRITE phase only (not body read)

@app.post("/sa/upload")
async def sa_upload(target: str = "short_allocation", password: str = "",
                    files: _SA_List[_SA_UploadFile] = _SA_File(...),
                    password_form: str = _SA_Form("", alias="password")):
    # The dashboard sends the password inside the multipart BODY (a plain `password: str` parameter is read from the
    # URL, so it always arrived blank and the "Replace protected file" prompt just came back again and again).
    password = password or password_form
    orig_names = [_sa_os.path.basename(f.filename) for f in files]

    # ── Phase 1: read HTTP body into memory ─────────────────────────────────
    # This MUST NOT be inside wait_for. If the timeout cancels a body-read,
    # uvicorn drops the TCP connection before sending any response → browser
    # sees "Failed to fetch" instead of a helpful error message.
    try:
        items = [(f.filename, await f.read()) for f in files]
    except Exception as e:
        return {"dest": "", "label": target, "ok": [], "skipped": [],
                "rejected": [f"{n}: could not read upload: {e}" for n in orig_names],
                "valid": [], "log": None}

    # ── Phase 2: write to disk (blocking — apply timeout here only) ─────────
    # Slow writes are caused by antivirus real-time scan or OneDrive/cloud-sync
    # on the destination folder. The server console prints per-file timings.
    async def _write():
        loop = _sa_asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sa_save_bytes, items, target, password)

    try:
        return await _sa_asyncio.wait_for(_write(), timeout=_SA_WRITE_TIMEOUT_S)
    except _sa_asyncio.TimeoutError:
        return {"dest": "", "label": target, "ok": [], "skipped": [],
                "rejected": [f"{n}: write timed out after {_SA_WRITE_TIMEOUT_S}s — "
                             f"likely antivirus or OneDrive scanning the destination folder. "
                             f"Check the server console for per-file timing."
                             for n in orig_names],
                "valid": [], "log": None, "timeout": True}
    except Exception as e:
        return {"dest": "", "label": target, "ok": [], "skipped": [],
                "rejected": [f"{n}: {e}" for n in orig_names],
                "valid": [], "log": None}


class _SAAuth(_SA_BaseModel):
    password: str

@app.post("/sa/auth")
def sa_auth(a: _SAAuth):
    return {"ok": _sa_check_pw(a.password)}


def _sa_clear_popup_state():
    """Delete any leftover captcha/token/missing-file popup state files so a stopped SA
    run's unanswered prompt can't reappear on a later, unrelated run. (.missing_decision.json
    used to be left out of this list — that's exactly why clicking Run again could instantly
    re-show the PREVIOUS run's "Wait / Run with blank file" popup: the new run's downloader
    clears that file itself once it starts, but there's a window right after clicking Run,
    before the new subprocess has actually started executing, where the poller can still see
    the OLD file. Clearing it here — right when Run is clicked, and on Stop — closes that gap.)"""
    import os as _sco
    _dir = _sco.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    for _fn in (".captcha_state", ".captcha_answer", ".captcha_ncdex.png",
                ".token_state", ".token_answer", ".missing_decision.json"):
        try: _sco.remove(_sco.path.join(_dir, _fn))
        except OSError: pass

@app.post("/sa/stop")
def sa_stop(request: Request):
    allowed, why = _can_stop("sa", request)
    if not allowed:
        return JSONResponse({"ok": False, "error": why}, status_code=403)
    _r = _sa_stop()
    _sa_clear_popup_state()
    return _r


import tempfile as _sa_tmp

_CHUNK_DIR = _sa_os.path.join(_sa_tmp.gettempdir(), "rms_upload_chunks")

@app.post("/sa/upload-chunk")
async def sa_upload_chunk(upload_id: str, chunk_num: int, total_chunks: int,
                          filename: str, target: str = "short_allocation",
                          chunk: _SA_UploadFile = _SA_File(...)):
    """Receive one slice as multipart/form-data (same transport as small-file upload —
    Defender only intercepts large single requests, not many small multipart ones)."""
    chunk_dir = _sa_os.path.join(_CHUNK_DIR, upload_id)
    _sa_os.makedirs(chunk_dir, exist_ok=True)
    data = await chunk.read()
    with open(_sa_os.path.join(chunk_dir, f"{chunk_num:05d}"), "wb") as f:
        f.write(data)
    return {"ok": True, "chunk": chunk_num, "total": total_chunks, "bytes": len(data)}


@app.post("/sa/upload-finalize")
def sa_upload_finalize(upload_id: str, filename: str, total_chunks: int,
                       target: str = "short_allocation", password: str = ""):
    """Assemble all chunks into the final file, validate, copy to SA/LAS folder."""
    import shutil as _sh
    from core.short_allocation import (cfg as _c, _name_ok as _lnok, SA_VALID as _lv,
                                       check_password as _sa_check_pw,
                                       SA_PROTECTED_UPLOAD_FILES as _sa_protected)
    chunk_dir = _sa_os.path.join(_CHUNK_DIR, upload_id)
    base = _sa_os.path.basename(filename)
    c = _c()
    dest = (c["las"] if target == "las_funding"
            else (_sa_os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES") if target == "common_files"
                  else c["output"]))
    if not _lnok(base, target):
        spec = _lv.get(target, _lv["short_allocation"])
        _sh.rmtree(chunk_dir, ignore_errors=True)
        return {"dest": dest, "ok": [], "skipped": [],
                "rejected": [f"{base}: not a recognised {spec['label']} file"], "password_required": []}
    tp = _sa_os.path.join(dest, base)
    _protected = base.lower() in _sa_protected
    if _sa_os.path.exists(tp) and _protected and not _sa_check_pw(password):
        _sh.rmtree(chunk_dir, ignore_errors=True)
        return {"dest": dest, "ok": [], "skipped": [], "rejected": [],
                "password_required": [base]}
    # Everything else: replace an existing same-named file rather than skipping
    # it — per explicit instruction, an ordinary re-upload should update the
    # file in place. Only the protected set above needs a password to do that.
    try:
        _sa_os.makedirs(dest, exist_ok=True)
        with open(tp, "wb") as out:
            for i in range(total_chunks):
                cp = _sa_os.path.join(chunk_dir, f"{i:05d}")
                with open(cp, "rb") as cf:
                    out.write(cf.read())
        print(f"[upload-finalize] assembled {base} from {total_chunks} chunks → {dest}")
        try:
            use_pg = _sa_os.getenv("USE_POSTGRES","false").strip().lower()=="true"
            _sf = __import__("core.db_postgres",fromlist=["save_run_file"]).save_run_file if use_pg \
                  else __import__("core.db",fromlist=["save_run_file"]).save_run_file
            with open(tp,"rb") as fb: _sf("short_allocation","upload",base,fb.read(),tag=target)
        except Exception as e:
            print(f"[upload-finalize] DB archive skipped: {e}")
        return {"dest": dest, "ok": [base], "skipped": [], "rejected": [], "password_required": []}
    except Exception as e:
        return {"dest": dest, "ok": [], "skipped": [], "rejected": [f"{base}: {e}"], "password_required": []}
    finally:
        _sh.rmtree(chunk_dir, ignore_errors=True)


class _SAFindImport(_SA_BaseModel):
    filename: str        # just the filename — server finds it in common locations
    target: str = "short_allocation"

@app.post("/sa/find-and-import")
def sa_find_and_import(req: _SAFindImport):
    """Search the ENTIRE user profile (and common drives) for the filename and copy it.
    No path entry needed — works wherever the file lives on this machine."""
    import shutil as _sh, time as _lt, getpass as _gp
    from core.short_allocation import cfg as _c, _name_ok as _lnok, SA_VALID as _lv
    filename = _sa_os.path.basename(req.filename)
    c = _c()
    dest = (c["las"] if req.target == "las_funding"
            else (_sa_os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES") if req.target == "common_files"
                  else c["output"]))
    # validate name
    if not _lnok(filename, req.target):
        spec = _lv.get(req.target, _lv["short_allocation"])
        return {"found": False, "dest": dest, "ok": [], "skipped": [],
                "rejected": [f"{filename}: not a recognised {spec['label']} file"]}
    # already in destination folder?
    tp = _sa_os.path.join(dest, filename)
    if _sa_os.path.exists(tp):
        return {"found": True, "dest": dest, "ok": [], "skipped": [filename], "rejected": []}

    def _do_copy(src):
        _sa_os.makedirs(dest, exist_ok=True)
        _sh.copy2(src, tp)
        print(f"[find-and-import] {filename} found at {src}, copied in {_lt.time()-t0:.2f}s")
        try:
            use_pg = _sa_os.getenv("USE_POSTGRES","false").strip().lower()=="true"
            _sf = __import__("core.db_postgres",fromlist=["save_run_file"]).save_run_file if use_pg \
                  else __import__("core.db",fromlist=["save_run_file"]).save_run_file
            with open(tp,"rb") as fb: _sf("short_allocation","upload",filename,fb.read(),tag=req.target)
        except Exception as e:
            print(f"[find-and-import] DB archive skipped: {e}")
        return {"found": True, "dest": dest, "ok": [filename], "skipped": [], "rejected": []}

    t0 = _lt.time()
    try:
        user = _sa_os.getenv("USERNAME") or _gp.getuser()
    except Exception:
        user = "User"

    # ── Pass 1: check specific folders instantly (< 5 ms) ───────────────────
    quick_roots = [
        _sa_os.path.join("C:\\Users", user, "Downloads"),
        _sa_os.path.join("C:\\Users", user, "Desktop"),
        _sa_os.path.join("C:\\Users", user, "Documents"),
        _sa_os.path.join("C:\\Users", user, "OneDrive"),
        dest,  # already checked above but in case it was just created
    ]
    for root in quick_roots:
        p = _sa_os.path.join(root, filename)
        if _sa_os.path.isfile(p):
            try: return _do_copy(p)
            except Exception as e: return {"found": True, "dest": dest, "ok": [], "skipped": [], "rejected": [f"{filename}: {e}"]}

    # ── Pass 2: recursive walk of entire user profile (skips heavy dirs) ────
    SKIP = {"AppData", ".git", "node_modules", "__pycache__", "venv", ".venv",
            "site-packages", "WindowsApps", "$RECYCLE.BIN", "System Volume Information"}
    user_root = _sa_os.path.join("C:\\Users", user)
    fn_lower = filename.lower()
    for dirpath, dirs, files in _sa_os.walk(user_root, topdown=True):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            if f.lower() == fn_lower:
                full = _sa_os.path.join(dirpath, f)
                try: return _do_copy(full)
                except Exception as e: return {"found": True, "dest": dest, "ok": [], "skipped": [], "rejected": [f"{filename}: {e}"]}

    # ── Pass 3: check other drive roots (D:\, E:\, etc.) ────────────────────
    import string as _str
    for letter in _str.ascii_uppercase:
        root = f"{letter}:\\"
        if root == "C:\\" or not _sa_os.path.isdir(root): continue
        p = _sa_os.path.join(root, filename)
        if _sa_os.path.isfile(p):
            try: return _do_copy(p)
            except Exception as e: return {"found": True, "dest": dest, "ok": [], "skipped": [], "rejected": [f"{filename}: {e}"]}

    print(f"[find-and-import] {filename} not found anywhere on this machine after {_lt.time()-t0:.1f}s")
    return {"found": False, "dest": dest, "ok": [], "skipped": [], "rejected": [
        f"{filename}: not found on this machine. Make sure the file is saved locally (not just in browser/email)."]}


class _SALocalImport(_SA_BaseModel):
    paths: list          # list of absolute Windows paths already on this machine
    target: str = "short_allocation"
    run_date: str = ""   # YYYY-MM-DD; blank = today
    run_no: str = ""     # "01".."04"; blank = skip run-number check


def _check_run_no(filename: str, run_no: str):
    """Check the trailing run-number suffix in the filename matches run_no ('01'..'04').
    Two trailing patterns are detected:
      • _i0N  at the end → .lis files  (e.g. F_SA01_P_06436_26062026_i02.lis → run 02)
      • _0N   at the end → MCX/NCCL    (e.g. MCX_PeakMargin56720_20260626_03.csv → run 03)
    NOTE: _SA01_ in the MIDDLE of the name is the exchange product code, NOT the run number.
    Files with no trailing run suffix (RISK_REPORT, main.csv, EXCEPTION…) always pass."""
    if not run_no:
        return None
    import re as _re
    expected = run_no.zfill(2)
    base     = _sa_os.path.splitext(_sa_os.path.basename(filename))[0]
    # Matches _i0N (lis files) or _0N (MCX/csv files) at the very end
    m = _re.search(r'_i?(0[1-4])$', base, _re.IGNORECASE)
    if m:
        file_run = m.group(1)
        if file_run != expected:
            return (f"run-{file_run} file — current window is SA-{expected}. "
                    f"Please select the SA-{expected} version.")
    return None  # no trailing run suffix → shared file, always allowed


def _check_file_date(filename: str, run_date: str):
    """Return None if ok, or an error string if the date embedded in the filename
    doesn't match the expected run date.  Files with NO date in their name pass freely."""
    import re, datetime as _dt
    if not run_date:
        run_date = _dt.date.today().isoformat()
    try:
        expected = _dt.date.fromisoformat(run_date)
    except Exception:
        return None
    base_no_ext = _sa_os.path.splitext(_sa_os.path.basename(filename))[0]
    # Match 8-digit runs not surrounded by more digits (avoids partial matches)
    for m in re.findall(r'(?<!\d)(\d{8})(?!\d)', base_no_ext):
        candidate = None
        # Try YYYYMMDD first (e.g. MCX_PeakMargin56720_20260626_03.csv)
        try:
            y, mo, d = int(m[:4]), int(m[4:6]), int(m[6:])
            candidate = _dt.date(y, mo, d)
        except (ValueError, OverflowError):
            pass
        # Then try DDMMYYYY (e.g. RISK_REPORT_25062026.csv)
        if candidate is None:
            try:
                d, mo, y = int(m[:2]), int(m[2:4]), int(m[4:])
                candidate = _dt.date(y, mo, d)
            except (ValueError, OverflowError):
                continue   # not a valid date in either format — skip
        if candidate != expected:
            return (f"wrong date — file is from {candidate.strftime('%d/%m/%Y')} "
                    f"but run date is {expected.strftime('%d/%m/%Y')}. "
                    f"Please select the {expected.strftime('%d/%m/%Y')} file.")
        return None   # date matches — OK
    return None  # no recognisable date in filename — always allowed


@app.post("/sa/import-local")
def sa_import_local(req: _SALocalImport):
    """Copy files that already exist on THIS machine into the SA/LAS folder.
    Avoids the 130MB HTTP upload entirely — just a local file copy, no size limit."""
    import shutil as _sh, time as _lt
    from core.short_allocation import cfg as _lcfg, SA_VALID as _lval, _name_ok as _lnok
    c = _lcfg()
    dest = (c["las"] if req.target == "las_funding"
            else (_sa_os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES") if req.target == "common_files"
                  else c["output"]))
    _sa_os.makedirs(dest, exist_ok=True)
    ok, skipped, rejected = [], [], []
    for raw_path in req.paths:
        raw_path = raw_path.strip().strip('"')
        base = _sa_os.path.basename(raw_path)
        if not _sa_os.path.isfile(raw_path):
            rejected.append(f"{base}: file not found at path '{raw_path}'")
            continue
        if not _lnok(base, req.target):
            spec = _lval.get(req.target, _lval["short_allocation"])
            rejected.append(f"{base}: not a recognised {spec['label']} file")
            continue
        date_err = _check_file_date(base, req.run_date)
        if date_err:
            rejected.append(f"{base}: {date_err}")
            continue
        run_err = _check_run_no(base, req.run_no)
        if run_err:
            rejected.append(f"{base}: {run_err}")
            continue
        tp = _sa_os.path.join(dest, base)
        if _sa_os.path.exists(tp):
            skipped.append(base)
            continue
        try:
            t0 = _lt.time()
            _sh.copy2(raw_path, tp)
            dt = _lt.time() - t0
            ok.append(base)
            print(f"[sa/import-local] copied {base} ({_sa_os.path.getsize(tp):,} bytes) in {dt:.2f}s")
            # Archive to DB if enabled
            try:
                use_pg = _sa_os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
                _sf = __import__("core.db_postgres", fromlist=["save_run_file"]).save_run_file if use_pg \
                      else __import__("core.db", fromlist=["save_run_file"]).save_run_file
                with open(tp, "rb") as _fb:
                    _sf("short_allocation", "upload", base, _fb.read(), tag=req.target)
            except Exception as _dbe:
                print(f"[sa/import-local] DB archive skipped: {_dbe}")
        except Exception as e:
            rejected.append(f"{base}: {e}")
    return {"dest": dest, "ok": ok, "skipped": skipped, "rejected": rejected}


@app.get("/sa/pick-files")
def sa_pick_files(target: str = "short_allocation"):
    """Open Win32 GetOpenFileNameW via ctypes — zero startup, instant native dialog."""
    import ctypes, ctypes.wintypes as _wt

    OFN_EXPLORER        = 0x00080000
    OFN_ALLOWMULTISELECT= 0x00000200
    OFN_FILEMUSTEXIST   = 0x00001000
    OFN_PATHMUSTEXIST   = 0x00000800

    class _OFN(ctypes.Structure):
        _fields_ = [
            ("lStructSize",       _wt.DWORD),
            ("hwndOwner",         _wt.HWND),
            ("hInstance",         _wt.HINSTANCE),
            ("lpstrFilter",       _wt.LPCWSTR),
            ("lpstrCustomFilter", _wt.LPWSTR),
            ("nMaxCustFilter",    _wt.DWORD),
            ("nFilterIndex",      _wt.DWORD),
            ("lpstrFile",         ctypes.c_void_p),   # must be void* to accept addressof(buf)
            ("nMaxFile",          _wt.DWORD),
            ("lpstrFileTitle",    _wt.LPWSTR),
            ("nMaxFileTitle",     _wt.DWORD),
            ("lpstrInitialDir",   _wt.LPCWSTR),
            ("lpstrTitle",        _wt.LPCWSTR),
            ("Flags",             _wt.DWORD),
            ("nFileOffset",       _wt.WORD),
            ("nFileExtension",    _wt.WORD),
            ("lpstrDefExt",       _wt.LPCWSTR),
            ("lCustData",         _wt.LPARAM),
            ("lpfnHook",          ctypes.c_void_p),
            ("lpTemplateName",    _wt.LPCWSTR),
            ("pvReserved",        ctypes.c_void_p),
            ("dwReserved",        _wt.DWORD),
            ("FlagsEx",           _wt.DWORD),
        ]

    # ── PHASE 1: set up the struct (before dialog opens) ────────────────────────
    # PowerShell fallback ONLY fires here — never after the dialog has opened.
    BUF = 65536
    buf = ctypes.create_unicode_buffer(BUF)
    try:
        ofn = _OFN()
        ofn.lStructSize = ctypes.sizeof(_OFN)
        ofn.hwndOwner   = ctypes.windll.user32.GetForegroundWindow()  # appear in front
        ofn.lpstrFile   = ctypes.addressof(buf)
        ofn.nMaxFile    = BUF
        ofn.lpstrTitle  = "Select files to import"
        ofn.Flags       = OFN_EXPLORER | OFN_ALLOWMULTISELECT | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST
        fn = ctypes.windll.comdlg32.GetOpenFileNameW
        fn.argtypes = [ctypes.POINTER(_OFN)]
        fn.restype  = _wt.BOOL
    except Exception as e_setup:
        # Setup failed BEFORE any dialog opened → safe to try PowerShell instead
        print(f"[pick-files] ctypes setup failed ({e_setup}), using PowerShell")
        import subprocess as _sp
        _ps = ("Add-Type -AssemblyName System.Windows.Forms;"
               "$d=New-Object System.Windows.Forms.OpenFileDialog;"
               "$d.Multiselect=$true;$d.Title='Select files to import';"
               "if($d.ShowDialog() -eq 'OK'){$d.FileNames -join '|'}else{''}")
        try:
            r = _sp.run(["powershell","-NoProfile","-NonInteractive","-Command",_ps],
                        capture_output=True, text=True, timeout=300)
            raw2 = r.stdout.strip()
            return {"paths": [p.strip() for p in raw2.split("|") if p.strip()] if raw2 else []}
        except Exception as e2:
            return {"paths": [], "error": str(e2)}

    # ── PHASE 2: show dialog (no fallback after this point — dialog is open) ──
    if not fn(ctypes.byref(ofn)):
        return {"paths": []}  # user cancelled

    # ── PHASE 3: parse result buffer ────────────────────────────────────────────
    try:
        # create_unicode_buffer has no .raw — read bytes directly from memory address
        raw  = ctypes.string_at(ctypes.addressof(buf), BUF * 2)
        parts, cur = [], bytearray()
        i = 0
        while i < len(raw) - 1:
            lo, hi = raw[i], raw[i + 1]
            if lo == 0 and hi == 0:
                if cur:
                    parts.append(cur.decode("utf-16-le"))
                    cur = bytearray()
                else:
                    break   # double-null = end
            else:
                cur += bytes([lo, hi])
            i += 2
        if not parts:
            return {"paths": []}
        paths = parts if len(parts) == 1 else [parts[0].rstrip("\\") + "\\" + f for f in parts[1:]]
        print(f"[pick-files] selected {len(paths)}: {paths}")
        return {"paths": paths}
    except Exception as e_parse:
        print(f"[pick-files] buffer parse error: {e_parse}")
        return {"paths": [], "error": str(e_parse)}


@app.get("/sa/credentials")
def sa_credentials_get():
    return _sa_get_creds()

@app.post("/sa/credentials")
def sa_credentials_set(u: _SAConfig):
    if not _sa_check_pw(u.password):
        return {"ok": False, "error": "Wrong password"}
    _sa_update_env(u.updates)
    return {"ok": True}


@app.get("/sa/backup/list")
def sa_backup_list(from_date: str = "", to_date: str = "", types: str = ""):
    tl = [t for t in types.split(",") if t.strip()] if types else None
    return {"files": _sa_list_backups(from_date, to_date, tl)}

@app.get("/sa/client-peak-lookup")
def sa_client_peak_lookup(client_codes: str = "", from_date: str = "", to_date: str = ""):
    codes = [c.strip().upper() for c in client_codes.replace(";", ",").split(",") if c.strip()]
    if not codes:
        return {"ok": False, "error": "client_codes is required (comma-separated for multiple)"}
    try:
        all_rows = []
        for code in codes:
            rows = _sa_client_peak_lookup(code, from_date, to_date)
            for r in rows:
                r["client_code"] = code
            all_rows.extend(rows)
        all_rows.sort(key=lambda x: (x["date"], x["client_code"], x["run"]))
        return {"ok": True, "codes": codes, "rows": all_rows}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/sa/browse-folder")
def sa_browse_folder():
    return _sa_browse()

@app.get("/sa/backup/default-dest")
def sa_backup_default_dest():
    """Return the configured default destination path for backup restore operations."""
    c = _sa_cfg()
    return {"dest": c.get("backup_dest", "") or ""}

class _SARestore(_SA_BaseModel):
    paths: list
    dest: str

@app.post("/sa/backup/copy")
def sa_backup_copy(r: _SARestore):
    return _sa_copy_backups(r.paths, r.dest)


# === SHARED HISTORY DB (multi-machine) ===
# When USE_POSTGRES=true in .env, all file archives go to PostgreSQL instead
# of SQLite — so any machine on the LAN can retrieve files from the central DB.
# When USE_POSTGRES=false (default), falls back to SQLite (core/db.py).
_USE_PG = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
if _USE_PG:
    from core.db_postgres import list_run_files as _db_list_files, get_run_file as _db_get_file, delete_run_file as _db_del_file
else:
    from core.db import list_run_files as _db_list_files, get_run_file as _db_get_file, delete_run_file as _db_del_file

@app.get("/sa/db/files")
def sa_db_files(tag: str = "", days: int = 90, kind: str = "",
                from_date: str = "", to_date: str = ""):
    return {"files": _db_list_files(
        pipeline="short_allocation",
        tag=tag or None,
        kind=kind or None,
        days=days,
        from_date=from_date or None,
        to_date=to_date or None,
    )}

@app.get("/sa/db/file")
def sa_db_file(id: int):
    found = _db_get_file(id)
    if not found:
        return {"error": "not found"}
    filename, content = found
    return _SA_Stream(iter([content]), media_type="application/octet-stream",
                      headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@app.delete("/sa/db/file")
def sa_db_file_delete(id: int):
    """Permanently delete one record from run_files (removes bytes from Postgres too)."""
    try:
        deleted = _db_del_file(id)
        return {"ok": deleted, "id": id}
    except Exception as e:
        return {"ok": False, "error": str(e)}

class _SADbRestore(_SA_BaseModel):
    file_ids: list
    dest: str

@app.post("/sa/db/restore")
def sa_db_restore(r: _SADbRestore):
    """Write files from Postgres to a local folder on the server machine."""
    import os as _os
    try:
        _os.makedirs(r.dest, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"Cannot create folder: {e}", "copied": [], "failed": []}
    ok, failed = [], []
    for fid in r.file_ids:
        try:
            found = _db_get_file(int(fid))
            if not found:
                failed.append(f"id={fid}: not found in database")
                continue
            filename, content = found
            dest_path = _os.path.join(r.dest, filename)
            with open(dest_path, "wb") as f:
                f.write(content)
            ok.append(filename)
        except Exception as e:
            failed.append(f"id={fid}: {e}")
    return {"ok": len(failed) == 0, "copied": ok, "failed": failed, "dest": r.dest}


# === PIPELINE BOX (reusable, all pipelines) ===
import json as _px_json
from fastapi.responses import StreamingResponse as _PX_Stream
from core.pipeline_box import (stream_pipeline as _px_stream, stop_pipeline as _px_stop,
                               list_logs as _px_list_logs, read_log as _px_read_log,
                               latest_log as _px_latest_log)

@app.get("/px/run-stream")
def px_run_stream(request: Request, pipe: str = "", run_no: int = 0, market_time: str = "", force: bool = False):
    # Identity comes straight off THIS request's signed session — not a shared
    # global guessed from "whatever -stream request landed most recently"
    # (that guess broke when two people's browsers were both making -stream
    # requests around the same time; see lock_manager.on_start's docstring).
    _by = None
    try:
        from core.auth import current_user as _cu
        _u = _cu(request)
        if _u:
            _by = f"{_u.get('name') or _u.get('sub')} ({_u.get('sub')})"
        else:
            from urllib.parse import unquote as _uq
            _by = _uq(request.cookies.get("rms_user", "")).strip() or None
    except Exception:
        _by = None
    _ip = request.client.host if request.client else ""
    if not _by:
        try:                                    # no live session: the person last signed in from this PC
            from core import lock_manager as _lm4
            _by = _lm4.ip_identity(_ip) or None
        except Exception:
            pass
    if pipe == "ctcl":
        _ctcl_clear_popup_state()  # drop any stale unanswered captcha/token from a previous stopped/crashed run
    if pipe == "probable":
        _probable_clear_popup_state()
    # market_time: only meaningful for pipe="probable" — the operator's chosen
    # market session time from the dashboard's web modal (process-probable-box.js),
    # replacing code2.py's old customtkinter popup. Threaded into the run's own
    # subprocess env (PROBABLE_MARKET_TIME), read by core/pipelines/probable_full.py.
    # Never silently defaulted by THIS route — the modal always sends a real
    # value; the "9:30 AM" fallback only applies if somehow called without one.
    _extra_env = {"PROBABLE_MARKET_TIME": market_time.strip()} if (pipe == "probable" and market_time.strip()) else None
    if pipe == "ms15":
        # The NMASS allocation-upload question is only ever asked when an ADMIN started this run.
        # Any other user's run finishes before that step (file stays on disk, backed up).
        _extra_env = {"MS15_ALLOW_UPLOAD": "1" if _actor(request)[1] else "0"}
    def gen():
        for ev in _px_stream(pipe, run_no=run_no if run_no else None, extra_env=_extra_env,
                              by=_by, ip=_ip, force=force):
            yield f"data: {_px_json.dumps(ev)}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return _PX_Stream(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

@app.post("/px/stop")
def px_stop(request: Request, pipe: str = ""):
    if not pipe:
        return {"ok": False, "error": "pipe is required"}
    allowed, why = _can_stop(f"px_{pipe}", request)
    if not allowed:
        return JSONResponse({"ok": False, "error": why}, status_code=403)
    return _px_stop(pipe)

@app.get("/px/logs")
def px_logs(pipe: str = ""):
    return {"logs": _px_list_logs(pipe)}

@app.get("/px/latest-log")
def px_latest_log(pipe: str = ""):
    """The server's own record of this pipe's most recently written log —
    used by dashboard.html's pxOpenView() to catch a "📋 View" modal showing a
    stale run (cached in THIS browser's localStorage from a run it actually
    watched, possibly yesterday) when the server knows a newer one exists."""
    return {"log": _px_latest_log(pipe)}

@app.get("/px/log")
def px_log(pipe: str = "", name: str = ""):
    return {"content": _px_read_log(pipe, name)}

@app.get("/px/explain-error")
def px_explain_error(pipe: str = "", log: str = ""):
    """Plain-English explanation for ANY pipeline's failed run — works for every
    pipe registered in PIPELINES (ctcl, ms15, cli, and anything added later), not
    a one-off hand-built-per-pipe thing.

    DETERMINISTIC FIRST, AI GUESS ONLY AS A LAST RESORT: every pipeline's
    pre_run_file_check() prints an exact "Missing required files: X, Y" (or
    wrong-date/stale) block on failure — that's not a guess, it's the complete,
    already-known reason. extract_known_failure_reason() parses that block and
    returns it directly, naming the exact file(s), which is far more useful than
    an LLM's paraphrase ("some required files"). Only when the log does NOT
    contain one of these known structures do we fall back to translate_error()
    (Groq free tier) to take a best guess at a genuinely unrecognized failure —
    same fallback contract nmass_download.py/mcx90_download.py already rely on.
    Returns {ok, explanation, source: "known"|"ai"}."""
    from core.pipeline_box import PIPELINES as _pel_P, extract_known_failure_reason as _pel_known
    from core.error_translator import translate_error as _pel_tr
    if pipe not in _pel_P:
        return {"ok": False, "error": "unknown pipeline"}
    text = _px_read_log(pipe, log)
    if not text or text.startswith("(could not read log"):
        return {"ok": False, "error": "log not found"}
    known = _pel_known(text)
    if known:
        return {"ok": True, "explanation": known, "source": "known"}
    lines = text.splitlines()
    label = _pel_P[pipe].get("name", pipe.upper())
    explanation = _pel_tr(f"{label} pipeline run", lines)
    return {"ok": True, "explanation": explanation, "source": "ai"}


from core.pipeline_box import pb_status as _pbs, pb_get_config as _pbc, pb_update as _pbu

@app.get("/px/status")
def px_status(pipe: str = ""):
    return _pbs(pipe)

@app.get("/px/config")
def px_config_get(pipe: str = ""):
    return _pbc(pipe)


@app.post("/px/advance-run")
def px_advance_run(request: Request, pipe: str = "", to: int = -1, password: str = ""):
    """Manually mark today's run counter forward for ANY pipe — generalizes
    the existing /cli/advance-run (which only ever did this for CLI). Needed
    when a run was genuinely completed OUTSIDE this dashboard (e.g. someone
    ran it by hand on a different machine/instance pointed at the same
    output folder) — the counter here never saw it happen, so the dashboard
    keeps insisting on Run 01 before it will unlock Run 02.

    `to` (optional): set the counter to this exact run number instead of just
    incrementing by 1 — e.g. to='1' after manually running CTCL-01 elsewhere,
    so CTCL-02 becomes the next enabled tile. If omitted, behaves like
    /cli/advance-run always did: +1 from whatever today's counter currently is.

    Does NOT touch which files exist on disk or re-run anything — this ONLY
    changes which tile the dashboard considers "next", so use it once you're
    sure the actual output for that run number is already sitting in the
    backup folder (or was produced by whatever ran it elsewhere).

    Password-gated with the SAME password as the "🔑 Credentials" button
    (SA_CONFIG_PASSWORD, checked via the identical core.short_allocation.
    check_password() every /px/config save already uses) — checked here
    server-side, not just hidden behind the frontend's saAskPassword() modal,
    so this can't be bypassed by calling the endpoint directly.

    Also writes a SERVER-SIDE override record (core.pipeline_box._write_override)
    for every run number this pushes the counter past — e.g. advancing 4→6
    tags BOTH 5 and 6, since the tile logic marks both "done" the moment the
    counter passes them. 2026-08-17 incident #2: this used to only be recorded
    in the acting browser's own localStorage, so a colleague using this same
    button from a different machine left every OTHER viewer of the dashboard
    seeing a normal-looking "Done today" tile with a working View button and
    silently no log behind it — nothing on screen said it had been overridden.
    Now every viewer sees the same "ran elsewhere" state and who/when it was
    marked, regardless of whose browser did it."""
    from core.short_allocation import check_password as _ar_pw
    if not _ar_pw(password):
        return {"ok": False, "error": "Wrong password"}
    import os as _aro, datetime as _ard
    from core.pipeline_box import PIPELINES as _arP, _write_override as _ar_wo
    p = _arP.get(pipe)
    if not p:
        return {"ok": False, "error": f"unknown pipe '{pipe}'"}
    base = _aro.getenv(p.get("state_dir_env", ""), p.get("state_default", ""))
    _aro.makedirs(base, exist_ok=True)
    fp = _aro.path.join(base, p.get("state_file", "run_counter.txt"))
    today = _ard.datetime.now().strftime("%Y-%m-%d")
    n = 0
    try:
        content = open(fp, encoding="utf-8", errors="replace").read().strip()
        if "|" in content:
            d, existing_n = content.split("|", 1)
            if d.strip() == today:
                n = int(existing_n)
    except Exception:
        pass
    old_n = n
    # `to` = 0 is a valid target (RESET: un-mark every run done today). It used
    # to be treated as "not given" (falsy) and silently advanced by +1 instead,
    # so a run could never be un-marked. Omitted `to` is now signalled by -1.
    n = int(to) if to >= 0 else (n + 1)
    n = max(0, min(n, p.get("runs", n)))
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"{today}|{n}")
    if n < old_n:
        # Lowering the counter: drop the manual-override records for the runs
        # that are no longer done, so their tiles stop showing "marked done".
        try:
            import json as _arj
            _op = _aro.path.join(base, "run_overrides.json")
            with open(_op, encoding="utf-8") as _f:
                _od = _arj.load(_f)
            _od["runs"] = {k: v for k, v in (_od.get("runs") or {}).items() if int(k) <= n}
            with open(_op, "w", encoding="utf-8") as _f:
                _arj.dump(_od, _f)
        except Exception:
            pass
    if n > old_n:
        try:
            from core.auth import current_user as _ar_cu
            _u = _ar_cu(request)
            _by = f"{_u.get('name') or _u.get('sub')} ({_u.get('sub')})" if _u else None
        except Exception:
            _by = None
        _at = _ard.datetime.now().strftime("%H:%M")
        for _rn in range(old_n + 1, n + 1):
            _ar_wo(p, _rn, {"type": "mark_done", "at": _at, "by": _by})
    return {"ok": True, "run_state": n, "folder": base}


@app.post("/ctcl/backfill-db")
def ctcl_backfill_db(date: str = "", run: int = 0, password: str = ""):
    """Push an ALREADY-on-disk CTCL vs NMASS run folder into PostgreSQL.

    For a run that genuinely completed OUTSIDE this dashboard (e.g. run by
    hand on a different machine) whose output files were then copied by hand
    into THIS machine's BACKUPFILES AUTOMATED\\<date>\\<run> folder — the
    normal end-of-run archive step (backup_and_cleanup → _archive_to_db,
    which only fires when THIS dashboard's Run button drives the pipeline)
    never got a chance to run for those files, so they never made it into the
    run_files Postgres table.

    `date`: folder name exactly as on disk, e.g. "21-07-2026" (DD-MM-YYYY).
    `run`: the two-digit run folder number, e.g. 1 for ".../01".

    Does NOT touch or move anything on disk — only reads the files already
    there and archives their bytes to Postgres (safe to call more than once).
    Password-gated with the SAME SA_CONFIG_PASSWORD as the 🔑 Credentials
    button, checked server-side exactly like /px/advance-run."""
    from core.short_allocation import check_password as _bk_pw
    if not _bk_pw(password):
        return {"ok": False, "error": "Wrong password"}
    if not date or not run:
        return {"ok": False, "error": "date and run are required"}
    from core.pipelines.ctcl_vs_nmass import backfill_run_to_db
    return backfill_run_to_db(date, int(run))


@app.post("/px/backfill-db")
def px_backfill_db(pipe: str = "", date: str = "", run: int = 0, subfolder: str = "", password: str = ""):
    """Push an ALREADY-on-disk backup folder into PostgreSQL — generic version
    covering ctcl, ms15, cli, and probable (the original CTCL-only
    /ctcl/backfill-db still works too; this just extends the same idea to
    every process, since the four pipes' backup folders don't all have the
    same shape and each needed its own backfill_*_to_db() written for its
    actual on-disk layout:
      - ctcl / ms15: BACKUPFILES AUTOMATED\\<DD-MM-YYYY>\\<NN>\\
      - cli:         BACKUPFILES AUTOMATED\\<DD-MM-YYYY>\\<Morning|Afternoon|Evening>\\
                     OR \\<HH-MM-SS>\\ (exact run time, some machines back up
                     by timestamp instead of slot name — backfill_date_to_db()
                     classifies either into Morning/Afternoon/Evening by hour).
                     Older dates before any split may still be flat, no
                     subfolder at all — handled too.
                     Pass date="ALL" to backfill every date folder found under
                     BACKUPFILES AUTOMATED in one call (backfill_all_to_db()) —
                     for pushing months of history in one shot instead of one
                     date at a time.
      - probable:    BACKUPFILES AUTOMATED\\<date>\\RECONCILIATION or SHORTAGE\\
                     (date format is "21 JUL 2026", NOT DD-MM-YYYY — different
                     from the other three)

    Does NOT touch or move anything on disk — only reads files already there
    and archives their bytes to Postgres. Safe to call more than once.
    Password-gated with the SAME SA_CONFIG_PASSWORD as every other sensitive
    action here, checked server-side."""
    from core.short_allocation import check_password as _bk_pw3
    if not _bk_pw3(password):
        return {"ok": False, "error": "Wrong password"}
    if not date:
        return {"ok": False, "error": "date is required"}
    try:
        if pipe == "ctcl":
            from core.pipelines.ctcl_vs_nmass import backfill_run_to_db
            if not run:
                return {"ok": False, "error": "run is required for ctcl"}
            return backfill_run_to_db(date, int(run))
        elif pipe == "ms15":
            from core.pipelines.margin_shortage_15min import backfill_run_to_db
            if not run:
                return {"ok": False, "error": "run is required for ms15"}
            return backfill_run_to_db(date, int(run))
        elif pipe == "cli":
            from core.pipelines.cli_upload import backfill_date_to_db, backfill_all_to_db
            if date.strip().upper() == "ALL":
                return backfill_all_to_db()
            return backfill_date_to_db(date)
        elif pipe == "probable":
            from core.pipelines.probable_full import backfill_run_to_db
            return backfill_run_to_db(date, subfolder or None)
        else:
            return {"ok": False, "error": f"unknown pipe '{pipe}'"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/px/backfill-fix-cli-dates")
def px_backfill_fix_cli_dates():
    """One-off repair for CLI backfilled rows that got stamped with
    date.today() in Postgres's run_date column instead of their real date
    (see core.db_postgres.fix_cli_run_dates() for the full story — this is
    why backfilled history was showing up as "today" in the Client Data
    lookup, Get Files, and the merged-download Date column). Re-derives the
    correct date from each row's own `tag` and fixes it in place. Safe to
    call more than once."""
    if os.getenv("USE_POSTGRES", "false").strip().lower() != "true":
        return {"ok": False, "error": "USE_POSTGRES is not enabled on this machine"}
    from core.db_postgres import fix_cli_run_dates
    return fix_cli_run_dates()


@app.post("/px/cli-delete-backup-rows")
def px_cli_delete_backup_rows(date: str = "", confirm: bool = False, password: str = ""):
    """Delete Client Prudential Limit BACKUP rows from Postgres for one date
    (default today, format YYYY-MM-DD). Only pipeline='cli', kind='backup'.
    Password-gated (SA_CONFIG_PASSWORD). With confirm=false (default) it is a
    DRY RUN: nothing is deleted, it just lists what would be. Call again with
    confirm=true to actually delete."""
    from core.short_allocation import check_password as _pw
    if not _pw(password):
        return {"ok": False, "error": "Wrong password"}
    if os.getenv("USE_POSTGRES", "false").strip().lower() != "true":
        return {"ok": False, "error": "USE_POSTGRES is not enabled on this machine"}
    from core.db_postgres import delete_cli_backup_rows
    return delete_cli_backup_rows(date or None, confirm)


@app.post("/sa/backfill-db")
def sa_backfill_db(date: str = "", run: int = 0, password: str = ""):
    """Push an ALREADY-on-disk Short Allocation run folder into PostgreSQL —
    the SA equivalent of /px/backfill-db (SA isn't in PIPELINES, so it gets
    its own route, same as /sa/mark-run-done vs /px/advance-run).
    Password-gated identically."""
    from core.short_allocation import check_password as _bk_pw4
    if not _bk_pw4(password):
        return {"ok": False, "error": "Wrong password"}
    if not date or not run:
        return {"ok": False, "error": "date and run are required"}
    from core.short_allocation import backfill_run_to_db as _sa_backfill
    return _sa_backfill(date, int(run))


class _PXConfig(_SA_BaseModel):
    pipe: str
    password: str
    updates: dict

@app.post("/px/config")
def px_config_set(u: _PXConfig):
    return _pbu(u.pipe, u.password, u.updates)


from core.pipeline_box import (pb_upload as _pbup, pb_backup_types as _pbbt,
                               pb_list_backups as _pblb, pb_copy_backups as _pbcb)

@app.post("/px/upload")
def px_upload(pipe: str = "", target: str = "main", password: str = "",
                    files: _SA_List[_SA_UploadFile] = _SA_File(...),
                    password_form: str = _SA_Form("", alias="password")):
    # Password arrives in the multipart body from the dashboard (see sa_upload) — accept it from there or the URL.
    password = password or password_form
    import shutil as _sh
    saved = []
    for f in files:
        tmp = _sa_os.path.join(_sa_tmp.gettempdir(), _sa_os.path.basename(f.filename))
        with open(tmp, "wb") as out:
            _sh.copyfileobj(f.file, out)
        saved.append(tmp)
    return _pbup(pipe, saved, target, password)

@app.get("/px/backup-types")
def px_backup_types(pipe: str = ""):
    return {"types": _pbbt(pipe)}

@app.get("/px/backup/list")
def px_backup_list(pipe: str = "", from_date: str = "", to_date: str = "", types: str = "", slots: str = ""):
    tl = [t for t in types.split(",") if t.strip()] if types else None
    sl = [s for s in slots.split(",") if s.strip()] if slots else None
    return {"files": _pblb(pipe, from_date, to_date, tl, sl)}

class _PXRestore(_SA_BaseModel):
    pipe: str
    paths: list
    dest: str

@app.post("/px/backup/copy")
def px_backup_copy(r: _PXRestore):
    return _pbcb(r.pipe, r.paths, r.dest)

@app.get("/px/backup/download")
def px_backup_download(pipe: str = "", path: str = ""):
    """Download a single backup file to the browser. path is either a real
    filesystem path inside the pipeline's backup roots, or a 'db:<pipeline>:<id>'
    reference into Postgres run_files (used when this machine has no local
    copy on disk — see pb_list_backups)."""
    from core.pipeline_box import pb_backup_file as _pbbf, pb_backup_db_file as _pbbdf
    from fastapi.responses import FileResponse as _PX_FileResp, Response as _PX_Resp
    if str(path or "").startswith("db:"):
        got = _pbbdf(path)
        if not got:
            return {"error": "file not found in Postgres"}
        fname, content = got
        return _PX_Resp(content=content, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    ap = _pbbf(pipe, path)
    if not ap:
        return {"error": "file not found or outside backup folder"}
    return _PX_FileResp(ap, filename=_sa_os.path.basename(ap),
                        media_type="application/octet-stream")

@app.get("/px/backup/download-filtered")
def px_backup_download_filtered(pipe: str = "", path: str = "", client_code: str = ""):
    """Same file as /px/backup/download, but with only the row(s) belonging to
    `client_code` (comma-separate for multiple) — same columns/format as the
    original, just filtered. Falls back to the plain, unfiltered download if
    the file has no recognizable account column (e.g. an .xlsx that isn't a
    normal collateral export) rather than erroring out."""
    from core.pipeline_box import pb_backup_file_filtered as _pbbff, pb_backup_file as _pbbf, pb_backup_db_file as _pbbdf2
    from fastapi.responses import FileResponse as _PX_FileResp2, Response as _PX_Resp2
    codes = [c.strip() for c in client_code.replace(";", ",").split(",") if c.strip()]
    if codes:
        got = _pbbff(pipe, path, codes)
        if got:
            fname, content, media_type = got
            if content is None:
                # Recognized/parsed fine, but none of the given code(s) appear in
                # this particular file (e.g. the account doesn't trade this
                # exchange at all) — a distinguishable JSON body instead of a
                # blob, so the caller can skip it rather than save a useless
                # empty file.
                return {"error": "no_data", "message": f"No rows for {client_code} in {fname}"}
            return _PX_Resp2(content=content, media_type=media_type,
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    # No codes given, or the file couldn't be filtered — serve the original.
    # path may be a real on-disk path OR a 'db:<pipeline>:<id>' Postgres
    # reference (a file backfilled from another machine, no local disk copy
    # on this one) — handle both, same as /px/backup/download.
    if str(path or "").startswith("db:"):
        got2 = _pbbdf2(path)
        if not got2:
            return {"error": "file not found in Postgres"}
        fname2, content2 = got2
        return _PX_Resp2(content=content2, media_type="application/octet-stream",
                         headers={"Content-Disposition": f'attachment; filename="{fname2}"'})
    ap = _pbbf(pipe, path)
    if not ap:
        return {"error": "file not found or outside backup folder"}
    return _PX_FileResp2(ap, filename=_sa_os.path.basename(ap),
                         media_type="application/octet-stream")

@app.post("/px/backup/download-merged")
async def px_backup_download_merged(request: Request):
    """Same filtering as /px/backup/download-filtered, but across many files
    at once (typically one file TYPE's files across a whole date range),
    stacked into ONE output with a 'Date' column inserted — so filtering 5
    exchange file types across a 30-day range returns 5 files (one per
    type), not up to 150 (one per type per date).

    Body: {"pipe": "cli", "client_code": "SN120",
           "items": [{"path": "...", "date": "03-09-2026", "name": "..."}, ...]}
    `items` should all be the SAME file type — the "Get files" tab groups by
    type client-side (it already computes the type label for display) and
    calls this once per group."""
    from core.pipeline_box import pb_backup_download_merged as _pbbdm
    from fastapi.responses import Response as _PX_Resp3
    body = await request.json()
    pipe = (body.get("pipe") or "").strip()
    items = body.get("items") or []
    client_code = (body.get("client_code") or "").strip()
    codes = [c.strip() for c in client_code.replace(";", ",").split(",") if c.strip()]
    if not codes:
        return {"error": "client_code required"}
    if not items:
        return {"error": "items required"}
    from starlette.concurrency import run_in_threadpool as _px_rtp
    got = await _px_rtp(_pbbdm, pipe, items, codes)   # heavy Excel merge: off the main loop so the server stays responsive
    if not got:
        return {"error": "no_filterable_column", "message": "This file type has no recognizable account column."}
    fname, content, media_type = got
    if content is None:
        return {"error": "no_data", "message": f"No rows for {client_code} across any of these files"}
    return _PX_Resp3(content=content, media_type=media_type,
                     headers={"Content-Disposition": f'attachment; filename="{fname}"'})

@app.get("/px/client-lookup")
def px_client_lookup(pipe: str = "", client_codes: str = "",
                     from_date: str = "", to_date: str = "",
                     runs: str = ""):
    """Scan the pipeline's backup folders for per-client input collaterals and
    output (FULL_FILE) values across a date range — CTCL analogue of
    /sa/client-peak-lookup.
    runs: optional comma-separated list of run numbers to include (e.g. "1,3,5").
          Empty = all runs."""
    from core.pipeline_box import pb_client_lookup as _pbcl
    codes = [c.strip() for c in client_codes.replace(";", ",").split(",") if c.strip()]
    run_filter = None
    if runs.strip():
        try:
            run_filter = [int(x.strip()) for x in runs.split(",") if x.strip()]
        except Exception:
            pass
    try:
        return _pbcl(pipe, codes, from_date, to_date, run_filter)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/px/cli-client-lookup")
def px_cli_client_lookup(client_codes: str = "", from_date: str = "", to_date: str = "",
                          slots: str = ""):
    """CLI Prudential Limit's own client-data lookup — Morning/Afternoon/Evening
    slot folders instead of numbered runs, plus the raw CM/FO/CO component
    columns (Cash allocated (b) / Cash-Others (c) / Non-cash Securities (d))
    individually, not just their summed bucket value. See pb_cli_client_lookup
    for why this isn't just pb_client_lookup with an extra pipe branch.
    slots: optional comma-separated "Morning,Afternoon,Evening" — empty = all."""
    from core.pipeline_box import pb_cli_client_lookup as _pbccl
    codes = [c.strip() for c in client_codes.replace(";", ",").split(",") if c.strip()]
    slot_filter = [s.strip() for s in slots.split(",") if s.strip()]
    try:
        return _pbccl(codes, from_date, to_date, slot_filter)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/sa/las-email-download-stream")
def sa_las_email_download_stream():
    """Stream the LAS Debit Balances email download live.
    Runs las_email_downloader.py and streams output as SSE events.
    Exit 0 = success/already-exists; Exit 1 = not found in inbox."""
    import subprocess as _sub, threading as _thr, queue as _q2, sys as _sys2
    def gen():
        q2 = _q2.Queue()
        script = _sa_os.path.join(
            _sa_os.getenv("SA_CODES_DIR", r"D:\CLAUDE\rms-copilot\sa_codes"),
            "las_email_downloader.py"
        )
        python = _sa_os.getenv("SA_PYTHON", "python")
        env2 = _sa_os.environ.copy()
        env2["SA_ENV_FILE"] = r"D:\CLAUDE\rms-copilot\.env"
        env2["PYTHONIOENCODING"] = "utf-8"   # prevent cp1252 UnicodeEncodeError
        env2["PYTHONUTF8"] = "1"             # Python 3.7+ UTF-8 mode
        env2["PYTHONUNBUFFERED"] = "1"       # otherwise child stdout is block-buffered on
                                              # a pipe and a slow/quiet run can look hung
        try:
            proc = _sub.Popen(
                [python, "-u", script],
                stdout=_sub.PIPE, stderr=_sub.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env2,
                cwd=_sa_os.path.join(_sa_os.path.dirname(_sa_os.path.abspath(__file__)))
            )
        except Exception as e:
            yield f"data: {_sa_json.dumps({'type':'log','msg':f'❌ Failed to start downloader: {e}'})}\n\n"
            yield f"data: {_sa_json.dumps({'type':'error','msg':str(e)})}\n\n"
            yield 'data: {"type":"end"}\n\n'
            return

        def _reader():
            for line in proc.stdout:
                q2.put(("log", line.rstrip()))
            proc.wait()
            q2.put(("rc", proc.returncode))

        _thr.Thread(target=_reader, daemon=True).start()

        import time as _t2
        rc = None
        while True:
            try:
                kind2, val = q2.get(timeout=30)
            except _q2.Empty:
                yield f"data: {_sa_json.dumps({'type':'log','msg':'⏳ Waiting…'})}\n\n"
                continue
            if kind2 == "log":
                yield f"data: {_sa_json.dumps({'type':'log','msg':val})}\n\n"
            elif kind2 == "rc":
                rc = val
                break

        if rc == 0:
            yield f"data: {_sa_json.dumps({'type':'done','msg':'✅ LAS Debit file ready'})}\n\n"
        else:
            yield f"data: {_sa_json.dumps({'type':'error','msg':'⚠️ File not found in inbox — check later or add manually'})}\n\n"
        yield 'data: {"type":"end"}\n\n'

    return _SA_Stream(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/sa/las-debit-status")
def sa_las_debit_status():
    """Check if today's LAS Debit Balances file is present in the LAS folder."""
    import datetime as _dtt
    las_dir = _sa_os.getenv("SA_LAS_FUNDING_DIR", r"C:\LAS FUNDING")
    today_str = _dtt.date.today().strftime("%d-%m-%Y")
    fname = f"LAS Debit Balances-{today_str}.xlsx"
    fpath = _sa_os.path.join(las_dir, fname)
    exists = _sa_os.path.isfile(fpath)
    size = _sa_os.path.getsize(fpath) if exists else 0
    return {
        "filename": fname,
        "exists": exists,
        "size": size,
        "las_dir": las_dir,
        "today": today_str,
    }


@app.get("/sa/missing-file/pending")
def sa_missing_file_pending():
    """Check if the downloader is waiting for an operator decision about a missing exchange file.
    Returns {pending, missing, context, timeout_at} when a decision is needed."""
    sa_dir     = _sa_os.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    state_file = _sa_os.path.join(sa_dir, ".missing_decision.json")
    try:
        import json as _j, datetime as _dt_mf
        with open(state_file, encoding="utf-8") as f:
            data = _j.load(f)
        if data.get("state") != "PENDING":
            return {"pending": False}
        # ── Staleness guard: if timeout_at is in the past, the downloader process
        # that wrote this file is gone (killed/crashed). Auto-clear so the popup
        # doesn't persist forever. Add a 60-second grace period.
        timeout_at = data.get("timeout_at", "")
        if timeout_at:
            try:
                expiry = _dt_mf.datetime.fromisoformat(timeout_at)
                if _dt_mf.datetime.now() > expiry + _dt_mf.timedelta(seconds=60):
                    try: _sa_os.remove(state_file)
                    except Exception: pass
                    return {"pending": False}
            except Exception:
                pass
        return {
            "pending":    True,
            "missing":    data.get("missing", []),
            "context":    data.get("context", ""),
            "timeout_at": data.get("timeout_at", ""),
        }
    except Exception:
        return {"pending": False}


@app.post("/sa/missing-file/dismiss")
def sa_missing_file_dismiss():
    """Operator manually dismisses a stale missing-file popup when no run is active."""
    sa_dir     = _sa_os.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    state_file = _sa_os.path.join(sa_dir, ".missing_decision.json")
    try:
        _sa_os.remove(state_file)
    except Exception:
        pass
    return {"ok": True}


class _SAMissingDecision(_SA_BaseModel):
    action: str   # "WAIT" | "RUN_BLANK"

@app.post("/sa/missing-file/decision")
def sa_missing_file_decision(r: _SAMissingDecision):
    """Submit the operator's decision for a missing exchange file.
    action='WAIT'     → downloader will retry after 5 min.
    action='RUN_BLANK' → downloader will create blank placeholder file and continue."""
    sa_dir     = _sa_os.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    state_file = _sa_os.path.join(sa_dir, ".missing_decision.json")
    action     = r.action.strip().upper()
    if action not in ("WAIT", "RUN_BLANK"):
        return {"ok": False, "error": f"Invalid action '{action}'. Must be WAIT or RUN_BLANK."}
    try:
        import json as _j
        # Read current state to preserve missing list
        try:
            with open(state_file, encoding="utf-8") as f:
                data = _j.load(f)
        except Exception:
            data = {}
        data["state"] = action
        with open(state_file, "w", encoding="utf-8") as f:
            _j.dump(data, f)
        return {"ok": True, "action": action}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/sa/las-wa-manifest")
def sa_las_wa_manifest():
    """Return the WhatsApp send manifest written by the last LAS run.
    Shows zones, image filenames, masked recipient numbers (last 4 digits), sent status."""
    las_dir = _sa_os.getenv("SA_LAS_FUNDING_DIR", r"C:\LAS FUNDING")
    mf_path = _sa_os.path.join(las_dir, ".las_wa_manifest.json")
    try:
        import json as _j
        data = _j.load(open(mf_path, encoding="utf-8"))
        # Mask recipient numbers: show only last 4 digits for display
        for zone in data.values():
            zone["recipients_display"] = [
                f"****{n[-4:]}" if len(n) >= 4 else n
                for n in (zone.get("recipients") or [])
            ]
        return {"ok": True, "manifest": data}
    except FileNotFoundError:
        return {"ok": False, "error": "No manifest found — run LAS first."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/sa/las-wa-image")
def sa_las_wa_image(zone: str):
    """Serve the LAS WhatsApp image for the given zone (reads path from manifest).
    Searches backup folders automatically if original path is gone."""
    from fastapi import HTTPException as _HE
    las_dir  = _sa_os.getenv("SA_LAS_FUNDING_DIR", r"C:\LAS FUNDING")
    mf_path  = _sa_os.path.join(las_dir, ".las_wa_manifest.json")
    try:
        import json as _jj
        _mdata = _jj.load(open(mf_path, encoding="utf-8"))
    except Exception:
        raise _HE(status_code=404, detail="Manifest not found")
    _zv = _mdata.get(zone)
    if not _zv:
        raise _HE(status_code=404, detail=f"Zone '{zone}' not in manifest")
    _img_path = _zv.get("image", "")
    if not _sa_os.path.isfile(_img_path):
        _img_name = _sa_os.path.basename(_img_path)
        _found    = None
        _bak_root = _sa_os.path.join(las_dir, "BACKUP FILES AUTOMATED")
        if _sa_os.path.isdir(_bak_root):
            for _rr, _dd, _ff in _sa_os.walk(_bak_root):
                if _img_name in _ff:
                    _found = _sa_os.path.join(_rr, _img_name); break
        if not _found:
            for _sd in (_sa_os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES"),
                        _sa_os.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION"),
                        las_dir):
                _c = _sa_os.path.join(_sd, _img_name)
                if _sa_os.path.isfile(_c):
                    _found = _c; break
        if not _found:
            raise _HE(status_code=404, detail=f"Image file not found: {_img_name}")
        _img_path = _found
    return FileResponse(_img_path, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


class _SAWaResend(_SA_BaseModel):
    zone:             str
    image_path:       str
    title:            str
    recipients:       list   # full phone numbers to send to (may include a temp override)

@app.post("/sa/las-wa-resend")
def sa_las_wa_resend(r: _SAWaResend):
    """Re-upload a LAS image and resend to specified recipients via WhatsApp.
    Recipients may include a temporary override number not stored anywhere."""
    import requests as _req, os as _os2
    auth_token  = _sa_os.getenv("SA_WA_TOKEN", "")
    upload_url  = _sa_os.getenv("SA_WA_UPLOAD_URL", "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/media")
    send_url    = _sa_os.getenv("SA_WA_SEND_URL",   "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/messages")
    today       = __import__("datetime").datetime.now().strftime("%d-%m-%Y")

    # Resolve image path — original path first, then search backup subfolders,
    # then check SA_OUTPUT_DIR (common/local fallback).
    _img_path = r.image_path
    if not _sa_os.path.isfile(_img_path):
        _img_name  = _sa_os.path.basename(_img_path)
        _las_dir   = _sa_os.getenv("SA_LAS_FUNDING_DIR", r"C:\LAS FUNDING")
        _common_d  = _sa_os.getenv("SA_COMMON_DIR",      r"C:\COMMON FILES")
        _sa_out_d  = _sa_os.getenv("SA_OUTPUT_DIR",      r"C:\SHORT ALLOCATION")
        _found     = None
        # Search backup subfolders of LAS FUNDING
        _bak_root  = _sa_os.path.join(_las_dir, "BACKUP FILES AUTOMATED")
        if _sa_os.path.isdir(_bak_root):
            for _root, _dirs, _files in _sa_os.walk(_bak_root):
                if _img_name in _files:
                    _found = _sa_os.path.join(_root, _img_name); break
        # Fallback: COMMON FILES and SA_OUTPUT_DIR
        if not _found:
            for _d in (_common_d, _sa_out_d, _las_dir):
                _candidate = _sa_os.path.join(_d, _img_name)
                if _sa_os.path.isfile(_candidate):
                    _found = _candidate; break
        if _found:
            _img_path = _found
            print(f"📂 Image resolved from backup: {_img_path}")
        else:
            return {"ok": False, "error": f"Image not found: {r.image_path} (also searched backup folders)"}
    # Normalize phone numbers: user may enter 10-digit numbers without +91
    def _norm_phone(p: str) -> str:
        p = p.strip().replace(" ", "").replace("-", "")
        if p.startswith("+91"):   p = p[3:]
        elif p.startswith("91") and len(p) == 12: p = p[2:]
        return ("+91" + p) if len(p) == 10 else p
    _recipients = [_norm_phone(p) for p in r.recipients if str(p).strip()]
    if not _recipients:
        return {"ok": False, "error": "No recipients specified."}

    # Upload image
    try:
        with open(_img_path, "rb") as _f:
            up_r = _req.post(
                upload_url,
                headers={"Authorization": f"Bearer {auth_token}", "accept": "application/json"},
                files={"file": ("report.jpg", _f, "image/jpeg")},
                timeout=30,
            )
        if up_r.status_code not in (200, 201):
            return {"ok": False, "error": f"Upload failed ({up_r.status_code}): {up_r.text[:200]}"}
        media_id = up_r.json().get("id") or up_r.json().get("mediaHandle")
    except Exception as e:
        return {"ok": False, "error": f"Upload error: {e}"}

    # Send to each recipient
    results = []
    for phone in _recipients:
        payload = {
            "to": phone,
            "type": "template",
            "source": "external",
            "template": {
                "name": "daily_report",
                "language": {"code": "en"},
                "components": [
                    {"type": "header", "parameters": [{"type": "image", "image": {"id": media_id}}]},
                    {"type": "body",   "parameters": [{"type": "text", "text": r.title}, {"type": "text", "text": today}]},
                ],
            },
        }
        try:
            sr = _req.post(send_url, headers={"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}, json=payload, timeout=20)
            results.append({"phone": f"****{phone[-4:]}", "ok": sr.status_code == 200, "detail": sr.text[:100] if sr.status_code != 200 else ""})
        except Exception as e:
            results.append({"phone": f"****{phone[-4:]}", "ok": False, "detail": str(e)})

    return {"ok": all(r["ok"] for r in results), "results": results}


@app.get("/sa/captcha/pending")
def sa_captcha_pending(request: Request):
    """Check if NCDEX CAPTCHA is awaiting browser input.
    Returns {pending: bool, image_b64: str|null}.
    ncdex_downloader writes .captcha_state='PENDING' + .captcha_ncdex.png to
    SA_OUTPUT_DIR; this endpoint exposes them to the dashboard poller."""
    import base64 as _b64
    sa_dir     = _sa_os.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    state_file = _sa_os.path.join(sa_dir, ".captcha_state")
    img_file   = _sa_os.path.join(sa_dir, ".captcha_ncdex.png")
    try:
        st = open(state_file, encoding="utf-8").read().strip()
        if st != "PENDING":
            return {"pending": False}
        if not _popup_visible(request, state_file, ["sa"]):
            return {"pending": False}
        if not _sa_os.path.isfile(img_file):
            return {"pending": True, "image_b64": None}
        img_b64 = _b64.b64encode(open(img_file, "rb").read()).decode()
        return {"pending": True, "image_b64": img_b64}
    except Exception:
        return {"pending": False}


class _SACaptchaAnswer(_SA_BaseModel):
    text: str

@app.post("/sa/captcha/answer")
def sa_captcha_answer(a: _SACaptchaAnswer):
    """Submit the typed CAPTCHA text to the waiting NCDEX downloader.
    Writes .captcha_answer then sets .captcha_state='ANSWERED'."""
    sa_dir     = _sa_os.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    state_file = _sa_os.path.join(sa_dir, ".captcha_state")
    ans_file   = _sa_os.path.join(sa_dir, ".captcha_answer")
    try:
        with open(ans_file, "w", encoding="utf-8") as f:
            f.write(a.text.strip())
        with open(state_file, "w", encoding="utf-8") as f:
            f.write("ANSWERED")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Captcha auto-fill switch (2Captcha) ─────────────────────────────────────
# core/captcha_solver.py answers the NMASS / NCDEX captchas automatically (45 s per try, 5 tries per login, then the
# normal popup). This switch lives in a small JSON file that every pipeline process reads, so a change applies to
# the very next captcha without restarting anything. The API key itself is only ever read from .env.
@app.get("/captcha/auto")
def captcha_auto_get():
    """Current state of the auto-fill switch plus today's accepted/rejected counts."""
    from core import captcha_solver as _cs
    _st = _cs.switch_state()
    return {"enabled": _st["enabled"], "by": _st["by"], "at": _st["at"], "key_configured": bool(_cs.api_key()),
            "max_tries": _cs.MAX_TRIES, "timeout_s": _cs.TIMEOUT_S, "today": _cs.stats_today(),
            "balance": __import__("core.captcha_balance", fromlist=["x"]).read_state()}


class _CaptchaAutoBody(_SA_BaseModel):
    enabled: bool


@app.post("/captcha/auto")
def captcha_auto_set(body: _CaptchaAutoBody, request: Request):
    """Turn captcha auto-fill on or off for everybody (the change is attributed to whoever clicked)."""
    from core import captcha_solver as _cs
    _who, _adm = _actor(request)
    try:
        _st = _cs.set_enabled(body.enabled, _who or "")
    except OSError as _e:
        return {"ok": False, "error": f"Could not save the switch: {_e}"}
    return {"ok": True, "enabled": _st["enabled"], "key_configured": bool(_cs.api_key())}


@app.get("/captcha/balance")
def captcha_balance_get():
    """Last 2Captcha balance reading (hourly check) and the alert settings."""
    from core import captcha_balance as _cb
    st = _cb.read_state()
    return {**st, "limit": _cb.limit(), "to": os.getenv("CAPTCHA_ALERT_TO", ""), "cc": os.getenv("CAPTCHA_ALERT_CC", "")}

@app.post("/captcha/balance/test")
def captcha_balance_test():
    """Send a [TEST] copy of the low-balance mail to the saved To / CC."""
    from core import captcha_balance as _cb
    try:
        return _cb.send_test()
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/captcha/token-auto")
def captcha_token_get():
    from core import mcx_soft_token as _mt
    st = _mt.switch_state()
    return {**st, "seed_ok": _mt.seed_ok()}

@app.post("/captcha/token-auto")
async def captcha_token_set(request: Request):
    """Turn the generated MCX RSA code on or off for everybody."""
    from core import mcx_soft_token as _mt
    body = await request.json()
    try:
        st = _mt.set_switch(bool(body.get("enabled")), (_actor(request)[0] or ""))
    except OSError as e:
        return {"ok": False, "error": f"Could not save the switch: {e}"}
    return {"ok": True, **st}

@app.post("/captcha/balance/check")
def captcha_balance_check():
    """Check the balance right now (also mails the alert if it is low and one is due)."""
    from core import captcha_balance as _cb
    return {"ok": True, **_cb.check()}

@app.on_event("startup")
def _captcha_balance_startup():
    try:
        from core import captcha_balance as _cb
        _cb.start_ticker()
    except Exception as _e:
        print(f"⚠️  2Captcha balance check not started: {type(_e).__name__}: {_e}")


# ── CTCL vs NMASS: which MCX file supplies "Margin Limit" (MCX 90% or MCX Deposit) ──────────────────────────
# One switch for everybody, kept in a small JSON file the run itself reads (core/ctcl_mcx_source.py).
@app.get("/ctcl/mcx-source")
def ctcl_mcx_source_get():
    """Current MCX source for CTCL vs NMASS: {source: "mcx90"|"deposit", label, by, at}."""
    from core import ctcl_mcx_source as _ms
    return _ms.describe()


class _CtclMcxSourceBody(_SA_BaseModel):
    source: str


@app.post("/ctcl/mcx-source")
def ctcl_mcx_source_set(body: _CtclMcxSourceBody, request: Request):
    """Choose the MCX file CTCL vs NMASS uses (takes effect on the next run)."""
    from core import ctcl_mcx_source as _ms
    _who, _adm = _actor(request)
    try:
        _ms.set_source(body.source, _who or "")
    except ValueError as _e:
        return {"ok": False, "error": str(_e)}
    except OSError as _e:
        return {"ok": False, "error": f"Could not save the choice: {_e}"}
    return {"ok": True, **_ms.describe()}


_ctcl_last_answered_at: dict = {}   # {source: timestamp} — per-source, NOT shared across
                                     # NMASS/NCDEX (a shared single timestamp used to leak a
                                     # false "previous attempt failed" reason from one login
                                     # context into the other's very first captcha attempt).

def _ctcl_captcha_dir(pending_only=False):
    """Folder holding the live NMASS/NCDEX .captcha_state. Candidates: CTCL's
    folder, CLI's folder (cli_download.py points NMASS_SAVE_DIR there), MS15's
    folder. Returns the first whose state is PENDING, else the CTCL default."""
    import os as _o, json as _j
    default = _o.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    cands = [default, _o.getenv("CLI_FOLDER", ""), _o.getenv("MARGIN_SHORTAGE_FOLDER", "")]
    for d in cands:
        if not d:
            continue
        try:
            with open(_o.path.join(d, ".captcha_state"), encoding="utf-8") as f:
                if _j.load(f).get("state") == "PENDING":
                    return d
        except Exception:
            continue
    return default


@app.get("/ctcl/captcha/pending")
def ctcl_captcha_pending(request: Request):
    """Check if the NMASS or NCDEX (NCFE) captcha is awaiting dashboard input.
    Returns {pending, image_b64, detected, reason, source}.
    If the Python script doesn't write a reason (old version), we infer one from
    how recently an answer was submitted for THIS SAME source (retry fallback) —
    scoped per source so answering an NMASS captcha doesn't make a brand-new
    NCDEX captcha falsely claim a previous attempt failed."""
    import base64 as _b64, json as _cj, os as _co, time as _ct
    global _ctcl_last_answered_at
    # The login script writes its popup state into NMASS_SAVE_DIR, which is
    # the CTCL folder for CTCL runs but CLI's OWN folder for Client Prudential
    # Limit (cli_download.py). Scan every candidate folder and use the first
    # one with a PENDING captcha — otherwise a CLI-triggered captcha never
    # appeared in the dashboard and the run sat waiting for it.
    _dir = _ctcl_captcha_dir(pending_only=True)
    _sf  = _co.path.join(_dir, ".captcha_state")
    _img = _co.path.join(_dir, ".captcha_nmass.png")
    try:
        _st = _cj.load(open(_sf, encoding="utf-8"))
        if _st.get("state") != "PENDING":
            return {"pending": False}
        _pipe = "cli" if _dir == _co.getenv("CLI_FOLDER", "\0") else "ms15" if _dir == _co.getenv("MARGIN_SHORTAGE_FOLDER", "\0") else "ctcl"
        if not _popup_visible(request, _sf, [_pipe]):
            return {"pending": False}
        img_b64 = _b64.b64encode(open(_img, "rb").read()).decode() if _co.path.isfile(_img) else None
        reason = _st.get("reason", "")
        # source: "ncdex" = NCDEX portal login (ncfe.ncdex.com), "nmass" = NMASS portal
        source = _st.get("source", "nmass")
        _last = _ctcl_last_answered_at.get(source, 0)
        # Fallback: if script didn't write a reason but WE answered THIS source recently → it's a retry
        if not reason and _last and (_ct.time() - _last) < 90:
            reason = "⚠️ Previous attempt failed — try again"
        return {"pending": True, "image_b64": img_b64, "detected": _st.get("detected", ""), "reason": reason, "source": source}
    except Exception:
        return {"pending": False}

class _CTCLCaptchaAnswer(_SA_BaseModel):
    text: str

@app.post("/ctcl/captcha/answer")
def ctcl_captcha_answer(a: _CTCLCaptchaAnswer):
    """Submit typed captcha text to the waiting NMASS/NCDEX script."""
    import os as _co, time as _ct, json as _cj
    global _ctcl_last_answered_at
    _dir = _ctcl_captcha_dir(pending_only=True)
    _sf  = _co.path.join(_dir, ".captcha_state")
    _af  = _co.path.join(_dir, ".captcha_answer")
    try:
        # Read which source we're answering BEFORE overwriting the state file,
        # so the retry-fallback timestamp above is recorded against the right one.
        _source = "nmass"
        try:
            _source = _cj.load(open(_sf, encoding="utf-8")).get("source", "nmass")
        except Exception:
            pass
        with open(_af, "w", encoding="utf-8") as f: f.write(a.text.strip())
        with open(_sf, "w", encoding="utf-8") as f:
            _cj.dump({"state": "ANSWERED"}, f)
        _ctcl_last_answered_at[_source] = _ct.time()   # record answer time for retry-reason fallback
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/sa/las-checkpoint")
def sa_las_checkpoint():
    """Return the last LAS checkpoint (step + status). Used by dashboard resume banner."""
    try:
        import sys as _ck_sys
        _p_ = _sa_os.path.join(_sa_os.path.dirname(__file__), "sa_codes")
        if _p_ not in _ck_sys.path: _ck_sys.path.insert(0, _p_)
        import importlib
        ckpt_mod = importlib.import_module("_checkpoint")
        data = ckpt_mod.read_las()
        return data or {}
    except Exception as e:
        return {"error": str(e)}



@app.get("/sa/sa-checkpoint")
def sa_sa_checkpoint():
    """Return the last SA Code-2 checkpoint. Used by dashboard resume banner."""
    try:
        import sys as _ck_sys
        _p_ = _sa_os.path.join(_sa_os.path.dirname(__file__), "sa_codes")
        if _p_ not in _ck_sys.path: _ck_sys.path.insert(0, _p_)
        import importlib
        ckpt_mod = importlib.import_module("_checkpoint")
        data = ckpt_mod.read_sa()
        return data or {}
    except Exception as e:
        return {"error": str(e)}


@app.get("/sa/post-email-stream")
def sa_post_email_stream():
    """SSE: run run_post_email_LAS4.py and stream output line by line to dashboard.
    Use after LAS4 emails were sent but the post-email Scenario/Top-100/backup steps crashed.

    Writes a real .log file (same LOG_DIR/naming convention as every other SA
    step) and records run status for SA-04 on completion. Before this, the
    output existed ONLY in the browser's SSE stream — so once the page moved
    on there was no trace the run ever happened, the Logs modal showed nothing,
    and the SA-04 tile stayed stuck on whatever the crashed LAS4 run left
    behind ("LAS not recorded") even after a fully successful recovery."""
    import json as _pe_json, subprocess as _pe_sub, sys as _pe_sys, datetime as _pe_dt
    from core.short_allocation import (LOG_DIR as _PE_LOG_DIR, _record as _pe_record,
                                        _archive_to_db as _pe_archive)
    sa_codes_dir = _sa_os.path.join(_sa_os.path.dirname(__file__), "sa_codes")
    script = _sa_os.path.join(sa_codes_dir, "run_post_email_LAS4.py")
    _tag = "SA-04"          # post-email steps only exist for run 4 (LAS4.py)
    def gen():
        _tail = []
        _sa_os.makedirs(_PE_LOG_DIR, exist_ok=True)
        _ts = _pe_dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        logp = _sa_os.path.join(_PE_LOG_DIR, f"{_tag}__run_post_email_LAS4__{_ts}.log")
        try:
            env = {**_sa_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                   "PYTHONUNBUFFERED": "1"}
            with open(logp, "w", encoding="utf-8", errors="replace") as lf:
                lf.write(f"# run_post_email_LAS4.py  tag={_tag}  start={_ts}\n")
                lf.flush()
                proc = _pe_sub.Popen(
                    [_pe_sys.executable, "-u", script],
                    cwd=sa_codes_dir, stdout=_pe_sub.PIPE, stderr=_pe_sub.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                )
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    if line:
                        lf.write(line + "\n"); lf.flush()
                        yield f"data: {_pe_json.dumps({'type':'log','msg':line})}\n\n"
                        _tail.append(line)
                        if len(_tail) > 20:
                            _tail.pop(0)
                proc.wait()
                rc = proc.returncode
                lf.write(f"\n# exit code: {rc}\n")
            if rc != 0:
                # AI (Groq) plain-English fallback — same as every other pipeline now has,
                # instead of just showing a bare exit code.
                try:
                    from core.error_translator import translate_error
                    _ai_msg = translate_error("Short Allocation — Post-Email Steps", _tail)
                except Exception:
                    _ai_msg = None
                if _ai_msg:
                    yield f"data: {_pe_json.dumps({'type':'note','msg':_ai_msg})}\n\n"
            # Record status so the SA-04 tile / History reflect what actually
            # happened. A successful post-email recovery means LAS is finished
            # end-to-end (emails sent, Top-100/Top-15 built, backup done), so
            # code4 is marked done and the stale failure state is cleared.
            try:
                if rc == 0:
                    _pe_record(_tag, code4="done", overall="done",
                               failed_step=None, failed_step_label=None,
                               log_code4=_sa_os.path.basename(logp))
                else:
                    _pe_record(_tag, code4="error", overall="error",
                               failed_step="run_post_email_LAS4.py",
                               failed_step_label="LAS Funding (post-email steps)",
                               log_code4=_sa_os.path.basename(logp))
            except Exception as _re:
                yield f"data: {_pe_json.dumps({'type':'log','msg':f'⚠️ Could not record run status: {_re}'})}\n\n"
            try:
                _pe_archive(_tag, "log", logp)
            except Exception:
                pass
            yield f"data: {_pe_json.dumps({'type':'log','msg':f'📝 Log saved → {_sa_os.path.basename(logp)}'})}\n\n"
            yield f"data: {_pe_json.dumps({'type':'done','ok': rc == 0, 'msg': 'Post-email steps completed' if rc == 0 else f'Exit code {rc}'})}\n\n"
        except Exception as e:
            try:
                with open(logp, "a", encoding="utf-8", errors="replace") as lf:
                    lf.write(f"\n# stream error: {e}\n")
            except Exception:
                pass
            yield f"data: {_pe_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return _SA_Stream(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/sa/mcx-deposit-stream")
def sa_mcx_deposit_stream(request: Request):
    """SSE: run mcx_deposit_downloader.py standalone and stream output to dashboard.
    Downloads RSK383 (Cash Collateral Deposit Limit) from eclear.mcxccl.com.
    RSA SecurID token popup appears on desktop — user enters manually.
    Saves as MCX_DEPOSIT_HH.MM.xlsx in the SA output folder.
    Full output is written to logs/short_allocation/MCX_DEPOSIT__<ts>.log (appears in Logs modal).
    Uses daemon thread so log is preserved even if browser tab is closed mid-download."""
    import json as _dep_json
    from core.pipelines.mcx_deposit_download import stream_mcx_deposit_download
    _sa_clear_popup_state()  # drop any stale unanswered captcha/token from a previous stopped/crashed run
    # Record WHO clicked, so the RSA token popup is shown to that person (not only to admins). This button is a
    # standalone download — no lock / action slot stores its requester — so it registers itself in _POPUP_OWNERS.
    _who, _adm = _actor(request)
    _note_who(request)
    if _who:
        _POPUP_OWNERS["sa"] = _who
    def gen():
        try:
            for ev in stream_mcx_deposit_download():
                yield f"data: {_dep_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_dep_json.dumps({'type':'error','msg':str(e)})}\n\n"
        finally:
            if _who and _POPUP_OWNERS.get("sa") == _who:    # download over (or stream closed): stop pointing at this user
                _POPUP_OWNERS.pop("sa", None)
        yield 'data: {"type":"end"}\n\n'
    return _SA_Stream(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/ctcl/download/mcx-deposit-stream")
def ctcl_mcx_deposit_download_stream(request: Request):
    """SSE: download the MCX Deposit file (RSK383, "UCC Code" + "Margin Limit") INTO the CTCL vs NMASS folder.
    Same downloader as Short Allocation's "MCX Deposit" button; the RSA token popup is shown to whoever clicked."""
    import json as _cd_json
    from core.pipelines.mcx_deposit_download import stream_mcx_deposit_download
    from core.pipelines.mcx90_download import _pipe_folder as _cd_folder
    _ctcl_clear_popup_state()
    _who, _adm = _actor(request)
    _note_who(request)
    if _who:
        _POPUP_OWNERS["ctcl"] = _who
    def gen():
        try:
            for ev in stream_mcx_deposit_download(output_dir=_cd_folder("ctcl"), caller="ctcl"):
                yield f"data: {_cd_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_cd_json.dumps({'type':'error','msg':str(e)})}\n\n"
        finally:
            if _who and _POPUP_OWNERS.get("ctcl") == _who:
                _POPUP_OWNERS.pop("ctcl", None)
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


# ── CTCL download + stop endpoints ──────────────────────────────────────────

@app.get("/ctcl/download/nmass-stream")
def ctcl_nmass_download_stream():
    """SSE: run NMASS_NCFE_API_MIX.PY and stream output to dashboard.
    Script path: ctcl_codes/NMASS_NCFE_API_MIX.PY (inside Claude folder).
    Supports cancel via /ctcl/stop (sets _PB['cancel']=True)."""
    import json as _nd_json
    from core.pipelines.nmass_download import stream_nmass_download
    _ctcl_clear_popup_state()  # drop any stale unanswered captcha/token from a previous stopped/crashed run
    def gen():
        try:
            for ev in stream_nmass_download():
                yield f"data: {_nd_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_nd_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/ctcl/download/mcx90-stream")
def ctcl_mcx90_download_stream():
    """SSE: run MCX_90_PERCENT_FILE_API.py and stream output to dashboard.
    Script path: ctcl_codes/MCX_90_PERCENT_FILE_API.py (inside Claude folder).
    RSA SecurID token is entered via the web dashboard (state-file pattern)."""
    import json as _m9_json
    from core.pipelines.mcx90_download import stream_mcx90_download
    _ctcl_clear_popup_state()  # drop any stale unanswered captcha/token from a previous stopped/crashed run
    def gen():
        try:
            for ev in stream_mcx90_download():
                yield f"data: {_m9_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_m9_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.post("/ctcl/stop")
def ctcl_stop(request: Request):
    """Stop any running CTCL pipeline or download (NMASS, MCX90, main run).
    Sets CTCL's own _PB['ctcl'] cancel flag and terminates its tracked
    subprocess if one is running — never touches MS15 or CLI's independent slots.
    Only the run's own starter or an admin may do this — see _can_stop()."""
    allowed, why = _can_stop("px_ctcl", request)
    if not allowed:
        return JSONResponse({"ok": False, "error": why}, status_code=403)
    from core.pipeline_box import stop_pipeline as _ctcl_sp
    _ctcl_sp("ctcl")
    _ctcl_clear_popup_state()
    return {"ok": True}


def _ctcl_clear_popup_state():
    """Delete any leftover captcha/token popup state files so a stopped run's
    unanswered prompt can't reappear and get shown for a later, unrelated run.
    (This is exactly what caused the NCDEX captcha to pop back up when
    running MCX Deposit afterwards.)"""
    import os as _cco
    _dir = _cco.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    for _fn in (".captcha_state", ".captcha_answer", ".captcha_nmass.png",
                ".token_state", ".token_answer"):
        try: _cco.remove(_cco.path.join(_dir, _fn))
        except OSError: pass


# ── RSA SecurID token endpoints (MCX 90% CTCL side) ─────────────────────────

@app.get("/ctcl/token/pending")
def ctcl_token_pending(request: Request):
    """Poll: is MCX 90% waiting for an RSA token? Reads .token_state in CTCL_NMASS_FOLDER."""
    import json as _tj, os as _tos, time as _tt
    _dir = _tos.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    _sf  = _tos.path.join(_dir, ".token_state")
    try:
        st = _tj.load(open(_sf, encoding="utf-8"))
        if st.get("state") == "PENDING" and _popup_visible(request, _sf, ["ctcl"]):
            return {"pending": True, "label": st.get("label","MCX 90%"),
                    "attempt": st.get("attempt",1), "max_attempts": st.get("max_attempts",3),
                    "error_msg": st.get("error_msg")}
    except Exception:
        pass
    return {"pending": False}


@app.post("/ctcl/token/answer")
async def ctcl_token_answer(req: Request):
    """Submit RSA token answer for MCX 90% download."""
    import json as _tj, os as _tos
    data = await req.json()
    token = (data.get("token") or "").strip()
    if not token:
        return {"ok": False, "error": "empty token"}
    _dir = _tos.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    _sf  = _tos.path.join(_dir, ".token_state")
    _af  = _tos.path.join(_dir, ".token_answer")
    try:
        open(_af, "w", encoding="utf-8").write(token)
        _tj.dump({"state": "ANSWERED"}, open(_sf, "w", encoding="utf-8"))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── RSA SecurID token endpoints (MCX Deposit SA side) ───────────────────────

@app.get("/sa/token/pending")
def sa_token_pending(request: Request):
    """Poll: is MCX Deposit waiting for an RSA token? Reads .token_state in SA_OUTPUT_DIR.

    This file is shared with Client Prudential Limit's own MCX Web Allocation
    button (both ultimately call sa_codes/mcx_deposit_downloader.py's
    download_mcx_deposit()) — "caller" (stamped by _token_popup() from the
    MCX_DEPOSIT_CALLER env var the invoking pipeline sets) tells the dashboard
    which box actually triggered this specific request, purely for display."""
    import json as _stj, os as _sos
    _dir = _sos.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    _sf  = _sos.path.join(_dir, ".token_state")
    try:
        st = _stj.load(open(_sf, encoding="utf-8"))
        if st.get("state") == "PENDING" and _popup_visible(request, _sf, [st.get("caller", "sa") or "sa"]):
            return {"pending": True, "label": st.get("label","MCX Deposit"),
                    "attempt": st.get("attempt",1), "max_attempts": st.get("max_attempts",3),
                    "error_msg": st.get("error_msg"), "caller": st.get("caller","sa")}
    except Exception:
        pass
    return {"pending": False}


@app.post("/sa/token/answer")
async def sa_token_answer(req: Request):
    """Submit RSA token answer for MCX Deposit download."""
    import json as _stj, os as _sos
    data = await req.json()
    token = (data.get("token") or "").strip()
    if not token:
        return {"ok": False, "error": "empty token"}
    _dir = _sos.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
    _sf  = _sos.path.join(_dir, ".token_state")
    _af  = _sos.path.join(_dir, ".token_answer")
    try:
        open(_af, "w", encoding="utf-8").write(token)
        _stj.dump({"state": "ANSWERED"}, open(_sf, "w", encoding="utf-8"))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── CTCL readiness (file presence check for dashboard pre-run badge) ─────────

@app.get("/ctcl/readiness")
def ctcl_readiness():
    """Check whether all NMASS/CTCL input files are present in CTCL_NMASS_FOLDER.
    Returns {"items": [{"key":str, "ok":bool, "example":str}]}
    Update Limits and MCX 90% added — core/pipelines/ctcl_vs_nmass.py already
    requires both (see UPDATE_LIMITS_FILE / MCX_RANDOM_NAME_FILE there), the
    readiness banner just never checked for them.

    KAMBALA is appended HERE rather than inside _readiness_for() because that
    helper is shared with /ms15/readiness, and Margin Shortage has no Kambala
    file — adding it there would show MS15 a permanently-missing input.
    """
    _dir = os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    res = _readiness_for(_dir)
    # Permanent reference file: presence only, no age rule — it is never
    # re-downloaded, so "old" is the normal and correct state for it.
    try:
        from core.pipelines.ctcl_vs_nmass import kambala_name as _kb_name
        _kb = _kb_name()
        _kb_ok = os.path.isfile(os.path.join(_dir, _kb))
        res["items"].append({
            "key": "KAMBALA_REFERENCE", "ok": _kb_ok, "example": _kb,
            "age_min": None,
            "why": "" if _kb_ok else "not present — permanent reference file, must not be deleted",
            "file": _kb if _kb_ok else ""})
    except Exception as _kb_e:
        print(f"[ctcl] readiness: KAMBALA check skipped: {type(_kb_e).__name__}: {_kb_e}", flush=True)

    # MTF risk report — needed by EVERY run (MTFProfit/MTFLoss deduction). Uploaded by hand once a day, reused by runs 1-5,
    # backed up and removed after run 5 — so it is "upload_only" (never part of the download shortcut).
    try:
        import datetime as _mtdt, glob as _mtglob
        _mtname = f"MTF_RISK_REPORT_{_mtdt.date.today().strftime('%d%m%Y')}.csv"
        _mtok = os.path.isfile(os.path.join(_dir, _mtname))
        res["items"].append({
            "key": "MTF_RISK_REPORT", "ok": _mtok, "example": f"MTF risk report ({_mtname})",
            "age_min": None, "upload_only": True,
            "why": "" if _mtok else "not present — upload today's file once; runs 1-5 reuse it",
            "file": _mtname if _mtok else ""})
    except Exception as _mte:
        print(f"[ctcl] readiness: MTF risk report check skipped: {type(_mte).__name__}: {_mte}", flush=True)

    # T5 DEBIT report — compulsory on RUN 1 ONLY (pre_run_file_check() in ctcl_vs_nmass.py requires it just for run 1;
    # by run 2+ it has been swept into the backup folder). It is uploaded by hand, never downloaded, so it is flagged
    # "upload_only": the dashboard lists it as missing but it must not stop the "skip download" shortcut.
    try:
        import datetime as _t5dt
        _cnt, _next = os.path.join(_dir, "run_counter.txt"), 1
        try:
            _d, _n = open(_cnt).read().strip().split("|")
            if _d == _t5dt.date.today().strftime("%Y-%m-%d"):
                _next = int(_n) + 1
        except Exception:
            _next = 1
        if _next > 5:
            _next = 1
        res["next_run"] = _next
        if _next == 1:
            _t5name = f"T5DEBIT_{_t5dt.date.today().strftime('%d%m%Y')}.csv"
            _t5ok = os.path.isfile(os.path.join(_dir, _t5name))
            res["items"].append({
                "key": "T5_DEBIT_REPORT", "ok": _t5ok, "example": f"T5 DEBIT report ({_t5name})",
                "age_min": None, "upload_only": True,
                "why": "" if _t5ok else "not present — compulsory for Run 1, upload it before running",
                "file": _t5name if _t5ok else ""})
    except Exception as _t5e:
        print(f"[ctcl] readiness: T5 DEBIT check skipped: {type(_t5e).__name__}: {_t5e}", flush=True)
    return res


def _readiness_for(_dir, pipe="ctcl"):
    """Shared by /ctcl/readiness and /ms15/readiness — both boxes download the
    same five inputs into their own folder and both now apply the SAME age
    limits, so an old file can never be silently reused as if freshly fetched.

    An item is only "ok" when it is PRESENT **and** RECENT:
        CM / FO / CO collaterals    ≤ 4 min   (stamp in the filename)
        ClientNetEffectiveDeposits  ≤ 4 min   (no stamp — modified time)
        MCX 90%                     ≤ 45 min  (stamp in the filename)
        Update Limits               presence only — it's maintained by hand,
                                    not downloaded, so an age rule would be wrong
    Each item carries age_min/why so the banner can say WHY something isn't
    usable ("18.4 min old — older than the 4 min limit") instead of just
    "missing", which would be misleading when the file is sitting right there.
    """
    import os as _ros
    from core import file_freshness as _ff

    items = []
    _collats = [
        ("CM_CLIENT_LEVEL",               "CM_ClientLevelCollaterals",  "collateral"),
        ("FO_CLIENT_LEVEL",               "FO_ClientLevelCollaterals",  "collateral"),
        ("CO_CLIENT_LEVEL",               "CO_ClientLevelCollaterals",  "collateral"),
        # No stamp in this one's name — falls back to modified time inside check().
        ("CLIENT_NET_EFFECTIVE_DEPOSITS", "ClientNetEffectiveDeposits", "mtime"),
    ]
    for key, prefix, kind in _collats:
        path = _ff.newest(_dir, lambda f, p=prefix: f.upper().startswith(p.upper())
                          and f.lower().endswith(".csv"), kind="collateral")
        r = _ff.check(path, kind=kind) if path else {
            "ok": False, "age_min": None, "name": "", "why": "not present"}
        items.append({"key": key, "ok": r["ok"], "example": prefix,
                      "age_min": r.get("age_min"), "why": r.get("why"),
                      "file": r.get("name")})

    # Update Limits — presence only, on purpose (see docstring).
    _ul = _ff.newest(_dir, lambda f: f.lower().startswith("update limits")
                     and f.lower().endswith(".xlsx"), kind="mtime")
    items.append({"key": "UPDATE_LIMITS", "ok": bool(_ul), "example": "Update Limits",
                  "age_min": None, "why": "" if _ul else "not present",
                  "file": _ros.path.basename(_ul) if _ul else ""})

    # MCX 90% — random prefix, then YYYYMMDDHHMMSS (alkNAVlSFN-20260729145211.xlsx).
    # Identified as "any .xlsx that isn't Update Limits", matching
    # core/pipelines/ctcl_vs_nmass.py's MCX_RANDOM_NAME_FILE logic.
    # Both MCX files may be in the folder now (the download button fetches both), so only a file whose header
    # columns match the SELECTED source counts - the other one must not turn this item green.
    from core import stale_files as _sfm
    from core import ctcl_mcx_source as _msrc
    _want = _msrc.get_source(pipe)
    _cols = _sfm.MCX_KINDS[_want]
    _mcx = _ff.newest(_dir, lambda f: f.lower().endswith(".xlsx") and "update limits" not in f.lower()
                      and (_sfm._MCX_STAMPED.search(f) or f.upper().startswith(_sfm._MCX_OWN_DEPOSIT))
                      and _sfm.has_columns(_ros.path.join(_dir, f), _cols), kind="mcx90")
    _r = _ff.check(_mcx, kind="mcx90") if _mcx else {
        "ok": False, "age_min": None, "name": "", "why": "not present"}
    items.append({"key": "MCX_90_PERCENT", "ok": _r["ok"],
                  "example": "MCX 90% (random filename, .xlsx)",
                  "age_min": _r.get("age_min"), "why": _r.get("why"),
                  "file": _r.get("name")})

    return {"items": items, "folder": _dir,
            "max_age_min": {"collateral": _ff.max_age_collateral(),
                            "mcx90": _ff.max_age_mcx90()}}


# ── Margin Shortage (15-min) download + captcha/token + readiness ──────────
# Mirrors the CTCL block above exactly, but reads/writes MARGIN_SHORTAGE_FOLDER
# and calls the shared download generators with pipe="ms15" instead of "ctcl" —
# see core/pipelines/nmass_download.py + mcx90_download.py for the pipe-aware
# folder selection + MCX90 cross-copy logic. Generic /px/* endpoints (status,
# config/credentials, stop-via-taskkill, logs, backup) already work for
# pipe=ms15 once it's registered in PIPELINES — only the download streams,
# captcha/token popup handshake, and readiness check need dedicated routes,
# same reason CTCL needs them instead of generic ones.

@app.get("/ms15/download/nmass-stream")
def ms15_nmass_download_stream():
    """SSE: run NMASS_NCFE_API_MIX.PY (pipe=ms15 → saves into MARGIN_SHORTAGE_FOLDER)."""
    import json as _mn_json
    from core.pipelines.nmass_download import stream_nmass_download
    _ms15_clear_popup_state()
    def gen():
        try:
            for ev in stream_nmass_download(pipe="ms15"):
                yield f"data: {_mn_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_mn_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/ms15/download/mcx90-stream")
def ms15_mcx90_download_stream():
    """SSE: run MCX_90_PERCENT_FILE_API.py (pipe=ms15 → saves into MARGIN_SHORTAGE_FOLDER,
    auto-skips if today's file already arrived via CTCL's cross-copy at a paired run time)."""
    import json as _mm_json
    from core.pipelines.mcx90_download import stream_mcx90_download
    _ms15_clear_popup_state()
    def gen():
        try:
            for ev in stream_mcx90_download(pipe="ms15"):
                yield f"data: {_mm_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_mm_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


def _mcx_both_stream(pipe: str, request: Request):
    """SSE: download the MCX 90% file AND the MCX Deposit file in ONE go - one TCS BANCS login, one RSA token, one
    wait loop, one log (core/pipelines/mcx90_download.stream_mcx90_download(both=True)). The files land in `pipe`'s
    own folder ("ctcl" or "ms15") and the log is that box's own."""
    import json as _cb_json
    from core.pipelines.mcx90_download import stream_mcx90_download
    (_ctcl_clear_popup_state if pipe == "ctcl" else _ms15_clear_popup_state)()
    _who, _adm = _actor(request)
    _note_who(request)
    if _who:
        _POPUP_OWNERS[pipe] = _who
    def gen():
        try:
            for ev in stream_mcx90_download(pipe=pipe, both=True):
                yield f"data: {_cb_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_cb_json.dumps({'type':'error','msg':str(e)})}\n\n"
        finally:
            if _who and _POPUP_OWNERS.get(pipe) == _who:
                _POPUP_OWNERS.pop(pipe, None)
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/ctcl/download/mcx-both-stream")
def ctcl_mcx_both_stream(request: Request):
    """Download both MCX files (90% + Deposit) into the CTCL vs NMASS folder."""
    return _mcx_both_stream("ctcl", request)


@app.get("/ms15/download/mcx-both-stream")
def ms15_mcx_both_stream(request: Request):
    """Download both MCX files (90% + Deposit) into the Margin Shortage folder."""
    return _mcx_both_stream("ms15", request)


@app.get("/ms15/mcx-source")
def ms15_mcx_source_get():
    """Current MCX file choice for Margin Shortage: {source: "mcx90"|"deposit", label, by, at}."""
    from core import ctcl_mcx_source as _ms
    return _ms.describe("ms15")


@app.post("/ms15/mcx-source")
def ms15_mcx_source_set(body: _CtclMcxSourceBody, request: Request):
    """Choose the MCX file Margin Shortage uses (takes effect on the next run)."""
    from core import ctcl_mcx_source as _ms
    _who, _adm = _actor(request)
    try:
        _ms.set_source(body.source, _who or "", pipe="ms15")
    except ValueError as _e:
        return {"ok": False, "error": str(_e)}
    except OSError as _e:
        return {"ok": False, "error": f"Could not save the choice: {_e}"}
    return {"ok": True, **_ms.describe("ms15")}


@app.get("/ms15/download/mcx-deposit-stream")
def ms15_mcx_deposit_download_stream(request: Request):
    """SSE: download the MCX Deposit file ("UCC Code" + "Margin Limit") INTO the Margin Shortage folder."""
    import json as _cd_json
    from core.pipelines.mcx_deposit_download import stream_mcx_deposit_download
    from core.pipelines.mcx90_download import _pipe_folder as _cd_folder
    _ms15_clear_popup_state()
    _who, _adm = _actor(request)
    _note_who(request)
    if _who:
        _POPUP_OWNERS["ms15"] = _who
    def gen():
        try:
            for ev in stream_mcx_deposit_download(output_dir=_cd_folder("ms15"), caller="ms15"):
                yield f"data: {_cd_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_cd_json.dumps({'type':'error','msg':str(e)})}\n\n"
        finally:
            if _who and _POPUP_OWNERS.get("ms15") == _who:
                _POPUP_OWNERS.pop("ms15", None)
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.post("/ms15/stop")
def ms15_stop(request: Request):
    """Stop any running Margin Shortage download or the main pipeline run —
    only MS15's own independent slot, never CTCL's or CLI's. Only the run's
    own starter or an admin may do this — see _can_stop()."""
    allowed, why = _can_stop("px_ms15", request)
    if not allowed:
        return JSONResponse({"ok": False, "error": why}, status_code=403)
    from core.pipeline_box import stop_pipeline as _ms15_sp
    _ms15_sp("ms15")
    _ms15_clear_popup_state()
    return {"ok": True}


def _ms15_clear_popup_state():
    """Delete any leftover captcha/token popup state files so a stopped run's
    unanswered prompt can't reappear later — same fix as CTCL's
    _ctcl_clear_popup_state, scoped to MARGIN_SHORTAGE_FOLDER."""
    import os as _mco
    _dir = _mco.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    for _fn in (".captcha_state", ".captcha_answer", ".captcha_nmass.png",
                ".token_state", ".token_answer"):
        try: _mco.remove(_mco.path.join(_dir, _fn))
        except OSError: pass


_ms15_last_answered_at: dict = {}

@app.get("/ms15/captcha/pending")
def ms15_captcha_pending(request: Request):
    """Check if the NMASS or NCDEX (NCFE) captcha is awaiting dashboard input,
    for the Margin Shortage box's own NMASS download. Same logic as
    /ctcl/captcha/pending, scoped to MARGIN_SHORTAGE_FOLDER."""
    import base64 as _b64, json as _cj, os as _co, time as _ct
    global _ms15_last_answered_at
    _dir = _co.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    _sf  = _co.path.join(_dir, ".captcha_state")
    _img = _co.path.join(_dir, ".captcha_nmass.png")
    try:
        _st = _cj.load(open(_sf, encoding="utf-8"))
        if _st.get("state") != "PENDING":
            return {"pending": False}
        if not _popup_visible(request, _sf, ["ms15"]):
            return {"pending": False}
        img_b64 = _b64.b64encode(open(_img, "rb").read()).decode() if _co.path.isfile(_img) else None
        reason = _st.get("reason", "")
        source = _st.get("source", "nmass")
        _last = _ms15_last_answered_at.get(source, 0)
        if not reason and _last and (_ct.time() - _last) < 90:
            reason = "⚠️ Previous attempt failed — try again"
        return {"pending": True, "image_b64": img_b64, "detected": _st.get("detected", ""), "reason": reason, "source": source}
    except Exception:
        return {"pending": False}

@app.post("/ms15/captcha/answer")
def ms15_captcha_answer(a: _CTCLCaptchaAnswer):
    """Submit typed captcha text to the waiting NMASS/NCDEX script (Margin Shortage box)."""
    import os as _co, time as _ct, json as _cj
    global _ms15_last_answered_at
    _dir = _co.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    _sf  = _co.path.join(_dir, ".captcha_state")
    _af  = _co.path.join(_dir, ".captcha_answer")
    try:
        _source = "nmass"
        try:
            _source = _cj.load(open(_sf, encoding="utf-8")).get("source", "nmass")
        except Exception:
            pass
        with open(_af, "w", encoding="utf-8") as f: f.write(a.text.strip())
        with open(_sf, "w", encoding="utf-8") as f:
            _cj.dump({"state": "ANSWERED"}, f)
        _ms15_last_answered_at[_source] = _ct.time()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/ms15/token/pending")
def ms15_token_pending(request: Request):
    """Poll: is MCX 90% (Margin Shortage box) waiting for an RSA token?"""
    import json as _tj, os as _tos
    _dir = _tos.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    _sf  = _tos.path.join(_dir, ".token_state")
    try:
        st = _tj.load(open(_sf, encoding="utf-8"))
        if st.get("state") == "PENDING" and _popup_visible(request, _sf, ["ms15"]):
            return {"pending": True, "label": st.get("label","MCX 90%"),
                    "attempt": st.get("attempt",1), "max_attempts": st.get("max_attempts",3),
                    "error_msg": st.get("error_msg")}
    except Exception:
        pass
    return {"pending": False}


@app.post("/ms15/token/answer")
async def ms15_token_answer(req: Request):
    """Submit RSA token answer for MCX 90% download (Margin Shortage box)."""
    import json as _tj, os as _tos
    data = await req.json()
    token = (data.get("token") or "").strip()
    if not token:
        return {"ok": False, "error": "empty token"}
    _dir = _tos.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    _sf  = _tos.path.join(_dir, ".token_state")
    _af  = _tos.path.join(_dir, ".token_answer")
    try:
        open(_af, "w", encoding="utf-8").write(token)
        _tj.dump({"state": "ANSWERED"}, open(_sf, "w", encoding="utf-8"))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


_r1r2_last_answered_at: dict = {}

@app.get("/r1r2/captcha/pending")
def r1r2_captcha_pending(request: Request):
    """Check if the NMASS or NCDEX (NCFE) captcha is awaiting dashboard input,
    for the R1-R2 Comparison Report's Live Snapshot download.
    Same logic as /ms15/captcha/pending, scoped to R1R2_FOLDER.

    Must resolve the SAME folder core.pipelines.r1_r2_comparison.BASE_FOLDER
    (and nmass_download.py's _PIPE_FOLDER["r1r2"], which mirrors it) resolves
    to — that's where NMASS_NCFE_API_MIX.PY actually writes .captcha_state/
    .captcha_nmass.png. This used to hardcode a bare os.getenv fallback of
    D:\\R1_R2_COMPARISON, which only matched on a machine that happens to set
    R1R2_FOLDER to that exact path. On any machine where R1R2_FOLDER isn't
    set (falls back to <repo>\\data\\r1_r2_comparison instead), this endpoint
    was checking a folder nothing ever wrote to — so it always reported
    "pending: false" and the captcha popup never appeared, even while the
    download was genuinely sitting there waiting for an answer. Importing
    BASE_FOLDER directly guarantees this can never drift out of sync again."""
    import base64 as _b64, json as _cj, os as _co, time as _ct
    from core.pipelines.r1_r2_comparison import BASE_FOLDER as _dir
    global _r1r2_last_answered_at
    _sf  = _co.path.join(_dir, ".captcha_state")
    _img = _co.path.join(_dir, ".captcha_nmass.png")
    try:
        _st = _cj.load(open(_sf, encoding="utf-8"))
        if _st.get("state") != "PENDING":
            return {"pending": False}
        if not _popup_visible(request, _sf, ["r1r2"]):
            return {"pending": False}
        img_b64 = _b64.b64encode(open(_img, "rb").read()).decode() if _co.path.isfile(_img) else None
        reason = _st.get("reason", "")
        source = _st.get("source", "nmass")
        _last = _r1r2_last_answered_at.get(source, 0)
        if not reason and _last and (_ct.time() - _last) < 90:
            reason = "⚠️ Previous attempt failed — try again"
        return {"pending": True, "image_b64": img_b64, "detected": _st.get("detected", ""), "reason": reason, "source": source}
    except Exception:
        return {"pending": False}

@app.post("/r1r2/captcha/answer")
def r1r2_captcha_answer(a: _CTCLCaptchaAnswer):
    """Submit typed captcha text to the waiting NMASS/NCDEX script (R1/R2 box).
    See r1r2_captcha_pending()'s docstring — must resolve the same folder."""
    import os as _co, time as _ct, json as _cj
    from core.pipelines.r1_r2_comparison import BASE_FOLDER as _dir
    global _r1r2_last_answered_at
    _sf  = _co.path.join(_dir, ".captcha_state")
    _af  = _co.path.join(_dir, ".captcha_answer")
    try:
        _source = "nmass"
        try:
            _source = _cj.load(open(_sf, encoding="utf-8")).get("source", "nmass")
        except Exception:
            pass
        with open(_af, "w", encoding="utf-8") as f: f.write(a.text.strip())
        with open(_sf, "w", encoding="utf-8") as f:
            _cj.dump({"state": "ANSWERED"}, f)
        _r1r2_last_answered_at[_source] = _ct.time()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/ms15/readiness")
def ms15_readiness():
    """Check whether all input files are present in MARGIN_SHORTAGE_FOLDER.
    Returns {"items": [{"key":str, "ok":bool, "example":str}]} — same shape
    as /ctcl/readiness, matching core/pipelines/margin_shortage_15min.py's
    pre_run_file_check() requirements (CM/FO/CO/NCDEX/RISK_REPORT/MCX90)."""
    import os as _ros, glob as _rg
    _dir = _ros.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    # Same age rules as CTCL (4 min collaterals, 45 min MCX 90%) — this box
    # runs 11 times a day, so reusing an old file here is even easier to do by
    # accident than on CTCL.
    _base = _readiness_for(_dir, "ms15")
    # MS15 has no "Update Limits"; it needs RISK_REPORT instead, which may live
    # in the shared common folder. RISK_REPORT is dropped once per day by hand,
    # NOT downloaded per run, so it is checked for PRESENCE only — an age limit
    # on it would fail every run after the first few minutes of the day.
    items = [it for it in _base["items"] if it["key"] != "UPDATE_LIMITS"]
    _rr = bool(_rg.glob(_ros.path.join(_dir, "RISK_REPORT_*.csv")))
    if not _rr:
        _common = _ros.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
        _rr = bool(_rg.glob(_ros.path.join(_common, "RISK_REPORT_*.csv")))
    items.insert(4, {"key": "RISK_REPORT", "ok": _rr, "example": "RISK_REPORT",
                     "age_min": None, "why": "" if _rr else "not present", "file": ""})
    # MCX item follows the "MCX file to use" choice of this box: a Deposit file has no stamp in its name, so
    # its age falls back to the file time (handled by file_freshness); only the label changes here.
    try:
        from core import ctcl_mcx_source as _ms
        if _ms.get_source("ms15") == "deposit":
            for it in items:
                if it["key"] == "MCX_90_PERCENT":
                    it["key"] = "MCX_DEPOSIT_FILE"
                    it["example"] = "MCX Deposit (.xlsx)"
    except Exception:
        pass
    return {"items": items, "folder": _dir, "max_age_min": _base["max_age_min"]}


# ── SAFE TEST RUN (Margin Shortage 15-min) ─────────────────────────────────
# A one-shot rehearsal of a FULL real run with every destination redirected to
# the tester. Arming writes a marker file; the pipeline consumes (deletes) it
# the moment the run commits, so it is structurally impossible for the flag to
# survive into a second run. See the long comment block above
# _safe_test_marker_path() in core/pipelines/margin_shortage_15min.py.
@app.get("/ms15/safe-test")
def ms15_safe_test_status():
    """Armed state + the exact redirect targets, so the UI can show the operator
    precisely where a test run's messages will land BEFORE they commit to it."""
    import os as _st
    from core.pipelines.margin_shortage_15min import is_safe_test_armed as _armed
    return {
        "armed": _armed(),
        "email_to": [e.strip() for e in _st.getenv("MS15_TEST_EMAIL_TO", "kartavya.dhillon@mastertrust.co.in").split(",") if e.strip()],
        "wa_to": _st.getenv("MS15_TEST_WA_TO", "+917889026088").strip(),
    }


@app.post("/ms15/safe-test/arm")
def ms15_safe_test_arm(password: str = ""):
    """Arm the NEXT Margin Shortage run as a safe test. Password-gated like every
    other state-changing MS15 route — arming changes where real messages go."""
    from core.short_allocation import check_password as _st_pw
    if not _st_pw(password):
        return {"ok": False, "error": "Wrong password"}
    from core.pipelines.margin_shortage_15min import arm_safe_test as _arm
    try:
        _arm()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "armed": True}


@app.post("/ms15/safe-test/disarm")
def ms15_safe_test_disarm():
    """Cancel an armed safe test. Deliberately NOT password-gated — disarming
    only ever moves toward normal live behaviour, and an operator who armed by
    mistake must always be able to undo it instantly."""
    from core.pipelines.margin_shortage_15min import disarm_safe_test as _disarm
    return {"ok": True, "armed": False, "was_armed": _disarm()}


@app.get("/ms15/shortage-wa-config")
def ms15_shortage_wa_config_get():
    """Read the recipient list for the MARGIN SHORTAGE ALERT image (an internal
    RM/branch team list — separate from the per-client Gupshup alerts, and
    separate from the unrelated Peak Margin report's own WA_RECIPIENTS).
    Backing store is just the MS15_SHORTAGE_WA_TO env var — no password
    needed to READ it (matches every other /px/config-style GET), only to save."""
    import os as _mwc
    return {"to": _mwc.getenv("MS15_SHORTAGE_WA_TO", "")}


def _ms15_normalize_wa_number(v: str) -> str:
    """A user only ever types the 10 local digits; this fills in +91
    automatically (mirrors process-probable-box.js's client-side
    _ms15NormalizePhone, kept here too so the cap/format is enforced no
    matter what calls this route)."""
    v = (v or "").strip()
    if not v:
        return ""
    if v.startswith("+"):
        return v
    digits = "".join(ch for ch in v if ch.isdigit())
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[2:]
    return ("+91" + digits) if digits else ""


@app.post("/ms15/shortage-wa-config")
def ms15_shortage_wa_config_set(to: str = "", password: str = ""):
    """Save the MARGIN SHORTAGE ALERT image's recipient list. MS15's box is
    native to dashboard.html (can't be edited on disk), so this is the button
    injected by process-probable-box.js — same password as Credentials."""
    from core.short_allocation import check_password as _mwc_pw
    if not _mwc_pw(password):
        return {"ok": False, "error": "Wrong password"}
    numbers = [_ms15_normalize_wa_number(n) for n in to.split(",")]
    numbers = [n for n in numbers if n]
    if len(numbers) > 10:
        return {"ok": False, "error": "Maximum 10 numbers allowed"}
    from core.pipeline_box import _write_env as _mwc_write
    _mwc_write({"MS15_SHORTAGE_WA_TO": ",".join(numbers)})
    return {"ok": True}


def _ctcl_parse_wa_entries(raw: str):
    """Parses a comma-separated list where each entry is either a bare number
    ("9811122233") or a number with a "#Name" comment ("9811122233#Kartavya"),
    so whoever added a number can tell whose it is later. Returns
    (clean_numbers: list[str], labels_csv: str) — labels_csv is "num:label,..."
    for entries that had a name, ready to store in its own env var. The
    number-only env var (CTCL_SHORTFALL_WA_TO etc.) NEVER carries the "#Name"
    part, so nothing downstream that sends the actual WhatsApp message needs
    to know this convention exists."""
    numbers, labels = [], []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "#" in entry:
            num_part, label_part = entry.split("#", 1)
        else:
            num_part, label_part = entry, ""
        num = _ms15_normalize_wa_number(num_part)
        label = label_part.strip()
        if not num:
            continue
        numbers.append(num)
        if label:
            labels.append(f"{num}:{label}")
    return numbers, ",".join(labels)


@app.get("/ctcl/wa-config")
def ctcl_wa_config_get():
    """Read the recipient lists for the Run 1 CASH RISK CODES dashboard images
    (core/pipelines/margin_shortfall_dashboard.py) — same pattern as MS15's own
    /ms15/shortage-wa-config. No password needed to READ (matches every other
    /px/config-style GET), only to save. "to" is the default/all-zones
    fallback list; to_delhi/to_mumbai/to_ludhiana are per-zone lists that,
    when set, receive ONLY that zone's image instead of the default list.
    The *_labels fields are "num:name,num:name" strings the console UI uses to
    redisplay each number's "#Name" comment — they're display-only, never
    used when actually sending."""
    import os as _cwc
    return {
        "to": _cwc.getenv("CTCL_SHORTFALL_WA_TO", ""),
        "to_labels": _cwc.getenv("CTCL_SHORTFALL_WA_TO_LABELS", ""),
        "to_delhi": _cwc.getenv("CTCL_SHORTFALL_WA_TO_DELHI", ""),
        "to_delhi_labels": _cwc.getenv("CTCL_SHORTFALL_WA_TO_DELHI_LABELS", ""),
        "to_mumbai": _cwc.getenv("CTCL_SHORTFALL_WA_TO_MUMBAI", ""),
        "to_mumbai_labels": _cwc.getenv("CTCL_SHORTFALL_WA_TO_MUMBAI_LABELS", ""),
        "to_ludhiana": _cwc.getenv("CTCL_SHORTFALL_WA_TO_LUDHIANA", ""),
        "to_ludhiana_labels": _cwc.getenv("CTCL_SHORTFALL_WA_TO_LUDHIANA_LABELS", ""),
    }


@app.post("/ctcl/wa-config")
def ctcl_wa_config_set(to: str = "", to_delhi: str = "", to_mumbai: str = "", to_ludhiana: str = "", password: str = ""):
    """Save the CASH RISK CODES dashboard's recipient lists — the button
    injected by process-probable-box.js on the CTCL vs NMASS box, mirroring
    MS15's. Same console password as every other 🔑 Credentials save.
    "to" is the default/all-zones fallback; the three to_* fields are
    per-zone overrides (each capped at 10 numbers, like "to"). Each entry may
    carry an optional "#Name" comment (see _ctcl_parse_wa_entries) which is
    split out into its own *_LABELS env var and never stored mixed in with
    the actual phone numbers."""
    from core.short_allocation import check_password as _cwc_pw
    if not _cwc_pw(password):
        return {"ok": False, "error": "Wrong password"}

    parsed = {
        "CTCL_SHORTFALL_WA_TO": _ctcl_parse_wa_entries(to),
        "CTCL_SHORTFALL_WA_TO_DELHI": _ctcl_parse_wa_entries(to_delhi),
        "CTCL_SHORTFALL_WA_TO_MUMBAI": _ctcl_parse_wa_entries(to_mumbai),
        "CTCL_SHORTFALL_WA_TO_LUDHIANA": _ctcl_parse_wa_entries(to_ludhiana),
    }
    for _k, (_nums, _labels) in parsed.items():
        if len(_nums) > 10:
            return {"ok": False, "error": "Maximum 10 numbers allowed"}
    from core.pipeline_box import _write_env as _cwc_write
    env_update = {}
    for key, (nums, labels) in parsed.items():
        env_update[key] = ",".join(nums)
        env_update[f"{key}_LABELS"] = labels
    _cwc_write(env_update)
    return {"ok": True}


def _ms15_wa_rows(date: str, runs: str):
    run_list = [int(r) for r in runs.split(",") if r.strip().isdigit()]
    use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
    if use_pg:
        from core.db_postgres import list_whatsapp_sends as _lw
    else:
        from core.db import list_whatsapp_sends as _lw
    return _lw(date, run_list), run_list

@app.get("/ms15/whatsapp-report")
def ms15_whatsapp_report(date: str, runs: str = ""):
    """Build and return an .xlsx listing every CLIENT who actually received a
    WhatsApp margin alert on `date` (YYYY-MM-DD), optionally filtered to specific
    run numbers (`runs`= comma-separated, e.g. "2,4,8"). Columns: Run, Client
    Code, Margin Warning %, Sent At. Reads the margin_whatsapp_log DB table
    (Postgres or SQLite per USE_POSTGRES).

    Client-only — the separate internal "MARGIN SHORTAGE IMAGE" sends to the
    RM/branch team (see run_shortage_image_alert() in margin_shortage_15min.py)
    have their own dedicated report at /ms15/whatsapp-report-team instead of
    being mixed into this one as extra rows. They used to be blended in here
    with a mostly-blank "Mobile (team sends only)" column, which was confusing
    to read — two separate downloads is clearer than one file trying to answer
    two different questions ("which clients got alerted" vs "which team
    numbers got the image").

    The client's mobile number is deliberately NOT a column here — the client
    code already identifies who was alerted, which is what this report is for;
    the phone number added nothing and put client contact details into a file
    that gets emailed and passed around. It is still recorded in the database
    for audit, just not exported here."""
    import io as _io
    from fastapi.responses import Response as _Resp
    try:
        rows, run_list = _ms15_wa_rows(date, runs)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    rows = [r for r in rows if not str(r.get("client_code") or "").startswith("TEAM")]
    import openpyxl as _oxl
    wb = _oxl.Workbook(); ws = wb.active; ws.title = "WhatsApp Alerts"
    ws.append(["Run", "Client Code", "Margin Warning %", "Sent At"])
    for r in rows:
        ws.append([r.get("run_number"), r.get("client_code"),
                   r.get("margin_pct"), str(r.get("sent_at") or "")])
    for col in ws.columns:
        _w = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = _w + 2
    buf = _io.BytesIO(); wb.save(buf); buf.seek(0)
    _rtag = ("_runs" + "-".join(str(r) for r in run_list)) if run_list else "_all-runs"
    fname = f"WhatsApp_Recipients_{date}{_rtag}.xlsx"
    return _Resp(content=buf.getvalue(),
                 media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/ms15/whatsapp-report-team")
def ms15_whatsapp_report_team(date: str, runs: str = ""):
    """Build and return an .xlsx listing every TEAM number that actually
    received the internal MARGIN SHORTAGE IMAGE alert on `date` (YYYY-MM-DD),
    optionally filtered to specific run numbers. Columns: Run, Mobile,
    Accounts In Image, Sent At — the companion report to /ms15/whatsapp-report
    (which is client-only), so "who on the team got the image" is a separate,
    unambiguous download instead of oddly-formatted rows mixed into the client
    file. This is exactly what to check when someone on the team says they
    didn't get the image despite the run log saying it sent."""
    import io as _io
    from fastapi.responses import Response as _Resp
    try:
        rows, run_list = _ms15_wa_rows(date, runs)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    rows = [r for r in rows if str(r.get("client_code") or "").startswith("TEAM")]
    import openpyxl as _oxl
    wb = _oxl.Workbook(); ws = wb.active; ws.title = "Margin Shortage Image — Team"
    ws.append(["Run", "Mobile", "Accounts In Image", "Sent At"])
    for r in rows:
        ws.append([r.get("run_number"), r.get("mobile") or "",
                   r.get("margin_pct"), str(r.get("sent_at") or "")])
    for col in ws.columns:
        _w = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = _w + 2
    buf = _io.BytesIO(); wb.save(buf); buf.seek(0)
    _rtag = ("_runs" + "-".join(str(r) for r in run_list)) if run_list else "_all-runs"
    fname = f"MarginShortageImage_TeamSends_{date}{_rtag}.xlsx"
    return _Resp(content=buf.getvalue(),
                 media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/ms15/output-file")
def ms15_output_file(date: str, run: int):
    """Return the Shortage_vs_CC_Deposit output .xlsx for a given date (YYYY-MM-DD)
    and run number. Looks in that run's backup folder first
    (MARGIN_SHORTAGE_FOLDER/BACKUPFILES AUTOMATED/<DD-MM-YYYY>/<NN>/); if the run
    is the current one whose files haven't been backed up yet, falls back to the
    live output in the base folder."""
    import os as _oos, glob as _og, datetime as _odt
    _dir = _oos.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    try:
        _ddmmyyyy = _odt.datetime.strptime(date, "%Y-%m-%d").strftime("%d-%m-%Y")
    except Exception:
        return {"ok": False, "error": "date must be YYYY-MM-DD"}
    run_folder = _oos.path.join(_dir, "BACKUPFILES AUTOMATED", _ddmmyyyy, f"{int(run):02d}")
    cands = _og.glob(_oos.path.join(run_folder, "Shortage_vs_CC_Deposit*.xlsx"))
    # Prefer the main output over the per-zone files
    cands.sort(key=lambda p: (("_" in _oos.path.basename(p)[len("Shortage_vs_CC_Deposit"):]), p))
    if not cands:
        # current run not yet backed up — fall back to live output in base folder,
        # but only if today matches the requested date (otherwise it's stale/wrong)
        if date == _odt.datetime.now().strftime("%Y-%m-%d"):
            cands = _og.glob(_oos.path.join(_dir, "Shortage_vs_CC_Deposit*.xlsx"))
            cands.sort(key=lambda p: (("_" in _oos.path.basename(p)[len("Shortage_vs_CC_Deposit"):]), p))
    if not cands:
        return {"ok": False, "error": f"No output file found for {date} run {run}."}
    _fp = cands[0]
    return FileResponse(_fp, filename=_oos.path.basename(_fp),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── Probable Defaulter (pipe key "probable") ─────────────────────────────────
# Generic /px/* endpoints (status/config-credentials/stop/logs/backup/upload)
# already work for pipe=probable once it's registered in PIPELINES — same
# reason CTCL/MS15/CLI only need dedicated routes for downloads, the
# captcha/token popup handshake, and the readiness check.
#
# RSA token entry for the TCS BANCS RSK383/RSK335 downloads (core/pipelines/
# probable_tcs_bancs.py) needs NO new route at all: it reuses the EXACT SAME
# _token_popup()/.token_state file (in SA_OUTPUT_DIR) that Short Allocation's
# MCX Deposit button and Client Prudential Limit's MCX Web Allocation button
# already share — probable_tcs_bancs.py sets MCX_DEPOSIT_CALLER="probable"
# before calling it, and /sa/token/pending already returns whatever "caller"
# is stamped into the state file, generically. The ONLY frontend change needed
# is teaching dashboard.html's _pipeBoxLabel() about the new caller value —
# done via the injected process-probable-box.js (dashboard.html itself is
# never edited), which wraps the existing function.

def _probable_clear_popup_state():
    """Delete any leftover NMASS/NCDEX captcha popup state so a stopped run's
    unanswered prompt can't reappear later — same fix as CTCL/MS15's own
    _clear_popup_state, scoped to PROBABLE_FOLDER."""
    import os as _pco
    _dir = _pco.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    for _fn in (".captcha_state", ".captcha_answer", ".captcha_nmass.png",
                ".token_state", ".token_answer"):
        try: _pco.remove(_pco.path.join(_dir, _fn))
        except OSError: pass


_probable_last_answered_at: dict = {}

@app.get("/probable/captcha/pending")
def probable_captcha_pending(request: Request):
    """Check if the NMASS or NCDEX (NCFE) captcha is awaiting dashboard input,
    for the Probable Defaulter box's own NMASS download. Same logic as
    /ctcl/captcha/pending and /ms15/captcha/pending, scoped to PROBABLE_FOLDER.
    Polled by process-probable-box.js's own captcha modal (dashboard.html's
    built-in poller only checks ctcl/ms15/sa sources, so this pipe needs its
    own small modal rather than touching that file)."""
    import base64 as _b64, json as _cj, os as _co, time as _ct
    global _probable_last_answered_at
    _dir = _co.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    _sf  = _co.path.join(_dir, ".captcha_state")
    _img = _co.path.join(_dir, ".captcha_nmass.png")
    try:
        _st = _cj.load(open(_sf, encoding="utf-8"))
        if _st.get("state") != "PENDING":
            return {"pending": False}
        if not _popup_visible(request, _sf, ["probable"]):
            return {"pending": False}
        img_b64 = _b64.b64encode(open(_img, "rb").read()).decode() if _co.path.isfile(_img) else None
        reason = _st.get("reason", "")
        source = _st.get("source", "nmass")
        _last = _probable_last_answered_at.get(source, 0)
        if not reason and _last and (_ct.time() - _last) < 90:
            reason = "⚠️ Previous attempt failed — try again"
        return {"pending": True, "image_b64": img_b64, "detected": _st.get("detected", ""), "reason": reason, "source": source}
    except Exception:
        return {"pending": False}


@app.post("/probable/captcha/answer")
def probable_captcha_answer(a: _CTCLCaptchaAnswer):
    """Submit typed captcha text to the waiting NMASS/NCDEX script (Probable Defaulter box)."""
    import os as _co, time as _ct, json as _cj
    global _probable_last_answered_at
    _dir = _co.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    _sf  = _co.path.join(_dir, ".captcha_state")
    _af  = _co.path.join(_dir, ".captcha_answer")
    try:
        _source = "nmass"
        try:
            _source = _cj.load(open(_sf, encoding="utf-8")).get("source", "nmass")
        except Exception:
            pass
        with open(_af, "w", encoding="utf-8") as f: f.write(a.text.strip())
        with open(_sf, "w", encoding="utf-8") as f:
            _cj.dump({"state": "ANSWERED"}, f)
        _probable_last_answered_at[_source] = _ct.time()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _note_who(request: Request):
    """Tell lock_manager exactly who is behind THIS request, so the action that starts a moment later records the
    right person (it otherwise guesses from 'the most recent -stream request in the last 15 s', which goes wrong when
    two downloads are started back to back — and then the captcha / RSA-token popup is hidden from the person who
    clicked)."""
    try:
        from core import lock_manager as _lmn
        _by, _adm = _actor(request)
        _lmn.note_request(_by or "", request.client.host if request.client else "", request.url.path)
    except Exception:
        pass


@app.get("/probable/download/nmass-stream")
def probable_nmass_download_stream(request: Request):
    """SSE: run NMASS_NCFE_API_MIX.PY (pipe=probable → saves into PROBABLE_FOLDER).
    Same shared cross-process dl_lock as ctcl/ms15 — see core/dl_lock.py."""
    import json as _pn_json
    from core.pipelines.nmass_download import stream_nmass_download
    _probable_clear_popup_state()
    _note_who(request)
    def gen():
        try:
            for ev in stream_nmass_download(pipe="probable"):
                yield f"data: {_pn_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_pn_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/probable/download/tcs-bancs-stream")
def probable_tcs_bancs_download_stream(request: Request):
    """SSE: RSK383 (Cash Collateral Deposit Limit) + RSK335 (Margin Utilization
    View / Clientwise MCX Margin) from eclear.mcxccl.com. Run as a genuine
    subprocess via pipeline_box._stream_code() (same reason CLI's MCX Web
    Allocation button does) so Stop can kill it instantly regardless of which
    blocking call (TCS BANCS login, RSA token wait, export poll) is in
    progress. See core/pipelines/probable_tcs_bancs.py."""
    import json as _pt_json
    # _stream_code_act (NOT _stream_code): claims PROBABLE's own "tcs" ACTION
    # slot rather than its main run slot. Using the run slot made this single
    # download look like a full pipeline run to every other screen — greying
    # out the entire box (including the unrelated NMASS/NCDEX button) and
    # announcing "PROBABLE is already running — started by <name>". See
    # _stream_code_act's docstring in core/pipeline_box.py.
    from core.pipeline_box import _stream_code_act
    _probable_clear_popup_state()
    _note_who(request)
    code = ("import os; from core.pipelines.probable_tcs_bancs import download_both as _f; "
            "_r = _f(os.getenv('PROBABLE_FOLDER', r'D:\\\\PROBABLE')); "
            "import sys; sys.exit(0 if (_r.get('rsk383') and _r.get('rsk335')) else 1)")
    def gen():
        try:
            for ev in _stream_code_act(code, "probable", "tcs", "probable_tcs_bancs"):
                yield f"data: {_pt_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_pt_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/probable/readiness")
def probable_readiness():
    """Check whether all input files are present in PROBABLE_FOLDER for today —
    same shape as /ctcl/readiness and /ms15/readiness, matching
    core/pipelines/probable_reconcile.py's _check_required_files()."""
    import os as _ros, glob as _rg, datetime as _rdt
    _dir = _ros.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    _today_ddmmyyyy = _rdt.datetime.now().strftime("%d%m%Y")
    _today_yyyymmdd = _rdt.datetime.now().strftime("%Y%m%d")
    checks = [
        ("PROBABLE_FULLFILE", f"Probable_Fullfile_{_today_ddmmyyyy}*.xlsx"),
        # "NCDEX" alone was ambiguous — the NMASS/NCDEX download produces four
        # files and this is only one of them. Name it the way it appears on
        # disk so the banner points straight at the actual file.
        ("CLIENTNET(NCDEX)",   f"ClientNetEffectiveDeposits_{_today_ddmmyyyy}*"),
        ("CM_CLIENT_LEVEL",    f"CM_ClientLevelCollaterals_{_today_ddmmyyyy}*"),
        ("CO_CLIENT_LEVEL",    f"CO_ClientLevelCollaterals_{_today_ddmmyyyy}*"),
        ("FO_CLIENT_LEVEL",    f"FO_ClientLevelCollaterals_{_today_ddmmyyyy}*"),
        # Names as the OPERATOR knows these two files, not as TCS BANCS's
        # internal report codes. RSK383 arrives under a random exported name
        # (<random>-<YYYYMMDDHHMMSS>.xlsx) and is what everyone calls the "MCX
        # Deposit" file; RSK335 is the one we rename to Clientwise_mcx_margin
        # and everyone calls the "90% file". Listing them as MCX_RSK383 /
        # MCX_RSK335_90PCT meant the missing-files banner named report codes
        # nobody can match to a file on disk.
        ("MCX_DEPOSIT_FILE",        f"*{_today_yyyymmdd}*.xlsx"),
        ("CLIENTWISE_MCX_MARGIN",   "Clientwise_mcx_margin*.xlsx"),
    ]
    items = []
    for key, pat in checks:
        found = bool(_rg.glob(_ros.path.join(_dir, pat)))
        items.append({"key": key, "ok": found, "example": pat})
    return {"items": items, "folder": _dir}


# ── SAFE TEST RUN (Probable Defaulter) ─────────────────────────────────────
# Replaces the old PROBABLE_TEST_MODE flag, which merely SKIPPED WhatsApp sends
# and so proved nothing about whether they work. A safe test still really sends
# everything — only the destination changes, and the backup step is skipped so
# the day's files stay in place for the real run. Arming writes a marker file
# that probable_full.py consumes the moment the pre-run file check passes, so
# it is structurally impossible for the flag to survive into a second run.
# See the long comment block above _safe_test_marker_path() in
# core/pipelines/probable_alerts.py.
@app.get("/probable/safe-test")
def probable_safe_test_status():
    """Armed state + the exact redirect targets, so the UI can show the operator
    precisely where a test run's messages will land BEFORE they commit to it."""
    from core.pipelines.probable_alerts import (
        is_safe_test_armed as _armed, safe_test_targets as _targets,
    )
    _email_to, _wa_to = _targets()
    return {"armed": _armed(), "email_to": _email_to, "wa_to": _wa_to}


@app.post("/probable/safe-test/arm")
def probable_safe_test_arm(password: str = ""):
    """Arm the NEXT Probable run as a safe test. Password-gated like every other
    state-changing route — arming changes where real messages go."""
    from core.short_allocation import check_password as _st_pw
    if not _st_pw(password):
        return {"ok": False, "error": "Wrong password"}
    from core.pipelines.probable_alerts import arm_safe_test as _arm
    try:
        _arm()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "armed": True}


@app.post("/probable/safe-test/disarm")
def probable_safe_test_disarm():
    """Cancel an armed safe test. Deliberately NOT password-gated — disarming
    only ever moves toward normal live behaviour, and an operator who armed by
    mistake must always be able to undo it instantly."""
    from core.pipelines.probable_alerts import disarm_safe_test as _disarm
    return {"ok": True, "armed": False, "was_armed": _disarm()}


# ───────────────────────── PHYSICAL DELIVERY (card in the Process Center) ─────────────────────────
# The report scripts live in core/pipelines/physdel_codes/; the card (platform/js/process-physdel.js) uses these
# routes for what the generic /px/* routes do not cover: window status + files-to-be-used, the CASH % popup value,
# the Safe test arm, and the five WhatsApp number lists.
_PD_WA_LISTS = {"DELHI": "Delhi", "LUDHIANA": "Ludhiana", "CHANDIGARH": "Chandigarh", "MUMBAI": "Mumbai",
                "FULL": "Full list (1 Cr alert)"}


@app.get("/pd/status")
def pd_status():
    from core.pipelines import physdel_cfg as _pd
    _pd.apply_defaults()
    return {"window": _pd.window_info(), "inputs": _pd.inputs_report(), "missing": _pd.missing_inputs(),
            "last_run": _pd.last_run(), "percent": _pd.get_percent(), "file_percent": _pd.get_file_percent(), "busy": _pd.busy_other(), "safe_test": _pd.safe_test_targets(),
            "today": __import__("datetime").date.today().isoformat()}


@app.post("/pd/percent")
def pd_percent(value: str = ""):
    """Saves the CASH % typed in the Run popup (the run reads it back). Anything not a positive number is refused."""
    from core.pipelines import physdel_cfg as _pd
    try:
        v = _pd.parse_percent(value)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "percent": _pd.set_percent(v)}


@app.get("/ops/progress")
def ops_progress(pipe: str = "", kind: str = "", since: float = 0):
    """What the CURRENT (or latest) run of ANY process box is doing, read from its log - so every open window shows the live
    steps even when somebody else started the run (generic version of /pd/progress)."""
    import glob as _g, os as _os, re as _re
    from core.pipeline_box import LOG_BASE, PIPELINES
    if not _re.fullmatch(r"[a-z0-9_]{1,20}", pipe or ""):
        return {"ok": False, "steps": []}
    sub = "short_allocation" if pipe == "sa" else (PIPELINES.get(pipe) or {}).get("logdir")
    if not sub:
        return {"ok": False, "steps": []}
    d = _os.path.join(LOG_BASE, sub)
    if kind == "action":      # a single button action (downloader etc.): the newest download/upload log since it began
        files = [f for f in _g.glob(_os.path.join(d, "*.log"))
                 if ("_download__" in _os.path.basename(f) or _os.path.basename(f).upper().startswith("UPLOAD"))
                 and _os.path.getmtime(f) >= float(since or 0) - 5]
    else:
        files = [f for f in _g.glob(_os.path.join(d, "*.log"))
                 if not _os.path.basename(f).upper().startswith("UPLOAD") and "_download__" not in _os.path.basename(f)]
    if not files:
        return {"ok": True, "steps": [], "latest": ""}
    f = max(files, key=_os.path.getmtime)
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()[-4000:]
    except OSError:
        return {"ok": True, "steps": [], "latest": ""}
    steps, latest = [], ""
    for ln in lines:
        m = _re.search(r"\[FRIENDLY_MSG\]\s*(.+?)\s*$", ln)
        t = m.group(1) if m else None
        if t is None:
            m2 = _re.match(r"^\[\d\d:\d\d:\d\d\]\s+(.*\S.*)$", ln)
            if m2 and _re.search(r"[✅⏳📦📧📤📥🔄▶⚠❌]|\b(downloading|downloaded|merging|saving|sending|sent|created|completed|backup|uploading|checking|reading|building)\b", m2.group(1), _re.I):
                t = m2.group(1)
        if t:
            t = t.strip()[:160]
            if not steps or steps[-1] != t:
                steps.append(t)
        if ln.strip() and not ln.startswith("#"):
            latest = _re.sub(r"^\[\d\d:\d\d:\d\d\]\s*", "", ln).strip()[:160]
    done = any(l.startswith("# Ended") for l in lines)
    return {"ok": True, "steps": steps[-12:], "latest": latest, "finished": done, "log": _os.path.basename(f)}


@app.get("/pd/progress")
def pd_progress(pipe: str = "physdel"):
    """What the CURRENT (or latest) PHYSICAL DELIVERY run is doing, read from its log - so every open window can show the
    steps and counts even when somebody else started the run.  steps = the plain-English step lines in order."""
    import glob as _g, os as _os, re as _re
    from core.pipeline_box import LOG_BASE
    if pipe not in ("physdel", "physdelfile"):
        return {"ok": False, "steps": []}
    files = _g.glob(_os.path.join(LOG_BASE, "physdel", f"{pipe}__*.log"))
    if not files:
        return {"ok": True, "steps": [], "detail": ""}
    f = max(files, key=_os.path.getmtime)
    try:
        txt = open(f, encoding="utf-8", errors="replace").read()
    except OSError:
        return {"ok": True, "steps": [], "detail": ""}
    steps = []
    for m in _re.finditer(r"^\[FRIENDLY_MSG\]\s*(.+?)\s*$", txt, _re.M):
        if not steps or steps[-1] != m.group(1):
            steps.append(m.group(1))
    c = lambda pat: len(_re.findall(pat, txt))
    br, rg, zn, wa = c(r"Branch Mail Sent"), c(r"Region Mail Sent"), c(r"Zone Mail Sent"), c(r"\| .{0,3}WhatsApp Sent")
    wf = c(r"WhatsApp (?:Failed|failed)")
    tot = _re.search(r"Unique clients in pivot : (\d+)", txt)
    last = steps[-1] if steps else ""
    detail = ""
    if "WhatsApp" in last and wa:
        detail = f"{wa} client WhatsApp messages sent" + (f" of {tot.group(1)} clients" if tot else "") + (f" · {wf} failed" if wf else "")
    elif "branch" in last.lower() and br:
        detail = f"{br} branch e-mails sent so far"
    elif "region" in last.lower() and rg:
        detail = f"{rg} region e-mails sent so far"
    elif "zone" in last.lower() and zn:
        detail = f"{zn} zone e-mails sent so far"
    done = bool(_re.search(r"^# Ended", txt, _re.M))
    return {"ok": True, "steps": steps, "detail": detail, "finished": done, "log": _os.path.basename(f),
            "counts": {"branch": br, "region": rg, "zone": zn, "whatsapp": wa}}


@app.post("/pd/file-percent")
def pd_file_percent(value: str = ""):
    """Saves the CASH % typed for the 'Create full file & e-mail it' button (0 is allowed)."""
    from core.pipelines import physdel_cfg as _pd
    try:
        v = _pd.set_percent(value, file=True)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "percent": v}


@app.get("/pd/safe-test")
def pd_safe_test_status():
    from core.pipelines import physdel_cfg as _pd
    _pd.apply_defaults()
    return _pd.safe_test_targets()


@app.post("/pd/safe-test/arm")
def pd_safe_test_arm(password: str = ""):
    from core.short_allocation import check_password as _pw
    if not _pw(password):
        return {"ok": False, "error": "Wrong password"}
    from core.pipelines import physdel_cfg as _pd
    t = _pd.safe_test_targets()
    if not t["email_to"] or len("".join(c for c in str(t["wa_to"]) if c.isdigit())) < 10:
        return {"ok": False, "error": "Set PD_TEST_EMAIL_TO and PD_TEST_WA_TO in .env first (where test messages should go)."}
    _pd.arm_safe_test()
    return {"ok": True, "armed": True}


@app.post("/pd/safe-test/disarm")
def pd_safe_test_disarm():
    from core.pipelines import physdel_cfg as _pd
    return {"ok": True, "armed": False, "was_armed": _pd.disarm_safe_test()}


@app.get("/pd/wa-config")
def pd_wa_config_get():
    from core.pipelines import physdel_cfg as _pd
    _pd.apply_defaults()
    return {"lists": [{"key": k, "label": lbl, "value": os.getenv(f"PD_WA_{k}", "")} for k, lbl in _PD_WA_LISTS.items()]}


@app.post("/pd/wa-config")
async def pd_wa_config_set(request: Request):
    """Body: {password, lists: {DELHI: "99..., 97...", ...}}.  Only digits are kept; max 10 per list."""
    import re as _re
    body = await request.json()
    from core.short_allocation import check_password as _pw
    if not _pw(str(body.get("password", ""))):
        return {"ok": False, "error": "Wrong password"}
    upd = {}
    for k, v in (body.get("lists") or {}).items():
        if k not in _PD_WA_LISTS:
            continue
        nums = []
        for part in _re.split(r"[,;\s]+", str(v or "")):
            d = _re.sub(r"\D", "", part)
            if not d:
                continue
            if len(d) == 12 and d.startswith("91"):
                d = d[2:]
            if len(d) != 10:
                return {"ok": False, "error": f"'{part}' in {_PD_WA_LISTS[k]} is not a 10-digit mobile number"}
            if d not in nums:
                nums.append(d)
        if len(nums) > 10:
            return {"ok": False, "error": f"{_PD_WA_LISTS[k]}: maximum 10 numbers"}
        upd[f"PD_WA_{k}"] = ",".join(nums)
    from core.pipeline_box import _write_env as _we
    _we(upd)
    return {"ok": True}


@app.get("/probable/mcx-lock-status")
def probable_mcx_lock_status():
    """Live status of PROBABLE's OWN TCS BANCS download lock — used by
    process-probable-box.js to grey out this box's TCS BANCS button while
    ITS OWN download is running.

    Scoped to owner="probable" on purpose. The lock used to be shared across
    Short Allocation / Client Prudential Limit / PROBABLE, so any one of them
    downloading greyed out (and hard-refused) the other two. They fetch
    different files and each handles TCS session-expiry itself, so per explicit
    instruction they now run independently — this only ever reports PROBABLE."""
    try:
        import sys as _ls, os as _lo
        _p_ = _lo.path.join(_lo.path.dirname(__file__), "sa_codes")
        if _p_ not in _ls.path: _ls.path.insert(0, _p_)
        import importlib as _limp
        _lmdd = _limp.import_module("mcx_deposit_downloader")
        return _lmdd.mcx_lock_status("probable")
    except Exception as e:
        return {"busy": False, "error": str(e)}


# ── Client Prudential Limit (pipe key "cli") ─────────────────────────────────
# Generic /px/* endpoints (status/config/credentials/stop/logs/backup) already
# work for pipe=cli once it's registered in PIPELINES — only the download/upload
# SSE streams and the readiness check need dedicated routes, same reason CTCL
# has /ctcl/download/nmass-stream + /ctcl/readiness instead of generic ones.

def _cli_stream_endpoint(code, logname_prefix):
    """Shared plumbing: run `code` as a genuine subprocess via pipeline_box._stream_code()
    (tracked in _PB["proc"]) so /cli/stop's taskkill /F /T can kill it INSTANTLY no
    matter what it's doing — FTP transfer, pandas processing, a nested login subprocess.
    This is what fixed "Stop didn't stop" — the old version ran the work directly in the
    FastAPI request thread, which taskkill can't reach."""
    import json as _cli_json
    from core.pipeline_box import _stream_code
    def gen():
        try:
            for ev in _stream_code(code, "cli", logname_prefix):
                yield f"data: {_cli_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_cli_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/cli/download-stream")
def cli_download_stream():
    """SSE: FTP download + NSCCL/NCDEX collateral files + processing.
    See core/pipelines/cli_download.py."""
    return _cli_stream_endpoint(
        "from core.pipelines.cli_download import run_cli_download as _f; _f()",
        "cli_download")


@app.get("/cli/mcx-download-stream")
def cli_mcx_download_stream():
    """SSE: MCX RSK383 (Query Detailed Limit) download + convert to
    MCX_WebAllocationDeallocation.csv. Standalone button, same idea as CTCL's
    "📥 MCX 90%" — skips the login entirely if the file's already there.
    See core/pipelines/cli_mcx_download.py."""
    return _cli_stream_endpoint(
        "from core.pipelines.cli_mcx_download import run_cli_mcx_download as _f; _f()",
        "cli_mcx_download")


@app.get("/cli/upload-stream")
def cli_upload_stream():
    """SSE: FTP upload (with missing-file check) + backup.
    See core/pipelines/cli_upload.py."""
    return _cli_stream_endpoint(
        "from core.pipelines.cli_upload import run_cli_upload as _f; _f()",
        "cli_upload")


class _CliOldRunAuth(_SA_BaseModel):
    password: str

@app.post("/cli/run-old-auth")
def cli_run_old_auth(a: _CliOldRunAuth):
    """Checks the password gate in front of the Run OLD button — separate from
    SA's own /sa/auth (SA_CONFIG_PASSWORD): this is its own credential,
    CLI_OLD_RUN_PASS, set in .env (defaults to "12345" if not set)."""
    return {"ok": a.password == _sa_os.getenv("CLI_OLD_RUN_PASS", "12345")}


@app.get("/cli/run-old-stream")
def cli_run_old_stream():
    """SSE: the full "OLD" Client Prudential Limit cycle for the CLI-01/CLI-02 Run
    button — Allocation Detail (local-first, FTP fallback, builds CLI_PRUDENTIAL_
    LIMIT.txt) → Download (CM/FO/CO/ClientNetEffectiveDeposits, skip what's already
    there) → MCX Web Allocation (skip download if present, always still convert) →
    FTP upload (always verifies/creates the FTP folder) + local backup + Postgres
    backup. One subprocess for the whole chain, so Stop kills all of it at once
    instead of only whichever of the 4 steps happened to be running.
    This is the "OLD" pipeline — a "NEW" one will be added later as a second,
    separate Run button once its logic is provided."""
    code = (
        "from core.pipelines.cli_allocation import run_cli_allocation as _a; "
        "from core.pipelines.cli_download import run_cli_download as _d; "
        "from core.pipelines.cli_mcx_download import run_cli_mcx_download as _m; "
        "from core.pipelines.cli_upload import run_cli_upload as _u; "
        "_a(); _d(); _m(); _u()"
    )
    return _cli_stream_endpoint(code, "cli_run_old")


# Back-compat alias — old dashboard.html builds (or bookmarked links) may still
# hit the pre-rename URL; route it to the exact same handler.
@app.get("/cli/run-full-stream")
def cli_run_full_stream_alias():
    return cli_run_old_stream()


@app.get("/cli/run-new-stream")
def cli_run_new_stream():
    """SSE: the "NEW" Client Prudential Limit cycle — the user's uploaded
    ALLOCATION_CLI_NMASS_NCDEX-2841612e.py, explicitly described as "the new one is
    this". Only the Allocation Detail step differs from OLD: it pre-checks
    RISK_REPORT + Kambala for today (stopping the whole chain if either is missing),
    then layers RISK_REPORT's MTF_STATUS='Y' accounts + Kambala's abs(Z-column) on
    top of the base allocation sum when building CLI_PRUDENTIAL_LIMIT.txt. Download,
    MCX Web Allocation and the FTP upload/backup steps are unchanged from OLD — the
    user only described a new Allocation step — so this chain reuses those exact
    same three functions."""
    code = (
        "from core.pipelines.cli_allocation_new import run_cli_allocation_new as _a; "
        "from core.pipelines.cli_download import run_cli_download as _d; "
        "from core.pipelines.cli_mcx_download import run_cli_mcx_download as _m; "
        "from core.pipelines.cli_upload import run_cli_upload as _u; "
        "_a(); _d(); _m(); _u()"
    )
    return _cli_stream_endpoint(code, "cli_run_new")


def _r1r2_stream_endpoint(code, logname_prefix):
    """Same plumbing as _cli_stream_endpoint, tracked under its own "r1r2"
    slot in _PB instead of "cli" — Live Snapshot/Afternoon/Evening are their
    own independent runs, never blocked by (or blocking) CLI/CTCL/MS15/SA/
    PROBABLE. See core/pipelines/r1_r2_comparison.py."""
    import json as _r1r2_json
    from core.pipeline_box import _stream_code
    def gen():
        try:
            for ev in _stream_code(code, "r1r2", logname_prefix):
                yield f"data: {_r1r2_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_r1r2_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/r1r2/live-stream")
def r1r2_live_stream():
    """SSE: Live Snapshot — no once-per-day limit, can be run any number of
    times. See core/pipelines/r1_r2_comparison.py."""
    return _r1r2_stream_endpoint(
        "from core.pipelines.r1_r2_comparison import run_r1r2_live as _f; _f()",
        "r1r2_live")


@app.get("/r1r2/afternoon-stream")
def r1r2_afternoon_stream():
    """SSE: Afternoon — once per calendar day."""
    return _r1r2_stream_endpoint(
        "from core.pipelines.r1_r2_comparison import run_r1r2_afternoon as _f; _f()",
        "r1r2_afternoon")


@app.get("/r1r2/evening-stream")
def r1r2_evening_stream():
    """SSE: Evening — once per calendar day."""
    return _r1r2_stream_endpoint(
        "from core.pipelines.r1_r2_comparison import run_r1r2_evening as _f; _f()",
        "r1r2_evening")


@app.get("/r1r2/download-stream")
def r1r2_download_stream():
    """SSE: standalone CM/FO/CO/NCDEX download only — no MCX, no
    reconciliation, no email. Same idea as CLI's dedicated "📥 Download"
    button: lets that one leg be retried on its own instead of only ever
    running inside a full Live Snapshot. Skips the login entirely if today's
    4 files are already sitting in R1R2_FOLDER."""
    return _r1r2_stream_endpoint(
        "from core.pipelines.r1_r2_comparison import download_nmass_ncdex_only as _f; _f()",
        "r1r2_download")


@app.get("/r1r2/mcx-download-stream")
def r1r2_mcx_download_stream():
    """SSE: standalone MCX leg only. Same idea as CLI's dedicated "📥 MCX Web
    Allocation" button. Skips the TCS BANCS login entirely if today's MCX
    file is already sitting in R1R2_FOLDER."""
    return _r1r2_stream_endpoint(
        "from core.pipelines.r1_r2_comparison import download_mcx_only as _f; _f()",
        "r1r2_mcx_download")


@app.get("/r1r2/readiness")
def r1r2_readiness():
    """Check whether all 5 files (CM/FO/CO/NCDEX/MCX) are present in
    R1R2_FOLDER, dated today. Reuses r1_r2_comparison's own
    _scan_and_choose() so "present" here means the exact same thing it means
    when Live Snapshot itself decides whether a download is needed — same
    content-sniffing, same today-only + filename-date cross-check. Feeds the
    box's missing-files banner and the Upload files modal's checklist."""
    from core.pipelines.r1_r2_comparison import BASE_FOLDER as _r1r2_dir, _scan_and_choose as _r1r2_scan
    import os as _r1r2_os
    _r1r2_os.makedirs(_r1r2_dir, exist_ok=True)
    # verbose=False: this endpoint is polled passively and frequently by every
    # open dashboard tab (every few seconds) just to check file status - not a
    # real run - so it must not spam "[ok]"/"[use]" lines into the server's
    # console on every poll. See _scan_and_choose()'s docstring.
    _, chosen_paths, missing = _r1r2_scan(_r1r2_dir, today_only=True, verbose=False)
    _examples = {
        "CM": "CM_ClientLevelCollaterals_<date>.csv",
        "FO": "FO_ClientLevelCollaterals_<date>.csv",
        "CO": "CO_ClientLevelCollaterals_<date>.csv",
        "NCDEX": "ClientNetEffectiveDeposits_<date>.csv",
        "MCX": "<random>-<YYYYMMDDHHMMSS>.xlsx",
    }
    items = []
    for seg in ("CM", "FO", "CO", "NCDEX", "MCX"):
        ok = seg not in missing
        items.append({"key": seg, "ok": ok, "example": _examples[seg],
                      "file": _r1r2_os.path.basename(chosen_paths[seg]) if (ok and seg in chosen_paths) else ""})
    return {"items": items, "folder": _r1r2_dir}


@app.get("/r1r2/today-status")
def r1r2_today_status():
    """Read-only peek at today's meta (.reconciliation_meta.json) — which of
    Afternoon/Evening have already run today (Live has no once-per-day limit
    so it's never added here). Drives the box's 3 tiles. Deliberately does
    NOT call rotate_if_new_day() — that function ARCHIVES yesterday's master
    as a side effect, which must only ever happen from an actual run, never
    from a passive dashboard poll."""
    from core.pipelines.r1_r2_comparison import BASE_FOLDER as _r1r2_dir, load_meta as _r1r2_load_meta
    import datetime as _r1r2_dt
    today_str = _r1r2_dt.datetime.now().strftime("%d-%m-%Y")
    meta = _r1r2_load_meta(_r1r2_dir)
    modes_done = meta.get("modes_done", []) if meta.get("date") == today_str else []
    return {"date": today_str, "modes_done": modes_done, "morning_done": "morning" in modes_done}


@app.post("/r1r2/stop")
def r1r2_stop(request: Request):
    """Stop any running R1/R2 subprocess (Live Snapshot's NMASS/NCDEX or MCX
    download, or the reconciliation/email step) — only this box's own
    independent slot, never CLI's/CTCL's/MS15's/SA's/PROBABLE's. Mirrors
    /cli/stop exactly, just for the "r1r2" pipe key."""
    allowed, why = _can_stop("px_r1r2", request)
    if not allowed:
        return JSONResponse({"ok": False, "error": why}, status_code=403)
    from core.pipeline_box import stop_pipeline as _r1r2_sp, _PB
    was_r1r2 = bool(_PB.get("r1r2", {}).get("running"))
    _r1r2_sp("r1r2")
    if was_r1r2:
        # Same reasoning as /cli/stop — a force-killed subprocess never runs
        # its own lock cleanup, so release both shared locks explicitly if
        # THIS box was the one actually holding them.
        try:
            import sys as _cs
            _p_ = os.path.join(os.path.dirname(__file__), "sa_codes")
            if _p_ not in _cs.path: _cs.path.insert(0, _p_)
            import importlib as _cimp
            _mdd = _cimp.import_module("mcx_deposit_downloader")
            _mdd.force_release_mcx_lock_if_owner("r1r2")
        except Exception:
            pass
        try:
            from core import dl_lock as _cdl
            if _cdl.status().get("owner") == "r1r2":
                _cdl.release()
        except Exception:
            pass
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
# RECONCILIATION  (HOLDING / T1 HOLDING / NFO BFO MTFS / KAMBALA POSITION)
# Logic lives in core/pipelines/reconciliation.py — these endpoints are plumbing.
# Own "reco" run slot: never blocks (or is blocked by) CTCL / CLI / R1-R2 / SA.
# ═══════════════════════════════════════════════════════════════════════════

def _reco_stream_endpoint(code, logname_prefix):
    import json as _rc_json
    from core.pipeline_box import _stream_code
    def gen():
        try:
            for ev in _stream_code(code, "reco", logname_prefix):
                yield f"data: {_rc_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_rc_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/reco/run-all-stream")
def reco_run_all_stream():
    """SSE: all four processes, in order. Strict file check first — if anything is
    missing nothing is downloaded, run or e-mailed. Already-DONE processes are skipped."""
    return _reco_stream_endpoint(
        "from core.pipelines.reconciliation import run_reco_all as _f; _f()", "reco_all")


@app.get("/reco/run-stream")
def reco_run_stream(proc: str = ""):
    """SSE: ONE process (HOLDING | T1 | NFO | KAMB)."""
    key = (proc or "").strip().upper()
    if key not in ("HOLDING", "T1", "NFO", "KAMB"):
        return JSONResponse({"ok": False, "error": "unknown process"}, status_code=400)
    return _reco_stream_endpoint(
        f"from core.pipelines.reconciliation import run_reco_one as _f; _f({key!r})",
        "reco_" + key.lower())


@app.get("/reco/download-stream")
def reco_download_stream(kind: str = ""):
    """SSE: stand-alone download — kind = nse | ftp | search."""
    k = (kind or "").strip().lower()
    if k not in ("nse", "ftp", "search"):
        return JSONResponse({"ok": False, "error": "unknown download"}, status_code=400)
    import json as _rc_json
    from core.pipeline_box import _stream_code_act
    code = f"from core.pipelines.reconciliation import run_download as _f; _f({k!r})"
    def gen():
        try:
            # own action slot per download → NSE / FTP / BANCS run side by side and never block Run
            for ev in _stream_code_act(code, "reco", "dl_" + k, "reco_dl_" + k):
                yield f"data: {_rc_json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_rc_json.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/reco/dl-status")
def reco_dl_status():
    """Outcome of the three stand-alone downloads (so the page never keeps spinning)."""
    import datetime as _rcdt
    from core.pipelines import reconciliation as _rc
    from core.pipeline_box import act_slot as _rc_as
    st = _rc.read_dl_states()
    out = {}
    today = _rcdt.date.today().isoformat()
    for k in ("nse", "ftp", "search"):
        slot, _ = _rc_as("reco", "dl_" + k)
        s = st.get(k) or {}
        status = s.get("status") if s.get("date") == today else None
        if status == "running" and not slot.get("running"):
            status = "failed"                      # stopped / killed
            s = {**s, "msg": s.get("msg") or "stopped"}
        out[k] = {"running": bool(slot.get("running")), "status": status,
                  "msg": s.get("msg", ""), "time": s.get("time", "")}
    return out


@app.get("/reco/readiness")
def reco_readiness():
    """Every file the four processes need (strict — all must be ok to run) + EXCEPTION info."""
    from core.pipelines import reconciliation as _rc
    import datetime as _rcdt
    _rc.ensure_dirs()
    import time as _rct
    _done = set(_rc.done_keys())
    _pend = [p for p in _rc.PROCS if _rc.PKEY[p] not in _done]      # only what is still to run
    all_done = not _pend
    items = _rc.check_files(_pend) if _pend else []
    if any(not i["ok"] for i in items):
        # a file can look missing for a moment (share/antivirus/another process) — look again before saying so
        _rct.sleep(0.7)
        items2 = _rc.check_files(_pend)
        if sum(1 for i in items2 if not i["ok"]) < sum(1 for i in items if not i["ok"]):
            print("[reco] readiness flicker: " + ", ".join(i["key"] for i in items if not i["ok"])
                  + " looked missing on the first look but present on the second")
        items = items2
    exc = _rc.find_exception()
    sr = _rc.find_uploaded("search")
    sr_today = bool(sr) and _rcdt.datetime.fromtimestamp(sr[0].stat().st_mtime).date() == _rcdt.date.today()
    return {"items": items, "all_done": all_done, "folder": str(_rc.reco_base()), "common_folder": str(_rc.common_dir()),
            "exception": ({"file": exc.name,
                           "date": _rcdt.datetime.fromtimestamp(exc.stat().st_mtime).strftime("%d-%m-%Y")}
                          if exc else None),
            "search_results_uploaded": sr_today,
            "scripts_missing": [n for n in list(_rc.SCRIPT_NAMES.values()) + [_rc.SEARCH_SCRIPT_NAME]
                                if not _rc.script_path(n)],
            "codes_dir": str(_rc.codes_dir())}


@app.get("/reco/today-status")
def reco_today_status():
    from core.pipelines import reconciliation as _rc
    import datetime as _rcdt
    d = _rcdt.date.today()
    st = _rc.load_state(d)
    cur = None
    try:
        from core.pipeline_box import _PB as _rc_pb
        if _rc_pb.get("reco", {}).get("running"):
            cur = _rc.get_current()
    except Exception:
        cur = None
    return {"date": d.strftime("%d-%m-%Y"), "done": _rc.done_keys(d),
            "state": {_rc.PKEY[p]: st.get(p, {}) for p in _rc.PROCS},
            "current": cur}


@app.post("/reco/upload")
def reco_upload(target: str = "", files: _SA_List[_SA_UploadFile] = _SA_File(...)):
    """ONE upload button for everything: HOLDING_/RISK_REPORT_ → COMMON FILES (the previous
    day's copy is archived automatically), EXCEPTION → its permanent folder (replacing archives
    the old file), PositionBook / SearchResults → today's drop folder."""
    import shutil as _sh
    from core.pipelines import reconciliation as _rc
    saved = []
    for f in files:
        tmp = _sa_os.path.join(_sa_tmp.gettempdir(), _sa_os.path.basename(f.filename))
        with open(tmp, "wb") as out:
            _sh.copyfileobj(f.file, out)
        saved.append(tmp)
    return _rc.route_uploads(saved, target if target in ("common", "main") else "")


def _common_names_today():
    import datetime as _cdt
    d = _cdt.date.today().strftime("%d%m%Y")
    return {"HOLDING": f"HOLDING_{d}.csv", "RISK REPORT": f"RISK_REPORT_{d}.csv"}


@app.get("/common/status")
def common_status():
    """Process Center 'Upload common files': is today's HOLDING / RISK_REPORT in COMMON FILES?"""
    folder = _sa_os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    items = []
    try:
        from core.common_ftp import read_sources as _rs
        _src = _rs(folder)
    except Exception:
        _src = {}
    for label, name in _common_names_today().items():
        p = _sa_os.path.join(folder, name)
        from core.file_probe import settle as _settle
        have = bool(_settle(lambda: _sa_os.path.isfile(p), tries=4, delay=0.4))   # a file being replaced for a moment is not "missing"
        mod = ""
        if have:
            import datetime as _cdt
            mod = _cdt.datetime.fromtimestamp(_sa_os.path.getmtime(p)).strftime("%H:%M:%S")
        items.append({"label": label, "name": name, "present": have, "modified": mod,
                      "source": (_src.get(name) or {}).get("how", "") if have else "", "source_by": (_src.get(name) or {}).get("by", "") if have else ""})
    return {"items": items}


@app.get("/common/ftp/status")
def common_ftp_status():
    """State of the automatic FTP fetch of today's HOLDING / RISK_REPORT (shown in the Upload Common Files window)."""
    from core import common_ftp as _cf
    return _cf.status()


@app.get("/common/ftp/log")
def common_ftp_log(date: str = ""):
    """Daily log of the FTP fetch (auto + button) - shown from the Upload Common Files window."""
    import re as _re
    from core import common_ftp as _cf
    return _cf.read_log(date if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or "") else "")


@app.post("/common/ftp/fetch")
def common_ftp_fetch(request: Request):
    """'Fetch from FTP now' button: one pass in the background (skips files already in COMMON FILES)."""
    import threading as _th
    from core import common_ftp as _cf
    if not _cf.configured():
        return {"ok": False, "error": "FTP is not set up - add COMMON_FTP_HOST / COMMON_FTP_USER / COMMON_FTP_PASS to .env."}
    if _cf.status().get("running"):
        return {"ok": True, "message": "A fetch is already running."}
    try:
        _by, _adm = _actor(request)
    except Exception:
        _by = ""
    print(f"📥 COMMON FILES FTP fetch started by {_by or 'unknown'}", flush=True)
    _th.Thread(target=lambda: _cf.fetch_once(manual=True), daemon=True, name="common-ftp-manual").start()
    return {"ok": True, "message": "Started - it takes a minute or two for big files."}


_COMMON_KINDS = {"HOLDING": "HOLDING", "RISK_REPORT": "RISK_REPORT"}
_COMMON_NAME_RE = __import__("re").compile(r"^(HOLDING|RISK_REPORT)_(\d{2})(\d{2})(\d{4})\.csv$", __import__("re").I)


def _common_db_index():
    """{filename.lower(): newest run_files row} for every HOLDING_/RISK_REPORT_ file stored in the database.
    Matched on the file NAME (the date inside it), not run_date — housekeeping stores the row on the day it moved it."""
    import os as _o
    try:
        if _o.getenv("USE_POSTGRES", "false").strip().lower() == "true":
            from core.db_postgres import list_run_files as _lf
        else:
            from core.db import list_run_files as _lf
        rows = _lf(days=3650)
    except Exception as e:
        return {}, f"{type(e).__name__}: {e}"
    idx = {}
    for r in rows:                                   # rows come newest first
        fn = str(r.get("filename") or "")
        if _COMMON_NAME_RE.match(fn) and fn.lower() not in idx:
            idx[fn.lower()] = r
    return idx, ""


def _common_disk_path(name):
    """COMMON FILES\\<name> or COMMON FILES\\BACKUP FILES AUTOMATED\\<dd-mm-yyyy>\\<name>, else ''."""
    m = _COMMON_NAME_RE.match(name)
    if not m:
        return ""
    base = _sa_os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    for p in (_sa_os.path.join(base, name),
              _sa_os.path.join(base, "BACKUP FILES AUTOMATED", f"{m.group(2)}-{m.group(3)}-{m.group(4)}", name)):
        if _sa_os.path.isfile(p):
            return p
    return ""


@app.get("/common/range")
def common_range(frm: str = "", to: str = "", kinds: str = "HOLDING,RISK_REPORT", only_found: int = 1):
    """'Get files' tab: for every day in the range and each chosen file type, is it on disk (COMMON FILES / its dated
    backup) and is it stored in the database?"""
    import datetime as _d
    try:
        a = _d.date.fromisoformat(frm); b = _d.date.fromisoformat(to or frm)
    except ValueError:
        return {"error": "Pick a valid From and To date."}
    if b < a:
        a, b = b, a
    if (b - a).days > 92:
        return {"error": "Please pick a range of up to 92 days."}
    want = [k for k in (kinds or "").split(",") if k.strip().upper() in _COMMON_KINDS]
    if not want:
        return {"error": "Tick HOLDING and/or RISK REPORT."}
    idx, db_err = _common_db_index()
    rows, d = [], a
    while d <= b:
        for k in want:
            k = k.strip().upper()
            name = f"{k}_{d.strftime('%d%m%Y')}.csv"
            p = _common_disk_path(name)
            r = idx.get(name.lower())
            size = _sa_os.path.getsize(p) if p else (r.get("size_bytes") if r else 0)
            if only_found and not p and not r:
                continue
            rows.append({"date": d.isoformat(), "kind": k, "name": name, "disk": bool(p),
                         "db": bool(r), "db_id": (r or {}).get("id"), "size": int(size or 0),
                         "weekend": d.weekday() >= 5})
        d += _d.timedelta(days=1)
    return {"rows": rows, "db_error": db_err, "from": a.isoformat(), "to": b.isoformat()}


def _common_bytes(name):
    """(bytes, source) for a HOLDING/RISK_REPORT file name — disk first, then the database. None if neither."""
    p = _common_disk_path(name)
    if p:
        with open(p, "rb") as f:
            return f.read(), "disk"
    idx, _e = _common_db_index()
    r = idx.get(name.lower())
    if r:
        import os as _o
        if _o.getenv("USE_POSTGRES", "false").strip().lower() == "true":
            from core.db_postgres import get_run_file as _gf
        else:
            from core.db import get_run_file as _gf
        got = _gf(r["id"])
        if got:
            return got[1], "database"
    return None


@app.get("/common/get")
def common_get(name: str, src: str = "auto"):
    """Download ONE HOLDING/RISK_REPORT file of any date (disk first, else database) to the caller's computer."""
    from fastapi import HTTPException as _HE
    from fastapi.responses import Response as _Resp
    name = _sa_os.path.basename(name or "")
    if not _COMMON_NAME_RE.match(name):
        raise _HE(status_code=400, detail="Only HOLDING_<DDMMYYYY>.csv and RISK_REPORT_<DDMMYYYY>.csv can be fetched.")
    if src == "db":
        idx, _e = _common_db_index()
        r = idx.get(name.lower())
        got = None
        if r:
            import os as _o
            _gf = (__import__("core.db_postgres", fromlist=["get_run_file"]).get_run_file
                   if _o.getenv("USE_POSTGRES", "false").strip().lower() == "true"
                   else __import__("core.db", fromlist=["get_run_file"]).get_run_file)
            g = _gf(r["id"])
            got = (g[1], "database") if g else None
    else:
        got = _common_bytes(name)
    if not got:
        raise _HE(status_code=404, detail=f"{name} was not found.")
    return _Resp(content=got[0], media_type="text/csv",
                 headers={"Content-Disposition": f'attachment; filename="{name}"', **_NO_CACHE})


@app.get("/common/zip")
def common_zip(frm: str, to: str = "", kinds: str = "HOLDING,RISK_REPORT"):
    """Every available file in the range as one ZIP (a single download for many days)."""
    import io, zipfile, datetime as _d
    from fastapi import HTTPException as _HE
    from fastapi.responses import Response as _Resp
    try:
        a = _d.date.fromisoformat(frm); b = _d.date.fromisoformat(to or frm)
    except ValueError:
        raise _HE(status_code=400, detail="Invalid date.")
    if b < a:
        a, b = b, a
    if (b - a).days > 92:
        raise _HE(status_code=400, detail="Range is limited to 92 days.")
    want = [k.strip().upper() for k in kinds.split(",") if k.strip().upper() in _COMMON_KINDS]
    buf, n, d = io.BytesIO(), 0, a
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        while d <= b:
            for k in want:
                name = f"{k}_{d.strftime('%d%m%Y')}.csv"
                got = _common_bytes(name)
                if got:
                    z.writestr(name, got[0]); n += 1
            d += _d.timedelta(days=1)
    if not n:
        raise _HE(status_code=404, detail="No files found in that range.")
    return _Resp(content=buf.getvalue(), media_type="application/zip",
                 headers={"Content-Disposition": f'attachment; filename="COMMON_FILES_{a.strftime("%d%m%Y")}_{b.strftime("%d%m%Y")}.zip"', **_NO_CACHE})


@app.get("/common/download")
def common_download(name: str):
    """Download today's HOLDING / RISK_REPORT to the machine of whoever clicked (their browser's Downloads).
    Only today's two exact names are served — nothing else in COMMON FILES can be fetched this way."""
    from fastapi import HTTPException as _HE
    allowed = {n.lower(): n for n in _common_names_today().values()}
    real = allowed.get(_sa_os.path.basename(name or "").lower())
    if not real:
        raise _HE(status_code=400, detail="Only today's HOLDING and RISK_REPORT files can be downloaded.")
    p = _sa_os.path.join(_sa_os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES"), real)
    if not _sa_os.path.isfile(p):
        raise _HE(status_code=404, detail=f"{real} is not in COMMON FILES.")
    return FileResponse(p, media_type="text/csv", filename=real, headers=_NO_CACHE)


@app.post("/common/upload")
def common_upload(request: Request, files: _SA_List[_SA_UploadFile] = _SA_File(...)):
    """Process Center 'Upload common files'. STRICT: only today's HOLDING_<DDMMYYYY>.csv and
    RISK_REPORT_<DDMMYYYY>.csv are accepted — nothing else, and no other date. Same-day file is
    replaced; older HOLDING/RISK_REPORT files are archived by the existing COMMON FILES housekeeping."""
    import shutil as _sh, re as _re, datetime as _cdt
    allowed = {n.lower(): n for n in _common_names_today().values()}
    folder = _sa_os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    try:
        _sa_os.makedirs(folder, exist_ok=True)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Cannot reach the COMMON FILES folder: {e}"}, status_code=500)
    ok, rejected = [], []
    for f in files:
        raw = (f.filename or "").replace("\\", "/").split("/")[-1]
        want = allowed.get(raw.lower())
        if not want:
            _m = _re.match(r"^(HOLDING|RISK_REPORT)_(\d{2})(\d{2})(\d{4})\.csv$", raw, _re.I)
            if _m:
                rejected.append(f"{raw} is dated {_m.group(2)}-{_m.group(3)}-{_m.group(4)}. Only today's file "
                                f"({_cdt.date.today():%d-%m-%Y}) is accepted.")
            else:
                rejected.append(f"{raw or '(no name)'} is not allowed. Only {', '.join(allowed.values())} are accepted.")
            continue
        part = _sa_os.path.join(folder, want + ".part")
        try:
            with open(part, "wb") as out:
                _sh.copyfileobj(f.file, out)
            size = _sa_os.path.getsize(part)
            if size == 0:
                raise ValueError("file is empty")
            with open(part, "rb") as fh:
                head = fh.readline(65536).decode("utf-8-sig", "replace").upper()
            if want.startswith("RISK_REPORT") and "ACCOUNTID" not in head:
                raise ValueError("this does not look like a risk report (no ACCOUNTID column)")
            if "," not in head and ";" not in head and "\t" not in head:
                raise ValueError("this does not look like a CSV file")
            _sa_os.replace(part, _sa_os.path.join(folder, want))
            ok.append(want)
            try:
                from core.common_ftp import mark_source as _ms
                try: _who = _actor(request)[0]
                except Exception: _who = ""
                _ms(folder, want, "Manual", _who or "")
            except Exception:
                pass
        except Exception as e:
            rejected.append(f"{raw}: {e}")
            try: _sa_os.remove(part)
            except OSError: pass
    if ok:
        try:
            from core.short_allocation import housekeep_common_files as _hk
            _hk()
        except Exception as e:
            print(f"⚠️  common upload: housekeeping skipped: {e}")
        try:
            _by, _adm = _actor(request)
        except Exception:
            _by = ""
        print(f"📤 COMMON FILES upload by {_by or 'unknown'}: {', '.join(ok)}", flush=True)
    return {"ok": bool(ok) or not rejected, "saved": ok, "rejected": rejected}


@app.get("/reco/holidays")
def reco_holidays():
    from core.pipelines import reconciliation as _rc
    rows = _rc.load_holidays()
    items = [{"iso": d.isoformat(), "date": d.strftime("%d-%m-%Y"), "weekday": d.strftime("%A"),
              "note": n} for d, n in sorted(rows.items(), reverse=True)]
    return {"items": items, "file": str(_rc.holidays_file())}


@app.post("/reco/holidays/add")
async def reco_holidays_add(req: Request):
    from core.short_allocation import check_password as _rc_pw
    from core.pipelines import reconciliation as _rc
    import datetime as _rcdt
    b = await req.json()
    if not _rc_pw(b.get("password", "")):
        return {"ok": False, "error": "Wrong password"}
    try:
        d = _rcdt.date.fromisoformat((b.get("date") or "").strip())
    except Exception:
        return {"ok": False, "error": "Pick a valid date"}
    try:
        _by, _adm = _actor(req)
    except Exception:
        _by = ""
    _who = (_by or "unknown user").replace(",", " ").strip()
    _rc.add_holiday(d, f"{_who} on {_rcdt.datetime.now():%d/%m/%Y %H:%M}")
    return {"ok": True}


@app.post("/reco/holidays/remove")
async def reco_holidays_remove(req: Request):
    from core.short_allocation import check_password as _rc_pw
    from core.pipelines import reconciliation as _rc
    import datetime as _rcdt
    b = await req.json()
    if not _rc_pw(b.get("password", "")):
        return {"ok": False, "error": "Wrong password"}
    try:
        d = _rcdt.date.fromisoformat((b.get("date") or "").strip())
    except Exception:
        return {"ok": False, "error": "Pick a valid date"}
    _rc.remove_holiday(d)
    return {"ok": True}


@app.post("/reco/stop")
def reco_stop(request: Request):
    allowed, why = _can_stop("px_reco", request)
    if not allowed:
        return JSONResponse({"ok": False, "error": why}, status_code=403)
    from core.pipeline_box import stop_pipeline as _rc_sp
    _rc_sp("reco")
    try:
        from core.pipeline_box import stop_action as _rc_sa
        for _a in ("dl_nse", "dl_ftp", "dl_search"):
            _rc_sa("reco", _a)
    except Exception:
        pass
    # a stopped run never cleans up its own pending RSA-token pop-up
    try:
        _d = _sa_os.getenv("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
        for _f in (".token_state", ".token_answer"):
            try: _sa_os.remove(_sa_os.path.join(_d, _f))
            except OSError: pass
    except Exception:
        pass
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
# MTF IMPACT COST  (tile in "Processing & Reports" → detail modal)
# Logic lives in core/pipelines/mtf_impact_cost.py — these endpoints are plumbing.
# Own "mtfic" run slot: never blocks (or is blocked by) any other box.
# ═══════════════════════════════════════════════════════════════════════════

def _mtfic_sse(code, prefix, action=None):
    import json as _mj
    from core import pipeline_box as _pbx
    def gen():
        try:
            it = (_pbx._stream_code_act(code, "mtfic", action, prefix) if action
                  else _pbx._stream_code(code, "mtfic", prefix))
            for ev in it:
                yield f"data: {_mj.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_mj.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


def _mtfic_date(s):
    import re as _mr
    s = (s or "").strip()
    return s if _mr.fullmatch(r"[0-9\-/]{0,10}", s) else ""


_MTFIC_UNLOCK = {"until": 0.0}      # admin re-run permission (one run, 15 minutes)


@app.get("/mtfic/status")
def mtfic_status(request: Request, date: str = ""):
    from core.pipelines import mtf_impact_cost as _m
    st = _m.status(_mtfic_date(date))
    try:
        st["admin"] = bool(_actor(request)[1])
    except Exception:
        st["admin"] = False
    return st


@app.post("/mtfic/unlock")
async def mtfic_unlock(request: Request):
    """Allow ONE more run today. Signed-in admins pass directly; anyone else needs the config password."""
    import time as _mt
    ok = False
    try:
        ok = bool(_actor(request)[1])
    except Exception:
        pass
    if not ok:
        try:
            b = await request.json()
        except Exception:
            b = {}
        from core.short_allocation import check_password as _mpw
        ok = bool(_mpw((b or {}).get("password", "")))
        if not ok:
            return {"ok": False, "error": "Wrong password — only an admin can re-run this."}
    _MTFIC_UNLOCK["until"] = _mt.time() + 900
    return {"ok": True}


@app.get("/mtfic/run-stream")
def mtfic_run_stream(request: Request, date: str = "", wa: int = 1):
    import time as _mt
    d = _mtfic_date(date)
    force = "0"
    if _MTFIC_UNLOCK["until"] > _mt.time():
        force = "1"
        _MTFIC_UNLOCK["until"] = 0.0          # one run per unlock
    try:
        by = _actor(request)[0] or ""
    except Exception:
        by = ""
    return _mtfic_sse(
        f"from core.pipelines.mtf_impact_cost import run_all as _f; "
        f"_f({d!r}, {'1' if wa else '0'!r}, {force!r}, {by!r})", "mtfic_run")


@app.get("/mtfic/download-stream")
def mtfic_download_stream(date: str = ""):
    d = _mtfic_date(date)
    return _mtfic_sse(
        f"from core.pipelines.mtf_impact_cost import run_download as _f; _f({d!r})", "mtfic_download", "dl")


@app.post("/mtfic/upload")
def mtfic_upload(files: _SA_List[_SA_UploadFile] = _SA_File(...)):
    """TradeBook_<DDMMYYYY> and C_CATG_<MON><YYYY>.T01 → the single MTF Impact Cost folder."""
    import shutil as _sh
    from core.pipelines import mtf_impact_cost as _m
    saved = []
    for f in files:
        tmp = _sa_os.path.join(_sa_tmp.gettempdir(), _sa_os.path.basename(f.filename))
        with open(tmp, "wb") as out:
            _sh.copyfileobj(f.file, out)
        saved.append(tmp)
    return _m.route_uploads(saved)


@app.get("/mtfic/file")
def mtfic_file(kind: str = "xlsx"):
    """Last run's Excel / image — served from its Backup folder (the originals are cleaned away)."""
    from core.pipelines import mtf_impact_cost as _m
    if kind not in ("xlsx", "png"):
        return JSONResponse({"ok": False, "error": "unknown file"}, status_code=400)
    p = _m.served_file(kind)
    if not p:
        return JSONResponse({"ok": False, "error": "No finished run to download yet."}, status_code=404)
    return FileResponse(str(p), filename=p.name, headers=_NO_CACHE)


@app.post("/mtfic/stop")
def mtfic_stop(request: Request):
    allowed, why = _can_stop("px_mtfic", request)
    if not allowed:
        return JSONResponse({"ok": False, "error": why}, status_code=403)
    from core.pipeline_box import stop_pipeline as _msp
    _msp("mtfic")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
# ALLOCATION REPORT  (tile in "Processing & Reports" → modal). The nightly close runs in the Scheduler;
# this is the manual side: day so far / up to a time / custom range, optional client codes, download or e-mail.
# Logic: core/pipelines/allocation_report.py (a subprocess). Own "alloc" run slot.
# ═══════════════════════════════════════════════════════════════════════════

def _alloc_sse(code, prefix):
    import json as _aj
    from core import pipeline_box as _pbx
    def gen():
        try:
            for ev in _pbx._stream_code(code, "alloc", prefix):
                yield f"data: {_aj.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {_aj.dumps({'type':'error','msg':str(e)})}\n\n"
        yield 'data: {"type":"end"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/alloc/status")
def alloc_status():
    from core.pipelines import allocation_report as _ar
    return _ar.status()


@app.get("/alloc/run-stream")
def alloc_run_stream(request: Request, mode: str = "sofar", until: str = "", frm: str = "", to: str = "",
                     clients: str = "", deliver: str = "download", mailto: str = ""):
    import json as _aj, re as _ar_re
    if mode not in ("sofar", "until", "custom"):
        return JSONResponse({"ok": False, "error": "unknown mode"}, status_code=400)
    if mode == "until" and not _ar_re.fullmatch(r"\d{1,2}:\d{2}", until or ""):
        return JSONResponse({"ok": False, "error": "time must look like 15:00"}, status_code=400)
    if mode == "custom" and not (_ar_re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", frm or "")
                                 and _ar_re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", to or "")):
        return JSONResponse({"ok": False, "error": "from / to must look like 2026-10-12 10:00"}, status_code=400)
    try:
        by = _actor(request)[0] or ""
    except Exception:
        by = ""
    opts = {"mode": mode, "until": until, "from": frm, "range_to": to, "clients": clients,
            "deliver": "mail" if deliver == "mail" else "download", "mail_to": mailto, "by": by}
    code = ("from core.pipelines.allocation_report import run_cli as _f; _f(" + repr(_aj.dumps(opts)) + ")")
    return _alloc_sse(code, "alloc_run")


@app.get("/alloc/logs")
def alloc_logs():
    """One log per run (manual and nightly), newest first."""
    from core.pipelines import allocation_report as _ar
    return {"ok": True, "runs": _ar.list_logs(100)}


@app.get("/alloc/log")
def alloc_log(name: str = ""):
    from core.pipelines import allocation_report as _ar
    txt = _ar.read_log_file(name)
    if txt is None:
        return JSONResponse({"ok": False, "error": "Log not found."}, status_code=404)
    return {"ok": True, "name": name, "text": txt}


@app.get("/alloc/file")
def alloc_file(name: str = ""):
    from core.pipelines import allocation_report as _ar
    p = _ar.served_file(name)
    if not p:
        return JSONResponse({"ok": False, "error": "File not found — run the report again."}, status_code=404)
    return FileResponse(str(p), filename=p.name, headers=_NO_CACHE)


@app.post("/alloc/stop")
def alloc_stop(request: Request):
    allowed, why = _can_stop("px_alloc", request)
    if not allowed:
        return JSONResponse({"ok": False, "error": why}, status_code=403)
    from core.pipeline_box import stop_pipeline as _asp
    _asp("alloc")
    try:
        from core.pipelines import allocation_report as _ar
        _ar._release()
    except Exception:
        pass
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
# MARGIN SHORTAGE (15-min) — SHORTFALL ALLOCATION UPLOAD PROMPT
#
# Uploading sends real allocation instructions to the exchange, so there is no
# "upload" button anywhere: the 15-min run itself asks, once, at the moment the
# allocation file exists and its total is known. These two endpoints are the
# bridge between that waiting subprocess and the dashboard.
#
# The default is always DON'T UPLOAD. No answer, a timeout, a wrong password,
# or a dashboard nobody has open all end the same way — the file stays on disk,
# backed up and archived. Only a password-verified yes uploads.
# ═══════════════════════════════════════════════════════════════════════════

def _ms15_alloc_prompt_files():
    _d = _sa_os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    return (_sa_os.path.join(_d, ".alloc_prompt.json"),
            _sa_os.path.join(_d, ".alloc_prompt_state"))


@app.get("/ms15/alloc/prompt")
def ms15_alloc_prompt(request: Request):
    """Is a 15-min run waiting on an upload decision? Polled by the dashboard
    while the box is running. Only an admin is ever shown the question."""
    import json as _ap_json
    if not _actor(request)[1]:
        return {"pending": False}
    info_f, state_f = _ms15_alloc_prompt_files()
    try:
        if open(state_f, encoding="utf-8").read().strip().upper() != "PENDING":
            return {"pending": False}
        with open(info_f, encoding="utf-8") as f:
            info = _ap_json.load(f)
    except Exception:
        return {"pending": False}
    return {"pending": True, **info}


class _AllocPromptAnswer(BaseModel):
    action: str = "skip"        # "upload" | "skip"
    password: str = ""


@app.post("/ms15/alloc/prompt/answer")
def ms15_alloc_prompt_answer(a: _AllocPromptAnswer, request: Request):
    """Answer the pending upload question.

    The password is checked HERE, server-side. A modal that only hides a button
    is not a control — this is the thing that actually decides whether real
    allocation instructions leave the building, so it verifies the same console
    password as Credentials (/sa/auth) before writing UPLOAD.

    Skipping needs no password: choosing NOT to send is always allowed.
    """
    if not _actor(request)[1]:
        return {"ok": False, "error": "Only an administrator can upload the allocation files."}
    info_f, state_f = _ms15_alloc_prompt_files()
    try:
        if open(state_f, encoding="utf-8").read().strip().upper() != "PENDING":
            return {"ok": False, "error": "Nothing is waiting for an answer."}
    except Exception:
        return {"ok": False, "error": "Nothing is waiting for an answer."}

    want_upload = (a.action or "").strip().lower() == "upload"
    if want_upload and not _sa_check_pw(a.password):
        # Deliberately does NOT write the state file — a wrong password leaves
        # the run still waiting, so it can be retried, and times out into "not
        # uploaded" if it never gets a correct one.
        return {"ok": False, "error": "Wrong password."}

    try:
        with open(state_f, "w", encoding="utf-8") as f:
            f.write("UPLOAD" if want_upload else "SKIP")
    except Exception as e:
        return {"ok": False, "error": f"Could not record the answer: {e}"}
    return {"ok": True, "action": "upload" if want_upload else "skip"}


@app.post("/cli/mark-variant")
async def cli_mark_variant(request: Request):
    """Records which Run button (OLD/NEW) was last used and whether it succeeded —
    purely for the dashboard's "which one did I just run" indicator next to the
    two Run buttons. Not used for any actual pipeline decision-making."""
    import json as _cmv_json, os as _cmv_os, datetime as _cmv_dt
    body = await request.json()
    variant = str(body.get("variant", "")).upper()
    if variant not in ("OLD", "NEW"):
        return {"ok": False, "error": "variant must be OLD or NEW"}
    logdir = _cmv_os.path.join(_cmv_os.path.dirname(_cmv_os.path.abspath(__file__)), "logs", "cli")
    _cmv_os.makedirs(logdir, exist_ok=True)
    fp = _cmv_os.path.join(logdir, "last_variant.json")
    data = {"variant": variant, "ok": bool(body.get("ok", True)),
            "ts": _cmv_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        _cmv_json.dump(data, open(fp, "w", encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


@app.get("/cli/last-variant")
def cli_last_variant():
    import json as _clv_json, os as _clv_os
    fp = _clv_os.path.join(_clv_os.path.dirname(_clv_os.path.abspath(__file__)), "logs", "cli", "last_variant.json")
    try:
        return _clv_json.load(open(fp, encoding="utf-8"))
    except Exception:
        return {"variant": None, "ok": None, "ts": None}


@app.post("/cli/stop")
def cli_stop(request: Request):
    """Stop any running CLI download/upload subprocess (e.g. the NMASS_NCFE_API_MIX.PY
    subprocess launched by cli_download.py's Step 3) — only CLI's own independent
    slot, never CTCL's or MS15's. Only the run's own starter or an admin may
    do this — see _can_stop()."""
    allowed, why = _can_stop("px_cli", request)
    if not allowed:
        return JSONResponse({"ok": False, "error": why}, status_code=403)
    from core.pipeline_box import stop_pipeline as _cli_sp, _PB
    was_cli = bool(_PB.get("cli", {}).get("running"))
    _cli_sp("cli")
    if was_cli:
        # If CLI's own subprocess held the shared MCX-deposit lock (its "MCX Web
        # Allocation" button) or the shared NMASS/NCFE login lock (its download
        # step, core/dl_lock.py), a force-killed process never runs its own
        # cleanup — release both explicitly now, so the NEXT click doesn't
        # wrongly report "already in use" for minutes even though Stop was just
        # clicked. Only releases each if its recorded owner is actually "cli"
        # (see force_release_mcx_lock_if_owner's docstring for why that check
        # matters — stopping CLI must never clear a lock SA/CTCL/MS15
        # legitimately still holds).
        try:
            import sys as _cs
            _p_ = os.path.join(os.path.dirname(__file__), "sa_codes")
            if _p_ not in _cs.path: _cs.path.insert(0, _p_)
            import importlib as _cimp
            _mdd = _cimp.import_module("mcx_deposit_downloader")
            _mdd.force_release_mcx_lock_if_owner("cli")
        except Exception:
            pass
        try:
            from core import dl_lock as _cdl
            if _cdl.status().get("owner") == "cli":
                _cdl.release()
        except Exception:
            pass
    return {"ok": True}


@app.post("/cli/advance-run")
def cli_advance_run():
    """Mark today's CLI-01/CLI-02 slot as complete (called by the dashboard after
    Download+Upload both succeed) — advances run_counter.txt the same way CTCL's
    counter advances, so the next run window's tile shows 'Opens HH:MM' correctly."""
    import os as _car_os, datetime as _car_dt
    _dir = _car_os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    _car_os.makedirs(_dir, exist_ok=True)
    fp = _car_os.path.join(_dir, "run_counter.txt")
    today = _car_dt.datetime.now().strftime("%Y-%m-%d")
    n = 0
    try:
        content = open(fp, encoding="utf-8", errors="replace").read().strip()
        if "|" in content:
            d, existing_n = content.split("|", 1)
            if d.strip() == today:
                n = int(existing_n)
    except Exception:
        pass
    # Slot-aware + idempotent: set the counter to THIS run's slot number
    # (morning=1, afternoon=2, evening=3) instead of blindly adding 1. Adding 1
    # let a second same-slot run (re-run, retry) push the counter past its slot
    # and mark the NEXT slot done too, and any missed advance left the slot
    # looking "not done / still clickable". max() never moves it backwards.
    try:
        from core.pipelines.cli_allocation_new import _current_window as _car_cw
        _slot, _ = _car_cw()
    except Exception:
        _slot = None
    if _slot is None:
        _h = _car_dt.datetime.now().hour
        _slot = "morning" if _h < 12 else ("afternoon" if _h < 18 else "evening")
    _idx = {"morning": 1, "afternoon": 2, "evening": 3}.get(_slot, n + 1)
    n = max(n, _idx)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"{today}|{n}")
    return {"ok": True, "run_state": n, "slot": _slot}


@app.get("/cli/readiness")
def cli_readiness():
    """Check whether all Client Prudential Limit input files are present for today.
    Same pattern as /ctcl/readiness — used by the dashboard's missing-files banner.
    RISK_REPORT lives in the shared COMMON FILES folder (same file CTCL vs NMASS
    uses), not the CLI base folder, so it's checked there separately."""
    import os as _clr_os, glob as _clr_g, datetime as _clr_dt
    _dir = _clr_os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    _common_dir = _clr_os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    today_str = _clr_dt.datetime.now().strftime("%d%m%Y")
    # Allocation_Detail, RISK_REPORT and Kambala are MORNING-ONLY inputs (they only
    # feed CLI_PRUDENTIAL_LIMIT.txt, which afternoon/evening never rebuild). So
    # they are only listed as "needed" in the morning. Slot = the run-window gate;
    # outside every window, fall back to the clock (before 12:00 = morning).
    try:
        from core.pipelines.cli_allocation_new import _current_window as _clr_cw
        _clr_slot, _ = _clr_cw()
    except Exception:
        _clr_slot = None
    if _clr_slot is None:
        _clr_slot = "morning" if _clr_dt.datetime.now().hour < 12 else "later"
    _clr_morning = (_clr_slot == "morning")
    checks = [
        # CM/FO/CO collaterals are .txt (the run requests JSON:TXT, like the original).
        ("CM_CLIENT_LEVEL",              f"CM_ClientLevelCollaterals_{today_str}*.txt"),
        ("FO_CLIENT_LEVEL",              f"FO_ClientLevelCollaterals_{today_str}*.txt"),
        ("CO_CLIENT_LEVEL",              f"CO_ClientLevelCollaterals_{today_str}*.txt"),
        ("CLIENT_NET_EFFECTIVE_DEPOSITS","ClientNetEffectiveDeposits_*.csv"),
        # CLI_PRUDENTIAL_LIMIT.txt is deliberately NOT listed: the run itself
        # builds it from Allocation_Detail, so it can never be an "input".
    ]
    if _clr_morning:
        checks.insert(0, ("ALLOCATION_DETAIL", f"Allocation_Detail_{today_str}.csv"))
    items = []
    for key, pat in checks:
        found = bool(_clr_g.glob(_clr_os.path.join(_dir, pat)))
        items.append({"key": key, "ok": found, "example": pat})
    # MCX Web Allocation: satisfied by the converted MCX_WebAllocationDeallocation.csv
    # OR a raw random-named RSK383 export for today (<random>-YYYYMMDDHHMMSS.xlsx) —
    # the run converts the raw export itself, so either one means "present".
    _ymd = _clr_dt.datetime.now().strftime("%Y%m%d")
    _mcx_ok = (bool(_clr_g.glob(_clr_os.path.join(_dir, "MCX_WebAllocationDeallocation.csv")))
               or bool(_clr_g.glob(_clr_os.path.join(_dir, f"*-{_ymd}*.xlsx"))))
    items.append({"key": "MCX_WEB_ALLOCATION", "ok": _mcx_ok,
                  "example": "MCX_WebAllocationDeallocation.csv or *-YYYYMMDDHHMMSS.xlsx"})
    if _clr_morning:
        # RISK_REPORT: pre-file check accepts either the CLI base folder or the shared
        # COMMON FILES folder (the dashboard's "Common files" button uploads it there).
        risk_found = (bool(_clr_g.glob(_clr_os.path.join(_dir, f"RISK_REPORT_{today_str}.csv")))
                      or bool(_clr_g.glob(_clr_os.path.join(_common_dir, f"RISK_REPORT_{today_str}.csv"))))
        items.append({"key": "RISK_REPORT", "ok": risk_found, "example": "RISK_REPORT_*.csv"})
        # Kambala: pre-file check for the "new" allocation script — CLI base folder,
        # dated today (the run also fetches it from FTP itself if missing).
        kambala_pat = f"Kambala_Omny_Depo_Adhoc_All_level.txt_{today_str}.txt"
        kambala_found = bool(_clr_g.glob(_clr_os.path.join(_dir, kambala_pat)))
        items.append({"key": "KAMBALA", "ok": kambala_found, "example": kambala_pat})
    return {"items": items, "folder": _dir, "common_folder": _common_dir}


# ── MCX Deposit stop ─────────────────────────────────────────────────────────

_sa_mcx_dep_proc = None   # set by mcx_deposit_download.py when proc starts

@app.post("/sa/mcx-deposit-stop")
def sa_mcx_deposit_stop():
    """Kill the MCX Deposit download subprocess if running."""
    global _sa_mcx_dep_proc
    import subprocess as _ksp, os as _kos
    p = _sa_mcx_dep_proc
    if p and p.poll() is None:
        try:
            if _kos.name == "nt":
                _ksp.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                p.terminate()
        except Exception:
            pass
    _sa_mcx_dep_proc = None
    # Same reasoning as /cli/stop: a force-killed subprocess never runs its own
    # lock cleanup, so release the shared MCX-deposit lock explicitly here too
    # — only if it's actually still recorded as owned by "sa".
    try:
        import sys as _ss
        _p_ = _kos.path.join(_kos.path.dirname(__file__), "sa_codes")
        if _p_ not in _ss.path: _ss.path.insert(0, _p_)
        import importlib as _simp
        _smdd = _simp.import_module("mcx_deposit_downloader")
        _smdd.force_release_mcx_lock_if_owner("sa")
    except Exception:
        pass
    _sa_clear_popup_state()
    return {"ok": True}


@app.get("/sa/mcx-deposit/lock-status")
def sa_mcx_deposit_lock_status():
    """Live PER-BOX status of the MCX-deposit locks (see
    sa_codes/mcx_deposit_downloader.py's _acquire_mcx_lock/mcx_lock_status) —
    polled by the dashboard so each button greys itself out THE INSTANT ITS OWN
    box starts, on every connected screen, instead of only finding out after a
    click.

    The lock used to be SHARED, so Short Allocation downloading also greyed out
    (and hard-refused) Client Prudential Limit's and PROBABLE's buttons. They
    download different files and each handles TCS session-expiry itself, so per
    explicit instruction they now run independently.

    Returns each box separately, plus a legacy top-level {"busy","owner"} —
    the first busy box — so any older caller keeps working unchanged:
      {"busy": bool, "owner": ..., "boxes": {"sa": {...}, "cli": {...}, "probable": {...}}}
    """
    try:
        import sys as _ls
        _p_ = os.path.join(os.path.dirname(__file__), "sa_codes")
        if _p_ not in _ls.path: _ls.path.insert(0, _p_)
        import importlib as _limp
        _lmdd = _limp.import_module("mcx_deposit_downloader")
        boxes = {o: _lmdd.mcx_lock_status(o) for o in _lmdd._MCX_OWNERS}
        legacy = _lmdd.mcx_lock_status()
        return {**legacy, "boxes": boxes}
    except Exception as e:
        return {"busy": False, "error": str(e)}


# ── CTCL per-run log store (survives page refresh) ────────────────────────────

_ctcl_run_logs: dict = {}   # {run_no(int): {download_log, ctcl_log, top20_log, payout_log, has_error, updated_at}}

def _ctcl_log_path():
    import os as _lp_os
    d = _lp_os.path.join(_lp_os.path.dirname(__file__), "logs", "ctcl")
    _lp_os.makedirs(d, exist_ok=True)
    return _lp_os.path.join(d, "run_logs.json")

def _ctcl_logs_load():
    import json as _lj
    try:
        with open(_ctcl_log_path(), encoding="utf-8") as f:
            return {int(k): v for k, v in _lj.load(f).items()}
    except Exception:
        return {}

def _ctcl_logs_save(store):
    import json as _lj
    try:
        with open(_ctcl_log_path(), "w", encoding="utf-8") as f:
            _lj.dump({str(k): v for k, v in store.items()}, f, indent=2)
    except Exception:
        pass

# Load persisted logs on startup
_ctcl_run_logs = _ctcl_logs_load()


@app.post("/ctcl/run-log")
async def ctcl_save_run_log(req: Request):
    """Save/update per-run log info. Merges into existing record so partial updates work."""
    global _ctcl_run_logs
    data = await req.json()
    run_no = data.get("run_no")
    if not run_no:
        return {"ok": False, "error": "missing run_no"}
    existing = _ctcl_run_logs.get(int(run_no), {})
    # Only overwrite fields that were explicitly provided (non-empty)
    for field in ("download_log", "ctcl_log", "top20_log", "payout_log"):
        v = data.get(field)
        if v:
            existing[field] = v
    for field in ("has_error", "updated_at", "stage_status", "friendly_msg"):
        if field in data:
            existing[field] = data[field]
    # Explicit clear — a normal (non-retry) run does MarginTop20/Payout INLINE in the same
    # combined log, so any top20_log/payout_log left over from a PAST DAY's "Retry ... only"
    # (run numbers repeat every day — CTCL-02 today vs CTCL-02 four days ago is the same key)
    # is now stale and must be wiped, or the View modal would show that old day's log forever
    # instead of falling back to today's fresh combined ctcl_log.
    for field in (data.get("clear_fields") or []):
        if field in ("download_log", "ctcl_log", "top20_log", "payout_log"):
            existing[field] = ""
    _ctcl_run_logs[int(run_no)] = existing
    _ctcl_logs_save(_ctcl_run_logs)
    return {"ok": True}


@app.get("/ctcl/run-log/{run_no}")
def ctcl_get_run_log(run_no: int):
    """Return saved log info for a CTCL run. Falls back to disk if not in memory."""
    r = _ctcl_run_logs.get(run_no)
    if not r:
        store = _ctcl_logs_load()
        r = store.get(run_no)
        if r:
            _ctcl_run_logs[run_no] = r
    if r:
              return {"ok": True, **r}
    return {"ok": False}
