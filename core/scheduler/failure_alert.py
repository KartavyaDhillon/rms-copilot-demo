"""GLOBAL FAILURE ALERT — when ANY scheduled process fails for good, a warning image goes out on WhatsApp.

How it fits together
--------------------
* The scheduler engine (engine._execute) calls notify() once a run has FAILED and no retry is waiting — so a
  process that still has retries left stays quiet until its LAST attempt fails (no spam, no false alarms).
* The image is drawn here (Pillow), the same for every process: red banner, process name, schedule, time, reason in
  plain words, what to do. Only the details change.
* It is sent with the WhatsApp connection already set under Credentials (auth token, upload / send URL). The
  template's two body variables carry a short label ("FAILED - <PROCESS>") and the date-time; the picture carries
  everything else.
* WHO gets it: every process has its own list (its 📱 WhatsApp numbers button on the Processes tab, saved per
  process in .env). Only when a process has none, the common list in Credentials > WhatsApp is used.
* Settings (Credentials > WhatsApp): alert_switch (on/off for everything), alert_numbers (common fallback list),
  alert_template (blank = reuse the connection's own template).

Safety: notify() NEVER raises — an alert problem must not hide the real failure or break the scheduler. Every attempt
(sent / skipped / failed) is appended to <scheduler data>/failure_alerts.jsonl.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

DEDUPE_MINUTES = 60                 # the same schedule alerts at most once per hour
_LAST: dict = {}                    # schedule id -> epoch seconds of the last alert
_LOCK = threading.Lock()

RED, DARK, GREY = "#C62828", "#212121", "#616161"
W = 1080


# ───────────────────────── settings ─────────────────────────
def _settings() -> dict:
    from core.scheduler import connections
    return connections.get("whatsapp")


def parse_numbers(text: str) -> List[str]:
    """'9812345678 #Ravi, +919876543210' -> ['+919812345678', '+919876543210'] (the format the other WhatsApp
    processes use). Anything that is not a valid number is skipped, never an error."""
    from core.scheduler import helpers as H
    out: List[str] = []
    for part in re.split(r"[,;\n]+", text or ""):
        try:
            for n in H.parse_phones(part):
                if n not in out:
                    out.append(n)
        except ValueError:
            continue
    return out


def numbers_for(pdef, settings: dict) -> Tuple[List[str], str]:
    """(numbers, where they came from): the PROCESS' own list first; the common list in Credentials > WhatsApp
    only when the process has none."""
    try:
        from core.scheduler import catalog, proc_settings
        own = parse_numbers(proc_settings.get(pdef).get(catalog.ALERT_PARAM, ""))
    except Exception:
        own = []
    if own:
        return own, "this process"
    return parse_numbers(settings.get("alert_numbers", "")), "common list"


def _switch_on(value: str) -> bool:
    return str(value or "on").strip().lower() in ("on", "yes", "y", "true", "1")


# ───────────────────────── the reason, in plain words ─────────────────────────
_REASONS = [
    (r"server stopped or restarted", "The console was stopped or restarted while this was running."),
    (r"scheduler was not running|Missed", "The console / scheduler was not running at the scheduled time."),
    (r"No Allocation_Logs|decoded to 0 bytes|0 bytes", "No allocation data could be built - the e-mails arrived empty or are missing."),
    (r"timed? ?out|timeout", "The server did not answer in time (timeout)."),
    (r"401|403|login|password|credential|authenticat|unauthori[sz]ed|refused", "Login or credentials were refused."),
    (r"\b5\d\d\b|bad gateway|service unavailable|gateway", "The remote server returned an error (it is down or busy)."),
    (r"connection|resolve|network|unreachable|ssl", "Network problem - the server could not be reached."),
    (r"smtp|mail", "The e-mail could not be sent."),
    (r"permission|being used by another|locked|open in", "A file is locked - it is probably open in another program."),
    (r"not found|no such file|not available|missing|does not exist", "A required file or record was not found."),
]


def friendly_reason(etype: str, emsg: str) -> Tuple[str, str]:
    """(headline, technical detail) — both one line, safe for an image."""
    blob = f"{etype} {emsg}"
    headline = "The process stopped with an error."
    for pat, text in _REASONS:
        if re.search(pat, blob, re.I):
            headline = text
            break
    detail = re.sub(r"\s+", " ", (emsg or etype or "")).strip()
    return headline, (f"{etype}: {detail}" if etype and not detail.startswith(etype) else detail)[:240]


# ───────────────────────── the image ─────────────────────────
def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = (["arialbd.ttf", "C:/Windows/Fonts/arialbd.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["arial.ttf", "C:/Windows/Fonts/arial.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> List[str]:
    lines: List[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split():
            test = (cur + " " + word).strip()
            if draw.textlength(test, font=font) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                while draw.textlength(word, font=font) > max_w:      # a single very long word: cut it
                    cut = len(word)
                    while cut > 1 and draw.textlength(word[:cut], font=font) > max_w:
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                cur = word
        lines.append(cur)
    return lines or [""]


KINDS = {   # kind -> (banner title, banner sub-title, status line, "when" label, WhatsApp label prefix)
    "FAILED": ("SCHEDULER FAILED", "A scheduled process did NOT complete", "FAILED - not completed", "Failed at", "FAILED"),
    "INTERRUPTED": ("RUN INTERRUPTED", "The server stopped while a process was running", "INTERRUPTED - may be half done", "Stopped at", "INTERRUPTED"),
    "MISSED": ("RUN MISSED", "A scheduled run did NOT start", "MISSED - not run", "Noticed at", "MISSED"),
}


def render_image(out: Path, *, process: str, schedule: str, failed_at: str, started: str, trigger: str,
                 attempt_text: str, headline: str, detail: str, run_id: str, kind: str = "FAILED") -> Path:
    """Draw the alert. Height grows with the text so nothing is ever cut off."""
    from PIL import Image, ImageDraw
    probe = ImageDraw.Draw(Image.new("RGB", (W, 10)))
    f_lbl, f_val, f_big, f_txt, f_small = _font(28, True), _font(40, True), _font(68, True), _font(36), _font(28)

    title, subtitle, status_text, when_lbl, _pre = KINDS.get(kind, KINDS["FAILED"])
    # Kept deliberately SHORT and plain for non-technical readers: which process, when, what happened, what to do.
    # (Schedule name, trigger, attempt, run id and the technical error stay in the run's log - not on the picture.)
    rows = [("Process", process), (when_lbl, failed_at), ("Status", status_text)]
    head_lines = _wrap(probe, headline, f_txt, W - 160)
    det_lines: List[str] = []
    todo = {"INTERRUPTED": ["1. This process was stopped in the middle.",
                            "2. It may be only half done.",
                            "3. Please inform the process owner."],
            "MISSED": ["1. This process did not start at its time.",
                       "2. Its work has not been done.",
                       "3. Please inform the process owner."]}.get(kind, [
            "1. This process did not finish its work.",
            "2. Its report / file may be missing or incomplete.",
            "3. Please inform the process owner."])

    row_h = 92
    proc_lines = {i: _wrap(probe, v, f_val, W - 160) for i, (_k, v) in enumerate(rows)}
    extra = sum((len(v) - 1) * 46 for v in proc_lines.values()) + 12 + 10 * len(proc_lines[0])
    card1 = 40 + len(rows) * row_h + extra
    card2 = 70 + len(head_lines) * 52 + 30
    card3 = 80 + len(todo) * 52 + 24
    H = 300 + 40 + card1 + 30 + card2 + 30 + card3 + 100

    im = Image.new("RGB", (W, H), "#F4F4F4")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 300], fill=RED)
    cx, cy = 150, 150
    d.polygon([(cx, cy - 85), (cx + 95, cy + 80), (cx - 95, cy + 80)], fill="white")
    d.text((cx, cy + 25), "!", font=_font(110, True), fill=RED, anchor="mm")
    d.text((290, 90), title, font=f_big, fill="white")
    d.text((290, 185), subtitle, font=_font(36), fill="#FFE0E0")

    def card(y, h, fill="white", outline="#DDDDDD", width=2):
        d.rounded_rectangle([40, y, W - 40, y + h], radius=24, fill=fill, outline=outline, width=width)

    y0 = 340
    card(y0, card1)
    y = y0 + 34
    for i, (k, v) in enumerate(rows):
        d.text((80, y), k.upper(), font=f_lbl, fill=GREY)
        y += 38
        for ln in proc_lines[i]:
            d.text((80, y), ln, font=(_font(52, True) if k == "Process" else f_val), fill=RED if k == "Status" else DARK)
            y += 58 if k == "Process" else 46
        y += row_h - 38 - 46

    y0 = y0 + card1 + 30
    card(y0, card2)
    d.text((80, y0 + 28), "REASON", font=f_lbl, fill=GREY)
    y = y0 + 76
    for ln in head_lines:
        d.text((80, y), ln, font=f_txt, fill=DARK)
        y += 52

    y0 = y0 + card2 + 30
    card(y0, card3, fill="#FFF8E1", outline="#FFB300", width=3)
    d.text((80, y0 + 26), "WHAT TO DO", font=_font(30, True), fill="#E65100")
    y = y0 + 80
    for t in todo:
        d.text((80, y), t, font=_font(32), fill=DARK)
        y += 52

    d.text((W // 2, H - 50), "RMS Copilot  |  Master Capital Services Ltd  |  Automated alert",
           font=_font(24), fill=GREY, anchor="mm")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


# ───────────────────────── the entry point ─────────────────────────
def _fmt(ts) -> str:
    try:
        return ts.astimezone().strftime("%d-%b-%Y %H:%M:%S")
    except Exception:
        return str(ts or "-")


def _log(rec: dict) -> None:
    try:
        from core.scheduler import paths
        p = paths.sched_root() / "failure_alerts.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


class _Ctx:                                  # the WhatsApp helper only needs a .log()
    def log(self, msg):
        print(f"[failure-alert] {msg}", flush=True)


def notify(schedule: dict, pdef, execution_id: str, etype: str, emsg: str, ended, trigger: str,
           attempt: int, log_text: str = "", now: Optional[float] = None, kind: str = "FAILED") -> dict:
    """Build the image and WhatsApp it. Returns {'sent': bool, 'reason': str, ...}. Never raises."""
    rec = {"at": dt.datetime.now().isoformat(timespec="seconds"), "execution": execution_id,
           "schedule": (schedule or {}).get("name"), "process": getattr(pdef, "name", None)}
    try:
        c = _settings()
        numbers, source = numbers_for(pdef, c)
        if not _switch_on(c.get("alert_switch")):
            rec.update(sent=False, reason="alert switch is off")
        elif not numbers:
            rec.update(sent=False, reason="no alert numbers set (process WhatsApp-numbers button, or Credentials > WhatsApp)")
        elif not c.get("auth_token"):
            rec.update(sent=False, reason="WhatsApp auth token not set")
        else:
            sid = str((schedule or {}).get("id") or "")
            t = time.time() if now is None else now
            with _LOCK:
                last = _LAST.get(sid + ":" + kind)
                if last is not None and t - last < DEDUPE_MINUTES * 60:
                    rec.update(sent=False, reason=f"already alerted for this schedule in the last {DEDUPE_MINUTES} min")
                    _log(rec)
                    return rec
                _LAST[sid + ":" + kind] = t
            process = getattr(pdef, "name", None) or (schedule or {}).get("process_key") or "Unknown process"
            head, detail = friendly_reason(etype, emsg)
            started = ""
            try:
                from core.scheduler import store
                row = store.get_execution(execution_id) or {}
                started = _fmt(store.parse_ts(row["started_utc"])) if row.get("started_utc") else ""
            except Exception:
                pass
            trig = {"MANUAL_RUN_NOW": "Run Now (manual)", "SCHEDULED": "Scheduled", "RETRY": "Retry"}.get(
                str(trigger or ""), str(trigger or "-").replace("_", " ").title())
            from core.scheduler import paths, helpers as H
            img = render_image(
                paths.sched_root() / "alerts" / f"{kind.lower()}_{re.sub(r'[^A-Za-z0-9]+', '_', process)[:40]}_{int(t)}.png",
                process=process, schedule=str((schedule or {}).get("name") or process), failed_at=_fmt(ended),
                started=started or "-", trigger=trig, attempt_text=(f"{attempt}" + (" (final)" if kind == "FAILED" else "")) if attempt else "-",
                headline=head, detail=detail, run_id=str(execution_id), kind=kind)
            label = re.sub(r"\s+", " ", f"{KINDS.get(kind, KINDS['FAILED'])[4]} - {process}".upper())[:60]
            date_text = dt.datetime.now().strftime("%d-%m-%Y %H:%M")
            ok, bad = [], []
            for n in numbers:
                try:
                    H.whatsapp_send_image(img, n, label, date_text, _Ctx(),
                                          template_name=(c.get("alert_template") or "").strip() or None)
                    ok.append(n[-4:])
                except Exception as e:
                    bad.append(f"{n[-4:]}: {type(e).__name__}: {str(e)[:120]}")
            rec.update(sent=bool(ok), reason="sent" if ok else "all sends failed", image=str(img), numbers_from=source,
                       to_ok=ok, to_failed=bad)
    except Exception as e:                                     # never let an alert problem escape
        rec.update(sent=False, reason=f"alert error: {type(e).__name__}: {e}")
    _log(rec)
    print(f"[scheduler] failure alert: {rec.get('reason')}", flush=True)
    return rec


def notify_execution(eid: str, kind: str) -> dict:
    """Alert for a STORED run (e.g. one just marked 'Interrupted'). Never raises."""
    try:
        from core.scheduler import catalog, store
        row = store.get_execution(eid) or {}
        sched = store.get_schedule(row.get("schedule_id")) or {"id": row.get("schedule_id"), "name": row.get("schedule_name"),
                                                               "process_key": row.get("process_key")}
        pdef = catalog.get(row.get("process_key"))
        ended = store.parse_ts(row.get("ended_utc")) if row.get("ended_utc") else None
        return notify(sched, pdef, eid, row.get("error_type") or kind.title(), row.get("error_msg") or "",
                      ended, row.get("trigger_type") or "", int(row.get("attempt") or 0), kind=kind)
    except Exception as e:
        print(f"[scheduler] failure alert ({kind}) could not run: {type(e).__name__}: {e}", flush=True)
        return {"sent": False, "reason": f"{type(e).__name__}: {e}"}


def outcome_line(rec: dict) -> str:
    """One readable line for the run's log: was the WhatsApp failure alert sent, and if not, why."""
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    if rec.get("sent"):
        n = ", ".join("…" + x for x in rec.get("to_ok") or [])
        bad = f" (failed for: {'; '.join(rec['to_failed'])})" if rec.get("to_failed") else ""
        return f"[{stamp}] WhatsApp failure alert SENT to {n}{bad}."
    return f"[{stamp}] WhatsApp failure alert NOT sent - {rec.get('reason', 'unknown reason')}."
