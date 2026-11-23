"""NMASS Level-1 margin row (OBLIGATION REPORT): the real answer of 23-11-2026 13:23:10 -> the four FO boxes, the file with the screen's
column names, the date check of the card, the picture block (FO line + MCX line).
Run:  python -m unittest tests.test_nmass_level1 -v"""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import nmass_level1 as L1  # noqa: E402

# the answer of cm-margins-summery-level-I seen on the portal (23 Nov 2026 13:23:10)
REAL = {"status": "success", "messages": {"01010000": "Data available"},
        "data": {"result": {"cmCode": "M50154", "timeStamp": "23 Nov 2026 13:23:10", "effectiveColl": 1.150572286612E10,
                            "mnlw": 5000000.0, "totalMargins": 7.58425241679E9, "cmName": "MASTER CAPITAL SERVICES L",
                            "cash": 1.147388197089E10, "nonCash": 3.184089523E7, "effNonCash": 3.184089523E7,
                            "cmUtilisation": 73.04, "cmPropMargin": 7.584252416789999E9, "tmExcessMargin": 8.156370573351488E8,
                            "totMrgnUtilisation": 8.399889474125148E9},
                 "timestamp": "23 Nov 2026 13:23:10"}}


class Level1(unittest.TestCase):
    def test_parse_and_screen_values(self):
        row = L1.parse(REAL)
        self.assertEqual(row["CM CODE"], "M50154")
        self.assertAlmostEqual(row["CM CASH (A)"], 11473881970.89, places=1)
        self.assertAlmostEqual(row["EFFECTIVE COLLATERAL (C = A+B)"], 11505722866.12, places=1)
        self.assertAlmostEqual(row["TOTAL MARGINS UTILISED (G = E+F)"], 8399889474.13, places=1)
        self.assertEqual(row["TIMESTAMP"], "23 Nov 2026 13:23:10")

    def test_file_has_the_screen_column_names_and_a_dated_name(self):
        with tempfile.TemporaryDirectory() as t:
            p = L1.write_csv(L1.parse(REAL), t)
            self.assertEqual(Path(p).name, "FO_Level1_Margins_23112026132310.csv")
            head = Path(p).read_text(encoding="utf-8-sig").splitlines()[0].split(",")
            self.assertEqual(head[:3], ["CM CODE", "MEMBER NAME", "CM CASH (A)"])
            self.assertIn("TM MARGINS > 90% (F)", head)
            self.assertIn("CM PROP MARGINS (E)", head)
            boxes = L1.read_boxes(p)
            self.assertEqual([round(boxes[k], 2) for k in ("fo_deposit", "fo_used", "fo_pro", "fo_client")], [1150.57, 839.99, 758.43, 81.56])
            from core import obligation_card as oc
            self.assertEqual(oc.fo_date(Path(p).name), dt.date(2026, 11, 23))        # the card's "is it today's" check reads the name
            self.assertTrue(oc._FO_RX.match(Path(p).name))
            self.assertTrue(L1.is_level1(p))

    def test_an_empty_or_failed_answer_is_refused(self):
        for bad in ({"status": "success", "data": {"result": []}}, {"status": "fail"}, {"status": "success", "data": {"result": {"cmCode": "X"}}}, None):
            with self.assertRaises(L1.Level1Error):
                L1.parse(bad)

    def test_picture_has_fo_line_and_mcx_line(self):
        from core import obligation_image as OI
        import pandas as pd
        cols = {"ClientID": ["A"], "Product": ["-"], "PayInPayOut": [1e7], "FuturesMTM": [1e7], "DayNetPremium": [1e7],
                "MCXFuturesMTM": [1e7], "MCXDayNetPremium": [1e7]}
        obl = pd.DataFrame(cols)
        for c in OI.METRICS + OI.MCX_METRICS:
            obl[c + "_Cr"] = obl[c] / 1e7
        cust = obl[["ClientID", "Product"] + OI.METRICS + [m + "_Cr" for m in OI.METRICS]]
        m = {"fo_deposit": 1150.57, "fo_used": 839.99, "fo_pro": 758.43, "fo_client": 81.56,
             "mcx_deposit": 155.72, "mcx_used": 112.3, "pro_util": 103.19, "client_util": 9.11}
        sh = OI.build_sheet(obl, cust, m)
        texts = [c["text"] for c in sh.cells.values()]
        for t in ("FO DEPOSIT", "TOTAL MARGIN USED (FO)", "PRO MARGIN UTILISATION (FO)", "CLIENT MARGIN UTILISATION (FO)",
                  "MCX DEPOSIT", "TOTAL MARGIN USED (MCX)", "PRO MARGIN UTILIZATION (MCX)", "CLIENT MARGIN UTILIZATION (MCX)",
                  "1150.57 Cr", "839.99 Cr", "758.43 Cr", "81.56 Cr", "155.72 Cr", "112.30 Cr", "103.19 Cr", "9.11 Cr", "Custodian Obligation"):
            self.assertIn(t, texts)
        m["fo_pro"] = m["fo_client"] = None                                          # an old collateral file has no such numbers
        self.assertIn("-", [c["text"] for c in OI.build_sheet(obl, cust, m).cells.values()])
        with tempfile.TemporaryDirectory() as t:
            self.assertTrue(Path(OI.render(str(Path(t) / "x.png"), obl, cust, m)).stat().st_size > 1000)


if __name__ == "__main__":
    unittest.main()


class OwnLoginTests(unittest.TestCase):
    """The OBLIGATION card's own NMASS login (Credentials) goes to its child process only, with its own session folder and no shared lock."""

    def _run(self, own):
        import os
        from unittest import mock
        from core import obligation_card as oc
        seen = {}

        def fake_child(cmd, env, timeout):
            seen["env"] = env
            work = Path(env["NMASS_SAVE_DIR"])
            L1.write_csv(L1.parse(REAL), str(work))
            return 0, ""
        keys = ("OBLIGATION_NMASS_USER", "OBLIGATION_NMASS_PASSWORD")
        old = {k: os.environ.get(k) for k in keys}
        with tempfile.TemporaryDirectory() as t:
            os.environ["OBLIGATION_DIR"] = t
            for k in keys:
                os.environ.pop(k, None)
            if own:
                os.environ["OBLIGATION_NMASS_USER"], os.environ["OBLIGATION_NMASS_PASSWORD"] = "M0999999", "secret-x"
            work = Path(t) / "w"
            work.mkdir()
            from core import dl_lock
            with mock.patch.object(oc, "_run_child", fake_child), mock.patch.object(dl_lock, "acquire") as acq, \
                    mock.patch.object(dl_lock, "release") as rel:
                out = oc.step_fo(work)
            seen.update(acq=acq.called, rel=rel.called, out=out.name, sess_exists=(Path(t) / "_nmass_session").is_dir())
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return seen

    def test_own_login_is_used_only_in_the_child_with_its_own_session(self):
        r = self._run(own=True)
        self.assertEqual(r["env"]["CTCL_NMASS_USER"], "M0999999")
        self.assertEqual(r["env"]["CTCL_NMASS_PASSWORD"], "secret-x")
        self.assertTrue(r["env"]["NMASS_SESSION_DIR"].endswith("_nmass_session"))
        self.assertTrue(r["sess_exists"])
        self.assertFalse(r["acq"] or r["rel"])                         # the shared NMASS lock is not touched
        self.assertTrue(r["out"].startswith("FO_Level1_Margins_"))
        import os
        self.assertNotEqual(os.environ.get("CTCL_NMASS_USER"), "M0999999")   # this process's shared login is unchanged

    def test_without_an_own_login_the_shared_one_and_its_lock_are_used(self):
        r = self._run(own=False)
        self.assertNotEqual(r["env"].get("CTCL_NMASS_USER"), "M0999999")
        self.assertNotIn("NMASS_SESSION_DIR", {k for k in r["env"] if k == "NMASS_SESSION_DIR" and r["env"][k].endswith("_nmass_session")})
        self.assertTrue(r["acq"] and r["rel"])
