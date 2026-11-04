"""MTF trade file: layout, product filter (MTF + MTFS) and the e-mail retry rules.   python -m unittest tests.test_mtfic_trade_file"""
import datetime as dt
import os
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from core.pipelines import mtf_impact_cost as M


class TradeFile(unittest.TestCase):
    def test_layout_matches_the_sample(self):
        self.assertEqual(M.trade_line("9292566", "MT110213", "1000000088131305"),
                         "9292566,11,,,,,,,,,,,,,MT110213,,N,,,,,1000000088131305,,,,1,")
        self.assertEqual(M.trade_line("410235856", "6DJN007", "1200000109420884"),
                         "410235856,11,,,,,,,,,,,,,6DJN007,,N,,,,,1200000109420884,,,,1,")

    def test_only_mtf_and_mtfs_rows(self):
        tb = pd.DataFrame({"Product": ["MTF", "mtfs ", "CNC", "NRML", "MIS"], "TradeID": ["1", "2", "3", "4", "5"],
                           "ClientID": ["A1", "B2", "C3", "D4", "E5"], "ExchOrderID": ["11", "22", "33", "44", "55"]})
        with tempfile.TemporaryDirectory() as d:
            p, n, skipped = M.build_trade_file(tb, dt.date(2026, 11, 4), Path(d))
            self.assertEqual((p.name, n, skipped), ("MTF_TRADE-04112026.txt", 2, 0))
            lines = p.read_bytes().decode().split("\r\n")
            self.assertEqual(lines[0], M.trade_line("1", "A1", "11"))
            self.assertEqual(lines[1], M.trade_line("2", "B2", "22"))

    def test_excel_style_numbers_become_plain_digits(self):
        self.assertEqual(M._clean_id("123.0"), "123")
        self.assertEqual(M._clean_id(" 412776889 "), "412776889")
        self.assertEqual(M._clean_id("nan"), "")


class MailRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.f = Path(self.tmp.name) / "MTF_TRADE-04112026.txt"
        self.f.write_text("x")
        self.env = mock.patch.dict(os.environ, {"SENDER_EMAIL": "me@x.in", "SENDER_PASSWORD": "pw", "MTFIC_TRADE_TO": "a@x.in",
                                                "MTFIC_TRADE_CC": "b@x.in"})
        self.env.start()
        self.logs = mock.patch.object(M, "log", lambda *a, **k: None)
        self.logs.start()

    def tearDown(self):
        self.env.stop(); self.logs.stop(); self.tmp.cleanup()

    def test_server_errors_are_retried_until_it_works(self):
        calls = {"n": 0}

        class FakeSMTP:
            def __init__(self, *a, **k):
                calls["n"] += 1
                if calls["n"] < 4:
                    raise OSError("connection reset")
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): pass
            def login(self, *a): pass
            def send_message(self, *a, **k): pass

        with mock.patch("smtplib.SMTP", FakeSMTP), mock.patch("time.sleep", lambda s: None):
            r = M.send_trade_file_email(self.f, dt.date(2026, 11, 4), 5)
        self.assertTrue(r["sent"])
        self.assertEqual((r["attempts"], r["to"], r["cc"]), (4, ["a@x.in"], ["b@x.in"]))

    def test_refused_password_is_not_retried(self):
        class BadAuth:
            n = 0
            def __init__(self, *a, **k): BadAuth.n += 1
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): pass
            def login(self, *a): raise smtplib.SMTPAuthenticationError(535, b"bad")

        with mock.patch("smtplib.SMTP", BadAuth), mock.patch("time.sleep", lambda s: None):
            r = M.send_trade_file_email(self.f, dt.date(2026, 11, 4), 5)
        self.assertFalse(r["sent"])
        self.assertEqual(BadAuth.n, 1)

    def test_blank_to_goes_to_the_sender_only(self):
        with mock.patch.dict(os.environ, {"MTFIC_TRADE_TO": "", "MTFIC_TRADE_CC": ""}):
            class OK:
                def __init__(self, *a, **k): pass
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def starttls(self): pass
                def login(self, *a): pass
                def send_message(self, *a, **k): pass
            with mock.patch("smtplib.SMTP", OK):
                r = M.send_trade_file_email(self.f, dt.date(2026, 11, 4), 1)
        self.assertEqual(r["to"], ["me@x.in"])

    def test_two_attachments_and_fixed_subject(self):
        c = Path(self.tmp.name) / "MTF_04112026_KAMBALA.csv"; c.write_text("a,b\n1,2\n")
        sent = {}

        class OK:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): pass
            def login(self, *a): pass
            def send_message(self, msg, **k): sent["m"] = msg
        with mock.patch("smtplib.SMTP", OK):
            r = M.send_trade_file_email([self.f, c], dt.date(2026, 11, 4), 5)
        self.assertTrue(r["sent"])
        self.assertEqual(sent["m"]["Subject"], "MTF_MTFS_TRADE_FILE")
        names = [p.get_filename() for p in sent["m"].walk() if p.get_filename()]
        self.assertEqual(names, [self.f.name, "MTF_04112026_KAMBALA.csv"])


class KambalaCsv(unittest.TestCase):
    def test_only_mtf_mtfs_rows_all_columns(self):
        import pandas as pd
        tb = pd.DataFrame({"Product": ["MTF", "CNC", " mtfs ", "MIS"], "TradeID": [1, 2, 3, 4], "X": list("abcd")})
        with tempfile.TemporaryDirectory() as t:
            path, n = M.build_tradebook_csv(tb, dt.date(2026, 11, 4), Path(t))
            self.assertEqual(path.name, "MTF_04112026_KAMBALA.csv")
            out = pd.read_csv(path)
        self.assertEqual(n, 2)
        self.assertEqual(list(out.columns), ["Product", "TradeID", "X"])
        self.assertEqual(list(out["TradeID"]), [1, 3])


if __name__ == "__main__":
    unittest.main()
