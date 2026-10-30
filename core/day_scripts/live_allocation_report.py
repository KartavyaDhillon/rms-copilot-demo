import pandas as pd
import os
import re
import sys
import shutil
from datetime import datetime
from openpyxl import Workbook, load_workbook
from openpyxl.utils.dataframe import dataframe_to_rows
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)


import warnings


# --- Suppress openpyxl UserWarnings ---
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# --- Base folder ---
base_folder = r"C:\LIVE ALLOCATION"

# --- Downstream folder ---
# Report_Live_Allocation.xlsx is copied here right after it's built (and
# before the backup/cleanup step below deletes the original from
# base_folder), so MCX_INTRSAR_final.py finds it automatically - same idea
# as Master_File_final.py copying its Master file forward.
MCX_INTRA_FOLDER = r"C:\MCX_INTRA_SAR"


print("\n🔍 Checking Master and Risk files before processing...\n")

# --- Check Master File ---
master_files = [f for f in os.listdir(base_folder) if f.endswith("_MASTER.xlsx")]
if not master_files:
    print("❌ No Master file found in folder.")
    print("⛔ Process stopped. Please place *_MASTER.xlsx in base folder and run again.")
    sys.exit(1)
else:
    print(f"✅ Master file found: {max(master_files)}")

# --- Check Risk File ---
risk_files = [f for f in os.listdir(base_folder) if f.startswith("RISK_REPORT") and f.endswith(".csv")]
if not risk_files:
    print("❌ No Risk Report file found in folder.")
    print("⛔ Process stopped. Please place RISK_REPORT_*.csv in base folder and run again.")
    sys.exit(1)
else:
    print(f"✅ Risk Report found: {max(risk_files)}")



print("\n🔍 Pre-checking all folders before processing...\n")

missing_check = []

for i in range(1, 9):
    folder = os.path.join(base_folder, str(i))
    peak = f"{i:02d}"

    if not os.path.exists(folder):
        missing_check.append((i, "Folder Missing"))
        continue

    # Expected Files Checks
    cm = any(f.startswith("CM_ClientLevelCollaterals") and f.lower().endswith(('.txt', '.csv')) for f in os.listdir(folder))
    fo = any(f.startswith("FO_ClientLevelCollaterals") and f.lower().endswith(('.txt', '.csv')) for f in os.listdir(folder))
    mcx = any(f.startswith(f"MCX_Web_Allocation_{peak}") for f in os.listdir(folder))
    ncdex = any(f.startswith("ClientNetEffectiveDeposits_") and f.endswith(".csv") for f in os.listdir(folder))

    if not cm:
        missing_check.append((i, "CM_ClientLevelCollaterals"))
    if not fo:
        missing_check.append((i, "FO_ClientLevelCollaterals"))
    if not mcx:
        missing_check.append((i, f"MCX_Web_Allocation_{peak}"))
    if not ncdex:
        missing_check.append((i, "ClientNetEffectiveDeposits"))

# If any missing, stop execution here
if missing_check:
    print("❌ The following required files are missing:\n")
    for folder_num, file_name in missing_check:
        print(f"   - Folder {folder_num}: {file_name}")
    print("\n⛔ Process stopped. Please place missing files and run again.")
    sys.exit(1)

print("✅ All required files are present. Processing will now begin...\n")









# --- Track missing files per folder for email ---
missing_files_dict = {}  # folder path -> list of missing files


# --- Output merged DataFrame ---
merged_df_base = pd.DataFrame()


# --- Final column order ---
final_columns = [
    'File Date', 'Peak Files Number', 'Zone', 'Region', 'Branch',
    'ACCOUNTID', 'NAME', 'CM', 'FM', 'MCX', 'NCDEX',
    'Cash_Allocated_CM', 'NCL_CM', 'Total Deposit CM (L + M)',
    'Cash_Allocated_FO', 'NCL_FO', 'Total Deposit FO (O + P)',
    'Cash_Allocated_MCX', 'NCL_MCX', 'Total_Deposit_MCX (R + S)',
    'Cash_Allocated_NCDEX','NCL_NCDEX', 'Total_Deposit_NCDEX (U + V)',
    'Total_Exchange_Peak (H+I+J+K)', 'Total_Deposit (N+Q+T+W)','Net_Shortage (Y-X)'
]

# --- Function to detect MCX header dynamically ---
def read_mcx_dynamic(file_path):
    for skip_rows in range(10):
        df_try = pd.read_excel(file_path, skiprows=skip_rows, nrows=1)
        if 'UCC Code' in df_try.columns:
            df = pd.read_excel(file_path, skiprows=skip_rows, dtype=str)
            df.columns = df.columns.str.strip()
            return df
    print(f"⚠️ Could not find header in first 10 rows for {file_path}")
    return pd.DataFrame()

# --- Function to convert CM/FO txt/csv to Excel with numeric client codes ---
def save_client_codes_numeric_xlsx(df, path, client_col='Client Code', leading_zeros=9):
    wb = Workbook()
    ws = wb.active
    for r_idx, row in enumerate(dataframe_to_rows(df, index=False, header=True), 1):
        for c_idx, value in enumerate(row, 1):
            cell = ws.cell(row=r_idx, column=c_idx, value=value)
            if r_idx == 1 and value == client_col:
                client_col_idx = c_idx
    for row in ws.iter_rows(min_row=2, min_col=client_col_idx, max_col=client_col_idx):
      for cell in row:
         if cell.value is not None:
            cell.value = str(cell.value).strip()  # Keep the code exactly as in source
            cell.number_format = '@'  # Force text format

    wb.save(path)

# --- Track processed/failed folders ---
processed_folders = []
failed_folders = []


# --- Loop over folders 1-8 ---
for i in range(1, 9):
    folder_i = os.path.join(base_folder, str(i))
    peak_number = f"{i:02d}"


    # --- Track missing files for this folder ---
    missing_files = []


    print(f"\n📂 Starting processing folder {i}: {folder_i}")

    if not os.path.exists(folder_i):
        print(f"⚠️ Folder {folder_i} not found — skipping")
        continue

    try:
        # --- Master file in base folder ---
        master_files = [f for f in os.listdir(base_folder) if f.endswith("_MASTER.xlsx")]
        if not master_files:
           print("❌ No Master file found — stopping the process")
           sys.exit(1)  # <-- stops the script immediately
        latest_master = max(master_files, key=lambda f: datetime.strptime(re.search(r'(\d{8})_MASTER\.xlsx', f).group(1), "%d%m%Y"))
        master_df = pd.read_excel(os.path.join(base_folder, latest_master), dtype=str)
        master_df['ClntId'] = master_df['ClntId'].str.strip()
        file_date = datetime.strptime(re.search(r'(\d{8})_MASTER\.xlsx', latest_master).group(1), "%d%m%Y").strftime("%d-%m-%Y")

        # --- Risk Report file in base folder ---
        risk_files = [f for f in os.listdir(base_folder) if f.startswith("RISK_REPORT") and f.endswith(".csv")]
        if not risk_files:
            print("❌ No Risk Report file found — stopping the process")
            sys.exit(1)  # <-- stops the script immediately
        latest_risk = max(risk_files, key=lambda f: datetime.strptime(re.search(r'RISK_REPORT_(\d{8})\.csv', f).group(1), "%d%m%Y"))
        risk_df = pd.read_csv(os.path.join(base_folder, latest_risk), dtype={'ACCOUNTID': str}, low_memory=False)
        risk_df['ACCOUNTID'] = risk_df['ACCOUNTID'].str.strip()
        risk_df['File Date'] = file_date
        risk_df['Peak Files Number'] = peak_number


        # --- Merge Master & Risk ---
        def clean_code(code):
            if pd.isna(code):
               return ''
            code = str(code).strip().upper().replace('*','')
            return code  # remove int conversion, keep as string


        master_df['ClntId_clean'] = master_df['ClntId'].apply(clean_code)
        risk_df['ACCOUNTID_clean'] = risk_df['ACCOUNTID'].apply(clean_code)
        merged_df = pd.merge(master_df, risk_df, left_on='ClntId_clean', right_on='ACCOUNTID_clean', how='left')
        merged_df['ACCOUNTID'] = merged_df['ClntId']
        merged_df.drop(columns=['ClntId_clean','ACCOUNTID_clean'], inplace=True, errors='ignore')

        # --- Pick dynamic CM/FM/MCX/NCDEX from Master ---
        peak_number_int = i
        for col_prefix in ['CM','FM','MCX','NCDEX']:
            master_col = f"{col_prefix}{peak_number_int}"  # e.g., CM2 if folder 2
            if master_col in merged_df.columns:
                merged_df[col_prefix] = pd.to_numeric(merged_df[master_col], errors='coerce').fillna(0)
            else:
                merged_df[col_prefix] = 0

        # --- CM / FO files ---
        cm_files = [f for f in os.listdir(folder_i) if f.startswith("CM_ClientLevelCollaterals") and f.lower().endswith(('.txt','.csv'))]
        fo_files = [f for f in os.listdir(folder_i) if f.startswith("FO_ClientLevelCollaterals") and f.lower().endswith(('.txt','.csv'))]

        if not cm_files:
           missing_files.append("CM_ClientLevelCollaterals")
        if not fo_files:
           missing_files.append("FO_ClientLevelCollaterals")




        cm_df = pd.read_csv(os.path.join(folder_i, cm_files[0]), dtype=str) if cm_files else pd.DataFrame()
        if not cm_df.empty:
           cm_df['Client Code'] = cm_df['Client Code'].str.strip()

        fo_df = pd.read_csv(os.path.join(folder_i, fo_files[0]), dtype=str) if fo_files else pd.DataFrame()
        if not fo_df.empty:
           fo_df['Client Code'] = fo_df['Client Code'].str.strip()

        if not cm_df.empty:
            save_client_codes_numeric_xlsx(cm_df, os.path.join(folder_i,"CM_ClientLevelCollaterals.xlsx"))
            cm_df = pd.read_excel(os.path.join(folder_i,"CM_ClientLevelCollaterals.xlsx"), dtype={'Client Code': str})
            cm_df['Client Code'] = cm_df['Client Code'].str.strip()
            cm_df.columns = cm_df.columns.str.strip().str.replace('"','')
            cm_df['NCL_CM'] = cm_df['Cash-Others (c)'] + cm_df['Non-cash Securities (d)']
            cm_df.rename(columns={'Cash allocated (b)':'Cash_Allocated_CM'}, inplace=True)
            cm_df = cm_df[['Client Code','Cash_Allocated_CM','NCL_CM']]
            merged_df = pd.merge(merged_df, cm_df, left_on='ACCOUNTID', right_on='Client Code', how='left').drop(columns=['Client Code'], errors='ignore')
        else:
            merged_df['Cash_Allocated_CM'] = merged_df['NCL_CM'] = 0

        if not fo_df.empty:
            save_client_codes_numeric_xlsx(fo_df, os.path.join(folder_i,"FO_ClientLevelCollaterals.xlsx"))
            fo_df = pd.read_excel(os.path.join(folder_i,"FO_ClientLevelCollaterals.xlsx"), dtype={'Client Code': str})
            fo_df['Client Code'] = fo_df['Client Code'].str.strip()
            fo_df.columns = fo_df.columns.str.strip().str.replace('"','')
            fo_df['NCL_FO'] = fo_df['Cash-others (c)'] + fo_df['Non-cash Securities (d)']
            fo_df.rename(columns={'Cash allocated (b)':'Cash_Allocated_FO'}, inplace=True)
            fo_df = fo_df[['Client Code','Cash_Allocated_FO','NCL_FO']]
            merged_df = pd.merge(merged_df, fo_df, left_on='ACCOUNTID', right_on='Client Code', how='left').drop(columns=['Client Code'], errors='ignore')
        else:
            merged_df['Cash_Allocated_FO'] = merged_df['NCL_FO'] = 0

        # --- MCX ---
        mcx_file = [f for f in os.listdir(folder_i) if f.startswith(f"MCX_Web_Allocation_{peak_number}")]
        mcx_df = read_mcx_dynamic(os.path.join(folder_i, mcx_file[0])) if mcx_file else pd.DataFrame()
        
        if not mcx_file:
           missing_files.append(f"MCX_Web_Allocation_{peak_number}")


        if not mcx_df.empty:
            mcx_df['UCC Code'] = mcx_df['UCC Code'].astype(str).str.strip().str.replace('"','')
            merged_df['ACCOUNTID'] = merged_df['ACCOUNTID'].astype(str).str.strip()

            for col in ['Allocation at EOD','Own Collateral EOD','Own Others Collateral at EOD']:
                if col in mcx_df.columns:
                    mcx_df[col] = pd.to_numeric(mcx_df[col].astype(str).str.replace(',','').str.strip(), errors='coerce').fillna(0)
            mcx_df['Cash_Allocated_MCX'] = mcx_df['Allocation at EOD']
            mcx_df['NCL_MCX'] = mcx_df['Own Collateral EOD'] + mcx_df['Own Others Collateral at EOD']
            mcx_df = mcx_df[['UCC Code','Cash_Allocated_MCX','NCL_MCX']]
            merged_df = pd.merge(merged_df, mcx_df, left_on='ACCOUNTID', right_on='UCC Code', how='left').drop(columns=['UCC Code'], errors='ignore')
        else:
            merged_df['Cash_Allocated_MCX'] = merged_df['NCL_MCX'] = 0

        # --- NCDEX ---
        ncx_files = [f for f in os.listdir(folder_i) if f.startswith("ClientNetEffectiveDeposits_") and f.endswith(".csv")]
        if not ncx_files:
            missing_files.append("ClientNetEffectiveDeposits")




        if ncx_files:
            def extract_date_ncdex(filename):
                match = re.search(r'ClientNetEffectiveDeposits_(\d{8})\.csv', filename)
                return datetime.strptime(match.group(1), "%d%m%Y") if match else datetime.min

            latest_ncx_file = max(ncx_files, key=extract_date_ncdex)
            ncdex_df = pd.read_csv(os.path.join(folder_i, latest_ncx_file), low_memory=False, dtype=str)
            ncdex_df.columns = ncdex_df.columns.str.strip()
            
            ncdex_df['Client/CP Code'] = ncdex_df['Client/CP Code'].astype(str).str.strip().str.replace('"','')
            merged_df['ACCOUNTID'] = merged_df['ACCOUNTID'].astype(str).str.strip()


            required_cols = ['Client/CP Code', 'Cash Equivalent', 'Securities Cash Component', 'Total Non Cash']
            if all(col in ncdex_df.columns for col in required_cols):
                ncdex_df['Client/CP Code'] = ncdex_df['Client/CP Code'].astype(str).str.strip()
                for col in ['Cash Equivalent', 'Securities Cash Component', 'Total Non Cash']:
                    ncdex_df[col] = pd.to_numeric(ncdex_df[col].astype(str).str.replace(',', '').str.strip(), errors='coerce').fillna(0)
                ncdex_df['NCL_NCDEX'] = ncdex_df['Securities Cash Component'] + ncdex_df['Total Non Cash']
                merged_df = pd.merge(
                    merged_df,
                    ncdex_df[['Client/CP Code', 'Cash Equivalent', 'NCL_NCDEX']],
                    left_on='ACCOUNTID',
                    right_on='Client/CP Code',
                    how='left'
                )
                merged_df.rename(columns={'Cash Equivalent': 'Cash_Allocated_NCDEX'}, inplace=True)
                merged_df.drop(columns=['Client/CP Code'], inplace=True, errors='ignore')
                merged_df['Cash_Allocated_NCDEX'] = merged_df['Cash_Allocated_NCDEX'].fillna(0)
                merged_df['NCL_NCDEX'] = merged_df['NCL_NCDEX'].fillna(0)
            else:
                merged_df['Cash_Allocated_NCDEX'] = merged_df['NCL_NCDEX'] = 0
        else:
            merged_df['Cash_Allocated_NCDEX'] = merged_df['NCL_NCDEX'] = 0

        # --- Total Deposit & Net Shortage Calculations ---
        merged_df['Total Deposit CM (L + M)'] = merged_df['Cash_Allocated_CM'].fillna(0) + merged_df['NCL_CM'].fillna(0)
        merged_df['Total Deposit FO (O + P)'] = merged_df['Cash_Allocated_FO'].fillna(0) + merged_df['NCL_FO'].fillna(0)
        merged_df['Total_Deposit_MCX (R + S)'] = merged_df['Cash_Allocated_MCX'].fillna(0) + merged_df['NCL_MCX'].fillna(0)
        merged_df['Total_Deposit_NCDEX (U + V)'] = merged_df['Cash_Allocated_NCDEX'].fillna(0) + merged_df['NCL_NCDEX'].fillna(0)

        merged_df['Total_Exchange_Peak (H+I+J+K)'] = merged_df['CM'] + merged_df['FM'] + merged_df['MCX'] + merged_df['NCDEX']
        merged_df['Total_Deposit (N+Q+T+W)'] = merged_df['Total Deposit CM (L + M)'] + merged_df['Total Deposit FO (O + P)'] + merged_df['Total_Deposit_MCX (R + S)'] + merged_df['Total_Deposit_NCDEX (U + V)']
        merged_df['Net_Shortage (Y-X)'] = merged_df['Total_Deposit (N+Q+T+W)'] - merged_df['Total_Exchange_Peak (H+I+J+K)']

        # --- Fill all remaining blanks with 0 ---
        merged_df = merged_df.fillna(0).infer_objects()

        # --- Append to base merged df ---
        merged_df_base = pd.concat([merged_df_base, merged_df], ignore_index=True)


        # --- Save missing files info ---
        if missing_files:
            missing_files_dict[folder_i] = missing_files

        processed_folders.append(i)
        print(f"✅ Folder {i} processed successfully.")

    except Exception as e:
        failed_folders.append(i)
        print(f"❌ Error processing folder {i}: {e}")
        print("\n⛔ Process stopped because a folder failed. No further steps executed.\n")
        sys.exit(1)

# --- Keep only final columns and save ---
merged_df_base = merged_df_base.reindex(columns=final_columns)
output_file = os.path.join(base_folder, "Report_Live_Allocation.xlsx")
merged_df_base.to_excel(output_file, index=False)

# --- Adjust column widths ---
wb = load_workbook(output_file)
ws = wb.active
for col in ws.columns:
    max_length = max(len(str(c.value)) if c.value else 0 for c in col)
    ws.column_dimensions[col[0].column_letter].width = max_length + 3
wb.save(output_file)

print(f"✅ Report_Live_Allocation created successfully.")

# --- COPY TO MCX_INTRA_SAR so MCX_INTRSAR_final.py can find it ---
# Must happen here, BEFORE the backup/cleanup step further down deletes the
# original from base_folder.
try:
    os.makedirs(MCX_INTRA_FOLDER, exist_ok=True)
    shutil.copy2(output_file, os.path.join(MCX_INTRA_FOLDER, os.path.basename(output_file)))
    print(f"📤 Report_Live_Allocation copied to: {os.path.join(MCX_INTRA_FOLDER, os.path.basename(output_file))}")
except Exception as e:
    print(f"❌ Could not copy Report_Live_Allocation to {MCX_INTRA_FOLDER}: {e}")


# --- CREATE PEAKWISE FILE ---
peakwise_file = os.path.join(base_folder, "PEAKWISE_LIVE_ALLOCATION.xlsx")

with pd.ExcelWriter(peakwise_file, engine='openpyxl') as writer:
    for i in range(1, 9):
        peak = f"{i:02d}"
        
        # Filter data for that peak
        df_peak = merged_df_base[
            merged_df_base['Peak Files Number'] == peak
        ]
        
        # Write to sheet
        df_peak.to_excel(writer, sheet_name=f"Peak {peak}", index=False)

# --- AUTO ADJUST COLUMN WIDTHS (for all sheets) ---
wb_peak = load_workbook(peakwise_file)

for sheet in wb_peak.worksheets:
    for col in sheet.columns:
        max_length = 0
        column = col[0].column_letter
        
        for cell in col:
            try:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            except:
                pass
        
        sheet.column_dimensions[column].width = max_length + 3

wb_peak.save(peakwise_file)

print(f"✅ Peakwise file created successfully at: {peakwise_file}")




# --- Final summary ---
print("\n📊 Processing Summary:")
print(f"Processed folders: {processed_folders}")
print(f"Failed folders: {failed_folders}")
print(f"\n✅ Merged file created successfully at: {output_file}")




import smtplib
from email.message import EmailMessage
from email.utils import formataddr
import os

# --- Email settings ---
smtp_server = "smtp3.netcore.co.in"
smtp_port = 587
sender_email = "kartavya.dhillon@mastertrust.co.in"
password = ""
receiver_email =  "rms_del@mastertrust.co.in"
cc_email = ""  # empty for now

# --- Pick the File Date from merged report in readable format ---
if not merged_df_base.empty and 'File Date' in merged_df_base.columns:
    # Assuming File Date is like '28-10-2025'
    raw_date = merged_df_base['File Date'].iloc[0]
    try:
        date_obj = datetime.strptime(raw_date, "%d-%m-%Y")
        email_date = date_obj.strftime("%d %b %Y")  # e.g., 17 Oct 2025
    except:
        email_date = raw_date
else:
    email_date = datetime.now().strftime("%d %b %Y")

# --- Compose email ---
msg = EmailMessage()
msg['From'] = formataddr(("Kartavya Dhillon", sender_email))
msg['To'] = receiver_email
msg['Cc'] = cc_email
msg['Subject'] = "Live Allocation Data"

body = f"""<p>Dear Rajeev/Ashutosh,<br><br>

Please find the live allocation data of {email_date}.</p> 

"""

# --- Add missing files info to email body ---
if missing_files_dict:
    body += "<p><b>⚠️ Note:</b> The above data is without the following missing files:<br>"
    for folder, files in missing_files_dict.items():
        folder_name = os.path.basename(folder)
        body += f"Folder {folder_name}: {', '.join(files)}<br>"
    body += "</p>"

body += """<p>Warm regards,<br>
Kartavya Dhillon<br>
Master Capital Services Ltd<br>
📞 011-66331000 | Ext. 84</p>
"""

msg.set_content(body, subtype='html')

# --- Attach Report_Live_Allocation ---
with open(output_file, 'rb') as f:
    msg.add_attachment(
        f.read(),
        maintype='application',
        subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        filename=os.path.basename(output_file)
    )

# --- Attach PEAKWISE_LIVE_ALLOCATION ---
with open(peakwise_file, 'rb') as f:
    msg.add_attachment(
        f.read(),
        maintype='application',
        subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        filename=os.path.basename(peakwise_file)
    )


import time
import smtplib

# Sending email
print("📨 Starting email send with infinite retry (3 sec delay)...")

while True:
    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(sender_email, password)
            server.send_message(msg)

        print(f"✅ Email sent successfully to {receiver_email}")
        break  # EXIT LOOP ONLY AFTER SUCCESS

    except smtplib.SMTPException as e:
        print(f"⚠️ SMTP error: {e}")
        print("🔁 Retrying in 3 seconds...\n")
        time.sleep(3)

    except Exception as e:
        print(f"⚠️ General error: {e}")
        print("🔁 Retrying in 3 seconds...\n")
        time.sleep(3)




# --- IGNORE: skip .py files, .bat files, and all subfolders during backup ---
def ignore_files_and_subfolders(folder, items):
    ignored = []
    for item in items:
        item_path = os.path.join(folder, item)

        # Skip .py and .bat files
        if item.lower().endswith((".py", ".bat")):
            ignored.append(item)
            continue

        # Skip ANY subfolders (do not copy inside folders 1-8)
        if os.path.isdir(item_path):
            ignored.append(item)
            continue

    return ignored


import shutil

# --- CREATE BACKUP FOLDER WITH DATE ---
backup_base = os.path.join(base_folder, "BACKUP FILES")

# folder named as the report date, example: 30-10-2025
backup_date_folder = os.path.join(backup_base, file_date)

if not os.path.exists(backup_date_folder):
    os.makedirs(backup_date_folder)

print(f"🗂 Creating backup in: {backup_date_folder}")

# --- COPY FINAL REPORT ---
shutil.copy(output_file, backup_date_folder)

# --- COPY PEAKWISE FILE ---
shutil.copy(peakwise_file, backup_date_folder)

# --- COPY MASTER FILE USED ---
shutil.copy(os.path.join(base_folder, latest_master), backup_date_folder)

# --- COPY RISK FILE USED ---
shutil.copy(os.path.join(base_folder, latest_risk), backup_date_folder)

# --- COPY FOLDERS 1 TO 8 (skip .py, .bat and subfolders) ---
for i in range(1, 9):
    source_folder = os.path.join(base_folder, str(i))
    dest_folder = os.path.join(backup_date_folder, str(i))

    if os.path.exists(source_folder):
        shutil.copytree(source_folder, dest_folder, ignore=ignore_files_and_subfolders)

# --- AFTER SUCCESSFUL BACKUP → DELETE ORIGINAL FILES & FOLDERS ---

# Delete Final Report
os.remove(output_file)

# Delete Peakwise File
os.remove(peakwise_file)

# Delete MASTER and RISK
os.remove(os.path.join(base_folder, latest_master))
os.remove(os.path.join(base_folder, latest_risk))

# --- DELETE ONLY NON-PY, NON-BAT FILES (do not delete subfolders) ---
for i in range(1, 9):
    folder_to_clean = os.path.join(base_folder, str(i))

    if not os.path.exists(folder_to_clean):
        continue

    # Delete top-level files except .py and .bat
    for item in os.listdir(folder_to_clean):
        item_path = os.path.join(folder_to_clean, item)

        # Skip .py and .bat files
        if item.lower().endswith((".py", ".bat")):
            continue

        # Skip subfolders completely
        if os.path.isdir(item_path):
            continue

        # Delete allowed files
        try:
            os.remove(item_path)
        except Exception as e:
            print(f"⚠️ Could not delete {item}: {e}")

    # Remove folder only if empty after cleanup
    try:
        os.rmdir(folder_to_clean)
    except:
        pass  # folder still has .bat or .py or subfolders → keep it

print("✅ Backup Completed & Source Files Deleted Successfully.")

