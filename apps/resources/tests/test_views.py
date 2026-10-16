"""Tests for manager-only Resources pages and form workflows."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Extension, Role, Status, User
from apps.audit.models import AuditLog
from apps.resources.models import GeneralResource, MonthlyResource, MonthlyTheme, WeeklyPowerpoint


def person(role, *, extension=None):
    """Create an activated account for a view test."""
    return User.objects.create_user(
        password="test-pass-123",
        first_name=role,
        last_name="manager-test",
        role=role,
        extension=extension,
        status=Status.ACTIVE,
        must_change_password=False,
    )


class ResourceManagementViewTests(TestCase):
    """Exercise the Resources management screens through Django's test client."""

    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.settings_override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.temporary_directory.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temporary_directory.cleanup)
        self.extension = Extension.objects.create(name="alpha")
        self.super_admin = User.objects.create_superuser(
            password="test-pass-123", first_name="Super", last_name="Admin",
        )
        self.admin = person(Role.ADMIN)
        self.coordinator = person(Role.COORDINATOR, extension=self.extension)
        self.member = person(Role.MEMBER, extension=self.extension)

    def upload(self, name, content=b"resource bytes"):
        """Build a supported document upload."""
        return SimpleUploadedFile(name, content)

    def test_only_admin_roles_open_management_lists(self):
        url = reverse("resources:theme_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

        for actor, home in (
            (self.coordinator, reverse("dashboards:coordinator")),
            (self.member, reverse("dashboards:member")),
        ):
            self.client.force_login(actor)
            self.assertRedirects(self.client.get(url), home, fetch_redirect_response=False)

        self.client.force_login(self.admin)
        for name in ("theme_list", "weekly_list", "general_list"):
            response = self.client.get(reverse(f"resources:{name}"))
            self.assertEqual(response.status_code, 200, name)

    def test_theme_create_edit_publish_and_inline_validation(self):
        self.client.force_login(self.admin)
        create_url = reverse("resources:theme_create")
        response = self.client.get(create_url)
        self.assertContains(response, 'data-confirm="Create this monthly theme as a draft?"')

        invalid = self.client.post(create_url, {"month": "2026-13", "title": "Invalid"})
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "Enter a valid month.")
        self.assertFalse(MonthlyTheme.objects.exists())

        response = self.client.post(create_url, {
            "month": "2026-10", "title": "October theme", "description": "One line\nSecond line",
        })
        theme = MonthlyTheme.objects.get()
        self.assertRedirects(response, reverse("resources:theme_detail", args=[theme.pk]), fetch_redirect_response=False)
        self.assertEqual(theme.month, date(2026, 10, 1))
        self.assertFalse(theme.is_published)
        self.assertTrue(AuditLog.objects.filter(action="resource.monthly_theme_created", entity_id=theme.pk).exists())

        detail_url = reverse("resources:theme_detail", args=[theme.pk])
        self.assertContains(self.client.get(detail_url), "Publish theme?")
        publish_url = reverse("resources:theme_publish", args=[theme.pk])
        self.client.post(publish_url, {"published": "unexpected"})
        theme.refresh_from_db()
        self.assertFalse(theme.is_published)
        response = self.client.post(publish_url, {"published": "1"})
        self.assertRedirects(response, detail_url, fetch_redirect_response=False)
        theme.refresh_from_db()
        self.assertTrue(theme.is_published)

        edit_url = reverse("resources:theme_edit", args=[theme.pk])
        response = self.client.post(edit_url, {
            "month": "2026-10", "title": "October revised", "description": "Updated",
        })
        self.assertRedirects(response, detail_url, fetch_redirect_response=False)
        theme.refresh_from_db()
        self.assertEqual(theme.title, "October revised")

    def test_monthly_file_form_adds_private_file(self):
        self.client.force_login(self.admin)
        theme = MonthlyTheme.objects.create(title="October", month=date(2026, 10, 1))
        url = reverse("resources:monthly_resource_create", args=[theme.pk])
        response = self.client.get(url)
        self.assertContains(response, "Maximum size:")
        invalid = self.client.post(url, {
            "kind": MonthlyResource.Kind.OTHER, "title": "Guide", "description": "",
            "file": self.upload("guide.exe"),
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "Choose a resource file")
        self.assertFalse(MonthlyResource.objects.exists())
        response = self.client.post(
            url,
            {
                "kind": MonthlyResource.Kind.OTHER,
                "title": "Guide",
                "description": "",
                "file": self.upload("guide.pdf"),
            },
        )
        self.assertRedirects(response, reverse("resources:theme_detail", args=[theme.pk]), fetch_redirect_response=False)
        resource = MonthlyResource.objects.get(monthly_theme=theme)
        self.assertEqual(resource.original_filename, "guide.pdf")
        self.assertTrue(resource.file.storage.exists(resource.file.name))

    def test_weekly_and_general_resource_forms_create_records(self):
        self.client.force_login(self.admin)
        weekly_response = self.client.post(
            reverse("resources:weekly_create"),
            {
                "title": "Sunday slides", "week_start": "2026-10-18", "description": "",
                "monthly_theme": "", "file": self.upload("sunday.pptx"),
            },
        )
        self.assertRedirects(weekly_response, reverse("resources:weekly_list"), fetch_redirect_response=False)
        weekly = WeeklyPowerpoint.objects.get()
        self.assertEqual(weekly.original_filename, "sunday.pptx")

        general_response = self.client.post(
            reverse("resources:general_create"),
            {
                "title": "Fonts", "category": GeneralResource.Category.FONT,
                "description": "", "file": self.upload("fonts.pdf"),
            },
        )
        self.assertRedirects(general_response, reverse("resources:general_list"), fetch_redirect_response=False)
        self.assertEqual(GeneralResource.objects.get().original_filename, "fonts.pdf")

    def test_archive_is_manager_action_but_force_delete_is_super_admin_only(self):
        self.client.force_login(self.admin)
        theme = MonthlyTheme.objects.create(title="October", month=date(2026, 10, 1))
        response = self.client.post(reverse("resources:theme_archive", args=[theme.pk]))
        self.assertRedirects(response, reverse("resources:theme_detail", args=[theme.pk]), fetch_redirect_response=False)
        theme.refresh_from_db()
        self.assertTrue(theme.is_archived)

        response = self.client.post(reverse("resources:theme_delete", args=[theme.pk]), follow=False)
        self.assertRedirects(response, reverse("dashboards:admin"), fetch_redirect_response=False)
        self.assertTrue(MonthlyTheme.objects.filter(pk=theme.pk).exists())

        self.client.force_login(self.super_admin)
        response = self.client.post(reverse("resources:theme_delete", args=[theme.pk]))
        self.assertRedirects(response, reverse("resources:theme_list"), fetch_redirect_response=False)
        self.assertFalse(MonthlyTheme.objects.filter(pk=theme.pk).exists())

    def test_management_lists_filter_archived_and_search_titles(self):
        self.client.force_login(self.admin)
        active = MonthlyTheme.objects.create(title="October gathering", month=date(2026, 10, 1))
        archived = MonthlyTheme.objects.create(
            title="September gathering", month=date(2026, 9, 1), archived_at=timezone.now(),
        )
        response = self.client.get(reverse("resources:theme_list"), {"q": "October", "status": "all"})
        self.assertContains(response, active.title)
        self.assertNotContains(response, archived.title)