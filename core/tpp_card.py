"""core/tpp_card.py - TENTATIVE PAYIN PAYOUT card (NARRATION_COMBINED.py as a console card).

What the job does (the maths is the script's, copied unchanged - NORMAL and CUSTODIAN reports from the same two files):
    Positionbook_<ddmmyyyy>.csv/.xlsx (uploaded in the card - NOT the CNC F11 PositionBook)  +  RISK_REPORT_<ddmmyyyy>.csv (read from COMMON FILES)
    -> CASH (buying / selling), FUTURE and MCX (MTM + premium + exercise value of ITM contracts expiring today) per client
    -> PayIn_PayOut_Summary_<tag>.xlsx/.png and Custodian_PayIn_PayOut_Summary_<tag>.xlsx/.png
       (Excel password protected: Scenario@<ddmmyyyy>; a report whose figures are all zero is left out)
    -> ONE mail "Tentative Obligation File-<ddmmyyyy>" (pictures in the body, Excel attached)
    -> the day's files go to BACKUP FILES AUTOMATED\\<dd-mm-yyyy> (the risk report in COMMON FILES is only copied, never moved).

Settlement prices (ITM contracts expiring today): asked in a console popup. They can be typed, or taken from ANY csv / xlsx file:
the popup lists the file's column names and you say which one is the SYMBOL and which one is the PRICE (the choice is remembered).

Different from the hand script, on purpose: no mail login in the code (sender = the card's Credentials or the shared sender; To / CC =
the card's Email To / CC window), the mail is tried 5 times and then the run FAILS (files stay), run by the button only, once a day
(a second run asks for the password), the popups are the console's own.
"""
from __future__ import annotations

import collections
import datetime as dt
import io
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NAME = "TENTATIVE PAYIN PAYOUT"
FOLDER_NAME = "TENTATIVE PAYIN PAYOUT"
PIPE = "tpp"
BACKUP_SUB = "BACKUP FILES AUTOMATED"
MAIL_SUBJECT_PREFIX = "Tentative Obligation File"
STEP_LABEL = {"input": "Inputs", "settle": "Settlement prices", "calc": "Reports", "email": "E-mail", "backup": "Backup"}
STEP_ORDER = ("input", "settle", "calc", "email", "backup")
MAIL_DEFAULTS = {"REPORT": ("sukhbirsingh@mastertrust.co.in,va@mastertrust.co.in,rks@mastertrust.co.in",
                            "abhishek.dhiman@mastertrust.co.in,priyanshu.karna@mastertrust.co.in,"
                            "sumit.aggarwal@mastertrust.co.in,sandeep.kumar2@mastertrust.co.in")}
MAIL_LABEL = {"REPORT": "Tentative Obligation file"}
FRESH_MIN = int(os.getenv("TPP_FRESH_MIN", "60") or 60)          # a PositionBook with no date in its name must have been saved this recently
REQ_COLS = ["Exch", "ClientID", "OpType", "Ind Moneyness", "Symbol", "MToM", "DayBuyAmount", "DaySellAmount",
            "StrikePrice", "NetQty", "ExpDate"]
_SKIP_NAMES = ("settlement_", "payin_payout", "custodian_payin")

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_PLT_LOCK = threading.Lock()
_LOG = collections.deque(maxlen=400)
_ACTIVE = {"cancel": False, "log_file": None}


class Busy(Exception):
    pass


class PreRunError(Exception):
    pass


class Cancelled(Exception):
    pass


# ───────────────────────────── mail defaults (shared Email To / CC window) ─────────────────────────────
def mail_keys(k: str) -> tuple:
    return (f"TPP_{k}_TO", f"TPP_{k}_CC")


def apply_defaults() -> None:
    for k, (to, cc) in MAIL_DEFAULTS.items():
        tk, ck = mail_keys(k)
        os.environ.setdefault(tk, to)
        os.environ.setdefault(ck, cc)


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("TPP_DIR", "").strip()
    if v:
        p = Path(v)
    else:
        try:
            from core.scheduler import paths
            base = paths.data_root()
        except Exception:                                        # noqa: BLE001
            base = Path(__file__).resolve().parent.parent / "data"
        p = base / FOLDER_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_dir() -> Path:
    p = root() / "_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_root() -> Path:
    return root() / BACKUP_SUB


def log_dir() -> Path:
    v = os.getenv("TPP_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / "tpp"
    except Exception:                                            # noqa: BLE001
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def common_dir() -> Path:
    return Path(os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES"))


def _today() -> dt.date:
    return dt.date.today()


def _iso(t=None) -> str:
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def _check_date(s: str) -> str:
    if not re.fullmatch(r"\d{2}-\d{2}-\d{4}", s or ""):
        raise ValueError("date must be dd-mm-yyyy")
    return s


# ───────────────────────────── settings / state ─────────────────────────────
def _settings_path() -> Path:
    return _state_dir() / "settings.json"


def get_settings() -> dict:
    d = {"send_mail": True, "alert_numbers": "", "settle_map": {}}
    try:
        d.update(json.loads(_settings_path().read_text(encoding="utf-8")))
    except Exception:                                            # noqa: BLE001
        pass
    return d


def _write_settings(cur: dict) -> None:
    with _STATE_LOCK:
        _settings_path().write_text(json.dumps(cur, indent=1), encoding="utf-8")


def save_settings(update: dict, by: str = "") -> dict:
    cur = get_settings()
    if "send_mail" in update:
        cur["send_mail"] = bool(update["send_mail"])
    if "alert_numbers" in update:
        raw = str(update["alert_numbers"] or "").strip()
        if raw:
            from core.scheduler import failure_alert
            if not failure_alert.parse_numbers(raw):
                raise ValueError("those WhatsApp numbers are not valid")
        cur["alert_numbers"] = raw
    cur["by"], cur["at"] = by, _iso()
    _write_settings(cur)
    return cur


def _state_path() -> Path:
    return _state_dir() / "state.json"


def read_state() -> dict:
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        st = {}
    if st.get("date") != _today().isoformat():
        return {"date": _today().isoformat(), "status": "idle", "steps": {}}
    st.setdefault("steps", {})
    st.setdefault("status", "idle")
    return st


def _write_state(st: dict):
    with _STATE_LOCK:
        tmp = _state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        tmp.replace(_state_path())


def _update(**kw) -> dict:
    with _STATE_LOCK:
        st = read_state()
        st.update(kw)
        _write_state(st)
        return st


def _set_step(name: str, status: str, msg: str = ""):
    with _STATE_LOCK:
        st = read_state()
        st["steps"][name] = {"status": status, "msg": msg[:300], "at": _iso()}
        _write_state(st)


# ───────────────────────────── logging / stop ─────────────────────────────
def log(msg: str):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    _LOG.append(line)
    f = _ACTIVE.get("log_file")
    if f:
        try:
            f.write(line + "\n")
            f.flush()
        except Exception:                                        # noqa: BLE001
            pass
    print("[tpp] " + msg, flush=True)


def recent_log(n: int = 120) -> list:
    return list(_LOG)[-n:]


def _open_run_log(trigger, by):
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"tpp__{now:%d-%m-%Y}__{now:%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== {NAME} - {now:%d-%m-%Y %H:%M:%S} =====\nstarted by: {by or trigger} ({trigger})\n\n")
        f.flush()
        return f
    except Exception:                                            # noqa: BLE001
        return None


def _check_cancel():
    if _ACTIVE.get("cancel"):
        raise Cancelled("Stopped.")


# ───────────────────────────── the script's constants and calculation (unchanged) ─────────────────────────────
SUBJECT_PREFIX = "Tentative Obligation File"            # subject = "<prefix>-DDMMYYYY"
SUBTITLE = "Cash  |  Futures  |  MCX"
NORMAL = {"key": "normal", "title": "Pay-In / Pay-Out Summary", "file_prefix": "PayIn_PayOut_Summary",
          "heading": "Normal Accounts", "custodian": False}
CUSTODIAN = {"key": "custodian", "title": "Custodian Pay-In / Pay-Out Summary",
             "file_prefix": "Custodian_PayIn_PayOut_Summary", "heading": "Custodian Accounts", "custodian": True}

FO_EXCH = ["NFO", "BFO"]
CASH_EXCH = ["NSE", "BSE", "EQT", "MSE"]
MCX_EXCH = ["MCX"]
CUSTODIAN_TOKEN = "MASTE"      # custodian accounts = CPCode containing this text

DATE_FORMATS = ["%d-%b-%y", "%d-%b-%Y", "%d-%m-%Y", "%d-%m-%y", "%d/%m/%Y", "%d/%m/%y",
                "%Y-%m-%d", "%Y/%m/%d", "%d%b%Y", "%d%b%y", "%Y%m%d", "%d.%m.%Y", "%d %b %Y",
                "%d-%B-%Y", "%d-%B-%y"]



def indian(n):
    """Indian digit grouping, no decimals, keep sign."""
    n = int(round(n))
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return sign + s


def parse_dates(s):
    s = s.astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for f in DATE_FORMATS:
        todo = out.isna() & (s != "")
        if not todo.any():
            break
        out[todo] = pd.to_datetime(s[todo], format=f, errors="coerce")
    bad = (out.isna() & (s != "")).sum()
    if bad:
        log(f"WARNING: {bad} ExpDate values could not be parsed (ignored for expiry check)")
    return out


def cp_has(series, *tokens):
    pat = "|".join(re.escape(t) for t in tokens)
    return series.str.contains(pat, case=False, regex=True, na=False)



def expiry_today(df, today):
    return df["ExpDate_dt"].dt.date == today


def derivative_block(d, exch_list, cp_tokens, today, label, settle_prices, pick=False):
    """FUTURE / MCX. MTM (OpType == XX) + PREMIUM (OpType != XX) + exercise value on expiry day for ITM."""
    base = d[d["Exch"].isin(exch_list)].copy()
    hit = cp_has(base["CPCode"], *cp_tokens)
    base = base[hit] if pick else base[~hit]          # pick=True -> custodian mode (keep matches)
    base["IsExpToday"] = expiry_today(base, today)
    info = {"rows_after_exch_cp": len(base), "expiry_today_rows": int(base["IsExpToday"].sum())}

    xx = base[base["OpType"] == "XX"]
    mtm = xx.groupby("ClientID", as_index=False)["MToM"].sum().sort_values("ClientID")
    mtm = mtm[mtm["MToM"] != 0]

    px = base[base["OpType"] != "XX"].copy()
    info["premium_rows"] = len(px)
    prem = px.groupby("ClientID", as_index=False)[["DayBuyAmount", "DaySellAmount"]].sum()
    prem["NetPremium"] = prem["DaySellAmount"] - prem["DayBuyAmount"]

    ex = px[px["IsExpToday"] & (px["Ind Moneyness"] == "ITM") & px["Symbol"].isin(list(settle_prices))].copy()
    info["itm_expiry_rows"] = len(ex)
    if len(ex):
        ex["Settlement"] = ex["Symbol"].map(settle_prices)
        ex["LTP_SETTLE_MINUS_STRIKE"] = ex["Settlement"] - ex["StrikePrice"]
        ex["LTP_ABS"] = ex["LTP_SETTLE_MINUS_STRIKE"].abs()
        ex["exrcassgnedval"] = ex["LTP_ABS"] * ex["NetQty"]
        exc = ex.groupby("ClientID", as_index=False)["exrcassgnedval"].sum()
    else:
        exc = pd.DataFrame(columns=["ClientID", "exrcassgnedval"])
    exc["exrcassgnedval"] = exc["exrcassgnedval"].astype(float)
    prem = prem.merge(exc, on="ClientID", how="outer")
    num_cols = ["DayBuyAmount", "DaySellAmount", "NetPremium", "exrcassgnedval"]
    prem[num_cols] = prem[num_cols].astype(float).fillna(0.0)
    prem["Premium"] = prem["NetPremium"] + prem["exrcassgnedval"]
    prem = prem.sort_values("ClientID").reset_index(drop=True)

    ex_cols = ["Exch", "ClientID", "Symbol", "ExpDate", "StrikePrice", "OpType", "NetQty",
               "Ind Moneyness", "Settlement", "LTP_SETTLE_MINUS_STRIKE", "LTP_ABS", "exrcassgnedval"]
    ex_out = ex[ex_cols] if len(ex) else pd.DataFrame(columns=ex_cols)
    return {"label": label, "mtm": mtm, "prem": prem, "exercise": ex_out, "info": info,
            "mtm_total": float(mtm["MToM"].sum()), "prem_total": float(prem["Premium"].sum())}


def itm_symbols(d, today):
    out = {}
    m = d[(d["Exch"].isin(FO_EXCH + MCX_EXCH)) & (d["OpType"] != "XX") & (d["Ind Moneyness"] == "ITM")
          & expiry_today(d, today)]
    for sym, g in m.groupby("Symbol"):
        out[sym] = {"exch": ", ".join(sorted(g["Exch"].unique())), "rows": len(g),
                    "strikes": f"{g['StrikePrice'].min():,.0f} - {g['StrikePrice'].max():,.0f}",
                    "optional": set(g["Exch"].unique()) <= set(MCX_EXCH)}
    return out


def cash_block(d, tokens=("CAP",), pick=False):
    c = d[d["Exch"].isin(CASH_EXCH)].copy()
    hit = cp_has(c["CPCode"], *tokens)
    c = c[hit] if pick else c[~hit]
    g = c.groupby("ClientID", as_index=False)[["DayBuyAmount", "DaySellAmount"]].sum().sort_values("ClientID")
    return {"df": g, "buying": float(g["DayBuyAmount"].sum()), "selling": float(g["DaySellAmount"].sum()), "rows": len(c)}


def build_narration(cash, fut, mcx):
    def label(base, total, positive_is_in):
        if abs(total) < 0.005:
            return f"{base} PAYIN PAYOUT"
        return f"{base} {'PAYIN' if (total > 0) == positive_is_in else 'PAYOUT'}"

    b, s = cash["buying"], -cash["selling"]
    t = b + s
    rows = [[label("CASH", t, True), t, b, s, 0.0, 0.0]]
    for base, blk in (("FUTURE", fut), ("MCX", mcx)):
        f, p = blk["mtm_total"], blk["prem_total"]
        t = f + p
        rows.append([label(base, t, False), t, 0.0, 0.0, f, p])
    return rows


def build_report(der, today, settle, spec):
    """One complete report (normal or custodian) -> dict with narration rows + blocks."""
    if spec["custodian"]:
        fut = derivative_block(der, FO_EXCH, [CUSTODIAN_TOKEN], today, "FUTURE", settle, pick=True)
        mcx = derivative_block(der, MCX_EXCH, [CUSTODIAN_TOKEN], today, "MCX", settle, pick=True)
        cash = cash_block(der, (CUSTODIAN_TOKEN,), pick=True)
        rule = f"CUSTODIAN: keep only CPCode containing {CUSTODIAN_TOKEN} (all 3 segments)"
    else:
        fut = derivative_block(der, FO_EXCH, ["BSEFNO", "NSEFNO"], today, "FUTURE", settle)
        mcx = derivative_block(der, MCX_EXCH, ["MCXCOM"], today, "MCX", settle)
        cash = cash_block(der)
        rule = "FUTURE: drop BSEFNO/NSEFNO | CASH: drop CAP | MCX: drop MCXCOM"
    for blk in (fut, mcx):
        log(f"[{spec['key']}] {blk['label']}: MTM {blk['mtm_total']:,.2f} | Premium {blk['prem_total']:,.2f} | {blk['info']}")
    log(f"[{spec['key']}] CASH: Buying {cash['buying']:,.2f} | Selling {cash['selling']:,.2f} | rows {cash['rows']}")
    narr = build_narration(cash, fut, mcx)
    if spec["custodian"]:
        # custodian report: show ONLY the rows that have a value (a blank CASH / MCX row is left out)
        narr = [r for r in narr if any(round(float(v)) != 0 for v in r[1:])]
    return {"spec": spec, "fut": fut, "mcx": mcx, "cash": cash, "narr": narr, "rule": rule}


def write_excel(path, rep, settle, checks):
    narr, cash, fut, mcx = rep["narr"], rep["cash"], rep["fut"], rep["mcx"]
    wb = Workbook()
    hdr_fill = PatternFill("solid", fgColor="1F3864")
    hdr_font = Font(bold=True, color="FFFFFF")
    tot_fill = PatternFill("solid", fgColor="D9E1F2")
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    num = '#,##0.00;[Red]-#,##0.00'

    ws = wb.active
    ws.title = "NARRATION"
    ws.append(["Narration", "Total", "Buying", "Selling", "Future/MCX MTM", "Premium"])
    for r in narr:
        ws.append(r)
    for c in ws[1]:
        c.fill, c.font, c.border, c.alignment = hdr_fill, hdr_font, border, Alignment(horizontal="center")
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for i, c in enumerate(row):
            c.border = border
            if i:
                c.number_format = num
    for i in range(1, 7):
        ws.column_dimensions[get_column_letter(i)].width = 26 if i == 1 else 20

    def sheet(name, df, total_cols=None):
        w = wb.create_sheet(name)
        w.append(list(df.columns))
        for rec in df.itertuples(index=False):
            w.append([None if (isinstance(v, float) and pd.isna(v)) else v for v in rec])
        for c in w[1]:
            c.fill, c.font, c.border, c.alignment = hdr_fill, hdr_font, border, Alignment(horizontal="center")
        for row in w.iter_rows(min_row=2, max_row=w.max_row):
            for c in row:
                c.border = border
                if isinstance(c.value, (int, float)):
                    c.number_format = num
        if total_cols:
            last = w.max_row
            row = ["TOTAL"] + [None] * (len(df.columns) - 1)
            for cname in total_cols:
                idx = list(df.columns).index(cname)
                L = get_column_letter(idx + 1)
                row[idx] = f"=SUM({L}2:{L}{last})" if last >= 2 else 0
            w.append(row)
            for c in w[w.max_row]:
                c.font, c.fill, c.border = Font(bold=True), tot_fill, border
                if c.value is not None and c.column > 1:
                    c.number_format = num
        for i, col in enumerate(df.columns, 1):
            w.column_dimensions[get_column_letter(i)].width = max(14, min(30, len(str(col)) + 4))
        w.freeze_panes = "A2"

    sheet("CASH", cash["df"].rename(columns={"ClientID": "CLIENT CODE"}), ["DayBuyAmount", "DaySellAmount"])
    for tag, blk in (("FUTURE", fut), ("MCX", mcx)):
        sheet(f"{tag}_MTM", blk["mtm"].rename(columns={"ClientID": "CLIENT CODE"}), ["MToM"])
        sheet(f"{tag}_PREMIUM", blk["prem"].rename(columns={"ClientID": "CLIENT CODE"}),
              ["DayBuyAmount", "DaySellAmount", "NetPremium", "exrcassgnedval", "Premium"])
        sheet(f"{tag}_EXERCISE", blk["exercise"], ["exrcassgnedval"])
    sp = (pd.DataFrame({"SYMBOL": list(settle), "SETTLEMENT PRICE": list(settle.values())}) if settle
          else pd.DataFrame(columns=["SYMBOL", "SETTLEMENT PRICE"]))
    sheet("SETTLEMENT", sp)
    sheet("CHECKS", pd.DataFrame(checks, columns=["Check", "Value"]))
    wb.save(path)


def write_dashboard(path, title, narr_rows, stamp):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    with _PLT_LOCK:
        _write_dashboard(plt, FancyBboxPatch, path, title, narr_rows, stamp)


def _write_dashboard(plt, FancyBboxPatch, path, title, narr_rows, stamp):
    heads = ["Narration", "Total", "Buying", "Selling", "Future/MCX MTM", "Premium"]
    col_w = [3.3, 2.3, 1.9, 1.9, 2.1, 2.2]
    W = 11.5
    H = 3.45 + 0.95 * max(1, len(narr_rows))          # picture height follows the number of rows (3 rows = 6.3 as before)
    fig = plt.figure(figsize=(W, H), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    fig.patch.set_facecolor("#F4F6FA")
    NAVY, LINE, INK, MUTE = "#14284B", "#E3E8F0", "#1B2230", "#8A94A6"
    RED, GREEN = "#C62828", "#1B7F3B"
    ACCENT = ["#3B6FD4", "#1C9C8C", "#E09A1A"]

    ax.add_patch(plt.Rectangle((0, H - 1.3), W, 1.3, fc=NAVY, ec="none"))
    ax.text(0.5, H - 0.55, title, fontsize=25, fontweight="bold", color="white", va="center")
    ax.text(0.5, H - 0.98, SUBTITLE, fontsize=13, color="#AFC0E0", va="center")
    ax.text(W - 0.5, H - 0.55, stamp, fontsize=13, color="white", va="center", ha="right")
    ax.text(W - 0.5, H - 0.98, "All figures in \u20b9", fontsize=13, color="#AFC0E0", va="center", ha="right")

    x0, x1 = 0.4, W - 0.4
    scale = (x1 - x0) / sum(col_w)
    xs = [x0]
    for w in col_w:
        xs.append(xs[-1] + w * scale)
    top = H - 1.3 - 0.4
    hh, rh = 0.65, 0.95
    card_h = hh + rh * len(narr_rows)
    ax.add_patch(FancyBboxPatch((x0, top - card_h), x1 - x0, card_h, boxstyle="round,pad=0,rounding_size=0.12",
                                fc="white", ec=LINE, lw=1.2))
    ax.add_patch(plt.Rectangle((x0 + 0.01, top - hh), x1 - x0 - 0.02, hh - 0.01, fc="#EAEFF8", ec="none"))
    for i, h in enumerate(heads):
        if i == 0:
            ax.text(xs[0] + 0.4, top - hh / 2, h.upper(), color=NAVY, fontsize=12, fontweight="bold", va="center", ha="left")
        else:
            ax.text((xs[i] + xs[i + 1]) / 2, top - hh / 2, h.upper(), color=NAVY, fontsize=12,
                    fontweight="bold", va="center", ha="center")

    for r, row in enumerate(narr_rows):
        y1 = top - hh - r * rh
        y0 = y1 - rh
        yc = (y0 + y1) / 2
        if r < len(narr_rows) - 1:
            ax.plot([x0 + 0.3, x1 - 0.3], [y0, y0], color=LINE, lw=1)
        ax.add_patch(plt.Rectangle((x0 + 0.01, y0 + 0.2), 0.09, rh - 0.4, fc=ACCENT[r % 3], ec="none"))
        ax.add_patch(plt.Rectangle((xs[1] + 0.1, y0 + 0.12), xs[2] - xs[1] - 0.2, rh - 0.24, fc="#F5F7FB", ec="none"))
        ax.text(xs[0] + 0.4, yc, row[0], fontsize=14.5, fontweight="bold", color=INK, va="center", ha="left")
        is_cash_row = str(row[0]).strip().upper().startswith("CASH")
        for i in range(1, 6):
            v = row[i]
            if is_cash_row and i == 2:                  # Buying -> always RED, sign dropped
                disp, colr = indian(abs(v)), RED
            elif is_cash_row and i == 3:                # Selling -> always GREEN, sign dropped
                disp, colr = indian(abs(v)), GREEN
            elif is_cash_row and i == 1:                # Total -> magnitude only; selling > buying GREEN, buying > selling RED
                buying, selling = row[2], row[3]
                colr = GREEN if abs(selling) > abs(buying) else (RED if abs(buying) > abs(selling) else MUTE)
                disp = indian(abs(v))
            else:
                colr = RED if v < -0.5 else (GREEN if v > 0.5 else MUTE)
                disp = indian(v)
            ax.text((xs[i] + xs[i + 1]) / 2, yc, disp, fontsize=14.5 if i == 1 else 14,
                    fontweight="bold" if i == 1 else "normal", color=colr, va="center", ha="center")

    ax.add_patch(plt.Rectangle((0, 0), W, 0.65, fc="#E9EDF4", ec="none"))
    ax.text(0.5, 0.325, "Cash  |  Futures  |  MCX  |  Daily Pay-In / Pay-Out", fontsize=12, color="#5B6577", va="center")
    ax.text(W - 0.5, 0.325, "Master Capital Services Ltd", fontsize=12, color="#5B6577", va="center", ha="right",
            fontweight="bold")
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)


def build_email_html(cids):
    """cids = [(cid, title, heading, note)] in the order they appear."""
    blocks = ""
    for cid, title, heading, note in cids:
        blocks += f"""
    <tr><td style="padding:16px 28px 4px 28px;font-size:15px;font-weight:bold;color:#14284B;">{heading}</td></tr>
    <tr><td style="padding:6px 28px 6px 28px;">
        <img src="cid:{cid}" alt="{title}" width="860"
             style="display:block;width:860px;max-width:100%;height:auto;border:1px solid #E3E8F0;border-radius:6px;">
    </td></tr>
    <tr><td style="padding:0 28px 10px 28px;font-size:13px;line-height:1.5;color:#3B4A63;"><b>NOTE:</b> {note}</td></tr>"""
    return f"""\
<html><body style="margin:0;padding:0;background:#EEF1F6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#EEF1F6;padding:24px 0;">
<tr><td align="center">
  <table role="presentation" width="920" cellpadding="0" cellspacing="0"
         style="max-width:920px;width:100%;background:#FFFFFF;border-radius:10px;overflow:hidden;
                font-family:'Segoe UI',Calibri,Arial,sans-serif;color:#1B2230;">
    <tr><td style="padding:26px 28px 6px 28px;font-size:15px;line-height:1.6;">
        Dear Sir,<br>
        Please find the Tentative Obligation File Summary below:-
    </td></tr>{blocks}
    <tr><td style="padding:6px 28px 14px 28px;font-size:13px;line-height:1.5;color:#3B4A63;">
        The Day Net Premium includes FO Day Net Premium and MCX Day Net Premium.<br>
        The attached Excel files are password protected.
    </td></tr>
    <tr><td style="padding:0 28px;"><div style="border-top:1px solid #E3E8F0;"></div></td></tr>
    <tr><td style="padding:18px 28px 26px 28px;font-size:14px;line-height:1.65;">
        Best regards,<br>
        Sandeep Kumar<br>
        011-42111079
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


def all_zero(narr_rows):
    """True when every figure shown on the dashboard is zero."""
    return all(round(float(v)) == 0 for row in narr_rows for v in row[1:])




# ───────────────────────────── reading the two input files ─────────────────────────────
def _canon_cols(df, wanted):
    low = {w.lower(): w for w in wanted}
    df.columns = [low.get(str(c).strip().lower(), str(c).strip()) for c in df.columns]
    return df


def _read_raw(path, nrows=None):
    """Position book as text: .xlsx/.xls (first sheet) or the tab separated csv (any separator is tried when it is not tabs)."""
    path = str(path)
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path, dtype=str, keep_default_na=False, nrows=nrows)
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, nrows=nrows)
    if df.shape[1] < 5:
        df = pd.read_csv(path, sep=None, engine="python", dtype=str, keep_default_na=False, nrows=nrows)
    return df


def read_der(path):
    df = _canon_cols(_read_raw(path), REQ_COLS)
    miss = [c for c in REQ_COLS if c not in df.columns]
    if miss:
        raise ValueError("the PositionBook has no column " + ", ".join(miss))
    for c in ("Exch", "ClientID", "OpType", "Ind Moneyness", "Symbol"):
        df[c] = df[c].astype(str).str.strip()
    df["Exch"] = df["Exch"].str.upper()
    df["OpType"] = df["OpType"].str.upper()
    df["Ind Moneyness"] = df["Ind Moneyness"].str.upper()
    for c in ("MToM", "DayBuyAmount", "DaySellAmount", "StrikePrice", "NetQty"):
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "").str.strip(), errors="coerce").fillna(0.0)
    df["ExpDate_dt"] = parse_dates(df["ExpDate"])
    return df


def read_risk(path):
    r = pd.read_csv(path, dtype=str, keep_default_na=False,
                    usecols=lambda c: str(c).strip().lower() in ("accountid", "cpcode"))
    r = _canon_cols(r, ["ACCOUNTID", "CPCode"])
    if "ACCOUNTID" not in r.columns or "CPCode" not in r.columns:
        raise ValueError("the risk report has no ACCOUNTID / CPCode column")
    r["ACCOUNTID"] = r["ACCOUNTID"].str.strip()
    r["CPCode"] = r["CPCode"].str.strip()
    r = r[r["ACCOUNTID"] != ""]
    dup = r["ACCOUNTID"].duplicated(keep=False)
    if dup.any():
        log(f"NOTE: {r.loc[dup, 'ACCOUNTID'].nunique()} ACCOUNTID(s) repeated in risk report - first row used")
    return r.drop_duplicates("ACCOUNTID", keep="first")


def add_cpcode(der, risk):
    m = der.merge(risk, how="left", left_on="ClientID", right_on="ACCOUNTID")
    missing = m["CPCode"].isna().sum()
    if missing:
        log(f"NOTE: {missing} DER rows have no match in risk report (CPCode treated as blank, kept)")
    m["CPCode"] = m["CPCode"].fillna("")
    return m


# ───────────────────────────── file rules (name date / saved time) ─────────────────────────────
_D8 = re.compile(r"(?<!\d)(\d{2})[-_.]?(\d{2})[-_.]?(\d{4})(?!\d)")        # ddmmyyyy, dd-mm-yyyy ...
_Y8 = re.compile(r"(?<!\d)(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")        # yyyymmdd, yyyy-mm-dd ...


def name_date(name: str):
    """The date written in a file name (17112026, 17-11-2026, 20261117 ...) or None when there is none."""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    for mt in _D8.finditer(stem):
        try:
            return dt.date(int(mt.group(3)), int(mt.group(2)), int(mt.group(1)))
        except ValueError:
            continue
    for mt in _Y8.finditer(stem):
        try:
            if 2000 <= int(mt.group(1)) <= 2100:
                return dt.date(int(mt.group(1)), int(mt.group(2)), int(mt.group(3)))
        except ValueError:
            continue
    return None


def freshness_problem(name: str, modified_ms=None, now: dt.datetime = None) -> str:
    """'' when the file may be taken: today's date in its name, or - with no date in the name - saved within FRESH_MIN minutes."""
    now = now or dt.datetime.now()
    d = name_date(name)
    if d is not None:
        return "" if d == now.date() else f"it is dated {dstr(d)} - only today's ({dstr(now.date())}) PositionBook is accepted"
    try:
        t = dt.datetime.fromtimestamp(float(modified_ms) / 1000.0)
    except (TypeError, ValueError, OverflowError, OSError):
        return "there is no date in the name and its saved time could not be read - put today's date in the name (e.g. Positionbook_" + now.strftime("%d%m%Y") + ".csv)"
    age = (now - t).total_seconds() / 60.0
    if age > FRESH_MIN:
        return f"there is no date in the name and the file was saved {int(age)} min ago (older than {FRESH_MIN} min) - export it again or put today's date in the name"
    if age < -5:
        return "its saved time is in the future - check the computer's clock"
    return ""


# ───────────────────────────── inputs ─────────────────────────────
def position_path(base: Path = None) -> Path:
    """The PositionBook in the card's folder: the newest Positionbook*.csv/.xlsx/.xls (a file put there by hand counts too)."""
    base = Path(base or root())
    best = None
    if base.is_dir():
        for p in base.iterdir():
            if (p.is_file() and p.name.lower().startswith("positionbook") and not p.name.startswith("~$")
                    and p.suffix.lower() in (".csv", ".xlsx", ".xls")):
                if best is None or p.stat().st_mtime > best.stat().st_mtime:
                    best = p
    return best or base / f"Positionbook_{dstr(_today()).replace('-', '')}.csv"


def risk_path(today: dt.date = None):
    """Today's RISK_REPORT_<ddmmyyyy>.csv from COMMON FILES (or its dated backup there). Read-only - never moved."""
    from core.scheduler import helpers as H
    return H.find_risk_report(None)


def input_status(base: Path = None, today: dt.date = None, now: dt.datetime = None) -> list:
    today = today or _today()
    now = now or dt.datetime.now()
    p = position_path(base)
    found = p.is_file()
    row = {"key": "POSITIONBOOK", "label": "PositionBook (upload)", "name": p.name, "found": found, "ok": False, "why": "",
           "size": 0, "time": "", "where": "", "stale": False}
    if found:
        st = p.stat()
        saved = dt.datetime.fromtimestamp(st.st_mtime)
        row.update(size=st.st_size, time=saved.strftime("%H:%M:%S"), where=str(p.parent))
        if st.st_size == 0:
            row["why"] = "empty"
        else:
            nd = name_date(p.name)
            if nd is not None:
                row["ok"], row["stale"] = nd == today, nd != today
                row["why"] = "" if nd == today else f"dated {dstr(nd)} - not today"
            else:
                age = (now - saved).total_seconds() / 60.0
                row["ok"] = age <= FRESH_MIN
                row["stale"] = not row["ok"]
                row["why"] = "" if row["ok"] else f"no date in the name and saved {int(age)} min ago (older than {FRESH_MIN} min)"
    else:
        row["why"] = "not uploaded"
    rk = {"key": "RISKREPORT", "label": "Risk report (COMMON FILES)", "name": f"RISK_REPORT_{today:%d%m%Y}.csv", "found": False,
          "ok": False, "why": "", "size": 0, "time": "", "where": str(common_dir()), "stale": False}
    try:
        rp = Path(risk_path(today))
        s2 = rp.stat()
        rk.update(name=rp.name, found=True, ok=s2.st_size > 0, size=s2.st_size, where=str(rp.parent),
                  time=dt.datetime.fromtimestamp(s2.st_mtime).strftime("%H:%M:%S"), why="" if s2.st_size > 0 else "empty")
    except Exception as e:                                       # noqa: BLE001
        rk["why"] = "not in COMMON FILES yet" if isinstance(e, FileNotFoundError) else f"{type(e).__name__}: {e}"
    return [row, rk]


def pre_run_check(base: Path = None) -> dict:
    rows = input_status(base)
    bad = [r for r in rows if not r["ok"]]
    if bad:
        raise PreRunError("Missing / not ready: " + "; ".join(f"{r['label']}" + (f" ({r['why']})" if r["why"] else "") for r in bad))
    return {"inputs": rows}


# ───────────────────────────── settlement prices ─────────────────────────────
def _settle_path() -> Path:
    return _state_dir() / "settle.json"


def stored_settlement() -> dict:
    """{'prices': {SYMBOL_UPPER: price}, 'source': '...', 'at': '...'} saved for TODAY, else empty."""
    try:
        d = json.loads(_settle_path().read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        d = {}
    if d.get("date") != _today().isoformat():
        return {"prices": {}, "source": "", "at": ""}
    return {"prices": {str(k).upper(): float(v) for k, v in (d.get("prices") or {}).items()},
            "source": d.get("source", ""), "at": d.get("at", "")}


_NEED_CACHE: dict = {}


def need_symbols(today: dt.date = None) -> list:
    """The symbols that need a settlement price today: ITM option contracts expiring today (NFO / BFO / MCX).
    [{symbol, exch, rows, strikes, optional (MCX only: blank = skip), price (already stored or None)}]"""
    today = today or _today()
    p = position_path()
    if not p.is_file():
        raise PreRunError("Upload today's PositionBook first.")
    key = (str(p), p.stat().st_mtime, p.stat().st_size, today.isoformat())
    if _NEED_CACHE.get("k") != key:
        _NEED_CACHE.update(k=key, v=itm_symbols(read_der(p), today))
    stored = stored_settlement()["prices"]
    out = []
    for sym, m in sorted(_NEED_CACHE["v"].items()):
        out.append({"symbol": sym, "exch": m["exch"], "rows": int(m["rows"]), "strikes": m["strikes"],
                    "optional": bool(m["optional"]), "price": stored.get(sym.upper())})
    return out


def save_settlement(prices: dict, source: str = "typed in the popup", token: str = "", by: str = "") -> dict:
    """Keep today's settlement prices ({symbol: price}). A file chosen through the mapping window is also copied to the card folder
    (Settlement_<ddmmyyyy>.<ext>) so it goes to the backup with the rest."""
    clean = {}
    for k, v in (prices or {}).items():
        sym = str(k).strip().upper()
        if not sym:
            continue
        try:
            val = float(str(v).replace(",", "").strip())
        except ValueError:
            raise ValueError(f"the price of {k} is not a number")
        if val <= 0:
            raise ValueError(f"the price of {k} must be more than 0")
        clean[sym] = val
    with _STATE_LOCK:
        _settle_path().write_text(json.dumps({"date": _today().isoformat(), "prices": clean, "source": source, "by": by, "at": _iso()}, indent=1),
                                  encoding="utf-8")
    if token:
        src = _settle_file(token)
        if src:
            dest = root() / f"Settlement_{_today():%d%m%Y}{src.suffix.lower()}"
            try:
                shutil.copy2(src, dest)
            except OSError as e:
                log(f"settlement file not copied: {e}")
    _event(f"Settlement prices saved ({len(clean)} symbol(s), {source}) by {by or 'user'}: " + ", ".join(f"{k}={v:g}" for k, v in clean.items()))
    return {"ok": True, "count": len(clean), "source": source}


# --- any file -> SYMBOL / PRICE columns chosen by the user ---
def _settle_dir() -> Path:
    p = _state_dir() / "settle_files"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _settle_file(token: str):
    if not re.fullmatch(r"[0-9a-f]{12}", token or ""):
        return None
    for p in _settle_dir().glob(token + ".*"):
        return p
    return None


def _sweep_settle_files(max_age_s: int = 3 * 86400):
    try:
        for p in _settle_dir().iterdir():
            if time.time() - p.stat().st_mtime > max_age_s:
                p.unlink()
    except OSError:
        pass


def _read_table(path: Path, sheet=None) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path, sheet_name=(sheet if sheet not in (None, "") else 0), header=None, dtype=str, keep_default_na=False)
    else:
        df = pd.read_csv(path, header=None, dtype=str, sep=None, engine="python", encoding_errors="replace", keep_default_na=False)
    df = df.fillna("")
    keep = [i for i in range(len(df)) if any(str(x).strip() for x in df.iloc[i])]            # drop the fully blank rows, keep their order
    return df.iloc[keep].reset_index(drop=True)


def _num(x):
    try:
        v = float(str(x).replace(",", "").strip())
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _header_names(df, header_row: int) -> list:
    row = df.iloc[header_row - 1]
    names, seen = [], {}
    for i, x in enumerate(row):
        n = str(x).strip() or f"Column {i + 1}"
        if n in seen:
            seen[n] += 1
            n = f"{n} ({seen[n]})"
        else:
            seen[n] = 1
        names.append(n)
    return names


def _guess_cols(names: list, last: dict) -> dict:
    sc = pc = ""
    if last.get("symbol_col") in names and last.get("price_col") in names:
        return {"symbol_col": last["symbol_col"], "price_col": last["price_col"], "remembered": True}
    low = [n.lower() for n in names]
    for i, n in enumerate(low):
        if any(k in n for k in ("symbol", "name", "underlying", "scrip", "instrument", "commodity", "index")):
            sc = names[i]
            break
    for i, n in enumerate(low):
        if names[i] != sc and any(k in n for k in ("price", "settle", "close", "value", "rate", "ltp")):
            pc = names[i]
            break
    return {"symbol_col": sc, "price_col": pc, "remembered": False}


def inspect_settle_file(name: str = "", data: bytes = None, token: str = "", sheet=None, header_row=None, by: str = "") -> dict:
    """A new file (name + data) or an earlier one (token): its sheets, the column names of the header row, a few sample rows and a guess
    of the SYMBOL / PRICE columns (the last choice is remembered)."""
    _sweep_settle_files()
    if data is not None:
        name = os.path.basename(name or "")
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".csv", ".xlsx", ".xlsm", ".xls", ".txt"):
            raise ValueError(f"{name or 'the file'}: use a csv or Excel file")
        if not data:
            raise ValueError(f"{name}: the file is empty")
        token = uuid.uuid4().hex[:12]
        (_settle_dir() / (token + ext)).write_bytes(data)
        _event(f"Settlement file picked: {name} ({len(data):,} bytes) by {by or 'user'}")
    path = _settle_file(token)
    if path is None:
        raise ValueError("that settlement file is no longer there - pick it again")
    sheets = []
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        try:
            sheets = list(pd.ExcelFile(path).sheet_names)
        except Exception as e:                                   # noqa: BLE001
            raise ValueError(f"cannot read the Excel file ({type(e).__name__})")
        if sheet in (None, "") or sheet not in sheets:
            sheet = sheets[0] if sheets else None
    try:
        df = _read_table(path, sheet)
    except Exception as e:                                       # noqa: BLE001
        raise ValueError(f"cannot read the file ({type(e).__name__}: {str(e)[:120]})")
    if df.empty or df.shape[1] < 2:
        raise ValueError("the file needs at least 2 columns (a symbol and a price)")
    hr = int(header_row or 1)
    hr = max(1, min(hr, len(df)))
    names = _header_names(df, hr)
    sample = [[str(x) for x in df.iloc[i]] for i in range(hr, min(len(df), hr + 6))]
    return {"ok": True, "token": token, "name": name or path.name, "sheets": sheets, "sheet": sheet, "header_row": hr,
            "columns": names, "sample": sample, "rows": max(0, len(df) - hr),
            "guess": _guess_cols(names, get_settings().get("settle_map") or {})}


def apply_settle_file(token: str, symbol_col: str, price_col: str, sheet=None, header_row=1, by: str = "") -> dict:
    """Read {SYMBOL: price} from the chosen columns. Nothing is saved yet - the popup shows the values and the user submits them."""
    info = inspect_settle_file(token=token, sheet=sheet, header_row=header_row)
    names = info["columns"]
    if symbol_col not in names or price_col not in names:
        raise ValueError("choose the SYMBOL column and the PRICE column")
    if symbol_col == price_col:
        raise ValueError("the SYMBOL column and the PRICE column must be different")
    df = _read_table(_settle_file(token), info["sheet"])
    si, pi = names.index(symbol_col), names.index(price_col)
    prices, bad, dup = {}, 0, 0
    for i in range(info["header_row"], len(df)):
        sym = str(df.iloc[i, si]).strip().upper()
        if not sym or sym == "NAN":
            continue
        val = _num(df.iloc[i, pi])
        if val is None or val <= 0:
            bad += 1
            continue
        if sym in prices:
            dup += 1
        prices[sym] = val
    if not prices:
        raise ValueError(f"no usable rows - '{symbol_col}' / '{price_col}' gave no symbol with a number above 0")
    cur = get_settings()
    cur["settle_map"] = {"symbol_col": symbol_col, "price_col": price_col}
    _write_settings(cur)
    try:
        need = {n["symbol"].upper() for n in need_symbols()}
    except PreRunError:
        need = set()
    _event(f"Settlement file mapped: SYMBOL = '{symbol_col}', PRICE = '{price_col}' -> {len(prices)} price(s), {bad} row(s) without a valid price, "
           f"{len(need & set(prices))} of {len(need)} needed symbol(s) found (by {by or 'user'})")
    return {"ok": True, "prices": prices, "count": len(prices), "bad_rows": bad, "duplicates": dup,
            "matched": sorted(need & set(prices)), "missing": sorted(need - set(prices)), "token": token, "name": info["name"]}


# ───────────────────────────── the reports ─────────────────────────────
def step_calc(base: Path, settle_prices: dict, today: dt.date = None) -> dict:
    """The two reports (normal, custodian): Excel + picture each. Returns the list of what was made."""
    today = today or _today()
    base = Path(base)
    der_path, rk_path = position_path(base), Path(risk_path(today))
    der = add_cpcode(read_der(der_path), read_risk(rk_path))
    log(f"DER rows {len(der)} | Exch counts: {der['Exch'].value_counts().to_dict()}")
    fo_exp = int((der["Exch"].isin(FO_EXCH + MCX_EXCH) & expiry_today(der, today)).sum())
    log(f"Rows with expiry = today in NFO/BFO/MCX: {fo_exp}")
    need = itm_symbols(der, today)
    settle = {s: settle_prices[s.upper()] for s in need if s.upper() in settle_prices}
    missing = [s for s in need if s.upper() not in settle_prices and not need[s]["optional"]]
    if missing:
        raise PreRunError("Settlement prices are missing for: " + ", ".join(missing))
    log(f"Settlement prices: {settle}" if need else "No ITM contract expiring today -> exercise value = 0")
    stamp = dt.datetime.now().strftime("%d-%b-%Y %H:%M")
    tag = dt.datetime.now().strftime("%Y%m%d_%H%M")
    built = []
    for spec in (NORMAL, CUSTODIAN):
        _check_cancel()
        rep = build_report(der, today, settle, spec)
        checks = [["Run date", f"{today:%d-%b-%Y}"], ["DER file", str(der_path)], ["Risk report", str(rk_path)],
                  ["Rows with expiry today (NFO/BFO/MCX)", fo_exp], ["FUTURE info", str(rep["fut"]["info"])],
                  ["MCX info", str(rep["mcx"]["info"])], ["Settlement prices", str(settle)], ["CPCode filters", rep["rule"]]]
        xlsx = base / f"{spec['file_prefix']}_{tag}.xlsx"
        png = base / f"{spec['file_prefix']}_{tag}.png"
        write_excel(str(xlsx), rep, settle, checks)
        write_dashboard(str(png), spec["title"], rep["narr"], stamp)
        for r in rep["narr"]:
            log(f"[{spec['key']}] " + " | ".join([r[0]] + [indian(v) for v in r[1:]]))
        built.append({"spec": spec, "rep": rep, "xlsx": xlsx, "png": png, "zero": all_zero(rep["narr"])})
    return {"built": built, "date": today, "risk": rk_path, "der": der_path, "settle": settle}


def protect(path: Path, today: dt.date = None) -> Path:
    """<name>_<ddmmyyyy>.xlsx with the open-password Scenario@<ddmmyyyy>; the unprotected file is removed. Stops (never sends it open) if it cannot."""
    from core.enterprise_risk_calc import protect_xlsx
    today = today or _today()
    dated = path.with_name(f"{path.stem}_{today:%d%m%Y}{path.suffix}")
    shutil.copy2(path, dated)
    try:
        protect_xlsx(dated, f"Scenario@{today:%d%m%Y}")
    except Exception as e:                                       # noqa: BLE001
        try:
            dated.unlink()
        except OSError:
            pass
        raise RuntimeError(f"could not password-protect {path.name}: {e}. Nothing was e-mailed.")
    path.unlink()
    log(f"Password applied -> {dated.name} (password Scenario@<ddmmyyyy>)")
    return dated


# ───────────────────────────── mail ─────────────────────────────
def recipients(key: str = "REPORT"):
    from core.mcx_cash_report import split_addrs
    tk, ck = mail_keys(key)
    d_to, d_cc = MAIL_DEFAULTS[key]
    return split_addrs(os.environ.get(tk, d_to)), split_addrs(os.environ.get(ck, d_cc))


def send_report(items, today: dt.date, to, cc, attempts=5, gap=10, sleep=time.sleep, smtp_factory=None):
    """items = [(spec, png, protected_xlsx)] -> ONE mail: the pictures in the body, the Excel files attached. Retries; raises when it never goes."""
    import smtplib
    from email import encoders
    from email.mime.base import MIMEBase
    from email.mime.image import MIMEImage
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from core import sender_override
    user, pwd = sender_override.sender(PIPE)
    host, port = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in"), int(os.getenv("SMTP_PORT", "587") or 587)
    if not to:
        raise RuntimeError("no 'To' address set - use the Email To / CC button")
    if not (user and pwd):
        raise RuntimeError("the sender login is not set (Credentials > E-mail sender, or SENDER_EMAIL / SENDER_PASSWORD in .env)")
    notes = {"normal": "Custodian Accounts have been removed from this report.",
             "custodian": "This report contains Custodian Accounts only."}
    cids = [(f"dash_{s['key']}", s["title"], s["heading"], notes[s["key"]]) for s, _p, _x in items]
    last = None
    for n in range(1, attempts + 1):
        try:
            _check_cancel()
            msg = MIMEMultipart("mixed")
            msg["From"], msg["To"] = user, ", ".join(to)
            if cc:
                msg["Cc"] = ", ".join(cc)
            msg["Subject"] = f"{MAIL_SUBJECT_PREFIX}-{today:%d%m%Y}"
            alt = MIMEMultipart("alternative")
            msg.attach(alt)
            alt.attach(MIMEText("Dear Sir,\nPlease find the Tentative Obligation File Summary below:-\n\n"
                                "(View this email in HTML to see the summary.)\n\nBest regards,\nSandeep Kumar\n011-42111079", "plain", "utf-8"))
            related = MIMEMultipart("related")
            alt.attach(related)
            related.attach(MIMEText(build_email_html(cids), "html", "utf-8"))
            for (s, png, _x), (cid, *_r) in zip(items, cids):
                img = MIMEImage(Path(png).read_bytes(), _subtype="png")
                img.add_header("Content-ID", f"<{cid}>")
                img.add_header("Content-Disposition", "inline", filename=Path(png).name)
                related.attach(img)
            for _s, _p, xl in items:
                part = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                part.set_payload(Path(xl).read_bytes())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment", filename=Path(xl).name)
                msg.attach(part)
            srv = (smtp_factory or smtplib.SMTP)(host, port, timeout=60)
            try:
                srv.starttls()
                srv.login(user, pwd)
                srv.send_message(msg, to_addrs=list(to) + list(cc))
            finally:
                try:
                    srv.quit()
                except Exception:                                # noqa: BLE001
                    pass
            log(f"E-mail sent: {msg['Subject']} ({len(items)} report(s)) -> {', '.join(to)}" + (f" (cc {', '.join(cc)})" if cc else ""))
            return
        except Cancelled:
            raise
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(f"the mail login was refused: {e}")
        except Exception as e:                                   # noqa: BLE001
            last = e
            log(f"E-mail failed (try {n}/{attempts}): {type(e).__name__}: {e}")
            if n < attempts:
                sleep(gap)
    raise RuntimeError(f"e-mail did not go: {type(last).__name__}: {last}")


# ───────────────────────────── backup ─────────────────────────────
def backup_and_cleanup(base: Path = None, day: dt.date = None, risk: Path = None, extra=()) -> Path:
    """The day's PositionBook, settlement file, pictures and protected Excel files are moved to BACKUP FILES AUTOMATED\\<dd-mm-yyyy>;
    the risk report is COPIED there (it stays in COMMON FILES). Nothing else is touched."""
    base = Path(base or root())
    dest = backup_root() / dstr(day or _today())
    dest.mkdir(parents=True, exist_ok=True)
    mine = {Path(x).name for x in extra}
    for p in sorted(base.iterdir()):
        if not p.is_file() or p.name.startswith((".", "~$")) or p.suffix.lower() not in (".csv", ".xlsx", ".xls", ".png"):
            continue
        low = p.name.lower()
        if not (low.startswith("positionbook") or low.startswith(_SKIP_NAMES) or p.name in mine):
            continue
        target = dest / p.name
        if target.exists():
            target.unlink()
        shutil.move(str(p), str(target))
        log(f"moved to backup: {p.name}")
    if risk and Path(risk).is_file():
        shutil.copy2(risk, dest / Path(risk).name)
        log(f"copied to backup: {Path(risk).name} (stays in COMMON FILES)")
    return dest


# ───────────────────────────── failure alert ─────────────────────────────
def send_alert(headline, detail, sender=None) -> dict:
    """WhatsApp FAILURE ALERT picture (same as the other cards). Never raises."""
    rec = {"sent": False, "reason": ""}
    try:
        from core.scheduler import connections, failure_alert, helpers as H, paths
        raw = get_settings().get("alert_numbers") or ""
        c = connections.get("whatsapp")
        numbers = failure_alert.parse_numbers(raw) or failure_alert.parse_numbers(c.get("alert_numbers", ""))
        if not numbers:
            rec["reason"] = "no alert number set"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        if sender is not None:
            sender(numbers, headline, detail)
            rec.update(sent=True, reason="sent")
            return rec
        if not c.get("auth_token"):
            rec["reason"] = "WhatsApp auth token is not set"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        now = dt.datetime.now()
        img = failure_alert.render_image(paths.sched_root() / "alerts" / f"tpp_{int(time.time())}.png", process=NAME,
                                         schedule="Manual run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"), started="-",
                                         trigger="Manual run", attempt_text="final", headline=headline, detail=detail,
                                         run_id="TPP", kind="FAILED")

        class _C:
            def log(self, m):
                log(m)
        ok, bad = [], []
        for num in numbers:
            try:
                H.whatsapp_send_image(img, num, f"FAILURE ALERT - {NAME}", now.strftime("%d-%m-%Y %H:%M"), _C(),
                                      template_name=(c.get("alert_template") or "").strip() or None)
                ok.append(num[-4:])
            except Exception as e:                               # noqa: BLE001
                bad.append(f"{num[-4:]}: {type(e).__name__}: {str(e)[:100]}")
        rec.update(sent=bool(ok), reason=("sent to ..." + ", ...".join(ok)) if ok else "all sends failed: " + "; ".join(bad))
        log("WhatsApp failure alert " + rec["reason"])
    except Exception as e:                                       # noqa: BLE001
        rec["reason"] = f"{type(e).__name__}: {e}"
        log("WhatsApp failure alert error: " + rec["reason"])
    return rec


# ───────────────────────────── the run ─────────────────────────────
def is_running() -> bool:
    return _RUN_LOCK.locked()


def needs_password() -> bool:
    return read_state().get("status") == "done"


def run(trigger="manual", by="", alert=None, sleep=time.sleep, smtp_factory=None) -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy(f"{NAME} is already running.")
    alert = alert or send_alert
    base = root()
    _ACTIVE["cancel"] = False
    _ACTIVE["log_file"] = _open_run_log(trigger, by)
    _update(status="running", started=_iso(), finished="", error="", trigger=trigger, by=by, steps={}, sent=[], counts={},
            alert_sent=False, alert_reason="", made=[])
    result = {"ok": False}
    try:
        log(f"=== {NAME} started ({trigger}{', ' + by if by else ''}) ===")
        _set_step("input", "running")
        pre_run_check(base)
        rows = input_status(base)
        _set_step("input", "ok", f"{rows[0]['name']} · {rows[0]['size']:,} bytes · risk report {rows[1]['name']}")
        _check_cancel()
        _set_step("settle", "running")
        need = need_symbols()
        stored = stored_settlement()["prices"]
        missing = [n["symbol"] for n in need if not n["optional"] and n["symbol"].upper() not in stored]
        if missing:
            raise PreRunError("Settlement prices are needed for: " + ", ".join(missing) + " - press Run and enter them (or add them from a file).")
        _set_step("settle", "ok", (f"{len([n for n in need if n['symbol'].upper() in stored])} price(s) from {stored_settlement()['source'] or 'saved prices'}"
                                   if need else "no ITM contract expires today - none needed"))
        _check_cancel()
        _set_step("calc", "running")
        res = step_calc(base, stored)
        zero = [b for b in res["built"] if b["zero"]]
        live = [b for b in res["built"] if not b["zero"]]
        counts = {b["spec"]["key"]: len(b["rep"]["narr"]) for b in res["built"]}
        _update(counts=counts)
        _set_step("calc", "ok", ", ".join(f"{b['spec']['key']}: {'all zero' if b['zero'] else str(len(b['rep']['narr'])) + ' row(s)'}" for b in res["built"]))
        _check_cancel()
        items, made = [], []
        for b in live:
            prot = protect(b["xlsx"], res["date"])
            items.append((b["spec"], b["png"], prot))
            made += [b["png"], prot]
        for b in zero:
            log(f"[{b['spec']['key']}] every value is zero -> NOT added to the e-mail (image and Excel not attached)")
            made += [b["xlsx"], b["png"]]
        if not items:
            _set_step("email", "skipped", "all reports are zero - nothing sent")
            _set_step("backup", "skipped", "files left in the folder (nothing was sent)")
            _update(status="done", finished=_iso(), error="", made=[Path(x).name for x in made])
            log("All reports are zero - NOTHING SENT. Files are left in the folder (no backup / cleanup done).")
            return {"ok": True, "counts": counts, "sent": False}
        if get_settings().get("send_mail", True):
            _set_step("email", "running")
            to, cc = recipients("REPORT")
            send_report(items, res["date"], to, cc, sleep=sleep, smtp_factory=smtp_factory)
            _update(sent=["REPORT"])
            _set_step("email", "ok", "sent: " + ", ".join(s["key"] for s, _p, _x in items))
        else:
            _set_step("email", "skipped", "e-mail is switched off in Settings")
        _check_cancel()
        _set_step("backup", "running")
        dest = backup_and_cleanup(base, res["date"], res["risk"], extra=[Path(x) for x in made if Path(x).is_file()])
        _set_step("backup", "ok", str(dest))
        _update(status="done", finished=_iso(), error="", made=[Path(x).name for x in made])
        log(f"=== {NAME} finished ===")
        result = {"ok": True, "counts": counts, "sent": True}
    except Cancelled:
        _update(status="stopped", finished=_iso(), error="Stopped.")
        log("Stopped.")
        result = {"ok": False, "stopped": True}
    except Exception as e:                                       # noqa: BLE001
        msg = str(e) if isinstance(e, PreRunError) else f"{type(e).__name__}: {e}"
        _update(status="failed", finished=_iso(), error=msg[:600])
        log("FAILED: " + msg)
        rec = alert(f"The {NAME} run failed.", msg[:600])
        _update(alert_sent=bool(rec.get("sent")), alert_reason=rec.get("reason", ""))
        result = {"ok": False, "error": msg}
    finally:
        f = _ACTIVE.get("log_file")
        _ACTIVE["log_file"] = None
        if f:
            try:
                f.close()
            except Exception:                                    # noqa: BLE001
                pass
        _RUN_LOCK.release()
    return result


def request_run(by="", password=None, background=True) -> dict:
    if is_running():
        raise Busy(f"{NAME} is already running.")
    if needs_password():
        from core import short_allocation
        if not password or not short_allocation.check_password(password):
            return {"ok": False, "need_password": True, "msg": "Today's run already succeeded - enter the password to run it again."}
    if background:
        threading.Thread(target=lambda: run("manual", by), name="tpp-manual", daemon=True).start()
        return {"ok": True, "started": True}
    return run("manual", by)


def any_fetching(exclude: str = "") -> bool:
    return False                                                 # this card downloads nothing


def fetch_one(what: str, by="") -> dict:
    raise ValueError("this card has no downloads - upload the PositionBook")


def stop_running(by="") -> bool:
    if not is_running():
        return False
    _ACTIVE["cancel"] = True
    log(f"Stop requested by {by or 'user'}.")
    return True


# ───────────────────────────── files / backup ─────────────────────────────
def _bk_day(name) -> Path:
    from core import backup_archive
    return backup_archive.day_dir(backup_root(), str(name))


def list_files(sub: str = "") -> list:
    base = root() if not sub else _bk_day(sub)
    out = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".part"):
                out.append({"name": p.name, "size": p.stat().st_size,
                            "time": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S")})
    return out


def list_backup_dates() -> list:
    from core import backup_archive
    b = backup_root()
    if not b.is_dir():
        return []
    names = [p.name for p in b.iterdir() if p.is_dir() and re.fullmatch(r"\d{2}-\d{2}-\d{4}", p.name)]
    names += [n for n in backup_archive.archived_days(b) if re.fullmatch(r"\d{2}-\d{2}-\d{4}", n)]
    return sorted(set(names), key=lambda s: dt.datetime.strptime(s, "%d-%m-%Y"), reverse=True)


def file_path(name: str, sub: str = "") -> Path:
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("bad file name")
    p = (root() if not sub else _bk_day(_check_date(sub))) / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return p


def dates_between(a: dt.date, b: dt.date) -> list:
    out, d = [], a
    while d <= b:
        out.append(d)
        d += dt.timedelta(days=1)
    return out


def range_listing(a: dt.date, b: dt.date) -> list:
    rows = []
    for d in dates_between(a, b):
        for f in list_files(dstr(d)):
            rows.append(dict(f, date=dstr(d)))
    return rows


def zip_range(a: dt.date, b: dt.date) -> Path:
    import tempfile
    z = Path(tempfile.gettempdir()) / f"TENTATIVE_PAYIN_PAYOUT_{a:%d%m%Y}_{b:%d%m%Y}_{int(time.time())}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dates_between(a, b):
            for f in list_files(dstr(d)):
                zf.write(_bk_day(dstr(d)) / f["name"], f"{dstr(d)}/{f['name']}")
    return z


# ───────────────────────────── upload ─────────────────────────────
def _event(msg: str):
    """An event: shown in the live log AND kept in logs/tpp/tpp__<dd-mm-yyyy>__uploads.log (the Logs button)."""
    log(msg)
    try:
        with open(log_dir() / f"tpp__{dstr(_today())}__uploads.log", "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%d-%m-%Y %H:%M:%S}] {msg}\n")
    except Exception:                                            # noqa: BLE001
        pass


def _refuse(name, why, by=""):
    _event(f"Upload REFUSED: {name} - {why} (by {by or 'user'})")
    raise ValueError(f"{name}: {why}")


def accept_upload(name: str, data: bytes, by="", modified_ms=None, now: dt.datetime = None) -> dict:
    """The upload button: today's PositionBook only (today's date in the name, or - with no date in the name - saved within FRESH_MIN minutes).
    It is kept as Positionbook_<today ddmmyyyy>.<csv|xlsx|xls>. Every upload - taken or refused - is written to the card's log."""
    name = os.path.basename(name or "")
    ext = os.path.splitext(name)[1].lower()
    if not name or name.startswith(".") or ext in (".py", ".bat", ".exe", ".part"):
        _refuse(name or "(no name)", "this file name is not allowed", by)
    if not data:
        _refuse(name, "the file is empty", by)
    if not re.match(r"^position\s*book", name, re.I) or ext not in (".csv", ".xlsx", ".xls"):
        _refuse(name, "only the PositionBook is used here (a name like Positionbook_<ddmmyyyy>.csv or .xlsx)", by)
    why = freshness_problem(name, modified_ms, now)
    if why:
        _refuse(name, why, by)
    part = root() / ("Positionbook_upload" + ext + ".part")
    part.write_bytes(data)
    try:
        head = _canon_cols(_read_head(part, ext), REQ_COLS)
    except Exception as e:                                       # noqa: BLE001
        part.unlink(missing_ok=True)
        _refuse(name, f"cannot be read ({type(e).__name__})", by)
    miss = [c for c in REQ_COLS if c not in head.columns]
    if miss:
        part.unlink(missing_ok=True)
        _refuse(name, f"columns missing - {', '.join(miss)} (is this the derivative PositionBook?)", by)
    d = name_date(name)
    day = dstr(_today()).replace("-", "")
    target = root() / f"Positionbook_{day}{ext}"
    for old in list(root().iterdir()):                           # the earlier PositionBook of the day is replaced
        if old.is_file() and old.name.lower().startswith("positionbook") and old != target and old.suffix.lower() in (".csv", ".xlsx", ".xls"):
            old.unlink()
    part.replace(target)
    _NEED_CACHE.clear()
    _event(f"Upload ACCEPTED: {name} as {target.name} ({len(data):,} bytes) by {by or 'user'} - "
           + (f"dated {dstr(d)} (today)" if d else f"no date in the name, saved within {FRESH_MIN} min"))
    return {"ok": True, "name": target.name, "size": len(data)}


def _read_head(part: Path, ext: str):
    """The first rows of an uploaded (still .part) file - read through a temporary copy that has the real extension."""
    tmp = part.with_name("_head_check" + ext)
    shutil.copyfile(part, tmp)
    try:
        return _read_raw(tmp, nrows=3)
    finally:
        tmp.unlink(missing_ok=True)


def status() -> dict:
    st = read_state()
    running = is_running()
    s = st.get("status", "idle")
    if s == "running" and not running:
        s = "failed"
        st["error"] = st.get("error") or "Interrupted (the server restarted while it was running)."
    set_ = get_settings()
    inputs = input_status()
    steps = {k: dict(v) for k, v in (st.get("steps") or {}).items()}
    for v in steps.values():                                     # a step still marked running while nothing runs = the server restarted
        if v.get("status") == "running" and not running:
            v["status"], v["msg"] = "failed", "interrupted (the server restarted or the step was stopped)"
    if not running and all(r["ok"] for r in inputs) and (steps.get("input") or {}).get("status", "") in ("failed", ""):
        steps["input"] = {"status": "done", "msg": "both files in place", "at": ""}
    sv = stored_settlement()
    return {"name": NAME, "date": st.get("date"), "status": "running" if running else s, "running": running,
            "fetching": False, "stopping": bool(_ACTIVE.get("cancel")) and running, "started": st.get("started", ""),
            "finished": st.get("finished", ""), "error": st.get("error", ""), "trigger": st.get("trigger", ""), "by": st.get("by", ""),
            "counts": st.get("counts", {}), "sent": st.get("sent", []), "made": st.get("made", []),
            "alert_sent": bool(st.get("alert_sent")), "alert_reason": st.get("alert_reason", ""), "auto": False,
            "needs_password": s == "done" and not running,
            "settlement": {"count": len(sv["prices"]), "source": sv["source"], "at": sv["at"]},
            "settings": {k: set_[k] for k in ("send_mail", "alert_numbers")},
            "steps": [dict(key=k, label=STEP_LABEL[k], **steps.get(k, {"status": "", "msg": ""})) for k in STEP_ORDER],
            "inputs": inputs, "folder": str(root()), "log": recent_log(60)}
