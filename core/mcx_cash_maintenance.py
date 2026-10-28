"""
core/mcx_cash_maintenance.py - MCX CASH MAINTENANCE, fully automatic (replaces the hand-run MCX_CASH_MAINTENANCE.py + FINAL.py).

Eight times a day (default 09:30 10:30 11:30 12:30 13:30 14:30 15:30 16:30) it produces and e-mails the MCX Cash Maintenance
shortfall reports from four input files:

    MCX 90% (RSK335)   MCX deposit (RSK383)   CM client-level collaterals (csv)   FO client-level collaterals (csv)

WHERE THE INPUTS COME FROM (per time, editable on the card):
    "ms15"  linked to the MARGIN SHORTAGE (15 minute) run: the timer WAITS until that run has finished and its backup
            folder exists, then copies the four files out of it (anything it lacks is downloaded by the "own" way).
    "own"   downloaded here: NMASS (CM / FO) and ONE MCX eclear login that clicks Export for RSK383 AND RSK335 first and
            only then waits and downloads both (sa_codes/mcx_tcs_pair_downloader.py).
Default: 09:30 11:30 14:30 16:30 follow MS15 runs 1, 3, 7, 10 (the first run that starts at/after that time); the others
download by themselves.

Folders (named by the time, not 1..8 - the times can be changed):
    <data>/MCX CASH MAINTENANCE/BACKUPFILES AUTOMATED/<dd-mm-yyyy>/<HH-MM>/   the 4 inputs + the 2 reports (as sent)
Per-time results: <data>/MCX CASH MAINTENANCE/_state/<dd-mm-yyyy>.json ; settings: _state/settings.json.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

NAME = "MCX CASH MAINTENANCE"
FOLDER_NAME = "MCX CASH MAINTENANCE"
DEFAULT_TIMES = "09:30,10:30,11:30,12:30,13:30,14:30,15:30,16:30"
DEFAULT_MS15_TIMES = ("09:30", "11:30", "14:30", "16:30")
REPO = Path(__file__).resolve().parent.parent
SUB = "BACKUPFILES AUTOMATED"
PG_PIPELINE = "mcxcash"
KEY_RE = re.compile(r"^\d{2}-\d{2}$")
UNUSED_PREFIX = "NOT_USED__"

# the six things a time's folder must hold (label, kind)
LABELS = [("MCX 90%", "client"), ("MCX deposit", "deposit"), ("CM collaterals", "cm"), ("FO collaterals", "fo"),
          ("Full report", "full"), ("Summary report", "summary")]
INPUT_KINDS = ("client", "deposit", "cm", "fo")
KIND_LABEL = {k: l for l, k in LABELS}
_CM_RX = re.compile(r"^CM_ClientLevelCollaterals_.*\.csv$", re.I)
_FO_RX = re.compile(r"^FO_ClientLevelCollaterals_.*\.csv$", re.I)

_INTEREST = re.compile(r"\[[A-Z0-9_:]+\]|✅|❌|⚠️|saved|login|logged|logout|token|session|download|wait|export|error|failed|"
                       r"=== |ms15|copied|report|email|password|file", re.I)
_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_ACTIVE: dict = {}
_LOG = collections.deque(maxlen=300)
_KIND_CACHE: dict = {}


class Busy(Exception):
    pass


# ───────────────────────────── paths ─────────────────────────────
def root() -> Path:
    v = os.getenv("MCX_CASH_DIR", "").strip()
    if v:
        p = Path(v)
    else:
        try:
            from core.scheduler import paths
            base = paths.data_root()
        except Exception:
            base = REPO / "data"
        p = base / FOLDER_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def dstr(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def parse_dstr(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%d-%m-%Y").date()


def day_dir(d: dt.date, create=False) -> Path:
    p = root() / SUB / dstr(d)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def check_key(key) -> str:
    key = str(key or "").strip().replace(":", "-")
    if not KEY_RE.match(key):
        raise ValueError(f"'{key}' is not a time folder like 09-30")
    return key


def slot_dir(d: dt.date, key: str, create=False) -> Path:
    p = day_dir(d) / check_key(key)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def _state_dir() -> Path:
    p = root() / "_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cancel_flag() -> Path:
    return _state_dir() / ".cancel"


# ───────────────────────────── times / settings ─────────────────────────────
def _parse_times(s: str):
    out = []
    for part in re.split(r"[,\s;]+", (s or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", part)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError(f"'{part}' is not a time like 13:30")
        out.append(int(m.group(1)) * 60 + int(m.group(2)))
    return out


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def key_of(m: int) -> str:
    return f"{m // 60:02d}-{m % 60:02d}"


def minutes_of(key: str) -> int:
    k = check_key(key)
    return int(k[:2]) * 60 + int(k[3:])


def _settings_path() -> Path:
    return _state_dir() / "settings.json"


def get_settings() -> dict:
    """{'times','auto','modes':{'09:30':'ms15',...},'ms15_runs':{'09:30':1}} saved from the card's Timings window."""
    try:
        s = json.loads(_settings_path().read_text(encoding="utf-8"))
    except Exception:
        s = {}
    modes = {str(k): v for k, v in (s.get("modes") or {}).items() if v in ("ms15", "own")}
    runs = {}
    for k, v in (s.get("ms15_runs") or {}).items():
        try:
            runs[str(k)] = int(v)
        except (TypeError, ValueError):
            pass
    return {"times": str(s.get("times") or "").strip(), "auto": bool(s.get("auto", True)), "modes": modes, "ms15_runs": runs}


def save_settings(times=None, auto=None, modes=None, ms15_runs=None, by="") -> dict:
    cur = get_settings()
    if times is not None:
        mins = sorted(set(_parse_times(times)))
        if not mins:
            raise ValueError("enter at least one time like 09:30")
        if len(mins) > 12:
            raise ValueError("at most 12 timings")
        cur["times"] = ",".join(hhmm(m) for m in mins)
    if auto is not None:
        cur["auto"] = bool(auto)
    if modes is not None:
        bad = [v for v in modes.values() if v not in ("ms15", "own")]
        if bad:
            raise ValueError("source must be 'ms15' or 'own'")
        cur["modes"] = {hhmm(_parse_times(k)[0]): v for k, v in modes.items()}
    if ms15_runs is not None:
        cur["ms15_runs"] = {hhmm(_parse_times(k)[0]): int(v) for k, v in ms15_runs.items() if str(v).strip()}
    cur["by"], cur["at"] = by, _iso()
    with _STATE_LOCK:
        tmp = _settings_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        os.replace(tmp, _settings_path())
    return get_settings()


def auto_enabled() -> bool:
    return get_settings()["auto"]


def slot_times() -> list:
    raw = get_settings()["times"] or os.getenv("MCX_CASH_TIMES", "") or DEFAULT_TIMES
    try:
        t = sorted(set(_parse_times(raw)))
    except ValueError:
        t = sorted(set(_parse_times(DEFAULT_TIMES)))
    return t or _parse_times(DEFAULT_TIMES)


def mode_of(m: int, settings=None) -> str:
    s = settings or get_settings()
    h = hhmm(m)
    return s["modes"].get(h) or ("ms15" if h in DEFAULT_MS15_TIMES else "own")


# ───────────────────────────── the 15-minute run it follows ─────────────────────────────
def ms15_base() -> Path:
    return Path(os.getenv("MARGIN_SHORTAGE_FOLDER", r"D:\15 minutes"))


def ms15_windows() -> list:
    """[(run number, start minute)] from MS15_RUN<i>_WINDOW in .env (e.g. 09:30-09:45)."""
    out = []
    for i in range(1, 40):
        v = os.getenv(f"MS15_RUN{i}_WINDOW", "").strip()
        m = re.match(r"(\d{1,2}):(\d{2})", v)
        if m:
            out.append((i, int(m.group(1)) * 60 + int(m.group(2))))
    return out


def ms15_run_for(m: int, settings=None):
    """Which MS15 run this time follows: the saved override, else the first run that starts at or after this time."""
    s = settings or get_settings()
    h = hhmm(m)
    if h in s["ms15_runs"]:
        return s["ms15_runs"][h]
    for n, start in ms15_windows():
        if start >= m - 2:
            return n
    return None


def ms15_folder(d: dt.date, n: int) -> Path:
    return ms15_base() / SUB / d.strftime("%Y-%m-%d") / f"{int(n):02d}"


def ms15_counter(d: dt.date) -> int:
    try:
        a, b = (ms15_base() / "run_counter.txt").read_text(encoding="utf-8", errors="replace").strip().split("|", 1)
        return int(b) if a.strip() == d.strftime("%Y-%m-%d") else 0
    except Exception:
        return 0


def ms15_state(d: dt.date, n: int) -> str:
    """'ready' (its backup folder is complete) | 'waiting' (that run has not finished) | 'gone' (it was skipped / ran elsewhere)."""
    f = ms15_folder(d, n)
    cnt = ms15_counter(d)
    has_files = f.is_dir() and any(p.is_file() for p in f.iterdir())
    if has_files and ((f / "USED_FILES.txt").exists() or cnt >= n):
        return "ready"
    if cnt >= n:
        return "gone"
    # a later run's folder exists although this one never did -> it was skipped
    try:
        later = [x for x in (ms15_base() / SUB / d.strftime("%Y-%m-%d")).iterdir() if x.name.isdigit() and int(x.name) > n]
        if later:
            return "gone"
    except OSError:
        pass
    return "waiting"


# ───────────────────────────── state ─────────────────────────────
def _state_path(d: dt.date) -> Path:
    return _state_dir() / f"{dstr(d)}.json"


def read_state(d: dt.date) -> dict:
    with _STATE_LOCK:
        try:
            return json.loads(_state_path(d).read_text(encoding="utf-8"))
        except Exception:
            return {"slots": {}}


def _write_state(d: dt.date, st: dict):
    with _STATE_LOCK:
        p = _state_path(d)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)


def _update(d: dt.date, key: str, **kw) -> dict:
    with _STATE_LOCK:
        st = read_state(d)
        rec = st["slots"].setdefault(check_key(key), {})
        rec.update(kw)
        _write_state(d, st)
        return rec


def _iso(t=None):
    return (t or dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


# ───────────────────────────── files ─────────────────────────────
def kind_of(path) -> str | None:
    """'client' | 'deposit' | 'cm' | 'fo' | 'full' | 'summary' | None - by the name for csv / reports, by the columns for xlsx."""
    p = Path(path)
    n = p.name
    if n.startswith(".") or n.endswith(".part"):
        return None
    if n.lower() == "mcx_cash_maintenance_full_file.xlsx":
        return "full"
    if n.lower() == "mcx_cash_maintenance.xlsx":
        return "summary"
    if _CM_RX.match(n):
        return "cm"
    if _FO_RX.match(n):
        return "fo"
    if n.lower().endswith(".xlsx"):
        try:
            st = p.stat()
        except OSError:
            return None
        ck = (str(p), st.st_size, int(st.st_mtime))
        if ck not in _KIND_CACHE:
            from core import mcx_cash_report as R
            _KIND_CACHE[ck] = R.classify_xlsx(str(p))
            if len(_KIND_CACHE) > 400:
                _KIND_CACHE.clear()
        return _KIND_CACHE[ck]
    return None


def _kinds_in(folder: Path) -> dict:
    """{kind: newest non-empty file path} found in a folder."""
    best = {}
    try:
        names = [f for f in os.listdir(folder) if (folder / f).is_file()]
    except OSError:
        return best
    for n in names:
        p = folder / n
        try:
            if p.stat().st_size <= 0:
                continue
        except OSError:
            continue
        k = kind_of(p)
        if k and (k not in best or p.stat().st_mtime > best[k].stat().st_mtime):
            best[k] = p
    return best


def check_files(d: dt.date, key: str) -> dict:
    """{'found': {label: filename}, 'missing': [labels], 'inputs_missing': [labels]}"""
    have = _kinds_in(slot_dir(d, key))
    found = {l: have[k].name for l, k in LABELS if k in have}
    return {"found": found, "missing": [l for l, _ in LABELS if l not in found],
            "inputs_missing": [l for l, k in LABELS if k in INPUT_KINDS and l not in found]}


def list_files(d: dt.date, key: str) -> list:
    folder = slot_dir(d, key)
    out = []
    try:
        for f in sorted(os.listdir(folder)):
            p = folder / f
            if p.is_file():
                s = p.stat()
                out.append({"name": f, "size": s.st_size, "mtime": _iso(dt.datetime.fromtimestamp(s.st_mtime))})
    except OSError:
        pass
    return out


def file_path(d: dt.date, key: str, name: str) -> Path:
    if not name or name != os.path.basename(name) or name.startswith("."):
        raise ValueError("bad file name")
    p = slot_dir(d, key) / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return p


def list_dates() -> list:
    out = []
    try:
        for n in os.listdir(root() / SUB):
            try:
                out.append(parse_dstr(n))
            except ValueError:
                continue
    except OSError:
        pass
    return sorted(out, reverse=True)


def _slot_names(base: Path) -> list:
    try:
        return sorted(n for n in os.listdir(base) if KEY_RE.match(n) and (base / n).is_dir())
    except OSError:
        return []


def _tmp_zip(prefix) -> Path:
    tmpdir = _state_dir() / "_tmp"
    tmpdir.mkdir(exist_ok=True)
    for old in tmpdir.glob("*.zip"):
        try:
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink()
        except OSError:
            pass
    fd, path = tempfile.mkstemp(suffix=".zip", prefix=prefix, dir=tmpdir)
    os.close(fd)
    return Path(path)


def zip_folder(d: dt.date, key: str = None) -> Path:
    base = day_dir(d)
    if not base.is_dir():
        raise FileNotFoundError(dstr(d))
    path = _tmp_zip(f"mcxcash_{dstr(d)}_")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for s in ([check_key(key)] if key else _slot_names(base)):
            sd = base / s
            if sd.is_dir():
                for f in sorted(os.listdir(sd)):
                    if (sd / f).is_file():
                        z.write(sd / f, f"{dstr(d)}/{s}/{f}")
    return path


def dates_between(d_from: dt.date, d_to: dt.date) -> list:
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    return [x for x in sorted(list_dates()) if d_from <= x <= d_to]


def range_listing(d_from: dt.date, d_to: dt.date) -> list:
    out = []
    for d in dates_between(d_from, d_to):
        slots = [{"slot": n, "files": list_files(d, n)} for n in _slot_names(day_dir(d))]
        out.append({"date": dstr(d), "folders": slots, "files": sum(len(s["files"]) for s in slots)})
    return out


def zip_range(d_from: dt.date, d_to: dt.date, slots=None) -> Path:
    path = _tmp_zip("mcxcash_range_")
    want = {check_key(s) for s in slots} if slots else None
    n = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for d in dates_between(d_from, d_to):
            base = day_dir(d)
            for s in _slot_names(base):
                if want is not None and s not in want:
                    continue
                for f in sorted(os.listdir(base / s)):
                    if (base / s / f).is_file():
                        z.write(base / s / f, f"{dstr(d)}/{s}/{f}")
                        n += 1
    if not n:
        os.unlink(path)
        raise FileNotFoundError("no files in that range")
    return path


# ───────────────────────────── running ─────────────────────────────
def cancel_requested() -> bool:
    return _cancel_flag().exists()


def request_cancel():
    _cancel_flag().write_text(_iso(), encoding="utf-8")
    p = _ACTIVE.get("proc")
    if p is not None:
        _kill(p)


def _kill(p):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=15)
        else:
            p.kill()
    except Exception:
        pass


def _python() -> str:
    return os.getenv("CTCL_PYTHON") or os.getenv("SA_PYTHON") or sys.executable


def _run_child(cmd, env, log, timeout_s, cancel):
    flags = 0x08000000 if os.name == "nt" else 0
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(REPO), text=True,
                         encoding="utf-8", errors="replace", creationflags=flags)
    _ACTIVE["proc"] = p
    tail = []

    def pump():
        for line in p.stdout:
            line = line.rstrip()
            if not line:
                continue
            tail.append(line)
            del tail[:-60]
            if _INTEREST.search(line):
                log(line[:300])
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t0 = time.time()
    try:
        while p.poll() is None:
            if cancel():
                log("Stopped by request.")
                _kill(p)
                break
            if time.time() - t0 > timeout_s:
                log(f"Stopped: no result after {int(timeout_s // 60)} minutes.")
                _kill(p)
                break
            time.sleep(0.5)
        p.wait(timeout=20)
    finally:
        _ACTIVE.pop("proc", None)
    t.join(timeout=5)
    return (p.returncode if p.returncode is not None else -1), tail


_AUTH = re.compile(r"\[(NMASS|MCX90|NCFE|MCX_DEPOSIT)_(AUTH_FAIL|LOCKED)\]", re.I)


def _auth_message(tail) -> str:
    for ln in tail:
        m = _AUTH.search(ln)
        if m:
            who = {"NMASS": "NMASS", "MCX90": "MCX", "NCFE": "NCDEX", "MCX_DEPOSIT": "MCX"}.get(m.group(1).upper(), m.group(1))
            if m.group(2).upper() == "LOCKED":
                return f"{who} account is LOCKED - get it unlocked, then run this time again."
            return f"{who} login failed - the password looks wrong. Update it in Credentials."
        if "[MCX_TOKEN_FAIL]" in ln:
            return "MCX: the RSA code was not accepted (stopped after the allowed tries, nothing more was sent)."
        if ln.startswith("[PAIR_FAIL]"):
            return "MCX: " + ln[len("[PAIR_FAIL]"):].strip()
    return ""


# ── the four ways to get inputs ──
def step_ms15(folder: Path, d: dt.date, key: str, log, cancel) -> dict:
    """Wait for the linked MS15 run to finish, then copy the files out of its backup folder."""
    m = minutes_of(key)
    n = ms15_run_for(m)
    if not n:
        return {"ok": False, "msg": "no 15-minute run is set for this time (MS15_RUN windows) - using own download"}
    max_wait = int(os.getenv("MCX_CASH_MS15_WAIT_MIN", "40")) * 60
    t0, noted = time.time(), 0
    while True:
        if cancel():
            return {"ok": False, "msg": "Stopped."}
        st = ms15_state(d, n)
        if st != "waiting":
            break
        if time.time() - t0 > max_wait:
            log(f"15-minute run {n} did not finish within {max_wait // 60} min - downloading myself.")
            return {"ok": False, "msg": f"15-minute run {n} did not finish in time"}
        if time.time() - noted > 120:
            log(f"Waiting for the 15-minute run {n} to finish (its backup folder is not there yet)...")
            noted = time.time()
        time.sleep(float(os.getenv("MCX_CASH_MS15_POLL_SEC", "20")))
    if st == "gone":
        log(f"15-minute run {n} was skipped / ran elsewhere - no backup to take files from.")
        return {"ok": False, "msg": f"15-minute run {n} left no backup here"}
    src = ms15_folder(d, n)
    time.sleep(2)                                             # let the backup finish writing
    got = []
    have = _kinds_in(folder)
    for f in sorted(os.listdir(src)):
        p = src / f
        if not p.is_file():
            continue
        clean = f[len(UNUSED_PREFIX):] if f.startswith(UNUSED_PREFIX) else f
        k = kind_of(p) if clean == f else None
        if clean != f:                                         # classify the real name's content from the stored copy
            tmp = folder / ("_probe_" + clean)
            try:
                shutil.copy2(p, tmp)
                k = kind_of(tmp)
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
        if k in INPUT_KINDS and k not in have:
            dst = folder / clean
            shutil.copy2(p, dst)
            have[k] = dst
            got.append(KIND_LABEL[k])
    log(f"Copied from 15-minute run {n}: {', '.join(got) or 'nothing usable'}.")
    return {"ok": True, "msg": f"from 15-minute run {n}: {', '.join(got) or 'nothing'}", "got": got}


def step_nmass(folder: Path, d: dt.date, key: str, log, cancel) -> dict:
    """CM + FO client-level collaterals by the console's own NMASS script (shared NMASS session, waits for its turn)."""
    script = os.getenv("CTCL_NMASS_SCRIPT", str(REPO / "ctcl_codes" / "NMASS_NCFE_API_MIX.PY"))
    if not os.path.isfile(script):
        return {"ok": False, "msg": f"NMASS script not found: {script}"}
    from core import dl_lock
    waited = 0
    while True:
        try:
            dl_lock.acquire("mcxcash")
            break
        except dl_lock.NmassLoginBusyError as e:
            if cancel() or waited > 300:
                return {"ok": False, "msg": f"The NMASS login stayed busy (used by '{e.owner}') - try again."}
            if waited % 30 == 0:
                log(f"NMASS login is in use by '{e.owner}' - waiting...")
            time.sleep(5)
            waited += 5
    try:
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                    "NMASS_SAVE_DIR": str(folder), "NCFE_LOGOUT_AFTER": "1"})
        env.pop("NMASS_RUN_MODE", None)
        log("NMASS download (CM / FO collaterals) ...")
        alive = threading.Event()

        def keep_fresh():
            while not alive.wait(45):
                try:
                    os.utime(dl_lock._LOCK_PATH, None)
                except OSError:
                    pass
        threading.Thread(target=keep_fresh, daemon=True).start()
        try:
            rc, tail = _run_child([_python(), script], env, log, int(os.getenv("MCX_CASH_NMASS_TIMEOUT_MIN", "20")) * 60, cancel)
        finally:
            alive.set()
    finally:
        dl_lock.release()
    have = _kinds_in(folder)
    miss = [KIND_LABEL[k] for k in ("cm", "fo") if k not in have]
    if not miss:
        return {"ok": True, "msg": "CM and FO collaterals downloaded."}
    return {"ok": False, "msg": _auth_message(tail) or ("Stopped." if cancel() else f"missing: {', '.join(miss)} (script ended with code {rc}).")}


def step_mcx(folder: Path, d: dt.date, key: str, log, cancel) -> dict:
    """MCX 90% (RSK335) + MCX deposit (RSK383): one eclear login, both exports clicked first, then both downloaded."""
    from core import tcs_session_lock as L
    log("Waiting for the MCX eclear login (shared with Live Allocation)...")
    if not L.acquire("mcxcash", wait_s=int(os.getenv("MCX_CASH_TCS_WAIT_MIN", "20")) * 60, cancel=cancel, log=log):
        return {"ok": False, "msg": "The MCX eclear login stayed busy - try again."}
    tmp = folder / "_mcx_dl"
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        stop_beat = threading.Event()

        def beat():
            while not stop_beat.wait(60):
                L.touch()
        threading.Thread(target=beat, daemon=True).start()
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "MCX_TOKEN_MODE": "auto",
                    "MCX_DEPOSIT_CALLER": "mcxcash", "PYTHONPATH": str(REPO) + os.pathsep + env.get("PYTHONPATH", "")})
        log("MCX eclear: exporting RSK383 + RSK335, then downloading both ...")
        try:
            rc, tail = _run_child([_python(), str(REPO / "sa_codes" / "mcx_tcs_pair_downloader.py"), "--out", str(tmp)], env, log,
                                  int(os.getenv("MCX_CASH_MCX_TIMEOUT_MIN", "45")) * 60, cancel)
        finally:
            stop_beat.set()
        got = _kinds_in(tmp)
        have = _kinds_in(folder)
        moved = []
        for k in ("client", "deposit"):
            if k in got and k not in have:
                shutil.move(str(got[k]), str(folder / got[k].name))
                moved.append(KIND_LABEL[k])
        have = _kinds_in(folder)
        miss = [KIND_LABEL[k] for k in ("client", "deposit") if k not in have]
        if not miss:
            return {"ok": True, "msg": "MCX 90% and MCX deposit downloaded."}
        return {"ok": False, "msg": _auth_message(tail) or ("Stopped." if cancel() else f"missing: {', '.join(miss)} (code {rc}).")}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        L.release("mcxcash")


def step_report(folder: Path, d: dt.date, key: str, log, cancel) -> dict:
    """Calculate the two reports from the four inputs and lock them with the file password."""
    from core import mcx_cash_report as R
    have = _kinds_in(folder)
    miss = [KIND_LABEL[k] for k in INPUT_KINDS if k not in have]
    if miss:
        return {"ok": False, "msg": "inputs missing: " + ", ".join(miss)}
    work = Path(tempfile.mkdtemp(prefix="mcxcash_", dir=_state_dir()))
    try:
        res = R.build_reports(str(have["client"]), str(have["deposit"]), str(have["cm"]), str(have["fo"]), str(work), log)
        pwd = os.getenv("MCX_CASH_FILE_PASSWORD", R.DEFAULT_PASSWORD)
        for p in (res["full"], res["summary"]):
            R.protect(p, pwd, log)
        for p in (res["full"], res["summary"]):
            os.replace(p, folder / os.path.basename(p))
        log(f"Reports ready ({res['rows']} clients, total Cash_Maintenance {res['total']:,}).")
        return {"ok": True, "msg": f"{res['rows']} clients, total Cash_Maintenance {res['total']:,}", "rows": res["rows"], "total": res["total"]}
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "msg": f"{type(e).__name__}: {e}"[:300]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def step_email(folder: Path, d: dt.date, key: str, log, cancel) -> dict:
    """Send the FULL file and the SUMMARY file (each only once, even if one of the two failed before)."""
    from core import mcx_cash_report as R
    rec = read_state(d)["slots"].get(key, {})
    mailed = dict(rec.get("mailed") or {})
    have = _kinds_in(folder)
    label = R.run_label()
    errs = []
    for kind in ("full", "summary"):
        if mailed.get(kind):
            continue
        if kind not in have:
            errs.append(f"{kind} report missing")
            continue
        to, cc = R.recipients(kind)
        try:
            R.send_mail(to, cc, f"MCX Cash Maintenance Shortfall Report – {label}", R.mail_body(label), str(have[kind]), log)
            mailed[kind] = _iso()
            _update(d, key, mailed=dict(mailed))
        except Exception as e:                                # noqa: BLE001
            errs.append(f"{kind}: {e}")
            log(f"Email of the {kind} report FAILED - {e}")
    if errs:
        return {"ok": False, "msg": " | ".join(errs)[:300]}
    return {"ok": True, "msg": "Both reports e-mailed."}


STEPS = {"ms15": step_ms15, "nmass": step_nmass, "mcx": step_mcx, "report": step_report, "email": step_email}   # tests replace these
STEP_LABEL = {"ms15": "15-minute link", "nmass": "NMASS", "mcx": "MCX eclear", "report": "Report", "email": "E-mail"}


# ───────────────────────────── logs / archive / upload ─────────────────────────────
def log_dir() -> Path:
    v = os.getenv("MCX_CASH_LOG_DIR", "").strip()
    try:
        from core.pipeline_box import LOG_BASE
        p = Path(v) if v else Path(LOG_BASE) / "mcxcash"
    except Exception:
        p = Path(v) if v else root() / "_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _open_run_log(key, trigger="", by="", force=False):
    try:
        now = dt.datetime.now()
        f = open(log_dir() / f"mcxcash_t{key.replace('-', '')}__{now:%Y%m%d_%H%M%S}.log", "w", encoding="utf-8")
        f.write(f"===== MCX CASH MAINTENANCE - {key.replace('-', ':')} - {now:%d-%m-%Y %H:%M:%S} =====\n"
                f"started by: {by or trigger or 'timer'} ({trigger or 'auto'}){' - run again (force)' if force else ''}\n\n")
        f.flush()
        return f
    except Exception:
        return None


def _stash_files(d, key, kinds, log=print):
    """'Run again': move the old files of these kinds aside (nothing is deleted)."""
    have = _kinds_in(slot_dir(d, key))
    names = [have[k] for k in kinds if k in have]
    if not names:
        return
    dest = _state_dir() / "_replaced" / dstr(d) / key / dt.datetime.now().strftime("%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        for old in (_state_dir() / "_replaced").iterdir():
            try:
                if (d - parse_dstr(old.name)).days > 7:
                    shutil.rmtree(old, ignore_errors=True)
            except ValueError:
                pass
    except OSError:
        pass
    for p in names:
        try:
            os.replace(p, dest / p.name)
        except OSError:
            pass
    log(f"Old copies moved aside ({len(names)} file(s)).")


def archive_slot(d, key, log=print) -> int:
    if os.getenv("USE_POSTGRES", "false").strip().lower() != "true":
        return 0
    try:
        from core.db_postgres import save_run_file
    except Exception as e:
        log(f"Postgres archive skipped: {e}")
        return 0
    rec = read_state(d)["slots"].get(key, {})
    done, n = set(rec.get("archived") or []), 0
    for f in list_files(d, key):
        k = f"{f['name']}|{f['size']}"
        if k in done:
            continue
        try:
            save_run_file(PG_PIPELINE, "backup", f["name"], file_path(d, key, f["name"]).read_bytes(),
                          tag=f"{dstr(d)}/{key}", run_date=d.isoformat())
            done.add(k)
            n += 1
        except Exception as e:                                # noqa: BLE001
            log(f"Postgres archive failed for {f['name']}: {type(e).__name__}: {e}")
    if n:
        _update(d, key, archived=sorted(done))
        log(f"{n} file(s) archived to PostgreSQL.")
    return n


def accept_upload(d, key: str, name: str, data: bytes, by="", log=print) -> dict:
    """Put one file you got by hand into this time's folder. Accepted: CM_/FO_ClientLevelCollaterals_*.csv, an MCX 90% or
    MCX deposit .xlsx (recognised by its columns), or the two report files."""
    name = os.path.basename(name or "")
    if not data:
        raise ValueError(f"'{name}' is empty")
    folder = slot_dir(d, key, create=True)
    tmp = folder / (".upload_" + name)
    tmp.write_bytes(data)
    try:
        k = None
        if _CM_RX.match(name):
            k = "cm"
        elif _FO_RX.match(name):
            k = "fo"
        elif name.lower() == "mcx_cash_maintenance_full_file.xlsx":
            k = "full"
        elif name.lower() == "mcx_cash_maintenance.xlsx":
            k = "summary"
        elif name.lower().endswith(".xlsx"):
            from core import mcx_cash_report as R
            k = R.classify_xlsx(str(tmp))                     # by its columns - the portal's file names change every time
        if not k:
            raise ValueError(f"'{name}' is not a file this process uses (CM_/FO_ClientLevelCollaterals_*.csv, the MCX 90% or MCX "
                             f"deposit .xlsx, or the two report files)")
        _stash_files(d, key, [k], log)
        os.replace(tmp, folder / name)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return {"name": name, "label": KIND_LABEL[k]}


def finish_upload(d, key: str, by="", log=print) -> dict:
    chk = check_files(d, key)
    if not chk["inputs_missing"]:
        _update(d, key, files=chk["found"], inputs_by_hand=True)
    archive_slot(d, key, log)
    return {"inputs_complete": not chk["inputs_missing"], "missing": chk["missing"], "found": chk["found"]}


# ───────────────────────────── run one time ─────────────────────────────
def is_running() -> bool:
    return _RUN_LOCK.locked()


def run_slot(key, trigger="schedule", by="", log=print, cancelled=lambda: False, force=False) -> dict:
    """Produce and mail the reports for one time. `force` starts again from scratch (old files are moved aside).
    Only what is still missing is fetched, so a retry never repeats finished work. Raises Busy if another run is going."""
    key = check_key(key)
    m = minutes_of(key)
    _out, fh = log, {}

    def log(msg, _k=key):                                      # noqa: F811
        _LOG.append((dt.datetime.now().strftime("%H:%M:%S"), _k, str(msg)))
        if fh.get("f"):
            try:
                fh["f"].write(f"{dt.datetime.now():%H:%M:%S}  {msg}\n")
                fh["f"].flush()
            except Exception:
                pass
        _out(msg)
    d = dt.date.today()
    prev = read_state(d)["slots"].get(key, {})
    if prev.get("skipped") and trigger != "manual":
        return prev
    if not _RUN_LOCK.acquire(blocking=False):
        a = _ACTIVE.get("slot")
        raise Busy(f"{a.replace('-', ':')} is still running" if a else "Another run is going")
    try:
        try:
            _cancel_flag().unlink()
        except OSError:
            pass
        _ACTIVE.update({"slot": key, "date": dstr(d), "started": _iso()})
        folder = slot_dir(d, key, create=True)
        fh["f"] = _open_run_log(key, trigger, by, force)
        settings = get_settings()
        mode = mode_of(m, settings)
        if force:
            _stash_files(d, key, ("client", "deposit", "cm", "fo", "full", "summary"), log)
            _update(d, key, mailed={})
        _update(d, key, status="running", started=_iso(), ended="", trigger=trigger, by=by, msg="", steps={}, skipped=False,
                redo=False, override_done=False, source=mode)
        stop = lambda: bool(cancelled()) or cancel_requested()  # noqa: E731
        steps = {}

        def do(name):
            try:
                steps[name] = STEPS[name](folder, d, key, log, stop)
            except Exception as e:                             # noqa: BLE001 - one step failing must not kill the timer
                steps[name] = {"ok": False, "msg": f"{type(e).__name__}: {e}"[:300]}
                log(f"{STEP_LABEL[name]} FAILED - {steps[name]['msg']}")
            _update(d, key, steps=dict(steps))

        def missing_inputs():
            return [k for k in INPUT_KINDS if k not in _kinds_in(folder)]
        log(f"=== {key.replace('-', ':')} - inputs by: {'15-minute run' if mode == 'ms15' else 'own download'}")
        if missing_inputs() and mode == "ms15" and not stop():
            do("ms15")
        if any(k in missing_inputs() for k in ("cm", "fo")) and not stop():
            do("nmass")
        if any(k in missing_inputs() for k in ("client", "deposit")) and not stop():
            do("mcx")
        if not missing_inputs() and not stop():
            if len({"full", "summary"} - set(_kinds_in(folder))):
                do("report")
            rec = read_state(d)["slots"].get(key, {})
            mailed = rec.get("mailed") or {}
            if not (mailed.get("full") and mailed.get("summary")) and not stop():
                do("email")
        chk = check_files(d, key)
        rec = read_state(d)["slots"].get(key, {})
        mailed = rec.get("mailed") or {}
        if rec.get("skipped"):
            status, msg = "skipped", "Skipped by user."
        elif stop() and chk["missing"]:
            status, msg = "cancelled", "Stopped before it finished."
        elif not chk["missing"] and mailed.get("full") and mailed.get("summary"):
            status = "done"
            rp = (steps.get("report") or {}).get("msg")
            msg = f"Reports sent - {rp}" if rp else "Reports sent."
        else:
            bad = [f"{STEP_LABEL[k]}: {v.get('msg')}" for k, v in steps.items() if not v.get("ok")]
            status, msg = "failed", " | ".join(bad) or ("Missing: " + ", ".join(chk["missing"]))
        log(f"=== {key.replace('-', ':')}: {status.upper()} - {msg}")
        try:
            archive_slot(d, key, log)
        except Exception:                                      # noqa: BLE001
            pass
        return _update(d, key, status=status, ended=_iso(), msg=msg, files=chk["found"])
    finally:
        if fh.get("f"):
            try:
                fh["f"].close()
            except Exception:
                pass
        _ACTIVE.clear()
        _RUN_LOCK.release()


def skip_slot(key, by="") -> dict:
    d = dt.date.today()
    key = check_key(key)
    running = _RUN_LOCK.locked() and _ACTIVE.get("slot") == key
    rec = _update(d, key, skipped=True, skipped_by=by, skipped_at=_iso())
    if running:
        request_cancel()
        return rec
    return _update(d, key, status="skipped", msg="Skipped by user.", ended=_iso())


def unskip_slot(key) -> dict:
    d = dt.date.today()
    key = check_key(key)
    rec = read_state(d)["slots"].get(key, {})
    if rec.get("status") == "skipped":
        return _update(d, key, skipped=False, status="", msg="")
    return _update(d, key, skipped=False)


def stop_running(by="") -> bool:
    if not _RUN_LOCK.locked():
        return False
    request_cancel()
    return True


def mark_undone(key, by="") -> dict:
    """Override: treat this time as NOT done - at its time (now, if passed) everything is produced and mailed again."""
    return _update(dt.date.today(), key, status="", skipped=False, redo=True, override_done=False, auto_attempts=0, last_auto="",
                   msg=f"Marked not done by {by or 'a user'} - will run again.", steps={}, mailed={}, marked_by=by, marked_at=_iso())


def mark_done(key, by="") -> dict:
    return _update(dt.date.today(), key, status="done", skipped=False, redo=False, override_done=True,
                   msg=f"Marked done by {by or 'a user'} (override).", marked_by=by, marked_at=_iso())


# ───────────────────────────── the timer ─────────────────────────────
_TICK = {"thread": None, "stop": threading.Event()}


def _trading_day(d: dt.date) -> bool:
    try:
        from core.scheduler import helpers as H
        return bool(H.is_trading_day(d))
    except Exception:
        return d.weekday() < 5


def _grace_min() -> int:
    try:
        return max(5, int(os.getenv("MCX_CASH_GRACE_MIN", "60")))
    except ValueError:
        return 60


def _retry_cfg():
    try:
        n = max(0, int(os.getenv("MCX_CASH_RETRIES", "2")))
    except ValueError:
        n = 2
    try:
        gap = max(1, int(os.getenv("MCX_CASH_RETRY_GAP_MIN", "5")))
    except ValueError:
        gap = 5
    return n, gap


def _complete(d, key, rec) -> bool:
    mailed = rec.get("mailed") or {}
    return not check_files(d, key)["missing"] and bool(mailed.get("full") and mailed.get("summary"))


def due_slot(now: dt.datetime = None):
    """-> (key, force) the timer should start now, or None. Same rules as Live Allocation: a time is due when it has come, is
    not finished / skipped, and is inside its window (until the next timing, at most MCX_CASH_GRACE_MIN)."""
    now = now or dt.datetime.now()
    d = now.date()
    if not _trading_day(d):
        return None
    times = slot_times()
    st = read_state(d)["slots"]
    cur = now.hour * 60 + now.minute
    retries, gap = _retry_cfg()
    for i, t in enumerate(times):
        if t > cur:
            break
        key = key_of(t)
        rec = st.get(key, {})
        if rec.get("skipped") or rec.get("override_done"):
            continue
        redo = bool(rec.get("redo"))
        if not redo:
            if _complete(d, key, rec):
                continue
            nxt = times[i + 1] if i + 1 < len(times) else 24 * 60
            if cur >= min(nxt, t + _grace_min()):
                continue
            n = int(rec.get("auto_attempts") or 0)
            if n:
                if n > retries:
                    continue
                try:
                    if (now - dt.datetime.strptime(rec.get("last_auto") or "", "%Y-%m-%d %H:%M:%S")).total_seconds() < gap * 60:
                        continue
                except ValueError:
                    pass
        return key, redo
    return None


def tick(now: dt.datetime = None, runner=None):
    if not auto_enabled() or _RUN_LOCK.locked():
        return None
    now = now or dt.datetime.now()
    hit = due_slot(now)
    if not hit:
        return None
    key, force = hit
    d = now.date()
    rec = read_state(d)["slots"].get(key, {})
    _update(d, key, auto_attempts=int(rec.get("auto_attempts") or 0) + 1, last_auto=now.strftime("%Y-%m-%d %H:%M:%S"))

    def go():
        try:
            run_slot(key, trigger="auto", by="timer", force=force)
        except Busy:
            pass
        except Exception as e:                                 # noqa: BLE001
            print(f"[mcx-cash] timer run of {key} failed: {type(e).__name__}: {e}", flush=True)
    if runner:
        runner(key, force)
    else:
        threading.Thread(target=go, name=f"mcx-cash-auto-{key}", daemon=True).start()
    return key


def start_ticker(interval_s: int = 20):
    if _TICK["thread"] and _TICK["thread"].is_alive():
        return False
    _TICK["stop"].clear()

    def loop():
        print("[mcx-cash] timer started - reports are produced and mailed at their times by themselves.", flush=True)
        while not _TICK["stop"].wait(interval_s):
            try:
                tick()
            except Exception as e:                             # noqa: BLE001
                print(f"[mcx-cash] timer error: {type(e).__name__}: {e}", flush=True)
    th = threading.Thread(target=loop, name="mcx-cash-timer", daemon=True)
    _TICK["thread"] = th
    th.start()
    return True


def stop_ticker():
    _TICK["stop"].set()


# ───────────────────────────── status for the card ─────────────────────────────
def status(d: dt.date = None, now: dt.datetime = None) -> dict:
    now = now or dt.datetime.now()
    d = d or now.date()
    today = d == now.date()
    times = slot_times()
    settings = get_settings()
    st = read_state(d)["slots"]
    cur = now.hour * 60 + now.minute
    live = _ACTIVE.get("slot") if (_RUN_LOCK.locked() and _ACTIVE.get("date") == dstr(d)) else None
    slots, count = [], {"done": 0, "failed": 0, "running": 0, "skipped": 0, "missed": 0, "upcoming": 0, "due": 0, "partial": 0}
    for t in times:
        key = key_of(t)
        rec = dict(st.get(key, {}))
        chk = check_files(d, key)
        mailed = rec.get("mailed") or {}
        complete = _complete(d, key, rec)
        s = rec.get("status") or ""
        if live == key:
            state = "running"
        elif rec.get("override_done"):
            state = "done"
        elif rec.get("redo"):
            state = "due" if (today and cur >= t) else "upcoming"
        elif s == "running":
            state = "failed"
            rec["msg"] = "Interrupted (the server restarted while it was running)."
        elif s == "skipped" or rec.get("skipped"):
            state = "skipped"
        elif complete:
            state = "done"
        elif s in ("failed", "cancelled"):
            state = "failed"
        elif chk["found"]:
            state = "partial" if not today or cur >= t else "upcoming"
        elif not today:
            state = "missed"
        elif cur < t:
            state = "upcoming"
        elif cur < t + 5:
            state = "due"
        else:
            state = "missed"
        count[state] = count.get(state, 0) + 1
        mode = mode_of(t, settings)
        n = ms15_run_for(t, settings) if mode == "ms15" else None
        slots.append({"slot": key, "time": hhmm(t), "state": state, "msg": rec.get("msg", ""), "started": rec.get("started", ""),
                      "ended": rec.get("ended", ""), "by": rec.get("by", ""), "trigger": rec.get("trigger", ""),
                      "steps": rec.get("steps", {}), "attempts": int(rec.get("auto_attempts") or 0), "redo": bool(rec.get("redo")),
                      "override": bool(rec.get("override_done")), "mode": mode, "ms15_run": n,
                      "found": chk["found"], "missing": chk["missing"], "files": len(chk["found"]), "total": len(LABELS),
                      "mailed": {"full": bool(mailed.get("full")), "summary": bool(mailed.get("summary"))},
                      "folder": str(slot_dir(d, key))})
    nxt = next((s for s in slots if s["state"] in ("due", "upcoming")), None)
    lg = [{"t": t, "slot": sl, "msg": m} for t, sl, m in list(_LOG)[-40:]]
    return {"log": lg, "date": dstr(d), "today": today, "now": now.strftime("%H:%M"), "running": live, "slots": slots,
            "auto": settings["auto"], "times": ",".join(hhmm(x) for x in times), "trading_day": _trading_day(d),
            "grace_min": _grace_min(), "retries": _retry_cfg()[0], "counts": count,
            "next": ({"slot": nxt["slot"], "time": nxt["time"]} if nxt else None),
            "ms15_runs": [{"run": n, "start": hhmm(s)} for n, s in ms15_windows()],
            "root": str(root()), "dates": [dstr(x) for x in list_dates()[:30]]}
