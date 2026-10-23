"""One-off repair of the single broken PEAK MARGIN line in .env (the one `diagnose_env.py` reports).

Replaces the line that starts with SCHED_PEAK_MARGIN_REPORT_FO_CODES= by the two correct lines, after making a backup.
Does nothing (and says so) if that line is not found exactly once.

    .venv\\Scripts\\python tools\\fix_peak_line.py
"""
import datetime as dt
import os
import shutil

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, ".env")
START = "SCHED_PEAK_MARGIN_REPORT_FO_CODES="
GOOD = [
    "SCHED_PEAK_MARGIN_REPORT_FO_CODES='JDA563,JDA937,JDA1209,JDA1210,JDA1260'",
    "SCHED_PEAK_MARGIN_REPORT_CM_CODES='06436,PRO_06436,6436'",
]

with open(PATH, encoding="utf-8", newline="") as f:
    raw = f.read()
nl = "\r\n" if "\r\n" in raw else "\n"
lines = raw.split(nl) if nl == "\r\n" else raw.split("\n")
hits = [i for i, l in enumerate(lines) if l.startswith(START)]
print("lines found:", len(hits))
if len(hits) != 1:
    print("Nothing changed (expected exactly 1).")
else:
    i = hits[0]
    print("old line", i + 1, ":", lines[i][:60] + " ...")
    if any(l.startswith("SCHED_PEAK_MARGIN_REPORT_CM_CODES=") for l in lines):
        GOOD = GOOD[:1]                                   # a separate CM_CODES line already exists - keep it
    bak = PATH + ".bak_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy(PATH, bak)
    lines[i:i + 1] = GOOD
    with open(PATH, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join(lines))
    print("FIXED.  Backup:", bak)
