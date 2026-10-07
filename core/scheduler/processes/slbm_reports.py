"""SLBM REPORTS — SLB Provisional Margin (S_PROV_MGN) and MG02 files from NSE (segment SLB / reports) for the previous
working day, with section headers added, e-mailed as two separate mails (from nn.py)."""
import csv
import datetime as dt

from core.scheduler import connections, helpers as H, nse, paths
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "SLBM REPORTS"

HDR_PROV = {
    "10": ["Client Code", "Security Symbol", "Security Series", "Expiry Date", "Settlement Type", "Settlement Number",
           "Buy Qty", "Lending Price", "Lending Price Value", "Margin Amount"],
    "20": ["Client Code", "Lending Price Value", "Margin Amount"],
    "30": ["Security Symbol", "Security Series", "Expiry Date", "Buy Qty", "Lending Price Value", "Margin Amount"],
    "40": ["Lending Price Value", "Total Margins"],
}
HDR_MG02 = {
    "10": ["Client Code", "Security Symbol", "Security Series", "Expiry", "Settlement Type", "Settlement Number",
           "Buy Qty", "Buy Value", "Sell Qty", "Sell Value", "Net Open Qty", "Net Open Value", "MTM Price",
           "MTM Profit/Loss", "Margin Amount"],
    "20": ["Client Code", "Margins", "Mtm Loss", "Total Margins"],
    "30": ["Security Symbol", "Security Series", "Expiry", "Open Qty", "Open value", "Margin"],
    "40": ["Margins", "MTM Loss", "Total Margins"],
}


def add_headers(path, hmap):
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.reader(f) if r and any(c.strip() for c in r)]
    if not rows or rows[0][0].strip() not in hmap:
        return                                                  # empty, or headers already applied
    out, cur = [], None
    for r in rows:
        g = r[0].strip()
        if g not in hmap:
            out.append(r)
            continue
        if g != cur:
            if cur is not None:
                out.append([])
            out.append([""] + hmap[g])
            cur = g
        out.append(r)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(out)
    tmp.replace(path)


@H.logged(FOLDER)
def _run(params, ctx):
    K.require_trading_day(ctx)
    inp = paths.proc_dir(FOLDER, "inputs")
    mem = connections.require("nse", "member_code")["member_code"]
    d = H.prev_trading_day(dt.date.today())
    ds = f"{d:%d%m%Y}"
    jobs = [("prov", f"S_PROV_MGN_{mem}_{ds}.csv", HDR_PROV, "SLBM Provisional Margin Report",
             "SLBM Provisional Margin (S_PROV_MGN)"),
            ("mg02", f"S_MG02_{mem}_{ds}.csv", HDR_MG02, "SLBM MG02 Report", "SLBM MG02 (S_MG02)")]
    if all(H.already_sent(FOLDER, k, ctx) for k, *_ in jobs):
        raise H.Skipped(H.skip_message(FOLDER, [k for k, *_ in jobs], "e-mailed"))
    sent, missing, done = 0, [], []
    for key, fname, hmap, subj, heading in jobs:
        if H.already_sent(FOLDER, key, ctx):
            continue
        to, cc = H.parse_emails(params.get(f"{key}_to")), H.parse_emails(params.get(f"{key}_cc"))
        if not to:
            raise ValueError(f"No 'To' address for the {heading} mail (Recipients button).")
        # SLB reports live in the member area
        f = nse.download("member", "SLB", "reports", fname, inp, ctx) if not K.wait_of(params) else \
            nse.download_wait("member", "SLB", "reports", fname, inp, ctx, K.wait_of(params))
        if not f:
            missing.append(fname)
            continue
        add_headers(f, hmap)
        body = (f"<html><body>Hi Team,<br><br>Please find attached the {heading} report for {d:%d-%m-%Y}:<br>"
                f"&nbsp;&nbsp;- {fname}<br><br>Please review the data and let us know if you notice any discrepancies."
                f"<br><br>{K.sig(params)}</body></html>")
        H.send_email(f"{subj} - {d:%d-%m-%Y}", body, to, cc, [f], ctx, html=True)
        H.mark_sent(FOLDER, key)
        sent += 1
        done.append(f)
    H.backup_files(FOLDER, done, ctx)
    if missing and not sent:
        raise FileNotFoundError(f"Not on NSE yet: {', '.join(missing)}")
    return f"{sent} mail(s) sent" + (f"; not available: {', '.join(missing)}" if missing else "")


_CC = "sandeep.kumar2@mastertrust.co.in,amit.pundir@mastertrust.co.in,sumit.aggarwal@mastertrust.co.in,rms_del@mastertrust.co.in"
register(
    key="slbm_reports",
    name="SLBM REPORTS",
    description="Downloads the SLB provisional-margin and MG02 files for the previous working day, adds headers and "
                "e-mails each one separately.",
    run=_run,
    params=[
        Param("prov_to", "Provisional margin — To", "text", required=True, fmt="emails", scope="process",
              default="sachin.kheria@mastertrust.co.in,hs@mastertrust.co.in,rks@mastertrust.co.in,va@mastertrust.co.in,manish.chand@mastertrust.co.in,lalit.pant@mastertrust.co.in"),
        Param("prov_cc", "Provisional margin — Cc", "text", fmt="emails", scope="process", default=_CC),
        Param("mg02_to", "MG02 — To", "text", required=True, fmt="emails", scope="process",
              default="sachin.kheria@mastertrust.co.in,va@mastertrust.co.in,manish.chand@mastertrust.co.in,lalit.pant@mastertrust.co.in"),
        Param("mg02_cc", "MG02 — Cc", "text", fmt="emails", scope="process", default=_CC),
        Param("signature", "E-mail signature", "text", default=K.SIGNATURE),
        Param("wait_minutes", "Wait for the file (minutes)", "int", default=0),
    ],
    retry_safe=False, parallel_safe=False, category="NSE files", folder=FOLDER, uses=("nse", "smtp"),
)
