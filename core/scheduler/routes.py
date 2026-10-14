"""FastAPI router for the Scheduler  —  /sched/*   (included from api.py).

Authorisation
-------------
  * RMS_AUTH_REQUIRED=false (auth switched off): everything is allowed, user shows as the rms_user cookie.
  * auth on:  read endpoints need a signed-in user;
              create / edit / pause / resume / enable / disable / delete need an ADMIN;
              Run Now needs a signed-in user.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from core.scheduler import catalog, connections, engine, paths, proc_settings, rules, service, store

router = APIRouter(prefix="/sched", tags=["scheduler"])
UTC = dt.timezone.utc


def _who(request: Request):
    """(display identity, is_admin, signed_in)"""
    try:
        from core.auth import auth_required, current_user
        u = current_user(request)
        if not auth_required():                      # auth switched off → open, like the rest of the console
            if u:
                return f"{u.get('name') or u.get('sub')} ({u.get('sub')})", True, True
            ck = unquote(request.cookies.get("rms_user", "")).strip()
            return (ck or "unknown user"), True, True
        if u:
            return f"{u.get('name') or u.get('sub')} ({u.get('sub')})", bool(u.get("is_admin")), True
    except Exception:
        pass
    return "", False, False


def _deny(msg, code=403):
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


def _guard(request: Request, admin: bool):
    who, is_admin, signed = _who(request)
    if not signed:
        return None, _deny("Please sign in first.", 401)
    if admin and not is_admin:
        return None, _deny("Only an administrator can change schedules.")
    return who, None


def _gate(request: Request, body: dict):
    """Who may change recipients / credentials: an administrator, OR anyone signed in who supplies the same console
    password the Process Center asks for (its Email To/CC and Credentials buttons work the same way).
    Always removes the '_password' field from `body` so it can never be stored."""
    pw = str((body or {}).pop("_password", "") or "")
    who, is_admin, signed = _who(request)
    if not signed:
        return None, _deny("Please sign in first.", 401)
    if is_admin:
        return who, None
    ok = False
    try:
        from core.short_allocation import check_password
        ok = bool(pw) and bool(check_password(pw))
    except Exception:
        ok = False
    if not ok:
        return None, _deny("Wrong or missing password — enter the console password (the one the Process Center asks for).", 403)
    return who, None


async def _json(request: Request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _safe(fn):
    try:
        return fn(), None
    except service.ValidationFailed as e:
        return None, JSONResponse({"ok": False, "error": "; ".join(e.errors), "errors": e.errors}, status_code=422)
    except engine.BusyError as e:
        return None, JSONResponse({"ok": False, "error": str(e)}, status_code=409)
    except KeyError as e:
        return None, JSONResponse({"ok": False, "error": str(e).strip("'\"")}, status_code=404)
    except Exception as e:
        return None, JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


# ── read ────────────────────────────────────────────────────────────────────
@router.get("/processes")
def processes(request: Request):
    who, err = _guard(request, False)
    if err:
        return err
    return {"processes": [d.to_dict() for d in catalog.all_defs()]}


@router.get("/summary")
def summary(request: Request):
    who, err = _guard(request, False)
    if err:
        return err
    out, e = _safe(lambda: service.summary(health=engine.get_engine().health()))
    if e:
        return e
    out["needs_password"] = not _who(request)[1]            # the UI asks for the console password first when True
    return out


@router.get("/schedules")
def schedules(request: Request, status: str = "", q: str = ""):
    who, err = _guard(request, False)
    if err:
        return err

    def go():
        now = dt.datetime.now(UTC)
        items = [service.view(s, now) for s in store.list_schedules()]
        if status:
            items = [i for i in items if i["display_status"] == status]
        if q:
            ql = q.lower()
            items = [i for i in items if ql in i["name"].lower() or ql in (i["process_name"] or "").lower()]
        return {"items": items}
    out, e = _safe(go)
    return e or out


@router.get("/schedules/{sid}")
def schedule_one(sid: str, request: Request):
    who, err = _guard(request, False)
    if err:
        return err
    s = store.get_schedule(sid)
    if not s:
        return _deny("Schedule not found.", 404)
    return service.view(s)


@router.post("/preview")
async def preview(request: Request):
    who, err = _guard(request, False)
    if err:
        return err
    body = await _json(request)
    out, e = _safe(lambda: service.preview(body))
    return e or out


@router.get("/executions")
def executions(request: Request, date: str = "", process: str = "", schedule: str = "",
               status: str = "", limit: int = 200):
    who, err = _guard(request, False)
    if err:
        return err

    def go():
        d0 = d1 = None
        if date:
            tz = rules.get_tz(rules.DEFAULT_TZ)
            day = dt.date.fromisoformat(date[:10])
            d0 = dt.datetime.combine(day, dt.time(0, 0), tzinfo=tz).astimezone(UTC)
            d1 = d0 + dt.timedelta(days=1)
        rows = store.list_executions(d0, d1, process, schedule, status.upper(), min(max(limit, 1), 500))
        return {"items": [service.execution_view(r) for r in rows]}
    out, e = _safe(go)
    return e or out


@router.get("/executions/{eid}")
def execution_one(eid: str, request: Request):
    who, err = _guard(request, False)
    if err:
        return err
    r = store.get_execution(eid)
    if not r:
        return _deny("Execution not found.", 404)
    return service.execution_view(r)


@router.get("/audit")
def audit(request: Request, limit: int = 200):
    who, err = _guard(request, False)
    if err:
        return err
    rows = store.list_audit(min(max(limit, 1), 500))
    for r in rows:
        d = store.parse_ts(r["ts_utc"])
        r["ts_text"] = rules.fmt_local(d, rules.DEFAULT_TZ, True)
    return {"items": rows}


# ── write ───────────────────────────────────────────────────────────────────
@router.post("/schedules")
async def create(request: Request):
    body = await _json(request)
    who, err = _gate(request, body)
    if err:
        return err
    out, e = _safe(lambda: service.view(service.create(body, who)))
    return e or {"ok": True, "schedule": out}


@router.put("/schedules/{sid}")
async def edit(sid: str, request: Request):
    body = await _json(request)
    who, err = _gate(request, body)
    if err:
        return err

    def go():
        s = service.update(sid, body, who)
        if s is None:
            raise KeyError("Schedule not found.")
        return service.view(s)
    out, e = _safe(go)
    return e or {"ok": True, "schedule": out}


def _action(fn_name):
    async def handler(sid: str, request: Request):
        who, err = _gate(request, await _json(request))
        if err:
            return err

        def go():
            s = getattr(service, fn_name)(sid, who)
            if s is None:
                raise KeyError("Schedule not found.")
            return service.view(s)
        out, e = _safe(go)
        return e or {"ok": True, "schedule": out}
    return handler


for _name in ("pause", "resume", "enable", "disable"):
    router.add_api_route(f"/schedules/{{sid}}/{_name}", _action(_name), methods=["POST"], name=f"sched_{_name}")


@router.delete("/schedules/{sid}")
async def delete(sid: str, request: Request):
    who, err = _gate(request, await _json(request))
    if err:
        return err

    def go():
        if not service.delete(sid, who):
            raise KeyError("Schedule not found.")
        return True
    out, e = _safe(go)
    return e or {"ok": True}


@router.post("/schedules/{sid}/run-now")
async def run_now(sid: str, request: Request):
    who, err = _gate(request, await _json(request))     # admin, or the console password — nobody runs it by chance
    if err:
        return err
    out, e = _safe(lambda: engine.get_engine().run_now(sid, who))
    return e or {"ok": True, "execution_id": out}


# ── connections (WhatsApp / SMTP / POP3) — secrets are never returned ───────
@router.get("/connections")
def connections_get(request: Request):
    who, err = _guard(request, False)
    if err:
        return err
    return {"groups": connections.view(), "can_edit": True, "needs_password": not _who(request)[1]}


@router.post("/verify-password")
async def verify_password(request: Request):
    """Checked BEFORE the recipients / credentials forms open (same flow as the Process Center)."""
    body = await _json(request)
    who, err = _gate(request, {"_password": body.get("password", "")})
    return err or {"ok": True}


@router.put("/connections/{group}")
async def connections_put(group: str, request: Request):
    body = await _json(request)
    who, err = _gate(request, body)
    if err:
        return err

    def go():
        try:
            return connections.update(group, body, who)
        except ValueError as ex:
            raise service.ValidationFailed([str(ex)])
    out, e = _safe(go)
    return e or {"ok": True, "changed": out}


# ── RMS LIMITS MAND KAMBALA: "Add client codes" (appends codes, e-mails, uploads to Kambala) ──
@router.post("/rms-limits/add")
async def rms_limits_add(request: Request):
    body = await _json(request)
    who, err = _gate(request, body)
    if err:
        return err
    from core.scheduler.processes import rms_limits_mand_kambala as rl

    def go():
        pdef = catalog.get("rms_limits_mand_kambala")
        try:
            out = rl.start_add(body.get("rows"), catalog.effective_params(pdef, {}), bool(body.get("upload")))
        except ValueError as ex:
            raise service.ValidationFailed([m.strip() for m in str(ex).split(";") if m.strip()])
        store.audit(who, "rms_limits.add", {"id": None, "name": rl.FOLDER}, after={"rows": body.get("rows")})
        return out
    out, e = _safe(go)
    return e or {"ok": True, **out}


@router.get("/rms-limits/state")
def rms_limits_state(request: Request, id: str = ""):
    who, err = _guard(request, False)
    if err:
        return err
    from core.scheduler.processes import rms_limits_mand_kambala as rl
    st = rl.add_state(id)
    return st or JSONResponse({"ok": False, "error": "Unknown run."}, status_code=404)


# ── Portal Downloads: file jobs defined on screen (core/scheduler/portal_jobs.py) ──
def _portal_view(j: dict, schedules: list) -> dict:
    from core.scheduler import portal_jobs as pj
    key = pj.KEY_PREFIX + j["id"]
    mine = [s for s in schedules if s.get("process_key") == key]
    try:
        r = pj.resolve(j)
    except Exception as e:
        r = {"filename": j.get("filename", ""), "date_text": "", "error": str(e)}
    return {**j, "key": key, "resolved": r,
            "schedules": [{"id": s["id"], "name": s.get("name"), "status": s.get("status")} for s in mine]}


@router.get("/portal")
def portal_list(request: Request):
    who, err = _guard(request, False)
    if err:
        return err
    from core.scheduler import portal_jobs as pj

    def go():
        schedules = store.list_schedules()
        return {"jobs": [_portal_view(j, schedules) for j in sorted(pj.load_jobs(), key=lambda x: x["name"].lower())],
                "tokens": [{"token": t, "example": e} for t, e in pj.TOKEN_HELP],
                "defaults": {"signature": pj.DEFAULT_SIGNATURE, "body": pj.DEFAULT_BODY},
                "needs_password": not _who(request)[1]}
    out, e = _safe(go)
    return e or out


@router.post("/portal/preview")
async def portal_preview(request: Request):
    """Live 'what would this fetch today?' for the form (same code the run uses)."""
    who, err = _guard(request, False)
    if err:
        return err
    from core.scheduler import portal_jobs as pj
    body = await _json(request)
    errs, job = pj.clean_job(body, existing_id=str(body.get("id") or "") or "preview")
    try:
        r = pj.resolve(job) if job["filename"] else {}
    except Exception as e:
        r = {"error": str(e)}
    return {"ok": True, "errors": errs, "resolved": r}


@router.post("/portal")
async def portal_create(request: Request):
    body = await _json(request)
    who, err = _gate(request, body)
    if err:
        return err
    from core.scheduler import portal_jobs as pj

    def go():
        try:
            job = pj.save_job(body)
        except ValueError as ex:
            raise service.ValidationFailed([m.strip() for m in str(ex).split(";") if m.strip()])
        store.audit(who, "portal.create", {"id": None, "name": job["name"]}, after=job)
        return job
    out, e = _safe(go)
    return e or {"ok": True, "job": out}


@router.put("/portal/{jid}")
async def portal_update(jid: str, request: Request):
    body = await _json(request)
    who, err = _gate(request, body)
    if err:
        return err
    from core.scheduler import portal_jobs as pj

    def go():
        before = pj.get_job(jid)
        try:
            job = pj.save_job(body, job_id=jid)
        except ValueError as ex:
            raise service.ValidationFailed([m.strip() for m in str(ex).split(";") if m.strip()])
        store.audit(who, "portal.update", {"id": None, "name": job["name"]}, before=before, after=job)
        return job
    out, e = _safe(go)
    return e or {"ok": True, "job": out}


@router.delete("/portal/{jid}")
async def portal_delete(jid: str, request: Request):
    body = await _json(request)
    who, err = _gate(request, body)
    if err:
        return err
    from core.scheduler import portal_jobs as pj

    def go():
        j = pj.get_job(jid)
        if not j:
            raise KeyError("Unknown job.")
        used = [s for s in store.list_schedules() if s.get("process_key") == pj.KEY_PREFIX + jid]
        if used:
            raise service.ValidationFailed([f"This job still has {len(used)} schedule(s) — delete them on the Schedules tab first."])
        pj.delete_job(jid)
        store.audit(who, "portal.delete", {"id": None, "name": j["name"]}, before=j)
        return True
    out, e = _safe(go)
    return e or {"ok": True}


@router.post("/portal/test")
async def portal_test_start(request: Request):
    who, err = _guard(request, False)
    if err:
        return err
    from core.scheduler import portal_jobs as pj
    body = await _json(request)
    try:
        return {"ok": True, **pj.start_test(body)}
    except ValueError as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=422)
    except Exception as ex:
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"}, status_code=500)


@router.get("/portal/test/{tid}")
def portal_test_state(tid: str, request: Request):
    who, err = _guard(request, False)
    if err:
        return err
    from core.scheduler import portal_jobs as pj
    st = pj.test_state(tid)
    return {"ok": True, **st} if st else _deny("Test not found (it may have expired).", 404)


# ── per-process files: data/scheduler/<PROCESS NAME>/{inputs,outputs,logs,backup} ──
_MAX_UPLOAD = 100 * 1024 * 1024


def _pdef(pkey: str):
    d = catalog.get(pkey)
    if not d or not d.folder:
        raise KeyError("Unknown process.")
    return d


def _list_dir(d, limit=60):
    out = []
    if d.is_dir():
        for p in sorted((x for x in d.iterdir() if x.is_file()), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
            st = p.stat()
            out.append({"name": p.name, "size": st.st_size,
                        "modified": dt.datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M:%S")})
    return out


@router.get("/files/{pkey}")
def files_list(pkey: str, request: Request):
    who, err = _guard(request, False)
    if err:
        return err

    def go():
        d = _pdef(pkey)
        paths.ensure_process_dirs(d.folder)
        backups = []
        bdir = paths.proc_dir(d.folder, "backup")
        for p in sorted((x for x in bdir.iterdir() if x.is_dir()), key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
            backups.append({"name": p.name, "files": [f.name for f in p.iterdir() if f.is_file()]})
        return {"process": d.name, "folder": str(paths.proc_dir(d.folder)),
                "upload_exts": list(d.upload_exts), "upload_help": d.upload_help,
                "inputs": _list_dir(paths.proc_dir(d.folder, "inputs")),
                "outputs": _list_dir(paths.proc_dir(d.folder, "outputs")),
                "logs": _list_dir(paths.proc_dir(d.folder, "logs")),
                "backup": backups}
    out, e = _safe(go)
    return e or out


@router.post("/files/{pkey}/upload")
async def files_upload(pkey: str, request: Request, file: UploadFile = File(...)):
    who, err = _guard(request, False)
    if err:
        return err

    async def go():
        d = _pdef(pkey)
        if not d.upload_exts:
            raise ValueError("This process does not take uploaded files.")
        name = paths.safe_name(file.filename or "")
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in d.upload_exts:
            raise ValueError(f"Only {', '.join(d.upload_exts)} files can be uploaded here.")
        data = await file.read()
        if not data:
            raise ValueError("The file is empty.")
        if len(data) > _MAX_UPLOAD:
            raise ValueError("The file is larger than 100 MB.")
        dest = paths.proc_dir(d.folder, "inputs") / name
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)                                   # a run never sees a half-written file
        store.audit(who, "file.upload", {"id": None, "name": d.name}, after={"file": name, "bytes": len(data)})
        return name
    try:
        name = await go()
        return {"ok": True, "name": name}
    except KeyError as ex:
        return _deny(str(ex).strip("'\""), 404)
    except ValueError as ex:
        return _deny(str(ex), 422)
    except Exception as ex:
        return _deny(f"{type(ex).__name__}: {ex}", 500)


@router.get("/files/{pkey}/download")
def files_download(pkey: str, request: Request, area: str = "outputs", name: str = ""):
    who, err = _guard(request, False)
    if err:
        return err
    try:
        d = _pdef(pkey)
        if area not in ("inputs", "outputs", "logs"):
            return _deny("Not allowed.", 400)
        p = paths.resolve_inside(d.folder, area, name)
        if not p.is_file():
            return _deny("File not found.", 404)
        return FileResponse(str(p), filename=p.name)
    except KeyError as ex:
        return _deny(str(ex).strip("'\""), 404)
    except ValueError as ex:
        return _deny(str(ex), 400)


@router.get("/files/{pkey}/log")
def files_log(pkey: str, request: Request, name: str = ""):
    """Text of one run log (last ~200 KB)."""
    who, err = _guard(request, False)
    if err:
        return err
    try:
        p = paths.resolve_inside(_pdef(pkey).folder, "logs", name)
        if not p.is_file():
            return _deny("File not found.", 404)
        data = p.read_bytes()[-200_000:]
        return {"name": p.name, "text": data.decode("utf-8", errors="replace")}
    except KeyError as ex:
        return _deny(str(ex).strip("'\""), 404)
    except ValueError as ex:
        return _deny(str(ex), 400)


# ── process-level settings (recipients …) ───────────────────────────────────
@router.get("/process-settings/{pkey}")
def settings_get(pkey: str, request: Request):
    who, err = _guard(request, False)
    if err:
        return err

    def go():
        d = _pdef(pkey)
        return {"process": d.name, "values": proc_settings.view(d),
                "params": [p.to_dict() for p in d.params if p.scope == "process"],
                "can_edit": True, "needs_password": not _who(request)[1]}
    out, e = _safe(go)
    return e or out


@router.put("/process-settings/{pkey}")
async def settings_put(pkey: str, request: Request):
    body = await _json(request)
    who, err = _gate(request, body)
    if err:
        return err
    try:
        clean = proc_settings.save(_pdef(pkey), body, who)
        return {"ok": True, "values": clean}
    except KeyError as ex:
        return _deny(str(ex).strip("'\""), 404)
    except ValueError as ex:
        return _deny(str(ex), 422)
    except Exception as ex:
        return _deny(f"{type(ex).__name__}: {ex}", 500)


@router.delete("/files/{pkey}")
async def files_delete(pkey: str, request: Request, name: str = ""):
    """Remove a wrongly uploaded input file (inputs only; any signed-in user, like uploading)."""
    who, err = _guard(request, False)
    if err:
        return err
    try:
        d = _pdef(pkey)
        p = paths.resolve_inside(d.folder, "inputs", name)
        if not p.is_file():
            return _deny("File not found.", 404)
        p.unlink()
        store.audit(who, "file.delete", {"id": None, "name": d.name}, before={"file": p.name})
        return {"ok": True}
    except KeyError as ex:
        return _deny(str(ex).strip("'\""), 404)
    except ValueError as ex:
        return _deny(str(ex), 400)
