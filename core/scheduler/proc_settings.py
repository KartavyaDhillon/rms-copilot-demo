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
    if p.fmt == "phones":
        return ",".join(helpers.parse_phones(v))
    if p.fmt == "emails":
        return ",".join(helpers.parse_emails(v))
    return v.strip()


def get(pdef) -> Dict[str, str]:
    """Saved process-level values (only the process-scope params that have a value)."""
    out = {}
    for p in pdef.params:
        if p.scope == "process":
            v = os.getenv(envstore.proc_key(pdef.key, p.name), "")
            if v.strip():
                out[p.name] = v.strip()
    return out


def view(pdef) -> Dict[str, str]:
    """Saved values with defaults filled in, for the editor."""
    vals = get(pdef)
    for p in pdef.params:
        if p.scope == "process" and p.name not in vals and p.default not in (None, ""):
            vals[p.name] = p.default
    return vals


def save(pdef, values: dict, user: str) -> Dict[str, str]:
    """Validate (phone / e-mail formats …) then write to .env. Raises ValueError listing every problem."""
    from core.scheduler import catalog, store
    errs, clean = catalog.validate_params(pdef, values, scope="process")
    if errs:
        raise ValueError("; ".join(errs))
    before = get(pdef)
    names = {p.name: p for p in pdef.params if p.scope == "process"}
    updates = {envstore.proc_key(pdef.key, n): _norm(names[n], clean.get(n, "")) for n in names}
    envstore.write(envstore.shared_file(), f"process: {pdef.name}", updates)
    after = get(pdef)
    store.audit(user, "process.settings", {"id": None, "name": pdef.name}, before=before, after=after)
    return after
