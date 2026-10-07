"""Process-level settings (who a process sends to).

Saved per process in data/scheduler/<PROCESS NAME>/settings.json and shared by every schedule of that process,
so numbers / e-mail addresses are changed in ONE place (Processes tab) — not inside each schedule.
Not secret (phone numbers, addresses); tokens and passwords live in connections.py.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Dict

from core.scheduler import paths

_LOCK = threading.Lock()


def _file(pdef):
    return paths.proc_dir(pdef.folder) / "settings.json"


def get(pdef) -> Dict[str, str]:
    """Saved process-level values (only the process-scope params)."""
    if not pdef.folder:
        return {}
    names = {p.name for p in pdef.params if p.scope == "process"}
    with _LOCK:
        try:
            d = json.loads(_file(pdef).read_text(encoding="utf-8"))
        except Exception:
            d = {}
    return {k: v for k, v in d.items() if k in names and v not in (None, "")}


def view(pdef) -> Dict[str, str]:
    """Saved values with defaults filled in, for the editor."""
    vals = get(pdef)
    for p in pdef.params:
        if p.scope == "process" and p.name not in vals and p.default not in (None, ""):
            vals[p.name] = p.default
    return vals


def save(pdef, values: dict, user: str) -> Dict[str, str]:
    """Validate (phone / e-mail formats …) and store. Raises ValueError with all problems."""
    from core.scheduler import catalog, store
    if not pdef.folder:
        raise ValueError("This process has no settings.")
    errs, clean = catalog.validate_params(pdef, values, scope="process")
    if errs:
        raise ValueError("; ".join(errs))
    before = get(pdef)
    p = _file(pdef)
    tmp = p.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    store.audit(user, "process.settings", {"id": None, "name": pdef.name}, before=before, after=clean)
    return clean
