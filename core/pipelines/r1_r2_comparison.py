"""
core/pipelines/r1_r2_comparison.py — Segment-wise Collateral Movement Report
==============================================================================
Console-native port of the standalone reconciliation_snapshot_v2.py. Tracks
per-client collateral allocation across 5 exchange segments (CM, FO, CO,
NCDEX, MCX) through the trading day, always diffing against a fixed Morning
baseline (never "vs whatever ran last").

Three entry points, one per dashboard button — NOT the generic numbered
px run-no model every other multi-run pipe (ctcl/ms15/cli/sa) uses, because
those assume strictly sequential, once-each runs. Here Live Snapshot repeats
any number of times with no fixed order relative to Afternoon/Evening, so it
needs its own bespoke SSE endpoints (see api.py's /r1r2/*-stream) instead:
    run_r1r2_live()       - Live Snapshot. No once-per-day limit.
    run_r1r2_afternoon()  - once per calendar day.
    run_r1r2_evening()    - once per calendar day.

FIRST SET OF 5 FILES (the fixed Morning baseline) — always read from CLI
Prudential Limit's own backup for today, read-only, never moved/deleted:
    <CLI_FOLDER>\\BACKUPFILES AUTOMATED\\<DD-MM-YYYY>\\<HH-MM-SS>\\
classified into Morning/Afternoon/Evening by time-of-day (see _slot_for_hour).

SECOND SET OF 5 FILES:
    Afternoon / Evening -> same CLI backup mechanism, for that slot. Falls
        back to picking today's files directly out of R1R2_FOLDER if CLI
        hasn't produced that slot's backup yet (e.g. manually dropped files).
    Live Snapshot -> checks R1R2_FOLDER itself first; if all 5 are already
        there and dated today, reuses them with no login at all. Otherwise
        downloads via the CONSOLE'S OWN shared pipelines:
          - core.pipelines.nmass_download.stream_nmass_download(pipe="r1r2")
            for CM/FO/CO/NCDEX - same shared NMASS/NCDEX login lock as
            CTCL/MS15/PROBABLE, so this can never collide with (or get
            locked out by) one of those running at the same time. Captcha
            answered via the dashboard's existing modal, scoped to R1R2_FOLDER
            (see api.py's /r1r2/captcha/pending + /r1r2/captcha/answer).
          - sa_codes/mcx_deposit_downloader.py's download_mcx_deposit() for
            MCX - the exact same function/session SA's "MCX Deposit" and
            CLI's "MCX Web Allocation" buttons already use. RSA token
            answered via the ALREADY-GENERIC dashboard modal (/sa/token/*,
            keyed by the "caller" field) - no new endpoint needed for this
            leg, just MCX_DEPOSIT_CALLER="r1r2" so the badge shows the right
            box name (see dashboard.html's _pipeBoxLabel()).
    Live Snapshot's files are then MOVED (not copied) into this run's backup
    folder afterward, so R1R2_FOLDER ends up clean - a same-day rerun always
    re-downloads fresh rather than reusing leftovers.

OUTPUT: allocation_master.xlsx in R1R2_FOLDER - never accumulates every run
of the day; each Afternoon/Evening/Live run REPLACES the previous run's
non-Morning columns, so the master always holds exactly 10 data columns
(5 Morning + 5 latest run) plus that run's diff columns. Resets automatically
on a new calendar day (yesterday's master archived to Backup\\<date>\\).

BACKUP: every run gets its own Backup\\<date>\\<HH-MM-SS>\\ folder containing
a copy of the 5 Morning files, the 5 files used for this run's own snapshot,
and a copy of the master as of this run. Morning/Afternoon/Evening files are
COPIED (CLI's own backup is never touched); Live's are MOVED (they're
R1R2_FOLDER-owned - either freshly downloaded or manually dropped there).

EMAIL: sent via the shared SMTP account (SENDER_EMAIL/SENDER_PASSWORD),
infinite retry every 10s on failure, same pattern as every other pipe here.
"""
import datetime
import json
import os
import re
import shutil
import smtplib
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# ===========================================================================
#  CONFIG
# ===========================================================================
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLI_FOLDER = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")

# Margin Shortage (15-min) — source for the "15-min Backup" mode's second set.
# NOTE its backup layout is a THIRD convention, different from CLI's and this
# module's own: <MS15_FOLDER>\BACKUPFILES AUTOMATED\<YYYY-MM-DD>\<NN>\
# (zero-padded RUN NUMBER, not a HH-MM-SS time). See backup_and_cleanup() in
# core/pipelines/margin_shortage_15min.py.
MS15_FOLDER = os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
MS15_BACKUP_ROOT = "BACKUPFILES AUTOMATED"

# On a fresh machine (nobody's set R1R2_FOLDER in .env yet) this lives inside
# the console's own data folder; override in .env (e.g. R1R2_FOLDER=D:\R1_R2_COMPARISON)
# for a machine that already has a folder in use.
BASE_FOLDER = os.getenv("R1R2_FOLDER", os.path.join(_REPO, "data", "r1_r2_comparison"))

SEGMENT_ORDER = ["CM", "FO", "CO", "NCDEX", "MCX"]
MASTER_FILENAME = "allocation_master.xlsx"
META_FILENAME = ".reconciliation_meta.json"
BACKUP_FOLDER_NAME = "Backup"
SOURCE_EXTENSIONS = (".csv", ".txt", ".xlsx", ".xlsm", ".xls")


def _emails(key):
    return [e.strip() for e in os.getenv(key, "").split(",") if e.strip()]


EMAIL_TO = _emails("R1R2_EMAIL_TO")
EMAIL_CC = _emails("R1R2_EMAIL_CC")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "")


class AbortRun(Exception):
    """Raised when the run must stop without changing anything on disk."""


# ---------------------------------------------------------------------------
# fuzzy header matching helpers
# ---------------------------------------------------------------------------

def normalize_alpha(text):
    return re.sub(r"[^a-z]", "", str(text).lower())


def is_client_id_header(cell):
    n = normalize_alpha(cell)
    return ("client" in n and "code" in n) or ("ucc" in n and "code" in n)


def amount_kind(cell):
    n = normalize_alpha(cell)
    if "cashallocated" in n:
        return "cash_allocated"
    if "cashequivalent" in n:
        return "cash_equivalent"
    if "allocation" in n and "eod" in n:
        return "allocation_eod"
    return None


def is_amount_header(cell):
    return amount_kind(cell) is not None


def to_float(val):
    if val is None:
        return 0.0
    s = str(val).replace(",", "").strip()
    if s == "" or s.lower() in ("nan", "none"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# reading a source file (csv / txt / xlsx)
# ---------------------------------------------------------------------------

def read_raw_rows(path):
    import csv
    ext = os.path.splitext(path)[1].lower()
    rows = []
    if ext in (".csv", ".txt"):
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            for row in csv.reader(f):
                rows.append([c.strip() if c is not None else "" for c in row])
    elif ext in (".xlsx", ".xlsm", ".xls"):
        try:
            import openpyxl
        except ImportError:
            raise AbortRun("openpyxl is required.\n  Install it with:  pip install openpyxl")
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
        ws = wb.worksheets[0]
        for row in ws.iter_rows(values_only=True):
            rows.append(["" if c is None else str(c).strip() for c in row])
    else:
        return None
    return rows


def find_header_row(rows, scan_limit=60):
    for i, row in enumerate(rows[:scan_limit]):
        if any(is_client_id_header(c) for c in row) and any(is_amount_header(c) for c in row):
            return i
    return None


def detect_segment(filename, headers):
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    token = re.split(r"[_\-\s]", stem)[0]
    if token in ("cm", "fo", "co"):
        return token.upper()
    kinds = {amount_kind(h) for h in headers if amount_kind(h)}
    if "cash_equivalent" in kinds:
        return "NCDEX"
    if "allocation_eod" in kinds:
        return "MCX"
    return None


def extract_from_file(path, verbose=True):
    """Returns (segment, {client_code: amount}) or None if unreadable/unknown.

    verbose=False silences every print here - used by /r1r2/readiness (api.py),
    which calls this indirectly via _scan_and_choose() on a tight poll (every
    open dashboard tab, every few seconds) just to check file status. With
    verbose defaulting to True, those passive polls were printing "[ok]"/
    "[skip]" lines to the server's console continuously even with zero clicks -
    real runs (Live Snapshot, Download, etc., captured into their own log file
    via _stream_code) still get the full verbose output as before."""
    try:
        rows = read_raw_rows(path)
    except AbortRun:
        raise
    except Exception as exc:
        if verbose:
            print(f"  [skip] could not read {os.path.basename(path)}: {exc}")
        return None

    if not rows:
        if verbose:
            print(f"  [skip] empty or unreadable: {os.path.basename(path)}")
        return None

    header_idx = find_header_row(rows)
    if header_idx is None:
        if verbose:
            print(f"  [skip] no recognizable header row in: {os.path.basename(path)}")
        return None

    headers = rows[header_idx]
    segment = detect_segment(path, headers)
    if segment is None:
        if verbose:
            print(f"  [skip] could not tell which segment this is: {os.path.basename(path)}")
        return None

    id_col = next((i for i, h in enumerate(headers) if is_client_id_header(h)), None)
    wanted_kind = {"NCDEX": "cash_equivalent", "MCX": "allocation_eod"}.get(segment, "cash_allocated")
    amt_col = next((i for i, h in enumerate(headers) if amount_kind(h) == wanted_kind), None)
    if id_col is None or amt_col is None:
        if verbose:
            print(f"  [skip] required columns not found in: {os.path.basename(path)}")
        return None

    if verbose:
        print(f"  [ok] {os.path.basename(path)}  ->  {segment}, header row {header_idx + 1}, "
              f"client col '{headers[id_col]}', amount col '{headers[amt_col]}'")

    totals = {}
    for row in rows[header_idx + 1:]:
        if id_col >= len(row):
            continue
        client = row[id_col].strip()
        if not client:
            continue
        amt = to_float(row[amt_col]) if amt_col < len(row) else 0.0
        totals[client] = totals.get(client, 0.0) + amt

    return segment, totals


def parse_file_stamp(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))

    for d in reversed(re.findall(r"\d{14}", stem)):
        parsed = _split_date8(d[:8])
        if parsed is None:
            continue
        hh, mi = d[8:10], d[10:12]
        if hh.isdigit() and mi.isdigit() and int(hh) < 24 and int(mi) < 60:
            return parsed, f"{hh}.{mi}", "filename"

    if not re.search(r"\d{14}", stem):
        for d in reversed(re.findall(r"\d{8}", stem)):
            parsed = _split_date8(d)
            if parsed is not None:
                return parsed, mtime.strftime("%H.%M"), "filename date + file time"

    return mtime.strftime("%d-%m-%Y"), mtime.strftime("%H.%M"), "file modified time"


def _split_date8(d):
    if len(d) != 8 or not d.isdigit():
        return None
    if 2000 <= int(d[0:4]) <= 2099:
        y, mo, da = d[0:4], d[4:6], d[6:8]
    elif 2000 <= int(d[4:8]) <= 2099:
        da, mo, y = d[0:2], d[2:4], d[4:8]
    else:
        return None
    if not (1 <= int(mo) <= 12 and 1 <= int(da) <= 31):
        return None
    return f"{da}-{mo}-{y}"


# ---------------------------------------------------------------------------
# auto-pick the 5 files for a given CLI backup slot (Morning/Afternoon/Evening)
# ---------------------------------------------------------------------------

def list_files_in(folder):
    """Excludes this pipeline's own files (master xlsx, meta json, in-progress
    .tmp write) so they're never mistaken for a segment source file."""
    out = []
    own_files = {MASTER_FILENAME.lower(), META_FILENAME.lower()}
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        if name.startswith("~$") or name.startswith("."):
            continue
        if name.lower() in own_files or name.lower().endswith(".tmp.xlsx"):
            continue
        if os.path.splitext(name)[1].lower() not in SOURCE_EXTENSIONS:
            continue
        out.append(full)
    return out


def _scan_and_choose(folder, today_only=False, verbose=True):
    """Content-sniff every file in `folder`, pick the newest valid file per
    segment. Returns (chosen_totals, chosen_paths, missing_segments) - never
    raises.

    verbose=False silences this function's own prints AND passes through to
    extract_from_file() - see that function's docstring for why (used by
    /r1r2/readiness's frequent passive polling, api.py)."""
    files = list_files_in(folder)
    if today_only:
        today = datetime.datetime.now().date()
        today_str = datetime.datetime.now().strftime("%d-%m-%Y")
        kept = []
        for f in files:
            if datetime.datetime.fromtimestamp(os.path.getmtime(f)).date() != today:
                continue
            # mtime alone isn't a reliable enough "is this actually today's
            # file" check - cross-check the date embedded in the filename too.
            file_date_str, _, note = parse_file_stamp(f)
            if note != "file modified time" and file_date_str != today_str:
                if verbose:
                    print(f"  [skip] {os.path.basename(f)} - filename date "
                          f"({file_date_str}) does not match today ({today_str}), skipping")
                continue
            kept.append(f)
        files = kept

    candidates = {}
    for path in files:
        result = extract_from_file(path, verbose=verbose)
        if result is None:
            continue
        segment, totals = result
        candidates.setdefault(segment, []).append((os.path.getmtime(path), path, totals))

    chosen, chosen_paths = {}, {}
    for segment, lst in candidates.items():
        lst.sort(key=lambda x: x[0], reverse=True)
        _, path, totals = lst[0]
        chosen[segment] = totals
        chosen_paths[segment] = path
        if verbose:
            print(f"  [use] {segment:<5} {os.path.basename(path)}  ({len(totals)} clients)")

    missing = [s for s in SEGMENT_ORDER if s not in chosen]
    return chosen, chosen_paths, missing


def _slot_for_hour(hh):
    if hh < 12:
        return "Morning"
    if hh < 18:
        return "Afternoon"
    return "Evening"


def _find_slot_folder(slot, date_str):
    """CLI's backup layout: <CLI_FOLDER>\\BACKUPFILES AUTOMATED\\<date>\\<HH-MM-SS>\\
    Classify each run folder by its own HH via _slot_for_hour() and use the
    LATEST one that falls in the requested slot for that date."""
    date_dir = os.path.join(CLI_FOLDER, "BACKUPFILES AUTOMATED", date_str)
    if not os.path.isdir(date_dir):
        return None, []

    seen = []
    matches = []
    for name in sorted(os.listdir(date_dir)):
        full = os.path.join(date_dir, name)
        if not os.path.isdir(full):
            continue
        m = re.match(r"^(\d{2})-(\d{2})-(\d{2})$", name)
        if not m:
            continue
        this_slot = _slot_for_hour(int(m.group(1)))
        seen.append((name, this_slot))
        if this_slot == slot:
            matches.append(name)

    if not matches:
        return None, seen
    matches.sort()
    return os.path.join(date_dir, matches[-1]), seen


def collect_from_cli_slot(slot, date_str):
    slot_dir, seen = _find_slot_folder(slot, date_str)
    if slot_dir is None:
        date_dir = os.path.join(CLI_FOLDER, "BACKUPFILES AUTOMATED", date_str)
        seen_desc = ", ".join(f"{n} ({s})" for n, s in seen) or "(none)"
        raise AbortRun(
            f"No CLI {slot} backup found for {date_str}.\n"
            f"  Looked under:\n    {date_dir}\n"
            f"  Run folder(s) seen there: {seen_desc}\n"
            f"  Run CLI Prudential Limit's {slot} step first, then try again."
        )

    print(f"CLI {slot} backup ({date_str}): scanning {slot_dir}\n")
    chosen, chosen_paths, missing = _scan_and_choose(slot_dir)
    print()

    if missing:
        raise AbortRun(
            f"CLI {slot} backup for {date_str} is missing file(s) for: " + ", ".join(missing) + "\n"
            f"  Folder checked:\n    {slot_dir}\n"
            "  All 5 files (CM, FO, CO, NCDEX, MCX) must be present in that backup."
        )

    return chosen, chosen_paths


def _consume_base_folder_files(chosen_paths, tag):
    """Move files picked up by a BASE_FOLDER fallback scan OUT of BASE_FOLDER
    immediately, into a private per-run holding folder.

    Without this, a LATER fetch step in the SAME run - Live Snapshot's own
    "is everything already here?" check (collect_live_second_set), or a
    SECOND call to collect_cli_slot_or_base_folder for a different slot
    (e.g. Afternoon's own fetch, right after Morning's fallback already ran)
    - would re-scan BASE_FOLDER, find the exact same physical files still
    sitting there dated today, and silently treat them as a fresh, distinct
    second set. That is exactly what happened on 2026-09-02 12:24: CLI's
    Morning backup wasn't available yet, Morning fell back to BASE_FOLDER and
    read 5 files - but left them in place - so Live Snapshot's own check ran
    two minutes later, found the SAME 5 files "already present and dated
    today", and reused them instead of downloading anything. The run
    "succeeded" but compared a snapshot against itself (zero diff, no real
    Live data).

    list_files_in()/_scan_and_choose() only ever look at files directly
    inside BASE_FOLDER (os.listdir, not recursive) - so moving a file into
    ANY subfolder makes it invisible to every later scan this run, without
    needing to touch _scan_and_choose() itself. Returns a NEW
    {segment: path} dict pointing at the moved-to locations - callers must
    use these for anything afterward (e.g. this run's own backup copy),
    never the original BASE_FOLDER paths (which no longer exist)."""
    hold_dir = os.path.join(BASE_FOLDER, ".consumed", tag, datetime.datetime.now().strftime("%H%M%S"))
    os.makedirs(hold_dir, exist_ok=True)
    moved = {}
    for seg, src in chosen_paths.items():
        dest = os.path.join(hold_dir, os.path.basename(src))
        try:
            shutil.move(src, dest)
            moved[seg] = dest
        except Exception as e:
            print(f"  [warn] could not move consumed {tag} {seg} file out of {BASE_FOLDER}: {e}")
            moved[seg] = src
    print(f"Moved these {len(moved)} file(s) out of {BASE_FOLDER} now that they've been used "
          f"as {tag} - the next fetch step needs genuinely fresh/distinct files, not these again.\n")
    return moved


def collect_cli_slot_or_base_folder(slot_name, date_str):
    """Try CLI's own <slot_name> backup first. If CLI hasn't produced that
    slot yet (or files were manually dropped into BASE_FOLDER instead), fall
    back to checking BASE_FOLDER directly for today's dated files. No
    download is triggered here (only Live Snapshot downloads) - this only
    reuses whatever's already on disk.

    IMPORTANT: whenever the BASE_FOLDER fallback is used, the files it finds
    are immediately moved out of BASE_FOLDER (see _consume_base_folder_files)
    so nothing later in this same run can mistake them for a second, distinct
    set of today's files."""
    try:
        return collect_from_cli_slot(slot_name, date_str)
    except AbortRun as cli_err:
        print(f"CLI {slot_name} backup not found - checking {BASE_FOLDER} for today's files instead...\n")
        chosen, chosen_paths, missing = _scan_and_choose(BASE_FOLDER, today_only=True)
        if missing:
            raise AbortRun(
                f"{cli_err}\n\n"
                f"  Also checked {BASE_FOLDER} directly - still missing: " + ", ".join(missing) + "."
            )
        print(f"Using {slot_name} files found directly in {BASE_FOLDER}.\n")
        chosen_paths = _consume_base_folder_files(chosen_paths, slot_name.lower())
        return chosen, chosen_paths


# ---------------------------------------------------------------------------
# Live Snapshot - check BASE_FOLDER first, only download what's missing, via
# the CONSOLE'S OWN shared downloader pipelines (not a standalone subprocess).
# ---------------------------------------------------------------------------

def _run_nmass_ncdex_download():
    """CM/FO/CO/NCDEX via the console's shared NMASS/NCDEX downloader -
    core.pipelines.nmass_download.stream_nmass_download(pipe="r1r2"). Same
    shared login lock as CTCL/MS15/PROBABLE (core.dl_lock) so this can never
    collide with one of those running at the same time. Its captcha is
    answered through the dashboard's existing modal, scoped to BASE_FOLDER
    (see api.py's /r1r2/captcha/pending + /r1r2/captcha/answer, and
    dashboard.html's captchaStartPoller() r1r2 block)."""
    from core.pipelines.nmass_download import stream_nmass_download
    ok = False
    for ev in stream_nmass_download(pipe="r1r2"):
        if ev["type"] == "log":
            print(ev["msg"])
        elif ev["type"] == "done":
            ok = bool(ev.get("ok"))
            if not ok:
                raise AbortRun(f"NSCCL/NCDEX download failed: {ev.get('msg', '')}")
        elif ev["type"] == "error":
            raise AbortRun(f"NSCCL/NCDEX download failed: {ev.get('msg', '')}")
    if not ok:
        raise AbortRun("NSCCL/NCDEX download did not report success - see the log above.")


def _run_mcx_tcs_bancs_download():
    """MCX via sa_codes/mcx_deposit_downloader.py's download_mcx_deposit() -
    the SAME function/session SA's "MCX Deposit" and CLI's "MCX Web
    Allocation" buttons already use. MCX_DEPOSIT_CALLER="r1r2" so the
    dashboard's RSA token badge shows the right box name - the token modal
    itself is already generic across callers (/sa/token/*), no new endpoint
    needed for this leg."""
    sys.path.insert(0, os.path.join(_REPO, "sa_codes"))
    import importlib
    mdd = importlib.import_module("mcx_deposit_downloader")

    os.environ["MCX_DEPOSIT_CALLER"] = "r1r2"
    try:
        xlsx_path = mdd.download_mcx_deposit(output_dir=BASE_FOLDER)
    except mdd.MCXDepositBusyError as e:
        raise AbortRun(
            f"MCX TCS BANCS download is in use right now (by '{e.owner}') - try again shortly."
        )
    except Exception as e:
        raise AbortRun(f"MCX TCS BANCS download failed: {e}")
    if not xlsx_path:
        raise AbortRun("MCX TCS BANCS download failed - see the log above.")
    print(f"MCX downloaded: {os.path.basename(xlsx_path)}\n")


def collect_live_second_set():
    """Mode == 'live': check BASE_FOLDER itself first - if all 5 segments
    are already sitting there (dated today), use them as-is and skip every
    login entirely. Only missing legs trigger their own download."""
    print(f"Checking for already-present files in {BASE_FOLDER}...\n")
    chosen, chosen_paths, missing = _scan_and_choose(BASE_FOLDER, today_only=True)

    if not missing:
        print("All 5 files already present and dated today - skipping NSCCL/NCDEX "
              "and TCS BANCS login entirely.\n")
        return chosen, chosen_paths

    print(f"Missing: {', '.join(missing)} - need to download.\n")

    need_nmass = any(s in missing for s in ("CM", "FO", "CO", "NCDEX"))
    need_mcx = "MCX" in missing

    if need_nmass:
        print("Fetching CM/FO/CO/NCDEX...\n")
        _run_nmass_ncdex_download()
    else:
        print("CM/FO/CO/NCDEX already present and dated today - skipping NSCCL/NCDEX login.\n")

    if need_mcx:
        print("\nFetching MCX...\n")
        _run_mcx_tcs_bancs_download()
    else:
        print("\nMCX already present and dated today - skipping TCS BANCS login.\n")

    print(f"\nRe-scanning {BASE_FOLDER} after download...\n")
    chosen, chosen_paths, missing = _scan_and_choose(BASE_FOLDER, today_only=True)
    if missing:
        raise AbortRun(
            "Still missing file(s) after downloading: " + ", ".join(missing) + "\n"
            f"  Check {BASE_FOLDER} by hand - the download step above may have partially failed."
        )
    return chosen, chosen_paths


# ---------------------------------------------------------------------------
# Standalone Download / MCX Deposit buttons — same idea as CLI's dedicated
# "📥 Download" / "📥 MCX Web Allocation" buttons: fetch just that one leg
# on its own (for retrying a failed download without running the whole
# reconciliation), skipping the login entirely if today's file is already
# sitting in BASE_FOLDER. No reconciliation, no master update, no email.
# ---------------------------------------------------------------------------

def download_nmass_ncdex_only():
    """CM/FO/CO/NCDEX only — reuses the same console-shared downloader Live
    Snapshot uses (core.pipelines.nmass_download, pipe="r1r2"), so this can
    never collide with CTCL/MS15/PROBABLE running at the same time."""
    os.makedirs(BASE_FOLDER, exist_ok=True)
    print(f"Checking for CM/FO/CO/NCDEX already present today in {BASE_FOLDER}...\n")
    _, _, missing = _scan_and_choose(BASE_FOLDER, today_only=True)
    need = [s for s in ("CM", "FO", "CO", "NCDEX") if s in missing]
    if not need:
        print("CM/FO/CO/NCDEX already present and dated today — nothing to do.\n")
        return
    print(f"Missing: {', '.join(need)} — fetching...\n")
    try:
        _run_nmass_ncdex_download()
    except AbortRun as e:
        print("\n" + "!" * 70)
        print("DOWNLOAD FAILED — nothing was changed")
        print("!" * 70)
        print(f"\n{e}\n")
        raise SystemExit(1)
    print("\nDone.")


def download_mcx_only():
    """MCX leg only — reuses sa_codes/mcx_deposit_downloader.py's
    download_mcx_deposit(), the same function SA's "MCX Deposit" and CLI's
    "MCX Web Allocation" buttons already use."""
    os.makedirs(BASE_FOLDER, exist_ok=True)
    print(f"Checking for an MCX file already present today in {BASE_FOLDER}...\n")
    _, _, missing = _scan_and_choose(BASE_FOLDER, today_only=True)
    if "MCX" not in missing:
        print("MCX file already present and dated today — nothing to do.\n")
        return
    print("Fetching MCX...\n")
    try:
        _run_mcx_tcs_bancs_download()
    except AbortRun as e:
        print("\n" + "!" * 70)
        print("DOWNLOAD FAILED — nothing was changed")
        print("!" * 70)
        print(f"\n{e}\n")
        raise SystemExit(1)
    print("\nDone.")


# ---------------------------------------------------------------------------
# "15-min Backup" mode - second set comes from ONE chosen Margin Shortage
# (15-min) backup run instead of a CLI slot or a live download.
#
# Two things make this mode different from the other three:
#   1. Which backup run to use is chosen BY THE OPERATOR at click time (the
#      dashboard shows a tickbox list of today's runs), not derived from the
#      clock - so the same day can be re-run against several different 15-min
#      snapshots. That's also why it has no once-per-day limit.
#   2. Only CM/FO/CO/NCDEX come from that backup. MS15's own MCX file is the
#      MCX **90%** file, which is a different report from the MCX **Deposit**
#      file this reconciliation needs - using it would silently compare the
#      wrong numbers. So the 5th file is fetched exactly the way Live Snapshot
#      fetches it (mcx_deposit_downloader.download_mcx_deposit), reusing
#      today's copy in BASE_FOLDER if one is already there.
# ---------------------------------------------------------------------------

MS15_BACKUP_SEGMENTS = ["CM", "FO", "CO", "NCDEX"]


def _ms15_backup_date_dir(date_str):
    """date_str is DD-MM-YYYY (this module's convention); MS15 names its backup
    folders YYYY-MM-DD, so translate."""
    dd, mm, yyyy = date_str.split("-")
    return os.path.join(MS15_FOLDER, MS15_BACKUP_ROOT, f"{yyyy}-{mm}-{dd}")


def list_ms15_backup_runs(date_str=None, verbose=False):
    """Today's Margin Shortage backup runs, newest first - feeds the picker.

    Each entry: {run, folder, label, segments, missing, usable}.
    `label` is the run's own file timestamp ("11.45"), so the operator picks by
    the time they actually care about rather than by run number alone.
    Never raises - an unreadable/absent folder just yields an empty list.
    """
    date_str = date_str or datetime.datetime.now().strftime("%d-%m-%Y")
    date_dir = _ms15_backup_date_dir(date_str)
    out = []
    if not os.path.isdir(date_dir):
        return out

    for name in sorted(os.listdir(date_dir)):
        full = os.path.join(date_dir, name)
        if not os.path.isdir(full) or not name.isdigit():
            continue
        try:
            chosen, chosen_paths, _ = _scan_and_choose(full, verbose=verbose)
        except Exception:
            continue
        missing = [s for s in MS15_BACKUP_SEGMENTS if s not in chosen_paths]
        label = ""
        if "CM" in chosen_paths:
            try:
                _, label, _ = parse_file_stamp(chosen_paths["CM"])
            except Exception:
                label = ""
        out.append({
            "run": int(name),
            "folder": full,
            "label": label,
            "segments": {s: os.path.basename(chosen_paths[s])
                         for s in MS15_BACKUP_SEGMENTS if s in chosen_paths},
            "clients": {s: len(chosen[s]) for s in MS15_BACKUP_SEGMENTS if s in chosen},
            "missing": missing,
            "usable": not missing,
        })

    out.sort(key=lambda r: r["run"], reverse=True)
    return out


def collect_ms15_backup_second_set(run_no, date_str):
    """Second set for run_mode == 'ms15backup'.

    CM/FO/CO/NCDEX are COPIED out of the chosen MS15 backup run into a private
    holding folder under BASE_FOLDER before anything else touches them - MS15's
    backups are that pipeline's own audit trail and must never be moved,
    renamed or consumed by this one. MCX Deposit is fetched separately.
    """
    date_dir = _ms15_backup_date_dir(date_str)
    if not os.path.isdir(date_dir):
        raise AbortRun(
            f"No Margin Shortage (15-min) backups found for {date_str}.\n"
            f"  Looked under:\n    {date_dir}\n"
            f"  The 15-min pipeline creates one folder per run there; run it first."
        )

    run_dir = os.path.join(date_dir, f"{int(run_no):02d}")
    if not os.path.isdir(run_dir):
        available = sorted(n for n in os.listdir(date_dir)
                           if n.isdigit() and os.path.isdir(os.path.join(date_dir, n)))
        raise AbortRun(
            f"Margin Shortage run {int(run_no):02d} has no backup folder for {date_str}.\n"
            f"  Looked for:\n    {run_dir}\n"
            f"  Runs available that day: {', '.join(available) or '(none)'}"
        )

    print(f"Margin Shortage backup run {int(run_no):02d} ({date_str}): scanning {run_dir}\n")
    chosen, chosen_paths, _ = _scan_and_choose(run_dir)
    print()

    missing = [s for s in MS15_BACKUP_SEGMENTS if s not in chosen_paths]
    if missing:
        raise AbortRun(
            f"Margin Shortage run {int(run_no):02d} backup is missing file(s) for: "
            + ", ".join(missing) + "\n"
            f"  Folder checked:\n    {run_dir}\n"
            "  CM, FO, CO and NCDEX must all be present in that backup run."
        )

    # Copy (never move) the 4 files into this run's own holding folder.
    hold_dir = os.path.join(BASE_FOLDER, ".ms15pick",
                            f"{date_str}_run{int(run_no):02d}_"
                            + datetime.datetime.now().strftime("%H%M%S"))
    os.makedirs(hold_dir, exist_ok=True)
    seg_totals, seg_paths = {}, {}
    for seg in MS15_BACKUP_SEGMENTS:
        src = chosen_paths[seg]
        dest = os.path.join(hold_dir, os.path.basename(src))
        try:
            shutil.copy2(src, dest)
        except Exception as e:
            raise AbortRun(f"Could not copy {seg} out of the Margin Shortage backup: {e}")
        seg_totals[seg] = chosen[seg]
        seg_paths[seg] = dest
    print(f"Copied 4 file(s) out of the 15-min backup into:\n  {hold_dir}\n"
          f"  (the 15-min backup itself is left untouched)\n")

    # ── MCX Deposit — deliberately NOT from the MS15 backup ────────────────
    print("MCX: the 15-min backup holds the MCX 90% file, which is a different "
          "report from the MCX Deposit file this reconciliation needs — "
          "fetching the Deposit file instead.\n")
    mcx_chosen, mcx_paths, mcx_missing = _scan_and_choose(BASE_FOLDER, today_only=True)
    if "MCX" in mcx_missing:
        print("No MCX Deposit file for today in the base folder — downloading it.\n")
        _run_mcx_tcs_bancs_download()
        mcx_chosen, mcx_paths, mcx_missing = _scan_and_choose(BASE_FOLDER, today_only=True)
        if "MCX" in mcx_missing:
            raise AbortRun(
                "MCX Deposit file still missing after the download step.\n"
                f"  Check {BASE_FOLDER} by hand — the download above may have partially failed."
            )
    else:
        print("MCX Deposit file for today already present — skipping the TCS BANCS login.\n")

    seg_totals["MCX"] = mcx_chosen["MCX"]
    seg_paths["MCX"] = mcx_paths["MCX"]
    return seg_totals, seg_paths


# ---------------------------------------------------------------------------
# meta - tracks which date the current master belongs to, which modes have
# already run today, and today's morning column labels (the fixed baseline)
# ---------------------------------------------------------------------------

def meta_path(folder):
    return os.path.join(folder, META_FILENAME)


def load_meta(folder):
    p = meta_path(folder)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_meta(folder, meta):
    with open(meta_path(folder), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def rotate_if_new_day(folder, today_str):
    meta = load_meta(folder)
    old_date = meta.get("date")
    master_path = os.path.join(folder, MASTER_FILENAME)

    if old_date and old_date != today_str and os.path.exists(master_path):
        dest_dir = os.path.join(folder, BACKUP_FOLDER_NAME, old_date)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, MASTER_FILENAME)
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"allocation_master__{n}.xlsx")
            n += 1
        shutil.move(master_path, dest)
        print(f"New day detected ({old_date} -> {today_str}). "
              f"Archived yesterday's master to:\n  {dest}\n"
              f"Starting a fresh master for {today_str}.\n")
        meta = {}

    if meta.get("date") != today_str:
        meta = {"date": today_str, "modes_done": [], "morning_labels": {}}
        save_meta(folder, meta)

    return meta


# ---------------------------------------------------------------------------
# master file (xlsx) - load / update / save with formatting
# ---------------------------------------------------------------------------

def load_master_xlsx(path):
    import openpyxl
    if not os.path.exists(path):
        return [], {}
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    fieldnames = [str(c) if c is not None else "" for c in next(rows_iter)]
    rows = {}
    for row in rows_iter:
        d = {fieldnames[i]: row[i] for i in range(len(fieldnames)) if i < len(row)}
        code = d.get("Client Code")
        if code:
            rows[code] = d
    return fieldnames, rows


def write_master_xlsx(path, ordered_cols, rows_by_client, diff_cols):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Allocation Master"

    HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
    HEADER_FONT = Font(color="FFFFFF", bold=True)
    POS_FILL = PatternFill("solid", fgColor="C6EFCE")
    NEG_FILL = PatternFill("solid", fgColor="FFC7CE")
    NET_FONT = Font(bold=True)

    ws.append(ordered_cols)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for client in sorted(rows_by_client):
        row = rows_by_client[client]
        ws.append([row.get(c, 0) for c in ordered_cols])

    amount_cols = [c for c in ordered_cols if c != "Client Code"]
    col_idx = {c: i + 1 for i, c in enumerate(ordered_cols)}
    for c in amount_cols:
        ci = col_idx[c]
        is_diff = c in diff_cols
        is_net = c.startswith("Net_Diff")
        for r in range(2, ws.max_row + 1):
            cell = ws.cell(row=r, column=ci)
            cell.number_format = "#,##0.00"
            if is_net:
                cell.font = NET_FONT
            if is_diff:
                val = cell.value or 0
                if val > 0:
                    cell.fill = POS_FILL
                elif val < 0:
                    cell.fill = NEG_FILL

    for i, c in enumerate(ordered_cols, start=1):
        maxlen = len(str(c))
        for r in range(2, min(ws.max_row, 500) + 1):
            v = ws.cell(row=r, column=i).value
            maxlen = max(maxlen, len(f"{v}"))
        ws.column_dimensions[get_column_letter(i)].width = min(max(maxlen + 2, 10), 32)

    ws.freeze_panes = "B2"

    tmp_path = path + ".tmp.xlsx"
    try:
        wb.save(tmp_path)
        os.replace(tmp_path, path)
    except PermissionError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise AbortRun(
            f"Could not save '{os.path.basename(path)}' - it is most likely open "
            f"in Excel right now.\n  Close it and run the script again."
        )


def update_master(folder, meta, seg_totals, seg_label, is_morning):
    """The master never accumulates every run of the day - each non-Morning
    run REPLACES whatever the previous run's columns were, so at any point
    the master holds exactly the fixed Morning baseline (5 cols) plus the
    latest run's snapshot (5 cols) plus that run's diffs."""
    master_path = os.path.join(folder, MASTER_FILENAME)
    fieldnames, master_rows = load_master_xlsx(master_path)

    new_cols = [f"{seg}_{seg_label}" for seg in SEGMENT_ORDER]
    diff_cols = []
    if not is_morning:
        morning_labels = meta["morning_labels"]
        diff_cols = [f"{seg}_Diff_{morning_labels[seg]}_to_{seg_label}" for seg in SEGMENT_ORDER]
        diff_cols.append(f"Net_Diff_Morning_to_{seg_label}")

        keep = {"Client Code"} | {f"{seg}_{morning_labels[seg]}" for seg in SEGMENT_ORDER}
        fieldnames = [c for c in fieldnames if c in keep]
        for row in master_rows.values():
            for c in list(row.keys()):
                if c not in keep:
                    del row[c]

    all_clients = set(master_rows)
    for totals in seg_totals.values():
        all_clients.update(totals)

    for client in all_clients:
        row = master_rows.setdefault(client, {"Client Code": client})
        net_diff = 0.0
        for seg in SEGMENT_ORDER:
            new_val = round(seg_totals.get(seg, {}).get(client, 0.0), 2)
            row[f"{seg}_{seg_label}"] = new_val
            if not is_morning:
                morning_col = f"{seg}_{meta['morning_labels'][seg]}"
                prev_val = to_float(row.get(morning_col, 0))
                seg_diff = round(new_val - prev_val, 2)
                row[f"{seg}_Diff_{meta['morning_labels'][seg]}_to_{seg_label}"] = seg_diff
                net_diff += seg_diff
        if not is_morning:
            row[f"Net_Diff_Morning_to_{seg_label}"] = round(net_diff, 2)

    ordered = ["Client Code"]
    for c in fieldnames + new_cols + diff_cols:
        if c not in ordered:
            ordered.append(c)

    all_diff_cols_seen = [c for c in ordered if "_Diff_" in c or c.startswith("Net_Diff")]
    write_master_xlsx(master_path, ordered, master_rows, all_diff_cols_seen)
    return master_path


# ---------------------------------------------------------------------------
# per-run backup - Backup\<date>\<HH-MM-SS>\morning\ + <mode>\ + master copy
# ---------------------------------------------------------------------------

def _backup_run_files(today_str, mode_key, morning_paths, seg_paths, master_path):
    time_label = datetime.datetime.now().strftime("%H-%M-%S")
    run_dir = os.path.join(BASE_FOLDER, BACKUP_FOLDER_NAME, today_str, time_label)
    mode_dir = os.path.join(run_dir, mode_key)
    os.makedirs(mode_dir, exist_ok=True)

    if morning_paths:
        morning_dir = os.path.join(run_dir, "morning")
        os.makedirs(morning_dir, exist_ok=True)
        for seg, src in morning_paths.items():
            try:
                shutil.copy2(src, os.path.join(morning_dir, os.path.basename(src)))
            except Exception as e:
                print(f"  [warn] could not back up Morning {seg} file: {e}")
    else:
        print("  [note] Morning files unavailable for this run's backup snapshot - skipped.\n")

    for seg, src in seg_paths.items():
        dest = os.path.join(mode_dir, os.path.basename(src))
        try:
            if mode_key == "live":
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(src, dest)
            else:
                shutil.copy2(src, dest)
        except Exception as e:
            print(f"  [warn] could not back up {mode_key} {seg} file: {e}")

    try:
        shutil.copy2(master_path, os.path.join(run_dir, MASTER_FILENAME))
    except Exception as e:
        print(f"  [warn] could not copy master snapshot into backup: {e}")

    print(f"This run's files backed up to:\n  {run_dir}\n"
          f"  (morning\\ + {mode_key}\\ + {MASTER_FILENAME})\n")


# ---------------------------------------------------------------------------
# email
# ---------------------------------------------------------------------------

def send_email_with_retry(subject, body, to_list, cc_list, attachment_path):
    from email.message import EmailMessage
    while True:
        try:
            msg = EmailMessage()
            msg["From"] = SENDER_EMAIL
            msg["To"] = ", ".join(to_list)
            if cc_list:
                msg["Cc"] = ", ".join(cc_list)
            msg["Subject"] = subject
            msg.set_content(body, subtype="html")
            with open(attachment_path, "rb") as f:
                msg.add_attachment(f.read(), maintype="application",
                                   subtype="octet-stream",
                                   filename=os.path.basename(attachment_path))
            all_recipients = to_list + cc_list
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60) as server:
                server.ehlo(); server.starttls(); server.ehlo()
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.send_message(msg, to_addrs=all_recipients)
            print("Email sent successfully.")
            break
        except Exception as e:
            print(f"Email failed: {e}")
            print("Retrying in 10 seconds...")
            time.sleep(10)


_EMAIL_MODE_LABEL = {
    "live": "Live",
    "afternoon": "Afternoon",
    "evening": "Evening",
    "ms15backup": "15-min Backup",
}


def build_email(mode, date_str, seg_label, morning_label, master_path):
    mode = _EMAIL_MODE_LABEL.get(mode, mode.capitalize())
    subject = "Segment-Wise Collateral Movement Report"
    body = f"""
Dear Team,<br><br>

Please find attached the <b>Segment-wise Collateral Movement Report</b> for {date_str}.<br><br>

<b>Note:</b><br>
This run compares this <b>{mode} ({seg_label})</b> snapshot against
this morning's fixed baseline ({morning_label}), across all five segments -
<b>CM, FO, CO, NCDEX and MCX</b>. For every client:
<ul>
    <li>Each <b>&lt;Segment&gt;_Diff_{morning_label}_to_{seg_label}</b>
        column shows how much that segment moved since this morning ({morning_label})
        (positive = allocated, negative = deallocated).</li>
    <li><b>Net_Diff_Morning_to_{seg_label}</b> is the sum of all five segment
        diffs - the client's total net movement since this morning.</li>
</ul>

Diff cells are shaded green for net allocation and red for net deallocation,
so movement is visible at a glance.<br><br>

Best Regards,<br>
<b>Kartavya Dhillon</b><br>
Master Capital Services Ltd<br>
011-66331000 | Ext. 84
"""
    return subject, body


# ---------------------------------------------------------------------------

def main(run_mode, ms15_run=None):
    run_mode = run_mode.lower().strip()
    if run_mode not in ("afternoon", "evening", "live", "ms15backup"):
        raise AbortRun(f"run_mode must be 'afternoon', 'evening', 'live' or "
                       f"'ms15backup', got: {run_mode!r}")
    if run_mode == "ms15backup" and not ms15_run:
        raise AbortRun("15-min Backup mode needs a run number — pick one from the list.")

    slot_name = {"live": "Live", "ms15backup": f"15-min Backup (run {int(ms15_run):02d})"}.get(
        run_mode, run_mode.capitalize())

    print("=" * 70)
    print("Segment-wise Collateral Movement Report")
    print("=" * 70)
    print(f"CLI folder : {CLI_FOLDER}")
    print(f"Base folder: {BASE_FOLDER}")
    print(f"Run mode   : {slot_name}\n")

    os.makedirs(BASE_FOLDER, exist_ok=True)

    today_str = datetime.datetime.now().strftime("%d-%m-%Y")
    meta = rotate_if_new_day(BASE_FOLDER, today_str)

    # Live Snapshot and 15-min Backup have no once-per-day limit - only
    # Afternoon/Evening do. 15-min Backup is deliberately unlimited because
    # each run targets a DIFFERENT 15-min snapshot, so re-running it is the
    # normal case, not a mistake.
    if run_mode not in ("live", "ms15backup") and run_mode in meta.get("modes_done", []):
        raise AbortRun(
            f"{slot_name} has already been run today ({today_str}).\n"
            f"  Each of Afternoon/Evening can only run once per day.\n"
            f"  (Delete {META_FILENAME} in {BASE_FOLDER} to force a re-run for testing.)"
        )

    # ── Step 1: Morning baseline - fetched every run (this run's own backup
    # snapshot needs a copy of it), only WRITTEN to the master once per day ──
    print("Fetching Morning CLI backup...\n")
    try:
        morning_totals, morning_paths = collect_cli_slot_or_base_folder("Morning", today_str)
    except AbortRun:
        if "morning" not in meta.get("modes_done", []):
            raise
        print("  [warn] could not re-fetch Morning CLI backup for this run's "
              "backup snapshot - master already has today's Morning baseline, "
              "continuing without it.\n")
        morning_totals, morning_paths = None, None

    if "morning" not in meta.get("modes_done", []):
        _, morning_label, note = parse_file_stamp(morning_paths["CM"])
        print(f"Morning stamped as {morning_label}"
              + ("" if note == "filename" else f"  (time taken from {note})") + "\n")

        update_master(BASE_FOLDER, meta, morning_totals, morning_label, is_morning=True)
        meta["modes_done"].append("morning")
        meta["morning_labels"] = {seg: morning_label for seg in SEGMENT_ORDER}
        save_meta(BASE_FOLDER, meta)
        print("Morning baseline written to master.\n")
    else:
        print("Morning baseline already in today's master - reusing it.\n")

    # ── Step 2: this run's second snapshot ───────────────────────────────
    print(f"Fetching {slot_name} snapshot...\n")
    if run_mode == "live":
        seg_totals, seg_paths = collect_live_second_set()
    elif run_mode == "ms15backup":
        seg_totals, seg_paths = collect_ms15_backup_second_set(ms15_run, today_str)
    else:
        seg_totals, seg_paths = collect_cli_slot_or_base_folder(slot_name, today_str)
    _, seg_label, note = parse_file_stamp(seg_paths["CM"])
    print(f"{slot_name} stamped as {seg_label}"
          + ("" if note == "filename" else f"  (time taken from {note})") + "\n")

    master_path = update_master(BASE_FOLDER, meta, seg_totals, seg_label, is_morning=False)
    if run_mode not in ("live", "ms15backup"):
        meta["modes_done"].append(run_mode)
    save_meta(BASE_FOLDER, meta)

    print(f"\n{slot_name} snapshot + diff-vs-Morning written to:\n  {master_path}\n")

    _backup_run_files(today_str, run_mode, morning_paths, seg_paths, master_path)

    # ── Step 3: email ─────────────────────────────────────────────────────
    if not EMAIL_TO:
        print("No R1R2_EMAIL_TO configured - skipping email.\n"
              "  Set R1R2_EMAIL_TO in .env to enable it.")
    else:
        morning_label_for_email = meta["morning_labels"]["CM"]
        subject, body = build_email(run_mode, today_str, seg_label, morning_label_for_email, master_path)
        print(f"Sending email...\n  Subject: {subject}\n  To: {EMAIL_TO}\n  Cc: {EMAIL_CC}\n")
        send_email_with_retry(subject, body, EMAIL_TO, EMAIL_CC, master_path)

    print("\n" + "=" * 70)
    print("Done.")
    print("=" * 70)


def _run(mode, ms15_run=None):
    try:
        main(mode, ms15_run=ms15_run)
    except AbortRun as e:
        print("\n" + "!" * 70)
        print("RUN STOPPED - nothing was changed")
        print("!" * 70)
        print(f"\n{e}\n")
        raise SystemExit(1)


def run_r1r2_live():
    _run("live")


def run_r1r2_afternoon():
    _run("afternoon")


def run_r1r2_evening():
    _run("evening")


def run_r1r2_ms15_backup(run_no):
    """4th mode: Morning baseline exactly as always (CLI's backup), second set
    from the Margin Shortage (15-min) backup run the operator ticked. No
    once-per-day limit."""
    _run("ms15backup", ms15_run=int(run_no))


if __name__ == "__main__":
    mode = sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else "live"
    _ms15_run = sys.argv[2].strip() if len(sys.argv) > 2 and sys.argv[2].strip() else None
    _run(mode, ms15_run=int(_ms15_run) if _ms15_run else None)
