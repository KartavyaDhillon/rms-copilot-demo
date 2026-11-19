"""Card locks snapshot: only running cards appear, with who started them. Run: python -m unittest tests.test_card_locks -v"""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import card_locks as CL


class Fake(types.SimpleNamespace):
    def is_running(self):
        return self.running

    def status(self):
        return self.st


class Tests(unittest.TestCase):
    def test_only_running_cards_with_who(self):
        mods = {"core.cnc_f11_card": Fake(running=True, st={"by": "Abhishek", "started": "2026-11-18 15:53:30"}),
                "core.payout_check_card": Fake(running=False, st={}),
                "core.live_allocation": Fake(running=True, st={"slots": [{"state": "done"}, {"state": "running", "by": "Raj", "started": "2026-11-18 11:30:00"}]})}
        old = CL.importlib.import_module
        CL.importlib.import_module = lambda n: mods[n] if n in mods else (_ for _ in ()).throw(ImportError(n))
        try:
            snap = CL.snapshot()["cards"]
        finally:
            CL.importlib.import_module = old
        self.assertEqual(set(snap), {"CNC F11", "LIVE ALLOCATION CAPTURE"})
        self.assertEqual(snap["CNC F11"]["by"], "Abhishek")
        self.assertEqual(snap["LIVE ALLOCATION CAPTURE"]["by"], "Raj")

    def test_every_card_module_has_is_running(self):
        import importlib
        for title, (key, mod) in CL.CARDS.items():
            m = importlib.import_module(mod)
            self.assertTrue(callable(getattr(m, "is_running", None)), mod)


if __name__ == "__main__":
    unittest.main()
