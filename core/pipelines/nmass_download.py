"""
core/pipelines/nmass_download.py - SSE generator for the NMASS + NCFE file downloader.

Runs NMASS_NCFE_API_MIX.PY as a subprocess and streams every output line as a
Server-Sent Event so the dashboard can show live progress.

Script path: env var CTCL_NMASS_SCRIPT
  default:  D:\\MY CODES WORKING\\NMASS_NCFE_API_MIX.PY

The script downloads four files into its target folder (CTCL_NMASS_FOLDER for
the CTCL vs NMASS box, MARGIN_SHORTAGE_FOLDER for the Margin Shortage 15-min
box - see `pipe` param below):
  - CM_ClientLevelCollaterals_<DDMMYYYY>.csv
  - FO_ClientLevelCollaterals_<DDMMYYYY>.csv
  - CO_ClientLevelCollaterals_<DDMMYYYY>.csv
  - ClientNetEffectiveDeposits_<DDMMYYYY>.csv

Typical runtime: ~20 seconds.
"""
import datetime
import os
import queue
import re
import subprocess
import threading
from core.pipeline_box import _PB, act_slot, clear_if_stale_act
from core.error_translator import translate_error
from core import dl_lock

# Tags NMASS_NCFE_API_MIX.PY prints on a confirmed wrong password / locked
# account (NCFE = the NCDEX-side login embedded in that same script).
# This generator used to pass every line through as a plain "log" event with
# no scanning at all, so a locked account just looked like ordinary output -
# the exit code stayed 0 (see the matching fix in NMASS_NCFE_API_MIX.PY) and
# the dashboard said "downloaded successfully" while NCDEX had silently failed.
_AUTH_BAD = re.compile(r"\[(NMASS|MCX90|NCFE)_(AUTH_FAIL|LOCKED)\]", re.I)

# Human-readable server names for the plain-English banner (non-technical
# users read this strip, not the raw log).
_FRIENDLY_SERVER = {"NCFE": "NCDEX", "NMASS": "NMASS", "MCX90": "MCX 90%"}


def _friendly_auth_msg(server, reason):
    name = _FRIENDLY_SERVER.get((server or "").upper(), server or "the exchange")
    if reason == "LOCKED":
        return (f"Account is LOCKED ({name}) - the download cannot continue. "
                f"Contact the exchange / your dealing desk to get it unlocked, "
                f"then run this again.")
    return (f"Login failed ({name}) - the password appears to be wrong. "
            f"Update it in Credentials and try again.")


# Which env var (+ default) holds each pipe's NMASS/NCDEX download folder.
# NOTE: unlike MCX90, these files are NEVER shared between ctcl and ms15 -
# each pipe always downloads its own copy independently (per explicit
# instruction: NSCCL/NCDEX take only 40-50s, no need to share).
# "ms15" here is the PIPELINES dict key (core/pipeline_box.py) for the
# Margin Shortage (15-min) box; its DB archival tag is the separate string
# "margin_shortage" (core/pipelines/margin_shortage_15min.py).
_PIPE_FOLDER = {
    "ctcl":     ("CTCL_NMASS_FOLDER",     r"D:\CTCL VS NMASS"),
    "ms15":     ("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes"),
    "probable": ("PROBABLE_FOLDER",        r"D:\PROBABLE"),
    # Segment-wise Collateral Movement Report (Live Snapshot's CM/FO/CO/NCDEX
    # leg) - see core/pipelines/r1_r2_comparison.py. Default mirrors that
    # module's own BASE_FOLDER default (repo/data/r1_r2_comparison) so the
    # two agree even if R1R2_FOLDER is never set in .env.
    "r1r2":     ("R1R2_FOLDER", os.path.join(
                     os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "data", "r1_r2_comparison")),
}

# How stale the 4 NMASS/NCDEX files are allowed to be before a fresh download
# is triggered. CTCL and MS15 run every 15–30 min all day, so the default
# 4-minute window (NMASS_MAX_FILE_AGE_MIN env var) stays exactly as-is for
# them — that's what makes each of their runs pull genuinely current data.
# PROBABLE runs ONCE a day and downloads well ahead of the actual run, so a
# 4-minute window there meant clicking the button again 10 minutes later
# always re-downloaded even though nothing meaningful could have changed —
# this is the "4-minute rule I haven't added here at all" the operator
# flagged. 30 minutes for PROBABLE specifically; unrelated to the other pipes.
_PIPE_MAX_AGE_MIN = {"probable": 30.0}


# ═══════════════════════════════════════════════════════════════════════════
# "ALREADY FRESH — SKIP THE LOGIN" PRE-CHECK
#
# Every NMASS/NCDEX download costs a full exchange login: a captcha the operator
# has to read and type, a session that the whole team shares one of, and about a
# minute of waiting. Re-paying that when the files on disk are already current is
# pure waste — and worse, it burns the single shared NMASS session, which is what
# makes a colleague's run collide and loop on the captcha.
#
# So before logging in at all, check what's already there. The collateral files
# carry their own generation timestamp IN THE FILENAME:
#
#     CM_ClientLevelCollaterals_29072026113154.csv
#                               └──┬───┘└──┬─┘
#                            DDMMYYYY    HHMMSS      → 29/07/2026 11:31:54
#
# which is the exchange's own "as of" time — far more trustworthy than the file's
# modified time (that only says when it landed on this disk, which a copy, a
# restore or a backup script can change without the DATA being any newer).
#
# ClientNetEffectiveDeposits has no such stamp, so it falls back to modified time.
# MCX 90% is deliberately NOT covered here at all — it has a random filename, is
# downloaded by a different module, and is shared with CTCL on paired run times.
# ═══════════════════════════════════════════════════════════════════════════

# Files whose freshness comes from the DDMMYYYYHHMMSS stamp in their own name.
_STAMPED_PREFIXES = ("CM_ClientLevelCollaterals",
                     "FO_ClientLevelCollaterals",
                     "CO_ClientLevelCollaterals")
# Files with no in-name stamp — fall back to the filesystem's modified time.
_MTIME_PREFIXES = ("ClientNetEffectiveDeposits",)

_STAMP_RE = re.compile(r"_(\d{2})(\d{2})(\d{4})(\d{2})(\d{2})(\d{2})")


def _stamp_from_name(fname):
    """Parse DDMMYYYYHHMMSS out of a collateral filename → datetime, or None."""
    m = _STAMP_RE.search(fname)
    if not m:
        return None
    dd, mm, yyyy, hh, mi, ss = (int(x) for x in m.groups())
    try:
        return datetime.datetime(yyyy, mm, dd, hh, mi, ss)
    except ValueError:
        return None


def _newest_for(folder, prefix):
    """Newest file in `folder` starting with `prefix`, or None."""
    try:
        cands = [f for f in os.listdir(folder) if f.upper().startswith(prefix.upper())]
    except Exception:
        return None
    if not cands:
        return None
    # Sort by the in-name stamp when there is one, else by modified time.
    def _key(f):
        st = _stamp_from_name(f)
        if st:
            return st
        try:
            return datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(folder, f)))
        except Exception:
            return datetime.datetime.min
    return max(cands, key=_key)


def check_nmass_files_fresh(folder, max_age_min=None):
    """Are all four NMASS/NCDEX inputs present AND recent enough to reuse?

    Returns {"fresh": bool, "detail": [str, ...]} — detail is one human-readable
    line per file, used for the dashboard strip either way, so a SKIP always says
    exactly which files it trusted and how old each one was.
    """
    if max_age_min is None:
        try:
            max_age_min = float(os.getenv("NMASS_MAX_FILE_AGE_MIN", "4"))
        except Exception:
            max_age_min = 4.0
    now = datetime.datetime.now()
    detail, all_fresh = [], True

    for prefix in _STAMPED_PREFIXES + _MTIME_PREFIXES:
        fname = _newest_for(folder, prefix)
        if not fname:
            detail.append(f"{prefix}: not present")
            all_fresh = False
            continue
        stamped = prefix in _STAMPED_PREFIXES
        when = _stamp_from_name(fname) if stamped else None
        src = "file time"
        if when is None:
            # Either an unstamped file, or a stamped one whose name didn't parse —
            # fall back to modified time rather than treating it as missing.
            try:
                when = datetime.datetime.fromtimestamp(
                    os.path.getmtime(os.path.join(folder, fname)))
            except Exception:
                detail.append(f"{fname}: unreadable")
                all_fresh = False
                continue
        else:
            src = "in filename"
        age_min = (now - when).total_seconds() / 60.0
        ok = 0 <= age_min <= max_age_min
        # A stamp in the FUTURE means a clock mismatch somewhere — don't trust it
        # as "fresh", download again rather than silently reusing something odd.
        detail.append(f"{fname}: {age_min:.1f} min old ({src})" + ("" if ok else " → too old"))
        if not ok:
            all_fresh = False

    return {"fresh": all_fresh, "detail": detail, "max_age_min": max_age_min}


def stream_nmass_download(pipe="ctcl", force=False):
    """Generator that runs the NMASS download script and yields SSE event dicts.

    `pipe` is "ctcl" (default, CTCL vs NMASS box) or "ms15" (Margin Shortage
    15-min box) - selects which folder the 4 files land in.

    Event shapes:
      {"type": "log",   "msg": str}          - one output line
      {"type": "done",  "ok": bool, "msg": str}  - final result
      {"type": "error", "msg": str}          - fatal error
      {"type": "end"}                        - always last, close the EventSource
    """
    script = os.getenv("CTCL_NMASS_SCRIPT",
                       os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                    "ctcl_codes", "NMASS_NCFE_API_MIX.PY"))
    python = os.getenv("CTCL_PYTHON", os.getenv("SA_PYTHON", "python"))

    _env_key, _default_dir = _PIPE_FOLDER.get(pipe, _PIPE_FOLDER["ctcl"])
    _own_dir = os.getenv(_env_key, _default_dir)
    os.makedirs(_own_dir, exist_ok=True)
    _folder_note = (f"[{pipe}] saving into: {_own_dir}  (from {_env_key}"
                     f"{'=' + os.environ[_env_key] if _env_key in os.environ else ' — NOT SET, using default'})")
    # Says out loud what this SERVER PROCESS resolved the save folder to. A
    # folder mistake used to be invisible until someone went and checked the
    # disk themselves — worse, it was invisible EVEN IN THE SAVED LOG, since
    # this line used to only be yielded live (nothing here writes to _logpath
    # until much further down, right before the subprocess starts) — so
    # anyone checking after the fact via 🗂️ Logs would never see it. It's
    # written to the log file (see "# saving into" below) as well as yielded
    # here for the live strip, so it's there either way. If it ever disagrees
    # with where the files actually land, the bug is inside
    # NMASS_NCFE_API_MIX.PY (or downstream of it), not in this resolution step.
    yield {"type": "log", "msg": _folder_note}

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"]       = "1"
    env["PYTHONUNBUFFERED"] = "1"  # child stdout is block-buffered on a pipe otherwise -
                                    # a slow login/retry loop with low print volume can then
                                    # look completely hung with a near-empty log for minutes
    env["NMASS_SAVE_DIR"]   = _own_dir  # tells NMASS_NCFE_API_MIX.PY where to save
                                          # this run's 4 files

    # -- log file ---------------------------------------------------------------
    # Pipe-aware logdir/name (was hardcoded to "ctcl" for every pipe — meant an
    # ms15/probable download's log never showed up in THAT pipe's own Logs tab,
    # only ctcl's). PIPELINES[pipe]["logdir"] always matches pipe's own key
    # (see core/pipeline_box.py's registry) so this now lands in the right place
    # for whichever pipe actually called this.
    from core.pipeline_box import PIPELINES as _PIPELINES
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _logdir_key = _PIPELINES.get(pipe, {}).get("logdir", "ctcl")
    _logdir = os.path.join(_repo, "logs", _logdir_key)
    os.makedirs(_logdir, exist_ok=True)
    _ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _logname = f"{pipe}_download__{_ts}.log"
    _logpath = os.path.join(_logdir, _logname)
    # Write the header + the folder-resolution line into the log file RIGHT
    # NOW, rather than waiting for the subprocess-reading loop further down to
    # open the file — that loop uses "w" (truncate), so writing here FIRST and
    # switching that later open to "a" (append) is what keeps this line in the
    # saved log instead of only ever appearing in the live SSE stream.
    try:
        with open(_logpath, "w", encoding="utf-8", errors="replace") as _lf0:
            _lf0.write(f"# NMASS Download  start={_ts}\n")
            try:
                from core.pipeline_box import who_started_line as _wsl
                _lf0.write(_wsl())
            except Exception:
                pass
            _lf0.write(f"# {_folder_note}\n")
    except Exception:
        pass  # never let a logging problem stop the actual download

    if not os.path.isfile(script):
        yield {"type": "log",   "msg": f"NMASS script not found: {script}"}
        yield {"type": "error", "msg": f"Script not found: {script}"}
        yield {"type": "end"}
        return

    # ── Skip the whole login if what's on disk is already current ──────────
    # Done BEFORE dl_lock.acquire() on purpose: if we're not going to download,
    # we must not take the shared NMASS login lock even briefly, or we'd stall a
    # colleague who genuinely needs it.
    if not force:
        _fresh = check_nmass_files_fresh(_own_dir, max_age_min=_PIPE_MAX_AGE_MIN.get(pipe))
        for _d in _fresh["detail"]:
            yield {"type": "log", "msg": "  " + _d}
        if _fresh["fresh"]:
            yield {"type": "log",
                   "msg": f"All 4 files already present and under "
                          f"{_fresh['max_age_min']:.0f} min old — skipping the NMASS/NCDEX "
                          f"login entirely (no captcha needed)."}
            yield {"type": "done", "ok": True,
                   "msg": f"Files already current — download skipped "
                          f"(all 4 within {_fresh['max_age_min']:.0f} min)", "log": ""}
            yield {"type": "end"}
            return
        yield {"type": "log", "msg": "Files missing or too old — a fresh download is needed."}

    yield {"type": "log", "msg": f"Starting NMASS download: {os.path.basename(script)}"}
    yield {"type": "log", "msg": "Downloading CM / FO / CO / ClientNetEffectiveDeposits..."}

    # The subprocess about to start writes its captcha image / session cookie
    # to a FIXED path shared by ctcl/ms15/cli (NMASS_NCFE_API_MIX.PY's own
    # TEMP_FOLDER) — this is the one real conflict between pipes, so it's
    # guarded by its own narrow, short-lived cross-process lock instead of
    # blocking anything else about this pipe or any other.
    try:
        dl_lock.acquire(pipe)
    except dl_lock.NmassLoginBusyError as e:
        yield {"type": "log", "msg": f"NMASS/NCDEX login is in use by '{e.owner}' right now — waiting is "
                                      f"usually under a minute; try again shortly."}
        yield {"type": "error", "msg": f"⚠ NMASS/NCDEX login already in progress (started by {e.owner}). "
                                        f"This step takes well under a minute — try again shortly."}
        yield {"type": "end"}
        return

    q = queue.Queue()
    # Own slot, not the pipe's shared run slot — so this download no longer
    # blocks (or gets blocked by) that box's other buttons. See the long note
    # above act_slot() in core/pipeline_box.py.
    _pb, _pb_lock = act_slot(pipe, "nmass")
    with _pb_lock:
        clear_if_stale_act(pipe, "nmass")
        if _pb.get("running"):
            yield {"type": "log", "msg": "An NMASS/NCDEX download is already running for this box."}
            yield {"type": "error", "msg": "⚠ This box's NMASS/NCDEX download is already in progress — "
                                            "wait for it to finish, or press Stop."}
            yield {"type": "end"}
            return
        _pb["running"] = True
        _pb["started_at"] = datetime.datetime.now().isoformat()
    try:                                   # who clicked it — so only that person gets this run's captcha popup
        from core.pipeline_box import _log_identity
        _pb["by"] = _log_identity()[0]
    except Exception:
        _pb["by"] = None
    _pb["cancel"] = False
    _auth_fail = None  # set to {"server":.., "reason":..} if a login_failed tag is seen
    _log_buffer = []   # last lines the script printed - used by the AI fallback below
                        # when the failure is a new/unrecognized kind (no known tag matched)

    try:
        proc = subprocess.Popen(
            [python, "-u", script],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
        )
    except Exception as e:
        dl_lock.release()
        _pb["running"] = False; _pb["proc"] = None; _pb["started_at"] = None
        yield {"type": "log",   "msg": f"Failed to start NMASS script: {e}"}
        yield {"type": "error", "msg": str(e)}
        yield {"type": "end"}
        return

    _pb["proc"] = proc; _pb["running"] = True

    def _reader():
        for line in proc.stdout:
            q.put(("log", line.rstrip()))
        proc.wait()
        q.put(("rc", proc.returncode))

    threading.Thread(target=_reader, daemon=True).start()

    rc = None
    try:
        # "a" (append), not "w" — the header + folder-resolution line were
        # already written above, before we even knew whether this run would
        # get this far (freshness-skip / script-not-found return earlier).
        with open(_logpath, "a", encoding="utf-8", errors="replace") as _lf:
            while True:
                try:
                    kind, val = q.get(timeout=2)
                except queue.Empty:
                    if _pb.get("cancel"):
                        try: proc.terminate()
                        except Exception: pass
                    yield {"type": "log", "msg": "Waiting for NMASS download..."}
                    continue
                if kind == "log":
                    _pb["last_event_at"] = datetime.datetime.now().isoformat()
                    _lf.write(val + "\n")
                    yield {"type": "log", "msg": val}
                    _log_buffer.append(val)
                    m = _AUTH_BAD.search(val)
                    if m:
                        _auth_fail = {"server": m.group(1).upper(), "reason": m.group(2).upper()}
                        yield {"type": "login_failed", "server": _auth_fail["server"],
                               "reason": _auth_fail["reason"], "msg": val}
                elif kind == "rc":
                    rc = val
                    break
    finally:
        _pb["proc"] = None; _pb["running"] = False
        dl_lock.release()   # release the moment the subprocess is done, not held
                             # for the rest of this pipe's own compute/email phase

    if _pb.get("cancel"):
        yield {"type": "stopped", "msg": "Stopped by user.", "log": _logname}
        yield {"type": "end"}
        return

    if rc == 0:
        yield {"type": "done", "ok": True,  "msg": "NMASS files downloaded successfully - 4 files ready", "log": _logname}
    elif _auth_fail:
        yield {"type": "done", "ok": False,
               "msg": _friendly_auth_msg(_auth_fail["server"], _auth_fail["reason"]),
               "log": _logname}
    else:
        # No known tag matched - this is a NEW kind of failure we've never seen before.
        # Try the free Groq AI fallback first (reads the last lines the script printed
        # and explains them in plain English); if that's unavailable/fails, fall back
        # to the old fixed message instead of ever showing a raw exit code.
        _ai_msg = translate_error("NCDEX/NMASS download", _log_buffer)
        _msg = _ai_msg or f"NMASS download did not complete (exit code {rc}) - check Logs or contact tech support."
        yield {"type": "done", "ok": False, "msg": _msg, "log": _logname}
    yield {"type": "end"}
