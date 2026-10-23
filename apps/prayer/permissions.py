"""prayer.permissions: ONE place that decides who may send, see and handle prayer requests.

Views start from `PrayerRequest.objects.mine(user)` or `.inbox_for(actor)`; every service calls the `can_*`
checks again on the locked row (hiding a button is not access control).

  send a request       role member, member account, can sign in, has an extension
  see the inbox        coordinator (own extension) | admin, super-admin (extensions with no coordinator)
  reply, mark done     anyone whose `inbox_for` contains the request (and who can sign in)

Anonymous requests are masked by `sender_label`, which EVERY template and JSON helper must use.
"""
from django.core.exceptions import PermissionDenied

from apps.accounts.models import Role

from .models import PrayerRequest

HANDLER_ROLES = (Role.COORDINATOR, Role.ADMIN, Role.SUPER_ADMIN)


def _signed_in(actor):
    """True for a real person who can sign in (`can_sign_in`; never test `status` by hand)."""
    return actor is not None and getattr(actor, "is_authenticated", False) and actor.can_sign_in


def can_send(user):
    """May `user` send prayer requests? Members only: the model needs the sender's extension."""
    return _signed_in(user) and user.role == Role.MEMBER and user.extension_id is not None


def can_use_inbox(actor):
    """Page-level gate for the inbox screens (each request is still checked with `can_handle`)."""
    return _signed_in(actor) and actor.role in HANDLER_ROLES


def can_handle(actor, prayer_request):
    """May `actor` read, reply to and complete this request? True only if it is in their inbox."""
    if not can_use_inbox(actor):
        return False
    return PrayerRequest.objects.inbox_for(actor).filter(pk=prayer_request.pk).exists()


def require(check, actor, *args):
    """Raise PermissionDenied unless `check(actor, *args)` is true; services call this first."""
    if not check(actor, *args):
        raise PermissionDenied("You cannot do this with this prayer request.")


def sender_label(prayer_request, viewer):
    """The sender's name as `viewer` may see it. One function, used everywhere.

    An anonymous request reads "Anonymous" for EVERYONE (coordinators and admins too); the sender sees
    "You (anonymous)". `user_id` is still stored, but it never reaches a template or a JSON reply.
    """
    if prayer_request.is_anonymous:
        if viewer is not None and viewer.pk == prayer_request.user_id:
            return "You (anonymous)"
        return "Anonymous"
    if viewer is not None and viewer.pk == prayer_request.user_id:
        return "You"
    return person_name(prayer_request.user)


def person_name(user):
    """Best available name for a person. (verify) use the repo's own display-name property if it has one."""
    for attr in ("display_name", "full_name"):
        value = getattr(user, attr, None)
        if value:
            return str(value() if callable(value) else value)
    return user.get_full_name() or str(user)
