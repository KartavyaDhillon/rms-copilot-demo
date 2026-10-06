import os
import glob
import pandas as pd
import polars as pl
import io
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
import sys

# =================================================================
# CONFIG
# =================================================================
folder = r"C:\T1 HOLDING"


# =================================================================
# COPY DESTINATIONS FOR RISK REPORT
# =================================================================
RISK_COPY_PATHS = [
    r"C:\KARTAVYA\MCX WEB ZONEWISE AMIT SIR",
    r"C:\KARTAVYA\SN359",
    r"C:\KARTAVYA\15 minutes",
    r"C:\KARTAVYA\CTCL VS NMASS",
    r"C:\KARTAVYA\CONCENTRATION",
    r"C:\KARTAVYA\MTF ORDER FILE",
    r"C:\KARTAVYA\SN327"
]


print("\n============== FILE CHECK START ==============\n")

required_patterns = {
    "C_PLEDGE": "C_*_SEC_PLEDGE_*_02.csv",
    "F_PLEDGE": "F_*_SEC_PLEDGE_*_02.csv",
    "SEARCHRESULT": "SearchResults*.xlsx",
    "HOLDING": "HOLDING_*.csv",
    "T1_KAMBALA": "T1_HOLDING_KAMBALA_*.TXT",
    "RISK_REPORT": "RISK_REPORT_*.csv",
    "EXCEPTION": "EXCEPTION_*.xls*",
    "T1_SYMPHONY": "T1_HOLDING_SYMPHONY_*.TXT",
    "MF_KAMB": "MF_KAMB_HOLDING_*.TXT"
}
missing_files = []

for name, pattern in required_patterns.items():
    full_pattern = os.path.join(folder, pattern)
    files = glob.glob(full_pattern)

    if not files:
        missing_files.append(f"{name} → Pattern: {pattern}")

# If ANY file is missing → EXIT program
if missing_files:
    print("❌ ERROR: Missing required files!\n")
    for m in missing_files:
        print("   -", m)
    print("\n❌ PROGRAM STOPPED — FIX FILES AND RUN AGAIN ❌\n")
    sys.exit(1)

print("✔ All required files found!")
print("\n============== FILE CHECK END ================\n")





# =================================================================
# PICK FILES DYNAMICALLY
# =================================================================
file1 = max(glob.glob(os.path.join(folder, "C_*_SEC_PLEDGE_*_02.csv")), key=os.path.getmtime)
file2 = max(glob.glob(os.path.join(folder, "F_*_SEC_PLEDGE_*_02.csv")), key=os.path.getmtime)
search_file = max(glob.glob(os.path.join(folder, "SearchResults*.xlsx")), key=os.path.getmtime)

print("Using File1:", file1)
print("Using File2:", file2)
print("Using Search:", search_file)


# =================================================================
# HEADER DETECTOR FOR C_ & F_ FILES
# =================================================================
def find_header_line_raw(path, target_col="Client/CP code"):
    target = target_col.upper().strip()
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for i in range(40):
            line = f.readline()
            if not line:
                break
            cols = [c.strip().upper() for c in line.split(",")]
            if target in cols:
                return i
    raise Exception(f"HEADER NOT FOUND in → {path}")


# =================================================================
# READ PLEDGE FILE
# =================================================================
def read_pledge_file(path):

    REQUIRED = {
        "CLIENT/CP CODE": "ClientCode",
        "SYMBOL": "SYMBOL",
        "ISIN": "ISIN",
        "NET EFFECTIVE QTY AFTER QTY LIMITS": "NETQTY",
        "HAIRCUT": "HAIRCUT",
         "SERIES": "SERIES"
    }

    # Final clean rows stored here
    final_rows = []

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    header_map = None  # active header index mapping

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            continue

        cols = [c.strip() for c in line.split(",")]
        upper_cols = [c.upper() for c in cols]

        # ---- 1️⃣ Detect NEW header row anytime ----
        if "CLIENT/CP CODE" in upper_cols:

            header_map = {}  # reset mapping

            for req_col in REQUIRED:
                if req_col in upper_cols:
                    header_map[REQUIRED[req_col]] = upper_cols.index(req_col)
                else:
                    header_map[REQUIRED[req_col]] = None   # missing column

            # go to next line
            continue

        # ---- 2️⃣ Skip data rows until first header seen ----
        if header_map is None:
            continue

        # ---- 3️⃣ Extract row using current header map ----
        row = {}

        for key, idx in header_map.items():
            if idx is None or idx >= len(cols):
                row[key] = ""
            else:
                row[key] = cols[idx].strip()

        final_rows.append(row)

    # ---- 4️⃣ Convert to Polars ----
    df = pl.DataFrame(final_rows)

    # ---- 5️⃣ Convert NETQTY & HAIRCUT to float ----
    df = df.with_columns([
        pl.col("NETQTY")
        .cast(pl.Float64, strict=False)
        .fill_null(0),

        pl.col("HAIRCUT")
        .cast(pl.Float64, strict=False)
        .fill_null(0)
    ])

    return df


# =================================================================
# SEARCH HEADER IN EXCEL
# =================================================================
def detect_excel_header(path, target_col):
    df = pd.read_excel(path, header=None, nrows=30, dtype=str)
    target = target_col.upper().strip()
    for i in range(30):
        row = [str(x).upper().strip() for x in df.iloc[i].tolist()]
        if target in row:
            return i
    raise Exception("HEADER NOT FOUND IN SEARCH FILE")


# =================================================================
# READ SEARCHRESULT FILE
# =================================================================
def read_search_file(path):
    header_row = detect_excel_header(path, "UCC Code")
    df_pd = pd.read_excel(path, header=None, dtype=str)

    df_pd.columns = df_pd.iloc[header_row]
    df_pd = df_pd.drop(index=range(header_row + 1))
    df_pd = df_pd.reset_index(drop=True)
    df_pd.columns = [c.strip() for c in df_pd.columns]

    df = pl.from_pandas(df_pd)

    df = df.with_columns([
        pl.col("Quantity")
        .cast(str)
        .str.replace_all(",", "")
        .cast(pl.Float64, strict=False)
        .fill_null(0),

        pl.col("Haircut/Var")
        .cast(pl.Float64, strict=False)
        .fill_null(0)
    ])

    return df.select([
        pl.col("UCC Code").alias("ClientCode"),
        pl.col("Instrument").alias("ISIN"),
        pl.col("Security symbol").alias("SYMBOL"),
    pl.col("Series").alias("SERIES"),
        pl.col("Quantity").alias("NETQTY"),
        pl.col("Haircut/Var").alias("HAIRCUT")
    ])


# =================================================================
# LOAD ALL FILES
# =================================================================
df1 = read_pledge_file(file1).rename({
    "NETQTY": "NETQTY_F1",
    "HAIRCUT": "HAIRCUT_F1",
    "SERIES": "SERIES_F1"
})

df2 = read_pledge_file(file2).rename({
    "NETQTY": "NETQTY_F2",
    "HAIRCUT": "HAIRCUT_F2",
    "SERIES": "SERIES_F2"
})

df3 = read_search_file(search_file).rename({
    "NETQTY": "NETQTY_F3",
    "HAIRCUT": "HAIRCUT_F3",
    "SERIES": "SERIES_F3"
})

# clean df1 clientcode only
df1 = df1.with_columns(pl.col("ClientCode").fill_null("").str.strip_chars())


# =================================================================
# MAIN MERGE (no duplicate ISIN/SYMBOL)
# =================================================================
merged = (
    df1
    .join(
        df2.rename({
            "ClientCode": "ClientCode_F2",
            "ISIN": "ISIN_F2",
            "SYMBOL": "SYMBOL_F2"
        }),
        left_on=["ClientCode", "ISIN"],
        right_on=["ClientCode_F2", "ISIN_F2"],
        how="full"
    )
    .join(
        df3.rename({
            "ClientCode": "ClientCode_F3",
            "ISIN": "ISIN_F3",
            "SYMBOL": "SYMBOL_F3"
        }),
        left_on=["ClientCode", "ISIN"],
        right_on=["ClientCode_F3", "ISIN_F3"],
        how="full"
    )
)


# =================================================================
# FIX CLIENTCODE FROM ALL FILES (no blanks)
# =================================================================
merged = merged.with_columns(
    pl.coalesce([
        pl.col("ClientCode"),
        pl.col("ClientCode_F2"),
        pl.col("ClientCode_F3")
    ]).alias("ClientCode")
)

merged = merged.drop(["ClientCode_F2", "ClientCode_F3"], strict=False)


# =================================================================
# FIX ISIN & SYMBOL — pick from ANY file
# =================================================================
merged = merged.with_columns([
    pl.coalesce([
        pl.col("ISIN"),
        pl.col("ISIN_F2"),
        pl.col("ISIN_F3")
    ]).alias("ISIN"),

    pl.coalesce([
        pl.col("SYMBOL"),
        pl.col("SYMBOL_F2"),
        pl.col("SYMBOL_F3")
    ]).alias("SYMBOL"),
])



# drop helper ISIN/SYMBOL
merged = merged.drop(["ISIN_F2", "ISIN_F3", "SYMBOL_F2", "SYMBOL_F3"], strict=False)

# ============================================================
# SERIES PRIORITY: F → C → Search
# ============================================================
merged = merged.with_columns(
    pl.coalesce([
        pl.col("SERIES_F2"),
        pl.col("SERIES_F1"),
        pl.col("SERIES_F3"),
        pl.lit("")
    ]).alias("SERIES")
)



# =================================================================
# FINAL NETQTY & HAIRCUT COMBINED (pre-group)
# =================================================================
merged = merged.with_columns([
    (
        pl.col("NETQTY_F1").fill_null(0) +
        pl.col("NETQTY_F2").fill_null(0) +
        pl.col("NETQTY_F3").fill_null(0)
    ).alias("NETQTY_COMBINED"),

    (
        pl.col("HAIRCUT_F1").fill_null(0) +
        pl.col("HAIRCUT_F2").fill_null(0) +
        pl.col("HAIRCUT_F3").fill_null(0)
    ).alias("HAIRCUT_COMBINED")
])


# =================================================================
# REMOVE DUPLICATES BY GROUPING (IMPORTANT)
# =================================================================
merged = (
    merged
    .group_by(["ClientCode", "ISIN"])
    .agg([
        pl.col("SYMBOL").first().alias("SYMBOL"),
        pl.col("SERIES").first().alias("SERIES"),
        pl.col("NETQTY_F1").sum().alias("NETQTY_F1"),
        pl.col("NETQTY_F2").sum().alias("NETQTY_F2"),
        pl.col("NETQTY_F3").sum().alias("NETQTY_F3"),
        pl.col("HAIRCUT_F1").sum().alias("HAIRCUT_F1"),
        pl.col("HAIRCUT_F2").sum().alias("HAIRCUT_F2"),
        pl.col("HAIRCUT_F3").sum().alias("HAIRCUT_F3"),
    ])
    .with_columns([
        (pl.col("NETQTY_F1") + pl.col("NETQTY_F2") + pl.col("NETQTY_F3"))
        .alias("NETQTY_COMBINED"),

        pl.coalesce([
    pl.when(pl.col("HAIRCUT_F2").is_not_null() & (pl.col("HAIRCUT_F2") != 0)).then(pl.col("HAIRCUT_F2")),
    pl.when(pl.col("HAIRCUT_F1").is_not_null() & (pl.col("HAIRCUT_F1") != 0)).then(pl.col("HAIRCUT_F1")),
    pl.when(pl.col("HAIRCUT_F3").is_not_null() & (pl.col("HAIRCUT_F3") != 0)).then(pl.col("HAIRCUT_F3")),
    pl.lit(0)
]).alias("HAIRCUT_COMBINED")

    ])
)


# =================================================================
# REMOVE EMPTY ROWS WHERE ALL NETQTY VALUES ARE ZERO   <-- ADD HERE
# =================================================================
merged = merged.filter(
    ~(
        (pl.col("NETQTY_F1").fill_null(0) == 0) &
        (pl.col("NETQTY_F2").fill_null(0) == 0) &
        (pl.col("NETQTY_F3").fill_null(0) == 0)
    )
)

# =================================================================
# REMOVE DUPLICATES BY GROUPING (IMPORTANT)
# =================================================================
merged = (
    merged
    .group_by(["ClientCode", "ISIN", "SYMBOL"])
    .agg([
        pl.col("SERIES").first().alias("SERIES"),
        pl.col("NETQTY_F1").sum().alias("NETQTY_F1"),
        pl.col("NETQTY_F2").sum().alias("NETQTY_F2"),
        pl.col("NETQTY_F3").sum().alias("NETQTY_F3"),
        pl.col("HAIRCUT_F1").sum().alias("HAIRCUT_F1"),
        pl.col("HAIRCUT_F2").sum().alias("HAIRCUT_F2"),
        pl.col("HAIRCUT_F3").sum().alias("HAIRCUT_F3"),
    ])
    .with_columns([
        (pl.col("NETQTY_F1") + pl.col("NETQTY_F2") + pl.col("NETQTY_F3"))
        .alias("NETQTY_COMBINED"),

        pl.coalesce([
    pl.when(pl.col("HAIRCUT_F2").is_not_null() & (pl.col("HAIRCUT_F2") != 0)).then(pl.col("HAIRCUT_F2")),
    pl.when(pl.col("HAIRCUT_F1").is_not_null() & (pl.col("HAIRCUT_F1") != 0)).then(pl.col("HAIRCUT_F1")),
    pl.when(pl.col("HAIRCUT_F3").is_not_null() & (pl.col("HAIRCUT_F3") != 0)).then(pl.col("HAIRCUT_F3")),
    pl.lit(0)
]).alias("HAIRCUT_COMBINED")

    ])
)


# =================================================================
# FINAL SELECT
# =================================================================
merged = merged.select([
    pl.col("ClientCode"),
    pl.col("ISIN"),
    pl.col("SYMBOL"),
    pl.col("SERIES"),
    pl.col("NETQTY_F1"),
    pl.col("NETQTY_F2"),
    pl.col("NETQTY_F3"),
    pl.col("NETQTY_COMBINED"),
    pl.col("HAIRCUT_F1"),
    pl.col("HAIRCUT_F2"),
    pl.col("HAIRCUT_F3"),
    pl.col("HAIRCUT_COMBINED")
])


# =================================================================
# CLEAN NETQTY & HAIRCUT COLUMNS (replace blank / null with 0)
# =================================================================
merged = merged.with_columns([
    pl.col("NETQTY_F1").cast(pl.Float64, strict=False).fill_null(0).alias("NETQTY_F1"),
    pl.col("NETQTY_F2").cast(pl.Float64, strict=False).fill_null(0).alias("NETQTY_F2"),
    pl.col("NETQTY_F3").cast(pl.Float64, strict=False).fill_null(0).alias("NETQTY_F3"),
    pl.col("HAIRCUT_F1").cast(pl.Float64, strict=False).fill_null(0).alias("HAIRCUT_F1"),
    pl.col("HAIRCUT_F2").cast(pl.Float64, strict=False).fill_null(0).alias("HAIRCUT_F2"),
    pl.col("HAIRCUT_F3").cast(pl.Float64, strict=False).fill_null(0).alias("HAIRCUT_F3"),
])


# =================================================================
# REMOVE BLANK ISIN ROWS
# =================================================================
merged = merged.filter(
    (pl.col("ISIN").is_not_null()) &
    (pl.col("ISIN").str.strip_chars() != "")
)

# =================================================================
# SAVE OUTPUT (OVERWRITE SAME FILE)
# =================================================================
output = os.path.join(folder, "FINAL_MERGED_OUTPUT.csv")
merged.write_csv(output)

print("\n✅ FINAL CLEANED FILE CREATED →", output)


import os
import glob
import re
import polars as pl

folder = r"C:\T1 HOLDING"


# ===============================================================
# 1️⃣ AUTO-DETECT DATE FROM HOLDING FILE
# ===============================================================
holding_files_all = glob.glob(os.path.join(folder, "HOLDING_*.csv"))
if not holding_files_all:
    raise Exception("❌ No HOLDING_*.csv file found!")

m = re.search(r"HOLDING_(\d{8})", os.path.basename(holding_files_all[0]))
if not m:
    raise Exception("❌ Could not extract date from HOLDING filename!")

valid_date = m.group(1)
print("📅 Detected date:", valid_date)


# ===============================================================
# 2️⃣ READ HOLDING FILE
# ===============================================================
holding_pattern = os.path.join(folder, f"HOLDING_{valid_date}*")
holding_files = glob.glob(holding_pattern)

if not holding_files:
    raise Exception(f"❌ No Holding file found for {valid_date}")

holding_file = holding_files[0]
print("📄 Holding File:", holding_file)

df_holding = pl.read_csv(
    holding_file,
    infer_schema_length=0,
    ignore_errors=True,
    schema_overrides={
        "AccountID": pl.Utf8,
        "Boscrip": pl.Utf8,
        "Symbol": pl.Utf8,
        "ISIN": pl.Utf8
    }
).select([
    pl.col("AccountID").alias("A"),        # ClientCode
    pl.col("Boscrip").alias("B"),
    pl.col("Symbol").alias("C"),
    pl.col("ISIN"),
    pl.col("ClientStatus"),
    pl.col("DPAcStatus"),
    pl.col("CustVar")
])



# Keep ClientStatus + DPAcStatus for final reconciliation merge
df_holding_status = df_holding.select([
    pl.col("A").alias("ClientCode"),
    pl.col("ClientStatus"),
    pl.col("DPAcStatus")
]).unique(subset=["ClientCode"])

# CustVar reference table for final reconciliation merge
df_holding_custvar = df_holding.select([
    pl.col("A").alias("ClientCode"),
    pl.col("ISIN"),
    pl.col("CustVar")
]).unique(subset=["ClientCode", "ISIN"])



# ===============================================================
# 3️⃣ READ T1 FILE (A, B, C, NetQty)
# ===============================================================
t1_pattern = os.path.join(folder, f"T1_HOLDING_KAMBALA_{valid_date}*")
t1_files = glob.glob(t1_pattern)

if not t1_files:
    raise Exception(f"❌ T1_HOLDING_KAMBALA file not found for {valid_date}")

t1_file = t1_files[0]
print("📄 T1 File:", t1_file)

with open(t1_file, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

rows = [line.strip().split("|") for line in lines[1:] if line.strip()]
if not rows:
    raise Exception("❌ T1 file has no usable rows!")

# Build DataFrame with A, B, C, NetQty  (NetQty = column index 4)
df_t1 = pl.DataFrame({
    "A": [r[0].strip() for r in rows],
    "B": [r[1].strip() if len(r) > 1 else "" for r in rows],
    "C": [r[2].strip() if len(r) > 2 else "" for r in rows],
    "NetQty": [
        r[4].replace(",", "").strip() if len(r) > 4 else "0"
        for r in rows
    ],
}).with_columns(
    pl.col("NetQty").cast(pl.Float64)
)

# FIX: give every Kambala line its own unique row number so the pass-2
# ISIN can be attached back to the SAME line (not to every line that
# shares the same client + Boscrip).
df_t1 = df_t1.with_columns(pl.Series("_rid", range(df_t1.height)))


# ===============================================================
# 4️⃣ PASS 1 — MATCH (A + B)
# ===============================================================
df_p1 = df_t1.join(
    df_holding.select(["A", "B", "ISIN"]),
    on=["A", "B"],
    how="left"
).rename({"ISIN": "ISIN_p1"})


# ===============================================================
# 5️⃣ PASS 2 — MATCH (A + C) for unmatched rows
# ===============================================================
unmatched = df_p1.filter(
    pl.col("ISIN_p1").is_null() | (pl.col("ISIN_p1") == "")
)

df_p2 = unmatched.join(
    df_holding.select(["A", "C", "ISIN"]),

    left_on=["A", "C"],
    right_on=["A", "C"],
    how="left"
).rename({"ISIN": "ISIN_p2"})


# ===============================================================
# 6️⃣ MERGE PASS 1 + PASS 2 (priority: p1 → p2)
# ===============================================================
df_final = (
    df_p1.join(
        df_p2.select(["_rid", "ISIN_p2"]),
        on="_rid",
        how="left"
    )
)

df_final = df_final.with_columns(
    pl.coalesce([
        pl.col("ISIN_p1"),
        pl.col("ISIN_p2")
    ]).fill_null("UNKNOWN").alias("ISIN")
)


# ===============================================================
# 7️⃣ FINAL OUTPUT — RENAME TO CORRECT COLUMN NAMES
# ===============================================================
df_final = df_final.rename({
    "A": "ClientCode",
    "B": "Boscrip",
    "C": "Symbol"
})

df_final = df_final.select([
    "ClientCode", "Boscrip", "Symbol", "NetQty", "ISIN"
])


# ===============================================================
# 8️⃣ SAVE OUTPUT
# ===============================================================
output_file = os.path.join(folder, f"T1_Holding_Kambala_With_ISIN_{valid_date}.csv")
df_final.write_csv(output_file)

print("✅ DONE — Saved:", output_file)



# =====================================================================
#            🔥  CONVERT T1_HOLDING_ISIN_<date>.TXT → CSV  🔥
# =====================================================================

# ===============================================================
# READ T1 NOREN FILE (OPTIONAL)
# ===============================================================
noren_pattern = os.path.join(folder, f"T1_HOLDING_NOREN_{valid_date}*")
noren_files = glob.glob(noren_pattern)

if noren_files:

    noren_file = noren_files[0]
    print("📄 NOREN File:", noren_file)

    with open(noren_file, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    rows = [line.strip().split("|") for line in lines[1:] if line.strip()]

    df_t1_noren_raw = pl.DataFrame({
        "A": [r[0].strip() for r in rows],
        "B": [r[1].strip() if len(r) > 1 else "" for r in rows],
        "C": [r[2].strip() if len(r) > 2 else "" for r in rows],
        "NetQty": [
            r[4].replace(",", "").strip() if len(r) > 4 else "0"
            for r in rows
        ],
    },
    schema={
        "A": pl.Utf8,
        "B": pl.Utf8,
        "C": pl.Utf8,
        "NetQty": pl.Float64
    })

    # FIX: unique row number per NOREN line (same reason as Kambala above)
    df_t1_noren_raw = df_t1_noren_raw.with_columns(
        pl.Series("_rid", range(df_t1_noren_raw.height))
    )

    df_n1 = df_t1_noren_raw.join(
        df_holding.select(["A", "B", "ISIN"]),
        on=["A", "B"],
        how="left"
    ).rename({"ISIN": "ISIN_p1"})

    unmatched_n = df_n1.filter(
        pl.col("ISIN_p1").is_null() | (pl.col("ISIN_p1") == "")
    )

    df_n2 = unmatched_n.join(
        df_holding.select(["A", "C", "ISIN"]),
        left_on=["A", "C"],
        right_on=["A", "C"],
        how="left"
    ).rename({"ISIN": "ISIN_p2"})

    df_n_final = df_n1.join(
        df_n2.select(["_rid", "ISIN_p2"]),
        on="_rid",
        how="left"
    )

    df_n_final = df_n_final.with_columns(
        pl.coalesce([
            pl.col("ISIN_p1"),
            pl.col("ISIN_p2")
        ]).fill_null("UNKNOWN").alias("ISIN")
    )

    df_n_final = df_n_final.rename({
        "A": "ClientCode",
        "NetQty": "NETQTY_T1_NOREN"
    }).select([
        "ClientCode", "ISIN", "NETQTY_T1_NOREN"
    ])

    df_n_final = (
        df_n_final
        .group_by(["ClientCode", "ISIN"])
        .agg(pl.col("NETQTY_T1_NOREN").sum())
    )

else:

    print("⚠ T1_HOLDING_NOREN file not found — skipping NOREN processing")

    df_n_final = pl.DataFrame({
    "ClientCode": pl.Series([], dtype=pl.Utf8),
    "ISIN": pl.Series([], dtype=pl.Utf8),
    "NETQTY_T1_NOREN": pl.Series([], dtype=pl.Float64)
})



print("\n================ T1_HOLDING_SYMPHONY Processing ================\n")

# 1️⃣ PICK SYMPHONY FILE
symphony_pattern = os.path.join(folder, f"T1_HOLDING_SYMPHONY_{valid_date}*.TXT")
symphony_files = glob.glob(symphony_pattern)

if not symphony_files:
    raise Exception(f"❌ No T1_HOLDING_SYMPHONY file found for {valid_date}")

symphony_file = symphony_files[0]
print("Using T1_HOLDING_SYMPHONY File:", symphony_file)

# 2️⃣ READ FILE (| DELIMITED) — DO NOT SKIP ANY ROW
with open(symphony_file, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

rows = [line.strip().split("|") for line in lines if line.strip()]

if not rows:
    raise Exception("❌ T1_HOLDING_SYMPHONY file contains no valid rows!")

# 3️⃣ BUILD DATAFRAME WITH A,B,C
df_symphony = pl.DataFrame({
    "ClientCode": [r[0].strip() for r in rows],
    "ISIN":        [r[1].strip() if len(r) > 1 else "" for r in rows],
    "NETQTY_SYMPHONY": [
        r[2].replace(",", "").strip() if len(r) > 2 else "0"
        for r in rows
    ]
}).with_columns(
    pl.col("NETQTY_SYMPHONY").cast(pl.Float64, strict=False)
)

print("✔ SYMPHONY file loaded successfully!")


# ===============================================================
# 🔥 READ MF_KAMB_HOLDING FILE
# ===============================================================
mf_kamb_pattern = os.path.join(folder, f"MF_KAMB_HOLDING_{valid_date}*.TXT")
mf_kamb_files = glob.glob(mf_kamb_pattern)

if not mf_kamb_files:
    raise Exception(f"❌ MF_KAMB_HOLDING file not found for {valid_date}")

mf_kamb_file = mf_kamb_files[0]
print("📄 MF KAMB File:", mf_kamb_file)

with open(mf_kamb_file, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

rows = [line.strip().split("|") for line in lines if line.strip()]

df_mf_kamb = pl.DataFrame({
    "ClientCode": [r[0].strip() for r in rows],
    "ISIN":       [r[1].strip() if len(r) > 1 else "" for r in rows],
    "NETQTY_MF_KAMB": [
        r[2].replace(",", "").strip() if len(r) > 2 else "0"
        for r in rows
    ]
}).with_columns(
    pl.col("NETQTY_MF_KAMB").cast(pl.Float64, strict=False).fill_null(0)
)

print("✔ MF_KAMB_HOLDING loaded successfully!")


# ===============================================================
# 🔥 READ MF_NOREN_HOLDING FILE
# ===============================================================
mf_noren_pattern = os.path.join(folder, f"MF_NOREN_HOLDING_{valid_date}*.TXT")
mf_noren_files = glob.glob(mf_noren_pattern)

if mf_noren_files:
    mf_noren_file = mf_noren_files[0]
    print("📄 MF NOREN File:", mf_noren_file)
else:
    print("⚠ MF_NOREN_HOLDING file not found — skipping MF NOREN processing")
    df_mf_noren = pl.DataFrame({
    "ClientCode": pl.Series([], dtype=pl.Utf8),
    "ISIN": pl.Series([], dtype=pl.Utf8),
    "NETQTY_MF_NOREN": pl.Series([], dtype=pl.Float64)
})

if mf_noren_files:

    mf_noren_file = mf_noren_files[0]
    print("📄 MF NOREN File:", mf_noren_file)

    with open(mf_noren_file, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    rows = [line.strip().split("|") for line in lines if line.strip()]

    df_mf_noren = pl.DataFrame({
        "ClientCode": [r[0].strip() for r in rows],
        "ISIN":       [r[1].strip() if len(r) > 1 else "" for r in rows],
        "NETQTY_MF_NOREN": [
            r[2].replace(",", "").strip() if len(r) > 2 else "0"
            for r in rows
        ]
    }).with_columns(
        pl.col("NETQTY_MF_NOREN").cast(pl.Float64, strict=False).fill_null(0)
    )

    print("✔ MF_NOREN_HOLDING loaded successfully!")

else:

    print("⚠ MF_NOREN_HOLDING file not found — skipping MF NOREN")

    df_mf_noren = pl.DataFrame({
    "ClientCode": pl.Series([], dtype=pl.Utf8),
    "ISIN": pl.Series([], dtype=pl.Utf8),
    "NETQTY_MF_NOREN": pl.Series([], dtype=pl.Float64)
})






print("\n==================== RECONCILIATION START ====================\n")

# =====================================================================
# 1️⃣ LOAD ALL 3 REQUIRED FILES
# =====================================================================

# FINAL MERGED OUTPUT
merged_file = os.path.join(folder, "FINAL_MERGED_OUTPUT.csv")
df_merged = pl.read_csv(merged_file)

# T1 KAMBALA FILE
t1_kambala_file = os.path.join(folder, f"T1_Holding_Kambala_With_ISIN_{valid_date}.csv")
df_t1_kam = pl.read_csv(t1_kambala_file)



# =====================================================================
# 2️⃣ FIRST MATCH — ClientCode + ISIN
# =====================================================================
df_step1 = df_merged.join(
    df_t1_kam.select(["ClientCode", "ISIN", "NetQty", "Symbol"]),
    on=["ClientCode", "ISIN"],
    how="left"
).rename({
    "NetQty": "NETQTY_T1_KAMBALA",
    "Symbol": "Symbol_T1"
})

# Keep merged file's symbol separately
df_step1 = df_step1.with_columns(
    pl.col("SYMBOL").alias("SYMBOL_MERGED")
)

# Rename merged quantity
df_step1 = df_step1.rename({"NETQTY_COMBINED": "NETQTY_MERGED"})

# ===============================================================
# 🔥 ADD MF QUANTITIES INTO T1 COLUMNS (BEFORE MATCHING)
# ===============================================================

# Ensure T1 qty columns exist and are numeric
df_step1 = df_step1.with_columns([
    pl.col("NETQTY_T1_KAMBALA").cast(pl.Float64, strict=False).fill_null(0)
])

# Join MF KAMB
df_step1 = df_step1.join(
    df_mf_kamb,
    on=["ClientCode", "ISIN"],
    how="left"
)

# Join MF NOREN
df_step1 = df_step1.join(
    df_mf_noren,
    on=["ClientCode", "ISIN"],
    how="left"
)

# Replace null MF qty with 0
df_step1 = df_step1.with_columns([
    pl.col("NETQTY_MF_KAMB").fill_null(0),
    pl.col("NETQTY_MF_NOREN").fill_null(0)
])

# Add MF into KAMBALA (important)
df_step1 = df_step1.with_columns([
    (pl.col("NETQTY_T1_KAMBALA") + pl.col("NETQTY_MF_KAMB"))
        .alias("NETQTY_T1_KAMBALA")
])

print("✔ MF quantities added BEFORE reconciliation!")


# =====================================================================
# 3️⃣ INITIAL REMARKS (LEVEL 1)
# =====================================================================
df_step1 = df_step1.with_columns(
    pl.when(pl.col("NETQTY_MERGED") == pl.col("NETQTY_T1_KAMBALA"))
      .then(pl.lit("Matched"))
      .otherwise(pl.lit("Mismatched"))
      .alias("REMARK_LEVEL1")
)


# =====================================================================
# 4️⃣ SECOND MATCH — for mismatches: ClientCode + Symbol
# =====================================================================
mismatch_lvl1 = df_step1.filter(pl.col("REMARK_LEVEL1") == "Mismatched")

df_level2 = mismatch_lvl1.join(
    df_t1_kam.select(["ClientCode", "Symbol", "NetQty"]),
    left_on=["ClientCode", "SYMBOL_MERGED"],
    right_on=["ClientCode", "Symbol"],
    how="left"
).rename({"NetQty": "NETQTY_T1_KAMBALA_ALT"})

df_level2 = df_level2.with_columns(
    pl.when(pl.col("NETQTY_MERGED") == pl.col("NETQTY_T1_KAMBALA_ALT"))
      .then(pl.lit("Matched"))
      .otherwise(pl.lit("Mismatched"))
      .alias("REMARK_LEVEL2")
)


# =====================================================================
# 5️⃣ THIRD MATCH — With T1_HOLDING_NOREN (ClientCode + ISIN)
# =====================================================================
df_level3 = df_level2.join(
    df_n_final,
    on=["ClientCode", "ISIN"],
    how="left"
)

df_level3 = df_level3.with_columns([
    pl.col("NETQTY_T1_NOREN").cast(pl.Float64, strict=False).fill_null(0),
    pl.col("NETQTY_MF_NOREN").fill_null(0)
])

# Add MF into NOREN qty BEFORE comparison
df_level3 = df_level3.with_columns(
    (pl.col("NETQTY_T1_NOREN") + pl.col("NETQTY_MF_NOREN"))
        .alias("NETQTY_T1_NOREN")
)
df_level3 = df_level3.with_columns(
    pl.when(pl.col("NETQTY_MERGED") == pl.col("NETQTY_T1_NOREN"))
      .then(pl.lit("Matched"))
      .otherwise(pl.lit("Mismatched"))
      .alias("REMARK_LEVEL3")
)

# =====================================================================
# 6️⃣ MERGE ALL REMARKS BACK INTO STEP1
# =====================================================================
df_step1 = df_step1.join(
    df_level3.select([
        "ClientCode", "ISIN", 
        "REMARK_LEVEL2", 
        "REMARK_LEVEL3", 
        "NETQTY_T1_NOREN"
    ]),
    on=["ClientCode", "ISIN"],
    how="left"
)

# ============================
# FINAL REMARK PRIORITY
# ============================
df_step1 = df_step1.with_columns(
    pl.coalesce([
        pl.col("REMARK_LEVEL3"),
        pl.col("REMARK_LEVEL2"),
        pl.col("REMARK_LEVEL1")
    ]).alias("FINAL_REMARKS")
)

df_step1 = df_step1.drop([
    "NETQTY_MF_KAMB",
    "NETQTY_MF_NOREN"
], strict=False)

# =====================================================================
# 7️⃣ FINAL OUTPUT SELECTION
# =====================================================================
df_output = df_step1.select([
    pl.col("ClientCode"),
    pl.col("ISIN"),
    pl.col("SYMBOL_MERGED").alias("Symbol"),
  pl.col("SERIES"),
    pl.col("NETQTY_MERGED"),
    pl.col("NETQTY_T1_KAMBALA"),
    pl.col("NETQTY_T1_NOREN"),
    pl.col("HAIRCUT_F1"),
    pl.col("HAIRCUT_F2"),
    pl.col("HAIRCUT_F3"),
    pl.col("HAIRCUT_COMBINED"),
    pl.col("FINAL_REMARKS")
])


# ============================================================
# MOVE SERIES NEXT TO Symbol COLUMN (SAFE VERSION)
# ============================================================

cols = df_output.columns

# Remove SERIES if it already exists
cols_no_series = [c for c in cols if c != "SERIES"]

# Insert SERIES after Symbol
pos = cols_no_series.index("Symbol") + 1
new_cols = cols_no_series[:pos] + ["SERIES"] + cols_no_series[pos:]

df_output = df_output.select(new_cols)


# Replace blanks/nulls with 0 in T1 quantity columns
df_output = df_output.with_columns([
    pl.col("NETQTY_T1_KAMBALA").cast(pl.Float64, strict=False).fill_null(0),
    pl.col("NETQTY_T1_NOREN").cast(pl.Float64, strict=False).fill_null(0)
])




df_output = df_output.join(
    df_symphony,
    on=["ClientCode", "ISIN"],
    how="left"
)

# Replace null Symphony qty with 0
df_output = df_output.with_columns(
    pl.col("NETQTY_SYMPHONY").fill_null(0)
)

print("✔ SYMPHONY data merged successfully!")

# ============================================================
#  MATCH NETQTY_MERGED WITH NETQTY_SYMPHONY  → FINAL REMARK
# ============================================================
df_output = df_output.with_columns(
    pl.when(pl.col("NETQTY_MERGED") == pl.col("NETQTY_SYMPHONY"))
      .then(pl.lit("Matched"))
      .otherwise(pl.col("FINAL_REMARKS"))
      .alias("FINAL_REMARKS")
)

print("✔ Symphony remark logic applied!")






# ------------------------------------------------------------
# 1️⃣ PICK RISK REPORT FILE
# ------------------------------------------------------------
risk_files = glob.glob(os.path.join(folder, f"RISK_REPORT_{valid_date}.*"))
if not risk_files:
    raise Exception("❌ No Risk Report file found!")

risk_file = risk_files[0]
print("Risk File:", risk_file)

# =====================================================================
# 📦 COPY RISK REPORT TO MULTIPLE FOLDERS
# =====================================================================
import shutil

print("\n================ COPYING RISK REPORT ====================\n")

for dest_path in RISK_COPY_PATHS:
    try:
        # Ensure folder exists
        os.makedirs(dest_path, exist_ok=True)

        # Destination file path
        dest_file = os.path.join(dest_path, os.path.basename(risk_file))

        # Copy file
        shutil.copy2(risk_file, dest_file)

        print(f"✔ Copied to → {dest_path}")

    except Exception as e:
        print(f"❌ Failed to copy to {dest_path} → {e}")

print("\n✔ Risk Report copied to all destinations!\n")



ext = os.path.splitext(risk_file)[1].lower()

# ------------------------------------------------------------
# 2️⃣ READ RISK REPORT
# ------------------------------------------------------------
if ext in [".xlsx", ".xls", ".xlsm", ".xlsb"]:
    df_risk = pl.from_pandas(pd.read_excel(risk_file, dtype=str))
else:
    preview = pd.read_csv(risk_file, nrows=0, dtype=str)
    schema_risk = {c: pl.Utf8 for c in preview.columns}
    df_risk = pl.read_csv(risk_file, schema_overrides=schema_risk)

df_risk = df_risk.rename({"ACCOUNTID": "ClientCode"}).select([
    "ClientCode",
    "TRADING_SOFTWARE_TYPE",
    "POAFLAG",
    "POA_EXEMPT"
]).unique(subset=["ClientCode"])


# ------------------------------------------------------------
# 3️⃣ MERGE INTO RECONCILIATION df_output
# ------------------------------------------------------------
df_output = df_output.join(
    df_risk,
    on="ClientCode",
    how="left",
    suffix="_RISK"
)

# ------------------------------------------------------------
# 4️⃣ POSITION TRADING_SOFTWARE_TYPE AFTER SYMBOL COLUMN
# ------------------------------------------------------------
cols = df_output.columns
idx = cols.index("Symbol") + 1

new_order = list(dict.fromkeys(cols[:idx] + ["TRADING_SOFTWARE_TYPE"] + cols[idx:]))

df_output = df_output.select(new_order)



print("✔ RISK REPORT merged successfully!")

# Replace blank POA fields with 'NA'
df_output = df_output.with_columns([
    pl.col("POAFLAG").fill_null("NA"),
    pl.col("POA_EXEMPT").fill_null("NA")
])


# =====================================================================
#  🔥  EXCEPTION FILE MERGE (AFTER RISK REPORT MERGE)
# =====================================================================

# 1️⃣ Pick EXCEPTION file dynamically
exception_files = glob.glob(os.path.join(folder, "EXCEPTION_*.xls*"))
if not exception_files:
    raise Exception("❌ No EXCEPTION_*.xls* file found!")

exception_file = exception_files[0]
print("EXCEPTION File:", exception_file)

# 2️⃣ Read EXCEPTION Excel file
df_exception = pl.from_pandas(pd.read_excel(exception_file, dtype=str))

# 3️⃣ Filter EXCEPTION_STATIC_CODE == 'HOLDING'
df_exception = df_exception.filter(
    pl.col("EXCEPTION_STATIC_CODE").str.strip_chars().str.to_uppercase() == "HOLDING"
)

# 4️⃣ Keep only EFFECTED_CODE column
df_exception = df_exception.select([
    pl.col("EFFECTED_CODE").alias("ClientCode")
]).unique()

# 5️⃣ Add EXCEPTION column to df_output
df_output = df_output.join(
    df_exception.with_columns(pl.lit("YES").alias("EXCEPTION")),
    on="ClientCode",
    how="left"
)

# 6️⃣ Replace null EXCEPTION with NO
df_output = df_output.with_columns(
    pl.col("EXCEPTION").fill_null("NO")
)

print("✔ EXCEPTION file merged successfully!")


# =====================================================================
# 🔥 MERGE HOLDING STATUS COLUMNS (ClientStatus & DPAcStatus)
# =====================================================================
df_output = df_output.join(
    df_holding_status,
    on="ClientCode",
    how="left"
)

# Replace blanks / nulls with 'NA'
df_output = df_output.with_columns([
    pl.col("ClientStatus").fill_null("NA"),
    pl.col("DPAcStatus").fill_null("NA")
])

print("✔ HOLDING STATUS merged successfully!")

# =====================================================================
# 🔥 MERGE CustVar FROM HOLDING (match: ClientCode + ISIN)
# =====================================================================
df_output = df_output.join(
    df_holding_custvar,
    on=["ClientCode", "ISIN"],
    how="left"
)

# Replace null CustVar with 0
df_output = df_output.with_columns(
    pl.col("CustVar").cast(pl.Float64, strict=False).fill_null(0)
)

print("✔ CustVar merged successfully!")


# Move CustVar right after HAIRCUT_COMBINED (only IF it exists)
cols = df_output.columns
new_cols = []
cust_present = "CustVar" in cols

for col in cols:
    if col != "CustVar":      # avoid duplication
        new_cols.append(col)
    if col == "HAIRCUT_COMBINED" and cust_present:
        new_cols.append("CustVar")

df_output = df_output.select(new_cols)

# ===========================================
# MOVE FINAL_REMARKS TO THE END OF FILE
# ===========================================
cols = df_output.columns

# Remove FINAL_REMARKS from current position
cols_no_final = [c for c in cols if c != "FINAL_REMARKS"]

# Add FINAL_REMARKS as last column
new_order = cols_no_final + ["FINAL_REMARKS"]

df_output = df_output.select(new_order)

print("✔ FINAL_REMARKS moved to last column!")



# Start with an empty FINAL_REMARKS_1 column
df_output = df_output.with_columns(
    pl.lit("").alias("FINAL_REMARKS_1")
)


# ======================================================
# RULE 1 — MF ROUND-OFF OVERRIDE (CORRECT VERSION)
# If INF + mismatch and rounded qty matches
# → change FINAL_REMARKS to Matched
# ======================================================
# ======================================================
# MF ROUND-OFF OVERRIDE (KAMBALA + NOREN)
# ======================================================

df_output = df_output.with_columns(
    pl.when(
        (pl.col("FINAL_REMARKS") == "Mismatched") &
        pl.col("ISIN").str.starts_with("INF") &
        (
            (
                pl.col("NETQTY_MERGED").round(0) ==
                pl.col("NETQTY_T1_KAMBALA").round(0)
            )
            |
            (
                pl.col("NETQTY_MERGED").round(0) ==
                pl.col("NETQTY_T1_NOREN").round(0)
            )
        )
    )
    .then(pl.lit("Matched"))
    .otherwise(pl.col("FINAL_REMARKS"))
    .alias("FINAL_REMARKS")
)

# ======================================================
# ✅ ADD THIS BLOCK HERE
# RULE 0 — If FINAL_REMARKS = Matched → HOLDING OK
# ======================================================
df_output = df_output.with_columns(
    pl.when(pl.col("FINAL_REMARKS") == "Matched")
      .then(pl.lit("HOLDING OK"))
      .otherwise(pl.col("FINAL_REMARKS_1"))
      .alias("FINAL_REMARKS_1")
)






# redefine mismatch AFTER round override
is_mismatch = pl.col("FINAL_REMARKS") == pl.lit("Mismatched")

# ======================================================
# ✅ ADD THIS BLOCK HERE
# RULE 1 — ISIN begins with INF → OPEN ENDED MUTUAL FUND
# ======================================================
df_output = df_output.with_columns(
    pl.when(
        is_mismatch &
        pl.col("ISIN").str.starts_with("INF")
    )
    .then(pl.lit("OPEN ENDED MUTUAL FUND"))
    .otherwise(pl.col("FINAL_REMARKS_1"))
    .alias("FINAL_REMARKS_1")
)



# ===========================================================
# RULE  1A — OPEN ENDED MUTUAL FUND + POAFLAG = N
# If POAFLAG = N:
#    If EXCEPTION = YES → NON POA + EXCEPTION
#    Else               → NON POA
# ===========================================================
df_output = df_output.with_columns(
    pl.when(
        (pl.col("FINAL_REMARKS_1") == pl.lit("OPEN ENDED MUTUAL FUND")) &
        (pl.col("POAFLAG").str.to_uppercase() == pl.lit("N"))
    )
    .then(
        pl.when(pl.col("EXCEPTION") == pl.lit("YES"))
          .then(pl.lit("NON POA + EXCEPTION"))
          .otherwise(pl.lit("NON POA"))
    )
    .otherwise(pl.col("FINAL_REMARKS_1"))
    .alias("FINAL_REMARKS_1")
)


# ===========================================================
# RULE 1B — Remaining OPEN ENDED MUTUAL FUND + EXCEPTION = YES
# After rule A is applied, remaining OPEN ENDED MUTUAL FUND
# with EXCEPTION = YES → EXCEPTION
# ===========================================================
df_output = df_output.with_columns(
    pl.when(
        (pl.col("FINAL_REMARKS_1") == pl.lit("OPEN ENDED MUTUAL FUND")) &
        (pl.col("EXCEPTION") == pl.lit("YES"))
    )
    .then(pl.lit("EXCEPTION"))
    .otherwise(pl.col("FINAL_REMARKS_1"))
    .alias("FINAL_REMARKS_1")
)

# ===========================================================
# RULE 1C — OPEN ENDED MUTUAL FUND + SERIES = EQ 
# → FRACTION QTY / HOLDING NOT OK
# ===========================================================
df_output = df_output.with_columns(
pl.when(
    (pl.col("FINAL_REMARKS_1") == pl.lit("OPEN ENDED MUTUAL FUND")) &
    (pl.col("SERIES").str.to_uppercase() == pl.lit("EQ"))
)
.then(
    pl.when(
        (pl.col("NETQTY_MERGED") % 1) != 0
    )
    .then(pl.lit("FRACTION QTY"))
    .otherwise(pl.lit("HOLDING NOT OK"))
)
.otherwise(pl.col("FINAL_REMARKS_1"))
.alias("FINAL_REMARKS_1")
)



# ===========================================================
# RULE 2 — HAIRCUT/CustVar combination → UNAPPROVED
# ===========================================================
df_output = df_output.with_columns(
    pl.when(
        (pl.col("FINAL_REMARKS") == pl.lit("Mismatched")) & (

            # Case 1: Haircut = 0 AND CustVar = 0 or 100
            ((pl.col("HAIRCUT_COMBINED") == 0) & 
             (pl.col("CustVar").is_in([0, 100]))) |

            # Case 2: Haircut = 100 AND CustVar = 0 or 100
            ((pl.col("HAIRCUT_COMBINED") == 100) & 
             (pl.col("CustVar").is_in([0, 100]))) |

            # NEW CASE: Haircut between 1 and 99 AND CustVar = 100
            ((pl.col("HAIRCUT_COMBINED") > 0) &
             (pl.col("HAIRCUT_COMBINED") < 100) &
             (pl.col("CustVar") == 100))
        )
    )
    .then(pl.lit("UNAPPROVED"))
    .otherwise(pl.col("FINAL_REMARKS_1"))
    .alias("FINAL_REMARKS_1")
)
# ======================================================
# RULE 3 — still mismatch & empty
# POAFLAG = 'N' AND EXCEPTION = 'YES' → NON POA + EXCEPTION
# ======================================================
df_output = df_output.with_columns(
    pl.when(
        is_mismatch &
        (pl.col("FINAL_REMARKS_1") == pl.lit("")) &
        (pl.col("POAFLAG").str.to_uppercase() == pl.lit("N")) &
        (pl.col("EXCEPTION") == pl.lit("YES"))
    )
    .then(pl.lit("NON POA + EXCEPTION"))
    .otherwise(pl.col("FINAL_REMARKS_1"))
    .alias("FINAL_REMARKS_1")
)

# ======================================================
# RULE 4 — still mismatch & empty
# POAFLAG = 'N' AND EXCEPTION = 'NO' → NON POA
# ======================================================
df_output = df_output.with_columns(
    pl.when(
        is_mismatch &
        (pl.col("FINAL_REMARKS_1") == pl.lit("")) &
        (pl.col("POAFLAG").str.to_uppercase() == pl.lit("N")) &
        (pl.col("EXCEPTION") == pl.lit("NO"))
    )
    .then(pl.lit("NON POA"))
    .otherwise(pl.col("FINAL_REMARKS_1"))
    .alias("FINAL_REMARKS_1")
)

# ======================================================
# RULE 5 — still mismatch & empty
# EXCEPTION = 'YES' → EXCEPTION
# ======================================================
df_output = df_output.with_columns(
    pl.when(
        is_mismatch &
        (pl.col("FINAL_REMARKS_1") == pl.lit("")) &
        (pl.col("EXCEPTION") == pl.lit("YES"))
    )
    .then(pl.lit("EXCEPTION"))
    .otherwise(pl.col("FINAL_REMARKS_1"))
    .alias("FINAL_REMARKS_1")
)

# ======================================================
# RULE 6 — still mismatch & empty
# DPAcStatus != 'Active' → DP SUSPENDED / CLOSED
# ======================================================
df_output = df_output.with_columns(
    pl.when(
        is_mismatch &
        (pl.col("FINAL_REMARKS_1") == pl.lit("")) &
        (pl.col("DPAcStatus").str.to_uppercase() != pl.lit("ACTIVE"))
    )
    .then(pl.lit("DP SUSPENDED / CLOSED"))
    .otherwise(pl.col("FINAL_REMARKS_1"))
    .alias("FINAL_REMARKS_1")
)


# ======================================================
# LAST RULE — Any remaining mismatched → HOLDING NOT OK
# ======================================================
df_output = df_output.with_columns(
    pl.when(
        (pl.col("FINAL_REMARKS") == pl.lit("Mismatched")) &
        (pl.col("FINAL_REMARKS_1") == pl.lit(""))
    )
    .then(pl.lit("HOLDING NOT OK"))
    .otherwise(pl.col("FINAL_REMARKS_1"))
    .alias("FINAL_REMARKS_1")
)

print("✔ Final fallback applied: HOLDING NOT OK")



print("✔ FINAL_REMARKS_1 generated successfully!")


# =====================================================================
# 8️⃣ SAVE FINAL RECONCILIATION FILE
# =====================================================================
recon_file = os.path.join(folder, f"Reconciliation_{valid_date}.csv")
print("\n📁 FINAL RECONCILIATION FILE CREATED →", recon_file)

# =====================================================================
# 8️⃣ SAVE FINAL RECONCILIATION FILE AS EXCEL WITH AUTO COLUMN WIDTH
# =====================================================================
excel_file = os.path.join(folder, f"Reconciliation_{valid_date}.xlsx")

import pandas as pd

# Convert Polars → Pandas for ExcelWriter
df_excel = df_output.to_pandas()

with pd.ExcelWriter(excel_file, engine="xlsxwriter") as writer:
    df_excel.to_excel(writer, sheet_name="Reconciliation", index=False)

    worksheet = writer.sheets["Reconciliation"]

    # Auto-adjust column widths
    for col_idx, col_name in enumerate(df_excel.columns):
        max_len = max(
            df_excel[col_name].astype(str).map(len).max(),
            len(col_name)
        ) + 2
        worksheet.set_column(col_idx, col_idx, max_len)

print("\n📘 Excel with adjusted column widths saved:", excel_file)

print("\n==================== RECONCILIATION END ====================\n")


# =====================================================================
#  🔥  GENERATE T1_HOLDING_RECONCILIATION.xlsx (FINAL OUTPUT)
# =====================================================================

print("\n================ GENERATING T1_HOLDING_RECONCILIATION.xlsx ================\n")

# ------------------------------------------------------------
# 1️⃣ DROP UNWANTED COLUMNS
# ------------------------------------------------------------
df_final_recon = df_output.drop([
    "HAIRCUT_F1",
    "HAIRCUT_F2",
    "HAIRCUT_F3"
], strict=False)

# ------------------------------------------------------------
# 2️⃣ MOVE NETQTY_SYMPHONY NEXT TO NETQTY_T1_0319
# ------------------------------------------------------------
cols = df_final_recon.columns

# Build new order
new_cols_order = []
for col in cols:
    new_cols_order.append(col)
    if col == "NETQTY_T1_NOREN":
        new_cols_order.append("NETQTY_SYMPHONY")

# Remove duplicates (prevent duplicate NETQTY_SYMPHONY)
new_cols_order = list(dict.fromkeys(new_cols_order))

df_final_recon = df_final_recon.select(new_cols_order)


# ------------------------------------------------------------
# 3️⃣ SAVE AS EXCEL WITH AUTO COLUMN WIDTH
# ------------------------------------------------------------
excel_file2 = os.path.join(folder, "T1_HOLDING_RECONCILIATION.xlsx")
# =============================================================
# RENAME COLUMNS FOR T1_HOLDING_RECONCILIATION OUTPUT
# =============================================================
df_final_recon = df_final_recon.rename({
    "HAIRCUT_COMBINED": "HAIRCUT_SEC_PLEDGE",
    "NETQTY_MERGED": "NETQTY_SEC_PLEDGE",
    "EXCEPTION" : "EXCEPTION HOLDINGS"
})

df_excel2 = df_final_recon.to_pandas()

with pd.ExcelWriter(excel_file2, engine="xlsxwriter") as writer:
    df_excel2.to_excel(writer, sheet_name="T1_Recon", index=False)

    worksheet = writer.sheets["T1_Recon"]

    # Auto adjust columns
    for col_idx, col_name in enumerate(df_excel2.columns):
        max_len = max(
            df_excel2[col_name].astype(str).map(len).max(),
            len(col_name)
        ) + 2
        worksheet.set_column(col_idx, col_idx, max_len)

print("📘 T1_HOLDING_RECONCILIATION.xlsx generated successfully!")




import os

file_names_used = [
    os.path.basename(file1),
    os.path.basename(file2),
    os.path.basename(search_file),
    os.path.basename(holding_file),
    os.path.basename(t1_file),
    os.path.basename(noren_file) if noren_files else "T1_HOLDING_NOREN - NOT FOUND",
    os.path.basename(symphony_file),
    os.path.basename(risk_file),
    os.path.basename(exception_file)
]


# =====================================================================
# 📧 EMAIL SENDING BLOCK — WITH ATTACHMENT
# =====================================================================
print("\n==================== EMAIL SENDING START ====================\n")

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

SMTP_SERVER = "smtp3.netcore.co.in"
SMTP_PORT   = 587
SMTP_USER   = "kartavya.dhillon@mastertrust.co.in"
SMTP_PASS   = "$Kartavya25*"

TO_LIST = ["rms_del@mastertrust.co.in"]

CC_LIST = []

attachment_path = os.path.join(folder, "T1_HOLDING_RECONCILIATION.xlsx")

# ---------------- Email Message ----------------
msg = MIMEMultipart()
msg["From"] = SMTP_USER
msg["To"] = ", ".join(TO_LIST)
msg["Cc"] = ", ".join(CC_LIST)
msg["Subject"] = "T1 Holdings vs SEC_PLEDGE File Comparison and Morning Testing Update"

files_html = "<br>".join([f"• {name}" for name in file_names_used])


body_html = f"""
Dear Team,<br><br>

We are comparing our T1 holding files with the <b><u>SEC PLEDGE</u></b> files 
using the Kambala, NOREN, and SYMP systems.<br><br>

Any mismatches identified during this comparison will be reviewed and the updated 
logic will be used in the morning testing. This helps us verify in one click 
whether there are any major issues in the holdings.<br><br>

Unapproved holdings, NON-POA holdings, and Exception holdings are not uploaded 
in the T1 file even if present in the SEC_PLEDGE list.<br><br>

<b>Files Used for Today’s Reconciliation:</b><br>
{files_html}<br><br>


Warm regards,<br>
<b>Kartavya Dhillon</b><br>
Master Capital Services Ltd<br>
📞 011-66331000 | Ext. 84
"""
msg.attach(MIMEText(body_html, "html"))

# ---------------- Attachment ----------------
with open(attachment_path, "rb") as f:
    part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
    part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
    msg.attach(part)

# ---------------- Send Email ----------------
server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
server.starttls()
server.login(SMTP_USER, SMTP_PASS)
server.sendmail(SMTP_USER, TO_LIST + CC_LIST, msg.as_string())
server.quit()

print("📧 Email sent successfully with attachment:", attachment_path)



# =====================================================================
# 📦 BACKUP & CLEANUP BLOCK
# =====================================================================
print("\n==================== BACKUP & CLEANUP START ====================\n")

import shutil
from datetime import datetime

# 1️⃣ Construct backup path
backup_root = os.path.join(folder, "BACKUP FILES AUTOMATED")

# Create main backup folder if not exist
os.makedirs(backup_root, exist_ok=True)

# Convert date → DD-MM-YYYY
backup_date = f"{valid_date[0:2]}-{valid_date[2:4]}-{valid_date[4:8]}"

backup_date_folder = os.path.join(backup_root, backup_date)
os.makedirs(backup_date_folder, exist_ok=True)

print("Backup Folder:", backup_date_folder)

# 2️⃣ Move all files except .py, .bat and folders to backup
for item in os.listdir(folder):
    item_path = os.path.join(folder, item)

    # Skip folders inside folder
    if os.path.isdir(item_path):
        continue

    # Skip our code files
    if item.lower().endswith(".py") or item.lower().endswith(".bat"):
        continue

    # Move everything else
    try:
        shutil.move(item_path, backup_date_folder)
        print("Moved →", item)
    except Exception as e:
        print("Failed to move:", item, "Error:", e)

print("\n✔ Backup Completed Successfully!")

# 3️⃣ Keep only .py, .bat & folders in main directory
for item in os.listdir(folder):
    path = os.path.join(folder, item)

    if os.path.isfile(path):
        if not (item.lower().endswith(".py") or item.lower().endswith(".bat")):
            os.remove(path)
            print("Deleted leftover file:", item)

print("\n✔ Cleanup Completed — Only .py, .bat & folders kept.\n")
print("==================== BACKUP & CLEANUP END ====================\n")







