"""core/backup_to_db.py - copy EVERY process's backed-up files into PostgreSQL (run_files, kind "backup").

Covers: every Process Center card backup folder (pipeline_box.PIPELINES), every folder named like "BACKUP FILES [AUTOMATED]" /
"BACKUP AUTOMATION" under the data root, and every scheduler process (<sched_root>/<process>/backup/<dd-mm-yyyy>).
Tag convention (same as the pipelines that already save): "<dd-mm-yyyy>/<run folder>"  -> core.backup_db.restore_day brings it back.
A file already in the database (same pipeline + day + name + size) is never stored twice. Only when USE_POSTGRES=true. Never raises.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from pathlib import Path

from core import backup_archive as BA
from core import backup_db

MAX_MB = int(os.getenv("BACKUP_DB_MAX_MB", "150") or 150)
EVERY_S = int(os.getenv("BACKUP_DB_EVERY_S", "1800") or 1800)
RECENT_DAYS = int(os.getenv("BACKUP_DB_RECENT_DAYS", "3") or 3)
SKIP_EXT = {".part", ".tmp", ".lock", ".crdownload", ".pyc"}
AUTOMATION_RE = re.compile(r"^backup\s*automation$", re.I)
_LOCK = threading.Lock()
_TICK = {"thread": None, "stop": threading.Event()}
_LAST: dict = {}


class Busy(RuntimeError):
    pass


def enabled() -> bool:
    return backup_db.enabled()


def _root() -> Path:
    from core.scheduler import paths
    return paths.data_root()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "process"


def _state_file() -> Path:
    p = _root() / "_backup_db"
    p.mkdir(parents=True, exist_ok=True)
    return p / "state.json"


def _load() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:                                                     # noqa: BLE001
        return {}


def _save(d: dict) -> None:
    try:
        _state_file().write_text(json.dumps(d), encoding="utf-8")
    except Exception as e:                                                # noqa: BLE001
        print(f"[backup_to_db] state not saved: {e}", flush=True)


def sources() -> list:
    """[(pipeline name, root path)] - every backup folder whose day folders must be copied to the database."""
    out, seen = [], set()

    def add(name, root):
        k = os.path.normcase(os.path.abspath(str(root)))
        if k in seen or not os.path.isdir(root):
            return
        seen.add(k)
        out.append((name, str(root)))

    try:                                                                  # 1. Process Center cards
        from core import pipeline_box as PB
        for pipe in list(PB.PIPELINES):
            try:
                for r in PB._pb_backup_roots(pipe):
                    add(backup_db.DB_PIPES.get(pipe, [pipe])[0], r)
            except Exception:                                             # noqa: BLE001
                continue
    except Exception as e:                                                # noqa: BLE001
        print(f"[backup_to_db] cards not listed: {type(e).__name__}: {e}", flush=True)
    try:                                                                  # 2. BACKUP FILES [AUTOMATED] folders anywhere under the data root
        for r in BA.find_roots(_root()):
            add(_slug(Path(r).parent.name), r)
    except Exception as e:                                                # noqa: BLE001
        print(f"[backup_to_db] roots not listed: {e}", flush=True)
    try:                                                                  # 3. BACKUP AUTOMATION folders (payout / cnc cards)
        base = _root()
        for dp, dn, _ in os.walk(base):
            if dp[len(str(base)):].count(os.sep) >= 4:
                dn[:] = []
                continue
            for n in list(dn):
                if AUTOMATION_RE.match(n):
                    add(_slug(Path(dp).name), os.path.join(dp, n))
                    dn.remove(n)
    except Exception as e:                                                # noqa: BLE001
        print(f"[backup_to_db] automation folders not listed: {e}", flush=True)
    try:                                                                  # 4. scheduler processes
        from core.scheduler import paths
        sr = paths.sched_root()
        if sr.is_dir():
            for p in sorted(sr.iterdir()):
                if p.is_dir() and not p.name.startswith(("_", ".")):
                    add("sched_" + _slug(p.name), p / "backup")
    except Exception as e:                                                # noqa: BLE001
        print(f"[backup_to_db] scheduler not listed: {e}", flush=True)
    return out


def _existing(db, pipeline: str, d: dt.date, cache: dict) -> set:
    k = (pipeline, d)
    if k not in cache:
        try:
            rows = db.list_run_files(pipeline=pipeline, run_date=d.isoformat(), days=36500)
            cache[k] = {(r.get("filename"), int(r.get("size_bytes") or 0)) for r in rows}
        except Exception as e:                                            # noqa: BLE001
            print(f"[backup_to_db] lookup failed {pipeline} {d}: {e}", flush=True)
            cache[k] = None
    return cache[k]


def archive_day(pipeline: str, day_path: str, d: dt.date, state: dict, cache: dict, res: dict, db=None) -> None:
    db = db or backup_db._db()
    done = state.setdefault(pipeline, {})
    day_tag = f"{d:%d-%m-%Y}"
    for dp, dn, fn in os.walk(day_path):
        rel = os.path.relpath(dp, day_path)
        sub = "" if rel == "." else rel.replace("\\", "/")
        for name in fn:
            if name.startswith((".", "~$")) or os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            fp = os.path.join(dp, name)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            res["seen"] += 1
            if time.time() - st.st_mtime < 60:
                continue                                                  # still being written
            if st.st_size > MAX_MB * 1024 * 1024:
                res["too_big"] += 1
                continue
            key = f"{day_tag}/{sub}/{name}"
            sig = f"{st.st_size}:{int(st.st_mtime)}"
            if done.get(key) == sig:
                continue
            have = _existing(db, pipeline, d, cache)
            if have is None:
                res["errors"] += 1
                continue
            if (name, st.st_size) in have:
                done[key] = sig
                res["already"] += 1
                continue
            try:
                with open(fp, "rb") as f:
                    data = f.read()
                db.save_run_file(pipeline, "backup", name, data, f"{day_tag}/{sub}" if sub else day_tag, d.isoformat())
                have.add((name, st.st_size))
                done[key] = sig
                res["stored"] += 1
            except Exception as e:                                        # noqa: BLE001
                res["errors"] += 1
                print(f"[backup_to_db] {pipeline} {name}: {type(e).__name__}: {e}", flush=True)


def scan(recent_days: int | None = None, today: dt.date | None = None) -> dict:
    """Copy new backup files to the database. recent_days=None -> every day folder (backfill); else only the last N days."""
    if not enabled():
        return {"ok": False, "error": "USE_POSTGRES is not true"}
    if not _LOCK.acquire(blocking=False):
        raise Busy("a database backup pass is already running")
    t0 = time.time()
    res = {"seen": 0, "stored": 0, "already": 0, "too_big": 0, "errors": 0, "sources": 0}
    try:
        today = today or dt.date.today()
        state, cache = _load(), {}
        db = backup_db._db()
        srcs = sources()
        res["sources"] = len(srcs)
        for pipeline, root in srcs:
            for name, path in BA.iter_day_dirs(root):
                d = BA.parse_day(name)
                if d is None or d > today:
                    continue
                if recent_days is not None and (today - d).days > recent_days:
                    continue
                try:
                    archive_day(pipeline, path, d, state, cache, res, db)
                except Exception as e:                                    # noqa: BLE001
                    res["errors"] += 1
                    print(f"[backup_to_db] {pipeline} {name}: {type(e).__name__}: {e}", flush=True)
            _save(state)
        res["ok"] = True
        res["seconds"] = round(time.time() - t0, 1)
        res["at"] = dt.datetime.now().isoformat(timespec="seconds")
        res["mode"] = "all days" if recent_days is None else f"last {recent_days} days"
        _LAST.clear()
        _LAST.update(res)
        if res["stored"]:
            print(f"[backup_to_db] {res['stored']} file(s) stored in the database ({res['mode']})", flush=True)
        return res
    finally:
        _LOCK.release()


def status() -> dict:
    return {"enabled": enabled(), "running": _LOCK.locked(), "every_s": EVERY_S, "last": dict(_LAST),
            "sources": [{"pipeline": p, "root": r} for p, r in sources()] if enabled() else []}


def _loop():
    first = True
    while not _TICK["stop"].wait(120 if first else EVERY_S):
        try:
            if enabled():
                scan(None if first else RECENT_DAYS)                      # first pass after start = full backfill
                first = False
        except Busy:
            pass
        except Exception as e:                                            # noqa: BLE001
            print(f"[backup_to_db] {type(e).__name__}: {e}", flush=True)


def start_ticker() -> None:
    t = _TICK["thread"]
    if t and t.is_alive():
        return
    _TICK["stop"].clear()
    t = threading.Thread(target=_loop, name="backup-to-db", daemon=True)
    _TICK["thread"] = t
    t.start()
