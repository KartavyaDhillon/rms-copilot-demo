import pandas as pd
import os
from datetime import datetime
from openpyxl import load_workbook
import re
from email.message import EmailMessage
import smtplib

# ================== INITIAL FILE CHECK BEFORE PROCESS STARTS ==================

folder_path = r"C:\FILES CHECKING POSITIONBOOK"

required_files = {
    "PositionBook.xlsx": None,
    "FO TXT (fo_position_kambala_*.txt)": None,
    "BSEF TXT (bsef_position_kamb_*.txt)": None,
    "CASH TXT (KAMB_CASH_POS_NRML_*.txt)": None,
    "RISK REPORT (risk_report*.csv/xlsx)": None,
}

all_files = os.listdir(folder_path)

# Check PositionBook
if "PositionBook.xlsx" in all_files:
    required_files["PositionBook.xlsx"] = True

# Identify files by pattern
for f in all_files:
    fname = f.lower()

    if fname.startswith("fo_position_kambala_") and fname.endswith(".txt"):
        required_files["FO TXT (fo_position_kambala_*.txt)"] = True

    if fname.startswith("bsef_position_kamb_") and fname.endswith(".txt"):
        required_files["BSEF TXT (bsef_position_kamb_*.txt)"] = True

    if fname.startswith("kamb_cash_pos_nrml_") and fname.endswith(".txt"):
        required_files["CASH TXT (KAMB_CASH_POS_NRML_*.txt)"] = True

    if fname.startswith("risk_report") and (fname.endswith(".csv") or fname.endswith(".xlsx")):
        required_files["RISK REPORT (risk_report*.csv/xlsx)"] = True

# Check results
missing_files = [name for name, found in required_files.items() if not found]

if missing_files:
    print("\n❌ Program stopped — missing required files:\n")
    for m in missing_files:
        print(f" - {m}")
    print("\n⚠️ Please keep all files in folder & run again.")
    exit()
else:
    print("✅ All required files found — proceeding...\n")



# ================== CONFIG ==================
folder_path = r"C:\FILES CHECKING POSITIONBOOK"
today_str = datetime.now().strftime("%d%m%Y")


# ================== HELPERS (unchanged style) ==================
compare_cols = ['Symbol','ExpDate','StrikePrice','OpType','CFBuyQty','CFSellQty']
all_cols = ['ClientID'] + compare_cols

def clean_name(filename):
    name = os.path.splitext(os.path.basename(filename))[0]
    prefix = re.split(r'\d', name, 1)[0].rstrip('_- ')
    prefix = prefix.replace('_', ' ').strip().title()
    return prefix

def parse_date_generic(x):
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

def preprocess(df):
    df['OpType'] = df['OpType'].replace('XX','')
    df = df.fillna(0)
    for col in ['ClientID','Symbol','OpType']:
        df[col] = df[col].astype(str).str.strip().str.lower().apply(lambda x: re.sub(r'[^a-z0-9]', '', x))
    df['ExpDate'] = df['ExpDate'].apply(parse_date_generic)
    for col in ['StrikePrice','CFBuyQty','CFSellQty']:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    return df

# ========== YOUR EXACT LOGIC (DITTO) ==========
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
# ==============================================

# ================== FILE DISCOVERY ==================
files = [os.path.join(folder_path, f) for f in os.listdir(folder_path)]

# Risk Report
risk_file = None
for f in files:
    fname = os.path.basename(f).strip().lower()
    if fname.startswith("risk_report") and (fname.endswith(".csv") or fname.endswith(".xlsx")):
        risk_file = f
        break

if risk_file:
    print(f"✅ Risk Report file found: {os.path.basename(risk_file)}")
else:
    raise FileNotFoundError(f"⚠️ No Risk Report file found in folder: {folder_path}. Program stopped.")

# Header file: PositionBook.xlsx
position_book_path = os.path.join(folder_path, "PositionBook.xlsx")
if not os.path.exists(position_book_path):
    raise FileNotFoundError("⚠️ 'PositionBook.xlsx' not found. Program stopped.")
print(f"✅ Header file found: {os.path.basename(position_book_path)}")

# TXT files by prefix
fo_txt = None
bsef_txt = None
for f in files:
    base = os.path.basename(f).strip().lower()
    if base.startswith("fo_position_kambala_") and base.endswith(".txt"):
        fo_txt = f
    elif base.startswith("bsef_position_kamb_") and base.endswith(".txt"):
        bsef_txt = f

if not fo_txt:
    raise FileNotFoundError("⚠️ No 'fo_position_kambala_*.txt' file found.")
if not bsef_txt:
    raise FileNotFoundError("⚠️ No 'bsef_position_kamb_*.txt' file found.")

print(f"✅ FO TXT:   {os.path.basename(fo_txt)}")
print(f"✅ BSEF TXT: {os.path.basename(bsef_txt)}")

# ================== READ POSITION BOOK ==================
df_pb = pd.read_excel(position_book_path, dtype=str)
df_pb.columns = df_pb.columns.str.strip()

# StrikePrice = -0.01 -> 0 (as requested)
if 'StrikePrice' in df_pb.columns:
    df_pb['StrikePrice'] = pd.to_numeric(df_pb['StrikePrice'], errors='coerce').fillna(0)
    df_pb.loc[df_pb['StrikePrice'] == -0.01, 'StrikePrice'] = 0
    df_pb['StrikePrice'] = df_pb['StrikePrice'].astype(str)

# Validate required columns
required_pb_cols = ['ClientID','Symbol','ExpDate','StrikePrice','OpType','CFSellQty','CFBuyQty','Exch']
missing = [c for c in required_pb_cols if c not in df_pb.columns]
if missing:
    raise Exception(f"⚠️ Missing columns in PositionBook.xlsx: {missing}")

# Split by Exch
df1_nfo = df_pb[df_pb['Exch'].astype(str).str.strip().str.upper() == 'NFO'][all_cols].copy()
df1_bfo = df_pb[df_pb['Exch'].astype(str).str.strip().str.upper() == 'BFO'][all_cols].copy()

# ================== READ TXT (NO HEADER, COMMA, SKIP FIRST ROW) ==================
# Mapping: A→ClientID(0), C→Symbol(2), E→ExpDate(4), F→StrikePrice(5), G→OpType(6), H→CFBuyQty(7), J→CFSellQty(9)
txt_pick_idx = [0, 2, 4, 5, 6, 7, 9]  # order must align to all_cols

def read_position_txt(path):
    df_raw = pd.read_csv(path, header=None, dtype=str, skiprows=1)
    max_idx = max(txt_pick_idx)
    if df_raw.shape[1] <= max_idx:
        raise Exception(f"File {os.path.basename(path)} does not have required columns.")
    df = df_raw.iloc[:, txt_pick_idx].copy()
    df.columns = all_cols
    return df

df2_fo  = read_position_txt(fo_txt)
df2_bse = read_position_txt(bsef_txt)

# ================== PREPROCESS (same as your code) ==================
df1_nfo = preprocess(df1_nfo)
df1_bfo = preprocess(df1_bfo)
df2_fo  = preprocess(df2_fo)
df2_bse = preprocess(df2_bse)

# ================== RUN YOUR EXACT GROUPED COMPARISON (twice) ==================
def run_one(df1, df2, header_file, without_header_file, out_suffix):
    header_display = clean_name(header_file)
    noheader_display = clean_name(without_header_file)

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
                            diffs.append(f"{col}: {header_display}={v1} | {noheader_display}={v2}")
                    elif col=='ExpDate':
                        if pd.isna(v1) and pd.isna(v2):
                            continue
                        if v1!=v2:
                            diffs.append(f"{col}: {header_display}={v1} | {noheader_display}={v2}")
                    else:
                        if not is_equal_str(v1,v2):
                            diffs.append(f"{col}: {header_display}={v1} | {noheader_display}={v2}")

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
    output_file = os.path.join(folder_path, f"Mismatched_{out_suffix}_KAMBALA_{today_str}.xlsx")
    df_out = pd.DataFrame(mismatch_rows)
    if df_out.empty:
        print("✅ No mismatch found!")
        df_out = pd.DataFrame([{"Message": "No mismatch found"}])
    else:
        print("⚠️ Mismatches found, saving details...")

    # ---------- Rename columns/messages to display names ----------
    header_display    = clean_name(header_file)         # "Positionbook"
    noheader_display  = clean_name(without_header_file) # e.g. "Fo Position Kambala"

    df_out.rename(columns={
        'Headerfile_Row(file1)': f"Mismatched Row in {header_display}",
        'Withoutheaderfile_Row(file2)': f"Mismatched Row in {noheader_display} File"
    }, inplace=True)

    if 'Mismatch' in df_out.columns:
        df_out['Mismatch'] = df_out['Mismatch'].replace({
            'Not in File1': f"Record not exist in {header_display} File",
            'Not in File2': f"Record not exist in {noheader_display} File"
        })

    # ---------- Risk report identification (same as your code) ----------
    df_out["Identification by Auto"] = "Virtual ID"
    if risk_file:
        df_risk = pd.read_csv(risk_file, dtype=str) if risk_file.endswith('.csv') else pd.read_excel(risk_file, dtype=str)
        df_risk.columns = df_risk.columns.str.strip().str.upper()
        if "ACCOUNTID" in df_risk.columns:
            risk_ids = set(df_risk["ACCOUNTID"].astype(str).str.strip().str.lower())
            if 'ClientID' in df_out.columns:
                df_out["Identification by Auto"] = df_out["ClientID"].astype(str).str.strip().str.lower().apply(
                    lambda cid: "Exists in Risk Report" if cid in risk_ids else "Virtual ID"
                )
        else:
            print("⚠️ ACCOUNTID column missing in Risk Report")
    else:
        print("⚠️ No RISK_REPORT file found, skipping identification.")

    # Save Excel + autosize
    df_out.to_excel(output_file, index=False)
    wb = load_workbook(output_file); ws = wb.active
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

    print(f"Comparison done! Output saved to: {output_file}")
    return output_file

# Run NFO vs FO TXT
file_out_nfo = run_one(df1_nfo, df2_fo, position_book_path, fo_txt, "NFO")

# Run BFO vs BSEF TXT
file_out_bfo = run_one(df1_bfo, df2_bse, position_book_path, bsef_txt, "BFO")



# ================== CASH (DITTO LOGIC) ==================

# ================== CASH FILE COMPARISON (EXACT SAME LOGIC, 3 COLUMNS) ==================

# find CASH file
cash_txt = None
for f in files:
    base = os.path.basename(f).strip().lower()
    if base.startswith("kamb_cash_pos_nrml_") and base.endswith(".txt"):
        cash_txt = f
        break

if not cash_txt:
    raise FileNotFoundError("⚠️ CASH file 'KAMB_CASH_POS_NRML_*.txt' not found")

print(f"✅ CASH TXT: {os.path.basename(cash_txt)}")

# read TXT (A,C,F) skip first line
df2_cash = pd.read_csv(cash_txt, header=None, dtype=str, skiprows=1)
df2_cash = df2_cash.iloc[:, [0, 2, 4]]
df2_cash.columns = ['ClientID','Symbol','NetQty']

# filter PB for NSE
df1_cash = df_pb[df_pb['Exch'].astype(str).str.strip().str.upper() == 'NSE'][['ClientID','Symbol','NetQty']].copy()

# cleaning like your code style
df1_cash = df1_cash.fillna(0)
df2_cash = df2_cash.fillna(0)

for col in ['ClientID','Symbol']:
    df1_cash[col] = df1_cash[col].astype(str).str.strip().str.lower().apply(lambda x: re.sub(r'[^a-z0-9]', '', x))
    df2_cash[col] = df2_cash[col].astype(str).str.strip().str.lower().apply(lambda x: re.sub(r'[^a-z0-9]', '', x))

df1_cash['NetQty'] = pd.to_numeric(df1_cash['NetQty'], errors='coerce').fillna(0)
df2_cash['NetQty'] = pd.to_numeric(df2_cash['NetQty'], errors='coerce').fillna(0)

# define compare cols for this run
compare_cols_cash = ['Symbol','NetQty']

def rows_equal_cash(r1,r2):
    for col in compare_cols_cash:
        v1 = r1[col]
        v2 = r2[col]
        if col == 'NetQty':
            if float(v1) != float(v2): return False
        else:
            if not is_equal_str(v1,v2): return False
    return True

# run comparison (same loop style)
mismatch_rows_cash = []
header_display = "PositionBook"
noheader_display = clean_name(cash_txt)

for client in set(df1_cash['ClientID']).union(df2_cash['ClientID']):
    grp1 = df1_cash[df1_cash['ClientID']==client]
    grp2 = df2_cash[df2_cash['ClientID']==client]
    used = set()

    # exact matches
    for i1,r1 in grp1.iterrows():
        for i2,r2 in grp2.iterrows():
            if i2 in used: continue
            if rows_equal_cash(r1,r2):
                used.add(i2)
                break

    # differences + missing in file2
    for i1,r1 in grp1.iterrows():
        if any(rows_equal_cash(r1, grp2.loc[i]) for i in used if i in grp2.index):
            continue
        matched = False
        for i2,r2 in grp2.iterrows():
            if i2 in used: continue
            diffs=[]
            for col in compare_cols_cash:
                v1=r1[col]; v2=r2[col]
                if col=='NetQty':
                    if float(v1)!=float(v2):
                        diffs.append(f"{col}: {header_display}={v1} | {noheader_display}={v2}")
                else:
                    if not is_equal_str(v1,v2):
                        diffs.append(f"{col}: {header_display}={v1} | {noheader_display}={v2}")
            if diffs:
                mismatch_rows_cash.append({
                    'Headerfile_Row(file1)': i1+2,
                    'Withoutheaderfile_Row(file2)': i2+2,
                    'ClientID': client,
                    'Mismatch': 'Values differ',
                    'Differences': "; ".join(diffs)
                })
                used.add(i2)
                matched = True
                break
        if not matched:
            mismatch_rows_cash.append({
                'Headerfile_Row(file1)': i1+2,
                'Withoutheaderfile_Row(file2)': '',
                'ClientID': client,
                'Mismatch': 'Record not exist in Cash File',
                'Differences': ''
            })

    # remaining in file2 not used
    for i2,r2 in grp2.iterrows():
        if i2 not in used:
            mismatch_rows_cash.append({
                'Headerfile_Row(file1)': '',
                'Withoutheaderfile_Row(file2)': i2+2,
                'ClientID': client,
                'Mismatch': 'Record not exist in PositionBook',
                'Differences': ''
            })

# save excel
cash_output = os.path.join(folder_path, f"Mismatched_MTFS_KAMBALA_{today_str}.xlsx")
df_out_cash = pd.DataFrame(mismatch_rows_cash)

# Rename headers same as NFO/BFO output
header_display = "PositionBook"
noheader_display = clean_name(cash_txt)

df_out_cash.rename(columns={
    'Headerfile_Row(file1)': f"Mismatched Row in {header_display}",
    'Withoutheaderfile_Row(file2)': f"Mismatched Row in {noheader_display} File"
}, inplace=True)

# Normalize mismatch message
df_out_cash['Mismatch'] = df_out_cash['Mismatch'].replace({
    'Record not exist in Cash File': f"Record not exist in {noheader_display} File",
    'Record not exist in PositionBook': f"Record not exist in {header_display} File"
})

# Add Identification by Auto column (same logic as NFO/BFO)
df_out_cash["Identification by Auto"] = "Virtual ID"

if risk_file:
    df_risk = pd.read_csv(risk_file, dtype=str) if risk_file.endswith('.csv') else pd.read_excel(risk_file, dtype=str)
    df_risk.columns = df_risk.columns.str.strip().str.upper()

    if "ACCOUNTID" in df_risk.columns:
        risk_ids = set(df_risk["ACCOUNTID"].astype(str).str.strip().str.lower())

        df_out_cash["Identification by Auto"] = df_out_cash["ClientID"].astype(str).str.strip().str.lower().apply(
            lambda cid: "Exists in Risk Report" if cid in risk_ids else "Virtual ID"
        )
    else:
        print("⚠ ACCOUNTID column missing in Risk Report")
else:
    print("⚠ No RISK_REPORT file found")



if df_out_cash.empty:
    print("✅ No CASH mismatch found!")
    df_out_cash = pd.DataFrame([{"Message":"No mismatch found"}])
else:
    print("⚠️ CASH mismatches found")

df_out_cash.to_excel(cash_output, index=False)
wb = load_workbook(cash_output); ws = wb.active
for col in ws.columns:
    ml = max(len(str(c.value)) if c.value else 0 for c in col)
    ws.column_dimensions[col[0].column_letter].width = ml+5
wb.save(cash_output)

print(f"✅ MTF comparison done → {os.path.basename(cash_output)}")






import shutil
from email.message import EmailMessage
import smtplib
import os
import re

# ============================================================
# 🔍 Extract date from the TXT file names (e.g. _11022025.txt)
# ============================================================

def extract_date_from_filename(filename):
    m = re.search(r'(\d{8})', filename)
    if m:
        d = m.group(1)
        return f"{d[0:2]}-{d[2:4]}-{d[4:8]}"
    return today_str  # fallback

# pick date from FO file (or BSEF/CASH if needed)
date_from_file = extract_date_from_filename(os.path.basename(fo_txt))

# Backup folder base path
backup_base = r"C:\FILES CHECKING POSITIONBOOK\BACKUP FILES AUTOMATED"

# Today's subfolder inside backup path (based on file date)
backup_folder = os.path.join(backup_base, date_from_file)
os.makedirs(backup_folder, exist_ok=True)

print(f"📁 Backup folder created: {backup_folder}")

# ============================================================
# 📎 EMAIL SENDING – attach all 3 mismatch files
# ============================================================

smtp_server = "smtp3.netcore.co.in"
smtp_port = 587
sender_email = "kartavya.dhillon@mastertrust.co.in"
password = "$Kartavya25*"

to_emails = ["kaushik.jana@mastertrust.co.in"]      # FILES CHECK POSITIONBOOK
cc_emails = ["rms_del@mastertrust.co.in","rms_chd@mastertrust.co.in"]

subject = f"Reconciliation of Positions - NFO, BFO, MTFS"
body = f"""\
Hi Team,

Please find attached the mismatched files for {date_from_file}.

Regards,
Kartavya Dhillon
Master Capital Services Ltd
📞 011-66331000 | Ext. 84
"""

msg = EmailMessage()
msg['From'] = sender_email
msg['To'] = ", ".join(to_emails)
msg['CC'] = ", ".join(cc_emails)
msg['Subject'] = subject
msg.set_content(body)

# Attach the three mismatch files
attachments = [file_out_nfo, file_out_bfo, cash_output]

for f in attachments:
    if os.path.exists(f):
        with open(f, "rb") as ff:
            msg.add_attachment(
                ff.read(),
                maintype='application',
                subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                filename=os.path.basename(f)
            )
        print(f"📎 Attached: {os.path.basename(f)}")
    else:
        print(f"⚠️ Attachment missing: {f}")

try:
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(sender_email, password)
        server.send_message(msg)
    print("✅ Email sent successfully!")
except Exception as e:
    print(f"⚠️ Email failed: {e}")

# ============================================================
# 🗃️ BACKUP FILES BEFORE DELETE
# ============================================================

files_to_backup = [
    fo_txt, bsef_txt, cash_txt,
    position_book_path,
    file_out_nfo, file_out_bfo, cash_output,
    risk_file
]

for f in files_to_backup:
    if f and os.path.exists(f):
        try:
            shutil.copy(f, backup_folder)
            print(f"✅ Backup copied: {os.path.basename(f)}")
        except Exception as e:
            print(f"⚠️ Backup failed for {f}: {e}")

print("✅ Backup completed")

# ============================================================
# 🧹 DELETE everything except .py files
# ============================================================

for f in os.listdir(folder_path):
    full = os.path.join(folder_path, f)
    if os.path.isfile(full) and not (f.lower().endswith(".py") or f.lower().endswith(".bat")):

        try:
            os.remove(full)
            print(f"🗑️ Deleted: {f}")
        except:
            print(f"⚠️ Could not delete: {f}")

print("✅ Cleanup completed — .py and .bat files preserved")


