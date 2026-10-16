"""Tests for the role-scoped Resources library and private file downloads."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Extension, Role, Status, User
from apps.resources.models import GeneralResource, MonthlyResource, MonthlyTheme, WeeklyPowerpoint


class ResourceLibraryAndDownloadTests(TestCase):
    """Verify library visibility, permission checks, download names, and missing files."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.settings_override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.temp_dir.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_dir.cleanup)

        extension = Extension.objects.create(name="alpha")
        self.super_admin = self.person(Role.SUPER_ADMIN)
        self.admin = self.person(Role.ADMIN)
        self.coordinator = self.person(Role.COORDINATOR, extension=extension)
        self.member = self.person(Role.MEMBER, extension=extension)

        self.published = MonthlyTheme.objects.create(
            title="Published theme", month=date(2026, 10, 1), is_published=True,
        )
        self.draft = MonthlyTheme.objects.create(
            title="Draft theme", month=date(2026, 11, 1),
        )
        self.archived = MonthlyTheme.objects.create(
            title="Archived theme", month=date(2026, 9, 1), is_published=True,
            archived_at=timezone.now(),
        )
        self.monthly_file = MonthlyResource.objects.create(
            monthly_theme=self.published,
            kind=MonthlyResource.Kind.OTHER,
            title="Study guide",
            file=SimpleUploadedFile("study guide.pdf", b"monthly document"),
        )
        self.draft_file = MonthlyResource.objects.create(
            monthly_theme=self.draft,
            kind=MonthlyResource.Kind.OTHER,
            title="Draft attachment",
            file=SimpleUploadedFile("draft.pdf", b"draft document"),
        )
        self.weekly = WeeklyPowerpoint.objects.create(
            title="Sunday slides",
            week_start=date(2026, 10, 18),
            file=SimpleUploadedFile("sunday slides.pptx", b"presentation bytes"),
        )
        self.archived_weekly = WeeklyPowerpoint.objects.create(
            title="Old slides",
            week_start=date(2026, 10, 11),
            archived_at=timezone.now(),
            file=SimpleUploadedFile("old.pptx", b"archived presentation"),
        )
        self.general = GeneralResource.objects.create(
            title="Coordinator guide",
            category=GeneralResource.Category.OTHER,
            file=SimpleUploadedFile("guide.docx", b"general document"),
        )

    def person(self, role, *, extension=None):
        """Create an activated person with any required extension assignment."""
        return User.objects.create_user(
            password="test-pass-123",
            first_name=role,
            last_name="library-test",
            role=role,
            extension=extension,
            status=Status.ACTIVE,
            must_change_password=False,
        )

    def read_response(self, response):
        """Consume a streaming file response as bytes."""
        return b"".join(response.streaming_content)

    def test_logged_out_library_and_download_redirect_to_login(self):
        response = self.client.get(reverse("resources:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

        response = self.client.get(reverse("resources:weekly_download", args=[self.weekly.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_coordinator_library_shows_only_active_published_library_content(self):
        self.client.force_login(self.coordinator)
        response = self.client.get(reverse("resources:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.published.title)
        self.assertContains(response, self.monthly_file.title)
        self.assertContains(response, self.weekly.title)
        self.assertContains(response, self.general.title)
        self.assertNotContains(response, self.draft.title)
        self.assertNotContains(response, self.draft_file.title)
        self.assertNotContains(response, self.archived.title)
        self.assertNotContains(response, self.archived_weekly.title)

    def test_member_library_contains_monthly_themes_only(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("resources:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.published.title)
        self.assertContains(response, self.monthly_file.title)
        self.assertNotContains(response, self.weekly.title)
        self.assertNotContains(response, self.general.title)
        self.assertNotContains(response, "Weekly presentations")
        self.assertNotContains(response, "General resources")

    def test_download_streams_private_file_with_original_filename(self):
        self.client.force_login(self.coordinator)
        response = self.client.get(reverse("resources:monthly_resource_download", args=[self.monthly_file.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertIn("study guide.pdf", response["Content-Disposition"])
        self.assertEqual(self.read_response(response), b"monthly document")

        response = self.client.get(reverse("resources:weekly_download", args=[self.weekly.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("sunday slides.pptx", response["Content-Disposition"])
        self.assertEqual(self.read_response(response), b"presentation bytes")

        response = self.client.get(reverse("resources:general_download", args=[self.general.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("guide.docx", response["Content-Disposition"])
        self.assertEqual(self.read_response(response), b"general document")

    def test_members_cannot_download_weekly_or_general_resources(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.get(reverse("resources:weekly_download", args=[self.weekly.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("resources:general_download", args=[self.general.pk])).status_code,
            404,
        )

    def test_coordinator_cannot_download_unpublished_or_archived_files(self):
        self.client.force_login(self.coordinator)
        self.assertEqual(
            self.client.get(reverse("resources:monthly_resource_download", args=[self.draft_file.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("resources:weekly_download", args=[self.archived_weekly.pk])).status_code,
            404,
        )

    def test_missing_file_shows_friendly_message_and_returns_to_library(self):
        self.client.force_login(self.coordinator)
        self.monthly_file.file.storage.delete(self.monthly_file.file.name)
        response = self.client.get(
            reverse("resources:monthly_resource_download", args=[self.monthly_file.pk]),
            follow=True,
        )
        self.assertRedirects(response, reverse("resources:list"))
        self.assertContains(response, "This file is no longer available.")

    def test_admins_can_use_the_library_but_archives_stay_out_of_it(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("resources:list"))
        self.assertContains(response, self.published.title)
        self.assertContains(response, self.weekly.title)
        self.assertNotContains(response, self.archived_weekly.title)

        self.client.force_login(self.super_admin)
        self.assertEqual(self.client.get(reverse("resources:list")).status_code, 200)
