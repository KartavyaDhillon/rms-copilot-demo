"""
cnc_f11.py — CNC F11 PositionBook processor (wrapped as a function)
====================================================================
Your CNC_F11 script, wrapped in run_cnc_f11() so the platform can call it.
ONLY changes from your original:
  • FOLDER            → .env (CNC_F11_FOLDER)
  • SMTP credentials  → .env (SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD)
  • recipients/cc     → .env (CNC_F11_TO, CNC_F11_CC)
  • exit()            → return {"status": "missing_files", ...}
  • wrapped in def run_cnc_f11(); the rest is identical to your logic.
"""
import os
import pandas as pd
import datetime
import shutil
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

load_dotenv()

FOLDER = os.getenv("CNC_F11_FOLDER", r"C:\CNC_F11")

REQUIRED_FILES = ["PositionBook.csv"]

RESULT_FILE = os.path.join(FOLDER, "result.xlsx")
FINAL_CSV   = os.path.join(FOLDER, "Final_Result.csv")

FINAL_COLUMNS = ["ClientID", "Exch", "Symbol", "Instrument", "Product",
                 "NetBuyQty", "NetSellQty", "NetQty"]

COLUMN_MAP = {
    "clientid": "ClientID", "accountid": "ClientID",
    "exch": "Exch", "exchg-seg": "Exch",
    "symbol": "Symbol",
    "instrument": "Instrument", "instrument name": "Instrument",
    "product": "Product", "product type": "Product",
    "netbuyqty": "NetBuyQty", "netbuy quantity": "NetBuyQty",
    "netsellqty": "NetSellQty", "netsell quantity": "NetSellQty",
    "netqty": "NetQty", "net quantity": "NetQty",
}


def read_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(file_path)
    else:
        return pd.read_excel(file_path)

def normalize_columns(df):
    new_cols = {}
    for col in df.columns:
        key = col.strip().lower()
        if key in COLUMN_MAP:
            new_cols[col] = COLUMN_MAP[key]
    return df.rename(columns=new_cols)

def process(df):
    df = normalize_columns(df)
    missing = [c for c in FINAL_COLUMNS if c not in df.columns]
    for col in missing:
        df[col] = None
    return df[FINAL_COLUMNS]

def add_union(df):
    df = df.copy()
    df.insert(df.columns.get_loc("Symbol")+1, "Union",
              df["ClientID"].astype(str) + df["Exch"].astype(str) + df["Symbol"].astype(str))
    return df

def add_vl_column(df_to_update, df_lookup):
    df_to_update = df_to_update.copy()
    lookup_set = set(df_lookup["Union"])
    df_to_update["VL"] = df_to_update["Union"].apply(lambda x: x if x in lookup_set else "N/A")
    return df_to_update

def reorder_columns(df):
    df = df.copy()
    if "VL" in df.columns:
        cols = df.columns.tolist()
        cols.remove("VL")
        product_idx = cols.index("Product")
        cols.insert(product_idx + 1, "VL")
        df = df[cols]
    return df

def update_sheet4_quantities(sheet4_df, sheet5_df, buying_df):
    sheet4_df = sheet4_df.copy()
    buying_lookup = buying_df.set_index("Union")["NetQty"].to_dict()
    sheet4_df["NetBuyQty"] = sheet4_df["Union"].map(buying_lookup).fillna(0)
    sheet4_df["NetSellQty"] = 0
    sheet4_df["NetQty"] = 0
    sheet5_lookup = sheet5_df.set_index("Union")["NetSellQty"].to_dict()
    sheet4_df["NetSellQty"] = sheet4_df["Union"].map(sheet5_lookup).fillna(0)
    sheet4_df["NetQty"] = sheet4_df["NetBuyQty"] - sheet4_df["NetSellQty"]
    sheet4_df["Product"] = "CNC"
    if "VL" in sheet4_df.columns:
        sheet4_df = sheet4_df.drop(columns=["VL"])
    return sheet4_df

def append_filtered_cnc(sheet1_df, sheet4_df):
    sheet1_df = sheet1_df.copy()
    sheet1_df["NetBuyQty"] = pd.to_numeric(sheet1_df["NetBuyQty"], errors="coerce").fillna(0)
    sheet1_df["NetSellQty"] = pd.to_numeric(sheet1_df["NetSellQty"], errors="coerce").fillna(0)
    sheet1_df["NetQty"] = pd.to_numeric(sheet1_df["NetQty"], errors="coerce").fillna(0)
    filtered_cnc = sheet1_df[
        (sheet1_df["Product"] == "CNC") &
        (sheet1_df["NetBuyQty"] != 0) &
        (sheet1_df["NetSellQty"] != 0)
    ].copy()
    sheet4_df = pd.concat([sheet4_df, filtered_cnc], ignore_index=True)
    sheet4_df["NetQty"] = sheet4_df["NetQty"].replace(0, "")
    sheet4_df["NetQty_sort"] = pd.to_numeric(sheet4_df["NetQty"], errors="coerce")
    sheet4_df = sheet4_df.sort_values(by="NetQty_sort", na_position="last").drop(columns="NetQty_sort")
    if "Union" in sheet4_df.columns:
        sheet4_df = sheet4_df.drop(columns=["Union"])
    return sheet4_df

def clean_clientid_column(df):
    if "ClientID" not in df.columns:
        return df
    s = df["ClientID"].astype(str).str.strip()
    s = s.str.replace(r"^'+", "", regex=True)
    s = s.str.replace(r"\.0+$", "", regex=True)
    nums = pd.to_numeric(s, errors="coerce")
    mask_numeric = nums.notnull()
    df = df.copy()
    df.loc[mask_numeric, "ClientID"] = nums[mask_numeric].astype(int)
    df.loc[~mask_numeric, "ClientID"] = s[~mask_numeric]
    return df


def export_and_send_email(sheet4_df, folder):
    today_str = datetime.datetime.now().strftime("%d-%m-%Y")
    xls_filename = f"CNC_F11_DATED_{today_str}.xlsx"
    xls_path = os.path.join(folder, xls_filename)
    sheet4_df.to_excel(xls_path, index=False)
    print(f"✅ Sheet4 exported to Excel: {xls_path}")

    # SMTP from .env
    smtp_server = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    smtp_port   = int(os.getenv("SMTP_PORT", 587))
    sender_email = os.getenv("SENDER_EMAIL")
    password     = os.getenv("SENDER_PASSWORD")

    recipients = [e.strip() for e in os.getenv("CNC_F11_TO", "").split(",") if e.strip()]
    cc_list    = [e.strip() for e in os.getenv("CNC_F11_CC", "").split(",") if e.strip()]
    all_recipients = recipients + cc_list

    subject = "CNC_F11"
    body = f"""Dear Sir,

Please find attached file of CNC_F11 dated {today_str}.

Best Regards,
Kartavya Dhillon
Master Capital Services Ltd
📞 011-66331000 | Ext. 84
"""
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = ", ".join(recipients)
    msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with open(xls_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f"attachment; filename={xls_filename}")
    msg.attach(part)

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, password)
            server.sendmail(sender_email, all_recipients, msg.as_string())
        print("📧 Email sent successfully with attachment!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

    # BACKUP BEFORE DELETION
    backup_root = os.path.join(folder, "BACKUP AUTOMATION")
    os.makedirs(backup_root, exist_ok=True)
    today_str = datetime.datetime.now().strftime("%d-%m-%Y")
    backup_folder = os.path.join(backup_root, today_str)
    os.makedirs(backup_folder, exist_ok=True)

    for f in os.listdir(folder):
        if f.lower().endswith((".csv", ".xlsx", ".xls")):
            src = os.path.join(folder, f)
            dst = os.path.join(backup_folder, f)
            try:
                shutil.copy2(src, dst)
                print(f"📦 Backed up file: {f} → {backup_folder}")
            except Exception as e:
                print(f"⚠️ Failed to backup {f}: {e}")

    for f in os.listdir(folder):
        if f.lower().endswith((".csv", ".xlsx")):
            try:
                os.remove(os.path.join(folder, f))
                print(f"🗑 Deleted file: {f}")
            except Exception as e:
                print(f"⚠️ Could not delete {f}: {e}")

    return xls_path


def run_cnc_f11():
    """CNC F11: read PositionBook → buying/selling match → Sheet4 → email → backup."""
    # required file check
    present_files = os.listdir(FOLDER) if os.path.exists(FOLDER) else []
    missing = [req for req in REQUIRED_FILES
               if not any(f.lower() == req.lower() for f in present_files)]
    if missing:
        print("❌ Missing required file(s):")
        for f in missing:
            print("   -", f)
        print("\nPlease place the above file(s) inside the folder and run again.")
        return {"status": "missing_files", "missing": missing}
    print("✅ All required input files found!")

    file_path = os.path.join(FOLDER, "PositionBook.csv")
    result_df = process(read_file(file_path))
    print(f"Rows loaded from PositionBook: {len(result_df)}")

    buying_df = result_df[(result_df["Product"].isin(["MTF", "NRML"])) & (result_df["NetQty"] > 0)]
    buying_df = add_union(buying_df)

    selling_df = result_df[(result_df["Product"].isin(["MTFS", "CNC"])) & (result_df["NetQty"] < 0)]
    selling_df = add_union(selling_df)

    buying_df = add_vl_column(buying_df, selling_df)
    selling_df = add_vl_column(selling_df, buying_df)

    sheet4_df = buying_df[buying_df["VL"] != "N/A"]
    sheet4_df = reorder_columns(sheet4_df)
    sheet5_df = selling_df[selling_df["VL"] != "N/A"]
    sheet5_df = reorder_columns(sheet5_df)

    sheet4_df = update_sheet4_quantities(sheet4_df, sheet5_df, buying_df)
    sheet4_df = append_filtered_cnc(result_df, sheet4_df)

    with pd.ExcelWriter(RESULT_FILE, engine="openpyxl") as writer:
        result_df.to_excel(writer, index=False, sheet_name="Sheet1")
        buying_df.to_excel(writer, index=False, sheet_name="Buying")
        selling_df.to_excel(writer, index=False, sheet_name="Selling")
        sheet4_df.to_excel(writer, index=False, sheet_name="Sheet4")
        sheet5_df.to_excel(writer, index=False, sheet_name="Sheet5")
    print(f"✅ Result saved to: {RESULT_FILE}")

    export_and_send_email(sheet4_df, FOLDER)
    return {"status": "ok"}


if __name__ == "__main__":
    print(run_cnc_f11())
