"""70 Files downloader streaming runner."""
import queue, threading, io, traceback, contextlib, os
from core.pipelines.files_downloader_70 import run_files_downloader_70

def stream_files_downloader_70(trade_date_str):
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
                holder["result"] = run_files_downloader_70(trade_date_str)
        except Exception as e:
            holder["error"] = f"{type(e).__name__}: {e}"
            holder["trace"] = traceback.format_exc()[-700:]
        finally:
            q.put(None)
    yield {"type": "log", "msg": f"📅 Downloading files for {trade_date_str}..."}
    t = threading.Thread(target=worker, daemon=True); t.start()
    while True:
        line = q.get()
        if line is None: break
        cls = "ok" if any(x in line for x in ("✅","✔","🎉")) else "warn" if any(x in line for x in ("❌","⚠️")) else "run"
        yield {"type": "log", "msg": line, "cls": cls}
    t.join()
    if "error" in holder:
        try:   # persist the real traceback so the chat agent can diagnose from it
            import json as _j, time as _t
            _ld = os.getenv("AGENT_LOG_DIR", os.path.join(os.getcwd(), "logs"))
            os.makedirs(_ld, exist_ok=True)
            with open(os.path.join(_ld, "last_error.json"), "w", encoding="utf-8") as _f:
                _j.dump({"pipeline": "70 Files", "msg": holder["error"],
                         "trace": holder.get("trace", ""), "ts": _t.time()}, _f)
        except Exception:
            pass
        yield {"type": "error", "msg": f"🔥 {holder['error']}", "trace": holder.get("trace","")}
    else:
        yield {"type": "done", "status": holder["result"].get("status","ok")}
