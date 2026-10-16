"""Permission rules for managing resources and viewing the published library."""
from django.core.exceptions import PermissionDenied

from apps.accounts.models import Role


_MANAGERS = (Role.SUPER_ADMIN, Role.ADMIN)
_LIBRARY_READERS = (Role.COORDINATOR, Role.MEMBER)


def _usable(actor):
    """True for an authenticated account that can currently sign in."""
    return bool(actor is not None and getattr(actor, "is_authenticated", False) and actor.is_active)


def can_manage_resources(actor):
    """Return whether the actor can create, edit, publish, archive, or delete resources."""
    return _usable(actor) and actor.role in _MANAGERS


def require_resource_manager(actor):
    """Raise PermissionDenied unless the actor is an admin or super-admin."""
    if not can_manage_resources(actor):
        raise PermissionDenied("Only an admin can manage resources.")


def can_view_monthly_theme(actor, theme):
    """Allow managers to inspect all themes and others to view published active themes."""
    if not _usable(actor) or theme is None:
        return False
    if actor.role in _MANAGERS:
        return True
    return (
        actor.role in _LIBRARY_READERS
        and theme.archived_at is None
        and theme.is_published
    )


def can_view_monthly_resource(actor, resource):
    """Apply the parent theme's visibility rule to one of its files."""
    return bool(resource is not None and can_view_monthly_theme(actor, resource.monthly_theme))


def can_view_weekly_powerpoint(actor, powerpoint):
    """Allow managers all records and coordinators active weekly presentations."""
    if not _usable(actor) or powerpoint is None:
        return False
    if actor.role in _MANAGERS:
        return True
    return actor.role == Role.COORDINATOR and powerpoint.archived_at is None


def can_view_general_resource(actor, resource):
    """Allow managers all records and coordinators active general resources."""
    if not _usable(actor) or resource is None:
        return False
    if actor.role in _MANAGERS:
        return True
    return actor.role == Role.COORDINATOR and resource.archived_at is None