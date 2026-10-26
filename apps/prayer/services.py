"""prayer.services: sending and replying to prayer requests (a reply also closes the request).

Every function is atomic, re-checks permission on the locked row, writes ONE audit row (ids only) and
creates its bell items in the same transaction. PRIVACY (AGENTS.md): prayer text, reply text and sender
names never go into `audit_log` or `notification.data`; a bell item says THAT something happened and the
page it links to does the permission check.
"""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit.services import log_action
from apps.core.services import ServiceError, consume_submission_token
from apps.notifications.services import notify_many, notify

from . import permissions
from .models import PrayerReply, PrayerRequest, signin_filter

# Notification types, declared here like `event_changed` is in events. (verify) register them wherever events
# registers its type so the bell list and the preferences screen know them.
NOTIFY_REQUEST = "prayer_request"
NOTIFY_REPLIED = "prayer_replied"

MAX_BODY = 2000
MAX_PER_DAY = 5


def _clean(body, limit=MAX_BODY):
    """Trim the text and check its length; raises ServiceError with a friendly message."""
    text = (body or "").strip()
    if not text:
        raise ServiceError("Please write your prayer request.")
    if len(text) > limit:
        raise ServiceError(f"Please keep it under {limit} characters.")
    return text


def _handlers_of(prayer_request):
    """Ids of the people who should hear about a new request: its extension's coordinators, else admins.

    (verify) If an extension stores its coordinator on `Extension` instead of on the user, change ONLY this
    function and `PrayerRequestQuerySet.inbox_for`.
    """
    users = get_user_model().objects.filter(signin_filter())
    coordinators = list(
        users.filter(role=Role.COORDINATOR, extension_id=prayer_request.extension_id).values_list("pk", flat=True)
    )
    if coordinators:
        return coordinators
    return list(users.filter(role__in=(Role.ADMIN, Role.SUPER_ADMIN)).values_list("pk", flat=True))


def send_prayer_request(actor, body, is_anonymous, token):
    """Create a prayer request. Returns a result with `.request` and `.duplicate`.

    A replayed one-time token does nothing (`duplicate=True`). `coordinator` stays NULL and `extension` is the
    sender's extension right now. At most MAX_PER_DAY per person per Manila day.
    """
    permissions.require(permissions.can_send, actor)
    text = _clean(body)
    with transaction.atomic():
        # Lock the sender so two quick taps cannot both pass the daily cap.
        sender = get_user_model().objects.select_for_update().get(pk=actor.pk)
        permissions.require(permissions.can_send, sender)
        # (verify) signature of consume_submission_token; it must run INSIDE atomic so a refusal rolls it back.
        if not consume_submission_token(token):
            return SimpleNamespace(request=None, duplicate=True)
        today = timezone.localdate()
        sent_today = PrayerRequest.objects.filter(user=sender, created_at__date=today).count()
        if sent_today >= MAX_PER_DAY:
            raise ServiceError(f"You can send up to {MAX_PER_DAY} prayer requests a day. Please try again tomorrow.")
        created = PrayerRequest.objects.create(
            user=sender, coordinator=None, extension_id=sender.extension_id,
            body=text, is_anonymous=bool(is_anonymous),
        )
        log_action(
            actor=sender, action="prayer.send", entity_type="prayer_request", entity_id=created.pk,
            after={"extension_id": created.extension_id, "is_anonymous": created.is_anonymous},
        )
        notify_many(
            _handlers_of(created), NOTIFY_REQUEST, actor=None,
            entity_type="prayer_request", entity_id=created.pk,
        )
    return SimpleNamespace(request=created, duplicate=False)


def _locked_request(actor, request_id):
    """The request with this id, locked, if `actor` may handle it; otherwise the same "not found" for both cases."""
    found = PrayerRequest.objects.select_for_update().filter(pk=request_id, archived_at__isnull=True).first()
    if found is None or not permissions.can_handle(actor, found):
        raise ServiceError("That prayer request no longer exists.")
    return found


def reply_to_request(actor, request_id, body):
    """Add a reply, mark the request replied (the final state) and tell the sender (no text)."""
    text = _clean(body)
    with transaction.atomic():
        found = _locked_request(actor, request_id)
        reply = PrayerReply.objects.create(prayer_request=found, replier=actor, body=text)
        if found.status == PrayerRequest.Status.OPEN:
            found.status = PrayerRequest.Status.REPLIED
            found.save(update_fields=["status", "updated_at"])
        log_action(
            actor=actor, action="prayer.reply", entity_type="prayer_request", entity_id=found.pk,
            after={"reply_id": reply.pk},
        )
        notify(found.user_id, NOTIFY_REPLIED, actor=actor, entity_type="prayer_request", entity_id=found.pk)
    return reply

