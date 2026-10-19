"""Unit tests for core/captcha_solver.py (no network: 2Captcha is replaced by scripted fakes)."""
import tempfile
import unittest
from pathlib import Path

from core import captcha_solver as cs


class FakeResponse:
    """Stands in for requests.Response."""

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class FakeHttp:
    """Returns scripted JSON replies in order and records every (url, payload)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply)


class Clock:
    """Virtual time: sleep() advances it, so a 45 s timeout is tested instantly."""

    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def __call__(self):
        return self.now


def make_client(replies):
    clock = Clock()
    return cs.V2Client("SECRETKEY", http=FakeHttp(replies), sleep=clock.sleep, clock=clock)


def ready(text, task_id=1):
    return [{"errorId": 0, "taskId": task_id}, {"errorId": 0, "status": "ready", "solution": {"text": text}}]


def never_ready(task_id=1, polls=14):
    # With a 4 s first wait and a poll every 3 s, a 45 s timeout is reached after exactly 14 "processing" replies.
    return [{"errorId": 0, "taskId": task_id}] + [{"errorId": 0, "status": "processing"}] * polls


class ClientTests(unittest.TestCase):
    def test_task_is_case_sensitive_with_profile_lengths(self):
        client = make_client(ready("aB3dEf"))
        result = client.solve(b"img", cs.PROFILES["nmass"])
        task = client.http.calls[0][1]["task"]
        self.assertEqual((task["type"], task["case"], task["minLength"], task["maxLength"]),
                         ("ImageToTextTask", True, 6, 6))
        self.assertEqual((result.text, result.task_id), ("aB3dEf", 1))

    def test_timeout_after_45_seconds(self):
        client = make_client(never_ready())
        with self.assertRaises(cs.SolverError) as ctx:
            client.solve(b"img", cs.PROFILES["nmass"], timeout_s=45)
        self.assertEqual(ctx.exception.code, "TIMEOUT")
        self.assertLessEqual(client.clock(), 48)           # gave up at about 45 s, not later

    def test_api_error_carries_code_and_hides_key(self):
        client = make_client([{"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE", "errorDescription": "x"}])
        with self.assertRaises(cs.SolverError) as ctx:
            client.solve(b"img", cs.PROFILES["ncdex"])
        self.assertEqual(ctx.exception.code, "ERROR_ZERO_BALANCE")
        self.assertEqual(client.scrub("a SECRETKEY b"), "a *** b")

    def test_network_error_becomes_solver_error_without_key(self):
        client = make_client([ConnectionError("boom SECRETKEY")])
        with self.assertRaises(cs.SolverError) as ctx:
            client.solve(b"img", cs.PROFILES["nmass"])
        self.assertEqual(ctx.exception.code, "NETWORK")
        self.assertNotIn("SECRETKEY", str(ctx.exception))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self._stats = cs.STATS_FILE
        self._tmp = tempfile.TemporaryDirectory()
        cs.STATS_FILE = Path(self._tmp.name) / "stats.csv"

    def tearDown(self):
        cs.STATS_FILE = self._stats
        self._tmp.cleanup()

    def session(self, replies, portal="nmass", enabled=True, max_tries=5):
        return cs.AutoSession(portal, client=make_client(replies), max_tries=max_tries,
                              enabled_fn=lambda: enabled)

    def test_returns_answer_and_feedback_resets_budget(self):
        s = self.session(ready("aB3dEf", 7) + [{"errorId": 0}])
        self.assertEqual(s.solve(b"i"), "aB3dEf")
        self.assertEqual(s.tries_used, 1)
        self.assertTrue(s.accepted())
        self.assertEqual(s.tries_used, 0)
        self.assertTrue(s.client.http.calls[-1][0].endswith("reportCorrect"))

    def test_wrong_captcha_five_times_then_manual(self):
        replies = []
        for n in range(5):
            replies += ready("abcdef", n) + [{"errorId": 0}]       # answer + reportIncorrect
        s = self.session(replies)
        for _ in range(5):
            self.assertEqual(s.solve(b"i"), "abcdef")
            self.assertTrue(s.rejected())
        self.assertIsNone(s.solve(b"i"))                           # sixth time: a person must answer
        self.assertTrue(s.gave_up)
        self.assertIn("5 times", s.manual_note())

    def test_timeout_resends_the_same_picture_as_a_new_try(self):
        s = self.session(never_ready(1) + ready("aB3dEf", 2))
        self.assertEqual(s.solve(b"i"), "aB3dEf")
        self.assertEqual(s.tries_used, 2)                          # the timed-out try counted
        creates = [c for c in s.client.http.calls if c[0].endswith("createTask")]
        self.assertEqual(len(creates), 2)
        self.assertEqual(creates[0][1]["task"]["body"], creates[1][1]["task"]["body"])

    def test_five_timeouts_hand_over_to_manual(self):
        replies = []
        for n in range(5):
            replies += never_ready(n)
        s = self.session(replies)
        self.assertIsNone(s.solve(b"i"))
        self.assertEqual(s.tries_used, 5)

    def test_accepted_captcha_gives_the_second_captcha_a_fresh_budget(self):
        # NMASS: captcha accepted, then "existing session" logout, then a second captcha.
        s = self.session(ready("aB3dEf", 1) + [{"errorId": 0}] + ready("zZ9yYx", 2))
        s.solve(b"i")
        s.accepted()
        self.assertEqual(s.solve(b"i"), "zZ9yYx")
        self.assertEqual(s.tries_used, 1)

    def test_switch_off_means_manual_without_calling_2captcha(self):
        s = self.session([], enabled=False)
        self.assertIsNone(s.solve(b"i"))
        self.assertEqual(s.client.http.calls, [])

    def test_fatal_error_stops_auto_for_the_login(self):
        s = self.session([{"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE", "errorDescription": ""}])
        self.assertIsNone(s.solve(b"i"))
        self.assertFalse(s.active())
        self.assertIn("unavailable", s.manual_note())

    def test_wrong_shaped_answer_is_retried(self):
        s = self.session(ready("abc", 1) + ready("aB3dEf", 2))     # 3 chars is not an NMASS captcha
        self.assertEqual(s.solve(b"i"), "aB3dEf")
        self.assertEqual(s.tries_used, 2)

    def test_ncdex_answer_is_upper_cased(self):
        s = self.session(ready("637fcf"), portal="ncdex")
        self.assertEqual(s.solve(b"i"), "637FCF")

    def test_rejected_of_a_typed_answer_is_not_automatic(self):
        s = self.session([])
        self.assertFalse(s.rejected())                              # nothing automatic was pending
        self.assertFalse(s.accepted())

    def test_unknown_portal(self):
        with self.assertRaises(ValueError):
            cs.AutoSession("nope")


class SwitchTests(unittest.TestCase):
    def test_switch_file_round_trip_and_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "switch.json"
            self.assertTrue(cs.switch_state(path)["enabled"])        # default on when never touched
            cs.set_enabled(False, "tester", path)
            state = cs.switch_state(path)
            self.assertEqual((state["enabled"], state["by"]), (False, "tester"))
            cs.set_enabled(True, "tester", path)
            self.assertTrue(cs.switch_state(path)["enabled"])


if __name__ == "__main__":
    unittest.main()
