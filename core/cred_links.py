"""One place to change a site's password — every other place that logs in to the SAME account follows.

WHY
    The same exchange / portal login was stored under many different setting names (e.g. the NCDEX login lives in
    CTCL_NCDEX_*, SA_NCDEX_* and NCDEX_*; the NSE extranet password in NSE_PASSWORD, SA_NSE_PASSWORD, RECO_NSE_PASSWORD
    and the Scheduler's connection). Change it in one screen and the others kept the old password → locked accounts.

HOW
    Every "save" path (Process Center 🔑 credentials, Short Allocation credentials, Scheduler → Credentials) calls
    on_saved({SETTING_NAME: new_value, ...}) right after it saves. For every password setting in that dict, the new
    password is copied to every other setting of the SAME SITE whose user name is the SAME account.

    Same-account rule: a password is copied only where the user name matches (case-insensitive). A different login on
    the same site (say, a second MCX user) is never overwritten.

    Nothing is changed at start-up and nothing is changed when a value is saved unchanged. Values are never logged —
    only the NAMES of the settings that were updated.

SITES  (user_setting, password_setting) pairs.  kind: plain | nse (encrypted token) | list (comma separated, newest first)
"""
from __future__ import annotations

import base64
import os
import re
from typing import Dict, List, Optional, Tuple

SITES: Dict[str, dict] = {
    "NSE extranet (connect2nse.com)": {
        "kind": "nse",
        "pairs": [("NSE_LOGIN_ID", "NSE_PASSWORD"), ("SA_NSE_LOGINID", "SA_NSE_PASSWORD"),
                  ("RECO_NSE_LOGIN_ID", "RECO_NSE_PASSWORD"),
                  ("SCHED_NSE_LOGIN_ID", "SCHED_NSE_ENCRYPTED_PASSWORD")],
        "plain_sources": {"SCHED_NSE_PASSWORD": "SCHED_NSE_LOGIN_ID"},      # plain text → encrypted before copying
        "members": ["NSE_MEMBER_CODE", "SA_NSE_MEMBER", "RECO_NSE_MEMBER", "SCHED_NSE_MEMBER_CODE"],
    },
    "NCDEX NCFE (ncfe.ncdex.com)": {
        "kind": "plain",
        "pairs": [("CTCL_NCDEX_USER", "CTCL_NCDEX_PASSWORD"), ("SA_NCDEX_USER", "SA_NCDEX_PASSWORD"),
                  ("NCDEX_USERNAME", "NCDEX_PASSWORD")],
    },
    "NMASS / NSCCL": {
        "kind": "plain",
        "pairs": [("CTCL_NMASS_USER", "CTCL_NMASS_PASSWORD"), ("NSCCL_USERNAME", "NSCCL_PASSWORD")],
    },
    "MCX eclear / TCS BaNCS (eclear.mcxccl.com)": {
        "kind": "plain",
        "pairs": [("SA_MCX_DEPOSIT_USER", "SA_MCX_DEPOSIT_PASSWORD"), ("CTCL_MCX90_USER", "CTCL_MCX90_PASSWORD"),
                  ("RECO_MCX_USER", "RECO_MCX_PASSWORD"), ("SCHED_MCX_ECLEAR_USER_ID", "SCHED_MCX_ECLEAR_PASSWORD")],
    },
    "MCX file portal (sftp.mcxindia.com)": {
        "kind": "list",
        "pairs": [("MCX_USERNAME", "MCX_PASSWORDS"), ("SA_MCX_USER", "SA_MCX_PASSWORDS"),
                  ("SCHED_MCX_SFTP_USER", "SCHED_MCX_SFTP_PASSWORD")],
        "list_keys": {"MCX_PASSWORDS", "SA_MCX_PASSWORDS"},
        "second_single": {"SCHED_MCX_SFTP_PASSWORD": "SCHED_MCX_SFTP_PASSWORD2"},
    },
    "Back-office API (boapi)": {
        "kind": "plain",
        "pairs": [("KORP_USERNAME", "KORP_PASSWORD"), ("SCHED_BACKOFFICE_USERNAME", "SCHED_BACKOFFICE_PASSWORD")],
    },
    "E-mail sending (SMTP)": {
        "kind": "plain",
        "pairs": [("SENDER_EMAIL", "SENDER_PASSWORD"), ("SA_SMTP_EMAIL", "SA_SMTP_PASSWORD"),
                  ("SA_DL_SMTP_EMAIL", "SA_DL_SMTP_PASSWORD"), ("SA2_SMTP_USER", "SA2_SMTP_PASS"),
                  ("PROBABLE_ALERTS_SMTP_EMAIL", "PROBABLE_ALERTS_SMTP_PASSWORD"),
                  ("SCHED_SMTP_EMAIL", "SCHED_SMTP_PASSWORD"), ("SCHED_SMTPB_EMAIL", "SCHED_SMTPB_PASSWORD")],
    },
    "Mailbox reading (POP3)": {
        "kind": "plain",
        "pairs": [("LAS_POP_EMAIL", "LAS_POP_PASSWORD"), ("SCHED_POP3_USERNAME", "SCHED_POP3_PASSWORD"),
                  ("SCHED_POP3B_USERNAME", "SCHED_POP3B_PASSWORD")],
    },
}


# ───────────────────────── helpers ─────────────────────────
def _same(a: Optional[str], b: Optional[str]) -> bool:
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    return bool(a) and a == b


def _looks_encrypted(v: str) -> bool:
    """NSE token = base64 of AES blocks (16 bytes each)."""
    try:
        raw = base64.b64decode(v.strip(), validate=True)
        return len(raw) >= 16 and len(raw) % 16 == 0
    except Exception:
        return False


def _nse_encrypt(plain: str) -> str:
    from core.short_allocation import _nse_encrypt as enc
    return enc(plain)


def _paths():
    from core.scheduler import envstore
    return envstore.shared_file(), envstore.local_file(), envstore


def _defined_in(path, key: str, envstore) -> bool:
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            m = envstore._KEY.match(ln)
            if m and m.group(1) == key:
                return True
    except FileNotFoundError:
        pass
    return False


def _write(updates: Dict[str, str]) -> None:
    """Write each key into every .env file that already defines it (a stale copy in .env.local would otherwise
    win over the new value after a reload). A key defined nowhere goes to .env — where the legacy screens keep it."""
    if not updates:
        return
    shared, local, envstore = _paths()
    per_file: Dict[object, Dict[str, str]] = {shared: {}, local: {}}
    for k, v in updates.items():
        hit = False
        for p in (shared, local):
            if _defined_in(p, k, envstore):
                per_file[p][k] = v
                hit = True
        if not hit:
            per_file[local if k.startswith("SCHED_") and "PASSWORD" in k else shared][k] = v
    for p, ups in per_file.items():
        if ups:
            envstore.write(p, "linked credentials", ups)


# ───────────────────────── the one public function ─────────────────────────
def on_saved(saved: Dict[str, str]) -> List[str]:
    """`saved` = {setting name: value just saved}. Returns the NAMES of the other settings that were updated."""
    changed: Dict[str, str] = {}
    try:
        for site, spec in SITES.items():
            pairs: List[Tuple[str, str]] = spec["pairs"]
            kind = spec["kind"]
            sources: List[Tuple[str, str, str]] = []                 # (user_value, password_key, new_value)
            for ukey, pkey in pairs:
                if pkey in saved and str(saved[pkey]).strip():
                    user = str(saved.get(ukey) or os.getenv(ukey, "")).strip()
                    sources.append((user, pkey, str(saved[pkey]).strip()))
            for pkey, ukey in (spec.get("plain_sources") or {}).items():
                if pkey in saved and str(saved[pkey]).strip():
                    user = str(saved.get(ukey) or os.getenv(ukey, "")).strip()
                    try:
                        sources.append((user, pkey, _nse_encrypt(str(saved[pkey]).strip())))
                    except Exception:
                        pass                                              # cannot encrypt here → leave the others alone
            for user, src_key, new in sources:
                if not user:
                    continue
                if kind == "nse" and not _looks_encrypted(new):
                    try:
                        new = _nse_encrypt(new)                            # someone typed the plain password
                    except Exception:
                        continue
                items = [x.strip() for x in new.split(",") if x.strip()] if kind == "list" and src_key in spec["list_keys"] else [new]
                for ukey, pkey in pairs:
                    tgt_user = saved[ukey] if ukey in saved else os.getenv(ukey, "")
                    if not _same(tgt_user, user):
                        continue                                           # a different account — never touched
                    cur = os.getenv(pkey, "")
                    if kind == "list" and pkey in spec["list_keys"]:
                        old = [x.strip() for x in cur.split(",") if x.strip()]
                        val = ",".join(items + [o for o in old if o not in items][:max(0, 2 - len(items))])
                    else:
                        val = items[0]
                    if val != cur:
                        changed[pkey] = val
                    second = (spec.get("second_single") or {}).get(pkey)
                    if second and len(items) > 1 and items[1] != os.getenv(second, ""):
                        changed[second] = items[1]
            # the member code is not a secret and not an account — it simply follows
            for mk in spec.get("members", []):
                if mk in saved and str(saved[mk]).strip():
                    for other in spec["members"]:
                        if other != mk and os.getenv(other, "") and os.getenv(other, "") != str(saved[mk]).strip():
                            changed[other] = str(saved[mk]).strip()
        if changed:
            _write(changed)
            print(f"[cred_links] password / member code kept in step in: {', '.join(sorted(changed))}", flush=True)
    except Exception as e:                       # never break a save because the follow-up failed
        print(f"[cred_links] could not update the linked settings: {type(e).__name__}: {e}", flush=True)
        return []
    # also make sure the value just saved is not shadowed by a stale copy in .env.local
    try:
        shared, local, envstore = _paths()
        stale = {k: str(v) for k, v in saved.items() if k not in changed and _defined_in(local, k, envstore)}
        if stale:
            _write(stale)
    except Exception:
        pass
    return sorted(changed)


def report() -> List[dict]:
    """For a diagnostic: per site, per account, which settings hold a DIFFERENT password from the others (no values)."""
    out = []
    for site, spec in SITES.items():
        by_user: Dict[str, List[Tuple[str, str]]] = {}
        for ukey, pkey in spec["pairs"]:
            u, p = os.getenv(ukey, "").strip().lower(), os.getenv(pkey, "")
            if u and p:
                by_user.setdefault(u, []).append((pkey, p.split(",")[0].strip() if spec["kind"] == "list" else p))
        for u, rows in by_user.items():
            if len({p for _k, p in rows}) > 1:
                out.append({"site": site, "user": u, "settings": [k for k, _p in rows],
                            "in_step": False})
    return out
