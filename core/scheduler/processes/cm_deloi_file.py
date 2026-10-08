"""CM DELOI FILE — F_CM_DELOI_<member>_<date>.CSV.gz from NSE (FO / Reports), filtered to the member's own code and
e-mailed; the full file goes to a second list (from CM_DELOI_Final.py). Looks back up to 3 trading days."""
import datetime as dt

from core.scheduler import connections, helpers as H, nse, paths
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "CM DELOI FILE"


@H.logged(FOLDER)
def _run(params, ctx):
    pd = H.need("pandas")
    to, cc = K.recipients(params)
    full_to, full_cc = H.parse_emails(params.get("full_to")), H.parse_emails(params.get("full_cc"))
    K.require_trading_day(ctx)
    if H.already_sent(FOLDER, "filtered", ctx) and H.already_sent(FOLDER, "full", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["filtered", "full"], "e-mailed"))
    inp = paths.proc_dir(FOLDER, "inputs")
    mem = connections.require("nse", "member_code")["member_code"]
    wait = K.wait_of(params)

    d, gz, used = H.prev_trading_day(dt.date.today()), None, None
    for i in range(3):
        name = f"F_CM_DELOI_{mem}_{d:%d%m%Y}.CSV.gz"
        ctx.log(f"Looking for the CM_DELOI file of {d:%d-%m-%Y} (try {i + 1}/3)…")
        gz =nse.download_wait("member", "FO", "Reports", name, inp, ctx, wait) if (wait and i == 0) else \
            nse.download("member", "FO", "Reports", name, inp, ctx)
        if gz:
            used = d
            break
        d = H.prev_trading_day(d)
    if not gz:
        raise FileNotFoundError("CM_DELOI file was not found for the last 3 trading days.")
    csv_all = K.gunzip(gz)

    df = pd.read_csv(csv_all)
    ctx.log(f"Unzipped {csv_all.name}: {len(df):,} row(s), {len(df.columns)} column(s).")
    df.columns = [c.strip() for c in df.columns]
    col = "Client Account /CP Code"
    if col not in df.columns:
        raise RuntimeError(f"Column '{col}' not found. Columns: {df.columns.tolist()}")
    df[col] = df[col].astype(str).str.strip()
    flt = df[df[col] == mem]
    ctx.log(f"{len(flt)} row(s) for code {mem}.")
    ds = f"{used:%d%m%Y}"
    p_flt = paths.proc_dir(FOLDER, "outputs") / "CM_DELOI_06436.CSV"
    flt.to_csv(p_flt, index=False)
    p_full = paths.proc_dir(FOLDER, "outputs") / "FULL_CM_DELOI_06436.CSV"
    csv_all.replace(p_full)

    def body(greet):
        return (f"<html><body>{greet},<br><br>Please find the daily CM_DELOI_06436_{ds}<br><br>"
                f"Best regards,<br>{K.sig(params)}</body></html>")
    if not H.already_sent(FOLDER, "filtered", ctx):
        H.send_email("CM_DELOI_06436", body("Dear Sir"), to, cc, [p_flt], ctx, html=True)
        H.mark_sent(FOLDER, "filtered")
    if full_to and not H.already_sent(FOLDER, "full", ctx):
        H.send_email("FULL CM_DELOI FILE", body("Hi Team"), full_to, full_cc, [p_full], ctx, html=True)
        H.mark_sent(FOLDER, "full")
    H.backup_files(FOLDER, [gz, p_flt, p_full], ctx)
    return f"CM_DELOI of {used:%d-%m-%Y} e-mailed ({len(flt)} own rows)"


register(
    key="cm_deloi_file",
    name="CM DELOI FILE",
    description="Downloads the CM_DELOI file from NSE, e-mails the member's own rows and, separately, the full file.",
    run=_run,
    params=K.mail_params("maninders@mastertrust.co.in,kamal.mitter@mastertrust.co.in,cd.solanki@mastertrust.co.in",
                         "sandeep.kumar2@mastertrust.co.in,sumit.aggarwal@mastertrust.co.in,amit.pundir@mastertrust.co.in") + [
        Param("full_to", "Full file — To", "text", fmt="emails", scope="process", default="rms_del@mastertrust.co.in"),
        Param("full_cc", "Full file — Cc", "text", fmt="emails", scope="process"),
    ],
    retry_safe=False, parallel_safe=False, category="NSE files", folder=FOLDER, uses=("nse", "smtp"),
)
