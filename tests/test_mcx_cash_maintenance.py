"""MCX CASH MAINTENANCE - report maths, file recognition, 15-minute link, timer windows, retry, override, upload, zip.
No portal is ever contacted and no mail is sent (steps are replaced / SMTP is faked)."""
import datetime as dt
import os
import shutil
import tempfile
import unittest
import zipfile

import pandas as pd

from core import mcx_cash_maintenance as M, mcx_cash_report as R, tcs_session_lock as LK

D = dt.date(2026, 10, 28)                      # a Wednesday


def make_inputs(folder, name_prefix="", cm_name="CM_ClientLevelCollaterals_28102026.csv",
                fo_name="FO_ClientLevelCollaterals_28102026.csv", client_name="RSK335-20261028093011.xlsx",
                deposit_name="RSK383-20261028093010.xlsx"):
    os.makedirs(folder, exist_ok=True)
    cl = pd.DataFrame({"Client Code": ["A", "B", "C"], "Client Margin Limit": [1000, 500, 200],
                       "Margin Utilization": ["800", "400", "100"], "Cli / TM Mrgn 90% (Rs)": ["700", "300", "50"]})
    dp = pd.DataFrame({"UCC Code": ["a", "B", "D"], "Allocation at EOD": [100, 0, 5], "Own Collateral EOD": ["50", 0, 5],
                       "Own Others Collateral at EOD": [10, 0, 5]})
    for df, nm in ((cl, client_name), (dp, deposit_name)):
        p = os.path.join(folder, name_prefix + nm)
        with pd.ExcelWriter(p) as w:              # two junk rows above the header, like the portal's export
            pd.DataFrame([["Report", ""], ["", ""]]).to_excel(w, index=False, header=False, startrow=0)
            df.to_excel(w, index=False, startrow=2)
    pd.DataFrame({"Client Code": ["A", "Z"], "Cash allocated (b)": [10, 1], "Cash-Others (c)": [1, 0],
                  "Non-cash Securities (d)": [2, 0]}).to_csv(os.path.join(folder, name_prefix + cm_name), index=False)
    pd.DataFrame({"Client Code": ["A"], "Cash allocated (b)": [5], "Cash-others (c)": [1],
                  "Non-cash Securities (d)": [0]}).to_csv(os.path.join(folder, name_prefix + fo_name), index=False)


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        make_inputs(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_classify_by_columns_not_by_name(self):
        self.assertEqual(R.classify_xlsx(os.path.join(self.tmp, "RSK335-20261028093011.xlsx")), "client")
        self.assertEqual(R.classify_xlsx(os.path.join(self.tmp, "RSK383-20261028093010.xlsx")), "deposit")
        other = os.path.join(self.tmp, "Shortage.xlsx")
        pd.DataFrame({"x": [1]}).to_excel(other, index=False)
        self.assertIsNone(R.classify_xlsx(other))

    def test_numbers_match_the_hand_run_script(self):
        res = R.build_reports(os.path.join(self.tmp, "RSK335-20261028093011.xlsx"), os.path.join(self.tmp, "RSK383-20261028093010.xlsx"),
                              os.path.join(self.tmp, "CM_ClientLevelCollaterals_28102026.csv"),
                              os.path.join(self.tmp, "FO_ClientLevelCollaterals_28102026.csv"), os.path.join(self.tmp, "out"))
        full = pd.read_excel(res["full"]).set_index("UCC Code")
        self.assertEqual(int(full.loc["A", "Cash_Maintenance"]), 500)           # min(800,1000) - 2*(100+50), A has other collateral
        self.assertEqual(int(full.loc["B", "Cash_Maintenance"]), 0)             # no "Own Others Collateral" -> zeroed
        self.assertEqual(int(full.loc["C", "Cash_Maintenance"]), 0)
        self.assertEqual(int(full.loc["A", "TOTAL NSSCL DEPOSIT"]), 19)         # CM 10+1+2 plus FO 5+1+0
        self.assertEqual(int(full.loc["A", "MCX Ledger + Cash Equivalent Collateral"]), 150)
        self.assertIn("D", full.index)                                          # outer merge keeps deposit-only clients
        self.assertEqual(full.index[-1], "GRAND TOTAL")
        self.assertEqual(int(full.loc["GRAND TOTAL", "Cash_Maintenance"]), 500)
        self.assertEqual(res["rows"], 4)
        summ = pd.read_excel(res["summary"])
        self.assertEqual(summ["UCC Code"].iloc[-1], "GRAND TOTAL")
        self.assertEqual(summ["UCC Code"].iloc[0], "A")                         # sorted by 90% margin desc

    def test_protect_refuses_to_send_unprotected(self):
        with self.assertRaises(RuntimeError):
            R.protect(os.path.join(self.tmp, "RSK335-20261028093011.xlsx"), "")

    def test_protect_roundtrip_when_msoffcrypto_is_there(self):
        try:
            import msoffcrypto
        except ImportError:
            self.skipTest("msoffcrypto-tool not installed here")
        p = os.path.join(self.tmp, "RSK335-20261028093011.xlsx")
        R.protect(p, "secret1")
        with open(p, "rb") as fh:
            f = msoffcrypto.OfficeFile(fh)
            self.assertTrue(f.is_encrypted())

    def test_recipients_default_to_the_hand_run_lists(self):
        for k in R.DEFAULT_MAIL:
            os.environ.pop(k, None)
        to, cc = R.recipients("summary")
        self.assertEqual(to, ["va@mastertrust.co.in", "rks@mastertrust.co.in"])
        self.assertEqual(len(cc), 3)
        to, cc = R.recipients("full")
        self.assertEqual(len(to), 3)
        self.assertEqual(cc, [])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ms15 = tempfile.mkdtemp()
        os.environ.update({"MCX_CASH_DIR": os.path.join(self.tmp, "mc"), "MCX_CASH_LOG_DIR": os.path.join(self.tmp, "logs"),
                           "MARGIN_SHORTAGE_FOLDER": self.ms15, "MCX_CASH_MS15_POLL_SEC": "0.01", "MCX_CASH_MS15_WAIT_MIN": "0",
                           "TCS_SESSION_LOCK_DIR": os.path.join(self.tmp, "lock")})
        os.environ.pop("MCX_CASH_TIMES", None)
        for i, w in enumerate(["09:30-09:45", "09:50-10:05", "11:30-11:45", "11:55-12:15", "13:00-13:15", "13:20-13:35",
                               "14:30-14:45", "14:55-15:10", "15:15-15:30", "16:30-16:45"], start=1):
            os.environ[f"MS15_RUN{i}_WINDOW"] = w
        M._KIND_CACHE.clear()
        self.saved = dict(M.STEPS)
        self.calls = []
        self.sent = []

        def fake_nmass(folder, d, key, log, cancel):
            self.calls.append("nmass")
            pd.DataFrame({"Client Code": ["A"], "Cash allocated (b)": [1], "Cash-Others (c)": [1], "Non-cash Securities (d)": [1]}
                         ).to_csv(os.path.join(folder, "CM_ClientLevelCollaterals_28102026.csv"), index=False)
            pd.DataFrame({"Client Code": ["A"], "Cash allocated (b)": [1], "Cash-others (c)": [1], "Non-cash Securities (d)": [1]}
                         ).to_csv(os.path.join(folder, "FO_ClientLevelCollaterals_28102026.csv"), index=False)
            return {"ok": True, "msg": "ok"}

        def fake_mcx(folder, d, key, log, cancel):
            self.calls.append("mcx")
            if getattr(self, "mcx_fail", False):
                return {"ok": False, "msg": "RSA code refused"}
            tmp = os.path.join(self.tmp, "mk")
            make_inputs(tmp)
            for n in ("RSK335-20261028093011.xlsx", "RSK383-20261028093010.xlsx"):
                shutil.copy(os.path.join(tmp, n), os.path.join(folder, n))
            return {"ok": True, "msg": "ok"}

        def fake_email(folder, d, key, log, cancel):
            self.calls.append("email")
            if getattr(self, "mail_fail", False):
                return {"ok": False, "msg": "smtp down"}
            M._update(d, key, mailed={"full": "x", "summary": "x"})
            return {"ok": True, "msg": "ok"}
        M.STEPS["nmass"], M.STEPS["mcx"], M.STEPS["email"] = fake_nmass, fake_mcx, fake_email
        # the real ms15 and report steps are used

    def tearDown(self):
        M.STEPS.update(self.saved)
        for k in ("MCX_CASH_DIR", "MCX_CASH_LOG_DIR", "MARGIN_SHORTAGE_FOLDER", "MCX_CASH_MS15_POLL_SEC", "MCX_CASH_MS15_WAIT_MIN",
                  "TCS_SESSION_LOCK_DIR"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.ms15, ignore_errors=True)

    def run_key(self, key, **kw):
        # today() is the real today - pin the module's idea of the date by running against the real date folder
        return M.run_slot(key, trigger="manual", **kw)

    def today_ms15(self, n, files=True, manifest=True, counter=None):
        d = dt.date.today()
        f = os.path.join(self.ms15, "BACKUPFILES AUTOMATED", d.strftime("%Y-%m-%d"), f"{n:02d}")
        os.makedirs(f, exist_ok=True)
        if files:
            make_inputs(f, client_name="RSK335-X.xlsx", deposit_name="RSK383-X.xlsx")
            # the MCX file the 15-minute run did not use is stored with the NOT_USED__ prefix
            os.rename(os.path.join(f, "RSK383-X.xlsx"), os.path.join(f, "NOT_USED__RSK383-X.xlsx"))
            pd.DataFrame({"a": [1]}).to_excel(os.path.join(f, "Margin_Shortage_Alert_x.xlsx"), index=False)
        if manifest:
            open(os.path.join(f, "USED_FILES.txt"), "w").write("x")
        with open(os.path.join(self.ms15, "run_counter.txt"), "w") as fh:
            fh.write(f"{d.strftime('%Y-%m-%d')}|{counter if counter is not None else n}")


class Settings(Base):
    def test_default_times_modes_and_run_numbers(self):
        s = M.status()
        self.assertEqual([x["time"] for x in s["slots"]], ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30", "16:30"])
        self.assertEqual([x["mode"] for x in s["slots"]], ["ms15", "own", "ms15", "own", "own", "ms15", "own", "ms15"])
        self.assertEqual([x["ms15_run"] for x in s["slots"] if x["mode"] == "ms15"], [1, 3, 7, 10])

    def test_change_a_time_and_a_source(self):
        M.save_settings(times="09:45,10:30", modes={"09:45": "ms15", "10:30": "own"}, ms15_runs={"09:45": 2})
        self.assertEqual([M.hhmm(m) for m in M.slot_times()], ["09:45", "10:30"])
        s = M.status()
        self.assertEqual((s["slots"][0]["mode"], s["slots"][0]["ms15_run"]), ("ms15", 2))
        with self.assertRaises(ValueError):
            M.save_settings(times="25:00")
        with self.assertRaises(ValueError):
            M.save_settings(modes={"09:30": "banana"})

    def test_bad_folder_names_are_refused(self):
        for bad in ("../x", "9-30", "09:3", ""):
            with self.assertRaises(ValueError):
                M.check_key(bad)
        self.assertEqual(M.check_key("09:30"), "09-30")


class Ms15State(Base):
    def test_waiting_ready_gone(self):
        self.assertEqual(M.ms15_state(dt.date.today(), 1), "waiting")
        self.today_ms15(1, files=False, manifest=False, counter=0)
        self.assertEqual(M.ms15_state(dt.date.today(), 1), "waiting")           # folder exists but the run has not finished
        self.today_ms15(1)
        self.assertEqual(M.ms15_state(dt.date.today(), 1), "ready")
        self.assertEqual(M.ms15_state(dt.date.today(), 2), "waiting")
        # run 3 was skipped / ran elsewhere: counter jumped past it and no folder was made
        self.today_ms15(4, counter=4)
        self.assertEqual(M.ms15_state(dt.date.today(), 3), "gone")


class RunFlow(Base):
    def test_ms15_time_waits_then_copies_and_downloads_only_what_is_missing(self):
        # run 1 finished: backup holds MCX 90% + (NOT_USED__) deposit + the shortage output; CM/FO csv are there too
        self.today_ms15(1)
        rec = self.run_key("09-30")
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(self.calls, ["email"])                                   # nothing was downloaded - all 4 came from the 15-min run
        d = dt.date.today()
        found = M.check_files(d, "09-30")["found"]
        self.assertEqual(sorted(found), sorted(l for l, _ in M.LABELS))
        self.assertTrue(rec["steps"]["ms15"]["ok"])
        self.assertFalse(any(n.startswith("NOT_USED") for n in os.listdir(M.slot_dir(d, "09-30"))))
        self.assertFalse(any(n.startswith("Margin_Shortage") for n in os.listdir(M.slot_dir(d, "09-30"))))

    def test_ms15_not_finished_in_time_falls_back_to_own_download(self):
        rec = self.run_key("09-30")                                              # no backup exists, wait limit is 0 minutes
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(self.calls, ["nmass", "mcx", "email"])
        self.assertFalse(rec["steps"]["ms15"]["ok"])

    def test_ms15_that_lacks_a_file_downloads_just_that_one(self):
        self.today_ms15(1)
        d = dt.date.today()
        f = os.path.join(self.ms15, "BACKUPFILES AUTOMATED", d.strftime("%Y-%m-%d"), "01")
        os.remove(os.path.join(f, "CM_ClientLevelCollaterals_28102026.csv"))
        os.remove(os.path.join(f, "FO_ClientLevelCollaterals_28102026.csv"))
        rec = self.run_key("09-30")
        self.assertEqual(self.calls, ["nmass", "email"])                          # MCX files came from the link, CM/FO downloaded
        self.assertEqual(rec["status"], "done")

    def test_own_time_downloads_in_order_and_mails_once(self):
        rec = self.run_key("10-30")
        self.assertEqual(self.calls, ["nmass", "mcx", "email"])
        self.assertEqual(rec["status"], "done")
        self.assertEqual(M.check_files(dt.date.today(), "10-30")["missing"], [])
        self.calls.clear()
        self.run_key("10-30")                                                    # nothing left to do
        self.assertEqual(self.calls, [])

    def test_failed_mcx_is_retried_alone_and_nmass_is_not_repeated(self):
        self.mcx_fail = True
        rec = self.run_key("10-30")
        self.assertEqual(rec["status"], "failed")
        self.assertIn("RSA code refused", rec["msg"])
        self.assertNotIn("email", self.calls)
        self.calls.clear()
        self.mcx_fail = False
        rec = self.run_key("10-30")
        self.assertEqual(self.calls, ["mcx", "email"])
        self.assertEqual(rec["status"], "done")

    def test_failed_mail_is_retried_without_rebuilding_or_downloading(self):
        self.mail_fail = True
        rec = self.run_key("10-30")
        self.assertEqual(rec["status"], "failed")
        self.assertIn("smtp down", rec["msg"])
        self.calls.clear()
        self.mail_fail = False
        rec = self.run_key("10-30")
        self.assertEqual(self.calls, ["email"])
        self.assertEqual(rec["status"], "done")

    def test_run_again_moves_the_old_files_aside_and_mails_again(self):
        self.run_key("10-30")
        self.calls.clear()
        rec = self.run_key("10-30", force=True)
        self.assertEqual(self.calls, ["nmass", "mcx", "email"])
        self.assertEqual(rec["status"], "done")
        aside = os.path.join(M.root(), "_state", "_replaced")
        self.assertTrue(os.path.isdir(aside))

    def test_skip_and_override(self):
        M.skip_slot("10-30")
        self.assertEqual(M.status()["slots"][1]["state"], "skipped")
        self.assertEqual(M.run_slot("10-30", trigger="auto")["status"], "skipped")        # the timer must not start it
        self.assertEqual(self.calls, [])
        M.unskip_slot("10-30")
        M.mark_done("10-30")
        self.assertEqual(M.status()["slots"][1]["state"], "done")
        M.mark_undone("10-30")
        self.assertIn(M.status()["slots"][1]["state"], ("due", "upcoming"))


class Timer(Base):
    def at(self, h, m):
        return dt.datetime(2026, 10, 28, h, m)

    def test_nothing_before_the_first_time_and_picks_the_first_due(self):
        self.assertIsNone(M.due_slot(self.at(9, 0)))
        self.assertEqual(M.due_slot(self.at(9, 31)), ("09-30", False))

    def test_window_ends_at_the_next_time_or_the_grace(self):
        self.assertEqual(M.due_slot(self.at(10, 29)), ("09-30", False))
        self.assertEqual(M.due_slot(self.at(10, 31)), ("10-30", False))          # 09:30 window over, 10:30 starts

    def test_weekend_is_quiet(self):
        self.assertIsNone(M.due_slot(dt.datetime(2026, 10, 31, 10, 0)))          # Saturday

    def test_retries_wait_the_gap_and_stop_after_the_limit(self):
        d = dt.date(2026, 10, 28)
        M._update(d, "09-30", auto_attempts=1, last_auto="2026-10-28 09:31:00")
        self.assertEqual(M.due_slot(self.at(9, 33)), None)                       # inside the gap, and later times not yet due
        self.assertEqual(M.due_slot(self.at(9, 37)), ("09-30", False))
        M._update(d, "09-30", auto_attempts=3)
        self.assertIsNone(M.due_slot(self.at(9, 50)))

    def test_done_time_is_not_started_again(self):
        d = dt.date(2026, 10, 28)
        folder = M.slot_dir(d, "09-30", create=True)
        make_inputs(str(folder))
        for n in ("MCX_CASH_MAINTENANCE_FULL_FILE.xlsx", "MCX_CASH_MAINTENANCE.xlsx"):
            pd.DataFrame({"x": [1]}).to_excel(os.path.join(folder, n), index=False)
        self.assertEqual(M.due_slot(self.at(9, 40)), ("09-30", False))         # files there but not mailed yet
        M._update(d, "09-30", mailed={"full": "x", "summary": "x"})
        self.assertIsNone(M.due_slot(self.at(9, 40)))

    def test_marked_not_done_runs_even_after_its_window(self):
        d = dt.date(2026, 10, 28)
        M._update(d, "09-30", redo=True)
        self.assertEqual(M.due_slot(self.at(15, 0)), ("09-30", True))

    def test_tick_respects_auto_off_and_counts_attempts(self):
        started = []
        M.save_settings(auto=False)
        self.assertIsNone(M.tick(self.at(9, 31), runner=lambda k, f: started.append(k)))
        M.save_settings(auto=True)
        self.assertEqual(M.tick(self.at(9, 31), runner=lambda k, f: started.append(k)), "09-30")
        self.assertEqual(M.read_state(dt.date(2026, 10, 28))["slots"]["09-30"]["auto_attempts"], 1)


class Files(Base):
    def test_upload_recognises_files_by_content_and_rejects_the_rest(self):
        d = dt.date.today()
        src = os.path.join(self.tmp, "src")
        make_inputs(src)
        for n in ("RSK335-20261028093011.xlsx", "RSK383-20261028093010.xlsx", "CM_ClientLevelCollaterals_28102026.csv",
                  "FO_ClientLevelCollaterals_28102026.csv"):
            r = M.accept_upload(d, "09-30", n, open(os.path.join(src, n), "rb").read())
            self.assertTrue(r["label"])
        with self.assertRaises(ValueError):
            M.accept_upload(d, "09-30", "notes.txt", b"hello")
        with self.assertRaises(ValueError):
            M.accept_upload(d, "09-30", "other.xlsx", open(os.path.join(src, "RSK335-20261028093011.xlsx"), "rb").read()[:10])
        res = M.finish_upload(d, "09-30")
        self.assertTrue(res["inputs_complete"])
        self.assertEqual(sorted(res["missing"]), ["Full report", "Summary report"])
        # a second copy of the same kind replaces the first (the old one is moved aside, never two in the folder)
        M.accept_upload(d, "09-30", "RSK335-NEW.xlsx", open(os.path.join(src, "RSK335-20261028093011.xlsx"), "rb").read())
        names = [f["name"] for f in M.list_files(d, "09-30")]
        self.assertIn("RSK335-NEW.xlsx", names)
        self.assertNotIn("RSK335-20261028093011.xlsx", names)

    def test_zip_range_and_listing(self):
        d = dt.date.today()
        for k in ("09-30", "10-30"):
            f = M.slot_dir(d, k, create=True)
            (f / "a.txt").write_text("x")
        z = M.zip_range(d, d)
        with zipfile.ZipFile(z) as zf:
            self.assertEqual(sorted(zf.namelist()), [f"{M.dstr(d)}/09-30/a.txt", f"{M.dstr(d)}/10-30/a.txt"])
        z = M.zip_range(d, d, ["10-30"])
        with zipfile.ZipFile(z) as zf:
            self.assertEqual(zf.namelist(), [f"{M.dstr(d)}/10-30/a.txt"])
        with self.assertRaises(ValueError):
            M.zip_range(d, d, ["../x"])
        self.assertEqual(M.range_listing(d, d)[0]["files"], 2)

    def test_file_path_cannot_escape(self):
        d = dt.date.today()
        (M.slot_dir(d, "09-30", create=True) / "a.txt").write_text("x")
        with self.assertRaises(ValueError):
            M.file_path(d, "09-30", "../a.txt")
        self.assertTrue(M.file_path(d, "09-30", "a.txt").is_file())


class SharedLoginLock(Base):
    def test_second_taker_waits_and_can_be_cancelled(self):
        self.assertTrue(LK.acquire("liveallocation", wait_s=1))
        self.assertFalse(LK.acquire("mcxcash", wait_s=0))
        self.assertEqual(LK.holder()["owner"], "liveallocation")
        LK.release("mcxcash")                                                    # not the holder - must not free it
        self.assertIsNotNone(LK.holder())
        LK.release("liveallocation")
        self.assertTrue(LK.acquire("mcxcash", wait_s=1))
        LK.release("mcxcash")


class PairDownloader(unittest.TestCase):
    def test_picks_our_two_files_by_time_and_query(self):
        from bs4 import BeautifulSoup
        import importlib.util
        spec = importlib.util.spec_from_file_location("pair", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                                          "sa_codes", "mcx_tcs_pair_downloader.py"))
        pair = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pair)
        t383, t335 = dt.datetime(2026, 10, 28, 9, 30, 0), dt.datetime(2026, 10, 28, 9, 30, 8)

        def row(q, status, ts, name):
            return f"<tr><td>w</td><td>t</td><td>{q}</td><td>ty</td><td>{status}</td><td>{ts}</td><td>{name}</td></tr>"
        html = "<table><tbody>" + row("Transaction From Date  Equals  28%2F10%2F2026", "Generated", "28/10/2026 09:30:02", "RSK383-20261028093002.xlsx") + \
            row("Client Code  Equals  DFLT", "In Progress", "28/10/2026 09:30:09", "RSK335-20261028093009.xlsx") + \
            row("Client Code  Equals  DFLT", "Generated", "28/10/2026 08:00:00", "RSK335-20261028080000.xlsx") + "</tbody></table>"
        rows = BeautifulSoup(html, "html.parser").select("table tbody tr")
        got = pair.pick_targets(rows, t383, t335)
        self.assertEqual(got["RSK383"], ("RSK383-20261028093002.xlsx", True))
        self.assertEqual(got["RSK335"], ("RSK335-20261028093009.xlsx", False))     # still generating -> not ready yet


if __name__ == "__main__":
    unittest.main()


class LockLeftovers(Base):
    def test_lock_of_a_dead_server_or_of_the_same_card_is_taken_over(self):
        import json
        p = LK._path()
        p.write_text(json.dumps({"owner": "liveallocation", "pid": 99999999, "since": "15:10:00"}), encoding="utf-8")
        self.assertTrue(LK.acquire("liveallocation", wait_s=0))          # holder process does not exist (server was restarted)
        LK.release("liveallocation")
        p.write_text(json.dumps({"owner": "liveallocation", "pid": os.getpid(), "since": "15:10:00"}), encoding="utf-8")
        self.assertTrue(LK.acquire("liveallocation", wait_s=0))          # the card's own leftover (it never runs twice at once)
        LK.release("liveallocation")
        p.write_text(json.dumps({"owner": "mcxcash", "pid": os.getpid(), "since": "15:10:00"}), encoding="utf-8")
        self.assertFalse(LK.acquire("liveallocation", wait_s=0))         # a really running other card is respected
