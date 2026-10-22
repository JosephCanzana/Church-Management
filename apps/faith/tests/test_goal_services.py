"""Goal services: create, validation, edit rules, change schedule, cap, archive, refusals, audit privacy."""
import datetime as dt

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.core.services import ServiceError, new_submission_token
from apps.faith import services
from apps.faith.models import Goal, GoalCheckin

from .goal_helpers import TODAY, at_date, audit_dump, cannot_sign_in, goal_data, make_user

DAY = dt.timedelta(days=1)


class CreateAndValidateTests(TestCase):
    """create_goal and clean_goal_data."""

    def setUp(self):
        self.user = make_user()

    def create(self, **over):
        """Create a goal on TODAY."""
        with at_date(TODAY):
            return services.create_goal(self.user, goal_data(**over))

    def test_create_daily_goal(self):
        goal = self.create()
        self.assertEqual((goal.user, goal.status, goal.repeat_type), (self.user, "active", "daily"))
        self.assertIsNone(goal.weekdays)

    def test_create_weekly_goal_sorts_and_dedupes_weekdays(self):
        goal = self.create(repeat_type="weekly", weekdays=[4, 0, 0, 2])
        self.assertEqual(goal.weekdays, [0, 2, 4])

    def test_daily_goal_drops_weekdays(self):
        goal = self.create(weekdays=[1, 2])
        self.assertIsNone(goal.weekdays)

    def test_one_off_goal_is_daily_with_same_end_date(self):
        goal = self.create(end_date=TODAY)
        self.assertEqual(goal.end_date, goal.start_date)

    def test_validation_errors(self):
        bad = [
            {"title": "   "},
            {"repeat_type": "weekly", "weekdays": []},
            {"repeat_type": "monthly"},
            {"end_date": TODAY - DAY},                       # before start and before today
            {"start_date": TODAY + DAY, "end_date": TODAY},  # end before start
            {"start_time": dt.time(9, 0), "end_time": dt.time(8, 0)},
            {"start_time": dt.time(9, 0), "end_time": dt.time(9, 0)},
            {"end_time": dt.time(9, 0)},                     # end without a start
            {"start_date": None},
        ]
        for over in bad:
            with self.subTest(over=over), self.assertRaises(ServiceError):
                self.create(**over)
        self.assertEqual(Goal.objects.count(), 0)

    def test_time_range_and_start_time_alone_are_valid(self):
        self.create(start_time=dt.time(6, 0))
        self.create(title="Run", start_time=dt.time(6, 0), end_time=dt.time(7, 0))
        self.assertEqual(Goal.objects.count(), 2)

    def test_title_whitespace_is_cleaned(self):
        self.assertEqual(self.create(title="  Read   the\tBible ").title, "Read the Bible")

    def test_replayed_form_token_is_refused(self):
        token = new_submission_token()
        with at_date(TODAY):
            services.create_goal(self.user, goal_data(), token=token)
            with self.assertRaises(services.AlreadySaved):
                services.create_goal(self.user, goal_data(), token=token)
        self.assertEqual(Goal.objects.count(), 1)

    def test_cap_of_twenty_active_goals(self):
        for number in range(20):
            self.create(title=f"Goal {number}")
        with self.assertRaises(ServiceError):
            self.create(title="One too many")
        Goal.objects.filter(user=self.user).first().delete()
        self.create(title="Fits again")                      # archived or removed goals free a slot


class EditRuleTests(TestCase):
    """update_goal: what can and cannot change."""

    def setUp(self):
        self.user = make_user()
        with at_date(TODAY):
            self.goal = services.create_goal(self.user, goal_data(start_date=TODAY - 3 * DAY))

    def update(self, **over):
        """Edit the goal on TODAY with its current data plus `over`."""
        base = goal_data(start_date=self.goal.start_date)
        base.update(over)
        with at_date(TODAY):
            return services.update_goal(self.user, self.goal.pk, base)

    def checkin(self, day, done=True):
        """Insert a check-in row directly."""
        return GoalCheckin.objects.create(goal=self.goal, checkin_date=day, done=done)

    def test_title_description_and_times_always_editable(self):
        goal = self.update(title="New", description="d", start_time=dt.time(5, 0), end_time=dt.time(6, 0))
        self.assertEqual((goal.title, goal.start_time), ("New", dt.time(5, 0)))

    def test_nothing_changed_writes_no_audit_row(self):
        before = audit_dump()
        self.update()
        self.assertEqual(before, audit_dump())

    def test_start_cannot_move_later_than_first_checkin(self):
        self.checkin(TODAY - 2 * DAY)
        with self.assertRaises(ServiceError):
            self.update(start_date=TODAY - DAY)
        self.update(start_date=TODAY - 2 * DAY)                # equal to the first check-in is fine
        self.update(start_date=TODAY - 5 * DAY)                # earlier is fine

    def test_end_cannot_be_before_today(self):
        with self.assertRaises(ServiceError):
            self.update(end_date=TODAY - DAY)

    def test_end_cannot_be_before_last_done_checkin(self):
        self.checkin(TODAY)
        with self.assertRaises(ServiceError):
            self.update(end_date=TODAY - DAY)

    def test_repeat_locks_after_first_checkin(self):
        self.update(repeat_type="weekly", weekdays=[0, 1])      # no check-in yet: allowed
        self.checkin(TODAY, done=False)                          # even an unchecked row locks it
        with self.assertRaises(ServiceError):
            self.update(repeat_type="daily", weekdays=None)
        with self.assertRaises(ServiceError):
            self.update(repeat_type="weekly", weekdays=[0, 2])
        self.update(title="Same schedule, new title", repeat_type="weekly", weekdays=[1, 0])

    def test_completed_goal_with_future_end_date_becomes_active_again(self):
        Goal.objects.filter(pk=self.goal.pk).update(
            status="completed", end_date=TODAY - DAY, completed_at=dt.datetime(2026, 3, 3, tzinfo=dt.timezone.utc),
        )
        goal = self.update(end_date=TODAY + 5 * DAY)
        self.assertEqual((goal.status, goal.completed_at), ("active", None))

    def test_completed_goal_stays_completed_when_end_date_unchanged(self):
        Goal.objects.filter(pk=self.goal.pk).update(status="completed", end_date=TODAY - DAY)
        goal = self.update(title="Renamed", end_date=TODAY - DAY)
        self.assertEqual(goal.status, "completed")

    def test_archived_goal_cannot_be_edited(self):
        with at_date(TODAY):
            services.archive_goal(self.user, self.goal.pk)
        with self.assertRaises(ServiceError):
            self.update(title="Nope")


class ChangeScheduleTests(TestCase):
    """change_schedule: end today, start tomorrow, linked."""

    def setUp(self):
        self.user = make_user()
        with at_date(TODAY):
            self.goal = services.create_goal(
                self.user, goal_data(start_date=TODAY - 2 * DAY, start_time=dt.time(6, 30)),
            )
            GoalCheckin.objects.create(goal=self.goal, checkin_date=TODAY - DAY)

    def test_old_goal_ends_today_and_new_goal_starts_tomorrow(self):
        with at_date(TODAY):
            new = services.change_schedule(self.user, self.goal.pk, {"repeat_type": "weekly", "weekdays": [0, 3]})
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.end_date, TODAY)
        self.assertEqual(new.previous_goal_id, self.goal.pk)
        self.assertEqual((new.start_date, new.repeat_type, new.weekdays), (TODAY + DAY, "weekly", [0, 3]))
        self.assertEqual((new.title, new.start_time), (self.goal.title, dt.time(6, 30)))

    def test_same_schedule_is_refused(self):
        with at_date(TODAY), self.assertRaises(ServiceError):
            services.change_schedule(self.user, self.goal.pk, {"repeat_type": "daily", "weekdays": None})

    def test_goal_ending_today_is_refused(self):
        Goal.objects.filter(pk=self.goal.pk).update(end_date=TODAY)
        with at_date(TODAY), self.assertRaises(ServiceError):
            services.change_schedule(self.user, self.goal.pk, {"repeat_type": "weekly", "weekdays": [1]})

    def test_second_change_is_refused_because_the_old_goal_already_ends_today(self):
        with at_date(TODAY):
            services.change_schedule(self.user, self.goal.pk, {"repeat_type": "weekly", "weekdays": [1]})
            with self.assertRaises(ServiceError):
                services.change_schedule(self.user, self.goal.pk, {"repeat_type": "weekly", "weekdays": [2]})
        self.assertEqual(Goal.objects.count(), 2)


class ArchiveRestoreTests(TestCase):
    """archive_goal and restore_goal go through the core helpers."""

    def setUp(self):
        self.user = make_user()
        with at_date(TODAY):
            self.goal = services.create_goal(self.user, goal_data())

    def test_archive_then_restore(self):
        with at_date(TODAY):
            services.archive_goal(self.user, self.goal.pk)
            self.goal.refresh_from_db()
            self.assertEqual(self.goal.status, "archived")
            self.assertIsNotNone(self.goal.archived_at)
            services.archive_goal(self.user, self.goal.pk)       # twice is quiet
            services.restore_goal(self.user, self.goal.pk)
        self.goal.refresh_from_db()
        self.assertEqual((self.goal.status, self.goal.archived_at), ("active", None))

    def test_restore_of_an_ended_goal_is_completed(self):
        Goal.objects.filter(pk=self.goal.pk).update(end_date=TODAY)
        with at_date(TODAY):
            services.archive_goal(self.user, self.goal.pk)
        with at_date(TODAY + 2 * DAY):
            services.restore_goal(self.user, self.goal.pk)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, "completed")
        self.assertIsNotNone(self.goal.completed_at)

    def test_restore_respects_the_cap(self):
        with at_date(TODAY):
            services.archive_goal(self.user, self.goal.pk)
            for number in range(20):
                services.create_goal(self.user, goal_data(title=f"Goal {number}"))
            with self.assertRaises(ServiceError):
                services.restore_goal(self.user, self.goal.pk)


class RefusalAndPrivacyTests(TestCase):
    """Who may act, and what reaches the audit log."""

    def setUp(self):
        self.owner = make_user()
        self.other = make_user()
        with at_date(TODAY):
            self.goal = services.create_goal(self.owner, goal_data())

    def test_people_who_cannot_sign_in_are_refused(self):
        with at_date(TODAY), cannot_sign_in():
            with self.assertRaises(PermissionDenied):
                services.create_goal(self.owner, goal_data())
            with self.assertRaises(PermissionDenied):
                services.set_checkin(self.owner, self.goal.pk, True)

    def test_another_persons_goal_is_refused_like_a_missing_one(self):
        with at_date(TODAY):
            for action in (
                lambda pk: services.update_goal(self.other, pk, goal_data()),
                lambda pk: services.archive_goal(self.other, pk),
                lambda pk: services.restore_goal(self.other, pk),
                lambda pk: services.change_schedule(self.other, pk, {"repeat_type": "weekly", "weekdays": [1]}),
                lambda pk: services.set_checkin(self.other, pk, True),
                lambda pk: services.set_checkin_note(self.other, pk, "hi"),
            ):
                for pk in (self.goal.pk, 999999):
                    with self.subTest(pk=pk), self.assertRaises(PermissionDenied):
                        action(pk)

    def test_audit_rows_never_hold_goal_text(self):
        secret_title, secret_description = "ZXQ-secret-title", "ZXQ-secret-description"
        with at_date(TODAY):
            goal = services.create_goal(self.owner, goal_data(title=secret_title, description=secret_description))
            services.update_goal(self.owner, goal.pk, goal_data(title=secret_title + " 2", description=secret_description))
            new = services.change_schedule(self.owner, goal.pk, {"repeat_type": "weekly", "weekdays": [1]})
            services.set_checkin_note(self.owner, goal.pk, "ZXQ-secret-note")
            services.archive_goal(self.owner, new.pk)
            services.restore_goal(self.owner, new.pk)
        dump = audit_dump()
        self.assertNotIn("ZXQ", dump)
        for action in ("goal.create", "goal.update", "goal.change_schedule", "goal.archive", "goal.restore"):
            self.assertIn(action, dump)
