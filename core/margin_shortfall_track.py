"""core/margin_shortfall_track.py - MARGIN SHORTFALL TRACK card (MARGIN_SHORTFALL_TRACK.py as a console card).

The hand routine took "the latest file of each kind" from one folder, made FINAL_SHORTFALL_<date>.xlsx and TRACK_<date>.xlsx and
mailed every TRACK file.  Here the same maths is kept, but the way the files arrive and are kept is different:

    * Margin_Detail files (AccountID, MAX; the date is only in the file NAME, e.g. Margin_Detail_20112026.xlsx) are uploaded - any
      number at once.  Every delivery is kept as a VERSION of its date (v001, v002 ...); an identical file is ignored, a changed
      one becomes the next version (the newest is used).  Nothing is overwritten or renamed.
    * SA_CM_Full, SA_FO_Full, MCX_EODSAR_<ddmmyyyy> and MCX_Intra_Output are NOT copied by hand: they are picked from the console's
      own SA04 REPORTING / EODSAR / MASTER FILE outputs of that date (also from their month-archived backup folders).
    * A date is built when it is new, its margin has a newer version, or one of its four source files changed.  A date whose
      source file is missing is BLOCKED (named in the card) and the rest go on.
    * TRACK is recomputed for the affected anchors in date order (MAX_DAYS = 5 future days).  "complete" / "broken" is only a flag
      on the TRACK, nothing is moved or deleted.
    * E-mail: only the NEWEST date that is newer than the last sent date is mailed (to rms_del by default, nobody else); older or
      revised dates are only processed.  A mail that does not go is not marked sent - the next run tries again.
    * Existing FINAL_SHORTFALL_<date>.xlsx files (also inside a .zip) can be imported once, so the TRACK windows continue.

Store (<data>/MARGIN SHORTFALL TRACK):  margin/<ddmmyyyy>/v001_<name>  final/  track/  _state/ (index.json, settings.json, run.json)
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path

import pandas as pd

NAME = "MARGIN SHORTFALL TRACK"
FOLDER_NAME = "MARGIN SHORTFALL TRACK"
PIPE = "mst"
MAX_DAYS = 5
GAP_COLS = 1
FINAL_COLS = ["Client Code", "SA_CM", "SA_FO", "MCX_INTRA", "MCX_EODSAR", "MARGIN"]
NUM_COLS = FINAL_COLS[1:]
MAIL_DEFAULTS = {"REPORT": ("rms_del@mastertrust.co.in", "")}
MAIL_LABEL = {"REPORT": "Daily Shortfall Track report"}
STEP_ORDER = ("inputs", "final", "track", "email")
STEP_LABEL = {"inputs": "Check the files", "final": "FINAL files", "track": "TRACK files", "email": "E-mail"}
# source key -> (console card, process, output file name pattern)
SOURCES = {
    "SA_CM": ("sa", "sa_cm", r"SA_CM_Full\.xlsx"),
    "SA_FO": ("sa", "sa_fo", r"SA_FO_Full\.xlsx"),
    "MCX_EODSAR": ("eodsar", "mcx_eodsar", r"MCX_EODSAR_\d{8}\.xlsx"),
    "MCX_INTRA": ("master", "mcx_intrasar", r"MCX_Intra_Output\.xlsx"),
}
SRC_LABEL = {"SA_CM": "SA_CM_Full", "SA_FO": "SA_FO_Full", "MCX_EODSAR": "MCX_EODSAR", "MCX_INTRA": "MCX_Intra_Output"}
INTRA_REMARK_COLS = ["Final Coverage (NCL - NCDEX)", "Final Remarks (L-I)"]

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_LOG = collections.deque(maxlen=400)
_ACTIVE = {"cancel": False, "log_file": None}


class Busy(Exception):
    pass


class Cancelled(Exception):
    pass


# ───────────────────────────── small helpers ─────────────────────────────
def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def _c8(d: dt.date) -> str:
    return d.strftime("%d%m%Y")


def _iso(t=None) -> str:
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _today() -> dt.date:
    return dt.date.today()


def _check_date(s: str) -> str:
    if not re.fullmatch(r"\d{2}-\d{2}-\d{4}", s or ""):
        raise ValueError("date must be dd-mm-yyyy")
    return s


def _pd(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def name_date(name: str):
    from core.cnc_f11_card import name_date as _nd
    return _nd(name)


def subject_date(d: dt.date) -> str:
    day = d.day
    suf = "th" if 10 <= day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suf} {d.strftime('%b %Y')}"


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("MST_DIR", "").strip()
    if v:
        p = Path(v)
    else:
        try:
            from core.scheduler import paths
            base = paths.data_root()
        except Exception:                                        # noqa: BLE001
            base = Path(__file__).resolve().parent.parent / "data"
        p = Path(base) / FOLDER_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_root() -> Path:
    return root()


def _sub(n: str) -> Path:
    p = root() / n
    p.mkdir(parents=True, exist_ok=True)
    return p


def margin_dir(d: dt.date) -> Path:
    p = _sub("margin") / _c8(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def final_path(d: dt.date) -> Path:
    return _sub("final") / f"FINAL_SHORTFALL_{_c8(d)}.xlsx"


def track_path(d: dt.date) -> Path:
    return _sub("track") / f"TRACK_{_c8(d)}.xlsx"


def _state_dir() -> Path:
    return _sub("_state")


def log_dir() -> Path:
    v = os.getenv("MST_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / PIPE
    except Exception:                                            # noqa: BLE001
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ───────────────────────────── mail defaults ─────────────────────────────
def mail_keys(k: str) -> tuple:
    return (f"MST_{k}_TO", f"MST_{k}_CC")


def apply_defaults() -> None:
    for k, (to, cc) in MAIL_DEFAULTS.items():
        tk, ck = mail_keys(k)
        os.environ.setdefault(tk, to)
        os.environ.setdefault(ck, cc)


def recipients(key: str = "REPORT"):
    from core.mcx_cash_report import split_addrs
    tk, ck = mail_keys(key)
    d_to, d_cc = MAIL_DEFAULTS[key]
    return split_addrs(os.environ.get(tk, d_to)), split_addrs(os.environ.get(ck, d_cc))


# ───────────────────────────── settings / index / run state ─────────────────────────────
def _settings_path() -> Path:
    return _state_dir() / "settings.json"


def _index_path() -> Path:
    return _state_dir() / "index.json"


def _run_path() -> Path:
    return _state_dir() / "run.json"


def _write(path: Path, data: dict):
    from core.safe_state import write_json
    with _STATE_LOCK:
        write_json(path, data)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return default


def read_index() -> dict:
    ix = _read_json(_index_path(), {})
    ix.setdefault("margin", {})          # iso date -> [ {v, file, sha, clients, at, by, orig} ]
    ix.setdefault("final", {})           # iso date -> {how, margin_v, sig, at, rows}
    ix.setdefault("track", {})           # iso anchor -> {status, end, days}
    ix.setdefault("sent", {"last": "", "log": []})
    ix.setdefault("imported_max", "")
    return ix


def _save_index(ix: dict):
    _write(_index_path(), ix)


def get_settings() -> dict:
    d = {"send_mail": True, "alert_numbers": ""}
    d.update(_read_json(_settings_path(), {}))
    ix = read_index()
    last = ix["sent"].get("last") or ""
    d["last_sent"] = dstr(_pd(last)) if last else ""
    return d


def save_settings(update: dict, by: str = "") -> dict:
    cur = {"send_mail": True, "alert_numbers": ""}
    cur.update(_read_json(_settings_path(), {}))
    if "send_mail" in update:
        cur["send_mail"] = bool(update["send_mail"])
    if "alert_numbers" in update:
        raw = str(update["alert_numbers"] or "").strip()
        if raw:
            from core.scheduler import failure_alert
            if not failure_alert.parse_numbers(raw):
                raise ValueError("those WhatsApp numbers are not valid")
        cur["alert_numbers"] = raw
    if "last_sent" in update:
        raw = str(update["last_sent"] or "").strip()
        with _STATE_LOCK:
            ix = read_index()
            if raw:
                _check_date(raw)
                ix["sent"]["last"] = dt.datetime.strptime(raw, "%d-%m-%Y").date().isoformat()
            else:
                ix["sent"]["last"] = ""
            ix["sent"].setdefault("log", []).append({"at": _iso(), "by": by, "event": "last sent date set to " + (raw or "(blank)")})
            _save_index(ix)
    cur["by"], cur["at"] = by, _iso()
    _write(_settings_path(), {k: cur[k] for k in ("send_mail", "alert_numbers", "by", "at")})
    return get_settings()


def read_run() -> dict:
    st = _read_json(_run_path(), {})
    st.setdefault("steps", {})
    st.setdefault("status", "idle")
    return st


def _update(**kw) -> dict:
    with _STATE_LOCK:
        st = read_run()
        st.update(kw)
        _write(_run_path(), st)
        return st


def _set_step(name: str, status: str, msg: str = ""):
    with _STATE_LOCK:
        st = read_run()
        st["steps"][name] = {"status": status, "msg": msg[:300], "at": _iso()}
        _write(_run_path(), st)


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
    print("[mst] " + msg, flush=True)


def recent_log(n: int = 120) -> list:
    return list(_LOG)[-n:]


def _open_run_log(trigger, by):
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"{PIPE}__{now:%d-%m-%Y}__{now:%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== {NAME} - {now:%d-%m-%Y %H:%M:%S} =====\nstarted by: {by or trigger} ({trigger})\n\n")
        f.flush()
        return f
    except Exception:                                            # noqa: BLE001
        return None


def _event(msg: str):
    """An upload / import event: live log AND logs/mst/mst__<dd-mm-yyyy>__uploads.log (the Logs button)."""
    log(msg)
    try:
        with open(log_dir() / f"{PIPE}__{dstr(_today())}__uploads.log", "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%d-%m-%Y %H:%M:%S}] {msg}\n")
    except Exception:                                            # noqa: BLE001
        pass


def _check_cancel():
    if _ACTIVE.get("cancel"):
        raise Cancelled("Stopped.")


# ───────────────────────────── reading the files ─────────────────────────────
def _read_table(src, name: str = "") -> pd.DataFrame:
    """xlsx / xls / csv (path or bytes) -> DataFrame (first sheet)."""
    nm = (name or (str(src) if not isinstance(src, (bytes, bytearray)) else "")).lower()
    buf = io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src
    if nm.endswith(".csv"):
        return pd.read_csv(buf)
    try:
        return pd.read_excel(buf, engine="calamine")
    except Exception:                                            # noqa: BLE001
        if hasattr(buf, "seek"):
            buf.seek(0)
        return pd.read_excel(buf)


def _col(df: pd.DataFrame, name: str, file: str = "") -> str:
    if name in df.columns:
        return name
    low = {re.sub(r"\s+", " ", str(c)).strip().lower(): c for c in df.columns}
    k = re.sub(r"\s+", " ", name).strip().lower()
    if k in low:
        return low[k]
    raise ValueError(f"column '{name}' not found" + (f" in {file}" if file else "") + f" (columns: {', '.join(map(str, df.columns))[:200]})")


def _txt(s: pd.Series) -> pd.Series:
    return s.astype("string").str.strip()


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").astype(float)


def _frame(codes: pd.Series, col: str, vals: pd.Series, how: str = "sum") -> pd.DataFrame:
    t = pd.DataFrame({"Client Code": codes.values, col: vals.values}).dropna(subset=["Client Code"])
    g = t.groupby("Client Code", sort=False)[col]
    g = g.max() if how == "max" else g.sum()
    out = g.reset_index()
    for c in NUM_COLS:
        if c not in out.columns:
            out[c] = 0.0
    return out[FINAL_COLS]


def margin_table(src, name: str = "") -> pd.DataFrame:
    df = _read_table(src, name)
    a, m = _col(df, "AccountID", name), _col(df, "MAX", name)
    return _frame(_txt(df[a]), "MARGIN", _num(df[m]), "max")


def build_final_df(paths: dict, margin_src, margin_name: str = "") -> pd.DataFrame:
    """The five sources -> FINAL_SHORTFALL (one row per client per source, as the hand script did)."""
    parts = []
    for key in ("SA_CM", "SA_FO"):
        df = _read_table(paths[key])
        fr, cc, sh = _col(df, "FINAL REMARKS", key), _col(df, "Client Code", key), _col(df, "Short(H-D)", key)
        blank = df[fr].isna() | (df[fr].astype("string").str.strip() == "")
        parts.append(_frame(_txt(df.loc[blank, cc]), key, _num(df.loc[blank, sh])))
    df = _read_table(paths["MCX_EODSAR"])
    rem, cid, eod = _col(df, "T5 _remarks", "MCX_EODSAR"), _col(df, "Client ID", "MCX_EODSAR"), _col(df, "EOD Short Allocation", "MCX_EODSAR")
    sel = df[rem].astype("string").str.strip().str.lower() == "other"
    parts.append(_frame(_txt(df.loc[sel, cid]), "MCX_EODSAR", _num(df.loc[sel, eod])))
    df = _read_table(paths["MCX_INTRA"])
    rc = next((c for c in INTRA_REMARK_COLS if c in df.columns), None)
    if rc is None:
        raise ValueError(f"MCX_Intra_Output: none of {INTRA_REMARK_COLS} found (columns: {', '.join(map(str, df.columns))[:200]})")
    cc = _col(df, "Client Code", "MCX_INTRA")
    txt = df[rc].astype("string")
    sel = txt.str.endswith("_SHORT").fillna(False).astype(bool)      # "NO SHORT" must not count
    val = txt[sel].str.replace("_SHORT", "", regex=False).str.replace("-", "", n=1, regex=False)
    parts.append(_frame(_txt(df.loc[sel, cc]), "MCX_INTRA", _num(val)))
    parts.append(margin_table(margin_src, margin_name))
    out = pd.concat(parts, ignore_index=True)
    out["Client Code"] = out["Client Code"].astype(object)
    return out.sort_values("Client Code", kind="stable").reset_index(drop=True)[FINAL_COLS]


# ───────────────────────────── margin intake (versions) ─────────────────────────────
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def latest_margin(ix: dict, iso: str):
    v = ix["margin"].get(iso) or []
    return v[-1] if v else None


def accept_margin(name: str, data: bytes, by: str = "", today: dt.date = None) -> dict:
    """One Margin_Detail file -> a version of its date.  status: new | revised | identical (ignored)."""
    d = name_date(name)
    if d is None:
        raise ValueError(f"{name}: no date in the file name - rename it like Margin_Detail_{_c8(_today())}.xlsx")
    if d > (today or _today()):
        raise ValueError(f"{name}: dated {dstr(d)}, which is in the future")
    try:
        t = margin_table(data, name)
    except ValueError as e:
        raise ValueError(f"{name}: {e}")
    except Exception as e:                                       # noqa: BLE001
        raise ValueError(f"{name}: cannot be read ({type(e).__name__})")
    sha = _sha(data)
    iso = d.isoformat()
    with _STATE_LOCK:
        ix = read_index()
        vers = ix["margin"].setdefault(iso, [])
        if any(v["sha"] == sha for v in vers):
            same = next(v for v in vers if v["sha"] == sha)
            res = {"name": name, "date": dstr(d), "status": "identical", "v": same["v"], "clients": len(t),
                   "msg": f"identical to v{same['v']:03d} - ignored"}
            _event(f"Margin upload IGNORED: {name} ({dstr(d)}) is identical to v{same['v']:03d} (by {by or 'user'})")
            return res
        prev = vers[-1] if vers else None
        n = (prev["v"] + 1) if prev else 1
        ext = os.path.splitext(name)[1].lower() or ".xlsx"
        stored = f"v{n:03d}_{re.sub(r'[^A-Za-z0-9._-]', '_', os.path.splitext(os.path.basename(name))[0])}{ext}"
        (margin_dir(d) / stored).write_bytes(data)
        vers.append({"v": n, "file": stored, "sha": sha, "clients": int(len(t)), "at": _iso(), "by": by, "orig": name})
        _save_index(ix)
    status = "revised" if prev else "new"
    msg = (f"saved as v{n:03d} - {len(t)} client(s)" + (f" (v{prev['v']:03d} had {prev['clients']})" if prev else ""))
    _event(f"Margin upload {status.upper()}: {name} -> {dstr(d)} {msg} (by {by or 'user'})")
    return {"name": name, "date": dstr(d), "status": status, "v": n, "clients": int(len(t)), "msg": msg}


# ───────────────────────────── import of existing files ─────────────────────────────
def import_final(name: str, data: bytes, by: str = "") -> dict:
    d = name_date(name)
    if d is None:
        raise ValueError(f"{name}: no date in the file name")
    try:
        df = _read_table(data, name)
    except Exception as e:                                       # noqa: BLE001
        raise ValueError(f"{name}: cannot be read ({type(e).__name__})")
    missing = [c for c in FINAL_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: columns missing - {', '.join(missing)} (is this a FINAL_SHORTFALL file?)")
    iso = d.isoformat()
    with _STATE_LOCK:
        ix = read_index()
        cur = ix["final"].get(iso)
        if cur and cur.get("how") == "built":
            return {"name": name, "date": dstr(d), "status": "kept", "msg": "already built by the console - kept"}
        p = final_path(d)
        p.write_bytes(data)
        ix["final"][iso] = {"how": "imported", "margin_v": 0, "sig": "", "at": _iso(), "rows": int(len(df)), "orig": name}
        if not ix.get("imported_max") or iso > ix["imported_max"]:
            ix["imported_max"] = iso
        ix["track"].pop(iso, None)
        _save_index(ix)
    _event(f"FINAL imported: {name} -> {dstr(d)} ({len(df)} row(s), by {by or 'user'})")
    return {"name": name, "date": dstr(d), "status": "imported", "msg": f"imported - {len(df)} row(s)"}


def accept_upload(name: str, data: bytes, by: str = "") -> dict:
    """The one upload button: Margin_Detail files, FINAL_SHORTFALL files (also inside a zip) and old TRACK files (not needed - ignored)."""
    base = os.path.basename(name or "")
    low = base.lower()
    if not base or base.startswith(".") or low.endswith((".py", ".bat", ".exe", ".part")):
        raise ValueError(f"{base or '(no name)'}: this file name is not allowed")
    if not data:
        raise ValueError(f"{base}: the file is empty")
    if low.endswith(".zip"):
        out, bad = [], []
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError(f"{base}: not a valid zip")
        for zi in zf.infolist():
            nm = os.path.basename(zi.filename)
            if zi.is_dir() or not re.match(r"^FINAL_SHORTFALL_.*\.xlsx$", nm, re.I):
                continue
            try:
                out.append(import_final(nm, zf.read(zi), by))
            except ValueError as e:
                bad.append(str(e))
        if not out and not bad:
            raise ValueError(f"{base}: no FINAL_SHORTFALL_*.xlsx file inside")
        return {"name": base, "status": "zip", "msg": f"{len(out)} FINAL file(s) imported" + (f", {len(bad)} refused" if bad else ""),
                "items": out, "rejected": bad}
    if re.match(r"^margin[_ -]?detail", low):
        return accept_margin(base, data, by)
    if re.match(r"^final_shortfall_", low):
        return import_final(base, data, by)
    if re.match(r"^track_", low):
        _event(f"TRACK file skipped: {base} - TRACK files are rebuilt from the FINAL files (by {by or 'user'})")
        return {"name": base, "status": "skipped", "date": "", "msg": "not needed - TRACK is rebuilt from the FINAL files"}
    raise ValueError(f"{base}: use Margin_Detail_<ddmmyyyy>.xlsx, FINAL_SHORTFALL_<ddmmyyyy>.xlsx or a zip of FINAL files")


# ───────────────────────────── picking the four files from the console ─────────────────────────────
def _find(card: str, proc: str, rx: str, d: dt.date):
    """The newest file of that console output for the day (also from the month-archived folder, also a DIWALI day)."""
    try:
        from core import backup_archive, daycards as dc
        base = backup_archive.day_dir(dc.card_dir(card), dc.dstr(d))
        label = dc.PROCS[proc].label
    except Exception:                                            # noqa: BLE001
        return None
    r = re.compile(rx, re.I)
    best = None
    for folder in (base / label, base / "DIWALI" / label):
        if folder.is_dir():
            for f in folder.iterdir():
                if f.is_file() and r.fullmatch(f.name) and (best is None or f.stat().st_mtime > best.stat().st_mtime):
                    best = f
    return best


def source_files(d: dt.date) -> dict:
    out = {}
    for key, (card, proc, rx) in SOURCES.items():
        p = _find(card, proc, rx, d)
        if p is not None and key == "MCX_EODSAR":
            nd = name_date(p.name)
            if nd is not None and nd != d:
                p = None                                         # the EODSAR file must be the one of this very date
        out[key] = p
    return out


def _src_sig(paths: dict) -> str:
    return "|".join(f"{k}:{p.stat().st_size}:{int(p.stat().st_mtime)}" if p else f"{k}:-" for k, p in sorted(paths.items()))


# ───────────────────────────── the plan (what is done / what is to do) ─────────────────────────────
def plan(ix: dict = None) -> list:
    ix = ix or read_index()
    isos = sorted(set(ix["margin"]) | set(ix["final"]))
    rows = []
    for iso in isos:
        d = _pd(iso)
        m = latest_margin(ix, iso)
        fin = ix["final"].get(iso)
        src = source_files(d) if m is not None else {}          # an imported-only date has no margin and needs no source
        missing = [SRC_LABEL[k] for k, v in src.items() if v is None]
        sig = _src_sig(src)
        todo = ""
        if m is not None:
            if fin is None:
                todo = "new"
            elif fin.get("how") == "imported":
                todo = "revised" if m["v"] > 0 else ""
            elif fin.get("margin_v") != m["v"]:
                todo = "revised"
            elif fin.get("sig") != sig and not missing:
                todo = "source"
        blocked = bool(todo) and (bool(missing) or m is None)
        if todo and not blocked:
            state = {"new": "to build", "revised": "to rebuild (new margin version)", "source": "to rebuild (source file changed)"}[todo]
        elif blocked:
            state = "BLOCKED"
        elif fin:
            state = "imported" if fin.get("how") == "imported" else "built"
        else:
            state = "no FINAL"
        rows.append({"iso": iso, "date": dstr(d), "margin_v": m["v"] if m else 0, "margin_versions": len(ix["margin"].get(iso, [])),
                     "margin_clients": m["clients"] if m else 0, "margin_name": m["orig"] if m else "",
                     "sources": {k: v is not None for k, v in src.items()}, "missing": missing, "todo": todo, "blocked": blocked,
                     "state": state, "final": bool(fin) and final_path(d).is_file(), "how": (fin or {}).get("how", ""),
                     "rows": (fin or {}).get("rows", 0), "track": (ix["track"].get(iso) or {}).get("status", "")})
    return rows


# ───────────────────────────── TRACK ─────────────────────────────
def _finals(ix: dict) -> list:
    return sorted(d for d in ix["final"] if final_path(_pd(d)).is_file())


def build_track(anchor: str, finals: list, cache: dict) -> dict | None:
    """TRACK_<anchor>.xlsx exactly as the hand script (next MAX_DAYS FINAL files, clients present on every day so far)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    i = finals.index(anchor)
    future = finals[i + 1: i + 1 + MAX_DAYS]
    if not future:
        return None

    def df_of(iso):
        if iso not in cache:
            cache[iso] = _read_table(str(final_path(_pd(iso))))
        return cache[iso]
    surviving = set(df_of(anchor)["Client Code"].dropna().astype(str).str.strip())
    if not surviving:
        return None
    wb = Workbook()
    ws = wb.active
    ws.title = "TRACK"
    col, broke, end = 1, False, None
    for iso in [anchor] + future:
        day = _pd(iso)
        df = df_of(iso).copy()
        df["Client Code"] = df["Client Code"].astype(str).str.strip()
        prev = set(surviving)
        surviving &= set(df["Client Code"])
        if not surviving:
            ws.merge_cells(start_row=3, start_column=col, end_row=3, end_column=col + 5)
            ws.cell(row=3, column=col, value=f"NO COMMON CLIENT CODE EXISTS ON {dstr(day)}").font = Font(bold=True)
            surviving, broke, end = prev, True, iso
            break
        ws.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col + 5)
        ws.cell(row=1, column=col, value=f"DATE: {dstr(day)}").font = Font(bold=True)
        for k, h in enumerate(FINAL_COLS):
            ws.cell(row=2, column=col + k, value=h).font = Font(bold=True)
        g = df[df["Client Code"].isin(surviving)].groupby("Client Code", sort=True)[NUM_COLS].sum().reset_index()
        for r, row in enumerate(g.itertuples(index=False), start=3):
            for c, v in enumerate(row):
                ws.cell(row=r, column=col + c, value=(v if isinstance(v, str) else float(v)))
        col += len(FINAL_COLS) + GAP_COLS
        end = iso
    p = track_path(_pd(anchor))
    tmp = p.with_suffix(".part")
    wb.save(tmp)
    tmp.replace(p)
    status = "broken" if broke else ("complete" if len(future) == MAX_DAYS else "open")
    return {"status": status, "end": end if status != "open" else "", "days": len(future)}


# ───────────────────────────── mail ─────────────────────────────
MAIL_BODY = """<html><body>
<p>Dear Team,</p>
<p>Please find attached the <b>Shortfall Track Reports</b> generated.</p>
<p>This report reflects client-wise shortfall continuity across multiple settlement days and provides a consolidated view to support
operational monitoring and timely follow-up actions.</p>
<p>All available track reports present as of today have been attached for your review.</p>
<p>Kindly review the attached reports and take necessary action wherever applicable.</p>
<br>
<p>Best regards,<br><b>Kartavya Dhillon</b><br>Master Capital Services Ltd<br>&#9742; 011-66331000 | Ext. 84</p>
</body></html>"""


def send_files(subject, body, paths, to, cc, attempts=5, gap=10, sleep=time.sleep, smtp_factory=None):
    """One html mail with the attachments; retried; raises RuntimeError if it never goes."""
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
            msg.attach(MIMEText(body, "html", "utf-8"))
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


def send_decision(ix: dict, rows: list) -> dict:
    """Which date (if any) is mailed now and which TRACK files go with it.
    -> {"date": iso|None, "files": [Path], "why": str, "hold": bool}"""
    finals = _finals(ix)
    if not finals:
        return {"date": None, "files": [], "why": "no FINAL file yet", "hold": False}
    newest = finals[-1]
    last = ix["sent"].get("last") or ix.get("imported_max") or ""
    if last and newest <= last:
        return {"date": None, "files": [], "why": f"nothing new - {dstr(_pd(newest))} is not newer than the last sent date {dstr(_pd(last))}", "hold": False}
    waiting = [r for r in rows if r["iso"] > newest and r["margin_v"] and (r["blocked"] or r["todo"])]
    if waiting:
        r = waiting[0]
        return {"date": None, "files": [], "hold": True,
                "why": f"waiting for {r['date']} ({'missing: ' + ', '.join(r['missing']) if r['missing'] else 'not built yet'}) - the newest date is mailed once"}
    files = []
    for iso in sorted(ix["track"]):
        t = ix["track"][iso]
        if not track_path(_pd(iso)).is_file():
            continue
        if t.get("status") == "open" or (t.get("end") and (not last or t["end"] > last)):
            files.append(track_path(_pd(iso)))
    if not files:
        return {"date": newest, "files": [], "why": "no TRACK file to send (no date has a following day yet)", "hold": False}
    return {"date": newest, "files": files, "why": "", "hold": False}


# ───────────────────────────── the run ─────────────────────────────
def is_running() -> bool:
    return _RUN_LOCK.locked()


def needs_password() -> bool:
    return False


def _replace_final(d: dt.date):
    """An older FINAL of the date is kept (final/_replaced) before it is rebuilt."""
    p = final_path(d)
    if p.is_file():
        old = _sub("final") / "_replaced"
        old.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, old / f"{p.stem}__{dt.datetime.now():%Y%m%d_%H%M%S}.xlsx")


def step_final(ix: dict, rows: list, errors: list) -> list:
    built = []
    for r in rows:
        if not r["todo"]:
            continue
        _check_cancel()
        d = _pd(r["iso"])
        if r["blocked"]:
            log(f"{r['date']}: BLOCKED - " + (("missing " + ", ".join(r["missing"])) if r["missing"] else "no margin file"))
            continue
        try:
            m = latest_margin(ix, r["iso"])
            src = source_files(d)
            log(f"{r['date']}: building FINAL (margin v{m['v']:03d}, " + ", ".join(f"{SRC_LABEL[k]}={p.name}" for k, p in src.items()) + ")")
            df = build_final_df(src, str(margin_dir(d) / m["file"]), m["file"])
            _replace_final(d)
            p = final_path(d)
            tmp = p.with_suffix(".part")
            df.to_excel(tmp, index=False, engine="openpyxl")
            tmp.replace(p)
            with _STATE_LOCK:
                ix2 = read_index()
                ix2["final"][r["iso"]] = {"how": "built", "margin_v": m["v"], "sig": _src_sig(src), "at": _iso(), "rows": int(len(df))}
                _save_index(ix2)
            ix["final"][r["iso"]] = {"how": "built", "margin_v": m["v"], "sig": _src_sig(src), "at": _iso(), "rows": int(len(df))}
            built.append(r["iso"])
            log(f"{r['date']}: FINAL_SHORTFALL_{_c8(d)}.xlsx saved - {len(df)} row(s)")
        except Cancelled:
            raise
        except Exception as e:                                   # noqa: BLE001
            errors.append(f"{r['date']}: {type(e).__name__}: {e}")
            log(f"{r['date']}: FINAL FAILED - {type(e).__name__}: {e}")
    return built


def step_track(ix: dict, changed: list) -> dict:
    finals = _finals(ix)
    anchors = set()
    for c in changed:
        if c in finals:
            i = finals.index(c)
            anchors.update(finals[max(0, i - MAX_DAYS): i + 1])
    anchors.update(finals[-(MAX_DAYS + 1):])                      # the live window is always refreshed
    anchors.update(a for a in finals if a not in ix["track"] or not track_path(_pd(a)).is_file())
    cache, done = {}, {}
    for a in sorted(anchors):
        _check_cancel()
        res = build_track(a, finals, cache)
        if res is None:
            continue
        done[a] = res
        log(f"TRACK_{_c8(_pd(a))}.xlsx - {res['days']} following day(s) - {res['status']}")
    with _STATE_LOCK:
        ix2 = read_index()
        ix2["track"].update(done)
        _save_index(ix2)
    ix["track"].update(done)
    return done


def step_email(ix: dict, rows: list, sleep=time.sleep, smtp_factory=None, by: str = "") -> dict:
    dec = send_decision(ix, rows)
    if not dec["date"]:
        return {"sent": False, "skipped": True, "why": dec["why"], "hold": dec["hold"]}
    if not dec["files"]:
        return {"sent": False, "skipped": True, "why": dec["why"], "hold": False}
    d = _pd(dec["date"])
    to, cc = recipients("REPORT")
    subject = f"Daily Shortfall Track Reports Till - {subject_date(d)}"
    entry = {"at": _iso(), "by": by, "date": dec["date"], "to": to, "cc": cc, "files": [p.name for p in dec["files"]]}
    try:
        send_files(subject, MAIL_BODY, dec["files"], to, cc, sleep=sleep, smtp_factory=smtp_factory)
    except Cancelled:
        raise
    except Exception as e:                                       # noqa: BLE001
        entry.update(ok=False, error=str(e)[:300])
        with _STATE_LOCK:
            ix2 = read_index()
            ix2["sent"].setdefault("log", []).append(entry)
            _save_index(ix2)
        raise
    entry["ok"] = True
    with _STATE_LOCK:
        ix2 = read_index()
        ix2["sent"]["last"] = dec["date"]
        ix2["sent"].setdefault("log", []).append(entry)
        ix2["sent"]["log"] = ix2["sent"]["log"][-200:]
        _save_index(ix2)
    return {"sent": True, "date": dec["date"], "files": entry["files"], "to": to}


def send_alert(headline, detail, sender=None) -> dict:
    """WhatsApp FAILURE ALERT picture (same as the other cards). Never raises."""
    rec = {"sent": False, "reason": ""}
    try:
        from core.scheduler import connections, failure_alert, helpers as H, paths
        raw = get_settings().get("alert_numbers") or ""
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
        img = failure_alert.render_image(paths.sched_root() / "alerts" / f"mst_{int(time.time())}.png", process=NAME,
                                         schedule="Manual run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-",
                                         trigger="Manual run", attempt_text="final", headline=headline, detail=detail,
                                         run_id="MST", kind="FAILED")

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


def run(trigger="manual", by="", alert=None, sleep=time.sleep, smtp_factory=None) -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy(f"{NAME} is already running.")
    alert = alert or send_alert
    _ACTIVE["cancel"] = False
    _ACTIVE["log_file"] = _open_run_log(trigger, by)
    _update(status="running", started=_iso(), finished="", error="", trigger=trigger, by=by, steps={}, sent=[], counts={},
            alert_sent=False, alert_reason="", note="")
    result = {"ok": False}
    try:
        log(f"=== {NAME} started ({trigger}{', ' + by if by else ''}) ===")
        ix = read_index()
        _set_step("inputs", "running")
        rows = plan(ix)
        todo = [r for r in rows if r["todo"]]
        blocked = [r for r in todo if r["blocked"]]
        _set_step("inputs", "ok", f"{len(rows)} date(s) · {len(todo) - len(blocked)} to build" + (f" · {len(blocked)} BLOCKED" if blocked else ""))
        for r in blocked:
            log(f"BLOCKED {r['date']}: " + (("missing " + ", ".join(r["missing"])) if r["missing"] else "no margin file"))
        _check_cancel()
        errors = []
        _set_step("final", "running")
        built = step_final(ix, rows, errors)
        _set_step("final", "failed" if errors else "ok",
                  (f"{len(built)} built" + (f" · {len(blocked)} blocked" if blocked else "") + (f" · {len(errors)} failed" if errors else "")))
        _update(counts={"dates": len(rows), "built": len(built), "blocked": len(blocked), "failed": len(errors)})
        _check_cancel()
        _set_step("track", "running")
        done = step_track(ix, built)
        _set_step("track", "ok", f"{len(done)} TRACK file(s) refreshed")
        _check_cancel()
        rows = plan(read_index())
        if get_settings().get("send_mail", True):
            _set_step("email", "running")
            res = step_email(read_index(), rows, sleep=sleep, smtp_factory=smtp_factory, by=by)
            if res.get("sent"):
                _update(sent=["REPORT"])
                _set_step("email", "ok", f"sent for {dstr(_pd(res['date']))}: " + ", ".join(res["files"]))
            else:
                _set_step("email", "skipped", res.get("why", ""))
                log("E-mail not sent: " + res.get("why", ""))
        else:
            _set_step("email", "skipped", "e-mail is switched off in Settings")
        if errors:
            raise RuntimeError("; ".join(errors)[:500])
        _update(status="done", finished=_iso(), error="")
        log(f"=== {NAME} finished ===")
        result = {"ok": True, "built": built}
    except Cancelled:
        _update(status="stopped", finished=_iso(), error="Stopped.")
        log("Stopped.")
        result = {"ok": False, "stopped": True}
    except Exception as e:                                       # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        for s in STEP_ORDER:
            if (read_run()["steps"].get(s) or {}).get("status") == "running":
                _set_step(s, "failed", str(e))
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
    if background:
        threading.Thread(target=lambda: run("manual", by), name="mst-manual", daemon=True).start()
        return {"ok": True, "started": True}
    return run("manual", by)


def any_fetching(exclude: str = "") -> bool:
    return False


def fetch_one(what: str, by="") -> dict:
    raise ValueError("this card has no downloads - upload the Margin_Detail files")


def stop_running(by="") -> bool:
    if not is_running():
        return False
    _ACTIVE["cancel"] = True
    log(f"Stop requested by {by or 'user'}.")
    return True


# ───────────────────────────── status ─────────────────────────────
def input_status() -> list:
    return [{k: r[k] for k in ("date", "state", "margin_v", "margin_versions", "margin_clients", "sources", "missing", "track")} for r in plan()]


def status() -> dict:
    st = read_run()
    running = is_running()
    s = st.get("status", "idle")
    if s == "running" and not running:
        s = "failed"
        st["error"] = st.get("error") or "Interrupted (the server restarted while it was running)."
    ix = read_index()
    rows = plan(ix)
    dec = send_decision(ix, rows)
    steps = {k: dict(v) for k, v in (st.get("steps") or {}).items()}
    for v in steps.values():
        if v.get("status") == "running" and not running:
            v["status"], v["msg"] = "failed", "interrupted (the server restarted or the step was stopped)"
    last = ix["sent"].get("last") or ""
    base = ""
    if not last and ix.get("imported_max"):
        base = ix["imported_max"]
    set_ = get_settings()
    return {"name": NAME, "status": "running" if running else s, "running": running, "fetching": False,
            "stopping": bool(_ACTIVE.get("cancel")) and running, "started": st.get("started", ""), "finished": st.get("finished", ""),
            "error": st.get("error", ""), "trigger": st.get("trigger", ""), "by": st.get("by", ""), "counts": st.get("counts", {}),
            "sent": st.get("sent", []), "alert_sent": bool(st.get("alert_sent")), "alert_reason": st.get("alert_reason", ""),
            "auto": False, "needs_password": False,
            "settings": {k: set_[k] for k in ("send_mail", "alert_numbers", "last_sent")},
            "last_sent": dstr(_pd(last)) if last else "", "baseline": dstr(_pd(base)) if base else "",
            "next_mail": {"date": dstr(_pd(dec["date"])) if dec["date"] else "", "files": [p.name for p in dec["files"]], "why": dec["why"], "hold": dec["hold"]},
            "mail_log": [x for x in ix["sent"].get("log", []) if x.get("ok") is not None][-8:][::-1],
            "dates": rows[::-1][:40],
            "steps": [dict(key=k, label=STEP_LABEL[k], **steps.get(k, {"status": "", "msg": ""})) for k in STEP_ORDER],
            "inputs": [], "folder": str(root()), "log": recent_log(60)}


# ───────────────────────────── files / backup (margin versions, FINAL, TRACK by date) ─────────────────────────────
def _day_files(d: dt.date) -> list:
    out = []
    mdir = root() / "margin" / _c8(d)
    if mdir.is_dir():
        out += sorted(p for p in mdir.iterdir() if p.is_file())
    for p in (final_path(d), track_path(d)):
        if p.is_file():
            out.append(p)
    return out


def list_files(sub: str = "") -> list:
    if not sub:
        return []
    d = dt.datetime.strptime(_check_date(sub), "%d-%m-%Y").date()
    return [{"name": p.name, "size": p.stat().st_size, "time": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S")}
            for p in _day_files(d)]


def list_backup_dates() -> list:
    ix = read_index()
    return sorted({dstr(_pd(x)) for x in list(ix["margin"]) + list(ix["final"])}, key=lambda s: dt.datetime.strptime(s, "%d-%m-%Y"), reverse=True)


def file_path(name: str, sub: str = "") -> Path:
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("bad file name")
    if not sub:
        raise FileNotFoundError(name)
    d = dt.datetime.strptime(_check_date(sub), "%d-%m-%Y").date()
    for p in _day_files(d):
        if p.name == name:
            return p
    raise FileNotFoundError(name)


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
    z = Path(tempfile.gettempdir()) / f"MARGIN_SHORTFALL_TRACK_{_c8(a)}_{_c8(b)}_{int(time.time())}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dates_between(a, b):
            for p in _day_files(d):
                zf.write(p, f"{dstr(d)}/{p.name}")
    return z
