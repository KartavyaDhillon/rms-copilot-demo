"""UPDATE LIMITS DATA - every Update Limits file kept as Parquet, so one client's limits for any date / time come back
in well under a second (instead of opening a 100 MB .xlsx).

Where the files come from
    1. CTCL vs NMASS: each run moves its Update Limits<ddmmyyyy>.xlsx to
       <CTCL folder>\\BACKUPFILES AUTOMATED\\<dd-mm-yyyy>\\<NN>\\. Every day at the auto time (default 18:00) the files of
       today (and of the 5 days before, when one was missed) are stored. "Store today now" does the same at once.
    2. Manual upload (after the last CTCL run, about 3 pm, or any old file): the original is kept under uploads\\.
    3. "Load history": the CTCL backup folders of a date range (for example the last 2-3 months), one file after another.

The time of a snapshot (column SNAP_TIME), in this order:
    a time in the file name (Update Limits05112026_1530.xlsx, ... 15-30-00 ...) ->
    the file's modified time (the moment it was downloaded; copies keep it; for an upload the browser sends it) when it
    is on the same date -> the CTCL run's own time (its FULL_FILE_CTCL_VS_NMASS_NN.xlsx) -> for a manual upload with
    none of these the time must be typed.

Layout under <console data>\\UPDATE LIMITS DATA\\
    parquet\\<yyyy-mm>\\UL_<yyyy-mm-dd>_<HHMMSS>.parquet   one file per snapshot, sorted by Entity, + TRADE_DATE / SNAP_TIME / SOURCE
    uploads\\<dd-mm-yyyy>\\<original name>                 manual uploads (kept KEEP_UPLOAD_DAYS days)
    index.json  settings.json  ul.log
"""
from __future__ import annotations

import datetime as dt
import json
import os
import queue
import re
import threading
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional

FOLDER = "UPDATE LIMITS DATA"
KEY = "Entity"
KEEP_UPLOAD_DAYS = 45
MAX_RANGE_DAYS = 93
EXTRA = ["TRADE_DATE", "SNAP_TIME", "SOURCE"]
# shown first in the client view (the rest follow in file order)
KEY_FIELDS = ["ClientName", "Profile", "Cash", "Collateral(Total)", "MarginUsed", "Available Margin", "Payin", "Payout",
              "Unrealized", "UnrealizedEqMargin", "Realized", "PeakMargin", "Block Amount", "Remarks Text", "Remarks Amount",
              "DaylongCash", "UnclearedCash", "BrokerCollateral", "Span", "Exposure", "Premium", "VarElm", "Additional Margin"]

_LOCK = threading.RLock()
_Q: "queue.Queue[dict]" = queue.Queue()
_JOB: Dict = {"running": False, "kind": "", "current": "", "done": 0, "total": 0, "stored": 0, "skipped": 0,
              "failed": [], "lines": deque(maxlen=200), "started": "", "finished": ""}
_STOP = threading.Event()
_WORKER: Dict = {}
_TICK: Dict = {}


# ───────────────────────────── folders / small helpers ─────────────────────────────
def data_dir() -> Path:
    v = os.getenv("UL_DATA_DIR", "").strip()
    if v:
        p = Path(v)
    else:
        try:
            from core.scheduler import paths
            p = Path(paths.data_root()) / FOLDER
        except Exception:
            p = Path(__file__).resolve().parents[1] / "data" / FOLDER
    p.mkdir(parents=True, exist_ok=True)
    return p


def ctcl_base() -> Path:
    return Path(os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS"))


def ctcl_backup_root() -> Path:
    s = settings().get("ctcl_root") or ""
    return Path(s) if s.strip() else ctcl_base() / "BACKUPFILES AUTOMATED"


def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def parse_date(s, default: Optional[dt.date] = None) -> Optional[dt.date]:
    s = str(s or "").strip()
    if not s:
        return default
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d%m%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"date not understood: {s!r} (use 05-11-2026)")


def parse_time(s) -> Optional[str]:
    m = re.match(r"^\s*([01]?\d|2[0-3])[:.\-]?([0-5]\d)(?:[:.\-]?([0-5]\d))?\s*$", str(s or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3) or '00'}" if m else None


def log(msg: str):
    line = f"[{dt.datetime.now():%d-%m %H:%M:%S}] {msg}"
    _JOB["lines"].append(line)
    try:
        with open(data_dir() / "ul.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _atomic_json(p: Path, obj):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, p)


# ───────────────────────────── settings ─────────────────────────────
DEFAULTS = {"auto": True, "time": "18:00", "ctcl_root": "", "last_auto": ""}


def settings() -> dict:
    try:
        s = json.loads((data_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    return {**DEFAULTS, **s}


def save_settings(new: dict) -> dict:
    with _LOCK:
        s = settings()
        if "auto" in new:
            s["auto"] = bool(new["auto"])
        if "time" in new:
            t = parse_time(new["time"])
            if not t:
                raise ValueError("auto time must look like 18:00")
            s["time"] = t[:5]
        if "ctcl_root" in new:
            s["ctcl_root"] = str(new["ctcl_root"] or "").strip()
        if "last_auto" in new:
            s["last_auto"] = new["last_auto"]
        _atomic_json(data_dir() / "settings.json", s)
        return s


# ───────────────────────────── index ─────────────────────────────
def index() -> List[dict]:
    try:
        return json.loads((data_dir() / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_index(rows: List[dict]):
    rows.sort(key=lambda e: (e["date"], e["time"]))
    _atomic_json(data_dir() / "index.json", rows)


def snapshots(d: Optional[dt.date] = None, d2: Optional[dt.date] = None) -> List[dict]:
    rows = index()
    if d:
        a, b = d.isoformat(), (d2 or d).isoformat()
        rows = [e for e in rows if a <= e["date"] <= b]
    return rows


def summary() -> dict:
    rows = index()
    by: Dict[str, int] = {}
    for e in rows:
        by[e["date"]] = by.get(e["date"], 0) + 1
    last = rows[-1] if rows else None
    today = dt.date.today().isoformat()
    size = sum(int(e.get("bytes") or 0) for e in rows)
    return {"count": len(rows), "days": len(by), "dates": [{"date": k, "n": by[k]} for k in sorted(by, reverse=True)],
            "latest": last, "today_n": by.get(today, 0), "bytes": size}


# ───────────────────────────── date / time of a file ─────────────────────────────
_DATE_RX = re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)")


def date_in_name(name: str) -> Optional[dt.date]:
    for m in _DATE_RX.finditer(name):
        try:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
    return None


def time_in_name(name: str) -> Optional[str]:
    stem = Path(name).stem
    stem = _DATE_RX.sub(" ", stem)
    m = re.search(r"(?<!\d)([01]\d|2[0-3])[:.\-_ ]?([0-5]\d)(?:[:.\-_ ]?([0-5]\d))?(?!\d)", stem)
    if m:
        return f"{m.group(1)}:{m.group(2)}:{m.group(3) or '00'}"
    m = re.search(r"(?<!\d)(\d{1,2})[:.\-_ ]?([0-5]\d)?\s*(am|pm)(?![a-z])", stem, re.I)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        if h < 24:
            return f"{h:02d}:{m.group(2) or '00'}:00"
    return None


def _same_day_time(ts: Optional[float], d: dt.date) -> Optional[str]:
    if not ts:
        return None
    t = dt.datetime.fromtimestamp(ts)
    return t.strftime("%H:%M:%S") if t.date() == d else None


def _run_time(run_dir: Optional[Path], d: dt.date) -> Optional[str]:
    if not run_dir:
        return None
    try:
        outs = sorted(run_dir.glob("FULL_FILE_CTCL_VS_NMASS_*.xlsx"), key=lambda p: p.stat().st_mtime)
        if outs:
            return _same_day_time(outs[0].stat().st_mtime, d)
    except OSError:
        pass
    return None


def snap_time(name: str, d: dt.date, mtime: Optional[float], run_dir: Optional[Path] = None) -> tuple:
    """(HH:MM:SS, how) or (None, '')."""
    t = time_in_name(name)
    if t:
        return t, "file name"
    t = _same_day_time(mtime, d)
    if t:
        return t, "file modified time"
    t = _run_time(run_dir, d)
    if t:
        return t, "CTCL run time"
    return None, ""


# ───────────────────────────── reading one file -> parquet ─────────────────────────────
def read_limits(path: Path):
    """The whole sheet as a polars DataFrame (header = the row holding 'Entity'); Entity is text."""
    import polars as pl
    if path.suffix.lower() == ".csv":
        df = pl.read_csv(path, infer_schema_length=20000, schema_overrides={KEY: pl.Utf8}, ignore_errors=True)
    else:
        import fastexcel
        rd = fastexcel.read_excel(str(path))
        hdr = 0
        head = rd.load_sheet(0, header_row=None, n_rows=15).to_polars()
        for i, row in enumerate(head.iter_rows()):
            if any(str(v).strip() == KEY for v in row if v is not None):
                hdr = i
                break
        df = rd.load_sheet(0, header_row=hdr, schema_sample_rows=None, dtypes={KEY: "string"}).to_polars()
    df = df.rename({c: str(c).strip() for c in df.columns if str(c).strip() != c and str(c).strip() not in df.columns})
    if KEY not in df.columns:
        raise ValueError(f"no '{KEY}' column - is this an Update Limits file?")
    df = df.with_columns(pl.col(KEY).cast(pl.Utf8).str.strip_chars())
    df = df.filter(pl.col(KEY).is_not_null() & (pl.col(KEY) != ""))
    if df.height == 0:
        raise ValueError("the file has no client rows")
    return df


def _parquet_path(d: dt.date, t: str) -> Path:
    return data_dir() / "parquet" / d.strftime("%Y-%m") / f"UL_{d.isoformat()}_{t.replace(':', '')}.parquet"


def _sig(path: Path) -> str:
    st = path.stat()
    return f"{path.name}|{st.st_size}|{int(st.st_mtime)}"


def ingest_file(path: Path, d: dt.date, source: str, t: Optional[str] = None, time_src: str = "",
                run_dir: Optional[Path] = None, mtime: Optional[float] = None, by: str = "", keep_path: str = "") -> dict:
    """Store one Update Limits file as a snapshot -> {"ok", "status": stored|skipped, "entry"}."""
    import polars as pl
    path = Path(path)
    sig = _sig(path)
    with _LOCK:
        for e in index():
            if e.get("sig") == sig and e["date"] == d.isoformat():
                return {"ok": True, "status": "skipped", "entry": e, "why": "already stored"}
    if not t:
        t, time_src = snap_time(path.name, d, mtime if mtime is not None else path.stat().st_mtime, run_dir)
    if not t:
        raise ValueError(f"{path.name}: no time found (not in the name, modified time is another day) - give the time")
    t0 = time.time()
    df = read_limits(path)
    df = df.with_columns(pl.lit(d).alias("TRADE_DATE"), pl.lit(t).alias("SNAP_TIME"), pl.lit(source).alias("SOURCE"))
    df = df.select(EXTRA + [c for c in df.columns if c not in EXTRA]).sort(KEY)
    out = _parquet_path(d, t)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    df.write_parquet(tmp, compression="zstd", row_group_size=16384, statistics=True)
    os.replace(tmp, out)
    entry = {"id": f"{d.isoformat()}_{t.replace(':', '')}", "date": d.isoformat(), "time": t, "time_src": time_src,
             "source": source, "file": path.name, "src_path": keep_path or str(path), "sig": sig, "rows": df.height,
             "cols": df.width - len(EXTRA), "parquet": str(out.relative_to(data_dir())), "bytes": out.stat().st_size,
             "stored_at": dt.datetime.now().isoformat(timespec="seconds"), "by": by, "secs": round(time.time() - t0, 1)}
    with _LOCK:
        rows = [e for e in index() if e["id"] != entry["id"]]
        replaced = len(rows) != len(index())
        rows.append(entry)
        _save_index(rows)
    return {"ok": True, "status": "replaced" if replaced else "stored", "entry": entry}


# ───────────────────────────── finding the CTCL files of a day ─────────────────────────────
def ctcl_files(d: dt.date, root: Optional[Path] = None) -> List[dict]:
    """Update Limits files of day d: every run folder of the CTCL backup, + the one still in the CTCL folder."""
    found = []
    root = Path(root) if root else ctcl_backup_root()
    day = root / dstr(d)
    try:
        for run in sorted(p for p in day.iterdir() if p.is_dir()):
            for f in run.glob("Update Limits*"):
                if f.suffix.lower() in (".xlsx", ".csv") and f.is_file():
                    found.append({"path": f, "run_dir": run, "source": f"CTCL run {run.name}"})
    except OSError:
        pass
    if d == dt.date.today():
        try:
            for f in ctcl_base().glob(f"Update Limits*{d:%d%m%Y}*.xlsx"):
                if f.is_file() and time.time() - f.stat().st_mtime > 120:      # not being written now
                    found.append({"path": f, "run_dir": None, "source": "CTCL folder (before its run)"})
        except OSError:
            pass
    return found


# ───────────────────────────── the background worker ─────────────────────────────
def job() -> dict:
    j = dict(_JOB)
    j["lines"] = list(_JOB["lines"])[-60:]
    j["queued"] = _Q.qsize()
    return j


def _worker():
    while True:
        task = _Q.get()
        try:
            if _STOP.is_set():
                continue
            _JOB["current"] = task.get("label") or Path(task["path"]).name
            try:
                r = ingest_file(Path(task["path"]), task["date"], task["source"], t=task.get("time"),
                                time_src=task.get("time_src", ""), run_dir=task.get("run_dir"), mtime=task.get("mtime"),
                                by=task.get("by", ""), keep_path=task.get("keep_path", ""))
                e = r["entry"]
                if r["status"] == "skipped":
                    _JOB["skipped"] += 1
                    log(f"= {dstr(task['date'])} {Path(task['path']).name}: already stored ({e['time']})")
                else:
                    _JOB["stored"] += 1
                    log(f"✓ {dstr(task['date'])} {e['time']} ({e['time_src']}) {e['source']}: {e['rows']:,} clients in {e['secs']} s")
            except Exception as ex:                                            # noqa: BLE001
                _JOB["failed"].append(f"{dstr(task['date'])} {Path(task['path']).name}: {ex}")
                log(f"✗ {dstr(task['date'])} {Path(task['path']).name}: {type(ex).__name__}: {ex}")
        finally:
            _JOB["done"] += 1
            _Q.task_done()
            if _Q.empty():
                if _JOB["running"]:
                    log(f"Finished: {_JOB['stored']} stored, {_JOB['skipped']} already there, {len(_JOB['failed'])} failed.")
                _JOB.update(running=False, current="", finished=dt.datetime.now().isoformat(timespec="seconds"))
                _STOP.clear()


def _ensure_worker():
    th = _WORKER.get("t")
    if not th or not th.is_alive():
        th = threading.Thread(target=_worker, name="update-limits-worker", daemon=True)
        _WORKER["t"] = th
        th.start()


def _enqueue(tasks: List[dict], kind: str):
    if not tasks:
        return 0
    with _LOCK:
        if not _JOB["running"]:
            _JOB.update(running=True, kind=kind, done=0, total=0, stored=0, skipped=0, failed=[],
                        started=dt.datetime.now().isoformat(timespec="seconds"), finished="")
        _JOB["total"] += len(tasks)
        _ensure_worker()
        for t in tasks:
            _Q.put(t)
    return len(tasks)


def enqueue_days(days: List[dt.date], kind: str, root: Optional[Path] = None, by: str = "") -> dict:
    known = {e.get("sig") for e in index()}
    tasks, nfiles = [], 0
    for d in days:
        for f in ctcl_files(d, root):
            nfiles += 1
            try:
                if _sig(f["path"]) in known:
                    continue
            except OSError:
                continue
            tasks.append({"path": f["path"], "date": d, "source": f["source"], "run_dir": f["run_dir"], "by": by,
                          "label": f"{dstr(d)} {f['source']}"})
    n = _enqueue(tasks, kind)
    if n:
        log(f"{kind}: {n} file(s) to store ({nfiles - n} already stored) from {Path(root) if root else ctcl_backup_root()}")
    return {"ok": True, "queued": n, "found": nfiles}


def load_history(d1: dt.date, d2: dt.date, root: str = "", by: str = "") -> dict:
    if d2 < d1:
        d1, d2 = d2, d1
    if (d2 - d1).days > 400:
        raise ValueError("pick at most about one year at a time")
    r = Path(root) if root.strip() else ctcl_backup_root()
    if not r.is_dir():
        raise ValueError(f"folder not found on the server: {r}")
    days = [d1 + dt.timedelta(days=i) for i in range((d2 - d1).days + 1)]
    res = enqueue_days(days, "history", r, by)
    if not res["found"]:
        res["note"] = f"no Update Limits files in {r}\\<dd-mm-yyyy>\\<NN>\\ for these dates"
    return res


def stop() -> bool:
    if not _JOB["running"]:
        return False
    _STOP.set()
    try:
        while True:
            _Q.get_nowait()
            _Q.task_done()
            _JOB["done"] += 1
    except queue.Empty:
        pass
    log("Stopped by the user - the file being stored now finishes, the rest is dropped.")
    return True


# ───────────────────────────── manual upload ─────────────────────────────
def upload_target(name: str, d: dt.date) -> Path:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", Path(name).name).strip() or "Update Limits.xlsx"
    p = data_dir() / "uploads" / dstr(d)
    p.mkdir(parents=True, exist_ok=True)
    return p / safe


def check_upload(name: str, date_hint: str = "", time_hint: str = "", lastmod_ms: str = "") -> dict:
    """Decide date and time of an upload BEFORE it is sent -> {date, time, time_src} or ValueError."""
    if Path(name).suffix.lower() not in (".xlsx", ".csv"):
        raise ValueError("only .xlsx (or .csv) Update Limits files")
    d = date_in_name(name) or parse_date(date_hint)
    if not d:
        raise ValueError("no date in the file name (ddmmyyyy) - pick the date")
    t, how = parse_time(time_hint), "typed"
    if not t:
        t, how = time_in_name(name), "file name"
    if not t and str(lastmod_ms or "").strip():
        try:
            t, how = _same_day_time(int(float(lastmod_ms)) / 1000.0, d), "file modified time"
        except ValueError:
            t = None
    if not t:
        raise ValueError("no time: not in the file name and the file's modified time is another day - type the time")
    return {"date": d, "time": t, "time_src": how}


def finish_upload(tmp: Path, name: str, info: dict, by: str = "") -> dict:
    d = info["date"]
    dest = upload_target(name, d)
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}_{info['time'].replace(':', '')}{dest.suffix}")
    os.replace(tmp, dest)
    _enqueue([{"path": dest, "date": d, "source": "manual upload", "time": info["time"], "time_src": info["time_src"],
               "by": by, "keep_path": str(dest), "label": f"{dstr(d)} upload {dest.name}"}], "upload")
    log(f"Upload {dest.name}: {dstr(d)} {info['time']} ({info['time_src']}) - storing…")
    return {"ok": True, "name": dest.name, "date": dstr(d), "time": info["time"], "time_src": info["time_src"]}


def cleanup_uploads(keep_days: int = KEEP_UPLOAD_DAYS) -> int:
    n, limit = 0, dt.date.today() - dt.timedelta(days=keep_days)
    root = data_dir() / "uploads"
    if not root.is_dir():
        return 0
    for sub in root.iterdir():
        try:
            if sub.is_dir() and dt.datetime.strptime(sub.name, "%d-%m-%Y").date() < limit:
                for f in sub.iterdir():
                    f.unlink()
                sub.rmdir()
                n += 1
        except (ValueError, OSError):
            continue
    return n


# ───────────────────────────── search / downloads ─────────────────────────────
def _pick(d1: dt.date, d2: Optional[dt.date], times: Optional[List[str]]) -> List[dict]:
    d2 = d2 or d1
    if d2 < d1:
        d1, d2 = d2, d1
    if (d2 - d1).days > MAX_RANGE_DAYS:
        raise ValueError(f"pick at most {MAX_RANGE_DAYS} days")
    rows = snapshots(d1, d2)
    if times:
        want = {t[:5] for t in times if t}
        rows = [e for e in rows if e["time"][:5] in want or e["id"] in times]
    return rows


MAX_CLIENTS = 500


def client_list(client: str) -> List[str]:
    """'7GDW01, 4790 6WBB2040' (comma / space / new line) -> ['7GDW01', '4790', '6WBB2040'] (upper case, no repeats)."""
    out = []
    for c in re.split(r"[,;\s]+", str(client or "").upper()):
        c = c.strip()
        if c and c not in out:
            out.append(c)
    if not out:
        raise ValueError("type a client code")
    if len(out) > MAX_CLIENTS:
        raise ValueError(f"at most {MAX_CLIENTS} client codes at a time (got {len(out)})")
    return out


def _client_frame(client: str, rows: List[dict]):
    import polars as pl
    qs = client_list(client)
    qz = [q.lstrip("0") for q in qs if q.lstrip("0")]
    up = pl.col(KEY).str.to_uppercase()
    cond = up.is_in(qs)
    if qz:                                    # 080011226 and 80011226 are the same client
        cond = cond | up.str.strip_chars_start("0").is_in(qz)

    def one(e):
        p = data_dir() / e["parquet"]
        if not p.exists():
            return None
        return pl.scan_parquet(p).filter(cond).collect()
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(rows)))) as ex:
        parts = [x for x in ex.map(one, rows) if x is not None and x.height]
    if not parts:
        return None
    return pl.concat(parts, how="diagonal_relaxed").sort(["TRADE_DATE", "SNAP_TIME", KEY])


def _jv(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    return str(v)


def search(client: str, d1: dt.date, d2: Optional[dt.date] = None, times: Optional[List[str]] = None) -> dict:
    t0 = time.time()
    rows = _pick(d1, d2, times)
    if not rows:
        return {"ok": True, "client": client, "snapshots": 0, "rows": [], "columns": [], "ms": 0,
                "note": "nothing stored for this date - use Store today now, Load history or Upload"}
    df = _client_frame(client, rows)
    qs = client_list(client)
    if df is None:
        return {"ok": True, "client": client, "clients": qs, "missing": qs, "snapshots": len(rows), "rows": [], "columns": [],
                "ms": int((time.time() - t0) * 1000),
                "note": f"{'client ' + qs[0] + ' is' if len(qs) == 1 else 'none of the ' + str(len(qs)) + ' clients is'} in the {len(rows)} snapshot(s) of these dates"}
    have = {str(x).upper() for x in df[KEY].to_list()}
    havez = {h.lstrip("0") for h in have}
    missing = [q for q in qs if q not in have and q.lstrip("0") not in havez]
    cols = [c for c in df.columns if c not in EXTRA]
    order = [KEY] + [c for c in KEY_FIELDS if c in cols] + [c for c in cols if c != KEY and c not in KEY_FIELDS]
    out = []
    for r in df.iter_rows(named=True):
        out.append({"date": r["TRADE_DATE"].isoformat() if r["TRADE_DATE"] else "", "time": r["SNAP_TIME"], "source": r["SOURCE"],
                    "data": {c: _jv(r.get(c)) for c in order}})
    return {"ok": True, "client": client, "clients": qs, "missing": missing, "snapshots": len(rows), "rows": out,
            "columns": order, "ms": int((time.time() - t0) * 1000)}


def client_excel(client: str, d1: dt.date, d2: Optional[dt.date] = None, times: Optional[List[str]] = None) -> Path:
    rows = _pick(d1, d2, times)
    df = _client_frame(client, rows) if rows else None
    if df is None:
        raise FileNotFoundError("no rows")
    import polars as pl
    df = df.with_columns(pl.col("TRADE_DATE").dt.strftime("%d-%m-%Y"))
    tmp = data_dir() / "tmp"
    tmp.mkdir(exist_ok=True)
    _clean_tmp(tmp)
    qs = client_list(client)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", qs[0])[:30] if len(qs) == 1 else f"{len(qs)}_CLIENTS"
    out = tmp / f"UPDATE_LIMITS_{safe}_{d1:%d%m%Y}{'_' + (d2 or d1).strftime('%d%m%Y') if d2 and d2 != d1 else ''}_{int(time.time())}.xlsx"
    df.write_excel(out, worksheet="Update Limits", autofit=False, freeze_panes=(1, 3))
    return out


def full_file(sid: str, fmt: str = "orig") -> Path:
    e = next((x for x in index() if x["id"] == sid), None)
    if not e:
        raise FileNotFoundError("snapshot not found")
    if fmt == "orig":
        p = Path(e.get("src_path") or "")
        if p.is_file():
            return p
        raise FileNotFoundError("the original file is not there any more - use CSV")
    if fmt == "parquet":
        return data_dir() / e["parquet"]
    import polars as pl
    tmp = data_dir() / "tmp"
    tmp.mkdir(exist_ok=True)
    _clean_tmp(tmp)
    d = dt.date.fromisoformat(e["date"])
    base = f"Update Limits{d:%d%m%Y}_{e['time'].replace(':', '')}"
    csv = tmp / f"{base}.csv"
    pl.read_parquet(data_dir() / e["parquet"]).drop(EXTRA).write_csv(csv)
    z = tmp / f"{base}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
        zf.write(csv, csv.name)
    csv.unlink(missing_ok=True)
    return z


def _clean_tmp(tmp: Path, older_s: int = 3600):
    for f in tmp.iterdir():
        try:
            if time.time() - f.stat().st_mtime > older_s:
                f.unlink()
        except OSError:
            pass


# ───────────────────────────── daily auto store ─────────────────────────────
def _hm(s: str) -> int:
    t = parse_time(s) or "18:00:00"
    return int(t[:2]) * 60 + int(t[3:5])


def auto_tick(now: Optional[dt.datetime] = None) -> Optional[str]:
    s = settings()
    if not s.get("auto"):
        return None
    now = now or dt.datetime.now()
    if now.hour * 60 + now.minute < _hm(s.get("time") or "18:00") or s.get("last_auto") == now.date().isoformat():
        return None
    save_settings({"last_auto": now.date().isoformat()})
    days = [now.date() - dt.timedelta(days=i) for i in range(5, -1, -1)]          # today + the 5 days before (a missed day)
    r = enqueue_days(days, "auto")
    cleanup_uploads()
    log(f"Auto store {now:%d-%m-%Y %H:%M}: {r['queued']} new file(s) of {r['found']} found.")
    return f"queued {r['queued']}"


def store_today(by: str = "") -> dict:
    return enqueue_days([dt.date.today()], "today", by=by)


def start_ticker(interval_s: int = 60):
    th = _TICK.get("t")
    if th and th.is_alive():
        return False
    ev = threading.Event()

    def loop():
        while not ev.wait(interval_s):
            try:
                auto_tick()
            except Exception as ex:                                              # noqa: BLE001
                print(f"[update-limits] timer error: {type(ex).__name__}: {ex}", flush=True)
    th = threading.Thread(target=loop, name="update-limits-timer", daemon=True)
    _TICK.update(t=th, stop=ev)
    th.start()
    return True


def status() -> dict:
    s = settings()
    return {"ok": True, **summary(), "job": job(), "settings": {"auto": s["auto"], "time": s["time"], "ctcl_root": s["ctcl_root"],
            "ctcl_root_used": str(ctcl_backup_root()), "last_auto": s.get("last_auto", "")}, "folder": str(data_dir())}
