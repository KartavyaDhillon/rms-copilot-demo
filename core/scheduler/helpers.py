"""Shared building blocks for the scheduled processes (WhatsApp, e-mail, risk report, run logs, markers).

Rules every process follows
---------------------------
* Failure = raise.  Never swallow an error and "finish" — the scheduler would show Success.
* The RISK_REPORT is READ from COMMON FILES and never moved, renamed or deleted.
* Outbound sends (WhatsApp / e-mail) are NOT blindly retried forever: a few tries on network trouble, then fail.
* A scheduled / missed-run-recovery run will not send the same thing twice in a day (marker file);
  an explicit Run Now is allowed to send again (the UI asks for confirmation).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from core.scheduler import connections, paths

MANUAL = "MANUAL_RUN_NOW"


# ───────────────────────── small utilities ─────────────────────────
def today_ddmmyyyy() -> str:
    return dt.datetime.now().strftime("%d%m%Y")


def today_dash() -> str:
    return dt.datetime.now().strftime("%d-%m-%Y")


def parse_list(text, split_spaces: bool = True) -> List[str]:
    """'a@x.com, b@x.com;c' / newline separated → ['a@x.com','b@x.com','c'] (no blanks, no duplicates)."""
    if isinstance(text, (list, tuple)):
        parts = [str(x) for x in text]
    else:
        parts = re.split(r"[,\s;]+" if split_spaces else r"[,;\r\n]+", str(text or ""))
    out, seen = [], set()
    for p in parts:
        p = p.strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def parse_phones(text) -> List[str]:
    out = []
    for p in parse_list(text, split_spaces=False):          # "+91 98123 45678" is one number
        d = re.sub(r"[^\d+]", "", p)
        if not re.fullmatch(r"\+\d{10,15}", d):
            raise ValueError(f"'{p}' is not a valid phone number — use the international form, e.g. +919812345678.")
        out.append(d)
    return out


def parse_emails(text) -> List[str]:
    out = parse_list(text)
    for e in out:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
            raise ValueError(f"'{e}' is not a valid e-mail address.")
    return out


def need(mod: str, pip: str = ""):
    """Import a third-party module or raise a plain message."""
    try:
        return __import__(mod, fromlist=["x"])
    except ImportError:
        raise RuntimeError(f"Python package '{pip or mod}' is not installed on the server — run: pip install {pip or mod}")


# ───────────────────────── risk report (read-only, COMMON FILES) ─────────────────────────
def common_dir() -> Path:
    return Path(os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES"))


def find_risk_report(ctx=None) -> Path:
    """Today's RISK_REPORT_<DDMMYYYY>.csv from COMMON FILES; if the console already rolled it into today's dated
    backup folder (COMMON FILES\\BACKUP FILES AUTOMATED\\<dd-mm-yyyy>) that copy is used.  Only today's exact
    name is ever accepted.  The file is never moved or deleted."""
    name = f"RISK_REPORT_{today_ddmmyyyy()}.csv"
    cands = [common_dir() / name, common_dir() / "BACKUP FILES AUTOMATED" / today_dash() / name]
    for c in cands:
        if c.is_file():
            if ctx:
                ctx.log(f"Risk report: {c}")
            return c
    raise FileNotFoundError(f"Today's risk report ({name}) was not found in {common_dir()} — "
                            f"upload it to COMMON FILES first.")


# ───────────────────────── run log + folders ─────────────────────────
def logged(folder: str):
    """Decorator for a process run(params, ctx): makes sure its folders exist and writes a log file to
    data/scheduler/<folder>/logs/ whatever happens (success or failure)."""
    def deco(fn: Callable):
        def wrapper(params, ctx):
            paths.ensure_process_dirs(folder)
            err = None
            try:
                return fn(params, ctx)
            except Exception as e:
                err = e
                ctx.log(f"FAILED: {type(e).__name__}: {e}")
                raise
            finally:
                try:
                    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
                    p = paths.proc_dir(folder, "logs") / f"{stamp}_{'FAILED' if err else 'OK'}.log"
                    p.write_text(ctx.text() + "\n", encoding="utf-8")
                    _trim_logs(paths.proc_dir(folder, "logs"))
                except Exception:
                    pass
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def _trim_logs(d: Path, keep: int = 120):
    files = sorted(d.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[keep:]:
        try:
            p.unlink()
        except Exception:
            pass


# ───────────────────────── "already sent today" markers ─────────────────────────
def _marker_file(folder: str) -> Path:
    return paths.proc_dir(folder, "state") / f"sent_{today_ddmmyyyy()}.json"


def already_sent(folder: str, key: str, ctx) -> bool:
    """True when a scheduled/recovery run must skip because this exact send already succeeded today."""
    if getattr(ctx, "trigger", "") == MANUAL:
        return False
    try:
        return key in json.loads(_marker_file(folder).read_text(encoding="utf-8"))
    except Exception:
        return False


def mark_sent(folder: str, key: str):
    f = _marker_file(folder)
    try:
        cur = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        cur = []
    if key not in cur:
        cur.append(key)
    f.write_text(json.dumps(cur), encoding="utf-8")
    for old in f.parent.glob("sent_*.json"):              # keep only the last 7 days of markers
        if old != f and (time.time() - old.stat().st_mtime) > 7 * 86400:
            try:
                old.unlink()
            except Exception:
                pass


# ───────────────────────── backup (never touches the risk report) ─────────────────────────
def backup_files(folder: str, files: Iterable[Path], ctx) -> Path:
    dest = paths.proc_dir(folder, "backup") / today_dash()
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        f = Path(f)
        if not f.is_file():
            continue
        if f.name.upper().startswith("RISK_REPORT"):
            continue                                       # never move the shared risk report
        target = dest / f.name
        if target.exists():
            target = dest / f"{f.stem}_{dt.datetime.now():%H%M%S}{f.suffix}"
        f.replace(target)
        ctx.log(f"Backed up: {f.name} → backup/{dest.name}/")
    return dest


# ───────────────────────── WhatsApp (Tata Tele Omni) ─────────────────────────
def whatsapp_send_image(image: Path, to: str, label: str, date_text: str, ctx,
                        attempts: int = 4, delay: float = 15.0, template_extra: Optional[list] = None):
    """Upload `image` and send it with the configured template ({label}, {date_text} as the two body variables).
    Retries ONLY on network trouble / 429 / 5xx, at most `attempts` times; any other answer fails at once."""
    requests = need("requests")
    c = connections.require("whatsapp", "auth_token", "upload_url", "send_url", "template_name")
    auth = {"Authorization": f"Bearer {c['auth_token']}"}

    def _post(fn_desc, call):
        last = ""
        for i in range(1, attempts + 1):
            try:
                r = call()
                if r.status_code in (200, 201):
                    return r
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"WhatsApp {fn_desc} refused — {last}")
            except RuntimeError:
                raise
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            ctx.log(f"WhatsApp {fn_desc} attempt {i}/{attempts} failed ({last})")
            if i < attempts:
                time.sleep(delay)
        raise RuntimeError(f"WhatsApp {fn_desc} failed after {attempts} tries — {last}")

    def up():
        with open(image, "rb") as fh:
            return requests.post(c["upload_url"], headers={**auth, "accept": "application/json"},
                                 files={"file": (image.name, fh, "image/jpeg" if image.suffix.lower() in (".jpg", ".jpeg") else "image/png")},
                                 timeout=60)
    r = _post("upload", up)
    j = r.json()
    media_id = j.get("id") or j.get("mediaHandle")
    if not media_id:
        raise RuntimeError("WhatsApp upload answered without a media id.")
    ctx.log(f"Image uploaded ({image.name})")

    payload = {"to": to, "type": "template", "source": "external",
               "template": {"name": c["template_name"], "language": {"code": c["template_lang"] or "en"},
                            "components": [
                                {"type": "header", "parameters": [{"type": "image", "image": {"id": media_id}}]},
                                {"type": "body", "parameters": [{"type": "text", "text": label},
                                                                {"type": "text", "text": date_text}]}]}}
    _post("send", lambda: requests.post(c["send_url"], headers={**auth, "Content-Type": "application/json"},
                                        json=payload, timeout=60))
    ctx.log(f"WhatsApp sent to {'*' * (len(to) - 4)}{to[-4:]}")


# ───────────────────────── e-mail (SMTP) ─────────────────────────
def send_email(subject: str, body: str, to: List[str], cc: List[str], attachments: List[Path], ctx,
               html: bool = False, attempts: int = 3, delay: float = 10.0):
    c = connections.require("smtp", "server", "port", "email", "password")
    if not to:
        raise ValueError("No 'To' e-mail address configured for this schedule.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = c["email"]
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if html:
        msg.set_content("Please view this e-mail in an HTML-capable client.")
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)
    for a in attachments:
        a = Path(a)
        sub = "vnd.openxmlformats-officedocument.spreadsheetml.sheet" if a.suffix.lower() == ".xlsx" else "octet-stream"
        msg.add_attachment(a.read_bytes(), maintype="application", subtype=sub, filename=a.name)
    last = ""
    for i in range(1, attempts + 1):
        try:
            with smtplib.SMTP(c["server"], int(c["port"]), timeout=30) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
                s.login(c["email"], c["password"])
                s.send_message(msg, from_addr=c["email"], to_addrs=list(to) + list(cc))
            ctx.log(f"E-mail sent: '{subject}' → To {len(to)} / Cc {len(cc)}")
            return
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(f"E-mail login refused — check the SMTP password in Scheduler → Connections ({e.smtp_code}).")
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            ctx.log(f"E-mail attempt {i}/{attempts} failed ({last})")
            if i < attempts:
                time.sleep(delay)
    raise RuntimeError(f"E-mail could not be sent after {attempts} tries — {last}")
