"""Tests for upload validation, private storage, and file lifecycle cleanup."""
from datetime import date
from io import StringIO
from tempfile import TemporaryDirectory
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.core.services import force_delete
from apps.core.uploads import (
    PrivateUploadStorage,
    safe_filename,
    validate_photo_upload,
    validate_resource_upload,
)
from apps.resources.models import MonthlyResource, MonthlyTheme, WeeklyPowerpoint


class UploadValidationTests(SimpleTestCase):
    """Check accepted upload types, configured size limits, and private URLs."""

    @override_settings(PHOTO_UPLOAD_MAX_BYTES=4, RESOURCE_UPLOAD_MAX_BYTES=8)
    def test_upload_type_and_size_limits(self):
        validate_photo_upload(SimpleUploadedFile("photo.webp", b"1234"))
        validate_resource_upload(SimpleUploadedFile("guide.PDF", b"12345678"))

        for upload, validator in (
            (SimpleUploadedFile("photo.gif", b"x"), validate_photo_upload),
            (SimpleUploadedFile("guide.zip", b"x"), validate_resource_upload),
            (SimpleUploadedFile("large.png", b"12345"), validate_photo_upload),
            (SimpleUploadedFile("large.pdf", b"123456789"), validate_resource_upload),
        ):
            with self.subTest(name=upload.name), self.assertRaises(ValidationError):
                validator(upload)

    def test_filename_is_path_free_and_safe(self):
        self.assertEqual(safe_filename(r"..\private folder\lesson plan.pdf"), "lesson_plan.pdf")

    def test_private_storage_has_no_url(self):
        with self.assertRaises(ValueError):
            PrivateUploadStorage().url("monthly_resource/guide.pdf")


class UploadedFileLifecycleTests(TransactionTestCase):
    """Ensure saved uploads retain names and stale files are removed."""

    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.settings_override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.temporary_directory.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temporary_directory.cleanup)

    def test_replacement_and_delete_remove_files_and_keep_original_name(self):
        theme = MonthlyTheme.objects.create(title="October", month=date(2026, 10, 1))
        resource = MonthlyResource(
            monthly_theme=theme,
            kind=MonthlyResource.Kind.OTHER,
            title="Study guide",
            file=SimpleUploadedFile("lesson plan.pdf", b"first file"),
        )
        resource.save()
        old_name = resource.file.name
        self.assertEqual(resource.original_filename, "lesson plan.pdf")
        self.assertTrue(resource.file.storage.exists(old_name))
        with self.assertRaises(ValueError):
            resource.file.url

        resource.file = SimpleUploadedFile("new notes.pdf", b"replacement")
        resource.save()
        replacement_name = resource.file.name
        self.assertFalse(resource.file.storage.exists(old_name))
        self.assertEqual(resource.original_filename, "new notes.pdf")

        resource.delete()
        self.assertFalse(resource.file.storage.exists(replacement_name))

    def test_force_delete_removes_files_and_missing_file_is_harmless(self):
        powerpoint = WeeklyPowerpoint.objects.create(
            title="Sunday", week_start=date(2026, 10, 18),
            file=SimpleUploadedFile("slides.pptx", b"slides"),
        )
        stored_name = powerpoint.file.name
        powerpoint.archived_at = timezone.now()
        powerpoint.save(update_fields=["archived_at"])

        force_delete(powerpoint)
        self.assertFalse(powerpoint.file.storage.exists(stored_name))
        self.assertFalse(WeeklyPowerpoint.objects.filter(pk=powerpoint.pk).exists())

        missing = WeeklyPowerpoint.objects.create(
            title="Missing", week_start=date(2026, 10, 25),
            file=SimpleUploadedFile("missing.pptx", b"slides"),
        )
        missing.file.storage.delete(missing.file.name)
        missing.archived_at = timezone.now()
        missing.save(update_fields=["archived_at"])
        force_delete(missing)

    def test_purge_command_removes_expired_upload_and_row(self):
        powerpoint = WeeklyPowerpoint.objects.create(
            title="Expired", week_start=date(2026, 11, 1),
            file=SimpleUploadedFile("expired.pptx", b"slides"),
        )
        stored_name = powerpoint.file.name
        WeeklyPowerpoint.objects.filter(pk=powerpoint.pk).update(archived_at=timezone.now())
        WeeklyPowerpoint.objects.filter(pk=powerpoint.pk).update(purge_at=timezone.now())

        call_command("purge_archived", stdout=StringIO())

        self.assertFalse(powerpoint.file.storage.exists(stored_name))
        self.assertFalse(WeeklyPowerpoint.objects.filter(pk=powerpoint.pk).exists())