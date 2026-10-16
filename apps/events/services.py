"""events.services: create, edit and cancel events.

Every function: checks permission first (`permissions.require`), runs in `transaction.atomic()`,
locks the row it changes and re-checks permission on the LOCKED row, raises `ServiceError` with a
message that is safe to show, writes an audit entry and, for a time or place change or a
cancellation, sends the notices in the same transaction.

Notification types declared here (the `type` column has no choices, the list keeps growing):
    event_changed    time or place changed (a title or description edit does not notify)
    event_cancelled  the event was cancelled (important)
    event_reminder   an upcoming event, sent by the 15-minute job
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Extension, Role, Status, User
from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.core.services import ServiceError, consume_submission_token
from apps.notifications.models import Notification
from apps.notifications.services import notify_many
from apps.audit.models import AuditLog

from . import permissions
from .models import Event, EventReminder
from datetime import timedelta

EVENT_CHANGED = "event_changed"
EVENT_CANCELLED = "event_cancelled"
EVENT_REMINDER = "e255vent_reminder"
REMINDER_MAX_MINUTES = 43200   # same ceiling as UserSettings.event_reminder_minutes
REMINDER_KEEP_DAYS = 30        # EventReminder rows older than this are trimmed by send_reminders
EVENT_REMINDER = "event_reminder"
REMINDER_MAX_MINUTES = 43200

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


def _event_url(event):
    """The event page, as a path on this site (used by push messages)."""
    return f"/events/{event.pk}/"


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
                push={
                    "title": locked.title,
                    "body": f"The {' and '.join(changes)} changed.",
                    "url": _event_url(locked), "tag": f"event-{locked.pk}",
                },
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
            push={
                "title": locked.title, "body": "This event was cancelled.",
                "url": _event_url(locked), "tag": f"event-{locked.pk}",
            },
        )
    return locked


# ----------------------------------------------------------------- reminders
def _reminder_push(event):
    """Push text: the event title and its start in Manila time. Nothing private."""
    local = timezone.localtime(event.start_at)
    return {
        "title": event.title,
        "body": f"Starts {local:%a, %b %d at %I:%M %p}",
        "url": _event_url(event),
        "tag": f"event-reminder-{event.pk}",
    }


def _pending_by_lead(event, now):
    """{lead_minutes: [user ids]} still to be reminded about `event` right now.

    Due means the person's lead time has been reached and the event has not started. "Reached"
    (not "within this exact quarter hour") so a missed run catches up on the next one.
    Already-sent reminders for this start time are left out.
    """
    ids = event_recipients(event)
    if not ids:
        return {}
    due = {}
    wanted = User.objects.filter(pk__in=ids, settings__event_reminders_enabled=True).values_list(
        "pk", "settings__event_reminder_minutes",
    )
    for user_id, lead in wanted:
        if event.start_at - timedelta(minutes=lead) <= now:
            due.setdefault(lead, []).append(user_id)
    if not due:
        return {}
    done = set(
        EventReminder.objects.filter(
            event=event, event_start=event.start_at, lead_minutes__in=list(due),
        ).values_list("user_id", "lead_minutes")
    )
    pending = {}
    for lead, user_ids in due.items():
        left = [uid for uid in user_ids if (uid, lead) not in done]
        if left:
            pending[lead] = left
    return pending


def _upcoming_event_ids(now):
    """Ids of live, not-cancelled events that have not started and start within the longest lead."""
    return list(
        Event.objects.filter(
            archived_at__isnull=True, cancelled_at__isnull=True,
            start_at__gt=now, start_at__lte=now + timedelta(minutes=REMINDER_MAX_MINUTES),
        ).order_by("start_at").values_list("pk", flat=True)
    )


def due_reminders(now=None):
    """What the job would send now, without sending: [(event_id, {lead_minutes: [user ids]})].

    Read-only (no locks), for tests and dry runs. `send_reminders` re-checks under a lock.
    """
    now = now or timezone.now()
    out = []
    for event in Event.objects.filter(pk__in=_upcoming_event_ids(now)).order_by("start_at"):
        pending = _pending_by_lead(event, now)
        if pending:
            out.append((event.pk, pending))
    return out


def send_reminders(now=None):
    """Send every reminder that is due. Returns {"events": n, "reminders": n} (counts only).

    One transaction per event: lock it, re-check it is still upcoming and not cancelled, record
    an EventReminder row per person and send the bell items (and pushes, after commit). Running
    it twice in a row sends nothing new. Writes ONE audit row (source system_job, counts only),
    and only when something was sent, so the 15-minute job does not fill the trail.
    """
    now = now or timezone.now()
    result = {"events": 0, "reminders": 0}
    for event_id in _upcoming_event_ids(now):
        try:
            with transaction.atomic():
                locked = _lock(event_id)
                if locked.is_cancelled or locked.start_at <= now:
                    continue
                pending = _pending_by_lead(locked, now)
                if not pending:
                    continue
                sent = 0
                for lead, user_ids in pending.items():
                    EventReminder.objects.bulk_create(
                        [
                            EventReminder(
                                event=locked, user_id=uid, lead_minutes=lead,
                                event_start=locked.start_at,
                            )
                            for uid in user_ids
                        ],
                        ignore_conflicts=True,
                    )
                    sent += notify_many(
                        user_ids, EVENT_REMINDER,
                        entity_type="event", entity_id=locked.pk,
                        data={
                            "title": locked.title, "lead_minutes": lead,
                            "starts_at": locked.start_at.isoformat(),
                        },
                        push=_reminder_push(locked),
                    )
                result["events"] += 1
                result["reminders"] += sent
        except ServiceError:
            continue   # the event vanished between the list and the lock
    # tidy up: old reminder rows are no longer needed once the event is long past
    EventReminder.objects.filter(event_start__lt=now - timedelta(days=REMINDER_KEEP_DAYS)).delete()
    if result["reminders"]:
        log_action("event.reminders_sent", source=AuditLog.Source.SYSTEM_JOB, after=result)
    return result
