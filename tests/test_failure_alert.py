"""Global failure alert (no network, no scheduler): numbers, reasons, image, dedupe, switches.
Run:  python -m unittest tests.test_failure_alert -v
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.scheduler import failure_alert as fa


class _P:
    name = "ALLOCATION REPORT"


class FailureAlert(unittest.TestCase):
    def setUp(self):
        fa._LAST.clear()
        self.tmp = Path(tempfile.mkdtemp())

    def test_numbers_are_cleaned(self):
        self.assertEqual(fa.parse_numbers("9812345678 #Ravi, +91 98123-45678;12;919812345678, 9811111111"),
                         ["+919812345678", "+919811111111"])
        self.assertEqual(fa.parse_numbers(""), [])

    def test_each_process_has_its_own_list_with_a_common_fallback(self):
        from core.scheduler import catalog
        pdef = catalog.ProcessDef(key="k", name="K", run=lambda p, c: None,
                                  params=[catalog.Param(catalog.ALERT_PARAM, "x", "text", fmt="phones", scope="process")])
        with mock.patch("core.scheduler.proc_settings.get", return_value={catalog.ALERT_PARAM: "9812345678 #A"}):
            self.assertEqual(fa.numbers_for(pdef, {"alert_numbers": "9899999999"}), (["+919812345678"], "this process"))
        with mock.patch("core.scheduler.proc_settings.get", return_value={}):
            self.assertEqual(fa.numbers_for(pdef, {"alert_numbers": "9899999999"}), (["+919899999999"], "common list"))
            self.assertEqual(fa.numbers_for(pdef, {})[0], [])

    def test_every_registered_process_gets_the_alert_setting(self):
        from core.scheduler import catalog
        d = catalog.register("tmp_alert_key", "T", lambda p, c: None)
        try:
            q = [x for x in d.params if x.name == catalog.ALERT_PARAM]
            self.assertEqual(len(q), 1)
            self.assertEqual((q[0].scope, q[0].fmt), ("process", "phones"))
        finally:
            catalog.unregister("tmp_alert_key")

    def test_reason_in_plain_words(self):
        h, d = fa.friendly_reason("RuntimeError", "No Allocation_Logs file was produced")
        self.assertIn("empty", h)
        self.assertIn("RuntimeError", d)
        self.assertIn("timeout", fa.friendly_reason("ReadTimeout", "Read timed out")[0].lower())
        self.assertIn("error", fa.friendly_reason("X", "weird")[0])

    def test_image_is_drawn_for_a_long_text(self):
        out = fa.render_image(self.tmp / "a.png", process="A VERY LONG PROCESS NAME " * 3, schedule="S", failed_at="t",
                              started="t", trigger="Scheduled", attempt_text="1", headline="h " * 80,
                              detail="d " * 400, run_id="abcdef123456")
        self.assertTrue(out.is_file() and out.stat().st_size > 5000)

    def _settings(self, **kw):
        base = {"alert_switch": "on", "alert_numbers": "9876543210", "auth_token": "tok", "alert_template": ""}
        base.update(kw)
        return mock.patch.object(fa, "_settings", return_value=base)

    def _run(self, **kw):
        sched = {"id": "s1", "name": "Nightly allocation", "process_key": "alloc"}
        with mock.patch("core.scheduler.paths.sched_root", return_value=self.tmp), \
                mock.patch("core.scheduler.proc_settings.get", return_value={}):
            return fa.notify(sched, _P(), "e" * 12, "RuntimeError", "boom", None, "SCHEDULED", 1, **kw)

    def test_switch_off_and_no_numbers_send_nothing(self):
        with self._settings(alert_switch="off"), mock.patch("core.scheduler.helpers.whatsapp_send_image") as m:
            self.assertFalse(self._run()["sent"])
            m.assert_not_called()
        with self._settings(alert_numbers=""), mock.patch("core.scheduler.helpers.whatsapp_send_image") as m:
            self.assertFalse(self._run()["sent"])
            m.assert_not_called()

    def test_sends_once_then_dedupes(self):
        with self._settings(), mock.patch("core.scheduler.helpers.whatsapp_send_image") as m:
            r1 = self._run(now=1000.0)
            r2 = self._run(now=1000.0 + 600)                    # 10 minutes later: same schedule -> quiet
            r3 = self._run(now=1000.0 + 3 * 3600)               # 3 hours later: alerts again
        self.assertTrue(r1["sent"])
        self.assertFalse(r2["sent"])
        self.assertTrue(r3["sent"])
        self.assertEqual(m.call_count, 2)
        label = m.call_args[0][2]
        self.assertTrue(label.startswith("FAILED - ALLOCATION REPORT"))

    def test_a_failing_send_never_raises(self):
        with self._settings(), mock.patch("core.scheduler.helpers.whatsapp_send_image", side_effect=RuntimeError("down")):
            r = self._run()
        self.assertFalse(r["sent"])
        self.assertEqual(r["reason"], "all sends failed")


if __name__ == "__main__":
    unittest.main()
