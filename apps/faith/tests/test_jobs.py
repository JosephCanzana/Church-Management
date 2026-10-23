"""Tests for the goal jobs: complete_ended_goals and send_goal_reminders (Brick 10).

`make_user` and `make_goal` are small local helpers: adjust the User fields to your project's
factory if `create_user` needs more arguments. Push sending is mocked; nothing leaves the process.
"""
from datetime import date, datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.models import SiteSetting
from apps.faith import jobs
from apps.faith.models import Goal, GoalCheckin
from apps.notifications.models import Notification
from apps.accounts.models import Status
from apps.faith.tests.helpers import make_user

MANILA = ZoneInfo("Asia/Manila")
User = get_user_model()

def make_goal(user, **extra):
    """Create an active daily goal that started yesterday."""
    today = timezone.localdate()
    fields = dict(user=user, title="Secret goal title", repeat_type="daily", weekdays=None,
                  start_date=today - timedelta(days=1), status=Goal.Status.ACTIVE)
    fields.update(extra)
    return Goal.objects.create(**fields)


def at(hour, minute=0):
    """Today's date in Manila at hour:minute, as an aware datetime."""
    return datetime.combine(timezone.localdate(), time(hour, minute), tzinfo=MANILA)

class CompleteEndedGoalsTests(TestCase):
    """complete_ended_goals completes each ended goal once and leaves the rest alone."""

    def test_ended_goal_completes_once(self):
        user = make_user()
        ended = make_goal(user, end_date=timezone.localdate() - timedelta(days=1))
        open_ended = make_goal(user, title="Open")
        ends_today = make_goal(user, title="Today", end_date=timezone.localdate())
        self.assertEqual(jobs.complete_ended_goals(), 1)
        self.assertEqual(jobs.complete_ended_goals(), 0)  # second run does nothing
        for goal in (ended, open_ended, ends_today):
            goal.refresh_from_db()
        self.assertEqual(ended.status, Goal.Status.COMPLETED)
        self.assertIsNotNone(ended.completed_at)
        self.assertEqual(open_ended.status, Goal.Status.ACTIVE)
        self.assertEqual(ends_today.status, Goal.Status.ACTIVE)

    def test_audit_row_holds_no_text(self):
        make_goal(make_user(), end_date=timezone.localdate() - timedelta(days=1))
        jobs.complete_ended_goals()
        row = AuditLog.objects.get(action="goal.complete")
        self.assertEqual(row.source, AuditLog.Source.SYSTEM_JOB)
        self.assertNotIn("Secret goal title", str(row.__dict__))

    def test_dry_run_changes_nothing(self):
        goal = make_goal(make_user(), end_date=timezone.localdate() - timedelta(days=1))
        call_command("complete_ended_goals", "--dry-run")
        goal.refresh_from_db()
        self.assertEqual(goal.status, Goal.Status.ACTIVE)


@mock.patch("apps.notifications.services.push_send", create=True)
class GoalReminderTests(TestCase):
    """send_goal_reminders only reaches the right goals, once, with the title in the push only."""

    def setUp(self):
        self.user = make_user()

    def test_goal_just_started_gets_one_reminder(self, _push):
        goal = make_goal(self.user, start_time=time(9, 0))
        self.assertEqual(jobs.send_goal_reminders(at(9, 5)), 1)
        self.assertEqual(jobs.send_goal_reminders(at(9, 10)), 0)  # same key, same day: quiet
        self.assertEqual(Notification.objects.filter(user=self.user, type="goal_reminder").count(), 1)
        self.assertEqual(goal.pk, Notification.objects.get(type="goal_reminder").data["goal_id"])

    def test_title_never_in_notification_data(self, _push):
        make_goal(self.user, start_time=time(9, 0))
        jobs.send_goal_reminders(at(9, 5))
        note = Notification.objects.get(type="goal_reminder")
        self.assertEqual(set(note.data), {"goal_id"})
        self.assertNotIn("Secret goal title", str(note.data))

    def test_push_carries_title_and_goals_link(self, _push):
        make_goal(self.user, start_time=time(9, 0))
        with mock.patch("apps.faith.jobs.notify") as notify:
            jobs.send_goal_reminders(at(9, 5))
        push = notify.call_args.kwargs["push"]
        self.assertEqual(push, {"title": "Goal reminder", "body": 'Time for "Secret goal title"', "url": "/goals/"})

    def test_wrong_times_and_states_are_skipped(self, _push):
        make_goal(self.user, title="No time")
        make_goal(self.user, title="Too early", start_time=time(10, 0))
        make_goal(self.user, title="Too old", start_time=time(8, 0))
        make_goal(self.user, title="Archived", start_time=time(9, 0), status=Goal.Status.ARCHIVED,
                  archived_at=timezone.now())
        make_goal(self.user, title="Not started", start_time=time(9, 0),
                  start_date=timezone.localdate() + timedelta(days=1))
        self.assertEqual(jobs.send_goal_reminders(at(9, 5)), 0)

    def test_done_today_is_skipped(self, _push):
        goal = make_goal(self.user, start_time=time(9, 0))
        GoalCheckin.objects.create(goal=goal, checkin_date=timezone.localdate(), done=True)
        self.assertEqual(jobs.send_goal_reminders(at(9, 5)), 0)

    def test_weekly_goal_on_a_different_weekday_is_skipped(self, _push):
        other = (timezone.localdate().weekday() + 1) % 7
        make_goal(self.user, start_time=time(9, 0), repeat_type="weekly", weekdays=[other])
        self.assertEqual(jobs.send_goal_reminders(at(9, 5)), 0)

    def test_owner_who_cannot_sign_in_is_skipped(self, _push):
        gone = make_user(status=Status.SUSPENDED)
        make_goal(gone, start_time=time(9, 0))
        self.assertEqual(jobs.send_goal_reminders(at(9, 5)), 0)

    def test_switch_off_sends_nothing(self, _push):
        make_goal(self.user, start_time=time(9, 0))
        SiteSetting.objects.create(key="faith_goal_reminders_enabled", value=False)
        self.assertEqual(jobs.send_goal_reminders(at(9, 5)), 0)
        self.assertFalse(Notification.objects.filter(type="goal_reminder").exists())

    def test_just_after_midnight_window_is_empty(self, _push):
        make_goal(self.user, start_time=time(23, 55))
        self.assertEqual(jobs.send_goal_reminders(at(0, 5)), 0)
