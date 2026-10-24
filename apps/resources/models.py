"""resources: monthly theme (cover + pictures), weekly PowerPoint, general resources."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import ArchivableModel, TimestampedModel
from apps.core.uploads import (
    FileCleanupModel,
    private_storage,
    private_upload_to,
    validate_photo_upload,
    validate_resource_upload,
)

USER = settings.AUTH_USER_MODEL

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def validate_monthly_file(value):
    """Accept a picture (the normal case) or a document (rows from before pictures).

    Tries the photo rules first; if the file is not a picture it must pass the
    resource rules, so older PDF/PPTX/DOCX/XLSX rows stay valid on re-save.
    """
    try:
        validate_photo_upload(value)
    except ValidationError:
        validate_resource_upload(value)


class MonthlyTheme(TimestampedModel, ArchivableModel, FileCleanupModel):
    """One month. It holds a cover picture, resource pictures and weekly presentations."""
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    month = models.DateField(unique=True)          # always the 1st of the month
    is_published = models.BooleanField(default=False)
    # Cover picture (optional). Private storage like every resource file: it is
    # served by a permission-checked view, so a draft month never leaks its cover.
    cover_image = models.FileField(
        upload_to=private_upload_to,
        storage=private_storage,
        validators=[validate_photo_upload],
        blank=True,
    )
    cover_original_filename = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    FILE_ORIGINAL_NAME_FIELDS = {"cover_image": "cover_original_filename"}

    class Meta:
        db_table = "monthly_theme"
        constraints = [
            models.CheckConstraint(condition=Q(month__day=1), name="monthly_theme_first_of_month"),
        ]

    def __str__(self):
        return self.title

    @property
    def has_cover(self):
        """True when a cover picture is stored."""
        return bool(self.cover_image and self.cover_image.name)


class MonthlyResource(TimestampedModel, FileCleanupModel):
    class Kind(models.TextChoices):
        BACKGROUND = "background", "Background"
        LOGO = "logo", "Logo"
        WALLPAPER = "wallpaper", "Wallpaper"
        OTHER = "other", "Other"

    monthly_theme = models.ForeignKey(MonthlyTheme, on_delete=models.CASCADE, related_name="resources")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(
        upload_to=private_upload_to,
        storage=private_storage,
        validators=[validate_monthly_file],
    )
    original_filename = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "monthly_resource"

    FILE_ORIGINAL_NAME_FIELDS = {"file": "original_filename"}

    @property
    def is_image(self):
        """True for a picture, which the month pages show as a thumbnail."""
        name = (self.original_filename or self.file.name or "").lower()
        return name.endswith(IMAGE_EXTENSIONS)


class WeeklyPowerpoint(TimestampedModel, ArchivableModel, FileCleanupModel):
    title = models.CharField(max_length=255)       # the week's topic
    description = models.TextField(blank=True)
    week_start = models.DateField()
    monthly_theme = models.ForeignKey(
        MonthlyTheme, null=True, blank=True, on_delete=models.SET_NULL, related_name="weekly_powerpoints",
    )
    file = models.FileField(
        upload_to=private_upload_to,
        storage=private_storage,
        validators=[validate_resource_upload],
    )
    original_filename = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "weekly_powerpoint"      # admin + coordinator only: checked in code

    FILE_ORIGINAL_NAME_FIELDS = {"file": "original_filename"}


class GeneralResource(TimestampedModel, ArchivableModel, FileCleanupModel):
    class Category(models.TextChoices):
        LOGO = "logo", "Logo"
        FONT = "font", "Font"
        LAYOUT = "layout", "Layout"
        OTHER = "other", "Other"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    file = models.FileField(
        upload_to=private_upload_to,
        storage=private_storage,
        validators=[validate_resource_upload],
    )
    original_filename = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "general_resource"

    FILE_ORIGINAL_NAME_FIELDS = {"file": "original_filename"}