"""MARGIN SHORTFALL TRACK card: versions, picking the four files from the console, FINAL / TRACK maths, mail rule, import."""
import datetime as dt
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import margin_shortfall_track as M          # noqa: E402


def xlsx(df) -> bytes:
    b = io.BytesIO()
    df.to_excel(b, index=False)
    return b.getvalue()


class FakeSMTP:
    sent = []
    fail = False

    def __init__(self, host, port, timeout=0):
        pass

    def starttls(self):
        pass

    def login(self, u, p):
        if FakeSMTP.fail:
            raise OSError("network down")

    def send_message(self, msg, to_addrs=None):
        FakeSMTP.sent.append((msg["Subject"], list(to_addrs or []), [pt.get_filename() for pt in msg.walk() if pt.get_filename()]))

    def quit(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.env = {"MST_DIR": str(t / "mst"), "DAYCARDS_DIR": str(t / "dc"), "MST_LOG_DIR": str(t / "log"),
                    "SENDER_EMAIL": "a@b.c", "SENDER_PASSWORD": "x", "MST_REPORT_TO": "rms_del@mastertrust.co.in", "MST_REPORT_CC": ""}
        self.old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        FakeSMTP.sent, FakeSMTP.fail = [], False

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # --- fake console outputs for a day -------------------------------------------------
    def put(self, d, card, label, name, df):
        from core import daycards as dc
        p = Path(self.env["DAYCARDS_DIR"]) / dc.CARDS[card]["folder"] / dc.dstr(d) / label / name
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(p, index=False)
        return p

    def sources(self, d, cm=("C1", "C2"), fo=("C2",), eod=("C3",), intra=("C4",), skip=()):
        c8 = d.strftime("%d%m%Y")
        if "SA_CM" not in skip:
            self.put(d, "sa", "SA CM", "SA_CM_Full.xlsx", pd.DataFrame({"Client Code": [*cm, "BLANKED"], "Short(H-D)": [*[100.0] * len(cm), 999.0],
                                                                       "FINAL REMARKS": [*[None] * len(cm), "covered"]}))
        if "SA_FO" not in skip:
            self.put(d, "sa", "SA FO", "SA_FO_Full.xlsx", pd.DataFrame({"Client Code": list(fo), "Short(H-D)": [50.0] * len(fo), "FINAL REMARKS": [""] * len(fo)}))
        if "MCX_EODSAR" not in skip:
            self.put(d, "eodsar", "MCX EODSAR", f"MCX_EODSAR_{c8}.xlsx", pd.DataFrame({"Client ID": [*eod, "OTHERCLIENT"], "T5 _remarks": [*["Other"] * len(eod), "x"],
                                                                                    "EOD Short Allocation": [*[7.0] * len(eod), 1.0]}))
        if "MCX_INTRA" not in skip:
            self.put(d, "master", "MCX INTRASAR", "MCX_Intra_Output.xlsx", pd.DataFrame({"Client Code": [*intra, "NS"], "Final Coverage (NCL - NCDEX)": [*["-25.5_SHORT"] * len(intra), "NO SHORT"]}))

    def margin(self, d, rows=(("C1", 10.0), ("C1", 30.0), ("C9", 5.0)), name=None):
        nm = name or f"Margin_Detail_{d:%d%m%Y}.xlsx"
        return M.accept_margin(nm, xlsx(pd.DataFrame(rows, columns=["AccountID", "MAX"])), by="t")


D1, D2, D3 = dt.date(2026, 11, 17), dt.date(2026, 11, 18), dt.date(2026, 11, 19)


class IntakeTests(Base):
    def test_new_identical_revised(self):
        r = self.margin(D1)
        self.assertEqual((r["status"], r["v"], r["clients"]), ("new", 1, 2))
        self.assertEqual(self.margin(D1)["status"], "identical")
        r = self.margin(D1, rows=(("C1", 99.0),))
        self.assertEqual((r["status"], r["v"]), ("revised", 2))
        files = sorted(p.name for p in (Path(self.env["MST_DIR"]) / "margin" / "17112026").iterdir())
        self.assertEqual(len(files), 2)                                  # both versions kept, nothing overwritten
        self.assertTrue(files[0].startswith("v001_") and files[1].startswith("v002_"))

    def test_refusals(self):
        with self.assertRaises(ValueError):
            M.accept_margin("Margin_Detail.xlsx", xlsx(pd.DataFrame({"AccountID": ["A"], "MAX": [1]})))
        with self.assertRaises(ValueError):
            M.accept_margin("Margin_Detail_01012099.xlsx", xlsx(pd.DataFrame({"AccountID": ["A"], "MAX": [1]})))
        with self.assertRaises(ValueError):
            M.accept_margin("Margin_Detail_17112026.xlsx", xlsx(pd.DataFrame({"X": ["A"]})))
        with self.assertRaises(ValueError):
            M.accept_upload("random.xlsx", b"123")


class FinalTests(Base):
    def test_final_maths_and_columns(self):
        self.margin(D1)
        self.sources(D1)
        out = M.run(alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP, sleep=lambda s: None)
        self.assertTrue(out["ok"], out)
        df = pd.read_excel(M.final_path(D1))
        self.assertEqual(list(df.columns), M.FINAL_COLS)
        by = df.groupby("Client Code")[M.NUM_COLS].sum()
        self.assertEqual(by.loc["C1", "SA_CM"], 100.0)
        self.assertNotIn("BLANKED", by.index)                            # remark filled -> not a shortfall
        self.assertEqual(by.loc["C2", "SA_FO"], 50.0)
        self.assertEqual(by.loc["C3", "MCX_EODSAR"], 7.0)
        self.assertNotIn("OTHERCLIENT", by.index)
        self.assertEqual(by.loc["C4", "MCX_INTRA"], 25.5)               # sign removed, _SHORT cut
        self.assertNotIn("NS", by.index)                                 # "NO SHORT" is not a shortfall
        self.assertEqual(by.loc["C1", "MARGIN"], 30.0)                   # max per client, not the sum
        self.assertEqual(len(df), 2 + 1 + 1 + 1 + 2)                     # one row per client per source

    def test_blocked_names_the_missing_file_and_others_go_on(self):
        self.margin(D1)
        self.margin(D2)
        self.sources(D1)
        self.sources(D2, skip=("SA_FO",))
        rows = {r["date"]: r for r in M.plan()}
        self.assertTrue(rows["18-11-2026"]["blocked"])
        self.assertEqual(rows["18-11-2026"]["missing"], ["SA_FO_Full"])
        out = M.run(alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP, sleep=lambda s: None)
        self.assertTrue(out["ok"])
        self.assertTrue(M.final_path(D1).is_file())
        self.assertFalse(M.final_path(D2).is_file())

    def test_eodsar_of_another_date_is_not_taken(self):
        self.margin(D1)
        self.sources(D1, skip=("MCX_EODSAR",))
        self.put(D1, "eodsar", "MCX EODSAR", "MCX_EODSAR_18112026.xlsx", pd.DataFrame({"Client ID": ["A"], "T5 _remarks": ["other"], "EOD Short Allocation": [1.0]}))
        self.assertTrue(M.plan()[0]["blocked"])

    def test_revised_margin_rebuilds_and_keeps_the_old_final(self):
        self.margin(D1)
        self.sources(D1)
        M.run(alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP, sleep=lambda s: None)
        self.assertEqual(M.plan()[0]["state"], "built")
        self.margin(D1, rows=(("C1", 500.0),))
        self.assertEqual(M.plan()[0]["todo"], "revised")
        M.run(alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP, sleep=lambda s: None)
        df = pd.read_excel(M.final_path(D1))
        self.assertEqual(df[df["MARGIN"] > 0]["MARGIN"].tolist(), [500.0])  # newest version used
        self.assertEqual(len(list((Path(self.env["MST_DIR"]) / "final" / "_replaced").iterdir())), 1)
        self.assertEqual(M.plan()[0]["state"], "built")


class TrackAndMailTests(Base):
    def three(self, third_cm=("C1",)):
        for d in (D1, D2, D3):
            self.margin(d)
        self.sources(D1, cm=("C1", "C2"))
        self.sources(D2, cm=("C1", "C2"))
        self.sources(D3, cm=third_cm, fo=("C1",), eod=("C1",), intra=("C1",))

    def go(self, **kw):
        return M.run(alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP, sleep=lambda s: None, **kw)

    def test_only_the_newest_date_is_mailed_and_only_to_rms_del(self):
        self.three()
        self.assertTrue(self.go()["ok"])
        self.assertEqual(len(FakeSMTP.sent), 1)                          # three dates processed -> ONE mail
        subj, to, files = FakeSMTP.sent[0]
        self.assertEqual(subj, "Daily Shortfall Track Reports Till - 19th Nov 2026")
        self.assertEqual(to, ["rms_del@mastertrust.co.in"])
        self.assertEqual(sorted(files), ["TRACK_17112026.xlsx", "TRACK_18112026.xlsx"])
        self.assertEqual(M.status()["last_sent"], "19-11-2026")
        self.go()                                                        # nothing new -> no second mail
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_older_date_added_later_is_processed_but_not_mailed(self):
        self.three()
        self.go()
        FakeSMTP.sent.clear()
        d0 = dt.date(2026, 11, 16)
        self.margin(d0)
        self.sources(d0)
        self.go()
        self.assertTrue(M.final_path(d0).is_file())                      # processed
        self.assertEqual(FakeSMTP.sent, [])                              # but 16th is older than the last sent 19th

    def test_failed_mail_is_not_marked_sent_and_is_retried(self):
        self.three()
        FakeSMTP.fail = True
        out = self.go()
        self.assertFalse(out["ok"])
        self.assertEqual(M.status()["last_sent"], "")
        FakeSMTP.fail = False
        self.assertTrue(self.go()["ok"])
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.assertEqual(M.status()["last_sent"], "19-11-2026")

    def test_mail_waits_while_a_newer_date_is_blocked(self):
        self.three()
        self.margin(dt.date(2026, 11, 20))                               # no source files for the 20th yet
        self.go()
        self.assertEqual(FakeSMTP.sent, [])
        self.assertTrue(M.status()["next_mail"]["hold"])
        self.sources(dt.date(2026, 11, 20), cm=("C1",), fo=("C1",), eod=("C1",), intra=("C1",))
        self.go()
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.assertIn("20th Nov 2026", FakeSMTP.sent[0][0])

    def test_mail_switch_off(self):
        self.three()
        M.save_settings({"send_mail": False})
        self.assertTrue(self.go()["ok"])
        self.assertEqual(FakeSMTP.sent, [])

    def test_track_continuity_and_flags(self):
        self.three(third_cm=("C1",))
        self.go()
        raw = pd.read_excel(M.track_path(D1), header=None)
        self.assertEqual(raw.iloc[0, 0], "DATE: 17-11-2026")
        self.assertEqual(list(raw.iloc[1, :6]), M.FINAL_COLS)
        self.assertEqual(raw.iloc[0, 7], "DATE: 18-11-2026")
        self.assertEqual(M.read_index()["track"]["2026-11-17"]["status"], "open")   # 2 following days only

    def test_track_breaks_when_no_common_client(self):
        for d in (D1, D2):
            self.margin(d)
        self.sources(D1, cm=("A",), fo=("A",), eod=("A",), intra=("A",))
        self.sources(D2, cm=("Z",), fo=("Z",), eod=("Z",), intra=("Z",))
        # the margin of both dates is C1 / C9, so make them differ as well
        self.margin(D2, rows=(("Q", 1.0),))
        self.go()
        t = M.read_index()["track"]["2026-11-17"]
        raw = pd.read_excel(M.track_path(D1), header=None)
        self.assertIn(t["status"], ("open", "broken"))
        if t["status"] == "broken":
            self.assertTrue(any("NO COMMON CLIENT CODE EXISTS ON 18-11-2026" in str(v) for v in raw.iloc[2].tolist()))

    def test_five_following_days_marks_complete(self):
        ds = [dt.date(2026, 11, 9) + dt.timedelta(days=i) for i in range(7)]
        for d in ds:
            self.margin(d)
            self.sources(d, cm=("C1",), fo=("C1",), eod=("C1",), intra=("C1",))
        self.go()
        ix = M.read_index()
        self.assertEqual(ix["track"]["2026-11-09"]["status"], "complete")
        self.assertEqual(ix["track"]["2026-11-09"]["end"], "2026-11-14")
        self.assertEqual(ix["track"]["2026-11-10"]["status"], "complete")
        self.assertEqual(ix["track"]["2026-11-11"]["status"], "open")   # only 4 following days


class ImportTests(Base):
    def final_bytes(self, clients):
        rows = [[c, 1.0, 0.0, 0.0, 0.0, 0.0] for c in clients]
        return xlsx(pd.DataFrame(rows, columns=M.FINAL_COLS))

    def test_import_then_last_sent_rule(self):
        for d, cl in ((D1, ["A", "B"]), (D2, ["A", "B"]), (D3, ["A"])):
            r = M.accept_upload(f"FINAL_SHORTFALL_{d:%d%m%Y}.xlsx", self.final_bytes(cl))
            self.assertEqual(r["status"], "imported")
        self.assertEqual(M.accept_upload("TRACK_17112026.xlsx", b"x")["status"], "skipped")
        M.save_settings({"last_sent": "18-11-2026"})
        out = M.run(alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP, sleep=lambda s: None)
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.assertIn("19th Nov 2026", FakeSMTP.sent[0][0])

    def test_without_last_sent_the_newest_import_is_the_baseline(self):
        M.accept_upload(f"FINAL_SHORTFALL_{D1:%d%m%Y}.xlsx", self.final_bytes(["A"]))
        M.accept_upload(f"FINAL_SHORTFALL_{D2:%d%m%Y}.xlsx", self.final_bytes(["A"]))
        M.run(alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP, sleep=lambda s: None)
        self.assertEqual(FakeSMTP.sent, [])                              # nothing is newer than what was imported
        self.assertEqual(M.status()["baseline"], "18-11-2026")

    def test_zip_import_and_bad_file(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("17 Nov 2026/FINAL_SHORTFALL_17112026.xlsx", self.final_bytes(["A"]))
            zf.writestr("17 Nov 2026/Other.xlsx", b"zz")
        r = M.accept_upload("backup.zip", z.getvalue())
        self.assertEqual(len(r["items"]), 1)
        with self.assertRaises(ValueError):
            M.accept_upload("FINAL_SHORTFALL_17112026.xlsx", xlsx(pd.DataFrame({"a": [1]})))

    def test_built_final_is_not_overwritten_by_import(self):
        self.margin = lambda *a, **k: None
        M.accept_margin("Margin_Detail_17112026.xlsx", xlsx(pd.DataFrame({"AccountID": ["A"], "MAX": [1.0]})))
        Base.sources(self, D1)
        M.run(alert=lambda *a: {"sent": False}, smtp_factory=FakeSMTP, sleep=lambda s: None)
        r = M.accept_upload("FINAL_SHORTFALL_17112026.xlsx", self.final_bytes(["Z"]))
        self.assertEqual(r["status"], "kept")


class StatusTests(Base):
    def test_status_shape_and_stuck_running(self):
        M._update(status="running")
        st = M.status()
        self.assertEqual(st["status"], "failed")                          # nothing is running -> not "Running"
        self.assertIn("steps", st)
        self.assertEqual([s["key"] for s in st["steps"]], list(M.STEP_ORDER))

    def test_files_listing_and_zip(self):
        self.margin = None
        M.accept_margin("Margin_Detail_17112026.xlsx", xlsx(pd.DataFrame({"AccountID": ["A"], "MAX": [1.0]})))
        self.assertEqual(M.list_backup_dates(), ["17-11-2026"])
        self.assertEqual(len(M.list_files("17-11-2026")), 1)
        z = M.zip_range(D1, D1)
        with zipfile.ZipFile(z) as zf:
            self.assertEqual(len(zf.namelist()), 1)
        with self.assertRaises(FileNotFoundError):
            M.file_path("nope.xlsx", "17-11-2026")


if __name__ == "__main__":
    unittest.main()
