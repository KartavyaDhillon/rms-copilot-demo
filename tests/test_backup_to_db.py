"""Every process backup is copied into PostgreSQL. Run: python -m unittest tests.test_backup_to_db -v"""
import datetime as dt, os, sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import backup_db as B, backup_to_db as T


class FakeDB:
    def __init__(self):
        self.rows = []
    def list_run_files(self, pipeline=None, run_date=None, days=30, **k):
        return [dict(r) for r in self.rows if r["pipeline"] == pipeline and r["run_date"] == run_date]
    def save_run_file(self, pipeline, kind, filename, content, tag=None, run_date=None):
        self.rows.append({"id": len(self.rows) + 1, "pipeline": pipeline, "kind": kind, "filename": filename, "size_bytes": len(content),
                          "tag": tag, "run_date": run_date, "content": content})
    def get_run_file(self, i):
        r = self.rows[i - 1]; return r["filename"], r["content"]


class Tests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        os.environ.update(SCHED_DATA_ROOT=self.t.name, USE_POSTGRES="true", BACKUP_ARCHIVE_EXTRA="")
        self.db = FakeDB(); self._o = B._db; B._db = lambda: self.db
        B._EMPTY.clear()
        self.old = time.time() - 3600

    def tearDown(self):
        B._db = self._o
        for k in ("SCHED_DATA_ROOT", "USE_POSTGRES"):
            os.environ.pop(k, None)
        self.t.cleanup()

    def mk(self, rel, data=b"abc"):
        p = Path(self.t.name) / rel
        p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
        os.utime(p, (self.old, self.old))

    def test_scheduler_and_backup_folders_are_stored_once_and_restorable(self):
        d = dt.date.today() - dt.timedelta(days=2); n = f"{d:%d-%m-%Y}"
        self.mk(f"scheduler/My Proc/backup/{n}/out.csv", b"12345")
        self.mk(f"X Card/BACKUP FILES AUTOMATED/{n}/10.00/a.xlsx", b"zz")
        self.mk(f"X Card/BACKUP FILES AUTOMATED/NOV 2020/01-11-2020/old.txt", b"o")
        r = T.scan()
        self.assertEqual(r["stored"], 3, r)
        pipes = {x["pipeline"] for x in self.db.rows}
        self.assertEqual(pipes, {"sched_my_proc", "x_card"})
        self.assertEqual(T.scan()["stored"], 0)                                   # nothing twice
        dest = Path(self.t.name) / "restore"
        self.assertEqual(B.restore_day("x_card", dest, n), 1)
        self.assertTrue((dest / n / "10.00" / "a.xlsx").is_file())

    def test_off_and_recent_window(self):
        os.environ["USE_POSTGRES"] = "false"
        self.assertFalse(T.scan()["ok"])
        os.environ["USE_POSTGRES"] = "true"
        d = dt.date.today() - dt.timedelta(days=20); n = f"{d:%d-%m-%Y}"
        self.mk(f"scheduler/P/backup/{n}/o.csv")
        self.assertEqual(T.scan(recent_days=3)["stored"], 0)
        self.assertEqual(T.scan()["stored"], 1)


if __name__ == "__main__":
    unittest.main()
