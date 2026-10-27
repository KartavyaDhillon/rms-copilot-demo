"""COMMON FILES - automatic fetch of today's HOLDING and RISK_REPORT from the back office FTP.

What it does
    Every trading day, from COMMON_FTP_START (07:30) until COMMON_FTP_STOP (08:15), every COMMON_FTP_EVERY_MIN (5) minutes:
      * a file that is already in COMMON FILES (uploaded by hand, or fetched earlier) and looks valid is NOT downloaded again;
      * a file that is on the FTP is downloaded (safe against a slow / big file: no overall time limit, resumes after a
        dropped connection, waits while the back office is still writing it), checked, then put in COMMON FILES;
      * after a download the SAME housekeeping as the "Upload Common Files" button runs - yesterday's HOLDING /
        RISK_REPORT go to COMMON FILES\\BACKUP FILES AUTOMATED\\<date> and into the database.
    If a file is still missing after COMMON_FTP_STOP, ONE alert e-mail goes to COMMON_FTP_ALERT_TO (from SENDER_EMAIL).
    The manual "Upload Common Files" button is untouched and keeps working.

Only the two files below are ever fetched.  Nothing on the FTP is ever deleted or changed.

.env
    COMMON_FTP_HOST / COMMON_FTP_USER / COMMON_FTP_PASS      (use a read-only account)
    COMMON_FTP_DIR          default /GINNI_AUTOMATION_FILE
    COMMON_FTP_PORT         default 21
    COMMON_FTP_TLS          true = explicit FTPS (use it if the server supports it), default false
    COMMON_FTP_ENABLED      default true
    COMMON_FTP_START / COMMON_FTP_STOP / COMMON_FTP_EVERY_MIN   default 07:30 / 08:15 / 5
    COMMON_FTP_ALERT_TO / COMMON_FTP_ALERT_CC               default rms_del@mastertrust.co.in / blank
"""
from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import threading
import time
from email.message import EmailMessage
from ftplib import FTP, FTP_TLS, all_errors, error_perm
from typing import Callable, Dict, List, Optional

KINDS = ("HOLDING", "RISK_REPORT")
_LOCK = threading.Lock()                 # one fetch at a time (the loop and the "Fetch now" button)
_STOP = threading.Event()
_THREAD: Optional[threading.Thread] = None
_STATE: dict = {"date": "", "files": {}, "last_attempt": "", "attempts": 0, "alerted": False, "done": False,
                "running": False, "message": ""}


# ───────────────────────── settings ─────────────────────────
def _env(k: str, d: str = "") -> str:
    return (os.getenv(k) or d).strip()


def common_dir() -> str:
    return _env("SA_COMMON_DIR", r"C:\COMMON FILES")


def enabled() -> bool:
    return _env("COMMON_FTP_ENABLED", "true").lower() not in ("0", "false", "no", "off")


def configured() -> bool:
    return bool(_env("COMMON_FTP_HOST") and _env("COMMON_FTP_USER") and _env("COMMON_FTP_PASS"))


def _hhmm(key: str, default: str) -> dt.time:
    try:
        h, m = _env(key, default).split(":")
        return dt.time(int(h), int(m))
    except Exception:
        h, m = default.split(":")
        return dt.time(int(h), int(m))


def window() -> tuple:
    return _hhmm("COMMON_FTP_START", "07:30"), _hhmm("COMMON_FTP_STOP", "08:15")


def every_seconds() -> int:
    try:
        return max(30, int(float(_env("COMMON_FTP_EVERY_MIN", "5")) * 60))
    except ValueError:
        return 300


def names_today(today: Optional[dt.date] = None) -> Dict[str, str]:
    d = (today or dt.date.today()).strftime("%d%m%Y")
    return {k: f"{k}_{d}.csv" for k in KINDS}


def log_dir() -> str:
    return os.path.join(common_dir(), "FTP FETCH LOGS")


def _log(msg: str) -> None:
    """Console line + a daily log file (FTP FETCH LOGS\\ftp_fetch_<date>.log) so the history can be read later from the screen."""
    line = f"[common-ftp {dt.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(log_dir(), exist_ok=True)
        with open(os.path.join(log_dir(), f"ftp_fetch_{dt.date.today():%Y-%m-%d}.log"), "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d} {line[len('[common-ftp '):].replace('] ', ' ', 1)}\n")
    except Exception:
        pass


def read_log(date: str = "", lines: int = 300) -> dict:
    d = date or f"{dt.date.today():%Y-%m-%d}"
    fp = os.path.join(log_dir(), f"ftp_fetch_{d}.log")
    days = sorted(x[10:20] for x in (os.listdir(log_dir()) if os.path.isdir(log_dir()) else []) if x.startswith("ftp_fetch_") and x.endswith(".log"))
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            txt = "".join(f.readlines()[-lines:])
    except Exception:
        txt = ""
    return {"date": d, "days": days[-30:], "text": txt}


# ───────────────────────── local file checks ─────────────────────────
def local_ok(path: str, kind: str) -> bool:
    """A file already in COMMON FILES counts only if it is not empty and looks like the right CSV."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return False
        return _head_error(path, kind) == ""
    except OSError:
        return False


def _head_error(path: str, kind: str) -> str:
    """Same checks the Upload Common Files button does. '' = fine."""
    with open(path, "rb") as fh:
        head = fh.readline(65536).decode("utf-8-sig", "replace").upper()
    if kind == "RISK_REPORT" and "ACCOUNTID" not in head:
        return "this does not look like a risk report (no ACCOUNTID column)"
    if "," not in head and ";" not in head and "\t" not in head:
        return "this does not look like a CSV file"
    return ""


# ───────────────────────── FTP ─────────────────────────
def _connect():
    host, port = _env("COMMON_FTP_HOST"), int(_env("COMMON_FTP_PORT", "21") or 21)
    tls = _env("COMMON_FTP_TLS", "false").lower() in ("1", "true", "yes", "on")
    # timeout = how long ONE network read may sit idle (a big file keeps data flowing, so it never trips);
    # there is deliberately NO limit on the total time of a download.
    ftp = FTP_TLS(timeout=120) if tls else FTP(timeout=120)
    ftp.connect(host, port, timeout=30)
    ftp.login(_env("COMMON_FTP_USER"), _env("COMMON_FTP_PASS"))
    if tls:
        ftp.prot_p()
    ftp.set_pasv(True)
    ftp.voidcmd("TYPE I")
    ftp.cwd(_env("COMMON_FTP_DIR", "/GINNI_AUTOMATION_FILE"))
    return ftp


def _size(ftp, name: str) -> Optional[int]:
    try:
        return ftp.size(name)
    except all_errors:
        return None


def mark_source(folder: str, name: str, how: str, by: str = "") -> None:
    """Remember how a file got into COMMON FILES ('FTP' = fetched automatically / by the Fetch button, 'Manual' = uploaded by a person)."""
    try:
        fp = os.path.join(folder, ".file_sources.json")
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = {}
        d[name] = {"how": how, "by": by, "at": dt.datetime.now().strftime("%H:%M:%S")}
        for k in list(d)[:-20]:          # keep it small
            d.pop(k, None)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def read_sources(folder: str) -> dict:
    try:
        with open(os.path.join(folder, ".file_sources.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _download(ftp, name: str, dest_part: str, want_size: Optional[int], progress: Callable[[str], None], on_bytes: Optional[Callable[[int], None]] = None):
    """Download with resume: a dropped connection continues where it stopped (up to 6 tries). Returns the live connection."""
    last_err = None
    for attempt in range(1, 7):
        have = os.path.getsize(dest_part) if os.path.exists(dest_part) else 0
        if want_size is not None and have > want_size:               # corrupt leftover - start again
            os.remove(dest_part)
            have = 0
        if want_size is not None and have == want_size:
            return ftp
        try:
            with open(dest_part, "ab" if have else "wb") as out:
                _last = [0.0]
                def _w(chunk, _o=out):
                    _o.write(chunk)
                    if on_bytes:
                        _t = time.time()
                        if _t - _last[0] >= 1.0:              # tell the screen at most once a second
                            _last[0] = _t
                            try:
                                on_bytes(_o.tell())
                            except Exception:
                                pass
                ftp.retrbinary(f"RETR {name}", _w, rest=have or None)
            if want_size is None or os.path.getsize(dest_part) == want_size:
                return ftp
            last_err = f"got {os.path.getsize(dest_part)} of {want_size} bytes"
        except all_errors as e:
            last_err = str(e)
        progress(f"download interrupted ({last_err}) - retrying from {os.path.getsize(dest_part) if os.path.exists(dest_part) else 0:,} bytes (try {attempt}/6)")
        time.sleep(min(30, 5 * attempt))
        try:
            ftp.voidcmd("NOOP")
        except all_errors:
            try:
                ftp.close()
            except Exception:
                pass
            ftp = _connect()
    raise RuntimeError(f"download did not complete: {last_err}")


# ───────────────────────── one pass ─────────────────────────
def fetch_once(manual: bool = False) -> dict:
    """Fetch whatever of today's two files is missing. Returns the per-file result (also kept in status())."""
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "message": "a fetch is already running", "files": _STATE.get("files", {})}
    try:
        today = dt.date.today()
        if _STATE.get("date") != today.isoformat():
            _STATE.update(date=today.isoformat(), files={}, attempts=0, alerted=False, done=False)
        _STATE.update(running=True, last_attempt=dt.datetime.now().isoformat(timespec="seconds"), message="")
        _STATE["attempts"] = int(_STATE.get("attempts", 0)) + 1
        folder = common_dir()
        os.makedirs(folder, exist_ok=True)
        names = names_today(today)
        files = _STATE["files"]
        need: Dict[str, str] = {}
        for kind, name in names.items():
            p = os.path.join(folder, name)
            if local_ok(p, kind):
                if files.get(name, {}).get("status") not in ("downloaded",):
                    files[name] = {"status": "already there", "detail": "already in COMMON FILES (uploaded by hand or fetched earlier) - not downloaded",
                                   "at": dt.datetime.now().strftime("%H:%M:%S")}
            else:
                need[kind] = name
        if not need:
            _STATE.update(done=True, message="all of today's files are in COMMON FILES")
            return {"ok": True, "message": _STATE["message"], "files": files}
        if not configured():
            _STATE["message"] = "FTP is not set up (COMMON_FTP_HOST / USER / PASS in .env)"
            for kind, name in need.items():
                files[name] = {"status": "error", "detail": _STATE["message"], "at": dt.datetime.now().strftime("%H:%M:%S")}
            return {"ok": False, "message": _STATE["message"], "files": files}

        try:
            ftp = _connect()
        except all_errors + (OSError,) as e:
            _STATE["message"] = f"could not connect to the FTP: {e}"
            for kind, name in need.items():
                files[name] = {"status": "error", "detail": _STATE["message"], "at": dt.datetime.now().strftime("%H:%M:%S")}
            _log(_STATE["message"])
            return {"ok": False, "message": _STATE["message"], "files": files}

        try:
            try:
                listing = set(ftp.nlst())
            except error_perm:                       # some servers answer 550 for an empty folder
                listing = set()
            got_any = False
            for kind, name in need.items():
                at = lambda: dt.datetime.now().strftime("%H:%M:%S")
                if name not in listing:
                    files[name] = {"status": "not on ftp", "detail": "not on the FTP yet", "at": at()}
                    _log(f"{name}: not on the FTP yet")
                    continue
                s1 = _size(ftp, name)
                time.sleep(10)
                s2 = _size(ftp, name)
                if s1 is not None and s2 is not None and s1 != s2:
                    files[name] = {"status": "being written", "detail": f"the back office is still writing it ({s2:,} bytes) - will retry", "at": at()}
                    _log(f"{name}: still being written ({s1:,} -> {s2:,})")
                    continue
                part = os.path.join(folder, name + ".ftp.part")
                files[name] = {"status": "downloading", "detail": f"downloading {(s2 or 0) / 1048576:.1f} MB", "at": at()}
                _log(f"{name}: downloading {(s2 or 0) / 1048576:.1f} MB")
                try:
                    def _prog(done, n=name, tot=s2):
                        if tot:
                            pct = min(99, int(done * 100 / tot))
                            files[n] = {"status": "downloading", "pct": pct, "at": at(),
                                        "detail": f"downloading {done / 1048576:.0f} of {tot / 1048576:.0f} MB - {pct}%"}
                    ftp = _download(ftp, name, part, s2, lambda m, n=name: _log(f"{n}: {m}"), _prog)
                    err = _head_error(part, kind)
                    if os.path.getsize(part) == 0:
                        err = "the downloaded file is empty"
                    if err:
                        raise ValueError(err)
                    os.replace(part, os.path.join(folder, name))
                    mark_source(folder, name, "FTP")
                    files[name] = {"status": "downloaded", "detail": f"downloaded from the FTP ({os.path.getsize(os.path.join(folder, name)) / 1048576:.1f} MB)", "at": at()}
                    _log(f"{name}: downloaded")
                    got_any = True
                except Exception as e:
                    files[name] = {"status": "error", "detail": str(e), "at": at()}
                    _log(f"{name}: FAILED - {e}")
                    try:
                        os.remove(part)
                    except OSError:
                        pass
            if got_any:
                try:                                    # same as the Upload button: yesterday's files -> backup folder + database
                    from core.short_allocation import housekeep_common_files
                    housekeep_common_files()
                except Exception as e:
                    _log(f"housekeeping skipped: {e}")
        finally:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass
        done = all(local_ok(os.path.join(folder, n), k) for k, n in names.items())
        _STATE.update(done=done, message="all of today's files are in COMMON FILES" if done else "still waiting for: " + ", ".join(
            n for k, n in names.items() if not local_ok(os.path.join(folder, n), k)))
        _write_status()
        return {"ok": done, "message": _STATE["message"], "files": files}
    except Exception as e:                              # never let the background loop die
        _STATE["message"] = f"{type(e).__name__}: {e}"
        _log(f"unexpected error: {_STATE['message']}")
        return {"ok": False, "message": _STATE["message"], "files": _STATE.get("files", {})}
    finally:
        _STATE["running"] = False
        _LOCK.release()


def status() -> dict:
    s = dict(_STATE)
    s["configured"], s["enabled"] = configured(), enabled()
    a, b = window()
    s["window"] = f"{a:%H:%M}-{b:%H:%M}"
    s["names"] = names_today()
    return s


def _write_status() -> None:
    try:
        with open(os.path.join(common_dir(), ".ftp_fetch_status.json"), "w", encoding="utf-8") as f:
            json.dump(_STATE, f, indent=1)
    except OSError:
        pass


# ───────────────────────── alert e-mail ─────────────────────────
def send_alert(missing: List[str], detail: str) -> bool:
    """One e-mail from SENDER_EMAIL to COMMON_FTP_ALERT_TO. Never raises."""
    try:
        sender, pw = _env("SENDER_EMAIL"), _env("SENDER_PASSWORD")
        to = [e.strip() for e in _env("COMMON_FTP_ALERT_TO", "rms_del@mastertrust.co.in").replace(";", ",").split(",") if e.strip()]
        cc = [e.strip() for e in _env("COMMON_FTP_ALERT_CC").replace(";", ",").split(",") if e.strip()]
        if not (sender and pw and to):
            _log("alert not sent - SENDER_EMAIL / SENDER_PASSWORD / COMMON_FTP_ALERT_TO missing")
            return False
        a, b = window()
        msg = EmailMessage()
        msg["From"], msg["To"] = sender, ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = f"COMMON FILES not on the console yet - {dt.date.today():%d-%m-%Y}"
        msg.set_content(
            "Dear Team,\n\n"
            f"The console tried to fetch today's file(s) from the FTP between {a:%H:%M} and {b:%H:%M} "
            f"({_STATE.get('attempts', 0)} attempts) but these are still not in COMMON FILES:\n\n  - "
            + "\n  - ".join(missing) + f"\n\nLast result: {detail}\n\n"
            "Please upload them by hand with 'Upload Common Files' (top of the Process Center), or check the FTP folder.\n\n"
            "Regards,\nRMS Console (automatic message)")
        with smtplib.SMTP(_env("SMTP_SERVER", "smtp3.netcore.co.in"), int(_env("SMTP_PORT", "587") or 587), timeout=60) as s:
            s.ehlo(); s.starttls(); s.ehlo(); s.login(sender, pw)
            s.send_message(msg, to_addrs=to + cc)
        _log(f"alert e-mail sent to {', '.join(to)}")
        return True
    except Exception as e:
        _log(f"alert e-mail failed: {e}")
        return False


# ───────────────────────── background loop ─────────────────────────
def _trading_day(d: dt.date) -> bool:
    try:
        from core.scheduler import helpers as H
        return H.is_trading_day(d)
    except Exception:
        return d.weekday() < 5


def _sleep(seconds: float) -> None:
    _STOP.wait(seconds)


def _alert_if_missing() -> None:
    if _STATE.get("alerted") or _STATE.get("done"):
        return
    folder = common_dir()
    miss = [n for k, n in names_today().items() if not local_ok(os.path.join(folder, n), k)]
    if miss:
        _STATE["alerted"] = send_alert(miss, _STATE.get("message") or "not found on the FTP")
        if not _STATE["alerted"]:
            _STATE["alerted"] = True            # one try per day - never spam
    _write_status()


def run_loop() -> None:
    """Runs for the whole life of the server; one daily cycle."""
    _log("auto-fetch started")
    while not _STOP.is_set():
        try:
            today = dt.date.today()
            if _STATE.get("date") != today.isoformat():
                _STATE.update(date=today.isoformat(), files={}, attempts=0, alerted=False, done=False, message="")
            if not enabled() or not _trading_day(today) or _STATE.get("done") or _STATE.get("alerted"):
                _sleep(60)
                continue
            start, stop = window()
            now = dt.datetime.now().time()
            if now < start:
                _sleep(min(60, max(1, (dt.datetime.combine(today, start) - dt.datetime.now()).total_seconds())))
                continue
            if now > stop and _STATE.get("attempts", 0) > 0:     # window is over and we already tried inside it
                _alert_if_missing()
                continue
            res = fetch_once()                      # (a server started after the window still gets ONE catch-up try)
            if res.get("ok"):
                continue
            if dt.datetime.now().time() > stop:
                _alert_if_missing()
                continue
            _sleep(every_seconds())
        except Exception as e:                      # the loop must never die
            _log(f"loop error: {type(e).__name__}: {e}")
            _sleep(60)


def start_background() -> None:
    """Called once from the server's startup. Returns at once; the work happens in a daemon thread."""
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    if not enabled():
        _log("auto-fetch is switched off (COMMON_FTP_ENABLED=false)")
        return
    if not configured():
        _log("auto-fetch not started - set COMMON_FTP_HOST / COMMON_FTP_USER / COMMON_FTP_PASS in .env (manual upload still works)")
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=run_loop, name="common-ftp", daemon=True)
    _THREAD.start()
