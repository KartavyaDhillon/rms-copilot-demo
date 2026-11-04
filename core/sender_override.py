"""core/sender_override.py - each report can send from its OWN e-mail address; passwords stay linked everywhere.

Every process that sends mail used to read the one shared SENDER_EMAIL / SENDER_PASSWORD, so changing the sender in one
process's credentials changed it for all of them. Now each process has its own sender setting (<PIPE>_SENDER_EMAIL /
<PIPE>_SENDER_PASSWORD), shown and edited in that process's Credentials window:

  * blank (or never saved)  -> the shared SENDER_EMAIL / SENDER_PASSWORD is used, exactly as before;
  * set                     -> only THAT process sends from it;
  * a changed PASSWORD       -> copied to every other setting with the same e-mail address (core/cred_links.py).

The process knows who it is from the environment variable RMS_PIPE, which the console sets when it starts the run
(a variable of the run itself, so .env / .env.local can never override it).
"""
from __future__ import annotations

import os

PIPE_KEYS = {
    "ctcl": ("CTCL_SENDER_EMAIL", "CTCL_SENDER_PASSWORD"),
    "ms15": ("MS15_SENDER_EMAIL", "MS15_SENDER_PASSWORD"),
    "r1r2": ("R1R2_SENDER_EMAIL", "R1R2_SENDER_PASSWORD"),
    "reco": ("RECO_SENDER_EMAIL", "RECO_SENDER_PASSWORD"),
    "probable": ("PROBABLE_SENDER_EMAIL", "PROBABLE_SENDER_PASSWORD"),
    "mtfic": ("MTFIC_SMTP_EMAIL", "MTFIC_SMTP_PASSWORD"),
}


def _shared():
    return os.getenv("SENDER_EMAIL", "").strip(), os.getenv("SENDER_PASSWORD", "")


def sender(pipe: str = None):
    """(email, password) this run must send from."""
    pipe = (pipe or os.getenv("RMS_PIPE", "")).strip().lower()
    keys = PIPE_KEYS.get(pipe)
    s_email, s_pw = _shared()
    if keys:
        own = os.getenv(keys[0], "").strip()
        if own:
            pw = os.getenv(keys[1], "")
            if not pw and own.lower() == s_email.lower():        # same address as the shared sender: its password is the same
                pw = s_pw
            return own, pw
    return s_email, s_pw


def display_values(user_key: str, pass_key: str):
    """What the Credentials window should SHOW for a per-process sender: the saved value, else the shared one it falls
    back to (display only - nothing is written). -> (email, password) or None when the keys are not a sender pair."""
    for keys in PIPE_KEYS.values():
        if keys == (user_key, pass_key):
            own = os.getenv(user_key, "").strip()
            if own:
                return own, os.getenv(pass_key, "") or (_shared()[1] if own.lower() == _shared()[0].lower() else "")
            return _shared()
    return None
