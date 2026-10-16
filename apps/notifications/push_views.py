"""notifications.push_views: the small JSON endpoints behind the Settings device card, plus /sw.js.

Thin views: each one parses the request, calls ONE service and answers. Every signed-in view is
`role_required` (all four roles) + `never_cache` + POST only, and the services check the owner again.
Kept in its own module so the existing `notifications/views.py` (bell, list) stays untouched.
"""
import json

from django.contrib import messages
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.core.services import ServiceError

from . import push, services

ALL_ROLES = (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER)
MAX_BODY_BYTES = 8192
TEST_COOLDOWN_SECONDS = 10
SETTINGS_FALLBACK = "/settings/"


def _json_body(request):
    """The request body as a dict, or a ServiceError with a plain message."""
    if len(request.body) > MAX_BODY_BYTES:
        raise ServiceError("That request was too large.")
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise ServiceError("That request could not be read.")
    if not isinstance(data, dict):
        raise ServiceError("That request could not be read.")
    return data


def _error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


@role_required(*ALL_ROLES)
@never_cache
@require_POST
def subscribe(request):
    """Save this browser as a device. The page reloads afterwards, so the result is a Django message (a toast)."""
    try:
        data = _json_body(request)
        keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
        subscription = services.register_device(
            request.user,
            endpoint=data.get("endpoint", ""),
            p256dh=keys.get("p256dh", ""),
            auth=keys.get("auth", ""),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
    except ServiceError as exc:
        return _error(str(exc))
    messages.success(request, "Notifications are on for this device.")
    return JsonResponse({"ok": True, "id": subscription.pk, "label": subscription.label})


@role_required(*ALL_ROLES)
@never_cache
@require_POST
def unsubscribe(request):
    """Remove this browser's device (the Turn off button)."""
    try:
        services.unregister_device(request.user, _json_body(request).get("endpoint", ""))
    except ServiceError as exc:
        return _error(str(exc))
    messages.success(request, "Notifications are off for this device.")
    return JsonResponse({"ok": True})


@role_required(*ALL_ROLES)
@never_cache
@require_POST
def status(request):
    """Does the server still know this browser's subscription? (POST because the endpoint is private.)"""
    try:
        registered = services.is_registered(request.user, _json_body(request).get("endpoint", ""))
    except ServiceError as exc:
        return _error(str(exc))
    return JsonResponse({"ok": True, "registered": registered})


@role_required(*ALL_ROLES)
@never_cache
@require_POST
def test(request):
    """Push a test message to all of the person's devices and say how many were reached."""
    if not push.is_enabled():
        return _error("Push notifications are not set up on this server.")
    # One test every few seconds per person: the button must not become a way to spam devices.
    if not cache.add(f"push-test:{request.user.pk}", 1, TEST_COOLDOWN_SECONDS):
        return _error("Please wait a few seconds before sending another test.", status=429)
    result = push.send_test(request.user)
    if result["devices"] == 0:
        return _error("No device has notifications turned on yet.")
    reached, total = result["sent"], result["devices"]
    noun = "device" if total == 1 else "devices"
    return JsonResponse({
        "ok": True, "reached": reached, "total": total,
        "message": f"Test sent. Reached {reached} of {total} {noun}.",
    })


@role_required(*ALL_ROLES)
@never_cache
@require_POST
def remove_device(request, pk):
    """Remove one device from the Settings list (a normal form POST), then go back to the page."""
    try:
        services.remove_device(request.user, pk)
        messages.success(request, "Device removed.")
    except ServiceError as exc:
        messages.error(request, str(exc))
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = SETTINGS_FALLBACK
    return redirect(target)


@require_GET
def service_worker(request):
    """Serve `static/js/sw.js` from `/sw.js`.

    A service worker can only control pages at or below its own URL, so it has to be served from the
    site root, not from /static/js/. Public on purpose (the browser fetches it without a page). `no-cache`
    makes browsers check for a new version on every load.
    """
    path = finders.find("js/sw.js")
    if not path:
        raise Http404("sw.js not found")
    response = FileResponse(open(path, "rb"), content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response
