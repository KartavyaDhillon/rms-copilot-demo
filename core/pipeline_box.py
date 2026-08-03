"""
core/pipeline_box.py — REUSABLE dashboard runner for any pipeline.

Runs a pipeline's existing function as a subprocess (no logic change), captures the
FULL output to a per-process log file, surfaces ONLY email/WhatsApp status to the UI,
and is killable. Add a pipeline by adding one entry to PIPELINES — nothing else.
"""
import os, re, sys, glob, queue, threading, subprocess, datetime, shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_BASE = os.path.join(REPO, "logs")

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
                            "Payout Check": ["PAYOUT_CHECK_TO", "PAYOUT_CHECK_CC"]},
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
                       "note": "eclear.mcxccl.com login used by MCX_90_PERCENT_FILE_API.py"}},
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
                 ["MCX 90% file (xlsx)", "*.xlsx", "<random>-<YYYYMMDDHHMMSS>.xlsx"],
                 ["CTCL output file", "FULL_FILE_CTCL_VS_NMASS_*.xlsx", "FULL_FILE_CTCL_VS_NMASS_<NN>.xlsx"],
             ],
             # Phase transition markers — emitted as {type:'phase', name:'...'} SSE events
             "phase_patterns": [
                 (r"CTCL EMAILS COMPLETED",            {"type": "phase", "name": "ctcl_emails_done"}),
                 (r"RUN 1 DETECTED.*STARTING TOP20",   {"type": "phase", "name": "top20"}),
                 (r"STARTING PAYOUT DOWNLOAD",          {"type": "phase", "name": "payout"}),
                 (r"PAYOUT CHECKING COMPLETED",         {"type": "phase", "name": "payout_done"}),
             ]},
    # Client Prudential Limit — runs twice a day (morning/evening). Two independent
    # steps (Download, Upload) rather than CTCL's single chained pipeline; the
    # frontend runs both in sequence for the slot tiles' Run button (cliRunFull())
    # and also exposes them as standalone buttons for manual re-run of just one step.
    # See core/pipelines/cli_download.py + core/pipelines/cli_upload.py.
    "cli": {"name": "Client Prudential Limit", "logdir": "cli", "runs": 2,
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
                      "note": "202.58.232.124 — same FTP server other downloaders use"}},
            "upload_dir_env": "CLI_FOLDER", "upload_default": r"D:\CLI_PRUDENTIAL_LIMIT",
            # secondary upload target — RISK_REPORT lives in the shared common
            # folder, same as CTCL vs NMASS's copy of it (same file, same source).
            "common_dir_env": "SA_COMMON_DIR", "common_default": r"C:\COMMON FILES",
            "common_globs": ["RISK_REPORT_*.csv"], "common_label": "COMMON FILES",
            "backup_root_env": "CLI_FOLDER", "backup_subdir": "BACKUPFILES AUTOMATED",
            "file_types": [
                ["Allocation Detail", "Allocation_Detail_*.csv", "Allocation_Detail_<DDMMYYYY>.csv"],
                ["Intermediate", "Intermediate.csv", "Intermediate.csv"],
                ["CLI Prudential Limit", "CLI_PRUDENTIAL_LIMIT.txt", "CLI_PRUDENTIAL_LIMIT.txt"],
                ["CM Client Collaterals", "CM_ClientLevelCollaterals_*.csv", "CM_ClientLevelCollaterals_<date>.csv"],
                ["FO Client Collaterals", "FO_ClientLevelCollaterals_*.csv", "FO_ClientLevelCollaterals_<date>.csv"],
                ["CO Client Collaterals", "CO_ClientLevelCollaterals_*.csv", "CO_ClientLevelCollaterals_<date>.csv"],
                ["Client Net Effective Deposits", "ClientNetEffectiveDeposits_*.csv", "ClientNetEffectiveDeposits_<date>.csv"],
                ["MCX Web Allocation/Deallocation", "MCX_WebAllocationDeallocation.csv", "MCX_WebAllocationDeallocation.csv"],
                ["RISK_REPORT", "RISK_REPORT_*.csv", "RISK_REPORT_<date>.csv"],
                ["Kambala", "Kambala_Omny_Depo_Adhoc_All_level.txt_*.txt", "Kambala_Omny_Depo_Adhoc_All_level.txt_<DDMMYYYY>.txt"],
            ]},
    # Margin Shortage (15-min) — runs 11x/day, ported from 15minutesfinal_whatsapp.PY.
    # (Was 9; 16:30 and 16:50 added when the desk's shift extended. The night
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
             "func": "run_margin_shortage_15min", "logdir": "ms15", "runs": 11,
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
                                "Final Summary": ["PROBABLE_FINAL_TO", "PROBABLE_FINAL_CC"],
                                "Branch/Region mail-list override":
                                    ["PROBABLE_MAILLIST_OVERRIDE_TO", "PROBABLE_MAILLIST_OVERRIDE_CC"]},
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
                     ["Probable Fullfile (manual daily upload)", "Probable_Fullfile_*.xlsx", "Probable_Fullfile_<DDMMYYYY>.xlsx"],
                     ["CM Client Collaterals", "CM_ClientLevelCollaterals_*.csv,CM_ClientLevelCollaterals_*.xlsx", "CM_ClientLevelCollaterals_<date>.csv"],
                     ["FO Client Collaterals", "FO_ClientLevelCollaterals_*.csv,FO_ClientLevelCollaterals_*.xlsx", "FO_ClientLevelCollaterals_<date>.csv"],
                     ["CO Client Collaterals", "CO_ClientLevelCollaterals_*.csv,CO_ClientLevelCollaterals_*.xlsx", "CO_ClientLevelCollaterals_<date>.csv"],
                     ["Client Net Effective Deposits", "ClientNetEffectiveDeposits_*.csv,ClientNetEffectiveDeposits_*.xlsx", "ClientNetEffectiveDeposits_<date>.csv"],
                     ["MCX RSK383 (xlsx)", "*.xlsx", "MCX_DEPOSIT_<HH.MM>.xlsx"],
                     ["MCX RSK335 / 90% file", "Clientwise_mcx_margin*.xlsx", "Clientwise_mcx_margin.xlsx"],
                     ["Delivery Margin", "F_DLMG02_06436*.csv", "F_DLMG02_06436_<DDMMYYYY>.csv"],
                     ["Reconciled output", "Probable_Fullfile_K_*.xlsx", "Probable_Fullfile_K_<DDMMYYYY>.xlsx"],
                     ["Margin comparison output", "All_Exchange_Margin_Comparison_*.xlsx", "All_Exchange_Margin_Comparison_<DDMMYYYY>.xlsx"],
                     ["WhatsApp log", "WhatsApp_Log.xlsx", "WhatsApp_Log.xlsx"],
                     # Zone shortage files + branch/region mail-list working files
                     # (probable_alerts.py's _step_7_zone_emails / _step_10_dynamic_mail /
                     # _step_11_validation) — previously missing from this list entirely,
                     # so they were never selectable/labeled in the Backup Files type filter
                     # even though they were always physically present in the SHORTAGE backup.
                     ["Zone shortage — Mumbai", "ZOMUMB_ZONE DATA.xlsx", "ZOMUMB_ZONE DATA.xlsx"],
                     ["Zone shortage — Chandigarh", "ZOCHND_ZONE DATA.xlsx", "ZOCHND_ZONE DATA.xlsx"],
                     ["Zone shortage — Delhi", "DELHI_ZONE DATA.xlsx", "DELHI_ZONE DATA.xlsx"],
                     ["Zone shortage — Ludhiana", "LUDH_ZONE DATA.xlsx", "LUDH_ZONE DATA.xlsx"],
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
}

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


def stream_pipeline(key, run_no=None, extra_env=None, by=None, ip=None):
    p = PIPELINES.get(key)
    pb = _PB.get(key)
    if pb is None:
        yield {"type": "error", "msg": f"Unknown pipeline '{key}'"}
        return
    lock_key = f"px_{key}"

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
            _who_line = ""
            try:
                from core import lock_manager as _lmw
                _wm = _lmw._META.get(lock_key, {})
                _who_line = f"# by={_wm.get('by') or 'unknown user'} ip={_wm.get('ip') or '-'}\n"
            except Exception:
                pass
            q.put({"type": "log", "msg": f"Starting {p['name']}…"})
            # For CTCL, optionally pass run_number_override so retries use the correct run slot
            if run_no and key == "ctcl":
                code = f"from {p['module']} import {p['func']} as _f; _f(run_number_override={int(run_no)})"
            else:
                code = f"from {p['module']} import {p['func']} as _f; _f()"
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                   "PYTHONUNBUFFERED": "1", "PYTHONPATH": REPO, **(extra_env or {})}
            saw_fail = False; last_exc = ""
            with open(logp, "w", encoding="utf-8", errors="replace") as lf:
                lf.write(f"# {p['name']}  start={ts}\n")
                if _who_line:
                    lf.write(_who_line)
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
                    if _FAIL.search(ls): saw_fail = True
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
            elif rc != 0 or saw_fail:
                _outcome = "failed"
                q.put({"type": "done", "ok": False, "log": os.path.basename(logp), "err": last_exc})
            else:
                _outcome = "completed"
                q.put({"type": "done", "ok": True, "log": os.path.basename(logp), "err": ""})
        except Exception as e:
            q.put({"type": "error", "msg": str(e)})
        finally:
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
            saw_fail = False
            with open(logp, "w", encoding="utf-8", errors="replace") as lf:
                lf.write(f"# {logname_prefix}  start={ts}\n")
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
                    if _FAIL.search(line): saw_fail = True
                    ev = _status(line)
                    if ev: q.put(ev)
                rc = proc.wait(); pb["proc"] = None
            if pb["cancel"]:
                _outcome = "stopped"
                q.put({"type": "stopped", "msg": "⛔ Stopped by user.", "log": os.path.basename(logp)})
            elif rc != 0 or saw_fail:
                _outcome = "failed"
                q.put({"type": "done", "ok": False, "log": os.path.basename(logp)})
            else:
                _outcome = "completed"
                q.put({"type": "done", "ok": True, "log": os.path.basename(logp)})
        except Exception as e:
            q.put({"type": "error", "msg": str(e)})
        finally:
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
}

def _log_friendly_label(key, filename):
    for prefix, label in _LOG_LABELS.get(key, []):
        if filename.startswith(prefix + "__"):
            return label
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

def _read_counter(p):
    base = os.getenv(p.get("state_dir_env", ""), p.get("state_default", ""))
    fp = os.path.join(base, p.get("state_file", "run_counter.txt"))
    try:
        content = open(fp, encoding="utf-8", errors="replace").read().strip()
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if "|" in content:
            d, n = content.split("|", 1)
            return int(n) if d.strip() == today else 0
        ints = re.findall(r"\d+", content)
        return int(ints[-1]) if ints else 0
    except Exception:
        return 0

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
            "server_busy_since": pb.get("started_at")}

def pb_get_config(pipe):
    p = PIPELINES.get(pipe, {}); g = os.getenv
    emails = {lbl: {"to": g(tk, ""), "cc": g(ck, ""), "to_key": tk, "cc_key": ck}
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
    return {"ok": True, "saved": len(upd)}


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

def _pb_push_allocation_to_ftp(local_path, filename):
    """Push a manually-uploaded Allocation_Detail file straight to the same FTP folder
    Client Prudential Limit's Download step pulls it from — auto-creates the folder
    path if it doesn't exist yet. Means nobody has to open an SFTP client by hand."""
    from ftplib import FTP
    ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
    ftp_user = os.getenv("FTP_USER", "TRADING")
    ftp_pass = os.getenv("FTP_PASS", "")
    remote_folder = "/TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION"
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
                "examples": [x for x in spec["examples"] if "risk_report" in x.lower()] or spec["examples"]}
    else:
        dest = os.getenv(p.get("upload_dir_env", ""), p.get("upload_default", ""))
        # RISK_REPORT belongs in the common folder — reject it here so it can't
        # land in the wrong place.
        globs = [g for g in spec["globs"] if g.lower() not in common_globs]
        spec = {**spec,
                "examples": [x for x in spec["examples"] if "risk_report" not in x.lower()]}
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
    return [{"label": r[0], "globs": r[1]} for r in PIPELINES.get(pipe, {}).get("file_types", [])]

def _pb_backup_roots(pipe):
    p = PIPELINES.get(pipe, {})
    base = os.getenv(p.get("backup_root_env", ""), p.get("upload_default", ""))
    return [os.path.join(base, p.get("backup_subdir", "BACKUPFILES AUTOMATED"))]

def _pb_parse_date_part(part):
    """Recognize a date encoded in one path segment of a backup folder tree.
    Two genuinely different conventions are in use: ctcl/ms15/cli/sa all use
    DD-MM-YYYY (e.g. "21-07-2026"), but probable's own backup steps
    (probable_reconcile.py/probable_alerts.py) format the date folder as
    "%d %b %Y" upper-cased (e.g. "21 JUL 2026") — not a bug in probable's own
    code (that's the reference script's own convention, left untouched per
    "don't change calculation/threshold logic"), just something this GENERIC
    lookup needs to also understand, or every Probable backup file silently
    fell back to file mtime for date-range filtering instead of its real
    logical folder date."""
    part = part.strip()
    mm = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", part)
    if mm:
        try: return datetime.date(int(mm.group(3)), int(mm.group(2)), int(mm.group(1)))
        except Exception: return None
    if re.fullmatch(r"\d{1,2} [A-Za-z]{3} \d{4}", part):
        try: return datetime.datetime.strptime(part.title(), "%d %b %Y").date()
        except Exception: return None
    return None

def pb_list_backups(pipe, from_date="", to_date="", types=None):
    globs = [g.strip().lower() for g in (types or []) if g and g.strip()]
    def pd(s):
        try: return datetime.datetime.strptime(s, "%Y-%m-%d").date()
        except Exception: return None
    df, dt = pd(from_date), pd(to_date)
    res = []
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
                p = part.strip()
                if re.fullmatch(r"\d{1,2}", p):
                    crun = p
            for fn in files:
                if globs and not any(_pb_fn.fnmatch(fn.lower(), g) for g in globs): continue
                fpp = os.path.join(dp, fn)
                try: st = os.stat(fpp)
                except Exception: continue
                fdate = cdate or datetime.date.fromtimestamp(st.st_mtime)
                if df and fdate < df: continue
                if dt and fdate > dt: continue
                res.append({
                    "path": fpp, "name": fn,
                    "date": fdate.strftime("%d-%m-%Y"),
                    "size": st.st_size,
                    "run": crun,
                    "time": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%H:%M"),
                })
    res.sort(key=lambda x: (x["date"], x["name"]))
    return res[:3000]

def pb_backup_file(pipe, path):
    """Return the absolute path if it is inside this pipeline's backup roots, else None.
    Used by the per-file download endpoint."""
    ap = os.path.abspath(path or "")
    roots = [os.path.abspath(r) for r in _pb_backup_roots(pipe)]
    if os.path.isfile(ap) and any(ap.startswith(r) for r in roots):
        return ap
    return None


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
                      "full_file_ctcl_vs_nmass", "update limits", "risk_report")

    # ── 1. Walk backup folders, group files by (date, run_n) directory ────────
    # Structure: root/<DD-MM-YYYY>/<NN>/files  where NN = zero-padded run number
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
                mm = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", part.strip())
                if mm:
                    try:
                        cdate = datetime.date(int(mm.group(3)), int(mm.group(2)), int(mm.group(1)))
                        found_date_idx = i
                    except Exception: pass
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

    for (cdate, run_n) in sorted_keys:
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
                        res[code] = round(float(sum(pd.to_numeric(sub[c], errors="coerce").fillna(0).sum() for c in vc)), 2)
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
                                    vals[code]["NCDEX"] = round(float(pd.to_numeric(sub[vc], errors="coerce").fillna(0).sum()), 2)
            except Exception as e:
                warnings.append(f"{dl} R{run_n} NCDEX: {e}")

        # ── MCX: any xlsx that has "Client Margin Limit" column ───────────────
        mcx_cand = [(fn, fpp) for fn, fpp in group
                    if fn.lower().endswith(".xlsx")
                    and not any(fn.lower().startswith(p) for p in KNOWN_PREFIXES)]
        for fn, fpp in mcx_cand:
            try:
                tdf, err = _pb_read_xlsx_pandas(fpp)
                if err or tdf is None: continue
                cc = _pb_col(tdf, "Client Code"); ml = _pb_col(tdf, "Client Margin Limit")
                if not cc or not ml: continue
                cs = tdf[cc].astype(str).str.strip().str.upper()
                for code in codes:
                    sub = tdf[cs == code]
                    if not sub.empty:
                        vals[code]["MCX"] = round(float(pd.to_numeric(sub[ml], errors="coerce").fillna(0).sum()), 2)
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
                            v = pd.to_numeric(_r[col], errors="coerce")
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
                            v = pd.to_numeric(_r[col], errors="coerce")
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
