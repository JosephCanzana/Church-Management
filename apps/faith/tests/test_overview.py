"""Tests for faith.overview (Brick 4): heatmap states, intensity maths, perfect days, rates, archived goals.

The first block uses plain objects and never touches the database (``SimpleTestCase``); the second block
checks the two database wrappers. ``TODAY`` is a Thursday, so the current week runs Monday 19 Oct to Sunday 25 Oct.
"""
from datetime import date, time, timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from apps.faith import overview

TODAY = date(2026, 10, 22)  # Thursday


def goal(goal_id=1, start=date(2026, 10, 15), end=None, repeat="daily", weekdays=None, title="Goal", start_time=None,
         end_time=None):
    """Return a plain stand-in for a Goal row with just the fields the overview reads."""
    return SimpleNamespace(
        id=goal_id, title=title, start_date=start, end_date=end, repeat_type=repeat, weekdays=weekdays or [],
        start_time=start_time, end_time=end_time, status="active",
    )


def d(day):
    """Return a date in October 2026."""
    return date(2026, 10, day)


class HeatmapDaysTests(SimpleTestCase):
    """The 84-day window."""

    def test_window_is_twelve_whole_weeks_ending_this_sunday(self):
        days = overview.heatmap_days(TODAY)
        self.assertEqual(len(days), 84)
        self.assertEqual(days[0], date(2026, 8, 3))
        self.assertEqual(days[0].weekday(), 0)
        self.assertEqual(days[-1], date(2026, 10, 25))
        self.assertEqual(days[-1].weekday(), 6)
        self.assertTrue(all(b - a == timedelta(days=1) for a, b in zip(days, days[1:])))


class GoalCellStateTests(SimpleTestCase):
    """Each day of one goal is done, missed, off, open or future."""

    def setUp(self):
        self.g = goal(start=d(15))
        self.done = {d(15), d(16)}

    def cells(self, done=None, today=TODAY):
        ov = overview.build_goal_overview(self.g, self.done if done is None else done, today)
        return {c["date"]: c for c in ov["heatmap"]}

    def test_states(self):
        cells = self.cells()
        self.assertEqual(cells[d(16)]["state"], "done")
        self.assertEqual(cells[d(17)]["state"], "missed")
        self.assertEqual(cells[d(10)]["state"], "off")          # before the goal started
        self.assertEqual(cells[d(22)]["state"], "open")         # today, not ticked
        self.assertEqual(cells[d(23)]["state"], "future")

    def test_today_ticked_is_done(self):
        self.assertEqual(self.cells(done={d(22)})[d(22)]["state"], "done")

    def test_unscheduled_weekday_is_off(self):
        self.g = goal(start=d(5), repeat="weekly", weekdays=[0])  # Mondays only
        cells = self.cells(done=set())
        self.assertEqual(cells[d(19)]["state"], "missed")       # a Monday
        self.assertEqual(cells[d(20)]["state"], "off")          # a Tuesday

    def test_every_cell_has_a_text_label(self):
        cells = self.cells()
        self.assertEqual(cells[d(16)]["label"], "Fri 16 Oct: done")
        self.assertEqual(cells[d(17)]["label"], "Sat 17 Oct: missed")
        self.assertEqual(cells[d(22)]["label"], "Thu 22 Oct: still open today")
        self.assertTrue(all(c["label"] for c in cells.values()))

    def test_ended_goal_is_off_after_its_end_date(self):
        self.g = goal(start=d(15), end=d(18))
        cells = self.cells(done=set())
        self.assertEqual(cells[d(18)]["state"], "missed")
        self.assertEqual(cells[d(19)]["state"], "off")


class GoalStreakAndRateTests(SimpleTestCase):
    """Streaks, counts and the completion rate of one goal."""

    def test_unchecked_today_does_not_break_the_streak(self):
        ov = overview.build_goal_overview(goal(start=d(15)), {d(20), d(21)}, TODAY)
        self.assertEqual(ov["streak"], {"current": 2, "best": 2})

    def test_ticking_today_extends_it(self):
        ov = overview.build_goal_overview(goal(start=d(15)), {d(20), d(21), d(22)}, TODAY)
        self.assertEqual(ov["streak"]["current"], 3)

    def test_missed_day_resets_and_best_is_kept(self):
        done = {d(15), d(16), d(17), d(18), d(20), d(21)}       # 19 missed
        ov = overview.build_goal_overview(goal(start=d(15)), done, TODAY)
        self.assertEqual(ov["streak"], {"current": 2, "best": 4})

    def test_weekly_goal_counts_scheduled_days_only(self):
        g = goal(start=d(12), repeat="weekly", weekdays=[0, 2])  # Mon and Wed
        ov = overview.build_goal_overview(g, {d(12), d(14), d(19), d(21)}, TODAY)
        self.assertEqual(ov["streak"], {"current": 4, "best": 4})
        self.assertEqual((ov["done"], ov["expected"]), (4, 4))

    def test_open_today_is_not_expected(self):
        ov = overview.build_goal_overview(goal(start=d(20)), {d(20)}, TODAY)   # 20, 21, 22(open)
        self.assertEqual((ov["done"], ov["expected"]), (1, 2))
        self.assertEqual(ov["rate"], 50)

    def test_rate_rounds_half_up(self):
        self.assertEqual(overview._rate(1, 8), 13)   # 12.5
        self.assertEqual(overview._rate(1, 3), 33)
        self.assertEqual(overview._rate(2, 3), 67)
        self.assertIsNone(overview._rate(0, 0))

    def test_goal_with_no_checkins(self):
        ov = overview.build_goal_overview(goal(start=d(15)), set(), TODAY)
        self.assertFalse(ov["has_checkins"])
        self.assertEqual((ov["done"], ov["expected"], ov["rate"]), (0, 7, 0))
        self.assertEqual(ov["streak"], {"current": 0, "best": 0})
        self.assertEqual(ov["ring"]["pct"], 0)

    def test_goal_that_starts_today_has_nothing_expected_yet(self):
        ov = overview.build_goal_overview(goal(start=TODAY), set(), TODAY)
        self.assertEqual((ov["expected"], ov["rate"]), (0, None))
        self.assertEqual(ov["ring"]["text"], "-")

    def test_future_goal_is_all_zero(self):
        ov = overview.build_goal_overview(goal(start=d(30)), set(), TODAY)
        self.assertEqual((ov["done"], ov["expected"], ov["rate"]), (0, 0, None))

    def test_ended_goal_counts_only_up_to_its_end(self):
        ov = overview.build_goal_overview(goal(start=d(10), end=d(12)), {d(10), d(11), d(12)}, TODAY)
        self.assertEqual((ov["done"], ov["expected"], ov["rate"]), (3, 3, 100))


class WeekBarTests(SimpleTestCase):
    """The last eight weeks."""

    def test_eight_weeks_oldest_first_ending_this_week(self):
        ov = overview.build_goal_overview(goal(start=d(15)), {d(15)}, TODAY)
        bars = ov["weeks"]
        self.assertEqual(len(bars), 8)
        self.assertEqual(bars[-1]["label"], "Oct 19")
        self.assertEqual(bars[0]["label"], "Aug 31")

    def test_week_values(self):
        # Week of 12 Oct: goal started 12 Oct, 4 of 7 done. This week: 19, 20, 21 done, 22 open.
        done = {d(12), d(13), d(14), d(15), d(19), d(20), d(21)}
        ov = overview.build_goal_overview(goal(start=d(12)), done, TODAY)
        last, before = ov["weeks"][-1], ov["weeks"][-2]
        self.assertEqual((before["short"], before["display"], before["pct"]), ("4/7", "4 of 7 done", 57))
        self.assertEqual((last["short"], last["pct"]), ("3/3", 100))

    def test_empty_week_and_tiny_bar(self):
        done = {d(12)}
        ov = overview.build_goal_overview(goal(start=d(12)), done, TODAY)
        self.assertEqual(ov["weeks"][0]["display"], "nothing scheduled")
        self.assertEqual(ov["weeks"][0]["pct"], 0)
        self.assertEqual(ov["weeks"][-2]["pct"], 14)    # 1 of 7
        tiny = overview._week_bars([(d(12), True)] + [(d(12) + timedelta(days=i), False) for i in range(1, 6)], TODAY)
        self.assertEqual(tiny[-2]["pct"], 17)
        many = [(d(12), True)] + [(d(12) + timedelta(days=i % 7), False) for i in range(60)]
        self.assertGreaterEqual(overview._week_bars(many, TODAY)[-2]["pct"], overview.MIN_BAR_PERCENT)


class BestWeekdayTests(SimpleTestCase):
    """Best weekday needs a sample and at least one done."""

    def test_picks_the_best_rate(self):
        # Mondays 5, 12, 19 all done; Tuesdays 6, 13, 20 none done.
        g = goal(start=d(5), repeat="weekly", weekdays=[0, 1])
        ov = overview.build_goal_overview(g, {d(5), d(12), d(19)}, TODAY)
        self.assertEqual(ov["best_weekday"], {"name": "Monday", "rate": 100, "done": 3, "expected": 3})

    def test_tie_goes_to_the_earlier_weekday(self):
        g = goal(start=d(5), repeat="weekly", weekdays=[0, 1])
        ov = overview.build_goal_overview(g, {d(5), d(12), d(6), d(13)}, TODAY)
        self.assertEqual(ov["best_weekday"]["name"], "Monday")

    def test_not_enough_data(self):
        g = goal(start=d(19), repeat="weekly", weekdays=[0])
        self.assertIsNone(overview.build_goal_overview(g, {d(19)}, TODAY)["best_weekday"])   # 1 settled day only
        self.assertIsNone(overview.build_goal_overview(goal(start=d(15)), set(), TODAY)["best_weekday"])  # none done


class MonthGridTests(SimpleTestCase):
    """The month grid for the current month."""

    def test_october_2026(self):
        ov = overview.build_goal_overview(goal(start=d(15)), {d(15)}, TODAY)
        month = ov["month"]
        self.assertEqual(month["label"], "October 2026")
        self.assertEqual(len(month["weeks"]), 5)
        self.assertTrue(all(len(week) == 7 for week in month["weeks"]))
        first_week = month["weeks"][0]
        self.assertEqual([c["state"] for c in first_week[:3]], ["pad", "pad", "pad"])   # 28-30 Sep
        self.assertEqual(first_week[3]["number"], 1)
        self.assertEqual(month["weeks"][-1][-1]["state"], "pad")                      # 1 Nov
        flat = {c["date"]: c for week in month["weeks"] for c in week}
        self.assertEqual(flat[d(15)]["state"], "done")
        self.assertEqual(flat[d(22)]["state"], "open")
        self.assertEqual(flat[d(30)]["state"], "future")


class GlobalTests(SimpleTestCase):
    """All goals together."""

    def cells(self, goals, done_by_goal):
        ov = overview.build_global(goals, done_by_goal, TODAY)
        return ov, {c["date"]: c for c in ov["heatmap"]}

    def test_empty_user_returns_zeros(self):
        ov, cells = self.cells([], {})
        self.assertFalse(ov["has_goals"])
        self.assertEqual(ov["perfect"], {"current": 0, "best": 0, "total": 0})
        self.assertEqual((ov["week"]["rate"], ov["month"]["rate"]), (None, None))
        self.assertTrue(all(c["state"] in ("off", "future") for c in cells.values()))

    def test_intensity_steps(self):
        goals = [goal(goal_id=i, start=d(15)) for i in (1, 2, 3, 4)]
        # 16: none, 17: 1 of 4, 18: 2 of 4, 19: 3 of 4, 20: 4 of 4
        done = {
            1: {d(17), d(18), d(19), d(20)},
            2: {d(18), d(19), d(20)},
            3: {d(19), d(20)},
            4: {d(20)},
        }
        _ov, cells = self.cells(goals, done)
        self.assertEqual(cells[d(16)]["state"], "missed")
        self.assertEqual((cells[d(17)]["state"], cells[d(17)]["level"]), ("partial", 1))   # 25%
        self.assertEqual((cells[d(18)]["state"], cells[d(18)]["level"]), ("partial", 2))   # 50%
        self.assertEqual((cells[d(19)]["state"], cells[d(19)]["level"]), ("partial", 3))   # 75%
        self.assertEqual(cells[d(20)]["state"], "perfect")
        self.assertEqual(cells[d(19)]["label"], "Mon 19 Oct: 3 of 4 done")

    def test_third_boundaries(self):
        goals = [goal(goal_id=i, start=d(15)) for i in (1, 2, 3)]
        _ov, cells = self.cells(goals, {1: {d(16), d(17)}, 2: {d(17)}})
        self.assertEqual(cells[d(16)]["level"], 1)   # 1 of 3 = 33%
        self.assertEqual(cells[d(17)]["level"], 2)   # 2 of 3 = 67%

    def test_today_states(self):
        goals = [goal(goal_id=1, start=d(15)), goal(goal_id=2, start=d(15))]
        _ov, cells = self.cells(goals, {})
        self.assertEqual(cells[d(22)]["state"], "open")
        _ov, cells = self.cells(goals, {1: {d(22)}})
        self.assertEqual(cells[d(22)]["state"], "partial")

    def test_perfect_day_streaks(self):
        goals = [goal(goal_id=1, start=d(18)), goal(goal_id=2, start=d(18))]
        done = {1: {d(18), d(19), d(20), d(21), d(22)}, 2: {d(18), d(19), d(21)}}
        ov, _cells = self.cells(goals, done)
        # 18 perfect, 19 perfect, 20 partial, 21 perfect, 22 partial (open, skipped)
        self.assertEqual(ov["perfect"], {"current": 1, "best": 2, "total": 3})

    def test_finishing_today_adds_to_the_streak(self):
        goals = [goal(goal_id=1, start=d(21))]
        ov, _cells = self.cells(goals, {1: {d(21), d(22)}})
        self.assertEqual(ov["perfect"]["current"], 2)

    def test_days_with_nothing_scheduled_do_not_break_a_streak(self):
        g = goal(start=d(12), repeat="weekly", weekdays=[0, 2])
        ov, _cells = self.cells([g], {1: {d(12), d(14), d(19), d(21)}})
        self.assertEqual(ov["perfect"], {"current": 4, "best": 4, "total": 4})

    def test_rates_use_the_open_today_rule(self):
        goals = [goal(goal_id=1, start=d(20)), goal(goal_id=2, start=d(20))]
        ov, _cells = self.cells(goals, {1: {d(20), d(21), d(22)}, 2: {d(20)}})
        # 20: 2/2, 21: 1/2, 22: goal 1 done, goal 2 still open (not expected) -> 4 of 5
        self.assertEqual(ov["week"], {"done": 4, "expected": 5, "rate": 80})
        self.assertEqual(ov["month"], ov["week"])

    def test_week_window_is_seven_days(self):
        g = goal(start=d(1))
        ov, _cells = self.cells([g], {1: {d(1), d(2), d(3)}})
        self.assertEqual(ov["week"]["done"], 0)            # 16 to 22 Oct: nothing done
        self.assertEqual(ov["week"]["expected"], 6)        # today is open
        self.assertEqual(ov["month"]["done"], 3)           # 23 Sep to 22 Oct holds 1-3 Oct
        self.assertEqual(ov["month"]["expected"], 21)      # 1 to 21 Oct; today is open

    def test_completed_goal_still_counts(self):
        g = goal(start=d(15), end=d(17))
        ov, cells = self.cells([g], {1: {d(15), d(16), d(17)}})
        self.assertEqual(ov["perfect"]["total"], 3)
        self.assertEqual(cells[d(18)]["state"], "off")


class ActivityTests(SimpleTestCase):
    """The opened and read strips."""

    streaks = {"open": {"current": 3, "best": 9}, "read": {"current": 0, "best": 2}}

    def test_rows(self):
        rows = [(d(20), True, 0), (d(21), True, 2), (d(22), True, 1)]
        open_row, read_row = overview.build_activity(rows, self.streaks, TODAY)
        self.assertEqual((open_row["label"], read_row["label"]), ("Opened the app", "Read the Bible"))
        self.assertEqual((open_row["days"], read_row["days"]), (3, 2))
        self.assertEqual(len(open_row["cells"]), overview.ACTIVITY_DAYS)
        self.assertEqual(open_row["cells"][-1]["date"], TODAY)
        self.assertEqual((open_row["current"], open_row["best"]), (3, 9))

    def test_today_not_yet_is_open_not_missed(self):
        _open_row, read_row = overview.build_activity([(d(21), True, 1)], self.streaks, TODAY)
        self.assertEqual(read_row["cells"][-1]["state"], "open")
        self.assertEqual(read_row["cells"][-2]["state"], "done")
        self.assertEqual(read_row["cells"][-3]["state"], "missed")


class TodayWidgetTests(SimpleTestCase):
    """The dashboard goals widget."""

    def test_counts_and_order(self):
        goals = [
            goal(goal_id=1, title="Zeta", start_time=time(18, 0)),
            goal(goal_id=2, title="Beta"),
            goal(goal_id=3, title="Alpha", start_time=time(6, 30)),
            goal(goal_id=4, title="Gamma"),
            goal(goal_id=5, title="Done one"),
        ]
        out = overview.build_today(goals, {5})
        self.assertEqual((out["total"], out["done"], out["remaining"]), (5, 1, 4))
        self.assertEqual([g["title"] for g in out["next_up"]], ["Beta", "Gamma", "Alpha"])  # all-day first, then by time
        self.assertEqual(out["more"], 1)
        self.assertFalse(out["all_done"])
        self.assertEqual(out["ring"]["pct"], 20)
        self.assertEqual(out["next_up"][2]["when"], "6:30 AM")

    def test_all_done_and_none(self):
        self.assertTrue(overview.build_today([goal()], {1})["all_done"])
        empty = overview.build_today([], set())
        self.assertEqual((empty["total"], empty["all_done"], empty["next_up"]), (0, False, []))
        self.assertEqual(empty["ring"]["text"], "-")


class TextHelperTests(SimpleTestCase):
    """Small text helpers."""

    def test_repeat_and_when(self):
        self.assertEqual(overview.repeat_summary(goal()), "Every day")
        self.assertEqual(overview.repeat_summary(goal(start=d(15), end=d(15))), "One time")
        self.assertEqual(overview.repeat_summary(goal(repeat="weekly", weekdays=[2, 0])), "Weekly: Mon, Wed")
        self.assertEqual(overview.when_summary(goal()), "All day")
        self.assertEqual(overview.when_summary(goal(start_time=time(0, 5))), "12:05 AM")
        self.assertEqual(overview.when_summary(goal(start_time=time(13, 0), end_time=time(14, 30))), "1:00 PM - 2:30 PM")

    def test_ring(self):
        self.assertEqual(overview.make_ring(None)["text"], "-")
        self.assertEqual(overview.make_ring(100)["dash"], "251.33")
        self.assertEqual(overview.make_ring(0)["dash"], "0.00")
        self.assertEqual(overview.make_ring(150)["pct"], 100)


class DatabaseWrapperTests(TestCase):
    """The two wrappers read the right rows: archived goals out, completed goals in, notes never read.

    ``make_goal`` follows the plan's description of the Goal model.
    """

    def setUp(self):
        from apps.accounts.models import Extension, PersonType, Role, Status, User
        extension = Extension.objects.get_or_create(name="Test Extension")[0]
        # Same arguments the accounts tests use (no email, so the email_verified_pair constraint is satisfied).
        self.user = User.objects.create_user(
            password="pw-12345-pw", first_name="Juan", last_name="Dela Cruz", role=Role.MEMBER,
            extension=extension, person_type=PersonType.MEMBER_ACCOUNT, status=Status.ACTIVE,
            must_change_password=False,
        )

    def make_goal(self, title="Pray", status="active", start=date(2026, 10, 15), **kw):
        from django.utils import timezone

        from apps.faith.models import Goal
        if status == "archived":
            kw.setdefault("archived_at", timezone.now())    # goal_archived_matches_status needs both together
        return Goal.objects.create(user=self.user, title=title, start_date=start, repeat_type="daily",
                                   status=status, **kw)

    def test_archived_excluded_completed_included(self):
        from apps.faith.models import GoalCheckin
        active = self.make_goal("Active")
        completed = self.make_goal("Completed", status="completed", end_date=date(2026, 10, 17))
        archived = self.make_goal("Archived", status="archived")
        for g in (active, completed, archived):
            GoalCheckin.objects.create(goal=g, checkin_date=date(2026, 10, 16), done=True)
        ov = overview.global_overview(self.user, TODAY)
        self.assertEqual(ov["goal_count"], 2)
        cells = {c["date"]: c for c in ov["heatmap"]}
        self.assertEqual(cells[date(2026, 10, 16)]["label"], "Fri 16 Oct: 2 of 2 done")

    def test_unticked_rows_and_notes_do_not_count(self):
        from apps.faith.models import GoalCheckin
        g = self.make_goal()
        GoalCheckin.objects.create(goal=g, checkin_date=date(2026, 10, 16), done=False, note="private words")
        ov = overview.goal_overview(g, TODAY)
        self.assertEqual(ov["done"], 0)
        self.assertNotIn("private words", repr(ov))
        self.assertNotIn("private words", repr(overview.global_overview(self.user, TODAY)))

    def test_goal_overview_uses_one_query(self):
        g = self.make_goal()
        with self.assertNumQueries(1):
            overview.goal_overview(g, TODAY)

    def test_user_with_no_goals(self):
        ov = overview.global_overview(self.user, TODAY)
        self.assertFalse(ov["has_goals"])
        self.assertEqual(ov["perfect"]["total"], 0)
        self.assertEqual(len(ov["activity"]), 2)

    def test_today_summary_only_active_goals_scheduled_today(self):
        from apps.faith.models import GoalCheckin
        a = self.make_goal("A")
        self.make_goal("Done", status="completed", end_date=date(2026, 10, 17))
        self.make_goal("Future", start=date(2026, 10, 30))
        GoalCheckin.objects.create(goal=a, checkin_date=TODAY, done=True)
        out = overview.today_summary(self.user, TODAY)
        self.assertEqual((out["total"], out["done"]), (1, 1))
        self.assertTrue(out["all_done"])