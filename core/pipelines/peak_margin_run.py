"""
Peak-margin runner - STREAMING generator version.

process_pair_stream(index) is a GENERATOR: it yields events one-at-a-time as
they happen, so the UI can show them live like a terminal. Each yielded event
is a dict: {"type": "log"/"done"/"error", ...}.

Behaviour:
  - i01 RESETS the day (fresh report, counter back to 0), then fills Peak1.
  - i02/i03/i04 add their own column to the existing report.
  - On i04, cleans up ONLY the 8 downloaded data files (keeps images).
  - Full error handling with clear messages.

process_pair(index) (non-streaming) is kept for the agent/scheduler - it just
drains the generator and returns the final result.
"""
import io
import os
import json
import contextlib
import traceback
from datetime import datetime
from core.downloaders.nse import download_pair
from core.pipelines.peak_margin import update_report_with_pair, reset_day, FOLDER
from core.pipelines.report_image import build_report_images

SNAPSHOT_TIME = {"i01": "10:30", "i02": "12:30", "i03": "13:45", "i04": "15:30"}


def _runlog_path():
    return os.path.join(FOLDER, "peak_runs.json")


def _record_run(index, today):
    path = _runlog_path()
    log = {"date": today, "ran": []}
    if os.path.exists(path):
        try:
            saved = json.load(open(path))
            if saved.get("date") == today:
                log = saved
        except (json.JSONDecodeError, OSError):
            pass
    if index not in log["ran"]:
        log["ran"].append(index)
    os.makedirs(FOLDER, exist_ok=True)
    json.dump(log, open(path, "w"), indent=2)
    return sorted(log["ran"])


def _cleanup_downloaded_files(today):
    member = os.getenv("NSE_MEMBER_CODE", "")
    deleted = []
    for prefix in (f"F_SA01_P_{member}_{today}_", f"C_SA01_P_{member}_{today}_"):
        for i in range(1, 5):
            for ext in (".xlsx", ".lis.gz"):
                p = os.path.join(FOLDER, f"{prefix}i0{i}{ext}")
                if os.path.exists(p):
                    try:
                        os.remove(p); deleted.append(os.path.basename(p))
                    except OSError:
                        pass
    return deleted


def _emoji_line(line):
    low = line.lower()
    if "login successful" in low: return "🔑 " + line
    if "already have" in low:     return "📁 " + line
    if "downloaded" in low:       return "⬇️ " + line
    if any(w in low for w in ("error", "fail", "timed out", "unauthorized")): return "⚠️ " + line
    return "• " + line


def cleanup_after_send():
    """Delete the downloaded data files for today. Call this ONLY after a
    successful send, so we never remove files the send still needs."""
    today = datetime.now().strftime("%d%m%Y")
    return _cleanup_downloaded_files(today)


def process_pair_stream(index: str):
    """GENERATOR. Yields {"type":...} events as the run progresses."""
    try:
        when = SNAPSHOT_TIME.get(index, index)
        today = datetime.now().strftime("%d%m%Y")
        yield {"type": "log", "msg": f"🔍 Checking the {when} peak-margin snapshot ({index})..."}

        # i01 = first run of the day -> wipe leftovers, start fresh.
        if index == "i01":
            reset_day(today)
            yield {"type": "log", "msg": "🧼 New day reset — starting a fresh report (Peak1)."}

        # Download with a LIVE callback so each step streams the moment it happens
        # (no more capture-then-dump bursts). We collect events into a list that
        # the callback appends to, and we drain it as we go via a small queue.
        import queue, threading
        q = queue.Queue()
        result_holder = {}
        def on_event(msg):
            q.put(msg)
        def worker():
            try:
                result_holder["dl"] = download_pair(index, FOLDER, on_event=on_event)
            except Exception as e:
                result_holder["err"] = e
            finally:
                q.put(None)  # sentinel = done
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while True:
            msg = q.get()
            if msg is None:
                break
            yield {"type": "log", "msg": _emoji_line(msg)}
        t.join()
        if "err" in result_holder:
            raise result_holder["err"]
        dl = result_holder["dl"]

        if dl["status"] == "bad_index":
            yield {"type": "error", "msg": f"❌ '{index}' is not a valid snapshot (use i01-i04)."}
            return
        if dl["status"] == "not_arrived":
            which = " and ".join(dl.get("missing", []))
            yield {"type": "log", "msg": f"⏳ The {when} files ({which}) haven't arrived yet. Retry next run."}
            yield {"type": "done", "status": "not_arrived", "index": index}
            return

        yield {"type": "log", "msg": f"✅ Files for the {when} snapshot have arrived."}
        yield {"type": "log", "msg": "🧮 Computing peak margin..."}
        report = update_report_with_pair(index, dl["fo_path"], dl["cm_path"], today)

        yield {"type": "log", "msg": "🖼️ Building report images..."}
        images = build_report_images(report, FOLDER)
        yield {"type": "log", "msg": "✅ Report images ready (FO + CM/Total)."}

        ran = _record_run(index, today)   # list like ["i02","i03","i04"]
        ran_label = ", ".join(ran)
        yield {"type": "log", "msg": f"🎉 Done. {when} column updated "
               f"(captured this session: {ran_label})."}

        # NOTE: cleanup is NOT done here anymore. It happens only AFTER a
        # successful send (see the backend), so we never delete files the
        # send still needs.

        yield {"type": "done", "status": "computed", "index": index,
               "report": report, "ran": ran, "ran_label": ran_label,
               "is_final": (index == "i04"),
               "image_urls": {"fo": "/report-image/F_filtered.png",
                              "cm": "/report-image/C_filtered.png"}}

    except Exception as e:
        yield {"type": "error", "msg": f"🔥 ERROR: {type(e).__name__}: {e}",
               "trace": traceback.format_exc()[-600:]}


def process_pair(index: str) -> dict:
    """Non-streaming convenience: drain the generator, return final result."""
    msgs, final = [], {"status": "error", "index": index}
    for ev in process_pair_stream(index):
        if ev["type"] in ("log",):
            msgs.append(ev["msg"])
        elif ev["type"] == "error":
            msgs.append(ev["msg"])
            final = {"status": "error", "index": index, "error": ev["msg"]}
        elif ev["type"] == "done":
            final = {k: v for k, v in ev.items() if k != "type"}
    final["messages"] = msgs
    return final


if __name__ == "__main__":
    import sys, time
    idx = sys.argv[1] if len(sys.argv) > 1 else "i01"
    # Print live, like the terminal, with a tiny delay so you SEE the steps
    for ev in process_pair_stream(idx):
        if ev.get("msg"):
            print(ev["msg"]); time.sleep(0.2)
