"""PHYSICAL DELIVERY - step 1: NIFTY 500 prices + PositionBook -> PositionBook_With_LTP.xlsx (Data + PIVOT).

Moved into the console from NSE_LTP_updated.py.  The calculations are unchanged.  What changed:
  * folders come from core.pipelines.physdel_cfg (inside the console), not from C:\\KARTAVYA
  * the CASH % comes from the console's Run popup instead of a desktop (tkinter) window
  * PositionBook is the newest PositionBook*.csv / *.xlsx in the INPUT folder
  * the run stops with a clear message (exit code 1) when the expiry date is not in the PositionBook
"""
import requests
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
import glob

from datetime import datetime, timedelta
import os


# ==========================================================
# CONFIG
# ==========================================================

from core.pipelines import physdel_cfg as C      # all folders / settings come from the console's own config

BASE_FOLDER = C.base()

INPUT_FOLDER = C.input_dir()

MAIN_FOLDER = os.path.join(
    BASE_FOLDER,
    "MAIN_FOLDER"
)

POSITIONBOOK_FILE = os.path.join(
    INPUT_FOLDER,
    "PositionBook.xlsx"
)

POSITIONBOOK_CSV = os.path.join(
    INPUT_FOLDER,
    "PositionBook.csv"
)

RISK_REPORT_FILE = C.risk_report_path()      # read straight from COMMON FILES - never copied or backed up here

BRM_XLSX_FILE = os.path.join(
    MAIN_FOLDER,
    "BRM LIST_File.xlsx"
)

BRM_CSV_FILE = os.path.join(
    MAIN_FOLDER,
    "BRM LIST_File.csv"
)

MAIL_LIST_FILE = os.path.join(
    MAIN_FOLDER,
    "Copy of T1 MAIL LIST.xlsx"
)

BRANCH_REGION_REF_FILE = os.path.join(
    MAIN_FOLDER,
    "Branch_AND_Region_LIST.xlsx"
)

POSITIONBOOK_WITH_LTP_FILE = os.path.join(
    BASE_FOLDER,
    "PositionBook_With_LTP.xlsx"
)

NIFTY500_FILE = os.path.join(
    BASE_FOLDER,
    "NIFTY500_CONSTITUENTS.xlsx"
)

# ==========================================================
# CTCL FILE — DYNAMIC SUFFIX MATCHING
# ==========================================================
# File pattern: FULL_FILE_CTCL_VS_NMASS_DELH_XX.xlsx
# where XX can be 01, 02, 03, 04, 05, etc.

CTCL_FILE_PATTERN = os.path.join(
    INPUT_FOLDER,
    "FULL_FILE_CTCL_VS_NMASS_*.xlsx"
)


def find_ctcl_file():
    """Find the CTCL file with any suffix number."""
    matches = glob.glob(CTCL_FILE_PATTERN)
    if matches:
        matches.sort(key=os.path.getmtime, reverse=True)
        return matches[0]
    return None


# ==========================================================
# PRE RUN VALIDATION
# ==========================================================

def validate_required_files():

    print("\n" + "=" * 60)
    print("PRE RUN VALIDATION")
    print("=" * 60)

    missing_files = []

    # ==================================================
    # PositionBook
    # ==================================================

    _pb = C.positionbook()
    if _pb:
        print(f"✅ PositionBook Found : {os.path.basename(_pb)}")
    else:
        print("❌ PositionBook Missing")
        missing_files.append("PositionBook")

    # ==================================================
    # Risk Report
    # ==================================================

    if os.path.exists(RISK_REPORT_FILE):
        print("✅ Risk Report")
    else:
        print("❌ Risk Report")
        missing_files.append("Risk Report")

    # ==================================================
    # BRM Master
    # ==================================================

    if (
        os.path.exists(BRM_XLSX_FILE)
        or
        os.path.exists(BRM_CSV_FILE)
    ):
        print("✅ BRM Master Found")
    else:
        print("❌ BRM Master Missing")
        missing_files.append("BRM Master")

    # ==================================================
    # Mail List
    # ==================================================

    if os.path.exists(MAIL_LIST_FILE):
        print("✅ Mail List")
    else:
        print("❌ Mail List")
        missing_files.append("Mail List")

    # ==================================================
    # Branch Region Master
    # ==================================================

    if os.path.exists(BRANCH_REGION_REF_FILE):
        print("✅ Branch Region Master")
    else:
        print("❌ Branch Region Master")
        missing_files.append("Branch Region Master")

    # ==================================================
    # CTCL File
    # ==================================================

    ctcl_file = find_ctcl_file()

    if ctcl_file:
        ctcl_suffix = os.path.splitext(
            os.path.basename(ctcl_file)
        )[0].split("_")[-1]

        deposit_header = f"DEPOSIT CTCL (File {ctcl_suffix})"

        print(f"✅ CTCL File Found : {os.path.basename(ctcl_file)}")
    else:
        print("❌ CTCL File Missing (FULL_FILE_CTCL_VS_NMASS_*.xlsx)")
        missing_files.append("CTCL File")

    # ==================================================
    # Result
    # ==================================================

    if missing_files:

        print("\n❌ VALIDATION FAILED")

        for item in missing_files:
            print(f"   • {item}")

        raise SystemExit

    print("\n✅ ALL REQUIRED FILES FOUND")
    print("=" * 60)


URL = (
    "https://www.nseindia.com/api/NextApi/apiClient/"
    "indexTrackerApi"
)

PARAMS = {
    "functionName": "getConstituents",
    "index": "NIFTY 500",
    "noofrecords": "0"
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json"
}


def get_percentage():
    """The CASH % typed in the console's Run popup (kept by the console; default 2)."""
    value = C.get_percent()
    print(f"CASH percentage chosen in the Run popup : {value:g}%")
    return value


def get_nifty500_data():

    session = requests.Session()

    session.get(
        "https://www.nseindia.com",
        headers=HEADERS,
        timeout=30
    )

    response = session.get(
        URL,
        params=PARAMS,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    return response.json()["data"]


def autofit_columns(ws):

    for column in ws.columns:

        max_length = 0

        column_letter = get_column_letter(
            column[0].column
        )

        for cell in column:

            try:
                length = len(str(cell.value))

                if length > max_length:
                    max_length = length

            except:
                pass

        ws.column_dimensions[column_letter].width = (
            max_length + 4
        )


def fix_leading_zero_client_ids(pos_df):
    """
    Some upstream export step (a spreadsheet re-save, most commonly)
    turns a purely-numeric ClientID like "070017911" into the number
    70017911, silently dropping the leading zero. Alphanumeric codes
    (e.g. "07AG96") are never affected, since Excel/pandas can only
    misinterpret values that are ALL digits as a number.

    This cross-checks every purely-numeric ClientID in PositionBook
    against the CTCL file's ACCOUNTID column (the same file the
    mobile-lookup API trusts). If "0" + ClientID matches a real
    ACCOUNTID there, the leading zero is restored automatically -
    before any SMS/WhatsApp step ever runs.
    """

    ctcl_file = find_ctcl_file()

    if not ctcl_file:
        return pos_df

    try:
        ctcl_df = pd.read_excel(ctcl_file, dtype=str)
    except Exception as e:
        print(f"⚠ Could not read CTCL file for ClientID cross-check: {e}")
        return pos_df

    if "ACCOUNTID" not in ctcl_df.columns:
        return pos_df

    ctcl_ids = set(
        ctcl_df["ACCOUNTID"].dropna().astype(str).str.strip()
    )

    corrected_log = []

    def try_fix(cid):
        cid = str(cid).strip()

        if cid in ctcl_ids:
            return cid

        if cid.isdigit():
            zero_padded = "0" + cid
            if zero_padded in ctcl_ids:
                corrected_log.append(f"  {cid}  ->  {zero_padded}")
                return zero_padded

        return cid

    pos_df["ClientID"] = (
        pos_df["ClientID"].astype(str).str.strip().apply(try_fix)
    )

    if corrected_log:
        print(
            f"\n⚠ LEADING ZERO AUTO-CORRECTED for "
            f"{len(corrected_log)} ClientID(s) "
            f"(matched against CTCL ACCOUNTID):"
        )
        for line in corrected_log:
            print(line)
        print()

    return pos_df


def keep_nfo_bfo_only(pos_df):
    """PositionBook: keep ONLY the rows whose Exch is NFO or BFO - done first, before any calculation."""
    col = next((c for c in pos_df.columns if str(c).strip().lower() in ("exch", "exchange")), None)
    if col is None:
        print("\n❌ The PositionBook has no 'Exch' column, so NFO / BFO rows cannot be selected.")
        print("[FRIENDLY_MSG] The PositionBook has no 'Exch' column - please upload the correct PositionBook file.")
        raise SystemExit(1)
    before = len(pos_df)
    vals = pos_df[col].astype(str).str.strip().str.upper()
    pos_df = pos_df[vals.isin(["NFO", "BFO"])].copy()
    print(f"\nPositionBook filter ({col} = NFO / BFO only): kept {len(pos_df):,} of {before:,} rows"
          f" ({', '.join(f'{k}: {v:,}' for k, v in vals[vals.isin(['NFO', 'BFO'])].value_counts().items()) or 'none'})")
    if pos_df.empty:
        print("[FRIENDLY_MSG] The PositionBook has no NFO / BFO rows in the 'Exch' column - nothing to calculate.")
        raise SystemExit(1)
    return pos_df


def merge_positionbook(
    nifty_df,
    percent
):

    pb_path = C.positionbook()

    if pb_path and pb_path.lower().endswith(".csv"):

        pos_df = pd.read_csv(pb_path, dtype={"ClientID": str})

    elif pb_path:

        pos_df = pd.read_excel(pb_path, dtype={"ClientID": str})

    else:

        print("\nPositionBook file not found.")
        return

    pos_df = keep_nfo_bfo_only(pos_df)          # only NFO / BFO rows, before any calculation
    pos_df = fix_leading_zero_client_ids(pos_df)

    percent_text = f"{percent:g}"

    up_col = f"CASH {percent_text}% UP"
    down_col = f"CASH {percent_text}% DOWN"

    nifty_required = nifty_df[
        [
            "SYMBOL",
            "LTP",
            up_col,
            down_col
        ]
    ]

    merged_df = pos_df.merge(
        nifty_required,
        left_on="Symbol",
        right_on="SYMBOL",
        how="left"
    )

    merged_df.rename(
        columns={
            "LTP": "LTP"
        },
        inplace=True
    )

    final_df = merged_df[
    [
        "ClientID",
        "ClientName",
        "Symbol",
        "ExpDate",
        "NetQty",
        "OpType",
        "Token",
        "Instrument",
        "StrikePrice",
        "BEP",
        "CFBuyAvgPrice",
        "CFSellAvgPrice",
        "LTP_y",
        up_col,
        down_col
    ]
].copy()

    final_df.rename(
    columns={
        "LTP_y": "SPOT PRICE"
    },
    inplace=True
)

    final_df["SPOT PRICE"] = pd.to_numeric(
    final_df["SPOT PRICE"],
    errors="coerce"
).fillna(0).round(2)

    final_df[up_col] = pd.to_numeric(
        final_df[up_col],
        errors="coerce"
    ).fillna(0).round(2)

    final_df[down_col] = pd.to_numeric(
        final_df[down_col],
        errors="coerce"
    ).fillna(0).round(2)

    # ==========================================================
    # REMOVE INDEX OPTIONS / FUTURES
    # ==========================================================

    before_count = len(final_df)

    final_df = final_df[
        ~final_df["Instrument"].isin(
            [
                "OPTIDX",
                "FUTIDX"
            ]
        )
    ]

    after_count = len(final_df)

    print(
        f"Removed {before_count - after_count} OPTIDX/FUTIDX rows"
    )

    # ==========================================================
    # LAST TUESDAY EXPIRY FILTER
    # ==========================================================
    def parse_expdate(series):
    # Try 4-digit year first (e.g. 28-Jul-2026)
        parsed = pd.to_datetime(series, format="%d-%b-%Y", errors="coerce")

    # For anything that failed, retry with 2-digit year (e.g. 28-Jul-26)
        mask = parsed.isna()
        if mask.any():
            parsed.loc[mask] = pd.to_datetime(
                 series[mask],
                 format="%d-%b-%y",
                 errors="coerce"
             )

        return parsed

    final_df["ExpDate"] = parse_expdate(final_df["ExpDate"])

    current_year = datetime.now().year
    current_month = datetime.now().month

    if current_month == 12:

        next_month = datetime(
            current_year + 1,
            1,
            1
        )

    else:

        next_month = datetime(
            current_year,
            current_month + 1,
            1
        )

    last_day = (
        next_month -
        timedelta(days=1)
    )

    while last_day.weekday() != 1:
        last_day -= timedelta(days=1)

    target_date = last_day

    available_dates = set(
        final_df["ExpDate"]
        .dropna()
        .dt.date
    )
    print("\nTarget Date:", target_date.date())

    print("\nAvailable Dates:")
    for d in sorted(available_dates):
        print(d)

    if target_date.date() in available_dates:

        selected_date = target_date

        print(
            f"Last Tuesday Found : "
            f"{selected_date.strftime('%d-%b-%Y')}"
        )

    elif (
        target_date -
        timedelta(days=1)
    ).date() in available_dates:

        selected_date = (
            target_date -
            timedelta(days=1)
        )

        print(
            f"Last Tuesday Missing. "
            f"Using Previous Day : "
            f"{selected_date.strftime('%d-%b-%Y')}"
        )

    else:

        print("\n")
        print("=" * 80)
        print("❌ EXPIRY DATE VALIDATION FAILED")
        print("=" * 80)

        print(
            f"Expected Expiry Date : "
            f"{target_date.strftime('%d-%b-%Y')}"
        )

        print(
            f"Fallback Date        : "
            f"{(target_date - timedelta(days=1)).strftime('%d-%b-%Y')}"
        )

        print("\nNeither date was found in PositionBook.")

        print(
            "\nPlease verify the ExpDate column "
            "in PositionBook and ensure the correct "
            "expiry date is available."
        )

        print("=" * 80)

        print("[FRIENDLY_MSG] The expiry date was not found in the PositionBook - check the ExpDate column "
              f"(expected {target_date.strftime('%d-%b-%Y')} or {(target_date - timedelta(days=1)).strftime('%d-%b-%Y')}).")
        raise SystemExit(1)

    before_count = len(final_df)

    final_df = final_df[
        final_df["ExpDate"].dt.date
        ==
        selected_date.date()
    ]

    after_count = len(final_df)

    print(
        f"Removed "
        f"{before_count - after_count} "
        f"rows using expiry filter"
    )

    # ==========================================================
    # MONEYNESS UP
    # ==========================================================

    final_df["MONEYNESS UP"] = "OTM"

    final_df.loc[
        (final_df["OpType"] == "CE") &
        (final_df["StrikePrice"] < final_df[up_col]),
        "MONEYNESS UP"
    ] = "ITM"

    final_df.loc[
        (final_df["OpType"] == "PE") &
        (final_df["StrikePrice"] > final_df[up_col]),
        "MONEYNESS UP"
    ] = "ITM"

    final_df.loc[
        final_df["OpType"] == "XX",
        "MONEYNESS UP"
    ] = "FUTURE"

    # ==========================================================
    # MONEYNESS DOWN
    # ==========================================================

    final_df["MONEYNESS DOWN"] = "OTM"

    final_df.loc[
        (final_df["OpType"] == "CE") &
        (final_df["StrikePrice"] < final_df[down_col]),
        "MONEYNESS DOWN"
    ] = "ITM"

    final_df.loc[
        (final_df["OpType"] == "PE") &
        (final_df["StrikePrice"] > final_df[down_col]),
        "MONEYNESS DOWN"
    ] = "ITM"

    final_df.loc[
        final_df["OpType"] == "XX",
        "MONEYNESS DOWN"
    ] = "FUTURE"

    # ==========================================================
    # SMS FLAG
    # ==========================================================

    final_df["SMS FLAG"] = "YES"

    final_df.loc[
        (final_df["MONEYNESS UP"] == "OTM") &
        (final_df["MONEYNESS DOWN"] == "OTM"),
        "SMS FLAG"
    ] = "NO"

    # ==========================================================
    # SENTIMENT
    # ==========================================================

    final_df["SENTIMENT"] = ""

    final_df.loc[
        (final_df["OpType"] == "CE") &
        (final_df["NetQty"] > 0),
        "SENTIMENT"
    ] = "LONG"

    final_df.loc[
        (final_df["OpType"] == "CE") &
        (final_df["NetQty"] < 0),
        "SENTIMENT"
    ] = "SHORT"

    final_df.loc[
        (final_df["OpType"] == "PE") &
        (final_df["NetQty"] < 0),
        "SENTIMENT"
    ] = "LONG"

    final_df.loc[
        (final_df["OpType"] == "PE") &
        (final_df["NetQty"] > 0),
        "SENTIMENT"
    ] = "SHORT"

    final_df.loc[
        (final_df["OpType"] == "XX") &
        (final_df["NetQty"] > 0),
        "SENTIMENT"
    ] = "LONG"

    final_df.loc[
        (final_df["OpType"] == "XX") &
        (final_df["NetQty"] < 0),
        "SENTIMENT"
    ] = "SHORT"

    # ==========================================================
    # DELIVERY VALUE
    # ==========================================================

    final_df["DELIVERY VALUE"] = 0.0

    final_df.loc[
        final_df["OpType"].isin(["CE", "PE"]),
        "DELIVERY VALUE"
    ] = (
        pd.to_numeric(
            final_df["StrikePrice"],
            errors="coerce"
        ).fillna(0)
        *
        pd.to_numeric(
            final_df["NetQty"],
            errors="coerce"
        ).fillna(0)
    )

    final_df.loc[
        final_df["OpType"] == "XX",
        "DELIVERY VALUE"
    ] = (
        pd.to_numeric(
            final_df["NetQty"],
            errors="coerce"
        ).fillna(0)
        *
        pd.to_numeric(
            final_df["SPOT PRICE"],
            errors="coerce"
        ).fillna(0)
    )

    # ==========================================================
    # ADJUST DELIVERY VALUE SIGN USING SENTIMENT
    # ==========================================================

    final_df.loc[
        final_df["SENTIMENT"] == "LONG",
        "DELIVERY VALUE"
    ] = (
        final_df.loc[
            final_df["SENTIMENT"] == "LONG",
            "DELIVERY VALUE"
        ].abs()
    )

    final_df.loc[
        final_df["SENTIMENT"] == "SHORT",
        "DELIVERY VALUE"
    ] = (
        -final_df.loc[
            final_df["SENTIMENT"] == "SHORT",
            "DELIVERY VALUE"
        ].abs()
    )

    # ==========================================================
    # READ TODAY'S RISK REPORT
    # ==========================================================

    today_file = RISK_REPORT_FILE

    if os.path.exists(today_file):

        risk_df = pd.read_csv(
            today_file,
            low_memory=False,
            dtype={"ACCOUNTID": str}
        )

        risk_required = risk_df[
            [
                "ACCOUNTID",
                "Zone",
                "Region",
                "Branch",
                "SubBranch",
                "TRADING_SOFTWARE_TYPE",
                "POAFLAG"
            ]
        ].drop_duplicates()

        final_df = final_df.merge(
            risk_required,
            left_on="ClientID",
            right_on="ACCOUNTID",
            how="left"
        )

        final_df.drop(
            columns=["ACCOUNTID"],
            inplace=True,
            errors="ignore"
        )

        print(
            f"Risk Report Merged : {today_file}"
        )

    else:

        print(
            f"Risk Report not found : {today_file}"
        )

    # ==========================================================
    # READ BRM MASTER
    # ==========================================================

    brm_csv = BRM_CSV_FILE
    brm_xlsx = BRM_XLSX_FILE

    brm_df = None

    if os.path.exists(brm_csv):

        brm_df = pd.read_csv(
            brm_csv
        )

    elif os.path.exists(brm_xlsx):

        brm_df = pd.read_excel(
            brm_xlsx
        )

    if brm_df is not None:

        brm_df["Branch"] = (
            brm_df["Branch"]
            .astype(str)
            .str.strip()
            .str.upper()
        )

        final_df["Branch"] = (
            final_df["Branch"]
            .astype(str)
            .str.strip()
            .str.upper()
        )

        brm_required = brm_df[
            [
                "Branch",
                "BRM"
            ]
        ].drop_duplicates()

        final_df = final_df.merge(
            brm_required,
            on="Branch",
            how="left"
        )

        final_df["BRM"] = (
            final_df["BRM"]
            .astype(str)
            .str.strip()
            .str.upper()
        )

        print(
            "BRM Mapping Completed"
        )

    else:

        print(
            "BRM LIST_File not found"
        )

    # ==========================================================
    # FINAL COLUMN ORDER
    # ==========================================================

    final_df = final_df[
        [
            "Zone",
            "Region",
            "Branch",
            "SubBranch",
            "TRADING_SOFTWARE_TYPE",

            "ClientID",
            "ClientName",
            "POAFLAG",

            "Symbol",
            "ExpDate",
            "OpType",
            "StrikePrice",
            "NetQty",

            "SPOT PRICE",

            up_col,
            down_col,

            "MONEYNESS UP",
            "MONEYNESS DOWN",

            "SMS FLAG",

            "BRM",

            "SENTIMENT",

            "DELIVERY VALUE"
        ]
    ]

    # ==========================================================
    # REMOVE ZERO NET QTY RECORDS
    # ==========================================================

    before_count = len(final_df)

    final_df = final_df[
        pd.to_numeric(
            final_df["NetQty"],
            errors="coerce"
        ).fillna(0) != 0
    ]

    after_count = len(final_df)

    print(
        f"Removed {before_count - after_count} rows where NetQty = 0"
    )

    final_df["ExpDate"] = final_df["ExpDate"].dt.strftime(
       "%d-%b-%Y"
    ).str.upper()

    # ==========================================================
    # ROUND ALL NUMERIC COLUMNS TO 2 DECIMALS
    # ==========================================================

    numeric_cols = final_df.select_dtypes(include="number").columns
    final_df[numeric_cols] = final_df[numeric_cols].round(2)

    # ==========================================================
    # BUILD PIVOT SHEET
    # ==========================================================

    print("[FRIENDLY_MSG] Building the PIVOT sheet", flush=True)
    print("\n" + "=" * 60)
    print("BUILDING PIVOT SHEET")
    print("=" * 60)

    pivot_df = build_pivot_sheet(final_df)

    # ==========================================================
    # SAVE OUTPUT WITH BOTH SHEETS
    # ==========================================================

    output_file = POSITIONBOOK_WITH_LTP_FILE

    with pd.ExcelWriter(
        output_file,
        engine="openpyxl"
    ) as writer:

        # Sheet 1: Data (original)
        final_df.to_excel(
            writer,
            sheet_name="Data",
            index=False
        )

        ws = writer.sheets["Data"]
        ws.freeze_panes = "A2"
        for cell in ws[1]:
            cell.font = Font(bold=True)
        autofit_columns(ws)

        # Sheet 2: PIVOT
        if pivot_df is not None and not pivot_df.empty:

            pivot_df.to_excel(
                writer,
                sheet_name="PIVOT",
                index=False
            )

            ws2 = writer.sheets["PIVOT"]
            ws2.freeze_panes = "A2"

            header_fill = PatternFill(
                start_color="1F4E79",
                end_color="1F4E79",
                fill_type="solid"
            )
            header_font = Font(
                bold=True,
                color="FFFFFF"
            )

            for cell in ws2[1]:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(
                    horizontal="center",
                    wrap_text=True
                )

            autofit_columns(ws2)

            print(f"✅ PIVOT sheet created : {len(pivot_df)} rows")

        else:
            print("⚠ PIVOT sheet is empty (no SMS FLAG = YES rows)")

    print(
        f"\nPositionBook saved as : {output_file}"
    )


def build_pivot_sheet(final_df):
    """
    Build the PIVOT sheet from SMS FLAG = YES rows.

    Steps:
    1. Filter SMS FLAG = YES
    2. Pivot: SUM of DELIVERY VALUE grouped by ClientID
    3. Bring Zone (first occurrence per ClientID)
    4. NET LEDGER = FIN_WITHOUT_MGN + CASH_LEDGER from Risk Report
    5. DEPOSIT CTCL and DEPOSIT NMASS from CTCL file (raw, by ClientID)
    6. For clients with NET LEDGER < 0:
       Adjust DEPOSIT NMASS = NET LEDGER + DEPOSIT NMASS
       (DEPOSIT CTCL is NOT adjusted - it stays raw)
    7. REMARKS: |DELIVERY VALUE| >= 1 Crore => "ALERT 1 CR"
    8. % DELIVERY VALUE = (DELIVERY VALUE / DEPOSIT CTCL) * 100
       If abs value >= 400 => append "ALERT" to REMARKS
    9. AUCTION VALUE 30%: 30% of DELIVERY VALUE where < 0, else 0
    10. NEW DEPOSIT(E + H) = DEPOSIT CTCL + AUCTION VALUE 30%
        If < 0 => append "ALERT_AUCTION_VALUE" to REMARKS
    11. DEPOSIT NMASS( AFTER ADJUSTING NET LEDGER) reported as the
        LAST column, for reference only - nothing calculates from it.
    """

    # ----------------------------------------------------------
    # STEP 1: Filter SMS FLAG = YES
    # ----------------------------------------------------------

    sms_yes = final_df[
        final_df["SMS FLAG"] == "YES"
    ].copy()

    if sms_yes.empty:
        print("⚠ No rows with SMS FLAG = YES")
        return None

    print(f"SMS FLAG = YES rows : {len(sms_yes)}")

    # ----------------------------------------------------------
    # STEP 2: Pivot — SUM of DELIVERY VALUE by ClientID
    # ----------------------------------------------------------

    pivot = sms_yes.groupby("ClientID").agg(
        {
            "DELIVERY VALUE": "sum",
            "Zone": "first"
        }
    ).reset_index()

    pivot.rename(
        columns={
            "DELIVERY VALUE": "SUM OF DELIVERY VALUE"
        },
        inplace=True
    )
    pivot["SUM OF DELIVERY VALUE"] = (
           pivot["SUM OF DELIVERY VALUE"]
           .round(2)
     )
    print(f"Unique clients in pivot : {len(pivot)}")

    # ----------------------------------------------------------
    # STEP 3: NET LEDGER from Risk Report
    # ----------------------------------------------------------
    # NET LEDGER = FIN_WITHOUT_MGN + CASH_LEDGER
    # Matched on ACCOUNTID = ClientID

    if os.path.exists(RISK_REPORT_FILE):

        risk_df = pd.read_csv(
            RISK_REPORT_FILE,
            low_memory=False,
            dtype={"ACCOUNTID": str}
        )

        required_risk_cols = [
            "ACCOUNTID",
            "FIN_WITHOUT_MGN",
            "CASH_LEDGER"
        ]

        missing_cols = [
            c for c in required_risk_cols
            if c not in risk_df.columns
        ]

        if missing_cols:
            print(
                f"⚠ Risk Report missing columns: {missing_cols}"
            )
            pivot["NET LEDGER"] = 0.0
        else:
            risk_df["FIN_WITHOUT_MGN"] = pd.to_numeric(
                risk_df["FIN_WITHOUT_MGN"],
                errors="coerce"
            ).fillna(0)

            risk_df["CASH_LEDGER"] = pd.to_numeric(
                risk_df["CASH_LEDGER"],
                errors="coerce"
            ).fillna(0)

            risk_df["NET_LEDGER_CALC"] = (
                risk_df["FIN_WITHOUT_MGN"]
                +
                risk_df["CASH_LEDGER"]
            )

            risk_agg = risk_df.groupby("ACCOUNTID").agg(
                {"NET_LEDGER_CALC": "first"}
            ).reset_index()

            pivot = pivot.merge(
                risk_agg,
                left_on="ClientID",
                right_on="ACCOUNTID",
                how="left"
            )

            pivot.drop(
                columns=["ACCOUNTID"],
                inplace=True,
                errors="ignore"
            )

            pivot.rename(
                columns={
                    "NET_LEDGER_CALC": "NET LEDGER"
                },
                inplace=True
            )

            pivot["NET LEDGER"] = pivot["NET LEDGER"].fillna(0)
            pivot["NET LEDGER"] = (
                  pivot["NET LEDGER"]
                  .round(2)
              )

            print("✅ NET LEDGER computed from Risk Report")

    else:
        print("⚠ Risk Report not found. NET LEDGER set to 0.")
        pivot["NET LEDGER"] = 0.0

    # ----------------------------------------------------------
    # STEP 4: DEPOSIT CTCL + DEPOSIT NMASS from CTCL file
    # ----------------------------------------------------------
    # Both are pulled straight from the same CTCL workbook, matched
    # on ACCOUNTID = ClientID.
    #
    #   DEPOSIT CTCL  -> RAW. No adjustment. This is the figure the
    #                    % DELIVERY VALUE and NEW DEPOSIT(E + H)
    #                    columns are calculated from.
    #   DEPOSIT NMASS -> gets the NET LEDGER adjustment in STEP 5 and
    #                    is reported as the LAST column, for reference
    #                    only. Nothing is calculated from it.

    ctcl_file = find_ctcl_file()

    if ctcl_file:
        ctcl_suffix = os.path.splitext(
         os.path.basename(ctcl_file)
      )[0].split("_")[-1]

        deposit_header = f"DEPOSIT CTCL (File {ctcl_suffix})"
    else:
        deposit_header = "DEPOSIT CTCL"

    nmass_header = "DEPOSIT NMASS( AFTER ADJUSTING NET LEDGER)"

    def find_deposit_column(columns, wanted, reject):
        """Locate a deposit column by name.

        Exact match first (case-insensitive, extra spaces squashed),
        then a token fallback that explicitly refuses the other
        deposit column so CTCL and NMASS can never be confused.
        """

        for c in columns:
            if " ".join(str(c).upper().split()) == wanted:
                return c

        for c in columns:
            name = str(c).upper()
            if (
                "DEPOSIT" in name
                and reject not in name
                and wanted.split()[-1] in name
            ):
                return c

        return None

    if ctcl_file:

        ctcl_df = pd.read_excel(ctcl_file, dtype={"ACCOUNTID": str})

        ctcl_col = find_deposit_column(
            ctcl_df.columns,
            "DEPOSIT CTCL",
            "NMASS"
        )

        nmass_col = find_deposit_column(
            ctcl_df.columns,
            "DEPOSIT NMASS",
            "CTCL"
        )

        if "ACCOUNTID" not in ctcl_df.columns:
            print("⚠ 'ACCOUNTID' column not found in CTCL file")
            pivot[deposit_header] = 0.0
            pivot[nmass_header] = 0.0

        else:

            # ----- DEPOSIT CTCL (raw) -----
            if not ctcl_col:
                print("⚠ 'DEPOSIT CTCL' column not found in CTCL file")
                pivot[deposit_header] = 0.0
            else:
                ctcl_df[ctcl_col] = pd.to_numeric(
                    ctcl_df[ctcl_col],
                    errors="coerce"
                ).fillna(0)

            # ----- DEPOSIT NMASS -----
            if not nmass_col:
                print("⚠ 'DEPOSIT NMASS' column not found in CTCL file")
                pivot[nmass_header] = 0.0
            else:
                ctcl_df[nmass_col] = pd.to_numeric(
                    ctcl_df[nmass_col],
                    errors="coerce"
                ).fillna(0)

            wanted_cols = [
                c for c in (ctcl_col, nmass_col) if c
            ]

            if wanted_cols:

                ctcl_agg = ctcl_df.groupby("ACCOUNTID").agg(
                    {c: "first" for c in wanted_cols}
                ).reset_index()

                pivot = pivot.merge(
                    ctcl_agg,
                    left_on="ClientID",
                    right_on="ACCOUNTID",
                    how="left"
                )

                pivot.drop(
                    columns=["ACCOUNTID"],
                    inplace=True,
                    errors="ignore"
                )

                rename_map = {}
                if ctcl_col:
                    rename_map[ctcl_col] = deposit_header
                if nmass_col:
                    rename_map[nmass_col] = nmass_header

                pivot.rename(
                    columns=rename_map,
                    inplace=True
                )

                for header in rename_map.values():
                    pivot[header] = (
                        pivot[header].fillna(0).round(2)
                    )

                if ctcl_col:
                    print(
                        f"✅ DEPOSIT CTCL loaded from "
                        f"{os.path.basename(ctcl_file)} "
                        f"(column: '{ctcl_col}')"
                    )

                if nmass_col:
                    print(
                        f"✅ DEPOSIT NMASS loaded from "
                        f"{os.path.basename(ctcl_file)} "
                        f"(column: '{nmass_col}')"
                    )

    else:
        print(
            "⚠ CTCL file not found. "
            "DEPOSIT CTCL and DEPOSIT NMASS set to 0."
        )
        pivot[deposit_header] = 0.0
        pivot[nmass_header] = 0.0

    # ----------------------------------------------------------
    # STEP 5: For clients with NET LEDGER < 0,
    #         adjust DEPOSIT NMASS = NET LEDGER + DEPOSIT NMASS
    # ----------------------------------------------------------
    # ASSUMPTION: Negative ledger balance reduces the effective
    # deposit. Only applies to negative NET LEDGER clients.
    # Clients with NET LEDGER >= 0 keep their original DEPOSIT NMASS.
    #
    # NOTE: DEPOSIT CTCL is deliberately NOT touched here - it stays
    # exactly as it appears in the CTCL file.

    negative_ledger_mask = pivot["NET LEDGER"] < 0

    pivot.loc[
        negative_ledger_mask,
        nmass_header
    ] = (
        pivot.loc[negative_ledger_mask, "NET LEDGER"]
        +
        pivot.loc[negative_ledger_mask, nmass_header]
    )
    pivot[nmass_header] = (
             pivot[nmass_header]
             .round(2)
         )
    neg_count = negative_ledger_mask.sum()
    print(
        f"NET LEDGER < 0 clients : {neg_count} "
        f"(DEPOSIT NMASS adjusted)"
    )

    # ----------------------------------------------------------
    # STEP 6: REMARKS — ALERT 1 CR
    # ----------------------------------------------------------
    # If (SUM OF DELIVERY VALUE) >= 1,00,00,000 (1 Crore)

    ONE_CRORE = 10000000

    pivot["REMARKS"] = ""

    pivot.loc[
        pivot["SUM OF DELIVERY VALUE"] >= ONE_CRORE,
        "REMARKS"
    ] = "ALERT 1 CR"

    alert_1cr_count = (
        pivot["SUM OF DELIVERY VALUE"] >= ONE_CRORE
    ).sum()
    print(f"ALERT 1 CR clients : {alert_1cr_count}")

    # ----------------------------------------------------------
    # STEP 7: % DELIVERY VALUE (A/B*100)
    # ----------------------------------------------------------
    # A = SUM OF DELIVERY VALUE
    # B = DEPOSIT CTCL
    # Column name includes the formula reference

    col_a = "SUM OF DELIVERY VALUE"
    col_b = deposit_header

    pct_col = f"% DELIVERY VALUE((C/E)*100)"

    pivot[pct_col] = 0.0

    non_zero_deposit = pivot[col_b] != 0

    pivot.loc[non_zero_deposit, pct_col] = (
        pivot.loc[non_zero_deposit, col_a]
        /
        pivot.loc[non_zero_deposit, col_b]
        * 100
    ).round(2)

    # If (%) >= 400, append ALERT to REMARKS
    alert_pct_mask = pivot[pct_col] >= 400

    for idx in pivot.index[alert_pct_mask]:
        current = pivot.at[idx, "REMARKS"]
        if current:
            if "ALERT" not in current or current == "ALERT 1 CR":
                pivot.at[idx, "REMARKS"] = current + ", ALERT"
        else:
            pivot.at[idx, "REMARKS"] = "ALERT"

    alert_pct_count = alert_pct_mask.sum()
    print(f"% DELIVERY VALUE >= 400 clients : {alert_pct_count}")

    # ----------------------------------------------------------
    # STEP 8: AUCTION VALUE 30%
    # ----------------------------------------------------------
    # SUM OF DELIVERY VALUE < 0 => 30% of that value
    # SUM OF DELIVERY VALUE >= 0 => 0

    pivot["AUCTION VALUE 30%"] = 0.0

    negative_delivery_mask = pivot["SUM OF DELIVERY VALUE"] < 0

    pivot.loc[
        negative_delivery_mask,
        "AUCTION VALUE 30%"
    ] = (
        pivot.loc[
            negative_delivery_mask,
            "SUM OF DELIVERY VALUE"
        ] * 0.30
    )
    pivot["AUCTION VALUE 30%"] = (
          pivot["AUCTION VALUE 30%"]
          .round(2)
       )
    print(
        f"Clients with negative DELIVERY VALUE : "
        f"{negative_delivery_mask.sum()}"
    )

    # ----------------------------------------------------------
    # STEP 9: NEW DEPOSIT(E + H)
    # ----------------------------------------------------------
    # = DEPOSIT CTCL + AUCTION VALUE 30%
    # If < 0 => append ALERT_AUCTION_VALUE to REMARKS

    pivot["NEW DEPOSIT(E + H)"] = (
        pivot[deposit_header]
        +
        pivot["AUCTION VALUE 30%"]
    )
    pivot["NEW DEPOSIT(E + H)"] = (
           pivot["NEW DEPOSIT(E + H)"]
           .round(2)
     )
    neg_new_deposit_mask = (
        pivot["NEW DEPOSIT(E + H)"] < 0
    )

    for idx in pivot.index[neg_new_deposit_mask]:
        current = pivot.at[idx, "REMARKS"]
        if current:
            pivot.at[idx, "REMARKS"] = (
                current + ", ALERT_AUCTION_VALUE"
            )
        else:
            pivot.at[idx, "REMARKS"] = "ALERT_AUCTION_VALUE"

    neg_new_deposit_count = neg_new_deposit_mask.sum()
    print(
        f"NEW DEPOSIT < 0 clients : {neg_new_deposit_count}"
    )

    # ----------------------------------------------------------
    # FINAL COLUMN ORDER
    # ----------------------------------------------------------

    pivot = pivot[
        [
            "Zone",
            "ClientID",
            "SUM OF DELIVERY VALUE",
            "NET LEDGER",
            deposit_header,
            pct_col,
            "REMARKS",
            "AUCTION VALUE 30%",
            "NEW DEPOSIT(E + H)",
            nmass_header
        ]
    ]

    return pivot


def main():
    validate_required_files()
    print("=" * 60)
    print("NIFTY 500 CONSTITUENTS DOWNLOADER")
    print("=" * 60)

    print("\n[1/7] Opening Configuration Window...")
    percent = get_percentage()

    if percent is None:
        return

    print(f"[2/7] CASH % Selected : {percent}")

    print("[FRIENDLY_MSG] Downloading NIFTY 500 prices from NSE", flush=True)
    print("\n[3/7] Downloading NSE Constituents Data...")
    data = get_nifty500_data()

    print(f"[4/7] Records Downloaded : {len(data)}")

    print("\n[5/7] Creating DataFrame...")
    print("Downloading NSE Data...")

    df = pd.DataFrame(data)

    df.rename(
        columns={
            "cmSymbol": "SYMBOL",
            "lasttradedPrice": "LTP"
        },
        inplace=True
    )

    print("[SUCCESS] SYMBOL Column Created")
    print("[SUCCESS] LTP Column Created")
    df.columns = [str(col).upper() for col in df.columns]

    percent_text = f"{percent:g}"

    up_col = f"CASH {percent_text}% UP"
    down_col = f"CASH {percent_text}% DOWN"

    df[up_col] = (
        df["LTP"] * (1 + percent / 100)
    ).round(2)

    df[down_col] = (
        df["LTP"] * (1 - percent / 100)
    ).round(2)
    print(f"[SUCCESS] CASH {percent}% UP Column Created")
    print(f"[SUCCESS] CASH {percent}% DOWN Column Created")
    output_file = NIFTY500_FILE
    print("[FRIENDLY_MSG] Saving the NIFTY 500 price file", flush=True)
    print("\n[6/7] Applying Excel Formatting...")
    with pd.ExcelWriter(
        output_file,
        engine="openpyxl"
    ) as writer:

        df.to_excel(
            writer,
            sheet_name="NIFTY500",
            index=False
        )

        ws = writer.sheets["NIFTY500"]

        ws.freeze_panes = "A2"

        for cell in ws[1]:
            cell.font = Font(bold=True)

        autofit_columns(ws)

    print("\n[7/7] Saving File...")

    print("\n" + "=" * 60)
    print("PROCESS COMPLETED SUCCESSFULLY")
    print("=" * 60)

    print(f"Rows Downloaded : {len(df)}")
    print(f"Saved File      : {output_file}")
    print("[FRIENDLY_MSG] Merging the PositionBook, risk report and deposits", flush=True)
    merge_positionbook(
        df,
        percent
    )

    print("=" * 60)

if __name__ == "__main__":
    main()