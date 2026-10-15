"""Portal Downloads: column filter, NSE 5xx retry, e-mail that never gives up.

    python -m unittest tests.test_portal_filter_retry -v
"""
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_nse_not_found import Resp
from tests.test_scheduler_processes import Base, Ctx


class Filter(Base):
    def _run(self, text, flt):
        from core.scheduler import portal_jobs as P
        d = Path(tempfile.mkdtemp())
        src, dst = d / "a.csv", d / "b.csv"
        src.write_text(text, encoding="utf-8")
        kept, total = P.filter_csv(src, dst, flt, Ctx())
        return kept, total, dst.read_text(encoding="utf-8").splitlines()

    DATA = "Symbol, Client ,Qty\nNIFTY,A1,100\nBANKNIFTY,A2,50\nRELIANCE,A1,10\n"

    def test_in_and_header(self):
        k, t, lines = self._run(self.DATA, {"match": "all", "conditions": [{"column": "symbol", "op": "in", "values": "nifty, banknifty"}]})
        self.assertEqual((k, t), (2, 3))
        self.assertEqual(lines[0], "Symbol, Client ,Qty")

    def test_all_vs_any(self):
        c = [{"column": "client", "op": "in", "values": "A1"}, {"column": "Qty", "op": "gt", "values": "60"}]
        self.assertEqual(self._run(self.DATA, {"match": "all", "conditions": c})[0], 1)
        self.assertEqual(self._run(self.DATA, {"match": "any", "conditions": c})[0], 2)

    def test_exact_match(self):
        d = "Symbol,Qty\nNIFTY,100\nnifty,100\nNIFTY50,100.0\n"
        eq = lambda op, v, col="Symbol": self._run(d, {"match": "all", "conditions": [{"column": col, "op": op, "values": v}]})
        self.assertEqual(eq("eq", "NIFTY")[2], ["Symbol,Qty", "NIFTY,100"])          # not nifty, not NIFTY50
        self.assertEqual(eq("neq", "NIFTY")[0], 2)
        self.assertEqual(eq("eq", "100", "Qty")[0], 2)                                # "100.0" is not exactly "100"
        self.assertEqual(eq("in", "100", "Qty")[0], 3)                                # "is one of" stays tolerant

    def test_zero_rows_keeps_header(self):
        k, t, lines = self._run(self.DATA, {"match": "all", "conditions": [{"column": "Symbol", "op": "in", "values": "ZZZ"}]})
        self.assertEqual((k, lines), (0, ["Symbol, Client ,Qty"]))

    def test_pipe_delimited(self):
        k, _, lines = self._run("A|B\n1|x\n2|y\n", {"match": "all", "conditions": [{"column": "B", "op": "in", "values": "y"}]})
        self.assertEqual((k, lines), (1, ["A|B", "2|y"]))

    def test_missing_column_lists_real_ones(self):
        with self.assertRaises(RuntimeError) as cm:
            self._run(self.DATA, {"match": "all", "conditions": [{"column": "Nope", "op": "in", "values": "1"}]})
        self.assertIn("Symbol", str(cm.exception))

    def test_option_defaults(self):
        from core.scheduler import portal_jobs as P
        base = {"name": "x", "segment": "FO", "folder": "Reports", "filename": "a.csv"}
        f = P.clean_job({**base, "filter": {"enabled": True, "conditions": [{"column": "A", "op": "eq", "values": "1"}]}})[1]["filter"]
        self.assertTrue(f["keep_name"])
        self.assertFalse(f["mail_note"])

    def test_validation(self):
        from core.scheduler import portal_jobs as P
        errs, job = P.clean_job({"name": "x", "segment": "FO", "folder": "Reports", "filename": "a_{YYYYMMDD}.csv.gz",
                                 "filter": {"enabled": True, "conditions": [{"column": "", "op": "in", "values": ""}]}})
        self.assertTrue(any("at least one column" in e for e in errs))
        self.assertEqual(job["filter"]["conditions"], [])


class Retries(Base):
    def test_nse_503_then_file(self):
        from core.scheduler import nse
        ok = Resp(200, "x", {"Content-Type": "application/octet-stream"}, b"DATA")
        with mock.patch.object(nse, "_send", side_effect=[Resp(503), Resp(504), ok]), \
                mock.patch.object(nse, "_token", return_value="t"), mock.patch("time.sleep"):
            p = nse.download("member", "FO", "Reports", "f.csv.gz", Path(tempfile.mkdtemp()), Ctx())
        self.assertEqual(p.read_bytes(), b"DATA")

    def test_nse_cut_short_is_retried(self):
        from core.scheduler import nse
        cut = Resp(200, "x", {"Content-Length": "10"}, b"DATA")
        ok = Resp(200, "x", {"Content-Length": "4"}, b"DATA")
        with mock.patch.object(nse, "_send", side_effect=[cut, ok]), mock.patch.object(nse, "_token", return_value="t"), \
                mock.patch("time.sleep"):
            self.assertIsNotNone(nse.download("member", "FO", "Reports", "f.csv.gz", Path(tempfile.mkdtemp()), Ctx()))

    def _mail(self, smtp_side_effect, attempts):
        from core.scheduler import helpers as H, connections
        conn = {"server": "s", "port": "587", "email": "a@b.c", "password": "p"}
        smtp = mock.MagicMock()
        smtp.return_value.__enter__.return_value.send_message.side_effect = smtp_side_effect
        with mock.patch.object(connections, "require", return_value=conn), mock.patch("smtplib.SMTP", smtp), \
                mock.patch("time.sleep"):
            H.send_email("s", "b", ["x@y.z"], [], [], Ctx(), attempts=attempts)
        return smtp.return_value.__enter__.return_value.send_message.call_count

    def test_email_infinite_until_it_works(self):
        err = smtplib.SMTPServerDisconnected("gone")
        self.assertEqual(self._mail([err] * 7 + [None], 0), 8)          # far past the old limit of 3

    def test_email_recipient_refused_not_retried(self):
        with self.assertRaises(RuntimeError):
            self._mail(smtplib.SMTPRecipientsRefused({"x@y.z": (550, b"no such user")}), 0)

    def test_email_default_still_gives_up(self):
        with self.assertRaises(RuntimeError):
            self._mail(smtplib.SMTPServerDisconnected("gone"), 3)


if __name__ == "__main__":
    unittest.main()
