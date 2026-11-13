"""core/ncdex_comm_limit.py - NCDEX COMM LIMIT: the daily NCXDEX_DEPOSIT_FILE.txt (RMS LIMITS, NCX segment), built and sent by itself.

Port of ncxdex_deposit_automation.py - THE FILE IS BUILT EXACTLY AS BEFORE (build_output_lines / format_collateral unchanged):
    every ClientNet client with its Total Collateral, then every ACCOUNTID of the 3-month active file not already there with 0,
    excluded codes left out, no code twice, header line "RMS LIMITS", fixed tail "0|||||0||||0||COMMODITY_EXP|no".

    inputs  (all in ONE folder, root() = <console data>/NCDEX COMM LIMIT)
        ClientNetEffectiveDeposits_<ddmmyyyy>[HHMMSS].csv   NCDEX NCFE - same download (and captcha auto-fill) as Margin
                                                             Shortage; taken from MCX COMM LIMIT's folder when its NMASS run
                                                             already brought today's copy.                     step "clientnet"
        NCDEX_3MONTH_ACTIVE_DETAILS_<ddmmyyyy>.csv           mailbox - previous day (Monday: Sun, Sat, Fri)    step "active"
    then   build -> e-mail -> Kambala FTP -> backup (BACKUPFILES AUTOMATED/<dd-mm-yyyy>) and clean-up.

RUN RULES (same as MCX COMM LIMIT)
    auto  : 10 s after PROBABLE's Run, once per day; a failure sends the WhatsApp alert, nobody retries by themselves.
    manual: Run button - asks for the password only when today's run already succeeded.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

NAME = "NCDEX COMM LIMIT"
FOLDER_NAME = "NCDEX COMM LIMIT"
PIPE = "ncdexcomm"
BACKUP_SUB = "BACKUPFILES AUTOMATED"
REPO = Path(__file__).resolve().parent.parent
OUTPUT_FILENAME = "NCXDEX_DEPOSIT_FILE.txt"
DEFAULT_TO = "rms_del@mastertrust.co.in"
DEFAULT_EXCLUDED = "01292"

CLIENT_CODE_COLUMN = "Client/CP Code"
COLLATERAL_COLUMN = "Total Collateral (Cash and Non Cash)"
ACTIVE_3MONTH_COLUMN = "ACCOUNTID"
EXCHANGE_SEGMENT = "NCX"
FIXED_TAIL = "0|||||0||||0||COMMODITY_EXP|no"

STEP_LABEL = {"clientnet": "ClientNet deposits (NCDEX)", "active": "3-month active (mail)", "build": "Build file",
              "email": "E-mail", "ftp": "Kambala FTP", "backup": "Backup"}

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_STEP_LOCKS = collections.defaultdict(threading.Lock)   # one lock PER download: different downloads run side by side
_LOG = collections.deque(maxlen=400)
_ACTIVE = {"procs": [], "cancel": False, "log_file": None}


class Busy(Exception):
    pass


class Cancelled(Exception):
    pass


# ───────────────────────────── paths / settings / state ─────────────────────────────
def root() -> Path:
    v = os.getenv("NCDEX_COMM_DIR", "").strip()
    if v:
        p = Path(v)
    else:
        try:
            from core.scheduler import paths
            base = paths.data_root()
        except Exception:
            base = REPO / "data"
        p = base / FOLDER_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def _state_dir() -> Path:
    p = root() / "_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_root() -> Path:
    return root() / BACKUP_SUB


def log_dir() -> Path:
    v = os.getenv("NCDEX_COMM_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / PIPE
    except Exception:
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _today() -> dt.date:
    return dt.date.today()


def _iso(t=None) -> str:
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def get_settings() -> dict:
    d = {"auto": True, "delay_s": 10, "ftp_enabled": True, "alert_numbers": "", "excluded": DEFAULT_EXCLUDED}
    try:
        d.update(json.loads((_state_dir() / "settings.json").read_text(encoding="utf-8")))
    except Exception:
        pass
    d["delay_s"] = max(0, int(d.get("delay_s") or 0))
    return d


def save_settings(update: dict, by: str = "") -> dict:
    cur = get_settings()
    for k in ("auto", "ftp_enabled"):
        if k in update:
            cur[k] = bool(update[k])
    if "delay_s" in update:
        cur["delay_s"] = max(0, min(600, int(update["delay_s"])))
    if "excluded" in update:
        cur["excluded"] = ",".join(c.strip() for c in re.split(r"[,;\s]+", str(update["excluded"] or "")) if c.strip())
    if "alert_numbers" in update:
        raw = str(update["alert_numbers"] or "").strip()
        if raw:
            from core.scheduler import failure_alert
            if not failure_alert.parse_numbers(raw):
                raise ValueError("those WhatsApp numbers are not valid")
        cur["alert_numbers"] = raw
    cur["by"], cur["at"] = by, _iso()
    with _STATE_LOCK:
        (_state_dir() / "settings.json").write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return cur


def excluded_codes() -> set:
    return {c.strip() for c in re.split(r"[,;\s]+", get_settings().get("excluded", "")) if c.strip()}


def auto_enabled() -> bool:
    return bool(get_settings().get("auto"))


def read_state() -> dict:
    try:
        st = json.loads((_state_dir() / "state.json").read_text(encoding="utf-8"))
    except Exception:
        st = {}
    if st.get("date") != dstr(_today()):
        return {"date": dstr(_today()), "status": "idle", "auto_tried": False, "steps": {}}
    st.setdefault("steps", {})
    st.setdefault("status", "idle")
    return st


def _write_state(st: dict):
    with _STATE_LOCK:
        p = _state_dir() / "state.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        tmp.replace(p)


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


# ───────────────────────────── logging ─────────────────────────────
def log(msg: str):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    _LOG.append(line)
    f = _ACTIVE.get("log_file")
    if f:
        try:
            f.write(line + "\n")
            f.flush()
        except Exception:
            pass
    print("[ncdexcomm] " + msg, flush=True)


def _file_only(msg: str):
    """Write a line to the run's log file only (not to the on-screen live log)."""
    f = _ACTIVE.get("log_file")
    if f:
        try:
            f.write(f"[{dt.datetime.now():%H:%M:%S}]   {msg}\n")
            f.flush()
        except Exception:
            pass


def _cancel() -> bool:
    return bool(_ACTIVE.get("cancel"))


def _check_cancel():
    if _cancel():
        raise Cancelled("Stopped.")


# ───────────────────────────── the input files ─────────────────────────────
def find_client_net_csv(base: Path, d: dt.date = None):
    """ClientNetEffectiveDeposits_<ddmmyyyy>.csv or ..._<ddmmyyyy><HHMMSS>.csv (newest wins). None if not there."""
    ds = (d or _today()).strftime("%d%m%Y")
    rx = re.compile(rf"^ClientNetEffectiveDeposits_{ds}(\d{{6}})?\.csv$", re.I)
    m = [p for p in Path(base).iterdir() if p.is_file() and rx.match(p.name)] if Path(base).is_dir() else []
    return max(m, key=lambda p: p.stat().st_mtime) if m else None


def candidate_dates_for(d: dt.date) -> list:
    """Previous day; on Monday: Sunday, then Saturday, then Friday (same order as the original)."""
    if d.weekday() == 0:
        return [d - dt.timedelta(days=1), d - dt.timedelta(days=2), d - dt.timedelta(days=3)]
    return [d - dt.timedelta(days=1)]


def active_name(d: dt.date) -> str:
    return f"NCDEX_3MONTH_ACTIVE_DETAILS_{d:%d%m%Y}.csv"


def find_active(base: Path):
    for d in candidate_dates_for(_today()):
        p = Path(base) / active_name(d)
        if p.is_file():
            return p
    return None


def input_status() -> list:
    base = root()
    cn = find_client_net_csv(base)
    ac = find_active(base)
    rows = []
    for label, p, want in (("ClientNet deposits (today)", cn, f"ClientNetEffectiveDeposits_{_today():%d%m%Y}*.csv"),
                           ("3-month active (previous day)", ac, " or ".join(active_name(d) for d in candidate_dates_for(_today())))):
        rows.append({"label": label, "found": bool(p), "ok": bool(p), "name": p.name if p else "", "want": want,
                     "size": p.stat().st_size if p else 0,
                     "time": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%H:%M:%S") if p else ""})
    return rows


# ───────────────────────────── child process (the shared NMASS/NCFE script, NCDEX part only) ─────────────────────────────
def _python() -> str:
    return os.getenv("CTCL_PYTHON") or os.getenv("SA_PYTHON") or sys.executable


def _kill(p):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=15)
        else:
            p.kill()
    except Exception:
        pass


_INTEREST = re.compile(r"\[[A-Z0-9_:]+\]|saved|login|logged|captcha|download|error|failed|NCDEX|restart", re.I)


def _run_child(cmd, env, timeout_s):
    flags = 0x08000000 if os.name == "nt" else 0
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(REPO), text=True,
                         encoding="utf-8", errors="replace", creationflags=flags)
    _ACTIVE["procs"].append(p)
    tail = []

    def pump():
        for line in p.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                del tail[:-60]
                if _INTEREST.search(line):
                    log(line[:300])
                else:
                    _file_only(line[:600])    # the saved log is the FULL output
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t0 = time.time()
    try:
        while p.poll() is None:
            if _cancel():
                _kill(p)
                break
            if time.time() - t0 > timeout_s:
                log(f"Stopped: no result after {int(timeout_s // 60)} minutes.")
                _kill(p)
                break
            time.sleep(0.5)
        p.wait(timeout=20)
    finally:
        try:
            _ACTIVE["procs"].remove(p)
        except ValueError:
            pass
    t.join(timeout=5)
    return (p.returncode if p.returncode is not None else -1), tail


def _borrow_client_net(base: Path):
    """Today's ClientNet file already downloaded by MCX COMM LIMIT's NMASS run -> copy it here."""
    try:
        from core import mcx_comm_limit
        src = find_client_net_csv(mcx_comm_limit.root())
    except Exception:
        src = None
    if src and src.stat().st_size > 0:
        shutil.copy2(src, base / src.name)
        return src.name
    return None


def step_clientnet(base: Path) -> dict:
    if find_client_net_csv(base):
        return {"ok": True, "skipped": True, "msg": "already in the folder"}
    got = _borrow_client_net(base)
    if got:
        return {"ok": True, "msg": f"{got} taken from MCX COMM LIMIT's NMASS download"}
    script = os.getenv("CTCL_NMASS_SCRIPT", str(REPO / "ctcl_codes" / "NMASS_NCFE_API_MIX.PY"))
    if not os.path.isfile(script):
        return {"ok": False, "msg": f"NMASS / NCDEX script not found: {script}"}
    from core import dl_lock
    waited = 0
    while True:                                                  # one NMASS / NCDEX login at a time (shared lock)
        _check_cancel()
        try:
            dl_lock.acquire("ncdexcomm")
            break
        except dl_lock.NmassLoginBusyError as e:
            got = _borrow_client_net(base)
            if got:
                return {"ok": True, "msg": f"{got} taken from MCX COMM LIMIT's NMASS download"}
            if waited > 900:
                return {"ok": False, "msg": f"The NMASS / NCDEX login stayed busy (used by '{e.owner}')."}
            if waited % 30 == 0:
                log(f"NCDEX / NMASS login is in use by '{e.owner}' - waiting...")
            time.sleep(5)
            waited += 5
    try:
        got = _borrow_client_net(base)                           # the one we waited for may have brought it
        if got:
            return {"ok": True, "msg": f"{got} taken from MCX COMM LIMIT's NMASS download"}
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                    "NMASS_SAVE_DIR": str(base), "NCFE_LOGOUT_AFTER": "1", "NMASS_RUN_MODE": "ncfe_only"})
        alive = threading.Event()

        def keep_fresh():
            while not alive.wait(45):
                try:
                    os.utime(dl_lock._LOCK_PATH, None)
                except OSError:
                    pass
        threading.Thread(target=keep_fresh, daemon=True).start()
        log("NCDEX download (ClientNetEffectiveDeposits) - the captcha is filled in by itself ...")
        try:
            rc, tail = _run_child([_python(), script], env, int(os.getenv("NCDEXCOMM_TIMEOUT_MIN", "15")) * 60)
        finally:
            alive.set()
    finally:
        dl_lock.release()
    p = find_client_net_csv(base)
    if p is None:                                                # the server may send it without the date in the name
        plain = base / "ClientNetEffectiveDeposits.csv"
        if plain.is_file() and dt.date.fromtimestamp(plain.stat().st_mtime) == _today():
            p = plain.rename(base / f"ClientNetEffectiveDeposits_{_today():%d%m%Y}.csv")
    if p and p.stat().st_size > 0:
        return {"ok": True, "msg": f"downloaded {p.name}"}
    auth = next((ln for ln in tail if re.search(r"NCFE_(AUTH_FAIL|LOCKED)|password|locked", ln, re.I)), "")
    return {"ok": False, "msg": ("NCDEX login problem - check Credentials. " if auth else "") + f"ClientNet file not downloaded (exit {rc})."}


# ── mailbox (same binary search as the original: 09:00-12:00 first, then the rest of that day) ──
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def pop_cfg() -> dict:
    from core.scheduler import connections
    c = connections.get("pop3")
    return {"server": os.getenv("MCXCOMM_POP_SERVER") or c.get("server") or "pop9.netcore.co.in",
            "port": int(os.getenv("MCXCOMM_POP_PORT") or c.get("port") or 995),
            "user": os.getenv("MCXCOMM_POP_USER") or c.get("username") or "",
            "password": os.getenv("MCXCOMM_POP_PASS") or c.get("password") or ""}


def _fetch_from_mail(pop, count, filename: str, day: dt.date):
    import email
    from email.header import decode_header
    from email.utils import parsedate_to_datetime

    def msg_date(i):
        try:
            _r, lines, _o = pop.top(i, 0)
            d = parsedate_to_datetime(email.message_from_bytes(b"\n".join(lines)).get("Date", ""))
            if d.tzinfo is None:
                d = d.replace(tzinfo=IST)
            return d.astimezone(IST)
        except Exception:
            return None

    def first_ge(t):
        lo, hi, res = 1, count, None
        while lo <= hi:
            mid = (lo + hi) // 2
            d = msg_date(mid)
            if d is None or d < t:
                lo = mid + 1
            else:
                res, hi = mid, mid - 1
        return res

    def last_le(t):
        lo, hi, res = 1, count, None
        while lo <= hi:
            mid = (lo + hi) // 2
            d = msg_date(mid)
            if d is None or d > t:
                hi = mid - 1
            else:
                res, lo = mid, mid + 1
        return res

    def rng(a, b):
        s, e = first_ge(a), last_le(b)
        return list(range(e, s - 1, -1)) if s is not None and e is not None and s <= e else []

    def dec(s):
        out = ""
        for chunk, enc in decode_header(s or ""):
            out += chunk.decode(enc or "utf-8", errors="ignore") if isinstance(chunk, bytes) else chunk
        return out

    def scan(idx):
        for i in idx:
            _check_cancel()
            try:
                _r, lines, _o = pop.retr(i)
                msg = email.message_from_bytes(b"\n".join(lines))
            except Exception:
                continue
            for part in msg.walk():
                if part.get_content_maintype() == "multipart" or not part.get_filename():
                    continue
                if dec(part.get_filename()).strip().lower() == filename.lower():
                    data = part.get_payload(decode=True)
                    if data:
                        return data
        return None

    win = rng(dt.datetime.combine(day, dt.time(9, 0), tzinfo=IST), dt.datetime.combine(day, dt.time(12, 0), tzinfo=IST))
    data = scan(win)
    if data is None:
        whole = rng(dt.datetime.combine(day, dt.time.min, tzinfo=IST), dt.datetime.combine(day, dt.time.max, tzinfo=IST))
        seen = set(win)
        data = scan([i for i in whole if i not in seen])
    return data


def step_active(base: Path) -> dict:
    p = find_active(base)
    if p:
        return {"ok": True, "skipped": True, "msg": f"{p.name} already in the folder"}
    import poplib
    import ssl
    cfg = pop_cfg()
    if not (cfg["user"] and cfg["password"]):
        return {"ok": False, "msg": "mailbox login is not set - use the Credentials button"}
    ctx = ssl.create_default_context()
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass
    last = ""
    for attempt in range(1, 4):
        try:
            pop = poplib.POP3_SSL(cfg["server"], cfg["port"], timeout=60, context=ctx)
            try:
                pop.user(cfg["user"])
                pop.pass_(cfg["password"])
                count = len(pop.list()[1])
                for d in candidate_dates_for(_today()):
                    name = active_name(d)
                    log(f"Looking for {name} in the mail of {d:%d-%m-%Y (%a)} ...")
                    data = _fetch_from_mail(pop, count, name, d)
                    if data:
                        part = base / (name + ".part")
                        part.write_bytes(data)
                        part.replace(base / name)
                        return {"ok": True, "msg": f"{name} from the mail of {d:%d-%m-%Y}"}
                return {"ok": False, "msg": "3-month active file not found in the mail of " +
                        ", ".join(f"{d:%d-%m (%a)}" for d in candidate_dates_for(_today()))}
            finally:
                try:
                    pop.quit()
                except Exception:
                    pass
        except Cancelled:
            raise
        except Exception as e:                                   # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            log(f"POP3 attempt {attempt}/3 failed: {last}")
            if attempt < 3:
                time.sleep(30)
    return {"ok": False, "msg": f"mailbox could not be read: {last}"}


STEPS = {"clientnet": step_clientnet, "active": step_active}     # tests replace these


# ───────────────────────────── the BUILD (unchanged from the original) ─────────────────────────────
def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find_column(columns, target_name, aliases=None):
    nt = _normalize(target_name)
    for col in columns:
        if _normalize(col) == nt:
            return col
    for alias in aliases or []:
        na = _normalize(alias)
        for col in columns:
            if _normalize(col) == na:
                return col
    for col in columns:
        if nt in _normalize(col):
            return col
    raise KeyError(f"Could not find a column matching '{target_name}'. Available columns in the CSV: {list(columns)}")


def format_collateral(value) -> str:
    import pandas as pd
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise ValueError("empty collateral value")
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if value == "":
            raise ValueError("empty collateral value")
    num = float(value)
    rounded = round(num, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}"


def build_output_lines(df, df_active, excluded=None):
    import pandas as pd
    excluded = excluded if excluded is not None else excluded_codes()
    client_col = find_column(df.columns, CLIENT_CODE_COLUMN, aliases=["Client CP Code", "CP Code", "Client Code"])
    collateral_col = find_column(df.columns, COLLATERAL_COLUMN, aliases=["Total Collateral (Cash and Non-Cash)",
                                                                           "Total Collateral Cash and Non Cash",
                                                                           "Total Collateral(Cash and Non Cash)"])
    account_col = find_column(df_active.columns, ACTIVE_3MONTH_COLUMN, aliases=["Account ID", "Account Id", "AccountId", "Account_Id"])
    lines, seen, skipped, excl, with_coll = [], set(), 0, 0, 0
    for idx, row in df.iterrows():
        raw_code, raw_coll = row[client_col], row[collateral_col]
        if pd.isna(raw_code) or str(raw_code).strip() == "":
            skipped += 1
            continue
        code = str(raw_code).strip()
        if code in excluded:
            excl += 1
            continue
        try:
            cs = format_collateral(raw_coll)
        except (ValueError, TypeError):
            log(f"ClientNet row {idx + 2}: skipping client {code} - unreadable collateral value {raw_coll!r}")
            skipped += 1
            continue
        if float(cs) != 0:
            with_coll += 1
        lines.append(f"{code}|{EXCHANGE_SEGMENT}|||{cs}|{FIXED_TAIL}")
        seen.add(code)
    log(f"ClientNet file: {len(lines)} lines ({skipped} rows skipped, {excl} excluded)")
    added, seen_active = 0, set()
    for _idx, row in df_active.iterrows():
        raw = row[account_col]
        if pd.isna(raw) or str(raw).strip() == "":
            continue
        code = str(raw).strip()
        if code in seen_active:
            continue
        seen_active.add(code)
        if code in excluded:
            excl += 1
            continue
        if code in seen:
            continue
        lines.append(f"{code}|{EXCHANGE_SEGMENT}|||0|{FIXED_TAIL}")
        seen.add(code)
        added += 1
    log(f"3-month active file: {added} more client(s) added with 0 collateral; {with_coll} of {len(lines)} have collateral")
    return lines, {"skipped": skipped, "excluded": excl, "with_collateral": with_coll, "added_from_active": added}


def build(base: Path) -> dict:
    import pandas as pd
    cn, ac = find_client_net_csv(base), find_active(base)
    if not cn:
        raise RuntimeError(f"today's ClientNetEffectiveDeposits_{_today():%d%m%Y}.csv is not in the folder")
    if not ac:
        raise RuntimeError("the previous day's NCDEX_3MONTH_ACTIVE_DETAILS file is not in the folder")
    df = pd.read_csv(cn, encoding="utf-8-sig")
    df_active = pd.read_csv(ac, encoding="utf-8-sig")
    lines, st = build_output_lines(df, df_active)
    if not lines:
        raise ValueError("No valid client rows found - not sending an empty file.")
    out = base / OUTPUT_FILENAME
    with open(out, "w", encoding="utf-8") as f:
        f.write("RMS LIMITS\n")
        for ln in lines:
            f.write(ln + "\n")
    log(f"Output file written: {out.name} ({len(lines)} client lines)")
    return {"out": out, "inputs": [cn, ac], "count": len(lines), **st}


# ───────────────────────────── e-mail / FTP / backup ─────────────────────────────
def recipients():
    from core.mcx_cash_report import split_addrs
    return (split_addrs(os.getenv("NCDEXCOMM_TO", DEFAULT_TO)) or [DEFAULT_TO]), split_addrs(os.getenv("NCDEXCOMM_CC", ""))


def mail_body(out_name: str, st: dict) -> str:
    today = dt.datetime.now().strftime("%d-%b-%Y")
    return ("<html><body style='font-family:Calibri,Arial,sans-serif;font-size:14px'>"
            "Dear Team,<br><br>Please find attached today's RMS Limits file for NCDEX (Commodity segment), generated from the "
            f"Client Net Effective Deposits report dated {today}.<br><br>"
            f"File name: {out_name}<br>Total clients included: {st['count']}<br>"
            f"Clients having collateral (non-zero Total Collateral): {st['with_collateral']}<br>"
            f"Additional clients added from 3-month active file: {st['added_from_active']}<br>"
            f"Clients excluded (EXCLUDED_CLIENT_CODES): {st['excluded']}<br>"
            f"Rows skipped (missing/invalid data): {st['skipped']}<br><br>"
            "Best Regards,<br>Kartavya Dhillon<br>Master Capital Services Ltd<br>011-66331000 | Ext. 84</body></html>")


def upload_to_kambala_ftp(file_path: Path) -> bool:
    if not get_settings().get("ftp_enabled", True):
        log("Kambala FTP upload is switched off.")
        return False
    from core import mcx_comm_limit as M
    cfg = M.kambala_ftp_cfg()
    if not cfg["password"]:
        log("Kambala FTP upload skipped - the FTP password is empty (Credentials > Kambala FTP).")
        return False
    from ftplib import FTP, error_perm
    name, size = file_path.name, file_path.stat().st_size

    def once():
        ftp = FTP()
        ftp.connect(cfg["host"], cfg["port"], timeout=60)
        try:
            ftp.login(cfg["user"], cfg["password"])
            ftp.set_pasv(True)
            ftp.cwd("/")
            for d in [p for p in cfg["folder"].replace("\\", "/").split("/") if p]:
                try:
                    ftp.cwd(d)
                except error_perm:
                    ftp.mkd(d)
                    ftp.cwd(d)
            with open(file_path, "rb") as f:
                ftp.storbinary(f"STOR {name}.part", f, blocksize=64 * 1024)
            try:
                ftp.delete(name)
            except Exception:
                pass
            ftp.rename(name + ".part", name)
            ftp.voidcmd("TYPE I")
            try:
                got = ftp.size(name)
            except Exception:
                got = None
            if got is not None and got != size:
                raise RuntimeError(f"size mismatch for {name}: local {size:,} bytes, server {got:,} bytes")
        finally:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

    for attempt in range(1, 6):
        _check_cancel()
        try:
            log(f"Kambala FTP upload of {name} (attempt {attempt}/5)...")
            once()
            log(f"Kambala FTP: uploaded {name} ({size:,} bytes)")
            return True
        except error_perm as exc:
            log(f"Kambala FTP refused the request ({exc}) - not retrying. Check user / password / folder.")
            return False
        except Exception as exc:                                  # noqa: BLE001
            log(f"Kambala FTP attempt {attempt} failed: {exc}")
            if attempt < 5:
                time.sleep(5)
    log(f"Kambala FTP upload of {name} FAILED after 5 attempts")
    return False


def backup_and_cleanup(files, run_log: Path = None) -> Path:
    dest = backup_root() / dstr(_today())
    dest.mkdir(parents=True, exist_ok=True)
    for p in files:
        shutil.copy2(p, dest / Path(p).name)
        log(f"backed up {Path(p).name}")
    if run_log and Path(run_log).is_file():
        shutil.copy2(run_log, dest / Path(run_log).name)
    for p in files:
        try:
            Path(p).unlink()
        except OSError as e:
            log(f"could not remove {Path(p).name}: {e}")
    log(f"Backup and clean-up done -> {dest}")
    return dest


# ───────────────────────────── failure alert ─────────────────────────────
def send_alert(headline, detail) -> dict:
    """Same WhatsApp FAILURE ALERT picture as MCX COMM LIMIT, with this card's name and numbers."""
    from core import mcx_comm_limit as M
    rec = {"sent": False, "reason": ""}
    try:
        from core.scheduler import connections, failure_alert, helpers as H, paths
        raw = get_settings().get("alert_numbers") or ""
        numbers = failure_alert.parse_numbers(raw) if raw.strip() else M.alert_numbers()
        if not numbers:
            numbers = failure_alert.parse_numbers(connections.get("whatsapp").get("alert_numbers", ""))
        if not numbers:
            rec["reason"] = "no alert number set"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        c = connections.get("whatsapp")
        if not c.get("auth_token"):
            rec["reason"] = "WhatsApp auth token is not set"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        now = dt.datetime.now()
        img = failure_alert.render_image(paths.sched_root() / "alerts" / f"ncdexcomm_{int(time.time())}.png",
                                         process=NAME, schedule="Auto run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"),
                                         started="-", trigger="Auto run", attempt_text="final", headline=headline,
                                         detail=detail, run_id="NCDEXCOMM", kind="FAILED")

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


def _do_step(name: str, base: Path, have_lock: bool = False) -> dict:
    """Runs one step. The same download never runs twice at once: a run that reaches a step whose button download is
    still going waits for it (and then finds the file there and skips)."""
    lk = _STEP_LOCKS[name]
    if not have_lock:
        lk.acquire()
    try:
        _set_step(name, "running")
        try:
            r = STEPS[name](base)
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


def run(trigger="manual", by="", alert=None) -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy(f"{NAME} is already running.")
    alert = alert or send_alert
    base = root()
    _ACTIVE["cancel"] = False
    run_log = None
    try:
        now = dt.datetime.now()
        run_log = log_dir() / f"{PIPE}__{dstr(now.date())}__{now:%H%M%S}.log"
        _ACTIVE["log_file"] = open(run_log, "w", encoding="utf-8")
        _ACTIVE["log_file"].write(f"===== {NAME} - {now:%d-%m-%Y %H:%M:%S} =====\nstarted by: {by or trigger} ({trigger})\n\n")
    except Exception:
        _ACTIVE["log_file"] = None
    st0 = read_state()
    _update(status="running", started=_iso(), finished="", error="", trigger=trigger, by=by, steps={},
            auto_tried=bool(st0.get("auto_tried")) or trigger in ("probable", "auto"), mailed=False, uploaded=False)
    result = {"ok": False}
    try:
        log(f"=== {NAME} started ({trigger}{', ' + by if by else ''}) ===")
        outs = {}
        ths = [threading.Thread(target=lambda n=n: outs.__setitem__(n, _do_step(n, base)), daemon=True) for n in ("clientnet", "active")]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        _check_cancel()
        bad = [f"{STEP_LABEL[k]}: {v.get('msg', '')}" for k, v in outs.items() if not v.get("ok")]
        if bad:
            raise RuntimeError("; ".join(bad))
        _set_step("build", "running")
        res = build(base)
        _set_step("build", "ok", f"{res['count']} clients ({res['with_collateral']} with collateral)")
        _update(rows=res["count"], with_collateral=res["with_collateral"], added=res["added_from_active"])
        _check_cancel()
        _set_step("email", "running")
        from core import mcx_cash_report as R
        to, cc = recipients()
        R.send_mail(to, cc, "NCDEX RMS Limits File", mail_body(res["out"].name, res), str(res["out"]), log=log)
        _set_step("email", "ok", "sent")
        _update(mailed=True)
        _set_step("ftp", "running")
        ok_ftp = upload_to_kambala_ftp(res["out"])
        _set_step("ftp", "ok" if ok_ftp else "failed", "uploaded" if ok_ftp else "not uploaded - see the log")
        _update(uploaded=ok_ftp)
        _set_step("backup", "running")
        f = _ACTIVE.get("log_file")
        if f:
            f.flush()
        dest = backup_and_cleanup([res["out"]] + res["inputs"], run_log)
        _set_step("backup", "ok", str(dest))
        _update(status="done", finished=_iso(), error="")
        log(f"=== {NAME} finished ===")
        result = {"ok": True, "rows": res["count"], "ftp": ok_ftp}
        if not ok_ftp and get_settings().get("ftp_enabled", True):
            alert("The NCDEX limit file was e-mailed but the Kambala FTP upload failed.", "Upload it to Kambala by hand (it is in the backup folder).")
    except Cancelled:
        _update(status="stopped", finished=_iso(), error="Stopped.")
        log("Stopped.")
        result = {"ok": False, "stopped": True}
    except Exception as e:                                       # noqa: BLE001
        msg = f"{type(e).__name__}: {e}" if not isinstance(e, RuntimeError) else str(e)
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
            except Exception:
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
        threading.Thread(target=lambda: run("manual", by), name="ncdexcomm-manual", daemon=True).start()
        return {"ok": True, "started": True}
    return run("manual", by)


def any_fetching(exclude: str = "") -> bool:
    return any(k != exclude and l.locked() for k, l in list(_STEP_LOCKS.items()))


def fetch_one(what: str, by="") -> dict:
    if what not in STEPS:
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
            _do_step(what, root(), have_lock=True)
        finally:
            lk.release()
    threading.Thread(target=go, name=f"ncdexcomm-fetch-{what}", daemon=True).start()
    return {"ok": True, "started": what}


def stop_running(by="") -> bool:
    if not is_running() and not any_fetching():
        return False
    _ACTIVE["cancel"] = True
    for p in list(_ACTIVE["procs"]):
        _kill(p)
    log(f"Stop requested by {by or 'user'}.")
    return True


def auto_due() -> bool:
    st = read_state()
    return bool(auto_enabled() and not st.get("auto_tried") and st.get("status") not in ("running", "done") and not is_running())


def hook_probable() -> bool:
    """Called when Run is clicked on PROBABLE: 10 s later this card starts by itself (once per day, auto on)."""
    if not auto_due():
        log("PROBABLE run seen - auto start skipped (" + ("auto is off" if not auto_enabled() else "already tried / running / done today") + ").")
        return False
    delay = get_settings()["delay_s"]
    _update(auto_tried=True, auto_scheduled=_iso())

    def go():
        time.sleep(delay)
        try:
            run("probable", "auto (after PROBABLE run)")
        except Busy:
            pass
        except Exception as e:                                   # noqa: BLE001
            log(f"auto start error: {type(e).__name__}: {e}")
    threading.Thread(target=go, name="ncdexcomm-auto", daemon=True).start()
    log(f"PROBABLE run seen - {NAME} starts in {delay} s.")
    return True


# ───────────────────────────── files / status ─────────────────────────────
def _check_date(s: str) -> str:
    if not re.match(r"^\d{2}-\d{2}-\d{4}$", s or ""):
        raise ValueError("date must be dd-mm-yyyy")
    return s


def list_files(sub: str = "") -> list:
    base = root() if not sub else backup_root() / sub
    out = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".part"):
                out.append({"name": p.name, "size": p.stat().st_size,
                            "time": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S")})
    return out


def list_backup_dates() -> list:
    b = backup_root()
    if not b.is_dir():
        return []
    ds = [p.name for p in b.iterdir() if p.is_dir() and re.match(r"^\d\d-\d\d-\d{4}$", p.name)]
    return sorted(ds, key=lambda s: dt.datetime.strptime(s, "%d-%m-%Y"), reverse=True)


def file_path(name: str, sub: str = "") -> Path:
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("bad file name")
    p = (root() if not sub else backup_root() / _check_date(sub)) / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return p


def range_listing(a: dt.date, b: dt.date) -> list:
    rows, d = [], a
    while d <= b:
        rows += [dict(f, date=dstr(d)) for f in list_files(dstr(d))]
        d += dt.timedelta(days=1)
    return rows


def zip_range(a: dt.date, b: dt.date) -> Path:
    import tempfile
    z = Path(tempfile.gettempdir()) / f"NCDEX_COMM_LIMIT_{a:%d%m%Y}_{b:%d%m%Y}_{int(time.time())}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in range_listing(a, b):
            zf.write(backup_root() / f["date"] / f["name"], f"{f['date']}/{f['name']}")
    return z


def accept_upload(name: str, data: bytes, by="") -> dict:
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
    se = get_settings()
    steps = {k: dict(v) for k, v in (st.get("steps") or {}).items()}
    inputs = input_status()
    # a step shown as failed whose file is now in the folder (uploaded by hand / downloaded later) -> shown as present
    if not running and len(inputs) >= 2:
        for step, row in (("clientnet", inputs[0]), ("active", inputs[1])):
            if (steps.get(step) or {}).get("status", "") in ("failed", "") and row["found"] and row["ok"]:
                steps[step] = {"status": "done", "msg": f"file is in the folder: {row['name']}"[:300],
                               "at": (steps.get(step) or {}).get("at", "")}
    return {"name": NAME, "date": st.get("date"), "status": "running" if running else s, "running": running,
            "fetching": any_fetching(), "started": st.get("started", ""), "finished": st.get("finished", ""),
            "error": st.get("error", ""), "trigger": st.get("trigger", ""), "by": st.get("by", ""),
            "rows": st.get("rows"), "with_collateral": st.get("with_collateral"), "added": st.get("added"),
            "mailed": bool(st.get("mailed")), "uploaded": bool(st.get("uploaded")),
            "alert_sent": bool(st.get("alert_sent")), "alert_reason": st.get("alert_reason", ""),
            "auto": bool(se["auto"]), "auto_tried": bool(st.get("auto_tried")), "needs_password": s == "done" and not running,
            "settings": {k: se[k] for k in ("auto", "delay_s", "ftp_enabled", "alert_numbers", "excluded")},
            "steps": [dict(key=k, label=STEP_LABEL[k], **steps.get(k, {"status": "", "msg": ""})) for k in STEP_LABEL],
            "inputs": inputs, "folder": str(root()), "log": list(_LOG)[-60:]}
