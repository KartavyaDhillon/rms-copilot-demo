"""Shared connection settings for scheduled processes (WhatsApp, SMTP, POP3), stored in the .env files.

    .env        # scheduler connection: <name>   SCHED_<GROUP>_<FIELD>=…   (server, port, URLs, user names …)
    .env.local  the same, for passwords and tokens only (machine + secret — never carried between machines)

Changes apply at once (the running server's environment is updated too) — no restart.

Safety: the API never returns a secret (only {"set": true, "hint": "…ab12"}); saving a blank secret keeps
the old one; audit records only WHICH fields changed; run logs scrub secret-looking values.
"""
from __future__ import annotations

import contextvars
import os
import re
from typing import Dict, List, Optional

from core.scheduler import envstore

# The process that is running right now (set by the engine around pdef.run). When that process has its OWN copy of a
# connection (see "own settings" below), get()/require() hand out the own copy instead of the shared one.
_PROC: "contextvars.ContextVar[str]" = contextvars.ContextVar("sched_process_key", default="")


def set_process(key: str):
    return _PROC.set(key or "")


def reset_process(token) -> None:
    try:
        _PROC.reset(token)
    except Exception:
        pass

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
            # Global FAILURE ALERT (core/scheduler/failure_alert.py): when ANY scheduled process fails for good,
            # a warning image is WhatsApp'd to these numbers.
            ("alert_switch", "Send failure alerts (on / off)", False, "on"),
            ("alert_numbers", "COMMON WhatsApp numbers (used by every process that has no numbers of its own)", False, ""),
            ("alert_template", "Alert template name (blank = same as above)", False, ""),
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
    # Allocation Report mailboxes: stored under the SAME ALLOC_* settings the Process Center's Allocation Report uses
    "alloc_pop3": {
        "label": "Allocation Report — mailbox the logs arrive in (POP3)",
        "env": {"server": "ALLOC_POP_SERVER", "port": "ALLOC_POP_PORT", "username": "ALLOC_POP_USER", "password": "ALLOC_POP_PASS"},
        "fields": [
            ("server", "POP3 server", False, "pop9.netcore.co.in"),
            ("port", "Port", False, "995"),
            ("username", "Mailbox user name", False, ""),
            ("password", "Password", True, ""),
        ],
    },
    "alloc_smtp": {
        "label": "Allocation Report — report sender (SMTP)",
        "env": {"server": "ALLOC_SMTP_SERVER", "port": "ALLOC_SMTP_PORT", "email": "ALLOC_SMTP_USER", "password": "ALLOC_SMTP_PASS"},
        "fields": [
            ("server", "SMTP server", False, "smtp3.netcore.co.in"),
            ("port", "Port", False, "587"),
            ("email", "Sender e-mail (login)", False, ""),
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
            ("upload_above4cr", "Upload ABOVE 4 CR HOLDING BLOCKING file to Kambala", False, "no"),
        ],
    },
    "common_ftp": {
        "label": "COMMON FILES FTP (HOLDING / RISK_REPORT fetch)",
        # the fetch code (core/common_ftp.py) reads these existing COMMON_FTP_* settings
        "env": {"host": "COMMON_FTP_HOST", "port": "COMMON_FTP_PORT", "user": "COMMON_FTP_USER", "password": "COMMON_FTP_PASS",
                "folder": "COMMON_FTP_DIR", "secure": "COMMON_FTP_TLS", "missing_mail_to": "COMMON_FTP_ALERT_TO",
                "missing_mail_cc": "COMMON_FTP_ALERT_CC"},
        "fields": [
            ("host", "FTP server", False, "202.58.232.124"),
            ("port", "Port", False, "21"),
            ("user", "User name", False, ""),
            ("password", "Password", True, ""),
            ("folder", "Folder on the server", False, "/GINNI_AUTOMATION_FILE"),
            ("secure", "Use secure FTP / FTPS (true / false) - this server does NOT support it, keep false", False, "false"),
            ("missing_mail_to", "E-mail goes to (if a file is still missing)", False, "rms_del@mastertrust.co.in"),
            ("missing_mail_cc", "E-mail Cc", False, ""),
        ],
    },
    "darwinbox": {
        "label": "Darwinbox (HR portal login)",
        "fields": [
            ("username", "Darwinbox user name / employee code", False, ""),
            ("password", "Darwinbox password", True, ""),
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
            ("base_url", "API address", False, "https://boapi.mastertrust.co.in"),
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


def _key(group: str, field: str) -> str:
    """Setting name in the .env files: SCHED_<GROUP>_<FIELD>, unless the group maps the field to an existing setting."""
    return (GROUPS[group].get("env") or {}).get(field) or envstore.conn_key(group, field)


def _raw(group: str, field: str) -> str:
    return (os.getenv(_key(group, field)) or "").strip()


# ───────────── "own settings": one process uses its own copy of a connection (e.g. its own sender mailbox) ─────────────
#   SCHED_OVR_<PROCESS>__<GROUP>__<FIELD>     the process's own value   (secrets in .env.local, the rest in .env)
#   SCHED_OVR_<PROCESS>__<GROUP>__ENABLED     yes  = this process uses its own copy;  anything else = the shared one
def _pk(process: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(process or "")).strip("_").upper()


def _okey(process: str, group: str, field: str) -> str:
    return f"SCHED_OVR_{_pk(process)}__{group.upper()}__{field.upper()}"


def own_enabled(group: str, process: Optional[str] = None) -> bool:
    proc = process if process is not None else _PROC.get()
    return bool(proc) and (os.getenv(_okey(proc, group, "ENABLED")) or "").strip().lower() == "yes"


def _oraw(process: str, group: str, field: str) -> str:
    return (os.getenv(_okey(process, group, field)) or "").strip()


def get(group: str, process: Optional[str] = None) -> Dict[str, str]:
    """Full values (defaults filled in) — for process code only, never for the API.
    A process that switched this connection to its own settings gets its own values (never mixed with the shared ones:
    its own password always goes with its own e-mail address)."""
    spec = _spec(group)
    proc = process if process is not None else _PROC.get()
    if proc and own_enabled(group, proc):
        return {k: (_oraw(proc, group, k) or dflt) for k, _l, _s, dflt in spec["fields"]}
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
OPTIONAL = {("nse", "encrypted_password"), ("mcx_sftp", "password2"), ("mcx_eclear", "rsa_sdtid"),
            ("whatsapp", "alert_numbers"), ("whatsapp", "alert_template"),
            ("common_ftp", "missing_mail_cc")}


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


# Which field identifies the ACCOUNT of a group. A new password for an account is handed to every other place that holds
# the SAME account (same e-mail / same user id); other accounts are never touched. None = one account for the whole group.
IDENT = {"smtp": "email", "smtpb": "email", "alloc_smtp": "email", "pop3": "username", "pop3b": "username",
         "alloc_pop3": "username", "nse": "login_id", "mcx_sftp": "user", "mcx_eclear": "user_id", "backoffice": "username",
         "atom": "username", "darwinbox": "username", "kambala_ftp": "user", "common_ftp": "user"}


def view_for(process: str) -> List[dict]:
    """view() as THIS process sees it: its own copy when it has one, otherwise the shared values."""
    out = []
    for g in view():
        grp = g["group"]
        if own_enabled(grp, process):
            fields = []
            for k, label, secret, dflt in GROUPS[grp]["fields"]:
                raw = _oraw(process, grp, k)
                opt = (grp, k) in OPTIONAL
                if secret:
                    fields.append({"name": k, "label": label, "secret": True, "set": bool(raw), "optional": opt,
                                   "hint": ("…" + raw[-4:]) if len(raw) > 8 else ("set" if raw else "")})
                else:
                    fields.append({"name": k, "label": label, "secret": False, "value": raw or dflt, "default": dflt, "optional": opt})
            g = dict(g, fields=fields)
        out.append(g)
    return out


def _process_name(pk: str) -> str:
    try:
        from core.scheduler import catalog
        for d in catalog.all_defs():
            if _pk(d.key) == pk:
                return d.name
    except Exception:
        pass
    return pk


def _propagate(group: str, ident_value: str, field: str, new: str, user: str, skip_process: str = "", skip_shared: bool = False) -> List[str]:
    """A password / token of one account changed: give the new value to every other holder of the SAME account.
    Returns the names of the places that were updated."""
    spec = _spec(group)
    ident = IDENT.get(group)
    same = lambda have: (not ident) or (str(have or "").strip().lower() == str(ident_value or "").strip().lower())
    done: List[str] = []
    if not skip_shared and same(_raw(group, ident) if ident else "") and _raw(group, field) != new:
        update(group, {field: new}, user)                       # the shared setting (also keeps the other screens in step)
        done.append("shared setting")
    pat = re.compile(r"^SCHED_OVR_(.+?)__" + re.escape(group.upper()) + r"__ENABLED$")
    skip_pk = _pk(skip_process) if skip_process else ""
    updates = {}
    for k in list(os.environ):
        m = pat.match(k)
        if not m or (os.getenv(k) or "").strip().lower() != "yes" or m.group(1) == skip_pk:
            continue
        pk = m.group(1)
        have = (os.getenv(f"SCHED_OVR_{pk}__{group.upper()}__{ident.upper()}") or "") if ident else ""
        if same(have) and (os.getenv(f"SCHED_OVR_{pk}__{group.upper()}__{field.upper()}") or "") != new:
            updates[f"SCHED_OVR_{pk}__{group.upper()}__{field.upper()}"] = new
            done.append(_process_name(pk))
    if updates:
        envstore.write(envstore.local_file(), f"connection (same login of {spec['label']})", updates)
    return done


def _update_own(group: str, values: dict, user: str, process: str, linked_out: Optional[list] = None) -> List[str]:
    """Save from ONE process's Credentials: only that process changes. A new password of an account is also handed to every
    other place that uses the same account."""
    spec = _spec(group)
    ident = IDENT.get(group)
    secret = {k: s for k, _l, s, _d in spec["fields"]}
    had_own = own_enabled(group, process)
    cur = get(group, process)                                    # what this process uses right now
    final, changed, supplied = dict(cur), [], set()
    for k, v in (values or {}).items():
        if k not in secret:
            continue
        v = "" if v is None else str(v).strip()
        if secret[k] and v == "":
            continue                                             # blank secret = keep
        if "\n" in v or "\r" in v:
            raise ValueError(f"'{k}' must be a single line.")
        if k.endswith("_url") and v and not v.lower().startswith(("http://", "https://")):
            raise ValueError(f"'{k}' must be a web address starting with http:// or https:// (you typed '{v[:40]}').")
        supplied.add(k)
        if final.get(k) != v:
            final[k] = v
            changed.append(k)
    if not changed:
        return []
    if ident and ident in changed and final.get(ident, "").lower() != (cur.get(ident) or "").lower():
        need = [l for k, l, sec, _d in spec["fields"] if sec and (group, k) not in OPTIONAL and k not in supplied]
        if need:
            raise ValueError(f"You changed the {dict((k, l) for k, l, _s, _d in spec['fields'])[ident]} - also type the "
                             f"{', '.join(need)} of that account (the old one belongs to {cur.get(ident) or 'the previous account'}).")
        # a different account: the secrets of the old one must not be carried over
        for k, l, sec, _d in spec["fields"]:
            if sec and k not in supplied:
                final[k] = ""
    plain, secrets = {}, {}
    write_keys = [k for k, _l, _s, _d in spec["fields"]] if not had_own else changed
    for k in write_keys:
        (secrets if secret[k] else plain)[_okey(process, group, k)] = final.get(k, "")
    if not had_own:
        plain[_okey(process, group, "ENABLED")] = "yes"
    title = f"connection (own copy of {process}): {spec['label']}"
    envstore.write(envstore.shared_file(), title, plain)
    if secrets:
        envstore.write(envstore.local_file(), title, secrets)
    # a new password / token of this account -> every other holder of the same account
    linked: List[str] = []
    for k in changed:
        if secret[k] and final.get(k):
            linked += _propagate(group, final.get(ident, "") if ident else "", k, final[k], user, skip_process=process)
    if linked_out is not None:
        linked_out.extend(sorted(set(linked)))
    try:
        from core.scheduler import store
        store.audit(user, "connection.update_own", {"id": process, "name": spec["label"]}, before=None,
                    after={"changed_fields": changed, "also_updated": sorted(set(linked))})
    except Exception:
        pass
    return changed


def update(group: str, values: dict, user: str, process: Optional[str] = None, linked_out: Optional[list] = None) -> List[str]:
    """Save the supplied fields. Blank secret = keep the old one. Returns the names of changed fields.
    process=<key>: save from that process's Credentials - only that process changes (see _update_own)."""
    if process:
        return _update_own(group, values, user, process, linked_out)
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
        if k.endswith("_url") and v and not v.lower().startswith(("http://", "https://")):
            raise ValueError(f"'{k}' must be a web address starting with http:// or https:// (you typed '{v[:40]}').")
        if _raw(group, k) != v and (v or not secret[k]):
            (secrets if secret[k] else plain)[_key(group, k)] = v
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
        ident = IDENT.get(group)
        for k in changed:                                   # a new password of this account -> every process copy of the SAME account
            if secret[k] and (secrets.get(_key(group, k)) or ""):
                try:
                    _linked = _propagate(group, get(group).get(ident, "") if ident else "", k, secrets[_key(group, k)], user, skip_shared=True)
                    if linked_out is not None:
                        linked_out.extend(_linked)
                except Exception:
                    pass
        try:
            from core.scheduler import store
            store.audit(user, "connection.update", {"id": None, "name": spec["label"]}, before=None,
                        after={"changed_fields": changed})
        except Exception:
            pass
    return changed
