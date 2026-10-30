import unittest
from pathlib import Path
from unittest import mock
import tempfile

from core.scheduler import nse


class R:
    def __init__(self, code, text="", content=b"", headers=None):
        self.status_code, self.text, self.content, self.headers = code, text, content, headers or {}
    def json(self): raise ValueError


class Ctx:
    cancelled = False
    def __init__(self): self.lines = []
    def log(self, m): self.lines.append(m)


def run(responses):
    seq = iter(responses)
    ctx = Ctx()
    with mock.patch.object(nse, "_send", side_effect=lambda *a, **k: next(seq)), mock.patch.object(nse, "_token", return_value="t"), \
         mock.patch("time.sleep"), mock.patch.object(nse.H, "need", return_value=mock.Mock()):
        out = nse.download("member", "SLB", "reports", "S_MG02_06436_29102026.csv", Path(tempfile.mkdtemp()), ctx)
    return out, ctx


NF = R(400, '{"responseCode":["715"],"message":"FOLDER NOT FOUND"}')


class NotFoundRetry(unittest.TestCase):
    def test_transient_715_then_file(self):
        out, ctx = run([NF, R(200, content=b"abc", headers={"Content-Type": "text/csv"})])
        self.assertIsNotNone(out)
        self.assertTrue(any("asking again" in l for l in ctx.lines))

    def test_real_not_published_still_returns_none(self):
        out, ctx = run([NF, NF, NF])
        self.assertIsNone(out)
        self.assertEqual(sum("asking again" in l for l in ctx.lines), 2)

    def test_404_is_not_retried(self):
        out, ctx = run([R(404)])
        self.assertIsNone(out)
        self.assertFalse(any("asking again" in l for l in ctx.lines))


if __name__ == "__main__":
    unittest.main()
