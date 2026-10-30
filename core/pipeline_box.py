"""
core/pipeline_box.py — REUSABLE dashboard runner for any pipeline.

Runs a pipeline's existing function as a subprocess (no logic change), captures the
FULL output to a per-process log file, surfaces ONLY email/WhatsApp status to the UI,
and is killable. Add a pipeline by adding one entry to PIPELINES — nothing else.
"""
import os, re, sys, glob, queue, threading, subprocess, datetime, shutil, json

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_BASE = os.path.join(REPO, "logs")


# ── LOG ATTRIBUTION HEADER / FOOTER ──────────────────────────────────────────
# Every log file this module writes opens with the same block, so the answer to
# "who ran this, from where, and did it actually finish?" is IN THE LOG rather
# than only in the live lock metadata and the rolling audit trail — both of
# which are gone or overwritten by the time anyone opens a log days later
# asking that question.
#
# The footer matters as much as the header: without it there is no way to tell
# a run that COMPLETED from one that was killed or crashed halfway, because
# both just... stop. A log with no "ended" line was interrupted.
def _log_identity(explicit_by=None, explicit_ip=None, lock_key=None):
    """(by, ip) for a log header, best source first:
        1. what the caller passed in (read from that request's signed session)
        2. the live lock metadata for this run, if it has already claimed one
        3. lock_manager's 15-second "most recent -stream request" note
    Never raises — a log header must not be able to break a run."""
    if explicit_by:
        return explicit_by, (explicit_ip or "-")
    try:
        from core import lock_manager as _lmi
        if lock_key:
            _m = _lmi._META.get(lock_key) or {}
            if _m.get("by"):
                return _m["by"], (_m.get("ip") or "-")
        _r = _lmi._consume_request()
        return (_r.get("by") or "unknown user"), (_r.get("ip") or "-")
    except Exception:
        return "unknown user", "-"


def who_started_line(explicit_by=None, explicit_ip=None, lock_key=None):
    """One '# started by: ...' line for the top of any log file (never raises)."""
    try:
        by, ip = _log_identity(explicit_by, explicit_ip, lock_key)
    except Exception:
        by, ip = "unknown user", "-"
    if not by or by == "unknown user":
        return "# started by: nobody recorded (automatic run / timer, or started outside the dashboard)\n"
    return f"# started by: {by}  (from {ip or '-'})\n"


def _log_header(title, started_dt, by=None, ip=None, trigger="manual (dashboard)"):
    import socket
    try:
        host = socket.gethostname()
    except Exception:
        host = "-"
    bar = "# " + "=" * 68 + "\n"
    return (bar +
            f"# RMS Copilot — {title}\n" +
            f"# Started : {started_dt.strftime('%Y-%m-%d %H:%M:%S')}\n" +
            f"# By      : {by or 'unknown user'}\n" +
            f"# From    : {ip or '-'}\n" +
            f"# Host    : {host}\n" +
            f"# Trigger : {trigger}\n" +
            bar)


def _log_footer(started_dt, outcome="finished"):
    end = datetime.datetime.now()
    secs = max(0, int((end - started_dt).total_seconds()))
    dur = f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"
    return (f"\n# {'-' * 68}\n"
            f"# Ended   : {end.strftime('%Y-%m-%d %H:%M:%S')}  ·  took {dur}  ·  outcome={outcome}\n")

# ── pipeline registry (extend this per pipeline) ─────────────────────────────
PIPELINES = {
    "ctcl": {"name": "CTCL vs NMASS", "module": "core.pipelines.ctcl_vs_nmass",
             "func": "run_ctcl_vs_nmass", "logdir": "ctcl", "runs": 5,
             "state_dir_env": "CTCL_NMASS_FOLDER", "state_default": r"D:\CTCL VS NMASS",
             "state_file": "run_counter.txt",
             "email_keys": {"Main": ["CTCL_MAIN_TO", "CTCL_MAIN_CC"],
                            "Chandigarh": ["CTCL_CHND_TO", "CTCL_CHND_CC"],
                            "Ludhiana": ["CTCL_LUDH_TO", "CTCL_LUDH_CC"],
                            "Mumbai": ["CTCL_MUMB_TO", "CTCL_MUMB_CC"],
                            "Delhi": ["CTCL_DELH_TO", "CTCL_DELH_CC"],
                            "Top20 CDR+MT": ["CTCL_TOP20_CDRMT_TO", "CTCL_TOP20_CC"],
                            "Top20 V2": ["CTCL_TOP20_V2_TO", "CTCL_TOP20_CC"],
                            "Top20 V1": ["CTCL_TOP20_V1_TO", "CTCL_TOP20_CC"],
                            "Payout Check": ["PAYOUT_CHECK_TO", "PAYOUT_CHECK_CC"],
                            "Cash Risk Codes": ["CTCL_SHORTFALL_EMAIL_TO", "CTCL_SHORTFALL_EMAIL_CC"]},
             "cred": {"SMTP sender (email account)": {"user_key": "SENDER_EMAIL",
                       "pass_key": "SENDER_PASSWORD",
                       "note": "the email account CTCL sends reports from"},
                      "NMASS / NSCCL login": {"user_key": "CTCL_NMASS_USER",
                       "pass_key": "CTCL_NMASS_PASSWORD",
                       "note": "connect2nsccl.com login used by NMASS_NCFE_API_MIX.PY"},
                      "NMASS member code": {"single_key": "CTCL_NMASS_CONSCODE",
                       "note": "consCode sent with every NMASS request — not a login password"},
                      "NCDEX login": {"user_key": "CTCL_NCDEX_USER",
                       "pass_key": "CTCL_NCDEX_PASSWORD",
                       "note": "NCDEX portal login used for the CTCL NCDEX download"},
                      "MCX 90% login": {"user_key": "CTCL_MCX90_USER",
                       "pass_key": "CTCL_MCX90_PASSWORD",
                       "note": "eclear.mcxccl.com login used by MCX_90_PERCENT_FILE_API.py"},
                      "WhatsApp (Tata) bearer token — CASH RISK CODES": {"single_key": "WA_AUTH_TOKEN",
                       "note": "sends the Run 1 CASH RISK CODES dashboard images (core/pipelines/margin_shortfall_dashboard.py) — "
                               "same Tata account MS15's shortage image alert uses. Rotate here once ready; "
                               "the value pasted into this integration earlier must be treated as compromised."}},
             "upload_dir_env": "CTCL_NMASS_FOLDER", "upload_default": r"D:\CTCL VS NMASS",
             # secondary upload target — files the pipeline reads from C:\COMMON FILES
             "common_dir_env": "SA_COMMON_DIR", "common_default": r"C:\COMMON FILES",
             "common_globs": ["RISK_REPORT_*.csv"], "common_label": "COMMON FILES",
             "backup_root_env": "CTCL_NMASS_FOLDER", "backup_subdir": "BACKUPFILES AUTOMATED",
             "file_types": [
                 ["CM Client Collaterals", "CM_ClientLevelCollaterals_*.csv,CM_ClientLevelCollaterals_*.xlsx", "CM_ClientLevelCollaterals_<date>.csv"],
                 ["FO Client Collaterals", "FO_ClientLevelCollaterals_*.csv,FO_ClientLevelCollaterals_*.xlsx", "FO_ClientLevelCollaterals_<date>.csv"],
                 ["CO Client Collaterals", "CO_ClientLevelCollaterals_*.csv,CO_ClientLevelCollaterals_*.xlsx", "CO_ClientLevelCollaterals_<date>.csv"],
                 ["Client Net Effective Deposits", "ClientNetEffectiveDeposits_*.csv,ClientNetEffectiveDeposits_*.xlsx", "ClientNetEffectiveDeposits_<date>.csv"],
                 ["Update Limits", "Update Limits*.xlsx", "Update Limits <date>.xlsx"],
                 ["RISK_REPORT", "RISK_REPORT_*.csv", "RISK_REPORT_<date>.csv"],
                 ["MTF risk report", "MTF_RISK_REPORT_*.csv", "MTF_RISK_REPORT_<date>.csv"],
                 ["T5 DEBIT report", "T5DEBIT_*.csv", "T5DEBIT_<date>.csv"],
                 ["MCX 90% file (xlsx)", "*.xlsx", "<random>-<YYYYMMDDHHMMSS>.xlsx"],
                 ["CTCL output file", "FULL_FILE_CTCL_VS_NMASS_*.xlsx", "FULL_FILE_CTCL_VS_NMASS_<NN>.xlsx"],
             ],
             # Phase transition markers — emitted as {type:'phase', name:'...'} SSE events
             "phase_patterns": [
                 (r"CTCL EMAILS COMPLETED",            {"type": "phase", "name": "ctcl_emails_done"}),
                 (r"RUN 1 DETECTED.*STARTING TOP20",   {"type": "phase", "name": "top20"}),
                 (r"RUN 1 DETECTED.*STARTING CASH RISK CODES", {"type": "phase", "name": "cash_risk_codes"}),
                 (r"STARTING PAYOUT DOWNLOAD",          {"type": "phase", "name": "payout"}),
                 (r"PAYOUT CHECKING COMPLETED",         {"type": "phase", "name": "payout_done"}),
             ]},
    # Client Prudential Limit — runs THREE times a day (morning / afternoon /
    # evening). Two independent steps (Download, Upload) rather than CTCL's single
    # chained pipeline; the frontend runs both in sequence for the slot tiles' Run
    # button (cliRunFull()) and also exposes them as standalone buttons for manual
    # re-run of just one step.
    # Only the MORNING slot rebuilds CLI_PRUDENTIAL_LIMIT.txt; afternoon and
    # evening reuse it and upload into ALLOCATION(AFTERNOON) / ALLOCATION(EVENING).
    # The window times and the whole slot chain live in
    # core/pipelines/cli_allocation_new.py's module docstring — read that before
    # changing `runs` here, since the two must agree.
    # See core/pipelines/cli_download.py + core/pipelines/cli_upload.py.
    "cli": {"name": "Client Prudential Limit", "logdir": "cli", "runs": 3,
            "state_dir_env": "CLI_FOLDER", "state_default": r"D:\CLI_PRUDENTIAL_LIMIT",
            "state_file": "run_counter.txt",
            "email_keys": {},
            # Credentials reuse existing CTCL vs NMASS / SA MCX Deposit entries —
            # same broker accounts, so one password update fixes every process that
            # uses that account instead of three separate stale copies.
            "cred": {"NSCCL / NMASS login": {"user_key": "CTCL_NMASS_USER",
                      "pass_key": "CTCL_NMASS_PASSWORD",
                      "note": "connect2nsccl.com login — shared with CTCL vs NMASS"},
                     "NMASS member code": {"single_key": "CTCL_NMASS_CONSCODE",
                      "note": "consCode sent with every NSCCL request — shared with CTCL vs NMASS"},
                     "NCDEX / NCFE login": {"user_key": "CTCL_NCDEX_USER",
                      "pass_key": "CTCL_NCDEX_PASSWORD",
                      "note": "ncfe.ncdex.com login — shared with CTCL vs NMASS"},
                     "MCX RSK383 login": {"user_key": "SA_MCX_DEPOSIT_USER",
                      "pass_key": "SA_MCX_DEPOSIT_PASSWORD",
                      "note": "eclear.mcxccl.com \"Query Detailed Limit\" login — shared with the Short Allocation MCX Deposit button (NOT the same report as MCX 90%)"},
                     "FTP (Allocation files)": {"user_key": "FTP_USER",
                      "pass_key": "FTP_PASS",
                      "note": "202.58.232.124 — same FTP server other downloaders use"},
                     "Kambala FTP (second upload)": {"user_key": "CLI_KAMB_FTP_USER",
                      "pass_key": "CLI_KAMB_FTP_PASS",
                      "note": "202.58.232.29, port 21 — the same files are also uploaded here. Blank user = ftpadmin."},
                     "Kambala FTP folder": {"single_key": "CLI_KAMB_FTP_DIR",
                      "note": "folder on the Kambala FTP where the files go (created if missing). Blank = the login folder (/)."}},
            "upload_dir_env": "CLI_FOLDER", "upload_default": r"D:\CLI_PRUDENTIAL_LIMIT",
            # secondary upload target — RISK_REPORT lives in the shared common
            # folder, same as CTCL vs NMASS's copy of it (same file, same source).
            "common_dir_env": "SA_COMMON_DIR", "common_default": r"C:\COMMON FILES",
            "common_globs": ["RISK_REPORT_*.csv"], "common_label": "COMMON FILES",
            "backup_root_env": "CLI_FOLDER", "backup_subdir": "BACKUPFILES AUTOMATED",
            "file_types": [
                ["Allocation Detail", "Allocation_Detail_*.csv", "Allocation_Detail_<DDMMYYYY>.csv"],
                ["CLI Prudential Limit", "CLI_PRUDENTIAL_LIMIT.txt", "CLI_PRUDENTIAL_LIMIT.txt"],
                ["CM Client Collaterals", "CM_ClientLevelCollaterals_*.csv,CM_ClientLevelCollaterals_*.txt", "CM_ClientLevelCollaterals_<date>.csv"],
                ["FO Client Collaterals", "FO_ClientLevelCollaterals_*.csv,FO_ClientLevelCollaterals_*.txt", "FO_ClientLevelCollaterals_<date>.csv"],
                ["CO Client Collaterals", "CO_ClientLevelCollaterals_*.csv,CO_ClientLevelCollaterals_*.txt", "CO_ClientLevelCollaterals_<date>.csv"],
                ["Client Net Effective Deposits", "ClientNetEffectiveDeposits_*.csv", "ClientNetEffectiveDeposits_<date>.csv"],
                ["MCX Web Allocation/Deallocation", "MCX_WebAllocationDeallocation.csv", "MCX_WebAllocationDeallocation.csv"],
                ["MCX Deposit (RSK383 export, xlsx)", "*.xlsx", "<random>-<YYYYMMDDHHMMSS>.xlsx"],
                ["RISK_REPORT", "RISK_REPORT_*.csv", "RISK_REPORT_<date>.csv"],
                ["Kambala", "Kambala_Omny_Depo_Adhoc_All_level.txt_*.txt", "Kambala_Omny_Depo_Adhoc_All_level.txt_<DDMMYYYY>.txt"],
            ]},
    # R1-R2 Comparison Report — Live Snapshot / Afternoon /
    # Evening. Same "not the generic numbered px runner" shape as CLI: Live
    # Snapshot repeats any number of times with no fixed order relative to
    # Afternoon/Evening, so the 3 buttons hit their own bespoke SSE endpoints
    # (/r1r2/live-stream, /r1r2/afternoon-stream, /r1r2/evening-stream in
    # api.py) instead of /px/run-stream?pipe=r1r2&run_no=N. This registry
    # entry exists for the shared infra only — Backup Files browser, log
    # viewer, credentials modal. See core/pipelines/r1_r2_comparison.py.
    "r1r2": {"name": "R1-R2 Comparison Report", "logdir": "r1r2", "runs": 1,
             # state_default/upload_default MUST match core.pipelines.r1_r2_comparison's
             # own BASE_FOLDER default (and nmass_download.py's _PIPE_FOLDER["r1r2"],
             # which mirrors it) — all three resolve R1R2_FOLDER the same way, falling
             # back to <repo>\data\r1_r2_comparison when it isn't set in .env. This used
             # to hardcode D:\R1_R2_COMPARISON here, which only matched machines that
             # happen to set R1R2_FOLDER to exactly that path — on any machine where it's
             # unset, Upload files/Backup files/px/status all silently pointed at a
             # folder nothing ever wrote to (same class of bug fixed in the r1r2 captcha
             # endpoints in api.py — see those docstrings). upload_default also doubles
             # as the backup-root fallback (pb_backup path: os.getenv(backup_root_env,
             # upload_default)), so fixing it here covers Backup files too.
             "state_dir_env": "R1R2_FOLDER", "state_default": os.path.join(REPO, "data", "r1_r2_comparison"),
             "state_file": "run_counter.txt",
             "email_keys": {"Report": ["R1R2_EMAIL_TO", "R1R2_EMAIL_CC"]},
             "cred": {"SMTP sender (email account)": {"user_key": "SENDER_EMAIL",
                       "pass_key": "SENDER_PASSWORD",
                       "note": "same account every other pipe here sends reports from"},
                      "NSCCL / NMASS login": {"user_key": "CTCL_NMASS_USER",
                       "pass_key": "CTCL_NMASS_PASSWORD",
                       "note": "connect2nsccl.com login (Live Snapshot only) — shared with CTCL vs NMASS"},
                      "NCDEX / NCFE login": {"user_key": "CTCL_NCDEX_USER",
                       "pass_key": "CTCL_NCDEX_PASSWORD",
                       "note": "ncfe.ncdex.com login (Live Snapshot only) — shared with CTCL vs NMASS"},
                      "MCX RSK383 (TCS BANCS) login": {"user_key": "SA_MCX_DEPOSIT_USER",
                       "pass_key": "SA_MCX_DEPOSIT_PASSWORD",
                       "note": "eclear.mcxccl.com \"Query Detailed Limit\" login (Live Snapshot only) — "
                               "shared with Short Allocation's MCX Deposit / CLI's MCX Web Allocation buttons"}},
             "upload_dir_env": "R1R2_FOLDER", "upload_default": os.path.join(REPO, "data", "r1_r2_comparison"),
             "backup_root_env": "R1R2_FOLDER", "backup_subdir": "Backup",
             "file_types": [
                 ["CM Client Collaterals", "CM_ClientLevelCollaterals_*.csv,CM_ClientLevelCollaterals_*.txt,CM_ClientLevelCollaterals_*.xlsx", "CM_ClientLevelCollaterals_<date>.csv"],
                 ["FO Client Collaterals", "FO_ClientLevelCollaterals_*.csv,FO_ClientLevelCollaterals_*.txt,FO_ClientLevelCollaterals_*.xlsx", "FO_ClientLevelCollaterals_<date>.csv"],
                 ["CO Client Collaterals", "CO_ClientLevelCollaterals_*.csv,CO_ClientLevelCollaterals_*.txt,CO_ClientLevelCollaterals_*.xlsx", "CO_ClientLevelCollaterals_<date>.csv"],
                 ["Client Net Effective Deposits", "ClientNetEffectiveDeposits_*.csv,ClientNetEffectiveDeposits_*.xlsx", "ClientNetEffectiveDeposits_<date>.csv"],
                 ["MCX (RSK383 export / Web Allocation)", "*.xlsx,MCX_WebAllocationDeallocation.csv", "<random>-<YYYYMMDDHHMMSS>.xlsx"],
                 ["R1-R2 Comparison Report (output)", "R1-R2_COMPARISON_REPORT.xlsx", "R1-R2_COMPARISON_REPORT.xlsx"],
             ]},
    # RECONCILIATION — 4 daily processes (HOLDING / T1 HOLDING / NFO BFO MTFS / KAMBALA
    # POSITION) + "Run all". Bespoke SSE endpoints (/reco/*, api.py) driven by
    # core/pipelines/reconciliation.py — this entry is for the shared infra (Credentials,
    # Email To/CC, Upload to COMMON FILES, Backup browser, Logs). HOLDING_*.csv and
    # RISK_REPORT_*.csv are COMMON FILES types: uploading a new day's file archives the
    # old one into COMMON FILES\BACKUP FILES AUTOMATED\<its date> (that archive is
    # where "previous HOLDING" is read from).
    "reco": {"name": "RECONCILIATION", "logdir": "reco", "runs": 1,
             "state_dir_env": "RECO_FOLDER", "state_default": os.path.join(REPO, "data", "RECONCILIATION"),
             "state_file": "run_counter.txt",
             "email_default": "kartavya.dhillon@mastertrust.co.in",
             "email_keys": {"HOLDING": ["RECO_HOLDING_TO", "RECO_HOLDING_CC"],
                            "T1 HOLDING": ["RECO_T1_TO", "RECO_T1_CC"],
                            "NFO BFO MTFS": ["RECO_NFO_TO", "RECO_NFO_CC"],
                            "KAMBALA POSITION": ["RECO_KAMB_TO", "RECO_KAMB_CC"]},
             "cred": {"SMTP sender (email account)": {"user_key": "SENDER_EMAIL",
                       "pass_key": "SENDER_PASSWORD",
                       "note": "the account the four reports are sent from. Leave blank to keep the login written inside the scripts."},
                      "NSE extranet (member code / login / password)": {"user_key": "RECO_NSE_LOGIN_ID",
                       "pass_key": "RECO_NSE_PASSWORD",
                       "note": "connect2nse.com extranet login (Trade + SEC_PLEDGE files). Blank = the login inside HOLDING_final.py is used."},
                      "NSE member code": {"single_key": "RECO_NSE_MEMBER",
                       "note": "e.g. 06436. Blank = taken from HOLDING_final.py."},
                      "MCX RSK136 (T1 SearchResults) login": {"user_key": "RECO_MCX_USER",
                       "pass_key": "RECO_MCX_PASSWORD",
                       "note": "eclear.mcxccl.com login used by search_results.py (RSA token is asked in the dashboard). Blank = the one inside the script."},
                      "FTP (Kambala / Symphony / Noren files)": {"user_key": "FTP_USER",
                       "pass_key": "FTP_PASS",
                       "note": "same FTP the CLI box uploads to"}},
             "upload_dir_env": "RECO_FOLDER", "upload_default": os.path.join(REPO, "data", "RECONCILIATION"),
             "common_dir_env": "SA_COMMON_DIR", "common_default": r"C:\COMMON FILES",
             "common_globs": ["HOLDING_*.csv", "RISK_REPORT_*.csv"], "common_label": "COMMON FILES",
             "backup_root_env": "RECO_FOLDER", "backup_subdir": "Backup",
             "file_types": [
                 ["HOLDING (COMMON FILES)", "HOLDING_*.csv", "HOLDING_<DDMMYYYY>.csv"],
                 ["RISK_REPORT (COMMON FILES)", "RISK_REPORT_*.csv", "RISK_REPORT_<DDMMYYYY>.csv"],
                 ["EXCEPTION (kept once)", "EXCEPTION*.xls,EXCEPTION*.xlsx,EXCEPTION*.xlsm", "EXCEPTION_<date>.xlsx"],
                 ["PositionBook (with MCX)", "*position*.xlsx", "PositionBook.xlsx"],
                 ["Reconciliation outputs", "*.xlsx,*.csv,*.txt", "—"],
             ]},
    # MTF Impact Cost — one on-demand run (tile in Processing & Reports). Everything lives in ONE
    # folder; HOLDING is read from COMMON FILES and never touched. After a completed run the used
    # + generated files are backed up to Backup\<date>\<time> and removed from the base folder.
    # Bespoke endpoints (/mtfic/*, api.py) → core/pipelines/mtf_impact_cost.py.
    "mtfic": {"name": "MTF Impact Cost", "logdir": "mtfic", "runs": 1,
              "state_dir_env": "MTF_IMPACT_FOLDER", "state_default": os.path.join(REPO, "data", "SECURITY CAT IMPACT COST"),
              "state_file": "run_counter.txt",
              "email_keys": {},
              "cred": {"WhatsApp (Tata) bearer token": {"single_key": "MTFIC_WA_TOKEN",
                        "note": "sends the dashboard image. Leave blank to use the shared WA_AUTH_TOKEN."},
                       "WhatsApp numbers": {"single_key": "MTFIC_WA_NUMBERS",
                        "note": "10-digit numbers only (+91 is added automatically), optionally with a name after #, comma separated. Example: 9876543210#Rahul, 9123456789#Priya. Blank = nothing is sent."}},
              "upload_dir_env": "MTF_IMPACT_FOLDER", "upload_default": os.path.join(REPO, "data", "SECURITY CAT IMPACT COST"),
              "backup_root_env": "MTF_IMPACT_FOLDER", "backup_subdir": "Backup",
              "file_types": [
                  ["TradeBook", "TradeBook_*.csv,TradeBook_*.xlsx,TradeBook_*.xls", "TradeBook_<DDMMYYYY>.csv"],
                  ["NSE impact-cost file", "C_CATG_*.T01", "C_CATG_<MON><YYYY>.T01"],
                  ["Reconciliation Excel", "Reconciliation_MTF_ImpactCost_*.xlsx", "Reconciliation_MTF_ImpactCost_<DDMMYYYY>.xlsx"],
                  ["Dashboard image", "Reconciliation_MTF_Dashboard_*.png", "Reconciliation_MTF_Dashboard_<DDMMYYYY>.png"],
              ]},
    # Allocation report (manual runs from the Process Center tile; the nightly close runs in the Scheduler).
    "alloc": {"name": "Allocation Report", "logdir": "alloc", "runs": 1,
              "state_dir_env": "ALLOC_REPORT_FOLDER",
              "state_default": os.path.join(REPO, "data", "scheduler", "ALLOCATION REPORT"),
              "state_file": "run_counter.txt",
              "email_keys": {"Report e-mail (used when 'E-mail to' is left blank)": ["ALLOC_MAIL_TO", "ALLOC_MAIL_CC"]},
              "email_default": "",
              "cred": {"Mailbox the logs arrive in (POP3)": {"user_key": "ALLOC_POP_USER", "pass_key": "ALLOC_POP_PASS",
                        "note": "the inbox that receives the Allocation_Logs e-mails"},
                       "Report sender (SMTP)": {"user_key": "ALLOC_SMTP_USER", "pass_key": "ALLOC_SMTP_PASS",
                        "note": "the mail account the report is sent from"}},
              "upload_dir_env": "ALLOC_REPORT_FOLDER",
              "upload_default": os.path.join(REPO, "data", "scheduler", "ALLOCATION REPORT"),
              "backup_root_env": "ALLOC_REPORT_FOLDER", "backup_subdir": "BACKUPFILES AUTOMATED",
              "file_types": []},
    # Margin Shortage (15-min) — runs 17x/day, ported from 15minutesfinal_whatsapp.PY.
    # (Was 9, then 11 — 16:30 and 16:50 added when the desk's shift extended; now 17 with the
    # evening runs 18:20 … 22:50 (MS15_RUN12..17_WINDOW in .env). The night
    # shift after 16:50 still runs the original batch script on another machine —
    # see MS15_TRACKER_JSON in .env for how the dedup state is handed over.)
    # Reuses the SAME NSCCL/NCFE/MCX90 download credentials as CTCL vs NMASS (shared
    # accounts) via core/pipelines/nmass_download.py + mcx90_download.py, called with
    # pipe="ms15" so files land in MARGIN_SHORTAGE_FOLDER instead of CTCL_NMASS_FOLDER.
    # MCX 90% is additionally auto-shared with CTCL's box at 3 confirmed paired run
    # times (see mcx90_download.py's _SHARE_PAIRS) — NSCCL/NCDEX are never shared.
    # DB archival tag for this pipeline's own records is "margin_shortage" (separate
    # from the "ms15" routing key here) — see core/pipelines/margin_shortage_15min.py.
    "ms15": {"name": "Margin Shortage (15-min)", "module": "core.pipelines.margin_shortage_15min",
             "func": "run_margin_shortage_15min", "logdir": "ms15", "runs": 17,
             "state_dir_env": "MARGIN_SHORTAGE_FOLDER", "state_default": r"D:\15 minutes",
             "state_file": "run_counter.txt",
             "email_keys": {"Main": ["MS15_MAIN_TO", "MS15_MAIN_CC"],
                            "Delhi": ["MS15_DELH_TO", "MS15_DELH_CC"],
                            "Ludhiana": ["MS15_LUDH_TO", "MS15_LUDH_CC"],
                            "Mumbai": ["MS15_MUMB_TO", "MS15_MUMB_CC"],
                            "Chandigarh": ["MS15_CHND_TO", "MS15_CHND_CC"]},
             # Download credentials are shared with CTCL vs NMASS (same broker accounts) —
             # a password update here fixes both processes at once. WhatsApp (Gupshup) and
             # the Mastertrust webpayout mobile-lookup are new/separate for this pipeline.
             "cred": {"SMTP sender (email account)": {"user_key": "SENDER_EMAIL",
                       "pass_key": "SENDER_PASSWORD",
                       "note": "same email account CTCL vs NMASS sends reports from"},
                      "NMASS / NSCCL login": {"user_key": "CTCL_NMASS_USER",
                       "pass_key": "CTCL_NMASS_PASSWORD",
                       "note": "connect2nsccl.com login — shared with CTCL vs NMASS"},
                      "NMASS member code": {"single_key": "CTCL_NMASS_CONSCODE",
                       "note": "consCode sent with every NMASS request — shared with CTCL vs NMASS"},
                      "NCDEX login": {"user_key": "CTCL_NCDEX_USER",
                       "pass_key": "CTCL_NCDEX_PASSWORD",
                       "note": "NCDEX portal login — shared with CTCL vs NMASS"},
                      "MCX 90% login": {"user_key": "CTCL_MCX90_USER",
                       "pass_key": "CTCL_MCX90_PASSWORD",
                       "note": "eclear.mcxccl.com login — shared with CTCL vs NMASS"},
                      "WhatsApp (Gupshup) API key": {"single_key": "GUPSHUP_API_KEY",
                       "note": "client-facing margin alert — separate from the Tata WhatsApp used elsewhere"},
                      "Mastertrust webpayout login": {"user_key": "MT_WEBPAYOUT_USER",
                       "pass_key": "MT_WEBPAYOUT_PASSWORD",
                       "note": "used to look up each client's mobile number for WhatsApp alerts"}},
             "upload_dir_env": "MARGIN_SHORTAGE_FOLDER", "upload_default": r"D:\15 minutes",
             # secondary upload target — same shared RISK_REPORT mechanism as CTCL vs NMASS.
             "common_dir_env": "SA_COMMON_DIR", "common_default": r"C:\COMMON FILES",
             "common_globs": ["RISK_REPORT_*.csv"], "common_label": "COMMON FILES",
             "backup_root_env": "MARGIN_SHORTAGE_FOLDER", "backup_subdir": "BACKUPFILES AUTOMATED",
             "file_types": [
                 ["CM Client Collaterals", "CM_ClientLevelCollaterals_*.csv,CM_ClientLevelCollaterals_*.xlsx", "CM_ClientLevelCollaterals_<date>.csv"],
                 ["FO Client Collaterals", "FO_ClientLevelCollaterals_*.csv,FO_ClientLevelCollaterals_*.xlsx", "FO_ClientLevelCollaterals_<date>.csv"],
                 ["CO Client Collaterals", "CO_ClientLevelCollaterals_*.csv,CO_ClientLevelCollaterals_*.xlsx", "CO_ClientLevelCollaterals_<date>.csv"],
                 ["Client Net Effective Deposits", "ClientNetEffectiveDeposits_*.csv,ClientNetEffectiveDeposits_*.xlsx", "ClientNetEffectiveDeposits_<date>.csv"],
                 ["RISK_REPORT", "RISK_REPORT_*.csv", "RISK_REPORT_<date>.csv"],
                 ["MCX 90% file (xlsx)", "*.xlsx", "<random>-<YYYYMMDDHHMMSS>.xlsx"],
                 ["Margin Shortage output", "Shortage_vs_CC_Deposit*.xlsx", "Shortage_vs_CC_Deposit.xlsx"],
             ]},
    # Probable Defaulter — ported from the user-supplied "probable for claude" /
    # "PROBABLE FINAL NEW" reference folders (codes/API HITTING/code1.py +
    # code2.py + tcs_bancs_probable.py). Runs once/day: NMASS/NCDEX auto-download
    # (reuses core/pipelines/nmass_download.py, same as ctcl/ms15), TCS BANCS
    # RSK383+RSK335 (core/pipelines/probable_tcs_bancs.py, shares the SA MCX
    # Deposit / CLI MCX Web Allocation cross-process session lock), reconciliation
    # (core/pipelines/probable_reconcile.py) then alerts (core/pipelines/
    # probable_alerts.py) — chained by core/pipelines/probable_full.py so the
    # dashboard's single Run button drives both stages, with the operator's
    # chosen market-session time threaded through via the PROBABLE_MARKET_TIME
    # env var (set by /px/run-stream's extra_env for this pipe only — see
    # api.py's px_run_stream and the injected market-time web modal in
    # process-probable-box.js). The Probable_Fullfile_<date>.xlsx base file is
    # downloaded manually each day by the operator — recognized below as an
    # upload file type, not something this pipeline ever fetches itself.
    "probable": {"name": "PROBABLE", "module": "core.pipelines.probable_full",
                 "func": "run_probable_full", "logdir": "probable", "runs": 1,
                 "state_dir_env": "PROBABLE_FOLDER", "state_default": r"D:\PROBABLE",
                 "state_file": "run_counter.txt",
                 "email_keys": {"Reconciliation Report": ["PROBABLE_RECON_TO", "PROBABLE_RECON_CC"],
                                "Chandigarh": ["PROBABLE_CHND_TO", "PROBABLE_CHND_CC"],
                                "Ludhiana": ["PROBABLE_LUDH_TO", "PROBABLE_LUDH_CC"],
                                "Mumbai": ["PROBABLE_MUMB_TO", "PROBABLE_MUMB_CC"],
                                "Final Summary": ["PROBABLE_FINAL_TO", "PROBABLE_FINAL_CC"]},
                 # Branch / region e-mails always go to the addresses in MASTER\MAIL LIST.xlsx (and CONTACT_AP Details)
                 # — there is no on-screen override for them any more (see probable_alerts._step_10_dynamic_mail).
                 "cred": {"NSE Delivery Margin login": {"user_key": "SA_NSE_LOGINID",
                           "pass_key": "SA_NSE_PASSWORD",
                           "note": "connect2nse.com login — shared with Short Allocation's NSE download"},
                          "NSE Delivery Margin member code": {"single_key": "SA_NSE_MEMBER",
                           "note": "memberCode sent with every NSE request — shared with Short Allocation"},
                          "NMASS / NSCCL login": {"user_key": "CTCL_NMASS_USER",
                           "pass_key": "CTCL_NMASS_PASSWORD",
                           "note": "connect2nsccl.com login — shared with CTCL vs NMASS / Margin Shortage"},
                          "NMASS member code": {"single_key": "CTCL_NMASS_CONSCODE",
                           "note": "consCode sent with every NMASS request — shared with CTCL vs NMASS"},
                          "NCDEX login": {"user_key": "CTCL_NCDEX_USER",
                           "pass_key": "CTCL_NCDEX_PASSWORD",
                           "note": "NCDEX portal login — shared with CTCL vs NMASS / Margin Shortage"},
                          "MCX RSK383/RSK335 (TCS BANCS) login": {"user_key": "SA_MCX_DEPOSIT_USER",
                           "pass_key": "SA_MCX_DEPOSIT_PASSWORD",
                           "note": "eclear.mcxccl.com login — shared with Short Allocation's MCX Deposit "
                                   "and Client Prudential Limit's MCX Web Allocation buttons"},
                          "SMTP sender — Reconciliation report": {"user_key": "SENDER_EMAIL",
                           "pass_key": "SENDER_PASSWORD",
                           "note": "same kartavya account CTCL/MS15 send reports from"},
                          "SMTP sender — Alerts (zone/branch/region emails)":
                           {"user_key": "PROBABLE_ALERTS_SMTP_EMAIL", "pass_key": "PROBABLE_ALERTS_SMTP_PASSWORD",
                            "note": "dedicated sender account for the alerts stage (raj.kumari, per the reference script)"},
                          "WhatsApp (Tata) bearer token": {"single_key": "PROBABLE_WA_TOKEN",
                           "note": "client-facing 'probable_margin_short' template — same Tata account as PEAK MARGIN's WA_AUTH_TOKEN"},
                          "Mastertrust webpayout login (mobile lookup)": {"user_key": "MT_WEBPAYOUT_USER",
                           "pass_key": "MT_WEBPAYOUT_PASSWORD",
                           "note": "same OAuth already used by Margin Shortage 15-min to look up client mobiles"},
                          "Excel zone-file password": {"single_key": "PROBABLE_ZONE_XLSX_PASSWORD",
                           "note": "password applied to the 4 zone Excel files before emailing (win32com)"}},
                 "upload_dir_env": "PROBABLE_FOLDER", "upload_default": r"D:\PROBABLE",
                 # No genuinely separate "common files" folder for this pipe (unlike
                 # CTCL/MS15/CLI's shared RISK_REPORT) — pointed at the SAME base
                 # folder defensively so dashboard.html's generic pxUpload() modal
                 # (which unconditionally renders a "COMMON FILES" upload button
                 # for every pipe) can't crash trying to os.makedirs("") if that
                 # button is ever clicked for Probable; it just lands harmlessly
                 # in the same base folder instead.
                 "common_dir_env": "PROBABLE_FOLDER", "common_default": r"D:\PROBABLE",
                 "common_globs": [], "common_label": "PROBABLE",
                 "backup_root_env": "PROBABLE_FOLDER", "backup_subdir": "BACKUPFILES AUTOMATED",
                 "file_types": [
                     ["Probable Fullfile (manual daily upload)", "Probable_Fullfile_[0-9]*.xlsx", "Probable_Fullfile_<DDMMYYYY>.xlsx"],
                     ["CM Client Collaterals", "CM_ClientLevelCollaterals_*.csv,CM_ClientLevelCollaterals_*.xlsx", "CM_ClientLevelCollaterals_<date>.csv"],
                     ["FO Client Collaterals", "FO_ClientLevelCollaterals_*.csv,FO_ClientLevelCollaterals_*.xlsx", "FO_ClientLevelCollaterals_<date>.csv"],
                     ["CO Client Collaterals", "CO_ClientLevelCollaterals_*.csv,CO_ClientLevelCollaterals_*.xlsx", "CO_ClientLevelCollaterals_<date>.csv"],
                     ["Client Net Effective Deposits", "ClientNetEffectiveDeposits_*.csv,ClientNetEffectiveDeposits_*.xlsx", "ClientNetEffectiveDeposits_<date>.csv"],
                     ["MCX Deposit (random-name file, e.g. uCYuWHgDZp-20261028082545.xlsx)",
                      "*-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].xlsx,MCX_DEPOSIT_*.xlsx",
                      "<random>-<YYYYMMDDHHMMSS>.xlsx"],
                     ["MCX 90% file (Clientwise_mcx_margin)", "Clientwise_mcx_margin*.xlsx", "Clientwise_mcx_margin.xlsx"],
                     ["Delivery Margin", "F_DLMG02_06436*.csv", "F_DLMG02_06436_<DDMMYYYY>.csv"],
                     ["Probable Fullfile K (reconciled output we create)", "Probable_Fullfile_K_*.xlsx", "Probable_Fullfile_K_<DDMMYYYY>.xlsx"],
                     ["Margin comparison output", "All_Exchange_Margin_Comparison_*.xlsx", "All_Exchange_Margin_Comparison_<DDMMYYYY>.xlsx"],
                     ["WhatsApp log", "WhatsApp_Log.xlsx", "WhatsApp_Log.xlsx"],
                     # Zone shortage files + branch/region mail-list working files
                     # (probable_alerts.py's _step_7_zone_emails / _step_10_dynamic_mail /
                     # _step_11_validation) — previously missing from this list entirely,
                     # so they were never selectable/labeled in the Backup Files type filter
                     # even though they were always physically present in the SHORTAGE backup.
                     ["Zone shortage files (all 4 zones)",
                      "ZOMUMB_ZONE DATA.xlsx,ZOCHND_ZONE DATA.xlsx,DELHI_ZONE DATA.xlsx,LUDH_ZONE DATA.xlsx",
                      "ZOMUMB_ZONE DATA.xlsx"],
                     ["Branch/region mail-list working files",
                      "Filtered_Zone_ID.xlsx,Branch_Region_ID_Extracted.xlsx,Mismatched_Branch_And_Region_IDs.xlsx",
                      "Filtered_Zone_ID.xlsx"],
                     ["Mail send log", "mail_log.csv", "mail_log.csv"],
                     # MASTER reference files (updated roughly once a month, NOT daily) — see
                     # upload_dir_env note below for the dedicated MASTER upload destination.
                     ["MASTER — Mail list", "MAIL LIST.xlsx", "MASTER/MAIL LIST.xlsx"],
                     ["MASTER — Branch & Region list", "Branch_AND_Region_LIST.xlsx", "MASTER/Branch_AND_Region_LIST.xlsx"],
                 ],
                 # MASTER files (MAIL LIST.xlsx, Branch_AND_Region_LIST.xlsx — read by
                 # probable_alerts.py's _step_10_dynamic_mail/_step_11_validation) live in
                 # PROBABLE_FOLDER\MASTER, a THIRD destination the generic pb_upload() target
                 # system didn't have (only "main"/PROBABLE_FOLDER and "common" existed before).
                 # These are updated rarely (per operator: about once a month), so uploading
                 # one should always REPLACE the existing file (see pb_upload's target="master"
                 # branch), unlike daily input files where "already present" correctly means
                 # "already downloaded today, don't re-fetch."
                 "master_dir_env": "PROBABLE_FOLDER", "master_subdir": "MASTER",
                 "master_globs": ["mail list.xlsx", "branch_and_region_list.xlsx"],
                 "master_label": "MASTER",
                 },
    # PHYSICAL DELIVERY — runs once a day in the 5 trading days before the last-Tuesday expiry, and as many
    # times as needed on expiry day itself (so "runs" is not 1 — the once-a-day guard must not apply; the
    # 5-minute duplicate cooldown still does).  Everything lives inside the console: the report scripts are
    # core/pipelines/physdel_codes/{ltp,send}.py, driven by core/pipelines/physical_delivery.py, and the
    # folders / dates / test mode by core/pipelines/physdel_cfg.py.  Uploads: PositionBook -> INPUT, master
    # files -> MAIN_FOLDER, RISK_REPORT -> COMMON FILES.  The CTCL vs NMASS output is NOT uploaded — each run
    # picks it from the latest CTCL vs NMASS backup and shows which file that is.
    "physdel": {"name": "PHYSICAL DELIVERY", "module": "core.pipelines.physical_delivery",
                "func": "run_physical_delivery", "logdir": "physdel", "runs": 20,
                "state_dir_env": "PHYSICAL_DELIVERY_FOLDER", "state_default": r"D:\CONSOLE\data\PHYSICAL DELIVERY",
                "state_file": "run_counter.txt",
                "email_keys": {"Chandigarh zone": ["PD_ZONE_CHND_TO", "PD_ZONE_CHND_CC"],
                               "Ludhiana zone": ["PD_ZONE_LUDH_TO", "PD_ZONE_LUDH_CC"],
                               "Mumbai zone": ["PD_ZONE_MUMB_TO", "PD_ZONE_MUMB_CC"],
                               "Delhi zone": ["PD_ZONE_DELH_TO", "PD_ZONE_DELH_CC"],
                               "Run log": ["PD_LOG_TO", "PD_LOG_CC"],
                               "Full file (the button under Run)": ["PD_FILE_TO", "PD_FILE_CC"]},
                "cred": {"SMTP sender (alerts account)": {"user_key": "PROBABLE_ALERTS_SMTP_EMAIL",
                          "pass_key": "PROBABLE_ALERTS_SMTP_PASSWORD",
                          "note": "the account every PHYSICAL DELIVERY mail is sent from (same as PROBABLE's alerts sender)"},
                         "WhatsApp (Tata) bearer token": {"single_key": "WA_AUTH_TOKEN",
                          "note": "client messages and the ALERT 1 CR pictures - same Tata account as the other processes"},
                         "Mastertrust webpayout login (mobile lookup)": {"user_key": "MT_WEBPAYOUT_USER",
                          "pass_key": "MT_WEBPAYOUT_PASSWORD",
                          "note": "finds each client's mobile number before the WhatsApp message"},
                         "Excel zone-file password": {"single_key": "PROBABLE_ZONE_XLSX_PASSWORD",
                          "note": "password put on the zone Excel files before they are e-mailed"}},
                "upload_dir_env": "PHYSICAL_DELIVERY_INPUT", "upload_default": r"D:\CONSOLE\data\PHYSICAL DELIVERY\INPUT",
                "common_dir_env": "SA_COMMON_DIR", "common_default": r"C:\COMMON FILES",
                "common_globs": ["RISK_REPORT_*.csv"], "common_label": "COMMON FILES",
                "backup_root_env": "PHYSICAL_DELIVERY_FOLDER", "backup_subdir": "BACKUPFILES AUTOMATED",
                "file_types": [
                    ["PositionBook", "PositionBook*.csv,PositionBook*.xlsx", "PositionBook_<date>.csv"],
                ],
                "backup_types": [
                    ["PositionBook", "PositionBook*.csv,PositionBook*.xlsx"],
                    ["Position report with prices", "PositionBook_With_LTP.xlsx"],
                    ["CTCL vs NMASS output used", "FULL_FILE_CTCL_VS_NMASS*.xlsx"],
                    ["Risk report", "RISK_REPORT_*.csv"],
                    ["Zone / branch / region files", "*.csv,*.xlsx"],
                ],
                "master_dir_env": "PHYSICAL_DELIVERY_FOLDER", "master_subdir": "MAIN_FOLDER",
                "master_globs": ["copy of t1 mail list.xlsx", "branch_and_region_list.xlsx",
                                 "brm list_file.xlsx", "brm list_file.csv"],
                "master_label": "MASTER FILES",
                },
}
# "Create full file & e-mail it" button of the PHYSICAL DELIVERY window: its own run slot (so Stop / status work on it)
# but the same folders, e-mail sender and logs as the main Run.  runs=1 + a counter file nobody writes => no once-a-day
# block and no 5-minute cool-down (the file is often made several times with different CASH %).
PIPELINES["physdelfile"] = {"name": "PHYSICAL DELIVERY - full file", "module": "core.pipelines.physical_delivery",
                            "func": "run_full_file", "logdir": "physdel", "runs": 1,
                            "state_dir_env": "PHYSICAL_DELIVERY_FOLDER", "state_default": PIPELINES["physdel"]["state_default"],
                            "state_file": "full_file_counter.txt", "email_keys": {}, "cred": {}}
# LIVE ALLOCATION CAPTURE - runs from its own timer (core/live_allocation.py), not from this file's Run machinery
# ("runs": 0 keeps it out of the run-counter tiles).  It is registered so the shared Credentials and Logs windows work
# for it exactly like for the other cards.  Run logs: logs/liveallocation/liveallocation_f<folder>__<date>_<time>.log
PIPELINES["liveallocation"] = {
    "name": "LIVE ALLOCATION CAPTURE", "module": "core.live_allocation", "func": "run_slot",
    "logdir": "liveallocation", "runs": 0, "state_dir_env": "LIVE_ALLOC_DIR",
    "state_default": r"D:\CONSOLE\data\LIVE ALLOCATION CAPTURE", "state_file": "run_counter.txt", "email_keys": {},
    "cred": {"NSCCL / NMASS login": {"user_key": "CTCL_NMASS_USER", "pass_key": "CTCL_NMASS_PASSWORD",
                                    "note": "connect2nsccl.com login - shared with CTCL vs NMASS and the 15-minute download"},
             "NMASS member code": {"single_key": "CTCL_NMASS_CONSCODE",
                                   "note": "consCode sent with every NSCCL request - shared"},
             "NCDEX / NCFE login": {"user_key": "CTCL_NCDEX_USER", "pass_key": "CTCL_NCDEX_PASSWORD",
                                    "note": "ncfe.ncdex.com login - shared; this capture logs out after the download"},
             "MCX RSK383 login": {"user_key": "SA_MCX_DEPOSIT_USER", "pass_key": "SA_MCX_DEPOSIT_PASSWORD",
                                  "note": "eclear.mcxccl.com web allocation login - shared with Short Allocation. "
                                          "The RSA code comes from the seed (Scheduler > Connections > MCX eclear)."}},
}
# MCX CASH MAINTENANCE - own timer (core/mcx_cash_maintenance.py), like LIVE ALLOCATION CAPTURE. Run logs:
# logs/mcxcash/mcxcash_t<HHMM>__<date>_<time>.log
PIPELINES["mcxcash"] = {
    "name": "MCX CASH MAINTENANCE", "module": "core.mcx_cash_maintenance", "func": "run_slot",
    "logdir": "mcxcash", "runs": 0, "state_dir_env": "MCX_CASH_DIR",
    "state_default": r"D:\CONSOLE\data\MCX CASH MAINTENANCE", "state_file": "run_counter.txt",
    "email_default": "",
    "email_keys": {"Full file report": ["MCXCASH_FULL_TO", "MCXCASH_FULL_CC"],
                   "Summary report": ["MCXCASH_SUMMARY_TO", "MCXCASH_SUMMARY_CC"]},
    "cred": {"NSCCL / NMASS login": {"user_key": "CTCL_NMASS_USER", "pass_key": "CTCL_NMASS_PASSWORD",
                                    "note": "connect2nsccl.com login for the CM / FO collaterals - shared with CTCL vs NMASS and the 15-minute download"},
             "NMASS member code": {"single_key": "CTCL_NMASS_CONSCODE",
                                   "note": "consCode sent with every NSCCL request - shared"},
             "MCX eclear login (RSK383 + RSK335)": {"user_key": "SA_MCX_DEPOSIT_USER", "pass_key": "SA_MCX_DEPOSIT_PASSWORD",
                                                    "note": "eclear.mcxccl.com - MCX 90% and MCX deposit, one login. Shared with Short Allocation and "
                                                            "Live Allocation. The RSA code comes from the seed (Scheduler > Connections > MCX eclear)."},
             "Report file password": {"single_key": "MCX_CASH_FILE_PASSWORD",
                                      "note": "opens the two Excel reports that are e-mailed (the recipients already know it)"}},
}
# 2CAPTCHA low-balance alert (core/captcha_balance.py): only here so the shared e-mail / settings window works for it
PIPELINES["captchabal"] = {
    "name": "2CAPTCHA BALANCE ALERT", "module": "core.captcha_balance", "func": "check", "logdir": "captchabal", "runs": 0,
    "state_dir_env": "CAPTCHA_BALANCE_DIR", "state_default": r"D:\CONSOLE\data", "state_file": "run_counter.txt",
    "email_default": "",
    "email_keys": {"Low balance alert (sent when the balance is at or below the limit)": ["CAPTCHA_ALERT_TO", "CAPTCHA_ALERT_CC"]},
    "cred": {"Alert limit in US dollars": {"single_key": "CAPTCHA_LOW_BALANCE",
                                           "note": "A mail goes out when the 2Captcha balance is at or below this amount (default 0.80). Checked every hour."}},
}
# 76 FILES DOWNLOADER - its own card and child-process runner (core/files70.py); registered for the shared Credentials
# and Logs windows. Run logs: logs/files70/files70__<dd-mm-yyyy>__<time>.log
PIPELINES["files70"] = {
    "name": "76 FILES DOWNLOADER", "module": "core.files70", "func": "run", "logdir": "files70", "runs": 0,
    "state_dir_env": "FILES76_DIR", "state_default": r"D:\CONSOLE\data\76 FILES DOWNLOADER", "state_file": "run_counter.txt",
    "email_keys": {},
    "cred": {"NCDEX / NCFE login": {"user_key": "CTCL_NCDEX_USER", "pass_key": "CTCL_NCDEX_PASSWORD",
                                    "note": "ncfe.ncdex.com - the 01292_MGN end file. Shared with CTCL vs NMASS and Live "
                                            "Allocation; this card logs out after the download"},
             "NCDEX web extranet login": {"user_key": "F70_NCDEX_WEB_USER", "pass_key": "F70_NCDEX_WEB_PASSWORD",
                                          "note": "webextranet.ncdex.com - the NCCL_MARGIN_REP 01-08 files. Leave empty to "
                                                  "use the NCFE login above"},
             "NCDEX security answer": {"single_key": "NCDEX_SEC_ANSWER",
                                       "note": "answer to the NCDEX security question (both sites). Default: a"}},
}
try:
    from core import mcx_cash_report as _mcr
    _mcr.apply_defaults()              # To / CC defaults of the hand-run script (only fills what .env lacks)
except Exception:
    pass
try:                                   # PHYSICAL DELIVERY folder / e-mail / WhatsApp defaults (only fills what .env lacks)
    from core.pipelines import physdel_cfg as _pd_cfg
    _pd_cfg.apply_defaults()
except Exception:
    pass

# _PB used to be ONE shared dict for the whole server, covering ctcl/ms15/cli
# (and even sa/sa_mcx_deposit at one point) — the theory being that any two
# browser-automation pipelines running at once could collide. That was too
# broad, and traced to the exact same class of over-locking already fixed once
# for Short Allocation (see core/short_allocation.py's stream_short_allocation
# docstring note): CTCL vs NMASS's main run and Margin Shortage's main run
# don't touch each other's files at all (NMASS_SAVE_DIR/MARGIN_SHORTAGE_FOLDER
# are pipe-specific), so blocking MS15's Run button while CTCL is mid-run had
# nothing real behind it — same mistake, different pipe pair.
#
# The ONE genuine shared resource — NMASS_NCFE_API_MIX.PY's captcha image +
# session-cookie pickle, written to one fixed path regardless of which pipe
# invoked it — now has its own narrow, short-lived cross-process lock
# (core/dl_lock.py), separate from this. _PB is now keyed PER PIPE so each
# pipe's Run/Stop is independent of the others, same pattern _SA already uses
# for Short Allocation.
_PB = {k: {"proc": None, "cancel": False, "running": False, "started_at": None}
       for k in PIPELINES}

_EMAIL_OK  = re.compile(r"email sent successfully|all emails sent", re.I)
_EMAIL_BAD = re.compile(r"email (failed|error)", re.I)
# Anchored to the EXACT phrases margin_shortage_15min.py's send_whatsapp() prints
# (the only pipeline that sends WhatsApp) — NOT a loose "whatsapp ... sent/success"
# scan. The old loose version could false-match a client's TEST-mode simulated
# line ("[TEST] would send WhatsApp to <client_code> ...") whenever that client's
# own code happened to contain a substring like "SENT" — a real, if rare, data-
# dependent false positive. Anchoring to "submitted for"/"error for" (what the
# code actually prints right after the word WhatsApp, not anywhere later in the
# line) removes that class of accidental match entirely.
_WA_OK     = re.compile(r"whatsapp submitted for|all whatsapp messages processed", re.I)
_WA_BAD    = re.compile(r"whatsapp (api error|error) for", re.I)
# A test-mode simulated line (margin_shortage_15min.py's "[TEST] would send…")
# must NEVER be able to trigger a real success/failure event no matter what
# text happens to follow it (e.g. a client code containing "sent"/"error") —
# checked BEFORE the WA/EMAIL patterns below, short-circuiting to "ignore".
_TEST_TAG  = re.compile(r"\[test\]", re.I)
_FAIL      = re.compile(r"pre-run file check failed|aborting execution|traceback \(most recent", re.I)
_EXC_LINE  = re.compile(r"^(?:[A-Za-z]+\.)*[A-Za-z]*(Error|Exception):\s+.{3,}", re.I)
# A traceback mid-log is NOT automatically a run-level failure — several
# pipelines (margin_shortage_15min.py's send_email is the known case, 2026-08-17
# incident: an SMTP "Server not connected" traceback fired, then the code's own
# `time.sleep(5)` + retry succeeded 6s later, exit code 0, "RUN N COMPLETED
# SUCCESSFULLY" printed) catch a transient error, log the traceback for
# diagnostics, retry, and recover. Previously `saw_fail` was sticky — ANY
# traceback anywhere in the output permanently flagged the whole run
# outcome=failed even though it fully completed. That false "failed" tag is
# what led an operator to manually re-trigger an already-successful run,
# duplicating every email/WhatsApp alert and burning a run-counter slot meant
# for the next scheduled window. Now: a _FAIL match is only fatal if no
# recovery evidence (an explicit "✅ ... sent successfully"/"✅ ... completed"
# line, which every pipeline's retry-success path already prints) appears
# afterward. A FRESH _FAIL after a recovery still correctly fails the run.
#
# The recovery marker alone (_RECOVERED) is deliberately NOT sufficient on its
# own — an unrelated LATER success line elsewhere in a long run (e.g. "✅
# WhatsApp Alert Process Completed" in a totally different section) must not
# be able to paper over a DIFFERENT step that genuinely failed and was never
# retried. Every pipeline's own retry loops explicitly print a "retrying"
# line (🔁 Retrying in Ns..., "Retrying in 5 seconds...", etc. — checked
# across core/pipelines/*.py) right after catching the error and before
# trying again, so recovery only counts when that explicit signal appears
# BETWEEN the failure and the success line — i.e. the pipeline's own code
# said "I am retrying this," not just "something else happened to succeed
# afterward."
_RECOVERED   = re.compile(r"✅.*(sent successfully|completed)", re.I)
_RETRY_SIGNAL = re.compile(r"retrying", re.I)
# Explicit tags printed by ctcl_codes/*.py on a confirmed wrong password
# (confirmed via test_nmass_login.py / test_mcx90_login.py diagnostics), plus
# the NCFE (NCDEX-side) tags for wrong password / locked account, plus
# MCX_DEPOSIT (sa_codes/mcx_deposit_downloader.py, reused by the "cli" pipeline's
# MCX Web Allocation step — same RSK383 report as SA's MCX Deposit button).
_AUTH_BAD  = re.compile(r"\[(NMASS|MCX90|NCFE|MCX_DEPOSIT)_(AUTH_FAIL|LOCKED)\]", re.I)
# A pipeline prints this when an expected output file turned out to be missing
# but the run continued anyway (rather than aborting) — e.g. cli_upload.py's FTP
# step skips an absent file instead of failing the whole upload. The dashboard
# turns this into a popup naming exactly which file wasn't there.
_MISSING_FILE = re.compile(r"\[MISSING_FILE\]\s*(.+)")
# A plain-English message a worker function wants shown in the dashboard strip
# (e.g. the Groq AI fallback translation of an unrecognized error) — printed
# instead of a raw exit code. See core/error_translator.py.
_FRIENDLY_MSG = re.compile(r"\[FRIENDLY_MSG\]\s*(.+)")

def _status(line):
    s = line.strip()
    if not s: return None
    if _TEST_TAG.search(s): return None
    if _EMAIL_OK.search(s):  return {"type": "email",    "ok": True,  "msg": s[:200]}
    if _EMAIL_BAD.search(s): return {"type": "email",    "ok": False, "msg": s[:200]}
    if _WA_OK.search(s):     return {"type": "whatsapp", "ok": True,  "msg": s[:200]}
    if _WA_BAD.search(s):    return {"type": "whatsapp", "ok": False, "msg": s[:200]}
    m = _AUTH_BAD.search(s)
    if m: return {"type": "login_failed", "server": m.group(1).upper(),
                  "reason": m.group(2).upper(), "msg": s[:200]}
    m2 = _MISSING_FILE.search(s)
    if m2: return {"type": "missing_file", "name": m2.group(1).strip()[:200]}
    m3 = _FRIENDLY_MSG.search(s)
    if m3: return {"type": "note", "msg": m3.group(1).strip()[:300]}
    return None

# ═══════════════════════════════════════════════════════════════════════════
# PER-ACTION SLOTS — so a box's independent downloads can run side by side.
#
# Until now every box had exactly ONE slot (_PB[pipe]), shared by its full run
# AND each of its standalone download buttons. That single `proc` handle was the
# real constraint: start two things and the second overwrites the first's handle,
# after which Stop can only ever kill the second, leaving the first running with
# no way to reach it. So the UI blocked everything whenever anything was going —
# even combinations with no actual conflict, like CLI's "CM/FO/CO/NCDEX Download"
# (NSE/NCDEX portal) and "MCX Web Allocation" (TCS BANCS), which are entirely
# separate systems.
#
# Each named action now gets its own slot, so they track their own subprocess and
# can be stopped independently. What this does NOT change:
#   • Two people starting the SAME action still collide — that slot is single-use.
#   • The shared NMASS login is still guarded by core/dl_lock.py. Two NMASS
#     downloads can't interleave just because their slots are separate; that
#     lock is about the exchange session, not our bookkeeping.
#   • The MCX/TCS BANCS session lock is likewise untouched.
# Only our own artificial "one process per box" limit goes away.
# ═══════════════════════════════════════════════════════════════════════════
_ACT = {}          # "pipe:action" -> slot dict (same shape as a _PB entry)
_ACT_LOCKS = {}

# Human labels for the lock strips / audit trail.
_ACT_LABELS = {
    "nmass": "NMASS/NCDEX download",
    "mcx90": "MCX 90% download",
    "tcs":   "TCS BANCS download (DEPOSIT+90%)",
    "dl_nse":    "NSE member-portal download",
    "dl_ftp":    "FTP download",
    "dl_search": "Search Result (BANCS) download",
}


def act_slot(pipe, action):
    """Get (slot, lock) for one named action of one pipe, creating it on first use."""
    key = f"{pipe}:{action}"
    if key not in _ACT:
        _ACT[key] = {"running": False, "proc": None, "cancel": False,
                     "started_at": None, "last_event_at": None,
                     "pipe": pipe, "action": action}
        _ACT_LOCKS[key] = threading.Lock()
    return _ACT[key], _ACT_LOCKS[key]


def act_label(pipe, action):
    base = _ACT_LABELS.get(action, action)
    return f"{PIPELINES.get(pipe, {}).get('name', pipe)} — {base}"


def clear_if_stale_act(pipe, action):
    """Same activity-based staleness test as _clear_if_stale, for an action slot."""
    slot, _ = act_slot(pipe, action)
    if not slot.get("running"):
        return
    p = slot.get("proc")
    if p is not None and p.poll() is None:
        return
    ref = slot.get("last_event_at") or slot.get("started_at")
    if not ref:
        return
    try:
        age = (datetime.datetime.now() - datetime.datetime.fromisoformat(ref)).total_seconds()
    except Exception:
        return
    if age > _IDLE_STALE_SECS:
        slot["running"] = False
        slot["proc"] = None
        slot["started_at"] = None
        slot["last_event_at"] = None


def stop_action(pipe, action):
    """Stop ONE action of one pipe, leaving that pipe's run and its other
    actions alone — the whole point of splitting the slots."""
    slot, _ = act_slot(pipe, action)
    slot["cancel"] = True
    p = slot.get("proc")
    if p and p.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                p.terminate()
        except Exception:
            pass
    slot["running"] = False
    slot["proc"] = None
    slot["started_at"] = None
    return {"ok": True}


def running_actions():
    """[(pipe, action, started_at)] for every action currently live — used by
    lock_manager to describe them, and by the UI to label the right button."""
    out = []
    for key in list(_ACT.keys()):
        pipe, _, action = key.partition(":")
        clear_if_stale_act(pipe, action)
        slot = _ACT[key]
        if slot.get("running"):
            out.append((pipe, action, slot.get("started_at")))
    return out


def stop_pipeline(pipe):
    """pipe: 'ctcl' | 'ms15' | 'cli' | 'probable' (or any future pipe) — stops
    ONLY that pipe's own run/download, never anything another pipe has going
    (each has its own independent slot).

    ALWAYS force-clears this pipe's "running"/"proc" slot before returning —
    previously this only killed a tracked subprocess and set cancel=True,
    relying on the worker thread's own `finally` block (in stream_pipeline()/
    _stream_code() below) to flip "running" back to False once the killed
    process's stdout loop actually breaks. If that worker thread never gets
    there — e.g. it crashed/hung before reaching that point, or was itself
    orphaned by a server restart/reload mid-run — "running" stayed True
    forever with no live worker left to ever clear it, wedging every button
    for that pipe ("X is running on another screen") with nothing actually
    running. Stop is supposed to mean "make it stop, no matter what" — so it
    now unconditionally releases the slot itself, in addition to still
    attempting the taskkill for a genuinely tracked process."""
    pb = _PB.get(pipe)
    if pb is None:
        return {"ok": False, "error": f"unknown pipe '{pipe}'"}
    pb["cancel"] = True
    p = pb.get("proc")
    if p and p.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                p.terminate()
        except Exception:
            pass
    pb["running"] = False
    pb["proc"] = None
    pb["started_at"] = None
    # Stop means stop — including this pipe's standalone downloads, which now
    # live in their own slots and would otherwise survive a Stop that the
    # operator reasonably expects to halt everything in that box.
    for _k in list(_ACT.keys()):
        _p, _, _a = _k.partition(":")
        if _p == pipe and _ACT[_k].get("running"):
            try:
                stop_action(_p, _a)
            except Exception:
                pass
    try:
        from core import lock_manager as _lm
        _lm.on_end(f"px_{pipe}", "stopped")
    except Exception:
        pass
    # If this pipe's subprocess was force-killed while it held the shared
    # MCX-deposit/TCS BANCS lock (PROBABLE's TCS BANCS button reuses the exact
    # same lock file as SA's "MCX Deposit" and CLI's "MCX Web Allocation" — see
    # sa_codes/mcx_deposit_downloader.py), a taskkill'd process never reaches
    # its own `finally: _release_mcx_lock()`. Previously only /cli/stop and
    # /sa/mcx-deposit-stop did this explicit release — PROBABLE didn't, so a
    # Stop click (or a server restart) mid-TCS-BANCS-download left the lock
    # file behind with owner="probable" forever (until the 10-minute staleness
    # window quietly expired), and every next click failed immediately with
    # "already in progress (started by 'probable')" — stuck blocking itself.
    # Generalized here (any pipe, not just cli) so this can't recur for
    # whichever pipe reuses this lock next. Only releases if the lock is
    # actually still recorded as owned by THIS pipe — never clears a lock
    # another box legitimately still holds.
    try:
        sa_dir = os.path.join(REPO, "sa_codes")
        if sa_dir not in sys.path:
            sys.path.insert(0, sa_dir)
        import importlib
        _mdd = importlib.import_module("mcx_deposit_downloader")
        _mdd.force_release_mcx_lock_if_owner(pipe)
    except Exception:
        pass
    return {"ok": True}


# A run realistically finishes in minutes, never hours — CTCL/MS15/CLI/Probable
# all complete well under this. If a pipe's slot has been marked "running" for
# longer than this AND there's no live tracked subprocess behind it, the lock
# is almost certainly stale (worker thread died/hung without reaching its own
# `finally` reset) rather than a genuinely 40-minute-long run. Auto-clearing it
# here means a stuck lock heals itself on the very next status check / run
# attempt, instead of requiring someone to notice, diagnose, and restart the
# whole server to get unstuck — exactly the situation this fixes.
_STALE_RUN_SECS = 1800  # 30 minutes — absolute fallback, measured from run start

# ── Activity-based staleness (the real fix) ────────────────────────────────
# _STALE_RUN_SECS alone was measured from when the run STARTED, so a lock that
# leaked one second after a successful run still sat there claiming "running"
# for a full 30 minutes — which is exactly what was seen: a run that finished
# fine at 11:32 was still showing "running on another screen · 32m" at 12:04,
# blocking every button on the box for everyone.
#
# A run that is genuinely alive produces output constantly. So instead of asking
# "did this start a long time ago?" (a real long run looks identical to a leaked
# lock) this asks "has anything happened recently?" — no live subprocess AND no
# event for this long means the worker is gone, whatever the reason it failed to
# clear its own flag. Heals in ~3 minutes instead of 30, and can't misfire on a
# genuinely long run because such a run is still emitting lines.
_IDLE_STALE_SECS = 180  # 3 minutes with no output and no live process


def _touch(pb):
    """Mark this pipe as still alive. Called on every event a run emits."""
    try:
        pb["last_event_at"] = datetime.datetime.now().isoformat()
    except Exception:
        pass

def _clear_if_stale(pipe):
    pb = _PB.get(pipe)
    if not pb or not pb.get("running"):
        return
    p = pb.get("proc")
    if p is not None and p.poll() is None:
        return  # a real, live subprocess is still tracked — not stale

    def _age(iso):
        try:
            return (datetime.datetime.now() - datetime.datetime.fromisoformat(iso)).total_seconds()
        except Exception:
            return None

    # Primary test: nothing has been emitted for a while and no process is alive.
    _reason = None
    last = pb.get("last_event_at")
    if last:
        a = _age(last)
        if a is not None and a > _IDLE_STALE_SECS:
            _reason = f"idle_{int(a)}s"
    started = pb.get("started_at")
    if _reason is None:
        # No activity ever recorded (e.g. a lock claimed by a generator whose
        # worker died before emitting anything) — fall back to age since start.
        if not started:
            return
        a = _age(started)
        if a is None:
            return
        # With no event ever seen, the idle window applies from the start time —
        # otherwise this class of leak would still wait the full 30 minutes.
        limit = _IDLE_STALE_SECS if last is None else _STALE_RUN_SECS
        if a <= limit:
            return
        _reason = f"no_activity_{int(a)}s"

    pb["running"] = False
    pb["proc"] = None
    pb["started_at"] = None
    pb["last_event_at"] = None
    try:
        from core import lock_manager as _lm
        _lm.on_end(f"px_{pipe}", f"stale_auto_cleared ({_reason})")
    except Exception:
        pass

# One lock PER PIPE — a second person clicking Run for the SAME pipe while it's
# still going must never launch a second subprocess that clobbers the first
# one's "proc" handle (Stop could then only ever kill whichever started last,
# leaking the first to run unmanaged, and the two runs' captcha/token popups
# could interleave confusingly). That risk is real only WITHIN a single pipe —
# a second CTCL click while CTCL is running — not across pipes (ctcl/ms15/cli
# don't share files or in-process state with each other; see the note on _PB
# above). Each lock makes its own pipe's check-and-claim atomic against a
# near-simultaneous second request for THAT SAME pipe only.
_PB_LOCKS = {k: threading.Lock() for k in PIPELINES}


def _safe_test_armed_for(key):
    """True when THIS pipe has a safe test armed for its next run. Only
    PROBABLE has a safe-test facility today; anything else is always False.
    Never raises — a broken check here must not be able to block a run."""
    if key != "probable":
        return False
    try:
        from core.pipelines.probable_alerts import is_safe_test_armed
        return bool(is_safe_test_armed())
    except Exception:
        return False


def stream_pipeline(key, run_no=None, extra_env=None, by=None, ip=None, force=False):
    p = PIPELINES.get(key)
    pb = _PB.get(key)
    if pb is None:
        yield {"type": "error", "msg": f"Unknown pipeline '{key}'"}
        return
    lock_key = f"px_{key}"

    # ── ONCE-A-DAY GUARD ──────────────────────────────────────────────────
    # A pipeline declared "runs": 1 that has already completed a LIVE run
    # today must not be startable again. PROBABLE is the case this exists
    # for: a second run re-sends every branch, region and zone email and
    # re-fires the client WhatsApp blast, and the day's input files have
    # already been moved into the backup folder by then, so the re-run
    # works from nothing.
    #
    # An armed safe test is deliberately exempt — a rehearsal must stay
    # possible after the real run, and it never writes the counter itself.
    if p and p.get("runs") == 1 and not _safe_test_armed_for(key):
        if _read_counter(p) >= 1:
            yield {"type": "error",
                   "msg": f"⛔ {p.get('name', key)} has already completed its run for "
                          f"today. It is a once-a-day process — running it again would "
                          f"re-send every email and WhatsApp message. If you genuinely "
                          f"need to re-run it, clear run_counter.txt in the process "
                          f"folder first."}
            return

    # ── DUPLICATE-RUN COOLDOWN (2026-08-17 incident) ────────────────────────
    # Multi-run-per-day pipes (ctcl/ms15/cli — anything with "runs" > 1) had no
    # protection against firing the SAME slot's worth of alerts twice in quick
    # succession. What actually happened: MS15's 11:55 run completed fully
    # (all emails/WhatsApp sent, files archived) but got tagged outcome=failed
    # by a since-fixed bug (a transient SMTP retry's traceback was mistaken for
    # a fatal error). The operator, trusting that false "failed" tag, manually
    # re-ran it 10 minutes later — which resent all 7 WhatsApp margin images,
    # all zone/main emails, and every client alert a second time, and burned a
    # run-counter slot meant for the next real scheduled window.
    #
    # The outcome-flag bug is fixed at the source now, but this is cheap,
    # independent insurance for the NEXT time a run looks wrong for some other
    # reason: block a same-pipe run that starts within COOLDOWN_MIN of the
    # previous run's clean completion, with a clear explanation instead of
    # silently duplicating every client-facing message. `force=True` (not
    # currently wired to any UI button — call /px/run-stream?...&force=1
    # directly if a fast genuine re-run is ever needed) bypasses it.
    COOLDOWN_MIN = 5
    if p and p.get("runs", 1) > 1 and not force:
        _last = pb.get("last_completed_at")
        if _last:
            _mins = (datetime.datetime.now() - _last).total_seconds() / 60.0
            if _mins < COOLDOWN_MIN:
                yield {"type": "error",
                       "msg": f"⛔ {p.get('name', key)} already completed a run "
                              f"{_mins:.1f} min ago. Running again now would resend every "
                              f"email/WhatsApp alert from that run a second time. If this "
                              f"really is a genuine next run (not a retry of the one that "
                              f"just finished), wait {COOLDOWN_MIN - _mins:.1f} more min, or "
                              f"check 🗂️ Logs first to confirm the last run actually needs "
                              f"redoing."}
                return

    _clear_if_stale(key)
    with _PB_LOCKS[key]:
        if pb.get("running"):
            busy_label = p.get("name", key) if p else key
        else:
            busy_label = None
            pb["running"] = True  # claim the slot NOW, before this generator's
                                   # caller even gets control back — closes the
                                   # race window a second near-simultaneous
                                   # request for THIS pipe could otherwise slip through.
            pb["started_at"] = datetime.datetime.now().isoformat()
    # lock-manager hooks (metadata + audit + live broadcast) — never fatal
    try:
        from core import lock_manager as _lm
    except Exception:
        _lm = None
    if busy_label:
        _who = ""
        if _lm:
            try:
                _who = _lm.lock_line(lock_key); _lm.on_reject(lock_key, busy_label)
            except Exception:
                pass
        yield {"type": "error",
               "msg": f"⚠ {busy_label} is already running right now — wait for it "
                      f"to finish, or Stop it, before starting a new run.{_who}"}
        return
    if _lm:
        try:
            _lm.on_start(lock_key, PIPELINES.get(key, {}).get("name", key), pipe=key, by=by, ip=ip)
        except Exception:
            pass

    q = queue.Queue()

    # Compile phase-transition patterns for this pipeline (e.g. CTCL top20/payout markers)
    phase_pats = [(re.compile(pat, re.I), dict(ev))
                  for pat, ev in p.get("phase_patterns", [])] if p else []

    def worker():
        pb["cancel"] = False; pb["running"] = True
        _touch(pb)
        _outcome = "error"
        try:
            if not p:
                q.put({"type": "error", "msg": f"Unknown pipeline '{key}'"}); return
            logdir = os.path.join(LOG_BASE, p["logdir"]); os.makedirs(logdir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            logp = os.path.join(logdir, f"{key}__{ts}.log")
            # Stamp WHO started this into the log file itself. The lock metadata
            # and audit trail already know, but those are live/rolling — the log
            # is the thing anyone opens weeks later asking "who ran this one?"
            _started_dt = datetime.datetime.now()
            _by, _ip = _log_identity(by, ip, lock_key)
            q.put({"type": "log", "msg": f"Starting {p['name']}…"})
            # For CTCL, optionally pass run_number_override so retries use the correct run slot
            if run_no and key == "ctcl":
                code = f"from {p['module']} import {p['func']} as _f; _f(run_number_override={int(run_no)})"
            else:
                code = f"from {p['module']} import {p['func']} as _f; _f()"
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                   "PYTHONUNBUFFERED": "1", "PYTHONPATH": REPO,
                   **({"PD_RUN_BY": str(_by)} if key in ("physdel", "physdelfile") else {}), **(extra_env or {})}
            saw_fail = False; last_exc = ""; recovered_after_fail = False; retry_signaled = False
            with open(logp, "w", encoding="utf-8", errors="replace") as lf:
                lf.write(_log_header(p["name"], _started_dt, _by, _ip))
                # CREATE_NEW_PROCESS_GROUP (Windows only) — isolate this subprocess
                # from the FastAPI server's own console/process group, so a
                # `uvicorn --reload` restart (triggered by any code edit while a
                # pipeline is mid-run) can't cascade a Ctrl-Break-style interrupt
                # into a live CTCL/CLI/SA run. Stop is unaffected — it still kills
                # by PID via `taskkill /F /T`. See the identical fix + longer note
                # in core/short_allocation.py's _run_one().
                _popen_kwargs = {}
                if os.name == "nt":
                    _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                proc = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=REPO, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    **_popen_kwargs)
                pb["proc"] = proc
                for line in proc.stdout:
                    _ts = datetime.datetime.now().strftime("%H:%M:%S")
                    lf.write(f"[{_ts}] {line}" if not line.startswith("[") else line)
                    lf.flush()  # write to disk immediately — if Stop kills this mid-run,
                                # whatever was already printed is preserved instead of sitting
                                # lost in a buffer that never gets flushed
                    ls = line.strip()
                    _touch(pb)   # proof of life for _clear_if_stale
                    if _FAIL.search(ls): saw_fail = True; recovered_after_fail = False; retry_signaled = False
                    elif saw_fail and _RETRY_SIGNAL.search(ls): retry_signaled = True
                    elif saw_fail and retry_signaled and _RECOVERED.search(ls): recovered_after_fail = True
                    if saw_fail and _EXC_LINE.match(ls): last_exc = ls[:250]
                    ev = _status(line)
                    if ev: q.put(ev)
                    # emit phase-transition events (e.g. top20/payout for CTCL)
                    for pat, phase_ev in phase_pats:
                        if pat.search(line):
                            q.put(phase_ev); break
                rc = proc.wait(); pb["proc"] = None
            if pb["cancel"]:
                _outcome = "stopped"
                q.put({"type": "stopped", "msg": "⛔ Stopped by user.", "log": os.path.basename(logp)})
            elif rc != 0 or (saw_fail and not recovered_after_fail):
                _outcome = "failed"
                q.put({"type": "done", "ok": False, "log": os.path.basename(logp), "err": last_exc})
            else:
                _outcome = "completed"
                q.put({"type": "done", "ok": True, "log": os.path.basename(logp), "err": ""})
        except Exception as e:
            q.put({"type": "error", "msg": str(e)})
        finally:
            # Close the log with the outcome. Without this a killed run and a
            # clean one look identical — both just stop mid-file.
            try:
                with open(logp, "a", encoding="utf-8", errors="replace") as _lf2:
                    _lf2.write(_log_footer(_started_dt, _outcome))
            except Exception:
                pass
            pb["running"] = False; pb["proc"] = None
            # Feeds the duplicate-run cooldown above — only stamped on a genuine
            # clean completion, never on failed/stopped/error, so a bad run never
            # blocks the operator from immediately retrying it.
            if _outcome == "completed":
                pb["last_completed_at"] = datetime.datetime.now()
            try:
                from core import lock_manager as _lm2
                _lm2.on_end(lock_key, _outcome)
            except Exception:
                pass
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        ev = q.get()
        if ev is None: break
        yield ev

def _stream_code(code, logdir_key, logname_prefix):
    """Generic SSE streaming: runs `code` as a subprocess, yields event dicts.
    Used by retry helpers (stream_ctcl_top20, stream_ctcl_payout, ms15's Test
    run) so each sub-phase can be re-run without restarting the full pipeline.
    `logdir_key` is always one of the PIPELINES keys ('ctcl'/'ms15'/'cli') — uses
    that SAME pipe's own independent slot/lock as stream_pipeline(), so e.g. a
    CTCL retry (MarginTop20/Payout) correctly blocks a second CTCL retry, but
    never blocks MS15 or CLI, which have nothing to do with it."""
    pb = _PB.get(logdir_key)
    if pb is None:
        yield {"type": "error", "msg": f"Unknown pipeline '{logdir_key}'"}
        return
    lock_key = f"px_{logdir_key}"
    lock = _PB_LOCKS.setdefault(logdir_key, threading.Lock())
    _clear_if_stale(logdir_key)
    with lock:
        if pb.get("running"):
            busy_label = PIPELINES.get(logdir_key, {}).get("name", logdir_key)
        else:
            busy_label = None
            pb["running"] = True
            pb["started_at"] = datetime.datetime.now().isoformat()
    try:
        from core import lock_manager as _lm
    except Exception:
        _lm = None
    if busy_label:
        _who = ""
        if _lm:
            try:
                _who = _lm.lock_line(lock_key); _lm.on_reject(lock_key, busy_label)
            except Exception:
                pass
        yield {"type": "error",
               "msg": f"⚠ {busy_label} is already running right now — wait for it "
                      f"to finish, or Stop it, before starting a new run.{_who}"}
        return
    if _lm:
        try:
            _lm.on_start(lock_key, logname_prefix.replace("_", " "), pipe=logdir_key)
        except Exception:
            pass

    logdir = os.path.join(LOG_BASE, logdir_key)
    os.makedirs(logdir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logp = os.path.join(logdir, f"{logname_prefix}__{ts}.log")
    q = queue.Queue()
    def worker():
        pb["cancel"] = False; pb["running"] = True
        _touch(pb)
        _outcome = "error"
        try:
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                   "PYTHONUNBUFFERED": "1", "PYTHONPATH": REPO}
            saw_fail = False; recovered_after_fail = False; retry_signaled = False
            _started_dt = datetime.datetime.now()
            _by, _ip = _log_identity(lock_key=lock_key)
            with open(logp, "w", encoding="utf-8", errors="replace") as lf:
                lf.write(_log_header(logname_prefix.replace("_", " "), _started_dt, _by, _ip))
                proc = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=REPO, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1)
                pb["proc"] = proc
                for line in proc.stdout:
                    _ts2 = datetime.datetime.now().strftime("%H:%M:%S")
                    lf.write(f"[{_ts2}] {line}" if not line.startswith("[") else line)
                    lf.flush()  # write to disk immediately — if Stop kills this mid-run,
                                # whatever was already printed is preserved instead of sitting
                                # lost in a buffer that never gets flushed
                    _touch(pb)   # proof of life for _clear_if_stale
                    if _FAIL.search(line): saw_fail = True; recovered_after_fail = False; retry_signaled = False
                    elif saw_fail and _RETRY_SIGNAL.search(line): retry_signaled = True
                    elif saw_fail and retry_signaled and _RECOVERED.search(line): recovered_after_fail = True
                    ev = _status(line)
                    if ev: q.put(ev)
                rc = proc.wait(); pb["proc"] = None
            if pb["cancel"]:
                _outcome = "stopped"
                q.put({"type": "stopped", "msg": "⛔ Stopped by user.", "log": os.path.basename(logp)})
            elif rc != 0 or (saw_fail and not recovered_after_fail):
                _outcome = "failed"
                q.put({"type": "done", "ok": False, "log": os.path.basename(logp)})
            else:
                _outcome = "completed"
                q.put({"type": "done", "ok": True, "log": os.path.basename(logp)})
        except Exception as e:
            q.put({"type": "error", "msg": str(e)})
        finally:
            try:
                with open(logp, "a", encoding="utf-8", errors="replace") as _lf2:
                    _lf2.write(_log_footer(_started_dt, _outcome))
            except Exception:
                pass
            pb["running"] = False; pb["proc"] = None
            try:
                from core import lock_manager as _lm2
                _lm2.on_end(lock_key, _outcome)
            except Exception:
                pass
            q.put(None)
    threading.Thread(target=worker, daemon=True).start()
    while True:
        ev = q.get()
        if ev is None: break
        yield ev

def _stream_code_act(code, pipe, action, logname_prefix):
    """Same as _stream_code(), but claims that pipe's OWN named ACTION slot
    (_ACT["pipe:action"]) instead of its main run slot (_PB[pipe]).

    Why this exists: PROBABLE's "TCS (DEPOSIT+90%)" button was running through
    _stream_code(), which takes the pipe's MAIN slot — the exact same slot a
    full Run takes. Consequences, all of which were reported as bugs:
      • lock_manager reported it as a whole-box "process" lock, so
        process-locks.js greyed out EVERY button in the PROBABLE box (Email
        To/CC, Credentials, Upload files, Backup files, Manual overrides, and
        the completely unrelated NMASS/NCDEX button) — even though TCS BANCS
        is a different script hitting a different server.
      • the banner read "PROBABLE is already running — started by <name>",
        i.e. it announced a whole pipeline run when all that was happening was
        one download.
      • it blocked a genuine Run, and a Run blocked it.
    NMASS/NCDEX already used an action slot (see stream_nmass_download), which
    is why only TCS BANCS showed this. Action slots are auto-reported by
    lock_manager.snapshot() via running_actions() as kind:"action" locks, so
    they grey out ONLY their own button — no on_start/on_end needed here."""
    slot, lock = act_slot(pipe, action)
    clear_if_stale_act(pipe, action)
    with lock:
        if slot.get("running"):
            yield {"type": "error",
                   "msg": f"⚠ {act_label(pipe, action)} is already running right now — "
                          f"wait for it to finish, or Stop it, before starting it again."}
            return
        slot["running"] = True
        slot["cancel"] = False
        slot["started_at"] = datetime.datetime.now().isoformat()
    try:                                   # who clicked it — so only that person gets this run's captcha/token popup
        slot["by"] = _log_identity()[0]
    except Exception:
        slot["by"] = None
    _touch(slot)

    logdir = os.path.join(LOG_BASE, pipe)
    os.makedirs(logdir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logp = os.path.join(logdir, f"{logname_prefix}__{ts}.log")
    q = queue.Queue()

    def worker():
        try:
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                   "PYTHONUNBUFFERED": "1", "PYTHONPATH": REPO}
            saw_fail = False; recovered_after_fail = False; retry_signaled = False
            _started_dt = datetime.datetime.now()
            _by, _ip = _log_identity()
            _outcome = "error"
            with open(logp, "w", encoding="utf-8", errors="replace") as lf:
                lf.write(_log_header(logname_prefix.replace("_", " "), _started_dt, _by, _ip))
                proc = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=REPO, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1)
                slot["proc"] = proc
                for line in proc.stdout:
                    _ts2 = datetime.datetime.now().strftime("%H:%M:%S")
                    lf.write(f"[{_ts2}] {line}" if not line.startswith("[") else line)
                    lf.flush()
                    _touch(slot)
                    if _FAIL.search(line): saw_fail = True; recovered_after_fail = False; retry_signaled = False
                    elif saw_fail and _RETRY_SIGNAL.search(line): retry_signaled = True
                    elif saw_fail and retry_signaled and _RECOVERED.search(line): recovered_after_fail = True
                    ev = _status(line)
                    if ev: q.put(ev)
                rc = proc.wait(); slot["proc"] = None
            if slot.get("cancel"):
                _outcome = "stopped"
                q.put({"type": "stopped", "msg": "⛔ Stopped by user.", "log": os.path.basename(logp)})
            elif rc != 0 or (saw_fail and not recovered_after_fail):
                _outcome = "failed"
                q.put({"type": "done", "ok": False, "log": os.path.basename(logp)})
            else:
                _outcome = "completed"
                q.put({"type": "done", "ok": True, "log": os.path.basename(logp)})
        except Exception as e:
            q.put({"type": "error", "msg": str(e)})
        finally:
            try:
                with open(logp, "a", encoding="utf-8", errors="replace") as _lf2:
                    _lf2.write(_log_footer(_started_dt, _outcome))
            except Exception:
                pass
            slot["running"] = False
            slot["proc"] = None
            slot["started_at"] = None
            # A force-killed subprocess never runs its own
            # `finally: _release_mcx_lock()`, which would leave
            # C:\SHORT ALLOCATION\.mcx_deposit_lock behind and make every
            # later click fail with "already in progress".
            try:
                sa_dir = os.path.join(REPO, "sa_codes")
                if sa_dir not in sys.path:
                    sys.path.insert(0, sa_dir)
                import importlib
                importlib.import_module("mcx_deposit_downloader").force_release_mcx_lock_if_owner(pipe)
            except Exception:
                pass
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        ev = q.get()
        if ev is None: break
        yield ev


def stream_ctcl_top20():
    """Retry: run Margin Top20 only (without re-running full CTCL pipeline)."""
    code = "from core.pipelines.margin_top20 import run_margin_top20; run_margin_top20()"
    return _stream_code(code, "ctcl", "ctcl_top20_retry")

def stream_ctcl_payout():
    """Retry: run Payout download + check only."""
    code = ("from core.pipelines.payout_download import run_payout_download; "
            "from core.pipelines.payout_check import run_payout_check; "
            "run_payout_download(); run_payout_check()")
    return _stream_code(code, "ctcl", "ctcl_payout_retry")


_LOG_TS_RE = re.compile(r"(\d{8})_(\d{6})")

def _log_display_time(full_path, filename):
    """Pull a proper 'DD Mon YYYY, HH:MM:SS' timestamp out of a log filename (they all embed
    one, e.g. ctcl__20260713_100435.log), falling back to the file's mtime if the name doesn't
    match. Used so the Logs modal shows real dates/times instead of a raw filename."""
    m = _LOG_TS_RE.search(filename)
    if m:
        try:
            dt = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            return dt.strftime("%d %b %Y, %H:%M:%S")
        except Exception:
            pass
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(full_path)).strftime("%d %b %Y, %H:%M:%S")
    except Exception:
        return ""

# Maps a log filename's prefix to a human step label, so the Logs modal can
# tell the user which stage (Download / CTCL processing / MarginTop20 /
# Payout / MCX 90% / Upload / Restore...) a given log belongs to instead of
# just a bare date+time. Ordered most-specific-prefix-first, since e.g.
# "ctcl_download" and "ctcl_mcx90" must be matched before the bare "ctcl"
# fallback (which would otherwise match everything starting with "ctcl").
_LOG_LABELS = {
    "ctcl": [
        ("ctcl_download",     "📥 Download"),
        ("ctcl_mcx90",        "📥 MCX 90% Download"),
        ("ctcl_mcx_deposit",  "📥 MCX Deposit Download"),
        ("ctcl_mcx_both",     "📥 MCX files (90% + Deposit)"),
        ("ctcl_top20_retry",  "🔁 MarginTop20 (retry)"),
        ("ctcl_payout_retry", "🔁 Payout (retry)"),
        ("ctcl_payout",       "💰 Payout Check"),
        ("ctcl_top20",        "📊 MarginTop20"),
        ("payout",            "💰 Payout Check"),
        ("top20",             "📊 MarginTop20"),
        ("ctcl",              "⚙️ CTCL vs NMASS"),
    ],
    "cli": [
        ("cli_download",     "📥 Download"),
        ("cli_mcx_download", "📥 MCX Web Allocation"),
        ("cli_upload",       "📤 Upload"),
        ("UPLOAD",           "📤 Upload"),
        ("RESTORE",          "♻️ Restore"),
    ],
    "liveallocation": [("liveallocation_f%d" % _i, "📥 Folder %d" % _i) for _i in range(12, 0, -1)],
    "r1r2": [
        ("r1r2_download",     "📥 Download"),
        ("r1r2_mcx_download", "📥 MCX Deposit"),
        ("r1r2_live",         "🔴 Live Snapshot"),
        ("r1r2_afternoon",    "🌤️ Afternoon"),
        ("r1r2_evening",      "🌙 Evening"),
        ("r1r2_ms15backup",   "🕒 15-min Backup"),
        ("UPLOAD",            "📤 Upload"),
        ("RESTORE",           "♻️ Restore"),
    ],
    "reco": [
        ("reco_all",      "▶ Run all"),
        ("reco_holding",  "HOLDING"),
        ("reco_t1",       "T1 HOLDING"),
        ("reco_nfo",      "NFO BFO MTFS"),
        ("reco_kamb",     "KAMBALA POSITION"),
        ("UPLOAD",        "📤 Upload"),
        ("RESTORE",       "♻️ Restore"),
    ],
    "ms15": [
        ("ms15_download",     "📥 NMASS/NCDEX"),
        ("ms15_mcx90",        "📥 MCX 90%"),
        ("ms15_mcx_deposit",  "📥 MCX Deposit"),
        ("ms15_mcx_both",     "📥 MCX files (90% + Deposit)"),
        ("ms15_alloc_upload", "📤 Allocation Upload"),
        ("UPLOAD",            "📤 Upload"),
        ("RESTORE",           "♻️ Restore"),
    ],
}

def _log_friendly_label(key, filename):
    for prefix, label in _LOG_LABELS.get(key, []):
        if filename.startswith(prefix + "__"):
            return label
    if key == "mcxcash":                         # mcxcash_t0930__20261028_093001.log -> "09:30"
        m = re.match(r"mcxcash_t(\d{2})(\d{2})__", filename)
        if m:
            return "🕘 %s:%s" % (m.group(1), m.group(2))
    return None

def _log_probably_failed(text):
    """Cheap ok/fail guess from a log's own tail — same _FAIL regex the live
    'done' event already uses (see _stream_code's saw_fail), applied after the
    fact to a saved file. Used only by list_logs()/latest_log() below, to give
    the browser's "last run" summary a real pass/fail signal even for a run
    this particular browser never watched live (see the long comment on
    latest_log() for why that matters)."""
    if not text:
        return None
    return bool(_FAIL.search(text[-4000:]))


def latest_log(key):
    """The single most-recently-written log for `key`, with a real mtime and a
    best-effort ok/fail guess — the SERVER's own record of "what actually last
    happened", independent of any one browser.

    Why this exists: dashboard.html's "📋 View" (pxOpenView/_pxRunLog) is fed
    entirely from this browser's own localStorage, written only when THIS TAB
    was open and listening to a run's SSE 'done' event. A pipe that runs many
    times a day (MS15: 9x) but was watched from a different tab/machine for
    most of a day — or whose page got reloaded partway through — leaves that
    cache holding whatever the LAST run THIS BROWSER happened to see finish
    was, even if that was yesterday evening. The View modal then confidently
    shows a stale date with no indication anything is wrong (reported: MS15's
    "run summary" showing 8/3 4:52 PM while looking at today's run-05 tile).
    dashboard.html's pxOpenView() now cross-checks against this endpoint on
    every open and prefers it whenever it's newer than the cached value."""
    p = PIPELINES.get(key)
    if not p:
        return None
    d = os.path.join(LOG_BASE, p["logdir"])
    try:
        names = [f for f in os.listdir(d) if f.endswith(".log")]
        if not names:
            return None
        names.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
        f = names[0]
        full = os.path.join(d, f)
        mtime = os.path.getmtime(full)
        try:
            text = open(full, encoding="utf-8", errors="replace").read()
        except Exception:
            text = ""
        failed = _log_probably_failed(text)
        return {"name": f, "mtime": mtime, "mtime_ms": int(mtime * 1000),
                "display_time": _log_display_time(full, f),
                "label": _log_friendly_label(key, f),
                "ok": (not failed) if failed is not None else None}
    except Exception:
        return None


def list_logs(key, limit=80):
    p = PIPELINES.get(key)
    if not p: return []
    d = os.path.join(LOG_BASE, p["logdir"])
    try:
        # Only show actual terminal-output logs — exclude internal state
        # files like run_logs.json (not a log, just JSON run metadata; it
        # gets rewritten on every run so it would otherwise sort to the top
        # and open as an unhelpful raw JSON blob instead of a real log).
        # Sort by real file mtime (not filename) so the most recently
        # written log is always first, regardless of its name prefix.
        names = [f for f in os.listdir(d) if f.endswith(".log")]
        names.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
        fs = names[:limit]
        out = []
        for f in fs:
            full = os.path.join(d, f)
            out.append({"name": f, "size": os.path.getsize(full),
                        "display_time": _log_display_time(full, f),
                        "label": _log_friendly_label(key, f)})
        return out
    except Exception:
        return []

def read_log(key, name):
    p = PIPELINES.get(key)
    if not p: return ""
    fp = os.path.join(LOG_BASE, p["logdir"], os.path.basename(name))
    try:
        return open(fp, encoding="utf-8", errors="replace").read()[-120000:]
    except Exception as e:
        return f"(could not read log: {e})"


# ── Deterministic failure reason (checked BEFORE ever asking the AI) ─────────
# Every pipeline's pre_run_file_check() (ctcl_vs_nmass.py, margin_shortage_15min.py,
# ...) prints a KNOWN, exact structure on failure: a "Missing required files:" /
# "wrong date" / "stale" section header, followed by one indented bullet line per
# file. That's not a guess — it's the literal, complete reason already sitting in
# the log. Asking an LLM to paraphrase it (previous behaviour) can only make it
# vaguer ("some required files") instead of naming the exact file(s), which is
# exactly what a person needs to fix it. This is checked first; translate_error()
# (Groq AI) is only ever used as a fallback for a genuinely unrecognized failure.
_KNOWN_SECTION_HEADERS = [
    (re.compile(r"missing required files?\s*:", re.I), "missing"),
    (re.compile(r"files?\s+(?:with|not matching)\s+.*date", re.I), "wrong_date"),
    (re.compile(r"files?\s+older than allowed time|stale files", re.I), "stale"),
]
_KNOWN_SECTION_END = re.compile(r"aborting execution|execution aborted|pre-run file check failed", re.I)


def extract_known_failure_reason(log_text):
    """Scan a run's log for the exact structured pre-run-check failure block.
    Returns a precise, human-written sentence naming the actual file(s) if
    found, or None if this log doesn't contain that known structure (caller
    should fall back to the AI translator in that case)."""
    if not log_text:
        return None
    sections = {"missing": [], "wrong_date": [], "stale": []}
    current = None
    for raw in log_text.splitlines():
        s = raw.strip()
        if not s:
            continue
        header_hit = False
        for pat, key in _KNOWN_SECTION_HEADERS:
            if pat.search(s):
                current = key; header_hit = True; break
        if header_hit:
            continue
        if current:
            if _KNOWN_SECTION_END.search(s):
                current = None
                continue
            item = re.sub(r"^[\s\-\*•❌✖✘]+", "", s).strip()
            if item:
                sections[current].append(item)
    parts = []
    if sections["missing"]:
        parts.append("missing file(s): " + ", ".join(sections["missing"]))
    if sections["wrong_date"]:
        parts.append("file(s) not dated today: " + ", ".join(sections["wrong_date"]))
    if sections["stale"]:
        parts.append("file(s) too old to use: " + ", ".join(sections["stale"]))
    if not parts:
        return None
    return "❌ Pre-run file check failed — " + "; ".join(parts) + ". Fix this, then run again."


# ── reusable config / windows / run-state (per pipeline) ─────────────────────
from core.short_allocation import check_password as _pb_pw, mask_number as _pb_mask

_OVERRIDES_LAST = {}   # overrides file -> last good 'runs' dict
_COUNTER_LAST = {}   # file path -> (date, n) of the last value read successfully


def _read_counter(p):
    """How many runs are done today for this pipe (from its run_counter.txt).

    The dashboard polls this every few seconds while a run is rewriting the file (open(...,"w") truncates it first), so
    a poll could catch the file EMPTY / half-written / locked and the old code answered 0 - the tiles then flashed
    "CTCL-01 overdue" for a few seconds until the next poll. Now a failed or empty read is retried a few times, and if it
    still fails the last good value of today is returned. Only a file that stays missing with no value earlier today (or one dated another
    day) counts as 0."""
    import time as _t
    base = os.getenv(p.get("state_dir_env", ""), p.get("state_default", ""))
    fp = os.path.join(base, p.get("state_file", "run_counter.txt"))
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    for _attempt in range(4):
        try:
            content = open(fp, encoding="utf-8", errors="replace").read().strip()
            if not content:
                raise ValueError("empty - being rewritten")
            if "|" in content:
                d, n = content.split("|", 1)
                n = int(n.strip())                       # "2026-10-21|" (half-written) raises -> retry
                _COUNTER_LAST[fp] = (d.strip(), n)
                return n if d.strip() == today else 0
            ints = re.findall(r"\d+", content)
            return int(ints[-1]) if ints else 0
        except Exception:                                # includes "file briefly missing" while another process swaps it
            _t.sleep(0.05)
    last = _COUNTER_LAST.get(fp)
    return last[1] if last and last[0] == today else 0

def _overrides_path(p):
    base = os.getenv(p.get("state_dir_env", ""), p.get("state_default", ""))
    return os.path.join(base, "run_overrides.json")

def _read_overrides(p):
    """Today's manual-override records for this pipe: {run_num_str: {...}}.

    2026-08-17 incident #2: MS15's tile 06 (13:20) showed "Done today" with a
    working-looking View button, but nothing was ever run for it and no log
    existed — because "Mark run done" (Manual overrides) only ever recorded
    the override in the acting browser's localStorage. Anyone viewing the
    dashboard from a DIFFERENT machine/browser/session (the normal case here —
    several people share this console) saw a completely normal "done" tile
    with no indication it was skipped, and a View click that led nowhere.
    This file is the server-side counterpart: written by /px/advance-run
    (api.py) whenever the counter is pushed forward, so every viewer — not
    just whoever clicked the button — sees which runs were genuinely executed
    versus manually marked done. Superset of the client-side record; the
    frontend now prefers this and falls back to localStorage only for entries
    created before this existed."""
    import time as _t
    fp = _overrides_path(p)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    for _attempt in range(4):                            # a half-written / briefly missing file is retried
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            if data.get("date") != today:
                return {}
            _OVERRIDES_LAST[fp] = data.get("runs", {})
            return _OVERRIDES_LAST[fp]
        except Exception:
            _t.sleep(0.05)
    return _OVERRIDES_LAST.get(fp, {})                    # still unreadable: last good value, else none

def _write_override(p, run_num, info):
    """Record run `run_num` as manually overridden today. Merges with any
    existing entries for today (e.g. mark_done then later backfill for the
    same run) rather than clobbering them. Never raises — a failure to write
    this audit record must not be able to block the actual override action
    (advance-run/backfill) it's describing."""
    fp = _overrides_path(p)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    data = {"date": today, "runs": {}}
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            existing = json.load(f)
        if existing.get("date") == today:
            data = existing
    except Exception:
        pass
    key = str(run_num)
    entry = dict(data["runs"].get(key) or {})
    entry.update(info)
    data["runs"][key] = entry
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8", errors="replace") as f:
            json.dump(data, f)
    except Exception:
        pass

def _busy_label_for(key):
    if not key:
        return key
    return PIPELINES.get(key, {}).get("name", key)


def pb_status(pipe):
    p = PIPELINES.get(pipe, {})
    runs = p.get("runs", 0)
    wins = {i: os.getenv(f"{pipe.upper()}_RUN{i}_WINDOW", "") for i in range(1, runs + 1)}
    _clear_if_stale(pipe)  # heals a wedged "running" lock (see stop_pipeline's note above)
    pb = _PB.get(pipe, {})
    # Real backend "is a run for THIS pipe actually in progress right now" signal —
    # previously the dashboard only had its own client-side _pxRunning flag, which
    # resets to false on every page reload/new tab and has no way to learn a run
    # is genuinely still going. That caused the "Files needed before running"
    # banner to freeze on stale pre-run data (or, after a reload, to ignore an
    # in-progress run entirely) instead of re-checking and hiding itself once the
    # files it was complaining about had actually been downloaded.
    #
    # This is now scoped to THIS pipe's own independent slot only — ctcl/ms15/cli
    # each have their own _PB entry, so CTCL running never makes MS15 or CLI
    # report busy (the bug this replaced: one shared _PB made every pipe's status
    # reflect whichever ONE of them happened to be running, anywhere).
    _running_here = bool(pb.get("running"))
    return {"windows": wins, "runs": runs, "run_state": _read_counter(p), "running": _running_here,
            "now": datetime.datetime.now().strftime("%H:%M"),
            "server_busy": _running_here, "server_busy_pipe": pipe if _running_here else None,
            "server_busy_label": _busy_label_for(pipe) if _running_here else None,
            "server_busy_since": pb.get("started_at"),
            # Server-side override records (see _read_overrides docstring) — lets
            # EVERY viewer, not just whoever clicked the button, tell a genuinely
            # executed run apart from one that was only marked done manually.
            "overrides": _read_overrides(p)}

def pb_get_config(pipe):
    p = PIPELINES.get(pipe, {}); g = os.getenv
    _ed = p.get("email_default", "")
    emails = {lbl: {"to": g(tk, _ed), "cc": g(ck, _ed), "to_key": tk, "cc_key": ck}
              for lbl, (tk, ck) in p.get("email_keys", {}).items()}
    creds = {}
    for lbl, c in p.get("cred", {}).items():
        if "single_key" in c:
            creds[lbl] = {"single_key": c["single_key"], "value": g(c["single_key"], ""),
                           "note": c.get("note", "")}
        else:
            creds[lbl] = {"user": g(c["user_key"], ""), "pass": _pb_mask(g(c["pass_key"], "")),
                          "user_key": c["user_key"], "pass_key": c["pass_key"], "note": c.get("note", "")}
    return {"emails": emails, "creds": creds}

def _write_env(updates):
    envf = os.path.join(REPO, ".env")
    try: lines = open(envf, encoding="utf-8").readlines()
    except Exception: lines = []
    seen, out = set(), []
    for ln in lines:
        mm = re.match(r"\s*([A-Z0-9_]+)\s*=", ln)
        if mm and mm.group(1) in updates:
            out.append(f"{mm.group(1)}={updates[mm.group(1)]}\n"); seen.add(mm.group(1))
        else:
            out.append(ln)
    for k, v in updates.items():
        if k not in seen: out.append(f"{k}={v}\n")
    open(envf, "w", encoding="utf-8").writelines(out)

def pb_update(pipe, password, updates):
    if not _pb_pw(password):
        return {"ok": False, "error": "Wrong password"}
    p = PIPELINES.get(pipe, {})
    allowed = set()
    for tk, ck in p.get("email_keys", {}).values(): allowed.add(tk); allowed.add(ck)
    for c in p.get("cred", {}).values():
        if "single_key" in c: allowed.add(c["single_key"])
        else: allowed.add(c["user_key"]); allowed.add(c["pass_key"])
    for i in range(1, p.get("runs", 0) + 1): allowed.add(f"{pipe.upper()}_RUN{i}_WINDOW")
    upd = {k: v for k, v in (updates or {}).items() if k in allowed}
    _write_env(upd)
    # Also update the running server's environment: run subprocesses inherit it,
    # and load_dotenv() never overrides an already-set variable — so without this
    # an edited To/CC (or password) would not apply until the server restarted.
    for _k, _v in upd.items():
        os.environ[_k] = str(_v)
    # A site password changed here must change everywhere that site is used (see core/cred_links.py).
    linked = []
    try:
        from core import cred_links
        linked = cred_links.on_saved({k: str(v) for k, v in upd.items()})
    except Exception as _e:
        print(f"[cred_links] skipped: {_e}", flush=True)
    return {"ok": True, "saved": len(upd), "also_updated": linked}


# ── reusable upload (validated) + backup retrieval (per pipeline) ────────────
import fnmatch as _pb_fn

def pb_valid(pipe):
    p = PIPELINES.get(pipe, {})
    globs, examples = [], []
    for row in p.get("file_types", []):
        globs += [g.strip() for g in row[1].split(",") if g.strip()]
        examples.append(row[0] + ((" — " + row[2]) if len(row) > 2 and row[2] else ""))
    return {"globs": globs, "examples": examples, "label": p.get("name", pipe)}

def _name_ok(name, globs):
    n = name.lower()
    return any(_pb_fn.fnmatch(n, g.lower()) for g in globs)

def _pb_push_file_to_ftp_folder(remote_folder, local_path, filename):
    """Push one local file into ANY FTP folder, auto-creating the path if it
    doesn't exist yet — generalised out of _pb_push_allocation_to_ftp() (which
    always pushed to the one fixed ALLOCATION folder) so the Client Prudential
    Limit evening/NEW flow can push into ALLOCATION(EVENING) too, using the
    exact same create-if-missing logic instead of a second copy of it."""
    from ftplib import FTP
    ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
    ftp_user = os.getenv("FTP_USER", "TRADING")
    ftp_pass = os.getenv("FTP_PASS", "")
    ftp = FTP(ftp_host)
    ftp.login(ftp_user, ftp_pass)
    try:
        ftp.cwd("/")
        for d in remote_folder.strip("/").split("/"):
            try:
                ftp.cwd(d)
            except Exception:
                ftp.mkd(d)
                ftp.cwd(d)
        with open(local_path, "rb") as f:
            ftp.storbinary(f"STOR {filename}", f)
    finally:
        ftp.quit()


def _pb_push_allocation_to_ftp(local_path, filename):
    """Push a manually-uploaded Allocation_Detail file straight to the same FTP folder
    Client Prudential Limit's Download step pulls it from — auto-creates the folder
    path if it doesn't exist yet. Means nobody has to open an SFTP client by hand."""
    _pb_push_file_to_ftp_folder("/TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION", local_path, filename)


def _pb_fetch_file_from_ftp_folder(remote_folder, local_path, filename):
    """Plain RETR of one file from `remote_folder` — no cleanup, no folder
    creation, no other side effect. _pb_fetch_allocation_detail() below always
    also runs _pb_cleanup_allocation_ftp_folder() (deletes everything except
    today's file) — exactly what must NOT happen when Client Prudential
    Limit's evening/NEW run reads today's Allocation_Detail back out of the
    still-plain ALLOCATION folder before it's been archived to
    ALLOCATION(MORNING): that cleanup would wipe out everything Run 1 already
    uploaded there (CLI_PRUDENTIAL_LIMIT.txt, MCX file, collaterals) before
    there was any chance to preserve it under the archived name. This is the
    side-effect-free primitive that flow uses instead."""
    from ftplib import FTP
    ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
    ftp_user = os.getenv("FTP_USER", "TRADING")
    ftp_pass = os.getenv("FTP_PASS", "")
    ftp = FTP(ftp_host)
    ftp.login(ftp_user, ftp_pass)
    try:
        ftp.cwd(remote_folder)
        files = ftp.nlst()
        if filename not in files:
            raise RuntimeError(f"{filename} not found in {remote_folder} on FTP")
        with open(local_path, "wb") as f:
            ftp.retrbinary(f"RETR {filename}", f.write)
    finally:
        ftp.quit()


def _pb_ftp_folder_exists(remote_folder):
    """True if `remote_folder` exists on the FTP server, else False. Used by
    the Client Prudential Limit evening/NEW flow to decide, at the start of
    every evening Run NEW, whether ALLOCATION(EVENING) has already been set
    up today (by an earlier click) or whether this is the first one."""
    from ftplib import FTP
    ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
    ftp_user = os.getenv("FTP_USER", "TRADING")
    ftp_pass = os.getenv("FTP_PASS", "")
    ftp = FTP(ftp_host)
    ftp.login(ftp_user, ftp_pass)
    try:
        ftp.cwd(remote_folder)
        return True
    except Exception:
        return False
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def _pb_ftp_rename_folder(old_remote_folder, new_remote_folder):
    """Rename a folder on the FTP server in place (RNFR/RNTO) — every file
    inside travels with it untouched. Used to archive Client Prudential
    Limit's morning ALLOCATION folder as ALLOCATION(MORNING) once the
    evening/NEW run has safely copied out the one file it needs."""
    from ftplib import FTP
    ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
    ftp_user = os.getenv("FTP_USER", "TRADING")
    ftp_pass = os.getenv("FTP_PASS", "")
    ftp = FTP(ftp_host)
    ftp.login(ftp_user, ftp_pass)
    try:
        ftp.rename(old_remote_folder, new_remote_folder)
    finally:
        ftp.quit()


def _pb_cleanup_allocation_ftp_folder(keep_filename, _p):
    """Port of the reference script's cleanup_ftp_folder(): delete everything in
    the ALLOCATION FTP folder except today's Allocation_Detail file. The original
    script calls this both after using an existing local file AND after a fresh
    download, so the FTP folder never accumulates old days' files. Non-fatal —
    failures here don't affect whether Allocation Detail itself was obtained."""
    from ftplib import FTP
    ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
    ftp_user = os.getenv("FTP_USER", "TRADING")
    ftp_pass = os.getenv("FTP_PASS", "")
    remote_folder = "/TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION"
    try:
        ftp = FTP(ftp_host)
        ftp.login(ftp_user, ftp_pass)
        ftp.cwd(remote_folder)
        files = ftp.nlst()
        for file in files:
            if file == keep_filename:
                continue
            try:
                ftp.delete(file)
                _p(f"🗑 Deleted old FTP file: {file}")
            except Exception as _de:
                _p(f"⚠️ Could not delete {file}: {_de}")
        ftp.quit()
        _p("✅ FTP cleanup done (Allocation file preserved)")
    except Exception as _ce:
        _p(f"⚠️ FTP cleanup failed (non-fatal): {_ce}")


def _pb_cleanup_ftp_folder_keep_prefixes(remote_folder, keep_prefixes, _p):
    """Delete every file in `remote_folder` except those whose name starts with
    one of `keep_prefixes` (case-insensitive). Used by Client Prudential
    Limit's afternoon Run NEW upload step to prune the morning's ALLOCATION
    folder down to just Allocation_Detail_<date>.csv + CLI_PRUDENTIAL_LIMIT.txt
    once afternoon's own files have gone into ALLOCATION(AFTERNOON) — those
    two are the only things anything downstream still reads out of the plain
    ALLOCATION folder; everything else there is stale collateral/MCX data from
    the morning run. Non-fatal — failures here don't affect whether the
    afternoon/evening upload itself succeeded."""
    from ftplib import FTP
    ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
    ftp_user = os.getenv("FTP_USER", "TRADING")
    ftp_pass = os.getenv("FTP_PASS", "")
    try:
        ftp = FTP(ftp_host)
        ftp.login(ftp_user, ftp_pass)
        try:
            ftp.cwd(remote_folder)
        except Exception:
            _p(f"ℹ️ {remote_folder} doesn't exist yet — nothing to clean.")
            ftp.quit()
            return
        files = ftp.nlst()
        kept, deleted = [], []
        for file in files:
            if any(file.lower().startswith(p.lower()) for p in keep_prefixes):
                kept.append(file)
                continue
            try:
                ftp.delete(file)
                deleted.append(file)
                _p(f"🗑 Deleted from {remote_folder}: {file}")
            except Exception as _de:
                _p(f"⚠️ Could not delete {file} from {remote_folder}: {_de}")
        ftp.quit()
        _p(f"✅ {remote_folder} cleaned — kept {len(kept)} file(s) "
           f"({', '.join(kept) or 'none'}), deleted {len(deleted)}")
    except Exception as _ce:
        _p(f"⚠️ Could not clean {remote_folder} (non-fatal): {_ce}")


def _pb_delete_ftp_folder(remote_folder, _p):
    """Delete every file inside `remote_folder`, then remove the folder itself
    (RMD). Used by Client Prudential Limit's evening Run NEW upload step to
    wipe out ALLOCATION(AFTERNOON) entirely once the evening folder has its
    own fresh files uploaded — that folder never holds Allocation_Detail or
    CLI_PRUDENTIAL_LIMIT.txt (both are morning-only, uploaded only to the
    plain ALLOCATION folder), so by evening there is nothing in it worth
    keeping. Non-fatal — failures here don't affect whether the evening
    upload itself succeeded."""
    from ftplib import FTP
    ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
    ftp_user = os.getenv("FTP_USER", "TRADING")
    ftp_pass = os.getenv("FTP_PASS", "")
    try:
        ftp = FTP(ftp_host)
        ftp.login(ftp_user, ftp_pass)
        try:
            ftp.cwd(remote_folder)
        except Exception:
            _p(f"ℹ️ {remote_folder} doesn't exist — nothing to delete.")
            ftp.quit()
            return
        files = ftp.nlst()
        for file in files:
            try:
                ftp.delete(file)
                _p(f"🗑 Deleted: {remote_folder}/{file}")
            except Exception as _de:
                _p(f"⚠️ Could not delete {file}: {_de}")
        ftp.cwd("/")
        try:
            ftp.rmd(remote_folder)
            _p(f"✅ Deleted folder: {remote_folder}")
        except Exception as _re:
            _p(f"⚠️ Could not remove folder {remote_folder} (non-fatal — its files were "
               f"still deleted above; safe to remove the empty folder by hand): {_re}")
        ftp.quit()
    except Exception as _ce:
        _p(f"⚠️ Could not delete {remote_folder} (non-fatal): {_ce}")


def _pb_fetch_allocation_detail(base_folder, today_str, _p):
    """Shared Allocation_Detail fetch (local-first, FTP fallback, auto-create+push
    FTP folder if needed, then clean the FTP folder down to just today's file —
    matching the reference script's cleanup_ftp_folder()) — used identically by
    BOTH the OLD and NEW Client Prudential Limit allocation steps
    (core/pipelines/cli_allocation.py and cli_allocation_new.py). This part of the
    pipeline is unchanged between OLD and NEW — only the pre-file check and the
    processing/math differ — so it lives here once instead of being duplicated.
    `_p` is the caller's log function (prints + appends to its own log buffer).
    Returns the local Allocation_Detail path on success. Raises on failure — caller
    wraps this in its own try/except to add the [FRIENDLY_MSG]/SystemExit handling."""
    from ftplib import FTP
    alloc_filename = f"Allocation_Detail_{today_str}.csv"
    alloc_path = os.path.join(base_folder, alloc_filename)
    remote_folder = "/TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION"
    ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
    ftp_user = os.getenv("FTP_USER", "TRADING")
    ftp_pass = os.getenv("FTP_PASS", "")

    if os.path.exists(alloc_path):
        _p(f"⏭ Using existing local file: {alloc_filename} — skipping FTP download")
        try:
            ftp = FTP(ftp_host); ftp.login(ftp_user, ftp_pass)
            folder_exists = True
            try:
                ftp.cwd(remote_folder)
            except Exception:
                folder_exists = False
            finally:
                ftp.quit()
            if not folder_exists:
                _p(f"ℹ️  FTP folder {remote_folder} doesn't exist yet — creating it and "
                   f"pushing our local {alloc_filename} there (replaces the manual SFTP step)")
                _pb_push_allocation_to_ftp(alloc_path, alloc_filename)
                _p("✅ FTP folder created and file pushed")
        except Exception as _fe:
            _p(f"⚠️  Could not verify/bootstrap FTP folder (non-fatal): {_fe}")
        # Reference script calls cleanup_ftp_folder() here too — keep the FTP
        # folder down to just today's file even on the local-first path.
        _pb_cleanup_allocation_ftp_folder(alloc_filename, _p)
        return alloc_path

    _p(f"ℹ️  {alloc_filename} not found locally — checking FTP")
    ftp = FTP(ftp_host)
    ftp.login(ftp_user, ftp_pass)
    folder_exists = True
    try:
        ftp.cwd(remote_folder)
    except Exception:
        folder_exists = False
    if not folder_exists:
        ftp.quit()
        raise RuntimeError(
            f"{remote_folder} doesn't exist on FTP AND {alloc_filename} isn't in our local "
            f"folder either — upload it via 'Upload files' first, or place the file on the "
            f"FTP server directly")
    try:
        files = ftp.nlst()
        if alloc_filename not in files:
            raise RuntimeError(f"{alloc_filename} not found on FTP and not present locally — "
                                f"upload it via 'Upload files' or place it on the FTP server first")
        with open(alloc_path, "wb") as f:
            ftp.retrbinary(f"RETR {alloc_filename}", f.write)
        _p(f"✅ Downloaded: {alloc_filename}")
        # Reference script deletes every other file in the folder right after a
        # fresh download too — same cleanup, just inline there instead of a
        # separate call.
    finally:
        ftp.quit()
    _pb_cleanup_allocation_ftp_folder(alloc_filename, _p)
    return alloc_path


def _pb_download_kambala_from_ftp(dest_path, filename):
    """Single-file fetch of the Kambala deposit file used by Client Prudential
    Limit's precheck (RISK_REPORT + Kambala must both be present for today before
    Run OLD proceeds). Extracted from the user's ftpfiles_newfinal.py — that script
    downloads ~15 files across 9 FTP folders for a completely different workflow
    (bank alerts, position books, holdings); this pulls out ONLY the one file CLI
    needs, from ONE folder, instead of running that whole script.
      Remote folder: /TRADING/CTCL_FILES/KORP_LIVE/KAMB/DEPO
      Filename:      Kambala_Omny_Depo_Adhoc_All_level.txt_<DDMMYYYY>.txt
      Match:         case-insensitive (same as the source script's `rf.lower() ==
                      fname.lower()` check)
    Returns True if downloaded, False if the folder doesn't have it (caller decides
    what to do — cli_allocation.py's precheck treats "not found" as still-missing
    and stops the run; cli_download.py just leaves it for next time)."""
    from ftplib import FTP
    ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
    ftp_user = os.getenv("FTP_USER", "TRADING")
    ftp_pass = os.getenv("FTP_PASS", "")
    remote_folder = "/TRADING/CTCL_FILES/KORP_LIVE/KAMB/DEPO"
    ftp = FTP(ftp_host)
    ftp.login(ftp_user, ftp_pass)
    try:
        ftp.cwd(remote_folder)
        remote_files = ftp.nlst()
        match = next((rf for rf in remote_files if rf.lower() == filename.lower()), None)
        if not match:
            return False
        with open(dest_path, "wb") as f:
            ftp.retrbinary(f"RETR {match}", f.write)
        return True
    finally:
        ftp.quit()


def _pb_archive_stale_common_files(common_dir, globs, pipe):
    """Global cleanup for the shared COMMON FILES folder (C:\\COMMON FILES by
    default) — used by both CTCL vs NMASS and Client Prudential Limit for
    RISK_REPORT (same physical file, one shared copy). The user was manually
    moving yesterday's leftover RISK_REPORT into
    C:\\COMMON FILES\\BACKUP FILES AUTOMATED\\<its own date>\\ every time they
    uploaded a fresh one — this automates that: whenever *anything* gets
    uploaded to the common folder, every file matching this pipe's
    common_globs that ISN'T dated today gets moved into its own dated
    subfolder there, named after the date embedded in ITS filename (not
    today's date) — matches the existing manual pattern exactly (a file
    dated 13-07-2026 archived on 14-07-2026 lands in a folder named
    "13-07-2026", not "14-07-2026").
    Generic by design — works off whatever common_globs a pipe declares, so
    adding a new shared common-file type later doesn't need new cleanup code.
    Best-effort only: never raises, since this runs as a side effect of an
    upload the user is actively waiting on."""
    if not globs or not os.path.isdir(common_dir):
        return
    today_str = datetime.datetime.now().strftime("%d%m%Y")
    backup_root = os.path.join(common_dir, "BACKUP FILES AUTOMATED")
    for g in globs:
        for fp in glob.glob(os.path.join(common_dir, g)):
            base = os.path.basename(fp)
            if not os.path.isfile(fp):
                continue
            if today_str in base:
                continue   # today's own file — leave it in place
            # Pull an 8-digit DDMMYYYY run out of the filename to name the
            # archive folder after; fall back to today's date if the filename
            # doesn't have one (shouldn't happen for RISK_REPORT_<date>.csv).
            m = re.search(r"(\d{2})(\d{2})(\d{4})", base)
            folder_name = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else \
                          datetime.datetime.now().strftime("%d-%m-%Y")
            dest_dir = os.path.join(backup_root, folder_name)
            try:
                os.makedirs(dest_dir, exist_ok=True)
                dest_path = os.path.join(dest_dir, base)
                if os.path.exists(dest_path):
                    os.remove(dest_path)
                shutil.move(fp, dest_path)
                try:
                    use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
                    _sf = (__import__("core.db_postgres", fromlist=["save_run_file"]).save_run_file if use_pg
                           else __import__("core.db", fromlist=["save_run_file"]).save_run_file)
                    with open(dest_path, "rb") as fb:
                        _sf(pipe, "backup", base, fb.read(), tag=folder_name)
                except Exception:
                    pass
            except Exception:
                pass   # best-effort — don't let a stray file block the actual upload


# ── Password-protected reference files ───────────────────────────────────────
# A small set of rarely-updated files where a wrong/stale replacement silently
# changes real calculations for every account (KAMBALA's virtual-ID list,
# PROBABLE's branch/region + mail-list masters) rather than failing loudly —
# so REPLACING an existing copy of one of these requires the same password
# used everywhere else in this app (core.short_allocation.check_password).
# A FIRST upload (nothing there yet) never needs a password — only replacing
# one that already exists does. Matched case-insensitively on filename alone,
# so it works no matter which button/target the upload came in through.
PROTECTED_UPLOAD_FILES = {
    "ctcl": {
        "kambala_allocation_list.xlsx": {"dest_env": "CTCL_NMASS_FOLDER",
                                          "dest_default": r"D:\CTCL VS NMASS", "dest_subdir": ""},
    },
    "probable": {
        "mail list.xlsx":               {"dest_env": "PROBABLE_FOLDER",
                                          "dest_default": r"D:\PROBABLE", "dest_subdir": "MASTER"},
        "brm list_file.xlsx":           {"dest_env": "PROBABLE_FOLDER",
                                          "dest_default": r"D:\PROBABLE", "dest_subdir": "MASTER"},
        "brm list_file.csv":            {"dest_env": "PROBABLE_FOLDER",
                                          "dest_default": r"D:\PROBABLE", "dest_subdir": "MASTER"},
        "branch_and_region_list.xlsx":  {"dest_env": "PROBABLE_FOLDER",
                                          "dest_default": r"D:\PROBABLE", "dest_subdir": "MASTER"},
    },
    "physdel": {                        # replacing a master file needs the app password (a first upload does not)
        "copy of t1 mail list.xlsx":    {"dest_env": "PHYSICAL_DELIVERY_FOLDER",
                                          "dest_default": r"D:\CONSOLE\data\PHYSICAL DELIVERY", "dest_subdir": "MAIN_FOLDER"},
        "branch_and_region_list.xlsx":  {"dest_env": "PHYSICAL_DELIVERY_FOLDER",
                                          "dest_default": r"D:\CONSOLE\data\PHYSICAL DELIVERY", "dest_subdir": "MAIN_FOLDER"},
        "brm list_file.xlsx":           {"dest_env": "PHYSICAL_DELIVERY_FOLDER",
                                          "dest_default": r"D:\CONSOLE\data\PHYSICAL DELIVERY", "dest_subdir": "MAIN_FOLDER"},
        "brm list_file.csv":            {"dest_env": "PHYSICAL_DELIVERY_FOLDER",
                                          "dest_default": r"D:\CONSOLE\data\PHYSICAL DELIVERY", "dest_subdir": "MAIN_FOLDER"},
    },
}


def _pb_protected_dest(rule):
    base = os.getenv(rule["dest_env"], rule["dest_default"])
    sub = rule.get("dest_subdir") or ""
    return os.path.join(base, sub) if sub else base


def pb_upload(pipe, items, target="main", password=""):
    """Validated upload. target='main' → pipeline folder (e.g. D:\\CTCL VS NMASS);
    target='common' → the common folder (e.g. C:\\COMMON FILES, RISK_REPORT only);
    target='master' → a pipe's own rarely-updated reference folder (currently only
    PROBABLE's PROBABLE_FOLDER\\MASTER — MAIL LIST.xlsx / Branch_AND_Region_LIST.xlsx).

    Every file is checked against PROTECTED_UPLOAD_FILES FIRST, regardless of
    `target` — if it's a recognized protected filename for this pipe, it goes
    to its own fixed destination and REPLACING an existing copy requires
    `password` to check out via check_password(); a wrong/missing password
    puts that filename in the returned "password_required" list instead of
    ok/skipped/rejected, so the frontend can prompt and retry.

    For everything else: an existing same-named file is now REPLACED, not
    skipped — this used to be "skip, keep the old one" for 'main'/'common'
    (daily input files) on the theory that a same-named file already being
    there meant "already downloaded today". Per explicit instruction, every
    ordinary upload should now update the file in place; the small protected
    set above is the only place a stale re-upload is actually dangerous
    enough to gate behind a password instead."""
    from core.short_allocation import check_password as _pb_check_pw
    p = PIPELINES.get(pipe, {})
    spec = pb_valid(pipe)
    common_globs = [g.lower() for g in p.get("common_globs", [])]
    protected_map = PROTECTED_UPLOAD_FILES.get(pipe, {})
    password_required = []
    remaining_items = []
    protected_ok, protected_rejected = [], []
    for fp in items:
        base = os.path.basename(fp)
        rule = protected_map.get(base.lower())
        if not rule:
            remaining_items.append(fp)
            continue
        dest2 = _pb_protected_dest(rule)
        try:
            os.makedirs(dest2, exist_ok=True)
        except Exception as e:
            protected_rejected.append(f"{base}: could not reach {dest2} ({e})")
            try: os.remove(fp)
            except Exception: pass
            continue
        tp2 = os.path.join(dest2, base)
        if os.path.exists(tp2) and not _pb_check_pw(password):
            password_required.append(base)
            continue   # leave fp where it is — the frontend retries with a password
        try:
            if os.path.exists(tp2):
                os.remove(tp2)
            shutil.move(fp, tp2)
            protected_ok.append(base)
            try:
                use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
                _sf = (__import__("core.db_postgres", fromlist=["save_run_file"]).save_run_file if use_pg
                       else __import__("core.db", fromlist=["save_run_file"]).save_run_file)
                with open(tp2, "rb") as fb:
                    _sf(pipe, "upload", base, fb.read(), tag="protected")
            except Exception:
                pass
        except Exception as e:
            protected_rejected.append(f"{base}: {e}")
    items = remaining_items
    # Default is now REPLACE for every target, not just 'master' — see docstring.
    replace_existing = True
    if target == "master":
        dest = os.getenv(p.get("master_dir_env", ""), p.get("upload_default", ""))
        dest = os.path.join(dest, p.get("master_subdir", "MASTER"))
        globs = [g.lower() for g in p.get("master_globs", [])]
        spec = {**spec,
                "label": p.get("master_label", "MASTER"),
                "examples": [g for g in p.get("master_globs", [])]}
    elif target == "common":
        dest = os.getenv(p.get("common_dir_env", ""), p.get("common_default", ""))
        globs = common_globs
        spec = {**spec,
                "label": p.get("common_label", "COMMON FILES"),
                "examples": [x for x in spec["examples"] if x.lower().startswith("risk_report")] or spec["examples"]}
    else:
        dest = os.getenv(p.get("upload_dir_env", ""), p.get("upload_default", ""))
        # RISK_REPORT belongs in the common folder — reject it here so it can't
        # land in the wrong place.
        globs = [g for g in spec["globs"] if g.lower() not in common_globs]
        spec = {**spec,
                "examples": [x for x in spec["examples"] if not x.lower().startswith("risk_report")]}
    # If the real base folder can't be created/reached (e.g. this dashboard is
    # running on a different machine than the one with D:\CLI_PRUDENTIAL_LIMIT
    # etc. actually mapped), fall back to a generic temp folder instead of
    # failing outright — the response tells the caller which folder was
    # actually used (spec["dest_is_fallback"]) so the UI can say so.
    dest_is_fallback = False
    try:
        os.makedirs(dest, exist_ok=True)
    except Exception:
        import tempfile
        dest = os.path.join(tempfile.gettempdir(), "RMS_COPILOT_UPLOAD_FALLBACK", pipe, target)
        os.makedirs(dest, exist_ok=True)
        dest_is_fallback = True
    logdir = os.path.join(LOG_BASE, p.get("logdir", pipe)); os.makedirs(logdir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logp = os.path.join(logdir, f"UPLOAD__{ts}.log")
    ok, skipped, rejected = list(protected_ok), [], list(protected_rejected)
    lines = [f"# UPLOAD {pipe}  dest={dest}  time={ts}"]
    if protected_ok:
        lines.append(f"# password-protected files replaced: {', '.join(protected_ok)}")
    if password_required:
        lines.append(f"# password required, not replaced: {', '.join(password_required)}")
    for fp in items:
        base = os.path.basename(fp)
        if globs and not _name_ok(base, globs):
            rejected.append(base); lines.append(f"REJECT  {base}")
            try: os.remove(fp)
            except Exception: pass
            continue
        tp = os.path.join(dest, base)
        if os.path.exists(tp) and not replace_existing:
            skipped.append(base); lines.append(f"SKIP    {base}")
            try: os.remove(fp)
            except Exception: pass
            continue
        if os.path.exists(tp) and replace_existing:
            try: os.remove(tp)
            except Exception as e:
                rejected.append(f"{base}: could not replace existing file ({e})")
                lines.append(f"ERROR   {base}: could not remove old version: {e}")
                try: os.remove(fp)
                except Exception: pass
                continue
            lines.append(f"REPLACE {base}")
        try:
            shutil.move(fp, tp); ok.append(base); lines.append(f"OK      {base}")
            # Archive a durable copy into Postgres/SQLite (same table SA's manual
            # upload already uses) so the file's bytes are retrievable from ANY
            # machine afterward — the local folder write above still has to happen
            # because the exchange-login scripts are hardcoded to read from that
            # Windows path on the one machine with exchange access, but the DB copy
            # means the upload itself isn't only living on whichever PC ran this.
            # Applies uniformly to every pipe (CTCL, CLI, future ones) — not just SA.
            try:
                use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
                _sf = (__import__("core.db_postgres", fromlist=["save_run_file"]).save_run_file if use_pg
                       else __import__("core.db", fromlist=["save_run_file"]).save_run_file)
                with open(tp, "rb") as fb:
                    _sf(pipe, "upload", base, fb.read(), tag=target)
            except Exception as e:
                lines.append(f"  DB archive skipped for {base}: {e}")
            # NOTE: this button intentionally does NOT push anything to FTP —
            # it's purely "put a manually-downloaded file into the base folder."
            # The FTP push (with folder-create-if-missing) happens generically
            # inside the Run chain's upload step (cli_upload.py's run_cli_upload),
            # which always verifies/creates the FTP folder before uploading
            # whatever's sitting in the base folder — including a file placed
            # here by hand.
        except Exception as e:
            rejected.append(f"{base}: {e}"); lines.append(f"ERROR   {base}: {e}")
    lines.append(f"# done: {len(ok)} copied, {len(skipped)} already-present, {len(rejected)} rejected")
    # Whenever something lands in the shared COMMON FILES folder, sweep it for
    # any not-dated-today RISK_REPORT (or whatever else this pipe shares there)
    # left over from a previous day and archive it — was being done by hand.
    if target == "common":
        _pb_archive_stale_common_files(dest, common_globs, pipe)
    try:
        open(logp, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    except Exception:
        pass
    return {"dest": dest, "label": spec["label"], "ok": ok, "skipped": skipped,
            "rejected": rejected, "valid": spec["examples"], "dest_is_fallback": dest_is_fallback,
            "password_required": password_required}

def pb_backup_types(pipe):
    pp = PIPELINES.get(pipe, {})
    # "backup_types" (when a pipe has it) is what the Backup files window can show; "file_types" is what may be UPLOADED.
    return [{"label": r[0], "globs": r[1]} for r in (pp.get("backup_types") or pp.get("file_types", []))]

def _pb_backup_roots(pipe):
    p = PIPELINES.get(pipe, {})
    base = os.getenv(p.get("backup_root_env", ""), p.get("upload_default", ""))
    return [os.path.join(base, p.get("backup_subdir", "BACKUPFILES AUTOMATED"))]

def _pb_cli_env_window(env_key, default):
    """Read an 'HH:MM-HH:MM' window from .env (CLI_RUN1_WINDOW /
    CLI_RUN2_WINDOW / CLI_RUN3_WINDOW — same vars cli_allocation_new.py's
    run-gate and cli_upload.py's backfill classifier use), falling back to
    `default` on anything missing/malformed. Returns (start_min, end_min)."""
    def _mins(hhmm, fallback_mins):
        try:
            h, m = (int(x) for x in hhmm.strip().split(":", 1))
            return h * 60 + m
        except Exception:
            return fallback_mins
    fb_start, _, fb_end = default.partition("-")
    fh, fm = (int(x) for x in fb_start.split(":"))
    eh, em = (int(x) for x in fb_end.split(":"))
    fb_start_mins, fb_end_mins = fh * 60 + fm, eh * 60 + em
    raw = (os.getenv(env_key) or "").strip() or default
    start_s, _, end_s = raw.partition("-")
    return _mins(start_s, fb_start_mins), _mins(end_s, fb_end_mins)


def _pb_cli_slot_from_dirname(name):
    """CLI-only: classify an HH-MM-SS exact-run-time folder name (e.g.
    "08-36-11", "15-55-53", "20-23-14" — some machines back up by timestamp
    instead of a Morning/Afternoon/Evening word) into the real CLI run
    window it actually falls in — read from .env (CLI_RUN1_WINDOW /
    CLI_RUN2_WINDOW / CLI_RUN3_WINDOW), defaulting to Morning 07:00-10:00,
    Afternoon 15:30-17:15, Evening 19:00-21:30, 24-hour clock — falling back
    to a broad <12/<18/else half-of-day bucket for a stray time outside all
    three (one-off manual run). Returns None if name isn't HH-MM-SS at all.
    Mirrors cli_upload.py's backfill_date_to_db()._slot_for_subdir() — kept
    in sync by hand since one lives in core/pipelines (backfill path) and
    this one lives in core/pipeline_box.py (disk-scan/list path)."""
    m = re.fullmatch(r"(\d{2})-(\d{2})-\d{2}", name)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    mins = hour * 60 + minute
    m_start, m_end = _pb_cli_env_window("CLI_RUN1_WINDOW", "07:00-10:00")
    a_start, a_end = _pb_cli_env_window("CLI_RUN2_WINDOW", "15:30-17:15")
    e_start, e_end = _pb_cli_env_window("CLI_RUN3_WINDOW", "19:00-21:30")
    if m_start <= mins <= m_end: return "Morning"
    if a_start <= mins <= a_end: return "Afternoon"
    if e_start <= mins <= e_end: return "Evening"
    if hour < 12: return "Morning"
    if hour < 18: return "Afternoon"
    return "Evening"


def _pb_parse_date_part(part):
    """Recognize a date encoded in one path segment of a backup folder tree.
    THREE genuinely different conventions are in use, confirmed against what
    each pipeline's own backup_and_cleanup() actually writes to disk (not just
    what a comment here used to claim): ctcl/cli use DD-MM-YYYY (e.g.
    "21-07-2026"); ms15 (margin_shortage_15min.py) uses YYYY-MM-DD (e.g.
    "2026-06-01") — this was NOT recognized here before, which is exactly why
    /px/client-lookup?pipe=ms15 always came back with empty inputs/outputs and
    zero warnings (every ms15 backup date folder silently failed to parse as a
    date, so the whole by-directory scan found nothing to iterate); probable's
    own backup steps (probable_reconcile.py/probable_alerts.py) format the
    date folder as "%d %b %Y" upper-cased (e.g. "21 JUL 2026") — not a bug in
    probable's own code (that's the reference script's own convention, left
    untouched per "don't change calculation/threshold logic"), just something
    this GENERIC lookup needs to also understand, or every Probable backup
    file silently fell back to file mtime for date-range filtering instead of
    its real logical folder date."""
    part = part.strip()
    mm = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", part)
    if mm:
        try: return datetime.date(int(mm.group(3)), int(mm.group(2)), int(mm.group(1)))
        except Exception: return None
    mm2 = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", part)
    if mm2:
        try: return datetime.date(int(mm2.group(1)), int(mm2.group(2)), int(mm2.group(3)))
        except Exception: return None
    if re.fullmatch(r"\d{1,2} [A-Za-z]{3} \d{4}", part):
        try: return datetime.datetime.strptime(part.title(), "%d %b %Y").date()
        except Exception: return None
    return None

# Some pipelines archive their per-run output files to Postgres under a
# DIFFERENT "pipeline" column value than the short key used everywhere else
# in this file/the API (historical — the per-run archival call inside each
# pipeline script predates the short "px" keys). The short key itself is
# ALSO checked unconditionally below, since the generic upload/housekeeping
# archival in this file (pb_upload, the BACKUPFILES housekeeping step) always
# uses the short key. cli/probable have no separate long name.
_PB_DB_PIPELINE_ALIASES = {
    "ctcl": ["ctcl_vs_nmass"],
    "ms15": ["margin_shortage"],
    "sa":   ["short_allocation"],
}

def _pb_db_pipeline_names(pipe):
    names, seen, out = [pipe] + _PB_DB_PIPELINE_ALIASES.get(pipe, []), set(), []
    for n in names:
        if n not in seen:
            seen.add(n); out.append(n)
    return out

def pb_list_backups(pipe, from_date="", to_date="", types=None, slots=None):
    globs = [g.strip().lower() for g in (types or []) if g and g.strip()]
    slot_filter = {s for s in (slots or []) if s in ("Morning", "Afternoon", "Evening")}
    def pd(s):
        try: return datetime.datetime.strptime(s, "%Y-%m-%d").date()
        except Exception: return None
    df, dt = pd(from_date), pd(to_date)
    res = []
    seen_disk = set()   # (iso date, lowercase filename, size, modified minute): the very same file copied into two
                        # backup folders must be listed once, not twice
    seen_keys = set()   # (iso date, lowercase filename, size) — used to skip a
                         # Postgres row when the exact same file was already
                         # found on disk, so a machine that still has BOTH
                         # (old machine, dual-writing) doesn't show duplicates
    for root in _pb_backup_roots(pipe):
        if not os.path.isdir(root): continue
        for dp, _d, files in os.walk(root):
            cdate = None
            crun = None
            for part in dp.replace("\\", "/").split("/"):
                _cd = _pb_parse_date_part(part)
                if _cd:
                    cdate = _cd
                    continue
                # A bare 1-2 digit folder segment inside the dated backup tree is
                # the run-number subfolder (ctcl/ms15: <date>\<NN>\...). Files with
                # a fixed daily name (e.g. "Update Limits<date>.xlsx") are otherwise
                # indistinguishable in the archive view — same name every run, only
                # size differs — so surface which run + what time produced each one.
                # CLI uses a Morning/Afternoon/Evening word instead of a number
                # (it can run up to 3x/day at fixed windows, not a numbered
                # sequence) — same idea, so it gets the same "run" treatment.
                p = part.strip()
                if re.fullmatch(r"\d{1,2}", p):
                    crun = p
                elif p in ("Morning", "Afternoon", "Evening"):
                    crun = p
                elif pipe == "cli":
                    crun = _pb_cli_slot_from_dirname(p) or crun
            for fn in files:
                if globs and not any(_pb_fn.fnmatch(fn.lower(), g) for g in globs): continue
                fpp = os.path.join(dp, fn)
                try: st = os.stat(fpp)
                except Exception: continue
                fdate = cdate or datetime.date.fromtimestamp(st.st_mtime)
                if df and fdate < df: continue
                if dt and fdate > dt: continue
                _dk = (fdate.isoformat(), fn.lower(), st.st_size, int(st.st_mtime // 60))
                if _dk in seen_disk: continue
                seen_disk.add(_dk)
                res.append({
                    "path": fpp, "name": fn,
                    "date": fdate.strftime("%d-%m-%Y"),
                    "size": st.st_size,
                    "run": crun,
                    "time": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%H:%M"),
                })
                seen_keys.add((fdate.isoformat(), fn.lower(), st.st_size))

    # ── Postgres supplement ─────────────────────────────────────────────────
    # Local BACKUPFILES AUTOMATED folders are being phased out in favour of
    # Postgres as the single source of truth — a fresh/secondary machine (no
    # exchange downloads run there) never populates these disk folders at all,
    # so relying on disk alone means the search always comes back empty there.
    # Every file archived via save_run_file() during a real run already lives
    # in run_files regardless of which machine ran it, so pull those in too —
    # skipping anything the disk scan above already found, so a machine that
    # still has BOTH (the original/download machine, still dual-writing)
    # doesn't show the same file twice.
    try:
        if os.getenv("USE_POSTGRES", "false").strip().lower() == "true":
            from core.db_postgres import list_run_files as _pb_db_list_files
            for db_pipe in _pb_db_pipeline_names(pipe):
                for r in _pb_db_list_files(pipeline=db_pipe, from_date=(from_date or None),
                                            to_date=(to_date or None), days=36500):
                    fn = r.get("filename") or ""
                    if not fn: continue
                    if globs and not any(_pb_fn.fnmatch(fn.lower(), g) for g in globs): continue
                    fdate = r.get("run_date")
                    if not fdate: continue
                    key = (fdate.isoformat(), fn.lower(), r.get("size_bytes") or 0)
                    if key in seen_keys: continue
                    seen_keys.add(key)
                    tag = str(r.get("tag") or "")
                    crun = None
                    m = re.search(r"/(\d{1,2}|Morning|Afternoon|Evening)$", tag)  # "<date>/<run-or-slot>"
                    if m: crun = m.group(1)
                    elif re.fullmatch(r"\d{1,2}", tag): crun = tag  # bare run-number tag
                    ran_at = r.get("ran_at")
                    res.append({
                        "path": f"db:{db_pipe}:{r['id']}",
                        "name": fn,
                        "date": fdate.strftime("%d-%m-%Y"),
                        "size": r.get("size_bytes") or 0,
                        "run": crun,
                        "time": ran_at.strftime("%H:%M") if ran_at else "",
                    })
    except Exception as e:
        print(f"[pb_list_backups] Postgres lookup skipped (non-fatal): {e}")

    if slot_filter:
        res = [r for r in res if r.get("run") in slot_filter]
    res.sort(key=lambda x: (x["date"], x["name"]))
    return res[:3000]

def pb_backup_file(pipe, path):
    """Return the absolute path if it is inside this pipeline's backup roots, else None.
    Used by the per-file download endpoint. Returns None (not an error) for
    "db:..." paths — those are handled separately by pb_backup_db_file, since
    they have no filesystem path at all."""
    if str(path or "").startswith("db:"):
        return None
    ap = os.path.abspath(path or "")
    roots = [os.path.abspath(r) for r in _pb_backup_roots(pipe)]
    if os.path.isfile(ap) and any(ap.startswith(r) for r in roots):
        return ap
    return None

def pb_backup_db_file(path):
    """Companion to pb_backup_file for Postgres-sourced entries — path looks
    like 'db:<db_pipeline>:<run_files.id>' (as produced by pb_list_backups).
    Returns (filename, content_bytes) or None if not a db: path / not found."""
    s = str(path or "")
    if not s.startswith("db:"):
        return None
    parts = s.split(":", 2)
    if len(parts) != 3:
        return None
    try:
        file_id = int(parts[2])
    except ValueError:
        return None
    from core.db_postgres import get_run_file as _pb_db_get_file
    return _pb_db_get_file(file_id)


# Candidate account/client-code column names, tried in order, across every
# file type the backup browser's "Get files" tab can filter — CSV/XLSX
# collateral files use "Client Code" (or "Client/CP Code" for the NCDEX-style
# file); RISK_REPORT/Allocation Detail/Intermediate use "ACCOUNTID"/"AccountID".
_PB_ACCOUNT_COL_CANDIDATES = (
    "AccountID", "ACCOUNTID", "Account ID", "Client Code",
    "Client/CP Code", "UCC Code", "CLIENT CODE",
)


def _bk_fast_filtered_xlsx(ap, fname, client_codes):
    """Fast path for ONE .xlsx backup (same return contract as pb_backup_file_filtered).
    Returns (handled, result). handled=False means 'could not do it the fast way - use the old code'."""
    try:
        from core import bk_merge as _bk
        import io
        codes = [c for c in (client_codes or []) if c and c.strip()]
        if not codes:
            return False, None
        cands = list(_PB_ACCOUNT_COL_CANDIDATES) + ["Entity"]
        _f, df, status, _how = _bk.filter_one(ap, codes, cands, use_cache=True)
        xmime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if status == "error":
            return False, None
        if status == "no_column":
            return True, None            # same answer the old code gave: no recognisable account column
        if status == "no_data" or df is None or df.height == 0:
            return True, (fname, None, xmime)
        import pandas as pd
        pdf = pd.DataFrame(df.to_dict(as_series=False))
        bio = io.BytesIO()
        pdf.to_excel(bio, index=False)
        return True, (fname, bio.getvalue(), xmime)
    except Exception:
        return False, None


def pb_backup_file_filtered(pipe, path, client_codes):
    """Like pb_backup_file, but returns only the rows belonging to
    `client_codes` (case-insensitive), in the file's ORIGINAL column layout —
    same columns, same order, just fewer rows. Used by the "Get files" tab's
    optional Client/Account Code filter: fill in a code and every file you'd
    otherwise download in full comes back pre-filtered to just that account.

    Handles the two plain-text formats specially (CLI_PRUDENTIAL_LIMIT.txt:
    "ClientPrudentialLimit" header then "AccountID,Sum" lines; Kambala's
    '|'-delimited export with AccountID in column 0) since neither is a
    normal table. Every CSV/XLSX file is handled generically by trying each
    name in _PB_ACCOUNT_COL_CANDIDATES.

    Returns (filename, content_bytes, media_type) on success — content_bytes
    is None instead of b"" when the file WAS recognized/parsed but zero rows
    matched the given code(s) (e.g. that account doesn't trade this exchange
    at all), so the caller can tell "nothing to download here" apart from
    "here's a valid, genuinely empty file" and skip it instead of handing
    back a useless blank file. Returns None (not a tuple) if the file has no
    recognizable account column at all (or isn't one of the two special text
    formats) — caller falls back to serving the original, unfiltered file
    rather than failing outright.

    Handles BOTH a real on-disk path AND a 'db:<pipeline>:<id>' Postgres
    reference (same convention as pb_backup_file/pb_backup_db_file) — a file
    backfilled from another machine may have no local disk copy at all, so
    filtering has to work from the stored bytes too, not just from disk."""
    import pandas as pd
    content = None
    if str(path or "").startswith("db:"):
        got = pb_backup_db_file(path)
        if not got:
            return None
        fname, content = got
    else:
        ap = pb_backup_file(pipe, path)
        if not ap:
            return None
        fname = os.path.basename(ap)
        if fname.lower().endswith(".xlsx"):
            # big Excel backups: fast reader + cached copy (core/bk_merge.py) instead of parsing it again and again here
            handled, fast = _bk_fast_filtered_xlsx(ap, fname, client_codes)
            if handled:
                return fast
        try:
            with open(ap, "rb") as f:
                content = f.read()
        except Exception:
            return None
    codes = {c.strip().upper() for c in (client_codes or []) if c and c.strip()}
    if not codes:
        return None
    low = fname.lower()

    # ── CLI_PRUDENTIAL_LIMIT.txt — "ClientPrudentialLimit" header, then
    # "AccountID,Sum" lines (see core/pipelines/cli_allocation_new.py /
    # ALLOCATION_CLI_NMASS_NCDEX.py's run_processing()). ──────────────────────
    if low == "cli_prudential_limit.txt":
        try:
            lines = content.decode("utf-8", errors="replace").splitlines()
        except Exception:
            return None
        if not lines:
            return None
        header, rows = lines[0], lines[1:]
        kept = [ln for ln in rows if ln.split(",", 1)[0].strip().upper() in codes]
        if not kept:
            return (fname, None, "text/plain")
        out = "\n".join([header] + kept) + "\n"
        return (fname, out.encode("utf-8"), "text/plain")

    # ── Kambala_Omny_Depo_Adhoc_All_level.txt_<date>.txt — '|'-delimited,
    # row 0 is a header (kept as-is), column 0 is AccountID. ──────────────────
    if low.startswith("kambala_omny_depo_adhoc_all_level.txt_"):
        try:
            lines = content.decode("utf-8", errors="ignore").splitlines()
        except Exception:
            return None
        if not lines:
            return None
        header, rows = lines[0], lines[1:]
        kept = [ln for ln in rows if ln.split("|", 1)[0].strip().upper() in codes]
        if not kept:
            return (fname, None, "text/plain")
        out = "\n".join([header] + kept) + "\n"
        return (fname, out.encode("utf-8"), "text/plain")

    # ── Everything else: CSV / plain-text-with-a-header (CM/FO/CO
    # *ClientLevelCollaterals*.txt are comma-delimited exports with a real
    # header row — same shape as .csv, just a .txt extension — so .txt is
    # treated the same as .csv here) or XLSX, with SOME account-ish column —
    # try each known name in turn. ─────────────────────────────────────────
    is_csv = low.endswith(".csv") or low.endswith(".txt")
    is_xlsx = low.endswith(".xlsx")
    if not (is_csv or is_xlsx):
        return None
    import io
    col = None
    try:
        if is_csv:
            try:
                df = pd.read_csv(io.BytesIO(content), dtype=str)
            except Exception:
                df = pd.read_csv(io.BytesIO(content), dtype=str, encoding="latin-1")
            col = _pb_col(df, *_PB_ACCOUNT_COL_CANDIDATES)
        else:
            tmp_path = None
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
                    tf.write(content)
                    tmp_path = tf.name
                df, err = _pb_read_xlsx_pandas(tmp_path)
                col = _pb_col(df, *_PB_ACCOUNT_COL_CANDIDATES) if (not err and df is not None) else None
                if not col:
                    # Row-0 read either failed or had no recognizable account
                    # column — the real header may not be row 0 at all (e.g.
                    # the MCX RSK383 export, "Client Code" buried a few rows
                    # down — same issue _pb_read_xlsx_header_scan already
                    # works around for the Client Data lookup, just never
                    # applied here). Scan the first several rows for one
                    # that contains a recognizable account column.
                    for cand in _PB_ACCOUNT_COL_CANDIDATES:
                        hdf, herr = _pb_read_xlsx_header_scan(tmp_path, [cand])
                        if not herr and hdf is not None:
                            hcol = _pb_col(hdf, *_PB_ACCOUNT_COL_CANDIDATES)
                            if hcol:
                                df, col = hdf, hcol
                                break
                if not col or df is None:
                    return None
            finally:
                if tmp_path:
                    try: os.remove(tmp_path)
                    except Exception: pass
    except Exception:
        return None
    if not col:
        return None
    mask = df[col].astype(str).str.strip().str.upper().isin(codes)
    filtered = df[mask]
    if filtered.empty:
        return (fname, None, "text/csv" if is_csv else
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if is_csv:
        buf_s = filtered.to_csv(index=False)
        return (fname, buf_s.encode("utf-8"), "text/csv")
    bio = io.BytesIO()
    filtered.to_excel(bio, index=False)
    return (fname, bio.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _pb_filtered_bytes_to_df(fname, content, media_type):
    """Turn whatever pb_backup_file_filtered() handed back (csv bytes, xlsx
    bytes, or the text/plain CLI_PRUDENTIAL_LIMIT.txt/Kambala special
    formats) into a plain DataFrame of strings, so rows from many different
    dates' files can be concatenated — used by pb_backup_download_merged()
    only. Returns None on anything unparseable rather than raising."""
    import pandas as pd, io
    try:
        if media_type == "text/csv":
            try:
                return pd.read_csv(io.BytesIO(content), dtype=str)
            except Exception:
                return pd.read_csv(io.BytesIO(content), dtype=str, encoding="latin-1")
        if media_type and media_type.startswith("application/vnd.openxmlformats"):
            return pd.read_excel(io.BytesIO(content), dtype=str)
        # text/plain — one of pb_backup_file_filtered()'s two special formats:
        # CLI_PRUDENTIAL_LIMIT.txt is comma-delimited ("AccountID,Sum" rows
        # under a single header line), Kambala's export is '|'-delimited.
        text = content.decode("utf-8", errors="replace")
        lines = [ln for ln in text.splitlines() if ln != ""]
        if not lines:
            return None
        delim = "|" if (fname or "").lower().startswith("kambala") else ","
        header = lines[0].split(delim)
        rows = []
        for ln in lines[1:]:
            parts = ln.split(delim)
            if len(parts) < len(header):
                parts = parts + [""] * (len(header) - len(parts))
            elif len(parts) > len(header):
                parts = parts[:len(header)]
            rows.append(parts)
        return pd.DataFrame(rows, columns=header)
    except Exception:
        return None


def pb_backup_download_merged(pipe, items, client_codes):
    """Like pb_backup_file_filtered(), but across MANY files (typically the
    same file TYPE across a whole date range) stacked into ONE output, with
    a 'Date' column inserted as the first column so you can still tell which
    date each row came from — built for the "Get files" tab's client-code
    filter: filtering 5 exchange file types across a 30-day range used to
    hand back up to 5×30 separate per-date files; this hands back 5, one per
    type, each with every matching date's rows stacked together.

    `items` — list of {"path": ..., "date": "DD-MM-YYYY", "name": ...} (as
    returned by pb_list_backups / /px/backup/list) — all assumed to be the
    SAME file type; the caller (frontend) does the grouping by type since it
    already computes the type label for display.

    Returns (filename, content_bytes, media_type) — content_bytes is None if
    every item in the group had zero matching rows for the given code(s)
    (nothing to merge), same "no_data" convention as pb_backup_file_filtered.
    Returns None if `items` is empty or no item could be parsed at all."""
    import pandas as pd
    import re as _re
    codes = [c.strip() for c in (client_codes or []) if c and c.strip()]
    if not codes or not items:
        return None
    frames = []
    out_name = None
    any_recognized = False
    for it in items:
        path = it.get("path", "")
        date = it.get("date", "")
        if not path:
            continue
        got = pb_backup_file_filtered(pipe, path, codes)
        if not got:
            continue
        fname, content, media_type = got
        any_recognized = True
        if out_name is None:
            out_name = fname
        if content is None:
            continue   # this date's file had no rows for the code(s) — skip
        df = _pb_filtered_bytes_to_df(fname, content, media_type)
        if df is None or df.empty:
            continue
        # Only insert a Date column if the file doesn't already have one of
        # its own (some exports carry their own date/timestamp column) —
        # don't stack a second one on top.
        if not _pb_col(df, "Date"):
            # Written as ="DD-MM-YYYY" (an Excel formula producing literal
            # text) rather than the bare string — a plain "03-10-2026" gets
            # silently auto-converted by Excel into a real date value AND
            # reformatted per the user's locale, but ONLY on rows where the
            # day number is <=12 (so it could plausibly be misread as a
            # month, e.g. "03" in "03-10-2026"); a row like "15-09-2026"
            # can't be misread that way and is left as plain text — hence
            # the same column showing two different formats depending on
            # the date. Wrapping in ="..." forces every row to render
            # identically, always as the original DD-MM-YYYY text.
            df.insert(0, "Date", f'="{date}"' if date else date)
        frames.append(df)
    if not any_recognized:
        return None   # not a filterable file type at all — caller falls back
    if not frames:
        return (out_name or "merged.csv", None, "text/csv")
    merged = pd.concat(frames, ignore_index=True, sort=False)
    base = _re.sub(r"\.(csv|txt|xlsx)$", "", out_name or "merged", flags=_re.I)
    out_bytes = merged.to_csv(index=False).encode("utf-8")
    return (f"{base}_MERGED.csv", out_bytes, "text/csv")


# ── client data lookup across backup runs (Polars-powered) ───────────────────

def _pb_col(df, *names):
    """Case/space-insensitive column resolver — works on pandas or polars DataFrame.
    Returns the real column name or None."""
    cols = list(df.columns) if hasattr(df, "columns") else []
    low = {str(c).lower().strip(): c for c in cols}
    for n in names:
        c = low.get(str(n).lower().strip())
        if c is not None:
            return c
    return None

def _pb_read_csv_polars(path, codes):
    """Read a CSV with Polars (fast) and return {code: float} aggregate for given codes.
    col_selector is called with the polars df to pick columns to sum."""
    import polars as pl
    try:
        df = pl.read_csv(path, infer_schema_length=0, ignore_errors=True)
    except Exception:
        return None, None   # (df, error)
    return df, None

def _pb_read_xlsx_pandas(path):
    """Read xlsx. Tries polars/fastexcel (Rust, fast) first, falls back to pandas/msoffcrypto."""
    import pandas as pd
    # polars.read_excel() uses fastexcel (Rust) — much faster than openpyxl when available
    try:
        import polars as pl
        df = pl.read_excel(path, infer_schema_length=0)
        return df.to_pandas().astype(str), None
    except Exception:
        pass
    # pandas fallback (openpyxl — slower but always available)
    try:
        return pd.read_excel(path, dtype=str), None
    except Exception as e_pd:
        try:
            import io, msoffcrypto
            buf = io.BytesIO()
            with open(path, "rb") as f:
                off = msoffcrypto.OfficeFile(f)
                off.load_key(password=os.getenv("CTCL_XLSX_PASSWORD", "master123"))
                off.decrypt(buf)
            buf.seek(0)
            return pd.read_excel(buf, dtype=str), None
        except Exception:
            return None, str(e_pd)

def _pb_norm_header(s):
    """Collapse ANY run of whitespace (including embedded newlines from an
    Excel cell that visually wraps a header across two lines, e.g. "Client
    Margin\\nLimit") down to a single space, then strip. Plain .str.strip()
    only trims the ends — it does nothing for an internal line-break, which
    makes an exact-match header search silently fail even though the header
    text looks identical to the human eye. Used on both sides of the
    comparison (scanned row values AND required_columns) so either one being
    written with a literal newline still matches."""
    import re as _re
    return _re.sub(r"\s+", " ", str(s)).strip()

def _pb_read_xlsx_header_scan(path, required_columns, max_scan_rows=10):
    """Like _pb_read_xlsx_pandas, but for files (the MCX margin-limit export)
    whose real header row isn't reliably row 0 — scans the first
    max_scan_rows rows for one containing ALL required_columns, then re-reads
    using that row as the header. Same header-detection approach
    core/pipelines/ctcl_vs_nmass.py's own detect_header_excel() already uses
    for the real pipeline run — which is why a real run picks up MCX deposits
    correctly even for a file this lookup tool used to misread as header=0.
    Whitespace-normalized on both sides (see _pb_norm_header) so a header
    cell that wraps across two lines in Excel still matches."""
    import pandas as pd
    try:
        raw = pd.read_excel(path, header=None, dtype=str)
    except Exception as e:
        return None, str(e)
    req_norm = [_pb_norm_header(c) for c in required_columns]
    for i in range(min(max_scan_rows, len(raw))):
        row_norm = [_pb_norm_header(v) for v in raw.iloc[i].tolist()]
        if all(col in row_norm for col in req_norm):
            try:
                df = pd.read_excel(path, header=i, dtype=str)
                df.columns = [_pb_norm_header(c) for c in df.columns]
                return df, None
            except Exception as e:
                return None, str(e)
    return None, f"header row with {required_columns} not found in first {max_scan_rows} rows"

def _pb_sum_collateral_pl(df_pl, code_col, val_cols, codes):
    """Polars: filter rows for each code, sum val_cols. Returns {code: float}."""
    import polars as pl
    # Cast value columns to Float64
    exprs = []
    for c in val_cols:
        exprs.append(pl.col(c).str.replace_all(",", "").cast(pl.Float64, strict=False).fill_null(0).alias(c))
    exprs.append(pl.col(code_col).str.strip_chars().str.to_uppercase().alias("__code"))
    df2 = df_pl.with_columns(exprs)
    result = {}
    for code in codes:
        sub = df2.filter(pl.col("__code") == code)
        if sub.is_empty():
            continue
        tot = sum(sub[c].sum() or 0.0 for c in val_cols)
        result[code] = round(tot, 2)
    return result

# Optional progress hook for the client-data lookups. core/lookup_job.py (the separate worker process that runs a
# lookup) sets this; in the normal web server it stays None and nothing changes.
_PB_PROGRESS = None


def _pb_prog(i, n, label=""):
    cb = _PB_PROGRESS
    if cb:
        try:
            cb(i, n, str(label))
        except Exception:
            pass


def pb_client_lookup(pipe, client_codes, from_date="", to_date="", runs=None):
    """Scan this pipeline's backup folders for a client's values per date × run.

    Backup structure:
      <backup_root>/<DD-MM-YYYY>/<run_subfolder_NN>/files...
    Each run subfolder (named "01","02",...) contains its own fresh downloads of
    CM/FO/CO/NCDEX/MCX input files + FULL_FILE_CTCL_VS_NMASS_NN.xlsx output.
    Every run's input files are different (fresh exchange downloads), so we must
    read each run subfolder independently.

    Returns:
      inputs  → one row per (date, run, client_code): CM/FO/CO/MCX/NCDEX/TOTAL
      outputs → one row per (date, run, client_code): DEP_NMASS/DEP_CTCL/DIFF/REMARKS
    """
    import pandas as pd
    codes = [c.strip().upper() for c in (client_codes or []) if c and c.strip()]
    if not codes:
        return {"ok": False, "error": "Enter at least one client code."}

    def pdte(s):
        try: return datetime.datetime.strptime(s, "%Y-%m-%d").date()
        except Exception: return None
    d_from, d_to = pdte(from_date), pdte(to_date)

    KNOWN_PREFIXES = ("cm_clientlevelcollaterals", "fo_clientlevelcollaterals",
                      "co_clientlevelcollaterals", "clientneteffectivedeposits",
                      "full_file_ctcl_vs_nmass", "update limits", "risk_report", "mtf_risk_report")

    # ── 1. Walk backup folders, group files by (date, run_n) directory ────────
    # Structure: root/<date-folder>/<NN>/files  where NN = zero-padded run
    # number. The date-folder format is NOT the same for every pipeline (ctcl/
    # cli use DD-MM-YYYY, ms15 uses YYYY-MM-DD, probable uses "21 JUL 2026") —
    # _pb_parse_date_part() knows all three; using its own narrower regex here
    # (DD-MM-YYYY only) used to make every ms15 lookup silently return empty.
    by_dir = {}   # (date_obj, run_n) → list of (fn, fpp)

    for root in _pb_backup_roots(pipe):
        if not os.path.isdir(root): continue
        for dp, _dirs, files in os.walk(root):
            if not files: continue
            parts = dp.replace("\\", "/").split("/")
            cdate = None
            run_n = None
            found_date_idx = None
            for i, part in enumerate(parts):
                pdate = _pb_parse_date_part(part.strip())
                if pdate is not None:
                    cdate = pdate
                    found_date_idx = i
                elif found_date_idx is not None and run_n is None:
                    # The segment immediately after the date folder is the run subfolder
                    if re.fullmatch(r"\d{1,2}", part.strip()):
                        try: run_n = int(part.strip())
                        except Exception: pass
            if cdate is None: continue
            if d_from and cdate < d_from: continue
            if d_to   and cdate > d_to:   continue
            key = (cdate, run_n)   # run_n may be None for old flat structure
            by_dir.setdefault(key, []).extend((fn, os.path.join(dp, fn)) for fn in files)

    inputs, outputs, warnings = [], [], []

    # ── 2. Per-directory (= per run) extraction ───────────────────────────────
    # Sort: date descending, run ascending
    sorted_keys = sorted(by_dir.keys(), key=lambda k: (k[0], k[1] or 0), reverse=False)
    sorted_keys.sort(key=lambda k: k[0], reverse=True)

    # Limit to 200 (date, run) combinations to avoid timeout
    sorted_keys = sorted_keys[:200]

    # Runs the user did not tick are dropped BEFORE counting, so progress ("day X of N") only counts real work.
    sorted_keys = [k for k in sorted_keys if not (runs and k[1] is not None and k[1] not in runs)]
    for _pi, (cdate, run_n) in enumerate(sorted_keys):
        _pb_prog(_pi, len(sorted_keys), f"{cdate.strftime('%d-%m-%Y')}" + (f" · run {run_n}" if run_n else ""))
        if runs and run_n is not None and run_n not in runs: continue
        dl = cdate.strftime("%d-%m-%Y")
        group = by_dir[(cdate, run_n)]   # list of (fn, fpp)

        def _scan_collatearl(tag, pref):
            cand = [(fn, fpp) for fn, fpp in group
                    if fn.lower().startswith(pref) and fn.lower().endswith((".csv",".xlsx"))]
            if not cand: return {}
            fn, fpp = cand[0]   # take first match (only one per run subfolder)
            try:
                if fpp.lower().endswith(".csv"):
                    df_pl, err = _pb_read_csv_polars(fpp, codes)
                    if err or df_pl is None: return {}
                    cc  = _pb_col(df_pl, "Client Code")
                    v1  = _pb_col(df_pl, "Cash allocated (b)")
                    v2  = _pb_col(df_pl, "Cash-Others (c)", "Cash-others (c)")
                    v3  = _pb_col(df_pl, "Non-cash Securities (d)")
                    vc  = [c for c in [v1, v2, v3] if c]
                    if not cc or not vc: warnings.append(f"{dl} R{run_n} {fn}: cols missing"); return {}
                    return _pb_sum_collateral_pl(df_pl, cc, vc, codes)
                else:
                    tdf, err = _pb_read_xlsx_pandas(fpp)
                    if err or tdf is None: return {}
                    cc  = _pb_col(tdf, "Client Code")
                    vc  = [c for c in (_pb_col(tdf,"Cash allocated (b)"),
                                        _pb_col(tdf,"Cash-Others (c)","Cash-others (c)"),
                                        _pb_col(tdf,"Non-cash Securities (d)")) if c]
                    if not cc or not vc: return {}
                    cs  = tdf[cc].astype(str).str.strip().str.upper()
                    res = {}
                    for code in codes:
                        sub = tdf[cs == code]
                        if sub.empty: continue
                        # Strip thousands-separator commas before converting —
                        # same fix as the MCX block below (pd.to_numeric alone
                        # silently turns "1,234.56" into NaN -> 0, it doesn't
                        # know comma-grouped numbers).
                        res[code] = round(float(sum(
                            pd.to_numeric(sub[c].astype(str).str.replace(",", "", regex=False),
                                          errors="coerce").fillna(0).sum()
                            for c in vc
                        )), 2)
                    return res
            except Exception as e:
                warnings.append(f"{dl} R{run_n} {fn}: {e}"); return {}

        vals = {c: {} for c in codes}
        for tag, pref in [("CM","cm_clientlevelcollaterals"),
                           ("FO","fo_clientlevelcollaterals"),
                           ("CO","co_clientlevelcollaterals")]:
            for code, v in _scan_collatearl(tag, pref).items():
                vals[code][tag] = v

        # ── NCDEX ─────────────────────────────────────────────────────────────
        ncdex_cand = [(fn, fpp) for fn, fpp in group
                      if fn.lower().startswith("clientneteffectivedeposits")
                      and fn.lower().endswith((".csv",".xlsx"))]
        if ncdex_cand:
            fn, fpp = ncdex_cand[0]
            try:
                if fpp.lower().endswith(".csv"):
                    df_pl, err = _pb_read_csv_polars(fpp, codes)
                    if df_pl is not None and not err:
                        cc = _pb_col(df_pl, "Client/CP Code", "Client Code")
                        vc = _pb_col(df_pl, "Total Collateral (Cash and Non Cash)")
                        if cc and vc:
                            for code, v in _pb_sum_collateral_pl(df_pl, cc, [vc], codes).items():
                                vals[code]["NCDEX"] = v
                else:
                    tdf, err = _pb_read_xlsx_pandas(fpp)
                    if tdf is not None and not err:
                        cc = _pb_col(tdf, "Client/CP Code", "Client Code")
                        vc = _pb_col(tdf, "Total Collateral (Cash and Non Cash)")
                        if cc and vc:
                            cs = tdf[cc].astype(str).str.strip().str.upper()
                            for code in codes:
                                sub = tdf[cs == code]
                                if not sub.empty:
                                    # Strip thousands-separator commas — same
                                    # bug as MCX/CM/FO/CO below.
                                    vc_clean = sub[vc].astype(str).str.replace(",", "", regex=False)
                                    vals[code]["NCDEX"] = round(float(pd.to_numeric(vc_clean, errors="coerce").fillna(0).sum()), 2)
            except Exception as e:
                warnings.append(f"{dl} R{run_n} NCDEX: {e}")

        # ── MCX: any xlsx that has "Client Margin Limit" column ───────────────
        mcx_cand = [(fn, fpp) for fn, fpp in group
                    if fn.lower().endswith(".xlsx")
                    and not any(fn.lower().startswith(p) for p in KNOWN_PREFIXES)]
        for fn, fpp in mcx_cand:
            try:
                # Real header is just "Margin Limit" in some exports, not
                # "Client Margin Limit" — try both (see pb_cli_client_lookup's
                # same fix, confirmed against the actual file).
                tdf, err = _pb_read_xlsx_header_scan(fpp, ["Client Code", "Client Margin Limit"])
                if err or tdf is None:
                    tdf, err = _pb_read_xlsx_header_scan(fpp, ["Client Code", "Margin Limit"])
                if err or tdf is None: continue
                cc = _pb_col(tdf, "Client Code")
                ml = _pb_col(tdf, "Client Margin Limit", "Margin Limit")
                if not cc or not ml: continue
                cs = tdf[cc].astype(str).str.strip().str.upper()
                for code in codes:
                    sub = tdf[cs == code]
                    if not sub.empty:
                        # Values in this column are comma-formatted thousands
                        # ("910,642,663.92") — pd.to_numeric doesn't strip
                        # those on its own, it just silently coerces the whole
                        # thing to NaN -> 0 on errors="coerce". Real pipeline
                        # code (ctcl_vs_nmass.py's _num()) already strips
                        # commas before casting; this path never did, which is
                        # why MCX came back 0 here even for a client whose
                        # real Client Margin Limit was in the hundreds of
                        # millions — found and matched correctly, just parsed
                        # as zero afterward.
                        ml_clean = sub[ml].astype(str).str.replace(",", "", regex=False)
                        vals[code]["MCX"] = round(float(pd.to_numeric(ml_clean, errors="coerce").fillna(0).sum()), 2)
                break
            except Exception: continue

        # ── Append input row per (date, run, client_code) ─────────────────────
        for code in codes:
            v = vals[code]
            if not v: continue
            comps = [v.get("CM",0), v.get("FO",0), v.get("CO",0), v.get("NCDEX",0), v.get("MCX",0)]
            inputs.append({
                "date": dl, "run": run_n, "client_code": code,
                "CM": v.get("CM",0), "FO": v.get("FO",0), "CO": v.get("CO",0),
                "NCDEX": v.get("NCDEX",0), "MCX": v.get("MCX",0),
                "TOTAL": round(sum(comps), 2),
            })

        # ── Output row for this run ─────────────────────────────────────────
        # pipe-aware: CTCL vs NMASS writes FULL_FILE_CTCL_VS_NMASS_<NN>.xlsx
        # (one copy per run, filename carries the run number); Margin Shortage
        # (15-min, pipe="ms15") writes a single Shortage_vs_CC_Deposit.xlsx into
        # EACH run's own backup subfolder (name never changes - the folder itself
        # carries the run number, same convention as the CM/FO/CO input files).
        if pipe == "ms15":
            ff_cand = [(fn, fpp) for fn, fpp in group
                       if fn.lower() == "shortage_vs_cc_deposit.xlsx"]
            for fn, fpp in ff_cand:
                ff_run = run_n
                if runs and ff_run is not None and ff_run not in runs: continue
                try:
                    tdf, err = _pb_read_xlsx_pandas(fpp)
                    if err or tdf is None: continue
                    code_col = _pb_col(tdf, "ACCOUNTID", "CLIENT CODE", "Client Code")
                    dep_col  = _pb_col(tdf, "DEPOSIT NMASS")
                    tot_col  = _pb_col(tdf, "TOTAL MARGIN")
                    diff_col = _pb_col(tdf, "DIFFERENCE(F-G)", "DIFFERENCE", "Difference")
                    warn_col = _pb_col(tdf, "MARGIN WARNING %")
                    rem_col  = _pb_col(tdf, "REMARKS", "Remarks")
                    zone_col = _pb_col(tdf, "Zone", "ZONE")
                    if not code_col: continue
                    cs2 = tdf[code_col].astype(str).str.strip().str.upper()
                    for code in codes:
                        sub = tdf[cs2 == code]
                        if sub.empty: continue
                        r = sub.iloc[0]
                        def _g(col, _r=r):
                            if not col: return 0
                            # These are pipeline-written output files, so the
                            # cell is normally already a clean number — but
                            # strip commas defensively too, same reasoning as
                            # the raw-file reads above, in case a value ever
                            # round-trips through a comma-formatted string.
                            raw_v = _r[col]
                            if isinstance(raw_v, str):
                                raw_v = raw_v.replace(",", "")
                            v = pd.to_numeric(raw_v, errors="coerce")
                            return 0 if pd.isna(v) else float(v)
                        outputs.append({
                            "date": dl, "run": ff_run, "client_code": code,
                            "zone":        str(r[zone_col]).strip() if zone_col else "",
                            "DEP_NMASS":   _g(dep_col),
                            "TOTAL_MARGIN":_g(tot_col),
                            "DIFF":        _g(diff_col),
                            "WARNING_PCT": _g(warn_col),
                            "REMARKS":     str(r[rem_col]).strip() if rem_col else "",
                        })
                except Exception as e:
                    warnings.append(f"{fn}: {e}")
        else:
            ff_cand = [(fn, fpp) for fn, fpp in group
                       if re.match(r"FULL_FILE_CTCL_VS_NMASS_\d+\.xlsx", fn, re.I)]
            for fn, fpp in ff_cand:
                mm2 = re.match(r"FULL_FILE_CTCL_VS_NMASS_0*(\d+)\.xlsx", fn, re.I)
                ff_run = int(mm2.group(1)) if mm2 else run_n
                if runs and ff_run is not None and ff_run not in runs: continue
                try:
                    tdf, err = _pb_read_xlsx_pandas(fpp)
                    if err or tdf is None: continue
                    code_col = _pb_col(tdf, "ACCOUNTID", "CLIENT CODE", "Client Code")
                    dep_col  = _pb_col(tdf, "DEPOSIT NMASS", "Deposit NMASS")
                    ctcl_col = _pb_col(tdf, "DEPOSIT CTCL", "Deposit CTCL")
                    diff_col = _pb_col(tdf, "DIFFERENCE(G-F)", "DIFFERENCE", "Difference")
                    rem_col  = _pb_col(tdf, "REMARKS", "Remarks")
                    zone_col = _pb_col(tdf, "Zone", "ZONE")
                    if not code_col: continue
                    cs2 = tdf[code_col].astype(str).str.strip().str.upper()
                    for code in codes:
                        sub = tdf[cs2 == code]
                        if sub.empty: continue
                        r = sub.iloc[0]
                        def _g(col, _r=r):
                            if not col: return 0
                            # These are pipeline-written output files, so the
                            # cell is normally already a clean number — but
                            # strip commas defensively too, same reasoning as
                            # the raw-file reads above, in case a value ever
                            # round-trips through a comma-formatted string.
                            raw_v = _r[col]
                            if isinstance(raw_v, str):
                                raw_v = raw_v.replace(",", "")
                            v = pd.to_numeric(raw_v, errors="coerce")
                            return 0 if pd.isna(v) else float(v)
                        outputs.append({
                            "date": dl, "run": ff_run, "client_code": code,
                            "zone":     str(r[zone_col]).strip() if zone_col else "",
                            "DEP_NMASS": _g(dep_col), "DEP_CTCL": _g(ctcl_col),
                            "DIFF":      _g(diff_col),
                            "REMARKS":   str(r[rem_col]).strip() if rem_col else "",
                        })
                except Exception as e:
                    warnings.append(f"{fn}: {e}")

    # Sort: date desc, run asc
    def _sort_key(x):
        try: d = datetime.datetime.strptime(x["date"], "%d-%m-%Y")
        except Exception: d = datetime.datetime.min
        return (-d.toordinal(), x.get("run") or 0)

    inputs.sort(key=_sort_key)
    outputs.sort(key=_sort_key)
    return {"ok": True, "codes": codes, "inputs": inputs, "outputs": outputs, "warnings": warnings[:20]}


def pb_cli_client_lookup(client_codes, from_date="", to_date="", slots=None):
    """CLI Prudential Limit's own "Client data" lookup — separate from
    pb_client_lookup() on purpose: that function's run-subfolder detection
    only recognizes a bare 1-2 digit folder name ("01","02",...), and CLI's
    backup tree uses the word-named Morning/Afternoon/Evening slot folders
    instead (see _cli_time_slot() in core/pipelines/cli_upload.py) — extending
    the shared regex risked quietly changing what counts as a "run" for the
    already-relied-upon CTCL/MS15 lookup, so this is its own standalone walk.

    Backup structure: <backup_root>/<DD-MM-YYYY>/<Morning|Afternoon|Evening>/files...

    Unlike pb_client_lookup, this ALSO reports the raw per-component columns
    that were summed into each of CM/FO/CO (Cash allocated (b), Cash-Others
    (c), Non-cash Securities (d)) individually, per the user's request to see
    "which columns were used to calculate all these singly" — not just the
    pre-summed bucket total.

    Returns:
      inputs  → one row per (date, slot, client_code): CM/FO/CO/MCX/NCDEX/TOTAL
                plus CM_B/CM_C/CM_D, FO_B/FO_C/FO_D, CO_B/CO_C/CO_D raw components
      outputs → always [] — CLI has no comparable "deposit vs uploaded" output
                file the way CTCL vs NMASS / Margin Shortage do, so there is
                nothing to put in a second table.
    """
    import pandas as pd
    codes = [c.strip().upper() for c in (client_codes or []) if c and c.strip()]
    if not codes:
        return {"ok": False, "error": "Enter at least one client code."}

    def pdte(s):
        try: return datetime.datetime.strptime(s, "%Y-%m-%d").date()
        except Exception: return None
    d_from, d_to = pdte(from_date), pdte(to_date)

    slot_filter = {s for s in (slots or []) if s in ("Morning", "Afternoon", "Evening")}

    KNOWN_PREFIXES = ("cm_clientlevelcollaterals", "fo_clientlevelcollaterals",
                      "co_clientlevelcollaterals", "clientneteffectivedeposits",
                      "allocation_detail", "cli_prudential_limit")

    # ── 1. Walk CLI's backup folders, group files by (date, slot) directory ──
    inputs, warnings = [], []
    by_dir = {}   # (date_obj, slot_str) → list of (fn, fpp)
    for root in _pb_backup_roots("cli"):
        if not os.path.isdir(root): continue
        for dp, _dirs, files in os.walk(root):
            if not files: continue
            parts = dp.replace("\\", "/").split("/")
            cdate = None
            slot = None
            found_date_idx = None
            for i, part in enumerate(parts):
                pdate = _pb_parse_date_part(part.strip())
                if pdate is not None:
                    cdate = pdate
                    found_date_idx = i
                elif found_date_idx is not None and slot is None:
                    p = part.strip()
                    if p in ("Morning", "Afternoon", "Evening"):
                        slot = p
                    else:
                        slot = _pb_cli_slot_from_dirname(p) or slot
            if cdate is None: continue
            if d_from and cdate < d_from: continue
            if d_to   and cdate > d_to:   continue
            key = (cdate, slot)   # slot may be None for old flat structure
            by_dir.setdefault(key, []).extend((fn, os.path.join(dp, fn)) for fn in files)

    # ── 1b. Postgres supplement — a file archived to Postgres (a live run on
    # ANY machine, or a Backfill-to-DB) but with no local disk copy on THIS
    # machine must still be found, otherwise a fresh/secondary machine's
    # lookup always comes back empty even though the data genuinely exists
    # (same reasoning as pb_list_backups's own Postgres supplement a bit
    # further up this file). Downloads just the files this lookup actually
    # cares about (KNOWN_PREFIXES) into a throwaway temp folder so the
    # existing disk-based scanning code below can read them exactly like a
    # normal file, rather than duplicating the CSV/txt parsing per-source.
    _tmp_dir = None
    try:
        if os.getenv("USE_POSTGRES", "false").strip().lower() == "true":
            from core.db_postgres import list_run_files as _cli_db_list, get_run_file as _cli_db_get
            seen = {(cdate, slot, fn.lower())
                    for (cdate, slot), items in by_dir.items() for fn, _fpp in items}
            db_rows = _cli_db_list(pipeline="cli", from_date=(from_date or None),
                                    to_date=(to_date or None), days=36500)
            wanted = [r for r in db_rows if any(
                (r.get("filename") or "").lower().startswith(p) for p in KNOWN_PREFIXES)]
            if wanted:
                import tempfile
                _tmp_dir = tempfile.mkdtemp(prefix="cli_lookup_")
                for r in wanted:
                    fn = r.get("filename") or ""
                    fdate = r.get("run_date")
                    if not fn or not fdate: continue
                    if d_from and fdate < d_from: continue
                    if d_to   and fdate > d_to:   continue
                    tag = str(r.get("tag") or "")
                    m = re.search(r"/(Morning|Afternoon|Evening)$", tag)
                    rslot = m.group(1) if m else None
                    dedupe_key = (fdate, rslot, fn.lower())
                    if dedupe_key in seen: continue   # already have this file on disk
                    got = _cli_db_get(r["id"])
                    if not got: continue
                    _, content = got
                    dest_dir = os.path.join(_tmp_dir, fdate.isoformat(), rslot or "_")
                    os.makedirs(dest_dir, exist_ok=True)
                    fpp = os.path.join(dest_dir, fn)
                    try:
                        with open(fpp, "wb") as f: f.write(content)
                    except Exception:
                        continue
                    by_dir.setdefault((fdate, rslot), []).append((fn, fpp))
                    seen.add(dedupe_key)
    except Exception as e:
        warnings.append(f"Postgres lookup skipped (non-fatal): {e}")

    sorted_keys = sorted(by_dir.keys(), key=lambda k: (k[0], k[1] or ""), reverse=False)
    sorted_keys.sort(key=lambda k: k[0], reverse=True)
    sorted_keys = sorted_keys[:200]

    sorted_keys = [k for k in sorted_keys if not (slot_filter and k[1] is not None and k[1] not in slot_filter)]
    for _pi, (cdate, slot) in enumerate(sorted_keys):
        _pb_prog(_pi, len(sorted_keys), f"{cdate.strftime('%d-%m-%Y')}" + (f" · {slot}" if slot else ""))
        if slot_filter and slot is not None and slot not in slot_filter: continue
        dl = cdate.strftime("%d-%m-%Y")
        group = by_dir[(cdate, slot)]

        def _scan_collateral_detail(pref):
            """Returns {code: {"B":val,"C":val,"D":val}} — the three raw
            component columns individually, not just their sum, per client."""
            cand = [(fn, fpp) for fn, fpp in group
                    if fn.lower().startswith(pref) and fn.lower().endswith((".csv", ".txt", ".xlsx"))]
            if not cand: return {}
            fn, fpp = cand[0]
            try:
                if fpp.lower().endswith(".xlsx"):
                    tdf, err = _pb_read_xlsx_pandas(fpp)
                    if err or tdf is None: return {}
                else:
                    try:
                        tdf = pd.read_csv(fpp, dtype=str)
                    except Exception:
                        tdf = pd.read_csv(fpp, dtype=str, encoding="latin-1")
                cc = _pb_col(tdf, "Client Code")
                b_col = _pb_col(tdf, "Cash allocated (b)")
                c_col = _pb_col(tdf, "Cash-Others (c)", "Cash-others (c)")
                d_col = _pb_col(tdf, "Non-cash Securities (d)")
                if not cc: warnings.append(f"{dl} {slot or ''} {fn}: cols missing"); return {}
                cs = tdf[cc].astype(str).str.strip().str.upper()
                res = {}
                for code in codes:
                    sub = tdf[cs == code]
                    if sub.empty: continue
                    def _s(col):
                        if not col: return 0.0
                        return round(float(pd.to_numeric(
                            sub[col].astype(str).str.replace(",", "", regex=False),
                            errors="coerce").fillna(0).sum()), 2)
                    res[code] = {"B": _s(b_col), "C": _s(c_col), "D": _s(d_col)}
                return res
            except Exception as e:
                warnings.append(f"{dl} {slot or ''} {fn}: {e}"); return {}

        detail = {c: {} for c in codes}
        for tag, pref in [("CM", "cm_clientlevelcollaterals"),
                           ("FO", "fo_clientlevelcollaterals"),
                           ("CO", "co_clientlevelcollaterals")]:
            for code, bcd in _scan_collateral_detail(pref).items():
                detail[code][tag] = bcd

        vals = {c: {} for c in codes}

        # ── NCDEX ─────────────────────────────────────────────────────────────
        ncdex_cand = [(fn, fpp) for fn, fpp in group
                      if fn.lower().startswith("clientneteffectivedeposits")
                      and fn.lower().endswith((".csv", ".txt", ".xlsx"))]
        if ncdex_cand:
            fn, fpp = ncdex_cand[0]
            try:
                if fpp.lower().endswith(".xlsx"):
                    tdf, err = _pb_read_xlsx_pandas(fpp)
                    if err: tdf = None
                else:
                    try:
                        tdf = pd.read_csv(fpp, dtype=str)
                    except Exception:
                        tdf = pd.read_csv(fpp, dtype=str, encoding="latin-1")
                if tdf is not None:
                    cc = _pb_col(tdf, "Client/CP Code", "Client Code")
                    vc = _pb_col(tdf, "Total Collateral (Cash and Non Cash)")
                    # Bifurcation: the NCDEX file's own Total Collateral is
                    # itself "Total Cash Component" + "Total Non Cash" — same
                    # idea as CM/FO/CO's b+c+d, just under NCDEX's own column
                    # names (per user request, show the real column names).
                    cash_col = _pb_col(tdf, "Total Cash Component")
                    noncash_col = _pb_col(tdf, "Total Non Cash")
                    if cc and vc:
                        cs = tdf[cc].astype(str).str.strip().str.upper()
                        for code in codes:
                            sub = tdf[cs == code]
                            if not sub.empty:
                                vc_clean = sub[vc].astype(str).str.replace(",", "", regex=False)
                                vals[code]["NCDEX"] = round(float(pd.to_numeric(vc_clean, errors="coerce").fillna(0).sum()), 2)
                                if cash_col:
                                    cc_clean = sub[cash_col].astype(str).str.replace(",", "", regex=False)
                                    vals[code]["NCDEX_CASH"] = round(float(pd.to_numeric(cc_clean, errors="coerce").fillna(0).sum()), 2)
                                if noncash_col:
                                    nc_clean = sub[noncash_col].astype(str).str.replace(",", "", regex=False)
                                    vals[code]["NCDEX_NONCASH"] = round(float(pd.to_numeric(nc_clean, errors="coerce").fillna(0).sum()), 2)
            except Exception as e:
                warnings.append(f"{dl} {slot or ''} NCDEX: {e}")

        # ── MCX: any xlsx with a "Client Margin Limit" column ─────────────────
        mcx_cand = [(fn, fpp) for fn, fpp in group
                    if fn.lower().endswith(".xlsx")
                    and not any(fn.lower().startswith(p) for p in KNOWN_PREFIXES)]
        for fn, fpp in mcx_cand:
            try:
                # The real export's margin-limit header is just "Margin
                # Limit" (confirmed from the actual file), not "Client
                # Margin Limit" as some other pipelines' copy of this file
                # has it — try both, "Client Margin Limit" first since
                # that's what margin_shortage_15min.py's own reader expects.
                tdf, err = _pb_read_xlsx_header_scan(fpp, ["Client Code", "Client Margin Limit"])
                if err or tdf is None:
                    tdf, err = _pb_read_xlsx_header_scan(fpp, ["Client Code", "Margin Limit"])
                if err or tdf is None: continue
                cc = _pb_col(tdf, "Client Code")
                ml = _pb_col(tdf, "Client Margin Limit", "Margin Limit")
                # Bifurcation: the same RSK383 export also carries "Margin
                # Utilization" alongside "Client Margin Limit" (see
                # margin_shortage_15min.py's find_mcx_file()) — included when
                # the column is present so MCX gets a breakdown too.
                mu = _pb_col(tdf, "Margin Utilization")
                if not cc or not ml: continue
                cs = tdf[cc].astype(str).str.strip().str.upper()
                for code in codes:
                    sub = tdf[cs == code]
                    if not sub.empty:
                        ml_clean = sub[ml].astype(str).str.replace(",", "", regex=False)
                        vals[code]["MCX"] = round(float(pd.to_numeric(ml_clean, errors="coerce").fillna(0).sum()), 2)
                        if mu:
                            mu_clean = sub[mu].astype(str).str.replace(",", "", regex=False)
                            vals[code]["MCX_UTIL"] = round(float(pd.to_numeric(mu_clean, errors="coerce").fillna(0).sum()), 2)
                break
            except Exception: continue

        # ── Append one input row per (date, slot, client_code) ────────────────
        for code in codes:
            d = detail.get(code, {})
            v = vals.get(code, {})
            cm_b, cm_c, cm_d = d.get("CM", {}).get("B", 0), d.get("CM", {}).get("C", 0), d.get("CM", {}).get("D", 0)
            fo_b, fo_c, fo_d = d.get("FO", {}).get("B", 0), d.get("FO", {}).get("C", 0), d.get("FO", {}).get("D", 0)
            co_b, co_c, co_d = d.get("CO", {}).get("B", 0), d.get("CO", {}).get("C", 0), d.get("CO", {}).get("D", 0)
            cm = round(cm_b + cm_c + cm_d, 2)
            fo = round(fo_b + fo_c + fo_d, 2)
            co = round(co_b + co_c + co_d, 2)
            ncdex = v.get("NCDEX", 0)
            mcx = v.get("MCX", 0)
            ncdex_cash, ncdex_noncash = v.get("NCDEX_CASH", 0), v.get("NCDEX_NONCASH", 0)
            mcx_util = v.get("MCX_UTIL", 0)
            if not d and not v: continue   # nothing found for this code in this slot at all
            inputs.append({
                "date": dl, "slot": slot, "client_code": code,
                "CM": cm, "CM_B": cm_b, "CM_C": cm_c, "CM_D": cm_d,
                "FO": fo, "FO_B": fo_b, "FO_C": fo_c, "FO_D": fo_d,
                "CO": co, "CO_B": co_b, "CO_C": co_c, "CO_D": co_d,
                "NCDEX": ncdex, "NCDEX_CASH": ncdex_cash, "NCDEX_NONCASH": ncdex_noncash,
                "MCX": mcx, "MCX_UTIL": mcx_util,
                "TOTAL": round(cm + fo + co + ncdex + mcx, 2),
            })

    _SLOT_ORDER = {"Morning": 0, "Afternoon": 1, "Evening": 2}
    def _sort_key(x):
        try: dd = datetime.datetime.strptime(x["date"], "%d-%m-%Y")
        except Exception: dd = datetime.datetime.min
        return (-dd.toordinal(), _SLOT_ORDER.get(x.get("slot"), 9))
    inputs.sort(key=_sort_key)
    if _tmp_dir:
        shutil.rmtree(_tmp_dir, ignore_errors=True)
    return {"ok": True, "codes": codes, "inputs": inputs, "outputs": [], "warnings": warnings[:20]}


def pb_copy_backups(pipe, paths, dest):
    if not dest or not str(dest).strip():
        return {"ok": False, "error": "No destination folder given."}
    dest = str(dest).strip()
    roots = [os.path.abspath(r) for r in _pb_backup_roots(pipe)]
    try:
        os.makedirs(dest, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"Could not create destination: {e}"}
    logdir = os.path.join(LOG_BASE, PIPELINES.get(pipe, {}).get("logdir", pipe))
    os.makedirs(logdir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logp = os.path.join(logdir, f"RESTORE__{ts}.log")
    copied, failed, log_lines = [], [], [f"# RESTORE {pipe}  dest={dest}  time={ts}"]
    for pth in paths:
        if str(pth or "").startswith("db:"):
            # Postgres-sourced entry — no disk file to copy from, write the
            # stored bytes straight to the destination instead.
            got = pb_backup_db_file(pth)
            if not got:
                failed.append(f"{pth}: not found in Postgres")
                log_lines.append(f"FAIL   {pth}: not found in Postgres")
                continue
            fname, content = got
            try:
                with open(os.path.join(dest, fname), "wb") as f:
                    f.write(content)
                copied.append(fname)
                log_lines.append(f"OK     {pth} (from Postgres)")
            except Exception as e:
                failed.append(f"{fname}: {e}")
                log_lines.append(f"FAIL   {pth}: {e}")
            continue
        ap = os.path.abspath(pth)
        base = os.path.basename(ap)
        if not any(ap.startswith(r) for r in roots):
            failed.append(f"{base}: outside backup folder")
            log_lines.append(f"REJECT {pth}")
            continue
        try:
            shutil.copy2(ap, os.path.join(dest, base))
            copied.append(base)
            log_lines.append(f"OK     {pth}")
        except Exception as e:
            failed.append(f"{base}: {e}")
            log_lines.append(f"FAIL   {pth}: {e}")
    try:
        open(logp, "w", encoding="utf-8").write("\n".join(log_lines) + "\n")
    except Exception:
        pass
    return {"ok": True, "copied": copied, "failed": failed}
