"""Finds the lines of the .env file(s) that python-dotenv cannot read ("could not parse statement starting at line N").

Such a line is IGNORED by the console - the setting on it is silently not loaded.  This tool lists them, with the
value hidden (only the name and the first / last character are shown), so it is safe to copy the output.

Run from the console folder:   .venv\\Scripts\\python tools\\check_env.py
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mask(text: str) -> str:
    """NAME=v******x  - never shows the real value."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    if "=" not in first:
        return first[:25] + ("..." if len(first) > 25 else "")
    k, v = first.split("=", 1)
    v = v.strip()
    return f"{k.strip()}={v[:1]}{'*' * min(max(len(v) - 2, 0), 12)}{v[-1:] if len(v) > 1 else ''}"


def _why(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    if "=" not in first:
        return "no '=' sign in the line"
    k, v = first.split("=", 1)
    if not k.strip() or " " in k.strip():
        return "the NAME before '=' is empty or has spaces"
    v = v.strip()
    if v[:1] in ("'", '"') and v.count(v[0]) < 2:
        return "a quote was opened but never closed"
    if v[:1] in ("'", '"') and v.count(v[0]) >= 2 and v[v.rindex(v[0]) + 1:].strip() not in ("",) and not v[v.rindex(v[0]) + 1:].strip().startswith("#"):
        return "extra text after the closing quote"
    return "special characters - put the whole value inside 'single quotes'"


def main() -> int:
    try:
        from dotenv.parser import parse_stream
    except Exception as e:
        print("python-dotenv is not available in this Python:", e)
        return 1
    bad = 0
    for name in (".env", ".env.local"):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            data = f.read()
        for b in parse_stream(io.StringIO(data)):
            if b.error:
                bad += 1
                print(f"{name} line {b.original.line}:  {_mask(b.original.string)}    <- {_why(b.original.string)}")
    print("\nAll lines can be read." if not bad else
          f"\n{bad} line(s) cannot be read. Open the file in Notepad, go to that line number and put the value in 'single quotes'.")
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
