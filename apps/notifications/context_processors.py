"""Template context for the topbar bell: the unread count and the latest few notifications.

Both values are lazy, so pages that never show the bell (login, public pages) cost no queries.
"""
from django.utils.functional import SimpleLazyObject

from apps.notifications.display import decorate
from apps.notifications.models import Notification

RECENT_LIMIT = 5


def bell(request):
    """Provide ``unread_count`` and ``recent_notifications`` for signed-in people."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    own = Notification.objects.filter(user=user)
    return {
        "unread_count": SimpleLazyObject(lambda: own.filter(read_at__isnull=True).count()),
        "recent_notifications": SimpleLazyObject(
            lambda: decorate(list(own.select_related("actor").order_by("-created_at", "-pk")[:RECENT_LIMIT]))
        ),
    }
