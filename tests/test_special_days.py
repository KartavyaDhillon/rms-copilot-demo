"""Special trading day (e.g. Sunday Muhurat): manual runs work, schedules / auto timers stay as they are,
and a file named with today's date falls back to the previous trading day's file."""
import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

SUN = dt.date(2026, 11, 8)      # Muhurat Sunday
FRI = dt.date(2026, 11, 6)


class Ctx:
    def __init__(self, trigger=""):
        self.trigger = trigger
        self.lines = []

    def log(self, s):
        self.lines.append(s)


class SpecialDays(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["SCHED_HOLIDAYS_FILE"] = os.path.join(self.tmp, "holidays.csv")
        os.environ["SCHED_SPECIAL_FILE"] = os.path.join(self.tmp, "special.csv")
        from core.scheduler import holidays, helpers
        holidays._SPECIAL.update({"path": None, "mtime": None, "days": set()})
        holidays._CACHE.update({"path": None, "mtime": None, "days": set()})
        self.H, self.hol = helpers, holidays

    def tearDown(self):
        os.environ.pop("SCHED_HOLIDAYS_FILE", None)
        os.environ.pop("SCHED_SPECIAL_FILE", None)

    def test_file_is_created_with_the_muhurat_sunday(self):
        self.assertTrue(self.H.is_special_day(SUN))
        self.assertFalse(self.H.is_special_day(dt.date(2026, 11, 15)))

    def test_auto_stays_closed_manual_opens(self):
        self.assertFalse(self.H.is_trading_day(SUN))                       # schedules / timers: unchanged
        self.assertTrue(self.H.is_trading_day(SUN, manual=True))           # Run now
        self.assertFalse(self.H.is_trading_day(dt.date(2026, 11, 15), manual=True))   # an ordinary Sunday stays closed

    def test_require_trading_day(self):
        from core.scheduler.processes import _kit as K
        from core.scheduler.catalog import Skipped
        K.require_trading_day(Ctx(self.H.MANUAL), SUN)                      # no error
        with self.assertRaises(Skipped):
            K.require_trading_day(Ctx("SCHEDULED"), SUN)
        with self.assertRaises(Skipped):
            K.require_trading_day(Ctx(self.H.MANUAL), dt.date(2026, 11, 15))

    def test_previous_trading_day_of_sunday_is_friday(self):
        self.assertEqual(self.H.prev_trading_day(SUN), FRI)

    def test_fallback_day_only_on_special_non_trading_day(self):
        self.assertEqual(self.H.todays_file_fallback_day(SUN), FRI)
        self.assertIsNone(self.H.todays_file_fallback_day(FRI))
        self.assertIsNone(self.H.todays_file_fallback_day(dt.date(2026, 11, 15)))

    def test_nse_tries_sunday_then_friday(self):
        from core.scheduler import nse
        calls = []

        def fake_one(kind, seg, folder, name, dest, ctx, date=None):
            calls.append(name)
            return "OK:" + name if "06112026" in name else None

        class FakeDT:
            date = mock.Mock()
        FakeDT.date.today.return_value = SUN
        with mock.patch.object(nse, "_download_one", fake_one), mock.patch.object(nse, "dt", FakeDT), \
                mock.patch.object(self.H, "todays_file_fallback_day", lambda today=None: FRI):
            got = nse.download("common", "FO", "/Limit Files", "F_AEL_OPT_CONTRACTS_08112026.csv.gz", self.tmp, Ctx())
        self.assertEqual(calls, ["F_AEL_OPT_CONTRACTS_08112026.csv.gz", "F_AEL_OPT_CONTRACTS_06112026.csv.gz"])
        self.assertEqual(got, "OK:F_AEL_OPT_CONTRACTS_06112026.csv.gz")

    def test_nse_sunday_file_found_no_fallback(self):
        from core.scheduler import nse
        calls = []

        def fake_one(kind, seg, folder, name, dest, ctx, date=None):
            calls.append(name)
            return "OK"

        class FakeDT:
            date = mock.Mock()
        FakeDT.date.today.return_value = SUN
        with mock.patch.object(nse, "_download_one", fake_one), mock.patch.object(nse, "dt", FakeDT), \
                mock.patch.object(self.H, "todays_file_fallback_day", lambda today=None: FRI):
            nse.download("common", "FO", "/Limit Files", "Contract_Delta_08112026_i04.csv", self.tmp, Ctx())
        self.assertEqual(len(calls), 1)

    def test_previous_day_names_are_untouched(self):
        from core.scheduler import nse

        class FakeDT:
            date = mock.Mock()
        FakeDT.date.today.return_value = SUN
        with mock.patch.object(nse, "dt", FakeDT), mock.patch.object(self.H, "todays_file_fallback_day", lambda today=None: FRI):
            self.assertIsNone(nse._friday_name("BhavCopy_NSE_FO_0_0_0_20261106_F_0000.csv.zip"))


if __name__ == "__main__":
    unittest.main()
