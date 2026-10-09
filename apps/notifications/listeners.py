"""notifications.listeners: turn events from other apps into bell notifications.

`notifications` depends on `accounts` (never the other way round), so it listens
to the signals accounts sends. This file must be imported once when Django starts:
add it to NotificationsConfig.ready() (see the chat message).

PRIVACY (AGENTS.md): a notification says THAT something happened. It never carries
the email address, who took it, or any private text; the bell shows a fixed sentence
chosen from `type`.
"""
from django.dispatch import receiver

from apps.accounts.signals import email_released

from .models import Notification, NotificationPreference

EMAIL_RELEASED = "email_released"


@receiver(email_released, dispatch_uid="notifications.email_released")
def notify_email_released(sender, user, **kwargs):
    """Tell someone their pending (unverified) email was removed because another account verified it.

    Honors the person's own in-app preference for this type (default: on).
    Runs inside the sender's transaction, so it is undone if that rolls back.
    """
    preference = NotificationPreference.objects.filter(user=user, type=EMAIL_RELEASED).first()
    if preference is not None and not preference.in_app:
        return
    Notification.objects.create(
        user=user,
        type=EMAIL_RELEASED,
        importance=Notification.Importance.IMPORTANT,
        entity_type="user",
        entity_id=user.pk,
        data={"reason": "verified_by_another_account"},
    )