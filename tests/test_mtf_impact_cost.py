"""Safety tests for the MTF Impact Cost folder logic (no pandas / network needed).
Run:  python -m unittest tests.test_mtf_impact_cost -v
"""
import datetime
import os
import shutil
import tempfile
import unittest
from unittest import mock
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

    def test_impact_file_only_counts_when_saved_today(self):
        self.assertEqual(m.find_catg(self.d), (None, ""))
        src = self.base / "C_CATG_OCT2026.T01"
        src.write_bytes(b"20,A,EQ,INE1,1,0.1")
        self.assertEqual(m.find_catg(self.d)[0], src)                 # saved now = today's
        old = datetime.datetime.now().timestamp() - 3 * 86400
        os.utime(src, (old, old))
        with mock.patch.object(m.os, "name", "posix"):                # judged by modified date only (on Windows a copy made today also counts)
            self.assertEqual(m.find_catg(self.d), (None, ""))         # an older copy is ignored
            self.assertIn("not today", m.status("09-10-2026")["items"][2]["note"])    # and the dashboard says why
        m.backup_and_clean([src], self.d)                             # ...and so is one sitting in Backup
        self.assertEqual(m.find_catg(self.d), (None, ""))

    def test_file_copied_in_today_with_an_old_modified_date_counts_on_windows(self):
        src = self.base / "C_CATG_NOV2026.T01"
        src.write_bytes(b"20,A,EQ,INE1,1,0.1")
        old = datetime.datetime.now().timestamp() - 5 * 86400         # Explorer copy keeps the old modified date...
        os.utime(src, (old, old))
        fake = os.stat_result((0o100644, 0, 0, 1, 0, 0, 10, old, old, datetime.datetime.now().timestamp()))   # ...but creation = today
        with mock.patch.object(m.os, "name", "nt"), mock.patch.object(Path, "stat", return_value=fake):
            self.assertEqual(m._saved_day(src), datetime.date.today())

    def _bk(self, day, tag, name, data=b"20,A,EQ,INE1,1,0.1"):
        f = m.backup_root() / day / tag / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        return f

    def test_backup_copy_used_when_nse_is_down_only_if_3_days_old_or_less_and_same_month(self):
        d = datetime.date(2026, 11, 17)
        f = self._bk("14-11-2026", "101010", "C_CATG_NOV2026.T01")            # 3 days old (a Friday file for a Monday run)
        p, age, why = m.find_backup_catg(d)
        self.assertEqual((p, age), (f, 3))
        newer = self._bk("16-11-2026", "090909", "C_CATG_NOV2026.T01")        # the newest one wins
        self.assertEqual(m.find_backup_catg(d)[0], newer)
        # 4 days old is too old
        self.assertIsNone(m.find_backup_catg(datetime.date(2026, 11, 21))[0])

    def test_backup_copy_never_used_across_a_month_change(self):
        self._bk("30-11-2026", "101010", "C_CATG_NOV2026.T01")
        p, age, why = m.find_backup_catg(datetime.date(2026, 12, 1))          # 1 Dec: the monthly file is different
        self.assertIsNone(p)
        self.assertIn("another month", why)
        # ...but a DEC file in Backup is fine
        f = self._bk("01-12-2026", "080808", "C_CATG_DEC2026.T01")
        self.assertEqual(m.find_backup_catg(datetime.date(2026, 12, 2))[0], f)

    def test_empty_or_missing_backup(self):
        self.assertIsNone(m.find_backup_catg(datetime.date(2026, 11, 17))[0])
        self._bk("16-11-2026", "101010", "C_CATG_NOV2026.T01", data=b"")
        self.assertIsNone(m.find_backup_catg(datetime.date(2026, 11, 17))[0])

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
