"""Dashboard scheduler-slot counting (core/ops_sched.py). Throw-away sqlite, fixed clock."""
import datetime as dt, os, sqlite3, tempfile, unittest
from core.scheduler import catalog, rules, service, store
from core import ops_sched

UTC = dt.timezone.utc
IST = rules.get_tz("Asia/Kolkata")


def ist(h, mi=0):
    return dt.datetime(2026, 10, 28, h, mi, tzinfo=IST).astimezone(UTC)


class OpsSched(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        store._CONN_FACTORY = lambda: sqlite3.connect(db, timeout=15)
        store.ensure_schema()
        os.environ["SCHED_HOLIDAYS_FILE"] = os.path.join(tmp, "none.csv")
        catalog.register("t.dash", "Dash process", lambda p, c: "ok")
        self.addCleanup(catalog.unregister, "t.dash")

    def test_slots_and_statuses(self):
        now = ist(9, 0)
        s = service.create({"name": "Dash", "process_key": "t.dash", "params": {}, "tz": "Asia/Kolkata",
                            "rule": {"type": "times", "times": ["08:00", "08:30", "09:30", "10:00"]}}, "t", now)
        sched = store.get_schedule(s["id"])
        eid = store.create_execution(sched, ist(8, 0), "scheduled", "RUNNING", ist(8, 0))
        store.finish_execution(eid, "SUCCESS", ist(8, 1))
        # 08:30 has no execution and is 30 min old -> missed ; 09:30 and 10:00 are later
        r = ops_sched.today(now)
        st = [i["status"] for i in r["items"]]
        self.assertEqual(st, ["done", "missed", "upcoming", "upcoming"])
        self.assertEqual((r["done"], r["missed"], r["total"]), (1, 1, 4))

    def test_idle_run_counts_as_done(self):
        now = ist(9, 0)
        s = service.create({"name": "Dash2", "process_key": "t.dash", "params": {}, "tz": "Asia/Kolkata",
                            "rule": {"type": "times", "times": ["08:00"]}}, "t", now)
        sched = store.get_schedule(s["id"])
        store.create_execution(sched, ist(8, 0), "scheduled", "SKIPPED", ist(8, 0))
        self.assertEqual(ops_sched.today(now)["done"], 1)


if __name__ == "__main__":
    unittest.main()
