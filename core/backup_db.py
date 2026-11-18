"""core/backup_db.py - when a day folder is NOT in the local backup, look in the database (PostgreSQL run_files) and bring it back.

Several processes copy every backed-up file into PostgreSQL as well (save_run_file: CTCL vs NMASS, CLI, Margin Shortage,
Short Allocation, LIVE ALLOCATION, MCX CASH MAINTENANCE, Probable ...). If the local day folder is gone or was never on this
machine, `restore_day` writes those files back into the local backup folder (<root>\\<dd-mm-yyyy>\\<run>\\<file>), after which every
reader - card, date range, zip, report - sees them exactly as if they had always been there.

Only when USE_POSTGRES=true. Never raises (a database problem is logged and the local answer stays "nothing there").
A day that has nothing in the database is not asked again for CACHE_S seconds, so a page that lists many days stays fast.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
from pathlib import Path

CACHE_S = int(os.getenv("BACKUP_DB_CACHE_S", "600") or 600)
# process key -> the pipeline names its files are stored under in run_files
DB_PIPES = {
    "ctcl": ["ctcl_vs_nmass", "ctcl"], "cli": ["cli"], "ms15": ["margin_shortage", "ms15"], "sa": ["short_allocation", "sa"],
    "liveallocation": ["liveallocation"], "mcxcash": ["mcxcash"], "probable": ["probable"], "reconciliation": ["reconciliation"],
}
_EMPTY: dict = {}                                    # (pipe, iso date) -> time of the last empty answer


def enabled() -> bool:
    return os.getenv("USE_POSTGRES", "false").strip().lower() == "true"


def _db():
    from core import db_postgres
    return db_postgres


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", os.path.basename(name or "")).strip() or "file"


def _run_part(tag: str) -> str:
    """'<date>/<run-or-slot>' -> '<run-or-slot>' ; a bare date or bare run number is handled too ('' = straight in the day folder)."""
    tag = str(tag or "").strip().replace("\\", "/")
    parts = [p for p in tag.split("/") if p]
    if not parts:
        return ""
    if re.fullmatch(r"\d{2}[-.]\d{2}[-.]\d{4}|\d{4}-\d{2}-\d{2}", parts[0]):
        parts = parts[1:]
    elif len(parts) == 1 and not re.fullmatch(r"\d{1,2}", parts[0]):
        return ""
    return _safe("/".join(parts)).replace("/", "_") if parts else ""


def day_files(pipe: str, d: dt.date) -> list:
    """[{id, name, run, size, pipeline}] stored for that day (all names the process is stored under). [] when off / none / error."""
    if not enabled():
        return []
    out = []
    try:
        db = _db()
        for p in DB_PIPES.get(pipe, [pipe]):
            for r in db.list_run_files(pipeline=p, run_date=d.isoformat(), days=36500):
                if r.get("filename"):
                    out.append({"id": r["id"], "name": r["filename"], "run": _run_part(r.get("tag")), "size": r.get("size_bytes") or 0, "pipeline": p})
    except Exception as e:                           # noqa: BLE001
        print(f"[backup_db] database lookup skipped: {type(e).__name__}: {e}", flush=True)
        return []
    return out


def restore_day(pipe: str, root, name: str, d: dt.date = None, now=time.time) -> int:
    """Bring that day's files back from the database into <root>\\<name>\\... . Returns how many files were written (0 = nothing)."""
    if not enabled():
        return 0
    if d is None:
        from core import backup_archive
        d = backup_archive.parse_day(name)
    if d is None:
        return 0
    if d >= dt.date.today():
        return 0                                     # today is never restored: it is being written right now
    key = (pipe, d.isoformat())
    if now() - _EMPTY.get(key, 0) < CACHE_S:
        return 0
    rows = day_files(pipe, d)
    if not rows:
        _EMPTY[key] = now()
        return 0
    base = Path(root) / name
    written = 0
    db = _db()
    for r in rows:
        dest_dir = base / r["run"] if r["run"] else base
        dest = dest_dir / _safe(r["name"])
        try:
            if dest.is_file() and dest.stat().st_size == r["size"]:
                continue
            got = db.get_run_file(int(r["id"]))
            if not got:
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(got[1])
            os.replace(tmp, dest)
            written += 1
        except Exception as e:                       # noqa: BLE001
            print(f"[backup_db] could not restore {r['name']}: {type(e).__name__}: {e}", flush=True)
    if written:
        print(f"[backup_db] {pipe} {d:%d-%m-%Y}: {written} file(s) brought back from the database into {base}", flush=True)
    return written


def day_dir(pipe: str, root, name: str) -> Path:
    """backup_archive.day_dir, and when the day is not on disk (directly or in a month folder) it is restored from the database first."""
    from core import backup_archive
    p = backup_archive.day_dir(root, name)
    if p.is_dir() and any(p.iterdir()):
        return p
    try:
        if restore_day(pipe, root, name):
            return backup_archive.day_dir(root, name)
    except Exception as e:                           # noqa: BLE001
        print(f"[backup_db] {type(e).__name__}: {e}", flush=True)
    return p


_DATES: dict = {}                                    # pipe -> (time, [dates])


def dates(pipe: str, now=time.time) -> list:
    """Every day this process has files for in the database (so a date range / a date list also shows days that are only there). Cached."""
    if not enabled():
        return []
    hit = _DATES.get(pipe)
    if hit and now() - hit[0] < CACHE_S:
        return list(hit[1])
    out = set()
    try:
        db = _db()
        for p in DB_PIPES.get(pipe, [pipe]):
            for r in db.list_run_files(pipeline=p, days=36500):
                if r.get("run_date"):
                    out.add(r["run_date"])
    except Exception as e:                           # noqa: BLE001
        print(f"[backup_db] database date list skipped: {type(e).__name__}: {e}", flush=True)
    res = sorted(out)
    _DATES[pipe] = (now(), res)
    return res
