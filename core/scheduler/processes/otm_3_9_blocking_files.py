"""3-9% BLOCKING FILES — blocking file of OTM stock-option contracts 3–9 % away from the underlying, built from the
previous trading day's FO Bhavcopy (from finalblocking2.0.py)."""
import datetime as dt

from core.scheduler import helpers as H, nse, paths
from core.scheduler.catalog import register
from core.scheduler.processes import _kit as K

FOLDER = "3-9% BLOCKING FILES"


@H.logged(FOLDER)
def _run(params, ctx):
    pd = H.need("pandas")
    to, cc = K.recipients(params)
    K.require_trading_day(ctx)
    if H.already_sent(FOLDER, "mail", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["mail"], "e-mailed"))
    inp, out = paths.proc_dir(FOLDER, "inputs"), paths.proc_dir(FOLDER, "outputs")
    bday = H.prev_trading_day(dt.date.today())
    ctx.log(f"Step 1/3: FO Bhavcopy of the previous trading day {bday:%d-%m-%Y}…")
    z = nse.download_wait("common", "FO", "/Bhavcopy", f"BhavCopy_NSE_FO_0_0_0_{bday:%Y%m%d}_F_0000.csv.zip",
                          inp, ctx, K.wait_of(params))
    bhav = K.unzip_first(z, inp)
    ctx.log(f"Unzipped → {bhav.name} ({bhav.stat().st_size / 1024:,.0f} KB). Step 2/3: selecting stock options 3–9 % OTM…")

    df = pd.read_csv(bhav)
    ctx.log(f"Bhavcopy rows: {len(df):,}.")
    df = df[df["FinInstrmTp"] == "STO"].copy()
    df["XpryDt"] = pd.to_datetime(df["XpryDt"], errors="coerce")
    df = df.dropna(subset=["XpryDt"])
    past = df[df["XpryDt"].dt.date <= bday]
    fut = df[df["XpryDt"].dt.date > bday]
    if not past.empty:
        exp = past["XpryDt"].max()
    elif not fut.empty:
        exp = fut["XpryDt"].min()
    else:
        raise RuntimeError("No valid expiry found in the Bhavcopy.")
    df = df[df["XpryDt"] == exp]
    ctx.log(f"Expiry used: {exp.date()}")

    und, k = df["UndrlygPric"], df["StrkPric"]
    ce = (df["OptnTp"] == "CE") & (k >= und * 1.03) & (k <= und * 1.09)
    pe = (df["OptnTp"] == "PE") & (k >= und * 0.91) & (k <= und * 0.97)
    df = df[ce | pe]
    if df.empty:
        raise RuntimeError("No contracts fell inside the 3–9 % band — check the Bhavcopy.")

    txt = out / "3_TO_9%_Range_of_OTMContracts_BLOCKING_FILE.txt"
    with open(txt, "w", encoding="utf-8") as f:
        f.write("NFO Restricted Basket|M|NFONRML\n")
        for r in df.itertuples(index=False):
            f.write(f"OPTSTK|{r.TckrSymb}|{r.XpryDt.strftime('%d-%b-%Y').upper()}|{float(r.StrkPric)}|{r.OptnTp}\n")
    ctx.log(f"{len(df)} contract line(s) written.")

    body = ("<html><body><p>Dear Team,</p><p>Please find attached the <b>3% to 9% range of OTMContracts blocking file</b> "
            f"generated from the latest NSE FO Bhavcopy.</p><p>Best regards,<br>{K.sig(params)}</p></body></html>")
    H.send_email("3 TO 9% Range of OTMContracts BLOCKING FILE", body, to, cc, [txt], ctx, html=True)
    H.mark_sent(FOLDER, "mail")
    H.backup_files(FOLDER, [bhav, txt], ctx)
    K.clean_inputs(inp.glob("*"))
    return f"3–9% blocking file e-mailed ({len(df)} contracts)"


register(
    key="otm_3_9_blocking_files",
    name="3-9% BLOCKING FILES",
    description="Builds the 3–9 % OTM stock-option blocking file from the previous trading day's FO Bhavcopy and e-mails it.",
    run=_run,
    params=K.mail_params("rms_del@mastertrust.co.in"),
    retry_safe=False, parallel_safe=False, category="Blocking files", folder=FOLDER, uses=("nse", "smtp"),
)
