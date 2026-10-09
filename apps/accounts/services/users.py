"""accounts.services.users: business rules for managing people (super-admin).

Same conventions as accounts.services: every function checks permission
first, does its work in `transaction.atomic()`, writes `log_action()` in the
same transaction, and reports a rule failure as ServiceError (a message that is
safe to show). Actions on a person who is already in the wanted state return
`changed=False` and log nothing, so a double click is harmless.

PASSWORDS: a new or reset password exists in plain text only inside the return
value (PasswordInfo) so the view can show it ONCE. It is never stored, logged
or put in the session, and audit_log would strip it anyway.

LOCK ORDER (so overlapping requests cannot deadlock): extensions first in
ascending id order, then people (the super-admin rows in ascending id order).
"""
import secrets
from collections import namedtuple
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError
from django.utils import timezone

from apps.audit.services import log_action
from apps.core import services as core_services
from apps.core.services import ServiceError, consume_submission_token
from apps.core.text import clean_text

from ..validators import generate_strong_password, validate_strong_password

from ..models import (
    ArchiveReason, DefaultPassword, Extension, Role, Status, User, UserExtensionHistory,
)
from ..permissions import assignable_roles, default_password_roles, require_user_manager

ROLES_WITH_EXTENSION = (Role.COORDINATOR, Role.MEMBER)
USER_DETAIL_FIELDS = ("first_name", "middle_name", "last_name", "birth_date")
MAX_BULK = 100
MIN_PASSWORD_LENGTH = 8
# No 0/O, 1/l/I: generated passwords get read aloud and typed by hand.
PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# ------------------------------------------------------------------ passwords
@dataclass
class PasswordInfo:
    """How a password was chosen, and what (if anything) may be shown once."""

    kind: str                 # "typed" | "default" | "generated"
    value: str = ""           # the plain text, only for "generated"

    @property
    def display(self):
        """What the show-once dialog prints in the password column."""
        if self.kind == "generated":
            return self.value
        if self.kind == "default":
            return "The default password"
        return "The password you typed"


def generate_password(length=12):
    """A random password a person can type without guessing which letter it is."""
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def _default_password_hash(actor, role):
    """The hash of `actor`'s OWN default password for people of `role`, or None.

    Defaults are personal: coordinator A may keep one password and coordinator
    B another. Only roles beneath the actor can have one.
    """
    if role not in default_password_roles(actor):
        return None
    row = DefaultPassword.objects.filter(owner=actor, applies_to_role=role).first()
    return row.password_hash if row else None


def _set_initial_password(actor, person, typed):
    """Set `person`'s password: what was typed, else `actor`'s default, else a random one."""
    if typed:
        try:
            validate_strong_password(typed)
        except ValidationError as exc:
            raise ServiceError(exc.messages[0])
        person.set_password(typed)
        return PasswordInfo("typed")
    stored = _default_password_hash(actor, person.role)
    if stored:
        person.password = stored          # already a hash; copied, never decoded
        return PasswordInfo("default")
    value = generate_strong_password()
    person.set_password(value)
    return PasswordInfo("generated", value)


# ---------------------------------------------------------------------- names
NAME_FIELDS = ("first_name", "middle_name", "last_name")


def normalize_details(data):
    """Copy of `data` with the name parts the way the database stores them.

    Trimmed, single spaces, lowercase (`core.text.clean_text`), so "Joseph
    Canzana" and "joseph  canzana" are the same person. The screens show names
    through `name_case`.
    """
    cleaned = dict(data)
    for name in NAME_FIELDS:
        if name in cleaned:
            cleaned[name] = clean_text(cleaned[name])
    return cleaned


def find_duplicate_person(first_name, middle_name, last_name, birth_date=None, *, exclude_pk=None):
    """Someone with the same name, or None. Archived people count.

    Two real people can share a name, so a match is ignored only when BOTH have
    a birth date and the dates differ. `__iexact` also catches rows saved
    before names were stored in lowercase.
    """
    people = User.objects.filter(
        first_name__iexact=first_name, middle_name__iexact=middle_name, last_name__iexact=last_name,
    )
    if exclude_pk is not None:
        people = people.exclude(pk=exclude_pk)
    for other in people.order_by("pk"):
        if birth_date and other.birth_date and birth_date != other.birth_date:
            continue
        return other
    return None


def duplicate_message(data, *, exclude_pk=None):
    """A sentence explaining the clash when `data` names someone who exists, else None."""
    data = normalize_details(data)
    other = find_duplicate_person(
        data.get("first_name") or "", data.get("middle_name") or "", data.get("last_name") or "",
        data.get("birth_date"), exclude_pk=exclude_pk,
    )
    if other is None:
        return None
    where = " (archived)" if other.status == Status.ARCHIVED else ""
    return (
        f"{other.full_name}{where} already exists with Account ID {other.account_id}. "
        "If these are two different people, give both a birth date so they can be told apart."
    )


def _refuse_duplicate(data, *, exclude_pk=None):
    message = duplicate_message(data, exclude_pk=exclude_pk)
    if message:
        raise ServiceError(message)


# --------------------------------------------------------------------- locking
Locked = namedtuple("Locked", "person extension other_super_admins")


def _lock_extensions(ids):
    """Lock extension rows in ascending id order. Returns {id: Extension}."""
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    rows = Extension.objects.select_for_update().filter(pk__in=ids).order_by("pk")
    return {e.pk: e for e in rows}


def _lock_person(pk):
    """Lock a person, their extension and (for a super-admin) every super-admin.

    Returns Locked(person, extension, other_super_admins) where the last is the
    OTHER super-admins who are active, used to protect the last one.
    """
    row = User.objects.filter(pk=pk).values("extension_id", "role").first()
    if row is None:
        raise ServiceError("That person no longer exists.")
    extension = _lock_extensions([row["extension_id"]]).get(row["extension_id"])
    if row["role"] == Role.SUPER_ADMIN:
        admins = list(User.objects.select_for_update().filter(role=Role.SUPER_ADMIN).order_by("pk"))
        person = next((a for a in admins if a.pk == pk), None)
        others = [a for a in admins if a.pk != pk and a.status == Status.ACTIVE]
    else:
        person = User.objects.select_for_update().filter(pk=pk).first()
        others = []
    if person is None or person.extension_id != row["extension_id"] or person.role != row["role"]:
        raise ServiceError("This person was just changed by someone else. Reload the page and try again.")
    return Locked(person, extension, others)


def _refuse_self(actor, person, what):
    if actor.pk == person.pk:
        raise ServiceError(f"You cannot {what} your own account.")


def _refuse_last_super_admin(locked, what):
    person = locked.person
    if person.role == Role.SUPER_ADMIN and person.status == Status.ACTIVE and not locked.other_super_admins:
        raise ServiceError(f"You cannot {what} the last active super-admin.")


def _usable_status(person):
    """Status for someone coming back (restore, unsuspend).

    Anyone who still has to change their password goes back to not_activated;
    everyone else goes back to active.
    """
    return Status.NOT_ACTIVATED if person.must_change_password else Status.ACTIVE


def _log_person(action, actor, request, person, **kwargs):
    """One audit row about a person."""
    log_action(
        action,
        actor=actor,
        request=request,
        entity_type="app_user",
        entity_id=person.pk,
        entity_label=str(person),
        extension_id=person.extension_id,
        **kwargs,
    )


# ---------------------------------------------------------------------- create
@dataclass
class CreateResult:
    user: object = None
    password: PasswordInfo = None
    duplicate: bool = False


def create_user(actor, request, data, *, token):
    """Create a person. `data` comes from UserCreateForm.cleaned_data.

    token     -- the form's one-time token; a replay returns duplicate=True and
                 creates nothing.
    activated -- True skips activation (status active, no forced password
                 change). Meant for test accounts.
    Coordinators: the extension must not already have one; the new person is
    set as its coordinator in the same transaction.
    """
    require_user_manager(actor)
    data = normalize_details(data)
    role = data["role"]
    if role not in assignable_roles(actor):
        raise PermissionDenied("You cannot create that role.")
    wanted = data.get("extension") if role in ROLES_WITH_EXTENSION else None
    if role in ROLES_WITH_EXTENSION and wanted is None:
        raise ServiceError("Choose an extension for this role.")

    with transaction.atomic():
        if not consume_submission_token(token):
            return CreateResult(duplicate=True)

        ext = _lock_extensions([wanted.pk]).get(wanted.pk) if wanted is not None else None
        if wanted is not None:
            if ext is None or ext.is_archived:
                raise ServiceError("Choose an extension that is not archived.")
            if role == Role.COORDINATOR and ext.coordinator_id:
                raise ServiceError(f"{ext.name} already has a coordinator. Remove or replace them first.")

        _refuse_duplicate(data)

        activated = bool(data.get("activated"))
        person = User(
            first_name=data["first_name"],
            middle_name=data.get("middle_name", ""),
            last_name=data["last_name"],
            birth_date=data.get("birth_date"),
            role=role,
            extension=ext,
            status=Status.ACTIVE if activated else Status.NOT_ACTIVATED,
            must_change_password=not activated,
            created_by=actor,
        )
        info = _set_initial_password(actor, person, data.get("password") or "")
        person.save()                      # User.save() generates the account id

        if ext is not None:
            UserExtensionHistory.objects.create(
                user=person, extension=ext, from_date=timezone.localdate(),
                reason=UserExtensionHistory.Reason.INITIAL,
            )
            if role == Role.COORDINATOR:
                ext.coordinator = person
                ext.save(update_fields=["coordinator", "updated_at"])

        _log_person(
            "account.create", actor, request, person,
            after={
                "role": role,
                "extension_id": ext.pk if ext else None,
                "status": person.status,
                "activated_by_admin": activated,
                "how_set": info.kind,          # not "password_...": audit drops those keys
            },
        )
    return CreateResult(user=person, password=info)


# ---------------------------------------------------------------------- update
def update_user(actor, request, ref, data):
    """Change a person's details, role and extension together. Returns (person, changed).

    Rules: no role change on yourself; the last active super-admin keeps their
    role; a coordinator seat is cleared when someone leaves it and taken when
    someone enters it (an extension has one coordinator); moving extension
    closes the open history row and opens a new one.
    """
    require_user_manager(actor)
    data = normalize_details(data)
    new_role = data["role"]
    wanted = data.get("extension") if new_role in ROLES_WITH_EXTENSION else None
    if new_role in ROLES_WITH_EXTENSION and wanted is None:
        raise ServiceError("Choose an extension for this role.")

    with transaction.atomic():
        current_ext_id = User.objects.filter(pk=ref.pk).values_list("extension_id", flat=True).first()
        exts = _lock_extensions([current_ext_id, wanted.pk if wanted else None])
        locked = _lock_person(ref.pk)
        person = locked.person
        if person.status == Status.ARCHIVED:
            raise ServiceError("Restore this person before editing them.")
        # Checked on the locked row: keeping the current role is always fine (a
        # super-admin stays one); giving a role nobody can hand out is not.
        if new_role != person.role and new_role not in assignable_roles(actor):
            raise PermissionDenied("You cannot give that role.")
        old_ext = exts.get(person.extension_id)
        if person.extension_id and old_ext is None:
            raise ServiceError("This person was just changed by someone else. Reload the page and try again.")
        new_ext = exts.get(wanted.pk) if wanted is not None else None

        before = {name: getattr(person, name) for name in USER_DETAIL_FIELDS}
        for name in USER_DETAIL_FIELDS:
            if name in data:
                setattr(person, name, data[name])
        details_changed = [n for n in USER_DETAIL_FIELDS if before[n] != getattr(person, n)]
        if details_changed:
            _refuse_duplicate({n: getattr(person, n) for n in USER_DETAIL_FIELDS}, exclude_pk=person.pk)
        old_role = person.role
        old_ext_id = person.extension_id
        role_changed = new_role != old_role
        ext_changed = (new_ext.pk if new_ext else None) != old_ext_id

        if role_changed:
            _refuse_self(actor, person, "change the role of")
            if new_role != Role.SUPER_ADMIN:
                _refuse_last_super_admin(locked, "change the role of")
        if ext_changed and new_ext is not None and new_ext.is_archived:
            raise ServiceError("Choose an extension that is not archived.")
        if new_role == Role.COORDINATOR and new_ext.coordinator_id not in (None, person.pk):
            raise ServiceError(f"{new_ext.name} already has a coordinator. Remove or replace them first.")
        if not (details_changed or role_changed or ext_changed):
            return person, False

        # Leaving the coordinator seat (a different role, or a different extension).
        if (
            old_role == Role.COORDINATOR and old_ext is not None
            and old_ext.coordinator_id == person.pk
            and (new_role != Role.COORDINATOR or new_ext is None or new_ext.pk != old_ext.pk)
        ):
            old_ext.coordinator = None
            old_ext.save(update_fields=["coordinator", "updated_at"])

        person.role = new_role
        person.extension = new_ext
        person.save(update_fields=[*details_changed, "role", "extension", "updated_at"])

        # Taking the coordinator seat.
        if new_role == Role.COORDINATOR and new_ext.coordinator_id != person.pk:
            new_ext.coordinator = person
            new_ext.save(update_fields=["coordinator", "updated_at"])

        if ext_changed:
            UserExtensionHistory.objects.filter(user=person, to_date__isnull=True).update(
                to_date=timezone.localdate()
            )
            if new_ext is not None:
                UserExtensionHistory.objects.create(
                    user=person, extension=new_ext, from_date=timezone.localdate(),
                    reason=UserExtensionHistory.Reason.MANUAL,
                )

        if details_changed:
            _log_person(
                "account.update", actor, request, person,
                before={n: before[n] for n in details_changed},
                after={n: getattr(person, n) for n in details_changed},
            )
        if role_changed:
            _log_person("account.role_change", actor, request, person,
                        before={"role": old_role}, after={"role": new_role})
        if ext_changed:
            _log_person("account.extension_change", actor, request, person,
                        before={"extension_id": old_ext_id},
                        after={"extension_id": new_ext.pk if new_ext else None})
    return person, True


# --------------------------------------------------------------- status changes
def archive_user(actor, request, ref):
    """Archive a person (not yourself, not the last active super-admin).

    A coordinator leaves the seat and becomes a member, so restoring them later
    never brings back a coordinator the extension no longer has.
    """
    require_user_manager(actor)
    with transaction.atomic():
        locked = _lock_person(ref.pk)
        person = locked.person
        if person.status == Status.ARCHIVED:
            return person, False
        _refuse_self(actor, person, "archive")
        _refuse_last_super_admin(locked, "archive")

        old_status = person.status
        extra = ["status", "archive_reason"]
        if person.role == Role.COORDINATOR:
            ext = locked.extension
            if ext is not None and ext.coordinator_id == person.pk:
                ext.coordinator = None
                ext.save(update_fields=["coordinator", "updated_at"])
            person.role = Role.MEMBER
            extra.append("role")
            _log_person("account.role_change", actor, request, person,
                        before={"role": Role.COORDINATOR}, after={"role": Role.MEMBER})
        person.status = Status.ARCHIVED
        person.archive_reason = ArchiveReason.MANUAL
        core_services.archive(person, extra_fields=extra)
        _log_person("account.archive", actor, request, person,
                    before={"status": old_status}, after={"status": Status.ARCHIVED})
    return person, True


def restore_user(actor, request, ref):
    """Bring an archived person back (their extension must not be archived)."""
    require_user_manager(actor)
    with transaction.atomic():
        locked = _lock_person(ref.pk)
        person = locked.person
        if person.status != Status.ARCHIVED:
            return person, False
        ext = locked.extension
        if ext is not None and ext.is_archived:
            raise ServiceError(f"{ext.name} is archived. Restore the extension first.")
        person.status = _usable_status(person)
        person.archive_reason = None
        core_services.restore(person, extra_fields=["status", "archive_reason"])
        _log_person("account.restore", actor, request, person,
                    before={"status": Status.ARCHIVED}, after={"status": person.status})
    return person, True


def suspend_user(actor, request, ref):
    """Block login without losing anything. Reversible; never purged."""
    require_user_manager(actor)
    with transaction.atomic():
        locked = _lock_person(ref.pk)
        person = locked.person
        if person.status == Status.SUSPENDED:
            return person, False
        if person.status == Status.ARCHIVED:
            raise ServiceError("Restore this person before suspending them.")
        _refuse_self(actor, person, "suspend")
        _refuse_last_super_admin(locked, "suspend")
        old_status = person.status
        person.status = Status.SUSPENDED
        person.save(update_fields=["status", "updated_at"])
        _log_person("account.suspend", actor, request, person,
                    before={"status": old_status}, after={"status": Status.SUSPENDED})
    return person, True


def unsuspend_user(actor, request, ref):
    """Lift a suspension."""
    require_user_manager(actor)
    with transaction.atomic():
        locked = _lock_person(ref.pk)
        person = locked.person
        if person.status != Status.SUSPENDED:
            return person, False
        person.status = _usable_status(person)
        person.save(update_fields=["status", "updated_at"])
        _log_person("account.unsuspend", actor, request, person,
                    before={"status": Status.SUSPENDED}, after={"status": person.status})
    return person, True


def deactivate_user(actor, request, ref):
    """Send an active person back to not_activated: they must activate again."""
    require_user_manager(actor)
    with transaction.atomic():
        locked = _lock_person(ref.pk)
        person = locked.person
        if person.status == Status.NOT_ACTIVATED:
            return person, False
        if person.status != Status.ACTIVE:
            raise ServiceError("Only active accounts can be deactivated.")
        _refuse_self(actor, person, "deactivate")
        _refuse_last_super_admin(locked, "deactivate")
        person.status = Status.NOT_ACTIVATED
        person.must_change_password = True
        person.save(update_fields=["status", "must_change_password", "updated_at"])
        _log_person("account.deactivate", actor, request, person,
                    before={"status": Status.ACTIVE}, after={"status": Status.NOT_ACTIVATED})
    return person, True


# ---------------------------------------------------------------- reset password
@dataclass
class ResetResult:
    user: object = None
    password: PasswordInfo = None
    duplicate: bool = False


def _reset_password(actor, request, ref, typed=""):
    """Give a person a new password: `typed`, else the actor's default, else generated.

    Returns (person, info).
    """
    with transaction.atomic():
        locked = _lock_person(ref.pk)
        person = locked.person
        if person.status == Status.ARCHIVED:
            raise ServiceError("Restore this person before resetting their password.")
        _refuse_self(actor, person, "reset the password of")
        info = _set_initial_password(actor, person, typed)
        person.must_change_password = True
        # Changing the hash also signs the person out of every open session.
        person.save(update_fields=["password", "must_change_password", "updated_at"])
        _log_person("account.password_reset", actor, request, person,
                    after={"needs_activation": True, "how_set": info.kind})
    return person, info


def reset_password(actor, request, ref, *, token, typed=""):
    """Reset one person's password. A replayed token returns duplicate=True.

    `typed` is an optional custom password (must pass the strong-password rule); empty means the
    actor's default for the person's role, else a generated one.
    """
    require_user_manager(actor)
    with transaction.atomic():
        if not consume_submission_token(token):
            return ResetResult(duplicate=True)
        person, info = _reset_password(actor, request, ref, typed)
    return ResetResult(user=person, password=info)


# ------------------------------------------------------------------ force delete
def force_delete_user(actor, request, ref):
    """Permanently delete an ARCHIVED person (not yourself). Returns (None, True).

    The audit row is written first in the same transaction; if the database
    refuses the delete (other records still point at the person) everything,
    including that row, rolls back. The profile image is removed from disk only
    after the delete has committed.
    """
    require_user_manager(actor)
    try:
        with transaction.atomic():
            locked = _lock_person(ref.pk)
            person = locked.person
            if person.status != Status.ARCHIVED:
                raise ServiceError("Only archived people can be deleted.")
            _refuse_self(actor, person, "delete")
            image = person.profile_image
            image_name, storage = (image.name, image.storage) if image else (None, None)
            _log_person("account.delete", actor, request, person,
                        before={"role": person.role, "extension_id": person.extension_id})
            core_services.force_delete(person)
            if image_name:
                transaction.on_commit(lambda: storage.delete(image_name))
    except (ProtectedError, RestrictedError):
        raise ServiceError(
            "This person is still referenced by other records, so they cannot be deleted yet."
        )
    return None, True


# --------------------------------------------------------------------------- bulk
BULK_ACTIONS = {
    "archive": archive_user,
    "restore": restore_user,
    "suspend": suspend_user,
    "unsuspend": unsuspend_user,
    "deactivate": deactivate_user,
    "delete": force_delete_user,
}
BULK_ACTION_NAMES = (*BULK_ACTIONS, "reset_password")
# Shown when an action had nothing to do for a person.
NOTHING_TO_DO = {
    "archive": "already archived",
    "restore": "not archived",
    "suspend": "already suspended",
    "unsuspend": "not suspended",
    "deactivate": "not activated yet",
}


@dataclass
class BulkResult:
    done: list = field(default_factory=list)       # labels of people it worked for
    skipped: list = field(default_factory=list)    # (label, reason)
    passwords: list = field(default_factory=list)  # [name, account id, text] for reset_password
    duplicate: bool = False


def run_bulk(actor, request, action, ids, *, token):
    """Run one action over many people. Each person is its own transaction.

    A person the action cannot apply to (yourself, the last super-admin, wrong
    status, gone) is skipped with a reason; it never fails the others. People
    are handled in ascending id order. The token is consumed first, so a
    repeated submission of the same selection does nothing.
    """
    require_user_manager(actor)
    if action not in BULK_ACTION_NAMES:
        raise ServiceError("That action is not available.")
    ids = sorted({int(i) for i in ids})
    if not ids:
        raise ServiceError("Select at least one person.")
    if len(ids) > MAX_BULK:
        raise ServiceError(f"Select at most {MAX_BULK} people at a time.")

    with transaction.atomic():
        if not consume_submission_token(token):
            return BulkResult(duplicate=True)

    result = BulkResult()
    for pk in ids:
        person = User.objects.filter(pk=pk).first()
        if person is None:
            result.skipped.append((f"#{pk}", "no longer exists"))
            continue
        label = person.full_name
        try:
            if action == "reset_password":
                _, info = _reset_password(actor, request, person)
                result.passwords.append([person.full_name, person.account_id, info.display])
                result.done.append(label)
            else:
                _, changed = BULK_ACTIONS[action](actor, request, person)
                if changed:
                    result.done.append(label)
                else:
                    result.skipped.append((label, NOTHING_TO_DO.get(action, "nothing to do")))
        except ServiceError as exc:
            result.skipped.append((label, str(exc)))

    if action == "archive" and len(result.done) >= 2:
        log_action(
            "account.bulk_archive", actor=actor, request=request, entity_type="app_user",
            after={"count": len(result.done)},
        )
    return result