"""
core/readiness.py — Input File Readiness.
==========================================
Answers one question per pipeline: "Can I run this RIGHT NOW?"

For each pipeline it knows the REQUIRED input files (date-aware patterns)
and checks whether each one is present in the pipeline's folder today.
Pure filesystem checks — no LLM, no network, instant.

Used by:
  - GET /folder-readiness  → dashboard "Input File Readiness" card
  - agent tool check_input_readiness → "can I run CTCL now / what's missing?"

Reads the same env vars as the agent's FOLDER_MAP, directly (no import,
so there is no circular dependency between agent.py and this module).
"""
import os
from datetime import datetime

# Same env names + defaults as core/agent.py FOLDER_MAP — kept in sync by hand
# (5 lines of duplication beats a circular import).
_FOLDERS = {
    "ctcl":          lambda: os.getenv("CTCL_NMASS_FOLDER",    r"D:\CTCL VS NMASS"),
    "cnc_f11":       lambda: os.getenv("CNC_F11_FOLDER",       r"C:\CNC_F11"),
    "mtf_order":     lambda: os.getenv("MTF_ORDER_FOLDER",     r"D:\MTF ORDER FILE"),
    "concentration": lambda: os.getenv("CONCENTRATION_FOLDER", r"D:\CONCENTRATION"),
}

# Required INPUT files per pipeline. {ddmmyyyy} is replaced with today's date.
# Match = case-insensitive substring against filenames in the folder (top level).
REQUIRED_INPUTS = {
    "ctcl": [
        ("CM collaterals",        "CM_ClientLevelCollaterals_{ddmmyyyy}"),
        ("FO collaterals",        "FO_ClientLevelCollaterals_{ddmmyyyy}"),
        ("CO collaterals",        "CO_ClientLevelCollaterals_{ddmmyyyy}"),
        ("Update Limits",         "Update Limits"),
        ("Net Effective Deposits","ClientNetEffectiveDeposits"),
    ],
    "cnc_f11": [
        ("PositionBook",          "PositionBook.csv"),
    ],
    "mtf_order": [
        ("MTF positions",         "MTFPOSITION_{ddmmyyyy}"),
        ("Risk shortage report",  "RISK_SHORTAGE"),
        ("Risk report",           "RISK_REPORT"),
    ],
    "concentration": [
        ("Nifty50 list",          "nifty50"),
        ("BSE200 list",           "BSE200"),
        ("BSE500 list",           "BSE500"),
    ],
}

DISPLAY_NAMES = {
    "ctcl": "CTCL vs NMASS",
    "cnc_f11": "CNC F11",
    "mtf_order": "MTF Order File",
    "concentration": "Concentration",
}


def check_readiness(pipeline: str) -> dict:
    """Check one pipeline. Returns present/missing input files + ready flag."""
    if pipeline not in REQUIRED_INPUTS:
        return {"error": f"No readiness rules for '{pipeline}'. "
                         f"Known: {list(REQUIRED_INPUTS.keys())}"}
    folder = _FOLDERS[pipeline]()
    if not os.path.isdir(folder):
        return {"pipeline": pipeline, "name": DISPLAY_NAMES.get(pipeline, pipeline),
                "folder": folder, "ready": False,
                "error": f"Folder not found: {folder}"}

    ddmmyyyy = datetime.now().strftime("%d%m%Y")
    try:
        names = [f for f in os.listdir(folder)
                 if os.path.isfile(os.path.join(folder, f))]
    except OSError as e:
        return {"pipeline": pipeline, "folder": folder, "ready": False,
                "error": f"Cannot read folder: {e}"}
    lower_names = [n.lower() for n in names]

    present, missing = [], []
    for label, pattern in REQUIRED_INPUTS[pipeline]:
        pat = pattern.replace("{ddmmyyyy}", ddmmyyyy).lower()
        hit = next((names[i] for i, n in enumerate(lower_names) if pat in n), None)
        if hit:
            present.append({"input": label, "file": hit})
        else:
            missing.append({"input": label,
                            "expected_pattern": pattern.replace("{ddmmyyyy}", ddmmyyyy)})

    return {
        "pipeline": pipeline,
        "name": DISPLAY_NAMES.get(pipeline, pipeline),
        "folder": folder,
        "checked_at": datetime.now().strftime("%H:%M:%S"),
        "ready": len(missing) == 0,
        "present": present,
        "missing": missing,
    }


def check_all() -> dict:
    """Check every pipeline that has readiness rules."""
    out = {p: check_readiness(p) for p in REQUIRED_INPUTS}
    return {
        "checked_at": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
        "pipelines": out,
        "ready_count": sum(1 for r in out.values() if r.get("ready")),
        "total": len(out),
    }
