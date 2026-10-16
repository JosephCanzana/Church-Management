"""events.services: create, edit and cancel events.

Every function: checks permission first (`permissions.require`), runs in `transaction.atomic()`,
locks the row it changes and re-checks permission on the LOCKED row, raises `ServiceError` with a
message that is safe to show, writes an audit entry and, for a time or place change or a
cancellation, sends the notices in the same transaction.

Notification types declared here (the `type` column has no choices, the list keeps growing):
    event_changed    time or place changed (a title or description edit does not notify)
    event_cancelled  the event was cancelled (important)
"""
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Extension, Role, Status, User
from apps.audit.services import log_action
from apps.core.services import ServiceError, consume_submission_token
from apps.notifications.models import Notification
from apps.notifications.services import notify_many

from . import permissions
from .models import Event

EVENT_CHANGED = "event_changed"
EVENT_CANCELLED = "event_cancelled"

TITLE_MAX = 255
LOCATION_MAX = 255

# Fields whose old and new value go to the audit log (description only as a flag, see update_event).
_AUDITED = ("title", "start_at", "end_at", "location")


# ----------------------------------------------------------------- helpers
def _aware(value, label):
    """An aware datetime (a naive one is read as Manila time), or ServiceError when missing."""
    if value is None:
        raise ServiceError(f"{label} is required.")
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value


def _clean(*, title, description, start_at, end_at, location):
    """Validate and normalise the editable fields; returns them as a dict."""
    title = (title or "").strip()
    if not title:
        raise ServiceError("Give the event a title.")
    if len(title) > TITLE_MAX:
        raise ServiceError(f"The title can be at most {TITLE_MAX} characters.")
    location = (location or "").strip()
    if len(location) > LOCATION_MAX:
        raise ServiceError(f"The location can be at most {LOCATION_MAX} characters.")
    start_at = _aware(start_at, "The start time")
    if end_at is not None:
        end_at = _aware(end_at, "The end time")
        if end_at <= start_at:
            raise ServiceError("The end must be after the start.")
    return {
        "title": title,
        "description": (description or "").strip(),
        "location": location,
        "start_at": start_at,
        "end_at": end_at,
    }


def _lock(event):
    """Lock and reload an event (an Event or an id). No select_related: Postgres refuses
    FOR UPDATE on the nullable side of a join."""
    pk = getattr(event, "pk", event)
    try:
        return Event.objects.select_for_update().get(pk=pk, archived_at__isnull=True)
    except Event.DoesNotExist:
        raise ServiceError("This event no longer exists.")


def event_recipients(event, actor=None):
    """Ids of the people who get a notice about `event`.

    Global event: everyone with an ACTIVE account. Extension event: that extension's ACTIVE
    members and coordinator. Never the person who made the change. Suspended, archived and
    not-yet-activated accounts are skipped. (People who turned event alerts off are left out
    later, by `notify_many`.)
    """
    people = User.objects.filter(status=Status.ACTIVE)
    if event.extension_id is not None:
        people = people.filter(
            extension_id=event.extension_id, role__in=[Role.MEMBER, Role.COORDINATOR],
        )
    if actor is not None:
        people = people.exclude(pk=actor.pk)
    return list(people.values_list("pk", flat=True))


# ----------------------------------------------------------------- create
def create_event(
    actor, *, extension_id, title, start_at, end_at=None, description="", location="",
    token=None, request=None,
):
    """Create an event. `extension_id=None` makes a global event (admin and super-admin only).

    `token` is optional: pass the form's one-time token to stop a double click from creating
    the event twice (the second call raises ServiceError). Creating does not notify anyone.
    """
    permissions.require(permissions.can_create, actor, extension_id)
    fields = _clean(
        title=title, description=description, start_at=start_at, end_at=end_at, location=location,
    )
    with transaction.atomic():
        if token is not None and not consume_submission_token(token):
            raise ServiceError("This event was already saved.")
        extension = None
        if extension_id is not None:
            extension = Extension.objects.filter(pk=extension_id, archived_at__isnull=True).first()
            if extension is None:
                raise ServiceError("Choose an extension that is still active.")
        event = Event.objects.create(extension=extension, created_by=actor, **fields)
        log_action(
            "event.create", actor=actor, request=request,
            entity_type="event", entity_id=event.pk, entity_label=event.title,
            extension_id=event.extension_id,
            after={
                "title": event.title, "start_at": event.start_at, "end_at": event.end_at,
                "location": event.location, "global": event.extension_id is None,
            },
        )
    return event


# ----------------------------------------------------------------- update
def update_event(
    actor, event, *, title, start_at, end_at=None, description="", location="", request=None,
):
    """Edit an event (send every editable field, as the form does). Returns the saved event.

    The scope (global or which extension) cannot change after creation. The start of an event
    that already started cannot be moved. A change to the time or place notifies the recipients;
    a title or description edit does not. Nothing changed means nothing is saved or logged.
    """
    fields = _clean(
        title=title, description=description, start_at=start_at, end_at=end_at, location=location,
    )
    with transaction.atomic():
        locked = _lock(event)
        permissions.require(permissions.can_manage, actor, locked)
        if locked.is_cancelled:
            raise ServiceError("A cancelled event cannot be changed.")
        if fields["start_at"] != locked.start_at and locked.start_at <= timezone.now():
            raise ServiceError("The start of an event that already happened cannot be moved.")

        before, after = {}, {}
        for name in _AUDITED:
            if getattr(locked, name) != fields[name]:
                before[name] = getattr(locked, name)
                after[name] = fields[name]
        description_changed = locked.description != fields["description"]
        if description_changed:
            after["description_changed"] = True  # the text itself stays out of the audit trail
        if not after:
            return locked

        changes = []
        if "start_at" in after or "end_at" in after:
            changes.append("time")
        if "location" in after:
            changes.append("place")

        for name, value in fields.items():
            setattr(locked, name, value)
        locked.save(update_fields=[*fields, "updated_at"])

        entry = log_action(
            "event.update", actor=actor, request=request,
            entity_type="event", entity_id=locked.pk, entity_label=locked.title,
            extension_id=locked.extension_id, before=before or None, after=after,
        )
        if changes:
            notify_many(
                event_recipients(locked, actor), EVENT_CHANGED,
                actor=actor, entity_type="event", entity_id=locked.pk,
                data={"title": locked.title, "changes": changes},
                audit_log_id=entry.pk,
            )
    return locked


# ----------------------------------------------------------------- cancel
def cancel_event(actor, event, *, request=None):
    """Cancel an event for good. It stays on record, marked cancelled, and recipients are told.

    Refused when it is already cancelled or already over (an event that happened is history,
    not a cancellation).
    """
    with transaction.atomic():
        locked = _lock(event)
        permissions.require(permissions.can_manage, actor, locked)
        if locked.is_cancelled:
            raise ServiceError("This event is already cancelled.")
        now = timezone.now()
        if (locked.end_at or locked.start_at) <= now:
            raise ServiceError("An event that already happened cannot be cancelled.")

        locked.cancelled_at = now
        locked.cancelled_by = actor
        locked.save(update_fields=["cancelled_at", "cancelled_by", "updated_at"])

        entry = log_action(
            "event.cancel", actor=actor, request=request,
            entity_type="event", entity_id=locked.pk, entity_label=locked.title,
            extension_id=locked.extension_id,
            before={"status": "scheduled"}, after={"status": "cancelled"},
        )
        notify_many(
            event_recipients(locked, actor), EVENT_CANCELLED,
            actor=actor, entity_type="event", entity_id=locked.pk,
            data={"title": locked.title},
            importance=Notification.Importance.IMPORTANT,
            audit_log_id=entry.pk,
        )
    return locked
