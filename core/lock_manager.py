"""
core/lock_manager.py — centralized execution lock manager.

DESIGN PRINCIPLE — "derive, don't duplicate":
The lock registry does NOT keep its own idea of what is running. Lock state is
DERIVED live from the real execution state that already exists and is already
authoritative:

    * _PB      (core.pipeline_box)     — ctcl/ms15/cli's own INDEPENDENT slots
                                         (one dict per pipe — see the note on
                                         _PB in pipeline_box.py for why they
                                         are not shared)
    * _SA      (core.short_allocation) — the Short Allocation runner
    * .mcx_deposit_lock file           — the cross-process TCS BANCS session lock
    * dl_lock  (core.dl_lock)          — the cross-process NMASS/NCFE login lock
                                         (the one thing ctcl/ms15/cli genuinely
                                         share: a fixed captcha/session path)

so a lock can NEVER outlive or contradict the actual process (the exact class
of stale/over-broad-lock bug this project already hit and fixed once — see the
Task 43 note in dashboard.html's pxRenderRuns). What this module ADDS on top:

    * WHO/WHERE metadata (user name from the rms_user cookie, client IP,
      request path) attached to each real lock,
    * an append-only audit trail (logs/audit.jsonl): start / end / reject,
      with duration and outcome,
    * one uniform snapshot() every UI (platform pages, the embedded Process
      Center overlay, the AI assistant) consumes,
    * an "affects" list per lock, so WHICH buttons to disable is decided
      SERVER-side (granular — Short Allocation is never blocked because CTCL
      is running; that lesson is enforced here, not re-learned in every UI).

Crash recovery falls out of the design: if the server restarts, _PB/_SA are
empty → no locks are reported; the MCX file lock has its own stale-expiry.
Nothing here can wedge.

Queueing is deliberately NOT implemented (Option A: reject with a clear,
attributed message). These runs are bound to time-of-day windows and to
sequential run numbers — auto-firing a queued duplicate later would produce
wrong-time reports/emails to clients, which is worse than asking the second
operator to wait. (Jenkins-style queues suit stateless CI jobs, not
trading-day windows.)
"""
import os
import json
import time
import datetime
import threading

REPO      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT_LOG = os.path.join(REPO, "logs", "audit.jsonl")

_L = threading.Lock()

# who/ip metadata per lock key — enrichment only, never the source of truth
_META = {}            # key -> {"by","ip","path","started_at","label","pipe"}

# most recent run-starting HTTP request (set by api.py middleware, consumed by
# on_start). Streams start within milliseconds of their request, so a short
# freshness window is enough; if two run requests ever land in the same
# instant, _PB_LOCK rejects one anyway and the audit records the rejection.
_LAST_REQ = {"at": 0.0}

# which dashboard boxes each lock affects — the ONLY place this mapping lives.
# ctcl/ms15/cli each have their OWN independent run-slot in pipeline_box._PB
# (they don't share files or state with each other — CTCL running must never
# grey out MS15 or CLI's Run buttons); SA is likewise independent. The ONE
# real shared resource between ctcl/ms15/cli — the NMASS/NCFE login's captcha
# image + session cookie, always written to the same fixed path regardless of
# which pipe is downloading — has its own separate "nmass_login" lock below,
# scoped to nothing (affects: []) since it only ever blocks for the ~40-50s a
# login takes, never a whole pipeline run.
_AFFECTS = {"px_ctcl": ["ctcl"], "px_ms15": ["ms15"], "px_cli": ["cli"], "sa": ["sa"]}

# Standalone download-button actions (core.pipeline_box's _ACT slots, e.g.
# "ctcl:mcx90", "ms15:nmass") each map to exactly ONE button's DOM id — used
# so the frontend can grey out just that one button instead of the whole box.
# A colleague downloading NMASS/NCDEX on PROBABLE has nothing to do with that
# box's separate TCS BANCS button (different .py, different server, no shared
# session) — only a lock's OWN button should ever disable from this list.
_ACTION_BUTTON = {
    ("ctcl", "mcx90"):    "ctclMcx90Btn",
    ("ms15", "nmass"):    "ms15NmassBtn",
    ("ms15", "mcx90"):    "ms15Mcx90Btn",
    ("probable", "nmass"): "probableNmassBtn",
    ("probable", "tcs"):   "probableTcsBtn",
}


def note_request(name, ip, path):
    with _L:
        _LAST_REQ.update({"name": (name or "").strip()[:60], "ip": ip or "",
                          "path": path or "", "at": time.time()})


def _consume_request():
    with _L:
        if time.time() - _LAST_REQ.get("at", 0) < 15:
            return {"by": _LAST_REQ.get("name") or "unknown user",
                    "ip": _LAST_REQ.get("ip", ""), "path": _LAST_REQ.get("path", "")}
    return {"by": "unknown user", "ip": "", "path": ""}


def _audit(event):
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        event = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), **event}
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def on_start(key, label, pipe=None, by=None, ip=None):
    """`by`/`ip`, when given, come straight from the HTTP request that is
    ACTUALLY starting this run (read from its signed session, in the route
    handler itself) and are used as-is — no guessing involved.

    When omitted, falls back to _consume_request()'s 15-second "most recent
    -stream request" heuristic. That heuristic has a real failure mode: it is
    ONE global slot shared by every "-stream" endpoint across every pipe, so
    an unrelated stream request (another user's download, another pipe's own
    stream) landing in the same 15s window wins the attribution instead of
    whoever actually triggered THIS run. Callers that can identify their own
    requester should always pass by=/ip= to sidestep that entirely."""
    who = {"by": by, "ip": ip or "", "path": ""} if by else _consume_request()
    with _L:
        _META[key] = {**who, "label": label, "pipe": pipe or key,
                      "started_at": datetime.datetime.now().isoformat(timespec="seconds")}
    _audit({"event": "start", "lock": key, "label": label, "pipe": pipe,
            "by": who["by"], "ip": who["ip"], "path": who["path"]})


def on_end(key, outcome="finished"):
    with _L:
        meta = _META.pop(key, None)
    dur = None
    if meta and meta.get("started_at"):
        try:
            dur = int((datetime.datetime.now()
                       - datetime.datetime.fromisoformat(meta["started_at"])).total_seconds())
        except Exception:
            pass
    _audit({"event": "end", "lock": key, "label": (meta or {}).get("label"),
            "by": (meta or {}).get("by"), "ip": (meta or {}).get("ip"),
            "outcome": outcome, "duration_s": dur})


def on_reject(key, busy_label):
    who = _consume_request()
    _audit({"event": "reject", "lock": key, "label": busy_label,
            "by": who["by"], "ip": who["ip"],
            "holder": (_META.get(key) or {}).get("by")})


def lock_line(key):
    """Short attribution string for rejection messages: 'started by X at HH:MM'."""
    m = _META.get(key)
    if not m:
        return ""
    t = (m.get("started_at") or "")[11:16]
    who = m.get("by") or "unknown user"
    return f" It was started by {who}" + (f" at {t}" if t else "") + \
           (f" from {m['ip']}" if m.get("ip") else "") + "."


def _elapsed(started_iso):
    try:
        return max(0, int((datetime.datetime.now()
                           - datetime.datetime.fromisoformat(started_iso)).total_seconds()))
    except Exception:
        return None


def snapshot():
    """Live, derived lock state. Safe to call from any thread at any rate."""
    locks = []

    # px slots — ctcl / ms15 / cli each have their OWN independent slot now
    # (see core/pipeline_box.py's _PB note), so each only ever reports itself.
    try:
        from core.pipeline_box import _PB, _busy_label_for, _clear_if_stale
        for _pipe, _pb in _PB.items():
            _clear_if_stale(_pipe)  # heal a wedged lock before ever reporting it as busy
            if _pb.get("running"):
                _key = f"px_{_pipe}"
                meta = _META.get(_key, {})
                started = meta.get("started_at") or _pb.get("started_at")
                locks.append({
                    "key": _key, "kind": "process", "state": "RUNNING",
                    "pipe": _pipe, "label": _busy_label_for(_pipe),
                    "by": meta.get("by") or "unknown user", "ip": meta.get("ip", ""),
                    "started_at": started, "elapsed_s": _elapsed(started) if started else None,
                    "affects": _AFFECTS.get(_key, [_pipe]),
                })
    except Exception:
        pass

    # Standalone download-button actions (NMASS/NCDEX, MCX 90%) — a SEPARATE
    # slot from the main pipeline run (see core.pipeline_box.act_slot's long
    # note on why). Before this, snapshot() only ever looked at _PB, so someone
    # else's screen showed nothing at all while a colleague was mid-download —
    # no strip, no greyed buttons, no "already running" — right up until the
    # main run actually started, at which point the RUNNING strip would
    # suddenly appear looking "late" (it wasn't late; the wait genuinely had
    # no visible state to report until then). These actions don't call
    # on_start()/on_end() (they're short, ~40-50s, and not attributed to a
    # specific person the way a full run is) so `by` falls back to the pipe's
    # own label, same precedent as the NMASS/NCFE login resource lock below.
    try:
        from core.pipeline_box import running_actions, act_label, _busy_label_for as _blf3
        for _pipe, _action, _started in running_actions():
            _key = f"act_{_pipe}_{_action}"
            locks.append({
                # kind:"action" (not "process") tells the frontend this is a
                # single standalone button, not a whole-box run — it should
                # grey out ONLY the one button named in "button" below, never
                # the rest of the box, and never override an actual whole-box
                # "process"/"resource" lock that's also active for this pipe.
                "key": _key, "kind": "action", "state": "RUNNING",
                "pipe": _pipe, "label": act_label(_pipe, _action),
                "by": _blf3(_pipe) or _pipe, "ip": "",
                "started_at": _started, "elapsed_s": _elapsed(_started) if _started else None,
                "affects": _AFFECTS.get(f"px_{_pipe}", [_pipe]),
                "button": _ACTION_BUTTON.get((_pipe, _action)),
            })
    except Exception:
        pass

    # NMASS/NCFE login — the one real shared resource between ctcl/ms15/cli
    # (see core/dl_lock.py). affects: [] — it never greys out a Run button,
    # it just means a login is mid-flight for ~40-50s somewhere.
    try:
        from core import dl_lock
        from core.pipeline_box import _busy_label_for as _blf2
        st = dl_lock.status()
        if st.get("busy"):
            locks.append({
                "key": "nmass_login", "kind": "resource", "state": "RUNNING",
                "pipe": st.get("owner"), "label": "NMASS/NCDEX login",
                "by": _blf2(st.get("owner")) or st.get("owner"),
                "ip": "", "started_at": st.get("started_at"),
                "elapsed_s": _elapsed(st.get("started_at")) if st.get("started_at") else None,
                "affects": [],
            })
    except Exception:
        pass

    # Short Allocation (independent subsystem)
    try:
        from core.short_allocation import _SA
        if _SA.get("running"):
            meta = _META.get("sa", {})
            started = meta.get("started_at")
            locks.append({
                "key": "sa", "kind": "process", "state": "RUNNING",
                "pipe": "sa", "label": meta.get("label") or "Short Allocation",
                "by": meta.get("by") or "unknown user", "ip": meta.get("ip", ""),
                "started_at": started, "elapsed_s": _elapsed(started) if started else None,
                "affects": _AFFECTS["sa"],
            })
    except Exception:
        pass

    # MCX Deposit / TCS BANCS download (file-based, cross-process, with
    # stale-expiry + PID-liveness + owner tagging in
    # sa_codes/mcx_deposit_downloader.py).
    #
    # NOT a shared mutex any more: SA, CLI and PROBABLE each hold their OWN
    # lock and can all download at the same time (different files; each
    # handles TCS session-expiry itself). This row is therefore INFORMATIONAL
    # only — affects: [] already meant it greys out nothing, which is now
    # also the accurate description rather than a convenient exception.
    # mcx_lock_status() with no owner reports the first busy box.
    try:
        import sys, importlib
        sa_dir = os.path.join(REPO, "sa_codes")
        if sa_dir not in sys.path:
            sys.path.insert(0, sa_dir)
        st = importlib.import_module("mcx_deposit_downloader").mcx_lock_status()
        if st.get("busy"):
            locks.append({
                "key": "mcx_deposit", "kind": "resource", "state": "RUNNING",
                "pipe": st.get("owner"), "label": "MCX Deposit / TCS BANCS session",
                "by": {"sa": "Short Allocation box", "cli": "Client Prudential box",
                       "probable": "Probable Defaulter box"}.get(st.get("owner"), st.get("owner")),
                "ip": "", "started_at": st.get("started_at"),
                "elapsed_s": _elapsed(st.get("started_at")) if st.get("started_at") else None,
                "affects": [],   # its two buttons already self-manage via /sa/mcx-deposit/lock-status
            })
    except Exception:
        pass

    return {"now": datetime.datetime.now().isoformat(timespec="seconds"), "locks": locks}


def read_audit(limit=200):
    """Most recent audit events, newest first."""
    try:
        with open(AUDIT_LOG, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            f.seek(max(0, size - 400_000))
            lines = f.read().decode("utf-8", errors="replace").strip().splitlines()
        out = []
        for ln in reversed(lines[-int(limit) * 2:]):
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
            if len(out) >= int(limit):
                break
        return out
    except FileNotFoundError:
        return []
    except Exception:
        return []
