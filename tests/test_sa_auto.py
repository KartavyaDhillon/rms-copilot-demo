import datetime as dt
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "sa_codes"))

from core import short_allocation as SA   # noqa: E402
from core import sa_auto as AU            # noqa: E402
import sa_peak                            # noqa: E402


class PeakNumbers(unittest.TestCase):
    def test_cash_and_fo_stop_at_4_the_rest_go_to_8(self):
        got = [sa_peak.suffixes(i) for i in range(1, 9)]
        self.assertEqual(got[3], ("i04", "i04", "04"))
        for n in (5, 6, 7, 8):
            cf, co, mx = got[n - 1]
            self.assertEqual(cf, "i04")                  # cash / fo: the 4th file
            self.assertEqual(co, f"i{n:02d}")            # CO and MCX/NCCL: their own file
            self.assertEqual(mx, f"{n:02d}")
        self.assertFalse(sa_peak.reuses_cf(4))
        self.assertTrue(sa_peak.reuses_cf(5))

    def test_run_9_is_refused(self):
        with self.assertRaises(ValueError):
            sa_peak.suffixes(9)

    def test_4th_cash_fo_files_are_copied_back_from_run_4_backup(self):
        with tempfile.TemporaryDirectory() as d:
            bk = os.path.join(d, "BACKUP FILES AUTOMATED", "28-10-2026", "04")
            os.makedirs(bk)
            for seg in "CF":
                open(os.path.join(bk, f"{seg}_SA01_P_06436_28102026_i04.lis"), "w").write("x")
            back = sa_peak.restore_cf_from_backup(d, "i04", "28-10-2026", log=lambda *_: None)
            self.assertEqual(len(back), 2)
            self.assertTrue(os.path.exists(os.path.join(d, "C_SA01_P_06436_28102026_i04.lis")))
            self.assertTrue(os.path.exists(os.path.join(bk, "C_SA01_P_06436_28102026_i04.lis")))   # the backup stays whole
            self.assertEqual(sa_peak.restore_cf_from_backup(d, "i04", "28-10-2026", log=lambda *_: None), [])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = SA.LOG_DIR, SA.STATUS_FILE
        SA.LOG_DIR = self.tmp.name
        SA.STATUS_FILE = os.path.join(self.tmp.name, "_run_status.json")
        for k in ("SA_RUN5_TIME", "SA_AUTO_RUN5", "SA_WA_FAILURE_ALERT"):
            os.environ.pop(k, None)

    def tearDown(self):
        SA.LOG_DIR, SA.STATUS_FILE = self.old
        self.tmp.cleanup()


class Settings(Base):
    def test_defaults_runs_5_and_6_auto_with_the_asked_times(self):
        st = AU.get_settings()
        self.assertEqual([st["runs"][str(n)]["time"] for n in (5, 6, 7, 8)], ["17:45", "19:30", "21:30", "23:30"])
        self.assertEqual([st["runs"][str(n)]["auto"] for n in range(1, 9)], [False] * 4 + [True, True, False, False])
        self.assertEqual((st["predl_min"], st["fresh_min"], st["wait_min"], st["wait_min_56"], st["retries"]), (10, 45, 5, 15, 2))
        self.assertEqual(AU.wait_minutes(5, st), 15)
        self.assertEqual(AU.wait_minutes(7, st), 5)

    def test_save_and_validate(self):
        AU.save_settings({"runs": {"7": {"time": "21:45", "auto": True}}, "wait_min_56": 20})
        st = AU.get_settings()
        self.assertEqual(st["runs"]["7"], {"time": "21:45", "auto": True})
        self.assertEqual(st["wait_min_56"], 20)
        with self.assertRaises(ValueError):
            AU.save_settings({"runs": {"5": {"time": "25:99"}}})
        with self.assertRaises(ValueError):
            AU.save_settings({"runs": {"9": {"auto": True}}})

    def test_windows_of_runs_5_to_8_exist(self):
        w = SA.cfg()["windows"]
        self.assertEqual(w[5], "17:45-19:29")
        self.assertEqual(w[8], "23:30-23:59")


class Timer(Base):
    def at(self, hh, mm):
        return dt.datetime(2026, 10, 28, hh, mm)

    def test_deposit_ten_minutes_before_then_the_run(self):
        st, state = AU.get_settings(), {"date": "x", "runs": {}}
        self.assertIsNone(AU.due_action(self.at(17, 34), st, state))
        self.assertEqual(AU.due_action(self.at(17, 35), st, state), ("deposit", 5))
        state["runs"]["5"] = {"deposit": "done"}
        self.assertIsNone(AU.due_action(self.at(17, 44), st, state))
        self.assertEqual(AU.due_action(self.at(17, 45), st, state), ("run", 5))

    def test_runs_7_and_8_have_no_deposit_step_and_are_off_by_default(self):
        st = AU.get_settings()
        done = {"date": "x", "runs": {"5": {"status": "done"}, "6": {"status": "done"}}}
        self.assertIsNone(AU.due_action(self.at(21, 35), st, done))        # 7 is off: nothing, and no deposit step for it anyway
        st["runs"]["7"]["auto"] = True
        self.assertIsNone(AU.due_action(self.at(21, 20), st, done))        # no deposit pre-step for run 7
        self.assertEqual(AU.due_action(self.at(21, 31), st, done), ("run", 7))

    def test_one_attempt_per_day_and_missed_after_grace(self):
        st = AU.get_settings()
        for status in ("done", "failed", "running", "skipped", "stopped", "waiting for peak file (15 min)"):
            self.assertIsNone(AU.due_action(self.at(17, 50), st, {"date": "x", "runs": {"5": {"status": status, "deposit": "done"}}}), status)
        self.assertEqual(AU.due_action(self.at(19, 20), st, {"date": "x", "runs": {"5": {"deposit": "done"}}})[0:2], ("missed", 5))

    def test_a_run_done_by_hand_is_not_started_again(self):
        SA._record("SA-05", overall="done", code2="done")
        self.assertEqual(AU.due_action(self.at(17, 50), AU.get_settings(), {"date": "x", "runs": {}}), ("mark_done", 5))

    def test_does_nothing_while_a_run_is_going_on(self):
        SA._SA["running"] = True
        try:
            self.assertIsNone(AU.tick(self.at(17, 50)))
        finally:
            SA._SA["running"] = False


class Deposit(Base):
    def test_fresh_file_skips_and_old_file_downloads(self):
        with tempfile.TemporaryDirectory() as d:
            st = AU.get_settings()
            self.assertEqual(AU.deposit_is_fresh(st, folder=d), (False, None))
            f = os.path.join(d, "MCX_DEPOSIT_17.20.xlsx")
            open(f, "w").write("x")
            self.assertTrue(AU.deposit_is_fresh(st, folder=d)[0])
            old = time.time() - 46 * 60
            os.utime(f, (old, old))
            self.assertFalse(AU.deposit_is_fresh(st, folder=d)[0])
            os.utime(f, (time.time() - 44 * 60,) * 2)
            self.assertTrue(AU.deposit_is_fresh(st, folder=d)[0])

    def test_step_deposit_skips_when_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "MCX_DEPOSIT_17.20.csv"), "w").write("x")
            old = SA.cfg
            SA.cfg = lambda: {**old(), "output": d}
            calls = []
            try:
                r = AU.step_deposit(5, download=lambda: calls.append(1) or (True, "ok"))
            finally:
                SA.cfg = old
            self.assertEqual(r["status"], "skipped")
            self.assertEqual(calls, [])

    def test_step_deposit_downloads_and_retries_once(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["TCS_SESSION_LOCK_DIR"] = d
            old = SA.cfg
            SA.cfg = lambda: {**old(), "output": d}
            seq = [(False, "token refused"), (True, "done")]
            try:
                r = AU.step_deposit(5, download=lambda: seq.pop(0), sleep=lambda s: None)
            finally:
                SA.cfg = old
                os.environ.pop("TCS_SESSION_LOCK_DIR", None)
            self.assertEqual(r["status"], "done")
            self.assertEqual(seq, [])


class RunSlot(Base):
    def test_missing_peak_waits_15_minutes_twice_then_alerts(self):
        sleeps, alerts, tries = [], [], []

        def run(n):
            tries.append(n)
            return {"ok": False, "msg": "Download failed", "peak_missing": "MCX_PeakMargin56720_20261028_05.csv", "stopped": False}
        rs = AU.run_slot(5, run=run, sleep=sleeps.append, alert=lambda n, h, d: alerts.append((n, h, d)) or {"sent": True})
        self.assertEqual(len(tries), 3)                      # first try + 2 retries
        self.assertEqual(sleeps, [900, 900])                 # 15 minutes each (runs 5 and 6)
        self.assertEqual(rs["status"], "failed")
        self.assertEqual(len(alerts), 1)
        self.assertIn("peak file", alerts[0][1])
        self.assertIn("30 minutes", alerts[0][2])

    def test_run_7_waits_5_minutes(self):
        sleeps = []
        AU.run_slot(7, run=lambda n: {"ok": False, "msg": "x", "peak_missing": "f", "stopped": False}, sleep=sleeps.append,
                    alert=lambda *a: {"sent": False})
        self.assertEqual(sleeps, [300, 300])

    def test_success_on_the_second_try_sends_nothing(self):
        seq = [{"ok": False, "msg": "x", "peak_missing": "f", "stopped": False}, {"ok": True, "msg": "", "peak_missing": None, "stopped": False}]
        alerts = []
        rs = AU.run_slot(6, run=lambda n: seq.pop(0), sleep=lambda s: None, alert=lambda *a: alerts.append(a) or {})
        self.assertEqual(rs["status"], "done")
        self.assertEqual(alerts, [])

    def test_other_failure_alerts_at_once_without_waiting(self):
        sleeps, alerts = [], []
        rs = AU.run_slot(5, run=lambda n: {"ok": False, "msg": "SA Processing failed", "peak_missing": None, "stopped": False},
                         sleep=sleeps.append, alert=lambda n, h, d: alerts.append(h) or {"sent": True})
        self.assertEqual(sleeps, [])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(rs["status"], "failed")

    def test_stopped_by_a_person_is_not_retried_or_alerted(self):
        alerts = []
        rs = AU.run_slot(5, run=lambda n: {"ok": False, "msg": "stopped", "peak_missing": None, "stopped": True},
                         sleep=lambda s: None, alert=lambda *a: alerts.append(a) or {})
        self.assertEqual(rs["status"], "stopped")
        self.assertEqual(alerts, [])

    def test_alert_without_a_number_says_why(self):
        r = AU.send_alert(5, "h", "d")
        self.assertFalse(r["sent"])
        self.assertIn("FAILURE ALERT", r["reason"])

    def test_alert_goes_to_the_failure_alert_numbers(self):
        os.environ["SA_WA_FAILURE_ALERT"] = "9812345678, +919876543210"
        got = []
        try:
            r = AU.send_alert(5, "h", "d", sender=lambda nums, h, d: got.append(nums))
        finally:
            os.environ.pop("SA_WA_FAILURE_ALERT", None)
        self.assertTrue(r["sent"])
        self.assertEqual(got[0], ["+919812345678", "+919876543210"])


if __name__ == "__main__":
    unittest.main()


class OrchestratorRuns5to8(Base):
    """stream_short_allocation with stand-in scripts: runs 5-8 do Download + SA Processing and never LAS Funding."""

    def setUp(self):
        super().setUp()
        self.d = tempfile.TemporaryDirectory()
        r = self.d.name
        self.codes = os.path.join(r, "codes"); os.makedirs(self.codes)
        self.state = os.path.join(r, "run_state.txt")
        open(self.state, "w").write(f"DATE={dt.date.today().isoformat()}\nRUN=5\nRETRY=0\n")
        self.flag = os.path.join(r, "missing.flag")
        self.las = os.path.join(r, "las_ran.txt")
        open(os.path.join(self.codes, SA.CODE1), "w").write(
            "import os,sys\n"
            f"if os.path.exists(r'{self.flag}') and os.getenv('SA_AUTO_RUN')=='1':\n"
            "    print('[SA_PEAK_MISSING] MCX_PeakMargin_05.csv'); sys.exit(3)\n"
            "print('downloaded'); sys.exit(0)\n")
        open(os.path.join(self.codes, SA.CODE2), "w").write("print('processed')\n")
        for s in (SA.CODE3, SA.CODE4):
            open(os.path.join(self.codes, s), "w").write(f"open(r'{self.las}','w').write('x')\n")
        self.env = {"SA_CODES_DIR": self.codes, "SA_STATE_FILE": self.state, "SA_OUTPUT_DIR": r,
                    "SA_LAS_FUNDING_DIR": r, "SA_PYTHON": sys.executable}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        import core.env_boot as eb
        self.eb_reload, eb.reload = eb.reload, (lambda *a, **k: None)   # keep the test's paths

    def tearDown(self):
        import core.env_boot as eb
        eb.reload = self.eb_reload
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.d.cleanup()
        super().tearDown()

    def test_run_5_downloads_processes_and_never_runs_las(self):
        evs = list(SA.stream_short_allocation("05"))
        self.assertTrue(any(e.get("type") == "done" and e.get("ok") for e in evs), evs)
        self.assertFalse(os.path.exists(self.las))
        self.assertEqual(SA.get_run_status("SA-05").get("overall"), "done")

    def test_auto_run_reports_the_missing_peak_file_without_a_popup(self):
        open(self.flag, "w").write("x")
        res = AU._run_stream(5)
        self.assertFalse(res["ok"])
        self.assertIn("MCX_PeakMargin_05.csv", res["peak_missing"] or "")
        self.assertFalse(os.path.exists(self.las))

    def test_same_missing_file_does_not_stop_a_manual_run_from_the_normal_flow(self):
        open(self.flag, "w").write("x")                       # not auto: the script behaves as before (exit 0 here)
        evs = list(SA.stream_short_allocation("05"))
        self.assertTrue(any(e.get("type") == "done" and e.get("ok") for e in evs), evs)
