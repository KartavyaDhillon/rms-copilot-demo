"""MCX / NCL PEAK MARGIN UPLOAD (core.scheduler.peak_upload) - dates, ledger, catch-up, sources order, upload answer."""
import datetime as dt
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core.scheduler import helpers as H, paths, peak_upload as P


class Ctx:
    trigger = "SCHEDULE"
    cancelled = False

    def __init__(self):
        self.lines = []

    def log(self, m):
        self.lines.append(str(m))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.p1 = mock.patch.object(paths, "sched_root", lambda: self.root / "sched")
        self.p1.start()
        # holidays: 02-11-2026 is a holiday in this test
        self.p2 = mock.patch.object(H, "holidays", lambda: {dt.date(2026, 11, 2)})
        self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop(); self.tmp.cleanup()


class Dates(Base):
    def test_t2_uses_the_holiday_calendar(self):
        # Thu 05-11: T-1 = 04-11, T-2 = 03-11
        t2, todo = P.dates_to_do("X", 0, None, today=dt.date(2026, 11, 5))
        self.assertEqual(t2, dt.date(2026, 11, 3))
        self.assertEqual(todo, [dt.date(2026, 11, 3)])
        # Wed 04-11: T-1 = 03-11, T-2 = 30-10 (02-11 holiday, weekend skipped)
        t2, _ = P.dates_to_do("X", 0, None, today=dt.date(2026, 11, 4))
        self.assertEqual(t2, dt.date(2026, 10, 30))

    def test_catch_up_only_days_not_in_the_ledger(self):
        P.mark_uploaded("X", dt.date(2026, 10, 29))
        _, todo = P.dates_to_do("X", 7, None, today=dt.date(2026, 11, 5))
        self.assertEqual(todo, [dt.date(2026, 10, 27), dt.date(2026, 10, 28), dt.date(2026, 10, 30),
                                dt.date(2026, 11, 3)])

    def test_force_date_is_alone(self):
        _, todo = P.dates_to_do("X", 7, dt.date(2026, 10, 29), today=dt.date(2026, 11, 5))
        self.assertEqual(todo, [dt.date(2026, 10, 29)])

    def test_first_run_is_t2_only(self):
        _, todo = P.dates_to_do("X", 7, None, today=dt.date(2026, 11, 5))   # empty ledger
        self.assertEqual(todo, [dt.date(2026, 11, 3)])

    def test_own_backup_counts_as_uploaded(self):
        b = paths.proc_dir("X", "backup")
        (b / "29-10-2026").mkdir(parents=True)
        (b / "29-10-2026" / "NCL_PEAKMARGIN_29102026.zip").write_bytes(b"x")
        (b / "28-10-2026").mkdir()                        # no zip -> not counted
        c = Ctx()
        self.assertEqual(P.sync_from_own_backup("X", c), 1)
        self.assertIn("2026-10-29", P.ledger("X"))
        self.assertNotIn("2026-10-28", P.ledger("X"))
        self.assertEqual(P.sync_from_own_backup("X", c), 0)   # already in the list


class Upload(Base):
    def _auth(self):
        return mock.patch.object(P, "_UploadAuth", lambda folder, ctx: mock.Mock(base="https://x", get=lambda f=False: "T"))

    def test_success_false_is_an_error(self):
        z = self.root / "a.zip"
        z.write_bytes(b"PK")
        r = mock.Mock(status_code=200, text='{"Success":false,"Message":"bad"}')
        r.json.return_value = {"Success": False}
        with self._auth(), mock.patch("requests.post", return_value=r):
            with self.assertRaises(RuntimeError):
                P.upload("X", z, "T.zip", dt.date(2026, 10, 29), Ctx())

    def test_success_true_returns(self):
        z = self.root / "a.zip"
        z.write_bytes(b"PK")
        r = mock.Mock(status_code=200, text='{"Success":true}')
        r.json.return_value = {"Success": True}
        with self._auth(), mock.patch("requests.post", return_value=r) as post:
            P.upload("X", z, "NCL_PEAKMARGIN_DDMMYYYY.zip", dt.date(2026, 10, 29), Ctx())
        self.assertEqual(post.call_args.kwargs["data"], {"fileName": "NCL_PEAKMARGIN_DDMMYYYY.zip", "date": "29-10-2026"})


class RunLoop(Base):
    def test_uploads_each_day_marks_and_archives(self):
        def one_day(d, work):
            work.mkdir(parents=True, exist_ok=True)
            f = work / f"f_{d:%d%m%Y}.csv"
            f.write_text("x")
            return [f]
        c = Ctx()
        with mock.patch.object(P, "upload", return_value="ok") as up, \
                mock.patch.object(H, "is_trading_day", return_value=True):
            P.mark_uploaded("X", dt.date(2026, 10, 29))
            with mock.patch.object(P, "dates_to_do", return_value=(dt.date(2026, 11, 3),
                                                                   [dt.date(2026, 10, 30), dt.date(2026, 11, 3)])):
                res = P.run_dates("X", {"catch_up_days": 7}, c, one_day, "NCL_PEAKMARGIN", "T.zip")
        self.assertEqual(up.call_count, 2)
        self.assertIn("30-10-2026", res)
        led = P.ledger("X")
        self.assertIn("2026-11-03", led)
        z = paths.proc_dir("X", "backup") / "03-11-2026" / "NCL_PEAKMARGIN_03112026.zip"
        self.assertTrue(z.exists())
        self.assertEqual(zipfile.ZipFile(z).namelist(), ["f_03112026.csv"])
        self.assertFalse((paths.proc_dir("X", "inputs") / "03-11-2026").exists())

    def test_a_missing_day_fails_but_others_still_upload(self):
        def one_day(d, work):
            if d.day == 30:
                raise FileNotFoundError("missing NCDEX")
            work.mkdir(parents=True, exist_ok=True)
            f = work / "a.csv"
            f.write_text("x")
            return [f]
        with mock.patch.object(P, "upload", return_value="ok"), mock.patch.object(H, "is_trading_day", return_value=True), \
                mock.patch.object(P, "dates_to_do", return_value=(dt.date(2026, 11, 3),
                                                                  [dt.date(2026, 10, 30), dt.date(2026, 11, 3)])):
            with self.assertRaises(RuntimeError) as e:
                P.run_dates("X", {}, Ctx(), one_day, "NCL_PEAKMARGIN", "T.zip")
        self.assertIn("NOT uploaded: 30-10-2026", str(e.exception))
        self.assertIn("2026-11-03", P.ledger("X"))
        self.assertNotIn("2026-10-30", P.ledger("X"))


class Sources(Base):
    def test_ncdex_backup_used_when_exchange_fails(self):
        from core.scheduler.processes import ncl_peak_margin_upload as N
        from core import files70
        d = dt.date(2026, 10, 29)
        day76 = self.root / "76" / "29-10-2026"
        want = N.names(d, "06436", "01292")
        for seg in ("NCDEX", "CM", "FO"):
            (day76 / seg).mkdir(parents=True, exist_ok=True)
            for n in want[seg]:
                (day76 / seg / n).write_text("x")
        calls = []

        def ncdex_fail(*a, **k):
            calls.append("ncdex")
            raise RuntimeError("captcha")

        def nse_none(items, base, ctx):
            calls.append("nse")
            return [i["name"] for i in items]
        with mock.patch.object(files70, "day_dir", lambda dd: day76), mock.patch.object(files70, "_member", lambda: "06436"), \
                mock.patch.object(files70, "fetch_ncdex_peaks", ncdex_fail), mock.patch.object(files70, "fetch_nse", nse_none):
            files = N._one_day_factory({}, Ctx())(d, self.root / "work")
        self.assertEqual(len(files), 16)
        self.assertIn("ncdex", calls)                 # the exchange was tried first
        self.assertTrue((self.root / "work" / "NCDEX" / want["NCDEX"][0]).exists())


class Registered(unittest.TestCase):
    def test_both_processes_registered(self):
        from core.scheduler import catalog
        catalog.load_builtin()
        self.assertIsNotNone(catalog.get("mcx_peak_margin_upload"))
        self.assertIsNotNone(catalog.get("ncl_peak_margin_upload"))


if __name__ == "__main__":
    unittest.main()
