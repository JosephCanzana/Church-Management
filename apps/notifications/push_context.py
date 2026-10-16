"""notifications.push_context: template variables for Web Push (add to TEMPLATES context_processors).

`push_enabled` is False until the VAPID keys are set, and templates hide every push control then.
Only the PUBLIC key is exposed; the private key never leaves the settings module.
"""
from django.conf import settings

from . import push


def push_config(request):
    """Context: `push_enabled` and `push_public_key`."""
    enabled = push.is_enabled()
    return {
        "push_enabled": enabled,
        "push_public_key": settings.VAPID_PUBLIC_KEY if enabled else "",
    }
