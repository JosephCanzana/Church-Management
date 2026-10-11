"""bible.views_superadmin: the super-admin "Site settings" screen for the Bible.

Every view is wrapped in role_required(SUPER_ADMIN) (the page guard); the
functions in site_settings.py check permission again (the action guard). Views
stay thin: read the request, call a service, show a toast, redirect.

A toggle form posts the state it WANTS (enabled=1 or 0), never "flip it", so a
double click or a stale page cannot turn a switch the wrong way.
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.core.services import ServiceError

from . import services
from .site_settings import set_online_services_enabled, set_version_enabled
from .switches import read_switches
from .versions import VERSIONS_BY_CODE

super_admin_only = role_required(Role.SUPER_ADMIN)

BACK = "bible_superadmin:site_settings"


def _wanted_state(request):
    """True/False from the posted ``enabled`` field, or None when it is missing or odd."""
    return {"1": True, "0": False}.get(request.POST.get("enabled"))


def _report(request, name, enabled, changed):
    """Toast for the result of one toggle."""
    word = "on" if enabled else "off"
    if changed:
        messages.success(request, f"{name} is now turned {word}.")
    else:
        messages.info(request, f"{name} was already turned {word}.")


@super_admin_only
@require_http_methods(["GET"])
def site_settings(request):
    """Show the master switch and one switch per Bible version."""
    state = read_switches()
    cards = []
    for status in services.version_statuses():
        on = state.versions[status.version.code]
        cards.append({
            "version": status.version,
            "on": on,
            # Switched on but still unreadable (no key, a limit, a problem at ESV).
            "locked_reason": status.reason if on else "",
        })
    return render(request, "bible/superadmin/site_settings.html", {
        "cards": cards,
        "online_on": state.master,
    })


@super_admin_only
@require_POST
def version_toggle(request, code):
    """Turn one Bible version on or off."""
    enabled = _wanted_state(request)
    version = VERSIONS_BY_CODE.get(code)
    if enabled is None or version is None:
        messages.error(request, "That request was not understood. Reload the page and try again.")
        return redirect(BACK)
    try:
        changed = set_version_enabled(request.user, code, enabled, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
        return redirect(BACK)
    _report(request, version.label, enabled, changed)
    return redirect(BACK)


@super_admin_only
@require_POST
def online_toggle(request):
    """Turn every online Bible service on or off."""
    enabled = _wanted_state(request)
    if enabled is None:
        messages.error(request, "That request was not understood. Reload the page and try again.")
        return redirect(BACK)
    try:
        changed = set_online_services_enabled(request.user, enabled, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
        return redirect(BACK)
    _report(request, "Online Bible services", enabled, changed)
    return redirect(BACK)
