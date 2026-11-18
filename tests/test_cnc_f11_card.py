"""CNC F11 card: the calculation (same result as the old CNC_final.py functions), upload, run with a fake mail server, failure, stop.
Run:  python -m unittest tests.test_cnc_f11_card -v"""
import datetime as dt
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

POSITION = (
    "ClientID,Exch,Symbol,Instrument,Product,NetBuyQty,NetSellQty,NetQty\n"
    "A1,NSE,TCS,EQ,MTF,100,0,100\n"
    "A1,NSE,TCS,EQ,CNC,0,40,-40\n"
    "A2,NSE,INFY,EQ,NRML,50,0,50\n"          # no sell side -> not matched
    "A3,NSE,ITC,EQ,CNC,30,10,20\n"           # CNC with a buy and a sell quantity -> appended to Sheet4
    "A4,NSE,SBIN,EQ,MTFS,0,5,-5\n"           # sell side without a buy -> not matched
)


class FakeSMTP:
    sent = []
    fail = False

    def __init__(self, host, port, timeout=0):
        pass

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg, to_addrs=None):
        if FakeSMTP.fail:
            raise OSError("connection dropped")
        FakeSMTP.sent.append((msg, list(to_addrs or [])))

    def quit(self):
        pass


class CncCardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CNC_F11_DIR"] = self.tmp.name
        os.environ["CNC_F11_LOG_DIR"] = str(Path(self.tmp.name) / "_logs")
        os.environ["SENDER_EMAIL"], os.environ["SENDER_PASSWORD"] = "x@example.com", "pw"
        for k in ("CNCF11_SENDER_EMAIL", "CNCF11_SENDER_PASSWORD"):
            os.environ.pop(k, None)
        from core import cnc_f11_card as m
        self.m = m
        m.apply_defaults()
        FakeSMTP.sent, FakeSMTP.fail = [], False
        self.alerts = []
        self.alert = lambda h, d, sender=None: (self.alerts.append((h, d)) or {"sent": True, "reason": "test"})

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, text=POSITION):
        (self.m.root() / "PositionBook.csv").write_text(text, encoding="utf-8")

    def test_calculation_matches_the_old_script(self):
        self.put()
        res = self.m.calculate(self.m.root() / "PositionBook.csv")
        s4 = res["sheet4"].reset_index(drop=True)
        self.assertEqual(len(s4), 2)
        a1 = s4[s4["ClientID"] == "A1"].iloc[0]
        self.assertEqual((a1["NetBuyQty"], a1["NetSellQty"], a1["NetQty"], a1["Product"]), (100, 40, 60, "CNC"))
        self.assertTrue((s4["ClientID"] == "A3").any())
        self.assertNotIn("Union", s4.columns)
        self.assertNotIn("VL", s4.columns)
        # same maths as core/pipelines/cnc_f11.py (the earlier port of CNC_final.py)
        try:
            from core.pipelines import cnc_f11 as old
        except Exception:                                        # noqa: BLE001
            self.skipTest("old module not importable here")
        r = old.process(old.read_file(str(self.m.root() / "PositionBook.csv")))
        b = old.add_union(r[(r["Product"].isin(["MTF", "NRML"])) & (r["NetQty"] > 0)])
        s = old.add_union(r[(r["Product"].isin(["MTFS", "CNC"])) & (r["NetQty"] < 0)])
        b, s2 = old.add_vl_column(b, s), old.add_vl_column(s, b)
        s4o = old.reorder_columns(b[b["VL"] != "N/A"])
        s5o = old.reorder_columns(s2[s2["VL"] != "N/A"])
        s4o = old.append_filtered_cnc(r, old.update_sheet4_quantities(s4o, s5o, b)).reset_index(drop=True)
        self.assertTrue(s4o.equals(s4))

    def test_run_mails_backs_up_and_cleans(self):
        self.put()
        r = self.m.run("manual", "tester", alert=self.alert, sleep=lambda s: None, smtp_factory=FakeSMTP)
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(FakeSMTP.sent), 1)
        msg, to = FakeSMTP.sent[0]
        self.assertEqual(msg["Subject"], "CNC_F11")
        names = [p.get_filename() for p in msg.walk() if p.get_filename()]
        today = dt.date.today().strftime("%d-%m-%Y")
        self.assertEqual(names, [f"CNC_F11_DATED_{today}.xlsx"])
        self.assertIn("rms_del@mastertrust.co.in", to)
        base = self.m.root()
        self.assertEqual([p.name for p in base.iterdir() if p.suffix.lower() in (".csv", ".xlsx")], [])
        bk = sorted(p.name for p in (base / "BACKUP AUTOMATION" / today).iterdir())
        self.assertEqual(bk, sorted([f"CNC_F11_DATED_{today}.xlsx", "PositionBook.csv", "result.xlsx"]))
        st = self.m.status()
        self.assertEqual(st["status"], "done")
        self.assertTrue(st["needs_password"])
        self.assertEqual(st["counts"]["sheet4"], 2)
        self.assertEqual(self.alerts, [])
        self.assertIn(today, self.m.list_backup_dates())

    def test_mail_failure_keeps_files_and_alerts(self):
        self.put()
        FakeSMTP.fail = True
        r = self.m.run("manual", "tester", alert=self.alert, sleep=lambda s: None, smtp_factory=FakeSMTP)
        self.assertFalse(r["ok"])
        self.assertEqual(self.m.status()["status"], "failed")
        self.assertTrue((self.m.root() / "PositionBook.csv").exists())          # not backed up, not deleted
        self.assertFalse((self.m.root() / "BACKUP AUTOMATION").exists())
        self.assertEqual(len(self.alerts), 1)
        self.assertFalse(self.m.is_running())                                   # no stuck 'Running'

    def test_missing_positionbook_fails_cleanly(self):
        r = self.m.run("manual", "tester", alert=self.alert, sleep=lambda s: None, smtp_factory=FakeSMTP)
        self.assertFalse(r["ok"])
        self.assertIn("PositionBook", r["error"])
        self.assertEqual(FakeSMTP.sent, [])
        self.assertEqual(len(self.alerts), 1)
        self.assertFalse(self.m.is_running())

    def test_mail_switched_off_still_backs_up(self):
        self.put()
        self.m.save_settings({"send_mail": False}, by="t")
        r = self.m.run("manual", "t", alert=self.alert, sleep=lambda s: None, smtp_factory=FakeSMTP)
        self.assertTrue(r["ok"])
        self.assertEqual(FakeSMTP.sent, [])

    def test_upload_rules(self):
        ok = self.m.accept_upload("PositionBook (2).csv", POSITION.encode(), by="t")
        self.assertEqual(ok["name"], "PositionBook.csv")
        self.assertTrue(self.m.input_status()[0]["ok"])
        for bad_name, data in (("holding.csv", POSITION.encode()), ("PositionBook.csv", b""), ("PositionBook.exe", b"x"),
                               ("PositionBook.csv", b"a,b\n1,2\n")):
            with self.assertRaises(ValueError):
                self.m.accept_upload(bad_name, data)

    def test_status_shapes_for_the_card_routes(self):
        st = self.m.status()
        for k in ("steps", "inputs", "settings", "log", "folder", "running", "status"):
            self.assertIn(k, st)
        self.assertEqual([s["key"] for s in st["steps"]], ["input", "calc", "email", "backup"])
        self.assertFalse(self.m.stop_running())
        with self.assertRaises(ValueError):
            self.m.fetch_one("mail")
        with self.assertRaises(ValueError):
            self.m._check_date("2026-11-18")

    def test_second_run_needs_password_after_done(self):
        self.put()
        self.m.run("manual", "t", alert=self.alert, sleep=lambda s: None, smtp_factory=FakeSMTP)
        r = self.m.request_run(by="t", password="", background=False)
        self.assertTrue(r.get("need_password"))


if __name__ == "__main__":
    unittest.main()
