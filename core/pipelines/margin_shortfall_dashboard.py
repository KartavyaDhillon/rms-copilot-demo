"""
core/pipelines/margin_shortfall_dashboard.py — Cash Risk Codes / Margin Shortfall Dashboard
=============================================================================================
Ported from the standalone "D:\\NEW CTCL\\margin_shortfall_pipeline.py" script into a
callable pipeline module, so CTCL vs NMASS's Run 1 can call it directly — same pattern
as core/pipelines/margin_top20.py (imported + called from the `if run_number == 1:`
block in ctcl_vs_nmass.py).

WHAT IT DOES (unchanged from the original script):
  1. Reads the "Update Limits*.xlsx"/"*.csv" file (polars+fastexcel fast path, falls
     back to a pure-Python XML streamer if those aren't installed).
  2. NET LEDGER = Cash + Payin                          (kept only if <= NET_LEDGER_MAX)
  3. Also requires Span == 0 AND Exposure == 0
  4. Percentage = NET LEDGER / GrossCollateral * 100     (kept only if <= PERCENT_MAX)
  5. Looks up Zone from the RISK_REPORT file (ACCOUNTID -> Zone).
  6. Splits flagged accounts by Zone, renders one dashboard PNG per zone group.
  7. Optionally sends each zone's dashboard via WhatsApp (Tata Tele Omnichannel API).
  8. Optionally emails the CSV.

WHAT CHANGED FROM THE ORIGINAL STANDALONE SCRIPT:
  - Wrapped in run_margin_shortfall_dashboard(base_dir, common_dir=None,
    update_limits_file=None, risk_file=None, output_dir=None) instead of a bare
    `if __name__ == "__main__":` block, so it can be imported and called inline.
  - BASE_FOLDER is no longer hardcoded to "D:\\NEW CTCL" — it takes CTCL vs NMASS's
    real BASE_DIR (or whatever base_dir the caller passes).
  - update_limits_file / risk_file can be passed in directly (the exact paths
    ctcl_vs_nmass.py already resolved for its own run), avoiding a second multi-minute
    read of a 100MB+ Update Limits file. Falls back to its own glob-based resolution
    (resolve_source_file / resolve_risk_report_file) if not given — e.g. for standalone
    testing.
  - resolve_risk_report_file() now checks `common_dir` (SA_COMMON_DIR, where
    RISK_REPORT actually lives — it's a shared file across ctcl/ms15/sa) BEFORE
    base_dir, matching the exact fallback order ctcl_vs_nmass.py already uses
    elsewhere in this codebase for RISK_REPORT. The original standalone script only
    ever looked in BASE_FOLDER, which would have silently failed to find RISK_REPORT
    on a real run where it's only in the common folder.
  - THE TWO HARDCODED SECRETS FROM THE ORIGINAL SCRIPT ARE GONE. The original had a
    live WhatsApp bearer token and the sender mailbox's real password hardcoded as
    `os.environ.get(..., "<the actual secret>")` fallback defaults — directly
    contradicting its own docstring ("never hardcode them... treat as compromised and
    rotate immediately"). Both must be rotated with their real providers (Tata Tele /
    the mailbox) since they were exposed to this integration work; there is no
    hardcoded fallback here — if the env vars below aren't set, sending is skipped
    with a clear message instead of silently using a stale/leaked secret.
      WA_AUTH_TOKEN          — Tata Tele Omnichannel bearer token (no fallback)
      MARGIN_SHORTFALL_SENDER_EMAIL / MARGIN_SHORTFALL_SENDER_PASSWORD
                              — falls back to the console's existing shared
                                SENDER_EMAIL / SENDER_PASSWORD (used by CTCL vs NMASS
                                and margin_top20.py already) if these two aren't set,
                                so a distinct mailbox is optional, not required.

Requirements (same as the original):
    pip install pandas polars fastexcel pillow requests
    (lxml only needed as a fallback if polars/fastexcel aren't installed)
"""

import glob
import os
import re
import smtplib
import zipfile
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import requests
from PIL import Image, ImageDraw, ImageFont

try:
    import polars as pl
    _HAS_POLARS = True
except ImportError:
    _HAS_POLARS = False

try:
    from lxml import etree
    _HAS_LXML = True
except ImportError:
    _HAS_LXML = False

# ============================================================
# CONFIG — thresholds/zones unchanged from the original script
# ============================================================
NET_LEDGER_MAX = -1000     # keep rows where NET LEDGER <= this (e.g. -1000 keeps -1001, -5000, ... but not -999)
PERCENT_MAX = -80          # keep rows where Percentage <= this (i.e. -80%, -95%, -8000% ...)

# Zone codes (from the risk report's Zone column) grouped into each dashboard image.
# Any Zone value NOT listed here (e.g. "ZOCHND") is excluded from the per-zone
# dashboard images, but stays in the master CSV.
ZONE_GROUPS = {
    "Delhi Zone": ["ZODELH", "ZOMT", "ZOCDR", "ZO"],
    "Mumbai Zone": ["ZOMUMB"],
    "Ludhiana Zone": ["ZOLUDH"],
}

DASHBOARD_CSV_PREFIX = "CASH_RISK_CODES_Data"
DASHBOARD_PNG_PREFIX = "CASH_RISK_CODES_Dashboard"

# ---- WhatsApp (Tata Tele Business Omnichannel) ----
SEND_WHATSAPP = True
WA_UPLOAD_URL = "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/media"
WA_SEND_URL = "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/messages"
WA_TEMPLATE_NAME = "daily_report"      # must be an approved WhatsApp template
WA_TEMPLATE_LANGUAGE = "en"

# Default/fallback list, only used if the CTCL_SHORTFALL_WA_TO env var is empty
# (e.g. before the "📱 WhatsApp numbers" button on the CTCL vs NMASS dashboard
# box has ever been used to save a list). Once saved from the dashboard, the
# env var is the source of truth — see _whatsapp_numbers() below.
WHATSAPP_NUMBERS = [
    "+917889026088",
    # "+91XXXXXXXXXX",
]
WHATSAPP_ZONE_NUMBERS = {
    # "Delhi Zone": ["+91XXXXXXXXXX"],
    # "Mumbai Zone": ["+91XXXXXXXXXX"],
    # "Ludhiana Zone": ["+91XXXXXXXXXX"],
}


def _whatsapp_numbers():
    """Recipient list for the dashboard sends — CTCL_SHORTFALL_WA_TO (set via the
    dashboard's password-protected "📱 WhatsApp numbers" button, same pattern as
    MS15's own shortage-image recipient list) if set, else WHATSAPP_NUMBERS above."""
    raw = os.getenv("CTCL_SHORTFALL_WA_TO", "").strip()
    if raw:
        return [n.strip() for n in raw.split(",") if n.strip()]
    return WHATSAPP_NUMBERS

# ---- Email (SMTP) ----
SEND_EMAIL = True
EMAIL_SUBJECT = "Cash Risk Codes"
# Default/fallback recipients, only used if CTCL_SHORTFALL_EMAIL_TO /
# CTCL_SHORTFALL_EMAIL_CC are both empty. Once set from the dashboard's
# existing "✉ Email To / CC" button (CTCL vs NMASS box already lists every
# email_keys entry there — see PIPELINES["ctcl"]["email_keys"]["Cash Risk
# Codes"] in core/pipeline_box.py), those env vars are the source of truth —
# see _email_to()/_email_cc() below.
EMAIL_TO = [
    "kartavya.dhillon@mastertrust.co.in",
    # "someone@mastertrust.co.in",
]
EMAIL_CC = [
    # "someone-else@mastertrust.co.in",
]


def _email_to():
    raw = os.getenv("CTCL_SHORTFALL_EMAIL_TO", "").strip()
    if raw:
        return [e.strip() for e in raw.split(",") if e.strip()]
    return EMAIL_TO


def _email_cc():
    raw = os.getenv("CTCL_SHORTFALL_EMAIL_CC", "").strip()
    if raw:
        return [e.strip() for e in raw.split(",") if e.strip()]
    return EMAIL_CC

NEEDED_HEADERS = ["Entity", "Cash", "Payin", "GrossCollateral", "Span", "Exposure"]

SSML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
COL_RE = re.compile(r"([A-Z]+)")


# ============================================================
# 1. LOCATE THE SOURCE FILES (only used when the caller doesn't
#    already have the exact paths — ctcl_vs_nmass.py passes them in directly)
# ============================================================
def resolve_source_file(base_dir):
    candidates = glob.glob(os.path.join(base_dir, "Update Limits*.xlsx")) + \
        glob.glob(os.path.join(base_dir, "Update Limits*.csv"))
    candidates = [c for c in candidates if not os.path.basename(c).startswith("~$")]
    if not candidates:
        raise FileNotFoundError(f"No 'Update Limits*.xlsx' or 'Update Limits*.csv' file found in {base_dir}")
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def resolve_risk_report_file(base_dir, common_dir=None):
    """RISK_REPORT is a shared file — it lives in SA_COMMON_DIR (`common_dir`) on a
    real run, with base_dir only as a fallback for manual-placement/standalone
    testing. Matches the exact order ctcl_vs_nmass.py already uses for RISK_REPORT
    elsewhere in this codebase."""
    if common_dir and os.path.isdir(common_dir):
        candidates = glob.glob(os.path.join(common_dir, "RISK_REPORT*.csv"))
        candidates = [c for c in candidates if not os.path.basename(c).startswith("~$")]
        if candidates:
            candidates.sort(key=os.path.getmtime, reverse=True)
            return candidates[0]
    candidates = glob.glob(os.path.join(base_dir, "RISK_REPORT*.csv"))
    candidates = [c for c in candidates if not os.path.basename(c).startswith("~$")]
    if not candidates:
        raise FileNotFoundError(
            f"No 'RISK_REPORT*.csv' file found in {common_dir or '(no common_dir given)'} or {base_dir}")
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def load_zone_map(risk_report_path):
    """ACCOUNTID -> Zone, from the risk report file."""
    if _HAS_POLARS:
        try:
            pl_df = pl.read_csv(risk_report_path, columns=["ACCOUNTID", "Zone"])
            risk = pd.DataFrame(pl_df.to_dict(as_series=False))
            risk = risk.drop_duplicates(subset="ACCOUNTID", keep="first")
            return dict(zip(risk["ACCOUNTID"], risk["Zone"]))
        except Exception as e:
            print(f"polars fast-read failed for risk report ({e}); falling back to pandas...")
    risk = pd.read_csv(risk_report_path, usecols=["ACCOUNTID", "Zone"], dtype=str)
    risk = risk.drop_duplicates(subset="ACCOUNTID", keep="first")
    return dict(zip(risk["ACCOUNTID"], risk["Zone"]))


def load_status_map(risk_report_path):
    """ACCOUNTID -> ClientStatus, from the risk report file. Returns None if the
    ClientStatus column isn't present (e.g. an older RISK_REPORT export) instead
    of failing the whole run - the Active-status filter is then simply skipped,
    with a clear message printed so that's never silent."""
    try:
        if _HAS_POLARS:
            try:
                pl_df = pl.read_csv(risk_report_path, columns=["ACCOUNTID", "ClientStatus"])
                risk = pd.DataFrame(pl_df.to_dict(as_series=False))
                risk = risk.drop_duplicates(subset="ACCOUNTID", keep="first")
                return dict(zip(risk["ACCOUNTID"], risk["ClientStatus"]))
            except Exception as e:
                print(f"polars fast-read failed for ClientStatus ({e}); falling back to pandas...")
        risk = pd.read_csv(risk_report_path, usecols=["ACCOUNTID", "ClientStatus"], dtype=str)
        risk = risk.drop_duplicates(subset="ACCOUNTID", keep="first")
        return dict(zip(risk["ACCOUNTID"], risk["ClientStatus"]))
    except Exception as e:
        print(f"ClientStatus column not found in RISK_REPORT ({e}) - skipping the Active-status filter.")
        return None


# ============================================================
# 2. READ THE UPDATE LIMITS FILE (polars fast path + fallback)
# ============================================================
def _read_xlsx_lxml(xlsx_path):
    """Pure-Python fallback (no polars/fastexcel needed): streams the sheet's
    XML directly instead of loading the whole workbook into memory."""
    if not _HAS_LXML:
        raise ImportError(
            "Neither 'polars'+'fastexcel' nor 'lxml' is installed. "
            "Run: pip install polars fastexcel   (recommended, much faster)"
        )
    needed = set(NEEDED_HEADERS)
    z = zipfile.ZipFile(xlsx_path)

    shared_strings = []
    with z.open("xl/sharedStrings.xml") as f:
        for _, elem in etree.iterparse(f, events=("end",)):
            if etree.QName(elem).localname == "si":
                shared_strings.append("".join(elem.itertext()))
                elem.clear()

    def cell_value(c):
        t = c.get("t")
        v_elem = c.find(f"{SSML_NS}v")
        is_elem = c.find(f"{SSML_NS}is")
        if t == "s" and v_elem is not None:
            return shared_strings[int(v_elem.text)]
        elif t == "inlineStr" and is_elem is not None:
            return "".join(is_elem.itertext())
        elif v_elem is not None:
            return v_elem.text
        return None

    with z.open("xl/workbook.xml") as f:
        wb_xml = f.read()
    sheet_rid = re.search(rb'<sheet[^>]*r:id="(rId\d+)"', wb_xml).group(1).decode()
    with z.open("xl/_rels/workbook.xml.rels") as f:
        rels_xml = f.read()
    target = re.search(
        rf'<Relationship[^>]*Id="{sheet_rid}"[^>]*Target="([^"]+)"'.encode(), rels_xml
    ).group(1).decode()
    sheet_path = "xl/" + target if not target.startswith("/") else target.lstrip("/")

    header_map = {}
    col_of = {}
    records = []
    with z.open(sheet_path) as f:
        context = etree.iterparse(f, events=("end",), tag=f"{SSML_NS}row")
        for _, row in context:
            row_idx = row.get("r")
            if row_idx == "1":
                for c in row:
                    ref = c.get("r")
                    col_letter = COL_RE.match(ref).group(1)
                    header_map[col_letter] = cell_value(c)
                col_of = {v: k for k, v in header_map.items() if v in needed}
                missing = needed - set(col_of.keys())
                if missing:
                    raise ValueError(f"Columns not found in sheet: {missing}")
            else:
                vals = {}
                wanted_cols = set(col_of.values())
                for c in row:
                    ref = c.get("r")
                    col_letter = COL_RE.match(ref).group(1)
                    if col_letter in wanted_cols:
                        vals[col_letter] = cell_value(c)
                records.append({h: vals.get(col_of[h]) for h in NEEDED_HEADERS})
            row.clear()
            parent = row.getparent()
            if parent is not None:
                while parent.getprevious() is not None:
                    del parent.getparent()[0]

    return pd.DataFrame(records)


def read_ledger_data(path):
    ext = os.path.splitext(path)[1].lower()

    if _HAS_POLARS:
        try:
            if ext == ".xlsx":
                pl_df = pl.read_excel(path, engine="calamine", columns=NEEDED_HEADERS)
            elif ext == ".csv":
                pl_df = pl.read_csv(path, columns=NEEDED_HEADERS)
            else:
                raise ValueError(f"Unsupported file type for the Update Limits data: {ext}")
            return pd.DataFrame(pl_df.to_dict(as_series=False))
        except Exception as e:
            print(f"polars fast-read failed ({e}); falling back to the slower reader...")

    if ext == ".xlsx":
        return _read_xlsx_lxml(path)
    elif ext == ".csv":
        return pd.read_csv(path, usecols=NEEDED_HEADERS)
    else:
        raise ValueError(f"Unsupported file type for the Update Limits data: {ext}")


# ============================================================
# 3. COMPUTE NET LEDGER / PERCENTAGE AND FILTER
# ============================================================
def build_shortfall_table(df, zone_map=None, status_map=None):
    """Returns (table, dropped_inactive_count). dropped_inactive_count is how
    many otherwise-flagged accounts were removed because their RISK_REPORT
    ClientStatus wasn't "Active" (0 if status_map is None, i.e. the column
    wasn't found)."""
    for col in ["Cash", "Payin", "GrossCollateral", "Span", "Exposure"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["NET LEDGER"] = df["Cash"] + df["Payin"]
    df = df[df["NET LEDGER"] <= NET_LEDGER_MAX].copy()          # e.g. -1001, -5000, ... (not -999)

    df = df[(df["Span"] == 0) & (df["Exposure"] == 0)].copy()   # only accounts with no Span/Exposure usage

    df = df[df["GrossCollateral"] != 0].copy()          # avoid divide-by-zero
    df["Percentage"] = (df["NET LEDGER"] / df["GrossCollateral"]) * 100

    df = df[df["Percentage"] <= PERCENT_MAX].copy()
    df = df.sort_values("Percentage")                    # worst first

    # Active-status filter — applied to whatever has survived every threshold
    # filter above. An account not found in the ClientStatus map (unmapped
    # ACCOUNTID) is treated as not-Active and dropped too, same as an explicit
    # non-Active value. Not shown in the dashboard images — only mentioned (as
    # plain English + this count) in the email body.
    dropped_inactive = 0
    if status_map is not None:
        status_series = df["Entity"].map(status_map)
        is_active = status_series.astype(str).str.strip().str.lower().eq("active")
        dropped_inactive = int((~is_active).sum())
        df = df[is_active].copy()

    if zone_map is not None:
        df["Zone"] = df["Entity"].map(zone_map)

    # Whole numbers only in the email/WhatsApp output — round AFTER filtering
    # (filters above compare against NET_LEDGER_MAX/PERCENT_MAX on the exact
    # values, so rounding first could flip a borderline row in or out).
    # Cast to int (not just .round(0)) so the CSV shows "1234", not "1234.0".
    df["NET LEDGER"] = df["NET LEDGER"].round(0).astype("int64")
    df["GrossCollateral"] = df["GrossCollateral"].round(0).astype("int64")
    df["Percentage"] = df["Percentage"].round(0).astype("int64")

    cols = ["Entity", "Zone", "NET LEDGER", "GrossCollateral", "Percentage"] if zone_map is not None \
        else ["Entity", "NET LEDGER", "GrossCollateral", "Percentage"]
    table = df[cols].rename(
        columns={"Entity": "CLIENT CODE", "Zone": "ZONE", "GrossCollateral": "GROSS COLLATERAL", "Percentage": "%"}
    )
    return table, dropped_inactive


# ============================================================
# 4. DASHBOARD IMAGE
# ============================================================
def format_indian(num):
    neg = num < 0
    num = int(round(abs(num)))
    s = str(num)
    if len(s) <= 3:
        out = s
    else:
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        out = ",".join(parts) + "," + last3
    return ("-" if neg else "") + out


def sev_color(pct):
    a = abs(pct)
    if a >= 500:
        return (120, 15, 25)
    elif a >= 200:
        return (196, 30, 40)
    elif a >= 120:
        return (230, 85, 40)
    else:
        return (235, 140, 30)


def _font(bold, size):
    for base in (
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(base, size)
        except Exception:
            continue
    return ImageFont.load_default()


# A single image taller than ~7000px gets rejected by some delivery/upload
# pipelines (and WhatsApp itself dislikes extremely tall images), so the
# table is paginated - each page is a separate, self-contained PNG.
ROWS_PER_PAGE = 140


def render_dashboard(table, source_file, out_path, zone_label=None):
    """Renders `table` as one or more dashboard PNGs.
    Returns a list of file paths actually written (page 1, page 2, ...)."""
    font_title = _font(True, 40)
    font_sub = _font(False, 22)
    font_kpi_val = _font(True, 30)
    font_kpi_lbl = _font(False, 18)
    font_head = _font(True, 20)
    font_row = _font(False, 19)
    font_row_b = _font(True, 19)
    font_footer = _font(False, 16)

    NAVY, NAVY_2, TEAL = (18, 38, 74), (28, 56, 104), (20, 110, 140)
    BG, ROW_WHITE, ROW_ALT, BORDER = (244, 246, 250), (255, 255, 255), (238, 242, 248), (222, 227, 234)
    TEXT_DARK, TEXT_MUTE, WHITE = (28, 34, 46), (110, 120, 135), (255, 255, 255)

    WIDTH, MARGIN, ROW_H = 1360, 40, 46
    HEADER_H, KPI_H, TABLE_HEAD_H, FOOTER_H, TOP_PAD = 150, 130, 54, 60, 24

    n_total = len(table)
    n_pages = max(1, -(-n_total // ROWS_PER_PAGE))  # ceil div

    total_net_ledger = table["NET LEDGER"].sum() if n_total else 0
    avg_pct = table["%"].mean() if n_total else 0
    worst = table.iloc[0] if n_total else None
    mtime = datetime.fromtimestamp(os.path.getmtime(source_file))

    base, ext = os.path.splitext(out_path)
    written_paths = []

    for page in range(n_pages):
        chunk = table.iloc[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]
        n_rows = len(chunk)
        total_height = (
            TOP_PAD + HEADER_H + 20
            + (KPI_H + 30 if page == 0 else 0)
            + TABLE_HEAD_H + max(n_rows, 1) * ROW_H + FOOTER_H + 30
        )

        img = Image.new("RGB", (WIDTH, total_height), BG)
        draw = ImageDraw.Draw(img)

        def text_w(text, font):
            b = draw.textbbox((0, 0), text, font=font)
            return b[2] - b[0]

        y = TOP_PAD
        draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + HEADER_H], fill=NAVY)
        draw.rectangle([MARGIN, y, MARGIN + 10, y + HEADER_H], fill=TEAL)
        title = "CASH RISK CODES"
        if zone_label:
            title += f"  \u2014  {zone_label.upper()}"
        if n_pages > 1:
            title += f"  (Page {page + 1}/{n_pages})"
        f_title = font_title
        while text_w(title, f_title) > WIDTH - 2 * MARGIN - 340 and f_title.size > 22:
            f_title = _font(True, f_title.size - 2)
        draw.text((MARGIN + 40, y + 24), title, fill=WHITE, font=f_title)
        draw.text((MARGIN + 40, y + 76), f"NET LEDGER \u2264 {NET_LEDGER_MAX:,.0f}, Span=0, Exposure=0, and % \u2264 {PERCENT_MAX}", fill=(200, 212, 230), font=font_sub)
        draw.text((WIDTH - MARGIN - 40, y + 30), f"As of {mtime.strftime('%d-%m-%Y')}", fill=WHITE, font=font_sub, anchor="ra")
        draw.text((WIDTH - MARGIN - 40, y + 62), f"Snapshot {mtime.strftime('%H:%M')} IST", fill=(200, 212, 230), font=font_sub, anchor="ra")
        draw.text((WIDTH - MARGIN - 40, y + 96), os.path.basename(source_file), fill=(160, 176, 200), font=font_footer, anchor="ra")
        y += HEADER_H + 20

        if page == 0:
            cards = [
                ("CLIENTS FLAGGED", f"{n_total:,}", TEAL),
                ("TOTAL NET LEDGER SHORTFALL", "\u20b9 " + format_indian(total_net_ledger), (196, 30, 40)),
                ("AVERAGE SHORTFALL %", f"{avg_pct:,.0f}%", (230, 85, 40)),
                ("WORST CASE", f"{worst['CLIENT CODE']}  ({worst['%']:,.0f}%)" if worst is not None else "-", (120, 15, 25)),
            ]
            card_gap = 20
            card_w = (WIDTH - 2 * MARGIN - card_gap * 3) / 4
            for i, (label, value, color) in enumerate(cards):
                cx = MARGIN + i * (card_w + card_gap)
                draw.rectangle([cx, y, cx + card_w, y + KPI_H], fill=ROW_WHITE, outline=BORDER, width=2)
                draw.rectangle([cx, y, cx + 8, y + KPI_H], fill=color)
                draw.text((cx + 24, y + 20), label, fill=TEXT_MUTE, font=font_kpi_lbl)
                f = font_kpi_val
                while text_w(value, f) > card_w - 48 and f.size > 16:
                    f = _font(True, f.size - 2)
                draw.text((cx + 24, y + 58), value, fill=TEXT_DARK, font=f)
            y += KPI_H + 30

        has_zone = "ZONE" in table.columns
        if has_zone:
            col_x = {
                "CLIENT CODE": MARGIN + 20,
                "ZONE": MARGIN + 300,
                "NET LEDGER": MARGIN + 460,
                "GROSS COLLATERAL": MARGIN + 780,
                "%": WIDTH - MARGIN - 40,
            }
        else:
            col_x = {
                "CLIENT CODE": MARGIN + 20,
                "NET LEDGER": MARGIN + 420,
                "GROSS COLLATERAL": MARGIN + 720,
                "%": WIDTH - MARGIN - 40,
            }
        draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + TABLE_HEAD_H], fill=NAVY_2)
        draw.text((col_x["CLIENT CODE"], y + TABLE_HEAD_H / 2), "CLIENT CODE", fill=WHITE, font=font_head, anchor="lm")
        if has_zone:
            draw.text((col_x["ZONE"], y + TABLE_HEAD_H / 2), "ZONE", fill=WHITE, font=font_head, anchor="lm")
        draw.text((col_x["NET LEDGER"], y + TABLE_HEAD_H / 2), "NET LEDGER (\u20b9)", fill=WHITE, font=font_head, anchor="lm")
        draw.text((col_x["GROSS COLLATERAL"], y + TABLE_HEAD_H / 2), "GROSS COLLATERAL (\u20b9)", fill=WHITE, font=font_head, anchor="lm")
        draw.text((col_x["%"], y + TABLE_HEAD_H / 2), "%", fill=WHITE, font=font_head, anchor="rm")
        y += TABLE_HEAD_H

        for idx, (_, r) in enumerate(chunk.iterrows()):
            row_bg = ROW_WHITE if idx % 2 == 0 else ROW_ALT
            draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + ROW_H], fill=row_bg)
            draw.line([MARGIN, y + ROW_H, WIDTH - MARGIN, y + ROW_H], fill=BORDER, width=1)
            color = sev_color(r["%"])
            draw.ellipse([MARGIN + 20, y + ROW_H / 2 - 6, MARGIN + 32, y + ROW_H / 2 + 6], fill=color)
            draw.text((col_x["CLIENT CODE"] + 24, y + ROW_H / 2), str(r["CLIENT CODE"]), fill=TEXT_DARK, font=font_row_b, anchor="lm")
            if has_zone:
                draw.text((col_x["ZONE"], y + ROW_H / 2), str(r["ZONE"]) if pd.notna(r["ZONE"]) else "-", fill=TEXT_DARK, font=font_row, anchor="lm")
            draw.text((col_x["NET LEDGER"], y + ROW_H / 2), format_indian(r["NET LEDGER"]), fill=(196, 30, 40), font=font_row, anchor="lm")
            draw.text((col_x["GROSS COLLATERAL"], y + ROW_H / 2), format_indian(r["GROSS COLLATERAL"]), fill=TEXT_DARK, font=font_row, anchor="lm")
            pct_text = f"{r['%']:,.0f}%"
            pw = text_w(pct_text, font_row_b) + 24
            px1 = WIDTH - MARGIN - 40
            px0 = px1 - pw
            draw.rounded_rectangle([px0, y + 7, px1, y + ROW_H - 7], radius=10, fill=color)
            draw.text(((px0 + px1) / 2, y + ROW_H / 2), pct_text, fill=WHITE, font=font_row_b, anchor="mm")
            y += ROW_H

        y += 20
        draw.line([MARGIN, y, WIDTH - MARGIN, y], fill=BORDER, width=1)
        y += 14
        footer_zone = f" in {zone_label}" if zone_label else ""
        draw.text((MARGIN, y), f"Generated {datetime.now().strftime('%d-%b-%Y %H:%M')}  \u2022  {n_total} client account(s){footer_zone} breached the {PERCENT_MAX}% Net Ledger / Gross Collateral threshold",
                   fill=TEXT_MUTE, font=font_footer)

        page_path = out_path if n_pages == 1 else f"{base}_p{page + 1}{ext}"
        img.save(page_path, optimize=True)
        written_paths.append(page_path)

    return written_paths


# ============================================================
# 5. WHATSAPP SEND (Tata Tele Business Omnichannel API)
# ============================================================
def _upload_whatsapp_media(image_path, wa_auth_token):
    with open(image_path, "rb") as fh:
        files = {"file": (os.path.basename(image_path), fh, "image/png")}
        headers = {"Authorization": f"Bearer {wa_auth_token}", "accept": "application/json"}
        resp = requests.post(WA_UPLOAD_URL, headers=headers, files=files)
    if resp.status_code not in (200, 201):
        print(f"UPLOAD FAILED for {image_path}: {resp.status_code} | {resp.text}")
        return None
    return resp.json().get("id") or resp.json().get("mediaHandle")


def send_whatsapp_dashboard(image_paths, numbers, wa_auth_token, label=""):
    """Uploads each dashboard page once, then sends every page to every number."""
    if not wa_auth_token:
        print("WA_AUTH_TOKEN is not set (env var) - skipping WhatsApp send.")
        return
    if not numbers:
        print(f"No WhatsApp numbers configured for {label or 'this dashboard'} - skipping.")
        return
    if not image_paths:
        print(f"No dashboard pages to send for {label or 'this dashboard'} - skipping.")
        return

    media_ids = []
    for path in image_paths:
        media_id = _upload_whatsapp_media(path, wa_auth_token)
        if media_id:
            media_ids.append((path, media_id))

    current_date = datetime.now().strftime("%d-%m-%Y")
    n_pages = len(media_ids)

    for number in numbers:
        for page_idx, (path, media_id) in enumerate(media_ids, start=1):
            body_label = f"RISK_CODES {label}".strip()
            if n_pages > 1:
                body_label += f" (page {page_idx}/{n_pages})"
            message_data = {
                "to": number,
                "type": "template",
                "source": "external",
                "template": {
                    "name": WA_TEMPLATE_NAME,
                    "language": {"code": WA_TEMPLATE_LANGUAGE},
                    "components": [
                        {"type": "header", "parameters": [{"type": "image", "image": {"id": media_id}}]},
                        {"type": "body", "parameters": [
                            {"type": "text", "text": body_label},
                            {"type": "text", "text": current_date},
                        ]},
                    ],
                },
            }
            resp = requests.post(
                WA_SEND_URL,
                headers={"Authorization": f"Bearer {wa_auth_token}", "Content-Type": "application/json"},
                json=message_data,
            )
            if resp.status_code == 200:
                print(f"Sent {label} page {page_idx}/{n_pages} to {number}")
            else:
                print(f"FAILED {label} page {page_idx}/{n_pages} to {number}: {resp.status_code} | {resp.text}")


# ============================================================
# 6. EMAIL (SMTP)
# ============================================================
def send_email_report(attachments, zone_summary_lines, sender_email, sender_password, dropped_inactive=0):
    email_to = _email_to()
    email_cc = _email_cc()
    if not sender_password:
        print("No SMTP password available (MARGIN_SHORTFALL_SENDER_PASSWORD / SENDER_PASSWORD "
              "both unset) - skipping email send.")
        return
    if not email_to:
        print("Email To is empty (CTCL_SHORTFALL_EMAIL_TO / EMAIL_TO both unset) - skipping email send.")
        return

    smtp_server = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = ", ".join(email_to)
    if email_cc:
        msg["Cc"] = ", ".join(email_cc)
    msg["Subject"] = EMAIL_SUBJECT

    body_lines = [
        "Hi,",
        "",
        "Please find attached today's Cash Risk Codes report.",
        "",
        "How this report is built, in simple terms:",
        "",
        "1. For every client account in the Update Limits file, Net Ledger is calculated as Cash + Payin.",
        "",
        f"2. Only accounts where Net Ledger is {NET_LEDGER_MAX:,} or more negative (for example -1,001, -5,000, and so on) are kept - smaller shortfalls are ignored.",
        "",
        "3. From those, only accounts where both Span and Exposure are exactly 0 are kept (this leaves out accounts that still have open derivative positions).",
        "",
        "4. For the remaining accounts, we work out what percentage of the account's Gross Collateral this Net Ledger shortfall represents: Percentage = (Net Ledger / Gross Collateral) x 100.",
        "",
        f"5. Only accounts where this percentage is {PERCENT_MAX}% or worse (for example -80%, -90%, -8000%, and so on) are flagged as a margin shortfall.",
        "",
        "6. Each flagged account's Zone is looked up from the Risk Report file, matched on its Account ID.",
        "",
        f"7. Each flagged account's ClientStatus is also checked in the Risk Report file - only accounts marked \"Active\" are kept; the rest are removed from this list entirely. {dropped_inactive} account(s) were removed this run because of that check.",
        "",
        "8. The flagged accounts are then split by zone. Accounts in any zone not listed below are not counted in a zone group, but are still included in the attached CSV.",
        "",
        "Zone-wise breakdown for this run:",
    ] + zone_summary_lines + [
        "",
        "Attached:",
        f"- {DASHBOARD_CSV_PREFIX}.csv (full list, all zones)",
        "",
        "This is an automated report.",
    ]
    msg.attach(MIMEText("\n".join(body_lines), "plain"))

    for path in attachments:
        with open(path, "rb") as f:
            data = f.read()
        if path.lower().endswith(".png"):
            part = MIMEImage(data, name=os.path.basename(path))
        else:
            part = MIMEApplication(data, Name=os.path.basename(path))
        part["Content-Disposition"] = f'attachment; filename="{os.path.basename(path)}"'
        msg.attach(part)

    all_recipients = email_to + email_cc
    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=60) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, all_recipients, msg.as_string())
        print(f"Email sent to {', '.join(all_recipients)}")
    except Exception as e:
        print(f"EMAIL FAILED: {e}")


# ============================================================
# MAIN — callable entry point (was `if __name__ == "__main__":`)
# ============================================================
def run_margin_shortfall_dashboard(base_dir, common_dir=None, update_limits_file=None,
                                    risk_file=None, output_dir=None):
    """Callable version of the original standalone script's main block.

    base_dir            — CTCL vs NMASS's BASE_DIR (where Update Limits normally is)
    common_dir           — SA_COMMON_DIR, checked first for RISK_REPORT (shared file)
    update_limits_file   — exact path already resolved by the caller, skips re-glob
    risk_file             — exact path already resolved by the caller, skips re-glob
    output_dir           — where the CSV/PNGs get written; defaults to base_dir

    Returns a dict summary: {"ok": bool, "flagged": int, "csv": path, "error": str|None}
    """
    output_dir = output_dir or base_dir
    os.makedirs(output_dir, exist_ok=True)
    run_date_str = datetime.now().strftime("%d%m%Y")
    dashboard_csv = os.path.join(output_dir, f"{DASHBOARD_CSV_PREFIX}_{run_date_str}.csv")

    wa_auth_token = os.getenv("WA_AUTH_TOKEN", "")
    sender_email = os.getenv("MARGIN_SHORTFALL_SENDER_EMAIL") or os.getenv("SENDER_EMAIL", "")
    sender_password = os.getenv("MARGIN_SHORTFALL_SENDER_PASSWORD") or os.getenv("SENDER_PASSWORD", "")

    source_file = update_limits_file or resolve_source_file(base_dir)
    print("Reading:", source_file)
    raw_df = read_ledger_data(source_file)
    print("Total rows read:", len(raw_df))

    risk_report_file = risk_file or resolve_risk_report_file(base_dir, common_dir)
    print("Reading:", risk_report_file)
    zone_map = load_zone_map(risk_report_file)
    print("Zone lookups loaded:", len(zone_map))
    status_map = load_status_map(risk_report_file)
    if status_map is not None:
        print("ClientStatus lookups loaded:", len(status_map))

    shortfall_table, dropped_inactive = build_shortfall_table(raw_df, zone_map=zone_map, status_map=status_map)
    # NET LEDGER / GROSS COLLATERAL / % are already rounded to whole numbers
    # inside build_shortfall_table() — nothing further to round here.
    print("Rows breaching threshold (all zones):", len(shortfall_table))
    if status_map is not None:
        print("Rows dropped for not being ClientStatus=Active:", dropped_inactive)

    shortfall_table.to_csv(dashboard_csv, index=False)
    print("CSV written:", dashboard_csv)

    all_dashboard_pages = []       # [(zone_label, [page_path, ...]), ...]
    zone_summary_lines = []
    grouped_codes = set()

    for zone_label, codes in ZONE_GROUPS.items():
        grouped_codes.update(codes)
        zone_table = shortfall_table[shortfall_table["ZONE"].isin(codes)]
        zone_summary_lines.append(f"- {zone_label}: {len(zone_table)} account(s) flagged")

        if len(zone_table) == 0:
            print(f"No accounts flagged in {zone_label} - skipping dashboard image and WhatsApp send.")
            continue

        zone_slug = zone_label.replace(" ", "_")
        zone_png = os.path.join(output_dir, f"{DASHBOARD_PNG_PREFIX}_{zone_slug}_{run_date_str}.png")
        pages = render_dashboard(zone_table, source_file, zone_png, zone_label=zone_label)
        for p in pages:
            print("Dashboard image written:", p)
        all_dashboard_pages.append((zone_label, pages))

    other_count = len(shortfall_table[~shortfall_table["ZONE"].isin(grouped_codes)])
    if other_count:
        zone_summary_lines.append(f"- Other zones (CSV only, not shown as a separate dashboard): {other_count} account(s)")

    if SEND_WHATSAPP:
        default_numbers = _whatsapp_numbers()
        for zone_label, pages in all_dashboard_pages:
            numbers = WHATSAPP_ZONE_NUMBERS.get(zone_label) or default_numbers
            send_whatsapp_dashboard(pages, numbers, wa_auth_token, label=zone_label)

    if SEND_EMAIL:
        send_email_report([dashboard_csv], zone_summary_lines, sender_email, sender_password,
                          dropped_inactive=dropped_inactive)

    return {"ok": True, "flagged": len(shortfall_table), "csv": dashboard_csv, "error": None}


if __name__ == "__main__":
    # Standalone testing only — mirrors the original script's config.
    _base = os.getenv("MARGIN_SHORTFALL_TEST_DIR", r"D:\NEW CTCL")
    run_margin_shortfall_dashboard(_base)
