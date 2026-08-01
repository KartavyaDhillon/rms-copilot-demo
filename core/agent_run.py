"""
core/agent_run.py — Streaming runner for the chat agent.
Runs the agent in a background thread and yields SSE events live.
"""
import queue, threading, json
from core.agent import run_agent_chat


def stream_agent(user_message: str, history: list):
    """
    Generator that yields SSE-style event dicts as the agent works.
    Yields events:
      {"type": "tool_start", "tool": name, "args": {...}}
      {"type": "tool_done",  "tool": name, "preview": "..."}
      {"type": "answer",     "text": "...", "history": [...]}
      {"type": "error",      "msg": "..."}
    """
    q = queue.Queue()
    holder = {}

    def on_step(ev):
        q.put(ev)

    def worker():
        try:
            answer, updated_history = run_agent_chat(user_message, history, on_step)
            holder["answer"]  = answer
            holder["history"] = updated_history
        except Exception as e:
            holder["error"] = str(e)
        finally:
            q.put(None)  # sentinel

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    while True:
        ev = q.get()
        if ev is None:
            break
        yield ev

    t.join()

    if "error" in holder:
        yield {"type": "error", "msg": holder["error"]}
    else:
        yield {"type": "answer", "text": holder["answer"], "history": holder["history"]}
