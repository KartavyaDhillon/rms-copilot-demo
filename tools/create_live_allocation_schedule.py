"""One-off helper: creates the LIVE ALLOCATION CAPTURE schedule (8 timings, Mon-Fri, holidays skipped) in the console's
scheduler.  Safe to run twice - it does nothing if the schedule already exists.

    python tools/create_live_allocation_schedule.py            (run on the machine that runs the scheduler)

Edit TIMES below (and the process setting 'The 8 timings' in Scheduler > Processes) if the timings ever change."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TIMES = ["10:12", "12:10", "13:27", "15:10", "16:53", "18:55", "20:55", "22:55"]
NAME = "LIVE ALLOCATION CAPTURE (8 times a day)"


def main():
    from core import env_boot  # noqa: F401  (loads .env exactly like the server does)
    from core.scheduler import catalog, service, store
    store.ensure_schema()
    catalog.all_defs()                      # loads every built-in process
    if any(s["name"] == NAME for s in store.list_schedules()):
        print("Already there - nothing changed.")
        return 0
    data = {"name": NAME, "process_key": "live_allocation_capture", "params": {}, "tz": "Asia/Kolkata",
            "rule": {"type": "times", "times": TIMES, "days": [0, 1, 2, 3, 4]}, "skip_holidays": True,
            "missed_policy": "grace", "missed_grace_min": 20, "overlap_policy": "queue",
            "retry": {"enabled": True, "max": 2, "delay_min": 5, "backoff": False},
            "description": "NMASS + NCDEX + MCX files into folders 1-8 of today's date."}
    out = service.create(data, "setup script", dt.datetime.now(dt.timezone.utc))
    print("Created:", out.get("name"), "- next run", out.get("next_fire_utc"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
