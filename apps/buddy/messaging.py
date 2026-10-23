"""buddy.messaging: the chat between two partners (Brick 8).

Three kinds of note live in `BuddyNote`: a text message, a nudge (👋) and an "I prayed for you" tap (🙏).

Rules (AGENTS.md and the Brick 8 plan):
- Every write needs a LIVE partnership (an open `UserBuddy` row whose two people can both sign in).
  When the partner can no longer sign in the thread stays readable but is read-only.
- The partnership row is locked (`select_for_update`) before anything is checked, so the rate limits
  and the one-time token cannot be beaten by two requests at once. No nullable FK is joined to the lock.
- PRIVACY: no audit rows for messages, nudges or prayed taps. `notification.data` carries ids and the
  kind only. The only place message text leaves the table is the PUSH preview (first ~40 characters),
  and a push is never stored.
- Messages are never deleted when a partnership ends. `thread_for` merges every period of the same
  two people, with a divider (end date and both final notes) between periods.
"""
import math
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.template.defaultfilters import date as date_filter
from django.urls import reverse
from django.utils import timezone

# VERIFY: the plan places these in core (`core/services.py` for ServiceError and
# `consume_submission_token`, the form helper `new_submission_token`). If the project keeps them
# elsewhere, fix the two import lines below; nothing else in this file touches the token machinery.
from apps.core.services import ServiceError, consume_submission_token
from apps.notifications.services import notify

from .models import BuddyNote, UserBuddy
from .permissions import can_use_buddy
from .services import active_partnership, is_live  # VERIFY: names from the Brick 6 plan

Kind = BuddyNote.Kind

# ---------------------------------------------------------------- limits and constants
MESSAGE_MAX = 1000                     # characters in one message
NUDGE_COOLDOWN = timedelta(hours=1)    # one nudge per hour
PREVIEW_CHARS = 40                     # how much of a message a push may show
PAGE_SIZE = 50                         # notes per page of the thread
POLL_LIMIT = 100                       # most notes one poll may return

# Notification type for all three kinds. VERIFY: copy the way `events` declares `event_changed`
# (and register it for preferences if events does) so people can mute it.
BUDDY_MESSAGE = "buddy_message"

READ_ONLY_MESSAGE = "Your buddy can no longer sign in, so this chat is read-only."


# ---------------------------------------------------------------- small helpers
def _name(user):
    """A person's display name. Works whether `full_name` is a property or a method."""
    name = getattr(user, "full_name", None)
    if callable(name):
        name = name()
    return (name or user.get_username() or "Your buddy").strip()


def _partner_id(row, user):
    """The other person's id in a partnership row."""
    return row.user_b_id if row.user_a_id == user.pk else row.user_a_id


def _partner(row, user):
    """The other person as a User object."""
    return get_user_model().objects.get(pk=_partner_id(row, user))


def _clean_body(body):
    """Trim, unify line breaks and drop NUL characters (PostgreSQL refuses them in text)."""
    text = (body or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _locked_partnership(actor):
    """The actor's open partnership, locked for this transaction. Call inside `transaction.atomic()`."""
    if actor is None or not can_use_buddy(actor):
        raise ServiceError("Buddy is not available for this account.")
    row = active_partnership(actor)
    if row is None:
        raise ServiceError("You do not have a buddy right now.")
    # Re-read under the lock: the partnership may have ended a moment ago.
    row = UserBuddy.objects.select_for_update().filter(pk=row.pk, ended_at__isnull=True).first()
    if row is None:
        raise ServiceError("This partnership has ended.")
    return row


def _require_live(row):
    """Writes are refused while the partner cannot sign in (the thread is read-only)."""
    if not is_live(row):
        raise ServiceError(READ_ONLY_MESSAGE)


def _preview(note):
    """The push body: a short text preview, or a fixed phrase for a nudge or prayed tap."""
    if note.kind == Kind.NUDGE:
        return "👋 nudged you"
    if note.kind == Kind.PRAYED:
        return "🙏 prayed for you"
    text = " ".join((note.body or "").split())
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS].rstrip() + "…"


def _notify_partner(row, actor, partner, note):
    """Bell item and push for a new note. Called inside the same transaction as the insert.

    `notify()` keeps one bell item per `dedupe_key` for ever, so the key has to change once the
    person has read the thread. Using the id of the OLDEST unread note from this sender does that:
    it stays the same during an unread burst (one push) and becomes a new id after `mark_read`.
    """
    oldest_unread = (
        BuddyNote.objects.filter(buddy=row, sender=actor, read_at__isnull=True)
        .order_by("pk").values_list("pk", flat=True).first()
    )
    notify(
        partner, BUDDY_MESSAGE, actor=actor,
        entity_type="user_buddy", entity_id=row.pk,
        data={"kind": note.kind, "sender_id": actor.pk},          # ids only, never the text
        dedupe_key=f"buddy_msg:{partner.pk}:{actor.pk}:{oldest_unread}",
        push={
            "title": _name(actor),
            "body": _preview(note),
            "url": reverse("buddy:messages"),
            "tag": f"buddy-{row.pk}",                              # the phone folds a burst into one
        },
    )


# ---------------------------------------------------------------- limits (also used by the page)
def _nudge_wait_seconds(row, actor):
    """Seconds until this person may nudge again (0 when they may nudge now)."""
    last = (
        BuddyNote.objects.filter(buddy=row, sender=actor, kind=Kind.NUDGE)
        .order_by("-pk").values_list("created_at", flat=True).first()
    )
    if last is None:
        return 0
    return max(0, math.ceil((last + NUDGE_COOLDOWN - timezone.now()).total_seconds()))


def _prayed_today(row, actor):
    """True when this person already sent a prayed tap today (Manila calendar day)."""
    return BuddyNote.objects.filter(
        buddy=row, sender=actor, kind=Kind.PRAYED, created_at__date=timezone.localdate(),
    ).exists()


def limits_for(user):
    """What the composer needs to know: seconds to wait for a nudge, and whether prayed is used up today."""
    row = active_partnership(user) if can_use_buddy(user) else None
    if row is None:
        return {"nudge_wait": 0, "prayed_today": False}
    return {"nudge_wait": _nudge_wait_seconds(row, user), "prayed_today": _prayed_today(row, user)}


# ---------------------------------------------------------------- writes
def send_message(actor, body, token):
    """Send a text message. Returns the new note, or None when this one-time token was already used.

    The token is consumed INSIDE the transaction, so a refused message gives the token back.
    """
    text = _clean_body(body)
    with transaction.atomic():
        row = _locked_partnership(actor)
        _require_live(row)
        if not text:
            raise ServiceError("Write a message first.")
        if len(text) > MESSAGE_MAX:
            raise ServiceError(f"Messages can be up to {MESSAGE_MAX} characters.")
        # VERIFY: signature and return value of consume_submission_token. This assumes it takes the
        # token and returns False for a replay. Adjust this ONE call if the project's helper differs.
        if not consume_submission_token(token):
            return None
        note = BuddyNote.objects.create(buddy=row, sender=actor, kind=Kind.MESSAGE, body=text)
        _notify_partner(row, actor, _partner(row, actor), note)
    return note


def send_nudge(actor):
    """Send a 👋. One per hour per person. Returns the new note."""
    with transaction.atomic():
        row = _locked_partnership(actor)
        _require_live(row)
        wait = _nudge_wait_seconds(row, actor)
        if wait:
            minutes = max(1, math.ceil(wait / 60))
            raise ServiceError(f"You can nudge again in {minutes} minute{'s' if minutes != 1 else ''}.")
        note = BuddyNote.objects.create(buddy=row, sender=actor, kind=Kind.NUDGE, body=None)
        _notify_partner(row, actor, _partner(row, actor), note)
    return note


def send_prayed(actor):
    """Send "I prayed for you" 🙏. One per day per person. Returns the new note."""
    with transaction.atomic():
        row = _locked_partnership(actor)
        _require_live(row)
        if _prayed_today(row, actor):
            raise ServiceError("You already let your buddy know you prayed today.")
        note = BuddyNote.objects.create(buddy=row, sender=actor, kind=Kind.PRAYED, body=None)
        _notify_partner(row, actor, _partner(row, actor), note)
    return note


def mark_read(actor):
    """Mark every unread note from the partner as read. Returns how many changed.

    Reading is allowed while the thread is read-only. It covers every period of the same two people,
    so "Seen" stays right on notes from an earlier partnership.
    """
    if actor is None or not can_use_buddy(actor):
        return 0
    row = active_partnership(actor)
    if row is None:
        return 0
    with transaction.atomic():
        return (
            BuddyNote.objects
            .filter(buddy__user_a_id=row.user_a_id, buddy__user_b_id=row.user_b_id, read_at__isnull=True)
            .exclude(sender=actor)
            .update(read_at=timezone.now())
        )


# ---------------------------------------------------------------- reads
def unread_count(user):
    """Unread notes from the current partner (for a dot on the Messages tab)."""
    row = active_partnership(user) if can_use_buddy(user) else None
    if row is None:
        return 0
    return BuddyNote.objects.filter(buddy=row, read_at__isnull=True).exclude(sender=user).count()


def note_item(note, viewer_id):
    """One note as a plain dict for the page and the JSON replies (times are Manila local)."""
    local = timezone.localtime(note.created_at)
    return {
        "type": "note",
        "key": f"n{note.pk}",
        "id": note.pk,
        "kind": note.kind,
        "body": note.body,
        "mine": note.sender_id == viewer_id,
        "time": date_filter(local, "g:i A"),
        "day": date_filter(local, "D j M Y"),
    }


def seen_state(user, row=None):
    """My latest note in the open partnership and whether the partner has read it (the "Seen" label)."""
    row = row or (active_partnership(user) if can_use_buddy(user) else None)
    if row is None:
        return {"last_mine_id": None, "last_mine_seen": False}
    last = BuddyNote.objects.filter(buddy=row, sender=user).order_by("-pk").first()
    if last is None:
        return {"last_mine_id": None, "last_mine_seen": False}
    return {"last_mine_id": last.pk, "last_mine_seen": last.read_at is not None}


def _divider(period, viewer_id, names):
    """A divider for an ended period: the end date and the final notes, shown only to the two partners.

    `final_note` was written by whoever ended it (`ended_by`); `partner_final_note` by the other person.
    """
    notes = []
    other_id = period.user_b_id if period.ended_by_id == period.user_a_id else period.user_a_id
    for author_id, text in ((period.ended_by_id, period.final_note), (other_id, period.partner_final_note)):
        if text:
            mine = author_id == viewer_id
            notes.append({"mine": mine, "name": "You" if mine else names.get(author_id, "Your buddy"), "text": text})
    return {
        "type": "divider",
        "key": f"p{period.pk}",
        "ended": date_filter(timezone.localtime(period.ended_at), "j M Y"),
        "notes": notes,
    }


def thread_for(user, before=None, limit=PAGE_SIZE):
    """The conversation with the current partner, newest `limit` notes (older ones via `before=<note id>`).

    Reads the notes of EVERY partnership row for the same sorted pair, so earlier periods show again
    when the same two people pair again. A person with no open partnership gets an empty, inactive
    thread. An inactive partner (cannot sign in) still shows the thread, flagged `read_only`.
    `before` must be a note of this same pair; anything else gives an empty page.
    """
    empty = {
        "active": False, "read_only": True, "partner_name": "", "items": [], "has_more": False,
        "oldest_id": None, "newest_id": None, "last_mine_id": None, "last_mine_seen": False,
    }
    if user is None or not can_use_buddy(user):
        return empty
    row = active_partnership(user)
    if row is None:
        return empty

    pair = Q(buddy__user_a_id=row.user_a_id, buddy__user_b_id=row.user_b_id)
    notes_qs = BuddyNote.objects.filter(pair)
    upper_period = None            # period of the oldest note already on screen (when paging back)
    if before is not None:
        upper_period = BuddyNote.objects.filter(pair, pk=before).values_list("buddy_id", flat=True).first()
        if upper_period is None:
            return {**empty, "active": True}
        notes_qs = notes_qs.filter(pk__lt=before)

    fetched = list(notes_qs.order_by("-pk")[: limit + 1])
    has_more = len(fetched) > limit
    notes = list(reversed(fetched[:limit]))

    people = get_user_model().objects.in_bulk([row.user_a_id, row.user_b_id])
    names = {pk: _name(person) for pk, person in people.items()}
    ended = list(
        UserBuddy.objects.filter(user_a_id=row.user_a_id, user_b_id=row.user_b_id, ended_at__isnull=False)
        .order_by("pk")
    )

    items, previous_period = [], None
    for note in notes:
        if previous_period is not None and note.buddy_id != previous_period:
            items += [_divider(p, user.pk, names) for p in ended if previous_period <= p.pk < note.buddy_id]
        items.append(note_item(note, user.pk))
        previous_period = note.buddy_id
    # Dividers after the last note shown. On the newest page that is every ended period from there on;
    # when paging back it stops at the period of the note that was already on screen.
    # (A period with no notes at all, before the very first note, is not shown: nothing to separate.)
    upper = upper_period if upper_period is not None else float("inf")
    start = previous_period if previous_period is not None else 0
    if before is None or notes:
        items += [_divider(p, user.pk, names) for p in ended if start <= p.pk < upper]

    return {
        "active": True,
        "read_only": not is_live(row),
        "partner_name": names.get(_partner_id(row, user), "Your buddy"),
        "items": items,
        "has_more": has_more,
        "oldest_id": notes[0].pk if notes else None,
        "newest_id": notes[-1].pk if notes and before is None else None,
        **seen_state(user, row),
    }


def poll_since(user, after_id):
    """Notes newer than `after_id` in the OPEN partnership, plus the "Seen" state. Only the two partners get any.

    `active` False means the partnership ended (or the person is not paired): the page reloads.
    """
    if user is None or not can_use_buddy(user):
        return {"active": False, "read_only": True, "notes": [], "last_mine_id": None, "last_mine_seen": False}
    row = active_partnership(user)
    if row is None:
        return {"active": False, "read_only": True, "notes": [], "last_mine_id": None, "last_mine_seen": False}
    notes = BuddyNote.objects.filter(buddy=row, pk__gt=after_id).order_by("pk")[:POLL_LIMIT]
    return {
        "active": True,
        "read_only": not is_live(row),
        "notes": [note_item(n, user.pk) for n in notes],
        **seen_state(user, row),
    }
