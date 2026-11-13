"""Shared engine of the two peak-margin upload processes (MCX PEAK MARGIN UPLOAD, NCL PEAK MARGIN UPLOAD).

Ported from D:\\MCX PEAK FILES UPLOAD\\mcx_peakmargin_zip_upload.py and D:\\NCCL FILE UPLOAD\\ncl_peakmargin_zip_upload.py.

What changed compared with the two scripts
------------------------------------------
* WHICH DATE: the scripts walked back weekday by weekday and treated "files missing" as "holiday". That silently
  uploaded an OLDER day when a day's files were simply not there yet (5-11-2026: 03-11, 02-11 and 30-10 were skipped
  and 29-10 was uploaded). Now the date is T-2 from the market-holiday calendar (the same holidays_list.csv as the rest
  of the scheduler), and a missing file is an ERROR (failure alert), never a reason to pick another day.
* DAYS THAT WERE MISSED: every uploaded trade date is written to a ledger. Each run also uploads the trading days of the
  last N days (setting "Catch up missed days") that are not in the ledger yet, oldest first. Each run also counts every
  data\\scheduler\\<process>\\backup\\<dd-mm-yyyy> folder holding a zip as uploaded (own backup only). The very first
  run on a server (empty ledger) uploads T-2 only - catch-up starts from the next run.
* FILES:
    NSE CM/FO/CO .lis   -> NSE extranet (Scheduler > Connections > NSE), then the 76 FILES DOWNLOADER day folder.
    MCX 8 peak csv      -> MCX file portal (Connections > MCX file portal), then the 76 FILES DOWNLOADER day folder.
    NCDEX 8 peak csv    -> NCDEX web extranet with the captcha auto-filled (the same code as the 76 FILES DOWNLOADER),
                           then the console's 76 FILES DOWNLOADER day folder (nothing else).
* UPLOAD: Connections > "Mastertrust upload API" (no password in the code). The answer must say Success=true.
* AFTER: zip -> data\\scheduler\\<process>\\backup\\<trade date dd-mm-yyyy>\\, working files deleted.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from core.scheduler import connections, helpers as H, paths

UPLOAD_LOGIN = "/api/auth/login"
UPLOAD_FILE = "/api/dataservice/customFileImport"


# ───────────────────────────── ledger: which trade dates are uploaded ─────────────────────────────
def _ledger_file(folder: str) -> Path:
    return paths.proc_dir(folder, "state") / "uploaded.json"


def ledger(folder: str) -> Dict[str, str]:
    try:
        return json.loads(_ledger_file(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mark_uploaded(folder: str, d: dt.date, note: str = ""):
    led = ledger(folder)
    led[d.isoformat()] = (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + (f" {note}" if note else "")).strip()
    keep = sorted(led)[-400:]                                   # about 1.5 years is plenty
    _ledger_file(folder).write_text(json.dumps({k: led[k] for k in keep}, indent=1), encoding="utf-8")


def sync_from_own_backup(folder: str, ctx) -> int:
    """Every run: each data\\scheduler\\<process>\\backup\\<dd-mm-yyyy> folder that holds a zip = already uploaded
    (merged into the list, never removes anything). Only the process's OWN backup is looked at."""
    led = ledger(folder)
    n = 0
    root = paths.proc_dir(folder, "backup")
    try:
        for sub in root.iterdir():
            try:
                d = dt.datetime.strptime(sub.name, "%d-%m-%Y").date()
            except ValueError:
                continue
            if d.isoformat() in led:
                continue
            if sub.is_dir() and any(f.suffix.lower() == ".zip" for f in sub.iterdir()):
                led[d.isoformat()] = "zip found in this process's backup folder"
                n += 1
    except OSError as e:
        ctx.log(f"Backup folder not readable ({root}): {e}")
    if n:
        keep = sorted(led)[-400:]
        _ledger_file(folder).write_text(json.dumps({k: led[k] for k in keep}, indent=1), encoding="utf-8")
        ctx.log(f"{n} trade date(s) found in backup\\ - they count as uploaded.")
    return n


def dates_to_do(folder: str, catch_up_days: int, force: Optional[dt.date], today: Optional[dt.date] = None
                ) -> Tuple[dt.date, List[dt.date]]:
    """(T-2 trade date, dates to upload oldest first). T-2 = two TRADING days back on the holiday calendar."""
    today = today or dt.date.today()
    t2 = H.prev_trading_day(today, 2)
    if force:
        return t2, [force]
    led = ledger(folder)
    if not led:                       # very first run on this server: nothing to compare with -> T-2 only, no catch-up
        return t2, [t2]
    todo, d = [], t2
    oldest = t2 - dt.timedelta(days=max(0, int(catch_up_days or 0)))
    while d >= oldest:
        if d.isoformat() not in led:
            todo.append(d)
        d = H.prev_trading_day(d)
    return t2, sorted(todo)


def parse_force_date(v) -> Optional[dt.date]:
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d%m%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"'Upload only this trade date' must look like 29-10-2026 (got {v!r}).")


# ───────────────────────────── getting the files ─────────────────────────────
def f70_ctx(ctx):
    from core import files70
    return files70._Ctx(ctx.log, lambda: bool(getattr(ctx, "cancelled", False)))


def copy_from_backups(names: List[str], dest: Path, sources: List[Path], ctx, label: str) -> List[str]:
    """Copy each missing name from the first backup folder that has it (any letter case, not empty). -> still missing."""
    still = []
    for n in names:
        hit = None
        for src in sources:
            try:
                listing = {x.lower(): x for x in __import__("os").listdir(src)}
            except OSError:
                continue
            real = listing.get(n.lower())
            if real and (src / real).stat().st_size > 0:
                hit = src / real
                break
        if hit:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(hit, dest / n)
            ctx.log(f"  ✓ {n} taken from the backup {hit.parent}")
        else:
            still.append(n)
    if still:
        ctx.log(f"{label}: not in any backup either: {', '.join(still)}")
    return still


def present(names: List[str], folder: Path) -> List[str]:
    out = []
    for n in names:
        p = folder / n
        if p.is_file() and p.stat().st_size > 0:
            out.append(n)
    return out


# ───────────────────────────── zip + upload ─────────────────────────────
def build_zip(zip_path: Path, files: List[Path], ctx) -> int:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    ctx.log(f"Zipped {len(files)} file(s) into {zip_path.name} ({zip_path.stat().st_size:,} bytes).")
    return len(files)


_TRUST = {"done": False, "how": ""}


def _requests_with_windows_certs(ctx=None):
    """service.mastertrust.co.in uses a certificate that Python's own list does not know, but Windows does (the old
    scripts worked on PCs with pip-system-certs). Use the Windows certificate store: truststore (pip install truststore)
    or pip-system-certs, whichever is installed. -> the requests module."""
    requests = H.need("requests")
    if not _TRUST["done"]:
        _TRUST["done"] = True
        try:
            import truststore
            truststore.inject_into_ssl()
            _TRUST["how"] = "truststore"
        except Exception:
            try:
                import pip_system_certs.wrapt_requests  # noqa: F401
                _TRUST["how"] = "pip-system-certs"
            except Exception:
                _TRUST["how"] = ""
        if ctx is not None:
            ctx.log("Upload API: using the Windows certificate store (" + _TRUST["how"] + ")." if _TRUST["how"] else
                    "Upload API: neither truststore nor pip-system-certs is installed - Python's own certificate list is used.")
    return requests


def _is_cert_error(e) -> bool:
    m = str(e)
    return "CERTIFICATE_VERIFY_FAILED" in m or "certificate verify failed" in m


def _cert_help() -> str:
    return ("the upload server's SSL certificate is not trusted by this Python. Fix once on the console PC: "
            "open a terminal in the console's Python and run  pip install truststore  then restart the console.")


class _UploadAuth:
    def __init__(self, folder: str, ctx):
        c = connections.require("upload_api", "base_url", "user_id", "password")
        self.base = c["base_url"].rstrip("/")
        self.user, self.pw = c["user_id"], c["password"]
        self.ctx = ctx
        self.cache = paths.proc_dir(folder, "state") / "upload_token.json"
        self.token, self.exp = None, None
        try:
            j = json.loads(self.cache.read_text(encoding="utf-8"))
            if j.get("user") == self.user:
                self.token, self.exp = j.get("token"), dt.datetime.fromisoformat(j.get("expires_at"))
        except Exception:
            pass

    def get(self, force: bool = False) -> str:
        if not force and self.token and self.exp and dt.datetime.now() < self.exp - dt.timedelta(minutes=5):
            return self.token
        requests = _requests_with_windows_certs(self.ctx)
        last = ""
        for n in range(1, 11):
            if getattr(self.ctx, "cancelled", False):
                raise RuntimeError("stopped")
            try:
                r = requests.post(self.base + UPLOAD_LOGIN, json={"userId": self.user, "password": self.pw}, timeout=20)
                if r.status_code in (500, 502, 503, 504):
                    last = f"HTTP {r.status_code}"
                else:
                    body = r.json() if r.content else {}
                    data = body.get("data") or {}
                    tok = data.get("accessToken")
                    if r.ok and body.get("success") and tok:
                        self.token = tok
                        self.exp = dt.datetime.now() + dt.timedelta(seconds=int(data.get("accessTokenExpiresIn") or 3600))
                        self.cache.write_text(json.dumps({"user": self.user, "token": tok,
                                                          "expires_at": self.exp.isoformat()}), encoding="utf-8")
                        self.ctx.log("Upload API: logged in.")
                        return tok
                    if r.status_code in (400, 401, 403):
                        raise RuntimeError(f"Upload API refused the login (HTTP {r.status_code}): {r.text[:200]} - "
                                           "check Scheduler > Connections > Mastertrust upload API.")
                    last = f"HTTP {r.status_code}: {r.text[:150]}"
            except RuntimeError:
                raise
            except Exception as e:
                if _is_cert_error(e):
                    raise RuntimeError("Upload API: " + _cert_help())        # retrying cannot fix a certificate
                last = f"{type(e).__name__}: {e}"
            self.ctx.log(f"Upload API login try {n}/10 failed ({last}) - again in 5 s.")
            time.sleep(5)
        raise RuntimeError(f"Upload API login failed 10 times - {last}")


def upload(folder: str, zip_path: Path, template_name: str, d: dt.date, ctx, mime: str = "application/zip",
           part_name: Optional[str] = None) -> str:
    """POST customFileImport (file = the zip, fileName = the literal template, date = DD-MM-YYYY). -> the answer text.
    mime / part_name: for a file that is not a zip (the FO files upload) - the content type and the file name sent in
    the multipart `file` part (default: the zip's own name)."""
    requests = _requests_with_windows_certs(ctx)
    auth = _UploadAuth(folder, ctx)
    force, last = False, ""
    for n in range(1, 9):
        if getattr(ctx, "cancelled", False):
            raise RuntimeError("stopped")
        tok = auth.get(force)
        force = False
        try:
            with open(zip_path, "rb") as fh:
                r = requests.post(auth.base + UPLOAD_FILE, headers={"Authorization": f"Bearer {tok}"},
                                  files={"file": (part_name or zip_path.name, fh, mime)},
                                  data={"fileName": template_name, "date": f"{d:%d-%m-%Y}"}, timeout=180)
        except Exception as e:
            if _is_cert_error(e):
                raise RuntimeError("Upload API: " + _cert_help())
            last = f"{type(e).__name__}: {e}"
            ctx.log(f"Upload try {n}/8: {last} - again in 10 s.")
            time.sleep(10)
            continue
        ctx.log(f"Upload {zip_path.name} (date {d:%d-%m-%Y}) -> HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code == 401:
            force = True
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"HTTP {r.status_code}"
            time.sleep(10)
            continue
        ok_flag = None
        try:
            j = r.json()
            ok_flag = j.get("Success", j.get("success"))
        except Exception:
            pass
        if 200 <= r.status_code < 300 and ok_flag is not False:
            return r.text[:300]
        raise RuntimeError(f"The upload API did not accept {zip_path.name}: HTTP {r.status_code} {r.text[:300]}")
    raise RuntimeError(f"Upload of {zip_path.name} failed 8 times - {last}")


def archive(folder: str, d: dt.date, keep: List[Path], work: Path, ctx) -> Path:
    """Move the zip (and anything in keep) to backup\\<dd-mm-yyyy of the TRADE date>, then delete the work folder."""
    dest = paths.proc_dir(folder, "backup") / f"{d:%d-%m-%Y}"
    dest.mkdir(parents=True, exist_ok=True)
    for f in keep:
        if Path(f).is_file():
            t = dest / Path(f).name
            if t.exists():
                t = dest / f"{Path(f).stem}_{dt.datetime.now():%H%M%S}{Path(f).suffix}"
            Path(f).replace(t)
    shutil.rmtree(work, ignore_errors=True)
    ctx.log(f"Backed up to backup\\{dest.name}\\ and the working files were deleted.")
    return dest


# ───────────────────────────── the common run loop ─────────────────────────────
def run_dates(folder: str, params, ctx, one_day: Callable[[dt.date, Path], List[Path]], zip_prefix: str,
              template_name: str) -> str:
    """one_day(d, work_dir) must return the files to zip, or raise when a mandatory file is missing."""
    force = parse_force_date(params.get("force_date"))
    if not force and getattr(ctx, "trigger", "") != H.MANUAL and not H.is_trading_day(dt.date.today()):
        raise H.Skipped(f"{dt.date.today():%d-%m-%Y} is not a trading day (weekend / market holiday) - nothing to do.")
    sync_from_own_backup(folder, ctx)
    t2, todo = dates_to_do(folder, int(params.get("catch_up_days") or 0), force)
    ctx.log(f"T-2 trade date on the holiday calendar: {t2:%d-%m-%Y} ({t2:%A}).")
    if not todo:
        raise H.Skipped(f"{zip_prefix} of {t2:%d-%m-%Y} (and the catch-up days) are already uploaded - nothing to do. "
                        "Use 'Upload only this trade date' to send a day again.")
    ctx.log("To upload: " + ", ".join(f"{d:%d-%m-%Y}" for d in todo))
    done, failed = [], []
    for d in todo:
        if getattr(ctx, "cancelled", False):
            break
        ctx.log(f"\n===== {zip_prefix} - trade date {d:%d-%m-%Y} =====")
        work = paths.proc_dir(folder, "inputs") / f"{d:%d-%m-%Y}"
        try:
            files = one_day(d, work)
            zp = paths.proc_dir(folder, "outputs") / f"{zip_prefix}_{d:%d%m%Y}.zip"
            build_zip(zp, files, ctx)
            ans = upload(folder, zp, template_name, d, ctx)
            mark_uploaded(folder, d)
            archive(folder, d, [zp], work, ctx)
            done.append(d)
            ctx.log(f"✓ {d:%d-%m-%Y} uploaded. {ans[:160]}")
        except Exception as e:
            failed.append((d, f"{type(e).__name__}: {e}"))
            ctx.log(f"✗ {d:%d-%m-%Y} NOT uploaded - {type(e).__name__}: {e}")
            if str(e).startswith("Upload API"):           # the upload itself is broken: the other days would fail the same way
                ctx.log("The upload API cannot be used right now - the remaining days are left for the next run.")
                break
    summary = (f"uploaded: {', '.join(f'{d:%d-%m-%Y}' for d in done) or 'none'}")
    if failed:
        raise RuntimeError(summary + " | NOT uploaded: " + "; ".join(f"{d:%d-%m-%Y} ({m[:140]})" for d, m in failed))
    return f"{zip_prefix} {summary}"
