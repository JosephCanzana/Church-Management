"""Tests for faith.schedule (pure functions, no database)."""
from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.faith.schedule import is_scheduled, scheduled_dates


def goal(repeat="daily", start=date(2026, 3, 2), end=None, weekdays=None):
    """Build a goal-like object; the schedule code only reads these four fields."""
    return SimpleNamespace(repeat_type=repeat, start_date=start, end_date=end, weekdays=weekdays)


class IsScheduledTests(SimpleTestCase):
    """is_scheduled: window and weekday rules."""

    def test_daily_every_day_in_window(self):
        g = goal(end=date(2026, 3, 10))
        for day in range(2, 11):
            self.assertTrue(is_scheduled(g, date(2026, 3, day)))

    def test_before_start_is_not_scheduled(self):
        self.assertFalse(is_scheduled(goal(), date(2026, 3, 1)))

    def test_start_day_is_scheduled(self):
        self.assertTrue(is_scheduled(goal(), date(2026, 3, 2)))

    def test_after_end_is_not_scheduled_but_end_day_is(self):
        g = goal(end=date(2026, 3, 5))
        self.assertTrue(is_scheduled(g, date(2026, 3, 5)))
        self.assertFalse(is_scheduled(g, date(2026, 3, 6)))

    def test_no_end_date_never_ends(self):
        self.assertTrue(is_scheduled(goal(end=None), date(2040, 1, 1)))

    def test_one_off_goal_is_a_daily_goal_with_same_start_and_end(self):
        g = goal(start=date(2026, 3, 4), end=date(2026, 3, 4))
        self.assertEqual(scheduled_dates(g, date(2026, 3, 1), date(2026, 3, 10)), [date(2026, 3, 4)])

    def test_weekly_only_on_chosen_weekdays(self):
        # 2026-03-02 is a Monday. Monday (0) and Thursday (3).
        g = goal(repeat="weekly", weekdays=[0, 3])
        self.assertTrue(is_scheduled(g, date(2026, 3, 2)))    # Mon
        self.assertFalse(is_scheduled(g, date(2026, 3, 3)))   # Tue
        self.assertTrue(is_scheduled(g, date(2026, 3, 5)))    # Thu

    def test_weekday_zero_is_monday(self):
        g = goal(repeat="weekly", weekdays=[0])
        self.assertEqual(date(2026, 3, 2).weekday(), 0)
        self.assertTrue(is_scheduled(g, date(2026, 3, 2)))
        self.assertFalse(is_scheduled(g, date(2026, 3, 8)))   # Sunday (6)

    def test_weekly_with_no_weekdays_is_never_due(self):
        self.assertFalse(is_scheduled(goal(repeat="weekly", weekdays=None), date(2026, 3, 2)))

    def test_weekly_still_respects_window(self):
        g = goal(repeat="weekly", weekdays=[0], end=date(2026, 3, 9))
        self.assertTrue(is_scheduled(g, date(2026, 3, 9)))    # Monday on the end day
        self.assertFalse(is_scheduled(g, date(2026, 3, 16)))  # Monday after the end

    def test_leap_day_is_just_a_day(self):
        g = goal(start=date(2028, 2, 28))
        self.assertTrue(is_scheduled(g, date(2028, 2, 29)))


class ScheduledDatesTests(SimpleTestCase):
    """scheduled_dates: inclusive range, oldest first."""

    def test_range_is_inclusive_and_ordered(self):
        g = goal()
        days = scheduled_dates(g, date(2026, 3, 2), date(2026, 3, 4))
        self.assertEqual(days, [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)])

    def test_clipped_to_goal_window(self):
        g = goal(start=date(2026, 3, 3), end=date(2026, 3, 4))
        self.assertEqual(
            scheduled_dates(g, date(2026, 3, 1), date(2026, 3, 10)),
            [date(2026, 3, 3), date(2026, 3, 4)],
        )

    def test_weekly_dates(self):
        g = goal(repeat="weekly", weekdays=[0])
        self.assertEqual(
            scheduled_dates(g, date(2026, 3, 1), date(2026, 3, 16)),
            [date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 16)],
        )

    def test_end_before_start_is_empty(self):
        self.assertEqual(scheduled_dates(goal(), date(2026, 3, 5), date(2026, 3, 4)), [])
