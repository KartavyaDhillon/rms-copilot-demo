"""
core/pipelines/nmass_alloc_upload.py — upload a Shortfall Allocation file to NMASS.

The Margin Shortage (15-min) pipeline generates an allocation file at the end of
every cycle (see the "SHORTFALL ALLOCATION FILE" block in
core/pipelines/margin_shortage_15min.py). That file is written to disk under a
PLACEHOLDER batch number and nothing is sent anywhere — uploading is always an
explicit, deliberate operator action, never something a scheduled run does on
its own.

When the operator does choose to upload, this module:
  1. runs NMASS_NCFE_API_MIX.PY in `alloc_upload` mode (which reuses that
     script's existing login / captcha / session-restart machinery),
  2. that script asks NMASS which allocation batch is currently last
     (files-list, folderType=upload) and claims the next number,
  3. renames the file to the claimed batch and POSTs it (upload-files),
  4. renames the local copy to match, so disk and NMASS never disagree.

The batch number CANNOT be decided here, ahead of time: other people and
systems upload allocation files for the same member code all day, so "which
batch is next" is only knowable at the moment of upload, from NMASS itself.
"""
import datetime
import os
import queue
import re
import subprocess
import threading

from core.pipeline_box import act_slot, clear_if_stale_act
from core.error_translator import translate_error
from core import dl_lock

# MS15 writes both the working .xlsx and the final fixed-format file here.
# Deliberately a SUBfolder of MARGIN_SHORTAGE_FOLDER: backup_and_cleanup()
# in margin_shortage_15min.py sweeps only top-level files and explicitly skips
# directories, so allocation files survive the end-of-run cleanup instead of
# being swept into BACKUPFILES AUTOMATED the moment they're created.
ALLOC_SUBDIR = "ALLOC_FILES"

# <CONSCODE>_ALLOC_<DDMMYYYY>.T#### — the exact shape NMASS's upload folder uses.
_ALLOC_NAME_RE = re.compile(r"^(\d+)_ALLOC_(\d{8})\.T(\d{4})$", re.I)

# Printed by the script once the upload is confirmed, carrying the batch name
# NMASS actually accepted. Parsed back out so the dashboard can report it.
_UPLOADED_RE = re.compile(r"\[ALLOC_UPLOADED\]\s+(\S+)")


def alloc_dir():
    """Folder MS15 writes allocation files into."""
    base = os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")
    return os.path.join(base, ALLOC_SUBDIR)


def list_alloc_files(today_only=True):
    """Allocation files on disk, newest first — feeds the dashboard's picker.

    Returns a list of dicts: {name, size, modified, batch, date, is_today}.
    """
    d = alloc_dir()
    today = datetime.datetime.now().strftime("%d%m%Y")
    out = []
    try:
        names = os.listdir(d)
    except Exception:
        return out

    for name in names:
        m = _ALLOC_NAME_RE.match(name)
        if not m:
            continue          # skip the working .xlsx and anything else
        _cons, ddmmyyyy, batch = m.groups()
        if today_only and ddmmyyyy != today:
            continue
        path = os.path.join(d, name)
        try:
            st = os.stat(path)
        except Exception:
            continue
        out.append({
            "name": name,
            "size": st.st_size,
            "modified": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d-%m-%Y %H:%M:%S"),
            "batch": int(batch),
            "date": ddmmyyyy,
            "is_today": ddmmyyyy == today,
            "lines": _count_lines(path),
        })

    out.sort(key=lambda r: r["modified"], reverse=True)
    return out


def _count_lines(path):
    """Number of allocation rows in the file — shown in the picker so the
    operator can sanity-check they're about to send the right snapshot."""
    try:
        with open(path, "rb") as f:
            return sum(1 for ln in f if ln.strip())
    except Exception:
        return 0


def preview_alloc_file(name, max_rows=15):
    """First few rows of an allocation file, for the confirm dialog."""
    path = os.path.join(alloc_dir(), os.path.basename(name))
    rows = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    rows.append(ln)
                if len(rows) >= max_rows:
                    break
    except Exception:
        pass
    return rows


# Always Immediate/Intraday. This file exists to cover a shortfall that is live
# right now, so the EOD batch would be useless for it — there is no situation
# where this pipeline should send anything else, hence no option to get it wrong.
ALLOC_REQUEST_TYPE = "I"


def stream_alloc_upload(name):
    """Generator that uploads one allocation file and yields SSE event dicts.

    `name` — filename inside ALLOC_FILES (not a full path; anything with a
    directory component in it is rejected).
    """
    # ── validate the target ────────────────────────────────────────────────
    safe_name = os.path.basename((name or "").strip())
    if not safe_name or safe_name != (name or "").strip():
        yield {"type": "error", "msg": "Invalid allocation file name."}
        yield {"type": "end"}
        return
    if not _ALLOC_NAME_RE.match(safe_name):
        yield {"type": "error",
               "msg": f"'{safe_name}' is not an allocation file "
                      f"(expected <code>_ALLOC_<DDMMYYYY>.T####)."}
        yield {"type": "end"}
        return

    src = os.path.join(alloc_dir(), safe_name)
    if not os.path.isfile(src):
        yield {"type": "error", "msg": f"File not found: {src}"}
        yield {"type": "end"}
        return

    script = os.getenv("CTCL_NMASS_SCRIPT",
                       os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                    "ctcl_codes", "NMASS_NCFE_API_MIX.PY"))
    python = os.getenv("CTCL_PYTHON", os.getenv("SA_PYTHON", "python"))
    if not os.path.isfile(script):
        yield {"type": "error", "msg": f"NMASS script not found: {script}"}
        yield {"type": "end"}
        return

    yield {"type": "log", "msg": f"Allocation file : {src}"}
    yield {"type": "log", "msg": f"Rows to upload  : {_count_lines(src)}"}
    yield {"type": "log", "msg": "Upload type     : Immediate / Intraday"}
    yield {"type": "log", "msg": "The batch number will be taken from NMASS itself "
                                  "(last existing batch + 1) just before sending."}

    # ── log file ───────────────────────────────────────────────────────────
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logdir = os.path.join(repo, "logs", "ms15")
    os.makedirs(logdir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logname = f"ms15_alloc_upload__{ts}.log"
    logpath = os.path.join(logdir, logname)
    try:
        with open(logpath, "w", encoding="utf-8", errors="replace") as lf0:
            lf0.write(f"# Allocation Upload  start={ts}\n")
            lf0.write(f"# file={src}  type=Immediate/Intraday\n")
    except Exception:
        pass

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["NMASS_RUN_MODE"] = "alloc_upload"
    env["NMASS_ALLOC_FILE"] = src
    env["NMASS_ALLOC_REQUEST_TYPE"] = ALLOC_REQUEST_TYPE
    # THIS is what keeps the captcha inside the dashboard instead of throwing a
    # bare Tk window onto the server's desktop. captcha_popup() in
    # NMASS_NCFE_API_MIX.PY writes .captcha_state / .captcha_nmass.png into
    # NMASS_SAVE_DIR and polls there for the answer; /ms15/captcha/pending reads
    # them from MARGIN_SHORTAGE_FOLDER. Without this line NMASS_SAVE_DIR was
    # unset, so the script fell back to CTCL_NMASS_FOLDER — a folder the ms15
    # captcha endpoint never looks at — the dashboard never learned a captcha
    # was waiting, and the script popped its own desktop window instead (which
    # nobody sees when the server runs headless or on another machine).
    env["NMASS_SAVE_DIR"] = os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes")

    # Same shared captcha/session file as every other NMASS call — take the
    # same narrow cross-process lock so an upload can't collide with a
    # colleague's download mid-login.
    try:
        dl_lock.acquire("ms15-alloc")
    except dl_lock.NmassLoginBusyError as e:
        yield {"type": "error",
               "msg": f"⚠ NMASS login already in progress (started by {e.owner}). "
                      f"This takes well under a minute — try again shortly."}
        yield {"type": "end"}
        return

    q = queue.Queue()
    _pb, _pb_lock = act_slot("ms15", "alloc_upload")
    with _pb_lock:
        clear_if_stale_act("ms15", "alloc_upload")
        if _pb.get("running"):
            dl_lock.release()
            yield {"type": "error", "msg": "⚠ An allocation upload is already running."}
            yield {"type": "end"}
            return
        _pb["running"] = True
        _pb["started_at"] = datetime.datetime.now().isoformat()
    _pb["cancel"] = False

    try:
        proc = subprocess.Popen(
            [python, "-u", script],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
        )
    except Exception as e:
        dl_lock.release()
        _pb["running"] = False; _pb["proc"] = None; _pb["started_at"] = None
        yield {"type": "error", "msg": f"Failed to start upload: {e}"}
        yield {"type": "end"}
        return

    _pb["proc"] = proc

    def _reader():
        for line in proc.stdout:
            q.put(("log", line.rstrip()))
        proc.wait()
        q.put(("rc", proc.returncode))

    threading.Thread(target=_reader, daemon=True).start()

    rc = None
    uploaded_as = None
    log_buffer = []
    try:
        with open(logpath, "a", encoding="utf-8", errors="replace") as lf:
            while True:
                try:
                    kind, val = q.get(timeout=2)
                except queue.Empty:
                    if _pb.get("cancel"):
                        try: proc.terminate()
                        except Exception: pass
                    yield {"type": "log", "msg": "Waiting for NMASS..."}
                    continue
                if kind == "log":
                    _pb["last_event_at"] = datetime.datetime.now().isoformat()
                    lf.write(val + "\n")
                    yield {"type": "log", "msg": val}
                    log_buffer.append(val)
                    m = _UPLOADED_RE.search(val)
                    if m:
                        uploaded_as = m.group(1)
                elif kind == "rc":
                    rc = val
                    break
    finally:
        _pb["proc"] = None; _pb["running"] = False
        dl_lock.release()

    if _pb.get("cancel"):
        yield {"type": "stopped", "msg": "Stopped by user.", "log": logname}
        yield {"type": "end"}
        return

    if rc == 0 and uploaded_as:
        yield {"type": "done", "ok": True,
               "msg": f"Uploaded to NMASS as {uploaded_as} (Immediate/Intraday). "
                      f"Use the refresh icon on the NMASS page to see the return file.",
               "uploaded_as": uploaded_as, "log": logname}
    elif rc == 0:
        # Exit 0 but no confirmation tag — treat as a failure rather than
        # reporting a success we never actually saw NMASS confirm.
        yield {"type": "done", "ok": False,
               "msg": "Upload finished without a confirmation from NMASS — "
                      "check the log and the NMASS upload folder before retrying.",
               "log": logname}
    else:
        ai = translate_error("NMASS allocation upload", log_buffer)
        yield {"type": "done", "ok": False,
               "msg": ai or f"Allocation upload failed (exit code {rc}) — see Logs.",
               "log": logname}
    yield {"type": "end"}
