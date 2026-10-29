"""
core/pipelines/mcx_deposit_download.py — SSE generator for MCX Deposit (RSK383) downloader.

Runs sa_codes/mcx_deposit_downloader.py as a subprocess.
Writes full output to logs/short_allocation/MCX_DEPOSIT__<timestamp>.log
so it appears in the SA dashboard's 🗂️ Logs modal.

Log is written line-by-line with flush() — even if the browser tab is closed
or the stream is terminated, every line printed up to that point is preserved.
"""
import os
import re
import queue
import subprocess
import threading
import datetime
import sys
from core.error_translator import translate_error

_BUSY_TAG = re.compile(r"\[MCX_DEPOSIT_BUSY:(\w+)\]")

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def stream_mcx_deposit_download(output_dir=None, caller="sa", extra_env=None):
    """Generator that runs mcx_deposit_downloader.py and yields SSE event dicts.

    `output_dir` (optional) is the folder the MCX_DEPOSIT_HH.MM.xlsx file is saved into - Short Allocation's own
    folder when omitted, the CTCL vs NMASS folder when CTCL's "MCX file download" button asks for the Deposit file.
    `caller` ("sa" or "ctcl") tells the dashboard's RSA token popup which box asked.

    Full output is written to logs/short_allocation/MCX_DEPOSIT__<timestamp>.log.
    Uses a background thread so the log is written even if the client disconnects.

    Event shapes:
      {"type": "log",   "msg": str}              — one output line
      {"type": "done",  "ok": bool, "msg": str}  — final result
      {"type": "error", "msg": str}              — fatal error
      {"type": "end"}                            — always last, close the EventSource

    Concurrency note: this does NOT claim ctcl/ms15/cli's shared _PB lock (an
    earlier version of this did, and ended up blocking Short Allocation's
    entire dashboard box — including already-done View buttons — just because
    Client Prudential Limit's unrelated MCX Web Allocation download was
    running, which was wrong; they don't actually conflict with each other).
    The ONE real shared resource is sa_codes/mcx_deposit_downloader.py's TCS
    BANCS session/.token_state file, used by both THIS button and CLI's "MCX
    Web Allocation" button — that has its own narrow, purpose-built lock
    inside mcx_deposit_downloader.py itself (_acquire_mcx_lock/_release_mcx_lock),
    which is where a genuine conflict between just those two gets caught (see
    the "[MCX_DEPOSIT_BUSY]" tag handling below).
    """
    script = os.path.join(_REPO, "sa_codes", "mcx_deposit_downloader.py")
    python = sys.executable

    # ── persistent log file (appears in SA Logs modal) ───────────────────────
    _lf = None
    try:
        # CTCL / Margin Shortage downloads are logged in THEIR OWN Logs; the SA button keeps the SA log.
        _own = caller in ("ctcl", "ms15")
        _logdir = os.path.join(_REPO, "logs", caller if _own else "short_allocation")
        os.makedirs(_logdir, exist_ok=True)
        _ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        _lf = open(os.path.join(_logdir, (f"{caller}_mcx_deposit__{_ts}.log" if _own else f"MCX_DEPOSIT__{_ts}.log")),
                   "w", encoding="utf-8", errors="replace")
        _lf.write(f"# mcx_deposit_downloader.py  start={_ts}\n")
        try:
            from core.pipeline_box import who_started_line as _wsl
            _lf.write(_wsl())
        except Exception:
            pass
        _lf.flush()
    except Exception:
        _lf = None

    def _log(msg):
        if _lf:
            try:
                _lf.write(str(msg) + "\n")
                _lf.flush()          # flush every line so termination still saves output
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
    env["PYTHONUNBUFFERED"] = "1"  # child stdout is block-buffered on a pipe otherwise —
                                    # a slow TCS BANCS login/export-poll loop with low print
                                    # volume can then look completely hung with a near-empty
                                    # log for minutes (this is what happened to SA-03/SA-04).
    try:   # the MCX Deposit button of Short Allocation also remembers its export, so a retry fetches the SAME file
        if caller in (None, "", "sa") and "MCX_EXPORT_STATE_FILE" not in (extra_env or {}) and not env.get("MCX_EXPORT_STATE_FILE"):
            from core import sa_auto as _saa
            env["MCX_EXPORT_STATE_FILE"] = _saa.deposit_state_file()
            env.setdefault("MCX_EXPORT_RESUME_MAX_MIN", "60")
    except Exception:
        pass
    env.update(extra_env or {})        # e.g. MCX_TOKEN_MODE=auto for the unattended Short Allocation auto run
    env["MCX_DEPOSIT_CALLER"] = caller  # "sa" = SA's OWN button (the default); "ctcl" = CTCL's MCX file download — this is SA's OWN
                                        # "MCX Deposit" button; CLI's "MCX Web Allocation"
                                        # button sets "cli" instead so the dashboard's RSA
                                        # token popup shows the correct owning box.

    if not os.path.isfile(script):
        _log(f"❌ Script not found: {script}")
        _close_log()
        yield {"type": "error", "msg": f"Script not found: {script}"}
        yield {"type": "end"}
        return

    _log("🚀 Starting MCX Deposit (RSK383) download from eclear.mcxccl.com")
    _log("ℹ️  RSA code: filled automatically when Token auto-fill is ON; otherwise a pop-up asks you for it.")
    _log("⏳ TCS BANCS server can take several minutes — please wait…")
    yield {"type": "log", "msg": "🚀 Starting MCX Deposit (RSK383) download"}
    yield {"type": "log", "msg": "ℹ️  RSA code: filled automatically when Token auto-fill is ON; otherwise a pop-up asks you for it."}

    q: queue.Queue = queue.Queue()
    _auth_fail = False  # set True if the wrong-password tag is seen
    _busy_msg = None    # set if the other caller (sa/cli) already has this in progress
    _log_buffer = []    # last lines the script printed — used by the AI fallback below
                         # when the failure is a new/unrecognized kind (no known tag matched)

    _argv = [python, "-u", script] + ([output_dir] if output_dir else [])
    try:
        proc = subprocess.Popen(
            _argv,
            cwd=os.path.join(_REPO, "sa_codes"),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
        )
    except Exception as e:
        _log(f"❌ Failed to start script: {e}")
        _close_log()
        yield {"type": "error", "msg": str(e)}
        yield {"type": "end"}
        return

    # Register proc so /sa/mcx-deposit-stop can kill it
    try:
        import api as _api_ref
        _api_ref._sa_mcx_dep_proc = proc
    except Exception:
        pass

    def _reader():
        """Background thread: reads subprocess output, writes to log, queues for SSE."""
        for line in proc.stdout:
            stripped = line.rstrip()
            _log(stripped)           # always written, even if client disconnects
            q.put(("log", stripped))
        proc.wait()
        q.put(("rc", proc.returncode))

    threading.Thread(target=_reader, daemon=True).start()

    rc = None
    while True:
        try:
            kind, val = q.get(timeout=120)   # 2-min wait — TCS BANCS is slow
        except queue.Empty:
            _log("⏳ Still waiting for TCS BANCS server…")
            yield {"type": "log", "msg": "⏳ Still waiting for TCS BANCS server…"}
            continue
        if kind == "log":
            yield {"type": "log", "msg": val}
            _log_buffer.append(val)
            # This generator never scanned for the auth-failure tag mcx_deposit_downloader.py
            # prints on a wrong password — so a wrong password just looked like any other
            # exit-code-1 failure, with nothing telling a non-technical user WHY it failed.
            if "[MCX_DEPOSIT_AUTH_FAIL]" in val:
                _auth_fail = True
                yield {"type": "login_failed", "server": "MCX Deposit", "msg": val}
            else:
                _busy_m = _BUSY_TAG.search(val)
                if _busy_m:
                    # Report the ACTUAL owner recorded in the lock file, not an
                    # assumption — it can be "cli" (Client Prudential Limit's MCX
                    # Web Allocation button genuinely running right now) OR it
                    # can be "sa" itself: a STALE lock left behind by an earlier
                    # run of THIS SAME button that got force-killed via Stop
                    # (taskkill never lets its cleanup code run). Blindly saying
                    # "Client Prudential Limit is using it" would be wrong in
                    # that second case, which is exactly what was reported.
                    _owner = _busy_m.group(1)
                    if _owner == "cli":
                        _busy_msg = ("⏳ Client Prudential Limit's MCX Web Allocation download is "
                                     "using this same TCS BANCS session right now — wait for it to "
                                     "finish, then try MCX Deposit again.")
                    else:
                        _busy_msg = ("⏳ This TCS BANCS session shows as still in use, most likely a "
                                     "leftover from a previous MCX Deposit attempt that was stopped "
                                     "or crashed — try again in a moment; use Stop first if it's "
                                     "still showing as running.")
        elif kind == "rc":
            rc = val
            break

    # Clear proc reference
    try:
        import api as _api_ref2
        _api_ref2._sa_mcx_dep_proc = None
    except Exception:
        pass

    if rc == 0:
        _log("✅ MCX Deposit file downloaded successfully")
        _close_log()
        yield {"type": "done", "ok": True, "msg": "✅ MCX Deposit downloaded successfully"}
    elif _auth_fail:
        _log(f"❌ MCX Deposit download finished with exit code {rc}")
        _close_log()
        yield {"type": "done", "ok": False,
               "msg": "🔑 MCX Deposit login failed — the password appears to be wrong. "
                      "Update it in Credentials and try again."}
    elif rc == 2 or _busy_msg:
        _log("⏳ MCX Deposit download did not start — TCS BANCS session busy (used by another box)")
        _close_log()
        yield {"type": "done", "ok": False,
               "msg": _busy_msg or ("⏳ This TCS BANCS session is in use by another box right "
                                     "now — wait for it to finish, then try again.")}
    else:
        # No known tag matched — a new kind of failure. Try the free Groq AI
        # fallback first; if it's unavailable/fails, use the old fixed message
        # instead of ever showing a raw exit code.
        _log(f"❌ MCX Deposit download finished with exit code {rc}")
        _close_log()
        _ai_msg = translate_error("MCX Deposit download", _log_buffer)
        _msg = _ai_msg or f"❌ MCX Deposit download did not complete (exit code {rc}) — check 🗂️ Logs or contact tech support."
        yield {"type": "done", "ok": False, "msg": _msg}
    yield {"type": "end"}
