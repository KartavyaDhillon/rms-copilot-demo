"""Tests for the scheduled processes + helpers. Nothing real is sent: WhatsApp / SMTP / POP3 are mocked.

    python -m unittest discover -s tests -t . -v
"""
import csv
import datetime as dt
import importlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import pandas  # noqa: F401
    import PIL     # noqa: F401
    HAVE_IMG = True
except Exception:
    HAVE_IMG = False


class Ctx:
    def __init__(self, trigger="SCHEDULED"):
        self.trigger, self.cancelled, self.lines = trigger, False, []

    def log(self, m):
        self.lines.append(str(m))

    def text(self):
        return "\n".join(self.lines)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = {k: os.environ.get(k) for k in ("SCHED_DATA_ROOT", "SCHED_ENV_DIR", "SA_COMMON_DIR", "CLI_FOLDER")}
        self.old_sched = {k: v for k, v in os.environ.items() if k.startswith("SCHED_") and k not in self.old}
        os.environ["SCHED_DATA_ROOT"] = str(self.tmp / "data")
        os.environ["SCHED_ENV_DIR"] = str(self.tmp / "envdir")           # never touch the real .env
        (self.tmp / "envdir").mkdir()
        os.environ["SA_COMMON_DIR"] = str(self.tmp / "COMMON")
        os.environ["CLI_FOLDER"] = str(self.tmp / "CLI")
        (self.tmp / "COMMON").mkdir()
        (self.tmp / "CLI").mkdir()

    def tearDown(self):
        for k in [k for k in os.environ if k.startswith("SCHED_") and k not in self.old]:
            os.environ.pop(k, None)                                         # values written by saves
        os.environ.update(self.old_sched)
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_risk(self, rows, name=None):
        name = name or f"RISK_REPORT_{dt.datetime.now():%d%m%Y}.csv"
        p = self.tmp / "COMMON" / name
        cols = ["ACCOUNTID", "FIN_WITHOUT_MGN", "CASH_LEDGER", "TOTAL_STOCK_EX_REPLG", "MARGIN", "FNONOV", "Zone", "CPCode"]
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow(r)
        return p


class Helpers(Base):
    def test_parse_lists(self):
        from core.scheduler import helpers as H
        self.assertEqual(H.parse_list("a@x.com, b@x.com;a@x.com\nc"), ["a@x.com", "b@x.com", "c"])
        self.assertEqual(H.parse_phones("+91 98123-45678, +917541827310"), ["+919812345678", "+917541827310"])
        with self.assertRaises(ValueError):
            H.parse_phones("9812345678")                  # no country code
        with self.assertRaises(ValueError):
            H.parse_emails("not-an-email")

    def test_paths_are_safe(self):
        from core.scheduler import paths
        self.assertEqual(paths.safe_name("..\\..\\evil.xlsx"), "evil.xlsx")
        self.assertEqual(paths.safe_name("a:b?.csv"), "a_b_.csv")
        with self.assertRaises(ValueError):
            paths.safe_name("..")
        with self.assertRaises(ValueError):
            paths.proc_dir("../x")
        p = paths.ensure_process_dirs("PARTIC ID EMAIL")
        for s in paths.SUBS:
            self.assertTrue((p / s).is_dir())
        self.assertEqual(p.parent.name, "scheduler")

    def test_risk_report_found_only_for_today_and_never_removed(self):
        from core.scheduler import helpers as H
        with self.assertRaises(FileNotFoundError):
            H.find_risk_report()
        old = self.write_risk([], "RISK_REPORT_01012020.csv")
        with self.assertRaises(FileNotFoundError):          # an old day's file is never accepted
            H.find_risk_report()
        today = self.write_risk([])
        self.assertEqual(H.find_risk_report(), today)
        self.assertTrue(old.exists() and today.exists())

    def test_backup_never_moves_the_risk_report(self):
        from core.scheduler import helpers as H, paths
        paths.ensure_process_dirs("T")
        risk = self.write_risk([])
        other = paths.proc_dir("T", "outputs") / "x.xlsx"
        other.write_text("x")
        H.backup_files("T", [risk, other], Ctx())
        self.assertTrue(risk.exists())
        self.assertFalse(other.exists())

    def test_sent_markers_only_block_scheduled_runs(self):
        from core.scheduler import helpers as H, paths
        paths.ensure_process_dirs("T")
        self.assertFalse(H.already_sent("T", "k", Ctx()))
        H.mark_sent("T", "k")
        self.assertTrue(H.already_sent("T", "k", Ctx("SCHEDULED")))
        self.assertTrue(H.already_sent("T", "k", Ctx("MISSED_RUN_RECOVERY")))
        self.assertFalse(H.already_sent("T", "k", Ctx(H.MANUAL)))     # Run Now may send again


class Connections(Base):
    def test_secrets_are_masked_kept_and_audited_without_values(self):
        from core.scheduler import connections as C
        with mock.patch("core.scheduler.store.audit") as aud:
            self.assertEqual(C.update("whatsapp", {"auth_token": "SUPERSECRETTOKEN1234", "template_name": "t1"}, "me"), ["auth_token", "template_name"])
            self.assertEqual(C.update("whatsapp", {"auth_token": ""}, "me"), [])           # blank = keep
            self.assertEqual(C.get("whatsapp")["auth_token"], "SUPERSECRETTOKEN1234")
            self.assertNotIn("SUPERSECRET", json.dumps(aud.call_args_list, default=str))
        shown = json.dumps(C.view())
        self.assertNotIn("SUPERSECRET", shown)
        self.assertIn("1234", shown)                                                      # only the hint
        env, local = (self.tmp / "envdir" / ".env").read_text(), (self.tmp / "envdir" / ".env.local").read_text()
        self.assertIn("SCHED_WHATSAPP_TEMPLATE_NAME='t1'", env)
        self.assertNotIn("SUPERSECRET", env)                                              # secrets only in .env.local
        self.assertIn("SCHED_WHATSAPP_AUTH_TOKEN='SUPERSECRETTOKEN1234'", local)
        self.assertIn("# scheduler connection: WhatsApp", env)
        with self.assertRaises(KeyError):
            C.update("nope", {}, "me")

    def test_require_explains_what_is_missing(self):
        from core.scheduler import connections as C
        with self.assertRaises(RuntimeError) as e:
            C.require("smtp", "password")
        self.assertIn("Credentials", str(e.exception))
        self.assertIn("'password' is not set", str(e.exception))


class ProcessSettings(Base):
    def _pdef(self):
        from core.scheduler import catalog
        importlib.reload(importlib.import_module("core.scheduler.processes.sn327_whatsapp"))
        return catalog.get("sn327_whatsapp")

    def test_recipients_are_validated_saved_once_and_win_over_schedule_params(self):
        from core.scheduler import catalog, proc_settings
        d = self._pdef()
        with mock.patch("core.scheduler.store.audit"):
            with self.assertRaises(ValueError):
                proc_settings.save(d, {"to": "9812345678"}, "me")               # no country code
            with self.assertRaises(ValueError):
                proc_settings.save(d, {"to": ""}, "me")                         # required
            proc_settings.save(d, {"to": "+919812345678, +917541827310"}, "me")
        self.assertEqual(proc_settings.get(d)["to"], "+919812345678,+917541827310")
        env = (self.tmp / "envdir" / ".env").read_text()
        self.assertIn("# scheduler process: SN327 WHATSAPP", env)                 # visible in .env under the process name
        self.assertIn("SCHED_SN327_WHATSAPP_TO='+919812345678,+917541827310'", env)
        with mock.patch("core.scheduler.store.audit"):
            proc_settings.save(d, {"to": "+919812345678"}, "me")                   # edit in place, no duplicate line
        self.assertEqual((self.tmp / "envdir" / ".env").read_text().count("SCHED_SN327_WHATSAPP_TO="), 1)
        eff = catalog.effective_params(d, {"account": "SN327", "to": "+910000000000"})
        self.assertEqual(eff["to"], "+919812345678")                               # process-level wins
        self.assertEqual(eff["account"], "SN327")

    def test_schedules_cannot_carry_process_level_params(self):
        from core.scheduler import catalog
        d = self._pdef()
        errs, clean = catalog.validate_params(d, {"account": "SN327"})            # recipients not required on a schedule
        self.assertEqual(errs, [])
        errs, _ = catalog.validate_params(d, {"to": "+919812345678"})
        self.assertTrue(errs)


class Registration(Base):
    def test_four_processes_registered_with_the_agreed_names(self):
        from core.scheduler import catalog
        for mod in ("mcx_web_morning_email", "sn327_whatsapp", "sn359_whatsapp", "partic_id_email"):
            importlib.reload(importlib.import_module(f"core.scheduler.processes.{mod}"))   # re-registers
        names = {d.name for d in catalog.all_defs()}
        for n in ("MCX WEB MORNING EMAIL", "SN327 WHATSAPP", "SN359 WHATSAPP", "PARTIC ID EMAIL"):
            self.assertIn(n, names)
        for d in catalog.all_defs():
            self.assertFalse(d.retry_safe)                  # sends are never auto-retried by the scheduler
            self.assertEqual(d.folder, d.name)
        self.assertTrue(catalog.get("mcx_web_morning_email").upload_exts)
        self.assertTrue(catalog.get("partic_id_email").upload_exts)


@unittest.skipUnless(HAVE_IMG, "pandas / pillow not installed")
class WhatsApp(Base):
    ROWS = [["SN359", 100, 50, 1000, 20, 5, "Z1", ""], ["SN232", 1, 1, 1, 1, 1, "Z1", ""],
            ["SN233", 2, 2, 2, 2, 2, "Z1", ""], ["SN327", 700, 80, 5000, 300, 40, "Z1", ""]]

    def test_sn359_sends_to_each_number_once_and_keeps_the_risk_report(self):
        from core.scheduler.processes import sn359_whatsapp as m
        risk = self.write_risk(self.ROWS)
        calls = []
        with mock.patch("core.scheduler.helpers.whatsapp_send_image", side_effect=lambda img, to, label, d, ctx, **k: calls.append((Path(img).name, to))):
            out = m._run({"main_to": "+919810000001", "second_to": "+917540000002"}, Ctx())
        self.assertEqual(len(calls), 2)
        self.assertIn("2 WhatsApp", out)
        self.assertTrue(risk.exists())
        from core.scheduler.catalog import Skipped
        with mock.patch("core.scheduler.helpers.whatsapp_send_image") as send:    # scheduled again the same day → nothing
            with self.assertRaises(Skipped) as sk:
                m._run({"main_to": "+919810000001", "second_to": "+917540000002"}, Ctx())
            self.assertIn("Already sent today at", str(sk.exception))              # says WHEN, not a silent success
            send.assert_not_called()
            m._run({"main_to": "+919810000001"}, Ctx("MANUAL_RUN_NOW"))             # Run Now sends again
            self.assertEqual(send.call_count, 1)

    def test_one_failing_number_does_not_stop_the_others_but_the_run_fails(self):
        from core.scheduler.processes import sn327_whatsapp as m
        self.write_risk(self.ROWS)
        sent = []

        def fake(img, to, label, d, ctx, **k):
            if to.endswith("0001"):
                raise RuntimeError("boom")
            sent.append(to)
        with mock.patch("core.scheduler.helpers.whatsapp_send_image", side_effect=fake):
            with self.assertRaises(RuntimeError) as e:
                m._run({"to": "+919810000001, +919810000002"}, Ctx())
        self.assertEqual(sent, ["+919810000002"])
        self.assertIn("1 send(s) failed", str(e.exception))

    def test_missing_risk_report_fails_clearly(self):
        from core.scheduler.processes import sn327_whatsapp as m
        with self.assertRaises(FileNotFoundError):
            m._run({"to": "+919810000001"}, Ctx())

    def test_indian_number_format(self):
        from core.scheduler.imaging import format_indian
        self.assertEqual(format_indian(1234567), "12,34,567")
        self.assertEqual(format_indian(999), "999")
        self.assertEqual(format_indian(-100000), "-1,00,000")


@unittest.skipUnless(HAVE_IMG, "pandas not installed")
class McxMorning(Base):
    def _rsk(self, folder, name):
        import pandas as pd
        p = Path(folder) / name
        pd.DataFrame([["junk", None], ["UCC Code", "Limit"], ["SN327", 5], ["SN359", 7]]).to_excel(p, index=False, header=False)
        return p

    def test_accepts_both_names_and_prefers_an_uploaded_file(self):
        from core.scheduler import paths
        from core.scheduler.processes import mcx_web_morning_email as m
        stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
        self._rsk(self.tmp / "CLI", f"abc123-{stamp}.xlsx")
        got = m._locate(Ctx())
        self.assertEqual(got.parent, paths.proc_dir(m.FOLDER, "inputs"))        # copied, CLI original left alone
        self.assertTrue((self.tmp / "CLI" / f"abc123-{stamp}.xlsx").exists())
        up = self._rsk(paths.proc_dir(m.FOLDER, "inputs"), "MCX_DEPOSIT_09.15.xlsx")
        self.assertEqual(m._locate(Ctx()), up if up.stat().st_mtime >= got.stat().st_mtime else got)

    def test_no_file_fails_fast_without_any_login(self):
        from core.scheduler.processes import mcx_web_morning_email as m
        with self.assertRaises(FileNotFoundError) as e:
            m._run({"zochnd_to": "a@x.com", "zoludh_to": "b@x.com"}, Ctx())
        self.assertIn("CLI", str(e.exception))

    def test_full_run_splits_by_zone_emails_each_and_keeps_risk(self):
        from core.scheduler import paths
        from core.scheduler.processes import mcx_web_morning_email as m
        stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
        self._rsk(paths.proc_dir(m.FOLDER, "inputs"), f"zz9-{stamp}.xlsx")
        risk = self.write_risk([["SN327", 0, 0, 0, 0, 0, "ZOCHND", ""], ["SN359", 0, 0, 0, 0, 0, "ZOLUDH", ""]])
        mails = []
        with mock.patch("core.scheduler.helpers.send_email", side_effect=lambda s, b, to, cc, att, ctx, **k: mails.append((s, to))):
            out = m._run({"zochnd_to": "a@x.com", "zoludh_to": "b@x.com"}, Ctx())
        self.assertEqual(len(mails), 2)
        self.assertTrue(risk.exists())
        self.assertIn("2 zone", out)
        from core.scheduler.catalog import Skipped
        with mock.patch("core.scheduler.helpers.send_email") as se:                # same day again → no duplicate mails
            with self.assertRaises(Skipped) as sk:
                m._run({"zochnd_to": "a@x.com", "zoludh_to": "b@x.com"}, Ctx())
            self.assertIn("Already e-mailed today at", str(sk.exception))
            se.assert_not_called()
        logs = list(paths.proc_dir(m.FOLDER, "logs").glob("*_SKIPPED.log"))        # the skip is explained in the log file
        self.assertTrue(logs and "SKIPPED: Already e-mailed" in logs[0].read_text())


class ParticMatching(unittest.TestCase):
    def test_matching_rules(self):
        from core.scheduler import partic_lib as P
        risk = {"C1": "NSECAP-111$NSEFNO-222", "C2": "", "C3": "BSECAP-9"}
        rows = [("C1", "NSE", "111"), ("C1", "NFO", "999"), ("C2", "NSE", "1"), ("C9", "NSE", "1"), ("C3", "CDS", "5"), ("C3", "NSE", "7")]
        matched, mismatched, notfound = P.run_matching(rows, risk)
        self.assertEqual(len(matched), 1)
        self.assertEqual(len(notfound), 2)                                          # blank CPCode + unknown client
        reasons = " | ".join(m["Reason"] for m in mismatched)
        self.assertIn("Partic_ID MISMATCH in NSEFNO", reasons)
        self.assertIn("not enabled on the trading platform", reasons)
        self.assertIn("No NSECAP entry", reasons)

    def test_table_header_detection_and_txt_reading(self):
        from core.scheduler import partic_lib as P
        d = Path(tempfile.mkdtemp())
        try:
            f = d / "output.txt"
            f.write_text("client_id\tsegment\tpartic_id\nC1\tNSE\t111\n\nC2\tBSE\t222\n", encoding="utf-8")
            self.assertEqual(P.read_partic_rows(f), [("C1", "NSE", "111"), ("C2", "BSE", "222")])
            self.assertEqual(P.find_partic_file(d), f)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
