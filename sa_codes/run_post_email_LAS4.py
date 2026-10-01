"""
run_post_email_LAS4.py
======================
Standalone script — runs ONLY the POST-EMAIL section of LAS4.py.
Use this when LAS4.py completes all emails but crashes in the
Scenario / Top-100 / backup steps.

Run from command line:
    python run_post_email_LAS4.py

No arguments needed. Reads the same .env file as LAS4.py.

FIX included: UP_8% and DOWN_8% are treated as optional columns.
If scenario.csv only has UP_6%/DOWN_6%, the script works fine.
"""

import os as _sa_os
try:
    from dotenv import load_dotenv as _sa_ld
    _sa_ld(_sa_os.getenv("SA_ENV_FILE", r"D:\CLAUDE\rms-copilot\.env"))
except Exception:
    pass

def _SAENV(_k, _d):
    _v = _sa_os.getenv(_k)
    return _v if (_v is not None and _v != "") else _d

def _SALIST(_k, _d):
    _v = _sa_os.getenv(_k)
    return [x.strip() for x in _v.split(",") if x.strip()] if _v else _d

# ── stdlib ──────────────────────────────────────────────────────────
import os
import sys
import glob
import shutil
import time
import gc
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

import pandas as pd
import openpyxl
from openpyxl.styles import Alignment, Border, Side
from openpyxl.utils import get_column_letter
import win32com.client as win32
import psutil

# ── config ──────────────────────────────────────────────────────────
BASE_FOLDER = _SAENV("SA_LAS_FUNDING_DIR", r"C:\KARTAVYA\LAS FUNDING")
COMMON_DIR  = _SAENV("SA_COMMON_DIR",      r"C:\COMMON FILES")

SMTP_SERVER    = "smtp3.netcore.co.in"
SMTP_PORT      = 587
EMAIL_ADDRESS  = _SAENV("SA_SMTP_EMAIL",    "Sandeep.kumar2@mastertrust.co.in")
EMAIL_PASSWORD = _SAENV("SA_SMTP_PASSWORD", "$Sandeep24*")

EMAIL_DATE     = datetime.now().strftime("%d%m%Y")
today          = datetime.now().strftime("%d%m%Y")
today_dd_mm_yyyy = datetime.today().strftime("%d-%m-%Y")

# ── helpers ─────────────────────────────────────────────────────────

def password_protect_excel_force(input_file):
    _today = datetime.today().strftime("%d%m%Y")
    password = f"Scenario@{_today}"
    input_file = os.path.abspath(input_file)
    base, ext = os.path.splitext(input_file)
    dated_file = f"{base}_{_today}{ext}"
    excel = win32.DispatchEx("Excel.Application")
    excel.DisplayAlerts = False
    excel.Visible = False
    wb = excel.Workbooks.Open(input_file, False, False)
    wb.SaveAs(dated_file, FileFormat=51, Password=password)
    wb.Close(False)
    excel.Quit()
    print(f"🔐 PASSWORD APPLIED → {os.path.basename(dated_file)}")
    print(f"🔑 Password: {password}")
    return dated_file


def send_email_infinite_retry(subject, body, to_recipients, cc_recipients,
                               attachments, is_html=False):
    while True:
        try:
            msg = MIMEMultipart()
            msg["From"]    = EMAIL_ADDRESS
            msg["To"]      = ", ".join(to_recipients)
            msg["Cc"]      = ", ".join(cc_recipients)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "html" if is_html else "plain"))
            files_attached = 0
            for fp in attachments:
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header("Content-Disposition",
                                        f'attachment; filename="{os.path.basename(fp)}"')
                        msg.attach(part)
                        files_attached += 1
                        print(f"📎 Attached: {fp}")
                else:
                    print(f"⚠️ Missing attachment: {fp}")
            # Only retry-loop when attachments were actually expected and came
            # up empty (a real missing file) -- an intentional attachments=[]
            # call must not trip this, or it retries forever. Matches the same
            # fix applied in LAS4.py's copy of this function.
            if attachments and files_attached == 0:
                print("❌ No attachments — retrying in 3 seconds...")
                time.sleep(3)
                continue
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.sendmail(EMAIL_ADDRESS,
                                to_recipients + cc_recipients,
                                msg.as_string())
            print(f"✅ Email sent successfully: {subject}")
            break
        except Exception as e:
            print(f"❌ Email error ({subject}): {e}")
            print("🔁 Retrying in 3 seconds...")
            time.sleep(3)


def safe_delete(file_path, retries=10, delay=2):
    for i in range(retries):
        try:
            if os.path.exists(file_path):
                os.chmod(file_path, 0o777)
                os.remove(file_path)
                print(f"🗑️ Deleted: {file_path}")
                return True
        except PermissionError:
            print(f"⚠️ File locked, retry {i+1}/{retries}: {file_path}")
            time.sleep(delay)
        except Exception as e:
            print(f"⚠️ Delete error: {e}")
            time.sleep(delay)
    print(f"❌ Could not delete after retries: {file_path}")
    return False


def to_numeric_series(s):
    return pd.to_numeric(
        s.astype(str)
         .str.replace("%",  "", regex=False)
         .str.replace(",",  "", regex=False)
         .str.replace("−",  "-", regex=False)
         .str.replace("–",  "-", regex=False)
         .str.replace("—",  "-", regex=False)
         .str.strip(),
        errors="coerce"
    )


def clean_to_int(series):
    return (
        pd.to_numeric(
            series.astype(str).str.replace(",", "").str.strip(),
            errors="coerce"
        ).fillna(0).round(0).astype(int)
    )

# ════════════════════════════════════════════════════════════════════
# TENTATIVE OBLIGATION SUMMARY EMAIL — resume point.
# NARRATION.py-style dashboard email, fully self-contained (NOT loaded from
# an external D:\CLAUDE\NARRATION.py path — that broke the very first run on
# a machine where that file doesn't exist; see LAS4.py's copy of this
# function for the full reasoning). Re-reads BASE_FOLDER's own main.csv
# directly (this is a standalone resume script, so df_main_raw from LAS4.py's
# run isn't available here) rather than depending on main_filtered or the
# old Tentative Obligation File existing on disk.
# ════════════════════════════════════════════════════════════════════
print("\n📊 Preparing Tentative Obligation Summary (NARRATION-style, self-contained, resume point)")

def _las_narration_tentative_email(df_main_raw):
    """Builds + sends the NARRATION.py-style Pay-In/Pay-Out dashboard email
    for the Tentative Obligation step — no external file dependency, no
    import of any other script. Password-protects the xlsx attachment with
    the same Scenario@<DDMMYYYY> scheme every other LAS4 file uses, and
    retries sending forever on SMTP failure, same as send_email_infinite_retry
    elsewhere in this file."""
    import time as _nt_time
    import smtplib as _nt_smtplib
    from email.mime.multipart import MIMEMultipart as _NtMulti
    from email.mime.text import MIMEText as _NtText
    from email.mime.image import MIMEImage as _NtImage
    from email.mime.base import MIMEBase as _NtBase
    from email import encoders as _nt_encoders
    import win32com.client as _nt_win32
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _nt_plt
    from matplotlib.patches import FancyBboxPatch as _NtBox
    from openpyxl import Workbook as _NtWorkbook
    from openpyxl.styles import Font as _NtFont, PatternFill as _NtFill, Alignment as _NtAlign, Border as _NtBorder, Side as _NtSide
    from openpyxl.utils import get_column_letter as _nt_col_letter

    _TITLE = "Pay-In / Pay-Out Summary"
    _SUBTITLE = "Cash  |  Futures  |  MCX"
    _SUBJECT_PREFIX = "Tentative Obligation File"
    _FILE_PREFIX = "PayIn_PayOut_Summary"
    _DATA_SHEETS = [
        ("FuturesMTM", "FuturesMTM"),
        ("MCXFuturesMTM", "MCXFuturesMTM"),
        ("DayNetPremium", "DayNetPremium"),
        ("MCXDayNetPremium", "MCXDayNetPremium"),
        ("EquityAmount", "EquityAmount"),
    ]

    def _nt_indian(n):
        n = int(round(n))
        sign_ = "-" if n < 0 else ""
        s = str(abs(n))
        if len(s) > 3:
            head, tail = s[:-3], s[-3:]
            parts = []
            while len(head) > 2:
                parts.insert(0, head[-2:]); head = head[:-2]
            if head: parts.insert(0, head)
            s = ",".join(parts) + "," + tail
        return sign_ + s

    def _nt_sign(x):
        x = float(x)
        return 1 if x > 0 else (-1 if x < 0 else 0)

    def _nt_build_sheet_df(df, col):
        d = df[["ClientID", "Product", col]].copy()
        d["Product"] = d["Product"].astype(str).str.strip()
        d[col] = pd.to_numeric(d[col].astype(str).str.replace(",", "").str.strip(), errors="coerce").fillna(0)
        d = d[(d["Product"] == "-") & (d[col] != 0)]
        d = d[["ClientID", col]].rename(columns={"ClientID": "CLIENT CODE"})
        return d.reset_index(drop=True)

    def _nt_compute_narration(sheets):
        fut = sheets["FuturesMTM"]["FuturesMTM"].sum()
        mcx = sheets["MCXFuturesMTM"]["MCXFuturesMTM"].sum()
        dnp = sheets["DayNetPremium"]["DayNetPremium"].sum()
        mdnp = sheets["MCXDayNetPremium"]["MCXDayNetPremium"].sum()
        eq = sheets["EquityAmount"]["EquityAmount"]
        buying = eq[eq > 0].sum()
        selling = eq[eq < 0].sum()
        fo_premium = -_nt_sign(dnp) * (abs(dnp) - abs(mdnp))
        m_sc = -mdnp
        def _lbl(base, total, positive_is_in):
            if abs(total) < 0.005: return f"{base} PAYIN PAYOUT"
            is_in = (total > 0) == positive_is_in
            return f"{base} {'PAYIN' if is_in else 'PAYOUT'}"
        rows = [
            ["CASH", buying, selling, 0.0, 0.0, True],
            ["FUTURE", 0.0, 0.0, fut, fo_premium, False],
            ["MCX", 0.0, 0.0, mcx, m_sc, False],
        ]
        out = []
        for base, b, s_, f, p, pos_in in rows:
            total = b + s_ + f + p
            out.append([_lbl(base, total, pos_in), total, b, s_, f, p])
        return out

    def _nt_write_excel(path, narr_rows, sheets):
        wb = _NtWorkbook()
        hdr_fill = _NtFill("solid", fgColor="1F3864")
        hdr_font = _NtFont(bold=True, color="FFFFFF")
        tot_fill = _NtFill("solid", fgColor="D9E1F2")
        thin = _NtSide(style="thin", color="999999")
        border = _NtBorder(left=thin, right=thin, top=thin, bottom=thin)
        num_fmt = '#,##0.00;[Red]-#,##0.00'
        ws = wb.active
        ws.title = "NARRATION"
        heads = ["Narration", "Total", "Buying", "Selling", "Future/MCX MTM", "Premium"]
        ws.append(heads)
        for r in narr_rows: ws.append(r)
        for c in ws[1]:
            c.fill, c.font, c.border = hdr_fill, hdr_font, border
            c.alignment = _NtAlign(horizontal="center")
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            for i, c in enumerate(row):
                c.border = border
                if i: c.number_format = num_fmt
        for i, w in enumerate([26, 20, 20, 20, 20, 20], 1):
            ws.column_dimensions[_nt_col_letter(i)].width = w
        for name, col in _DATA_SHEETS:
            d = sheets[name]
            w = wb.create_sheet(name)
            w.append(["CLIENT CODE", col])
            for rec in d.itertuples(index=False): w.append(list(rec))
            last = w.max_row
            w.append(["TOTAL", f"=SUM(B2:B{last})" if last >= 2 else 0])
            for c in w[1]:
                c.fill, c.font, c.border = hdr_fill, hdr_font, border
                c.alignment = _NtAlign(horizontal="center")
            for row in w.iter_rows(min_row=2, max_row=w.max_row):
                row[0].border = row[1].border = border
                row[1].number_format = num_fmt
            for c in w[w.max_row]:
                c.font, c.fill = _NtFont(bold=True), tot_fill
            w.column_dimensions["A"].width = 18
            w.column_dimensions["B"].width = 24
            w.freeze_panes = "A2"
        wb.save(path)

    def _nt_write_dashboard(path, narr_rows, stamp):
        heads = ["Narration", "Total", "Buying", "Selling", "Future/MCX MTM", "Premium"]
        col_w = [3.3, 2.3, 1.9, 1.9, 2.1, 2.2]
        W, H = 11.5, 6.3
        fig = _nt_plt.figure(figsize=(W, H), dpi=150)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
        fig.patch.set_facecolor("#F4F6FA")
        NAVY, LINE, INK, MUTE = "#14284B", "#E3E8F0", "#1B2230", "#8A94A6"
        RED, GREEN = "#C62828", "#1B7F3B"
        ACCENT = ["#3B6FD4", "#1C9C8C", "#E09A1A"]
        ax.add_patch(_nt_plt.Rectangle((0, H - 1.3), W, 1.3, fc=NAVY, ec="none"))
        ax.text(0.5, H - 0.55, _TITLE, fontsize=25, fontweight="bold", color="white", va="center")
        ax.text(0.5, H - 0.98, _SUBTITLE, fontsize=13, color="#AFC0E0", va="center")
        ax.text(W - 0.5, H - 0.55, stamp, fontsize=13, color="white", va="center", ha="right")
        ax.text(W - 0.5, H - 0.98, "All figures in \u20b9", fontsize=13, color="#AFC0E0", va="center", ha="right")
        x0, x1 = 0.4, W - 0.4
        scale = (x1 - x0) / sum(col_w)
        xs = [x0]
        for w in col_w: xs.append(xs[-1] + w * scale)
        top = H - 1.3 - 0.4
        hh, rh = 0.65, 0.95
        card_h = hh + rh * len(narr_rows)
        ax.add_patch(_NtBox((x0, top - card_h), x1 - x0, card_h, boxstyle="round,pad=0,rounding_size=0.12", fc="white", ec=LINE, lw=1.2))
        ax.add_patch(_nt_plt.Rectangle((x0 + 0.01, top - hh), x1 - x0 - 0.02, hh - 0.01, fc="#EAEFF8", ec="none"))
        for i, h in enumerate(heads):
            if i == 0:
                ax.text(xs[0] + 0.4, top - hh / 2, h.upper(), color=NAVY, fontsize=12, fontweight="bold", va="center", ha="left")
            else:
                ax.text((xs[i] + xs[i + 1]) / 2, top - hh / 2, h.upper(), color=NAVY, fontsize=12, fontweight="bold", va="center", ha="center")
        for r, row in enumerate(narr_rows):
            y1 = top - hh - r * rh
            y0 = y1 - rh
            yc = (y0 + y1) / 2
            if r < len(narr_rows) - 1:
                ax.plot([x0 + 0.3, x1 - 0.3], [y0, y0], color=LINE, lw=1)
            ax.add_patch(_nt_plt.Rectangle((x0 + 0.01, y0 + 0.2), 0.09, rh - 0.4, fc=ACCENT[r % 3], ec="none"))
            ax.add_patch(_nt_plt.Rectangle((xs[1] + 0.1, y0 + 0.12), xs[2] - xs[1] - 0.2, rh - 0.24, fc="#F5F7FB", ec="none"))
            ax.text(xs[0] + 0.4, yc, row[0], fontsize=14.5, fontweight="bold", color=INK, va="center", ha="left")
            for i in range(1, 6):
                v = row[i]
                colr = RED if v < -0.5 else (GREEN if v > 0.5 else MUTE)
                ax.text((xs[i] + xs[i + 1]) / 2, yc, _nt_indian(v), fontsize=14.5 if i == 1 else 14, fontweight="bold" if i == 1 else "normal", color=colr, va="center", ha="center")
        ax.add_patch(_nt_plt.Rectangle((0, 0), W, 0.65, fc="#E9EDF4", ec="none"))
        ax.text(0.5, 0.325, "Cash  |  Futures  |  MCX  |  Daily Pay-In / Pay-Out", fontsize=12, color="#5B6577", va="center")
        ax.text(W - 0.5, 0.325, "Master Capital Services Ltd", fontsize=12, color="#5B6577", va="center", ha="right", fontweight="bold")
        fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
        _nt_plt.close(fig)

    def _nt_password_protect(input_file):
        _today = datetime.now().strftime("%d%m%Y")
        password = f"Scenario@{_today}"
        input_file = os.path.abspath(input_file)
        base, ext = os.path.splitext(input_file)
        dated_file = f"{base}_{_today}{ext}"
        excel = _nt_win32.DispatchEx("Excel.Application")
        excel.DisplayAlerts = False
        excel.Visible = False
        wb = excel.Workbooks.Open(input_file, False, False)
        wb.SaveAs(dated_file, FileFormat=51, Password=password)
        wb.Close(False)
        excel.Quit()
        print(f"🔐 PASSWORD APPLIED → {os.path.basename(dated_file)}")
        print(f"🔑 Password: {password}")
        return dated_file

    def _nt_build_email_html(stamp):
        return f"""\
<html><body style="margin:0;padding:0;background:#EEF1F6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#EEF1F6;padding:24px 0;">
<tr><td align="center">
  <table role="presentation" width="920" cellpadding="0" cellspacing="0"
         style="max-width:920px;width:100%;background:#FFFFFF;border-radius:10px;overflow:hidden;
                font-family:'Segoe UI',Calibri,Arial,sans-serif;color:#1B2230;">
    <tr><td style="padding:26px 28px 6px 28px;font-size:15px;line-height:1.6;">
        Dear Sir,<br>
        Please find the Tentative Obligation File Summary below:-
    </td></tr>
    <tr><td style="padding:14px 28px 6px 28px;">
        <img src="cid:dashboard" alt="{_TITLE}" width="860"
             style="display:block;width:860px;max-width:100%;height:auto;border:1px solid #E3E8F0;border-radius:6px;">
    </td></tr>
    <tr><td style="padding:0 28px 14px 28px;font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td style="padding:0 28px 16px 28px;font-size:13px;line-height:1.5;color:#3B4A63;">
        <b>NOTE:</b> Custodian Accounts have been removed from this report.<br>
        The Day Net Premium includes FO Day Net Premium and MCX Day Net Premium.
    </td></tr>
    <tr><td style="padding:0 28px;"><div style="border-top:1px solid #E3E8F0;"></div></td></tr>
    <tr><td style="padding:18px 28px 26px 28px;font-size:14px;line-height:1.65;">
        Best regards,<br>
        Sandeep Kumar<br>
        011-42111079
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""

    sheets = {name: _nt_build_sheet_df(df_main_raw, col) for name, col in _DATA_SHEETS}
    narr_rows = _nt_compute_narration(sheets)
    stamp = datetime.now().strftime("%d-%b-%Y %H:%M")
    xlsx_path = os.path.join(BASE_FOLDER, f"{_FILE_PREFIX}_{EMAIL_DATE}.xlsx")
    png_path = os.path.join(BASE_FOLDER, f"{_FILE_PREFIX}_{EMAIL_DATE}.png")
    _nt_write_excel(xlsx_path, narr_rows, sheets)
    _nt_write_dashboard(png_path, narr_rows, stamp)
    protected_xlsx = _nt_password_protect(xlsx_path)

    to_list = _SALIST("SA_MAIL_TENTATIVE_TO", ["kartavya.dhillon@mastertrust.co.in"])
    cc_list = _SALIST("SA_MAIL_TENTATIVE_CC", ["kartavya.dhillon@mastertrust.co.in"])

    while True:
        try:
            msg = _NtMulti("mixed")
            msg["From"] = EMAIL_ADDRESS
            msg["To"] = ", ".join(to_list)
            if cc_list: msg["Cc"] = ", ".join(cc_list)
            msg["Subject"] = f"{_SUBJECT_PREFIX}-{EMAIL_DATE}"
            alt = _NtMulti("alternative")
            msg.attach(alt)
            alt.attach(_NtText("Dear Sir,\nPlease find the Tentative Obligation File Summary below:-\n\n"
                                "(View this email in HTML to see the summary.)\n\nBest regards,\nSandeep Kumar\n011-42111079", "plain", "utf-8"))
            related = _NtMulti("related")
            alt.attach(related)
            related.attach(_NtText(_nt_build_email_html(stamp), "html", "utf-8"))
            with open(png_path, "rb") as f:
                img = _NtImage(f.read(), _subtype="png")
            img.add_header("Content-ID", "<dashboard>")
            img.add_header("Content-Disposition", "inline", filename=os.path.basename(png_path))
            related.attach(img)
            with open(protected_xlsx, "rb") as f:
                part = _NtBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                part.set_payload(f.read())
            _nt_encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=os.path.basename(protected_xlsx))
            msg.attach(part)
            with _nt_smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.sendmail(EMAIL_ADDRESS, to_list + cc_list, msg.as_string())
            print(f"✅ Email sent successfully: {_SUBJECT_PREFIX}-{EMAIL_DATE}")
            break
        except Exception as e:
            print(f"❌ Email error ({_SUBJECT_PREFIX}-{EMAIL_DATE}): {e}")
            print("🔁 Retrying in 3 seconds...")
            _nt_time.sleep(3)

_main_csv_path = os.path.join(BASE_FOLDER, "main.csv")
if not os.path.exists(_main_csv_path):
    raise FileNotFoundError(f"❌ main.csv not found: {_main_csv_path}")
df_main_raw = pd.read_csv(_main_csv_path, dtype=str)

_las_narration_tentative_email(df_main_raw)
print("✅ NARRATION-style Tentative Obligation email sent (password-protected attachment)")

# ════════════════════════════════════════════════════════════════════
print("\n📊 Starting POST-EMAIL Scenario & LAS reference steps")
# ════════════════════════════════════════════════════════════════════

# ==================================================
# STEP A: SCENARIO_Filtered.xlsx
# ==================================================
print("📄 Creating SCENARIO_Filtered.xlsx")

scenario_file          = os.path.join(BASE_FOLDER, "scenario.csv")
scenario_filtered_file = os.path.join(BASE_FOLDER, "SCENARIO_Filtered.xlsx")

if not os.path.exists(scenario_file):
    raise FileNotFoundError(f"❌ scenario.csv not found: {scenario_file}")

df_scn = pd.read_csv(scenario_file, dtype=str)

EXCLUDE_CLIENTS = {"OWN", "*OWN*", "01292", "PRO_06436", "06436"}
df_scn["Client ID"] = df_scn["Client ID"].astype(str).str.strip().str.upper()
clients_before = set(df_scn["Client ID"].unique())
df_scn = df_scn[~df_scn["Client ID"].isin(EXCLUDE_CLIENTS)]
df_scn = df_scn[~df_scn["Client ID"].str.contains("OWN", case=False, na=False)]
clients_after   = set(df_scn["Client ID"].unique())
removed_clients = sorted(clients_before - clients_after)

if removed_clients:
    print("🧹 Scenario cleanup applied — removed internal client codes:")
    for c in removed_clients:
        print(f"   ❌ {c}")
    print(f"🔢 Total removed: {len(removed_clients)}")
else:
    print("🧹 Scenario cleanup applied — no internal client codes found")

# ── FIX: UP_8%/DOWN_8% are optional ────────────────────────────────
base_required = [
    "Product", "Zone", "Client ID", "Name",
    "UP_2%", "DOWN_2%", "UP_4%", "DOWN_4%",
    "UP_6%", "DOWN_6%",
]
optional_8pct = ["UP_8%", "DOWN_8%"]
required_cols = base_required + [c for c in optional_8pct if c in df_scn.columns]

missing_cols = [c for c in required_cols if c not in df_scn.columns]
if missing_cols:
    raise ValueError(f"❌ Missing columns in SCENARIO.csv: {missing_cols}")

if "UP_8%" not in df_scn.columns:
    print("ℹ️  UP_8% / DOWN_8% not present in scenario.csv — continuing without them")

df_scn_filtered = df_scn[required_cols].copy()
df_scn_filtered.to_excel(scenario_filtered_file, index=False)
print(f"✅ Created: {scenario_filtered_file}")

# ==================================================
# STEP B: SCENARIO_Filtered_ProductDash.xlsx
# ==================================================
print("🔍 Creating SCENARIO_Filtered_ProductDash.xlsx")

scenario_product_dash_file = os.path.join(BASE_FOLDER, "SCENARIO_Filtered_ProductDash.xlsx")
df_dash = df_scn_filtered[
    df_scn_filtered["Product"].astype(str).str.strip() == "-"
].copy()
df_dash.to_excel(scenario_product_dash_file, index=False)
print(f"✅ Created: {scenario_product_dash_file}")

# ==================================================
# STEP C: SCENARIO_Top100.xlsx (UP / DOWN sheets)
# ==================================================
print("📈 Creating SCENARIO_Top100.xlsx")

scenario_top100_file = os.path.join(BASE_FOLDER, "SCENARIO_Top100.xlsx")

# Build up/down column lists based on what actually exists
up_cols   = ["Product", "Zone", "Client ID", "Name",
             "UP_2%",   "UP_4%",   "UP_6%"]
down_cols = ["Product", "Zone", "Client ID", "Name",
             "DOWN_2%", "DOWN_4%", "DOWN_6%"]
if "UP_8%"   in df_scn_filtered.columns:
    up_cols.append("UP_8%")
if "DOWN_8%" in df_scn_filtered.columns:
    down_cols.append("DOWN_8%")

xls    = pd.ExcelFile(scenario_product_dash_file)
sheets = {sheet: xls.parse(sheet) for sheet in xls.sheet_names}

with pd.ExcelWriter(scenario_top100_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        df[[c for c in up_cols   if c in df.columns]].copy().to_excel(
            writer, sheet_name="Top_Market_UP_100",   index=False)
        df[[c for c in down_cols if c in df.columns]].copy().to_excel(
            writer, sheet_name="Top_Market_DOWN_100", index=False)

print(f"✅ Created: {scenario_top100_file}")

# ==================================================
# STEP D: LAS Debit reference file with LAS FUNDING
# ==================================================
print("📄 Creating LAS Debit reference file with LAS FUNDING")

las_source_file = os.path.join(BASE_FOLDER, f"LAS Debit Balances-{today_dd_mm_yyyy}.xlsx")

if os.path.exists(las_source_file):
    las_output_file = las_source_file.replace(".xlsx", "_With_LAS_FUNDING.xlsx")
    df_las = pd.read_excel(las_source_file, engine="openpyxl")
    if "Net Debit" not in df_las.columns:
        raise ValueError("❌ 'Net Debit' column not found in LAS Debit file")
    df_las["Net Debit"]    = pd.to_numeric(df_las["Net Debit"], errors="coerce")
    df_las["LAS FUNDING"]  = -df_las["Net Debit"].abs()
    df_las.to_excel(las_output_file, index=False)
    print(f"✅ Created LAS reference file: {las_output_file}")
else:
    print(f"⚠️ LAS Debit file not found, skipped: {las_source_file}")

print("\n🎉 POST-EMAIL Scenario & LAS reference steps COMPLETED")

# ==================================================
# STEP E: MERGE LAS FUNDING INTO SCENARIO_Top100
# ==================================================
print("💰 Merging LAS FUNDING into SCENARIO_Top100")

funding_file = os.path.join(BASE_FOLDER, f"LAS Debit Balances-{today_dd_mm_yyyy}_With_LAS_FUNDING.xlsx")
output_file  = os.path.join(BASE_FOLDER, "SCENARIO_Top100_WithFunding.xlsx")

if not os.path.exists(scenario_top100_file):
    raise FileNotFoundError(f"❌ SCENARIO_Top100.xlsx not found: {scenario_top100_file}")
if not os.path.exists(funding_file):
    raise FileNotFoundError(f"❌ LAS FUNDING reference file not found: {funding_file}")

df_funding = pd.read_excel(funding_file, dtype=str)
df_funding = df_funding[["Trading Code", "LAS FUNDING"]].copy()
df_funding.rename(columns={"Trading Code": "Client ID"}, inplace=True)
df_funding["LAS FUNDING"] = pd.to_numeric(df_funding["LAS FUNDING"], errors="coerce")

xls    = pd.ExcelFile(scenario_top100_file)
sheets = {sheet: xls.parse(sheet, dtype=str) for sheet in xls.sheet_names}

updated_sheets = {}
for sheet_name, df in sheets.items():
    if "Client ID" in df.columns:
        df = df.merge(df_funding, how="left", on="Client ID")
    updated_sheets[sheet_name] = df

with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in updated_sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)

print(f"✅ SCENARIO_Top100_WithFunding created: {output_file}")

# ==================================================
# STEP F: MERGE SA0X DEPOSIT INTO SCENARIO_Top100
# ==================================================
print("💼 Merging SA0X Total value of Collateral+fund into SCENARIO_Top100_WithFunding")

scenario_file = os.path.join(BASE_FOLDER, "SCENARIO_Top100_WithFunding.xlsx")
output_file   = os.path.join(BASE_FOLDER, "SCENARIO_Top100_WithFunding_Deposit.xlsx")

all_files = glob.glob(os.path.join(BASE_FOLDER, "SA0*_OUTPUT FILE.xlsx"))
deposit_list = []
for file in all_files:
    try:
        df_sa = pd.read_excel(file, dtype=str)
        if "Client/UCC Code" in df_sa.columns and "Total value of Collateral+fund" in df_sa.columns:
            deposit_list.append(df_sa[["Client/UCC Code", "Total value of Collateral+fund"]])
        else:
            print(f"⚠️ Required columns missing in {file}")
    except Exception as e:
        print(f"⚠️ Could not read {file}: {e}")

if not deposit_list:
    raise ValueError("❌ No valid SA0X files found with required columns.")

deposit_df = pd.concat(deposit_list, ignore_index=True).drop_duplicates()

xls    = pd.ExcelFile(scenario_file)
sheets = {sheet: xls.parse(sheet, dtype=str) for sheet in xls.sheet_names}

for sheet_name, df in sheets.items():
    if "Client ID" in df.columns:
        merged_df = df.merge(deposit_df, left_on="Client ID",
                             right_on="Client/UCC Code", how="left")
        merged_df.drop(columns=["Client/UCC Code"], inplace=True, errors="ignore")
        sheets[sheet_name] = merged_df
    else:
        print(f"⚠️ Skipped sheet '{sheet_name}' (no Client ID column).")

with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)

print(f"✅ SCENARIO_Top100_WithFunding_Deposit created: {output_file}")

# ==================================================
# STEP G: MERGE RISK NOV INTO SCENARIO_Top100
# ==================================================
print("⚠️ Merging RISK NOV into SCENARIO_Top100_WithFunding_Deposit")

_SA_OUTPUT_DIR2   = _SAENV("SA_OUTPUT_DIR", r"C:\SHORT ALLOCATION")
_risk_candidates2 = [
    os.path.join(COMMON_DIR,      f"RISK_REPORT_{today}.csv"),
    os.path.join(_SA_OUTPUT_DIR2, f"RISK_REPORT_{today}.csv"),
    os.path.join(BASE_FOLDER,     f"RISK_REPORT_{today}.csv"),
]
risk_file = next((p for p in _risk_candidates2 if os.path.isfile(p)), None)

if not risk_file:
    raise FileNotFoundError(
        f"❌ No RISK_REPORT file found for date {today}\n"
        f"   Searched: {_risk_candidates2}"
    )
print(f"📂 Reading risk report: {risk_file}")

df_risk = pd.read_csv(risk_file, dtype=str)
df_risk.columns = df_risk.columns.str.strip()
df_risk["FNONOV"] = pd.to_numeric(df_risk.get("FNONOV", 0), errors="coerce").fillna(0)
df_risk["COMNOV"] = pd.to_numeric(df_risk.get("COMNOV", 0), errors="coerce").fillna(0)
df_risk["NOV"]    = df_risk["FNONOV"] + df_risk["COMNOV"]
df_risk_merge     = df_risk[["ACCOUNTID", "NOV"]].copy()

scenario_file = os.path.join(BASE_FOLDER, "SCENARIO_Top100_WithFunding_Deposit.xlsx")
if not os.path.exists(scenario_file):
    raise FileNotFoundError(f"❌ Scenario file not found: {scenario_file}")

xls    = pd.ExcelFile(scenario_file)
sheets = {sheet: xls.parse(sheet, dtype=str) for sheet in xls.sheet_names}

updated_sheets = {}
for sheet_name, df in sheets.items():
    if "Client ID" in df.columns:
        df = df.merge(df_risk_merge, how="left", left_on="Client ID", right_on="ACCOUNTID")
        df.drop(columns=["ACCOUNTID"], inplace=True, errors="ignore")
    updated_sheets[sheet_name] = df

with pd.ExcelWriter(scenario_file, engine="openpyxl", mode="w") as writer:
    for sheet_name, df in updated_sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)

print(f"✅ NOV column merged successfully into all sheets: {scenario_file}")

# ==================================================
# STEP H: FINAL DEPOSIT CALCULATION
# ==================================================
print("🧮 Creating final Scenario file with deposit calculations")

input_file  = os.path.join(BASE_FOLDER, "SCENARIO_Top100_WithFunding_Deposit.xlsx")
output_file = os.path.join(BASE_FOLDER, "SCENARIO_Top100_Final.xlsx")

xls    = pd.ExcelFile(input_file)
sheets = {sheet: xls.parse(sheet) for sheet in xls.sheet_names}

updated_sheets = {}
for sheet_name, df in sheets.items():
    if all(col in df.columns for col in ["LAS FUNDING", "Total value of Collateral+fund", "NOV"]):
        df["LAS FUNDING"]                      = pd.to_numeric(df["LAS FUNDING"], errors="coerce").fillna(0)
        df["Total value of Collateral+fund"]   = pd.to_numeric(df["Total value of Collateral+fund"], errors="coerce").fillna(0)
        df["NOV"]                              = pd.to_numeric(df["NOV"], errors="coerce").fillna(0)
        df.loc[df["NOV"] > 0, "NOV"]          = 0
        df["Deposit(Own Client Fund)"]                    = df["Total value of Collateral+fund"] + df["LAS FUNDING"]
        df["Deposit (After adjustment Short NOV)"]        = df["Deposit(Own Client Fund)"] + df["NOV"]
    df = df.fillna(0)
    updated_sheets[sheet_name] = df

with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in updated_sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)

print(f"✅ Final Scenario file created successfully: {output_file}")

# ==================================================
# STEP I: TOP-100 FILTERING & SORTING
# ==================================================
print("📊 Creating Top-100 filtered scenario file")

file_path   = os.path.join(BASE_FOLDER, "SCENARIO_Top100_Final.xlsx")
output_file = os.path.join(BASE_FOLDER, "SCENARIO_Top100_Final_Top100_Filtered.xlsx")

mode = "most_negative_first"

target_cols_up   = ["UP_2%",   "UP_4%",   "UP_6%",   "UP_8%",
                    "LAS FUNDING", "Total value of Collateral+fund", "NOV",
                    "Deposit(Own Client Fund)", "Deposit (After adjustment Short NOV)"]
target_cols_down = ["DOWN_2%", "DOWN_4%", "DOWN_6%", "DOWN_8%",
                    "LAS FUNDING", "Total value of Collateral+fund", "NOV",
                    "Deposit(Own Client Fund)", "Deposit (After adjustment Short NOV)"]

sheets = pd.read_excel(file_path, sheet_name=None)

for sheet_name in list(sheets.keys()):
    if sheet_name.strip().lower() == "sheet1":
        del sheets[sheet_name]
        continue
    df = sheets[sheet_name].copy()

    if sheet_name == "Top_Market_UP_100" and "UP_2%" in df.columns:
        df["UP_2%"] = to_numeric_series(df["UP_2%"])
        df = df[df["UP_2%"] < 0].copy()
        df = df.sort_values(by="UP_2%", ascending=True).head(100).reset_index(drop=True)
        for col in target_cols_up:
            if col in df.columns:
                df[col] = to_numeric_series(df[col]).fillna(0).round(0).astype(int)
        sheets[sheet_name] = df

    elif sheet_name == "Top_Market_DOWN_100" and "DOWN_2%" in df.columns:
        df["DOWN_2%"] = to_numeric_series(df["DOWN_2%"])
        df = df[df["DOWN_2%"] < 0].copy()
        df = df.sort_values(by="DOWN_2%", ascending=True).head(100).reset_index(drop=True)
        for col in target_cols_down:
            if col in df.columns:
                df[col] = to_numeric_series(df[col]).fillna(0).round(0).astype(int)
        sheets[sheet_name] = df

    else:
        for col in (target_cols_up + target_cols_down):
            if col in df.columns:
                df[col] = to_numeric_series(df[col]).fillna(0).round(0).astype(int)
        sheets[sheet_name] = df

with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)

print("✅ Top-100 filtered scenario file created:", output_file)

# ==================================================
# STEP J: REARRANGE COLUMNS
# ==================================================
print("🔀 Rearranging columns for Top Market sheets")

file_path   = os.path.join(BASE_FOLDER, "SCENARIO_Top100_Final.xlsx")
output_file = os.path.join(BASE_FOLDER, "SCENARIO_Top100_Final_Rearranged.xlsx")

up_order   = ["Zone", "Client ID", "Name", "LAS FUNDING",
              "Total value of Collateral+fund",
              "Deposit(Own Client Fund)", "Deposit (After adjustment Short NOV)",
              "UP_2%",   "UP_4%",   "UP_6%",   "UP_8%"]
down_order = ["Zone", "Client ID", "Name", "LAS FUNDING",
              "Total value of Collateral+fund",
              "Deposit(Own Client Fund)", "Deposit (After adjustment Short NOV)",
              "DOWN_2%", "DOWN_4%", "DOWN_6%", "DOWN_8%"]

sheets = pd.read_excel(file_path, sheet_name=None)

for sheet_name, df in sheets.items():
    if sheet_name == "Top_Market_UP_100":
        remaining_cols = [col for col in df.columns if col not in up_order]
        ordered = [df[col] for col in up_order if col in df.columns]
        df = pd.concat(ordered + [sheets[sheet_name][remaining_cols]], axis=1)
        sheets[sheet_name] = df
    elif sheet_name == "Top_Market_DOWN_100":
        remaining_cols = [col for col in df.columns if col not in down_order]
        ordered = [df[col] for col in down_order if col in df.columns]
        df = pd.concat(ordered + [sheets[sheet_name][remaining_cols]], axis=1)
        sheets[sheet_name] = df

with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)

print(f"✅ Rearranged file created successfully: {output_file}")

# ==================================================
# STEP K: SCENARIO ANALYSIS TOP 100 (WITH MARGIN)
# ==================================================
print("📘 Creating final Scenario Analysis Top 100 file")

input_file  = os.path.join(BASE_FOLDER, "SCENARIO_Top100_Final_Rearranged.xlsx")
output_file = os.path.join(BASE_FOLDER, "Scenario Analysis_Top_100_Clients.xlsx")
main_file   = os.path.join(BASE_FOLDER, "MAIN.csv")

xls    = pd.ExcelFile(input_file)
sheets = {sheet: xls.parse(sheet) for sheet in xls.sheet_names}

df_main = pd.read_csv(main_file, dtype=str)
df_main.columns = df_main.columns.str.strip()

if "ClientID" in df_main.columns:
    client_col = "ClientID"
elif "Client ID" in df_main.columns:
    client_col = "Client ID"
else:
    raise ValueError("❌ MAIN.csv missing ClientID / Client ID column")

df_main[client_col] = df_main[client_col].astype(str).str.strip().str.upper()
df_main["Total Margin Utill"] = (
    pd.to_numeric(df_main["Total Margin Utill"], errors="coerce").fillna(0).astype(int)
)

if "Top_Market_UP_100" in sheets:
    df_up = sheets["Top_Market_UP_100"].copy()
    for col in ["UP_2%", "UP_4%", "UP_6%", "UP_8%", "Deposit(Own Client Fund)",
                "Deposit (After adjustment Short NOV)"]:
        if col in df_up.columns:
            df_up[col] = clean_to_int(df_up[col])
    top_up_100 = df_up.sort_values(by="UP_2%", ascending=True).head(100)
    top_up_100 = top_up_100.drop(columns=["Total value of Collateral+fund", "Product", "NOV"], errors="ignore")
    top_up_100 = top_up_100.merge(
        df_main[[client_col, "Total Margin Utill"]].rename(columns={client_col: "Client ID"}),
        on="Client ID", how="left"
    )
    top_up_100.rename(columns={"Total Margin Utill": "MARGIN"}, inplace=True)
    cols = top_up_100.columns.tolist()
    if "MARGIN" in cols and "Deposit (After adjustment Short NOV)" in cols:
        cols.insert(cols.index("Deposit (After adjustment Short NOV)") + 1,
                    cols.pop(cols.index("MARGIN")))
        top_up_100 = top_up_100[cols]
    sheets["Top_Market_UP_100"] = top_up_100

if "Top_Market_DOWN_100" in sheets:
    df_down = sheets["Top_Market_DOWN_100"].copy()
    for col in ["DOWN_2%", "DOWN_4%", "DOWN_6%", "DOWN_8%", "Deposit(Own Client Fund)",
                "Deposit (After adjustment Short NOV)"]:
        if col in df_down.columns:
            df_down[col] = clean_to_int(df_down[col])
    top_down_100 = df_down.sort_values(by="DOWN_2%", ascending=True).head(100)
    top_down_100 = top_down_100.drop(columns=["Total value of Collateral+fund", "Product", "NOV"], errors="ignore")
    top_down_100 = top_down_100.merge(
        df_main[[client_col, "Total Margin Utill"]].rename(columns={client_col: "Client ID"}),
        on="Client ID", how="left"
    )
    top_down_100.rename(columns={"Total Margin Utill": "MARGIN"}, inplace=True)
    cols = top_down_100.columns.tolist()
    if "MARGIN" in cols and "Deposit (After adjustment Short NOV)" in cols:
        cols.insert(cols.index("Deposit (After adjustment Short NOV)") + 1,
                    cols.pop(cols.index("MARGIN")))
        top_down_100 = top_down_100[cols]
    sheets["Top_Market_DOWN_100"] = top_down_100

sheets.pop("Sheet1", None)

with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)

print("✅ Final Scenario Analysis file created:", output_file)

# ==================================================
# STEP L: FORMAT TOP 100 FILE
# ==================================================
print("🎨 Applying final formatting to Scenario Analysis file")

thin_border = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"),  bottom=Side(style="thin")
)

wb = openpyxl.load_workbook(output_file)
for ws in wb.worksheets:
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border
    for col in ws.columns:
        max_length = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                if cell.value is not None:
                    max_length = max(max_length, len(str(cell.value)))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max_length + 2, 55)
wb.save(output_file)
print(f"✅ Formatting applied: {output_file}")

password_protect_excel_force(output_file)

# ==================================================
# STEP N: SCENARIO ANALYSIS TOP 15 CLIENTS
# ==================================================
print("📘 Creating Scenario Analysis Top 15 Clients file")

input_file  = os.path.join(BASE_FOLDER, "SCENARIO_Top100_Final_Rearranged.xlsx")
output_file = os.path.join(BASE_FOLDER, "Scenario Analysis_Top_15_Clients.xlsx")
main_file   = os.path.join(BASE_FOLDER, "MAIN.csv")

xls    = pd.ExcelFile(input_file)
sheets = {sheet: xls.parse(sheet) for sheet in xls.sheet_names}

df_main = pd.read_csv(main_file, dtype=str)
df_main.columns = df_main.columns.str.strip()

if "ClientID" in df_main.columns:
    client_col = "ClientID"
elif "Client ID" in df_main.columns:
    client_col = "Client ID"
else:
    raise ValueError("❌ MAIN.csv missing ClientID / Client ID column")

df_main[client_col] = df_main[client_col].astype(str).str.strip().str.upper()
df_main["Total Margin Utill"] = (
    pd.to_numeric(df_main["Total Margin Utill"], errors="coerce").fillna(0).astype(int)
)

if "Top_Market_UP_100" in sheets:
    df_up = sheets["Top_Market_UP_100"].copy()
    for col in ["UP_2%", "UP_4%", "UP_6%", "UP_8%", "Deposit(Own Client Fund)",
                "Deposit (After adjustment Short NOV)"]:
        if col in df_up.columns:
            df_up[col] = clean_to_int(df_up[col])
    top_up_15 = df_up.sort_values(by="UP_2%", ascending=True).head(15)
    top_up_15 = top_up_15.drop(columns=["Total value of Collateral+fund", "Product", "NOV"], errors="ignore")
    top_up_15 = top_up_15.merge(
        df_main[[client_col, "Total Margin Utill"]].rename(columns={client_col: "Client ID"}),
        on="Client ID", how="left"
    )
    top_up_15.rename(columns={"Total Margin Utill": "MARGIN"}, inplace=True)
    cols = top_up_15.columns.tolist()
    if "MARGIN" in cols and "Deposit (After adjustment Short NOV)" in cols:
        cols.insert(cols.index("Deposit (After adjustment Short NOV)") + 1,
                    cols.pop(cols.index("MARGIN")))
        top_up_15 = top_up_15[cols]
    sheets["Top_Market_UP_15"] = top_up_15
    sheets.pop("Top_Market_UP_100", None)

if "Top_Market_DOWN_100" in sheets:
    df_down = sheets["Top_Market_DOWN_100"].copy()
    for col in ["DOWN_2%", "DOWN_4%", "DOWN_6%", "DOWN_8%", "Deposit(Own Client Fund)",
                "Deposit (After adjustment Short NOV)"]:
        if col in df_down.columns:
            df_down[col] = clean_to_int(df_down[col])
    top_down_15 = df_down.sort_values(by="DOWN_2%", ascending=True).head(15)
    top_down_15 = top_down_15.drop(columns=["Total value of Collateral+fund", "Product", "NOV"], errors="ignore")
    top_down_15 = top_down_15.merge(
        df_main[[client_col, "Total Margin Utill"]].rename(columns={client_col: "Client ID"}),
        on="Client ID", how="left"
    )
    top_down_15.rename(columns={"Total Margin Utill": "MARGIN"}, inplace=True)
    cols = top_down_15.columns.tolist()
    if "MARGIN" in cols and "Deposit (After adjustment Short NOV)" in cols:
        cols.insert(cols.index("Deposit (After adjustment Short NOV)") + 1,
                    cols.pop(cols.index("MARGIN")))
        top_down_15 = top_down_15[cols]
    sheets["Top_Market_DOWN_15"] = top_down_15
    sheets.pop("Top_Market_DOWN_100", None)

sheets.pop("Sheet1", None)

with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)

print("✅ Scenario Analysis Top 15 file created:", output_file)

# ==================================================
# STEP O: FORMAT TOP 15 FILE
# ==================================================
print("🎨 Applying formatting to Scenario Analysis Top 15 file")

wb = openpyxl.load_workbook(output_file)
for ws in wb.worksheets:
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border
    for col in ws.columns:
        max_length = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                if cell.value is not None:
                    max_length = max(max_length, len(str(cell.value)))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max_length + 2, 55)
wb.save(output_file)
print(f"✅ Formatting applied: {output_file}")

password_protect_excel_force(output_file)

# ==================================================
# EMAIL: Scenario Analysis - Top 100 ALL ZONE
# ==================================================
send_email_infinite_retry(
    subject="Scenario Analysis - Top 100 ALL ZONE",
    body="""Dear Sir,\n\nPlease find above subjected file.\n\nBest regards,\nSandeep Kumar\n011-42111079\n""",
    to_recipients=_SALIST("SA_MAIL_TOP100_TO", ["va@mastertrust.co.in"]),
    cc_recipients=_SALIST("SA_MAIL_TOP100_CC", [
        "abhishek.dhiman@mastertrust.co.in",
        "priyanshu.karna@mastertrust.co.in",
        "sumit.aggarwal@mastertrust.co.in",
        "sandeep.kumar2@mastertrust.co.in"
    ]),
    attachments=[
        os.path.join(BASE_FOLDER, f"Scenario Analysis_Top_100_Clients_{EMAIL_DATE}.xlsx")
    ],
    is_html=False
)

# ==================================================
# EMAIL: Scenario Analysis - Top 15 ALL ZONE
# ==================================================
send_email_infinite_retry(
    subject="Scenario Analysis - Top 15 ALL ZONE",
    body="""Dear Sir,\n\nPlease find above subjected file.\n\nBest regards,\nSandeep Kumar\n011-42111079\n""",
    to_recipients=_SALIST("SA_MAIL_TOP15_TO", ["rks@mastertrust.co.in"]),
    cc_recipients=_SALIST("SA_MAIL_TOP15_CC", [
        "abhishek.dhiman@mastertrust.co.in",
        "priyanshu.karna@mastertrust.co.in",
        "sumit.aggarwal@mastertrust.co.in",
        "sandeep.kumar2@mastertrust.co.in"
    ]),
    attachments=[
        os.path.join(BASE_FOLDER, f"Scenario Analysis_Top_15_Clients_{EMAIL_DATE}.xlsx")
    ],
    is_html=False
)

# ==================================================
# KILL EXCEL BEFORE BACKUP
# ==================================================
for proc in psutil.process_iter(['pid', 'name']):
    try:
        if proc.info['name'] and "EXCEL" in proc.info['name'].upper():
            proc.kill()
            print(f"🛑 Killed Excel process: {proc.info['pid']}")
    except Exception:
        pass

time.sleep(2)
gc.collect()
time.sleep(3)

# ==================================================
# BACKUP + CLEANUP
# ==================================================
print("📦 Starting BACKUP + CLEANUP")

BASE        = BASE_FOLDER
BACKUP_ROOT = os.path.join(BASE, "BACKUP FILES AUTOMATED")
TODAY_LABEL = datetime.now().strftime("%d-%m-%Y")
RUN_NO      = "4"
RUN_PATH    = os.path.join(BACKUP_ROOT, TODAY_LABEL, RUN_NO)

PICTURES_PATH   = os.path.join(RUN_PATH, "PICTURES")
MAIN_FILES_PATH = os.path.join(RUN_PATH, "MAIN FILES")
OUTPUT_PATH     = os.path.join(RUN_PATH, "OUTPUT")

for p in [PICTURES_PATH, MAIN_FILES_PATH, OUTPUT_PATH]:
    os.makedirs(p, exist_ok=True)

print(f"📂 Using backup folder: {RUN_PATH}")

backed_up_files = set()

# Images
image_exts = (".jpg", ".jpeg", ".png")
for f in os.listdir(BASE):
    src = os.path.join(BASE, f)
    if os.path.isfile(src) and f.lower().endswith(image_exts):
        shutil.copy2(src, os.path.join(PICTURES_PATH, f))
        backed_up_files.add(src)
print("🖼️ Images backed up")

# Input files
for f in ["main.csv", "scenario.csv"]:
    src = os.path.join(BASE, f)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(MAIN_FILES_PATH, f))
        backed_up_files.add(src)

for f in os.listdir(BASE):
    if f.startswith("LAS Debit Balances-") and f.endswith(".xlsx"):
        src = os.path.join(BASE, f)
        shutil.copy2(src, os.path.join(MAIN_FILES_PATH, f))
        backed_up_files.add(src)
    if f.startswith("SA0") and f.endswith("_OUTPUT FILE.xlsx"):
        src = os.path.join(BASE, f)
        shutil.copy2(src, os.path.join(MAIN_FILES_PATH, f))
        backed_up_files.add(src)

_rr_common_files = [
    f for f in (os.listdir(COMMON_DIR) if os.path.isdir(COMMON_DIR) else [])
    if f.startswith("RISK_REPORT_") and f.endswith(".csv")
]
for f in _rr_common_files:
    src = os.path.join(COMMON_DIR, f)
    dst = os.path.join(MAIN_FILES_PATH, f)
    if not os.path.exists(dst):
        shutil.copy2(src, dst)
    print(f"📂 RISK_REPORT backed up from common folder → {f}")

print("📥 Main input files backed up")

# All xlsx/csv output
for f in os.listdir(BASE):
    src = os.path.join(BASE, f)
    if not os.path.isfile(src):
        continue
    if f.lower().endswith((".py", ".bat")):
        continue
    if f.lower().endswith((".xlsx", ".csv")):
        shutil.copy2(src, os.path.join(OUTPUT_PATH, f))
        backed_up_files.add(src)

print("📤 All CSV/XLSX output files backed up")

# Zonewise
zonewise_path = os.path.join(BASE, "ZONEWISE")
if os.path.exists(zonewise_path) and os.path.isdir(zonewise_path):
    for f in os.listdir(zonewise_path):
        src = os.path.join(zonewise_path, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(OUTPUT_PATH, f))
            backed_up_files.add(src)
    print("📁 ZONEWISE files backed up")
else:
    print("ℹ️ ZONEWISE folder not found, skipping backup")

# Kill Excel again before delete
for proc in psutil.process_iter(['pid', 'name']):
    try:
        if proc.info['name'] and "EXCEL" in proc.info['name'].upper():
            proc.kill()
    except Exception:
        pass
time.sleep(2)
gc.collect()
time.sleep(3)

# Safe delete
print("🧹 Starting SAFE cleanup")
for f in os.listdir(BASE):
    full_path = os.path.join(BASE, f)
    if not os.path.isfile(full_path):
        continue
    if f.lower().endswith((".py", ".bat")):
        continue
    if f.startswith("RISK_REPORT_") and f.endswith(".csv"):
        continue
    if f.lower().endswith((".xlsx", ".csv", ".jpg", ".jpeg", ".png")):
        safe_delete(full_path)

print("✅ BACKUP + CLEANUP COMPLETED SUCCESSFULLY")

# Delete ZONEWISE folder
if os.path.exists(zonewise_path):
    try:
        shutil.rmtree(zonewise_path)
        print("🗑️ ZONEWISE folder deleted after backup")
    except Exception as e:
        print(f"⚠️ Could not delete ZONEWISE folder: {e}")

print("✅ run_post_email_LAS4.py complete")
