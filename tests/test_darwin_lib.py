"""Tests for the Darwinbox exit-clearance mailer (darwin_lib) and its scheduler process. Nothing real is contacted.

    python -m unittest tests.test_darwin_lib -v
"""
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

try:
    from core.scheduler import darwin_lib as L
    HAVE = True
except Exception:                                   # json5 / bs4 / cryptography not installed
    HAVE = False

ROW = ('{"Employee": "<dbx-hover-data data-id=\\"%s\\">%s</dbx-hover-data><span class=\\"label-two\\">%s</span>", '
       '"Designation": "Exec", "LWD": "01-11-2026", "Location": "Delhi, Delhi, India, (Delhi)", '
       '"meta": {"action_id": "a1_s1_t%s", "category": "separation_work_task"}}')


def page(rows):
    body = ",".join(ROW % (e, n, e, e) for e, n in rows)
    return ('<html><script>var x = {tableOptions: {options: {initialTableData: [' + body + '], '
            'name: "core_taskbox_tasks"}}};</script></html>').replace("var x = {tableOptions", "core_taskbox_tasks; var tableOptions") \
        if False else ('<html><script>"core_taskbox_tasks"; tableOptions = {options: {initialTableData: [' + body + ']}};</script></html>')


@unittest.skipUnless(HAVE, "darwin_lib dependencies missing")
class Lib(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.keep = {k: getattr(L, k) for k in ("HERE", "STATE_FILE", "EXCEL_FILE", "LOCK_FILE", "LOG_FILE", "COOKIE_FILE",
                                                "ACT_PREVIEW_JSON", "ACT_PREVIEW_XLSX", "ACT_BLOCKED_FILE")}
        L.HERE = self.tmp
        L.STATE_FILE, L.EXCEL_FILE = self.tmp / "s.json", self.tmp / "e.xlsx"
        L.LOCK_FILE, L.LOG_FILE, L.COOKIE_FILE = self.tmp / "l.lock", self.tmp / "l.log", self.tmp / "c.json"
        L.ACT_PREVIEW_JSON, L.ACT_PREVIEW_XLSX, L.ACT_BLOCKED_FILE = self.tmp / "p.json", self.tmp / "p.xlsx", self.tmp / "b.txt"
        L._reset_stats()

    def tearDown(self):
        for k, v in self.keep.items():
            setattr(L, k, v)

    # ---- safety ---------------------------------------------------------------------------------
    def test_no_secret_is_in_the_code(self):
        src = Path(L.__file__).read_text(encoding="utf-8")
        for bad in ("Master@123", "$Sandeep24", "eyJhbGci", "M0213669"):
            self.assertNotIn(bad, src)

    def test_missing_memory_file_refuses_to_run(self):
        with mock.patch.dict(os.environ, {"DARWIN_ALLOW_EMPTY_STATE": ""}):
            with self.assertRaises(L.AppError):
                L.load_state()
        with mock.patch.dict(os.environ, {"DARWIN_ALLOW_EMPTY_STATE": "1"}):
            self.assertEqual(L.load_state()["sent"], {})

    def test_bundled_memory_file_is_valid(self):
        p = Path(__file__).resolve().parents[1] / "data" / "darwinbox_exit" / "darwinbox_sent_records.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            self.assertIn("sent", d)
            self.assertIn("batches", d)

    # ---- parsing / checks --------------------------------------------------------------------------
    def test_parse_replies(self):
        self.assertEqual(L.parse_manohar("NA"), ([], ""))
        codes, prob = L.parse_manohar("AB1234, XY99Z1")
        self.assertEqual(codes, ["AB1234", "XY99Z1"])
        pairs, zero, prob = L.parse_jagesh("AB1234 - 15,000")
        self.assertEqual(pairs, [("AB1234", Decimal(15000))])
        self.assertEqual(L.parse_jagesh("Nil")[1], True)

    def test_shortfall_rule(self):
        self.assertEqual(L.shortfall_of(Decimal("-16.71")), Decimal("16.71"))
        self.assertEqual(L.shortfall_of(Decimal("25.93")), Decimal(0))

    def test_plan_blocks_on_a_mismatch(self):
        cand = {"key": "M1", "v": {"name": "A"}, "cells": {"Manohar": "AB1234", "Jagesh": "AB1234 - 500", "Anuj": "none"}}
        risk = {"AB1234": [{"shortfall": "-100", "comp": "", "name": "x"}]}
        plan = L.make_plan(cand, risk)
        self.assertEqual(plan["status"], "BLOCKED")
        risk["AB1234"][0]["shortfall"] = "-500"
        self.assertEqual(L.make_plan(cand, risk)["status"], "READY")

    # ---- the no-resend guarantee ---------------------------------------------------------------------
    class Client:
        def __init__(self, rows):
            self.rows = rows

        def get_tasks_html(self, fresh=False):
            return page(self.rows)

    def _run(self, rows):
        sent = []
        with mock.patch.object(L, "send_with_retry", lambda msg, recipients=None: sent.append(msg)), \
                mock.patch.object(L, "enrich", lambda c, recs: [r.update(_ok=True) for r in recs]), \
                mock.patch.object(L, "collect_replies", lambda st: False), \
                mock.patch.object(L, "act_step_safe", lambda *a, **k: 0), \
                mock.patch.object(L, "reminder_step_safe", lambda *a, **k: None), \
                mock.patch.object(L, "alert_step_safe", lambda *a, **k: None), \
                mock.patch.object(L, "write_excel", lambda st: None):
            L._reset_stats()
            rc = L.run(self.Client(rows))
        return rc, sent

    def test_an_employee_is_never_e_mailed_twice(self):
        os.environ["DARWIN_ALLOW_EMPTY_STATE"] = "1"
        try:
            rc, sent = self._run([("M0001", "Asha One")])
            self.assertEqual((rc, len(sent), L.STATS["emailed"]), (0, 1, 1))
            rc, sent = self._run([("M0001", "Asha One")])                 # same Task Box again (e.g. the 15:00 run)
            self.assertEqual(len(sent), 0)
            rc, sent = self._run([("M0001", "Asha One"), ("M0002", "Ben Two")])
            self.assertEqual(len(sent), 1)                                # only the NEW one
            st = json.loads(L.STATE_FILE.read_text(encoding="utf-8"))
            self.assertEqual(set(st["sent"]), {"M0001", "M0002"})
        finally:
            os.environ.pop("DARWIN_ALLOW_EMPTY_STATE", None)

    def test_a_failed_send_is_not_remembered(self):
        os.environ["DARWIN_ALLOW_EMPTY_STATE"] = "1"
        try:
            def boom(msg, recipients=None):
                raise L.AppError("SMTP login was rejected")
            with mock.patch.object(L, "send_with_retry", boom), \
                    mock.patch.object(L, "enrich", lambda c, recs: [r.update(_ok=True) for r in recs]):
                L.run(self.Client([("M0009", "Fail Case")]))
            self.assertFalse(L.STATE_FILE.exists() and "M0009" in json.loads(L.STATE_FILE.read_text())["sent"])
        finally:
            os.environ.pop("DARWIN_ALLOW_EMPTY_STATE", None)

    def test_configure_fills_everything(self):
        keep = (L.USERNAME, L.TO, L.CC, L.WA_TO, L.ACT_SUBMIT, L.FROM_ADDR)
        try:
            L.configure({"darwin_user": "U1", "darwin_password": "P1", "smtp_user": "a@x.in", "smtp_password": "p",
                         "to": ["t@x.in"], "cc": [], "overdue_phones": ["+911"], "act_submit": False})
            self.assertEqual((L.USERNAME, L.TO, L.CC, L.WA_TO, L.ACT_SUBMIT, L.FROM_ADDR),
                             ("U1", ["t@x.in"], [], ["+911"], False, "a@x.in"))
            self.assertEqual(L.ACT_ALERT_TO, ["a@x.in"])
        finally:
            L.USERNAME, L.TO, L.CC, L.WA_TO, L.ACT_SUBMIT, L.FROM_ADDR = keep


@unittest.skipUnless(HAVE, "darwin_lib dependencies missing")
class ProcessWrapper(unittest.TestCase):
    def test_registered_and_outcomes(self):
        from core.scheduler import catalog
        from core.scheduler.processes import darwinbox_exit_clearance as P
        catalog.load_builtin()
        d = catalog.get("darwinbox_exit_clearance")
        self.assertEqual(d.name, "DARWINBOX EXIT CLEARANCE")
        self.assertEqual(d.folder, d.name)
        self.assertIn("darwinbox", d.uses)

        class Ctx:
            cancelled = False
            def log(self, m): pass
            def text(self): return ""

        env = {"SCHED_DARWINBOX_USERNAME": "u", "SCHED_DARWINBOX_PASSWORD": "p", "SCHED_SMTPB_EMAIL": "a@x.in",
               "SCHED_SMTPB_PASSWORD": "p", "SCHED_POP3B_USERNAME": "a@x.in", "SCHED_POP3B_PASSWORD": "p",
               "SCHED_DATA_ROOT": tempfile.mkdtemp()}
        with mock.patch.dict(os.environ, env):
            def go(res):
                with mock.patch.object(P.L, "run_once", lambda: res):
                    return P._run.__wrapped__({}, Ctx()) if hasattr(P._run, "__wrapped__") else P._run({}, Ctx())
            idle = {"rc": 0, "stats": {"emailed": 0, "replies": 0, "submitted": 0, "reminders": 0, "alerts": 0, "blocked": False}}
            with self.assertRaises(P.H.Skipped):
                go(idle)
            busy = {"rc": 0, "stats": dict(idle["stats"], emailed=2, submitted=1)}
            self.assertIn("2 employee(s) e-mailed", go(busy))
            with self.assertRaises(RuntimeError):
                go({"rc": 3, "stats": dict(idle["stats"], blocked=True)})
            with self.assertRaises(RuntimeError):
                go({"rc": 1, "stats": dict(idle["stats"])})


if __name__ == "__main__":
    unittest.main()
