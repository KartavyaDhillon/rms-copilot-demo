"""PAYOUT CHECK card: upload rule, run (steps with stand-ins), no-requests skip, store failure is not fatal, failure alert, stop,
payout_check's new optional hooks, and the real payout maths on small files.
Run:  python -m unittest tests.test_payout_check_card -v"""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class CardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PAYOUT_CHECK_DIR"] = self.tmp.name
        os.environ["PAYOUT_CHECK_LOG_DIR"] = str(Path(self.tmp.name) / "_logs")
        os.environ["PAYOUT_FOLDER"] = str(Path(self.tmp.name) / "payout")
        Path(os.environ["PAYOUT_FOLDER"]).mkdir()
        from core import payout_check_card as m
        self.m = m
        self.alerts = []
        self.alert = lambda h, d, sender=None: (self.alerts.append((h, d)) or {"sent": True, "reason": "test"})
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def today(self):
        return dt.date.today().strftime("%d%m%Y")

    def upload(self, name=None, data=b"x" * 50, modified_ms=None):
        return self.m.accept_upload(name or f"Update Limits {self.today()} 1540.xlsx", data, by="t", modified_ms=modified_ms)

    # ---- upload ----
    def test_upload_today_with_date_is_taken_and_logged(self):
        r = self.upload()
        self.assertTrue(r["ok"])
        self.assertTrue(self.m.input_status()[0]["ok"])
        log = (Path(os.environ["PAYOUT_CHECK_LOG_DIR"]) / f"payoutchk__{dt.date.today():%d-%m-%Y}__uploads.log").read_text(encoding="utf-8")
        self.assertIn("Upload ACCEPTED", log)
        self.assertIn("15:40:00", log)          # the time written in the file name is the snapshot time

    def test_upload_old_date_refused_and_logged(self):
        old = (dt.date.today() - dt.timedelta(days=1)).strftime("%d%m%Y")
        with self.assertRaises(ValueError) as cm:
            self.upload(f"Update Limits {old}.xlsx")
        self.assertIn("only today", str(cm.exception))
        log = (Path(os.environ["PAYOUT_CHECK_LOG_DIR"]) / f"payoutchk__{dt.date.today():%d-%m-%Y}__uploads.log").read_text(encoding="utf-8")
        self.assertIn("Upload REFUSED", log)
        self.assertFalse(self.m.input_status()[0]["ok"])

    def test_upload_without_date_needs_a_fresh_file(self):
        now = dt.datetime.now()
        fresh = (now - dt.timedelta(minutes=5)).timestamp() * 1000
        stale = (now - dt.timedelta(minutes=90)).timestamp() * 1000
        with self.assertRaises(ValueError):
            self.upload("Update Limits.xlsx", modified_ms=stale)
        self.assertTrue(self.upload("Update Limits.xlsx", modified_ms=fresh)["ok"])

    def test_upload_wrong_file_refused(self):
        for bad in ("PositionBook.csv", "Update Limits.py", "notes.xlsx"):
            with self.assertRaises(ValueError):
                self.upload(bad)
        with self.assertRaises(ValueError):
            self.upload(data=b"")

    def test_new_upload_replaces_the_old_one(self):
        self.upload(f"Update Limits {self.today()} 1540.xlsx")
        self.upload(f"Update Limits {self.today()} 1650.xlsx")
        names = [p.name for p in self.m.root().iterdir() if p.name.lower().startswith("update limits")]
        self.assertEqual(len(names), 1)
        self.assertIn("1650", names[0])

    # ---- run ----
    def fns(self, records=3, fail_check=None):
        def download():
            self.calls.append("download")
            f = Path(os.environ["PAYOUT_FOLDER"]) / "base.xlsx"
            f.write_bytes(b"b")
            return {"status": "ok", "records": records, "file": str(f) if records else ""}

        def check(update_file, base_file):
            self.calls.append("check")
            if fail_check:
                raise RuntimeError(fail_check)
            run = Path(os.environ["PAYOUT_FOLDER"]) / "BACKUPFILES AUTOMATED" / dt.date.today().strftime("%d %b %Y").upper() / "01"
            run.mkdir(parents=True)
            (run / "PAYOUT_CHECKING_x.xlsx").write_bytes(b"o")
            (run / "base.xlsx").write_bytes(b"b")
            return {"status": "ok"}
        return {"download": download, "check": check}

    def test_run_ok_twice_without_password(self):
        self.upload()
        r = self.m.run("manual", "t", alert=self.alert, step_fns=self.fns())
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.calls, ["download", "check"])
        st = self.m.status()
        self.assertEqual(st["status"], "done")
        self.assertEqual(st["counts"]["requests"], 3)
        self.assertFalse(st["needs_password"])
        self.assertTrue(all(s["status"] in ("ok", "skipped") for s in st["steps"]), st["steps"])
        # the files of the run are in the card backup
        days = self.m.list_backup_dates()
        self.assertEqual(days, [dt.date.today().strftime("%d-%m-%Y")])
        names = [f["name"] for f in self.m.list_files(days[0])]
        self.assertTrue(any(n.endswith("PAYOUT_CHECKING_x.xlsx") for n in names), names)
        # second run (4:50) starts without a password, once a new file is uploaded
        self.upload(f"Update Limits {self.today()} 1650.xlsx")
        self.assertTrue(self.m.request_run(background=False, by="t") is not None)
        self.assertEqual(self.m.read_state()["runs"], 2)

    def test_no_requests_today_skips_the_check(self):
        self.upload()
        r = self.m.run("manual", "t", alert=self.alert, step_fns=self.fns(records=0))
        self.assertTrue(r["ok"])
        self.assertNotIn("check", self.calls)
        steps = {s["key"]: s["status"] for s in self.m.status()["steps"]}
        self.assertEqual(steps["check"], "skipped")

    def test_update_limits_is_filed_for_the_18_00_store_and_nothing_is_stored_in_the_run(self):
        self.upload()
        self.m.run("manual", "t", alert=self.alert, step_fns=self.fns())
        self.assertEqual([s["key"] for s in self.m.status()["steps"]], ["input", "download", "check", "backup"])
        from core import update_limits as ul
        pf = ul.payout_files(dt.date.today())
        self.assertEqual(len(pf), 1, pf)
        self.assertTrue(pf[0]["time"])                                       # the snapshot time travels with the file
        self.assertTrue(pf[0]["source"].startswith("PAYOUT CHECK"))
        self.assertEqual([p.name for p in self.m._stash_dir().iterdir()], [])   # the stash copy was moved to the backup

    def test_no_requests_still_files_the_update_limits(self):
        self.upload()
        self.m.run("manual", "t", alert=self.alert, step_fns=self.fns(records=0))
        from core import update_limits as ul
        self.assertEqual(len(ul.payout_files(dt.date.today())), 1)

    def test_missing_file_fails_with_alert(self):
        r = self.m.run("manual", "t", alert=self.alert, step_fns=self.fns())
        self.assertFalse(r["ok"])
        self.assertEqual(self.m.status()["status"], "failed")
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.calls, [])

    def test_check_failure_keeps_the_upload_and_alerts(self):
        self.upload()
        r = self.m.run("manual", "t", alert=self.alert, step_fns=self.fns(fail_check="Payout check: e-mail did not go"))
        self.assertFalse(r["ok"])
        self.assertEqual(len(self.alerts), 1)
        self.assertTrue(self.m.input_status()[0]["ok"])          # still there for the retry
        self.assertFalse(self.m.is_running())                    # not stuck on Running

    def test_stop_during_a_run(self):
        self.upload()
        fns = self.fns()
        orig = fns["download"]

        def download():
            self.m.stop_running("t")
            return orig()
        fns["download"] = download
        r = self.m.run("manual", "t", alert=self.alert, step_fns=fns)
        self.assertTrue(r.get("stopped"))
        self.assertEqual(self.m.status()["status"], "stopped")
        self.assertNotIn("check", self.calls)
        self.assertEqual(self.alerts, [])
        self.assertFalse(self.m.is_running())

    def test_stop_when_nothing_runs(self):
        self.assertFalse(self.m.stop_running("t"))

    def test_sender_is_the_cards_own(self):
        os.environ["PAYOUTCHK_SENDER_EMAIL"], os.environ["PAYOUTCHK_SENDER_PASSWORD"] = "own@example.com", "pw"
        os.environ["SENDER_EMAIL"], os.environ["SENDER_PASSWORD"] = "shared@example.com", "pw2"
        try:
            from core import sender_override
            self.assertEqual(sender_override.sender("payoutchk")[0], "own@example.com")
        finally:
            os.environ.pop("PAYOUTCHK_SENDER_EMAIL", None)
            os.environ.pop("PAYOUTCHK_SENDER_PASSWORD", None)

    def test_registered_with_the_console(self):
        from core import pipeline_box, card_alerts
        p = pipeline_box.PIPELINES["payoutchk"]
        self.assertEqual(p["logdir"], "payoutchk")
        self.assertIn("KORP_USERNAME", [v["user_key"] for v in p["cred"].values()])
        self.assertEqual(card_alerts.OWN_ALERT["payoutchk"], "core.payout_check_card")
        self.assertIn("payoutchk", card_alerts.OWN_LOGDIRS)


class PayoutCheckHooks(unittest.TestCase):
    """payout_check.run_payout_check with the new optional hooks, on tiny real files and a fake mail server."""

    def test_forced_files_check_and_mail_cap(self):
        import polars as pl
        import pandas as pd
        from core.pipelines import payout_check as pc
        tmp = Path(tempfile.mkdtemp())
        try:
            pc.PAYOUT_DIR = pc.BASE_DIR = pc.OUTPUT_DIR = str(tmp)
            pc.CTCL_DIR = str(tmp / "nope")
            tdy = dt.date.today().strftime("%d%m%Y")
            base = tmp / f"{tdy}101010.xlsx"
            pd.DataFrame({"Zone": ["N", "N"], "Region": ["R", "R"], "Account ID": ["A1", "A2"], "Request Amount": [100.0, 5.0],
                          "Release Amount": [0.0, 0.0], "Request Date": ["d", "d"], "Bank Account No": ["1", "2"], "Bank Code": ["b", "b"],
                          "Maker": ["m", "m"], "Approve By": ["a", "a"], "Approve Date": ["d", "d"], "Checker Time": ["t", "t"]}).to_excel(base, index=False)
            upd = tmp / "Update Limits custom.xlsx"
            pd.DataFrame({"Entity": ["A1", "A2"], "Available Margin": [-50.0, 20.0], "MarginUsed": [10.0, 1.0],
                          "Payin": [1.0, 1.0], "Cash": [2.0, 2.0]}).to_excel(upd, index=False)
            os.environ["SENDER_EMAIL"], os.environ["SENDER_PASSWORD"] = "x@example.com", "pw"
            # the mail server does not exist -> with the cap it stops (no endless loop)
            os.environ["SMTP_SERVER"], os.environ["SMTP_PORT"] = "127.0.0.1", "1"
            import time as _t
            orig_sleep = _t.sleep
            _t.sleep = lambda s: None
            try:
                with self.assertRaises(RuntimeError) as cm:
                    pc.run_payout_check(_update_file=str(upd), _base_file=str(base), _max_mail_tries=2)
            finally:
                _t.sleep = orig_sleep
            self.assertIn("e-mail did not go", str(cm.exception))
            outs = list(tmp.glob("PAYOUT_CHECKING_*.xlsx"))
            self.assertEqual(len(outs), 1)
            df = pl.read_excel(outs[0])
            self.assertEqual(df["Account ID"].to_list(), ["A1"])             # only the negative Available Margin client
            self.assertEqual(df["TOTAL DEPOSIT"].to_list(), [3.0])           # Payin + Cash
            # stop flag ends the mail loop at once
            with self.assertRaises(RuntimeError) as cm2:
                pc.run_payout_check(_update_file=str(upd), _base_file=str(base), _cancel=lambda: True)
            self.assertIn("stopped", str(cm2.exception))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
