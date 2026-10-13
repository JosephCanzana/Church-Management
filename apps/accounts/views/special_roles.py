"""accounts.views.special_roles: giving and taking special member roles, and the limits screen.

Giving and taking is done from a member's page by the coordinator of that extension (and by the
super-admin and admins anywhere); the services check the rules again.

The limits screen is for the super-admin and admins only. It lists extensions with a search box and
pages; each row has one number per special role (empty = no limit set, so nobody there can be given
that role yet), and the number cannot be higher than the extension's members. Tick rows and use the
bar to set one role to one value for the ticked extensions, or for every extension.
The super-admin reaches the same panel inside Site settings (section "special-roles"); the admin
reaches it from the "Special roles" button on the people list. Both post to `special_role_limits`.
"""
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.services import ServiceError

from ..decorators import role_required
from ..models import Role, SpecialRole
from ..services.special_roles import (
    assign_special_role, bulk_set_special_role_limits, limit_extensions, limit_rows,
    remove_special_role, set_special_role_limits,
)
from .users import _gone, _person_or_none, _to, people_manager

limit_manager = role_required(Role.SUPER_ADMIN, Role.ADMIN)

PAGE_SIZE = 25
MAX_LIMIT = 100000          # a sanity bound on typed numbers; the real cap is the member count
MAX_TICKED = 100            # extensions one "set selected" may touch
SECTION_KEY = "special-roles"


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


# ------------------------------------------------------------------ limits
def limits_context(request, section_key=""):
    """Template context for the limits panel: search, one page of rows, and where forms go back to.

    Used by the standalone page (admin) and by the Site settings section (super-admin), so both
    show exactly the same thing. `querystring` holds the search (and section) WITHOUT the page number.
    """
    search = request.GET.get("q", "").strip()[:100]
    paginator = Paginator(limit_extensions(search), PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    roles = list(SpecialRole.objects.order_by("name"))
    params = {}
    if section_key:
        params["section"] = section_key
    if search:
        params["q"] = search
    namespace = "superadmin" if section_key else request.resolver_match.namespace
    return {
        "roles": roles,
        "rows": limit_rows(page.object_list, roles),
        "page": page,
        "q": search,
        "total": paginator.count,
        "section_key": section_key,
        "querystring": urlencode(params),
        "next_url": request.get_full_path(),
        "search_action": request.path,
        "limits_action_url": reverse(f"{namespace}:special_role_limits"),
    }


def _whole_number(raw):
    """int from a typed whole number, or None when it is empty or not one."""
    raw = (raw or "").strip()
    if raw.isdigit() and int(raw) <= MAX_LIMIT:
        return int(raw)
    return None


def _back(request):
    """Redirect to where the form came from (`next`, same host only), else to the role's own limits page."""
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(target)
    return _to(request, "special_role_limits")


def _site_settings_url(request):
    """The Site settings section the super-admin uses instead of the standalone page."""
    params = {"section": SECTION_KEY}
    for key in ("q", "page"):
        if request.GET.get(key):
            params[key] = request.GET[key]
    return f"{reverse('bible_superadmin:site_settings')}?{urlencode(params)}"


def _save_one(request):
    """Save the typed limits of ONE extension (the row's own form)."""
    values, bad = {}, False
    for role in SpecialRole.objects.all():
        raw = request.POST.get(f"limit_{role.code}", "").strip()
        if not raw:
            values[role.code] = None
        elif _whole_number(raw) is None:
            bad = True
        else:
            values[role.code] = _whole_number(raw)
    extension_id = request.POST.get("extension", "")
    if bad or not extension_id.isdigit():
        messages.error(request, "Enter whole numbers for the limits, or leave one empty for none.")
        return
    try:
        changed = set_special_role_limits(request.user, request, int(extension_id), values)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Limits saved." if changed else "No changes to save.")


def _bulk_set(request, everyone):
    """Set one role to one value for the ticked extensions, or for all of them."""
    role = SpecialRole.objects.filter(pk=request.POST.get("role", "")).first()
    value = _whole_number(request.POST.get("value"))
    if role is None or value is None or value < 1:
        messages.error(request, "Choose a role and enter a whole number of at least 1.")
        return
    ids = None
    if not everyone:
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()][:MAX_TICKED]
        if not ids:
            messages.error(request, "Tick at least one extension first.")
            return
    try:
        result = bulk_set_special_role_limits(request.user, request, role.code, value, ids)
    except ServiceError as exc:
        messages.error(request, str(exc))
        return
    if result.done:
        count = len(result.done)
        messages.success(
            request, f"{role.name} limit set to {value} in {count} {'extension' if count == 1 else 'extensions'}.")
    elif not result.skipped:
        messages.info(request, f"Nothing to change: {role.name} was already {value} there.")
    if result.skipped:
        shown = "; ".join(f"{name} ({reason})" for name, reason in result.skipped[:3])
        more = len(result.skipped) - 3
        messages.warning(
            request,
            f"Skipped {len(result.skipped)}: {shown}" + (f" and {more} more." if more > 0 else "."),
        )


@limit_manager
@never_cache
@require_http_methods(["GET", "POST"])
def special_role_limits(request):
    """How many people each extension may have in each special role."""
    if request.method == "POST":
        action = request.POST.get("action", "save_one")
        if action == "set_selected":
            _bulk_set(request, everyone=False)
        elif action == "set_all":
            _bulk_set(request, everyone=True)
        else:
            _save_one(request)
        return _back(request)
    if request.user.role == Role.SUPER_ADMIN:
        return redirect(_site_settings_url(request))
    return render(request, "accounts/people/special_role_limits.html", limits_context(request))
