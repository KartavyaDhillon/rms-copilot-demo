"""NCL PEAK MARGIN UPLOAD — port of D:\\NCCL FILE UPLOAD\\ncl_peakmargin_zip_upload.py (engine: core.scheduler.peak_upload).

Trade date = T-2 on the holiday calendar. Zip NCL_PEAKMARGIN_<DDMMYYYY>.zip =
    NCDEX  NCCL_MARGIN_REP_01292_<yyyymmdd>_01..08.csv  (8, mandatory)
           1) NCDEX web extranet, captcha auto-filled (same login as the 76 FILES DOWNLOADER)
           2) the console's 76 FILES DOWNLOADER day folder \\NCDEX (data\\76 FILES DOWNLOADER) - nothing else
    CM     C_SA01_P_<member>_<ddmmyyyy>_i01..i04.lis   (4, mandatory)  NSE extranet, then the 76 FILES day folder \\CM
    FO     F_SA01_P_<member>_<ddmmyyyy>_i01..i04.lis   (4, mandatory)  NSE extranet, then \\FO
    CO     O_SA01_P_<member>_<ddmmyyyy>_i01..i08.lis   (8, optional)   NSE extranet, then \\CO
uploaded to customFileImport (fileName = NCL_PEAKMARGIN_DDMMYYYY.zip, date = the trade date)."""
import datetime as dt
from pathlib import Path

from core.scheduler import helpers as H, peak_upload as P
from core.scheduler.catalog import Param, register

FOLDER = "NCL PEAK MARGIN UPLOAD"


def names(d: dt.date, member: str, ucc: str) -> dict:
    ymd, dmy = d.strftime("%Y%m%d"), d.strftime("%d%m%Y")
    return {
        "NCDEX": [f"NCCL_MARGIN_REP_{ucc}_{ymd}_{i:02d}.csv" for i in range(1, 9)],
        "CM": [f"C_SA01_P_{member}_{dmy}_i0{i}.lis" for i in range(1, 5)],
        "FO": [f"F_SA01_P_{member}_{dmy}_i0{i}.lis" for i in range(1, 5)],
        "CO": [f"O_SA01_P_{member}_{dmy}_i0{i}.lis" for i in range(1, 9)],
    }


def _one_day_factory(params, ctx):
    from core import files70

    def one_day(d: dt.date, work: Path):
        member, ucc = files70._member(), files70.NCDEX_UCC
        want = names(d, member, ucc)
        fctx = P.f70_ctx(ctx)
        day76 = files70.day_dir(d)

        # NSE CM / FO / CO: the extranet first, then the 76 FILES DOWNLOADER folder
        for seg, folder in (("CM", "CM"), ("FO", "FO"), ("CO", "CO")):
            todo = [n for n in want[seg] if n not in P.present([n], work / seg)]
            if todo:
                ctx.log(f"NSE {seg}: {len(todo)} file(s) from the NSE extranet…")
                items = [{"group": f"nse_{seg.lower()}", "sub": seg, "name": n, "src": "nse", "seg": seg,
                          "folder": "Reports", "remote": n + ".gz"} for n in todo]
                try:
                    files70.fetch_nse(items, work, fctx)
                except Exception as e:
                    ctx.log(f"NSE {seg}: {type(e).__name__}: {e} - trying the backup.")
                left = [n for n in todo if n not in P.present([n], work / seg)]
                if left:
                    P.copy_from_backups(left, work / seg, [day76 / folder], ctx, f"NSE {seg}")

        # NCDEX 8 peaks: the exchange (captcha auto-filled) first, then the console's 76 FILES DOWNLOADER folder
        todo = [n for n in want["NCDEX"] if n not in P.present([n], work / "NCDEX")]
        if todo:
            ctx.log(f"NCDEX: {len(todo)} peak file(s) from the NCDEX web extranet (captcha filled automatically)…")
            try:
                files70.fetch_ncdex_peaks([{"group": "ncdex_peak", "sub": "NCDEX", "name": n, "src": "ncdexweb"}
                                           for n in todo], work, d, fctx)
            except Exception as e:
                ctx.log(f"NCDEX web extranet: {type(e).__name__}: {e} - trying the 76 FILES DOWNLOADER folder.")
            left = [n for n in todo if n not in P.present([n], work / "NCDEX")]
            if left:
                P.copy_from_backups(left, work / "NCDEX", [day76 / "NCDEX"], ctx, "NCDEX")

        miss = [n for seg in ("NCDEX", "CM", "FO") for n in want[seg] if n not in P.present([n], work / seg)]
        if miss:
            raise FileNotFoundError(f"{len(miss)} mandatory file(s) missing for {d:%d-%m-%Y}: {', '.join(miss)}")
        co = P.present(want["CO"], work / "CO")
        ctx.log(f"All mandatory files are here (NCDEX 8, CM 4, FO 4) + CO {len(co)}/8 (optional).")
        return ([work / "NCDEX" / n for n in want["NCDEX"]] + [work / "CM" / n for n in want["CM"]] +
                [work / "FO" / n for n in want["FO"]] + [work / "CO" / n for n in co])
    return one_day


@H.logged(FOLDER)
def _run(params, ctx):
    return P.run_dates(FOLDER, params, ctx, _one_day_factory(params, ctx), "NCL_PEAKMARGIN", "NCL_PEAKMARGIN_DDMMYYYY.zip")


register(
    key="ncl_peak_margin_upload",
    name="NCL PEAK MARGIN UPLOAD",
    description="T-2 (holiday calendar) NCL peak-margin zip: NCDEX 8 from the NCDEX extranet (captcha auto-filled, "
                "else the 76 FILES backup), CM/FO/CO from NSE - uploaded to the Mastertrust customFileImport API. "
                "Also uploads missed days.",
    run=_run,
    params=[
        Param("catch_up_days", "Catch up missed days (calendar days back)", "int", default=7,
              help="Also upload the trading days of this many days before T-2 that were never uploaded. 0 = only T-2."),
        Param("force_date", "Upload only this trade date (DD-MM-YYYY)", "str", default="",
              help="Leave empty normally. Filled = upload exactly this day (again), nothing else."),
    ],
    retry_safe=False, parallel_safe=False, category="Uploads", folder=FOLDER, uses=("nse", "upload_api"),
)
