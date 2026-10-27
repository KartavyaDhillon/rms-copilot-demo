"""PHYSICAL DELIVERY - one Run button: pick the files, build PositionBook_With_LTP.xlsx, then send everything.

    1. (optional) Safe test run  -> every e-mail / WhatsApp goes to the tester only, backup skipped
    2. check that all inputs exist and print WHICH files this run will use
    3. copy the latest CTCL vs NMASS output into INPUT (the original stays); the RISK_REPORT is read from COMMON FILES as it is
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

    other = C.busy_other()
    if other:
        _line(f"[FRIENDLY_MSG] Not started - {other} is running right now. Wait for it to finish, then try again.")
        if safe:
            os.environ.pop("PD_TEST_MODE", None)
        raise SystemExit(1)
    ok = False
    C.set_busy("PHYSICAL DELIVERY")
    try:
        _line("[FRIENDLY_MSG] Copying the CTCL vs NMASS output")
        _stage(rep["ctcl"]["path"], C.input_dir(), "FULL_FILE_CTCL_VS_NMASS*")
        for old in glob.glob(os.path.join(C.input_dir(), "RISK_REPORT_*.csv")):   # a stale copy from an older version must not be used
            try:
                os.remove(old)
            except OSError:
                pass

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
        C.clear_busy()
        try:
            C.record_run(ok, pct, test=safe, by=by)
        except Exception:
            pass
        if safe:
            os.environ.pop("PD_TEST_MODE", None)


def run_full_file():
    """'Create full file & e-mail it' button: the first half of PHYSICAL DELIVERY only.

    Same checks and the same calculation as the main Run up to the position report with prices (the full file, not
    split into branch / region / zone), but NOTHING is sent to branches, regions, zones or clients and nothing is
    backed up.  The file is saved as FULLFILE_<cash %>_<HH-MM-SS>.xlsx in 'FULL FILES' and e-mailed to PD_FILE_TO
    (default rms_del@mastertrust.co.in).  0 % is allowed.
    """
    os.environ["PD_FILE_MODE"] = "1"
    C.apply_defaults()
    pct = C.get_percent()
    by = os.getenv("PD_RUN_BY", "")

    other = C.busy_other()
    if other:
        _line(f"[FRIENDLY_MSG] Not started - {other} is running right now. Wait for it to finish, then try again.")
        raise SystemExit(1)

    _line("[FRIENDLY_MSG] Checking the files this run will use")
    _line(f"CASH % : {pct:g}%  (from the popup)")
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
        raise SystemExit(1)

    ok = False
    C.set_busy("the full-file run")
    try:
        _line("[FRIENDLY_MSG] Copying the CTCL vs NMASS output")
        _stage(rep["ctcl"]["path"], C.input_dir(), "FULL_FILE_CTCL_VS_NMASS*")
        for old in glob.glob(os.path.join(C.input_dir(), "RISK_REPORT_*.csv")):
            try:
                os.remove(old)
            except OSError:
                pass

        src = os.path.join(C.base(), "PositionBook_With_LTP.xlsx")
        if os.path.isfile(src):                       # an older file must never be mistaken for this run's
            try:
                os.remove(src)
            except OSError:
                pass

        _line("[FRIENDLY_MSG] Getting NIFTY 500 prices and building the full file")
        from core.pipelines.physdel_codes import ltp
        ltp.main()
        if not os.path.isfile(src) or dt.datetime.fromtimestamp(os.path.getmtime(src)).date() != dt.date.today():
            _line("[FRIENDLY_MSG] The full file was not created - nothing was sent. See the log above.")
            raise SystemExit(1)

        _line("[FRIENDLY_MSG] Saving the full file")
        stamp = dt.datetime.now().strftime("%H-%M-%S")
        name = f"FULLFILE_{pct:g}%_{stamp}.xlsx"
        os.makedirs(C.full_files_dir(), exist_ok=True)
        dst = os.path.join(C.full_files_dir(), name)
        shutil.move(src, dst)                         # moved, so a later main Run can never pick this one up by mistake
        _line(f"Saved : {dst}")

        to, cc = C.email_list("PD_FILE_TO", ["rms_del@mastertrust.co.in"]), C.email_list("PD_FILE_CC")
        if not to:
            _line("[FRIENDLY_MSG] The file is saved but NOT e-mailed - no e-mail address is set (Email To / CC -> Full file).")
            raise SystemExit(1)
        _line(f"[FRIENDLY_MSG] E-mailing the full file to {', '.join(to)}")
        from core.pipelines.physdel_codes import send
        today = dt.date.today().strftime("%d-%m-%Y")
        body = (f"Dear Team,\n\nPlease find attached the full Physical Delivery file "
                f"(CASH {pct:g}%) generated on {today} at {stamp.replace('-', ':')}.\n"
                f"This is the complete file - it has not been split branch / region / zone-wise and nothing "
                f"has been sent to branches or clients.\n\nBest regards,\nRisk Management Team")
        send.send_email_with_attachments(to, cc, f"Physical Delivery full file - CASH {pct:g}% - {today} {stamp.replace('-', ':')}",
                                         body, [dst])
        ok = True
        _line("[FRIENDLY_MSG] Full file e-mailed")

        # Mail is out: back up what this run used and leave nothing stale behind (only after a successful e-mail -
        # if it failed, the PositionBook stays so the button can simply be pressed again).
        _line("[FRIENDLY_MSG] Backing up and clearing the files this run used")
        bk = os.path.join(C.backup_dir(), dt.date.today().strftime("%d-%m-%Y"), f"{stamp}_FULL FILE")
        os.makedirs(bk, exist_ok=True)
        shutil.copy2(dst, bk)                                                   # the finished full file (also stays in FULL FILES)
        moved = 0
        for pat in ("PositionBook*", "FULL_FILE_CTCL_VS_NMASS*"):              # PositionBook(s) + the staged CTCL copy
            for f in glob.glob(os.path.join(C.input_dir(), pat)):
                try:
                    if pat.startswith("PositionBook"):
                        shutil.copy2(f, bk)                                     # backed up ...
                    os.remove(f)                                                # ... and deleted from INPUT
                    moved += 1
                except OSError as e:
                    _line(f"   could not clear {os.path.basename(f)}: {e}")
        _line(f"Backup : {bk}")
        _line(f"INPUT folder cleared ({moved} file(s) removed) - nothing stale is left.")
    finally:
        C.clear_busy()
        os.environ.pop("PD_FILE_MODE", None)
