"""A day folder filed into its month folder (NOV 2026\\13-11-2026) is still found by the readers of the cards.
Run:  python -m unittest tests.test_backup_month_readers -v"""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class MonthReaders(unittest.TestCase):
    def test_files70_live_allocation_mcx_cash(self):
        from core import files70, live_allocation as L, mcx_cash_maintenance as M
        d = dt.date(2026, 11, 13)
        saved = {m: m.root for m in (files70, L, M)}
        self.addCleanup(lambda: [setattr(m, "root", r) for m, r in saved.items()])
        with tempfile.TemporaryDirectory() as tmp:
            for mod in (files70, L, M):
                base = Path(tmp) / mod.__name__ / mod.SUB
                mod.root = (lambda b=Path(tmp) / mod.__name__: b)
                day = base / "NOV 2026" / "13-11-2026"
                (day / "CM").mkdir(parents=True)
                (day / "CM" / "a.txt").write_text("x")
                self.assertEqual(mod.day_dir(d), day, mod.__name__)
            self.assertTrue(any(f["rel"].endswith("a.txt") for f in files70.backup_files(d)))
            self.assertIn(d, L.list_dates())
            self.assertIn(d, M.list_dates())
            # a day that exists nowhere still points at the plain place (new days are created there)
            self.assertEqual(files70.day_dir(dt.date(2026, 11, 18)), Path(tmp) / "core.files70" / files70.SUB / "18-11-2026")


if __name__ == "__main__":
    unittest.main()
