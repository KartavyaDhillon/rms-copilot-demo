"""Repairs the broken multi-line lines in .env (the 'could not parse statement starting at line N' warnings).

What went wrong: client-code lists (…_ACCOUNTS / …_CODES) were saved ONE CODE PER LINE inside quotes, and a later save
pushed another setting (…_ALERT_PHONES) into the middle of them.  This tool puts every such list back on ONE line
(duplicates removed, order kept) and moves the stray settings out.  Nothing else in the file is touched.

Run from the console folder (the folder that holds .env):
    .venv\\Scripts\\python tools\\fix_env.py            -> shows what it WOULD change (nothing is written)
    .venv\\Scripts\\python tools\\fix_env.py --apply    -> makes a backup  .env.bak_YYYYMMDD_HHMMSS  and fixes .env
Then:  .venv\\Scripts\\python tools\\check_env.py   (should say "All lines can be read.")
"""
import datetime as dt
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _closed(value: str) -> bool:
    """True when a value that starts with a quote also closes it (on the same line)."""
    v = value.strip()
    if v[:1] not in ("'", '"'):
        return True
    q, k = v[0], 1
    while k < len(v):
        if v[k] == "\\":
            k += 2
            continue
        if v[k] == q:
            return True
        k += 1
    return False


def fix(lines):
    out, report, i = [], [], 0
    while i < len(lines):
        m = KEY.match(lines[i])
        if not m or _closed(m.group(2)):
            out.append(lines[i])
            i += 1
            continue
        key, first = m.group(1), m.group(2).strip()[1:]                     # opened a quote, never closed
        tokens = [t for t in re.split(r"[,\s]+", first.strip("'\"")) if t]
        moved, j = [], i + 1
        while j < len(lines):
            ln = lines[j]
            if not ln.strip():                                              # blank line inside the mess
                j += 1
                continue
            if ln.lstrip().startswith("#"):                                 # next section: the mess is over
                break
            km = KEY.match(ln)
            if km and not _closed(km.group(2)):                             # the NEXT broken list starts here
                break
            if km:                                 # a complete setting pushed into the list
                moved.append(ln)
                j += 1
                continue
            tokens += [t for t in re.split(r"[,\s]+", ln.strip().strip("'\"")) if t]    # a leftover code line
            j += 1
        seen, uniq = set(), []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        out.append(f"{key}='{','.join(uniq)}'")
        out.extend(moved)
        report.append(f"{key}  ->  {','.join(uniq)}" + (f"   (moved {len(moved)} stray setting line(s) out)" if moved else ""))
        i = j
    return out, report


def main(argv):
    path = os.path.join(HERE, ".env")
    if not os.path.isfile(path):
        print("No .env in", HERE)
        return 1
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    out, report = fix(raw.splitlines())
    if not report:
        print("Nothing to fix - no broken multi-line values found.")
        return 0
    print("Will fix:")
    for r in report:
        print("  ", r)
    if "--apply" not in argv:
        print("\n(Dry run - nothing written.  Run again with --apply to fix the file.)")
        return 0
    bak = path + ".bak_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(bak, "w", encoding="utf-8", newline="") as f:
        f.write(raw)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join(out) + nl)
    print("\nFixed.  Backup of the old file:", bak)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
