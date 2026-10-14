"""NCL CO POSITION FILE — downloads the previous trading day's CO position file from the NSE member portal
(segment CO › Reports › Position_NCL_CO_0_CM_<member>_<YYYYMMDD>_F_0000.csv.gz) and unzips it.

The date in the file name is the PREVIOUS TRADING DAY (weekends and the NSE holiday calendar are skipped), e.g. run on
14-10-2026 → Position_NCL_CO_0_CM_06436_20261013_F_0000.csv.gz.  Step 2 (upload to the Kambala FTP) is added next."""
import datetime as dt

from core.scheduler import connections, helpers as H, nse, paths
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "NCL CO POSITION FILE"


@H.logged(FOLDER)
def _run(params, ctx):
    K.require_trading_day(ctx)                                   # today must be a trading day (holiday calendar)
    today = dt.date.today()
    bday = H.prev_trading_day(today)                             # the date that is inside the file name
    member = str(connections.require("nse", "member_code", "login_id")["member_code"]).strip()
    name = f"Position_NCL_CO_0_CM_{member}_{bday:%Y%m%d}_F_0000.csv.gz"
    inp, out = paths.proc_dir(FOLDER, "inputs"), paths.proc_dir(FOLDER, "outputs")
    ctx.log(f"Previous trading day: {bday:%d-%m-%Y} → file {name}")

    gz = nse.download_wait("member", "CO", "Reports", name, inp, ctx, K.wait_of(params))
    csv_path = K.gunzip(gz, out / name[:-3])                     # …/outputs/Position_NCL_CO_..._0000.csv
    ctx.log(f"Unzipped → {csv_path.name} ({csv_path.stat().st_size / 1024:,.0f} KB) in {out}.")
    H.backup_files(FOLDER, [gz], ctx)                            # the unzipped csv stays in outputs for the upload step
    return f"{csv_path.name} downloaded and unzipped ({bday:%d-%m-%Y})"


register(
    key="ncl_co_position_file",
    name="NCL CO POSITION FILE",
    description="Downloads the previous trading day's CO position file (Position_NCL_CO_0_CM_<member>_<YYYYMMDD>_F_0000.csv.gz) "
                "from the NSE member portal (CO › Reports), skipping weekends and market holidays, and unzips it.",
    run=_run,
    params=[Param("wait_minutes", "Wait for the file (minutes)", "int", default=0,
                  help="0 = try once. Otherwise keep checking every minute for up to this long.")],
    retry_safe=True, parallel_safe=False, category="Reports", folder=FOLDER, uses=("nse",),
)
