"""core/mail_identity.py - the sender switch and the "moving to noreply" note. No network: a fake SMTP class stands in."""
import os
import smtplib
import tempfile
import unittest
from datetime import date
from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest import mock

from core import mail_identity as mi

OLD, NEW = mi.OLD_USER, mi.NEW_USER


class FakeSMTP:
    refuse_noreply = False
    log = []

    def __init__(self, *a, **k):
        self.logins, self.sent = [], []
        FakeSMTP.log.append(self)

    def ehlo(self, *a): return (250, b"ok")
    def starttls(self, *a, **k): return (220, b"ok")

    def login(self, user, pw, *a, **k):
        if user == NEW and FakeSMTP.refuse_noreply:
            raise smtplib.SMTPAuthenticationError(535, b"bad")
        self.logins.append(user)
        return (235, b"ok")

    def sendmail(self, from_addr, to_addrs, msg, *a, **k):
        self.sent.append((from_addr, msg))
        return {}

    def send_message(self, msg, from_addr=None, to_addrs=None, *a, **k):          # like the real one: flattens, then sendmail()
        from email.utils import parseaddr
        self.sendmail(from_addr or parseaddr(msg["From"])[1], to_addrs or msg["To"], msg.as_string())
        return {}

    def quit(self): return (221, b"bye")
    def __enter__(self): return self
    def __exit__(self, *a): return False


HTML = "<html><body><p>Dear Sir,</p><table><tr><td>Report</td></tr></table><br>Best regards,<br>Kartavya Dhillon</body></html>"


def html_msg(sender=OLD, html=HTML):
    m = MIMEMultipart("mixed")
    m["From"], m["To"], m["Subject"] = f"Kartavya <{sender}>", "a@b.c", "Report"
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("Dear Sir,\nReport\n\nBest regards,\nKartavya", "plain"))
    alt.attach(MIMEText(html, "html"))
    m.attach(alt)
    att = MIMEApplication(b"\x00\x01binary-data\xff", Name="r.xlsx")
    att["Content-Disposition"] = 'attachment; filename="r.xlsx"'
    m.attach(att)
    return m


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.conf = Path(d) / ".mail_identity.json"
        self.p = [mock.patch.object(mi, "CONF_FILE", self.conf),
                  mock.patch.dict(os.environ, {"NOREPLY_SMTP_PASS": "pw"}, clear=False),
                  mock.patch.object(smtplib, "SMTP", FakeSMTP)]
        for x in self.p:
            x.start()
        FakeSMTP.refuse_noreply = False
        FakeSMTP.log = []
        mi.install()

    def tearDown(self):
        for x in reversed(self.p):
            x.stop()

    def send(self, msg, login=OLD, raw=False):
        with smtplib.SMTP("h", 587) as s:
            s.starttls()
            s.login(login, "x")
            if raw:
                s.sendmail(msg["From"], ["a@b.c"], msg.as_string())
            else:
                s.send_message(msg)
        return FakeSMTP.log[-1]


class Settings(unittest.TestCase):
    def test_auto_switches_on_the_date(self):
        cfg = mi._defaults()
        self.assertEqual(mi.effective(cfg, date(2026, 11, 30)), "kartavya")
        self.assertEqual(mi.effective(cfg, date(2026, 12, 1)), "noreply")

    def test_set_mode_validates(self):
        with mock.patch.object(mi, "CONF_FILE", Path(tempfile.mkdtemp()) / "c.json"):
            self.assertEqual(mi.set_mode("noreply")["effective"], "noreply")
            self.assertEqual(mi.get_config()["mode"], "noreply")
            with self.assertRaises(ValueError):
                mi.set_mode("bogus")


class Note(unittest.TestCase):
    def test_above_signature(self):
        out = mi.put_note_html(HTML)
        self.assertLess(out.index("safe sender"), out.index("Best regards"))
        self.assertGreater(out.index("safe sender"), out.index("</table>"))
        self.assertEqual(mi.put_note_html(out), out)                      # idempotent

    def test_no_signature_goes_before_body_end(self):
        out = mi.put_note_html("<html><body><p>Hi</p></body></html>")
        self.assertTrue(out.endswith("</body></html>"))
        self.assertIn("safe sender", out)

    def test_text_part(self):
        out = mi.put_note_text("Dear Sir,\nReport\n\nRegards,\nK")
        self.assertLess(out.index("safe sender"), out.index("Regards"))


class Wrapper(Base):
    def test_notice_mode_keeps_sender_and_adds_note_above_signature(self):
        mi.set_mode("kartavya")
        s = self.send(html_msg())
        self.assertEqual(s.logins, [OLD])
        frm, raw = s.sent[0]
        self.assertEqual(frm, OLD)
        import email
        m = email.message_from_string(raw)
        html = [p for p in m.walk() if p.get_content_type() == "text/html"][0].get_payload(decode=True).decode()
        self.assertLess(html.index("safe sender"), html.index("Best regards"))
        txt = [p for p in m.walk() if p.get_content_type() == "text/plain"][0].get_payload(decode=True).decode()
        self.assertIn("safe sender", txt)
        att = [p for p in m.walk() if p.get_filename()][0]
        self.assertEqual(att.get_payload(decode=True), b"\x00\x01binary-data\xff")     # attachment untouched

    def test_noreply_mode_changes_login_from_and_reply_to_without_note(self):
        mi.set_mode("noreply")
        s = self.send(html_msg())
        self.assertEqual(s.logins, [NEW])
        frm, raw = s.sent[0]
        self.assertEqual(frm, NEW)
        self.assertIn(NEW, raw.split("\n\n")[0])
        self.assertIn("Reply-To: " + OLD, raw)
        self.assertNotIn("safe sender", raw)

    def test_raw_sendmail_path_same_result(self):
        mi.set_mode("noreply")
        s = self.send(html_msg(), raw=True)
        self.assertEqual(s.logins, [NEW])
        self.assertEqual(s.sent[0][0], NEW)
        self.assertIn("Reply-To: " + OLD, s.sent[0][1])

    def test_noreply_password_missing_falls_back_to_old_sender(self):
        mi.set_mode("noreply")
        with mock.patch.object(mi, "_env", return_value=""):
            s = self.send(html_msg())
        self.assertEqual(s.logins, [OLD])
        self.assertEqual(s.sent[0][0], OLD)
        self.assertNotIn("safe sender", s.sent[0][1])

    def test_noreply_login_refused_falls_back_and_mail_is_still_sent(self):
        mi.set_mode("noreply")
        FakeSMTP.refuse_noreply = True
        s = self.send(html_msg())
        self.assertEqual(s.logins, [OLD])
        self.assertEqual(len(s.sent), 1)
        self.assertEqual(s.sent[0][0], OLD)

    def test_other_senders_never_touched(self):
        for mode in ("kartavya", "noreply"):
            mi.set_mode(mode)
            m = html_msg(sender="anand.singh@mastertrust.co.in")
            before = m.as_string()
            s = self.send(m, login="anand.singh@mastertrust.co.in")
            self.assertEqual(s.logins, ["anand.singh@mastertrust.co.in"])
            self.assertEqual(s.sent[0][1], before)

    def test_email_message_object(self):
        mi.set_mode("kartavya")
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = OLD, "a@b.c", "x"
        m.set_content("Hi\n\nThanks,\nK")
        m.add_alternative(HTML, subtype="html")
        s = self.send(m)
        import email
        parsed = email.message_from_string(s.sent[0][1])
        html = [p for p in parsed.walk() if p.get_content_type() == "text/html"][0].get_payload(decode=True).decode()
        self.assertIn("safe sender", html)

    def test_saved_password_and_test_mail(self):
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(mi, "SECRET_FILE", d / "s.json"), mock.patch.object(mi, "_env", lambda n: "kpw" if n == "SENDER_PASSWORD" else ""):
            self.assertFalse(mi.describe()["noreply_ready"])
            with self.assertRaises(ValueError):
                mi.set_password("  ")
            self.assertTrue(mi.set_password("secret")["noreply_ready"])
            self.assertNotIn("secret", str(mi.describe()))                       # never sent back to the browser
            r = mi.send_test("me@x.y", as_noreply=True)                          # direct noreply login
            self.assertTrue(r["ok"] and r["sent_as"] == NEW)
            self.assertEqual(FakeSMTP.log[-1].logins, [NEW])
            mi.set_mode("kartavya")
            r = mi.send_test("me@x.y")                                           # follows the mode: Kartavya + note
            self.assertTrue(r["ok"] and r["note_added"] and r["sent_as"] == OLD)
            mi.set_mode("noreply")
            r = mi.send_test("me@x.y")
            self.assertTrue(r["ok"] and r["sent_as"] == NEW and not r["note_added"])

    def test_standard_signature_replaces_short_signoff_with_phone_before_system_line(self):
        mi.set_mode("kartavya")
        s = self.send(html_msg())
        import email
        m = email.message_from_string(s.sent[0][1])
        html = [p for p in m.walk() if p.get_content_type() == "text/html"][0].get_payload(decode=True).decode()
        self.assertNotIn("Kartavya Dhillon", html)                                   # old sign-off replaced
        for a, b in (("safe sender", "Best regards"), ("Best regards", "RMS Automated Reports"),
                     ("RMS Automated Reports", "011-66331000 | Ext. 84"), ("011-66331000 | Ext. 84", "This is a system-generated mail.")):
            self.assertLess(html.index(a), html.index(b), (a, b))
        self.assertEqual(html.count("RMS Automated Reports"), 1)
        txt = [p for p in m.walk() if p.get_content_type() == "text/plain"][0].get_payload(decode=True).decode()
        self.assertLess(txt.index("011-66331000"), txt.index("system-generated"))

    def test_long_tail_after_signoff_is_kept(self):
        mi.set_mode("kartavya")
        html = "<html><body><p>Hi</p><br>Thanks,<br>" + ("Important detail line. " * 30) + "</body></html>"
        out = mi.put_note_html(html, note=True, sig=True)
        self.assertIn("Important detail line.", out)
        self.assertNotIn("RMS Automated Reports", out)
        self.assertLess(out.index("safe sender"), out.index("Thanks"))

    def test_signature_in_noreply_mode_and_can_be_turned_off(self):
        mi.set_mode("noreply")
        raw = self.send(html_msg()).sent[0][1]
        import email
        m = email.message_from_string(raw)
        html = [p for p in m.walk() if p.get_content_type() == "text/html"][0].get_payload(decode=True).decode()
        self.assertIn("RMS Automated Reports", html)
        self.assertNotIn("safe sender", html)
        self.conf.write_text('{"mode": "noreply", "signature": false}', encoding="utf-8")
        html2 = [p for p in email.message_from_string(self.send(html_msg()).sent[0][1]).walk() if p.get_content_type() == "text/html"][0].get_payload(decode=True).decode()
        self.assertIn("Kartavya Dhillon", html2)                                    # original sign-off untouched

    def test_sandeep_gets_the_same_treatment_and_replies_go_to_him(self):
        sand = "Sandeep.Kumar2@mastertrust.co.in"                                    # the scripts write it with capitals
        mi.set_mode("kartavya")
        raw = self.send(html_msg(sender=sand), login=sand).sent[0][1]
        import email
        html = [p for p in email.message_from_string(raw).walk() if p.get_content_type() == "text/html"][0].get_payload(decode=True).decode()
        self.assertIn("safe sender", html)
        self.assertIn("RMS Automated Reports", html)
        mi.set_mode("noreply")
        s = self.send(html_msg(sender=sand), login=sand)
        self.assertEqual(s.logins, [NEW])
        self.assertEqual(s.sent[0][0], NEW)
        self.assertIn("Reply-To: " + sand, s.sent[0][1])                             # not Kartavya
        s2 = self.send(html_msg(), login=OLD)
        self.assertIn("Reply-To: " + OLD, s2.sent[0][1])

    def test_scheduler_style_send_with_explicit_from_addr(self):
        """core/scheduler/helpers.py and darwin_lib.py call send_message(msg, from_addr=<login>, to_addrs=[...]) after login(); same result."""
        mi.set_mode("noreply")
        m = html_msg()
        with smtplib.SMTP("h", 587) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(OLD, "x")
            s.send_message(m, from_addr=OLD, to_addrs=["a@b.c", "c@d.e"])
        sm = FakeSMTP.log[-1]
        self.assertEqual(sm.logins, [NEW])
        self.assertEqual(sm.sent[0][0], NEW)
        self.assertIn("From: Kartavya <" + NEW + ">", sm.sent[0][1])

    def test_keep_sender_leaves_the_connection_untouched(self):
        sand = "sandeep.kumar2@mastertrust.co.in"
        for mode in ("kartavya", "noreply"):
            mi.set_mode(mode)
            m = html_msg(sender=sand)
            before = m.as_string()
            with smtplib.SMTP("h", 587) as s:
                mi.keep_sender(s)
                s.login(sand, "x")
                s.send_message(m, from_addr=sand, to_addrs=["a@b.c"])
            sm = FakeSMTP.log[-1]
            self.assertEqual(sm.logins, [sand])
            self.assertEqual(sm.sent[0][0], sand)
            self.assertEqual(sm.sent[0][1], before)                                  # no note, no signature, no Reply-To

    def test_install_twice_wraps_once(self):
        before = smtplib.SMTP
        mi.install()
        self.assertIs(smtplib.SMTP, before)


if __name__ == "__main__":
    unittest.main()
