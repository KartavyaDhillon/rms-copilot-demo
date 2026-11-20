"""core/mtf_order_file.py - MTF ORDER FILE card (EMAIL_FILE.bat + MTF_ORDER_FILE.bat as ONE job).

Daily timeline (trading days; the times are editable in the card's Settings):
    08:00 - 12:00  the mail "MTF_RISK_SHORTAGE_REPORT" (attachment MTF_RISK_SHORTAGE_REPORT.xlsx) is picked from the mailbox
                   (looked for every 5 minutes; never a mail of an earlier day, never outside that window)
    10:00          the run: MTFPOSITION + RISK_REPORT are read from COMMON FILES -> order files (core/mtf_order_calc.py)
                   -> one mail per zone + the full-file mail -> everything moved to BACKUP FILES AUTOMATED\\<dd-mm-yyyy>
Outputs: MTFPOSITION_<d>_SUMMARY(.csv + per zone), ORD_KAMB_MTFS_<d>(.csv + per zone), ORD_O319_MTFS_<d>(.csv + per zone, only if
         a 0319 account is in it), MTF_RISK_SHORTAGE_REPORT(.xlsx + per zone csv).
Nothing secret is in the code: the mailbox login is the card's Credentials (or Scheduler > Connections > pop3), the sender is the
card's Credentials (MTFOF_SENDER_*) or the shared one.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path

NAME = "MTF ORDER FILE"
FOLDER_NAME = "MTF ORDER FILE"
PIPE = "mtfof"
BACKUP_SUB = "BACKUP FILES AUTOMATED"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MAIL_SUBJECT = "MTF_RISK_SHORTAGE_REPORT"
MAIL_ATTACHMENT = "MTF_RISK_SHORTAGE_REPORT.xlsx"
SHORTAGE_STEM = "MTF_RISK_SHORTAGE_REPORT"
WINDOW_START = dt.time(8, 0)      # default; the real start is mail_from() (Settings)
WINDOW_END = dt.time(12, 0)
ZONES = ("CHND", "MUMB", "LUDH", "DELH")
_VR, _SK = "vipin.rai@mastertrust.co.in", "sandeep.kumar2@mastertrust.co.in"
MAIL_DEFAULTS = {                                       # the groups of MTF_ORDER_FILE_final.py (the Email To / CC window edits them)
    "FULL": ("rms_del@mastertrust.co.in,rms@mastertrust.co.in", ""),
    "CHND": ("rsharda@mastertrust.co.in,rms_chd@mastertrust.co.in", f"{_VR},{_SK}"),
    "MUMB": ("pradip.mandavkar@mastertrust.co.in", f"{_VR},{_SK}"),
    "LUDH": ("ludhianacompliance@mastertrust.co.in,kuljit.singh@mastertrust.co.in", f"{_VR},{_SK}"),
    "DELH": ("rms_del@mastertrust.co.in", f"{_VR},{_SK}"),
}
MAIL_LABEL = {"FULL": "Full file", "CHND": "Zone CHND", "MUMB": "Zone MUMB", "LUDH": "Zone LUDH", "DELH": "Zone DELH"}
STEP_LABEL = {"mail": "Shortage mail", "mtfpos": "MTFPOSITION", "risk": "Risk report", "calc": "Order files", "email": "E-mails", "backup": "Backup"}
STEP_ORDER = ("mail", "mtfpos", "risk", "calc", "email", "backup")

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_LOG = collections.deque(maxlen=400)
_ACTIVE = {"cancel": False, "log_file": None}
_STEP_LOCKS = collections.defaultdict(threading.Lock)
_TICK = {"thread": None, "stop": threading.Event()}


class Busy(Exception):
    pass


class PreRunError(Exception):
    pass


class Cancelled(Exception):
    pass


# ───────────────────────────── mail defaults (shared Email To / CC window) ─────────────────────────────
def mail_keys(k: str) -> tuple:
    return (f"MTFOF_{k}_TO", f"MTFOF_{k}_CC")


def mail_from() -> dt.time:
    """When the mailbox is first looked at: Settings > 'Mail look-up start time' (default 08:00); always earlier than the 12:00 end."""
    try:
        v = get_settings()["mail_from"]
        t = dt.time(int(v[:2]), int(v[3:]))
        if t < WINDOW_END:
            return t
    except Exception:                                            # noqa: BLE001
        pass
    return WINDOW_START


def apply_defaults() -> None:
    for k, (to, cc) in MAIL_DEFAULTS.items():
        tk, ck = mail_keys(k)
        os.environ.setdefault(tk, to)
        os.environ.setdefault(ck, cc)


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("MTF_ORDER_DIR", "").strip()
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


def common_dir() -> Path:
    return Path(os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES"))


def _state_dir() -> Path:
    p = root() / "_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_root() -> Path:
    return root() / BACKUP_SUB


def log_dir() -> Path:
    v = os.getenv("MTF_ORDER_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / "mtfof"
    except Exception:                                            # noqa: BLE001
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _today() -> dt.date:
    return dt.date.today()


def _iso(t=None) -> str:
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def dstr(d: dt.date) -> str:
    """Backup day folder, as the hand script made it: dd-mm-yyyy."""
    return d.strftime("%d-%m-%Y")


def _check_date(s: str) -> str:
    if not re.fullmatch(r"\d{2}-\d{2}-\d{4}", s or ""):
        raise ValueError("date must be dd-mm-yyyy")
    return s


def _dd(d=None) -> str:
    return (d or _today()).strftime("%d%m%Y")


# ───────────────────────────── settings / state ─────────────────────────────
def _hhmm(s, default: str) -> str:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(s or ""))
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return default


def _settings_path() -> Path:
    return _state_dir() / "settings.json"


def get_settings() -> dict:
    d = {"auto": True, "run_at": "10:00", "mail_from": "08:00", "send_mail": True, "alert_numbers": ""}
    try:
        d.update(json.loads(_settings_path().read_text(encoding="utf-8")))
    except Exception:                                            # noqa: BLE001
        pass
    d["run_at"] = _hhmm(d.get("run_at"), "10:00")
    d["mail_from"] = _hhmm(d.get("mail_from"), "08:00")
    return d


def save_settings(update: dict, by: str = "") -> dict:
    cur = get_settings()
    for k in ("auto", "send_mail"):
        if k in update:
            cur[k] = bool(update[k])
    if "run_at" in update:
        v = _hhmm(update["run_at"], "")
        if not v:
            raise ValueError(f"{update['run_at']!r} is not a time like 10:00")
        cur["run_at"] = v
    if "mail_from" in update:
        v = _hhmm(update["mail_from"], "")
        if not v:
            raise ValueError(f"{update['mail_from']!r} is not a time like 09:00")
        if dt.time(int(v[:2]), int(v[3:])) >= WINDOW_END:
            raise ValueError(f"the mail start time must be earlier than {WINDOW_END:%H:%M} (the end of the mail window)")
        cur["mail_from"] = v
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


def auto_enabled() -> bool:
    return bool(get_settings().get("auto"))


def _state_path() -> Path:
    return _state_dir() / "state.json"


def read_state() -> dict:
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        st = {}
    if st.get("date") != _today().isoformat():
        return {"date": _today().isoformat(), "status": "idle", "steps": {}, "sched": {}}
    st.setdefault("steps", {})
    st.setdefault("status", "idle")
    st.setdefault("sched", {})
    return st


def _write_state(st: dict):
    with _STATE_LOCK:
        from core.safe_state import write_json
        write_json(_state_path(), st)


def _update(**kw) -> dict:
    with _STATE_LOCK:
        st = read_state()
        st.update(kw)
        _write_state(st)
        return st


def _sched_mark(key: str, **kw):
    with _STATE_LOCK:
        st = read_state()
        st["sched"].setdefault(key, {}).update(kw)
        _write_state(st)


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
    print("[mtfof] " + msg, flush=True)


def recent_log(n: int = 120) -> list:
    return list(_LOG)[-n:]


def _open_run_log(trigger, by):
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"mtfof__{now:%d-%m-%Y}__{now:%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== {NAME} - {now:%d-%m-%Y %H:%M:%S} =====\nstarted by: {by or trigger} ({trigger})\n\n")
        f.flush()
        return f
    except Exception:                                            # noqa: BLE001
        return None


def _cancel() -> bool:
    return bool(_ACTIVE.get("cancel"))


def _check_cancel():
    if _cancel():
        raise Cancelled("Stopped.")


def cancellable(fn, what: str, every: float = 30.0, poll: float = 0.5):
    """Run a blocking call (a mailbox read) in a helper thread so that Stop works and the log shows it is alive."""
    box = {}

    def work():
        try:
            box["r"] = fn()
        except BaseException as e:                               # noqa: BLE001
            box["e"] = e
    th = threading.Thread(target=work, name="mtfof-call", daemon=True)
    th.start()
    t0, last = time.time(), time.time()
    while th.is_alive():
        th.join(poll)
        if _cancel():
            raise Cancelled("Stopped.")
        if time.time() - last >= every:
            last = time.time()
            log(f"{what}: still waiting for the answer… ({int(time.time() - t0)} s)")
    if "e" in box:
        raise box["e"]
    return box.get("r")


# ───────────────────────────── inputs ─────────────────────────────
def _saved_day(p: Path) -> dt.date:
    t = p.stat().st_mtime
    if os.name == "nt":
        t = max(t, p.stat().st_ctime)
    return dt.datetime.fromtimestamp(t).date()


def shortage_path(base: Path = None) -> Path:
    return Path(base or root()) / MAIL_ATTACHMENT


def input_status(base: Path = None, today: dt.date = None) -> list:
    from core import mtf_order_calc as calc
    base = Path(base or root())
    today = today or _today()
    d = _dd(today)
    rows = []

    def add(key, label, p, ok, why="", name=None):
        found = bool(p and Path(p).is_file())
        row = {"key": key, "label": label, "name": name or (Path(p).name if p else ""), "found": found, "ok": bool(found and ok),
               "why": "" if (found and ok) else why, "size": 0, "time": "", "where": ""}
        if found:
            st = Path(p).stat()
            row.update(size=st.st_size, time=dt.datetime.fromtimestamp(st.st_mtime).strftime("%H:%M:%S"), where=str(Path(p).parent))
        rows.append(row)

    for key, label, nm in (("MTFPOSITION", "MTFPOSITION (COMMON FILES)", f"MTFPOSITION_{d}.csv"),
                           ("RISK_REPORT", "RISK_REPORT (COMMON FILES)", f"RISK_REPORT_{d}.csv")):
        p = common_dir() / nm
        add(key, label, p if p.is_file() else None, p.is_file() and p.stat().st_size > 0, "empty" if p.is_file() else "", nm)
    sp = shortage_path(base)
    ok, why = False, ""
    if sp.is_file():
        if _saved_day(sp) != today:
            why = "not saved today"
        else:
            why = calc.shortage_problem(sp)
            ok = not why
    add("SHORTAGE", "Shortage report (mail)", sp if sp.is_file() else None, ok, why, MAIL_ATTACHMENT)
    return rows


def pre_run_check(base: Path = None) -> dict:
    rows = input_status(base)
    bad = [r for r in rows if not r["ok"]]
    if bad:
        raise PreRunError("Missing / not ready: " + "; ".join(f"{r['label']}" + (f" ({r['why']})" if r["why"] else "") for r in bad))
    return {"inputs": rows}


# ───────────────────────────── mailbox: the shortage report ─────────────────────────────
def pop_cfg() -> dict:
    from core.scheduler import connections
    c = connections.get("pop3")
    return {"server": os.getenv("MTFOF_POP_SERVER") or c.get("server") or "pop9.netcore.co.in",
            "port": int(os.getenv("MTFOF_POP_PORT") or c.get("port") or 995),
            "user": os.getenv("MTFOF_POP_USER") or c.get("username") or "",
            "password": os.getenv("MTFOF_POP_PASS") or c.get("password") or ""}


def find_mail(pop, count: int, day: dt.date, start=None, end=WINDOW_END, check=lambda: None):
    """Newest mail of `day` inside [start, end] (IST) whose subject has MAIL_SUBJECT and which carries MAIL_ATTACHMENT.
    Binary search on the Date headers (TOP, no download of bodies). -> (bytes, datetime) or None."""
    import email
    import poplib
    from email import policy
    from email.parser import BytesParser

    def parse(lines):
        return BytesParser(policy=policy.default).parsebytes(b"\r\n".join(lines))

    def head(i):
        _r, lines, _o = pop.top(i, 0)
        msg = parse(lines)
        dh = msg["Date"]
        if not dh:
            return None, msg
        try:
            d = email.utils.parsedate_to_datetime(dh)
        except (TypeError, ValueError):
            return None, msg
        if d.tzinfo is None:
            d = d.replace(tzinfo=IST)
        return d.astimezone(IST), msg

    def dt_at(i):
        order = []
        for step in range(0, 4):
            if i + step <= count:
                order.append(i + step)
            if step and i - step >= 1:
                order.append(i - step)
        for j in order:
            try:
                d, _ = head(j)
            except (TimeoutError, OSError, poplib.error_proto):
                continue
            if d is not None:
                return d
        return None

    start = start or mail_from()
    lo_t = dt.datetime.combine(day, start, tzinfo=IST)
    hi_t = dt.datetime.combine(day, end, tzinfo=IST)
    lo, hi = 1, count + 1                                        # first message at or after the window start
    while lo < hi:
        mid = (lo + hi) // 2
        d = dt_at(mid)
        if d is None:
            hi = mid
        elif d < lo_t:
            lo = mid + 1
        else:
            hi = mid
    found = []
    for i in range(lo, count + 1):
        check()
        try:
            d, msg = head(i)
        except (TimeoutError, OSError, poplib.error_proto):
            continue
        if d is None:
            continue
        if d > hi_t:
            break
        if d < lo_t or MAIL_SUBJECT.lower() not in str(msg["Subject"] or "").lower():
            continue
        found.append((d, i))
    for d, i in sorted(found, reverse=True):                     # newest first
        check()
        try:
            _r, full, _o = pop.retr(i)
        except (TimeoutError, OSError, poplib.error_proto):
            continue
        for part in parse(full).iter_attachments():
            if (part.get_filename() or "").lower() == MAIL_ATTACHMENT.lower():
                data = part.get_payload(decode=True)
                if data:
                    return data, d
    return None


def step_mail(base: Path, force: bool = False) -> dict:
    from core import mtf_order_calc as calc
    sp = shortage_path(base)
    if not force and sp.is_file() and _saved_day(sp) == _today() and not calc.shortage_problem(sp):
        return {"ok": True, "skipped": True, "msg": "already in the folder"}
    now = dt.datetime.now(IST)
    if now.time() < mail_from():
        return {"ok": False, "msg": f"the mail is looked for from {mail_from():%H:%M} (it is {now:%H:%M})"}
    import poplib
    import ssl
    cfg = pop_cfg()
    if not (cfg["user"] and cfg["password"]):
        return {"ok": False, "msg": "mailbox login is not set - use the Credentials button (mailbox that receives MTF_RISK_SHORTAGE_REPORT)"}
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")                        # pop9.netcore.co.in uses a small DH key

    def work():
        pop = poplib.POP3_SSL(cfg["server"], cfg["port"], context=ctx, timeout=30)
        try:
            pop.user(cfg["user"])
            pop.pass_(cfg["password"])
            count, _size = pop.stat()
            log(f"Mailbox has {count} message(s); looking for today's {MAIL_SUBJECT} between {mail_from():%H:%M} and {WINDOW_END:%H:%M}.")
            return find_mail(pop, count, now.date(), check=_check_cancel)
        finally:
            try:
                pop.quit()
            except Exception:                                    # noqa: BLE001
                pass
    hit = cancellable(work, "mailbox")
    if not hit:
        return {"ok": False, "msg": f"today's {MAIL_SUBJECT} mail ({mail_from():%H:%M}-{WINDOW_END:%H:%M}) is not there yet"}
    data, when = hit
    part = base / (MAIL_ATTACHMENT + ".part")
    part.write_bytes(data)
    part.replace(sp)
    bad = calc.shortage_problem(sp)
    if bad:
        return {"ok": False, "msg": f"the attachment of the {when:%H:%M} mail is not a usable shortage report: {bad}"}
    return {"ok": True, "msg": f"{MAIL_ATTACHMENT} (mail of {when:%H:%M})"}


def step_mtfpos(base: Path, force: bool = False) -> dict:
    from core import common_ftp
    r = common_ftp.fetch_mtfposition()
    return {"ok": bool(r["ok"]), "skipped": r.get("status") == "present", "msg": r.get("detail", "")}


def step_risk(base: Path, force: bool = False) -> dict:
    p = common_dir() / f"RISK_REPORT_{_dd()}.csv"
    if p.is_file() and p.stat().st_size > 0:
        return {"ok": True, "skipped": True, "msg": "in COMMON FILES"}
    return {"ok": False, "msg": "RISK_REPORT is not in COMMON FILES yet - upload it with Upload Common Files (or wait for the 07:30 FTP fetch)"}


STEPS = {"mail": step_mail, "mtfpos": step_mtfpos, "risk": step_risk}


def _do_step(name: str, base: Path, force: bool = False, have_lock: bool = False) -> dict:
    lk = _STEP_LOCKS[name]
    if not have_lock:
        t0, noted = time.time(), 0.0
        while not lk.acquire(timeout=1):                         # the same download is already running - wait, but Stop works
            if _cancel():
                _set_step(name, "stopped", "Stopped.")
                return {"ok": False, "msg": "Stopped."}
            if time.time() - noted >= 30:
                noted = time.time()
                log(f"{STEP_LABEL[name]}: already running (timer or button) - waiting for it… ({int(time.time() - t0)} s)")
    try:
        log(f"{STEP_LABEL[name]}: starting…")
        _set_step(name, "running")
        try:
            r = STEPS[name](base, force) if name == "mail" else STEPS[name](base)
        except Cancelled:
            _set_step(name, "stopped", "Stopped.")
            return {"ok": False, "msg": "Stopped."}
        except Exception as e:                                   # noqa: BLE001
            r = {"ok": False, "msg": f"{type(e).__name__}: {e}"}
        _set_step(name, "skipped" if r.get("skipped") else ("ok" if r.get("ok") else "failed"), r.get("msg", ""))
        log(f"{STEP_LABEL[name]}: {'OK' if r.get('ok') else 'FAILED'} - {r.get('msg', '')}")
        return r
    finally:
        if not have_lock:
            lk.release()


# ───────────────────────────── calculation, e-mail, backup ─────────────────────────────
def stage_inputs(base: Path) -> str:
    """Copy MTFPOSITION / RISK_REPORT from COMMON FILES (the originals stay there). Returns the date part (ddmmyyyy)."""
    d = _dd()
    for nm in (f"MTFPOSITION_{d}.csv", f"RISK_REPORT_{d}.csv"):
        shutil.copy2(common_dir() / nm, base / nm)
        log(f"staged {nm} from COMMON FILES")
    return d


def step_calc(base: Path) -> dict:
    from core import mtf_order_calc as calc
    d = stage_inputs(base)
    for old in list(base.glob(f"ORD_*_MTFS_{d}*.csv")) + list(base.glob(f"MTFPOSITION_{d}_SUMMARY*.csv")) + list(base.glob(f"{SHORTAGE_STEM}_*.csv")):
        old.unlink()                                             # a stale file of today can never be e-mailed by mistake
    res = cancellable(lambda: calc.build_order_files(base / f"MTFPOSITION_{d}.csv", shortage_path(base), base / f"RISK_REPORT_{d}.csv",
                                                     base, d, log=log), "calculation", every=20)
    res["date"] = d
    return res


def recipients(key: str):
    from core.mcx_cash_report import split_addrs
    tk, ck = mail_keys(key)
    d_to, d_cc = MAIL_DEFAULTS[key]
    return split_addrs(os.environ.get(tk, d_to)), split_addrs(os.environ.get(ck, d_cc))


def _sig() -> str:
    return "Best Regards,<br>\n<b>Kartavya Dhillon</b><br>\nMaster Capital Services Ltd<br>\n📞 011-66331000 | Ext. 84<br>\n"


def mail_body() -> str:
    return f"""Dear Team,<br><br>

As discussed, we are preparing the MTFS order file for 0319 and Kambala cases.<br><br>

<b><u>Stock Selection Criteria:</u></b><br>
In this file, we will first select the script that shows the <b>higher loss(negative P/L)</b>, calculated as:<br><br>

<b><u>For Example:</u></b><br><br>

For a NET SHORTAGE VALUE of ₹1249.02 the account has: 2 Shares<br><br>

TCS → Margin to be released value is  ₹13,616.65, per share rate ₹680.83 & its P/L = -452.21<br><br>
MOTHERSON → Margin to be released ₹66,469.52, per share rate ₹28.18 & its P/L = 37.25<br><br>

We first pick <b>TCS</b> because its <b>P/L</b> is negative , and take 1 Qty (≈ ₹680.83).<br><br>
The remaining ≈ ₹568.19 is covered by <b>MOTHERSON,</b> taking 21 Qty(≈ ₹591.78) which makes our total <b>release margin</b> 1272.61.<br><br>
A buffer of ₹100 is allowed to keep the match practical.<br><br>

{_sig()}"""


def send_files(subject, body, paths, to, cc, attempts=5, gap=10, sleep=time.sleep, smtp_factory=None):
    """One mail with several attachments; retries. Raises RuntimeError if it never goes."""
    import smtplib
    from email import encoders
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from core import sender_override
    user, pwd = sender_override.sender(PIPE)
    host, port = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in"), int(os.getenv("SMTP_PORT", "587") or 587)
    if not to:
        raise RuntimeError("no 'To' address set - use the Email To / CC button")
    if not (user and pwd):
        raise RuntimeError("the sender login is not set (Credentials > E-mail sender, or SENDER_EMAIL / SENDER_PASSWORD in .env)")
    last = None
    for n in range(1, attempts + 1):
        try:
            msg = MIMEMultipart()
            msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to), subject
            if cc:
                msg["Cc"] = ", ".join(cc)
            msg.attach(MIMEText(body, "html"))
            for p in paths:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(Path(p).read_bytes())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f'attachment; filename="{Path(p).name}"')
                msg.attach(part)
            srv = (smtp_factory or smtplib.SMTP)(host, port, timeout=60)
            try:
                srv.starttls()
                srv.login(user, pwd)
                srv.send_message(msg, to_addrs=list(to) + list(cc))
            finally:
                try:
                    srv.quit()
                except Exception:                                # noqa: BLE001
                    pass
            log(f"E-mail sent: {subject} ({len(paths)} file(s)) -> {', '.join(to)}" + (f" (cc {', '.join(cc)})" if cc else ""))
            return
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(f"the mail login was refused: {e}")
        except Exception as e:                                   # noqa: BLE001
            last = e
            log(f"E-mail failed (try {n}/{attempts}): {type(e).__name__}: {e}")
            if n < attempts:
                sleep(gap)
    raise RuntimeError(f"e-mail did not go: {type(last).__name__}: {last}")


def step_email(res: dict, sleep=time.sleep, smtp_factory=None) -> dict:
    """One mail per zone (summary + Kambala + 0319 + zone shortage file), then the full-file mail."""
    sent, failed, empty = [], [], []
    body = mail_body()
    for z in ZONES:
        zr = res["zones"][z]
        c = zr["counts"]
        if not (c["kamb"] or c["o319"]):
            empty.append(z)
            log(f"zone {z}: no order lines today - no mail")
            continue
        files = [p for p in (zr["summary"], zr["kamb"], zr["risk"], zr["o319"]) if p]
        to, cc = recipients(z)
        try:
            send_files(f"MTFS {z} Order File – 0319 & Kambala Cases", body, files, to, cc, sleep=sleep, smtp_factory=smtp_factory)
            sent.append(z)
        except Exception as e:                                   # noqa: BLE001
            failed.append(f"{z}: {e}")
            log(f"E-mail {z} FAILED: {e}")
    files = [p for p in (res["summary"], res["kamb"], res["risk_file"], res["o319"]) if p]
    to, cc = recipients("FULL")
    try:
        send_files("MTFS Order Full File – 0319 & Kambala Cases", body, files, to, cc, sleep=sleep, smtp_factory=smtp_factory)
        sent.append("FULL")
    except Exception as e:                                       # noqa: BLE001
        failed.append(f"FULL: {e}")
        log(f"E-mail FULL FAILED: {e}")
    return {"sent": sent, "failed": failed, "empty": empty}


def backup_and_cleanup(base: Path = None, date_part: str = None) -> Path:
    """Inputs + outputs move to BACKUP FILES AUTOMATED\\<dd-mm-yyyy> (what the hand script did: everything except scripts/folders)."""
    base = Path(base or root())
    date_part = date_part or _dd()
    dest = backup_root() / f"{date_part[:2]}-{date_part[2:4]}-{date_part[4:]}"
    dest.mkdir(parents=True, exist_ok=True)
    for p in sorted(base.iterdir()):
        if not p.is_file() or p.suffix.lower() in (".py", ".bat", ".part") or p.name.startswith("."):
            continue
        target = dest / p.name
        if target.exists():
            target.unlink()                                      # a re-run of the same day replaces its own earlier files
        shutil.move(str(p), str(target))
        log(f"moved to backup: {p.name}")
    return dest


# ───────────────────────────── failure alert ─────────────────────────────
def send_alert(headline, detail, sender=None) -> dict:
    """WhatsApp FAILURE ALERT picture (same as the other cards). Never raises."""
    rec = {"sent": False, "reason": ""}
    try:
        from core.scheduler import connections, failure_alert, helpers as H, paths
        raw = get_settings().get("alert_numbers") or ""          # this card's own list; blank -> the COMMON list below (Credentials > WhatsApp)
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
        img = failure_alert.render_image(paths.sched_root() / "alerts" / f"mtfof_{int(time.time())}.png", process=NAME,
                                         schedule="Auto run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-",
                                         trigger="Auto run", attempt_text="final", headline=headline, detail=detail,
                                         run_id="MTFOF", kind="FAILED")

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
    return read_state().get("status") == "done"


def run(trigger="manual", by="", alert=None) -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy(f"{NAME} is already running.")
    alert = alert or send_alert
    base = root()
    _ACTIVE["cancel"] = False
    _ACTIVE["log_file"] = _open_run_log(trigger, by)
    _update(status="running", started=_iso(), finished="", error="", trigger=trigger, by=by, steps={}, mailed=False, sent=[],
            failed_mail=[], empty_zones=[], counts={})
    result = {"ok": False}
    try:
        log(f"=== {NAME} started ({trigger}{', ' + by if by else ''}) ===")
        outs = {}
        for n in ("mail", "mtfpos", "risk"):
            _check_cancel()
            outs[n] = _do_step(n, base)
        bad = [f"{STEP_LABEL[k]}: {v.get('msg', '')}" for k, v in outs.items() if not v.get("ok")]
        _set_step("calc", "running")
        try:
            pre_run_check(base)
        except PreRunError as e:
            raise PreRunError(f"{e}" + (f"  [{'; '.join(bad)}]" if bad else ""))
        _check_cancel()
        res = step_calc(base)
        c = res["counts"]
        _set_step("calc", "ok", f"{c['stock_lines']} stock line(s) · {c['kamb']} Kambala + {c['o319']} 0319 order(s) · {c['accounts']} account(s)")
        _update(counts=c)
        _check_cancel()
        if get_settings().get("send_mail", True):
            _set_step("email", "running")
            m = step_email(res)
            _update(sent=m["sent"], failed_mail=m["failed"], empty_zones=m["empty"], mailed=bool(m["sent"]))
            _set_step("email", "failed" if m["failed"] else "ok",
                      ("sent: " + ", ".join(m["sent"])) + ("; no orders: " + ", ".join(m["empty"]) if m["empty"] else "") + ("; FAILED: " + "; ".join(m["failed"]) if m["failed"] else ""))
            if m["failed"]:
                raise RuntimeError("some e-mails did not go (files are kept, not backed up): " + "; ".join(m["failed"]))
        else:
            _set_step("email", "skipped", "e-mail is switched off in Settings")
        _set_step("backup", "running")
        dest = backup_and_cleanup(base, res["date"])
        _set_step("backup", "ok", str(dest))
        _update(status="done", finished=_iso(), error="")
        log(f"=== {NAME} finished ===")
        result = {"ok": True, "counts": c}
    except Cancelled:
        _update(status="stopped", finished=_iso(), error="Stopped.")
        log("Stopped.")
        result = {"ok": False, "stopped": True}
    except Exception as e:                                       # noqa: BLE001
        msg = f"{type(e).__name__}: {e}" if not isinstance(e, PreRunError) else str(e)
        _update(status="failed", finished=_iso(), error=msg[:600])
        log("FAILED: " + msg)
        rec = alert(f"The {NAME} run failed.", msg[:600])
        _update(alert_sent=bool(rec.get("sent")), alert_reason=rec.get("reason", ""))
        result = {"ok": False, "error": msg}
    finally:
        f = _ACTIVE.get("log_file")
        _ACTIVE["log_file"] = None
        if f:
            try:
                f.close()
            except Exception:                                    # noqa: BLE001
                pass
        _RUN_LOCK.release()
    return result


def request_run(by="", password=None, background=True) -> dict:
    if is_running():
        raise Busy(f"{NAME} is already running.")
    if needs_password():
        from core import short_allocation
        if not password or not short_allocation.check_password(password):
            return {"ok": False, "need_password": True, "msg": "Today's run already succeeded - enter the password to run it again."}
    if background:
        threading.Thread(target=lambda: run("manual", by), name="mtfof-manual", daemon=True).start()
        return {"ok": True, "started": True}
    return run("manual", by)


FETCH_KINDS = {"mail": "mail", "mtfpos": "mtfpos"}


def any_fetching(exclude: str = "") -> bool:
    return any(k != exclude and l.locked() for k, l in list(_STEP_LOCKS.items()))


def fetch_one(what: str, by="") -> dict:
    """The single-download buttons (shortage mail, MTFPOSITION)."""
    if what not in FETCH_KINDS:
        raise ValueError("unknown download")
    if is_running():
        raise Busy("A full run is going - wait for it or Stop it.")
    lk = _STEP_LOCKS[what]
    if not lk.acquire(blocking=False):
        raise Busy(f"{STEP_LABEL[what]} is already downloading - wait for it to finish.")

    def go():
        if not any_fetching(exclude=what):
            _ACTIVE["cancel"] = False
        try:
            log(f"{STEP_LABEL[what]}: started by {by or 'user'}")
            _do_step(what, root(), force=True, have_lock=True)
        finally:
            lk.release()
    threading.Thread(target=go, name=f"mtfof-fetch-{what}", daemon=True).start()
    return {"ok": True, "started": what}


def stop_running(by="") -> bool:
    if not is_running() and not any_fetching():
        return False
    _ACTIVE["cancel"] = True
    log(f"Stop requested by {by or 'user'}.")
    return True


# ───────────────────────────── the timer ─────────────────────────────
def _at(hhmm: str) -> dt.time:
    h, m = hhmm.split(":")
    return dt.time(int(h), int(m))


def _trading_day(d: dt.date) -> bool:
    try:
        from core.scheduler import helpers as H
        return bool(H.is_trading_day(d))
    except Exception:                                            # noqa: BLE001
        return d.weekday() < 5


MAIL_RETRY_S = 300       # the mailbox is looked at every 5 minutes from 08:00
RUN_RETRY_S = 600
MAX_TRIES = 3


def tick(now: dt.datetime = None, alert=None):
    """One look at the clock (every ~20 s). 08:00-12:00 pick the mail; at the run time do the run (inputs are fetched if missing)."""
    now = now or dt.datetime.now()
    s = get_settings()
    if not s.get("auto") or not _trading_day(now.date()):
        return
    if is_running() or any_fetching():
        return
    alert = alert or send_alert
    st = read_state()
    sc = st["sched"]
    base = root()

    def spaced(key, every):
        last = sc.get(key, {}).get("last")
        return not last or (now - dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() >= every

    # 1) the shortage mail: from 08:00 until it is found or 12:00 passes
    mj = sc.get("mail", {})
    if not (mj.get("done") or mj.get("gave_up")) and mail_from() <= now.time():
        if now.time() > WINDOW_END:
            _sched_mark("mail", gave_up=True)               # the run section below sends the one alert
            mj = sc["mail"] = dict(mj, gave_up=True)
        elif spaced("mail", MAIL_RETRY_S):
            _sched_mark("mail", last=_iso(now), tries=int(mj.get("tries", 0)) + 1)
            _ACTIVE["cancel"] = False
            r = _do_step("mail", base)
            if r.get("ok"):
                _sched_mark("mail", done=True)
            return
    # 2) the run at the set time (10:00)
    rj = sc.get("run", {})
    if rj.get("done") or rj.get("gave_up") or st.get("status") in ("running", "done"):
        return
    if now.time() < _at(s["run_at"]) or not spaced("run", RUN_RETRY_S // 2):
        return
    rows = input_status(base)
    tries = int(rj.get("tries", 0))
    if not all(r["ok"] for r in rows):
        if _inputs_could_still_arrive(now):
            _sched_mark("run", last=_iso(now))
            log(f"run at {s['run_at']} is waiting for its input files…")
            _ACTIVE["cancel"] = False
            for n in ("mtfpos", "mail"):
                if not input_ok_for(n, base):
                    _do_step(n, base)
            return
        _sched_mark("run", gave_up=True)                         # past the mail window and still not complete: ONE alert
        miss = "; ".join(r["label"] + (f" ({r['why']})" if r["why"] else "") for r in rows if not r["ok"])
        alert(f"{NAME}: input files are missing at {WINDOW_END:%H:%M} - no order file today.", miss)
        log("gave up: " + miss)
        return
    _sched_mark("run", last=_iso(now), tries=tries + 1)
    last_try = tries + 1 >= MAX_TRIES
    r = run("auto", "auto (timer)", alert=alert if last_try else (lambda h, d: {"sent": False, "reason": "will retry"}))
    if r.get("ok") or r.get("stopped"):
        _sched_mark("run", done=True)
    elif last_try:
        _sched_mark("run", gave_up=True)


def _inputs_could_still_arrive(now: dt.datetime) -> bool:
    return now.time() <= WINDOW_END


def input_ok_for(step: str, base: Path) -> bool:
    key = {"mtfpos": "MTFPOSITION", "mail": "SHORTAGE"}[step]
    return next(r for r in input_status(base) if r["key"] == key)["ok"]


def start_ticker(interval_s: int = 20):
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return False
    _TICK["stop"].clear()

    def loop():
        log("timer started (mail pick from 08:00 / run at the set time).")
        while not _TICK["stop"].wait(interval_s):
            try:
                tick()
            except Exception as e:                               # noqa: BLE001
                print(f"[mtfof] timer error: {type(e).__name__}: {e}", flush=True)
    th = threading.Thread(target=loop, name="mtfof-timer", daemon=True)
    _TICK["thread"] = th
    th.start()
    return True


def stop_ticker():
    _TICK["stop"].set()


# ───────────────────────────── files / backup listing / status ─────────────────────────────
def _bk_day(name) -> Path:
    from core import backup_archive
    return backup_archive.day_dir(backup_root(), str(name))


def list_files(sub: str = "") -> list:
    base = root() if not sub else _bk_day(sub)
    out = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".part"):
                out.append({"name": p.name, "size": p.stat().st_size,
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
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("bad file name")
    p = (root() if not sub else _bk_day(_check_date(sub))) / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return p


def dates_between(a: dt.date, b: dt.date) -> list:
    out, d = [], a
    while d <= b:
        out.append(d)
        d += dt.timedelta(days=1)
    return out


def range_listing(a: dt.date, b: dt.date) -> list:
    rows = []
    for d in dates_between(a, b):
        for f in list_files(dstr(d)):
            rows.append(dict(f, date=dstr(d)))
    return rows


def zip_range(a: dt.date, b: dt.date) -> Path:
    import tempfile
    z = Path(tempfile.gettempdir()) / f"MTF_ORDER_FILE_{a:%d%m%Y}_{b:%d%m%Y}_{int(time.time())}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dates_between(a, b):
            for f in list_files(dstr(d)):
                zf.write(_bk_day(dstr(d)) / f["name"], f"{dstr(d)}/{f['name']}")
    return z


def accept_upload(name: str, data: bytes, by="") -> dict:
    """Upload files: kept under their own name in this folder (the shortage report by hand, if the mail is late)."""
    name = os.path.basename(name or "")
    if not name or name.startswith(".") or name.lower().endswith((".py", ".bat", ".exe", ".part")):
        raise ValueError("this file name is not allowed")
    if not data:
        raise ValueError("the file is empty")
    if re.match(r"^(RISK_REPORT|MTFPOSITION)_", name, re.I):
        raise ValueError(f"{name}: this file is read from COMMON FILES - put it there (Upload Common Files), not here")
    if name.upper().startswith(SHORTAGE_STEM) and name.lower() != MAIL_ATTACHMENT.lower():
        raise ValueError(f"the shortage report must be named {MAIL_ATTACHMENT}")
    part = root() / (name + ".part")
    part.write_bytes(data)
    part.replace(root() / name)
    log(f"Uploaded {name} ({len(data):,} bytes) by {by or 'user'}")
    return {"ok": True, "name": name, "size": len(data)}


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
    busy = running or any_fetching()
    for v in steps.values():                                     # a step still marked running while nothing runs = the server restarted
        if v.get("status") == "running" and not busy:
            v["status"], v["msg"] = "failed", "interrupted (the server restarted or the step was stopped)"
    by = {r["key"]: r for r in inputs}
    if not running:
        for step, key in (("mail", "SHORTAGE"), ("mtfpos", "MTFPOSITION"), ("risk", "RISK_REPORT")):
            cur = (steps.get(step) or {}).get("status", "")
            if cur in ("failed", "") and by[key]["ok"]:
                steps[step] = {"status": "done", "msg": "file in place: " + by[key]["name"], "at": (steps.get(step) or {}).get("at", "")}
    sc = st.get("sched", {})
    return {"name": NAME, "date": st.get("date"), "status": "running" if running else s, "running": running,
            "fetching": any_fetching(), "stopping": bool(_ACTIVE.get("cancel")) and busy, "started": st.get("started", ""),
            "finished": st.get("finished", ""), "error": st.get("error", ""), "trigger": st.get("trigger", ""), "by": st.get("by", ""),
            "counts": st.get("counts", {}), "sent": st.get("sent", []), "failed_mail": st.get("failed_mail", []),
            "empty_zones": st.get("empty_zones", []), "alert_sent": bool(st.get("alert_sent")), "alert_reason": st.get("alert_reason", ""),
            "auto": bool(set_["auto"]), "needs_password": s == "done" and not running,
            "settings": {k: set_[k] for k in ("auto", "run_at", "mail_from", "send_mail", "alert_numbers")},
            "sched": {k: {"done": bool(v.get("done")), "gave_up": bool(v.get("gave_up")), "tries": v.get("tries", 0)} for k, v in sc.items()},
            "steps": [dict(key=k, label=STEP_LABEL[k], **steps.get(k, {"status": "", "msg": ""})) for k in STEP_ORDER],
            "inputs": inputs, "folder": str(root()), "log": recent_log(60)}
