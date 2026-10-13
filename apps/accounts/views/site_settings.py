"""accounts.views.site_settings: the super-admin "Site settings" shell.

One page, one menu, one section at a time (`?section=<key>`). The sections come from the registry in
`accounts/site_sections.py`; each owns its template, its context and its own POST views. This view
only picks the section and renders the shell, so a new function never changes it.
"""
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from ..decorators import role_required
from ..models import Role
from ..permissions import require_site_settings_manager
from ..site_sections import all_sections, get_section


@role_required(Role.SUPER_ADMIN)
@never_cache
@require_http_methods(["GET"])
def site_settings(request):
    """Show the menu of sections and the chosen one (the first when `section` is missing or unknown)."""
    require_site_settings_manager(request.user)
    sections = all_sections()
    current = get_section(request.GET.get("section", "")) or sections[0]
    return render(request, "accounts/superadmin/site_settings.html", {
        "sections": sections,
        "current": current,
        **current.context(request),
    })
