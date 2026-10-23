"""buddy.permissions: the one place that decides who may use Buddy.

Any role may pair, but only a person who can sign in (`User.can_sign_in`). Guests and
no-account members never hold a buddy, a request or a notification.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.views.decorators.cache import never_cache

from apps.accounts.models import PersonType, Status


def can_use_buddy(user):
    """True for a signed-in person who can still sign in (any role)."""
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "can_sign_in", False))


def signable_q(prefix=""):
    """A `Q` that keeps only people who can sign in, for queries that cannot call the property.

    It is the database twin of `User.can_sign_in` (a `member_account` who is neither archived
    nor suspended). `prefix` reaches the person through a relation, for example
    `signable_q("from_user__")`. A test compares it with the property for every type and status,
    so the two cannot drift apart without a failing test.
    """
    return (
        Q(**{f"{prefix}person_type": PersonType.MEMBER_ACCOUNT})
        & ~Q(**{f"{prefix}status__in": [Status.ARCHIVED, Status.SUSPENDED]})
    )


def buddy_screen(view):
    """Guard a Buddy view: sign-in required, no caching, and only people who may use Buddy.

    Hiding a link is not access control, so every view in this app is wrapped with this.
    """

    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not can_use_buddy(request.user):
            raise PermissionDenied("Buddy is not available for this account.")
        return view(request, *args, **kwargs)

    return login_required(never_cache(guarded))
