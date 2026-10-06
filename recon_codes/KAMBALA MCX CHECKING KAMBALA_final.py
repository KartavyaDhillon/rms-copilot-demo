import pandas as pd
import os
from datetime import datetime
from openpyxl import load_workbook
import re



folder_path = r"C:\KAMBALA MCX CHECKING\Kambala"

# -------- List all files in folder --------
files = [os.path.join(folder_path, f) for f in os.listdir(folder_path)]

# -------- Identify Risk Report file --------
risk_file = None
for f in files:
    fname = os.path.basename(f).strip().lower()
    if fname.startswith("risk_report") and (fname.endswith(".csv") or fname.endswith(".xlsx")):
        risk_file = f
        break

# -------- Stop program if not found --------
if risk_file:
    print(f"✅ Risk Report file found: {os.path.basename(risk_file)}")
else:
    raise FileNotFoundError(f"⚠️ No Risk Report file found in folder: {folder_path}. Program stopped.")


folder_path = r"C:\KAMBALA MCX CHECKING\Kambala"
today_str = datetime.now().strftime("%d%m%Y")

files = [os.path.join(folder_path, f) for f in os.listdir(folder_path)]


compare_cols = ['Symbol','ExpDate','StrikePrice','OpType','CFBuyQty','CFSellQty']
all_cols = ['ClientID'] + compare_cols
cols_to_pick = [4,6,7,8,9,24,26]


# ---------- Find data files, skip RISK_REPORT ----------
data_files = [f for f in files if not os.path.basename(f).upper().startswith("RISK_REPORT")]

file_with_header = None
file_without_header = None

for f in data_files:
    try:
        df_sample = pd.read_csv(f, nrows=5) if f.endswith('.csv') else pd.read_excel(f, nrows=5)
        df_cols = [str(c).strip().lower() for c in df_sample.columns]

        # Detect header file (has ClientID or any compare_cols)
        if 'clientid' in df_cols or any(col.lower() in df_cols for col in compare_cols):
            file_with_header = f
        # Detect no-header file (does not have ClientID but enough columns)
        elif len(df_sample.columns) >= max(cols_to_pick)+1:
            file_without_header = f
    except:
        continue

if not file_with_header:
    raise Exception(f"No header file found.\nData files found: {data_files}")
if not file_without_header:
    raise Exception(f"No no-header file found.\nData files found: {data_files}")

print(f"File with header: {file_with_header}")
print(f"File without header: {file_without_header}")

# ---------- Read files ----------
df1 = pd.read_csv(file_with_header, dtype=str) if file_with_header.endswith('.csv') else pd.read_excel(file_with_header, dtype=str)
df1.columns = df1.columns.str.strip()

df2 = pd.read_csv(file_without_header, header=None, dtype=str) if file_without_header.endswith('.csv') else pd.read_excel(file_without_header, header=None, dtype=str)
df2 = df2.iloc[:, cols_to_pick]
df2.columns = all_cols

# ---------- Preprocess ----------
def preprocess(df):
    df['OpType'] = df['OpType'].replace('XX','')
    df = df.fillna(0)
    for col in ['ClientID','Symbol','OpType']:
        df[col] = df[col].astype(str).str.strip().str.lower().apply(lambda x: re.sub(r'[^a-z0-9]', '', x))
    def parse_date(x):
        if pd.isna(x) or str(x).strip() in [0,'0','', 'nan']:
            return pd.NaT
        x_str = str(x).strip()
        formats = [
            "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y",
            "%d/%m/%Y", "%d/%m/%y", "%d/%b/%Y", "%d/%b/%y",
            "%d-%B-%Y", "%d-%B-%y", "%Y-%m-%d"
        ]
        for fmt in formats:
            try:
                return pd.to_datetime(x_str, format=fmt).date()
            except:
                continue
        try:
            return pd.to_datetime(x_str).date()
        except:
            return pd.NaT
    df['ExpDate'] = df['ExpDate'].apply(parse_date)
    for col in ['StrikePrice','CFBuyQty','CFSellQty']:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    return df

df1 = preprocess(df1)
df2 = preprocess(df2)

# ---------- Comparison functions ----------
def is_equal_str(v1,v2):
    vals = ['', '0', 0, 0.0, None, 'nan']
    return (str(v1).strip().lower() in [str(x).lower() for x in vals] and
            str(v2).strip().lower() in [str(x).lower() for x in vals]) or str(v1).strip().lower() == str(v2).strip().lower()

def rows_equal(r1,r2):
    for col in compare_cols:
        v1 = r1[col]
        v2 = r2[col]
        if col in ['StrikePrice','CFBuyQty','CFSellQty']:
            if float(v1) != float(v2):
                return False
        elif col == 'ExpDate':
            if pd.isna(v1) and pd.isna(v2):
                continue
            if v1 != v2:
                return False
        else:
            if not is_equal_str(v1,v2):
                return False
    return True

# ---------- Grouped Comparison ----------
mismatch_rows = []

for client_id in set(df1['ClientID'].unique()).union(set(df2['ClientID'].unique())):
    grp1 = df1[df1['ClientID'] == client_id].copy()
    grp2 = df2[df2['ClientID'] == client_id].copy()
    used2 = set()
    
    # Exact matches
    for idx1, row1 in grp1.iterrows():
        for idx2, row2 in grp2.iterrows():
            if idx2 in used2:
                continue
            if rows_equal(row1, row2):
                used2.add(idx2)
                break

    # Partial/Not found matches
    for idx1, row1 in grp1.iterrows():
        already_matched = any(rows_equal(row1, grp2.loc[i]) for i in used2 if i in grp2.index)
        if already_matched:
            continue
        matched = False
        for idx2, row2 in grp2.iterrows():
            if idx2 in used2:
                continue
            diffs = []
            for col in compare_cols:
                v1 = row1[col]
                v2 = row2[col]
                if col in ['StrikePrice','CFBuyQty','CFSellQty']:
                    if float(v1) != float(v2):
                        diffs.append(f"{col}: File1={v1} | File2={v2}")
                elif col=='ExpDate':
                    if pd.isna(v1) and pd.isna(v2):
                        continue
                    if v1!=v2:
                        diffs.append(f"{col}: File1={v1} | File2={v2}")
                else:
                    if not is_equal_str(v1,v2):
                        diffs.append(f"{col}: File1={v1} | File2={v2}")
            if diffs:
                mismatch_rows.append({
                    'Headerfile_Row(file1)': idx1+2,
                    'Withoutheaderfile_Row(file2)': idx2+2,
                    'ClientID': client_id,
                    'Mismatch':'Values differ',
                    'Differences': "; ".join(diffs)
                })
                used2.add(idx2)
                matched = True
                break
        if not matched:
            mismatch_rows.append({
                'Headerfile_Row(file1)': idx1+2,
               'Withoutheaderfile_Row(file2)': '',
                'ClientID': client_id,
                'Mismatch':'Not in File2',
                'Differences':''
            })

    # Remaining unmatched in File2
    for idx2, row2 in grp2.iterrows():
        if idx2 not in used2:
            mismatch_rows.append({
                'Headerfile_Row(file1)':'',
                'Withoutheaderfile_Row(file2)': idx2+2,
                'ClientID': client_id,
                'Mismatch':'Not in File1',
                'Differences':''
            })

# ---------- Save output ----------
output_file = os.path.join(folder_path, f"Mismatched_MCX_KAMB_{today_str}.xlsx")
df_out = pd.DataFrame(mismatch_rows)
if df_out.empty:
    print("✅ No mismatch found!")
    df_out = pd.DataFrame([{"Message": "No mismatch found"}])
else:
    print("⚠️ Mismatches found, saving details...")

import re

# ---------- Create Clean Display Names ----------
def clean_name(filename):
    name = os.path.splitext(os.path.basename(filename))[0]   # remove extension
    # extract prefix before digits
    prefix = re.split(r'\d', name, 1)[0].rstrip('_- ')
    prefix = prefix.replace('_', ' ').strip().title()
    return prefix

header_display = clean_name(file_with_header)        # e.g. "Position Book"
noheader_display = clean_name(file_without_header)   # e.g. "Mcx Position"

# ---------- Rename Column Headers ----------
df_out.rename(columns={
    'Headerfile_Row(file1)': f"Mismatched Row in {header_display}",
    'Withoutheaderfile_Row(file2)': f"Mismatched Row in {noheader_display} File"
}, inplace=True)

# ---------- Update Mismatch Messages ----------
if 'Mismatch' in df_out.columns:
    df_out['Mismatch'] = df_out['Mismatch'].replace({
        'Not in File1': f"Record not exist in {header_display} File",
        'Not in File2': f"Record not exist in {noheader_display} File"
    })

# ---------- Identify RISK REPORT file ----------
risk_file = None
for f in files:
    name = os.path.basename(f).strip().lower()  # remove spaces + make lowercase
    if name.startswith("risk_report") and (name.endswith(".csv") or name.endswith(".xlsx")):
        risk_file = f
        break

# ---------- Identification by Auto ----------

if "ClientID" in df_out.columns:

    # Default value
    df_out["Identification by Auto"] = "Virtual ID"

    if risk_file:
        # Load the Risk Report file
        df_risk = (
            pd.read_csv(risk_file, dtype=str)
            if risk_file.endswith('.csv')
            else pd.read_excel(risk_file, dtype=str)
        )

        # Standardize header names
        df_risk.columns = df_risk.columns.str.strip().str.upper()

        if "ACCOUNTID" in df_risk.columns:
            # Create lookup set
            risk_ids = set(
                df_risk["ACCOUNTID"].astype(str).str.strip().str.lower()
            )

            # Apply identification logic
            df_out["Identification by Auto"] = (
                df_out["ClientID"]
                .astype(str)
                .str.strip()
                .str.lower()
                .apply(
                    lambda cid: (
                        "Exists in Risk Report"
                        if cid in risk_ids
                        else "Virtual ID"
                    )
                )
            )
        else:
            print("⚠️ ACCOUNTID column missing in Risk Report")
    else:
        print("⚠️ No RISK_REPORT file found, skipping identification.")

else:
    # If ClientID column missing, create empty column
    df_out["Identification by Auto"] = ""

# Save output
df_out.to_excel(output_file, index=False)

# Auto-adjust Excel columns
wb = load_workbook(output_file)
ws = wb.active
for col in ws.columns:
    max_length = 0
    column = col[0].column_letter
    for cell in col:
        try:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        except:
            pass
    ws.column_dimensions[column].width = max_length + 5
wb.save(output_file)
wb.close()   

print(f"Comparison done! Output saved to: {output_file}")

from email.message import EmailMessage
import smtplib
import os

# ---------- Email Configuration ----------
smtp_server = "smtp3.netcore.co.in"
smtp_port = 587
sender_email = "kartavya.dhillon@mastertrust.co.in"
password = "$Kartavya25*"

to_emails = ["kaushik.jana@mastertrust.co.in"]  # add more as needed    
cc_emails = ["rms_del@mastertrust.co.in","rms_chd@mastertrust.co.in"]        # add more as needed

subject = "Reconciliation of Positions"
body = """\
Hi Team,

Please find attached the mismatched files for your reference.

Warm regards,
Kartavya Dhillon
Master Capital Services Ltd
📞 011-66331000 | Ext. 84
"""

# ---------- Create Email ----------
msg = EmailMessage()
msg['From'] = sender_email
msg['To'] = ", ".join(to_emails)
msg['CC'] = ", ".join(cc_emails)
msg['Subject'] = subject
msg.set_content(body)  # plain text body

# ---------- Attach the Excel file properly ----------
with open(output_file, 'rb') as f:
    file_data = f.read()
    file_name = os.path.basename(output_file)
msg.add_attachment(file_data,
                   maintype='application',
                   subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                   filename=file_name)

# ---------- Send Email ----------
try:
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(sender_email, password)
        server.send_message(msg)
    print("✅ Email sent successfully with attachment!")
except Exception as e:
    print(f"⚠️ Failed to send email: {e}")


import shutil

# ---------- BACKUP SECTION BEFORE DELETING FILES ----------
print("\n===== BACKUP PROCESS STARTED =====")

# Extract date from risk report name (RISK_REPORT_01122025)
backup_date = None
risk_base = os.path.basename(risk_file)

match = re.search(r"(\d{8})", risk_base)
if match:
    raw_date = match.group(1)  # 01122025
    backup_date = f"{raw_date[:2]}-{raw_date[2:4]}-{raw_date[4:]}"  # 01-12-2025
else:
    # fallback to today's date
    backup_date = datetime.now().strftime("%d-%m-%Y")

# Create backup folder path
backup_folder = os.path.join(folder_path, "BACKUP FILES AUTOMATED", backup_date)
os.makedirs(backup_folder, exist_ok=True)

print(f"📁 Backup folder created: {backup_folder}")

# List of all files to back up
files_to_backup = [file_with_header, file_without_header, output_file]
if risk_file:
    files_to_backup.append(risk_file)

# Copy files to backup folder
for f in files_to_backup:
    try:
        if f and os.path.exists(f):
            shutil.copy(f, backup_folder)
            print(f"🔄 Backed up: {os.path.basename(f)}")
    except Exception as e:
        print(f"⚠️ Failed to backup {f}: {e}")

print("✅ Backup completed successfully!")
print("====================================\n")




# ---------- Delete the checked files ----------
try:
    if os.path.exists(file_with_header):
        os.remove(file_with_header)
        print(f"Deleted file with header: {file_with_header}")
    if os.path.exists(file_without_header):
        os.remove(file_without_header)
        print(f"Deleted file without header: {file_without_header}")
    if risk_file and os.path.exists(risk_file):
        os.remove(risk_file)
        print(f"Deleted Risk Report file: {risk_file}")
except Exception as e:
    print(f"⚠️ Error while deleting files: {e}")
