"""
file_freshness.py — "is this downloaded file recent enough to use?"

WHY THIS EXISTS
───────────────
Every readiness check in this console used to ask only "does the file exist?".
That is not the same question as "is it safe to process", and the gap showed up
in a real run: at 14:29 the Client Prudential Limit download saved collateral
files, and at 14:55 a CTCL vs NMASS run found them sitting there, skipped its own
download, and processed 26-minute-old data. Nothing was corrupt — the files were
genuine and from that day — but nothing warned that they were stale either, and
the same logic would just as happily let a 15:15 run process a 09:30 file.

For a margin/collateral process, "today's file" is far too loose. These are the
agreed limits:

    CM / FO / CO collaterals   ≤ 4 minutes    stamp is IN THE FILENAME
    ClientNetEffectiveDeposits ≤ 4 minutes    no stamp — modified time
    MCX 90%                    ≤ 45 minutes   stamp is IN THE FILENAME
    Update Limits              (no age rule — updated manually, not downloaded)

TWO FILENAME STAMP FORMATS, DELIBERATELY KEPT SEPARATE
──────────────────────────────────────────────────────
    CM_ClientLevelCollaterals_29072026142913.csv
                              └─DDMMYYYY┘└HHMMSS┘     → 29/07/2026 14:29:13

    alkNAVlSFN-20260729145211.xlsx
               └─YYYYMMDD─┘└HHMMSS┘                    → 29/07/2026 14:52:11
    (the leading part is random — TCS BANCS generates it per export)

Note the day/year order is REVERSED between them. Parsing one with the other's
pattern silently yields a wrong-but-plausible date, so they get separate
functions rather than one clever regex.

WHY THE FILENAME BEATS MODIFIED TIME
────────────────────────────────────
The stamp is the exchange's own "as of" time — when the data was generated.
Modified time only records when the file landed on this disk, which a copy, a
restore, a backup job or moving it between folders will all change without the
DATA being any newer. Where a stamp exists it wins; modified time is the
fallback for files that have none.
"""

import datetime
import os
import re

# CM_ClientLevelCollaterals_29072026142913.csv  → DD MM YYYY HH MM SS
_COLLATERAL_STAMP = re.compile(r"_(\d{2})(\d{2})(\d{4})(\d{2})(\d{2})(\d{2})")
# alkNAVlSFN-20260729145211.xlsx                → YYYY MM DD HH MM SS
_MCX_STAMP = re.compile(r"(20\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")


def _env_float(key, default):
    try:
        return float(os.getenv(key, "") or default)
    except Exception:
        return float(default)


def max_age_collateral():
    return _env_float("NMASS_MAX_FILE_AGE_MIN", 4)


def max_age_mcx90():
    return _env_float("MCX90_MAX_FILE_AGE_MIN", 45)


def collateral_stamp(fname):
    """DDMMYYYYHHMMSS out of a collateral filename → datetime, or None."""
    m = _COLLATERAL_STAMP.search(os.path.basename(fname or ""))
    if not m:
        return None
    dd, mm, yyyy, hh, mi, ss = (int(x) for x in m.groups())
    try:
        return datetime.datetime(yyyy, mm, dd, hh, mi, ss)
    except ValueError:
        return None


def mcx90_stamp(fname):
    """YYYYMMDDHHMMSS out of an MCX 90% export filename → datetime, or None."""
    m = _MCX_STAMP.search(os.path.basename(fname or ""))
    if not m:
        return None
    yyyy, mm, dd, hh, mi, ss = (int(x) for x in m.groups())
    try:
        return datetime.datetime(yyyy, mm, dd, hh, mi, ss)
    except ValueError:
        return None


def _mtime(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path))
    except Exception:
        return None


def file_age_minutes(path, kind="collateral"):
    """Age in minutes, and where that came from → (age_or_None, "in filename"|"file time").

    kind: "collateral" | "mcx90" | "mtime"
    """
    when, src = None, "file time"
    if kind == "collateral":
        when = collateral_stamp(path)
    elif kind == "mcx90":
        when = mcx90_stamp(path)
    if when is not None:
        src = "in filename"
    else:
        when = _mtime(path)
    if when is None:
        return None, src
    return (datetime.datetime.now() - when).total_seconds() / 60.0, src


def check(path, kind="collateral", max_age_min=None):
    """{"ok","age_min","source","name"} for one file.

    A stamp dated in the FUTURE is treated as NOT fresh. That means a clock is
    wrong somewhere, and re-downloading is the safe response — silently trusting
    a file we can't date correctly is exactly what this module exists to stop.
    """
    if max_age_min is None:
        max_age_min = max_age_mcx90() if kind == "mcx90" else max_age_collateral()
    name = os.path.basename(path or "")
    if not path or not os.path.exists(path):
        return {"ok": False, "age_min": None, "source": None, "name": name,
                "why": "not present"}
    age, src = file_age_minutes(path, kind)
    if age is None:
        return {"ok": False, "age_min": None, "source": src, "name": name,
                "why": "could not determine age"}
    ok = 0 <= age <= max_age_min
    why = "" if ok else (
        f"{age:.1f} min old — older than the {max_age_min:.0f} min limit"
        if age > max_age_min else "timestamp is in the future — check the clock"
    )
    return {"ok": ok, "age_min": round(age, 1), "source": src, "name": name,
            "why": why, "max_age_min": max_age_min}


def newest(folder, pattern_fn, kind="collateral"):
    """Newest matching file in `folder` (by stamp where present, else mtime)."""
    try:
        cands = [f for f in os.listdir(folder) if pattern_fn(f)]
    except Exception:
        return None
    if not cands:
        return None

    def _key(f):
        p = os.path.join(folder, f)
        when = collateral_stamp(f) if kind == "collateral" else (
            mcx90_stamp(f) if kind == "mcx90" else None)
        return when or _mtime(p) or datetime.datetime.min

    return os.path.join(folder, max(cands, key=_key))
