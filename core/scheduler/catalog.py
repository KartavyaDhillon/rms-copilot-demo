"""Registry of processes the scheduler can run.

Why a catalog: the Process Center's existing runs are SSE routes tied to a browser and
several of them need a human (RSA token, uploads).  The scheduler is for human-free
processes (reports, e-mails, messages, housekeeping) — each is registered once here and the
whole UI (process list, parameter form, validation) is built from this registry.

Add a process = add ONE file under core/scheduler/processes/ (auto-imported):

    from core.scheduler.catalog import register, Param

    def _run(params, ctx):
        ctx.log("sending…")
        # do the work; RAISE on failure; return a short text summary on success
        return "sent 1 e-mail"

    register(
        key="reports.morning_mail",          # unique, never rename once schedules use it
        name="Morning report e-mail",
        description="Sends the morning summary to the desk.",
        run=_run,
        params=[Param("recipient_group", "Recipient group", "choice", choices=["desk", "mgmt"], default="desk")],
        retry_safe=False,        # True ONLY if running it twice is harmless
        parallel_safe=False,     # True ONLY if two copies may run at once
        lock_pipe=None,          # optional: a Process Center pipe key ("ctcl"…) that must be idle
    )
"""
from __future__ import annotations

import importlib
import pkgutil
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

PARAM_TYPES = ("str", "text", "int", "bool", "choice")


@dataclass
class Param:
    name: str
    label: str
    type: str = "str"
    required: bool = False
    default: Any = None
    choices: Optional[List[str]] = None
    help: str = ""

    def to_dict(self):
        return {"name": self.name, "label": self.label, "type": self.type, "required": self.required,
                "default": self.default, "choices": self.choices, "help": self.help}


@dataclass
class ProcessDef:
    key: str
    name: str
    run: Callable[[dict, Any], Any]
    description: str = ""
    params: List[Param] = field(default_factory=list)
    retry_safe: bool = False
    parallel_safe: bool = False
    lock_pipe: Optional[str] = None
    category: str = "General"

    def to_dict(self):
        return {"key": self.key, "name": self.name, "description": self.description, "category": self.category,
                "params": [p.to_dict() for p in self.params],
                "retry_safe": self.retry_safe, "parallel_safe": self.parallel_safe}


_REG: Dict[str, ProcessDef] = {}
_LOCK = threading.Lock()
_LOADED = False


def register(key, name, run, description="", params=None, retry_safe=False, parallel_safe=False,
             lock_pipe=None, category="General") -> ProcessDef:
    if not key or not callable(run):
        raise ValueError("register(): key and a callable run are required")
    for p in params or []:
        if p.type not in PARAM_TYPES:
            raise ValueError(f"{key}: parameter '{p.name}' has unsupported type '{p.type}' "
                             f"(secrets are deliberately not supported — keep them in .env)")
    d = ProcessDef(key=key, name=name, run=run, description=description, params=list(params or []),
                   retry_safe=retry_safe, parallel_safe=parallel_safe, lock_pipe=lock_pipe, category=category)
    with _LOCK:
        _REG[key] = d
    return d


def unregister(key):
    with _LOCK:
        _REG.pop(key, None)


def load_builtin():
    """Import every module in core/scheduler/processes/ once (each registers itself)."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        from core.scheduler import processes as pkg
        for m in pkgutil.iter_modules(pkg.__path__):
            if m.name.startswith("_"):
                continue
            try:
                importlib.import_module(f"core.scheduler.processes.{m.name}")
            except Exception as e:                    # one broken process file must not break the rest
                print(f"[scheduler] could not load process module '{m.name}': {type(e).__name__}: {e}", flush=True)
    except Exception as e:
        print(f"[scheduler] process package not loadable: {e}", flush=True)


def get(key) -> Optional[ProcessDef]:
    load_builtin()
    return _REG.get(key)


def all_defs() -> List[ProcessDef]:
    load_builtin()
    return sorted(_REG.values(), key=lambda d: (d.category, d.name))


def validate_params(pdef: ProcessDef, values: dict) -> (list, dict):
    """-> (errors, cleaned values). Unknown keys are rejected; types are coerced."""
    errs, out = [], {}
    values = dict(values or {})
    known = {p.name: p for p in pdef.params}
    for k in values:
        if k not in known:
            errs.append(f"Unknown parameter '{k}'.")
    for p in pdef.params:
        v = values.get(p.name, p.default)
        if v in (None, ""):
            if p.required:
                errs.append(f"'{p.label}' is required.")
            continue
        try:
            if p.type == "int":
                v = int(v)
            elif p.type == "bool":
                v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
            elif p.type == "choice":
                if v not in (p.choices or []):
                    raise ValueError
            else:
                v = str(v)
        except (TypeError, ValueError):
            errs.append(f"'{p.label}' has an invalid value.")
            continue
        out[p.name] = v
    return errs, out
