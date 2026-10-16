"""notifications.services: the one place that creates bell items, and the place that manages push devices.

Other apps call `notify()` / `notify_many()` inside their own `transaction.atomic()` (together
with `log_action()`), so the change and its notices commit or roll back together.

PRIVACY (AGENTS.md): never put prayer text, goals, notes or tithe amounts in `data`. A notice says
THAT something happened; the page it links to does the permission check.

PUSH (Brick 12): pass `push={"title": ..., "body": ..., "url": "/events/12/"}` to also send a Web Push.
The bell item is always created first. The push goes out after the transaction commits, only to people
who did not mute that type, and a push failure can never undo or block the notice.

`notifications` never imports a feature app: callers pass plain ids, a type string and an
entity type and id (no foreign keys).
"""
import re

from django.db import transaction

from apps.accounts.models import Status
from apps.audit.services import log_action
from apps.core.services import ServiceError

from . import push as push_module
from .models import Notification, NotificationPreference, PushSubscription

BATCH = 500
MAX_DEVICES = 10                                   # per person; stops one account filling the table
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-+/=]{8,255}$")   # base64 / base64url characters only


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
    dedupe_key=None, importance=Notification.Importance.NORMAL, audit_log_id=None, push=None,
):
    """Create one bell item for `user` (a User or an id). Returns it, or None when skipped.

    Skipped when the person turned off in-app alerts of this `type`, or when a notice with the
    same `dedupe_key` already exists for them. It does NOT skip the actor: callers choose the
    recipients. `push` (a mapping with title, body, url) also sends a Web Push for a notice that was
    created, which means a muted type or a duplicate never pushes.
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
        if not created:
            return None
    else:
        row = Notification.objects.create(user_id=user_id, **values)
    if push:
        push_module.dispatch_after_commit([user_id], push)
    return row


def notify_many(
    user_ids, type, *, actor=None, entity_type=None, entity_id=None, data=None,
    importance=Notification.Importance.NORMAL, audit_log_id=None, push=None,
):
    """Create the same bell item for every id in `user_ids`; returns how many were created.

    People who turned off in-app alerts of this `type` are left out. Callers decide who the
    recipients are (and leave the actor out of them). One query for the preferences, then bulk
    inserts, so a global event does not cost one query per person. With `push`, the same people
    who got a bell item also get a Web Push (once, after commit).
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
    if push and rows:
        push_module.dispatch_after_commit([row.user_id for row in rows], push)
    return len(rows)


# ================================================================ push devices (Brick 12)
def _check_owner(user):
    """Permission guard: only a signed-in person who can still sign in manages their own devices."""
    if user is None or not getattr(user, "pk", None) or not getattr(user, "is_authenticated", False):
        raise ServiceError("Sign in to manage notifications.")
    if getattr(user, "status", None) in (Status.ARCHIVED, Status.SUSPENDED):
        raise ServiceError("This account cannot manage notifications.")


def _audit(actor, action, subscription_id, label):
    """Write the audit entry. Only the device label is recorded: never the endpoint or the keys."""
    log_action(
        actor=actor, action=action,
        entity_type="push_subscription", entity_id=subscription_id,
        after={"label": label},
    )


def list_devices(user):
    """The person's own devices, newest first."""
    _check_owner(user)
    return list(PushSubscription.objects.filter(user=user).order_by("-created_at"))


def is_registered(user, endpoint):
    """True when this person has a device with this endpoint (the page uses it to keep both sides in step)."""
    _check_owner(user)
    if not isinstance(endpoint, str) or not endpoint:
        return False
    return PushSubscription.objects.filter(user=user, endpoint_hash=push_module.hash_endpoint(endpoint)).exists()


def register_device(user, *, endpoint, p256dh, auth, user_agent=""):
    """Save (or refresh) this browser as a push device for `user`. Returns the PushSubscription.

    The same browser signing in as someone else moves the device to the new person, so one phone never
    pushes the previous owner's notices to the next person. Refuses unknown push services, bad keys and
    more than `MAX_DEVICES` devices.
    """
    _check_owner(user)
    if not push_module.is_enabled():
        raise ServiceError("Push notifications are not set up on this server.")
    if not isinstance(endpoint, str) or not isinstance(p256dh, str) or not isinstance(auth, str):
        raise ServiceError("This device sent incomplete details.")
    if len(endpoint) > 2000 or not push_module.endpoint_allowed(endpoint):
        raise ServiceError("This browser's push service is not supported.")
    if not _KEY_PATTERN.match(p256dh) or not _KEY_PATTERN.match(auth):
        raise ServiceError("This device sent incomplete details.")

    label = push_module.device_label(user_agent)
    digest = push_module.hash_endpoint(endpoint)
    with transaction.atomic():
        existing = PushSubscription.objects.select_for_update().filter(endpoint_hash=digest).first()
        if existing is None and PushSubscription.objects.filter(user=user).count() >= MAX_DEVICES:
            raise ServiceError(f"You can have up to {MAX_DEVICES} devices. Remove one first.")
        subscription, _created = PushSubscription.objects.update_or_create(
            endpoint_hash=digest,
            defaults={"user": user, "endpoint": endpoint, "p256dh": p256dh, "auth": auth, "label": label},
        )
        _audit(user, "push.subscribe", subscription.pk, label)
    return subscription


def unregister_device(user, endpoint):
    """Remove this person's device that has this endpoint. Returns True when one was removed."""
    _check_owner(user)
    if not isinstance(endpoint, str) or not endpoint:
        return False
    with transaction.atomic():
        subscription = (
            PushSubscription.objects.select_for_update()
            .filter(user=user, endpoint_hash=push_module.hash_endpoint(endpoint)).first()
        )
        if subscription is None:
            return False
        pk, label = subscription.pk, subscription.label
        subscription.delete()
        _audit(user, "push.unsubscribe", pk, label)
    return True


def remove_device(user, pk):
    """Remove one of the person's own devices by id (the Settings list). Someone else's id is "not found"."""
    _check_owner(user)
    with transaction.atomic():
        subscription = PushSubscription.objects.select_for_update().filter(pk=pk, user=user).first()
        if subscription is None:
            raise ServiceError("That device was not found.")
        label = subscription.label
        subscription.delete()
        _audit(user, "push.remove_device", pk, label)
