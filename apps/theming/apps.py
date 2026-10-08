# Merge into the apps.py that `startapp` generated (keep name = "theming").
from django.apps import AppConfig


class ThemingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.theming"
    label = "theming"

    def ready(self):
        from . import signals  # noqa: F401