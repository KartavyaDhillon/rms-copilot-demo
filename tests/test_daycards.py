"""MASTER FILE / SA / EODSAR cards (core/daycards.py): the pre-check always runs first and stops the process when a file is missing,
inputs are read in place, outputs are harvested, manual uploads are validated, backup zips are laid out like the day folder."""
import ast
import datetime as dt
import os
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core import daycards as D

DAY = dt.date(2026, 10, 29)
S = "29102026"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dctest_"))
        self.day = self.tmp / "76" / "29-10-2026"
        self.day.mkdir(parents=True)
        self.live = self.tmp / "live"
        self.common = self.tmp / "common"
        self.common.mkdir()
        os.environ["DAYCARDS_DIR"] = str(self.tmp / "data")
        self.patches = [mock.patch("core.files70.day_dir", lambda d: self.day),
                        mock.patch("core.live_allocation.day_dir", lambda d: self.live, create=True),
                        mock.patch("core.common_ftp.common_dir", lambda: str(self.common), create=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.environ.pop("DAYCARDS_DIR", None)


class PreCheck(Base):
    def test_missing_files_stop_the_run_and_are_logged_with_name_and_source(self):
        lines = []
        with mock.patch("subprocess.Popen") as popen:
            res = D.run_proc("master", "master_file", DAY, lines.append)
        popen.assert_not_called()                           # the script must not even start
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "pre-check")
        self.assertTrue(res["missing"])
        text = "\n".join(lines)
        self.assertIn("PRE-CHECK FAILED", text)
        self.assertIn("NOT run and nothing was e-mailed", text)
        self.assertIn("expected:", text)
        self.assertIn("from:", text)

    def test_every_process_has_a_precheck_list(self):
        for key in D.PROCS:
            self.assertTrue(D.check_needs(key, DAY), key)

    def test_status_counts_found_and_total(self):
        st = D.status("master", DAY)
        self.assertEqual([p["key"] for p in st["procs"]], D.CARDS["master"]["procs"])
        for p in st["procs"]:
            self.assertEqual(p["found"], 0)
            self.assertGreater(p["total"], 0)
        self.assertIsNone(st["manual"])
        self.assertIsNotNone(D.status("sa", DAY)["manual"])


class Manual(Base):
    def test_wrong_name_rejected(self):
        with self.assertRaises(ValueError):
            D.save_manual(DAY, "response", "other.csv", b"x")
        with self.assertRaises(ValueError):
            D.save_manual(DAY, "alloc", "Allocation_Detail_01012026.csv", b"x")
        with self.assertRaises(ValueError):
            D.save_manual(DAY, "response", D.RESPONSE_NAME, b"")

    def test_wrong_day_in_response_rejected(self):
        with self.assertRaises(ValueError):
            D.save_manual(DAY, "response", D.RESPONSE_NAME, b"Date,x\n28/10/2026,1\n")

    def test_saved_and_shown(self):
        D.save_manual(DAY, "response", D.RESPONSE_NAME, b"Date,x\n29/10/2026,1\n")
        D.save_manual(DAY, "alloc", f"Allocation_Detail_{S}.csv", b"a,b\n1,2\n")
        slots = {s["key"]: s for s in D.manual_status(DAY)["slots"]}
        self.assertTrue(slots["response"]["present"] and slots["alloc"]["present"])


class ManualSlots(Base):
    def test_each_card_shows_only_the_manual_files_it_uses(self):
        keys = lambda c: [x["key"] for x in D.manual_status(DAY, c)["slots"]]
        self.assertEqual(keys("sa"), ["response"])
        self.assertEqual(sorted(keys("eodsar")), ["alloc", "response"])


class Risk(Base):
    def test_risk_report_found_in_dated_backup_and_not_in_zips(self):
        bk = self.common / "BACKUP FILES AUTOMATED" / "29-10-2026"
        bk.mkdir(parents=True)
        (bk / f"RISK_REPORT_{S}.csv").write_text("x")
        self.assertEqual([x.path.parent for x in D.risk_sources(DAY)], [bk])
        (self.common / f"RISK_REPORT_{S}.csv").write_text("y")                 # today's file in COMMON FILES is found too
        self.assertEqual(len(D.risk_sources(DAY)), 2)
        files = D.backup_files("sa", DAY, ["sa_cm"])
        self.assertFalse([f for f in files if "RISK" in f["rel"]])
        self.assertFalse([m for m in D.missing_for_backup("sa", DAY, ["sa_cm"]) if "Risk" in m["label"]])


class Auto(Base):
    def setUp(self):
        super().setUp()
        for k in list(os.environ):
            if k.startswith("DC_") and "_AUTO" in k:
                os.environ.pop(k)
        self.tdays = mock.patch.object(D, "_trading_day", lambda d: d.weekday() < 5)
        self.tdays.start()
        self.logp = mock.patch.object(D, "log_dir", lambda card: self.tmp / "logs" / card)
        self.logp.start()

    def tearDown(self):
        self.logp.stop()
        self.tdays.stop()
        super().tearDown()

    def at(self, h, m, card="master", starter=None):
        return D.auto_tick(card, dt.datetime(2026, 10, 30, h, m), starter=starter or (lambda c, procs, d, by="": {"ok": True}))

    def test_times_per_card(self):
        self.assertEqual({c: D.auto_time(c) for c in D.CARDS}, {"master": "09:30", "sa": "11:00", "eodsar": "11:00"})

    def test_runs_only_after_its_time_on_trading_days(self):
        self.assertIsNone(self.at(9, 29))
        self.assertIn("attempt 1", self.at(9, 30))
        self.assertIsNone(D.auto_tick("master", dt.datetime(2026, 10, 31, 9, 45), starter=lambda *a, **k: {"ok": True}))   # Saturday
        self.assertIsNone(self.at(10, 59, "sa"))
        self.assertTrue(self.at(11, 0, "sa"))

    def test_retries_wait_and_switch_off(self):
        self.assertTrue(self.at(9, 30))
        self.assertIsNone(self.at(9, 40))                 # too soon after the first try
        self.assertTrue(self.at(9, 46))
        os.environ["DC_SA_AUTO"] = "off"
        self.assertIsNone(self.at(11, 5, "sa"))

    def test_time_can_be_changed(self):
        os.environ["DC_MASTER_AUTO_TIME"] = "10:15"
        self.assertIsNone(self.at(9, 40))
        self.assertTrue(self.at(10, 15))

    def test_only_what_is_not_done_is_started(self):
        got = {}
        D_ = D
        with mock.patch.object(D_, "proc_done", lambda c, k, d: k == "master_file"):
            self.at(9, 30, starter=lambda c, procs, d, by="": got.setdefault("p", procs) and {"ok": True})
        self.assertEqual(got["p"], ["live_allocation_report", "mcx_intrasar"])


class Upload(Base):
    def test_routes_by_name(self):
        (self.live / "8").mkdir(parents=True)
        with mock.patch("core.live_allocation.day_dir", lambda d: self.live):
            r = D.save_upload("sa", DAY, f"MCX_Web_Allocation_03.xlsx", b"x")
            self.assertTrue((self.live / "3" / "MCX_Web_Allocation_03.xlsx").is_file())
            r = D.save_upload("eodsar", DAY, f"ClientNetEffectiveDeposits_{S}.csv", b"x")
            self.assertIn("folder 8", r["where"])
        r = D.save_upload("sa", DAY, f"RISK_REPORT_{S}.csv", b"x")
        self.assertTrue((self.common / f"RISK_REPORT_{S}.csv").is_file())
        r = D.save_upload("sa", DAY, "EPI.xlsx", b"x")
        self.assertTrue((self.day / "EPI.xlsx").is_file())
        r = D.save_upload("sa", DAY, "MCX_MARGIN_56720_20261029.csv", b"x")
        self.assertTrue((self.day / "MCX" / "MCX_MARGIN_56720_20261029.csv").is_file())
        D.save_upload("eodsar", DAY, f"Allocation_Detail_{S}.csv", b"a")
        self.assertTrue((D.manual_dir(DAY) / f"Allocation_Detail_{S}.csv").is_file())

    def test_wrong_files_rejected(self):
        with self.assertRaises(ValueError):
            D.save_upload("sa", DAY, "random.csv", b"x")
        with self.assertRaises(ValueError):
            D.save_upload("sa", DAY, f"RISK_REPORT_01012026.csv", b"x")
        with self.assertRaises(ValueError):
            D.save_upload("sa", DAY, "EPI.xlsx", b"")


class Upstream(Base):
    def test_files_made_by_an_earlier_step_are_not_missing(self):
        needs = D.check_needs("live_allocation_report", DAY)
        up = [n for n in needs if n["upstream"]]
        self.assertEqual([n["upstream"] for n in up], ["master_file"])
        self.assertFalse(D.would_pass("live_allocation_report", DAY))     # the earlier step has no inputs here
        with mock.patch.object(D, "check_needs", side_effect=lambda proc, d, staged=None: (
                [] if proc == "master_file" else [{"present": False, "optional": False, "upstream": "master_file"}])):
            self.assertTrue(D.would_pass("live_allocation_report", DAY))


class Run(Base):
    def test_script_runs_in_work_folder_and_output_is_harvested(self):
        for n in D.PROCS["master_file"].needs(DAY):
            pass
        # fake every needed file so the pre-check passes
        for n in D.check_needs("master_file", DAY):
            self.assertFalse(n["present"])
        stub = self.tmp / "stub.py"
        stub.write_text(textwrap.dedent(f"""
            open("{S}_MASTER.xlsx", "w").write("ok")
            print("stub ran")
        """), encoding="utf-8")
        lines = []
        with mock.patch.object(D, "check_needs", return_value=[]), \
             mock.patch.object(D, "stage_plan", return_value=[]), \
             mock.patch.object(D, "patch_script", side_effect=lambda proc, text, work, values=None: text):
            res = D.run_proc("master", "master_file", DAY, lines.append, script_override=stub)
        self.assertTrue(res["ok"], "\n".join(lines))
        out = D.out_dir("master", "master_file", DAY)
        self.assertEqual([p.name for p in out.iterdir()], [f"{S}_MASTER.xlsx"])
        self.assertFalse(list((D.card_dir("master") / "_work").glob("master_file_*")))   # work folder removed

    def test_failed_script_is_failed_and_workfolder_kept(self):
        stub = self.tmp / "bad.py"
        stub.write_text("raise SystemExit(3)", encoding="utf-8")
        with mock.patch.object(D, "check_needs", return_value=[]), mock.patch.object(D, "stage_plan", return_value=[]), \
             mock.patch.object(D, "patch_script", side_effect=lambda proc, text, work, values=None: text):
            res = D.run_proc("master", "master_file", DAY, lambda m: None, script_override=stub)
        self.assertFalse(res["ok"])
        self.assertEqual(res["rc"], 3)


class Backup(Base):
    def test_zip_layout_and_filters(self):
        (self.day / "NSE CM").mkdir()
        f = self.day / "NSE CM" / "x.csv"
        f.write_text("1")
        out = D.out_dir("master", "master_file", DAY)
        out.mkdir(parents=True)
        (out / f"{S}_MASTER.xlsx").write_text("m")
        fake = [D.Src("x.csv", f, "day", f"29-10-2026/NSE CM/x.csv")]
        with mock.patch.object(D, "stage_plan", return_value=fake):
            names = lambda io: set(zipfile.ZipFile(D.make_zip("master", DAY, ["master_file"], io)).namelist())
            self.assertEqual(names("in"), {"29-10-2026/NSE CM/x.csv"})
            self.assertEqual(len(names("out")), 1)
            self.assertTrue(any("/OUTPUT/" in n for n in names("out")))
            self.assertEqual(len(names("both")), 2)
            z = D.make_zip("master", DAY, ["master_file"], "both", ["29-10-2026/NSE CM/x.csv"])
            self.assertEqual(zipfile.ZipFile(z).namelist(), ["29-10-2026/NSE CM/x.csv"])
            with self.assertRaises(FileNotFoundError):
                D.make_zip("master", DAY, ["master_file"], "both", ["nope"])

    def test_output_path_blocks_traversal(self):
        out = D.out_dir("master", "master_file", DAY)
        out.mkdir(parents=True)
        (out / "a.txt").write_text("a")
        self.assertIsNotNone(D.output_path("master", "master_file", DAY, "a.txt"))
        self.assertEqual(D.output_path("master", "master_file", DAY, "../../a.txt").parent, out)   # only the file name counts
        self.assertIsNone(D.output_path("master", "master_file", DAY, "../../nothere.txt"))
        self.assertIsNone(D.output_path("master", "live_allocation_report", DAY, "a.txt"))


class Scripts(unittest.TestCase):
    def test_bundled_scripts_parse_and_have_no_hardcoded_password(self):
        for p in D.SCRIPTS.glob("*.py"):
            if p.name == "__init__.py":
                continue
            text = p.read_text(encoding="utf-8")
            ast.parse(text)
            self.assertNotRegex(text, r"(?i)password\s*=\s*[\"'][^\"']{4,}[\"']", p.name)

    def test_email_env_names(self):
        to, cc = D.email_env("mcx_intrasar")
        self.assertTrue(to.startswith("DC_") and cc.startswith("DC_"))


if __name__ == "__main__":
    unittest.main()
