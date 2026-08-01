"""
core/pipelines/probable_full.py — the Probable Defaulter box's single "Run"
button chains STEP 1 (reconciliation, code1.py) then STEP 2 (alerts, code2.py)
in one subprocess, exactly the way core/pipeline_box.stream_pipeline() expects
every pipe's "func" to work (no arguments, everything else driven by env vars
or its own pre-run file check).

The one piece of per-run operator input the reference script's code2.py used
to collect via a desktop customtkinter popup — the market session time — is
threaded through here via the PROBABLE_MARKET_TIME env var instead: the
dashboard's web modal (process-probable-box.js) collects it just before Run
is clicked, and api.py's /px/run-stream passes it through stream_pipeline()'s
`extra_env` into THIS subprocess's environment (see core/pipeline_box.py's
stream_pipeline signature and PIPELINES["probable"]'s module/func wiring).
Downloading the NMASS/NCDEX/TCS BANCS input files is intentionally NOT part
of this chain — those are their own separate buttons (reusing
core/pipelines/nmass_download.py with pipe="probable" and
core/pipelines/probable_tcs_bancs.py), meant to be run ahead of time (e.g.
~8:20 AM) per the operator's own instruction, exactly like every other
pipeline here. Run simply fails with a clear "Missing required files:"
message (via probable_reconcile.py's _check_required_files) if they aren't
there yet — same convention as CTCL vs NMASS / Margin Shortage.
"""
import os

from core.pipelines.probable_reconcile import run_probable_reconcile
from core.pipelines.probable_alerts import run_probable_alerts


def _archive_to_db(tag, kind, filepath, max_retries=3, retry_delay=2):
    """Copy a file's bytes into PostgreSQL with retry. Never raises. Same
    pattern as ctcl_vs_nmass._archive_to_db/margin_shortage_15min._archive_to_db
    — PROBABLE never had one of these before (its two backup steps,
    probable_reconcile.py's and probable_alerts.py's, only ever moved files on
    disk, never archived them to Postgres), so this is new, used only by
    backfill_run_to_db() below for now."""
    if not filepath or not os.path.exists(filepath):
        return False
    use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
    if not use_pg:
        print(f"[probable_full] ⚠️  USE_POSTGRES=false — {os.path.basename(filepath)} NOT archived.")
        return False
    import time as _time
    fname = os.path.basename(filepath)
    try:
        from core.db_postgres import save_run_file
        with open(filepath, "rb") as f:
            content = f.read()
    except Exception as e:
        print(f"[probable_full] WARNING could not read {filepath} for archiving: {e}")
        return False
    for attempt in range(1, max_retries + 1):
        try:
            save_run_file("probable", kind, fname, content, tag=tag)
            print(f"[probable_full] archived {fname} ({len(content):,} bytes) -> PostgreSQL")
            return True
        except Exception as e:
            if attempt < max_retries:
                print(f"[probable_full] archive attempt {attempt}/{max_retries} failed for {fname}: {e} — retrying in {retry_delay}s")
                _time.sleep(retry_delay)
            else:
                print(f"[probable_full] WARNING: {fname} could NOT be archived after {max_retries} attempts: {e}")
    return False


def backfill_run_to_db(date_str, subfolder=None):
    """Push an ALREADY-backed-up PROBABLE day's folder into PostgreSQL — for
    when a run genuinely completed OUTSIDE this dashboard and its files were
    copied by hand into this machine's own backup folder.

    Unlike ctcl/ms15/sa, PROBABLE has no two-digit run number (it runs once/
    day) — instead probable_reconcile.py and probable_alerts.py each back up
    into their OWN named subfolder under the SAME date folder:
    BACKUPFILES AUTOMATED\\<date>\\RECONCILIATION\\ and \\SHORTAGE\\.
    Also note PROBABLE's date-folder format is DIFFERENT from every other
    pipe's DD-MM-YYYY — it's "%d %b %Y" upper-cased, e.g. "21 JUL 2026"
    (see probable_reconcile.py / probable_alerts.py's own backup steps).

    `subfolder` — "RECONCILIATION" or "SHORTAGE" to do just one; omit/None to
    do both (whichever of the two actually exists on disk).
    Safe to call more than once — re-archiving just adds another row."""
    base_folder = os.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    subfolders = [subfolder] if subfolder else ["RECONCILIATION", "SHORTAGE"]
    archived, skipped, folders_used = [], [], []
    any_found = False
    for sf in subfolders:
        folder = os.path.join(base_folder, "BACKUPFILES AUTOMATED", date_str, sf)
        if not os.path.isdir(folder):
            continue
        any_found = True
        folders_used.append(folder)
        db_tag = f"{date_str}/{sf}"
        for fname in sorted(os.listdir(folder)):
            fpath = os.path.join(folder, fname)
            if not os.path.isfile(fpath):
                continue
            if _archive_to_db(db_tag, sf.lower(), fpath):
                archived.append(fname)
            else:
                skipped.append(fname)
    if not any_found:
        return {"ok": False, "error": f"No RECONCILIATION/SHORTAGE folder found under "
                                       f"{os.path.join(base_folder, 'BACKUPFILES AUTOMATED', date_str)}"}
    return {"ok": True, "folders": folders_used, "archived": archived, "skipped": skipped}


def run_probable_full():
    market_time = os.getenv("PROBABLE_MARKET_TIME", "9:30 AM").strip() or "9:30 AM"

    # ── SAFE TEST RUN: read the arm here, consume it the moment the pre-run
    # file check passes (via run_probable_reconcile's on_checks_passed hook).
    # Consuming late means a run that aborts for missing files leaves the arm
    # intact — otherwise the operator's next click would silently be a LIVE run
    # they believed was still a test. See the long block in probable_alerts.py.
    from core.pipelines.probable_alerts import (
        is_safe_test_armed, disarm_safe_test, safe_test_targets,
    )
    safe_test = is_safe_test_armed()

    if safe_test:
        _test_email, _test_wa = safe_test_targets()
        # Refuse to start a test that isn't actually safe. A blank/short test
        # number or a missing test email would mean "redirect to nowhere" —
        # and depending on which one is blank, the messages could fall back to
        # real recipients. Better to stop and say so.
        _digits = "".join(ch for ch in _test_wa if ch.isdigit())
        if len(_digits) < 11 or not _test_email:
            print("[FRIENDLY_MSG] Safe test run is armed but its redirect targets are not "
                  "configured properly, so the run was stopped before sending anything.")
            print(f"   PROBABLE_TEST_WA_TO    = {_test_wa!r}  (need a valid +91 number)")
            print(f"   PROBABLE_TEST_EMAIL_TO = {_test_email!r}")
            print("   Fix these in .env and restart the server, then arm the test again.")
            raise SystemExit(1)
        print("=" * 78)
        print("🧪 SAFE TEST RUN — this run is a REHEARSAL. Everything really sends, but:")
        print(f"     every client WhatsApp  →  {_test_wa}   (no client is messaged)")
        print(f"     every email            →  {', '.join(_test_email)}   (CC dropped)")
        print("     subjects tagged        →  [TEST — IGNORE]")
        print("     backup & cleanup       →  SKIPPED (your files stay put for the real run)")
        print("   This arm is spent by this one run.")
        print("=" * 78)

    print(f"🚀 Probable Defaulter — full run starting (market time: {market_time})")
    run_probable_reconcile(
        safe_test=safe_test,
        on_checks_passed=(disarm_safe_test if safe_test else None),
    )
    run_probable_alerts(market_time=market_time, safe_test=safe_test)
    print("✅ Probable Defaulter — full run completed"
          + (" (SAFE TEST — nothing reached any client or branch)" if safe_test else ""))


if __name__ == "__main__":
    run_probable_full()
