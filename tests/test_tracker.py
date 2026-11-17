"""Work Tracker backend. Run:  python -m unittest tests.test_tracker -v"""
import datetime as dt
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["USE_POSTGRES"] = "false"

from core import db as _db                      # noqa: E402
from core import tracker as tr                  # noqa: E402

BOSS = {"sub": "M001", "name": "Sandeep", "is_admin": True}
ABHI = {"sub": "M002", "name": "Abhishek", "is_admin": False}
PRIY = {"sub": "M003", "name": "Priyanshu", "is_admin": False}
USERS = [{"employee_id": u["sub"], "display_name": u["name"], "is_active": True, "is_admin": u["is_admin"]} for u in (BOSS, ABHI, PRIY)]


class Tracker(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old_db, self._old_root = _db.DB_PATH, os.environ.get("RMS_DATA_ROOT")
        _db.DB_PATH = str(self.tmp / "t.db")
        os.environ["RMS_DATA_ROOT"] = str(self.tmp)
        tr._READY["ok"] = False
        self.sent = []
        tr._SENDER["fn"] = lambda ch, to, subject, lines: self.sent.append((ch, to, subject, list(lines)))
        self.p1 = mock.patch("core.auth.list_users", return_value=USERS)
        self.p2 = mock.patch.object(tr, "files_root", lambda: self._files())
        self.p1.start(); self.p2.start()
        tr.save_person(BOSS, "M002", mobile="9876543210", email="abhi@example.com")
        tr.save_person(BOSS, "M001", email="boss@example.com")

    def _files(self):
        p = self.tmp / "files"
        p.mkdir(exist_ok=True)
        return p

    def tearDown(self):
        self.p1.stop(); self.p2.stop()
        tr._SENDER["fn"] = None
        _db.DB_PATH = self._old_db
        if self._old_root is None:
            os.environ.pop("RMS_DATA_ROOT", None)
        else:
            os.environ["RMS_DATA_ROOT"] = self._old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_assign_notifies_and_visibility(self):
        t = tr.create_task(BOSS, "Mail the exchange about the penalty", assignee="M002", due="2026-11-20", priority="high")
        self.assertEqual(t["key"], "T-1")
        self.assertEqual(t["assignee_name"], "Abhishek")
        self.assertEqual({c for c, *_ in self.sent}, {"whatsapp", "email"})          # both contacts of Abhishek
        self.assertIn("Task assigned", self.sent[0][2])
        self.assertEqual(len(tr.list_tasks(ABHI)), 1)
        self.assertEqual(len(tr.list_tasks(PRIY)), 0)                                # not his task
        with self.assertRaises(tr.TrackerError):
            tr.get_task(PRIY, t["id"])

    def test_assignee_can_move_status_but_not_reassign(self):
        t = tr.create_task(BOSS, "Send ledger confirmation", assignee="M002")
        self.sent.clear()
        with self.assertRaises(tr.TrackerError):
            tr.update_task(ABHI, t["id"], assignee="M003")
        t2 = tr.update_task(ABHI, t["id"], status="waiting", waiting_on="Ludhiana branch", follow_up_days=2)
        self.assertEqual(t2["status"], "waiting")
        self.assertEqual(t2["waiting_days"], 0)
        t3 = tr.update_task(ABHI, t["id"], status="done")
        self.assertTrue(t3["done_at"])
        self.assertTrue(any("Done" in s[2] for s in self.sent))                      # the creator is told
        acts = [a["body"] for a in t3["activity"]]
        self.assertTrue(any("Waiting on: Ludhiana branch" in a for a in acts))
        self.assertTrue(any("Status: Done" in a for a in acts))

    def test_private_note_is_only_mine(self):
        n = tr.create_task(ABHI, "Call the bank tomorrow", kind="note", due="2026-11-18")
        self.assertTrue(n["private"])
        self.assertEqual(len(tr.list_tasks(BOSS)), 0)                                # even a manager does not see it
        with self.assertRaises(tr.TrackerError):
            tr.get_task(BOSS, n["id"])
        with self.assertRaises(tr.TrackerError):
            tr.update_task(ABHI, n["id"], assignee="M003")

    def test_comments_and_files(self):
        t = tr.create_task(BOSS, "Prepare the audit file", assignee="M002")
        self.sent.clear()
        tr.add_comment(ABHI, t["id"], "Half done, sharing a screenshot")
        self.assertTrue(any(s[1] == "boss@example.com" for s in self.sent))          # comment goes to the other side
        r = tr.add_file(ABHI, t["id"], "shot 1.png", b"\x89PNG....")
        p, name, is_img = tr.file_path(BOSS, r["id"])
        self.assertTrue(p.is_file() and is_img and name == "shot 1.png")
        with self.assertRaises(tr.TrackerError):
            tr.add_file(ABHI, t["id"], "run.bat", b"x")
        with self.assertRaises(tr.TrackerError):
            tr.file_path(PRIY, r["id"])
        full = tr.get_task(BOSS, t["id"])
        self.assertEqual(len(full["file_list"]), 1)
        self.assertEqual(tr.list_tasks(BOSS)[0]["comments"], 1)
        tr.delete_task(BOSS, t["id"])
        self.assertFalse(p.exists())
        self.assertEqual(tr.list_tasks(BOSS), [])

    def test_digest_overdue_today_and_followup(self):
        a = tr.create_task(BOSS, "Overdue thing", assignee="M002", due="2026-11-10")
        tr.create_task(BOSS, "Today thing", assignee="M002", due="2026-11-17")
        w = tr.create_task(BOSS, "Ask exchange", assignee="M002", kind="followup", waiting_on="Exchange helpdesk", follow_up_days=2)
        self.assertEqual(w["status"], "waiting")
        tr._run("UPDATE tracker_tasks SET waiting_since = ? WHERE id = ?", ("2026-11-14T10:00:00", w["id"]))
        self.sent.clear()
        r = tr.digest(dt.date(2026, 11, 17))
        self.assertEqual(r["tasks"], 3)
        mine = [s for s in self.sent if s[1] == "abhi@example.com"][0]
        self.assertTrue(mine[3][0].startswith("OVERDUE"))
        self.assertTrue(any(x.startswith("FOLLOW UP") and "Exchange helpdesk" in x for x in mine[3]))
        self.assertTrue(any(s[2] == "Work Tracker summary" for s in self.sent))       # manager summary
        self.sent.clear()
        tr.digest(dt.date(2026, 11, 18))                                              # follow-up not repeated the next day
        mine = [s for s in self.sent if s[1] == "abhi@example.com"][0]
        self.assertFalse(any(x.startswith("FOLLOW UP") for x in mine[3]))
        self.assertTrue(tr.get_task(BOSS, a["id"])["overdue"])

    def test_tick_once_a_day_not_sunday(self):
        tr.create_task(BOSS, "Something due", assignee="M002", due="2026-11-16")
        self.assertFalse(tr.tick(dt.datetime(2026, 11, 15, 10, 0)))                   # Sunday
        self.assertFalse(tr.tick(dt.datetime(2026, 11, 16, 9, 0)))                    # before 09:30
        self.assertTrue(tr.tick(dt.datetime(2026, 11, 16, 9, 31)))
        self.assertFalse(tr.tick(dt.datetime(2026, 11, 16, 12, 0)))

    def test_validation_and_people_rules(self):
        with self.assertRaises(tr.TrackerError):
            tr.create_task(BOSS, "ab")
        with self.assertRaises(tr.TrackerError):
            tr.create_task(BOSS, "Good title", assignee="NOBODY")
        with self.assertRaises(tr.TrackerError):
            tr.create_task(BOSS, "Good title", due="2026-02-31")
        with self.assertRaises(tr.TrackerError):
            tr.save_person(ABHI, "M003", mobile="9999999999")                        # not a manager
        with self.assertRaises(tr.TrackerError):
            tr.save_person(BOSS, "M003", mobile="123")
        with self.assertRaises(tr.TrackerError):
            tr.save_settings(ABHI, remind_at="10:00")
        self.assertEqual(tr.save_settings(BOSS, remind_at="10:15")["remind_at"], "10:15")
        tr.save_person(BOSS, "M003", role="manager")
        self.assertTrue(tr.is_manager(PRIY))
        with self.assertRaises(tr.TrackerError):
            tr.list_tasks(None)
        s = tr.summary(ABHI)
        self.assertEqual(s["mine"], 0)


if __name__ == "__main__":
    unittest.main()
