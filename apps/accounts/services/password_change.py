"""accounts.services.password_change: finish a person's forced password change.

A person has to change their password when `must_change_password = True`. It is set when a leader
creates the account, when a password is reset and (brick 4) when a registration is approved.
Status plays no part: a reset leaves the person `active`.

The form (`SetNewPasswordForm`) checks the SHAPE of the new password; this module holds the rule
and the write.
"""
from dataclasses import dataclass

from django.contrib.auth import update_session_auth_hash
from django.db import transaction

from apps.audit.services import log_action

from ..models import PersonType, Status, User


@dataclass(frozen=True)
class PasswordChangeResult:
    """What `complete_password_change` did.

    changed -- True when the password was set. False means there was nothing
               to do (already changed in another tab or by a double click,
               or the person is suspended, archived or cannot sign in).
    """

    changed: bool


def user_needs_password_change(user):
    """True if this person still has to set their own password."""
    return bool(user.must_change_password)


def complete_password_change(request, user, new_password):
    """Set `new_password` for `user` and clear `must_change_password`.

    Safe to call twice: the second call sees it is already done, changes nothing and returns
    `PasswordChangeResult(changed=False)`.

    The row is locked first, so two simultaneous submits cannot both pass. The password never
    reaches the audit log. The flag is logged as `needs_change`, NOT `needs_password_change`:
    `log_action` drops any key that contains "password", so that name would silently vanish.
    """
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)

        # Suspended and archived people are blocked from signing in, and only a member with an
        # account has a password to change.
        if locked.status in (Status.SUSPENDED, Status.ARCHIVED):
            return PasswordChangeResult(changed=False)
        if locked.person_type != PersonType.MEMBER_ACCOUNT:
            return PasswordChangeResult(changed=False)
        if not user_needs_password_change(locked):
            return PasswordChangeResult(changed=False)

        locked.set_password(new_password)
        locked.must_change_password = False
        locked.save(update_fields=["password", "must_change_password", "updated_at"])

        log_action(
            "account.password_set",
            actor=locked,
            request=request,
            entity_type="user",
            entity_id=locked.pk,
            entity_label=str(locked),
            before={"needs_change": True},
            after={"needs_change": False},
        )

    # Changing the password changes the session hash, which would log the
    # person out of this very request. Re-sign the session with the new hash.
    update_session_auth_hash(request, locked)
    return PasswordChangeResult(changed=True)
