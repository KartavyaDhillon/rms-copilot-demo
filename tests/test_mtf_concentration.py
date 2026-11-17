"""MTF CONCENTRATION card (core/mtf_concentration.py) + MTFPOSITION fetch / Other files (core/common_ftp.py).
Offline: no network, no real mail. The end-to-end test runs the REAL calculation on a real day's backup when one exists
(D:\\CONCENTRATION\\BACKUPFILES AUTOMATED\\<day>) and is skipped otherwise.
"""
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import common_ftp as CF            # noqa: E402
from core import mtf_concentration as M      # noqa: E402

TODAY = dt.date.today()
D = TODAY.strftime("%d%m%Y")


def _csv(path, header, rows=3):
    Path(path).write_text(",".join(header) + "\n" + "\n".join(",".join("x" for _ in header) for _ in range(rows)) + "\n", encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "CONC"
        self.common = self.tmp / "COMMON"
        self.common.mkdir()
        self.env = mock.patch.dict(os.environ, {"MTF_CONC_DIR": str(self.root), "SA_COMMON_DIR": str(self.common),
                                                "MTF_CONC_LOG_DIR": str(self.tmp / "logs"), "SCHED_DATA_ROOT": str(self.tmp / "data")})
        self.env.start()
        self.root.mkdir()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class Names(Base):
    def test_backup_folder_name_style(self):
        self.assertEqual(M.dstr(dt.date(2026, 11, 16)), "16th Nov 2026")
        self.assertEqual(M.dstr(dt.date(2026, 11, 1)), "01st Nov 2026")
        self.assertEqual(M.dstr(dt.date(2026, 2, 12)), "12th Feb 2026")
        self.assertEqual(M.parse_bname("16th Nov 2026"), dt.date(2026, 11, 16))
        self.assertEqual(M.parse_bname(M.dstr(dt.date(2026, 3, 3))), dt.date(2026, 3, 3))
        self.assertIsNone(M.parse_bname("../etc"))
        with self.assertRaises(ValueError):
            M._check_date("..\\x")

    def test_outputs(self):
        o = M.outputs()
        self.assertEqual(o["FULL"], f"FULLFILE_CONCENTRATION_{D}.xlsx")
        self.assertEqual(o["ZOLUDH"], f"CONCENTRATION_ZOLUDH_{D}.xlsx")


class Inputs(Base):
    def _all(self):
        _csv(self.common / f"MTFPOSITION_{D}.csv", ["a", "b"])
        _csv(self.common / f"RISK_REPORT_{D}.csv", ["ACCOUNTID", "FIN_UPTODATE_MTF"])
        (self.root / f"{D}084000_SHORTAGE.xlsx").write_bytes(b"x")
        _csv(self.root / "ind_nifty50list.csv", ["Symbol", "ISIN Code"])
        _csv(self.root / "BSE 200index_Constituents.csv", ["Scrip Code", "ISIN No."])
        _csv(self.root / "BSE 500index_Constituents.csv", ["Scrip Code", "ISIN No."])
        (self.root / "EXCEPTION.xlsx").write_bytes(b"x")

    def test_all_present(self):
        self._all()
        rows = M.input_status()
        self.assertTrue(all(r["ok"] for r in rows), [r for r in rows if not r["ok"]])
        M.pre_run_check()

    def test_old_shortage_is_not_today(self):
        self._all()
        (self.root / f"{D}084000_SHORTAGE.xlsx").unlink()
        (self.root / "15102025084000_SHORTAGE.xlsx").write_bytes(b"x")
        self.assertIsNone(M.find_shortage())
        with self.assertRaises(M.PreRunError):
            M.pre_run_check()

    def test_index_file_wrong_columns_or_old(self):
        self._all()
        _csv(self.root / "BSE 500index_Constituents.csv", ["foo"])
        by = {r["key"]: r for r in M.input_status()}
        self.assertFalse(by["BSE500"]["ok"])
        self.assertIn("columns missing", by["BSE500"]["why"])
        old = (dt.datetime.now() - dt.timedelta(days=3)).timestamp()
        os.utime(self.root / "ind_nifty50list.csv", (old, old))
        with mock.patch.object(M.os, "name", "posix"):
            by = {r["key"]: r for r in M.input_status()}
        self.assertFalse(by["NIFTY50"]["ok"])
        self.assertEqual(by["NIFTY50"]["why"], "not saved today")

    def test_exception_from_other_files(self):
        self._all()
        (self.root / "EXCEPTION.xlsx").unlink()
        self.assertFalse(M.step_exception(self.root)["ok"])
        (Path(CF.other_dir()) / "EXCEPTION.xlsx").write_bytes(b"x")
        self.assertTrue(M.step_exception(self.root)["ok"])
        self.assertTrue({r["key"]: r for r in M.input_status()}["EXCEPTION"]["ok"])

    def test_risk_missing_message(self):
        r = M.step_risk(self.root)
        self.assertFalse(r["ok"])
        self.assertIn("COMMON FILES", r["msg"])

    def test_upload_rejects_common_files(self):
        with self.assertRaises(ValueError):
            M.accept_upload(f"RISK_REPORT_{D}.csv", b"x")
        with self.assertRaises(ValueError):
            M.accept_upload("evil.py", b"x")
        self.assertEqual(M.accept_upload("EXCEPTION.xlsx", b"abc")["size"], 3)


class Mail(Base):
    def test_default_recipients_from_original_script(self):
        to, cc = M.recipients("ZOLUDH")
        self.assertIn("kuljit.singh@mastertrust.co.in", to)
        self.assertEqual(len(cc), 4)
        self.assertEqual(M.recipients("FULL"), (["rms_del@mastertrust.co.in"], []))
        with mock.patch.dict(os.environ, {"MTFCONC_ZOCHND_TO": "a@x.com", "MTFCONC_ZOCHND_CC": ""}):
            self.assertEqual(M.recipients("ZOCHND"), (["a@x.com"], []))

    def test_apply_defaults_does_not_overwrite(self):
        with mock.patch.dict(os.environ, {"MTFCONC_FULL_TO": "mine@x.com"}, clear=False):
            os.environ.pop("MTFCONC_ZOMUMB_TO", None)
            M.apply_defaults()
            self.assertEqual(os.environ["MTFCONC_FULL_TO"], "mine@x.com")
            self.assertIn("pradip.mandavkar", os.environ["MTFCONC_ZOMUMB_TO"])

    def test_email_sends_only_files_that_exist(self):
        for k in ("FULL", "ZODELH", "ZOCHND"):
            (self.root / M.outputs()[k]).write_bytes(b"x")
        sent = []

        class Fake:
            def __init__(self, *a, **k): pass
            def starttls(self): pass
            def login(self, u, p): pass
            def send_message(self, msg, to_addrs=None): sent.append((msg["Subject"], list(to_addrs)))
            def quit(self): pass
        with mock.patch.dict(os.environ, {"SENDER_EMAIL": "a@b.com", "SENDER_PASSWORD": "pw"}):
            r = M.step_email(self.root, ["FULL", "ZODELH", "ZOCHND"], smtp_factory=Fake, sleep=lambda s: None)
        self.assertEqual(r["sent"], ["FULL", "ZODELH", "ZOCHND"])
        self.assertEqual(r["failed"], [])
        self.assertEqual(sent[0][0], f"MTF Concentration Report - FULL FILE - {D}")
        self.assertEqual(sent[2][0], f"MTF Concentration Report - ZOCHND - {D}")
        self.assertIn("rms_chd@mastertrust.co.in", sent[2][1])

    def test_email_failure_is_reported_not_raised(self):
        (self.root / M.outputs()["FULL"]).write_bytes(b"x")

        class Bad:
            def __init__(self, *a, **k): raise OSError("net down")
        with mock.patch.dict(os.environ, {"SENDER_EMAIL": "a@b.com", "SENDER_PASSWORD": "pw"}):
            r = M.step_email(self.root, ["FULL"], smtp_factory=Bad, sleep=lambda s: None)
        self.assertEqual(r["sent"], [])
        self.assertEqual(len(r["failed"]), 1)

    def test_no_sender_is_a_clear_error(self):
        (self.root / M.outputs()["FULL"]).write_bytes(b"x")
        with mock.patch.dict(os.environ, {"SENDER_EMAIL": "", "SENDER_PASSWORD": ""}):
            r = M.step_email(self.root, ["FULL"], sleep=lambda s: None)
        self.assertIn("sender login is not set", r["failed"][0])


class Backup(Base):
    def test_backup_moves_inputs_outputs_and_copies_exception(self):
        for n in (f"MTFPOSITION_{D}.csv", f"RISK_REPORT_{D}.csv", "ind_nifty50list.csv", "BSE 200index_Constituents.csv",
                  "BSE 500index_Constituents.csv", "EXCEPTION.xlsx", f"{D}084000_SHORTAGE.xlsx", M.outputs()["FULL"], "keep.txt"):
            (self.root / n).write_bytes(b"x")
        dest = M.backup_and_cleanup(self.root)
        self.assertEqual(dest.name, M.dstr(TODAY))
        self.assertTrue((self.root / "EXCEPTION.xlsx").exists())
        self.assertTrue((dest / "EXCEPTION.xlsx").exists())
        self.assertTrue((self.root / "keep.txt").exists())
        for n in (f"MTFPOSITION_{D}.csv", M.outputs()["FULL"], f"{D}084000_SHORTAGE.xlsx"):
            self.assertFalse((self.root / n).exists())
            self.assertTrue((dest / n).exists())
        self.assertEqual(M.list_backup_dates(), [M.dstr(TODAY)])
        self.assertTrue(M.file_path(M.outputs()["FULL"], M.dstr(TODAY)).is_file())


class Timer(Base):
    def _now(self, hhmm):
        h, m = hhmm.split(":")
        return dt.datetime.combine(TODAY, dt.time(int(h), int(m)))

    def setUp(self):
        super().setUp()
        self.p = mock.patch.object(M, "_trading_day", lambda d: True)
        self.p.start()
        self.calls = []
        self.sp = mock.patch.dict(M.STEPS, {
            "korp": lambda b, f=False: (self.calls.append("korp") or {"ok": True, "msg": "ok"}),
            "index": lambda b, f=False: (self.calls.append("index") or {"ok": True, "msg": "ok"}),
            "mtfpos": lambda b: (self.calls.append("mtfpos") or {"ok": True, "msg": "ok"})})
        self.sp.start()

    def tearDown(self):
        self.sp.stop()
        self.p.stop()
        super().tearDown()

    def test_nothing_before_the_time(self):
        M.tick(self._now("08:39"), alert=lambda *a: {})
        self.assertEqual(self.calls, [])

    def test_downloads_at_0840_once(self):
        M.tick(self._now("08:40"), alert=lambda *a: {})
        M.tick(self._now("08:41"), alert=lambda *a: {})
        self.assertEqual(self.calls, ["korp", "index"])

    def test_mtfposition_at_0915(self):
        M.tick(self._now("08:40"), alert=lambda *a: {})
        M.tick(self._now("09:14"), alert=lambda *a: {})
        self.assertNotIn("mtfpos", self.calls)
        M.tick(self._now("09:15"), alert=lambda *a: {})
        self.assertEqual(self.calls.count("mtfpos"), 1)
        M.tick(self._now("09:16"), alert=lambda *a: {})
        self.assertEqual(self.calls.count("mtfpos"), 1)

    def test_run_at_0930_when_inputs_ready_and_waits_when_not(self):
        runs = []
        with mock.patch.object(M, "run", lambda *a, **k: (runs.append(1) or {"ok": True})):
            M.tick(self._now("08:40"), alert=lambda *a: {})
            M.tick(self._now("09:15"), alert=lambda *a: {})
            with mock.patch.object(M, "_inputs_ok", lambda *a, **k: False):
                M.tick(self._now("09:30"), alert=lambda *a: {})
            self.assertEqual(runs, [])                        # still waiting for files
            with mock.patch.object(M, "_inputs_ok", lambda *a, **k: True):
                M.tick(self._now("09:36"), alert=lambda *a: {})
            self.assertEqual(runs, [1])
            M.tick(self._now("09:50"), alert=lambda *a: {})
            self.assertEqual(runs, [1])                       # once a day

    def test_auto_off_and_late_cutoff_and_holiday(self):
        M.save_settings({"auto": False})
        M.tick(self._now("08:45"), alert=lambda *a: {})
        self.assertEqual(self.calls, [])
        M.save_settings({"auto": True})
        M.tick(self._now("15:30"), alert=lambda *a: {})
        self.assertEqual(self.calls, [])
        with mock.patch.object(M, "_trading_day", lambda d: False):
            M.tick(self._now("08:45"), alert=lambda *a: {})
        self.assertEqual(self.calls, [])

    def test_failed_download_alerts_after_four_tries(self):
        alerts = []
        with mock.patch.dict(M.STEPS, {"korp": lambda b, f=False: {"ok": False, "msg": "login failed"}}):
            for i in range(5):
                M.tick(self._now("08:40") + dt.timedelta(minutes=11 * i), alert=lambda h, d: alerts.append(h) or {"sent": True})
        self.assertEqual(len(alerts), 1)
        self.assertIn("08:40 downloads failed", alerts[0])

    def test_settings_validation(self):
        with self.assertRaises(ValueError):
            M.save_settings({"run_at": "25:99"})
        self.assertEqual(M.save_settings({"run_at": "9:45"})["run_at"], "09:45")


class FtpPart(Base):
    def test_mtfposition_present_is_not_downloaded(self):
        _csv(self.common / f"MTFPOSITION_{D}.csv", ["a", "b"])
        with mock.patch.object(CF, "_connect", side_effect=AssertionError("must not connect")):
            r = CF.fetch_mtfposition()
        self.assertEqual(r["status"], "present")
        self.assertTrue(CF.mtfpos_status()["present"])

    def test_mtfposition_not_configured(self):
        with mock.patch.object(CF, "configured", lambda: False):
            r = CF.fetch_mtfposition()
        self.assertFalse(r["ok"])

    def test_mtfposition_not_on_ftp(self):
        class F:
            def nlst(self): return ["HOLDING_x.csv"]
            def quit(self): pass
        with mock.patch.object(CF, "configured", lambda: True), mock.patch.object(CF, "_connect", lambda: F()):
            r = CF.fetch_mtfposition()
        self.assertEqual(r["status"], "not on ftp")

    def test_mtfposition_is_not_in_the_morning_window_kinds(self):
        self.assertEqual(CF.KINDS, ("HOLDING", "RISK_REPORT"))

    def test_other_files(self):
        self.assertEqual(CF.other_save("EXCEPTION.xlsx", b"abc", by="u")["size"], 3)
        CF.other_save("EXCEPTION.xlsx", b"abcd")                       # replaced
        self.assertEqual([f["name"] for f in CF.other_list()], ["EXCEPTION.xlsx"])
        self.assertEqual(Path(CF.other_path("EXCEPTION.xlsx")).read_bytes(), b"abcd")
        for bad in ("run.bat", "x.py", "../a.txt", ""):
            if bad == "../a.txt":
                self.assertEqual(CF._other_safe(bad), "a.txt")          # only the base name is ever used
            else:
                with self.assertRaises(ValueError):
                    CF.other_save(bad, b"x")
        with self.assertRaises(ValueError):
            CF.other_save("e.txt", b"")
        CF.other_delete("EXCEPTION.xlsx")
        self.assertEqual(CF.other_list(), [])
        with self.assertRaises(FileNotFoundError):
            CF.other_path("EXCEPTION.xlsx")
        self.assertTrue(os.path.isdir(os.path.join(str(self.common), "OTHER FILES")))


class IndexParsing(Base):
    def test_valid_csv_rules(self):
        good = b"Scrip Code,ISIN No.\n" + b"\n".join(b"%d,INE%d" % (i, i) for i in range(150))
        self.assertEqual(M._valid_csv(good, M.BSE_NEED, 100), "")
        self.assertIn("HTML", M._valid_csv(b"<html>x</html>", M.BSE_NEED, 1))
        self.assertIn("columns missing", M._valid_csv(b"a,b\n1,2\n", M.BSE_NEED, 0))
        self.assertIn("only", M._valid_csv(b"Scrip Code,ISIN No.\n1,2\n", M.BSE_NEED, 100))

    def test_fresh_index_files_are_skipped(self):
        for fn, cols in (("ind_nifty50list.csv", ["Symbol", "ISIN Code"]), ("BSE 200index_Constituents.csv", ["Scrip Code", "ISIN No."]),
                         ("BSE 500index_Constituents.csv", ["Scrip Code", "ISIN No."])):
            _csv(self.root / fn, cols)
        r = M.step_index(self.root)
        self.assertTrue(r["ok"] and r["skipped"])


@unittest.skipUnless(Path(r"D:\CONCENTRATION\BACKUPFILES AUTOMATED").is_dir() or Path("/sessions/eager-loving-shannon/mnt/CONCENTRATION/BACKUPFILES AUTOMATED").is_dir(),
                     "no real backup day to replay")
class EndToEnd(Base):
    """Replays a real backed-up day through the REAL calculation script + the card's run(): the e-mails go to a fake SMTP."""

    def test_replay_real_day(self):
        import polars as pl
        bk = next(p for p in (Path(r"D:\CONCENTRATION\BACKUPFILES AUTOMATED"), Path("/sessions/eager-loving-shannon/mnt/CONCENTRATION/BACKUPFILES AUTOMATED")) if p.is_dir())
        src = bk / "16th Nov 2026"
        if not (src / "FULLFILE_CONCENTRATION_16112026.xlsx").is_file():
            self.skipTest("16th Nov 2026 backup not there")
        # inputs: RISK/MTFPOSITION go to COMMON FILES under TODAY's date, the rest into the card folder
        shutil.copy2(src / "MTFPOSITION_16112026.csv", self.common / f"MTFPOSITION_{D}.csv")
        shutil.copy2(src / "RISK_REPORT_16112026.csv", self.common / f"RISK_REPORT_{D}.csv")
        for n in ("ind_nifty50list.csv", "BSE 200index_Constituents.csv", "BSE 500index_Constituents.csv"):
            shutil.copy2(src / n, self.root / n)
        shutil.copy2(src / "16112026080344_SHORTAGE.xlsx", self.root / f"{D}080344_SHORTAGE.xlsx")
        ex = bk.parent / "EXCEPTION.xlsx"
        shutil.copy2(ex if ex.is_file() else src / "EXCEPTION.xlsx", self.root / "EXCEPTION.xlsx")
        for p in list(self.root.iterdir()) + list(self.common.iterdir()):      # copies keep their old mtime: stamp them "saved today"
            if p.is_file():
                os.utime(p, None)
        copy_dir = self.tmp / "ENT"
        M.save_settings({"copy_dir": str(copy_dir)})
        sent = []

        class Fake:
            def __init__(self, *a, **k): pass
            def starttls(self): pass
            def login(self, u, p): pass
            def send_message(self, msg, to_addrs=None): sent.append(msg["Subject"])
            def quit(self): pass
        orig = M.send_one
        with mock.patch.dict(os.environ, {"SENDER_EMAIL": "a@b.com", "SENDER_PASSWORD": "pw"}), \
                mock.patch.object(M, "send_one", lambda *a, **k: orig(*a, **dict(k, smtp_factory=Fake, sleep=lambda s: None))):
            res = M.run("test", "unit test", alert=lambda *a, **k: {})
        self.assertTrue(res["ok"], M.read_state().get("error"))
        dest = M.backup_root() / M.dstr(TODAY)
        # same number of zone files as the real day; the full file has the same rows and violation counts
        real = sorted(p.name.replace("16112026", D) for p in src.glob("*CONCENTRATION*.xlsx"))
        mine = sorted(p.name for p in dest.glob("*CONCENTRATION*.xlsx"))
        self.assertEqual(mine, real)
        a = pl.read_excel(src / "FULLFILE_CONCENTRATION_16112026.xlsx")
        b = pl.read_excel(dest / f"FULLFILE_CONCENTRATION_{D}.xlsx")
        self.assertEqual(a.shape, b.shape)
        self.assertEqual(a.columns, b.columns)
        self.assertEqual(a["VIOLATION BASED ON SCRIP LEVEL"].to_list(), b["VIOLATION BASED ON SCRIP LEVEL"].to_list())
        self.assertEqual(len(sent), len(real))
        self.assertTrue((copy_dir / f"FULLFILE_CONCENTRATION_{D}.xlsx").is_file())
        # COMMON FILES originals are untouched; the card folder is clean except EXCEPTION.xlsx
        self.assertTrue((self.common / f"RISK_REPORT_{D}.csv").is_file())
        self.assertEqual(sorted(p.name for p in self.root.iterdir() if p.is_file()), ["EXCEPTION.xlsx"])
        self.assertEqual(M.status()["status"], "done")


if __name__ == "__main__":
    unittest.main()
