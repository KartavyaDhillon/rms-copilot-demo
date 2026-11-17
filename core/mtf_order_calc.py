"""core/mtf_order_calc.py - the MTF ORDER FILE calculation (the maths of MTF_ORDER_FILE_final.py, nothing else).

No mail, no backup, no hard-coded folder: it reads three files and writes the order files into one folder.
    MTFPOSITION_<ddmmyyyy>.csv       the day's MTF positions                       (COMMON FILES)
    MTF_RISK_SHORTAGE_REPORT.xlsx    accounts with a net shortage (mail attachment)
    RISK_REPORT_<ddmmyyyy>.csv       gives every account its TRADING_SOFTWARE_TYPE  (COMMON FILES)

Per account with a NEGATIVE net shortage, stocks are chosen to release that much margin:
  1. every stock whose ClRate - MtfRate is negative (a loss), the biggest loss first, within a Rs 100 buffer;
  2. then the single stock whose releasable margin is closest to what is still short;
  3. then the cheapest-margin stocks while they still fit inside the buffer.
Output: MTFPOSITION_<d>_SUMMARY.csv, ORD_KAMB_MTFS_<d>.csv (Kambala), ORD_O319_MTFS_<d>.csv (0319), and the same split by zone.
The logic, the column names and the file formats are those of the hand script; only the order of the summary rows is now fixed.
"""
from __future__ import annotations

import math
import re
import warnings
from pathlib import Path

import pandas as pd
import polars as pl

warnings.filterwarnings("ignore")

TOLERANCE = 100.0
ZONES = {"CHND": ["ZOCHND"], "MUMB": ["ZOMUMB"], "LUDH": ["ZOLUDH", "ZO"], "DELH": []}     # DELH = everything else
ZONE_ORDER = ("CHND", "MUMB", "LUDH", "DELH")
SHORTAGE_STEM = "MTF_RISK_SHORTAGE_REPORT"
EDITED_NAME = "MtfPosition_edited.csv"


class CalcError(Exception):
    """A file is not what the calculation needs (the message says what)."""


def safe_num(x) -> float:
    try:
        if pd.isna(x):
            return 0.0
        s = str(x).replace(",", "").strip()
        return float(s) if s else 0.0
    except Exception:                                            # noqa: BLE001
        return 0.0


def safe_int(x) -> int:
    try:
        return int(round(safe_num(x)))
    except Exception:                                            # noqa: BLE001
        return 0


def read_str(path) -> pd.DataFrame:
    """Any csv / txt / xlsx as an all-text frame (an empty cell is empty, nothing is guessed)."""
    path = str(path)
    ext = path.rsplit(".", 1)[-1].lower()
    if ext in ("csv", "txt"):
        return pd.read_csv(path, dtype=object, encoding_errors="replace", on_bad_lines="skip")
    if ext in ("xls", "xlsx"):
        return pd.read_excel(path, dtype=object)
    raise CalcError(f"unsupported file type: {path}")


def find_columns(df: pd.DataFrame):
    """(account column, net-shortage column) of the shortage report, or (None, None)."""
    acct = net = None
    for c in df.columns:
        if str(c).strip().lower().replace(" ", "") in ("accountid", "account_id", "account"):
            acct = c
        if "net" in str(c).lower() and "short" in str(c).lower():
            net = c
    return acct, net


_PROBLEM_CACHE: dict = {}


def shortage_problem(path) -> str:
    """'' when the file is a usable shortage report, else why not. Cached per file version: reading a 30,000-row
    workbook takes seconds and the card's status is polled every few seconds."""
    try:
        st = Path(path).stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return "file not found"
    if key not in _PROBLEM_CACHE:
        if len(_PROBLEM_CACHE) > 20:
            _PROBLEM_CACHE.clear()
        _PROBLEM_CACHE[key] = _shortage_problem(path)
    return _PROBLEM_CACHE[key]


def _shortage_problem(path) -> str:
    try:
        df = read_str(path)
    except Exception as e:                                       # noqa: BLE001
        return f"unreadable ({type(e).__name__}: {e})"
    acct, net = find_columns(df)
    if acct is None:
        return "no 'Account ID' column"
    if net is None:
        return "no 'Net shortage' column"
    cols = {str(c).strip() for c in df.columns}
    if "Zone" not in cols:
        return "no 'Zone' column"
    return ""


def make_edited(mtf_file, edited_path) -> pd.DataFrame:
    """Part 1: the position file with the two working columns added (saved as MtfPosition_edited.csv). Returns the numeric frame."""
    pdf = read_str(mtf_file)
    if "AccountID" not in pdf.columns:
        raise CalcError("MTFPOSITION has no 'AccountID' column")
    for col in ["ClRate", "MtfRate", "MtfVar", "Qty", "MtfValueClosing"]:
        if col not in pdf.columns:
            pdf[col] = pd.NA
        pdf[col] = pdf[col].apply(safe_num)
    pdf["Qty"] = pdf["Qty"].apply(safe_int)
    pdf["MtfValueClosing"] = pdf.apply(
        lambda r: round(r["Qty"] * r["ClRate"], 2) if r["MtfValueClosing"] == 0 else r["MtfValueClosing"], axis=1)
    pdf["ClRate-MtfRate(L-K)"] = (pdf["ClRate"] - pdf["MtfRate"]).round(2)
    pdf["Margin to be released(U*N/100)"] = ((pdf["MtfValueClosing"] * pdf["MtfVar"]) / 100).round(2)
    pdf.to_csv(edited_path, index=False)
    return pdf


def match_accounts(pdf: pd.DataFrame, risk_short: pd.DataFrame, acct_col, net_col, tolerance: float = TOLERANCE) -> set:
    """Part 2: choose the stocks (and quantities) for every shortage. Updates pdf in place, returns the used row labels."""
    acct_map = {}
    for idx, row in pdf.iterrows():
        acct_map.setdefault(str(row["AccountID"]).strip(), []).append(idx)
    used = set()
    for _, r in risk_short.iterrows():
        acct = str(r[acct_col]).strip()
        shortage = abs(safe_num(r[net_col]))
        if acct not in acct_map:
            continue
        cands = []
        for idx in acct_map[acct]:
            cands.append({"idx": idx, "margin": pdf.at[idx, "_Margin"], "unit": pdf.at[idx, "_unit_margin"],
                          "ClRate": pdf.at[idx, "_ClRate"], "Qty": pdf.at[idx, "_Qty"],
                          "lk": safe_num(pdf.at[idx, "ClRate-MtfRate(L-K)"])})
        used_map = {}
        remaining = shortage
        # STEP 1 - every loss-making (negative L-K) stock first, biggest loss first
        neg_cands = sorted([c for c in cands if c["lk"] < 0], key=lambda x: x["lk"])
        for s in neg_cands:
            if remaining <= 0:
                break
            unit, qty_avail = s["unit"], s["Qty"]
            if unit <= 0 or qty_avail <= 0:
                continue
            full_rel = qty_avail * unit
            if full_rel - remaining <= tolerance:
                used_map[s["idx"]] = used_map.get(s["idx"], 0) + qty_avail
                remaining -= full_rel
                continue
            q = qty_avail
            while q > 0 and ((q * unit) - remaining) > tolerance:
                q -= 1
            if q > 0:
                used_map[s["idx"]] = used_map.get(s["idx"], 0) + q
                remaining -= q * unit
        # the loss-making stocks are never touched again
        neg_used = {c["idx"] for c in neg_cands}
        avail = [c for c in cands if c["idx"] not in neg_used]
        # STEP 2 - the other stocks: the one whose margin is closest to the rest, then the cheapest ones
        if remaining > 0 and avail:
            def pkey(c):
                return (abs(c["margin"] - remaining), 0 if c["margin"] <= remaining else 1, c["margin"])
            primary = min(avail, key=pkey)
            pidx, unit, have = primary["idx"], primary["unit"], primary["Qty"]
            if unit > 0 and have > 0:
                qty_need = min(math.ceil(remaining / unit), have)
                released = qty_need * unit
                while qty_need > 0 and released > (remaining + tolerance):
                    qty_need -= 1
                    released = qty_need * unit
                if qty_need > 0:
                    used_map[pidx] = used_map.get(pidx, 0) + qty_need
                    remaining -= released
            if remaining > 0:
                for s in sorted([c for c in avail if c["idx"] != pidx and c["unit"] > 0], key=lambda x: x["unit"]):
                    if remaining <= 0:
                        break
                    unit, qty_avail = s["unit"], s["Qty"]
                    qmax = min(qty_avail, math.floor(remaining / unit))
                    if qmax <= 0:
                        continue
                    rel2 = qmax * unit
                    if rel2 - remaining <= tolerance:
                        used_map[s["idx"]] = used_map.get(s["idx"], 0) + qmax
                        remaining -= rel2
        for idx, qtake in used_map.items():
            qtake = int(qtake)
            pdf.at[idx, "Qty"] = qtake
            new_val = round(qtake * pdf.at[idx, "_ClRate"], 2)
            pdf.at[idx, "MtfValueClosing"] = new_val
            pdf.at[idx, "Margin to be released(U*N/100)"] = round(new_val * pdf.at[idx, "_MtfVar"] / 100, 2)
            used.add(idx)
    return used


def _in_zone(series: pd.Series, zone: str) -> pd.Series:
    others = ZONES["CHND"] + ZONES["MUMB"] + ZONES["LUDH"]
    return ~series.isin(others) if zone == "DELH" else series.isin(ZONES[zone])


def build_order_files(mtf_file, shortage_file, risk_report_file, out_dir, date_part: str, log=print) -> dict:
    """The whole calculation. Writes into out_dir; returns the paths and counts (see the end)."""
    out_dir = Path(out_dir)
    mtf_file, shortage_file, risk_report_file = Path(mtf_file), Path(shortage_file), Path(risk_report_file)
    edited = out_dir / EDITED_NAME
    summary_path = out_dir / f"MTFPOSITION_{date_part}_SUMMARY.csv"

    # ---- part 1: edited position file ----
    log(f"reading {mtf_file.name}")
    make_edited(mtf_file, edited)
    pdf = pd.read_csv(str(edited), dtype=object)
    original_columns = list(pdf.columns)
    pdf["_ClRate"] = pdf["ClRate"].apply(safe_num)
    pdf["_Qty"] = pdf["Qty"].apply(safe_int)
    pdf["_Margin"] = pdf["Margin to be released(U*N/100)"].apply(safe_num)
    pdf["_MtfVar"] = pdf["MtfVar"].apply(safe_num)
    pdf["_unit_margin"] = (pdf["_ClRate"] * pdf["_MtfVar"] / 100).round(6)

    # ---- shortage report: keep only the negative net shortages (the file itself is cut down, as the hand script does) ----
    log(f"reading {shortage_file.name}")
    rpdf = read_str(shortage_file)
    acct_col, net_col = find_columns(rpdf)
    if acct_col is None or net_col is None:
        raise CalcError(f"{shortage_file.name}: needs an 'Account ID' and a 'Net shortage' column")
    rpdf["_net"] = rpdf[net_col].apply(safe_num)
    negative = rpdf[rpdf["_net"] < 0].drop(columns=["_net"]).copy()
    if shortage_file.suffix.lower() == ".xlsx":
        negative.to_excel(shortage_file, index=False)
    else:
        negative.to_csv(shortage_file, index=False)
    log(f"{len(negative)} account(s) with a negative net shortage (of {len(rpdf)} in the report)")
    risk_short = negative.copy()
    risk_short["_abs"] = risk_short[net_col].apply(safe_num).abs()
    risk_short = risk_short.sort_values("_abs", ascending=False, kind="mergesort")

    used = match_accounts(pdf, risk_short, acct_col, net_col)
    final_pdf = pdf.loc[sorted(used), original_columns]
    for col in final_pdf.columns:
        if pd.api.types.is_float_dtype(final_pdf[col]):
            final_pdf[col] = final_pdf[col].round(2)
    final_pdf.to_csv(summary_path, index=False)
    log(f"{len(final_pdf)} stock line(s) chosen")

    # ---- TRADING_SOFTWARE_TYPE from the risk report ----
    pl_final = pl.read_csv(str(summary_path), infer_schema_length=0, ignore_errors=True)
    if "TRADING_SOFTWARE_TYPE" in pl_final.columns:
        pl_final = pl_final.drop("TRADING_SOFTWARE_TYPE")
    pl_rrep = pl.read_csv(str(risk_report_file), infer_schema_length=0, ignore_errors=True)
    for need in ("ACCOUNTID", "TRADING_SOFTWARE_TYPE"):
        if need not in pl_rrep.columns:
            raise CalcError(f"{risk_report_file.name} has no '{need}' column")
    small = (pl_rrep.select([pl.col("ACCOUNTID").cast(pl.Utf8).alias("AccountID"),
                             pl.col("TRADING_SOFTWARE_TYPE").cast(pl.Utf8)])
             .unique(subset=["AccountID"], keep="first", maintain_order=True))     # one row per account: no stock can be listed twice
    joined = pl_final.join(small, on="AccountID", how="left")
    cols, new_cols = joined.columns, []
    for c in cols:
        new_cols.append(c)
        if c == "AccountID":
            new_cols.append("TRADING_SOFTWARE_TYPE")
    new_cols = [x for i, x in enumerate(new_cols) if x not in new_cols[:i]]
    joined.select(new_cols).write_csv(str(summary_path))

    # ---- the two order files ----
    sdf = pd.read_csv(summary_path, dtype=str)
    sdf["ScripName"] = sdf["ScripName"].astype(str).str.strip()
    sdf["AccountID"] = sdf["AccountID"].astype(str).str.strip()
    sdf["Qty"] = pd.to_numeric(sdf["Qty"], errors="coerce").fillna(0).astype(int)
    kdf = sdf[sdf["TRADING_SOFTWARE_TYPE"] == "KAMB"].copy()
    n = len(kdf)
    kamb_path = out_dir / f"ORD_KAMB_MTFS_{date_part}.csv"
    pd.DataFrame({
        "Exchange": ["NSE"] * n, "Instr": ["EQ"] * n, "Symbol": kdf["ScripName"], "OptionType": [""] * n, "Strike": [0] * n,
        "ExpDate": [""] * n, "AccountId": kdf["AccountID"], "BUY/SELL": ["SELL"] * n, "CustFirm": ["C"] * n,
        "Product": ["MTFS"] * n, "PriceType": ["MKT"] * n, "OrdDuration": ["DAY"] * n, "Price": [0] * n, "Qty": kdf["Qty"],
        "TriggerPrice": [0] * n, "DiscQty": [0] * n, "Remarks": ["0:00"] * n, "MKT Prot%": [3] * n, "PCode": [""] * n,
        "OrderValue": [""] * n}).to_csv(kamb_path, index=False)
    log(f"Kambala order file: {n} order(s)")
    odf = sdf[sdf["TRADING_SOFTWARE_TYPE"] == "O319"].copy()
    m = len(odf)
    o319_path = None
    if m:
        o319_path = out_dir / f"ORD_O319_MTFS_{date_part}.csv"
        pd.DataFrame({
            "EXCHANGE": ["NSE"] * m, "SERIES": ["EQ"] * m, "SYMBOL": odf["ScripName"], "FILLER1": ["NA"] * m,
            "FILLER2": ["NA"] * m, "FILLER3": ["NA"] * m, "FILLER4": [0] * m, "QTY1": odf["Qty"], "QTY2": odf["Qty"],
            "BUY/SELL": ["SELL"] * m, "ORDER TYPE": ["MARKET"] * m, "FILLER5": ["NA"] * m, "CLI": ["CLI"] * m,
            "PRODUCT TYPE": ["MTFS"] * m, "VALIDITY": ["DAY"] * m, "CLIENT CODE": odf["AccountID"]}).to_csv(o319_path, index=False)
        log(f"0319 order file: {m} order(s)")
    else:
        log("no 0319 account - no 0319 order file")

    # ---- zone split ----
    risk_zone_df = read_str(shortage_file)
    risk_zone_df.columns = risk_zone_df.columns.str.strip()
    if "Account ID" not in risk_zone_df.columns or "Zone" not in risk_zone_df.columns:
        raise CalcError("the shortage report needs 'Account ID' and 'Zone' columns for the zone split")
    risk_zone_df["Account ID"] = risk_zone_df["Account ID"].astype(str).str.strip()
    risk_zone_df["Zone"] = risk_zone_df["Zone"].astype(str).str.strip().str.upper()
    zone_map = dict(zip(risk_zone_df["Account ID"], risk_zone_df["Zone"]))      # an account the report does not know goes to DELH

    kamb_raw = pd.read_csv(kamb_path, dtype=str)
    kamb_raw["_Zone"] = kamb_raw["AccountId"].astype(str).map(zone_map).fillna("DELH")
    o319_raw = None
    if o319_path:
        o319_raw = pd.read_csv(o319_path, dtype=str)
        o319_raw["_Zone"] = o319_raw["CLIENT CODE"].astype(str).map(zone_map).fillna("DELH")
    summary_raw = pd.read_csv(summary_path, dtype=str)
    summary_raw["Zone"] = summary_raw["Zone"].astype(str).str.strip().str.upper()

    zones = {}
    for z in ZONE_ORDER:
        sp = out_dir / f"MTFPOSITION_{date_part}_SUMMARY_{z}.csv"
        s_zone = summary_raw[_in_zone(summary_raw["Zone"], z)]
        s_zone.to_csv(sp, index=False)
        kp = out_dir / f"ORD_KAMB_MTFS_{date_part}_{z}.csv"
        k_zone = kamb_raw[_in_zone(kamb_raw["_Zone"], z)]
        k_zone.drop(columns=["_Zone"], errors="ignore").to_csv(kp, index=False)
        op, on = None, 0
        if o319_raw is not None:
            o_zone = o319_raw[_in_zone(o319_raw["_Zone"], z)]
            on = len(o_zone)
            if on:
                op = out_dir / f"ORD_O319_MTFS_{date_part}_{z}.csv"
                o_zone.drop(columns=["_Zone"], errors="ignore").to_csv(op, index=False)
        rp = out_dir / f"{SHORTAGE_STEM}_{z}.csv"
        risk_zone_df[_in_zone(risk_zone_df["Zone"], z)].to_csv(rp, index=False)
        zones[z] = {"summary": sp, "kamb": kp, "o319": op, "risk": rp,
                    "counts": {"summary": len(s_zone), "kamb": len(k_zone), "o319": on}}
        log(f"zone {z}: {len(k_zone)} Kambala + {on} 0319 order(s)")
    return {"edited": edited, "summary": summary_path, "kamb": kamb_path, "o319": o319_path, "risk_file": shortage_file,
            "zones": zones,
            "counts": {"shortage_accounts": len(negative), "stock_lines": len(final_pdf), "kamb": n, "o319": m,
                       "accounts": int(sdf["AccountID"].nunique())}}


def date_part_of(mtf_file) -> str:
    """'17082026' from MTFPOSITION_17082026.csv."""
    m = re.match(r"^MTFPOSITION_(\d{8})", Path(mtf_file).name, re.I)
    if not m:
        raise CalcError(f"cannot read the date from {Path(mtf_file).name}")
    return m.group(1)
