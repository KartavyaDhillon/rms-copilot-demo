"""xlsx_ready: a workbook still being written must never be picked up.
Run:  python -m unittest tests.test_xlsx_ready -v
"""
import os
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from core.scheduler import helpers as H


class XlsxReady(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _good(self, name="a.xlsx"):
        p = self.tmp / name
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("[Content_Types].xml", "<x/>")
            z.writestr("xl/workbook.xml", "<w/>")
        return p

    def test_empty_file_is_not_ready(self):
        p = self.tmp / "e.xlsx"
        p.write_bytes(b"")
        self.assertEqual(H.xlsx_ready(p, 0), (False, "0 KB"))

    def test_half_written_file_is_not_ready(self):
        p = self.tmp / "h.xlsx"
        p.write_bytes(b"PK\x03\x04 half written")
        self.assertFalse(H.xlsx_ready(p, 0)[0])

    def test_recently_touched_file_waits(self):
        p = self._good()
        self.assertFalse(H.xlsx_ready(p, 60)[0])
        os.utime(p, (time.time() - 120, time.time() - 120))
        self.assertTrue(H.xlsx_ready(p, 60)[0])

    def test_complete_file_is_ready(self):
        self.assertEqual(H.xlsx_ready(self._good(), 0), (True, ""))


if __name__ == "__main__":
    unittest.main()
