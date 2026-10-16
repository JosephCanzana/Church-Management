"""Brick 13: event reminders (due_reminders, send_reminders, the send_event_reminders command)."""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Status
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.theming.models import UserSettings

from .. import services
from ..models import EventReminder
from .helpers import archive_user, build_world, in_days, make_event


def reminders_for(user):
    return Notification.objects.filter(user=user, type=services.EVENT_REMINDER)


def set_lead(user, minutes=None, enabled=True):
    values = {"event_reminders_enabled": enabled}
    if minutes is not None:
        values["event_reminder_minutes"] = minutes
    UserSettings.objects.filter(user=user).update(**values)


class ReminderTests(TestCase):
    def setUp(self):
        self.w = build_world()

    def test_default_one_day_reminder(self):
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        result = services.send_reminders()
        self.assertEqual(result["events"], 1)
        self.assertEqual(reminders_for(self.w.member_a).count(), 1)

    def test_not_before_the_lead_time(self):
        make_event(self.w.ext_a, start_at=in_days(3))
        self.assertEqual(services.send_reminders(), {"events": 0, "reminders": 0})
        self.assertEqual(reminders_for(self.w.member_a).count(), 0)

    def test_running_twice_sends_nothing_new(self):
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        again = services.send_reminders()
        self.assertEqual(again, {"events": 0, "reminders": 0})
        self.assertEqual(reminders_for(self.w.member_a).count(), 1)
        self.assertEqual(EventReminder.objects.filter(user=self.w.member_a).count(), 1)

    def test_due_reminders_does_not_send(self):
        event = make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        due = services.due_reminders()
        self.assertEqual([event_id for event_id, _ in due], [event.pk])
        self.assertEqual(Notification.objects.count(), 0)

    def test_each_person_uses_their_own_lead(self):
        set_lead(self.w.member_a, 60)
        set_lead(self.w.member_a2, 180)
        make_event(self.w.ext_a, start_at=timezone.now() + timedelta(hours=2, minutes=30))
        services.send_reminders()
        self.assertEqual(reminders_for(self.w.member_a).count(), 0)   # 1 hour not reached
        self.assertEqual(reminders_for(self.w.member_a2).count(), 1)  # 3 hours reached

    def test_one_hour_lead_arrives_when_reached(self):
        set_lead(self.w.member_a, 60)
        make_event(self.w.ext_a, start_at=timezone.now() + timedelta(minutes=59))
        services.send_reminders()
        self.assertEqual(reminders_for(self.w.member_a).count(), 1)

    def test_off_is_skipped(self):
        set_lead(self.w.member_a, enabled=False)
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        self.assertEqual(reminders_for(self.w.member_a).count(), 0)
        self.assertEqual(reminders_for(self.w.member_a2).count(), 1)

    def test_cancelled_event_sends_nothing(self):
        event = make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.cancel_event(self.w.coord_a, event)
        Notification.objects.all().delete()
        self.assertEqual(services.send_reminders(), {"events": 0, "reminders": 0})

    def test_started_event_sends_nothing(self):
        make_event(self.w.ext_a, start_at=timezone.now() - timedelta(minutes=5),
                   end_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(services.send_reminders(), {"events": 0, "reminders": 0})

    def test_rescheduled_event_reminds_again(self):
        event = make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        event.start_at = in_days(0, hours=23)
        event.save(update_fields=["start_at"])
        result = services.send_reminders()
        self.assertEqual(result["events"], 1)
        self.assertEqual(reminders_for(self.w.member_a).count(), 2)

    def test_extension_event_reaches_only_that_extension(self):
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        for person in (self.w.member_a, self.w.member_a2, self.w.coord_a):
            self.assertEqual(reminders_for(person).count(), 1)
        for person in (self.w.member_b, self.w.coord_b, self.w.admin, self.w.super):
            self.assertEqual(reminders_for(person).count(), 0)

    def test_global_event_reaches_every_active_person(self):
        make_event(None, start_at=in_days(0, hours=20))
        services.send_reminders()
        for person in (self.w.member_a, self.w.member_b, self.w.coord_b, self.w.admin, self.w.super):
            self.assertEqual(reminders_for(person).count(), 1)

    def test_suspended_and_archived_are_skipped(self):
        self.w.member_a.status = Status.SUSPENDED
        self.w.member_a.save(update_fields=["status"])
        archive_user(self.w.member_a2)
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        self.assertEqual(reminders_for(self.w.member_a).count(), 0)
        self.assertEqual(reminders_for(self.w.member_a2).count(), 0)
        self.assertEqual(reminders_for(self.w.coord_a).count(), 1)

    def test_bell_item_holds_only_title_and_timing(self):
        event = make_event(self.w.ext_a, title="Prayer night", start_at=in_days(0, hours=20))
        services.send_reminders()
        item = reminders_for(self.w.member_a).get()
        self.assertEqual(item.entity_type, "event")
        self.assertEqual(item.entity_id, event.pk)
        self.assertEqual(set(item.data), {"title", "lead_minutes", "starts_at"})

    def test_job_logs_counts_only_and_only_when_something_was_sent(self):
        services.send_reminders()
        self.assertFalse(AuditLog.objects.filter(action="event.reminders_sent").exists())
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        entry = AuditLog.objects.get(action="event.reminders_sent")
        self.assertEqual(entry.source, AuditLog.Source.SYSTEM_JOB)
        self.assertIsNone(entry.actor_id)
        self.assertEqual(entry.after, {"events": 1, "reminders": 3})


class CommandTests(TestCase):
    def test_command_sends_and_dry_run_does_not(self):
        w = build_world()
        make_event(w.ext_a, start_at=in_days(0, hours=20))
        out = StringIO()
        call_command("send_event_reminders", "--dry-run", stdout=out)
        self.assertIn("dry run", out.getvalue())
        self.assertEqual(Notification.objects.count(), 0)
        call_command("send_event_reminders", stdout=out)
        self.assertEqual(Notification.objects.filter(type=services.EVENT_REMINDER).count(), 3)
