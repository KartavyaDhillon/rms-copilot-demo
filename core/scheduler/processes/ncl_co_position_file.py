"""NCL CO POSITION FILE — downloads the previous trading day's CO position file from the NSE member portal
(segment CO › Reports › Position_NCL_CO_0_CM_<member>_<YYYYMMDD>_F_0000.csv.gz), unzips it, E-MAILS it, then uploads it to
the Kambala FTP and backs it up.

The date in the file name is the PREVIOUS TRADING DAY (weekends and the NSE holiday calendar are skipped), e.g. run on
14-10-2026 → Position_NCL_CO_0_CM_06436_20261013_F_0000.csv.gz.

Some days NSE publishes no such file.  That is not an error: the run finishes as SUCCESS, the log says clearly that there
was no file, a short "NO FILE" e-mail goes to the same recipients, and nothing is uploaded."""
import datetime as dt

from core.scheduler import connections, helpers as H, nse, paths
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "NCL CO POSITION FILE"
SIGNATURE = "Regards,\nKartavya Dhillon\nMaster Capital Services Ltd\nTel: 011-66331000 | Ext. 84"


@H.logged(FOLDER)
def _run(params, ctx):
    K.require_trading_day(ctx)                                   # today must be a trading day (holiday calendar)
    to, cc = K.recipients(params)
    if H.already_sent(FOLDER, "mail", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["mail"], "e-mailed"))
    today = dt.date.today()
    bday = H.prev_trading_day(today)                             # the date that is inside the file name
    member = str(connections.require("nse", "member_code", "login_id")["member_code"]).strip()
    name = f"Position_NCL_CO_0_CM_{member}_{bday:%Y%m%d}_F_0000.csv.gz"
    inp, out = paths.proc_dir(FOLDER, "inputs"), paths.proc_dir(FOLDER, "outputs")
    ctx.log(f"Step 1/4: previous trading day is {bday:%d-%m-%Y} → looking for {name} (CO › Reports).")

    try:
        gz = nse.download_wait("member", "CO", "Reports", name, inp, ctx, K.wait_of(params))
    except FileNotFoundError:
        # NSE did not publish it (it does not come every day) — a normal outcome, not a failure.
        ctx.log(f"NO FILE: NSE has no {name} for {bday:%d-%m-%Y} on the member portal. Nothing was uploaded.")
        subject = f"NCL CO Position File - {bday:%d-%m-%Y} - NO FILE"
        body = ("<html><body><p>Hi Team,</p>"
                f"<p><b>No NCL CO Position file is available on the NSE member portal for {bday:%d-%m-%Y}.</b></p>"
                f"<p>File looked for: {name} (CO &rsaquo; Reports).<br>Nothing has been attached or uploaded to the Kambala FTP.</p>"
                f"<p>{K.sig(params)}</p></body></html>")
        try:
            H.send_email(subject, body, to, cc, [], ctx, html=True)      # not marked "sent": a later run may still get the file
            ctx.log(f"“No file” e-mail sent: “{subject}”.")
        except Exception as e:
            ctx.log(f"[!] Could not send the “no file” e-mail: {type(e).__name__}: {e}")
        ctx.log("This is normal on days when NSE does not publish the position file. The run is marked SUCCESS.")
        return f"No file on the member portal for {bday:%d-%m-%Y} — “no file” e-mail sent, nothing uploaded"

    csv_path = K.gunzip(gz, out / name[:-3])                     # …/outputs/Position_NCL_CO_..._0000.csv
    ctx.log(f"Step 2/4: unzipped → {csv_path.name} ({csv_path.stat().st_size / 1024:,.0f} KB).")

    subject = f"NCL CO Position File - {bday:%d-%m-%Y}"
    sig = K.sig(params)
    body = ("<html><body><p>Hi Team,</p><p>Please find attached the NCL CO Position file for "
            f"{bday:%d-%m-%Y}.</p><p>{sig}</p></body></html>")
    ctx.log(f"Step 3/4: e-mailing {csv_path.name} to {', '.join(to)}" + (f" (cc {', '.join(cc)})" if cc else "") + "…")
    H.send_email(subject, body, to, cc, [csv_path], ctx, html=True)
    H.mark_sent(FOLDER, "mail")
    ctx.log(f"E-mail sent: “{subject}”.")

    ctx.log("Step 4/4: uploading to the Kambala FTP…")
    H.kambala_upload([csv_path], ctx)
    H.backup_files(FOLDER, [gz, csv_path], ctx)
    ctx.log("Done: e-mailed, uploaded and backed up.")
    return f"{csv_path.name} e-mailed and uploaded ({bday:%d-%m-%Y})"


def _params():
    ps = K.mail_params("kartavya.dhillon@mastertrust.co.in", "", with_wait=True)
    for p in ps:
        if p.name == "signature":
            p.default = SIGNATURE
    return ps


register(
    key="ncl_co_position_file",
    name="NCL CO POSITION FILE",
    description="Downloads the previous trading day's CO position file (Position_NCL_CO_0_CM_<member>_<YYYYMMDD>_F_0000.csv.gz) "
                "from the NSE member portal (CO › Reports), unzips it, e-mails it, uploads it to the Kambala FTP and backs "
                "it up. Skips weekends and market holidays. If NSE did not publish the file the run still ends as success, "
                "with a 'no file' message in the log.",
    run=_run,
    params=_params(),
    retry_safe=False, parallel_safe=False, category="Reports", folder=FOLDER, uses=("nse", "smtp", "kambala_ftp"),
)
