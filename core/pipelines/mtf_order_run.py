"""MTF Order streaming runner — streams the run live to the dashboard."""
import queue, threading, io, traceback, contextlib
from core.pipelines.mtf_order import run_mtf_order


def _emoji(line):
    low = line.lower()
    if any(e in line for e in ("✅","✔","🎯","📦","📁","📤")): return line
    if "❌" in line or "error" in low or "fail" in low: return line if "❌" in line else "❌ " + line
    if "⚠️" in line or "warning" in low or "missing" in low: return line if "⚠️" in line else "⚠️ " + line
    return "• " + line


def stream_mtf_order():
    q = queue.Queue(); holder = {}
    class QW(io.TextIOBase):
        def write(self, text):
            for line in text.splitlines():
                if line.strip(): q.put(line)
            return len(text)
        def flush(self): pass
    def worker():
        try:
            with contextlib.redirect_stdout(QW()):
                holder["result"] = run_mtf_order()
        except Exception as e:
            holder["error"] = f"{type(e).__name__}: {e}"
            holder["trace"] = traceback.format_exc()[-700:]
        finally:
            q.put(None)
    yield {"type": "log", "msg": "🚀 Starting MTF Order File..."}
    t = threading.Thread(target=worker, daemon=True); t.start()
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
            yield {"type": "done", "status": r.get("status","ok")}
