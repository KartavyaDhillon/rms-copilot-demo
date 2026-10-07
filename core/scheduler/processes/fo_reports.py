"""FO REPORTS — the five NSE FO "Reports" files (Position NCL collateral, SA04, SA05, MG12, MG13) for the previous
trading day, extracted and e-mailed (from SUMIT GANDHI\\x.py).  Missing files are named in the mail."""
import datetime as dt

from core.scheduler import connections, helpers as H, nse, paths
from core.scheduler.catalog import register
from core.scheduler.processes import _kit as K

FOLDER = "FO REPORTS"


@H.logged(FOLDER)
def _run(params, ctx):
    to, cc = K.recipients(params)
    K.require_trading_day(ctx)
    if H.already_sent(FOLDER, "mail", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["mail"], "e-mailed"))
    inp = paths.proc_dir(FOLDER, "inputs")
    mem = connections.require("nse", "member_code")["member_code"]
    d = H.prev_trading_day(dt.date.today())
    dm, ym = f"{d:%d%m%Y}", f"{d:%Y%m%d}"
    specs = [f"Position_NCL_FO_0_CM_{mem}_{ym}_F_0000.csv.gz", f"F_SA04_{mem}_{dm}.lis.gz",
             f"F_SA05_{mem}_{dm}.lis.gz", f"F_MG12_{mem}_{dm}.lis.gz", f"F_MG13_{mem}_{dm}.lis.gz"]
    wait = K.wait_of(params)
    got, missing = [], []
    for i, name in enumerate(specs):
        gz = nse.download_wait("member", "FO", "Reports", name, inp, ctx, wait) if (wait and i == 0) else \
            nse.download("member", "FO", "Reports", name, inp, ctx)
        if not gz and wait and i > 0:
            try:
                gz = nse.download_wait("member", "FO", "Reports", name, inp, ctx, 1)
            except FileNotFoundError:
                gz = None
        if gz:
            try:
                got.append(K.gunzip(gz))
            except OSError:                                    # not really gzipped — keep raw
                raw = gz.with_suffix("")
                gz.replace(raw)
                got.append(raw)
            else:
                gz.unlink(missing_ok=True)
        else:
            missing.append(name[:-3])
    ds = f"{d:%d-%m-%Y}"
    subject = f"FO Reports (SA04, SA05, MG12, MG13, POSITION_NCL) - {ds}"
    if not got:
        body = (f"<html><body><p>Dear Sir,</p><p>No FO SA04, SA05, MG12, MG13 or position report files (SA04, SA05, MG12, "
                f"MG13, NCL FO Collateral) were found on the extranet for the trading day {ds}.</p>"
                f"<p>Best regards,<br>{K.sig(params)}</p></body></html>")
    else:
        miss = ("<p>The following file(s) were <b>not available</b> on the extranet for "
                f"{ds}:</p><ul>{''.join(f'<li>{m}</li>' for m in missing)}</ul>") if missing else ""
        body = ("<html><body><p>Dear Sir,</p><p>Please find attached the FO SA04, SA05, MG12, MG13 and Position NCL "
                f"reports downloaded from the extranet for the trading day {ds}.</p>{miss}"
                f"<p>Best regards,<br>{K.sig(params)}</p></body></html>")
    H.send_email(subject, body, to, cc, got, ctx, html=True)
    H.mark_sent(FOLDER, "mail")
    H.backup_files(FOLDER, got, ctx)
    if not got:
        raise FileNotFoundError("No FO report file was on NSE — a 'not found' mail was sent.")
    return f"{len(got)} file(s) e-mailed" + (f"; missing {len(missing)}" if missing else "")


register(
    key="fo_reports",
    name="FO REPORTS",
    description="Downloads the FO Position NCL, SA04, SA05, MG12 and MG13 report files for the previous trading day and "
                "e-mails them, naming any that are missing.",
    run=_run,
    params=K.mail_params("anils@mastertrust.co.in,sumit.gandhi1@mastertrust.co.in", "kartavya.dhillon@mastertrust.co.in"),
    retry_safe=False, parallel_safe=False, category="NSE files", folder=FOLDER, uses=("nse", "smtp"),
)
