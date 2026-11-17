"""Monthly backup archive. Run:  python -m unittest tests.test_backup_archive -v"""
import datetime as dt
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core import backup_archive as ba


class Archive(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = os.environ.get("RMS_DATA_ROOT")
        os.environ["RMS_DATA_ROOT"] = str(self.tmp)
        self.bk = self.tmp / "CONCENTRATION" / "BACKUPFILES AUTOMATED"
        self.bk2 = self.tmp / "COMMON FILES" / "BACKUP FILES AUTOMATED"
        for root, days in ((self.bk, ("28-10-2026", "30-10-2026", "31-10-2026", "02-11-2026", "03-11-2026", "02-04-2026---")),
                           (self.bk2, ("2026-10-15", "2026-11-03"))):
            for d in days:
                (root / d).mkdir(parents=True)
                (root / d / "a.csv").write_text("x")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("RMS_DATA_ROOT", None)
        else:
            os.environ["RMS_DATA_ROOT"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_and_name(self):
        self.assertEqual(ba.parse_day("08-11-2026"), dt.date(2026, 11, 8))
        self.assertEqual(ba.parse_day("2026-11-08"), dt.date(2026, 11, 8))
        self.assertIsNone(ba.parse_day("02-04-2026---"))
        self.assertIsNone(ba.parse_day("31-02-2026"))
        self.assertEqual(ba.month_name(dt.date(2026, 10, 3)), "OCT 2026")

    def test_finds_both_backup_roots(self):
        self.assertEqual({r.parent.name for r in ba.find_roots()}, {"CONCENTRATION", "COMMON FILES"})

    def test_first_weekday_moves_whole_october_only_outside_grace(self):
        today = dt.date(2026, 11, 3)                              # Tuesday, 1st-2nd were weekend/Monday
        res = ba.run(today=today, grace_days=3)
        # 28-10 is 6 days old -> moves; 30-10 is 4 days -> moves; 31-10 is 3 days -> stays (grace); 2026-10-15 moves
        self.assertEqual(res["moved"], 3)
        self.assertTrue((self.bk / "OCT 2026" / "28-10-2026" / "a.csv").is_file())
        self.assertTrue((self.bk / "OCT 2026" / "30-10-2026").is_dir())
        self.assertTrue((self.bk2 / "OCT 2026" / "2026-10-15" / "a.csv").is_file())
        self.assertTrue((self.bk / "31-10-2026").is_dir())
        self.assertTrue((self.bk / "03-11-2026").is_dir())       # running month untouched
        self.assertTrue((self.bk / "02-11-2026").is_dir())
        self.assertTrue((self.bk / "02-04-2026---").is_dir())    # unknown name untouched
        res2 = ba.run(today=dt.date(2026, 11, 5), grace_days=3)  # next pass collects the leftover day
        self.assertEqual(res2["moved"], 1)
        self.assertTrue((self.bk / "OCT 2026" / "31-10-2026").is_dir())

    def test_second_run_is_a_no_op_and_merge_never_overwrites(self):
        ba.run(today=dt.date(2026, 11, 20), grace_days=0)
        self.assertEqual(ba.run(today=dt.date(2026, 11, 20), grace_days=0)["found"], 0)
        (self.bk / "28-10-2026").mkdir()                          # same day appears again (late file)
        (self.bk / "28-10-2026" / "b.csv").write_text("new")
        res = ba.run(today=dt.date(2026, 11, 20), grace_days=0)
        self.assertEqual(res["merged"], 1)
        self.assertTrue((self.bk / "OCT 2026" / "28-10-2026" / "b.csv").is_file())
        self.assertFalse((self.bk / "28-10-2026").exists())

    def test_clash_is_reported_not_overwritten(self):
        ba.run(today=dt.date(2026, 11, 20), grace_days=0)
        (self.bk / "28-10-2026").mkdir()
        (self.bk / "28-10-2026" / "a.csv").write_text("DIFFERENT")
        res = ba.run(today=dt.date(2026, 11, 20), grace_days=0)
        self.assertEqual(res["failed"], 1)
        self.assertEqual((self.bk / "OCT 2026" / "28-10-2026" / "a.csv").read_text(), "x")
        self.assertTrue((self.bk / "28-10-2026" / "a.csv").exists())

    def test_tick_runs_once_on_weekdays_only(self):
        ba.save_settings(run_time="06:30", grace_days=0)
        self.assertFalse(ba.tick(dt.datetime(2026, 11, 7, 7, 0)))      # Saturday
        self.assertFalse(ba.tick(dt.datetime(2026, 11, 9, 6, 0)))      # before the time
        self.assertTrue(ba.tick(dt.datetime(2026, 11, 9, 7, 0)))       # Monday
        self.assertFalse(ba.tick(dt.datetime(2026, 11, 9, 8, 0)))      # only once a day
        self.assertTrue((self.bk / "OCT 2026" / "31-10-2026").is_dir())

    def test_old_day_is_still_found_after_filing(self):
        ba.run(today=dt.date(2026, 11, 20), grace_days=0)
        self.assertEqual(ba.day_dir(self.bk, "28-10-2026"), self.bk / "OCT 2026" / "28-10-2026")
        self.assertEqual(ba.day_dir(self.bk, "03-11-2026"), self.bk / "03-11-2026")
        self.assertIn("28-10-2026", ba.archived_days(self.bk))

    def test_settings_validation(self):
        with self.assertRaises(ValueError):
            ba.save_settings(run_time="25:99")
        with self.assertRaises(ValueError):
            ba.save_settings(grace_days=99)
        self.assertEqual(ba.save_settings(grace_days=5, enabled=False)["grace_days"], 5)


if __name__ == "__main__":
    unittest.main()
