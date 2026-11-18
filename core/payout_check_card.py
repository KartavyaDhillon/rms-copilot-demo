"""core/payout_check_card.py - PAYOUT CHECK card (PAYMENT_REQUEST_LOG_FILE.py + PAYOUT_FINAL.py as one console card).

The two hand scripts are already modules of the CTCL vs NMASS run (core/pipelines/payout_download.py and payout_check.py).
This card runs just those two on a fresh Update Limits file, so the 3:40 and the 4:50 payout check no longer need a full
CTCL vs NMASS run:

    Update Limits file (uploaded in the card)
      -> today's Payment Request Log from the back-office API (payout_download)
      -> clients with NEGATIVE Available Margin joined to the requests, TOTAL DEPOSIT added (payout_check)
      -> one mail (PAYOUT CHECKING FILE | <ddmmyyyy>) -> backup.

On purpose (same as the other cards):
    * nothing secret is in the code - back-office login = Credentials (shared with every back-office job), mail login =
      this card's sender or the shared one, To / CC = the Email To / CC window (the same "Payout Check" list as CTCL vs NMASS);
    * several runs a day are normal here (3:40, 4:50 ...), so a second run needs no password;
    * the mail is tried 5 times (the hand script tried for ever), Stop works between the steps and between mail tries.
"""
from __future__ import annotations

import collections
import contextlib
import datetime as dt
import json
import os
import re
import shutil
import sys
import threading
import time
import zipfile
from pathlib import Path

from core import cnc_f11_card as _cnc          # name_date / freshness_problem / dates_between are the same rules

NAME = "PAYOUT CHECK"
FOLDER_NAME = "PAYOUT CHECK"
PIPE = "payoutchk"
BACKUP_SUB = "BACKUP AUTOMATION"
STEP_LABEL = {"input": "Update Limits", "download": "Payment Request Log",
              "check": "Payout check + e-mail", "backup": "Backup"}
STEP_ORDER = ("input", "download", "check", "backup")
MAIL_DEFAULTS = {"REPORT": ("rms_del@mastertrust.co.in", "")}
MAIL_LABEL = {"REPORT": "Payout Check file"}
MAIL_TRIES = 5

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_LOG = collections.deque(maxlen=400)
_ACTIVE = {"cancel": False, "log_file": None, "thread": None}


class Busy(Exception):
    pass


class PreRunError(Exception):
    pass


class Cancelled(Exception):
    pass


# ───────────────────────────── mail keys: the SAME list as "Payout Check" of CTCL vs NMASS ─────────────────────────────
def mail_keys(k: str) -> tuple:
    return ("PAYOUT_CHECK_TO", "PAYOUT_CHECK_CC")


def apply_defaults() -> None:
    for k, (to, cc) in MAIL_DEFAULTS.items():
        tk, ck = mail_keys(k)
        os.environ.setdefault(tk, to)
        os.environ.setdefault(ck, cc)


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("PAYOUT_CHECK_DIR", "").strip()
    if v:
        p = Path(v)
    else:
        try:
            from core.scheduler import paths
            base = paths.data_root()
        except Exception:                                        # noqa: BLE001
            base = Path(__file__).resolve().parent.parent / "data"
        p = base / FOLDER_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_dir() -> Path:
    p = root() / "_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_root() -> Path:
    return root() / BACKUP_SUB


def log_dir() -> Path:
    v = os.getenv("PAYOUT_CHECK_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / "payoutchk"
    except Exception:                                            # noqa: BLE001
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _today() -> dt.date:
    return dt.date.today()


def _iso(t=None) -> str:
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def _check_date(s: str) -> str:
    if not re.fullmatch(r"\d{2}-\d{2}-\d{4}", s or ""):
        raise ValueError("date must be dd-mm-yyyy")
    return s


def payout_dir() -> Path:
    """Where payout_download writes and payout_check reads / backs up (PAYOUT_FOLDER, same as the CTCL run)."""
    from core.pipelines import payout_check
    return Path(payout_check.PAYOUT_DIR)


# ───────────────────────────── settings / state ─────────────────────────────
def _settings_path() -> Path:
    return _state_dir() / "settings.json"


def get_settings() -> dict:
    d = {"send_mail": True, "alert_numbers": ""}
    try:
        d.update(json.loads(_settings_path().read_text(encoding="utf-8")))
    except Exception:                                            # noqa: BLE001
        pass
    return d


def save_settings(update: dict, by: str = "") -> dict:
    cur = get_settings()
    if "send_mail" in update:
        cur["send_mail"] = bool(update["send_mail"])
    if "alert_numbers" in update:
        raw = str(update["alert_numbers"] or "").strip()
        if raw:
            from core.scheduler import failure_alert
            if not failure_alert.parse_numbers(raw):
                raise ValueError("those WhatsApp numbers are not valid")
        cur["alert_numbers"] = raw
    cur["by"], cur["at"] = by, _iso()
    with _STATE_LOCK:
        _settings_path().write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return cur


def _state_path() -> Path:
    return _state_dir() / "state.json"


def read_state() -> dict:
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        st = {}
    if st.get("date") != _today().isoformat():
        return {"date": _today().isoformat(), "status": "idle", "steps": {}, "runs": 0}
    st.setdefault("steps", {})
    st.setdefault("status", "idle")
    st.setdefault("runs", 0)
    return st


def _write_state(st: dict):
    with _STATE_LOCK:
        tmp = _state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        tmp.replace(_state_path())


def _update(**kw) -> dict:
    with _STATE_LOCK:
        st = read_state()
        st.update(kw)
        _write_state(st)
        return st


def _set_step(name: str, status: str, msg: str = ""):
    with _STATE_LOCK:
        st = read_state()
        st["steps"][name] = {"status": status, "msg": msg[:300], "at": _iso()}
        _write_state(st)


# ───────────────────────────── logging / stop ─────────────────────────────
def log(msg: str):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    _LOG.append(line)
    f = _ACTIVE.get("log_file")
    if f:
        try:
            f.write(line + "\n")
            f.flush()
        except Exception:                                        # noqa: BLE001
            pass
    sys.__stdout__.write("[payoutchk] " + msg + "\n") if sys.__stdout__ else None


def recent_log(n: int = 120) -> list:
    return list(_LOG)[-n:]


def _open_run_log(trigger, by):
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"payoutchk__{now:%d-%m-%Y}__{now:%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== {NAME} - {now:%d-%m-%Y %H:%M:%S} =====\nstarted by: {by or trigger} ({trigger})\n\n")
        f.flush()
        return f
    except Exception:                                            # noqa: BLE001
        return None


def _check_cancel():
    if _ACTIVE.get("cancel"):
        raise Cancelled("Stopped.")


class _Tee:
    """Lines printed by the two payout modules (in the run's own thread) go to this card's log; other threads are untouched."""

    def __init__(self, orig, thread):
        self.orig, self.thread, self.buf = orig, thread, ""

    def write(self, s):
        if threading.current_thread() is not self.thread:
            return self.orig.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                log(line.rstrip())
        return len(s)

    def flush(self):
        if threading.current_thread() is not self.thread:
            self.orig.flush()


@contextlib.contextmanager
def _captured():
    tee = _Tee(sys.stdout, threading.current_thread())
    old = sys.stdout
    sys.stdout = tee
    try:
        yield
    finally:
        sys.stdout = old


# ───────────────────────────── the Update Limits file ─────────────────────────────
_UL_RX = re.compile(r"^update[ _]limits.*\.(xlsx|csv)$", re.I)


def update_path(base: Path = None):
    """The uploaded Update Limits file in the card's folder (the newest one) or None."""
    base = Path(base or root())
    found = [p for p in base.iterdir() if p.is_file() and _UL_RX.match(p.name)] if base.is_dir() else []
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _meta_path() -> Path:
    return _state_dir() / "upload.json"


def _read_meta() -> dict:
    try:
        return json.loads(_meta_path().read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return {}


def input_status(base: Path = None, today: dt.date = None) -> list:
    today = today or _today()
    p = update_path(base)
    row = {"key": "UPDATE_LIMITS", "label": "Update Limits (upload)", "name": p.name if p else "Update Limits*.xlsx", "found": bool(p),
           "ok": False, "why": "", "size": 0, "time": "", "where": "", "stale": False}
    if p:
        st = p.stat()
        saved = dt.datetime.fromtimestamp(max(st.st_mtime, st.st_ctime) if os.name == "nt" else st.st_mtime)
        row.update(size=st.st_size, time=saved.strftime("%H:%M:%S"), where=str(p.parent))
        if st.st_size == 0:
            row["why"] = "empty"
        else:
            row["ok"] = True
            up = _read_meta()
            if up.get("name") == p.name and up.get("date") and up["date"] != today.isoformat():
                row["stale"], row["why"] = True, f"uploaded {up['date']} - not today"
    else:
        row["why"] = "not uploaded"
    return [row]


def pre_run_check(base: Path = None) -> dict:
    rows = input_status(base)
    bad = [r for r in rows if not r["ok"]]
    if bad:
        raise PreRunError("Missing / not ready: " + "; ".join(f"{r['label']}" + (f" ({r['why']})" if r["why"] else "") for r in bad))
    return {"inputs": rows}


# ───────────────────────────── steps ─────────────────────────────
def step_download() -> dict:
    from core.pipelines.payout_download import run_payout_download
    with _captured():
        r = run_payout_download()
    if not isinstance(r, dict) or r.get("status") != "ok":
        raise RuntimeError("Payment Request Log: " + str((r or {}).get("message") or (r or {}).get("step") or r)[:240])
    return r


def step_check(update_file: Path, base_file: str) -> dict:
    from core.pipelines.payout_check import run_payout_check
    with _captured():
        r = run_payout_check(_update_file=str(update_file), _base_file=base_file, _cancel=lambda: bool(_ACTIVE.get("cancel")),
                             _max_mail_tries=MAIL_TRIES)
    if not isinstance(r, dict) or r.get("status") != "ok":
        raise RuntimeError("Payout check: " + (("missing " + ", ".join(r.get("missing", []))) if isinstance(r, dict) and r.get("missing") else str(r)[:240]))
    return r


def _latest_run_folder(day: dt.date):
    """payout_check backs the day's files up to <PAYOUT>\\BACKUPFILES AUTOMATED\\<DD MON YYYY>\\<run> - the newest run."""
    d = payout_dir() / "BACKUPFILES AUTOMATED" / day.strftime("%d %b %Y").upper()
    runs = [p for p in d.iterdir() if p.is_dir() and p.name.isdigit()] if d.is_dir() else []
    return max(runs, key=lambda p: int(p.name)) if runs else None


def _stash_dir() -> Path:
    p = _state_dir() / "ul_stash"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _stash_update_file(up: Path) -> Path:
    """A copy of the uploaded Update Limits (original time kept), so it reaches the backup whatever the check does with the original."""
    dest = _stash_dir() / Path(up).name
    shutil.copy2(up, dest)
    return dest


def backup_and_cleanup(day: dt.date = None, update_file: Path = None, stamp: str = "", with_run: bool = True) -> Path:
    """The files of THIS run (from payout_check's run folder) + the uploaded Update Limits go to BACKUP AUTOMATION\\<dd-mm-yyyy>\\<hhmmss>.
    The Update Limits file is what UPDATE LIMITS DATA stores from here at its daily time (18:00): nothing is stored during the run.
    A small `_update_limits.json` next to it keeps the snapshot time (from the file name / its saved time / the upload time)."""
    day = day or _today()
    dest = backup_root() / dstr(day) / (stamp or dt.datetime.now().strftime("%H%M%S"))
    dest.mkdir(parents=True, exist_ok=True)
    run = _latest_run_folder(day) if with_run else None
    if run:
        for p in run.iterdir():
            if p.is_file():
                shutil.copy2(p, dest / p.name)
                log(f"saved in backup: {p.name}")
    if update_file and Path(update_file).is_file():
        target = dest / Path(update_file).name
        if target.exists() and target.stat().st_size == Path(update_file).stat().st_size:
            Path(update_file).unlink()                           # payout_check already filed the same file there
        else:
            shutil.move(str(update_file), str(target))
        meta = _read_meta()
        t, how = meta.get("time") or dt.datetime.now().strftime("%H:%M:%S"), meta.get("time_src") or "upload time"
        (dest / "_update_limits.json").write_text(json.dumps({"file": target.name, "time": t, "time_src": how, "date": day.isoformat()}),
                                                  encoding="utf-8")
        log(f"Update Limits filed in backup ({target.name}, snapshot time {t}) - UPDATE LIMITS DATA stores it at its daily time.")
    return dest


# ───────────────────────────── failure alert ─────────────────────────────
def send_alert(headline, detail, sender=None) -> dict:
    """WhatsApp FAILURE ALERT picture (same as the other cards). Never raises."""
    rec = {"sent": False, "reason": ""}
    try:
        from core.scheduler import connections, failure_alert, helpers as H, paths
        raw = get_settings().get("alert_numbers") or ""          # blank -> the COMMON list (Credentials > WhatsApp)
        c = connections.get("whatsapp")
        numbers = failure_alert.parse_numbers(raw) or failure_alert.parse_numbers(c.get("alert_numbers", ""))
        if not numbers:
            rec["reason"] = "no alert number set"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        if sender is not None:
            sender(numbers, headline, detail)
            rec.update(sent=True, reason="sent")
            return rec
        if not c.get("auth_token"):
            rec["reason"] = "WhatsApp auth token is not set"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        now = dt.datetime.now()
        img = failure_alert.render_image(paths.sched_root() / "alerts" / f"payoutchk_{int(time.time())}.png", process=NAME,
                                         schedule="Manual run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-",
                                         trigger="Manual run", attempt_text="final", headline=headline, detail=detail,
                                         run_id="PAYOUTCHK", kind="FAILED")

        class _C:
            def log(self, m):
                log(m)
        ok, bad = [], []
        for num in numbers:
            try:
                H.whatsapp_send_image(img, num, f"FAILURE ALERT - {NAME}", now.strftime("%d-%m-%Y %H:%M"), _C(),
                                      template_name=(c.get("alert_template") or "").strip() or None)
                ok.append(num[-4:])
            except Exception as e:                               # noqa: BLE001
                bad.append(f"{num[-4:]}: {type(e).__name__}: {str(e)[:100]}")
        rec.update(sent=bool(ok), reason=("sent to ..." + ", ...".join(ok)) if ok else "all sends failed: " + "; ".join(bad))
        log("WhatsApp failure alert " + rec["reason"])
    except Exception as e:                                       # noqa: BLE001
        rec["reason"] = f"{type(e).__name__}: {e}"
        log("WhatsApp failure alert error: " + rec["reason"])
    return rec


# ───────────────────────────── the run ─────────────────────────────
def is_running() -> bool:
    return _RUN_LOCK.locked()


def needs_password() -> bool:
    return False                                                 # 3:40, 4:50 ... - several runs a day are the normal case


def run(trigger="manual", by="", alert=None, step_fns=None) -> dict:
    """`step_fns` (tests): {'download','check'} replacements."""
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy(f"{NAME} is already running.")
    alert = alert or send_alert
    fns = {"download": step_download, "check": step_check}
    fns.update(step_fns or {})
    base = root()
    _ACTIVE["cancel"] = False
    _ACTIVE["log_file"] = _open_run_log(trigger, by)
    prev = read_state()
    _update(status="running", started=_iso(), finished="", error="", trigger=trigger, by=by, steps={}, counts={},
            alert_sent=False, alert_reason="", runs=int(prev.get("runs", 0)) + 1)
    old_pipe = os.environ.get("RMS_PIPE")
    os.environ["RMS_PIPE"] = PIPE                                # payout_check's mail uses this card's sender
    result = {"ok": False}
    try:
        log(f"=== {NAME} started ({trigger}{', ' + by if by else ''}) ===")
        _set_step("input", "running")
        pre_run_check(base)
        up = update_path(base)
        row = input_status(base)[0]
        _set_step("input", "ok", f"{up.name} · {row['size']:,} bytes" + (f" · {row['why']}" if row.get("stale") else ""))
        _check_cancel()
        stash = _stash_update_file(up)                           # a copy for the backup - the check may move the original
        stamp = dt.datetime.now().strftime("%H%M%S")
        _check_cancel()
        _set_step("download", "running")
        dl = fns["download"]()
        recs = int(dl.get("records", 0) or 0)
        _set_step("download", "ok", f"{recs} payment request(s) today")
        _update(counts={"requests": recs})
        _check_cancel()
        if recs == 0 or not dl.get("file"):
            _set_step("check", "skipped", "no payment request today - nothing to check")
            dest = backup_and_cleanup(_today(), stash, stamp, with_run=False)
            _set_step("backup", "ok", f"Update Limits filed (no payment request): {dest}")
        elif not get_settings().get("send_mail", True):
            _set_step("check", "skipped", "e-mail is switched off in Settings (the check needs it) - not run")
            dest = backup_and_cleanup(_today(), stash, stamp, with_run=False)
            _set_step("backup", "ok", f"Update Limits filed (check not run): {dest}")
        else:
            _set_step("check", "running")
            fns["check"](up, dl["file"])
            _set_step("check", "ok", "mailed: " + ", ".join(_to_list()))
            _update(sent=["REPORT"])
            _check_cancel()
            _set_step("backup", "running")
            dest = backup_and_cleanup(_today(), stash, stamp)
            _set_step("backup", "ok", str(dest))
        _update(status="done", finished=_iso(), error="")
        log(f"=== {NAME} finished ===")
        result = {"ok": True}
    except Cancelled:
        _update(status="stopped", finished=_iso(), error="Stopped.")
        log("Stopped.")
        result = {"ok": False, "stopped": True}
    except Exception as e:                                       # noqa: BLE001
        if _ACTIVE.get("cancel") and "stopped" in str(e).lower():       # the mail loop of payout_check ends with RuntimeError("stopped")
            stopped = True
        else:
            stopped = False
        if stopped:
            _update(status="stopped", finished=_iso(), error="Stopped.")
            log("Stopped.")
            result = {"ok": False, "stopped": True}
        else:
            msg = str(e) if isinstance(e, PreRunError) else f"{type(e).__name__}: {e}"
            _update(status="failed", finished=_iso(), error=msg[:600])
            log("FAILED: " + msg)
            rec = alert(f"The {NAME} run failed.", msg[:600])
            _update(alert_sent=bool(rec.get("sent")), alert_reason=rec.get("reason", ""))
            result = {"ok": False, "error": msg}
    finally:
        if old_pipe is None:
            os.environ.pop("RMS_PIPE", None)
        else:
            os.environ["RMS_PIPE"] = old_pipe
        f = _ACTIVE.get("log_file")
        _ACTIVE["log_file"] = None
        if f:
            try:
                f.close()
            except Exception:                                    # noqa: BLE001
                pass
        _RUN_LOCK.release()
    return result


def _to_list() -> list:
    try:
        from core.mcx_cash_report import split_addrs
        return split_addrs(os.environ.get("PAYOUT_CHECK_TO", MAIL_DEFAULTS["REPORT"][0])) or ["(no To address)"]
    except Exception:                                            # noqa: BLE001
        return ["the To list"]


def request_run(by="", password=None, background=True) -> dict:
    if is_running():
        raise Busy(f"{NAME} is already running.")
    if background:
        th = threading.Thread(target=lambda: run("manual", by), name="payoutchk-manual", daemon=True)
        _ACTIVE["thread"] = th
        th.start()
        return {"ok": True, "started": True}
    return run("manual", by)


def any_fetching(exclude: str = "") -> bool:
    return False


def fetch_one(what: str, by="") -> dict:
    raise ValueError("this card has no separate downloads - upload the Update Limits file and press Run")


def stop_running(by="") -> bool:
    if not is_running():
        return False
    _ACTIVE["cancel"] = True
    log(f"Stop requested by {by or 'user'}.")
    return True


# ───────────────────────────── files / backup ─────────────────────────────
def _bk_day(name) -> Path:
    from core import backup_archive
    return backup_archive.day_dir(backup_root(), str(name))


def list_files(sub: str = "") -> list:
    """sub '' = the card's folder; 'dd-mm-yyyy' = that day (every run's folder inside it, shown as <hhmmss>/<file>)."""
    out = []
    if not sub:
        base = root()
        for p in sorted(base.iterdir()) if base.is_dir() else []:
            if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".part"):
                out.append({"name": p.name, "size": p.stat().st_size, "time": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S")})
        return out
    day = _bk_day(sub)
    if day.is_dir():
        for p in sorted(day.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                out.append({"name": p.relative_to(day).as_posix(), "size": p.stat().st_size,
                            "time": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S")})
    return out


def list_backup_dates() -> list:
    from core import backup_archive
    b = backup_root()
    if not b.is_dir():
        return []
    names = [p.name for p in b.iterdir() if p.is_dir() and re.fullmatch(r"\d{2}-\d{2}-\d{4}", p.name)]
    names += [n for n in backup_archive.archived_days(b) if re.fullmatch(r"\d{2}-\d{2}-\d{4}", n)]
    return sorted(set(names), key=lambda s: dt.datetime.strptime(s, "%d-%m-%Y"), reverse=True)


def file_path(name: str, sub: str = "") -> Path:
    if "\\" in name or name.startswith(".") or ".." in name.split("/"):
        raise ValueError("bad file name")
    p = (root() if not sub else _bk_day(_check_date(sub))) / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return p


dates_between = _cnc.dates_between


def range_listing(a: dt.date, b: dt.date) -> list:
    rows = []
    for d in dates_between(a, b):
        for f in list_files(dstr(d)):
            rows.append(dict(f, date=dstr(d)))
    return rows


def zip_range(a: dt.date, b: dt.date) -> Path:
    import tempfile
    z = Path(tempfile.gettempdir()) / f"PAYOUT_CHECK_{a:%d%m%Y}_{b:%d%m%Y}_{int(time.time())}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dates_between(a, b):
            for f in list_files(dstr(d)):
                zf.write(_bk_day(dstr(d)) / f["name"], f"{dstr(d)}/{f['name']}")
    return z


# ───────────────────────────── upload ─────────────────────────────
def _event(msg: str):
    """An upload event: in the live log AND logs/payoutchk/payoutchk__<dd-mm-yyyy>__uploads.log (the Logs button)."""
    log(msg)
    try:
        with open(log_dir() / f"payoutchk__{dstr(_today())}__uploads.log", "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%d-%m-%Y %H:%M:%S}] {msg}\n")
    except Exception:                                            # noqa: BLE001
        pass


def _refuse(name, why, by=""):
    _event(f"Upload REFUSED: {name} - {why} (by {by or 'user'})")
    raise ValueError(f"{name}: {why}")


def accept_upload(name: str, data: bytes, by="", modified_ms=None, now: dt.datetime = None) -> dict:
    """Only today's Update Limits file: today's date in the name, or - with no date - saved in the last FRESH_MIN minutes.
    Kept in the card's folder under its own name; its time (name > saved time > now) is remembered for UPDATE LIMITS DATA."""
    now = now or dt.datetime.now()
    name = os.path.basename(name or "")
    if not name or name.startswith(".") or name.lower().endswith((".py", ".bat", ".exe", ".part")):
        _refuse(name or "(no name)", "this file name is not allowed", by)
    if not data:
        _refuse(name, "the file is empty", by)
    if not _UL_RX.match(name):
        _refuse(name, "only the Update Limits file is used here (a name like 'Update Limits 18112026 1540.xlsx')", by)
    why = _cnc.freshness_problem(name, modified_ms, now)
    if why:
        _refuse(name, why.replace("PositionBook", "Update Limits"), by)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name)
    part = root() / (safe + ".part")
    part.write_bytes(data)
    for old in [p for p in root().iterdir() if p.is_file() and _UL_RX.match(p.name) and p.name != safe]:
        old.unlink()                                             # one Update Limits file at a time
    target = root() / safe
    part.replace(target)
    try:
        from core import update_limits as ul
        t, how = ul.time_in_name(safe), "file name"
        if not t and str(modified_ms or "").strip():
            try:
                t, how = ul._same_day_time(float(modified_ms) / 1000.0, now.date()), "file modified time"
            except (TypeError, ValueError, OverflowError, OSError):
                t = None
        if not t:
            t, how = now.strftime("%H:%M:%S"), "upload time"
    except Exception:                                            # noqa: BLE001
        t, how = now.strftime("%H:%M:%S"), "upload time"
    with _STATE_LOCK:
        _meta_path().write_text(json.dumps({"name": safe, "date": now.date().isoformat(), "time": t, "time_src": how, "by": by}), encoding="utf-8")
    d = _cnc.name_date(safe)
    _event(f"Upload ACCEPTED: {safe} ({len(data):,} bytes) by {by or 'user'} - snapshot time {t} ({how}) - "
           + (f"dated {dstr(d)} (today)" if d else f"no date in the name, saved within {_cnc.FRESH_MIN} min"))
    return {"ok": True, "name": safe, "size": len(data)}


def status() -> dict:
    st = read_state()
    running = is_running()
    s = st.get("status", "idle")
    if s == "running" and not running:
        s = "failed"
        st["error"] = st.get("error") or "Interrupted (the server restarted while it was running)."
    set_ = get_settings()
    inputs = input_status()
    steps = {k: dict(v) for k, v in (st.get("steps") or {}).items()}
    for v in steps.values():                                     # a step still marked running while nothing runs = the server restarted
        if v.get("status") == "running" and not running:
            v["status"], v["msg"] = "failed", "interrupted (the server restarted or the step was stopped)"
    if not running and inputs[0]["ok"] and (steps.get("input") or {}).get("status", "") in ("failed", ""):
        steps["input"] = {"status": "done", "msg": "file in place: " + inputs[0]["name"], "at": ""}
    return {"name": NAME, "date": st.get("date"), "status": "running" if running else s, "running": running,
            "fetching": False, "stopping": bool(_ACTIVE.get("cancel")) and running, "started": st.get("started", ""),
            "finished": st.get("finished", ""), "error": st.get("error", ""), "trigger": st.get("trigger", ""), "by": st.get("by", ""),
            "counts": st.get("counts", {}), "sent": st.get("sent", []), "alert_sent": bool(st.get("alert_sent")),
            "alert_reason": st.get("alert_reason", ""), "auto": False, "needs_password": False, "runs": st.get("runs", 0),
            "settings": {k: set_[k] for k in ("send_mail", "alert_numbers")},
            "steps": [dict(key=k, label=STEP_LABEL[k], **steps.get(k, {"status": "", "msg": ""})) for k in STEP_ORDER],
            "inputs": inputs, "folder": str(root()), "log": recent_log(60)}
