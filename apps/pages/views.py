"""Views for the public pages app."""
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from apps.accounts.decorators import home_url_for, role_required
from apps.accounts.models import Role
from apps.pages.services import get_about_context, get_give_context, get_landing_context


def _is_site_manager(user):
    """True for super-admin and admin: they may look at the public pages while signed in."""
    return user.is_authenticated and getattr(user, "role", None) in (Role.SUPER_ADMIN, Role.ADMIN)


def _viewer_context(request):
    """Extra template context for signed-in visitors of the public pages.

    `site_manager` shows the admin bar; `app_home_url` is where "Open app" goes
    (the navbar used "/", which only worked because "/" redirected signed-in users).
    """
    user = request.user
    return {
        "site_manager": _is_site_manager(user),
        "app_home_url": home_url_for(user) if user.is_authenticated else "",
    }


@require_GET
def landing_page(request):
    """Public landing page at "/". All content and defaults come from the service.

    Members and coordinators still go straight to their dashboard; super-admin and
    admin stay here so they can check the page without logging out.
    """
    if request.user.is_authenticated and not _is_site_manager(request.user):
        return redirect(home_url_for(request.user))
    return render(request, "pages/landing.html", {**get_landing_context(), **_viewer_context(request)})


# The root URL and AGENTS.md refer to this name; keep both names pointing at one view.
landing_view = landing_page


@require_GET
def give_view(request):
    """Public giving page at /give/: instructions and accounts. No form, no amount, no database write."""
    return render(request, "pages/give.html", {**get_give_context(), **_viewer_context(request)})


@require_GET
@role_required(Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER)
def about_view(request):
    """In-app About page at /about/ for every signed-in role (mission, vision, core values)."""
    return render(request, "pages/about.html", get_about_context())