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

  Mails of other people (Sandeep, Anand ...) are never touched: only mails logged in as / sent from OLD_USER.

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
NEW_USER = "noreply_rms@mastertrust.co.in"
SWITCH_DATE = "2026-12-01"
MARK = "<!--rms-sender-note-->"
TEXT_MARK = "[rms-sender-note]"
_lock = threading.Lock()


# ── settings ──────────────────────────────────────────────────────────────────────────────────────────────────
def _defaults():
    return {"mode": "auto", "switch_date": SWITCH_DATE, "old_user": OLD_USER, "noreply_user": NEW_USER,
            "reply_to": OLD_USER, "note_date_text": "1 Dec 2026"}


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


def noreply_creds(cfg=None):
    cfg = cfg or get_config()
    return (_env("NOREPLY_SMTP_USER") or cfg["noreply_user"]), (_env("NOREPLY_SMTP_PASS") or _env("NOREPLY_SMTP_PASSWORD"))


def describe():
    cfg = get_config()
    eff = effective(cfg)
    user, pw = noreply_creds(cfg)
    return {"mode": cfg["mode"], "effective": eff, "switch_date": cfg["switch_date"], "sender": user if eff == "noreply" else cfg["old_user"],
            "noreply_user": user, "noreply_ready": bool(pw),
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


def put_note_html(html, cfg=None):
    """Insert the note box just above the signature ('Regards' / 'Thanks' ...). No signature: just before </body>, else at the end."""
    if MARK in html:
        return html
    box = note_html(cfg)
    last = None
    for m in _SIG_HTML.finditer(html):
        last = m
    if last is not None:
        start = last.start()
        win = html[max(0, start - 200):start]
        cut = max(win.lower().rfind(t) for t in ("<br", "<p", "<div", "<table"))
        pos = start - (len(win) - cut) if cut >= 0 else start
        lt = html.rfind("<", 0, start)                       # never split a tag that holds the word
        gt = html.rfind(">", 0, start)
        if lt > gt:
            pos = lt
        return html[:pos] + box + html[pos:]
    i = html.lower().rfind("</body>")
    return html[:i] + box + html[i:] if i >= 0 else html + box


def put_note_text(text, cfg=None):
    if TEXT_MARK in text or "Note: From" in text and "safe sender" in text:
        return text
    line = note_text(cfg) + "\n\n"
    last = None
    for m in _SIG_TEXT.finditer(text):
        last = m
    if last is not None:
        return text[:last.start()] + line + text[last.start():]
    return text.rstrip("\n") + "\n\n" + line.rstrip("\n") + "\n"


# ── rewriting one message ────────────────────────────────────────────────────────────────────────────────────────
def _is_old(addr, cfg=None):
    return parseaddr(str(addr or ""))[1].strip().lower() == (cfg or get_config())["old_user"].lower()


def _inject_note(msg, cfg):
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
        new = put_note_html(text, cfg) if ct == "text/html" else put_note_text(text, cfg)
        if new == text:
            continue
        if "Content-Transfer-Encoding" in part:
            del part["Content-Transfer-Encoding"]
        part.set_payload(new, charset="utf-8")
        changed = True
    return changed


def _to_noreply(msg, cfg, user):
    name = parseaddr(str(msg["From"] or ""))[0]
    del msg["From"]
    msg["From"] = formataddr((name, user)) if name else user
    if not msg["Reply-To"]:
        msg["Reply-To"] = cfg["reply_to"]


def prepare(msg, swapped, user=None, cfg=None):
    """Apply the note / the sender change to an email.message.Message in place. Returns True when it changed anything."""
    cfg = cfg or get_config()
    if not _is_old(msg["From"], cfg):
        return False
    if swapped:
        _to_noreply(msg, cfg, user or cfg["noreply_user"])
        return True
    if effective(cfg) == "kartavya":
        return _inject_note(msg, cfg)
    return False


def prepare_raw(raw, from_addr, swapped, user=None):
    """Same for sendmail(): raw is a str or bytes message. Returns (raw, from_addr)."""
    cfg = get_config()
    head = raw[:4000].decode("latin-1", "replace") if isinstance(raw, (bytes, bytearray)) else raw[:4000]
    if cfg["old_user"].lower() not in head.lower():
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

    def login(self, user, password, *a, **k):
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
                        _log(f"noreply login refused ({e.smtp_code}) - sending as {cfg['old_user']} this time")
                        try:
                            self.ehlo()
                        except Exception:
                            pass
                else:
                    _log("NOREPLY_SMTP_PASS is not set - sending as " + cfg["old_user"] + " this time")
        except smtplib.SMTPException:
            raise
        except Exception as e:                               # never let this module break a mail
            _log(f"login hook skipped: {type(e).__name__}: {e}")
        return super(_Mixin, self).login(user, password, *a, **k)

    def send_message(self, msg, from_addr=None, to_addrs=None, *a, **k):
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
        if not self._rms_busy:                               # send_message() calls sendmail() itself: already handled
            try:
                msg, from_addr = prepare_raw(msg, from_addr, self._rms_swapped, self._rms_user)
            except Exception as e:
                _log(f"note/sender step skipped: {type(e).__name__}: {e}")
        elif self._rms_swapped and _is_old(from_addr):
            from_addr = self._rms_user
        return super(_Mixin, self).sendmail(from_addr, to_addrs, msg, *a, **k)


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
