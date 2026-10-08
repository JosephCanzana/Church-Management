"""Template context for the signed-in app shell.

Register ``accounts.context_processors.navigation`` in
TEMPLATES[0]["OPTIONS"]["context_processors"]. It adds:

* ``nav``               the menus for this user and page (see accounts.navigation)
* ``mobile_nav_style``  "bottom" or "hamburger"
"""
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

from .navigation import build_nav

MOBILE_NAV_STYLES = ("bottom", "hamburger")
DEFAULT_MOBILE_NAV = "bottom"
DEV_SESSION_KEY = "dev_mobile_nav"


def _dev_override(request):
    """DEBUG only: ?nav=bottom or ?nav=hamburger forces a style for this session.

    Any other value (for example ?nav=off) clears it. Delete this function once
    the settings page can change user_settings.mobile_nav_style.
    """
    if not settings.DEBUG:
        return None
    session = getattr(request, "session", None)
    if session is None:
        return None
    requested = request.GET.get("nav")
    if requested in MOBILE_NAV_STYLES:
        session[DEV_SESSION_KEY] = requested
    elif requested is not None:
        session.pop(DEV_SESSION_KEY, None)
    override = session.get(DEV_SESSION_KEY)
    return override if override in MOBILE_NAV_STYLES else None


def _mobile_nav_style(request, user):
    """The user's saved mobile nav style, falling back to the bottom bar."""
    override = _dev_override(request)
    if override:
        return override
    try:
        style = user.settings.mobile_nav_style
    except ObjectDoesNotExist:
        return DEFAULT_MOBILE_NAV
    return style if style in MOBILE_NAV_STYLES else DEFAULT_MOBILE_NAV


def navigation(request):
    """Add the nav menus and mobile nav style for signed-in users."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {
        "nav": build_nav(user.role, request.path),
        "mobile_nav_style": _mobile_nav_style(request, user),
    }