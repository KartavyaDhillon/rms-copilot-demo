"""ACTIVE CLIENT REPORT — deposits (DEPOSIT CTCL / DEPOSIT NMASS) from today's CTCL vs NMASS run-1 output joined with
ClientStatus from today's risk report; the ACTIVE rows are e-mailed as CustomColumnServer.csv (from Ashutosh_FINAL.py).

Inputs:  1) today's RISK_REPORT (COMMON FILES, read only)   2) FULL_FILE_CTCL_VS_NMASS_01.xlsx (CTCL vs NMASS run 1)
"""
from core.scheduler import helpers as H, paths
from core.scheduler.catalog import register
from core.scheduler.processes import _ctcl as C, _kit as K

FOLDER = "ACTIVE CLIENT REPORT"


@H.logged(FOLDER)
def _run(params, ctx):
    pd = H.need("pandas")
    to, cc = K.recipients(params)
    if H.already_sent(FOLDER, "mail", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["mail"], "e-mailed"))
    risk = H.find_risk_report(ctx)
    src = C.get_run1(params, ctx)

    ctx.log(f"CTCL run-1 file: {getattr(src, 'name', src)}")
    rk = pd.read_csv(risk, low_memory=False)
    ctx.log(f"Risk report read: {len(rk):,} row(s).")
    rk.columns = rk.columns.str.strip()
    if "ClientStatus" not in rk.columns:
        raise RuntimeError("The risk report has no ClientStatus column — cannot find the ACTIVE clients.")
    status = rk[["ACCOUNTID", "ClientStatus"]].drop_duplicates(subset="ACCOUNTID", keep="first")
    dep = C.read_run1(src, ["ACCOUNTID", "DEPOSIT CTCL", "DEPOSIT NMASS"])[["ACCOUNTID", "DEPOSIT CTCL", "DEPOSIT NMASS"]]
    ctx.log(f"Deposits read from run 1: {len(dep):,} client(s). Joining with the risk report's ClientStatus…")
    merged = pd.merge(dep, status, on="ACCOUNTID", how="left")
    final = pd.DataFrame({"CLIENTID": merged["ACCOUNTID"], "Test1": merged["DEPOSIT CTCL"],
                          "Test2": merged["DEPOSIT NMASS"], "Test3": 0, "Test4": 0,
                          "ClientStatus": merged["ClientStatus"]})
    out = paths.proc_dir(FOLDER, "outputs")
    full = out / "CustomColumnServer.csv"
    final.to_csv(full, index=False)
    active = final[final["ClientStatus"].astype(str).str.strip().str.lower() == "active"]
    ctx.log(f"{len(final)} client(s) in total, {len(active)} ACTIVE.")
    if active.empty:
        raise H.Skipped("No ACTIVE clients found — e-mail not sent.")
    att = out / "CustomColumnServer_ACTIVE.csv"
    active.to_csv(att, index=False)
    send = out / "CustomColumnServer.csv"                       # recipients expect this file name
    tmp = out / "_send" / "CustomColumnServer.csv"
    tmp.parent.mkdir(exist_ok=True)
    active.to_csv(tmp, index=False)
    body = ("Hello,\n\nPlease find attached the list of ACTIVE clients.\n\nRegards\n" + K.sig_text(params))
    H.send_email("Active Client Report", body, to, cc, [tmp], ctx)
    H.mark_sent(FOLDER, "mail")
    tmp.unlink(missing_ok=True)
    H.backup_files(FOLDER, [full, att], ctx)
    return f"{len(active)} ACTIVE client(s) e-mailed"


register(
    key="active_client_report",
    name="ACTIVE CLIENT REPORT",
    description="After CTCL vs NMASS run 1: joins its deposits with the risk report's ClientStatus and e-mails the "
                "ACTIVE clients.",
    run=_run,
    params=K.mail_params("ashutosh.yadav@mastertrust.co.in,abhishek.dhiman@mastertrust.co.in,rms_del@mastertrust.co.in",
                         with_wait=False) + [C.wait_param()],
    retry_safe=False, parallel_safe=False, category="CTCL reports", folder=FOLDER, uses=("smtp",),
)
