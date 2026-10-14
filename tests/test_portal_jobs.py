"""Portal Downloads (core/scheduler/portal_jobs.py): placeholders, validation, catalogue sync, and the run
(downloaded / no file) with NSE, SMTP and Kambala mocked.

    python -m unittest tests.test_portal_jobs -v
"""
import datetime as dt
import gzip
import unittest
from unittest import mock

from tests.test_scheduler_processes import Base, Ctx


def good(**over):
    j = {"name": "CO Position Demo", "source": "member", "segment": "co", "folder": "Reports",
         "filename": "Position_NCL_CO_0_CM_{member}_{YYYYMMDD}_F_0000.csv.gz", "days_back": 1, "trading_day_only": False,
         "wait_minutes": 0, "unzip": True,
         "email": {"enabled": True, "to": "a@mastertrust.co.in", "cc": "", "subject": "NCL CO Position File - {DD-MM-YYYY}",
                   "body": "Hi Team,\n\nPlease find attached {FILE}.", "signature": "Regards,\nKartavya", "notify_no_file": True},
         "kambala": {"enabled": True, "name": ""}}
    j.update(over)
    return j


MEMBER = {"member_code": "06436", "login_id": "M0213815"}


class Placeholders(Base):
    def test_date_tokens_and_member(self):
        from core.scheduler import portal_jobs as pj
        t = pj.tokens_for(dt.date(2026, 10, 13), "06436")
        self.assertEqual(pj.fill("Position_NCL_CO_0_CM_{member}_{YYYYMMDD}_F_0000.csv.gz", t),
                         "Position_NCL_CO_0_CM_06436_20261013_F_0000.csv.gz")
        self.assertEqual(pj.fill("{DDMMYYYY}|{DDMMYY}|{DD-MON-YYYY}|{Mon}", t), "13102026|131026|13-OCT-2026|Oct")

    def test_unknown_placeholders_are_found(self):
        from core.scheduler import portal_jobs as pj
        self.assertEqual(pj.unknown_tokens("a_{YYYYMMDD}_{TYPO}_{member}"), ["TYPO"])
        self.assertEqual(pj.unknown_tokens("{NAME} {FILE}", extra=("NAME", "FILE")), [])

    def test_file_date_goes_back_by_trading_days(self):
        from core.scheduler import portal_jobs as pj
        mon = dt.date(2026, 10, 12)                       # a Monday
        with mock.patch("core.scheduler.helpers.prev_trading_day", return_value=dt.date(2026, 10, 9)) as p:
            self.assertEqual(pj.file_date({"days_back": 1}, mon), dt.date(2026, 10, 9))
            p.assert_called_once_with(mon, 1)
        self.assertEqual(pj.file_date({"days_back": 0}, mon), mon)


class Validation(Base):
    def test_good_job_is_clean(self):
        from core.scheduler import portal_jobs as pj
        errs, job = pj.clean_job(good())
        self.assertEqual(errs, [])
        self.assertEqual(job["segment"], "CO")
        self.assertEqual(job["id"], "co_position_demo")

    def test_problems_are_explained(self):
        from core.scheduler import portal_jobs as pj
        errs, _ = pj.clean_job(good(name="", segment="", folder="", filename="x_{NOPE}.csv"))
        text = " ".join(errs)
        for word in ("name", "Segment", "folder", "NOPE"):
            self.assertIn(word, text)
        errs, _ = pj.clean_job(good(email={"enabled": True, "to": ""}))
        self.assertTrue(any("To" in e for e in errs))
        errs, _ = pj.clean_job(good(name="a/b"))
        self.assertTrue(errs)
        errs, _ = pj.clean_job(good(days_back=99))
        self.assertTrue(errs)


class Catalogue(Base):
    def test_saved_job_becomes_a_process_and_can_be_removed(self):
        from core.scheduler import catalog, portal_jobs as pj
        job = pj.save_job(good())
        d = catalog.get("portal_" + job["id"])
        self.assertIsNotNone(d)
        self.assertEqual(d.name, "CO Position Demo")
        self.assertEqual(d.folder, d.name)
        self.assertEqual(set(d.uses), {"nse", "smtp", "kambala_ftp"})
        self.assertFalse(d.retry_safe)                    # it e-mails: never auto-retried
        pj.delete_job(job["id"])
        self.assertIsNone(catalog.get("portal_" + job["id"]))

    def test_duplicate_names_and_builtin_names_are_refused(self):
        from core.scheduler import portal_jobs as pj
        pj.save_job(good())
        with self.assertRaises(ValueError):
            pj.save_job(good())                            # same name
        with self.assertRaises(ValueError):
            pj.save_job(good(name="PEAK MARGIN REPORT"))   # a built-in process
        with self.assertRaises(ValueError):
            pj.save_job(good(name="ncl co position file")) # the built-in NCL CO POSITION FILE (any capitalisation)
        pj.delete_job("co_position_demo")

    def test_edit_keeps_the_id(self):
        from core.scheduler import portal_jobs as pj
        job = pj.save_job(good())
        j2 = pj.save_job(good(name="Renamed file", wait_minutes=5), job_id=job["id"])
        self.assertEqual(j2["id"], job["id"])
        self.assertEqual(pj.get_job(job["id"])["wait_minutes"], 5)
        pj.delete_job(job["id"])


class Running(Base):
    def _job(self, **over):
        from core.scheduler import portal_jobs as pj
        errs, job = pj.clean_job(good(**over))
        self.assertEqual(errs, [])
        return job

    def test_downloads_unzips_emails_then_uploads(self):
        from core.scheduler import paths, portal_jobs as pj
        job = self._job()
        inp = paths.proc_dir(job["name"], "inputs")

        def fake_dl(kind, segment, folder, name, dest, ctx, wait=0, date=None):
            p = dest / name
            with gzip.open(p, "wb") as f:
                f.write(b"a,b\n1,2\n")
            self.assertEqual((kind, segment, folder), ("member", "CO", "Reports"))
            return p
        order = []
        with mock.patch("core.scheduler.portal_jobs.connections.require", return_value=MEMBER), \
             mock.patch("core.scheduler.nse.download_wait", side_effect=fake_dl), \
             mock.patch("core.scheduler.helpers.send_email", side_effect=lambda *a, **k: order.append(("mail", a))) as send, \
             mock.patch("core.scheduler.helpers.kambala_upload", side_effect=lambda f, c: order.append(("ftp", f)) or True) as ftp:
            res = pj.run_job(job, Ctx())
        self.assertEqual([o[0] for o in order], ["mail", "ftp"])           # e-mail FIRST, then Kambala
        subject, body, to, cc, files = send.call_args[0][:5]
        self.assertTrue(subject.startswith("NCL CO Position File - "))
        self.assertIn("Position_NCL_CO_0_CM_06436_", files[0].name)
        self.assertTrue(files[0].name.endswith("_F_0000.csv"))              # the unzipped file is what goes out
        self.assertIn("Please find attached", body)
        self.assertIn("Regards", body)
        self.assertEqual(to, ["a@mastertrust.co.in"])
        self.assertIn("e-mailed", res)
        self.assertIn("uploaded to Kambala", res)

    def test_no_file_is_a_success_with_a_no_file_email_and_no_upload(self):
        from core.scheduler import portal_jobs as pj
        job = self._job()
        with mock.patch("core.scheduler.portal_jobs.connections.require", return_value=MEMBER), \
             mock.patch("core.scheduler.nse.download_wait", side_effect=FileNotFoundError("none")), \
             mock.patch("core.scheduler.helpers.send_email") as send, \
             mock.patch("core.scheduler.helpers.kambala_upload") as ftp:
            ctx = Ctx()
            res = pj.run_job(job, ctx)
        self.assertIn("No file", res)
        subject, body = send.call_args[0][:2]
        self.assertTrue(subject.endswith(" - NO FILE"))
        self.assertIn("No file is available on the NSE member portal", body)
        self.assertEqual(send.call_args[0][4], [])                          # nothing attached
        ftp.assert_not_called()
        self.assertIn("NO FILE", ctx.text())

    def test_no_file_email_can_be_switched_off(self):
        from core.scheduler import portal_jobs as pj
        job = self._job()
        job["email"]["notify_no_file"] = False
        with mock.patch("core.scheduler.portal_jobs.connections.require", return_value=MEMBER), \
             mock.patch("core.scheduler.nse.download_wait", side_effect=FileNotFoundError("none")), \
             mock.patch("core.scheduler.helpers.send_email") as send:
            pj.run_job(job, Ctx())
        send.assert_not_called()

    def test_rename_on_the_ftp(self):
        from core.scheduler import portal_jobs as pj
        job = self._job(email={"enabled": False}, kambala={"enabled": True, "name": "POS_{DDMMYYYY}.csv"})

        def fake_dl(kind, segment, folder, name, dest, ctx, wait=0, date=None):
            p = dest / name
            with gzip.open(p, "wb") as f:
                f.write(b"x")
            return p
        sent = []
        with mock.patch("core.scheduler.portal_jobs.connections.require", return_value=MEMBER), \
             mock.patch("core.scheduler.nse.download_wait", side_effect=fake_dl), \
             mock.patch("core.scheduler.helpers.kambala_upload", side_effect=lambda f, c: sent.extend(f) or True):
            pj.run_job(job, Ctx())
        self.assertRegex(sent[0].name, r"^POS_\d{8}\.csv$")


class Test_download(Base):
    def test_test_never_needs_email_settings(self):
        from core.scheduler import portal_jobs as pj
        r = good()
        r["email"] = {"enabled": True, "to": ""}                          # would fail a save, must not fail a test
        with mock.patch("core.scheduler.portal_jobs.threading.Thread") as th:
            out = pj.start_test(r)
        self.assertIn("id", out)
        th.assert_called_once()


if __name__ == "__main__":
    unittest.main()
