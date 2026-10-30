"""NSE 'file not there yet' answers must never be a failure: 404, 400/715 FOLDER NOT FOUND, 400/714 FILE NOT FOUND.

    python -m unittest tests.test_nse_not_found -v
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_scheduler_processes import Base, Ctx



from core.scheduler import nse
nse.NOT_FOUND_PAUSE = 0          # the retry pause for 715/714 answers is real in production; no waiting in tests

class Resp:
    def __init__(self, code, text="", headers=None, content=b""):
        self.status_code, self.text, self.headers, self.content = code, text, headers or {}, content

    def json(self):
        import json
        return json.loads(self.text)


class NotThereYet(Base):
    def _dl(self, resp):
        from core.scheduler import nse
        with mock.patch.object(nse, "_send", return_value=resp), mock.patch.object(nse, "_token", return_value="t"):
            return nse.download("member", "CM", "Reports", "C_SA01_X.lis.gz", Path(tempfile.mkdtemp()), Ctx())

    def test_404(self):
        self.assertIsNone(self._dl(Resp(404)))

    def test_400_714_file_not_found(self):          # seen live 15-10-2026 inside a wait loop
        self.assertIsNone(self._dl(Resp(400, '{"responseCode":["714"],"responseDesc":"FILE NOT FOUND","data":null}')))

    def test_400_715_folder_not_found(self):
        self.assertIsNone(self._dl(Resp(400, '{"responseCode":["715"],"responseDesc":"FOLDER NOT FOUND","data":null}')))

    def test_other_400_is_still_a_failure(self):
        with self.assertRaises(RuntimeError):
            self._dl(Resp(400, '{"responseCode":["999"],"responseDesc":"BAD REQUEST"}'))


if __name__ == "__main__":
    unittest.main()
