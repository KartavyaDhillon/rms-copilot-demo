"""Process-level settings (who a process sends to) — stored in .env.

Each value lives in .env under  '# scheduler process: <PROCESS NAME>'  as
SCHED_<PROCESS_KEY>_<PARAM>=…  — one place for every schedule of that process (see envstore.py).
Phone numbers / e-mail addresses are not secret; tokens and passwords are in connections.py.
"""
from __future__ import annotations

import os
from typing import Dict

from core.scheduler import envstore, helpers


def _norm(p, v: str) -> str:
    """Store lists on one line: 'a@x.com,b@x.com'."""
    if p.fmt in ("phones", "emails"):
        return helpers.normalise_list(p.fmt, v)           # keeps '#name' labels, adds +91 to 10-digit numbers
    return v.strip()


def _portal_id(pdef):
    from core.scheduler import portal_jobs
    return pdef.key[len(portal_jobs.KEY_PREFIX):] if pdef.key.startswith(portal_jobs.KEY_PREFIX) else None


def get(pdef) -> Dict[str, str]:
    """Saved process-level values.  A value that was SAVED EMPTY counts as saved: clearing a Cc box on purpose
    (for example while testing) means "nobody", it must NOT fall back to the built-in default list.  Only a setting
    that was never saved at all (no line in .env) uses the default."""
    pid = _portal_id(pdef)
    if pid and pdef.params:                                  # a Portal Downloads job: To / Cc live in the job itself
        from core.scheduler import portal_jobs
        return portal_jobs.recipients_of(pid)
    out = {}
    for p in pdef.params:
        if p.scope == "process":
            v = os.environ.get(envstore.proc_key(pdef.key, p.name))
            if v is not None:
                out[p.name] = v.strip()
    return out


def view(pdef) -> Dict[str, str]:
    """Saved values with defaults filled in (only for settings never saved), for the editor."""
    vals = get(pdef)
    for p in pdef.params:
        if p.scope == "process" and p.name not in vals and p.default not in (None, ""):
            vals[p.name] = p.default
    return vals


def save(pdef, values: dict, user: str) -> Dict[str, str]:
    """Validate (phone / e-mail formats …) then write to .env. Raises ValueError listing every problem."""
    import dataclasses
    from core.scheduler import catalog, store
    pid = _portal_id(pdef)
    if pid and pdef.params:
        from core.scheduler import portal_jobs
        before = portal_jobs.recipients_of(pid)
        after = portal_jobs.save_recipients(pid, str(values.get("to", before["to"])), str(values.get("cc", before["cc"])))
        store.audit(user, "process.settings", {"id": None, "name": pdef.name}, before=before, after=after)
        return after
    # Only the settings that were SENT are checked and saved: the process card has separate e-mail and WhatsApp
    # buttons, and saving one list must never blank (or demand) the other.
    sent = dataclasses.replace(pdef, params=[p for p in pdef.params if p.scope != "process" or p.name in values])
    errs, clean = catalog.validate_params(sent, values, scope="process")
    if errs:
        raise ValueError("; ".join(errs))
    before = get(pdef)
    names = {p.name: p for p in pdef.params if p.scope == "process" and p.name in values}
    updates = {envstore.proc_key(pdef.key, n): _norm(names[n], clean.get(n, "")) for n in names}
    envstore.write(envstore.shared_file(), f"process: {pdef.name}", updates)
    after = get(pdef)
    store.audit(user, "process.settings", {"id": None, "name": pdef.name}, before=before, after=after)
    return after
