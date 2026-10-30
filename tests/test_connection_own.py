import os
import tempfile
import unittest
from unittest import mock

from core.scheduler import connections as C


class ProcessCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {
            "SCHED_ENV_DIR": self.tmp,
            "SCHED_SMTP_EMAIL": "kartavya@x.in", "SCHED_SMTP_PASSWORD": "kpw", "SCHED_SMTP_SERVER": "smtp.x.in", "SCHED_SMTP_PORT": "587"})
        self.env.start()
        self.addCleanup(self.env.stop)
        p = mock.patch.object(C, "_process_name", side_effect=lambda pk: pk)       # no need to load every process (and the real .env)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in list(os.environ) if k.startswith("SCHED_OVR_")])

    def sender_of(self, proc):
        tok = C.set_process(proc)
        try:
            return C.get("smtp")["email"], C.get("smtp")["password"]
        finally:
            C.reset_process(tok)

    def test_changing_the_sender_in_one_process_changes_only_that_process(self):
        C.update("smtp", {"email": "csd@x.in", "password": "cpw"}, "t", process="sumit")
        self.assertEqual(self.sender_of("sumit"), ("csd@x.in", "cpw"))
        self.assertEqual(self.sender_of("peak"), ("kartavya@x.in", "kpw"))            # others untouched
        self.assertEqual(os.environ["SCHED_SMTP_EMAIL"], "kartavya@x.in")

    def test_new_email_needs_its_own_password(self):
        with self.assertRaises(ValueError):
            C.update("smtp", {"email": "csd@x.in"}, "t", process="sumit")
        self.assertEqual(self.sender_of("sumit"), ("kartavya@x.in", "kpw"))

    def test_new_password_reaches_every_place_with_the_same_email_only(self):
        C.update("smtp", {"email": "csd@x.in", "password": "c1"}, "t", process="sumit")
        C.update("smtp", {"email": "csd@x.in", "password": "c1"}, "t", process="other_csd")
        C.update("smtp", {"email": "someone@x.in", "password": "s1"}, "t", process="third")
        got = []
        C.update("smtp", {"password": "c2"}, "t", process="sumit", linked_out=got)      # csd's password changed
        self.assertEqual(self.sender_of("sumit")[1], "c2")
        self.assertEqual(self.sender_of("other_csd")[1], "c2")                          # same e-mail -> updated
        self.assertEqual(self.sender_of("third")[1], "s1")                              # other e-mail -> untouched
        self.assertEqual(self.sender_of("peak")[1], "kpw")                              # shared kartavya -> untouched
        self.assertTrue(got)

    def test_password_change_of_the_shared_login_reaches_copies_of_the_same_login(self):
        C.update("smtp", {"password": "k1"}, "t", process="a_copy")                    # a copy that still has kartavya
        C.update("smtp", {"password": "k2"}, "t")                                       # kartavya's password changed (shared tab)
        self.assertEqual(self.sender_of("a_copy")[1], "k2")
        self.assertEqual(self.sender_of("peak")[1], "k2")

    def test_view_shows_what_the_process_uses(self):
        C.update("smtp", {"email": "csd@x.in", "password": "verylongsecret"}, "t", process="sumit")
        f = {x["name"]: x for x in {g["group"]: g for g in C.view_for("sumit")}["smtp"]["fields"]}
        self.assertEqual(f["email"]["value"], "csd@x.in")
        self.assertTrue(f["password"]["set"])
        f2 = {x["name"]: x for x in {g["group"]: g for g in C.view_for("peak")}["smtp"]["fields"]}
        self.assertEqual(f2["email"]["value"], "kartavya@x.in")


if __name__ == "__main__":
    unittest.main()
