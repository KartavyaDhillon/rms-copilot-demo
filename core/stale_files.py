"""Remove stale / duplicate input files from a pipeline folder BEFORE a run, so a run only ever sees (and later backs up)
the one file it really uses.

Why: a download that finished 10 minutes before a run, followed by a fresh download at run time, leaves TWO files of the
same kind in the folder (CM_ClientLevelCollaterals_<DDMMYYYY>2049.csv and ..._2233.csv). The run picked one of them and
BOTH went into the backup, so nobody could tell which one was used.

Rules (shared by CTCL vs NMASS and Margin Shortage 15-min):
  * For every input kind, the NEWEST file that carries TODAY's date is kept; every other file of that kind in the
    folder (older downloads from earlier today, files of earlier days) is deleted and logged.
  * If a kind has no today's file at all, nothing of that kind is touched (the pre-run check then reports it, and the
    operator can still see what is lying there).
  * MCX files: the 90% file ("Client Code" + "Client Margin Limit") and the Deposit file ("UCC Code" + "Margin Limit")
    are two kinds. The newest of each kind is kept - the one NOT selected for the run is not deleted (it is
    downloaded on purpose); it is only marked NOT_USED in the backup (see mark_unused_name).
  * RISK_REPORT in the shared common folder is never touched (only the copy inside the pipeline folder).
"""
from __future__ import annotations

import os
import re
from datetime import datetime

NOT_USED_PREFIX = "NOT_USED__"
_MCX_STAMPED = re.compile(r"-\d{14}\.xlsx$", re.I)            # TCS BANCS export: <random>-YYYYMMDDHHMMSS.xlsx
_MCX_OWN_DEPOSIT = "MCX_DEPOSIT_"                              # console's own download: MCX_DEPOSIT_HH.MM.xlsx
MCX_KINDS = {
    "mcx90":   ["Client Code", "Client Margin Limit"],
    "deposit": ["UCC Code", "Margin Limit"],
}


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip()


def has_columns(path: str, required) -> bool:
    """True when one of the first rows of the sheet holds every required header."""
    try:
        import pandas as pd
        raw = pd.read_excel(path, header=None, nrows=15)
    except Exception:
        return False
    need = {_norm(c) for c in required}
    return any(need.issubset({_norm(v) for v in raw.iloc[i].tolist()}) for i in range(len(raw)))


def _mdate(path: str):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).date()
    except OSError:
        return None


def _delete(path: str, why: str, log, deleted: list) -> None:
    try:
        os.remove(path)
        deleted.append(os.path.basename(path))
        log(f"🧹 Removed stale file: {os.path.basename(path)}  ({why})")
    except OSError as e:
        log(f"⚠️ Could not remove stale file {os.path.basename(path)}: {e}")


def purge_family(folder: str, prefix: str, exts, today_ddmmyyyy: str, log=print) -> list:
    """Keep only the newest TODAY-dated file whose name starts with `prefix`; delete the other files of that family.
    Returns the list of deleted file names."""
    deleted: list = []
    try:
        names = [f for f in os.listdir(folder)
                 if f.startswith(prefix) and f.lower().endswith(tuple(exts)) and os.path.isfile(os.path.join(folder, f))]
    except OSError:
        return deleted
    todays = [f for f in names if today_ddmmyyyy in f]
    if not todays:
        return deleted                                          # nothing valid for today: leave everything visible
    keep = max(todays, key=lambda f: os.path.getmtime(os.path.join(folder, f)))
    for f in names:
        if f != keep:
            _delete(os.path.join(folder, f), f"older than {keep}", log, deleted)
    return deleted


def purge_mcx(folder: str, today_yyyymmdd: str, log=print, exclude=()) -> dict:
    """Same rule for the two MCX kinds. Returns {"mcx90": newest path|None, "deposit": newest path|None, "deleted": [...]}."""
    result = {"mcx90": None, "deposit": None, "deleted": []}
    today = datetime.now().date()
    ex = [e.lower() for e in exclude]
    try:
        names = [f for f in os.listdir(folder)
                 if f.lower().endswith(".xlsx") and os.path.isfile(os.path.join(folder, f))
                 and (_MCX_STAMPED.search(f) or f.upper().startswith(_MCX_OWN_DEPOSIT))
                 and not any(x in f.lower() for x in ex)]
    except OSError:
        return result
    by_kind: dict = {k: [] for k in MCX_KINDS}
    for f in names:
        p = os.path.join(folder, f)
        for kind, cols in MCX_KINDS.items():
            if has_columns(p, cols):
                by_kind[kind].append(p)
                break
    for kind, paths in by_kind.items():
        def _is_today(p):
            n = os.path.basename(p)
            return today_yyyymmdd in n or (n.upper().startswith(_MCX_OWN_DEPOSIT) and _mdate(p) == today)
        todays = [p for p in paths if _is_today(p)]
        if not todays:
            continue
        keep = max(todays, key=os.path.getmtime)
        result[kind] = keep
        for p in paths:
            if p != keep:
                _delete(p, f"older MCX {kind} file, newer is {os.path.basename(keep)}", log, result["deleted"])
    return result


def mark_unused_name(fname: str, unused_names) -> str:
    """Backup name of a file: the downloaded-but-not-used MCX file gets the NOT_USED__ prefix."""
    return NOT_USED_PREFIX + fname if fname in set(unused_names or ()) else fname


def write_used_manifest(run_folder: str, used: dict, unused) -> None:
    """USED_FILES.txt inside the backup run folder: what the run used and what was only downloaded."""
    try:
        lines = [f"Run files manifest - written {datetime.now():%d-%m-%Y %H:%M:%S}", "", "USED by this run:"]
        lines += [f"   {k}: {os.path.basename(v)}" for k, v in used.items() if v]
        lines += ["", "Downloaded but NOT USED (stored with the prefix " + NOT_USED_PREFIX + "):"]
        lines += [f"   {n}" for n in unused] or ["   (none)"]
        with open(os.path.join(run_folder, "USED_FILES.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass
