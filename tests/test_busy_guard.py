"""While a process runs, uploads / settings of THAT process are refused (409); other processes and read-only calls are not.
The guard's code is taken from api.py as it is and mounted on a tiny app (the whole console needs heavy libraries).
Run:  python -m unittest tests.test_busy_guard -v"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class BusyGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except Exception as e:                                   # noqa: BLE001
            raise unittest.SkipTest(f"fastapi not available: {e}")
        src = (ROOT / "api.py").read_text(encoding="utf-8")
        a = src.index("_BUSY_CARD_PREFIX = ")
        b = src.index('@app.on_event("startup")\nasync def _stall_watch_startup')
        app = FastAPI()
        exec(compile(src[a:b], "api_busy_guard", "exec"), {"app": app})

        for path in ("/obligation/upload", "/obligation/override", "/cnc-f11/upload", "/pd/wa-config", "/px/upload"):
            app.post(path)(lambda: {"ok": True})
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_running_card_refuses_upload_and_settings_but_not_other_cards(self):
        running = {"cards": {"OBLIGATION REPORT": {"by": "Sumit", "running": True}}}
        with mock.patch("core.card_locks.snapshot", return_value=running):
            r = self.client.post("/obligation/upload")
            self.assertEqual(r.status_code, 409)
            self.assertIn("running", r.json()["detail"])
            self.assertIn("Sumit", r.json()["detail"])
            self.assertEqual(self.client.post("/obligation/override").status_code, 409)
            self.assertEqual(self.client.post("/cnc-f11/upload").status_code, 200)       # another card is idle

    def test_idle_card_is_not_blocked(self):
        with mock.patch("core.card_locks.snapshot", return_value={"cards": {}}):
            self.assertEqual(self.client.post("/obligation/upload").status_code, 200)

    def test_physdel_and_process_center_uploads(self):
        locks = {"locks": [{"pipe": "physdel", "by": "Sumit", "label": "PHYSICAL DELIVERY"}, {"pipe": "ctcl", "by": "Amit", "label": "CTCL"}]}
        with mock.patch("core.lock_manager.snapshot", return_value=locks):
            self.assertEqual(self.client.post("/pd/wa-config").status_code, 409)
            self.assertEqual(self.client.post("/px/upload?pipe=ctcl").status_code, 409)
            self.assertEqual(self.client.post("/px/upload?pipe=cli").status_code, 200)   # cli is idle

    def test_a_broken_lookup_never_blocks(self):
        with mock.patch("core.card_locks.snapshot", side_effect=RuntimeError("boom")):
            self.assertEqual(self.client.post("/obligation/upload").status_code, 200)


if __name__ == "__main__":
    unittest.main()
