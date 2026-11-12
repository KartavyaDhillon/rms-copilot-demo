import datetime as dt
import os
import tempfile
import unittest

from core import ms15_auto as M

TIMES = {1: 9 * 60 + 30, 2: 9 * 60 + 50}


def at(h, m):
    return dt.datetime(2026, 11, 12, h, m)


ST = {"auto": True, "mcx_lead_min": 7, "nmass_lead_min": 1, "wait_min": 4, "grace_min": 20, "alert_numbers": ""}
EMPTY = {"date": "x", "runs": {}}


class DueTests(unittest.TestCase):
    def due(self, now, state=None, st=ST, counter=0):
        return M.due_action(now, st=st, state=state or {"runs": {}}, times=TIMES, counter=counter)

    def test_nothing_early(self):
        self.assertIsNone(self.due(at(9, 20)))

    def test_mcx_seven_minutes_before(self):
        self.assertIsNone(self.due(at(9, 22)))
        self.assertEqual(self.due(at(9, 23)), ("mcx", 1))

    def test_nmass_one_minute_before(self):
        s = {"runs": {"1": {"mcx": "running"}}}
        self.assertIsNone(self.due(at(9, 28), s))
        self.assertEqual(self.due(at(9, 29), s), ("nmass", 1))

    def test_run_at_time(self):
        s = {"runs": {"1": {"mcx": "done", "nmass": "done"}}}
        self.assertEqual(self.due(at(9, 30), s), ("run", 1))

    def test_missed_after_grace(self):
        self.assertEqual(self.due(at(9, 55), {"runs": {"1": {}}}), ("missed", 1))

    def test_already_done_by_counter(self):
        self.assertEqual(self.due(at(9, 31), counter=1), ("mark_done", 1))

    def test_off_does_nothing(self):
        self.assertIsNone(self.due(at(9, 30), st={**ST, "auto": False}))

    def test_terminal_and_running_skipped(self):
        s = {"runs": {"1": {"status": "done"}}}
        self.assertIsNone(self.due(at(9, 31), s, counter=1))
        s ={"runs": {"1": {"status": "running"}, "2": {}}}
        self.assertIsNone(self.due(at(9, 31), s))


class RunSlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = M.LOG_DIR
        M.LOG_DIR = self.tmp
        M._JOBS.clear()
        self.alerts = []
        self.clock = [1000.0]

    def tearDown(self):
        M.LOG_DIR = self._old

    def now(self):
        return self.clock[0]

    def sleep(self, s):
        self.clock[0] += s

    def alert(self, n, head, detail):
        self.alerts.append((n, head, detail))
        return {"sent": True, "reason": "sent"}

    def go(self, missing, run=lambda: (True, "ok"), pre=None):
        M._upd(1, **(pre or {"mcx": "done", "nmass": "done"}))
        return M.run_slot(1, ST, now_fn=self.now, sleep=self.sleep, missing=missing, run=run, alert=self.alert,
                          start=lambda n, w: None)

    def test_runs_when_files_ready(self):
        r = self.go(lambda: [])
        self.assertEqual(r["status"], "done")
        self.assertEqual(self.alerts, [])

    def test_waits_then_runs_when_files_arrive(self):
        calls = {"n": 0}

        def missing():
            calls["n"] += 1
            return ["CM_CLIENT_LEVEL - missing"] if calls["n"] < 6 else []
        r = self.go(missing)
        self.assertEqual(r["status"], "done")
        self.assertLess(self.clock[0] - 1000, 4 * 60)

    def test_files_never_arrive_alerts_after_four_minutes(self):
        r = self.go(lambda: ["CM_CLIENT_LEVEL - missing"], pre={"mcx": "done", "nmass": "failed", "nmass_msg": "login failed"})
        self.assertEqual(r["status"], "failed")
        self.assertGreaterEqual(self.clock[0] - 1000, 4 * 60)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("NMASS", self.alerts[0][2])
        self.assertIn("login failed", self.alerts[0][2])

    def test_run_error_alerts(self):
        r = self.go(lambda: [], run=lambda: (False, "boom"))
        self.assertEqual(r["status"], "failed")
        self.assertIn("boom", self.alerts[0][2])



class CtclAndLateTests(unittest.TestCase):
    ST = {**ST, "ctcl_prefetch": True, "ctcl_lead_min": 7}

    def test_ctcl_skips_when_ms15_file_is_fresh(self):
        times = {3: 11 * 60 + 40}
        self.assertIsNone(M.due_ctcl(at(11, 32), self.ST, {"runs": {}}, times, age=10))
        self.assertEqual(M.due_ctcl(at(11, 33), self.ST, {"runs": {}}, times, age=10)[0], "skip")
        self.assertEqual(M.due_ctcl(at(11, 33), self.ST, {"runs": {}}, times, age=40)[0], "download")
        self.assertEqual(M.due_ctcl(at(11, 33), self.ST, {"runs": {}}, times, age=None)[0], "download")
        self.assertIsNone(M.due_ctcl(at(11, 33), self.ST, {"runs": {}, "ctcl": {"3": "done"}}, times, age=40))
        self.assertIsNone(M.due_ctcl(at(11, 33), {**self.ST, "ctcl_prefetch": False}, {"runs": {}}, times, age=40))

    def test_late_mcx_file_is_parked_as_not_used(self):
        import time as _t
        base, bk = tempfile.mkdtemp(), tempfile.mkdtemp()
        old = os.path.join(base, "MCX_DEPOSIT_09.20.xlsx"); open(old, "w").close()
        os.utime(old, (_t.time() - 3600, _t.time() - 3600))
        late = os.path.join(base, "abcXYZ-20261112093100.xlsx"); open(late, "w").close()
        other = os.path.join(base, "CM_ClientLevelCollaterals_12112026093000.csv"); open(other, "w").close()
        M.LOG_DIR = tempfile.mkdtemp()
        moved = M.park_late_mcx(1, _t.time() - 60, _t.time(), base=base, backup=bk)
        self.assertEqual(moved, ["abcXYZ-20261112093100.xlsx"])
        self.assertTrue(os.path.exists(os.path.join(bk, "NOT_USED__abcXYZ-20261112093100.xlsx")))
        self.assertTrue(os.path.exists(old) and os.path.exists(other))


if __name__ == "__main__":
    unittest.main()
