"""notifications.templatetags.push_tags: the Settings "device" card.

Usage in the settings page template:
    {% load push_tags %}
    {% push_device_card %}
The tag fetches the signed-in person's own devices itself, so the settings view needs no change.
It renders nothing when push is not set up (no VAPID keys) or nobody is signed in.
"""
from django import template
from django.conf import settings

from .. import push, services
from ..models import PushSubscription

register = template.Library()


@register.inclusion_tag("notifications/_push_card.html", takes_context=True)
def push_device_card(context):
    """Context for `_push_card.html`: the person's devices, the public key and the form token."""
    request = context.get("request")
    user = getattr(request, "user", None)
    enabled = bool(push.is_enabled() and user is not None and user.is_authenticated)
    devices = list(PushSubscription.objects.filter(user=user).order_by("-created_at")) if enabled else []
    return {
        "enabled": enabled,
        "devices": devices,
        "public_key": settings.VAPID_PUBLIC_KEY if enabled else "",
        "max_devices": services.MAX_DEVICES,
        "csrf_token": context.get("csrf_token", ""),
        "next_url": request.get_full_path() if request is not None else "/settings/",
    }
