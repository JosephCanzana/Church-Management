"""Tests for transactional Resources services, audit records, and file cleanup."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Extension, Role, Status, User
from apps.audit.models import AuditLog
from apps.core.services import ServiceError
from apps.resources import services
from apps.resources.models import GeneralResource, MonthlyResource, MonthlyTheme, WeeklyPowerpoint


def person(role, *, extension=None):
    """Create an activated account for a Resources service test."""
    return User.objects.create_user(
        password="test-pass-123",
        first_name=role,
        last_name="test",
        role=role,
        extension=extension,
        status=Status.ACTIVE,
        must_change_password=False,
    )


class ResourceServiceTests(TestCase):
    """Exercise service guards, state transitions, auditing, and uploaded files."""

    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.settings_override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.temporary_directory.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temporary_directory.cleanup)
        self.extension = Extension.objects.create(name="alpha")
        self.admin = person(Role.ADMIN)
        self.super_admin = person(Role.SUPER_ADMIN)
        self.coordinator = person(Role.COORDINATOR, extension=self.extension)

    def upload(self, name, content=b"file bytes"):
        """Build a small upload with a supported extension."""
        return SimpleUploadedFile(name, content)

    def test_only_managers_can_call_mutating_services(self):
        with self.assertRaises(PermissionDenied):
            services.create_monthly_theme(
                self.coordinator, month=date(2026, 10, 1), title="Denied",
            )
        with self.assertRaises(PermissionDenied):
            services.create_general_resource(
                self.coordinator, title="Denied", category=GeneralResource.Category.OTHER,
                file=self.upload("denied.pdf"),
            )
        self.assertFalse(MonthlyTheme.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_monthly_theme_validation_create_update_and_publish(self):
        with self.assertRaises(ServiceError):
            services.create_monthly_theme(
                self.admin, month=date(2026, 10, 2), title="Invalid date",
            )

        theme = services.create_monthly_theme(
            self.admin,
            month=date(2026, 10, 1),
            title="  October theme  ",
            description="Private edit content",
        )
        self.assertEqual(theme.title, "October theme")
        created = AuditLog.objects.get(action="resource.monthly_theme_created")
        self.assertEqual((created.actor_id, created.entity_id), (self.admin.pk, theme.pk))
        self.assertNotIn("Private edit content", str(created.after))

        with self.assertRaises(ServiceError):
            services.create_monthly_theme(
                self.admin, month=date(2026, 10, 1), title="Duplicate",
            )

        updated = services.update_monthly_theme(
            self.admin, theme.pk, title="October revised", description="Changed text",
        )
        self.assertEqual(updated.title, "October revised")
        update_log = AuditLog.objects.get(action="resource.monthly_theme_updated")
        self.assertEqual(update_log.after, {"changed_fields": ["title", "description"]})
        self.assertNotIn("Changed text", str(update_log.after))

        self.assertTrue(services.set_monthly_theme_published(self.admin, theme.pk, True))
        self.assertFalse(services.set_monthly_theme_published(self.admin, theme.pk, True))
        theme.refresh_from_db()
        self.assertTrue(theme.is_published)
        self.assertEqual(AuditLog.objects.filter(action="resource.monthly_theme_published").count(), 1)

    def test_monthly_files_are_audited_without_names_or_contents_and_replaced_safely(self):
        theme = services.create_monthly_theme(
            self.admin, month=date(2026, 10, 1), title="October",
        )
        resource = services.create_monthly_resource(
            self.admin,
            theme.pk,
            kind=MonthlyResource.Kind.OTHER,
            title="Study guide",
            file=self.upload("lesson plan.pdf"),
        )
        old_name = resource.file.name
        create_log = AuditLog.objects.get(action="resource.monthly_resource_created")
        self.assertNotIn("lesson plan.pdf", str(create_log.after))
        self.assertNotIn("file bytes", str(create_log.after))

        with self.captureOnCommitCallbacks(execute=True):
            resource = services.update_monthly_resource(
                self.admin, resource.pk, file=self.upload("revised notes.pdf", b"replacement bytes"),
            )
        new_name = resource.file.name
        self.assertFalse(resource.file.storage.exists(old_name))
        self.assertEqual(resource.original_filename, "revised notes.pdf")

        with self.captureOnCommitCallbacks(execute=True):
            services.delete_monthly_resource(self.admin, resource.pk)
        self.assertFalse(resource.file.storage.exists(new_name))
        self.assertFalse(MonthlyResource.objects.filter(pk=resource.pk).exists())

    def test_theme_archive_restore_and_force_delete_clean_child_files(self):
        theme = services.create_monthly_theme(
            self.admin, month=date(2026, 10, 1), title="October",
        )
        resource = services.create_monthly_resource(
            self.admin,
            theme.pk,
            kind=MonthlyResource.Kind.OTHER,
            title="Study guide",
            file=self.upload("guide.pdf"),
        )
        stored_name = resource.file.name

        self.assertTrue(services.archive_monthly_theme(self.admin, theme.pk))
        self.assertFalse(services.archive_monthly_theme(self.admin, theme.pk))
        self.assertTrue(services.restore_monthly_theme(self.admin, theme.pk))
        self.assertFalse(services.restore_monthly_theme(self.admin, theme.pk))
        self.assertTrue(services.archive_monthly_theme(self.admin, theme.pk))
        with self.assertRaises(ServiceError):
            services.update_monthly_theme(self.admin, theme.pk, title="Archived edit")
        self.assertTrue(services.force_delete_monthly_theme(self.admin, theme.pk))
        self.assertFalse(resource.file.storage.exists(stored_name))
        self.assertFalse(MonthlyTheme.objects.filter(pk=theme.pk).exists())
        self.assertFalse(MonthlyResource.objects.filter(pk=resource.pk).exists())

    def test_weekly_presentation_create_update_archive_restore_and_delete(self):
        powerpoint = services.create_weekly_powerpoint(
            self.admin,
            title="Sunday slides",
            week_start=date(2026, 10, 18),
            file=self.upload("sunday.pptx"),
        )
        old_name = powerpoint.file.name
        with self.captureOnCommitCallbacks(execute=True):
            powerpoint = services.update_weekly_powerpoint(
                self.admin, powerpoint.pk, title="Updated slides", file=self.upload("updated.pptx"),
            )
        self.assertFalse(powerpoint.file.storage.exists(old_name))
        self.assertEqual(powerpoint.original_filename, "updated.pptx")
        self.assertTrue(services.archive_weekly_powerpoint(self.admin, powerpoint.pk))
        self.assertTrue(services.restore_weekly_powerpoint(self.admin, powerpoint.pk))
        self.assertTrue(services.archive_weekly_powerpoint(self.admin, powerpoint.pk))
        self.assertTrue(services.force_delete_weekly_powerpoint(self.admin, powerpoint.pk))
        self.assertFalse(WeeklyPowerpoint.objects.filter(pk=powerpoint.pk).exists())

    def test_general_resource_create_update_archive_restore_and_delete(self):
        resource = services.create_general_resource(
            self.super_admin,
            title="  Font pack ",
            category=GeneralResource.Category.FONT,
            file=self.upload("fonts.zip.pdf"),
            description="Optional notes",
        )
        self.assertEqual(resource.title, "Font pack")
        self.assertEqual(resource.original_filename, "fonts.zip.pdf")
        with self.captureOnCommitCallbacks(execute=True):
            resource = services.update_general_resource(
                self.super_admin, resource.pk, category=GeneralResource.Category.OTHER,
            )
        self.assertEqual(resource.category, GeneralResource.Category.OTHER)
        self.assertTrue(services.archive_general_resource(self.admin, resource.pk))
        self.assertTrue(services.restore_general_resource(self.admin, resource.pk))
        self.assertTrue(services.archive_general_resource(self.admin, resource.pk))
        stored_name = resource.file.name
        self.assertTrue(services.force_delete_general_resource(self.admin, resource.pk))
        self.assertFalse(resource.file.storage.exists(stored_name))
        self.assertFalse(GeneralResource.objects.filter(pk=resource.pk).exists())