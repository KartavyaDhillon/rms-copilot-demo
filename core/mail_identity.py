"""Who the console's e-mails come from, plus the "we are moving to noreply" note.

WHY THIS FILE EXISTS
  About 40 scripts send mail through smtplib with the sender's address and password written inside them. Instead of
  editing every one, this module wraps smtplib.SMTP / SMTP_SSL once (install()) and handles two things for the mails
  that are sent as kartavya.dhillon@mastertrust.co.in:

    1. NOTE   - before the switch date a small amber box is added to the mail, ABOVE the signature:
                "Note: From 1 Dec 2026, these mails will come from noreply_rms@mastertrust.co.in.
                 Please mark it as a safe sender."
    2. SENDER - when the sender is noreply (switch date reached, or the button pressed) the SMTP login becomes the
                noreply account and the From header / envelope sender are rewritten. Reply-To stays Kartavya.

  3. SIGNATURE - until the mails come from noreply: the person's own (Best regards, / Kartavya Dhillon / Master Capital Services Ltd /
                phone line); from noreply: Best regards, / RMS Automated Reports / phone line / This is a system-generated mail.
                ("signature": false in .mail_identity.json stops all of it). It replaces the old sign-off only when that sign-off is short (a name); a long tail is left alone.

  The Darwinbox mailer is excluded on purpose (keep_sender): it stays Sandeep, because replies are expected there.
  Mails of other people (Anand ...) are never touched: only mails logged in as / sent from the addresses in OLD_USERS
  (Kartavya and Sandeep; "old_users" in .mail_identity.json can list more). Reply-To is the person the mail was sent as.

MODES  (file .mail_identity.json in the repo root, or the button in the Process Center)
    auto      before switch_date: sender Kartavya + note;   from switch_date: sender noreply (no note)    <- default
    kartavya  always Kartavya + note
    noreply   always noreply (no note)                                                  (the "switch")

SAFETY: if the noreply password is missing or the noreply login is refused, the mail is sent the OLD way (no mail is
lost) and a line "[MAIL_IDENTITY] ..." is printed. Any error inside this module never stops a mail.

noreply SMTP details (same server smtp3.netcore.co.in as today):  NOREPLY_SMTP_USER (default noreply_rms@mastertrust.co.in)
and NOREPLY_SMTP_PASS in .env.local (or the environment). They are never written to git.
"""
import json
import os
import re
import smtplib
import sys
import threading
from datetime import date, datetime
from email import message_from_bytes, message_from_string
from email.utils import formataddr, parseaddr
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONF_FILE = REPO / ".mail_identity.json"
MODES = ("auto", "kartavya", "noreply")
OLD_USER = "kartavya.dhillon@mastertrust.co.in"
OLD_USERS = (OLD_USER, "sandeep.kumar2@mastertrust.co.in")        # every sender that moves to noreply
NEW_USER = "noreply_rms@mastertrust.co.in"
SWITCH_DATE = "2026-12-01"
MARK = "<!--rms-sender-note-->"
TEXT_MARK = "[rms-sender-note]"
_lock = threading.Lock()


# ── settings ──────────────────────────────────────────────────────────────────────────────────────────────────
def _defaults():
    return {"mode": "auto", "switch_date": SWITCH_DATE, "old_user": OLD_USER, "old_users": list(OLD_USERS), "noreply_user": NEW_USER,
            "note_date_text": "1 Dec 2026",
            "signature": True, "signature_name": "RMS Automated Reports", "signature_phone": "011-66331000 | Ext. 84",
            "company": "Master Capital Services Ltd",
            "people": {OLD_USERS[0]: "Kartavya Dhillon", OLD_USERS[1]: "Sandeep Kumar"}}


def get_config():
    cfg = _defaults()
    try:
        raw = json.loads(CONF_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            cfg.update({k: v for k, v in raw.items() if v not in (None, "")})
    except (OSError, ValueError):
        pass
    if cfg.get("mode") not in MODES:
        cfg["mode"] = "auto"
    return cfg


def _switch_day(cfg):
    try:
        return datetime.strptime(str(cfg.get("switch_date")), "%Y-%m-%d").date()
    except ValueError:
        return datetime.strptime(SWITCH_DATE, "%Y-%m-%d").date()


def effective(cfg=None, today=None):
    """'noreply' or 'kartavya' - who the mail comes from right now."""
    cfg = cfg or get_config()
    if cfg["mode"] == "noreply":
        return "noreply"
    if cfg["mode"] == "kartavya":
        return "kartavya"
    return "noreply" if (today or date.today()) >= _switch_day(cfg) else "kartavya"


def _env(name):
    v = os.environ.get(name)
    if v:
        return v.strip()
    for fn in (".env.local", ".env"):                       # the console loads these for itself; scripts run on their own may not
        try:
            for line in (REPO / fn).read_text(encoding="utf-8-sig").splitlines():
                k, _, val = line.partition("=")
                if k.strip() == name and val.strip():
                    return val.strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


SECRET_FILE = REPO / ".mail_identity_secret.json"          # password typed in the Process Center (gitignored, never returned by the API)


def _saved_password():
    try:
        return str(json.loads(SECRET_FILE.read_text(encoding="utf-8")).get("noreply_password") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def set_password(pw, who=""):
    pw = (pw or "").strip()
    if not pw:
        raise ValueError("Type the noreply password first")
    tmp = SECRET_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"noreply_password": pw, "saved_by": who or "", "saved_at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    os.replace(tmp, SECRET_FILE)
    return describe()


def noreply_creds(cfg=None):
    cfg = cfg or get_config()
    return (_env("NOREPLY_SMTP_USER") or cfg["noreply_user"]), (_env("NOREPLY_SMTP_PASS") or _env("NOREPLY_SMTP_PASSWORD") or _saved_password())


def send_test(to, as_noreply=False):
    """Send one test mail through the same wrapper the real mails use. as_noreply=True logs in as noreply directly (checks the
    password without changing the mode); otherwise it follows the current mode (Kartavya + note, or noreply)."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    cfg = get_config()
    to = (to or "").strip() or cfg["old_user"]
    if "@" not in to:
        return {"ok": False, "error": "Type a valid e-mail address"}
    host = _env("SMTP_SERVER") or "smtp3.netcore.co.in"
    port = int(_env("SMTP_PORT") or 587)
    nu, npw = noreply_creds(cfg)
    if as_noreply:
        user, pw, frm = nu, npw, nu
        if not npw:
            return {"ok": False, "error": "The noreply password is not saved yet"}
    else:
        user, pw, frm = cfg["old_user"], (_env("SENDER_PASSWORD") or _env("SMTP_PASSWORD") or _env("ALLOC_SMTP_PASS") or ""), cfg["old_user"]
        if effective(cfg) == "kartavya" and not pw:
            return {"ok": False, "error": "Kartavya's own SMTP password is not in .env (SENDER_PASSWORD), so the 'as Kartavya' test cannot log in. Use 'Test as noreply'."}
    m = MIMEMultipart("alternative")
    m["From"], m["To"], m["Subject"] = formataddr(("RMS Automated Reports", frm)), to, "Test - RMS mail sender"
    if as_noreply:
        tt, th = signature_text(cfg), signature_html(cfg)
    else:                                                       # the wrapper turns this sign-off into the right signature for the current mode
        tt, th = "Best regards,\nKartavya Dhillon\n", "<br>Best regards,<br>Kartavya Dhillon"
    m.attach(MIMEText("This is a test mail from the RMS system.\n\n" + tt, "plain", "utf-8"))
    m.attach(MIMEText("<html><body><p>This is a test mail from the RMS system.</p>" + th + "</body></html>", "html", "utf-8"))
    install()
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, pw)
            sent_as = nu if getattr(s, "_rms_swapped", False) else user
            s.send_message(m)
        return {"ok": True, "to": to, "sent_as": sent_as, "note_added": effective(cfg) == "kartavya" and not as_noreply}
    except smtplib.SMTPAuthenticationError as e:
        return {"ok": False, "error": f"The server refused the login for {user} ({e.smtp_code}). Check the password and that SMTP AUTH is enabled for this mailbox."}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def describe():
    cfg = get_config()
    eff = effective(cfg)
    user, pw = noreply_creds(cfg)
    return {"mode": cfg["mode"], "effective": eff, "switch_date": cfg["switch_date"], "sender": user if eff == "noreply" else cfg["old_user"],
            "noreply_user": user, "noreply_ready": bool(pw), "old_user": cfg["old_user"], "old_users": _old_users(cfg),
            "label": {"auto": "Automatic (noreply from " + str(cfg["switch_date"]) + ")", "kartavya": "Always Kartavya (with note)",
                      "noreply": "Always noreply"}[cfg["mode"]]}


def set_mode(mode, who=""):
    mode = (mode or "").strip().lower()
    if mode not in MODES:
        raise ValueError("mode must be one of: " + ", ".join(MODES))
    cfg = {}
    try:
        cfg = json.loads(CONF_FILE.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            cfg = {}
    except (OSError, ValueError):
        cfg = {}
    cfg.update({"mode": mode, "changed_by": who or "", "changed_at": datetime.now().isoformat(timespec="seconds")})
    tmp = CONF_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, CONF_FILE)
    return describe()


def _log(msg):
    try:
        print(f"[MAIL_IDENTITY] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


# ── the note ──────────────────────────────────────────────────────────────────────────────────────────────────
def note_text(cfg=None):
    cfg = cfg or get_config()
    return f"Note: From {cfg['note_date_text']}, these mails will come from {cfg['noreply_user']}. Please mark it as a safe sender."


def note_html(cfg=None):
    cfg = cfg or get_config()
    return (MARK + '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:14px 0;"><tr>'
            '<td style="background:#FFF4CE;border:1px solid #F2B600;border-left:6px solid #E69500;padding:10px 14px;'
            'font-family:Calibri,Arial,sans-serif;font-size:14px;color:#5A3E00;">'
            '<b style="color:#B45F00;">&#9888; Note:</b> From <b>' + cfg["note_date_text"] + '</b>, these mails will come from '
            '<b>' + cfg["noreply_user"] + '</b>. Please mark it as a <b>safe sender</b>.</td></tr></table>')


_SIG_WORDS = r"(?:(?:best|warm|kind|with)\s+)?(?:thanks\s*(?:&amp;|&|and)\s*)?regards\b|thank\s+you\b|thanks\b"
_SIG_HTML = re.compile(_SIG_WORDS, re.I)
_SIG_TEXT = re.compile(r"^[ \t]*(?:" + _SIG_WORDS + ")", re.I | re.M)


SIG_MARK = "<!--rms-signature-->"


def signature_html(cfg=None, person=None):
    """person=None: the standard system signature (used once mails come from noreply).
    person="Kartavya Dhillon": the personal signature used until then (name, company, phone)."""
    cfg = cfg or get_config()
    mid = f"{person}<br>{cfg['company']}" if person else f"<b>{cfg['signature_name']}</b>"
    last = "" if person else "<br>This is a system-generated mail."
    return (SIG_MARK + '<p style="margin:10px 0 0 0;font-family:Calibri,Arial,sans-serif;font-size:14px;">Best regards,<br>'
            + mid + '<br>&#128222; ' + cfg["signature_phone"] + last + '</p>')


def signature_text(cfg=None, person=None):
    cfg = cfg or get_config()
    if person:
        return f"Best regards,\n{person}\n{cfg['company']}\n\U0001F4DE {cfg['signature_phone']}\n"
    return f"Best regards,\n{cfg['signature_name']}\n\U0001F4DE {cfg['signature_phone']}\nThis is a system-generated mail.\n"


def _signoff_pos(html):
    """Where the existing sign-off ('Best regards' / 'Thanks' ...) starts (the whole line/paragraph), or None."""
    last = None
    for m in _SIG_HTML.finditer(html):
        last = m
    if last is None:
        return None
    start = last.start()
    win = html[max(0, start - 200):start]
    cut = max(win.lower().rfind(t) for t in ("<br", "<p", "<div", "<table"))
    pos = start - (len(win) - cut) if cut >= 0 else start
    lt, gt = html.rfind("<", 0, start), html.rfind(">", 0, start)
    return lt if lt > gt else pos                              # never split a tag that holds the word


def put_note_html(html, cfg=None, note=True, sig=False):
    """note: amber box just ABOVE the signature. sig: replace the old sign-off with the standard signature (only when what
    follows the sign-off is short - a name; a long tail is kept as it is). No sign-off: both go before </body>."""
    cfg = cfg or get_config()
    person = sig if isinstance(sig, str) else None
    want_note, want_sig = note and MARK not in html, bool(sig) and SIG_MARK not in html
    if not (want_note or want_sig):
        return html
    box = note_html(cfg) if want_note else ""
    end = html.lower().rfind("</body>")
    end = len(html) if end < 0 else end
    pos = _signoff_pos(html)
    if pos is None:
        return html[:end] + box + (signature_html(cfg, person) if want_sig else "") + html[end:]
    tail = html[pos:end]
    short = len(re.sub(r"<[^>]+>", " ", tail).strip()) <= 300 and "<table" not in tail.lower()
    if want_sig and short:
        return html[:pos] + box + signature_html(cfg, person) + html[end:]
    return html[:pos] + box + html[pos:]


def put_note_text(text, cfg=None, note=True, sig=False):
    cfg = cfg or get_config()
    want_note = note and TEXT_MARK not in text and not ("Note: From" in text and "safe sender" in text)
    person = sig if isinstance(sig, str) else None
    want_sig = bool(sig) and cfg["signature_name"] not in text and cfg["signature_phone"] not in text
    if not (want_note or want_sig):
        return text
    box = note_text(cfg) + "\n\n" if want_note else ""
    last = None
    for m in _SIG_TEXT.finditer(text):
        last = m
    if last is None:
        return text.rstrip("\n") + "\n\n" + box + (signature_text(cfg, person) if want_sig else "")
    tail = text[last.start():]
    if want_sig and len(tail.strip()) <= 300:
        return text[:last.start()] + box + signature_text(cfg, person)
    return text[:last.start()] + box + text[last.start():]


# ── rewriting one message ────────────────────────────────────────────────────────────────────────────────────────
def _old_users(cfg=None):
    cfg = cfg or get_config()
    us = cfg.get("old_users") or [cfg["old_user"]]
    return [str(u).strip().lower() for u in us if str(u).strip()]


def _is_old(addr, cfg=None):
    return parseaddr(str(addr or ""))[1].strip().lower() in _old_users(cfg)


def _inject_note(msg, cfg, note=True, sig=False):
    changed = False
    for part in msg.walk():
        if part.is_multipart():
            continue
        if (part.get_content_disposition() or "") == "attachment" or part.get_filename():
            continue
        ct = part.get_content_type()
        if ct not in ("text/html", "text/plain"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        cs = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(cs, "replace")
        except LookupError:
            text = payload.decode("utf-8", "replace")
        new = put_note_html(text, cfg, note, sig) if ct == "text/html" else put_note_text(text, cfg, note, sig)
        if new == text:
            continue
        if "Content-Transfer-Encoding" in part:
            del part["Content-Transfer-Encoding"]
        part.set_payload(new, charset="utf-8")
        changed = True
    return changed


def _to_noreply(msg, cfg, user):
    name, orig = parseaddr(str(msg["From"] or ""))
    del msg["From"]
    msg["From"] = formataddr((name, user)) if name else user
    if not msg["Reply-To"]:
        msg["Reply-To"] = orig                                   # replies still reach the person it was sent as


def prepare(msg, swapped, user=None, cfg=None):
    """Apply the note / the sender change to an email.message.Message in place. Returns True when it changed anything."""
    cfg = cfg or get_config()
    if not _is_old(msg["From"], cfg):
        return False
    changed = False
    sender = parseaddr(str(msg["From"] or ""))[1].lower()
    if swapped:
        _to_noreply(msg, cfg, user or cfg["noreply_user"])
        changed = True
    note = (not swapped) and effective(cfg) == "kartavya"
    sig = False
    if cfg.get("signature", True):
        # from noreply: the system signature; until then: the person's own name, company and the phone line
        sig = True if swapped else (dict(cfg.get("people") or {}).get(sender) or False)
        sig = sig if sig is True else (sig or False)
    if note or sig:
        changed = _inject_note(msg, cfg, note, sig) or changed
    return changed


def prepare_raw(raw, from_addr, swapped, user=None):
    """Same for sendmail(): raw is a str or bytes message. Returns (raw, from_addr)."""
    cfg = get_config()
    head = raw[:4000].decode("latin-1", "replace") if isinstance(raw, (bytes, bytearray)) else raw[:4000]
    if not any(u in head.lower() for u in _old_users(cfg)):
        return raw, from_addr
    msg = message_from_bytes(bytes(raw)) if isinstance(raw, (bytes, bytearray)) else message_from_string(raw)
    if not prepare(msg, swapped, user, cfg):
        return raw, from_addr
    if swapped and _is_old(from_addr, cfg):
        from_addr = user or cfg["noreply_user"]
    return (msg.as_bytes() if isinstance(raw, (bytes, bytearray)) else msg.as_string()), from_addr


# ── the smtplib wrapper ──────────────────────────────────────────────────────────────────────────────────────────
class _Mixin:
    _rms_swapped = False
    _rms_user = None
    _rms_busy = False
    _rms_skip = False                                          # keep_sender(server): this connection is left exactly as the script wrote it

    def login(self, user, password, *a, **k):
        if self._rms_skip:
            return super(_Mixin, self).login(user, password, *a, **k)
        try:
            cfg = get_config()
            if effective(cfg) == "noreply" and _is_old(user, cfg):
                nu, npw = noreply_creds(cfg)
                if npw:
                    try:
                        r = super(_Mixin, self).login(nu, npw, *a, **k)
                        self._rms_swapped, self._rms_user = True, nu
                        return r
                    except smtplib.SMTPAuthenticationError as e:
                        _log(f"noreply login refused ({e.smtp_code}) - sending as {user} this time")
                        try:
                            self.ehlo()
                        except Exception:
                            pass
                else:
                    _log("NOREPLY_SMTP_PASS is not set - sending as " + str(user) + " this time")
        except smtplib.SMTPException:
            raise
        except Exception as e:                               # never let this module break a mail
            _log(f"login hook skipped: {type(e).__name__}: {e}")
        return super(_Mixin, self).login(user, password, *a, **k)

    def send_message(self, msg, from_addr=None, to_addrs=None, *a, **k):
        if self._rms_skip:
            return super(_Mixin, self).send_message(msg, from_addr, to_addrs, *a, **k)
        try:
            prepare(msg, self._rms_swapped, self._rms_user)
            if self._rms_swapped and from_addr and _is_old(from_addr):
                from_addr = self._rms_user
        except Exception as e:
            _log(f"note/sender step skipped: {type(e).__name__}: {e}")
        self._rms_busy = True
        try:
            return super(_Mixin, self).send_message(msg, from_addr, to_addrs, *a, **k)
        finally:
            self._rms_busy = False

    def sendmail(self, from_addr, to_addrs, msg, *a, **k):
        if self._rms_skip:
            return super(_Mixin, self).sendmail(from_addr, to_addrs, msg, *a, **k)
        if not self._rms_busy:                               # send_message() calls sendmail() itself: already handled
            try:
                msg, from_addr = prepare_raw(msg, from_addr, self._rms_swapped, self._rms_user)
            except Exception as e:
                _log(f"note/sender step skipped: {type(e).__name__}: {e}")
        elif self._rms_swapped and _is_old(from_addr):
            from_addr = self._rms_user
        return super(_Mixin, self).sendmail(from_addr, to_addrs, msg, *a, **k)


def keep_sender(server):
    """Call on a freshly created smtplib connection that must stay EXACTLY as the script wrote it: no sender change, no note,
    no signature (used for the Darwinbox mailer: people reply to Sandeep there). Harmless if the wrapper is not installed."""
    try:
        server._rms_skip = True
    except Exception:
        pass
    return server


def install():
    """Wrap smtplib.SMTP and smtplib.SMTP_SSL (once per process)."""
    with _lock:
        for name in ("SMTP", "SMTP_SSL"):
            orig = getattr(smtplib, name)
            if getattr(orig, "_rms_identity", False):
                continue
            w = type(name, (_Mixin, orig), {"_rms_identity": True})
            setattr(smtplib, name, w)
    return True
