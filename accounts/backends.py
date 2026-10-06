"""accounts.backends: checks who is logging in.

Django calls the backends listed in settings.AUTHENTICATION_BACKENDS whenever
someone runs `authenticate(...)`. This backend lets a person identify
themselves with EITHER their account id OR a verified email address.

It only answers "is this identifier + password a valid, usable account?".
It knows nothing about pages, messages, redirects or audit logging; that is
the job of `accounts/services.py` and `accounts/views.py`.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend


def find_user(identifier):
    """Return the User matching `identifier`, or None if there is none.

    - Contains "@"  -> treated as an email. Only a VERIFIED email counts
      (email_verified_at is set); `pending_email` is never used for login.
      The match is case-insensitive.
    - Otherwise     -> treated as an account id (exact match).

    This does NOT check the password or the account status. The service layer
    also uses it after a failed login to work out the reason for the audit log.
    """
    User = get_user_model()
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    if "@" in identifier:
        return User.objects.filter(
            email__iexact=identifier, email_verified_at__isnull=False
        ).first()
    return User.objects.filter(account_id=identifier).first()


class AccountIdOrEmailBackend(BaseBackend):
    """Authenticate with account id or verified email plus password."""

    def authenticate(self, request, identifier=None, password=None, username=None, **kwargs):
        """Return the User if the credentials are valid, otherwise None.

        `username` is accepted too because Django's own admin login form
        passes the field under that name. Without it, /django-admin/ login
        would stop working for the super-admin.
        """
        identifier = identifier or username
        if identifier is None or password is None:
            return None

        user = find_user(identifier)
        if user is None:
            # Hash a dummy password so an unknown account takes about as long
            # as a wrong password. This stops response time from revealing
            # which account ids exist.
            get_user_model()().set_password(password)
            return None

        # Archived accounts are denied (User.is_active is False for them).
        # The password is checked first so timing stays the same either way.
        if user.check_password(password) and user.is_active:
            return user
        return None

    def get_user(self, user_id):
        """Load the user for an existing session (called on every request).

        Returning None for an archived user logs them out immediately, even
        if they still had a valid session when they were archived.
        """
        User = get_user_model()
        user = User.objects.filter(pk=user_id).first()
        return user if user is not None and user.is_active else None