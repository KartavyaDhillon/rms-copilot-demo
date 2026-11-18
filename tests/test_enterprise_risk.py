"""ENTERPRISE RISK REPORT card: calculation (replayed on the real 16-11-2026 day), mailbox pick, screener, run, switch, timer, stop.
Run:  python -m unittest tests.test_enterprise_risk -v"""
import datetime as dt
import os
import shutil
import tempfile
import threading
import time
import unittest
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

REAL = Path(r"D:\ENTERPRISE RISK\BACKUPFILES AUTOMATED\16112026")
if not REAL.is_dir():
    REAL = Path("/sessions/eager-loving-shannon/mnt/ENTERPRISE RISK/BACKUPFILES AUTOMATED/16112026")
HAVE_REAL = REAL.is_dir()
TEST_FILE_PW, TEST_ABV_PW = "file-pw-for-tests", "abv-pw-for-tests"          # made up here - never the real passwords


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.keys = ("ENTRISK_DIR", "ENTRISK_LOG_DIR", "RMS_DATA_ROOT", "ENTRISK_FILE_PASSWORD", "ENTRISK_ABV20_PASSWORD",
                     "ENTRISK_SENDER_EMAIL", "ENTRISK_SENDER_PASSWORD", "ENTRISK_SCREENER_USER", "ENTRISK_SCREENER_PASS")
        self.old = {k: os.environ.get(k) for k in self.keys}
        os.environ.update(ENTRISK_DIR=str(self.tmp / "card"), ENTRISK_LOG_DIR=str(self.tmp / "logs"), RMS_DATA_ROOT=str(self.tmp),
                          ENTRISK_FILE_PASSWORD=TEST_FILE_PW, ENTRISK_ABV20_PASSWORD=TEST_ABV_PW,
                          ENTRISK_SENDER_EMAIL="t@x.com", ENTRISK_SENDER_PASSWORD="pw", ENTRISK_SCREENER_USER="u", ENTRISK_SCREENER_PASS="p")
        from core import enterprise_risk as m
        self.m = m
        m._ACTIVE.update(cancel=False, log_file=None)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stage_real(self, var="redflag", conc=True, day=None):
        """Real 16-11 risk files + FULLFILE renamed to today's date; the abv20 csv is a made-up encrypted workbook."""
        from core import enterprise_risk_calc as calc
        day = day or dt.date.today()
        base = self.m.root()
        for kind, nm in (("without", "Without"), ("with", "With")):
            shutil.copy(REAL / f"EnterpriseRiskReport_{nm}_Margin_REDFLAG_2026-11-16.xlsx", base / (calc.input_name(var, kind, self.m.d2(day)) + ".xlsx"))
        if conc:
            shutil.copy(REAL / "FULLFILE_CONCENTRATION_16112026.xlsx", base / f"FULLFILE_CONCENTRATION_{self.m.d1(day)}.xlsx")
        names = pd.read_excel(REAL / "FULLFILE_CONCENTRATION_16112026.xlsx", usecols=["ScripName"]).dropna().ScripName.unique()[:300]
        tmp = base / "abv.xlsx"
        pd.DataFrame({"symbol": names, "net ledger": [600000 + i * 1000 for i in range(len(names))]}).to_excel(tmp, index=False)
        tmp.rename(base / "Concentration_abv20percentage.csv")
        calc.protect_xlsx(base / "Concentration_abv20percentage.csv", TEST_ABV_PW)


class FakeResp:
    def __init__(self, text="", status=200, url=""):
        self.text, self.status_code, self.url = text, status, url


class FakeSession:
    """screener.in stand-in: one company page with top ratios, quick ratios and a profit table."""
    def __init__(self, fail=()):
        self.cookies = {"csrftoken": "x"}
        self.calls, self.fail = [], set(fail)

    def get(self, url, headers=None, timeout=0):
        self.calls.append(url)
        if "/dash/" in url:
            return FakeResp("ok", url=url)
        for f in self.fail:
            if f in url:
                raise RuntimeError("boom")
        if "/consolidated/" in url:
            return FakeResp("<html></html>")                                  # no company-info -> standalone page
        if "quick_ratios" in url:
            return FakeResp('<ul><li><span class="name">PEG Ratio</span><span class="value">1.5</span></li>'
                            '<li><span class="name">Industry PE</span><span class="value">22</span></li></ul>')
        if "/company/" in url:
            return FakeResp('<html><head><meta name="description" content="x Profit: 1,234 Cr. Promoter Holding: 55.5%"></head><body>'
                            '<div id="company-info" data-warehouse-id="77"></div><ul id="top-ratios">'
                            '<li><span class="name">Market Cap</span><span class="value">₹ 100 Cr.</span></li>'
                            '<li><span class="name">Current Price</span><span class="value">₹ 200</span></li>'
                            '<li><span class="name">Book Value</span><span class="value">₹ 50</span></li></ul>'
                            '<section id="profit-loss"><table><tr><td>OPM %</td><td>12%</td></tr><tr><td>EPS in Rs</td><td>9.5</td></tr></table></section>'
                            '</body></html>')
        return FakeResp("")

    def post(self, url, data=None, headers=None, allow_redirects=True, timeout=0):
        return FakeResp("")


class FakeSmtp:
    sent = []

    def __init__(self, host, port, timeout=0):
        pass

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg, to_addrs=None):
        FakeSmtp.sent.append((msg["Subject"], [p.get_filename() for p in msg.walk() if p.get_filename()], msg["To"], msg["Cc"]))

    def quit(self):
        pass


# ───────────────────────────── calculation ─────────────────────────────
@unittest.skipUnless(HAVE_REAL, "real 16-11-2026 backup not available")
class Calculation(Env):
    def test_symbols_and_sheets_match_the_real_day(self):
        from core import enterprise_risk_calc as calc
        self.stage_real()
        base = self.m.root()
        res = calc.build_reports(base, "redflag", self.m.d1(), self.m.d2(), [], [TEST_ABV_PW], 10, log=lambda *_: None)
        union = []
        for kind in ("without", "with"):
            union += [s for s in res["symbols"][kind] if s not in union]
        real = list(pd.read_excel(REAL / "screener_output_REDFLAG.xlsx").SYMBOL)
        self.assertEqual(union, real)                                           # same 24 symbols in the same order as the hand run
        x = pd.ExcelFile(res["files"]["without"])
        self.assertEqual(x.sheet_names, ["REDFLAG_WITHOUT_MARGIN", "MTF CONCENTRATION", "CONCENTRATION_ABV20"])
        self.assertEqual(len(x.parse("REDFLAG_WITHOUT_MARGIN")), res["counts"]["without"]["symbols"])
        mtf = x.parse("MTF CONCENTRATION")
        self.assertTrue((mtf["FIN_UPTODATE_MTF"].abs() > 500000).all())
        self.assertTrue((mtf["Scrip_Concentration[(T/U)*100]"] > 10).all())
        self.assertTrue(set(mtf["ScripName"]) <= set(res["symbols"]["without"]))
        self.assertTrue((x.parse("CONCENTRATION_ABV20")["net ledger"] > 500000).all())

    def test_top_n_and_missing_files(self):
        from core import enterprise_risk_calc as calc
        self.stage_real()
        res = calc.build_reports(self.m.root(), "redflag", self.m.d1(), self.m.d2(), [], [TEST_ABV_PW], 3, log=lambda *_: None)
        self.assertLessEqual(res["counts"]["without"]["symbols"], 6)
        with self.assertRaises(calc.CalcError) as cm:
            calc.build_reports(self.m.root(), "simple", self.m.d1(), self.m.d2(), [], [TEST_ABV_PW], 10, log=lambda *_: None)
        self.assertIn("EnterpriseRiskReport_Without_Margin_", str(cm.exception))   # the simple names are not there

    def test_wrong_password_is_named(self):
        from core import enterprise_risk_calc as calc
        self.stage_real()
        with self.assertRaises(calc.CalcError) as cm:
            calc.build_reports(self.m.root(), "redflag", self.m.d1(), self.m.d2(), [], ["wrong"], 10, log=lambda *_: None)
        self.assertIn("password", str(cm.exception).lower())

    def test_protect_and_unlock(self):
        from core import enterprise_risk_calc as calc
        p = self.tmp / "t.xlsx"
        pd.DataFrame({"a": [1]}).to_excel(p, index=False)
        calc.protect_xlsx(p, "pw1")
        self.assertEqual(p.read_bytes()[:4].hex(), "d0cf11e0")
        out, how = calc.unlock(p, ["nope", "pw1"])
        self.assertIn("unlocked", how)
        with self.assertRaises(calc.CalcError):
            calc.protect_xlsx(p, "")


class Screener(Env):
    def test_scrape_fields_and_fallbacks(self):
        from core import enterprise_risk_calc as calc
        row = calc.scrape_symbol(FakeSession(), "ABC", sleep=lambda s: None, log=lambda *_: None)
        self.assertEqual(row["Market Cap"], "₹ 100 Cr.")
        self.assertEqual(row["Profit after tax"], "₹ 1,234 Cr.")
        self.assertEqual(row["Promoter holding"], "55.5%")
        self.assertEqual(row["OPM"], "12%")
        self.assertEqual(row["EPS"], "₹ 9.5")
        self.assertEqual(row["PEG Ratio"], "1.5")
        self.assertEqual(row["Price to book value"], "4.00")                   # 200 / 50, the fallback
        self.assertEqual(row["Debt to equity"], "")

    def test_symbol_with_ampersand_is_url_encoded_and_bad_symbols_refused(self):
        from core import enterprise_risk_calc as calc
        s = FakeSession()
        calc.scrape_symbol(s, "M&M", sleep=lambda x: None, log=lambda *_: None)
        self.assertTrue(any("/company/M%26M/" in u for u in s.calls))
        for bad in ("", "../etc", "A B", "X" * 40, "<script>"):
            with self.assertRaises(calc.CalcError):
                calc.valid_symbol(bad)
        self.assertEqual(calc.valid_symbol(" bajaj-auto "), "BAJAJ-AUTO")

    def test_failed_symbol_gets_an_empty_row_but_stop_propagates(self):
        from core import enterprise_risk_calc as calc
        rows = calc.screener_rows(FakeSession(fail=("/BAD/",)), ["OK1", "BAD", "OK2"], sleep=lambda s: None, log=lambda *_: None, pause=0)
        self.assertEqual([r[0] for r in rows], ["OK1", "BAD", "OK2"])
        self.assertEqual(rows[1][1], "")

        def stop():
            raise self.m.Cancelled("x")
        with self.assertRaises(self.m.Cancelled):
            calc.screener_rows(FakeSession(), ["A"], sleep=lambda s: None, log=lambda *_: None, check=stop)

    def test_login_refused_is_a_clear_error(self):
        from core import enterprise_risk_calc as calc

        class Refused(FakeSession):
            def get(self, url, headers=None, timeout=0):
                return FakeResp("", url="https://www.screener.in/login/?next=/dash/") if "/dash/" in url else FakeResp("")
        with self.assertRaises(calc.CalcError) as cm:
            calc.screener_login("u", "p", session=Refused(), log=lambda *_: None)
        self.assertIn("login FAILED", str(cm.exception))
        with self.assertRaises(calc.CalcError):
            calc.screener_login("", "", session=FakeSession())

    def test_on_demand_file_is_made_and_mailed_to_rms_del(self):
        m = self.m
        FakeSmtp.sent = []
        m.new_session = lambda: FakeSession()
        r = m.screener_once("tcs", "tester", sleep=lambda s: None, smtp_factory=FakeSmtp)
        self.assertTrue(r["ok"], r)
        out = Path(r["file"])
        self.assertEqual(out.name, "TCS_SCREENER.xlsx")
        self.assertEqual(pd.read_excel(out, sheet_name="SCREENER").iloc[0]["SYMBOL"], "TCS")
        subj, atts, to, _cc = FakeSmtp.sent[0]
        self.assertEqual(atts, ["TCS_SCREENER.xlsx"])
        self.assertIn("rms_del@mastertrust.co.in", to)
        self.assertEqual(m.status()["screener"]["status"], "done")
        bad = m.screener_once("../x", "t")
        self.assertFalse(bad["ok"])
        self.assertEqual(m.status()["screener"]["status"], "failed")

    def test_request_screener_validates_and_refuses_a_second_at_once(self):
        m = self.m
        with self.assertRaises(ValueError):
            m.request_screener("a b")
        self.assertTrue(m._SCR_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(m.Busy):
                m.request_screener("TCS")
        finally:
            m._SCR_LOCK.release()


# ───────────────────────────── mailbox ─────────────────────────────
def make_mail(subject, sender, day, hh, mm, att_name=None, payload=b"data"):
    msg = EmailMessage()
    msg["Subject"], msg["From"] = subject, sender
    msg["Date"] = f"{day:%a, %d %b %Y} {hh:02d}:{mm:02d}:00 +0530"
    msg.set_content("hi")
    if att_name:
        msg.add_attachment(payload + f"{hh}{mm}".encode(), maintype="application", subtype="octet-stream", filename=att_name)
    return msg.as_bytes()


class FakePop:
    def __init__(self, mails):
        self.mails = mails

    def top(self, i, n):
        raw = self.mails[i - 1]
        head = raw.split(b"\n\n", 1)[0]
        return "+OK", head.splitlines(), len(head)

    def retr(self, i):
        return "+OK", self.mails[i - 1].splitlines(), len(self.mails[i - 1])


class MailPick(Env):
    NR = "noreply@mastertrust.co.in"

    def test_classify_both_types(self):
        c = self.m.classify
        self.assertEqual(c("redflag", self.NR, "EnterpriseRiskReport Without Margin REDFLAG"), "without")
        self.assertEqual(c("redflag", self.NR, "Enterprise Risk Report With Margin REDFLAG"), "with")
        self.assertIsNone(c("redflag", self.NR, "Enterprise Risk Report With Margin"))
        self.assertEqual(c("simple", self.NR, "Enterprise Risk Report Without Margin"), "without")
        self.assertEqual(c("simple", self.NR, "Enterprise Risk Report With Margin"), "with")
        self.assertIsNone(c("simple", self.NR, "Enterprise Risk Report With Margin REDFLAG"))
        self.assertIsNone(c("redflag", "someone@else.com", "EnterpriseRiskReport Without Margin REDFLAG"))
        self.assertEqual(c("redflag", "Raj <raj.kumari@mastertrust.co.in>", "Default Product_Concentration_Report"), "concentration")

    def test_start_times_window_and_newest(self):
        d = dt.date(2026, 11, 16)
        NR, RJ = self.NR, "raj.kumari@mastertrust.co.in"
        S = "EnterpriseRiskReport Without Margin REDFLAG"
        mails = [make_mail("Default Product_Concentration_Report", RJ, d, 8, 0, "Concentration_abv20percentage.csv"),   # before 10:03 but csv starts 07:30
                 make_mail(S, NR, d, 10, 0, "EnterpriseRiskReport_Without_Margin_REDFLAG_2026-11-16.xlsx"),             # before the start: never
                 make_mail(S, NR, d, 10, 5, "EnterpriseRiskReport_Without_Margin_REDFLAG_2026-11-16.xlsx"),
                 make_mail(S, NR, d, 10, 9, "EnterpriseRiskReport_Without_Margin_REDFLAG_2026-11-16.xlsx"),             # newest: wins
                 make_mail("EnterpriseRiskReport With Margin REDFLAG", NR, d, 10, 9, None),                              # no attachment: skipped
                 make_mail(S, NR, d, 11, 30, "EnterpriseRiskReport_Without_Margin_REDFLAG_2026-11-16.xlsx")]           # after the end
        starts = {"without": dt.time(10, 3), "with": dt.time(10, 3), "concentration": dt.time(7, 30)}
        got = self.m.find_mails(FakePop(mails), len(mails), d, "redflag", starts, dt.time(11, 0))
        self.assertEqual(set(got), {"without", "concentration"})
        self.assertTrue(got["without"][1].endswith(b"109"))
        self.assertEqual(got["concentration"][0], "Concentration_abv20percentage.csv")

    def test_full_day_fallback_finds_a_mail_outside_the_window(self):
        """A mail that came at 08:20 (before the 09:00 start) is missed by the window but found by the full-day look-up."""
        d = dt.date(2026, 11, 16)
        att = "EnterpriseRiskReport_Without_Margin_REDFLAG_2026-11-16.xlsx"
        mails = [make_mail("EnterpriseRiskReport Without Margin REDFLAG", self.NR, d, 8, 20, att),
                 make_mail("EnterpriseRiskReport Without Margin REDFLAG", self.NR, d, 13, 40, att)]
        self.assertEqual(self.m.find_mails(FakePop(mails), 2, d, "redflag", {"without": dt.time(9, 0)}, dt.time(12, 0)), {})
        got = self.m.find_mails(FakePop(mails), 2, d, "redflag", {"without": dt.time(0, 0)}, dt.time(23, 59, 59))
        self.assertTrue(got["without"][1].endswith(b"1340"))                     # newest of the whole day

    def test_old_saved_window_is_moved_to_the_new_one_once(self):
        import json
        p = self.m._settings_path()
        p.write_text(json.dumps({"mail_from": "10:15", "mail_until": "11:00", "run_at": "10:45", "top_n": 7}), encoding="utf-8")
        s = self.m.get_settings()
        self.assertEqual((s["mail_from"], s["mail_until"], s["run_at"], s["top_n"]), ("09:00", "12:00", "10:45", 7))
        self.m.save_settings({"mail_from": "09:30"})                            # a choice made AFTER the move is kept
        self.assertEqual(self.m.get_settings()["mail_from"], "09:30")

    def test_later_start_time_ignores_earlier_mail(self):
        d = dt.date(2026, 11, 16)
        mails = [make_mail("EnterpriseRiskReport Without Margin REDFLAG", self.NR, d, 10, 5, "EnterpriseRiskReport_Without_Margin_REDFLAG_2026-11-16.xlsx")]
        self.assertEqual(self.m.find_mails(FakePop(mails), 1, d, "redflag", {"without": dt.time(10, 30)}, dt.time(11, 0)), {})
        self.assertEqual(set(self.m.find_mails(FakePop(mails), 1, d, "redflag", {"without": dt.time(10, 3)}, dt.time(11, 0))), {"without"})


# ───────────────────────────── the run ─────────────────────────────
@unittest.skipUnless(HAVE_REAL, "real 16-11-2026 backup not available")
class FullRun(Env):
    def setUp(self):
        super().setUp()
        m = self.m
        FakeSmtp.sent = []
        m.STEPS = dict(m.STEPS, mail=lambda base, force=False: {"ok": True, "skipped": True, "msg": "test"})
        m.new_session = lambda: FakeSession()
        self._send = m.send_files
        m.send_files = lambda *a, **k: self._send(*a, **dict(k, smtp_factory=FakeSmtp, sleep=lambda s: None))
        import core.enterprise_risk_calc as calc
        self._pause = calc.screener_rows
        calc.screener_rows = lambda *a, **k: self._pause(*a, **dict(k, pause=0, sleep=lambda s: None))
        self.calc = calc

    def tearDown(self):
        self.m.send_files = self._send
        self.calc.screener_rows = self._pause
        super().tearDown()

    def test_run_redflag_end_to_end(self):
        m = self.m
        self.stage_real()
        r = m.run("manual", "test", alert=lambda h, t: {"sent": False})
        self.assertTrue(r["ok"], r)
        subj, atts, to, cc = FakeSmtp.sent[0]
        self.assertIn("Red Flag", subj)
        d1 = m.d1()
        self.assertEqual(sorted(atts), sorted([f"ENTERPRISE_WITHOUT_MARGIN_REDFLAG_{d1}.xlsx", f"ENTERPRISE_WITH_MARGIN_REDFLAG_{d1}.xlsx"]))
        bk = m.backup_root() / d1
        self.assertTrue((bk / "screener_output_REDFLAG.xlsx").is_file())
        self.assertTrue((bk / f"FULLFILE_CONCENTRATION_{d1}.xlsx").is_file())
        self.assertEqual([p.name for p in m.root().iterdir() if p.is_file()], [])               # working folder is clean
        out, how = self.calc.unlock(bk / f"ENTERPRISE_WITHOUT_MARGIN_REDFLAG_{d1}.xlsx", [TEST_FILE_PW])   # protected with the Credentials password
        self.assertIn("unlocked", how)
        x = pd.ExcelFile(out)
        self.assertEqual(x.sheet_names, ["REDFLAG_WITHOUT_MARGIN", "MTF CONCENTRATION", "CONCENTRATION_ABV20", "SCREENER"])
        self.assertEqual(list(x.parse("SCREENER").columns), self.calc.SCREENER_COLUMNS)
        st = m.status()
        self.assertEqual(st["status"], "done")
        self.assertTrue(st["needs_password"])
        self.assertTrue(all(s["status"] in ("ok", "skipped", "done") for s in st["steps"]), st["steps"])

    def test_switch_to_simple_uses_the_simple_names_and_wording(self):
        m = self.m
        m.save_settings({"variant": "simple"})
        self.stage_real(var="simple")
        r = m.run("manual", "t", alert=lambda h, t: {"sent": False})
        self.assertTrue(r["ok"], r)
        subj, atts, _to, _cc = FakeSmtp.sent[0]
        self.assertNotIn("Red Flag", subj)
        self.assertTrue(all("REDFLAG" not in a for a in atts), atts)
        d1 = m.d1()
        out, _ = self.calc.unlock(m.backup_root() / d1 / f"ENTERPRISE_WITHOUT_MARGIN_{d1}.xlsx", [TEST_FILE_PW])
        self.assertEqual(pd.ExcelFile(out).sheet_names[0], "ENTERPRISERISK_WITHOUT_MARGIN")
        self.assertTrue((m.backup_root() / d1 / "screener_output.xlsx").is_file())
        with self.assertRaises(ValueError):
            m.save_settings({"variant": "other"})

    def test_conc_is_picked_from_mtf_concentration(self):
        m = self.m
        self.stage_real(conc=False)
        src = self.tmp / "mtfconc"
        src.mkdir()
        shutil.copy(REAL / "FULLFILE_CONCENTRATION_16112026.xlsx", src / f"FULLFILE_CONCENTRATION_{m.d1()}.xlsx")
        m.conc_sources = lambda today=None: [src / f"FULLFILE_CONCENTRATION_{m.d1()}.xlsx"]
        self.assertFalse(any(r["key"] == "FULLFILE" and r["ok"] is False for r in m.input_status()))
        r = m.run("manual", "t", alert=lambda h, t: {"sent": False})
        self.assertTrue(r["ok"], r)
        self.assertTrue((src / f"FULLFILE_CONCENTRATION_{m.d1()}.xlsx").is_file())             # the original stays in MTF CONCENTRATION

    def test_missing_conc_fails_with_the_reason_and_one_alert(self):
        m = self.m
        self.stage_real(conc=False)
        m.conc_sources = lambda today=None: []
        alerts = []
        r = m.run("manual", "t", alert=lambda h, t: alerts.append(t) or {"sent": True})
        self.assertFalse(r["ok"])
        self.assertIn("FULLFILE_CONCENTRATION", r["error"])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(FakeSmtp.sent, [])

    def test_no_file_password_means_no_mail_of_unprotected_files(self):
        m = self.m
        self.stage_real()
        os.environ["ENTRISK_FILE_PASSWORD"] = ""
        os.environ["ENTRISK_ABV20_PASSWORD"] = TEST_ABV_PW
        r = m.run("manual", "t", alert=lambda h, t: {"sent": False})
        self.assertFalse(r["ok"])
        self.assertIn("password", r["error"].lower())
        self.assertEqual(FakeSmtp.sent, [])
        self.assertTrue(any(m.root().glob("ENTERPRISE_*")))                                    # kept, not backed up

    def test_screener_login_failure_stops_before_mail(self):
        m = self.m
        self.stage_real()

        def refuse():
            raise self.calc.CalcError("screener.in login FAILED - check the user and password in Credentials")
        m.new_session = refuse
        r = m.run("manual", "t", alert=lambda h, t: {"sent": False})
        self.assertFalse(r["ok"])
        self.assertIn("login FAILED", r["error"])
        self.assertEqual(FakeSmtp.sent, [])

    def test_stop_ends_the_run_and_nothing_stays_running(self):
        m = self.m
        self.stage_real()
        gate = threading.Event()

        def slow(base, force=False):
            while not gate.is_set() and not m._cancel():
                time.sleep(0.05)
            m._check_cancel()
            return {"ok": True, "msg": "x"}
        m.STEPS["mail"] = slow
        res = {}
        th = threading.Thread(target=lambda: res.update(m.run("manual", "t", alert=lambda h, t: {"sent": False})))
        th.start()
        time.sleep(0.4)
        self.assertTrue(m.status()["running"])
        self.assertTrue(m.stop_running("t"))
        th.join(8)
        self.assertFalse(th.is_alive())
        self.assertTrue(res.get("stopped"))
        st = m.status()
        self.assertEqual(st["status"], "stopped")
        self.assertTrue(all(s["status"] != "running" for s in st["steps"]))

    def test_stop_during_screener_ends_the_run(self):
        m = self.m
        self.stage_real()
        started = threading.Event()

        class Slow(FakeSession):
            def get(self, url, headers=None, timeout=0):
                if "/company/" in url:
                    started.set()
                    time.sleep(0.3)
                return super().get(url, headers, timeout)
        m.new_session = lambda: Slow()
        res = {}
        th = threading.Thread(target=lambda: res.update(m.run("manual", "t", alert=lambda h, t: {"sent": False})))
        th.start()
        self.assertTrue(started.wait(30))
        m.stop_running("t")
        th.join(30)
        self.assertFalse(th.is_alive())
        self.assertTrue(res.get("stopped"), res)
        self.assertEqual(FakeSmtp.sent, [])
        self.assertTrue(all(s["status"] != "running" for s in m.status()["steps"]))


class Timer(Env):
    def test_timer_rules(self):
        m = self.m
        calls, runs = [], []
        m.STEPS = dict(m.STEPS, mail=lambda base, force=False: calls.append("mail") or {"ok": True, "msg": "ok"})
        m.run = lambda trigger="manual", by="", alert=None: runs.append(trigger) or {"ok": True}
        mon = dt.datetime(2026, 11, 16, 8, 30)                                  # a Monday
        m.tick(mon)
        self.assertEqual(calls, [])                                            # before 09:00
        m.tick(mon.replace(hour=9, minute=0))
        self.assertEqual(calls, ["mail"])
        m.tick(mon.replace(hour=10, minute=5))
        self.assertEqual(calls, ["mail"])                                      # done: not asked again
        m.tick(mon.replace(hour=10, minute=29))
        self.assertEqual(runs, [])                                             # before the run time
        m.tick(mon.replace(hour=10, minute=30))
        self.assertEqual(runs, [])                                             # inputs are not in -> it waits, no run
        sat = dt.datetime(2026, 11, 14, 10, 31)
        m.tick(sat)
        self.assertEqual(calls, ["mail"])                                      # weekends: nothing
        m.save_settings({"auto": False})
        m.tick(mon.replace(hour=10, minute=40))
        self.assertEqual(runs, [])

    def test_gives_up_once_after_the_window_with_one_alert(self):
        m = self.m
        alerts = []
        m.STEPS = dict(m.STEPS, mail=lambda base, force=False: {"ok": False, "msg": "not there yet"})
        mon = dt.datetime(2026, 11, 16, 12, 5)                                  # after the 12:00 end of the window
        m.tick(mon, alert=lambda h, d: alerts.append(h) or {"sent": True})
        m.tick(mon.replace(minute=20), alert=lambda h, d: alerts.append(h) or {"sent": True})
        self.assertEqual(len(alerts), 1)

    def test_settings_validation_and_defaults(self):
        m = self.m
        s = m.get_settings()
        self.assertEqual((s["variant"], s["run_at"], s["mail_from"], s["mail_until"]), ("redflag", "10:30", "09:00", "12:00"))
        for bad in ({"run_at": "25:61"}, {"mail_from": "12:30"}, {"top_n": 0}, {"variant": "x"}):
            with self.assertRaises(ValueError):
                m.save_settings(bad)
        s = m.save_settings({"mail_from": "9:00", "run_at": "10:45", "top_n": 12, "variant": "simple"})
        self.assertEqual((s["mail_from"], s["run_at"], s["top_n"], s["variant"]), ("09:00", "10:45", 12, "simple"))
        self.assertEqual(m.status()["settings"]["variant"], "simple")

    def test_upload_rules(self):
        m = self.m
        self.assertTrue(m.accept_upload("FULLFILE_CONCENTRATION_17112026.xlsx", b"x")["ok"])
        for bad in ("evil.py", "run.bat", "", ".hidden"):
            with self.assertRaises(ValueError):
                m.accept_upload(bad, b"x")
        with self.assertRaises(ValueError):
            m.accept_upload("a.xlsx", b"")


if __name__ == "__main__":
    unittest.main()
