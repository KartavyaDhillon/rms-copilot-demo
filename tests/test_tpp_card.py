"""TENTATIVE PAYIN PAYOUT card: file rules, settlement-file mapping, a full run (fake mail). Run: python -m unittest tests.test_tpp_card -v"""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TODAY = dt.date.today()
D8 = TODAY.strftime("%d%m%Y")
EXP = TODAY.strftime("%d-%b-%Y")
HEAD = "Exch\tClientID\tOpType\tInd Moneyness\tSymbol\tMToM\tDayBuyAmount\tDaySellAmount\tStrikePrice\tNetQty\tExpDate\n"
ROWS = [
    ["NFO", "A1", "XX", "", "NIFTY", "100", "0", "0", "0", "0", EXP],                    # future MTM 100
    ["NFO", "A1", "CE", "ITM", "NIFTY", "0", "50", "20", "24000", "10", EXP],            # premium 20-50 = -30, exercise |25000-24000|*10
    ["NSE", "A1", "", "", "TCS", "0", "1000", "400", "0", "0", EXP],                     # cash
    ["NFO", "MCS9", "XX", "", "NIFTY", "500", "0", "0", "0", "0", EXP],                  # custodian
]
RISK = "ACCOUNTID,CPCode\nA1,ABC\nMCS9,MASTERX\n"


def csv_bytes():
    return (HEAD + "\n".join("\t".join(r) for r in ROWS) + "\n").encode()


class FakeSMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg, to_addrs=None):
        FakeSMTP.sent.append((msg, to_addrs))

    def quit(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        r = Path(self.t.name)
        (r / "common").mkdir()
        (r / "common" / f"RISK_REPORT_{D8}.csv").write_text(RISK)
        os.environ.update(TPP_DIR=str(r / "card"), SA_COMMON_DIR=str(r / "common"), SCHED_DATA_ROOT=str(r / "sd"),
                          TPP_LOG_DIR=str(r / "logs"), SMTP_SERVER="x", SENDER_EMAIL="a@b.c", SENDER_PASSWORD="p")
        from core import tpp_card as T
        self.T = T
        T._NEED_CACHE.clear()
        FakeSMTP.sent.clear()
        self.fresh = (dt.datetime.now().timestamp() - 600) * 1000                        # saved 10 min ago

    def tearDown(self):
        for k in ("TPP_DIR", "SA_COMMON_DIR", "SCHED_DATA_ROOT", "TPP_LOG_DIR"):
            os.environ.pop(k, None)
        self.t.cleanup()


class RuleTests(Base):
    def test_one_hour_rule_and_name_date(self):
        T = self.T
        now = dt.datetime.now()
        self.assertEqual(T.freshness_problem(f"Positionbook_{D8}.csv", None, now), "")                  # today's date in the name
        self.assertIn("dated", T.freshness_problem("Positionbook_01012020.csv", self.fresh, now))       # another day: refused
        self.assertEqual(T.freshness_problem("Positionbook.csv", self.fresh, now), "")                  # no date, saved 10 min ago
        old = (now.timestamp() - 61 * 60) * 1000
        self.assertIn("older than 60", T.freshness_problem("Positionbook.csv", old, now))               # no date, 61 min ago: refused

    def test_upload_keeps_todays_name_and_refuses_wrong_files(self):
        T = self.T
        r = T.accept_upload("Positionbook.csv", csv_bytes(), modified_ms=self.fresh)
        self.assertEqual(r["name"], f"Positionbook_{D8}.csv")
        self.assertTrue(T.input_status()[0]["ok"])
        with self.assertRaises(ValueError):
            T.accept_upload("PositionBook.csv", csv_bytes(), modified_ms=(dt.datetime.now().timestamp() - 7200) * 1000)
        with self.assertRaises(ValueError):
            T.accept_upload("CNC.csv", csv_bytes(), modified_ms=self.fresh)                               # not a Positionbook name
        with self.assertRaises(ValueError):
            T.accept_upload("Positionbook.csv", b"a,b\n1,2\n", modified_ms=self.fresh)                    # columns missing
        self.assertTrue(T.input_status()[0]["ok"])                                                       # the good file was not harmed

    def test_file_put_in_the_folder_by_hand(self):
        T = self.T
        p = T.root() / "Positionbook.csv"
        p.write_bytes(csv_bytes())
        self.assertTrue(T.input_status()[0]["ok"])                                                       # no date, modified just now
        old = dt.datetime.now().timestamp() - 3 * 3600
        os.utime(p, (old, old))
        row = T.input_status()[0]
        self.assertFalse(row["ok"])
        self.assertIn("older than 60", row["why"])


class SettleTests(Base):
    def setUp(self):
        super().setUp()
        self.T.accept_upload("Positionbook.csv", csv_bytes(), modified_ms=self.fresh)

    def test_symbols_needed(self):
        need = self.T.need_symbols()
        self.assertEqual([n["symbol"] for n in need], ["NIFTY"])
        self.assertFalse(need[0]["optional"])

    def test_any_file_with_chosen_columns(self):
        T = self.T
        data = b"Index,Final Close,Other\nNifty,25000.5,1\nSENSEX,80000,2\nBAD,,3\n"
        info = T.inspect_settle_file(name="whatever.csv", data=data)
        self.assertEqual(info["columns"], ["Index", "Final Close", "Other"])
        self.assertEqual(info["guess"]["symbol_col"], "Index")
        res = T.apply_settle_file(info["token"], "Index", "Final Close")
        self.assertEqual(res["prices"], {"NIFTY": 25000.5, "SENSEX": 80000.0})
        self.assertEqual(res["matched"], ["NIFTY"])
        self.assertEqual(res["bad_rows"], 1)
        self.assertEqual(T.inspect_settle_file(token=info["token"])["guess"]["remembered"], True)        # the choice is remembered
        with self.assertRaises(ValueError):
            T.apply_settle_file(info["token"], "Index", "Index")

    def test_excel_sheet_and_header_row(self):
        import pandas as pd
        T = self.T
        p = Path(self.t.name) / "s.xlsx"
        with pd.ExcelWriter(p) as w:
            pd.DataFrame([["Prices of the day", ""], ["Name", "Rate"], ["NIFTY", 25100]]).to_excel(w, sheet_name="Sheet A", header=False, index=False)
            pd.DataFrame([["x", "y"]]).to_excel(w, sheet_name="Other", header=False, index=False)
        info = T.inspect_settle_file(name="s.xlsx", data=p.read_bytes())
        self.assertEqual(info["sheets"], ["Sheet A", "Other"])
        info = T.inspect_settle_file(token=info["token"], sheet="Sheet A", header_row=2)
        self.assertEqual(info["columns"], ["Name", "Rate"])
        res = T.apply_settle_file(info["token"], "Name", "Rate", sheet="Sheet A", header_row=2)
        self.assertEqual(res["prices"], {"NIFTY": 25100.0})

    def test_save_validates_and_is_for_today(self):
        T = self.T
        with self.assertRaises(ValueError):
            T.save_settlement({"NIFTY": "abc"})
        with self.assertRaises(ValueError):
            T.save_settlement({"NIFTY": 0})
        T.save_settlement({"nifty": "25,000.5"}, source="typed")
        self.assertEqual(T.stored_settlement()["prices"], {"NIFTY": 25000.5})


class RunTests(Base):
    def setUp(self):
        super().setUp()
        self.T.accept_upload("Positionbook.csv", csv_bytes(), modified_ms=self.fresh)
        self.T.MAIL_DEFAULTS["REPORT"] = ("to@x.com", "cc@x.com")
        os.environ["TPP_REPORT_TO"], os.environ["TPP_REPORT_CC"] = "to@x.com", "cc@x.com"

    def test_run_needs_the_prices_then_mails_and_backs_up(self):
        T = self.T
        r = T.run("manual", "t", alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP)
        self.assertFalse(r["ok"])
        self.assertIn("Settlement prices are needed", T.read_state()["error"])
        self.assertEqual(FakeSMTP.sent, [])
        T.save_settlement({"NIFTY": 25000})
        r = T.run("manual", "t", alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP)
        self.assertTrue(r["ok"], T.read_state())
        msg, rcpts = FakeSMTP.sent[0]
        self.assertEqual(msg["Subject"], f"Tentative Obligation File-{D8}")
        self.assertEqual(sorted(rcpts), ["cc@x.com", "to@x.com"])
        names = [p.get_filename() for p in msg.walk() if p.get_filename() and p.get_filename().endswith(".xlsx")]
        self.assertEqual(len(names), 2)                                                                  # normal + custodian
        bk = T.backup_root() / TODAY.strftime("%d-%m-%Y")
        got = sorted(p.name for p in bk.iterdir())
        self.assertIn(f"Positionbook_{D8}.csv", got)
        self.assertIn(f"RISK_REPORT_{D8}.csv", got)                                                      # copied
        self.assertTrue((Path(self.t.name) / "common" / f"RISK_REPORT_{D8}.csv").is_file())              # still in COMMON FILES
        self.assertEqual([p.name for p in T.root().iterdir() if p.is_file()], [])                        # the card folder is clean
        self.assertTrue(T.needs_password())                                                              # once a day
        self.assertTrue(T.request_run(background=False)["need_password"])

    def test_missing_risk_report_stops_before_anything(self):
        T = self.T
        os.remove(Path(self.t.name) / "common" / f"RISK_REPORT_{D8}.csv")
        r = T.run("manual", "t", alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP)
        self.assertFalse(r["ok"])
        self.assertIn("Risk report", T.read_state()["error"])


if __name__ == "__main__":
    unittest.main()
