"""UNCLEARED-file helpers of the Margin Shortage (15-min) pipeline (no FTP, no network).
Run:  python -m unittest tests.test_ms15_uncleared -v
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.pipelines import margin_shortage_15min as ms

NAME = "Kambala_Omny_Depo_Adhoc_All_level.txt_{}.txt"
ROW = "{}||||0|10000000|||5010000000000||10000000||||0||MTM_ONEX|no||5010000000000|0|||||{}|||||0||0|0||0|0|0|0||||||0|0|2500000"


class Uncleared(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _file(self, date, rows):
        p = self.base / NAME.format(date)
        p.write_text("RMS Limits MAND\n" + "\n".join(ROW.format(c, v) for c, v in rows) + "\n", encoding="utf-8")
        return p

    def test_reads_column_a_and_z_and_ignores_the_first_line(self):
        p = self._file("13102026", [("011011", "125.5"), ("011252A", "0"), ("011011", "999")])
        data = ms._uncleared_read(str(p))
        self.assertEqual(data, {"011011": 125.5, "011252A": 0.0})        # first value wins for a repeated code

    def test_only_todays_file_is_found(self):
        self._file("12102026", [("A1", "1")])
        self.assertIsNone(ms._uncleared_local(str(self.base), "13102026"))
        t = self._file("13102026", [("A1", "2")])
        self.assertEqual(ms._uncleared_local(str(self.base), "13102026"), str(t))

    def test_older_file_goes_to_the_backup_of_its_own_day_and_today_stays(self):
        old = self._file("12102026", [("A1", "1")])
        today = self._file("13102026", [("A1", "2")])
        (self.base / "BACKUPFILES AUTOMATED" / "2026-10-12" / "01").mkdir(parents=True)
        (self.base / "BACKUPFILES AUTOMATED" / "2026-10-12" / "03").mkdir(parents=True)
        ms._uncleared_sweep_old(str(self.base), "13102026")
        self.assertFalse(old.exists())
        self.assertTrue(today.exists())                                    # today's file is never touched
        self.assertTrue((self.base / "BACKUPFILES AUTOMATED" / "2026-10-12" / "03" / old.name).exists())

    def test_prefix_must_match_exactly(self):
        (self.base / "Other_file_13102026.txt").write_text("x")
        (self.base / "Kambala_Omny_Depo_Adhoc_All_level.txt_13102026.csv").write_text("x")
        self.assertIsNone(ms._uncleared_local(str(self.base), "13102026"))


if __name__ == "__main__":
    unittest.main()
