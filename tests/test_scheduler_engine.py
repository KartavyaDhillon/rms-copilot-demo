"""Engine / persistence tests — fake clock, throw-away sqlite file, MOCK processes only.
No real RMS process is ever imported or run.   python -m unittest discover -s tests -t . -v
"""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
import types
import unittest

from core.scheduler import catalog, engine, rules, service, store

UTC = dt.timezone.utc
IST = rules.get_tz("Asia/Kolkata")


def ist(y, mo, d, h=0, mi=0, s=0):
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=IST).astimezone(UTC)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        store._CONN_FACTORY = lambda: sqlite3.connect(self.db, timeout=15)
        store.ensure_schema()
        os.environ["SCHED_HOLIDAYS_FILE"] = os.path.join(self.tmp, "no_holidays.csv")   # no file → no holidays
        self.calls = {"ok": 0, "fail": 0, "flaky": 0, "other": 0}
        self.flaky_fail_times = 1

        def ok(params, ctx):
            self.calls["ok"] += 1
            ctx.log("hello")
            return "done"

        def fail(params, ctx):
            self.calls["fail"] += 1
            raise RuntimeError("boom password=hunter2")

        def flaky(params, ctx):
            self.calls["flaky"] += 1
            if self.calls["flaky"] <= self.flaky_fail_times:
                raise RuntimeError("temporary")
            return "recovered"

        def other(params, ctx):
            self.calls["other"] += 1

        catalog.register("t.ok", "OK process", ok, params=[catalog.Param("n", "N", "int", default=1)])
        catalog.register("t.fail", "Failing process", fail)
        catalog.register("t.flaky", "Flaky process", flaky, retry_safe=True)
        catalog.register("t.other", "Other", other)
        self.now = ist(2026, 10, 7, 7, 0)                     # Wednesday
        self.clock = lambda: self.now
        self.eng = engine.SchedulerEngine(clock=self.clock, synchronous=True, worker_id="A", is_holiday=lambda d: False)

    def tearDown(self):
        for k in ("t.ok", "t.fail", "t.flaky", "t.other", "t.pipe"):
            catalog.unregister(k)
        store._CONN_FACTORY = None
        os.environ.pop("SCHED_HOLIDAYS_FILE", None)

    # helpers
    def mk(self, rule=None, process="t.ok", name="S1", **kw):
        data = {"name": name, "process_key": process, "tz": "Asia/Kolkata", "skip_holidays": False,
                "rule": rule or {"type": "times", "times": ["07:45"], "days": [0, 1, 2, 3, 4]}}
        data.update(kw)
        return service.create(data, "tester", self.now)

    def at(self, h, m=0, s=0, day=7):
        self.now = ist(2026, 10, day, h, m, s)
        return self.eng.tick(self.now)

    def rows(self, **kw):
        return store.list_executions(**kw)

    def statuses(self):
        return sorted((r["trigger_type"], r["status"]) for r in self.rows())


class TestScheduling(Base):
    def test_only_the_agreed_processes_are_registered(self):
        for k in ("t.ok", "t.fail", "t.flaky", "t.other", "t.pipe"):
            catalog.unregister(k)
        from core.scheduler.processes import ALL_NAMES
        self.assertEqual({d.name for d in catalog.all_defs()}, set(ALL_NAMES))

    def test_runs_once_at_the_scheduled_time(self):
        self.mk()
        self.at(7, 44, 50)
        self.assertEqual(self.calls["ok"], 0)
        self.at(7, 45, 5)
        self.assertEqual(self.calls["ok"], 1)
        self.at(7, 45, 15)
        self.at(7, 46, 0)
        self.assertEqual(self.calls["ok"], 1)                       # no duplicate
        self.assertEqual(self.statuses(), [("SCHEDULED", "SUCCESS")])

    def test_multiple_times_per_day(self):
        self.mk({"type": "times", "times": ["07:45", "12:00"]})
        self.at(7, 45, 5)
        self.at(12, 0, 5)
        self.assertEqual(self.calls["ok"], 2)

    def test_fifteen_minute_interval(self):
        self.mk({"type": "interval", "every": 15, "unit": "minutes", "start": "09:00", "end": "17:00"})
        for h, m in [(9, 0), (9, 15), (9, 30)]:
            self.at(h, m, 5)
        self.assertEqual(self.calls["ok"], 3)

    def test_two_instances_never_double_run(self):
        self.mk()
        eng_b = engine.SchedulerEngine(clock=self.clock, synchronous=True, worker_id="B", is_holiday=lambda d: False)
        self.now = ist(2026, 10, 7, 7, 45, 5)
        a = self.eng.tick(self.now)
        b = eng_b.tick(self.now)
        self.assertTrue(a["leader"])
        self.assertFalse(b["leader"])                                # lease held by A
        self.assertEqual(self.calls["ok"], 1)

    def test_same_window_claimed_by_two_evaluators_runs_once(self):
        s = self.mk()
        eng_b = engine.SchedulerEngine(clock=self.clock, synchronous=True, worker_id="B", is_holiday=lambda d: False)
        self.now = ist(2026, 10, 7, 7, 45, 5)
        snapshot = store.get_schedule(s["id"])                       # both evaluators hold the SAME stale snapshot
        self.eng._evaluate(dict(snapshot), self.now)
        eng_b._evaluate(dict(snapshot), self.now)                    # loses the compare-and-swap
        self.assertEqual(self.calls["ok"], 1)

    def test_lease_failover_after_leader_stops(self):
        self.mk()
        eng_b = engine.SchedulerEngine(clock=self.clock, synchronous=True, worker_id="B", is_holiday=lambda d: False)
        self.at(7, 0, 1)                                              # A becomes leader
        self.now = ist(2026, 10, 7, 7, 45, 5)
        self.assertTrue(eng_b.tick(self.now)["leader"])               # A silent > lease → B takes over
        self.assertEqual(self.calls["ok"], 1)

    def test_holiday_skipped_by_service(self):
        # real calendar module reads the (non-existent) temp file → nothing is a holiday, run happens
        self.mk(skip_holidays=True)
        self.at(7, 45, 5)
        self.assertEqual(self.calls["ok"], 1)


class TestMissedRuns(Base):
    def test_latest_missed_runs_once_as_recovery(self):
        self.mk()
        eng2 = engine.SchedulerEngine(clock=self.clock, synchronous=True, worker_id="NEW", is_holiday=lambda d: False)
        self.now = ist(2026, 10, 7, 10, 0)                            # server was off from 07:00
        eng2.tick(self.now)                                           # a brand-new instance = restart
        self.assertEqual(self.calls["ok"], 1)
        self.assertEqual(self.statuses(), [("MISSED_RUN_RECOVERY", "SUCCESS")])

    def test_skip_policy_records_but_does_not_run(self):
        self.mk(missed_policy="skip")
        self.at(10, 0)
        self.assertEqual(self.calls["ok"], 0)
        self.assertEqual(self.statuses(), [("SCHEDULED", "MISSED")])

    def test_grace_window(self):
        self.mk(missed_policy="grace", missed_grace_min=30)
        self.at(8, 10)                                                # 25 min late → runs
        self.assertEqual(self.calls["ok"], 1)
        self.mk(missed_policy="grace", missed_grace_min=30, name="S2",
                rule={"type": "times", "times": ["09:00"]})
        self.at(11, 0)                                                # 120 min late → only recorded
        self.assertEqual(self.calls["ok"], 1)
        self.assertIn(("SCHEDULED", "MISSED"), self.statuses())

    def test_long_outage_on_interval_does_not_replay_history(self):
        self.mk({"type": "interval", "every": 15, "unit": "minutes", "start": "09:00", "end": "17:00"})
        self.now = ist(2026, 10, 7, 8, 0)
        self.at(14, 0, 20)                                            # offline since 08:00
        self.assertEqual(self.calls["ok"], 1)                         # only the on-time 14:00 run
        st = self.statuses()
        self.assertEqual(st.count(("SCHEDULED", "SUCCESS")), 1)
        self.assertEqual(st.count(("SCHEDULED", "MISSED")), 1)        # all earlier ticks = ONE summary row
        self.assertEqual(len(st), 2)

    def test_pause_and_resume_do_not_count_as_missed(self):
        s = self.mk()
        service.pause(s["id"], "tester", self.now)
        self.at(7, 45, 5)
        self.assertEqual(self.calls["ok"], 0)
        self.now = ist(2026, 10, 7, 9, 0)
        service.resume(s["id"], "tester", self.now)
        self.at(9, 0, 30)
        self.assertEqual(self.rows(), [])                             # nothing missed, nothing run
        self.at(7, 45, 5, day=8)
        self.assertEqual(self.calls["ok"], 1)                         # next valid run still happens


class TestOverlapAndLocks(Base):
    def _busy_row(self, s):
        """A run of this schedule that is 'still running' (fresh heartbeat, started earlier)."""
        self.now = ist(2026, 10, 7, 7, 45, 0)
        return store.create_execution(s, ist(2026, 10, 7, 6, 0), "SCHEDULED", "RUNNING", self.now, worker="X")

    def test_overlap_skip(self):
        s = self.mk()
        self._busy_row(s)
        self.at(7, 45, 5)
        self.assertEqual(self.calls["ok"], 0)
        self.assertIn(("SCHEDULED", "SKIPPED"), self.statuses())

    def test_overlap_queue_then_runs_when_free(self):
        s = self.mk(overlap_policy="queue")
        busy = self._busy_row(s)
        self.at(7, 45, 5)
        self.assertIn(("SCHEDULED", "QUEUED"), self.statuses())
        self.assertEqual(self.calls["ok"], 0)
        store.finish_execution(busy, "SUCCESS", self.now)
        self.at(7, 45, 20)
        self.assertEqual(self.calls["ok"], 1)

    def test_parallel_requires_parallel_safe_process(self):
        with self.assertRaises(service.ValidationFailed):
            self.mk(overlap_policy="parallel")

    def test_another_schedule_running_same_process_blocks(self):
        a = self.mk(name="A")
        self.mk(name="B")
        self._busy_row(a)
        self.at(7, 45, 5)
        self.assertEqual(self.calls["ok"], 0)

    def test_existing_process_center_lock_is_respected(self):
        catalog.register("t.pipe", "Pipe-bound", lambda p, c: None, lock_pipe="ctcl")
        fake = types.ModuleType("core.pipeline_box")
        fake._PB = {"ctcl": {"running": True}}
        saved = sys.modules.get("core.pipeline_box")
        sys.modules["core.pipeline_box"] = fake
        try:
            self.mk(process="t.pipe")
            self.at(7, 45, 5)
            self.assertIn(("SCHEDULED", "SKIPPED"), self.statuses())
        finally:
            if saved is not None:
                sys.modules["core.pipeline_box"] = saved
            else:
                sys.modules.pop("core.pipeline_box", None)


class TestFailures(Base):
    def test_failure_is_stored_and_isolated(self):
        self.mk(process="t.fail", name="BAD")
        self.mk(process="t.ok", name="GOOD")
        self.at(7, 45, 5)
        self.assertEqual(self.calls["fail"], 1)
        self.assertEqual(self.calls["ok"], 1)                          # the other schedule still ran
        bad = [r for r in self.rows() if r["status"] == "FAILED"][0]
        self.assertEqual(bad["error_type"], "RuntimeError")
        self.assertNotIn("hunter2", bad["error_msg"])                  # secret scrubbed
        full = store.get_execution(bad["id"])
        self.assertNotIn("hunter2", full["log_text"])

    def test_deliberate_skip_is_recorded_as_skipped_with_its_reason(self):
        def nothing(params, ctx):
            raise catalog.Skipped("Already sent today at 12:07:01 — nothing to do.")
        catalog.register("t.skip", "Skipping process", nothing)
        try:
            self.mk(process="t.skip", name="SK")
            self.at(7, 45, 5)
            r = self.rows()[0]
            self.assertEqual(r["status"], "SKIPPED")                   # not SUCCESS, not FAILED
            self.assertIn("12:07:01", r["error_msg"])
            self.assertIn("Skipped", store.get_execution(r["id"])["log_text"])
        finally:
            catalog.unregister("t.skip")

    def test_unknown_process_does_not_crash_the_tick(self):
        s = self.mk()
        store._write("UPDATE sched_schedules SET process_key='gone.process' WHERE id=?", (s["id"],))
        self.at(7, 45, 5)
        self.assertEqual(self.statuses(), [("SCHEDULED", "FAILED")])

    def test_retry_runs_again_after_delay(self):
        self.mk(process="t.flaky", retry={"enabled": True, "max": 2, "delay_min": 1})
        self.at(7, 45, 5)
        self.assertEqual(self.calls["flaky"], 1)
        self.at(7, 45, 30)                                              # not yet 1 minute
        self.assertEqual(self.calls["flaky"], 1)
        self.at(7, 47, 0)
        self.assertEqual(self.calls["flaky"], 2)
        self.assertIn(("SCHEDULED", "SUCCESS"), self.statuses())

    def test_retry_stops_after_max(self):
        self.flaky_fail_times = 99
        self.mk(process="t.flaky", retry={"enabled": True, "max": 2, "delay_min": 1})
        self.at(7, 45, 5)
        for i in range(1, 8):
            self.at(7, 45 + i * 2, 0)
        self.assertEqual(self.calls["flaky"], 3)                        # 1 try + 2 retries

    def test_retry_refused_for_processes_not_marked_safe(self):
        with self.assertRaises(service.ValidationFailed):
            self.mk(process="t.ok", retry={"enabled": True, "max": 2, "delay_min": 1})

    def test_interrupted_run_is_failed_not_rerun(self):
        s = self.mk()
        eid = store.create_execution(s, ist(2026, 10, 7, 6, 0), "SCHEDULED", "RUNNING", ist(2026, 10, 7, 6, 0), worker="DEAD")
        self.at(7, 0, 30)
        self.assertEqual(store.get_execution(eid)["status"], "FAILED")
        self.assertEqual(store.get_execution(eid)["error_type"], "Interrupted")
        self.assertEqual(self.calls["ok"], 0)


class TestRunNowAndLifecycle(Base):
    def test_run_now_does_not_move_the_next_run(self):
        s = self.mk()
        before = service.view(store.get_schedule(s["id"]), self.now)["next_run_utc"]
        self.now = ist(2026, 10, 7, 7, 10)
        self.eng.run_now(s["id"], "tester")
        self.assertEqual(self.calls["ok"], 1)
        after = service.view(store.get_schedule(s["id"]), self.now)["next_run_utc"]
        self.assertEqual(before, after)
        self.at(7, 45, 5)
        self.assertEqual(self.calls["ok"], 2)                           # the normal run still happens
        self.assertEqual(self.statuses(), [("MANUAL_RUN_NOW", "SUCCESS"), ("SCHEDULED", "SUCCESS")])

    def test_run_now_refused_when_busy(self):
        s = self.mk()
        store.create_execution(s, self.now, "SCHEDULED", "RUNNING", self.now, worker="X")
        with self.assertRaises(engine.BusyError):
            self.eng.run_now(s["id"], "tester")
        self.assertEqual(self.calls["ok"], 0)

    def test_one_time_schedule_completes(self):
        s = self.mk({"type": "once", "at": ["2026-10-09T11:42", "2026-10-12T15:17"]})
        self.at(11, 42, 5, day=9)
        self.at(15, 17, 5, day=12)
        self.assertEqual(self.calls["ok"], 2)
        self.assertEqual(store.get_schedule(s["id"])["status"], "completed")

    def test_end_date_completes_schedule(self):
        s = self.mk(end_date="2026-10-08")
        self.at(7, 45, 5)
        self.at(7, 45, 5, day=8)
        self.at(7, 50, 0, day=8)
        self.assertEqual(self.calls["ok"], 2)
        self.assertEqual(store.get_schedule(s["id"])["status"], "completed")

    def test_edit_changes_timing_and_keeps_history(self):
        s = self.mk()
        self.at(7, 45, 5)
        self.now = ist(2026, 10, 7, 8, 0)
        service.update(s["id"], {"name": "S1", "process_key": "t.ok", "tz": "Asia/Kolkata", "skip_holidays": False,
                                 "rule": {"type": "times", "times": ["09:00"]}}, "tester", self.now)
        self.at(9, 0, 5)
        self.assertEqual(self.calls["ok"], 2)
        self.assertEqual(len(self.rows()), 2)

    def test_audit_trail_and_no_secrets(self):
        s = self.mk()
        service.pause(s["id"], "tester", self.now)
        service.resume(s["id"], "tester", self.now)
        self.eng.run_now(s["id"], "tester")
        service.delete(s["id"], "tester", self.now)
        actions = [a["action"] for a in store.list_audit()]
        for a in ("created", "paused", "resumed", "run_now", "deleted"):
            self.assertIn(a, actions)
        self.assertEqual(store.scrub({"password": "x", "n": {"api_key": "y", "ok": 1}}),
                         {"password": "***", "n": {"api_key": "***", "ok": 1}})

    def test_validation(self):
        with self.assertRaises(service.ValidationFailed):
            self.mk(process="not.registered")
        with self.assertRaises(service.ValidationFailed):
            self.mk(tz="Mars/Base")
        with self.assertRaises(service.ValidationFailed):
            self.mk(rule={"type": "times", "times": []})
        with self.assertRaises(service.ValidationFailed):
            self.mk(params={"unknown": 1})
        with self.assertRaises(service.ValidationFailed):
            self.mk(rule={"type": "once", "at": ["2020-01-01T10:00"]})   # already in the past

    def test_preview_uses_the_same_calculation(self):
        data = {"name": "x", "process_key": "t.ok", "tz": "Asia/Kolkata", "skip_holidays": False,
                "rule": {"type": "times", "times": ["07:45"], "days": [0, 1, 2, 3, 4]}}
        pv = service.preview(data, self.now)
        self.assertEqual(len(pv["upcoming"]), 5)
        self.assertEqual(pv["upcoming"][0]["text"], "07 Oct 2026 07:45")
        s = self.mk()
        self.assertEqual(service.view(store.get_schedule(s["id"]), self.now)["next_run_utc"], pv["upcoming"][0]["utc"])

    def test_schedules_survive_restart(self):
        s = self.mk()
        store._CONN_FACTORY = lambda: sqlite3.connect(self.db, timeout=15)   # "new process", same database file
        self.assertEqual(store.get_schedule(s["id"])["name"], "S1")


if __name__ == "__main__":
    unittest.main()
