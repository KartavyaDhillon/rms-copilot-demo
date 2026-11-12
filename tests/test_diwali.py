import datetime as dt
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core import daycards as D

DAY = dt.date(2026, 11, 8)


class DiwaliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._env = dict(os.environ)
        os.environ["DIWALI_BASE"] = str(self.tmp / "new")
        os.environ["DAYCARDS_DIR"] = str(self.tmp / "data")
        os.environ["SCHED_SPECIAL_FILE"] = str(self.tmp / "sp.csv")
        os.environ["SCHED_HOLIDAYS_FILE"] = str(self.tmp / "h.csv")
        base = self.tmp / "new" / "08-11-2026"
        for sub, names in {
            "MCX": ["MCX_PeakMargin56720_20261108_04.csv", "MCX_MARGIN_56720_20261108.csv"],
            "NCDEX": ["NCCL_MARGIN_REP_01292_20261108_01.csv", "01292_MGN_08112026140355.CSV"],
            "CM": ["Margin_NCL_CM_0_CM_06436_20261108_P_1100.csv", "C_SA01_P_06436_08112026_i01.lis", "C_CC02_06436_08112026.csv"],
        }.items():
            (base / sub).mkdir(parents=True)
            for n in names:
                (base / sub / n).write_text("x")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_date_defaults_to_muhurat_sunday(self):
        self.assertEqual(D.diwali_date(), DAY)

    def test_numbered_files_are_copies_of_the_single_session_file(self):
        with D.diwali_mode():
            names = {s.rel for s in D.stage_plan("master_file", DAY)}
        for i in range(1, 9):
            self.assertIn(f"MCX_PeakMargin56720_20261108_{i:02d}.csv", names)
            self.assertIn(f"NCCL_MARGIN_REP_01292_20261108_{i:02d}.csv", names)
        for t in ("1100", "1230", "1430", "1530"):
            self.assertIn(f"Margin_NCL_CM_0_CM_06436_20261108_P_{t}.csv", names)
        with D.diwali_mode():
            self.assertIn("C_SA01_P_06436_08112026_i04.lis", {s.rel for s in D.stage_plan("sa_cm", DAY)})

    def test_normal_mode_untouched(self):
        self.assertFalse(D.is_diwali())
        self.assertNotEqual(D.out_dir("sa", "sa_cm", DAY), None)
        with D.diwali_mode():
            self.assertIn("DIWALI", D.out_dir("sa", "sa_cm", DAY).parts)
        self.assertNotIn("DIWALI", D.out_dir("sa", "sa_cm", DAY).parts)

    def test_single_live_folder_feeds_every_slot(self):
        live = self.tmp / "new" / "08-11-2026" / "LIVE ALLOCATION"
        live.mkdir()
        for n in ("MCX_Web_Allocation_xx.xlsx", "CM_ClientLevelCollaterals_08112026101010.csv", "ClientNetEffectiveDeposits_08112026.csv"):
            (live / n).write_text("x")
        with D.diwali_mode():
            names = {s.rel for s in D.stage_plan("live_allocation_report", DAY)}
            sa = {s.rel for s in D.stage_plan("sa_cm", DAY)}
        for i in range(1, 9):
            self.assertIn(f"{i}/MCX_Web_Allocation_{i:02d}.xlsx", names)
            self.assertIn(f"{i}/ClientNetEffectiveDeposits_08112026.csv", names)
        for i in (1, 2, 3, 4, 8):
            self.assertIn(f"MCX_Web_Allocation_{i:02d}.xlsx", sa)

    def test_backup_folder_any_name_is_used(self):
        os.environ["LIVE_ALLOC_DIR"] = str(self.tmp / "live")
        f = self.tmp / "live" / "BACKUPFILES AUTOMATED" / "08-11-2026" / "peak"
        f.mkdir(parents=True)
        for n in ("MCX_Web_Allocation_03.xlsx", "ClientNetEffectiveDeposits_08112026.csv"):
            (f / n).write_text("x")
        with D.diwali_mode():
            names = {s.rel for s in D.stage_plan("sa_cm", DAY)}
        for i in (1, 2, 3, 4, 8):
            self.assertIn(f"MCX_Web_Allocation_{i:02d}.xlsx", names)

    def test_upload_routing(self):
        r = D.save_diwali_live(DAY, "MCX_Web_Allocation_01.xlsx", b"abc")
        self.assertTrue((self.tmp / "new" / "08-11-2026" / "LIVE ALLOCATION" / "MCX_Web_Allocation_01.xlsx").exists())
        D.save_diwali_live(DAY, "RISK_REPORT_08112026.csv", b"abc")
        self.assertTrue((self.tmp / "new" / "08-11-2026" / "RISK_REPORT_08112026.csv").exists())
        with self.assertRaises(ValueError):
            D.save_diwali_live(DAY, "RISK_REPORT_07112026.csv", b"abc")
        with self.assertRaises(ValueError):
            D.save_diwali_live(DAY, "Allocation_Detail_07112026.csv", b"abc")

    def test_backup_excludes_copies(self):
        with D.diwali_mode():
            files = D.backup_files("master", DAY, ["master_file"])
        self.assertFalse(any(f["kind"] == "synth" for f in files))


if __name__ == "__main__":
    unittest.main()
