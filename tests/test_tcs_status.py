"""TCS BANCS status: free / busy / clash and the next automatic user.   python -m unittest tests.test_tcs_status"""
import datetime as dt
import unittest
from unittest import mock

from core import tcs_status as T


class Status(unittest.TestCase):
    def test_free_busy_clash(self):
        with mock.patch.object(T, "_card_holder", return_value=None), mock.patch.object(T, "_box_holders", return_value=[]), \
                mock.patch.object(T, "_next_users", return_value=[{"name": "X", "at": "15:10", "minutes": 20}]):
            s = T.status()
            self.assertEqual((s["state"], s["next"]["name"]), ("free", "X"))
        card = {"name": "LIVE ALLOCATION CAPTURE", "since": "11:02:10", "elapsed_s": 134, "source": "card"}
        box = {"name": "PROBABLE (TCS button)", "since": "11:03:00", "elapsed_s": 60, "source": "box"}
        with mock.patch.object(T, "_card_holder", return_value=card), mock.patch.object(T, "_box_holders", return_value=[]), \
                mock.patch.object(T, "_next_users", return_value=[]):
            self.assertEqual(T.status()["state"], "busy")
        with mock.patch.object(T, "_card_holder", return_value=card), mock.patch.object(T, "_box_holders", return_value=[box]), \
                mock.patch.object(T, "_next_users", return_value=[]):
            self.assertEqual(T.status()["state"], "clash")

    def test_next_users_never_raises_and_is_sorted(self):
        r = T._next_users(dt.datetime(2026, 11, 4, 9, 0))
        self.assertEqual(r, sorted(r, key=lambda x: x["minutes"]))


if __name__ == "__main__":
    unittest.main()
