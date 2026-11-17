"""MTF ORDER FILE card: calculation (replayed on the real 17-08-2026 day), mailbox pick, run, timer, stop.
Run:  python -m unittest tests.test_mtf_order_file -v"""
import csv
import datetime as dt
import os
import shutil
import tempfile
import threading
import time
import unittest
from email.message import EmailMessage
from pathlib import Path

REAL = Path(r"D:\MTF ORDER FILE\BACKUP FILES AUTOMATED\17-08-2026")
if not REAL.is_dir():
    REAL = Path("/sessions/eager-loving-shannon/mnt/MTF ORDER FILE/BACKUP FILES AUTOMATED/17-08-2026")


def rows(p):
    with open(p, newline="", encoding="utf-8-sig") as f:
        return sorted(tuple(x.strip() for x in r) for r in csv.reader(f))


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.common = self.tmp / "COMMON"
        self.common.mkdir()
        self.old = {k: os.environ.get(k) for k in ("MTF_ORDER_DIR", "SA_COMMON_DIR", "MTF_ORDER_LOG_DIR", "RMS_DATA_ROOT")}
        os.environ.update(MTF_ORDER_DIR=str(self.tmp / "card"), SA_COMMON_DIR=str(self.common), MTF_ORDER_LOG_DIR=str(self.tmp / "logs"),
                          RMS_DATA_ROOT=str(self.tmp))
        from core import mtf_order_file as m
        self.m = m
        m._ACTIVE.update(cancel=False, log_file=None)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stage_real(self, today=None):
        """Real 17-08 inputs, renamed to today's date, put where the card looks for them."""
        d = (today or dt.date.today()).strftime("%d%m%Y")
        shutil.copy(REAL / "MTFPOSITION_17082026.csv", self.common / f"MTFPOSITION_{d}.csv")
        shutil.copy(REAL / "RISK_REPORT_17082026.csv", self.common / f"RISK_REPORT_{d}.csv")
        shutil.copy(REAL / "MTF_RISK_SHORTAGE_REPORT.xlsx", self.m.shortage_path())
        return d


@unittest.skipUnless(REAL.is_dir(), "real 17-08-2026 backup not available")
class Calculation(Env):
    def test_replay_of_the_real_day_gives_the_same_files(self):
        from core import mtf_order_calc as calc
        out = self.tmp / "out"
        out.mkdir()
        shutil.copy(REAL / "MTF_RISK_SHORTAGE_REPORT.xlsx", out)
        res = calc.build_order_files(REAL / "MTFPOSITION_17082026.csv", out / "MTF_RISK_SHORTAGE_REPORT.xlsx",
                                     REAL / "RISK_REPORT_17082026.csv", out, "17082026", log=lambda *_: None)
        self.assertEqual(res["counts"]["kamb"], 287)
        self.assertEqual(res["counts"]["o319"], 0)
        self.assertIsNone(res["o319"])
        for f in sorted(REAL.glob("*.csv")):
            if f.name.startswith(("MTFPOSITION_17082026.csv", "RISK_REPORT")):
                continue
            self.assertTrue((out / f.name).is_file(), f.name)
            self.assertEqual(rows(f), rows(out / f.name), f.name)           # same rows (the hand script's row order was random)
        for z, n in (("CHND", 162), ("MUMB", 45), ("LUDH", 8), ("DELH", 72)):
            self.assertEqual(res["zones"][z]["counts"]["kamb"], n)

    def test_bad_shortage_report_is_named(self):
        from core import mtf_order_calc as calc
        import pandas as pd
        p = self.tmp / "x.xlsx"
        pd.DataFrame({"Account ID": ["A"], "Zone": ["ZODELH"]}).to_excel(p, index=False)
        self.assertIn("Net shortage", calc.shortage_problem(p))
        pd.DataFrame({"Account ID": ["A"], "Net_shortage": [-5]}).to_excel(p, index=False)
        self.assertIn("Zone", calc.shortage_problem(p))
        pd.DataFrame({"Account ID": ["A"], "Zone": ["Z"], "Net_shortage": [-5]}).to_excel(p, index=False)
        self.assertEqual(calc.shortage_problem(p), "")


def make_mail(subject, day, hh, mm, attach=True, name="MTF_RISK_SHORTAGE_REPORT.xlsx"):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "a@b.c"
    msg["Date"] = f"{day:%a, %d %b %Y} {hh:02d}:{mm:02d}:00 +0530"
    msg.set_content("hi")
    if attach:
        msg.add_attachment(b"PK-data-" + f"{hh}{mm}".encode(), maintype="application", subtype="octet-stream", filename=name)
    return msg.as_bytes()


class FakePop:
    def __init__(self, mails):
        self.mails = mails

    def top(self, i, n):
        raw = self.mails[i - 1]
        head = raw.split(b"\n\n", 1)[0] if b"\n\n" in raw else raw.split(b"\r\n\r\n", 1)[0]
        return "+OK", head.splitlines(), len(head)

    def retr(self, i):
        return "+OK", self.mails[i - 1].splitlines(), len(self.mails[i - 1])


class MailPick(Env):
    def test_start_time_from_settings(self):
        d = dt.date(2026, 11, 16)
        mails = [make_mail("MTF_RISK_SHORTAGE_REPORT", d, 8, 20), make_mail("MTF_RISK_SHORTAGE_REPORT", d, 9, 30)]
        self.assertEqual(self.m.mail_from(), dt.time(8, 0))                        # default
        self.m.save_settings({"mail_from": "9:00"})
        self.assertEqual(self.m.mail_from(), dt.time(9, 0))
        _data, when = self.m.find_mail(FakePop(mails), 2, d)
        self.assertEqual((when.hour, when.minute), (9, 30))
        self.assertIsNone(self.m.find_mail(FakePop(mails[:1]), 1, d))              # the 08:20 mail is before the start time
        self.assertEqual(self.m.status()["settings"]["mail_from"], "09:00")
        for bad in ("12:00", "13:00", "junk"):
            with self.assertRaises(ValueError):
                self.m.save_settings({"mail_from": bad})
        self.assertEqual(self.m.mail_from(), dt.time(9, 0))

    def test_window_subject_and_newest(self):
        d = dt.date(2026, 11, 16)
        y = d - dt.timedelta(days=1)
        mails = [make_mail("MTF_RISK_SHORTAGE_REPORT", y, 9, 0),                 # yesterday: never
                 make_mail("hello", d, 7, 30),
                 make_mail("MTF_RISK_SHORTAGE_REPORT", d, 7, 59),                 # before 08:00: never
                 make_mail("MTF_RISK_SHORTAGE_REPORT", d, 8, 20),
                 make_mail("Re: MTF_RISK_SHORTAGE_REPORT", d, 9, 5),              # newest in the window: wins
                 make_mail("MTF_RISK_SHORTAGE_REPORT", d, 12, 30)]                # after 12:00: never
        data, when = self.m.find_mail(FakePop(mails), len(mails), d)
        self.assertEqual((when.hour, when.minute), (9, 5))
        self.assertTrue(data.endswith(b"95"))

    def test_nothing_in_window(self):
        d = dt.date(2026, 11, 16)
        mails = [make_mail("MTF_RISK_SHORTAGE_REPORT", d, 12, 30), make_mail("MTF_RISK_SHORTAGE_REPORT", d, 9, 0, attach=False),
                 make_mail("MTF_RISK_SHORTAGE_REPORT", d, 9, 10, name="other.xlsx")]
        self.assertIsNone(self.m.find_mail(FakePop(mails), len(mails), d))


class FakeSmtp:
    sent = []

    def __init__(self, host, port, timeout=0):
        pass

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg, to_addrs=None):
        FakeSmtp.sent.append((msg["Subject"], [p.get_filename() for p in msg.walk() if p.get_filename()], msg["To"]))

    def quit(self):
        pass


@unittest.skipUnless(REAL.is_dir(), "real 17-08-2026 backup not available")
class FullRun(Env):
    def setUp(self):
        super().setUp()
        FakeSmtp.sent = []
        os.environ["MTFOF_SENDER_EMAIL"], os.environ["MTFOF_SENDER_PASSWORD"] = "t@x.com", "pw"
        self.m.STEPS = dict(self.m.STEPS, mail=lambda base, force=False: {"ok": True, "skipped": True, "msg": "test"},
                            mtfpos=lambda base: {"ok": True, "skipped": True, "msg": "test"})
        self._send = self.m.send_files
        self.m.send_files = lambda *a, **k: self._send(*a, **dict(k, smtp_factory=FakeSmtp, sleep=lambda s: None))

    def tearDown(self):
        self.m.send_files = self._send
        os.environ.pop("MTFOF_SENDER_EMAIL", None)
        os.environ.pop("MTFOF_SENDER_PASSWORD", None)
        super().tearDown()

    def test_run_makes_mails_and_backs_up(self):
        d = self.stage_real()
        r = self.m.run("manual", "test", alert=lambda h, t: {"sent": False})
        self.assertTrue(r["ok"], r)
        subjects = [s for s, _a, _t in FakeSmtp.sent]
        self.assertEqual(len([s for s in subjects if "Full File" in s]), 1)
        self.assertEqual(len([s for s in subjects if s.startswith("MTFS ") and "Order File" in s]), 4)      # one per zone
        zone = next(a for s, a, _t in FakeSmtp.sent if "LUDH" in s)
        self.assertEqual(sorted(zone), sorted([f"MTFPOSITION_{d}_SUMMARY_LUDH.csv", f"ORD_KAMB_MTFS_{d}_LUDH.csv", "MTF_RISK_SHORTAGE_REPORT_LUDH.csv"]))
        bk = self.m.backup_root() / dt.date.today().strftime("%d-%m-%Y")
        self.assertTrue((bk / f"ORD_KAMB_MTFS_{d}.csv").is_file())
        self.assertEqual([p.name for p in self.m.root().iterdir() if p.is_file()], [])          # working folder is clean
        self.assertTrue((self.common / f"MTFPOSITION_{d}.csv").is_file())                      # COMMON FILES originals stay
        st = self.m.status()
        self.assertEqual(st["status"], "done")
        self.assertTrue(st["needs_password"])
        self.assertEqual([s["status"] for s in st["steps"] if s["key"] == "backup"], ["ok"])

    def test_missing_input_fails_with_the_reason_and_alert(self):
        self.stage_real()
        for p in self.common.glob("RISK_REPORT_*"):
            p.unlink()
        self.m.STEPS["risk"] = lambda base: {"ok": False, "msg": "RISK_REPORT is not in COMMON FILES yet"}
        alerts = []
        r = self.m.run("manual", "t", alert=lambda h, t: alerts.append((h, t)) or {"sent": True})
        self.assertFalse(r["ok"])
        self.assertIn("RISK_REPORT", r["error"])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(self.m.status()["status"], "failed")
        self.assertEqual(FakeSmtp.sent, [])

    def test_mail_failure_keeps_files_and_fails(self):
        self.stage_real()

        def boom(*a, **k):
            raise RuntimeError("smtp down")
        self.m.send_files = boom
        r = self.m.run("manual", "t", alert=lambda h, t: {"sent": False})
        self.assertFalse(r["ok"])
        self.assertIn("did not go", r["error"])
        self.assertTrue(any(self.m.root().glob("ORD_KAMB_MTFS_*.csv")))                        # kept for a retry, not backed up

    def test_stop_ends_the_run_and_nothing_stays_running(self):
        self.stage_real()
        gate = threading.Event()

        def slow(base, force=False):
            while not gate.is_set() and not self.m._cancel():
                time.sleep(0.05)
            self.m._check_cancel()
            return {"ok": True, "msg": "x"}
        self.m.STEPS["mail"] = slow
        res = {}
        th = threading.Thread(target=lambda: res.update(self.m.run("manual", "t", alert=lambda h, t: {"sent": False})))
        th.start()
        time.sleep(0.4)
        self.assertTrue(self.m.status()["running"])
        self.assertTrue(self.m.stop_running("t"))
        th.join(8)
        self.assertFalse(th.is_alive())
        self.assertTrue(res.get("stopped"))
        st = self.m.status()
        self.assertEqual(st["status"], "stopped")
        self.assertFalse(st["running"])
        self.assertTrue(all(s["status"] != "running" for s in st["steps"]))

    def test_second_run_while_running_is_refused(self):
        self.stage_real()
        self.assertTrue(self.m._RUN_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(self.m.Busy):
                self.m.run("manual", "t")
        finally:
            self.m._RUN_LOCK.release()


class Timer(Env):
    def test_timer_rules(self):
        m = self.m
        calls = []
        m.STEPS = dict(m.STEPS, mail=lambda base, force=False: calls.append("mail") or {"ok": True, "msg": "ok"})
        runs = []
        m.run = lambda trigger="manual", by="", alert=None: runs.append(trigger) or {"ok": True}
        mon = dt.datetime(2026, 11, 16, 7, 0)                                  # a Monday
        m.tick(mon)
        self.assertEqual(calls, [])                                           # before 08:00
        m.tick(mon.replace(hour=8, minute=1))
        self.assertEqual(calls, ["mail"])
        m.tick(mon.replace(hour=8, minute=3))
        self.assertEqual(calls, ["mail"])                                     # the mail is done: not asked again
        m.tick(mon.replace(hour=9, minute=59))
        self.assertEqual(runs, [])                                            # before the run time
        (m.root() / m.MAIL_ATTACHMENT).write_bytes(b"x")                      # inputs are not ready -> waits, no run
        m.tick(mon.replace(hour=10, minute=0))
        self.assertEqual(runs, [])
        m.save_settings({"auto": False})
        m.tick(mon.replace(hour=10, minute=30))
        self.assertEqual(runs, [])                                            # auto off

    def test_settings_validation(self):
        with self.assertRaises(ValueError):
            self.m.save_settings({"run_at": "25:61"})
        self.assertEqual(self.m.save_settings({"run_at": "9:45"})["run_at"], "09:45")
        self.assertEqual(self.m.get_settings()["run_at"], "09:45")


if __name__ == "__main__":
    unittest.main()
