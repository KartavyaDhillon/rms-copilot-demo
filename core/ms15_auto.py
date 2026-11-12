"""core/ms15_auto.py - MARGIN SHORTAGE (15-min) AUTO RUN: starts every run by itself, with no one clicking.

For every run n (its time = start of MS15_RUN<n>_WINDOW in .env, e.g. 09:30) while the Auto switch is ON:

  T - 7 min  download BOTH MCX files (MCX 90% + MCX Deposit) in one go          (one TCS BANCS login, one RSA token)
  T - 1 min  download the NMASS / NCDEX files (CM / FO / CO / client net deposits)
  T          start the run. If a download is still going, WAIT up to 4 minutes (and until the files are really there).
             Files still missing after that -> a WhatsApp FAILURE ALERT that says plainly what is missing and why.
  Any other failure of the run itself -> the same kind of alert, at once.

Nothing is typed by a person: the RSA code comes from the soft token (MCX_TOKEN_MODE=auto) and the captchas from the
captcha solver. Which of the two MCX files the run USES is the separate "MCX file" choice (core/ctcl_mcx_source.py).

Settings and today's state live in logs/ms15/_auto_settings.json and _auto_state.json; every step is written to
logs/ms15/AUTORUN__<date>.log. The alert numbers are kept in the settings (Credentials > Auto run).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(REPO, "logs", "ms15")
_LOCK = threading.RLock()
_TICK = {"thread": None, "stop": threading.Event()}
_JOBS = {}                                   # (run, "mcx"|"nmass") -> {"thread", "ok", "msg", "t0", "t1"}
_READINESS = {"fn": None}                    # set by api.py: the same check the card's banner uses
TERMINAL = ("done", "failed", "stopped", "missed", "skipped")


# ───────────────────────── settings ─────────────────────────
def _settings_path():
    return os.path.join(LOG_DIR, "_auto_settings.json")


def _state_path():
    return os.path.join(LOG_DIR, "_auto_state.json")


def _env_int(name, default):
    try:
        return int(float(os.getenv(name, str(default))))
    except ValueError:
        return default


def defaults():
    return {
        "auto": os.getenv("MS15_AUTO", "").strip().lower() in ("1", "on", "true", "yes"),
        "mcx_lead_min": _env_int("MS15_AUTO_MCX_LEAD_MIN", 7),       # MCX files (both) start this long before the run
        "nmass_lead_min": _env_int("MS15_AUTO_NMASS_LEAD_MIN", 1),   # NMASS / NCDEX start this long before the run
        "wait_min": _env_int("MS15_AUTO_WAIT_MIN", 4),               # at run time: wait this long for downloads still going
        "grace_min": _env_int("MS15_AUTO_GRACE_MIN", 20),            # not started within this long after its time = "missed"
        "ctcl_prefetch": os.getenv("CTCL_MCX_PREFETCH", "on").strip().lower() not in ("0", "off", "no", "false"),
        "ctcl_lead_min": _env_int("CTCL_MCX_LEAD_MIN", 7),           # CTCL vs NMASS: MCX files this long before ITS run time
        "alert_numbers": os.getenv("MS15_WA_FAILURE_ALERT", ""),
    }


def get_settings():
    d = defaults()
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:
        saved = {}
    for b in ("auto", "ctcl_prefetch"):
        if b in saved:
            d[b] = bool(saved[b])
    if "alert_numbers" in saved:
        d["alert_numbers"] = str(saved["alert_numbers"] or "")
    for k in ("mcx_lead_min", "nmass_lead_min", "wait_min", "grace_min", "ctcl_lead_min"):
        if k in saved:
            try:
                d[k] = max(0, int(saved[k]))
            except (TypeError, ValueError):
                pass
    return d


def save_settings(update: dict, who: str = ""):
    """Validated save. Raises ValueError with a plain message."""
    cur = get_settings()
    for b in ("auto", "ctcl_prefetch"):
        if b in update:
            cur[b] = bool(update[b])
    if "alert_numbers" in update:
        from core.scheduler import failure_alert
        raw = str(update["alert_numbers"] or "").strip()
        if raw and not failure_alert.parse_numbers(raw):
            raise ValueError("no valid WhatsApp number found - use 10-digit or +91 numbers separated by commas")
        cur["alert_numbers"] = raw
    for k in ("mcx_lead_min", "nmass_lead_min", "wait_min", "grace_min", "ctcl_lead_min"):
        if k in update:
            try:
                v = int(update[k])
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a whole number")
            if not 0 <= v <= 120:
                raise ValueError(f"{k} must be between 0 and 120")
            cur[k] = v
    cur["saved_by"], cur["saved_at"] = who, dt.datetime.now().isoformat(timespec="seconds")
    os.makedirs(LOG_DIR, exist_ok=True)
    if cur["nmass_lead_min"] > cur["mcx_lead_min"] + 30:
        raise ValueError("NMASS/NCDEX lead looks wrong (more than 30 min above the MCX lead)")
    tmp = _settings_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=1)
    os.replace(tmp, _settings_path())
    return cur


# ───────────────────────── run times ─────────────────────────
def run_times() -> dict:
    """{run number: minute of day} from MS15_RUN<n>_WINDOW (the start of the window)."""
    from core import mcx_cash_maintenance as mc
    return {n: m for n, m in mc.ms15_windows()}


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


# ───────────────────────── today's state ─────────────────────────
def _today():
    return dt.date.today().isoformat()


def read_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            d = json.load(f)
        if d.get("date") == _today():
            return d
    except Exception:
        pass
    return {"date": _today(), "runs": {}}


def _write_state(d):
    os.makedirs(LOG_DIR, exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _state_path())


def _upd(n, **kw):
    with _LOCK:
        d = read_state()
        r = d["runs"].setdefault(str(n), {})
        r.update(kw)
        _write_state(d)
        return r


def run_state(n):
    return read_state()["runs"].get(str(n), {})


def log(msg):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    print("[ms15-auto] " + line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, f"AUTORUN__{dt.date.today():%Y%m%d}.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ───────────────────────── the WhatsApp failure alert ─────────────────────────
def alert_numbers():
    from core.scheduler import failure_alert
    return failure_alert.parse_numbers(get_settings().get("alert_numbers", ""))


def send_alert(n, headline, detail, test=False, sender=None):
    """WhatsApp 'FAILURE ALERT' picture to the alert numbers. -> {'sent', 'reason'}. Never raises."""
    rec = {"run": n, "sent": False, "reason": ""}
    try:
        numbers = alert_numbers()
        if not numbers:
            rec["reason"] = "no alert number set - add one in Credentials > Auto run"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        if sender is not None:
            sender(numbers, headline, detail)
            rec.update(sent=True, reason="sent")
            return rec
        from core.scheduler import connections, failure_alert, helpers as H, paths
        c = connections.get("whatsapp")
        if not c.get("auth_token"):
            rec["reason"] = "WhatsApp auth token is not set (Scheduler > Credentials > WhatsApp)"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        name = f"MARGIN SHORTAGE RUN {int(n):02d}" + (" (TEST)" if test else "")
        now = dt.datetime.now()
        img = failure_alert.render_image(
            paths.sched_root() / "alerts" / f"ms15_auto_{int(n):02d}_{int(time.time())}.png",
            process=name, schedule="Auto run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-",
            trigger="Auto run", attempt_text="final", headline=headline, detail=detail, run_id=f"MS-{int(n):02d}", kind="FAILED")

        class _C:
            def log(self, m):
                log(m)
        label = (("TEST - " if test else "") + f"FAILURE ALERT - {name}")[:60].upper()
        ok, bad = [], []
        for num in numbers:
            try:
                H.whatsapp_send_image(img, num, label, now.strftime("%d-%m-%Y %H:%M"), _C(),
                                      template_name=(c.get("alert_template") or "").strip() or None)
                ok.append(num[-4:])
            except Exception as e:                               # noqa: BLE001
                bad.append(f"{num[-4:]}: {type(e).__name__}: {str(e)[:100]}")
        rec.update(sent=bool(ok), reason=("sent to ..." + ", ...".join(ok)) if ok else "all sends failed: " + "; ".join(bad))
        log("WhatsApp failure alert " + ("SENT to ..." + ", ...".join(ok) if ok else "FAILED: " + "; ".join(bad)))
    except Exception as e:                                       # noqa: BLE001
        rec["reason"] = f"{type(e).__name__}: {e}"
        log("WhatsApp failure alert error: " + rec["reason"])
    return rec


# ───────────────────────── the two downloads (each in its own thread) ─────────────────────────
def _consume(stream):
    ok, msg = False, "no result"
    for ev in stream:
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t == "done":
            ok, msg = bool(ev.get("ok")), str(ev.get("msg") or "")
        elif t == "error":
            ok, msg = False, str(ev.get("msg") or "error")
    return ok, msg


def _dl_mcx():
    from core.pipelines.mcx90_download import stream_mcx90_download
    return _consume(stream_mcx90_download(pipe="ms15", both=True, extra_env={"MCX_TOKEN_MODE": "auto"}))


def _dl_nmass():
    from core.pipelines.nmass_download import stream_nmass_download
    return _consume(stream_nmass_download(pipe="ms15"))


_LABEL = {"mcx": "MCX files (MCX 90% + MCX Deposit)", "nmass": "NMASS / NCDEX files"}


def start_download(n, what, fn=None):
    """Start the download for run n in a thread (once). what = 'mcx' | 'nmass'. -> the job dict."""
    key = (int(n), what)
    with _LOCK:
        job = _JOBS.get(key)
        if job and job.get("date") == _today():
            return job
        fn = fn or (_dl_mcx if what == "mcx" else _dl_nmass)
        job = {"date": _today(), "ok": None, "msg": "", "t0": time.time(), "t1": None, "thread": None}

        def work():
            log(f"Run {n:02d}: {_LABEL[what]} download started.")
            try:
                ok, msg = fn()
            except Exception as e:                               # noqa: BLE001
                ok, msg = False, f"{type(e).__name__}: {e}"
            job.update(ok=ok, msg=msg, t1=time.time())
            _upd(n, **{what: "done" if ok else "failed", what + "_msg": msg[:300]})
            log(f"Run {n:02d}: {_LABEL[what]} download " + ("finished." if ok else f"FAILED: {msg}"))
        _upd(n, **{what: "running"})
        th = threading.Thread(target=work, name=f"ms15-auto-{what}-{n}", daemon=True)
        job["thread"] = th
        _JOBS[key] = job
        th.start()
        return job


def _job_alive(n, what):
    j = _JOBS.get((int(n), what))
    return bool(j and j.get("date") == _today() and j.get("thread") and j["thread"].is_alive())


# ───────────────────────── readiness (are the files really there) ─────────────────────────
def set_readiness(fn):
    _READINESS["fn"] = fn


def missing_files():
    """-> list of 'NAME - why' for every needed input that is missing or too old (empty = all good)."""
    fn = _READINESS["fn"]
    if fn is None:
        import importlib
        mod = importlib.import_module("api")
        fn = mod.ms15_readiness
    res = fn() or {}
    out = []
    for it in res.get("items", []):
        if it.get("ok") or it.get("upload_only"):
            continue
        out.append(f"{it.get('key')} - {it.get('why') or 'missing'}")
    return out


def _is_mcx_file(name: str) -> bool:
    from core import stale_files as sf
    return name.lower().endswith(".xlsx") and (bool(sf._MCX_STAMPED.search(name)) or name.upper().startswith(sf._MCX_OWN_DEPOSIT))


def park_late_mcx(n, since: float, until: float, base=None, backup=None) -> list:
    """An MCX file that arrived AFTER run n had already started was not used by it: rename it NOT_USED__<name> and move it
    into run n's backup folder (the same place the run puts its own unused MCX file).  -> list of moved names."""
    base = base or os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    if backup is None:
        from core import mcx_cash_maintenance as mc
        backup = str(mc.ms15_folder(dt.date.today(), _counter_n() or n))
    moved = []
    try:
        names = os.listdir(base)
    except OSError:
        return moved
    for f in names:
        p = os.path.join(base, f)
        try:
            if not (os.path.isfile(p) and _is_mcx_file(f) and since <= os.path.getmtime(p) <= until + 5):
                continue
            os.makedirs(backup, exist_ok=True)
            dest = os.path.join(backup, f if f.startswith("NOT_USED__") else "NOT_USED__" + f)
            os.replace(p, dest)
            moved.append(f)
        except OSError:
            continue
    if moved:
        log(f"Run {int(n):02d}: MCX file(s) that came after the run had started were not used - moved to {backup} as NOT_USED: {', '.join(moved)}")
    return moved


def _late_mcx_watch(n, run_t0):
    """The run did not wait for the second MCX file. When that download ends, the file it brought is parked as NOT_USED."""
    def w():
        j = _JOBS.get((int(n), "mcx"))
        if j and j.get("thread"):
            j["thread"].join(timeout=30 * 60)
        park_late_mcx(n, run_t0, time.time())
    threading.Thread(target=w, name=f"ms15-auto-late-{n}", daemon=True).start()


# ───────────────────────── one automatic run ─────────────────────────
def _run_pipeline():
    """Runs the Margin Shortage pipeline exactly like the Run button does. -> (ok, message)."""
    from core.pipeline_box import stream_pipeline
    ok, msg = False, "no result"
    for ev in stream_pipeline("ms15", extra_env={"MS15_ALLOW_UPLOAD": "0"}, by="Auto run", ip=""):
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t == "done":
            ok, msg = bool(ev.get("ok")), str(ev.get("err") or ev.get("msg") or ("finished" if ev.get("ok") else "failed"))
        elif t == "error":
            ok, msg = False, str(ev.get("msg") or "error")
        elif t == "stopped":
            ok, msg = False, "stopped by a person"
    return ok, msg


def run_slot(n, st=None, now_fn=time.time, sleep=time.sleep, missing=None, run=None, alert=None, start=None):
    """Run n: make sure the downloads are started, wait up to wait_min for files, then run. -> final state dict."""
    st = st or get_settings()
    missing = missing or missing_files
    run = run or _run_pipeline
    alert = alert or send_alert
    start = start or start_download
    _upd(n, status="running", started=dt.datetime.now().strftime("%H:%M:%S"))
    # the timer normally started them already (T-7 / T-1); if it was late, start them now
    for what in ("mcx", "nmass"):
        if run_state(n).get(what) not in ("done", "running", "failed"):
            log(f"Run {n:02d}: {_LABEL[what]} had not been started yet - starting it now.")
            start(n, what)
    deadline = now_fn() + st["wait_min"] * 60
    waited = False

    def _state():
        # only the files the run really needs count: the MCX file in use (MCX 90% OR Deposit) and the NMASS/NCDEX files.
        # The other MCX file may still be downloading - the run does not wait for it.
        gaps = missing() or []
        need_mcx = any(g.startswith("MCX") for g in gaps)
        need_nmass = any(not g.startswith("MCX") for g in gaps)
        busy = [w for w, need in (("mcx", need_mcx), ("nmass", need_nmass)) if need and _job_alive(n, w)]
        return busy, gaps
    while True:
        busy, gaps = _state()
        if not gaps or now_fn() >= deadline:
            break
        if not waited:
            waited = True
            why = ("still downloading: " + ", ".join(_LABEL[w] for w in busy)) if busy else "files not ready: " + "; ".join(gaps)
            log(f"Run {n:02d}: {why}. Waiting up to {st['wait_min']} min.")
            _upd(n, status=f"waiting for files (max {st['wait_min']} min)")
        sleep(5)
    busy, gaps = _state()
    if busy or gaps:
        reasons = []
        for w in ("mcx", "nmass"):
            rs = run_state(n)
            if w in busy:
                reasons.append(f"{_LABEL[w]} download was still running after {st['wait_min']} minutes")
            elif rs.get(w) == "failed":
                reasons.append(f"{_LABEL[w]} download failed: {rs.get(w + '_msg', '')[:160]}")
        head = "Margin Shortage did not run: required files are missing."
        detail = "; ".join(reasons + (["Missing/old: " + ", ".join(gaps)] if gaps else [])) or "files not available"
        detail += f" - Please download them and run {int(n):02d} by hand."
        log(f"Run {n:02d}: FAILED before start - {detail}")
        rec = alert(n, head, detail[:600])
        return _upd(n, status="failed", ended=dt.datetime.now().strftime("%H:%M:%S"), msg=detail[:300],
                    alert_sent=bool(rec.get("sent")), alert_reason=rec.get("reason", ""))
    _upd(n, status="running")
    log(f"Run {n:02d}: all files ready - starting the run.")
    run_t0 = time.time()
    try:
        ok, msg = run()
    except Exception as e:                                       # noqa: BLE001
        ok, msg = False, f"{type(e).__name__}: {e}"
    if _job_alive(n, "mcx"):
        _late_mcx_watch(n, run_t0)
    if ok:
        log(f"Run {n:02d}: finished successfully.")
        return _upd(n, status="done", ended=dt.datetime.now().strftime("%H:%M:%S"), msg="finished", by="auto")
    if "stopped by a person" in msg:
        log(f"Run {n:02d}: stopped by a person.")
        return _upd(n, status="stopped", ended=dt.datetime.now().strftime("%H:%M:%S"), msg=msg)
    log(f"Run {n:02d}: run failed - {msg}")
    rec = alert(n, "The Margin Shortage run stopped with an error.",
                f"{msg[:400]} - Please check the Logs of the Margin Shortage card and run {int(n):02d} by hand.")
    return _upd(n, status="failed", ended=dt.datetime.now().strftime("%H:%M:%S"), msg=msg[:300],
                alert_sent=bool(rec.get("sent")), alert_reason=rec.get("reason", ""))


# ───────────────────────── the timer ─────────────────────────
def _counter_n():
    try:
        base = os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
        a, b = open(os.path.join(base, "run_counter.txt"), encoding="utf-8", errors="replace").read().strip().split("|", 1)
        return int(b) if a.strip() == _today() else 0
    except Exception:
        return 0


def due_action(now: dt.datetime, st=None, state=None, times=None, counter=None):
    """What the timer should do now -> ('mcx'|'nmass'|'run'|'mark_done'|'missed', n) | None. Pure (testable)."""
    st = st or get_settings()
    if not st["auto"]:
        return None
    state = state if state is not None else read_state()
    times = times if times is not None else run_times()
    counter = _counter_n() if counter is None else counter
    cur = now.hour * 60 + now.minute
    for n in sorted(times):
        t = times[n]
        rs = state["runs"].get(str(n), {})
        stat = str(rs.get("status", ""))
        if stat in TERMINAL or stat.startswith(("running", "waiting")):
            continue
        if cur < t - max(st["mcx_lead_min"], st["nmass_lead_min"]):
            continue
        if cur < t:
            if not rs.get("mcx") and t - st["mcx_lead_min"] <= cur:
                return ("mcx", n)
            if not rs.get("nmass") and t - st["nmass_lead_min"] <= cur:
                return ("nmass", n)
            continue
        if counter >= n:
            return ("mark_done", n)                              # already completed (by hand, or on another PC)
        if cur - t > st["grace_min"]:
            return ("missed", n)
        return ("run", n)
    return None


# ───────────────────────── CTCL vs NMASS: MCX files before ITS runs ─────────────────────────
MAX_AGE_MIN = 45


def ctcl_times() -> dict:
    """{run number: minute of day} from CTCL_RUN<n>_WINDOW (start of the window)."""
    out = {}
    for i in range(1, 40):
        m = re.match(r"(\d{1,2}):(\d{2})", os.getenv(f"CTCL_RUN{i}_WINDOW", "").strip())
        if m:
            out[i] = int(m.group(1)) * 60 + int(m.group(2))
    return out


def ctcl_file_age_min(now_ts=None):
    """Minutes since the newest MCX file OF THE KIND CTCL USES (MCX 90% or Deposit) came into the CTCL folder; None = none."""
    try:
        from core import ctcl_mcx_source as src
        kind = src.get_source("ctcl")
    except Exception:
        kind = "mcx90"
    folder = os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    best = None
    try:
        for f in os.listdir(folder):
            low = f.lower()
            ok = low.startswith("mcx_deposit_") if kind == "deposit" else bool(re.search(r"-\d{14}\.xlsx$", low))
            if ok and low.endswith(".xlsx") and not low.startswith("not_used__"):
                t = os.path.getmtime(os.path.join(folder, f))
                best = t if best is None or t > best else best
    except OSError:
        return None
    return None if best is None else ((now_ts or time.time()) - best) / 60.0


def due_ctcl(now: dt.datetime, st=None, state=None, times=None, age=None):
    """-> CTCL run number whose MCX files must be downloaded now, or None.  At T - ctcl_lead_min, ONLY when the newest file
    CTCL would use would be older than 45 minutes at its run time (a fresh Margin Shortage file is used instead)."""
    st = st or get_settings()
    if not st.get("ctcl_prefetch"):
        return None
    state = state if state is not None else read_state()
    times = times if times is not None else ctcl_times()
    cur = now.hour * 60 + now.minute
    lead = st["ctcl_lead_min"]
    for n in sorted(times):
        t = times[n]
        if (state.get("ctcl") or {}).get(str(n)) or not (t - lead <= cur < t):
            continue
        a = ctcl_file_age_min() if age is None else age
        if a is not None and a + lead <= MAX_AGE_MIN:
            return ("skip", n, a)
        return ("download", n, a)
    return None


def _ctcl_mark(n, what):
    with _LOCK:
        d = read_state()
        d.setdefault("ctcl", {})[str(n)] = what
        _write_state(d)


def _ctcl_download(n):
    def work():
        from core.pipelines.mcx90_download import stream_mcx90_download
        log(f"CTCL vs NMASS run {n:02d}: downloading both MCX files for it now.")
        ok, msg = _consume(stream_mcx90_download(pipe="ctcl", both=True, extra_env={"MCX_TOKEN_MODE": "auto"}))
        _ctcl_mark(n, "done" if ok else "failed")
        log(f"CTCL vs NMASS run {n:02d}: MCX download " + ("finished." if ok else f"FAILED: {msg}"))
    _ctcl_mark(n, "running")
    threading.Thread(target=work, name=f"ctcl-mcx-{n}", daemon=True).start()


def tick_ctcl(now=None):
    act = due_ctcl(now or dt.datetime.now())
    if not act:
        return None
    kind, n, age = act
    if kind == "skip":
        _ctcl_mark(n, "skipped")
        log(f"CTCL vs NMASS run {n:02d}: its MCX file is {age:.0f} min old (fresh enough, from Margin Shortage) - no download.")
    else:
        _ctcl_download(n)
    return act


def _pipeline_busy():
    try:
        from core.pipeline_box import _PB
        return bool(_PB.get("ms15", {}).get("running"))
    except Exception:
        return False


def tick(now=None, **kw):
    """One look at the clock. Downloads start in threads (not blocking); a run blocks until it ends. -> the action or None."""
    now = now or dt.datetime.now()
    act = due_action(now)
    if not act:
        return None
    kind, n = act
    st = get_settings()
    if kind in ("mcx", "nmass"):
        start_download(n, kind, **{k: v for k, v in kw.items() if k == "fn"})
    elif kind == "mark_done":
        _upd(n, status="done", msg="already completed before the auto run", by="manual")
    elif kind == "missed":
        _upd(n, status="missed", msg="not started in time")
        log(f"Run {n:02d}: its time passed more than {st['grace_min']} minutes ago and it did not run - marked missed.")
        send_alert(n, "The automatic run did not start at its time.",
                   f"Margin Shortage run {n:02d} was due at {hhmm(run_times().get(n, 0))} but did not start. Please run it by hand.")
    elif kind == "run":
        if _pipeline_busy():
            return None                                          # someone is running it by hand: look again in a moment
        extra = {k: v for k, v in kw.items() if k in ("run", "sleep", "alert", "missing", "start", "now_fn")}
        if extra:
            run_slot(n, st, **extra)
        else:                                                    # own thread: the timer keeps ticking (CTCL downloads) meanwhile
            _upd(n, status="running", started=dt.datetime.now().strftime("%H:%M:%S"))
            threading.Thread(target=run_slot, args=(n, st), name=f"ms15-auto-run-{n}", daemon=True).start()
    return act


def start_ticker(interval_s: int = 15):
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return False
    _TICK["stop"].clear()

    def loop():
        log("Margin Shortage Auto Run timer started.")
        while not _TICK["stop"].wait(interval_s):
            try:
                tick_ctcl()
            except Exception as e:                               # noqa: BLE001
                print(f"[ms15-auto] ctcl error: {type(e).__name__}: {e}", flush=True)
            try:
                tick()
            except Exception as e:                               # noqa: BLE001
                print(f"[ms15-auto] error: {type(e).__name__}: {e}", flush=True)
    th = threading.Thread(target=loop, name="ms15-auto", daemon=True)
    _TICK["thread"] = th
    th.start()
    return True


def stop_ticker():
    _TICK["stop"].set()


# ───────────────────────── for the screen ─────────────────────────
def status():
    st = get_settings()
    state = read_state()
    times = run_times()
    rows = []
    counter = _counter_n()
    for n in sorted(times):
        t = times[n]
        rs = state["runs"].get(str(n), {})
        # missed / failed by the timer, but the run was then done by hand (the run counter moved past it): it is DONE now
        if counter >= n and (rs.get("status") or "") in ("missed", "failed", "stopped", "skipped", ""):
            was = rs.get("status") or ""
            try:
                rs = _upd(n, status="done", by="manual", msg="run by hand" + (f" (the auto run was {was})" if was else ""))
            except Exception:
                rs = dict(rs, status="done", by="manual")
        by = rs.get("by") or ("auto" if rs.get("status") == "done" else "")
        rows.append({"run": n, "time": hhmm(t), "mcx_at": hhmm(max(0, t - st["mcx_lead_min"])),
                     "nmass_at": hhmm(max(0, t - st["nmass_lead_min"])), "state": rs.get("status", ""),
                     "mcx": rs.get("mcx", ""), "nmass": rs.get("nmass", ""), "msg": rs.get("msg", ""),
                     "alert_sent": rs.get("alert_sent"), "alert_reason": rs.get("alert_reason", ""), "by": by})
    return {"settings": st, "runs": rows, "now": dt.datetime.now().strftime("%H:%M"),
            "ctcl": [{"run": n, "time": hhmm(t), "mcx_at": hhmm(max(0, t - st["ctcl_lead_min"])),
                      "state": (state.get("ctcl") or {}).get(str(n), "")} for n, t in sorted(ctcl_times().items())],
            "timer": bool(_TICK["thread"] and _TICK["thread"].is_alive()),
            "alert_numbers_count": len(alert_numbers())}


def skip_today(n, undo=False):
    return _upd(n, status="" if undo else "skipped", msg="" if undo else "skipped by a person")
