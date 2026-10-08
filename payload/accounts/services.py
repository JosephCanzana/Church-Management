"""accounts.services: business logic for the accounts app.

Views stay thin and call functions from here. Per AGENTS.md, service functions
wrap their work in `transaction.atomic()` and call `log_action()` inside the
same transaction.

Sections: login, then extension management (super-admin).

Extension text is STORED lowercase with single spaces (`core.text.clean_text`)
and SHOWN in title case (`core.text.title_case`). Older rows may still hold
mixed case, so every comparison below ignores case and nothing rewrites an
old value until someone really changes it.
"""
from dataclasses import dataclass

from django.contrib.auth import authenticate, login
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, RestrictedError

from audit.models import AuditLog
from audit.services import log_action
from core import services as core_services
from core.services import ServiceError
from core.text import clean_text, title_case

from .address import check_address
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
    one of: "unknown_account", "bad_password", "archived", "suspended". audit_log has no
    free-form details column, so it goes in `after`.
    """
    user = find_user(identifier)
    if user is None:
        reason = "unknown_account"
    elif user.status == Status.ARCHIVED:
        reason = "archived"
    elif user.status == Status.SUSPENDED:
        reason = "suspended"
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


#: Every field must be filled in except the building number.
REQUIRED_EXTENSION_FIELDS = tuple(f for f in EXTENSION_FIELDS if f != "building_number")

_FIELD_LABELS = {
    "name": "extension name", "country": "country", "province": "province",
    "municipality": "municipality", "barangay": "barangay",
    "postal_code": "postal code", "street": "street",
}


def clean_extension_data(data, existing=None):
    """Normalise and check extension values; return a dict of EXTENSION_FIELDS.

    This is the server-side rule set, so it holds even if the form or the
    browser's JavaScript is bypassed:
      - text is trimmed, single-spaced and lowercase (the postal code keeps
        its case);
      - every field except the building number is required;
      - for the Philippines the province, municipality, barangay and postal
        code must match `accounts.address` (`existing` lets an old, unlisted
        value stay until it is changed).
    Raises ServiceError with a message the view can show.
    """
    values = {}
    for name in EXTENSION_FIELDS:
        raw = data.get(name) or ""
        values[name] = " ".join(str(raw).split()) if name == "postal_code" else clean_text(raw)
    missing = [_FIELD_LABELS[n] for n in REQUIRED_EXTENSION_FIELDS if not values[n]]
    if missing:
        raise ServiceError("Please fill in: " + ", ".join(missing) + ".")
    problems = check_address(values, existing)
    if problems:
        raise ServiceError(next(iter(problems.values())))
    return values


def _name_taken(name, exclude_pk=None):
    """True when another extension already uses this name, ignoring case."""
    taken = Extension.objects.filter(name__iexact=name)
    if exclude_pk:
        taken = taken.exclude(pk=exclude_pk)
    return taken.exists()


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
        entity_label=title_case(extension.name),
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

    Values are cleaned by `clean_extension_data` (lowercase, required fields,
    Philippine address rules). A duplicate name, whatever its capitalisation,
    becomes a ServiceError.
    """
    require_extension_manager(actor)
    values = clean_extension_data(data)
    try:
        with transaction.atomic():
            if _name_taken(values["name"]):
                raise ServiceError("An extension with that name already exists.")
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
    with no edits leaves no audit row. "Changed" ignores capitalisation, so an
    old "Santa Rosa" is not rewritten as "santa rosa" unless something else
    about it changed. Archived extensions must be restored first.
    """
    require_extension_manager(actor)
    try:
        with transaction.atomic():
            locked = _lock_extension(extension.pk)
            if locked.is_archived:
                raise ServiceError("Restore this extension before editing it.")
            before = _extension_snapshot(locked)
            merged = {**before, **{k: v for k, v in data.items() if k in EXTENSION_FIELDS}}
            clean = clean_extension_data(merged, existing=before)
            changed = [n for n in EXTENSION_FIELDS if before[n].lower() != clean[n].lower()]
            if not changed:
                return locked, False
            if "name" in changed and _name_taken(clean["name"], exclude_pk=locked.pk):
                raise ServiceError("An extension with that name already exists.")
            for name in changed:
                setattr(locked, name, clean[name])
            locked.save(update_fields=[*changed, "updated_at"])
            _log_extension(
                "extension.update", actor, request, locked,
                before={n: before[n] for n in changed},
                after={n: clean[n] for n in changed},
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
                f"{title_case(ext.name)} still has {people} {noun}. Move or archive them first."
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


# ------------------------------------------------------------ bulk actions
@dataclass
class BulkResult:
    """Outcome of a bulk action on extensions.

    done    -- display names of the extensions that were changed.
    skipped -- one readable sentence per extension that was left alone, with
               the reason (still has people, not archived, already archived...).
    """

    done: list
    skipped: list


def _run_bulk(actor, request, ids, service, unchanged_message):
    """Apply `service` to each extension on its own, never stopping at a failure.

    Each call is its own transaction (the services use `transaction.atomic()`),
    so an extension that is refused leaves the others untouched. Ids that no
    longer exist are ignored, like a double click on a deleted row.
    """
    require_extension_manager(actor)
    result = BulkResult(done=[], skipped=[])
    for ext in Extension.objects.filter(pk__in=ids).order_by("name", "pk"):
        name = title_case(ext.name)  # read before the service: delete removes the row
        try:
            _, changed = service(actor, request, ext)
        except ServiceError as exc:
            reason = str(exc)
            # Some refusals already name the extension; add the name when they do not.
            result.skipped.append(reason if name.lower() in reason.lower() else f"{name}: {reason}")
            continue
        if changed:
            result.done.append(name)
        else:
            result.skipped.append(unchanged_message.format(name=name))
    return result


def bulk_archive_extensions(actor, request, ids):
    """Archive several extensions; ones that still have people are skipped."""
    return _run_bulk(actor, request, ids, archive_extension, "{name} is already archived.")


def bulk_delete_extensions(actor, request, ids):
    """Permanently delete several ARCHIVED extensions; others are skipped."""
    return _run_bulk(actor, request, ids, force_delete_extension, "{name} was not deleted.")
