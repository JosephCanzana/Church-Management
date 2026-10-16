"""Permission rules for managing public landing-page content."""
from django.core.exceptions import PermissionDenied

from apps.accounts.models import Role


def can_manage_landing(actor):
    """Return whether the actor is an active admin or super-admin."""
    return bool(
        actor is not None
        and getattr(actor, "is_authenticated", False)
        and actor.is_active
        and actor.role in (Role.ADMIN, Role.SUPER_ADMIN)
    )


def require_landing_manager(actor):
    """Raise PermissionDenied unless the actor may manage landing content."""
    if not can_manage_landing(actor):
        raise PermissionDenied("Only an admin can manage landing content.")