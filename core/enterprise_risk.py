"""core/enterprise_risk.py - ENTERPRISE RISK REPORT card (EMAIL.bat + FINAL.bat + any_screener.py as ONE card).

Daily timeline (Mon-Fri trading days; every time is editable in the card's Settings):
    09:00 -> 12:00  the Enterprise Risk mails (With / Without Margin) and the Concentration_abv20percentage.csv mail are picked from the
                    mailbox (looked for every 5 minutes; today's mails only). A mail that is not found inside the window is then
                    looked for among ALL of today's mails (00:00 - 23:59), so one that came before 09:00 or after 12:00 is still taken.
    10:30           the run: FULLFILE_CONCENTRATION is taken from MTF CONCENTRATION (its output or its backup, copied - the original stays)
                    -> the two report workbooks (top symbols by NET_EXPOSURE and DAYSTOLIQUIDATE_5PER_VOL, MTF CONCENTRATION and
                    CONCENTRATION_ABV20 sheets) -> screener.in data (SCREENER sheet + combined file) -> password protection
                    -> one e-mail -> everything moved to BACKUPFILES AUTOMATED\\<ddmmyyyy>
Switch (Settings): REDFLAG files (FINAL_REDFLAG.py / EMAIL_POP3_REDFLAG.py) or the SIMPLE files (FINAL.py / EMAIL_POP3.py).
On demand: the "Screener" button - type a company symbol, the file <SYMBOL>_SCREENER.xlsx is made and e-mailed to rms_del@mastertrust.co.in.
Nothing secret is in the code: screener.in login, the passwords of the files, the mailbox and the sender are in the card's Credentials.
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

NAME = "ENTERPRISE RISK REPORT"
FOLDER_NAME = "ENTERPRISE RISK"
PIPE = "entrisk"
BACKUP_SUB = "BACKUPFILES AUTOMATED"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ENTERPRISE_SENDER = "noreply@mastertrust.co.in"
CONC_SENDER = "raj.kumari@mastertrust.co.in"
CONC_SUBJECT = "Default Product_Concentration_Report"
CONC_FILE = "Concentration_abv20percentage.csv"
EARLY_START = dt.time(7, 30)                  # the concentration csv may arrive before the Enterprise mails
SPREADSHEET_EXT = (".xlsx", ".xlsm", ".xls", ".xlsb")
MAIL_DEFAULTS = {
    "REPORT": ("kartavya.dhillon@mastertrust.co.in", "sandeep.kumar2@mastertrust.co.in,sumit.aggarwal@mastertrust.co.in"),
    "SCREENER": ("rms_del@mastertrust.co.in", ""),
}
MAIL_LABEL = {"REPORT": "Enterprise Risk report (daily run)", "SCREENER": "Screener file (on demand)"}
STEP_LABEL = {"mail": "Mailbox files", "conc": "MTF CONCENTRATION file", "calc": "Report files", "screener": "screener.in",
              "protect": "Password protection", "email": "E-mail", "backup": "Backup"}
STEP_ORDER = ("mail", "conc", "calc", "screener", "protect", "email", "backup")

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_SCR_LOCK = threading.Lock()
_LOG = collections.deque(maxlen=400)
_ACTIVE = {"cancel": False, "log_file": None}
_STEP_LOCKS = collections.defaultdict(threading.Lock)
_TICK = {"thread": None, "stop": threading.Event()}


class Busy(Exception):
    pass


class PreRunError(Exception):
    pass


class Cancelled(Exception):
    is_cancel = True


# ───────────────────────────── mail defaults ─────────────────────────────
def mail_keys(k: str) -> tuple:
    return (f"ENTRISK_{k}_TO", f"ENTRISK_{k}_CC")


def apply_defaults() -> None:
    for k, (to, cc) in MAIL_DEFAULTS.items():
        tk, ck = mail_keys(k)
        os.environ.setdefault(tk, to)
        os.environ.setdefault(ck, cc)


def recipients(key: str):
    from core.mcx_cash_report import split_addrs
    tk, ck = mail_keys(key)
    d_to, d_cc = MAIL_DEFAULTS[key]
    return split_addrs(os.environ.get(tk, d_to)), split_addrs(os.environ.get(ck, d_cc))


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("ENTRISK_DIR", "").strip()
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


def screener_dir() -> Path:
    p = root() / "SCREENER OUTPUT"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    v = os.getenv("ENTRISK_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / "entrisk"
    except Exception:                                            # noqa: BLE001
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _today() -> dt.date:
    return dt.date.today()


def _iso(t=None) -> str:
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def d1(d=None) -> str:
    """ddmmyyyy - the date of the backup folder and of the output files."""
    return (d or _today()).strftime("%d%m%Y")


def d2(d=None) -> str:
    """yyyy-mm-dd - the date inside the Enterprise mail attachment names."""
    return (d or _today()).strftime("%Y-%m-%d")


dstr = d1                                                      # backup day folder name (shared card routes)


def _check_date(s: str) -> str:
    if not re.fullmatch(r"\d{8}", s or ""):
        raise ValueError("bad backup folder name")
    return s


# ───────────────────────────── settings / state ─────────────────────────────
def _hhmm(s, default: str) -> str:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(s or ""))
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return default


def _t(s: str) -> dt.time:
    return dt.time(int(s[:2]), int(s[3:]))


def _settings_path() -> Path:
    return _state_dir() / "settings.json"


DEFAULT_SETTINGS = {"auto": True, "variant": "redflag", "run_at": "10:30", "mail_from": "09:00", "mail_until": "12:00",
                    "send_mail": True, "protect": True, "top_n": 10, "alert_numbers": ""}
WINDOW_VERSION = 2          # 2 = the mail window is 09:00 - 12:00 (the mails arrive about 10:00) and a full-day look-up follows


def get_settings() -> dict:
    d = dict(DEFAULT_SETTINGS)
    saved = {}
    try:
        saved = json.loads(_settings_path().read_text(encoding="utf-8"))
        d.update(saved)
    except Exception:                                            # noqa: BLE001
        pass
    if saved and saved.get("win_v") != WINDOW_VERSION:           # one-time: an older saved window (10:03 / 10:15 - 11:00) missed the 10:00 mails
        d["mail_from"], d["mail_until"], d["win_v"] = DEFAULT_SETTINGS["mail_from"], DEFAULT_SETTINGS["mail_until"], WINDOW_VERSION
        try:
            tmp = _settings_path().with_suffix(".tmp")
            tmp.write_text(json.dumps({**saved, "mail_from": d["mail_from"], "mail_until": d["mail_until"], "win_v": WINDOW_VERSION}, indent=1), encoding="utf-8")
            tmp.replace(_settings_path())
        except Exception:                                        # noqa: BLE001
            pass
    for k in ("run_at", "mail_from", "mail_until"):
        d[k] = _hhmm(d.get(k), DEFAULT_SETTINGS[k])
    if d.get("variant") not in ("redflag", "simple"):
        d["variant"] = "redflag"
    try:
        d["top_n"] = max(1, min(200, int(d.get("top_n", 10))))
    except (TypeError, ValueError):
        d["top_n"] = 10
    return d


def save_settings(update: dict, by: str = "") -> dict:
    cur = get_settings()
    for k in ("auto", "send_mail", "protect"):
        if k in update:
            cur[k] = bool(update[k])
    if "variant" in update:
        if update["variant"] not in ("redflag", "simple"):
            raise ValueError("the report type must be REDFLAG or SIMPLE")
        if update["variant"] != cur["variant"] and is_running():
            raise ValueError("a run is going - change the report type after it ends")
        cur["variant"] = update["variant"]
    for k in ("run_at", "mail_from", "mail_until"):
        if k in update:
            v = _hhmm(update[k], "")
            if not v:
                raise ValueError(f"{update[k]!r} is not a time like 10:30")
            cur[k] = v
    if _t(cur["mail_from"]) >= _t(cur["mail_until"]):
        raise ValueError("the mail look-up start time must be earlier than its end time")
    if "top_n" in update:
        try:
            n = int(update["top_n"])
        except (TypeError, ValueError):
            raise ValueError("symbols per report must be a number")
        if not 1 <= n <= 200:
            raise ValueError("symbols per report must be 1 to 200")
        cur["top_n"] = n
    if "alert_numbers" in update:
        raw = str(update["alert_numbers"] or "").strip()
        if raw:
            from core.scheduler import failure_alert
            if not failure_alert.parse_numbers(raw):
                raise ValueError("those WhatsApp numbers are not valid")
        cur["alert_numbers"] = raw
    cur["by"], cur["at"], cur["win_v"] = by, _iso(), WINDOW_VERSION
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
    scr = st.get("screener", {})
    if st.get("date") != _today().isoformat():
        return {"date": _today().isoformat(), "status": "idle", "steps": {}, "sched": {}, "screener": scr}
    st.setdefault("steps", {})
    st.setdefault("status", "idle")
    st.setdefault("sched", {})
    st.setdefault("screener", {})
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
    print("[entrisk] " + msg, flush=True)


def recent_log(n: int = 120) -> list:
    return list(_LOG)[-n:]


def _open_run_log(trigger, by, what="run"):
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"entrisk__{now:%d-%m-%Y}__{now:%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== {NAME} {what} - {now:%d-%m-%Y %H:%M:%S} =====\nstarted by: {by or trigger} ({trigger})\n\n")
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
    """Run a blocking call in a helper thread so that Stop works and the log shows it is alive."""
    box = {}

    def work():
        try:
            box["r"] = fn()
        except BaseException as e:                               # noqa: BLE001
            box["e"] = e
    th = threading.Thread(target=work, name="entrisk-call", daemon=True)
    th.start()
    t0, last = time.time(), time.time()
    while th.is_alive():
        th.join(poll)
        if _cancel():
            raise Cancelled("Stopped.")
        if time.time() - last >= every:
            last = time.time()
            log(f"{what}: still working… ({int(time.time() - t0)} s)")
    if "e" in box:
        raise box["e"]
    return box.get("r")


# ───────────────────────────── credentials (never in code) ─────────────────────────────
def _env(k: str) -> str:
    return os.environ.get(k, "").strip()


def file_passwords() -> list:
    return [p for p in (os.environ.get("ENTRISK_FILE_PASSWORD", ""),) if p]


def abv_passwords() -> list:
    return [p for p in (os.environ.get("ENTRISK_ABV20_PASSWORD", ""), os.environ.get("ENTRISK_FILE_PASSWORD", "")) if p]


def pop_cfg() -> dict:
    from core.scheduler import connections
    c = connections.get("pop3")
    return {"server": _env("ENTRISK_POP_SERVER") or c.get("server") or "pop9.netcore.co.in",
            "port": int(_env("ENTRISK_POP_PORT") or c.get("port") or 995),
            "user": _env("ENTRISK_POP_USER") or c.get("username") or "",
            "password": os.environ.get("ENTRISK_POP_PASS") or c.get("password") or ""}


# ───────────────────────────── inputs ─────────────────────────────
def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _saved_day(p: Path) -> dt.date:
    t = p.stat().st_mtime
    if os.name == "nt":
        t = max(t, p.stat().st_ctime)
    return dt.datetime.fromtimestamp(t).date()


def conc_sources(today: dt.date = None) -> list:
    """Where MTF CONCENTRATION leaves FULLFILE_CONCENTRATION_<ddmmyyyy>.xlsx: its working folder, its backup day folder, its copy folder."""
    today = today or _today()
    nm = f"FULLFILE_CONCENTRATION_{d1(today)}.xlsx"
    out = []
    try:
        from core import mtf_concentration as mc
        out.append(mc.root() / nm)
        out.append(mc._bk_day(mc.dstr(today)) / nm)
        cd = (mc.get_settings().get("copy_dir") or "").strip()
        if cd:
            out.append(Path(cd) / nm)
    except Exception:                                            # noqa: BLE001
        pass
    return out


def find_conc(base: Path = None, today: dt.date = None):
    """(path, where): the file in this card's folder, else the first MTF CONCENTRATION location that has it."""
    base = Path(base or root())
    today = today or _today()
    nm = f"FULLFILE_CONCENTRATION_{d1(today)}.xlsx"
    own = base / nm
    if own.is_file() and own.stat().st_size > 0:
        return own, "this folder"
    for p in conc_sources(today):
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p, "MTF CONCENTRATION"
        except OSError:
            continue
    return None, ""


def input_status(base: Path = None, today: dt.date = None, var: str = None) -> list:
    from core import enterprise_risk_calc as calc
    base = Path(base or root())
    today = today or _today()
    var = var or get_settings()["variant"]
    rows = []

    def add(key, label, p, ok, why="", name="", where=""):
        found = bool(p and Path(p).is_file())
        row = {"key": key, "label": label, "name": name or (Path(p).name if p else ""), "found": found, "ok": bool(found and ok),
               "why": why if (found and not ok) else "", "size": 0, "time": "", "where": where}
        if found:
            st = Path(p).stat()
            row.update(size=st.st_size, time=dt.datetime.fromtimestamp(st.st_mtime).strftime("%H:%M:%S"), where=where or str(Path(p).parent))
        rows.append(row)

    for kind, label in (("without", "Enterprise Without Margin"), ("with", "Enterprise With Margin")):
        pre = calc.input_name(var, kind, d2(today))
        p = calc.find_file(base, pre)
        add(kind.upper(), label + (" (REDFLAG)" if var == "redflag" else ""), p, p is not None and p.stat().st_size > 0, "empty", pre + ".xlsx")
    cp = base / CONC_FILE
    add("ABV20", "Concentration above 20% (mail)", cp if cp.is_file() else None, cp.is_file() and cp.stat().st_size > 0 and _saved_day(cp) == today,
        "not saved today", CONC_FILE)
    fp, where = find_conc(base, today)
    add("FULLFILE", "FULLFILE_CONCENTRATION", fp, fp is not None, "", f"FULLFILE_CONCENTRATION_{d1(today)}.xlsx", where)
    return rows


def pre_run_check(base: Path = None) -> dict:
    rows = input_status(base)
    bad = [r for r in rows if not r["ok"]]
    if bad:
        raise PreRunError("Missing / not ready: " + "; ".join(f"{r['label']}" + (f" ({r['why']})" if r["why"] else "") for r in bad))
    return {"inputs": rows}


# ───────────────────────────── mailbox ─────────────────────────────
def classify(var: str, sender: str, subject: str):
    """'without' / 'with' for an Enterprise mail of this report type, 'concentration' for the csv mail, else None."""
    from core import enterprise_risk_calc as calc
    v = calc.variant(var)
    s, n = (sender or "").lower(), _norm(subject)
    if ENTERPRISE_SENDER in s:
        for key, pat in v["mail_key"].items():
            if pat in n and (not v["must_have"] or v["must_have"] in n) and (not v["must_not_have"] or v["must_not_have"] not in n):
                return key
    if CONC_SENDER in s and _norm(CONC_SUBJECT) in n:
        return "concentration"
    return None


def _decode(s) -> str:
    from email.header import decode_header
    out = ""
    for chunk, enc in decode_header(s or ""):
        out += chunk.decode(enc or "utf-8", errors="ignore") if isinstance(chunk, bytes) else chunk
    return out


def find_mails(pop, count: int, day: dt.date, var: str, starts: dict, end: dt.time, check=lambda: None) -> dict:
    """Newest mail of `day` per wanted key (key -> start time) between its start and `end` (IST), with the right attachment.
    -> {key: (filename, bytes, datetime)}. Binary search on the Date headers, header-only scan, full download for matches only."""
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

    earliest = dt.datetime.combine(day, min(starts.values()), tzinfo=IST)
    hi_t = dt.datetime.combine(day, end, tzinfo=IST)
    lo, hi = 1, count + 1
    while lo < hi:
        mid = (lo + hi) // 2
        d = dt_at(mid)
        if d is None:
            hi = mid
        elif d < earliest:
            lo = mid + 1
        else:
            hi = mid
    cands = {k: [] for k in starts}
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
        key = classify(var, str(msg["From"] or ""), _decode(str(msg["Subject"] or "")))
        if key in starts and d >= dt.datetime.combine(day, starts[key], tzinfo=IST):
            cands[key].append((d, i))
    found = {}
    for key, lst in cands.items():
        for d, i in sorted(lst, reverse=True):                   # newest first
            check()
            try:
                _r, full, _o = pop.retr(i)
            except (TimeoutError, OSError, poplib.error_proto):
                continue
            atts = []
            for part in parse(full).iter_attachments():
                fn = os.path.basename(_decode(part.get_filename() or ""))
                if key == "concentration":
                    if fn.lower() == CONC_FILE.lower():
                        atts.append((fn, part))
                elif fn.lower().endswith(SPREADSHEET_EXT):
                    atts.append((fn, part))
            if key != "concentration":
                atts.sort(key=lambda a: "redflag" not in _norm(a[0]) if var == "redflag" else "redflag" in _norm(a[0]))
            if atts and atts[0][1].get_payload(decode=True):
                found[key] = (atts[0][0], atts[0][1].get_payload(decode=True), d)
                break
    return found


def step_mail(base: Path, force: bool = False) -> dict:
    s = get_settings()
    var = s["variant"]
    have = {r["key"]: r["ok"] for r in input_status(base, var=var)}
    wanted = {"without": "WITHOUT", "with": "WITH", "concentration": "ABV20"}
    todo = [k for k, rk in wanted.items() if force or not have.get(rk)]
    if not todo:
        return {"ok": True, "skipped": True, "msg": "all three files are already in the folder"}
    now = dt.datetime.now(IST)
    start = _t(s["mail_from"])
    if now.time() < start:
        return {"ok": False, "msg": f"the mailbox is looked at from {s['mail_from']} (it is {now:%H:%M})"}
    import poplib
    import ssl
    cfg = pop_cfg()
    if not (cfg["user"] and cfg["password"]):
        return {"ok": False, "msg": "mailbox login is not set - use the Credentials button (mailbox that receives the Enterprise Risk mails)"}
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")                        # pop9.netcore.co.in uses a small DH key
    starts = {k: (EARLY_START if k == "concentration" else start) for k in todo}

    def work():
        pop = poplib.POP3_SSL(cfg["server"], cfg["port"], context=ctx, timeout=30)
        try:
            pop.user(cfg["user"])
            pop.pass_(cfg["password"])
            count, _size = pop.stat()
            log(f"Mailbox has {count} message(s); looking for today's {var.upper()} Enterprise mails from {s['mail_from']} to {s['mail_until']}.")
            got = find_mails(pop, count, now.date(), var, starts, _t(s["mail_until"]), check=_check_cancel)
            miss = [k for k in starts if k not in got]
            if miss:                                             # not in the window: look at ALL of today's mails (00:00 - 23:59)
                names = {"without": "Without Margin", "with": "With Margin", "concentration": "Concentration csv"}
                log(f"Not found between {s['mail_from']} and {s['mail_until']}: {', '.join(names[k] for k in miss)} - now checking ALL of today's mails (full day).")
                got.update(find_mails(pop, count, now.date(), var, {k: dt.time(0, 0) for k in miss}, dt.time(23, 59, 59), check=_check_cancel))
                for k in miss:
                    if k in got:
                        log(f"{names[k]}: found in the full-day look-up (mail of {got[k][2]:%H:%M}).")
            return got
        finally:
            try:
                pop.quit()
            except Exception:                                    # noqa: BLE001
                pass
    got = cancellable(work, "mailbox")
    saved = []
    for key, (fn, data, when) in got.items():
        part = base / (fn + ".part")
        part.write_bytes(data)
        part.replace(base / fn)
        saved.append(f"{fn} ({when:%H:%M})")
        log(f"saved {fn} (mail of {when:%H:%M})")
    miss = [k for k in todo if k not in got]
    if miss:
        names = {"without": "Without Margin", "with": "With Margin", "concentration": "Concentration csv"}
        return {"ok": False, "msg": ("saved " + ", ".join(saved) + "; " if saved else "") + "not there yet: " + ", ".join(names[m] for m in miss)}
    return {"ok": True, "msg": "saved " + ", ".join(saved)}


def step_conc(base: Path, force: bool = False) -> dict:
    p, where = find_conc(base)
    if p is None:
        return {"ok": False, "msg": "FULLFILE_CONCENTRATION of today is not there - MTF CONCENTRATION has not made it yet (it runs at 09:30). Upload it here if you have it."}
    if where == "this folder":
        return {"ok": True, "skipped": True, "msg": "in this folder"}
    shutil.copy2(p, base / p.name)
    return {"ok": True, "msg": f"copied from MTF CONCENTRATION ({p.parent.name}) - the original stays"}


STEPS = {"mail": step_mail, "conc": step_conc}


def _do_step(name: str, base: Path, force: bool = False, have_lock: bool = False) -> dict:
    lk = _STEP_LOCKS[name]
    if not have_lock:
        t0, noted = time.time(), 0.0
        while not lk.acquire(timeout=1):
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
            r = STEPS[name](base, force) if name in ("mail", "conc") else STEPS[name](base)
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


# ───────────────────────────── report, screener, protection ─────────────────────────────
def step_calc(base: Path, s: dict) -> dict:
    from core import enterprise_risk_calc as calc
    for old in base.glob("ENTERPRISE_*_MARGIN*_*.xlsx"):
        old.unlink()                                             # a stale file can never be e-mailed by mistake
    return cancellable(lambda: calc.build_reports(base, s["variant"], d1(), d2(), file_passwords(), abv_passwords(), s["top_n"], log=log),
                       "report calculation", every=20)


def new_session():
    from core import enterprise_risk_calc as calc
    return calc.screener_login(_env("ENTRISK_SCREENER_USER"), os.environ.get("ENTRISK_SCREENER_PASS", ""), log=log)


def step_screener(base: Path, s: dict, files: dict) -> dict:
    from core import enterprise_risk_calc as calc

    def work():
        sess = new_session()
        return calc.add_screener(base, s["variant"], files, sess, log=log, check=_check_cancel)
    return cancellable(work, "screener.in", every=20)


def step_protect(files: list, s: dict) -> str:
    from core import enterprise_risk_calc as calc
    if not s.get("protect", True):
        return "protection is switched off in Settings - files are NOT password protected"
    pw = os.environ.get("ENTRISK_FILE_PASSWORD", "")
    if not pw:
        raise PreRunError("the file password is not set (Credentials > Enterprise file password) - the reports must be protected before they are mailed")
    for f in files:
        calc.protect_xlsx(f, pw)
        log(f"protected: {Path(f).name}")
    return f"{len(files)} file(s) protected"


# ───────────────────────────── e-mail ─────────────────────────────
def _sig() -> str:
    return "Best regards,<br>\n<b>Kartavya Dhillon</b><br>\nMaster Capital Services Ltd<br>\n📞 011-66331000 | Ext. 84"


def report_body(var: str, names: list) -> str:
    from core import enterprise_risk_calc as calc
    v = calc.variant(var)
    items = "".join(f"<li>{n}</li>\n" for n in names)
    return f"""<html>
<body>
Dear Sir,<br><br>
{v['intro']}
<b>Activities performed:</b>
<ul>
<li>Generated {v['act']} for <b>Without Margin</b></li>
<li>Generated {v['act']} for <b>With Margin</b></li>
</ul><br>
<b>Sorting logic used in Enterprise files:</b>
<ol>
<li>Top 10 symbols selected based on <b>NET_EXPOSURE</b></li>
<li>Top 10 symbols selected based on <b>DAYSTOLIQUIDATE_5PER_VOL</b></li>
<li>Both lists were merged and duplicate symbols were removed before generating the final output</li>
<li>MTF CONCENTRATION sheet filtered where <b>FIN_UPTODATE_MTF(ABS) &gt; ₹5,00,000</b> and <b>Scrip_Concentration &gt; 10%</b></li>
<li>CONCENTRATION_ABV20 sheet filtered where <b>Net Ledger &gt; ₹5,00,000</b></li>
</ol><br>
Attached Files:
<ul>
{items}</ul><br>
Please find the attached reports for your review and reference.<br><br>
{_sig()}
</body>
</html>"""


def send_files(subject, body, paths, to, cc, attempts=5, gap=10, sleep=time.sleep, smtp_factory=None):
    """One mail with attachments; retries. Raises RuntimeError if it never goes."""
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


def step_email(files: list, var: str) -> dict:
    from core import enterprise_risk_calc as calc
    to, cc = recipients("REPORT")
    subject = calc.variant(var)["subject"].format(d=d1())
    send_files(subject, report_body(var, [Path(f).name for f in files]), files, to, cc)
    return {"sent": ["REPORT"], "failed": []}


# ───────────────────────────── backup ─────────────────────────────
def backup_and_cleanup(base: Path = None, extra: list = ()) -> Path:
    """Inputs + outputs move to BACKUPFILES AUTOMATED\\<ddmmyyyy> (a name that already exists there gets a time suffix)."""
    from core import enterprise_risk_calc as calc
    base = Path(base or root())
    dest = backup_root() / d1()
    dest.mkdir(parents=True, exist_ok=True)
    names = set(p for p in extra)
    for var in ("redflag", "simple"):
        for kind in ("without", "with"):
            for pre in (calc.input_name(var, kind, d2()),):
                p = calc.find_file(base, pre)
                if p:
                    names.add(p)
            names.update(base.glob(calc.output_name(var, kind, d1())))
        sp = base / calc.variant(var)["screener_file"]
        if sp.is_file():
            names.add(sp)
    for pat in (f"FULLFILE_CONCENTRATION_{d1()}*", "Concentration_abv20percentage*"):
        names.update(base.glob(pat))
    n = 0
    for p in sorted(names):
        p = Path(p)
        if not p.is_file():
            continue
        target = dest / p.name
        if target.exists():
            target = dest / f"{p.stem}_{dt.datetime.now():%H%M%S}{p.suffix}"
        shutil.move(str(p), str(target))
        n += 1
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
        img = failure_alert.render_image(paths.sched_root() / "alerts" / f"entrisk_{int(time.time())}.png", process=NAME,
                                         schedule="Auto run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-",
                                         trigger="Auto run", attempt_text="final", headline=headline, detail=detail,
                                         run_id="ENTRISK", kind="FAILED")

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
    s = get_settings()
    _ACTIVE["cancel"] = False
    _ACTIVE["log_file"] = _open_run_log(trigger, by)
    _update(status="running", started=_iso(), finished="", error="", trigger=trigger, by=by, steps={}, mailed=False, sent=[],
            failed_mail=[], counts={}, variant=s["variant"])
    result = {"ok": False}
    try:
        log(f"=== {NAME} started ({s['variant'].upper()} files; {trigger}{', ' + by if by else ''}) ===")
        outs = {}
        for n in ("mail", "conc"):
            _check_cancel()
            outs[n] = _do_step(n, base)
        bad = [f"{STEP_LABEL[k]}: {v.get('msg', '')}" for k, v in outs.items() if not v.get("ok")]
        _set_step("calc", "running")
        try:
            pre_run_check(base)
        except PreRunError as e:
            raise PreRunError(f"{e}" + (f"  [{'; '.join(bad)}]" if bad else ""))
        _check_cancel()
        res = step_calc(base, s)
        c = {k: v for k, v in res["counts"].items()}
        _set_step("calc", "ok", f"Without {c['without']['symbols']} symbols · With {c['with']['symbols']} symbols")
        _update(counts=c)
        _check_cancel()
        _set_step("screener", "running")
        sc = step_screener(base, s, res["files"])
        _set_step("screener", "ok", f"{sc['symbols']} symbol(s) from screener.in")
        _check_cancel()
        files = [res["files"]["without"], res["files"]["with"]]
        _set_step("protect", "running")
        msg = step_protect(files, s)
        _set_step("protect", "ok" if s.get("protect", True) else "skipped", msg)
        _check_cancel()
        if s.get("send_mail", True):
            _set_step("email", "running")
            m = step_email(files, s["variant"])
            _update(sent=m["sent"], failed_mail=m["failed"], mailed=True)
            _set_step("email", "ok", "sent to " + ", ".join(recipients("REPORT")[0]))
        else:
            _set_step("email", "skipped", "e-mail is switched off in Settings")
        _set_step("backup", "running")
        dest = backup_and_cleanup(base, [sc["master"]])
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
        threading.Thread(target=lambda: run("manual", by), name="entrisk-manual", daemon=True).start()
        return {"ok": True, "started": True}
    return run("manual", by)


FETCH_KINDS = {"mail": "mail", "conc": "conc"}


def any_fetching(exclude: str = "") -> bool:
    return any(k != exclude and l.locked() for k, l in list(_STEP_LOCKS.items())) or _SCR_LOCK.locked()


def fetch_one(what: str, by="") -> dict:
    """The single buttons (mailbox files, MTF CONCENTRATION file)."""
    if what not in FETCH_KINDS:
        raise ValueError("unknown download")
    if is_running():
        raise Busy("A full run is going - wait for it or Stop it.")
    lk = _STEP_LOCKS[what]
    if not lk.acquire(blocking=False):
        raise Busy(f"{STEP_LABEL[what]} is already running - wait for it to finish.")

    def go():
        if not any_fetching(exclude=what):
            _ACTIVE["cancel"] = False
        try:
            log(f"{STEP_LABEL[what]}: started by {by or 'user'}")
            _do_step(what, root(), force=(what == "mail"), have_lock=True)
        finally:
            lk.release()
    threading.Thread(target=go, name=f"entrisk-fetch-{what}", daemon=True).start()
    return {"ok": True, "started": what}


def stop_running(by="") -> bool:
    if not is_running() and not any_fetching():
        return False
    _ACTIVE["cancel"] = True
    log(f"Stop requested by {by or 'user'}.")
    return True


# ───────────────────────────── on demand: one company's screener file ─────────────────────────────
def _scr_set(**kw):
    with _STATE_LOCK:
        st = read_state()
        st["screener"] = dict(st.get("screener", {}), **kw)
        _write_state(st)


def screener_once(symbol: str, by="", send=True, sleep=time.sleep, smtp_factory=None, session=None) -> dict:
    """Make <SYMBOL>_SCREENER.xlsx and e-mail it (rms_del by default). Runs in the calling thread."""
    from core import enterprise_risk_calc as calc
    sym = (symbol or "").strip().upper()
    try:
        sym = calc.valid_symbol(symbol)
        _scr_set(symbol=sym, status="running", msg="logging in to screener.in…", at=_iso(), by=by, file="")
        out = screener_dir() / f"{sym}_SCREENER.xlsx"
        sess = session or new_session()
        _scr_set(msg="reading the company page…")
        calc.one_symbol_file(out, sym, sess, sleep=sleep, log=log, check=_check_cancel)
        sent = ""
        if send:
            to, cc = recipients("SCREENER")
            send_files(f"Screener - {sym} | {dt.datetime.now():%d-%m-%Y}", f"Dear Team,<br><br>Please find attached the screener data of <b>{sym}</b>.<br><br>{_sig()}",
                       [out], to, cc, sleep=sleep, smtp_factory=smtp_factory)
            sent = ", ".join(to)
        _scr_set(status="done", msg=("e-mailed to " + sent) if sent else "file made", file=str(out), at=_iso())
        return {"ok": True, "file": str(out), "sent_to": sent}
    except Cancelled:
        _scr_set(status="stopped", msg="Stopped.", at=_iso())
        return {"ok": False, "stopped": True}
    except Exception as e:                                       # noqa: BLE001
        msg = f"{type(e).__name__}: {e}" if not isinstance(e, calc.CalcError) else str(e)
        _scr_set(status="failed", msg=msg[:300], at=_iso())
        log(f"screener {sym} FAILED: {msg}")
        return {"ok": False, "error": msg}


def request_screener(symbol: str, by="") -> dict:
    from core import enterprise_risk_calc as calc
    try:
        sym = calc.valid_symbol(symbol)
    except calc.CalcError as e:
        raise ValueError(str(e))
    if not _SCR_LOCK.acquire(blocking=False):
        raise Busy("a screener request is already running - wait for it to finish")

    def go():
        try:
            if not is_running():
                _ACTIVE["cancel"] = False
            log(f"Screener {sym}: started by {by or 'user'}")
            screener_once(sym, by)
        finally:
            _SCR_LOCK.release()
    threading.Thread(target=go, name="entrisk-screener", daemon=True).start()
    return {"ok": True, "started": sym}


# ───────────────────────────── the timer ─────────────────────────────
def _trading_day(d: dt.date) -> bool:
    if d.weekday() >= 5:
        return False
    try:
        from core.scheduler import helpers as H
        return bool(H.is_trading_day(d))
    except Exception:                                            # noqa: BLE001
        return True


MAIL_RETRY_S = 300
RUN_RETRY_S = 600
MAX_TRIES = 3


def _mail_ready(base) -> bool:
    by = {r["key"]: r["ok"] for r in input_status(base)}
    return by.get("WITHOUT") and by.get("WITH") and by.get("ABV20")


def tick(now: dt.datetime = None, alert=None):
    """One look at the clock (every ~20 s), Mon-Fri trading days only."""
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
    end = _t(s["mail_until"])

    def spaced(key, every):
        last = sc.get(key, {}).get("last")
        return not last or (now - dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() >= every

    # 1) the mailbox files: from the start time until all three are in or the end time passes
    mj = sc.get("mail", {})
    if not (mj.get("done") or mj.get("gave_up")) and now.time() >= _t(s["mail_from"]):
        if now.time() > end:
            _sched_mark("mail", gave_up=True)                    # the run section below sends the one alert
        elif spaced("mail", MAIL_RETRY_S):
            _sched_mark("mail", last=_iso(now), tries=int(mj.get("tries", 0)) + 1)
            _ACTIVE["cancel"] = False
            r = _do_step("mail", base)
            if r.get("ok"):
                _sched_mark("mail", done=True)
            return
    # 2) the run at the set time (10:30)
    rj = sc.get("run", {})
    if rj.get("done") or rj.get("gave_up") or st.get("status") in ("running", "done"):
        return
    if now.time() < _t(s["run_at"]) or not spaced("run", RUN_RETRY_S // 2):
        return
    rows = input_status(base)
    tries = int(rj.get("tries", 0))
    if not all(r["ok"] for r in rows):
        if now.time() <= end:
            _sched_mark("run", last=_iso(now))
            log(f"run at {s['run_at']} is waiting for its input files…")
            _ACTIVE["cancel"] = False
            if not input_ok_key(base, "FULLFILE"):
                _do_step("conc", base)
            return
        _sched_mark("run", gave_up=True)                         # past the mail window and still not complete: ONE alert
        miss = "; ".join(r["label"] + (f" ({r['why']})" if r["why"] else "") for r in rows if not r["ok"])
        alert(f"{NAME}: input files are missing at {end:%H:%M} - no report today.", miss)
        log("gave up: " + miss)
        return
    _sched_mark("run", last=_iso(now), tries=tries + 1)
    last_try = tries + 1 >= MAX_TRIES
    r = run("auto", "auto (timer)", alert=alert if last_try else (lambda h, d: {"sent": False, "reason": "will retry"}))
    if r.get("ok") or r.get("stopped"):
        _sched_mark("run", done=True)
    elif last_try:
        _sched_mark("run", gave_up=True)


def input_ok_key(base, key: str) -> bool:
    return any(r["key"] == key and r["ok"] for r in input_status(base))


def start_ticker(interval_s: int = 20):
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return False
    _TICK["stop"].clear()

    def loop():
        log("timer started (mailbox files / run at the set time).")
        while not _TICK["stop"].wait(interval_s):
            try:
                tick()
            except Exception as e:                               # noqa: BLE001
                print(f"[entrisk] timer error: {type(e).__name__}: {e}", flush=True)
    th = threading.Thread(target=loop, name="entrisk-timer", daemon=True)
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
    names = [p.name for p in b.iterdir() if p.is_dir() and re.fullmatch(r"\d{8}", p.name)]
    names += [n for n in backup_archive.archived_days(b) if re.fullmatch(r"\d{8}", n)]
    return sorted(set(names), key=lambda x: dt.datetime.strptime(x, "%d%m%Y"), reverse=True)


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
    z = Path(tempfile.gettempdir()) / f"ENTERPRISE_RISK_{a:%d%m%Y}_{b:%d%m%Y}_{int(time.time())}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dates_between(a, b):
            for f in list_files(dstr(d)):
                zf.write(_bk_day(dstr(d)) / f["name"], f"{dstr(d)}/{f['name']}")
    return z


def accept_upload(name: str, data: bytes, by="") -> dict:
    """Upload files: kept under their own name in this folder (a file you got by hand if the mail or MTF CONCENTRATION is late)."""
    name = os.path.basename(name or "")
    if not name or name.startswith(".") or name.lower().endswith((".py", ".bat", ".exe", ".part")):
        raise ValueError("this file name is not allowed")
    if not data:
        raise ValueError("the file is empty")
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
        if (steps.get("mail") or {}).get("status", "") in ("failed", "") and all(by[k]["ok"] for k in ("WITHOUT", "WITH", "ABV20")):
            steps["mail"] = {"status": "done", "msg": "files in place", "at": (steps.get("mail") or {}).get("at", "")}
        if (steps.get("conc") or {}).get("status", "") in ("failed", "") and by["FULLFILE"]["ok"]:
            steps["conc"] = {"status": "done", "msg": "file in " + (by["FULLFILE"]["where"] or "place"), "at": (steps.get("conc") or {}).get("at", "")}
    scr = dict(st.get("screener") or {})
    if scr.get("status") == "running" and not _SCR_LOCK.locked():
        scr.update(status="failed", msg="interrupted (the server restarted)")
    sc = st.get("sched", {})
    return {"name": NAME, "date": st.get("date"), "status": "running" if running else s, "running": running,
            "fetching": any_fetching(), "stopping": bool(_ACTIVE.get("cancel")) and busy, "started": st.get("started", ""),
            "finished": st.get("finished", ""), "error": st.get("error", ""), "trigger": st.get("trigger", ""), "by": st.get("by", ""),
            "counts": st.get("counts", {}), "sent": st.get("sent", []), "failed_mail": st.get("failed_mail", []),
            "alert_sent": bool(st.get("alert_sent")), "alert_reason": st.get("alert_reason", ""),
            "variant": set_["variant"], "auto": bool(set_["auto"]), "needs_password": s == "done" and not running,
            "settings": {k: set_[k] for k in ("auto", "variant", "run_at", "mail_from", "mail_until", "send_mail", "protect", "top_n", "alert_numbers")},
            "sched": {k: {"done": bool(v.get("done")), "gave_up": bool(v.get("gave_up")), "tries": v.get("tries", 0)} for k, v in sc.items()},
            "steps": [dict(key=k, label=STEP_LABEL[k], **steps.get(k, {"status": "", "msg": ""})) for k in STEP_ORDER],
            "inputs": inputs, "screener": scr, "folder": str(root()), "log": recent_log(60)}
