"""core/sa_auto.py - SHORT ALLOCATION AUTO RUN: starts a run by itself at its time, with no one clicking.

What one automatic run does (for a run whose Auto switch is ON):

  1. PRE-STEP, 10 minutes before the run time (runs 1-6 only): download the MCX Deposit file (the "MCX Deposit" button).
     It is SKIPPED when a MCX_DEPOSIT file is already in the base folder and was modified less than 45 minutes ago
     (somebody uploaded it, or an earlier try got it). The RSA code is filled by itself (Auto-fill CAPTCHA / TOKEN).
  2. At the run time: Download -> SA Processing (-> LAS Funding for runs 1-4; runs 5-8 never run LAS Funding).
     No pop-up ever opens. If an exchange peak file has not arrived yet the run waits WAIT minutes (15, the same for every
     automatic run 1-8) and tries again, RETRIES times (2). Still missing after that (~30 min) -> a WhatsApp FAILURE ALERT.
  3. Any other failure sends the WhatsApp alert at once (more tries would not help).

Settings (Short Allocation > Auto Run): per run the time and the Auto switch, plus the wait / retry / freshness numbers.
Defaults come from .env (SA_RUN5_TIME ... SA_AUTO_RUN5 ...) so nothing needs to be typed to start. Alert numbers:
the WhatsApp numbers button, row "FAILURE ALERT (auto run)" (.env key SA_WA_FAILURE_ALERT).

Every step is written to logs/short_allocation/AUTORUN__<date>.log (it appears in the Logs window).
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import threading
import time

from core import short_allocation as SA

MAX_RUN = SA.MAX_RUN
DEPOSIT_RUNS = 6                       # the MCX deposit pre-step exists for runs 1-6 only
_DEFAULT_TIME = {5: "17:45", 6: "19:30", 7: "21:30", 8: "23:30"}
_DEFAULT_AUTO = {5: True, 6: True}     # for now only runs 5 and 6 are automatic
_LOCK = threading.RLock()
_TICK = {"thread": None, "stop": threading.Event(), "busy": None}


# ───────────────────────── settings ─────────────────────────
def _settings_path():
    return os.path.join(SA.LOG_DIR, "_auto_settings.json")


def _state_path():
    return os.path.join(SA.LOG_DIR, "_auto_state.json")


def _env_int(name, default):
    try:
        return int(float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _hhmm(s):
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(s or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return f"{h:02d}:{mi:02d}" if 0 <= h < 24 and 0 <= mi < 60 else None


def _window_start(n):
    try:
        w = (SA.cfg()["windows"].get(n) or "").split("-")[0]
        return _hhmm(w)
    except Exception:
        return None


def defaults():
    runs = {}
    for n in range(1, MAX_RUN + 1):
        t = _hhmm(os.getenv(f"SA_RUN{n}_TIME")) or _DEFAULT_TIME.get(n) or _window_start(n) or ""
        a = os.getenv(f"SA_AUTO_RUN{n}", "").strip().lower()
        auto = (a in ("1", "on", "true", "yes")) if a else _DEFAULT_AUTO.get(n, False)
        runs[str(n)] = {"time": t, "auto": auto}
    return {
        "runs": runs,
        "predl_min": _env_int("SA_AUTO_PREDL_MIN", 10),        # MCX deposit starts this long before the run
        "fresh_min": _env_int("SA_AUTO_FRESH_MIN", 45),        # a deposit file newer than this is not downloaded again
        "wait_min": _env_int("SA_AUTO_WAIT_MIN", 15),          # wait for a missing peak file - EVERY automatic run (the old 5 minutes is only for a run started by hand)
        "retries": _env_int("SA_AUTO_RETRIES", 2),
        "grace_min": _env_int("SA_AUTO_GRACE_MIN", 90),        # a run not started within this long after its time is "missed"
    }


def get_settings():
    d = defaults()
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:
        saved = {}
    for n, r in (saved.get("runs") or {}).items():
        if n in d["runs"] and isinstance(r, dict):
            if _hhmm(r.get("time")):
                d["runs"][n]["time"] = _hhmm(r["time"])
            if "auto" in r:
                d["runs"][n]["auto"] = bool(r["auto"])
    for k in ("predl_min", "fresh_min", "wait_min", "retries", "grace_min"):
        if k in saved:
            try:
                d[k] = max(0, int(saved[k]))
            except (TypeError, ValueError):
                pass
    return d


def save_settings(update: dict, who: str = ""):
    """Validated save. Raises ValueError with a plain message."""
    cur = get_settings()
    for n, r in (update.get("runs") or {}).items():
        if str(n) not in cur["runs"]:
            raise ValueError(f"run {n} does not exist (1-{MAX_RUN})")
        if "time" in r:
            t = _hhmm(r["time"])
            if not t:
                raise ValueError(f"run {n}: '{r['time']}' is not a time (use HH:MM, 24-hour)")
            cur["runs"][str(n)]["time"] = t
        if "auto" in r:
            cur["runs"][str(n)]["auto"] = bool(r["auto"])
    for k in ("predl_min", "fresh_min", "wait_min", "retries", "grace_min"):
        if k in update:
            try:
                v = int(update[k])
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a whole number")
            if not 0 <= v <= 600:
                raise ValueError(f"{k} must be between 0 and 600")
            cur[k] = v
    cur["saved_by"], cur["saved_at"] = who, dt.datetime.now().isoformat(timespec="seconds")
    os.makedirs(SA.LOG_DIR, exist_ok=True)
    tmp = _settings_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=1)
    os.replace(tmp, _settings_path())
    return cur


def wait_minutes(n, st=None):
    """Same for every automatic run (1-8). A run started by hand keeps the downloader's own 5-minute wait."""
    return (st or get_settings())["wait_min"]


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
    os.makedirs(SA.LOG_DIR, exist_ok=True)
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


def _log_path():
    return os.path.join(SA.LOG_DIR, f"AUTORUN__{dt.date.today():%Y%m%d}.log")


def log(msg):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    print("[sa-auto] " + line, flush=True)
    try:
        os.makedirs(SA.LOG_DIR, exist_ok=True)
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ───────────────────────── the MCX deposit pre-step ─────────────────────────
def deposit_file_age_min(now=None, folder=None):
    """Minutes since the newest MCX_DEPOSIT file in the base folder was modified; None when there is none."""
    folder = folder or SA.cfg()["output"]
    files = []
    for pat in ("MCX_DEPOSIT_*.xlsx", "MCX_DEPOSIT_*.csv"):
        files += glob.glob(os.path.join(folder, pat))
    if not files:
        return None
    newest = max(os.path.getmtime(f) for f in files)
    return ((now or time.time()) - newest) / 60.0


def deposit_is_fresh(st=None, now=None, folder=None):
    st = st or get_settings()
    age = deposit_file_age_min(now, folder)
    return age is not None and age < st["fresh_min"], age


def _download_deposit():
    """Runs the same downloader as the MCX Deposit button. -> (ok, message)."""
    from core.pipelines.mcx_deposit_download import stream_mcx_deposit_download
    ok, msg = False, "no result"
    for ev in stream_mcx_deposit_download(extra_env={"MCX_TOKEN_MODE": "auto"}):
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "done":
            ok, msg = bool(ev.get("ok")), str(ev.get("msg") or "")
        elif ev.get("type") == "error":
            ok, msg = False, str(ev.get("msg") or "error")
    return ok, msg


def step_deposit(n, st=None, now_fn=time.time, sleep=time.sleep, download=None, cancel=lambda: False):
    """Pre-step of run n. -> {'status': 'skipped'|'done'|'failed', 'msg'}. Never raises."""
    st = st or get_settings()
    download = download or _download_deposit
    fresh, age = deposit_is_fresh(st, now_fn())
    if fresh:
        m = f"MCX deposit file is already there (modified {age:.0f} min ago, less than {st['fresh_min']} min) - not downloaded again."
        log(m)
        return {"status": "skipped", "msg": m}
    log("MCX deposit file is missing or older than %d min - downloading it now." % st["fresh_min"]
        if age is not None else "No MCX deposit file in the base folder - downloading it now.")
    from core import tcs_session_lock
    last = ""
    for attempt in (1, 2):
        if cancel():
            break
        if not tcs_session_lock.acquire("sadeposit", wait_s=900, cancel=cancel, log=log):
            last = "the MCX login stayed busy (another download was using it)"
            continue
        try:
            ok, last = download()
        except Exception as e:                                  # noqa: BLE001
            ok, last = False, f"{type(e).__name__}: {e}"
        finally:
            tcs_session_lock.release("sadeposit")
        if ok:
            log("MCX deposit downloaded.")
            return {"status": "done", "msg": "MCX deposit downloaded."}
        log(f"MCX deposit try {attempt} failed: {last}")
        if attempt == 1:
            sleep(180)                                           # 3 minutes, then one more try
    return {"status": "failed", "msg": last or "MCX deposit could not be downloaded"}


# ───────────────────────── the WhatsApp failure alert ─────────────────────────
def alert_numbers():
    from core.scheduler import failure_alert
    return failure_alert.parse_numbers(os.getenv("SA_WA_FAILURE_ALERT", ""))


def send_alert(n, headline, detail, kind="SA_AUTO", sender=None, test=False):
    """WhatsApp 'FAILURE ALERT' picture to the numbers in 'FAILURE ALERT (auto run)'. -> {'sent', 'reason'}. Never raises."""
    rec = {"run": n, "sent": False, "reason": ""}
    try:
        SA._load()
        numbers = alert_numbers()
        if not numbers:
            rec["reason"] = "no number set - add one under WhatsApp numbers > FAILURE ALERT (auto run)"
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
        name = f"SHORT ALLOCATION SA-{n:02d}" + (" (TEST)" if test else "")
        now = dt.datetime.now()
        img = failure_alert.render_image(
            paths.sched_root() / "alerts" / f"sa_auto_{n:02d}_{int(time.time())}.png",
            process=name, schedule="Auto run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-",
            trigger="Auto run", attempt_text="final", headline=headline, detail=detail, run_id=f"SA-{n:02d}", kind=kind)

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
        rec.update(sent=bool(ok), reason="sent to ..." + ", ...".join(ok) if ok else "all sends failed: " + "; ".join(bad))
        log("WhatsApp failure alert " + ("SENT to ..." + ", ...".join(ok) if ok else "FAILED: " + "; ".join(bad)))
    except Exception as e:                                       # noqa: BLE001
        rec["reason"] = f"{type(e).__name__}: {e}"
        log("WhatsApp failure alert error: " + rec["reason"])
    return rec


def render_sample(n=5, test=True):
    """The exact picture the WhatsApp alert carries, drawn with sample wording. -> Path of the PNG."""
    from core.scheduler import failure_alert, paths
    now = dt.datetime.now()
    head = ("THIS IS A TEST - nothing has failed. " if test else "") + "The exchange peak file has not arrived yet: MCX_PeakMargin56720_20261028_05.csv."
    detail = f"Waited 30 minutes over 2 retries; the file was still missing. Please check the exchange / upload it by hand and run SA-{n:02d}."
    return failure_alert.render_image(
        paths.sched_root() / "alerts" / f"sa_auto_sample_{n:02d}.png",
        process=f"SHORT ALLOCATION SA-{n:02d}" + (" (TEST)" if test else ""), schedule="Auto run",
        failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-", trigger="Auto run", attempt_text="final",
        headline=head, detail=detail, run_id=f"SA-{n:02d}", kind="SA_AUTO")


# ───────────────────────── one automatic run ─────────────────────────
def _run_stream(n):
    """Runs run n through the same code as the Run button. -> {'ok','msg','peak_missing','stopped'}."""
    out = {"ok": False, "msg": "", "peak_missing": None, "stopped": False}
    tag = f"{n:02d}"
    try:
        from core import lock_manager as lm
        lm.on_start("sa", f"Short Allocation SA-{tag}", pipe="sa", by="Auto run", ip="")
    except Exception:
        lm = None
    outcome = "finished"
    try:
        for ev in SA.stream_short_allocation(tag, auto=True):
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            if t == "done":
                out["ok"], out["msg"] = bool(ev.get("ok")), str(ev.get("msg") or "")
                outcome = "completed" if out["ok"] else "failed"
            elif t == "error":
                out["ok"], out["msg"] = False, str(ev.get("msg") or "error")
                outcome = "failed"
            elif t == "stopped":
                out["stopped"], out["msg"] = True, str(ev.get("msg") or "stopped")
                outcome = "stopped"
            elif t == "log" and ev.get("msg"):
                pass
    finally:
        out["peak_missing"] = SA._SA.get("peak_missing")
        try:
            if lm:
                lm.on_end("sa", outcome)
        except Exception:
            pass
    return out


def run_slot(n, st=None, run=None, sleep=time.sleep, alert=None, cancel=lambda: False):
    """The whole main run of run n with the wait-and-retry rule. -> final state dict. Never raises."""
    st = st or get_settings()
    run = run or _run_stream
    alert = alert or send_alert
    wait = wait_minutes(n, st)
    tries = 1 + st["retries"]
    _upd(n, status="running", started=dt.datetime.now().strftime("%H:%M:%S"), attempts=0)
    last = {}
    for k in range(1, tries + 1):
        _upd(n, attempts=k, status="running" if k == 1 else f"retry {k - 1}")
        log(f"SA-{n:02d}: try {k} of {tries}.")
        try:
            last = run(n)
        except Exception as e:                                   # noqa: BLE001
            last = {"ok": False, "msg": f"{type(e).__name__}: {e}", "peak_missing": None, "stopped": False}
        if last.get("ok"):
            log(f"SA-{n:02d}: finished successfully.")
            return _upd(n, status="done", ended=dt.datetime.now().strftime("%H:%M:%S"), msg="finished")
        if last.get("stopped") or cancel():
            log(f"SA-{n:02d}: stopped by a person - no more automatic tries today.")
            return _upd(n, status="stopped", ended=dt.datetime.now().strftime("%H:%M:%S"), msg=last.get("msg", "stopped"))
        pm = last.get("peak_missing")
        if pm and k < tries:
            log(f"SA-{n:02d}: peak file(s) not there yet ({pm}). Waiting {wait} minutes, then trying again.")
            _upd(n, status=f"waiting for peak file ({wait} min)", missing=pm)
            sleep(wait * 60)
            continue
        break
    pm = last.get("peak_missing")
    waited = wait * st["retries"]
    if pm:
        head = f"The exchange peak file has not arrived yet: {pm}."
        detail = f"Waited {waited} minutes over {st['retries']} retries; the file was still missing. Please check the exchange / upload it by hand and run SA-{n:02d}."
    else:
        head = "The run stopped with an error."
        detail = (last.get("msg") or "see the run log") + f" - Please check the log of SA-{n:02d}."
    rec = alert(n, head, detail)
    return _upd(n, status="failed", ended=dt.datetime.now().strftime("%H:%M:%S"), msg=(pm or last.get("msg") or "failed")[:300],
                alert_sent=bool(rec.get("sent")), alert_reason=rec.get("reason", ""))


# ───────────────────────── the timer ─────────────────────────
def _mins(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _already_done(n):
    try:
        r = SA.get_run_status(f"SA-{n:02d}") or {}
    except Exception:
        r = {}
    return r.get("overall") == "done" or (n > SA.LAS_RUNS and r.get("code2") == "done")


def due_action(now: dt.datetime, st=None, state=None):
    """What the timer should do now -> ('deposit', n) | ('run', n) | ('missed', n) | None. Pure (testable)."""
    st = st or get_settings()
    state = state if state is not None else read_state()
    cur = now.hour * 60 + now.minute
    for n in range(1, MAX_RUN + 1):
        cfg = st["runs"].get(str(n)) or {}
        if not cfg.get("auto") or not cfg.get("time"):
            continue
        rs = state["runs"].get(str(n), {})
        if rs.get("status") in ("done", "failed", "stopped", "missed", "skipped", "running") or str(rs.get("status", "")).startswith(("retry", "waiting")):
            continue
        t = _mins(cfg["time"])
        if n <= DEPOSIT_RUNS and not rs.get("deposit") and t - st["predl_min"] <= cur < t:
            return ("deposit", n)
        if cur >= t:
            if _already_done(n):
                return ("mark_done", n)                          # somebody ran it by hand already
            if cur - t > st["grace_min"]:
                return ("missed", n)
            return ("run", n)
    return None


def tick(now=None, **kw):
    """One look at the clock. Runs at most one action (blocking). -> the action taken or None."""
    now = now or dt.datetime.now()
    if SA._SA.get("running"):
        return None                                              # a run is going on (manual or automatic): wait
    act = due_action(now)
    if not act:
        return None
    kind, n = act
    st = get_settings()
    if kind == "mark_done":
        _upd(n, status="done", msg="already completed before the auto run")
    elif kind == "missed":
        _upd(n, status="missed", msg="not started in time")
        log(f"SA-{n:02d}: the start time passed more than {st['grace_min']} minutes ago and it did not run - marked missed.")
        send_alert(n, "The automatic run did not start at its time.",
                   f"SA-{n:02d} was due at {st['runs'][str(n)]['time']} but the console did not start it (it may have been switched off). Please run it by hand.")
    elif kind == "deposit":
        r = step_deposit(n, st, **{k: v for k, v in kw.items() if k in ("download", "sleep", "now_fn")})
        _upd(n, deposit=r["status"], deposit_msg=r["msg"][:200])
        if r["status"] == "failed":
            send_alert(n, "The MCX Deposit file could not be downloaded.",
                       f"{r['msg'][:180]} - SA-{n:02d} will still try at its time; please upload the file by hand if it fails.")
    elif kind == "run":
        run_slot(n, st, **{k: v for k, v in kw.items() if k in ("run", "sleep", "alert")})
    return act


def start_ticker(interval_s: int = 20):
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return False
    _TICK["stop"].clear()

    def loop():
        log("Auto Run timer started.")
        while not _TICK["stop"].wait(interval_s):
            try:
                tick()
            except Exception as e:                               # noqa: BLE001
                print(f"[sa-auto] error: {type(e).__name__}: {e}", flush=True)
    th = threading.Thread(target=loop, name="sa-auto", daemon=True)
    _TICK["thread"] = th
    th.start()
    return True


def stop_ticker():
    _TICK["stop"].set()


# ───────────────────────── for the screen ─────────────────────────
def status():
    st = get_settings()
    state = read_state()
    now = dt.datetime.now()
    rows = []
    for n in range(1, MAX_RUN + 1):
        c = st["runs"][str(n)]
        rs = state["runs"].get(str(n), {})
        rows.append({"run": n, "time": c["time"], "auto": c["auto"], "state": rs.get("status", ""),
                     "deposit": rs.get("deposit", ""), "attempts": rs.get("attempts", 0), "msg": rs.get("msg", ""),
                     "alert_sent": rs.get("alert_sent"), "alert_reason": rs.get("alert_reason", "")})
    age = deposit_file_age_min()
    raw = [x.strip() for x in os.getenv("SA_WA_FAILURE_ALERT", "").split(",") if x.strip()]
    return {"settings": {k: v for k, v in st.items() if k != "runs"}, "runs": rows, "alert_numbers_raw": raw, "now": now.strftime("%H:%M"),
            "timer": bool(_TICK["thread"] and _TICK["thread"].is_alive()),
            "deposit_age_min": None if age is None else round(age, 1),
            "alert_numbers_count": len(alert_numbers())}


def skip_today(n, undo=False):
    return _upd(n, status="" if undo else "skipped", msg="" if undo else "skipped by a person")


def reset_today(n):
    """Forget today's automatic attempt of run n (so the timer may start it again if its time has not passed the grace)."""
    with _LOCK:
        d = read_state()
        d["runs"].pop(str(n), None)
        _write_state(d)
