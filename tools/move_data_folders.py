r"""
tools/move_data_folders.py — move working folders into the new data root.

WHAT PROBLEM THIS SOLVES
────────────────────────
When a machine runs before its .env.local exists, every folder setting falls
back to the value in .env — the OLD machine's layout. The console starts
happily and writes real output into folders like D:\15 minutes at the drive
root. Once .env.local is generated, the SETTINGS point at
D:\CONSOLE\data\15 minutes, but the FILES already written are still in the old
place, and nothing moves them.

That split is dangerous precisely because it is quiet. A pipeline looks in the
new folder, finds nothing, and either re-downloads or reports a missing input.
Worse, a pipeline that looks up yesterday's file by name can find the stale copy
in the old folder and process it as if it were current.

This walks every folder setting, works out old-location → new-location from the
two env files, and moves the contents across.

SAFETY — read this before using --apply
───────────────────────────────────────
  * DRY RUN BY DEFAULT. Nothing moves until you pass --apply.
  * NEVER OVERWRITES. If a file already exists at the destination, the source
    copy is left alone and reported. You decide which one is right — this script
    will not guess with a day's output.
  * MOVES, DOES NOT COPY, so you cannot end up running against two live copies
    of the same folder. The now-empty source folder is left behind on purpose:
    seeing it empty is how you confirm the move happened.
  * SKIPS code and installed-program paths, using the same classification as
    tools/make_env_local.py (imported, not duplicated, so the two cannot drift).

USAGE
─────
  See what would move — always do this first:
      python tools\move_data_folders.py

  Do it:
      python tools\move_data_folders.py --apply

  Afterwards, delete the empty old folders by hand once you're satisfied. That
  last step is deliberately manual: it is the one action here that is not
  reversible, and it is not urgent.
"""
import argparse
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Reuse make_env_local's own reader + classifier rather than reimplementing
# them. If its rules change (a new kind of non-data path), this follows along.
from make_env_local import _read_env_pairs, _classify  # noqa: E402

_ENV = os.path.join(_REPO, ".env")
_LOCAL = os.path.join(_REPO, ".env.local")


def _last_values(path):
    """key -> value, last occurrence wins (matching dotenv's own behaviour)."""
    out = {}
    for k, v in _read_env_pairs(path):
        out[k] = v
    return out


def _entries(value):
    """A setting may be a comma-separated list of paths (CODE_FOLDERS)."""
    return [p.strip() for p in value.split(",")] if "," in value else [value.strip()]


def _move_tree(src, dst, apply_it):
    """Merge src into dst without ever overwriting. Returns (moved, skipped, errors)."""
    moved, skipped, errors = [], [], []
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        for name in files:
            s = os.path.join(root, name)
            d = os.path.join(target_dir, name)
            if os.path.exists(d):
                skipped.append(d)
                continue
            if not apply_it:
                moved.append(d)
                continue
            try:
                os.makedirs(target_dir, exist_ok=True)
                shutil.move(s, d)
                moved.append(d)
            except Exception as e:
                errors.append(f"{s} -> {d}: {e}")
    return moved, skipped, errors


def main():
    ap = argparse.ArgumentParser(
        description="Move working folders from their old locations into the new data root.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually move files. Without this, nothing is changed.")
    args = ap.parse_args()

    if not os.path.exists(_LOCAL):
        print(f"ERROR: no .env.local at {_LOCAL}")
        print("       Run tools\\make_env_local.py first — without it there is no")
        print("       'new location' to move anything to.")
        return 2
    if not os.path.exists(_ENV):
        print(f"ERROR: no .env at {_ENV}")
        return 2

    old, new = _last_values(_ENV), _last_values(_LOCAL)

    plans = []          # (key, src, dst)
    for key, new_val in new.items():
        if key not in old:
            continue
        for src, dst in zip(_entries(old[key]), _entries(new_val)):
            if not src or not dst or os.path.normcase(src) == os.path.normcase(dst):
                continue
            kind, _ = _classify(src)
            if kind != "data":
                continue          # repo code / installed program — not ours to move
            # isdir() is the ONLY test for "is this a folder we should move".
            # Deliberately not "does the name have an extension" — folder names
            # here include `Holding 2.O`, which that test would wrongly skip.
            # It also excludes file settings (SA_STATE_FILE, MS15_TRACKER_JSON)
            # for free: those travel with the folder their own parent key moves.
            if not os.path.isdir(src):
                continue          # never existed on this machine — nothing to move
            plans.append((key, src, dst))

    # Two settings can point at the same folder; move it once.
    seen, unique = set(), []
    for key, src, dst in plans:
        nc = os.path.normcase(src)
        if nc in seen:
            continue
        seen.add(nc)
        unique.append((key, src, dst))

    if not unique:
        print("\nNothing to move — no old folder from .env exists on this machine.")
        print("That is the expected result on a clean deploy.")
        return 0

    print(f"\n  mode : {'APPLY — files will be moved' if args.apply else 'DRY RUN — nothing will be changed'}")
    print(f"  found: {len(unique)} folder(s) still at their old location\n")

    total_moved = total_skipped = 0
    all_errors = []
    for key, src, dst in unique:
        moved, skipped, errors = _move_tree(src, dst, args.apply)
        total_moved += len(moved)
        total_skipped += len(skipped)
        all_errors += errors
        print(f"  {key}")
        print(f"      from {src}")
        print(f"      to   {dst}")
        print(f"      {len(moved)} file(s)" + (f", {len(skipped)} already at destination (left alone)" if skipped else ""))
        for s in skipped[:5]:
            print(f"        exists: {s}")
        if len(skipped) > 5:
            print(f"        … and {len(skipped) - 5} more")
        for e in errors:
            print(f"        ERROR {e}")

    print(f"\n  {'moved' if args.apply else 'would move'}: {total_moved} file(s)")
    if total_skipped:
        print(f"  left in place (already exist at destination): {total_skipped}")
        print("  Check those by hand — two copies of the same file means one is stale.")
    if all_errors:
        print(f"  errors: {len(all_errors)} — nothing else was affected")
        return 1
    if not args.apply:
        print("\n  Re-run with --apply to move them.")
    else:
        print("\n  Done. The old folders are left behind (now empty) so you can see")
        print("  the move happened. Delete them by hand once you're satisfied.")
        print("  Then: python tools\\doctor.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
