"""MASTER FILE / SA / EODSAR cards - the hand-run scripts of one trading day, run from the console.

Three cards, seven processes (each process = one script in core/day_scripts/, unchanged except that its folders and
e-mail settings are patched in at run time):

    MASTER FILE   master_file -> live_allocation_report -> mcx_intrasar       (output of one is the input of the next)
    SA            sa_cm, sa_fo
    EODSAR        mcx_eodsar, ncdex_eodsar

Where things live (<data> = the console's data folder, e.g. D:\\CONSOLE\\data):
    inputs   never copied to a "process folder": they are READ from where they already are
             76 FILES DOWNLOADER day folder, LIVE ALLOCATION CAPTURE folders 1-8, COMMON FILES (RISK_REPORT),
             <data>/MANUAL FILES/<dd-mm-yyyy>/ (RESPONSE_2_mcx.csv, Allocation_Detail_<ddmmyyyy>.csv - uploaded on the card)
    outputs  <data>/<CARD>/<dd-mm-yyyy>/<PROCESS>/        (what the script produced; it is also e-mailed by the script)
    a run    works in a temporary folder <data>/<CARD>/_work/<process>_<time>/ - the inputs are copied in there, the script
             runs there (its own "backup and delete" step only touches that copy) and the folder is removed afterwards.
             The day folder is never touched.

The run itself is a child process (python -m core.daycards --card .. --procs .. --date ..): the server starts it, shows its
log and can stop it.  Run logs: logs/daycard_<card>/<process>__<dd-mm-yyyy>__<time>.log
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent / "day_scripts"
MEMBER, MCX_USER, NCDEX_UCC = "06436", "56720", "01292"
MANUAL_FOLDER = "MANUAL FILES"
RESPONSE_NAME = "RESPONSE_2_mcx.csv"


# ───────────────────────────── paths / dates ─────────────────────────────
def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def data_root() -> Path:
    v = os.getenv("DAYCARDS_DIR", "").strip()
    if v:
        return Path(v)
    try:
        from core.scheduler import paths
        return Path(paths.data_root())
    except Exception:
        return REPO / "data"


def parse_date(s: Optional[str]) -> dt.date:
    from core import files70 as F
    return F.parse_date(s)


def default_date() -> dt.date:
    from core import files70 as F
    return F.default_date()


def card_dir(card: str) -> Path:
    return data_root() / CARDS[card]["folder"]


def out_dir(card: str, proc: str, d: dt.date) -> Path:
    return card_dir(card) / dstr(d) / PROCS[proc].label


def manual_dir(d: dt.date) -> Path:
    return data_root() / MANUAL_FOLDER / dstr(d)


def log_dir(card: str) -> Path:
    return REPO / "logs" / f"daycard_{card}"


def _state_dir(card: str) -> Path:
    return card_dir(card) / "_state"


# ───────────────────────────── definitions ─────────────────────────────
@dataclass
class Need:
    group: str
    label: str
    rx: str                      # matched (full, case-insensitive) against the staged relative path, e.g. "1/CM_Client.."
    optional: bool = False


@dataclass
class Proc:
    key: str
    label: str
    card: str
    script: str
    needs: Callable[[dt.date], List[Need]]
    stage: List[str]                             # what is staged, see _stage()
    outputs: List[str]                           # regexes of the files the script produces
    patch: dict                                  # how folders and the e-mail are patched, see _patch()
    to_default: str = "rms_del@mastertrust.co.in"
    cc_default: str = ""
    note: str = ""


def _ymd(d):
    return d.strftime("%Y%m%d")


def _dmy(d):
    return d.strftime("%d%m%Y")


def _n(group, label, rx, opt=False):
    return Need(group, label, rx, opt)


def _peaks(d):
    y = _ymd(d)
    out = [_n("MCX peak files", f"MCX peak {i:02d}", rf"MCX_PeakMargin{MCX_USER}_{y}_{i:02d}\.csv") for i in range(1, 9)]
    out += [_n("NCDEX peak files", f"NCDEX peak {i:02d}", rf"NCCL_MARGIN_REP_{NCDEX_UCC}_{y}_{i:02d}\.csv") for i in range(1, 9)]
    return out


def _ncdex_end(d):
    return [_n("NCDEX end file", "NCDEX end file (01292_MGN)", rf"{NCDEX_UCC}_MGN_{_dmy(d)}\d{{6}}\.csv")]


def _sa01(d, fo=True, cm=True):
    m, s = MEMBER, _dmy(d)
    out = []
    if fo:
        out += [_n("FO SA01 files", f"F_SA01 i0{i}", rf"F_SA01_P_{m}_{s}_i0{i}\.lis") for i in range(1, 5)]
        out += [_n("FO SA01 files", "F_SA01 end file", rf"F_SA01_{m}_{s}\.lis")]
    if cm:
        out += [_n("CM SA01 files", f"C_SA01 i0{i}", rf"C_SA01_P_{m}_{s}_i0{i}\.lis") for i in range(1, 5)]
        out += [_n("CM SA01 files", "C_SA01 end file", rf"C_SA01_{m}_{s}\.lis")]
    return out


def needs_master(d):
    y, m = _ymd(d), MEMBER
    out = []
    for t in ("1100", "1230", "1430", "1530"):
        out.append(_n("NSE CM", f"CM peak {t}", rf"Margin_NCL_CM_0_CM_{m}_{y}_P_{t}\.(csv|xlsx)"))
    out.append(_n("NSE CM", "C_MG13 end file", rf"C_MG13_{m}_{_dmy(d)}\.lis"))
    for t in ("1100", "1230", "1430", "1530"):
        out.append(_n("NSE FO", f"FO peak {t}", rf"Margin_NCL_FO_0_CM_{m}_{y}_P_{t}\.(csv|xlsx)"))
    out.append(_n("NSE FO", "FO final (F_0000)", rf"Margin_NCL_FO_0_CM_{m}_{y}_F_0000\.(csv|xlsx)"))
    out += _peaks(d) + _ncdex_end(d)
    for t in ("1100", "1230", "1430", "1530", "1700", "1900", "2030", "2230"):
        out.append(_n("NSE CO (optional)", f"CO peak {t}", rf"Margin_NCL_CO_0_CM_{m}_{y}_P_{t}\.(csv|xlsx)", True))
    out.append(_n("NSE CO (optional)", "CO final (F_0000)", rf"Margin_NCL_CO_0_CM_{m}_{y}_F_0000\.(csv|xlsx)", True))
    return out


def needs_live(d):
    s = _dmy(d)
    out = [_n("Inputs from before", "Master file (from MASTER FILE process)", rf"{s}_MASTER\.xlsx"),
           _n("Inputs from before", "Risk report", rf"RISK_REPORT_{s}\.csv")]
    for i in range(1, 9):
        g = f"Live allocation folder {i}"
        out += [_n(g, "CM collaterals", rf"{i}/CM_ClientLevelCollaterals_{s}\d{{6}}\.(csv|txt)"),
                _n(g, "FO collaterals", rf"{i}/FO_ClientLevelCollaterals_{s}\d{{6}}\.(csv|txt)"),
                _n(g, "MCX web allocation", rf"{i}/MCX_Web_Allocation_{i:02d}.*"),
                _n(g, "NCDEX net deposits", rf"{i}/ClientNetEffectiveDeposits_{s}\.csv")]
    return out


def needs_intra(d):
    s, y = _dmy(d), _ymd(d)
    return ([_n("Inputs from before", "Master file (from MASTER FILE process)", rf"{s}_MASTER\.xlsx"),
             _n("Inputs from before", "Live allocation report (from LIVE ALLOCATION REPORT)", r"Report_Live_Allocation\.xlsx"),
             _n("MCX intra file", "MCX_INTRASAR", rf"MCX_INTRASAR_{MCX_USER}_{y}\.csv")]
            + _sa01(d) + _peaks(d) + _ncdex_end(d))


def _sa_common(d):
    s = _dmy(d)
    out = _sa01(d) + _peaks(d) + _ncdex_end(d)
    out += [_n("MCX web allocation", f"MCX web allocation {i:02d} (live folder {i})", rf"MCX_Web_Allocation_{i:02d}\.xlsx") for i in (1, 2, 3, 4, 8)]
    out += [_n("MCX end of day", "MCX_MARGIN (EOD peak file)", rf"MCX_MARGIN_{MCX_USER}_{_ymd(d)}\.csv"),
            _n("MCX end of day", "RESPONSE_2_mcx.csv (manual)", r"RESPONSE_2_mcx\.csv"),
            _n("Other", "EPI.xlsx", r"EPI\.xlsx"),
            _n("Other", "Risk report", rf"RISK_REPORT_{s}\.csv")]
    return out


def needs_sa_cm(d):
    return [_n("CM main file", "C_SA04 main file", rf"C_SA04.*_{_dmy(d)}\.lis")] + _sa_common(d)


def needs_sa_fo(d):
    return [_n("FO main file", "F_SA04 main file", rf"F_SA04.*_{_dmy(d)}\.lis")] + _sa_common(d)


def _cc02(d):
    s = _dmy(d)
    return [_n("CC02 files", f"{p}_CC02", rf"{p}_CC02_{MEMBER}_{s}\.csv") for p in ("C", "F", "X", "O")]


def needs_mcx_eodsar(d):
    s = _dmy(d)
    return ([_n("MCX end file", "MCX_MARGIN (EOD file)", rf"MCX_MARGIN_{MCX_USER}_{_ymd(d)}\.csv"),
             _n("Manual files", f"Allocation_Detail_{s}.csv (manual)", rf"Allocation_Detail_{s}\.csv")]
            + _cc02(d) + [_n("NCDEX net deposits", "ClientNetEffectiveDeposits (live folder 8)", rf"ClientNetEffectiveDeposits_{s}\.csv")])


def needs_ncdex_eodsar(d):
    s = _dmy(d)
    return ([_n("NCDEX end file", "NCDEX end file (01292_MGN)", rf"{NCDEX_UCC}_MGN_{s}\d{{6}}\.csv"),
             _n("Manual files", f"Allocation_Detail_{s}.csv (manual)", rf"Allocation_Detail_{s}\.csv"),
             _n("Manual files", "RESPONSE_2_mcx.csv (manual)", r"RESPONSE_2_mcx\.csv")] + _cc02(d))


# how each script is patched (names of the variables inside the script)
#   roots  : folders written in the script -> replaced by folders inside the work folder (work = the main folder)
#   smtp   : the variable names of the SMTP login;  to / cc : (variable name, "str" | "list")
_SA_RECIP = [("all_recipients = [receiver_email]", 'all_recipients = [_a.strip() for _a in receiver_email.split(",") if _a.strip()]'),
             ("all_recipients.append(cc_email)", 'all_recipients.extend(_a.strip() for _a in cc_email.split(",") if _a.strip())')]

PROCS: Dict[str, Proc] = {}
CARDS: Dict[str, dict] = {}


def _reg(p: Proc):
    PROCS[p.key] = p


_reg(Proc("master_file", "MASTER FILE", "master", "master_file.py", needs_master, ["day"], [r"\d{8}_MASTER\.xlsx"],
          dict(roots={r"C:\Rajeev Ji": "", r"C:\LIVE ALLOCATION": "_next/LIVE ALLOCATION", r"C:\MCX_INTRA_SAR": "_next/MCX_INTRA_SAR"},
               smtp=("smtp_server", "smtp_port", "sender_email", "password"), to=("to_emails", "list"), cc=("cc_emails", "list")),
          note="All the NSE / MCX / NCDEX peak files of the day -> the consolidated Master file (<date>_MASTER.xlsx), e-mailed."))
_reg(Proc("live_allocation_report", "LIVE ALLOCATION REPORT", "master", "live_allocation_report.py", needs_live,
          ["prev:master_file:\\d{8}_MASTER\\.xlsx", "risk", "live_tree"],
          [r"Report_Live_Allocation\.xlsx", r"PEAKWISE_LIVE_ALLOCATION\.xlsx"],
          dict(roots={r"C:\LIVE ALLOCATION": "", r"C:\MCX_INTRA_SAR": "_next/MCX_INTRA_SAR"},
               smtp=("smtp_server", "smtp_port", "sender_email", "password"), to=("receiver_email", "str"), cc=("cc_email", "str")),
          note="Master file + risk report + live allocation folders 1-8 -> Report_Live_Allocation and PEAKWISE_LIVE_ALLOCATION, e-mailed."))
_reg(Proc("mcx_intrasar", "MCX INTRASAR", "master", "mcx_intrasar.py", needs_intra,
          ["day", "prev:master_file:\\d{8}_MASTER\\.xlsx", "prev:live_allocation_report:Report_Live_Allocation\\.xlsx"],
          [r"MCX_Intra_Output\.xlsx", r"Report_Live_Allocation_Updated\.xlsx"],
          dict(roots={r"C:\MCX_INTRA_SAR": "", r"D:\MARGIN SHORTFALL TRACK": "_track"},
               smtp=("SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"), to=("to_emails", "list"), cc=("cc_emails", "list")),
          to_default="rajeev.kumar2@mastertrust.co.in,ashutosh.yadav@mastertrust.co.in,rms_del@mastertrust.co.in",
          cc_default="sandeep.kumar2@mastertrust.co.in,sumit.aggarwal@mastertrust.co.in",
          note="MCX_INTRASAR file + SA files + peaks + Master + live allocation report -> MCX_Intra_Output, e-mailed."))
_reg(Proc("sa_cm", "SA CM", "sa", "sa_cm.py", needs_sa_cm,
          ["day", "risk", "manual:RESPONSE_2_mcx\\.csv", "live_flat:1,2,3,4,8:MCX_Web_Allocation_\\d+\\.xlsx"], [r"SA_CM_Full\.xlsx"],
          dict(roots={r"C:\PRIYANKA\SA_01_CM": "", r"C:\PRIYANKA\BACKUP FILES AUTOMATED": "_backup", r"D:\MARGIN SHORTFALL TRACK": "_track"},
               smtp=("smtp_server", "smtp_port", "sender_email", "password"), to=("receiver_email", "str"), cc=("cc_email", "str"),
               text=_SA_RECIP),
          note="SA04 CM: SA files + peaks + MCX web allocation + EPI + risk report + RESPONSE_2_mcx -> SA_CM_Full, e-mailed."))
_reg(Proc("sa_fo", "SA FO", "sa", "sa_fo.py", needs_sa_fo,
          ["day", "risk", "manual:RESPONSE_2_mcx\\.csv", "live_flat:1,2,3,4,8:MCX_Web_Allocation_\\d+\\.xlsx"], [r"SA_FO_Full\.xlsx"],
          dict(roots={r"C:\PRIYANKA\SA_01_FO": "", r"C:\PRIYANKA\BACKUP FILES AUTOMATED": "_backup", r"D:\MARGIN SHORTFALL TRACK": "_track"},
               smtp=("smtp_server", "smtp_port", "sender_email", "password"), to=("receiver_email", "str"), cc=("cc_email", "str"),
               text=_SA_RECIP),
          note="SA04 FO: same inputs as SA CM -> SA_FO_Full, e-mailed."))
_reg(Proc("mcx_eodsar", "MCX EODSAR", "eodsar", "mcx_eodsar.py", needs_mcx_eodsar,
          ["day", "manual:Allocation_Detail_.*", "live_flat:8:ClientNetEffectiveDeposits_.*\\.csv"], [r"MCX_EODSAR_\d{8}\.xlsx"],
          dict(roots={r"C:\PRIYANKA\MCX_EODSAR": "", r"C:\PRIYANKA\BACKUP FILES AUTOMATED": "_backup", r"D:\MARGIN SHORTFALL TRACK": "_track"},
               smtp=("SMTP_SERVER", "SMTP_PORT", "SMTP_EMAIL", "SMTP_PASSWORD"), to=("TO_LIST", "list"), cc=("CC_LIST", "list")),
          note="MCX end-of-day file + Allocation_Detail + CC02 files + NCDEX net deposits -> MCX_EODSAR, e-mailed."))
_reg(Proc("ncdex_eodsar", "NCDEX EODSAR", "eodsar", "ncdex_eodsar.py", needs_ncdex_eodsar,
          ["day", "manual:Allocation_Detail_.*", "manual:RESPONSE_2_mcx\\.csv"], [r"NCDEX_\d{8}\.xlsx"],
          dict(roots={r"C:\PRIYANKA\NCDEX_EODSAR": "", r"C:\PRIYANKA\BACKUP FILES AUTOMATED": "_backup", r"D:\MARGIN SHORTFALL TRACK": "_track"},
               smtp=("SMTP_SERVER", "SMTP_PORT", "SMTP_EMAIL", "SMTP_PASSWORD"), to=("TO_LIST", "list"), cc=("CC_LIST", "list")),
          note="NCDEX end file + Allocation_Detail + RESPONSE_2_mcx + CC02 files -> NCDEX_<date>, e-mailed."))

CARDS.update({
    "master": {"title": "MASTER FILE", "folder": "MASTER FILE", "icon": "🧾", "accent": "#10b981",
               "procs": ["master_file", "live_allocation_report", "mcx_intrasar"], "manual": False,
               "desc": "Master file → Live allocation report → MCX intrasar. One click runs them in order."},
    "sa": {"title": "SA04 REPORTING", "folder": "SA04 REPORTING", "icon": "📑", "accent": "#6366f1", "procs": ["sa_cm", "sa_fo"], "manual": True,
           "desc": "SA04 reports for CM and FO (with the MCX end-of-day files)."},
    "eodsar": {"title": "EODSAR", "folder": "EODSAR", "icon": "🌙", "accent": "#ec4899", "procs": ["mcx_eodsar", "ncdex_eodsar"], "manual": True,
               "desc": "MCX and NCDEX end-of-day short-allocation reports."},
})

MANUAL_SLOTS = [
    {"key": "response", "label": "RESPONSE_2_mcx.csv", "used": "SA CM, SA FO, NCDEX EODSAR"},
    {"key": "alloc", "label": "Allocation_Detail_<ddmmyyyy>.csv", "used": "MCX EODSAR, NCDEX EODSAR"},
]


def email_env(proc: str) -> tuple:
    k = proc.upper()
    return f"DC_{k}_TO", f"DC_{k}_CC"


def sender_env(card: str) -> tuple:
    k = card.upper()
    return f"DC_{k}_SMTP_USER", f"DC_{k}_SMTP_PASS"


def apply_defaults():
    """To / CC defaults = what the hand-run scripts had (only fills what .env lacks)."""
    for p in PROCS.values():
        t, c = email_env(p.key)
        os.environ.setdefault(t, p.to_default)
        os.environ.setdefault(c, p.cc_default)


# ───────────────────────────── where the files are ─────────────────────────────
@dataclass
class Src:
    rel: str            # path inside the work folder (and name used for the checklist), posix
    path: Path
    kind: str           # day | live | risk | manual | prev
    zip_rel: str        # path inside a backup zip (<dd-mm-yyyy>/...)


def _files(p: Path) -> List[Path]:
    try:
        return sorted(x for x in p.iterdir() if x.is_file() and not x.name.startswith("."))
    except OSError:
        return []


def day_sources(d: dt.date) -> List[Src]:
    from core import files70 as F
    base, out = F.day_dir(d), []
    for _k, _l, sub in F.GROUPS:
        if sub:
            out += [Src(x.name, x, "day", f"{dstr(d)}/{sub}/{x.name}") for x in _files(base / sub)]
    out += [Src(x.name, x, "day", f"{dstr(d)}/{x.name}") for x in _files(base) if x.name.lower() == "epi.xlsx"]
    return out


def live_sources(d: dt.date) -> List[tuple]:
    """[(slot, Path)] of every file in LIVE ALLOCATION CAPTURE folders 1-8 of that day."""
    try:
        from core import live_allocation as L
        base = L.day_dir(d)
    except Exception:
        return []
    out = []
    for i in range(1, 9):
        out += [(i, x) for x in _files(base / str(i))]
    return out


def risk_sources(d: dt.date) -> List[Src]:
    try:
        from core import common_ftp
        folder = Path(common_ftp.common_dir())
    except Exception:
        folder = Path(os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES"))
    rx = re.compile(rf"^RISK_REPORT_{_dmy(d)}\.csv$", re.I)
    return [Src(x.name, x, "risk", f"{dstr(d)}/RISK REPORT/{x.name}") for x in _files(folder) if rx.match(x.name)]


def manual_sources(d: dt.date) -> List[Src]:
    return [Src(x.name, x, "manual", f"{dstr(d)}/{MANUAL_FOLDER}/{x.name}") for x in _files(manual_dir(d))]


def outputs_of(proc: str, d: dt.date) -> List[Path]:
    return _files(out_dir(PROCS[proc].card, proc, d))


def stage_plan(proc: str, d: dt.date) -> List[Src]:
    """Everything this process reads, with the name it must have inside the work folder."""
    p = PROCS[proc]
    out: List[Src] = []
    for tok in p.stage:
        kind, _, rest = tok.partition(":")
        if kind == "day":
            out += day_sources(d)
        elif kind == "risk":
            out += risk_sources(d)
        elif kind == "manual":
            rx = re.compile(rest, re.I)
            out += [s for s in manual_sources(d) if rx.fullmatch(s.rel)]
        elif kind == "live_tree":
            out += [Src(f"{i}/{x.name}", x, "live", f"{dstr(d)}/{i}/{x.name}") for i, x in live_sources(d)]
        elif kind == "live_flat":
            slots, _, rx_s = rest.partition(":")
            want, rx = {int(x) for x in slots.split(",")}, re.compile(rx_s, re.I)
            out += [Src(x.name, x, "live", f"{dstr(d)}/{i}/{x.name}") for i, x in live_sources(d) if i in want and rx.fullmatch(x.name)]
        elif kind == "prev":
            key, _, rx_s = rest.partition(":")
            rx = re.compile(rx_s, re.I)
            for x in outputs_of(key, d):
                if rx.fullmatch(x.name):
                    out.append(Src(x.name, x, "prev", f"{dstr(d)}/OUTPUT/{PROCS[key].label}/{x.name}"))
        else:
            raise ValueError(f"unknown stage '{tok}'")
    seen, uniq = set(), []
    for s in out:                                   # the same name twice: the first one wins
        k = s.rel.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


def _readable(rx: str) -> str:
    t = rx.replace("\\d{6}", "<hhmmss>").replace("\\d{8}", "<ddmmyyyy>").replace("(csv|xlsx)", "csv").replace("(csv|txt)", "csv")
    t = t.replace(".*", "*").replace("\\.", ".").replace("\\", "")
    return t


def _where(rx: str) -> str:
    if re.match(r"^\d/", rx):
        return f"LIVE ALLOCATION CAPTURE, folder {rx[0]}"
    low = rx.lower()
    if low.startswith("risk_report"):
        return "COMMON FILES (RISK_REPORT of that day)"
    if low.startswith(("response_2", "allocation_detail")):
        return "upload it with 'Manual files' on this card"
    if "_master" in low:
        return "output of MASTER FILE > MASTER FILE process (run it first)"
    if low.startswith("report_live"):
        return "output of MASTER FILE > LIVE ALLOCATION REPORT (run it first)"
    if low.startswith(("clientneteffective", "mcx_web_allocation")):
        return "LIVE ALLOCATION CAPTURE"
    return "76 FILES DOWNLOADER (day folder of that date)"


def check_needs(proc: str, d: dt.date, staged: Optional[List[Src]] = None) -> List[dict]:
    staged = staged if staged is not None else stage_plan(proc, d)
    names = [s.rel for s in staged]
    out = []
    for n in PROCS[proc].needs(d):
        rx = re.compile(n.rx, re.I)
        hit = next((x for x in names if rx.fullmatch(x)), None)
        out.append({"group": n.group, "label": n.label, "optional": n.optional, "present": bool(hit), "name": hit or "",
                    "expect": _readable(n.rx), "where": _where(n.rx), "upstream": _upstream(proc, n.rx)})
    return out


# a file that an EARLIER process of the same card makes (Master file, Live allocation report): it is not "missing" before that
# process has run - the card counts it as "comes from the earlier step" when that step itself has all its files.
_UPSTREAM = [(re.compile(r"_MASTER\\\.xlsx$"), "master_file"), (re.compile(r"^Report_Live_Allocation\\\.xlsx$"), "live_allocation_report")]


def _upstream(proc: str, rx: str) -> str:
    for r, key in _UPSTREAM:
        if r.search(rx) and key in PROCS and key != proc and PROCS[key].card == PROCS[proc].card:
            return key
    return ""


def would_pass(proc: str, d: dt.date, _seen=()) -> bool:
    """True when every required file is there, counting the files that an earlier process of the card will make."""
    if proc in _seen:
        return False
    for n in check_needs(proc, d):
        if n["present"] or n["optional"]:
            continue
        if not (n["upstream"] and would_pass(n["upstream"], d, _seen + (proc,))):
            return False
    return True


# ───────────────────────────── running one process ─────────────────────────────
class PatchError(Exception):
    pass


def _py(v: str) -> str:
    return repr(v)


def _smtp_values(card: str) -> Dict[str, str]:
    """The login the scripts send with: the card's own sender (card > Credentials) when both its e-mail and password are set,
    otherwise the shared login of Scheduler > Connections > E-mail sending."""
    u_k, p_k = sender_env(card)
    user, pw = os.getenv(u_k, "").strip(), os.getenv(p_k, "").strip()
    try:
        from core.scheduler import connections
        c = connections.get("smtp")
    except Exception:
        c = {}
    own = bool(user and pw)
    return {"server": c.get("server") or "smtp3.netcore.co.in", "port": str(c.get("port") or "587"),
            "user": user if own else (c.get("email") or ""), "pass": pw if own else (c.get("password") or "")}


def split_addrs(s: str) -> List[str]:
    return [a.strip() for a in re.split(r"[;,\s]+", s or "") if a.strip()]


def patch_script(proc: str, text: str, work: Path, values: Optional[dict] = None) -> str:
    """Folders -> the work folder, SMTP login and To / CC -> the console's settings. Every substitution must hit."""
    p = PROCS[proc]
    spec = p.patch
    v = values or {}
    # 1. folders (longest first so that a root is never replaced inside a longer one)
    for old in sorted(spec["roots"], key=len, reverse=True):
        new = str((work / spec["roots"][old]) if spec["roots"][old] else work)
        if old not in text:
            continue
        text = text.replace(old, new)
    for old in spec["roots"]:
        if old in text:
            raise PatchError(f"{p.label}: folder {old} is still in the script")
    # 2. SMTP login
    sv = v.get("smtp") or _smtp_values(p.card)
    s_server, s_port, s_user, s_pass = spec["smtp"]
    text = _set_str(text, s_server, sv["server"], p)
    text = _set_int(text, s_port, int(sv["port"] or 587), p)
    text = _set_str(text, s_user, sv["user"], p)
    text = _set_str(text, s_pass, sv["pass"], p)
    # 3. recipients
    t_k, c_k = email_env(proc)
    to = v.get("to") if v.get("to") is not None else os.getenv(t_k, p.to_default)
    cc = v.get("cc") if v.get("cc") is not None else os.getenv(c_k, p.cc_default)
    text = _set_addr(text, spec["to"], split_addrs(to), p)
    text = _set_addr(text, spec["cc"], split_addrs(cc), p)
    # 4. small text fixes (several recipients in one variable)
    for old, new in spec.get("text", []):
        if old not in text:
            raise PatchError(f"{p.label}: expected line not found in the script: {old}")
        text = text.replace(old, new)
    return text


def _set_str(text, name, value, p):
    rx = re.compile(rf"(?m)^([ \t]*){re.escape(name)}[ \t]*=[ \t]*(?:\"[^\"\n]*\"|'[^'\n]*')")
    if not rx.search(text):
        raise PatchError(f"{p.label}: '{name} = \"...\"' not found in the script")
    return rx.sub(lambda m: f"{m.group(1)}{name} = {_py(value)}", text)


def _set_int(text, name, value, p):
    rx = re.compile(rf"(?m)^([ \t]*){re.escape(name)}[ \t]*=[ \t]*\d+")
    if not rx.search(text):
        raise PatchError(f"{p.label}: '{name} = <number>' not found in the script")
    return rx.sub(lambda m: f"{m.group(1)}{name} = {int(value)}", text)


def _set_addr(text, spec, addrs, p):
    name, kind = spec
    if kind == "str":                     # the script wants ONE string; several addresses are joined with commas
        return _set_str(text, name, ", ".join(addrs), p)
    rx = re.compile(rf"(?ms)^([ \t]*){re.escape(name)}[ \t]*=[ \t]*\[.*?\]")
    if not rx.search(text):
        raise PatchError(f"{p.label}: '{name} = [...]' not found in the script")
    return rx.sub(lambda m: f"{m.group(1)}{name} = {_py(list(addrs))}", text)


def _copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def run_proc(card: str, proc: str, d: dt.date, log: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False,
             script_override: Optional[Path] = None, python: Optional[str] = None, values: Optional[dict] = None) -> dict:
    """Run ONE process for the date. -> {"ok", "outputs": [...], "missing": [...], "rc"}"""
    p = PROCS[proc]
    t0 = time.time()
    staged = stage_plan(proc, d)
    needs = check_needs(proc, d, staged)
    miss = [n for n in needs if not n["present"] and not n["optional"]]
    log(f"===== {p.label} - trade date {dstr(d)} =====")
    log(f"PRE-CHECK: looking for {len(needs)} file(s) ({sum(1 for n in needs if n['optional'])} optional) ...")
    if miss:
        log("")
        log(f"PRE-CHECK FAILED - {len(miss)} required file(s) NOT FOUND. The process was NOT run and nothing was e-mailed.")
        last = None
        for n in miss:
            if n["group"] != last:
                log(f"  [{n['group']}]")
                last = n["group"]
            log(f"    - {n['label']}")
            log(f"        expected: {n['expect']}")
            log(f"        from:     {n['where']}")
        log("")
        log("Put the file(s) above in place (download / upload them), then run this process again.")
        return {"ok": False, "outputs": [], "missing": [f"{n['label']} ({n['expect']})" for n in miss], "rc": None, "stage": "pre-check"}
    opt_miss = [n for n in needs if not n["present"] and n["optional"]]
    if opt_miss:
        log(f"PRE-CHECK OK. Optional file(s) not found (the script goes on without them): {', '.join(n['label'] for n in opt_miss)}")
    else:
        log("PRE-CHECK OK - every file is there.")
    work_base = card_dir(card) / "_work"
    work_base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{proc}_", dir=str(work_base)))
    keep = False
    try:
        for sub in {s for s in p.patch["roots"].values() if s}:
            (work / sub).mkdir(parents=True, exist_ok=True)
        log(f"Working folder: {work}")
        log(f"Copying {len(staged)} input file(s) into it ...")
        for s in staged:
            _copy(s.path, work / s.rel)
        text = (script_override or (SCRIPTS / p.script)).read_text(encoding="utf-8")
        script = work / "_run_script.py"
        script.write_text(patch_script(proc, text, work, values), encoding="utf-8")
        log("Running the script ...")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc_ = subprocess.Popen([python or sys.executable, "-u", str(script)], cwd=str(work), env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in proc_.stdout:
            log(line.rstrip("\r\n"))
            if cancelled():
                proc_.kill()
                log("Stopped by the user.")
                break
        rc = proc_.wait()
        outputs = _harvest(p, d, work, {s.rel.lower() for s in staged}, log)
        ok = rc == 0 and bool(outputs)
        if rc == 0 and not outputs:
            log("The script finished but no output file was found - treated as failed.")
        if not ok:
            keep = True
            log(f"FAILED (exit code {rc}). The working folder is kept for inspection: {work}")
        log(f"===== {p.label}: {'DONE' if ok else 'FAILED'} in {time.time() - t0:.0f} s =====")
        return {"ok": ok, "outputs": [o.name for o in outputs], "missing": [], "rc": rc}
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
        _prune_failed(work_base, proc)


def _prune_failed(work_base: Path, proc: str, keep_n: int = 2):
    try:
        dirs = sorted((x for x in work_base.glob(f"{proc}_*") if x.is_dir()), key=lambda x: x.stat().st_mtime, reverse=True)
        for x in dirs[keep_n:]:
            shutil.rmtree(x, ignore_errors=True)
    except OSError:
        pass


def _harvest(p: Proc, d: dt.date, work: Path, staged_names: set, log) -> List[Path]:
    """Copy what the script produced into <data>/<CARD>/<date>/<PROCESS>/ (the largest / newest file of each name)."""
    found: Dict[str, Path] = {}
    for rx in p.outputs:
        r = re.compile(rx, re.I)
        for f in work.rglob("*"):
            if f.is_file() and r.fullmatch(f.name) and f.relative_to(work).as_posix().lower() not in staged_names:
                cur = found.get(f.name.lower())
                if cur is None or f.stat().st_mtime >= cur.stat().st_mtime:
                    found[f.name.lower()] = f
    dest = out_dir(p.card, p.key, d)
    got = []
    if found:
        dest.mkdir(parents=True, exist_ok=True)
        for f in found.values():
            shutil.copy2(f, dest / f.name)
            got.append(dest / f.name)
            log(f"Output saved: {dest / f.name}  ({f.stat().st_size / 1024:,.0f} KB)")
    return got


# ───────────────────────────── status for the card ─────────────────────────────
def _state_file(card: str, proc: str, d: dt.date) -> Path:
    return _state_dir(card) / f"{dstr(d)}__{proc}.json"


def _write_result(card: str, proc: str, d: dt.date, res: dict, started: str, by: str, log: str):
    try:
        _state_dir(card).mkdir(parents=True, exist_ok=True)
        _state_file(card, proc, d).write_text(json.dumps({**res, "started": started, "finished": dt.datetime.now().isoformat(timespec="seconds"),
                                                           "by": by, "log": log}), encoding="utf-8")
    except OSError:
        pass


def last_result(card: str, proc: str, d: dt.date) -> dict:
    try:
        return json.loads(_state_file(card, proc, d).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def status(card: str, d: Optional[dt.date] = None) -> dict:
    d = d or default_date()
    c = CARDS[card]
    procs = []
    live_cache = None
    for key in c["procs"]:
        p = PROCS[key]
        staged = stage_plan(key, d)
        needs = check_needs(key, d, staged)
        groups: Dict[str, list] = {}
        for n in needs:
            groups.setdefault(n["group"], []).append(n)
        for n in needs:                              # made by an earlier step of this card -> not "missing"
            n["later"] = bool(not n["present"] and n["upstream"] and would_pass(n["upstream"], d))
        req = [n for n in needs if not n["optional"]]
        later = [n for n in req if n["later"]]
        outs = outputs_of(key, d)
        procs.append({"key": key, "label": p.label, "note": p.note,
                      "found": sum(1 for n in req if n["present"] or n["later"]), "total": len(req),
                      "later": [{"label": n["label"], "from": PROCS[n["upstream"]].label} for n in later],
                      "missing": [n["label"] + (f"  [{n['group']}]" if n["group"] else "") for n in req if not n["present"] and not n["later"]],
                      "groups": [{"name": g, "found": sum(1 for n in v if n["present"]), "total": len(v), "optional": all(n["optional"] for n in v),
                                  "missing": [n["label"] for n in v if not n["present"] and not n["later"]]} for g, v in groups.items()],
                      "outputs": [{"name": o.name, "size": o.stat().st_size} for o in outs],
                      "last": last_result(card, key, d), "to": os.getenv(email_env(key)[0], p.to_default),
                      "cc": os.getenv(email_env(key)[1], p.cc_default)})
    return {"card": card, "title": c["title"], "date": dstr(d), "iso": d.isoformat(), "default_iso": default_date().isoformat(),
            "procs": procs, "job": job_status(card), "manual": manual_status(d, card) if c["manual"] else None,
            "folder": str(card_dir(card) / dstr(d))}


def manual_status(d: dt.date, card: Optional[str] = None) -> dict:
    files = manual_sources(d)
    resp = next((s for s in files if s.rel.lower() == RESPONSE_NAME.lower()), None)
    alloc = next((s for s in files if re.fullmatch(rf"Allocation_Detail_{_dmy(d)}\.csv", s.rel, re.I)), None)
    out = {"folder": str(manual_dir(d)),
            "slots": [{**MANUAL_SLOTS[0], "present": bool(resp), "name": resp.rel if resp else "", "size": resp.path.stat().st_size if resp else 0},
                      {**MANUAL_SLOTS[1], "label": f"Allocation_Detail_{_dmy(d)}.csv", "present": bool(alloc), "name": alloc.rel if alloc else "",
                       "size": alloc.path.stat().st_size if alloc else 0}]}
    if card:                                        # only the manual files that THIS card's processes read
        rxs = _card_rx(card, d)
        probe = {"response": RESPONSE_NAME, "alloc": f"Allocation_Detail_{_dmy(d)}.csv"}
        out["slots"] = [x for x in out["slots"] if any(r.fullmatch(probe[x["key"]]) for r in rxs)]
    return out


def save_manual(d: dt.date, slot: str, name: str, data: bytes) -> dict:
    """Save an uploaded manual file; the name is checked so that a wrong file never lands in the day's folder."""
    name = os.path.basename(name or "")
    if not data:
        raise ValueError("The file is empty.")
    if slot == "response":
        if name.lower() != RESPONSE_NAME.lower():
            raise ValueError(f"This slot takes {RESPONSE_NAME}. You picked '{name}'.")
        final = RESPONSE_NAME
        try:                                      # the scripts stop when the file is of another day - say so now
            head = data[:200000].decode("utf-8", errors="replace").splitlines()
            import csv
            rows = list(csv.reader(head))
            col = rows[0].index("Date") if rows and "Date" in rows[0] else None
            if col is not None:
                days = {dt.datetime.strptime(r[col].strip(), "%d/%m/%Y").date() for r in rows[1:6] if len(r) > col and r[col].strip()}
                if days and d not in days:
                    raise ValueError(f"{RESPONSE_NAME} is dated {', '.join(sorted(x.strftime('%d-%m-%Y') for x in days))}, not {dstr(d)}.")
        except (IndexError, ValueError) as e:
            if "is dated" in str(e):
                raise
    elif slot == "alloc":
        if not re.fullmatch(rf"Allocation_Detail_{_dmy(d)}\.csv", name, re.I):
            raise ValueError(f"The name must be Allocation_Detail_{_dmy(d)}.csv for {dstr(d)}. You picked '{name}'.")
        final = f"Allocation_Detail_{_dmy(d)}.csv"
    else:
        raise ValueError("unknown slot")
    folder = manual_dir(d)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (final + ".part")
    tmp.write_bytes(data)
    tmp.replace(folder / final)
    return {"ok": True, "name": final, "size": len(data)}


def _card_rx(card: str, d: dt.date) -> List["re.Pattern"]:
    out = []
    for key in CARDS[card]["procs"]:
        for n in PROCS[key].needs(d):
            out.append(re.compile(re.sub(r"^\d/", "", n.rx), re.I))
    return out


def save_upload(card: str, d: dt.date, name: str, data: bytes) -> dict:
    """ONE place for every file you have to bring yourself. The name decides where it goes, so a file can never land in
    the wrong folder: manual files -> MANUAL FILES; the downloader's files -> the day folder of 76 FILES DOWNLOADER (the
    downloader then sees it as already there); RISK_REPORT -> COMMON FILES; live-allocation files -> their folder 1-8."""
    from core import files70 as F
    name = os.path.basename(name or "")
    if not data:
        raise ValueError(f"{name or 'the file'} is empty.")
    if not any(r.fullmatch(name) for r in _card_rx(card, d)):
        raise ValueError(f"'{name}' is not a file this card needs for {dstr(d)} (check the name and the date in it).")
    if name.lower() == RESPONSE_NAME.lower():
        r = save_manual(d, "response", name, data)
        return {**r, "where": "MANUAL FILES"}
    if re.fullmatch(rf"Allocation_Detail_{_dmy(d)}\.csv", name, re.I):
        r = save_manual(d, "alloc", name, data)
        return {**r, "where": "MANUAL FILES"}
    if re.fullmatch(rf"RISK_REPORT_{_dmy(d)}\.csv", name, re.I):
        try:
            from core import common_ftp
            folder = Path(common_ftp.common_dir())
        except Exception:
            folder = Path(os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES"))
        where = "COMMON FILES"
    else:
        m = re.fullmatch(r"MCX_Web_Allocation_0?([1-8]).*", name, re.I)
        n = re.fullmatch(rf"ClientNetEffectiveDeposits_{_dmy(d)}\.csv", name, re.I)
        if m or n:
            from core import live_allocation as L
            slot = int(m.group(1)) if m else 8
            folder, where = L.day_dir(d) / str(slot), f"LIVE ALLOCATION CAPTURE, folder {slot}"
        else:
            hit = None
            for it in F.plan(d):
                if it.get("name") and it["name"].lower() == name.lower():
                    hit = it
                elif it.get("rx") and re.match(it["rx"], name, re.I):
                    hit = it
                if hit:
                    break
            if not hit:
                raise ValueError(f"'{name}' cannot be placed - it is not one of the downloader's files or a live allocation file.")
            folder = F.day_dir(d) / hit["sub"] if hit["sub"] else F.day_dir(d)
            where = "76 FILES DOWNLOADER" + (f" / {hit['sub']}" if hit["sub"] else "")
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (name + ".part")
    tmp.write_bytes(data)
    tmp.replace(folder / name)
    return {"ok": True, "name": name, "size": len(data), "where": where}


# ───────────────────────────── job control (server side) ─────────────────────────────
_JOBS: Dict[str, dict] = {}
_JOB_LOCK = threading.Lock()


def _job(card: str) -> dict:
    return _JOBS.setdefault(card, {"proc": None, "lines": deque(maxlen=400), "log": None, "date": None, "by": "", "started": None,
                                   "rc": None, "procs": [], "current": None})


def _state_path(card: str) -> Path:
    return log_dir(card) / ".state.json"


def _read_state(card: str) -> dict:
    try:
        return json.loads(_state_path(card).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(card: str, st: dict):
    log_dir(card).mkdir(parents=True, exist_ok=True)
    tmp = _state_path(card).with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    tmp.replace(_state_path(card))


def _alive(pid) -> bool:
    try:
        import psutil
        return bool(pid) and psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).status() != psutil.STATUS_ZOMBIE
    except Exception:
        return bool(pid)


def is_running(card: str) -> bool:
    j = _job(card)
    if j["proc"] is not None and j["proc"].poll() is None:
        return True
    st = _read_state(card)
    return bool(st.get("running") and _alive(st.get("pid")))


def job_status(card: str) -> dict:
    j, st = _job(card), _read_state(card)
    running = is_running(card)
    lines = list(j["lines"])
    if not lines and st.get("log"):
        try:
            lines = Path(st["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-150:]
        except OSError:
            lines = []
    return {"running": running, "date": st.get("date"), "by": st.get("by"), "started": st.get("started"), "procs": st.get("procs") or [],
            "current": st.get("current") if running else None, "lines": lines[-80:], "ended": st.get("ended")}


def start(card: str, procs: List[str], d: dt.date, by: str = "", ip: str = "") -> dict:
    if card not in CARDS:
        return {"ok": False, "error": "unknown card"}
    procs = [x for x in procs if x in CARDS[card]["procs"]]
    if not procs:
        return {"ok": False, "error": "nothing to run"}
    with _JOB_LOCK:
        if is_running(card):
            st = _read_state(card)
            return {"ok": False, "error": f"{CARDS[card]['title']} is already running ({', '.join(PROCS[x].label for x in st.get('procs') or [])} "
                                          f"for {st.get('date')}) - wait for it or press Stop."}
        j = _job(card)
        ld = log_dir(card)
        ld.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now()
        logp = ld / f"{'+'.join(procs) if len(procs) > 1 else procs[0]}__{dstr(d)}__{ts:%Y%m%d_%H%M%S}.log"
        try:
            from core.pipeline_box import who_started_line
            who = who_started_line(by or None, ip or None)
        except Exception:
            who = f"# started by: {by or 'nobody recorded'}\n"
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# {CARDS[card]['title']}  processes={','.join(procs)}  date={dstr(d)}  start={ts:%Y-%m-%d %H:%M:%S}\n{who}")
        cmd = [sys.executable, "-u", "-m", "core.daycards", "--card", card, "--procs", ",".join(procs), "--date", d.isoformat(), "--by", by or ""]
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        flags = (0x08000000 | 0x00004000) if os.name == "nt" else 0
        p = subprocess.Popen(cmd, cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=flags)
        j.update(proc=p, log=str(logp), date=dstr(d), by=by, started=ts.isoformat(timespec="seconds"), rc=None, procs=procs)
        j["lines"].clear()
        _write_state(card, {"running": True, "pid": p.pid, "date": dstr(d), "by": by, "started": j["started"], "log": str(logp),
                            "procs": procs, "current": procs[0]})

        def pump():
            with open(logp, "a", encoding="utf-8", errors="replace") as f:
                for line in p.stdout:
                    line = line.rstrip("\r\n")
                    if line.startswith("@@CURRENT "):
                        st = _read_state(card)
                        st["current"] = line[10:].strip()
                        _write_state(card, st)
                        continue
                    f.write(line + "\n")
                    f.flush()
                    j["lines"].append(line)
            rc = p.wait()
            j["rc"] = rc
            st = _read_state(card)
            st.update(running=False, ended=dt.datetime.now().isoformat(timespec="seconds"), rc=rc, current=None)
            _write_state(card, st)
        threading.Thread(target=pump, daemon=True, name=f"daycard-{card}-pump").start()
        return {"ok": True, "log": logp.name, "date": dstr(d)}


def stop(card: str) -> bool:
    st = _read_state(card)
    j = _job(card)
    pid = j["proc"].pid if j.get("proc") is not None and j["proc"].poll() is None else st.get("pid")
    if not pid or not is_running(card):
        return False
    try:
        import psutil
        proc = psutil.Process(int(pid))
        for c in proc.children(recursive=True):
            c.kill()
        proc.kill()
    except Exception:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    try:
        with open(st.get("log") or j.get("log"), "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%H:%M:%S}] Stopped by user.\n")
    except Exception:
        pass
    return True


def list_logs(card: str, limit: int = 60) -> list:
    try:
        names = sorted((p for p in log_dir(card).glob("*.log")), key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": p.name, "size": p.stat().st_size} for p in names[:limit]]
    except OSError:
        return []


# ───────────────────────────── backup: zips of a day ─────────────────────────────
def backup_files(card: str, d: dt.date, procs: Optional[List[str]] = None) -> List[dict]:
    """Every file a zip can hold for the chosen processes: the INPUTS they read and the OUTPUTS they made.
    [{rel, path, size, proc, io: 'in'|'out'}]   rel = '<dd-mm-yyyy>/<folder>/<file>'"""
    out: List[dict] = []
    for key in (procs or CARDS[card]["procs"]):
        if key not in CARDS[card]["procs"]:
            continue
        lab = PROCS[key].label
        for s in stage_plan(key, d):
            out.append({"rel": s.zip_rel, "path": s.path, "size": s.path.stat().st_size, "proc": key, "io": "in", "kind": s.kind})
        for f in outputs_of(key, d):
            out.append({"rel": f"{dstr(d)}/OUTPUT/{lab}/{f.name}", "path": f, "size": f.stat().st_size, "proc": key, "io": "out", "kind": "out"})
    seen, uniq = set(), []
    for f in out:
        if f["rel"] not in seen:
            seen.add(f["rel"])
            uniq.append(f)
    return uniq


def missing_for_backup(card: str, d: dt.date, procs: List[str]) -> List[dict]:
    out = []
    for key in procs:
        for n in check_needs(key, d):
            if not n["present"] and not n["optional"]:
                out.append({"proc": key, "label": n["label"], "group": n["group"]})
    return out


def make_zip(card: str, d: dt.date, procs: Optional[List[str]] = None, io: str = "both", rels: Optional[List[str]] = None) -> str:
    """Write the zip into a temp file -> its path. io: both | in | out.  rels: only these files."""
    files = [f for f in backup_files(card, d, procs) if io == "both" or f["io"] == io]
    if rels is not None:
        want = set(rels)
        files = [f for f in files if f["rel"] in want]
    if not files:
        raise FileNotFoundError("nothing to put in the zip")
    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="daycard_")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f["path"], f["rel"])
    return tmp


def output_path(card: str, proc: str, d: dt.date, name: str) -> Optional[Path]:
    if proc not in CARDS[card]["procs"]:
        return None
    p = out_dir(card, proc, d) / os.path.basename(name)
    return p if p.is_file() else None


# ───────────────────────────── child process entry ─────────────────────────────
def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.daycards")
    ap.add_argument("--card", required=True)
    ap.add_argument("--procs", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--by", default="")
    a = ap.parse_args(argv)
    d = dt.date.fromisoformat(a.date)
    try:                                           # the env of the child already holds the saved settings; defaults for the rest
        apply_defaults()
    except Exception:
        pass
    ok_all = True
    for key in a.procs.split(","):
        print(f"@@CURRENT {key}", flush=True)
        started = dt.datetime.now().isoformat(timespec="seconds")
        lines: List[str] = []

        def log(m, _l=lines):
            print(m, flush=True)

        try:
            res = run_proc(a.card, key, d, log)
        except Exception as e:                    # noqa: BLE001
            print(f"FAILED: {type(e).__name__}: {e}", flush=True)
            res = {"ok": False, "outputs": [], "missing": [], "rc": None, "error": f"{type(e).__name__}: {e}"}
        _write_result(a.card, key, d, res, started, a.by, "")
        if not res["ok"]:
            ok_all = False
            print(f"Stopped: {PROCS[key].label} failed, the next process was not started." if key != a.procs.split(",")[-1] else "", flush=True)
            break
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(_main())
