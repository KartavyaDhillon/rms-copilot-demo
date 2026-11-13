"""FO FILES UPLOAD - port of D:\\CLAUDE\\data\\nse_upload_senior_test.py (the new three-file version).

Uploads the files of the PREVIOUS TRADING DAY to the Mastertrust customFileImport API:
    1. F_OPTCSHDTLS01_<MEM>_DDMMYYYY.CSV   (NSE FO/Reports, gzipped)      uploaded exactly as NSE sends it
    2. F_<MEM>_ASMOTM_DDMMYYYY.csv         (NSE FO/Letters/Dnld)          header row removed first (when it has one)
    3. <MEM>_F_ASM_DDMMYY.pdf              (NSE FO/Letters/Dnld, gzipped) uploaded as it is

ONE DATE PER RUN - no lookback window. The date is the previous trading day on the market-holiday calendar:
    Tue-Fri 08:00  -> the day before (Mon..Thu)        Sat 00:30 -> Friday (the desk is closed after 01:00 and Saturday
    is off, so Friday's files go up half an hour after midnight)       Monday is not scheduled (Friday is already done).
The e-mail process sends the same day's files at 10 PM, so they are on NSE by then. Each (file, date) is written to a
ledger, so a second run of the same date uploads nothing twice. A file that NSE does not have is 'not available'
(not an error) as long as at least one of the three exists; if NONE exists the run FAILS (WhatsApp warning).
To send a day again / a missed day: Run Now with 'Upload only this trade date'."""
import datetime as dt
from pathlib import Path

from core.scheduler import connections, fo_files as F, helpers as H, paths, peak_upload as P
from core.scheduler.catalog import Param, register

FOLDER = "FO FILES UPLOAD"


def _archive(d: dt.date, keep, work: Path, remove_work: bool, ctx):
    dest = paths.proc_dir(FOLDER, "backup") / f"{d:%d-%m-%Y}"
    dest.mkdir(parents=True, exist_ok=True)
    for f in keep:
        f = Path(f)
        if f.is_file():
            t = dest / f.name
            if t.exists():
                t = dest / f"{f.stem}_{dt.datetime.now():%H%M%S}{f.suffix}"
            f.replace(t)
    ctx.log(f"Backed up to backup\\{dest.name}\\ (the file as NSE sent it).")
    if remove_work:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


@H.logged(FOLDER)
def _run(params, ctx):
    force = P.parse_force_date(params.get("force_date"))
    today = dt.date.today()
    d = force or H.prev_trading_day(today)
    ctx.log(f"Trade date: {d:%d-%m-%Y} ({d:%A}) - " + ("the date you asked for." if force else
            f"the previous trading day on the holiday calendar (run date {today:%d-%m-%Y}, {today:%A}). Nothing older is looked at."))
    mem = connections.require("nse", "member_code")["member_code"]
    todo = [s for s in F.specs(mem) if s["upload"] and (force or not F.is_uploaded(FOLDER, s["key"], d))]
    if not todo:
        raise H.Skipped(f"The files of {d:%d-%m-%Y} are already uploaded - nothing to do. "
                        "Use 'Upload only this trade date' to send the day again.")
    work = paths.proc_dir(FOLDER, "inputs") / f"{d:%d-%m-%Y}"
    ready = work / "upload_ready"
    try:
        wait = max(0, int(params.get("wait_minutes") or 0))
    except (TypeError, ValueError):
        wait = 0
    uploaded, missing, failed, keep = [], [], [], []
    for i, spec in enumerate(todo):
        if getattr(ctx, "cancelled", False):
            break
        ctx.log(f"\n-> {spec['label']}")
        local = spec["local"](d)
        try:
            p = F.fetch(spec, d, work, ctx, wait_minutes=wait if not uploaded and i == 0 else 0)
            if p is None:
                ctx.log(f"   {local}: not available on NSE - skipped.")
                missing.append(spec["key"])
                continue
            send = F.strip_header_copy(p, ready, ctx) if spec["strip_header"] else p
            if send is None:
                raise RuntimeError(f"{local}: nothing uploadable (empty, or only a header row).")
            ans = P.upload(FOLDER, send, spec["template"], d, ctx, mime=spec["mime"], part_name=local)
            F.mark_uploaded(FOLDER, spec["key"], d, "forced" if force else "")
            uploaded.append(spec["key"])
            keep += [p, send] if send != p else [p]
            ctx.log(f"✓ {local} uploaded. {ans[:160]}")
        except Exception as e:
            failed.append((spec["key"], f"{type(e).__name__}: {e}"))
            ctx.log(f"✗ {local} NOT uploaded - {type(e).__name__}: {e}")
            if str(e).startswith("Upload API"):               # the upload itself is unusable: the others would fail the same
                ctx.log("The upload API cannot be used right now - the remaining files are left for a manual run.")
                break
    if uploaded or missing:
        _archive(d, keep, work, remove_work=not failed, ctx=ctx)
    summary = (f"{d:%d-%m-%Y}: uploaded {', '.join(uploaded) or 'none'}"
               + (f"; not available on NSE: {', '.join(missing)}" if missing else ""))
    if failed:
        raise RuntimeError(summary + " | NOT uploaded: " + "; ".join(f"{k} ({m[:140]})" for k, m in failed)
                           + " | Run Now with 'Upload only this trade date' sends them again.")
    if not uploaded:
        raise FileNotFoundError(f"None of the {len(todo)} files exists on NSE for {d:%d-%m-%Y} - nothing could be uploaded. "
                                "If it was a trading day the files are late: Run Now later.")
    return summary


register(
    key="fo_files_upload",
    name="FO FILES UPLOAD",
    description="Uploads the previous trading day's F_OPTCSHDTLS01, F_ASMOTM (header removed) and ASM PDF from the NSE "
                "extranet to the Mastertrust customFileImport API. One date per run (no lookback). Tue-Fri 08:00, Sat 00:30.",
    run=_run,
    params=[
        Param("wait_minutes", "Wait for the first file (minutes)", "int", default=10,
              help="0 = try once. Otherwise keep checking every minute for up to this long."),
        Param("force_date", "Upload only this trade date (DD-MM-YYYY)", "str", default="",
              help="Leave empty normally. Filled = upload exactly this day (again), nothing else."),
    ],
    retry_safe=False, parallel_safe=False, category="Uploads", folder=FOLDER, uses=("nse", "upload_api"),
)
