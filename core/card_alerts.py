"""core/card_alerts.py - WhatsApp FAILURE ALERT for every console card (auto run OR manual run).

* Every card gets a "WhatsApp alerts" button (platform/js/process-wanumbers.js) next to its Credentials button. The numbers
  are saved per card here (data/card_alert_numbers.json). A card with no numbers of its own uses the common list
  (Scheduler > Credentials > WhatsApp > alert numbers); the global alert switch there turns everything off.
* Cards that already alert by themselves (they keep their own list in their Settings / Auto Run window) are NOT watched here,
  so nobody gets two pictures: OWN_ALERT below.
* All the other cards are covered by ONE watcher: every minute it looks at the run logs of the last day (the same logs the
  dashboard's "Needs your attention" list reads - core/ops_metrics.py). A run whose log has ended as FAILED and that has not
  been alerted yet sends the same picture the scheduler sends (core/scheduler/failure_alert.py). It makes no difference
  whether the run was started by a timer or by a button. At server start the logs that are already there are only
  remembered, never alerted (no flood after a restart). Same card + same reason: at most one picture per 30 minutes.
Never raises - an alert problem must not hide the real failure.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from pathlib import Path

DEDUPE_S = 30 * 60
QUIET_S = 90                    # a log must not have been written to for this long before it counts as a finished run
SCAN_EVERY_S = 60

# card key (PIPELINES key / button) -> module that keeps its own alert numbers in its Settings (and sends its own alert)
OWN_ALERT = {
    "mtfof": "core.mtf_order_file", "mtfconc": "core.mtf_concentration", "entrisk": "core.enterprise_risk",
    "ncdexcomm": "core.ncdex_comm_limit", "mcxcomm": "core.mcx_comm_limit", "cncf11": "core.cnc_f11_card",
    "ms15": "core.ms15_auto", "payoutchk": "core.payout_check_card",
}
OWN_LOGDIRS = {"mtfof", "mtfconc", "entrisk", "ncdexcomm", "mcxcomm", "cncf11", "ms15", "short_allocation", "payoutchk"}

_LOCK = threading.Lock()
_LAST: dict = {}
_TICK = {"thread": None, "stop": threading.Event()}


def _dir() -> Path:
    try:
        from core.scheduler import paths
        base = paths.data_root()
    except Exception:                                            # noqa: BLE001
        base = Path(__file__).resolve().parent.parent / "data"
    p = Path(base) / "card_alerts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _numbers_file() -> Path:
    return _dir() / "card_alert_numbers.json"


def _read() -> dict:
    try:
        return json.loads(_numbers_file().read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return {}


def _parse(raw: str) -> list:
    from core.scheduler import failure_alert
    return failure_alert.parse_numbers(raw or "")


# ───────────────────────────── numbers per card ─────────────────────────────
def get_numbers(pipe: str) -> dict:
    """{'raw': text as typed, 'count': valid numbers, 'own': True when this card keeps its list in its own Settings}."""
    pipe = (pipe or "").strip().lower()
    mod = OWN_ALERT.get(pipe)
    if mod:
        try:
            import importlib
            raw = str(importlib.import_module(mod).get_settings().get("alert_numbers", "") or "")
            return {"pipe": pipe, "raw": raw, "count": len(_parse(raw)), "own": True}
        except Exception:                                        # noqa: BLE001
            pass
    raw = str(_read().get(pipe, "") or "")
    return {"pipe": pipe, "raw": raw, "count": len(_parse(raw)), "own": False}


def set_numbers(pipe: str, raw: str, by: str = "") -> dict:
    pipe = (pipe or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{2,30}", pipe):
        raise ValueError("unknown card")
    raw = str(raw or "").strip()
    if raw and not _parse(raw):
        raise ValueError("those WhatsApp numbers are not valid (10 digits, optionally +91; separate several with commas)")
    mod = OWN_ALERT.get(pipe)
    if mod and pipe != "ms15":
        import importlib
        importlib.import_module(mod).save_settings({"alert_numbers": raw}, by=by)
        return get_numbers(pipe)
    d = _read()
    d[pipe] = raw
    tmp = _numbers_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(_numbers_file())
    return get_numbers(pipe)


def _common_settings() -> dict:
    from core.scheduler import connections
    return connections.get("whatsapp")


def numbers_for(pipe: str):
    own = _parse(get_numbers(pipe)["raw"])
    if own:
        return own, "this card"
    return _parse(_common_settings().get("alert_numbers", "")), "common list"


# ───────────────────────────── sending ─────────────────────────────
def _log(rec: dict):
    try:
        with open(_dir() / "card_alerts.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:                                            # noqa: BLE001
        pass


def notify_failure(pipe: str, name: str, headline: str, detail: str, trigger: str = "", started: str = "",
                   sender=None, now: float = None) -> dict:
    """Picture on WhatsApp for a failed run of card `pipe`. Returns {'sent', 'reason'}. Never raises."""
    rec = {"at": dt.datetime.now().isoformat(timespec="seconds"), "card": pipe, "name": name, "headline": headline}
    try:
        c = _common_settings()
        numbers, source = numbers_for(pipe)
        on = str(c.get("alert_switch") or "on").strip().lower() in ("on", "yes", "y", "true", "1")
        if not on:
            rec.update(sent=False, reason="alert switch is off")
        elif not numbers:
            rec.update(sent=False, reason="no alert numbers set (the card's WhatsApp alerts button, or Credentials > WhatsApp)")
        elif sender is None and not c.get("auth_token"):
            rec.update(sent=False, reason="WhatsApp auth token not set")
        else:
            t = time.time() if now is None else now
            with _LOCK:
                last = _LAST.get(pipe)
                if last is not None and t - last < DEDUPE_S:
                    rec.update(sent=False, reason="already alerted for this card in the last 30 min")
                    _log(rec)
                    return rec
                _LAST[pipe] = t
            if sender is not None:
                sender(numbers, headline, detail)
                rec.update(sent=True, reason="sent", numbers_from=source)
            else:
                from core.scheduler import failure_alert, helpers as H, paths
                nowd = dt.datetime.now()
                img = failure_alert.render_image(
                    paths.sched_root() / "alerts" / f"card_{re.sub(r'[^A-Za-z0-9]+', '_', pipe)}_{int(t)}.png",
                    process=name, schedule="Console card", failed_at=nowd.strftime("%d-%b-%Y %H:%M:%S"), started=started or "-",
                    trigger=trigger or "-", attempt_text="final", headline=headline, detail=detail, run_id=pipe.upper(), kind="FAILED")

                class _C:
                    def log(self, m):
                        print("[card-alert] " + str(m), flush=True)
                label = re.sub(r"\s+", " ", f"FAILED - {name}".upper())[:60]
                ok, bad = [], []
                for n in numbers:
                    try:
                        H.whatsapp_send_image(img, n, label, nowd.strftime("%d-%m-%Y %H:%M"), _C(),
                                              template_name=(c.get("alert_template") or "").strip() or None)
                        ok.append(n[-4:])
                    except Exception as e:                       # noqa: BLE001
                        bad.append(f"{n[-4:]}: {type(e).__name__}: {str(e)[:120]}")
                rec.update(sent=bool(ok), reason="sent" if ok else "all sends failed: " + "; ".join(bad), numbers_from=source,
                           to_ok=ok, to_failed=bad)
    except Exception as e:                                       # noqa: BLE001
        rec.update(sent=False, reason=f"alert error: {type(e).__name__}: {e}")
    _log(rec)
    print(f"[card-alert] {name}: {rec.get('reason')}", flush=True)
    return rec


# ───────────────────────────── the watcher ─────────────────────────────
def _seen_file() -> Path:
    return _dir() / "seen_logs.json"


def _load_seen() -> set:
    try:
        return set(json.loads(_seen_file().read_text(encoding="utf-8")))
    except Exception:                                            # noqa: BLE001
        return set()


def _save_seen(seen: set):
    try:
        _seen_file().write_text(json.dumps(sorted(seen)[-3000:]), encoding="utf-8")
    except Exception:                                            # noqa: BLE001
        pass


def check_once(seen: set, first: bool = False, logs=None, alert=None, now: float = None) -> list:
    """One look at the run logs. `first` = only remember what is there. Returns the alerts that were sent / tried."""
    out = []
    try:
        if logs is None:
            from core import ops_metrics
            logs = ops_metrics._scan_all_logs(1)
        alert = alert or notify_failure
        t = time.time() if now is None else now
        base = None
        try:
            from core import ops_metrics as _om
            base = _om.LOG_BASE
        except Exception:                                        # noqa: BLE001
            pass
        for e in logs:
            key = f"{e['logdir']}/{e['file']}"
            if key in seen:
                continue
            if e.get("logdir") in OWN_LOGDIRS or e.get("pipe") in OWN_ALERT:
                seen.add(key)
                continue
            if first:
                seen.add(key)
                continue
            if base:
                try:
                    if t - os.path.getmtime(os.path.join(base, e["logdir"], e["file"])) < QUIET_S:
                        continue                                 # still being written to - look again next time
                except OSError:
                    pass
            seen.add(key)
            if e.get("status") != "failed":
                continue
            why = (e.get("why") or "").strip() or "the run ended with an error - open the log"
            try:
                from core.scheduler import failure_alert
                head, detail = failure_alert.friendly_reason("", why)
            except Exception:                                    # noqa: BLE001
                head, detail = "The run failed.", why
            out.append(alert(e.get("pipe") or e["logdir"], e.get("pipe_name") or e["logdir"], head, detail,
                             trigger="Auto or manual run", started=e.get("start", "")))
    except Exception as ex:                                      # noqa: BLE001
        print(f"[card-alert] watcher error: {type(ex).__name__}: {ex}", flush=True)
    return out


def start_watcher():
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return
    _TICK["stop"].clear()

    def loop():
        seen = _load_seen()
        check_once(seen, first=True)
        _save_seen(seen)
        while not _TICK["stop"].wait(SCAN_EVERY_S):
            n = len(seen)
            check_once(seen)
            if len(seen) != n:
                _save_seen(seen)
    th = threading.Thread(target=loop, name="card-alert-watch", daemon=True)
    _TICK["thread"] = th
    th.start()


def stop_watcher():
    _TICK["stop"].set()
