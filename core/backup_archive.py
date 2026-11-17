"""core/backup_archive.py - monthly order for every process BACKUP folder.

Every process keeps one folder per day inside  <data root>\\<process>\\BACKUPFILES AUTOMATED\\<dd-mm-yyyy>.
When a month is over, its day folders are moved (nothing is deleted, nothing is renamed) into a month folder:

    BACKUPFILES AUTOMATED\\OCT 2026\\01-10-2026 ... 30-10-2026

Rules
  * only COMPLETED months move; the running month is never touched;
  * a day folder newer than "keep last N days" (default 3) stays where it is, so a job that looks at "yesterday" across the
    month change still finds it; the leftovers move on the next daily pass, so the month folder fills up by itself;
  * the daily pass runs on weekdays at the set time (default 06:30) - the first weekday of a month (1st / 2nd / 3rd after a
    weekend) is the first one that finds a completed month; a manual "Archive now" does the same any time;
  * a day folder whose files are open (Windows lock) is skipped and reported, the rest still moves;
  * an unknown folder name (e.g. "02-04-2026---") is never touched.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
BACKUP_RE = re.compile(r"^backup\s*files(\s*automated)?$", re.I)
MONTH_RE = re.compile(r"^(%s) (\d{4})$" % "|".join(MONTHS))
_DAY_PATTERNS = ((re.compile(r"^(\d{2})-(\d{2})-(\d{4})$"), "dmy"), (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), "ymd"),
                 (re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$"), "dmy"), (re.compile(r"^(\d{2})_(\d{2})_(\d{4})$"), "dmy"),
                 (re.compile(r"^(\d{2})(\d{2})(\d{4})$"), "dmy"))
MAX_DEPTH = 4
DEFAULTS = {"enabled": True, "grace_days": 3, "run_time": "06:30"}

_LOCK = threading.Lock()
_STATE_LOCK = threading.RLock()
_TICK = {"thread": None, "stop": threading.Event()}


class Busy(Exception):
    pass


def data_root() -> Path:
    try:
        from core.env_boot import data_root as _dr
        return Path(_dr())
    except Exception:                                            # noqa: BLE001
        return Path(os.getenv("RMS_DATA_ROOT", Path(__file__).resolve().parent.parent / "data"))


def _state_file() -> Path:
    p = data_root() / "_backup_archive"
    p.mkdir(parents=True, exist_ok=True)
    return p / "state.json"


def _load() -> dict:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        d = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in d.items() if k in ("enabled", "grace_days", "run_time", "last_run", "last_result", "last_auto_date")})
    return out


def _save(d: dict) -> None:
    with _STATE_LOCK:
        f = _state_file()
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, f)


def parse_day(name: str):
    """date of a day-folder name (dd-mm-yyyy, yyyy-mm-dd, dd.mm.yyyy, dd_mm_yyyy, ddmmyyyy) or None."""
    for rx, order in _DAY_PATTERNS:
        m = rx.match(name)
        if not m:
            continue
        a, b, c = (int(x) for x in m.groups())
        try:
            return dt.date(c, b, a) if order == "dmy" else dt.date(a, b, c)
        except ValueError:
            return None
    return None


def month_name(d: dt.date) -> str:
    return f"{MONTHS[d.month - 1]} {d.year}"


def day_dir(root, name: str) -> Path:
    """The day folder wherever it is now: directly in the backup folder, or inside its month folder (OCT 2026)."""
    root = Path(root)
    p = root / name
    if p.is_dir():
        return p
    d = parse_day(name)
    if d:
        q = root / month_name(d) / name
        if q.is_dir():
            return q
    return p


def archived_days(root) -> list:
    """Names of the day folders that sit inside month folders of this backup folder."""
    out = []
    try:
        for m in Path(root).iterdir():
            if m.is_dir() and MONTH_RE.match(m.name):
                out += [c.name for c in m.iterdir() if c.is_dir()]
    except OSError:
        pass
    return out


def find_roots(base: Path | None = None) -> list:
    """Every folder named like a backup folder under the data root (and BACKUP_ARCHIVE_EXTRA, ';' separated)."""
    starts = [Path(base) if base else data_root()]
    if not base:
        for x in os.getenv("BACKUP_ARCHIVE_EXTRA", "").split(";"):
            if x.strip():
                starts.append(Path(x.strip()))
    found = []

    def walk(p: Path, depth: int):
        try:
            with os.scandir(p) as it:
                kids = [e for e in it if e.is_dir(follow_symlinks=False)]
        except OSError:
            return
        for e in sorted(kids, key=lambda x: x.name.lower()):
            if e.name.startswith("_") or e.name.startswith("."):
                continue
            if BACKUP_RE.match(e.name):
                found.append(Path(e.path))                        # a backup root: do not look inside
            elif depth < MAX_DEPTH and parse_day(e.name) is None and not MONTH_RE.match(e.name):
                walk(Path(e.path), depth + 1)

    for s in starts:
        if s.is_dir():
            walk(s, 1)
    seen, out = set(), []
    for r in found:
        k = str(r).lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def plan(today: dt.date | None = None, grace_days: int | None = None, base: Path | None = None) -> list:
    """[{root, month, folder, date, src, dst}] - the day folders that belong in a month folder now."""
    today = today or dt.date.today()
    grace = _load()["grace_days"] if grace_days is None else int(grace_days)
    items = []
    for root in find_roots(base):
        try:
            with os.scandir(root) as it:
                kids = [e for e in it if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for e in kids:
            d = parse_day(e.name)
            if d is None:
                continue
            if (d.year, d.month) >= (today.year, today.month):
                continue                                          # running month (or a future date): never moved
            if (today - d).days <= grace:
                continue                                          # still inside the keep-in-place window
            mname = month_name(d)
            items.append({"root": str(root), "month": mname, "folder": e.name, "date": d.isoformat(),
                          "src": e.path, "dst": str(root / mname / e.name)})
    items.sort(key=lambda x: (x["root"].lower(), x["date"]))
    return items


def _move(src: str, dst: str) -> str:
    """'moved' | 'merged' | raises. An existing destination is merged item by item, never overwritten."""
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return "moved"
    skipped = 0
    for dp, dns, fns in os.walk(src):
        rel = os.path.relpath(dp, src)
        tdir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(tdir, exist_ok=True)
        for fn in fns:
            t = os.path.join(tdir, fn)
            if os.path.exists(t):
                skipped += 1
                continue
            shutil.move(os.path.join(dp, fn), t)
    if skipped:
        raise FileExistsError(f"{skipped} file(s) already exist in {dst} - left in place")
    shutil.rmtree(src, ignore_errors=True)                        # only empty leftovers of an already-merged copy
    return "merged"


def preview(today: dt.date | None = None, grace_days: int | None = None) -> dict:
    items = plan(today, grace_days)
    months = {}
    for it in items:
        months.setdefault(f"{it['root']}|{it['month']}", {"root": it["root"], "month": it["month"], "days": 0})["days"] += 1
    return {"roots": [str(r) for r in find_roots()], "to_move": len(items), "months": list(months.values()),
            "items": items[:300]}


def run(manual: bool = False, today: dt.date | None = None, grace_days: int | None = None, base: Path | None = None, log=print) -> dict:
    """Move every completed-month day folder into its month folder. Returns the summary (also stored as last_result)."""
    if not _LOCK.acquire(blocking=False):
        raise Busy("an archive pass is already running")
    t0 = time.time()
    try:
        items = plan(today, grace_days, base)
        moved = failed = merged = 0
        errors, touched = [], set()
        for it in items:
            try:
                r = _move(it["src"], it["dst"])
                merged += r == "merged"
                moved += 1
                touched.add((it["root"], it["month"]))
                log(f"{r}: {it['folder']} -> {it['month']}")
            except Exception as e:                                # noqa: BLE001  (open file, no rights, name clash)
                failed += 1
                errors.append(f"{it['folder']} in {Path(it['root']).parent.name}: {type(e).__name__}: {e}"[:300])
                log(f"skipped {it['folder']}: {e}")
        res = {"at": dt.datetime.now().isoformat(timespec="seconds"), "manual": bool(manual), "roots": len(find_roots(base)),
               "found": len(items), "moved": moved, "merged": merged, "failed": failed,
               "months": sorted({f"{m}" for _r, m in touched}), "errors": errors[:20], "secs": round(time.time() - t0, 1)}
        st = _load()
        st["last_run"] = res["at"]
        st["last_result"] = res
        if not base:
            _save(st)
        return res
    finally:
        _LOCK.release()


def status() -> dict:
    st = _load()
    pv = preview()
    return {"enabled": bool(st["enabled"]), "grace_days": int(st["grace_days"]), "run_time": st["run_time"],
            "last_run": st.get("last_run"), "last_result": st.get("last_result"), "running": _LOCK.locked(),
            "pending": pv["to_move"], "pending_months": pv["months"], "roots": pv["roots"], "data_root": str(data_root())}


def save_settings(enabled=None, grace_days=None, run_time=None) -> dict:
    st = _load()
    if enabled is not None:
        st["enabled"] = bool(enabled)
    if grace_days is not None:
        g = int(grace_days)
        if not 0 <= g <= 31:
            raise ValueError("keep-last days must be 0 to 31")
        st["grace_days"] = g
    if run_time is not None:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(run_time)):
            raise ValueError("time must be HH:MM (24 hour)")
        st["run_time"] = str(run_time)
    _save(st)
    return status()


def tick(now: dt.datetime | None = None) -> bool:
    """One timer step: on a weekday, after the set time, once a day, run the archive pass. True when it ran."""
    now = now or dt.datetime.now()
    st = _load()
    if not st["enabled"] or now.weekday() >= 5:
        return False
    if st.get("last_auto_date") == now.date().isoformat():
        return False
    if now.strftime("%H:%M") < st["run_time"]:
        return False
    st["last_auto_date"] = now.date().isoformat()
    _save(st)
    try:
        run(manual=False, today=now.date())
    except Busy:
        return False
    return True


def _loop():
    while not _TICK["stop"].wait(60):
        try:
            tick()
        except Exception as e:                                    # noqa: BLE001
            print(f"[backup-archive] {type(e).__name__}: {e}", flush=True)


def start_ticker() -> None:
    t = _TICK["thread"]
    if t and t.is_alive():
        return
    _TICK["stop"].clear()
    t = threading.Thread(target=_loop, name="backup-archive-tick", daemon=True)
    _TICK["thread"] = t
    t.start()
