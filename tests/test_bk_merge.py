"""Tests for core/bk_merge.py (fast client filter + merge worker). Run:  python -m unittest tests.test_bk_merge -v"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

try:
    import polars as pl          # noqa
    import openpyxl
    import fastexcel             # noqa
    HAVE = True
except Exception:                # pragma: no cover
    HAVE = False

if HAVE:
    from core import bk_merge as bk

CANDS = ["AccountID", "Account ID", "Client Code", "UCC Code", "Entity"]


def _make_xlsx(path, header_row=1, key="Entity", rows=None, extra_cols=6):
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet()
    for _ in range(header_row - 1):
        ws.append(["Some title"] if _ == 0 else [])
    ws.append([key, "Profile", "Cash"] + [f"C{i}" for i in range(extra_cols)])
    for r in (rows or []):
        ws.append([r[0], "MTM", r[1]] + [i for i in range(extra_cols)])
    wb.save(path)


@unittest.skipUnless(HAVE, "polars/fastexcel/openpyxl not installed")
class BkMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        os.environ["RMS_BKCACHE_DIR"] = os.path.join(self.d, "cache")
        os.environ["RMS_COLD_SLOTS"] = "2"

    def tearDown(self):
        os.environ.pop("RMS_BKCACHE_DIR", None)
        os.environ.pop("RMS_COLD_SLOTS", None)
        self.tmp.cleanup()

    def _xl(self, name, **kw):
        p = os.path.join(self.d, name)
        _make_xlsx(p, **kw)
        return p

    # -- one file ---------------------------------------------------------------------------------
    def test_entity_header_found_filtered_and_cached(self):
        p = self._xl("Update Limits_29102026.xlsx", rows=[("SN120", 5), ("SN121", 6), ("sn120 ", 7)])
        f, df, st, how = bk.filter_one(p, ["SN120"], CANDS)
        self.assertEqual((st, how), ("ok", "miss"))
        self.assertEqual(df.height, 2)                      # case + spaces tolerant
        self.assertTrue(os.path.isfile(bk.cache_path(p)))
        f, df2, st2, how2 = bk.filter_one(p, ["SN120"], CANDS)
        self.assertEqual((st2, how2, df2.height), ("ok", "hit", 2))

    def test_header_not_on_first_row(self):
        p = self._xl("x.xlsx", header_row=3, key="Client Code", rows=[("A1", 1), ("B2", 2)])
        f, df, st, how = bk.filter_one(p, ["B2"], CANDS)
        self.assertEqual(st, "ok")
        self.assertEqual(df.height, 1)
        self.assertIn("Cash", df.columns)

    def test_no_data_and_no_column(self):
        p = self._xl("a.xlsx", rows=[("A1", 1)])
        self.assertEqual(bk.filter_one(p, ["ZZ9"], CANDS)[2], "no_data")
        q = self._xl("b.xlsx", key="Something else", rows=[("A1", 1)])
        self.assertEqual(bk.filter_one(q, ["A1"], CANDS)[2], "no_column")
        self.assertEqual(bk.filter_one(q, ["A1"], CANDS)[3], "hit")       # remembered: not re-read

    def test_csv_and_special_text(self):
        p = os.path.join(self.d, "k.csv")
        open(p, "w").write("Client Code,Val\nSN1,10\nSN2,20\n")
        f, df, st, _ = bk.filter_one(p, ["SN2"], CANDS)
        self.assertEqual((st, df.height), ("ok", 1))
        c = os.path.join(self.d, "CLI_PRUDENTIAL_LIMIT.txt")
        open(c, "w").write("ClientPrudentialLimit\nSN1,5\nSN2,6\n")
        self.assertEqual(bk.filter_one(c, ["SN2"], CANDS)[2], "ok")
        self.assertEqual(bk.filter_one(c, ["NOPE"], CANDS)[2], "no_data")

    def test_tidy_numbers(self):
        df = pl.DataFrame({"a": ["92612705.430000007", "0.1", "SN1", "-51975311.009999998", "007.5", None]})
        out = bk.tidy_numbers(df)["a"].to_list()
        self.assertEqual(out[0], "92612705.43")
        self.assertEqual(out[1], "0.1")
        self.assertEqual(out[2], "SN1")
        self.assertEqual(out[3], "-51975311.01")
        self.assertEqual(out[4], "007.5")
        self.assertIsNone(out[5])

    def test_file_time(self):
        ft = bk.file_time
        self.assertEqual(ft("Update Limits01102026_0935.xlsx"), "09:35")
        self.assertEqual(ft("abc-20260729145211.xlsx"), "14:52")
        self.assertEqual(ft("MCX_DEPOSIT_12.38.xlsx"), "12:38")
        self.assertEqual(ft("Update Limits.xlsx", "13:07"), "13:07")
        self.assertEqual(ft("plain.xlsx", "", "", True), "")

    def test_stale_lock_is_taken_over(self):
        lk = os.path.join(self.d, "some.lock")
        open(lk, "w").write("99999999")                   # a pid that does not exist
        os.utime(lk, (1, 1))                              # and very old
        self.assertFalse(bk._try_lock(lk))                # first call clears the stale lock...
        self.assertTrue(bk._try_lock(lk))                 # ...so the next one can take it
        bk._unlock(lk)

    # -- a whole job in its own process --------------------------------------------------------------
    def _run_job(self, groups, codes, cancel=False):
        jd = os.path.join(self.d, "job")
        os.makedirs(jd, exist_ok=True)
        json.dump({"codes": codes, "cands": CANDS, "workers": 2, "cold": 0, "groups": groups},
                  open(os.path.join(jd, "job.json"), "w"))
        if cancel:
            open(os.path.join(jd, "cancel.flag"), "w").write("")
        env = dict(os.environ)
        r = subprocess.run([sys.executable, os.path.join(REPO, "core", "bk_merge.py"), jd],
                           capture_output=True, text=True, env=env, timeout=180)
        return jd, r

    def test_job_end_to_end(self):
        items = []
        for k, dt in enumerate(["27-10-2026", "28-10-2026", "29-10-2026"]):
            p = self._xl(f"Update Limits_{k}.xlsx", rows=[("SN120", k), ("SN999", 9)])
            items.append({"ap": p, "date": dt, "name": os.path.basename(p)})
        other = self._xl("Other.xlsx", rows=[("SN5", 1)])
        groups = [{"label": "Update Limits", "items": items, "missing": 0},
                  {"label": "Other", "items": [{"ap": other, "date": "29-10-2026", "name": "Other.xlsx"}], "missing": 0}]
        jd, r = self._run_job(groups, ["SN120"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        prog = json.load(open(os.path.join(jd, "progress.json")))
        self.assertEqual((prog["state"], prog["done"], prog["total"]), ("done", 4, 4))
        res = json.load(open(os.path.join(jd, "result.json")))["groups"]
        g0, g1 = res[0], res[1]
        self.assertEqual((g0["state"], g0["rows"], g0["files_with_rows"]), ("ok", 3, 3))
        self.assertTrue(g0["fname"].endswith("_MERGED.csv"))
        self.assertEqual(g1["state"], "no_data")
        txt = open(os.path.join(jd, g0["file"]), encoding="utf-8").read().splitlines()
        self.assertTrue(txt[0].startswith("Date,Time,Entity"))
        self.assertIn('""27-10-2026""', txt[1])           # ="27-10-2026" kept as text for Excel
        # second run: everything comes from the cache
        jd2, r2 = self._run_job(groups, ["SN120"])
        prog2 = json.load(open(os.path.join(jd2, "progress.json")))
        self.assertEqual(prog2["hits"], 4)

    def test_job_cancel(self):
        p = self._xl("c.xlsx", rows=[("SN1", 1)])
        groups = [{"label": "L", "items": [{"ap": p, "date": "", "name": "c.xlsx"}] * 3, "missing": 0}]
        jd, r = self._run_job(groups, ["SN1"], cancel=True)
        self.assertEqual(json.load(open(os.path.join(jd, "progress.json")))["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
