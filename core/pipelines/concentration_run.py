"""
Concentration streaming runner — TWO separate streams:
  1. stream_download()  → download files only (INDEX + KORP)
  2. stream_compute()   → compute + email + backup only
  3. process_concentration() → combined (for scheduler/agent)
"""
import io, traceback, contextlib, queue, threading
from core.pipelines.concentration import (
    download_concentration_files,
    run_concentration_compute,
    run_concentration,
)


def _emoji_line(line):
    low = line.lower()
    if any(w in low for w in ("successful", "success", "created", "sent", "moved", "copied", "completed", "exists", "present", "proceeding")):
        return "✅ " + line
    if any(w in low for w in ("error", "fail", "missing", "❌")):
        return "❌ " + line
    if any(w in low for w in ("downloading", "getting", "encrypting", "logging", "converting", "saving", "skipping")):
        return "⬇️ " + line
    if any(w in low for w in ("backup", "moved to backup", "copied to backup")):
        return "📦 " + line
    return "• " + line


def _stream_fn(fn):
    """Run fn() in a background thread, yield its print output live."""
    q = queue.Queue()
    result_holder = {}

    class QueueWriter(io.TextIOBase):
        def write(self, text):
            for line in text.splitlines():
                if line.strip():
                    q.put(line)
            return len(text)
        def flush(self): pass

    def worker():
        try:
            with contextlib.redirect_stdout(QueueWriter()):
                result_holder["result"] = fn()
        except Exception as e:
            result_holder["error"] = f"{type(e).__name__}: {e}"
            result_holder["trace"] = traceback.format_exc()[-600:]
        finally:
            q.put(None)  # sentinel

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    while True:
        line = q.get()
        if line is None:
            break
        yield {"type": "log", "msg": _emoji_line(line)}
    t.join()

    if "error" in result_holder:
        yield {"type": "error", "msg": f"🔥 {result_holder['error']}",
               "trace": result_holder.get("trace", "")}
    else:
        yield {"type": "done", "status": result_holder["result"].get("status", "ok"),
               "step": result_holder["result"].get("step", "")}


def stream_download():
    """Stream the download step live (INDEX files + KORP shortage)."""
    yield {"type": "log", "msg": "⬇️ Starting file downloads (index files + shortage)..."}
    yield from _stream_fn(download_concentration_files)


def stream_compute():
    """Stream the compute step live (polars + emails + backup)."""
    yield {"type": "log", "msg": "🧮 Starting concentration computation..."}
    yield from _stream_fn(run_concentration_compute)


def process_concentration():
    """Non-streaming combined run (for agent/scheduler). Returns final result."""
    msgs, final = [], {}
    for ev in list(_stream_fn(download_concentration_files)):
        if ev.get("msg"): msgs.append(ev["msg"])
        if ev["type"] in ("done", "error"): final = ev
    if final.get("type") == "error":
        return {"status": "error", "step": "download", "messages": msgs,
                "error": final.get("msg")}
    for ev in list(_stream_fn(run_concentration_compute)):
        if ev.get("msg"): msgs.append(ev["msg"])
        if ev["type"] in ("done", "error"): final = ev
    return {"status": final.get("status", "ok"), "messages": msgs}
