"""FO ASM/OTM/MWPL processes: the mail (10:00 PM) and the upload (previous trading day, ONE date per run).
Nothing real is touched: NSE, the upload API and SMTP are mocked.

    python -m unittest tests.test_fo_files -v
"""
import datetime as dt
import gzip
import json
from pathlib import Path
from unittest import mock

from tests.test_scheduler_processes import Base, Ctx

MEM = "06436"
D = dt.date(2026, 11, 12)                                 # the trade date used by the run tests (a Thursday)

ASMOTM_HDR = "SYMBOL,INSTRUMENT,EXPIRY_DATE,STRIKE,OPTION_TYPE,CLIENT_CODE,TM_CODE,MARGIN\n"
ASMOTM_ROWS = "ABC,OPTSTK,26-NOV-2026,100,CE,6NBB2189,06436,5\nXYZ,OPTSTK,26-NOV-2026,200,PE,6NBB2190,06436,7\n"
FOPT_ROWS = "06436,06436,6NBB2189,OPTSTK,ABC,26-NOV-2026,100,CE,1,2,3,4,5,6,7\n"


def fake_nse(available: dict):
    """nse.download replacement: writes the file when its name is in `available` ({name: bytes}); .gz names are gzipped."""
    calls = []

    def download(kind, segment, folder, filename, dest, ctx, date=None):
        calls.append(filename)
        if filename not in available:
            return None
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        data = available[filename]
        if filename.lower().endswith(".gz"):
            data = gzip.compress(data)
        p = dest / filename
        p.write_bytes(data)
        return p
    return download, calls


def names(d):
    return {
        "fopt": f"F_OPTCSHDTLS01_{MEM}_{d:%d%m%Y}.CSV.gz",
        "asmotm": f"F_{MEM}_ASMOTM_{d:%d%m%Y}.csv",
        "pdf": f"{MEM}_F_ASM_{d:%d%m%y}.pdf.gz",
        "mwpl": f"F_MWPLVIOL02_{MEM}_{d:%Y%m%d}.CSV.gz",
    }


class TestHelpers(Base):
    def test_match_listed_name(self):
        from core.scheduler import fo_files as F
        lst = ["f_06436_asmotm_12112026.CSV", "06436_F_ASM_12112026.pdf.gz", "other.csv"]
        self.assertEqual(F.match_listed_name(lst, [f"F_{MEM}_ASMOTM_12112026.csv"]), "f_06436_asmotm_12112026.CSV")
        # the PDF named with a 4-digit year is found through the second wanted name
        self.assertEqual(F.match_listed_name(lst, [f"{MEM}_F_ASM_121126.pdf.gz", f"{MEM}_F_ASM_12112026.pdf.gz"]),
                         "06436_F_ASM_12112026.pdf.gz")
        self.assertIsNone(F.match_listed_name(lst, [f"F_{MEM}_ASMOTM_13112026.csv"]))        # another day: never matches

    def test_strip_header_copy(self):
        from core.scheduler import fo_files as F
        c, out = Ctx(), self.tmp / "ready"
        src = self.tmp / "a.csv"
        src.write_text(ASMOTM_HDR + ASMOTM_ROWS)
        p = F.strip_header_copy(src, out, c)
        self.assertEqual(p.read_text(), ASMOTM_ROWS)
        self.assertEqual(src.read_text(), ASMOTM_HDR + ASMOTM_ROWS)                          # original untouched
        src.write_text(ASMOTM_ROWS)                                                          # no header: nothing dropped
        self.assertEqual(F.strip_header_copy(src, out, c).read_text(), ASMOTM_ROWS)
        src.write_text(ASMOTM_HDR)                                                           # header only
        self.assertIsNone(F.strip_header_copy(src, out, c))
        src.write_text("")                                                                   # empty
        self.assertIsNone(F.strip_header_copy(src, out, c))

    def test_add_header_idempotent(self):
        from core.scheduler import fo_files as F
        c = Ctx()
        p = self.tmp / "f.csv"
        p.write_text(FOPT_ROWS)
        F.add_header_to_csv(p, F.F_OPT_HEADERS, c)
        first = p.read_text().splitlines()[0]
        self.assertTrue(first.startswith("CM Code,TM Code/CP Code"))
        F.add_header_to_csv(p, F.F_OPT_HEADERS, c)                                           # second time: unchanged
        self.assertEqual(p.read_text().count("CM Code"), 1)

    def test_fetch_gz_variants_and_damaged(self):
        from core.scheduler import fo_files as F
        c = Ctx()
        spec = F.specs(MEM)[0]
        n = names(D)["fopt"]
        # NSE serves a plain file under the .gz name: kept, not an error
        def plain(kind, seg, folder, filename, dest, ctx, date=None):
            p = Path(dest) / filename
            p.write_bytes(FOPT_ROWS.encode())
            return p
        with mock.patch("core.scheduler.fo_files.nse.download", plain):
            p = F.fetch(spec, D, self.tmp / "w1", c)
        self.assertEqual(p.read_text(), FOPT_ROWS)
        # a cut-off gz is an ERROR, never "no file"
        def cut(kind, seg, folder, filename, dest, ctx, date=None):
            p = Path(dest) / filename
            p.write_bytes(gzip.compress(FOPT_ROWS.encode() * 50)[:25])
            return p
        with mock.patch("core.scheduler.fo_files.nse.download", cut):
            with self.assertRaises(RuntimeError):
                F.fetch(spec, D, self.tmp / "w2", c)

    def test_fetch_uses_listing_when_exact_name_refused(self):
        from core.scheduler import fo_files as F
        c = Ctx()
        spec = F.specs(MEM)[1]                                           # ASMOTM
        real = f"f_{MEM}_asmotm_{D:%d%m%Y}.CSV"                          # NSE spells it differently
        avail = {real: (ASMOTM_HDR + ASMOTM_ROWS).encode()}
        dl, calls = fake_nse(avail)
        with mock.patch("core.scheduler.fo_files.nse.download", dl), \
             mock.patch("core.scheduler.fo_files.nse.list_dir", lambda *a, **k: [real]):
            p = F.fetch(spec, D, self.tmp / "w", c)
        self.assertEqual(p.name, spec["local"](D))                       # renamed to the expected upload name
        self.assertIn(real, calls)


class TestDateAndSchedule(Base):
    def test_previous_trading_day_follows_holiday_calendar(self):
        from core.scheduler import helpers as H
        sat, fri, thu, tue, mon = (dt.date(2026, 11, 14), dt.date(2026, 11, 13), dt.date(2026, 11, 12),
                                   dt.date(2026, 11, 17), dt.date(2026, 11, 16))
        with mock.patch.object(H, "holidays", lambda: set()), mock.patch.object(H, "special_days", lambda: set()):
            self.assertEqual(H.prev_trading_day(sat), fri)               # Saturday 00:30 run -> Friday
            self.assertEqual(H.prev_trading_day(tue), mon)               # Tuesday 08:00 -> Monday
            self.assertEqual(H.prev_trading_day(fri), thu)
        with mock.patch.object(H, "holidays", lambda: {fri}), mock.patch.object(H, "special_days", lambda: set()):
            self.assertEqual(H.prev_trading_day(sat), thu)               # Friday a holiday -> Thursday (already done Fri 08:00)

    def test_schedule_rules_give_exactly_the_requested_times(self):
        from core.scheduler import rules
        tz = rules.get_tz("Asia/Kolkata")
        upload = {"rule": {"type": "multi", "parts": [
            {"type": "times", "times": ["08:00"], "days": [1, 2, 3, 4]},
            {"type": "times", "times": ["00:30"], "days": [5]}]}, "tz": "Asia/Kolkata", "skip_holidays": False}
        self.assertEqual(rules.validate_schedule_timing(upload), [])
        after = dt.datetime(2026, 11, 12, 12, 0, tzinfo=tz)              # Thursday noon
        got = []
        for u in rules.occurrences(upload, after, lambda d: False):
            got.append(u.astimezone(tz).strftime("%a %H:%M"))
            if len(got) == 5:
                break
        # Fri 08:00, Sat 00:30, then NO Sunday / Monday, Tue 08:00, Wed 08:00, Thu 08:00
        self.assertEqual(got, ["Fri 08:00", "Sat 00:30", "Tue 08:00", "Wed 08:00", "Thu 08:00"])
        mail = {"rule": {"type": "times", "times": ["22:00"], "days": [0, 1, 2, 3, 4]}, "tz": "Asia/Kolkata",
                "skip_holidays": True}
        hol = {dt.date(2026, 11, 13)}                                    # Friday is a market holiday
        got = [u.astimezone(tz).strftime("%a %H:%M") for u, _ in zip(rules.occurrences(mail, after, lambda d: d in hol), range(3))]
        self.assertEqual(got, ["Thu 22:00", "Mon 22:00", "Tue 22:00"])


class TestUploadProcess(Base):
    def setUp(self):
        super().setUp()
        from core.scheduler.processes import fo_files_upload as U
        self.U = U
        self.avail = {names(D)["fopt"]: FOPT_ROWS.encode(),
                      names(D)["asmotm"]: (ASMOTM_HDR + ASMOTM_ROWS).encode(),
                      names(D)["pdf"]: b"%PDF-1.4 fake"}
        self.posts = []

    def run_it(self, avail=None, params=None, upload_fn=None):
        U = self.U
        dl, self.calls = fake_nse(self.avail if avail is None else avail)

        def up(folder, path, template, d, ctx, mime="application/zip", part_name=None):
            self.posts.append({"name": Path(path).name, "part": part_name, "template": template, "date": d, "mime": mime,
                               "bytes": Path(path).read_bytes()})
            return '{"success":true}'
        with mock.patch("core.scheduler.fo_files.nse.download", dl), \
             mock.patch("core.scheduler.fo_files.nse.list_dir", lambda *a, **k: []), \
             mock.patch("core.scheduler.processes.fo_files_upload.connections.require", lambda g, *f: {"member_code": MEM}), \
             mock.patch("core.scheduler.processes.fo_files_upload.H.prev_trading_day", lambda *a, **k: D), \
             mock.patch("core.scheduler.processes.fo_files_upload.P.upload", upload_fn or up):
            ctx = Ctx()
            p = {"wait_minutes": 0, "force_date": ""}
            p.update(params or {})
            try:
                res = U._run(p, ctx)
            except BaseException as e:
                res = e
            return res, ctx

    def test_uploads_one_date_three_files_header_removed_from_asmotm_only(self):
        res, ctx = self.run_it()
        self.assertEqual(res, "12-11-2026: uploaded F_OPTCSHDTLS01, ASMOTM, ASM_PDF")
        by = {p["template"]: p for p in self.posts}
        self.assertEqual(len(self.posts), 3)
        self.assertTrue(all(p["date"] == D for p in self.posts))
        self.assertEqual(by[f"F_OPTCSHDTLS01_{MEM}_DDMMYYYY.csv"]["bytes"], FOPT_ROWS.encode())      # raw
        self.assertEqual(by[f"F_{MEM}_ASMOTM_DDMMYYYY.csv"]["bytes"], ASMOTM_ROWS.encode())          # header dropped
        self.assertEqual(by[f"F_{MEM}_ASMOTM_DDMMYYYY.csv"]["part"], f"F_{MEM}_ASMOTM_{D:%d%m%Y}.csv")
        self.assertEqual(by[f"{MEM}_F_ASM_DDMMYY.pdf"]["mime"], "application/pdf")
        self.assertEqual(by[f"{MEM}_F_ASM_DDMMYY.pdf"]["part"], f"{MEM}_F_ASM_{D:%d%m%y}.pdf")
        # ONLY the previous trading day was ever asked for: no other date in any NSE request
        self.assertTrue(all(f"{D:%d%m%Y}" in c or f"{D:%d%m%y}" in c for c in self.calls), self.calls)
        # backed up under the TRADE date, work folder gone
        from core.scheduler import paths
        bk = paths.proc_dir(self.U.FOLDER, "backup") / "12-11-2026"
        self.assertTrue((bk / f"F_{MEM}_ASMOTM_{D:%d%m%Y}.csv").is_file())
        self.assertFalse((paths.proc_dir(self.U.FOLDER, "inputs") / "12-11-2026").exists())

    def test_second_run_uploads_nothing_twice(self):
        self.run_it()
        self.posts.clear()
        res, ctx = self.run_it()
        from core.scheduler.catalog import Skipped
        self.assertIsInstance(res, Skipped)
        self.assertEqual(self.posts, [])

    def test_partial_day_is_not_an_error(self):
        res, ctx = self.run_it(avail={names(D)["fopt"]: FOPT_ROWS.encode()})       # ASMOTM and the PDF are not on NSE
        self.assertIn("not available on NSE: ASMOTM, ASM_PDF", res)
        self.assertEqual(len(self.posts), 1)

    def test_nothing_on_nse_fails_loudly(self):
        res, ctx = self.run_it(avail={})
        self.assertIsInstance(res, FileNotFoundError)
        self.assertEqual(self.posts, [])

    def test_failed_upload_is_reported_and_retried_next_time(self):
        def boom(folder, path, template, d, ctx, mime="x", part_name=None):
            if "ASMOTM" in template:
                raise RuntimeError("HTTP 500 body")
            return '{"success":true}'
        res, ctx = self.run_it(upload_fn=boom)
        self.assertIsInstance(res, RuntimeError)
        self.assertIn("ASMOTM", str(res))
        from core.scheduler import fo_files as F
        self.assertTrue(F.is_uploaded(self.U.FOLDER, "F_OPTCSHDTLS01", D))
        self.assertFalse(F.is_uploaded(self.U.FOLDER, "ASMOTM", D))                       # will be done on the next run
        self.posts.clear()
        res, ctx = self.run_it()                                                          # next run: only the missing one
        self.assertEqual([p["template"] for p in self.posts], [f"F_{MEM}_ASMOTM_DDMMYYYY.csv"])

    def test_force_date_uploads_that_day_again(self):
        self.run_it()
        self.posts.clear()
        res, ctx = self.run_it(params={"force_date": "12-11-2026"})
        self.assertEqual(len(self.posts), 3)


class TestMailProcess(Base):
    def setUp(self):
        super().setUp()
        from core.scheduler.processes import fo_asm_otm_mail as M
        self.M = M
        self.today = dt.date.today()
        self.sent = []

    def run_it(self, avail, trigger="SCHEDULED"):
        M = self.M
        dl, _ = fake_nse(avail)

        def send(subject, body, to, cc, attachments, ctx, html=False, **kw):
            self.sent.append({"subject": subject, "body": body, "att": [Path(a).name for a in attachments],
                              "fopt": next((Path(a).read_text() for a in attachments if "OPTCSH" in Path(a).name), None)})
        with mock.patch("core.scheduler.fo_files.nse.download", dl), \
             mock.patch("core.scheduler.fo_files.nse.list_dir", lambda *a, **k: []), \
             mock.patch("core.scheduler.processes.fo_asm_otm_mail.connections.require", lambda g, *f: {"member_code": MEM}), \
             mock.patch("core.scheduler.helpers.is_trading_day", lambda *a, **k: True), \
             mock.patch("core.scheduler.processes.fo_asm_otm_mail.H.send_email", send):
            ctx = Ctx(trigger)
            try:
                res = M._run({"to": "a@b.co", "cc": "", "wait_minutes": 0}, ctx)
            except BaseException as e:
                res = e
            return res, ctx

    def test_mail_has_today_files_with_header_and_names_missing(self):
        d = self.today
        res, ctx = self.run_it({names(d)["fopt"]: FOPT_ROWS.encode(), names(d)["asmotm"]: (ASMOTM_HDR + ASMOTM_ROWS).encode()})
        self.assertEqual(len(self.sent), 1)
        m = self.sent[0]
        self.assertEqual(len(m["att"]), 2)
        self.assertTrue(m["fopt"].startswith("CM Code,"))
        self.assertIn("not available", m["body"])
        self.assertIn(names(d)["pdf"][:-3].replace(".pdf", ".pdf"), m["body"])
        # a scheduled second run the same evening does not mail again
        res2, _ = self.run_it({names(d)["fopt"]: FOPT_ROWS.encode()})
        from core.scheduler.catalog import Skipped
        self.assertIsInstance(res2, Skipped)
        self.assertEqual(len(self.sent), 1)

    def test_no_file_sends_not_found_mail_and_fails(self):
        res, ctx = self.run_it({})
        self.assertIsInstance(res, FileNotFoundError)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["att"], [])
        self.assertIn("No FO ASM/OTM/MWPL files were found", self.sent[0]["body"])
