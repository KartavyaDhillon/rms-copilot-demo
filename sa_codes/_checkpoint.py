"""
_checkpoint.py — SA / LAS step checkpoint tracker
===================================================
Writes a JSON file before each major step so the dashboard knows exactly
where a script stopped if it crashes.  Also supports resume-from-step
by providing a helper that scripts call at each section start.

CHECKPOINT FILE:
    {OUTPUT_DIR}/.las_checkpoint.json   — written by LAS4 / LASUPTO3
    {OUTPUT_DIR}/.sa_checkpoint.json    — written by shortallocation_final...

RESUME ENV VAR:
    LAS_RESUME_FROM=<step_id>   — set by dashboard before relaunching script
    SA_RESUME_FROM=<step_id>    — same for SA Code-2

Example step_id values:
    LAS: DATA_LOAD, SA_COMPUTE, OUTPUT_FILES, SCREENSHOTS, ZONE_PROCESS,
         RISK_NOV, WA_IMAGES, WA_SEND, EMAILS, DONE
    SA:  INIT, SA_COMPUTE, EMAILS, BACKUP, DONE
"""

import os
import json
from datetime import datetime

try:
    # The fallback used to be a hardcoded D:\CLAUDE\rms-copilot\.env — one
    # machine's repo path, so on any other machine this load quietly did
    # nothing and _BASE_DIR below fell back to its C:\KARTAVYA default.
    # Derived from this file's own location instead, so it follows the checkout.
    _repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    from dotenv import load_dotenv
    # .env.local FIRST with override=True (machine layout wins), then .env
    # filling gaps — the same order core/env_boot.py establishes. Loading only
    # .env here would hand this module the wrong machine's folders.
    _local = os.path.join(_repo, ".env.local")
    if os.path.exists(_local):
        load_dotenv(_local, override=True)
    load_dotenv(os.getenv("SA_ENV_FILE", os.path.join(_repo, ".env")))
except Exception:
    pass

_BASE_DIR = os.getenv("SA_OUTPUT_DIR", r"C:\KARTAVYA\SHORT ALLOCATION")

_LAS_CKPT = os.path.join(_BASE_DIR, ".las_checkpoint.json")
_SA_CKPT  = os.path.join(_BASE_DIR, ".sa_checkpoint.json")

# ──────────────────────────────────────────────
# Ordered step list (used to determine "before" / "after")
# ──────────────────────────────────────────────
LAS_STEP_ORDER = [
    "DATA_LOAD",
    "SA_COMPUTE",
    "OUTPUT_FILES",
    "SCREENSHOTS",
    "ZONE_PROCESS",
    "RISK_NOV",
    "WA_IMAGES",
    "WA_SEND",
    "EMAILS",
    "DONE",
]

SA_STEP_ORDER = [
    "INIT",
    "SA_COMPUTE",
    "EMAILS",
    "BACKUP",
    "DONE",
]

# Friendly display names shown in dashboard
LAS_STEP_NAMES = {
    "DATA_LOAD":    "Load LAS Funding + SA files",
    "SA_COMPUTE":   "Compute SA deposits & scenario values",
    "OUTPUT_FILES": "Create output xlsx files",
    "SCREENSHOTS":  "Generate table screenshots",
    "ZONE_PROCESS": "Zone-wise filtering (CHND / LUDH / MUMB)",
    "RISK_NOV":     "Risk NOV merge & Top-100 scenario calc",
    "WA_IMAGES":    "Export WhatsApp images (zone screenshots)",
    "WA_SEND":      "Send WhatsApp messages",
    "EMAILS":       "Send zone emails",
    "DONE":         "Complete",
}

SA_STEP_NAMES = {
    "INIT":       "Initialise & load input files",
    "SA_COMPUTE": "Short Allocation processing & output",
    "EMAILS":     "Send zone emails",
    "BACKUP":     "Backup & cleanup output files",
    "DONE":       "Complete",
}


# ──────────────────────────────────────────────
# WRITE helpers (called inside LAS4 / LASUPTO3 / shortallocation)
# ──────────────────────────────────────────────

def _write(ckpt_file: str, script: str, step_id: str, run_no=None, extra: dict = None):
    try:
        os.makedirs(os.path.dirname(ckpt_file) or ".", exist_ok=True)
        payload = {
            "script":    script,
            "step_id":   step_id,
            "step_name": (LAS_STEP_NAMES if "LAS" in script else SA_STEP_NAMES).get(step_id, step_id),
            "run_no":    run_no,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "status":    "running",
        }
        if extra:
            payload.update(extra)
        with open(ckpt_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"[checkpoint] write error: {e}")


def las_step(step_id: str, run_no=None, script: str = "LAS"):
    """Call at the START of each LAS step. Writes checkpoint."""
    _write(_LAS_CKPT, script, step_id, run_no=run_no)
    print(f"[▶] Step: {LAS_STEP_NAMES.get(step_id, step_id)}")


def sa_step(step_id: str, run_no=None):
    """Call at the START of each SA Code-2 step. Writes checkpoint."""
    _write(_SA_CKPT, "SA", step_id, run_no=run_no)
    print(f"[▶] Step: {SA_STEP_NAMES.get(step_id, step_id)}")


def las_done(run_no=None):
    """Call at the END of a successful LAS run. Marks DONE."""
    _write(_LAS_CKPT, "LAS", "DONE", run_no=run_no, extra={"status": "done"})


def sa_done(run_no=None):
    """Call at the END of a successful SA run. Marks DONE."""
    _write(_SA_CKPT, "SA", "DONE", run_no=run_no, extra={"status": "done"})


# ──────────────────────────────────────────────
# READ helpers (called by api.py)
# ──────────────────────────────────────────────

def read_las() -> dict | None:
    try:
        if os.path.exists(_LAS_CKPT):
            with open(_LAS_CKPT, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def read_sa() -> dict | None:
    try:
        if os.path.exists(_SA_CKPT):
            with open(_SA_CKPT, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# RESUME helpers (called at the top of each script)
# ──────────────────────────────────────────────

def _should_run(step_id: str, resume_from: str, order: list) -> bool:
    """Return True if this step should execute (at or after resume_from)."""
    if not resume_from:
        return True
    try:
        return order.index(step_id) >= order.index(resume_from)
    except ValueError:
        return True   # unknown step → always run


def las_should_run(step_id: str) -> bool:
    """
    Returns True if this LAS step should execute.
    Reads LAS_RESUME_FROM env var.  If empty → always True.
    """
    resume = os.getenv("LAS_RESUME_FROM", "").strip()
    return _should_run(step_id, resume, LAS_STEP_ORDER)


def sa_should_run(step_id: str) -> bool:
    """
    Returns True if this SA step should execute.
    Reads SA_RESUME_FROM env var.
    """
    resume = os.getenv("SA_RESUME_FROM", "").strip()
    return _should_run(step_id, resume, SA_STEP_ORDER)
