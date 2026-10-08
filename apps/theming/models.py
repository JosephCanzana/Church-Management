"""theming: admin-editable themes + per-user display/reminder settings.

UserSettings lives here (not in accounts) so the dependency runs one way:
theming -> accounts. A post_save signal (signals.py) creates the row.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q

from apps.core.models import TimestampedModel

hex_validator = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a #RRGGBB hex colour.")


def hex_color():
    return models.CharField(max_length=7, validators=[hex_validator])


class Theme(TimestampedModel):
    name = models.CharField(max_length=100, unique=True)
    is_enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    is_builtin = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "theme"
        constraints = [
            # exactly one default theme
            models.UniqueConstraint(
                fields=["is_default"], condition=Q(is_default=True), name="uq_theme_default",
            ),
        ]

    def __str__(self):
        return self.name


class ThemePalette(models.Model):
    class Mode(models.TextChoices):
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"

    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name="palettes")
    mode = models.CharField(max_length=5, choices=Mode.choices)
    color_primary = hex_color()
    color_primary_hover = hex_color()
    color_primary_soft = hex_color()
    color_bg = hex_color()
    color_surface = hex_color()
    color_surface_alt = hex_color()
    color_txt_primary = hex_color()
    color_txt_secondary = hex_color()
    color_txt_muted = hex_color()
    color_border = hex_color()
    color_border_strong = hex_color()
    color_success = hex_color()
    color_error = hex_color()
    color_warning = hex_color()
    color_info = hex_color()
    color_accent = hex_color()
    color_accent_hover = hex_color()
    color_accent_soft = hex_color()

    class Meta:
        db_table = "theme_palette"
        constraints = [
            models.UniqueConstraint(fields=["theme", "mode"], name="uq_theme_palette_mode"),
        ]

    def __str__(self):
        return f"{self.theme.name} / {self.mode}"


class UserSettings(models.Model):
    class ColorMode(models.TextChoices):
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"
        SYSTEM = "system", "Follow device"

    class Language(models.TextChoices):
        EN = "en", "English"
        FIL = "fil", "Filipino"

    class MobileNav(models.TextChoices):
        BOTTOM = "bottom", "Bottom bar"
        HAMBURGER = "hamburger", "Hamburger menu"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, primary_key=True,
        on_delete=models.CASCADE, related_name="settings",
    )
    theme = models.ForeignKey(
        Theme, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )  # NULL = site default
    color_mode = models.CharField(max_length=10, choices=ColorMode.choices, default=ColorMode.SYSTEM)
    language = models.CharField(max_length=3, choices=Language.choices, default=Language.EN)
    mobile_nav_style = models.CharField(max_length=10, choices=MobileNav.choices, default=MobileNav.BOTTOM)
    event_reminders_enabled = models.BooleanField(default=True)
    event_reminder_minutes = models.PositiveIntegerField(
        default=1440, validators=[MinValueValidator(0), MaxValueValidator(43200)],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_settings"
        constraints = [
            models.CheckConstraint(
                condition=Q(event_reminder_minutes__lte=43200), name="reminder_minutes_max",
            ),
        ]