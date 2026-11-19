"""PAN rule of the Darwinbox exit clearance: no PAN -> HR-only mail, nothing to anybody else."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DARWIN_DATA_DIR", tempfile.mkdtemp())
from core.scheduler import darwin_lib as L  # noqa: E402


def rec(key="K1", name="Test Emp", emp="M0001", pan=""):
    r = {c: "" for c in L.COLUMNS}
    r.update({"Name": name, "Emp ID": emp, "Pan No.": pan, "_key": key, "_ok": True})
    return r


class PanRule(unittest.TestCase):
    def setUp(self):
        L.FROM_ADDR = "sender@x.in"
        L._reset_stats()

    def test_pan_ok(self):
        self.assertTrue(L.pan_ok("CBNPD5401L"))
        self.assertTrue(L.pan_ok("cbnpd5401l "))
        self.assertTrue(L.pan_ok("XXXXXX401L"))          # masked by MASK_PAN
        for bad in ("", None, "NA", "-", "ABC", "CBNPD54011"):
            self.assertFalse(L.pan_ok(bad), bad)

    def test_hr_only_mail(self):
        m = L.build_pan_message([rec()])
        self.assertEqual(m["To"], ", ".join(L.HR_TO))
        self.assertIsNone(m["Cc"])
        self.assertIn("PAN number is required", m.get_body(("plain",)).get_content())

    def test_hold_sends_to_hr_only_then_waits(self):
        state = {"sent": {}, "batches": []}
        with mock.patch.object(L, "send_with_retry") as snd, mock.patch.object(L, "save_state"):
            L.hold_for_pan(state, [rec()], dry_run=False)
            self.assertEqual(snd.call_args.kwargs["recipients"], L.HR_TO)
            self.assertEqual(snd.call_count, 1)
            L.hold_for_pan(state, [rec()], dry_run=False)         # same day: HR is not mailed again
            self.assertEqual(snd.call_count, 1)
            state["pan_wait"]["K1"]["last_mail"] = "2000-01-01 00:00:00"
            L.hold_for_pan(state, [rec()], dry_run=False)         # a day later: HR reminder, still HR only
            self.assertEqual(snd.call_count, 2)
            self.assertEqual(snd.call_args.kwargs["recipients"], L.HR_TO)

    def test_dry_run_sends_nothing(self):
        with mock.patch.object(L, "send_with_retry") as snd:
            L.hold_for_pan({"sent": {}, "batches": []}, [rec()], dry_run=True)
            snd.assert_not_called()

    def test_no_reminders_when_all_open_employees_lack_pan(self):
        st = {"sent": {"a": {"record": {"Pan No.": ""}}, "b": {"record": {"Pan No.": "CBNPD5401L"}}},
              "batches": [{"keys": ["a"], "sent_at": "2000-01-01 10:00:00", "replies": {}},
                          {"keys": ["a", "b"], "sent_at": "2000-01-01 10:00:00", "replies": {}}]}
        self.assertTrue(L._batch_pan_missing(st, st["batches"][0]))
        self.assertFalse(L._batch_pan_missing(st, st["batches"][1]))


if __name__ == "__main__":
    unittest.main()


class ContactFromForm(unittest.TestCase):
    QS = [{"question_type": "system-attributes", "config": {"question": "PAN", "field": "bank_pan_num"}},
          {"question_type": "system-attributes", "config": {"question": "Contact Number", "field": "personal_mobile_x"}},
          {"question_type": "choices", "config": {"question": "Removal of mobile number & email from Master Dealer"}}]

    def test_fields_asked_from_the_form(self):
        self.assertEqual([f for f, _ in L.form_attr_fields(self.QS)], ["bank_pan_num", "personal_mobile_x"])

    def test_contact_number_read_when_the_form_has_it(self):
        reply = {"fields_value": {"bank_pan_num": "CBNPD5401L", "personal_mobile_x": "8143175133"}}
        self.assertEqual(L.mobile_from_form(self.QS, reply), "8143175133")

    def test_old_form_without_the_question_gives_nothing(self):
        old = self.QS[:1] + self.QS[2:]
        self.assertEqual(L.mobile_from_form(old, {"fields_value": {"bank_pan_num": "CBNPD5401L"}}), "")


class MobileAskHr(unittest.TestCase):
    def setUp(self):
        L.FROM_ADDR = "sender@x.in"

    def test_hr_asked_only_when_mobile_missing(self):
        a, b = rec("A", "Has Mobile", "M1", "CBNPD5401L"), rec("B", "No Mobile", "M2", "CBNPD5401L")
        a["Mobile No."] = "8143175133"
        state = {"sent": {}, "batches": []}
        with mock.patch.object(L, "send_with_retry") as snd, mock.patch.object(L, "save_state"):
            L.ask_hr_for_mobile(state, [a, b], dry_run=False)
            self.assertEqual(snd.call_count, 1)
            self.assertEqual(snd.call_args.kwargs["recipients"], L.HR_TO)
            self.assertNotIn("Has Mobile", snd.call_args.args[0].get_body(("plain",)).get_content())
            L.ask_hr_for_mobile(state, [a, b], dry_run=False)         # once per employee
            self.assertEqual(snd.call_count, 1)

    def test_nothing_sent_when_all_have_mobile(self):
        a = rec("A", "Has Mobile", "M1", "CBNPD5401L")
        a["Mobile No."] = "8143175133"
        with mock.patch.object(L, "send_with_retry") as snd:
            L.ask_hr_for_mobile({"sent": {}, "batches": []}, [a], dry_run=False)
            snd.assert_not_called()

    def test_hr_stays_in_cc_of_the_normal_mail(self):
        self.assertIn("hrd@mastertrust.co.in", L.CC)


class FormFieldLog(unittest.TestCase):
    def test_log_has_no_values_and_shows_empty_fields(self):
        import json
        qs = ContactFromForm.QS
        reply = {"fields_value": {"bank_pan_num": "CBNPD5401L", "personal_mobile_x": ""},
                 "add_variables_value": {"primary_mobile": "", "firstname": "A"}}
        L.form_field_report({"Emp ID": "M777", "Name": "T"}, qs, reply, "test")
        data = json.loads((L.OUT_DIR / "Darwin_Form_Fields_M777.json").read_text(encoding="utf-8"))
        self.assertNotIn("CBNPD5401L", json.dumps(data))
        labels = {q["label"]: q["has_value"] for q in data["questions"] if q["profile_field"]}
        self.assertEqual(labels, {"PAN": True, "Contact Number": False})
        self.assertEqual(data["profile_variables"]["primary_mobile"], False)
