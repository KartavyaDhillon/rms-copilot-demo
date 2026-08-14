"""
core/pipelines/cli_mcx_download.py — dedicated "MCX Web Allocation" download step.

Split out of what used to be cli_upload.py's Step 1+2, per request — this needs to be
its own button (like CTCL's standalone "📥 MCX 90%") so it can be run/retried on its
own, and so the Upload/Run steps can skip it automatically if the file's already there.

Downloads RSK383 "Query Detailed Limit" from eclear.mcxccl.com by reusing
sa_codes/mcx_deposit_downloader.py's download_mcx_deposit() — the SAME report the SA
"MCX Deposit" button already downloads (same 56720CMMKR014139 account), already fixed
with a wrong-password tag and a capped relogin loop. Converts the result to
MCX_WebAllocationDeallocation.csv (finds the "UCC Code" header row, strips identifying
columns, removes thousand-separator commas — matches the original MCX_UPLOAD_BACKUP.py).

Runs as a genuine subprocess via core.pipeline_box._stream_code() so Stop can kill it
instantly regardless of which blocking call (the TCS BANCS login, the pandas convert)
is in progress.

Credentials: SA_MCX_DEPOSIT_USER/PASSWORD (shared with the SA MCX Deposit button).

Tags printed for core.pipeline_box._status():
  [MCX_DEPOSIT_AUTH_FAIL] / [MCX_DEPOSIT_LOCKED]  — from download_mcx_deposit()
  [FRIENDLY_MSG] <text>                            — plain-English AI fallback
"""
import os
import re
import sys
import datetime

from core.error_translator import translate_error

_AUTH_BAD = re.compile(r"\[MCX_DEPOSIT_(AUTH_FAIL|LOCKED)\]", re.I)

# Two names a valid MCX Web Allocation export can show up under:
#   1. MCX_DEPOSIT_HH.MM.xlsx        — what this pipeline's own downloader
#      produces (sa_codes/mcx_deposit_downloader.py's download_mcx_deposit()).
#      The old check here only looked for the "random name" pattern below, so
#      it never recognized its own downloaded file on the next run and always
#      re-triggered a fresh TCS BANCS login.
#   2. <random>-<14-digit-timestamp>.xlsx — the raw TCS BANCS ARCH export
#      filename, kept so a file placed manually (without renaming it) is also
#      recognized and NOT re-downloaded/overwritten.
_MCX_DEPOSIT_RE = re.compile(r"^MCX_DEPOSIT_.*\.xlsx$", re.I)
_MCX_RANDOM_RE = re.compile(r"^[A-Za-z0-9]+-\d{14}\.xlsx$", re.I)


# How old an existing export may be and still be reused instead of re-fetched.
# Same reasoning (and same default) as mcx90_download.py's _MCX90_MAX_AGE_MIN:
# "from today" is not the same as "current" — a file pulled hours ago would
# otherwise be silently reused for a much later run.
_MCX_MAX_AGE_MIN = float(os.getenv("MCX90_MAX_AGE_MIN", "45"))


def _purge_stale_mcx_exports(base_folder, _p=None, max_age_min=_MCX_MAX_AGE_MIN):
    """Delete today's MCX exports that are older than `max_age_min`, so a stale
    one can't satisfy the "already present" check and block a fresh download.
    Only touches files matching the two known export name patterns AND dated
    today — nothing else in the folder is considered."""
    now = datetime.datetime.now()
    for f in os.listdir(base_folder):
        if not (_MCX_DEPOSIT_RE.match(f) or _MCX_RANDOM_RE.match(f)):
            continue
        full = os.path.join(base_folder, f)
        if not os.path.isfile(full):
            continue
        try:
            mt = datetime.datetime.fromtimestamp(os.path.getmtime(full))
        except OSError:
            continue
        if mt.date() != now.date():
            continue
        age = (now - mt).total_seconds() / 60.0
        if age <= max_age_min:
            continue
        try:
            os.remove(full)
            if _p:
                _p(f"🗑 Removed stale MCX export {f} ({age:.0f} min old, limit "
                   f"{max_age_min:.0f} min) — fetching a fresh one.")
        except Exception as e:
            if _p:
                _p(f"⚠️ Could not remove stale MCX export {f}: {e}")


def _find_existing_mcx_file(base_folder, max_age_min=_MCX_MAX_AGE_MIN):
    now = datetime.datetime.now()
    candidates = []
    for f in os.listdir(base_folder):
        if not (_MCX_DEPOSIT_RE.match(f) or _MCX_RANDOM_RE.match(f)):
            continue
        full = os.path.join(base_folder, f)
        if not os.path.isfile(full):
            continue
        # Only reuse it if it's from today — an old leftover export from a
        # previous day shouldn't be silently fed into today's conversion —
        # AND only if it's still current (see _MCX_MAX_AGE_MIN).
        try:
            mt = datetime.datetime.fromtimestamp(os.path.getmtime(full))
        except OSError:
            continue
        if mt.date() != now.date():
            continue
        if max_age_min is not None and (now - mt).total_seconds() / 60.0 > max_age_min:
            continue
        candidates.append(full)
    if not candidates:
        return None
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def run_cli_mcx_download():
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    base_folder = os.getenv("CLI_FOLDER", r"D:\CLI_PRUDENTIAL_LIMIT")
    os.makedirs(base_folder, exist_ok=True)
    csv_path = os.path.join(base_folder, "MCX_WebAllocationDeallocation.csv")
    _log_buffer = []

    def _p(msg):
        print(msg, flush=True)
        _log_buffer.append(str(msg))

    if os.path.exists(csv_path):
        _p("⏭ MCX_WebAllocationDeallocation.csv already present — skipping TCS BANCS login")
        return

    # Clear out today's too-old exports BEFORE the "already present" check, so
    # a stale one can't block the download (see _purge_stale_mcx_exports).
    _purge_stale_mcx_exports(base_folder, _p)
    xlsx_path = _find_existing_mcx_file(base_folder)
    if xlsx_path:
        _p(f"⏭ Existing MCX export found: {os.path.basename(xlsx_path)} — skipping TCS BANCS login")
    else:
        _p("🚀 Logging in to TCS BANCS (RSK383 Query Detailed Limit) — reusing sa_codes/mcx_deposit_downloader.py")
        # Tag this as a CLI-triggered request so the dashboard's RSA token popup
        # shows "Client Prudential Limit", not "Short Allocation" — the two boxes
        # share the exact same download_mcx_deposit() function and .token_state
        # file (in SA_OUTPUT_DIR), so without this the popup always said "sa"
        # regardless of which box actually asked for the token. See
        # sa_codes/mcx_deposit_downloader.py's _token_popup() and
        # api.py's /sa/token/pending.
        os.environ["MCX_DEPOSIT_CALLER"] = "cli"
        sys.path.insert(0, os.path.join(_repo, "sa_codes"))
        import importlib
        # Import BEFORE the try below (not inside it) so `mdd` is always bound —
        # otherwise `except mdd.MCXDepositBusyError` would itself raise NameError
        # if this import ever failed while handling another exception.
        mdd = importlib.import_module("mcx_deposit_downloader")
        try:
            xlsx_path = mdd.download_mcx_deposit(output_dir=base_folder)
        except mdd.MCXDepositBusyError as _busy:
            # Report the ACTUAL owner from the lock file — it can genuinely be
            # "sa" (Short Allocation's own MCX Deposit button running right
            # now), but it can ALSO be "cli": a STALE lock left behind by an
            # earlier run of THIS SAME button that got force-killed via Stop
            # (taskkill never lets the lock's own cleanup code run). Always
            # blaming "Short Allocation" regardless was wrong in that second
            # case — exactly what was reported.
            if _busy.owner == "cli":
                _who = "a previous MCX Web Allocation attempt on this same button that was stopped or crashed"
            else:
                _who = "Short Allocation's MCX Deposit button"
            _p(f"⏳ MCX RSK383 download did not start — {_who} is using this same TCS BANCS session right now")
            _p(f"[FRIENDLY_MSG] {_who[0].upper()+_who[1:]} is using this same TCS BANCS session "
               f"right now — wait for it to finish, then try again{'; use Stop first if it still shows as running' if _busy.owner=='cli' else ''}.")
            raise SystemExit(1)
        except Exception as e:
            _p(f"❌ MCX RSK383 download crashed: {e}")
            _ai = translate_error("Client Prudential Limit — MCX Web Allocation download", _log_buffer)
            _p(f"[FRIENDLY_MSG] {_ai or ('MCX RSK383 download crashed: ' + str(e))}")
            raise SystemExit(1)
        if not xlsx_path:
            _p("❌ MCX RSK383 (Query Detailed Limit) download failed — see log")
            # download_mcx_deposit() already prints [MCX_DEPOSIT_AUTH_FAIL] itself on a
            # wrong password; if it returned None for some OTHER reason, give a fallback:
            _ai = translate_error("Client Prudential Limit — MCX Web Allocation download", _log_buffer)
            if _ai:
                _p(f"[FRIENDLY_MSG] {_ai}")
            raise SystemExit(1)
        _p(f"✅ Downloaded: {os.path.basename(xlsx_path)}")

    try:
        _p("🚀 Converting export to MCX_WebAllocationDeallocation.csv")
        import pandas as pd
        df_raw = pd.read_excel(xlsx_path, header=None)
        header_row_index = None
        for i in range(min(10, len(df_raw))):
            row_values = df_raw.iloc[i].astype(str).str.strip().tolist()
            if any("UCC Code" in str(x) for x in row_values):
                header_row_index = i
                break
        if header_row_index is None:
            raise RuntimeError("'UCC Code' header not found in downloaded export")
        df = pd.read_excel(xlsx_path, header=header_row_index)
        df.columns = df.columns.astype(str).str.strip()
        columns_to_remove = ["Date", "Clearing Member Name", "Trading Member Name",
                              "Client Name", "CP Code", "Currency"]
        df = df.drop(columns=[c for c in columns_to_remove if c in df.columns], errors="ignore")
        # pandas removed DataFrame.applymap in favor of .map() (deprecated since
        # 2.1, gone in the version installed here) — use whichever exists so this
        # keeps working across pandas versions.
        _clean = lambda x: str(x).replace(",", "") if pd.notna(x) else x
        if hasattr(df, "map"):
            df = df.map(_clean)
        else:
            df = df.applymap(_clean)
        df = df.reset_index(drop=True)
        df.to_csv(csv_path, index=False)
        _p(f"✅ Saved: {os.path.basename(csv_path)}")
    except Exception as e:
        _p(f"❌ Converting MCX export failed: {e}")
        _ai = translate_error("Client Prudential Limit — MCX Web Allocation convert", _log_buffer)
        _p(f"[FRIENDLY_MSG] {_ai or ('Converting MCX export failed: ' + str(e))}")
        raise SystemExit(1)


if __name__ == "__main__":
    run_cli_mcx_download()
