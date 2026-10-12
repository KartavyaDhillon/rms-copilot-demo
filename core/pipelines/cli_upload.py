"""
core/pipelines/cli_upload.py — Client Prudential Limit's FTP-PUSH + BACKUP step.

This used to also download+convert the MCX RSK383 file — that's now its own dedicated
"MCX Web Allocation" button/step (core/pipelines/cli_mcx_download.py), same pattern as
CTCL's standalone "📥 MCX 90%" button. This module only pushes whatever's currently
sitting in CLI_FOLDER up to the FTP server, then backs everything up.

Rewritten to run as a genuine subprocess via core.pipeline_box._stream_code() (like
every other pipeline's main Run) so Stop can kill it instantly regardless of which
blocking call (the FTP transfer itself) is in progress.

STEP 1 — FTP upload: same ALWAYS_FILES + DATE_BASED_FILES rules as the original
         MCX_UPLOAD_BACKUP.py, but with a real file-existence check — any expected
         file that's missing is reported by name (via [MISSING_FILE]) instead of being
         silently skipped, and the upload continues with whatever IS present.
STEP 2 — backup + cleanup (move everything except .py/.bat into
         BACKUPFILES AUTOMATED/<DD-MM-YYYY>/<slot>/, where <slot> is
         Morning/Afternoon/Evening based on the actual clock time this step
         runs — CLI runs up to 3x/day (07:00-10:00, 15:30-17:15, 19:00-21:30,
         overridable via CLI_RUN1_WINDOW/CLI_RUN2_WINDOW/CLI_RUN3_WINDOW in
         .env — see _cli_env_window()), and before this split every run of
         the day landed in the same flat date folder, so a later same-day
         run silently overwrote an earlier run's same-named files on disk
         (each was still archived to Postgres separately, just not
         distinguishable on disk). See _cli_time_slot().

FTP: FTP_HOST/FTP_USER/FTP_PASS (shared with the Download step and other downloaders).
STEP 1B — the same files are also pushed to the Kambala FTP (CLI_KAMB_FTP_HOST/_PORT/_USER/_PASS/_DIR,
         see _upload_to_kambala); our own FTP upload above is unchanged.

Tags printed for core.pipeline_box._status():
  [MISSING_FILE] <name>       — an expected file wasn't uploaded
  [FRIENDLY_MSG] <text>       — plain-English AI fallback for an unrecognized failure
"""
import datetime
import os
import re
import shutil
import time

from core.error_translator import translate_error


def _cli_env_window(env_key, default):
    """Read an 'HH:MM-HH:MM' window from .env (e.g. CLI_RUN1_WINDOW), falling
    back to `default` ('HH:MM-HH:MM' string) on anything missing/malformed —
    a typo in .env must not break backfill classification. Returns
    (start_minutes, end_minutes) as ints for cheap comparison."""
    def _mins(hhmm, fallback_mins):
        try:
            h, m = (int(x) for x in hhmm.strip().split(":", 1))
            return h * 60 + m
        except Exception:
            return fallback_mins

    fb_start, _, fb_end = default.partition("-")
    fh, fm = (int(x) for x in fb_start.split(":"))
    eh, em = (int(x) for x in fb_end.split(":"))
    fb_start_mins, fb_end_mins = fh * 60 + fm, eh * 60 + em

    raw = (os.getenv(env_key) or "").strip() or default
    start_s, _, end_s = raw.partition("-")
    return _mins(start_s, fb_start_mins), _mins(end_s, fb_end_mins)


def _cli_time_slot(dt=None):
    """Classify a CLI run's time into Morning / Afternoon / Evening for the
    backup folder + Postgres tag. The pipeline is actually scheduled to run
    in three windows — 07:00-10:00, 15:30-17:15, 19:00-21:30 by default,
    overridable via CLI_RUN1_WINDOW/CLI_RUN2_WINDOW/CLI_RUN3_WINDOW in .env —
    and simple noon / 6pm cutoffs happen to split cleanly around all three
    (07-10 is all before noon, 15:30-17:15 is all between noon and 6pm,
    19-21:30 is all after 6pm), while still giving a sane bucket for a
    one-off manual run outside those exact windows instead of erroring or
    falling back to a 4th "unknown" category."""
    dt = dt or datetime.datetime.now()
    if dt.hour < 12: return "Morning"
    if dt.hour < 18: return "Afternoon"
    return "Evening"


def _archive_to_db(tag, kind, filepath, max_retries=3, retry_delay=2, run_date=None):
    """Copy a file's bytes into PostgreSQL (or SQLite fallback) with retry.
    Never raises. Same dual-path pattern already used inline further down in
    this file's normal backup step — pulled out so backfill_date_to_db() can
    reuse it without duplicating the USE_POSTGRES/save_run_file dance.

    `run_date` — ISO 'YYYY-MM-DD' string for the Postgres run_files.run_date
    column. IMPORTANT: db_postgres.save_run_file() defaults this to
    date.today() when not given — fine for a live same-day archive (the
    caller further down this file relies on exactly that default), but a
    backfilled HISTORICAL file must pass its real date explicitly, or every
    backfilled file silently gets today's date in run_date (the actual date
    only lives in `tag`) — which is exactly what used to make backfilled
    data show up as "today" everywhere that filters/displays by run_date
    (Client Data lookup, Get Files' date range, the merged-download Date
    column). backfill_date_to_db() below always passes this now."""
    if not filepath or not os.path.exists(filepath):
        return False
    fname = os.path.basename(filepath)
    try:
        use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
        _sf = (__import__("core.db_postgres", fromlist=["save_run_file"]).save_run_file if use_pg
               else __import__("core.db", fromlist=["save_run_file"]).save_run_file)
        with open(filepath, "rb") as f:
            content = f.read()
    except Exception as e:
        print(f"[cli_upload] WARNING could not read {filepath} for archiving: {e}")
        return False
    for attempt in range(1, max_retries + 1):
        try:
            _sf("cli", kind, fname, content, tag=tag, run_date=run_date)
            print(f"[cli_upload] archived {fname} ({len(content):,} bytes) -> DB (run_date={run_date})")
            return True
        except Exception as e:
            if attempt < max_retries:
                print(f"[cli_upload] archive attempt {attempt}/{max_retries} failed for {fname}: {e} — retrying in {retry_delay}s")
                time.sleep(retry_delay)
            else:
                print(f"[cli_upload] WARNING: {fname} could NOT be archived after {max_retries} attempts: {e}")
    return False


def backfill_date_to_db(date_str):
    """Push an ALREADY-backed-up CLI (Client Prudential Limit) date folder
    into the DB — for when the upload genuinely happened OUTSIDE this
    dashboard and its files were then copied by hand into this machine's
    BACKUPFILES AUTOMATED\\<date> folder.

    Handles both layouts: the current one (date\\Morning|Afternoon|Evening\\*,
    since CLI runs up to 3x/day and each slot now gets its own subfolder —
    see _cli_time_slot) and the older flat one (date\\* directly, from
    before that split, still valid for old dates already on disk).

    `date_str` — folder name exactly as on disk, DD-MM-YYYY (e.g. "21-07-2026").
    Safe to call more than once — re-archiving just adds another row."""
    base_folder = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    date_folder = os.path.join(base_folder, "BACKUPFILES AUTOMATED", date_str)
    if not os.path.isdir(date_folder):
        return {"ok": False, "error": f"Folder not found: {date_folder}"}
    archived, skipped, tags = [], [], []
    # The ACTUAL date these files belong to, as ISO — see _archive_to_db's
    # docstring for why this must be passed explicitly rather than left to
    # default to today.
    try:
        _dd, _mm, _yyyy = date_str.split("-")
        iso_date = f"{_yyyy}-{_mm}-{_dd}"
    except Exception:
        iso_date = None

    def _slot_for_subdir(name):
        """Maps a subfolder name to Morning/Afternoon/Evening for the tag —
        handles both the word-named folders (Morning/Afternoon/Evening) AND
        the HH-MM-SS exact-run-time folders some machines actually produce
        on disk (e.g. "08-36-11", "15-55-53", "20-23-14" — same idea as
        _cli_time_slot() but classifying from the folder name instead of
        the current clock). Returns None for anything else (e.g. a loose
        file sitting directly in the date folder isn't a slot dir).

        For an HH-MM-SS folder, checks the actual CLI run windows first —
        read from .env (CLI_RUN1_WINDOW / CLI_RUN2_WINDOW / CLI_RUN3_WINDOW,
        same vars cli_allocation_new.py's run-gate uses — one .env edit now
        controls both whether a run is allowed to start AND which bucket a
        backfilled file lands in), defaulting to Morning 07:00-10:00,
        Afternoon 15:30-17:15, Evening 19:00-21:30 — and only falls back to
        the broad <12/<18/else cutoff for a stray time outside all three
        (e.g. a one-off manual run), so the bucket a file lands in matches
        when it was ACTUALLY run, not just a rough half-of-day guess."""
        if name in ("Morning", "Afternoon", "Evening"):
            return name
        m = re.fullmatch(r"(\d{2})-(\d{2})-\d{2}", name)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
            mins = hour * 60 + minute
            m_start, m_end = _cli_env_window("CLI_RUN1_WINDOW", "07:00-10:00")
            a_start, a_end = _cli_env_window("CLI_RUN2_WINDOW", "15:30-17:15")
            e_start, e_end = _cli_env_window("CLI_RUN3_WINDOW", "19:00-21:30")
            if m_start <= mins <= m_end:
                return "Morning"
            if a_start <= mins <= a_end:
                return "Afternoon"
            if e_start <= mins <= e_end:
                return "Evening"
            # Outside all three real run windows (one-off manual run) —
            # fall back to a sane half-of-day bucket rather than leaving it
            # unclassified.
            if hour < 12: return "Morning"
            if hour < 18: return "Afternoon"
            return "Evening"
        return None

    slot_dirs = [(d, _slot_for_subdir(d)) for d in sorted(os.listdir(date_folder))
                 if os.path.isdir(os.path.join(date_folder, d))]
    slot_dirs = [(d, slot) for d, slot in slot_dirs if slot is not None]

    if slot_dirs:
        for dirname, slot in slot_dirs:
            slot_path = os.path.join(date_folder, dirname)
            tag = f"{date_str}/{slot}"
            tags.append(tag)
            for fname in sorted(os.listdir(slot_path)):
                fpath = os.path.join(slot_path, fname)
                if not os.path.isfile(fpath):
                    continue
                if _archive_to_db(tag, "backup", fpath, run_date=iso_date):
                    archived.append(f"{dirname}/{fname}")
                else:
                    skipped.append(f"{dirname}/{fname}")
    else:
        tags.append(date_str)
        for fname in sorted(os.listdir(date_folder)):
            fpath = os.path.join(date_folder, fname)
            if not os.path.isfile(fpath):
                continue
            if _archive_to_db(date_str, "backup", fpath, run_date=iso_date):
                archived.append(fname)
            else:
                skipped.append(fname)
    return {"ok": True, "folder": date_folder, "tags": tags, "archived": archived, "skipped": skipped}


def backfill_all_to_db():
    """Loop backfill_date_to_db() over EVERY date folder already sitting
    under BACKUPFILES AUTOMATED — for pushing months of history into
    Postgres in one call instead of entering each date by hand through the
    Backfill-to-DB prompt. Safe to re-run (each file just adds another row,
    same as backfill_date_to_db itself); skips anything that isn't a
    DD-MM-YYYY-named folder."""
    base_folder = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    root = os.path.join(base_folder, "BACKUPFILES AUTOMATED")
    if not os.path.isdir(root):
        return {"ok": False, "error": f"Folder not found: {root}"}
    date_re = re.compile(r"^\d{2}-\d{2}-\d{4}$")
    def _key(d):
        try: return datetime.datetime.strptime(d, "%d-%m-%Y")
        except Exception: return datetime.datetime.min
    date_folders = sorted(
        (d for d in os.listdir(root)
         if date_re.match(d) and os.path.isdir(os.path.join(root, d))),
        key=_key,
    )
    if not date_folders:
        return {"ok": False, "error": f"No DD-MM-YYYY date folders found under {root}"}
    per_date, total_archived, total_skipped = [], 0, 0
    for d in date_folders:
        r = backfill_date_to_db(d)
        n_arch = len(r.get("archived", [])) if r.get("ok") else 0
        n_skip = len(r.get("skipped", [])) if r.get("ok") else 0
        total_archived += n_arch
        total_skipped += n_skip
        per_date.append({"date": d, "ok": r.get("ok", False), "error": r.get("error"),
                          "archived": n_arch, "skipped": n_skip})
    return {"ok": True, "dates": date_folders, "per_date": per_date,
            "total_archived": total_archived, "total_skipped": total_skipped}


def _upload_to_kambala(files, log, cfg=None):
    """Second FTP push (Kambala), in addition to our own FTP above. Settings in .env, set from the box's
    Credentials button: CLI_KAMB_FTP_HOST / _PORT / _USER / _PASS / _DIR (folder, created if missing).
    Each file goes up as name.part, is renamed when complete (the receiver never sees a half file) and its size is
    checked. A broken connection restarts the whole login after 5 s (up to 5 times); a wrong password is not retried.
    Returns ([uploaded names], error text or '' , skipped reason or '')."""
    from ftplib import FTP, error_perm
    cfg = cfg or {}                                  # optional override (used by the scheduler processes)
    host = (cfg.get("host") or os.getenv("CLI_KAMB_FTP_HOST") or "202.58.232.29").strip()
    try:
        port = int((cfg.get("port") or os.getenv("CLI_KAMB_FTP_PORT") or "21").strip())
    except ValueError:
        port = 21
    user = (cfg.get("user") or os.getenv("CLI_KAMB_FTP_USER") or "ftpadmin").strip()
    pw = cfg.get("password") or os.getenv("CLI_KAMB_FTP_PASS") or ""
    rdir = (cfg.get("folder") or os.getenv("CLI_KAMB_FTP_DIR") or "/").strip() or "/"
    if not pw:
        return [], "", "no password set — open Credentials → Kambala FTP and save it"
    done = []

    def once():
        ftp = FTP()
        ftp.connect(host, port, timeout=60)
        try:
            ftp.login(user, pw)
            ftp.set_pasv(True)
            ftp.cwd("/")
            for d in [p for p in rdir.replace("\\", "/").split("/") if p]:
                try:
                    ftp.cwd(d)
                except error_perm:
                    ftp.mkd(d)
                    ftp.cwd(d)
            for fp in files:
                name, size = os.path.basename(fp), os.path.getsize(fp)
                tmp = name + ".part"
                with open(fp, "rb") as f:
                    ftp.storbinary(f"STOR {tmp}", f, blocksize=64 * 1024)
                try:
                    ftp.delete(name)                 # replace an older copy of the same name
                except Exception:
                    pass
                ftp.rename(tmp, name)
                ftp.voidcmd("TYPE I")
                try:
                    got = ftp.size(name)
                except Exception:
                    got = None                       # server has no SIZE — cannot verify, not an error
                if got is not None and got != size:
                    raise RuntimeError(f"size mismatch for {name}: local {size:,} bytes, server {got:,} bytes")
                if name not in done:
                    done.append(name)
                log(f"⬆ Kambala FTP uploaded: {name}")
        finally:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

    try:
        from core import net_retry
        net_retry.run(once, "Kambala FTP upload", log)
    except Exception as e:
        return done, f"{type(e).__name__}: {e}", ""
    return done, "", ""


def run_cli_upload():
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    base_folder = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    os.makedirs(base_folder, exist_ok=True)
    today_str = datetime.datetime.now().strftime("%d%m%Y")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    upload_log_file = os.path.join(base_folder, "upload_log.txt")
    _log_buffer = []

    def _p(msg):
        print(msg, flush=True)
        _log_buffer.append(str(msg))

    # ── Afternoon / evening Run NEW hand-off (see cli_allocation_new.py's
    # module docstring for the full picture): that step, when it detects a
    # NON-MORNING run, writes a marker naming ALLOCATION(AFTERNOON) or
    # ALLOCATION(EVENING) as the upload target instead of the default
    # ALLOCATION folder, and CLI_PRUDENTIAL_LIMIT.txt is legitimately absent
    # (those slots skip building it) — so it must not be flagged as a
    # [MISSING_FILE]. Run OLD never writes this marker, so this whole block is
    # a no-op for it, at any time of day.
    #
    # Two filenames are checked: the current generic one, and the older
    # evening-only name. A marker written by a pre-upgrade run (or by a
    # half-upgraded pair of machines) would otherwise be silently ignored and
    # the upload would go to the WRONG folder — the exact failure this whole
    # marker mechanism exists to prevent.
    _slot_target = None
    for _marker_name in (".cli_slot_upload", ".cli_evening_upload"):
        _marker_path = os.path.join(base_folder, _marker_name)
        if not os.path.exists(_marker_path):
            continue
        if _slot_target is None:
            try:
                _slot_target = open(_marker_path, encoding="utf-8").read().strip() or None
            except Exception:
                _slot_target = None
        # Remove BOTH regardless of which one was read — a stale leftover
        # would otherwise redirect tomorrow morning's upload.
        try:
            os.remove(_marker_path)
        except OSError:
            pass
    _slot_name = None
    if _slot_target:
        _slot_name = ("afternoon" if "AFTERNOON" in _slot_target.upper()
                      else "evening" if "EVENING" in _slot_target.upper() else "non-morning")
        _p(f"🕒 {_slot_name.capitalize()} Run NEW upload — target folder is {_slot_target} "
           f"(not the default ALLOCATION folder), CLI_PRUDENTIAL_LIMIT.txt "
           f"expected to be absent.")

    # ── STEP 1: FTP upload with a real missing-file check ────────────────────
    _p("🚀 STEP 1: FTP upload")
    expected = [
        ("MCX_WebAllocationDeallocation",     "MCX_WebAllocationDeallocation.csv"),
        ("CM_ClientLevelCollaterals",         f"CM_ClientLevelCollaterals_{today_str}"),
        ("FO_ClientLevelCollaterals",         f"FO_ClientLevelCollaterals_{today_str}"),
        ("CO_ClientLevelCollaterals",         f"CO_ClientLevelCollaterals_{today_str}"),
        ("ClientNetEffectiveDeposits",        f"ClientNetEffectiveDeposits_{today_str}"),
    ]
    if not _slot_target:
        # Morning (or any Run OLD, any time) — CLI_PRUDENTIAL_LIMIT.txt IS
        # expected, exactly as it always has been.
        expected.insert(0, ("CLI_PRUDENTIAL_LIMIT", "CLI_PRUDENTIAL_LIMIT.txt"))
    valid_ext = (".txt", ".csv", ".xlsx")
    try:
        local_files = os.listdir(base_folder)
    except Exception:
        local_files = []

    files_to_upload, missing_names = [], []
    for label, pattern in expected:
        # CM/FO/CO collateral files are .txt only (ditto the original) — a stray
        # .csv with the same prefix is never uploaded.
        _exts = (".txt",) if label in ("CM_ClientLevelCollaterals", "FO_ClientLevelCollaterals",
                                       "CO_ClientLevelCollaterals") else valid_ext
        match = next((f for f in local_files
                      if pattern.lower() in f.lower() and f.lower().endswith(_exts)), None)
        if match:
            files_to_upload.append(os.path.join(base_folder, match))
        else:
            missing_names.append(label)
            _p(f"[MISSING_FILE] {label}")

    hard_error = None
    if missing_names:
        # Every file is needed downstream — never upload a partial set. Nothing is uploaded and nothing is
        # moved to backup, so the files already downloaded stay in place for the re-run.
        hard_error = ("Upload NOT done — required file(s) missing: " + ", ".join(missing_names)
                      + ". Nothing was uploaded and nothing was moved to backup; run it again.")
        _p(f"❌ {hard_error}")
    elif not files_to_upload:
        _p("⚠️ No files found to upload")
    else:
        try:
            from ftplib import FTP
            ftp_host = os.getenv("FTP_HOST", "202.58.232.124")
            ftp_user = os.getenv("FTP_USER", "TRADING")
            ftp_pass = os.getenv("FTP_PASS", "")
            remote_folder = _slot_target or "/TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION"
            ftp = FTP(ftp_host)
            ftp.login(ftp_user, ftp_pass)
            ftp.cwd("/")
            for d in remote_folder.strip("/").split("/"):
                try: ftp.cwd(d)
                except Exception: ftp.mkd(d); ftp.cwd(d)

            for file_path in files_to_upload:
                filename = os.path.basename(file_path)
                success, error_msg = False, ""
                for attempt in range(3):
                    try:
                        with open(file_path, "rb") as f:
                            ftp.storbinary(f"STOR {filename}", f)
                        success = True
                        break
                    except Exception as e:
                        error_msg = str(e)
                        time.sleep(2)
                with open(upload_log_file, "a") as ulog:
                    if success:
                        ulog.write(f"[{now_str}] SUCCESS | {filename}\n")
                        _p(f"⬆ Uploaded: {filename}")
                    else:
                        ulog.write(f"[{now_str}] FAILED  | {filename} | {error_msg}\n")
                        _p(f"⚠️ Upload failed: {filename} — {error_msg}")
            ftp.quit()
        except Exception as e:
            hard_error = f"FTP upload failed: {e}"
            _p(f"❌ {hard_error}")

    # ── STEP 1B: the same files to the Kambala FTP (in addition to our own FTP above) ──
    # Runs before the backup step moves the files away. Our own FTP's behaviour/result is unchanged; a Kambala
    # problem is reported loudly but does not turn the run into "failed" (the files are in the backup folder and
    # can be pushed by hand).
    if files_to_upload and not missing_names:
        _p("🚀 STEP 1B: Kambala FTP upload")
        _k_done, _k_err, _k_skip = _upload_to_kambala(files_to_upload, _p)
        if _k_skip:
            _p(f"⚠️ Kambala FTP upload skipped — {_k_skip}")
            _p(f"[FRIENDLY_MSG] Kambala FTP upload was skipped: {_k_skip}.")
        elif _k_err:
            _p(f"❌ Kambala FTP upload FAILED — {_k_err}")
            _p(f"[FRIENDLY_MSG] Uploaded to our FTP, but the Kambala FTP upload failed ({_k_err}). "
               f"The files are in the backup folder — upload them to Kambala by hand.")
        else:
            _p(f"✅ Kambala FTP: {len(_k_done)} file(s) uploaded and verified")
        try:
            with open(upload_log_file, "a") as ulog:
                for _n in _k_done:
                    ulog.write(f"[{now_str}] KAMBALA SUCCESS | {_n}\n")
                if _k_err:
                    ulog.write(f"[{now_str}] KAMBALA FAILED  | {_k_err}\n")
        except Exception:
            pass

    # ── STEP 1.5: prune the OLDER slot folder(s) on the FTP ──────────────────
    # Per explicit instruction: the three ALLOCATION / ALLOCATION(AFTERNOON) /
    # ALLOCATION(EVENING) folders and what gets uploaded into each stay exactly
    # as they are — this only adds a deletion step alongside that unchanged
    # upload behaviour.
    #   Afternoon upload: the morning's plain ALLOCATION folder still has all of
    #   this morning's files sitting in it. Prune it down to just
    #   Allocation_Detail_<date>.csv + CLI_PRUDENTIAL_LIMIT.txt — the only two
    #   anything downstream still needs out of it.
    #   Evening upload: ALLOCATION(AFTERNOON) never holds either of those two
    #   files (both are morning-only), so once evening's own folder has its
    #   files there is nothing left in ALLOCATION(AFTERNOON) worth keeping —
    #   delete it outright, folder and all.
    if _slot_name == "afternoon":
        _p("🧹 STEP 1.5: Cleaning morning's ALLOCATION folder — keeping only "
           "Allocation Detail + CLI Prudential Limit")
        from core.pipeline_box import _pb_cleanup_ftp_folder_keep_prefixes
        _pb_cleanup_ftp_folder_keep_prefixes(
            "/TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION",
            ["Allocation_Detail", "CLI_PRUDENTIAL_LIMIT"], _p)
    elif _slot_name == "evening":
        _p("🧹 STEP 1.5: Deleting the afternoon's ALLOCATION(AFTERNOON) folder — "
           "no longer needed")
        from core.pipeline_box import _pb_delete_ftp_folder
        _pb_delete_ftp_folder("/TRADING/CTCL_FILES/KORP_LIVE/ALLOCATION(AFTERNOON)", _p)

    # ── STEP 2: backup + cleanup (still runs even if upload had trouble) ─────
    _p("🚀 STEP 2: Backup + cleanup")
    try:
        if missing_names:
            raise RuntimeError("skipped — files are kept in the folder because the upload was not completed")
        today_folder = datetime.datetime.now().strftime("%d-%m-%Y")
        # Slot label for the Postgres tag comes from the RUN's own decision (the
        # window gate in cli_allocation_new.py leaves a marker for afternoon/
        # evening; no marker = the morning flow), NOT from the wall clock — a
        # morning run done at 14:50 is still a Morning run. Clock-based
        # _cli_time_slot() is only the fallback if that can't be determined.
        slot = _slot_name.capitalize() if _slot_name in ("afternoon", "evening") else "Morning"
        # ⚠️ Ditto-same as the original MCX_UPLOAD_BACKUP.py: the run subfolder
        # is a BARE HH-MM-SS timestamp, NOT the slot word. The original script
        # has this exact warning: "It used to be prefixed with the slot name
        # (morning_08-14-32 etc.), which read more nicely but broke another
        # script that parses these folder names. Do not re-add the prefix."
        # Reverted here for the same reason — also fixes the real collision
        # risk where two runs in the same window on the same day would
        # otherwise overwrite each other's files in the identical Morning/
        # Afternoon/Evening folder. `slot` is still used for the Postgres tag
        # below (readable in the Backup Files browser) — only the on-disk
        # folder name changes.
        run_time = datetime.datetime.now().strftime("%H-%M-%S")
        backup_path = os.path.join(base_folder, "BACKUPFILES AUTOMATED", today_folder, run_time)
        os.makedirs(backup_path, exist_ok=True)
        tag = f"{today_folder}/{slot}"
        valid_ext2 = (".csv", ".txt", ".xlsx")
        exclude_ext = (".py", ".bat")
        moved = []
        for f in os.listdir(base_folder):
            full = os.path.join(base_folder, f)
            if not os.path.isfile(full) or f.endswith(exclude_ext) or not f.endswith(valid_ext2):
                continue
            dest = os.path.join(backup_path, f)
            if os.path.exists(dest):
                os.remove(dest)
            shutil.move(full, dest)
            moved.append(f)
            # Also archive a durable copy into Postgres/SQLite — same table the
            # manual "Upload files" button already uses (pb_upload in
            # pipeline_box.py) — so the backup isn't only living on whichever
            # machine ran this. Local move above still happens regardless (the
            # exchange-login scripts need the local folder), this is additive.
            # tag is "<date>/<slot>" (e.g. "20-08-2026/Afternoon") — same
            # "<date>/<run-or-slot>" convention ctcl/ms15 use with their run
            # number, so the Backup files browser can tell same-day runs apart.
            try:
                use_pg = os.getenv("USE_POSTGRES", "false").strip().lower() == "true"
                _sf = (__import__("core.db_postgres", fromlist=["save_run_file"]).save_run_file if use_pg
                       else __import__("core.db", fromlist=["save_run_file"]).save_run_file)
                with open(dest, "rb") as fb:
                    _sf("cli", "backup", f, fb.read(), tag=tag)
            except Exception as _dbe:
                _p(f"  DB archive skipped for {f}: {_dbe}")
        _p(f"📦 Backup done — {len(moved)} file(s) moved to {backup_path} ({slot}) + Postgres/SQLite")
    except Exception as e:
        _p(f"⚠️ Backup/cleanup failed: {e}")

    if hard_error:
        _ai = translate_error("Client Prudential Limit upload", _log_buffer)
        _p(f"[FRIENDLY_MSG] {_ai or hard_error}")
        raise SystemExit(1)
    _p("✅ Client Prudential Limit upload step finished"
       + (f" ({len(missing_names)} file(s) skipped — see popup)" if missing_names else ""))


if __name__ == "__main__":
    run_cli_upload()
