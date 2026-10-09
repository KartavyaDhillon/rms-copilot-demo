"""Linked credentials + network restart helper.  Run from the repo root:  python -m unittest tests.test_cred_links -v"""
import os
import tempfile
import unittest
from pathlib import Path

from core import cred_links, net_retry

KEYS = ["CTCL_NCDEX_USER", "CTCL_NCDEX_PASSWORD", "SA_NCDEX_USER", "SA_NCDEX_PASSWORD",
        "NCDEX_USERNAME", "NCDEX_PASSWORD", "SA_MCX_USER", "SA_MCX_PASSWORDS", "MCX_USERNAME", "MCX_PASSWORDS",
        "SCHED_MCX_SFTP_USER", "SCHED_MCX_SFTP_PASSWORD", "SCHED_MCX_SFTP_PASSWORD2"]


class Linked(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = {k: os.environ.get(k) for k in KEYS + ["SCHED_ENV_DIR"]}
        os.environ["SCHED_ENV_DIR"] = str(self.tmp)
        (self.tmp / ".env").write_text("CTCL_NCDEX_USER=u1\nCTCL_NCDEX_PASSWORD=old\nSA_NCDEX_USER=u1\n"
                                       "SA_NCDEX_PASSWORD=old\nNCDEX_USERNAME=other\nNCDEX_PASSWORD=keep\n", encoding="utf-8")
        (self.tmp / ".env.local").write_text("", encoding="utf-8")
        for k, v in {"CTCL_NCDEX_USER": "u1", "CTCL_NCDEX_PASSWORD": "old", "SA_NCDEX_USER": "u1",
                     "SA_NCDEX_PASSWORD": "old", "NCDEX_USERNAME": "other", "NCDEX_PASSWORD": "keep"}.items():
            os.environ[k] = v

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_same_account_follows_other_account_untouched(self):
        changed = cred_links.on_saved({"CTCL_NCDEX_PASSWORD": "new"})
        self.assertIn("SA_NCDEX_PASSWORD", changed)
        self.assertEqual(os.environ["SA_NCDEX_PASSWORD"], "new")
        self.assertEqual(os.environ["NCDEX_PASSWORD"], "keep")                # different user name → not touched
        self.assertIn("new", (self.tmp / ".env").read_text(encoding="utf-8"))

    def test_nothing_changes_when_unchanged(self):
        os.environ["SA_NCDEX_PASSWORD"] = os.environ["CTCL_NCDEX_PASSWORD"] = "new"
        self.assertEqual(cred_links.on_saved({"CTCL_NCDEX_PASSWORD": "new"}), [])

    def test_blank_password_is_ignored(self):
        self.assertEqual(cred_links.on_saved({"CTCL_NCDEX_PASSWORD": ""}), [])
        self.assertEqual(os.environ["SA_NCDEX_PASSWORD"], "old")

    def test_stale_copy_in_env_local_is_replaced(self):
        (self.tmp / ".env.local").write_text("CTCL_NCDEX_PASSWORD='stale'\n", encoding="utf-8")
        cred_links.on_saved({"CTCL_NCDEX_PASSWORD": "new"})
        self.assertNotIn("stale", (self.tmp / ".env.local").read_text(encoding="utf-8"))

    def test_mcx_sftp_list_and_single(self):
        for k, v in {"SA_MCX_USER": "56720", "SA_MCX_PASSWORDS": "a1,a0", "MCX_USERNAME": "56720",
                     "MCX_PASSWORDS": "a1,a0", "SCHED_MCX_SFTP_USER": "56720", "SCHED_MCX_SFTP_PASSWORD": "a1"}.items():
            os.environ[k] = v
        cred_links.on_saved({"SA_MCX_PASSWORDS": "b1,a1"})
        self.assertEqual(os.environ["MCX_PASSWORDS"], "b1,a1")
        self.assertEqual(os.environ["SCHED_MCX_SFTP_PASSWORD"], "b1")
        self.assertEqual(os.environ["SCHED_MCX_SFTP_PASSWORD2"], "a1")


class Restart(unittest.TestCase):
    def test_network_error_restarts_then_succeeds(self):
        calls, logs = [], []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
            return "ok"
        self.assertEqual(net_retry.run(fn, "t", logs.append, tries=5, wait=0), "ok")
        self.assertEqual(len(calls), 3)

    def test_refused_password_is_not_retried(self):
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("MCX eclear refused the password")
        with self.assertRaises(RuntimeError):
            net_retry.run(fn, "t", lambda m: None, tries=5, wait=0)
        self.assertEqual(len(calls), 1)

    def test_gives_up_after_the_last_try(self):
        calls = []

        def fn():
            calls.append(1)
            raise ConnectionResetError("forcibly closed")
        with self.assertRaises(ConnectionResetError):
            net_retry.run(fn, "t", lambda m: None, tries=4, wait=0)
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
