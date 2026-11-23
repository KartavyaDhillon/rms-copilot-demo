"""core/nmass_level1.py - the NMASS "Risk Management - FO > Margins > CM" LEVEL-1 row (one line for the whole member).

Replaces the full FO_ClientLevelCollaterals file in the OBLIGATION REPORT: the page's own numbers are fetched, not summed up.

    GET  <BASE>/nmass/risk-fo/cm-margins-summery-level-I/JSON:JSON      (same login / cookies / `ufp` header as the other NMASS files)

The saved file has the column names exactly as the NSE screen shows them:

    CM CODE | MEMBER NAME | CM CASH (A) | CM NON-CASH | EFFECTIVE NON-CASH (B) | EFFECTIVE COLLATERAL (C = A+B) |
    CM PROP MARGINS (E) | TM MARGINS > 90% (F) | TOTAL MARGINS UTILISED (G = E+F) | PERCENTAGE (H = (G*100)/(C-MNLW)) | TIMESTAMP

File name:  FO_Level1_Margins_<ddmmyyyy><hhmmss>.csv   (date + time = the portal's own "MARGIN SUMMARY @" time, so an old answer is
caught by the date check of the card).

No network and no console import in here - the NMASS script and the tests use it as it is.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import re
from typing import Dict, Optional

PATH = "/nmass/risk-fo/cm-margins-summery-level-I/JSON:JSON"
FILE_PREFIX = "FO_Level1_Margins_"
FILE_RX = re.compile(r"^FO_Level1_Margins_.*\.csv$", re.I)

# screen column -> field of the JSON answer
COLUMNS = [
    ("CM CODE", "cmCode"),
    ("MEMBER NAME", "cmName"),
    ("CM CASH (A)", "cash"),
    ("CM NON-CASH", "nonCash"),
    ("EFFECTIVE NON-CASH (B)", "effNonCash"),
    ("EFFECTIVE COLLATERAL (C = A+B)", "effectiveColl"),
    ("CM PROP MARGINS (E)", "cmPropMargin"),
    ("TM MARGINS > 90% (F)", "tmExcessMargin"),
    ("TOTAL MARGINS UTILISED (G = E+F)", "totMrgnUtilisation"),
    ("PERCENTAGE (H = (G*100)/(C-MNLW))", "cmUtilisation"),
]
TS_COLUMN = "TIMESTAMP"
NUMERIC = {"cash", "nonCash", "effNonCash", "effectiveColl", "cmPropMargin", "tmExcessMargin", "totMrgnUtilisation", "cmUtilisation"}

# Obligation picture: box name -> screen column
BOXES = {"fo_deposit": "EFFECTIVE COLLATERAL (C = A+B)", "fo_used": "TOTAL MARGINS UTILISED (G = E+F)",
         "fo_pro": "CM PROP MARGINS (E)", "fo_client": "TM MARGINS > 90% (F)"}


class Level1Error(RuntimeError):
    pass


def parse_timestamp(text: str) -> Optional[dt.datetime]:
    """'23 Nov 2026 13:23:10' -> datetime (None when it does not read)."""
    try:
        return dt.datetime.strptime((text or "").strip(), "%d %b %Y %H:%M:%S")
    except ValueError:
        return None


def parse(resp: dict) -> Dict[str, object]:
    """The JSON answer -> {screen column: value, 'TIMESTAMP': text}. Raises Level1Error on anything that is not the full row."""
    if not isinstance(resp, dict) or str(resp.get("status", "")).lower() != "success":
        raise Level1Error(f"NMASS did not return the Level-1 row: {str(resp)[:200]}")
    data = resp.get("data") or {}
    res = data.get("result")
    if isinstance(res, list):                                    # a list of one is the same row
        res = res[0] if len(res) == 1 else None
    if not isinstance(res, dict):
        raise Level1Error("NMASS returned no Level-1 row (empty result) - the portal had no margin data yet.")
    row: Dict[str, object] = {}
    for col, key in COLUMNS:
        if key not in res or res[key] is None:
            raise Level1Error(f"the Level-1 answer has no '{key}' ({col})")
        row[col] = float(res[key]) if key in NUMERIC else str(res[key])
    row[TS_COLUMN] = str(res.get("timeStamp") or data.get("timestamp") or "")
    return row


def file_name(row: dict, now: Optional[dt.datetime] = None) -> str:
    ts = parse_timestamp(str(row.get(TS_COLUMN, ""))) or now or dt.datetime.now()
    return f"{FILE_PREFIX}{ts:%d%m%Y%H%M%S}.csv"


def write_csv(row: dict, folder: str, now: Optional[dt.datetime] = None) -> str:
    """Write the one-row CSV (plain numbers, no thousands separators). -> the file path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, file_name(row, now))
    cols = [c for c, _ in COLUMNS] + [TS_COLUMN]
    tmp = path + ".part"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerow([("%.2f" % row[c]) if isinstance(row[c], float) else row[c] for c in cols])
    os.replace(tmp, path)
    return path


def is_level1(path: str) -> bool:
    return bool(FILE_RX.match(os.path.basename(str(path))))


def read_boxes(path: str) -> Dict[str, float]:
    """Level-1 file -> {fo_deposit, fo_used, fo_pro, fo_client} in CRORE."""
    import pandas as pd
    df = pd.read_csv(path)
    if df.empty:
        raise Level1Error("the Level-1 file is empty")
    out = {}
    for k, col in BOXES.items():
        if col not in df.columns:
            raise Level1Error(f"the Level-1 file has no column '{col}'")
        out[k] = float(df[col].iloc[0]) / 1e7
    return out
