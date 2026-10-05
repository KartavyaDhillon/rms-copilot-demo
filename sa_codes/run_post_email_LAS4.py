"""
run_post_email_LAS4.py — re-run ONLY the post-email part of LAS4.py.

Use when LAS4 already sent its WhatsApp messages + emails but the steps after
them (SCENARIO_Filtered / Top-100 / LAS reference / merges / backup) crashed —
e.g. scenario.csv was missing a column. Fix the input file, then run this.
Nothing is re-sent: everything from LAS4.py's marker
"POST-EMAIL SAFE ADD-ON BLOCK" onward is executed, and nothing before it.

The LAS4.py "run 4 completed" guard, pre-flight and 45-minute freshness checks
are deliberately NOT repeated here (they ran on the original attempt).

Run from the dashboard (SA-04 → post-email recovery) or by hand:
    python sa_codes\\run_post_email_LAS4.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO)

# Same env loading order as the rest of sa_codes (machine .env.local wins).
try:
    from dotenv import load_dotenv
    _local = os.path.join(_REPO, ".env.local")
    if os.path.exists(_local):
        load_dotenv(_local, override=True)
    load_dotenv(os.getenv("SA_ENV_FILE", os.path.join(_REPO, ".env")))
except Exception:
    pass

MARKER = "POST-EMAIL SAFE ADD-ON BLOCK"
src_path = os.path.join(_HERE, "LAS4.py")
with open(src_path, encoding="utf-8") as f:
    lines = f.read().split("\n")

idx = next((i for i, l in enumerate(lines) if MARKER in l), None)
if idx is None:
    print(f"❌ Marker '{MARKER}' not found in LAS4.py — cannot resume.")
    sys.exit(1)
start = max(idx - 1, 0)          # include the "# ====" line above the marker

# ── Prelude: what LAS4.py defines at its top and the tail relies on ──────────
import os as _sa_os                                    # noqa: E402


def _SAENV(_k, _d):
    _v = _sa_os.getenv(_k)
    return _v if (_v is not None and _v != "") else _d


def _SALIST(_k, _d):
    _v = _sa_os.getenv(_k)
    return [x.strip() for x in _v.split(",") if x.strip()] if _v else _d


import glob, re                                        # noqa: E402,F401
import pandas as pd                                    # noqa: E402,F401
from datetime import datetime                          # noqa: E402,F401
import sys as _la_sys                                  # noqa: E402,F401
import _checkpoint as _ckpt                            # noqa: E402
try:
    from las_image_generator import xlsx_to_image as _las_img   # noqa: E402,F401
except Exception:
    _las_img = None

BASE_FOLDER = _SAENV("SA_LAS_FUNDING_DIR", r"C:\KARTAVYA\LAS FUNDING")
COMMON_DIR = _SAENV("SA_COMMON_DIR", r"C:\COMMON FILES")
_SA_OUTPUT_DIR = _SAENV("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
today_excel = datetime.today().strftime("%d-%m-%Y")
today_csv = datetime.today().strftime("%d%m%Y")

print(f"🔁 Resuming LAS4 from the post-email block (LAS4.py line {start + 1})")
print(f"📂 BASE_FOLDER = {BASE_FOLDER}")

# Pad with blank lines so tracebacks show the real LAS4.py line numbers.
code = compile("\n" * start + "\n".join(lines[start:]), src_path, "exec")
exec(code, globals())
