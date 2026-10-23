"""accounts.services.job_settings: super-admin changes to job settings and retention periods.

Every function checks `require_site_settings_manager`, locks the row, and is idempotent: posting the
value that is already stored changes nothing, writes no audit row and returns False. A real change
returns True and logs the key with before and after (numbers and switches only, no free text).
Reading is done through `apps.core.job_settings` so the jobs never import `accounts`.
"""
from django.db import transaction

from apps.accounts.permissions import require_site_settings_manager
from apps.audit.services import log_action
from apps.core import job_settings
from apps.core.models import RetentionPolicy, SiteSetting
from apps.core.services import ServiceError

MAX_RETENTION_DAYS = 3650  # ten years: a sanity cap, not a business rule


def _update_setting(actor, request, key, default, new_value, audit_action):
    """Store `new_value` under `key` if it differs; log and return True only on a real change."""
    with transaction.atomic():
        row, _ = SiteSetting.objects.select_for_update().get_or_create(key=key, defaults={"value": default})
        old = row.value
        if old == new_value:
            return False
        row.value, row.updated_by = new_value, actor
        row.save(update_fields=["value", "updated_by", "updated_at"])
        log_action(audit_action, actor=actor, request=request, entity_type="site_setting",
                   entity_label=key, before={"value": old}, after={"value": new_value})
    return True


def set_goal_reminders_enabled(actor, request, enabled):
    """Switch goal reminder pushes on or off. Returns True when the value changed."""
    require_site_settings_manager(actor)
    return _update_setting(
        actor, request, job_settings.GOAL_REMINDERS_KEY, job_settings.DEFAULT_GOAL_REMINDERS,
        bool(enabled), "site_setting.goal_reminders_toggled",
    )


def set_end_request_days(actor, request, days):
    """Set how many days an unanswered buddy end request waits. Returns True when it changed."""
    require_site_settings_manager(actor)
    try:
        if isinstance(days, bool):
            raise ValueError
        days = int(str(days).strip())
    except (TypeError, ValueError):
        raise ServiceError("Enter a whole number of days.")
    low, high = job_settings.MIN_END_REQUEST_DAYS, job_settings.MAX_END_REQUEST_DAYS
    if not low <= days <= high:
        raise ServiceError(f"Days must be between {low} and {high}.")
    return _update_setting(
        actor, request, job_settings.END_REQUEST_DAYS_KEY, job_settings.DEFAULT_END_REQUEST_DAYS,
        days, "site_setting.buddy_end_request_days_changed",
    )


def set_retention_days(actor, request, entity_type, days):
    """Change one auto-delete period; `days` of None or "" means never delete.

    Returns True when the value changed. Only rows that already exist can be edited. NOTE: the
    `set_purge_at()` database trigger fills `purge_at` when a record is ARCHIVED, so a new value
    applies to records archived from now on; records already archived keep their old date.
    """
    require_site_settings_manager(actor)
    if days in (None, ""):
        new_days = None
    else:
        try:
            if isinstance(days, bool):
                raise ValueError
            new_days = int(str(days).strip())
        except (TypeError, ValueError):
            raise ServiceError("Enter a whole number of days, or choose never.")
        if not 1 <= new_days <= MAX_RETENTION_DAYS:
            raise ServiceError(f"Days must be between 1 and {MAX_RETENTION_DAYS}.")
    with transaction.atomic():
        row = RetentionPolicy.objects.select_for_update().filter(pk=entity_type).first()
        if row is None:
            raise ServiceError("That record type was not found.")
        old_days = row.purge_after_days
        if old_days == new_days:
            return False
        row.purge_after_days, row.updated_by = new_days, actor
        row.save(update_fields=["purge_after_days", "updated_by", "updated_at"])
        log_action("retention_policy.change", actor=actor, request=request,
                   entity_type="retention_policy", entity_label=entity_type,
                   before={"purge_after_days": old_days}, after={"purge_after_days": new_days})
    return True
