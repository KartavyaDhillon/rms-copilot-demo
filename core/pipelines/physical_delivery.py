"""PHYSICAL DELIVERY - one Run button: pick the files, build PositionBook_With_LTP.xlsx, then send everything.

    1. (optional) Safe test run  -> every e-mail / WhatsApp goes to the tester only, backup skipped
    2. check that all inputs exist and print WHICH files this run will use
    3. copy the latest CTCL vs NMASS output + today's RISK_REPORT into INPUT  (read-only copies - the originals stay)
    4. physdel_codes.ltp   (NIFTY 500 prices, CASH % from the Run popup)  -> PositionBook_With_LTP.xlsx
    5. physdel_codes.send  (branch / region / zone mails, client WhatsApp, ALERT 1 CR, logs, backup)

The run is NEVER blocked by the date - the 5-days-before-expiry window is shown on the card only.
"""
from __future__ import annotations

import datetime as dt
import glob
import os
import shutil
import sys

from core.pipelines import physdel_cfg as C


def _line(txt=""):
    print(txt, flush=True)


def _describe(label, info):
    if not info:
        return f"   {label:<22}: NOT FOUND"
    return f"   {label:<22}: {info['name']}   [{info['where']}, saved {info['mtime'].replace('T', ' ')}]"


def _stage(src, dst_dir, pattern_to_clear):
    os.makedirs(dst_dir, exist_ok=True)
    for old in glob.glob(os.path.join(dst_dir, pattern_to_clear)):
        try:
            os.remove(old)
        except OSError:
            pass
    dst = os.path.join(dst_dir, os.path.basename(src))
    shutil.copyfile(src, dst)                     # copyfile (not copy2) => fresh modified time
    return dst


def run_physical_delivery():
    C.apply_defaults()
    pct = C.get_percent()
    safe = C.consume_safe_test()
    by = os.getenv("PD_RUN_BY", "")

    if safe:
        to, wa = C.test_email_to(), C.test_wa_to()
        digits = "".join(ch for ch in wa if ch.isdigit())
        if len(digits) < 10 or not to:
            _line("[FRIENDLY_MSG] Safe test run was stopped before sending anything - the test e-mail / WhatsApp "
                  "number is not set (PD_TEST_EMAIL_TO / PD_TEST_WA_TO in .env).")
            os.environ.pop("PD_TEST_MODE", None)
            raise SystemExit(1)
        _line("=" * 78)
        _line("SAFE TEST RUN - everything really runs, but:")
        _line(f"     every client WhatsApp  ->  {wa}   (first {C.test_max_clients()} clients only)")
        _line(f"     every e-mail           ->  {', '.join(to)}   (CC dropped, subject tagged [TEST - IGNORE])")
        _line("     backup                 ->  SKIPPED (your files stay for the real run)")
        _line("   This arm is spent by this one run.")
        _line("=" * 78)

    _line(f"[FRIENDLY_MSG] Checking the files this run will use")
    win = C.window_info()
    _line(f"Window : {win['text']}")
    _line(f"CASH % : {pct:g}%  (from the Run popup)")
    rep = C.inputs_report()
    _line("Files this run will use:")
    _line(_describe("PositionBook", rep["positionbook"]))
    _line(_describe("CTCL vs NMASS output", rep["ctcl"]))
    _line(_describe("Risk report", rep["risk"]))
    for m in rep["masters"]:
        _line(f"   {m['label']:<22}: {m['name']}   [{'master files' if m['present'] else 'NOT FOUND'}]")

    miss = C.missing_inputs(rep)
    if miss:
        for m in miss:
            _line(f"[MISSING_FILE] {m}")
        _line("[FRIENDLY_MSG] The run stopped - pre-run file check failed (see the list above).")
        if safe:
            os.environ.pop("PD_TEST_MODE", None)
        raise SystemExit(1)

    ok = False
    try:
        _line("[FRIENDLY_MSG] Copying the CTCL vs NMASS output and today's risk report")
        _stage(rep["ctcl"]["path"], C.input_dir(), "FULL_FILE_CTCL_VS_NMASS*")
        _stage(rep["risk"]["path"], C.input_dir(), "RISK_REPORT_*.csv")

        _line("[FRIENDLY_MSG] Getting NIFTY 500 prices and building the position report")
        from core.pipelines.physdel_codes import ltp
        ltp.main()
        out = os.path.join(C.base(), "PositionBook_With_LTP.xlsx")
        if not os.path.isfile(out) or dt.datetime.fromtimestamp(os.path.getmtime(out)).date() != dt.date.today():
            _line("[FRIENDLY_MSG] The position report was not created - nothing was sent. See the log above.")
            raise SystemExit(1)

        _line("[FRIENDLY_MSG] Sending branch, region and zone mails, client WhatsApp and alerts")
        from core.pipelines.physdel_codes import send
        send.main()
        if send.STEP_ERRORS:
            _line("[FRIENDLY_MSG] Finished with errors in: " + "; ".join(send.STEP_ERRORS))
            raise SystemExit(1)
        ok = True
        _line("[FRIENDLY_MSG] PHYSICAL DELIVERY finished")
    finally:
        try:
            C.record_run(ok, pct, test=safe, by=by)
        except Exception:
            pass
        if safe:
            os.environ.pop("PD_TEST_MODE", None)
