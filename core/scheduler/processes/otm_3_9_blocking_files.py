"""3-9% BLOCKING FILES — blocking file of OTM stock-option contracts 3–9 % away from the underlying, built from the
previous trading day's FO Bhavcopy (from finalblocking2.0.py)."""
import csv
import datetime as dt
from pathlib import Path

from core.scheduler import helpers as H, nse, paths
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "3-9% BLOCKING FILES"


def risk_categories(path: Path, software: str, exclude) -> list:
    """The distinct CATEGORY values of today's RISK_REPORT for one TRADING_SOFTWARE_TYPE (default KAMB), de-duplicated,
    without the excluded ones, sorted. Same as: filter TRADING_SOFTWARE_TYPE = KAMB, copy CATEGORY, remove duplicates."""
    want, skip = software.strip().upper(), {e.strip().strip("'\"").upper() for e in exclude if e and e.strip()}
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rd = csv.reader(f)
                head = next(rd)
                cols = {h.strip().upper(): i for i, h in enumerate(head)}
                for need in ("TRADING_SOFTWARE_TYPE", "CATEGORY"):
                    if need not in cols:
                        raise ValueError(f"{path.name} has no {need} column.")
                it, ic = cols["TRADING_SOFTWARE_TYPE"], cols["CATEGORY"]
                seen = {}
                for row in rd:
                    if len(row) <= max(it, ic) or row[it].strip().upper() != want:
                        continue
                    c = row[ic].strip()
                    if c and c.upper() not in skip:
                        seen.setdefault(c.upper(), c)
                return sorted(seen.values(), key=str.upper)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not read {path.name}.")


@H.logged(FOLDER)
def _run(params, ctx):
    pd = H.need("pandas")
    to, cc = K.recipients(params)
    K.require_trading_day(ctx)
    if H.already_sent(FOLDER, "mail", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["mail"], "e-mailed"))
    inp, out = paths.proc_dir(FOLDER, "inputs"), paths.proc_dir(FOLDER, "outputs")
    risk = H.find_risk_report(ctx)
    cats = risk_categories(risk, params.get("software_type") or "KAMB",
                           (params.get("exclude_categories") or "").replace("\n", ",").replace(";", ",").split(","))
    if not cats:
        raise RuntimeError(f"No CATEGORY found in {risk.name} for TRADING_SOFTWARE_TYPE = "
                           f"{(params.get('software_type') or 'KAMB').strip().upper()}.")
    ctx.log(f"Risk report {risk.name}: {len(cats)} categories: {', '.join(cats)}")
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
        # Kambala "RMS Blocking/UnBlocking" layout. Fixed text on both sides of the four values that change per
        # contract (symbol, option type, strike with 2 decimals, expiry):
        #   |||||B||NonSqroff|NFO|<SYMBOL>|OPTSTK|<CE/PE>|<strike>|<DD-MON-YYYY>|Y|<CATEGORY>||BUYING NOT ALLOWED IN OTM OPTION
        # one line per contract PER CATEGORY (categories from the risk report, see risk_categories()).
        f.write("RMS Blocking/UnBlocking\n")
        for r in df.itertuples(index=False):
            head = f"|||||B||NonSqroff|NFO|{r.TckrSymb}|OPTSTK|{r.OptnTp}|{float(r.StrkPric):.2f}|{r.XpryDt.strftime('%d-%b-%Y').upper()}|Y|"
            for c in cats:
                f.write(f"{head}{c}||BUYING NOT ALLOWED IN OTM OPTION\n")
    ctx.log(f"{len(df)} contract(s) x {len(cats)} categories = {len(df) * len(cats):,} line(s) written.")

    body = ("<html><body><p>Dear Team,</p><p>Please find attached the <b>3% to 9% range of OTMContracts blocking file</b> "
            f"generated from the latest NSE FO Bhavcopy.</p><p>Best regards,<br>{K.sig(params)}</p></body></html>")
    H.send_email("3 TO 9% Range of OTMContracts BLOCKING FILE", body, to, cc, [txt], ctx, html=True)
    H.mark_sent(FOLDER, "mail")
    H.backup_files(FOLDER, [bhav, txt], ctx)
    K.clean_inputs(inp.glob("*"))
    return f"3–9% blocking file e-mailed ({len(df)} contracts x {len(cats)} categories = {len(df) * len(cats):,} lines)"


register(
    key="otm_3_9_blocking_files",
    name="3-9% BLOCKING FILES",
    description="Builds the 3–9 % OTM stock-option blocking file from the previous trading day's FO Bhavcopy and e-mails it. Every contract gets one line per CATEGORY of today's RISK_REPORT (TRADING_SOFTWARE_TYPE = KAMB, duplicates removed, MTM_TEST_1 and LOWVALUE_NEW_5 left out).",
    run=_run,
    params=K.mail_params("rms_del@mastertrust.co.in") + [
        Param("software_type", "Risk report: TRADING_SOFTWARE_TYPE", "str", default="KAMB",
              help="Only the CATEGORYs of this trading software are used."),
        Param("exclude_categories", "Categories to leave out", "text", default="MTM_TEST_1, LOWVALUE_NEW_5",
              help="Comma separated. These CATEGORYs never get a line in the file."),
    ],
    retry_safe=False, parallel_safe=False, category="Blocking files", folder=FOLDER, uses=("nse", "smtp"),
)
