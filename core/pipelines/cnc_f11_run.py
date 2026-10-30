"""CNC F11 streaming runner — streams the run live to the dashboard."""
import queue, threading, io, traceback, contextlib
from core.pipelines.cnc_f11 import run_cnc_f11


def _emoji(line):
    low = line.lower()
    if "❌" in line or "fail" in low or "missing" in low: return line if "❌" in line else "❌ " + line
    if "⚠️" in line or "could not" in low: return line if "⚠️" in line else "⚠️ " + line
    if any(e in line for e in ("✅","📧","📦","🗑")): return line
    return "• " + line


def stream_cnc_f11():
    """Generator: yields events live as CNC F11 runs."""
    q = queue.Queue()
    holder = {}

    class QW(io.TextIOBase):
        def write(self, text):
            for line in text.splitlines():
                if line.strip(): q.put(line)
            return len(text)
        def flush(self): pass

    def worker():
        try:
            with contextlib.redirect_stdout(QW()):
                holder["result"] = run_cnc_f11()
        except Exception as e:
            holder["error"] = f"{type(e).__name__}: {e}"
            holder["trace"] = traceback.format_exc()[-600:]
        finally:
            q.put(None)

    yield {"type": "log", "msg": "🚀 Starting CNC F11..."}
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    while True:
        line = q.get()
        if line is None: break
        yield {"type": "log", "msg": _emoji(line)}
    t.join()

    if "error" in holder:
        yield {"type": "error", "msg": f"🔥 {holder['error']}", "trace": holder.get("trace","")}
    else:
        r = holder["result"]
        if r.get("status") == "missing_files":
            yield {"type": "done", "status": "missing_files", "missing": r.get("missing")}
        else:
            yield {"type": "done", "status": r.get("status", "ok")}
