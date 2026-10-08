"""accounts.services.activation: finish a person's activation.

A person has to activate when they were given a temporary password:
  - `status = not_activated` (a new account, or one that was deactivated), or
  - `must_change_password = True` (an account whose password was reset;
    its status stays `active`).
Activating means choosing their own password. That clears
`must_change_password` and, for a not-activated account, makes it `active`.

The form (`ActivationForm`) checks the SHAPE of the new password; this module
holds the rule and the write.
"""
from dataclasses import dataclass

from django.contrib.auth import update_session_auth_hash
from django.db import transaction

from apps.audit.services import log_action

from ..models import Status, User


@dataclass(frozen=True)
class ActivationResult:
    """What `activate_account` did.

    changed -- True when the password was set. False means there was nothing
               to do (already activated in another tab or by a double click,
               or the account is suspended or archived).
    """

    changed: bool


def user_needs_activation(user):
    """True if this person still has to set their own password."""
    return user.status == Status.NOT_ACTIVATED or user.must_change_password


def activate_account(request, user, new_password):
    """Set `new_password` for `user` and finish their activation.

    Safe to call twice: the second call sees the account is already done,
    changes nothing and returns `ActivationResult(changed=False)`.

    The row is locked first, so two simultaneous submits cannot both pass.
    The password never reaches the audit log (it only records the status and
    a `needs_activation` flag; a key containing "password" would be stripped
    by `log_action` anyway).
    """
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)

        # Suspended and archived people are blocked from logging in; they are
        # not activated from here.
        if locked.status in (Status.SUSPENDED, Status.ARCHIVED):
            return ActivationResult(changed=False)
        if not user_needs_activation(locked):
            return ActivationResult(changed=False)

        before = {"status": locked.status, "needs_activation": True}

        locked.set_password(new_password)
        locked.must_change_password = False
        if locked.status == Status.NOT_ACTIVATED:
            locked.status = Status.ACTIVE
        locked.save()

        log_action(
            "account.activated",
            actor=locked,
            request=request,
            entity_type="user",
            entity_id=locked.pk,
            entity_label=str(locked),
            before=before,
            after={"status": locked.status, "needs_activation": False},
        )

    # Changing the password changes the session hash, which would log the
    # person out of this very request. Re-sign the session with the new hash.
    update_session_auth_hash(request, locked)
    return ActivationResult(changed=True)