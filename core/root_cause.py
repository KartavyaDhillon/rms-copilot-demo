"""
core/root_cause.py — "Why is this client short / why did they get a WhatsApp?"
==============================================================================
Explains the REASON behind a client's status using ONLY the numbers that
exist in the output file. It decomposes the formula into its real parts:

  Shortfall (15-min) = DEPOSIT NMASS − TOTAL MARGIN   (negative = short)
  Margin Warning %   = DERIVATIVE MARGIN USED ÷ DEPOSIT NMASS × 100
  WhatsApp eligible  = Margin Warning % > 105

So "why is X short" becomes a real breakdown:
  "Deposit ₹8L, margin used ₹10L → short by ₹2L. The margin warning is 130%
   because derivative margin (₹10.4L) is 130% of the deposit (₹8L)."

THE HARD BOUNDARY (anti-hallucination):
  The file does NOT contain WHICH trade or position caused the margin to
  rise. So this module explains the COMPONENTS (deposit vs margin, which
  one is driving it) but explicitly REFUSES to invent a trade-level cause
  like "FO position increased by ₹1.4L". That would be fabrication.
  It states the components and names the boundary.
"""
import os
import pandas as pd

try:
    import polars as pl
except Exception:
    pl = None


def _read_df(path):
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    try:
        if pl is not None:
            return pl.read_csv(path, ignore_errors=True, infer_schema_length=200).to_pandas()
    except Exception:
        pass
    return pd.read_csv(path, on_bad_lines="skip", low_memory=False)


def _col(df, *cands):
    norm = {c: str(c).strip().lower() for c in df.columns}
    for cand in cands:
        cl = cand.strip().lower()
        for col, n in norm.items():
            if n == cl:
                return col
    for cand in cands:
        cl = cand.strip().lower()
        for col, n in norm.items():
            if cl in n:
                return col
    return None


def _num(v):
    try:
        return float(v)
    except Exception:
        return None


def _money(v):
    if v is None:
        return "n/a"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e7:
        return f"{sign}₹{a/1e7:.2f} cr"
    if a >= 1e5:
        return f"{sign}₹{a/1e5:.2f} lakh"
    return f"{sign}₹{a:,.0f}"


def _find_client_row(df, client_code):
    acct = _col(df, "ACCOUNTID", "client code", "clientid", "account", "client/cp code")
    if acct is None:
        return None, None
    mask = df[acct].astype(str).str.strip().str.upper() == str(client_code).strip().upper()
    if not mask.any():
        return None, acct
    return df[mask].iloc[0], acct


def analyze_margin_shortage(client_code: str) -> dict:
    """Explain a client's 15-minutes shortfall / margin-warning / WhatsApp status."""
    from core.agent import _find_output_file as find
    folder = os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    fp, name = find(folder, "margin_shortage", "latest")
    if not fp:
        return {"error": "15-minutes output (Shortage_vs_CC_Deposit.xlsx) not found today."}
    df = _read_df(fp)
    row, acct = _find_client_row(df, client_code)
    if row is None:
        return {"error": f"Client {client_code} not found in the 15-minutes output."}

    deposit = _num(row.get(_col(df, "DEPOSIT NMASS", "deposit nmass")))
    margin = _num(row.get(_col(df, "TOTAL MARGIN", "total margin")))
    diff = _num(row.get(_col(df, "DIFFERENCE(F-G)", "difference")))
    warn = _num(row.get(_col(df, "MARGIN WARNING %", "margin warning")))
    deriv = _num(row.get(_col(df, "DERIVATIVE MARGIN USED", "derivative margin")))
    remarks = str(row.get(_col(df, "REMARKS", "remark"), "")).strip()
    zone = str(row.get(_col(df, "Zone", "zone"), "")).strip()

    facts = {
        "client": str(client_code).upper(),
        "file": name,
        "zone": zone,
        "deposit_nmass": _money(deposit),
        "total_margin": _money(margin),
        "difference_F_minus_G": _money(diff),
        "remarks": remarks,
        "margin_warning_pct": round(warn, 2) if warn is not None else None,
        "derivative_margin_used": _money(deriv),
    }

    # ── build a PLAIN-ENGLISH explanation from the real numbers ──────────────
    lines = []
    # shortfall explanation
    if diff is not None:
        if diff < 0:
            lines.append(f"{client_code} is SHORT by {_money(diff)}. "
                         f"Deposit (NMASS) is {_money(deposit)} but total margin used is "
                         f"{_money(margin)}, so deposit minus margin = {_money(diff)} (negative = shortfall).")
        elif diff == 0:
            lines.append(f"{client_code} has no difference — deposit exactly equals margin used.")
        else:
            lines.append(f"{client_code} is NOT short — deposit {_money(deposit)} exceeds "
                         f"margin used {_money(margin)} by {_money(diff)} (excess).")

    # margin-warning explanation (the decomposition)
    if warn is not None:
        driver = ""
        if deriv is not None and deposit:
            driver = (f" This is {round(warn,2)}% because the derivative margin used "
                      f"({_money(deriv)}) is that percentage of the deposit ({_money(deposit)}). "
                      f"MARGIN WARNING % = derivative margin ÷ deposit × 100.")
        # which side is driving it
        side = ""
        if deriv is not None and deposit is not None:
            if warn > 105:
                side = (" The warning is high mainly because derivative margin is large "
                        "relative to the deposit — either heavy derivative exposure, a low "
                        "deposit, or both.")
        lines.append(f"Margin Warning % is {round(warn,2)}.{driver}{side}")

    # WhatsApp explanation
    if warn is not None:
        if warn > 105:
            lines.append(f"{client_code} is ELIGIBLE for a WhatsApp alert because its "
                         f"Margin Warning % ({round(warn,2)}) is above the 105% threshold. "
                         f"Note: whether an alert was actually SENT also depends on the day's "
                         f"tracker (first crossing of the day, or a ±10-point move since the "
                         f"last alert) — that history is not in this file.")
        else:
            lines.append(f"{client_code} is NOT eligible for a WhatsApp alert — its Margin "
                         f"Warning % ({round(warn,2)}) is at or below the 105% threshold.")

    # the honest boundary
    boundary = ("What this CANNOT tell you: which specific trade or position pushed the "
                "derivative margin up. That needs the underlying FO/MCX position data, which "
                "is not in this output file. The explanation above is the real component "
                "breakdown from the file — not a guess about individual trades.")

    return {"facts": facts, "explanation": " ".join(lines), "boundary": boundary}


def analyze_ctcl(client_code: str) -> dict:
    """Explain a client's CTCL status using the REAL priority logic from the code:
       CPCODE -> VIRTUAL ID -> LIMIT EXCESS / OK -> (CTCL < 0 always OK)."""
    from core.agent import _find_output_file as find
    folder = os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    fp, name = find(folder, "ctcl", "latest")
    if not fp:
        return {"error": "CTCL output (FULL_FILE_CTCL_VS_NMASS) not found today."}
    df = _read_df(fp)
    row, acct = _find_client_row(df, client_code)
    if row is None:
        return {"error": f"Client {client_code} not found in the CTCL output."}

    nmass = _num(row.get(_col(df, "DEPOSIT NMASS", "deposit nmass")))
    ctcl = _num(row.get(_col(df, "DEPOSIT CTCL", "deposit ctcl", "ctcl limit")))
    diff = _num(row.get(_col(df, "DIFFERENCE(G-F)", "difference", "shortfall")))
    remarks = str(row.get(_col(df, "REMARKS", "remark"), "")).strip()
    zone = str(row.get(_col(df, "Zone", "zone"), "")).strip()

    # Explain by the actual remark the code assigned. Note DIFFERENCE is
    # sign-aware: CTCL >= 0 → CTCL − NMASS; CTCL < 0 → CTCL + NMASS.
    lines = []
    rem_u = remarks.upper()
    if "CPCODE" in rem_u:
        lines.append(f"{client_code} is marked CPCODE — it settles through a custodian, so its "
                     f"terminal limit is not measured against its own NMASS deposit. The "
                     f"limit-vs-deposit test is skipped entirely for these accounts.")
    elif "VIRTUAL" in rem_u:
        lines.append(f"{client_code} is marked VIRTUAL ID — this is treated as an internal/"
                     f"house pseudo-account (blank zone or on the virtual-account list) with "
                     f"no real deposit, so it's excluded from limit review.")
    elif ctcl is not None and ctcl < 0:
        lines.append(f"{client_code} is OK despite a debit balance: DEPOSIT CTCL is negative "
                     f"({_money(ctcl)}), which means no limit was actually granted against "
                     f"deposit. For these accounts the two figures are ADDED rather than "
                     f"subtracted ({_money(ctcl)} + {_money(nmass)} = {_money(diff)}), and the "
                     f"row is never flagged LIMIT EXCESS however large the debit is.")
    elif "LIMIT EXCESS" in rem_u or "SHORTFALL" in rem_u or (diff is not None and diff > 0):
        lines.append(f"{client_code} is a LIMIT EXCESS of {_money(diff)}. Their CTCL limit "
                     f"({_money(ctcl)}) exceeds their NMASS deposit ({_money(nmass)}) by that "
                     f"amount — i.e. they've been allowed to trade on more limit than the "
                     f"collateral actually lodged at the exchange (DIFFERENCE = CTCL − NMASS, "
                     f"positive = excess).")
    elif diff is not None and diff < 0:
        lines.append(f"{client_code} is OK: lodged collateral (NMASS {_money(nmass)}) exceeds "
                     f"the CTCL limit ({_money(ctcl)}) by {_money(abs(diff))} — safe, no action "
                     f"needed.")
    else:
        lines.append(f"{client_code} is OK — no difference between CTCL limit and NMASS deposit.")

    boundary = ("This explains the limit-vs-deposit gap and the remark from the file. It does "
                "not include WHY the limit was set or why collateral changed — that lives in "
                "the back-office limit and collateral files, not this output.")
    return {"facts": {"client": str(client_code).upper(), "file": name, "zone": zone,
                      "deposit_nmass": _money(nmass), "deposit_ctcl": _money(ctcl),
                      "difference_G_minus_F": _money(diff), "remarks": remarks},
            "explanation": " ".join(lines),
            "boundary": boundary}
