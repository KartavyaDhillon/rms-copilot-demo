"""core/cnc_f11_card.py - CNC F11 card (CNC_final.py as a console card).

What the job does (same maths as CNC_final.py - nothing added, nothing dropped):
    PositionBook.csv (uploaded in the card)  ->  Buying (MTF / NRML, NetQty > 0)  and  Selling (MTFS / CNC, NetQty < 0)
    -> the rows that have the same ClientID + Exch + Symbol on both sides (the "Union" / "VL" match)
    -> Sheet4 = matched buying rows (NetSellQty from Selling, NetQty = buy - sell, Product = CNC) + the CNC rows of the
       PositionBook that have both a buy and a sell quantity, sorted by NetQty
    -> result.xlsx (Sheet1, Buying, Selling, Sheet4, Sheet5) and CNC_F11_DATED_<dd-mm-yyyy>.xlsx (Sheet4)
    -> one mail (subject CNC_F11) with the dated file attached
    -> everything (csv / xlsx / xls) is moved to BACKUP AUTOMATION\\<dd-mm-yyyy>.

Differences from the hand script, on purpose:
    * the mail login / recipients are NOT in the code - sender = the card's Credentials (CNCF11_SENDER_*) or the shared
      sender; To / CC = the card's Email To / CC window (the old addresses are the defaults);
    * the mail is tried 5 times (10 s apart) and then the run FAILS - the files are then kept (not backed up / deleted),
      so nothing is lost; the hand script retried for ever;
    * a run is started by the Run button only (the hand script was a batch file) - no timer.
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

import pandas as pd

NAME = "CNC F11"
FOLDER_NAME = "CNC F11"
PIPE = "cncf11"
BACKUP_SUB = "BACKUP AUTOMATION"
INPUT_NAME = "PositionBook.csv"
MAIL_SUBJECT = "CNC_F11"
STEP_LABEL = {"input": "PositionBook", "calc": "Matching (Sheet4)", "email": "E-mail", "backup": "Backup"}
STEP_ORDER = ("input", "calc", "email", "backup")
MAIL_DEFAULTS = {"REPORT": ("yugal.sharma@mastertrust.co.in,swarna.bartwal@mastertrust.co.in,"
                            "ops_backoffice@mastertrust.co.in,neeraj.chandra@mastertrust.co.in",
                            "rms_del@mastertrust.co.in")}
MAIL_LABEL = {"REPORT": "CNC F11 file"}

FINAL_COLUMNS = ["ClientID", "Exch", "Symbol", "Instrument", "Product", "NetBuyQty", "NetSellQty", "NetQty"]
COLUMN_MAP = {
    "clientid": "ClientID", "accountid": "ClientID",
    "exch": "Exch", "exchg-seg": "Exch",
    "symbol": "Symbol",
    "instrument": "Instrument", "instrument name": "Instrument",
    "product": "Product", "product type": "Product",
    "netbuyqty": "NetBuyQty", "netbuy quantity": "NetBuyQty",
    "netsellqty": "NetSellQty", "netsell quantity": "NetSellQty",
    "netqty": "NetQty", "net quantity": "NetQty",
}

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_LOG = collections.deque(maxlen=400)
_ACTIVE = {"cancel": False, "log_file": None}


class Busy(Exception):
    pass


class PreRunError(Exception):
    pass


class Cancelled(Exception):
    pass


# ───────────────────────────── mail defaults (shared Email To / CC window) ─────────────────────────────
def mail_keys(k: str) -> tuple:
    return (f"CNCF11_{k}_TO", f"CNCF11_{k}_CC")


def apply_defaults() -> None:
    for k, (to, cc) in MAIL_DEFAULTS.items():
        tk, ck = mail_keys(k)
        os.environ.setdefault(tk, to)
        os.environ.setdefault(ck, cc)


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("CNC_F11_DIR", "").strip()
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
    v = os.getenv("CNC_F11_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / "cncf11"
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
        return {"date": _today().isoformat(), "status": "idle", "steps": {}}
    st.setdefault("steps", {})
    st.setdefault("status", "idle")
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
    print("[cncf11] " + msg, flush=True)


def recent_log(n: int = 120) -> list:
    return list(_LOG)[-n:]


def _open_run_log(trigger, by):
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"cncf11__{now:%d-%m-%Y}__{now:%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== {NAME} - {now:%d-%m-%Y %H:%M:%S} =====\nstarted by: {by or trigger} ({trigger})\n\n")
        f.flush()
        return f
    except Exception:                                            # noqa: BLE001
        return None


def _check_cancel():
    if _ACTIVE.get("cancel"):
        raise Cancelled("Stopped.")


# ───────────────────────────── the calculation (CNC_final.py, unchanged maths) ─────────────────────────────
def read_file(file_path):
    ext = os.path.splitext(str(file_path))[1].lower()
    return pd.read_csv(file_path) if ext == ".csv" else pd.read_excel(file_path)


def normalize_columns(df):
    new_cols = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in COLUMN_MAP:
            new_cols[col] = COLUMN_MAP[key]
    return df.rename(columns=new_cols)


def process(df):
    df = normalize_columns(df)
    for col in [c for c in FINAL_COLUMNS if c not in df.columns]:
        df[col] = None
    return df[FINAL_COLUMNS]


def add_union(df):
    df = df.copy()
    df.insert(df.columns.get_loc("Symbol") + 1, "Union",
              df["ClientID"].astype(str) + df["Exch"].astype(str) + df["Symbol"].astype(str))
    return df


def add_vl_column(df_to_update, df_lookup):
    df_to_update = df_to_update.copy()
    lookup_set = set(df_lookup["Union"])
    df_to_update["VL"] = df_to_update["Union"].apply(lambda x: x if x in lookup_set else "N/A")
    return df_to_update


def reorder_columns(df):
    df = df.copy()
    if "VL" in df.columns:
        cols = df.columns.tolist()
        cols.remove("VL")
        cols.insert(cols.index("Product") + 1, "VL")
        df = df[cols]
    return df


def update_sheet4_quantities(sheet4_df, sheet5_df, buying_df):
    sheet4_df = sheet4_df.copy()
    buying_lookup = buying_df.set_index("Union")["NetQty"].to_dict()
    sheet4_df["NetBuyQty"] = sheet4_df["Union"].map(buying_lookup).fillna(0)
    sheet4_df["NetSellQty"] = 0
    sheet4_df["NetQty"] = 0
    sheet5_lookup = sheet5_df.set_index("Union")["NetSellQty"].to_dict()
    sheet4_df["NetSellQty"] = sheet4_df["Union"].map(sheet5_lookup).fillna(0)
    sheet4_df["NetQty"] = sheet4_df["NetBuyQty"] - sheet4_df["NetSellQty"]
    sheet4_df["Product"] = "CNC"
    if "VL" in sheet4_df.columns:
        sheet4_df = sheet4_df.drop(columns=["VL"])
    return sheet4_df


def append_filtered_cnc(sheet1_df, sheet4_df):
    sheet1_df = sheet1_df.copy()
    for c in ("NetBuyQty", "NetSellQty", "NetQty"):
        sheet1_df[c] = pd.to_numeric(sheet1_df[c], errors="coerce").fillna(0)
    filtered_cnc = sheet1_df[(sheet1_df["Product"] == "CNC") & (sheet1_df["NetBuyQty"] != 0) & (sheet1_df["NetSellQty"] != 0)].copy()
    sheet4_df = pd.concat([sheet4_df, filtered_cnc], ignore_index=True)
    sheet4_df["NetQty"] = sheet4_df["NetQty"].replace(0, "")
    sheet4_df["NetQty_sort"] = pd.to_numeric(sheet4_df["NetQty"], errors="coerce")
    sheet4_df = sheet4_df.sort_values(by="NetQty_sort", na_position="last").drop(columns="NetQty_sort")
    if "Union" in sheet4_df.columns:
        sheet4_df = sheet4_df.drop(columns=["Union"])
    return sheet4_df


def calculate(position_file) -> dict:
    """PositionBook file -> {result, buying, selling, sheet4, sheet5} (DataFrames)."""
    result_df = process(read_file(position_file))
    buying_df = result_df[(result_df["Product"].isin(["MTF", "NRML"])) & (result_df["NetQty"] > 0)]
    buying_df = add_union(buying_df)
    selling_df = result_df[(result_df["Product"].isin(["MTFS", "CNC"])) & (result_df["NetQty"] < 0)]
    selling_df = add_union(selling_df)
    buying_df = add_vl_column(buying_df, selling_df)
    selling_df = add_vl_column(selling_df, buying_df)
    sheet4_df = reorder_columns(buying_df[buying_df["VL"] != "N/A"])
    sheet5_df = reorder_columns(selling_df[selling_df["VL"] != "N/A"])
    sheet4_df = update_sheet4_quantities(sheet4_df, sheet5_df, buying_df)
    sheet4_df = append_filtered_cnc(result_df, sheet4_df)
    return {"result": result_df, "buying": buying_df, "selling": selling_df, "sheet4": sheet4_df, "sheet5": sheet5_df}


# ───────────────────────────── inputs ─────────────────────────────
def position_path(base: Path = None) -> Path:
    """PositionBook.csv in the card's folder (the name is matched without caring about capital letters)."""
    base = Path(base or root())
    if base.is_dir():
        for p in base.iterdir():
            if p.is_file() and p.name.lower() == INPUT_NAME.lower():
                return p
    return base / INPUT_NAME


def input_status(base: Path = None, today: dt.date = None) -> list:
    today = today or _today()
    p = position_path(base)
    found = p.is_file()
    row = {"key": "POSITIONBOOK", "label": "PositionBook (upload)", "name": p.name, "found": found, "ok": False, "why": "",
           "size": 0, "time": "", "where": "", "stale": False}
    if found:
        st = p.stat()
        saved = dt.datetime.fromtimestamp(max(st.st_mtime, st.st_ctime) if os.name == "nt" else st.st_mtime)
        row.update(size=st.st_size, time=saved.strftime("%H:%M:%S"), where=str(p.parent))
        if st.st_size == 0:
            row["why"] = "empty"
        else:
            row["ok"] = True
            if saved.date() != today:
                row["stale"] = True
                row["why"] = f"saved {saved:%d-%m-%Y} - not today"
    else:
        row["why"] = "not uploaded"
    return [row]


def pre_run_check(base: Path = None) -> dict:
    rows = input_status(base)
    bad = [r for r in rows if not r["ok"]]
    if bad:
        raise PreRunError("Missing / not ready: " + "; ".join(f"{r['label']}" + (f" ({r['why']})" if r["why"] else "") for r in bad))
    return {"inputs": rows}


# ───────────────────────────── mail ─────────────────────────────
def recipients(key: str = "REPORT"):
    from core.mcx_cash_report import split_addrs
    tk, ck = mail_keys(key)
    d_to, d_cc = MAIL_DEFAULTS[key]
    return split_addrs(os.environ.get(tk, d_to)), split_addrs(os.environ.get(ck, d_cc))


def mail_body(d: dt.date = None) -> str:
    d = d or _today()
    return (f"Dear Sir,\n\nPlease find attached file of CNC_F11 dated {dstr(d)}.\n\n"
            "Best Regards,\nKartavya Dhillon\nMaster Capital Services Ltd\n📞 011-66331000 | Ext. 84\n")


def send_files(subject, body, paths, to, cc, attempts=5, gap=10, sleep=time.sleep, smtp_factory=None):
    """One plain-text mail with the attachments; retries. Raises RuntimeError if it never goes."""
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
            _check_cancel()
            msg = MIMEMultipart()
            msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to), subject
            if cc:
                msg["Cc"] = ", ".join(cc)
            msg.attach(MIMEText(body, "plain", "utf-8"))
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
        except Cancelled:
            raise
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(f"the mail login was refused: {e}")
        except Exception as e:                                   # noqa: BLE001
            last = e
            log(f"E-mail failed (try {n}/{attempts}): {type(e).__name__}: {e}")
            if n < attempts:
                sleep(gap)
    raise RuntimeError(f"e-mail did not go: {type(last).__name__}: {last}")


# ───────────────────────────── steps ─────────────────────────────
def step_calc(base: Path, today: dt.date = None) -> dict:
    """Make result.xlsx and CNC_F11_DATED_<dd-mm-yyyy>.xlsx from PositionBook.csv."""
    today = today or _today()
    base = Path(base)
    res = calculate(position_path(base))
    result_file = base / "result.xlsx"
    with pd.ExcelWriter(result_file, engine="openpyxl") as w:
        res["result"].to_excel(w, index=False, sheet_name="Sheet1")
        res["buying"].to_excel(w, index=False, sheet_name="Buying")
        res["selling"].to_excel(w, index=False, sheet_name="Selling")
        res["sheet4"].to_excel(w, index=False, sheet_name="Sheet4")
        res["sheet5"].to_excel(w, index=False, sheet_name="Sheet5")
    dated = base / f"CNC_F11_DATED_{dstr(today)}.xlsx"
    res["sheet4"].to_excel(dated, index=False)
    counts = {"rows": int(len(res["result"])), "buying": int(len(res["buying"])), "selling": int(len(res["selling"])),
              "sheet4": int(len(res["sheet4"])), "sheet5": int(len(res["sheet5"]))}
    log(f"PositionBook rows: {counts['rows']} · Sheet4 rows: {counts['sheet4']} · saved {result_file.name} and {dated.name}")
    return {"dated": dated, "result": result_file, "counts": counts, "date": today}


def step_email(res: dict, sleep=time.sleep, smtp_factory=None) -> dict:
    to, cc = recipients("REPORT")
    send_files(MAIL_SUBJECT, mail_body(res["date"]), [res["dated"]], to, cc, sleep=sleep, smtp_factory=smtp_factory)
    return {"sent": ["REPORT"]}


def backup_and_cleanup(base: Path = None, day: dt.date = None) -> Path:
    """csv / xlsx / xls of the day are moved to BACKUP AUTOMATION\\<dd-mm-yyyy> (the hand script copied them and deleted them)."""
    base = Path(base or root())
    dest = backup_root() / dstr(day or _today())
    dest.mkdir(parents=True, exist_ok=True)
    for p in sorted(base.iterdir()):
        if not p.is_file() or p.name.startswith(".") or p.suffix.lower() not in (".csv", ".xlsx", ".xls"):
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
        img = failure_alert.render_image(paths.sched_root() / "alerts" / f"cncf11_{int(time.time())}.png", process=NAME,
                                         schedule="Manual run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-",
                                         trigger="Manual run", attempt_text="final", headline=headline, detail=detail,
                                         run_id="CNCF11", kind="FAILED")

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


def run(trigger="manual", by="", alert=None, sleep=time.sleep, smtp_factory=None) -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy(f"{NAME} is already running.")
    alert = alert or send_alert
    base = root()
    _ACTIVE["cancel"] = False
    _ACTIVE["log_file"] = _open_run_log(trigger, by)
    _update(status="running", started=_iso(), finished="", error="", trigger=trigger, by=by, steps={}, sent=[], counts={},
            alert_sent=False, alert_reason="")
    result = {"ok": False}
    try:
        log(f"=== {NAME} started ({trigger}{', ' + by if by else ''}) ===")
        _set_step("input", "running")
        pre_run_check(base)
        row = input_status(base)[0]
        if row.get("stale"):
            log(f"WARNING: PositionBook.csv is {row['why']}.")
        _set_step("input", "ok", f"{row['name']} · {row['size']:,} bytes" + (f" · {row['why']}" if row.get("stale") else ""))
        _check_cancel()
        _set_step("calc", "running")
        res = step_calc(base)
        c = res["counts"]
        _set_step("calc", "ok", f"{c['rows']} PositionBook row(s) · {c['sheet4']} row(s) in Sheet4")
        _update(counts=c)
        _check_cancel()
        if get_settings().get("send_mail", True):
            _set_step("email", "running")
            step_email(res, sleep=sleep, smtp_factory=smtp_factory)
            _update(sent=["REPORT"])
            _set_step("email", "ok", "sent: " + res["dated"].name)
        else:
            _set_step("email", "skipped", "e-mail is switched off in Settings")
        _check_cancel()
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
        msg = str(e) if isinstance(e, PreRunError) else f"{type(e).__name__}: {e}"
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
        threading.Thread(target=lambda: run("manual", by), name="cncf11-manual", daemon=True).start()
        return {"ok": True, "started": True}
    return run("manual", by)


def any_fetching(exclude: str = "") -> bool:
    return False                                                 # this card downloads nothing


def fetch_one(what: str, by="") -> dict:
    raise ValueError("this card has no downloads - upload PositionBook.csv")


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
    z = Path(tempfile.gettempdir()) / f"CNC_F11_{a:%d%m%Y}_{b:%d%m%Y}_{int(time.time())}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dates_between(a, b):
            for f in list_files(dstr(d)):
                zf.write(_bk_day(dstr(d)) / f["name"], f"{dstr(d)}/{f['name']}")
    return z


FRESH_MIN = int(os.getenv("CNC_F11_FRESH_MIN", "30") or 30)      # a PositionBook with no date in its name must have been saved this recently
_D8 = re.compile(r"(?<!\d)(\d{2})[-_.]?(\d{2})[-_.]?(\d{4})(?!\d)")        # ddmmyyyy, dd-mm-yyyy ...
_Y8 = re.compile(r"(?<!\d)(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")        # yyyymmdd, yyyy-mm-dd ...


def name_date(name: str):
    """The date written in a file name (17112026, 17-11-2026, 20261117 ...) or None when there is none."""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    for mt in _D8.finditer(stem):
        try:
            return dt.date(int(mt.group(3)), int(mt.group(2)), int(mt.group(1)))
        except ValueError:
            continue
    for mt in _Y8.finditer(stem):
        try:
            if 2000 <= int(mt.group(1)) <= 2100:
                return dt.date(int(mt.group(1)), int(mt.group(2)), int(mt.group(3)))
        except ValueError:
            continue
    return None


def freshness_problem(name: str, modified_ms=None, now: dt.datetime = None) -> str:
    """'' when the file may be taken: today's date in its name, or - with no date in the name - saved within FRESH_MIN minutes."""
    now = now or dt.datetime.now()
    d = name_date(name)
    if d is not None:
        if d != now.date():
            return f"it is dated {dstr(d)} - only today's ({dstr(now.date())}) PositionBook is accepted"
        return ""
    try:
        t = dt.datetime.fromtimestamp(float(modified_ms) / 1000.0)
    except (TypeError, ValueError, OverflowError, OSError):
        return "there is no date in the name and its saved time could not be read - put today's date in the name (e.g. PositionBook_" + now.strftime("%d%m%Y") + ".csv)"
    age = (now - t).total_seconds() / 60.0
    if age > FRESH_MIN:
        return f"there is no date in the name and the file was saved {int(age)} min ago (older than {FRESH_MIN} min) - export it again or put today's date in the name"
    if age < -5:
        return "its saved time is in the future - check the computer's clock"
    return ""


def _event(msg: str):
    """An upload event: shown in the live log AND kept in logs/cncf11/cncf11__<dd-mm-yyyy>__uploads.log (the Logs button)."""
    log(msg)
    try:
        with open(log_dir() / f"cncf11__{dstr(_today())}__uploads.log", "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%d-%m-%Y %H:%M:%S}] {msg}\n")
    except Exception:                                            # noqa: BLE001
        pass


def _refuse(name, why, by=""):
    _event(f"Upload REFUSED: {name} - {why} (by {by or 'user'})")
    raise ValueError(f"{name}: {why}")


def accept_upload(name: str, data: bytes, by="", modified_ms=None, now: dt.datetime = None) -> dict:
    """The upload button: only today's PositionBook is accepted (see freshness_problem); it is kept as PositionBook.csv.
    `modified_ms` = the file's own saved time as the browser reports it (File.lastModified). Every upload - taken or refused -
    is written to the card's log."""
    name = os.path.basename(name or "")
    if not name or name.startswith(".") or name.lower().endswith((".py", ".bat", ".exe", ".part")):
        _refuse(name or "(no name)", "this file name is not allowed", by)
    if not data:
        _refuse(name, "the file is empty", by)
    if not re.match(r"^positionbook.*\.csv$", name, re.I):
        _refuse(name, "only the PositionBook csv is used here (a name like PositionBook_<ddmmyyyy>.csv)", by)
    why = freshness_problem(name, modified_ms, now)
    if why:
        _refuse(name, why, by)
    try:
        head = pd.read_csv(pd.io.common.BytesIO(data), nrows=5)
    except Exception as e:                                       # noqa: BLE001
        _refuse(name, f"cannot be read as a csv ({type(e).__name__})", by)
    cols = {COLUMN_MAP[str(c).strip().lower()] for c in head.columns if str(c).strip().lower() in COLUMN_MAP}
    need = {"ClientID", "Product", "NetQty"}
    if not need <= cols:
        _refuse(name, f"columns missing - {', '.join(sorted(need - cols))} (is this the PositionBook?)", by)
    target = root() / INPUT_NAME
    old = position_path()
    part = root() / (INPUT_NAME + ".part")
    part.write_bytes(data)
    if old.exists() and old != target:
        old.unlink()
    part.replace(target)
    d = name_date(name)
    _event(f"Upload ACCEPTED: {name} as {INPUT_NAME} ({len(data):,} bytes) by {by or 'user'} - "
           + (f"dated {dstr(d)} (today)" if d else f"no date in the name, saved within {FRESH_MIN} min"))
    return {"ok": True, "name": INPUT_NAME, "size": len(data)}


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
            "alert_reason": st.get("alert_reason", ""), "auto": False, "needs_password": s == "done" and not running,
            "settings": {k: set_[k] for k in ("send_mail", "alert_numbers")},
            "steps": [dict(key=k, label=STEP_LABEL[k], **steps.get(k, {"status": "", "msg": ""})) for k in STEP_ORDER],
            "inputs": inputs, "folder": str(root()), "log": recent_log(60)}
