"""bible.site_settings: the super-admin turns a Bible version on or off.

Two guards, as everywhere else: the view is wrapped in ``role_required`` and
each function here starts with ``require_site_settings_manager`` (the action
guard). Both functions are idempotent: asking for the state a switch already
has changes nothing, writes no audit row and returns False, so a double click
or a second tab is harmless.

Rule: at least one version must stay readable, otherwise the Bible reader has
nothing to show. "Readable" means switched on (ESV also needs the master switch
on). Automatic ESV locks do not count here; they clear by themselves.

Audit rows say which switch changed and its old and new value, nothing else.
"""
from django.db import transaction

from apps.accounts.permissions import require_site_settings_manager
from apps.audit.services import log_action
from apps.core.models import SiteSetting
from apps.core.services import ServiceError

from .switches import KEY_MASTER, switch_key
from .versions import SOURCE_ESV, VERSIONS, VERSIONS_BY_CODE

AUDIT_VERSION = "site_setting.bible_version_toggled"
AUDIT_ONLINE = "site_setting.bible_online_toggled"

MSG_LAST_ONE = "At least one Bible version must stay on, so readers always have something to read."


def _lock_rows():
    """Create any missing switch rows (on) and lock them all, in key order.

    Locking in one fixed order keeps two simultaneous requests from deadlocking.
    Returns {key: SiteSetting}.
    """
    keys = sorted([KEY_MASTER, *(switch_key(version) for version in VERSIONS)])
    rows = {}
    for key in keys:
        row, _ = SiteSetting.objects.select_for_update().get_or_create(key=key, defaults={"value": True})
        rows[key] = row
    return rows


def _someone_can_read(rows, turning_off_key):
    """True if some version stays readable once ``turning_off_key`` is off."""
    def is_on(key):
        return False if key == turning_off_key else bool(rows[key].value)

    master_on = is_on(KEY_MASTER)
    return any(
        is_on(switch_key(version)) and (master_on or version.source != SOURCE_ESV)
        for version in VERSIONS
    )


def _apply(actor, request, *, key, enabled, action):
    """Set one switch under a lock. Returns True if it changed, False if it already was."""
    with transaction.atomic():
        rows = _lock_rows()
        row = rows[key]
        current = bool(row.value)
        if current == enabled:
            return False
        if not enabled and not _someone_can_read(rows, key):
            raise ServiceError(MSG_LAST_ONE)
        row.value = enabled
        row.updated_by = actor
        row.save(update_fields=["value", "updated_by", "updated_at"])
        log_action(
            action,
            actor=actor,
            request=request,
            entity_type="site_setting",
            entity_label=key,
            before={"enabled": current},
            after={"enabled": enabled},
        )
    return True


def set_version_enabled(actor, code, enabled, *, request=None):
    """Turn one Bible version on or off. True if it changed, False if already so."""
    require_site_settings_manager(actor)
    version = VERSIONS_BY_CODE.get(code)
    if version is None:
        raise ServiceError("That Bible version does not exist.")
    return _apply(actor, request, key=switch_key(version), enabled=bool(enabled), action=AUDIT_VERSION)


def set_online_services_enabled(actor, enabled, *, request=None):
    """Turn every online Bible service (today ESV) on or off at once."""
    require_site_settings_manager(actor)
    return _apply(actor, request, key=KEY_MASTER, enabled=bool(enabled), action=AUDIT_ONLINE)
