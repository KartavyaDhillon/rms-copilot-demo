"""Safety tests for the MTF Impact Cost folder logic (no pandas / network needed).
Run:  python -m unittest tests.test_mtf_impact_cost -v
"""
import datetime
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.pipelines import mtf_impact_cost as m


class MtfImpactCostFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.base = self.tmp / "base"
        self.common = self.tmp / "common"
        self.base.mkdir()
        self.common.mkdir()
        self._old = {k: os.environ.get(k) for k in ("MTF_IMPACT_FOLDER", "SA_COMMON_DIR")}
        os.environ["MTF_IMPACT_FOLDER"] = str(self.base)
        os.environ["SA_COMMON_DIR"] = str(self.common)
        self.d = datetime.date(2026, 10, 9)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tmpfile(self, name, data=b"x"):
        p = self.tmp / name
        p.write_bytes(data)
        return str(p)

    def test_upload_routes_only_known_names(self):
        res = m.route_uploads([
            self._tmpfile("TradeBook_09102026.csv"),
            self._tmpfile("c_catg_oct2026.t01"),
            self._tmpfile("HOLDING_09102026.csv"),
            self._tmpfile("random.txt"),
            self._tmpfile("TradeBook_10102026.csv", b""),
        ])
        self.assertEqual(len(res["ok"]), 2)
        self.assertEqual(len(res["rejected"]), 3)
        self.assertTrue((self.base / "TradeBook_09102026.csv").exists())
        self.assertTrue((self.base / "C_CATG_OCT2026.T01").exists())
        self.assertFalse((self.base / "HOLDING_09102026.csv").exists())
        self.assertFalse((self.common / "HOLDING_09102026.csv").exists())

    def test_backup_deletes_only_verified_base_files_and_never_holding(self):
        tb = self.base / "TradeBook_09102026.csv"
        tb.write_bytes(b"trades")
        xl = self.base / "Reconciliation_MTF_ImpactCost_09102026.xlsx"
        xl.write_bytes(b"xl")
        hold = self.common / "HOLDING_09102026.csv"
        hold.write_bytes(b"holding")
        keep = self.base / "notes.py"
        keep.write_bytes(b"print(1)")
        bdir, done, problems = m.backup_and_clean([tb, xl, hold], self.d)
        self.assertEqual(problems, [])
        self.assertFalse(tb.exists())
        self.assertFalse(xl.exists())
        self.assertTrue(hold.exists())                      # COMMON FILES copy is never removed
        self.assertTrue(keep.exists())                      # unrelated files untouched
        self.assertEqual((bdir / tb.name).read_bytes(), b"trades")
        self.assertIn(self.base.resolve(), bdir.resolve().parents)

    def test_impact_file_falls_back_to_this_months_backup_copy(self):
        self.assertEqual(m.find_catg(self.d), (None, ""))
        src = self.base / "C_CATG_OCT2026.T01"
        src.write_bytes(b"20,A,EQ,INE1,1,0.1")
        m.backup_and_clean([src], self.d)
        path, note = m.find_catg(self.d)
        self.assertIsNotNone(path)
        self.assertIn("backup", note)
        self.assertFalse((self.base / "C_CATG_OCT2026.T01").exists())

    def test_status_ready_needs_tradebook_and_common_holding(self):
        self.assertFalse(m.status("09-10-2026")["ready"])
        (self.base / "TradeBook_09102026.csv").write_bytes(b"x")
        self.assertFalse(m.status("09-10-2026")["ready"])
        (self.common / "HOLDING_09102026.csv").write_bytes(b"x")
        st = m.status("09-10-2026")
        self.assertTrue(st["ready"])
        self.assertEqual(m.detect_date(), self.d)


if __name__ == "__main__":
    unittest.main()
