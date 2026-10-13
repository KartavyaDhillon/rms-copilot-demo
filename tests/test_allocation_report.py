"""Helpers of the allocation-report engine (no mailbox, no network).
Run:  python -m unittest tests.test_allocation_report -v
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.pipelines import allocation_report as ar


class AllocHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = os.environ.get("ALLOC_REPORT_FOLDER")
        os.environ["ALLOC_REPORT_FOLDER"] = str(self.tmp / "base")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("ALLOC_REPORT_FOLDER", None)
        else:
            os.environ["ALLOC_REPORT_FOLDER"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clients_are_cleaned_and_deduplicated(self):
        self.assertEqual(ar._clients("ab123, sn120;AB123  x<y>"), ["AB123", "SN120"])
        self.assertEqual(ar._clients(""), [])

    def test_mails_must_be_valid(self):
        self.assertEqual(ar._clean_mails("a@x.com, bad, b@y.in;a@x.com"), ["a@x.com", "b@y.in"])

    def test_only_one_run_at_a_time(self):
        self.assertTrue(ar._acquire({"mode": "sofar", "by": "t"}))
        self.assertFalse(ar._acquire({"mode": "close"}))
        self.assertEqual(ar.lock_info()["mode"], "sofar")
        ar._release()
        self.assertIsNone(ar.lock_info())

    def test_client_filter_keeps_only_those_rows(self):
        from openpyxl import Workbook, load_workbook
        p = self.tmp / "UPWARDS_Logs_x.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["AllocationDate", "ClientCode", "AllocationAmount"])
        for c, a in (("AB123", 1), ("SN120", 2), ("ab123", 3), ("ZZ999", 4)):
            ws.append(["13-10-2026", c, a])
        wb.save(p)
        self.assertEqual(ar._filter_xlsx(str(p), {"AB123"}), 2)
        rows = list(load_workbook(p).active.iter_rows(values_only=True))
        self.assertEqual([r[1] for r in rows[1:]], ["AB123", "ab123"])
        self.assertEqual(ar._filter_xlsx(str(p), {"NONE"}), 0)
        self.assertFalse(p.exists())                      # nothing left -> no empty file

    def test_status_without_runs(self):
        st = ar.status()
        self.assertFalse(st["running"])
        self.assertEqual(st["last_manual"], {})


if __name__ == "__main__":
    unittest.main()
