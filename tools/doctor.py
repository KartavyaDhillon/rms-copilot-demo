r"""
tools/doctor.py — preflight check. Run after every deploy, before every
first-run-of-the-day on a machine you have just touched.

WHY
───
Nearly every deployment failure in this system is silent at the moment it
happens and loud several hours later: a missing reference file that only stops
CTCL at 08:40, a Postgres password that only fails when the first run tries to
archive, a folder setting that quietly resolved to its hardcoded fallback and
has been writing a day's output somewhere nobody looks.

Every one of those is detectable in about two seconds if you actually go and
look. This goes and looks.

Exit code is 0 when nothing is wrong, 1 when something needs attention — so
DEPLOY.bat can stop on a bad result instead of cheerfully restarting a broken
server.

USAGE
─────
    python tools\doctor.py            full check
    python tools\doctor.py --quick    skip the network/Postgres/SMTP probes
"""
import argparse
import importlib
import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import core.env_boot as env_boot  # noqa: E402  (must run before anything reads config)

# Same classifier make_env_local.py uses to decide what a path IS — imported so
# the two can never disagree about which settings are data folders, which are
# code inside the repo, and which are installed programs.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_env_local import _classify  # noqa: E402

_ABS_PATH = re.compile(r"^[A-Za-z]:\\")
_MARKER = "# keep-external:"

_PASS, _WARN, _FAIL = "ok  ", "WARN", "FAIL"
_results = []


def check(level, label, detail=""):
    _results.append((level, label, detail))
    print(f"  {level}  {label}" + (f"   {detail}" if detail else ""))


def section(title):
    print(f"\n{title}\n" + "-" * 74)


# ── 1. Interpreter and packages ─────────────────────────────────────────────
def check_python():
    section("1. Python and packages")
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    # 3.13.x is what the venv was built against. A different PATCH is fine —
    # wheels are compatible within a minor. A different MINOR is not.
    if (v.major, v.minor) == (3, 13):
        check(_PASS, "Python version", ver)
    else:
        check(_FAIL, "Python version", f"{ver} — this project expects 3.13.x")

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        check(_PASS, "Running inside the virtualenv", sys.prefix)
    else:
        check(_WARN, "NOT in a virtualenv",
              "run .venv\\Scripts\\activate first, or packages may be missing")

    # Only the imports whose absence produces a confusing failure much later.
    for mod, why in [
        ("dotenv", "config loading"), ("fastapi", "the web server"),
        ("uvicorn", "the web server"), ("pandas", "every pipeline"),
        ("polars", "CTCL / large CSV reads"), ("openpyxl", "Excel output"),
        ("psycopg2", "Postgres archiving"), ("requests", "every download"),
        ("selenium", "NMASS / TCS BANCS downloads"),
    ]:
        try:
            importlib.import_module(mod)
            check(_PASS, f"import {mod}")
        except Exception as e:
            check(_FAIL, f"import {mod}", f"needed for {why} — {type(e).__name__}")

    # Windows-only, and genuinely optional — but its absence changes behaviour
    # silently, which is worse than failing.
    if os.name == "nt":
        try:
            importlib.import_module("win32com.client")
            check(_PASS, "import win32com (Excel automation)")
        except Exception:
            check(_WARN, "win32com not available",
                  "PROBABLE zone files will send WITHOUT password protection")


# ── 2. Configuration files ──────────────────────────────────────────────────
def _env_pairs(path):
    pairs = []
    if not os.path.exists(path):
        return pairs
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                pairs.append((k.strip(), v.strip()))
    return pairs


def _externals():
    p = os.path.join(_REPO, ".env.local")
    out = set()
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith(_MARKER):
                    out.update(k.strip() for k in line[len(_MARKER):].split(",") if k.strip())
    return out


def check_config():
    section("2. Configuration")
    env_path = os.path.join(_REPO, ".env")
    local_path = os.path.join(_REPO, ".env.local")

    check(_PASS if os.path.exists(env_path) else _FAIL, ".env present", env_path)
    if os.path.exists(local_path):
        check(_PASS, ".env.local present", local_path)
    else:
        check(_WARN, "no .env.local",
              "this machine is using .env defaults — run tools\\make_env_local.py")

    root = os.getenv("RMS_DATA_ROOT", "")
    if root:
        check(_PASS if os.path.isdir(root) else _WARN, "RMS_DATA_ROOT", root)
    else:
        check(_WARN, "RMS_DATA_ROOT not set", f"falling back to {env_boot.data_root()}")

    secret = os.getenv("RMS_SECRET_KEY", "").strip()
    if len(secret) >= 20:
        check(_PASS, "RMS_SECRET_KEY set")
    else:
        check(_WARN, "RMS_SECRET_KEY missing/short",
              "every server restart will sign all users out")
    return root


# ── 3. Folders ──────────────────────────────────────────────────────────────
def check_folders(root):
    section("3. Working folders")
    pairs = _env_pairs(os.path.join(_REPO, ".env"))
    keys = []
    for k, v in pairs:
        if _ABS_PATH.match(v) and k not in keys:
            keys.append(k)

    external = _externals()
    strays = []
    for key in keys:
        resolved = os.getenv(key, "")
        if not resolved:
            continue

        # A setting can be a comma-separated list (CODE_FOLDERS). Classify each
        # entry, because they can legitimately be different kinds.
        parts = [p.strip() for p in resolved.split(",")] if "," in resolved else [resolved]
        kinds = [_classify(p)[0] for p in parts]

        # ── INSTALLED PROGRAM (e.g. SA_TESSERACT) ──────────────────────────
        # An .exe is NEVER "created on first run" — saying so turned a missing
        # Tesseract into a green tick, and the failure only surfaced later as a
        # captcha that never solved. Check the file, and say so plainly.
        if kinds and all(k == "program" for k in kinds):
            if os.path.isfile(resolved):
                check(_PASS, key, f"{resolved}  [installed program]")
            else:
                check(_FAIL, key, f"{resolved}  ← NOT INSTALLED HERE. "
                                  f"Install it, or point this at its real location.")
            continue

        # ── CODE INSIDE THE REPO (SA_CODES_DIR, CODE_FOLDERS) ──────────────
        # These arrive with `git clone`, so a missing one means the setting is
        # pointing at the wrong place — not that it will appear later. They are
        # also expected to sit OUTSIDE the data root, so the stray check below
        # must not fire on them.
        if "repo" in kinds:
            missing = [p for p, k in zip(parts, kinds)
                       if k == "repo" and not os.path.isdir(p)]
            if missing:
                check(_WARN, key, f"{resolved}  ← code folder(s) not found: "
                                  f"{', '.join(missing)}. Re-run tools\\make_env_local.py.")
            else:
                check(_PASS, key, f"{resolved}  [code, follows the repo]")
            continue

        exists = os.path.isdir(resolved)
        # Data folders DO auto-create on first use, so "missing" here really is
        # informational rather than an error.
        note = "" if exists else "(will be created on first run)"

        if root and key not in external:
            try:
                inside = os.path.commonpath([os.path.abspath(resolved),
                                             os.path.abspath(root)]) == os.path.abspath(root)
            except ValueError:
                inside = False       # different drive letters
            if not inside:
                strays.append((key, resolved))
                check(_WARN, key, f"{resolved}  ← OUTSIDE the data root")
                continue
        tag = "  [external, by design]" if key in external else ""
        check(_PASS, key, f"{resolved} {note}{tag}".strip())

    if strays:
        print()
        check(_WARN, f"{len(strays)} folder(s) resolve outside RMS_DATA_ROOT",
              "either rebase them (tools\\make_env_local.py) or mark them "
              "--keep-external if they are shared drop points")


# ── 4. Reference files ──────────────────────────────────────────────────────
def check_reference_files():
    section("4. Reference files (these cannot auto-create)")
    ctcl = os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    probable = os.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    wanted = [
        (os.path.join(_REPO, os.getenv("USER_SHEET", "Book1.xlsx")),
         "user roster — nobody can sign in without it"),
        (os.path.join(ctcl, os.getenv("CTCL_KAMBALA_FILE", "KAMBALA_ALLOCATION_LIST.xlsx")),
         "CTCL allocation + virtual IDs — the pre-run check fails without it"),
        (os.path.join(probable, "MASTER", "MAIL LIST.xlsx"),
         "PROBABLE branch/region recipients — step 10 is skipped without it"),
        (os.path.join(probable, "MASTER", "Branch_AND_Region_LIST.xlsx"),
         "PROBABLE validation reference — step 11 is skipped without it"),
    ]
    for path, why in wanted:
        if os.path.exists(path):
            check(_PASS, os.path.basename(path), path)
        else:
            check(_FAIL, os.path.basename(path), f"MISSING — {why}")


# ── 5. Postgres ─────────────────────────────────────────────────────────────
def check_postgres():
    section("5. Postgres")
    if os.getenv("USE_POSTGRES", "false").strip().lower() != "true":
        check(_WARN, "USE_POSTGRES is false",
              "runs will not be archived; History and Ask will be empty")
        return
    host = os.getenv("PG_HOST", "localhost")
    db = os.getenv("PG_DB", "postgres")
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=host, port=os.getenv("PG_PORT", "5432"), dbname=db,
            user=os.getenv("PG_USER", "postgres"),
            password=os.getenv("PG_PASSWORD", ""), connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'")
        n = cur.fetchone()[0]
        conn.close()
        check(_PASS, f"connected to {host}/{db}", f"{n} tables")
        if n == 0:
            check(_WARN, "database is empty",
                  "expected if this is a fresh install; NOT expected after a pg_restore")
    except Exception as e:
        check(_FAIL, f"cannot connect to {host}/{db}", f"{type(e).__name__}: {e}")


# ── 6. External tools ───────────────────────────────────────────────────────
def check_external():
    section("6. External tools")
    if os.name != "nt":
        check(_WARN, "not Windows", "Excel/Chrome checks skipped")
        return
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    if any(os.path.exists(p) for p in chrome_paths):
        check(_PASS, "Google Chrome installed")
    else:
        check(_FAIL, "Chrome not found", "NMASS/NCDEX and TCS BANCS downloads will fail")

    try:
        import win32com.client as win32
        excel = win32.gencache.EnsureDispatch("Excel.Application")
        excel.Quit()
        check(_PASS, "Excel COM automation works")
    except Exception as e:
        check(_WARN, "Excel COM not working",
              f"{type(e).__name__} — PROBABLE zone files will not be password-protected")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="skip Postgres and Excel/Chrome probes")
    args = ap.parse_args()

    print("=" * 74)
    print("RMS Copilot — preflight check")
    print(f"repo: {_REPO}")
    print("=" * 74)

    check_python()
    root = check_config()
    check_folders(root)
    check_reference_files()
    if not args.quick:
        check_postgres()
        check_external()

    fails = [r for r in _results if r[0] == _FAIL]
    warns = [r for r in _results if r[0] == _WARN]

    print("\n" + "=" * 74)
    if fails:
        print(f"{len(fails)} FAILURE(S) — do not start the server until these are fixed:\n")
        for _, label, detail in fails:
            print(f"   - {label}: {detail}")
    if warns:
        print(f"\n{len(warns)} warning(s) — review, but not necessarily blocking:\n")
        for _, label, detail in warns:
            print(f"   - {label}: {detail}")
    if not fails and not warns:
        print("All checks passed.")
    elif not fails:
        print("\nNo failures. Safe to start.")
    print("=" * 74)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
