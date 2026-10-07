"""accounts.views: login, logout and the activation placeholder.

Views stay thin: read the request, call a form and a service, then render a
page or redirect. Business rules live in `accounts/services.py`.
"""
from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
from functools import wraps

from .forms import GENERIC_LOGIN_ERROR, LoginForm
from .models import Role
from .services import attempt_login

from .models import Role

ROLE_HOME_NAMES = {
    Role.SUPER_ADMIN: "dashboards:superadmin",
    Role.ADMIN: "dashboards:admin",
    Role.COORDINATOR: "dashboards:coordinator",
    Role.MEMBER: "dashboards:member",
}

def home_url_for(user):
    """Return the landing URL for this user's role."""
    name = ROLE_HOME_NAMES.get(user.role)
    return reverse(name) if name else settings.LOGIN_REDIRECT_URL


def role_required(*roles):
    """Allow only these roles; anyone else goes to their own home page."""
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.user.role not in roles:
                return redirect(home_url_for(request.user))
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def post_login_redirect(request, user, needs_activation):
    """Decide where to send someone who is now logged in.

    Order of priority:
      1. Not activated / must change password -> activation page.
      2. A safe `next` URL (the page they were trying to open).
      3. Their role's home page.
    """
    if needs_activation:
        return reverse("accounts:activate")

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
    # Already logged in: skip the form. Activation is not re-checked here;
    # the activation gate belongs in middleware or role_required later.
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
                post_login_redirect(request, result.user, result.needs_activation)
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
def activate_view(request):
    """PLACEHOLDER for the activation flow (set your own password).

    Login sends not-activated and must-change-password users here. Replace
    this view with the real activation form in the next task.
    """
    return render(request, "accounts/activate_placeholder.html")