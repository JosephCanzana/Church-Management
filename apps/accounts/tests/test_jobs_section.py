"""Tests for the Site settings Jobs and Retention sections (Brick 10).

Only a super-admin may change a value, bad values are refused, and an unchanged value writes no
audit row. `make_user` is a local helper: adjust the fields to your project's factory if needed.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.services import job_settings as services
from apps.audit.models import AuditLog
from apps.core import job_settings
from apps.core.models import RetentionPolicy, SiteSetting
from apps.core.services import ServiceError
from apps.faith.tests.helpers import make_user as _make_user

User = get_user_model()


def make_user(role="member", **overrides):
    return _make_user(role=role, **overrides)


class JobSettingsServiceTests(TestCase):
    """Service rules: permission, validation, idempotence, audit."""

    def setUp(self):
        self.boss = make_user("super_admin")
        self.admin = make_user("admin")

    def test_defaults_without_rows(self):
        self.assertTrue(job_settings.goal_reminders_enabled())
        self.assertEqual(job_settings.end_request_days(), 7)

    def test_toggle_reminders_logs_once(self):
        self.assertTrue(services.set_goal_reminders_enabled(self.boss, None, False))
        self.assertFalse(job_settings.goal_reminders_enabled())
        self.assertFalse(services.set_goal_reminders_enabled(self.boss, None, False))  # same value
        self.assertEqual(AuditLog.objects.filter(action="site_setting.goal_reminders_toggled").count(), 1)

    def test_end_days_saved_and_validated(self):
        self.assertTrue(services.set_end_request_days(self.boss, None, "10"))
        self.assertEqual(job_settings.end_request_days(), 10)
        for bad in ("0", "91", "-3", "abc", "", "2.5", None):
            with self.subTest(bad=bad), self.assertRaises(ServiceError):
                services.set_end_request_days(self.boss, None, bad)
        self.assertEqual(job_settings.end_request_days(), 10)

    def test_unchanged_end_days_writes_no_audit_row(self):
        services.set_end_request_days(self.boss, None, "10")
        before = AuditLog.objects.count()
        self.assertFalse(services.set_end_request_days(self.boss, None, "10"))
        self.assertEqual(AuditLog.objects.count(), before)

    def test_only_super_admin_may_change(self):
        with self.assertRaises(Exception):
            services.set_goal_reminders_enabled(self.admin, None, False)
        with self.assertRaises(Exception):
            services.set_end_request_days(self.admin, None, "5")
        with self.assertRaises(Exception):
            services.set_retention_days(self.admin, None, "goal", "30")
        self.assertFalse(SiteSetting.objects.filter(key__in=[
            job_settings.GOAL_REMINDERS_KEY, job_settings.END_REQUEST_DAYS_KEY]).exclude(
            value__in=[True, 7]).exists())

    def test_bad_stored_value_falls_back(self):
        SiteSetting.objects.create(key=job_settings.END_REQUEST_DAYS_KEY, value="soon")
        self.assertEqual(job_settings.end_request_days(), 7)


class RetentionServiceTests(TestCase):
    """Retention edits: validation, never, unknown row, audit with before and after."""

    def setUp(self):
        self.boss = make_user("super_admin")

    def test_goal_and_prayer_rows_are_seeded(self):
        self.assertTrue(RetentionPolicy.objects.filter(pk="goal").exists())
        self.assertTrue(RetentionPolicy.objects.filter(pk="prayer_request").exists())

    def test_change_logs_before_and_after(self):
        self.assertTrue(services.set_retention_days(self.boss, None, "goal", "200"))
        self.assertEqual(RetentionPolicy.objects.get(pk="goal").purge_after_days, 200)
        row = AuditLog.objects.get(action="retention_policy.change")
        self.assertEqual(row.entity_label, "goal")

    def test_same_value_is_quiet(self):
        services.set_retention_days(self.boss, None, "goal", "200")
        before = AuditLog.objects.count()
        self.assertFalse(services.set_retention_days(self.boss, None, "goal", "200"))
        self.assertEqual(AuditLog.objects.count(), before)

    def test_never_and_bad_values(self):
        self.assertTrue(services.set_retention_days(self.boss, None, "goal", None))
        self.assertIsNone(RetentionPolicy.objects.get(pk="goal").purge_after_days)
        for bad in ("0", "-1", "3651", "ten"):
            with self.subTest(bad=bad), self.assertRaises(ServiceError):
                services.set_retention_days(self.boss, None, "goal", bad)

    def test_unknown_row_refused_and_not_created(self):
        with self.assertRaises(ServiceError):
            services.set_retention_days(self.boss, None, "made_up", "30")
        self.assertFalse(RetentionPolicy.objects.filter(pk="made_up").exists())


class SchedulerTests(TestCase):
    """The scheduler runs the new jobs at the right minutes."""

    def test_due_lists(self):
        import importlib.util
        from datetime import datetime
        spec = importlib.util.spec_from_file_location("scheduler", "production/deploy/scheduler.py")
        sched = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sched)
        at5 = sched.due(datetime(2026, 10, 23, 9, 5, tzinfo=sched.TZ))
        self.assertIn("send_goal_reminders", at5)
        self.assertNotIn("send_event_reminders", at5)
        night = sched.due(datetime(2026, 10, 23, sched.DAILY_HOUR, 0, tzinfo=sched.TZ))
        for name in ("complete_ended_goals", "close_stale_end_requests", "send_goal_reminders"):
            self.assertIn(name, night)
        self.assertLess(night.index("archive_inactive_users"), night.index("purge_archived"))
