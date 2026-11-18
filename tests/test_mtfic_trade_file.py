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


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body
        self.ok = 200 <= status < 400
        self.text = body if isinstance(body, str) else __import__("json").dumps(body)
        self.content = self.text.encode()

    def json(self):
        if isinstance(self._b, str):
            raise ValueError("not json")
        return self._b


LOGIN_OK = _Resp(200, {"success": True, "data": {"accessToken": "TOK123"}})


class FakeHttp:
    """requests stand-in: answers come from two lists (login answers, upload answers); every call is recorded."""
    def __init__(self, logins, uploads):
        self.logins, self.uploads, self.calls = list(logins), list(uploads), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        q = self.logins if url.endswith("/api/auth/login") else self.uploads
        a = q.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


class ServiceApiUpload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.f = Path(self.tmp.name) / "MTF_TRADE-17112026.txt"
        self.f.write_text("211766884,11,,,\r\n")
        self.cfg = mock.patch.object(M, "_upload_cfg", lambda: ("https://svc.example", "RMSDELH", "secret"))
        self.cfg.start()
        self.logs = mock.patch.object(M, "log", lambda *a, **k: None)
        self.logs.start()
        self.d = dt.date(2026, 11, 17)

    def tearDown(self):
        self.cfg.stop(); self.logs.stop(); self.tmp.cleanup()

    def test_logs_in_and_uploads_the_txt_as_field_file(self):
        h = FakeHttp([LOGIN_OK], [_Resp(200, {"Success": True, "Message": "File imported successfully"})])
        r = M.upload_trade_file(self.f, self.d, 6896, http=h, sleep=lambda s: None)
        self.assertTrue(r["ok"])
        self.assertEqual(r["message"], "File imported successfully")
        (u1, k1), (u2, k2) = h.calls
        self.assertEqual(u1, "https://svc.example/api/auth/login")
        self.assertEqual(k1["json"], {"userId": "RMSDELH", "password": "secret"})
        self.assertEqual(u2, "https://svc.example/api/dataservice/uploadMTFTrades")
        self.assertEqual(k2["headers"], {"Authorization": "Bearer TOK123"})
        self.assertEqual(list(k2["files"]), ["file"])
        self.assertEqual(k2["files"]["file"][0], "MTF_TRADE-17112026.txt")

    def test_permission_refused_403_is_reported_and_not_retried(self):
        h = FakeHttp([LOGIN_OK], [_Resp(403, {"message": "Access Violation: you are not authorized to access this API", "success": False})])
        r = M.upload_trade_file(self.f, self.d, 5, http=h, sleep=lambda s: None)
        self.assertFalse(r["ok"])
        self.assertEqual((r["status"], r["attempts"]), (403, 1))
        self.assertIn("Access Violation", r["error"])
        self.assertEqual(len(h.calls), 2)

    def test_success_false_with_http_200_is_a_failure(self):
        h = FakeHttp([LOGIN_OK], [_Resp(200, {"Success": False, "Message": "bad line 3"})])
        r = M.upload_trade_file(self.f, self.d, 5, http=h, sleep=lambda s: None)
        self.assertFalse(r["ok"])
        self.assertIn("bad line 3", r["message"])

    def test_network_and_5xx_errors_are_retried(self):
        h = FakeHttp([LOGIN_OK], [OSError("connection reset"), _Resp(503, "busy"), _Resp(200, {"Success": True, "Message": "ok"})])
        r = M.upload_trade_file(self.f, self.d, 5, http=h, sleep=lambda s: None)
        self.assertTrue(r["ok"])
        self.assertEqual(r["attempts"], 3)

    def test_expired_token_logs_in_again(self):
        h = FakeHttp([LOGIN_OK, _Resp(200, {"success": True, "data": {"accessToken": "NEW"}})],
                     [_Resp(401, {"message": "expired"}), _Resp(200, {"Success": True, "Message": "ok"})])
        r = M.upload_trade_file(self.f, self.d, 5, http=h, sleep=lambda s: None)
        self.assertTrue(r["ok"])
        self.assertEqual(h.calls[-1][1]["headers"], {"Authorization": "Bearer NEW"})

    def test_refused_login_is_not_retried(self):
        h = FakeHttp([_Resp(401, {"success": False, "message": "bad password"})], [])
        r = M.upload_trade_file(self.f, self.d, 5, http=h, sleep=lambda s: None)
        self.assertFalse(r["ok"])
        self.assertEqual(len(h.calls), 1)
        self.assertIn("refused the login", r["error"])

    def test_only_the_trade_txt_is_ever_uploaded(self):
        for name in ("MTF_04112026_KAMBALA.csv", "Reconciliation_MTF_ImpactCost_17112026.xlsx", "MTF_TRADE-17112026.csv"):
            p = Path(self.tmp.name) / name
            p.write_text("x")
            h = FakeHttp([], [])
            r = M.upload_trade_file(p, self.d, 5, http=h, sleep=lambda s: None)
            self.assertFalse(r["ok"])
            self.assertEqual(h.calls, [])                        # nothing was sent at all

    def test_no_rows_and_no_login_are_not_uploaded(self):
        h = FakeHttp([], [])
        r = M.upload_trade_file(self.f, self.d, 0, http=h, sleep=lambda s: None)
        self.assertTrue(r["skipped"])
        self.assertEqual(h.calls, [])
        with mock.patch.object(M, "_upload_cfg", lambda: ("https://svc.example", "", "")):
            r = M.upload_trade_file(self.f, self.d, 5, http=h, sleep=lambda s: None)
        self.assertIn("not set", r["error"])
        self.assertEqual(h.calls, [])

    def test_card_login_wins_over_the_shared_connection(self):
        self.cfg.stop()
        try:
            with mock.patch.dict(os.environ, {"MTFIC_API_USER": "OWNID", "MTFIC_API_PASS": "ownpw", "MTFIC_API_BASE": "https://x.example/"}):
                self.assertEqual(M._upload_cfg(), ("https://x.example", "OWNID", "ownpw"))
        finally:
            self.cfg.start()


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
