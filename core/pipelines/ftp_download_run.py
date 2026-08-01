"""FTP download streaming runner."""
import queue, threading, io, traceback, contextlib
from core.pipelines.ftp_download import run_ftp_download


def stream_ftp_download():
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
                holder["result"] = run_ftp_download()
        except Exception as e:
            holder["error"] = f"{type(e).__name__}: {e}"
            holder["trace"] = traceback.format_exc()[-700:]
        finally:
            q.put(None)

    yield {"type": "log", "msg": "📡 Connecting to FTP server..."}
    t = threading.Thread(target=worker, daemon=True); t.start()
    while True:
        line = q.get()
        if line is None: break
        cls = "ok" if line.startswith("✔") or "COMPLETED" in line or "✅" in line else \
              "warn" if line.startswith("❌") or "NOT FOUND" in line else "run"
        yield {"type": "log", "msg": line, "cls": cls}
    t.join()

    if "error" in holder:
        yield {"type": "error", "msg": f"🔥 {holder['error']}", "trace": holder.get("trace","")}
    else:
        r = holder["result"]
        yield {"type": "done", "status": r.get("status","ok"),
               "downloaded": r.get("downloaded",0), "not_found": r.get("not_found",0)}
