"""Client peak lookup: runs 5-8 use the 4th NSE C/F peak file (archived only under run 4) - its figures must be carried forward."""
import sys
import types
import unittest
from unittest import mock

from core import short_allocation as S


def _f(run, kind, name, content):
    return {"run_date": "2026-10-29", "tag": f"SA-0{run}", "kind": kind, "filename": name, "content": content.encode()}


FILES = [
    _f(4, "input_f_lis", "F_i04.lis", "x,SN120,85044873.21,0,0,61327391.03,0\\n".replace("\\n", "\n")),
    _f(4, "input_mcx_peak", "MCX_04.csv", "d,s,c,t,SN120,0,0,0,0.00,35762390.53,0,0\n"),
    _f(5, "input_mcx_peak", "MCX_05.csv", "d,s,c,t,SN120,0,0,0,0.00,20762390.53,0,0\n"),
]


class Carry(unittest.TestCase):
    def test_run5_gets_the_4th_nse_files(self):
        fake = types.SimpleNamespace(get_peak_files_range=lambda a, b: FILES)
        with mock.patch.dict(sys.modules, {"core.db_postgres": fake}):
            rows = {r["run"]: r for r in S._client_peak_lookup_pg("SN120")}
        self.assertEqual(rows["5"]["FO_PEAK"], 85044873.21)
        self.assertEqual(rows["5"]["TOTAL_COLL"], 82089781.56)
        self.assertEqual(rows["5"]["SHORTAGE"], -2955091.65)          # same as the SA05 output file
        self.assertEqual(rows["4"]["SHORTAGE"], 12044908.35 + 0.0)


if __name__ == "__main__":
    unittest.main()
