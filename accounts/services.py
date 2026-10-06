"""accounts.services: business logic for the accounts app (login so far).

Views stay thin and call functions from here. Per AGENTS.md, service functions
wrap their work in `transaction.atomic()` and call `log_action()` inside the
same transaction.
"""
from dataclasses import dataclass

from django.contrib.auth import authenticate, login
from django.db import transaction

from audit.models import AuditLog
from audit.services import log_action

from .backends import find_user
from .models import Status


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