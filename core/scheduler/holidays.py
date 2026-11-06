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


# ───────────────────────── special trading days (e.g. Muhurat trading on a Sunday) ─────────────────────────
# A date listed here is a day the exchanges are OPEN although it is a weekend / listed holiday.  It never changes
# the schedules or the auto-run timers (those keep treating the day as closed); it only lets a MANUAL run
# ("Run now" / a Process Center button) work on that day.  File: special_trading_days.csv next to the holiday list
# (first column DDMMYYYY, an optional note in column 2).  Override the location with SCHED_SPECIAL_FILE in .env.
_SPECIAL = {"path": None, "mtime": None, "days": set()}


def special_file() -> Optional[Path]:
    v = os.getenv("SCHED_SPECIAL_FILE", "").strip()
    if v:
        return Path(v)
    p = holiday_file()
    return (p.parent / "special_trading_days.csv") if p else None


SEED_SPECIAL = ("08112026", "Muhurat trading (Diwali) - Sunday")     # created once, with the file, when it does not exist yet


def _write_special(rows: dict) -> None:
    p = special_file()
    if not p:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["Date,Note"] + [f"{d:%d%m%Y},{(n or '').replace(',', ' ')}" for d, n in sorted(rows.items())]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def list_special() -> dict:
    """{date: note} of the special trading days file (created with the Muhurat Sunday in it on first use)."""
    load_special()
    p = special_file()
    out = {}
    if p and p.exists():
        with open(p, "r", newline="", encoding="utf-8-sig") as fh:
            for row in csv.reader(fh):
                d = _parse(row[0]) if row else None
                if d:
                    out[d] = row[1].strip() if len(row) > 1 else ""
    return out


def add_special(d: dt.date, note: str = "") -> None:
    rows = list_special()
    rows[d] = note
    _write_special(rows)


def remove_special(d: dt.date) -> None:
    rows = list_special()
    rows.pop(d, None)
    _write_special(rows)


def load_special() -> set:
    p = special_file()
    if p and not p.exists():
        try:                                                  # first use: create the file with the Muhurat Sunday in it
            d0 = _parse(SEED_SPECIAL[0])
            _write_special({d0: SEED_SPECIAL[1]})
        except Exception:
            pass
    if not p or not p.exists():
        _SPECIAL.update({"path": None, "mtime": None, "days": set()})
        return set()
    try:
        m = p.stat().st_mtime
    except OSError:
        return _SPECIAL["days"]
    if _SPECIAL["path"] == str(p) and _SPECIAL["mtime"] == m:
        return _SPECIAL["days"]
    days = set()
    try:
        with open(p, "r", newline="", encoding="utf-8-sig") as fh:
            for row in csv.reader(fh):
                if row:
                    d = _parse(row[0])
                    if d:
                        days.add(d)
    except Exception:
        return _SPECIAL["days"]
    _SPECIAL.update({"path": str(p), "mtime": m, "days": days})
    return days


def is_special(d: dt.date) -> bool:
    return d in load_special()
