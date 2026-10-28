"""
core/ops_metrics.py — read-only aggregation layer for the AI Operations Platform.

Feeds the new platform shell (platform/index.html, served at "/") with everything
its Dashboard needs: KPIs, system health, today's timeline, execution history and
the AI morning-brief. It NEVER starts, stops or modifies anything — every function
here only reads state that already exists (run counters, log files, env config,
sockets) so it can be polled freely by many open browser tabs without any risk to
live pipeline runs.

Design rules:
  * Never raise to the caller — every public function returns a dict/list even if
    half its inputs are unavailable (fields become None and the UI shows "—").
  * Everything expensive is cached with a short TTL, because the dashboard polls
    every ~12s and several people keep it open all day.
  * No new hard dependencies. psutil is used IF installed (CPU%); RAM has a ctypes
    fallback on Windows; disk uses shutil.disk_usage — so nothing breaks if pip
    install psutil was never run on this machine.
"""
import os
import re
import time
import json
import shutil
import socket
import datetime
import threading
import subprocess
import concurrent.futures

REPO     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_BASE = os.path.join(REPO, "logs")

_START_TS = time.time()   # fallback server-start marker (module imports at boot)

# ── tiny TTL cache ────────────────────────────────────────────────────────────
_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()

def _cached(key, ttl, fn):
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _CACHE_LOCK:
        _CACHE[key] = (now, val)
    return val


# ── pipeline registry view ────────────────────────────────────────────────────
# Which boxes exist, how many runs each has, where their windows live in .env,
# and what their log folders are. SA isn't in core.pipeline_box's PIPELINES so
# it's described here explicitly.
_PIPES = [
    {"key": "ctcl", "name": "CTCL vs NMASS",           "runs": 5, "win_env": "CTCL_RUN{i}_WINDOW", "logdir": "ctcl"},
    {"key": "sa",   "name": "Short Allocation",        "runs": 4, "win_env": "SA_RUN{i}_WINDOW",   "logdir": "short_allocation"},
    {"key": "ms15", "name": "Margin Shortage (15-min)","runs": 17, "win_env": "MS15_RUN{i}_WINDOW", "logdir": "ms15"},
    {"key": "cli",  "name": "Client Prudential Limit", "runs": 2, "win_env": "CLI_RUN{i}_WINDOW",  "logdir": "cli"},
]
_STATIC_PIPES = _PIPES        # fallback only - the live list is read from the Process Center registry


def _pipes():
    """Every Process Center box, read live from core.pipeline_box.PIPELINES (+ Short Allocation), so a
    pipeline added there appears on the Dashboard with no change here. Cached 30 s; falls back to the
    static list if the registry cannot be read."""
    def build():
        try:
            from core.pipeline_box import PIPELINES
            out, seen = [], set()
            for key, p in PIPELINES.items():
                runs = int(p.get("runs") or 0)
                logdir = p.get("logdir") or key
                if runs < 1 or logdir in seen:
                    continue
                seen.add(logdir)
                out.append({"key": key, "name": p.get("name") or key, "runs": runs,
                            "win_env": key.upper() + "_RUN{i}_WINDOW", "logdir": logdir})
            if "short_allocation" not in seen:
                out.append({"key": "sa", "name": "Short Allocation", "runs": 4,
                            "win_env": "SA_RUN{i}_WINDOW", "logdir": "short_allocation"})
            return out or list(_STATIC_PIPES)
        except Exception:
            return list(_STATIC_PIPES)
    return _cached("pipes_live", 30, build)


def _pipe_by_logdir():
    return {p["logdir"]: p for p in _pipes()}

_TS_RE       = re.compile(r"__(\d{8})_(\d{6})\.log$")
_EMAIL_OK_RE = re.compile(r"email sent successfully|all emails sent", re.I)
_WA_OK_RE    = re.compile(r"whatsapp submitted for|all whatsapp messages processed", re.I)
# same failure wording core.pipeline_box._status() recognises
_EMAIL_BAD_RE = re.compile(r"email (failed|error)", re.I)
_WA_BAD_RE    = re.compile(r"whatsapp (api error|error) for", re.I)
_FAILISH_RE  = re.compile(r"traceback \(most recent|aborting execution|pre-run file check failed|"
                          r"\[(NMASS|MCX90|NCFE|MCX_DEPOSIT)_(AUTH_FAIL|LOCKED)\]|\[FRIENDLY_MSG\]", re.I)


def _parse_win(w):
    """'08:40-09:00' → (mins_start, mins_end) or None."""
    try:
        a, b = w.strip().split("-")
        h1, m1 = a.split(":"); h2, m2 = b.split(":")
        return int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
    except Exception:
        return None


def _px_completed(pipe_key):
    """Completed-today counter for ctcl/ms15/cli via pipeline_box (0 on any issue)."""
    try:
        from core.pipeline_box import pb_status
        s = pb_status(pipe_key)
        return max(0, min(int(s.get("runs") or 0), int(s.get("run_state") or 0)))
    except Exception:
        return 0


def _sa_completed_and_runs():
    """(completed_count, per-run dict) for Short Allocation, today-scoped."""
    try:
        from core.short_allocation import get_all_run_status
        runs = get_all_run_status() or {}
        done = 0
        for j in range(1, 5):
            r = runs.get(f"SA-0{j}") or {}
            if r.get("code2") == "done" or r.get("overall") == "done":
                done = j
            else:
                break
        return done, runs
    except Exception:
        return 0, {}


def _running_now():
    """One entry per live process, WITH who/where metadata from the lock
    manager (which derives its truth from the same _PB/_SA state — see
    core/lock_manager.py). Falls back to raw flags if the lock layer errors."""
    try:
        from core import lock_manager
        return [{"pipe": l.get("pipe"), "label": l.get("label"), "since": l.get("started_at"),
                 "by": l.get("by"), "ip": l.get("ip"), "elapsed_s": l.get("elapsed_s")}
                for l in lock_manager.snapshot()["locks"] if l.get("kind") == "process"]
    except Exception:
        pass
    out = []
    try:
        from core.pipeline_box import _PB, _busy_label_for
        for _pipe, _pb in _PB.items():
            if _pb.get("running"):
                out.append({"pipe": _pipe, "label": _busy_label_for(_pipe),
                            "since": _pb.get("started_at")})
    except Exception:
        pass
    try:
        from core.short_allocation import _SA
        if _SA.get("running"):
            out.append({"pipe": "sa", "label": "Short Allocation", "since": None})
    except Exception:
        pass
    return out


# ── log scanning (history + today's stats) ────────────────────────────────────
_LOG_SCAN_CACHE: dict = {}   # path -> (mtime, size, entry)

def _classify_tail(tail):
    """Best-effort verdict from the last few KB of a finished log."""
    if "⛔" in tail or "Stopped by user" in tail:
        return "stopped"
    last_ok  = max(tail.rfind("✅"), tail.rfind("Completed successfully"))
    m_fail   = None
    for m in _FAILISH_RE.finditer(tail):
        m_fail = m
    last_bad = max(tail.rfind("❌"), m_fail.start() if m_fail else -1)
    if last_bad > last_ok:
        return "failed"
    if last_ok >= 0:
        return "ok"
    return "unknown"


def _scan_log(path, fname, logdir):
    """One history entry per .log file; cached by (mtime,size)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _LOG_SCAN_CACHE.get(path)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]

    m = _TS_RE.search(fname)
    start = None
    if m:
        try:
            start = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except Exception:
            start = None
    if start is None:
        start = datetime.datetime.fromtimestamp(st.st_mtime)

    dur = max(0, int(st.st_mtime - start.timestamp())) if m else None

    tail = ""
    emails = wa = emails_failed = wa_failed = 0
    try:
        with open(path, "rb") as f:
            size = st.st_size
            f.seek(max(0, size - 65536))
            blob = f.read().decode("utf-8", errors="replace")
        tail = blob[-6000:]
        emails        = len(_EMAIL_OK_RE.findall(blob))
        wa            = len(_WA_OK_RE.findall(blob))
        emails_failed = len(_EMAIL_BAD_RE.findall(blob))
        wa_failed     = len(_WA_BAD_RE.findall(blob))
    except Exception:
        pass

    pipe = _pipe_by_logdir().get(logdir)
    label = fname.split("__")[0].replace("_", " ")
    if pipe and label.strip().lower() == pipe["key"]:
        label = "Pipeline run"          # e.g. "ctcl__<ts>.log" → clearer than a bare "ctcl"
    entry = {
        "file": fname, "logdir": logdir,
        "pipe": pipe["key"] if pipe else logdir,
        "pipe_name": pipe["name"] if pipe else logdir.replace("_", " ").title(),
        "label": label,
        "start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "date": start.strftime("%Y-%m-%d"),
        "duration_s": dur,
        "status": _classify_tail(tail),
        "emails": emails, "whatsapp": wa,
        "emails_failed": emails_failed, "wa_failed": wa_failed,
        "size": st.st_size,
    }
    if len(_LOG_SCAN_CACHE) > 4000:
        _LOG_SCAN_CACHE.clear()
    _LOG_SCAN_CACHE[path] = (st.st_mtime, st.st_size, entry)
    return entry


def _scan_all_logs(days):
    """Every .log under logs/*/ newer than N days, newest first."""
    cutoff = time.time() - days * 86400
    out = []
    try:
        subdirs = [d for d in os.listdir(LOG_BASE)
                   if os.path.isdir(os.path.join(LOG_BASE, d))]
    except OSError:
        return []
    for sd in subdirs:
        d = os.path.join(LOG_BASE, sd)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for f in names:
            if not f.endswith(".log"):
                continue
            p = os.path.join(d, f)
            try:
                if os.path.getmtime(p) < cutoff:
                    continue
            except OSError:
                continue
            e = _scan_log(p, f, sd)
            if e:
                out.append(e)
    out.sort(key=lambda e: e["start"], reverse=True)
    return out


def history(days=7):
    days = max(1, min(60, int(days or 7)))
    return _cached(f"hist:{days}", 20, lambda: _scan_all_logs(days))


# ── files downloaded today ────────────────────────────────────────────────────
_WATCH_FOLDER_ENVS = ["CLI_FOLDER", "SA_OUTPUT_DIR", "SA_COMMON_DIR", "MARGIN_SHORTAGE_FOLDER",
                      "MARGIN_TOP20_FOLDER", "FTP_BASE_LOCAL", "SA_LAS_FUNDING_DIR"]

def _files_today():
    today = datetime.date.today()
    total = 0
    for k in _WATCH_FOLDER_ENVS:
        d = os.getenv(k, "").strip()
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        if len(names) > 4000:   # safety: never grind on a giant folder
            continue
        for f in names:
            p = os.path.join(d, f)
            try:
                if os.path.isfile(p) and datetime.date.fromtimestamp(os.path.getmtime(p)) == today:
                    total += 1
            except OSError:
                continue
    return total


# ── timeline ──────────────────────────────────────────────────────────────────
def timeline():
    """Today's full schedule across all four boxes, with live status per slot."""
    now_min = datetime.datetime.now().hour * 60 + datetime.datetime.now().minute
    running = _running_now()
    running_pipes = {r["pipe"] for r in running}
    sa_done, _sa_runs = _sa_completed_and_runs()

    items = []
    for p in _pipes():
        done = sa_done if p["key"] == "sa" else _px_completed(p["key"])
        nruns = p["runs"]
        if not any(os.getenv(p["win_env"].format(i=i), "").strip() for i in range(1, nruns + 1)):
            nruns = 1          # no time windows configured (on-demand box): one slot per day, not N
        for i in range(1, nruns + 1):
            w = os.getenv(p["win_env"].format(i=i), "").strip()
            pw = _parse_win(w)
            rid = f"{p['key'].upper()}-{i:02d}"
            if i <= done:
                status = "completed"
            elif p["key"] in running_pipes and i == done + 1:
                status = "running"
            elif i == done + 1 and pw and now_min >= pw[0]:
                status = "overdue" if now_min > pw[1] else "due"
            elif pw and now_min > pw[1]:
                status = "waiting"     # can't start yet (earlier run pending) but window passed
            else:
                status = "upcoming"
            items.append({"id": rid, "pipe": p["key"], "pipe_name": p["name"],
                          "window": w, "start_min": pw[0] if pw else None,
                          "status": status})
    items.sort(key=lambda x: (x["start_min"] is None, x["start_min"] or 0))
    return {"now_min": now_min, "items": items}


# ── summary / KPIs ────────────────────────────────────────────────────────────
def _unresolved_failures(logs):
    """Failed runs of today that are still failed: a failure is dropped as soon as a LATER run of the same step
    (same pipeline + same log label) finished OK - e.g. Margin Shortage fails at 13:30, re-run at 13:32 succeeds.
    Newest first."""
    last_ok = {}
    for e in logs:
        if e["status"] == "ok":
            k = (e["pipe"], e["label"])
            if e["start"] > last_ok.get(k, ""):
                last_ok[k] = e["start"]
    return [e for e in logs if e["status"] == "failed" and last_ok.get((e["pipe"], e["label"]), "") <= e["start"]]


def _summary_inner():
    tl = timeline()
    items = tl["items"]
    today = datetime.date.today().strftime("%Y-%m-%d")
    todays_logs = [e for e in history(2) if e["date"] == today]

    completed = sum(1 for i in items if i["status"] == "completed")
    total     = len(items)
    due       = [i for i in items if i["status"] in ("due", "overdue")]
    upcoming  = sum(1 for i in items if i["status"] == "upcoming")
    failed    = sum(1 for e in todays_logs if e["status"] == "failed")
    failed_open = _unresolved_failures(todays_logs)
    ok_logs   = [e for e in todays_logs if e["status"] == "ok"]
    durs      = [e["duration_s"] for e in ok_logs if e.get("duration_s")]
    running   = _running_now()

    finished_runs = len(ok_logs) + failed
    from core import ops_sched
    sc = _cached("sched_today", 10, ops_sched.today)
    return {
        "date": today,
        "runs_completed": completed + sc["done"], "runs_total": total + sc["total"],
        "boxes": {"done": completed, "total": total},
        "sched": {k: sc.get(k, 0) for k in ("done", "total", "failed", "running", "missed", "due")},
        "due_now": [{"id": i["id"], "pipe_name": i["pipe_name"], "window": i["window"],
                     "overdue": i["status"] == "overdue"} for i in due],
        "upcoming": upcoming,
        "failed_today": len(failed_open),          # only failures that were NOT followed by a success of the same step
        "failed_open": failed_open,
        "failed_all_today": failed,
        "executions_today": len(todays_logs),
        "success_rate": round(100 * len(ok_logs) / finished_runs) if finished_runs else None,
        "avg_runtime_s": int(sum(durs) / len(durs)) if durs else None,
        "total_runtime_s": int(sum(durs)) if durs else 0,
        "emails_today": sum(e["emails"] for e in todays_logs),
        "whatsapp_today": sum(e["whatsapp"] for e in todays_logs),
        "files_today": _cached("files_today", 60, _files_today),
        "running": running,
        "recent": todays_logs[:8],
    }

def summary():
    return _cached("summary", 10, _summary_inner)


def overview():
    """Dashboard timeline: manual boxes + scheduler slots in one list, plus the counters."""
    from core import ops_sched
    tl = timeline()
    sc = _cached("sched_today", 10, ops_sched.today)
    for i in tl["items"]:
        i.setdefault("source", "box")
    items = tl["items"] + sc["items"]
    items.sort(key=lambda x: (x["start_min"] is None, x["start_min"] or 0))
    return {"now_min": tl["now_min"], "items": items}


# ── system health ─────────────────────────────────────────────────────────────
def _probe_tcp(host, port, timeout=2.5):
    t0 = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {"up": True, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"up": False, "ms": None, "err": type(e).__name__}


def _tasklist_counts():
    if os.name != "nt":
        return {}
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=6)
        low = r.stdout.lower()
        return {"chrome":  low.count('"chrome.exe"'),
                "outlook": low.count('"outlook.exe"'),
                "python":  low.count('"python.exe"') + low.count('"pythonw.exe"')}
    except Exception:
        return {}


def _sys_resources():
    out = {"cpu": None, "ram": None, "disks": []}
    try:
        import psutil
        out["cpu"] = psutil.cpu_percent(interval=None)
        out["ram"] = psutil.virtual_memory().percent
        try:
            out["uptime_s"] = int(time.time() - psutil.Process(os.getpid()).create_time())
        except Exception:
            pass
    except Exception:
        # ctypes RAM fallback (Windows only, no psutil needed)
        if os.name == "nt":
            try:
                import ctypes
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
                ms = MEMORYSTATUSEX(); ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                    out["ram"] = int(ms.dwMemoryLoad)
            except Exception:
                pass
    if "uptime_s" not in out:
        out["uptime_s"] = int(time.time() - _START_TS)
    for drive in ("C:\\", "D:\\"):
        try:
            u = shutil.disk_usage(drive)
            out["disks"].append({"drive": drive[0] + ":", "pct": round(100 * u.used / u.total),
                                 "free_gb": round(u.free / 2**30)})
        except Exception:
            continue
    return out


def _health_inner():
    probes = {
        "smtp":     (os.getenv("SMTP_SERVER", "smtp3.netcore.co.in"), os.getenv("SMTP_PORT", "587")),
        "ftp":      (os.getenv("FTP_HOST", ""), 21),
        "postgres": (os.getenv("PG_HOST", "localhost"), os.getenv("PG_PORT", "5432")),
        "internet": ("8.8.8.8", 53),
    }
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {k: ex.submit(_probe_tcp, h, p) for k, (h, p) in probes.items() if h}
        for k, f in futs.items():
            try:
                results[k] = f.result(timeout=5)
            except Exception:
                results[k] = {"up": False, "ms": None}
    for k in probes:
        results.setdefault(k, {"up": False, "ms": None, "err": "not configured"})

    procs = _cached("tasklist", 30, _tasklist_counts)
    results["chrome"]  = {"up": procs.get("chrome", 0) > 0,  "count": procs.get("chrome")}
    results["outlook"] = {"up": procs.get("outlook", 0) > 0, "count": procs.get("outlook")}
    results["python_workers"] = {"up": True, "count": procs.get("python")}
    results["groq_ai"] = {"up": bool(os.getenv("GROQ_API_KEY", "").strip()), "configured": bool(os.getenv("GROQ_API_KEY", "").strip())}
    results["fastapi"] = {"up": True}   # we answered, so we're up
    results["resources"] = _sys_resources()
    results["checked_at"] = datetime.datetime.now().strftime("%H:%M:%S")
    return results

def health():
    return _cached("health", 45, _health_inner)


# ── AI brief ──────────────────────────────────────────────────────────────────
def _facts():
    s = summary(); h = health(); tl = overview()
    nxt = next((i for i in tl["items"] if i["status"] in ("due", "overdue")), None) \
          or next((i for i in tl["items"] if i["status"] == "upcoming"), None)
    warn = []
    for name, label in (("smtp", "SMTP"), ("ftp", "FTP"), ("postgres", "PostgreSQL"), ("internet", "Internet")):
        r = h.get(name) or {}
        if not r.get("up"):
            warn.append(f"{label} is unreachable")
        elif r.get("ms") and r["ms"] > 900:
            warn.append(f"{label} latency is high ({r['ms']} ms)")
    if s["failed_today"]:
        warn.append(f"{s['failed_today']} execution(s) failed today")
    return {
        "hour": datetime.datetime.now().hour,
        "completed": s["runs_completed"], "total": s["runs_total"],
        "failed": s["failed_today"], "emails": s["emails_today"],
        "files": s["files_today"], "running": s["running"],
        "due": s["due_now"], "next": nxt, "warnings": warn,
        "success_rate": s["success_rate"],
    }


def _brief_fallback(f):
    """Deterministic briefing — always available, no network."""
    hr = f["hour"]
    greet = "Good morning." if hr < 12 else ("Good afternoon." if hr < 17 else "Good evening.")
    lines = [greet]
    if f["running"]:
        lines.append(f"▶ {f['running'][0]['label']} is running right now.")
    lines.append(f"{f['completed']} of {f['total']} scheduled runs are done today"
                 + (f" · success rate {f['success_rate']}%." if f["success_rate"] is not None else "."))
    if f["failed"]:
        lines.append(f"⚠ {f['failed']} execution(s) failed — check Execution History.")
    if f["files"]:
        lines.append(f"{f['files']} files downloaded and {f['emails']} emails sent so far.")
    for w in f["warnings"][:3]:
        if "failed today" not in w:
            lines.append(f"⚠ {w}.")
    if not f["warnings"]:
        lines.append("All systems look healthy.")
    if f["due"]:
        d = f["due"][0]
        lines.append(f"Recommended next action: run {d['id']} ({d['pipe_name']})"
                     + (" — it is overdue." if d.get("overdue") else " — its window is open."))
    elif f["next"]:
        lines.append(f"Next up: {f['next']['id']} ({f['next']['pipe_name']}) at {f['next']['window'].split('-')[0] if f['next']['window'] else '—'}.")
    else:
        lines.append("Nothing further is scheduled today.")
    return "\n".join(lines)


def _brief_inner(force_ai=True):
    f = _facts()
    text = _brief_fallback(f)
    ai_used = False
    key = os.getenv("GROQ_API_KEY", "").strip()
    if force_ai and key:
        try:
            import requests
            prompt = (
                "You are the operations copilot inside a stock-brokerage RMS automation platform. "
                "Rewrite the following status facts as a crisp briefing for the operations team: "
                "max 6 short lines, plain language, no markdown headers, keep any run IDs exactly "
                "as given, end with the single most useful recommended next action.\n\nFACTS:\n" + text
            )
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.3, "max_tokens": 220},
                timeout=6,
            )
            resp.raise_for_status()
            t = resp.json()["choices"][0]["message"]["content"].strip()
            if t:
                text, ai_used = t, True
        except Exception:
            pass
    return {"text": text, "ai": ai_used,
            "generated_at": datetime.datetime.now().strftime("%H:%M:%S"),
            "due": f["due"], "warnings": f["warnings"]}


def brief(refresh=False):
    if refresh:
        with _CACHE_LOCK:
            _CACHE.pop("brief", None)
    return _cached("brief", 120, _brief_inner)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — file explorer, download center index, AI assistant
# All still strictly READ-ONLY.
# ═══════════════════════════════════════════════════════════════════════════

_ROOT_LABELS = {
    "CLI_FOLDER": "Client Prudential", "SA_OUTPUT_DIR": "Short Allocation",
    "SA_COMMON_DIR": "Common Files", "MARGIN_SHORTAGE_FOLDER": "Margin Shortage (15-min)",
    "MARGIN_TOP20_FOLDER": "Margin Top20", "FTP_BASE_LOCAL": "FTP Files",
    "SA_LAS_FUNDING_DIR": "LAS Funding",
}

def file_roots():
    """Whitelisted browse roots: the pipeline data folders + the logs archive.
    Only these keys are ever accepted by the file endpoints — the client can
    NEVER pass a raw path."""
    roots = []
    for k in _WATCH_FOLDER_ENVS:
        p = os.getenv(k, "").strip()
        if p and os.path.isdir(p):
            roots.append({"key": k, "label": _ROOT_LABELS.get(k, k), "path": p})
    if os.path.isdir(LOG_BASE):
        roots.append({"key": "LOGS", "label": "Run Logs", "path": LOG_BASE})
    return roots

def _root_path(key):
    if key == "LOGS":
        return LOG_BASE if os.path.isdir(LOG_BASE) else None
    if key in _WATCH_FOLDER_ENVS:
        p = os.getenv(key, "").strip()
        return p if p and os.path.isdir(p) else None
    return None

def _safe_join(root_key, sub):
    """Resolve root+sub, guaranteeing the result stays inside the root."""
    base = _root_path(root_key)
    if not base:
        return None
    target = os.path.realpath(os.path.join(base, sub or ""))
    if not (target + os.sep).startswith(os.path.realpath(base) + os.sep) \
       and target != os.path.realpath(base):
        return None
    return target

def files_list(root, sub="", q=""):
    target = _safe_join(root, sub)
    if not target or not os.path.isdir(target):
        return {"error": "Folder not found", "items": []}
    items = []
    try:
        names = os.listdir(target)
    except OSError as e:
        return {"error": str(e), "items": []}
    ql = (q or "").strip().lower()
    for n in names[:2000]:
        if ql and ql not in n.lower():
            continue
        fp = os.path.join(target, n)
        try:
            isdir = os.path.isdir(fp)
            st = os.stat(fp)
            items.append({"name": n, "dir": isdir,
                          "size": None if isdir else st.st_size,
                          "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
        except OSError:
            continue
        if len(items) >= 800:
            break
    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return {"items": items, "sub": sub or ""}

_TEXT_EXT = (".log", ".txt", ".csv", ".json", ".md", ".py", ".bat", ".env", ".ini", ".tsv", ".html", ".xml")

def file_preview(root, sub):
    target = _safe_join(root, sub)
    if not target or not os.path.isfile(target):
        return {"error": "File not found"}
    st = os.stat(target)
    meta = {"name": os.path.basename(target), "size": st.st_size,
            "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")}
    if not target.lower().endswith(_TEXT_EXT):
        return {**meta, "binary": True}
    try:
        with open(target, "rb") as f:
            blob = f.read(262144)                     # first 256 KB
        text = blob.decode("utf-8", errors="replace")
        return {**meta, "binary": False, "text": text,
                "truncated": st.st_size > 262144}
    except Exception as e:
        return {**meta, "error": str(e)}

def file_abspath(root, sub):
    """Guarded absolute path for the download endpoint (or None)."""
    target = _safe_join(root, sub)
    return target if target and os.path.isfile(target) else None

def downloads_index(days=3):
    """Recent files across all pipeline data folders — the Download Center."""
    days = max(1, min(30, int(days or 3)))
    cutoff = time.time() - days * 86400
    out = []
    for r in file_roots():
        if r["key"] == "LOGS":
            continue
        try:
            names = os.listdir(r["path"])
        except OSError:
            continue
        for n in names[:4000]:
            fp = os.path.join(r["path"], n)
            try:
                if not os.path.isfile(fp):
                    continue
                mt = os.path.getmtime(fp)
                if mt < cutoff:
                    continue
                out.append({"name": n, "root": r["key"], "folder": r["label"],
                            "size": os.path.getsize(fp),
                            "mtime": datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M")})
            except OSError:
                continue
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return {"items": out[:600], "days": days}


# ── AI assistant ──────────────────────────────────────────────────────────────
def _log_tail_for(logdir, fname, chars=3200):
    """Guarded tail of one log file for 'explain this log' requests."""
    fp = os.path.join(LOG_BASE, os.path.basename(logdir or ""), os.path.basename(fname or ""))
    try:
        with open(fp, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            f.seek(max(0, size - chars * 2))
            return f.read().decode("utf-8", errors="replace")[-chars:]
    except Exception:
        return ""

def _ask_context(log_ref=None):
    f = _facts()
    lines = ["CURRENT PLATFORM STATE:", _brief_fallback(f), ""]
    rec = summary().get("recent") or []
    if rec:
        lines.append("TODAY'S EXECUTIONS (latest first):")
        for e in rec[:15]:
            lines.append(f"- {e['start'][11:16]} {e['pipe_name']} / {e['label']}: {e['status']}"
                         + (f" ({e['duration_s']}s)" if e.get("duration_s") else ""))
    # live locks + recent audit — lets the assistant answer "why is SA-01
    # disabled?", "who started CTCL?", "how long has it been running?"
    try:
        from core import lock_manager
        snap = lock_manager.snapshot()
        if snap["locks"]:
            lines.append("")
            lines.append("ACTIVE LOCKS (why Run buttons are disabled right now):")
            for l in snap["locks"]:
                lines.append(f"- {l['label']}: RUNNING, started by {l.get('by') or 'unknown user'}"
                             + (f" from {l['ip']}" if l.get("ip") else "")
                             + (f" at {(l.get('started_at') or '')[11:16]}" if l.get("started_at") else "")
                             + (f", elapsed {l['elapsed_s']}s" if l.get("elapsed_s") is not None else "")
                             + f"; this locks: {', '.join(l.get('affects') or []) or 'its own buttons only'}")
            lines.append("(There is NO queue by design — a second click is rejected; "
                         "the other operator should wait or use Stop.)")
        aud = lock_manager.read_audit(8)
        if aud:
            lines.append("")
            lines.append("RECENT AUDIT EVENTS (newest first):")
            for a in aud[:8]:
                lines.append(f"- {(a.get('ts') or '')[11:16]} {a.get('event')}: {a.get('label')}"
                             + (f" by {a.get('by')}" if a.get("by") else "")
                             + (f" → {a.get('outcome')}" if a.get("outcome") else "")
                             + (f" ({a.get('duration_s')}s)" if a.get("duration_s") else ""))
    except Exception:
        pass
    if log_ref and log_ref.get("file"):
        tail = _log_tail_for(log_ref.get("logdir"), log_ref.get("file"))
        if tail:
            lines += ["", f"LOG TAIL ({log_ref['file']}):", tail]
    return "\n".join(lines)

_ASK_SYSTEM = (
    "You are RMS Copilot, the operations assistant inside a stock brokerage's Risk "
    "Management automation platform. The platform runs four daily pipelines: CTCL vs "
    "NMASS (5 runs), Short Allocation (4 runs), Margin Shortage 15-min (11 runs) and "
    "Client Prudential Limit (2 runs). They download exchange files (NSE/MCX/NCDEX/FTP), "
    "process risk reports, and send emails/WhatsApp. Users are non-technical trading-desk "
    "operators. Answer from the CONTEXT given; be concise and practical; use the exact "
    "run IDs and step names from the context; when explaining a failure, say what most "
    "likely went wrong in plain language and what to do next (retry, check credentials in "
    "the Process Center, check the file in Downloads, or contact tech support). If the "
    "context doesn't contain the answer, say so honestly instead of guessing. "
    "STYLE RULES: answer the question directly in the first sentence. NEVER open with "
    "'Based on the provided context' or mention/describe the context sections — the user "
    "cannot see them. Prefer 2-5 short lines with real numbers over paragraphs. No filler."
)

def _ask_fallback(question, ctx_facts):
    q = (question or "").lower()
    s = summary()
    if "lock" in q or "disabled" in q or "who started" in q or "who ran" in q:
        try:
            from core import lock_manager
            locks = lock_manager.snapshot()["locks"]
            if locks:
                return "Currently running:\n" + "\n".join(
                    f"• {l['label']} — started by {l.get('by') or 'unknown user'}"
                    + (f" ({l['ip']})" if l.get("ip") else "")
                    + (f" at {(l.get('started_at') or '')[11:16]}" if l.get("started_at") else "")
                    + (f", running for {(l.get('elapsed_s') or 0) // 60}m {(l.get('elapsed_s') or 0) % 60}s" if l.get("elapsed_s") is not None else "")
                    for l in locks) + \
                    "\nAffected Run buttons are disabled everywhere until it finishes (or is stopped)."
            return "Nothing is locked right now — no process is running, so all Run buttons should be enabled."
        except Exception:
            pass
    if "fail" in q or "error" in q or "wrong" in q:
        fails = [e for e in (s.get("recent") or []) if e["status"] == "failed"]
        if not fails:
            return "Nothing has failed today. All finished executions completed cleanly."
        return ("Failed today:\n" + "\n".join(
            f"• {e['start'][11:16]} — {e['pipe_name']} / {e['label']}" for e in fails[:6]) +
            "\nOpen Execution History and click a row to read the full log.")
    if "next" in q or "run" in q or "due" in q:
        due = s.get("due_now") or []
        if due:
            d = due[0]
            return f"Run {d['id']} ({d['pipe_name']}) next — its window {d['window']} is " + \
                   ("overdue." if d.get("overdue") else "open now.")
        return "Nothing is due right now. Check the Scheduler for the next window."
    if "summar" in q or "today" in q or "status" in q:
        return _brief_fallback(_facts())
    # deterministic client lookup even without AI
    try:
        from core import knowledge
        import re as _re
        for tok in set(_re.findall(r"\b[A-Za-z0-9]{4,15}\b", question or "")):
            fc = knowledge.find_client(tok)
            if fc["total"]:
                return (f"Client {fc['client']} appears in {fc['total']} indexed reports. Most recent:\n" +
                        "\n".join(f"• {f['name']} — {f['folder']}, {f['day']}" for f in fc["files"][:8]) +
                        "\nOpen them from the Files page.")
    except Exception:
        pass
    return ("AI answers need a GROQ_API_KEY in .env (currently unavailable). "
            "I can still tell you what failed, what's due next, who holds a lock, "
            "summarize today, or find a client code across indexed reports — "
            "try one of the quick actions.")

def ask(question, log_ref=None):
    # COMPUTED-FIRST: if the question is analytical over an output file
    # (top N / lowest / count / sum), the knowledge engine opens the actual
    # file and computes the answer — exact numbers, zero model prose. The AI
    # is only used for questions that genuinely need language understanding.
    if not log_ref:
        try:
            from core import knowledge
            a = knowledge.analyze_question(question)
        except Exception:
            a = None
        if a and a.get("items"):
            fmt = None
            try:
                fmt = knowledge.kb_fmt_num
            except Exception:
                fmt = lambda v: f"{v:,.2f}"
            head = (f"{a['direction'].title()} {a['n']} by {a['metric']}"
                    + (f"  ·  {a['filter']}" if a["filter"] else "") + "\n")
            body = "\n".join(f"{i}. {c}  —  {fmt(v)}"
                             for i, (c, v) in enumerate(a["items"], 1))
            foot = (f"\n\n{a['matched']} matching rows (of {a['total_rows']} total)"
                    f" · sum of {a['metric']} = {fmt(a['sum'])}")
            return {"answer": head + body + foot, "ai": False, "computed": True,
                    "sources": [{"type": "output", "label": a["name"],
                                 "detail": f"{a['folder']} · {a['day']} · {a['matched']} rows ranked by {a['metric']}",
                                 "root": a["root"], "sub": a["sub"]}]}
    ctx = _ask_context(log_ref)
    # Phase 3: pack real evidence from the knowledge index (code symbols,
    # output files + their columns/sheets, client-code hits, process KB,
    # per-month aggregates). The returned sources are index rows — the UI
    # shows them verbatim, so citations can never be hallucinated.
    sources = []
    try:
        from core import knowledge
        kb_ctx, sources = knowledge.retrieve(question)
        if kb_ctx:
            ctx += "\n\nKNOWLEDGE INDEX EVIDENCE (code + processes + indexed outputs):\n" + kb_ctx
    except Exception:
        pass
    key = os.getenv("GROQ_API_KEY", "").strip()
    if key:
        try:
            import requests
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
                      "messages": [{"role": "system", "content": _ASK_SYSTEM +
                                    " Base every claim ONLY on the CONTEXT/EVIDENCE sections; if the "
                                    "evidence doesn't cover the question, say what IS known and what "
                                    "isn't indexed yet."},
                                    {"role": "user", "content": ctx + "\n\nQUESTION: " + (question or "")[:2000]}],
                      "temperature": 0.25, "max_tokens": 650},
                timeout=22,
            )
            resp.raise_for_status()
            t = resp.json()["choices"][0]["message"]["content"].strip()
            if t:
                return {"answer": t, "ai": True, "sources": sources}
        except Exception:
            pass
    return {"answer": _ask_fallback(question, ctx), "ai": False, "sources": sources}
