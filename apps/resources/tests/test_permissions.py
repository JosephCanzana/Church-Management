"""Tests for resource management and library visibility permissions."""
from datetime import date

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Extension, Role, Status, User
from apps.resources import permissions
from apps.resources.models import (
    GeneralResource,
    MonthlyResource,
    MonthlyTheme,
    WeeklyPowerpoint,
)


def person(role, *, extension=None, status=Status.ACTIVE):
    """Create an activated account for a permission test."""
    extra = {"archived_at": timezone.now()} if status == Status.ARCHIVED else {}
    return User.objects.create_user(
        password="test-pass-123",
        first_name=role,
        last_name="test",
        role=role,
        extension=extension,
        status=status,
        must_change_password=False,
        **extra,
    )


class ResourcePermissionTests(TestCase):
    """Check manager, coordinator, member, inactive, and anonymous access."""

    def setUp(self):
        self.extension = Extension.objects.create(name="alpha")
        self.super_admin = person(Role.SUPER_ADMIN)
        self.admin = person(Role.ADMIN)
        self.coordinator = person(Role.COORDINATOR, extension=self.extension)
        self.member = person(Role.MEMBER, extension=self.extension)
        self.suspended = person(Role.MEMBER, extension=self.extension, status=Status.SUSPENDED)
        self.archived = person(Role.MEMBER, extension=self.extension, status=Status.ARCHIVED)
        self.published = MonthlyTheme.objects.create(
            title="Published", month=date(2026, 10, 1), is_published=True,
        )
        self.draft = MonthlyTheme.objects.create(title="Draft", month=date(2026, 11, 1))
        self.archived_theme = MonthlyTheme.objects.create(
            title="Archived", month=date(2026, 12, 1), is_published=True,
            archived_at=timezone.now(),
        )
        self.monthly_file = MonthlyResource(monthly_theme=self.published)
        self.weekly = WeeklyPowerpoint(title="Weekly", week_start=date(2026, 10, 18))
        self.archived_weekly = WeeklyPowerpoint(
            title="Archived weekly", week_start=date(2026, 10, 25),
            archived_at=timezone.now(),
        )
        self.general = GeneralResource(title="General")
        self.archived_general = GeneralResource(title="Archived general", archived_at=timezone.now())

    def test_only_admin_and_super_admin_can_manage(self):
        for actor in (self.super_admin, self.admin):
            self.assertTrue(permissions.can_manage_resources(actor))
        for actor in (self.coordinator, self.member, self.suspended, self.archived, AnonymousUser(), None):
            self.assertFalse(permissions.can_manage_resources(actor), actor)
        permissions.require_resource_manager(self.admin)
        with self.assertRaises(PermissionDenied):
            permissions.require_resource_manager(self.coordinator)

    def test_only_super_admin_can_force_delete_archived_resources(self):
        self.assertTrue(permissions.can_force_delete_resources(self.super_admin))
        for actor in (self.admin, self.coordinator, self.member, AnonymousUser(), None):
            self.assertFalse(permissions.can_force_delete_resources(actor), actor)
        with self.assertRaises(PermissionDenied):
            permissions.require_resource_force_deleter(self.admin)

    def test_admins_can_view_drafts_and_archived_records(self):
        for actor in (self.super_admin, self.admin):
            self.assertTrue(permissions.can_view_monthly_theme(actor, self.draft))
            self.assertTrue(permissions.can_view_monthly_theme(actor, self.archived_theme))
            self.assertTrue(permissions.can_view_weekly_powerpoint(actor, self.archived_weekly))
            self.assertTrue(permissions.can_view_general_resource(actor, self.archived_general))

    def test_coordinators_can_view_active_published_library_items(self):
        self.assertTrue(permissions.can_view_monthly_theme(self.coordinator, self.published))
        self.assertTrue(permissions.can_view_monthly_resource(self.coordinator, self.monthly_file))
        self.assertTrue(permissions.can_view_weekly_powerpoint(self.coordinator, self.weekly))
        self.assertTrue(permissions.can_view_general_resource(self.coordinator, self.general))
        self.assertFalse(permissions.can_view_monthly_theme(self.coordinator, self.draft))
        self.assertFalse(permissions.can_view_monthly_theme(self.coordinator, self.archived_theme))
        self.assertFalse(permissions.can_view_weekly_powerpoint(self.coordinator, self.archived_weekly))
        self.assertFalse(permissions.can_view_general_resource(self.coordinator, self.archived_general))

    def test_members_can_only_view_published_monthly_themes(self):
        self.assertTrue(permissions.can_view_monthly_theme(self.member, self.published))
        self.assertTrue(permissions.can_view_monthly_resource(self.member, self.monthly_file))
        self.assertFalse(permissions.can_view_monthly_theme(self.member, self.draft))
        self.assertFalse(permissions.can_view_monthly_theme(self.member, self.archived_theme))
        self.assertFalse(permissions.can_view_weekly_powerpoint(self.member, self.weekly))
        self.assertFalse(permissions.can_view_general_resource(self.member, self.general))

    def test_inactive_and_anonymous_actors_cannot_view_resources(self):
        for actor in (self.suspended, self.archived, AnonymousUser(), None):
            self.assertFalse(permissions.can_view_monthly_theme(actor, self.published), actor)
            self.assertFalse(permissions.can_view_weekly_powerpoint(actor, self.weekly), actor)
            self.assertFalse(permissions.can_view_general_resource(actor, self.general), actor)