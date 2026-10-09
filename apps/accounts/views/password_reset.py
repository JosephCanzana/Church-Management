"""accounts.views.password_reset: forgot password and the emailed reset link.

Public pages (no login). Views stay thin: validate with a form, call a service,
show the same answer whatever happened. Every page is `never_cache`. The reset
page also sends `Referrer-Policy: no-referrer`, so the token in its URL can
never leak to another site through a Referer header.
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.core.services import ServiceError

from ..decorators import home_url_for
from ..forms import ForgotPasswordForm, ResetPasswordForm
from ..services import password_reset as svc


def _no_referrer(response):
    """Stop the browser sending this page's URL (it holds the token) to other sites."""
    response["Referrer-Policy"] = "same-origin"
    return response


@never_cache
@require_http_methods(["GET", "POST"])
def forgot_password_view(request):
    """Ask for a reset link. The answer is identical for every input."""
    if request.user.is_authenticated:
        return redirect(home_url_for(request.user))
    form = ForgotPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        svc.request_password_reset(request, form.cleaned_data["identifier"])  # result ignored on purpose
        return redirect("accounts:forgot_password_done")
    return render(request, "accounts/forgot_password.html", {"form": form})


@never_cache
@require_http_methods(["GET"])
def forgot_password_done_view(request):
    """The one message shown after any request."""
    return render(request, "accounts/forgot_password_done.html", {"minutes": svc.link_minutes()})


@never_cache
@require_http_methods(["GET", "POST"])
def reset_password_view(request, token):
    """The page the emailed link opens: GET shows the form, POST sets the password.

    A GET never uses the token up (mail scanners open links). A bad, expired or
    used link always shows the same "link is no longer valid" page.
    """
    found = svc.get_valid_reset_token(token)
    if found is None:
        return _no_referrer(render(request, "accounts/reset_invalid.html", status=200))

    form = ResetPasswordForm(request.POST or None, user=found.user)
    if request.method == "POST" and form.is_valid():
        try:
            svc.reset_password(request, token, form.cleaned_data["new_password"])
        except ServiceError:
            return _no_referrer(render(request, "accounts/reset_invalid.html"))
        messages.success(request, "Your password was changed. Log in with your new password.")
        return _no_referrer(redirect("accounts:login"))
    return _no_referrer(render(request, "accounts/reset_password.html", {"form": form, "token": token}))
