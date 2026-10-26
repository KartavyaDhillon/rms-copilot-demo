import unittest
from unittest import mock

from core import korp_api as K


class FakeResp:
    def __init__(self, text, code=200):
        self.text, self.status_code = text, code


class Korp(unittest.TestCase):
    def setUp(self):
        K._cache.update(base=None, at=0.0)

    def test_parse_json_and_xml(self):
        self.assertEqual(K.parse_session_key('"179B86F87E4E49C0"'), "179B86F87E4E49C0")
        self.assertEqual(K.parse_session_key('<string xmlns="http://schemas.microsoft.com/2003/10/Serialization/">179B86F87E4E49C0</string>'), "179B86F87E4E49C0")

    def test_candidates_order_and_clean(self):
        c = K.candidates("https://boapi.mastertrust.co.in/api/")
        self.assertEqual(c[0], K.PRIMARY)
        self.assertIn(K.FALLBACK, c)
        self.assertEqual(len(c), len(set(c)))

    def test_falls_back_when_main_is_down_and_back_when_it_returns(self):
        def get(url, timeout=0):
            if url.startswith(K.PRIMARY):
                raise OSError("timed out")
            return FakeResp('"179B86F87E4E49C0"')
        msgs = []
        with mock.patch("requests.get", get):
            self.assertEqual(K.pick_base(log=msgs.append, force=True), K.FALLBACK)
        self.assertTrue(msgs)
        with mock.patch("requests.get", lambda url, timeout=0: FakeResp('"179B86F87E4E49C0"')):
            self.assertEqual(K.pick_base(force=True), K.PRIMARY)

    def test_none_answers_returns_main(self):
        with mock.patch("requests.get", side_effect=OSError("down")):
            self.assertEqual(K.pick_base(force=True, log=lambda m: None), K.PRIMARY)


if __name__ == "__main__":
    unittest.main()
