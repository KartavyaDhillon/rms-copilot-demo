"""MCX PEAK MARGIN UPLOAD — port of D:\\MCX PEAK FILES UPLOAD\\mcx_peakmargin_zip_upload.py (engine: core.scheduler.peak_upload).

Trade date = T-2 on the holiday calendar. Zip MCX_PEAKMARGIN_<DDMMYYYY>.zip = the 8 files
MCX_PeakMargin<user>_<yyyymmdd>_01..08.csv (all mandatory):
    1) MCX file portal folder /<dd-mm-yyyy> (password + TOTP from Scheduler > Connections > MCX file portal)
    2) the 76 FILES DOWNLOADER day folder \\MCX
uploaded to customFileImport (fileName = MCX_PEAKMARGIN_DDMMYYYY.zip, date = the trade date)."""
import datetime as dt
from pathlib import Path

from core.scheduler import helpers as H, peak_upload as P
from core.scheduler.catalog import Param, register

FOLDER = "MCX PEAK MARGIN UPLOAD"


def names(d: dt.date, user: str) -> list:
    return [f"MCX_PeakMargin{user}_{d:%Y%m%d}_{i:02d}.csv" for i in range(1, 9)]


def _one_day_factory(params, ctx):
    from core import files70

    def one_day(d: dt.date, work: Path):
        want = names(d, files70._mcx_user())
        dest = work / "MCX"
        todo = [n for n in want if n not in P.present([n], dest)]
        if todo:
            ctx.log(f"MCX: {len(todo)} peak file(s) from the MCX file portal…")
            try:
                files70.fetch_mcx([{"group": "mcx", "sub": "MCX", "name": n, "src": "mcx"} for n in todo],
                                  work, d, P.f70_ctx(ctx))
            except Exception as e:
                ctx.log(f"MCX portal: {type(e).__name__}: {e} - trying the backup.")
            left = [n for n in todo if n not in P.present([n], dest)]
            if left:
                P.copy_from_backups(left, dest, [files70.day_dir(d) / "MCX"], ctx, "MCX")
        miss = [n for n in want if n not in P.present([n], dest)]
        if miss:
            raise FileNotFoundError(f"{len(miss)} of the 8 MCX peak files missing for {d:%d-%m-%Y}: {', '.join(miss)}")
        ctx.log("All 8 MCX peak files are here.")
        return [dest / n for n in want]
    return one_day


@H.logged(FOLDER)
def _run(params, ctx):
    return P.run_dates(FOLDER, params, ctx, _one_day_factory(params, ctx), "MCX_PEAKMARGIN", "MCX_PEAKMARGIN_DDMMYYYY.zip")


register(
    key="mcx_peak_margin_upload",
    name="MCX PEAK MARGIN UPLOAD",
    description="T-2 (holiday calendar) MCX peak-margin zip: the 8 MCX_PeakMargin files from the MCX file portal "
                "(else the 76 FILES backup), uploaded to the Mastertrust customFileImport API. Also uploads missed days.",
    run=_run,
    params=[
        Param("catch_up_days", "Catch up missed days (calendar days back)", "int", default=7,
              help="Also upload the trading days of this many days before T-2 that were never uploaded. 0 = only T-2."),
        Param("force_date", "Upload only this trade date (DD-MM-YYYY)", "str", default="",
              help="Leave empty normally. Filled = upload exactly this day (again), nothing else."),
    ],
    retry_safe=False, parallel_safe=False, category="Uploads", folder=FOLDER, uses=("mcx_sftp", "upload_api"),
)
