"""events.permissions: ONE place that decides who may see and do what with an event.

Views start from `visible_events(actor)` (and answer 404 for anything outside it), and every
service calls `require(...)` again: hiding a button is not access control.

Who may do what (A = admin or super-admin, C = coordinator, M = member):
  see a global event             everyone who can sign in
  see an extension event         A any | C and M of THAT extension only
  create / edit / cancel global  A only
  create / edit / cancel ext.    A any | C own extension only | M never

Events belong to the EXTENSION, never to a coordinator account, so these rules compare
`actor.extension_id` with `event.extension_id` and the history survives a reassignment.

These functions answer ROLE and SCOPE only, plus the state rule that a cancelled event
cannot be edited again (`can_edit`, `can_cancel`). Timing rules (a started event cannot be
moved, an ended event cannot be cancelled) live in `services.py` as friendly ServiceErrors.
"""
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.accounts.models import Extension, Role

from .models import Event

VIEW = "view"
EDIT = "edit"
CANCEL = "cancel"

_ADMINS = (Role.SUPER_ADMIN, Role.ADMIN)


def _signed_in(actor):
    """True for a real person who can sign in (not anonymous, not archived, not suspended)."""
    return actor is not None and getattr(actor, "is_authenticated", False) and actor.is_active


def _own_extension(actor, extension_id):
    """True when `actor` belongs to the extension with this id."""
    return extension_id is not None and actor.extension_id == extension_id


# ----------------------------------------------------------------- seeing
def visible_events(actor):
    """The events `actor` may see, as a queryset (the ONE place that narrows events by who asks).

    Global events for everyone; extension events only for that extension's people; admins see
    all. Archived events are left out. Anyone else gets an empty queryset, so a view that does
    `get_object_or_404(visible_events(request.user), pk=...)` answers 404.
    """
    if not _signed_in(actor):
        return Event.objects.none()
    live = Event.objects.filter(archived_at__isnull=True)
    if actor.role in _ADMINS:
        return live
    scope = Q(extension__isnull=True)
    if actor.extension_id is not None:
        scope |= Q(extension_id=actor.extension_id)
    return live.filter(scope)


def can_view(actor, event):
    """May `actor` see this event? Same rule as `visible_events`."""
    if not _signed_in(actor) or event.archived_at is not None:
        return False
    if event.extension_id is None or actor.role in _ADMINS:
        return True
    return _own_extension(actor, event.extension_id)


# ----------------------------------------------------------------- managing
def can_create(actor, extension_id):
    """May `actor` create an event for this extension (None = a global event)?"""
    if not _signed_in(actor):
        return False
    if actor.role in _ADMINS:
        return True
    if actor.role == Role.COORDINATOR:
        return _own_extension(actor, extension_id)
    return False


def can_manage(actor, event):
    """Role and scope only: may `actor` change or cancel this event (whatever its state)?"""
    if not can_view(actor, event):
        return False
    if actor.role in _ADMINS:
        return True
    if actor.role == Role.COORDINATOR:
        return _own_extension(actor, event.extension_id)
    return False


def can_edit(actor, event):
    """May `actor` edit this event right now? Managers only, and never a cancelled event."""
    return not event.is_cancelled and can_manage(actor, event)


def can_cancel(actor, event):
    """May `actor` cancel this event right now? Managers only, and only once."""
    return not event.is_cancelled and can_manage(actor, event)


def available_actions(actor, event):
    """Actions to OFFER on an event (chips, detail page, list rows). The services check again."""
    allowed = set()
    if can_view(actor, event):
        allowed.add(VIEW)
    if can_edit(actor, event):
        allowed.add(EDIT)
    if can_cancel(actor, event):
        allowed.add(CANCEL)
    return allowed


# ----------------------------------------------------------------- page-level
def can_create_global(actor):
    """May `actor` create events that apply to every extension?"""
    return can_create(actor, None)


def can_manage_events(actor):
    """May `actor` see New event at all (admin, super-admin, or a coordinator with an extension)?"""
    if not _signed_in(actor):
        return False
    if actor.role in _ADMINS:
        return True
    return actor.role == Role.COORDINATOR and actor.extension_id is not None


def creatable_extensions(actor):
    """Extensions `actor` may create an event for, as a queryset (the form's choices).

    The QUERYSET form of `can_create`. Admin and super-admin: every extension that is not
    archived (plus "global", which the form offers when `can_create_global`). Coordinator: their
    own. Anyone else: none.
    """
    if not _signed_in(actor):
        return Extension.objects.none()
    live = Extension.objects.filter(archived_at__isnull=True)
    if actor.role in _ADMINS:
        return live
    if actor.role == Role.COORDINATOR and actor.extension_id is not None:
        return live.filter(pk=actor.extension_id)
    return Extension.objects.none()


def require(check, actor, obj=None):
    """Raise PermissionDenied unless `check(actor, obj)` is true; services call this first.

    `check` is one of the functions above, e.g. `require(can_manage, actor, event)`. For
    `can_create` pass the extension id (or None) as the third argument.
    """
    if not check(actor, obj):
        raise PermissionDenied("You cannot do this with this event.")
