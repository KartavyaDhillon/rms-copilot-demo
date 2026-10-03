"""
core/short_allocation.py — SHORT ALLOCATION orchestrator for the dashboard.

Reproduces  SHORT & LAS.bat  EXACTLY — no change to any script's logic:
    Code-1 (downloader)  ->  read run_state  ->  effective RUN = RUN - 1
    RUN 1/2/3  ->  Code-2 -> Code-3      |      RUN 4  ->  Code-2 -> Code-4
Code-2 ONLY runs if Code-1 succeeded (same gate as the batch).

Each script runs as its own subprocess (UTF-8 forced so emoji prints don't crash).
Full output -> per-process log file. Only email / WhatsApp status reaches the UI.
A run can be terminated at any time (kills the whole process tree).
"""
import os, re, queue, threading, subprocess, datetime, shutil, base64, json, glob
try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

# ── NSE AES-256-ECB encrypt/decrypt ─────────────────────────────────────────
# Key matches extranetlib/config.py — NSE API expects AES-256-ECB + PKCS7.
_NSE_AES_KEY = "LsC+f/rZmQALbOAnHJumdiQpX1mvyIeZRcafi1jpHI8="

def _nse_encrypt(plain: str) -> str:
    """Encrypt plain-text NSE password → base64 AES-256-ECB token."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding as _pad
        from cryptography.hazmat.backends import default_backend
        key = base64.standard_b64decode(_NSE_AES_KEY)
        cipher = Cipher(algorithms.AES(key), modes.ECB(), default_backend())
        enc = cipher.encryptor()
        padder = _pad.PKCS7(128).padder()
        padded = padder.update(plain.encode("utf-8")) + padder.finalize()
        encrypted = enc.update(padded) + enc.finalize()
        return base64.standard_b64encode(encrypted).decode("utf-8")
    except Exception as e:
        raise ValueError(f"NSE encrypt failed: {e}")

def _nse_decrypt(enc_b64: str) -> str:
    """Decrypt base64 AES-256-ECB token → plain-text NSE password."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding as _pad
        from cryptography.hazmat.backends import default_backend
        key = base64.standard_b64decode(_NSE_AES_KEY)
        cipher = Cipher(algorithms.AES(key), modes.ECB(), default_backend())
        dec = cipher.decryptor()
        raw = dec.update(base64.standard_b64decode(enc_b64)) + dec.finalize()
        unpadder = _pad.PKCS7(128).unpadder()
        return (unpadder.update(raw) + unpadder.finalize()).decode("utf-8")
    except Exception:
        return ""  # can't decrypt → treat as blank

REPO     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(REPO, ".env")
LOG_DIR  = os.path.join(REPO, "logs", "short_allocation")

CODE1 = "newshortallocationfiledownloader.py"
CODE2 = "shortallocation_final_new_with_co.py"
CODE3 = "LASUPTO3.py"
CODE4 = "LAS4.py"

# Human-readable stage names — the UI should never show a raw .py filename.
STAGE_LABEL = {CODE1: "Download", CODE2: "SA Processing", CODE3: "LAS Funding", CODE4: "LAS Funding"}

# live run state (for the Stop button)
_SA = {"proc": None, "cancel": False, "running": False, "auth_seen": set()}


def _load():
    # Was: load_dotenv(ENV_FILE, override=True) — a bare re-read of the SHARED
    # .env, which since .env.local arrived means "throw away this machine's
    # folder layout and replace it with the machine .env was written on".
    # cfg() calls this, and /sa/status calls cfg() every few seconds, so within
    # seconds of the dashboard opening every path in the server process silently
    # reverted to the old machine's. env_boot.reload() re-reads both files in
    # the right order instead. See the long note there.
    try:
        import core.env_boot as _eb
        _eb.reload()
    except Exception:
        if load_dotenv:
            try: load_dotenv(ENV_FILE, override=True)
            except Exception: pass


def cfg():
    _load(); g = os.getenv
    return {
        "python":  g("SA_PYTHON", "python"),
        "codes":   g("SA_CODES_DIR", r"D:\CLAUDE\rms-copilot\sa_codes"),
        "state":   g("SA_STATE_FILE", r"C:\KARTAVYA\SHORT ALLOCATION\run_state.txt"),
        "output":  g("SA_OUTPUT_DIR", r"C:\KARTAVYA\SHORT ALLOCATION"),
        "las":     g("SA_LAS_FUNDING_DIR", r"C:\KARTAVYA\LAS FUNDING"),
        "backup_dest": g("SA_BACKUP_DEST", ""),
        "windows": {n: g(f"SA_RUN{n}_WINDOW", "") for n in (1, 2, 3, 4)},
    }


# ── per-tag / per-day run status (independent of run_state.txt's download counter) ──
# run_state.txt is owned by CODE1 alone and only ever tracks "how many downloads have
# happened today" — it advances even if CODE2/3/4 fail afterwards. That decoupling is
# exactly why the dashboard could show "Done" on a run whose processing step actually
# failed. This store tracks the REAL outcome of every step so the UI can tell the two
# apart, and so a retry knows which steps are already safely done today.
STATUS_FILE = os.path.join(LOG_DIR, "_run_status.json")


def _today():
    return datetime.date.today().isoformat()


def _load_all_status():
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_all_status(d):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


def _record(tag, **fields):
    """Merge fields into today's record for `tag`. A new calendar day wipes it."""
    all_status = _load_all_status()
    today = _today()
    rec = all_status.get(tag) or {}
    if rec.get("date") != today:
        rec = {"date": today}
    rec.update(fields)
    rec["updated"] = datetime.datetime.now().strftime("%H:%M:%S")
    all_status[tag] = rec
    _save_all_status(all_status)
    return rec


def get_run_status(tag):
    rec = _load_all_status().get(tag) or {}
    return rec if rec.get("date") == _today() else {"date": _today()}


def get_all_run_status():
    today = _today()
    out = {}
    for tag, rec in _load_all_status().items():
        out[tag] = rec if rec.get("date") == today else {"date": today}
    return out


def _archive_to_db(tag, kind, filepath, max_retries=3, retry_delay=2):
    """Copy a file's bytes into PostgreSQL with retry.  Never raises — a DB
    hiccup must not break the run.  Retries up to max_retries times with
    retry_delay seconds between attempts before giving up.
    Returns True on success, False on failure."""
    if not filepath or not os.path.exists(filepath):
        return False
    use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
    if not use_pg:
        print(f"[short_allocation] ⚠️  USE_POSTGRES=false — {os.path.basename(filepath)} NOT archived. "
              f"Set USE_POSTGRES=true in .env to enable cross-machine file storage.")
        return False
    import time as _time
    fname = os.path.basename(filepath)
    try:
        from core.db_postgres import save_run_file
        with open(filepath, "rb") as f:
            content = f.read()
    except Exception as e:
        print(f"[short_allocation] WARNING could not read {filepath} for archiving: {e}")
        return False
    for attempt in range(1, max_retries + 1):
        try:
            save_run_file("short_allocation", kind, fname, content, tag=tag)
            print(f"[short_allocation] archived {fname} ({len(content):,} bytes) → PostgreSQL")
            return True  # success
        except Exception as e:
            if attempt < max_retries:
                print(f"[short_allocation] archive attempt {attempt}/{max_retries} failed for {fname}: {e} — retrying in {retry_delay}s")
                _time.sleep(retry_delay)
            else:
                print(f"[short_allocation] WARNING: {fname} could NOT be archived after {max_retries} attempts: {e}")
    return False


def _archive_input_files(tag, eff, c, put, is_last_run=False):
    """After download (Code1) succeeds, push raw exchange input files into Postgres.

    Per-run files (peak .lis / MCX / NCDEX) are archived every run — they carry
    a run-number suffix so each run's version is distinct.

    Daily files (RISK_REPORT, BRM, EXCEPTION, MCX_DEPOSIT) are the SAME file
    for all 4 runs today, so we archive them only on the last run (is_last_run=True)
    to avoid 4 identical copies cluttering the database.
    """
    import glob as _glob
    nse_i = f"i{eff:02d}"
    mcx_i = f"{eff:02d}"
    base = c["output"]
    # ── Per-run files: archived every run (run-number suffix keeps them distinct)
    patterns = [
        (f"C_SA01_P_06436_*_{nse_i}.lis", "input_c_lis"),
        (f"F_SA01_P_06436_*_{nse_i}.lis", "input_f_lis"),
        (f"O_SA01_P_06436_*_{nse_i}.lis", "input_o_lis"),
        (f"MCX_PeakMargin*_{mcx_i}.csv",   "input_mcx_peak"),
        (f"NCCL_MARGIN_REP*_{mcx_i}.csv",  "input_nccl_peak"),
    ]
    # ── Daily files: same content all day — archive once, on the last run only
    if is_last_run:
        patterns += [
            ("MCX_DEPOSIT_*.xlsx",   "input_mcx_deposit"),
            ("MCX_DEPOSIT_*.csv",    "input_mcx_deposit"),
            ("RISK_REPORT_*.csv",    "input_risk_report"),
            ("BRM LIST_File*.xlsx",  "input_brm_list"),
            ("BRM LIST_File*.csv",   "input_brm_list"),
            ("EXCEPTION.xlsx",       "input_exception"),
        ]
    archived = 0
    for pat, kind in patterns:
        matches = _glob.glob(os.path.join(base, pat))
        if matches:
            fp = sorted(matches)[-1]
            _archive_to_db(tag, kind, fp)
            archived += 1
    if archived:
        suffix = " (incl. daily files — last run)" if is_last_run else ""
        put({"type": "log", "msg": f"📦 {archived} input file(s) archived → PostgreSQL{suffix}"})


def _archive_las_outputs(tag, las_dir, since_ts, put):
    """After LAS3 / LAS4 succeeds, archive all output files written during this
    run to PostgreSQL.

    Input files that LAS *reads* (LAS Debit Balances, RISK_REPORT, main.csv,
    scenario.csv, SA0X files) are excluded — they are already archived elsewhere.

    IMPORTANT: Both LASUPTO3.py (runs 1-3) and LAS4.py (run 4) run a
    BACKUP+CLEANUP step at the END before exiting, which copies files to
    {las_dir}/BACKUP FILES AUTOMATED/{DD-MM-YYYY}/{N}/ and then DELETES them
    from the las_dir root.  _archive_las_outputs runs AFTER the script exits so
    the root is almost always empty.  We therefore scan the backup subfolder
    that the LAS script created for this run.

    Strategy:
      1. Collect all non-input candidates from the las_dir root (pre-cleanup).
      2. Also collect from the run's backup subfolders (post-cleanup fallback).
      3. Deduplicate by basename so we never archive the same file twice.
      4. Try to archive only files with mtime ≥ since_ts; fall back to all if
         the mtime check yields nothing.
    """
    if not las_dir or not os.path.isdir(las_dir):
        put({"type": "log", "msg": "⚠️ LAS output dir not found — skipping LAS archive."})
        return
    # Exact filenames that are INPUT files LAS reads — skip them
    _skip_exact = {"main.csv", "scenario.csv", "main_filtered.csv"}
    # Prefix strings for other inputs (case-insensitive startswith on lower filename)
    _skip_prefix = (
        "las debit balances",   # source debit file (e.g. "LAS Debit Balances-01072026.xlsx")
        "risk_report_",         # risk CSV archived as input separately
    )
    _skip_starts = (
        "sa0",                  # SA0X_OUTPUT FILE.xlsx — Code-2 output, not LAS output
    )
    _ok_exts = {".xlsx", ".xls", ".jpg", ".jpeg", ".png", ".csv", ".pdf"}

    def _is_output(fp):
        """Return True if fp is an LAS output file (not an input to skip)."""
        fn = os.path.basename(fp)
        fn_lower = fn.lower()
        ext = os.path.splitext(fn_lower)[1]
        if ext not in _ok_exts:
            return False
        if fn_lower in _skip_exact:
            return False
        if any(fn_lower.startswith(s) for s in _skip_prefix + _skip_starts):
            return False
        return True

    def _collect_flat(scan_dir):
        """Collect output files from a single directory level."""
        found = []
        if not os.path.isdir(scan_dir):
            return found
        for fp in glob.glob(os.path.join(scan_dir, "*")):
            if os.path.isfile(fp) and _is_output(fp):
                found.append(fp)
        return found

    # ── Pass 1: scan las_dir root (populated if cleanup hasn't run yet) ──────
    candidates = _collect_flat(las_dir)
    seen_names = {os.path.basename(f) for f in candidates}

    # ── Pass 2: scan backup subfolder created by LAS script's own cleanup ────
    # LASUPTO3.py  → {las_dir}/BACKUP FILES AUTOMATED/{DD-MM-YYYY}/{1,2,3}/
    # LAS4.py      → {las_dir}/BACKUP FILES AUTOMATED/{DD-MM-YYYY}/4/
    # The run number is encoded in tag: "SA-01" → "1", "SA-04" → "4"
    try:
        _run_n = tag[-1] if tag and tag[-1].isdigit() else None
        if _run_n:
            _bk_today = datetime.date.today().strftime("%d-%m-%Y")
            _bk_base = os.path.join(las_dir, "BACKUP FILES AUTOMATED", _bk_today, _run_n)
            # OUTPUT subfolder: xlsx/csv output files
            for _sub in ("OUTPUT", "PICTURES"):
                for fp in _collect_flat(os.path.join(_bk_base, _sub)):
                    if os.path.basename(fp) not in seen_names:
                        candidates.append(fp)
                        seen_names.add(os.path.basename(fp))
            # Also check root of backup run folder itself
            for fp in _collect_flat(_bk_base):
                if os.path.basename(fp) not in seen_names:
                    candidates.append(fp)
                    seen_names.add(os.path.basename(fp))
            if os.path.isdir(_bk_base):
                put({"type": "log", "msg": f"📦 LAS backup folder scanned: {_bk_base}"})
            else:
                put({"type": "log", "msg": f"📦 LAS backup folder not found yet: {_bk_base} — scanning root only."})
    except Exception as _be:
        put({"type": "log", "msg": f"⚠️ LAS backup folder scan error: {_be}"})

    skipped_msg = (f"({len(seen_names) - len(candidates)} inputs skipped)" if len(seen_names) > len(candidates)
                   else "")
    put({"type": "log", "msg": f"📦 LAS archive scan: {len(candidates)} output file(s) to consider {skipped_msg}"})

    if not candidates:
        put({"type": "log", "msg": f"📦 No LAS output files found in las_dir or backup. "
                                    f"LAS dir: {las_dir}"})
        return

    # ── Pass 3: prefer files whose mtime is ≥ since_ts ───────────────────────
    fresh = [fp for fp in candidates if _safe_mtime(fp) >= since_ts]
    to_archive = fresh if fresh else candidates
    if not fresh and candidates:
        put({"type": "log", "msg": "📦 LAS mtime check: 0 fresh files — archiving ALL candidates."})

    archived = 0
    failed = 0
    for fp in to_archive:
        ok = _archive_to_db(tag, "las_output", fp)
        if ok:
            archived += 1
        else:
            failed += 1

    if failed:
        put({"type": "log", "msg": f"📦 LAS archive: {archived} saved → PostgreSQL, "
                                    f"⚠️ {failed} FAILED (check server log for details)."})
    else:
        put({"type": "log", "msg": f"📦 {archived} LAS output file(s) archived → PostgreSQL"})


def _safe_mtime(fp):
    """Return mtime float, or 0 on error."""
    try:
        return os.path.getmtime(fp)
    except OSError:
        return 0


def _extract_missing_files(logpath):
    """Pull the '  - NAME -> pattern: ...' lines CODE2 prints when it self-aborts
    on missing input files, so the UI can show the exact reason without the user
    having to open the raw log."""
    if not logpath:
        return []
    try:
        with open(logpath, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return []
    out, capture = [], False
    for line in text.splitlines():
        if "Missing required files" in line:
            capture = True
            continue
        if capture:
            s = line.strip()
            if s.startswith("-"):
                out.append(s.lstrip("- ").strip())
            elif s == "":
                continue
            else:
                break
    return out


# ── status-only markers ──────────────────────────────────────────────────────
_EMAIL_OK  = re.compile(r"email sent successfully|all emails sent successfully|failure email sent successfully", re.I)
_EMAIL_BAD = re.compile(r"email error", re.I)
_WA_OK     = re.compile(r"all whatsapp messages processed|whatsapp.*(sent|success|delivered|submitted)", re.I)
_WA_BAD    = re.compile(r"whatsapp.*(fail|error)", re.I)

def _status_event(line):
    s = line.strip()
    if not s: return None
    if _EMAIL_OK.search(s):  return {"type": "email",    "ok": True,  "msg": s[:200]}
    if _EMAIL_BAD.search(s): return {"type": "email",    "ok": False, "msg": s[:200]}
    if _WA_OK.search(s):     return {"type": "whatsapp", "ok": True,  "msg": s[:200]}
    if _WA_BAD.search(s):    return {"type": "whatsapp", "ok": False, "msg": s[:200]}
    return None


def _auth_server(line):
    s = line.lower()
    # Explicit tags printed by the downloaders themselves — checked first,
    # since these are unambiguous (confirmed via test_*_login.py diagnostics)
    # and must not be shadowed by the looser generic checks below.
    if "[ncdex_auth_fail]" in s:
        return "NCDEX"
    if "[mcx_deposit_auth_fail]" in s:
        return "MCX Deposit"
    if "login error" in s and any(x in s for x in ("401", "unauthor", "invalid", "forbidden", "403")):
        return "NSE"
    # Was: `if "login failed" in s`. That blunt substring match false-triggered on
    # newshortallocationfiledownloader.py's HTML-vs-CSV diagnostic print —
    # "MCX returned HTML instead of CSV — session timed out or login failed." —
    # which merely lists login failure as ONE of two possible causes of a
    # download-time session timeout; it doesn't confirm the password was wrong.
    # That line fired this popup even on runs where the very same log shows
    # "MCX login successful" moments earlier. The real failure message (all
    # passwords exhausted) is the exact phrase "MCX login failed" — match that
    # instead, so a plain data/session timeout no longer masquerades as a bad
    # password and sends people to re-enter a password that was never wrong.
    if "mcx login failed" in s:
        return "MCX"
    if any(x in s for x in ("invalid credential", "incorrect password", "authentication failed")):
        return "the exchange"
    return None


def read_run_state(state_path):
    try:
        for line in open(state_path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line.upper().startswith("RUN="):
                return int(line.split("=", 1)[1])
    except Exception:
        return None
    return None


def stop_short_allocation():
    """Kill the running script and its child tree (selenium/chrome/popups)."""
    _SA["cancel"] = True
    p = _SA.get("proc")
    if p and p.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True)
            else:
                p.terminate()
                try: p.wait(timeout=3)
                except Exception: p.kill()
        except Exception:
            pass
    return {"ok": True, "stopped": True}


def _run_one(c, script, tag, put):
    if _SA["cancel"]:
        return 1, None
    os.makedirs(LOG_DIR, exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(script)[0]
    logp = os.path.join(LOG_DIR, f"{tag}__{base}__{ts}.log")
    put({"type": "step", "script": script, "state": "running"})
    rc = 1
    _auth_flagged = False          # did we already show a specific "login failed" popup this step?
    _tail_buf = []                 # last ~20 lines — fed to the AI fallback if this step fails unexpectedly
    try:
        with open(logp, "w", encoding="utf-8", errors="replace") as lf:
            lf.write(f"# {script}  tag={tag}  start={ts}\n")
            # PYTHONUNBUFFERED + "-u": without these, the CHILD process's own stdout
            # is block-buffered (not line-buffered) because it's writing to a pipe,
            # not a real terminal. print() calls sit in the child's internal buffer
            # (often 4-8 KB) instead of reaching us — so a run doing real work (a
            # slow login, a captcha retry loop) can look completely hung with a
            # near-empty log for minutes, even though the parent below flushes every
            # line it DOES receive immediately. core/pipeline_box.py (CTCL/CLI) has
            # always set this; short_allocation.py (SA) was missing it, which is why
            # SA runs were the ones going silent for minutes with a near-0-byte log.
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                   "PYTHONUNBUFFERED": "1"}
            # Pass date override to subprocess if set (for past-date runs)
            override = _SA.get("override_date")
            if override:
                env["SA_OVERRIDE_DATE"] = override
            # CREATE_NEW_PROCESS_GROUP (Windows only): without this, this subprocess
            # shares the FastAPI server's console/process group. `uvicorn --reload`
            # restarting on a code edit (or the terminal window closing) sends a
            # Ctrl-Break-style interrupt to that whole group — which was hitting
            # live downloader runs mid-flight (e.g. a KeyboardInterrupt during the
            # 5-minute missing-file retry wait) even though nothing about the run
            # itself failed. Isolating the process group means an accidental server
            # restart no longer kills an in-progress exchange login/download. Stop
            # still works exactly as before — it kills by PID via `taskkill /F /T`,
            # which doesn't care what process group the target is in.
            _popen_kwargs = {}
            if os.name == "nt":
                _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(
                [c["python"], "-u", os.path.join(c["codes"], script)],
                cwd=c["codes"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,   # prevent any input() calls from blocking
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                **_popen_kwargs)
            _SA["proc"] = proc
            for line in proc.stdout:
                lf.write(line)
                lf.flush()  # write to disk immediately — if Stop kills this mid-run, whatever
                            # was already printed is preserved instead of sitting lost in a
                            # buffer that never gets flushed (log used to show 0 KB after Stop)
                _tail_buf.append(line.rstrip("\n"))
                if len(_tail_buf) > 20:
                    _tail_buf.pop(0)
                ev = _status_event(line)
                if ev:
                    ev["script"] = script
                    put(ev)
                _srv = _auth_server(line)
                if _srv and _srv not in _SA["auth_seen"]:
                    _SA["auth_seen"].add(_srv)
                    _auth_flagged = True
                    put({"type": "login_failed", "server": _srv})
            rc = proc.wait()
            _SA["proc"] = None
    except Exception as e:
        put({"type": "step", "script": script, "state": "error", "detail": str(e)})
        return 1, logp
    # AI (Groq) plain-English fallback — same as every other pipeline now has. Only fires for
    # a failure we don't already have a specific, better explanation for (login failure already
    # shows its own popup; a user-triggered Stop isn't an error to explain).
    if rc != 0 and not _auth_flagged and not _SA["cancel"]:
        try:
            from core.error_translator import translate_error
            _ai_msg = translate_error(f"Short Allocation — {script}", _tail_buf)
            if _ai_msg:
                put({"type": "note", "msg": _ai_msg, "script": script})
        except Exception:
            pass
    put({"type": "step", "script": script,
         "state": "stopped" if _SA["cancel"] else ("done" if rc == 0 else "error"),
         "log": os.path.basename(logp)})
    return rc, logp


def stream_short_allocation(run_no, override_date=None, force=False, skip_las=False, las_only=False, download_only=False):
    """Generator of SSE events. Mirrors SHORT & LAS.bat. run_no = '01'..'04'.
    override_date: optional YYYY-MM-DD string to run for a past date.
    force:       if True, re-run all steps even if code1/code2 were done today.
    skip_las:    if True, run only Download + SA Processing; stop BEFORE LAS Funding.
                 Also forces code2 to re-run regardless of its current status.
    las_only:    if True, skip Download AND SA Processing entirely — jump straight
                 to LAS Funding. Use when SA Processing already succeeded but LAS failed.
    download_only: if True, run only the Download step (Code1) then stop — skip SA Processing and LAS."""
    c   = cfg()
    q   = queue.Queue()
    put = q.put
    tag = f"SA-{run_no}"

    # NOTE: this used to also claim ctcl/ms15/cli's shared _PB lock, on the
    # theory that any two browser-automation pipelines running at once could
    # collide. That was too broad — Short Allocation's main SA-01..04 cycle
    # never calls sa_codes/mcx_deposit_downloader.py at all (confirmed: it's
    # not imported anywhere in this file), so it doesn't actually conflict
    # with CTCL/MS15/CLI's main runs, or with CLI's own "MCX Web Allocation"
    # button — locking against them just blocked things that had nothing to
    # do with each other (e.g. this box's own already-done View buttons showed
    # as locked while CLI's MCX download ran). The ONE real shared resource —
    # download_mcx_deposit()'s TCS BANCS session/.token_state file, used by
    # both SA's own "MCX Deposit" button and CLI's "MCX Web Allocation" button
    # — now has its own narrow lock inside sa_codes/mcx_deposit_downloader.py
    # (see _acquire_mcx_lock/_release_mcx_lock there), scoped to exactly those
    # two callers and nothing else.
    _SA["cancel"] = False
    _SA["running"] = True
    _SA["auth_seen"] = set()
    _SA["override_date"] = override_date or None

    def stopped():
        return _SA["cancel"]

    def worker():
        try:
            # ── DB-FIRST: if output/LAS dirs don't exist and Postgres is on,
            # use temp dirs so no folders are auto-created on a foreign machine.
            # The SA scripts read SA_OUTPUT_DIR / SA_LAS_FUNDING_DIR from env —
            # we override them in the child process environment via _run_one's env dict.
            _use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
            _tmp_dirs = []  # temp dirs to clean up after the run

            def _effective_dir(env_key, current_path):
                """Return usable dir — local if it exists, else temp (DB-first mode)."""
                if os.path.isdir(current_path):
                    return current_path, False
                if _use_pg:
                    import tempfile as _tf
                    tmp = _tf.mkdtemp(prefix=f"sa_{env_key.lower()}_")
                    os.environ[env_key] = tmp   # picked up by _run_one child env
                    _tmp_dirs.append(tmp)
                    put({"type": "log", "msg": f"📂 {env_key} not found locally — using temp dir (DB-first mode). Files will be stored in Postgres."})
                    return tmp, True
                # Fallback: create the folder as before (original behaviour)
                os.makedirs(current_path, exist_ok=True)
                return current_path, False

            _out_dir, _out_is_tmp = _effective_dir("SA_OUTPUT_DIR",       c["output"])
            _las_dir, _las_is_tmp = _effective_dir("SA_LAS_FUNDING_DIR",  c["las"])

            # ── SA-only mode: force code2 to re-run, skip LAS entirely ──────────
            if skip_las:
                _record(tag, code2=None, overall=None, code3=None, code4=None,
                        failed_step=None, failed_step_label=None,
                        log_code3=None, log_code4=None)
                today_status = get_run_status(tag)
                put({"type": "log", "msg": f"SA-only mode — SA Processing will re-run; LAS Funding will be skipped for {tag}."})

            # ── LAS-only mode: skip Download + SA Processing, run only LAS ───────
            if las_only:
                put({"type": "log", "msg": f"LAS-only mode — skipping Download and SA Processing; running LAS Funding only for {tag}."})
                _eff_las = int(run_no)   # SA-01→1, SA-02→2, etc.
                if 1 <= _eff_las <= 3:
                    _next_las, _step_las = CODE3, "code3"
                elif _eff_las == 4:
                    _next_las, _step_las = CODE4, "code4"
                else:
                    put({"type": "error", "msg": f"Invalid run number for LAS-only: {run_no}"}); return
                # Ensure DATE=today in run_state.txt so LASUPTO3.py passes its date check.
                try:
                    _today_iso = datetime.date.today().isoformat()
                    _state_lines = open(c["state"], encoding="utf-8").readlines()
                    _has_date = any(l.startswith("DATE=") for l in _state_lines)
                    with open(c["state"], "w", encoding="utf-8") as _sf:
                        for _ln in _state_lines:
                            if _ln.startswith("DATE="):
                                _sf.write(f"DATE={_today_iso}\n")
                            else:
                                _sf.write(_ln)
                        if not _has_date:
                            _sf.write(f"DATE={_today_iso}\n")
                    put({"type": "log", "msg": f"run_state.txt: DATE → {_today_iso} (LAS date-check will pass)."})
                except Exception as _de:
                    put({"type": "log", "msg": f"⚠️ Could not update DATE in run_state.txt: {_de} — LAS may abort."})
                # Clear previous LAS error so re-run is treated as fresh.
                _record(tag, **{_step_las: None, "overall": None, "failed_step": None,
                                "failed_step_label": None, f"log_{_step_las}": None})
                os.environ["SA_SKIP_PREFLIGHT"] = "1"
                _las_start_ts = datetime.datetime.now().timestamp()
                rc_las, logp_las = _run_one(c, _next_las, tag, put)
                os.environ.pop("SA_SKIP_PREFLIGHT", None)
                _archive_to_db(tag, "log", logp_las)
                if stopped(): put({"type": "stopped", "msg": "⛔ Stopped by user."}); return
                if rc_las == 0:
                    _record(tag, **{_step_las: "done", "overall": "done",
                                    f"log_{_step_las}": os.path.basename(logp_las) if logp_las else None})
                    try:
                        _archive_las_outputs(tag, _las_dir or c.get("las", ""), _las_start_ts, put)
                    except Exception as _lae:
                        put({"type": "log", "msg": f"⚠️ LAS output archiving failed: {_lae}"})
                else:
                    _record(tag, **{_step_las: "error", "overall": "error",
                                    "failed_step": _next_las, "failed_step_label": STAGE_LABEL[_next_las],
                                    f"log_{_step_las}": os.path.basename(logp_las) if logp_las else None})
                put({"type": "done", "ok": rc_las == 0})
                return

            put({"type": "log", "msg": f"Starting SHORT ALLOCATION ({tag})"})
            if not os.path.exists(c["state"]):
                put({"type": "error", "msg": "Download has never run successfully — run Download once manually first."})
                _record(tag, overall="error", failed_step="run_state.txt", failed_step_label="Download (state file missing)"); return

            # STEP 1 — downloader (Code-2 only runs if this succeeds).
            # RESUME: if Code-1 already completed successfully today for this tag,
            # skip re-downloading/re-logging-in to the exchanges and go straight to
            # processing — this is what makes "Run" on a failed tile a real retry
            # instead of forcing a full restart from scratch.
            # force=True bypasses resume logic so everything runs from scratch.
            today_status = get_run_status(tag)
            if force:
                # Reset code1 so the tile shows Download as "starting" (not stale "done").
                # code2 is intentionally NOT reset — keeping it "done" keeps SA-02 unlocked
                # throughout the re-run. It gets re-written to "done" when Code2 finishes.
                _record(tag, overall=None, code1=None, code3=None, code4=None,
                        failed_step=None, failed_step_label=None,
                        log_code3=None, log_code4=None)
                today_status = get_run_status(tag)
                put({"type": "log", "msg": f"Force re-run: cleared status for {tag} — starting fresh Download → SA Processing → LAS."})

                # Fix run_state.txt: the downloader reads RUN=N and downloads files with
                # suffix iN (i.e. RUN=1 → i01, RUN=2 → i02, etc.).
                # We must set RUN to EXACTLY int(run_no) so Code1 downloads the correct
                # files for THIS specific run.
                # Example: SA-01 and SA-02 both done → RUN=3.
                #   Force-rerun SA-01: need RUN=1   (not 3-1=2 which gives SA-02 files!)
                #   Force-rerun SA-02: need RUN=2   (not 3-1=2 which also happens to work)
                #   Force-rerun SA-01 when SA-03 done: need RUN=1 (not 4-1=3!)
                # The only correct approach: set RUN = int(run_no) directly.
                try:
                    target_run = int(run_no)          # "01" → 1, "02" → 2, etc.
                    raw_now = read_run_state(c["state"])
                    if raw_now != target_run:
                        lines = open(c["state"], encoding="utf-8").readlines()
                        with open(c["state"], "w", encoding="utf-8") as _sf:
                            for _line in lines:
                                if _line.startswith("RUN="):
                                    _sf.write(f"RUN={target_run}\n")
                                else:
                                    _sf.write(_line)
                        put({"type": "log", "msg": f"run_state.txt: RUN {raw_now} → {target_run} (set to SA-0{run_no[-1]} — Code1 will download i{target_run:02d} files)."})
                    else:
                        put({"type": "log", "msg": f"run_state.txt: RUN={raw_now} already correct for SA-0{run_no[-1]}, no change needed."})
                except Exception as _rse:
                    put({"type": "log", "msg": f"⚠️ Could not adjust run_state.txt: {_rse} — downloader may pick wrong run suffix."})
            # download_only: reset code1 so it re-runs fresh even if already done today
            # NOTE: do NOT reset overall= here — preserve existing LAS "done" status from
            # the previous full run. Only code1 needs clearing so it re-runs.
            if download_only:
                _record(tag, code1=None, failed_step=None, failed_step_label=None, log1=None)
                today_status = get_run_status(tag)
                put({"type": "log", "msg": f"Download-only mode — running Download step only for {tag}."})
                # CRITICAL: set RUN=int(run_no) so Code1 downloads the correct suffix.
                # Without this, if SA-02 already ran (RUN=2 in file), a download-only
                # retry of SA-01 would download "02" files instead of "01".
                try:
                    target_run = int(run_no)   # "01" → 1
                    raw_now = read_run_state(c["state"])
                    if raw_now != target_run:
                        lines = open(c["state"], encoding="utf-8").readlines()
                        with open(c["state"], "w", encoding="utf-8") as _sf:
                            for _line in lines:
                                if _line.startswith("RUN="):
                                    _sf.write(f"RUN={target_run}\n")
                                else:
                                    _sf.write(_line)
                        put({"type": "log", "msg": f"run_state.txt: RUN {raw_now} → {target_run} (download-only retry for {tag})."})
                    else:
                        put({"type": "log", "msg": f"run_state.txt: RUN={raw_now} already correct for {tag}."})
                except Exception as _rse:
                    put({"type": "log", "msg": f"⚠️ Could not set run_state.txt for download-only: {_rse}"})

            if not force and not download_only and today_status.get("code1") == "done":
                put({"type": "log", "msg": f"✓ Download already completed today for {tag} — starting SA Processing."})
            else:
                rc, logp1 = _run_one(c, CODE1, tag, put)
                _archive_to_db(tag, "log", logp1)   # archive even on failure — useful for remote diagnosis
                if stopped(): put({"type": "stopped", "msg": "⛔ Stopped by user."}); return
                if rc != 0:
                    _record(tag, code1="error", overall="error", failed_step=CODE1,
                            failed_step_label=STAGE_LABEL[CODE1],
                            log1=os.path.basename(logp1) if logp1 else None)
                    put({"type": "error", "msg": "Download failed — check its log."}); return
                _record(tag, code1="done", log1=os.path.basename(logp1) if logp1 else None)

            # download_only: stop here — do not run SA Processing or LAS
            if download_only:
                put({"type": "done", "ok": True, "msg": "✅ Download completed successfully."})
                return

            # STEP 2 — effective RUN = file RUN - 1 (batch logic).
            # CRITICAL: on a non-force retry of SA-0X, run_state.txt may have been
            # incremented by later runs (SA-02 pushed it to 3, SA-03 to 4…).
            # If we don't correct it here, Code-2 reads RUN=3 → eff=2 and looks
            # for i02 files instead of i01 → "missing files" even though they exist.
            # Fix: ALWAYS set RUN = int(run_no)+1 before Code-2 reads it.
            _needed_run = int(run_no) + 1   # e.g. SA-01 → RUN must be 2 so eff=1
            raw = read_run_state(c["state"])
            if raw != _needed_run:
                try:
                    _lines = open(c["state"], encoding="utf-8").readlines()
                    with open(c["state"], "w", encoding="utf-8") as _sf:
                        for _ln in _lines:
                            if _ln.startswith("RUN="):
                                _sf.write(f"RUN={_needed_run}\n")
                            else:
                                _sf.write(_ln)
                    put({"type": "log", "msg": f"run_state.txt: RUN {raw} → {_needed_run} (corrected so Code-2 processes SA-{run_no[-1]:0>2} files)."})
                    raw = _needed_run
                except Exception as _rce:
                    put({"type": "log", "msg": f"⚠️ Could not correct run_state.txt: {_rce}"})
            # Archive input files BEFORE Code2 moves them to backup folder.
            # We know eff at this point (raw - 1), so we can match the exact suffix.
            # Daily files (RISK_REPORT, BRM, EXCEPTION) are only archived on run 4.
            try:
                _eff_pre = (raw - 1) if raw else None
                if _eff_pre and _eff_pre >= 1:
                    _archive_input_files(tag, _eff_pre, c, put, is_last_run=(_eff_pre == 4))
            except Exception as _aie:
                put({"type": "log", "msg": f"⚠️ Input file archiving failed: {_aie}"})
            if raw is None:
                _record(tag, overall="error", failed_step="run_state.txt", failed_step_label="Download (could not read run counter)")
                put({"type": "error", "msg": "Could not read the run counter after Download — check the Download log."}); return
            eff = raw - 1
            put({"type": "log", "msg": f"Effective RUN = {eff}"})

            # STEP 3 — decision (identical to batch)
            if 1 <= eff <= 3:
                next_script, step3_name = CODE3, "code3"
            elif eff == 4:
                next_script, step3_name = CODE4, "code4"
            else:
                put({"type": "log", "msg": "All 4 runs already completed for today."})
                _record(tag, overall="done")
                put({"type": "done", "ok": True}); return

            if not force and today_status.get("code2") == "done":
                put({"type": "log", "msg": f"✓ SA Processing already completed today for {tag} — starting LAS Funding."})
                rc = 0
            else:
                rc, logp2 = _run_one(c, CODE2, tag, put)
                _archive_to_db(tag, "log", logp2)
                if stopped(): put({"type": "stopped", "msg": "⛔ Stopped by user."}); return
                if rc != 0:
                    missing = _extract_missing_files(logp2)
                    _record(tag, code2="error", overall="error", failed_step=CODE2,
                            failed_step_label=STAGE_LABEL[CODE2],
                            log2=os.path.basename(logp2) if logp2 else None, missing_files=missing)
                    put({"type": "error",
                         "msg": "SA Processing failed — missing input files or a script error. Check its log.",
                         "missing_files": missing})
                    return
                _record(tag, code2="done", log2=os.path.basename(logp2) if logp2 else None,
                        missing_files=None)  # clear any stale missing-files from a prior failed attempt

                # LASUPTO3.py checks: state.get("DATE") == today's real date.
                # If Code-2 ran for a past-date override, it writes DATE=<past-date> into
                # run_state.txt, causing LAS to abort with "Code-2 not executed today."
                # Fix: always stamp DATE= with today so LAS can proceed after any Code-2 run.
                try:
                    _today_iso = datetime.date.today().isoformat()
                    _state_lines = open(c["state"], encoding="utf-8").readlines()
                    _has_date = any(l.startswith("DATE=") for l in _state_lines)
                    with open(c["state"], "w", encoding="utf-8") as _sf:
                        for _ln in _state_lines:
                            if _ln.startswith("DATE="):
                                _sf.write(f"DATE={_today_iso}\n")
                            else:
                                _sf.write(_ln)
                        if not _has_date:
                            _sf.write(f"DATE={_today_iso}\n")
                    put({"type": "log", "msg": f"run_state.txt: DATE → {_today_iso} (LAS date-check will pass)."})
                except Exception as _de:
                    put({"type": "log", "msg": f"⚠️ Could not update DATE in run_state.txt: {_de} — LAS may abort."})

                # Archive SA output + zone files → PostgreSQL.
                # Code2's backup_and_cleanup() MOVES these files from SA_OUTPUT_DIR into
                # BACKUP FILES AUTOMATED\DD-MM-YYYY\NN\ before Code2 exits, so we must
                # look in the backup folder, not the original output folder.
                _bk_today = datetime.date.today().strftime("%d-%m-%Y")
                # Use _out_dir (may be a temp dir in DB-first mode) rather than c["output"]
                _bk_base  = os.path.join(_out_dir, "BACKUP FILES AUTOMATED", _bk_today, f"{eff:02d}")
                _archive_to_db(tag, "output", os.path.join(_bk_base, f"SA{eff:02d}_OUTPUT FILE.xlsx"))
                for _zf in ["LUDH_zone.xlsx", "DELH_zone.xlsx", "MUMB_zone.xlsx", "CHND_zone.xlsx"]:
                    _archive_to_db(tag, "output_zone", os.path.join(_bk_base, _zf))

            # ── SA-only early exit — stop here, don't touch LAS ─────────────
            if skip_las:
                _record(tag, overall="done")
                put({"type": "log", "msg": "✓ SA Processing + email completed. LAS Funding intentionally skipped (SA-only run)."})
                put({"type": "done", "ok": True})
                return

            # Tell LAS scripts to skip their pre-flight file-existence checks.
            # Code2 already verified inputs before running, and its backup_and_cleanup()
            # MOVES files from SA_OUTPUT_DIR to backup — so the checks would fail spuriously.
            os.environ["SA_SKIP_PREFLIGHT"] = "1"
            _las_start_ts = datetime.datetime.now().timestamp()  # capture before LAS starts
            rc, logp3 = _run_one(c, next_script, tag, put)
            os.environ.pop("SA_SKIP_PREFLIGHT", None)  # restore after LAS exits
            _archive_to_db(tag, "log", logp3)
            if stopped(): put({"type": "stopped", "msg": "⛔ Stopped by user."}); return
            if rc == 0:
                _record(tag, **{step3_name: "done", "overall": "done",
                                f"log_{step3_name}": os.path.basename(logp3) if logp3 else None})
                # Archive all LAS output files produced during this run → Postgres
                try:
                    _archive_las_outputs(tag, _las_dir or c.get("las", ""), _las_start_ts, put)
                except Exception as _lae:
                    put({"type": "log", "msg": f"⚠️ LAS output archiving failed: {_lae}"})
            else:
                _record(tag, **{step3_name: "error", "overall": "error", "failed_step": next_script,
                                "failed_step_label": STAGE_LABEL[next_script],
                                f"log_{step3_name}": os.path.basename(logp3) if logp3 else None})
            put({"type": "done", "ok": rc == 0})
        except Exception as e:
            put({"type": "error", "msg": str(e)})
            _record(tag, overall="error", failed_step="internal", error_detail=str(e))
        finally:
            # Clean up temp dirs created for DB-first mode
            if _tmp_dirs:
                import shutil as _shu
                for _td in _tmp_dirs:
                    try:
                        _shu.rmtree(_td, ignore_errors=True)
                        put({"type": "log", "msg": f"🗑 Temp dir cleaned up: {_td}"})
                    except Exception:
                        pass
                # Restore env vars to original values so subsequent runs re-evaluate
                if _out_is_tmp:
                    os.environ.pop("SA_OUTPUT_DIR", None)
                if _las_is_tmp:
                    os.environ.pop("SA_LAS_FUNDING_DIR", None)
            _SA["running"] = False
            _SA["proc"] = None
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        ev = q.get()
        if ev is None:
            break
        yield ev


# ── config (read / update .env), with masking ────────────────────────────────
SA_KEYS_WA = ["SA_WA_DELHI", "SA_WA_CHND", "SA_WA_LUDH", "SA_WA_MUMB", "SA_WA_V1", "SA_WA_V2", "SA_WA_6NBB707"]
SA_KEYS_MAIL = [
    # SA Processing emails (sent by shortallocation_final_new_with_co.py)
    "SA2_MAIL_LUDH_TO", "SA2_MAIL_LUDH_CC",
    "SA2_MAIL_DELH_TO", "SA2_MAIL_DELH_CC",
    "SA2_MAIL_MUMB_TO", "SA2_MAIL_MUMB_CC",
    "SA2_MAIL_CHND_TO", "SA2_MAIL_CHND_CC",
    "SA2_MAIL_FULL_TO", "SA2_MAIL_FULL_CC",
    # FILE MISSING EMAIL — sent by sa_codes/newshortallocationfiledownloader.py's
    # send_failure_email()/send_manual_intervention_email() while it waits (in
    # 5-minute cycles) for an expected input file to arrive. Existing dedicated
    # keys — just newly exposed here so they show up in the Email To/CC modal;
    # nothing about how/when the email is sent changes.
    "SA_DL_EMAIL_TO", "SA_DL_EMAIL_CC",
    # LAS Funding emails (sent by LAS3.py / LAS4.py)
    "SA_MAIL_SCENARIO_ALL_TO", "SA_MAIL_SCENARIO_ALL_CC",
    "SA_MAIL_DELHI_TO", "SA_MAIL_DELHI_CC",
    "SA_MAIL_TENTATIVE_TO", "SA_MAIL_TENTATIVE_CC",
    "SA_MAIL_TOP100_TO", "SA_MAIL_TOP100_CC",
    "SA_MAIL_TOP15_TO", "SA_MAIL_TOP15_CC",
]
SA_KEYS_WIN = [f"SA_RUN{n}_WINDOW" for n in (1, 2, 3, 4)]
SA_KEYS_CRED = ["SA_NSE_LOGINID", "SA_NSE_PASSWORD", "SA_NSE_MEMBER",
                "SA_MCX_USER", "SA_MCX_PASSWORDS", "SA_NCDEX_USER", "SA_NCDEX_PASSWORD",
                "SA_MCX_DEPOSIT_USER", "SA_MCX_DEPOSIT_PASSWORD",
                "SA_SMTP_EMAIL", "SA_SMTP_PASSWORD",
                "SA2_SMTP_USER", "SA2_SMTP_PASS"]
# MCX/NCDEX user IDs are fixed exchange member codes — the dashboard should never
# offer to edit them, only the password. NSE's user ID can change.
SA_FIXED_USER = {"MCX", "NCDEX"}


def mask_number(num):
    n = (num or "").strip()
    return n if len(n) <= 4 else "•" * (len(n) - 4) + n[-4:]


def get_config():
    _load(); g = os.getenv
    return {
        # Full numbers, not masked — these are phone numbers, not secrets, and the
        # +/- editor needs the real values to let you remove ONE of several without
        # accidentally dropping the others (you can't reconstruct a number from a mask).
        "whatsapp": {k: [n.strip() for n in g(k, "").split(",") if n.strip()] for k in SA_KEYS_WA},
        # Same list shape as whatsapp — LAS4.py/LASUPTO3.py already split these on
        # comma via _SALIST, so multiple To/CC addresses per zone already work.
        "mail":     {k: [e.strip() for e in g(k, "").split(",") if e.strip()] for k in SA_KEYS_MAIL},
        "windows":  {k: g(k, "") for k in SA_KEYS_WIN},
    }


def get_credentials():
    _load(); g = os.getenv
    _nse_enc = g("SA_NSE_PASSWORD", "")
    _nse_plain = _nse_decrypt(_nse_enc) if _nse_enc else ""
    # Show last 4 chars of plain password as hint (like other fields), or "not set"
    _nse_hint = ("•" * max(0, len(_nse_plain) - 4) + _nse_plain[-4:]) if len(_nse_plain) > 4 else ("•" * len(_nse_plain) if _nse_plain else "not set")
    return {
        "NSE":   {"user": g("SA_NSE_LOGINID", ""), "pass": _nse_hint,
                  "user_key": "SA_NSE_LOGINID", "pass_key": "SA_NSE_PASSWORD", "user_editable": True,
                  "note": "type your plain-text NSE login password — the system encrypts it automatically"},
        # MCX tries each password in turn until one works — exposed as an editable
        # list (not masked) so you can add/remove one without re-typing the rest.
        "MCX":   {"user": g("SA_MCX_USER", ""), "pass_list": [p.strip() for p in g("SA_MCX_PASSWORDS", "").split(",") if p.strip()],
                  "user_key": "SA_MCX_USER", "pass_key": "SA_MCX_PASSWORDS", "user_editable": False,
                  "note": "member code is fixed. The system tries every password below in order until one works."},
        "NCDEX": {"user": g("SA_NCDEX_USER", ""), "pass": mask_number(g("SA_NCDEX_PASSWORD", "")),
                  "user_key": "SA_NCDEX_USER", "pass_key": "SA_NCDEX_PASSWORD", "user_editable": False,
                  "note": "member code is fixed — single password"},
        "MCX Deposit": {"user": g("SA_MCX_DEPOSIT_USER", "56720CMMKR014139"), "pass": mask_number(g("SA_MCX_DEPOSIT_PASSWORD", "")),
                  "user_key": "SA_MCX_DEPOSIT_USER", "pass_key": "SA_MCX_DEPOSIT_PASSWORD", "user_editable": False,
                  "note": "eclear.mcxccl.com — RSK383 Cash Collateral Deposit Limit. Update password here if portal login changes."},
        "Email sender — LAS Funding": {"user": g("SA_SMTP_EMAIL", ""), "pass": mask_number(g("SA_SMTP_PASSWORD", "")),
                  "user_key": "SA_SMTP_EMAIL", "pass_key": "SA_SMTP_PASSWORD", "user_editable": True,
                  "note": "the mailbox the LAS Funding step sends FROM. Update the password here whenever it changes."},
        # shortallocation_final_new_with_co.py had its OWN separate hardcoded SMTP
        # account (kartavya.dhillon@mastertrust.co.in) with no .env wiring at all —
        # this is that one, now editable the same way.
        "Email sender — SA Processing": {"user": g("SA2_SMTP_USER", ""), "pass": mask_number(g("SA2_SMTP_PASS", "")),
                  "user_key": "SA2_SMTP_USER", "pass_key": "SA2_SMTP_PASS", "user_editable": True,
                  "note": "the mailbox the SA Processing step sends FROM. Update the password here whenever it changes."},
    }


def backfill_run_to_db(date_str, run_number):
    """Push an ALREADY-backed-up SA run folder into PostgreSQL — the SA
    equivalent of ctcl_vs_nmass.backfill_run_to_db()/margin_shortage_15min's,
    for when a run genuinely completed OUTSIDE this dashboard and its output
    files were copied by hand into this machine's own backup folder.

    SA's disk layout matches ctcl/ms15 (date/run subfolders) but with two
    twists: the subdir has a SPACE — "BACKUP FILES AUTOMATED" (note: not
    "BACKUPFILES AUTOMATED" like every other pipe) — and the DB `tag` this
    writes under is "SA-NN" (matching every other SA archive call, e.g. the
    one inside stream_short_allocation()), NOT a date/run string — so a
    backfilled row lands in the SAME logical bucket a normal SA run's own
    automatic archiving already uses, just written later by hand.

    `date_str` — folder name exactly as on disk, DD-MM-YYYY (e.g. "21-07-2026").
    `run_number` — the two-digit run folder, e.g. 1 for ".../01".
    Safe to call more than once — re-archiving just adds another row."""
    out_dir = cfg().get("output") or os.getenv("SA_OUTPUT_DIR", r"C:\KARTAVYA\SHORT ALLOCATION")
    run_folder = os.path.join(out_dir, "BACKUP FILES AUTOMATED", date_str, f"{int(run_number):02d}")
    if not os.path.isdir(run_folder):
        return {"ok": False, "error": f"Folder not found: {run_folder}"}
    tag = f"SA-{int(run_number):02d}"
    archived, skipped = [], []
    for fname in sorted(os.listdir(run_folder)):
        fpath = os.path.join(run_folder, fname)
        if not os.path.isfile(fpath):
            continue
        kind = "output_zone" if fname.endswith("_zone.xlsx") else "output"
        if _archive_to_db(tag, kind, fpath):
            archived.append(fname)
        else:
            skipped.append(fname)
    return {"ok": True, "folder": run_folder, "tag": tag, "archived": archived, "skipped": skipped}


def check_password(pw):
    _load()
    return pw == os.getenv("SA_CONFIG_PASSWORD", "")


def update_env(updates: dict):
    # NSE password arrives as plain text from the UI — encrypt before storing.
    if "SA_NSE_PASSWORD" in updates and updates["SA_NSE_PASSWORD"]:
        try:
            updates["SA_NSE_PASSWORD"] = _nse_encrypt(updates["SA_NSE_PASSWORD"])
        except Exception as e:
            raise ValueError(f"Could not encrypt NSE password: {e}")
    allowed = set(SA_KEYS_WA) | set(SA_KEYS_MAIL) | set(SA_KEYS_WIN) | set(SA_KEYS_CRED)
    try:
        with open(ENV_FILE, encoding="utf-8") as f: lines = f.readlines()
    except Exception:
        lines = []
    seen, out = set(), []
    for ln in lines:
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", ln)
        if m and m.group(1) in updates and m.group(1) in allowed:
            k = m.group(1); out.append(f"{k}={updates[k]}\n"); seen.add(k)
        else:
            out.append(ln)
    for k, v in updates.items():
        if k in allowed and k not in seen:
            out.append(f"{k}={v}\n")
    with open(ENV_FILE, "w", encoding="utf-8") as f: f.writelines(out)
    return True


# Rarely-updated reference files where a wrong/stale replacement silently
# changes exception/BRM logic for every account rather than failing loudly —
# so REPLACING an existing copy of one of these requires the same password
# used everywhere else in this app (check_password() below). A FIRST upload
# (nothing there yet) never needs a password — only replacing one that
# already exists does. See core.pipeline_box.PROTECTED_UPLOAD_FILES for the
# equivalent list for CTCL/PROBABLE's own reference files.
SA_PROTECTED_UPLOAD_FILES = {"exception.xlsx", "brm list_file.xlsx", "brm list_file.csv"}

# Authoritative expected filenames per folder (from the scripts themselves).
SA_VALID = {
    "short_allocation": {
        "label": "SHORT ALLOCATION",
        "globs": ["C_SA01_P_06436_*.lis", "C_SA01_P_06436_*.lis.gz",
                  "F_SA01_P_06436_*.lis", "F_SA01_P_06436_*.lis.gz",
                  "O_SA01_P_06436_*.lis", "O_SA01_P_06436_*.lis.gz",
                  "MCX_PeakMargin56720_*.csv", "NCCL_MARGIN_REP_*.csv",
                  "MCX_DEPOSIT_*.xlsx", "MCX_DEPOSIT_*.csv",
                  "RISK_REPORT_*.csv", "BRM LIST_File*.xlsx", "BRM LIST_File*.csv",
                  "EXCEPTION.xlsx"],
        "examples": ["C_SA01_P_06436_<DDMMYYYY>_<NN>.lis", "F_SA01_P_06436_<DDMMYYYY>_<NN>.lis",
                     "O_SA01_P_06436_<DDMMYYYY>_<NN>.lis", "MCX_PeakMargin56720_<date>_<NN>.csv",
                     "NCCL_MARGIN_REP_<YYYYMMDD>_<NN>.csv", "RISK_REPORT_<date>.csv",
                     "BRM LIST_File*.xlsx", "MCX_DEPOSIT_*.csv", "EXCEPTION.xlsx"],
    },
    "las_funding": {
        "label": "LAS FUNDING",
        "globs": ["main.csv", "scenario.csv", "LAS Debit Balances-*.xlsx",
                  "RISK_REPORT_*.csv", "SA0*_OUTPUT FILE.xlsx"],
        "examples": ["main.csv", "scenario.csv", "LAS Debit Balances-<date>.xlsx",
                     "RISK_REPORT_<date>.csv", "SA0<N>_OUTPUT FILE.xlsx"],
    },
    "common_files": {
        "label": "COMMON FILES",
        "globs": ["RISK_REPORT_*.csv"],
        "examples": ["RISK_REPORT_<date>.csv"],
    },
}

# Files CODE2 (shortallocation_final_new_with_co.py) needs that are NOT produced by
# CODE1's downloader — they are manual exports the user must drop in via "Upload files"
# every day. These patterns are copied verbatim from that script's own check (lines
# ~113-167) so this can never drift out of sync with what will actually fail.
SA_REQUIRED_MANUAL = {
    "short_allocation": [
        # RISK_REPORT is intentionally absent here — it lives in COMMON FILES.
        # check_sa_readiness("short_allocation") still validates it as a fallback
        # so the pre-flight banner works, but the upload modal shows it under COMMON FILES.
        {"key": "MCX_DEPOSIT", "patterns": ["MCX_DEPOSIT_*.xlsx", "MCX_DEPOSIT_*.csv"],
         "example": "MCX_DEPOSIT_.xlsx or MCX_DEPOSIT_.csv"},
        {"key": "BRM_LIST", "patterns": ["BRM LIST_File*.xlsx", "BRM LIST_File*.csv"],
         "example": "BRM LIST_File*.xlsx or BRM LIST_File*.csv"},
        {"key": "EXCEPTION", "patterns": ["EXCEPTION.xlsx"], "example": "EXCEPTION.xlsx"},
    ],
    # COMMON FILES folder (SA_COMMON_DIR = C:\COMMON FILES).
    # Code-2 reads RISK_REPORT from here; the upload modal directs the user here.
    "common_files": [
        {"key": "RISK_REPORT", "patterns": ["RISK_REPORT_*.csv"], "example": "RISK_REPORT_<date>.csv"},
    ],
    # LAS FUNDING manual files — RISK_REPORT is NOT listed here because SA Code-2
    # automatically copies it to the LAS folder at the end of each SA run.
    # Listing it would cause a false "missing" warning before the first SA run of the day.
    "las_funding": [
        {"key": "MAIN",      "patterns": ["main.csv"],                   "example": "main.csv"},
        {"key": "SCENARIO",  "patterns": ["scenario.csv"],               "example": "scenario.csv"},
        {"key": "LAS_DEBIT", "patterns": ["LAS Debit Balances-*.xlsx"],  "example": "LAS Debit Balances-<date>.xlsx"},
    ],
}


def check_sa_readiness(target="short_allocation"):
    """Check the folder for the files CODE2 will demand BEFORE a run is started,
    so 'forgot to upload' is caught on the dashboard instead of inside a failed
    subprocess log. Mirrors CODE2's own glob check exactly for short_allocation.

    Supported targets: "short_allocation", "common_files", "las_funding"."""
    c = cfg()
    common_dir = os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    if target == "las_funding":
        folder = c["las"]
    elif target == "common_files":
        folder = common_dir
    else:
        folder = c["output"]
    # For short_allocation we explicitly add the RISK_REPORT check even though it
    # lives in common_files — the pre-flight banner must catch it before a run starts.
    items = SA_REQUIRED_MANUAL.get(target, SA_REQUIRED_MANUAL["short_allocation"])
    if target == "short_allocation":
        risk_item = {"key": "RISK_REPORT", "patterns": ["RISK_REPORT_*.csv"],
                     "example": "RISK_REPORT_<date>.csv"}
        items = list(items) + [risk_item]
    out, all_ok = [], True
    for it in items:
        found = None
        for p in it["patterns"]:
            matches = glob.glob(os.path.join(folder, p))
            if matches:
                found = sorted(matches)[-1]
                break
        # For short_allocation: RISK_REPORT may live in COMMON FILES instead
        if not found and it["key"] == "RISK_REPORT" and target == "short_allocation" and common_dir:
            for p in it["patterns"]:
                matches = glob.glob(os.path.join(common_dir, p))
                if matches:
                    found = sorted(matches)[-1]
                    break
        ok = found is not None
        all_ok = all_ok and ok
        out.append({"key": it["key"], "example": it["example"],
                    "found": os.path.basename(found) if found else None, "ok": ok})
    return {"folder": folder, "ready": all_ok, "items": out}


def housekeep_common_files():
    """Auto-backup stale files from COMMON FILES to a dated backup subfolder,
    and archive to Postgres/DB.  'Stale' = the date embedded in the filename is
    before today (e.g. RISK_REPORT_03072026.csv when today is 2026-07-04).

    Backup destination:
        C:\\COMMON FILES\\BACKUP FILES AUTOMATED\\{DD-MM-YYYY}\\{filename}

    Returns list of dicts: [{file, moved_to, archived, file_date}, ...]."""
    import datetime as _dt, shutil as _sh, re as _re
    common_dir = os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    if not os.path.isdir(common_dir):
        return []
    today = _dt.date.today()
    moved = []
    for fname in os.listdir(common_dir):
        fpath = os.path.join(common_dir, fname)
        if not os.path.isfile(fpath):
            continue
        # Extract 8-digit date from filename; try DDMMYYYY then YYYYMMDD
        m = _re.search(r'(\d{8})', fname)
        file_date = None
        if m:
            ds = m.group(1)
            for fmt in ("%d%m%Y", "%Y%m%d"):
                try:
                    file_date = _dt.datetime.strptime(ds, fmt).date(); break
                except ValueError:
                    pass
        # Skip if no parseable date, or if it's today's file
        if file_date is None or file_date >= today:
            continue
        # Move to backup sub-folder named by file's own date
        bak_dir = os.path.join(common_dir, "BACKUP FILES AUTOMATED",
                               file_date.strftime("%d-%m-%Y"))
        os.makedirs(bak_dir, exist_ok=True)
        dest_path = os.path.join(bak_dir, fname)
        try:
            _sh.move(fpath, dest_path)
        except Exception as e:
            print(f"⚠️  housekeep_common_files: could not move {fname}: {e}")
            continue
        # Archive to Postgres / SQLite
        archived = False
        try:
            use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
            if use_pg:
                from core.db_postgres import save_run_file as _save
            else:
                from core.db import save_run_file as _save
            with open(dest_path, "rb") as _f:
                _save("short_allocation", "common_housekeep", fname, _f.read(),
                      tag="common_files")
            archived = True
        except Exception as e:
            print(f"⚠️  housekeep_common_files: DB archive skipped for {fname}: {e}")
        print(f"♻️  housekeep_common_files: {fname} → {dest_path}  (archived={archived})")
        moved.append({"file": fname, "moved_to": dest_path,
                      "archived": archived, "file_date": str(file_date)})
    return moved


# "LAS Debit Balances-*.xlsx" below is a loose fnmatch glob — its `*` matches
# ANY characters, including a stray extra dot, so a near-miss name like
# "LAS Debit Balances-01-10-2026..xlsx" (double dot before the extension)
# used to upload "successfully" even though LAS4.py's own exact-filename
# lookup (f"LAS Debit Balances-{today_dd_mm_yyyy}.xlsx") would never find it —
# the file sat in the folder all day, silently unused, with no error anywhere.
# This regex enforces the ONLY filename LAS4.py will ever actually look for.
_LAS_DEBIT_EXACT_RE = re.compile(r"^las debit balances-\d{2}-\d{2}-\d{4}\.xlsx$")

def _name_ok(name, target):
    import fnmatch
    spec = SA_VALID.get(target, SA_VALID["short_allocation"])
    n = name.lower()
    if target == "las_funding" and n.startswith("las debit balances-"):
        return bool(_LAS_DEBIT_EXACT_RE.match(n))
    return any(fnmatch.fnmatch(n, g.lower()) for g in spec["globs"])


def _date_ok(name):
    """Check if the date embedded in a filename matches today.

    Returns (True, None)          — date is correct, or file has no recognisable date.
    Returns (False, reason_str)   — date found but doesn't match today.

    Only files whose date format is unambiguous are checked:
      • NSE LIS  [CFO]_SA01_P_*_<DDMMYYYY>_<NN>.lis[.gz]  → %d%m%Y
      • MCX Peak MCX_PeakMargin56720_<YYYYMMDD>_<NN>.csv   → %Y%m%d
      • NCDEX    NCCL_MARGIN_REP_*_<YYYYMMDD>_<NN>.csv     → %Y%m%d

    All other files (RISK_REPORT, BRM LIST, MCX_DEPOSIT, main.csv, etc.)
    pass through without a date check — only name-pattern validation applies.
    """
    import re as _re
    import datetime as _dt

    today = _dt.date.today()
    nl = name.lower()
    today_fmt = today.strftime("%d-%b-%Y")   # e.g. "04-Jul-2026"

    # ── NSE LIS: [CFO]_SA01_P_06436_<DDMMYYYY>_<NN>.lis[.gz] ────────────────
    m = _re.match(r"^[cfo]_sa01_p_\d+_(\d{8})_i?\d+\.lis(\.gz)?$", nl)
    if m:
        try:
            d = _dt.datetime.strptime(m.group(1), "%d%m%Y").date()
            if d != today:
                return False, (f"date in filename is {d.strftime('%d-%b-%Y')} "
                               f"but today is {today_fmt} — wrong date?")
        except ValueError:
            pass   # malformed 8-digit block — let pattern check suffice
        return True, None

    # ── MCX PeakMargin: MCX_PeakMargin56720_<YYYYMMDD>_<NN>.csv ─────────────
    m = _re.match(r"^mcx_peakmargin\d+_(\d{8})_\d+\.csv$", nl)
    if m:
        try:
            d = _dt.datetime.strptime(m.group(1), "%Y%m%d").date()
            if d != today:
                return False, (f"date in filename is {d.strftime('%d-%b-%Y')} "
                               f"but today is {today_fmt} — wrong date?")
        except ValueError:
            pass
        return True, None

    # ── NCDEX: NCCL_MARGIN_REP_<member>_<YYYYMMDD>_<NN>.csv ─────────────────
    m = _re.match(r"^nccl_margin_rep_\d+_(\d{8})_\d+\.csv$", nl)
    if m:
        try:
            d = _dt.datetime.strptime(m.group(1), "%Y%m%d").date()
            if d != today:
                return False, (f"date in filename is {d.strftime('%d-%b-%Y')} "
                               f"but today is {today_fmt} — wrong date?")
        except ValueError:
            pass
        return True, None

    # All other files: no date embedded or format ambiguous — skip date check
    return True, None


def save_uploaded_bytes(items, target="short_allocation", password=""):
    """Like move_uploaded, but writes the bytes directly to the final destination
    in ONE disk write — no temp-file-then-move. Halves the I/O the old path did,
    and times every file so a slow destination (antivirus scan / cloud-sync drive)
    shows up explicitly in the server console instead of as a silent hang.
    items = list of (filename, bytes).

    An existing same-named file is REPLACED by default (not skipped) — per
    explicit instruction, an ordinary re-upload should update the file in
    place. The one exception is SA_PROTECTED_UPLOAD_FILES (EXCEPTION.xlsx,
    BRM LIST_File.*): replacing an EXISTING copy of one of those requires a
    correct `password`; a wrong/missing one reports that filename back in
    `password_required` instead of silently skipping or replacing it."""
    import time as _time
    c = cfg()
    spec = SA_VALID.get(target, SA_VALID["short_allocation"])
    if target == "las_funding":
        dest = c["las"]
    elif target == "common_files":
        dest = os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    else:
        dest = c["output"]
    t0 = _time.time()
    os.makedirs(dest, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    mkdir_s = _time.time() - t0
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logp = os.path.join(LOG_DIR, f"UPLOAD__{target}__{ts}.log")
    ok, skipped, rejected, timings, password_required = [], [], [], [], []
    lines = [f"# UPLOAD  target={target} ({spec['label']})  dest={dest}  time={ts}  mkdir={mkdir_s:.2f}s"]
    for name, data in items:
        base = os.path.basename(name)
        if not _name_ok(base, target):
            rejected.append(base); lines.append(f"REJECT  {base}  (not a recognised {spec['label']} file)")
            continue
        date_ok, date_err = _date_ok(base)
        if not date_ok:
            rejected.append(f"{base}: {date_err}")
            lines.append(f"REJECT  {base}  ({date_err})")
            continue
        tp = os.path.join(dest, base)
        if os.path.exists(tp) and base.lower() in SA_PROTECTED_UPLOAD_FILES and not check_password(password):
            password_required.append(base); lines.append(f"PW-REQ  {base}  (protected file, existing copy)")
            continue
        if os.path.exists(tp):
            lines.append(f"REPLACE {base}  (already in folder)")
        t1 = _time.time()
        try:
            with open(tp, "wb") as f:
                f.write(data)
            dt = _time.time() - t1
            timings.append((base, dt))
            ok.append(base); lines.append(f"OK      {base}  ({dt:.2f}s, {len(data)} bytes)")
            print(f"[sa/upload] wrote {base} -> {dest} in {dt:.2f}s ({len(data)} bytes)")
            # Also archive to DB so any machine can pull this uploaded file later
            try:
                use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
                _sf = __import__("core.db_postgres", fromlist=["save_run_file"]).save_run_file if use_pg \
                      else __import__("core.db", fromlist=["save_run_file"]).save_run_file
                _sf("short_allocation", "upload", base, data, tag=target)
            except Exception as _dbe:
                print(f"[sa/upload] DB archive skipped: {_dbe}")
            if dt > 3:
                print(f"[sa/upload] ⚠ {base} took {dt:.2f}s to write — check antivirus / "
                      f"cloud-sync (OneDrive etc.) on '{dest}', it is the likely cause of upload timeouts.")
        except Exception as e:
            rejected.append(f"{base}: {e}"); lines.append(f"ERROR   {base}: {e}")
    lines.append(f"# done: {len(ok)} copied, {len(skipped)} already-present, "
                 f"{len(password_required)} password-required, {len(rejected)} rejected")
    try:
        with open(logp, "w", encoding="utf-8") as lf:
            lf.write("\n".join(lines) + "\n")
    except Exception:
        pass
    return {"dest": dest, "label": spec["label"], "ok": ok, "skipped": skipped,
            "rejected": rejected, "valid": spec["examples"], "log": os.path.basename(logp),
            "timings": [{"name": n, "seconds": round(s, 2)} for n, s in timings],
            "password_required": password_required}


def move_uploaded(items, target="short_allocation"):
    """Validate names, skip duplicates, copy valid ones; log every outcome."""
    c = cfg()
    spec = SA_VALID.get(target, SA_VALID["short_allocation"])
    if target == "las_funding":
        dest = c["las"]
    elif target == "common_files":
        dest = os.getenv("SA_COMMON_DIR", r"C:\COMMON FILES")
    else:
        dest = c["output"]
    os.makedirs(dest, exist_ok=True); os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logp = os.path.join(LOG_DIR, f"UPLOAD__{target}__{ts}.log")
    ok, skipped, rejected = [], [], []
    lines = [f"# UPLOAD  target={target} ({spec['label']})  dest={dest}  time={ts}"]
    for fp in items:
        base = os.path.basename(fp)
        if not _name_ok(base, target):
            rejected.append(base); lines.append(f"REJECT  {base}  (not a recognised {spec['label']} file)")
            try: os.remove(fp)
            except Exception: pass
            continue
        date_ok, date_err = _date_ok(base)
        if not date_ok:
            rejected.append(f"{base}: {date_err}")
            lines.append(f"REJECT  {base}  ({date_err})")
            try: os.remove(fp)
            except Exception: pass
            continue
        tp = os.path.join(dest, base)
        if os.path.exists(tp):
            skipped.append(base); lines.append(f"SKIP    {base}  (already in folder)")
            try: os.remove(fp)
            except Exception: pass
            continue
        try:
            shutil.move(fp, tp); ok.append(base); lines.append(f"OK      {base}")
        except Exception as e:
            rejected.append(f"{base}: {e}"); lines.append(f"ERROR   {base}: {e}")
    lines.append(f"# done: {len(ok)} copied, {len(skipped)} already-present, {len(rejected)} rejected")
    try:
        with open(logp, "w", encoding="utf-8") as lf:
            lf.write("\n".join(lines) + "\n")
    except Exception:
        pass
    return {"dest": dest, "label": spec["label"], "ok": ok, "skipped": skipped,
            "rejected": rejected, "valid": spec["examples"], "log": os.path.basename(logp)}


_LOG_TS_RE = re.compile(r"(\d{8})_(\d{6})")

def _log_display_time(full_path, filename):
    """Pull a proper 'DD Mon YYYY, HH:MM:SS' timestamp out of a log filename (they all embed
    one, e.g. SA-01__newshortallocationfiledownloader__20260713_080512.log), falling back to
    the file's mtime if the name doesn't match. Used so the Logs modal shows real dates/times
    instead of a raw filename."""
    m = _LOG_TS_RE.search(filename)
    if m:
        try:
            dt = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            return dt.strftime("%d %b %Y, %H:%M:%S")
        except Exception:
            pass
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(full_path)).strftime("%d %b %Y, %H:%M:%S")
    except Exception:
        return ""

def _log_friendly_label(filename):
    """Turns a log filename into a human step label (Download / SA Processing /
    LAS Funding / Upload / Backup Restore) so the Logs modal tells the user
    WHICH step a log belongs to, not just when it ran.
    Filenames look like: "{tag}__{script_base}__{ts}.log" (e.g.
    "SA-01__newshortallocationfiledownloader__20260713_140653.log"),
    or "UPLOAD__{target}__{ts}.log", or "BACKUP_RESTORE__{ts}.log"."""
    base_noext = os.path.splitext(filename)[0]
    parts = base_noext.split("__")
    if parts[0] == "UPLOAD":
        return "📤 Upload"
    if parts[0] == "BACKUP_RESTORE":
        return "♻️ Backup Restore"
    if len(parts) >= 3:
        # Post-email recovery run — not in STAGE_LABEL (it's a standalone
        # recovery script, not one of the four pipeline stages), but it still
        # needs its own label or it shows up unlabelled in the Logs modal.
        if parts[1] == "run_post_email_LAS4":
            return "📊 Post-Email Steps"
        script_label = STAGE_LABEL.get(parts[1] + ".py")
        if script_label:
            icon = {"Download": "📥", "SA Processing": "⚙️", "LAS Funding": "💳"}.get(script_label, "")
            return f"{icon} {script_label}".strip()
    return None

def list_logs(limit=80):
    # Sorting the filename string (old behaviour) groups logs by PREFIX
    # (BACKUP_RESTORE__ / SA-01__ / UPLOAD__...) before it groups by time, so the
    # most recent entry is almost never at the top. Sort by actual file mtime instead.
    try:
        # Only real terminal-output logs — exclude any stray non-.log state
        # files so they can't sort to the top and open as an unhelpful blob.
        names = [f for f in os.listdir(LOG_DIR) if f.endswith(".log")]
        names.sort(key=lambda f: os.path.getmtime(os.path.join(LOG_DIR, f)), reverse=True)
        names = names[:limit]
        out = []
        for f in names:
            full = os.path.join(LOG_DIR, f)
            out.append({"name": f, "size": os.path.getsize(full),
                        "display_time": _log_display_time(full, f),
                        "label": _log_friendly_label(f)})
        return out
    except Exception:
        return []


def read_log(name):
    p = os.path.join(LOG_DIR, os.path.basename(name))
    try:
        return open(p, encoding="utf-8", errors="replace").read()[-120000:]
    except Exception as e:
        return f"(could not read log: {e})"


# ── BACKUP RETRIEVAL ─────────────────────────────────────────────────────────
import re as _bk_re

def backup_roots():
    c = cfg()
    roots = []
    extra = os.getenv("SA_BACKUP_DIRS", "")
    if extra:
        roots += [r.strip() for r in extra.split(";") if r.strip()]
    for base in (c["output"], c["las"]):
        roots.append(os.path.join(base, "BACKUP FILES AUTOMATED"))
    out = []
    for r in roots:
        if r not in out:
            out.append(r)
    return out

def _folder_date(name):
    mm = _bk_re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", name.strip())
    if not mm:
        return None
    try:
        return datetime.date(int(mm.group(3)), int(mm.group(2)), int(mm.group(1)))
    except Exception:
        return None

def list_backups(from_date="", to_date="", types=None):
    import fnmatch as _fnm
    _globs = [g.strip().lower() for g in (types or []) if g and g.strip()]
    def pd(s):
        try: return datetime.datetime.strptime(s, "%Y-%m-%d").date()
        except Exception: return None
    d_from, d_to = pd(from_date), pd(to_date)
    results = []
    for root in backup_roots():
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            comp_date = None
            for part in dirpath.replace("\\", "/").split("/"):
                fd = _folder_date(part)
                if fd:
                    comp_date = fd
            for fn in files:
                if _globs and not any(_fnm.fnmatch(fn.lower(), g) for g in _globs):
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    st = os.stat(fp)
                except Exception:
                    continue
                fdate = comp_date or datetime.date.fromtimestamp(st.st_mtime)
                if d_from and fdate < d_from: continue
                if d_to and fdate > d_to: continue
                results.append({"path": fp, "name": fn, "date": fdate.strftime("%d-%m-%Y"),
                                "size": st.st_size, "rel": os.path.relpath(fp, root)})
    results.sort(key=lambda x: (x["date"], x["rel"]))
    return results[:3000]

def copy_backups(paths, dest):
    if not dest or not str(dest).strip():
        return {"ok": False, "error": "No destination folder given."}
    dest = str(dest).strip()
    safe_roots = [os.path.abspath(r) for r in backup_roots()]
    try:
        os.makedirs(dest, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"Could not create destination: {e}"}
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logp = os.path.join(LOG_DIR, f"BACKUP_RESTORE__{ts}.log")
    copied, failed, lines = [], [], [f"# RESTORE  dest={dest}  time={ts}"]
    for p in paths:
        ap = os.path.abspath(p)
        base = os.path.basename(ap)
        if not any(ap.startswith(r) for r in safe_roots):
            failed.append(f"{base}: outside backup folders"); lines.append(f"REJECT {p}"); continue
        try:
            shutil.copy2(ap, os.path.join(dest, base)); copied.append(base); lines.append(f"OK     {p}")
        except Exception as e:
            failed.append(f"{base}: {e}"); lines.append(f"FAIL   {p}: {e}")
    lines.append(f"# done: {len(copied)} copied, {len(failed)} failed")
    try:
        with open(logp, "w", encoding="utf-8") as lf:
            lf.write("\n".join(lines) + "\n")
    except Exception:
        pass
    return {"ok": True, "dest": dest, "copied": copied, "failed": failed, "log": os.path.basename(logp)}


def _norm_cc(x):
    """Normalise a client code to the same canonical form used during SA processing."""
    if not x:
        return x
    s = str(x).strip().upper().replace("*", "").replace("PRO_", "")
    if s in {"1292", "01292", "6436", "06436", "OWN"}:
        return "06436"
    return s


def _parse_lis_bytes(content: bytes, client_code: str):
    """Parse .lis file bytes → (peak, collateral) for client_code."""
    import csv, io
    peak = collateral = 0.0
    found = False
    for row in csv.reader(io.StringIO(content.decode("utf-8", errors="replace"))):
        if len(row) < 6:
            continue
        if _norm_cc(row[1]) == client_code:
            try:
                peak += float(str(row[2]).replace(",", "") or 0)
            except ValueError:
                pass
            try:
                collateral += float(str(row[5]).replace(",", "") or 0)
            except ValueError:
                pass
            found = True
    return (round(peak, 2), round(collateral, 2)) if found else None


def _parse_mcx_bytes(content: bytes, client_code: str):
    """Parse MCX_PeakMargin CSV bytes → (peak, collateral) for client_code."""
    import csv, io
    peak = coll = 0.0
    found = False
    for row in csv.reader(io.StringIO(content.decode("utf-8", errors="replace"))):
        if len(row) < 11:
            continue
        if _norm_cc(row[4]) == client_code:
            try:
                peak += float(str(row[8]).replace(",", "") or 0)
            except ValueError:
                pass
            try:
                coll += float(str(row[9]).replace(",", "") or 0) + \
                        float(str(row[10]).replace(",", "") or 0)
            except ValueError:
                pass
            found = True
    return (round(peak, 2), round(coll, 2)) if found else None


def _parse_nccl_bytes(content: bytes, client_code: str):
    """Parse NCCL_MARGIN_REP CSV bytes → (peak, collateral) for client_code."""
    import csv, io
    peak = coll = 0.0
    found = False
    reader = csv.DictReader(io.StringIO(content.decode("utf-8", errors="replace")))
    for row in reader:
        if _norm_cc(row.get("Client Code", "")) == client_code:
            try:
                peak += float(str(row.get("Initial Margin", "") or "0").replace(",", ""))
                peak += float(str(row.get("ELM Margin", "") or "0").replace(",", ""))
            except ValueError:
                pass
            try:
                coll += float(str(row.get("Collateral_Value_Snapshot", "") or "0").replace(",", ""))
            except ValueError:
                pass
            found = True
    return (round(peak, 2), round(coll, 2)) if found else None


def _client_peak_lookup_pg(client_code: str, from_date: str = "", to_date: str = ""):
    """
    Postgres-backed version of client_peak_lookup.
    Reads raw file bytes stored in run_files and parses them in-memory.
    Works from ANY machine — no local disk access needed.
    """
    from core.db_postgres import get_peak_files_range

    files = get_peak_files_range(from_date or None, to_date or None)
    if not files:
        return []

    # Group by (run_date, tag)
    from collections import defaultdict
    groups = defaultdict(dict)  # (run_date, tag) → {kind: (content, filename)}
    for f in files:
        key = (f["run_date"], f["tag"] or "")
        groups[key][f["kind"]] = (f["content"], f["filename"])

    results = []
    for (run_date_str, tag), kinds in sorted(groups.items()):
        run_folder = tag.replace("SA-0", "") if tag else "?"
        row = {
            "date": datetime.date.fromisoformat(run_date_str).strftime("%d-%m-%Y")
                    if "-" in run_date_str else run_date_str,
            "run": run_folder,
            "CM_PEAK": None, "FO_PEAK": None, "CO_PEAK": None,
            "MCX_PEAK": None, "NCDEX_PEAK": None,
            "CM_COLL": None, "FO_COLL": None, "CO_COLL": None,
            "MCX_COLL": None, "NCDEX_COLL": None,
        }

        for kind_key, peak_k, coll_k, parser in [
            ("input_c_lis",   "CM_PEAK",    "CM_COLL",    _parse_lis_bytes),
            ("input_f_lis",   "FO_PEAK",    "FO_COLL",    _parse_lis_bytes),
            ("input_o_lis",   "CO_PEAK",    "CO_COLL",    _parse_lis_bytes),
        ]:
            if kind_key in kinds:
                r = parser(kinds[kind_key][0], client_code)
                if r:
                    row[peak_k], row[coll_k] = r

        if "input_mcx_peak" in kinds:
            r = _parse_mcx_bytes(kinds["input_mcx_peak"][0], client_code)
            if r:
                row["MCX_PEAK"], row["MCX_COLL"] = r

        if "input_nccl_peak" in kinds:
            r = _parse_nccl_bytes(kinds["input_nccl_peak"][0], client_code)
            if r:
                row["NCDEX_PEAK"], row["NCDEX_COLL"] = r

        peak_vals = [row[k] for k in ("CM_PEAK","FO_PEAK","CO_PEAK","MCX_PEAK","NCDEX_PEAK") if row[k] is not None]
        coll_vals = [row[k] for k in ("CM_COLL","FO_COLL","CO_COLL","MCX_COLL","NCDEX_COLL") if row[k] is not None]
        if peak_vals or coll_vals:
            row["TOTAL_PEAK"] = round(sum(peak_vals), 2) if peak_vals else None
            row["TOTAL_COLL"] = round(sum(coll_vals), 2) if coll_vals else None
            if row["TOTAL_PEAK"] is not None and row["TOTAL_COLL"] is not None:
                row["SHORTAGE"] = round(row["TOTAL_COLL"] - row["TOTAL_PEAK"], 2)
            else:
                row["SHORTAGE"] = None
            results.append(row)

    return results


def client_peak_lookup(client_code: str, from_date: str = "", to_date: str = ""):
    """
    Return per-date/run peak values for *client_code*.

    Postgres-only — reads file bytes from run_files table.
    Raises if USE_POSTGRES=false or if the query fails.
    No disk fallback: all machines see the same data from the database.

    Columns extracted:
      CM_PEAK      — MINIMUM MARGIN REQ(A) from C_SA01_*.lis
      FO_PEAK      — MINIMUM MARGIN REQ(A) from F_SA01_*.lis
      CO_PEAK      — MINIMUM MARGIN REQ(A) from O_SA01_*.lis
      MCX_PEAK     — TOTAL UPFRONT MARGIN  from MCX_PeakMargin*.csv
      NCDEX_PEAK   — Initial Margin + ELM  from NCCL_MARGIN_REP*.csv
      CM_COLL      — TOTAL VALUE OF COLLATERAL(D) from C_LIS
      FO_COLL      — TOTAL VALUE OF COLLATERAL(D) from F_LIS
      CO_COLL      — TOTAL VALUE OF COLLATERAL(D) from O_LIS
      MCX_COLL     — CLIENT MARGIN ALLOCATION + CLIENT OTHER COLLATERALS from MCX
      NCDEX_COLL   — Collateral_Value_Snapshot from NCCL
      TOTAL_PEAK   — sum of all peak columns present
      TOTAL_COLL   — sum of all collateral columns present
      SHORTAGE     — TOTAL_COLL - TOTAL_PEAK
    """
    client_code = _norm_cc(client_code)
    use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
    if not use_pg:
        raise RuntimeError(
            "USE_POSTGRES=false — client peak lookup requires Postgres. "
            "Set USE_POSTGRES=true in .env."
        )
    return _client_peak_lookup_pg(client_code, from_date, to_date)

    # --- DISK SCAN (removed — all data must come from Postgres) ---
    # If you need to re-enable disk scan for testing, see git history.
    import csv as _csv
    import fnmatch as _fnm

    def _pd(s):
        try:
            return datetime.datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    d_from, d_to = _pd(from_date), _pd(to_date)

    def _fsum(path, cc_col_idx, val_col_idxs, delimiter=",", skip_header=False):
        """Read a delimited file and sum value columns for rows matching client code."""
        total = [0.0] * len(val_col_idxs)
        found = False
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                reader = _csv.reader(fh, delimiter=delimiter)
                for i, row in enumerate(reader):
                    if skip_header and i == 0:
                        continue
                    if len(row) <= max(cc_col_idx, *val_col_idxs):
                        continue
                    if _norm_cc(row[cc_col_idx]) == client_code:
                        for j, vi in enumerate(val_col_idxs):
                            try:
                                total[j] += float(str(row[vi]).replace(",", "").strip() or 0)
                            except ValueError:
                                pass
                        found = True
        except Exception:
            pass
        return total if found else None

    def _nccl_read(path, cc_field, val_fields):
        """Read NCCL CSV (has header) and return dict of sums for the client."""
        totals = {f: 0.0 for f in val_fields}
        found = False
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                reader = _csv.DictReader(fh)
                for row in reader:
                    cc = _norm_cc(row.get(cc_field, ""))
                    if cc == client_code:
                        for f in val_fields:
                            try:
                                totals[f] += float(str(row.get(f, "") or "0").replace(",", "").strip() or 0)
                            except ValueError:
                                pass
                        found = True
        except Exception:
            pass
        return totals if found else None

    results = []

    for root in backup_roots():
        if not os.path.isdir(root):
            continue
        try:
            date_entries = os.listdir(root)
        except Exception:
            continue
        for date_folder in date_entries:
            fd = _folder_date(date_folder)
            if not fd:
                continue
            if d_from and fd < d_from:
                continue
            if d_to and fd > d_to:
                continue
            date_path = os.path.join(root, date_folder)
            try:
                run_entries = sorted(os.listdir(date_path))
            except Exception:
                continue
            for run_folder in run_entries:
                run_path = os.path.join(date_path, run_folder)
                if not os.path.isdir(run_path):
                    continue
                try:
                    files_in_run = os.listdir(run_path)
                except Exception:
                    continue

                row = {
                    "date": fd.strftime("%d-%m-%Y"),
                    "run": run_folder,
                    "CM_PEAK": None, "FO_PEAK": None, "CO_PEAK": None,
                    "MCX_PEAK": None, "NCDEX_PEAK": None,
                    "CM_COLL": None, "FO_COLL": None, "CO_COLL": None,
                    "MCX_COLL": None, "NCDEX_COLL": None,
                }

                # LIS files — no header, comma-delimited
                # cols: 0=DATE 1=CLIENT_CODE 2=MIN_MARGIN_REQ 3=CASH_COLL 4=NON_CASH_COLL 5=TOTAL_COLL 6=EOD_SHORT
                for prefix, peak_key, coll_key in [
                    ("C_SA01_", "CM_PEAK",  "CM_COLL"),
                    ("F_SA01_", "FO_PEAK",  "FO_COLL"),
                    ("O_SA01_", "CO_PEAK",  "CO_COLL"),
                ]:
                    matches = [f for f in files_in_run
                               if f.upper().startswith(prefix) and f.lower().endswith(".lis")]
                    if matches:
                        fp = os.path.join(run_path, matches[-1])
                        r = _fsum(fp, cc_col_idx=1, val_col_idxs=[2, 5])
                        if r is not None:
                            row[peak_key] = r[0]
                            row[coll_key] = r[1]

                # MCX Peak — no header, comma-delimited
                # cols: 0=DATE 1=SNAPSHOT 2=CM_ID 3=TM_ID 4=CLIENT_CODE 5=INIT_MARGIN 6=ELM
                #        7=NET_BUY_PREMIUM 8=TOTAL_UPFRONT 9=CLIENT_MARGIN_ALLOC 10=OTHER_COLL 11=SHORT
                mcx_matches = [f for f in files_in_run
                               if f.upper().startswith("MCX_PEAKMARGIN") and f.lower().endswith(".csv")]
                if mcx_matches:
                    fp = os.path.join(run_path, mcx_matches[-1])
                    r = _fsum(fp, cc_col_idx=4, val_col_idxs=[8, 9, 10])
                    if r is not None:
                        row["MCX_PEAK"] = r[0]
                        row["MCX_COLL"] = r[1] + r[2]

                # NCDEX (NCCL) Peak — has header row
                nccl_matches = [f for f in files_in_run
                                if f.upper().startswith("NCCL_MARGIN_REP") and f.lower().endswith(".csv")]
                if nccl_matches:
                    fp = os.path.join(run_path, nccl_matches[-1])
                    r = _nccl_read(fp, "Client Code",
                                   ["Initial Margin", "ELM Margin", "Collateral_Value_Snapshot"])
                    if r is not None:
                        row["NCDEX_PEAK"] = (r["Initial Margin"] or 0) + (r["ELM Margin"] or 0)
                        row["NCDEX_COLL"] = r["Collateral_Value_Snapshot"] or 0

                # Derived totals
                peak_vals  = [row[k] for k in ("CM_PEAK","FO_PEAK","CO_PEAK","MCX_PEAK","NCDEX_PEAK") if row[k] is not None]
                coll_vals  = [row[k] for k in ("CM_COLL","FO_COLL","CO_COLL","MCX_COLL","NCDEX_COLL") if row[k] is not None]
                if peak_vals or coll_vals:
                    row["TOTAL_PEAK"] = round(sum(peak_vals), 2) if peak_vals else None
                    row["TOTAL_COLL"] = round(sum(coll_vals), 2) if coll_vals else None
                    if row["TOTAL_PEAK"] is not None and row["TOTAL_COLL"] is not None:
                        row["SHORTAGE"] = round(row["TOTAL_COLL"] - row["TOTAL_PEAK"], 2)
                    else:
                        row["SHORTAGE"] = None
                    # Round individual values
                    for k in ("CM_PEAK","FO_PEAK","CO_PEAK","MCX_PEAK","NCDEX_PEAK",
                              "CM_COLL","FO_COLL","CO_COLL","MCX_COLL","NCDEX_COLL"):
                        if row[k] is not None:
                            row[k] = round(row[k], 2)
                    results.append(row)

    results.sort(key=lambda x: (x["date"], x["run"]))
    return results


def browse_folder():
    """Open a native folder-picker on the RMS machine (same desktop as the dashboard)."""
    code = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r=tk.Tk(); r.withdraw()\n"
        "try:\n    r.attributes('-topmost', True)\nexcept Exception:\n    pass\n"
        "p=filedialog.askdirectory(title='Choose destination folder for backup files')\n"
        "print(p or '')\n"
    )
    try:
        c = cfg()
        out = subprocess.run([c["python"], "-c", code], capture_output=True, text=True, timeout=300)
        return {"ok": True, "path": (out.stdout or "").strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}
