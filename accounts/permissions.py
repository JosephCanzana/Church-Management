"""accounts.permissions: who may manage what.

Only the super-admin manages extensions (and, in the next step, users) for
now. Every service calls a check from here, so opening a screen to admins or
coordinators later means changing THIS file and adding routes, not rewriting
the services. A role_required() decorator on a view is not enough on its own:
it guards the page, these checks guard the action.
"""
from django.core.exceptions import PermissionDenied

from .models import Role


def is_super_admin(actor):
    """True for a signed-in, usable super-admin account."""
    return bool(
        actor is not None
        and actor.is_authenticated
        and actor.is_active
        and actor.role == Role.SUPER_ADMIN
    )


def can_manage_extensions(actor):
    """Create, edit, archive, restore, delete extensions, assign coordinators."""
    return is_super_admin(actor)


def require_extension_manager(actor):
    """Raise PermissionDenied (HTTP 403) unless `actor` may manage extensions."""
    if not can_manage_extensions(actor):
        raise PermissionDenied("Only the super-admin can manage extensions.")
