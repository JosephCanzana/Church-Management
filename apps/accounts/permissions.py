"""accounts.permissions: who may manage what.

Every service calls a check from here, so changing who may do something means
changing THIS file, not rewriting the services. A role_required() decorator on
a view is not enough on its own: it guards the page, these checks guard the
action (and, for people, the particular person it is aimed at).

Who manages whom
    super-admin  everyone (except: nobody can hand out the super-admin role)
    admin        coordinators and members, anywhere; never super-admins or other
                 admins; cannot force delete
    coordinator  members of their OWN extension only: create, edit details,
                 reset password, archive and restore. No role or extension
                 changes, no suspend, no delete.
The people matrix lives in `_ACTOR_RULES` below; `available_actions()` reads the
same matrix, so the buttons a screen shows always match what the server allows.
"""
from django.core.exceptions import PermissionDenied

from .models import Role, Status


# --------------------------------------------------------------------- actors
def _usable(actor):
    """True for a signed-in account that may use the app."""
    return bool(actor is not None and actor.is_authenticated and actor.is_active)


def is_super_admin(actor):
    """True for a signed-in, usable super-admin account."""
    return _usable(actor) and actor.role == Role.SUPER_ADMIN


def is_admin(actor):
    """True for a signed-in, usable admin account (not the super-admin)."""
    return _usable(actor) and actor.role == Role.ADMIN


def is_coordinator(actor):
    """True for a signed-in, usable coordinator who belongs to an extension."""
    return _usable(actor) and actor.role == Role.COORDINATOR and bool(actor.extension_id)


def can_manage_extensions(actor):
    """Create, edit, archive, restore, delete extensions, assign coordinators."""
    return is_super_admin(actor)


def require_extension_manager(actor):
    """Raise PermissionDenied (HTTP 403) unless `actor` may manage extensions."""
    if not can_manage_extensions(actor):
        raise PermissionDenied("Only the super-admin can manage extensions.")


# ------------------------------------------------------------- site settings
def can_manage_site_settings(actor):
    """Change site-wide settings, such as which Bible versions are switched on."""
    return is_super_admin(actor)


def require_site_settings_manager(actor):
    """Raise PermissionDenied (HTTP 403) unless `actor` may change site settings."""
    if not can_manage_site_settings(actor):
        raise PermissionDenied("Only the super-admin can change site settings.")


# --------------------------------------------------------------------- people
# Actions on a person. Strings (not an Enum) so templates and URLs can use them.
VIEW = "view"
EDIT = "edit"                       # name, birth date, suffix, phone, address
CHANGE_PLACEMENT = "placement"      # role and extension
RESET_PASSWORD = "reset_password"
ARCHIVE = "archive"
RESTORE = "restore"
SUSPEND = "suspend"
UNSUSPEND = "unsuspend"
DEACTIVATE = "deactivate"
DELETE = "delete"                   # force delete (archived people only)

PERSON_ACTIONS = (
    VIEW, EDIT, CHANGE_PLACEMENT, RESET_PASSWORD, ARCHIVE, RESTORE,
    SUSPEND, UNSUSPEND, DEACTIVATE, DELETE,
)

# role -> (roles of people they may act on, actions allowed). A coordinator is also
# limited to their own extension (see `can_act_on`).
_ACTOR_RULES = {
    Role.SUPER_ADMIN: ((Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER), set(PERSON_ACTIONS)),
    Role.ADMIN: ((Role.COORDINATOR, Role.MEMBER), set(PERSON_ACTIONS) - {DELETE}),
    Role.COORDINATOR: ((Role.MEMBER,), {VIEW, EDIT, RESET_PASSWORD, ARCHIVE, RESTORE}),
}

#: Roles that may open the people screens at all.
PEOPLE_MANAGER_ROLES = tuple(_ACTOR_RULES)


def can_manage_users(actor):
    """True for anyone who has the people screens (super-admin, admin, coordinator)."""
    return is_super_admin(actor) or is_admin(actor) or is_coordinator(actor)


def require_user_manager(actor):
    """Raise PermissionDenied (HTTP 403) unless `actor` has the people screens."""
    if not can_manage_users(actor):
        raise PermissionDenied("You cannot manage people.")


def can_act_on(actor, person, action):
    """May `actor` do `action` to this particular `person`?

    Role rules first, then the coordinator's extension. Nobody acts on their own
    row except the super-admin editing their own details (every other self
    action is refused in the services with its own message).
    """
    if not can_manage_users(actor) or person is None:
        return False
    if actor.pk == person.pk:
        return is_super_admin(actor) and action in (VIEW, EDIT)
    roles, actions = _ACTOR_RULES[actor.role]
    if action not in actions or person.role not in roles:
        return False
    if actor.role == Role.COORDINATOR:
        return person.extension_id == actor.extension_id
    return True


def require_can_act(actor, person, action):
    """Raise PermissionDenied (HTTP 403) unless `actor` may do `action` to `person`."""
    if not can_act_on(actor, person, action):
        raise PermissionDenied("You cannot do that to this person.")


def available_actions(actor, person):
    """The set of actions to OFFER for `person` right now (buttons, menus).

    Combines the permission matrix with the person's status, so Restore is only
    offered on an archived person and Archive never on one already archived.
    Hiding a button is not access control: the services check again.
    """
    allowed = set()

    def offer(action, condition=True):
        if condition and can_act_on(actor, person, action):
            allowed.add(action)

    offer(VIEW)
    if person.status == Status.ARCHIVED:
        offer(RESTORE)
        offer(DELETE)
        return allowed
    offer(EDIT)
    offer(RESET_PASSWORD)
    offer(ARCHIVE)
    offer(SUSPEND, person.status != Status.SUSPENDED)
    offer(UNSUSPEND, person.status == Status.SUSPENDED)
    offer(DEACTIVATE, person.status == Status.ACTIVE)
    offer(CHANGE_PLACEMENT)
    return allowed


def allowed_bulk_actions(actor):
    """Bulk action names `actor` may use at all (the list screen's button bar)."""
    if not can_manage_users(actor):
        return []
    _, actions = _ACTOR_RULES[actor.role]
    names = {
        "archive": ARCHIVE, "restore": RESTORE, "suspend": SUSPEND, "unsuspend": UNSUSPEND,
        "deactivate": DEACTIVATE, "reset_password": RESET_PASSWORD, "delete": DELETE,
    }
    return [name for name, action in names.items() if action in actions]


def can_change_placement(actor):
    """Move people between extensions or change their role (super-admin and admin)."""
    return is_super_admin(actor) or is_admin(actor)


def require_placement_manager(actor):
    """Raise PermissionDenied (HTTP 403) unless `actor` may place people in extensions."""
    if not can_change_placement(actor):
        raise PermissionDenied("Only an admin can move people between extensions.")


def can_create_person(actor, role, extension):
    """May `actor` create someone with this `role` in this `extension` (None for admins)?"""
    if not can_manage_users(actor) or role not in assignable_roles(actor):
        return False
    if is_coordinator(actor):
        return extension is not None and extension.pk == actor.extension_id
    return True


def require_can_create(actor, role, extension):
    """Raise PermissionDenied (HTTP 403) unless `actor` may create this kind of person."""
    if not can_create_person(actor, role, extension):
        raise PermissionDenied("You cannot create that person.")


def assignable_roles(actor):
    """Role values `actor` may give to someone (when creating or editing a person).

    Nobody can hand out the super-admin role from a screen. A super-admin may
    create admins; an admin only coordinators and members; a coordinator only
    members.
    """
    if is_super_admin(actor):
        return [Role.ADMIN, Role.COORDINATOR, Role.MEMBER]
    if is_admin(actor):
        return [Role.COORDINATOR, Role.MEMBER]
    if is_coordinator(actor):
        return [Role.MEMBER]
    return []


def default_password_roles(actor):
    """Roles `actor` keeps their OWN default password for, and may set.

    Default passwords are personal to the super-admin and each admin, one per role
    beneath them. A coordinator keeps none: they only use and see the global member
    password (`default_password_view_roles`).
    """
    if is_super_admin(actor):
        return [Role.ADMIN, Role.COORDINATOR, Role.MEMBER]
    if is_admin(actor):
        return [Role.COORDINATOR, Role.MEMBER]
    return []


def can_set_default_passwords(actor):
    """Set or remove default passwords (super-admin and admin only)."""
    return bool(default_password_roles(actor))


def default_password_view_roles(actor):
    """Roles whose default password `actor` may LOOK at on the default passwords screen.

    The super-admin and admins see their own. A coordinator sees only the global
    member password (read-only); they cannot set anything.
    """
    if is_coordinator(actor):
        return [Role.MEMBER]
    return default_password_roles(actor)


def can_type_password(actor):
    """Choose a password by hand when creating or resetting someone (super-admin and admin only).

    A coordinator always gives the global member password. Skipping activation (which
    would keep that shared password for good) is limited the same way.
    """
    return is_super_admin(actor) or is_admin(actor)


def require_password_typer(actor):
    """Raise PermissionDenied (HTTP 403) unless `actor` may type a password for someone."""
    if not can_type_password(actor):
        raise PermissionDenied("Only an admin can choose a password for someone.")


# ------------------------------------------------------------- special roles
def can_assign_special_roles(actor, extension_id):
    """Give or take the attendance / tithes-and-offering roles in one extension.

    Super-admin and admin may in any extension; a coordinator only in their own.
    """
    if is_super_admin(actor) or is_admin(actor):
        return True
    return is_coordinator(actor) and actor.extension_id == extension_id


def require_special_role_assigner(actor, extension_id):
    """Raise PermissionDenied (HTTP 403) unless `actor` may assign special roles there."""
    if not can_assign_special_roles(actor, extension_id):
        raise PermissionDenied("You cannot assign special roles in this extension.")


def can_set_special_role_limits(actor):
    """Set how many people an extension may have in each special role."""
    return is_super_admin(actor) or is_admin(actor)


def require_special_role_limit_manager(actor):
    """Raise PermissionDenied (HTTP 403) unless `actor` may set special-role limits."""
    if not can_set_special_role_limits(actor):
        raise PermissionDenied("Only an admin can set special-role limits.")
