"""Which MCX file a pipeline uses for the MCX "Margin Limit" part of DEPOSIT NMASS.

Used by CTCL vs NMASS ("ctcl") and Margin Shortage 15-min ("ms15"); each box has its OWN choice.

Two TCS BANCS exports can feed it:

  "mcx90"    the MCX 90% margin file - random-named .xlsx stamped with today's YYYYMMDD,
             columns "Client Code" and "Client Margin Limit"
  "deposit"  the MCX Deposit file ("Query Cash Collateral Detailed Limit") - either a random-named export stamped
             with today's YYYYMMDD or the console's own MCX_DEPOSIT_HH.MM.xlsx download,
             columns "UCC Code" and "Margin Limit"

Both also carry "Margin Utilization" (needed by Margin Shortage). The choice is a small JSON file per box, so the
dashboard button and the run (a separate process) always agree. Default is "mcx90", i.e. exactly the behaviour
before this option existed.

normalize_code() is the shared "leading zero" rule: the exchange files write an all-digit client code without its
leading zeros (80011226) while Update Limits / RISK_REPORT keep them (080011226); every pipeline matches clients on
the zero-stripped key so one client is never split into two rows.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

SOURCES = ("mcx90", "deposit")
LABELS = {"mcx90": "MCX 90%", "deposit": "MCX Deposit"}
# Header columns that identify each kind of file (checked in the first rows of the sheet). "Margin Utilization" is
# only needed by Margin Shortage, so it is listed separately.
REQUIRED_COLUMNS = {"mcx90": ["Client Code", "Client Margin Limit"], "deposit": ["UCC Code", "Margin Limit"]}
UTILIZATION_COLUMN = "Margin Utilization"
_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_FILES = {"ctcl": _ROOT / ".ctcl_mcx_source.json", "ms15": _ROOT / ".ms15_mcx_source.json"}
PIPES = tuple(_DEFAULT_FILES)
# Kept for older callers/tests that import SWITCH_FILE (the CTCL switch).
SWITCH_FILE = Path(os.getenv("CTCL_MCX_SOURCE_FILE") or _DEFAULT_FILES["ctcl"])


def _file(pipe: str, path: Path | None) -> Path:
    """Switch file of `pipe` (an explicit `path` wins - used by tests)."""
    if path is not None:
        return Path(path)
    if pipe == "ctcl":
        return SWITCH_FILE
    if pipe not in _DEFAULT_FILES:
        raise ValueError(f"unknown pipe {pipe!r}")
    return Path(os.getenv(f"{pipe.upper()}_MCX_SOURCE_FILE") or _DEFAULT_FILES[pipe])


def get_source(pipe: str = "ctcl", path: Path | None = None) -> str:
    """The selected source ("mcx90" or "deposit"); "mcx90" when nothing was ever chosen or the file is unreadable."""
    try:
        value = json.loads(_file(pipe, path).read_text(encoding="utf-8")).get("source", "mcx90")
    except (OSError, ValueError):
        return "mcx90"
    return value if value in SOURCES else "mcx90"


def set_source(source: str, who: str = "", pipe: str = "ctcl", path: Path | None = None) -> dict:
    """Persist the choice; raises ValueError for an unknown source or pipe."""
    if source not in SOURCES:
        raise ValueError(f"unknown MCX source {source!r}")
    state = {"source": source, "by": who, "at": datetime.now().isoformat(timespec="seconds")}
    _file(pipe, path).write_text(json.dumps(state), encoding="utf-8")
    return state


def describe(pipe: str = "ctcl", path: Path | None = None) -> dict:
    """State for the dashboard: {"source", "label", "by", "at"}."""
    source = get_source(pipe, path)
    try:
        saved = json.loads(_file(pipe, path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {"source": source, "label": LABELS[source], "by": saved.get("by", ""), "at": saved.get("at", "")}


def normalize_code(code) -> str:
    """Matching key for a client code: all-digit codes lose their leading zeros ("080011226" -> "80011226"),
    codes with letters ("03PI94") are only trimmed. Empty / missing -> ""."""
    text = "" if code is None else str(code).strip()
    if text.lower() in ("nan", "none", "<na>"):
        return ""
    if re.fullmatch(r"[0-9]+", text):
        return text.lstrip("0") or "0"
    return text
