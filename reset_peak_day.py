"""
Reset today's peak-margin state (report + run log + images).
Run:  python reset_peak_day.py
"""
import os
from core.pipelines.peak_margin import FOLDER

targets = ["peak_margin_report.json", "peak_runs.json",
           "F_filtered.png", "C_filtered.png",
           "F_filtered.jpg", "C_filtered.jpg"]   # old jpgs too, just in case
removed = []
for name in targets:
    p = os.path.join(FOLDER, name)
    if os.path.exists(p):
        os.remove(p)
        removed.append(name)

print("Reset complete. Removed:", removed if removed else "(nothing to remove)")
print("Next run will start fresh at '1 of 4'.")
