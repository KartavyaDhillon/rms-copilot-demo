"""Market-holiday calendar for weekday-style schedules.

Reads the SAME list the RECONCILIATION box maintains (holidays_list.csv, first column,
DDMMYYYY) so a holiday is entered once.  Override with SCHED_HOLIDAYS_FILE in .env.
Weekends are handled by the schedule's own weekday selection, not here.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
from pathlib import Path
from typing import Optional

_CACHE = {"path": None, "mtime": None, "days": set()}


def holiday_file() -> Optional[Path]:
    v = os.getenv("SCHED_HOLIDAYS_FILE", "").strip()
    if v:
        return Path(v)
    try:
        from core.pipelines.reconciliation import holidays_file
        return Path(holidays_file())
    except Exception:
        return None


def _parse(raw: str) -> Optional[dt.date]:
    raw = raw.strip().strip('"').strip("'")
    if not raw or raw.lower() == "date":
        return None
    if raw.isdigit() and len(raw) == 7:
        raw = raw.zfill(8)
    for fmt in ("%d%m%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def load() -> set:
    p = holiday_file()
    if not p or not p.exists():
        _CACHE.update({"path": None, "mtime": None, "days": set()})
        return set()
    try:
        m = p.stat().st_mtime
    except OSError:
        return _CACHE["days"]
    if _CACHE["path"] == str(p) and _CACHE["mtime"] == m:
        return _CACHE["days"]
    days = set()
    try:
        with open(p, "r", newline="", encoding="utf-8-sig") as fh:
            for row in csv.reader(fh):
                if row:
                    d = _parse(row[0])
                    if d:
                        days.add(d)
    except Exception:
        return _CACHE["days"]
    _CACHE.update({"path": str(p), "mtime": m, "days": days})
    return days


def is_holiday(d: dt.date) -> bool:
    return d in load()
