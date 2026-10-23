"""Tests for the faith widgets on the dashboard (Brick 4).

VERIFY: the audit and notification model names (AuditLog, Notification) follow the plan and AGENTS.md.
"""
from datetime import date
from unittest import mock

from django.test import TestCase

from apps.dashboards import services

TODAY = date(2026, 10, 22)


class FaithWidgetContextTests(TestCase):
    """get_dashboard_context carries the two new keys and keeps goal text out of stored data."""

    def make_user(self, role, _unused=None):
        from apps.accounts.models import Extension, PersonType, Status, User
        extension = Extension.objects.get_or_create(name="Test Extension")[0]
        return User.objects.create_user(
            password="pw-12345-pw", first_name="Juan", last_name="Dela Cruz", role=role, extension=extension,
            person_type=PersonType.MEMBER_ACCOUNT, status=Status.ACTIVE, must_change_password=False,
        )

    def context(self, user):
        with mock.patch("apps.dashboards.services.timezone.localdate", return_value=TODAY):
            return services.get_dashboard_context(user, None, "/")

    def test_member_and_leader_get_both_keys(self):
        for role, email in (("member", "m@example.com"), ("coordinator", "c@example.com")):
            ctx = self.context(self.make_user(role, email))
            self.assertIn("streaks", ctx)
            self.assertIn("goals_today", ctx)
            self.assertEqual(set(ctx["streaks"]) & {"open", "read"}, {"open", "read"})
            self.assertEqual(ctx["goals_today"]["total"], 0)

    def test_coming_soon_keeps_only_events(self):
        ctx = self.context(self.make_user("member", "m2@example.com"))
        self.assertEqual([c["label"] for c in ctx["coming_soon"]], ["Upcoming events"])

    def test_goal_text_is_in_the_context_but_never_in_stored_data(self):
        from apps.audit.models import AuditLog          # VERIFY model name
        from apps.faith.models import Goal
        from apps.notifications.models import Notification  # VERIFY model name
        user = self.make_user("member", "m3@example.com")
        Goal.objects.create(user=user, title="SECRET-GOAL-TITLE", start_date=date(2026, 10, 1), repeat_type="daily")
        ctx = self.context(user)
        self.assertEqual(ctx["goals_today"]["next_up"][0]["title"], "SECRET-GOAL-TITLE")
        self.assertNotIn("SECRET-GOAL-TITLE", repr(list(AuditLog.objects.values())))
        self.assertNotIn("SECRET-GOAL-TITLE", repr(list(Notification.objects.values())))

    def test_person_who_cannot_use_goals_gets_none(self):
        user = self.make_user("member", "m4@example.com")
        with mock.patch("apps.dashboards.services.can_use_goals", return_value=False):
            ctx = self.context(user)
        self.assertIsNone(ctx["streaks"])
        self.assertIsNone(ctx["goals_today"])