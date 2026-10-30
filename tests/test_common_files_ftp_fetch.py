import unittest
from unittest import mock

from core.scheduler.processes import common_files_ftp_fetch as P
from core.scheduler import helpers as H


class Ctx:
    cancelled = False
    def __init__(self): self.lines = []
    def log(self, m): self.lines.append(m)


class Retry(unittest.TestCase):
    def _call(self, results, params=None):
        seq = iter(results)
        CF = P.CF
        ctx = Ctx()
        t = {"v": 0.0}
        def now():
            t["v"] += 60.0
            return t["v"]
        fn = P._fetch
        with mock.patch.object(P.K, "require_trading_day"), mock.patch.object(CF, "configured", return_value=True), \
             mock.patch.object(CF, "is_fetching", return_value=False), \
             mock.patch.object(CF, "fetch_once", side_effect=lambda manual=False: next(seq)), \
             mock.patch.object(CF, "alert_once_if_missing", return_value=True) as al, \
             mock.patch.object(P.time, "sleep"), mock.patch.object(P.time, "time", side_effect=now):
            try:
                out = fn(params or {}, ctx)
                err = None
            except Exception as e:
                out, err = None, e
        return out, err, al, ctx

    def test_found_on_retry(self):
        miss = {"ok": False, "message": "still waiting for: HOLDING", "files": {}}
        ok = {"ok": True, "message": "ok", "files": {"HOLDING": {"status": "downloaded"}}}
        out, err, al, ctx = self._call([miss, ok])
        self.assertIsNone(err); self.assertIn("HOLDING", out); al.assert_not_called()
        self.assertTrue(any("retry 1 of 2" in l for l in ctx.lines))

    def test_gives_up_after_two_retries_no_email(self):
        miss = {"ok": False, "message": "still waiting for: HOLDING_30102026.csv, RISK_REPORT_30102026.csv",
                "files": {"HOLDING_30102026.csv": {"status": "not on ftp"}, "RISK_REPORT_30102026.csv": {"status": "not on ftp"}}}
        out, err, al, ctx = self._call([miss] * 10)
        self.assertIsInstance(err, RuntimeError)
        self.assertNotIsInstance(err, H.Skipped)
        self.assertIn("have not been uploaded to the console", str(err))
        self.assertIn("HOLDING and RISK REPORT", str(err))
        self.assertIn("checked 3 times", str(err))
        al.assert_not_called()                      # no e-mail alert any more
        self.assertEqual(sum("retry" in l for l in ctx.lines), 2)

    def test_headline_in_whatsapp_alert(self):
        from core.scheduler import failure_alert as FA
        head, _ = FA.friendly_reason("RuntimeError", "HOLDING and RISK REPORT have not been uploaded to the console - please upload them fast.")
        self.assertIn("upload them fast", head)


if __name__ == "__main__":
    unittest.main()
