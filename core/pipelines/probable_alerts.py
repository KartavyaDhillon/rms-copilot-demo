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
                      subject, body, to_list, cc_list, file_paths, max_tries=6,
                      is_html=False):
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
                msg.attach(MIMEText(body, "html" if is_html else "plain"))
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
            "Best regards,\nRaj Kumari\n011-66331033 / 37 / 38\n")
    if safe_test:
        body = ("*** THIS IS A TEST RUN — PLEASE IGNORE ***\n\n" + body)

    # Every zone send is recorded and handed back so Step 10 can put it in the
    # SAME mail log as the branch/region sends. Without this the zone emails
    # were the one part of the run with no written record at all — you could
    # see that 130 branches got their file, but not whether Chandigarh,
    # Ludhiana and Mumbai got theirs.
    zone_results = []

    def _zone(zone_code, env_to, env_cc, subject, path):
        to, cc = _emails(env_to, env_cc, safe_test)
        if not path or not os.path.exists(path):
            zone_results.append((zone_code, path, to, cc, "NOT SENT - FAILED",
                                 "Zone file was not generated — nothing to attach."))
            print(f"⏭ SKIPPED zone {zone_code} (no file)")
            return
        if not to:
            zone_results.append((zone_code, path, [], cc, "NOT SENT - NO EMAIL",
                                 f"No recipients configured — check {env_to} in .env."))
            print(f"⏭ SKIPPED zone {zone_code} (no recipients configured)")
            return
        ok = _send_with_retry(smtp_server, smtp_port, smtp_email, smtp_pass,
                              _subj(subject, safe_test), body, to, cc, [path])
        if ok:
            zone_results.append((zone_code, path, to, cc, "SENT",
                                 f"Zone file delivered to {len(to) + len(cc)} recipient(s)."))
        else:
            zone_results.append((zone_code, path, to, cc, "NOT SENT - FAILED",
                                 "Mail server refused the message after several attempts."))

    _zone("ZOCHND", "PROBABLE_CHND_TO", "PROBABLE_CHND_CC",
          f"Probable_SHORTAGE_CHND_{date_str}", zochnd_path)
    _zone("ZOLUDH", "PROBABLE_LUDH_TO", "PROBABLE_LUDH_CC",
          f"Probable_SHORTAGE_LUDH_{date_str}", ludh_path)
    _zone("ZOMUMB", "PROBABLE_MUMB_TO", "PROBABLE_MUMB_CC",
          f"Probable_SHORTAGE_MUMB_{date_str}", zomumb_path)
    print("📧 STEP 7: all zone emails processed")
    return zone_results


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
# STEP 9 — region/branch split.
# Region merge groups (which sub-regions get combined into one parent-named
# REGION_<x>.xlsx file) used to be hardcoded here. They now live in the
# COMBINED_REGION sheet of MASTER/MAIL LIST.xlsx, so ops can add/remove a
# merge group by editing that sheet instead of needing a code change.
# =====================================================
def _load_region_merge_groups(base_folder):
    mail_list_file = os.path.join(base_folder, "MASTER", "MAIL LIST.xlsx")
    try:
        df = pd.read_excel(mail_list_file, sheet_name="COMBINED_REGION")
        df.columns = df.columns.str.strip()
        groups = {}
        for _, row in df.iterrows():
            parent = str(row.get("PARENT_REGION", "")).strip()
            member = str(row.get("MEMBER_CODE", "")).strip()
            if parent and parent.lower() != "nan" and member and member.lower() != "nan":
                groups.setdefault(parent, []).append(member)
        return groups
    except Exception as e:
        print(f"⚠️ Could not read COMBINED_REGION sheet from {mail_list_file} ({e}) "
              f"— no region merging applied this run")
        return {}


def _step_9_region_branch(base_folder, updated_main_file):
    output_region_folder = os.path.join(base_folder, "REGION")
    output_branch_folder = os.path.join(base_folder, "BRANCH")
    os.makedirs(output_region_folder, exist_ok=True)
    os.makedirs(output_branch_folder, exist_ok=True)
    df = pd.read_excel(updated_main_file)

    if "Region ID" in df.columns:
        unique_regions = df["Region ID"].dropna().unique()
        merge_groups = _load_region_merge_groups(base_folder)
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


MAIL_LOG_COLUMNS = ["Timestamp", "Branch", "Region", "Zone", "File", "To", "CC",
                    "Email Source", "Status", "Detail"]


def _load_zone_lookup(base_folder, updated_main_file):
    """Branch ID / Region ID -> Zone ID, so the mail log can show which zone
    each branch/region belongs to instead of just a bare code.

    PRIMARY SOURCE: today's own updated_main_file (Probable_Fullfile_K) — the
    exact same file _step_9_region_branch split every BRANCH_/REGION_ file
    from. It carries Zone ID for every row of every branch/region that could
    possibly appear in today's run, across ALL zones (Delhi, CDR, MT, Ludhiana,
    Mumbai, Chandigarh, CSD...) — not a scoped subset.
    FALLBACK: MASTER/Branch_AND_Region_LIST.xlsx, a static reference snapshot,
    used only for a code that genuinely isn't in today's file at all (e.g. it
    was deliberately built filtered to just Delhi/CDR/MT/Ludhiana, so it can
    fill a gap today's file doesn't have but never override it).

    Earlier version sourced from the static file FIRST, which was wrong: any
    branch/region outside that file's original scope (a Mumbai branch, say)
    came back blank even though its zone was sitting in today's data the
    whole time — that's what was happening in production.

    MERGED REGIONS: a REGION_<parent>.xlsx (see _load_region_merge_groups) is
    logged under its parent's name, but the parent code can have ZERO rows of
    its own today even though the merged file has real data — e.g. "RHYD" has
    0 rows today, all the data came from its group-mate "RHYD2" (187 rows,
    ZODELH). A literal lookup on "RHYD" would come back blank despite the file
    being real and its true zone being obvious from RHYD2. So after building
    region_zone, every merge group's parent is backfilled from the first
    member that actually has a zone.

    None of this is fatal if it fails — sending must never be blocked by a
    Zone lookup — worst case the column just prints blank.
    """
    branch_zone, region_zone, branch_region = {}, {}, {}

    try:
        df = pd.read_excel(updated_main_file)
        df.columns = df.columns.str.strip()
        if "Branch ID" in df.columns and "Zone ID" in df.columns:
            branch_zone = {
                str(r["Branch ID"]).strip(): str(r["Zone ID"]).strip()
                for _, r in df.dropna(subset=["Branch ID", "Zone ID"])
                              .drop_duplicates(subset=["Branch ID"]).iterrows()
            }
        if "Region ID" in df.columns and "Zone ID" in df.columns:
            region_zone = {
                str(r["Region ID"]).strip(): str(r["Zone ID"]).strip()
                for _, r in df.dropna(subset=["Region ID", "Zone ID"])
                              .drop_duplicates(subset=["Region ID"]).iterrows()
            }
        # Branch -> its own Region, so a BRANCH row in the mail log can show
        # which region it sits under instead of leaving the column empty.
        # Verified 1:1 against live data — every branch maps to exactly one
        # region, no branch spans two.
        if "Branch ID" in df.columns and "Region ID" in df.columns:
            branch_region = {
                str(r["Branch ID"]).strip(): str(r["Region ID"]).strip()
                for _, r in df.dropna(subset=["Branch ID", "Region ID"])
                              .drop_duplicates(subset=["Branch ID"]).iterrows()
            }
    except Exception as e:
        print(f"⚠️ Could not read Zone ID from {updated_main_file} ({e}) "
              f"— falling back to the static reference file for zone lookup")

    reference_file = os.path.join(base_folder, "MASTER", "Branch_AND_Region_LIST.xlsx")
    try:
        ref = pd.read_excel(reference_file)
        ref.columns = ref.columns.str.strip()
        for _, r in ref.dropna(subset=["Branch ID", "Zone ID"]).iterrows():
            branch_zone.setdefault(str(r["Branch ID"]).strip(), str(r["Zone ID"]).strip())
        for _, r in ref.dropna(subset=["Region ID", "Zone ID"]).iterrows():
            region_zone.setdefault(str(r["Region ID"]).strip(), str(r["Zone ID"]).strip())
        for _, r in ref.dropna(subset=["Branch ID", "Region ID"]).iterrows():
            branch_region.setdefault(str(r["Branch ID"]).strip(), str(r["Region ID"]).strip())
    except Exception as e:
        print(f"⚠️ Could not read {reference_file} either ({e}) — some Zone values may stay blank")

    merge_groups = _load_region_merge_groups(base_folder)
    for parent, members in merge_groups.items():
        if region_zone.get(parent):
            continue
        for member in members:
            if region_zone.get(member):
                region_zone[parent] = region_zone[member]
                break

    return branch_zone, region_zone, branch_region

# Status → (row fill, font colour). Anything not listed falls through unstyled.
# "NOT SENT - NO FILE" is gone by design: a branch/region with no shortage file
# today is a non-event (nothing to send, nobody to tell), and logging ~20 of
# them per run buried the rows that actually need action.
_MAIL_LOG_FILLS = {
    "SENT":                ("C6EFCE", "006100"),   # green
    "NOT SENT - NO EMAIL": ("FFC7CE", "9C0006"),   # red
    "NOT SENT - FAILED":   ("FFC7CE", "9C0006"),   # red
}

# CC list for CONTACT_AP FALLBACK branch sends, keyed by the branch's zone.
# Applies ONLY to fallback sends — a branch that HAS a MAIL LIST row keeps the
# exact TO/CC typed in the spreadsheet, untouched, per explicit instruction
# ("check mail list if found there than thats ok otherwise CONTACT AP").
# Note these zone lists REPLACE rms_del rather than adding to it — again per
# explicit instruction ("in the CC only the one which i have told").
_ZONE_FALLBACK_CC = {
    "ZOCHND": ["vipin@mastertrust.co.in", "nitingupta@mastertrust.co.in"],
    "ZOLUDH": ["kuljit.singh@mastertrust.co.in"],
}
_DEFAULT_FALLBACK_CC = ["rms_del@mastertrust.co.in"]


def _fallback_cc_for_zone(zone):
    """CC list for a CONTACT_AP fallback send in this zone."""
    return list(_ZONE_FALLBACK_CC.get((zone or "").strip(), _DEFAULT_FALLBACK_CC))


def _write_mail_log(log_data, base_folder):
    """Write the Step 10 mail log as both mail_log.csv (unchanged filename, so
    anything that already looks for it keeps working) and a formatted
    mail_log.xlsx with a SUMMARY sheet + colour-coded DETAIL sheet.

    Returns the path that should be attached to the Step 12 summary email —
    the xlsx if it was written, otherwise the csv.
    """
    csv_path  = os.path.join(base_folder, "mail_log.csv")
    xlsx_path = os.path.join(base_folder, "mail_log.xlsx")

    df = pd.DataFrame(log_data, columns=MAIL_LOG_COLUMNS)
    df.to_csv(csv_path, index=False)

    # Summary counts, in a fixed order so the sheet reads the same every day
    # even when a category happens to be empty.
    order = ["SENT", "NOT SENT - NO EMAIL", "NOT SENT - FAILED"]
    counts = df["Status"].value_counts().to_dict() if not df.empty else {}
    summary_rows = [[s, int(counts.get(s, 0))] for s in order]
    for s, n in counts.items():                      # anything unexpected
        if s not in order:
            summary_rows.append([s, int(n)])
    summary_rows.append(["TOTAL FILES LOGGED", len(df)])
    df_summary = pd.DataFrame(summary_rows, columns=["Status", "Count"])

    try:
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
            df_summary.to_excel(xw, sheet_name="SUMMARY", index=False)
            df.to_excel(xw, sheet_name="DETAIL", index=False)

            head_fill = PatternFill("solid", fgColor="1F4E78")
            head_font = Font(bold=True, color="FFFFFF")

            for sheet_name in ("SUMMARY", "DETAIL"):
                ws = xw.book[sheet_name]
                for cell in ws[1]:
                    cell.fill = head_fill
                    cell.font = head_font
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                ws.freeze_panes = "A2"
                if ws.max_row > 1:
                    ws.auto_filter.ref = ws.dimensions
                # Auto-width, capped so the Detail column can't run off-screen.
                for col in ws.columns:
                    width = max((len(str(c.value)) for c in col if c.value is not None),
                                default=0)
                    ws.column_dimensions[get_column_letter(col[0].column)].width = \
                        min(max(width + 2, 10), 70)

            # Colour-code the DETAIL rows by Status.
            ws = xw.book["DETAIL"]
            status_col = MAIL_LOG_COLUMNS.index("Status") + 1
            for r in range(2, ws.max_row + 1):
                style = _MAIL_LOG_FILLS.get(ws.cell(row=r, column=status_col).value)
                if not style:
                    continue
                fill = PatternFill("solid", fgColor=style[0])
                font = Font(color=style[1], bold=style[0] != "C6EFCE")
                for c in range(1, len(MAIL_LOG_COLUMNS) + 1):
                    ws.cell(row=r, column=c).fill = fill
                    ws.cell(row=r, column=c).font = font

            # Make the SUMMARY numbers pop the same way.
            ws = xw.book["SUMMARY"]
            for r in range(2, ws.max_row + 1):
                style = _MAIL_LOG_FILLS.get(ws.cell(row=r, column=1).value)
                if style:
                    for c in (1, 2):
                        ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=style[0])
                        ws.cell(row=r, column=c).font = Font(color=style[1], bold=True)
            ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
            ws.cell(row=ws.max_row, column=2).font = Font(bold=True)

        not_sent = sum(int(counts.get(s, 0)) for s in order[1:])
        print(f"📑 STEP 10: mail log saved → {xlsx_path} "
              f"({int(counts.get('SENT', 0))} sent, {not_sent} NOT sent)")
        return xlsx_path
    except Exception as e:
        print(f"⚠️ Could not write formatted mail_log.xlsx ({e}) — "
              f"falling back to {csv_path}")
        return csv_path


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
                           updated_main_file, log_file_path, safe_test=False,
                           zone_results=None):
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

    log_data = []

    def _log_zone_results():
        """Step 7's zone emails (Chandigarh / Ludhiana / Mumbai), recorded in
        this same log so one file answers 'did everything that was generated
        actually reach someone?' for zones as well as branches and regions.
        These rows carry a Zone but no Branch/Region, which is what marks them
        as zone-level."""
        for zone_code, path, to, cc, status, detail in (zone_results or []):
            log_data.append([
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "", "", zone_code,
                os.path.basename(path) if path else "",
                "; ".join(to) if to else "",
                "; ".join(cc) if cc else "",
                "ZONE EMAIL (.env)", status, detail,
            ])

    if not os.path.exists(mail_list_file):
        print(f"⚠️ STEP 10 skipped — {mail_list_file} not found. Place the MAIL LIST.xlsx "
              f"reference file there to enable the dynamic branch/region mail engine.")
        _log_zone_results()
        return _write_mail_log(log_data, base_folder)
    df_mail = pd.read_excel(mail_list_file)
    df_mail.columns = df_mail.columns.str.strip()
    _log_zone_results()
    branch_zone, region_zone, branch_region = _load_zone_lookup(base_folder, updated_main_file)

    # One row per file we actually had something to say about. Status vocabulary
    # is deliberately tiny so the log answers, at a glance, "did every file we
    # generated actually reach somebody?":
    #   SENT                 — email accepted by the SMTP server
    #   NOT SENT - NO EMAIL  — file exists, but nobody is mapped to receive it
    #   NOT SENT - FAILED    — recipient + file both fine, SMTP refused it
    #
    # There is deliberately NO "no file" status. A branch/region with no
    # shortage file today is a non-event — nothing to send and nobody to tell —
    # and logging ~20 of those per run buried the rows that need action.
    def _log(branch, region, zone, path, to_emails, cc_emails, source, status, detail):
        log_data.append([
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            branch or "", region or "", zone or "",
            os.path.basename(path) if path else "",
            "; ".join(to_emails) if to_emails else "",
            "; ".join(cc_emails) if cc_emails else "",
            source, status, detail,
        ])

    def _send_mail(to_emails, cc_emails, code, path, branch=None, region=None,
                   source="MAIL LIST"):
        _type = "BRANCH" if branch else "REGION"
        _code = branch or region
        if _type == "BRANCH":
            _zone = branch_zone.get(_code, "")
            # A branch row now also carries the region it sits under, so the
            # Region column isn't empty on every branch line.
            _log_region = branch_region.get(_code, "")
        else:
            _zone = region_zone.get(_code, "")
            _log_region = region
        if override_to:
            source = f"{source} (TEST OVERRIDE)"
        # No file today = nothing to report. Silently skipped, not logged.
        if not path or not os.path.exists(path):
            print(f"⏭ SKIPPED (no file): {_code}")
            return
        if not to_emails:
            _log(branch, _log_region, _zone, path, [], cc_emails, source, "NOT SENT - NO EMAIL",
                 f"File was created but no email address was found to send it to — "
                 f"please check MAIL LIST for {_code}.")
            print(f"⏭ SKIPPED (no email): {_code}")
            return
        _last_err = "unknown error"
        for attempt in range(1, 6):
            try:
                msg = MIMEMultipart()
                msg["From"] = smtp_email
                msg["To"] = ", ".join(to_emails)
                msg["Cc"] = ", ".join(cc_emails) if cc_emails else ""
                msg["Subject"] = subject_line
                body = (("*** THIS IS A TEST RUN — PLEASE IGNORE ***\n\n" if safe_test else "") +
                        f"Dear Team,\n\nPlease find the attached PROBABLE SHORTAGE report for "
                        f"{branch or region}.\n\nBest regards,\nRaj Kumari\n011-66331033 / 37 / 38\n")
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
                _log(branch, _log_region, _zone, path, to_emails, cc_emails, source, "SENT",
                     f"{_type.title()} file delivered to {len(recipients)} recipient(s) "
                     f"on attempt {attempt}.")
                print(f"✅ email sent successfully: {_code} — {os.path.basename(path)} "
                      f"({len(recipients)} recipients)")
                return
            except Exception as e:
                _last_err = e
                print(f"❌ FAILED: {_code} → {e}")
                if attempt < 5:
                    time.sleep(15)
        _log(branch, _log_region, _zone, path, to_emails, cc_emails, source, "NOT SENT - FAILED",
             f"Email address was found, but the mail server refused the message "
             f"after 5 attempts: {_last_err}")

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

    # ---------------------------------------------------
    # CONTACT_AP fallback — any branch that HAS a shortage file today but has
    # NO row in MAIL LIST.xlsx Sheet1 (e.g. a brand-new branch) falls through
    # to the CONTACT_AP Details sheet (branch/AP contact directory) instead of
    # being silently skipped. rms_del@mastertrust.co.in is always CC'd here.
    # Safe-test override still applies, same as every other send in this step.
    # ---------------------------------------------------
    covered_branches = {
        str(row.get("BRANCH", "")).strip()
        for _, row in df_mail.iterrows()
        if str(row.get("BRANCH", "")).strip() and str(row.get("BRANCH", "")).strip().lower() != "nan"
    }
    covered_regions = {
        str(row.get("REGIONS", "")).strip()
        for _, row in df_mail.iterrows()
        if str(row.get("REGIONS", "")).strip() and str(row.get("REGIONS", "")).strip().lower() != "nan"
    }
    try:
        contact_df = pd.read_excel(mail_list_file, sheet_name="CONTACT_AP Details")
        contact_df.columns = contact_df.columns.str.strip()
        contact_lookup = {
            str(r.get("AP BO CODE", "")).strip(): str(r.get("email", "")).strip()
            for _, r in contact_df.iterrows()
            if str(r.get("AP BO CODE", "")).strip()
            and str(r.get("email", "")).strip().lower() not in ("", "nan")
        }
    except Exception as e:
        print(f"⚠️ Could not read CONTACT_AP Details sheet ({e}) — no branch fallback available")
        contact_lookup = {}

    if os.path.isdir(branch_folder):
        for fname in sorted(os.listdir(branch_folder)):
            if not (fname.startswith("BRANCH_") and fname.endswith(".xlsx")):
                continue
            branch = fname[len("BRANCH_"):-len(".xlsx")]
            if branch in covered_branches:
                continue
            fallback_email = contact_lookup.get(branch)
            if not fallback_email:
                _log(branch, branch_region.get(branch, ""), branch_zone.get(branch, ""),
                     os.path.join(branch_folder, fname), [], [],
                     "NONE", "NOT SENT - NO EMAIL",
                     "File was created but no email address was found to send it to — "
                     f"{branch} has no row in MAIL LIST and no email in CONTACT_AP "
                     "Details. Please add it to one of the two.")
                print(f"⏭ SKIPPED (no MAIL LIST entry, not in CONTACT_AP either): {branch}")
                continue
            if override_to:
                to_emails, cc_emails = override_to, override_cc
            else:
                to_emails = [e.strip() for e in fallback_email.split(",") if e.strip()]
                # CC depends on the branch's zone — Chandigarh and Ludhiana have
                # their own owners; everyone else goes to rms_del.
                cc_emails = _fallback_cc_for_zone(branch_zone.get(branch, ""))
            _send_mail(to_emails, cc_emails, branch,
                       os.path.join(branch_folder, fname), branch=branch,
                       source="CONTACT_AP FALLBACK")

    # ---------------------------------------------------
    # Orphan REGION sweep — log-only, and it stays that way by design.
    # CONTACT_AP Details holds branch codes only (verified: 0 of its 485 rows
    # match a Region ID), so there is no fallback address to fall back TO. More
    # importantly a REGION file contains every branch's clients in that region,
    # so it must never be auto-sent to a branch-level contact. Per explicit
    # instruction: branches receive branch files only. These are reported so
    # someone can add a proper MAIL LIST row, never guessed at.
    # ---------------------------------------------------
    if os.path.isdir(region_folder):
        for fname in sorted(os.listdir(region_folder)):
            if not (fname.startswith("REGION_") and fname.endswith(".xlsx")):
                continue
            region = fname[len("REGION_"):-len(".xlsx")]
            if region in covered_regions:
                continue
            _log(None, region, region_zone.get(region, ""),
                 os.path.join(region_folder, fname), [], [],
                 "NONE", "NOT SENT - NO EMAIL",
                 "File was created but no email address was found to send it to — "
                 f"no MAIL LIST row has {region} in REGIONS. Please add a row, or "
                 "fold it into a parent region via the COMBINED_REGION sheet.")
            print(f"⏭ SKIPPED (region file with no MAIL LIST entry): {region}")

    return _write_mail_log(log_data, base_folder)


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


def _build_summary_body(base_folder, safe_test=False):
    """HTML body for the Step 12 summary email.

    Reads back the mail log Step 10 just wrote and turns it into something a
    person can act on without opening an attachment: how many branch/region
    files went out, and a table of every one that did NOT, with the reason.

    Deliberately defensive — a summary email that fails to build is worse than
    a plain one, so any problem here falls back to the original short text
    rather than losing the email (and its attachments) entirely.
    """
    banner = ("<p style='background:#c00;color:#fff;padding:10px;font-weight:bold;"
              "border-radius:4px;'>*** THIS IS A TEST RUN — PLEASE IGNORE ***</p>"
              if safe_test else "")
    fallback = (banner + "<p>Dear Team,</p><p>PFA</p>"
                "<p>Best regards,<br>Raj Kumari<br>011-66331033 / 37 / 38</p>")
    try:
        csv_path = os.path.join(base_folder, "mail_log.csv")
        if not os.path.exists(csv_path):
            return fallback
        df = pd.read_csv(csv_path).fillna("")
        if df.empty:
            return fallback

        not_sent = df[df["Status"] != "SENT"]

        rows = []
        for _, r in not_sent.iterrows():
            # Branch filled -> B, else Region filled -> R, else it's a
            # zone-level email from Step 7 -> Z.
            if str(r["Branch"]).strip():
                tag, kind = "B", "Branch"
            elif str(r["Region"]).strip():
                tag, kind = "R", "Region"
            else:
                tag, kind = "Z", "Zone"
            reason = (f"Email not found for {kind}"
                      if r["Status"] == "NOT SENT - NO EMAIL"
                      else f"Sending failed for {kind}")
            rows.append(
                "<tr>"
                f"<td style='{_TD}text-align:center;'>{tag}</td>"
                f"<td style='{_TD}'>{r['Branch'] or ''}</td>"
                f"<td style='{_TD}'>{r['Region'] or ''}</td>"
                f"<td style='{_TD}'>{r['Zone'] or ''}</td>"
                f"<td style='{_TD}'>{reason}</td>"
                "</tr>")

        if rows:
            table = (
                "<p><b>The following files were created but "
                "<span style='color:#c00;'>could not be emailed</span>:</b></p>"
                "<table style='border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;"
                "font-size:13px;'>"
                "<tr style='background:#1F4E78;color:#fff;'>"
                f"<th style='{_TH}'>Branch/Region Tag</th><th style='{_TH}'>Branch</th>"
                f"<th style='{_TH}'>Region</th><th style='{_TH}'>Zone</th>"
                f"<th style='{_TH}'>Reason</th></tr>"
                + "".join(rows) + "</table>"
                "<div style='margin-top:18px;padding:12px 14px;background:#FFF8E1;"
                "border-left:4px solid #F0A500;font-size:13px;color:#5a4300;'>"
                "<b>Action required:</b> please add an email address for the entries above "
                "in <b>MAIL LIST.xlsx</b> (or <b>CONTACT_AP Details</b> for branches) "
                "before the next run. Until they are added, these reports will keep being "
                "generated every day and reaching nobody.</div>")
        else:
            table = ("<div style='padding:12px 14px;background:#E8F5E9;"
                     "border-left:4px solid #2E7D32;font-size:13px;color:#1b5e20;'>"
                     "<b>All clear:</b> every file created today was emailed successfully. "
                     "No addresses are missing.</div>")

        return f"""{banner}
<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222;">
<p>Dear Team,</p>
<p>Please find today's Probable Margin Shortage files attached.</p>
{table}
<p style="margin-top:20px;">Best regards,<br>Raj Kumari<br>011-66331033 / 37 / 38</p>
</div>"""
    except Exception as e:
        print(f"⚠️ Could not build the HTML summary body ({e}) — sending the plain version")
        return fallback


# Shared inline cell styles (email clients ignore <style> blocks, so these have
# to live on every cell).
_TH = "border:1px solid #ccc;padding:6px 10px;text-align:left;"
_TD = "border:1px solid #ccc;padding:5px 10px;"


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
    body = _build_summary_body(base_folder, safe_test=safe_test)
    file_paths = [delhi_path, ludh_path, zochnd_path, zomumb_path, updated_main_file,
                  log_file_path, mail_log_file, mismatch_output]
    _send_with_retry(smtp_server, smtp_port, smtp_email, smtp_pass,
                      _subj("Probable_Fullfile_UPDATED", safe_test),
                      body, to_list, cc_list, file_paths, is_html=True)


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
    zone_results = _step_7_zone_emails(base_folder, zomumb_path, zochnd_path, delhi_path,
                                       ludh_path, date_str, safe_test=safe_test)
    # _step_8_whatsapp emits its own stage updates internally (live progress
    # counter — see _stage() calls inside it), so no separate one is needed
    # here; the next stage print happens once it returns.
    log_file_path = _step_8_whatsapp(base_folder, updated_main_file, market_time, safe_test=safe_test)
    _stage("🏢 WhatsApp done — sending region/branch mail…")
    _step_9_region_branch(base_folder, updated_main_file)
    mail_log_file = _step_10_dynamic_mail(base_folder, delhi_path, ludh_path, zochnd_path,
                                          zomumb_path, updated_main_file, log_file_path,
                                          safe_test=safe_test, zone_results=zone_results)
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

        # Mark today's run as DONE so the console stops offering a re-run.
        # PROBABLE is a once-a-day process ("runs": 1 in core/pipeline_box.py)
        # and every other pipeline (CTCL / MS15 / CLI) already writes this
        # file — PROBABLE was configured to READ it but nothing ever WROTE it,
        # so _read_counter() returned 0 forever and the Run tile stayed
        # available all day. A second real run would re-send every branch,
        # region and zone email and re-run the WhatsApp blast.
        #
        # Written ONLY on the live path — a safe test must never consume the
        # day's run, or the rehearsal would block the real thing.
        try:
            _today = datetime.datetime.now().strftime("%Y-%m-%d")
            with open(os.path.join(base_folder, "run_counter.txt"), "w",
                      encoding="utf-8") as _cf:
                _cf.write(f"{_today}|1")
            print("✅ run_counter.txt updated — today's PROBABLE run is marked complete")
        except Exception as e:
            print(f"⚠️ Could not update run_counter.txt: {e}")


if __name__ == "__main__":
    import sys
    _mt = sys.argv[1] if len(sys.argv) > 1 else "9:30 AM"
    run_probable_alerts(market_time=_mt)
