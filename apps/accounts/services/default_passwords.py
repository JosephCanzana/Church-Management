"""accounts.services.default_passwords: each person's own default passwords.

A default password belongs to the person who keeps it (the owner) and applies
to people of one role beneath them. When that owner creates or resets someone
and types nothing, the default for the person's role is used (see
`users._set_initial_password`). Only the hash is stored, so a default can never
be shown again; to see what it is, set it again.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.audit.services import log_action
from apps.core.services import ServiceError

from ..models import DefaultPassword
from ..permissions import default_password_roles
from ..validators import validate_strong_password


def save_default_passwords(actor, request, changes):
    """Apply {role: ("set", password) | ("clear", None)} to `actor`'s own defaults.

    Returns the roles that really changed (removing a default that was never
    set changes nothing). Everything happens in one transaction.
    """
    allowed = default_password_roles(actor)
    if not allowed:
        raise PermissionDenied("You do not keep default passwords.")
    changed = []
    with transaction.atomic():
        for role, (kind, password) in changes.items():
            if role not in allowed:
                raise PermissionDenied("You cannot set a default password for that role.")
            if kind == "set":
                try:
                    validate_strong_password(password)
                except ValidationError as exc:
                    raise ServiceError(exc.messages[0])
                row = (
                    DefaultPassword.objects.select_for_update()
                    .filter(owner=actor, applies_to_role=role).first()
                    or DefaultPassword(owner=actor, applies_to_role=role)
                )
                row.set_plain(password)      # writes the hash and the encrypted copy together
                row.save()
            else:
                deleted, _ = DefaultPassword.objects.filter(owner=actor, applies_to_role=role).delete()
                if not deleted:
                    continue
            changed.append(role)
            log_action(
                f"account.default_password_{kind}", actor=actor, request=request,
                entity_type="app_user", entity_id=actor.pk, entity_label=str(actor),
                after={"role": role},          # the password itself is never logged
            )
    return changed