"""accounts.services.special_roles: the two special member roles and their per-extension limits.

Special roles (tithes_offering, attendance) let a member help the coordinator. A coordinator gives
and takes them directly for the members of their own extension; the super-admin and admins may do it
in any extension. HOW MANY people may hold a role in one extension is a limit that only the
super-admin and admins set (`set_special_role_limits`). With no limit set for a role in an extension,
nobody there can be given that role yet.

Rules the service enforces, whoever clicked:
- only a member of an extension can hold a role, and not an archived or suspended one;
- giving a role locks the extension row, so two clicks can never both take the last seat;
- a limit cannot be set below the number of people who already hold the role, and cannot be
  cleared while anyone holds it.
Every change writes an audit row. Archiving someone or moving them to another extension clears their
roles (`services.users._clear_special_roles`), which frees the seat.
"""
from collections import Counter

from django.db import transaction

from apps.audit.services import log_action
from apps.core.services import ServiceError
from apps.core.text import title_case

from ..models import (
    Extension, ExtensionSpecialRoleLimit, Role, SpecialRole, Status, User, UserSpecialRole,
)
from ..permissions import (
    require_special_role_assigner, require_special_role_limit_manager, require_user_manager,
)


# ------------------------------------------------------------------ reading
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


def limit_overview():
    """(roles, rows) for the limits screen: every active extension, and per role its limit and use."""
    roles = list(SpecialRole.objects.order_by("name"))
    limits = {
        (ext_id, role_id): count
        for ext_id, role_id, count in ExtensionSpecialRoleLimit.objects.values_list(
            "extension_id", "role_id", "max_count")
    }
    used = Counter(UserSpecialRole.objects.values_list("extension_id", "role_id"))
    rows = []
    for extension in Extension.objects.filter(archived_at__isnull=True).order_by("name"):
        rows.append({
            "extension": extension,
            "cells": [
                {"role": role, "limit": limits.get((extension.pk, role.code)),
                 "used": used.get((extension.pk, role.code), 0)}
                for role in roles
            ],
        })
    return roles, rows


# ----------------------------------------------------------------- giving
def assign_special_role(actor, request, person, role_code):
    """Give `person` a special role. Returns True if given, False if they already had it."""
    require_user_manager(actor)
    if person.role != Role.MEMBER or person.extension_id is None:
        raise ServiceError("Only members of an extension can hold a special role.")
    require_special_role_assigner(actor, person.extension_id)
    with transaction.atomic():
        extension = Extension.objects.select_for_update().get(pk=person.extension_id)
        locked = User.objects.select_for_update().get(pk=person.pk)
        if locked.extension_id != extension.pk or locked.role != Role.MEMBER:
            raise ServiceError("This person was moved. Reload the page and try again.")
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
