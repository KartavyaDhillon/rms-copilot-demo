"""Per-process sender e-mail; the password follows the same address everywhere.   python -m unittest tests.test_sender_override"""
import os
import unittest
from unittest import mock

from core import sender_override as S

BASE = {"SENDER_EMAIL": "shared@x.in", "SENDER_PASSWORD": "sharedpw"}


class Sender(unittest.TestCase):
    def _env(self, **kw):
        d = {k: "" for pair in S.PIPE_KEYS.values() for k in pair}
        d.update(BASE)
        d.update(kw)
        return mock.patch.dict(os.environ, d)

    def test_nothing_saved_means_the_shared_sender_as_before(self):
        with self._env(RMS_PIPE="ctcl"):
            self.assertEqual(S.sender(), ("shared@x.in", "sharedpw"))

    def test_own_sender_applies_to_that_process_only(self):
        with self._env(CTCL_SENDER_EMAIL="xyz@x.in", CTCL_SENDER_PASSWORD="xyzpw"):
            with mock.patch.dict(os.environ, {"RMS_PIPE": "ctcl"}):
                self.assertEqual(S.sender(), ("xyz@x.in", "xyzpw"))
            with mock.patch.dict(os.environ, {"RMS_PIPE": "ms15"}):
                self.assertEqual(S.sender(), ("shared@x.in", "sharedpw"))        # others keep sending as before
            with mock.patch.dict(os.environ, {"RMS_PIPE": ""}):
                self.assertEqual(S.sender(), ("shared@x.in", "sharedpw"))

    def test_same_address_with_blank_password_uses_the_shared_password(self):
        with self._env(MS15_SENDER_EMAIL="SHARED@x.in"):
            self.assertEqual(S.sender("ms15"), ("SHARED@x.in", "sharedpw"))

    def test_credentials_window_shows_the_effective_sender(self):
        with self._env():
            self.assertEqual(S.display_values("CTCL_SENDER_EMAIL", "CTCL_SENDER_PASSWORD"), ("shared@x.in", "sharedpw"))
            self.assertIsNone(S.display_values("OTHER_USER", "OTHER_PASS"))
        with self._env(CTCL_SENDER_EMAIL="xyz@x.in", CTCL_SENDER_PASSWORD="xyzpw"):
            self.assertEqual(S.display_values("CTCL_SENDER_EMAIL", "CTCL_SENDER_PASSWORD"), ("xyz@x.in", "xyzpw"))


if __name__ == "__main__":
    unittest.main()
