"""Which MCX file CTCL vs NMASS uses for the MCX "Margin Limit" part of DEPOSIT NMASS.

Two TCS BANCS exports can feed it (both are random-named .xlsx files that carry today's YYYYMMDD stamp):

  "mcx90"    the MCX 90% margin file   - columns "Client Code" and "Client Margin Limit"
  "deposit"  the MCX Deposit file ("Query Cash Collateral Detailed Limit") - columns "UCC Code" and "Margin Limit"

The choice is a single switch kept in a small JSON file, so the dashboard button and the run (a separate process)
always agree. Default is "mcx90", i.e. exactly the behaviour before this option existed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

SOURCES = ("mcx90", "deposit")
LABELS = {"mcx90": "MCX 90%", "deposit": "MCX Deposit"}
# Header columns that identify each kind of file (checked in the first 10 rows of the sheet).
REQUIRED_COLUMNS = {"mcx90": ["Client Code", "Client Margin Limit"], "deposit": ["UCC Code", "Margin Limit"]}
SWITCH_FILE = Path(os.getenv("CTCL_MCX_SOURCE_FILE") or Path(__file__).resolve().parents[1] / ".ctcl_mcx_source.json")


def get_source(path: Path | None = None) -> str:
    """The selected source ("mcx90" or "deposit"); "mcx90" when nothing was ever chosen or the file is unreadable."""
    try:
        value = json.loads((path or SWITCH_FILE).read_text(encoding="utf-8")).get("source", "mcx90")
    except (OSError, ValueError):
        return "mcx90"
    return value if value in SOURCES else "mcx90"


def set_source(source: str, who: str = "", path: Path | None = None) -> dict:
    """Persist the choice; raises ValueError for an unknown source."""
    if source not in SOURCES:
        raise ValueError(f"unknown MCX source {source!r}")
    state = {"source": source, "by": who, "at": datetime.now().isoformat(timespec="seconds")}
    (path or SWITCH_FILE).write_text(json.dumps(state), encoding="utf-8")
    return state


def describe(path: Path | None = None) -> dict:
    """State for the dashboard: {"source", "label", "by", "at"}."""
    source = get_source(path)
    try:
        saved = json.loads((path or SWITCH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {"source": source, "label": LABELS[source], "by": saved.get("by", ""), "at": saved.get("at", "")}
