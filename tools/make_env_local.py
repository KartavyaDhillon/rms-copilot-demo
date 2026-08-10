r"""
tools/make_env_local.py — generate this machine's .env.local.

WHAT PROBLEM THIS SOLVES
────────────────────────
.env is gitignored (it holds credentials), so `git pull` on the server brings
new CODE but never the new CONFIG it needs. Historically that meant hand-editing
.env on a live machine every time a process was added — under time pressure, on
the box that is about to run real money jobs.

The fix is a second file, .env.local, that core/env_boot.py loads FIRST with
override=True. Anything in it beats .env. So .env can keep its current absolute
paths as harmless defaults, and each machine states its own layout once, here.

WHY GENERATE IT RATHER THAN HAND-WRITE IT
─────────────────────────────────────────
There are ~25 folder settings spread across .env. Typing them by hand is how a
single mistyped path ends up writing a day's output into a folder nobody looks
at. This reads .env, finds every value that is an absolute Windows path, and
rewrites each one underneath a root you choose — keeping the existing folder
NAME so nothing else has to change.

    D:\PROBABLE          →  D:\CONSOLE\data\PROBABLE
    C:\COMMON FILES      →  D:\CONSOLE\data\COMMON FILES
    C:\PRIYANKA\SA_01_CM →  D:\CONSOLE\data\PRIYANKA\SA_01_CM

Re-run it after adding a process and it picks up the new keys automatically.
Your existing answers are preserved (see --keep-external below).

THREE KINDS OF PATH THIS MUST NOT BLINDLY REBASE
────────────────────────────────────────────────
Not every absolute path in .env is a data folder. Rebasing these under the data
root produces a path that cannot exist, and the failure is silent until the
feature that needs it is used:

  CODE inside this repo   SA_CODES_DIR=D:\CLAUDE\rms-copilot\sa_codes
                          These arrive with `git clone`; they live wherever the
                          repo lives. Rebased naively you get
                          <root>\CLAUDE\rms-copilot\sa_codes — a folder that
                          will never exist. They are re-pointed at THIS repo
                          (the one this script is running from) instead.

  INSTALLED PROGRAMS      SA_TESSERACT=C:\Program Files\Tesseract-OCR\...
                          Put there by an installer. Moving the setting does
                          not move the program. Left exactly as-is.

  MULTI-VALUE SETTINGS    CODE_FOLDERS=D:\CLAUDE\rms-copilot\core,D:\MY CODES
                          A comma-separated list. Only the first entry looked
                          like a path to the old single-value logic, so the
                          rest were silently left pointing at the old machine.
                          Each entry is now classified separately.

INTEGRATION FOLDERS — THE IMPORTANT BIT
───────────────────────────────────────
Some of these folders are not private scratch space; they are where ANOTHER
person or tool drops files (a shared COMMON FILES drop, a colleague's export
folder). Rebasing one of those does not just move your data — it silently
breaks somebody else's workflow, and you find out when a pipeline reports a
missing input days later.

Pass those with --keep-external so they are written out unchanged and clearly
commented as deliberate. doctor.py reads the same markers and will not flag
them as "outside the data root".

USAGE
─────
  Preview without writing anything (always do this first):
      python tools\make_env_local.py --root D:\CONSOLE\data --dry-run

  Write it, keeping two shared drops where they are:
      python tools\make_env_local.py --root D:\CONSOLE\data ^
          --keep-external SA_COMMON_DIR --keep-external RAJEEV_DIR

  Regenerate later after adding a process (keeps your previous --keep-external
  choices, which are recorded in the file itself):
      python tools\make_env_local.py --root D:\CONSOLE\data --reuse
"""
import argparse
import datetime
import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV = os.path.join(_REPO, ".env")
_LOCAL = os.path.join(_REPO, ".env.local")

# An absolute Windows path: a drive letter, a colon, a backslash.
_ABS_PATH = re.compile(r"^[A-Za-z]:\\")

# Settings that are machine-specific but are NOT paths. Listed so the generated
# file reminds you to set them rather than leaving you to discover them when
# Postgres refuses the connection at 08:40.
_NON_PATH_MACHINE_KEYS = [
    ("PG_PASSWORD", "Postgres password ON THIS MACHINE"),
    ("PG_HOST", "localhost, or the LAN IP of whichever machine hosts Postgres"),
]

_MARKER = "# keep-external:"

# Windows locations that belong to installed software, not to us. A path under
# one of these is where an installer put something; rebasing the SETTING does
# not move the PROGRAM, it just points at nothing. Matched case-insensitively
# against the part after the drive letter.
# Deliberately NOT including "users\" — a working folder can legitimately live
# under a user profile, and treating those as untouchable would silently leave
# them pointing at the old machine.
_PROGRAM_DIRS = ("program files (x86)\\", "program files\\", "windows\\",
                 "programdata\\")

# Marks a path as living inside a checkout of this repo. Such paths travel with
# `git clone`, so on this machine they belong under THIS repo, not under the
# data root.
_REPO_MARKER = "\\rms-copilot\\"


def _classify(value):
    """How should this one path be treated? Returns (kind, new_value).

    kind is 'data' (rebase under the data root), 'repo' (re-point at this
    checkout), 'program' (leave alone — installed software), or 'plain' (not an
    absolute path at all, e.g. a bare filename; leave alone)."""
    if not _ABS_PATH.match(value):
        return "plain", value
    tail = value[3:]                       # everything after "X:\"
    low = tail.lower()
    if low.startswith(_PROGRAM_DIRS):
        return "program", value
    marker_at = value.lower().find(_REPO_MARKER)
    if marker_at != -1:
        # …\rms-copilot\sa_codes  →  <this repo>\sa_codes
        inner = value[marker_at + len(_REPO_MARKER):]
        return "repo", (os.path.join(_REPO, inner) if inner else _REPO)
    return "data", value


def _rebase_value(value, root):
    """Apply _classify() across a whole setting, which may be a comma-separated
    list of paths (CODE_FOLDERS). Returns (new_value, kinds_used)."""
    parts = [p.strip() for p in value.split(",")] if "," in value else [value]
    out, kinds = [], []
    for p in parts:
        kind, v = _classify(p)
        kinds.append(kind)
        out.append(_rebase(v, root) if kind == "data" else v)
    return ",".join(out), kinds


def _read_env_pairs(path):
    """(key, value) for every simple KEY=VALUE line, in file order."""
    pairs = []
    if not os.path.exists(path):
        return pairs
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key:
                pairs.append((key, value))
    return pairs


def _previous_externals():
    """Read back the --keep-external choices recorded in an existing .env.local
    so --reuse can honour them without you retyping the list."""
    out = set()
    if not os.path.exists(_LOCAL):
        return out
    with open(_LOCAL, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(_MARKER):
                out.update(k.strip() for k in line[len(_MARKER):].split(",") if k.strip())
    return out


def _rebase(value, root):
    """D:\\PROBABLE → <root>\\PROBABLE. Everything after the drive letter is kept,
    so nested layouts (C:\\PRIYANKA\\SA_01_CM) survive intact."""
    tail = value[3:].strip("\\")          # drop "X:\"
    return os.path.join(root, tail) if tail else root


def main():
    ap = argparse.ArgumentParser(description="Generate .env.local for this machine.")
    ap.add_argument("--root", help=r"Data root, e.g. D:\CONSOLE\data")
    ap.add_argument("--keep-external", action="append", default=[], metavar="KEY",
                    help="Leave this key's path untouched (a shared/integration folder). Repeatable.")
    ap.add_argument("--reuse", action="store_true",
                    help="Reuse the --keep-external choices recorded in the existing .env.local.")
    ap.add_argument("--dry-run", action="store_true", help="Print the result, write nothing.")
    ap.add_argument("--force", action="store_true", help="Overwrite an existing .env.local.")
    args = ap.parse_args()

    if not os.path.exists(_ENV):
        print(f"ERROR: no .env found at {_ENV}")
        return 2

    root = (args.root or os.getenv("RMS_DATA_ROOT") or "").rstrip("\\")
    if not root:
        print("ERROR: --root is required (e.g. --root D:\\CONSOLE\\data)")
        return 2
    if not _ABS_PATH.match(root + "\\"):
        print(f"ERROR: --root must be an absolute Windows path, got {root!r}")
        return 2

    external = {k.strip() for k in args.keep_external if k.strip()}
    if args.reuse:
        external |= _previous_externals()

    pairs = _read_env_pairs(_ENV)
    # A multi-value setting only needs its FIRST entry to look like a path for
    # the whole line to be ours to handle — CODE_FOLDERS is the live example.
    path_keys = [(k, v) for k, v in pairs
                 if _ABS_PATH.match(v) or any(_ABS_PATH.match(p.strip()) for p in v.split(","))]
    if not path_keys:
        print("WARNING: no absolute paths found in .env — nothing to rebase.")

    # A key can legitimately appear more than once in .env; the last one wins at
    # load time, so mirror that here rather than emitting duplicates.
    seen, ordered = {}, []
    for k, v in path_keys:
        if k not in seen:
            ordered.append(k)
        seen[k] = v

    unknown = external - set(seen)
    if unknown:
        print("ERROR: --keep-external names no such path setting: " + ", ".join(sorted(unknown)))
        print("       Valid keys: " + ", ".join(ordered))
        return 2

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("# ── .env.local ─ THIS MACHINE ONLY. Never commit this file. ──────────────")
    L.append(f"# Generated by tools/make_env_local.py on {now}")
    L.append("#")
    L.append("# Loaded BEFORE .env, with override=True, so everything here wins over the")
    L.append("# shared defaults. See core/env_boot.py for why the order matters.")
    L.append("#")
    L.append("# Regenerate after adding a process (this keeps your choices below):")
    L.append(f"#     python tools\\make_env_local.py --root {root} --reuse")
    L.append("")
    if external:
        L.append(f"{_MARKER} {', '.join(sorted(external))}")
        L.append("")
    L.append("# The one line that defines where everything lives on this machine.")
    L.append(f"RMS_DATA_ROOT={root}")
    L.append("")

    L.append("# ── Machine settings that are not paths ─────────────────────────────────")
    for key, why in _NON_PATH_MACHINE_KEYS:
        current = next((v for k, v in reversed(pairs) if k == key), "")
        L.append(f"# {why}")
        L.append(f"{key}={current}")
    L.append("")

    # Classify everything first so each group can be written under its own
    # heading — a reader should be able to see at a glance which settings moved
    # under the data root and which deliberately did not.
    repo_keys, program_keys, data_keys = [], [], []
    new_values = {}
    for key in ordered:
        if key in external:
            continue
        nv, kinds = _rebase_value(seen[key], root)
        new_values[key] = nv
        if "repo" in kinds:
            repo_keys.append(key)
        elif kinds and all(k == "program" for k in kinds):
            program_keys.append(key)
        else:
            data_keys.append(key)

    L.append("# ── Working folders, rebased under the root above ───────────────────────")
    for key in data_keys:
        L.append(f"{key}={new_values[key]}")

    if repo_keys:
        L.append("")
        L.append("# ── CODE INSIDE THIS REPO — follows the checkout, not the data root ─────")
        L.append("# These arrive with `git clone`. Pointed at this repo automatically;")
        L.append("# a future `git pull` cannot move them, so they never need editing.")
        for key in repo_keys:
            L.append(f"{key}={new_values[key]}")

    if program_keys:
        L.append("")
        L.append("# ── INSTALLED PROGRAMS — left exactly as they were ──────────────────────")
        L.append("# Put there by an installer. If one of these is wrong on this machine,")
        L.append("# fix the path by hand to wherever it is actually installed here.")
        for key in program_keys:
            L.append(f"{key}={new_values[key]}")

    if external:
        L.append("")
        L.append("# ── DELIBERATELY LEFT OUTSIDE THE ROOT ──────────────────────────────────")
        L.append("# Shared drop points that another person or tool also writes into.")
        L.append("# Rebasing these would break their workflow, not just move your data.")
        L.append("# doctor.py knows about the marker above and will not flag them.")
        for key in ordered:
            if key in external:
                L.append(f"{key}={seen[key]}")

    text = "\n".join(L) + "\n"

    print(f"\n  root              : {root}")
    print(f"  rebased to root   : {len(data_keys)}")
    print(f"  pointed at repo   : {len(repo_keys)}" + (f"  ({', '.join(repo_keys)})" if repo_keys else ""))
    print(f"  installed programs: {len(program_keys)}" + (f"  ({', '.join(program_keys)})" if program_keys else ""))
    print(f"  left external     : {len(external)}" + (f"  ({', '.join(sorted(external))})" if external else ""))
    # Folders that came from a DIFFERENT drive than the data root are the ones
    # most likely to be somebody else's drop point rather than ours — worth a
    # second look before this gets written.
    _root_drive = root[:2].lower()
    cross = [k for k in data_keys if seen[k][:2].lower() != _root_drive]
    if cross:
        print(f"\n  NOTE: {len(cross)} folder(s) are moving onto another drive ({root[:2].upper()}) —")
        print("  check none of these is a shared drop point somebody else writes into.")
        print("  If one is, re-run with --keep-external <KEY> for it:")
        for k in cross:
            print(f"      {k:<38} {seen[k]}")

    if args.dry_run:
        print("\n--- .env.local (dry run, nothing written) ---\n")
        print(text)
        return 0

    if os.path.exists(_LOCAL) and not args.force and not args.reuse:
        print(f"\n{_LOCAL} already exists. Re-run with --reuse (keeps your choices) or --force.")
        return 1

    with open(_LOCAL, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\n  written         : {_LOCAL}")
    print("\nNext: python tools\\doctor.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
