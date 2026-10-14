"""ABOVE 4 CR HOLDING BLOCKING FILE: selection, line format, new/unblock comparison, e-mail, upload switch.

    python -m unittest tests.test_above_4cr_blocking -v
"""
import unittest
from unittest import mock

from tests.test_scheduler_processes import Base, Ctx

HEAD = "Zone,Region,Branch,SubBranch,RmtlID,AccountID,AccountName,Boscrip,Symbol,PledgeQty,RePledgeQty,HoldingFull,ExchVar\n"


def row(acct, bos, sym, pl=0, rp=0, hv=50_000_000, ev=100):
    return f"ZO,R,B,S,M,{acct},NAME,{bos},{sym},{pl},{rp},{hv},{ev}\n"


def line(acct, ex, scrip):
    return f"||||{acct}|S|CNC|ALL|{ex}|{scrip}|||||Y|||Selling blocked high value selling require early payin "


class Pure(unittest.TestCase):
    def test_selection_and_lines(self):
        import tempfile
        from pathlib import Path
        from core.scheduler.processes import above_4cr_holding_blocking_file as ab
        p = Path(tempfile.mkdtemp()) / "HOLDING_X.csv"
        p.write_text(HEAD + row("9LDH162", "504864", "") + row("7CPK534", "505712", "HIMTEK") +
                     row("LOW1", "1", "A", hv=39_999_999) + row("PL1", "2", "B", pl=5) + row("RP1", "3", "C", rp=1) +
                     row("EV1", "4", "D", ev=90) + row("7CPK534", "505712", "HIMTEK"))        # duplicate row
        out, n = ab.select_holdings(p)
        self.assertEqual(n, 3)
        self.assertEqual([l for _, l in out], [line("9LDH162", "BSE", "504864"), line("7CPK534", "NSE", "HIMTEK"),
                                                line("7CPK534", "BSE", "505712")])

    def test_compare_new_and_unblock(self):
        from core.scheduler.processes import above_4cr_holding_blocking_file as ab
        master = [line("A1", "BSE", "111"), line("A2", "NSE", "XYZ"), line("A2", "BSE", "222")]
        today = [(("A1", "BSE", "111"), line("A1", "BSE", "111")), (("A3", "NSE", "NEW"), line("A3", "NSE", "NEW"))]
        new, removed, kept = ab.compare(master, today)
        self.assertEqual([k for k, _ in new], [("A3", "NSE", "NEW")])
        self.assertEqual(removed, [("A2", "NSE", "XYZ"), ("A2", "BSE", "222")])
        self.assertEqual(kept, [master[0]])
        self.assertEqual(ab.unblock_csv(removed).splitlines(), ["AccountID,Boscrip,Symbol", "A2,,XYZ", "A2,222,"])


class Run(Base):
    def _go(self, upload):
        from core.scheduler.processes import above_4cr_holding_blocking_file as ab
        m = ab.master_path()
        m.write_bytes(("\r\n".join(["RMS Blocking/UnBlocking", line("A1", "BSE", "111"), line("A2", "NSE", "XYZ")]) + "\r\n").encode("latin-1"))
        hold = m.parent / "HOLDING_T.csv"
        hold.write_text(HEAD + row("A1", "111", "") + row("A3", "333", "NEW"))
        sent, ftp = [], []
        with mock.patch.object(ab, "find_holding", return_value=hold), \
             mock.patch("core.scheduler.processes._kit.require_trading_day"), \
             mock.patch.object(ab, "upload_is_on", return_value=upload), \
             mock.patch("core.scheduler.helpers.send_email", side_effect=lambda s, b, to, cc, files, c, **k: sent.append((s, [f.name for f in files]))), \
             mock.patch("core.scheduler.helpers.kambala_upload", side_effect=lambda f, c: ftp.append([x.name for x in f]) or True):
            res = ab._run({"to": "a@mastertrust.co.in"}, Ctx())
        return ab, m, sent, ftp, res

    def test_emails_both_files_uploads_only_blocking_file_when_on(self):
        ab, m, sent, ftp, res = self._go(True)
        self.assertEqual(sent[0][1], [ab.OUT_NAME, ab.UNBLOCK])
        self.assertEqual(ftp, [[ab.OUT_NAME]])
        out = (m.parent.parent / "outputs" / ab.OUT_NAME).read_text("latin-1")
        self.assertEqual(out.splitlines(), ["RMS Blocking/UnBlocking", line("A3", "BSE", "333"), line("A3", "NSE", "NEW")][:1] +
                         [l for l in out.splitlines()[1:]])
        self.assertIn(line("A3", "NSE", "NEW"), out)
        self.assertNotIn("A1", out)                                         # old code is not repeated
        new_master = m.read_text("latin-1")
        self.assertIn("A3", new_master)
        self.assertNotIn("A2", new_master)                                  # unblocked code leaves the master
        self.assertIn("1 to unblock", res)

    def test_nothing_qualifies_unblocks_everything_and_empties_the_master(self):
        from core.scheduler.processes import above_4cr_holding_blocking_file as ab
        m = ab.master_path()
        m.write_bytes(("\n".join(["RMS Blocking/UnBlocking", line("A1", "BSE", "111"), line("A1", "BSE", "111"),
                                  line("A2", "NSE", "XYZ")]) + "\n").encode("latin-1"))
        hold = m.parent / "HOLDING_T.csv"
        hold.write_text(HEAD + row("LOW", "1", "A", hv=1))
        sent = []
        with mock.patch.object(ab, "find_holding", return_value=hold), \
             mock.patch("core.scheduler.processes._kit.require_trading_day"), \
             mock.patch.object(ab, "upload_is_on", return_value=True), \
             mock.patch("core.scheduler.helpers.send_email", side_effect=lambda s, b, to, cc, files, c, **k: sent.append([f.name for f in files])), \
             mock.patch("core.scheduler.helpers.kambala_upload") as ftp:
            ab._run({"to": "a@mastertrust.co.in"}, Ctx())
        self.assertEqual(sent, [[ab.UNBLOCK]])                              # only the unblock list goes out
        ftp.assert_not_called()                                              # nothing to block → nothing uploaded
        self.assertEqual(m.read_text("latin-1").strip(), "RMS Blocking/UnBlocking")

    def test_upload_off(self):
        ab, m, sent, ftp, res = self._go(False)
        self.assertEqual(ftp, [])
        self.assertIn("NOT uploaded", res)


if __name__ == "__main__":
    unittest.main()
