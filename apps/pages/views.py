"""Views for the public pages app."""
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from apps.accounts.decorators import home_url_for, role_required
from apps.accounts.models import Role
from apps.pages.services import get_about_context, get_give_context, get_landing_context


@require_GET
def landing_page(request):
    """Public landing page at "/". All content and defaults come from the service."""
    if request.user.is_authenticated:
        return redirect(home_url_for(request.user))
    return render(request, "pages/landing.html", get_landing_context())


# The root URL and AGENTS.md refer to this name; keep both names pointing at one view.
landing_view = landing_page


@require_GET
def give_view(request):
    """Public giving page at /give/: instructions and accounts. No form, no amount, no database write."""
    return render(request, "pages/give.html", get_give_context())


@require_GET
@role_required(Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER)
def about_view(request):
    """In-app About page at /about/ for every signed-in role (mission, vision, core values)."""
    return render(request, "pages/about.html", get_about_context())
