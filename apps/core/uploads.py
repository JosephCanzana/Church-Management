"""Validation, naming, and storage helpers for uploaded files."""
import os
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import models, transaction
from django.utils.text import get_valid_filename


PHOTO_EXTENSIONS = {".jpg", ".png", ".webp"}
RESOURCE_EXTENSIONS = {".pdf", ".pptx", ".docx", ".xlsx"}


def safe_filename(filename):
    """Return a path-free filename safe for a local filesystem."""
    basename = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    return get_valid_filename(basename) or "upload"


def original_upload_name(filename):
    """Keep the uploaded basename for display and download headers."""
    return str(filename).replace("\\", "/").rsplit("/", 1)[-1][:255]


def validate_photo_upload(upload):
    """Accept only supported image extensions within the photo size limit."""
    _validate_upload(
        upload,
        allowed_extensions=PHOTO_EXTENSIONS,
        max_bytes=settings.PHOTO_UPLOAD_MAX_BYTES,
        kind="photo",
    )


def validate_resource_upload(upload):
    """Accept only supported document extensions within the resource limit."""
    _validate_upload(
        upload,
        allowed_extensions=RESOURCE_EXTENSIONS,
        max_bytes=settings.RESOURCE_UPLOAD_MAX_BYTES,
        kind="resource",
    )


def _validate_upload(upload, *, allowed_extensions, max_bytes, kind):
    """Validate an upload's filename extension and byte size."""
    filename = safe_filename(getattr(upload, "name", ""))
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise ValidationError(f"Choose a {kind} file with one of these extensions: {allowed}.")
    if upload.size > max_bytes:
        limit_mb = max_bytes / (1024 * 1024)
        raise ValidationError(f"The {kind} file must be {limit_mb:g} MB or smaller.")


def public_upload_to(instance, filename):
    """Place public images in their Caddy-served media directory."""
    directory = {
        "landing_image": "landing",
        "donation_account": "donation_qr",
    }[instance._meta.db_table]
    return f"{directory}/{safe_filename(filename)}"


def private_upload_to(instance, filename):
    """Place a resource upload in its table-specific private directory."""
    return f"{instance._meta.db_table}/{safe_filename(filename)}"


def delete_files(instance):
    """Remove every stored file field, tolerating files already missing."""
    for field in instance._meta.concrete_fields:
        if not isinstance(field, models.FileField):
            continue
        stored_file = getattr(instance, field.attname, None)
        if stored_file and stored_file.name:
            try:
                stored_file.storage.delete(stored_file.name)
            except OSError:
                pass


class FileCleanupModel(models.Model):
    """Delete replaced or removed files after the database change commits."""
    FILE_ORIGINAL_NAME_FIELDS = {}

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        old_files = {}
        if self.pk:
            old_files = type(self)._base_manager.filter(pk=self.pk).values(
                *self.FILE_ORIGINAL_NAME_FIELDS.keys()
            ).first() or {}

        for file_field, original_field in self.FILE_ORIGINAL_NAME_FIELDS.items():
            upload = getattr(self, file_field)
            if upload and not upload._committed:
                setattr(self, original_field, original_upload_name(upload.name))

        super().save(*args, **kwargs)

        for file_field in self.FILE_ORIGINAL_NAME_FIELDS:
            old_name = old_files.get(file_field)
            current_file = getattr(self, file_field)
            if old_name and old_name != current_file.name:
                self._delete_on_commit(getattr(self, file_field).storage, old_name)

    def delete(self, *args, **kwargs):
        files = [
            (getattr(self, field.attname).storage, getattr(self, field.attname).name)
            for field in self._meta.concrete_fields
            if isinstance(field, models.FileField)
            and getattr(self, field.attname, None)
            and getattr(self, field.attname).name
        ]
        result = super().delete(*args, **kwargs)
        for storage, name in files:
            self._delete_on_commit(storage, name)
        return result

    @staticmethod
    def _delete_on_commit(storage, name):
        """Remove a stale file only after its row change is durable."""
        def remove():
            try:
                storage.delete(name)
            except OSError:
                pass

        transaction.on_commit(remove)


class PrivateUploadStorage(FileSystemStorage):
    """Store resources outside public media and refuse URL generation."""
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
        kwargs["base_url"] = ""
        super().__init__(*args, **kwargs)

    @property
    def location(self):
        """Use the configured private root, including test overrides."""
        return os.path.abspath(settings.PRIVATE_MEDIA_ROOT)

    def url(self, name):
        """Prevent private uploads from being exposed as direct URLs."""
        raise ValueError("Private uploads do not have public URLs.")


private_storage = PrivateUploadStorage()