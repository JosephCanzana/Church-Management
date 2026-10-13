"""bible.views_superadmin: the Bible switches inside the super-admin "Site settings" screen.

The screen itself (menu and layout) is `accounts.views.site_settings`; the Bible is one section of
it (see `site_section.py`). What stays here are the two POST views that change a switch.

Every view is wrapped in role_required(SUPER_ADMIN) (the page guard); the
functions in site_settings.py check permission again (the action guard). Views
stay thin: read the request, call a service, show a toast, redirect.

A toggle form posts the state it WANTS (enabled=1 or 0), never "flip it", so a
double click or a stale page cannot turn a switch the wrong way.
"""
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.core.services import ServiceError

from .site_settings import set_online_services_enabled, set_version_enabled
from .versions import VERSIONS_BY_CODE

super_admin_only = role_required(Role.SUPER_ADMIN)


def _back():
    """Back to the Bible section of Site settings."""
    return redirect(f"{reverse('bible_superadmin:site_settings')}?section=bible")


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
@require_POST
def version_toggle(request, code):
    """Turn one Bible version on or off."""
    enabled = _wanted_state(request)
    version = VERSIONS_BY_CODE.get(code)
    if enabled is None or version is None:
        messages.error(request, "That request was not understood. Reload the page and try again.")
        return _back()
    try:
        changed = set_version_enabled(request.user, code, enabled, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
        return _back()
    _report(request, version.label, enabled, changed)
    return _back()


@super_admin_only
@require_POST
def online_toggle(request):
    """Turn every online Bible service on or off."""
    enabled = _wanted_state(request)
    if enabled is None:
        messages.error(request, "That request was not understood. Reload the page and try again.")
        return _back()
    try:
        changed = set_online_services_enabled(request.user, enabled, request=request)
    except ServiceError as error:
        messages.error(request, str(error))
        return _back()
    _report(request, "Online Bible services", enabled, changed)
    return _back()
