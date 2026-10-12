"""ATOM VS BANK ALERT — today's ATOM transactions (merchant console export) reconciled with today's Bank Alert data
(back-office API); Excel report by e-mail, mismatches as an image on WhatsApp (from the three scripts in
ATOM VS BANK ALERT\\codes).  Meant to run several times a day, so there is no 'already sent' block."""
import base64
import datetime as dt
import re
import time

from core.scheduler import connections, helpers as H, paths
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "ATOM VS BANK ALERT"
ATOM = "https://titan.atomtech.in/titan_merchant_console"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

EXP_HEADERS = [
    "Merchant Name", "Merchant ID", "Client Code", "Atom Txn ID", "Merchant Txn ID", "Amount", "Currency", "Txn Date",
    "Product", "Txn Type", "Bank Name", "Txn Status", "Recon Status", "Bank Ref No", "Auth No.", "Customer Acc. No.",
    "IFSC Code", "Settlement Date", "Merchant Type", "Discriminator", "EMI Bank", "Card Issuing Bank", "EMI Tenure",
    "Udf1", "Udf2", "Udf3", "Udf4", "Udf9", "Card Number", "EMI Status", "EMI Reason", "EMI Date", "Txn Charges", "GST",
    "SB Cess", "Krishi Kalyan Cess", "Total Chargeable", "Net Amount to be Paid", "Settlement Date", "Address Line 1",
    "Address Line 2", "Country", "State", "City", "Zip Code", "Description", "Refund Amount", "Refund Initiated Date",
    "Refund Processed Date", "Refund Closed Date", "Refund Closing Remarks", "Card Type", "Scheme", "Beneficiary Name",
    "IMPS Status", "Settlement Type", "UDFEX1", "UDFEX2", "UDFEX3", "UDFEX4", "UDFEX5", "UDFEX6", "UDFEX7", "UDFEX8",
    "UDFEX9", "UDFEX10", "QR Transaction Type", "Atom MW MID", "Atom TID", "Switch MID", "Switch TID", "NdpsProcCode",
    "UMN", "executionTxnId"]

OK = "✅ Matched"
CTCL_TXT = "CTCL LIMIT UPDATED BY TRADING SYSTEM"


def fetch_atom(out_dir, ctx, wait_s=600):
    requests = H.need("requests")
    c = connections.require("atom", "username", "password")
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    T = 120
    s.get(f"{ATOM}/SessionExpired", headers={"Accept": HTML}, timeout=T)
    if "JSESSIONID" not in s.cookies.get_dict():
        raise RuntimeError("ATOM: could not open a session.")
    r = s.post(f"{ATOM}/Login", data={"userName": c["username"], "userPswd": c["password"]},
               headers={"Accept": HTML, "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://titan.atomtech.in", "Referer": f"{ATOM}/SessionExpired"},
               allow_redirects=True, timeout=T)
    if r.status_code != 200 or "SessionExpired" in r.url or "userPswd" in r.text:
        # say WHAT the portal answered (never the password) so a wrong password can be told apart from a
        # locked account, a second live session, a blocked IP or a changed login page
        ctx.log(f"ATOM login answered: HTTP {r.status_code}, final page {r.url}")
        ctx.log(f"ATOM user name used: {c['username']} (password length {len(c['password'])})")
        text = re.sub(r"\s+", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text, flags=re.S | re.I))
        text = re.sub(r"<[^>]+>", " ", text)
        hints = [m.group(0).strip()[:200] for m in re.finditer(
            r"[^.!?]{0,80}(invalid|incorrect|wrong|locked|blocked|disabled|already|expired|captcha|attempt|inactive)[^.!?]{0,80}",
            text, flags=re.I)][:4]
        for h in hints:
            ctx.log(f"ATOM page says: {h}")
        raise RuntimeError("ATOM login failed — check the user name / password in Connections "
                           "(the lines above show what the ATOM site answered).")
    ctx.log("ATOM login OK.")
    s.get(f"{ATOM}/Dashboard", headers={"Accept": HTML}, timeout=T)
    s.get(f"{ATOM}/view-transaction-temp", headers={"Accept": HTML, "Referer": f"{ATOM}/Dashboard"}, timeout=T)

    row_re, td_re = re.compile(r"<tr>(.*?)</tr>", re.S | re.I), re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
    badge_re = re.compile(r'<span class="badge\s+(badge-[a-z]+)"[^>]*>\s*(.*?)\s*</span>', re.S | re.I)
    id_re = re.compile(r"downloadReportFile\?id=(\d+)", re.I)

    def rows():
        resp = s.get(f"{ATOM}/viewDownloads", headers={"Accept": HTML, "Referer": f"{ATOM}/view-transaction-temp"}, timeout=T)
        if resp.status_code != 200 or "reportTableBody" not in resp.text:
            raise RuntimeError("ATOM: 'My Downloads' page could not be opened (session expired?).")
        body = resp.text.split('id="reportTableBody"', 1)[1].split("</tbody>", 1)[0]
        out = []
        for h in row_re.findall(body):
            cells = td_re.findall(h)
            if len(cells) < 2:
                continue
            b, i = badge_re.search(h), id_re.search(h)
            out.append({"name": re.sub(r"<[^>]+>", "", cells[0]).strip(), "cls": b.group(1).lower() if b else "",
                        "txt": re.sub(r"<[^>]+>", "", b.group(2)).strip() if b else "", "id": int(i.group(1)) if i else None})
        return out
    before = rows()
    max_id = max([r["id"] for r in before if r["id"] is not None] or [0])
    names0 = {r["name"] for r in before}

    today = dt.datetime.now().strftime("%d/%m/%Y")
    eh = [("expHead[]", h) for h in EXP_HEADERS]
    flt = [("fromdate", today), ("todate", today), ("transactionid", ""), ("channeltype", "0"), ("product", "0"),
           ("transactiontype[]", "0"), ("bank[]", "0"), ("merchantid", ""), ("transactionstatus[]", "0"),
           ("reconcilation[]", "0"), ("reportType", "ALL"), ("accountId[]", "0"), ("searchBy", "0"), ("searchTo", "")]
    ajax = {"Accept": "*/*", "X-Requested-With": "XMLHttpRequest", "Origin": "https://titan.atomtech.in",
            "Referer": f"{ATOM}/view-transaction-temp"}
    t = s.get(f"{ATOM}/downloadTxnsTemp", params=eh + flt + [("fileExtension", ".xlsx"), ("pageNo", "-1"),
                                                           ("maskingFlag", "1"), ("pswd", "")], headers=ajax, timeout=T)
    if t.status_code != 200 or '"YES"' not in t.text:
        raise RuntimeError("ATOM export could not be prepared (no transactions today, or the session expired).")
    p = s.post(f"{ATOM}/XLdownloadTxns", data=eh + [("fileExt", ".xlsx")] + flt + [("maskingFlag", "1"), ("pswd", "")],
               headers={**ajax, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}, timeout=T)
    if p.status_code != 200 or "SUCCESS" not in p.text.upper():
        raise RuntimeError("ATOM did not accept the report request.")
    ctx.log("ATOM report requested — waiting for it in My Downloads…")

    rep, waited = None, 0
    while waited <= wait_s:
        new = [r for r in rows() if r["name"].lower().endswith(".xlsx") and (
            (r["id"] is not None and r["id"] > max_id) or (r["id"] is None and r["name"] not in names0))]
        ready = [r for r in new if r["id"] is not None and "success" in r["cls"]]
        if ready:
            rep = max(ready, key=lambda r: r["id"])
            break
        bad = [r for r in new if "danger" in r["cls"]]
        if bad and len(bad) == len(new):
            raise RuntimeError(f"ATOM report generation failed: {bad[0]['name']} ({bad[0]['txt']})")
        if getattr(ctx, "cancelled", False):
            raise RuntimeError("Cancelled.")
        time.sleep(10)
        waited += 10
    if not rep:
        raise RuntimeError(f"ATOM report was not ready after {wait_s} s.")
    d = s.get(f"{ATOM}/downloadReportFile", params={"id": rep["id"]},
              headers={"Accept": HTML, "Referer": f"{ATOM}/viewDownloads"}, timeout=T)
    if d.status_code != 200 or d.content[:2] != b"PK":
        raise RuntimeError("ATOM download did not return an Excel file.")
    out_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[<>:"/\\|?*]', "_", rep["name"])
    p = out_dir / name
    p.write_bytes(d.content)
    ctx.log(f"ATOM file saved: {name}")
    return p


def fetch_bank(out_dir, ctx):
    requests = H.need("requests")
    pd = H.need("pandas")
    H.need("cryptography")
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    c = connections.require("backoffice", "base_url", "username", "password", "client_ip", "client_mac")
    base = c["base_url"].rstrip("/")
    s = requests.Session()
    key = s.get(f"{base}/api/Common/SessionKey/Get", timeout=30).text.replace('"', "").encode()
    pdr = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    pw = base64.b64encode(enc.update(pdr.update(c["password"].encode()) + pdr.finalize()) + enc.finalize()).decode()
    lg = s.post(f"{base}/api/token", data={"CallFrom": "FINKORP", "UserName": c["username"], "Password": pw,
                                            "Grant_type": "password", "ClientIP": c["client_ip"],
                                            "ClientMAC": c["client_mac"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    try:
        token = lg.json()["access_token"]
    except Exception:
        raise RuntimeError(f"Back-office login failed (HTTP {lg.status_code}) — check Connections → Back-office API.")
    now = dt.datetime.now()
    fy = f"{now.year}-{now.year + 1}" if now.month >= 4 else f"{now.year - 1}-{now.year}"
    day = now.strftime("%Y-%m-%d")
    r = s.get(f"{base}/api/Vouchers/BankAlert/Get", headers={"Authorization": f"Bearer {token}", "FIRMID": "1001",
                                                             "FINANCIALYEAR": fy},
              params={"FromDate": day, "ToDate": day}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Back-office Bank Alert API answered HTTP {r.status_code}.")
    data = r.json()
    if not data:
        raise RuntimeError("Back-office returned no Bank Alert rows for today.")
    df = pd.DataFrame(data)
    df.drop(columns=["FirmID", "RowID"], errors="ignore", inplace=True)
    df.rename(columns={"FirmBankCode": "Firm Bank Code", "AlertID": "Alert ID", "AlertDate": "Alert Date",
                       "AlertFrom": "Alert From", "RemitterBankAcNo": "Remitter Bank AcNo",
                       "RemitterName": "Remitter Name", "RemitterBankName": "Remitter Bank Name",
                       "UserReferenceNo": "User Reference No", "DCFlag": "DC Flag", "PaymentMode": "Payment Mode",
                       "AccountID": "Account ID", "AccountName": "Account Name",
                       "FinancialPostDate": "Financial Post Date", "CTCLTransferFlag": "CTCL Transfer Flag",
                       "CTCLTransferDate": "CTCL Transfer Date", "CTCLResponse": "CTCL Response",
                       "AllocationUploadFlag": "Allocation Upload Flag", "SubBranch": "Sub Branch",
                       "FamilyID": "Family ID", "RmtlID": "Rmtl ID", "AllocationStartDate": "Allocation Start Date",
                       "AllocationEndDate": "Allocation End Date"}, inplace=True)
    for col in ("Alert Date", "Financial Post Date", "CTCL Transfer Date", "Allocation Start Date", "Allocation End Date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{now:%d%m%Y%H%M%S}.xlsx"
    df.to_excel(p, index=False)
    ctx.log(f"Bank alert rows: {len(df)}")
    return p


def _status(row, pd):
    txn = str(row.get("TXN STATUS(ATOM)", "")).strip().upper()
    resp = str(row.get("CTCL Response", "")).strip()
    if resp.upper() in ("", "NAN", "NONE"):
        resp = ""
    ctcl = resp.upper() == CTCL_TXT
    if txn == "FAILED" and ctcl:
        return "❌ Mismatch due to Txn Status"
    if pd.isna(row["AMOUNT_ATOM_FILE"]):
        return "❌ Missing in ATOM FILE"
    if pd.isna(row["AMOUNT_BANK_FILE"]):
        return "❌ Missing in BANK ALERT FILE"
    if abs(row["AMOUNT_ATOM_FILE"] - row["AMOUNT_BANK_FILE"]) > 0.01:
        return "❌ Amount Mismatch"
    if txn == "OK" and not ctcl:
        return "⚠️ CTCL TO BE CHECKED"
    return OK


def _alert_image(df, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, min(10, len(df) * 0.6 + 1)))
    ax.axis("off")
    t = ax.table(cellText=df.values, colLabels=df.columns, loc="center", cellLoc="center")
    t.auto_set_font_size(False)
    t.set_fontsize(11)
    t.scale(1.2, 1.5)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_text_props(weight="bold")
            cell.set_facecolor("#D9D9D9")
            cell.set_height(0.15)
        else:
            cell.set_height(0.12)
        if r > 0 and c == 2:
            txt = cell.get_text().get_text()
            cell.set_facecolor("#FFCCCC" if "❌" in txt else "#FFF2CC" if "⚠" in txt else "#CCFFCC")
        cell.set_edgecolor("black")
        cell.set_linewidth(1.5)
    fig.savefig(path, bbox_inches="tight", dpi=200)
    plt.close(fig)


@H.logged(FOLDER)
def _run(params, ctx):
    pd = H.need("pandas")
    H.need("openpyxl")
    to, cc = K.recipients(params)
    phones = H.parse_phones(params.get("phones"))
    K.require_trading_day(ctx)
    inp, out = paths.proc_dir(FOLDER, "inputs"), paths.proc_dir(FOLDER, "outputs")
    stamp = dt.datetime.now()

    # a broken connection restarts that whole portal login + download from the beginning (5 s wait, up to 5 times)
    atom_f = H.restart_on_network_error(ctx, "ATOM", lambda: fetch_atom(inp, ctx))
    bank_f = H.restart_on_network_error(ctx, "Back-office", lambda: fetch_bank(inp, ctx))
    txn_full, bank_full = pd.read_excel(atom_f), pd.read_excel(bank_f)

    txn = txn_full.rename(columns={"Client Code": "CLIENT_CODE", "Atom Txn ID": "ALERT_ID", "Amount": "AMOUNT_ATOM_FILE",
                                   "Txn Date": "Txn Date(ATOM)", "Txn Status": "TXN STATUS(ATOM)"})
    bank = bank_full.rename(columns={"UCC": "CLIENT_CODE", "Alert ID": "ALERT_ID", "Amount": "AMOUNT_BANK_FILE",
                                     "Alert From": "ALERT_FROM", "Financial Post Date": "Financial Post Date(BANK_ALERT)",
                                     "Allocation Upload Flag": "Allocation Upload Flag(BANK_ALERT)"})
    bank["ALERT_FROM"] = bank["ALERT_FROM"].astype(str).str.strip().str.upper()
    bank = bank[bank["ALERT_FROM"] == "ATOM"]
    txn = txn[["CLIENT_CODE", "ALERT_ID", "AMOUNT_ATOM_FILE", "Txn Date(ATOM)", "TXN STATUS(ATOM)"]].copy()
    bank = bank[["CLIENT_CODE", "ALERT_ID", "AMOUNT_BANK_FILE", "Financial Post Date(BANK_ALERT)",
                 "Allocation Upload Flag(BANK_ALERT)", "CTCL Transfer Date", "CTCL Response"]].copy()
    for col in ("CLIENT_CODE", "ALERT_ID"):
        txn[col] = txn[col].astype(str).str.strip()
        bank[col] = bank[col].astype(str).str.strip()
    txn["AMOUNT_ATOM_FILE"] = pd.to_numeric(txn["AMOUNT_ATOM_FILE"], errors="coerce")
    bank["AMOUNT_BANK_FILE"] = pd.to_numeric(bank["AMOUNT_BANK_FILE"], errors="coerce")
    comb = pd.merge(txn, bank, on=["CLIENT_CODE", "ALERT_ID"], how="outer")
    comb["STATUS"] = comb.apply(lambda r: _status(r, pd), axis=1)
    comb = comb.reindex(columns=["CLIENT_CODE", "ALERT_ID", "AMOUNT_ATOM_FILE", "AMOUNT_BANK_FILE", "Txn Date(ATOM)",
                                 "Financial Post Date(BANK_ALERT)", "TXN STATUS(ATOM)",
                                 "Allocation Upload Flag(BANK_ALERT)", "CTCL Transfer Date", "CTCL Response", "STATUS"])
    st = comb["STATUS"]
    alert = comb[st.isin(["❌ Amount Mismatch", "❌ Mismatch due to Txn Status", "❌ Missing in ATOM FILE",
                          "⚠️ CTCL TO BE CHECKED"]) |
                 ((st == "❌ Missing in BANK ALERT FILE") & (comb["TXN STATUS(ATOM)"].astype(str).str.upper() == "OK"))]
    alert = alert[["CLIENT_CODE", "AMOUNT_ATOM_FILE", "STATUS"]]

    from openpyxl.utils import get_column_letter
    report = out / f"Reconciliation_Report_{stamp:%Y%m%d}.xlsx"
    bank_atom = bank_full[bank_full["Alert From"].astype(str).str.strip().str.upper() == "ATOM"]
    with pd.ExcelWriter(report, engine="openpyxl") as w:
        comb.to_excel(w, sheet_name="Final_Reco", index=False)
        txn_full.to_excel(w, sheet_name="ATOM_FILE_DATA", index=False)
        bank_atom.to_excel(w, sheet_name="BANK_ALERT_FILE_DATA", index=False)
        for ws in w.sheets.values():
            for cells in ws.columns:
                ws.column_dimensions[get_column_letter(cells[0].column)].width = \
                    max([len(str(c.value)) for c in cells if c.value] or [8]) + 2
    ctx.log(f"Report built: {len(comb)} row(s), {len(alert)} needing attention.")

    body = ("<html><body><p>Hi Team,</p><p>Please find attached the latest <b>Reconciliation Report</b> between "
            "<b>Atom Transactions</b> and <b>Bank Alert Data</b>.</p><p><b>What this report does:</b></p><ul>"
            "<li>Matches data on <b>Client Code + Alert ID</b></li><li>Compares transaction amounts between both files</li>"
            "<li>Includes only <b>ATOM</b> alerts from the Bank Alert file</li></ul><p><b>Status explanation:</b></p><ul>"
            "<li><b>✅ Matched</b> — in both files and the amount matches</li>"
            "<li><b>❌ Missing in ATOM FILE</b> — in Bank Alert but not in Atom</li>"
            "<li><b>❌ Missing in BANK ALERT FILE</b> — in Atom but not in Bank Alert</li>"
            "<li><b>❌ Amount Mismatch</b> — in both, amounts differ</li>"
            "<li><b>❌ Mismatch due to Txn Status</b> — Txn Status FAILED but CTCL Response is “CTCL Limit Updated By Trading System”</li>"
            "<li><b>⚠️ CTCL TO BE CHECKED</b> — Txn Status OK but CTCL Response is not “CTCL Limit Updated By Trading System”</li></ul>"
            "<p>WhatsApp alert goes out only for the mismatch statuses above (Missing in BANK ALERT only when Txn Status = OK); "
            "if there are none, no WhatsApp is sent.</p>"
            f"<p>Regards,<br>{K.sig(params)}</p></body></html>")
    H.send_email(f"Reconciliation Report - Atom vs Bank Alert | {stamp:%d-%m-%Y %H:%M:%S}", body, to, cc, [report], ctx, html=True)

    note = "no mismatches"
    if len(alert):
        img = out / "Alert.jpg"
        _alert_image(alert, img)
        if phones:
            for ph in phones:
                H.whatsapp_send_image(img, ph, "MISMATCH REPORT ATOM vs BANK ALERT", f"{stamp:%d-%m-%Y}", ctx)
            note = f"{len(alert)} mismatch(es) sent on WhatsApp"
        else:
            ctx.log("No WhatsApp numbers set — mismatch image not sent.")
            note = f"{len(alert)} mismatch(es); no WhatsApp numbers set"
    # keep every run's files in its own numbered backup folder (several runs a day)
    dest = paths.proc_dir(FOLDER, "backup") / H.today_dash()
    dest.mkdir(parents=True, exist_ok=True)
    n = max([int(x.name) for x in dest.iterdir() if x.is_dir() and x.name.isdigit()] or [0]) + 1
    run = dest / str(n)
    run.mkdir()
    for f in (atom_f, bank_f, report, out / "Alert.jpg"):
        if f.is_file():
            f.replace(run / f.name)
    return f"report e-mailed; {note}"


register(
    key="atom_vs_bank_alert",
    name="ATOM VS BANK ALERT",
    description="Pulls today's ATOM transactions and Bank Alert data, reconciles them, e-mails the Excel report and "
                "sends mismatches on WhatsApp. Can run several times a day.",
    run=_run,
    params=K.mail_params("rms_del@mastertrust.co.in", with_wait=False) + [
        Param("phones", "WhatsApp numbers for mismatches", "text", fmt="phones", scope="process",
              help="10-digit numbers, optional #name after each.")],
    retry_safe=False, parallel_safe=False, category="Reports", folder=FOLDER, uses=("atom", "backoffice", "whatsapp", "smtp"),
)
