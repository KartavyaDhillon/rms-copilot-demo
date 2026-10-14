"""RMS LIMITS MAND KAMBALA: leading-zero-safe matching, CATEGORY refresh, appended codes, e-mail-then-FTP order.

    python -m unittest tests.test_rms_limits_mand -v
"""
import csv
import unittest
from unittest import mock

from tests.test_scheduler_processes import Base, Ctx

TAIL = "|" * 29
HEADER = "RMS Limits\x95MAND"          # the odd byte between the words must survive untouched


def line(code, cat, lim):
    return code + "|" * 16 + cat + "|no" + TAIL + str(lim)


MASTER_LINES = [line("09110005", "MTM_SINGLE_LEVEL_EXP", 280000),
                line("09110022", "MTM_SINGLE_LEVEL", 2200000),
                line("11AC82", "MTM_MTF", 140000)]


class Pure(unittest.TestCase):
    def test_codes_match_without_leading_zeros(self):
        from core.scheduler.processes import rms_limits_mand_kambala as rl
        self.assertEqual(rl.norm("09110005"), rl.norm("9110005"))
        self.assertEqual(rl.norm(" 11ac82 "), "11AC82")

    def test_category_changes_only_when_different_and_code_spelling_is_kept(self):
        from core.scheduler.processes import rms_limits_mand_kambala as rl
        risk = {"9110005": "MTM_SINGLE_LEVEL", "9110022": "MTM_SINGLE_LEVEL", "11AC82": "MTM_MTF"}
        out, changed, added, _ = rl.apply(MASTER_LINES, risk, [], Ctx())
        self.assertEqual(len(changed), 1)
        self.assertTrue(out[0].startswith("09110005|"))                 # leading zero kept
        self.assertIn("|MTM_SINGLE_LEVEL|no|", out[0])
        self.assertEqual(out[1:], MASTER_LINES[1:])                     # untouched lines are identical

    def test_new_codes_go_to_the_end_category_from_risk_when_blank(self):
        from core.scheduler.processes import rms_limits_mand_kambala as rl
        add = [{"account": "09110099", "limit": "300000", "category": ""},
               {"account": "7XYZ1", "limit": "100000", "category": "MTM_MTF"}]
        out, _, added, _ = rl.apply(MASTER_LINES, {"9110099": "MTM_SINGLE_LEVEL_EXP"}, add, Ctx())
        self.assertEqual(out[-2], line("09110099", "MTM_SINGLE_LEVEL_EXP", 300000))
        self.assertEqual(out[-1], line("7XYZ1", "MTM_MTF", 100000))
        self.assertEqual(len(added), 2)

    def test_no_category_anywhere_is_refused(self):
        from core.scheduler.processes import rms_limits_mand_kambala as rl
        with self.assertRaises(ValueError):
            rl.apply(MASTER_LINES, {}, [{"account": "NEW1", "limit": "5", "category": ""}], Ctx())

    def test_existing_code_is_not_added_twice(self):
        from core.scheduler.processes import rms_limits_mand_kambala as rl
        out, _, added, notes = rl.apply(MASTER_LINES, {}, [{"account": "9110005", "limit": "1", "category": "X1"}], Ctx())
        self.assertEqual(len(out), 3)
        self.assertFalse(added)
        self.assertTrue(notes)

    def test_form_validation(self):
        from core.scheduler.processes import rms_limits_mand_kambala as rl
        errs, rows = rl.clean_rows([{"account": "09110022", "limit": "2,80,000", "category": "mtm_mtf"}])
        self.assertEqual((errs, rows[0]["limit"], rows[0]["category"]), ([], "280000", "MTM_MTF"))
        self.assertTrue(rl.clean_rows([{"account": "A B", "limit": "x"}])[0])
        self.assertTrue(rl.clean_rows([])[0])


class Run(Base):
    def test_full_run_writes_file_emails_then_uploads(self):
        from core.scheduler import paths
        from core.scheduler.processes import rms_limits_mand_kambala as rl
        m = rl.master_path()
        m.write_bytes(("\r\n".join([HEADER] + MASTER_LINES) + "\r\n").encode("latin-1"))
        rep = m.parent / "RISK_REPORT_TEST.csv"
        with open(rep, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ACCOUNTID", "CATEGORY"])
            w.writerow(["9110005", "MTM_MTF"])
        order = []
        with mock.patch("core.scheduler.helpers.find_risk_report", return_value=rep), \
             mock.patch("core.scheduler.processes._kit.require_trading_day"), \
             mock.patch("core.scheduler.helpers.send_email", side_effect=lambda *a, **k: order.append("mail")), \
             mock.patch("core.scheduler.processes.rms_limits_mand_kambala.upload_is_on", return_value=True), \
             mock.patch("core.scheduler.helpers.kambala_upload", side_effect=lambda f, c: order.append("ftp") or True):
            res = rl.run_core({"to": "a@mastertrust.co.in"}, Ctx())
        self.assertEqual(order, ["mail", "ftp"])
        data = m.read_bytes().decode("latin-1")
        self.assertTrue(data.startswith(HEADER + "\r\n"))
        self.assertIn("09110005" + "|" * 16 + "MTM_MTF|no", data)
        self.assertIn("1 category change", res)

    def test_upload_off_only_emails_and_manual_add_makes_the_scheduled_run_skip(self):
        from core.scheduler import helpers as H
        from core.scheduler.processes import rms_limits_mand_kambala as rl
        m = rl.master_path()
        m.write_bytes(("\n".join([HEADER] + MASTER_LINES) + "\n").encode("latin-1"))
        rep = m.parent / "RISK_REPORT_TEST.csv"
        rep.write_text("ACCOUNTID,CATEGORY\n9110099,MTM_MTF\n")
        order = []
        with mock.patch("core.scheduler.helpers.find_risk_report", return_value=rep), \
             mock.patch("core.scheduler.processes._kit.require_trading_day"), \
             mock.patch("core.scheduler.helpers.send_email", side_effect=lambda *a, **k: order.append("mail")), \
             mock.patch("core.scheduler.processes.rms_limits_mand_kambala.upload_is_on", return_value=False), \
             mock.patch("core.scheduler.helpers.kambala_upload", side_effect=lambda f, c: order.append("ftp") or True):
            ctx = Ctx()
            ctx.trigger = H.MANUAL
            rl.run_core({"to": "a@mastertrust.co.in"}, ctx,
                        [{"account": "09110099", "limit": "5000", "category": ""}], manual_add=True)
            self.assertEqual(order, ["mail"])                                # upload is off by default
            self.assertIn("09110099" + "|" * 16 + "MTM_MTF|no", m.read_text("latin-1"))   # category from risk report
            sched = Ctx()
            sched.trigger = "SCHEDULED"
            with self.assertRaises(H.Skipped):
                rl.run_core({"to": "a@mastertrust.co.in"}, sched)           # scheduled run is skipped today
            rl.run_core({"to": "a@mastertrust.co.in"}, ctx)                 # Run Now still works
        self.assertEqual(order, ["mail", "mail"])


if __name__ == "__main__":
    unittest.main()
