"""UPDATE LIMITS DATA (core.update_limits): date/time of a file, storing as Parquet, de-dup, client search, uploads, auto store."""
import datetime as dt
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import polars as pl

from core import update_limits as U


def make_xlsx(p: Path, rows=None, mtime: dt.datetime = None):
    rows = rows or [("080011226", "A ONE", 100.5), ("7GDW01", "B TWO", -637.04), ("4790", "C", 0.0)]
    p.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"Entity": [r[0] for r in rows], "ClientName": [r[1] for r in rows], "Cash": [r[2] for r in rows]}).write_excel(p)
    if mtime:
        ts = mtime.timestamp()
        os.utime(p, (ts, ts))
    return p


def wait():
    for _ in range(300):
        if not U.job()["running"]:
            return
        time.sleep(0.05)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"UL_DATA_DIR": str(self.root / "data"), "CTCL_NMASS_FOLDER": str(self.root / "ctcl")})
        self.env.start()

    def tearDown(self):
        wait()
        self.env.stop()
        self.tmp.cleanup()


class Names(unittest.TestCase):
    def test_time_and_date_in_name(self):
        self.assertEqual(U.time_in_name("Update Limits05112026_1530.xlsx"), "15:30:00")
        self.assertEqual(U.time_in_name("Update Limits 05112026 15-30-20.xlsx"), "15:30:20")
        self.assertEqual(U.time_in_name("Update Limits05112026 3pm.xlsx"), "15:00:00")
        self.assertIsNone(U.time_in_name("Update Limits05112026.xlsx"))           # the date is not a time
        self.assertEqual(U.date_in_name("Update Limits05112026.xlsx"), dt.date(2026, 11, 5))

    def test_snap_time_order(self):
        d = dt.date(2026, 11, 5)
        same = dt.datetime(2026, 11, 5, 14, 51, 10).timestamp()
        other = dt.datetime(2026, 11, 6, 9, 0).timestamp()
        self.assertEqual(U.snap_time("x_1500.xlsx", d, same), ("15:00:00", "file name"))
        self.assertEqual(U.snap_time("x.xlsx", d, same), ("14:51:10", "file modified time"))
        self.assertEqual(U.snap_time("x.xlsx", d, other), (None, ""))


class Store(Base):
    def test_ingest_search_and_dedupe(self):
        d = dt.date(2026, 11, 5)
        f = make_xlsx(self.root / "ctcl" / "BACKUPFILES AUTOMATED" / "05-11-2026" / "01" / "Update Limits05112026.xlsx",
                      mtime=dt.datetime(2026, 11, 5, 9, 49, 5))
        r = U.ingest_file(f, d, "CTCL run 01")
        self.assertEqual(r["status"], "stored")
        self.assertEqual(r["entry"]["time"], "09:49:05")
        self.assertEqual(U.ingest_file(f, d, "CTCL run 01")["status"], "skipped")
        s = U.search("80011226", d)                       # leading zero dropped -> still found
        self.assertEqual(len(s["rows"]), 1)
        self.assertEqual(s["rows"][0]["data"]["Entity"], "080011226")
        self.assertEqual(s["rows"][0]["time"], "09:49:05")
        self.assertEqual(U.search("7gdw01", d)["rows"][0]["data"]["Cash"], -637.04)
        self.assertEqual(U.search("NOPE", d)["rows"], [])
        m = U.search("7gdw01, 80011226  NOPE\n4790", d)               # many codes: comma / space / new line
        self.assertEqual(sorted(x["data"]["Entity"] for x in m["rows"]), ["080011226", "4790", "7GDW01"])
        self.assertEqual(m["missing"], ["NOPE"])
        self.assertEqual(len(m["columns"]), 3)                          # every column of the file
        self.assertIn("3_CLIENTS", U.client_excel("7GDW01,4790,080011226", d).name)
        x = U.client_excel("7GDW01", d)
        self.assertTrue(x.exists() and x.stat().st_size > 1000)
        z = U.full_file(r["entry"]["id"], "csv")
        self.assertTrue(z.name.endswith(".zip"))
        self.assertEqual(U.full_file(r["entry"]["id"], "orig"), f)

    def test_history_finds_run_folders_and_skips_known(self):
        bk = self.root / "ctcl" / "BACKUPFILES AUTOMATED" / "04-11-2026"
        make_xlsx(bk / "01" / "Update Limits04112026.xlsx", mtime=dt.datetime(2026, 11, 4, 8, 39))
        make_xlsx(bk / "02" / "Update Limits04112026.xlsx", rows=[("7GDW01", "B", 5.0)], mtime=dt.datetime(2026, 11, 4, 9, 49))
        r = U.load_history(dt.date(2026, 11, 3), dt.date(2026, 11, 4))
        self.assertEqual((r["found"], r["queued"]), (2, 2))
        wait()
        self.assertEqual([e["time"] for e in U.snapshots(dt.date(2026, 11, 4))], ["08:39:00", "09:49:00"])
        rows = U.search("7GDW01", dt.date(2026, 11, 4))["rows"]
        self.assertEqual([x["data"]["Cash"] for x in rows], [-637.04, 5.0])
        self.assertEqual(U.search("7GDW01", dt.date(2026, 11, 4), times=["09:49"])["rows"][0]["data"]["Cash"], 5.0)
        self.assertEqual(U.load_history(dt.date(2026, 11, 4), dt.date(2026, 11, 4))["queued"], 0)

    def test_zip_of_chosen_files(self):
        import zipfile
        d = dt.date(2026, 11, 5)
        f1 = make_xlsx(self.root / "a" / "Update Limits05112026.xlsx", mtime=dt.datetime(2026, 11, 5, 9, 0, 1))
        f2 = make_xlsx(self.root / "b" / "Update Limits05112026.xlsx", rows=[("7GDW01", "B", 9.0)], mtime=dt.datetime(2026, 11, 5, 11, 0, 2))
        i1 = U.ingest_file(f1, d, "CTCL run 01")["entry"]["id"]
        i2 = U.ingest_file(f2, d, "CTCL run 02")["entry"]["id"]
        z = U.make_zip([i2, i1], "orig")
        self.assertEqual(zipfile.ZipFile(z).namelist(), ["Update Limits05112026_090001.xlsx", "Update Limits05112026_110002.xlsx"])
        self.assertEqual(zipfile.ZipFile(U.make_zip([i1], "csv")).namelist(), ["Update Limits05112026_090001.csv"])
        self.assertEqual(zipfile.ZipFile(U.make_zip([i1, i2], "parquet")).namelist()[0], "Update Limits05112026_090001.parquet")
        f1.unlink()                                                      # original gone -> that file falls back to CSV
        self.assertEqual(zipfile.ZipFile(U.make_zip([i1], "orig")).namelist(), ["Update Limits05112026_090001.csv"])
        with self.assertRaises(ValueError):
            U.make_zip([], "orig")

    def test_page_lists_the_full_file(self):
        d = dt.date(2026, 11, 5)
        f = make_xlsx(self.root / "p" / "Update Limits05112026.xlsx", mtime=dt.datetime(2026, 11, 5, 9, 0, 1))
        sid = U.ingest_file(f, d, "CTCL run 01")["entry"]["id"]
        p1 = U.page(sid, 0, 2)
        self.assertEqual((p1["total"], len(p1["rows"]), p1["columns"][0]), (3, 2, "Entity"))
        self.assertEqual(len(U.page(sid, 2, 2)["rows"]), 1)

    def test_upload_rules(self):
        with self.assertRaises(ValueError):
            U.check_upload("limits.xlsx")                                       # no date anywhere
        with self.assertRaises(ValueError):
            U.check_upload("Update Limits05112026.xlsx")                        # no time anywhere
        i = U.check_upload("Update Limits05112026.xlsx", lastmod_ms=str(int(dt.datetime(2026, 11, 5, 15, 2).timestamp() * 1000)))
        self.assertEqual((i["date"], i["time"], i["time_src"]), (dt.date(2026, 11, 5), "15:02:00", "file modified time"))
        self.assertEqual(U.check_upload("Update Limits05112026_1530.xlsx", time_hint="16:10")["time"], "16:10:00")
        tmp = make_xlsx(self.root / "x.part")
        r = U.finish_upload(tmp, "Update Limits05112026.xlsx", {"date": dt.date(2026, 11, 5), "time": "15:45:00", "time_src": "typed"})
        self.assertTrue(r["ok"])
        wait()
        e = U.snapshots(dt.date(2026, 11, 5))[0]
        self.assertEqual((e["source"], e["time"]), ("manual upload", "15:45:00"))
        self.assertTrue(Path(e["src_path"]).is_file())

    def test_auto_tick_once_a_day_after_the_time(self):
        make_xlsx(self.root / "ctcl" / "BACKUPFILES AUTOMATED" / "05-11-2026" / "03" / "Update Limits05112026.xlsx",
                  mtime=dt.datetime(2026, 11, 5, 11, 46))
        self.assertIsNone(U.auto_tick(dt.datetime(2026, 11, 5, 17, 59)))
        self.assertEqual(U.auto_tick(dt.datetime(2026, 11, 5, 18, 0)), "queued 1")
        self.assertIsNone(U.auto_tick(dt.datetime(2026, 11, 5, 19, 0)))          # already done today
        wait()
        self.assertEqual(U.summary()["count"], 1)
        U.save_settings({"auto": False})
        self.assertIsNone(U.auto_tick(dt.datetime(2026, 11, 6, 18, 30)))


if __name__ == "__main__":
    unittest.main()
