"""core/lookup_job.py - runs ONE client-data lookup (CTCL/MS15/... or CLI) in its own low-priority process.

    python core/lookup_job.py <job_dir>

job.json : {"kind": "run" | "slot", "pipe": "...", "codes": [...], "from_date": "...", "to_date": "...",
            "runs": [..] | null, "slots": [..] | null}
progress.json : {"state": "running"|"done"|"failed", "done": n, "total": n, "label": "...", "started": t, "updated": t, "error": ""}
result.json   : the lookup's normal answer (written last)
The web server only starts it, shows progress, and can kill it - so a slow lookup can never freeze the console.
"""
import json
import math
import os
import sys
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _low_priority():
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000)
        else:
            os.nice(10)
    except Exception:
        pass


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _clean(o):
    """Make the answer plain JSON: numpy numbers -> python, NaN -> None, dates -> text."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in o]
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if o is None or isinstance(o, (str, int, bool)):
        return o
    if hasattr(o, "item"):
        try:
            return _clean(o.item())
        except Exception:
            pass
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def main(job_dir):
    _low_priority()
    prog_path = os.path.join(job_dir, "progress.json")
    job = json.load(open(os.path.join(job_dir, "job.json"), encoding="utf-8"))
    prog = {"state": "running", "done": 0, "total": 0, "label": "", "started": time.time(), "updated": time.time(), "error": ""}
    _write(prog_path, prog)
    last = [0.0]

    def hook(i, n, label):
        now = time.time()
        if now - last[0] < 0.25 and i < n - 1:
            return
        last[0] = now
        prog.update({"done": i, "total": n, "label": label, "updated": now})
        _write(prog_path, prog)

    try:
        from core import pipeline_box as pb
        pb._PB_PROGRESS = hook
        if job.get("kind") == "slot":
            res = pb.pb_cli_client_lookup(job["codes"], job.get("from_date", ""), job.get("to_date", ""), job.get("slots") or [])
        else:
            res = pb.pb_client_lookup(job["pipe"], job["codes"], job.get("from_date", ""), job.get("to_date", ""), job.get("runs") or None)
        _write(os.path.join(job_dir, "result.json"), _clean(res))
        prog.update({"state": "done", "done": prog.get("total", 0), "updated": time.time()})
        _write(prog_path, prog)
        return 0
    except BaseException:
        prog.update({"state": "failed", "error": traceback.format_exc().strip().splitlines()[-1][:300], "updated": time.time()})
        _write(prog_path, prog)
        print(traceback.format_exc(), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
