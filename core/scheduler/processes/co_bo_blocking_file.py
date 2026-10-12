"""CO & BO BLOCKING FILE — NFO Restricted Basket (CO and BO) blocking files from NSE's F_AEL_OPT_CONTRACTS and the
previous day's FO Bhavcopy, e-mailed as BLOCKING_CO.txt and BLOCKING_BO.txt (from blockingfinal.py)."""
import datetime as dt
import math

from core.scheduler import helpers as H, nse, paths
from core.scheduler.catalog import register
from core.scheduler.processes import _kit as K

FOLDER = "CO & BO BLOCKING FILE"


@H.logged(FOLDER)
def _run(params, ctx):
    pd = H.need("pandas")
    to, cc = K.recipients(params)
    K.require_trading_day(ctx)
    if H.already_sent(FOLDER, "mail", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["mail"], "e-mailed"))
    today = dt.date.today()
    inp, out = paths.proc_dir(FOLDER, "inputs"), paths.proc_dir(FOLDER, "outputs")
    wait = K.wait_of(params)

    ctx.log(f"Step 1/4: option contract file for {today:%d-%m-%Y} from NSE (Limit Files)…")
    gz = nse.download_wait("common", "FO", "/Limit Files", f"F_AEL_OPT_CONTRACTS_{today:%d%m%Y}.csv.gz", inp, ctx, wait)
    opt_csv = K.gunzip(gz)
    ctx.log(f"Unzipped → {opt_csv.name} ({opt_csv.stat().st_size / 1024:,.0f} KB).")
    bday = H.prev_trading_day(today)
    ctx.log(f"Step 2/4: FO Bhavcopy of the previous trading day {bday:%d-%m-%Y}…")
    z = nse.download_wait("common", "FO", "/Bhavcopy", f"BhavCopy_NSE_FO_0_0_0_{bday:%Y%m%d}_F_0000.csv.zip", inp, ctx, wait)
    bhav_csv = K.unzip_first(z, inp)
    ctx.log(f"Unzipped → {bhav_csv.name} ({bhav_csv.stat().st_size / 1024:,.0f} KB).")

    ctx.log("Step 3/4: filtering NIFTY / BANKNIFTY puts and building the blocking lists…")
    df = pd.read_csv(opt_csv, header=None).iloc[:, :5]
    df.columns = ["Instrument", "Symbol", "Expiry", "Strike", "OptionType"]
    df = df[(df["Instrument"] == "OPTIDX") & (df["Symbol"].isin(["BANKNIFTY", "NIFTY"])) & (df["OptionType"] == "PE")]
    df["Expiry"] = pd.to_datetime(df["Expiry"], dayfirst=True, errors="coerce")
    first = today.replace(day=1)
    prev_month = (first - dt.timedelta(days=1)).replace(day=25)
    nm = (first + dt.timedelta(days=32)).replace(day=1)               # 1st of next month
    nn = (nm + dt.timedelta(days=32)).replace(day=1)                  # 1st of month after next
    end = nn - dt.timedelta(days=1)                                   # last day of next month
    df = df[(df["Expiry"] >= pd.Timestamp(prev_month)) & (df["Expiry"] <= pd.Timestamp(end))]
    ctx.log(f"{len(df)} index PUT contract(s) with expiry {prev_month:%d-%b-%Y} … {end:%d-%b-%Y}.")

    bh = pd.read_csv(bhav_csv)
    bh = bh[bh["TckrSymb"].isin(["BANKNIFTY", "NIFTY"])]
    parts = []
    for sym, step, drop in (("NIFTY", 50, 8), ("BANKNIFTY", 100, 5)):
        px = bh.loc[bh["TckrSymb"] == sym, "UndrlygPric"]
        if px.empty:
            raise RuntimeError(f"{sym} is missing in the Bhavcopy ({bhav_csv.name}).")
        atm = int(math.ceil(float(px.iloc[0]) / step) * step)
        s = df[(df["Symbol"] == sym) & (df["Strike"] <= atm)]
        excl = set(s["Strike"].drop_duplicates().sort_values(ascending=False).head(drop))
        parts.append(s[~s["Strike"].isin(excl)])
        ctx.log(f"{sym}: ATM {atm}, top {drop} strikes below it excluded.")
    res = pd.concat(parts, ignore_index=True)
    if res.empty:
        raise RuntimeError("No contracts left after filtering — check the contract file / Bhavcopy.")
    res["Expiry"] = res["Expiry"].dt.strftime("%d-%b-%Y").str.upper()

    files = []
    for name, header in (("BLOCKING_CO.txt", "NFO Restricted Basket|H|NFOCOBO"),
                         ("BLOCKING_BO.txt", "NFO Restricted Basket|B|NFOCOBO")):
        p = out / name
        with open(p, "w", encoding="utf-8") as f:
            f.write(header + "\n")
            for r in res.itertuples(index=False):
                f.write(f"{r.Instrument}|{r.Symbol}|{r.Expiry}|{int(r.Strike)}|{r.OptionType}\n")
        files.append(p)
    ctx.log(f"Created {len(res)} contract line(s) in each file.")

    body = ("<html><body><p>Hi Team,</p><p>Please find attached the <b>NFO Restricted Basket</b> files generated based on "
            "the latest <b>Option Contract</b> (F_AEL_OPT_CONTRACTS) and <b>FO Bhavcopy</b> data.</p>"
            "<ul><li><b>CO Blocking File</b></li><li><b>BO Blocking File</b></li></ul>"
            "<p>These files are prepared as per defined strike and expiry logic and are intended for upload in the system.</p>"
            f"<p>Best regards,<br>{K.sig(params)}</p></body></html>")
    ctx.log("Step 4/4: e-mailing the CO and BO files…")
    H.send_email("NFO Restricted Basket – CO & BO Blocking Files", body, to, cc, files, ctx, html=True)
    H.mark_sent(FOLDER, "mail")
    ctx.log("Uploading the CO and BO files to the Kambala FTP…")
    H.kambala_upload(files, ctx)
    H.backup_files(FOLDER, [opt_csv, bhav_csv] + files, ctx)
    K.clean_inputs(inp.glob("*"))
    return f"CO & BO files e-mailed ({len(res)} contracts each)"


register(
    key="co_bo_blocking_file",
    name="CO & BO BLOCKING FILE",
    description="Builds the NFO Restricted Basket CO and BO blocking files (NIFTY / BANKNIFTY puts) from NSE's option "
                "contract file and the previous day's Bhavcopy and e-mails them.",
    run=_run,
    params=K.mail_params("rms_del@mastertrust.co.in",
                         "sandeep.kumar2@mastertrust.co.in,amit.pundir@mastertrust.co.in,sumit.aggarwal@mastertrust.co.in"),
    retry_safe=False, parallel_safe=False, category="Blocking files", folder=FOLDER, uses=("nse", "smtp"),
)
