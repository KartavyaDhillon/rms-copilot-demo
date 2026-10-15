"""
core/pipelines/mcx90_download.py - SSE generator for the MCX 90% margin file downloader.

Runs MCX_90_PERCENT_FILE_API.py (TCS BANCS / eclear.mcxccl.com) as a subprocess.

Script path: env var MCX90_SCRIPT
  default:  D:\\MY CODES WORKING\\MCX_90_PERCENT_FILE_API.py

The script opens RSK335 "Margin Utilization View" on the TCS BANCS portal,
exports the Excel, polls for completion, and downloads it to its target folder
(CTCL_NMASS_FOLDER for the CTCL vs NMASS box, MARGIN_SHORTAGE_FOLDER for the
Margin Shortage (15-min) box - see `pipe` param below).
Output filename: <random name with YYYYMMDD datetime>.xlsx

NOTE: An RSA SecurID token popup (tkinter dialog) appears on the user's desktop
during this script. The user must type the 8-digit RSA code from the RSA app.

Typical runtime: 4-5 minutes (TCS BANCS server can be slow).

CTCL vs NMASS <-> Margin Shortage (15-min) MCX 90% sharing
------------------------------------------------------------
Both boxes need the same MCX 90% file, but a fresh TCS BANCS export+RSA-token
round trip takes 4-5 minutes - too slow to do twice at the same moment. Per
the confirmed run-window pairing, whichever box downloads MCX 90% first at a
paired time slot also copies the result into the sibling box's folder, and
the sibling's own MCX90 button skips its download entirely if today's file is
already sitting in its folder (see _today_mcx_file/_maybe_share_with_sibling
below). NSCCL (CM/FO/CO) and NCDEX files are NEVER shared this way - only
MCX 90%, per explicit instruction (those download in 40-50s, no need to share).
"""
import os
import glob
import queue
import re
import shutil
import subprocess
import threading
import datetime
from core.pipeline_box import _PB, act_slot
from core.error_translator import translate_error

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Tag MCX_90_PERCENT_FILE_API.py prints once its 3 password-retry popups are
# exhausted - this generator never scanned for it, so a confirmed wrong
# password just looked like ordinary log output.
_AUTH_BAD = re.compile(r"\[(NMASS|MCX90|NCFE)_(AUTH_FAIL|LOCKED)\]", re.I)

# Human-readable server names for the plain-English banner (non-technical
# users read this strip, not the raw log).
_FRIENDLY_SERVER = {"NCFE": "NCDEX", "NMASS": "NMASS", "MCX90": "MCX 90%"}

# Which env var (+ default) holds each pipe's MCX90 destination folder.
# NOTE: "ms15" is the PIPELINES dict key (core/pipeline_box.py) for the Margin
# Shortage (15-min) box — matches its MS15_RUN<n>_WINDOW env vars via
# pipe.upper(). The DB archival tag for that pipeline's own records is the
# separate string "margin_shortage" (core/pipelines/margin_shortage_15min.py) -
# the two names serve different purposes and don't need to match.
_PIPE_FOLDER = {
    "ctcl": ("CTCL_NMASS_FOLDER",     r"D:\CTCL VS NMASS"),
    "ms15": ("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes"),
}
# Which env var prefix holds each pipe's run windows (<PREFIX>_RUN<n>_WINDOW).
_PIPE_WINDOW_PREFIX = {"ctcl": "CTCL", "ms15": "MS15"}

# Confirmed exact pairing: (pipe, run#) -> (sibling pipe, sibling run#).
# CTCL-02(09:50-10:00)<->MS-02(09:50-10:05), CTCL-03(11:50-12:00)<->MS-04(12:00-12:15),
# CTCL-05(14:50-15:00)<->MS-08(14:55-15:10). Built both ways below.
_SHARE_PAIRS = {
    ("ctcl", 2): ("ms15", 2),
    ("ctcl", 3): ("ms15", 4),
    ("ctcl", 5): ("ms15", 8),
}
_SHARE_PAIRS.update({v: k for k, v in _SHARE_PAIRS.items()})


def _pipe_folder(pipe):
    env_key, default = _PIPE_FOLDER.get(pipe, _PIPE_FOLDER["ctcl"])
    d = os.getenv(env_key, default)
    os.makedirs(d, exist_ok=True)
    return d


def _current_window_index(pipe):
    """Return the 1-based run number whose <PREFIX>_RUN<n>_WINDOW currently
    contains 'now', or None if 'now' isn't inside any configured window for
    this pipe. Used purely to decide MCX90 sharing - not a state/counter."""
    prefix = _PIPE_WINDOW_PREFIX.get(pipe)
    if not prefix:
        return None
    now = datetime.datetime.now().time()
    n = 1
    while True:
        raw = os.getenv(f"{prefix}_RUN{n}_WINDOW")
        if not raw:
            break
        try:
            start_s, end_s = raw.split("-")
            sh, sm = [int(x) for x in start_s.split(":")]
            eh, em = [int(x) for x in end_s.split(":")]
            start = datetime.time(sh, sm)
            end = datetime.time(eh, em)
            if start <= now <= end:
                return n
        except Exception:
            pass
        n += 1
    return None


# How old today's MCX 90% file may be and still count as usable. Beyond this
# it is deleted and a fresh one downloaded.
#
# WHY: the check used to be "is there a file with TODAY's date?" with no age
# test at all — so a file pulled at 09:51 blocked the 11:30 run from
# downloading, and the run then computed off a nearly-two-hour-old snapshot of
# the exchange's numbers. Same-day is not the same as current.
_MCX90_MAX_AGE_MIN = float(os.getenv("MCX90_MAX_AGE_MIN", "45"))


def _file_age_min(path):
    """Age of `path` in minutes, or None if it can't be determined."""
    try:
        return (datetime.datetime.now()
                - datetime.datetime.fromtimestamp(os.path.getmtime(path))).total_seconds() / 60.0
    except Exception:
        return None


def _today_mcx_file(folder, max_age_min=None):
    """Newest MCX 90% file for TODAY in `folder` (random-name-with-YYYYMMDD
    pattern the underlying script produces), or None.

    max_age_min: when given (or when it defaults via _today_mcx_file_fresh),
    a file older than this is treated as NOT present, so the caller
    re-downloads instead of silently reusing a stale snapshot."""
    today = datetime.datetime.now().strftime("%Y%m%d")
    try:
        candidates = [f for f in glob.glob(os.path.join(folder, "*.xlsx")) if today in f]
    except Exception:
        return None
    if not candidates:
        return None
    candidates.sort(key=os.path.getmtime, reverse=True)
    newest = candidates[0]
    if max_age_min is not None:
        age = _file_age_min(newest)
        if age is None or age > max_age_min:
            return None
    return newest


def _purge_stale_mcx_files(folder, max_age_min=_MCX90_MAX_AGE_MIN):
    """Delete today's MCX 90% files that are older than `max_age_min`, so the
    "already present" check can't be satisfied by an out-of-date file and the
    download proceeds. Returns a list of human-readable log lines.

    Only ever touches files matching today's date — yesterday's files are left
    alone entirely (they're already excluded by the date filter and are somebody
    else's business), and nothing outside `folder` is considered."""
    lines = []
    today = datetime.datetime.now().strftime("%Y%m%d")
    try:
        candidates = [f for f in glob.glob(os.path.join(folder, "*.xlsx")) if today in f]
    except Exception:
        return lines
    for f in candidates:
        age = _file_age_min(f)
        if age is None or age <= max_age_min:
            continue
        try:
            os.remove(f)
            lines.append(f"Removed stale MCX 90% file {os.path.basename(f)} "
                         f"({age:.0f} min old, limit {max_age_min:.0f} min) — "
                         f"downloading a fresh one.")
        except Exception as e:
            lines.append(f"Could not remove stale MCX 90% file "
                         f"{os.path.basename(f)} ({age:.0f} min old): {e}")
    return lines


def _maybe_share_with_sibling(pipe, downloaded_file):
    """After a successful MCX90 download for `pipe`, check whether this run
    number is one of the 3 confirmed paired slots; if so, copy the file into
    the sibling pipe's folder too (skip if it's already there). Returns a list
    of human-readable log lines describing what (if anything) happened."""
    idx = _current_window_index(pipe)
    if idx is None:
        return []
    pair = _SHARE_PAIRS.get((pipe, idx))
    if not pair:
        return []
    sib_pipe, sib_idx = pair
    sib_folder = _pipe_folder(sib_pipe)
    # Age-aware: a sibling holding a STALE file used to count as "already has
    # it" and the fresh copy was withheld, leaving the sibling to run off the
    # old snapshot. Clear its stale copies first, then only skip if what's
    # left is genuinely current.
    stale_lines = _purge_stale_mcx_files(sib_folder)
    if _today_mcx_file(sib_folder, _MCX90_MAX_AGE_MIN):
        return stale_lines + [f"Sibling folder ({sib_pipe}) already has a current MCX 90% file - no copy needed."]
    try:
        dest = os.path.join(sib_folder, os.path.basename(downloaded_file))
        shutil.copy2(downloaded_file, dest)
        return stale_lines + [f"Shared MCX 90% file with {sib_pipe} (run #{sib_idx} window) -> {dest}"]
    except Exception as e:
        return stale_lines + [f"Could not share MCX 90% file with {sib_pipe}: {e}"]


def _friendly_auth_msg(server, reason):
    name = _FRIENDLY_SERVER.get((server or "").upper(), server or "the exchange")
    if reason == "LOCKED":
        return (f"Account is LOCKED ({name}) - the download cannot continue. "
                f"Contact the exchange / your dealing desk to get it unlocked, "
                f"then run this again.")
    return (f"Login failed ({name}) - the password appears to be wrong. "
            f"Update it in Credentials and try again.")


def stream_mcx90_download(pipe="ctcl"):
    """Generator that runs the MCX90 download script and yields SSE event dicts.

    `pipe` is "ctcl" (default, CTCL vs NMASS box) or "ms15" (Margin Shortage
    15-min box) - selects which folder the file lands in, and is used for the
    CTCL<->Margin-Shortage MCX90 sharing check (see module docstring).

    Full output is ALSO written to logs/ctcl/MCX90__<timestamp>.log so it shows
    up in the dashboard's Logs modal (the UI only shows a one-line status).

    Event shapes:
      {"type": "log",   "msg": str}              - one output line
      {"type": "done",  "ok": bool, "msg": str}  - final result
      {"type": "error", "msg": str}              - fatal error
      {"type": "end"}                            - always last, close the EventSource
    """
    script = os.getenv("MCX90_SCRIPT",
                       os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                    "ctcl_codes", "MCX_90_PERCENT_FILE_API.py"))
    python = os.getenv("CTCL_PYTHON", os.getenv("SA_PYTHON", "python"))

    own_dir = _pipe_folder(pipe)
    # Clear out anything from today that is already too old to use, BEFORE the
    # "already present" check — otherwise a stale file satisfies that check and
    # blocks the download, which is exactly what happened when a 09:51 file
    # stopped the 11:30 run from fetching current numbers.
    for _pl in _purge_stale_mcx_files(own_dir):
        yield {"type": "log", "msg": _pl}
    _existing = _today_mcx_file(own_dir, _MCX90_MAX_AGE_MIN)
    if _existing:
        _age = _file_age_min(_existing)
        _agetxt = f", {_age:.0f} min old" if _age is not None else ""
        _msg1 = ("Today's MCX 90% file already present (" + os.path.basename(_existing)
                 + _agetxt + ") - skipping download.")
        yield {"type": "log", "msg": _msg1}
        yield {"type": "done", "ok": True, "msg": "MCX 90% file already available"}
        yield {"type": "end"}
        return

    # -- persistent log file (appears in the CTCL Logs modal) --------------
    _lf = None
    try:
        _logdir = os.path.join(_REPO, "logs", "ctcl")
        os.makedirs(_logdir, exist_ok=True)
        _ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        _lf = open(os.path.join(_logdir, f"ctcl_mcx90__{_ts}.log"), "w",
                   encoding="utf-8", errors="replace")
        _lf.write(f"# MCX 90% download  start={_ts}\n"); _lf.flush()
    except Exception:
        _lf = None

    def _log(msg):
        if _lf:
            try:
                _lf.write(str(msg) + "\n"); _lf.flush()
            except Exception:
                pass

    def _close_log():
        if _lf:
            try:
                _lf.close()
            except Exception:
                pass

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"]       = "1"
    env["PYTHONUNBUFFERED"] = "1"  # child stdout is block-buffered on a pipe otherwise -
                                    # a slow login/retry loop with low print volume can then
                                    # look completely hung with a near-empty log for minutes
    env["MCX90_SAVE_DIR"]  = own_dir  # tells MCX_90_PERCENT_FILE_API.py where to save
                                        # this run's file - ctcl's folder by default,
                                        # Margin Shortage's folder when pipe="ms15"

    if not os.path.isfile(script):
        _log(f"MCX90 script not found: {script}")
        _close_log()
        yield {"type": "log",   "msg": f"MCX90 script not found: {script}"}
        yield {"type": "error", "msg": f"Script not found: {script}"}
        yield {"type": "end"}
        return

    for _m in (f"Starting MCX 90% download: {os.path.basename(script)}",
               "RSA SecurID popup will appear on your desktop - enter the 8-digit token code.",
               "TCS BANCS server can take 4-5 min - please wait..."):
        _log(_m)
        yield {"type": "log", "msg": _m}

    q = queue.Queue()
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
        _log(f"Failed to start MCX90 script: {e}")
        _close_log()
        yield {"type": "log",   "msg": f"Failed to start MCX90 script: {e}"}
        yield {"type": "error", "msg": str(e)}
        yield {"type": "end"}
        return

    # Own slot, not the pipe's shared run slot — MCX 90% (TCS BANCS) and the
    # NMASS/NCDEX download (NSE/NCDEX portal) are unrelated systems and can now
    # run side by side. See act_slot() in core/pipeline_box.py.
    _pb, _ = act_slot(pipe, "mcx90")
    _pb["cancel"] = False
    _pb["started_at"] = datetime.datetime.now().isoformat()
    try:                                   # who clicked it — so only that person gets this run's token popup
        from core.pipeline_box import _log_identity
        _pb["by"] = _log_identity()[0]
    except Exception:
        _pb["by"] = None
    _pb["proc"] = proc; _pb["running"] = True

    def _reader():
        for line in proc.stdout:
            q.put(("log", line.rstrip()))
        proc.wait()
        q.put(("rc", proc.returncode))

    threading.Thread(target=_reader, daemon=True).start()

    rc = None
    while True:
        try:
            kind, val = q.get(timeout=120)   # 2-min wait between log lines; actual download runs until subprocess finishes
        except queue.Empty:
            _log("Still waiting for TCS BANCS server...")
            yield {"type": "log", "msg": "Still waiting for TCS BANCS server..."}
            continue
        if kind == "log":
            _pb["last_event_at"] = datetime.datetime.now().isoformat()
            _log(val)
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

    _pb["proc"] = None; _pb["running"] = False

    if rc == 0:
        _log("MCX 90% file downloaded successfully")
        _downloaded = _today_mcx_file(own_dir)
        _share_msgs = _maybe_share_with_sibling(pipe, _downloaded) if _downloaded else []
        for _sm in _share_msgs:
            _log(_sm)
            yield {"type": "log", "msg": _sm}
        _close_log()
        yield {"type": "done", "ok": True,
               "msg": "MCX 90% file downloaded successfully"}
    elif _auth_fail:
        _log(f"MCX90 download finished with exit code {rc}")
        _close_log()
        yield {"type": "done", "ok": False,
               "msg": _friendly_auth_msg(_auth_fail["server"], _auth_fail["reason"])}
    else:
        # No known tag matched - a new kind of failure. Try the free Groq AI
        # fallback first; if it's unavailable/fails, use the old fixed message
        # instead of ever showing a raw exit code.
        _log(f"MCX90 download finished with exit code {rc}")
        _close_log()
        _ai_msg = translate_error("MCX 90% download", _log_buffer)
        _msg = _ai_msg or f"MCX 90% download did not complete (exit code {rc}) - check Logs or contact tech support."
        yield {"type": "done", "ok": False, "msg": _msg}
    yield {"type": "end"}
