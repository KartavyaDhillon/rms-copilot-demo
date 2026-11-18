"""WhatsApp failure alerts for every card: numbers per card, dedupe, global switch, and the log watcher.
Run:  python -m unittest tests.test_card_alerts -v"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class CardAlertTests(unittest.TestCase):
    def setUp(self):
        from core import card_alerts as ca
        self.ca = ca
        self.tmp = tempfile.TemporaryDirectory()
        self._dir, self._cs = ca._dir, ca._common_settings
        ca._dir = lambda: Path(self.tmp.name)
        self.common = {"alert_switch": "on", "alert_numbers": "", "auth_token": "t"}
        ca._common_settings = lambda: self.common
        ca._LAST.clear()
        self.sent = []

    def tearDown(self):
        self.ca._dir, self.ca._common_settings = self._dir, self._cs
        self.tmp.cleanup()

    def snd(self, numbers, head, detail):
        self.sent.append((numbers, head, detail))

    def test_numbers_are_saved_per_card(self):
        r = self.ca.set_numbers("physdel", "9812345678, +919876543210")
        self.assertEqual(r["count"], 2)
        self.assertFalse(r["own"])
        self.assertEqual(self.ca.get_numbers("physdel")["raw"], "9812345678, +919876543210")
        self.assertEqual(self.ca.get_numbers("mcxcash")["count"], 0)
        with self.assertRaises(ValueError):
            self.ca.set_numbers("physdel", "not a number")
        with self.assertRaises(ValueError):
            self.ca.set_numbers("../x", "9812345678")
        self.assertEqual(self.ca.set_numbers("physdel", "")["count"], 0)       # blank = back to the common list

    def test_own_alert_cards_use_their_own_settings(self):
        self.assertTrue(self.ca.get_numbers("mtfof")["own"])
        self.assertTrue(self.ca.get_numbers("cncf11")["own"])

    def test_notify_uses_card_numbers_then_common_and_dedupes(self):
        self.common["alert_numbers"] = "9000000001"
        r = self.ca.notify_failure("mtfic", "MTF Impact Cost", "h", "d", sender=self.snd, now=1000)
        self.assertTrue(r["sent"])
        self.assertEqual(r["numbers_from"], "common list")
        self.ca.set_numbers("physdel", "9812345678")
        r = self.ca.notify_failure("physdel", "PHYSICAL DELIVERY", "h", "d", sender=self.snd, now=1000)
        self.assertEqual(r["numbers_from"], "this card")
        self.assertEqual(self.sent[-1][0], ["+919812345678"])
        r = self.ca.notify_failure("physdel", "PHYSICAL DELIVERY", "h", "d", sender=self.snd, now=1000 + 600)
        self.assertFalse(r["sent"])
        self.assertIn("30 min", r["reason"])
        r = self.ca.notify_failure("physdel", "PHYSICAL DELIVERY", "h", "d", sender=self.snd, now=1000 + 2000)
        self.assertTrue(r["sent"])

    def test_no_numbers_and_switch_off(self):
        r = self.ca.notify_failure("mtfic", "X", "h", "d", sender=self.snd)
        self.assertFalse(r["sent"])
        self.assertIn("no alert numbers", r["reason"])
        self.common.update(alert_numbers="9000000001", alert_switch="off")
        r = self.ca.notify_failure("mtfic", "X", "h", "d", sender=self.snd)
        self.assertIn("switch is off", r["reason"])
        self.assertEqual(self.sent, [])

    def test_watcher_alerts_new_failures_only(self):
        e_old = {"logdir": "physdel", "file": "physdel__a.log", "pipe": "physdel", "pipe_name": "PHYSICAL DELIVERY", "status": "failed", "why": "x", "start": "s"}
        seen = set()
        self.ca.check_once(seen, first=True, logs=[e_old])                       # boot: remembered, never alerted
        calls = []
        al = lambda *a, **k: (calls.append(a) or {"sent": True})
        new_fail = {"logdir": "mcxcash", "file": "mcxcash__b.log", "pipe": "mcxcash", "pipe_name": "MCX CASH MAINTENANCE", "status": "failed", "why": "boom", "start": "s2"}
        new_ok = {"logdir": "files70", "file": "files70__c.log", "pipe": "files70", "pipe_name": "76 FILES", "status": "ok", "why": "", "start": "s3"}
        own = {"logdir": "mtfof", "file": "mtfof__d.log", "pipe": "mtfof", "pipe_name": "MTF ORDER FILE", "status": "failed", "why": "own", "start": "s4"}
        out = self.ca.check_once(seen, logs=[e_old, new_fail, new_ok, own], alert=al)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "mcxcash")
        out2 = self.ca.check_once(seen, logs=[e_old, new_fail, new_ok, own], alert=al)   # next minute: nothing new
        self.assertEqual(len(calls), 1)
        self.assertEqual(out2, [])


if __name__ == "__main__":
    unittest.main()
