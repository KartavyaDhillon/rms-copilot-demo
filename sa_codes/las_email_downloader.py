import os as _sa_os
try:
    from dotenv import load_dotenv as _sa_ld
    _sa_ld(_sa_os.getenv("SA_ENV_FILE", r"D:\CLAUDE\rms-copilot\.env"))
except Exception:
    pass
def _SAENV(_k, _d):
    _v = _sa_os.getenv(_k)
    return _v if (_v is not None and _v != "") else _d
# ---- end SA env shim ----

import os
import re
import ssl
import poplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime, parseaddr
from datetime import datetime, date as _date
import sys

# ── CONFIG (all from .env, with sensible fallbacks) ─────────────────────────
POP_SERVER   = _SAENV("LAS_POP_SERVER",   "pop9.netcore.co.in")
POP_PORT     = int(_SAENV("LAS_POP_PORT", "995"))
EMAIL_ID     = _SAENV("LAS_POP_EMAIL",    "kartavya.dhillon@mastertrust.co.in")
PASSWORD     = _SAENV("LAS_POP_PASSWORD", "$Kartavya25*")
SENDER_EMAIL = _SAENV("LAS_DEBIT_SENDER", "anand.singh@mastertrust.co.in")
SAVE_FOLDER  = _SAENV("SA_LAS_FUNDING_DIR", r"C:\LAS FUNDING")

# ── TODAY'S FILE ─────────────────────────────────────────────────────────────
today         = datetime.now().date()
today_str     = today.strftime("%d-%m-%Y")
expected_subject  = f"LAS Debit Balances-{today_str}"
expected_filename = f"LAS Debit Balances-{today_str}.xlsx"
save_path         = os.path.join(SAVE_FOLDER, expected_filename)

# Pre-compile a flexible subject pattern that tolerates extra dots/spaces/dashes
# before the date digits — e.g. "LAS Debit Balances-.02-07-2026..xlsx" still matches.
_dd, _mm, _yyyy = today.strftime("%d"), today.strftime("%m"), today.strftime("%Y")
_subject_pattern = re.compile(
    rf"las\s+debit\s+balances[-.\s]+{_dd}[-.\s]+{_mm}[-.\s]+{_yyyy}",
    re.IGNORECASE
)

print("=" * 60)
print(f"LAS Debit Balances Downloader — {today_str}")
print(f"Looking for  : {expected_subject}")
print(f"Sender filter: {SENDER_EMAIL}")
print(f"Save to      : {save_path}")
print("=" * 60)

# ── SKIP IF ALREADY PRESENT ───────────────────────────────────────────────────
if os.path.isfile(save_path):
    print(f"✅ Already downloaded: {save_path}")
    sys.exit(0)

os.makedirs(SAVE_FOLDER, exist_ok=True)

# ── CONNECT ───────────────────────────────────────────────────────────────────
print(f"\nConnecting to {POP_SERVER}:{POP_PORT} …")
context = ssl.create_default_context()
context.set_ciphers("DEFAULT:@SECLEVEL=1")

mail = poplib.POP3_SSL(POP_SERVER, POP_PORT, context=context)
mail.user(EMAIL_ID)
mail.pass_(PASSWORD)

num_messages = len(mail.list()[1])
print(f"Mailbox: {num_messages} message(s).")

# ── SCAN ALL TODAY'S EMAILS (newest first, stop at yesterday) ────────────────
# The LAS email arrives at 8 AM but 100+ other emails pile up before 10 AM.
# We walk newest->oldest and break when we hit a previous-day email — no cap.
print(f"Scanning all today's emails (newest first)...\n")

today_indexes = []
scanned = 0

for i in range(num_messages, 0, -1):
    try:
        resp, lines, _ = mail.top(i, 0)
        hdr = email.message_from_bytes(b"\n".join(lines))
        scanned += 1

        mail_date = None
        try:
            mail_date = parsedate_to_datetime(hdr.get("Date", "")).date()
        except Exception:
            pass  # unparseable date — include it to be safe

        if mail_date is not None and mail_date < today:
            print(f"  Reached {mail_date} at index {i} after {scanned} messages -- stopping")
            break

        today_indexes.append(i)
    except Exception as ex:
        print(f"  [warn] Could not read msg {i}: {ex}")
        continue

print(f"Today's emails found: {len(today_indexes)}")

# ── SEARCH FOR THE MATCHING EMAIL ────────────────────────────────────────────
today_indexes.reverse()   # oldest → newest, pick the freshest match last
downloaded = False

for i in today_indexes:
    try:
        resp, lines, _ = mail.top(i, 0)
        hdr = email.message_from_bytes(b"\n".join(lines))

        sender = parseaddr(hdr.get("From", "") or "")[1].lower()

        # Decode subject
        subject = ""
        raw_sub = hdr.get("Subject", "") or ""
        if raw_sub:
            for part, enc in decode_header(raw_sub):
                if isinstance(part, bytes):
                    subject += part.decode(enc or "utf-8", errors="ignore")
                else:
                    subject += (part or "")

        print(f"  [{i}] From: {sender}")
        print(f"       Subj: {subject}")

        if sender != SENDER_EMAIL.lower():
            print(f"       → skip (sender mismatch — expected {SENDER_EMAIL})")
            continue

        if not _subject_pattern.search(subject):
            print(f"       → skip (subject doesn't match '{expected_subject}' or flexible variant)")
            continue

        print(f"  ✓ Match! Downloading full message …")

        resp, lines, _ = mail.retr(i)
        msg = email.message_from_bytes(b"\n".join(lines))

        for part in msg.walk():
            disp = (part.get_content_disposition() or "").lower()
            if "attachment" not in disp:
                continue

            # Get filename
            fname = part.get_filename() or ""
            if fname:
                dec = decode_header(fname)[0]
                if isinstance(dec[0], bytes):
                    fname = dec[0].decode(dec[1] or "utf-8", errors="ignore")
                else:
                    fname = dec[0] or ""

            print(f"  Attachment: {fname!r}")

            payload = part.get_payload(decode=True)
            if payload is None:
                print("  → skip (no payload)")
                continue

            with open(save_path, "wb") as f:
                f.write(payload)

            print(f"\n✅ Saved → {save_path}")
            downloaded = True
            break

        if downloaded:
            break

    except Exception as ex:
        print(f"  ⚠ Error on message {i}: {ex}")
        continue

mail.quit()

print()
if downloaded:
    print("=" * 60)
    print("PROCESS COMPLETED SUCCESSFULLY")
    print("=" * 60)
    sys.exit(0)
else:
    print("=" * 60)
    print("File not found. Summary:")
    print(f"  Mailbox size   : {num_messages} messages")
    print(f"  Scanned today  : {len(today_indexes)} candidate(s)")
    print(f"  Expected sender: {SENDER_EMAIL}")
    print(f"  Expected subject (contains): {expected_subject}")
    print("Check output above to see why each candidate was skipped.")
    print("=" * 60)
    sys.exit(1)
