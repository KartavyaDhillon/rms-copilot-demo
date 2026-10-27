"""COMMON FILES auto-fetch from FTP: skip when already uploaded, download + validate, resume, alert once."""
import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

from core import common_ftp as F


class FakeFTP:
    """Just enough of ftplib.FTP: nlst, size, retrbinary (with rest), voidcmd, quit, close."""
    def __init__(self, files, drop_first=False):
        self.files, self.drop = files, drop_first
    def nlst(self): return list(self.files)
    def size(self, n): return len(self.files[n])
    def voidcmd(self, c): return "200"
    def retrbinary(self, cmd, cb, rest=None):
        data = self.files[cmd.split(" ", 1)[1]]
        start = rest or 0
        if self.drop:
            self.drop = False
            cb(data[start:start + 10])
            raise OSError("connection reset")        # an ftplib "all_errors" member
        cb(data[start:])
    def quit(self): pass
    def close(self): pass


class CommonFtp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {
            "SA_COMMON_DIR": self.tmp, "COMMON_FTP_HOST": "h", "COMMON_FTP_USER": "u", "COMMON_FTP_PASS": "p"})
        self.env.start()
        F._STATE.update(date="", files={}, attempts=0, alerted=False, done=False, running=False, message="")
        self.names = F.names_today()
        self.risk = (b"ACCOUNTID,Zone\n" + b"A1,Z\n" * 50)
        self.hold = (b"Client,ISIN,Qty\n" + b"A1,INE1,5\n" * 50)
        self.sleep = mock.patch("core.common_ftp.time.sleep", lambda *_: None)
        self.sleep.start()
        self.hk = mock.patch("core.short_allocation.housekeep_common_files", create=True)

    def tearDown(self):
        self.env.stop(); self.sleep.stop()

    def _fetch(self, files, **kw):
        with mock.patch.object(F, "_connect", lambda: FakeFTP(files, **kw)), \
             mock.patch("core.short_allocation.housekeep_common_files") as hk:
            r = F.fetch_once()
        return r, hk

    def test_downloads_both_and_runs_housekeeping(self):
        r, hk = self._fetch({self.names["RISK_REPORT"]: self.risk, self.names["HOLDING"]: self.hold})
        self.assertTrue(r["ok"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, self.names["RISK_REPORT"])))
        hk.assert_called_once()                       # yesterday's files go to the backup folder, like the Upload button
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".part")])

    def test_file_uploaded_by_hand_is_not_downloaded(self):
        p = os.path.join(self.tmp, self.names["RISK_REPORT"])
        open(p, "wb").write(self.risk)
        os.utime(p, (1, 1))
        r, hk = self._fetch({self.names["RISK_REPORT"]: b"ACCOUNTID,X\nDIFFERENT\n", self.names["HOLDING"]: self.hold})
        self.assertEqual(open(p, "rb").read(), self.risk)                      # untouched
        self.assertEqual(F._STATE["files"][self.names["RISK_REPORT"]]["status"], "already there")
        self.assertEqual(F._STATE["files"][self.names["HOLDING"]]["status"], "downloaded")

    def test_missing_on_ftp_waits(self):
        r, _ = self._fetch({})
        self.assertFalse(r["ok"])
        self.assertEqual(F._STATE["files"][self.names["RISK_REPORT"]]["status"], "not on ftp")

    def test_empty_or_wrong_file_is_rejected_and_not_kept(self):
        r, _ = self._fetch({self.names["RISK_REPORT"]: b"no,account,column\n1,2,3\n", self.names["HOLDING"]: self.hold})
        self.assertFalse(os.path.exists(os.path.join(self.tmp, self.names["RISK_REPORT"])))
        self.assertEqual(F._STATE["files"][self.names["RISK_REPORT"]]["status"], "error")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, self.names["HOLDING"])))

    def test_dropped_connection_resumes(self):
        r, _ = self._fetch({self.names["RISK_REPORT"]: self.risk, self.names["HOLDING"]: self.hold}, drop_first=True)
        self.assertTrue(r["ok"])
        self.assertEqual(open(os.path.join(self.tmp, self.names["RISK_REPORT"]), "rb").read(), self.risk)

    def test_file_still_being_written_is_not_taken(self):
        class Growing(FakeFTP):
            n = 0
            def size(self, name):
                Growing.n += 1
                return len(self.files[name]) + Growing.n
        with mock.patch.object(F, "_connect", lambda: Growing({self.names["RISK_REPORT"]: self.risk})):
            F.fetch_once()
        self.assertEqual(F._STATE["files"][self.names["RISK_REPORT"]]["status"], "being written")

    def test_alert_goes_once_from_sender(self):
        with mock.patch.dict(os.environ, {"SENDER_EMAIL": "kartavya.dhillon@mastertrust.co.in", "SENDER_PASSWORD": "x"}), \
             mock.patch("core.common_ftp.smtplib.SMTP") as smtp:
            F._STATE["attempts"] = 4
            F._alert_if_missing(); F._alert_if_missing()
        self.assertEqual(smtp.call_count, 1)
        sent = smtp.return_value.__enter__.return_value.send_message.call_args[0][0]
        self.assertEqual(sent["From"], "kartavya.dhillon@mastertrust.co.in")
        self.assertIn("rms_del@mastertrust.co.in", sent["To"])

    def test_window_defaults_and_names(self):
        a, b = F.window()
        self.assertEqual((a.hour, a.minute, b.hour, b.minute), (7, 30, 8, 15))
        self.assertEqual(set(F.names_today()), {"HOLDING", "RISK_REPORT"})      # DORMANT is not fetched


if __name__ == "__main__":
    unittest.main()
