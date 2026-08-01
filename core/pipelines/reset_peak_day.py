"""
Reset today's peak-margin state (report + run log + images).

Use this when you want a CLEAN test: delete the local NSE files AND run this,
so the report starts blank and the counter starts at 0 of 4.

Run:  python reset_peak_day.py
"""
import os
from core.pipelines.peak_margin import FOLDER

targets = ["peak_margin_report.json", "peak_runs.json",
           "F_filtered.jpg", "C_filtered.jpg"]
removed = []
for name in targets:
    p = os.path.join(FOLDER, name)
    if os.path.exists(p):
        os.remove(p)
        removed.append(name)

print("Reset complete. Removed:", removed if removed else "(nothing to remove)")
print("Next run will start fresh at '1 of 4'.")
