"""resources: monthly theme (+ files), weekly PowerPoint, general resources."""
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import ArchivableModel, TimestampedModel

USER = settings.AUTH_USER_MODEL


class MonthlyTheme(TimestampedModel, ArchivableModel):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    month = models.DateField(unique=True)          # always the 1st of the month
    is_published = models.BooleanField(default=False)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "monthly_theme"
        constraints = [
            models.CheckConstraint(condition=Q(month__day=1), name="monthly_theme_first_of_month"),
        ]

    def __str__(self):
        return self.title


class MonthlyResource(TimestampedModel):
    class Kind(models.TextChoices):
        BACKGROUND = "background", "Background"
        LOGO = "logo", "Logo"
        WALLPAPER = "wallpaper", "Wallpaper"
        OTHER = "other", "Other"

    monthly_theme = models.ForeignKey(MonthlyTheme, on_delete=models.CASCADE, related_name="resources")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to="monthly_resources/")
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "monthly_resource"


class WeeklyPowerpoint(TimestampedModel, ArchivableModel):
    title = models.CharField(max_length=255)       # the week's topic
    description = models.TextField(blank=True)
    week_start = models.DateField()
    monthly_theme = models.ForeignKey(
        MonthlyTheme, null=True, blank=True, on_delete=models.SET_NULL, related_name="weekly_powerpoints",
    )
    file = models.FileField(upload_to="weekly_ppt/")
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "weekly_powerpoint"      # admin + coordinator only: checked in code


class GeneralResource(TimestampedModel, ArchivableModel):
    class Category(models.TextChoices):
        LOGO = "logo", "Logo"
        FONT = "font", "Font"
        LAYOUT = "layout", "Layout"
        OTHER = "other", "Other"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    file = models.FileField(upload_to="general_resources/")
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "general_resource"