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
from datetime import timedelta

from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.core.services import ServiceError
from apps.core.text import clean_text
from apps.notifications.services import notify
from apps.faith.partner import partner_goal_overview

from .models import BuddyRequest, UserBuddy
from .permissions import can_use_buddy, signable_q

#: Notification types created here (declared in the owning app, like events' `event_changed`).
BUDDY_REQUEST = "buddy_request"
BUDDY_ACCEPTED = "buddy_accepted"
BUDDY_END_REQUESTED = "buddy_end_requested"   # Brick 7: your buddy asked to end the partnership
BUDDY_ENDED = "buddy_ended"                   # Brick 7: your buddy agreed, the partnership ended
NOTIFICATION_TYPES = (BUDDY_REQUEST, BUDDY_ACCEPTED, BUDDY_END_REQUESTED, BUDDY_ENDED)

MAX_PENDING_REQUESTS = 3     # outgoing pending requests per person
SEARCH_MIN_LENGTH = 3        # characters before a search crosses extensions
PAGE_SIZE = 20               # people per page of the candidate list
MAX_PAGE = 10_000            # stops absurd offsets
MAX_END_NOTE = 500           # characters in a final note
END_REQUEST_DAYS_KEY = "buddy_end_request_days"   # site_setting key
DEFAULT_END_REQUEST_DAYS = 7                      # used when the setting is missing or invalid

NOT_FOUND = "That request was not found."

PARTNER_GOALS_LIVE = "live"            # a live partner: the goals are shown
PARTNER_GOALS_INACTIVE = "inactive"    # partner cannot sign in: goals hidden
PARTNER_GOALS_NONE = "no_partner"      # no un-ended partnership at all


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


# ------------------------------------------------------------ ending (Brick 7)
def _clean_end_note(note):
    """A final note trimmed; None when empty. Over 500 characters is refused, never cut."""
    text = (note or "").strip()
    if len(text) > MAX_END_NOTE:
        raise ServiceError(f"A final note can have up to {MAX_END_NOTE} characters.")
    return text or None


def _find_row(user, *, live=None):
    """The person's newest un-ended row that is live (True), inactive (False) or either (None)."""
    for row in _active_rows(user):
        if live is None or is_live(row) is live:
            return row
    return None


def _lock_row(actor, *, live=None, missing="You do not have a buddy right now."):
    """Lock both people (ascending id), then the actor's un-ended row. Returns (row, me, partner).

    Everything is re-checked on the locked rows: the actor may still use Buddy, the row is still
    un-ended, and it is still live / inactive as the caller expects (`live=None` accepts both).
    Call it inside `transaction.atomic()`.
    """
    found = _find_row(actor, live=live)
    if found is None:
        raise ServiceError(missing)
    locked = _lock_users([found.user_a_id, found.user_b_id])
    me = locked.get(actor.pk)
    partner = locked.get(found.user_b_id if found.user_a_id == actor.pk else found.user_a_id)
    if me is None or not can_use_buddy(me):
        raise ServiceError("You cannot use Buddy right now.")
    row = UserBuddy.objects.select_for_update().filter(pk=found.pk, ended_at__isnull=True).first()
    if row is None:
        raise ServiceError("This partnership has already ended.")
    if partner is None:
        raise ServiceError(missing)
    if live is not None and partner.can_sign_in is not live:
        raise ServiceError("Your buddy's account just changed. Reload the page and try again.")
    return row, me, partner


def request_end(actor, note=None):
    """Ask your buddy to end the partnership, with an optional final note (up to 500 characters).

    Live partnership only. A second request changes nothing (the first one, by either person,
    stays), so a double tap is quiet. The partner gets a `buddy_end_requested` bell item.
    """
    text = _clean_end_note(note)
    with transaction.atomic():
        row, me, partner = _lock_row(actor, live=True)
        if row.end_requested_at is not None:
            return row
        now = timezone.now()
        row.end_requested_by = me
        row.end_requested_at = now
        row.end_request_note = text
        row.save(update_fields=["end_requested_by", "end_requested_at", "end_request_note"])
        log_action(
            "buddy.end_request", actor=me, entity_type="user_buddy", entity_id=row.pk,
            after={"requested_by_id": me.pk},
        )
        # The time is in the key because the dedupe key is permanent: a request that was cancelled
        # and made again must still reach the partner.
        notify(
            partner, BUDDY_END_REQUESTED, actor=me, entity_type="user_buddy", entity_id=row.pk,
            data={"buddy_id": row.pk}, dedupe_key=f"buddy_end_requested:{row.pk}:{now:%Y%m%d%H%M%S%f}",
        )
    return row


def cancel_end_request(actor):
    """Take back your own end request. Nothing pending: quiet. Someone else's request: refused."""
    with transaction.atomic():
        row, me, _partner = _lock_row(actor)
        if row.end_requested_at is None:
            return row
        if row.end_requested_by_id != me.pk:
            raise ServiceError("Only the person who asked can cancel the request.")
        row.end_requested_by = None
        row.end_requested_at = None
        row.end_request_note = None
        row.save(update_fields=["end_requested_by", "end_requested_at", "end_request_note"])
        log_action("buddy.end_cancel", actor=me, entity_type="user_buddy", entity_id=row.pk)
    return row


def agree_end(actor, note=None):
    """The OTHER person agrees to end it, with their own optional note. Ends the partnership.

    Stores `ended_by` = the requester, `final_note` = the requester's note and
    `partner_final_note` = this person's note. The requester gets a `buddy_ended` bell item.
    """
    text = _clean_end_note(note)
    with transaction.atomic():
        row, me, partner = _lock_row(actor, live=True)
        if row.end_requested_at is None:
            raise ServiceError("Your buddy has not asked to end the partnership.")
        if row.end_requested_by_id == me.pk:
            raise ServiceError("You asked to end this partnership. Your buddy has to agree.")
        row.ended_at = timezone.now()
        row.ended_by = partner                      # only two people: the requester is the partner
        row.final_note = row.end_request_note
        row.partner_final_note = text
        row.save(update_fields=["ended_at", "ended_by", "final_note", "partner_final_note"])
        log_action(
            "buddy.end", actor=me, entity_type="user_buddy", entity_id=row.pk,
            after={"mode": "agreed", "ended_by_id": partner.pk},
        )
        notify(
            partner, BUDDY_ENDED, actor=me, entity_type="user_buddy", entity_id=row.pk,
            data={"buddy_id": row.pk}, dedupe_key=f"buddy_ended:{row.pk}",
        )
    return row


def end_without_approval(actor, note=None):
    """End a partnership whose other person can no longer sign in. No agreement is needed.

    Refused while the partner can still sign in. Stores `ended_by` = actor and `final_note`.
    Nobody is notified: the partner cannot sign in to read it.
    """
    text = _clean_end_note(note)
    with transaction.atomic():
        if _find_row(actor, live=False) is None and live_partnership(actor) is not None:
            raise ServiceError("Your buddy can still sign in, so ending needs their agreement.")
        row, me, _partner = _lock_row(actor, live=False, missing="You have no partnership to end.")
        row.ended_at = timezone.now()
        row.ended_by = me
        row.final_note = text
        row.save(update_fields=["ended_at", "ended_by", "final_note"])
        log_action(
            "buddy.end", actor=me, entity_type="user_buddy", entity_id=row.pk,
            after={"mode": "inactive_partner", "ended_by_id": me.pk},
        )
    return row


def partnership_state(user):
    """What the End controls on the Buddy tab need, or None when `user` has no un-ended partnership.

    `mode` is "live" (nothing pending), "waiting" (you asked), "asked" (your buddy asked, so
    `request_note` is theirs for you to read) or "inactive" (your buddy can no longer sign in).
    """
    row = live_partnership(user) or active_partnership(user)
    if row is None:
        return None
    partner = partner_of(row, user)
    if not is_live(row):
        mode = "inactive"
    elif row.end_requested_at is None:
        mode = "live"
    elif row.end_requested_by_id == user.pk:
        mode = "waiting"
    else:
        mode = "asked"
    return {
        "mode": mode,
        "partner_name": partner.full_name,
        "requested_at": row.end_requested_at,
        "request_note": row.end_request_note if mode == "asked" else None,
        "auto_close_days": end_request_days(),
    }


def end_request_days():
    """Days an end request may wait before it closes by itself (site setting, default 7).

    A missing, non-numeric or out-of-range (1 to 365) value falls back to the default.
    """
    # (verify) If `core` already has a `site_setting(key, default)` helper, call it here instead.
    from apps.core.models import SiteSetting

    row = SiteSetting.objects.filter(key=END_REQUEST_DAYS_KEY).first()
    raw = getattr(row, "value", None)
    if isinstance(raw, dict):
        raw = raw.get("value")
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_END_REQUEST_DAYS
    return days if 1 <= days <= 365 else DEFAULT_END_REQUEST_DAYS


def stale_end_request_ids(now=None, days=None):
    """Ids of un-ended partnerships whose end request is older than `days` (the job and its dry run)."""
    now = now or timezone.now()
    days = end_request_days() if days is None else days
    return list(
        UserBuddy.objects.filter(
            ended_at__isnull=True, end_requested_at__isnull=False,
            end_requested_at__lt=now - timedelta(days=days),
        ).order_by("pk").values_list("pk", flat=True)
    )


def close_stale_end_requests(now=None, days=None):
    """End every partnership whose end request went unanswered for too long. Returns the count.

    The requester's wish is honoured: `ended_by` = the requester, `final_note` = their note, no
    `partner_final_note`. Each row is re-checked under its lock, so the job is safe to run twice
    and safe next to a person who agrees at the same moment. Logged as a system job.
    """
    now = now or timezone.now()
    days = end_request_days() if days is None else days
    closed = 0
    for pk in stale_end_request_ids(now, days):
        with transaction.atomic():
            found = UserBuddy.objects.filter(pk=pk).first()
            if found is None:
                continue
            _lock_users([found.user_a_id, found.user_b_id])
            row = UserBuddy.objects.select_for_update().filter(
                pk=pk, ended_at__isnull=True, end_requested_at__isnull=False,
                end_requested_at__lt=now - timedelta(days=days),
            ).first()
            if row is None:
                continue
            row.ended_at = now
            row.ended_by_id = row.end_requested_by_id
            row.final_note = row.end_request_note
            row.save(update_fields=["ended_at", "ended_by", "final_note"])
            log_action(
                "buddy.end", entity_type="user_buddy", entity_id=row.pk,
                after={"mode": "end_request_expired", "ended_by_id": row.end_requested_by_id},
                source=AuditLog.Source.SYSTEM_JOB,
            )
            closed += 1
    return closed

def partner_goals_for(viewer):
    """What the "Their goals" screen shows `viewer`: their LIVE partner's goals, nothing else.

    Takes no id from the request. The partner is read from the viewer's own partnership row
    (`partner_of`), so a person can only ever see the one buddy they are paired with. Returns
    `{"state", "partner_name", "overview"}`: `state` is "live" (overview filled), "inactive"
    (partner can no longer sign in, goals hidden) or "no_partner". Read-only: nothing is
    audited and the partner is not notified.
    """
    if not can_use_buddy(viewer):
        raise ServiceError("You cannot use Buddy right now.")
    row = live_partnership(viewer)
    if row is None:
        stale = active_partnership(viewer)
        if stale is None:
            return {"state": PARTNER_GOALS_NONE, "partner_name": "", "overview": None}
        return {
            "state": PARTNER_GOALS_INACTIVE,
            "partner_name": partner_of(stale, viewer).full_name,
            "overview": None,
        }
    partner = partner_of(row, viewer)
    return {
        "state": PARTNER_GOALS_LIVE,
        "partner_name": partner.full_name,
        "overview": partner_goal_overview(partner, timezone.localdate()),
    }

