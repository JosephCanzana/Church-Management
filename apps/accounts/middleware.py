"""accounts.middleware: keep people who must change their password on the change-password page.

Login already sends them to /accounts/change-password/, but nothing stopped them from
typing another address afterwards. This middleware closes that gap: while
`user_needs_password_change(user)` is true, every page except the change-password page
and logout redirects to the change-password page.

Place it AFTER AuthenticationMiddleware and MessageMiddleware in MIDDLEWARE.
"""
from django.shortcuts import redirect
from django.urls import reverse

from .services.password_change import user_needs_password_change


class PasswordChangeRequiredMiddleware:
    """Redirect signed-in people who must change their password, except on the allowed pages."""

    #: URL names a person who still must change their password may open.
    ALLOWED_URL_NAMES = ("accounts:change_password", "accounts:logout")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and user_needs_password_change(user)
            and request.path not in self._allowed_paths()
        ):
            return redirect("accounts:change_password")
        return self.get_response(request)

    def _allowed_paths(self):
        """Resolved at request time, so URL changes never leave a stale copy."""
        return {reverse(name) for name in self.ALLOWED_URL_NAMES}
