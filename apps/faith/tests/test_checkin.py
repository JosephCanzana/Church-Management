"""Check-ins: check, uncheck, idempotence, notes, closed days, refusals."""
import datetime as dt
import inspect

from django.test import TestCase

from apps.core.services import ServiceError
from apps.faith import services
from apps.faith.models import Goal, GoalCheckin

from .goal_helpers import TODAY, at_date, goal_data, make_user

DAY = dt.timedelta(days=1)


class CheckinTests(TestCase):
    """set_checkin and set_checkin_note."""

    def setUp(self):
        self.user = make_user()
        with at_date(TODAY):
            self.goal = services.create_goal(self.user, goal_data())

    def rows(self):
        """All check-in rows of the goal."""
        return GoalCheckin.objects.filter(goal=self.goal)

    def test_check_then_uncheck_keeps_the_row_and_the_note(self):
        with at_date(TODAY):
            services.set_checkin(self.user, self.goal.pk, True)
            services.set_checkin_note(self.user, self.goal.pk, "went well")
            result = services.set_checkin(self.user, self.goal.pk, False)
        row = self.rows().get()
        self.assertEqual((row.done, row.note, result["done"]), (False, "went well", False))

    def test_double_tap_is_idempotent(self):
        with at_date(TODAY):
            first = services.set_checkin(self.user, self.goal.pk, True)
            second = services.set_checkin(self.user, self.goal.pk, True)
        self.assertEqual((first["changed"], second["changed"]), (True, False))
        self.assertEqual(self.rows().count(), 1)

    def test_a_note_alone_creates_an_unchecked_row(self):
        with at_date(TODAY):
            services.set_checkin_note(self.user, self.goal.pk, "thinking about it")
        self.assertEqual((self.rows().get().done, self.rows().get().note), (False, "thinking about it"))

    def test_note_too_long_is_refused(self):
        with at_date(TODAY), self.assertRaises(ServiceError):
            services.set_checkin_note(self.user, self.goal.pk, "x" * (services.NOTE_MAX + 1))

    def test_goal_not_scheduled_today_is_refused(self):
        Goal.objects.filter(pk=self.goal.pk).update(repeat_type="weekly", weekdays=[0])   # Mondays; TODAY is Wednesday
        with at_date(TODAY), self.assertRaises(ServiceError):
            services.set_checkin(self.user, self.goal.pk, True)
        with at_date(TODAY), self.assertRaises(ServiceError):
            services.set_checkin_note(self.user, self.goal.pk, "nope")

    def test_goal_not_started_yet_is_refused(self):
        Goal.objects.filter(pk=self.goal.pk).update(start_date=TODAY + DAY)
        with at_date(TODAY), self.assertRaises(ServiceError):
            services.set_checkin(self.user, self.goal.pk, True)

    def test_archived_and_completed_goals_are_refused(self):
        for status, extra in (("completed", {}), ("archived", {"archived_at": dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc)})):
            Goal.objects.filter(pk=self.goal.pk).update(status=status, **extra)
            with self.subTest(status=status), at_date(TODAY), self.assertRaises(ServiceError):
                services.set_checkin(self.user, self.goal.pk, True)

    def test_no_date_is_ever_accepted(self):
        for function in (services.set_checkin, services.set_checkin_note):
            names = set(inspect.signature(function).parameters)
            self.assertFalse(names & {"date", "day", "checkin_date", "on_date"}, function.__name__)

    def test_midnight_closes_the_day(self):
        with at_date(TODAY):
            services.set_checkin(self.user, self.goal.pk, True)
        with at_date(TODAY + DAY):
            services.set_checkin(self.user, self.goal.pk, False)   # a stale page can only touch the NEW day
        old = self.rows().get(checkin_date=TODAY)
        new = self.rows().get(checkin_date=TODAY + DAY)
        self.assertEqual((old.done, new.done), (True, False))
