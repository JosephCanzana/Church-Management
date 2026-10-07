"""accounts.services: business logic for the accounts app.

Views stay thin and call functions from here. Per AGENTS.md, service functions
wrap their work in `transaction.atomic()` and call `log_action()` inside the
same transaction.

Sections: login, then extension management (super-admin).
"""
from dataclasses import dataclass

from django.contrib.auth import authenticate, login
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, RestrictedError

from audit.models import AuditLog
from audit.services import log_action
from core import services as core_services
from core.services import ServiceError

from .backends import find_user
from .models import Extension, Role, Status, User
from .permissions import require_extension_manager


# ============================================================ login
@dataclass
class LoginResult:
    """What happened during a login attempt.

    ok               -- True if the person is now logged in.
    user             -- the logged-in User, or None on failure.
    needs_activation -- True if they must finish activation (set a password)
                        before using the app.
    """

    ok: bool
    user: object = None
    needs_activation: bool = False


def _log_login_failure(request, identifier):
    """Write one failed-login row to the audit log.

    PRIVACY: never write the password, and never write the identifier text the
    person typed. People sometimes type their password into the id box, so the
    raw text could leak a password. We store only the account's own id/label
    (looked up from the database, not copied from the form) and a reason code.

    The reason code is worked out here by looking the account up again. It is
    one of: "unknown_account", "bad_password", "archived". audit_log has no
    free-form details column, so it goes in `after`.
    """
    user = find_user(identifier)
    if user is None:
        reason = "unknown_account"
    elif user.status == Status.ARCHIVED:
        reason = "archived"
    else:
        reason = "bad_password"

    log_action(
        "account.login_failed",
        request=request,                  # supplies ip_address and user_agent
        entity_type="app_user",
        entity_id=user.pk if user else None,
        entity_label=user.account_id if user else None,
        extension_id=user.extension_id if user else None,
        after={"reason": reason},
        status=AuditLog.Result.FAILED,
    )


def attempt_login(request, identifier, password):
    """Try to log someone in and report the outcome.

    Steps:
      1. Ask Django to authenticate (our backend checks id/email + password
         and rejects archived accounts).
      2. On failure, write an audit row and return a failed result. The view
         shows ONE generic message for every failure type, so an attacker
         cannot tell a wrong password from an unknown account.
      3. On success, call Django's `login()`. This creates the session and
         fires the `user_logged_in` signal, which updates `last_login` and
         drives the daily-open streak in the `faith` app.
      4. Flag whether the person still has to go through activation
         (never activated, or forced to change their password).
    """
    with transaction.atomic():
        user = authenticate(request, identifier=identifier, password=password)

        if user is None:
            _log_login_failure(request, identifier)
            return LoginResult(ok=False)

        login(request, user)

        needs_activation = (
            user.status == Status.NOT_ACTIVATED or user.must_change_password
        )
        return LoginResult(ok=True, user=user, needs_activation=needs_activation)


# ============================================================ extensions
# Locking order used by every function below (so two overlapping requests can
# never deadlock): the extension row first, then the people involved in
# ascending id order.

#: The editable columns of Extension. Also what the audit log records.
EXTENSION_FIELDS = (
    "name", "building_number", "street", "barangay",
    "municipality", "province", "country", "postal_code",
)


def _extension_snapshot(extension):
    """The editable fields as a dict (no private data, safe for audit_log)."""
    return {name: getattr(extension, name) for name in EXTENSION_FIELDS}


def _lock_extension(pk):
    """Fetch an extension with its row locked. Call inside transaction.atomic().

    No select_related on purpose: Postgres refuses FOR UPDATE on the nullable
    side of an outer join (Extension.coordinator is nullable).
    """
    try:
        return Extension.objects.select_for_update().get(pk=pk)
    except Extension.DoesNotExist:
        raise ServiceError("That extension no longer exists.")


def _log_extension(action, actor, request, extension, **kwargs):
    """One audit row about an extension, tagged with its own id."""
    log_action(
        action,
        actor=actor,
        request=request,
        entity_type="extension",
        entity_id=extension.pk,
        entity_label=extension.name,
        extension_id=extension.pk,
        **kwargs,
    )


def _log_role_change(actor, request, person, extension, old_role, new_role):
    """One audit row for a role change that happened as a side effect."""
    log_action(
        "account.role_change",
        actor=actor,
        request=request,
        entity_type="app_user",
        entity_id=person.pk,
        entity_label=str(person),
        extension_id=extension.pk,
        before={"role": old_role},
        after={"role": new_role},
    )


def create_extension(actor, request, data):
    """Create an extension. `data` holds EXTENSION_FIELDS values.

    A duplicate name (also caught earlier by the form) becomes a ServiceError.
    """
    require_extension_manager(actor)
    values = {k: v for k, v in data.items() if k in EXTENSION_FIELDS}
    try:
        with transaction.atomic():
            extension = Extension.objects.create(**values)
            _log_extension(
                "extension.create", actor, request, extension,
                after=_extension_snapshot(extension),
            )
    except IntegrityError:
        raise ServiceError("An extension with that name already exists.")
    return extension


def update_extension(actor, request, extension, data):
    """Change an extension's details. Returns (extension, changed).

    Only fields that really changed are written and logged, so saving a form
    with no edits leaves no audit row. Archived extensions must be restored
    first.
    """
    require_extension_manager(actor)
    try:
        with transaction.atomic():
            locked = _lock_extension(extension.pk)
            if locked.is_archived:
                raise ServiceError("Restore this extension before editing it.")
            before = _extension_snapshot(locked)
            for name in EXTENSION_FIELDS:
                if name in data:
                    setattr(locked, name, data[name])
            after = _extension_snapshot(locked)
            changed = [n for n in EXTENSION_FIELDS if before[n] != after[n]]
            if not changed:
                return locked, False
            locked.save(update_fields=[*changed, "updated_at"])
            _log_extension(
                "extension.update", actor, request, locked,
                before={n: before[n] for n in changed},
                after={n: after[n] for n in changed},
            )
    except IntegrityError:
        raise ServiceError("An extension with that name already exists.")
    return locked, True


def assign_coordinator(actor, request, extension, person):
    """Make `person` the coordinator of `extension`. Returns (extension, changed).

    Rules (the database cannot enforce them, so they live here):
      - The person must already belong to this extension (member or
        coordinator) and not be archived. They are never moved between
        extensions by this action.
      - They become role=coordinator. A previous coordinator of this
        extension becomes a member of it.
      - Choosing the current coordinator again does nothing.
    """
    require_extension_manager(actor)
    with transaction.atomic():
        ext = _lock_extension(extension.pk)
        if ext.is_archived:
            raise ServiceError("Restore this extension before changing its coordinator.")

        wanted = {person.pk}
        if ext.coordinator_id:
            wanted.add(ext.coordinator_id)
        people = {
            p.pk: p
            for p in User.objects.select_for_update().filter(pk__in=wanted).order_by("pk")
        }
        new = people.get(person.pk)
        if (
            new is None
            or new.extension_id != ext.pk
            or new.role not in (Role.MEMBER, Role.COORDINATOR)
            or new.status == Status.ARCHIVED
        ):
            raise ServiceError(
                "Only an active member of this extension can become its coordinator."
            )
        if ext.coordinator_id == new.pk and new.role == Role.COORDINATOR:
            return ext, False

        old = people.get(ext.coordinator_id) if ext.coordinator_id else None
        if old is not None and old.pk != new.pk and old.role == Role.COORDINATOR:
            old.role = Role.MEMBER
            old.save(update_fields=["role", "updated_at"])
            _log_role_change(actor, request, old, ext, Role.COORDINATOR, Role.MEMBER)

        if new.role != Role.COORDINATOR:
            previous_role = new.role
            new.role = Role.COORDINATOR
            new.save(update_fields=["role", "updated_at"])
            _log_role_change(actor, request, new, ext, previous_role, Role.COORDINATOR)

        ext.coordinator = new
        ext.save(update_fields=["coordinator", "updated_at"])
        _log_extension(
            "extension.coordinator_change", actor, request, ext,
            before={"coordinator": old.account_id if old else None},
            after={"coordinator": new.account_id},
        )
    return ext, True


def unassign_coordinator(actor, request, extension):
    """Remove the coordinator; they stay in the extension as a member."""
    require_extension_manager(actor)
    with transaction.atomic():
        ext = _lock_extension(extension.pk)
        if not ext.coordinator_id:
            return ext, False
        old = User.objects.select_for_update().filter(pk=ext.coordinator_id).first()
        if old is not None and old.role == Role.COORDINATOR:
            old.role = Role.MEMBER
            old.save(update_fields=["role", "updated_at"])
            _log_role_change(actor, request, old, ext, Role.COORDINATOR, Role.MEMBER)
        ext.coordinator = None
        ext.save(update_fields=["coordinator", "updated_at"])
        _log_extension(
            "extension.coordinator_change", actor, request, ext,
            before={"coordinator": old.account_id if old else None},
            after={"coordinator": None},
        )
    return ext, True


def archive_extension(actor, request, extension):
    """Archive an extension. Returns (extension, changed).

    Blocked while any non-archived person still belongs to it (move or archive
    them first). An already archived extension is left alone and logs nothing,
    so a double click is harmless. Any leftover coordinator link (an archived
    person) is cleared.
    """
    require_extension_manager(actor)
    with transaction.atomic():
        ext = _lock_extension(extension.pk)
        if ext.is_archived:
            return ext, False
        people = User.objects.filter(extension=ext).exclude(status=Status.ARCHIVED).count()
        if people:
            noun = "person" if people == 1 else "people"
            raise ServiceError(
                f"{ext.name} still has {people} {noun}. Move or archive them first."
            )
        extra = []
        if ext.coordinator_id:
            ext.coordinator = None
            extra.append("coordinator")
        core_services.archive(ext, extra_fields=extra)
        _log_extension(
            "extension.archive", actor, request, ext,
            before={"archived": False}, after={"archived": True},
        )
    return ext, True


def restore_extension(actor, request, extension):
    """Bring an archived extension back. Returns (extension, changed)."""
    require_extension_manager(actor)
    with transaction.atomic():
        ext = _lock_extension(extension.pk)
        if not ext.is_archived:
            return ext, False
        core_services.restore(ext)
        _log_extension(
            "extension.restore", actor, request, ext,
            before={"archived": True}, after={"archived": False},
        )
    return ext, True


def force_delete_extension(actor, request, extension):
    """Permanently delete an ARCHIVED extension. Returns (None, True).

    The audit row is written first, in the same transaction. If the database
    refuses the delete (people or records still reference the extension) the
    whole transaction, including that row, is rolled back.
    """
    require_extension_manager(actor)
    try:
        with transaction.atomic():
            ext = _lock_extension(extension.pk)
            if not ext.is_archived:
                raise ServiceError("Only archived extensions can be deleted.")
            _log_extension(
                "extension.delete", actor, request, ext,
                before=_extension_snapshot(ext),
            )
            core_services.force_delete(ext)
    except (ProtectedError, RestrictedError):
        raise ServiceError(
            "This extension is still used by people or records, so it cannot be "
            "deleted yet. Delete or purge those first."
        )
    return None, True
