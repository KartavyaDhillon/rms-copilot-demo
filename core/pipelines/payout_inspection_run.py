"""Payout inspection streaming runners."""
import queue, threading, io, traceback, contextlib, json
from core.pipelines.payout_inspection import run_payout_inspection_daily, run_payout_inspection_client

def _stream(fn):
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
                holder["result"] = fn()
        except Exception as e:
            holder["error"] = f"{type(e).__name__}: {e}"
            holder["trace"] = traceback.format_exc()[-600:]
        finally:
            q.put(None)
    t = threading.Thread(target=worker, daemon=True); t.start()
    while True:
        line = q.get()
        if line is None: break
        yield {"type": "log", "msg": line}
    t.join()
    if "error" in holder:
        yield {"type": "error", "msg": f"🔥 {holder['error']}"}
    else:
        yield {"type": "done", "status": holder["result"].get("status","ok"), "data": holder["result"]}

def stream_daily():
    yield {"type": "log", "msg": "📅 Starting daily payout ingestion..."}
    yield from _stream(run_payout_inspection_daily)

def stream_client(client_ids_str, fy_str):
    yield {"type": "log", "msg": f"🔍 Inspecting: {client_ids_str} | FY: {fy_str}"}
    yield from _stream(lambda: run_payout_inspection_client(client_ids_str, fy_str))
