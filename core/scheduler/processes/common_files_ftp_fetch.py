"""COMMON FILES FTP FETCH - fetches today's HOLDING and RISK_REPORT from the back-office FTP into COMMON FILES.

Keep ONE daily schedule (e.g. 07:40). One run does the whole job:
  * a file already in COMMON FILES (uploaded by hand or fetched earlier) is skipped;
  * a file that is not on the FTP yet (or still being written) is checked again after 5 minutes, up to 2 retries;
  * still missing after that -> the run FAILS, and the scheduler's common WhatsApp failure alert goes out saying that
    the files have not been uploaded to the console and must be uploaded fast. No e-mail is sent;
  * if the previous run is still downloading (big file) this run SKIPS at once - it never starts a second download.
Older files are moved to the BACKUP FILES AUTOMATED folder exactly like a manual upload."""
import re
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


def _kind_label(name):
    """'RISK_REPORT_30102026.csv' -> 'RISK REPORT'."""
    return re.sub(r"_?\d{8}\.\w+$", "", name or "").replace("_", " ").strip().upper() or (name or "")


def _missing_text(files):
    bad = [n for n, v in (files or {}).items() if (v or {}).get("status") not in ("downloaded", "already there")]
    kinds = sorted({_kind_label(n) for n in bad})
    return (" and ".join(kinds) if kinds else "HOLDING and RISK REPORT"), bad


def _fetch(params, ctx):
    K.require_trading_day(ctx)
    if not CF.configured():
        raise RuntimeError("FTP is not set up - add COMMON_FTP_HOST / COMMON_FTP_USER / COMMON_FTP_PASS to the console .env")
    if CF.is_fetching():
        raise H.Skipped("the previous fetch is still downloading - nothing started, the next run will check again.")
    every = max(1, _minutes(params.get("retry_every_min"), 5))
    retries = _minutes(params.get("retries"), 2)
    CF.LOG_HOOK = lambda line: ctx.log(line)
    try:
        got, tries = [], 0
        while True:
            res = CF.fetch_once(manual=False)
            files = res.get("files") or {}
            got += [n for n, v in files.items() if (v or {}).get("status") == "downloaded" and n not in got]
            if res.get("ok") or tries >= retries or getattr(ctx, "cancelled", False):
                break
            tries += 1
            ctx.log(f"still missing - {res.get('message', 'files not available yet')} - retry {tries} of {retries} in {every} min")
            end = time.time() + every * 60
            while time.time() < end and not getattr(ctx, "cancelled", False):
                time.sleep(min(5, max(0.0, end - time.time())))
    finally:
        CF.LOG_HOOK = None
    if res.get("ok"):
        if got:
            return "downloaded: " + ", ".join(got)
        raise H.Skipped("both files are already in COMMON FILES - nothing to download.")
    what, bad = _missing_text(res.get("files"))
    raise RuntimeError(f"{what} have not been uploaded to the console - please upload them fast. "
                       f"(Missing: {', '.join(bad) or 'files not on the FTP'}; checked {tries + 1} times, {every} min apart)")


_run = H.logged(FOLDER)(_fetch)


register(
    key="common_files_ftp_fetch",
    name="COMMON FILES FTP FETCH",
    description="Fetches today's HOLDING and RISK_REPORT from the FTP into COMMON FILES (skips files already there, never overlaps itself; retries after 5 min, max 2 times, then the WhatsApp failure alert).",
    run=_run,
    params=[Param("retry_every_min", "Retry after (minutes)", "int", default=5, scope="process",
                  help="How long to wait before checking the FTP again when a file is not there yet."),
            Param("retries", "Retries (max)", "int", default=2, scope="process",
                  help="How many more times to check. Still missing after that -> the WhatsApp failure alert.")],
    retry_safe=False, parallel_safe=False, category="Housekeeping", folder=FOLDER, uses=("common_ftp",),
)
