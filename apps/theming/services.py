"""theming.services: reading and changing a person's own display settings.

Personal preferences (color mode, mobile menu style) are deliberately NOT written to
audit_log: they are not an administrative event (build plan, rule 9.6).
"""
from django.db import transaction

from apps.core.services import ServiceError

from .models import UserSettings


def get_user_settings(user):
    """Return the person's settings row, creating it with the defaults if it is missing.

    The post_save signal and the backfill migration normally guarantee the row; this is the
    safety net so the settings page never fails for an account that somehow has none.
    """
    row, _ = UserSettings.objects.get_or_create(user=user)
    return row


def update_user_settings(user, *, color_mode, mobile_nav_style):
    """Save the signed-in person's own display settings.

    Returns (row, changed). Only `user` is ever edited: the caller passes request.user and
    nothing from the request names another account. Raises ServiceError for a missing
    account or a value outside the allowed choices (the form checks the same, this is the
    second guard).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        raise ServiceError("You must be signed in to change your settings.")
    if color_mode not in UserSettings.ColorMode.values:
        raise ServiceError("Choose light, dark or follow device.")
    if mobile_nav_style not in UserSettings.MobileNav.values:
        raise ServiceError("Choose the bottom bar or the hamburger menu.")

    with transaction.atomic():
        row, _ = UserSettings.objects.select_for_update().get_or_create(user=user)
        changed = (row.color_mode, row.mobile_nav_style) != (color_mode, mobile_nav_style)
        if changed:
            row.color_mode = color_mode
            row.mobile_nav_style = mobile_nav_style
            # updated_at is auto_now, so it must be listed to be written.
            row.save(update_fields=["color_mode", "mobile_nav_style", "updated_at"])
    return row, changed
