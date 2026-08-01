"""
core/daily_summary.py — RMS Daily Summary.
==========================================
Reads the REAL output files of each pipeline and produces a one-glance
morning summary:

  CTCL:          41 shortfall accounts, biggest ₹6.95 cr (FIXTAR0001)
  15-min:        54 shortfall accounts, 12 WhatsApp-eligible (>105%)
  Concentration: 258 clients at 100% scrip concentration
  Payout:        7 accounts blocked (negative margin)

EVERYTHING here is COUNTED FROM FILES — never estimated, never invented.
If a file is missing, that pipeline's line says "output not found today"
rather than guessing a number.

Reuses _find_output_file() from core.agent so the date-folder / backup /
main-vs-zone logic stays in ONE place.
"""
import os
import pandas as pd

try:
    import polars as pl
except Exception:
    pl = None


def _read_df(path):
    """Read an output file into a pandas DataFrame, tolerant of format."""
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    # csv / tsv
    try:
        if pl is not None:
            return pl.read_csv(path, ignore_errors=True, infer_schema_length=200).to_pandas()
    except Exception:
        pass
    return pd.read_csv(path, on_bad_lines="skip", low_memory=False)


def _find_col(df, *candidates):
    """Find a column by fuzzy match (case/space-insensitive contains)."""
    norm = {c: str(c).strip().lower() for c in df.columns}
    for cand in candidates:
        cl = cand.strip().lower()
        for col, n in norm.items():
            if n == cl:
                return col
    for cand in candidates:                       # fallback: contains
        cl = cand.strip().lower()
        for col, n in norm.items():
            if cl in n:
                return col
    return None


def _biggest(df, acct_col, val_col, most_negative=False):
    """Return (account, value) of the biggest |value| row."""
    if acct_col is None or val_col is None:
        return None, None
    tmp = df.copy()
    tmp["_v"] = pd.to_numeric(tmp[val_col], errors="coerce")
    tmp = tmp.dropna(subset=["_v"])
    if tmp.empty:
        return None, None
    row = tmp.loc[tmp["_v"].idxmin()] if most_negative else tmp.loc[tmp["_v"].idxmax()]
    return str(row[acct_col]), float(row["_v"])


# ── per-pipeline counters (each returns a small dict, all from the file) ──────

def _summary_ctcl(find, fmt_money):
    folder = _folder("ctcl")
    fp, name = find(folder, "ctcl", "latest")
    if not fp:
        return {"status": "no_output", "note": "CTCL output not found today."}
    df = _read_df(fp)
    rem = _find_col(df, "REMARKS", "remark", "status")
    diff = _find_col(df, "DIFFERENCE(G-F)", "difference", "shortfall")
    acct = _find_col(df, "ACCOUNTID", "client code", "clientid", "account")
    # CTCL calls the over-limit case "LIMIT EXCESS" (formerly "SHORTFALL").
    # Both matched so a re-read of an older output file still counts correctly.
    _rem_u = df[rem].astype(str).str.upper() if rem else None
    _excess = (_rem_u.str.contains("LIMIT EXCESS|SHORTFALL", regex=True, na=False)
               if _rem_u is not None else None)
    shortfalls = int(_excess.sum()) if _excess is not None else None
    # Biggest excess must be taken from the LIMIT EXCESS rows only. Since the
    # DIFFERENCE formula became sign-aware, a debit-balance account (CTCL < 0)
    # carries DIFFERENCE = CTCL + NMASS — which can be large and positive while
    # the account is perfectly OK. Ranking the raw column would surface one of
    # those as "biggest shortfall", which is simply wrong.
    _src = df[_excess] if _excess is not None and _excess.any() else df
    big_acct, big_val = _biggest(_src, acct, diff, most_negative=False)
    out = {"status": "ok", "file": name, "shortfall_accounts": shortfalls}
    if big_acct:
        out["biggest_shortfall"] = {"account": big_acct, "amount": fmt_money(big_val)}
    return out


def _summary_margin_shortage(find, fmt_money):
    folder = _folder("margin_shortage")
    fp, name = find(folder, "margin_shortage", "latest")
    if not fp:
        return {"status": "no_output", "note": "15-minutes output not found today."}
    df = _read_df(fp)
    rem = _find_col(df, "REMARKS", "remark", "status")
    diff = _find_col(df, "DIFFERENCE(F-G)", "difference")
    warn = _find_col(df, "MARGIN WARNING %", "margin warning", "warning")
    acct = _find_col(df, "ACCOUNTID", "client code", "account")
    shortfalls = int(df[rem].astype(str).str.upper().str.contains("SHORTFALL", na=False).sum()) if rem else None
    wa_eligible = None
    if warn:
        wa_eligible = int((pd.to_numeric(df[warn], errors="coerce") > 105).sum())
    big_acct, big_val = _biggest(df, acct, diff, most_negative=True)  # 15-min: negative = shortfall
    out = {"status": "ok", "file": name, "shortfall_accounts": shortfalls,
           "whatsapp_eligible": wa_eligible,
           "whatsapp_note": "eligible = MARGIN WARNING % > 105; actual sent-list depends on the day's tracker"}
    if big_acct:
        out["biggest_shortfall"] = {"account": big_acct, "amount": fmt_money(big_val)}
    return out


def _summary_concentration(find, fmt_money):
    folder = _folder("concentration")
    fp, name = find(folder, "concentration", "latest")
    if not fp:
        return {"status": "no_output", "note": "Concentration output not found today."}
    df = _read_df(fp)
    scrip = _find_col(df, "Scrip_Concentration[(L/M)*100]", "Scrip_Concentration", "scrip concentration")
    acct = _find_col(df, "ACCOUNTID", "client code", "account")
    at_100 = None
    if scrip:
        at_100 = int((pd.to_numeric(df[scrip], errors="coerce") >= 100).sum())
    big_acct, big_val = _biggest(df, acct, scrip, most_negative=False)
    out = {"status": "ok", "file": name, "clients_at_100pct": at_100,
           "total_rows": int(len(df))}
    if big_acct:
        out["highest_concentration"] = {"account": big_acct, "value": round(big_val, 2)}
    return out


def _summary_payout(find, fmt_money):
    folder = _folder("payout")
    fp, name = find(folder, "payout", "latest")
    if not fp:
        return {"status": "no_output", "note": "Payout output not found today."}
    df = _read_df(fp)
    # blocked / negative-margin accounts — try a few likely columns
    status_col = _find_col(df, "STATUS", "remark", "decision", "blocked")
    margin_col = _find_col(df, "margin", "available margin", "net margin")
    blocked = None
    if status_col:
        blocked = int(df[status_col].astype(str).str.upper()
                      .str.contains("BLOCK|NEGATIVE|HOLD|REJECT", na=False, regex=True).sum())
    elif margin_col:
        blocked = int((pd.to_numeric(df[margin_col], errors="coerce") < 0).sum())
    return {"status": "ok", "file": name, "blocked_accounts": blocked}


# ── folder lookup (reads the same env vars as agent.FOLDER_MAP) ──────────────
def _folder(pipeline):
    env = {
        "ctcl": ("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS"),
        "concentration": ("CONCENTRATION_FOLDER", r"D:\CONCENTRATION"),
        "margin_shortage": ("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes"),
        "payout": ("PAYOUT_FOLDER", r"D:\PAYOUT ASHISH"),
    }.get(pipeline)
    if not env:
        return ""
    return os.getenv(env[0], env[1])


def generate_daily_summary() -> dict:
    """Build the full daily summary by reading each pipeline's real output."""
    from datetime import datetime
    # import here to avoid a circular import at module load
    from core.agent import _find_output_file as find

    def fmt_money(v):
        # Indian-style short form
        try:
            v = float(v)
        except Exception:
            return str(v)
        sign = "-" if v < 0 else ""
        a = abs(v)
        if a >= 1e7:
            return f"{sign}₹{a/1e7:.2f} cr"
        if a >= 1e5:
            return f"{sign}₹{a/1e5:.2f} lakh"
        return f"{sign}₹{a:,.0f}"

    sections = {}
    for key, fn in [("ctcl", _summary_ctcl),
                    ("margin_shortage", _summary_margin_shortage),
                    ("concentration", _summary_concentration),
                    ("payout", _summary_payout)]:
        try:
            sections[key] = fn(find, fmt_money)
        except Exception as e:
            sections[key] = {"status": "error", "note": f"Could not read: {e}"}

    return {
        "date": datetime.now().strftime("%d %B %Y"),
        "generated_at": datetime.now().strftime("%H:%M:%S"),
        "sections": sections,
    }
