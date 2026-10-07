"""CONTRACT DELTA — Contract Delta file (NSE common /Limit Files) and the two Base Position files (member Letters/Dnld),
e-mailed to the risk team; the Contract Delta file alone goes in a second mail to another list (from 3files.py)."""
import datetime as dt

from core.scheduler import connections, helpers as H, nse, paths
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "CONTRACT DELTA"


@H.logged(FOLDER)
def _run(params, ctx):
    to, cc = K.recipients(params)
    cd_to, cd_cc = H.parse_emails(params.get("cd_to")), H.parse_emails(params.get("cd_cc"))
    K.require_trading_day(ctx)
    if H.already_sent(FOLDER, "all", ctx) and H.already_sent(FOLDER, "cd", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["all", "cd"], "e-mailed"))
    today = dt.date.today()
    inp = paths.proc_dir(FOLDER, "inputs")
    wait = K.wait_of(params)
    mem = connections.require("nse", "member_code")["member_code"]
    bday = H.prev_trading_day(today)

    cd_name = f"Contract_Delta_{today:%d%m%Y}_i04.csv"
    got, missing = [], []
    cd = nse.download_wait("common", "FO", "/Limit Files", cd_name, inp, ctx, wait) if wait else nse.download(
        "common", "FO", "/Limit Files", cd_name, inp, ctx)
    (got if cd else missing).append(cd or cd_name)

    bp = []
    for n in ("01", "02"):
        gz_name = f"F_MWPLBASEPOS{n}_{mem}_{bday:%Y%m%d}.CSV.gz"
        gz = nse.download_wait("member", "FO", "Letters/Dnld", gz_name, inp, ctx, wait) if wait else nse.download(
            "member", "FO", "Letters/Dnld", gz_name, inp, ctx)
        if gz:
            bp.append(K.gunzip(gz, inp / gz_name[:-3]))
            gz.unlink()
        else:
            missing.append(gz_name[:-3])
    if not cd and not bp:
        raise FileNotFoundError("None of the three files is on NSE yet.")

    d1, d2 = f"{today:%d-%m-%Y}", f"{bday:%d-%m-%Y}"
    miss = (f"These file(s) ({', '.join(str(m) for m in missing)}) not available on the portal today."
            if missing else "All expected files are available today.")
    body = ("<html><body><p>Hi Team,</p><p>Please find the attached files:</p>"
            f"<ul><li><b>Contract Delta:</b> {d1 if cd else 'Not Available'}</li>"
            f"<li><b>Base Position Files:</b> {d2 if bp else 'Not Available'}</li></ul><p>{miss}</p>"
            f"<p>Warm regards,<br>{K.sig(params)}</p></body></html>")
    if not H.already_sent(FOLDER, "all", ctx):
        H.send_email("CONTRACT DELTA & BASE POSITION FILES", body, to, cc, ([cd] if cd else []) + bp, ctx, html=True)
        H.mark_sent(FOLDER, "all")
    if cd and cd_to and not H.already_sent(FOLDER, "cd", ctx):
        b2 = (f"<html><body><p>Hi Team,</p><p>Please find attached the <b>Contract Delta file</b> for <b>{d1}</b>.</p>"
              f"<p>Warm regards,<br>{K.sig(params)}</p></body></html>")
        H.send_email(f"CONTRACT DELTA FILE – {d1}", b2, cd_to, cd_cc, [cd], ctx, html=True)
        H.mark_sent(FOLDER, "cd")
    H.backup_files(FOLDER, ([cd] if cd else []) + bp, ctx)
    return f"mailed {1 if cd else 0} Contract Delta + {len(bp)} base position file(s)" + (f"; missing {len(missing)}" if missing else "")


register(
    key="contract_delta",
    name="CONTRACT DELTA",
    description="Downloads Contract Delta + the two Base Position files from NSE, e-mails them all to the risk team and "
                "the Contract Delta alone to a second list.",
    run=_run,
    params=K.mail_params("rms_del@mastertrust.co.in") + [
        Param("cd_to", "Contract Delta only — To", "text", fmt="emails", scope="process",
              default="ludhianacompliance@mastertrust.co.in,rms_chd@mastertrust.co.in,pradip.mandavkar@mastertrust.co.in,kamal.mitter@mastertrust.co.in"),
        Param("cd_cc", "Contract Delta only — Cc", "text", fmt="emails", scope="process",
              default="sandeep.kumar2@mastertrust.co.in,amit.pundir@mastertrust.co.in"),
    ],
    retry_safe=False, parallel_safe=False, category="NSE files", folder=FOLDER, uses=("nse", "smtp"),
)
