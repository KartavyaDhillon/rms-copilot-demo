"""
concentration.py  —  Combined concentration pipeline
=====================================================
Combines three scripts in order:
  1. INDEX_FILE_DWONLOAD.py       → downloads Nifty50 / BSE200 / BSE500 index files
  2. KORP_FILE_MTFCRR_...py       → downloads the SHORTAGE file (MTF CRR Report 5)
  3. CONCENTRATION_FINAL.py       → computes violations, emails zone-wise reports, backs up

The ONLY changes from the originals:
  • Folder paths      → CONCENTRATION_FOLDER   (.env)
  • KORP credentials  → KORP_USERNAME, KORP_PASSWORD, KORP_CLIENT_IP,
                        KORP_CLIENT_MAC, KORP_FIRM_ID, KORP_BASE_URL  (.env)
  • SMTP credentials  → SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD  (.env)
  • exit()            → return {...}  (can't call exit() inside a web-server function)
  • Everything else is BYTE-FOR-BYTE identical to your originals.
"""
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from openpyxl import load_workbook
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
import base64
import glob
import os
import pandas as pd
import polars as pl
import requests
import shutil
import smtplib
import time
from dotenv import load_dotenv

load_dotenv()

# ---- Folder (shared by all 3 scripts) ----
CONCENTRATION_FOLDER = os.getenv("CONCENTRATION_FOLDER", r"D:\CONCENTRATION")

# ---- KORP credentials ----
KORP_BASE_URL  = os.getenv("KORP_BASE_URL", "https://boapi.mastertrust.co.in")   # main address; the spare is tried by core.korp_api
KORP_USERNAME  = os.getenv("KORP_USERNAME")
KORP_PASSWORD  = os.getenv("KORP_PASSWORD")
KORP_CLIENT_IP = os.getenv("KORP_CLIENT_IP")
KORP_CLIENT_MAC= os.getenv("KORP_CLIENT_MAC")
KORP_FIRM_ID   = os.getenv("KORP_FIRM_ID", "1001")

# ---- Concentration email recipients (change in .env, no code changes needed) ----
def _e(key):
    val = os.getenv(key, "")
    return [x.strip() for x in val.split(",") if x.strip()]

CONC_FULL_TO    = _e("CONC_FULL_TO")
CONC_FULL_CC    = _e("CONC_FULL_CC")
CONC_ZODELH_TO  = _e("CONC_ZODELH_TO")
CONC_ZODELH_CC  = _e("CONC_ZODELH_CC")
CONC_ZOLUDH_TO  = _e("CONC_ZOLUDH_TO")
CONC_ZOLUDH_CC  = _e("CONC_ZOLUDH_CC")
CONC_ZOCHND_TO  = _e("CONC_ZOCHND_TO")
CONC_ZOCHND_CC  = _e("CONC_ZOCHND_CC")
CONC_ZOMUMB_TO  = _e("CONC_ZOMUMB_TO")
CONC_ZOMUMB_CC  = _e("CONC_ZOMUMB_CC")

def download_concentration_files():
    """
    STEP 1 — Download all input files to CONCENTRATION_FOLDER:
      • INDEX_FILE_DWONLOAD.py  → Nifty50 (NSE), BSE200 & BSE500 (BSE) via Selenium
      • KORP_FILE_...py          → MTF CRR Report 5 (shortage file) via KORP API
    Safe to run independently; sends nothing, just downloads.
    """
    # ======================================================================
    # PART 1 — INDEX FILE DOWNLOAD (INDEX_FILE_DWONLOAD.py)
    # ======================================================================

    # ============================
    # DOWNLOAD FOLDER
    # ============================
    DOWNLOAD_DIR = CONCENTRATION_FOLDER

    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)

    # ============================
    # FILE CHECK FUNCTION
    # ============================
    def file_exists(keyword):
        files = os.listdir(DOWNLOAD_DIR)
        return any(keyword.lower() in f.lower() for f in files)

    # ============================
    # CHROME OPTIONS
    # ============================
    options = webdriver.ChromeOptions()

    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)

    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    wait = WebDriverWait(driver, 20)

    # ==========================================================
    # 1️⃣ DOWNLOAD NSE NIFTY 50 INDEX CONSTITUENT
    # ==========================================================
    if file_exists("NIFTY"):
        print("NSE file already exists. Skipping NSE download ✅")
    else:
        print("Downloading NSE NIFTY 50 file...")

        driver.get("https://www.nseindia.com/index-tracker/NIFTY%2050")

        time.sleep(3)

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        download_btn = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//img[@aria-label='Download Index Constituent']")
            )
        )

        driver.execute_script("arguments[0].click();", download_btn)

        time.sleep(3)

        print("NSE File Downloaded ✅")


    def wait_and_rename(expected_old_names, new_name, timeout=15):
        """
        Waits for file download and renames it safely
        """

        end_time = time.time() + timeout

        while time.time() < end_time:

            files = os.listdir(DOWNLOAD_DIR)

            for f in files:

                # Skip downloading files
                if f.endswith(".crdownload"):
                    continue

                old_path = os.path.join(DOWNLOAD_DIR, f)
                new_path = os.path.join(DOWNLOAD_DIR, new_name)

                # ✅ If file already has desired name
                if f.lower() == new_name.lower():
                    print(f"{new_name} already correct. Skipping rename ✅")
                    return True

                # ✅ Rename only if old keyword matched
                if any(name.lower() in f.lower() for name in expected_old_names):

                    os.rename(old_path, new_path)

                    print(f"Renamed → {new_name} ✅")
                    return True

            time.sleep(1)

        print(f"⚠️ Rename failed for {new_name}")
        return False


    # ==========================================================
    # FUNCTION TO DOWNLOAD DIRECT BSE INDEX PAGE
    # ==========================================================
    def download_bse_direct(url, index_name, keyword, final_name):

        if file_exists(final_name):
            print(f"{index_name} file already exists. Skipping download ✅")
            return

        print(f"Downloading {index_name} file...")

        driver.get(url)

        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)

        download_icon = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//i[contains(@class,'fa-download')]")
            )
        )

        driver.execute_script("arguments[0].click();", download_icon)

        # 🔥 Rename immediately after download
        wait_and_rename(
            ["sensexindex_constituents"],   # what BSE is currently giving
            final_name
        )

        print(f"{index_name} File Downloaded & Renamed ✅")

    download_bse_direct(
        "https://www.bseindia.com/sensex/IndicesWatch_Weight.aspx?iname=BSE500&index_Code=17",
        "BSE 500",
        "BSE 500",
        "BSE 500index_Constituents.csv"
    )

    download_bse_direct(
        "https://www.bseindia.com/sensex/IndicesWatch_Weight.aspx?iname=BSE200&index_Code=23",
        "BSE 200",
        "BSE 200",
        "BSE 200index_Constituents.csv"
    )


    driver.quit()

    print(f"\n🎉 Process completed for {CONCENTRATION_FOLDER}")
    # ======================================================================
    # PART 2 — SHORTAGE FILE DOWNLOAD (KORP_FILE_MTFCRR_REPORTS_DAILY_MARGINS.py)
    # ======================================================================

    # ===================================================
    # CONFIGURATION
    # ===================================================

    from core import korp_api
    BASE = korp_api.pick_base()          # main address, or the spare one if the main does not answer

    USERNAME = os.getenv("KORP_USERNAME")
    PASSWORD = os.getenv("KORP_PASSWORD")

    CLIENT_IP = os.getenv("KORP_CLIENT_IP")
    CLIENT_MAC = os.getenv("KORP_CLIENT_MAC")

    FIRM_ID = os.getenv("KORP_FIRM_ID", "1001")

    SAVE_FOLDER = CONCENTRATION_FOLDER

    os.makedirs(SAVE_FOLDER, exist_ok=True)

    # ===================================================
    # AUTO DATE + FINANCIAL YEAR
    # ===================================================

    today = datetime.now()

    record_date = today.astimezone().isoformat()

    # For filename format:
    # 21052026161225_SHORTAGE.xlsx

    timestamp = today.strftime("%d%m%Y%H%M%S")

    # Financial Year Dynamic

    if today.month >= 4:
        fy_start = today.year
        fy_end = today.year + 1
    else:
        fy_start = today.year - 1
        fy_end = today.year

    FINANCIAL_YEAR = f"{fy_start}-{fy_end}"

    print("=" * 60)
    print("Today's Date:", today.strftime("%d-%m-%Y"))
    print("Financial Year:", FINANCIAL_YEAR)
    print("=" * 60)

    # ===================================================
    # CREATE SESSION
    # ===================================================

    session = requests.Session()

    # ===================================================
    # STEP 1 : SESSION KEY
    # ===================================================

    print("\nGetting Session Key...")

    try:

        r = session.get(
            f"{BASE}/api/Common/SessionKey/Get",
            timeout=30
        )

        session_key = korp_api.parse_session_key(r.text)

        print("Session Key:", session_key)

    except Exception as e:

        print("Session Key Error:", e)
        return {"status": "error_korp", "message": "KORP step failed - check terminal output"}

    # ===================================================
    # STEP 2 : PASSWORD ENCRYPTION
    # ===================================================

    print("\nEncrypting Password...")

    try:

        key = session_key.encode()
        iv = session_key.encode()

        cipher = AES.new(
            key,
            AES.MODE_CBC,
            iv
        )

        encrypted = cipher.encrypt(
            pad(PASSWORD.encode(),16)
        )

        encrypted_password = base64.b64encode(
            encrypted
        ).decode()

        print("Password Encrypted")

    except Exception as e:

        print("Encryption Error:",e)
        return {"status": "error_korp", "message": "KORP step failed - check terminal output"}

    # ===================================================
    # STEP 3 : LOGIN
    # ===================================================

    print("\nLogging In...")

    payload = {

        "CallFrom":"FINKORP",
        "UserName":USERNAME,
        "Password":encrypted_password,
        "Grant_type":"password",
        "ClientIP":CLIENT_IP,
        "ClientMAC":CLIENT_MAC
    }

    headers = {

        "Content-Type":
        "application/x-www-form-urlencoded"
    }

    try:

        login = session.post(
            f"{BASE}/api/token",
            data=payload,
            headers=headers,
            timeout=60
        )

        login_json = login.json()

        token = login_json["access_token"]

        print("Login Successful")

    except Exception as e:

        print("Login Failed")
        print(e)

        try:
            print(login.text)
        except:
            pass

        return {"status": "error_korp", "message": "KORP step failed - check terminal output"}

    # ===================================================
    # STEP 4 : DOWNLOAD REPORT
    # ===================================================

    print("\nDownloading MTFCRR Report...")

    headers = {

        "Authorization":f"Bearer {token}",
        "FIRMID":FIRM_ID,
        "FINANCIALYEAR":FINANCIAL_YEAR,
        "Content-Type":"application/json"
    }

    payload = {

        "RecordDate":record_date,

        "ReportType":"5",

        "ActReportType":None,
        "FirmID":None,
        "AccountID":None,
        "Branch":None,
        "SubBranch":None,
        "RM":None,
        "FamilyGroup":None,
        "CustomFilter":None,

        "ExportFormat":0,
        "RequestType":0,

        "EmailAndFileParam":{

            "FromID":None,
            "IsEmailRequest":False,
            "HighRequestPriority":False,
            "EmailPriority":0,
            "DigitalSign":False,
            "PasswordProtected":False,
            "IsBackgroundProcess":False,
            "BackgroundProcessToken":None
        },

        "TradeDate":None,
        "Zone":None,
        "Region":None,
        "DeleteFlag":None
    }

    try:

        response = session.post(
            f"{BASE}/api/Reports/MTFCRRReport/Post",
            headers=headers,
            json=payload,
            timeout=300
        )

        print(
            "API Status:",
            response.status_code
        )

        if response.status_code != 200:

            print(response.text)
            return {"status": "error_korp", "message": "KORP step failed - check terminal output"}

    except Exception as e:

        print("Download Error:",e)
        return {"status": "error_korp", "message": "KORP step failed - check terminal output"}

    # ===================================================
    # STEP 5 : JSON → DATAFRAME
    # ===================================================

    print("\nConverting JSON Data...")

    try:

        data = response.json()

        if not data:

            print("No Data Found")
            return {"status": "error_korp", "message": "KORP step failed - check terminal output"}

        df = pd.DataFrame(data)

        # =========================================
        # Rename Columns
        # =========================================

        df.rename(columns={

            "GroupID":"Group ID",
            "AccountID":"Account ID",
            "LeastFundingValue":"Least Funding Value",
            "IntransitFundedValue":"Intransit Funded Value",
            "MTFColletral":"MTF Colletral",
            "ApplicableMargin":"Applicable Margin",
            "NetColletral":"Net Colletral",
            "MTFLedger":"MTF Ledger",
            "NonMTFledger":"Non MTF Ledger",
            "MTFRisk":"MTF Risk",
            "NonMtfToMtf":"Non MTF To MTF",
            "MtfToNonMtf":"MTF To Non MTF"

        }, inplace=True)

        # =========================================
        # Exact Column Order
        # =========================================

        required_columns=[

            "TradeDate",
            "Zone",
            "Region",
            "Branch",
            "SubBranch",
            "Group ID",
            "Account ID",
            "Least Funding Value",
            "Intransit Funded Value",
            "MTF Colletral",
            "Applicable Margin",
            "Net Colletral",
            "MTF Ledger",
            "Non MTF Ledger",
            "MTF Risk",
            "Non MTF To MTF",
            "MTF To Non MTF"
        ]

        df=df.reindex(
            columns=required_columns
        )

        print(
            "Rows Received:",
            len(df)
        )

    except Exception as e:

        print(
            "JSON Conversion Error:",
            e
        )
        return {"status": "error_korp", "message": "KORP step failed - check terminal output"}

    # ===================================================
    # STEP 6 : SAVE EXCEL
    # ===================================================

    filename = os.path.join(
        SAVE_FOLDER,
        f"{timestamp}_SHORTAGE.xlsx"
    )

    print("\nSaving Excel...")

    try:

        with pd.ExcelWriter(
            filename,
            engine='xlsxwriter'
        ) as writer:

            df.to_excel(
                writer,
                sheet_name='Sheet1',
                index=False
            )

            worksheet=writer.sheets["Sheet1"]

            for i,col in enumerate(df.columns):

                try:

                    width=max(

                        df[col]
                        .astype(str)
                        .map(len)
                        .max(),

                        len(col)

                    )+2

                    worksheet.set_column(
                        i,
                        i,
                        width
                    )

                except:
                    pass

        print("\n"+"="*60)
        print("FILE CREATED SUCCESSFULLY")
        print(filename)
        print("="*60)

    except Exception as e:

        print(
            "Excel Save Error:",
            e
        )
    return {"status": "ok", "step": "download"}


def run_concentration_compute():
    """
    STEP 2 — Run concentration check on already-downloaded files:
      • Polars computation (violations, scrip/portfolio level)
      • Zone-wise Excel output
      • Email (per EMAIL_MAPPING in .env)
      • Backup & cleanup
    Assumes files are already in CONCENTRATION_FOLDER.
    """
    # ======================================================================
    # PART 3 — CONCENTRATION COMPUTATION + EMAIL + BACKUP (CONCENTRATION_FINAL.py)
    # ======================================================================


    # ================= CONFIG =================
    BASE_FOLDER = CONCENTRATION_FOLDER
    os.chdir(BASE_FOLDER)

    today = datetime.today().strftime("%d%m%Y")

    mtf_file   = f"MTFPOSITION_{today}.csv"
    risk_file  = f"RISK_REPORT_{today}.csv"

    # ================= PRE-FILE CHECK =================


    # Find dynamic shortage file
    shortage_files = glob.glob("*_SHORTAGE.xlsx")

    required_files = [
        mtf_file,
        risk_file,
        "ind_nifty50list.csv",
        "BSE 200index_Constituents.csv",
        "BSE 500index_Constituents.csv",
        "EXCEPTION.xlsx"
    ]

    missing_files = []

    # Check normal files
    for file in required_files:
        file_path = os.path.join(BASE_FOLDER, file)

        if not os.path.isfile(file_path):
            missing_files.append(file)

    # Check shortage file separately
    if not shortage_files:
        missing_files.append("*_SHORTAGE.xlsx")

    if missing_files:

        print("\n❌ ERROR: The following required files are missing:\n")

        for file in missing_files:
            print(f"   - {file}")

        print("\n⚠ Please place all required files in the folder and rerun the program.\n")

        return {"status": "missing_files", "missing": missing_files}

    else:

        print("✅ All required files including SHORTAGE file are present. Proceeding...\n")


    missing_files = []

    for file in required_files:
        file_path = os.path.join(BASE_FOLDER, file)
        if not os.path.isfile(file_path):
            missing_files.append(file)

    if missing_files:
        print("\n❌ ERROR: The following required files are missing:\n")
        for file in missing_files:
            print(f"   - {file}")
        print("\n⚠ Please place all required files in the folder and rerun the program.\n")
        return {"status": "missing_files", "missing": missing_files}

    else:
        print("✅ All required files are present. Proceeding...\n")



    # ================= FAMILY CONFIGURATION =================

    FAMILY_RULES = {
        "FAMILY_A": {
            "codes": ["6HDD10110", "6HDD10111","6HWD2002"],
            "portfolio_limits": {
                "ind_nifty50list.csv": 100_000_000,   # 10 Cr
                "BSE 200index_Constituents.csv": 100_000_000,
                "BSE 500index_Constituents.csv": 60_000_000,   # 6 Cr
                "Others": 30_000_000   # 3 Cr
            },
            "scrip_others_limit": 30_000_000  # 3 Cr
        }
    }






    # ================= READ MAIN FILES (SAFE) =================
    mtf_df  = pl.read_csv(mtf_file, infer_schema_length=0)
    risk_df = pl.read_csv(risk_file, infer_schema_length=0)

    # ================= BRING FIN_UPTODATE_MTF =================
    risk_df = risk_df.select([
        pl.col("ACCOUNTID"),
        pl.col("FIN_UPTODATE_MTF")
    ])

    df = mtf_df.join(
        risk_df,
        left_on="AccountID",
        right_on="ACCOUNTID",
        how="left"
    )

    # ================= PORTFOLIO VALUE =================
    df = df.with_columns(
        pl.min_horizontal(
            pl.col("MtfValue").cast(pl.Float64, strict=False),
            pl.col("MtfValueClosing").cast(pl.Float64, strict=False)
        ).alias("PORTFOLIO VALUE")
    )


    # ================= PORTFOLIO VALUE_2 (ACCOUNT LEVEL) =================

    portfolio_agg = (
        df
        .group_by("AccountID")
        .agg([
            pl.col("MtfValue")
              .cast(pl.Float64, strict=False)
              .sum()
              .alias("SUM_MtfValue"),

            pl.col("MtfValueClosing")
              .cast(pl.Float64, strict=False)
              .sum()
              .alias("SUM_MtfValueClosing"),
        ])
        .with_columns(
            pl.min_horizontal(
                pl.col("SUM_MtfValue"),
                pl.col("SUM_MtfValueClosing")
            ).alias("PORTFOLIO VALUE_2")
        )
        .select([
            "AccountID",
            "PORTFOLIO VALUE_2"
        ])
    )

    df = df.join(
        portfolio_agg,
        on="AccountID",
        how="left"
    )



    # ================= SCRIP CONCENTRATION & COVER % =================

    df = df.with_columns([
        pl.when(
            pl.col("PORTFOLIO VALUE_2").cast(pl.Float64, strict=False) != 0
        )
        .then(
            pl.col("PORTFOLIO VALUE").cast(pl.Float64, strict=False)
            / pl.col("PORTFOLIO VALUE_2").cast(pl.Float64, strict=False) * 100
        )
        .otherwise(None)
        .alias("Scrip_Concentration"),

        pl.when(
            pl.col("PORTFOLIO VALUE_2").cast(pl.Float64, strict=False) != 0
        )
        .then(
            pl.lit(100)
            + (
                pl.col("FIN_UPTODATE_MTF").cast(pl.Float64, strict=False)
                / pl.col("PORTFOLIO VALUE_2").cast(pl.Float64, strict=False) * 100
            )
        )
        .otherwise(None)
        .alias("Cover % on Portfolio")
    ])




    # ================= READ INDEX FILES =================
    nifty_df = pl.read_csv("ind_nifty50list.csv", infer_schema_length=0)
    bse200_df = pl.read_csv("BSE 200index_Constituents.csv", infer_schema_length=0)
    bse500_df = pl.read_csv("BSE 500index_Constituents.csv", infer_schema_length=0)

    # ================= CREATE ISIN FROM BSE 500 =================
    bse500_isin = bse500_df.select([
        pl.col("Scrip Code"),
        pl.col("ISIN No.")
    ]).rename({
        "Scrip Code": "Boscrip",
        "ISIN No.": "ISIN"
    })

    df = df.join(
        bse500_isin,
        on="Boscrip",
        how="left"
    )

    # ================= PREP ISIN SETS =================
    nifty_isin  = set(nifty_df["ISIN Code"].drop_nulls())
    bse200_isin = set(bse200_df["ISIN No."].drop_nulls())
    bse500_isin = set(bse500_df["ISIN No."].drop_nulls())

    # ================= REMARKS COLUMN =================
    df = df.with_columns(
        pl.when(pl.col("ISIN").is_in(nifty_isin))
          .then(pl.lit("ind_nifty50list.csv"))
        .when(pl.col("ISIN").is_in(bse200_isin))
          .then(pl.lit("BSE 200index_Constituents.csv"))
        .when(pl.col("ISIN").is_in(bse500_isin))
          .then(pl.lit("BSE 500index_Constituents.csv"))
        .otherwise(pl.lit("Others"))
        .alias("REMARKS")
    )

    # ================= FORCE BSE OVERRIDE =================
    df = df.with_columns(
        pl.when(pl.col("ScripName").str.to_uppercase().str.strip_chars() == "BSE")
          .then(pl.lit("BSE 200index_Constituents.csv"))
          .otherwise(pl.col("REMARKS"))
          .alias("REMARKS")
    )

    # ================= remarks_1 (FIXED WITH ABSOLUTE VALUES) =================

    ONE_CRORE = 10_000_000
    FIFTY_LAKH = 5_000_000

    df = df.with_columns(
        pl.when(
            # BOTH FIN and Portfolio exceed 1 Cr (absolute)
            (pl.col("FIN_UPTODATE_MTF").cast(pl.Float64, strict=False).abs() > ONE_CRORE) &
            (pl.col("PORTFOLIO VALUE").cast(pl.Float64, strict=False).abs() > ONE_CRORE)
        )
        .then(pl.lit("Violation"))

        # NOT Others → limit = 1 Cr
        .when(
            (pl.col("REMARKS") != "Others") &
            (pl.col("PORTFOLIO VALUE").cast(pl.Float64, strict=False).abs() <= ONE_CRORE)
        )
        .then(pl.lit("OK"))

        # Others → limit = 50 L
        .when(
            (pl.col("REMARKS") == "Others") &
            (pl.col("PORTFOLIO VALUE").cast(pl.Float64, strict=False).abs() <= FIFTY_LAKH)
        )
        .then(pl.lit("OK"))

        .otherwise(pl.lit("Violation"))
        .alias("remarks_1")
    )




    # ================= SCRIP CONCENTRATION COUNT =================

    scrip_count_agg = (
        df
        .group_by("AccountID")
        .agg(
            pl.count("Boscrip").alias("SCRIP CONCENTRATION COUNT")
        )
    )

    df = df.join(
        scrip_count_agg,
        on="AccountID",
        how="left"
    )







    # ================= ROUND ALL NUMERIC COLUMNS (NO DECIMALS) =================

    df = df.with_columns([
        pl.col(col)
          .round(0)
          .cast(pl.Int64, strict=False)
        for col, dtype in df.schema.items()
        if dtype in (pl.Float32, pl.Float64)
    ])

    # ================= FORCE NUMERIC + ROUND (NO GREEN TRIANGLES) =================

    df = df.with_columns([
        pl.when(pl.col(col).cast(pl.Float64, strict=False).is_not_null())
        .then(
            pl.col(col)
            .cast(pl.Float64, strict=False)
            .round(0)
            .cast(pl.Int64)
        )
        .otherwise(pl.col(col))
        .alias(col)
        for col in df.columns
    ])



    # ================= FINAL COLUMN CLEANUP =================

    # 1️⃣ Drop unwanted columns
    df = df.drop([
        col for col in ["RmtlID", "OtpPendingQty"]
        if col in df.columns
    ])

    # 2️⃣ Rename columns
    df = df.rename({
        "REMARKS": "STOCK BASKET",
        "remarks_1": "VIOLATION BASED ON SCRIP LEVEL"
    })

    # 3️⃣ FORCE numeric columns to be REAL numbers + round (NO GREEN TRIANGLES)
    df = df.with_columns([
        pl.when(pl.col(col).cast(pl.Float64, strict=False).is_not_null())
        .then(
            pl.col(col)
            .cast(pl.Float64, strict=False)
            .round(0)
            .cast(pl.Int64)
        )
        .otherwise(pl.col(col))
        .alias(col)
        for col in df.columns
    ])




    # ================= FINAL FORMULA-BASED COLUMN RENAME =================

    df = df.rename({
        "PORTFOLIO VALUE": "MIN(MtfValue ,MtfValueClosing)",
        "PORTFOLIO VALUE_2": "PORTFOLIO_VALUE[MIN_OF_SUM(P&R)]",
        "Scrip_Concentration": "Scrip_Concentration[(T/U)*100]",
        "Cover % on Portfolio": "Cover % on Portfolio(100+((S/U)*100))"
    })



    # ================= ACCOUNT LEVEL BUCKET SUMS =================

    bucket_sum_agg = (
        df
        .with_columns(
            pl.when(pl.col("STOCK BASKET") == "Others")
              .then(pl.col("MIN(MtfValue ,MtfValueClosing)").cast(pl.Float64, strict=False).abs())
              .otherwise(pl.lit(0.0))
              .alias("OTHERS_VALUE"),

            pl.when(pl.col("STOCK BASKET") != "Others")
              .then(pl.col("MIN(MtfValue ,MtfValueClosing)").cast(pl.Float64, strict=False).abs())
              .otherwise(pl.lit(0.0))
              .alias("NON_OTHERS_VALUE")
        )
        .group_by("AccountID")
        .agg([
            pl.col("OTHERS_VALUE").sum().alias("TOTAL_OTHERS"),
            pl.col("NON_OTHERS_VALUE").sum().alias("TOTAL_NON_OTHERS"),
        ])
    )

    df = df.join(
        bucket_sum_agg,
        on="AccountID",
        how="left"
    )


    # ================= VIOLATION BASED ON PORTFOLIO (FINAL LOGIC) =================

    df = df.with_columns(
        pl.when(
            (pl.col("STOCK BASKET") == "Others") &
            (pl.col("TOTAL_OTHERS") > ONE_CRORE)
        )
        .then(pl.lit("Violation"))

        .when(
            (pl.col("STOCK BASKET") == "Others")
        )
        .then(pl.lit("OK"))

        .when(
            (pl.col("STOCK BASKET") != "Others") &
            (pl.col("TOTAL_NON_OTHERS") > ONE_CRORE)
        )
        .then(pl.lit("Violation"))

        .otherwise(pl.lit("OK"))
        .alias("VIOLATION BASED ON PORTFOLIO LEVEL")
    )




    # ================= FAMILY BASKET TOTALS =================

    family_basket_agg = (
        df
        .group_by(["AccountID", "STOCK BASKET"])
        .agg(
            pl.col("MIN(MtfValue ,MtfValueClosing)")
              .cast(pl.Float64, strict=False)
              .abs()
              .sum()
              .alias("BASKET_TOTAL")
        )
    )

    df = df.join(
        family_basket_agg,
        on=["AccountID", "STOCK BASKET"],
        how="left"
    )


    # ================= APPLY FAMILY SPECIAL TREATMENT =================

    for family_name, rules in FAMILY_RULES.items():

        family_codes = rules["codes"]
        portfolio_limits = rules["portfolio_limits"]
        scrip_others_limit = rules["scrip_others_limit"]

        # -------- SCRIP LEVEL OVERRIDE --------

        df = df.with_columns(
            pl.when(
                (pl.col("AccountID").is_in(family_codes)) &
                (pl.col("STOCK BASKET") == "Others") &
                (pl.col("TOTAL_OTHERS") > scrip_others_limit)
            )
            .then(pl.lit("Violation"))

            .when(
                (pl.col("AccountID").is_in(family_codes)) &
                (pl.col("STOCK BASKET") == "Others") &
                (pl.col("TOTAL_OTHERS") <= scrip_others_limit)
            )
            .then(pl.lit("OK"))

            .otherwise(pl.col("VIOLATION BASED ON SCRIP LEVEL"))
            .alias("VIOLATION BASED ON SCRIP LEVEL")
        )

        # -------- PORTFOLIO LEVEL OVERRIDE --------

        for basket_name, limit in portfolio_limits.items():

            df = df.with_columns(
                pl.when(
                    (pl.col("AccountID").is_in(family_codes)) &
                    (pl.col("STOCK BASKET") == basket_name) &
                    (pl.col("BASKET_TOTAL") > limit)
                )
                .then(pl.lit("Violation"))

                .when(
                    (pl.col("AccountID").is_in(family_codes)) &
                    (pl.col("STOCK BASKET") == basket_name) &
                    (pl.col("BASKET_TOTAL") <= limit)
                )
                .then(pl.lit("OK"))

                .otherwise(pl.col("VIOLATION BASED ON PORTFOLIO LEVEL"))
                .alias("VIOLATION BASED ON PORTFOLIO LEVEL")
            )

    # ================= EXCEPTION OVERRIDE (CLEAN - NO EXTRA COLUMNS) =================

    exception_file = "EXCEPTION.xlsx"

    if os.path.exists(exception_file):

        exception_df = pl.read_excel(exception_file)

        # Ensure numeric
        exception_df = exception_df.with_columns([
            pl.col("Nifty 50").cast(pl.Float64, strict=False),
            pl.col("BSE 200").cast(pl.Float64, strict=False),
            pl.col("BSE 500").cast(pl.Float64, strict=False),
            pl.col("Other Scrips").cast(pl.Float64, strict=False),
        ])

        for row in exception_df.iter_rows(named=True):

            acc_id = row["AccountID"]
            remark_type = row["Remarks"]

            # Determine basket-wise limit mapping
            df = df.with_columns(

                pl.when(
                    (pl.col("AccountID") == acc_id)
                )
                .then(
                    pl.when(pl.col("STOCK BASKET") == "ind_nifty50list.csv")
                        .then(row["Nifty 50"])

                    .when(pl.col("STOCK BASKET") == "BSE 200index_Constituents.csv")
                        .then(row["BSE 200"])

                    .when(pl.col("STOCK BASKET") == "BSE 500index_Constituents.csv")
                        .then(row["BSE 500"])

                    .when(pl.col("STOCK BASKET") == "Others")
                        .then(row["Other Scrips"])

                    .otherwise(None)
                )
                .otherwise(None)
                .alias("TEMP_EXCEPTION_LIMIT")
            )

            # -------- SCRIP LEVEL OVERRIDE --------
            if remark_type == "VIOLATION BASED ON SCRIP LEVEL":

                df = df.with_columns(
                    pl.when(
                        (pl.col("AccountID") == acc_id)
                    )
                    .then(
                        pl.when(
                            pl.col("MIN(MtfValue ,MtfValueClosing)")
                            .cast(pl.Float64, strict=False)
                            .abs()
                            > pl.col("TEMP_EXCEPTION_LIMIT")
                        )
                        .then(pl.lit("Violation"))
                        .otherwise(pl.lit("OK"))
                    )
                    .otherwise(pl.col("VIOLATION BASED ON SCRIP LEVEL"))
                    .alias("VIOLATION BASED ON SCRIP LEVEL")
                )

            # -------- PORTFOLIO LEVEL OVERRIDE --------
            if remark_type == "VIOLATION BASED ON PORTFOLIO LEVEL":

                df = df.with_columns(
                    pl.when(
                        (pl.col("AccountID") == acc_id)
                    )
                    .then(
                        pl.when(
                            pl.col("BASKET_TOTAL")
                            .cast(pl.Float64, strict=False)
                            .abs()
                            > pl.col("TEMP_EXCEPTION_LIMIT")
                        )
                        .then(pl.lit("Violation"))
                        .otherwise(pl.lit("OK"))
                    )
                    .otherwise(pl.col("VIOLATION BASED ON PORTFOLIO LEVEL"))
                    .alias("VIOLATION BASED ON PORTFOLIO LEVEL")
                )

        # Drop temp helper column
        df = df.drop("TEMP_EXCEPTION_LIMIT")

        print("✅ Exception override applied cleanly (no extra columns added)")


    df = df.drop(["TOTAL_OTHERS", "TOTAL_NON_OTHERS", "BASKET_TOTAL"])

    # ================= AUTO FIT FUNCTION =================

    def autofit_excel(file_path):
        wb = load_workbook(file_path)
        ws = wb.active

        # ---------- AUTO WIDTH ----------
        for column_cells in ws.columns:
            max_length = 0
            col_letter = column_cells[0].column_letter

            for cell in column_cells:
                try:
                    value = str(cell.value) if cell.value else ""
                    max_length = max(max_length, len(value))
                except:
                    pass

            ws.column_dimensions[col_letter].width = max_length + 2

        # ---------- FORCE NUMERIC (REMOVE GREEN TRIANGLE) ----------
        for col in range(1, ws.max_column + 1):
            for row in range(2, ws.max_row + 1):  # Skip header
                cell = ws.cell(row=row, column=col)

                if isinstance(cell.value, str):
                    try:
                        if "." in cell.value:
                            cell.value = float(cell.value)
                        else:
                            cell.value = int(cell.value)
                    except:
                        pass

        wb.save(file_path)



    # ================= ADD MTF COLLATERAL FROM SHORTAGE FILE =================


    # Find dynamic shortage file automatically
    shortage_files = glob.glob("*_SHORTAGE.xlsx")

    if shortage_files:

        # Take latest file if multiple exist
        shortage_file = max(shortage_files, key=os.path.getctime)

        print(f"✅ Shortage file found: {shortage_file}")

        # Read required columns only
        shortage_df = (
            pl.read_excel(shortage_file)
            .select([
                pl.col("Account ID").cast(pl.Utf8),
                pl.col("MTF Colletral")
            ])
            .rename({
                "Account ID": "AccountID"
            })
        )

        # Join with main dataframe
        df = df.join(
            shortage_df,
            on="AccountID",
            how="left"
        )

        # Move MTF Colletral after ISIN column
        cols = df.columns

        if "MTF Colletral" in cols and "ISIN" in cols:

            cols.remove("MTF Colletral")

            isin_index = cols.index("ISIN")
            cols.insert(isin_index + 1, "MTF Colletral")

            df = df.select(cols)

        print("✅ MTF Colletral column added successfully")

    else:
        print("⚠ No SHORTAGE file found")



    # ================= ADD MTF COLLATERAL TO PORTFOLIO VALUE (FINAL CHANGE) =================

    # Ensure MTF Colletral is numeric
    df = df.with_columns(
        pl.col("MTF Colletral")
          .cast(pl.Float64, strict=False)
          .fill_null(0)
          .alias("MTF Colletral")
    )

    # Update existing column itself
    df = df.with_columns(
        (
            pl.col("PORTFOLIO_VALUE[MIN_OF_SUM(P&R)]")
            .cast(pl.Float64, strict=False)
            +
            pl.col("MTF Colletral")
        )
        .round(0)
        .cast(pl.Int64)
        .alias("PORTFOLIO_VALUE[MIN_OF_SUM(P&R)]")
    )

    # Rename only at final output level
    df = df.rename({
        "PORTFOLIO_VALUE[MIN_OF_SUM(P&R)]":
        "PORTFOLIO_VALUE[MIN_OF_SUM(P&R)] + MTF COLLATERAL"
    })

    print("✅ Portfolio value updated with MTF Collateral")

    # ================= RECALCULATE SCRIP CONCENTRATION & COVER % =================

    df = df.with_columns([

        pl.when(
            pl.col("PORTFOLIO_VALUE[MIN_OF_SUM(P&R)] + MTF COLLATERAL")
            .cast(pl.Float64, strict=False) != 0
        )
        .then(
            (
                pl.col("MIN(MtfValue ,MtfValueClosing)")
                .cast(pl.Float64, strict=False)

                /

                pl.col("PORTFOLIO_VALUE[MIN_OF_SUM(P&R)] + MTF COLLATERAL")
                .cast(pl.Float64, strict=False)

            ) * 100
        )
        .otherwise(None)
        .round(0)
        .cast(pl.Int64)
        .alias("Scrip_Concentration[(T/U)*100]"),


        pl.when(
            pl.col("PORTFOLIO_VALUE[MIN_OF_SUM(P&R)] + MTF COLLATERAL")
            .cast(pl.Float64, strict=False) != 0
        )
        .then(
            100 +

            (
                pl.col("FIN_UPTODATE_MTF")
                .cast(pl.Float64, strict=False)

                /

                pl.col("PORTFOLIO_VALUE[MIN_OF_SUM(P&R)] + MTF COLLATERAL")
                .cast(pl.Float64, strict=False)

            ) * 100
        )
        .otherwise(None)
        .round(0)
        .cast(pl.Int64)
        .alias("Cover % on Portfolio(100+((S/U)*100))")

    ])

    print("✅ Scrip Concentration and Cover % recalculated")






    # ================= SAVE OUTPUT WITH DATE =================
    today_date = datetime.today().strftime("%d%m%Y")
    output_file = f"FULLFILE_CONCENTRATION_{today_date}.xlsx"
    df.write_excel(output_file)

    autofit_excel(output_file)

    # ================= CREATE ZONE-WISE VIOLATION FILES =================

    # Filter only violation rows
    violation_df = df.filter(
        (pl.col("VIOLATION BASED ON SCRIP LEVEL") == "Violation") |
        (pl.col("VIOLATION BASED ON PORTFOLIO LEVEL") == "Violation")
    )

    if violation_df.height > 0:

        # Columns to remove ONLY in zone files
        columns_to_drop_zone = [
            "MtfFLag",
            "NseVar",
            "MtfValueAHC",
            "MtfValueClosing_AHC",
            "MtfRate",
            "ClRate",
            "MtfVar",
            "NclVar"
        ]

        # ✅ Correct Zone mapping (as per your original logic)
        zone_groups = {
            "ZODELH": ["ZODELH", "ZOMT", "ZOCDR"],
            "ZOLUDH": ["ZO", "ZOLUDH"],
            "ZOCHND": ["ZOCHND"],
            "ZOMUMB": ["ZOMUMB"]
        }

        today_date = datetime.today().strftime("%d%m%Y")

        for zone_name, zones in zone_groups.items():

            zone_df = violation_df.filter(
                pl.col("Zone").is_in(zones)
            )

            if zone_df.height > 0:

                # Drop columns ONLY in zone files
                zone_df = zone_df.drop(
                    [col for col in columns_to_drop_zone if col in zone_df.columns]
                )

                # Rename columns ONLY in zone files
                zone_df = zone_df.rename({
            "PORTFOLIO_VALUE[MIN_OF_SUM(P&R)] + MTF COLLATERAL":
            "PORTFOLIO_VALUE[MIN_OF_SUM(I&J)] + MTF COLLATERAL",

            "Scrip_Concentration[(T/U)*100]":
            "Scrip_Concentration[(L/M)*100]",

            "Cover % on Portfolio(100+((S/U)*100))":
            "Cover % on Portfolio(100+((K/M)*100))"
        })

                file_name = f"CONCENTRATION_{zone_name}_{today_date}.xlsx"

                zone_df.write_excel(file_name)
                autofit_excel(file_name)

    print("✅ Zone-wise violation files created successfully")
    print("✅ Concentration file created successfully with rounded values and auto-fit columns")



    # ================= EMAIL CONFIG =================

    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_EMAIL = os.getenv("SENDER_EMAIL")
    SMTP_PASSWORD = os.getenv("SENDER_PASSWORD")


    # ================= EMAIL FUNCTION =================

    def send_email(subject, body_html, attachment_path, to_list, cc_list):

        while True:
            try:
                msg = MIMEMultipart()
                msg["From"] = SMTP_EMAIL
                msg["To"] = ", ".join(to_list)
                msg["Cc"] = ", ".join(cc_list)
                msg["Subject"] = subject

                msg.attach(MIMEText(body_html, "html"))

                with open(attachment_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())

                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename={os.path.basename(attachment_path)}"
                )

                msg.attach(part)

                server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
                server.starttls()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)

                server.sendmail(
                    SMTP_EMAIL,
                    to_list + cc_list,
                    msg.as_string()
                )

                server.quit()

                print(f"✅ Email Sent: {attachment_path}")
                break

            except smtplib.SMTPAuthenticationError as e:
                print("❌ Authentication Error — Stopping")
                print(e)
                break

            except Exception as e:
                print("⚠ SMTP/Network Error — Retrying in 10 seconds")
                print(e)
                time.sleep(10)


    full_body = f"""
    Dear Team,<br><br>

    Please find attached the Consolidated MTF Concentration Report dated <b>{today_date}</b>.<br><br>

    This report highlights accounts based on the following monitoring parameters:<br><br>

    <b>1. VIOLATION BASED ON SCRIP LEVEL (Scrip-Level Check):</b><br>
    • For stocks classified under Nifty50,BSE500,BSE200 indices, exposure above <b>₹1 Crore</b> is treated as a breach.<br>
    • For stocks classified under <b>Others</b>, exposure above <b>₹50 Lakhs</b> is treated as a breach.<br><br>

    <b>2. VIOLATION BASED ON PORTFOLIO (Account-Level Basket Check):</b><br>
    • Total exposure in <b><u>Nifty50,BSE500,BSE200 basket</u></b> exceeding <b>₹1 Crore</b> is marked as Violation.<br>
    • Total exposure in <b><u>Others basket</u></b> exceeding <b>₹1 Crore</b> is marked as Violation.<br><br>

    <b>Note:</b><br>
    • Designated family accounts are monitored under customized basket-level exposure limits (10Cr – NIFTY50/BSE200, 6Cr – BSE500, 3Cr – OTHERS) as per the approved risk framework.<br>
    • Special approved limits from the Exception file have been applied wherever applicable.<br>
    • MTF Collateral is sourced from <b>MTF CRR Report 5</b> and it has been considered while computing the revised portfolio exposure and concentration metrics.<br><br>
    Any account marked as <b>"Violation"</b> under either of the above columns requires immediate review and appropriate action.<br><br>

    Kindly review the attached report and take necessary corrective measures wherever applicable.<br><br>

    Best regards,<br>
    <b>Kartavya Dhillon</b><br>
    Master Capital Services Ltd<br>
    📞 011-66331000 | Ext. 84
    """


    def zone_body(zone):
        return f"""
        Dear Team,<br><br>

        Please find attached the MTF Concentration Report for <b>{zone}</b> dated <b>{today_date}</b>.<br><br>

        This report includes only those accounts where either of the following conditions is marked as <b>"Violation"</b>:<br><br>

        <b>• VIOLATION BASED ON SCRIP LEVEL:</b> Indicates scrip-level exposure exceeding the prescribed limits (₹1 Crore for index stocks<b><u>(Nifty50,BSE500,BSE200)</u></b> and ₹50 Lakhs for <b><u>Others</u></b>).<br><br>
        <b>• VIOLATION BASED ON PORTFOLIO:</b> Indicates aggregate basket-level exposure exceeding ₹1 Crore <b><u>(Nifty50,BSE500,BSE200)</u></b> or ₹1 Crore <b><u>(Others)</u></b>.<br>
           • MTF Collateral is sourced from <b>MTF CRR Report 5</b> and it has been considered while computing the revised portfolio exposure and concentration metrics.<br><br>

        Accounts marked as <b>"Violation"</b> require review to assess exposure levels and ensure adherence to internal risk and concentration guidelines.<br><br>

        Kindly examine the highlighted accounts and initiate necessary corrective action wherever applicable.<br><br>

        Best regards,<br>
        <b>Kartavya Dhillon</b><br>
        Master Capital Services Ltd<br>
        📞 011-66331000 | Ext. 84
        """

    EMAIL_MAPPING = {
        "FULL":   {"file": f"FULLFILE_CONCENTRATION_{today_date}.xlsx",   "to": CONC_FULL_TO,   "cc": CONC_FULL_CC},
        "ZODELH": {"file": f"CONCENTRATION_ZODELH_{today_date}.xlsx",     "to": CONC_ZODELH_TO, "cc": CONC_ZODELH_CC},
        "ZOLUDH": {"file": f"CONCENTRATION_ZOLUDH_{today_date}.xlsx",     "to": CONC_ZOLUDH_TO, "cc": CONC_ZOLUDH_CC},
        "ZOCHND": {"file": f"CONCENTRATION_ZOCHND_{today_date}.xlsx",     "to": CONC_ZOCHND_TO, "cc": CONC_ZOCHND_CC},
        "ZOMUMB": {"file": f"CONCENTRATION_ZOMUMB_{today_date}.xlsx",     "to": CONC_ZOMUMB_TO, "cc": CONC_ZOMUMB_CC},
    }


    for key, details in EMAIL_MAPPING.items():

        file_path = details["file"]

        if os.path.exists(file_path):

            if key == "FULL":
                subject = f"MTF Concentration Report - FULL FILE - {today_date}"
                body = full_body
            else:
                subject = f"MTF Concentration Report - {key} - {today_date}"
                body = zone_body(key)

            send_email(
                subject,
                body,
                file_path,
                details["to"],
                details["cc"]
            )


    # ================= BACKUP & CLEANUP SECTION =================


    # -------- Create Backup Folder --------

    backup_root = os.path.join(BASE_FOLDER, "BACKUPFILES AUTOMATED")

    # Format date like: 24th Feb 2026
    day = datetime.today().day
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    formatted_date = datetime.today().strftime(f"%d{suffix} %b %Y")

    backup_date_folder = os.path.join(backup_root, formatted_date)

    os.makedirs(backup_date_folder, exist_ok=True)

    print(f"📂 Backup Folder Created: {backup_date_folder}")


    # -------- Find dynamic shortage file --------

    shortage_files = glob.glob("*_SHORTAGE.xlsx")

    shortage_file = None

    if shortage_files:
        shortage_file = max(shortage_files, key=os.path.getctime)

    # -------- Input Files --------

    files_to_backup = [
        mtf_file,
        risk_file,
        "ind_nifty50list.csv",
        "BSE 200index_Constituents.csv",
        "BSE 500index_Constituents.csv",
        "EXCEPTION.xlsx"
    ]

    # Add dynamic shortage file if found
    if shortage_file:
        files_to_backup.append(shortage_file)


    # -------- Output Files --------

    output_files = [
        f"FULLFILE_CONCENTRATION_{today_date}.xlsx",
        f"CONCENTRATION_ZODELH_{today_date}.xlsx",
        f"CONCENTRATION_ZOLUDH_{today_date}.xlsx",
        f"CONCENTRATION_ZOCHND_{today_date}.xlsx",
        f"CONCENTRATION_ZOMUMB_{today_date}.xlsx"
    ]

    # Combine all files
    all_files = files_to_backup + output_files


    # -------- Move Files Safely --------

    for file_name in all_files:

        file_path = os.path.join(BASE_FOLDER, file_name)

        if os.path.isfile(file_path):

            destination_path = os.path.join(backup_date_folder, file_name)

            # the RISK_REPORT belongs to COMMON FILES, which keeps its own backup - never copied here
            if file_name == risk_file or str(file_name).upper().startswith("RISK_REPORT_"):
                os.remove(file_path)
                print(f"🧹 Risk Report removed from the working folder (not backed up): {file_name}")
                continue

            # If EXCEPTION file → COPY (do not remove from main folder)
            if file_name == "EXCEPTION.xlsx":
                shutil.copy2(file_path, destination_path)
                print(f"📋 Copied to Backup (kept in main folder): {file_name}")

            else:
                shutil.move(file_path, destination_path)
                print(f"✅ Moved to Backup: {file_name}")

    print("✅ All input and output files backed up and cleaned safely.")
    return {"status": "ok", "step": "compute"}


def run_concentration():
    """
    Combined: download_concentration_files() then run_concentration_compute().
    Use this for the scheduler; use the two functions separately for the dashboard.
    """
    dl = download_concentration_files()
    if dl.get("status") != "ok":
        return dl
    return run_concentration_compute()


if __name__ == "__main__":
    import sys
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step == "download":
        print(download_concentration_files())
    elif step == "compute":
        print(run_concentration_compute())
    else:
        print(run_concentration())
