"""CIRCUIT VALIDATION — NSE + BSE cash-market Bhavcopy and NSE security file (public sites) → upper / lower circuit
status per ISIN with a consecutive-day count; Excel report e-mailed (from CIRCUIT_VALIDATION_SCHEDULER.py)."""
import datetime as dt
import gzip
import json
import shutil
import time
import zipfile

from core.scheduler import helpers as H, paths
from core.scheduler.catalog import register
from core.scheduler.processes import _kit as K

FOLDER = "CIRCUIT VALIDATION"
NSE_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com/all-reports"}
BSE_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/markets/MarketInfo/BhavCopy.aspx"}


def _get(s, url, headers, retries=5, timeout=20):
    for _ in range(retries):
        try:
            r = s.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200 or r.status_code in (403, 404):
                return r
        except Exception:
            pass
        time.sleep(1)
    return None


def _ok(r, n=1000):
    return r is not None and r.status_code == 200 and len(r.content) > n


@H.logged(FOLDER)
def _run(params, ctx):
    pd = H.need("pandas")
    H.need("openpyxl")
    requests = H.need("requests")
    to, cc = K.recipients(params)
    K.require_trading_day(ctx)
    if H.already_sent(FOLDER, "mail", ctx):
        raise H.Skipped(H.skip_message(FOLDER, ["mail"], "e-mailed"))
    inp, out, state = (paths.proc_dir(FOLDER, x) for x in ("inputs", "outputs", "state"))
    s = requests.Session()

    # ── NSE bhavcopy + security file: today, then back up to 2 more days
    nse_ymd = None
    bhav_csv = sec_csv = None
    for i in range(3):
        d = dt.date.today() - dt.timedelta(days=i)
        ymd, dmy = d.strftime("%Y%m%d"), d.strftime("%d%m%Y")
        bf = f"BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
        r = _get(s, f"https://nsearchives.nseindia.com/content/cm/{bf}", NSE_H)
        if not _ok(r):
            continue
        zp = inp / bf
        zp.write_bytes(r.content)
        bhav_csv = K.unzip_first(zp, inp)
        zp.unlink()
        nse_ymd = ymd
        mf = f"NSE_CM_security_{dmy}.csv.gz"
        r2 = _get(s, f"https://nsearchives.nseindia.com/content/cm/interop/{mf}", NSE_H)
        if _ok(r2, 500):
            gp = inp / mf
            gp.write_bytes(r2.content)
            sec_csv = K.gunzip(gp)
            gp.unlink()
        break
    if not nse_ymd:
        raise FileNotFoundError("NSE Bhavcopy was not found for the last 3 days.")
    if not sec_csv:
        raise FileNotFoundError("NSE security file (NSE_CM_security) is not available for that date yet.")

    # ── BSE bhavcopy: same date, else the day before
    bse_csv = None
    for ds in (nse_ymd, (dt.datetime.strptime(nse_ymd, "%Y%m%d") - dt.timedelta(days=1)).strftime("%Y%m%d")):
        name = f"BhavCopy_BSE_CM_0_0_0_{ds}_F_0000.CSV"
        for _ in range(3):
            r = _get(s, f"https://www.bseindia.com/download/BhavCopy/Equity/{name}", BSE_H)
            if _ok(r):
                bse_csv = inp / name
                bse_csv.write_bytes(r.content)
                break
            time.sleep(2)
        if bse_csv:
            break
    if not bse_csv:
        raise FileNotFoundError("BSE Bhavcopy was not found (same date or the day before).")
    bse_date = nse_ymd
    if H.re.search(r"(\d{8})", bse_csv.name).group(1) != nse_ymd:
        raise RuntimeError("Date mismatch between the BSE and NSE files — not sending a report on mixed dates.")

    # ── history of consecutive circuit days (kept in the process's state folder)
    hist_f = state / "Circuit_History.csv"
    prev_f = state / "Circuit_History_prev.csv"
    empty = pd.DataFrame(columns=["Symbol", "Last_Status", "Count", "Date"])
    hist = pd.read_csv(hist_f) if hist_f.exists() else empty
    if len(hist) and str(hist["Date"].astype(str).max()) == bse_date:
        # already counted for this date — a repeat run must start from the day before, not count twice
        hist = pd.read_csv(prev_f) if prev_f.exists() else empty
    hist = hist.sort_values("Date").drop_duplicates(subset=["Symbol"], keep="last")
    hmap = hist.set_index("Symbol").to_dict("index")

    bse_df, nse_df, sec = pd.read_csv(bse_csv), pd.read_csv(bhav_csv), pd.read_csv(sec_csv)
    sec.columns = sec.columns.str.strip()
    sec["ElgbltyNrmlMkt"] = pd.to_numeric(sec["ElgbltyNrmlMkt"], errors="coerce")
    sec = sec[sec["ElgbltyNrmlMkt"] != 0][["TckrSymb", "SctySrs", "PricRg", "ISIN"]].rename(
        columns={"TckrSymb": "Symbol", "SctySrs": "Series", "PricRg": "CreditRating"})
    sec[["MIN", "MAX"]] = sec["CreditRating"].astype(str).str.split("-", expand=True)
    sec["MIN"], sec["MAX"] = pd.to_numeric(sec["MIN"], errors="coerce"), pd.to_numeric(sec["MAX"], errors="coerce")
    agg = {"OpnPric": "max", "HghPric": "max", "LwPric": "min", "ClsPric": "max", "LastPric": "max",
           "PrvsClsgPric": "max", "TtlTradgVol": "max"}
    cols = list(agg) + ["ISIN"]
    bse_d = bse_df[cols].groupby("ISIN").agg(agg).reset_index().set_index("ISIN").to_dict("index")
    nse_d = nse_df[cols].groupby("ISIN").agg(agg).reset_index().set_index("ISIN").to_dict("index")

    rows, trig = [], []
    for sr in sec.itertuples(index=False):
        lo, hi = sr.MIN, sr.MAX
        if pd.isna(lo) or pd.isna(hi):
            continue

        def status(row):
            if row is None:
                return "NO_DATA", None
            tc, up, dn = None, False, False
            for col in ("OpnPric", "HghPric", "LwPric", "ClsPric", "LastPric"):
                v = row[col]
                if pd.notna(v):
                    if v >= hi:
                        up, tc = True, tc or col
                    elif v <= lo:
                        dn, tc = True, tc or col
            return ("UPPER CIRCUIT", tc) if up else ("LOWER CIRCUIT", tc) if dn else ("OK", None)
        b, n = bse_d.get(sr.ISIN), nse_d.get(sr.ISIN)
        bs, bt = status(b)
        ns, nt = status(n)
        circ = ("UPPER CIRCUIT", "LOWER CIRCUIT")
        if n is not None:
            if ns in circ:
                fin, st, src, tc = n, ns, "NSE", nt
            elif bs in circ:
                fin, st, src, tc = b, bs, "BSE", bt
            else:
                fin, st, src, tc = n, ns, "NSE", nt
        elif b is not None:
            fin, st, src, tc = b, bs, "BSE", bt
        else:
            continue
        if st in circ and tc is None:
            tc = "ClsPric"
        prev = hmap.get(sr.Symbol)
        cnt = (min(prev["Count"] + 1, 5) if prev and prev["Last_Status"] == st else 1) if st in circ else 0
        rows.append({"Symbol": sr.Symbol, "Series": sr.Series, "ISIN": sr.ISIN, "PricRg": sr.CreditRating,
                     "OpnPric": fin["OpnPric"], "HghPric": fin["HghPric"], "LwPric": fin["LwPric"],
                     "ClsPric": fin["ClsPric"], "LastPric": fin["LastPric"], "PrvsClsgPric": fin["PrvsClsgPric"],
                     "TtlTradgVol": fin["TtlTradgVol"], "Status": st, "MARKING": src, "COUNT": cnt})
        trig.append(tc)
    fdf = pd.DataFrame(rows)
    keep = [i for i, v in enumerate(fdf["Series"] != "T0") if v]
    trig = [trig[i] for i in keep]
    fdf = fdf.iloc[keep].reset_index(drop=True)

    report = out / f"Circuit_Validation_Report_{bse_date}.xlsx"
    fdf.to_excel(report, index=False)
    from openpyxl import load_workbook
    from openpyxl.styles import Font
    wb = load_workbook(report)
    ws = wb.active
    hm = {c.value: i for i, c in enumerate(ws[1])}
    red, blue = Font(color="FFFF0000", bold=True), Font(color="FF000080", bold=True)
    for i, row in enumerate(ws.iter_rows(min_row=2)):
        tc = trig[i] if i < len(trig) else None
        if tc in hm:
            stt = row[hm["Status"]].value
            if stt == "LOWER CIRCUIT":
                row[hm[tc]].font = red
            elif stt == "UPPER CIRCUIT":
                row[hm[tc]].font = blue
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(len(str(c.value)) for c in col if c.value is not None) + 2
    wb.save(report)

    body = (f"<html><body style=\"font-family: Calibri, Arial, sans-serif; font-size: 14px; color: #333;\">"
            f"<p>Dear Sir,</p><p>Please find attached the <b>Circuit Validation Report</b> for <b>{bse_date}</b>.</p>"
            "<p><b>Report Highlights:</b></p><ul>"
            "<li>Data sourced from the <b>NSE and BSE cash Bhavcopy files</b> along with the <b>NSE security file</b></li>"
            "<li>Only eligible securities (<b>ElgbltyNrmlMkt not 0</b>) are considered</li>"
            "<li>Price range (<b>PricRg</b>) is split into <b>lower and upper limits</b>; Open, High, Low, Close and Last price are checked</li>"
            "<li>Data is aggregated at <b>ISIN level</b></li>"
            "<li><b>COUNT</b> = consecutive days (max 5) a symbol is at the same circuit; resets otherwise</li>"
            "<li><b>Exchange selection:</b> NSE has priority; if NSE is within range but BSE breaches, BSE is used; if NSE has no data, BSE is the fallback</li>"
            "<li>Status: <b>UPPER CIRCUIT</b> (at/above upper limit), <b>LOWER CIRCUIT</b> (at/below lower limit), <b>OK</b></li>"
            "<li>Series <b>T0</b> is excluded; symbols ending with “$” are BSE-exclusive</li></ul>"
            f"<p>Please review the report and take necessary action wherever applicable.</p>"
            f"<p>Warm regards,<br>{K.sig(params)}</p></body></html>")
    H.send_email("Daily Circuit Validation Report", body, to, cc, [report], ctx, html=True)
    H.mark_sent(FOLDER, "mail")
    # history is saved only AFTER the mail went out, so a failed run can be repeated without double counting
    if hist_f.exists():
        try:
            cur = pd.read_csv(hist_f)
            if len(cur) and str(cur["Date"].astype(str).max()) != bse_date:
                shutil.copyfile(hist_f, prev_f)
        except Exception:
            pass
    nh = fdf.assign(Date=bse_date)[["Symbol", "Status", "COUNT", "Date"]]
    nh.columns = ["Symbol", "Last_Status", "Count", "Date"]
    nh.to_csv(hist_f, index=False)
    H.backup_files(FOLDER, [bse_csv, bhav_csv, sec_csv, report], ctx)
    n_c = int((fdf["Status"].isin(["UPPER CIRCUIT", "LOWER CIRCUIT"])).sum())
    return f"report e-mailed for {bse_date} ({len(fdf)} securities, {n_c} at circuit)"


register(
    key="circuit_validation",
    name="CIRCUIT VALIDATION",
    description="Compares NSE/BSE prices with the circuit price bands, counts consecutive circuit days and e-mails the "
                "Excel report.",
    run=_run,
    params=K.mail_params("vipin@mastertrust.co.in,nitingupta@mastertrust.co.in,rms_del@mastertrust.co.in",
                         "sandeep.kumar2@mastertrust.co.in,sumit.aggarwal@mastertrust.co.in", with_wait=False),
    retry_safe=False, parallel_safe=False, category="Reports", folder=FOLDER, uses=("smtp",),
)
