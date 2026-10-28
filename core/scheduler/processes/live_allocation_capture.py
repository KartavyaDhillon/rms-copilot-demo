"""LIVE ALLOCATION CAPTURE - eight times a day downloads the NMASS (CM/FO/CO), NCDEX (net deposits) and MCX (web
allocation) files into numbered folders 1..8 (same layout nn.py / ss.py made).

All the logic is in core/live_allocation.py; this file only connects it to the scheduler.  Schedule it at the eight
timings (Scheduler > Schedules > New, 'times' rule).  Which folder a run fills is decided by the time it starts (the Nth
timing of the day); the Process Center card shows every slot, lets you open the files, skip a failed slot and run it again.
Passwords live in Credentials (.env), the RSA seed in Connections > MCX eclear - never in code."""
from core import live_allocation as L
from core.scheduler import helpers as H
from core.scheduler.catalog import Param, register
from core.scheduler.processes import _kit as K

FOLDER = "LIVE ALLOCATION CAPTURE"


def _slot_from_schedule(ctx) -> int:
    """The folder a run belongs to = the timing it was SCHEDULED for (not the moment it actually started, which can be
    late after a queue or a retry). A manual 'Run now' has no timing of its own, so the clock decides."""
    try:
        import datetime as dt
        from core.scheduler import store, rules
        row = store.get_execution(ctx.execution_id) or {}
        when = store.parse_ts(row.get("scheduled_for_utc"))
        if when and (row.get("trigger_type") or "").lower() != "manual":
            loc = when.astimezone(rules.get_tz(rules.DEFAULT_TZ)).replace(tzinfo=None)
            return L.pick_slot(loc, early_grace_min=1)
    except Exception:
        pass
    return L.pick_slot()


@H.logged(FOLDER)
def _run(params, ctx):
    K.require_trading_day(ctx)
    if L.auto_enabled() and str(getattr(ctx, "trigger", "")).lower() != "manual":
        # the card's own timer already captures every folder at its time - doing it twice would only fight over the portals
        raise H.Skipped("The LIVE ALLOCATION card's built-in timer does this - nothing to do here.")
    try:
        slot = int(params.get("slot") or 0)
    except (TypeError, ValueError):
        slot = 0
    slot = slot or _slot_from_schedule(ctx)
    ctx.log(f"Capturing slot {slot} of {len(L.slot_times())} ({L.hhmm(L.slot_times()[slot - 1]) if slot <= len(L.slot_times()) else '--'}).")
    try:
        rec = L.run_slot(slot, trigger=ctx.trigger, by=ctx.schedule_name, log=ctx.log, cancelled=lambda: bool(getattr(ctx, "cancelled", False)))
    except L.Busy as e:
        raise RuntimeError(f"{e} - this slot was not started.")
    st = rec.get("status")
    if st == "skipped":
        raise H.Skipped(f"Slot {slot} was skipped by a user.")
    if st == "done":
        return f"Slot {slot}: all 5 files captured"
    raise RuntimeError(f"Slot {slot}: {rec.get('msg') or st}")


register(
    key=L.PROCESS_KEY,
    name="LIVE ALLOCATION CAPTURE",
    description="Downloads CM/FO/CO (NMASS), Client Net Effective Deposits (NCDEX, logs out afterwards) and the MCX web "
                "allocation file into folders 1-8 of today's date, 8 times a day.",
    run=_run,
    params=[
        Param("slot", "Folder number (0 = by the time it starts)", "int", default=0,
              help="Leave 0. Only set 1-8 to force a particular folder."),
        Param("slot_times", "The 8 timings (HH:MM, comma separated)", "text", scope="process", default=L.DEFAULT_TIMES,
              help="Used to pick the folder number and to draw the Process Center card. The schedule itself is set on the "
                   "Schedules tab - keep both the same."),
    ],
    retry_safe=True, parallel_safe=False, category="Allocation", folder=FOLDER,
    uses=("mcx_eclear",),
)
