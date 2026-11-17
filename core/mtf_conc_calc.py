import polars as pl
from datetime import datetime
import os
import shutil
from openpyxl import load_workbook
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders


# ================= CONFIG =================
# Ported from CONCENTRATION_FINAL.py for the RMS console (core/mtf_concentration.py). The calculation below is UNCHANGED;
# only the folders come from the console now, and the e-mail + backup parts are done by the console (after this script).
BASE_FOLDER = os.environ["CONC_BASE_FOLDER"]

# Folder where the final FULLFILE_CONCENTRATION output should also be copied to ("" = no copy).
OUTPUT_COPY_FOLDER = os.environ.get("CONC_COPY_FOLDER", "").strip()

os.chdir(BASE_FOLDER)

today = datetime.today().strftime("%d%m%Y")

mtf_file   = f"MTFPOSITION_{today}.csv"
risk_file  = f"RISK_REPORT_{today}.csv"

# ================= PRE-FILE CHECK =================

import glob

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

    raise SystemExit(2)

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
    raise SystemExit(2)   # Stops program immediately

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

import glob

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

# ================= COPY FULL FILE TO ENTERPRISE RISK FOLDER =================

if OUTPUT_COPY_FOLDER:
    try:
        os.makedirs(OUTPUT_COPY_FOLDER, exist_ok=True)
        shutil.copy2(
            os.path.join(BASE_FOLDER, output_file),
            os.path.join(OUTPUT_COPY_FOLDER, output_file)
        )
        print(f"✅ Full file copied to: {os.path.join(OUTPUT_COPY_FOLDER, output_file)}")
    except Exception as _e:
        print(f"⚠ COPY_FAILED: could not copy the full file to {OUTPUT_COPY_FOLDER}: {_e}")

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

print("CONC_CALC_DONE")
