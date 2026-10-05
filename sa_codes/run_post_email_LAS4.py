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

# Helper functions LAS4.py defines BEFORE the marker (e.g. password_protect_excel_force)
# are needed by the tail too — load just those defs, run none of the earlier steps.
import time                                            # noqa: E402,F401
try:
    import win32com.client as win32                    # noqa: E402,F401
except Exception:
    win32 = None
import ast                                             # noqa: E402
_full = "\n".join(lines)
_tree = ast.parse(_full)
_defs = [n for n in _tree.body
         if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.lineno <= start]
if _defs:
    _mod = ast.Module(body=_defs, type_ignores=[])
    exec(compile(_mod, src_path, "exec"), globals())
    print(f"🧩 Loaded helper functions: {', '.join(d.name for d in _defs)}")

# ── Auto-resolve module-level settings defined before the marker ─────────────
# (EMAIL_DATE, SMTP_*, EMAIL_ADDRESS, imports …). Instead of guessing names one
# crash at a time, find every name the tail (and the helper defs above) reads
# but never defines, and execute ONLY the plain assignments/imports that define
# them (repeating for names those need). No step logic from before the marker runs.
import builtins as _bi                                 # noqa: E402

_tail_tree = ast.parse("\n".join(lines[start:]))


def _loaded_stored(tree):
    ld, st = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            (ld if isinstance(n.ctx, ast.Load) else st).add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            st.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                st.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.arg):
            st.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            st.add(n.name)
    return ld, st


def _module_level(stmts):
    """Statements outside any function/class body (descends into if/try/with/for)."""
    for s in stmts:
        if isinstance(s, (ast.FunctionDef, ast.ClassDef)):
            continue
        yield s
        for f in ("body", "orelse", "finalbody"):
            sub = getattr(s, f, None)
            if isinstance(sub, list):
                yield from _module_level(sub)
        for h in getattr(s, "handlers", []) or []:
            yield from _module_level(h.body)


_ld, _st = _loaded_stored(_tail_tree)
for d in _defs:
    _l2, _ = _loaded_stored(d)
    _ld |= _l2
_pre = [s for s in _module_level(_tree.body) if s.lineno < start + 1]
_pre = [s for s in _pre if isinstance(s, (ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom))]
_done = set()
for _round in range(6):
    _need = {n for n in _ld if n not in _st and n not in globals() and not hasattr(_bi, n)}
    _progress = False
    for s in _pre:
        if id(s) in _done:
            continue
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            _names = {(a.asname or a.name).split(".")[0] for a in s.names}
        else:
            _tg = s.targets if isinstance(s, ast.Assign) else [s.target]
            _names = {n.id for t in _tg for n in ast.walk(t) if isinstance(n, ast.Name)}
        if not (_names & _need):
            continue
        _done.add(id(s))
        try:
            exec(compile(ast.Module(body=[s], type_ignores=[]), src_path, "exec"), globals())
            _progress = True
            _l3, _ = _loaded_stored(s)
            _ld |= _l3
        except Exception as _e:
            print(f"⚠️ Could not pre-load {sorted(_names)} (LAS4.py line {s.lineno}): {_e}")
    if not _progress:
        break
_still = sorted(n for n in _ld if n not in _st and n not in globals() and not hasattr(_bi, n))
if _still:
    print(f"ℹ️ Names still undefined before start (may be set inside the block): {_still}")

print(f"🔁 Resuming LAS4 from the post-email block (LAS4.py line {start + 1})")
print(f"📂 BASE_FOLDER = {BASE_FOLDER}")

# Pad with blank lines so tracebacks show the real LAS4.py line numbers.
code = compile("\n" * start + "\n".join(lines[start:]), src_path, "exec")
exec(code, globals())
