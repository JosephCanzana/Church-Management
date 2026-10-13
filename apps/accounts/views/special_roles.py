"""accounts.views.special_roles: giving and taking special member roles, and the limits screen.

Giving and taking is done from a member's page by the coordinator of that extension (and by the
super-admin and admins anywhere); the services check the rules again. The limits screen is for the
super-admin and admins only: one form per extension, one number per special role (empty = no limit
set, so nobody there can be given that role yet). Redirects stay in the role's own area.
"""
from django.contrib import messages
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.services import ServiceError

from ..decorators import role_required
from ..models import Role, SpecialRole
from ..services.special_roles import (
    assign_special_role, limit_overview, remove_special_role, set_special_role_limits,
)
from .users import _gone, _person_or_none, _to, people_manager

limit_manager = role_required(Role.SUPER_ADMIN, Role.ADMIN)


def _special_role_action(request, pk, service, done, nothing):
    """Run give/take on one person and answer with a message."""
    person = _person_or_none(request.user, pk)
    if person is None:
        return _gone(request)
    role = SpecialRole.objects.filter(pk=request.POST.get("role", "")).first()
    if role is None:
        messages.error(request, "That special role does not exist.")
        return _to(request, "user_detail", pk=pk)
    try:
        changed = service(request.user, request, person, role.code)
    except ServiceError as exc:
        messages.error(request, str(exc))
        return _to(request, "user_detail", pk=pk)
    text = (done if changed else nothing).format(name=person.full_name, role=role.name)
    (messages.success if changed else messages.info)(request, text)
    return _to(request, "user_detail", pk=pk)


@people_manager
@require_POST
def special_role_assign(request, pk):
    return _special_role_action(request, pk, assign_special_role,
                                "{name} now has the {role} role.", "{name} already has the {role} role.")


@people_manager
@require_POST
def special_role_remove(request, pk):
    return _special_role_action(request, pk, remove_special_role,
                                "{name} no longer has the {role} role.", "{name} does not have the {role} role.")


@limit_manager
@never_cache
@require_http_methods(["GET", "POST"])
def special_role_limits(request):
    """How many people each extension may have in each special role (one form per extension)."""
    if request.method == "POST":
        values, bad = {}, False
        for role in SpecialRole.objects.all():
            raw = request.POST.get(f"limit_{role.code}", "").strip()
            if not raw:
                values[role.code] = None
            elif raw.isdigit():
                values[role.code] = int(raw)
            else:
                bad = True
        extension_id = request.POST.get("extension", "")
        if bad or not extension_id.isdigit():
            messages.error(request, "Enter whole numbers for the limits, or leave one empty for none.")
        else:
            try:
                changed = set_special_role_limits(request.user, request, int(extension_id), values)
            except ServiceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Limits saved." if changed else "No changes to save.")
        return _to(request, "special_role_limits")
    roles, rows = limit_overview()
    return render(request, "accounts/people/special_role_limits.html", {"roles": roles, "rows": rows})
