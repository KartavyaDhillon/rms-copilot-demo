"""MCX RESTRICTED BASKET — MCX option blocking files (BO / CO / MIS) from the latest MCX Bhavcopy: two expiries per
commodity, ATM ±10 strikes excluded; three separate e-mails (from MCX_BLOCKING_FINAL.py)."""
import datetime as dt

from core.scheduler import helpers as H, mcx, paths
from core.scheduler.catalog import register
from core.scheduler.processes import _kit as K

FOLDER = "MCX RESTRICTED BASKET"
SYMBOLS = ["CRUDEOIL", "NATURALGAS", "NATGASMINI", "CRUDEOILM"]
STEP = {"CRUDEOIL": 50, "NATURALGAS": 5, "NATGASMINI": 5, "CRUDEOILM": 50}
FILES = [("CO", "MCX Restricted Basket|H|MCXCO"), ("BO", "MCX Restricted Basket|B|MCXBO"),
         ("MIS", "MCX Restricted Basket|I|MCXMIS")]


@H.logged(FOLDER)
def _run(params, ctx):
    pd = H.need("pandas")
    to, cc = K.recipients(params)
    K.require_trading_day(ctx)
    if all(H.already_sent(FOLDER, k, ctx) for k, _ in FILES):
        raise H.Skipped(H.skip_message(FOLDER, [k for k, _ in FILES], "e-mailed"))
    inp, out = paths.proc_dir(FOLDER, "inputs"), paths.proc_dir(FOLDER, "outputs")
    bhav = mcx.latest_bhavcopy(inp, ctx)
    df = pd.read_csv(bhav) if bhav.suffix.lower() == ".csv" else pd.read_excel(bhav)
    ctx.log(f"Bhavcopy {bhav.name}: {len(df):,} row(s).")
    df["XpryDt"] = pd.to_datetime(df["XpryDt"], errors="coerce")
    today = dt.date.today()

    fut = df[(df["StrkPric"] == 0) & df["TckrSymb"].isin(SYMBOLS)]
    excl = {}
    for sym in SYMBOLS:
        s = fut[(fut["TckrSymb"] == sym) & (fut["XpryDt"].dt.date >= today)]
        if s.empty:
            continue
        price = s[s["XpryDt"] == s["XpryDt"].min()].iloc[0]["SttlmPric"]
        atm = int(round(price / STEP[sym]) * STEP[sym])
        excl[sym] = {atm + i * STEP[sym] for i in range(-10, 11)}
        ctx.log(f"{sym}: ATM {atm}")

    c = df[df["TckrSymb"].isin(SYMBOLS) & df["OptnTp"].isin(["CE", "PE"]) & (df["StrkPric"] > 0)].copy()
    c["XpryDt"] = c["XpryDt"].dt.date
    c = c[[(s % STEP[t]) == 0 for t, s in zip(c["TckrSymb"], c["StrkPric"])]]
    parts = []
    for sym in SYMBOLS:
        s = c[c["TckrSymb"] == sym]
        if s.empty:
            continue
        exps = sorted(s["XpryDt"].dropna().unique())
        cur = [e for e in exps if e.month == today.month and e.year == today.year]
        if cur:
            sel = [cur[0]] + [e for e in exps if e > cur[0]][:1]
        else:
            sel = exps[:2]
        s = s[s["XpryDt"].isin(sel)]
        if sym in excl:
            s = s[~s["StrkPric"].isin(excl[sym])]
        parts.append(s)
        ctx.log(f"{sym}: expiries {[e.strftime('%d-%b-%Y') for e in sel]}")
    if not parts:
        raise RuntimeError("No MCX contracts selected — check the Bhavcopy.")
    res = pd.concat(parts, ignore_index=True)
    ctx.log(f"{len(res):,} contract(s) selected after the ATM ± 10 strikes exclusion; building the CO / BO / MIS files…")
    res["E"] = res["XpryDt"].apply(lambda x: x.strftime("%d-%b-%Y").upper())

    body = ("<html><body style=\"font-family:Calibri;font-size:14px;\"><p>Hi Team,</p><p>Please find attached the "
            "<b>MCX Restricted Basket Blocking File</b> generated using the latest <b>MCX Bhavcopy</b>.</p>"
            "<p><b>File Generation Logic:</b></p><ul>"
            "<li>Futures settlement prices (<b>SttlmPric</b>) give the ATM strike for CRUDEOIL, CRUDEOILM, NATURALGAS and NATGASMINI.</li>"
            "<li>ATM is rounded to the strike interval: CRUDEOIL / CRUDEOILM (50), NATURALGAS / NATGASMINI (5).</li>"
            "<li>Option contracts within <b>ATM ± 10 strikes</b> are excluded.</li>"
            "<li>Current-month nearest expiry and the next immediate expiry are selected.</li></ul>"
            "<p>The attached file is generated through automation and ready for upload in the respective blocking framework.</p>"
            f"<p>Best regards,<br>{K.sig(params)}</p></body></html>")
    made, sent = [], 0
    for tag, header in FILES:
        p = out / f"MCX Restricted Basket_{tag}.txt"
        with open(p, "w", encoding="utf-8") as f:
            f.write(header + "\n")
            for r in res.itertuples(index=False):
                f.write(f"OPTFUT|{r.TckrSymb}|{r.E}|{int(r.StrkPric)}|{r.OptnTp}\n")
        made.append(p)
        if not H.already_sent(FOLDER, tag, ctx):
            H.send_email(f"MCX Restricted Basket - {tag}", body, to, cc, [p], ctx, html=True)
            H.mark_sent(FOLDER, tag)
            sent += 1
    ctx.log("Uploading the CO / BO / MIS files to the Kambala FTP…")
    H.kambala_upload(made, ctx)
    H.backup_files(FOLDER, [bhav] + made, ctx)
    return f"{sent} mail(s) sent ({len(res)} contracts per file)"


register(
    key="mcx_restricted_basket",
    name="MCX RESTRICTED BASKET",
    description="Builds the MCX BO / CO / MIS restricted-basket blocking files from the latest MCX Bhavcopy and "
                "e-mails each separately.",
    run=_run,
    params=K.mail_params("rms_del@mastertrust.co.in", "amit.pundir@mastertrust.co.in", with_wait=False),
    retry_safe=False, parallel_safe=False, category="Blocking files", folder=FOLDER, uses=("mcx_sftp", "smtp", "kambala_ftp"),
)
