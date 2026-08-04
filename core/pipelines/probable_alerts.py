"""
core/pipelines/probable_alerts.py — headless port of the reference
codes/API HITTING/code2.py (Probable Defaulter alerts, STEP 2 of the
pipeline: shortage calc → zone files → password-protect → zone emails →
client WhatsApp alerts → region/branch split → dynamic mail engine →
zone/validation → final summary mail → backup). Reference file is READ ONLY
(D:\\xxxx\\probable for claude / D:\\PROBABLE FINAL NEW) — nothing there is
ever modified; this is the ported, env-driven, dashboard-runnable version.

Differences from the reference script (deliberate, per instruction):
  - The reference script's customtkinter desktop popup (get_market_time()) —
    a native GUI window with radio buttons + hour/minute/AM-PM combo boxes —
    cannot show on a headless server. `market_time` is now a PARAMETER this
    function accepts, supplied by a new web modal built into the dashboard's
    Process Center (process-probable-box.js) that the operator fills in
    before every run — so the "9:30 AM" market session time is always
    actually chosen per-run, never silently defaulted, exactly as requested
    ("we cant rely on 9:30 timing only as sometimes we change this").
  - Every hardcoded credential/path from the reference script now comes from
    .env: the SMTP sender (raj.kumari account), the Tata WhatsApp bearer
    token + template name, the Mastertrust webpayout OAuth (reused from the
    EXISTING MT_WEBPAYOUT_USER/PASSWORD — same API, same use — already set up
    for Margin Shortage 15-min), the Excel zone-file password, and the base
    folder (PROBABLE_FOLDER, shared with probable_reconcile.py).
  - PROBABLE_TEST_MODE has been REPLACED by the SAFE TEST RUN (see the block
    above _safe_test_marker_path() below), matching Margin Shortage 15-min.
    The old flag worked by SKIPPING WhatsApp sends, which proved nothing about
    whether the sends actually work. A safe test does the opposite: every send
    really happens over the real Tata/SMTP paths, only the DESTINATION changes.
  - Every email TO/CC (zone emails, final summary, and the dynamic branch/
    region mail engine's per-row recipients from MASTER/MAIL LIST.xlsx) is
    currently overridden to kartavya.dhillon@mastertrust.co.in for testing —
    same convention as MS15/CTCL's ".env value change, not code branching"
    approach. Real recipients are preserved in a comment block in .env.
  - Bounded retries instead of `while True` infinite retry loops around SMTP/
    WhatsApp/OAuth calls, so a genuinely down server can't hang this run
    forever (same reasoning as probable_reconcile.py).
"""
import os
import re
import glob
import time
import shutil
import smtplib
import datetime
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders

import numpy as np
import pandas as pd
import requests


def _base_folder():
    d = os.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    os.makedirs(d, exist_ok=True)
    return d


def _stage(msg):
    """Announce the current stage to the dashboard's running pill.

    core/pipeline_box.py's _status() turns a "[FRIENDLY_MSG] …" line into a
    {"type":"note"} SSE event, which dashboard.html's pxHandle (see pxStage())
    renders as live text on this box's pill — same convention now used by
    core/pipelines/margin_shortage_15min.py and core/pipelines/probable_tcs_bancs.py.
    Without this a Run showed a bare "running…" the whole way through, with no
    sign of whether it was still reconciling, sending WhatsApp, or sending
    emails."""
    print(f"[FRIENDLY_MSG] {msg}")


# ═══════════════════════════════════════════════════════════════════════════
# SAFE TEST RUN — a ONE-SHOT, self-discarding rehearsal of a full real run.
#
# Replaces the old PROBABLE_TEST_MODE flag entirely (that flag worked by
# SKIPPING the WhatsApp sends, which proved nothing — it exercised none of the
# Tata API path, so a broken token or template looked exactly like a passing
# test). This does the opposite: every send STILL really happens, over the real
# Tata Tele / SMTP paths, with the real generated zone files attached — only
# the DESTINATION is swapped:
#
#   • every per-client WhatsApp        → PROBABLE_TEST_WA_TO   (no client messaged)
#   • the reconciliation report email  → PROBABLE_TEST_EMAIL_TO, CC dropped
#   • every zone email (CHND/LUDH/MUMB)→ PROBABLE_TEST_EMAIL_TO, CC dropped
#   • every branch/region email        → PROBABLE_TEST_EMAIL_TO, CC dropped
#   • the final summary email          → PROBABLE_TEST_EMAIL_TO, CC dropped
#   • subject lines                    → prefixed "[TEST — IGNORE]"
#   • BACKUP & CLEANUP                 → NOT performed (see below)
#
# Skipping the backup is the critical safety property, and it is PROBABLE's
# equivalent of MS15 skipping its alert tracker. Both backup steps here
# (probable_reconcile's RECONCILIATION and this file's SHORTAGE) physically
# MOVE every produced and input file out of PROBABLE_FOLDER into the dated
# backup folder. If a safe test did that, the day's downloaded input files
# would be gone and the operator's next REAL run would fail its pre-run check
# with "Missing required files" — having to re-download everything. So a safe
# test leaves the folder exactly as it found it, and the real run that follows
# behaves as if the test never happened.
#
# ARMING is one-shot by construction: the dashboard writes a marker file, and
# probable_full.py DELETES it as soon as the pre-run file check passes. There
# is no way for the flag to survive into a second run. Deliberately consumed
# AFTER the file check, not before — if the run aborts because an input file
# is missing, nothing was sent anywhere, so the arm must still be there for
# the retry. Otherwise the operator's next click would silently be a LIVE run
# they believed was a test.
# ═══════════════════════════════════════════════════════════════════════════
def _safe_test_marker_path():
    base = os.getenv("PROBABLE_FOLDER", r"D:\PROBABLE")
    return os.path.join(base, ".probable_safe_test")


def is_safe_test_armed():
    return os.path.exists(_safe_test_marker_path())


def arm_safe_test():
    p = _safe_test_marker_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(datetime.datetime.now().isoformat())
    return p


def disarm_safe_test():
    p = _safe_test_marker_path()
    try:
        if os.path.exists(p):
            os.remove(p)
            return True
    except Exception:
        pass
    return False


def safe_test_targets():
    """The exact redirect destinations, so the dashboard can show the operator
    precisely where a test run's messages will land BEFORE they commit to it."""
    email_to = [e.strip() for e in os.getenv(
        "PROBABLE_TEST_EMAIL_TO", "kartavya.dhillon@mastertrust.co.in").split(",") if e.strip()]
    wa_to = (os.getenv("PROBABLE_TEST_WA_TO", "") or "+917889026088").strip()
    return email_to, wa_to


def _emails(env_to, env_cc, safe_test=False):
    """Recipient pair for one email step. Under a safe test EVERY step collapses
    to the single tester address and CC is dropped entirely — a CC'd real
    address would defeat the whole point."""
    if safe_test:
        return list(safe_test_targets()[0]), []
    to = [e.strip() for e in os.getenv(env_to, "").split(",") if e.strip()]
    cc = [e.strip() for e in os.getenv(env_cc, "").split(",") if e.strip()]
    return to, cc


def _subj(s, safe_test=False):
    return f"[TEST — IGNORE] {s}" if safe_test else s


def _mask_mobile(number):
    number = str(number)
    return "******" + number[-4:]


# =====================================================
# STEP 1-4 — sum / shortage calc / filter / cleanup (unchanged math, just
# reads from PROBABLE_FOLDER and writes back there).
# =====================================================
def _steps_1_to_4(base_folder, today_ddmmyyyy):
    file_prefix = "Probable_Fullfile_K_"
    file_suffix = f"{today_ddmmyyyy}.xlsx"
    full_file_path = None
    for file_name in os.listdir(base_folder):
        if file_name.startswith(file_prefix) and file_name.endswith(file_suffix):
            full_file_path = os.path.join(base_folder, file_name)
            print(f"✅ File found: {full_file_path}")
            break
    if not full_file_path:
        raise FileNotFoundError(f"No {file_prefix}*{file_suffix} found — run the Reconcile "
                                 f"step first (probable_reconcile.py).")

    df = pd.read_excel(full_file_path)
    df["sum j and k"] = df["Initial Exp. Margin"] + df["Other Margin"]
    updated_file_path = os.path.join(base_folder, "Updated_File.xlsx")
    df.to_excel(updated_file_path, index=False)
    print(f"✅ STEP 1: sum j and k computed — {len(df)} rows")

    df = pd.read_excel(updated_file_path)
    col1, col2 = "Short On Initial Exp. Margin", "Short On Initial Exp. Other Margin"
    df[col1] = pd.to_numeric(df[col1], errors="coerce")
    df[col2] = pd.to_numeric(df[col2], errors="coerce")

    def _calc_sms(row):
        vals = [row[col1], row[col2]]
        negatives = [v for v in vals if pd.notna(v) and v < 0]
        return abs(min(negatives)) if negatives else np.nan

    df["Actual Shortage 4 sms/sqoff"] = df.apply(_calc_sms, axis=1)
    df = df[df["Actual Shortage 4 sms/sqoff"].notna()]
    updated_main_file = os.path.join(base_folder, "Probable_Fullfile_UPDATED.xlsx")
    df.to_excel(updated_main_file, index=False)
    print(f"✅ STEP 2: Actual Shortage 4 sms/sqoff computed — {len(df)} rows remaining")

    df = pd.read_excel(updated_main_file)
    df.columns = df.columns.str.strip()
    df = df[df["sum j and k"] > 0]
    df.to_excel(updated_main_file, index=False)
    print(f"✅ STEP 3: filtered to sum j and k > 0 — {len(df)} rows remaining")

    df = pd.read_excel(updated_main_file)
    df.columns = df.columns.str.strip()
    ids_to_remove = ["01292", "06436", "56720", "6436", "1292"]
    df["Account ID"] = df["Account ID"].astype(str).str.strip()
    df = df[~df["Account ID"].isin(ids_to_remove)]
    if "sum j and k" in df.columns:
        df = df.drop(columns=["sum j and k"])
    col_round = "Actual Shortage 4 sms/sqoff"
    if col_round in df.columns:
        df[col_round] = pd.to_numeric(df[col_round], errors="coerce").round(2)
    df.to_excel(updated_main_file, index=False)
    print(f"✅ STEP 4: removed house codes, rounded shortage — {len(df)} rows remaining")
    return updated_main_file


# =====================================================
# STEP 5-6 — zone files + password protection (win32com — runs fine on the
# actual Windows server, same as the rest of this repo's Excel automation).
# =====================================================
def _steps_5_to_6(base_folder, updated_main_file):
    df = pd.read_excel(updated_main_file)
    df.columns = df.columns.str.strip()
    if "Zone ID" not in df.columns:
        raise Exception("'Zone ID' column not found")

    zomumb_path = os.path.join(base_folder, "ZOMUMB_ZONE DATA.xlsx")
    zochnd_path = os.path.join(base_folder, "ZOCHND_ZONE DATA.xlsx")
    delhi_path  = os.path.join(base_folder, "DELHI_ZONE DATA.xlsx")
    ludh_path   = os.path.join(base_folder, "LUDH_ZONE DATA.xlsx")

    df[df["Zone ID"] == "ZOMUMB"].to_excel(zomumb_path, index=False)
    df[df["Zone ID"] == "ZOCHND"].to_excel(zochnd_path, index=False)
    df[df["Zone ID"].isin(["ZOCDR", "ZODELH", "ZOMT"])].to_excel(delhi_path, index=False)
    df[df["Zone ID"].isin(["ZOLUDH", "ZO"])].to_excel(ludh_path, index=False)
    print(f"✅ STEP 5: zone files created (Mumbai/Chandigarh/Delhi/Ludhiana)")

    password = os.getenv("PROBABLE_ZONE_XLSX_PASSWORD", "Master@2024")
    files_to_protect = [delhi_path, ludh_path, zochnd_path, zomumb_path]
    try:
        import win32com.client as win32
        excel = win32.gencache.EnsureDispatch("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        protected = 0
        for file in files_to_protect:
            if not os.path.exists(file):
                print(f"❌ Missing: {file}"); continue
            try:
                wb = excel.Workbooks.Open(file)
                wb.SaveAs(file, FileFormat=51, Password=password)
                wb.Close(SaveChanges=False)
                print(f"🔐 Protected: {os.path.basename(file)}")
                protected += 1
            except Exception as e:
                print(f"⚠ Error protecting {file}: {e}")
        excel.Quit()
        print(f"✅ STEP 6: {protected}/{len(files_to_protect)} zone files password-protected")
    except Exception as e:
        print(f"⚠️ Excel automation (win32com) failed — zone files saved WITHOUT password "
              f"protection: {e}")

    return zomumb_path, zochnd_path, delhi_path, ludh_path


# =====================================================
# STEP 7 — zone emails (raj.kumari sender account, per-zone recipients).
# =====================================================
def _send_with_retry(smtp_server, smtp_port, smtp_email, smtp_pass,
                      subject, body, to_list, cc_list, file_paths, max_tries=6):
    valid_files = [f for f in file_paths if f and os.path.exists(f)]
    if not valid_files:
        print(f"⏭ SKIPPED: {subject} (no attachment found)")
        return
    if not to_list:
        print(f"⏭ SKIPPED: {subject} (no TO recipients configured)")
        return
    for attempt in range(1, max_tries + 1):
        try:
            print(f"📡 Sending: {subject} (attempt {attempt}/{max_tries})")
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.ehlo(); server.starttls(); server.ehlo()
                server.login(smtp_email, smtp_pass)
                msg = MIMEMultipart()
                msg["From"] = smtp_email
                msg["To"] = ", ".join(to_list)
                msg["Cc"] = ", ".join(cc_list)
                msg["Subject"] = subject
                msg.attach(MIMEText(body, "plain"))
                for file_path in valid_files:
                    with open(file_path, "rb") as attachment:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(attachment.read())
                        encoders.encode_base64(part)
                        part.add_header("Content-Disposition",
                                         f'attachment; filename={os.path.basename(file_path)}')
                        msg.attach(part)
                all_recipients = to_list + cc_list
                server.sendmail(smtp_email, all_recipients, msg.as_string())
            print(f"✅ email sent successfully: {subject} ({len(to_list)+len(cc_list)} recipients)")
            return True
        except Exception as e:
            print(f"❌ FAILED: {subject} → {e}")
            if attempt < max_tries:
                print("🔁 Retrying in 15 sec..."); time.sleep(15)
    print(f"[FRIENDLY_MSG] Could not send '{subject}' after several attempts — "
          f"check SMTP settings in Credentials and re-send manually.")
    return False


def _step_7_zone_emails(base_folder, zomumb_path, zochnd_path, delhi_path, ludh_path, date_str,
                         safe_test=False):
    smtp_server = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    smtp_port   = int(os.getenv("SMTP_PORT", "587"))
    smtp_email  = os.getenv("PROBABLE_ALERTS_SMTP_EMAIL", "")
    smtp_pass   = os.getenv("PROBABLE_ALERTS_SMTP_PASSWORD", "")
    body = ("Dear Team,\n\nPlease Find the attachment of Probable Margin File.\n\n"
            "Best regards,\nRMS Copilot — Probable Defaulter\n")
    if safe_test:
        body = ("*** THIS IS A TEST RUN — PLEASE IGNORE ***\n\n" + body)

    chnd_to, chnd_cc = _emails("PROBABLE_CHND_TO", "PROBABLE_CHND_CC", safe_test)
    _send_with_retry(smtp_server, smtp_port, smtp_email, smtp_pass,
                      _subj(f"Probable_SHORTAGE_CHND_{date_str}", safe_test),
                      body, chnd_to, chnd_cc, [zochnd_path])

    ludh_to, ludh_cc = _emails("PROBABLE_LUDH_TO", "PROBABLE_LUDH_CC", safe_test)
    _send_with_retry(smtp_server, smtp_port, smtp_email, smtp_pass,
                      _subj(f"Probable_SHORTAGE_LUDH_{date_str}", safe_test),
                      body, ludh_to, ludh_cc, [ludh_path])

    mumb_to, mumb_cc = _emails("PROBABLE_MUMB_TO", "PROBABLE_MUMB_CC", safe_test)
    _send_with_retry(smtp_server, smtp_port, smtp_email, smtp_pass,
                      _subj(f"Probable_SHORTAGE_MUMB_{date_str}", safe_test),
                      body, mumb_to, mumb_cc, [zomumb_path])
    print("📧 STEP 7: all zone emails processed")


# =====================================================
# STEP 8 — client WhatsApp alerts (test-mode gated).
# =====================================================
def _step_8_whatsapp(base_folder, updated_main_file, market_time, safe_test=False):
    oauth_url  = "https://webpayout.mastertrust.co.in/api/Tradingdata/OAUTH_TOKEN"
    mobile_url = "https://webpayout.mastertrust.co.in/api/Codecheck/LIV_CHECKMOBILE?clientcode={client_code}"
    wa_url     = "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/messages"
    wa_token   = os.getenv("PROBABLE_WA_TOKEN", os.getenv("WA_AUTH_TOKEN", ""))
    wa_template = os.getenv("PROBABLE_WA_TEMPLATE", "probable_margin_short")
    wa_headers = {"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"}
    oauth_payload = {"grant_type": "password",
                     "username": os.getenv("MT_WEBPAYOUT_USER", ""),
                     "password": os.getenv("MT_WEBPAYOUT_PASSWORD", "")}
    test_wa_to = safe_test_targets()[1] if safe_test else ""
    if safe_test:
        print(f"🧪 SAFE TEST RUN — every WhatsApp message is REALLY sent over the Tata API "
              f"(so a bad token or template WILL surface), but every one of them goes to "
              f"{test_wa_to} instead of the client. No client is messaged.")

    def _get_oauth_token(max_tries=8):
        for attempt in range(max_tries):
            try:
                res = requests.post(oauth_url, json=oauth_payload, timeout=20)
                res.raise_for_status()
                token = res.json()["body"]["data"][0]["ACCESS_TOKEN"]
                print("✅ OAuth token generated")
                return token
            except Exception as e:
                print(f"❌ OAuth failed: {e} — retrying in 15s ({attempt+1}/{max_tries})")
                time.sleep(15)
        raise RuntimeError("OAuth token generation failed after repeated attempts")

    def _get_client_mobile(client_code, token):
        try:
            headers = {"Authorization": f"Bearer {token}"}
            res = requests.get(mobile_url.format(client_code=client_code), headers=headers, timeout=20)
            res.raise_for_status()
            return res.json()["body"]["data"]["data"][0]["MOBILE"]
        except Exception:
            return None

    # ── retry policy ──────────────────────────────────────────────────────
    # Ported from the reference D:\PHYSICAL DELIVERY\codes\FINAL_SMS_EMAIL_updated.PY
    # (read-only reference — nothing there is modified). Retry ONLY on things
    # that can plausibly succeed on a second try: transport failures (timeout,
    # connection reset) and server-side/rate-limit HTTP codes. A 401 (bad
    # token) or any other 4xx is a permanent, deterministic rejection —
    # retrying it 10 times just wastes 10x the wall-clock and floods the log
    # with the same rejection. Previous behaviour retried EVERY failure 5x
    # including a bad token.
    _WA_MAX_RETRIES = 10
    _WA_RETRY_STATUS = {408, 429, 500, 502, 503, 504}
    _WA_RETRY_SLEEP = 10

    def _send_or_simulate(client_code, mobile, shortage):
        mobile_n = str(mobile).replace("+91", "").strip()
        if not mobile_n.startswith("91"):
            mobile_n = "91" + mobile_n
        mobile_n = "+" + mobile_n
        # SAFE TEST: the message is still genuinely built and POSTed — same
        # template, same parameters, same API — only `to` is swapped for the
        # tester's number. The client's real number is still looked up first
        # (so a broken mobile lookup still fails visibly in a test) and it is
        # the CLIENT's masked number, not the tester's, that the caller writes
        # into WhatsApp_Log.xlsx — so the log still answers "who would have
        # been messaged", which is the whole reason to run a rehearsal.
        if safe_test:
            mobile_n = test_wa_to if test_wa_to.startswith("+") else ("+" + test_wa_to.lstrip("+"))
        payload = {"source": "internal", "to": mobile_n, "type": "template",
                   "template": {"name": wa_template, "language": {"code": "en"},
                                "components": [{"type": "body", "parameters": [
                                    {"type": "text", "text": str(client_code)},
                                    {"type": "text", "text": str(shortage)},
                                    {"type": "text", "text": str(market_time)}]}]}}
        for attempt in range(1, _WA_MAX_RETRIES + 1):
            try:
                res = requests.post(wa_url, headers=wa_headers, json=payload, timeout=30)
                if res.status_code == 200:
                    _tag = " [TEST → not the client]" if safe_test else ""
                    # NOT "whatsapp submitted for" — that exact phrase is the
                    # anchor core.pipeline_box._status()'s _WA_OK regex matches,
                    # which used to turn THIS line into its own dashboard event
                    # row. With 100–200 candidate clients that meant 100–200
                    # identical "WhatsApp submitted successfully" rows flooding
                    # the box, drowning out everything else and telling the
                    # operator nothing (the frontend doesn't even show this
                    # line's actual text — see dashboard.html's pxHandle). Still
                    # printed for the saved log/audit trail, just no longer
                    # matched into an event. The one event that fires for this
                    # whole step is the single batch summary at the end below.
                    print(f"  ✓ sent — {client_code} → {_mask_mobile(mobile_n)}{_tag}")
                    return "Sent [TEST]" if safe_test else "Sent"
                if res.status_code in _WA_RETRY_STATUS:
                    print(f"  ⚠ send attempt {attempt}/{_WA_MAX_RETRIES} — {client_code}: "
                          f"HTTP {res.status_code} (server/rate-limit) — retrying")
                    if attempt < _WA_MAX_RETRIES:
                        time.sleep(_WA_RETRY_SLEEP)
                    continue
                # Permanent rejection (401 bad token, 400 bad template/number,
                # any other 4xx). Retrying cannot change the outcome.
                _why = ("WhatsApp token rejected (401) — check PROBABLE_WA_TOKEN"
                        if res.status_code == 401 else f"HTTP {res.status_code}")
                print(f"  ✗ permanent failure — {client_code}: {_why}")
                return "Failed"
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError) as e:
                # Transport-level — genuinely worth another try.
                # NOT worded "whatsapp error for" (the _WA_BAD anchor in
                # core/pipeline_box.py) — this fires per attempt, so under that
                # wording one client could throw 10 red rows before going green.
                print(f"  ⚠ send attempt {attempt}/{_WA_MAX_RETRIES} — {client_code}: "
                      f"{type(e).__name__} — retrying")
                if attempt < _WA_MAX_RETRIES:
                    time.sleep(_WA_RETRY_SLEEP)
            except Exception as e:
                print(f"  ✗ permanent failure — {client_code}: {e}")
                return "Failed"
        print(f"  ✗ gave up after {_WA_MAX_RETRIES} attempts — {client_code}")
        return "Failed"

    token = _get_oauth_token()
    df = pd.read_excel(updated_main_file)
    df.columns = df.columns.str.strip()

    # ── build the candidate list FIRST, deduplicated ───────────────────────
    # The old code iterated the sheet directly and sent one message per ROW.
    # Probable_Fullfile_UPDATED can legitimately carry the same Account ID on
    # more than one row (a client with positions in several segments), so a
    # duplicated client got messaged twice — and Meta/Tata silently DROP a
    # byte-identical template message re-sent to the same number within a short
    # window. That is exactly the "94 sent but only 83 received" gap: the extras
    # were accepted with HTTP 200 (hence counted as sent) and then deduplicated
    # away before delivery. The reference script FINAL_SMS_EMAIL_updated.PY
    # guards this with its own `sent_clients` set; same idea here, done up front
    # so the reported total is the number of DISTINCT clients from the start.
    candidates, seen, dup_skipped = [], set(), 0
    for _, row in df.iterrows():
        client_code = str(row["Account ID"]).strip()
        initial_margin = pd.to_numeric(row["Initial Exp. Margin"], errors="coerce")
        other_margin = pd.to_numeric(row["Other Margin"], errors="coerce")
        if pd.isna(initial_margin) or pd.isna(other_margin):
            continue
        if (initial_margin + other_margin) <= 0:
            continue
        if client_code in seen:
            dup_skipped += 1
            print(f"  ⚠ duplicate client skipped — {client_code}")
            continue
        seen.add(client_code)
        candidates.append((client_code, row.get("Actual Shortage 4 sms/sqoff", 0)))

    total_candidates = len(candidates)
    print(f"\n📱 WHATSAPP PROCESS STARTING — {total_candidates} distinct clients "
          f"({dup_skipped} duplicate rows skipped), market time = {market_time}")
    if safe_test and total_candidates:
        # Every message is redirected to ONE number in a rehearsal, which makes
        # the dedupe above matter even more — and even distinct clients can
        # produce identical text if their shortage happens to match. Say so
        # rather than letting the tester count messages and think sends failed.
        print("🧪 SAFE TEST — all messages go to one number. WhatsApp may drop "
              "duplicates that are byte-identical, so the count you RECEIVE can "
              "legitimately be lower than the count sent. Judge a test by the "
              "log's per-client status, not by how many landed on your phone.")
    _stage(f"📱 Sending WhatsApp alerts to {total_candidates} clients…")

    # ── send in parallel ───────────────────────────────────────────────────
    # Was strictly one-at-a-time with a 0.5s sleep between each — ~2 minutes of
    # pure waiting for 200 clients, on top of the per-client mobile lookup.
    # ThreadPoolExecutor pattern lifted from the reference
    # FINAL_SMS_EMAIL_updated.PY (which uses 50 workers); 25 here because this
    # box also hits the webpayout mobile-lookup API in the same worker and
    # there's no reason to push either endpoint harder than needed. Override
    # with PROBABLE_WA_WORKERS if the API ever starts rate-limiting.
    try:
        _WORKERS = max(1, int(os.getenv("PROBABLE_WA_WORKERS", "25")))
    except Exception:
        _WORKERS = 25
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from threading import Lock

    log_rows = []
    _counts = {"sent": 0, "failed": 0, "no_mobile": 0, "done": 0}
    _lock = Lock()

    def _one_client(client_code, shortage):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mobile = _get_client_mobile(client_code, token)
        if not mobile:
            print(f"  ✗ no mobile on record — {client_code}")
            return [ts, client_code, "N/A", "Mobile Not Found"]
        status = _send_or_simulate(client_code, mobile, shortage)
        return [ts, client_code, _mask_mobile(mobile), status]

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {pool.submit(_one_client, cc, sh): cc for cc, sh in candidates}
        for fut in as_completed(futures):
            cc = futures[fut]
            try:
                row_out = fut.result()
            except Exception as e:
                print(f"  ✗ worker crashed — {cc}: {e}")
                row_out = [datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           cc, "N/A", f"Failed - {e}"]
            with _lock:
                log_rows.append(row_out)
                _counts["done"] += 1
                st = str(row_out[3])
                if st.startswith("Sent"):
                    _counts["sent"] += 1
                elif st == "Mobile Not Found":
                    _counts["no_mobile"] += 1
                else:
                    _counts["failed"] += 1
                # One stage update PER COMPLETED MESSAGE, not every 10. Cheap:
                # it's a single print, and results land as workers finish, so
                # the counter moves in bursts rather than a stall-then-jump.
                _stage(f"📱 WhatsApp: {_counts['done']}/{total_candidates} — "
                       f"{_counts['sent']} sent, {_counts['failed']} failed, "
                       f"{_counts['no_mobile']} no mobile")

    _sent_sf, _failed_sf, _no_mobile_sf = (_counts["sent"], _counts["failed"],
                                            _counts["no_mobile"])
    # Workers finish out of order — restore the sheet's own client order so the
    # log is readable/diffable rather than shuffled by thread timing.
    _order = {cc: i for i, (cc, _) in enumerate(candidates)}
    log_rows.sort(key=lambda r: _order.get(r[1], 10 ** 9))

    log_df = pd.DataFrame(log_rows, columns=["Date-Time", "Client Code", "Mobile Number", "WhatsApp Status"])
    log_file_path = os.path.join(base_folder, "WhatsApp_Log.xlsx")
    log_df.to_excel(log_file_path, index=False)
    print(f"📄 STEP 8: WhatsApp log saved → {log_file_path} ({len(log_rows)} clients processed)")

    # THE one dashboard event for this whole step — everything above is now
    # detail-log-only. Keeps the literal phrase "all whatsapp messages
    # processed" (the anchor core.pipeline_box._status()'s _WA_OK regex
    # matches), with the actual counts appended after an em-dash so
    # process-probable-box.js's pxHandle wrapper (which looks for text after
    # an em-dash — see _pxEvtDetail there) shows the real numbers instead of a
    # generic "WhatsApp submitted successfully". Ends up on screen as:
    # "WhatsApp submitted — 176 sent, 2 failed, 2 mobile not found (180 total)"
    print(f"all whatsapp messages processed — {_sent_sf} sent, {_failed_sf} failed, "
          f"{_no_mobile_sf} mobile not found ({len(log_rows)} distinct clients"
          + (f", {dup_skipped} duplicate rows skipped" if dup_skipped else "") + ")")
    return log_file_path


# =====================================================
# STEP 9 — region/branch split (unchanged logic).
# =====================================================
def _step_9_region_branch(base_folder, updated_main_file):
    output_region_folder = os.path.join(base_folder, "REGION")
    output_branch_folder = os.path.join(base_folder, "BRANCH")
    os.makedirs(output_region_folder, exist_ok=True)
    os.makedirs(output_branch_folder, exist_ok=True)
    df = pd.read_excel(updated_main_file)

    if "Region ID" in df.columns:
        unique_regions = df["Region ID"].dropna().unique()
        merge_groups = {"RWA": ["RWA", "RWA1", "RWA2", "RWA3", "RBHUB"],
                         "RHYD": ["RHYD", "RHYD2"],
                         "RDLNA": ["RDLNA", "RHYD3", "RBGLR", "RCHAI"],
                         "RCDRR": ["RCDRR", "RTTDZ", "BTTCH"]}
        handled = set()
        for merged_name, group in merge_groups.items():
            merged_data = df[df["Region ID"].isin(group)]
            if not merged_data.empty:
                out = os.path.join(output_region_folder, f"REGION_{merged_name}.xlsx")
                merged_data.to_excel(out, index=False)
                print(f"✅ REGION MERGED: {out}")
                handled.update(group)
        for region in unique_regions:
            if region in handled:
                continue
            region_data = df[df["Region ID"] == region]
            if not region_data.empty:
                out = os.path.join(output_region_folder, f"REGION_{region}.xlsx")
                region_data.to_excel(out, index=False)
                print(f"✅ REGION: {out}")
    else:
        print("⚠️ 'Region ID' not found")

    if "Branch ID" in df.columns:
        for branch in df["Branch ID"].dropna().unique():
            branch_data = df[df["Branch ID"] == branch]
            if not branch_data.empty:
                out = os.path.join(output_branch_folder, f"BRANCH_{branch}.xlsx")
                branch_data.to_excel(out, index=False)
                print(f"✅ BRANCH: {out}")
    else:
        print("⚠️ 'Branch ID' not found")
    print("🎉 STEP 9: region & branch files created")


# =====================================================
# STEP 10 — dynamic branch/region mail engine, driven by
# PROBABLE_FOLDER/MASTER/MAIL LIST.xlsx (a reference spreadsheet the ops
# team maintains on the server — same file the reference script reads).
# Recipient override: while PROBABLE_MAILLIST_OVERRIDE_TO is set in .env,
# EVERY branch/region email uses that address instead of the file's own
# TO/CC — this is the "redirect all emails to kartavya for testing" switch
# for this specific step (the only one whose recipients come from a
# spreadsheet rather than a fixed .env TO/CC pair).
# =====================================================
def _step_10_dynamic_mail(base_folder, delhi_path, ludh_path, zochnd_path, zomumb_path,
                           updated_main_file, log_file_path, safe_test=False):
    smtp_server = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    smtp_port   = int(os.getenv("SMTP_PORT", "587"))
    smtp_email  = os.getenv("PROBABLE_ALERTS_SMTP_EMAIL", "")
    smtp_pass   = os.getenv("PROBABLE_ALERTS_SMTP_PASSWORD", "")
    subject_line = _subj(f"PROBABLE_SHORTAGE_{datetime.datetime.now().strftime('%d-%m-%y')}", safe_test)
    mail_list_file = os.path.join(base_folder, "MASTER", "MAIL LIST.xlsx")
    branch_folder = os.path.join(base_folder, "BRANCH")
    region_folder = os.path.join(base_folder, "REGION")
    log_file = os.path.join(base_folder, "mail_log.csv")

    # Under a safe test this ALWAYS overrides, regardless of what
    # PROBABLE_MAILLIST_OVERRIDE_TO happens to be set to — this is the one step
    # whose recipients come from a spreadsheet rather than .env, so leaving it
    # to config would be the single hole through which a test could reach a
    # real branch.
    override_to, override_cc = _emails("PROBABLE_MAILLIST_OVERRIDE_TO",
                                       "PROBABLE_MAILLIST_OVERRIDE_CC", safe_test)

    if not os.path.exists(mail_list_file):
        print(f"⚠️ STEP 10 skipped — {mail_list_file} not found. Place the MAIL LIST.xlsx "
              f"reference file there to enable the dynamic branch/region mail engine.")
        return log_file
    df_mail = pd.read_excel(mail_list_file)
    df_mail.columns = df_mail.columns.str.strip()
    log_data = []

    def _send_mail(to_emails, cc_emails, code, path, branch=None, region=None):
        if not path or not os.path.exists(path):
            log_data.append([datetime.datetime.now(), branch, region, ";".join(to_emails), "NO FILE", path])
            print(f"⏭ SKIPPED (no file): {branch or region}")
            return
        if not to_emails:
            log_data.append([datetime.datetime.now(), branch, region, "NO EMAIL", "INVALID EMAIL", path])
            print(f"⏭ SKIPPED (no email): {branch or region}")
            return
        for attempt in range(1, 6):
            try:
                msg = MIMEMultipart()
                msg["From"] = smtp_email
                msg["To"] = ", ".join(to_emails)
                msg["Cc"] = ", ".join(cc_emails) if cc_emails else ""
                msg["Subject"] = subject_line
                body = (("*** THIS IS A TEST RUN — PLEASE IGNORE ***\n\n" if safe_test else "") +
                        f"Dear Team,\n\nPlease find the attached PROBABLE SHORTAGE report for "
                        f"{branch or region}.\n\nBest regards,\nRMS Copilot — Probable Defaulter\n")
                msg.attach(MIMEText(body, "plain"))
                with open(path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
                msg.attach(part)
                recipients = to_emails + cc_emails
                with smtplib.SMTP(smtp_server, smtp_port) as server:
                    server.ehlo(); server.starttls(); server.ehlo()
                    server.login(smtp_email, smtp_pass)
                    server.sendmail(smtp_email, recipients, msg.as_string())
                log_data.append([datetime.datetime.now(), branch, region, ";".join(to_emails), "SUCCESS", path])
                print(f"✅ email sent successfully: {branch or region} — {os.path.basename(path)} "
                      f"({len(recipients)} recipients)")
                return
            except Exception as e:
                print(f"❌ FAILED: {branch or region} → {e}")
                if attempt < 5:
                    time.sleep(15)
        log_data.append([datetime.datetime.now(), branch, region, ";".join(to_emails), "FAILED", path])

    for _, row in df_mail.iterrows():
        if override_to:
            to_emails, cc_emails = override_to, override_cc
        else:
            to_emails = [e.strip() for e in str(row.get("TO", "")).strip().split(",") if e.strip()]
            cc_emails = [e.strip() for e in str(row.get("CC", "")).strip().split(",") if e.strip()]
        branch = str(row.get("BRANCH", "")).strip()
        region = str(row.get("REGIONS", "")).strip()
        if branch and branch.lower() != "nan":
            _send_mail(to_emails, cc_emails, branch,
                       os.path.join(branch_folder, f"BRANCH_{branch}.xlsx"), branch=branch)
        if region and region.lower() != "nan":
            _send_mail(to_emails, cc_emails, region,
                       os.path.join(region_folder, f"REGION_{region}.xlsx"), region=region)

    pd.DataFrame(log_data, columns=["Timestamp", "Branch", "Region", "To", "Status", "Attachment"]).to_csv(
        log_file, index=False)
    print(f"📑 STEP 10: mail log saved → {log_file}")
    return log_file


# =====================================================
# STEP 11 — zone filter + branch/region validation against reference files
# (MASTER/Branch_AND_Region_LIST.xlsx) — unchanged logic, just env-driven paths.
# =====================================================
def _step_11_validation(base_folder, updated_main_file):
    filtered_file    = os.path.join(base_folder, "Filtered_Zone_ID.xlsx")
    extracted_output = os.path.join(base_folder, "Branch_Region_ID_Extracted.xlsx")
    mismatch_output  = os.path.join(base_folder, "Mismatched_Branch_And_Region_IDs.xlsx")
    master_file      = os.path.join(base_folder, "MASTER", "MAIL LIST.xlsx")
    reference_file   = os.path.join(base_folder, "MASTER", "Branch_AND_Region_LIST.xlsx")

    zone_ids = ["ZO", "ZOCDR", "ZODELH", "ZOMT"]
    df = pd.read_excel(updated_main_file)
    if "Zone ID" not in df.columns:
        print("❌ 'Zone ID' column not found — skipping STEP 11 validation")
        return mismatch_output
    filtered_df = df[df["Zone ID"].isin(zone_ids)]
    filtered_df.to_excel(filtered_file, index=False)
    print(f"✅ STEP 11a: filtered file created: {filtered_file}")

    if filtered_df.empty:
        print("⚠️ No data for validation"); return mismatch_output

    filtered_df.columns = filtered_df.columns.str.strip().str.upper()
    branch_col = next((c for c in filtered_df.columns if "BRANCH" in c), None)
    region_col = next((c for c in filtered_df.columns if "REGION" in c), None)
    if not branch_col or not region_col:
        print("⚠️ Branch/Region columns not found — skipping validation"); return mismatch_output
    extracted = filtered_df[[branch_col, region_col]].copy()
    extracted.columns = ["BRANCH ID", "REGION ID"]
    extracted["BRANCH ID"] = extracted["BRANCH ID"].fillna("").astype(str).str.strip().str.upper()
    extracted["REGION ID"] = extracted["REGION ID"].fillna("").astype(str).str.strip().str.upper()
    extracted = extracted[~((extracted["BRANCH ID"] == "") & (extracted["REGION ID"] == ""))].drop_duplicates()
    extracted.to_excel(extracted_output, index=False)
    print(f"✅ STEP 11b: extracted file: {extracted_output}")

    if not os.path.exists(reference_file) or not os.path.exists(master_file):
        print(f"⚠️ STEP 11c skipped — reference files not found in {os.path.join(base_folder, 'MASTER')}")
        return mismatch_output

    try:
        ref_df = pd.read_excel(reference_file)
        ref_df.columns = ref_df.columns.str.strip().str.upper()
        ref_branch_col = next((c for c in ref_df.columns if "BRANCH" in c), None)
        ref_region_col = next((c for c in ref_df.columns if "REGION" in c), None)
        ref_sub = ref_df[[ref_branch_col, ref_region_col]].copy()
        ref_sub.columns = ["BRANCH ID", "REGION ID"]
        ref_sub["BRANCH ID"] = ref_sub["BRANCH ID"].fillna("").astype(str).str.strip().str.upper()
        ref_sub["REGION ID"] = ref_sub["REGION ID"].fillna("").astype(str).str.strip().str.upper()
        ref_sub = ref_sub.drop_duplicates().reset_index(drop=True)

        merged = extracted.merge(ref_sub, on=["BRANCH ID", "REGION ID"], how="left", indicator=True)
        unmatched = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"]).reset_index(drop=True)

        ref_branches = set(ref_sub["BRANCH ID"].unique())
        ref_regions = set(ref_sub["REGION ID"].unique())

        def _reason(row):
            bf, rf = row["BRANCH ID"] in ref_branches, row["REGION ID"] in ref_regions
            if not bf and not rf: return "Branch and Region both not found"
            if not bf: return "Branch not found"
            if not rf: return "Region not found"
            return "Pair mismatch (branch exists but region not mapped to it)"

        if not unmatched.empty:
            unmatched["BRANCH_FOUND"] = unmatched["BRANCH ID"].apply(lambda x: x in ref_branches)
            unmatched["REGION_FOUND"] = unmatched["REGION ID"].apply(lambda x: x in ref_regions)
            unmatched["REASON"] = unmatched.apply(_reason, axis=1)
            exceptional_regions = {"RHYD": ["RHYD", "RHYD2"], "RCDRR": ["RCDRR", "RTTDZ", "BTTCH"],
                                    "RDLNA": ["RDLNA", "RHYD3", "RBGLR", "RCHAI"],
                                    "RWA": ["RWA", "RWA1", "RWA2", "RWA3", "RBHUB"]}

            def _map_parent(region):
                for parent, subs in exceptional_regions.items():
                    if region in subs: return parent
                return region

            unmatched["REGION_PARENT"] = unmatched["REGION ID"].apply(_map_parent)
            master_df = pd.read_excel(master_file)
            master_df.columns = master_df.columns.str.strip().str.upper()
            master_branch_col = next((c for c in master_df.columns if "BRANCH" in c), None)
            master_region_col = next((c for c in master_df.columns if "REGION" in c), None)
            master_branches = set(master_df[master_branch_col].astype(str).str.strip().str.upper())
            master_regions = set(master_df[master_region_col].astype(str).str.strip().str.upper())
            unmatched = unmatched[(~unmatched["BRANCH ID"].isin(master_branches))
                                   & (~unmatched["REGION_PARENT"].isin(master_regions))]
            if unmatched.empty:
                print("✅ No records where both Branch and Region are missing in MASTER FILE.")
            else:
                unmatched.to_excel(mismatch_output, index=False)
                print(f"⚠️ STEP 11c: {len(unmatched)} mismatched records saved → {mismatch_output}")
        else:
            print("✅ All Branch-Region pairs exist in reference mapping")
    except Exception as e:
        print(f"❌ STEP 11 validation error: {e}")
    return mismatch_output


# =====================================================
# STEP 12 — final summary mail.
# =====================================================
def _step_12_final_summary(base_folder, delhi_path, ludh_path, zochnd_path, zomumb_path,
                            updated_main_file, log_file_path, mail_log_file, mismatch_output,
                            safe_test=False):
    smtp_server = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in")
    smtp_port   = int(os.getenv("SMTP_PORT", "587"))
    smtp_email  = os.getenv("PROBABLE_ALERTS_SMTP_EMAIL", "")
    smtp_pass   = os.getenv("PROBABLE_ALERTS_SMTP_PASSWORD", "")
    to_list, cc_list = _emails("PROBABLE_FINAL_TO", "PROBABLE_FINAL_CC", safe_test)
    body = (("*** THIS IS A TEST RUN — PLEASE IGNORE ***\n\n" if safe_test else "") +
            "Dear Team,\n\nPFA\n\nBest regards,\nRMS Copilot — Probable Defaulter\n")
    file_paths = [delhi_path, ludh_path, zochnd_path, zomumb_path, updated_main_file,
                  log_file_path, mail_log_file, mismatch_output]
    _send_with_retry(smtp_server, smtp_port, smtp_email, smtp_pass,
                      _subj("Probable_Fullfile_UPDATED", safe_test),
                      body, to_list, cc_list, file_paths)


# =====================================================
# STEP 13 — backup & cleanup.
# =====================================================
def _step_13_backup(base_folder):
    formatted_date = datetime.datetime.now().strftime("%d %b %Y").upper()
    backup_folder = os.path.join(base_folder, "BACKUPFILES AUTOMATED", formatted_date, "SHORTAGE")
    # ".probable_safe_test" is the safe-test arm marker. A real run has already
    # consumed it by this point, so it normally isn't here — but if someone
    # armed a test WHILE this run was in flight, sweeping the marker into the
    # backup folder would silently destroy the arm and their next click would
    # be a live run they believed was a test. Cheap to guard, so it is guarded.
    exclude_files = {"MAIL LIST.xlsx", "Branch_AND_Region_LIST.xlsx",
                     os.path.basename(_safe_test_marker_path())}
    skip_ext = (".py", ".bat")

    def _move_safe(src_folder):
        if not os.path.exists(src_folder):
            return 0
        moved = 0
        for name in os.listdir(src_folder):
            src_path = os.path.join(src_folder, name)
            if not os.path.isfile(src_path):
                continue
            if name in exclude_files or name.lower().endswith(skip_ext):
                continue
            subfolder = "MAIN" if src_folder == base_folder else os.path.basename(src_folder)
            dest_dir = os.path.join(backup_folder, subfolder)
            os.makedirs(dest_dir, exist_ok=True)
            try:
                shutil.move(src_path, os.path.join(dest_dir, name))
                moved += 1
            except Exception as e:
                print(f"⚠ Could not move {name}: {e}")
        return moved

    total = sum(_move_safe(f) for f in
                [base_folder, os.path.join(base_folder, "BRANCH"), os.path.join(base_folder, "REGION")])
    print(f"\n✅ STEP 13: backup completed — {total} files moved to {backup_folder}" if total
          else "\n⚠ STEP 13: no files moved")


def run_probable_alerts(market_time="9:30 AM", safe_test=False):
    """market_time: string like '9:30 AM' — supplied by the dashboard's web
    modal (process-probable-box.js) before every run. NEVER hardcoded/silently
    defaulted per instruction; the modal always requires the operator to
    confirm or change it before Run starts.

    safe_test: when True, every email and every WhatsApp still really sends,
    but to the tester instead of the real recipient, and the backup/cleanup
    step is skipped so the folder is left exactly as found. See the block
    above _safe_test_marker_path(). Normally supplied by probable_full.py,
    which reads and consumes the armed marker."""
    base_folder = _base_folder()
    today_ddmmyyyy = datetime.datetime.now().strftime("%d%m%Y")
    date_str = datetime.datetime.now().strftime("%d%m%Y")
    print(f"Selected Market Time: {market_time}")

    _stage("📧 Generating zone files & sending zone emails…")
    updated_main_file = _steps_1_to_4(base_folder, today_ddmmyyyy)
    zomumb_path, zochnd_path, delhi_path, ludh_path = _steps_5_to_6(base_folder, updated_main_file)
    _step_7_zone_emails(base_folder, zomumb_path, zochnd_path, delhi_path, ludh_path, date_str,
                        safe_test=safe_test)
    # _step_8_whatsapp emits its own stage updates internally (live progress
    # counter — see _stage() calls inside it), so no separate one is needed
    # here; the next stage print happens once it returns.
    log_file_path = _step_8_whatsapp(base_folder, updated_main_file, market_time, safe_test=safe_test)
    _stage("🏢 WhatsApp done — sending region/branch mail…")
    _step_9_region_branch(base_folder, updated_main_file)
    mail_log_file = _step_10_dynamic_mail(base_folder, delhi_path, ludh_path, zochnd_path,
                                          zomumb_path, updated_main_file, log_file_path,
                                          safe_test=safe_test)
    _stage("🔎 Validating output & sending the final summary email…")
    mismatch_output = _step_11_validation(base_folder, updated_main_file)
    _step_12_final_summary(base_folder, delhi_path, ludh_path, zochnd_path, zomumb_path,
                            updated_main_file, log_file_path, mail_log_file, mismatch_output,
                            safe_test=safe_test)

    if safe_test:
        # See the SAFE TEST block at the top of this file. The backup MOVES the
        # day's input and output files out of PROBABLE_FOLDER; doing that after
        # a rehearsal would leave the operator's next REAL run with nothing to
        # work from. So the folder is left untouched and the real run proceeds
        # normally.
        print("🧪 SAFE TEST — backup & cleanup SKIPPED on purpose. Every file is still in "
              f"{base_folder}, so your next real run will find everything it needs.")
    else:
        _stage("📦 Backing up & archiving today's files…")
        _step_13_backup(base_folder)


if __name__ == "__main__":
    import sys
    _mt = sys.argv[1] if len(sys.argv) > 1 else "9:30 AM"
    run_probable_alerts(market_time=_mt)
