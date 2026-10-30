"""76 FILES DOWNLOADER - the previous trading day's NSE (CM/FO/CO/CD), MCX, NCDEX and EPI files into
<console data>\\76 FILES DOWNLOADER\\BACKUPFILES AUTOMATED\\<dd-mm-yyyy>\\ (nothing is copied anywhere else).

All the logic is in core/files70.py; this file only connects it to the scheduler. Schedule it once in the morning.
It starts the same download as the card's button (or waits for one that is already running), then checks the day
folder: if any file is still missing the run FAILS and the scheduler's WhatsApp failure alert names the missing files.
Both NCDEX captchas are answered by the auto-solver - nothing has to be typed."""
from core import files70 as F
from core.scheduler import helpers as H
from core.scheduler.catalog import register
from core.scheduler.processes import _kit as K

FOLDER = "76 FILES DOWNLOADER"


@H.logged(FOLDER)
def _run(params, ctx):
    K.require_trading_day(ctx)
    d = F.default_date()
    ctx.log(f"Trade date: {F.dstr(d)} (previous trading day). Folder: {F.day_dir(d)}")
    cancelled = lambda: bool(getattr(ctx, "cancelled", False))
    if F.is_running():
        ctx.log("A download is already running (started from the card) - waiting for it to finish.")
        F.wait(cancelled=cancelled)
    else:
        st = F.status(d)
        if st["found"] >= st["total"]:
            raise H.Skipped(f"All {st['total']} files of {F.dstr(d)} are already in the day folder.")
        res = F.start(d, by=f"scheduler ({ctx.schedule_name})", trigger="scheduled")
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "the download could not be started")
        F.wait(cancelled=cancelled, on_line=ctx.log)
    st = F.status(d)
    if st["found"] >= st["total"]:
        return f"{F.dstr(d)}: all {st['total']} files downloaded"
    missing = []
    for g in st["groups"]:
        names = [f["name"] for f in g["files"] if not f["present"]]
        if names:
            missing.append(f"{g['label']}: " + ", ".join(names[:4]) + (f" +{len(names) - 4} more" if len(names) > 4 else ""))
    raise RuntimeError(f"76 FILES {F.dstr(d)}: {st['total'] - st['found']} of {st['total']} files missing - " + " | ".join(missing))


register(
    key="files_70_downloader",
    name="76 FILES DOWNLOADER",
    description="Downloads the previous trading day's NSE CM/FO/CO/CD, MCX, NCDEX (auto captcha) and EPI files into one "
                "day folder; fails with the list of missing files so the WhatsApp alert says what is not there.",
    run=_run,
    params=[],
    retry_safe=True, parallel_safe=False, category="Downloads", folder=FOLDER,
    uses=("nse", "mcx_sftp", "backoffice"),
)
