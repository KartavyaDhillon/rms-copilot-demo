"""RMS Copilot — Scheduler / Automation Center.

Layout
------
rules.py     pure schedule maths (next runs, missed runs, validation, wording).
             The ONLY place next-run times are calculated — the API preview and the
             engine both call it, so the UI and the backend cannot disagree.
catalog.py   registry of processes the scheduler may run (register once, UI is dynamic).
calendar.py  market-holiday calendar shared with the RECONCILIATION holiday list.
store.py     persistence (sqlite or Postgres, same pattern as core/auth.py).
engine.py    leader-elected tick loop, missed-run / overlap / retry handling, Run Now.
routes.py    FastAPI router  /sched/*   (included from api.py).
processes/   one file per scheduler process (auto-imported). EMPTY until you add some.
"""
