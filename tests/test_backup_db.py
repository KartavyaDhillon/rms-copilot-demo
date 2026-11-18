"""Backup readers: month folders (NOV 2026) + the database fallback (PostgreSQL run_files) + the allocation-report backup scan.
Run:  python -m unittest tests.test_backup_db -v"""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import backup_db as B


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def list_run_files(self, pipeline=None, run_date=None, days=30, **k):
        self.calls += 1
        return [dict(r) for r in self.rows if r["pipeline"] == pipeline and (not run_date or r["run_date"].isoformat() == run_date)]

    def get_run_file(self, fid):
        for r in self.rows:
            if r["id"] == fid:
                return r["filename"], r["content"]
        return None


D = dt.date.today() - dt.timedelta(days=9)
ROWS = [
    {"id": 1, "pipeline": "liveallocation", "tag": f"{D:%d-%m-%Y}/3", "run_date": D, "filename": "a.xlsx", "size_bytes": 3, "content": b"abc"},
    {"id": 2, "pipeline": "ctcl_vs_nmass", "tag": f"{D:%d-%m-%Y}/01", "run_date": D, "filename": "Update Limits 1.xlsx", "size_bytes": 2, "content": b"zz"},
    {"id": 3, "pipeline": "ctcl_vs_nmass", "tag": f"{D:%d-%m-%Y}", "run_date": D, "filename": "flat.csv", "size_bytes": 1, "content": b"q"},
]


class DbFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeDB(ROWS)
        self._old = B._db
        B._db = lambda: self.fake
        os.environ["USE_POSTGRES"] = "true"
        B._EMPTY.clear()
        B._DATES.clear()

    def tearDown(self):
        B._db = self._old
        os.environ.pop("USE_POSTGRES", None)
        self.tmp.cleanup()

    def name(self):
        return D.strftime("%d-%m-%Y")

    def test_day_is_brought_back_into_run_folders(self):
        n = B.restore_day("liveallocation", self.tmp.name, self.name())
        self.assertEqual(n, 1)
        self.assertEqual((Path(self.tmp.name) / self.name() / "3" / "a.xlsx").read_bytes(), b"abc")
        n = B.restore_day("ctcl", self.tmp.name, self.name())
        self.assertEqual(n, 2)
        self.assertTrue((Path(self.tmp.name) / self.name() / "01" / "Update Limits 1.xlsx").is_file())
        self.assertTrue((Path(self.tmp.name) / self.name() / "flat.csv").is_file())          # a bare-date tag = straight in the day folder
        self.assertEqual(B.restore_day("ctcl", self.tmp.name, self.name()), 0)               # nothing is written twice

    def test_off_today_and_empty_answers(self):
        os.environ["USE_POSTGRES"] = "false"
        self.assertEqual(B.restore_day("liveallocation", self.tmp.name, self.name()), 0)
        os.environ["USE_POSTGRES"] = "true"
        today = dt.date.today().strftime("%d-%m-%Y")
        self.assertEqual(B.restore_day("liveallocation", self.tmp.name, today), 0)           # today is never restored
        other = (D - dt.timedelta(days=5)).strftime("%d-%m-%Y")
        B.restore_day("liveallocation", self.tmp.name, other)
        calls = self.fake.calls
        B.restore_day("liveallocation", self.tmp.name, other)
        self.assertEqual(self.fake.calls, calls)                                              # an empty day is not asked again at once

    def test_day_dir_uses_disk_first_then_month_folder_then_database(self):
        root = Path(self.tmp.name)
        (root / "NOV 2026" / "01-11-2026").mkdir(parents=True)
        (root / "NOV 2026" / "01-11-2026" / "x.txt").write_text("1")
        self.assertEqual(B.day_dir("ctcl", root, "01-11-2026"), root / "NOV 2026" / "01-11-2026")
        p = B.day_dir("ctcl", root, self.name())
        self.assertTrue(p.is_dir() and (p / "01" / "Update Limits 1.xlsx").is_file())

    def test_dates_include_database_days(self):
        self.assertEqual(B.dates("liveallocation"), [D])

    def test_live_allocation_and_update_limits_use_it(self):
        from core import live_allocation as L, update_limits as UL
        os.environ["LIVE_ALLOC_DIR"] = self.tmp.name
        try:
            self.assertEqual(L.list_files(D, 3)[0]["name"], "a.xlsx")                        # restored from the database
            self.assertIn(D, L.list_dates())
        finally:
            os.environ.pop("LIVE_ALLOC_DIR", None)
        root = Path(self.tmp.name) / "ctclroot"
        found = UL.ctcl_files(D, root=root)
        self.assertEqual([f["path"].name for f in found], ["Update Limits 1.xlsx"])


class ReaderTests(unittest.TestCase):
    def test_scheduler_helper_finds_a_day_in_its_month_folder(self):
        from core.scheduler import helpers as H
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "NOV 2026" / "08-11-2026").mkdir(parents=True)
            self.assertEqual(H._bk_day(Path(t), "08-11-2026"), Path(t) / "NOV 2026" / "08-11-2026")
            self.assertEqual(H._bk_day(Path(t), "09-11-2026"), Path(t) / "09-11-2026")

    def test_allocation_report_scan_reads_month_folders(self):
        from core.pipelines import allocation_report as ar

        class M:
            SUBJECT_MASTER, SUBJECT_MASTERTRUST, SENDER_FILTER = "s", "t", "a@b.c"
        with tempfile.TemporaryDirectory() as t:
            e = Path(t) / "NOV 2026" / "2026-11-09" / "10.00" / "ALLOCATION LOGS" / "2026-11-08_11.30_alloc_logs"
            e.mkdir(parents=True)
            (e / "a.txt").write_text("x")
            top = Path(t) / "2026-11-10" / "09.00" / "ALLOCATION LOGS" / "2026-11-08_12.30_alloc_logs"
            top.mkdir(parents=True)
            (top / "b.txt").write_text("y")
            r = ar._backup_matches(M, Path(t), dt.datetime(2026, 11, 8, 0, 1), dt.datetime(2026, 11, 8, 23, 59))
            self.assertEqual(len(r), 2)

    def test_cli_backfill_all_lists_archived_days(self):
        from core.pipelines import cli_upload
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "BACKUPFILES AUTOMATED"
            (root / "NOV 2026" / "02-11-2026").mkdir(parents=True)
            (root / "03-11-2026").mkdir(parents=True)
            os.environ["CLI_FOLDER"] = t
            seen = []
            old = cli_upload.backfill_date_to_db
            cli_upload.backfill_date_to_db = lambda d: (seen.append(d) or {"ok": True, "archived": [], "skipped": []})
            try:
                r = cli_upload.backfill_all_to_db()
            finally:
                cli_upload.backfill_date_to_db = old
                os.environ.pop("CLI_FOLDER", None)
            self.assertEqual(sorted(seen), ["02-11-2026", "03-11-2026"])
            self.assertTrue(r["ok"])


if __name__ == "__main__":
    unittest.main()
