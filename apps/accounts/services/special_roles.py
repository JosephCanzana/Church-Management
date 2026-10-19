"""accounts.services.special_roles: the two special member roles and their per-extension limits.

Special roles (tithes_offering, attendance) let a member help the coordinator. A coordinator gives
and takes them directly for the members of their own extension; the super-admin and admins may do it
in any extension. HOW MANY people may hold a role in one extension is a limit that only the
super-admin and admins set (`set_special_role_limits`, or `bulk_set_special_role_limits` for many
extensions at once). With no limit set for a role in an extension, nobody there can be given that
role yet.

Rules the service enforces, whoever clicked:
- only a member of an extension can hold a role, and not an archived or suspended one;
- giving a role locks the extension row, so two clicks can never both take the last seat;
- a limit cannot be set below the number of people who already hold the role, and cannot be
  cleared while anyone holds it;
- a limit cannot be higher than the number of members in the extension who can sign in (the only
  people who could ever hold a seat), so a limit is never a promise the extension cannot keep.
Every change writes an audit row. Archiving someone or moving them to another extension clears their
roles (`services.users._clear_special_roles`), which frees the seat.
"""
from collections import Counter
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q

from apps.audit.services import log_action
from apps.core.services import ServiceError
from apps.core.text import title_case

from ..models import (
    Extension, ExtensionSpecialRoleLimit, PersonType, Role, SpecialRole, Status, User, UserSpecialRole,
)
from ..permissions import (
    require_special_role_assigner, require_special_role_limit_manager, require_user_manager,
)

#: Short labels shown as tags on the people screens. A role not listed here falls back to "<name> member".
TAG_LABELS = {"tithes_offering": "Tithes member", "attendance": "Attendance member"}


# ------------------------------------------------------------------ reading
def member_counts(extension_ids=None):
    """{extension_id: members who can sign in}. This is the most a special-role limit may be.

    Only members with an account count (admins, coordinators, guests and no-account members cannot hold
    a special role), and not archived or suspended ones, because `assign_special_role` refuses them.
    """
    people = (
        User.objects.filter(
            role=Role.MEMBER, person_type=PersonType.MEMBER_ACCOUNT, extension_id__isnull=False,
        )
        .exclude(status__in=(Status.ARCHIVED, Status.SUSPENDED))
    )
    if extension_ids is not None:
        people = people.filter(extension_id__in=list(extension_ids))
    return dict(people.order_by().values_list("extension_id").annotate(n=Count("pk")))


def special_role_rows(person):
    """One dict per special role for a member's page: code, name, held, limit (None = not set), used."""
    limits = dict(
        ExtensionSpecialRoleLimit.objects.filter(extension_id=person.extension_id)
        .values_list("role_id", "max_count")
    )
    used = Counter(
        UserSpecialRole.objects.filter(extension_id=person.extension_id).values_list("role_id", flat=True)
    )
    held = set(UserSpecialRole.objects.filter(user=person).values_list("role_id", flat=True))
    return [
        {
            "code": role.code, "name": role.name, "held": role.code in held,
            "limit": limits.get(role.code), "used": used.get(role.code, 0),
        }
        for role in SpecialRole.objects.order_by("name")
    ]


def limit_extensions(search=""):
    """Active extensions for the limits screen, filtered by a search text and ordered by name.

    Every word of the search must match the name, province or municipality (stored lowercase,
    so the match ignores case). Returns a queryset so the view can paginate it.
    """
    extensions = Extension.objects.filter(archived_at__isnull=True)
    for word in (search or "").split():
        extensions = extensions.filter(
            Q(name__icontains=word) | Q(province__icontains=word) | Q(municipality__icontains=word)
        )
    return extensions.order_by("name", "pk")


def limit_rows(extensions, roles):
    """Rows for the limits screen: per extension its member count and, per role, limit and use."""
    extensions = list(extensions)
    ids = [extension.pk for extension in extensions]
    limits = {
        (ext_id, role_id): count
        for ext_id, role_id, count in ExtensionSpecialRoleLimit.objects.filter(
            extension_id__in=ids).values_list("extension_id", "role_id", "max_count")
    }
    used = Counter(
        UserSpecialRole.objects.filter(extension_id__in=ids).values_list("extension_id", "role_id")
    )
    members = member_counts(ids)
    return [
        {
            "extension": extension,
            "members": members.get(extension.pk, 0),
            "cells": [
                {"role": role, "limit": limits.get((extension.pk, role.code)),
                 "used": used.get((extension.pk, role.code), 0)}
                for role in roles
            ],
        }
        for extension in extensions
    ]


def attach_special_roles(people):
    """Give each person in `people` a `special_role_codes` list, with ONE query for the whole page.

    The list and detail screens call this, then show the tags with `{% special_role_badges person %}`.
    Returns the same people (as a list) so it can wrap a page's object_list.
    """
    people = list(people)
    held = {}
    for user_id, role_id in UserSpecialRole.objects.filter(
            user_id__in=[p.pk for p in people]).order_by("role_id").values_list("user_id", "role_id"):
        held.setdefault(user_id, []).append(role_id)
    for person in people:
        person.special_role_codes = held.get(person.pk, [])
    return people


def special_role_labels(codes):
    """Tag texts for a list of role codes, for example ['Tithes member', 'Attendance member']."""
    names = dict(SpecialRole.objects.values_list("code", "name"))
    return [TAG_LABELS.get(code) or f"{names.get(code, code)} member" for code in codes]


def filter_by_special_role(people, role_code):
    """Narrow a people queryset to those holding `role_code` (no change when it is empty).

    Uses a subquery, so it does not depend on the reverse relation name of the model.
    """
    if not role_code:
        return people
    return people.filter(
        Exists(UserSpecialRole.objects.filter(user=OuterRef("pk"), role_id=role_code))
    )


# ----------------------------------------------------------------- giving
def assign_special_role(actor, request, person, role_code):
    """Give `person` a special role. Returns True if given, False if they already had it."""
    require_user_manager(actor)
    if (
        person.role != Role.MEMBER or person.person_type != PersonType.MEMBER_ACCOUNT
        or person.extension_id is None
    ):
        raise ServiceError("Only members of an extension can hold a special role.")
    require_special_role_assigner(actor, person.extension_id)
    with transaction.atomic():
        extension = Extension.objects.select_for_update().get(pk=person.extension_id)
        locked = User.objects.select_for_update().get(pk=person.pk)
        if locked.extension_id != extension.pk or locked.role != Role.MEMBER:
            raise ServiceError("This person was moved. Reload the page and try again.")
        if locked.person_type != PersonType.MEMBER_ACCOUNT:
            raise ServiceError("Only members with an account can hold a special role.")
        if locked.status in (Status.ARCHIVED, Status.SUSPENDED):
            raise ServiceError("Only members who can sign in can hold a special role.")
        role = SpecialRole.objects.filter(pk=role_code).first()
        if role is None:
            raise ServiceError("That special role does not exist.")
        if UserSpecialRole.objects.filter(user=locked, role=role).exists():
            return False
        limit = ExtensionSpecialRoleLimit.objects.filter(extension=extension, role=role).first()
        if limit is None:
            raise ServiceError(
                f"No limit is set for {role.name} in {title_case(extension.name)} yet, "
                "so it cannot be given. Ask an admin to set one."
            )
        used = UserSpecialRole.objects.filter(extension=extension, role=role).count()
        if used >= limit.max_count:
            raise ServiceError(
                f"{title_case(extension.name)} already has {used} of {limit.max_count} for {role.name}. "
                "Remove someone first, or ask an admin to raise the limit."
            )
        UserSpecialRole.objects.create(user=locked, role=role, extension=extension, granted_by=actor)
        log_action(
            "special_role.assign", actor=actor, request=request,
            entity_type="app_user", entity_id=locked.pk, entity_label=str(locked),
            extension_id=extension.pk, after={"role": role.code},
        )
    return True


def remove_special_role(actor, request, person, role_code):
    """Take a special role from `person`. Returns True if removed, False if they did not hold it."""
    require_user_manager(actor)
    require_special_role_assigner(actor, person.extension_id)
    with transaction.atomic():
        removed, _ = UserSpecialRole.objects.filter(user=person, role_id=role_code).delete()
        if not removed:
            return False
        log_action(
            "special_role.remove", actor=actor, request=request,
            entity_type="app_user", entity_id=person.pk, entity_label=str(person),
            extension_id=person.extension_id, before={"role": role_code},
        )
    return True


# ------------------------------------------------------------------ limits
def set_special_role_limits(actor, request, extension_pk, values):
    """Set or clear one extension's limits. `values` is {role_code: int, or None to clear}.

    All or nothing: if any value is refused, none is saved. Returns {role_code: new limit or None}
    for what actually changed.
    """
    require_user_manager(actor)
    require_special_role_limit_manager(actor)
    changed = {}
    with transaction.atomic():
        extension = Extension.objects.select_for_update().filter(
            pk=extension_pk, archived_at__isnull=True).first()
        if extension is None:
            raise ServiceError("That extension no longer exists.")
        members = member_counts([extension.pk]).get(extension.pk, 0)
        for code, value in values.items():
            role = SpecialRole.objects.filter(pk=code).first()
            if role is None:
                continue
            used = UserSpecialRole.objects.filter(extension=extension, role=role).count()
            existing = ExtensionSpecialRoleLimit.objects.filter(extension=extension, role=role).first()
            if value is None:
                if existing is None:
                    continue
                if used:
                    raise ServiceError(
                        f"{role.name}: {used} {'person holds' if used == 1 else 'people hold'} it, "
                        "so the limit cannot be cleared. Remove them first."
                    )
                existing.delete()
                changed[code] = None
                continue
            if value < 1:
                raise ServiceError(f"{role.name}: the limit must be at least 1, or leave it empty for none.")
            if value < used:
                raise ServiceError(
                    f"{role.name}: {used} {'person already holds' if used == 1 else 'people already hold'} it. "
                    f"Set the limit to at least {used}, or remove some first."
                )
            # An unchanged value is always fine, even if members left since it was set.
            if value > members and not (existing and existing.max_count == value):
                if members == 0:
                    raise ServiceError(
                        f"{role.name}: {title_case(extension.name)} has no members who can hold it yet."
                    )
                raise ServiceError(
                    f"{role.name}: {title_case(extension.name)} has only {members} "
                    f"{'member' if members == 1 else 'members'}, so the limit cannot be higher than {members}."
                )
            if existing is None:
                ExtensionSpecialRoleLimit.objects.create(extension=extension, role=role, max_count=value)
            elif existing.max_count != value:
                existing.max_count = value
                existing.save(update_fields=["max_count"])
            else:
                continue
            changed[code] = value
        if changed:
            log_action(
                "special_role.limit", actor=actor, request=request,
                entity_type="extension", entity_id=extension.pk, entity_label=title_case(extension.name),
                extension_id=extension.pk, after={"limits": changed},
            )
    return changed


@dataclass
class LimitBulkResult:
    """What a bulk limit change did: names changed, how many were already right, and what was skipped."""
    done: list = field(default_factory=list)       # extension names whose limit changed
    unchanged: int = 0                              # extensions that already had this value
    skipped: list = field(default_factory=list)    # (extension name, reason)


def bulk_set_special_role_limits(actor, request, role_code, value, extension_ids=None):
    """Set one role's limit to `value` in many extensions (`extension_ids=None` means every active one).

    Each extension runs in its own transaction, in ascending id order, through
    `set_special_role_limits`, so every rule applies per extension: one that cannot take the value
    (fewer members than `value`, or more holders than `value`) is skipped with its reason and never
    stops the rest. One audit row per extension that changed. Safe to repeat: a repeat changes nothing.
    """
    require_user_manager(actor)
    require_special_role_limit_manager(actor)
    role = SpecialRole.objects.filter(pk=role_code).first()
    if role is None:
        raise ServiceError("That special role does not exist.")
    if value is None or value < 1:
        raise ServiceError("Enter a limit of at least 1.")
    targets = Extension.objects.filter(archived_at__isnull=True)
    if extension_ids is not None:
        targets = targets.filter(pk__in=list(extension_ids))
    result = LimitBulkResult()
    for pk, name in targets.order_by("pk").values_list("pk", "name"):
        try:
            changed = set_special_role_limits(actor, request, pk, {role.code: value})
        except ServiceError as exc:
            result.skipped.append((title_case(name), str(exc)))
            continue
        if changed:
            result.done.append(title_case(name))
        else:
            result.unchanged += 1
    return result
