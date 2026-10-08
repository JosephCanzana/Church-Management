from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import SiteSetting

from .models import UserSettings


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_settings(sender, instance, created, raw=False, **kwargs):
    """New users start with the site default language and reminder time."""
    if not created or raw:
        return
    lang = SiteSetting.objects.filter(key="default_language").values_list("value", flat=True).first()
    mins = SiteSetting.objects.filter(key="event_reminder_default_minutes").values_list("value", flat=True).first()
    UserSettings.objects.get_or_create(
        user=instance,
        defaults={
            "language": lang if lang in ("en", "fil") else "en",
            "event_reminder_minutes": mins if isinstance(mins, int) else 1440,
        },
    )