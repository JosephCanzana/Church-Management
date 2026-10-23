"""buddy.services: business logic for finding a buddy, requesting, accepting and the partner card.

Rules (see AGENTS.md and PLAN-faith-buddy-prayer.md, Brick 6):
  * Every change runs in `transaction.atomic()`, locks the people involved in ascending id
    order (never `select_related` together with `select_for_update`), then re-checks the state
    under the lock. Repeating an action is quiet where that makes sense.
  * Audit rows carry ids and counts only. Notification `data` carries ids only; the bell shows
    the sender through `actor`. Nothing a person typed ever lands in either table.
  * `buddy` may import read-only functions from `faith`; `faith` never imports `buddy`.
"""
from collections.abc import Mapping

from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.services import log_action
from apps.core.services import ServiceError
from apps.core.text import clean_text
from apps.notifications.services import notify

from .models import BuddyRequest, UserBuddy
from .permissions import can_use_buddy, signable_q

#: Notification types created here (declared in the owning app, like events' `event_changed`).
BUDDY_REQUEST = "buddy_request"
BUDDY_ACCEPTED = "buddy_accepted"
NOTIFICATION_TYPES = (BUDDY_REQUEST, BUDDY_ACCEPTED)

MAX_PENDING_REQUESTS = 3     # outgoing pending requests per person
SEARCH_MIN_LENGTH = 3        # characters before a search crosses extensions
PAGE_SIZE = 20               # people per page of the candidate list
MAX_PAGE = 10_000            # stops absurd offsets

NOT_FOUND = "That request was not found."


class CandidateState(models.TextChoices):
    """What the Request button should do for one person in the candidate list."""

    AVAILABLE = "available", "Available"
    PENDING_SENT = "pending_sent", "Request sent"
    PENDING_RECEIVED = "pending_received", "Asked you"
    HAS_BUDDY = "has_buddy", "Already has a buddy"


# ------------------------------------------------------------------ helpers
def _lock_users(user_ids):
    """Lock these people in ascending id order and return `{id: User}` (missing ids are absent).

    The rows come back freshly read, so permission checks on them are checks on the locked row.
    No `select_related`: `extension` is a nullable foreign key.
    """
    ordered = sorted(set(user_ids))
    rows = User.objects.select_for_update().filter(pk__in=ordered).order_by("pk")
    return {user.pk: user for user in rows}


def partner_of(row, user):
    """The other person in a partnership row. Raises ValueError when `user` is not in it."""
    if row.user_a_id == user.pk:
        return row.user_b
    if row.user_b_id == user.pk:
        return row.user_a
    raise ValueError("That person is not in this partnership.")


def is_live(row):
    """True when both people in the row can still sign in. A row that is not live is 'inactive'."""
    return row.user_a.can_sign_in and row.user_b.can_sign_in


def _active_rows(user, *, lock=False):
    """Un-ended partnership rows that include `user`, newest first."""
    rows = UserBuddy.objects.filter(ended_at__isnull=True).filter(Q(user_a=user) | Q(user_b=user))
    if lock:
        rows = rows.select_for_update()
    return rows.order_by("-pk")


def active_partnership(user):
    """The person's newest un-ended partnership row (live or inactive), or None."""
    return _active_rows(user).first()


def live_partnership(user):
    """The person's un-ended partnership whose two people can both sign in, or None."""
    for row in _active_rows(user):
        if is_live(row):
            return row
    return None


def end_dead_partnerships(user, *, actor=None):
    """End every un-ended partnership of `user` that is not live. Returns how many were ended.

    `ended_by` stays NULL (nobody chose this) and no final note is stored. Call it inside an
    atomic block that already holds the lock on `user`.
    """
    ended = 0
    with transaction.atomic():
        for row in _active_rows(user, lock=True):
            if is_live(row):
                continue
            row.ended_at = timezone.now()
            row.save(update_fields=["ended_at"])
            log_action(
                "buddy.end", actor=actor or user, entity_type="user_buddy", entity_id=row.pk,
                after={"reason": "partner_cannot_sign_in"},
            )
            ended += 1
    return ended


# ----------------------------------------------------------------- requests
def send_request(actor, to_user_id):
    """Ask another person to be your buddy. Returns the new pending `BuddyRequest`.

    Refused when it is yourself, when either person cannot sign in or already has a LIVE buddy,
    when you already have 3 pending requests, or when a request between the two of you is
    already pending (in either direction). An inactive partnership of yours is ended first.
    """
    if actor.pk == to_user_id:
        raise ServiceError("You cannot send a buddy request to yourself.")
    with transaction.atomic():
        locked = _lock_users([actor.pk, to_user_id])
        me, target = locked.get(actor.pk), locked.get(to_user_id)
        if me is None or not can_use_buddy(me):
            raise ServiceError("You cannot use Buddy right now.")
        if target is None or not can_use_buddy(target):
            raise ServiceError("That person is not available.")

        end_dead_partnerships(me)
        if live_partnership(me):
            raise ServiceError("You already have a buddy.")
        if live_partnership(target):
            raise ServiceError("That person already has a buddy.")

        pending = BuddyRequest.objects.filter(status=BuddyRequest.Status.PENDING)
        between = pending.filter(
            Q(from_user=me, to_user=target) | Q(from_user=target, to_user=me)
        ).first()
        if between is not None:
            if between.from_user_id == me.pk:
                raise ServiceError("You already sent that person a request.")
            raise ServiceError("That person already asked you. Accept it under Requests for you.")
        if pending.filter(from_user=me).count() >= MAX_PENDING_REQUESTS:
            raise ServiceError(f"You can have up to {MAX_PENDING_REQUESTS} pending requests. Cancel one first.")

        try:
            with transaction.atomic():
                request = BuddyRequest.objects.create(from_user=me, to_user=target)
        except IntegrityError:
            # The unique constraint is the last safety net for a duplicate.
            raise ServiceError("There is already a pending request between you two.")

        log_action(
            "buddy.request", actor=me, entity_type="buddy_request", entity_id=request.pk,
            after={"to_user_id": target.pk},
        )
        notify(
            target, BUDDY_REQUEST, actor=me, entity_type="buddy_request", entity_id=request.pk,
            data={"request_id": request.pk}, dedupe_key=f"buddy_request:{request.pk}",
        )
    return request


def accept_request(actor, request_id):
    """Accept a request sent to you. Returns the new (or already existing) `UserBuddy` row.

    Locks both people, re-checks that neither has a LIVE buddy, creates the partnership with
    the two ids sorted, and cancels every other pending request that involves either person.
    Accepting twice is quiet: the second call returns the same partnership.
    """
    with transaction.atomic():
        found = BuddyRequest.objects.filter(pk=request_id).first()
        if found is None or found.to_user_id != actor.pk:
            raise ServiceError(NOT_FOUND)
        locked = _lock_users([found.from_user_id, found.to_user_id])
        # People are locked first, then the request (same order everywhere).
        request = BuddyRequest.objects.select_for_update().filter(pk=request_id).first()
        me, sender = locked.get(found.to_user_id), locked.get(found.from_user_id)
        first_id, second_id = sorted([found.from_user_id, found.to_user_id])

        if request is None or request.to_user_id != actor.pk:
            raise ServiceError(NOT_FOUND)
        if request.status == BuddyRequest.Status.ACCEPTED:
            existing = UserBuddy.objects.filter(user_a_id=first_id, user_b_id=second_id, ended_at__isnull=True).first()
            if existing is not None:
                return existing
            raise ServiceError("This request was already accepted.")
        if request.status != BuddyRequest.Status.PENDING:
            raise ServiceError("This request is no longer open.")
        if me is None or not can_use_buddy(me):
            raise ServiceError("You cannot use Buddy right now.")
        if sender is None or not can_use_buddy(sender):
            raise ServiceError("That person can no longer take part in Buddy.")

        # An inactive partnership never blocks a new one: it is ended here.
        end_dead_partnerships(me)
        end_dead_partnerships(sender, actor=me)
        if live_partnership(me):
            raise ServiceError("You already have a buddy.")
        if live_partnership(sender):
            raise ServiceError("That person already has a buddy.")

        now = timezone.now()
        row = UserBuddy.objects.create(user_a_id=first_id, user_b_id=second_id)
        request.status = BuddyRequest.Status.ACCEPTED
        request.responded_at = now
        request.save(update_fields=["status", "responded_at"])
        cancelled = (
            BuddyRequest.objects.filter(status=BuddyRequest.Status.PENDING)
            .filter(Q(from_user_id__in=[first_id, second_id]) | Q(to_user_id__in=[first_id, second_id]))
            .update(status=BuddyRequest.Status.CANCELLED, responded_at=now)
        )

        log_action(
            "buddy.accept", actor=me, entity_type="user_buddy", entity_id=row.pk,
            after={"request_id": request.pk, "user_a_id": first_id, "user_b_id": second_id,
                   "cancelled_requests": cancelled},
        )
        notify(
            sender, BUDDY_ACCEPTED, actor=me, entity_type="user_buddy", entity_id=row.pk,
            data={"buddy_id": row.pk}, dedupe_key=f"buddy_accepted:{row.pk}",
        )
    return row


def _close_request(actor, request_id, *, side, new_status, action):
    """Shared body of decline (recipient) and cancel (sender). Repeating it is quiet."""
    with transaction.atomic():
        me = _lock_users([actor.pk]).get(actor.pk)
        if me is None or not can_use_buddy(me):
            raise ServiceError("You cannot use Buddy right now.")
        request = BuddyRequest.objects.select_for_update().filter(pk=request_id).first()
        if request is None or getattr(request, f"{side}_id") != me.pk:
            raise ServiceError(NOT_FOUND)
        if request.status == new_status:
            return request
        if request.status != BuddyRequest.Status.PENDING:
            raise ServiceError("This request is no longer open.")
        request.status = new_status
        request.responded_at = timezone.now()
        request.save(update_fields=["status", "responded_at"])
        log_action(action, actor=me, entity_type="buddy_request", entity_id=request.pk)
    return request


def decline_request(actor, request_id):
    """The recipient turns a request down. There is no cooldown: the sender may ask again."""
    return _close_request(
        actor, request_id, side="to_user", new_status=BuddyRequest.Status.DECLINED, action="buddy.decline",
    )


def cancel_request(actor, request_id):
    """The sender takes back a request that is still pending."""
    return _close_request(
        actor, request_id, side="from_user", new_status=BuddyRequest.Status.CANCELLED, action="buddy.cancel",
    )


def incoming_requests(user):
    """Pending requests sent to `user` by people who can still sign in, newest first."""
    return list(
        BuddyRequest.objects.filter(to_user=user, status=BuddyRequest.Status.PENDING)
        .filter(signable_q("from_user__"))
        .select_related("from_user", "from_user__extension")
        .order_by("-created_at", "-pk")
    )


def outgoing_requests(user):
    """Pending requests `user` sent, newest first. All of them show, so each can be cancelled."""
    return list(
        BuddyRequest.objects.filter(from_user=user, status=BuddyRequest.Status.PENDING)
        .select_related("to_user", "to_user__extension")
        .order_by("-created_at", "-pk")
    )


# --------------------------------------------------------------- candidates
def _page_number(value):
    """A page number from the query string: a whole number from 1 up, anything else is 1."""
    try:
        return max(1, min(int(value), MAX_PAGE))
    except (TypeError, ValueError):
        return 1


def candidates(actor, query="", page=1):
    """People `actor` could ask to be a buddy, one page of at most 20.

    DELIBERATE EXCEPTION to `User.objects.for_user()`: that stops at a coordinator's own
    extension, but a buddy may come from any extension, so this looks at every person who can
    sign in. To keep that safe it needs `can_use_buddy`, never returns the actor, caps a page at
    20 people, and gives back only id, display name, extension name and a state. No contact
    details, role or account id leave this function.

    Empty search (or fewer than 3 characters): the actor's own extension; admins and
    super-admins have no extension, so they see everyone. 3 or more characters: every
    extension, every word typed must match the first, middle or last name.

    Returns `{"rows", "page", "has_next", "has_previous", "query", "scope", "too_short"}`
    where `scope` is "search", "everyone" or "extension".
    """
    if not can_use_buddy(actor):
        raise ServiceError("You cannot use Buddy right now.")
    words = (query or "").split()
    text = " ".join(words)[:100]
    words = text.split()
    searching = len(text) >= SEARCH_MIN_LENGTH

    people = User.objects.filter(signable_q()).exclude(pk=actor.pk).select_related("extension")
    if searching:
        scope = "search"
        for word in words:
            word = clean_text(word)
            people = people.filter(
                Q(first_name__icontains=word) | Q(middle_name__icontains=word) | Q(last_name__icontains=word)
            )
    elif actor.role in (Role.ADMIN, Role.SUPER_ADMIN):
        scope = "everyone"
    else:
        scope = "extension"
        people = people.filter(extension_id=actor.extension_id) if actor.extension_id else people.none()

    page = _page_number(page)
    offset = (page - 1) * PAGE_SIZE
    chunk = list(people.order_by("last_name", "first_name", "pk")[offset:offset + PAGE_SIZE + 1])
    has_next = len(chunk) > PAGE_SIZE
    chunk = chunk[:PAGE_SIZE]

    states = _states_for(actor, [person.pk for person in chunk])
    rows = [
        {
            "id": person.pk,
            "name": person.full_name,
            "extension": person.extension.name if person.extension_id else "",
            "state": states.get(person.pk, CandidateState.AVAILABLE),
        }
        for person in chunk
    ]
    return {
        "rows": rows, "page": page, "has_next": has_next, "has_previous": page > 1,
        "query": text, "scope": scope, "too_short": bool(text) and not searching,
    }


def _states_for(actor, person_ids):
    """`{person id: CandidateState}` for the people on one page, in three queries."""
    if not person_ids:
        return {}
    busy = set()
    rows = (
        UserBuddy.objects.filter(ended_at__isnull=True)
        .filter(Q(user_a_id__in=person_ids) | Q(user_b_id__in=person_ids))
        .select_related("user_a", "user_b")
    )
    for row in rows:
        if is_live(row):                      # an inactive partnership does not make anyone busy
            busy.update((row.user_a_id, row.user_b_id))
    pending = BuddyRequest.objects.filter(status=BuddyRequest.Status.PENDING)
    sent = set(pending.filter(from_user=actor, to_user_id__in=person_ids).values_list("to_user_id", flat=True))
    received = set(pending.filter(to_user=actor, from_user_id__in=person_ids).values_list("from_user_id", flat=True))

    states = {}
    for pid in person_ids:
        if pid in busy:
            states[pid] = CandidateState.HAS_BUDDY
        elif pid in sent:
            states[pid] = CandidateState.PENDING_SENT
        elif pid in received:
            states[pid] = CandidateState.PENDING_RECEIVED
        else:
            states[pid] = CandidateState.AVAILABLE
    return states


# ------------------------------------------------------------- partner card
# Where each number may live in whatever `faith.streaks.streak_snapshot` returns. The snapshot's
# exact shape was not visible when this brick was written, so the lookup accepts the model field
# names, short names and a nested form. A test reads a real snapshot, so a mismatch shows up there.
_STREAK_PATHS = {
    "open_current": (("current_open_streak",), ("open_current",), ("current_open",), ("open", "current")),
    "open_best": (("longest_open_streak",), ("open_longest",), ("longest_open",), ("open_best",), ("open", "longest"), ("open", "best")),
    "read_current": (("current_read_streak",), ("read_current",), ("current_read",), ("read", "current")),
    "read_best": (("longest_read_streak",), ("read_longest",), ("longest_read",), ("read_best",), ("read", "longest"), ("read", "best")),
}


def _lookup(source, path):
    """Follow `path` through mappings or attributes; None when any step is missing."""
    for key in path:
        source = source.get(key) if isinstance(source, Mapping) else getattr(source, key, None)
        if source is None:
            return None
    return source


def _partner_streaks(partner):
    """The partner's four streak numbers, or None when the faith streak engine is not there."""
    try:
        from apps.faith.streaks import streak_snapshot
    except ImportError:          # Brick 1 not installed yet: the card simply shows dashes
        return None
    snapshot = streak_snapshot(partner, timezone.localdate())
    result = {}
    for name, paths in _STREAK_PATHS.items():
        value = next((v for v in (_lookup(snapshot, p) for p in paths) if v is not None), 0)
        result[name] = value
    return result


def partner_card(user):
    """What the partner card shows for `user`, or None when they have no LIVE partner.

    Read-only: name, extension, since when, and the partner's open and Bible streaks. Goals,
    notes and messages are never part of it.
    """
    row = live_partnership(user)
    if row is None:
        return None
    partner = partner_of(row, user)
    return {
        "partnership_id": row.pk,
        "partner_id": partner.pk,
        "name": partner.full_name,
        "extension": partner.extension.name if partner.extension_id else "",
        "since": row.started_at,
        "streaks": _partner_streaks(partner),
    }
