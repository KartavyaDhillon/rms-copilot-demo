"""
Test the peak-margin pipeline end to end (download -> compute), no sending.

Run from project root:  python run_peak_margin.py

This DOWNLOADS today's files from NSE, then computes the peaks and prints them.
It sends NOTHING. Safe to run.
"""
import json
from core.downloaders.nse import download_peak_margin_files
from core.pipelines.peak_margin import compute_peak_margin, FOLDER

print("Step 1: downloading today's files from NSE...")
dl = download_peak_margin_files(FOLDER)
print(f"  FO files: {len(dl['fo_paths'])}, CM files: {len(dl['cm_paths'])}")

print("\nStep 2: computing peak margin...")
result = compute_peak_margin(dl["fo_paths"], dl["cm_paths"])

print("\nRESULT:")
print(json.dumps(result, indent=2, default=str))
