import datetime as dt
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "sa_codes"))

from core import short_allocation as SA   # noqa: E402
from core import sa_auto as AU            # noqa: E402
import sa_peak                            # noqa: E402


class PeakNumbers(unittest.TestCase):
    def test_every_run_uses_its_own_peak_file_1_to_4(self):
        got = [sa_peak.suffixes(i) for i in range(1, 5)]
        self.assertEqual(got[3], ("i04", "i04", "04"))
        for n in (1, 2, 3, 4):
            self.assertEqual(got[n - 1], (f"i{n:02d}", f"i{n:02d}", f"{n:02d}"))
        self.assertFalse(sa_peak.reuses_cf(4))

    def test_runs_5_to_8_no_longer_exist(self):
        for n in (5, 6, 7, 8, 9):
            with self.assertRaises(ValueError):
                sa_peak.suffixes(n)
        self.assertEqual((SA.MAX_RUN, AU.MAX_RUN, sa_peak.MAX_RUN), (4, 4, 4))

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
        for k in ("SA_RUN1_TIME", "SA_AUTO_RUN1", "SA_RUN5_TIME", "SA_AUTO_RUN5", "SA_WA_FAILURE_ALERT"):
            os.environ.pop(k, None)

    def tearDown(self):
        SA.LOG_DIR, SA.STATUS_FILE = self.old
        self.tmp.cleanup()


class Settings(Base):
    def test_only_runs_1_to_4_exist_all_manual_by_default(self):
        st = AU.get_settings()
        self.assertEqual(sorted(st["runs"]), ["1", "2", "3", "4"])
        self.assertEqual([st["runs"][str(n)]["auto"] for n in range(1, 5)], [False] * 4)
        self.assertEqual((st["predl_min"], st["fresh_min"], st["wait_min"], st["retries"]), (15, 45, 15, 2))
        for n in range(1, 5):                            # an automatic run waits the same 15 minutes whichever run it is
            self.assertEqual(AU.wait_minutes(n, st), 15)

    def test_save_and_validate(self):
        AU.save_settings({"runs": {"4": {"time": "21:45", "auto": True}}, "wait_min": 20})
        st = AU.get_settings()
        self.assertEqual(st["runs"]["4"], {"time": "21:45", "auto": True})
        self.assertEqual(st["wait_min"], 20)
        with self.assertRaises(ValueError):
            AU.save_settings({"runs": {"4": {"time": "25:99"}}})
        for n in ("5", "8", "9"):
            with self.assertRaises(ValueError):
                AU.save_settings({"runs": {n: {"auto": True}}})

    def test_only_four_run_windows_exist(self):
        self.assertEqual(sorted(SA.cfg()["windows"]), [1, 2, 3, 4])


class Timer(Base):
    def at(self, hh, mm):
        return dt.datetime(2026, 10, 28, hh, mm)

    def auto1(self):
        """Settings with run 1 switched on at 17:45 (nothing is automatic by default any more)."""
        st = AU.get_settings()
        st["runs"]["1"].update(auto=True, time="17:45")
        return st

    def test_deposit_fifteen_minutes_before_then_the_run(self):
        st, state = self.auto1(), {"date": "x", "runs": {}}
        self.assertIsNone(AU.due_action(self.at(17, 29), st, state))
        self.assertEqual(AU.due_action(self.at(17, 30), st, state), ("deposit", 1))
        state["runs"]["1"] = {"deposit": "done"}
        self.assertIsNone(AU.due_action(self.at(17, 44), st, state))
        self.assertEqual(AU.due_action(self.at(17, 45), st, state), ("run", 1))

    def test_nothing_is_automatic_by_default(self):
        self.assertIsNone(AU.due_action(self.at(17, 45), AU.get_settings(), {"date": "x", "runs": {}}))

    def test_one_attempt_per_day_and_missed_after_grace(self):
        st = self.auto1()
        for status in ("done", "failed", "running", "skipped", "stopped", "waiting for peak file (15 min)"):
            self.assertIsNone(AU.due_action(self.at(17, 50), st, {"date": "x", "runs": {"1": {"status": status, "deposit": "done"}}}), status)
        self.assertEqual(AU.due_action(self.at(19, 20), st, {"date": "x", "runs": {"1": {"deposit": "done"}}})[0:2], ("missed", 1))

    def test_a_run_done_by_hand_is_not_started_again(self):
        SA._record("SA-01", overall="done", code2="done")
        self.assertEqual(AU.due_action(self.at(17, 50), self.auto1(), {"date": "x", "runs": {}}), ("mark_done", 1))

    def test_does_nothing_while_a_run_is_going_on(self):
        SA._SA["running"] = True
        try:
            self.assertIsNone(AU.tick(self.at(17, 50)))
        finally:
            SA._SA["running"] = False


class Numbers(Base):
    def test_ten_digit_numbers_and_names_are_stored_without_a_space_before_the_hash(self):
        import tempfile as tf
        with tf.TemporaryDirectory() as d:
            old = SA.ENV_FILE
            SA.ENV_FILE = os.path.join(d, ".env")
            try:
                SA.update_env({"SA_WA_FAILURE_ALERT": "9811122233 #Kartavya, +919876543210"})
                txt = open(SA.ENV_FILE, encoding="utf-8").read()
            finally:
                SA.ENV_FILE = old
        self.assertIn("SA_WA_FAILURE_ALERT=+919811122233#Kartavya,+919876543210", txt)

    def test_a_bad_number_is_refused_in_plain_words(self):
        with self.assertRaises(ValueError) as cm:
            SA.update_env({"SA_WA_FAILURE_ALERT": "12345"})
        self.assertIn("10-digit", str(cm.exception))

    def test_stored_form_is_read_back_by_the_alert(self):
        os.environ["SA_WA_FAILURE_ALERT"] = "+919811122233#Kartavya Dhillon,+919876543210"
        try:
            self.assertEqual(AU.alert_numbers(), ["+919811122233", "+919876543210"])
        finally:
            os.environ.pop("SA_WA_FAILURE_ALERT", None)


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
                r = AU.step_deposit(1, download=lambda: calls.append(1) or (True, "ok"))
            finally:
                SA.cfg = old
            self.assertEqual(r["status"], "skipped")
            self.assertEqual(calls, [])

    def test_step_deposit_makes_one_go_and_never_exports_twice(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["TCS_SESSION_LOCK_DIR"] = d
            old = SA.cfg
            SA.cfg = lambda: {**old(), "output": d}
            seq = [(False, "token refused"), (True, "done")]
            try:
                r = AU.step_deposit(1, download=lambda: seq.pop(0), sleep=lambda s: None)
            finally:
                SA.cfg = old
                os.environ.pop("TCS_SESSION_LOCK_DIR", None)
            self.assertEqual(r["status"], "failed")          # the downloader already did its own 5 + 5 minute re-logins
            self.assertEqual(len(seq), 1)                     # and no second download (= no second export) was started


class RunSlot(Base):
    def test_missing_peak_waits_15_minutes_twice_then_alerts(self):
        sleeps, alerts, tries = [], [], []

        def run(n):
            tries.append(n)
            return {"ok": False, "msg": "Download failed", "peak_missing": "MCX_PeakMargin56720_20261028_01.csv", "stopped": False}
        rs = AU.run_slot(1, run=run, sleep=sleeps.append, alert=lambda n, h, d: alerts.append((n, h, d)) or {"sent": True})
        self.assertEqual(len(tries), 3)                      # first try + 2 retries
        self.assertEqual(sleeps, [900, 900])                 # 15 minutes each
        self.assertEqual(rs["status"], "failed")
        self.assertEqual(len(alerts), 1)
        self.assertIn("peak file", alerts[0][1])
        self.assertIn("30 minutes", alerts[0][2])

    def test_no_second_alert_when_the_run_fails_only_because_the_deposit_alert_was_sent(self):
        alerts = []
        with tempfile.TemporaryDirectory() as d:
            old = SA.cfg
            SA.cfg = lambda: {**old(), "output": d}                     # no MCX_DEPOSIT file in the folder
            try:
                AU._upd(1, deposit="failed", deposit_alert_sent=True)
                rs = AU.run_slot(1, run=lambda n: {"ok": False, "msg": "files missing", "peak_missing": None, "stopped": False},
                                 sleep=lambda s: None, alert=lambda n, h, dd: alerts.append(h) or {"sent": True})
            finally:
                SA.cfg = old
        self.assertEqual(alerts, [])
        self.assertEqual(rs["status"], "failed")

    def test_other_errors_still_alert_even_after_a_deposit_alert(self):
        alerts = []
        with tempfile.TemporaryDirectory() as d:
            old = SA.cfg
            SA.cfg = lambda: {**old(), "output": d}
            try:
                AU._upd(1, deposit="failed", deposit_alert_sent=True)
                AU.run_slot(1, run=lambda n: {"ok": False, "msg": "x", "peak_missing": "MCX_PeakMargin.csv", "stopped": False},
                            sleep=lambda s: None, alert=lambda n, h, dd: alerts.append(h) or {"sent": True})
            finally:
                SA.cfg = old
        self.assertEqual(len(alerts), 1)                                 # a missing peak file is a different problem: its alert goes

    def test_run_1_on_auto_waits_15_minutes_not_5(self):
        sleeps = []
        AU.run_slot(1, run=lambda n: {"ok": False, "msg": "x", "peak_missing": "f", "stopped": False}, sleep=sleeps.append,
                    alert=lambda *a: {"sent": False})
        self.assertEqual(sleeps, [900, 900])             # not the old 5 minutes: that is only for a run started by hand

    def test_run_1_on_auto_gets_the_deposit_pre_step(self):
        st = AU.get_settings()
        st["runs"]["1"]["auto"] = True
        st["runs"]["1"]["time"] = t = "08:30"
        h, m = [int(x) for x in t.split(":")]
        pre = dt.datetime(2026, 10, 28, h, m) - dt.timedelta(minutes=15)
        self.assertEqual(AU.due_action(pre, st, {"date": "x", "runs": {}}), ("deposit", 1))

    def test_success_on_the_second_try_sends_nothing(self):
        seq = [{"ok": False, "msg": "x", "peak_missing": "f", "stopped": False}, {"ok": True, "msg": "", "peak_missing": None, "stopped": False}]
        alerts = []
        rs = AU.run_slot(2, run=lambda n: seq.pop(0), sleep=lambda s: None, alert=lambda *a: alerts.append(a) or {})
        self.assertEqual(rs["status"], "done")
        self.assertEqual(alerts, [])

    def test_other_failure_alerts_at_once_without_waiting(self):
        sleeps, alerts = [], []
        rs = AU.run_slot(1, run=lambda n: {"ok": False, "msg": "SA Processing failed", "peak_missing": None, "stopped": False},
                         sleep=sleeps.append, alert=lambda n, h, d: alerts.append(h) or {"sent": True})
        self.assertEqual(sleeps, [])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(rs["status"], "failed")

    def test_stopped_by_a_person_is_not_retried_or_alerted(self):
        alerts = []
        rs = AU.run_slot(1, run=lambda n: {"ok": False, "msg": "stopped", "peak_missing": None, "stopped": True},
                         sleep=lambda s: None, alert=lambda *a: alerts.append(a) or {})
        self.assertEqual(rs["status"], "stopped")
        self.assertEqual(alerts, [])

    def test_alert_without_a_number_says_why(self):
        r = AU.send_alert(1, "h", "d")
        self.assertFalse(r["sent"])
        self.assertIn("FAILURE ALERT", r["reason"])

    def test_alert_goes_to_the_failure_alert_numbers(self):
        os.environ["SA_WA_FAILURE_ALERT"] = "9812345678, +919876543210"
        got = []
        try:
            r = AU.send_alert(1, "h", "d", sender=lambda nums, h, d: got.append(nums))
        finally:
            os.environ.pop("SA_WA_FAILURE_ALERT", None)
        self.assertTrue(r["sent"])
        self.assertEqual(got[0], ["+919812345678", "+919876543210"])


if __name__ == "__main__":
    unittest.main()


class CheckModeStops(Base):
    def test_run_stops_when_the_collateral_files_cannot_be_downloaded(self):
        self.assertEqual(SA.cl_runs(), {5, 6})
        with mock.patch.dict(os.environ, {"SA_CL_RUNS": "off"}):
            self.assertEqual(SA.cl_runs(), set())
