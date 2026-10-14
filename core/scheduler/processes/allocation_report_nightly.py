"""ALLOCATION REPORT (nightly) — the allocation log report for everything since the LAST nightly close, up to the moment
this schedule fires (e.g. yesterday 23:59 → today 23:59). E-mails it, backs it up and moves the day marker.

The work is done by core/pipelines/allocation_report.py in a separate Python process (so its module-level state never
touches the console).  Manual / up-to-now / custom-range / client-code reports are the Process Center tile
"Allocation Report" — they never move the marker.

The script itself ships in the repo (core/pipelines/allocation_report_src.py). Setup once: copy window_state.json,
processed_uidls.json and open_window_uidls.json from the old machine into the folder shown in the log line "folder:",
and save the mailbox logins with the 🔑 Credentials button of the Allocation Report tile. The nightly recipients are the
To / Cc of this process (Recipients button).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from core.scheduler import helpers as H
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "ALLOCATION REPORT"
REPO = Path(__file__).resolve().parents[3]


@H.logged(FOLDER)
def _run(params, ctx):
    K.require_trading_day(ctx)
    to, cc = K.recipients(params)
    opts = {"mode": "close", "to": ", ".join(to), "cc": ", ".join(cc), "by": "scheduler"}
    code = ("from core.pipelines.allocation_report import run_cli as _f; _f(" + repr(json.dumps(opts)) + ")")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(REPO)}
    try:
        from core.pipelines import allocation_report as _ar
        ctx.log(f"folder: {_ar.base_dir()}")
    except Exception:
        pass
    ctx.log("Starting the nightly allocation report (last close → now)…")
    proc = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=str(REPO), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
    result, last_note = None, ""
    try:
        for line in proc.stdout:
            s = line.rstrip()
            if not s:
                continue
            if s.startswith("ALLOC_RESULT "):
                try:
                    result = json.loads(s[len("ALLOC_RESULT "):])
                except Exception:
                    pass
                continue
            if s.startswith("[FRIENDLY_MSG]"):
                last_note = s[len("[FRIENDLY_MSG]"):].strip()
            ctx.log(s)
            if getattr(ctx, "cancelled", False):
                proc.kill()
                break
    finally:
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(last_note or f"The allocation report run failed (exit code {rc}) — see the log above.")
    if result and result.get("empty"):
        return result.get("msg") or "No allocation e-mails in the window — nothing sent"
    if result:
        return (f"Report sent ({result.get('emails', 0)} e-mails read, {result.get('success_rows', 0)} rows) · "
                f"window {result.get('from')} → {result.get('to')}")
    return "Allocation report finished"


register(
    key="allocation_report_nightly",
    name="ALLOCATION REPORT",
    description="Nightly allocation log report: every allocation e-mail since the last close (e.g. yesterday 23:59) up to "
                "the time this schedule runs, with the opening balances from the CLI backup. E-mails the Downwards, "
                "Upwards and Allocation_Log_Report files. On-demand reports (up to now, a time, or a custom range, "
                "with client codes) are in the Process Center.",
    run=_run,
    params=K.mail_params("rms_del@mastertrust.co.in", "", with_wait=False),
    retry_safe=False, parallel_safe=False, category="Reports", folder=FOLDER, uses=(),
)
