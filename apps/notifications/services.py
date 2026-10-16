"""notifications.services: the one place that creates bell items.

Other apps call `notify()` / `notify_many()` inside their own `transaction.atomic()` (together
with `log_action()`), so the change and its notices commit or roll back together.

PRIVACY (AGENTS.md): never put prayer text, goals, notes or tithe amounts in `data`. A notice says
THAT something happened; the page it links to does the permission check.

`notifications` never imports a feature app: callers pass plain ids, a type string and an
entity type and id (no foreign keys).
"""
from .models import Notification, NotificationPreference

BATCH = 500


def _entity_pair(entity_type, entity_id):
    """The table refuses half an entity reference, so refuse it here with a clear message."""
    if (entity_type is None) != (entity_id is None):
        raise ValueError("entity_type and entity_id must be given together.")


def muted_user_ids(user_ids, type):
    """Of `user_ids`, those who turned OFF in-app alerts of this type."""
    return set(
        NotificationPreference.objects.filter(user_id__in=list(user_ids), type=type, in_app=False)
        .values_list("user_id", flat=True)
    )


def notify(
    user, type, *, actor=None, entity_type=None, entity_id=None, data=None,
    dedupe_key=None, importance=Notification.Importance.NORMAL, audit_log_id=None,
):
    """Create one bell item for `user` (a User or an id). Returns it, or None when skipped.

    Skipped when the person turned off in-app alerts of this `type`, or when a notice with the
    same `dedupe_key` already exists for them. It does NOT skip the actor: callers choose the
    recipients.
    """
    _entity_pair(entity_type, entity_id)
    user_id = getattr(user, "pk", user)
    if muted_user_ids([user_id], type):
        return None
    values = {
        "type": type,
        "actor_id": getattr(actor, "pk", None),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "data": data or {},
        "importance": importance,
        "audit_log_id": audit_log_id,
    }
    if dedupe_key is not None:
        row, created = Notification.objects.get_or_create(
            user_id=user_id, dedupe_key=dedupe_key, defaults=values,
        )
        return row if created else None
    return Notification.objects.create(user_id=user_id, **values)


def notify_many(
    user_ids, type, *, actor=None, entity_type=None, entity_id=None, data=None,
    importance=Notification.Importance.NORMAL, audit_log_id=None,
):
    """Create the same bell item for every id in `user_ids`; returns how many were created.

    People who turned off in-app alerts of this `type` are left out. Callers decide who the
    recipients are (and leave the actor out of them). One query for the preferences, then bulk
    inserts, so a global event does not cost one query per person.
    """
    _entity_pair(entity_type, entity_id)
    ids = list(dict.fromkeys(user_ids))
    if not ids:
        return 0
    muted = muted_user_ids(ids, type)
    rows = [
        Notification(
            user_id=user_id, type=type, actor_id=getattr(actor, "pk", None),
            entity_type=entity_type, entity_id=entity_id, data=dict(data or {}),
            importance=importance, audit_log_id=audit_log_id,
        )
        for user_id in ids if user_id not in muted
    ]
    Notification.objects.bulk_create(rows, batch_size=BATCH)
    return len(rows)
