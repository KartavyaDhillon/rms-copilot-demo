"""
mtf_order.py — MTF Order File generator (wrapped as a function)
================================================================
Document 12, wrapped in run_mtf_order(). Margin-based matching against the risk
shortage report, builds KAMB + O319 order files, splits by zone, emails each zone,
sends a final combined email, then backs up + cleans.

ONLY changes from the original:
  • FOLDER        → .env (MTF_ORDER_FOLDER)
  • SMTP creds    → .env (SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD)
  • zone + final email recipients → .env (MTF_*_TO / MTF_*_CC, MTF_FINAL_TO/CC)
  • exit()        → return {"status": "missing_files", ...}
  • wrapped in def run_mtf_order(); everything else identical.
"""
import os
import re
import math
import time
import shutil
from datetime import datetime
import polars as pl
import pandas as pd
from openpyxl import load_workbook
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.application import MIMEApplication
from email import encoders
import smtplib
import warnings
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

FOLDER = os.getenv("MTF_ORDER_FOLDER", r"D:\MTF ORDER FILE")
MTF_PREFIX = "MTFPOSITION_"
RISK_PREFIX = "MTF_RISK_SHORTAGE_REPORT"
RISK_REPORT_PREFIX = "RISK_REPORT_"
TOLERANCE = 100.0


def _emails(key):
    return [e.strip() for e in os.getenv(key, "").split(",") if e.strip()]


def run_mtf_order():
    """MTF order file: margin matching → KAMB/O319 order files → zone split → emails → backup."""
    MTF_EDITED = os.path.join(FOLDER, "MtfPosition_edited.csv")

    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_USER = os.getenv("SENDER_EMAIL")
    SMTP_PASS = os.getenv("SENDER_PASSWORD")

    # ---- file check ----
    print("\nChecking Required Files...\n")
    required_ok = True
    files = os.listdir(FOLDER) if os.path.exists(FOLDER) else []
    mtf_found = any(f.upper().startswith("MTFPOSITION_") and f.lower().endswith(".csv") for f in files)
    if not mtf_found:
        print("❌ ERROR: No MTFPOSITION_*.csv file found!"); required_ok = False
    else:
        print("✔ MTFPOSITION file found.")
    risk_short_found = any(f.upper().startswith("MTF_RISK_SHORTAGE_REPORT") and
                           (f.lower().endswith(".xlsx") or f.lower().endswith(".csv")) for f in files)
    if not risk_short_found:
        print("❌ ERROR: MTF_RISK_SHORTAGE_REPORT file missing!"); required_ok = False
    else:
        print("✔ MTF_RISK_SHORTAGE_REPORT found.")
    risk_report_found = any(f.upper().startswith("RISK_REPORT_") and f.lower().endswith(".csv") for f in files)
    if not risk_report_found:
        print("❌ ERROR: RISK_REPORT_*.csv file missing!"); required_ok = False
    else:
        print("✔ RISK_REPORT file found.")
    if not required_ok:
        print("\nProgram stopped. Please place all required files and run again.\n")
        return {"status": "missing_files"}

    # ---- helpers ----
    def pick_latest(prefix):
        files2 = os.listdir(FOLDER)
        date_pattern = re.compile(rf"{prefix}\d{{8}}.*\.(csv|txt|xls|xlsx)$", re.IGNORECASE)
        dated_files = [f for f in files2 if date_pattern.match(f)]
        if dated_files:
            return os.path.join(FOLDER, sorted(dated_files, reverse=True)[0])
        simple_pattern = re.compile(rf"{prefix}.*\.(csv|txt|xls|xlsx)$", re.IGNORECASE)
        simple_files = [f for f in files2 if simple_pattern.match(f)]
        if simple_files:
            return os.path.join(FOLDER, sorted(simple_files, reverse=True)[0])
        return None

    def safe_pl_read_csv_allstr(path):
        df = pl.read_csv(path, infer_schema_length=0, ignore_errors=True, has_header=True)
        for c in df.columns:
            df = df.with_columns(pl.col(c).cast(pl.Utf8))
        return df

    def safe_num(x):
        try:
            if pd.isna(x): return 0.0
            s = str(x).replace(",", "").strip()
            if s == "": return 0.0
            return float(s)
        except Exception:
            return 0.0

    def safe_int(x):
        try:
            return int(round(safe_num(x)))
        except Exception:
            return 0

    # ---- PART 1: create edited file ----
    mtf_file = pick_latest(MTF_PREFIX)
    if not mtf_file:
        return {"status": "missing_files", "missing": ["MTFPOSITION_*"]}
    print("Creating edited file from:", mtf_file)
    ext = mtf_file.split(".")[-1].lower()
    if ext in ("csv", "txt"):
        pl_df = safe_pl_read_csv_allstr(mtf_file)
    elif ext in ("xls", "xlsx"):
        pl_df = pl.from_pandas(pd.read_excel(mtf_file, dtype=str))
    else:
        raise Exception(f"Unsupported file format: {mtf_file}")
    pdf = pl_df.to_pandas()
    mtf_base = os.path.basename(mtf_file)
    mtf_date = mtf_base.split("_")[1].split(".")[0]
    OUTPUT_FINAL = os.path.join(FOLDER, f"MTFPOSITION_{mtf_date}_SUMMARY.csv")

    for col in ["ClRate", "MtfRate", "MtfVar", "Qty", "MtfValueClosing"]:
        if col not in pdf.columns:
            pdf[col] = pd.NA
        pdf[col] = pdf[col].apply(safe_num)
    pdf["Qty"] = pdf["Qty"].apply(safe_int)
    pdf["MtfValueClosing"] = pdf.apply(
        lambda r: round(r["Qty"] * r["ClRate"], 2) if r["MtfValueClosing"] == 0 else r["MtfValueClosing"], axis=1)
    pdf["ClRate-MtfRate(L-K)"] = (pdf["ClRate"] - pdf["MtfRate"]).round(2)
    pdf["Margin to be released(U*N/100)"] = ((pdf["MtfValueClosing"] * pdf["MtfVar"]) / 100).round(2)
    pdf.to_csv(MTF_EDITED, index=False)
    print("Created:", MTF_EDITED)

    # ---- PART 2: margin-based matching ----
    pl_mtf = safe_pl_read_csv_allstr(MTF_EDITED)
    pdf = pl_mtf.to_pandas()
    original_columns = list(pdf.columns)
    pdf["_ClRate"] = pdf["ClRate"].apply(safe_num)
    pdf["_Qty"] = pdf["Qty"].apply(safe_int)
    pdf["_Margin"] = pdf["Margin to be released(U*N/100)"].apply(safe_num)
    pdf["_MtfVar"] = pdf["MtfVar"].apply(safe_num)
    pdf["_unit_margin"] = (pdf["_ClRate"] * pdf["_MtfVar"] / 100).round(6)

    acct_map = {}
    for idx, row in pdf.iterrows():
        acct = str(row["AccountID"]).strip()
        acct_map.setdefault(acct, []).append(idx)

    risk_file = pick_latest(RISK_PREFIX)
    if not risk_file:
        return {"status": "missing_files", "missing": ["MTF_RISK_SHORTAGE_REPORT"]}
    print("Using risk:", risk_file)
    ext = risk_file.split(".")[-1].lower()
    if ext in ("xls", "xlsx"):
        rpdf = pd.read_excel(risk_file, dtype=str)
    else:
        rpdf = safe_pl_read_csv_allstr(risk_file).to_pandas()

    acct_col = None; net_col = None
    for c in rpdf.columns:
        if c.strip().lower().replace(" ", "") in ("accountid", "account_id", "account"):
            acct_col = c
        if "net" in c.lower() and "short" in c.lower():
            net_col = c

    rpdf["_net"] = rpdf[net_col].apply(safe_num)
    negative_risk = rpdf[rpdf["_net"] < 0].copy()
    if "_net" in negative_risk.columns:
        negative_risk = negative_risk.drop(columns=["_net"])
    if ext == "xlsx":
        negative_risk.to_excel(risk_file, index=False)
    else:
        negative_risk.to_csv(risk_file, index=False)
    print("\n✔ Cleaned negative-only risk file saved as:", risk_file)
    rpdf = negative_risk.copy()

    risk_short = rpdf.copy()
    risk_short["_abs"] = risk_short[net_col].apply(safe_num).abs()
    risk_short = risk_short.sort_values("_abs", ascending=False)
    used = set()

    for _, r in risk_short.iterrows():
        acct = str(r[acct_col]).strip()
        shortage = abs(safe_num(r[net_col]))
        if acct not in acct_map:
            continue
        rows = acct_map[acct]
        cands = []
        for idx in rows:
            cands.append({"idx": idx, "margin": pdf.at[idx, "_Margin"], "unit": pdf.at[idx, "_unit_margin"],
                          "ClRate": pdf.at[idx, "_ClRate"], "Qty": pdf.at[idx, "_Qty"],
                          "lk": safe_num(pdf.at[idx, "ClRate-MtfRate(L-K)"])})
        used_map = {}
        remaining = shortage
        neg_cands = sorted([c for c in cands if c["lk"] < 0], key=lambda x: x["lk"])
        for s in neg_cands:
            if remaining <= 0: break
            unit = s["unit"]; qty_avail = s["Qty"]
            if unit <= 0 or qty_avail <= 0: continue
            full_rel = qty_avail * unit
            overshoot = full_rel - remaining
            if overshoot <= TOLERANCE:
                used_map[s["idx"]] = used_map.get(s["idx"], 0) + qty_avail
                remaining -= full_rel
                continue
            q = qty_avail
            while q > 0 and ((q * unit) - remaining) > TOLERANCE:
                q -= 1
            if q > 0:
                used_map[s["idx"]] = used_map.get(s["idx"], 0) + q
                remaining -= q * unit
        neg_used_indices = set([c["idx"] for c in neg_cands])
        avail_cands = [c for c in cands if c["idx"] not in neg_used_indices]
        if remaining > 0 and avail_cands:
            def pkey(c):
                diff = abs(c["margin"] - remaining)
                below = 0 if c["margin"] <= remaining else 1
                return (diff, below, c["margin"])
            primary = min(avail_cands, key=pkey)
            pidx = primary["idx"]; unit = primary["unit"]; avail = primary["Qty"]
            if unit > 0 and avail > 0:
                qty_need = math.ceil(remaining / unit)
                qty_need = min(qty_need, avail)
                released = qty_need * unit
                while qty_need > 0 and released > (remaining + TOLERANCE):
                    qty_need -= 1
                    released = qty_need * unit
                if qty_need > 0:
                    used_map[pidx] = used_map.get(pidx, 0) + qty_need
                    remaining -= released
            if remaining > 0:
                secondaries = sorted([c for c in avail_cands if c["idx"] != pidx and c["unit"] > 0],
                                     key=lambda x: x["unit"])
                for s in secondaries:
                    if remaining <= 0: break
                    unit = s["unit"]; qty_avail = s["Qty"]
                    qmax = min(qty_avail, math.floor(remaining / unit))
                    if qmax <= 0: continue
                    rel2 = qmax * unit
                    if (rel2 - remaining) <= TOLERANCE:
                        used_map[s["idx"]] = used_map.get(s["idx"], 0) + qmax
                        remaining -= rel2
        for idx, qtake in used_map.items():
            qtake = int(qtake)
            pdf.at[idx, "Qty"] = qtake
            new_val = round(qtake * pdf.at[idx, "_ClRate"], 2)
            pdf.at[idx, "MtfValueClosing"] = new_val
            pdf.at[idx, "Margin to be released(U*N/100)"] = round(new_val * pdf.at[idx, "_MtfVar"] / 100, 2)
            used.add(idx)

    final_pdf = pdf.loc[list(used), original_columns]
    for col in final_pdf.columns:
        if pd.api.types.is_float_dtype(final_pdf[col]):
            final_pdf[col] = final_pdf[col].round(2)
    final_pdf.to_csv(OUTPUT_FINAL, index=False)
    print("\nSaved:", OUTPUT_FINAL, "Rows:", len(final_pdf))

    # merge TRADING_SOFTWARE_TYPE
    pl_final = pl.read_csv(OUTPUT_FINAL, infer_schema_length=0, dtypes={"*": pl.Utf8}, ignore_errors=True)
    if "TRADING_SOFTWARE_TYPE" in pl_final.columns:
        pl_final = pl_final.drop("TRADING_SOFTWARE_TYPE")
    risk_rep_file = pick_latest(RISK_REPORT_PREFIX)
    if not risk_rep_file:
        return {"status": "missing_files", "missing": ["RISK_REPORT_*"]}
    pl_rrep = pl.read_csv(risk_rep_file, infer_schema_length=0).with_columns([
        pl.col("ACCOUNTID").cast(pl.Utf8), pl.col("TRADING_SOFTWARE_TYPE").cast(pl.Utf8)])
    pl_rrep = pl_rrep.rename({"ACCOUNTID": "AccountID"})
    pl_rrep_small = pl_rrep.select(["AccountID", "TRADING_SOFTWARE_TYPE"])
    pl_joined = pl_final.join(pl_rrep_small, on="AccountID", how="left")
    cols = pl_joined.columns
    new_cols = []
    for c in cols:
        new_cols.append(c)
        if c == "AccountID":
            new_cols.append("TRADING_SOFTWARE_TYPE")
    new_cols = [x for i, x in enumerate(new_cols) if x not in new_cols[:i]]
    pl_joined = pl_joined.select(new_cols)
    pl_joined.write_csv(OUTPUT_FINAL)
    print("SUMMARY updated with TRADING_SOFTWARE_TYPE")

    # PART 3: KAMB order file
    mtf_files = [f for f in os.listdir(FOLDER) if f.startswith("MTFPOSITION_")]
    mtf_file = sorted(mtf_files, reverse=True)[0]
    date_part = mtf_file.split("_")[1].split(".")[0]
    order_file = os.path.join(FOLDER, f"ORD_KAMB_MTFS_{date_part}.csv")
    final_df = pd.read_csv(OUTPUT_FINAL, dtype=str)
    final_df["ScripName"] = final_df["ScripName"].astype(str).str.strip()
    final_df["AccountID"] = final_df["AccountID"].astype(str).str.strip()
    final_df["Qty"] = pd.to_numeric(final_df["Qty"], errors="coerce").fillna(0).astype(int)
    if "TRADING_SOFTWARE_TYPE" in final_df.columns:
        final_df = final_df[final_df["TRADING_SOFTWARE_TYPE"] == "KAMB"].copy()
    order_df = pd.DataFrame({
        "Exchange": ["NSE"] * len(final_df), "Instr": ["EQ"] * len(final_df),
        "Symbol": final_df["ScripName"], "OptionType": [""] * len(final_df),
        "Strike": [0] * len(final_df), "ExpDate": [""] * len(final_df),
        "AccountId": final_df["AccountID"], "BUY/SELL": ["SELL"] * len(final_df),
        "CustFirm": ["C"] * len(final_df), "Product": ["MTFS"] * len(final_df),
        "PriceType": ["MKT"] * len(final_df), "OrdDuration": ["DAY"] * len(final_df),
        "Price": [0] * len(final_df), "Qty": final_df["Qty"], "TriggerPrice": [0] * len(final_df),
        "DiscQty": [0] * len(final_df), "Remarks": ["0:00"] * len(final_df),
        "MKT Prot%": [3] * len(final_df), "PCode": [""] * len(final_df), "OrderValue": [""] * len(final_df)})
    order_df.to_csv(order_file, index=False)
    print("Order file created:", order_file)

    # PART 4: O319 order file
    final_df2 = pd.read_csv(OUTPUT_FINAL, dtype=str)
    final_df2["ScripName"] = final_df2["ScripName"].astype(str).str.strip()
    final_df2["AccountID"] = final_df2["AccountID"].astype(str).str.strip()
    final_df2["Qty"] = pd.to_numeric(final_df2["Qty"], errors="coerce").fillna(0).astype(int)
    o319_file = None
    if "TRADING_SOFTWARE_TYPE" in final_df2.columns:
        final_df2 = final_df2[final_df2["TRADING_SOFTWARE_TYPE"] == "O319"].copy()
    if final_df2.empty:
        print("No O319 accounts found. Skipping O319 order file.")
    else:
        o319_file = os.path.join(FOLDER, f"ORD_O319_MTFS_{date_part}.csv")
        o319_df = pd.DataFrame({
            "EXCHANGE": ["NSE"] * len(final_df2), "SERIES": ["EQ"] * len(final_df2),
            "SYMBOL": final_df2["ScripName"], "FILLER1": ["NA"] * len(final_df2),
            "FILLER2": ["NA"] * len(final_df2), "FILLER3": ["NA"] * len(final_df2),
            "FILLER4": [0] * len(final_df2), "QTY1": final_df2["Qty"], "QTY2": final_df2["Qty"],
            "BUY/SELL": ["SELL"] * len(final_df2), "ORDER TYPE": ["MARKET"] * len(final_df2),
            "FILLER5": ["NA"] * len(final_df2), "CLI": ["CLI"] * len(final_df2),
            "PRODUCT TYPE": ["MTFS"] * len(final_df2), "VALIDITY": ["DAY"] * len(final_df2),
            "CLIENT CODE": final_df2["AccountID"]})
        o319_df.to_csv(o319_file, index=False)
        print("O319 Order file created:", o319_file)

    # Zone emails
    ZONE_EMAIL_BODY = """Dear Team,<br><br>
As discussed, we are preparing the MTFS order file for 0319 and Kambala cases.<br><br>
<b>Stock Selection:</b> we first pick the script with the higher loss (negative P/L), then cover the
remaining shortage with the next best, allowing a ₹100 buffer to keep the match practical.<br><br>
Best Regards,<br><b>Kartavya Dhillon</b><br>Master Capital Services Ltd<br>📞 011-66331000 | Ext. 84<br>
"""
    zone_to_list = {"CHND": _emails("MTF_CHND_TO"), "MUMB": _emails("MTF_MUMB_TO"),
                    "LUDH": _emails("MTF_LUDH_TO"), "DELH": _emails("MTF_DELH_TO")}
    zone_cc_list = {"CHND": _emails("MTF_CHND_CC"), "MUMB": _emails("MTF_MUMB_CC"),
                    "LUDH": _emails("MTF_LUDH_CC"), "DELH": _emails("MTF_DELH_CC")}

    risk_zone_df = pd.read_excel(risk_file, dtype=str) if risk_file.lower().endswith(".xlsx") else pd.read_csv(risk_file, dtype=str)
    risk_zone_df.columns = risk_zone_df.columns.str.strip()
    risk_zone_df["Account ID"] = risk_zone_df["Account ID"].astype(str).str.strip()
    risk_zone_df["Zone"] = risk_zone_df["Zone"].astype(str).str.strip().str.upper()
    zone_map = dict(zip(risk_zone_df["Account ID"], risk_zone_df["Zone"]))
    print("✔ Zone mapping loaded:", len(zone_map), "accounts")

    kamb_df_raw = pd.read_csv(order_file, dtype=str)
    kamb_df_raw["AccountId"] = kamb_df_raw["AccountId"].astype(str)
    kamb_df_raw["_Zone"] = kamb_df_raw["AccountId"].map(zone_map).fillna("DELH")
    o319_df_raw = None
    if o319_file:
        temp319 = pd.read_csv(o319_file, dtype=str)
        temp319["CLIENT CODE"] = temp319["CLIENT CODE"].astype(str)
        temp319["_Zone"] = temp319["CLIENT CODE"].map(zone_map).fillna("DELH")
        o319_df_raw = temp319.copy()
    summary_df_raw = pd.read_csv(OUTPUT_FINAL, dtype=str)
    summary_df_raw["Zone"] = summary_df_raw["Zone"].astype(str).str.strip().str.upper()

    zones = {"CHND": ["ZOCHND"], "MUMB": ["ZOMUMB"], "LUDH": ["ZOLUDH", "ZO"], "DELH": []}
    base_summary_name = os.path.basename(OUTPUT_FINAL).replace(".csv", "")

    def create_zone_files(zone_name, zone_values):
        print(f"\n----- Processing Zone: {zone_name} -----")
        if zone_name == "DELH":
            summary_zone = summary_df_raw[~summary_df_raw["Zone"].isin(zones["CHND"] + zones["MUMB"] + zones["LUDH"])]
        else:
            summary_zone = summary_df_raw[summary_df_raw["Zone"].isin(zone_values)]
        summary_path = os.path.join(FOLDER, f"{base_summary_name}_{zone_name}.csv")
        summary_zone.to_csv(summary_path, index=False)
        if zone_name == "DELH":
            kamb_zone = kamb_df_raw[~kamb_df_raw["_Zone"].isin(zones["CHND"] + zones["MUMB"] + zones["LUDH"])]
        else:
            kamb_zone = kamb_df_raw[kamb_df_raw["_Zone"].isin(zone_values)]
        kamb_zone_path = os.path.join(FOLDER, f"ORD_KAMB_MTFS_{date_part}_{zone_name}.csv")
        kamb_zone.drop(columns=["_Zone"], errors="ignore").to_csv(kamb_zone_path, index=False)
        o319_zone_path = None
        if o319_df_raw is not None:
            if zone_name == "DELH":
                o319_zone = o319_df_raw[~o319_df_raw["_Zone"].isin(zones["CHND"] + zones["MUMB"] + zones["LUDH"])]
            else:
                o319_zone = o319_df_raw[o319_df_raw["_Zone"].isin(zone_values)]
            if not o319_zone.empty:
                o319_zone_path = os.path.join(FOLDER, f"ORD_O319_MTFS_{date_part}_{zone_name}.csv")
                o319_zone.drop(columns=["_Zone"], errors="ignore").to_csv(o319_zone_path, index=False)
        if zone_name == "DELH":
            risk_zone = risk_zone_df[~risk_zone_df["Zone"].isin(zones["CHND"] + zones["MUMB"] + zones["LUDH"])]
        else:
            risk_zone = risk_zone_df[risk_zone_df["Zone"].isin(zone_values)]
        risk_zone_path = os.path.join(FOLDER, f"MTF_RISK_SHORTAGE_REPORT_{zone_name}.csv")
        risk_zone.to_csv(risk_zone_path, index=False)

        attachments = [summary_path, kamb_zone_path, risk_zone_path]
        if o319_zone_path:
            attachments.append(o319_zone_path)
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(zone_to_list[zone_name])
        msg["Cc"] = ", ".join(zone_cc_list[zone_name])
        msg["Subject"] = f"MTFS {zone_name} Order File – 0319 & Kambala Cases"
        msg.attach(MIMEText(ZONE_EMAIL_BODY, "html"))
        for fp in attachments:
            with open(fp, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(fp)}"')
            msg.attach(part)
        recipients = zone_to_list[zone_name] + zone_cc_list[zone_name]
        while True:
            try:
                print(f"Sending ONE email for zone {zone_name} with {len(attachments)} files...")
                server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
                server.starttls(); server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, recipients, msg.as_string()); server.quit()
                print(f"✔ Email sent for zone {zone_name}")
                break
            except Exception as e:
                print("❌ Zone email failed:", e); print("Retrying in 5 seconds..."); time.sleep(5)

    for zname, zvals in zones.items():
        create_zone_files(zname, zvals)
    print("\n✔ All Zone-wise CSV files created and emailed successfully!\n")

    # Final combined email
    to_list = _emails("MTF_FINAL_TO") or ["rms_del@mastertrust.co.in"]
    cc_list = _emails("MTF_FINAL_CC")
    subject = "MTFS Order Full File – 0319 & Kambala Cases"
    body = ZONE_EMAIL_BODY
    attachments = [OUTPUT_FINAL, order_file, risk_file]
    if o319_file:
        attachments.append(o319_file)

    def send_final():
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(to_list)
        msg["Cc"] = ", ".join(cc_list)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))
        for file_path in attachments:
            with open(file_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(file_path))
            part["Content-Disposition"] = f'attachment; filename="{os.path.basename(file_path)}"'
            msg.attach(part)
        all_recipients = to_list + cc_list
        while True:
            try:
                print("Sending final combined email...")
                with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                    server.starttls(); server.login(SMTP_USER, SMTP_PASS)
                    server.sendmail(SMTP_USER, all_recipients, msg.as_string())
                print("✔ Final combined email sent successfully!")
                break
            except Exception as e:
                print("❌ Final email failed:", e); print("Retrying in 5 seconds..."); time.sleep(5)

    send_final()

    # Backup + clean
    print("\nCreating Backup...")
    backup_folder_name = f"{date_part[:2]}-{date_part[2:4]}-{date_part[4:]}"
    backup_root = os.path.join(FOLDER, "BACKUP FILES AUTOMATED")
    backup_path = os.path.join(backup_root, backup_folder_name)
    os.makedirs(backup_path, exist_ok=True)
    print("Backup folder:", backup_path)
    files_to_move = []
    for f in os.listdir(FOLDER):
        fp = os.path.join(FOLDER, f)
        if os.path.isdir(fp): continue
        if f.lower().endswith(".py") or f.lower().endswith(".bat"): continue
        files_to_move.append(fp)
    for file_path in files_to_move:
        try:
            shutil.move(file_path, backup_path)
            print("Moved:", os.path.basename(file_path))
        except Exception as e:
            print("Error moving", file_path, ":", e)
    print("\nBackup process completed successfully!")
    return {"status": "ok", "rows": len(final_pdf)}


if __name__ == "__main__":
    print(run_mtf_order())
