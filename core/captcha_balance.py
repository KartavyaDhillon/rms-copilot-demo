"""core/captcha_balance.py - e-mail a warning when the 2Captcha balance runs low, so the automatic captcha filling never stops.

Every hour (timer started with the server) the balance is read from 2Captcha (API v2 getBalance - it only reads, it costs
nothing). When it is at or below CAPTCHA_LOW_BALANCE (default 0.80 USD) a mail goes to CAPTCHA_ALERT_TO / CAPTCHA_ALERT_CC
(set them with the "Balance alert mail" button in the Process Center; blank To = nothing can be sent and the card says so).

Not to flood anybody: one mail when it first drops to the limit, then a reminder every CAPTCHA_ALERT_REPEAT_H hours (default 6)
while it is still low, and one "balance is back" note is NOT sent (the next hourly check simply resets). The API key never
appears in a log or a mail.
"""
from __future__ import annotations

import json
import os
import smtplib
import threading
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

DEFAULT_LIMIT = 0.80
_TICK = {"thread": None, "stop": threading.Event()}
_LOCK = threading.Lock()


def limit() -> float:
    try:
        return float(os.getenv("CAPTCHA_LOW_BALANCE", str(DEFAULT_LIMIT)))
    except ValueError:
        return DEFAULT_LIMIT


def repeat_hours() -> float:
    try:
        return max(1.0, float(os.getenv("CAPTCHA_ALERT_REPEAT_H", "6")))
    except ValueError:
        return 6.0


def _state_path() -> Path:
    v = os.getenv("CAPTCHA_BALANCE_STATE", "").strip()
    if v:
        return Path(v)
    try:
        from core.scheduler import paths
        base = paths.data_root()
    except Exception:
        base = Path(__file__).resolve().parent.parent / "data"
    Path(base).mkdir(parents=True, exist_ok=True)
    return Path(base) / "captcha_balance.json"


def read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(st: dict):
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def split_addrs(s):
    import re
    return [a.strip() for a in re.split(r"[;,\s]+", s or "") if a.strip() and "@" in a]


def fetch_balance(client=None) -> float:
    """The balance in USD. Raises RuntimeError with a plain message (never containing the key)."""
    from core import captcha_solver as cs
    key = cs.api_key()
    if not key:
        raise RuntimeError("no 2Captcha key (APIKEY_2CAPTCHA) in .env")
    c = client or cs.V2Client(key)
    try:
        return float(c.balance())
    except cs.SolverError as e:
        raise RuntimeError(f"2Captcha said: {e}") from None
    except Exception as e:                                    # noqa: BLE001
        raise RuntimeError(c.scrub(f"{type(e).__name__}: {e}")) from None


def send_alert(balance: float, to_list, cc_list, sender=None, test: bool = False) -> None:
    host, port = os.getenv("SMTP_SERVER", "smtp3.netcore.co.in"), int(os.getenv("SMTP_PORT", "587"))
    user, pwd = os.getenv("SENDER_EMAIL"), os.getenv("SENDER_PASSWORD")
    if not to_list:
        raise RuntimeError("no 'To' address set for the balance alert")
    if not (user and pwd):
        raise RuntimeError("SENDER_EMAIL / SENDER_PASSWORD are not set in .env")
    banner = ("<p style='background:#e8f0fe;padding:8px;border:1px solid #9db7e8'><b>THIS IS A TEST E-MAIL.</b> "
              "The balance below is the real current balance; the real alert looks exactly like this.</p>") if test else ""
    body = (f"<html><body style='font-family:Segoe UI,Arial,sans-serif;font-size:14px'>{banner}"
            f"<div style='background:#fdecea;border:2px solid #d93025;padding:12px 16px;margin-bottom:12px'>"
            f"<span style='color:#d93025;font-size:18px;font-weight:bold'>&#9888; WARNING: 2CAPTCHA BALANCE IS LOW - REFILL IMMEDIATELY</span><br>"
            f"<span style='font-size:15px'>Current balance: <b>${balance:.2f}</b> &nbsp;(alert limit ${limit():.2f})</span></div>"
            f"<p>Dear Team,</p>"
            f"<p><b>Please refill the 2Captcha account immediately</b> (2captcha.com &rarr; Add funds) so that all automated "
            f"processes keep running smoothly.</p>"
            f"<p>If the balance reaches zero, automatic captcha filling for NMASS / NCDEX stops and the dependent processes "
            f"(Live Allocation, MCX Cash Maintenance, downloads, etc.) will fail or wait for manual captcha entry.</p>"
            f"<p style='color:#666;font-size:12px'>Checked at {datetime.now():%d-%m-%Y %H:%M}. The balance is checked every hour; "
            f"while it stays low a reminder is sent every {repeat_hours():g} hours.</p>"
            f"<p>Regards,<br><b>RMS COPILOT</b><br><span style='color:#666;font-size:12px'>(Automated message - please do not reply)</span></p>"
            f"</body></html>")
    msg = MIMEMultipart()
    msg["From"], msg["To"], msg["Cc"] = user, ", ".join(to_list), ", ".join(cc_list)
    msg["Subject"] = ("[TEST] " if test else "") + f"WARNING: 2Captcha balance low (${balance:.2f}) - refill immediately"
    msg.attach(MIMEText(body, "html"))
    srv = (sender or smtplib.SMTP)(host, port, timeout=30)
    try:
        srv.starttls()
        srv.login(user, pwd)
        srv.send_message(msg, to_addrs=list(to_list) + list(cc_list))
    finally:
        try:
            srv.quit()
        except Exception:
            pass


def send_test(client=None, mailer=None) -> dict:
    """Send the alert mail right now, marked [TEST], to the saved To / CC - so you can see what it looks like. Uses the real balance."""
    to, cc = split_addrs(os.getenv("CAPTCHA_ALERT_TO", "")), split_addrs(os.getenv("CAPTCHA_ALERT_CC", ""))
    bal = fetch_balance(client)
    (mailer or (lambda b, t, c: send_alert(b, t, c, test=True)))(bal, to, cc)
    return {"ok": True, "balance": round(bal, 2), "to": to, "cc": cc}


def check(now: datetime = None, client=None, mailer=None, log=print) -> dict:
    """One look at the balance; mails when due. Never raises. -> the saved state."""
    now = now or datetime.now()
    with _LOCK:
        st = read_state()
        st["checked_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        st["limit"] = limit()
        try:
            bal = fetch_balance(client)
        except RuntimeError as e:
            st["error"] = str(e)
            log(f"[captcha-balance] could not read the balance: {e}")
            _write_state(st)
            return st
        st.pop("error", None)
        st["balance"] = round(bal, 2)
        low = bal <= limit()
        st["low"] = low
        if not low:
            st.pop("last_alert", None)                         # recharged: the next drop alerts at once
            st.pop("alert_error", None)
        else:
            last = st.get("last_alert", "")
            due = True
            if last:
                try:
                    due = (now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() >= repeat_hours() * 3600
                except ValueError:
                    due = True
            if due:
                to, cc = split_addrs(os.getenv("CAPTCHA_ALERT_TO", "")), split_addrs(os.getenv("CAPTCHA_ALERT_CC", ""))
                try:
                    (mailer or send_alert)(bal, to, cc)
                    st["last_alert"] = now.strftime("%Y-%m-%d %H:%M:%S")
                    st.pop("alert_error", None)
                    log(f"[captcha-balance] balance ${bal:.2f} - alert mailed to {', '.join(to)}")
                except Exception as e:                         # noqa: BLE001 - retried at the next hourly check
                    st["alert_error"] = f"{type(e).__name__}: {e}"[:200]
                    log(f"[captcha-balance] balance ${bal:.2f} but the alert mail failed: {e}")
        _write_state(st)
        return st


def start_ticker(interval_s: int = 3600, first_delay_s: int = 60):
    """Hourly check in the background (idempotent). The first look is a minute after the server starts."""
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return False
    _TICK["stop"].clear()

    def loop():
        print("[captcha-balance] hourly 2Captcha balance check started.", flush=True)
        wait = first_delay_s
        while not _TICK["stop"].wait(wait):
            try:
                check()
            except Exception as e:                             # noqa: BLE001
                print(f"[captcha-balance] error: {type(e).__name__}: {e}", flush=True)
            wait = interval_s
    th = threading.Thread(target=loop, name="captcha-balance", daemon=True)
    _TICK["thread"] = th
    th.start()
    return True


def stop_ticker():
    _TICK["stop"].set()
