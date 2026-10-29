"""
core/bk_merge.py - FAST "Get files" client filter + merge, run as its OWN low-priority process.

Why this exists
---------------
The old merge (pipeline_box.pb_backup_download_merged) ran inside the web server. For a big Excel
backup (e.g. "Update Limits_*.xlsx": 120 MB, 180,000 rows x 239 columns) it:
  * parsed the sheet with openpyxl (slow, pure Python, holds the server's one Python lock), and
  * when the client column name was not in its short list ("Entity" was not), it parsed the WHOLE
    sheet again for every candidate name (up to 8 full parses per file), 112 files in a row.
Every other request (status polls, login, other boxes) then waited behind it = the "hang".

What this does instead
----------------------
  * runs in a separate process (python core/bk_merge.py <job_dir>) at BELOW-NORMAL priority, so the
    console keeps its CPU; one merge job at a time (enforced by the API);
  * reads each .xlsx ONCE with the Rust calamine reader (polars + fastexcel), then keeps a compressed
    Parquet copy in the cache folder - the next lookup of the same file takes a fraction of a second;
  * handles several files at the same time (worker count picked from free memory);
  * writes progress.json after every file so the screen can show "file 37 of 112", time and an estimate.

The module imports only the standard library at the top; polars / pandas are imported lazily so the
web server can import it cheaply (cache_path / plan are used from api.py).
"""
import hashlib
import json
import os
import re
import sys
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# the shared "client / account column" names, plus Entity (the Update Limits files use it)
DEFAULT_CANDIDATES = ["AccountID", "ACCOUNTID", "Account ID", "Client Code",
                      "Client/CP Code", "UCC Code", "CLIENT CODE", "Entity"]

HEADER_SCAN_ROWS = 30


def _norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _set_low_priority():
    """Below-normal CPU priority: this process must never starve the web server."""
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000)     # BELOW_NORMAL_PRIORITY_CLASS
        else:
            os.nice(10)
    except Exception:
        pass


# ───────────────────────────── cache (one compressed copy per backup file) ─────────────────────────────
def cache_dir():
    d = os.environ.get("RMS_BKCACHE_DIR") or os.path.join(REPO, ".bkcache")
    return d


def _cache_key(path):
    ap = os.path.abspath(path)
    st = os.stat(ap)
    h = hashlib.sha1(f"{ap.lower()}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")).hexdigest()[:20]
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(ap))[:40]
    return f"{base}_{h}"


def cache_path(path):
    """Where the Parquet copy of this file lives (it may not exist yet). Changes when the file changes."""
    return os.path.join(cache_dir(), _cache_key(path) + ".parquet")


def _nocol_path(path, cands):
    sig = hashlib.sha1("|".join(_norm(c) for c in cands).encode("utf-8")).hexdigest()[:8]
    return os.path.join(cache_dir(), f"{_cache_key(path)}.nocol_{sig}")


def is_cached(path, cands=None):
    try:
        if os.path.isfile(cache_path(path)):
            return True
        return bool(cands) and os.path.isfile(_nocol_path(path, cands))
    except OSError:
        return False


# ───────────────────────────── reading ─────────────────────────────
def _find_acct(names, cands):
    for c in cands:
        for n in names:
            if _norm(n) == _norm(c):
                return n
    return None


def _code_expr(acct, codes):
    import polars as pl
    return pl.col(acct).cast(pl.Utf8).str.strip_chars().str.to_uppercase().is_in(list(codes))


def _read_raw_xlsx(path):
    """Whole sheet as TEXT, no header applied. Fast Rust reader first; pandas/openpyxl only as a last resort."""
    import polars as pl
    try:
        try:
            return pl.read_excel(path, engine="calamine", has_header=False,
                                 infer_schema_length=0, raise_if_empty=False)
        except TypeError:
            return pl.read_excel(path, engine="calamine", has_header=False, infer_schema_length=0)
    except Exception as e_fast:
        print(f"[bk_merge] fast reader failed on {os.path.basename(path)} ({type(e_fast).__name__}: {e_fast}); "
              f"using the slow reader", flush=True)
        import pandas as pd
        pdf = pd.read_excel(path, header=None, dtype=str)
        pdf = pdf.where(pdf.notna(), None)
        return pl.DataFrame({f"column_{i + 1}": pdf.iloc[:, i].tolist() for i in range(pdf.shape[1])},
                            schema={f"column_{i + 1}": pl.Utf8 for i in range(pdf.shape[1])})


def _table_from_raw(raw, cands):
    """Finds the header row (first HEADER_SCAN_ROWS rows, a row that holds a known client-column name),
    applies it, returns (table, account_column_name) or (None, None)."""
    import polars as pl
    candset = {_norm(c) for c in cands}
    hrow = None
    for i in range(min(HEADER_SCAN_ROWS, raw.height)):
        if any(v is not None and _norm(v) in candset for v in raw.row(i)):
            hrow = i
            break
    if hrow is None:
        return None, None
    names, seen = [], {}
    for j, v in enumerate(raw.row(hrow)):
        nm = str(v).strip() if v is not None and str(v).strip() else f"col_{j + 1}"
        if nm in seen:
            seen[nm] += 1
            nm = f"{nm}_{seen[nm]}"
        else:
            seen[nm] = 0
        names.append(nm)
    body = raw.slice(hrow + 1)
    body.columns = names
    body = body.with_columns([pl.col(c).cast(pl.Utf8) for c in body.columns])
    return body, _find_acct(names, cands)


def _read_csv_table(path):
    import polars as pl
    for kw in ({}, {"encoding": "utf8-lossy"}):
        try:
            try:
                return pl.read_csv(path, infer_schema=False, **kw)
            except TypeError:
                return pl.read_csv(path, infer_schema_length=0, **kw)
        except Exception:
            continue
    import pandas as pd
    pdf = pd.read_csv(path, dtype=str, encoding="latin-1")
    pdf = pdf.where(pdf.notna(), None)
    return pl.DataFrame({str(c): pdf[c].tolist() for c in pdf.columns},
                        schema={str(c): pl.Utf8 for c in pdf.columns})


def _special_df(fname, path, codes):
    """The two plain-text formats that are not normal tables (same rules as the old filter).
    Returns (handled, df_or_None)."""
    import polars as pl
    low = fname.lower()
    if low == "cli_prudential_limit.txt":
        delim = ","
    elif low.startswith("kambala_omny_depo_adhoc_all_level.txt_"):
        delim = "|"
    else:
        return False, None
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return True, None
    header, rows = lines[0], lines[1:]
    kept = [ln for ln in rows if ln.split(delim, 1)[0].strip().upper() in codes]
    if not kept:
        return True, pl.DataFrame()
    hdr = header.split(delim)
    out = []
    for ln in kept:
        if ln == "":
            continue
        parts = ln.split(delim)
        if len(parts) < len(hdr):
            parts = parts + [""] * (len(hdr) - len(parts))
        elif len(parts) > len(hdr):
            parts = parts[:len(hdr)]
        out.append(parts)
    if not out:
        return True, pl.DataFrame()
    return True, pl.DataFrame({h: [r[i] for r in out] for i, h in enumerate(hdr)},
                              schema={h: pl.Utf8 for h in hdr})


# ───────────────────────────── tidy numbers (only on the few rows we keep) ─────────────────────────────
_FLOATY = re.compile(r"^-?\d+\.\d{9,}$")


def _tidy_value(v):
    if v is not None and _FLOATY.match(v):
        try:
            s = repr(float(v))
            if "e" in s or "E" in s:
                return v
            return s[:-2] if s.endswith(".0") else s
        except Exception:
            return v
    return v


def tidy_numbers(df):
    """Excel stores 92612705.43 as 92612705.430000007 in the file XML; show it the way Excel does."""
    import polars as pl
    if df is None or df.height == 0:
        return df
    cols = []
    for c in df.columns:
        if df[c].dtype == pl.Utf8:
            cols.append(df[c].map_elements(_tidy_value, return_dtype=pl.Utf8))
    return df.with_columns(cols) if cols else df


# ───────────────────────────── one file ─────────────────────────────
def filter_one(path, codes, cands=None, use_cache=True):
    """Rows of ONE backup file that belong to `codes`.
    Returns (fname, df_or_None, status, how)
      status: 'ok' | 'no_data' (file fine, nothing for these codes) | 'no_column' | 'error'
      how:    'hit' (Parquet cache) | 'miss' (read the file now) | '' """
    import polars as pl
    cands = list(cands or DEFAULT_CANDIDATES)
    codes = {c.strip().upper() for c in codes if c and c.strip()}
    fname = os.path.basename(path)
    low = fname.lower()
    if not codes:
        return fname, None, "error", ""
    try:
        handled, df = _special_df(fname, path, codes)
        if handled:
            if df is None:
                return fname, None, "no_column", ""
            return fname, (tidy_numbers(df) if df.height else None), ("ok" if df.height else "no_data"), ""

        if low.endswith((".csv", ".txt")):
            table = _read_csv_table(path)
            acct = _find_acct(table.columns, cands)
            if not acct:
                return fname, None, "no_column", ""
            df = table.filter(_code_expr(acct, codes))
            return fname, (tidy_numbers(df) if df.height else None), ("ok" if df.height else "no_data"), ""

        if not low.endswith(".xlsx"):
            return fname, None, "no_column", ""

        # ── .xlsx ──
        pq = cache_path(path)

        def from_cache():
            """(done, result) - result is the finished return value when done."""
            if os.path.isfile(pq):
                lf = pl.scan_parquet(pq)
                acct_ = _find_acct(lf.collect_schema().names(), cands)
                if acct_:
                    d_ = lf.filter(_code_expr(acct_, codes)).collect()
                    return True, (fname, (tidy_numbers(d_) if d_.height else None),
                                  ("ok" if d_.height else "no_data"), "hit")
            if os.path.isfile(_nocol_path(path, cands)):
                return True, (fname, None, "no_column", "hit")
            return False, None

        if not use_cache:
            raw = _read_raw_xlsx(path)
            table, acct = _table_from_raw(raw, cands)
            del raw
            if table is None or not acct:
                return fname, None, "no_column", "miss"
            df = table.filter(_code_expr(acct, codes))
            return fname, (tidy_numbers(df) if df.height else None), ("ok" if df.height else "no_data"), "miss"

        done, res = from_cache()
        if done:
            return res
        # Not cached yet. Only ONE process reads a given file; anybody else who needs it waits for that copy.
        flock = pq + ".lock"
        waited_until = time.time() + 1800
        have_lock = False
        while True:
            if _try_lock(flock):
                have_lock = True
                break
            done, res = from_cache()
            if done:
                return res
            if time.time() > waited_until:
                break
            time.sleep(1.0)
        slot = None
        try:
            done, res = from_cache()          # somebody may have finished while we waited
            if done:
                return res
            slot = _acquire_slot()            # wait here (not fail) if the machine's memory slots are busy
            raw = _read_raw_xlsx(path)
            table, acct = _table_from_raw(raw, cands)
            del raw
            if table is None or not acct:
                _touch(_nocol_path(path, cands))
                return fname, None, "no_column", "miss"
            _write_parquet(table, pq)
            df = table.filter(_code_expr(acct, codes))
            return fname, (tidy_numbers(df) if df.height else None), ("ok" if df.height else "no_data"), "miss"
        finally:
            if slot:
                _unlock(slot)
            if have_lock:
                _unlock(flock)
    except Exception as e:                       # noqa
        print(f"[bk_merge] ERROR on {fname}: {type(e).__name__}: {e}", flush=True)
        return fname, None, "error", ""


def _touch(p):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("")
    except OSError:
        pass


# ───────────────────────────── sharing the machine fairly between people ─────────────────────────────
# Every search is its own job/process, so nobody waits for anybody else's search. The ONLY shared limit is
# memory: a first read of a 120 MB xlsx peaks near 3 GB. So there is a small, fixed number of "first-read
# slots" for the whole machine (lock files in the cache folder, safe across processes). A person who needs a
# first read when all slots are busy simply waits for a free one - never refused, never crashes anyone.
# Files that are already cached need no slot. Two people needing the same uncached file share ONE read.
def cold_slots():
    try:
        n = int(os.environ.get("RMS_COLD_SLOTS", "0") or 0)
    except ValueError:
        n = 0
    if n > 0:
        return n
    try:
        import psutil
        per_mb = float(os.environ.get("RMS_COLD_SLOT_MB", "3500") or 3500)
        return max(1, min(4, int((psutil.virtual_memory().total / (1024 * 1024)) * 0.4 // per_mb)))
    except Exception:
        return 2


def _lock_is_stale(p, max_age=1800):
    try:
        if time.time() - os.path.getmtime(p) > max_age:
            return True
        pid = int(open(p).read().strip() or 0)
        if pid:
            try:
                import psutil
                return not psutil.pid_exists(pid)
            except Exception:
                return False
    except (OSError, ValueError):
        return False
    return False


def _try_lock(p):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        if _lock_is_stale(p):
            try:
                os.remove(p)
            except OSError:
                pass
        return False
    except OSError:
        return False


def _unlock(p):
    try:
        os.remove(p)
    except OSError:
        pass


def _acquire_slot(poll=1.0, timeout=3600):
    """Waits for a free first-read slot (see above). Returns the lock path to release."""
    n = cold_slots()
    end = time.time() + timeout
    while time.time() < end:
        for k in range(n):
            p = os.path.join(cache_dir(), f"slot_{k}.lock")
            if _try_lock(p):
                return p
        time.sleep(poll)
    return None            # gave up waiting: read anyway rather than hang for ever


def _write_parquet(table, pq):
    try:
        os.makedirs(os.path.dirname(pq), exist_ok=True)
        tmp = pq + f".{os.getpid()}.tmp"
        table.write_parquet(tmp, compression="zstd")
        os.replace(tmp, pq)
    except Exception as e:                       # disk full / no rights: not fatal, just no cache
        print(f"[bk_merge] could not write cache {os.path.basename(pq)}: {e}", flush=True)
        try:
            os.remove(tmp)
        except Exception:
            pass


# ───────────────────────────── planning (used by the API before it starts a job) ─────────────────────────────
def plan(paths, cands=None):
    """How many of these files still need the one-time read, and how many parallel workers the free memory allows."""
    cands = list(cands or DEFAULT_CANDIDATES)
    cold, biggest = 0, 0.0
    for p in paths:
        try:
            if p.lower().endswith(".xlsx") and not is_cached(p, cands):
                cold += 1
                biggest = max(biggest, os.path.getsize(p) / (1024 * 1024))
        except OSError:
            pass
    cpu = os.cpu_count() or 2
    try:
        env_w = int(os.environ.get("RMS_MERGE_WORKERS", "0") or 0)
    except ValueError:
        env_w = 0
    # Memory is NOT decided here any more: the machine-wide "first-read slots" (cold_slots) cap how many
    # big first reads run at once across ALL people, so a job can safely use a few workers for cached files.
    workers = env_w if env_w > 0 else max(1, min(4, cpu - 2))
    return {"workers": workers, "cold": cold, "slots": cold_slots(), "biggest_mb": round(biggest)}


# ───────────────────────────── the job (runs in its own process) ─────────────────────────────
def _write_json(path, obj):
    tmp = path + ".tmp"
    for _ in range(5):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f)
            os.replace(tmp, path)
            return
        except OSError:
            time.sleep(0.05)


def _task(args):
    gi, ii, path, codes, cands, use_cache = args
    t0 = time.time()
    fname, df, status, how = filter_one(path, codes, cands, use_cache=use_cache)
    return {"g": gi, "i": ii, "fname": fname, "df": df, "status": status, "how": how,
            "secs": round(time.time() - t0, 2)}


def run_job(job_dir):
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import polars as pl
    _set_low_priority()
    job = json.load(open(os.path.join(job_dir, "job.json"), encoding="utf-8"))
    codes = job["codes"]
    cands = job.get("cands") or DEFAULT_CANDIDATES
    groups = job["groups"]
    workers = max(1, int(job.get("workers") or 1))
    total = sum(len(g["items"]) for g in groups)
    prog_path = os.path.join(job_dir, "progress.json")
    started = time.time()
    prog = {"state": "running", "done": 0, "total": total, "started": started, "updated": started,
            "current": "", "workers": workers, "cold_total": job.get("cold", 0), "cold_done": 0,
            "hits": 0, "labels": [g["label"] for g in groups], "error": ""}
    _write_json(prog_path, prog)
    print(f"[bk_merge] job start: {total} file(s), {workers} worker(s), cache={cache_dir()}", flush=True)

    tasks = [(gi, ii, it["ap"], codes, cands, not it.get("nocache"))
             for gi, g in enumerate(groups) for ii, it in enumerate(g["items"])]
    results = {}
    cancelled = False
    try:
        with ProcessPoolExecutor(max_workers=workers, initializer=_set_low_priority) as ex:
            futs = [ex.submit(_task, t) for t in tasks]
            for fut in as_completed(futs):
                r = fut.result()
                results[(r["g"], r["i"])] = r
                prog["done"] += 1
                prog["current"] = r["fname"]
                if r["how"] == "miss":
                    prog["cold_done"] += 1
                elif r["how"] == "hit":
                    prog["hits"] += 1
                prog["updated"] = time.time()
                _write_json(prog_path, prog)
                print(f"[bk_merge] {prog['done']}/{total} {r['fname']} -> {r['status']} "
                      f"({r['how'] or '-'} {r['secs']}s)", flush=True)
                if os.path.exists(os.path.join(job_dir, "cancel.flag")):
                    cancelled = True
                    for f in futs:
                        f.cancel()
                    break
    except Exception as e:                        # a worker died (out of memory?) or similar
        prog.update({"state": "failed", "updated": time.time(),
                     "error": f"{type(e).__name__}: {e}. If memory ran out, set RMS_MERGE_WORKERS=1 and try again."})
        _write_json(prog_path, prog)
        print("[bk_merge] FAILED:\n" + traceback.format_exc(), flush=True)
        return 1
    if cancelled:
        prog.update({"state": "cancelled", "updated": time.time()})
        _write_json(prog_path, prog)
        print("[bk_merge] cancelled", flush=True)
        return 2

    out_groups = []
    for gi, g in enumerate(groups):
        frames, out_name, recognized, errs = [], None, False, 0
        for ii, it in enumerate(g["items"]):
            r = results.get((gi, ii))
            if not r:
                continue
            if r["status"] == "error":
                errs += 1
                continue
            if r["status"] == "no_column":
                continue
            recognized = True
            if out_name is None:
                out_name = r["fname"]
            df = r["df"]
            if r["status"] != "ok" or df is None or df.height == 0:
                continue
            if not any(_norm(c) == "date" for c in df.columns):
                d = it.get("date") or ""
                df = df.with_columns(pl.lit(f'="{d}"' if d else "").alias("Date")).select(
                    ["Date"] + [c for c in df.columns])
            frames.append(df)
        entry = {"idx": gi, "label": g["label"], "files_total": len(g["items"]), "files_with_rows": len(frames),
                 "missing": g.get("missing", 0), "errors": errs, "rows": 0, "file": "", "fname": ""}
        if not recognized:
            entry["state"] = "error" if errs else "no_column"
        elif not frames:
            entry["state"] = "no_data"
        else:
            merged = pl.concat(frames, how="diagonal")
            base = re.sub(r"\.(csv|txt|xlsx)$", "", out_name or "merged", flags=re.I)
            outp = os.path.join(job_dir, f"group_{gi}.csv")
            merged.write_csv(outp)
            entry.update({"state": "ok", "rows": merged.height, "file": os.path.basename(outp),
                          "fname": f"{base}_MERGED.csv"})
        out_groups.append(entry)

    _write_json(os.path.join(job_dir, "result.json"), {"groups": out_groups, "finished": time.time()})
    prog.update({"state": "done", "updated": time.time(), "done": total})
    _write_json(prog_path, prog)
    print(f"[bk_merge] done in {time.time() - started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python core/bk_merge.py <job_dir>")
        sys.exit(2)
    sys.exit(run_job(sys.argv[1]))
