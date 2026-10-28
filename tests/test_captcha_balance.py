"""2Captcha low-balance alert: threshold, one mail then reminders, reset after recharge, failures never raise. No network, no mail."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from core import captcha_balance as CB


class Fake:
    def __init__(self, v):
        self.v = v
    def balance(self):
        if isinstance(self.v, Exception):
            raise self.v
        return self.v
    def scrub(self, t):
        return t


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ.update({"CAPTCHA_BALANCE_STATE": os.path.join(self.tmp, "b.json"), "CAPTCHA_ALERT_TO": "a@x.in",
                           "CAPTCHA_ALERT_CC": "b@x.in", "APIKEY_2CAPTCHA": "k"})
        os.environ.pop("CAPTCHA_LOW_BALANCE", None)
        self.sent = []
        self.mail = lambda bal, to, cc: self.sent.append((bal, to, cc))
        self.t0 = datetime(2026, 10, 28, 10, 0)

    def tearDown(self):
        for k in ("CAPTCHA_BALANCE_STATE", "CAPTCHA_ALERT_TO", "CAPTCHA_ALERT_CC", "APIKEY_2CAPTCHA", "CAPTCHA_LOW_BALANCE"):
            os.environ.pop(k, None)

    def chk(self, bal, at):
        return CB.check(at, Fake(bal), self.mail, log=lambda m: None)

    def test_no_mail_above_the_limit(self):
        st = self.chk(2.5, self.t0)
        self.assertEqual(self.sent, [])
        self.assertFalse(st["low"])
        self.assertEqual(st["balance"], 2.5)

    def test_mail_at_or_below_080_to_the_saved_addresses(self):
        self.chk(0.80, self.t0)
        self.assertEqual(self.sent, [(0.80, ["a@x.in"], ["b@x.in"])])

    def test_one_mail_then_a_reminder_only_after_six_hours(self):
        self.chk(0.5, self.t0)
        self.chk(0.5, self.t0 + timedelta(hours=1))
        self.chk(0.4, self.t0 + timedelta(hours=5))
        self.assertEqual(len(self.sent), 1)
        self.chk(0.4, self.t0 + timedelta(hours=6, minutes=1))
        self.assertEqual(len(self.sent), 2)

    def test_recharge_resets_so_the_next_drop_mails_at_once(self):
        self.chk(0.5, self.t0)
        self.chk(10.0, self.t0 + timedelta(hours=1))
        self.chk(0.7, self.t0 + timedelta(hours=2))
        self.assertEqual(len(self.sent), 2)

    def test_failed_mail_is_retried_at_the_next_hourly_check(self):
        def boom(b, t, c):
            raise RuntimeError("smtp down")
        st = CB.check(self.t0, Fake(0.3), boom, log=lambda m: None)
        self.assertIn("smtp down", st["alert_error"])
        self.chk(0.3, self.t0 + timedelta(hours=1))
        self.assertEqual(len(self.sent), 1)

    def test_api_error_never_raises_and_is_shown(self):
        st = CB.check(self.t0, Fake(RuntimeError("boom")), self.mail, log=lambda m: None)
        self.assertIn("boom", st["error"])
        self.assertEqual(self.sent, [])

    def test_limit_is_editable(self):
        os.environ["CAPTCHA_LOW_BALANCE"] = "2"
        self.chk(1.5, self.t0)
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
