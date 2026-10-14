"""Shared connection settings for scheduled processes (WhatsApp, SMTP, POP3), stored in the .env files.

    .env        # scheduler connection: <name>   SCHED_<GROUP>_<FIELD>=…   (server, port, URLs, user names …)
    .env.local  the same, for passwords and tokens only (machine + secret — never carried between machines)

Changes apply at once (the running server's environment is updated too) — no restart.

Safety: the API never returns a secret (only {"set": true, "hint": "…ab12"}); saving a blank secret keeps
the old one; audit records only WHICH fields changed; run logs scrub secret-looking values.
"""
from __future__ import annotations

import os
from typing import Dict, List

from core.scheduler import envstore

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
    "smtpb": {
        "label": "E-mail sending — second mailbox (SMTP)",
        "fields": [
            ("server", "SMTP server", False, "smtp3.netcore.co.in"),
            ("port", "Port", False, "587"),
            ("email", "Sender e-mail (login)", False, ""),
            ("password", "Password", True, ""),
        ],
    },
    "pop3b": {
        "label": "Mailbox reading — second mailbox (POP3)",
        "fields": [
            ("server", "POP3 server", False, "pop9.netcore.co.in"),
            ("port", "Port", False, "995"),
            ("username", "Mailbox user name", False, ""),
            ("password", "Password", True, ""),
        ],
    },
    "nse": {
        "label": "NSE Extranet (connect2nse.com)",
        "fields": [
            ("member_code", "Member code", False, "06436"),
            ("login_id", "Login ID", False, ""),
            ("password", "Password (plain)", True, ""),
            ("aes_key", "AES key (base64)", True, ""),
            ("encrypted_password", "OR already-encrypted password", True, ""),
        ],
    },
    "mcx_sftp": {
        "label": "MCX file portal (sftp.mcxindia.com)",
        "fields": [
            ("user", "User name", False, ""),
            ("password", "Password", True, ""),
            ("password2", "Second password to try", True, ""),
            ("totp_secret", "MFA (TOTP) secret", True, ""),
        ],
    },
    "mcx_eclear": {
        "label": "MCX eclear (surveillance)",
        "fields": [
            ("user_id", "User ID", False, ""),
            ("password", "Password", True, ""),
            ("rsa_zip", "RSA MAKER token zip (path on this PC)", False, ""),
            ("rsa_sdtid", "OR extracted .sdtid file (path)", False, ""),
        ],
    },
    "client_margin": {
        "label": "Client margin Excel passwords",
        "fields": [
            ("source_password", "Password of the incoming ClientMargin files", True, ""),
            ("output_password", "Password to put on the Top-20 report", True, ""),
        ],
    },
    "kambala_ftp": {
        "label": "Kambala FTP (file upload)",
        "fields": [
            ("host", "FTP server", False, "202.58.232.29"),
            ("port", "Port", False, "21"),
            ("user", "User name", False, "ftpadmin"),
            ("password", "Password", True, ""),
            ("folder", "Folder on the server", False, "/"),
            ("upload_enabled", "Upload RMS LIMITS MAND file to Kambala", False, "no"),
        ],
    },
    "atom": {
        "label": "ATOM merchant console",
        "fields": [
            ("username", "User name", False, ""),
            ("password", "Password", True, ""),
        ],
    },
    "backoffice": {
        "label": "Back-office API (Bank alerts)",
        "fields": [
            ("base_url", "API address", False, "http://boapi.mastertrust.co.in:15000"),
            ("username", "User name", False, ""),
            ("password", "Password", True, ""),
            ("client_ip", "Client IP sent with login", False, ""),
            ("client_mac", "Client MAC sent with login", False, ""),
        ],
    },
}


def _spec(group: str) -> dict:
    if group not in GROUPS:
        raise KeyError(f"Unknown connection '{group}'.")
    return GROUPS[group]


def _raw(group: str, field: str) -> str:
    return (os.getenv(envstore.conn_key(group, field)) or "").strip()


def get(group: str) -> Dict[str, str]:
    """Full values (defaults filled in) — for process code only, never for the API."""
    spec = _spec(group)
    return {k: (_raw(group, k) or dflt) for k, _l, _s, dflt in spec["fields"]}


def require(group: str, *keys: str) -> Dict[str, str]:
    """get() + raise a plain-English error if a needed field is still empty."""
    v = get(group)
    missing = [k for k in keys if not v.get(k)]
    if missing:
        raise RuntimeError(f"{GROUPS[group]['label']}: '{', '.join(missing)}' is not set — "
                           f"add it with the 🔑 Credentials button on the Scheduler's Processes tab.")
    return v


# Alternatives / extras that may stay empty — the UI must not call the connection "not set" because of them.
OPTIONAL = {("nse", "encrypted_password"), ("mcx_sftp", "password2"), ("mcx_eclear", "rsa_sdtid")}


def view() -> List[dict]:
    """Masked, for the UI."""
    out = []
    for g, spec in GROUPS.items():
        fields = []
        for k, label, secret, dflt in spec["fields"]:
            raw = _raw(g, k)
            opt = (g, k) in OPTIONAL
            if secret:
                fields.append({"name": k, "label": label, "secret": True, "set": bool(raw), "optional": opt,
                               "hint": ("…" + raw[-4:]) if len(raw) > 8 else ("set" if raw else "")})
            else:
                fields.append({"name": k, "label": label, "secret": False, "value": raw or dflt, "default": dflt,
                               "optional": opt})
        out.append({"group": g, "label": spec["label"], "fields": fields})
    return out


def update(group: str, values: dict, user: str) -> List[str]:
    """Save the supplied fields. Blank secret = keep the old one. Returns the names of changed fields."""
    spec = _spec(group)
    secret = {k: s for k, _l, s, _d in spec["fields"]}
    plain, secrets, changed = {}, {}, []
    for k, v in (values or {}).items():
        if k not in secret:
            continue
        v = "" if v is None else str(v).strip()
        if secret[k] and v == "":
            continue                                        # keep the existing secret
        if "\n" in v or "\r" in v:
            raise ValueError(f"'{k}' must be a single line.")
        if _raw(group, k) != v and (v or not secret[k]):
            (secrets if secret[k] else plain)[envstore.conn_key(group, k)] = v
            changed.append(k)
    title = f"connection: {spec['label']}"
    if plain:
        envstore.write(envstore.shared_file(), title, plain)
    if secrets:
        envstore.write(envstore.local_file(), title, secrets)
    if plain or secrets:
        # The same site login is also used by other screens — keep them in step (see core/cred_links.py).
        try:
            from core import cred_links
            cred_links.on_saved({**plain, **secrets})
        except Exception:
            pass
    if changed:
        try:
            from core.scheduler import store
            store.audit(user, "connection.update", {"id": None, "name": spec["label"]}, before=None,
                        after={"changed_fields": changed})
        except Exception:
            pass
    return changed
