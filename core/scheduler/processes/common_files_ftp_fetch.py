"""COMMON FILES FTP FETCH - fetches today's HOLDING and RISK_REPORT from the back-office FTP into COMMON FILES.

One run does the whole job by itself:
  * a file already in COMMON FILES (uploaded by hand or fetched earlier) is skipped;
  * a file not on the FTP yet (or still being written) is checked again every 'Retry every' minutes (default 5),
    for up to 'Keep trying for' minutes (default 15);
  * still missing after that -> the run FAILS, ONE alert e-mail goes to the RMS team and the scheduler's failure
    alert (WhatsApp) goes out too;
  * if the previous run is still downloading (big file) this run SKIPS at once - it never starts a second download.
Older files are moved to the BACKUP FILES AUTOMATED folder exactly like a manual upload."""
import time

from core import common_ftp as CF
from core.scheduler import helpers as H
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "COMMON FILES FTP FETCH"


def _minutes(v, default):
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return default


def _fetch(params, ctx):
    K.require_trading_day(ctx)
    if not CF.configured():
        raise RuntimeError("FTP is not set up - add COMMON_FTP_HOST / COMMON_FTP_USER / COMMON_FTP_PASS to the console .env")
    if CF.is_fetching():
        raise H.Skipped("the previous fetch is still downloading - nothing started, the next run will check again.")
    every = max(1, _minutes(params.get("retry_every_min"), 5))
    keep = _minutes(params.get("retry_for_min"), 15)
    t0 = time.time()
    CF.LOG_HOOK = lambda line: ctx.log(line)
    try:
        got = []
        while True:
            res = CF.fetch_once(manual=False)
            files = res.get("files") or {}
            got += [n for n, v in files.items() if (v or {}).get("status") == "downloaded" and n not in got]
            if res.get("ok"):
                break
            left = keep * 60 - (time.time() - t0)
            if left <= 0 or getattr(ctx, "cancelled", False):
                break
            wait = min(every * 60, left)
            ctx.log(f"still missing - {res.get('message', 'files not available yet')} - checking again in {int(wait // 60)} min {int(wait % 60)} s "
                    f"(giving up after {keep} min)")
            end = time.time() + wait
            while time.time() < end and not getattr(ctx, "cancelled", False):
                time.sleep(min(5, max(0.0, end - time.time())))
    finally:
        CF.LOG_HOOK = None
    if res.get("ok"):
        if got:
            return "downloaded: " + ", ".join(got)
        raise H.Skipped("both files are already in COMMON FILES - nothing to download.")
    msg = (res.get("message") or "files not available yet") + f" (tried for {keep} min, every {every} min)"
    if CF.alert_once_if_missing(msg):
        ctx.log("alert e-mail sent to the RMS team")
    raise RuntimeError(msg)


_run = H.logged(FOLDER)(_fetch)


register(
    key="common_files_ftp_fetch",
    name="COMMON FILES FTP FETCH",
    description="Fetches today's HOLDING and RISK_REPORT from the FTP into COMMON FILES (skips files already there, never overlaps itself, alerts if still missing after the cut-off).",
    run=_run,
    params=[Param("retry_every_min", "Retry every (minutes)", "int", default=5, scope="process",
                  help="How often to check the FTP again while a file is not there yet."),
            Param("retry_for_min", "Keep trying for (minutes)", "int", default=15, scope="process",
                  help="After this long, the run fails and ONE alert goes to the RMS team."),
            Param("alert_after", "Alert after (HH:MM) - no longer used", "str", default="08:15", scope="process",
                  help="Old setting, ignored. The run now retries and then alerts by itself.")],
    retry_safe=False, parallel_safe=False, category="Housekeeping", folder=FOLDER, uses=("common_ftp",),
)
