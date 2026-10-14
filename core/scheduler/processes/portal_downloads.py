"""Portal Downloads — registers every job saved on the Scheduler's "Portal Downloads" tab as a schedulable process
(see core/scheduler/portal_jobs.py).  This module defines no process of its own."""
from core.scheduler import portal_jobs

portal_jobs.sync()
