"""
core/ctcl_readiness.py — Pre-run file readiness check for CTCL vs NMASS pipeline.

Returns a JSON-serialisable dict matching the /sa/readiness pattern:
  {folder, common_folder, ready, items: [{key, label, example, found, ok}]}

Files checked:
  BASE_DIR (CTCL_NMASS_FOLDER, default D:\\CTCL VS NMASS):
    • CM_ClientLevelCollaterals_*.csv/xlsx    DDMMYYYY — downloaded by NMASS script
    • FO_ClientLevelCollaterals_*.csv/xlsx    DDMMYYYY — downloaded by NMASS script
    • CO_ClientLevelCollaterals_*.csv/xlsx    DDMMYYYY — downloaded by NMASS script
    • ClientNetEffectiveDeposits_*.csv/xlsx   DDMMYYYY — downloaded by NMASS script
    • *.xlsx containing YYYYMMDD (excl. Update Limits)  — MCX 90% file (TCS BANCS)
    • Update Limits*.xlsx                     DDMMYYYY  + modified within 30 min (manual)
  COMMON_DIR (SA_COMMON_DIR, default C:\\COMMON FILES):
    • RISK_REPORT_*.csv                       DDMMYYYY
"""
import os
import glob
import re
from datetime import datetime


def check_ctcl_readiness() -> dict:
    """Same check, asked again (up to 4 times, 0.5 s apart) before reporting a file as missing."""
    from core.file_probe import settle
    r = settle(lambda: (lambda x: x if x.get("ready") else None)(_check_ctcl_readiness_once()), tries=4, delay=0.5)
    return r or _check_ctcl_readiness_once()


def _check_ctcl_readiness_once() -> dict:
    """Check whether all input files required by run_ctcl_vs_nmass() are present."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    base_dir   = os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    common_dir = os.getenv("SA_COMMON_DIR",      r"C:\COMMON FILES")
    today_dmy  = datetime.now().strftime("%d%m%Y")
    today_ymd  = datetime.now().strftime("%Y%m%d")

    def _has_today(fname: str, fmt: str) -> bool:
        """Return True if fname contains today's date in the given format."""
        m8 = re.search(r"\d{8}", fname)
        if not m8:
            return False
        ds = m8.group(0)
        if fmt == "DDMMYYYY":
            return ds == today_dmy
        if fmt == "YYYYMMDD":
            return ds == today_ymd
        return False

    def _modified_within(fpath: str, minutes: int = 30) -> bool:
        try:
            age_s = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(fpath))).total_seconds()
            return age_s <= minutes * 60
        except Exception:
            return False

    # The MCX margin file depends on the "MCX file" choice of the CTCL box (core/ctcl_mcx_source.py):
    # the MCX 90% file (random name stamped YYYYMMDD) or the MCX Deposit file (the console's own download is
    # MCX_DEPOSIT_HH.MM.xlsx - no date in the name, so it counts when it was saved today).
    try:
        from core import ctcl_mcx_source as _mcxsrc
        _source = _mcxsrc.get_source()
    except Exception:
        _source = "mcx90"
    if _source == "deposit":
        _mcx_check = {
            "key":      "MCX_DEPOSIT_FILE",
            "label":    "MCX Deposit File (TCS BANCS)",
            "example":  "MCX Deposit file (MCX_DEPOSIT_hh.mm.xlsx, or <random>-" + today_ymd + "*.xlsx)",
            "patterns": ["*.xlsx"],
            "folder":   base_dir,
            "date_fmt": "YYYYMMDD",
            "mtime_today_prefix": "MCX_DEPOSIT_",
            "exclude":  ["update limits"],   # case-insensitive
        }
    else:
        _mcx_check = {
            "key":      "MCX_90_FILE",
            "label":    "MCX 90% Margin File (TCS BANCS)",
            "example":  f"<TCS BANCS random name>_{today_ymd}*.xlsx",
            "patterns": ["*.xlsx"],
            "folder":   base_dir,
            "date_fmt": "YYYYMMDD",
            "exclude":  ["update limits"],   # case-insensitive
        }

    def _saved_today(fpath: str) -> bool:
        try:
            return datetime.fromtimestamp(os.path.getmtime(fpath)).date() == datetime.now().date()
        except Exception:
            return False

    checks = [
        {
            "key":      "CM_CLIENT_LEVEL",
            "label":    "CM Collaterals",
            "example":  f"CM_ClientLevelCollaterals_{today_dmy}.csv",
            "patterns": ["CM_ClientLevelCollaterals_*.csv", "CM_ClientLevelCollaterals_*.xlsx"],
            "folder":   base_dir,
            "date_fmt": "DDMMYYYY",
        },
        {
            "key":      "FO_CLIENT_LEVEL",
            "label":    "FO Collaterals",
            "example":  f"FO_ClientLevelCollaterals_{today_dmy}.csv",
            "patterns": ["FO_ClientLevelCollaterals_*.csv", "FO_ClientLevelCollaterals_*.xlsx"],
            "folder":   base_dir,
            "date_fmt": "DDMMYYYY",
        },
        {
            "key":      "CO_CLIENT_LEVEL",
            "label":    "CO Collaterals",
            "example":  f"CO_ClientLevelCollaterals_{today_dmy}.csv",
            "patterns": ["CO_ClientLevelCollaterals_*.csv", "CO_ClientLevelCollaterals_*.xlsx"],
            "folder":   base_dir,
            "date_fmt": "DDMMYYYY",
        },
        {
            "key":      "CLIENT_NET_DEPOSITS",
            "label":    "Client Net Effective Deposits",
            "example":  f"ClientNetEffectiveDeposits_{today_dmy}.csv",
            "patterns": ["ClientNetEffectiveDeposits_*.csv", "ClientNetEffectiveDeposits_*.xlsx"],
            "folder":   base_dir,
            "date_fmt": "DDMMYYYY",
        },
        _mcx_check,
        {
            "key":               "UPDATE_LIMITS",
            "label":             "Update Limits (manual, <30 min old)",
            "example":           f"Update Limits*{today_dmy}*.xlsx",
            "patterns":          ["Update Limits*.xlsx"],
            "folder":            base_dir,
            "date_fmt":          "DDMMYYYY",
            "check_modified_min": 30,
        },
        {
            "key":      "RISK_REPORT",
            "label":    "RISK REPORT (Common Files)",
            "example":  f"RISK_REPORT_{today_dmy}.csv",
            "patterns": ["RISK_REPORT_*.csv"],
            "folder":   common_dir,
            "date_fmt": "DDMMYYYY",
        },
    ]

    items = []
    all_ok = True

    for chk in checks:
        folder = chk["folder"]
        found_path = None

        for pat in chk["patterns"]:
            candidates = glob.glob(os.path.join(folder, pat))

            # Apply exclude filter (e.g. remove "Update Limits" when matching *.xlsx)
            if "exclude" in chk:
                candidates = [
                    c for c in candidates
                    if not any(x in os.path.basename(c).lower() for x in chk["exclude"])
                ]

            # Filter to today's date only
            candidates = [
                c for c in candidates
                if _has_today(os.path.basename(c), chk["date_fmt"])
                or (chk.get("mtime_today_prefix")
                    and os.path.basename(c).upper().startswith(chk["mtime_today_prefix"])
                    and _saved_today(c))
            ]

            if candidates:
                # Pick the most recently modified file
                found_path = max(candidates, key=os.path.getmtime)
                break

        ok = found_path is not None

        # Update Limits: also must have been modified within 30 min (i.e. today's fresh upload)
        if ok and chk.get("check_modified_min"):
            if not _modified_within(found_path, chk["check_modified_min"]):
                ok = False
                found_path = None   # show as missing so the label in the UI is clear

        if not ok:
            all_ok = False

        items.append({
            "key":     chk["key"],
            "label":   chk["label"],
            "example": chk["example"],
            "found":   os.path.basename(found_path) if found_path else None,
            "ok":      ok,
        })

    return {
        "folder":        base_dir,
        "common_folder": common_dir,
        "ready":         all_ok,
        "items":         items,
    }
