"""accounts.middleware: keep people who still must activate on the activation page.

Login already sends them to /accounts/activate/, but nothing stopped them from
typing another address afterwards. This middleware closes that gap: while
`user_needs_activation(user)` is true, every page except the activation page
and logout redirects to the activation page.

Place it AFTER AuthenticationMiddleware and MessageMiddleware in MIDDLEWARE.
"""
from django.shortcuts import redirect
from django.urls import reverse

from .services.activation import user_needs_activation


class ActivationRequiredMiddleware:
    """Redirect signed-in people who must activate, except on the allowed pages."""

    #: URL names a person who still must activate may open.
    ALLOWED_URL_NAMES = ("accounts:activate", "accounts:logout")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and user_needs_activation(user)
            and request.path not in self._allowed_paths()
        ):
            return redirect("accounts:activate")
        return self.get_response(request)

    def _allowed_paths(self):
        """Resolved at request time, so URL changes never leave a stale copy."""
        return {reverse(name) for name in self.ALLOWED_URL_NAMES}