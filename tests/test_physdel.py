"""PHYSICAL DELIVERY: file picking, window dates, percent, safe test, patched scripts compile."""
import datetime as dt
import os
import py_compile
import tempfile
import unittest
from pathlib import Path

from core.pipelines import physdel_cfg as C

REPO = Path(__file__).resolve().parents[1]


def _touch(p, mt=None):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    if mt:
        os.utime(p, (mt, mt))


class PhysDel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("PHYSICAL_DELIVERY_FOLDER", "PHYSICAL_DELIVERY_INPUT", "PD_TEST_MODE",
                                                      "CTCL_NMASS_FOLDER", "SA_COMMON_DIR")}
        os.environ["PHYSICAL_DELIVERY_FOLDER"] = os.path.join(self.tmp, "PD")
        os.environ.pop("PHYSICAL_DELIVERY_INPUT", None)
        os.environ.pop("PD_TEST_MODE", None)
        os.environ["CTCL_NMASS_FOLDER"] = os.path.join(self.tmp, "CTCL")
        os.environ["SA_COMMON_DIR"] = os.path.join(self.tmp, "COMMON")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_ctcl_picks_latest_date_then_highest_run_main_file_only(self):
        r = os.path.join(self.tmp, "CTCL", "BACKUPFILES AUTOMATED")
        _touch(f"{r}/20-10-2026/01/FULL_FILE_CTCL_VS_NMASS_01.xlsx")
        _touch(f"{r}/22-10-2026/01/FULL_FILE_CTCL_VS_NMASS_01.xlsx")
        _touch(f"{r}/22-10-2026/03/FULL_FILE_CTCL_VS_NMASS_03.xlsx")
        _touch(f"{r}/22-10-2026/03/FULL_FILE_CTCL_VS_NMASS_DELH_03.xlsx")   # zone variant must be ignored
        _touch(f"{r}/22-10-2026/02/FULL_FILE_CTCL_VS_NMASS_02.xlsx")
        got = C.pick_ctcl_file()
        self.assertEqual(got["name"], "FULL_FILE_CTCL_VS_NMASS_03.xlsx")
        self.assertEqual(got["date"], "2026-10-22")
        self.assertEqual(got["run"], 3)

    def test_ctcl_none_when_no_backup(self):
        self.assertIsNone(C.pick_ctcl_file())

    def test_risk_report_is_todays_only(self):
        d = dt.date(2026, 10, 23)
        _touch(os.path.join(self.tmp, "COMMON", "RISK_REPORT_22102026.csv"))
        self.assertIsNone(C.risk_report(d))
        _touch(os.path.join(self.tmp, "COMMON", "RISK_REPORT_23102026.csv"))
        self.assertEqual(C.risk_report(d)["name"], "RISK_REPORT_23102026.csv")

    def test_positionbook_newest(self):
        inp = C.input_dir()
        _touch(os.path.join(inp, "PositionBook_old.csv"), 1_000_000)
        _touch(os.path.join(inp, "PositionBook_new.xlsx"), 2_000_000)
        _touch(os.path.join(inp, "Other.csv"), 3_000_000)
        self.assertTrue(C.positionbook().endswith("PositionBook_new.xlsx"))

    def test_missing_inputs_lists_everything(self):
        m = C.missing_inputs()
        self.assertTrue(any("PositionBook" in x for x in m))
        self.assertTrue(any("CTCL" in x for x in m))
        self.assertTrue(any("RISK_REPORT" in x for x in m))
        self.assertTrue(any("Master file" in x for x in m))

    def test_percent(self):
        self.assertEqual(C.parse_percent("2.5"), 2.5)
        self.assertEqual(C.parse_percent("3%"), 3.0)
        for bad in ("", "abc", "0", "-1", "500"):
            with self.assertRaises(Exception):
                C.parse_percent(bad)
        C.set_percent(4)
        self.assertEqual(C.get_percent(), 4.0)

    def test_window(self):
        hol = set()
        # October 2026: last Tuesday = 27 Oct
        self.assertEqual(C.last_tuesday(2026, 10), dt.date(2026, 10, 27))
        w = C.window_info(dt.date(2026, 10, 27), hol)
        self.assertEqual(w["phase"], "expiry_day")
        w = C.window_info(dt.date(2026, 10, 23), hol)
        self.assertEqual(w["phase"], "window")
        w = C.window_info(dt.date(2026, 10, 1), hol)
        self.assertEqual(w["phase"], "before")
        # after the expiry the next month's window is the one shown
        w = C.window_info(dt.date(2026, 10, 28), hol)
        self.assertEqual(w["expiry"], "2026-11-24")

    def test_safe_test_is_one_shot(self):
        self.assertFalse(C.is_safe_test_armed())
        C.arm_safe_test()
        self.assertTrue(C.is_safe_test_armed())
        self.assertTrue(C.consume_safe_test())
        self.assertTrue(C.test_mode())
        self.assertFalse(C.is_safe_test_armed())
        os.environ.pop("PD_TEST_MODE", None)
        self.assertFalse(C.consume_safe_test())

    def test_wa_numbers(self):
        os.environ["PD_WA_DELHI"] = "9899547339 #A, +91 97116 67470"
        try:
            self.assertEqual(C.wa_numbers("DELHI"), ["9899547339", "9711667470"])
        finally:
            os.environ.pop("PD_WA_DELHI", None)

    def test_scripts_compile_and_have_no_outside_references(self):
        d = REPO / "core" / "pipelines" / "physdel_codes"
        for n in ("ltp.py", "send.py"):
            py_compile.compile(str(d / n), doraise=True)
            txt = (d / n).read_text(encoding="utf-8")
            body = txt.split('"""', 2)[-1]                    # skip the explanatory docstring
            self.assertNotIn("C:\\KARTAVYA", body)
            self.assertNotIn("import tkinter", body)
            self.assertNotIn("from tkinter", body)
        py_compile.compile(str(REPO / "core" / "pipelines" / "physical_delivery.py"), doraise=True)

    def test_pipeline_registered(self):
        from core import pipeline_box as pb
        p = pb.PIPELINES["physdel"]
        self.assertGreater(p["runs"], 1)                       # many runs on expiry day: the once-a-day guard must not apply
        self.assertEqual([r[0] for r in p["file_types"]], ["PositionBook"])
        self.assertIn("RISK_REPORT_*.csv", p["common_globs"])
        self.assertEqual(p["master_subdir"], "MAIN_FOLDER")


if __name__ == "__main__":
    unittest.main()
