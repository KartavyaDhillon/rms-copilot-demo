"""READ-ONLY: shows exactly what is wrong in .env.  It never changes any file.

Run from the console folder:   .venv\\Scripts\\python tools\\diagnose_env.py

For every statement python-dotenv cannot read it prints the line number, the line itself (secrets masked: only the
setting NAME is shown plus the first/last character of the value) and the lines around it, and also flags
(a) lines where a setting is stuck INSIDE another setting's value, (b) quotes that do not match.
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
SECRET = re.compile(r"PASS|TOKEN|KEY|SECRET|PWD|ANSWER", re.I)


def show(n, text, mark=" "):
    m = KEY.match(text)
    if not m:
        shown = text if len(text) < 90 else text[:90] + "..."
    else:
        name, v = m.group(1), m.group(2)
        if SECRET.search(name):
            v = (v[:1] + "*" * min(max(len(v) - 2, 0), 8) + v[-1:]) if len(v) > 1 else v
        elif len(v) > 110:
            v = v[:60] + " ... " + v[-40:]
        shown = f"{name}={v}"
    print(f"{mark}{n:>5}: {shown}")


def main():
    path = os.path.join(HERE, ".env")
    if not os.path.isfile(path):
        print("No .env in", HERE)
        return 1
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        data = f.read()
    lines = data.splitlines()
    print(f"File: {path}   ({len(lines)} lines)\n")
    found = 0
    try:
        from dotenv.parser import parse_stream
        for b in parse_stream(io.StringIO(data)):
            if b.error:
                found += 1
                s = b.original.line
                print(f"PROBLEM {found}: python-dotenv cannot read the statement starting at line {s}")
                for k in range(max(1, s - 2), min(len(lines), s + 3) + 1):
                    show(k, lines[k - 1], ">>" if k == s else "  ")
                print()
    except Exception as e:
        print("(python-dotenv parser not available:", e, ")")
    print("Other suspicious lines:")
    other = 0
    for i, ln in enumerate(lines, 1):
        m = KEY.match(ln)
        if not m:
            continue
        v = m.group(2).strip()
        if re.search(r"[,'\"\s]SCHED_[A-Z0-9_]+=", v) or re.search(r"[,]\s*[A-Z][A-Z0-9_]{6,}=", v):
            other += 1
            print("  another setting is stuck INSIDE this value:")
            show(i, ln, "  ")
        elif v[:1] in ("'", '"') and v.count(v[0]) % 2 == 1:
            other += 1
            print("  odd number of quotes:")
            show(i, ln, "  ")
    if not other:
        print("  none")
    print("\nDone - nothing was changed." if (found or other) else "\nNo problems found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
