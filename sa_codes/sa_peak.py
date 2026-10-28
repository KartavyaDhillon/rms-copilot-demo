"""sa_codes/sa_peak.py - which peak-file number each segment uses for a Short Allocation run (1..8).

The exchange publishes NSE CASH (C) and NSE FO (F) peak files only up to number 4 (SA_PEAK_CAP_CF, default 4), so
runs 5-8 reuse the 4th C/F file. The other files (NSE CO, MCX peak, NCCL/NCDEX peak) go up to 8 (SA_PEAK_CAP_CO /
SA_PEAK_CAP_MCX, default 8). Used by the downloader AND the processing script so both always agree.
"""
import glob
import os
import shutil

MAX_RUN = 8


def _cap(name, default):
    try:
        return max(1, min(MAX_RUN, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def suffixes(run_no):
    """-> (cf_i, co_i, mcx_i): e.g. run 6 -> ('i04', 'i06', '06')."""
    n = int(run_no)
    if not 1 <= n <= MAX_RUN:
        raise ValueError(f"run {n} is outside 1..{MAX_RUN}")
    cf = min(n, _cap("SA_PEAK_CAP_CF", 4))
    co = min(n, _cap("SA_PEAK_CAP_CO", 8))
    mx = min(n, _cap("SA_PEAK_CAP_MCX", 8))
    return f"i{cf:02d}", f"i{co:02d}", f"{mx:02d}"


def reuses_cf(run_no):
    """True when this run uses an EARLIER run's C/F file (the 4th) instead of its own."""
    return suffixes(run_no)[0] != f"i{int(run_no):02d}"


def restore_cf_from_backup(output_dir, cf_i, today_ddmmyyyy_dashed, log=print):
    """Run 4's processing MOVES its C/F files into BACKUP FILES AUTOMATED/<dd-mm-yyyy>/04/. A later run that reuses
    them copies them back (copy - the backup stays whole). Returns the file names put back."""
    n = int(cf_i[1:])
    src = os.path.join(output_dir, "BACKUP FILES AUTOMATED", today_ddmmyyyy_dashed, f"{n:02d}")
    back = []
    for seg in ("C", "F"):
        if glob.glob(os.path.join(output_dir, f"{seg}_SA01_P_06436_*_{cf_i}.lis")):
            continue
        hits = glob.glob(os.path.join(src, f"{seg}_SA01_P_06436_*_{cf_i}.lis"))
        if hits:
            try:
                shutil.copy2(hits[0], os.path.join(output_dir, os.path.basename(hits[0])))
                back.append(os.path.basename(hits[0]))
                log(f"Reusing the {n}th peak file: {os.path.basename(hits[0])} (copied back from {src})")
            except OSError as e:
                log(f"Could not copy {hits[0]} back: {e}")
    return back
