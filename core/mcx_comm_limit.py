"""core/mcx_comm_limit.py - MCX COMM LIMIT: the daily Kambala commodity-limit file, built and sent by itself.

Port of MCX_COMM_LIMIT_CASH_MARGIN_V2.py - THE CALCULATION IS UNCHANGED (calculate() below is the same logic, line for line).
What is new is everything around it:

    inputs  (all land in ONE folder, root() = <console data>/MCX COMM LIMIT)
        MCX_ACTIVE_DETAILS_<ddmmyyyy>.csv         mailbox (POP3)                         step "mail"
        CASH_MARGIN*.csv                          back-office API custom report          step "korp"
        CO/FO/CM_ClientLevelCollaterals_*.csv     NMASS (same script as Margin Shortage) step "nmass"
        RISK_REPORT_<ddmmyyyy>.csv                READ from COMMON FILES (never copied here, never backed up here)   step "risk"
        Kambala_OMNY_COM_DEPO_ADHOC_<d>.TXT       depo FTP -> this folder                step "kambala"
        SearchResults*.xlsx  (TCS BANCS RSK136)   saved by PROBABLE's TCS download, else downloaded here   step "tcs"
        EXCLUSION.xlsx                            permanent list, ONLY in this folder (Upload files)   step "exclusion"
    every step is skipped when its file for today is already in the folder.

    then   pre-run check -> calculate -> e-mail -> Kambala FTP -> backup (BACKUPFILES AUTOMATED/<dd-mm-yyyy>) and clean-up.

RUN RULES
    * auto  : started 10 s after PROBABLE's Run (hook_probable()). Only ONCE per day - if it failed, the failure alert goes
              to WhatsApp and nobody retries by themselves.
    * manual: the Run button. If today's run is already DONE it needs the console password; after a failed / never-run day it
              goes straight through.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

NAME = "MCX COMM LIMIT"
FOLDER_NAME = "MCX COMM LIMIT"
PIPE = "mcxcomm"
BACKUP_SUB = "BACKUPFILES AUTOMATED"
REPO = Path(__file__).resolve().parent.parent

FO_CAP = 25_00_000
ELIGIBILITY_MIN = 25_00_000
CASH_MARGIN_NONCOM_CAP = 25_00_000
FORCE_COM_BAS = {"9VJ01", "9DM99", "9VJ05", "9VJ99", "8LAA2121", "8LAA2152"}
DEFAULT_TO = "rms_del@mastertrust.co.in"
KAMB_DEPO_DIR = "/TRADING/CTCL_FILES/KORP_LIVE/KAMB/DEPO"

KAMB_PATTERN = "Kambala_OMNY_COM_DEPO_ADHOC_*.TXT"
FO_PATTERNS = ["FO_ClientLevelCollaterals_*.csv", "FO_ClientLevelCollaterals_*.xlsx", "FO_ClientLevelCollaterals_*.csv.crdownload"]
CO_PATTERNS = ["CO_ClientLevelCollaterals_*.csv"]
CM_PATTERNS = ["CM_ClientLevelCollaterals_*.csv"]
CASH_PATTERNS = ["CASH_MARGIN*.csv", "CASH_MARGIN*.CSV"]
SEARCH_PATTERNS = ["SearchResults*.xlsx"]
MCX_PATTERNS = ["MCX_ACTIVE_DETAILS_*.csv"]
RISK_PATTERNS = ["RISK_REPORT_*.csv"]
EXCL_PATTERNS = ["EXCLUSION.xlsx"]

# label, key, patterns, how the pre-run check judges the date
INPUTS = [
    ("MCX active clients (mail)", "MCX_ACTIVE_DETAILS", MCX_PATTERNS, "none"),
    ("Risk report", "RISK_REPORT", RISK_PATTERNS, "name"),
    ("Cash margin (KORP)", "CASH_MARGIN", CASH_PATTERNS, "name_or_mtime"),
    ("TCS search result", "SEARCH_RESULTS", SEARCH_PATTERNS, "mtime"),
    ("FO collaterals", "FO_CLIENT_LEVEL", FO_PATTERNS, "name"),
    ("CO collaterals", "CO_CLIENT_LEVEL", CO_PATTERNS, "name"),
    ("CM collaterals", "CM_CLIENT_LEVEL", CM_PATTERNS, "name"),
    ("Kambala TXT", "KAMBALA_TXT", [KAMB_PATTERN], "name"),
    ("Exclusion list", "EXCLUSION", EXCL_PATTERNS, "none"),
]
BACKUP_PATTERNS = (MCX_PATTERNS + CASH_PATTERNS + SEARCH_PATTERNS + CO_PATTERNS + CM_PATTERNS + FO_PATTERNS
                   + EXCL_PATTERNS + [KAMB_PATTERN, "sum.csv", "ClientNetEffectiveDeposits*.csv"])   # the last one comes along with the NMASS download

STEP_LABEL = {"risk": "Risk report", "kambala": "Kambala TXT", "exclusion": "Exclusion list", "nmass": "NMASS (CO/FO/CM)",
              "korp": "Cash margin (KORP)", "mail": "MCX active mail", "tcs": "TCS search result", "calc": "Calculation",
              "email": "E-mail", "ftp": "Kambala FTP", "backup": "Backup"}

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_LOG = collections.deque(maxlen=400)
_ACTIVE = {"procs": [], "cancel": False, "log_file": None}


class Busy(Exception):
    pass


class PreRunError(Exception):
    pass


class Cancelled(Exception):
    pass


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("MCX_COMM_DIR", "").strip()
    if v:
        p = Path(v)
    else:
        try:
            from core.scheduler import paths
            base = paths.data_root()
        except Exception:
            base = REPO / "data"
        p = base / FOLDER_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def _state_dir() -> Path:
    p = root() / "_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_root() -> Path:
    return root() / BACKUP_SUB


def log_dir() -> Path:
    v = os.getenv("MCX_COMM_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / "mcxcomm"
    except Exception:
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _today() -> dt.date:
    return dt.date.today()


def _iso(t=None) -> str:
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


# ───────────────────────────── settings / state ─────────────────────────────
def _settings_path() -> Path:
    return _state_dir() / "settings.json"


def get_settings() -> dict:
    d = {"auto": True, "delay_s": 10, "ftp_enabled": True, "mail_day": "auto", "alert_numbers": ""}
    try:
        d.update(json.loads(_settings_path().read_text(encoding="utf-8")))
    except Exception:
        pass
    d["delay_s"] = max(0, int(d.get("delay_s") or 0))
    if d.get("mail_day") not in ("auto", "today", "yesterday"):
        d["mail_day"] = "auto"
    return d


def save_settings(update: dict, by: str = "") -> dict:
    cur = get_settings()
    if "auto" in update:
        cur["auto"] = bool(update["auto"])
    if "ftp_enabled" in update:
        cur["ftp_enabled"] = bool(update["ftp_enabled"])
    if "mail_day" in update and update["mail_day"] in ("auto", "today", "yesterday"):
        cur["mail_day"] = update["mail_day"]
    if "delay_s" in update:
        cur["delay_s"] = max(0, min(600, int(update["delay_s"])))
    if "alert_numbers" in update:
        raw = str(update["alert_numbers"] or "").strip()
        if raw:
            from core.scheduler import failure_alert
            if not failure_alert.parse_numbers(raw):
                raise ValueError("those WhatsApp numbers are not valid")
        cur["alert_numbers"] = raw
    cur["by"], cur["at"] = by, _iso()
    with _STATE_LOCK:
        _settings_path().write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return cur


def auto_enabled() -> bool:
    return bool(get_settings().get("auto"))


def _state_path() -> Path:
    return _state_dir() / "state.json"


def read_state() -> dict:
    """Today's record. A record from an earlier day is ignored (a new day starts clean)."""
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        st = {}
    if st.get("date") != dstr(_today()):
        return {"date": dstr(_today()), "status": "idle", "auto_tried": False, "steps": {}}
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


# ───────────────────────────── logging ─────────────────────────────
def log(msg: str):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    _LOG.append(line)
    f = _ACTIVE.get("log_file")
    if f:
        try:
            f.write(line + "\n")
            f.flush()
        except Exception:
            pass
    print("[mcxcomm] " + msg, flush=True)


def recent_log(n: int = 120) -> list:
    return list(_LOG)[-n:]


def _open_run_log(trigger, by):
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"mcxcomm__{dstr(now.date())}__{now:%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== {NAME} - {now:%d-%m-%Y %H:%M:%S} =====\nstarted by: {by or trigger} ({trigger})\n\n")
        f.flush()
        return f
    except Exception:
        return None


def _cancel() -> bool:
    return bool(_ACTIVE.get("cancel"))


def _check_cancel():
    if _cancel():
        raise Cancelled("Stopped.")


# ───────────────────────────── file helpers (same rules as the original script) ─────────────────────────────
def extract_date_from_filename(filename):
    m = re.search(r"(?<!\d)(\d{2}\d{2}20\d{2})", filename)
    if m:
        return m.group(1)
    m = re.search(r"(?<!\d)(20\d{6})(?!\d)", filename)
    return m.group(1) if m else None


def is_today_file(filename, today: dt.date = None) -> bool:
    s = extract_date_from_filename(filename)
    if not s:
        return False
    t = today or _today()
    return s in (t.strftime("%d%m%Y"), t.strftime("%Y%m%d"))


def is_modified_today(path, today: dt.date = None) -> bool:
    return dt.datetime.fromtimestamp(os.path.getmtime(path)).date() == (today or _today())


def _glob(base, patterns):
    out = []
    for p in patterns:
        out.extend(glob.glob(os.path.join(str(base), p)))
    return out


def pick_latest(base, patterns):
    files = _glob(base, patterns)
    if not files:
        raise FileNotFoundError(f"No file found for patterns: {patterns}")
    return max(files, key=os.path.getmtime)


def have_today(patterns, mode="name_or_mtime", base=None) -> bool:
    files = _glob(base or root(), patterns)
    if not files:
        return False
    f = max(files, key=os.path.getmtime)
    n = os.path.basename(f)
    if mode == "name":
        return is_today_file(n)
    if mode == "mtime":
        return is_modified_today(f)
    if mode == "none":
        return True
    return is_today_file(n) or is_modified_today(f)


def input_status(base=None) -> list:
    """One row per input: found?, file name, is it today's?  (the pre-run check, as a list)."""
    base = base or root()
    rows = []
    for label, key, pats, mode in INPUTS:
        files = _glob(risk_dir(), risk_patterns()) if key == "RISK_REPORT" else _glob(base, pats)
        if not files:
            rows.append({"key": key, "label": label, "found": False, "name": "", "ok": False, "why": "missing"})
            continue
        f = max(files, key=os.path.getmtime)
        n = os.path.basename(f)
        ok = True
        why = ""
        if mode == "name" and not is_today_file(n):
            ok, why = False, "not today's date"
        elif mode == "mtime" and not is_modified_today(f):
            ok, why = False, "not modified today"
        elif mode == "name_or_mtime" and not (is_today_file(n) or is_modified_today(f)):
            ok, why = False, "not today by name or time"
        rows.append({"key": key, "label": label, "found": True, "name": n, "ok": ok, "why": why,
                     "size": os.path.getsize(f), "time": dt.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%H:%M:%S")})
    return rows


def pre_run_check(base=None) -> dict:
    """Same all-mandatory check as the original script. Raises PreRunError with the plain list of problems."""
    rows = input_status(base)
    missing = [r["label"] for r in rows if not r["found"]]
    wrong = [f"{r['label']} -> {r['name']} ({r['why']})" for r in rows if r["found"] and not r["ok"]]
    if missing or wrong:
        parts = []
        if missing:
            parts.append("Missing files: " + ", ".join(missing))
        if wrong:
            parts.append("Not of the current date: " + "; ".join(wrong))
        raise PreRunError(" | ".join(parts))
    return {r["key"]: r["name"] for r in rows}


# ───────────────────────────── the CALCULATION (unchanged from V2) ─────────────────────────────
def calculate(base, log=log) -> dict:
    """Builds sum.csv and rewrites the Kambala TXT. -> {'rows': n, 'txt': path, 'kamb': n, 'eligible': n}"""
    import pandas as pd

    BASE_DIR = str(base)

    def clean_cols(df):
        df.columns = (df.columns.str.strip().str.upper().str.replace(r"[^\w]", "_", regex=True)
                      .str.replace("__+", "_", regex=True).str.strip("_"))
        return df

    def to_number(series):
        return series.astype(str).str.replace(",", "", regex=False).astype(float).fillna(0)

    def pl(patterns):
        return pick_latest(BASE_DIR, patterns)

    def client_level_sum(patterns):
        f = pl(patterns)
        df = pd.read_excel(f) if f.lower().endswith(".xlsx") else pd.read_csv(f, dtype=str)
        df = clean_cols(df)
        cash_c = next(c for c in df.columns if "CASH" in c and "OTHER" in c)
        non_cash_d = next(c for c in df.columns if "NON" in c and "SECUR" in c)
        total = to_number(df[cash_c]) + to_number(df[non_cash_d])
        log(f"{os.path.basename(f)}: {len(df)} rows")
        return total.groupby(df["CLIENT_CODE"].astype(str).str.strip()).sum()

    _co = client_level_sum(CO_PATTERNS)
    _fo = client_level_sum(FO_PATTERNS)
    _cm = client_level_sum(CM_PATTERNS)
    client_total = pd.concat([_co, _fo, _cm]).groupby(level=0).sum()
    client_total = client_total[client_total >= ELIGIBILITY_MIN]
    df_reco = pd.DataFrame({"ACCOUNTID": client_total.index.astype(str)})
    log(f"Clients with CO+FO+CM (c+d) >= {ELIGIBILITY_MIN:,}: {len(df_reco)}")
    eligible = len(df_reco)

    # MCX ACTIVE DETAILS
    df_mcx = pd.read_csv(pl(MCX_PATTERNS))
    df_mcx = clean_cols(df_mcx)
    permit_col = next(c for c in df_mcx.columns if "PERMIT" in c)
    df_mcx = df_mcx[df_mcx[permit_col].astype(str).str.upper().str.strip() == "Y"]
    df_mcx = df_mcx[["ACCOUNTID"]]
    df_mcx["ACCOUNTID"] = df_mcx["ACCOUNTID"].astype(str).str.strip()
    df_reco = df_reco.merge(df_mcx, on="ACCOUNTID", how="inner")

    # RISK REPORT
    df_risk = pd.read_csv(pick_latest(risk_dir(), risk_patterns()), low_memory=False)   # straight from COMMON FILES
    df_risk = clean_cols(df_risk)
    df_risk["FIN_WITHOUT_MGN"] = to_number(df_risk["FIN_WITHOUT_MGN"])
    if "TRADING_SOFTWARE_TYPE" not in df_risk.columns:
        raise ValueError("TRADING_SOFTWARE_TYPE column not found in RISK_REPORT")
    kamb_accounts = set(df_risk.loc[df_risk["TRADING_SOFTWARE_TYPE"].astype(str).str.strip().str.upper() == "KAMB",
                                    "ACCOUNTID"].astype(str).str.strip())
    df_risk["ACCOUNTID"] = df_risk["ACCOUNTID"].astype(str).str.strip()
    df_risk = df_risk[["ACCOUNTID", "FIN_WITHOUT_MGN"]]
    df_reco = df_reco.merge(df_risk, on="ACCOUNTID", how="left").fillna(0)

    # CASH MARGIN -> CASH_LEDGER
    def compute_cash_ledger_from_margin(df_cm):
        df_cm = clean_cols(df_cm)
        df_cm["CASH_MARGIN_AMOUNT"] = to_number(df_cm["CASH_MARGIN_AMOUNT"])
        df_cm["SEG_ID"] = df_cm["SEG_ID"].astype(str).str.strip().str.upper()
        records = []
        for account, grp in df_cm.groupby("ACCOUNTID"):
            is_com = grp["SEG_ID"] == "COM"
            com_value = grp.loc[is_com, "CASH_MARGIN_AMOUNT"].sum()
            non_com_value = grp.loc[~is_com, "CASH_MARGIN_AMOUNT"].sum()
            if non_com_value > CASH_MARGIN_NONCOM_CAP:
                non_com_value = CASH_MARGIN_NONCOM_CAP
            records.append({"ACCOUNTID": account, "CASH_LEDGER": com_value + non_com_value})
        return pd.DataFrame(records, columns=["ACCOUNTID", "CASH_LEDGER"])

    df_cash_margin = pd.read_csv(pl(CASH_PATTERNS))
    df_cash_ledger = compute_cash_ledger_from_margin(df_cash_margin)
    df_cash_ledger["ACCOUNTID"] = df_cash_ledger["ACCOUNTID"].astype(df_reco["ACCOUNTID"].dtype)
    df_reco = df_reco.merge(df_cash_ledger, on="ACCOUNTID", how="left").fillna(0)
    log(f"CASH_LEDGER computed from CASH_MARGIN for {df_cash_ledger.shape[0]} clients")

    # SEARCH RESULTS
    search_file = pl(SEARCH_PATTERNS)
    raw = pd.read_excel(search_file, header=None)
    header_row = next(i for i in range(10) if raw.iloc[i].astype(str).str.contains("UCC", case=False).any())
    df_search = pd.read_excel(search_file, header=header_row)
    df_search.columns = df_search.columns.str.strip()
    df_search["Value After Haircut"] = to_number(df_search["Value After Haircut"])
    df_haircut = (df_search.groupby("UCC Code", as_index=False)["Value After Haircut"].sum()
                  .rename(columns={"UCC Code": "ACCOUNTID", "Value After Haircut": "TOTAL_HAIRCUT"}))
    df_reco = df_reco.merge(df_haircut, on="ACCOUNTID", how="left").fillna(0)

    # FO CLIENT LEVEL COLLATERALS
    fo_file = pl(FO_PATTERNS)
    df_fo = pd.read_excel(fo_file) if fo_file.lower().endswith(".xlsx") else pd.read_csv(fo_file)
    df_fo = clean_cols(df_fo)
    cash_col = next(c for c in df_fo.columns if "CASH" in c and "OTHER" in c)
    non_cash_col = next(c for c in df_fo.columns if "NON" in c and "SECUR" in c)
    df_fo[cash_col] = to_number(df_fo[cash_col])
    df_fo[non_cash_col] = to_number(df_fo[non_cash_col])
    df_fo["FO_SUM"] = (df_fo[cash_col] + df_fo[non_cash_col]).clip(upper=FO_CAP)
    df_fo = df_fo.groupby("CLIENT_CODE", as_index=False)["FO_SUM"].sum()
    df_fo.rename(columns={"CLIENT_CODE": "ACCOUNTID"}, inplace=True)
    df_reco = df_reco.merge(df_fo, on="ACCOUNTID", how="left").fillna(0)

    # FINAL SUM
    df_reco["FINAL_SUM"] = df_reco["FIN_WITHOUT_MGN"] + df_reco["CASH_LEDGER"] + df_reco["TOTAL_HAIRCUT"] + df_reco["FO_SUM"]
    df_reco["FINAL_SUM"] = df_reco["FINAL_SUM"].clip(lower=0)

    # sum.csv
    sum_file = os.path.join(BASE_DIR, "sum.csv")
    df_reco[["ACCOUNTID", "FINAL_SUM"]].to_csv(sum_file, index=False)

    # Kambala TXT: NOVAL_EXP clients
    txt_file = pl([KAMB_PATTERN])
    noval_exp_clients = set()
    with open(txt_file, "r", encoding="utf-8") as f:
        rows = [line.rstrip("\n").split("|") for line in f]
    for row in rows[1:]:
        if len(row) > 16:
            if row[16].strip().upper() == "NOVAL_EXP":
                cc = row[0].strip()
                if cc:
                    noval_exp_clients.add(cc)
    log(f"Noval_Exp clients found: {len(noval_exp_clients)}")

    # update the TXT
    rows = []
    with open(txt_file, "r", encoding="utf-8") as f:
        for line in f:
            rows.append(line.rstrip("\n").split("|"))
    header = rows[0]
    data_rows = rows[1:]

    df_sum = pd.read_csv(sum_file)
    df_sum["ACCOUNTID"] = df_sum["ACCOUNTID"].astype(str)
    exclusion_file = os.path.join(BASE_DIR, "EXCLUSION.xlsx")
    if os.path.exists(exclusion_file):
        df_excl = pd.read_excel(exclusion_file)
        df_excl.columns = df_excl.columns.str.strip().str.upper()
        if "ACCOUNTID" not in df_excl.columns:
            raise ValueError("ACCOUNTID column not found in EXCLUSION.xlsx")
        excluded = set(df_excl["ACCOUNTID"].astype(str))
        df_sum = df_sum[~df_sum["ACCOUNTID"].isin(excluded)]
    df_sum = df_sum.reset_index(drop=True)

    before_cnt = len(df_sum)
    df_sum = df_sum[df_sum["ACCOUNTID"].isin(kamb_accounts)]
    df_sum = df_sum.reset_index(drop=True)
    after_cnt = len(df_sum)
    log(f"KAMB filter (RISK_REPORT): dropped {before_cnt - after_cnt} non-KAMB clients, remaining {after_cnt}")

    max_cols = max(len(r) for r in rows)
    max_cols = max(max_cols, 17)
    new_rows = []
    for i in range(len(df_sum)):
        base_row = data_rows[i] if i < len(data_rows) else []
        row = base_row + [""] * (max_cols - len(base_row))
        row[0] = df_sum.loc[i, "ACCOUNTID"]
        row[4] = f"{df_sum.loc[i, 'FINAL_SUM']:.2f}"
        account_id = df_sum.loc[i, "ACCOUNTID"]
        if account_id in FORCE_COM_BAS:
            row[16] = "COM_BAS"
        elif account_id in noval_exp_clients:
            row[16] = "COM_BAS"
        else:
            row[16] = "COMMODITY_EXP"
        new_rows.append(row)

    with open(txt_file, "w", encoding="utf-8") as f:
        f.write("|".join(header) + "\n")
        for r in new_rows:
            f.write("|".join(r) + "\n")
    log(f"TXT updated: {len(new_rows)} rows written (matches sum.csv)")
    return {"rows": len(new_rows), "txt": txt_file, "kamb": after_cnt, "eligible": eligible}


# ───────────────────────────── e-mail / FTP / backup ─────────────────────────────
def recipients():
    from core.mcx_cash_report import split_addrs
    to = split_addrs(os.getenv("MCXCOMM_TO", DEFAULT_TO)) or [DEFAULT_TO]
    return to, split_addrs(os.getenv("MCXCOMM_CC", ""))


def mail_body() -> str:
    today = dt.datetime.now().strftime("%d-%m-%Y")
    return f"""
<html><body>
    <p>Hi Team,</p>
    <p>Please find attached the daily <b>MCX_COMMODITY_LIMIT_FILE</b> dated <b>{today}</b>.</p>
    <p><b>Note:</b><br>This file has been generated based on the following criteria:
    <ul>
        <li>Clients with <b>(Cash-others (c) + Non-cash Securities (d)) &ge; &#8377;25,00,000</b>, summed across the <b>CO, FO and CM ClientLevelCollaterals</b> files, only.</li>
        <li>Clients with <b>MCX Permit to Trade = 'Y'(MCX_ACTIVE_DETAILS_CURRENTDATE.csv)</b> only.</li>
        <li>Final limit is calculated as the sum of:
            <ul>
                <li>Financial Collateral without Margin(RISK REPORT),</li>
                <li>Cash Ledger &mdash; computed from the <b>CASH_MARGIN</b> file as COM segment (exact) + all other segments (summed, capped at <b>&#8377;25,00,000</b>), so the net reflects funds actually available,</li>
                <li>Value After Haircut (Search Results), and</li>
                <li>FO Client Level Collateral (capped at <b>&#8377;25,00,000</b> per client).</li>
            </ul>
        </li>
        <li>Only clients with <b>TRADING_SOFTWARE_TYPE = KAMB</b> (as per RISK REPORT) have been included; all other trading software codes have been excluded.</li>
        <li>Clients listed in the <b>Exclusion file</b> have been removed.</li>
    </ul></p>
    <br>
    <p>Best Regards,<br><b>Kartavya Dhillon</b><br>Master Capital Services Ltd<br>011-66331000 | Ext. 84</p>
</body></html>"""


def send_email(txt_path, log=log):
    from core import mcx_cash_report as R
    to, cc = recipients()
    R.send_mail(to, cc, "MCX COMMODITY LIMIT FILE", mail_body(), txt_path, log=log)


def kambala_ftp_cfg() -> dict:
    from core.scheduler import connections
    c = connections.get("kambala_ftp")
    return {"host": c.get("host") or "202.58.232.29", "port": int(c.get("port") or 21), "user": c.get("user") or "ftpadmin",
            "password": c.get("password") or os.getenv("MCXCOMM_FTP_PASS", ""), "folder": c.get("folder") or "/"}


def upload_to_kambala_ftp(file_path, log=log) -> bool:
    """<name>.part, rename, size check, 5 tries. Never raises; True only when the file is confirmed on the server."""
    if not get_settings().get("ftp_enabled", True):
        log("Kambala FTP upload is switched off.")
        return False
    cfg = kambala_ftp_cfg()
    if not cfg["password"]:
        log("Kambala FTP upload skipped - the FTP password is empty (Scheduler > Connections > Kambala FTP).")
        return False
    from ftplib import FTP, error_perm
    name = os.path.basename(file_path)
    size = os.path.getsize(file_path)

    def once():
        ftp = FTP()
        ftp.connect(cfg["host"], cfg["port"], timeout=60)
        try:
            ftp.login(cfg["user"], cfg["password"])
            ftp.set_pasv(True)
            ftp.cwd("/")
            for d in [p for p in cfg["folder"].replace("\\", "/").split("/") if p]:
                try:
                    ftp.cwd(d)
                except error_perm:
                    ftp.mkd(d)
                    ftp.cwd(d)
            with open(file_path, "rb") as f:
                ftp.storbinary(f"STOR {name}.part", f, blocksize=64 * 1024)
            try:
                ftp.delete(name)
            except Exception:
                pass
            ftp.rename(name + ".part", name)
            ftp.voidcmd("TYPE I")
            try:
                got = ftp.size(name)
            except Exception:
                got = None
            if got is not None and got != size:
                raise RuntimeError(f"size mismatch for {name}: local {size:,} bytes, server {got:,} bytes")
        finally:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

    tries = 5
    for attempt in range(1, tries + 1):
        _check_cancel()
        try:
            log(f"Kambala FTP upload of {name} (attempt {attempt}/{tries})...")
            once()
            log(f"Kambala FTP: uploaded {name} ({size:,} bytes)")
            return True
        except error_perm as exc:
            log(f"Kambala FTP refused the request ({exc}) - not retrying. Check user / password / folder.")
            return False
        except Exception as exc:                                  # noqa: BLE001
            log(f"Kambala FTP attempt {attempt} failed: {exc}")
            if attempt < tries:
                time.sleep(5)
    log(f"Kambala FTP upload of {name} FAILED after {tries} attempts")
    return False


def backup_and_cleanup(base=None, log=log) -> Path:
    base = Path(base or root())
    txt_name = os.path.basename(pick_latest(base, [KAMB_PATTERN]))
    m = re.search(r"_(\d{8})", txt_name)
    day = dt.datetime.strptime(m.group(1), "%d%m%Y").date() if m else _today()
    dest = backup_root() / dstr(day)
    dest.mkdir(parents=True, exist_ok=True)
    files = list(set(_glob(base, BACKUP_PATTERNS)))
    for fp in files:
        shutil.copy2(fp, dest / os.path.basename(fp))
        log(f"backed up {os.path.basename(fp)}")
    for fp in files:
        n = os.path.basename(fp)
        if n.upper() == "EXCLUSION.XLSX" or n.lower().endswith((".py", ".bat")) or os.path.isdir(fp):
            continue
        try:
            os.remove(fp)
        except OSError as e:
            log(f"could not remove {n}: {e}")
    log(f"Backup and clean-up done -> {dest}")
    return dest


# ───────────────────────────── child processes ─────────────────────────────
def _python() -> str:
    return os.getenv("CTCL_PYTHON") or os.getenv("SA_PYTHON") or sys.executable


def _kill(p):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=15)
        else:
            p.kill()
    except Exception:
        pass


_INTEREST = re.compile(r"\[[A-Z0-9_:]+\]|saved|login|logged|token|session|download|wait|export|error|failed|READY|queued|RESUM", re.I)


def _run_child(cmd, env, timeout_s):
    flags = 0x08000000 if os.name == "nt" else 0
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(REPO), text=True,
                         encoding="utf-8", errors="replace", creationflags=flags)
    _ACTIVE["procs"].append(p)
    tail = []

    def pump():
        for line in p.stdout:
            line = line.rstrip()
            if not line:
                continue
            tail.append(line)
            del tail[:-60]
            if _INTEREST.search(line):
                log(line[:300])
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t0 = time.time()
    try:
        while p.poll() is None:
            if _cancel():
                _kill(p)
                break
            if time.time() - t0 > timeout_s:
                log(f"Stopped: no result after {int(timeout_s // 60)} minutes.")
                _kill(p)
                break
            time.sleep(0.5)
        p.wait(timeout=20)
    finally:
        try:
            _ACTIVE["procs"].remove(p)
        except ValueError:
            pass
    t.join(timeout=5)
    return (p.returncode if p.returncode is not None else -1), tail


# ───────────────────────────── the input steps ─────────────────────────────
# each returns {"ok": bool, "msg": str, "skipped": bool}
def risk_dir() -> Path:
    """The RISK_REPORT is READ from COMMON FILES - never copied into this card's folder and never backed up by this card."""
    from core import common_ftp
    return Path(common_ftp.common_dir())


def risk_patterns() -> list:
    """Exactly today's RISK_REPORT_<ddmmyyyy>.csv."""
    return [f"RISK_REPORT_{_today():%d%m%Y}.csv"]


def step_risk(base: Path) -> dict:
    """Only checks that today's RISK_REPORT is in COMMON FILES (the console's COMMON FILES fetch puts it there)."""
    name = risk_patterns()[0]
    if (risk_dir() / name).is_file():
        return {"ok": True, "skipped": True, "msg": f"{name} is in COMMON FILES (read from there)"}
    return {"ok": False, "msg": f"{name} is not in COMMON FILES yet ({risk_dir()})"}


def _ftp_cfg_kamb_depo():
    return os.getenv("FTP_HOST", "202.58.232.124"), os.getenv("FTP_USER", "TRADING"), os.getenv("FTP_PASS", "")


def step_kambala(base: Path) -> dict:
    """Kambala_OMNY_COM_DEPO_ADHOC_<ddmmyyyy>.TXT: already in this card's folder, else downloaded from the FTP into it."""
    if have_today([KAMB_PATTERN], "name", base):
        return {"ok": True, "skipped": True, "msg": "already in the folder"}
    name = f"Kambala_OMNY_COM_DEPO_ADHOC_{_today():%d%m%Y}.TXT"
    host, user, pw = _ftp_cfg_kamb_depo()
    if not pw:
        return {"ok": False, "msg": f"{name} is not in the folder and the depo file FTP password is not set (Credentials)"}
    from ftplib import FTP
    ftp = FTP(host, timeout=60)
    try:
        ftp.login(user, pw)
        ftp.cwd(KAMB_DEPO_DIR)
        remote = {r.lower(): r for r in ftp.nlst()}
        rf = remote.get(name.lower())
        if not rf:
            return {"ok": False, "msg": f"{name} is not on the FTP yet"}
        part = base / (name + ".part")
        with open(part, "wb") as f:
            ftp.retrbinary("RETR " + rf, f.write)
        part.replace(base / name)
        return {"ok": True, "msg": f"downloaded {name} from FTP"}
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def step_exclusion(base: Path) -> dict:
    """EXCLUSION.xlsx lives ONLY in this card's folder (put it there once with Upload files)."""
    if (base / "EXCLUSION.xlsx").is_file():
        return {"ok": True, "skipped": True, "msg": "present"}
    return {"ok": False, "msg": "EXCLUSION.xlsx is not in the folder - use Upload files"}


def step_nmass(base: Path) -> dict:
    """CO + FO + CM ClientLevelCollaterals by the console's NMASS script (shared with Margin Shortage)."""
    if all(have_today(p, "name", base) for p in (CO_PATTERNS, FO_PATTERNS, CM_PATTERNS)):
        return {"ok": True, "skipped": True, "msg": "CO / FO / CM already in the folder"}
    script = os.getenv("CTCL_NMASS_SCRIPT", str(REPO / "ctcl_codes" / "NMASS_NCFE_API_MIX.PY"))
    if not os.path.isfile(script):
        return {"ok": False, "msg": f"NMASS script not found: {script}"}
    from core import dl_lock
    waited = 0
    while True:
        _check_cancel()
        try:
            dl_lock.acquire("mcxcomm")
            break
        except dl_lock.NmassLoginBusyError as e:
            if waited > 600:
                return {"ok": False, "msg": f"The NMASS login stayed busy (used by '{e.owner}')."}
            if waited % 30 == 0:
                log(f"NMASS login is in use by '{e.owner}' - waiting...")
            time.sleep(5)
            waited += 5
    try:
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                    "NMASS_SAVE_DIR": str(base), "NCFE_LOGOUT_AFTER": "1"})
        env.pop("NMASS_RUN_MODE", None)
        alive = threading.Event()

        def keep_fresh():
            while not alive.wait(45):
                try:
                    os.utime(dl_lock._LOCK_PATH, None)
                except OSError:
                    pass
        threading.Thread(target=keep_fresh, daemon=True).start()
        try:
            rc, tail = _run_child([_python(), script], env, int(os.getenv("MCXCOMM_NMASS_TIMEOUT_MIN", "20")) * 60)
        finally:
            alive.set()
    finally:
        dl_lock.release()
    miss = [n for n, p in (("CO", CO_PATTERNS), ("FO", FO_PATTERNS), ("CM", CM_PATTERNS)) if not have_today(p, "name", base)]
    if not miss:
        return {"ok": True, "msg": "CO, FO and CM collaterals downloaded"}
    auth = next((ln for ln in tail if re.search(r"NMASS_(AUTH_FAIL|LOCKED)", ln)), "")
    return {"ok": False, "msg": ("NMASS login problem - check Credentials. " if auth else "") + f"missing: {', '.join(miss)} (script ended with code {rc})"}


def step_korp(base: Path) -> dict:
    """CASH_MARGIN custom report from the back-office API (Scheduler > Connections > Back-office API)."""
    if have_today(CASH_PATTERNS, "name_or_mtime", base):
        return {"ok": True, "skipped": True, "msg": "already in the folder"}
    import base64
    import requests
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from core import korp_api
    from core.scheduler import connections
    c = connections.require("backoffice", "base_url", "username", "password", "client_ip", "client_mac")
    b = korp_api.pick_base(c["base_url"], log=log)
    s = requests.Session()
    key = korp_api.parse_session_key(s.get(f"{b}/api/Common/SessionKey/Get", timeout=30).text).encode()
    pdr = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    pw = base64.b64encode(enc.update(pdr.update(c["password"].encode()) + pdr.finalize()) + enc.finalize()).decode()
    lg = s.post(f"{b}/api/token", data={"CallFrom": "FINKORP", "UserName": c["username"], "Password": pw, "Grant_type": "password",
                                       "ClientIP": c["client_ip"], "ClientMAC": c["client_mac"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    try:
        token = lg.json()["access_token"]
    except Exception:
        return {"ok": False, "msg": f"Back-office login failed (HTTP {lg.status_code}) - check Connections > Back-office API."}
    d = _today()
    fy = f"{d.year}-{d.year + 1}" if d.month >= 4 else f"{d.year - 1}-{d.year}"
    hdr = {"Authorization": f"Bearer {token}", "FIRMID": "1001", "FINANCIALYEAR": fy}
    tr = s.get(f"{b}/api/Masters/CustomReportTemplate/Get", headers=hdr,
               params={"TemplateType": "CUSTOM", "TemplateID": ""}, timeout=(10, 120))
    template = {}
    try:
        for t in tr.json():
            if any(isinstance(v, str) and v.strip().upper() == "CASH_MARGIN" for v in t.values()):
                template = t
                break
    except Exception:
        pass
    if not template:
        return {"ok": False, "msg": "the CASH_MARGIN template was not found in the back office"}
    file_name = next((v for k, v in template.items() if k.upper() in ("FILENAME", "FILE_NAME") and isinstance(v, str)), "")
    ds = d.strftime("%Y-%m-%d")
    payload = [{"FirmID": "1001", "TemplateType": "CUSTOM", "TemplateID": "CASH_MARGIN", "FileName": file_name,
                "FromDate": ds, "ToDate": ds, "RateDate": ds, "AccountID": "", "Branch": "", "SubBranch": "",
                "Custom1": "", "Custom2": "", "Custom3": "", "Custom4": ""}]
    last = ""
    for attempt in range(1, 5):
        _check_cancel()
        try:
            r = s.post(f"{b}/api/Utilities/CustomReportExport/Post", json=payload, timeout=(10, 180),
                       headers=dict(hdr, **{"Content-Type": "application/json; charset=utf-8"}))
            if r.status_code == 200 and r.content and "octet-stream" in r.headers.get("Content-Type", ""):
                break
            last = f"HTTP {r.status_code} {r.text[:150]}"
        except requests.exceptions.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        log(f"CASH_MARGIN export try {attempt}/4 failed: {last}")
        time.sleep(2 * attempt)
    else:
        return {"ok": False, "msg": f"CASH_MARGIN export failed: {last}"}
    cd = r.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.I)
    out_name = os.path.basename(m.group(1).strip()) if m else f"CASH_MARGIN_{d:%d%m%Y}.zip"
    out = base / (out_name + ".part")
    out.write_bytes(r.content)
    final = base / out_name
    out.replace(final)
    got = []
    if zipfile.is_zipfile(final):
        with zipfile.ZipFile(final) as z:
            for n in [n for n in z.namelist() if not n.endswith("/")]:
                t = base / os.path.basename(n)
                with z.open(n) as src, open(t, "wb") as o:
                    o.write(src.read())
                got.append(t.name)
        final.unlink()
    else:
        got.append(final.name)
    if not any(re.match(r"CASH_MARGIN.*\.csv$", g, re.I) for g in got):
        return {"ok": False, "msg": f"the CASH_MARGIN download held no CASH_MARGIN csv ({', '.join(got)})"}
    return {"ok": True, "msg": "downloaded " + ", ".join(got)}


# ── mailbox ──
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def pop_cfg() -> dict:
    from core.scheduler import connections
    c = connections.get("pop3")
    return {"server": os.getenv("MCXCOMM_POP_SERVER") or c.get("server") or "pop9.netcore.co.in",
            "port": int(os.getenv("MCXCOMM_POP_PORT") or c.get("port") or 995),
            "user": os.getenv("MCXCOMM_POP_USER") or c.get("username") or "",
            "password": os.getenv("MCXCOMM_POP_PASS") or c.get("password") or ""}


def _mail_for_day(pop, count, target: dt.date, scan_start=dt.time(9, 30)):
    """The V2 mailbox search (binary search on Date headers) for ONE day -> (name, bytes) or None."""
    import email
    import poplib
    from email import policy
    from email.parser import BytesParser
    from email.utils import parseaddr

    def parse(lines):
        return BytesParser(policy=policy.default).parsebytes(b"\r\n".join(lines))

    def head(i):
        _r, lines, _o = pop.top(i, 0)
        msg = parse(lines)
        dh = msg["Date"]
        if not dh:
            return None, msg
        try:
            d = email.utils.parsedate_to_datetime(dh)
        except (TypeError, ValueError):
            return None, msg
        if d.tzinfo is None:
            d = d.replace(tzinfo=IST)
        return d.astimezone(IST), msg

    def dt_at(i):
        order = []
        for step in range(0, 4):
            if i + step <= count:
                order.append(i + step)
            if step and i - step >= 1:
                order.append(i - step)
        for j in order:
            try:
                d, _ = head(j)
            except (TimeoutError, OSError, poplib.error_proto):
                continue
            if d is not None:
                return d
        return None

    bound = dt.datetime.combine(target, scan_start, tzinfo=IST)
    lo, hi = 1, count + 1
    while lo < hi:
        mid = (lo + hi) // 2
        d = dt_at(mid)
        if d is None:
            hi = mid
            continue
        if d < bound:
            lo = mid + 1
        else:
            hi = mid
    for i in range(lo, count + 1):
        _check_cancel()
        try:
            d, msg = head(i)
        except (TimeoutError, OSError, poplib.error_proto):
            continue
        if d is None:
            continue
        if d.date() > target:
            break
        if d.date() < target:
            continue
        if (msg["Subject"] or "").strip().lower() != "mcx active client details":
            continue
        if parseaddr(msg["From"] or "")[1].strip().lower() != "noreply@mastertrust.co.in":
            continue
        try:
            _r, full, _o = pop.retr(i)
        except (TimeoutError, OSError, poplib.error_proto):
            continue
        for part in parse(full).iter_attachments():
            fn = part.get_filename() or ""
            if fn.upper().startswith("MCX_ACTIVE_DETAILS") and fn.lower().endswith(".csv"):
                return fn, part.get_payload(decode=True), d
    return None


def step_mail(base: Path) -> dict:
    """MCX_ACTIVE_DETAILS_<ddmmyyyy>.csv from the mailbox. Day: today's mail if it has arrived, else the latest earlier one."""
    if any(is_modified_today(f) for f in _glob(base, MCX_PATTERNS)):
        return {"ok": True, "skipped": True, "msg": "already in the folder"}
    import poplib
    import ssl
    cfg = pop_cfg()
    if not (cfg["user"] and cfg["password"]):
        return {"ok": False, "msg": "mailbox login is not set - use the Credentials button (MCX ACTIVE mailbox)"}
    mode = get_settings().get("mail_day", "auto")
    today = dt.datetime.now(IST).date()
    days = {"today": [today], "yesterday": [today - dt.timedelta(days=1)],
            "auto": [today - dt.timedelta(days=n) for n in range(0, 6)]}[mode]
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")                        # pop9.netcore.co.in uses a small DH key
    pop = poplib.POP3_SSL(cfg["server"], cfg["port"], context=ctx, timeout=30)
    try:
        pop.user(cfg["user"])
        pop.pass_(cfg["password"])
        count, _size = pop.stat()
        log(f"Mailbox has {count} message(s).")
        for d in days:
            start = dt.time(9, 30) if d == today else dt.time(0, 0)
            hit = _mail_for_day(pop, count, d, start)
            if hit:
                name, data, when = hit
                part = base / (name + ".part")
                part.write_bytes(data)
                part.replace(base / name)
                return {"ok": True, "msg": f"{name} (mail of {d:%d-%m-%Y} {when:%H:%M})"}
            log(f"no 'MCX ACTIVE CLIENT DETAILS' mail for {d:%d-%m-%Y}")
        return {"ok": False, "msg": "no MCX ACTIVE CLIENT DETAILS mail found (" + ", ".join(f"{d:%d-%m}" for d in days) + ")"}
    finally:
        try:
            pop.quit()
        except Exception:
            pass


def step_tcs(base: Path) -> dict:
    """SearchResults (RSK136). PROBABLE's TCS download saves it into this folder; if today's is here we skip TCS entirely."""
    def present():
        return have_today(SEARCH_PATTERNS, "mtime", base)
    if present():
        return {"ok": True, "skipped": True, "msg": "already in the folder (saved by PROBABLE's TCS download)"}
    from core import tcs_session_lock as L
    t0, noted = time.time(), 0
    wait_max = int(os.getenv("MCXCOMM_TCS_WAIT_MIN", "20")) * 60
    while True:                                                   # someone else is on the TCS login (PROBABLE?) - it may bring the file
        _check_cancel()
        h = L.holder()
        if not h or h.get("owner") == "mcxcomm":
            break
        if present():
            return {"ok": True, "msg": f"saved by {L.NAMES.get(h.get('owner'), h.get('owner'))}"}
        if time.time() - t0 > wait_max:
            break
        if time.time() - noted > 60:
            log(f"The TCS login is used by {L.NAMES.get(h.get('owner'), h.get('owner'))} - waiting for it (it may save the search result)...")
            noted = time.time()
        time.sleep(5)
    if present():
        return {"ok": True, "msg": "saved by the TCS download that was running"}
    script = REPO / "core" / "pipelines" / "tcs_rsk136.py"
    env = os.environ.copy()
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "MCX_TOKEN_MODE": "auto",
                "MCX_DEPOSIT_CALLER": "mcxcomm"})
    log("Downloading the TCS search result (RSK136) myself...")
    rc, tail = _run_child([_python(), str(script), "--out", str(base)], env, int(os.getenv("MCXCOMM_TCS_TIMEOUT_MIN", "25")) * 60)
    if present():
        return {"ok": True, "msg": "downloaded RSK136"}
    fail = next((ln for ln in reversed(tail) if ln.startswith("[") or "fail" in ln.lower()), "")
    return {"ok": False, "msg": f"TCS search result not downloaded (exit {rc}). {fail[:160]}"}


STEPS = {"risk": step_risk, "kambala": step_kambala, "exclusion": step_exclusion, "nmass": step_nmass, "korp": step_korp,
         "mail": step_mail, "tcs": step_tcs}                      # tests replace these
PARALLEL = ("risk", "kambala", "exclusion", "nmass", "korp", "mail")


def _do_step(name: str, base: Path) -> dict:
    _set_step(name, "running")
    try:
        r = STEPS[name](base)
    except Cancelled:
        _set_step(name, "stopped", "Stopped.")
        return {"ok": False, "msg": "Stopped."}
    except Exception as e:                                       # noqa: BLE001
        r = {"ok": False, "msg": f"{type(e).__name__}: {e}"}
    _set_step(name, "skipped" if r.get("skipped") else ("ok" if r.get("ok") else "failed"), r.get("msg", ""))
    log(f"{STEP_LABEL[name]}: {'OK' if r.get('ok') else 'FAILED'} - {r.get('msg', '')}")
    return r


# ───────────────────────────── failure alert ─────────────────────────────
def alert_numbers():
    from core.scheduler import failure_alert
    raw = get_settings().get("alert_numbers") or ""
    if not raw.strip():
        try:
            from core import ms15_auto
            raw = ms15_auto.get_settings().get("alert_numbers", "")
        except Exception:
            raw = ""
    return failure_alert.parse_numbers(raw)


def send_alert(headline, detail, sender=None) -> dict:
    """WhatsApp 'FAILURE ALERT' picture. Never raises. Falls back to the common alert numbers when none are set here."""
    rec = {"sent": False, "reason": ""}
    try:
        numbers = alert_numbers()
        if not numbers:
            try:
                from core.scheduler import connections, failure_alert
                numbers = failure_alert.parse_numbers(connections.get("whatsapp").get("alert_numbers", ""))
            except Exception:
                numbers = []
        if not numbers:
            rec["reason"] = "no alert number set"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        if sender is not None:
            sender(numbers, headline, detail)
            rec.update(sent=True, reason="sent")
            return rec
        from core.scheduler import connections, failure_alert, helpers as H, paths
        c = connections.get("whatsapp")
        if not c.get("auth_token"):
            rec["reason"] = "WhatsApp auth token is not set"
            log("WhatsApp failure alert NOT sent: " + rec["reason"])
            return rec
        now = dt.datetime.now()
        img = failure_alert.render_image(paths.sched_root() / "alerts" / f"mcxcomm_{int(time.time())}.png",
                                         process=NAME, schedule="Auto run", failed_at=now.strftime("%d-%b-%Y %H:%M:%S"),
                                         started="-", trigger="Auto run", attempt_text="final", headline=headline,
                                         detail=detail, run_id="MCXCOMM", kind="FAILED")

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
        log("WhatsApp failure alert " + (rec["reason"]))
    except Exception as e:                                       # noqa: BLE001
        rec["reason"] = f"{type(e).__name__}: {e}"
        log("WhatsApp failure alert error: " + rec["reason"])
    return rec


# ───────────────────────────── the run ─────────────────────────────
def is_running() -> bool:
    return _RUN_LOCK.locked()


def needs_password() -> bool:
    """A manual run asks for the console password only when today's run already SUCCEEDED."""
    return read_state().get("status") == "done"


def run(trigger="manual", by="", alert=None) -> dict:
    """The whole job. Raises Busy if one is already running. Failure -> status 'failed' + WhatsApp alert (always, auto or manual)."""
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy("MCX COMM LIMIT is already running.")
    alert = alert or send_alert
    base = root()
    _ACTIVE["cancel"] = False
    _ACTIVE["log_file"] = _open_run_log(trigger, by)
    st0 = read_state()
    _update(status="running", started=_iso(), finished="", error="", trigger=trigger, by=by, steps={},
            auto_tried=bool(st0.get("auto_tried")) or trigger in ("probable", "auto"), mailed=False, uploaded=False)
    result = {"ok": False}
    try:
        log(f"=== {NAME} started ({trigger}{', ' + by if by else ''}) ===")
        # 1) the inputs that are independent of each other, in parallel
        outs, threads = {}, []
        for n in PARALLEL:
            th = threading.Thread(target=lambda n=n: outs.__setitem__(n, _do_step(n, base)), name=f"mcxcomm-{n}", daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        _check_cancel()
        # 2) TCS search result (waits for / reuses PROBABLE's download)
        outs["tcs"] = _do_step("tcs", base)
        _check_cancel()
        bad = [f"{STEP_LABEL[k]}: {v.get('msg', '')}" for k, v in outs.items() if not v.get("ok")]
        # a failed download is only fatal if the file is really missing - the check below decides
        # 3) pre-run check -> calculation
        _set_step("calc", "running")
        try:
            pre_run_check(base)
        except PreRunError as e:
            raise PreRunError(f"{e}" + (f"  [{'; '.join(bad)}]" if bad else ""))
        res = calculate(base)
        _set_step("calc", "ok", f"{res['rows']} clients written")
        _update(rows=res["rows"], eligible=res["eligible"])
        _check_cancel()
        # 4) e-mail
        _set_step("email", "running")
        send_email(res["txt"])
        _set_step("email", "ok", "sent")
        _update(mailed=True)
        # 5) Kambala FTP (a problem here never stops the backup, as before)
        _set_step("ftp", "running")
        ok_ftp = upload_to_kambala_ftp(res["txt"])
        _set_step("ftp", "ok" if ok_ftp else "failed", "uploaded" if ok_ftp else "not uploaded - see the log")
        _update(uploaded=ok_ftp)
        # 6) backup and clean
        _set_step("backup", "running")
        dest = backup_and_cleanup(base)
        _set_step("backup", "ok", str(dest))
        _update(status="done", finished=_iso(), error="")
        log(f"=== {NAME} finished ===")
        result = {"ok": True, "rows": res["rows"], "ftp": ok_ftp}
        if not ok_ftp and get_settings().get("ftp_enabled", True):
            alert("The limit file was e-mailed but the Kambala FTP upload failed.", "Upload the file to Kambala by hand (the file is in the backup folder).")
    except Cancelled:
        _update(status="stopped", finished=_iso(), error="Stopped.")
        log("Stopped.")
        result = {"ok": False, "stopped": True}
    except Exception as e:                                       # noqa: BLE001
        msg = f"{type(e).__name__}: {e}" if not isinstance(e, PreRunError) else str(e)
        _update(status="failed", finished=_iso(), error=msg[:600])
        log("FAILED: " + msg)
        rec = alert("The MCX COMM LIMIT run failed.", msg[:600])
        _update(alert_sent=bool(rec.get("sent")), alert_reason=rec.get("reason", ""))
        result = {"ok": False, "error": msg}
    finally:
        f = _ACTIVE.get("log_file")
        _ACTIVE["log_file"] = None
        if f:
            try:
                f.close()
            except Exception:
                pass
        _RUN_LOCK.release()
    return result


def request_run(by="", password=None, background=True) -> dict:
    """The Run button. Needs the password only when today's run is already done."""
    if is_running():
        raise Busy("MCX COMM LIMIT is already running.")
    if needs_password():
        from core import short_allocation
        if not password or not short_allocation.check_password(password):
            return {"ok": False, "need_password": True, "msg": "Today's run already succeeded - enter the password to run it again."}
    if background:
        threading.Thread(target=lambda: run("manual", by), name="mcxcomm-manual", daemon=True).start()
        return {"ok": True, "started": True}
    return run("manual", by)


_FETCH_LOCK = threading.Lock()
FETCH_KINDS = {"tcs": ("tcs",), "mail": ("mail",), "korp": ("korp",), "nmass": ("nmass",), "risk": ("risk",), "kambala": ("kambala",)}


def fetch_one(what: str, by="") -> dict:
    """The single-download buttons (TCS search result / mail file / KORP file / NMASS files ...). Not while a full run is going."""
    if what not in FETCH_KINDS:
        raise ValueError("unknown download")
    if is_running():
        raise Busy("A full run is going - wait for it or Stop it.")
    if not _FETCH_LOCK.acquire(blocking=False):
        raise Busy("A download is already going.")

    def go():
        _ACTIVE["cancel"] = False
        try:
            log(f"{STEP_LABEL[what]}: started by {by or 'user'}")
            _do_step(what, root())
        finally:
            _FETCH_LOCK.release()
    threading.Thread(target=go, name=f"mcxcomm-fetch-{what}", daemon=True).start()
    return {"ok": True, "started": what}


def stop_running(by="") -> bool:
    if not is_running() and not _FETCH_LOCK.locked():
        return False
    _ACTIVE["cancel"] = True
    for p in list(_ACTIVE["procs"]):
        _kill(p)
    log(f"Stop requested by {by or 'user'}.")
    return True


# ───────────────────────────── the PROBABLE link ─────────────────────────────
def auto_due() -> bool:
    st = read_state()
    return bool(auto_enabled() and not st.get("auto_tried") and st.get("status") not in ("running", "done") and not is_running())


def hook_probable() -> bool:
    """Called when Run is clicked on PROBABLE: 10 s later this card starts by itself (once per day, auto on)."""
    if not auto_due():
        log("PROBABLE run seen - auto start skipped (" + ("auto is off" if not auto_enabled() else "already tried / running / done today") + ").")
        return False
    delay = get_settings()["delay_s"]
    _update(auto_tried=True, auto_scheduled=_iso())               # marked now: two Run clicks can never start two auto runs

    def go():
        time.sleep(delay)
        try:
            run("probable", "auto (after PROBABLE run)")
        except Busy:
            pass
        except Exception as e:                                   # noqa: BLE001
            log(f"auto start error: {type(e).__name__}: {e}")
    threading.Thread(target=go, name="mcxcomm-auto", daemon=True).start()
    log(f"PROBABLE run seen - MCX COMM LIMIT starts in {delay} s.")
    return True


# ───────────────────────────── files / status ─────────────────────────────
def list_files(sub: str = "") -> list:
    base = root() if not sub else backup_root() / sub
    out = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".part"):
                out.append({"name": p.name, "size": p.stat().st_size,
                            "time": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S")})
    return out


def list_backup_dates() -> list:
    b = backup_root()
    if not b.is_dir():
        return []
    return sorted([p.name for p in b.iterdir() if p.is_dir()], key=lambda s: dt.datetime.strptime(s, "%d-%m-%Y") if re.match(r"\d\d-\d\d-\d{4}$", s) else dt.datetime.min, reverse=True)


def file_path(name: str, sub: str = "") -> Path:
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("bad file name")
    p = (root() if not sub else backup_root() / _check_date(sub)) / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return p


def _check_date(s: str) -> str:
    if not re.match(r"^\d{2}-\d{2}-\d{4}$", s or ""):
        raise ValueError("date must be dd-mm-yyyy")
    return s


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
    z = Path(tempfile.gettempdir()) / f"MCX_COMM_LIMIT_{a:%d%m%Y}_{b:%d%m%Y}_{int(time.time())}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dates_between(a, b):
            for f in list_files(dstr(d)):
                zf.write(backup_root() / dstr(d) / f["name"], f"{dstr(d)}/{f['name']}")
    return z


def accept_upload(name: str, data: bytes, by="") -> dict:
    """Upload files: stored under their own name in the data folder (EXCLUSION.xlsx replaces the permanent list)."""
    name = os.path.basename(name or "")
    if not name or name.startswith(".") or name.lower().endswith((".py", ".bat", ".exe", ".part")):
        raise ValueError("this file name is not allowed")
    if not data:
        raise ValueError("the file is empty")
    if name.upper().startswith("RISK_REPORT"):
        raise ValueError(f"{name}: the risk report is read from COMMON FILES - put it there, not here")
    base = root()
    part = base / (name + ".part")
    part.write_bytes(data)
    part.replace(base / name)
    log(f"Uploaded {name} ({len(data):,} bytes) by {by or 'user'}")
    return {"ok": True, "name": name, "size": len(data)}


def status() -> dict:
    st = read_state()
    running = is_running()
    s = st.get("status", "idle")
    if s == "running" and not running:
        s = "failed"
        st["error"] = st.get("error") or "Interrupted (the server restarted while it was running)."
    set_ = get_settings()
    steps = st.get("steps", {})
    return {"name": NAME, "date": st.get("date"), "status": "running" if running else s, "running": running,
            "fetching": _FETCH_LOCK.locked(),
            "started": st.get("started", ""), "finished": st.get("finished", ""), "error": st.get("error", ""),
            "trigger": st.get("trigger", ""), "by": st.get("by", ""), "rows": st.get("rows"), "eligible": st.get("eligible"),
            "mailed": bool(st.get("mailed")), "uploaded": bool(st.get("uploaded")),
            "alert_sent": bool(st.get("alert_sent")), "alert_reason": st.get("alert_reason", ""),
            "auto": bool(set_["auto"]), "auto_tried": bool(st.get("auto_tried")), "needs_password": s == "done" and not running,
            "settings": {k: set_[k] for k in ("auto", "delay_s", "ftp_enabled", "mail_day", "alert_numbers")},
            "steps": [dict(key=k, label=STEP_LABEL[k], **steps.get(k, {"status": "", "msg": ""}))
                      for k in ("risk", "kambala", "exclusion", "nmass", "korp", "mail", "tcs", "calc", "email", "ftp", "backup")],
            "inputs": input_status(), "folder": str(root()), "log": recent_log(60)}
