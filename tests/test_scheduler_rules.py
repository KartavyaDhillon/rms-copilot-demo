"""Pure schedule-maths tests (no DB, no processes).  Run from the repo root:

    python -m unittest discover -s tests -t . -v
"""
import datetime as dt
import unittest

from core.scheduler import rules

UTC = dt.timezone.utc
IST = rules.get_tz("Asia/Kolkata")


def ist(y, mo, d, h=0, mi=0, s=0):
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=IST).astimezone(UTC)


def S(rule, **kw):
    base = {"rule": rule, "tz": "Asia/Kolkata", "start_date": None, "end_date": None, "skip_holidays": False}
    base.update(kw)
    return base


def local(u):
    return u.astimezone(IST).strftime("%Y-%m-%d %H:%M")


class SpecificTimes(unittest.TestCase):
    def test_weekday_single_time_skips_weekend(self):
        s = S({"type": "times", "times": ["07:45"], "days": [0, 1, 2, 3, 4]})
        n = rules.next_after(s, ist(2026, 10, 9, 8, 0))            # Friday 08:00
        self.assertEqual(local(n), "2026-10-12 07:45")             # Monday

    def test_before_and_after_the_time_same_day(self):
        s = S({"type": "times", "times": ["07:45"]})
        self.assertEqual(local(rules.next_after(s, ist(2026, 10, 7, 7, 0))), "2026-10-07 07:45")
        self.assertEqual(local(rules.next_after(s, ist(2026, 10, 7, 7, 45))), "2026-10-08 07:45")   # strictly after

    def test_multiple_times_in_one_schedule(self):
        s = S({"type": "times", "times": ["19:30", "07:30", "12:00", "15:30"]})
        ups = rules.upcoming(s, ist(2026, 10, 7, 6, 0), 5)
        self.assertEqual([local(u) for u in ups], ["2026-10-07 07:30", "2026-10-07 12:00", "2026-10-07 15:30",
                                                   "2026-10-07 19:30", "2026-10-08 07:30"])

    def test_weekend_only(self):
        s = S({"type": "times", "times": ["10:00"], "days": [5, 6]})
        ups = rules.upcoming(s, ist(2026, 10, 7, 0, 0), 3)          # Wed
        self.assertEqual([local(u) for u in ups], ["2026-10-10 10:00", "2026-10-11 10:00", "2026-10-17 10:00"])

    def test_timezone_is_respected(self):
        s = S({"type": "times", "times": ["07:45"]})
        self.assertEqual(rules.next_after(s, ist(2026, 10, 7, 0, 0)).strftime("%H:%M"), "02:15")   # 07:45 IST = 02:15 UTC
        try:
            rules.get_tz("Asia/Dubai")
        except ValueError:
            self.skipTest("tzdata not installed")
        s2 = S({"type": "times", "times": ["07:45"]}, tz="Asia/Dubai")
        self.assertEqual(rules.next_after(s2, ist(2026, 10, 7, 0, 0)).strftime("%H:%M"), "03:45")


class Interval(unittest.TestCase):
    RULE = {"type": "interval", "every": 15, "unit": "minutes", "start": "09:00", "end": "17:00", "days": [0, 1, 2, 3, 4]}

    def test_fifteen_minutes_inside_window(self):
        s = S(self.RULE)
        ups = rules.between(s, ist(2026, 10, 7, 0, 0), ist(2026, 10, 7, 23, 59))
        self.assertEqual(len(ups), 33)                              # 09:00 … 17:00 inclusive
        self.assertEqual(local(ups[0]), "2026-10-07 09:00")
        self.assertEqual(local(ups[-1]), "2026-10-07 17:00")

    def test_after_window_goes_to_next_working_day(self):
        s = S(self.RULE)
        self.assertEqual(local(rules.next_after(s, ist(2026, 10, 9, 17, 0))), "2026-10-12 09:00")

    def test_hourly_and_two_hourly(self):
        s = S({"type": "interval", "every": 2, "unit": "hours", "start": "09:00", "end": "17:00"})
        ups = rules.between(s, ist(2026, 10, 7, 0, 0), ist(2026, 10, 7, 23, 59))
        self.assertEqual([local(u)[-5:] for u in ups], ["09:00", "11:00", "13:00", "15:00", "17:00"])
        s1 = S({"type": "interval", "every": 1, "unit": "hours", "start": "09:00", "end": "12:00"})
        self.assertEqual(len(rules.between(s1, ist(2026, 10, 7, 0, 0), ist(2026, 10, 7, 23, 59))), 4)


class Monthly(unittest.TestCase):
    def test_first_of_month(self):
        s = S({"type": "monthly", "day": 1, "times": ["08:30"]})
        ups = rules.upcoming(s, ist(2026, 10, 7), 3)
        self.assertEqual([local(u) for u in ups], ["2026-11-01 08:30", "2026-12-01 08:30", "2027-01-01 08:30"])

    def test_last_day_of_month_handles_february(self):
        s = S({"type": "monthly", "day": "last", "times": ["18:00"]})
        ups = rules.upcoming(s, ist(2027, 1, 31, 19, 0), 2)
        self.assertEqual([local(u) for u in ups], ["2027-02-28 18:00", "2027-03-31 18:00"])

    def test_day_31_only_in_31_day_months(self):
        s = S({"type": "monthly", "day": 31, "times": ["09:00"]})
        ups = rules.upcoming(s, ist(2026, 10, 7), 3)
        self.assertEqual([local(u)[:10] for u in ups], ["2026-10-31", "2026-12-31", "2027-01-31"])   # November skipped


class OnceAndDates(unittest.TestCase):
    def test_arbitrary_dates_run_exactly_once_each(self):
        s = S({"type": "once", "at": ["2026-10-25T09:05", "2026-10-09T11:42", "2026-10-12T15:17"]})
        ups = rules.upcoming(s, ist(2026, 10, 7), 10)
        self.assertEqual([local(u) for u in ups], ["2026-10-09 11:42", "2026-10-12 15:17", "2026-10-25 09:05"])
        self.assertIsNone(rules.next_after(s, ist(2026, 10, 25, 9, 5)))

    def test_end_date_stops_a_recurring_schedule(self):
        s = S({"type": "times", "times": ["07:45"]}, end_date="2026-10-09")
        self.assertEqual(local(rules.next_after(s, ist(2026, 10, 8, 8, 0))), "2026-10-09 07:45")
        self.assertIsNone(rules.next_after(s, ist(2026, 10, 9, 8, 0)))

    def test_start_date_delays_the_first_run(self):
        s = S({"type": "times", "times": ["07:45"]}, start_date="2026-10-20")
        self.assertEqual(local(rules.next_after(s, ist(2026, 10, 7))), "2026-10-20 07:45")


class Holidays(unittest.TestCase):
    def test_market_holiday_is_skipped_when_asked(self):
        hol = lambda d: d == dt.date(2026, 10, 12)
        rule = {"type": "times", "times": ["07:45"], "days": [0, 1, 2, 3, 4]}
        skipped = rules.next_after(S(rule, skip_holidays=True), ist(2026, 10, 9, 8), hol)
        kept = rules.next_after(S(rule, skip_holidays=False), ist(2026, 10, 9, 8), hol)
        self.assertEqual(local(skipped), "2026-10-13 07:45")
        self.assertEqual(local(kept), "2026-10-12 07:45")


class MissedPolicy(unittest.TestCase):
    NOW = ist(2026, 10, 7, 14, 0, 20)

    def test_classify(self):
        due = [ist(2026, 10, 7, 9, 0), ist(2026, 10, 7, 14, 0)]
        on_time, missed = rules.classify(due, self.NOW)
        self.assertEqual(on_time, [due[1]])
        self.assertEqual(missed, [due[0]])

    def test_latest_runs_only_the_newest(self):
        m = [ist(2026, 10, 7, 9), ist(2026, 10, 7, 10), ist(2026, 10, 7, 11)]
        run, skipped = rules.decide_missed("latest", 30, m, self.NOW)
        self.assertEqual(run, m[-1])
        self.assertEqual(skipped, m[:-1])

    def test_skip_runs_nothing(self):
        m = [ist(2026, 10, 7, 9)]
        self.assertEqual(rules.decide_missed("skip", 30, m, self.NOW), (None, m))

    def test_grace_window(self):
        recent = ist(2026, 10, 7, 13, 45)
        old = ist(2026, 10, 7, 9, 0)
        self.assertEqual(rules.decide_missed("grace", 30, [recent], self.NOW)[0], recent)
        self.assertIsNone(rules.decide_missed("grace", 30, [old], self.NOW)[0])

    def test_nothing_missed(self):
        self.assertEqual(rules.decide_missed("latest", 30, [], self.NOW), (None, []))


class Validation(unittest.TestCase):
    def errs(self, rule, **kw):
        return rules.validate_schedule_timing(S(rule, **kw))

    def test_good_rules(self):
        self.assertEqual(self.errs({"type": "times", "times": ["07:45"], "days": [0, 1]}), [])
        self.assertEqual(self.errs({"type": "interval", "every": 15, "unit": "minutes", "start": "09:00", "end": "17:00"}), [])

    def test_bad_rules(self):
        self.assertTrue(self.errs({"type": "times", "times": []}))
        self.assertTrue(self.errs({"type": "times", "times": ["25:00"]}))
        self.assertTrue(self.errs({"type": "times", "times": ["07:45", "07:45"]}))
        self.assertTrue(self.errs({"type": "times", "times": ["07:45"], "days": []}))
        self.assertTrue(self.errs({"type": "interval", "every": 0, "unit": "minutes", "start": "09:00", "end": "17:00"}))
        self.assertTrue(self.errs({"type": "interval", "every": 5, "unit": "minutes", "start": "17:00", "end": "09:00"}))
        self.assertTrue(self.errs({"type": "monthly", "day": 40, "times": ["09:00"]}))
        self.assertTrue(self.errs({"type": "once", "at": []}))
        self.assertTrue(self.errs({"type": "weird"}))
        self.assertTrue(self.errs({"type": "times", "times": ["07:45"]}, tz="Mars/Olympus"))
        self.assertTrue(self.errs({"type": "times", "times": ["07:45"]}, start_date="2026-10-10", end_date="2026-10-01"))


class Wording(unittest.TestCase):
    def test_describe(self):
        d = rules.describe(S({"type": "times", "times": ["07:45"], "days": [0, 1, 2, 3, 4]}))
        self.assertEqual(d, {"trigger": "At 7:45 AM", "days": "Mon–Fri"})
        d = rules.describe(S({"type": "interval", "every": 15, "unit": "minutes", "start": "09:00", "end": "17:00"}))
        self.assertIn("Every 15 minutes", d["trigger"])
        self.assertEqual(d["days"], "Every day")


if __name__ == "__main__":
    unittest.main()
