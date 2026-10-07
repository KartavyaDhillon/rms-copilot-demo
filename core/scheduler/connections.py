"""Shared connection settings for scheduled processes (WhatsApp, SMTP, POP3).

Why one place: the WhatsApp token / mail passwords used to be pasted into every script.  Here they are
entered once on Scheduler → Connections and every process reads them from here.

Safety
------
* Stored in data/scheduler/_connections.json on THIS server (same exposure as the .env file — protect the
  data folder with Windows permissions).  Never written to the database, the run logs or the audit trail
  (audit records only WHICH fields changed).
* The API never returns a secret: it returns {"set": true, "hint": "…ab12"}.  Saving a blank secret keeps
  the existing one.  Only an administrator can change anything.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Dict, List, Optional

from core.scheduler import paths

# name, label, secret?, default
GROUPS: Dict[str, dict] = {
    "whatsapp": {
        "label": "WhatsApp (Tata Tele Omni)",
        "fields": [
            ("auth_token", "Auth token", True, ""),
            ("upload_url", "Media upload URL", False, "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/media"),
            ("send_url", "Send message URL", False, "https://wb.omni.tatatelebusiness.com/whatsapp-cloud/messages"),
            ("template_name", "Template name", False, "daily_report"),
            ("template_lang", "Template language", False, "en"),
        ],
    },
    "smtp": {
        "label": "E-mail sending (SMTP)",
        "fields": [
            ("server", "SMTP server", False, "smtp3.netcore.co.in"),
            ("port", "Port", False, "587"),
            ("email", "Sender e-mail (login)", False, ""),
            ("password", "Password", True, ""),
        ],
    },
    "pop3": {
        "label": "Mailbox reading (POP3)",
        "fields": [
            ("server", "POP3 server", False, "pop9.netcore.co.in"),
            ("port", "Port", False, "995"),
            ("username", "Mailbox user name", False, ""),
            ("password", "Password", True, ""),
        ],
    },
}

_LOCK = threading.Lock()


def _file():
    return paths.sched_root() / "_connections.json"


def _load() -> dict:
    try:
        with open(_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save(d: dict):
    p = _file()
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


def _spec(group: str) -> dict:
    if group not in GROUPS:
        raise KeyError(f"Unknown connection '{group}'.")
    return GROUPS[group]


def get(group: str) -> Dict[str, str]:
    """Full values (defaults filled in) — for process code only, never for the API."""
    spec = _spec(group)
    with _LOCK:
        stored = _load().get(group, {})
    return {k: str(stored.get(k, dflt) if stored.get(k, "") != "" else dflt) for k, _l, _s, dflt in spec["fields"]}


def require(group: str, *keys: str) -> Dict[str, str]:
    """get() + raise a plain-English error if a needed field is still empty."""
    v = get(group)
    missing = [k for k in keys if not v.get(k)]
    if missing:
        label = GROUPS[group]["label"]
        raise RuntimeError(f"{label}: '{', '.join(missing)}' is not set — add it in Scheduler → Connections.")
    return v


def view() -> List[dict]:
    """Masked, for the UI."""
    out = []
    with _LOCK:
        stored = _load()
    for g, spec in GROUPS.items():
        fields = []
        for k, label, secret, dflt in spec["fields"]:
            raw = str(stored.get(g, {}).get(k, "") or "")
            if secret:
                fields.append({"name": k, "label": label, "secret": True, "set": bool(raw),
                               "hint": ("…" + raw[-4:]) if len(raw) > 8 else ("set" if raw else "")})
            else:
                fields.append({"name": k, "label": label, "secret": False, "value": raw or dflt, "default": dflt})
        out.append({"group": g, "label": spec["label"], "fields": fields})
    return out


def update(group: str, values: dict, user: str) -> List[str]:
    """Save the supplied fields. Blank secret = keep the old one. Returns the names of changed fields."""
    spec = _spec(group)
    known = {k: secret for k, _l, secret, _d in spec["fields"]}
    changed: List[str] = []
    with _LOCK:
        d = _load()
        cur = d.setdefault(group, {})
        for k, v in (values or {}).items():
            if k not in known:
                continue
            v = "" if v is None else str(v).strip()
            if known[k] and v == "":
                continue                                    # keep existing secret
            if str(cur.get(k, "")) != v:
                cur[k] = v
                changed.append(k)
        if changed:
            _save(d)
    if changed:
        try:
            from core.scheduler import store
            store.audit(user, "connection.update", {"id": None, "name": spec["label"]}, before=None,
                        after={"changed_fields": changed})
        except Exception:
            pass
    return changed
