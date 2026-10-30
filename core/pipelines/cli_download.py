"""
core/pipelines/cli_download.py — Client Prudential Limit's DOWNLOAD step.

Downloads these files (per explicit request — this button used to also handle
Allocation_Detail + the prudential-limit txt, which is now its own step,
core/pipelines/cli_allocation.py, run separately as Step 1 of Run OLD):
  CM_ClientLevelCollaterals_*.csv
  FO_ClientLevelCollaterals_*.csv
  CO_ClientLevelCollaterals_*.csv
  ClientNetEffectiveDeposits_*.csv
  Kambala_Omny_Depo_Adhoc_All_level.txt_<DDMMYYYY>.txt (from FTP's KAMB/DEPO folder —
  same single-file fetch core.pipeline_box._pb_download_kambala_from_ftp() also used
  by cli_allocation.py's precheck; added here per explicit request so retrying this
  button alone can pull it too, not just Run OLD's precheck)

Runs as a genuine subprocess (via core.pipeline_box._stream_code), same as every
other pipeline's main Run — this is what makes Stop able to kill it instantly no
matter what it's doing (a nested login subprocess), since Stop does `taskkill /F /T`
on the whole process tree, not just one Python object.

Logic: copy today's files from CTCL_NMASS_FOLDER if CTCL vs NMASS already ran today
(same files, no point re-downloading), else run NMASS_NCFE_API_MIX.PY as a nested
subprocess (same tested auth-fail tags) and copy the results across. Any file already
present locally is skipped entirely. Kambala is fetched separately via FTP (its own
folder, unrelated to the NSCCL/NCDEX login) and is non-fatal if not found yet.

Credentials: CTCL_NMASS_USER/PASSWORD/CONSCODE, CTCL_NCDEX_USER/PASSWORD (shared with
the "ctcl" pipeline). Kambala reuses FTP_HOST/FTP_USER/FTP_PASS (shared with Upload).

Tags this prints for core.pipeline_box._status() to pick up:
  [NMASS_AUTH_FAIL] / [NCFE_AUTH_FAIL] / [NCFE_LOCKED]  — from the nested subprocess
  [MISSING_FILE] <name>                                  — an expected file is absent
  [FRIENDLY_MSG] <text>                                   — plain-English AI fallback
"""
import datetime
import os
import re
import shutil
import subprocess

from core.error_translator import translate_error
from core import dl_lock

_AUTH_BAD = re.compile(r"\[(NMASS|MCX90|NCFE)_(AUTH_FAIL|LOCKED)\]", re.I)


def run_cli_download():
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    base_folder = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    ctcl_folder = os.getenv("CTCL_NMASS_FOLDER", r"D:\CTCL VS NMASS")
    os.makedirs(base_folder, exist_ok=True)
    today_str = datetime.datetime.now().strftime("%d%m%Y")
    _log_buffer = []

    def _p(msg):
        print(msg, flush=True)
        _log_buffer.append(str(msg))

    auth_fail_happened = False

    # ── NSCCL (CM/FO/CO) + NCDEX (ClientNetEffectiveDeposits) ────────────────
    _p("🚀 Downloading NSCCL + NCDEX collateral files")
    need_keys = ["CM_ClientLevelCollaterals", "FO_ClientLevelCollaterals",
                 "CO_ClientLevelCollaterals", "ClientNetEffectiveDeposits"]

    # CM/FO/CO must be .txt (original script requests JSON:TXT). A same-day .csv
    # (e.g. downloaded by CTCL, which requests CSV) is NOT accepted or copied for
    # CLI — it would be treated as missing and re-downloaded as .txt.
    # ClientNetEffectiveDeposits has no such restriction (always .csv).
    _TXT_ONLY = ("CM_ClientLevelCollaterals", "FO_ClientLevelCollaterals",
                 "CO_ClientLevelCollaterals")

    def _match(f, key):
        if key not in f or today_str not in f:
            return False
        return f.lower().endswith(".txt") if key in _TXT_ONLY else True

    def _has_today(folder, key):
        try:
            return any(_match(f, key) for f in os.listdir(folder))
        except Exception:
            return False

    missing_keys = [k for k in need_keys if not _has_today(base_folder, k)]
    if missing_keys:
        copied = []
        for k in list(missing_keys):
            if _has_today(ctcl_folder, k):
                try:
                    src_name = next(f for f in os.listdir(ctcl_folder) if _match(f, k))
                    shutil.copy2(os.path.join(ctcl_folder, src_name), os.path.join(base_folder, src_name))
                    copied.append(src_name); missing_keys.remove(k)
                except Exception:
                    pass
        if copied:
            _p(f"⏭ Copied from CTCL vs NMASS (already ran today): {', '.join(copied)}")
    else:
        _p("⏭ All four files already present locally — nothing to download")

    if missing_keys:
        _p("ℹ️  Not found yet — logging in via NMASS_NCFE_API_MIX.PY (same as CTCL vs NMASS)…")
        script = os.getenv("CTCL_NMASS_SCRIPT", os.path.join(_repo, "ctcl_codes", "NMASS_NCFE_API_MIX.PY"))
        python = os.getenv("CTCL_PYTHON", os.getenv("SA_PYTHON", "python"))
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"; env["PYTHONUTF8"] = "1"
        # ── Download into CLI's OWN folder ─────────────────────────────────
        # NMASS_NCFE_API_MIX.PY defaults to r"D:\CTCL VS NMASS" when this isn't
        # set. Because this step never set it, CLI's download was writing its
        # collateral files into the CTCL folder and then copying them across —
        # which quietly seeded CTCL's folder with files CTCL had not downloaded.
        # A CTCL run starting later then saw "files already present", skipped its
        # own download, and processed data fetched by a different pipeline at a
        # different time. (Confirmed in logs: a 14:29 CLI download produced the
        # files a 14:55 CTCL run went on to use.) Pointing the script at CLI's
        # own folder keeps each box's downloads to itself.
        env["NMASS_SAVE_DIR"] = base_folder
        # Ditto the original ALLOCATION_CLI_NMASS_NCDEX.py: request "JSON:TXT" so
        # CM/FO/CO arrive as .txt from the exchange itself (CTCL/MS15 keep CSV).
        env["NMASS_DATA_FORMAT"] = "TXT"
        if not os.path.isfile(script):
            _p(f"❌ Script not found: {script}")
        else:
            # This script writes its captcha image + session cookie to a FIXED
            # path shared by ctcl/ms15/cli regardless of which one calls it —
            # the one real conflict between the three pipes. This runs inside
            # CLI's own subprocess (a separate OS process from the main
            # server), so the guard must be a cross-process file lock
            # (core/dl_lock.py), not an in-memory one — same lock ctcl/ms15's
            # manual Download buttons use (core/pipelines/nmass_download.py).
            try:
                dl_lock.acquire("cli")
            except dl_lock.NmassLoginBusyError as e:
                _p(f"⏳ NMASS/NCDEX login is in use by '{e.owner}' right now — this step "
                   f"usually takes under a minute; it will be picked up on the next run.")
            else:
                try:
                    # Stream the login script's output LIVE (it used to be captured
                    # and only printed after exit, so a hang looked like a silent
                    # freeze) and enforce a hard timeout so a stuck exchange
                    # login/captcha can never block the whole run forever.
                    import threading, time as _t
                    _timeout = int(os.getenv("CLI_NMASS_TIMEOUT_SEC", "900"))
                    proc = subprocess.Popen([python, "-u", script], stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                            errors="replace", env=env)
                    def _pump():
                        nonlocal auth_fail_happened
                        for line in proc.stdout:
                            line = line.rstrip("\r\n")
                            _p(line)
                            if _AUTH_BAD.search(line):
                                auth_fail_happened = True
                    _th = threading.Thread(target=_pump, daemon=True)
                    _th.start()
                    _t0 = _t.time()
                    while proc.poll() is None and _t.time() - _t0 < _timeout:
                        _t.sleep(1)
                    if proc.poll() is None:
                        _p(f"⏱️ NMASS/NCDEX login did not finish within {_timeout}s — killing it "
                           f"(exchange site slow/captcha stuck). Re-run, or place the 4 files manually.")
                        try:
                            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                           capture_output=True)
                        except Exception:
                            proc.kill()
                    _th.join(timeout=5)
                    if proc.returncode == 0:
                        # Files now land directly in base_folder (NMASS_SAVE_DIR
                        # above), so there is nothing to copy across any more —
                        # just report what actually arrived, and say plainly if
                        # something expected didn't.
                        landed, still_missing = [], []
                        for k in need_keys:
                            if _has_today(base_folder, k):
                                try:
                                    landed.append(next(f for f in os.listdir(base_folder)
                                                       if _match(f, k)))
                                except Exception:
                                    landed.append(k)
                            else:
                                still_missing.append(k)
                        _p(f"✅ NSCCL/NCDEX done — saved to {base_folder}: "
                           f"{', '.join(landed) if landed else '(nothing)'}")
                        for k in still_missing:
                            _p(f"[MISSING_FILE] {k}")
                    elif not auth_fail_happened:
                        _p(f"⚠️ NMASS/NCDEX subprocess exited with code {proc.returncode}")
                        _ai = translate_error("Client Prudential Limit — NSCCL/NCDEX collateral files", _log_buffer)
                        if _ai:
                            _p(f"[FRIENDLY_MSG] {_ai}")
                except Exception as e:
                    _p(f"❌ Failed to run NMASS_NCFE_API_MIX.PY: {e}")
                    _ai = translate_error("Client Prudential Limit — NSCCL/NCDEX collateral files", _log_buffer)
                    if _ai:
                        _p(f"[FRIENDLY_MSG] {_ai}")
                finally:
                    dl_lock.release()

    # ── Kambala (KAMB/DEPO on FTP) — separate single-file fetch, non-fatal ────
    kambala_name = f"Kambala_Omny_Depo_Adhoc_All_level.txt_{today_str}.txt"
    kambala_path = os.path.join(base_folder, kambala_name)
    # Kambala only feeds CLI_PRUDENTIAL_LIMIT.txt (morning-only), so skip it on
    # the afternoon / evening runs entirely. Slot = the run-window gate; outside
    # every window fall back to the clock (before 12:00 = morning).
    try:
        from core.pipelines.cli_allocation_new import _current_window as _kcw
        _kslot, _ = _kcw()
    except Exception:
        _kslot = None
    if _kslot is None:
        _kslot = "morning" if datetime.datetime.now().hour < 12 else "later"
    if _kslot != "morning":
        _p("⏭ Skipping Kambala — only needed for the morning run (builds "
           "CLI_PRUDENTIAL_LIMIT.txt); not needed for afternoon/evening.")
    elif os.path.exists(kambala_path):
        _p(f"⏭ Kambala already present: {kambala_name}")
    else:
        _p("🚀 Downloading Kambala (FTP KAMB/DEPO)")
        try:
            from core.pipeline_box import _pb_download_kambala_from_ftp
            if _pb_download_kambala_from_ftp(kambala_path, kambala_name):
                _p(f"✅ Downloaded: {kambala_name}")
            else:
                _p(f"[MISSING_FILE] {kambala_name}")
        except Exception as _ke:
            _p(f"⚠️ Kambala download failed (non-fatal): {_ke}")
            _p(f"[MISSING_FILE] {kambala_name}")

    # ── Final missing-file check (non-fatal — Download still counts as done) ─
    still_missing = [k for k in need_keys if not _has_today(base_folder, k)]
    for k in still_missing:
        _p(f"[MISSING_FILE] {k}_{today_str}")

    if auth_fail_happened:
        raise SystemExit(1)   # login_failed event already carries the real message
    _p("✅ Client Prudential Limit download step finished")


if __name__ == "__main__":
    run_cli_download()
