"""
Peak Margin pipeline - PURE LOGIC, with CUMULATIVE column-wise reporting.

Matches the user's real behaviour:
   i01 run -> fills Peak1 column   (10:30)
   i02 run -> fills Peak2 column   (12:30)
   i03 run -> fills Peak3 column   (13:45)
   i04 run -> fills Peak4 column   (15:30)
into the SAME persistent report, so by the last run all 4 columns are filled -
identical to the original F_filtered / C_filtered output.

NEW DAY = CLEAN SLATE: the report stores today's date. On the first run of a
new day, yesterday's report is discarded and a fresh, fully-blank report is
built (Peak1..Peak4 all 0, no leftover clients). So a new day starts with only
Peak1 being filled by the i01 run; Peak2/3/4 stay blank until their runs.

Re-running an index just overwrites its own column -> idempotent.

.env:
    PEAK_FO_CODES=JDA563,JDA937,JDA1209,JDA1210,JDA1260
    PEAK_CM_CODES=06436,PRO_06436,6436
    PEAK_FOLDER=C:\\Peak Margin
"""
import os
import json
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

FO_CODES = os.getenv("PEAK_FO_CODES", "").split(",") if os.getenv("PEAK_FO_CODES") else []
CM_CODES = os.getenv("PEAK_CM_CODES", "").split(",") if os.getenv("PEAK_CM_CODES") else []
FOLDER = os.getenv("PEAK_FOLDER", "Peak Margin")

COLS = ["Date", "Code", "Peak1", "Peak2", "Peak3", "Peak4", "Other"]
INDEX_TO_PEAK = {"i01": "Peak1", "i02": "Peak2", "i03": "Peak3", "i04": "Peak4"}
PEAK_COLS = ["Peak1", "Peak2", "Peak3", "Peak4"]


def _state_path():
    return os.path.join(FOLDER, "peak_margin_report.json")


def _empty_report(today):
    """A fresh, fully-blank report for `today`: every configured client and
    both segments start with all four peak columns at 0."""
    fo = {code: {p: 0.0 for p in PEAK_COLS} for code in FO_CODES}
    seg = lambda: {"Code": None, **{p: 0.0 for p in PEAK_COLS}}
    return {"date": today, "fo_clients": fo, "cm_segment": seg(), "fo_segment": seg()}


def _load_report(today):
    """Return today's report. If the stored report is from a previous day
    (or missing/corrupt), START FRESH so yesterday's values never leak in."""
    path = _state_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                rep = json.load(f)
            if rep.get("date") == today:
                return rep          # same day -> keep accumulating
            # different day -> fall through to a clean slate
        except (json.JSONDecodeError, OSError):
            pass
    return _empty_report(today)      # new day / first run / corrupt -> blank


def _save_report(rep):
    os.makedirs(FOLDER, exist_ok=True)
    with open(_state_path(), "w") as f:
        json.dump(rep, f, indent=2)


def reset_day(today):
    """Start a brand-new blank report for today (used by the i01 run).
    Wipes any leftover report/run-log so the day starts clean."""
    rep = _empty_report(today)
    _save_report(rep)
    # also clear the run-log so the counter restarts at 0
    runlog = os.path.join(FOLDER, "peak_runs.json")
    if os.path.exists(runlog):
        try:
            os.remove(runlog)
        except OSError:
            pass
    return rep


def _val_for_code(path, codes):
    """Read one file; return (matched_code, Peak1 value) for the first matching code."""
    df = pd.read_excel(path, header=None)
    df.columns = COLS
    match = df[df["Code"].isin(codes)]
    if match.empty:
        return None, 0.0
    return match.iloc[0]["Code"], (pd.to_numeric(match.iloc[0]["Peak1"], errors="coerce") or 0.0)


def update_report_with_pair(index: str, fo_path: str, cm_path: str, today: str) -> dict:
    """Write THIS index's values into THIS index's column (Peak1..Peak4),
    leaving the other columns untouched. Returns the full updated report."""
    peak_col = INDEX_TO_PEAK[index]
    rep = _load_report(today)

    # FO clients table: each configured client's value for this snapshot.
    df = pd.read_excel(fo_path, header=None)
    df.columns = COLS
    for code in FO_CODES:
        m = df[df["Code"] == code]
        rep["fo_clients"].setdefault(code, {p: 0.0 for p in PEAK_COLS})
        rep["fo_clients"][code][peak_col] = (
            float(pd.to_numeric(m.iloc[0]["Peak1"], errors="coerce") or 0.0) if not m.empty else 0.0
        )

    # CM segment (from CM file) and FO segment (from FO file), matched by CM_CODES.
    cm_code, cm_val = _val_for_code(cm_path, CM_CODES)
    fo_code, fo_val = _val_for_code(fo_path, CM_CODES)
    if cm_code is not None:
        rep["cm_segment"]["Code"] = cm_code
    rep["cm_segment"][peak_col] = float(cm_val)
    if fo_code is not None:
        rep["fo_segment"]["Code"] = fo_code
    rep["fo_segment"][peak_col] = float(fo_val)

    _save_report(rep)
    return _with_totals(rep)


def _with_totals(rep):
    """Attach combined CM+FO totals per column."""
    totals = {col: rep["cm_segment"].get(col, 0.0) + rep["fo_segment"].get(col, 0.0)
              for col in PEAK_COLS}
    out = json.loads(json.dumps(rep))
    out["combined_totals"] = totals
    return out


def get_report(today: str) -> dict:
    """Return today's cumulative report (all columns filled so far)."""
    return _with_totals(_load_report(today))
