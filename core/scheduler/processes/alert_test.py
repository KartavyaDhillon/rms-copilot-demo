"""ALERT TEST — a process that FAILS ON PURPOSE, to check the whole failure-alert chain through the real scheduler.

How to use it (nothing else is touched, no file is read or written):
  1. Processes tab > ALERT TEST > 📱 WhatsApp numbers: put your own number, Save.
  2. Add schedule (any time) > Save, then Run Now on the Schedules tab.
  3. The run is recorded as FAILED and the warning picture should arrive on your WhatsApp within a minute.
Delete the schedule when you are done. It never runs by itself unless you schedule it.
"""
from core.scheduler.catalog import register
from core.scheduler.helpers import logged


@logged("ALERT TEST")                      # writes the run's log file, so Logs shows it like every other process
def _run(params, ctx):
    ctx.log("ALERT TEST: this process fails on purpose.")
    raise RuntimeError("TEST - this process fails on purpose to check the WhatsApp failure alert. Nothing is really wrong.")


register(
    key="alert_test_failure",
    name="ALERT TEST",
    description="Always fails - use it once to check that the WhatsApp failure alert reaches you. Delete its schedule afterwards.",
    run=_run, params=[], retry_safe=False, parallel_safe=True, category="Tools", folder="ALERT TEST",
)
