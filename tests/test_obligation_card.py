"""OBLIGATION REPORT card: MAIN upload rule, time label, relaxed slots (waiting / ATTENTION after 1 h, never an alert), manual override,
timer pick, run with stand-ins (done / failed alert / no alert for a missing file), backup, MCX child retry rules.
Run:  python -m unittest tests.test_obligation_card -v"""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MAIN_CSV = b"Product,Custodian,Net\n-,ABC,10\n"


class CardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OBLIGATION_DIR"] = self.tmp.name
        os.environ["OBLIGATION_LOG_DIR"] = str(Path(self.tmp.name) / "_logs")
        from core import obligation_card as m
        self.m = m
        m._ACTIVE.update(cancel=False, slot=None, proc=None)
        m.DL_GAP_S = 0
        self.alerts = []
        self.alert = lambda h, d, sender=None: (self.alerts.append((h, d)) or {"sent": True, "reason": "test"})
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def upload(self, name="MAIN_11.30.csv", data=MAIN_CSV):
        return self.m.accept_upload(name, data, by="t", modified_ms=dt.datetime.now().timestamp() * 1000)

    def at(self, h, mi):
        d = dt.date.today()
        return dt.datetime(d.year, d.month, d.day, h, mi)

    def fns(self, fail_fo=False):
        def fo(work):
            if fail_fo:
                raise RuntimeError("NMASS down")
            p = Path(work) / f"FO_ClientLevelCollaterals_{dt.date.today():%d%m%Y}123133.csv"
            p.write_text("a\n1\n")
            return p

        def mcx(work):
            p = Path(work) / "SearchResults_1.xlsx"
            p.write_bytes(b"x")
            return p

        def build(main, fo_f, sr, work):
            p = Path(work) / "Obligation_Report_Detailed.png"
            p.write_bytes(b"png")
            return p
        return {"fo": fo, "mcx": mcx, "build": build,
                "mail": lambda img, t: self.sent.append(("mail", t)), "whatsapp": lambda img, d: self.sent.append(("wa", d))}

    # ---- upload / time label ----
    def test_time_label_follows_the_file_name(self):
        self.assertEqual(self.m.main_time("MAIN_11.30.csv"), ("11:30 AM", "11-30-AM"))
        self.assertEqual(self.m.main_time("MAIN_14.30.csv"), ("2:30 PM", "2-30-PM"))
        self.assertEqual(self.m.main_time("MAIN_12.30.csv"), ("12:30 PM", "12-30-PM"))
        with self.assertRaises(ValueError):
            self.m.main_time("report.csv")

    def test_upload_rules(self):
        self.assertTrue(self.upload()["ok"])
        self.assertTrue(self.m.input_status()[0]["ok"])
        with self.assertRaises(ValueError):
            self.m.accept_upload("other.csv", MAIN_CSV, modified_ms=dt.datetime.now().timestamp() * 1000)
        with self.assertRaises(ValueError):
            self.m.accept_upload("MAIN_10.30.csv", b"a,b\n1,2\n", modified_ms=dt.datetime.now().timestamp() * 1000)
        old = (dt.datetime.now() - dt.timedelta(minutes=90)).timestamp() * 1000
        with self.assertRaises(ValueError):
            self.m.accept_upload("MAIN_10.30.csv", MAIN_CSV, modified_ms=old)
        self.upload("MAIN_12.30.csv")                                   # a new MAIN replaces the old one
        self.assertEqual(self.m.main_path().name, "MAIN_12.30.csv")

    # ---- settings / slots ----
    def test_default_eight_times_and_editable(self):
        self.assertEqual(len(self.m.slot_times()), 8)
        self.m.save_settings({"times": "10:30, 09:30, 12:30"})
        self.assertEqual([self.m.hhmm(x) for x in self.m.slot_times()], ["09:30", "10:30", "12:30"])
        with self.assertRaises(ValueError):
            self.m.save_settings({"times": "25:00"})
        with self.assertRaises(ValueError):
            self.m.save_settings({"report_numbers": "abc"})

    def test_late_slot_is_waiting_then_attention_never_an_alert(self):
        rows = self.m.slot_rows(self.at(10, 0))
        self.assertEqual(rows[0]["state"], "waiting")                    # 09:30, 30 min late
        self.assertEqual(rows[1]["state"], "upcoming")
        rows = self.m.slot_rows(self.at(10, 45))
        self.assertEqual(rows[0]["state"], "attention")                  # more than 60 min late
        self.assertIn("09:30", self.m.attention_notes(rows)[0])
        self.assertEqual(self.alerts, [])

    def test_earlier_slots_are_missed_not_attention_once_a_later_one_is_done(self):
        self.m.override_slots([2], by="t", now=self.at(11, 0))
        rows = self.m.slot_rows(self.at(11, 20))
        self.assertEqual(rows[0]["state"], "missed")
        self.assertEqual(rows[1]["state"], "done")
        self.assertEqual(self.m.attention_notes(rows), [])

    def test_override_counts_and_continues_from_next_slot(self):
        r = self.m.override_slots([1, 2], by="t", now=self.at(11, 0))
        self.assertEqual((r["done"], r["total"]), (2, 8))
        self.assertEqual(self.m.pick_target(dt.date.today(), self.at(11, 40)), 3)
        self.m.override_slots([1], by="t", now=self.at(11, 0))            # un-tick slot 2
        self.assertEqual(self.m.done_count(dt.date.today()), 1)
        with self.assertRaises(ValueError):
            self.m.override_slots([9])

    def test_pick_target_hand_run_early_and_auto_never_early(self):
        d = dt.date.today()
        self.assertEqual(self.m.pick_target(d, self.at(9, 25)), 1)       # 5 min early counts for 09:30
        self.assertIsNone(self.m.pick_target(d, self.at(9, 0), auto=True))
        self.assertEqual(self.m.pick_target(d, self.at(9, 0)), 1)        # a hand run before everything takes the first pending
        self.assertEqual(self.m.pick_target(d, self.at(12, 40), auto=True), 4)   # the latest slot whose time is here

    # ---- timer ----
    def test_timer_needs_todays_main_and_never_alerts(self):
        now = self.at(10, 40)
        if now.weekday() >= 5:
            self.skipTest("weekend")
        self.assertIsNone(self.m.due_slot(now))                          # no MAIN file: nothing happens, nothing is sent
        self.upload()
        self.assertEqual(self.m.due_slot(now), 2)                        # 10:30 is the latest slot whose time is here
        self.m.save_settings({"auto": False})
        self.assertIsNone(self.m.due_slot(now))

    def test_timer_tries_a_slot_twice_with_a_gap(self):
        now = self.at(10, 40)
        if now.weekday() >= 5:
            self.skipTest("weekend")
        self.upload()
        d = dt.date.today()
        self.m._slot_update(d, 2, auto_attempts=1, last_auto=(now - dt.timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S"))
        self.assertIsNone(self.m.due_slot(now))
        self.m._slot_update(d, 2, last_auto=(now - dt.timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(self.m.due_slot(now), 2)
        self.m._slot_update(d, 2, auto_attempts=2)
        self.assertIsNone(self.m.due_slot(now))

    # ---- run ----
    def test_run_success_sends_files_the_main_and_marks_the_slot(self):
        self.upload("MAIN_11.30.csv")
        r = self.m.run("manual", "t", alert=self.alert, step_fns=self.fns(), now=self.at(11, 35))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["slot"], 3)
        self.assertEqual(self.sent, [("mail", "11:30 AM"), ("wa", dt.date.today())])
        self.assertEqual(self.alerts, [])
        self.assertIsNone(self.m.main_path())                           # the MAIN file was used up
        day = self.m.backup_root() / dt.date.today().strftime("%d-%m-%Y") / "11-30-AM"
        self.assertTrue((day / "MAIN_11.30.csv").is_file() and (day / "Obligation_Report_Detailed.png").is_file())
        st = self.m.status(self.at(11, 36))
        self.assertEqual(st["done"], 1)
        self.assertFalse((self.m.root() / "_work").exists() and any((self.m.root() / "_work").iterdir()))

    def test_failed_run_alerts_once_and_keeps_the_main_file(self):
        self.upload()
        r = self.m.run("manual", "t", alert=self.alert, step_fns=self.fns(fail_fo=True), now=self.at(11, 35))
        self.assertFalse(r["ok"])
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("NMASS down", self.alerts[0][1])
        self.assertIsNotNone(self.m.main_path())
        self.assertEqual(self.m.slot_rows(self.at(11, 36))[2]["state"], "failed")

    def test_timer_failure_alerts_only_on_the_last_try(self):
        self.upload()
        d = dt.date.today()
        self.m._slot_update(d, 3, auto_attempts=1)
        self.m.run("auto", "timer", alert=self.alert, step_fns=self.fns(fail_fo=True), now=self.at(11, 35), slot=3)
        self.assertEqual(self.alerts, [])                                # one more try is coming
        self.m._slot_update(d, 3, auto_attempts=2)
        self.m.run("auto", "timer", alert=self.alert, step_fns=self.fns(fail_fo=True), now=self.at(11, 45), slot=3)
        self.assertEqual(len(self.alerts), 1)

    def test_run_without_main_file_is_not_an_alert(self):
        r = self.m.run("manual", "t", alert=self.alert, step_fns=self.fns(), now=self.at(11, 35))
        self.assertFalse(r["ok"])
        self.assertEqual(self.alerts, [])

    def test_old_fo_file_fails_the_run(self):
        self.upload()
        f = self.fns()

        def old_fo(work):
            p = Path(work) / "FO_ClientLevelCollaterals_01012020123133.csv"
            p.write_text("a\n")
            return p
        f["fo"] = old_fo
        r = self.m.run("manual", "t", alert=self.alert, step_fns=f, now=self.at(11, 35))
        self.assertFalse(r["ok"])
        self.assertIn("not today", self.alerts[0][1])

    def test_stop_is_not_a_failure(self):
        self.upload()
        f = self.fns()

        def fo(work):
            self.m._ACTIVE["cancel"] = True
            raise self.m.Cancelled("Stopped.")
        f["fo"] = fo
        r = self.m.run("manual", "t", alert=self.alert, step_fns=f, now=self.at(11, 35))
        self.assertTrue(r.get("stopped"))
        self.assertEqual(self.alerts, [])

    def test_download_retry_skips_wrong_password(self):
        calls = {"n": 0}

        def bad():
            calls["n"] += 1
            raise RuntimeError("MCX login failed - the password looks wrong.")
        with self.assertRaises(RuntimeError):
            self.m._with_retries("MCX", bad, tries=2, gap_s=0)
        self.assertEqual(calls["n"], 1)
        calls["n"] = 0

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("connection reset")
            return "ok"
        self.assertEqual(self.m._with_retries("MCX", flaky, tries=2, gap_s=0), "ok")

    def test_recipient_group_follows_the_system_clock(self):
        g = self.m.report_group
        self.assertEqual(g(self.at(9, 30)), "early")
        self.assertEqual(g(self.at(17, 0)), "early")
        self.assertEqual(g(self.at(17, 1)), "late")
        self.assertEqual(g(self.at(23, 50)), "late")
        self.assertEqual(g(self.at(0, 30)), "late")
        self.assertEqual(g(self.at(0, 31)), "early")
        self.m.save_settings({"split_time": "16:30"})
        self.assertEqual(g(self.at(16, 45)), "late")

    def test_late_mail_list_with_fallback(self):
        self.m.apply_defaults()
        os.environ["OBLIGATION_TO_LATE"], os.environ["OBLIGATION_CC_LATE"] = "night@x.in", ""
        self.assertEqual(self.m._lists("late")[0], ["night@x.in"])
        self.assertIn("sukhbirsingh@mastertrust.co.in", self.m._lists("early")[0])
        os.environ["OBLIGATION_TO_LATE"] = ""
        self.assertIn("sukhbirsingh@mastertrust.co.in", self.m._lists("late")[0])      # blank late list = same people
        self.m.save_settings({"report_numbers_late": "9876543210"})
        with self.assertRaises(ValueError):
            self.m.save_settings({"split_time": "17:00, 18:00"})

    def test_mail_defaults_come_from_the_old_script(self):
        self.m.apply_defaults()
        to = os.environ["OBLIGATION_TO"]
        self.assertIn("sukhbirsingh@mastertrust.co.in", to)
        self.assertIn("sandeep.kumar2@mastertrust.co.in", os.environ["OBLIGATION_CC"])


class McxChildTests(unittest.TestCase):
    def test_attempts_and_auth_stop(self):
        from core import obligation_mcx_cli as C

        class G:
            class GridAuthError(Exception):
                pass

            class GridTokenError(Exception):
                pass

            class _SessionLost(Exception):
                pass
            n = 0

            @classmethod
            def login(cls, *a, **k):
                cls.n += 1
                if cls.n == 1:
                    raise RuntimeError("boom")
                raise cls.GridAuthError("wrong password")
        with self.assertRaises(G.GridAuthError):
            C.download("x", "u", "p", lambda *a: "1", log=lambda m: None, tries=4, sleep=lambda s: None, grid=G,
                       session_factory=lambda: object())
        self.assertEqual(G.n, 2)                                         # one retry after a plain error, then the refused password stops it


if __name__ == "__main__":
    unittest.main()
