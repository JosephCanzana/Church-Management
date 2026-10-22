"""goal_streaks and the Today overview."""
import datetime as dt

from django.test import TestCase

from apps.faith import services
from apps.faith.models import Goal, GoalCheckin
from apps.faith.stats import goal_streaks, repeat_summary, time_label, today_overview

from .goal_helpers import TODAY, at_date, goal_data, make_user

DAY = dt.timedelta(days=1)


def days(*offsets):
    """Dates as offsets from TODAY (0 = today, -1 = yesterday)."""
    return {TODAY + offset * DAY for offset in offsets}


def daily(start_offset=-10, **over):
    """An unsaved daily goal that started `start_offset` days from TODAY."""
    return Goal(title="g", start_date=TODAY + start_offset * DAY, repeat_type="daily", **over)


class StreakTests(TestCase):
    """The streak rules, using unsaved goals and explicit done dates."""

    def test_unchecked_today_does_not_break_the_streak(self):
        self.assertEqual(goal_streaks(daily(), TODAY, days(-3, -2, -1)).current, 3)

    def test_checked_today_extends_it(self):
        self.assertEqual(goal_streaks(daily(), TODAY, days(-2, -1, 0)).current, 3)

    def test_a_missed_day_breaks_it(self):
        self.assertEqual(goal_streaks(daily(), TODAY, days(-5, -4, -3, -1)).current, 1)
        self.assertEqual(goal_streaks(daily(), TODAY, days(-5, -4, -3)).current, 0)

    def test_best_streak_survives_a_miss(self):
        result = goal_streaks(daily(), TODAY, days(-9, -8, -7, -6, -4, -3))
        self.assertEqual((result.current, result.best), (0, 4))

    def test_weekly_goal_counts_only_scheduled_days(self):
        # TODAY is a Wednesday. Mon/Wed/Fri goal: Wed 4, Mon 2, Fri 27 Feb.
        goal = Goal(title="g", start_date=TODAY - 14 * DAY, repeat_type="weekly", weekdays=[0, 2, 4])
        done = {dt.date(2026, 2, 25), dt.date(2026, 2, 27), dt.date(2026, 3, 2)}
        self.assertEqual(goal_streaks(goal, TODAY, done).current, 3)       # Tuesday and Thursday do not break it
        self.assertEqual(goal_streaks(goal, TODAY, done - {dt.date(2026, 2, 27)}).current, 1)

    def test_goal_not_started_has_no_streak(self):
        self.assertEqual(tuple(goal_streaks(daily(start_offset=2), TODAY, set())), (0, 0))

    def test_ended_goal_keeps_its_final_streak(self):
        goal = daily(start_offset=-6, end_date=TODAY - 3 * DAY)
        self.assertEqual(tuple(goal_streaks(goal, TODAY, days(-6, -5, -4, -3))), (4, 4))

    def test_goal_with_no_checkins(self):
        self.assertEqual(tuple(goal_streaks(daily(), TODAY, set())), (0, 0))

    def test_unchecked_rows_are_not_counted(self):
        user = make_user()
        with at_date(TODAY):
            goal = services.create_goal(user, goal_data(start_date=TODAY - 2 * DAY))
        GoalCheckin.objects.create(goal=goal, checkin_date=TODAY - DAY, done=True)
        GoalCheckin.objects.create(goal=goal, checkin_date=TODAY - 2 * DAY, done=False)
        self.assertEqual(goal_streaks(goal, TODAY).current, 1)


class LabelTests(TestCase):
    """Small display helpers."""

    def test_labels(self):
        self.assertEqual(repeat_summary(Goal(repeat_type="daily", start_date=TODAY)), "Every day")
        self.assertEqual(repeat_summary(Goal(repeat_type="daily", start_date=TODAY, end_date=TODAY)), "One time")
        self.assertEqual(repeat_summary(Goal(repeat_type="weekly", weekdays=[4, 0])), "Mon, Fri")
        self.assertEqual(time_label(Goal()), "All day")
        self.assertEqual(time_label(Goal(start_time=dt.time(9, 5))), "9:05 AM")
        self.assertEqual(time_label(Goal(start_time=dt.time(9, 0), end_time=dt.time(13, 30))), "9:00 AM to 1:30 PM")


class TodayOverviewTests(TestCase):
    """today_overview groups, orders and counts."""

    def test_grouping_order_and_counts(self):
        user = make_user()
        with at_date(TODAY):
            late = services.create_goal(user, goal_data(title="Late", start_time=dt.time(20, 0)))
            early = services.create_goal(user, goal_data(title="Early", start_time=dt.time(6, 0)))
            services.create_goal(user, goal_data(title="B all day"))
            services.create_goal(user, goal_data(title="A all day"))
            services.create_goal(user, goal_data(title="Mondays", repeat_type="weekly", weekdays=[0]))
            services.set_checkin(user, early.pk, True)
            overview = today_overview(user, TODAY)
        self.assertEqual([r["goal"].title for r in overview["all_day"]], ["A all day", "B all day"])
        self.assertEqual([r["goal"].title for r in overview["timed"]], ["Early", "Late"])
        self.assertEqual([o["goal"].title for o in overview["others"]], ["Mondays"])
        self.assertEqual((overview["total"], overview["done"], overview["percent"]), (4, 1, 25))
        self.assertEqual(overview["others"][0]["hint"], "Next: Mon")

    def test_empty_person(self):
        overview = today_overview(make_user(), TODAY)
        self.assertEqual((overview["total"], overview["percent"], overview["has_goals"]), (0, 0, False))

    def test_other_peoples_goals_and_archived_goals_are_not_shown(self):
        me, other = make_user(), make_user()
        with at_date(TODAY):
            services.create_goal(other, goal_data(title="Theirs"))
            mine = services.create_goal(me, goal_data(title="Mine"))
            services.archive_goal(me, mine.pk)
            overview = today_overview(me, TODAY)
        self.assertEqual((overview["total"], overview["has_goals"]), (0, False))
