"""FO ASM / OTM / MWPL REPORTS MAIL - port of D:\\CLAUDE\\data\\Final.py (runs at 10:00 PM, TODAY's files only).

Downloads up to four FO files for today from the NSE extranet (F_OPTCSHDTLS01 with a header row added, F_ASMOTM,
the ASM letter PDF, F_MWPLVIOL02 - the last one only exists on some days) and e-mails them in ONE mail. A file that
is not there is named in the mail. If none of the four is there a 'not found' mail goes out and the run is a FAILURE
(the process' WhatsApp warning is sent). Files are moved to backup\\<dd-mm-yyyy> after the mail has been sent.
Market holidays / weekends: nothing is done (the schedule skips them; a manual Run Now says so)."""
import datetime as dt

from core.scheduler import connections, fo_files as F, helpers as H, paths
from core.scheduler.catalog import register
from core.scheduler.processes import _kit as K

FOLDER = "FO ASM OTM MWPL MAIL"
TO = ("rms_del@mastertrust.co.in,rms_chd@mastertrust.co.in,kamal.mitter@mastertrust.co.in,cd.solanki@mastertrust.co.in,"
      "jagesh.aggarwal@mastertrust.co.in,nitin.kumar@mastertrust.co.in,lokesh.kumar1@mastertrust.co.in,"
      "accounts@mastertrust.co.in,maninders@mastertrust.co.in,kartik.dey@mastertrust.co.in,techlead@mastertrust.co.in,"
      "nand.kishor1@mastertrust.co.in")
CC = "va@mastertrust.co.in"


def _body(params, ds, got_labels, missing_labels):
    sig = K.sig(params)
    if not got_labels:
        return (f"<html><body><p>Dear Team,</p><p>No FO ASM/OTM/MWPL files were found on the extranet for {ds}.</p>"
                f"<p>Best regards,<br>{sig}</p></body></html>")
    miss = ""
    if missing_labels:
        miss = (f"<p>The following file(s) were <b>not available</b> on the extranet for {ds}:</p>"
                f"<ul>{''.join(f'<li>{m}</li>' for m in missing_labels)}</ul>")
    return (f"<html><body><p>Dear Team,</p><p>Please find attached the downloaded FO ASM/OTM/MWPL files from "
            f"extranet for {ds}.</p>{miss}<p>Best regards,<br>{sig}</p></body></html>")


@H.logged(FOLDER)
def _run(params, ctx):
    to, cc = K.recipients(params)
    K.require_trading_day(ctx)
    if H.already_sent(FOLDER, "mail", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["mail"], "e-mailed"))
    mem = connections.require("nse", "member_code")["member_code"]
    d = dt.date.today()
    ds = f"{d:%d-%m-%Y}"
    ctx.log(f"Files of today, {ds} ({d:%A}).")
    inp = paths.proc_dir(FOLDER, "inputs")
    K.clean_inputs(list(inp.iterdir()))                    # leftovers of a crashed run must not be mailed as today's
    wait = K.wait_of(params)
    got, got_labels, missing = [], [], []
    for i, spec in enumerate(F.specs(mem)):
        ctx.log(f"-> {spec['label']}")
        # only the first (main, daily) file is waited for; the others get a short second chance if we had to wait
        p = F.fetch(spec, d, inp, ctx, wait_minutes=wait if i == 0 else (1 if wait else 0))
        if p is None:
            ctx.log(f"   not available on NSE for {ds}.")
            missing.append(spec["local"](d))
            continue
        if spec["key"] == "F_OPTCSHDTLS01":
            F.add_header_to_csv(p, F.F_OPT_HEADERS, ctx)
        got.append(p)
        got_labels.append(spec["label"])
    subject = f"FO ASM/OTM/MWPL Reports - {ds}"
    H.send_email(subject, _body(params, ds, got_labels, missing), to, cc, got, ctx, html=True, attempts=8, delay=15)
    H.mark_sent(FOLDER, "mail")
    H.backup_files(FOLDER, got, ctx)
    if not got:
        raise FileNotFoundError(f"None of the FO ASM/OTM/MWPL files was on NSE for {ds} - a 'not found' mail was sent.")
    return f"{len(got)} file(s) e-mailed" + (f"; not available: {len(missing)}" if missing else "")


register(
    key="fo_asm_otm_mail",
    name="FO ASM OTM MWPL MAIL",
    description="10:00 PM: today's FO ASM / OTM / MWPL files (F_OPTCSHDTLS01 with header, F_ASMOTM, ASM PDF, MWPLVIOL02) "
                "from the NSE extranet, e-mailed in one mail; missing files are named in the mail.",
    run=_run,
    params=K.mail_params(TO, CC, wait_default=20),
    retry_safe=False, parallel_safe=False, category="NSE files", folder=FOLDER, uses=("nse", "smtp"),
)
