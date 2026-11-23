"""download_inventory.py - which exchange files the console downloads, from where, and how often.

Stand-alone: NOT part of the console. Run it from the project folder:

    python tools\\download_inventory.py                 # writes DOWNLOAD_INVENTORY_<date>.xlsx next to the project
    python tools\\download_inventory.py --out D:\\x.xlsx   # another file name
    python tools\\download_inventory.py --date 2026-11-20 # trading day used to expand the 76 FILES list

What it does (nothing is downloaded, nothing is changed):
  1. 76 FILES DOWNLOADER  - the exact file list the console itself plans for one day (core.files70.plan).
  2. Scheduler / NSE code - every nse.download / nse.download_wait / nse.list_dir call found in the code
                            (exchange file name, NSE segment + folder it is fetched from).
  3. Other downloaders    - modules that talk to NCDEX, MCX portals, KORP, BSE, NSE index sites, TCS BANCS, NMASS ...
                            file names found in them (marked "auto-detected": check once against the process).
  4. Runs per day         - "how many times this file is supposed to be downloaded in one trading day":
                            = sum, over every process that downloads it, of that process's runs per day.
                            Runs per day come from your LIVE schedules (Scheduler tab) when the console database can be
                            read, else from the RUNS table below (edit it - one line per process).

Sheets: File Inventory (one row per file), By Process (one row per process + its files), Notes.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import os
import re
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# --------------------------------------------------------------------------------------------------------------------
# EDIT HERE - runs per trading day of processes that are NOT in the Scheduler (auto timers of the Process Center cards).
# key = part of the module file name (lower case);  value = (runs per day, how it runs)
# A process that has live schedules in the Scheduler is counted from those schedules instead.
# --------------------------------------------------------------------------------------------------------------------
RUNS = {
    "files70":              (1, "auto once a day from 09:00 (repeats only while files are missing)"),
    "files_downloader_70":  (1, "auto once a day"),
    "live_allocation":      (8, "8 timed slots: 10:12 12:10 13:27 15:10 16:53 18:55 20:55 22:55"),
    "mcx_cash_maintenance": (8, "8 timed slots 09:30 ... 16:30"),
    "ms15_auto":            (1, "Margin Shortage 15-min auto (every 15 min from the card - set the real number here)"),
    "sa_auto":              (1, "Short Allocation auto - set the real number here"),
    "concentration":        (1, "once a day (KORP report 08:40, run 09:30)"),
    "mtf_concentration":    (1, "once a day"),
    "mcx_comm_limit":       (1, "once a day"),
    "ncdex_comm_limit":     (1, "once a day"),
    "enterprise_risk":      (1, "once a day"),
    "mtf_order_file":       (1, "once a day"),
}
DEFAULT_RUNS = (1, "ASSUMED 1 per day - add the process to RUNS above if it runs more often")

# host -> (exchange / owner, source name)
HOSTS = OrderedDict([
    ("connect2nse.com",       ("NSE",   "NSE Extranet API (connect2nse.com)")),
    ("connect2nsccl.com",     ("NSE",   "NSCCL Extranet (connect2nsccl.com)")),
    ("nsearchives.nseindia",  ("NSE",   "NSE Archives (nsearchives.nseindia.com)")),
    ("nseindia.com",          ("NSE",   "NSE website (nseindia.com)")),
    ("niftyindices.com",      ("NSE",   "NSE Indices (niftyindices.com)")),
    ("ncfe.ncdex.com",        ("NCDEX", "NCDEX NCFE portal (ncfe.ncdex.com)")),
    ("webextranet.ncdex.com", ("NCDEX", "NCDEX web extranet (webextranet.ncdex.com)")),
    ("sftp.mcxindia.com",     ("MCX",   "MCX file portal (sftp.mcxindia.com)")),
    ("eclear.mcxccl.com",     ("MCX",   "MCX CCL eClear (eclear.mcxccl.com)")),
    ("bseindia.com",          ("BSE",   "BSE India (bseindia.com / api.bseindia.com)")),
    ("boapi.mastertrust",     ("INTERNAL", "KORP back-office API (boapi.mastertrust.co.in)")),
    ("webpayout.mastertrust", ("INTERNAL", "Payout web (webpayout.mastertrust.co.in)")),
    ("atomtech.in",           ("INTERNAL", "ATOM portal (titan.atomtech.in)")),
    ("tatatelebusiness.com",  ("INTERNAL", "TCS BANCS / Omni (wb.omni.tatatelebusiness.com)")),
    ("screener.in",           ("OTHER", "Screener.in")),
])
HOST_ORDER = list(HOSTS)

SKIP_MODULES = {"pipeline_box", "files_downloader_70", "files_downloader_70_run", "files70", "ops_metrics", "ops_sched",
                "daycards", "file_freshness", "stale_files", "readiness", "code_index", "agent", "knowledge", "rag",
                "ctcl_readiness", "safe_state", "backup_archive", "backup_to_db", "card_alerts", "short_allocation"}
DOWNLOAD_CALL_RX = re.compile(r"requests\.(get|post)|\.session\(|Session\(\)|\.download\(|retrbinary|urlretrieve|"
                              r"Command[\"']?\s*[:=]\s*[\"']Download|\.get\(.*(url|URL)|\.post\(.*(url|URL)")
# positive list: only names that look like a file an exchange / clearing house publishes
EXCHANGE_NAME_RX = re.compile(
    r"^(?:[CFOX]_[A-Z0-9]|<x>_<MEMBER>_SEC_PLEDGE|Margin_NCL|Delivery_NCL|Trade_NCL|BhavCopy|Bhavcopy|MCX_|NCCL|<NCDEX_UCC>_MGN|"
    r"F_AEL|NSE_CM_security|CM_ClientLevel|FO_ClientLevel|CO_ClientLevel|ClientNetEffective|Clientwise_mcx|CASH_MARGIN|"
    r"Kambala_OMNY|MWPL|ASM|OTM|SearchResults|ind_nifty|NIFTY500|BSE|sec_bhavdata|MTFPOSITION|RISK_REPORT|RAW_MCX|MAIN_MCX|"
    r"<DATE>_MASTER|Circuit_History)", re.I)
EXCHANGE_SOURCES = {"NSE", "MCX", "NCDEX", "BSE"}
FILE_RX = re.compile(r"\.(csv|lis|xlsx|xls|xlsm|zip|gz|txt|dat|pdf|xml|mdb)(\b|$)", re.I)
NOISE_RX = re.compile(r"(settings|state|progress|run_counter|mail_log|List\.json|checkpoint|captcha|_log|\.log|^\*|"
                      r"^\.|output|OUTPUT|FULL_FILE|SUMMARY|Summary|REPORT_IMAGE|\.pdf$|mail|MAIL)", re.I)
CODE_DIRS = ["core", "ctcl_codes", "sa_codes", "pipelines", "recon_codes"]
SKIP_DIRS = {"__pycache__", "tests", ".git", "node_modules"}


# ----------------------------------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------------------------------
def normalise(name: str, member: str = "", mcxu: str = "") -> str:
    """File name -> pattern: dates, member code, slot numbers become <...> so one file = one row."""
    s = name
    if member:
        s = s.replace(member, "<MEMBER>")
    if mcxu:
        s = s.replace(mcxu, "<MCX_USER>")
    s = re.sub(r"\b0?1292\b", "<NCDEX_UCC>", s)
    s = re.sub(r"\d{8}", "<DATE>", s)
    s = re.sub(r"_P_\d{4}\b", "_P_<HHMM>", s)
    s = re.sub(r"_i0\d", "_i<NN>", s)
    s = re.sub(r"_0[1-8]\.(csv|CSV)", "_<NN>.csv", s)
    s = re.sub(r"\{[^{}]*(?:date|ddmmyyyy|dmy|ymd|ds|d|day|dt|today|stamp)[^{}]*\}", "<DATE>", s, flags=re.I)
    s = re.sub(r"\{[^{}]*(?:mem|m|member|code)\}", "<MEMBER>", s, flags=re.I)
    s = re.sub(r"\{[^{}]*\}", "<x>", s)
    s = re.sub(r"<DATE>(?:<DATE>)+", "<DATE>", s)
    s = re.sub(r"<(?:DATE|x)>_?<(?:DATE|x)>", "<DATE>", s)
    s = s.replace("<DDMMYYYY>", "<DATE>").replace("<date>", "<DATE>")
    return s


def exchange_from_name(n: str, default: str = "") -> str:
    u = n.upper()
    if u.startswith("MCX") or "MCX_" in u:
        return "MCX"
    if "NCCL" in u or "MGN" in u or "NCDEX" in u:
        return "NCDEX"
    if re.match(r"^(C|F|O|X)_", u) or u.startswith(("MARGIN_NCL", "DELIVERY_NCL", "BHAVCOPY", "F_AEL", "FO_", "CM_")):
        return "NSE"
    return default or "UNKNOWN"


def runs_for(module_path: str) -> tuple:
    key = module_path.lower()
    for k, v in RUNS.items():
        if k in key:
            return v
    return DEFAULT_RUNS


def render(node) -> str:
    """AST node -> readable template. f-string parts become {name}."""
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                try:
                    out.append("{" + ast.unparse(v.value).split(".")[-1].split("(")[0].split("[")[0] + "}")
                except Exception:
                    out.append("{x}")
        return "".join(out)
    try:
        return "{" + ast.unparse(node) + "}"
    except Exception:
        return "{x}"


def py_files():
    for d in CODE_DIRS:
        base = REPO / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.suffix.lower() == ".py" and not (set(p.parts) & SKIP_DIRS):
                yield p


def process_name(src: str, rel: str) -> str:
    """Process key/name from catalog.register(...) in the file, else the module name."""
    m = re.search(r"register\(\s*key\s*=\s*[\"']([^\"']+)[\"']\s*,\s*name\s*=\s*[\"']([^\"']+)[\"']", src, re.S)
    if m:
        return m.group(2)
    return Path(rel).stem


# ----------------------------------------------------------------------------------------------------------------------
# live schedules (runs per day) - optional
# ----------------------------------------------------------------------------------------------------------------------
def live_schedule_runs(day: dt.date) -> dict:
    """{process name (upper): runs on `day`} from the enabled Scheduler schedules, or {} when the DB is not reachable."""
    try:
        from core.scheduler import store, rules
        store.ensure_schema()
        out: dict = defaultdict(int)
        start = dt.datetime.combine(day, dt.time(0, 0), tzinfo=dt.timezone.utc) - dt.timedelta(hours=6)
        end = start + dt.timedelta(days=1)
        for s in store.list_schedules():
            if (s.get("status") or "").lower() not in ("active", "enabled", "on", "running"):
                continue
            try:
                n = len(rules.between(s, start, end, is_holiday=None))
            except Exception:
                continue
            name = (s.get("process_name") or s.get("process_key") or s.get("process") or "").upper()
            out[name] += n
            if s.get("process_key"):
                out[str(s["process_key"]).upper()] += n
        return dict(out)
    except Exception as e:                                           # noqa: BLE001
        print(f"  (live schedules not read: {type(e).__name__}: {str(e)[:80]} - using the RUNS table)")
        return {}


# ----------------------------------------------------------------------------------------------------------------------
# collectors
# ----------------------------------------------------------------------------------------------------------------------
class Row(dict):
    pass


def collect_files70(day: dt.date) -> list:
    rows = []
    try:
        from core import files70 as F
        member, mcxu = F._member(), F._mcx_user()
        plan = F.plan(day, member, mcxu)
    except Exception as e:                                           # noqa: BLE001
        print(f"  (76 FILES plan not read: {type(e).__name__}: {e})")
        return rows
    for it in plan:
        name = it.get("name") or it.get("label")
        src = it.get("src")
        if src == "nse":
            exch, source, where = "NSE", "NSE Extranet API (connect2nse.com)", f"member/{it['seg']}/{it['folder']}"
        elif src == "mcx":
            exch, source, where = "MCX", "MCX file portal (sftp.mcxindia.com)", "/Common (daily file folder)"
        elif src == "ncfe":
            exch, source, where = "NCDEX", "NCDEX NCFE portal (ncfe.ncdex.com)", "TradeListener/ViewUpdateMargins"
        elif src == "ncdexweb":
            exch, source, where = "NCDEX", "NCDEX web extranet (webextranet.ncdex.com)", "NCCL_MARGIN_REP 01-08"
        else:
            exch, source, where = "INTERNAL", "KORP back-office API (boapi.mastertrust.co.in)", "early pay-in"
        rows.append(Row(exchange=exch, segment=it.get("seg") or it.get("sub") or "", file=normalise(name, member, mcxu),
                        example=name, source=source, where=where, process="76 FILES DOWNLOADER",
                        module="core/files70.py", certain="Yes (exact plan)", optional=bool(it.get("optional"))))
    return rows


def collect_nse_calls() -> list:
    rows = []
    try:
        from core import files70 as F
        member, mcxu = F._member(), F._mcx_user()
    except Exception:
        member, mcxu = "06436", "56720"
    for p in py_files():
        rel = str(p.relative_to(REPO)).replace("\\", "/")
        if rel.endswith("files70.py") or rel.endswith("scheduler/nse.py"):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src)
        except Exception:
            continue
        if "nse.download" not in src and "nse.list_dir" not in src:
            continue
        pname = process_name(src, rel)
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("download", "download_wait", "list_dir")
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "nse"):
                continue
            a = n.args
            if len(a) < 3:
                continue
            kind, seg, folder = render(a[0]), render(a[1]), render(a[2])
            fname = render(a[3]) if (n.func.attr != "list_dir" and len(a) > 3) else "(folder listing)"
            if n.func.attr == "list_dir":
                continue
            if fname.startswith("{") and "job" in fname:           # generic user-defined portal jobs - see Notes
                continue
            rows.append(Row(exchange="NSE", segment=seg.strip("{}'\""), file=normalise(fname, member, mcxu), example=fname,
                            source="NSE Extranet API (connect2nse.com)", where=f"{kind}/{seg}/{folder}".replace("{", "<").replace("}", ">"),
                            process=pname, module=rel, certain="Yes (call in code)", optional=False))
    return rows


def collect_other_downloaders(seen_modules: set) -> list:
    """Modules that contain an exchange / portal host: take the file names found in them."""
    rows = []
    member, mcxu = "06436", "56720"
    for p in py_files():
        rel = str(p.relative_to(REPO)).replace("\\", "/")
        if rel in seen_modules or rel.endswith(("scheduler/nse.py", "scheduler/mcx.py")):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src)
        except Exception:
            continue
        hosts = [h for h in HOST_ORDER if h in src and HOSTS[h][0] in EXCHANGE_SOURCES]
        if not hosts or len(hosts) > 4:
            continue                                     # aggregators / readers (pipeline_box, ops_*, readiness ...)
        if Path(rel).stem in SKIP_MODULES:
            continue
        if not DOWNLOAD_CALL_RX.search(src):
            continue
        source_names = " / ".join(HOSTS[h][1] for h in hosts)
        exch_default = HOSTS[hosts[0]][0]
        pname = process_name(src, rel)
        found = {}
        for n in ast.walk(tree):
            if isinstance(n, (ast.Constant, ast.JoinedStr)):
                s = render(n)
                if not (4 < len(s) < 80) or " " in s.strip() or "\n" in s or "/" in s or "\\" in s:
                    continue
                if (not FILE_RX.search(s) or NOISE_RX.search(s) or s.startswith((".", "*", "^", "(", "\\"))
                        or "*" in s or "\\" in s or "^" in s or "$" in s or s.startswith("_")):
                    continue
                if s.lower() in (".xlsx", ".csv", ".txt", ".zip", ".gz") or re.fullmatch(r"[.*\w-]*\.(xlsx|csv|txt)", s) and len(s) < 9:
                    continue
                nm = normalise(s, member, mcxu)
                if not EXCHANGE_NAME_RX.match(nm):
                    continue
                found[nm] = s
        for patt, ex in sorted(found.items()):
            rows.append(Row(exchange=exchange_from_name(ex, exch_default), segment="", file=patt, example=ex,
                            source=source_names, where="(see module)", process=pname, module=rel,
                            certain="Auto-detected (check)", optional=False))
    return rows


def collect_mcx_calls() -> list:
    rows = []
    for p in py_files():
        rel = str(p.relative_to(REPO)).replace("\\", "/")
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "from core.scheduler import" not in src and "import mcx" not in src:
            continue
        if not re.search(r"\bmcx\.\w*[Dd]ownload|\bmcx\.Portal|\bmcx\.latest_bhavcopy", src):
            continue
        rows.append(Row(exchange="MCX", segment="COMM", file="Bhavcopy (latest, via mcx.latest_bhavcopy)", example="Bhavcopy",
                        source="MCX file portal (sftp.mcxindia.com)", where="/Common", process=process_name(src, rel),
                        module=rel, certain="Yes (call in code)", optional=False))
    return rows


# ----------------------------------------------------------------------------------------------------------------------
# build + Excel
# ----------------------------------------------------------------------------------------------------------------------
def build(day: dt.date):
    print("Reading the code ...")
    sched = live_schedule_runs(day)
    rows = collect_files70(day) + collect_nse_calls() + collect_mcx_calls()
    seen = {r["module"] for r in rows}
    rows += collect_other_downloaders(seen)

    def runs_of(r):
        for key in (r["process"].upper(), Path(r["module"]).stem.upper()):
            if key in sched:
                return sched[key], "live schedules"
        n, note = runs_for(r["module"])
        return n, note

    # group: one line per (exchange, file pattern, source, where)
    grp: "OrderedDict[tuple, dict]" = OrderedDict()
    for r in rows:
        k = (r["exchange"], r["file"], r["source"], r["where"])
        g = grp.setdefault(k, dict(exchange=r["exchange"], segment=r["segment"], file=r["file"], example=r["example"],
                                   source=r["source"], where=r["where"], procs=OrderedDict(), certain=set(),
                                   optional=r["optional"]))
        n, note = runs_of(r)
        g["procs"][r["process"]] = (n, note)
        g["certain"].add(r["certain"])
    inv = []
    for g in grp.values():
        if g["file"] in ("<x>", "{x}") or re.fullmatch(r"<x>(\.\w+)?", g["file"]):
            g["file"] = g["example"] = "(file name built at run time - see module)"
        total = sum(n for n, _ in g["procs"].values())
        inv.append([g["exchange"], g["segment"], g["file"], g["example"], g["source"], g["where"], total,
                    len(g["procs"]),
                    "; ".join(f"{p} ({n}x)" for p, (n, _) in g["procs"].items()),
                    "; ".join(sorted(g["certain"])), "optional file" if g["optional"] else ""])
    order = {"NSE": 0, "MCX": 1, "NCDEX": 2, "BSE": 3, "INTERNAL": 4, "OTHER": 5, "UNKNOWN": 6}
    inv.sort(key=lambda x: (order.get(x[0], 9), x[1], x[2]))

    byp: "OrderedDict[str, dict]" = OrderedDict()
    for r in rows:
        d = byp.setdefault(r["process"], dict(module=r["module"], files=set(), sources=set(), exch=set()))
        d["files"].add(r["file"]); d["sources"].add(r["source"]); d["exch"].add(r["exchange"])
    proc = []
    for name, d in sorted(byp.items()):
        n, note = runs_of(Row(process=name, module=d["module"]))
        proc.append([name, d["module"], ", ".join(sorted(d["exch"])), n, note, len(d["files"]), "; ".join(sorted(d["sources"]))])
    return inv, proc, bool(sched)


def write_excel(path: Path, inv, proc, live: bool, day: dt.date):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1F3864")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    exch_fill = {"NSE": "DDEBF7", "MCX": "FCE4D6", "NCDEX": "E2EFDA", "BSE": "FFF2CC", "INTERNAL": "EDEDED", "OTHER": "F2F2F2"}

    def sheet(ws, headers, data, widths, color_col=None, num_cols=()):
        ws.append(headers)
        for r in data:
            ws.append(r)
        for c in range(1, len(headers) + 1):
            h = ws.cell(row=1, column=c)
            h.font = Font(bold=True, color="FFFFFF"); h.fill = head_fill; border and setattr(h, "border", border)
            h.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.column_dimensions[get_column_letter(c)].width = widths[c - 1]
        ws.row_dimensions[1].height = 32
        for row in ws.iter_rows(min_row=2):
            fill = None
            if color_col is not None:
                fill = PatternFill("solid", fgColor=exch_fill.get(str(row[color_col].value), "FFFFFF"))
            for c in row:
                c.border = border
                c.alignment = Alignment(vertical="top", wrap_text=True,
                                        horizontal="center" if c.column - 1 in num_cols else "left")
                if fill is not None:
                    c.fill = fill
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    ws = wb.active
    ws.title = "File Inventory"
    sheet(ws, ["Exchange", "Segment", "File name (pattern)", "Example", "Downloaded from (site / API)", "Source path / folder",
               "Expected downloads per day", "No. of processes", "Downloaded by (runs per day)", "How sure", "Remark"],
          inv, [11, 9, 46, 40, 40, 34, 14, 11, 60, 20, 14], color_col=0, num_cols=(6, 7))
    ws2 = wb.create_sheet("By Process")
    sheet(ws2, ["Process", "Code module", "Exchange(s)", "Runs per day", "How it runs", "Files", "Sources"], proc,
          [38, 46, 14, 11, 60, 8, 70], num_cols=(3, 5))
    ws3 = wb.create_sheet("Notes")
    notes = [
        ["Made on", dt.datetime.now().strftime("%d-%m-%Y %H:%M")],
        ["Day used to expand the 76 FILES list", day.strftime("%d-%m-%Y")],
        ["Runs per day source", "live Scheduler schedules + RUNS table" if live else "RUNS table only (console database not reachable from here)"],
        ["Expected downloads per day", "Sum over every process that downloads the file of that process's runs per day. A file fetched by 76 FILES (1x) and by a 4x/day schedule shows 5."],
        ["Yes (exact plan)", "Taken from the same plan the 76 FILES DOWNLOADER card uses."],
        ["Yes (call in code)", "An nse.download / mcx call found in the code; {name} parts change by day."],
        ["Auto-detected (check)", "File name found inside a module that talks to that site; confirm once against the process."],
        ["Not covered", "Generic user-defined Portal Downloads jobs (Scheduler > Portal Downloads) and files that people upload by hand or receive by e-mail."],
        ["Edit", "tools/download_inventory.py -> RUNS table, for auto timers that are not in the Scheduler."],
    ]
    for r in notes:
        ws3.append(r)
    ws3.column_dimensions["A"].width = 38; ws3.column_dimensions["B"].width = 120
    for row in ws3.iter_rows():
        row[0].font = Font(bold=True)
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    wb.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="")
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    try:
        from core.scheduler import helpers as H
        if not a.date:
            day = H.prev_trading_day(dt.date.today())
    except Exception:
        pass
    inv, proc, live = build(day)
    out = Path(a.out) if a.out else REPO / f"DOWNLOAD_INVENTORY_{dt.date.today():%Y%m%d}.xlsx"
    write_excel(out, inv, proc, live, day)
    print(f"\n{len(inv)} files, {len(proc)} processes  ->  {out}")


if __name__ == "__main__":
    main()
