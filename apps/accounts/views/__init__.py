"""accounts.views: login, logout and the forced password change.

Views stay thin: read the request, call a form and a service, then render a
page or redirect. Business rules live in `accounts/services.py`. The role
guard and the role home URLs live in `accounts/decorators.py` (one copy only).
"""
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from ..decorators import home_url_for
from ..forms import GENERIC_LOGIN_ERROR, LoginForm, SetNewPasswordForm
from ..services import attempt_login
from ..services.password_change import complete_password_change, user_needs_password_change


def post_login_redirect(request, user, needs_password_change):
    """Decide where to send someone who is now logged in.

    Order of priority:
      1. Must change password -> the change-password page.
      2. A safe `next` URL (the page they were trying to open).
      3. Their role's home page.
    """
    if needs_password_change:
        return reverse("accounts:change_password")

    next_url = request.POST.get("next") or request.GET.get("next") or ""
    # Only follow `next` if it stays on this site. Otherwise an attacker could
    # craft a login link that bounces the user to a fake site (open redirect).
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url

    return home_url_for(user)


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request):
    """Show the login form (GET) and process it (POST)."""
    # Already logged in: skip the form. The password-change gate is PasswordChangeRequiredMiddleware.
    if request.user.is_authenticated:
        return redirect(home_url_for(request.user))

    form = LoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        result = attempt_login(
            request,
            form.cleaned_data["identifier"],
            form.cleaned_data["password"],
        )
        if result.ok:
            return redirect(
                post_login_redirect(request, result.user, result.needs_password_change)
            )
        # Same message whatever went wrong (see forms.GENERIC_LOGIN_ERROR).
        form.add_error(None, GENERIC_LOGIN_ERROR)

    # A badly shaped identifier fails form validation with the same generic
    # message, so the template shows one error box (field + non-field errors).
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    return render(
        request,
        "accounts/login.html",
        {"form": form, "next": next_url},
    )


@require_POST
def logout_view(request):
    """Log out. POST only: Django 5+ rejects GET so a stray link can't log you out."""
    logout(request)
    return redirect("accounts:login")


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def change_password_view(request):
    """Let someone with a temporary password choose their own (new password, confirm).

    Login and PasswordChangeRequiredMiddleware send people with `must_change_password` here.
    Anyone else goes to their home page.
    """
    user = request.user
    if not user_needs_password_change(user):
        return redirect(home_url_for(user))

    form = SetNewPasswordForm(request.POST or None, user=user)
    if request.method == "POST" and form.is_valid():
        result = complete_password_change(request, user, form.cleaned_data["new_password"])
        if result.changed:
            messages.success(request, "Your password was changed. Welcome!")
        return redirect(home_url_for(request.user))

    return render(request, "accounts/change_password.html", {"form": form})
