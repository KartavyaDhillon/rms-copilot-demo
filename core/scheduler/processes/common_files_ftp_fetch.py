"""COMMON FILES FTP FETCH - fetches today's HOLDING and RISK_REPORT from the back-office FTP into COMMON FILES.

Schedule it every 5 minutes from 07:30 (e.g. 07:30-08:15). It is safe to repeat:
  * a file already in COMMON FILES (uploaded by hand or fetched earlier) is skipped;
  * if the previous run is still downloading (big file) this run SKIPS at once - it never starts a second download;
  * a file the back office is still writing is retried by the next run;
  * after 'Alert after' (default 08:15) one alert e-mail goes out if something is still missing.
Older files are moved to the BACKUP FILES AUTOMATED folder exactly like a manual upload."""
import datetime as dt

from core import common_ftp as CF
from core.scheduler import helpers as H
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "COMMON FILES FTP FETCH"


@H.logged(FOLDER)
def _run(params, ctx):
    K.require_trading_day(ctx)
    if not CF.configured():
        raise RuntimeError("FTP is not set up - add COMMON_FTP_HOST / COMMON_FTP_USER / COMMON_FTP_PASS to the console .env")
    if CF.is_fetching():
        raise H.Skipped("the previous fetch is still downloading - nothing started, the next run will check again.")
    try:
        hh, mm = (params.get("alert_after") or "08:15").strip().split(":")
        alert_at = dt.time(int(hh), int(mm))
    except Exception:
        alert_at = dt.time(8, 15)
    CF.LOG_HOOK = lambda line: ctx.log(line)
    try:
        res = CF.fetch_once(manual=False)
    finally:
        CF.LOG_HOOK = None
    files = res.get("files") or {}
    got = [n for n, v in files.items() if (v or {}).get("status") == "downloaded"]
    if res.get("ok"):
        if got:
            return "downloaded: " + ", ".join(got)
        raise H.Skipped("both files are already in COMMON FILES - nothing to download.")
    late = dt.datetime.now().time() >= alert_at
    if late and CF.alert_once_if_missing(res.get("message", "")):
        ctx.log("alert e-mail sent to the RMS team")
    msg = res.get("message") or "files not available yet"
    if late:
        raise RuntimeError(msg)
    raise H.Skipped(msg + " - will try again at the next run.")


register(
    key="common_files_ftp_fetch",
    name="COMMON FILES FTP FETCH",
    description="Fetches today's HOLDING and RISK_REPORT from the FTP into COMMON FILES (skips files already there, never overlaps itself, alerts if still missing after the cut-off).",
    run=_run,
    params=[Param("alert_after", "Alert after (HH:MM)", "str", default="08:15", scope="process",
                  help="If a file is still missing after this time, ONE alert e-mail goes to the RMS team.")],
    retry_safe=True, parallel_safe=False, category="Housekeeping", folder=FOLDER,
)
