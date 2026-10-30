import datetime as dt
import gzip
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core import files70 as F

D = dt.date(2026, 10, 29)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"FILES76_DIR": str(self.tmp)})
        self.env.start()
        self.p1 = mock.patch.object(F, "_member", return_value="06436")
        self.p2 = mock.patch.object(F, "_mcx_user", return_value="56720")
        self.p3 = mock.patch.object(F, "_live_day", return_value=None)
        self.p4 = mock.patch.object(F, "LOG_DIR", self.tmp / "_logs")
        for p in (self.p1, self.p2, self.p3, self.p4):
            p.start()
        self.base = F.day_dir(D)

    def tearDown(self):
        mock.patch.stopall()
        self.env.stop()

    def put(self, rel, data=b"x,y\n1,2\n"):
        p = self.base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p


class Plan(Base):
    def test_counts_match_the_real_day_folder(self):
        from collections import Counter
        c = Counter(i["group"] for i in F.plan(D))
        self.assertEqual(dict(c), {"nse_cm": 16, "nse_fo": 15, "nse_co": 22, "nse_cd": 2, "mcx": 11,
                                   "ncdex_end": 1, "ncdex_peak": 8, "epi": 1})
        names = {i.get("name") for i in F.plan(D)}
        self.assertIn("C_CC02_06436_29102026.csv", names)
        self.assertIn("Margin_NCL_CO_0_CM_06436_20261029_P_2230.csv", names)
        self.assertIn("MCX_PeakMargin56720_20261029_08.csv", names)
        self.assertIn("NCCL_MARGIN_REP_01292_20261029_05.csv", names)

    def test_status_finds_any_case_and_the_timed_ncdex_file(self):
        self.put("CM/c_cc02_06436_29102026.CSV")
        self.put("NCDEX/01292_MGN_29102026091451.CSV")
        self.put("EPI.xlsx", b"")                       # empty = not there
        st = F.status(D)
        g = {x["key"]: x for x in st["groups"]}
        self.assertEqual(g["nse_cm"]["found"], 1)
        self.assertEqual(g["ncdex_end"]["found"], 1)
        self.assertEqual(g["epi"]["found"], 0)
        self.assertEqual(st["total"], 76)

    def test_folder_is_inside_the_console_data_folder(self):
        with mock.patch.dict(os.environ, {"FILES76_DIR": "", "SCHED_DATA_ROOT": str(self.tmp / "data")}):
            self.assertEqual(F.root(), self.tmp / "data" / "76 FILES DOWNLOADER")
            self.assertEqual(F.day_dir(D), self.tmp / "data" / "76 FILES DOWNLOADER" / "BACKUPFILES AUTOMATED" / "29-10-2026")

    def test_default_date_is_previous_trading_day(self):
        with mock.patch("core.scheduler.helpers.holidays", return_value=set()):
            self.assertEqual(F.default_date(dt.date(2026, 11, 2)), dt.date(2026, 10, 30))   # Monday -> Friday
        self.assertEqual(F.parse_date("29-10-2026"), D)

    def test_eodsar_copies_stay_inside_the_day_folder(self):
        for rel in ("CM/C_CC02_06436_29102026.csv", "FO/F_CC02_06436_29102026.CSV", "CO/O_CC02_06436_29102026.CSV",
                    "CD/X_CC02_06436_29102026.CSV", "NCDEX/01292_MGN_29102026091451.CSV", "MCX/MCX_MARGIN_56720_20261029.csv",
                    "MCX/MCX_EODSAR_56720_20261029.csv", "NCDEX/NCCL_MARGIN_REP_01292_20261029_01.csv"):
            self.put(rel)
        self.assertEqual(F.refresh_eodsar(D, log=lambda m: None), 6)
        self.assertEqual(F.refresh_eodsar(D, log=lambda m: None), 0)           # nothing twice
        self.assertEqual(len(os.listdir(self.base / F.EODSAR)), 6)


class Auto(Base):
    def setUp(self):
        super().setUp()
        self.p5 = mock.patch.object(F, "AUTO_FILE", self.tmp / "_logs" / ".auto.json")
        self.p5.start()
        self.env2 = mock.patch.dict(os.environ, {"F76_AUTO": "on", "F76_AUTO_TIME": "09:00", "F76_AUTO_RETRIES": "1", "F76_AUTO_RETRY_MIN": "15"})
        self.env2.start()
        self.calls = []
        self.starter = lambda d, by="", trigger="": self.calls.append(d) or {"ok": True}
        self.tdays = mock.patch.object(F, "_trading_day", return_value=True)
        self.tdays.start()
        self.dd = mock.patch.object(F, "default_date", return_value=D)
        self.dd.start()

    def at(self, h, m=0, day=dt.date(2026, 10, 30)):
        return dt.datetime(day.year, day.month, day.day, h, m)

    def test_waits_for_the_set_time_then_starts_once(self):
        self.assertIsNone(F.auto_tick(self.at(8, 59), self.starter))
        self.assertTrue(F.auto_tick(self.at(9, 0), self.starter))
        self.assertEqual(self.calls, [D])
        self.assertIsNone(F.auto_tick(self.at(9, 5), self.starter))                 # too soon for a retry

    def test_retries_only_the_set_number_of_times(self):
        F.auto_tick(self.at(9, 0), self.starter)
        self.assertTrue(F.auto_tick(self.at(9, 16), self.starter))                  # 1 retry
        self.assertIsNone(F.auto_tick(self.at(9, 40), self.starter))                # no more
        self.assertEqual(len(self.calls), 2)

    def test_nothing_when_everything_is_there_or_holiday_or_off(self):
        for it in F.plan(D):
            self.put((it["sub"] + "/" if it["sub"] else "") + (it.get("name") or "01292_MGN_29102026091451.CSV"))
        self.assertIsNone(F.auto_tick(self.at(9, 30), self.starter))
        self.assertEqual(self.calls, [])

    def test_holiday_and_off_and_after_cutoff(self):
        self.tdays.stop()
        with mock.patch.object(F, "_trading_day", return_value=False):
            self.assertIsNone(F.auto_tick(self.at(9, 30), self.starter))
        self.tdays.start()
        with mock.patch.dict(os.environ, {"F76_AUTO": "off"}):
            self.assertIsNone(F.auto_tick(self.at(9, 30), self.starter))
        self.assertIsNone(F.auto_tick(self.at(18, 30), self.starter))
        self.assertEqual(self.calls, [])

    def test_time_can_be_changed_and_bad_text_falls_back(self):
        with mock.patch.dict(os.environ, {"F76_AUTO_TIME": "07:45"}):
            self.assertEqual(F.auto_time(), "07:45")
        with mock.patch.dict(os.environ, {"F76_AUTO_TIME": "abc"}):
            self.assertEqual(F.auto_time(), "09:00")


class Nse(Base):
    def _it(self):
        return [i for i in F.plan(D) if i["group"] == "nse_cd"]

    def test_download_unpack_missing_and_relogin(self):
        class R:
            def __init__(self, code, content=b"", ctype="application/octet-stream"):
                self.status_code, self.content, self.headers, self.text = code, content, {"Content-Type": ctype}, ""
        answers = iter([R(401), R(200, gzip.compress(b"a,b\n")), R(404)])
        tokens = []
        from core.scheduler import nse
        with mock.patch.object(nse, "_send", side_effect=lambda *a, **k: next(answers)), \
             mock.patch.object(nse, "_token", side_effect=lambda ctx, force=False: tokens.append(force) or "t"), \
             mock.patch.object(F.time, "sleep"):
            miss = F.fetch_nse(self._it(), self.base, F._Ctx(lambda m: None))
        self.assertEqual((self.base / "CD" / "X_CC02_06436_29102026.CSV").read_bytes(), b"a,b\n")
        self.assertEqual(miss, ["X_SA01_06436_29102026.lis"])
        self.assertEqual(tokens[:2], [False, True])                             # 401 -> fresh login


class Run(Base):
    def test_groups_do_not_stop_each_other_and_result_lists_missing(self):
        lines = []
        with mock.patch.object(F, "fetch_nse", side_effect=lambda items, base, ctx: [F._save_unpacked(b"1", base / i["sub"] / i["name"]) and None for i in items] and []), \
             mock.patch.object(F, "fetch_mcx", side_effect=RuntimeError("MCX login failed")), \
             mock.patch.object(F, "fetch_ncdex_end", return_value=[]), \
             mock.patch.object(F, "fetch_ncdex_peaks", return_value=[]), \
             mock.patch.object(F, "fetch_epi", return_value=[]):
            res = F.run(D, log=lines.append)
        self.assertFalse(res["ok"])
        self.assertIn("mcx", res["errors"])
        self.assertEqual(res["groups"]["nse_cm"], "ok")
        self.assertEqual(res["groups"]["mcx"], "failed")
        self.assertIn("EPI.xlsx", res["missing"])
        self.assertTrue(any("RESULT" in l for l in lines))

    def test_nothing_downloaded_when_all_there(self):
        for it in F.plan(D):
            self.put((it["sub"] + "/" if it["sub"] else "") + (it.get("name") or "01292_MGN_29102026091451.CSV"))
        with mock.patch.object(F, "fetch_nse") as n, mock.patch.object(F, "fetch_mcx") as m:
            res = F.run(D, log=lambda x: None)
        n.assert_not_called(); m.assert_not_called()
        self.assertTrue(res["ok"])


class Backup(Base):
    def test_zip_full_groups_and_files(self):
        self.put("CM/C_SA04_06436_29102026.lis")
        self.put("MCX/MCX_PeakMargin56720_20261029_01.csv")
        self.put("MCX/.mcx_export_state.json")
        self.put("MCX & NCDEX EODSAR/C_CC02_06436_29102026.csv")
        self.put("3/MCX_Web_Allocation_03.xlsx")
        allf = F.backup_files(D)
        rels = {f["rel"]: f["group"] for f in allf}
        self.assertNotIn("29-10-2026/MCX/.mcx_export_state.json", rels)
        self.assertEqual(rels["29-10-2026/CM/C_SA04_06436_29102026.lis"], "nse_cm")
        self.assertEqual(rels["29-10-2026/3/MCX_Web_Allocation_03.xlsx"], "live")
        self.assertEqual(rels["29-10-2026/MCX & NCDEX EODSAR/C_CC02_06436_29102026.csv"], "eodsar")
        with zipfile.ZipFile(F.make_zip(D)) as z:
            self.assertEqual(len(z.namelist()), 4)
        with zipfile.ZipFile(F.make_zip(D, groups=["mcx"])) as z:
            self.assertEqual(z.namelist(), ["29-10-2026/MCX/MCX_PeakMargin56720_20261029_01.csv"])
        with zipfile.ZipFile(F.make_zip(D, rels=["29-10-2026/3/MCX_Web_Allocation_03.xlsx", "../../etc/passwd"])) as z:
            self.assertEqual(z.namelist(), ["29-10-2026/3/MCX_Web_Allocation_03.xlsx"])
        with self.assertRaises(FileNotFoundError):
            F.make_zip(D, groups=["epi"])

    def test_live_allocation_slots_join_the_zip(self):
        live = self.tmp / "live" / "29-10-2026"
        (live / "8").mkdir(parents=True)
        (live / "8" / "ClientNetEffectiveDeposits_29102026.csv").write_text("a")
        self.p3.stop()
        with mock.patch.object(F, "_live_day", return_value=live):
            rels = [f["rel"] for f in F.backup_files(D)]
        self.assertIn("29-10-2026/8/ClientNetEffectiveDeposits_29102026.csv", rels)


class Captcha(unittest.TestCase):
    def test_no_answer_means_failure_not_a_popup(self):
        auto = mock.Mock(max_tries=5)
        auto.solve.return_value = None
        with self.assertRaises(RuntimeError):
            F._solve(auto, b"img")


if __name__ == "__main__":
    unittest.main()


class UploadAccepted(unittest.TestCase):
    def test_only_expected_names_are_accepted(self):
        import tempfile, datetime as dt
        from pathlib import Path
        from unittest import mock
        tmp = Path(tempfile.mkdtemp())
        d = dt.date(2026, 10, 29)
        with mock.patch.object(F, "day_dir", lambda x: tmp / "day"):
            r = F.save_upload(d, "EPI.xlsx", b"x")
            self.assertTrue((tmp / "day" / "EPI.xlsx").is_file())
            r = F.save_upload(d, "MCX_MARGIN_%s_20261029.csv" % F._mcx_user(), b"x")
            self.assertTrue((tmp / "day" / "MCX" / ("MCX_MARGIN_%s_20261029.csv" % F._mcx_user())).is_file())
            for bad in ("random.csv", "EPI_old.xlsx", "MCX_MARGIN_%s_20250101.csv" % F._mcx_user()):
                with self.assertRaises(ValueError):
                    F.save_upload(d, bad, b"x")
            with self.assertRaises(ValueError):
                F.save_upload(d, "EPI.xlsx", b"")
