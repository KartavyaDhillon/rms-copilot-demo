"""R1-R2: a live file that could not be backed up is never reused; the move retries; the Excel reader closes the file.
Run: python -m unittest tests.test_r1r2_files -v"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class Tests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        os.environ["R1R2_FOLDER"] = self.t.name
        from core.pipelines import r1_r2_comparison as R
        self.R = R
        R.BASE_FOLDER = self.t.name

    def tearDown(self):
        os.environ.pop("R1R2_FOLDER", None)
        self.t.cleanup()

    def test_leftover_live_file_is_not_listed_again(self):
        R = self.R
        p = os.path.join(self.t.name, "MCX_DEPOSIT_04.24.xlsx")
        open(p, "wb").write(b"x" * 10)
        self.assertEqual(R.list_files_in(self.t.name), [p])
        R._mark_used(p)
        self.assertEqual(R.list_files_in(self.t.name), [])                       # skipped from now on
        open(p, "wb").write(b"y" * 20)                                           # a NEW file with the same name is fresh again
        self.assertEqual(R.list_files_in(self.t.name), [p])

    def test_move_retries_until_the_file_is_free(self):
        R = self.R
        src, dest = os.path.join(self.t.name, "a.xlsx"), os.path.join(self.t.name, "b.xlsx")
        open(src, "wb").write(b"1")
        calls = {"n": 0}
        real = R.shutil.move

        def flaky(a, b):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError(32, "in use")
            return real(a, b)
        R.shutil.move = flaky
        try:
            R._move_with_retry(src, dest, tries=5, wait=0)
        finally:
            R.shutil.move = real
        self.assertTrue(os.path.isfile(dest) and not os.path.exists(src))

    def test_excel_reader_releases_the_file(self):
        import openpyxl
        R = self.R
        p = os.path.join(self.t.name, "m.xlsx")
        wb = openpyxl.Workbook()
        wb.active.append(["UCC Code", "Allocation at EOD"])
        wb.active.append(["A1", 5])
        wb.save(p)
        self.assertEqual(R.read_raw_rows(p)[1][0], "A1")
        os.replace(p, p + ".moved")                                              # would fail on Windows if the handle were open
        self.assertTrue(os.path.exists(p + ".moved"))


if __name__ == "__main__":
    unittest.main()
