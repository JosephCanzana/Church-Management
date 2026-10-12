"""dashboards.views: one thin view per role; the work is done in services.py.

Each view is protected with ``role_required`` (hiding a link is not access control).
A person who opens another role's dashboard is sent to their own home page.
"""
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from apps.accounts.decorators import role_required
from apps.accounts.models import Role

from .services import VERSION_COOKIE, get_dashboard_context


def _render(request, template):
    """Render a role's dashboard template with the shared dashboard context."""
    dashboard = get_dashboard_context(request.user, request.COOKIES.get(VERSION_COOKIE), request.path)
    return render(request, template, {"dashboard": dashboard})


@never_cache
@role_required(Role.SUPER_ADMIN)
def superadmin_home(request):
    """Super-admin dashboard."""
    return _render(request, "dashboards/superadmin.html")


@never_cache
@role_required(Role.ADMIN)
def admin_home(request):
    """Admin dashboard."""
    return _render(request, "dashboards/admin.html")


@never_cache
@role_required(Role.COORDINATOR)
def coordinator_home(request):
    """Coordinator dashboard (numbers for their own extension)."""
    return _render(request, "dashboards/coordinator.html")


@never_cache
@role_required(Role.MEMBER)
def member_home(request):
    """Member dashboard."""
    return _render(request, "dashboards/member.html")
