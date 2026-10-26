"""CTCL vs NMASS: MTF risk report deduction wiring (file check, upload type, backup rule, arithmetic)."""
import fnmatch
import re
import unittest
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[1]
SRC = (REPO / "core" / "pipelines" / "ctcl_vs_nmass.py").read_text(encoding="utf-8")


class MtfDeduction(unittest.TestCase):
    def test_arithmetic_negative_nets_only_sign_ignored(self):
        mtf = pl.DataFrame({"ACCOUNTID": ["xyz", "abc", "080011226"],
                            "MTFProfit": ["100", "500", "0"], "MTFLoss": ["9976", "100", "5"]})
        net = mtf.with_columns((pl.col("MTFProfit").cast(pl.Float64) - pl.col("MTFLoss").cast(pl.Float64)).alias("NET")
                               ).filter(pl.col("NET") < 0)
        ded = dict(zip(net["ACCOUNTID"], (-net["NET"]).to_list()))
        self.assertEqual(ded, {"xyz": 9876.0, "080011226": 5.0})
        self.assertEqual(7000 - ded["xyz"], -2876)

    def test_pipeline_code_wiring(self):
        self.assertIn('"MTF_RISK_REPORT": {"patterns": ["MTF_RISK_REPORT_*.csv"]', SRC)
        self.assertIn('"MTF_RISK_REPORT", "T5DEBIT"', SRC)                       # classified as input
        self.assertRegex(SRC, r'MTF_RISK_REPORT"\) and run_number < 5: continue')   # kept until run 5
        self.assertIn("{MTF_NOTE_HTML}", SRC)                                       # one line in the e-mail
        self.assertIn("(\"ACCOUNTID\", \"MTFProfit\", \"MTFLoss\")", SRC)           # header check

    def test_upload_type_registered_and_not_confused_with_risk_report(self):
        from core import pipeline_box as pb
        types = {t[0]: t for t in pb.PIPELINES["ctcl"]["file_types"]}
        self.assertIn("MTF risk report", types)
        self.assertTrue(fnmatch.fnmatch("MTF_RISK_REPORT_26102026.csv", types["MTF risk report"][1]))
        self.assertFalse(fnmatch.fnmatch("MTF_RISK_REPORT_26102026.csv", "RISK_REPORT_*.csv"))
        self.assertFalse(fnmatch.fnmatch("RISK_REPORT_26102026.csv", types["MTF risk report"][1]))

    def test_compiles(self):
        import py_compile
        py_compile.compile(str(REPO / "core" / "pipelines" / "ctcl_vs_nmass.py"), doraise=True)
        py_compile.compile(str(REPO / "api.py"), doraise=True)


if __name__ == "__main__":
    unittest.main()
