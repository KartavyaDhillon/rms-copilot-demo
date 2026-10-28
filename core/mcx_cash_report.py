"""core/mcx_cash_report.py - the MCX CASH MAINTENANCE calculation, mail and password lock (from the hand-run
MCX_CASH_MAINTENANCE.py, same formulas, same columns, same two output files).

    inputs   client file  (MCX 90%, RSK335)   columns: Client Code, Client Margin Limit, Margin Utilization, Cli / TM Mrgn 90% (Rs)
             deposit file (MCX, RSK383)       columns: UCC Code, Allocation at EOD, Own Collateral EOD, Own Others Collateral at EOD
             CM_ClientLevelCollaterals_*.csv, FO_ClientLevelCollaterals_*.csv      (NSCCL)
    outputs  MCX_CASH_MAINTENANCE_FULL_FILE.xlsx   every client + GRAND TOTAL
             MCX_CASH_MAINTENANCE.xlsx             top 10 by Cash_Maintenance + top 10 by 90% margin + GRAND TOTAL

No password or mail account is written here: the file password comes from MCX_CASH_FILE_PASSWORD (Credentials) and the
mail goes through the console's own sender (SENDER_EMAIL / SENDER_PASSWORD / SMTP_SERVER in .env).
"""
from __future__ import annotations

import os
import re
import smtplib
import time
import warnings
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

OUT_FULL = "MCX_CASH_MAINTENANCE_FULL_FILE.xlsx"
OUT_SUMMARY = "MCX_CASH_MAINTENANCE.xlsx"
CLIENT_MARGIN_COL = "Cli / TM Mrgn 90% (Rs)"
CLIENT_CODE_COL = "Client Code"
UCC_CODE_COL = "UCC Code"

# the mail defaults of the hand-run script (editable on the card: Email To / CC)
DEFAULT_MAIL = {
    "MCXCASH_FULL_TO": "sandeep.kumar2@mastertrust.co.in,amit.pundir@mastertrust.co.in,sumit.aggarwal@mastertrust.co.in",
    "MCXCASH_FULL_CC": "",
    "MCXCASH_SUMMARY_TO": "va@mastertrust.co.in,rks@mastertrust.co.in",
    "MCXCASH_SUMMARY_CC": "sandeep.kumar2@mastertrust.co.in,amit.pundir@mastertrust.co.in,sumit.aggarwal@mastertrust.co.in",
}
DEFAULT_PASSWORD = "mcx123"          # the password the recipients already know; change it in Credentials


def apply_defaults():
    for k, v in DEFAULT_MAIL.items():
        os.environ.setdefault(k, v)
    os.environ.setdefault("MCX_CASH_FILE_PASSWORD", DEFAULT_PASSWORD)


# ───────────────────────────── reading ─────────────────────────────
def read_excel_dynamic(path, max_rows=10):
    import pandas as pd
    pre = pd.read_excel(path, header=None, nrows=max_rows)
    hdr = None
    for i in range(len(pre)):
        row = pre.iloc[i].astype(str).str.lower()
        if "client code" in row.values or "ucc code" in row.values or "cli / tm mrgn 90%" in row.values \
                or CLIENT_MARGIN_COL.lower() in row.values:
            hdr = i
            break
    if hdr is None:
        raise ValueError(f"Header not found in {os.path.basename(path)}")
    df = pd.read_excel(path, header=hdr)
    df.columns = df.columns.astype(str).str.strip()
    return df


def classify_xlsx(path):
    """'client' (MCX 90%), 'deposit' (MCX deposit) or None - by the columns, not by the name."""
    try:
        cols = list(read_excel_dynamic(path, 12).columns)
    except Exception:
        return None
    if CLIENT_MARGIN_COL in cols:
        return "client"
    if UCC_CODE_COL in cols and "Allocation at EOD" in cols:
        return "deposit"
    return None


def clean_numeric(series):
    return (series.astype(str).str.replace(",", "", regex=False).str.replace("₹", "", regex=False).str.strip()
            .replace("", "0").replace("nan", "0").astype(float))


# ───────────────────────────── calculation ─────────────────────────────
def compute(client_xlsx, deposit_xlsx, cm_csv, fo_csv, log=print):
    """-> (final_df with GRAND TOTAL row, grand_total dict, base_df without it)."""
    import pandas as pd
    client_df = read_excel_dynamic(client_xlsx)
    deposit_df = read_excel_dynamic(deposit_xlsx)
    log(f"Client rows {len(client_df)}, deposit rows {len(deposit_df)}")

    for c in (CLIENT_MARGIN_COL, "Client Margin Limit", "Margin Utilization"):
        client_df[c] = clean_numeric(client_df[c])
    client_df = client_df[[CLIENT_CODE_COL, "Client Margin Limit", "Margin Utilization", CLIENT_MARGIN_COL]]

    for c in ("Allocation at EOD", "Own Collateral EOD", "Own Others Collateral at EOD"):
        deposit_df[c] = clean_numeric(deposit_df[c])
    deposit_df = deposit_df[[UCC_CODE_COL, "Allocation at EOD", "Own Collateral EOD", "Own Others Collateral at EOD"]]
    deposit_df = deposit_df.rename(columns={"Allocation at EOD": "MCX_ledger", "Own Collateral EOD": "MCX Cash Equivalent Collateral"})

    client_df[CLIENT_CODE_COL] = client_df[CLIENT_CODE_COL].astype(str).str.strip().str.upper()
    deposit_df[UCC_CODE_COL] = deposit_df[UCC_CODE_COL].astype(str).str.strip().str.upper()

    final = pd.merge(client_df, deposit_df, left_on=CLIENT_CODE_COL, right_on=UCC_CODE_COL, how="outer")
    final[UCC_CODE_COL] = final[UCC_CODE_COL].fillna(final[CLIENT_CODE_COL])
    final = final.drop(columns=[CLIENT_CODE_COL])
    final[UCC_CODE_COL] = final[UCC_CODE_COL].astype(str).str.strip()
    final = final[(final[UCC_CODE_COL] != "") & (final[UCC_CODE_COL].str.lower() != "nan")]
    for c in ("MCX_ledger", "MCX Cash Equivalent Collateral", "Own Others Collateral at EOD", "Client Margin Limit",
              "Margin Utilization", CLIENT_MARGIN_COL):
        final[c] = final[c].fillna(0)

    cm, fo = pd.read_csv(cm_csv), pd.read_csv(fo_csv)
    for d in (cm, fo):
        d["Client Code"] = d["Client Code"].astype(str).str.strip().str.upper()
    for c in ("Cash allocated (b)", "Cash-Others (c)", "Non-cash Securities (d)"):
        cm[c] = clean_numeric(cm[c])
    for c in ("Cash allocated (b)", "Cash-others (c)", "Non-cash Securities (d)"):
        fo[c] = clean_numeric(fo[c])
    cm_g = cm.groupby("Client Code")[["Cash allocated (b)", "Cash-Others (c)", "Non-cash Securities (d)"]].sum()
    fo_g = fo.groupby("Client Code")[["Cash allocated (b)", "Cash-others (c)", "Non-cash Securities (d)"]].sum()
    fo_g = fo_g.rename(columns={"Cash-others (c)": "Cash-Others (c)"})
    ns = cm_g.add(fo_g, fill_value=0).reset_index().rename(columns={
        "Client Code": UCC_CODE_COL, "Cash allocated (b)": "NSSCL Cash Allocated", "Cash-Others (c)": "NSSCL Cash-Others",
        "Non-cash Securities (d)": "NSSCL Non-Cash Securities"})
    final = final.merge(ns, on=UCC_CODE_COL, how="left")
    for c in ("NSSCL Cash Allocated", "NSSCL Cash-Others", "NSSCL Non-Cash Securities"):
        final[c] = final[c].fillna(0)
    final["TOTAL NSSCL DEPOSIT"] = final["NSSCL Cash Allocated"] + final["NSSCL Cash-Others"] + final["NSSCL Non-Cash Securities"]

    final["MCX Ledger + Cash Equivalent Collateral"] = final["MCX_ledger"] + final["MCX Cash Equivalent Collateral"]
    final["ledger*2"] = final["MCX Ledger + Cash Equivalent Collateral"] * 2
    final["Cash_Maintenance"] = (final[["Margin Utilization", "Client Margin Limit"]].min(axis=1) - final["ledger*2"]).clip(lower=0)
    final = final.sort_values(by="Cash_Maintenance", ascending=False)
    final.loc[final["Own Others Collateral at EOD"] == 0, "Cash_Maintenance"] = 0

    num = final.select_dtypes(include="number").columns
    final[num] = final[num].round(0).astype(int)
    final = final[[UCC_CODE_COL, "MCX_ledger", "MCX Cash Equivalent Collateral", "MCX Ledger + Cash Equivalent Collateral",
                   "Own Others Collateral at EOD", "Client Margin Limit", "Margin Utilization", "Cash_Maintenance",
                   CLIENT_MARGIN_COL, "ledger*2", "NSSCL Cash Allocated", "NSSCL Cash-Others", "NSSCL Non-Cash Securities",
                   "TOTAL NSSCL DEPOSIT"]]
    num = final.select_dtypes(include="number").columns
    total = {c: "" for c in final.columns}
    total[UCC_CODE_COL] = "GRAND TOTAL"
    for c in num:
        total[c] = final[c].sum()
    base = final.copy()
    out = pd.concat([final, pd.DataFrame([total])], ignore_index=True)
    return out, total, base


def summary_frame(base, total, columns):
    import pandas as pd
    top_cash = base.sort_values("Cash_Maintenance", ascending=False).head(10)
    top_margin = base.sort_values(CLIENT_MARGIN_COL, ascending=False).head(10)
    s = pd.concat([top_cash, top_margin]).drop_duplicates(subset=[UCC_CODE_COL], keep="first")
    s = s[list(columns)].sort_values(by=[CLIENT_MARGIN_COL, "Cash_Maintenance"], ascending=[False, False])
    return pd.concat([s, pd.DataFrame([total])], ignore_index=True)


def _format_excel(path):
    from openpyxl import load_workbook
    from openpyxl.styles import Font
    wb = load_workbook(path)
    ws = wb.active
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=0)
        ws.column_dimensions[col[0].column_letter].width = width + 2
    for row in ws.iter_rows():
        if row[0].value == "GRAND TOTAL":
            for c in row:
                c.font = Font(bold=True)
            break
    wb.save(path)


def build_reports(client_xlsx, deposit_xlsx, cm_csv, fo_csv, out_dir, log=print) -> dict:
    """Write the FULL and SUMMARY files (not yet password-locked) into out_dir. -> {'full','summary','rows','total'}"""
    final, total, base = compute(client_xlsx, deposit_xlsx, cm_csv, fo_csv, log)
    os.makedirs(out_dir, exist_ok=True)
    full, summ = os.path.join(out_dir, OUT_FULL), os.path.join(out_dir, OUT_SUMMARY)
    final.to_excel(full, index=False)
    summary_frame(base, total, final.columns).to_excel(summ, index=False)
    _format_excel(full)
    _format_excel(summ)
    return {"full": full, "summary": summ, "rows": int(len(base)), "total": int(total.get("Cash_Maintenance") or 0)}


# ───────────────────────────── password lock ─────────────────────────────
def protect(path, password, log=print) -> str:
    """Open-password on an .xlsx. Uses msoffcrypto (pure python) when installed, else Excel itself (COM). Returns the
    method used; raises RuntimeError when neither works (the mail is then NOT sent unprotected)."""
    if not password:
        raise RuntimeError("no file password set (MCX_CASH_FILE_PASSWORD) - the report must not go out unprotected")
    try:
        import msoffcrypto                                    # type: ignore
        tmp = path + ".enc"
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            msoffcrypto.OfficeFile(src).encrypt(password, dst)
        os.replace(tmp, path)
        log(f"Password applied: {os.path.basename(path)} (msoffcrypto)")
        return "msoffcrypto"
    except ImportError:
        pass
    except Exception as e:                                    # noqa: BLE001
        log(f"msoffcrypto failed ({type(e).__name__}: {e}) - trying Excel")
    try:
        import pythoncom                                      # type: ignore
        import win32com.client as win32                       # type: ignore
    except ImportError:
        raise RuntimeError("cannot password-protect: install msoffcrypto-tool (pip install msoffcrypto-tool) or run on a PC with Excel")
    pythoncom.CoInitialize()
    excel = wb = None
    try:
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        ap = os.path.abspath(path)
        wb = excel.Workbooks.Open(ap)
        wb.SaveAs(ap, FileFormat=51, Password=password)
        log(f"Password applied: {os.path.basename(path)} (Excel)")
        return "excel"
    except Exception as e:                                    # noqa: BLE001
        raise RuntimeError(f"Excel could not password-protect {os.path.basename(path)}: {e}")
    finally:
        try:
            if wb:
                wb.Close(False)
        except Exception:
            pass
        try:
            if excel:
                excel.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


# ───────────────────────────── mail ─────────────────────────────
def split_addrs(s):
    return [a.strip() for a in re.split(r"[;,\s]+", s or "") if a.strip() and "@" in a]


def recipients(kind):
    """kind 'full' | 'summary' -> (to, cc) lists from the saved settings (.env)."""
    k = "MCXCASH_FULL" if kind == "full" else "MCXCASH_SUMMARY"
    return split_addrs(os.getenv(k + "_TO", DEFAULT_MAIL[k + "_TO"])), split_addrs(os.getenv(k + "_CC", DEFAULT_MAIL[k + "_CC"]))


def send_mail(to_list, cc_list, subject, body_html, attachment, log=print, attempts=5, gap=10, sleep=time.sleep):
    """Send one mail with the file attached; retries a few times. Raises RuntimeError if it never goes."""
    host, port = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in"), int(os.getenv("SMTP_PORT", "587"))
    user, pwd = os.getenv("SENDER_EMAIL"), os.getenv("SENDER_PASSWORD")
    if not to_list:
        raise RuntimeError("no 'To' address set - use the Email To / CC button")
    if not (user and pwd):
        raise RuntimeError("SENDER_EMAIL / SENDER_PASSWORD are not set in .env")
    last = None
    for n in range(1, attempts + 1):
        try:
            msg = MIMEMultipart()
            msg["From"], msg["To"], msg["Cc"], msg["Subject"] = user, ", ".join(to_list), ", ".join(cc_list), subject
            msg.attach(MIMEText(body_html, "html"))
            with open(attachment, "rb") as fh:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(fh.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(attachment)}"')
            msg.attach(part)
            srv = smtplib.SMTP(host, port, timeout=30)
            srv.starttls()
            srv.login(user, pwd)
            srv.send_message(msg, to_addrs=list(to_list) + list(cc_list))
            srv.quit()
            log(f"Email sent successfully - {os.path.basename(attachment)} -> {', '.join(to_list)}"
                + (f" (cc {', '.join(cc_list)})" if cc_list else ""))
            return
        except Exception as e:                                # noqa: BLE001
            last = e
            log(f"Email failed (try {n}/{attempts}): {type(e).__name__}: {e}")
            if n < attempts:
                sleep(gap)
    raise RuntimeError(f"email did not go: {type(last).__name__}: {last}")


def mail_body(run_label):
    return (f"<html><body><p>Dear Sir,</p><p>Please find attached the <b>MCX Cash Maintenance Shortfall Report</b> "
            f"generated at <b>{run_label}</b>.</p><p>The file is password protected.</p>"
            f"<p>Best regards,<br>Risk Management System</p></body></html>")


def run_label(now=None):
    return (now or datetime.now()).strftime("%d-%m-%Y %I:%M %p")
