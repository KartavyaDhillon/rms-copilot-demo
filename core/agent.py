"""
core/agent.py  —  RMS Ops Agent  (v2: all pipelines + file-reading + RAG context)
==================================================================================

What this does now:
  • Knows ALL 10 pipelines as callable tools
  • Can read output files from any pipeline and summarise/analyse them
  • Every lookup_rule call goes through RAG (data/circulars/*.txt)
  • Maintains conversation history for multi-turn (passed in from caller)
  • Resilient: parallel_tool_calls=False, retries once on malformed tool call

Tools available:
  lookup_rule          — RAG search over your circulars/SOPs
  calculate_penalty    — margin shortfall penalty calc
  run_peak_margin      — run i01/i02/i03/i04 snapshot
  run_concentration    — run concentration compute
  run_ctcl             — run CTCL vs NMASS
  run_cnc_f11          — run CNC F11
  run_mtf_order        — run MTF Order File
  run_ftp_download     — run FTP daily download
  read_output_file     — read a named output file and return its content for analysis
  list_output_files    — list files in any of your pipeline output folders
"""
import json
import os
from groq import Groq, BadRequestError
from core.config import GROQ_API_KEY
from core.compliance import answer_compliance
from core.penalty import calculate_penalty

_client = Groq(api_key=GROQ_API_KEY)
MODEL          = "llama-3.3-70b-versatile"   # primary (smart, more tokens)
FALLBACK_MODEL = "llama-3.1-8b-instant"      # used if primary hits its daily limit


# ── Folder map (used by list_output_files / read_output_file) ─────────────────
FOLDER_MAP = {
    "peak_margin":        os.getenv("PEAK_FOLDER",          r"C:\Peak Margin"),
    "concentration":      os.getenv("CONCENTRATION_FOLDER", r"D:\CONCENTRATION"),
    "ctcl":               os.getenv("CTCL_NMASS_FOLDER",    r"D:\CTCL VS NMASS"),
    "cnc_f11":            os.getenv("CNC_F11_FOLDER",       r"C:\CNC_F11"),
    "mtf_order":          os.getenv("MTF_ORDER_FOLDER",     r"D:\MTF ORDER FILE"),
    "ftp":                os.getenv("FTP_BASE_LOCAL",       r"C:\FTP FILES"),
    "margin_shortage":    os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes"),
    "margin_top20":       os.getenv("MARGIN_TOP20_FOLDER",  r"D:\MARGIN TOP20"),
    "payout":             os.getenv("PAYOUT_FOLDER",        r"D:\PAYOUT ASHISH"),
    "inspection":         os.getenv("INSPECTION_FOLDER",    r"D:\INSPECTION"),
    "code2":              os.getenv("CODE2_FOLDER",         r"D:\TUESDAY THURSDAY"),
    "uploads":            os.getenv("UPLOADS_FOLDER",       r"D:\CLAUDE\rms-copilot\uploads"),
}

# The MAIN output file pattern for each pipeline. When the user asks about
# "the output" of a pipeline, prefer a file matching this pattern over just
# the newest-modified file (which might be an input like RISK_REPORT).
OUTPUT_PATTERNS = {
    "ctcl":          "FULL_FILE_CTCL_VS_NMASS",
    "concentration": "FULLFILE_CONCENTRATION", # main violation output
    "cnc_f11":       "CNC_F11_DATED",
    "mtf_order":     "ORD_",                    # KAMB/O319 order files
    "payout":        "PAYOUT_CHECKING",
    "margin_top20":  "Top20",
    "code2":         "Output",
    "inspection":    "INSPECTION_OUTPUT",
    "margin_shortage": "Shortage_vs_CC_Deposit",   # 15-minutes output (NOT zone files)
}



# ══════════════════════════════════════════════════════════════════════════
# PIPELINE REGISTRY — permanent, structured knowledge of every pipeline.
# This is NOT data (which changes daily). This is knowledge of the CODE:
# what each pipeline does, what it reads, what it outputs, and where to look.
# Injected into the agent's system prompt so it ALWAYS knows it.
# ══════════════════════════════════════════════════════════════════════════
PIPELINE_REGISTRY = {
    "ctcl": {
        "name": "CTCL vs NMASS",
        "does": "Compares each client's trading-terminal limit (CTCL) against their actual back-office deposit (NMASS). More limit than deposit = the client can trade beyond what they've lodged = LIMIT EXCESS.",
        "reads": ["CM/FO/CO ClientLevelCollaterals (NSCCL)", "Update Limits file", "ClientNetEffectiveDeposits (NCDEX)", "RISK_REPORT (input)", "KAMBALA_ALLOCATION_LIST.xlsx (permanent reference)"],
        "output_file": "FULL_FILE_CTCL_VS_NMASS_NN.xlsx  (NN = run number 01-05)",
        "output_location": "D:\\CTCL VS NMASS\\BACKUPFILES AUTOMATED\\<date>\\<run>\\  (NOT the top level, which only has .bat scripts)",
        "key_columns": {
            "Zone": "client's zone: ZODELH, ZOLUDH, ZOCHND, ZOMUMB, ZOMT, ZOCDR",
            "ACCOUNTID": "the client code",
            "MTF_STATUS": "MTF status carried over from RISK_REPORT",
            "KAMBALA ALLOCATION STATUS": "Y if the account is on the Kambala allocation list, else N",
            "DEPOSIT NMASS": "actual deposit (back office)",
            "DEPOSIT CTCL": "trading terminal limit (UnrealizedEqMargin already deducted)",
            "DIFFERENCE(G-F)": "sign-aware. DEPOSIT CTCL >= 0 -> CTCL - NMASS (positive = LIMIT EXCESS). DEPOSIT CTCL < 0 -> CTCL + NMASS, informational only, NEVER an excess.",
            "REMARKS": "CPCODE / VIRTUAL ID / LIMIT EXCESS / OK",
        },
        "how_to_count_shortfall": "Count rows where REMARKS contains 'LIMIT EXCESS'. (Files produced before the rename say 'SHORTFALL' instead — match either when looking at history.) For a specific zone, also filter Zone == that code.",
        "remarks_priority": "CPCODE (any CPCode value; overrides everything) -> VIRTUAL ID (blank Zone or on the virtual list; cleared if DEPOSIT NMASS > 0) -> LIMIT EXCESS (CTCL >= 0 and DIFFERENCE > 0) / OK (CTCL >= 0 and DIFFERENCE <= 0) -> OK (CTCL < 0, always).",
        "important": "Do NOT rank DIFFERENCE(G-F) alone to find the biggest excess — debit-balance rows (DEPOSIT CTCL < 0) carry a large POSITIVE difference but are always OK. Filter REMARKS = LIMIT EXCESS first.",
        "note": "RISK_REPORT_*.csv is an INPUT, not the output. The output is always FULL_FILE_CTCL_VS_NMASS.",
    },
    "payout": {
        "name": "Payout Check",
        "does": "Flags clients who requested a fund withdrawal but have negative Available Margin (must not be paid out).",
        "reads": ["today's Payout Request Log (BOAPI)", "Update Limits file"],
        "output_file": "PAYOUT_CHECKING_DDMMYYYY_HHMMSS.xlsx",
        "output_location": "D:\\PAYOUT ASHISH (and its backups)",
        "key_columns": {
            "Account ID": "client code",
            "Available Margin": "negative = at risk, payout should be blocked",
            "Request Amount": "withdrawal requested",
            "Zone": "client zone",
        },
        "how_to_count_shortfall": "Count rows where Available Margin < 0.",
    },
    "concentration": {
        "name": "Concentration",
        "does": "Finds clients whose MTF position in one scrip exceeds the tier limit (Nifty50 50%, BSE200 30%, BSE500 20%, other 10%) or the family-group limit.",
        "reads": ["Nifty50/BSE200/BSE500 index files", "KORP shortage file", "EXCEPTION.xlsx", "zone position files"],
        "output_file": "violation report (zone-wise)",
        "output_location": "D:\\CONCENTRATION (and BACKUP AUTOMATION subfolders)",
        "key_columns": {
            "Zone": "client zone",
            "ACCOUNTID": "client/account code",
            "Scrip_Concentration[(L/M)*100]": "the SCRIP concentration % — THIS is what 'highest concentration' means",
            "Cover % on Portfolio(100+((S/U)*100))": "the PORTFOLIO cover %, a DIFFERENT column",
        },
        "how_to_count_shortfall": "For 'highest scrip concentration' sort by Scrip_Concentration descending. Many clients may TIE at 100% — if so COUNT or LIST them, never report just one. Violations = rows present in the file.",
    },
    "cnc_f11": {
        "name": "CNC F11",
        "does": "Finds clients with a long MTF/NRML position AND an offsetting short MTFS/CNC position in the same scrip (F11 conversion candidates).",
        "reads": ["PositionBook.csv"],
        "output_file": "CNC_F11_DATED_<date>.xlsx (Sheet4 = matched pairs)",
        "output_location": "C:\\CNC_F11 (and backups)",
        "key_columns": {"ClientID": "client", "Symbol": "scrip", "NetQty": "NetBuyQty - NetSellQty"},
        "how_to_count_shortfall": "Not a shortfall report — count matched pairs in Sheet4.",
    },
    "mtf_order": {
        "name": "MTF Order File",
        "does": "For margin-short MTF clients, generates SELL order files (which stocks, how many shares) to cover the shortfall.",
        "reads": ["MTFPOSITION_DDMMYYYY.csv", "MTF_RISK_SHORTAGE_REPORT", "RISK_REPORT"],
        "output_file": "ORD_KAMB_MTFS_*.csv and ORD_O319_MTFS_*.csv",
        "output_location": "D:\\MTF ORDER FILE (and backups)",
        "key_columns": {"AccountId": "client", "Symbol": "stock to sell", "Qty": "shares to sell"},
        "how_to_count_shortfall": "Count distinct AccountId in the order files.",
    },
    "code2": {
        "name": "Code2 Shortage (Tue/Thu)",
        "does": "Computes clients with a shortage after expiry margin is included. Runs only Tuesday/Thursday.",
        "reads": ["FULL_FILE_CTCL_VS_NMASS", "Update Limits"],
        "output_file": "Output.xlsx",
        "output_location": "D:\\TUESDAY THURSDAY (and backups)",
        "key_columns": {"ACCOUNTID": "client", "SHORTAGE": "negative = shortage", "EXPIRY MARGIN": "margin locked for expiry"},
        "how_to_count_shortfall": "Count rows where SHORTAGE < 0.",
    },
    "peak_margin": {
        "name": "Peak Margin",
        "does": "Builds cumulative Peak1-Peak4 columns showing each client's margin at the four daily snapshots (i01 10:30, i02 12:30, i03 13:45, i04 15:30).",
        "reads": ["NSE extranet snapshot files"],
        "output_file": "F_filtered.png (FO clients) and C_filtered.png (CM/FO/Total)",
        "output_location": "C:\\Peak Margin",
        "key_columns": {"Peak1-Peak4": "margin at each snapshot"},
        "how_to_count_shortfall": "Not a shortfall report — it shows peak margin utilisation.",
        "note": "i01 must run first each day (it resets the report). Running i02 before i01 leaves Peak1=0.",
    },
    "margin_shortage": {
        "name": "Margin Shortage (15 minutes)",
        "does": "Every 15 minutes, merges CM/FO/CO collaterals + NCDEX deposits + MCX limits, computes each client's deposit vs total margin, flags shortfalls, sends zone emails + WhatsApp alerts (Margin Warning % > 105).",
        "reads": ["CM/FO/CO ClientLevelCollaterals", "ClientNetEffectiveDeposits", "MCX 90% file", "RISK_REPORT"],
        "output_file": "Shortage_vs_CC_Deposit.xlsx (main) + Shortage_vs_CC_Deposit_<ZONE>.xlsx (zone files)",
        "output_location": "D:\\15 minutes (and BACKUPFILES AUTOMATED\\<date>\\<run> subfolders)",
        "key_columns": {
            "ACCOUNTID": "client code",
            "DEPOSIT NMASS": "total deposit (CM+FO+CO+NCDEX+MCX)",
            "TOTAL MARGIN": "total margin used",
            "DIFFERENCE(F-G)": "DEPOSIT NMASS minus TOTAL MARGIN; NEGATIVE = shortfall (opposite sign to CTCL!)",
            "REMARKS": "SHORTFALL / OK - No Difference / OK - Excess Difference",
            "MARGIN WARNING %": "derivative margin / deposit * 100 (rows sorted by this, highest first)",
            "FINAL SHORT CM ADJ(H+J)": "shortfall after CM adjustment; <0 used for zone files",
        },
        "how_to_count_shortfall": "Shortfalls = rows where REMARKS contains SHORTFALL (i.e. DIFFERENCE(F-G) < 0). The BIGGEST shortage = the MOST NEGATIVE DIFFERENCE(F-G) — use action 'bottom' on DIFFERENCE(F-G), NOT 'top'.",
        "whatsapp_logic": "WhatsApp alerts go to clients with MARGIN WARNING % > 105 (filter that column > 105 = ELIGIBLE clients). But ELIGIBLE is NOT the same as SENT: an alert is only actually sent the first time per day it crosses, or again if it moved by 10 percentage-points since the last alert. The actual sent-list lives in the margin_alert_tracker DB table (core/db.py / core/db_postgres.py — get_margin_alert/set_margin_alert), NOT in this output file. So from the file you can report who is ELIGIBLE (MARGIN WARNING % > 105), but you CANNOT know who was actually messaged — say so if asked.",
        "note": "This is a DIFFERENT report from CTCL vs NMASS. Sign is reversed: here negative DIFFERENCE(F-G) = shortfall. SHORTFALL accounts and WhatsApp-eligible (MARGIN WARNING % > 105) are DIFFERENT sets — do not conflate them.",
    },
}


# ══════════════════════════════════════════════════════════════════════════
# COLUMN LINEAGE — exact source + formula for EVERY output column, from the
# real code. Looked up on demand by the explain_column tool so the agent can
# answer "where does column X come from / how is it calculated" precisely,
# without bloating every prompt.
# ══════════════════════════════════════════════════════════════════════════
COLUMN_LINEAGE = {
    "ctcl": {
        "_output": "FULL_FILE_CTCL_VS_NMASS",
        "ACCOUNTID": "The client code (Entity / Client Code from the source files, renamed).",
        "DEPOSIT NMASS": "Total collateral lodged = CM_SUM + FO_SUM + CO_SUM + DEP_SUM + MARGIN_LIMIT. "
                         "CM/FO/CO_SUM each = Cash allocated (b) + Cash-Others (c) + Non-cash Securities (d) "
                         "from the CM/FO/CO_ClientLevelCollaterals files, summed per client. "
                         "DEP_SUM = 'Total Collateral (Cash and Non Cash)' from ClientNetEffectiveDeposits. "
                         "MARGIN_LIMIT = 'Client Margin Limit' from the margin file.",
        "DEPOSIT CTCL": "Total limit from the 'Update Limits' back-office file: it sums these columns "
                        "horizontally per row (Payin, Payout, Collateral(Total), Cash, DaylongCash, "
                        "MtfDaylongCash, CacDaylongCash, UnclearedCash, BrokerCollateral, EQT/DER/COM/FX "
                        "Collateral, AUX cash columns, Remarks Amount/Text), then DEDUCTS UnrealizedEqMargin "
                        "(negative values treated as 0 and ignored; positive values subtracted), "
                        "then sums per Entity.",
        "DIFFERENCE(G-F)": "Sign-aware, rounded 2dp. DEPOSIT CTCL >= 0 → CTCL − NMASS (POSITIVE = LIMIT "
                           "EXCESS, limit exceeds collateral lodged). DEPOSIT CTCL < 0 (debit balance) → "
                           "CTCL + NMASS; that value is informational only and such rows are ALWAYS OK.",
        "MTF_STATUS": "Carried over from RISK_REPORT by ACCOUNTID. Blank if the risk file lacks the column.",
        "KAMBALA ALLOCATION STATUS": "Y if ACCOUNTID appears in Sheet1 of KAMBALA_ALLOCATION_LIST.xlsx, else N.",
        "Zone": "Joined from RISK_REPORT by ACCOUNTID (left join). Also TRADING_SOFTWARE_TYPE, CPCode, PISAccount.",
        "REMARKS": "Priority: CPCODE (CPCode has any value — overrides everything) → VIRTUAL ID (blank zone "
                   "or on the virtual list from KAMBALA_ALLOCATION_LIST.xlsx 'VIRTUAL ID' sheet; cleared if "
                   "DEPOSIT NMASS > 0) → LIMIT EXCESS (CTCL >= 0 and DIFFERENCE > 0) / OK (CTCL >= 0 and "
                   "DIFFERENCE <= 0) → OK (CTCL < 0, always OK regardless of size).",
    },
    "margin_shortage": {
        "_output": "Shortage_vs_CC_Deposit",
        "ACCOUNTID": "Client Code, renamed.",
        "DEPOSIT NMASS": "CM_SUM + FO_SUM + CO_SUM + DEP_SUM + MARGIN_LIMIT (collateral sum incl. MCX limit). "
                         "CM/FO/CO_SUM = Cash allocated (b) + Cash-others (c) + Non-cash Securities (d) per file. "
                         "DEP_SUM = Total Collateral from ClientNetEffectiveDeposits. MARGIN_LIMIT = Client Margin "
                         "Limit from the MCX file.",
        "TOTAL MARGIN": "CM_MIN_MARGIN + FO_MIN_MARGIN + CO_MIN_MARGIN + CLIENTNET_MARGIN + MCX_MARGIN_UTIL. "
                        "The MIN_MARGIN values = 'Minimum Margins (a)' from each collateral file; CLIENTNET_MARGIN "
                        "= 'Margin' from ClientNetEffectiveDeposits; MCX_MARGIN_UTIL = 'Margin Utilization' from MCX file.",
        "DIFFERENCE(F-G)": "DEPOSIT NMASS − TOTAL MARGIN, rounded. NEGATIVE = SHORTFALL (opposite sign to CTCL).",
        "CM SEGMENT MARGIN": "= CM_MIN_MARGIN (the CM-only minimum margin).",
        "FINAL SHORT CM ADJ(H+J)": "= DIFFERENCE(F-G) + CM_MIN_MARGIN (shortfall after adding back CM margin; "
                                   "<0 selects rows for the zone shortfall files).",
        "DERIVATIVE MARGIN USED": "= TOTAL MARGIN − CM SEGMENT MARGIN (the FO/NCDEX/MCX portion).",
        "MARGIN WARNING %": "= DERIVATIVE MARGIN USED ÷ DEPOSIT NMASS × 100 (0 if deposit is 0). Rows sorted by "
                            "this descending. Drives the WhatsApp alert (>105 = eligible).",
        "REMARKS": "SHORTFALL (DIFFERENCE(F-G) < 0) / OK - No Difference (=0) / OK - Excess Difference (>0).",
    },
    "concentration": {
        "_output": "FULLFILE_CONCENTRATION",
        "Boscrip / ISIN": "Scrip code and ISIN (ISIN joined from the BSE 500 constituents list by scrip code).",
        "PORTFOLIO VALUE": "Value of the client's holding in that scrip (from the holdings file).",
        "FIN_UPTODATE_MTF": "The MTF-funded value for that scrip/client.",
        "REMARKS": "Which index basket the scrip's ISIN belongs to: Nifty50 / BSE200 / BSE500 / Others "
                   "(by ISIN set membership; ScripName=='BSE' forced to BSE200).",
        "remarks_1": "Violation/OK by tiered portfolio limits: both FIN_UPTODATE_MTF and PORTFOLIO VALUE > ₹1cr "
                     "→ Violation; index stock (REMARKS != Others) and ≤ ₹1cr → OK; Others and ≤ ₹50L → OK; else Violation.",
        "SCRIP CONCENTRATION COUNT": "Count of distinct scrips (Boscrip) per AccountID.",
        "Scrip_Concentration[(L/M)*100]": "The scrip-level concentration percentage.",
    },
    "payout": {
        "_output": "PAYOUT_CHECKING",
        "Available Margin": "From source = deposit minus margin used. The output keeps only rows where this is < 0 "
                            "(the accounts to block).",
        "MarginUsed": "Margin used, carried from source.", "TOTAL DEPOSIT": "Total deposit, carried from source.",
    },
    "cnc_f11": {
        "_output": "CNC_F11_DATED",
        "NetQty": "Net position quantity in the CNC segment (from PositionBook). Output keeps only NetQty < 0 (net short).",
        "ClientID": "Client code, normalised (numeric kept as int, else string).",
    },
}


def explain_column(pipeline: str, column: str = "") -> dict:
    """Return the source + formula for a column (or all columns) of a pipeline output."""
    pl_key = pipeline.strip().lower()
    table = COLUMN_LINEAGE.get(pl_key)
    if not table:
        return {"error": f"No column lineage for '{pipeline}'. Known: {list(COLUMN_LINEAGE.keys())}"}
    if not column:
        cols = {k: v for k, v in table.items() if not k.startswith("_")}
        return {"pipeline": pl_key, "output_file": table.get("_output"), "columns": cols}
    # match the column name: exact first, then substring
    cl = column.strip().lower()
    for col, desc in table.items():            # pass 1: exact
        if not col.startswith("_") and cl == col.lower():
            return {"pipeline": pl_key, "column": col, "how_it_is_built": desc}
    for col, desc in table.items():            # pass 2: substring (longer names win)
        if col.startswith("_"):
            continue
        if cl in col.lower() or col.lower() in cl:
            return {"pipeline": pl_key, "column": col, "how_it_is_built": desc}
    return {"pipeline": pl_key, "column": column,
            "note": f"No exact match. Available columns: {[c for c in table if not c.startswith('_')]}"}


# Aliases: every word/phrase that should map to a pipeline. Deterministic routing —
# the agent NEVER guesses which process; this dictionary decides.
PROCESS_ALIASES = {
    "ctcl": ["ctcl", "nmass", "ctcl vs nmass", "ctcl nmass", "deposit", "ctcl limit"],
    "payout": ["payout", "withdrawal", "payment request", "fund withdrawal", "payout check"],
    "concentration": ["concentration", "conc", "scrip limit", "index limit", "nifty50", "bse200", "bse500", "family limit"],
    "cnc_f11": ["cnc", "f11", "cnc f11", "positionbook", "position book", "conversion"],
    "mtf_order": ["mtf order", "order file", "mtf", "kamb", "o319", "sell order", "margin shortage order"],
    "code2": ["code2", "code 2", "tuesday thursday", "expiry margin", "expiry shortage"],
    "peak_margin": ["peak margin", "peak", "i01", "i02", "i03", "i04", "snapshot", "10:30", "12:30", "13:45", "15:30"],
    "margin_shortage": ["margin shortage", "15 minutes", "15 minute", "15 min", "shortage vs cc",
                        "margin warning", "nsccl download", "ncdex download", "collateral download"],
    "ftp": ["ftp", "ftp download", "kamb file", "noren", "symf"],
    "inspection": ["inspection", "payout inspection", "historical payout"],
    "margin_top20": ["top20", "top 20", "margin top20", "utilisation report"],
}



# ══════════════════════════════════════════════════════════════════════════
# DEEP KNOWLEDGE — full plain-English code logic per process, loaded from
# data/circulars/pipeline_knowledge.txt. The router injects the COMPLETE
# section for the detected process (not a summary), so the agent deeply
# understands that process's logic, files, and errors. Edit the .txt file
# to make the agent smarter — no code change needed.
# ══════════════════════════════════════════════════════════════════════════
_DEEP_KNOWLEDGE = None

_DEEP_HEADER_MAP = [
    ("PAYOUT INSPECTION", "inspection"),
    ("MARGIN TOP20", "margin_top20"),
    ("MARGIN SHORTAGE", "margin_shortage"),
    ("PEAK MARGIN", "peak_margin"),
    ("CONCENTRATION", "concentration"),
    ("CTCL VS NMASS", "ctcl"),
    ("CODE2", "code2"),
    ("CNC F11", "cnc_f11"),
    ("MTF ORDER", "mtf_order"),
    ("FTP", "ftp"),
    ("PAYOUT", "payout"),
    ("ERROR", "_errors"),
]

def _load_deep_knowledge():
    global _DEEP_KNOWLEDGE
    if _DEEP_KNOWLEDGE is not None:
        return _DEEP_KNOWLEDGE
    _DEEP_KNOWLEDGE = {}
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "data", "circulars", "pipeline_knowledge.txt"),
        os.path.join(os.getcwd(), "data", "circulars", "pipeline_knowledge.txt"),
    ]
    text = None
    for c in candidates:
        if os.path.exists(c):
            try:
                text = open(c, encoding="utf-8", errors="replace").read()
                break
            except Exception:
                pass
    if not text:
        return _DEEP_KNOWLEDGE
    # split into sections on lines of ==== followed by a header line
    import re as _re
    parts = _re.split(r"\n=+\n", text)
    current_key = None
    for part in parts:
        stripped = part.strip()
        head = stripped.split("\n")[0].upper() if stripped else ""
        matched = None
        for token, key in _DEEP_HEADER_MAP:
            if token in head:
                matched = key
                break
        # A real header part is just the title line (short). Long parts that
        # happen to START with a process word (e.g. "PAYOUT DOWNLOAD — ...")
        # are BODY content and must be appended, not treated as headers.
        if matched and len(stripped) < 120:
            current_key = matched
            _DEEP_KNOWLEDGE.setdefault(current_key, "")
        elif current_key:
            _DEEP_KNOWLEDGE[current_key] += part
    return _DEEP_KNOWLEDGE


def _is_data_question(text: str) -> bool:
    t = text.lower()
    words = ["how many", "highest", "lowest", "which client", "which scrip", "total",
             "sum", "average", "outcome", "output", "shortfall", "count", "top ",
             "max", "min", "list the", "show me", "read", "violation", "breach"]
    return any(w in t for w in words)


def detect_process(text: str):
    """
    Deterministically detect which pipeline(s) the user's text refers to.
    Returns a list of pipeline keys, longest/most-specific alias first.
    This is NOT an LLM guess — it is exact keyword matching, so it cannot pick wrong.
    """
    t = " " + text.lower() + " "
    hits = []  # (alias_length, pipeline_key)
    for key, aliases in PROCESS_ALIASES.items():
        for a in aliases:
            # word-ish boundary check
            if a in t:
                hits.append((len(a), key))
    # dedupe keeping the longest alias match order
    seen, ordered = set(), []
    for _, key in sorted(hits, key=lambda x: -x[0]):
        if key not in seen:
            seen.add(key); ordered.append(key)
    return ordered


def process_knowledge(key: str) -> str:
    """Return the full plain-English knowledge block for one process."""
    p = PIPELINE_REGISTRY.get(key)
    if not p:
        return ""
    lines = [f"PROCESS: {p['name']} (key: {key})",
             f"What it does: {p['does']}",
             f"Reads: {', '.join(p.get('reads', []))}",
             f"Output file: {p['output_file']}",
             f"Where it's stored: {p['output_location']}",
             "Columns: " + "; ".join(f"{k} = {v}" for k, v in p.get('key_columns', {}).items()),
             f"How to count/measure: {p['how_to_count_shortfall']}"]
    if p.get('note'):
        lines.append(f"Important: {p['note']}")
    return "\n".join(lines)


def _registry_summary():
    """Compact text version of the registry for the system prompt."""
    lines = []
    for key, p in PIPELINE_REGISTRY.items():
        lines.append(f"\n● {p['name']}  (pipeline key: '{key}')")
        lines.append(f"   What it does: {p['does']}")
        lines.append(f"   Output file: {p['output_file']}")
        lines.append(f"   Where: {p['output_location']}")
        cols = "; ".join(f"{k} = {v}" for k, v in p.get('key_columns', {}).items())
        lines.append(f"   Key columns: {cols}")
        lines.append(f"   To count: {p['how_to_count_shortfall']}")
        if p.get('note'):
            lines.append(f"   ⚠ {p['note']}")
    return "\n".join(lines)


def _find_output_file(folder, pipeline, filename):
    """
    Locate the correct output file for a pipeline.

    Returns (filepath, display_name) or (None, error_message).

    Strategy when filename == "latest":
      1. Look in top-level folder for files matching the pipeline's OUTPUT pattern.
      2. Look in BACKUPFILES AUTOMATED / dated subfolders. Backups are organised as
         BACKUP.../<YYYY-MM-DD>/<run>/file.xlsx . We pick the file from the most
         RECENT DATE FOLDER (parsed from the folder name) — NOT by file mtime,
         because shutil.copy2 preserves old mtimes and would mislead us.
      3. Among files in the chosen date folder, pick the highest run number.
    """
    import re
    from datetime import datetime
    READABLE = (".csv", ".xlsx", ".xls")
    pat = OUTPUT_PATTERNS.get(pipeline)

    if filename != "latest":
        # explicit filename: search top-level + all backup depths
        for root_d, _dirs, fnames in os.walk(folder):
            if filename in fnames:
                return os.path.join(root_d, filename), filename
        return None, f"File '{filename}' not found anywhere under {folder}."

    # Collect candidates with a (date, run, path, name) key
    dated = []          # (date_obj, run_int, path, name)
    undated = []        # (mtime, path, name) — fallback only

    def matches_pattern(name):
        return (pat is None) or (pat.lower() in name.lower())

    # top-level files (rare for outputs, but check)
    for f in os.listdir(folder):
        fp = os.path.join(folder, f)
        if os.path.isfile(fp) and f.lower().endswith(READABLE) and matches_pattern(f):
            undated.append((os.path.getmtime(fp), fp, f))

    # walk backup subfolders
    for entry in os.listdir(folder):
        ep = os.path.join(folder, entry)
        if os.path.isdir(ep) and "BACKUP" in entry.upper():
            for root_d, _dirs, fnames in os.walk(ep):
                # try to parse a date from the path components
                date_obj = None
                run_int = -1
                for part in root_d.replace("\\", "/").split("/"):
                    # date folder like 2026-06-08 (CTCL style, year first)
                    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", part)
                    if m:
                        try:
                            date_obj = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                        except ValueError:
                            pass
                    # date folder like 12-06-2026 (Concentration style, day first)
                    m2 = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", part)
                    if m2:
                        try:
                            date_obj = datetime(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
                        except ValueError:
                            pass
                    # date folder like "12 Jun 2026" (Payout style)
                    m3 = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", part)
                    if m3:
                        try:
                            date_obj = datetime.strptime(part, "%d %b %Y")
                        except ValueError:
                            try:
                                date_obj = datetime.strptime(part, "%d %B %Y")
                            except ValueError:
                                pass
                    # run folder like 01, 02
                    if re.fullmatch(r"\d{1,2}", part):
                        run_int = max(run_int, int(part))
                for f in fnames:
                    if f.lower().endswith(READABLE) and matches_pattern(f):
                        fp = os.path.join(root_d, f)
                        if date_obj:
                            dated.append((date_obj, run_int, fp, f))
                        else:
                            undated.append((os.path.getmtime(fp), fp, f))

    # Among matches, prefer the file whose name is CLOSEST to the bare pattern
    # (fewest extra chars), so "Shortage_vs_CC_Deposit.xlsx" beats the zone file
    # "Shortage_vs_CC_Deposit_DELH.xlsx", and "FULLFILE_CONCENTRATION_..." beats
    # "CONCENTRATION_ZODELH_...". Lower "extra length" = better main-file match.
    def extra_len(fname):
        base = os.path.splitext(fname)[0]
        if pat and pat.lower() in base.lower():
            # chars in the name beyond the pattern + a date (digits) are "suffix noise"
            import re as _re
            stripped = _re.sub(r"[_\-]?\d{6,8}", "", base)  # remove date tokens
            return len(stripped) - len(pat)
        return 999

    if dated:
        # newest date, highest run, then closest-to-pattern (main file beats zone file)
        dated.sort(key=lambda x: (x[0], x[1], -extra_len(x[3])), reverse=True)
        return dated[0][2], dated[0][3]
    if undated:
        undated.sort(key=lambda x: (x[0], -extra_len(x[2])), reverse=True)
        return undated[0][1], undated[0][2]

    return None, (f"No output file matching '{pat or 'data'}' found in {folder} "
                  f"or its backup subfolders.")

# ── TOOL DEFINITIONS ──────────────────────────────────────────────────────────
TOOLS = [
    # ── Knowledge ─────────────────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "lookup_rule",
        "description": "Search the knowledge base for rules, process logic, limits, or error fixes.",
        "parameters": {"type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"]}}},

    {"type": "function", "function": {
        "name": "calculate_penalty",
        "description": "Calculate the SEBI/exchange rupee penalty for a margin shortfall.",
        "parameters": {"type": "object",
            "properties": {
                "shortfall_amount": {"type": "number", "description": "shortfall in INR"},
                "days_short":       {"type": "integer", "description": "consecutive days short"}},
            "required": ["shortfall_amount", "days_short"]}}},

    # ── File access ────────────────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "list_output_files",
        "description": "List files in a pipeline folder, including backup subfolders where the real outputs live.",
        "parameters": {"type": "object",
            "properties": {
                "pipeline": {"type": "string",
                    "enum": list(FOLDER_MAP.keys()),
                    "description": "which pipeline's folder to list"}},
            "required": ["pipeline"]}}},

    {"type": "function", "function": {
        "name": "read_output_file",
        "description": "Preview a file's structure (columns + first rows). Not for counting — use ask_data for numbers.",
        "parameters": {"type": "object",
            "properties": {
                "pipeline": {"type": "string", "enum": list(FOLDER_MAP.keys()),
                    "description": "which pipeline folder the file lives in"},
                "filename":  {"type": "string",
                    "description": (
                        "Exact filename, or 'latest' to auto-pick the newest file "
                        "in that folder. E.g. 'PAYOUT_CHECKING_08062026.xlsx' or 'latest'."
                    )}},
            "required": ["pipeline", "filename"]}}},

    # ── Pipeline runners ───────────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "run_peak_margin",
        "description": "ONLY call when the user EXPLICITLY says run/execute/start. EXECUTES the peak margin pipeline. Do NOT call for questions like 'tell me about' or 'how many'. Run one peak-margin snapshot (i01=10:30, i02=12:30, i03=13:45, i04=15:30). Returns status + totals.",
        "parameters": {"type": "object",
            "properties": {"index": {"type": "string", "enum": ["i01","i02","i03","i04"]}},
            "required": ["index"]}}},

    {"type": "function", "function": {
        "name": "run_concentration",
        "description": "ONLY call when the user EXPLICITLY says run/execute/start. EXECUTES the concentration check. Do NOT call for questions like 'tell me about' or 'how many'. Run the concentration check (tier + family limits). Returns violation summary.",
        "parameters": {"type": "object", "properties": {}}}},

    {"type": "function", "function": {
        "name": "run_ctcl",
        "description": "ONLY call when the user EXPLICITLY says run/execute/start. EXECUTES the CTCL vs NMASS pipeline. Do NOT call for questions like 'tell me about' or 'how many'. Run CTCL vs NMASS (deposit diff, zone emails, TOP20 on run 1, payout check).",
        "parameters": {"type": "object", "properties": {}}}},

    {"type": "function", "function": {
        "name": "run_cnc_f11",
        "description": "ONLY call when the user EXPLICITLY says run/execute/start. EXECUTES the CNC F11 processor. Do NOT call for questions like 'tell me about' or 'how many'. Run CNC F11 (PositionBook buy/sell matching -> Sheet4 -> email).",
        "parameters": {"type": "object", "properties": {}}}},

    {"type": "function", "function": {
        "name": "run_mtf_order",
        "description": "ONLY call when the user EXPLICITLY says run/execute/start. EXECUTES the MTF order file generator. Do NOT call for questions like 'tell me about' or 'how many'. Run MTF Order File (margin matching -> KAMB + O319 order files -> emails).",
        "parameters": {"type": "object", "properties": {}}}},

    {"type": "function", "function": {
        "name": "run_ftp_download",
        "description": "ONLY call when the user EXPLICITLY says run/execute/start. EXECUTES the FTP download job. Do NOT call for questions like 'tell me about' or 'how many'. Run FTP download (KAMB/O319/NOREN/SYMF -> 8 destination folders).",
        "parameters": {"type": "object", "properties": {}}}},

    {"type": "function", "function": {
        "name": "ask_data",
        "description": "Answer ANY plain-English data question with EXACT pandas-computed numbers (counts, totals, max/min, top-N, account lookups). Primary tool for all data questions.",
        "parameters": {"type": "object",
            "properties": {
                "pipeline": {"type": "string", "enum": list(FOLDER_MAP.keys()),
                    "description": "which pipeline's output to query (e.g. ctcl, payout)"},
                "question": {"type": "string",
                    "description": "the user's full question, in plain English, exactly as asked"}},
            "required": ["pipeline", "question"]}}},

    {"type": "function", "function": {
        "name": "analyze_file",
        "description": "Standard computed breakdown of a data file (remarks/zone counts, sums, top accounts). Prefer ask_data for specific questions.",
        "parameters": {"type": "object",
            "properties": {
                "pipeline": {"type": "string",
                    "description": "Pipeline folder name, or 'uploads' for an uploaded file.",
                    "enum": list(FOLDER_MAP.keys())},
                "filename": {"type": "string",
                    "description": "Exact filename or 'latest'."},
                "question": {"type": "string",
                    "description": "What the user wants to know, in plain words. "
                                   "E.g. 'how many clients have shortfall', 'total shortfall amount', "
                                   "'which zone has most violations'. Used to decide what to compute."}},
            "required": ["pipeline", "filename", "question"]}}},

    # ── General-purpose file access (anything the user drops in the uploads folder) ──
    {"type": "function", "function": {
        "name": "search_code",
        "description": "Search the actual source code of all pipelines/scripts. Use for 'which script creates/writes X', 'where is Y defined/handled', 'show the logic of Z'. Returns file, line number, snippet.",
        "parameters": {"type": "object",
            "properties": {"query": {"type": "string",
                "description": "what to find — a filename, function, string, or concept"}},
            "required": ["query"]}}},

    {"type": "function", "function": {
        "name": "daily_summary",
        "description": "Generate the RMS daily summary — counts shortfalls, concentration breaches, WhatsApp-eligible and blocked accounts from today's real output files. Use for 'what happened today', 'daily summary', 'morning report', 'give me an overview'.",
        "parameters": {"type": "object", "properties": {}}}},

    {"type": "function", "function": {
        "name": "explain_column",
        "description": "Explain where an output COLUMN comes from and how it is calculated (its source files and formula). Use for 'how is DEPOSIT NMASS calculated', 'where does MARGIN WARNING % come from', 'what is DIFFERENCE(G-F)', 'what columns are in the CTCL output'. Leave column empty to list all columns of a pipeline.",
        "parameters": {"type": "object",
            "properties": {
                "pipeline": {"type": "string",
                    "enum": ["ctcl", "margin_shortage", "concentration", "payout", "cnc_f11"],
                    "description": "which pipeline's output"},
                "column": {"type": "string", "description": "the column name, e.g. 'DEPOSIT NMASS' (empty = list all)"}},
            "required": ["pipeline"]}}},

    {"type": "function", "function": {
        "name": "explain_client",
        "description": "Explain WHY a specific client is in shortage / has a high margin warning / got a WhatsApp alert, by decomposing the real numbers from the output file. Use for 'why is client X short', 'why did X get a whatsapp', 'why is X margin warning so high'. Specify the client code and which report.",
        "parameters": {"type": "object",
            "properties": {
                "client_code": {"type": "string", "description": "the ACCOUNTID, e.g. FIXTAR0001"},
                "report": {"type": "string", "enum": ["margin_shortage", "ctcl"],
                    "description": "margin_shortage for 15-min/WhatsApp questions, ctcl for CTCL shortfall"}},
            "required": ["client_code", "report"]}}},

    {"type": "function", "function": {
        "name": "list_indexed_code",
        "description": "List every script file the agent can read/search, with line and chunk counts. Use when the user asks 'what code/scripts can you read', 'which files are indexed', or wants to verify coverage.",
        "parameters": {"type": "object", "properties": {}}}},

    {"type": "function", "function": {
        "name": "check_input_readiness",
        "description": "Check whether a pipeline's required INPUT files for TODAY are present. Use before running, or when the user asks 'can I run X now' / 'what's missing'.",
        "parameters": {"type": "object",
            "properties": {"pipeline": {"type": "string",
                "enum": ["ctcl", "cnc_f11", "mtf_order", "concentration"],
                "description": "which pipeline to check"}},
            "required": ["pipeline"]}}},

    {"type": "function", "function": {
        "name": "list_uploaded_files",
        "description": "List files the user uploaded (general scratch folder, non-pipeline).",
        "parameters": {"type": "object", "properties": {}}}},

    {"type": "function", "function": {
        "name": "read_uploaded_file",
        "description": "Read an uploaded file (csv/xlsx/txt/log/json/md). filename='latest' for newest.",
        "parameters": {"type": "object",
            "properties": {"filename": {"type": "string",
                "description": "Exact filename in the uploads folder, or 'latest'."}},
            "required": ["filename"]}}},
]


# ══════════════════════════════════════════════════════════════════════════
# TOOL SUBSETTING — send the model ONLY the tools relevant to the question.
# All 21 tool definitions cost ~2460 tokens; on the free tier's 6000 TPM that
# (re-sent every agent step) is the main cause of rate-limit errors. We pick a
# small relevant subset per turn so a typical question sends ~700 tok of tools.
# _ACTIVE_TOOLS is set per turn in run_agent_chat; None = send all (safe default).
# ══════════════════════════════════════════════════════════════════════════
_ACTIVE_TOOLS = None

# Always-available core (data questions, the common case)
_CORE_TOOL_NAMES = {"ask_data", "analyze_file", "read_output_file", "list_output_files"}

def _select_tools(user_msg: str):
    """Return the subset of TOOLS relevant to this message (keeps tokens low)."""
    t = (user_msg or "").lower()
    keep = set(_CORE_TOOL_NAMES)

    # run/execute a pipeline
    if any(w in t for w in ("run ", "execute", "start ", "trigger", "download now",
                            "kick off", "launch")):
        keep |= {"run_peak_margin", "run_concentration", "run_ctcl", "run_cnc_f11",
                 "run_mtf_order", "run_ftp_download",
                 "check_input_readiness"}
    # readiness
    if any(w in t for w in ("ready", "can i run", "missing", "what's needed", "whats needed")):
        keep |= {"check_input_readiness"}
    # source code questions
    if any(w in t for w in ("script", "source code", "which file", "where is", ".py",
                            "function", "code for", "indexed")):
        keep |= {"search_code", "list_indexed_code"}
    # column lineage
    if any(w in t for w in ("column", "calculated", "formula", "how is", "where does",
                            "comes from", "derived")):
        keep |= {"explain_column"}
    # why / root cause
    if any(w in t for w in ("why is", "why did", "why does", "reason", "because",
                            "explain why", "what caused")):
        keep |= {"explain_client", "explain_column"}
    # daily summary
    if any(w in t for w in ("today", "summary", "overview", "what happened", "morning",
                            "daily")):
        keep |= {"daily_summary"}
    # rules / penalty / circulars
    if any(w in t for w in ("rule", "penalty", "circular", "limit", "regulation", "sebi", "exchange")):
        keep |= {"lookup_rule", "calculate_penalty"}
    # uploaded files
    if any(w in t for w in ("upload", "uploaded", "this file", "attached")):
        keep |= {"list_uploaded_files", "read_uploaded_file"}

    subset = [tool for tool in TOOLS if tool["function"]["name"] in keep]
    # safety: never send an empty tool list
    return subset if subset else TOOLS



# The LLM translates plain English into a structured query spec (which column,
# filter, operation). pandas then EXECUTES it for an exact answer. The LLM
# never counts or sums — so the numbers are always real, but you can ask
# anything in plain English.
# ══════════════════════════════════════════════════════════════════════════
import json as _json

_QUERY_TRANSLATE_SYSTEM = """You translate a plain-English question about a data file into a STRICT JSON query spec. Output ONLY the JSON. Never answer the question or invent numbers.
Fields (all optional): "filter":[{"column":..,"op":"contains|starts_with|ends_with|equals|gt|lt|gte|lte","value":..}] (AND), "action":"count|sum|average|max|min|top|bottom|lookup|list", "target_column", "group_by", "n".
Rules:
- Use ONLY columns from the provided list; map words to the closest real column ("shortfall amount"/"excess amount"->DIFFERENCE(G-F), client/account->ACCOUNTID, zone->Zone, remarks/status->REMARKS).
- CTCL vs NMASS: "shortfall"/"limit excess"/"over limit" as a status -> filter REMARKS contains LIMIT EXCESS. (Margin Shortage / 15-min is a DIFFERENT report and still uses SHORTFALL — see its examples below.)
- CTCL vs NMASS: ALWAYS pair a DIFFERENCE(G-F) ranking with a REMARKS contains LIMIT EXCESS filter. Debit-balance rows (DEPOSIT CTCL < 0) carry a large POSITIVE DIFFERENCE but are always OK, so ranking the column alone returns a wrong answer.
- highest/largest/maximum/top -> action "top" (or "max"); lowest/smallest -> "bottom"/"min". These words mean SORTING, NEVER a filter value.
- "how many"->count, "total"->sum, "average"->average. "everything about account X"->lookup + filter ACCOUNTID equals X.
- Zones: Delhi=ZODELH, Mumbai=ZOMUMB, Ludhiana=ZOLUDH, Chandigarh=ZOCHND (filter Zone equals code).
Examples:
Q: which client has the maximum shortfall/limit excess, show details -> {"filter":[{"column":"REMARKS","op":"contains","value":"LIMIT EXCESS"}],"action":"top","target_column":"DIFFERENCE(G-F)","n":1}
Q: how many ZODELH clients have shortfall/limit excess -> {"filter":[{"column":"Zone","op":"equals","value":"ZODELH"},{"column":"REMARKS","op":"contains","value":"LIMIT EXCESS"}],"action":"count"}
Q: show me everything about account JDA563 -> {"filter":[{"column":"ACCOUNTID","op":"equals","value":"JDA563"}],"action":"lookup"}
Q: total shortfall/limit excess amount -> {"filter":[{"column":"REMARKS","op":"contains","value":"LIMIT EXCESS"}],"action":"sum","target_column":"DIFFERENCE(G-F)"}
Q: how many accounts are on the kambala list -> {"filter":[{"column":"KAMBALA ALLOCATION STATUS","op":"equals","value":"Y"}],"action":"count"}
Q: how many CPCODE / custodian accounts -> {"filter":[{"column":"REMARKS","op":"contains","value":"CPCODE"}],"action":"count"}
Q: top 5 clients by deposit -> {"action":"top","target_column":"DEPOSIT NMASS","n":5}
Q: which scrip/client has highest concentration -> {"action":"top","target_column":"Scrip_Concentration","n":5}  (concentration means the Scrip_Concentration column, NOT Cover % on Portfolio)
Q: highest limit excess among accounts WITH collateral (excluding zero-deposit) -> {"filter":[{"column":"DEPOSIT NMASS","op":"gt","value":"0"},{"column":"REMARKS","op":"contains","value":"LIMIT EXCESS"}],"action":"top","target_column":"DIFFERENCE(G-F)","n":5}
Q: how many clients have 100% concentration -> {"filter":[{"column":"Scrip_Concentration","op":"gte","value":"100"}],"action":"count"}
Q: show/list accounts starting with PAL -> {"filter":[{"column":"ACCOUNTID","op":"starts_with","value":"PAL"}],"action":"list"}
Q: (margin_shortage / 15-min) biggest/highest shortage -> {"filter":[{"column":"REMARKS","op":"contains","value":"SHORTFALL"}],"action":"bottom","target_column":"DIFFERENCE(F-G)","n":1}  (in this report shortfall is NEGATIVE DIFFERENCE(F-G), so biggest shortage = most negative = bottom)
Q: (margin_shortage) how many have shortfall -> {"filter":[{"column":"REMARKS","op":"contains","value":"SHORTFALL"}],"action":"count"}
Q: (margin_shortage) how many are eligible for WhatsApp / above 105 -> {"filter":[{"column":"MARGIN WARNING %","op":"gt","value":"105"}],"action":"count"}
Q: (margin_shortage) list clients eligible for WhatsApp alert -> {"filter":[{"column":"MARGIN WARNING %","op":"gt","value":"105"}],"action":"list"}
Q: details of codes/accounts beginning with PAL -> {"filter":[{"column":"ACCOUNTID","op":"starts_with","value":"PAL"}],"action":"list"}
Output ONLY the JSON object."""


def _translate_to_query(question: str, columns: list) -> dict:
    """Ask the LLM to convert English -> query spec JSON. No numbers involved."""
    prompt = (f"Columns available in the file: {columns}\n\n"
              f"User question: {question}\n\n"
              f"Output the JSON query spec:")
    try:
        resp = _client.chat.completions.create(
            model=FALLBACK_MODEL,   # small model is fine for translation; saves tokens
            messages=[{"role": "system", "content": _QUERY_TRANSLATE_SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=0)
        text = resp.choices[0].message.content.strip()
        # strip code fences if present
        text = text.replace("```json", "").replace("```", "").strip()
        # grab the first {...} block
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            text = text[a:b+1]
        return _json.loads(text)
    except Exception as e:
        return {"_error": f"Could not translate question: {e}"}


def _execute_query(df, spec: dict) -> dict:
    """Run the structured query spec on the dataframe with pandas. Exact numbers."""
    import pandas as pd

    def find_col(name):
        for c in df.columns:
            if str(name).lower() == str(c).lower().strip():
                return c
        for c in df.columns:
            if str(name).lower() in str(c).lower():
                return c
        return None

    work = df
    applied_filters = []
    _SORT_WORDS = {"maximum","minimum","highest","lowest","largest","smallest","top","bottom","max","min"}
    for f in spec.get("filter", []) or []:
        col = find_col(f.get("column", ""))
        if not col:
            continue
        # safety: ignore nonsense filters like DIFFERENCE equals "maximum"
        if str(f.get("value","")).strip().lower() in _SORT_WORDS:
            continue
        op = f.get("op", "contains")
        val = f.get("value", "")
        series = work[col]
        try:
            if op == "contains":
                mask = series.astype(str).str.upper().str.contains(str(val).upper(), na=False)
            elif op in ("starts_with", "startswith", "begins_with"):
                mask = series.astype(str).str.strip().str.upper().str.startswith(str(val).strip().upper())
            elif op in ("ends_with", "endswith"):
                mask = series.astype(str).str.strip().str.upper().str.endswith(str(val).strip().upper())
            elif op == "equals":
                mask = series.astype(str).str.strip().str.upper() == str(val).strip().upper()
            elif op in ("gt", "lt", "gte", "lte"):
                num = pd.to_numeric(series, errors="coerce")
                v = float(val)
                mask = {"gt": num > v, "lt": num < v, "gte": num >= v, "lte": num <= v}[op]
            else:
                continue
            work = work[mask]
            applied_filters.append(f"{col} {op} {val}")
        except Exception:
            continue

    action = spec.get("action", "count")
    tcol = find_col(spec.get("target_column", "")) if spec.get("target_column") else None
    gcol = find_col(spec.get("group_by", "")) if spec.get("group_by") else None
    n = int(spec.get("n", 5) or 5)
    out = {"filters_applied": applied_filters or "none", "rows_matched": int(len(work))}

    # columns to show for row-returning actions
    show = []
    for want in ["ACCOUNTID","Account ID","Client Code","ClientID","NAME","Zone","REMARKS"]:
        c = find_col(want)
        if c and c not in show:
            show.append(c)
    if tcol and tcol not in show:
        show.append(tcol)

    def rows_to_records(frame, cols):
        recs = []
        for _, r in frame.iterrows():
            recs.append({str(k): (None if pd.isna(r[k]) else
                        (round(float(r[k]),2) if isinstance(r[k],(int,float)) else str(r[k])))
                        for k in (cols if cols else frame.columns)})
        return recs

    if action == "count":
        if gcol:
            vc = work[gcol].fillna("(blank)").astype(str).str.strip().value_counts()
            out["count_by"] = gcol
            out["counts"] = {str(k): int(v) for k, v in vc.items()}
        else:
            out["count"] = int(len(work))

    elif action in ("sum", "average") and tcol:
        col = pd.to_numeric(work[tcol], errors="coerce")
        if gcol:
            g = work.copy(); g["_v"] = col
            agg = g.groupby(g[gcol].astype(str))["_v"].sum() if action=="sum" else g.groupby(g[gcol].astype(str))["_v"].mean()
            out[f"{action}_by"] = gcol
            out["values"] = {str(k): round(float(v),2) for k,v in agg.items()}
        else:
            out[action] = round(float(col.sum() if action=="sum" else col.mean()), 2)
        out["column"] = tcol

    elif action in ("max", "min") and tcol:
        tmp = work.copy(); tmp["_v"] = pd.to_numeric(tmp[tcol], errors="coerce")
        tmp = tmp.dropna(subset=["_v"]).sort_values("_v", ascending=(action=="min"))
        if len(tmp):
            out[action] = round(float(tmp.iloc[0]["_v"]), 2)
            out["row"] = rows_to_records(tmp.head(1), show)[0]
        out["column"] = tcol

    elif action in ("top", "bottom") and tcol:
        tmp = work.copy(); tmp["_v"] = pd.to_numeric(tmp[tcol], errors="coerce")
        tmp = tmp.dropna(subset=["_v"]).sort_values("_v", ascending=(action=="bottom"))
        out["column"] = tcol
        # Tie detection: if many rows share the extreme value, return THEM ALL
        # (up to 25) so the agent can list them directly — never tell the user to ask again.
        if len(tmp) > 0:
            extreme = float(tmp.iloc[0]["_v"])
            tie_count = int((tmp["_v"] == extreme).sum())
            out["extreme_value"] = round(extreme, 2)
            out["rows_tied_at_extreme"] = tie_count
            if tie_count > 1:
                tied = tmp[tmp["_v"] == extreme]
                out["rows"] = rows_to_records(tied.head(25), show)
                out["tie_note"] = (f"{tie_count} clients share the {'highest' if action=='top' else 'lowest'} "
                                   f"{tcol} of {round(extreme,2)}. They are listed in rows.")
            else:
                out["rows"] = rows_to_records(tmp.head(n), show)
        else:
            out["rows"] = []

        # NMASS=0 awareness: if top shortfall rows have zero deposit, the
        # "shortfall" equals the full CTCL limit (no collateral recorded). Flag it
        # so the user can judge real-shortfall vs data-gap vs house account.
        _nm = next((c for c in work.columns if str(c).strip().upper() == "DEPOSIT NMASS"), None)
        if _nm is not None and out.get("rows"):
            zero_dep = [r for r in out["rows"]
                        if str(r.get("DEPOSIT NMASS", r.get(_nm, ""))).replace(".0","").strip() in ("0", "0.0", "")]
            if zero_dep:
                out["zero_nmass_flag"] = (
                    f"{len(zero_dep)} of these have DEPOSIT NMASS = 0, so the shortfall equals their "
                    f"entire CTCL limit (no collateral recorded). This can mean a house/proprietary "
                    f"account, or that collateral data didn't load for them — worth verifying before "
                    f"treating as a real shortfall.")

    elif action == "lookup":
        out["records"] = rows_to_records(work.head(10), None)  # full columns

    elif action == "list":
        out["rows"] = rows_to_records(work.head(n if n else 15), show)

    else:
        out["count"] = int(len(work))

    return out


# ── TOOL EXECUTOR ─────────────────────────────────────────────────────────────
# Words that signal the user actually wants to EXECUTE a pipeline (not ask about it).
_RUN_INTENT_WORDS = ("run ", "execute", "start ", "launch", "trigger", "download now",
                     "go ahead", "do it", "kick off", "fire ", "process now")
# Words that signal an INFORMATIONAL question (must never trigger execution).
_INFO_INTENT_WORDS = ("tell me", "how many", "what is", "what are", "show me", "which ",
                      "list ", "explain", "details of", "detail about", "about the",
                      "output", "count", "highest", "lowest", "status")

_RUN_TOOLS = {"run_peak_margin", "run_ctcl", "run_concentration", "run_cnc_f11",
              "run_mtf_order", "run_ftp_download"}

# The current user message, set per-turn so _run_tool can check intent.
_CURRENT_USER_MSG = ""


def _run_tool(name: str, args: dict) -> dict:
    # ── SAFETY GUARD: never EXECUTE a pipeline from an informational question ──
    if name in _RUN_TOOLS:
        msg = _CURRENT_USER_MSG.lower()
        has_run = any(w in msg for w in _RUN_INTENT_WORDS)
        has_info = any(w in msg for w in _INFO_INTENT_WORDS)
        if has_info and not has_run:
            return {"blocked": True,
                    "reason": ("This looks like a QUESTION about the pipeline, not a "
                               "request to run it. I did NOT execute anything. To read "
                               "results use ask_data / read_output_file. To actually RUN "
                               "the pipeline, say 'run <pipeline>' explicitly."),
                    "tool_blocked": name}
    return _run_tool_impl(name, args)


def _run_tool_impl(name: str, args: dict) -> dict:
    """Execute a tool call and return a JSON-serialisable result."""

    # ── Knowledge tools ─────────────────────────────────────────────────────
    if name == "lookup_rule":
        return {"answer": answer_compliance(args["question"])}

    if name == "calculate_penalty":
        return calculate_penalty(args["shortfall_amount"], args["days_short"])

    # ── File tools ──────────────────────────────────────────────────────────
    if name == "list_output_files":
        folder = FOLDER_MAP.get(args["pipeline"])
        if not folder or not os.path.isdir(folder):
            return {"error": f"Folder not found for pipeline '{args['pipeline']}': {folder}"}
        try:
            files = []
            for f in os.listdir(folder):
                fp = os.path.join(folder, f)
                if os.path.isfile(fp):
                    stat = os.stat(fp)
                    from datetime import datetime
                    files.append({
                        "name": f,
                        "size_kb": round(stat.st_size / 1024, 1),
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    })
            files.sort(key=lambda x: x["modified"], reverse=True)
            result = {"pipeline": args["pipeline"], "folder": folder, "files": files[:30]}

            # ALSO look inside backup/dated subfolders — the REAL output files
            # (FULL_FILE_*, etc.) usually live there, not at the top level.
            from datetime import datetime as _dt
            backup_files = []
            for entry in os.listdir(folder):
                ep = os.path.join(folder, entry)
                if os.path.isdir(ep) and "BACKUP" in entry.upper():
                    for root_d, _dirs, fnames in os.walk(ep):
                        for fn in fnames:
                            if fn.lower().endswith((".csv",".xlsx",".xls")):
                                fp = os.path.join(root_d, fn)
                                try:
                                    st = os.stat(fp)
                                    backup_files.append({
                                        "name": fn,
                                        "relpath": os.path.relpath(fp, folder),
                                        "modified": _dt.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
                                except Exception:
                                    pass
            if backup_files:
                backup_files.sort(key=lambda x: x["modified"], reverse=True)
                result["backup_files"] = backup_files[:30]
                result["note"] = ("The real output files are in the backup subfolders listed under "
                                  "'backup_files'. To analyse numbers from them, call analyze_file "
                                  "(it searches backups automatically). Do NOT report the top-level "
                                  ".bat files as outputs — those are scripts, not data.")
            return result
        except Exception as e:
            return {"error": str(e)}

    if name == "read_output_file":
        folder = FOLDER_MAP.get(args["pipeline"])
        filename = args.get("filename", "latest")
        pipeline = args["pipeline"]
        if not folder or not os.path.isdir(folder):
            return {"error": f"Folder not found: {folder}"}
        try:
            fpath, display = _find_output_file(folder, pipeline, filename)
            if fpath is None:
                return {"error": display}
            filename = display
            ext = filename.lower().split(".")[-1]

            if ext in ("txt", "log", "csv") and ext != "csv":
                content = open(fpath, encoding="utf-8", errors="replace").read()[:1200]
                return {"filename": filename, "file_full_path": fpath, "content": content}

            if ext == "csv":
                import polars as pl
                df = pl.read_csv(fpath, ignore_errors=True, infer_schema_length=100)
                rows = df.shape[0]; cols = df.columns
                preview = df.head(15).to_pandas().to_string(max_rows=15, max_cols=10)
                return {"filename": filename, "file_full_path": fpath, "rows": rows, "columns": list(cols), "preview": preview[:1200]}

            if ext in ("xlsx", "xls"):
                import pandas as pd
                df = pd.read_excel(fpath, nrows=100)
                rows_approx = len(df); cols = list(df.columns)
                preview = df.head(15).to_string(max_rows=15, max_cols=10)
                return {"filename": filename, "file_full_path": fpath, "rows_shown": rows_approx, "columns": cols, "preview": preview[:1200]}

            return {"error": f"Unsupported file type: {ext}. Supported: csv, xlsx, txt, log"}

        except Exception as e:
            return {"error": f"Could not read file: {e}"}

    # ── Pipeline runners ─────────────────────────────────────────────────────
    if name == "run_peak_margin":
        from core.pipelines.peak_margin_run import process_pair
        out = process_pair(args["index"])
        compact = {"status": out["status"], "index": out.get("index"), "messages": out.get("messages", [])}
        if out.get("status") == "computed":
            compact["totals"] = out["report"]["combined_totals"]
        return compact

    if name == "run_concentration":
        from core.pipelines.concentration_run import process_concentration
        out = process_concentration()
        return {k: v for k, v in out.items() if k not in ("messages",)}

    if name == "run_ctcl":
        from core.pipelines.ctcl_vs_nmass import run_ctcl_vs_nmass
        return run_ctcl_vs_nmass()

    if name == "run_cnc_f11":
        from core.pipelines.cnc_f11 import run_cnc_f11
        return run_cnc_f11()

    if name == "run_mtf_order":
        from core.pipelines.mtf_order import run_mtf_order
        return run_mtf_order()

    if name == "run_ftp_download":
        from core.pipelines.ftp_download import run_ftp_download
        return run_ftp_download()

    if name == "ask_data":
        import pandas as pd
        folder = FOLDER_MAP.get(args["pipeline"])
        if not folder or not os.path.isdir(folder):
            return {"error": f"Folder not found: {folder}"}
        fpath, display = _find_output_file(folder, args["pipeline"], "latest")
        if fpath is None:
            return {"error": display}
        try:
            df = (pd.read_csv(fpath, low_memory=False) if fpath.lower().endswith(".csv")
                  else pd.read_excel(fpath))
        except Exception as e:
            return {"error": f"Could not read {display}: {e}"}
        # 1) LLM translates English -> query spec (no numbers touched)
        spec = _translate_to_query(args["question"], list(df.columns))
        if spec.get("_error"):
            return {"error": spec["_error"]}
        # 2) pandas executes the spec -> exact answer
        answer = _execute_query(df, spec)
        answer["file_read"] = display
        answer["file_full_path"] = fpath
        answer["query_understood_as"] = spec
        return answer

    if name == "analyze_file":
        folder = FOLDER_MAP.get(args["pipeline"])
        filename = args.get("filename", "latest")
        if not folder or not os.path.isdir(folder):
            return {"error": f"Folder not found: {folder}"}
        try:
            import pandas as pd
            fpath, display = _find_output_file(folder, args["pipeline"], filename)
            if fpath is None:
                return {"error": display}   # display holds the error message
            filename = display

            # Load
            if fpath.lower().endswith(".csv"):
                df = pd.read_csv(fpath, low_memory=False)
            else:
                df = pd.read_excel(fpath)

            # Build REAL computed facts — this is what the LLM will report.
            facts = {
                "file_read": filename,
                "file_full_path": fpath,
                "total_rows": int(len(df)),
                "columns": list(df.columns),
            }

            def find_col(*names):
                for n in names:
                    for c in df.columns:
                        if n.lower() == str(c).lower().strip():
                            return c
                for n in names:
                    for c in df.columns:
                        if n.lower() in str(c).lower():
                            return c
                return None

            remarks_col = find_col("REMARKS", "Remark")
            zone_col    = find_col("Zone")
            acct_col    = find_col("ACCOUNTID", "Account ID", "Client Code", "ClientID")
            diff_col    = find_col("DIFFERENCE(G-F)", "DIFFERENCE", "SHORTAGE", "Shortfall")
            am_col      = find_col("Available Margin", "AvailableMargin")

            # mask of over-limit rows (used many places). CTCL vs NMASS says
            # "LIMIT EXCESS", Margin Shortage says "SHORTFALL", and files written
            # before the CTCL rename still say "SHORTFALL" — match all of them so
            # this works whichever report/vintage was loaded.
            short_mask = None
            if remarks_col:
                short_mask = df[remarks_col].astype(str).str.upper().str.contains(
                    "LIMIT EXCESS|SHORTFALL", regex=True, na=False)
                vc = df[remarks_col].fillna("(blank)").astype(str).str.strip().value_counts()
                facts["remarks_breakdown"] = {str(k): int(v) for k, v in vc.items()}
                facts["shortfall_client_count"] = int(short_mask.sum())

            if diff_col:
                col = pd.to_numeric(df[diff_col], errors="coerce")
                facts[f"sum_of_{diff_col}"] = round(float(col.sum(skipna=True)), 2)
                if acct_col:
                    tmp = df[[acct_col, diff_col]].copy()
                    tmp[diff_col] = pd.to_numeric(tmp[diff_col], errors="coerce")
                    tmp = tmp.dropna().reindex(tmp[diff_col].abs().sort_values(ascending=False).index)
                    facts["top_5_by_difference"] = [
                        {"account": str(r[acct_col]), "value": round(float(r[diff_col]), 2)}
                        for _, r in tmp.head(5).iterrows()]
                    # explicit single answers for "which client has max/min shortfall"
                    if len(tmp) > 0:
                        top = tmp.iloc[0]
                        facts["client_with_max_difference"] = {
                            "account": str(top[acct_col]), "value": round(float(top[diff_col]), 2)}
                        # max shortfall = most positive DIFFERENCE (CTCL-NMASS > 0)
                        pos = tmp[pd.to_numeric(tmp[diff_col], errors="coerce") > 0]
                        if len(pos) > 0:
                            mx = pos.iloc[0]
                            facts["client_with_max_shortfall"] = {
                                "account": str(mx[acct_col]), "value": round(float(mx[diff_col]), 2)}

            # ── ZONE breakdowns — total AND shortfall-only, per zone ──────────
            if zone_col:
                zser = df[zone_col].fillna("(blank)").astype(str).str.strip().str.upper()
                facts["zone_total_counts"] = {str(k): int(v) for k, v in zser.value_counts().items()}
                if short_mask is not None:
                    zshort = zser[short_mask]
                    facts["zone_shortfall_counts"] = {str(k): int(v) for k, v in zshort.value_counts().items()}

            if am_col:
                col = pd.to_numeric(df[am_col], errors="coerce")
                facts["count_negative_available_margin"] = int((col < 0).sum())

            # ── FLEXIBLE FILTER — answer the SPECIFIC question ────────────────
            # If the question names a zone code or a value, compute that exact count.
            q = (args.get("question") or "").upper()
            import re as _re
            # detect any token that looks like a zone code (ZO...) or known codes
            zone_tokens = [z for z in _re.findall(r"\bZO[A-Z]{2,4}\b", q) if z != "ZONE"]
            # also catch "ZONE XYZ" or "DELHI"->ZODELH style hints
            hint_map = {"DELHI": "ZODELH", "MUMBAI": "ZOMUMB", "LUDHIANA": "ZOLUDH", "CHANDIGARH": "ZOCHND"}
            for word, code in hint_map.items():
                if word in q:
                    zone_tokens.append(code)
            zone_tokens = list(dict.fromkeys(zone_tokens))  # dedupe

            if zone_tokens and zone_col:
                zser = df[zone_col].fillna("").astype(str).str.strip().str.upper()
                filtered = {}
                for zt in zone_tokens:
                    in_zone = zser == zt
                    entry = {"total_in_zone": int(in_zone.sum())}
                    if short_mask is not None:
                        entry["with_shortfall"] = int((in_zone & short_mask).sum())
                    filtered[zt] = entry
                facts["specific_zone_query"] = filtered

            return facts
        except Exception as e:
            return {"error": f"Could not analyze file: {e}"}

    if name == "search_code":
        try:
            from core.code_index import search_code as _sc
            return _sc(args.get("query", ""), k=3)
        except Exception as e:
            return {"error": f"Code index unavailable: {e}. "
                             "Check CODE_FOLDERS in .env and restart."}

    if name == "daily_summary":
        try:
            from core.daily_summary import generate_daily_summary
            return generate_daily_summary()
        except Exception as e:
            return {"error": f"Daily summary failed: {e}"}

    if name == "explain_column":
        try:
            return explain_column(args.get("pipeline", ""), args.get("column", ""))
        except Exception as e:
            return {"error": f"Column lookup failed: {e}"}

    if name == "explain_client":
        try:
            report = args.get("report", "margin_shortage")
            code = args.get("client_code", "")
            if report == "ctcl":
                from core.root_cause import analyze_ctcl
                return analyze_ctcl(code)
            from core.root_cause import analyze_margin_shortage
            return analyze_margin_shortage(code)
        except Exception as e:
            return {"error": f"Explain failed: {e}"}

    if name == "list_indexed_code":
        try:
            from core.code_index import list_indexed_code as _lic
            r = _lic()
            # keep the payload small for the model: names + counts only
            if "files" in r:
                r = {"folders_configured": r["folders_configured"],
                     "total_files": r["total_files"],
                     "files": [f["file"] for f in r["files"]][:80]}
            return r
        except Exception as e:
            return {"error": f"Code index unavailable: {e}"}

    if name == "check_input_readiness":
        try:
            from core.readiness import check_readiness as _cr
            return _cr(args.get("pipeline", ""))
        except Exception as e:
            return {"error": f"Readiness check failed: {e}"}

    if name == "list_uploaded_files":
        folder = FOLDER_MAP["uploads"]
        os.makedirs(folder, exist_ok=True)
        try:
            from datetime import datetime
            files = []
            for f in os.listdir(folder):
                fp = os.path.join(folder, f)
                if os.path.isfile(fp):
                    st = os.stat(fp)
                    files.append({"name": f, "size_kb": round(st.st_size/1024,1),
                                  "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
            files.sort(key=lambda x: x["modified"], reverse=True)
            if not files:
                return {"folder": folder, "files": [],
                        "note": "No files uploaded yet. Drop a file into the uploads folder to analyse it."}
            return {"folder": folder, "files": files[:40]}
        except Exception as e:
            return {"error": str(e)}

    if name == "read_uploaded_file":
        folder = FOLDER_MAP["uploads"]
        os.makedirs(folder, exist_ok=True)
        filename = args.get("filename", "latest")
        try:
            READABLE = (".csv",".xlsx",".xls",".txt",".log",".json",".md")
            if filename == "latest":
                cand = [f for f in os.listdir(folder)
                        if os.path.isfile(os.path.join(folder,f)) and f.lower().endswith(READABLE)]
                if not cand:
                    return {"error": f"No readable files in uploads folder ({folder})."}
                filename = max(cand, key=lambda f: os.path.getmtime(os.path.join(folder,f)))
            fpath = os.path.join(folder, filename)
            if not os.path.exists(fpath):
                return {"error": f"File not found: {filename}"}
            ext = filename.lower().split(".")[-1]
            if ext in ("txt","log","json","md"):
                return {"filename": filename, "file_full_path": fpath, "content": open(fpath, encoding="utf-8", errors="replace").read()[:1500]}
            if ext == "csv":
                import polars as pl
                df = pl.read_csv(fpath, ignore_errors=True, infer_schema_length=100)
                return {"filename": filename, "file_full_path": fpath, "rows": df.shape[0], "columns": list(df.columns),
                        "preview": df.head(15).to_pandas().to_string(max_rows=15, max_cols=10)[:1500]}
            if ext in ("xlsx","xls"):
                import pandas as pd
                df = pd.read_excel(fpath, nrows=100)
                return {"filename": filename, "file_full_path": fpath, "rows_shown": len(df), "columns": list(df.columns),
                        "preview": df.head(15).to_string(max_rows=15, max_cols=10)[:1500]}
            return {"error": f"Unsupported type: {ext}"}
        except Exception as e:
            return {"error": f"Could not read file: {e}"}

    return {"error": f"Unknown tool: {name}"}


# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
SYSTEM = """You are the RMS Operations Agent for Master Capital Services Ltd (member 06436).
You help the RMS team run pipelines, read/analyse output files, diagnose errors, and answer
process questions. You have direct LOCAL file access via tools.

HARD RULES:
1. NEVER invent data, clients, numbers, filenames, or REASONS. Every number comes from a tool
   result. Never narrate a tool call ("I will call X", "=function="), never say a tool's name —
   either CALL it or give the final answer in ONE message.
2. Data questions (how many / highest / total / which client / list / starting with / details of)
   -> ask_data {pipeline, question}. "code"/"client code"/"account" mean the ACCOUNTID column
   (account IDs, not .py scripts). Report ONLY the facts the tool returned; if asked WHY and the
   data lacks the reason, use explain_client/explain_column or say you can see the value not the
   reason — never invent a cause.
3. NEVER EXECUTE a pipeline unless the user explicitly says run/execute/start/trigger/download
   now. A QUESTION ("tell me about X", "how many in X", "X output") is answered by READING, never
   running. When in doubt, READ.
4. Source line is mandatory, exact format: Source: <filename> - <full folder path> (copy
   file_read + file_full_path from the tool result). Tool error / zero rows -> say so and name the
   folder; never answer with invented data.
5. Reading the user's OWN folders/files is LOCAL (never refuse as "no internet"). Real outputs live
   in BACKUP subfolders; RISK_REPORT_*.csv is a CTCL INPUT, not its output.
6. Two over-limit reports with OPPOSITE signs and DIFFERENT wording — never mix them.
   CTCL (FULL_FILE_CTCL_VS_NMASS): remark is "LIMIT EXCESS"; DIFFERENCE(G-F) is sign-aware —
   DEPOSIT CTCL >= 0 -> CTCL-NMASS (POSITIVE = LIMIT EXCESS), DEPOSIT CTCL < 0 -> CTCL+NMASS which
   is informational ONLY and always remark OK. So ALWAYS filter REMARKS = LIMIT EXCESS before
   ranking DIFFERENCE(G-F), or a debit-balance row will masquerade as the biggest excess.
   15-minutes/Margin Shortage (Shortage_vs_CC_Deposit): remark is "SHORTFALL";
   DIFFERENCE(F-G)=NMASS-margin, NEGATIVE=shortfall (biggest = most negative).
   Use the pipeline the user named. CTCL output files created before the rename say "SHORTFALL"
   where they would now say "LIMIT EXCESS" — same meaning.
7. WhatsApp (15-min): "eligible" = MARGIN WARNING % > 105 (computable); who was actually SENT
   depends on a tracker not in the file. Report eligible count + that caveat; never treat 105 as a
   client count.
8. Style: direct, short, Indian numbers (1,23,456), bold negatives/action items. Never claim
   emails/WhatsApps were sent. Never tell the user to "use ask_data" or to ask again — if you
   need more, call the tool yourself.
9. "Highest" with ties: the tool returns ALL tied clients in 'rows' — LIST them by ACCOUNTID
   directly (never tell the user to re-ask). If the result has zero_nmass_flag, report it: these
   accounts have DEPOSIT NMASS = 0 so the shortfall equals their full CTCL limit (no collateral
   recorded) — could be a house/proprietary account OR a data-load gap; tell the user to verify,
   and offer to show the highest shortfall among accounts that DO have collateral (NMASS > 0).

10. ERRORS / FAILURES: diagnose ONLY from a real traceback. If a traceback or an
    "ACTUAL TRACEBACK" block is present in this conversation, name the exact file:line
    it points to and explain it. If NONE is present, do NOT propose a cause, a missing
    folder, or a missing file — say you need the traceback and ask the user to paste it
    (or to re-run so it is captured). Never invent a reason you cannot see.
"""


# ── AGENT LOOP ────────────────────────────────────────────────────────────────
def _call_model(messages, model=None, force_tools=False):
    use_model = model or MODEL
    choice = "required" if force_tools else "auto"
    active = _ACTIVE_TOOLS if _ACTIVE_TOOLS is not None else TOOLS
    try:
        return _client.chat.completions.create(
            model=use_model, messages=messages, tools=active,
            tool_choice=choice, parallel_tool_calls=False)
    except Exception as e:
        msg = str(e).lower()
        # 0) provider rejects tool_choice="required" → retry as auto
        if force_tools and ("tool_choice" in msg or "required" in msg):
            return _call_model(messages, model=use_model, force_tools=False)
        # 1) Daily rate limit hit on the big model → retry on the small model
        if "rate_limit" in msg or "429" in msg:
            if use_model != FALLBACK_MODEL:
                return _client.chat.completions.create(
                    model=FALLBACK_MODEL, messages=messages, tools=active,
                    tool_choice=choice, parallel_tool_calls=False)
            raise  # already on fallback and still limited — nothing more to do
        # 2) Malformed tool call → recover gracefully
        if "tool_use_failed" in msg:
            return _call_model_badrequest(messages, e, use_model)
        # 3) anything else → bubble up
        raise


def _call_model_badrequest(messages, e, use_model):
    """Handle malformed tool calls gracefully."""
    es = str(e).lower()
    if "tool_use_failed" in es or "did not match schema" in es or "value must be one of" in es:
        messages.append({"role": "system",
            "content": "Your last tool call had an invalid argument. For data questions call "
                       "ask_data with exactly {pipeline, question}. check_input_readiness only "
                       "supports pipeline = ctcl | cnc_f11 | mtf_order | concentration (NOT "
                       "margin_shortage). For shortage/shortfall data use ask_data with "
                       "pipeline='ctcl'. Call the correct tool now, or answer in plain text."})
        try:
            return _client.chat.completions.create(
                model=use_model, messages=messages, tools=TOOLS,
                tool_choice="auto", parallel_tool_calls=False)
        except Exception:
            # Final fallback: answer with NO tools so it never hard-errors
            return _client.chat.completions.create(model=use_model, messages=messages)
    raise e



# Valid tool names (for parsing leaked/printed tool calls from weak models)
_VALID_TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


def _extract_leaked_tool_call(text: str):
    """
    The 8b fallback sometimes PRINTS a tool call as text instead of emitting it.
    Parse formats like:
       =function=ask_data>{"pipeline":"ctcl","question":"..."}
       <function=ask_data>{...}
       function=list_output_files>{"pipeline":"margin_shortage"}
       ask_data({"pipeline":"ctcl", ...})
    Returns (tool_name, args_dict) or None.
    """
    if not text:
        return None
    import re as _re, json as _json
    # find a known tool name followed by a {...} JSON blob anywhere in the text
    for name in _VALID_TOOL_NAMES:
        # name>{...}  or  name {...}  or  name({...})  or  name>= {...}
        m = _re.search(_re.escape(name) + r"\s*[>(:=]*\s*(\{.*?\})", text, _re.S)
        if m:
            blob = m.group(1)
            try:
                args = _json.loads(blob)
                if isinstance(args, dict):
                    return name, args
            except Exception:
                # try to repair a truncated/loose blob: grab key":"value pairs
                pairs = dict(_re.findall(r'"(\w+)"\s*:\s*"([^"]*)"', blob))
                if pairs:
                    return name, pairs
    return None


def _strip_leaked(text: str) -> str:
    """Remove any leaked '=function=...>{...}' tool-call noise from a final answer."""
    if not text:
        return text
    import re as _re
    cleaned = _re.sub(r"[<=]?\s*function\s*=\s*\w+\s*[>(:=]*\s*\{.*?\}", "", text, flags=_re.S)
    return cleaned.strip() or text.strip()


def _build_source(result):
    """Extract the source payload (file + rows) for the UI detail panel."""
    if not isinstance(result, dict):
        return None
    src = {}
    if result.get("file_full_path") or result.get("file_read"):
        src["file"] = result.get("file_read") or os.path.basename(result.get("file_full_path", ""))
        src["path"] = result.get("file_full_path", "")
    for k in ("rows", "records", "top_rows", "counts", "values", "count", "sum",
              "average", "max", "min", "rows_tied_at_extreme", "tie_note", "zero_nmass_flag",
              "understood_as", "query_understood_as", "filters_applied", "rows_matched"):
        if k in result:
            src[k] = result[k]
    return src or None


def _trim_for_model(result):
    """Trim a tool result before sending it into the model context (token diet)."""
    if isinstance(result, dict) and "preview" in result:
        r = dict(result)
        r["preview"] = str(result["preview"])[:600]
        return r
    return result


def _agent_loop(history: list, on_step, max_steps: int = 8, force_first_tool: bool = False):
    """
    history: full conversation so far as [{"role":..,"content":..}]
             The caller is responsible for prepending the system prompt.
    on_step: callback called with event dicts as the agent works.
    Returns: the final plain-text answer string.
    """
    import re as _re
    messages = history.copy()
    for step in range(max_steps):
        msg = _call_model(messages, force_tools=(force_first_tool and step == 0)).choices[0].message
        if not msg.tool_calls:
            text = msg.content or ""
            # The fallback (8b) model sometimes PRINTS a tool call as text instead of
            # emitting it — in several formats:
            #   "I will call ask_data(...)"
            #   "=function=ask_data>{"pipeline":"ctcl","question":"..."}"
            #   "<function=ask_data>{...}", "function=ask_data {...}"
            # Detect ALL of these, and if we can parse the intended call, RUN it ourselves.
            leaked = _extract_leaked_tool_call(text)
            if leaked:
                lname, largs = leaked
                # rescue: actually run the tool the model meant to call
                on_step({"type": "tool_start", "step": step + 1, "tool": lname, "args": largs})
                lresult = _run_tool(lname, largs)
                on_step({"type": "tool_done", "step": step + 1, "tool": lname,
                         "result_preview": str(lresult)[:300],
                         "source": _build_source(lresult)})
                lpayload = json.dumps(_trim_for_model(lresult), default=str)
                messages.append({"role": "assistant", "content": f"(calling {lname})"})
                messages.append({"role": "user",
                    "content": f"Tool {lname} returned: {lpayload}\n\nNow give the final answer "
                               f"in plain English, with the Source line. Do not describe calling tools."})
                continue  # loop again so the model phrases the real result

            narrated = bool(_re.search(r"\b(I will call|let me call|I'll use|I will use|will call the .*tool)\b",
                                        text, _re.I)) or "=function=" in text or "function=" in text
            if narrated and step == 0:
                messages.append({"role": "system",
                    "content": "Do NOT describe or print a tool call. Call it now, or give the final "
                               "answer. The user must not have to ask twice."})
                msg = _call_model(messages, force_tools=True).choices[0].message
                if not msg.tool_calls:
                    on_step({"type": "done", "step": step + 1})
                    return _strip_leaked(msg.content or "(no answer)")
            else:
                on_step({"type": "done", "step": step + 1})
                return _strip_leaked(text or "(no answer)")
        messages.append(msg)
        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            on_step({"type": "tool_start", "step": step + 1, "tool": name, "args": args})
            result = _run_tool(name, args)
            on_step({"type": "tool_done", "step": step + 1, "tool": name,
                     "result_preview": str(result)[:300],
                     "source": _build_source(result)})
            payload = json.dumps(_trim_for_model(result), default=str)
            if len(payload) > 2500:          # hard ceiling per tool result
                payload = payload[:2500] + ' ...(truncated)"}'
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": payload})
    return "Reached step limit."



# ══════════════════════════════════════════════════════════════════════════
# KNOWN-ERROR MATCHER — answers documented errors with ZERO LLM calls.
# Parses the error reference in pipeline_knowledge.txt into (keywords, block)
# pairs; a pasted error matching a documented pattern returns the exact fix
# instantly. Free, instant, cannot hallucinate, immune to rate limits.
# ══════════════════════════════════════════════════════════════════════════
_KNOWN_ERRORS = None

def _load_last_run_error(user_message: str = "") -> str:
    """Return the real traceback from the most recent failed pipeline run so the agent
    answers error questions from the actual stack instead of guessing. "" if none recent."""
    try:
        import json as _j, time as _t
        log_dir = os.getenv("AGENT_LOG_DIR", os.path.join(os.getcwd(), "logs"))
        path = os.path.join(log_dir, "last_error.json")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as _f:
            d = _j.load(_f)
        # Only attach when the user is explicitly asking about an error/failure.
        # Never inject into ordinary questions (that caused canned answers everywhere).
        low = (user_message or "").lower()
        if not any(w in low for w in ("error", "failed", "fail", "traceback",
                                      "aborting", "exception", "crash", "not working")):
            return ""
        age = _t.time() - float(d.get("ts", 0) or 0)
        if age > 86400:                   # ignore anything older than a day
            return ""
        trace = (d.get("trace") or "").strip()
        if not trace:
            return ""
        return ("ACTUAL TRACEBACK from the last failed run "
                f"({d.get('pipeline', '?')}):\n{trace}\n"
                "Use this real stack trace to find the exact file/line. Do not guess a cause.")
    except Exception:
        return ""


def _match_known_error(text: str):
    import re as _re
    global _KNOWN_ERRORS
    if _KNOWN_ERRORS is None:
        _KNOWN_ERRORS = []
        err = _load_deep_knowledge().get("_errors", "")
        for block in [b.strip() for b in err.split("\n\n") if "→" in b]:
            pat_part = block.partition("→")[0]
            keys = _re.findall(r'"([^"]{6,})"', pat_part) or [pat_part.strip()[:60]]
            word_sets = []
            for k in keys:
                ws = {w for w in _re.findall(r"[a-z0-9_]+", k.lower()) if len(w) > 3}
                if len(ws) >= 2:
                    word_sets.append(ws)
            if word_sets:
                _KNOWN_ERRORS.append((word_sets, block))

    tw = set(_re.findall(r"[a-z0-9_]+", text.lower()))

    # Distinct error SIGNATURES — a message that clearly names one category must NOT
    # be matched to a different category just because a generic word (like "login")
    # overlaps. E.g. a timeout is a network error, never a password error.
    signatures = {
        "timeout":    {"timeout", "timed", "read", "connection", "connectionpool", "unreachable", "refused", "503", "502", "504"},
        "password":   {"password", "credential", "expired", "unauthorized", "401", "403"},
        "filenotfound": {"filenotfound", "no", "such", "missing", "notfound"},
    }
    msg_categories = {cat for cat, words in signatures.items() if len(words & tw) >= 1}
    # If the message clearly looks like a timeout/network error, prefer that and
    # do NOT return a password/credential block.
    network_like = "timeout" in msg_categories or {"timed", "timeout", "connectionpool"} & tw

    # Score every known error by how many of its keywords appear in the message,
    # and require a strong match (not just one generic word). Pick the BEST.
    best, best_score = None, 0
    for word_sets, block in _KNOWN_ERRORS:
        block_l = block.lower()
        # skip password/credential blocks when the message is clearly network-related
        if network_like and any(w in block_l for w in ("password", "credential", "expired", "unauthorized")):
            continue
        for ws in word_sets:
            overlap = len(ws & tw)
            # require ALL keywords of at least one set to match (subset), OR a strong
            # majority for longer sets
            if ws.issubset(tw):
                score = overlap + 2          # exact subset = strong
            elif len(ws) >= 3 and overlap >= len(ws) - 1:
                score = overlap              # near-complete for specific errors
            else:
                continue
            if score > best_score:
                best, best_score = block, score
    return best



# ══════════════════════════════════════════════════════════════════════════
# AUDIT LOG — every agent Q&A appended to logs/agent_log.jsonl.
# LangSmith-lite: free, local, never blocks or breaks the chat.
# ══════════════════════════════════════════════════════════════════════════
def _log_chat(question: str, tools: list, answer: str, mode: str = "agent"):
    try:
        import json as _j
        from datetime import datetime as _dt
        log_dir = os.getenv("AGENT_LOG_DIR", os.path.join(os.getcwd(), "logs"))
        os.makedirs(log_dir, exist_ok=True)
        entry = {
            "ts": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,                      # "agent" | "known_error"
            "q": str(question)[:300],
            "tools": tools or [],
            "ans": str(answer)[:500],
        }
        with open(os.path.join(log_dir, "agent_log.jsonl"), "a", encoding="utf-8") as f:
            f.write(_j.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never break the chat


def run_agent_chat(user_message: str, history: list, on_step=None, max_steps: int = 8) -> tuple:
    """
    Main entry point for the chat UI.

    Args:
        user_message: the latest message from the user
        history: list of {"role": "user"|"assistant", "content": "..."} dicts
                 from previous turns (WITHOUT the system prompt — added here)
        on_step: optional callback for streaming tool events
        max_steps: safety limit on tool calls per turn

    Returns:
        (answer_str, updated_history_list)
        updated_history includes the new user message + assistant reply
        — the caller should save this and send it back next turn.
    """
    if on_step is None:
        on_step = lambda ev: None

    # Make the user message visible to the run-tool safety guard.
    global _CURRENT_USER_MSG
    _CURRENT_USER_MSG = user_message

    # Send the model only the tools relevant to this question (token diet for
    # the free tier's 6000 TPM limit). Falls back to all tools if unsure.
    global _ACTIVE_TOOLS
    _ACTIVE_TOOLS = _select_tools(user_message)

    # Collect tool names for the audit log (wraps the caller's on_step).
    _tools_used = []
    _caller_on_step = on_step
    def on_step(ev):
        if ev.get("type") == "tool_start" and ev.get("tool"):
            _tools_used.append(ev["tool"])
        _caller_on_step(ev)

    # ── ZERO-AI fast path: documented errors answered from the knowledge file ──
    if any(w in user_message.lower() for w in ("error", "failed", "fail", "traceback", "aborting")):
        hit = _match_known_error(user_message)
        if hit:
            on_step({"type": "tool_start", "step": 1, "tool": "error_knowledge_base", "args": {}})
            on_step({"type": "tool_done", "step": 1, "tool": "error_knowledge_base",
                     "result_preview": hit[:200]})
            answer = ("**Known error — answered instantly from your knowledge base (no AI call):**\n\n"
                      + hit.strip()
                      + "\n\nSource: pipeline_knowledge.txt — data\\circulars")
            updated = history + [{"role": "user", "content": user_message},
                                 {"role": "assistant", "content": answer}]
            _log_chat(user_message, _tools_used, answer, mode="known_error")
            return answer, updated

    # Build full message list: system + permanent pipeline knowledge + history + new msg.
    # Token diet: when a process is routed, its deep block replaces the full
    # registry (saves ~900 tokens/request on a 6,000 TPM free tier).
    system_full = SYSTEM
    if not detect_process(user_message):
        system_full += ("\n\nCOMPLETE PIPELINE KNOWLEDGE (you always know this — never guess):"
                        + _registry_summary())

    # ── DETERMINISTIC PROCESS ROUTING ────────────────────────────────────────
    # Detect which process(es) the user's message is about, BEFORE the LLM runs,
    # using exact keyword matching. Inject that process's full knowledge + tell
    # the agent exactly which pipeline key to use for tools. This removes the
    # "which process / which file" guesswork that caused wrong answers.
    detected = detect_process(user_message)
    routing_hint = ""
    if detected:
        primary = detected[0]
        blocks = "\n\n".join(process_knowledge(k) for k in detected[:2])
        # FULL deep knowledge for the primary process (your plain-English code logic)
        deep = _load_deep_knowledge().get(primary, "")[:3200]
        deep_block = f"\nFULL PROCESS LOGIC (plain English, from your code):\n{deep}\n" if deep else ""
        # error reference if the user mentions an error/failure
        err_block = ""
        if any(w in user_message.lower() for w in ("error", "fail", "crash", "not working", "issue")):
            err = _load_deep_knowledge().get("_errors", "")[:900]
            if err:
                err_block = f"\nERROR REFERENCE:\n{err}\n"
        routing_hint = (
            f"\n\n════════════════════════════════════════════════════════════════\n"
            f"THIS QUESTION IS ABOUT: {PIPELINE_REGISTRY[primary]['name']} "
            f"(pipeline key: '{primary}')\n"
            f"For any data tool call (ask_data, analyze_file, read_output_file, "
            f"list_output_files), use pipeline='{primary}'.\n"
            f"Relevant process knowledge:\n{blocks}\n"
            f"{deep_block}{err_block}"
            f"════════════════════════════════════════════════════════════════")
    system_full += routing_hint

    # Attach the real traceback ONLY for genuine error questions (additive; never
    # alters normal routing). "" for ordinary questions -> message is unchanged.
    _err_ctx = _load_last_run_error(user_message)
    messages = [{"role": "system", "content": system_full}] + history + \
               [{"role": "user", "content": user_message + (("\n\n" + _err_ctx) if _err_ctx else "")}]

    # If this is a data question about a known process, the model MUST call a tool
    # on its first step — answering from imagination is structurally blocked.
    force_first = bool(detected) and _is_data_question(user_message)
    try:
        answer = _agent_loop(messages, on_step, max_steps, force_first_tool=force_first)
    except Exception as e:
        # Translate any raw API/tool error into plain English before it reaches the user.
        answer = _humanize_error(e, detected)

    # Return the updated history (without system prompt — caller doesn't need it)
    updated = history + [
        {"role": "user",      "content": user_message},
        {"role": "assistant", "content": answer},
    ]
    _log_chat(user_message, _tools_used, answer, mode="agent")
    return answer, updated


def _humanize_error(e, detected=None) -> str:
    """Turn a raw exception / API error into a friendly plain-English message."""
    msg = str(e)
    low = msg.lower()

    # Daily / per-minute rate limit
    if "rate_limit" in low or "429" in low or "tokens per day" in low or "tokens per minute" in low:
        if "per day" in low or "tpd" in low:
            return ("I've used up today's free AI quota (the free tier has a daily limit). "
                    "It resets in about a day. In the meantime you can use the **Data Explorer** "
                    "tab for exact data answers — it uses far less of the quota.")
        return ("I'm getting requests too fast for the free tier's per-minute limit. "
                "Please wait a few seconds and ask again.")

    # Tool schema / validation error (e.g. unsupported pipeline for a tool)
    if "did not match schema" in low or "value must be one of" in low or "tool call validation" in low:
        # try to extract which pipeline was rejected and which are allowed
        import re as _re
        rejected = ""
        m = _re.search(r'"pipeline":\s*"([^"]+)"', msg)
        if m:
            rejected = m.group(1)
        # allowed = enum values from the "value must be one of [...]" part only
        oneof = _re.search(r"one of\s*\[([^\]]+)\]", msg)
        allowed = []
        if oneof:
            allowed = _re.findall(r'"?([a-z_]+)"?', oneof.group(1))
        allowed = [a for a in allowed if a in
                   ("ctcl", "cnc_f11", "mtf_order", "concentration", "payout",
                    "peak_margin", "margin_shortage", "ftp", "inspection", "margin_top20", "code2")
                   and a != rejected]
        if "check_input_readiness" in low or "readiness" in low:
            allowed_names = ", ".join(allowed) if allowed else "CTCL, CNC F11, MTF Order, Concentration"
            extra = f" '{rejected.replace('_',' ')}'" if rejected else ""
            return (f"I can only check input-file readiness for these pipelines: {allowed_names}. "
                    f"I don't have readiness rules for{extra} — that pipeline downloads input files "
                    f"rather than producing an output to check. Ask me about one of the supported "
                    f"pipelines, or ask me to read its output instead.")
        # generic schema error
        return ("I tried to use a tool with a value it doesn't support"
                + (f" (pipeline '{rejected}')" if rejected else "")
                + ". I can work with these pipelines: CTCL, Concentration, CNC F11, MTF Order, "
                  "Payout, Peak Margin, Code2. Could you rephrase using one of those?")

    # Malformed tool call that couldn't be recovered
    if "tool_use_failed" in low:
        return ("I had trouble forming that request. Could you rephrase it a bit more simply? "
                "For data, try e.g. 'in the CTCL output, which client has the highest shortfall?'")

    # Connection / provider down
    if "connection" in low or "timeout" in low or "503" in low or "502" in low:
        return "I couldn't reach the AI service just now. Please try again in a moment."

    # Fallback — never dump raw JSON at the user
    return ("Something went wrong while answering that. Please try rephrasing. "
            "(If it keeps happening, it may be the free-tier limit — the Data Explorer tab "
            "still works for exact data answers.)")


# ── backward-compat shims ─────────────────────────────────────────────────────
def run_agent(goal, verbose=True, max_steps=8):
    def printer(ev):
        if not verbose: return
        if ev["type"] == "tool_start":
            print(f"[step {ev['step']}] → {ev['tool']} {ev['args']}")
        elif ev["type"] == "tool_done":
            print(f"           ← {ev['result_preview']}")
    answer, _ = run_agent_chat(goal, [], printer, max_steps)
    return answer

def run_agent_traced(goal, max_steps=8):
    steps = []
    answer, _ = run_agent_chat(goal, [], steps.append, max_steps)
    return {"answer": answer, "steps": steps}
