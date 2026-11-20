import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import safe_state


class SafeState(unittest.TestCase):
    def test_plain(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            self.assertTrue(safe_state.write_json(p, {"a": 1}))
            self.assertEqual(json.loads(p.read_text()), {"a": 1})

    def test_locked_replace_retries(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            real = os.replace
            n = {"c": 0}

            def flaky(a, b):
                n["c"] += 1
                if n["c"] < 3:
                    raise PermissionError(5, "Access is denied")
                return real(a, b)
            with mock.patch("os.replace", flaky):
                self.assertTrue(safe_state.write_json(p, {"a": 2}, delay=0.01))
            self.assertEqual(json.loads(p.read_text()), {"a": 2})

    def test_always_locked_falls_back_and_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            with mock.patch("os.replace", side_effect=PermissionError(5, "denied")):
                self.assertTrue(safe_state.write_json(p, {"a": 3}, retries=2, delay=0.01))
            self.assertEqual(json.loads(p.read_text()), {"a": 3})


if __name__ == "__main__":
    unittest.main()
