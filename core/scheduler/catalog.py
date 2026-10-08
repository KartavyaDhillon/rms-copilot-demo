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


class Skipped(Exception):
    """Raised by a process that deliberately did nothing (e.g. 'already e-mailed today').
    The run is recorded as SKIPPED with this message — not as a success and not as a failure."""


@dataclass
class Param:
    name: str
    label: str
    type: str = "str"
    required: bool = False
    default: Any = None
    choices: Optional[List[str]] = None
    help: str = ""
    fmt: str = ""                          # "phones" | "emails" → checked when saved
    scope: str = "schedule"                # "process" = a setting of the process itself (recipients …), edited on the
                                           # Processes tab and shared by every schedule of that process

    def to_dict(self):
        return {"name": self.name, "label": self.label, "type": self.type, "required": self.required,
                "default": self.default, "choices": self.choices, "help": self.help, "fmt": self.fmt, "scope": self.scope}


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
    folder: str = ""                       # data/scheduler/<folder>/ — inputs, outputs, backup, logs
    upload_exts: tuple = ()                # e.g. (".xlsx",) → the Files tab shows an Upload button
    upload_help: str = ""
    uses: tuple = ()                       # Connections groups this process needs: "whatsapp" | "smtp" | "pop3"

    def to_dict(self):
        return {"key": self.key, "name": self.name, "description": self.description, "category": self.category,
                "params": [p.to_dict() for p in self.params],
                "retry_safe": self.retry_safe, "parallel_safe": self.parallel_safe,
                "folder": self.folder, "upload_exts": list(self.upload_exts), "upload_help": self.upload_help,
                "uses": list(self.uses)}


_REG: Dict[str, ProcessDef] = {}
_LOCK = threading.Lock()
_LOADED = False
_LOADING = False                       # True only while the first load is running (guards same-thread re-entry)
_LOAD_LOCK = threading.RLock()


def register(key, name, run, description="", params=None, retry_safe=False, parallel_safe=False,
             lock_pipe=None, category="General", folder="", upload_exts=(), upload_help="",
             uses=()) -> ProcessDef:
    if not key or not callable(run):
        raise ValueError("register(): key and a callable run are required")
    for p in params or []:
        if p.type not in PARAM_TYPES:
            raise ValueError(f"{key}: parameter '{p.name}' has unsupported type '{p.type}' "
                             f"(secrets are deliberately not supported — keep them in .env)")
    d = ProcessDef(key=key, name=name, run=run, description=description, params=list(params or []),
                   retry_safe=retry_safe, parallel_safe=parallel_safe, lock_pipe=lock_pipe, category=category,
                   folder=folder, upload_exts=tuple(e.lower() for e in upload_exts), upload_help=upload_help,
                   uses=tuple(uses))
    with _LOCK:
        _REG[key] = d
    return d


def unregister(key):
    with _LOCK:
        _REG.pop(key, None)


def load_builtin():
    """Import every module in core/scheduler/processes/ once (each registers itself)."""
    global _LOADED, _LOADING
    if _LOADED:
        return
    # The Scheduler page asks for /schedules and /processes at the same moment.  Before, the first request marked the
    # catalogue "loaded" and started importing, and the second saw "loaded" and read it while it was still EMPTY, so
    # the page showed "(not registered) missing" until the next refresh.  Now every caller waits here until the
    # import has finished, and _LOADED is only set at the end.
    with _LOAD_LOCK:
        if _LOADED or _LOADING:        # done already, or this very thread is mid-import (a module asked the catalogue)
            return
        _LOADING = True
        try:
            from core.scheduler import processes as pkg
            for m in pkgutil.iter_modules(pkg.__path__):
                if m.name.startswith("_"):
                    continue
                try:
                    importlib.import_module(f"core.scheduler.processes.{m.name}")
                except Exception as e:                # one broken process file must not break the rest
                    print(f"[scheduler] could not load process module '{m.name}': {type(e).__name__}: {e}", flush=True)
        except Exception as e:
            print(f"[scheduler] process package not loadable: {e}", flush=True)
        finally:
            _LOADING = False
        _LOADED = True


def get(key) -> Optional[ProcessDef]:
    load_builtin()
    return _REG.get(key)


def all_defs() -> List[ProcessDef]:
    load_builtin()
    return sorted(_REG.values(), key=lambda d: (d.category, d.name))


def effective_params(pdef: ProcessDef, schedule_params: dict) -> dict:
    """What the process actually receives: the schedule's own parameters + the process-level settings
    (recipients …) saved on the Processes tab. Process-level settings always win."""
    from core.scheduler import proc_settings
    out = dict(schedule_params or {})
    out.update(proc_settings.get(pdef))
    return out


def validate_params(pdef: ProcessDef, values: dict, scope: str = "schedule") -> (list, dict):
    """-> (errors, cleaned values). Unknown keys are rejected; types are coerced.
    Only parameters of the given scope are considered ("schedule" or "process")."""
    errs, out = [], {}
    values = dict(values or {})
    plist = [p for p in pdef.params if p.scope == scope]
    known = {p.name: p for p in plist}
    for k in values:
        if k not in known:
            errs.append(f"Unknown parameter '{k}'.")
    for p in plist:
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
                if p.fmt:
                    from core.scheduler import helpers
                    {"phones": helpers.parse_phones, "emails": helpers.parse_emails}[p.fmt](v)
        except (TypeError, ValueError) as ex:
            errs.append(f"'{p.label}': {ex}" if p.fmt and str(ex) else f"'{p.label}' has an invalid value.")
            continue
        out[p.name] = v
    return errs, out
