"""LIVE ALLOCATION CAPTURE - slots, state, skip, retry-only-missing, zip, MCX token rules. No portal is ever contacted."""
import datetime as dt
import importlib.util
import os
import tempfile
import unittest
import zipfile

from core import live_allocation as L, mcx_soft_token as T


def touch(folder, name, data=b"x"):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as f:
        f.write(data)


def fake_nmass(calls):
    def fn(folder, slot, log, cancel):
        calls.append("nmass")
        for n in ("CM_ClientLevelCollaterals_28102026.csv", "FO_ClientLevelCollaterals_28102026.csv",
                  "CO_ClientLevelCollaterals_28102026.csv", "ClientNetEffectiveDeposits.csv"):
            touch(folder, n)
        return {"ok": True, "msg": "ok"}
    return fn


def fake_mcx(calls, ok=True):
    def fn(folder, slot, log, cancel):
        calls.append("mcx")
        if ok:
            touch(folder, f"MCX_Web_Allocation_{slot:02d}.xlsx")
            return {"ok": True, "msg": "ok"}
        return {"ok": False, "msg": "RSA code refused"}
    return fn


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["LIVE_ALLOC_DIR"] = self.tmp
        os.environ["LIVE_ALLOC_LOG_DIR"] = os.path.join(self.tmp, "_logs")     # never write test logs into the real logs folder
        os.environ["LIVE_ALLOC_TIMES"] = L.DEFAULT_TIMES
        self.saved = dict(L.STEPS)
        self.calls = []
        L.STEPS["nmass_ncdex"] = fake_nmass(self.calls)
        L.STEPS["mcx"] = fake_mcx(self.calls)

    def tearDown(self):
        L.STEPS.update(self.saved)
        os.environ.pop("LIVE_ALLOC_DIR", None)
        os.environ.pop("LIVE_ALLOC_LOG_DIR", None)
        os.environ.pop("LIVE_ALLOC_TIMES", None)


class Slots(Base):
    def test_pick_slot_is_the_latest_slot_that_has_started(self):
        n = lambda h, m: dt.datetime(2026, 10, 28, h, m)
        self.assertEqual(L.pick_slot(n(9, 0)), 1)            # before the first timing -> folder 1
        self.assertEqual(L.pick_slot(n(10, 12)), 1)
        self.assertEqual(L.pick_slot(n(10, 40)), 1)
        self.assertEqual(L.pick_slot(n(12, 5)), 2)            # 5 minutes early is still slot 2
        self.assertEqual(L.pick_slot(n(13, 28)), 3)
        self.assertEqual(L.pick_slot(n(22, 56)), 8)

    def test_times_are_validated(self):
        with self.assertRaises(ValueError):
            L._parse_times("10:12, 25:00")
        self.assertEqual(len(L._parse_times(L.DEFAULT_TIMES)), 8)


class Run(Base):
    def test_full_run_fills_folder_n_and_reports_done(self):
        rec = L.run_slot(3, trigger="schedule", log=lambda m: None)
        self.assertEqual(rec["status"], "done")
        d = dt.date.today()
        self.assertEqual(sorted(os.listdir(L.slot_dir(d, 3))), sorted([
            "CM_ClientLevelCollaterals_28102026.csv", "FO_ClientLevelCollaterals_28102026.csv",
            "CO_ClientLevelCollaterals_28102026.csv", "ClientNetEffectiveDeposits.csv", "MCX_Web_Allocation_03.xlsx"]))
        self.assertEqual(L.status(d)["slots"][2]["state"], "done")
        self.assertEqual(L.status(d)["counts"]["done"], 1)

    def test_one_portal_failing_does_not_stop_the_other_and_retry_fetches_only_what_is_missing(self):
        L.STEPS["mcx"] = fake_mcx(self.calls, ok=False)
        rec = L.run_slot(2, log=lambda m: None)
        self.assertEqual(self.calls, ["nmass", "mcx"])        # MCX still ran after NMASS, and failed on its own
        self.assertEqual(rec["status"], "failed")
        self.assertIn("RSA code refused", rec["msg"])
        self.assertEqual(L.status(dt.date.today())["slots"][1]["state"], "failed")
        self.calls.clear()
        L.STEPS["mcx"] = fake_mcx(self.calls, ok=True)
        rec = L.run_slot(2, log=lambda m: None)               # retry
        self.assertEqual(self.calls, ["mcx"])                 # NMASS files were there: not downloaded again
        self.assertEqual(rec["status"], "done")

    def test_a_crashing_step_is_caught(self):
        def boom(folder, slot, log, cancel):
            raise RuntimeError("portal down")
        L.STEPS["nmass_ncdex"] = boom
        rec = L.run_slot(1, log=lambda m: None)
        self.assertEqual(rec["status"], "failed")
        self.assertIn("portal down", rec["msg"])
        self.assertIn("mcx", self.calls)

    def test_busy_while_another_slot_runs(self):
        self.assertTrue(L._RUN_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(L.Busy):
                L.run_slot(4, log=lambda m: None)
        finally:
            L._RUN_LOCK.release()

    def test_skip_blocks_the_timer_but_not_a_manual_run(self):
        L.skip_slot(5, by="ops")
        self.assertEqual(L.status(dt.date.today())["slots"][4]["state"], "skipped")
        rec = L.run_slot(5, trigger="schedule", log=lambda m: None)
        self.assertEqual(rec["status"], "skipped")
        self.assertEqual(self.calls, [])                      # nothing was downloaded
        rec = L.run_slot(5, trigger="manual", log=lambda m: None)
        self.assertEqual(rec["status"], "done")

    def test_skipping_the_running_slot_stops_it_so_the_next_is_not_blocked(self):
        def slow(folder, slot, log, cancel):
            L.skip_slot(slot, by="ops")                       # the operator presses Skip while it runs
            self.assertTrue(cancel())
            return {"ok": False, "msg": "Stopped."}
        L.STEPS["nmass_ncdex"] = slow
        rec = L.run_slot(6, log=lambda m: None)
        self.assertEqual(rec["status"], "skipped")
        self.assertFalse(L.is_running())
        L.STEPS["nmass_ncdex"] = fake_nmass(self.calls)
        rec = L.run_slot(7, log=lambda m: None)               # the next slot runs normally
        self.assertEqual(rec["status"], "done")


class Files(Base):
    def test_zip_keeps_the_date_slot_file_layout_and_names_cannot_escape(self):
        L.run_slot(1, log=lambda m: None)
        L.run_slot(2, log=lambda m: None)
        d = dt.date.today()
        z = L.zip_folder(d)
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
        self.assertIn(f"{L.dstr(d)}/1/MCX_Web_Allocation_01.xlsx", names)
        self.assertIn(f"{L.dstr(d)}/2/ClientNetEffectiveDeposits.csv", names)
        self.assertTrue(all(n.startswith(L.dstr(d) + "/") for n in names))
        one = L.zip_folder(d, 2)
        with zipfile.ZipFile(one) as zf:
            self.assertTrue(all("/2/" in n for n in zf.namelist()))
        for bad in ("../x", "..\\x", ".cancel", "a/b"):
            with self.assertRaises((ValueError, FileNotFoundError)):
                L.file_path(d, 1, bad)

    def test_status_of_a_slot_whose_time_has_not_come(self):
        early = dt.datetime.now().replace(hour=6, minute=0)
        s = L.status(early.date(), now=early)
        self.assertTrue(all(x["state"] == "upcoming" for x in s["slots"]))
        self.assertEqual(s["next"]["slot"], 1)
        late = early.replace(hour=23, minute=30)
        self.assertTrue(all(x["state"] == "missed" for x in L.status(late.date(), now=late)["slots"]))


class FakeTok:
    interval = 60

    def __init__(self):
        self.asked = []

    def time_left(self, now):
        return 20

    def at(self, when):
        self.asked.append(when)
        return "CODE@" + when.strftime("%H:%M:%S")


class TokenRules(unittest.TestCase):
    def test_the_next_code_is_used_never_the_current_and_a_second_try_waits_for_a_new_window(self):
        clock = [dt.datetime(2026, 10, 28, 10, 0, 5, tzinfo=dt.timezone.utc)]
        slept = []

        def sleep(s):
            slept.append(s)
            clock[0] += dt.timedelta(seconds=s)
        tok = FakeTok()
        c1 = T.next_code(tok, 1, sleep=sleep, now_fn=lambda: clock[0], log=lambda m: None)
        self.assertEqual(tok.asked[0], dt.datetime(2026, 10, 28, 10, 1, 5, tzinfo=dt.timezone.utc))   # now + one interval
        c2 = T.next_code(tok, 2, sleep=sleep, now_fn=lambda: clock[0], log=lambda m: None)
        self.assertEqual(slept, [22])                          # waited out the refused window first (20 s + 2)
        self.assertNotEqual(c1, c2)                            # a refused code is never sent again
        self.assertEqual(T.MAX_TOKEN_TRIES, 2)

    def test_relogin_waits_four_minutes_and_can_be_cancelled(self):
        slept = []
        self.assertTrue(T.relogin_wait(sleep=slept.append, log=lambda m: None))
        self.assertEqual(sum(slept), 240)
        slept.clear()
        flag = [False]

        def sleep(s):
            slept.append(s)
            flag[0] = len(slept) >= 3
        self.assertFalse(T.relogin_wait(cancelled=lambda: flag[0], sleep=sleep, log=lambda m: None))
        self.assertLess(sum(slept), 240)


class DownloaderAutoMode(unittest.TestCase):
    def test_auto_mode_never_opens_the_popup_and_stops_after_two_refusals(self):
        try:
            spec = importlib.util.spec_from_file_location("mcxdl_test", os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sa_codes", "mcx_deposit_downloader.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:                                 # pycryptodome etc. missing in this environment
            self.skipTest(f"downloader not importable here: {e}")
        os.environ["MCX_TOKEN_MODE"] = "auto"
        try:
            mod._AUTO_TOK = FakeTok()
            code = mod._token_popup(1, 2, None)
            self.assertTrue(code.startswith("CODE@"))
            src = open(spec.origin, encoding="utf-8").read()
            self.assertIn('MAX_RETRY = 2 if os.getenv("MCX_TOKEN_MODE"', src)
            self.assertIn("relogin_wait", src)
        finally:
            os.environ.pop("MCX_TOKEN_MODE", None)


if __name__ == "__main__":
    unittest.main()


class ThroughTheScheduler(Base):
    """The real engine + the real process wrapper, only the two portal steps are fake."""
    def setUp(self):
        super().setUp()
        import sqlite3
        from core.scheduler import catalog, engine, rules, store
        self.rules, self.store, self.catalog = rules, store, catalog
        self.db = os.path.join(self.tmp, "t.db")
        store._CONN_FACTORY = lambda: sqlite3.connect(self.db, timeout=15)
        store.ensure_schema()
        os.environ["SCHED_HOLIDAYS_FILE"] = os.path.join(self.tmp, "none.csv")
        os.environ["SCHED_DATA_ROOT"] = os.path.join(self.tmp, "sched")
        catalog.all_defs()
        self.IST = rules.get_tz("Asia/Kolkata")
        today = dt.date.today()
        while today.weekday() > 4:                           # a weekday, so the trading-day check passes
            today -= dt.timedelta(days=1)
        self.day = today
        self.eng = engine.SchedulerEngine(clock=lambda: self.now, synchronous=True, worker_id="T", is_holiday=lambda d: False)

    def tearDown(self):
        os.environ.pop("SCHED_DATA_ROOT", None)
        super().tearDown()

    def at(self, h, m):
        self.now = dt.datetime(self.day.year, self.day.month, self.day.day, h, m, tzinfo=self.IST).astimezone(dt.timezone.utc)
        return self.eng.tick(self.now)

    def test_a_schedule_at_the_eight_times_fills_the_matching_folders(self):
        from core.scheduler import service
        import tools.create_live_allocation_schedule as T
        L.save_settings(auto=False)                          # the card's own timer would otherwise own these slots
        self.now = dt.datetime(self.day.year, self.day.month, self.day.day, 9, 0, tzinfo=self.IST).astimezone(dt.timezone.utc)
        s = service.create({"name": "LA", "process_key": "live_allocation_capture", "params": {}, "tz": "Asia/Kolkata",
                            "rule": {"type": "times", "times": T.TIMES, "days": [0, 1, 2, 3, 4]}, "skip_holidays": False,
                            "overlap_policy": "queue", "missed_policy": "grace", "missed_grace_min": 20}, "t", self.now)
        today = dt.date.today()
        for (h, m, slot) in ((10, 12, 1), (13, 27, 3)):
            self.at(h, m)
            rows = self.store.list_executions(schedule_id=s["id"])
            self.assertTrue(any(r["status"] == "SUCCESS" for r in rows), [r["status"] for r in rows])
            self.assertEqual(L.status(today)["slots"][slot - 1]["state"], "done", slot)
        self.assertEqual(self.calls.count("nmass"), 2)       # only slots 1 and 3 ran


def at(h, m):
    return dt.datetime.combine(dt.date.today(), dt.time(h, m))


class Timer(Base):
    """The built-in timer: starts each folder at its time, retries, honours skip / override / timing changes."""
    def setUp(self):
        super().setUp()
        self._td = L._trading_day
        L._trading_day = lambda d: True

    def tearDown(self):
        L._trading_day = self._td
        super().tearDown()

    def run_it(self, s, force):
        L.run_slot(s, trigger="auto", by="timer", force=force, log=lambda m: None)

    def test_nothing_before_the_time_then_the_folder_starts_at_its_time(self):
        self.assertIsNone(L.tick(at(12, 7), runner=self.run_it))            # 12:07 - folder 2 is not due yet
        self.assertEqual(L.tick(at(12, 10), runner=self.run_it), 2)         # 12:10 - it starts by itself
        self.assertEqual(L.status(dt.date.today(), at(12, 11))["slots"][1]["state"], "done")
        self.assertIsNone(L.tick(at(12, 11), runner=self.run_it))           # captured: left alone

    def test_a_folder_missed_while_the_server_was_down_still_runs_but_not_after_the_next_timing(self):
        self.assertEqual(L.tick(at(12, 50), runner=lambda s, f: None), 2)   # up late, still inside folder 2's window
        d2 = tempfile.mkdtemp()
        os.environ["LIVE_ALLOC_DIR"] = d2
        self.assertEqual(L.tick(at(13, 30), runner=lambda s, f: None), 3)   # 13:27 has started: folder 3, not 2

    def test_failed_capture_is_retried_twice_five_minutes_apart_then_left_for_a_human(self):
        L.STEPS["mcx"] = fake_mcx(self.calls, ok=False)
        seen = []
        for hh, mm in ((12, 10), (12, 12), (12, 16), (12, 22), (12, 40)):
            seen.append(L.tick(at(hh, mm), runner=self.run_it))
        self.assertEqual(seen, [2, None, 2, 2, None])                       # attempts at 12:10, 12:16, 12:22 only

    def test_skip_auto_off_and_override(self):
        L.skip_slot(2, by="ops")
        self.assertIsNone(L.tick(at(12, 10), runner=self.run_it))           # skipped folder: not started
        L.unskip_slot(2)
        L.save_settings(auto=False)
        self.assertIsNone(L.tick(at(12, 10), runner=self.run_it))           # auto OFF: nothing starts
        L.save_settings(auto=True)
        L.mark_done(2, by="ops")
        self.assertIsNone(L.tick(at(12, 10), runner=self.run_it))           # marked done: left alone
        self.assertEqual(L.status(dt.date.today(), at(12, 10))["slots"][1]["state"], "done")

    def test_changed_timing_and_mark_not_done_captures_again_at_the_new_time(self):
        L.run_slot(2, log=lambda m: None)                                   # folder 2 captured at 12:10
        self.assertEqual(L.status(dt.date.today(), at(14, 0))["slots"][1]["state"], "done")
        L.save_settings(times="10:12,14:30,13:27,15:10")                    # moved to 14:30 (sorted: folder 2 = 13:27)
        self.assertEqual(L.slot_times(), [612, 807, 870, 910])
        L.mark_undone(2, by="ops")
        st = L.status(dt.date.today(), at(13, 0))["slots"][1]
        self.assertEqual((st["time"], st["state"]), ("13:27", "upcoming"))
        self.assertIsNone(L.tick(at(13, 0), runner=self.run_it))            # before its (new) time
        self.assertEqual(L.tick(at(13, 28), runner=self.run_it), 2)         # at the new time: captured again
        d = dt.date.today()
        self.assertEqual(len(os.listdir(L.slot_dir(d, 2))), 5)              # no duplicates in the folder
        self.assertTrue(os.path.isdir(L._state_dir() / "_replaced" / L.dstr(d) / "2"))   # old copies kept aside
        self.assertEqual(L.status(d, at(13, 29))["slots"][1]["state"], "done")
        with self.assertRaises(ValueError):
            L.save_settings(times="10:12,99:00")

    def test_run_writes_a_log_the_shared_logs_window_can_list(self):
        L.run_slot(1, log=lambda m: None)
        logs = sorted(L.log_dir().glob("liveallocation_f1__*.log"))
        self.assertTrue(logs)
        txt = logs[-1].read_text(encoding="utf-8")
        self.assertIn("LIVE ALLOCATION CAPTURE - folder 1", txt)
        self.assertIn("DONE", txt)


class Backup(Base):
    def test_range_listing_and_one_zip_for_many_days(self):
        today = dt.date.today()
        yday = today - dt.timedelta(days=1)
        L.run_slot(1, log=lambda m: None)
        touch(L.slot_dir(yday, 2), "MCX_Web_Allocation_02.xlsx")
        touch(L.slot_dir(yday, 3), "MCX_Web_Allocation_03.xlsx")
        days = L.range_listing(yday, today)
        self.assertEqual([x["date"] for x in days], [L.dstr(yday), L.dstr(today)])
        self.assertEqual(days[0]["files"], 2)
        with zipfile.ZipFile(L.zip_range(yday, today)) as z:                # full folder, both days
            names = z.namelist()
        self.assertIn(f"{L.dstr(yday)}/2/MCX_Web_Allocation_02.xlsx", names)
        self.assertIn(f"{L.dstr(today)}/1/MCX_Web_Allocation_01.xlsx", names)
        with zipfile.ZipFile(L.zip_range(yday, today, [3])) as z:           # only folder 3
            self.assertEqual(z.namelist(), [f"{L.dstr(yday)}/3/MCX_Web_Allocation_03.xlsx"])
        with self.assertRaises(FileNotFoundError):
            L.zip_range(today - dt.timedelta(days=30), today - dt.timedelta(days=20))


class ByHand(Base):
    def test_files_added_by_hand_are_validated_replace_old_ones_and_complete_the_folder(self):
        d = dt.date.today()
        for n in ("CM_ClientLevelCollaterals_28102026.csv", "FO_ClientLevelCollaterals_28102026.csv",
                  "CO_ClientLevelCollaterals_28102026.csv", "ClientNetEffectiveDeposits_28102026.csv"):
            L.accept_upload(d, 1, n, b"data")
        r = L.finish_upload(d, 1, by="ops")
        self.assertFalse(r["complete"])
        self.assertEqual(r["missing"], ["MCX web allocation"])
        L.accept_upload(d, 1, "MCX_Web_Allocation_01.xlsx", b"x")
        L.accept_upload(d, 1, "MCX_Web_Allocation_01.xlsx", b"newer")        # same kind again: replaces, no duplicate
        self.assertEqual(len(os.listdir(L.slot_dir(d, 1))), 5)
        self.assertTrue(L.finish_upload(d, 1, by="ops")["complete"])
        self.assertEqual(L.status(d)["slots"][0]["state"], "done")
        with self.assertRaises(ValueError):
            L.accept_upload(d, 1, "notes.txt", b"x")
        with self.assertRaises(ValueError):
            L.accept_upload(d, 1, "../CM_ClientLevelCollaterals_1.csv", b"")   # empty file refused

    def test_data_folder_is_named_live_allocation_capture_and_the_old_name_is_renamed(self):
        os.environ.pop("LIVE_ALLOC_DIR", None)
        base = tempfile.mkdtemp()
        os.makedirs(os.path.join(base, "live_allocation", "BACKUPFILES AUTOMATED"))
        from core.scheduler import paths
        real = paths.data_root
        paths.data_root = lambda: __import__("pathlib").Path(base)
        try:
            self.assertEqual(L.root().name, "LIVE ALLOCATION CAPTURE")
            self.assertTrue(os.path.isdir(os.path.join(base, "LIVE ALLOCATION CAPTURE", "BACKUPFILES AUTOMATED")))
            self.assertFalse(os.path.exists(os.path.join(base, "live_allocation")))
        finally:
            paths.data_root = real


class Helper(unittest.TestCase):
    def test_schedule_helper_uses_the_agreed_timings(self):
        import tools.create_live_allocation_schedule as T
        self.assertEqual(T.TIMES, ["10:12", "12:10", "13:27", "15:10", "16:53", "18:55", "20:55", "22:55"])
        self.assertEqual(L._parse_times(",".join(T.TIMES)), L._parse_times(L.DEFAULT_TIMES))
