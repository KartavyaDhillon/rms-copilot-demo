"""
allocation_report_src.py  --  the whole Allocation Report job, in one file
===========================================================================

Console copy of the stand-alone ALLOCATION_REPORT.py. Parsing / report logic
is unchanged; only the SETTINGS differ so it lives in the repo (git push /
pull to any machine):

  * folders  -> the console's data folders (no C:\\KARTAVYA\\... paths)
  * secrets  -> read from environment / .env (never stored in this file)

The console engine (core/pipelines/allocation_report.py) imports this module
and overrides the folders / mail settings at run time from the same env keys,
so the defaults below are what you get when running it by hand too.

ENV KEYS (put the secrets in .env.local, they are not committed)
    ALLOC_REPORT_FOLDER   work/state/output folder
                          (default <repo>/data/scheduler/ALLOCATION REPORT)
    CLI_FOLDER            CLI prudential folder; backups are read from
                          <CLI_FOLDER>/BACKUPFILES AUTOMATED
                          (default D:\\CLI_PRUDENTIAL_LIMIT)
    ALLOC_POP_SERVER / ALLOC_POP_PORT / ALLOC_POP_USER / ALLOC_POP_PASS
    ALLOC_SMTP_SERVER / ALLOC_SMTP_PORT / ALLOC_SMTP_USER / ALLOC_SMTP_PASS
    ALLOC_MAIL_TO / ALLOC_MAIL_CC   comma separated

WHAT IT DOES, IN ORDER
----------------------
 1. Fetches the allocation emails over POP3 and builds the usual three
    files: Allocation_Logs, DOWNWARDS_Logs, UPWARDS_Logs.
 2. Goes to BACKUP_ROOT, finds the date folder for the day this run
    STARTED, opens its OLDEST time sub-folder (the ~08:00 one), and takes
    the 5 opening-balance files out of it.
 3. Builds the Allocation_Log_Report from the log + those opening
    balances.
 4. Sends ONE email with THREE attachments:
        DOWNWARDS_Logs ... .xlsx
        UPWARDS_Logs ... .xlsx
        Allocation_Log_Report ... .xlsx
    The raw Allocation_Logs file is written to disk and backed up, but is
    NOT attached -- the report replaces it.
 5. Backs everything up under BASE_FOLDER and clears the working folder.

allocation_master.csv is NOT touched.

HOW IT IS RUN
-------------
    python allocation_report_src.py --scheduled       <- nightly
    python allocation_report_src.py                   <- manual, whole day so far
    python allocation_report_src.py --until 15:00     <- manual, day up to 15:00
    python allocation_report_src.py --scheduled --as-of "2026-08-12 23:59"

Only --scheduled moves the day marker forward, so manual runs can be run
as often as you like without shortening the night's report.

WHICH DAY'S BACKUP FOLDER IT USES
---------------------------------
The date comes from when the run STARTED, not the clock at the moment it
reaches the folder. A 23:59 run spends 2-3 minutes downloading mail and
would otherwise arrive after midnight and look in tomorrow's folder.

Requires:  pip install openpyxl
"""

import argparse
import csv
import glob
import json
import logging
import os
import poplib
import re
import shutil
import smtplib
import ssl
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, time as dtime
from email import encoders
from email import message_from_bytes
from email.header import decode_header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

import openpyxl
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


# =============================================================================
#  SETTINGS  --  console data folders (override with env, see docstring)
# =============================================================================

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _env(name, default=""):
    return (os.environ.get(name) or "").strip() or default


def _env_list(name, default=None):
    raw = _env(name)
    if not raw:
        return list(default or [])
    return [x.strip() for x in re.split(r"[;,]", raw) if x.strip()]


# ONE common folder for everything this script produces: downloads,
# outputs, backups, state files and the temporary working folder.
BASE_FOLDER = _env("ALLOC_REPORT_FOLDER",
                   os.path.join(_REPO, "data", "scheduler", "ALLOCATION REPORT"))

# Root of the daily backup tree holding the opening-balance files.
# Inside it: date folders named DD-MM-YYYY, and inside each of those,
# time sub-folders named HH-MM-SS. The OLDEST time sub-folder is used.
BACKUP_ROOT = os.path.join(_env("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT"),
                           "BACKUPFILES AUTOMATED")

# Files larger than this are never opened while hunting for the MCX file
# (RISK_REPORT and Kambala sit in the same folder and are huge).
MAX_SCAN_MB = 5

# =============================================================================
#  Everything below is the working code. The mail server, recipients and
#  parsing rules are further down in their own clearly marked sections.
# =============================================================================

RUN_START = datetime.now()          # fixed once, up front -- see docstring above
WORK_FOLDER = os.path.join(BASE_FOLDER, "analysis_work")


# =============================================================================
#  PART 1  --  MAIL FETCHING, PARSING AND THE THREE LOG FILES
# =============================================================================

POP_SERVER = _env("ALLOC_POP_SERVER", "pop9.netcore.co.in")

POP_PORT = int(_env("ALLOC_POP_PORT", "995"))

EMAIL_ID = _env("ALLOC_POP_USER", "sandeep.kumar2@mastertrust.co.in")

# secret: set ALLOC_POP_PASS in .env.local (MAIL_PASSWORD still honoured)
PASSWORD = _env("ALLOC_POP_PASS") or _env("MAIL_PASSWORD")

POP3_TIMEOUT_SECONDS = 120

SMTP_SERVER = _env("ALLOC_SMTP_SERVER", "smtp3.netcore.co.in")

SMTP_PORT = int(_env("ALLOC_SMTP_PORT", "587"))

SMTP_EMAIL = _env("ALLOC_SMTP_USER", "kartavya.dhillon@mastertrust.co.in")

# secret: set ALLOC_SMTP_PASS in .env.local (SMTP_PASSWORD still honoured)
SMTP_PASSWORD = _env("ALLOC_SMTP_PASS") or _env("SMTP_PASSWORD")

EMAIL_TO = _env_list("ALLOC_MAIL_TO", ["rms_del@mastertrust.co.in"])

EMAIL_CC = _env_list("ALLOC_MAIL_CC", [])

SENDER_FILTER = "support@mastertrust.co.in"

SUBJECT_MASTER = "MASTER-Allocation_Logs"

SUBJECT_MASTERTRUST = "MASTERTRUST-Allocation_Logs"

# ---------------------------------------------------------------------
# MCX (MCXCCL) allocation logs -- a 4th attachment that started arriving
# separately from the NSE ones.
#
# WARNING, and the reason routing is done by FILENAME and not by subject:
# the MCX email reuses the *exact same* subject line as the
# DOWNWARDS/UPWARDS email ("MASTERTRUST-Allocation_Logs"). Checked
# against 2,612 historical emails in Email_Mapping_Log.xlsx: that subject
# had always meant "2 attachments, plain CSV". So subject-based routing
# would hand this JSON log to the CSV parser and quietly produce garbage.
# The attachment name is the only reliable signal, hence MCX_FILE_PREFIX.
# ---------------------------------------------------------------------
MCX_FILE_PREFIX = "MCX"          # attachment names look like MCX_alloc_inqu20_55-11-Aug-2026.log

MCX_SEGMENT = "MCX"              # what these rows are labelled as in the report

BACKFILL_MODE = False

BACKFILL_START_DATE = "2026-06-24"  # YYYY-MM-DD -- set to your real start date

BASE_DIR = BASE_FOLDER   # every other path below derives from this

DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloaded_files")

ALLOC_CATEGORY_DIR = os.path.join(DOWNLOAD_DIR, "ALLOCATION LOGS")

DOWNUP_CATEGORY_DIR = os.path.join(DOWNLOAD_DIR, "DOWNWARDS_UPWARDS LOGS")

MCX_CATEGORY_DIR = os.path.join(DOWNLOAD_DIR, "MCX ALLOCATION LOGS")

OUTPUT_DIR = os.path.join(BASE_DIR, "output_files")

BACKUP_DIR = os.path.join(BASE_DIR, "BACKUPFILES AUTOMATED")

STATE_FILE = os.path.join(BASE_DIR, "processed_uidls.json")

WINDOW_STATE_FILE = os.path.join(BASE_DIR, "window_state.json")

OPEN_WINDOW_FILE = os.path.join(BASE_DIR, "open_window_uidls.json")

UNPARSED_LOG = os.path.join(BASE_DIR, "unparsed_lines.log")

RUN_LOG = os.path.join(BASE_DIR, "run_log.txt")

EMAIL_MAPPING_XLSX = os.path.join(BASE_DIR, "Email_Mapping_Log.xlsx")

MASTER_LOG_COLUMNS = [
    "RequestTime", "SequenceNo", "LogLevel", "MsgId", "RequestType",
    "RecordIndex", "AllocationDate", "Segment", "MemberCode", "BranchCode",
    "DealerSubCode", "ClientCode", "AccountType", "AllocationAmount",
    "Reserved1", "Reserved2", "Reserved3", "Reserved4", "Reserved5", "Reserved6",
    "ActionType", "ResponseStatus", "ResponseCode", "ResponseTime",
    # NEW -- filled in from the LATER statusInquiry Response for this
    # same MsgId, which confirms downstream processing of this exact
    # record (matched by client/amount/action, not just position).
    # Blank until/unless a matching statusInquiry Response shows up.
    "InquiryStatusCode", "InquiryReceivedTime",
    "SourceFile", "EmailReceivedTime",
]

RECORD_COLUMNS = [
    "AllocationDate", "Segment", "MemberCode", "BranchCode", "DealerSubCode",
    "ClientCode", "AccountType", "AllocationAmount",
    "Reserved1", "Reserved2", "Reserved3", "Reserved4", "Reserved5", "Reserved6",
    "ActionType", "SourceFile", "EmailReceivedTime",
]

EMAIL_MAPPING_COLUMNS = [
    "RunTimestamp", "EmailUIDL", "EmailDate", "EmailSubject", "EmailFrom",
    "AttachmentCount", "AttachmentNames", "SavedFolder", "FileType",
    "Status", "RowsExtracted", "ErrorMessage",
]

EMAIL_SIGNATURE = (
    "\n\nBest Regards,\n"
    "Kartavya Dhillon\n"
    "Master Capital Services Ltd\n"
    "\U0001F4DE 011-66331000 | Ext. 84"
)

def setup_logging():
    os.makedirs(BASE_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(RUN_LOG, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

def call_with_retry(func, what, permanent_exceptions=(), initial_wait=15, max_wait=300):
    """
    Calls func() and returns its result.

    On any exception NOT in permanent_exceptions: logs a warning and
    retries FOREVER with capped exponential backoff. This is the
    "unlimited retries on connection/server errors" behavior.

    On a permanent_exceptions match (bad credentials, rejected recipient,
    etc.): logs an error and re-raises immediately. Retrying a wrong
    password forever risks the mail provider locking the account -- that
    is not a risk worth taking just to satisfy "unlimited retries".
    """
    attempt = 0
    wait = initial_wait
    while True:
        attempt += 1
        try:
            return func()
        except permanent_exceptions as e:
            logging.error("%s failed with a permanent error (NOT retrying): %s", what, e)
            raise
        except Exception as e:
            logging.warning("%s failed on attempt %d (%s) -- retrying in %ds", what, attempt, e, wait)
            time.sleep(wait)
            wait = min(wait * 2, max_wait)

def get_processed_uidls():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_processed_uidls(uidl_set):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(uidl_set), f)

def get_window_state():
    if os.path.exists(WINDOW_STATE_FILE):
        with open(WINDOW_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        try:
            return datetime.fromisoformat(str(data["last_window_end"]))
        except (KeyError, ValueError):
            return None
    return None

def save_window_state(when):
    with open(WINDOW_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_window_end": when.isoformat()}, f)

def get_open_window_uidls():
    """UIDs already processed during the currently-open day.

    These are NOT skipped by the mailbox scan -- that is the whole point.
    A manual mid-day run re-downloads them so its output covers the full
    day rather than only the slice since the previous run.
    """
    if os.path.exists(OPEN_WINDOW_FILE):
        try:
            with open(OPEN_WINDOW_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f).get("uids", []))
        except (json.JSONDecodeError, AttributeError, TypeError):
            logging.warning("%s was unreadable -- starting the open window empty.", OPEN_WINDOW_FILE)
    return set()

def save_open_window_uidls(uidl_set, window_start):
    with open(OPEN_WINDOW_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "window_start": window_start.isoformat(),
            "uids": sorted(uidl_set),
        }, f, indent=2)

def close_open_window():
    """Fold the open day's UIDs into the permanent set and reset.

    Called ONLY by the scheduled end-of-day run. After this, those emails
    are never fetched again -- which is what keeps the mailbox scan from
    growing without bound.
    """
    open_uids = get_open_window_uidls()
    if open_uids:
        permanent = get_processed_uidls()
        permanent |= open_uids
        save_processed_uidls(permanent)
        logging.info("Day closed: %d UID(s) moved into the permanent processed list.", len(open_uids))
    if os.path.exists(OPEN_WINDOW_FILE):
        os.remove(OPEN_WINDOW_FILE)

def decode_mime_words(value):
    if not value:
        return ""
    parts = decode_header(value)
    result = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            result += text.decode(enc or "utf-8", errors="replace")
        else:
            result += text
    return result

def safe_float(value):
    # The NSE log delivers every field as text, but the MCX log delivers
    # "amt" as a real JSON number -- so this has to accept both. Without
    # this, .strip() below raises on a float and the whole MCX file fails.
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return float(value)
    value = (value or "").strip()
    if value == "":
        return ""
    try:
        return float(value)
    except ValueError:
        return value

def split_allocation_record(record_str, expected_len=15):
    fields = record_str.split(",")
    if len(fields) < expected_len:
        fields += [""] * (expected_len - len(fields))
    return fields[:expected_len]

_LOG_ENTRY_SPLIT_RE = re.compile(
    r"(?=^\d{2}:\d{2}:\d{2}\s+\d+\s+\[\w\]\s+:\s+)",
    re.MULTILINE,
)

_LOG_ENTRY_RE = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<seq>\d+)\s+\[(?P<level>\w)\]\s+:\s+"
    r"(?P<kind>Allocation|statusInquiry)\s+(?P<logtype>Request|Response)\s*(?P<rest>.*)$",
    re.DOTALL,
)

_RESPONSE_MSGID_RE = re.compile(r"^(\S+)\s+(\{.*\})$", re.DOTALL)

def parse_master_log_file(filepath, source_filename, email_received_time, unparsed_lines,
                          shared_rows_by_msgid=None, pending_inquiries=None):
    """
    Parses the timestamp + JSON style log (MASTER-Allocation_Logs).

    A Response line does NOT become its own row. Its status/code/time are
    written onto every row belonging to the Request with the same MsgId.

    statusInquiry Responses almost always re-send the SAME record as the
    original Allocation Request plus one new field: a downstream
    confirmation StatusCode. That code (and when it arrived) is matched back
    to the original row by MsgId + record content and stamped onto it via
    InquiryStatusCode / InquiryReceivedTime.

    CROSS-FILE MATCHING
    -------------------
    The confirmation usually arrives 1-3 minutes AFTER the request, which
    routinely puts it in the NEXT 10-minute log file. So when the caller
    passes shared_rows_by_msgid and pending_inquiries, this function does
    NOT stamp confirmations itself -- it registers its rows in the shared
    index and drops the inquiry records into the pending list. The caller
    then runs apply_inquiry_confirmations() ONCE after every file is parsed.

    Called without those two arguments it behaves as single-file matching.
    """
    rows = []
    pending_by_msgid = {}   # msgId -> rows still awaiting their Allocation Response

    # msgId -> ALL rows ever created for it. Shared across the whole run
    # when the caller supplies it, otherwise local to this one file.
    rows_by_msgid = shared_rows_by_msgid if shared_rows_by_msgid is not None else {}
    defer_inquiries = pending_inquiries is not None

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    chunks = [c.strip() for c in _LOG_ENTRY_SPLIT_RE.split(content) if c.strip()]

    for chunk in chunks:
        m = _LOG_ENTRY_RE.match(chunk)
        if not m:
            unparsed_lines.append(chunk)
            continue

        log_time = m.group("time")
        seq_no = m.group("seq")
        level = m.group("level")
        kind = m.group("kind")
        logtype = m.group("logtype")
        rest = m.group("rest").strip()

        # A statusInquiry Request is pure metadata -- it only names the
        # msgId being asked about and carries no allocation data. Skip it.
        if kind == "statusInquiry" and logtype == "Request":
            continue

        msg_id = ""
        json_str = rest
        if logtype == "Response":
            resp_match = _RESPONSE_MSGID_RE.match(rest)
            if resp_match:
                msg_id = resp_match.group(1)
                json_str = resp_match.group(2)

        try:
            payload = json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            unparsed_lines.append(chunk)
            continue

        if logtype == "Request":
            data = payload.get("data") or {}
            msg_id = data.get("msgId", "")
            request_type = data.get("requestType", "")
            alloc_str = data.get("allocationRequest") or ""
            records = alloc_str.split("^") if alloc_str else [""]

            new_rows = []
            for idx, rec in enumerate(records, start=1):
                fields = split_allocation_record(rec)
                row = {
                    "RequestTime": log_time, "SequenceNo": seq_no, "LogLevel": level,
                    "MsgId": msg_id, "RequestType": request_type,
                    "RecordIndex": idx,
                    "AllocationDate": fields[0], "Segment": fields[1],
                    "MemberCode": fields[2], "BranchCode": fields[3],
                    "DealerSubCode": fields[4], "ClientCode": fields[5],
                    "AccountType": fields[6], "AllocationAmount": safe_float(fields[7]),
                    "Reserved1": fields[8], "Reserved2": fields[9], "Reserved3": fields[10],
                    "Reserved4": fields[11], "Reserved5": fields[12], "Reserved6": fields[13],
                    "ActionType": fields[14],
                    "ResponseStatus": "", "ResponseCode": "", "ResponseTime": "",
                    "InquiryStatusCode": "", "InquiryReceivedTime": "",
                    "SourceFile": source_filename, "EmailReceivedTime": email_received_time,
                }
                rows.append(row)
                new_rows.append(row)

            pending_by_msgid.setdefault(msg_id, []).extend(new_rows)
            rows_by_msgid.setdefault(msg_id, []).extend(new_rows)

        elif kind == "statusInquiry":  # statusInquiry Response
            data = payload.get("data") or {}
            inquiry_str = data.get("inquiryResponse") or "" if isinstance(data, dict) else ""

            if not inquiry_str:
                # A successful inquiry with an empty body is normal, not an error.
                continue

            if defer_inquiries:
                # Cross-file mode: hand it to the caller, which matches
                # everything at the end via apply_inquiry_confirmations().
                pending_inquiries.append((msg_id, inquiry_str, log_time))
                continue

            target_rows = rows_by_msgid.get(msg_id)
            if not target_rows:
                logging.debug("statusInquiry Response for msgId=%s has no matching Request in this file -- skipped.", msg_id)
                continue

            # Match each inquiry record back to ITS row by content
            # (client/amount/action), not just position.
            unclaimed = list(target_rows)
            for rec in inquiry_str.split("^"):
                # 16 fields here, not 15: the inquiry format appends a
                # status code after ActionType.
                fields = split_allocation_record(rec, expected_len=16)
                inquiry_amount = safe_float(fields[7])
                match = None
                for row in unclaimed:
                    if (row["ClientCode"] == fields[5]
                            and row["ActionType"] == fields[14]
                            and row["Segment"] == fields[1]
                            and isinstance(row["AllocationAmount"], (int, float))
                            and isinstance(inquiry_amount, (int, float))
                            and abs(row["AllocationAmount"] - inquiry_amount) < 0.01):
                        match = row
                        break
                if match is not None:
                    match["InquiryStatusCode"] = fields[15]
                    match["InquiryReceivedTime"] = log_time
                    unclaimed.remove(match)
                else:
                    logging.debug(
                        "statusInquiry record for msgId=%s (client=%s) didn't match any row in the "
                        "original Request -- left unstamped rather than guessed.", msg_id, fields[5],
                    )

        else:  # Allocation Response
            status = payload.get("status", "")
            messages = payload.get("messages") or {}
            code = messages.get("code", "") if isinstance(messages, dict) else ""

            target_rows = pending_by_msgid.pop(msg_id, None)
            if target_rows:
                for row in target_rows:
                    row["ResponseStatus"] = status
                    row["ResponseCode"] = code
                    row["ResponseTime"] = log_time
            else:
                unparsed_lines.append(f"[Orphan Response, no matching Request in this file] {chunk}")

    return rows

def apply_inquiry_confirmations(rows_by_msgid, pending_inquiries):
    """Stamps InquiryStatusCode / InquiryReceivedTime across ALL of a run's
    files at once.

    Run after every attachment in the window has been parsed, so a
    confirmation that landed in one 10-minute file can still reach the
    request it belongs to in an earlier (or later) file.

    Touches ONLY those two columns.
    """
    stamped = 0
    no_request = 0
    no_match = 0
    still_processing = 0
    unmatched_details = []

    for msg_id, inquiry_str, log_time in pending_inquiries:
        # While the exchange is still working on a request it answers with
        # plain text, e.g. "Request Under Process" (code 01050102). That is
        # a normal status, NOT allocation data and NOT an error.
        if "," not in inquiry_str:
            still_processing += 1
            logging.debug("msgId %s: exchange replied '%s' -- still processing, no records yet.",
                          msg_id, inquiry_str.strip()[:60])
            continue

        target_rows = rows_by_msgid.get(msg_id)
        if not target_rows:
            # Request belongs to a file outside this window entirely.
            no_request += 1
            continue

        # Only rows not already confirmed are eligible: repeat inquiries for
        # the same msgId are harmless -- first confirmation wins.
        unclaimed = [r for r in target_rows if not r["InquiryStatusCode"]]
        if not unclaimed:
            continue

        for rec in inquiry_str.split("^"):
            fields = split_allocation_record(rec, expected_len=16)
            inquiry_amount = safe_float(fields[7])
            match = None
            for row in unclaimed:
                if (row["ClientCode"] == fields[5]
                        and row["ActionType"] == fields[14]
                        and row["Segment"] == fields[1]
                        and isinstance(row["AllocationAmount"], (int, float))
                        and isinstance(inquiry_amount, (int, float))
                        and abs(row["AllocationAmount"] - inquiry_amount) < 0.01):
                    match = row
                    break
            if match is not None:
                match["InquiryStatusCode"] = fields[15]
                match["InquiryReceivedTime"] = log_time
                unclaimed.remove(match)
                stamped += 1
            else:
                no_match += 1
                near = [r for r in target_rows if r["ClientCode"] == fields[5]]
                unmatched_details.append({
                    "MsgId": msg_id,
                    "InquiryTime": log_time,
                    "ClientCode": fields[5],
                    "Segment": fields[1],
                    "ActionType": fields[14],
                    "InquiryAmount": inquiry_amount,
                    "StatusCode": fields[15],
                    "SameClientInRequest": [
                        f"{r['Segment']}/{r['ActionType']}/{r['AllocationAmount']}"
                        f"{' [already confirmed]' if r['InquiryStatusCode'] else ''}"
                        for r in near
                    ] or ["(this client is not in that request at all)"],
                })

    logging.info(
        "statusInquiry matching (across all files in this window): %d row(s) confirmed. "
        "%d still being processed by the exchange (no records sent yet); "
        "%d referenced a request outside this window; "
        "%d record(s) found no matching row.",
        stamped, still_processing, no_request, no_match,
    )

    # Write the unmatched ones out so they can actually be looked at.
    if unmatched_details:
        path = os.path.join(BASE_DIR, "unmatched_inquiries.log")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 78}\n")
                f.write(f"Run {datetime.now():%Y-%m-%d %H:%M:%S}"
                        f"  --  {len(unmatched_details)} unmatched statusInquiry record(s)\n")
                f.write("These kept a blank InquiryStatusCode rather than being guessed at.\n")
                f.write(f"{'=' * 78}\n")
                for d in unmatched_details:
                    f.write(f"\nmsgId {d['MsgId']} at {d['InquiryTime']}\n")
                    f.write(f"  inquiry said : {d['Segment']} / {d['ClientCode']} / "
                            f"{d['ActionType']} / {d['InquiryAmount']} / code {d['StatusCode']}\n")
                    f.write(f"  request had  : {', '.join(d['SameClientInRequest'])}\n")
            logging.info("Details of the %d unmatched record(s) written to %s",
                         len(unmatched_details), path)
        except Exception as e:
            logging.warning("Couldn't write %s: %s", path, e)

    return stamped

# =====================================================================
# MCX (MCXCCL) allocation logs
# =====================================================================
#
# Same line shape as the NSE log, but the payload differs:
#   NSE : ... [I] : Allocation Request  {"data":{"msgId":..,"requestType":"I",
#                       "allocationRequest":"a,b,c,...^a,b,c,..."}}
#   MCX : ... [I] : MCXCCL Allocation Request  {"userId":..,"msgId":..,
#                       "allocationRequest":[{"curDate":...}, {...}]}
#
# The 15 keys in each MCX object are exactly the 15 positional fields of an
# NSE record, in the same order, so every MCX row fits MASTER_LOG_COLUMNS.
#
# "segment" must NOT be copied straight through: inside the MCX JSON it
# always reads "CO", which in this report already means the NSE commodity
# segment (964 clients appear in both logs). Segment is forced to
# MCX_SEGMENT. RequestType / InquiryStatusCode / InquiryReceivedTime stay
# blank for MCX rows (no such source fields).
#
# Response mapping: NSE {"status":"SUCCESS","messages":{"code":".."}}
#                   MCX {"status":"Success","message":"0100"}
# =====================================================================

_MCX_ENTRY_RE = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<seq>\d+)\s+\[(?P<level>\w)\]\s+:\s+"
    r"MCXCCL\s+(?P<kind>Allocation|statusInquiry)\s+(?P<logtype>Request|Response)\s*(?P<rest>.*)$",
    re.DOTALL,
)

def _mcx_response_code(payload):
    """MCX puts the code in "message"; NSE puts it in "messages"."message".

    Accepts either shape so a future format change on their side doesn't
    silently blank the column.
    """
    msg = payload.get("message")
    if isinstance(msg, str) and msg:
        return msg
    msgs = payload.get("messages")
    if isinstance(msgs, dict):
        return msgs.get("code", "")
    if isinstance(msgs, str):
        return msgs
    return ""

def sniff_mcx_vs_nse(filepath):
    """A file the FILENAME calls MCX (starts with "MCX_") is not always
    actually MCX content (e.g. 12-Aug-2026: "MCX_alloc_inqu18-55-..." carried
    490 ordinary NSE entries). This peeks at the content BEFORE deciding
    which parser handles the file.

    Returns:
      "mcx"     -- contains the literal "MCXCCL" marker -- use the MCX parser.
      "nse"     -- no "MCXCCL", but >=90% of chunks match the NSE line shape
                   -- use parse_master_log_file instead.
      "unknown" -- neither clearly -- leave to the MCX parser and
                   unparsed_lines.log exactly as before.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return "unknown"

    if "MCXCCL" in content:
        return "mcx"

    chunks = [c.strip() for c in _LOG_ENTRY_SPLIT_RE.split(content) if c.strip()]
    if not chunks:
        return "unknown"

    nse_matches = sum(1 for c in chunks if _LOG_ENTRY_RE.match(c))
    if nse_matches / len(chunks) >= 0.9:
        return "nse"
    return "unknown"

def parse_mcx_log_file(filepath, source_filename, email_received_time, unparsed_lines):
    """Parses an MCXCCL allocation log into ordinary MASTER_LOG_COLUMNS rows.

    Returns rows that slot straight into master_rows alongside the NSE
    ones -- they just carry Segment = MCX.

    FORWARD COMPATIBILITY (never seen in real MCX data yet, handled so they
    get picked up the moment they appear):
      requestType   -- checked at the envelope and the per-record level.
      statusInquiry -- a Request is skipped quietly; a Response is matched
                       by msgId only and logged loudly the first time.
    """
    rows = []
    pending_by_msgid = {}
    rows_by_msgid = {}   # persists for the whole file, incl. after a normal
                          # Response, in case a later statusInquiry Response
                          # for the same msgId needs to find these rows too.
    counts_checked = counts_bad = 0

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    chunks = [c.strip() for c in _LOG_ENTRY_SPLIT_RE.split(content) if c.strip()]

    for chunk in chunks:
        m = _MCX_ENTRY_RE.match(chunk)
        if not m:
            unparsed_lines.append(chunk)
            continue

        log_time = m.group("time")
        seq_no = m.group("seq")
        level = m.group("level")
        kind = m.group("kind")
        logtype = m.group("logtype")
        rest = m.group("rest").strip()

        # Not seen in any real MCX file yet -- if it starts arriving, treat
        # it like NSE does: pure metadata naming an msgId, nothing to stamp.
        if kind == "statusInquiry" and logtype == "Request":
            continue

        msg_id = ""
        json_str = rest
        if logtype == "Response":
            resp_match = _RESPONSE_MSGID_RE.match(rest)
            if resp_match:
                msg_id = resp_match.group(1)
                json_str = resp_match.group(2)

        try:
            payload = json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            unparsed_lines.append(chunk)
            continue

        if kind == "statusInquiry":  # statusInquiry Response -- no real sample seen yet
            target_rows = rows_by_msgid.get(msg_id)
            if not target_rows:
                logging.debug(
                    "MCX statusInquiry Response for msgId=%s has no matching Request "
                    "in this file -- skipped.", msg_id,
                )
                continue
            code = _mcx_response_code(payload)
            logging.warning(
                "MCX statusInquiry Response seen for the FIRST TIME (msgId=%s, file=%s) "
                "-- stamping InquiryStatusCode on all %d row(s) for this msgId. This code "
                "path has no real sample data yet -- please verify the mapping now that "
                "one has actually shown up.", msg_id, source_filename, len(target_rows),
            )
            for row in target_rows:
                row["InquiryStatusCode"] = code
                row["InquiryReceivedTime"] = log_time
            continue

        if logtype == "Request":
            # msgId sits at the TOP level here, not under "data".
            msg_id = payload.get("msgId", "")
            envelope_request_type = payload.get("requestType", "")
            records = payload.get("allocationRequest") or []
            if not isinstance(records, list):
                unparsed_lines.append(chunk)
                continue

            # The envelope declares how many records it is carrying -- the
            # one thing that would reveal a truncated attachment.
            declared = payload.get("totalRecordsCount")
            if declared not in (None, ""):
                counts_checked += 1
                try:
                    if int(declared) != len(records):
                        counts_bad += 1
                        logging.warning(
                            "MCX msgId %s declares totalRecordsCount=%s but carries %d record(s) "
                            "-- parsing what is actually there (%s).",
                            msg_id, declared, len(records), source_filename,
                        )
                except (TypeError, ValueError):
                    pass

            new_rows = []
            for idx, rec in enumerate(records, start=1):
                if not isinstance(rec, dict):
                    unparsed_lines.append(f"[MCX record not an object] {rec!r}")
                    continue
                row = {
                    "RequestTime": log_time, "SequenceNo": seq_no, "LogLevel": level,
                    "MsgId": msg_id,
                    "RequestType": envelope_request_type or rec.get("requestType", ""),
                    "RecordIndex": idx,
                    "AllocationDate": rec.get("curDate", ""),
                    # NOT rec["segment"] -- see the note above.
                    "Segment": MCX_SEGMENT,
                    "MemberCode": rec.get("cmCode", ""),
                    "BranchCode": rec.get("tmCode", ""),
                    "DealerSubCode": rec.get("cpCode", ""),
                    "ClientCode": rec.get("cliCode", ""),
                    "AccountType": rec.get("accType", ""),
                    "AllocationAmount": safe_float(rec.get("amt", "")),
                    "Reserved1": rec.get("filler1", ""), "Reserved2": rec.get("filler2", ""),
                    "Reserved3": rec.get("filler3", ""), "Reserved4": rec.get("filler4", ""),
                    "Reserved5": rec.get("filler5", ""), "Reserved6": rec.get("filler6", ""),
                    "ActionType": rec.get("action", ""),
                    "ResponseStatus": "", "ResponseCode": "", "ResponseTime": "",
                    "InquiryStatusCode": "", "InquiryReceivedTime": "",
                    "SourceFile": source_filename, "EmailReceivedTime": email_received_time,
                }
                rows.append(row)
                new_rows.append(row)

            pending_by_msgid.setdefault(msg_id, []).extend(new_rows)
            rows_by_msgid.setdefault(msg_id, []).extend(new_rows)

        else:  # MCXCCL Allocation Response
            status = payload.get("status", "")
            code = _mcx_response_code(payload)

            target_rows = pending_by_msgid.pop(msg_id, None)
            if target_rows:
                for row in target_rows:
                    row["ResponseStatus"] = status
                    row["ResponseCode"] = code
                    row["ResponseTime"] = log_time
            else:
                unparsed_lines.append(
                    f"[MCX Orphan Response, no matching Request in this file] {chunk}"
                )

    if pending_by_msgid:
        logging.warning(
            "MCX %s: %d request(s) never got a Response in this file "
            "(their ResponseStatus/Code/Time stay blank).",
            source_filename, len(pending_by_msgid),
        )
    logging.info(
        "MCX %s: %d record(s) parsed, %d envelope record-count(s) checked, %d mismatch(es).",
        source_filename, len(rows), counts_checked, counts_bad,
    )
    return rows

def parse_record_csv_file(filepath, source_filename, email_received_time, unparsed_lines):
    """Parses plain comma-separated record files (MASTERTRUST-Allocation_Logs)."""
    rows = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]

    for line in lines:
        fields = split_allocation_record(line)
        if all(v == "" for v in fields):
            unparsed_lines.append(line)
            continue
        rows.append({
            "AllocationDate": fields[0], "Segment": fields[1],
            "MemberCode": fields[2], "BranchCode": fields[3],
            "DealerSubCode": fields[4], "ClientCode": fields[5],
            "AccountType": fields[6], "AllocationAmount": safe_float(fields[7]),
            "Reserved1": fields[8], "Reserved2": fields[9], "Reserved3": fields[10],
            "Reserved4": fields[11], "Reserved5": fields[12], "Reserved6": fields[13],
            "ActionType": fields[14],
            "SourceFile": source_filename, "EmailReceivedTime": email_received_time,
        })

    return rows

def _existing_header(ws):
    header = [cell.value for cell in ws[1]]
    header = [h for h in header if h is not None and h != ""]
    return header or None

def write_fresh_xlsx(filepath, columns, rows):
    """Always writes a brand-new workbook -- used for the 3 per-window
    data files, which are never appended across windows/days anymore."""
    if not rows:
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(columns)
    for i, col_name in enumerate(columns, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(14, len(col_name) + 2)
    for row in rows:
        ws.append([row.get(col, "") for col in columns])
    wb.save(filepath)

def append_mapping_row(filepath, columns, row_dict):
    """The email mapping log is the ONE file that stays cumulative
    forever, across every window/day/backfill run -- it's the audit
    trail. Header-aware so a future column change can't silently
    misalign against an older file."""
    if os.path.exists(filepath):
        wb = load_workbook(filepath)
        ws = wb["Detail"] if "Detail" in wb.sheetnames else wb.active
        effective_columns = _existing_header(ws) or columns
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Detail"
        ws.append(columns)
        for i, col_name in enumerate(columns, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(16, len(col_name) + 2)
        effective_columns = columns

    ws.append([row_dict.get(col, "") for col in effective_columns])
    _rebuild_summary_sheet(wb, ws, effective_columns)
    wb.save(filepath)

def _rebuild_summary_sheet(wb, detail_ws, columns):
    if "Summary" in wb.sheetnames:
        wb.remove(wb["Summary"])
    summary_ws = wb.create_sheet("Summary")

    subj_idx = columns.index("EmailSubject") + 1
    status_idx = columns.index("Status") + 1

    total = 0
    subject_counts = {}
    status_counts = {}

    for r in range(2, detail_ws.max_row + 1):
        subj = detail_ws.cell(row=r, column=subj_idx).value
        status = detail_ws.cell(row=r, column=status_idx).value
        if subj is None and status is None:
            continue
        total += 1
        subject_counts[subj] = subject_counts.get(subj, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1

    summary_ws.append(["Metric", "Value"])
    summary_ws.append(["Total Emails Logged", total])
    summary_ws.append(["Last Updated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    summary_ws.append([])
    summary_ws.append(["By Subject", "Count"])
    for subj, cnt in subject_counts.items():
        summary_ws.append([subj, cnt])
    summary_ws.append([])
    summary_ws.append(["By Status", "Count"])
    for status, cnt in status_counts.items():
        summary_ws.append([status, cnt])

    for col_letter, width in (("A", 34), ("B", 16)):
        summary_ws.column_dimensions[col_letter].width = width

def _build_ssl_context():
    """
    Netcore's POP3 server (pop9.netcore.co.in) negotiates TLS using a
    Diffie-Hellman group too small for the default OpenSSL security level
    (SECLEVEL=2) shipped with Python 3.10+/OpenSSL 3.x, causing:
    ssl.SSLError: [SSL: DH_KEY_TOO_SMALL] dh key too small.

    Dropping to SECLEVEL=1 restores older, more permissive behavior for
    this connection only (stopgap; the durable fix is Netcore serving
    >=2048-bit DH params).
    """
    context = ssl.create_default_context()
    context.set_ciphers("DEFAULT@SECLEVEL=1")
    return context

def _do_connect_pop3():
    context = _build_ssl_context()
    conn = poplib.POP3_SSL(POP_SERVER, POP_PORT, context=context, timeout=POP3_TIMEOUT_SECONDS)
    conn.user(EMAIL_ID)
    conn.pass_(PASSWORD)
    return conn

def connect_pop3():
    if not PASSWORD:
        raise RuntimeError("POP3 password is not set -- put ALLOC_POP_PASS in .env.local")
    return call_with_retry(
        _do_connect_pop3,
        what="POP3 connect",
        permanent_exceptions=(poplib.error_proto,),
    )

def build_email_subject(window_label, window_end):
    base = f"Allocation Log Report - {window_end.strftime('%d-%b-%Y')}"
    # The nightly scheduled report keeps the exact original subject, so
    # any existing inbox rules/filters on it keep working. Only manual
    # mid-day runs get a suffix.
    if window_label.startswith("Intraday"):
        return f"{base} ({window_label})"
    return base

def build_email_body(window_label, window_start, window_end, stats):
    # Kept intentionally short -- no summary stats, no attachment list,
    # no automated-email disclaimer.
    body = (
        "Dear Team,\n\n"
        "Please find attached the allocation log files processed for the period "
        f"{window_start.strftime('%d-%b-%Y %H:%M')} to {window_end.strftime('%d-%b-%Y %H:%M')}.\n"
    )
    return body + EMAIL_SIGNATURE

def _do_send_email(subject, body, attachment_paths):
    msg = MIMEMultipart()
    msg["From"] = SMTP_EMAIL
    msg["To"] = ", ".join(EMAIL_TO)
    if EMAIL_CC:
        msg["Cc"] = ", ".join(EMAIL_CC)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    for path in attachment_paths:
        if not os.path.exists(path):
            continue
        part = MIMEBase("application", "octet-stream")
        with open(path, "rb") as f:
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
        msg.attach(part)

    all_recipients = list(EMAIL_TO) + list(EMAIL_CC)
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, all_recipients, msg.as_string())

    logging.info("Email sent to %s (cc: %s) with %d attachment(s).", EMAIL_TO, EMAIL_CC, len(attachment_paths))

def send_email_with_attachments(subject, body, attachment_paths):
    if not EMAIL_TO:
        logging.warning("EMAIL_TO is empty -- skipping email send.")
        return
    if not attachment_paths:
        logging.info("No output files were generated for this window -- skipping email send.")
        return
    if not SMTP_PASSWORD:
        raise RuntimeError("SMTP password is not set -- put ALLOC_SMTP_PASS in .env.local")

    call_with_retry(
        lambda: _do_send_email(subject, body, attachment_paths),
        what="SMTP send",
        permanent_exceptions=(
            smtplib.SMTPAuthenticationError,
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
        ),
    )

_CATEGORY_DIR = {
    "MASTER": ALLOC_CATEGORY_DIR,
    "MASTERTRUST": DOWNUP_CATEGORY_DIR,
    "MCX": MCX_CATEGORY_DIR,
}

_CATEGORY_SUFFIX = {
    "MASTER": "alloc_logs",
    "MASTERTRUST": "down_up_logs",
    "MCX": "mcx_alloc_logs",
}

def is_mcx_attachment(name):
    """True for the MCXCCL allocation log, judged by filename only.

    Deliberately name-based: the MCX email's subject is indistinguishable
    from the DOWNWARDS/UPWARDS one, so the subject cannot be trusted.
    """
    # Split on BOTH separators -- this is also called on names that came
    # straight out of an email header, which can carry a Windows path.
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return base.upper().startswith(MCX_FILE_PREFIX.upper())

def attachment_names(msg):
    """Filenames of an email's attachments, without decoding the payloads.

    Used to decide which category folder an email's files belong in
    BEFORE that folder is created (and before anything is written).
    """
    names = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        fn = part.get_filename()
        if fn:
            names.append(_sanitize_filename(decode_mime_words(fn)))
    return names

def make_category_folder(file_type, when):
    """
    ALLOCATION LOGS/<date>_<HH.MM>_alloc_logs/            (1 file goes here)
    DOWNWARDS_UPWARDS LOGS/<date>_<HH.MM>_down_up_logs/   (2 files go here)
    MCX ALLOCATION LOGS/<date>_<HH.MM>_mcx_alloc_logs/    (1 file goes here)
    """
    time_str = when.strftime("%Y-%m-%d_%H.%M")
    base = os.path.join(_CATEGORY_DIR[file_type], f"{time_str}_{_CATEGORY_SUFFIX[file_type]}")
    folder = base
    n = 1
    while os.path.exists(folder):
        n += 1
        folder = f"{base}_{n}"
    os.makedirs(folder, exist_ok=True)
    return folder

_WINDOWS_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*]')

def _sanitize_filename(name):
    """
    The source system names attachments like "alloc_inqu07:55-06-Jul-2026.log"
    -- the colon is fatal on Windows/NTFS (it is parsed as an Alternate Data
    Stream separator: a 0-byte base file, the content hidden in a stream, and
    every backup copy failing with WinError 87). Replacing reserved
    characters before ever touching disk fixes both.
    """
    return _WINDOWS_INVALID_CHARS_RE.sub("-", name)

def save_attachment(part, folder):
    """
    Saves one attachment part to disk, with byte-count diagnostics for
    "0 KB file" reports: logs the in-memory payload size AND the on-disk size
    right after writing + fsync.
    """
    filename = part.get_filename()
    if not filename:
        return None
    filename = decode_mime_words(filename)
    filename = _sanitize_filename(filename)

    payload = part.get_payload(decode=True)
    if payload is None:
        logging.warning("Attachment part %r has no decodable payload -- skipped.", filename)
        return None
    if len(payload) == 0:
        logging.warning("Attachment %r decoded to 0 bytes from the email itself -- writing anyway.", filename)

    filepath = os.path.join(folder, filename)
    if os.path.exists(filepath):
        name, ext = os.path.splitext(filename)
        filepath = os.path.join(folder, f"{name}_{int(time.time() * 1000)}{ext}")

    with open(filepath, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())

    on_disk_size = os.path.getsize(filepath)
    logging.info("Saved attachment %s (%d bytes in memory, %d bytes on disk)", filepath, len(payload), on_disk_size)
    if on_disk_size == 0 and len(payload) > 0:
        logging.error(
            "Attachment %s: payload was %d bytes in memory but the file on disk is 0 bytes -- "
            "this points to a filesystem/permissions/AV issue, not a parsing bug.",
            filepath, len(payload),
        )

    return filepath

def _close_quietly(conn):
    """Close a POP3 connection without ever raising. On a dead (timed-out)
    connection quit() itself throws -- that is what crashed 28-Sep-2026."""
    try:
        conn.quit()
    except Exception:
        try:
            conn.sock.close()
        except Exception:
            pass

class _Pop3Session:
    """A POP3 connection that survives a stalled or dropped connection:
    on any timeout/reset it opens a fresh connection and repeats the
    command (up to 4 tries), instead of dying or -- worse -- carrying on
    with a dead connection and reporting an empty mailbox."""

    def __init__(self):
        self.conn = connect_pop3()

    def call(self, name, *args):
        last = None
        for attempt in range(1, 5):
            try:
                return getattr(self.conn, name)(*args)
            except poplib.error_proto:
                raise                      # the server answered -ERR: not a connection problem
            except Exception as e:
                last = e
                logging.warning("POP3 %s%s failed on attempt %d/4 (%s) -- reconnecting.",
                                name, args, attempt, e)
                _close_quietly(self.conn)
                time.sleep(min(5 * attempt, 30))
                try:
                    self.conn = _do_connect_pop3()
                except Exception as ce:
                    last = ce
                    logging.warning("Reconnect failed: %s", ce)
        raise RuntimeError(f"Mailbox scan ABORTED: POP3 {name}{args} failed 4 times ({last})")

    def close(self):
        _close_quietly(self.conn)

def fetch_window_messages(session, window_start, window_end=None):
    """Find the allocation emails DATED inside [window_start, window_end].

    Scans the mailbox from the NEWEST message backwards and stops once it
    is well past window_start, so it never depends on the processed-UID
    list. Only the headers (POP3 TOP) are read to decide; a full download
    happens only for real matches."""
    num_messages = session.call("stat")[0]
    logging.info(
        "Mailbox has %d total message(s). Scanning newest -> oldest for mail dated "
        "%s to %s; stops once messages are older than the window.",
        num_messages, window_start, window_end or "now")
    _, uidl_list, _ = session.call("uidl")
    uidl_map = {}
    for entry in uidl_list:
        parts = entry.decode(errors="replace").split(" ", 1)
        if len(parts) == 2:
            uidl_map[int(parts[0])] = parts[1]

    matches = []
    checked = downloaded = older_streak = 0
    use_top, top_worked = True, False
    t0 = time.time()

    for num in range(num_messages, 0, -1):
        checked += 1
        if checked % 50 == 0:
            logging.info("Scan progress: %d message(s) checked (newest first), %d match(es) so far -- %.0fs elapsed",
                         checked, len(matches), time.time() - t0)
        raw_lines, full = None, False
        try:
            if use_top:
                try:
                    _, raw_lines, _ = session.call("top", num, 0)
                    top_worked = True
                except poplib.error_proto as e:
                    if top_worked:
                        raise
                    use_top = False
                    logging.warning("Server does not support TOP (%s) -- reading full messages instead (slower).", e)
            if raw_lines is None:
                _, raw_lines, _ = session.call("retr", num)
                downloaded += 1
                full = True
        except poplib.error_proto as e:
            logging.error("Could not read message #%s (server refused): %s", num, e)
            continue

        msg = message_from_bytes(b"\r\n".join(raw_lines))
        from_header = decode_mime_words(msg.get("From", ""))
        subject_header = decode_mime_words(msg.get("Subject", ""))
        date_header_raw = msg.get("Date", "")
        try:
            email_dt = parsedate_to_datetime(date_header_raw)
            if email_dt.tzinfo is not None:
                email_dt = email_dt.replace(tzinfo=None)
        except (TypeError, ValueError):
            if SENDER_FILTER.lower() in from_header.lower():
                logging.warning("Could not parse Date header %r on message #%s -- skipped.", date_header_raw, num)
            continue

        if email_dt < window_start:
            older_streak += 1
            if older_streak >= 300:
                break
            continue
        older_streak = 0
        if window_end is not None and email_dt > window_end:
            continue
        if SENDER_FILTER.lower() not in from_header.lower():
            continue
        subject_lower = subject_header.lower()
        if SUBJECT_MASTERTRUST.lower() in subject_lower:
            file_type = "MASTERTRUST"
        elif SUBJECT_MASTER.lower() in subject_lower:
            file_type = "MASTER"
        else:
            continue

        if not full:
            try:
                _, raw_lines, _ = session.call("retr", num)
            except poplib.error_proto as e:
                logging.error("Could not download message #%s (server refused): %s", num, e)
                continue
            downloaded += 1
            msg = message_from_bytes(b"\r\n".join(raw_lines))

        matches.append({
            "uid": uidl_map.get(num) or f"msgno-{num}", "msg": msg,
            "date_header": date_header_raw, "email_dt": email_dt,
            "subject": subject_header, "from": from_header, "file_type": file_type,
        })

    matches.reverse()          # oldest first, like the old scan produced
    logging.info("Mailbox scan complete: %d message(s) checked, %d downloaded, %d match(es), in %.0fs.",
                 checked, downloaded, len(matches), time.time() - t0)
    return matches

def send_failure_alert(reason):
    """Best-effort 'the run FAILED' email to the sending mailbox, so a bad
    night is noticed at once. Never raises."""
    try:
        msg = MIMEText(
            "The allocation report run FAILED and produced no report.\n\n"
            f"Started : {RUN_START:%d-%b-%Y %H:%M:%S}\n"
            f"Command : {' '.join(sys.argv)}\n\n{reason}\n\n"
            "Nothing was marked done, so simply run the scheduled report again -- it "
            "closes any missed day automatically.\n")
        msg["From"] = SMTP_EMAIL
        msg["To"] = SMTP_EMAIL
        msg["Subject"] = f"FAILED - Allocation Log Report run of {RUN_START:%d-%b-%Y %H:%M}"
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, [SMTP_EMAIL], msg.as_string())
        logging.info("Failure alert emailed to %s", SMTP_EMAIL)
    except Exception:
        logging.exception("Could not send the failure alert email either.")

def plan_scheduled_runs(marker, now):
    """For a --scheduled run: which past business days still need their
    close (oldest first), and is today's live close due (evening only)?

    A day is 'missed' if the marker is still before that day's 23:59. This
    lets the scheduled run repair a night that crashed, with no extra
    command: run it at any time and it closes the missed day(s)."""
    today = now.date()
    days = []
    if marker:
        d = marker.date()
        while d < today:
            if d.weekday() < 5 and marker < datetime.combine(d, dtime(23, 59)):
                days.append(d)
            d += timedelta(days=1)
    return days, now.hour >= 20

def fetch_matching_messages(conn, processed_uidls):
    """
    Scans the whole mailbox once (old oldest->newest scan, kept for the
    backfill mode). Non-matching messages (wrong sender or subject) are
    marked processed immediately. Matching messages are returned but NOT yet
    marked processed -- that only happens after their data is actually
    written, emailed, and backed up (see process_bucket).
    """
    num_messages = conn.stat()[0]
    logging.info(
        "Mailbox has %d total message(s). POP3 has no server-side date/sender filter, so every "
        "message not already marked processed has to be downloaded individually to check it -- "
        "that's the slow part below, not a hang. Progress is logged every 50 messages.",
        num_messages,
    )
    _, uidl_list, _ = conn.uidl()

    uidl_map = {}
    for entry in uidl_list:
        decoded = entry.decode(errors="replace")
        parts = decoded.split(" ", 1)
        if len(parts) == 2:
            uidl_map[int(parts[0])] = parts[1]

    matches = []
    retrieved_count = 0
    scan_start = time.time()

    for num in range(1, num_messages + 1):
        if num % 50 == 0 or num == num_messages:
            logging.info(
                "Scan progress: %d/%d messages checked, %d retrieved, %d match(es) so far -- %.0fs elapsed",
                num, num_messages, retrieved_count, len(matches), time.time() - scan_start,
            )

        uid = uidl_map.get(num)
        if uid is None or uid in processed_uidls:
            continue

        try:
            _, lines, _ = conn.retr(num)
            retrieved_count += 1
            raw_msg = b"\r\n".join(lines)
            msg = message_from_bytes(raw_msg)
        except Exception as e:
            logging.error("Failed to fetch message #%s: %s", num, e)
            continue

        from_header = decode_mime_words(msg.get("From", ""))
        subject_header = decode_mime_words(msg.get("Subject", ""))
        date_header_raw = msg.get("Date", "")

        if SENDER_FILTER.lower() not in from_header.lower():
            processed_uidls.add(uid)
            continue

        subject_lower = subject_header.lower()
        if SUBJECT_MASTERTRUST.lower() in subject_lower:
            file_type = "MASTERTRUST"
        elif SUBJECT_MASTER.lower() in subject_lower:
            file_type = "MASTER"
        else:
            processed_uidls.add(uid)
            continue

        try:
            email_dt = parsedate_to_datetime(date_header_raw)
            if email_dt.tzinfo is not None:
                email_dt = email_dt.replace(tzinfo=None)
        except (TypeError, ValueError):
            email_dt = None
            logging.warning("Could not parse Date header %r for uid=%s -- it will be skipped from bucketing.", date_header_raw, uid)

        matches.append({
            "uid": uid, "msg": msg, "date_header": date_header_raw,
            "email_dt": email_dt, "subject": subject_header,
            "from": from_header, "file_type": file_type,
        })

    save_processed_uidls(processed_uidls)
    logging.info(
        "Mailbox scan complete: %d message(s) retrieved, %d match(es) found, in %.0fs.",
        retrieved_count, len(matches), time.time() - scan_start,
    )
    return matches

def bucket_backfill(matches, start_date, end_date):
    """date -> list of matches, pre-seeded with every weekday in range
    (so weekdays with zero emails still show up as an empty, skipped
    bucket rather than being silently absent)."""
    buckets = {}
    d = start_date
    while d <= end_date:
        if d.weekday() < 5:  # Mon=0 .. Fri=4, skip Sat/Sun
            buckets[d] = []
        d += timedelta(days=1)

    for m in matches:
        if m["email_dt"] is None:
            continue
        day = m["email_dt"].date()
        if day in buckets:
            buckets[day].append(m)

    return buckets

def bucket_live(matches, window_start, window_end):
    bucket = []
    for m in matches:
        dt = m["email_dt"]
        if dt is not None and window_start <= dt <= window_end:
            bucket.append(m)
    return bucket

def backup_and_cleanup(src_file, dest_file):
    """
    Copy a file to the backup location and delete the original
    only if the backup succeeds.
    """
    try:
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)

        shutil.copy2(src_file, dest_file)

        # Verify backup exists before deleting original
        if os.path.exists(dest_file) and os.path.getsize(dest_file) == os.path.getsize(src_file):
            os.remove(src_file)
            logging.info("Backed up and deleted original: %s", src_file)
        else:
            logging.warning("Backup verification failed. Original retained: %s", src_file)

    except Exception as e:
        logging.warning("Backup failed for %s : %s", src_file, e)

def process_bucket(matches, window_start, window_end, window_label, permanent=False):
    if not matches:
        logging.info("No matching emails for %s (%s to %s) -- nothing to do.", window_label, window_start, window_end)
        return

    ts_tag = window_end.strftime("%Y-%m-%d_%H.%M")
    master_rows, file1_rows, file2_rows = [], [], []
    saved_files = []
    unparsed_lines = []

    # Shared across EVERY attachment in this window, so a statusInquiry
    # confirmation sitting in one 10-minute file can still be matched to
    # the request it belongs to in a different file. Applied once, after
    # the parsing loop below.
    shared_rows_by_msgid = {}
    pending_inquiries = []

    for m in matches:
        # Use THIS email's own timestamp for its folder name, not the
        # window's end time.
        folder_time = m["email_dt"] or window_end

        # Decide the category folder from the attachment NAMES, before
        # anything is written. The MCX email carries the MASTERTRUST
        # subject, so m["file_type"] alone would file it under
        # DOWNWARDS_UPWARDS LOGS and hand it to the CSV parser.
        att_names = attachment_names(m["msg"])
        folder_type = m["file_type"]
        if att_names and all(is_mcx_attachment(n) for n in att_names):
            folder_type = "MCX"
        folder = make_category_folder(folder_type, folder_time)
        attachments = []
        for part in m["msg"].walk():
            if part.get_content_maintype() == "multipart":
                continue
            fp = save_attachment(part, folder)
            if fp:
                attachments.append(fp)
        saved_files.extend(attachments)

        rows_extracted = 0
        status, error_msg = "Success", ""
        try:
            # Route per ATTACHMENT, not per email. An MCX log is an MCX log
            # whichever subject line it arrives under, and pulling it out
            # first means the 2-attachment check below still measures only
            # the real DOWNWARDS/UPWARDS pair.
            mcx_att = []
            mislabeled_nse_att = []
            rest_att = []
            for fp in attachments:
                if is_mcx_attachment(fp):
                    # Filename says MCX -- but check the content before
                    # trusting it (see sniff_mcx_vs_nse()).
                    if sniff_mcx_vs_nse(fp) == "nse":
                        mislabeled_nse_att.append(fp)
                    else:
                        mcx_att.append(fp)
                else:
                    rest_att.append(fp)

            for fp in mcx_att:
                rows = parse_mcx_log_file(
                    fp, os.path.basename(fp), m["date_header"], unparsed_lines,
                )
                # MCX rows join the same list as the NSE ones -- same
                # columns, just Segment = MCX.
                master_rows.extend(rows)
                rows_extracted += len(rows)

            for fp in mislabeled_nse_att:
                logging.warning(
                    "%s is named like an MCX file but its content has no MCXCCL "
                    "marker and matches plain NSE format instead -- parsing it as "
                    "NSE so its data reaches the report, instead of quarantining "
                    "it to unparsed_lines.log.", os.path.basename(fp),
                )
                rows = parse_master_log_file(
                    fp, os.path.basename(fp), m["date_header"], unparsed_lines,
                    shared_rows_by_msgid=shared_rows_by_msgid,
                    pending_inquiries=pending_inquiries,
                )
                master_rows.extend(rows)
                rows_extracted += len(rows)

            if m["file_type"] == "MASTER":
                for fp in rest_att:
                    rows = parse_master_log_file(
                        fp, os.path.basename(fp), m["date_header"], unparsed_lines,
                        shared_rows_by_msgid=shared_rows_by_msgid,
                        pending_inquiries=pending_inquiries,
                    )
                    master_rows.extend(rows)
                    rows_extracted += len(rows)
            elif rest_att:
                sorted_att = sorted(rest_att)
                if len(sorted_att) != 2:
                    logging.warning("Expected 2 attachments in MASTERTRUST email, found %d (uid=%s)", len(sorted_att), m["uid"])
                for i, fp in enumerate(sorted_att[:2]):
                    rows = parse_record_csv_file(fp, os.path.basename(fp), m["date_header"], unparsed_lines)
                    (file1_rows if i == 0 else file2_rows).extend(rows)
                    rows_extracted += len(rows)
        except Exception as e:
            status, error_msg = "Error", str(e)
            logging.exception("Error parsing attachments for uid %s", m["uid"])

        append_mapping_row(EMAIL_MAPPING_XLSX, EMAIL_MAPPING_COLUMNS, {
            "RunTimestamp": ts_tag, "EmailUIDL": m["uid"], "EmailDate": m["date_header"],
            "EmailSubject": m["subject"], "EmailFrom": m["from"],
            "AttachmentCount": len(attachments),
            "AttachmentNames": "; ".join(os.path.basename(a) for a in attachments),
            "SavedFolder": folder, "FileType": folder_type,
            "Status": status, "RowsExtracted": rows_extracted, "ErrorMessage": error_msg,
        })

    # Every attachment in this window is now parsed, so confirmations can
    # finally be matched to requests regardless of which file each landed
    # in. Must happen BEFORE the xlsx below is written. Only touches the
    # InquiryStatusCode / InquiryReceivedTime columns.
    if pending_inquiries:
        apply_inquiry_confirmations(shared_rows_by_msgid, pending_inquiries)

    # Guard: every attachment arrived EMPTY (0 bytes inside the email itself).
    # That is a sender-side problem, not a parsing one -- say so clearly,
    # keep the UIDs unmarked and drop the useless empty files. The mails
    # themselves stay on the server, so nothing is lost.
    if saved_files and all(os.path.getsize(p) == 0 for p in saved_files if os.path.exists(p)):
        logging.error(
            "ALL %d attachment(s) in %d email(s) are 0 bytes as received from the mail server -- "
            "the sending system emailed empty logs for %s to %s. Nothing parsed, nothing marked done.",
            len(saved_files), len(matches), window_start, window_end)
        for p in saved_files:
            try:
                os.remove(p)
            except OSError:
                pass
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_paths = []

    if master_rows:
        p = os.path.join(OUTPUT_DIR, f"Allocation_Logs_{ts_tag}.xlsx")
        write_fresh_xlsx(p, MASTER_LOG_COLUMNS, master_rows)
        output_paths.append(p)
    if file1_rows:
        p = os.path.join(OUTPUT_DIR, f"DOWNWARDS_Logs_{ts_tag}.xlsx")
        write_fresh_xlsx(p, RECORD_COLUMNS, file1_rows)
        output_paths.append(p)
    if file2_rows:
        p = os.path.join(OUTPUT_DIR, f"UPWARDS_Logs_{ts_tag}.xlsx")
        write_fresh_xlsx(p, RECORD_COLUMNS, file2_rows)
        output_paths.append(p)

    # BACKUPFILES AUTOMATED/<date>/<HH.MM>/ mirrors the ALLOCATION LOGS /
    # DOWNWARDS_UPWARDS LOGS / <per-email time> structure from
    # downloaded_files. Each raw file's relative path under DOWNLOAD_DIR is
    # reproduced under the backup's run folder. Output xlsx files go
    # directly under the run folder. The run's own HH.MM is a sub-folder so
    # a second run on the same day never overwrites the first run's backup.
    backup_date_folder = os.path.join(
        BACKUP_DIR, window_end.strftime("%Y-%m-%d"), window_end.strftime("%H.%M")
    )


    if unparsed_lines:
        with open(UNPARSED_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n--- {window_label} {ts_tag} ({len(unparsed_lines)} unparsed) ---\n")
            for line in unparsed_lines:
                f.write(line + "\n")
        logging.warning("%d line(s) could not be parsed -- see %s", len(unparsed_lines), UNPARSED_LOG)

    stats = {
        "emails": len(matches), "master_rows": len(master_rows),
        "file1_rows": len(file1_rows), "file2_rows": len(file2_rows),
    }
    subject = build_email_subject(window_label, window_end)
    body = build_email_body(window_label, window_start, window_end, stats)
    send_email_with_attachments(subject, body, output_paths)

    # Only now, after files are written, backed up, and (attempted to be)
    # emailed, do these UIDs get marked done.
    #
    # permanent=True  (backfill): closed historical day, never revisit.
    # permanent=False (live run): the day is still OPEN. These go to the
    #   open-window file instead, so a later manual run today can fetch
    #   them again and reproduce the full day. The scheduled end-of-day
    #   run calls close_open_window() to make them permanent.
    if permanent:
        processed = get_processed_uidls()
        for m in matches:
            processed.add(m["uid"])
        save_processed_uidls(processed)
    else:
        open_uids = get_open_window_uidls()
        for m in matches:
            open_uids.add(m["uid"])
        save_open_window_uidls(open_uids, window_start)

    inquiry_matched = sum(1 for r in master_rows if r.get("InquiryStatusCode"))
    logging.info(
        "%s complete: %d email(s), %d master rows (%d confirmed via statusInquiry), "
        "%d file1 rows, %d file2 rows.",
        window_label, len(matches), len(master_rows), inquiry_matched,
        len(file1_rows), len(file2_rows),
    )
    for p in saved_files:
        rel = os.path.relpath(p, DOWNLOAD_DIR)
        dest = os.path.join(backup_date_folder, rel)
        backup_and_cleanup(p, dest)

    os.makedirs(backup_date_folder, exist_ok=True)
    for p in output_paths:
        dest = os.path.join(backup_date_folder, os.path.basename(p))
        backup_and_cleanup(p, dest)


# =============================================================================
#  PART 2  --  BUILDING THE ALLOCATION LOG REPORT
# =============================================================================

SUCCESS_STATUSES = {"SUCCESS"}

LOG_FILE_PREFIX = "Allocation_Logs"

ALLOCATE_CODES = {"U", "A", "I"}

DEALLOCATE_CODES = {"D", "W"}

MASTER_SEGMENTS = ["CM", "FO", "CO", "NCDEX", "MCX"]

OUTPUT_FOLDER_NAME = "Reconciliation_Output"

MASTER_FILENAME = "allocation_master.csv"

def normalize_alpha(text):
    return re.sub(r"[^a-z]", "", str(text).lower())

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

def clean(val):
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() == "nan":
        return ""
    return s

def parse_time_to_minutes(timestr):
    s = clean(timestr)
    if not s:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))

def is_client_id_header(cell):
    n = normalize_alpha(cell)
    return ("client" in n and "code" in n) or ("ucc" in n and "code" in n)

def amount_kind(cell):
    n = normalize_alpha(cell)
    if "cashallocated" in n:
        return "cash_allocated"        # CM / FO / CO
    if "cashequivalent" in n:
        return "cash_equivalent"       # NCDEX
    if "allocation" in n and "eod" in n:
        return "allocation_eod"        # MCX
    return None

def is_amount_header(cell):
    return amount_kind(cell) is not None

def parse_filename_timestamp(path):
    """Pull a date+time out of filenames like:
    'CM_ClientLevelCollaterals_05082026085612.txt'  -> DDMMYYYYHHMMSS
    'kOBfBvsvuk-20260805084318.xlsx'                -> YYYYMMDDHHMMSS
    Returns 'HH:MM:SS' or None."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r"(\d{14})$", stem)
    if not m:
        return None
    d = m.group(1)
    if 2000 <= int(d[0:4]) <= 2099:
        hh, mm, ss = d[8:10], d[10:12], d[12:14]
    elif 2000 <= int(d[4:8]) <= 2099:
        hh, mm, ss = d[8:10], d[10:12], d[12:14]
    else:
        return None
    return f"{hh}:{mm}:{ss}"

def opening_file_time(path):
    """Time to stamp this file's BOD rows with. Prefers the time embedded in
    the filename; for files that only carry a date and no time (e.g. the
    NCDEX 'ClientNetEffectiveDeposits_06082026.csv'), falls back to the
    file's last-modified time as recorded on this machine."""
    from_name = parse_filename_timestamp(path)
    if from_name:
        return from_name, "from filename"
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%H:%M:%S"), "file modified time"
    except OSError:
        return "", "unknown"

def read_opening_file_rows(path):
    ext = os.path.splitext(path)[1].lower()
    rows = []
    if ext in (".csv", ".txt"):
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            for row in csv.reader(f):
                rows.append([c.strip() if c is not None else "" for c in row])
    elif ext in (".xlsx", ".xlsm", ".xls"):
        # read_only=True can silently return blank text for header cells on
        # some exchange-generated exports (merged title cells, blank leading
        # rows) - full mode is slower but reads these reliably.
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
        ws = wb.worksheets[0]
        for row in ws.iter_rows(values_only=True):
            rows.append(["" if c is None else str(c).strip() for c in row])
    else:
        return None
    return rows

def find_opening_header_row(rows, scan_limit=60):
    for i, row in enumerate(rows[:scan_limit]):
        has_id = any(is_client_id_header(c) for c in row)
        has_amt = any(is_amount_header(c) for c in row)
        if has_id and has_amt:
            return i
    return None

def detect_opening_segment(filename, headers):
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

def extract_opening_from_file(path):
    """Returns (segment, {client_code: amount}) or None."""
    rows = read_opening_file_rows(path)
    if not rows:
        return None
    header_idx = find_opening_header_row(rows)
    if header_idx is None:
        return None
    headers = rows[header_idx]
    segment = detect_opening_segment(path, headers)
    if segment is None:
        return None
    id_col = next((i for i, h in enumerate(headers) if is_client_id_header(h)), None)
    wanted_kind = {"NCDEX": "cash_equivalent", "MCX": "allocation_eod"}.get(segment, "cash_allocated")
    amt_col = next((i for i, h in enumerate(headers) if amount_kind(h) == wanted_kind), None)
    if id_col is None or amt_col is None:
        return None

    print(f"  [ok] {os.path.basename(path)}  ->  segment={segment}, "
          f"header row={header_idx + 1}, client col='{headers[id_col]}', amount col='{headers[amt_col]}'")

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

def read_opening_balances(folder):
    """Scans the folder for CM/FO/CO/NCDEX/MCX opening-balance snapshot
    files. Returns (totals, timestamps): totals = {segment: {client: amount}},
    timestamps = {segment: 'HH:MM:SS' or ''}. Both are {} if nothing usable
    is found - callers should treat that as 'no opening balance data
    available' and fall back to 0, not an error."""
    files = [
        os.path.join(folder, f) for f in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, f))
        and not f.startswith("~$")
        and not f.startswith("Allocation_Logs")   # that's the log file, handled separately
        and os.path.splitext(f)[1].lower() in (".csv", ".txt", ".xlsx", ".xlsm", ".xls")
    ]
    if not files:
        print(f"\n[note] No CM/FO/CO/NCDEX/MCX files found in {folder} - opening balances assumed to be 0.")
        return {}, {}

    print(f"\nReading opening balances from {folder}:")
    candidates = {}  # segment -> list of (mtime, path, totals)
    for path in files:
        result = extract_opening_from_file(path)
        if result is None:
            continue
        segment, totals = result
        candidates.setdefault(segment, []).append((os.path.getmtime(path), path, totals))

    if not candidates:
        print(f"  [note] none of the files in {folder} were recognizable CM/FO/CO/NCDEX/MCX files - opening balances assumed to be 0.")
        return {}, {}

    totals_out, time_out = {}, {}
    for segment, lst in candidates.items():
        lst.sort(key=lambda x: x[0], reverse=True)
        mtime, path, totals = lst[0]
        totals_out[segment] = totals
        stamp, source = opening_file_time(path)
        time_out[segment] = stamp
        print(f"  [use] {segment}: {os.path.basename(path)} "
              f"(as of {stamp or 'unknown time'}, {source})")
        for _, older_path, _ in lst[1:]:
            print(f"  [ignored, older file for {segment}]: {os.path.basename(older_path)}")

    return totals_out, time_out

def find_log_file(folder):
    candidates = [
        os.path.join(folder, f) for f in os.listdir(folder)
        if f.lower().startswith(LOG_FILE_PREFIX.lower())
        and not f.startswith("~$")
        and os.path.splitext(f)[1].lower() in (".xlsx", ".xlsm", ".xls", ".csv")
    ]
    if not candidates:
        raise SystemExit(f"No file starting with '{LOG_FILE_PREFIX}' found in {folder}")
    candidates.sort(key=os.path.getmtime, reverse=True)
    if len(candidates) > 1:
        print("  Multiple log files found, using the newest:")
        for c in candidates:
            print(f"    {'->' if c == candidates[0] else '  '} {os.path.basename(c)}")
    return candidates[0]

WANTED = {
    "requesttime": "RequestTime",
    "allocationdate": "AllocationDate",
    "segment": "Segment",
    "clientcode": "ClientCode",
    "allocationamount": "AllocationAmount",
    "actiontype": "ActionType",
    "responsestatus": "ResponseStatus",
    "responsecode": "ResponseCode",
    "responsetime": "ResponseTime",
    "inquirystatuscode": "InquiryStatusCode",
    "inquiryreceivedtime": "InquiryReceivedTime",
    "sequenceno": "SequenceNo",
}

def read_log(path):
    ext = os.path.splitext(path)[1].lower()

    if ext == ".csv":
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            raw = [r for r in csv.reader(f)]
    else:
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.worksheets[0]
        raw = [["" if c is None else c for c in row]
               for row in ws.iter_rows(values_only=True)]

    header_idx = None
    for i, row in enumerate(raw[:40]):
        norm = {normalize_alpha(c) for c in row}
        if "clientcode" in norm and "allocationamount" in norm:
            header_idx = i
            break
    if header_idx is None:
        raise SystemExit(f"Could not find a header row in {os.path.basename(path)}")

    headers = [normalize_alpha(c) for c in raw[header_idx]]
    col_of = {}
    for i, h in enumerate(headers):
        if h in WANTED and WANTED[h] not in col_of:
            col_of[WANTED[h]] = i

    missing = [v for v in ("ClientCode", "AllocationAmount", "Segment", "ActionType")
               if v not in col_of]
    if missing:
        raise SystemExit(f"Log file is missing required column(s): {', '.join(missing)}")

    rows = []
    seen = set()
    duplicate_count = 0
    for idx, r in enumerate(raw[header_idx + 1:]):
        if not any(clean(c) for c in r):
            continue
        rec = {}
        for name, cidx in col_of.items():
            rec[name] = clean(r[cidx]) if cidx < len(r) else ""
        if not rec.get("ClientCode"):
            continue

        # The log file is often several overlapping exports stitched
        # together, which repeats the exact same row more than once.
        # Compare the WHOLE row (every column) - same as Excel's Remove
        # Duplicates. Two rows for the same event at different inquiry
        # stages differ in at least one column, so both are correctly kept.
        dedup_key = tuple(clean(c) for c in r)
        if dedup_key in seen:
            duplicate_count += 1
            continue
        seen.add(dedup_key)

        rec["Amount"] = to_float(rec.get("AllocationAmount"))
        rec["Minutes"] = parse_time_to_minutes(rec.get("RequestTime"))
        rec["_order"] = idx
        rows.append(rec)

    log_date = rows[0].get("AllocationDate", "") if rows else ""
    print(f"  Read {len(rows)} log rows. Allocation date: {log_date or 'unknown'}")
    if duplicate_count:
        print(f"  Removed {duplicate_count} fully-identical duplicate row(s) "
              f"(every column matched - same as Excel's Remove Duplicates).")
    return rows, log_date

def is_success(rec):
    return rec.get("ResponseStatus", "").strip().upper() in SUCCESS_STATUSES

def system_action_label(rec):
    a = rec.get("ActionType", "").strip().upper()
    if a in ALLOCATE_CODES:
        return "U (UPWARDS)"
    if a in DEALLOCATE_CODES:
        return "D (DOWNWARDS)"
    return a or ""

# ---- Back Office snapshots (BOD / AFTERNOON / EVENING allocation) ---------
# The CLI Prudential box backs up its Back Office files every time it runs. The day's backup
# folders (named HH-MM-SS) are sorted by time, oldest first, and named by position -- the clock
# time itself does not matter:
#     1 folder   -> BOD allocation
#     2 folders  -> BOD allocation, AFTERNOON allocation
#     3 folders  -> BOD allocation, AFTERNOON allocation, EVENING allocation
# Every snapshot becomes one starting row per client+segment in the timeline,
# with Change Type "Back Office". Log rows that come after a snapshot are
# calculated from that snapshot's level.
SNAP_BOD = "BOD allocation"
SNAP_AFTERNOON = "AFTERNOON allocation"
SNAP_EVENING = "EVENING allocation"
SNAP_ORDER = (SNAP_BOD, SNAP_AFTERNOON, SNAP_EVENING)
CHANGE_TYPE_BACK_OFFICE = "Back Office"      # B and O capitals - exactly like this
SNAP_SUBDIR = "_snapshots"                   # work-folder sub-folder holding the AFTERNOON / EVENING files


def _hms_to_seconds(text):
    """'HH:MM[:SS]' -> seconds since midnight, or None when it can't be read."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", str(text or ""))
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)


def build_timeline(rows, opening_totals=None, opening_time=None, snapshots=None):
    opening_totals = opening_totals or {}   # {segment: {client: amount}}   (BOD allocation)
    opening_time = opening_time or {}       # {segment: 'HH:MM:SS'}
    snapshots = snapshots or []             # [(label, {segment: {client: amount}}, {segment: 'HH:MM:SS'})]  AFTERNOON / EVENING

    seg_set = {(r.get("Segment") or "(blank)") for r in rows} | set(opening_totals.keys())
    for _lbl, _tot, _tm in snapshots:
        seg_set |= set(_tot.keys())
    segments_seen = sorted(seg_set)

    by_client = defaultdict(list)
    for r in rows:
        by_client[r["ClientCode"]].append(r)

    all_clients = set(by_client.keys())
    for seg_totals in opening_totals.values():
        all_clients.update(seg_totals.keys())
    for _lbl, _tot, _tm in snapshots:
        for seg_totals in _tot.values():
            all_clients.update(seg_totals.keys())

    timeline_rows = []
    seg_summary = defaultdict(lambda: {
        "alloc_cnt": 0, "dealloc_cnt": 0, "nochange_cnt": 0,
        "alloc_amt": 0.0, "dealloc_amt": 0.0,
    })
    latest_per_client = {}   # client -> {seg: last known level}
    latest_time_per_client = {}   # client -> time of their last record (real or snapshot)

    for client in sorted(all_clients):
        events = by_client.get(client, [])
        events.sort(key=lambda r: (r["Minutes"] if r["Minutes"] is not None else 10**9, r["_order"]))

        # seed with opening balances instead of 0 - this is what makes a
        # client's first real event of the day show the true change instead
        # of a false "Allocated" for their whole opening balance.
        current_level = {seg: round(opening_totals.get(seg, {}).get(client, 0.0), 2) for seg in segments_seen}
        client_start = len(timeline_rows)

        # One "BOD allocation" row per segment this client has an opening
        # balance in - the starting line of the client's timeline.
        for seg in segments_seen:
            seg_opening = opening_totals.get(seg, {})
            if client not in seg_opening:
                continue
            val = round(seg_opening[client], 2)
            row_out = [
                client, opening_time.get(seg, ""), seg,
                SNAP_BOD, CHANGE_TYPE_BACK_OFFICE, val, 0.0, val,
            ]
            for s in segments_seen:
                row_out.append(round(current_level.get(s, 0.0), 2))
            # these five come from the log file itself - a synthetic BOD row
            # isn't a log row, so they're blank here. Count must match the
            # five headers added at the end of hdr: ResponseStatus,
            # ResponseCode, ResponseTime, InquiryStatusCode, InquiryReceivedTime.
            row_out += ["", "", "", "", ""]
            timeline_rows.append(row_out)

        # AFTERNOON / EVENING snapshots of this client, in time order. Each is
        # emitted just before the first log row that is at or after its time.
        pending = []
        for label, snap_totals, snap_times in snapshots:
            for seg in segments_seen:
                if client in snap_totals.get(seg, {}):
                    stamp = snap_times.get(seg, "")
                    secs = _hms_to_seconds(stamp)
                    pending.append((secs if secs is not None else 10**9, label, seg,
                                    round(snap_totals[seg][client], 2), stamp))
        pending.sort(key=lambda x: x[0])

        def emit_snapshot(item):
            _secs, label, seg, val, stamp = item
            current_level[seg] = val              # later log rows are calculated from this level
            row_out = [client, stamp, seg, label, CHANGE_TYPE_BACK_OFFICE, val, 0.0, val]
            for s in segments_seen:
                row_out.append(round(current_level.get(s, 0.0), 2))
            row_out += ["", "", "", "", ""]
            timeline_rows.append(row_out)

        for rec in events:
            ev_secs = _hms_to_seconds(rec.get("RequestTime"))
            while pending and ev_secs is not None and pending[0][0] <= ev_secs:
                emit_snapshot(pending.pop(0))

            seg = rec.get("Segment") or "(blank)"
            new_level = round(rec["Amount"], 2)
            prev_level = current_level.get(seg, 0.0)
            change = round(new_level - prev_level, 2)

            if change > 0:
                ctype = "Allocated"
                seg_summary[seg]["alloc_cnt"] += 1
                seg_summary[seg]["alloc_amt"] += change
            elif change < 0:
                ctype = "Deallocated"
                seg_summary[seg]["dealloc_cnt"] += 1
                seg_summary[seg]["dealloc_amt"] += -change
            else:
                ctype = "No Change"
                seg_summary[seg]["nochange_cnt"] += 1

            current_level[seg] = new_level

            row_out = [
                client, rec.get("RequestTime", ""), seg,
                system_action_label(rec), ctype, prev_level, change, new_level,
            ]
            for s in segments_seen:
                row_out.append(round(current_level.get(s, 0.0), 2))
            row_out += [
                rec.get("ResponseStatus", ""),
                rec.get("ResponseCode", ""), rec.get("ResponseTime", ""),
                rec.get("InquiryStatusCode", ""), rec.get("InquiryReceivedTime", ""),
            ]
            timeline_rows.append(row_out)
        for item in pending:                      # snapshots later than every log row of this client
            emit_snapshot(item)

        # after walking all of this client's rows, current_level holds the
        # last known value per segment - i.e. the value as of their last
        # (latest by time) record, which is what "latest CM/FO value" means.
        latest_per_client[client] = dict(current_level)
        latest_time_per_client[client] = next(
            (r[1] for r in reversed(timeline_rows[client_start:]) if r[1]), "")

    return timeline_rows, segments_seen, seg_summary, latest_per_client, latest_time_per_client

HDR_FILL = PatternFill("solid", fgColor="1F3864")

HDR_FONT = Font(color="FFFFFF", bold=True)

MONEY = '#,##0.00'

def write_sheet(wb, title, headers, rows, money_cols=(), freeze="A2"):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in rows:
        ws.append(r)
    for c in money_cols:
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=c).number_format = MONEY
    for c in range(1, len(headers) + 1):
        width = max(len(str(headers[c - 1])) + 2, 12)
        ws.column_dimensions[get_column_letter(c)].width = min(width, 22)
    ws.freeze_panes = freeze
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"
    return ws

def build_report(folder, rows, excluded, log_date):
    opening_totals, opening_time = read_opening_balances(folder)
    # AFTERNOON / EVENING allocation snapshots (optional) live in sub-folders of the work folder.
    snapshots = []
    for label in (SNAP_AFTERNOON, SNAP_EVENING):
        sub = os.path.join(folder, SNAP_SUBDIR, label)
        if os.path.isdir(sub):
            snap_totals, snap_times = read_opening_balances(sub)
            if snap_totals:
                snapshots.append((label, snap_totals, snap_times))
    timeline_rows, segments_seen, seg_summary, latest_per_client, latest_time_per_client = build_timeline(
        rows, opening_totals, opening_time, snapshots
    )

    excluded_by_segment = defaultdict(int)
    for rec in excluded:
        excluded_by_segment[rec.get("Segment", "") or "(blank)"] += 1

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # ---- 1. Client timeline (the main sheet) ------------------------------
    hdr = (["Client Code", "Time", "Segment", "System ActionType", "Change Type",
            "Previous amount", "Change Amount", "New Level"]
           + [f"{s} - current level" for s in segments_seen]
           + ["ResponseStatus", "ResponseCode", "ResponseTime",
              "InquiryStatusCode", "InquiryReceivedTime"])
    money_cols = tuple(range(6, 9 + len(segments_seen)))
    write_sheet(wb, "Client_Timeline", hdr, timeline_rows, money_cols=money_cols)

    # ---- Latest value per client per segment (last record, time-wise) -----
    hdr = ["Client Code", "Latest Time"] + [f"Latest {s} Value" for s in segments_seen]
    data = []
    for client in sorted(latest_per_client):
        levels = latest_per_client[client]
        data.append([client, latest_time_per_client.get(client, "")]
                     + [round(levels.get(s, 0.0), 2) for s in segments_seen])
    write_sheet(wb, "Latest_Client_Values", hdr, data,
                money_cols=tuple(range(3, 3 + len(segments_seen))))

    # ---- 2. Summary by segment --------------------------------------------
    hdr = ["Segment", "Times Allocated", "Times Deallocated", "No-Change Events",
           "Allocated Amount", "Deallocated Amount", "Net Movement",
           "Excluded (not SUCCESS)"]
    data = []
    tot = {"alloc_cnt": 0, "dealloc_cnt": 0, "nochange_cnt": 0, "alloc_amt": 0.0, "dealloc_amt": 0.0}
    for seg in sorted(set(seg_summary) | set(excluded_by_segment)):
        s = seg_summary[seg]
        for k in tot:
            tot[k] += s[k]
        data.append([
            seg, s["alloc_cnt"], s["dealloc_cnt"], s["nochange_cnt"],
            round(s["alloc_amt"], 2), round(s["dealloc_amt"], 2),
            round(s["alloc_amt"] - s["dealloc_amt"], 2),
            excluded_by_segment.get(seg, 0),
        ])
    data.append([
        "TOTAL", tot["alloc_cnt"], tot["dealloc_cnt"], tot["nochange_cnt"],
        round(tot["alloc_amt"], 2), round(tot["dealloc_amt"], 2),
        round(tot["alloc_amt"] - tot["dealloc_amt"], 2), len(excluded),
    ])
    ws = write_sheet(wb, "Summary_By_Segment", hdr, data, money_cols=(5, 6, 7))
    for c in range(1, len(hdr) + 1):
        ws.cell(row=ws.max_row, column=c).font = Font(bold=True)

    # ---- 3. Excluded rows --------------------------------------------------
    hdr = ["RequestTime", "Segment", "Client Code", "System ActionType",
           "Allocation Amount", "Response Status", "SequenceNo"]
    data = []
    for rec in excluded:
        data.append([
            rec.get("RequestTime", ""), rec.get("Segment", ""), rec["ClientCode"],
            system_action_label(rec), round(rec["Amount"], 2),
            rec.get("ResponseStatus", "") or "(no response)", rec.get("SequenceNo", ""),
        ])
    write_sheet(wb, "Excluded_Rows", hdr, data, money_cols=(5,))

    out_dir = os.path.join(folder, "Reconciliation_Output")
    os.makedirs(out_dir, exist_ok=True)
    safe_date = re.sub(r"[^A-Za-z0-9\-]", "", log_date) or "log"
    out_path = os.path.join(
        out_dir,
        f"Allocation_Log_Report_{safe_date}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    )
    wb.save(out_path)

    print(f"\n  Segments found : {', '.join(segments_seen) or 'none'}")
    print(f"  Allocations    : {tot['alloc_cnt']} events, {tot['alloc_amt']:,.2f}")
    print(f"  Deallocations  : {tot['dealloc_cnt']} events, {tot['dealloc_amt']:,.2f}")
    print(f"  Net movement   : {tot['alloc_amt'] - tot['dealloc_amt']:,.2f}")
    print(f"\nReport written to: {out_path}")

    # allocation_master.csv is deliberately NOT updated -- that file is no
    # longer used. The function that did it is left below, unused.

    return out_path

def find_master_path(out_dir):
    """Find the master file even if it's been renamed (e.g. Excel/Windows
    adding characters while it was open). Matches 'allocation_master*.csv'."""
    if not os.path.isdir(out_dir):
        return None
    candidates = [
        f for f in os.listdir(out_dir)
        if f.lower().startswith("allocation_master") and f.lower().endswith(".csv")
        and not f.startswith("~$")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda f: os.path.getmtime(os.path.join(out_dir, f)), reverse=True)
    if len(candidates) > 1:
        print(f"  [warn] multiple master-like files found, using the most recently modified: {candidates[0]}")
    elif candidates[0] != MASTER_FILENAME:
        print(f"  [note] master file found under a different name than expected ('{candidates[0]}'), using it as-is.")
    return os.path.join(out_dir, candidates[0])

def load_master(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = {row["Client Code"]: row for row in reader if row.get("Client Code")}
    return fieldnames, rows

def latest_existing_run(fieldnames):
    max_run = 0
    for col in fieldnames:
        m = re.match(r"^(?:CM|FO|CO|NCDEX|MCX)_R(\d+)$", col)
        if m:
            max_run = max(max_run, int(m.group(1)))
    return max_run

def update_master_with_latest(folder, latest_per_client, segments_seen):
    out_dir = os.path.join(folder, OUTPUT_FOLDER_NAME)
    master_path = find_master_path(out_dir)
    if master_path is None:
        print(f"\n[note] No master file found in {out_dir} - run reconciliation_snapshot.py "
              f"at least once first, then re-run this script to refresh it.")
        return

    fieldnames, master_rows = load_master(master_path)
    latest_run = latest_existing_run(fieldnames)
    if latest_run == 0:
        print(f"\n[note] {os.path.basename(master_path)} has no run columns yet - "
              f"run reconciliation_snapshot.py first.")
        return
    prev_run = latest_run - 1

    segs_to_update = [s for s in segments_seen if s in MASTER_SEGMENTS]
    if not segs_to_update:
        print("\n[note] none of the log's segments match CM/FO/CO/NCDEX/MCX - nothing to update.")
        return

    net_col = f"Net_Diff_R{prev_run}_to_R{latest_run}" if prev_run >= 1 else None
    touched_clients = 0

    for client, levels in latest_per_client.items():
        relevant = {seg: levels[seg] for seg in segs_to_update if seg in levels}
        if not relevant:
            continue
        row = master_rows.setdefault(client, {"Client Code": client})
        for seg, val in relevant.items():
            new_val = round(val, 2)
            row[f"{seg}_R{latest_run}"] = new_val
            if prev_run >= 1:
                prev_val = to_float(row.get(f"{seg}_R{prev_run}", 0))
                row[f"{seg}_Diff_R{prev_run}_to_R{latest_run}"] = round(new_val - prev_val, 2)
        if net_col:
            net = sum(to_float(row.get(f"{s}_Diff_R{prev_run}_to_R{latest_run}", 0)) for s in MASTER_SEGMENTS)
            row[net_col] = round(net, 2)
        touched_clients += 1

    ordered_fieldnames = list(fieldnames) if fieldnames else ["Client Code"]
    if "Client Code" not in ordered_fieldnames:
        ordered_fieldnames.insert(0, "Client Code")

    try:
        with open(master_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=ordered_fieldnames)
            w.writeheader()
            for client in sorted(master_rows.keys()):
                row = master_rows[client]
                w.writerow({c: row.get(c, 0) for c in ordered_fieldnames})
    except PermissionError:
        print(f"\n[error] Could not save '{master_path}' - it's probably open in Excel. "
              f"Close it and run this script again to apply this update.")
        return

    print(f"\nRefreshed Run {latest_run} in the master file from this log "
          f"(segments: {', '.join(segs_to_update)}).")
    print(f"Clients updated: {touched_clients}")
    print(f"Master file: {master_path}")


# =============================================================================
#  ORCHESTRATION  --  ties the two halves above together
# =============================================================================

def find_date_folder(run_date):
    """BACKUP_ROOT/DD-MM-YYYY for the day this run STARTED."""
    wanted = f"{run_date:%d-%m-%Y}"
    if not os.path.isdir(BACKUP_ROOT):
        print(f"  [X] BACKUP_ROOT does not exist: {BACKUP_ROOT}")
        return None
    for name in os.listdir(BACKUP_ROOT):
        full = os.path.join(BACKUP_ROOT, name)
        if os.path.isdir(full) and name.strip() == wanted:
            return full
    # tolerate stray suffixes like "04-08-2026---"
    for name in sorted(os.listdir(BACKUP_ROOT)):
        full = os.path.join(BACKUP_ROOT, name)
        if os.path.isdir(full) and name.strip().startswith(wanted):
            print(f"  [!] exact folder '{wanted}' not found; using '{name}'")
            return full
    print(f"  [X] No backup folder named {wanted} inside {BACKUP_ROOT}")
    avail = sorted(os.listdir(BACKUP_ROOT))[:10]
    print(f"      available: {', '.join(avail)}")
    return None


def oldest_time_subfolder(date_folder):
    """The morning one. Sub-folders are named HH-MM-SS; this runs at night,
    so the earliest-named folder is the morning snapshot."""
    subs = []
    for name in os.listdir(date_folder):
        full = os.path.join(date_folder, name)
        if not os.path.isdir(full):
            continue
        m = re.match(r"^(\d{2})-(\d{2})-(\d{2})$", name.strip())
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (99, 99, 99)
        subs.append((key, name, full))
    if not subs:
        print(f"  [X] No HH-MM-SS sub-folders inside {date_folder}")
        return None
    subs.sort()
    chosen = subs[0]
    print(f"  time sub-folders: {', '.join(s[1] for s in subs)}")
    print(f"  using the oldest (morning): {chosen[1]}")
    return chosen[2]


def pick_opening_files(src_folder):
    """Returns {segment: path} for the 5 files the report needs.

    CM / FO / CO / NCDEX are matched by filename. The MCX file's name is
    not predictable, so it is identified by looking INSIDE the remaining
    files for MCX-shaped headers, using the very same detection the report
    builder uses, so the two can never disagree. Files above MAX_SCAN_MB
    are never opened.
    """
    picked = {}
    everything = [os.path.join(src_folder, f) for f in os.listdir(src_folder)
                  if os.path.isfile(os.path.join(src_folder, f))
                  and not f.startswith("~$")]

    def by_name(pattern):
        hits = [p for p in everything if re.match(pattern, os.path.basename(p), re.I)]
        hits.sort(key=os.path.getmtime, reverse=True)
        return hits[0] if hits else None

    for seg, pattern in (("CM", r"^CM[_\-]"),
                         ("FO", r"^FO[_\-]"),
                         ("CO", r"^CO[_\-]"),
                         ("NCDEX", r"^ClientNetEffectiveDeposits")):
        p = by_name(pattern)
        if p:
            picked[seg] = p

    # ---- MCX: unknown filename, so identify it by content ----
    taken = set(picked.values())
    candidates = [p for p in everything
                  if p not in taken
                  and os.path.splitext(p)[1].lower() in (".csv", ".txt", ".xlsx", ".xlsm", ".xls")
                  and os.path.getsize(p) <= MAX_SCAN_MB * 1024 * 1024]
    candidates.sort(key=os.path.getsize)          # cheapest first
    for p in candidates:
        try:
            result = extract_opening_from_file(p)
        except Exception:
            continue
        if result and result[0] == "MCX":
            picked["MCX"] = p
            break

    print("\n  Opening-balance files selected:")
    for seg in ("CM", "FO", "CO", "NCDEX", "MCX"):
        p = picked.get(seg)
        print(f"     {seg:<6} {os.path.basename(p) if p else '*** NOT FOUND ***'}")
    missing = [s for s in ("CM", "FO", "CO", "NCDEX", "MCX") if s not in picked]
    if missing:
        print(f"  [!] missing: {', '.join(missing)} -- those segments start from 0")
    return picked


def pick_snapshot_folders(date_folder):
    """{label: folder} - the CLI backup folders (named HH-MM-SS) of one day, sorted by time, oldest
    first: the 1st is BOD, the 2nd AFTERNOON, the 3rd EVENING. Fewer folders just means fewer
    snapshots (EVENING is optional). Folders beyond the third are ignored."""
    subs = []
    for name in os.listdir(date_folder):
        full = os.path.join(date_folder, name)
        m = re.match(r"^(\d{2})-(\d{2})-(\d{2})$", name.strip())
        if os.path.isdir(full) and m:
            subs.append(((int(m.group(1)), int(m.group(2)), int(m.group(3))), name, full))
    subs.sort()
    chosen = {}
    for label, item in zip(SNAP_ORDER, subs):
        chosen[label] = item[2]
        print(f"  {label:<21}: {item[1]}")
    if len(subs) > len(SNAP_ORDER):
        print(f"  [!] {len(subs)} backup folders found -- only the first {len(SNAP_ORDER)} are used; "
              f"ignored: {', '.join(x[1] for x in subs[len(SNAP_ORDER):])}")
    return chosen


def check_too_many_snapshots(run_date, send=False):
    """Guard: the day's CLI backup folder may hold at most 3 time folders (BOD / AFTERNOON / EVENING).
    More than that means something is wrong (a stray or duplicate backup), and guessing which three to
    use could give a wrong report -- so the run is SKIPPED. Returns None when all is fine, otherwise the
    warning text; with send=True the same warning is e-mailed (no attachments)."""
    if not os.path.isdir(BACKUP_ROOT):
        return None
    wanted = f"{run_date:%d-%m-%Y}"
    date_folder = next((os.path.join(BACKUP_ROOT, n) for n in os.listdir(BACKUP_ROOT)
                        if os.path.isdir(os.path.join(BACKUP_ROOT, n)) and n.strip().startswith(wanted)), None)
    if not date_folder:
        return None
    names = sorted(n for n in os.listdir(date_folder)
                   if os.path.isdir(os.path.join(date_folder, n)) and re.match(r"^\d{2}-\d{2}-\d{2}$", n.strip()))
    if len(names) <= len(SNAP_ORDER):
        return None
    msg = (f"WARNING: {len(names)} CLI Prudential backup folders found for {wanted} "
           f"({', '.join(names)}). At most {len(SNAP_ORDER)} are allowed (BOD, AFTERNOON, EVENING). "
           f"The Allocation Log Report was SKIPPED and no report was produced. "
           f"Please remove the extra backup folder(s) from {date_folder} and run the report again.")
    logging.warning(msg)
    if send and EMAIL_TO and SMTP_PASSWORD:
        try:
            body = ("Dear Team,\n\n" + msg + "\n\nNo files are attached because no report was generated.\n"
                    "Nothing was marked as done, so the next run will cover this period again.\n") + EMAIL_SIGNATURE
            subject = f"WARNING - Allocation Log Report skipped ({wanted}): more than {len(SNAP_ORDER)} CLI backups"
            call_with_retry(lambda: _do_send_email(subject, body, []), what="SMTP send",
                            permanent_exceptions=(smtplib.SMTPAuthenticationError,
                                                  smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused))
            logging.info("Warning e-mail sent to %s", ", ".join(EMAIL_TO))
        except Exception:
            logging.exception("Could not send the warning e-mail.")
    return msg


def collect_snapshots(date_folder, work_folder):
    """Copies the Back Office files of every snapshot found for the day into the work folder:
    BOD allocation flat (as always), AFTERNOON / EVENING into <work>/_snapshots/<label>/.
    Returns [(label, original_path), ...]. A missing snapshot is skipped quietly."""
    chosen = pick_snapshot_folders(date_folder)
    used = []
    for label in SNAP_ORDER:
        folder = chosen.get(label)
        if not folder:
            print(f"  {label:<21}: no backup found -- skipped"
                  + ("" if label == SNAP_EVENING else "  [!]"))
            continue
        files = pick_opening_files(folder)
        if not files:
            print(f"  {label}: no usable files in {os.path.basename(folder)} -- skipped")
            continue
        dest = work_folder if label == SNAP_BOD else os.path.join(work_folder, SNAP_SUBDIR, label)
        os.makedirs(dest, exist_ok=True)
        for p in files.values():
            shutil.copy2(p, os.path.join(dest, os.path.basename(p)))
            used.append((label, p))
    return used


def clean_folder(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)


# ---- per-run log slices -------------------------------------------------
# The log files in the base folder are cumulative: every run appends to
# them. So: note how long each file is BEFORE the run does anything, then at
# backup time copy only what was added after that point. The live files stay
# cumulative (history); the backup copy is this run only.

LOG_FILES = ("run_log.txt", "unmatched_inquiries.log", "unparsed_lines.log")


def log_start_offsets():
    """Byte length of each log file before this run writes to it."""
    offsets = {}
    for name in LOG_FILES:
        p = os.path.join(BASE_DIR, name)
        try:
            offsets[name] = os.path.getsize(p) if os.path.exists(p) else 0
        except OSError:
            offsets[name] = 0
    return offsets


def save_log_slices(dest, offsets):
    """Writes each log file's THIS-RUN portion into the backup folder."""
    # Push anything still sitting in logging's buffers out to disk first,
    # otherwise the tail of this run's log is missing from the slice.
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass

    for name in LOG_FILES:
        src = os.path.join(BASE_DIR, name)
        if not os.path.exists(src):
            continue
        start = offsets.get(name, 0)
        try:
            with open(src, "r", encoding="utf-8", errors="replace") as f:
                f.seek(start)
                text = f.read()
            if not text.strip():
                continue          # this run added nothing -- no empty file
            with open(os.path.join(dest, name), "w", encoding="utf-8") as f:
                f.write(text)
            lines = text.count("\n")
            print(f"  {name} -> backup ({lines} line(s) from THIS run only)")
        except Exception as e:
            print(f"  [!] couldn't save {name}: {e}")


def main():
    ap = argparse.ArgumentParser(
        description="Fetch the allocation emails, build the report, and send it.")
    ap.add_argument("--scheduled", action="store_true",
                    help="Mark this as the nightly run. ONLY this moves "
                         "the day marker forward. Manual runs must not pass it.")
    ap.add_argument("--until", metavar="HH:MM", default=None,
                    help="Manual runs only: report the day up to this time instead of "
                         "up to right now, e.g. --until 15:00")
    ap.add_argument("--as-of", metavar="'YYYY-MM-DD HH:MM'", default=None,
                    help="Backfill only: re-run --scheduled as if it were actually this "
                         "exact date+time, instead of right now. Use this to cleanly "
                         "re-close a past day that never sent its EOD email (e.g. after a "
                         "crash) -- e.g. --scheduled --as-of \"2026-08-12 23:59\" redoes "
                         "the 12-Aug close exactly as if it had run at 23:59 that night: "
                         "same window end, and STEP 2's opening-balance lookup targets the "
                         "12-Aug CLI backup folder instead of today's. Requires "
                         "--scheduled; cannot combine with --until.")
    args = ap.parse_args()

    if args.scheduled and args.until:
        print("[X] --until is for manual runs only; a scheduled run covers the whole day.")
        sys.exit(1)

    if args.as_of and not args.scheduled:
        print("[X] --as-of only makes sense together with --scheduled (it re-runs a past "
              "EOD close). For a manual run on a specific day, --until alone won't reach "
              "back before today either -- use --as-of --scheduled instead.")
        sys.exit(1)

    if args.as_of and args.until:
        print("[X] --as-of and --until can't combine -- --as-of already fixes the whole "
              "date+time being reported.")
        sys.exit(1)

    as_of_dt = None
    if args.as_of:
        try:
            as_of_dt = datetime.strptime(args.as_of, "%Y-%m-%d %H:%M")
        except ValueError:
            print(f"[X] --as-of must look like '2026-08-12 23:59', got {args.as_of!r}")
            sys.exit(1)

    if args.scheduled and not as_of_dt:
        days, live = plan_scheduled_runs(get_window_state(), RUN_START)
        if days or live:
            setup_logging()
            for d in days:
                print(f"\n>>> Closing missed day {d:%d-%b-%Y} (window from the last close up to {d:%d-%b} 23:59)")
                ns = argparse.Namespace(scheduled=True, until=None, as_of=f"{d:%Y-%m-%d} 23:59")
                run_once(ns, datetime.combine(d, dtime(23, 59)))
            if live:
                return run_once(args, None)
            return
        print("Nothing to do: every earlier day is already closed, and today's close "
              "runs from 20:00 onwards (the 23:59 schedule).")
        return
    return run_once(args, as_of_dt)


def run_once(args, as_of_dt):
    # Everything below uses run_start instead of the bare module-level
    # RUN_START, so a --as-of backfill affects BOTH the window-start
    # fallback AND STEP 2's find_date_folder(run_start.date()) lookup for
    # opening-balance files.
    run_start = as_of_dt or RUN_START

    setup_logging()

    print("=" * 70)
    print(f"  ALLOCATION REPORT   started {RUN_START:%d-%b-%Y %H:%M:%S}"
          + (f"  (--as-of {run_start:%d-%b-%Y %H:%M})" if as_of_dt else ""))
    print(f"  mode: {'SCHEDULED (nightly)' if args.scheduled else 'MANUAL'}"
          + (f", capped at {args.until}" if args.until else "")
          + (f", backfilling as of {run_start:%d-%b-%Y %H:%M}" if as_of_dt else ""))
    print(f"  base folder: {BASE_FOLDER}")
    print("=" * 70)

    os.makedirs(BASE_FOLDER, exist_ok=True)
    _warn = check_too_many_snapshots(run_start.date(), send=bool(args.scheduled))
    if _warn:
        print("\n[!] " + _warn)
        return False
    clean_folder(WORK_FOLDER)

    # Note where each cumulative log file currently ends, so that at the
    # end only THIS run's additions get copied into the backup.
    log_offsets = log_start_offsets()

    # -----------------------------------------------------------------
    # STEP 1 -- fetch the mail, build the three log files
    # -----------------------------------------------------------------
    print("\n" + "-" * 70)
    print("STEP 1  fetching emails and building the three log files")
    print("-" * 70)

    window_start = get_window_state() or datetime.combine(run_start.date(), dtime.min)
    if not args.scheduled:
        # a manual report is "the day so far": never reach back into earlier days
        window_start = max(window_start, datetime.combine(RUN_START.date(), dtime.min))
    if as_of_dt and window_start >= as_of_dt:
        window_start = datetime.combine(as_of_dt.date(), dtime.min)
        print(f"  [!] day marker is already past {as_of_dt:%d-%b %H:%M}; "
              f"reporting {as_of_dt:%d-%b-%Y} only, from 00:00.")

    if as_of_dt:
        window_end = as_of_dt
    elif args.until:
        try:
            hh, mm = args.until.split(":")
            window_end = datetime.combine(RUN_START.date(), dtime(int(hh), int(mm)))
        except Exception:
            print(f"[X] --until must look like 15:00, got {args.until!r}")
            sys.exit(1)
    else:
        window_end = None

    session = _Pop3Session()
    try:
        matches = fetch_window_messages(session, window_start, window_end)
    finally:
        session.close()

    now = datetime.now()
    if window_end is None:
        window_end = now
    elif window_end > now:
        print(f"  {'--as-of ' + args.as_of if as_of_dt else '--until ' + str(args.until)} "
              f"is still in the future; using now instead.")
        window_end = now

    if args.scheduled and not as_of_dt:
        day_end = datetime.combine(run_start.date(), dtime(23, 59, 59))
        if window_end > day_end:
            window_end = day_end

    bucket = bucket_live(matches, window_start, window_end)
    label = "EOD" if args.scheduled else f"Intraday {window_end:%H:%M}"
    print(f"\n  window: {window_start:%d-%b %H:%M}  ->  {window_end:%d-%b %H:%M}")
    print(f"  emails in window: {len(bucket)}")

    if not bucket:
        print("\nNo emails in this window -- nothing to do.")
        return False

    # process_bucket calls send_email_with_attachments AFTER writing the
    # xlsx files but BEFORE moving them to backup, so swapping in a
    # capture function here is the moment those files exist and can be
    # copied out. The real email is sent later, with the report included.
    global send_email_with_attachments
    real_send = send_email_with_attachments
    grabbed = {}

    def capture_email(subject, body, paths):
        grabbed["files"] = []
        for p in paths:
            dest = os.path.join(WORK_FOLDER, os.path.basename(p))
            shutil.copy2(p, dest)
            grabbed["files"].append(dest)
        print(f"  (interim email suppressed; {len(paths)} file(s) copied to the work folder)")

    send_email_with_attachments = capture_email
    try:
        process_bucket(bucket, window_start, window_end, label, permanent=False)
    finally:
        send_email_with_attachments = real_send

    produced = grabbed.get("files", [])
    alloc_log = next((p for p in produced if os.path.basename(p).startswith("Allocation_Logs")), None)
    downwards = next((p for p in produced if os.path.basename(p).startswith("DOWNWARDS_Logs")), None)
    upwards = next((p for p in produced if os.path.basename(p).startswith("UPWARDS_Logs")), None)

    if not alloc_log:
        print("\n[X] No Allocation_Logs file was produced, so the report can't be built.")
        return False

    # -----------------------------------------------------------------
    # STEP 2 -- collect the opening-balance files
    # -----------------------------------------------------------------
    print("\n" + "-" * 70)
    print("STEP 2  collecting opening-balance files")
    print("-" * 70)
    print(f"  looking for the {run_start:%d-%m-%Y} folder "
          f"(date the run STARTED, not the clock now)")

    opening_used = []          # [(snapshot label, original path)] - BOD / AFTERNOON / EVENING allocation
    date_folder = find_date_folder(run_start.date())
    if date_folder:
        opening_used = collect_snapshots(date_folder, WORK_FOLDER)
    else:
        print("  [!] carrying on without opening balances (treated as 0)")

    # -----------------------------------------------------------------
    # STEP 3 -- build the report
    # -----------------------------------------------------------------
    print("\n" + "-" * 70)
    print("STEP 3  building the report")
    print("-" * 70)

    all_rows, log_date = read_log(alloc_log)
    if not all_rows:
        print("[X] No data rows found in the log file.")
        return False
    rows = [r for r in all_rows if is_success(r)]
    excluded = [r for r in all_rows if not is_success(r)]
    print(f"  SUCCESS rows kept : {len(rows)}")
    print(f"  Rows excluded     : {len(excluded)}")
    if not rows:
        print("[X] No SUCCESS rows found - nothing to report.")
        return False

    report = build_report(WORK_FOLDER, rows, excluded, log_date)

    # -----------------------------------------------------------------
    # STEP 4 -- send ONE email with the three files
    # -----------------------------------------------------------------
    print("\n" + "-" * 70)
    print("STEP 4  sending the email")
    print("-" * 70)

    attachments = [p for p in (downwards, upwards, report) if p and os.path.exists(p)]
    for p in attachments:
        print(f"  attaching {os.path.basename(p)}")
    print(f"  NOT attaching {os.path.basename(alloc_log)} (the report replaces it)")

    send_email_with_attachments(
        build_email_subject(label, window_end),
        build_email_body(label, window_start, window_end, {}),
        attachments,
    )

    # -----------------------------------------------------------------
    # STEP 5 -- backup and tidy up
    # -----------------------------------------------------------------
    print("\n" + "-" * 70)
    print("STEP 5  backup and cleanup")
    print("-" * 70)
    dest = os.path.join(BACKUP_DIR, f"{window_end:%Y-%m-%d}", f"{window_end:%H.%M}")
    os.makedirs(dest, exist_ok=True)
    for p in produced + [report]:
        if p and os.path.exists(p):
            shutil.copy2(p, os.path.join(dest, os.path.basename(p)))

    # The 5 opening-balance files are copied in too, in their own
    # sub-folder, so there is a record of WHICH opening files a given
    # report was built from.
    if opening_used:
        ob_dest = os.path.join(dest, "opening_balance_files")
        os.makedirs(ob_dest, exist_ok=True)
        for label, p in opening_used:
            try:
                os.makedirs(os.path.join(ob_dest, label), exist_ok=True)
                shutil.copy2(p, os.path.join(ob_dest, label, os.path.basename(p)))
            except Exception as e:
                print(f"  [!] couldn't back up {os.path.basename(p)}: {e}")
        print(f"  {len(opening_used)} opening-balance file(s) -> {ob_dest}")

    # This run's own log output goes in too, so a backup folder explains
    # itself. Only the portion this run wrote -- see save_log_slices.
    save_log_slices(dest, log_offsets)

    print(f"  everything copied to: {dest}")

    # downloaded_files only ever holds attachments TEMPORARILY; what is
    # left behind is empty timestamped folders. Remove them. Anything still
    # holding a file is left alone.
    removed = 0
    for root, dirs, files in os.walk(DOWNLOAD_DIR, topdown=False):
        if root == DOWNLOAD_DIR:
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
                removed += 1
        except OSError:
            pass
    if removed:
        print(f"  cleared {removed} empty folder(s) from downloaded_files")

    shutil.rmtree(WORK_FOLDER, ignore_errors=True)
    print("  work folder cleared")

    if args.scheduled:
        close_open_window()
        save_window_state(window_end)
        print(f"  day closed; marker moved to {window_end:%d-%b %H:%M}")
    else:
        print(f"  manual run -- marker left at {window_start:%d-%b %H:%M}")

    print("\n" + "=" * 70)
    print("  ALL DONE")
    print("=" * 70)
    return True


if __name__ == "__main__":
    # Guarantee that ANY crash anywhere in main() gets a full traceback
    # written to run_log.txt before the process exits (run_log.txt used to
    # just go silent), plus a failure e-mail.
    try:
        setup_logging()
        main()
    except SystemExit:
        raise
    except Exception:
        logging.exception("FATAL: main() crashed with an unhandled exception -- run aborted.")
        send_failure_alert(traceback.format_exc()[-3000:])
        raise
