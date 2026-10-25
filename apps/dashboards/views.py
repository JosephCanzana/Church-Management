"""dashboards.views: one thin view per role; the work is done in services.py.

Each view is protected with ``role_required`` (hiding a link is not access control).
A person who opens another role's dashboard is sent to their own home page.

The extension filter (``?extension=<id>``) is read here for the super-admin and the admin ONLY. The raw text goes to
the services, which validate it (a bad value means "All extensions"). The coordinator and member views never read it,
so a forged ``?extension=`` cannot move a coordinator out of their own extension.
"""
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from apps.accounts.decorators import role_required
from apps.accounts.models import Role

from .services import VERSION_COOKIE, get_dashboard_context


def _render(request, template, use_filter=False):
    """Render a role's dashboard template with the shared dashboard context.

    ``use_filter`` is True only for the super-admin and admin views: then ``?extension=`` is passed on as raw text.
    """
    extension = request.GET.get("extension") if use_filter else None
    dashboard = get_dashboard_context(
        request.user, request.COOKIES.get(VERSION_COOKIE), request.path, extension=extension
    )
    return render(request, template, {"dashboard": dashboard})


@never_cache
@role_required(Role.SUPER_ADMIN)
def superadmin_home(request):
    """Super-admin dashboard (with the extension picker)."""
    return _render(request, "dashboards/superadmin.html", use_filter=True)


@never_cache
@role_required(Role.ADMIN)
def admin_home(request):
    """Admin dashboard (with the extension picker)."""
    return _render(request, "dashboards/admin.html", use_filter=True)


@never_cache
@role_required(Role.COORDINATOR)
def coordinator_home(request):
    """Coordinator dashboard (numbers for their own extension; no filter is read)."""
    return _render(request, "dashboards/coordinator.html")


@never_cache
@role_required(Role.MEMBER)
def member_home(request):
    """Member dashboard."""
    return _render(request, "dashboards/member.html")
