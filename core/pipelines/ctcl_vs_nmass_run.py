"""
CTCL vs NMASS streaming runner. Streams the run live to the dashboard.
"""
import queue, threading, io, traceback, contextlib
from core.pipelines.ctcl_vs_nmass import run_ctcl_vs_nmass


def _emoji(line):
    # Strip our own [FRIENDLY_MSG]/[MISSING_FILE] tags before display — this legacy tile
    # stream doesn't parse them into separate event types like the main px Run does, so
    # without this the raw bracket tag would show up verbatim in the console.
    if line.startswith("[FRIENDLY_MSG]"):
        return "🤖 " + line[len("[FRIENDLY_MSG]"):].strip()
    if line.startswith("[MISSING_FILE]"):
        return "⚠️ Missing file: " + line[len("[MISSING_FILE]"):].strip()
    low = line.lower()
    if any(w in low for w in ("✅","passed","success","completed","created","committed","sent")): return line if any(e in line for e in ("✅","🎯","📦","🔐","🗑","📤")) else "✅ " + line
    if any(w in low for w in ("❌","failed","abort","critical")): return line if "❌" in line else "❌ " + line
    if any(w in low for w in ("⚠️","skipped","warning","no records")): return line if "⚠️" in line else "⚠️ " + line
    return "• " + line


def stream_ctcl_vs_nmass():
    """Generator: yields events live as the CTCL vs NMASS run progresses."""
    q = queue.Queue()
    result_holder = {}

    class QueueWriter(io.TextIOBase):
        def write(self, text):
            for line in text.splitlines():
                if line.strip(): q.put(line)
            return len(text)
        def flush(self): pass

    def worker():
        try:
            with contextlib.redirect_stdout(QueueWriter()):
                result_holder["result"] = run_ctcl_vs_nmass()
        except Exception as e:
            result_holder["error"] = f"{type(e).__name__}: {e}"
            result_holder["trace"] = traceback.format_exc()[-700:]
        finally:
            q.put(None)

    yield {"type": "log", "msg": "🚀 Starting CTCL vs NMASS run..."}
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    while True:
        line = q.get()
        if line is None: break
        yield {"type": "log", "msg": _emoji(line)}
    t.join()

    if "error" in result_holder:
        yield {"type": "error", "msg": f"🔥 {result_holder['error']}", "trace": result_holder.get("trace","")}
    else:
        r = result_holder["result"]
        if r.get("status") == "prerun_failed":
            yield {"type": "done", "status": "prerun_failed", "missing": r.get("missing"), "wrong_date": r.get("wrong_date")}
        else:
            yield {"type": "done", "status": r.get("status","ok"), "run_number": r.get("run_number")}
