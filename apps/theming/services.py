"""theming.services: reading and changing a person's own display settings.

Personal preferences (color mode, mobile menu style, event reminders) are deliberately NOT
written to audit_log: they are not an administrative event (build plan, rule 9.6).
"""
from django.db import transaction

from apps.core.services import ServiceError

from .models import UserSettings

# How early a reminder can come, in minutes (brick 13). "Off" is event_reminders_enabled=False.
REMINDER_MINUTES = (1440, 180, 60)


def get_user_settings(user):
    """Return the person's settings row, creating it with the defaults if it is missing.

    The post_save signal and the backfill migration normally guarantee the row; this is the
    safety net so the settings page never fails for an account that somehow has none.
    """
    row, _ = UserSettings.objects.get_or_create(user=user)
    return row


def update_user_settings(
    user, *, color_mode, mobile_nav_style,
    event_reminders_enabled=None, event_reminder_minutes=None,
):
    """Save the signed-in person's own display and reminder settings.

    Returns (row, changed). Only `user` is ever edited: the caller passes request.user and
    nothing from the request names another account. Raises ServiceError for a missing
    account or a value outside the allowed choices (the form checks the same, this is the
    second guard).

    `event_reminders_enabled` / `event_reminder_minutes` left as None are not touched. Turning
    reminders off passes enabled=False and minutes=None, so the lead time is remembered.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        raise ServiceError("You must be signed in to change your settings.")
    if color_mode not in UserSettings.ColorMode.values:
        raise ServiceError("Choose light, dark or follow device.")
    if mobile_nav_style not in UserSettings.MobileNav.values:
        raise ServiceError("Choose the bottom bar or the hamburger menu.")
    if event_reminders_enabled is not None and not isinstance(event_reminders_enabled, bool):
        raise ServiceError("Choose 1 day, 3 hours, 1 hour or off.")
    if event_reminder_minutes is not None and event_reminder_minutes not in REMINDER_MINUTES:
        raise ServiceError("Choose 1 day, 3 hours, 1 hour or off.")

    new_values = {"color_mode": color_mode, "mobile_nav_style": mobile_nav_style}
    if event_reminders_enabled is not None:
        new_values["event_reminders_enabled"] = event_reminders_enabled
    if event_reminder_minutes is not None:
        new_values["event_reminder_minutes"] = event_reminder_minutes

    with transaction.atomic():
        row, _ = UserSettings.objects.select_for_update().get_or_create(user=user)
        changed_fields = [name for name, value in new_values.items() if getattr(row, name) != value]
        if changed_fields:
            for name in changed_fields:
                setattr(row, name, new_values[name])
            # updated_at is auto_now, so it must be listed to be written.
            row.save(update_fields=[*changed_fields, "updated_at"])
    return row, bool(changed_fields)
