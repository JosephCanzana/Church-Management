"""Navigation for the signed-in app shell (sidebar, mobile drawer, bottom bar).

Everything the menus show comes from NAV_ITEMS below, so changing a menu never
means touching a template:

* add a link      -> add a NavItem line
* remove a link   -> delete its line
* rearrange       -> move the line (the order here is the order on screen)
* who sees it     -> change ``roles=``
* bottom bar      -> ``bottom=True`` (the first BOTTOM_BAR_SLOTS such items show
                     on the bar; everything else goes under "More")
* sidebar heading -> ``group="..."`` (consecutive items with the same group share
                     one heading; "" means no heading)

Only url names and icon names live here (plain strings, no imports from feature
apps), so the dependency rule holds. A url name that does not resolve yet is
shown as a dimmed placeholder instead of raising NoReverseMatch, which lets
planned pages be listed before their views exist.

Hiding a link is not access control: every view still needs role_required().
"""
from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse

# Role values as stored in User.role. Keep in sync with the User model.
SUPER_ADMIN = "super_admin"
ADMIN = "admin"
COORDINATOR = "coordinator"
MEMBER = "member"

EVERYONE = (SUPER_ADMIN, ADMIN, COORDINATOR, MEMBER)
LEADERS = (SUPER_ADMIN, ADMIN, COORDINATOR)

# Special url_name: each role's own landing page, taken from ROLE_HOME_NAMES.
ROLE_HOME = "role_home"

# How many items fit on the mobile bottom bar (a "More" button is always added).
BOTTOM_BAR_SLOTS = 4


@dataclass(frozen=True)
class NavItem:
    """One link in the app menus.

    icon must be a name in static/icons/sprite.svg. exact=True makes the link
    active only on its own path (use it for home pages, whose path is a prefix
    of other pages).
    """

    label: str
    url_name: str
    icon: str
    roles: tuple = EVERYONE
    bottom: bool = False
    group: str = ""
    exact: bool = False


# ---------------------------------------------------------------------------
# EDIT HERE. The url names below are starting guesses: any that do not exist yet
# show as dimmed placeholders, so replace them with your real names as views land.
# ---------------------------------------------------------------------------
NAV_ITEMS = [
    NavItem("Home", ROLE_HOME, "home", bottom=True, exact=True),
    NavItem("Events", "events:list", "calendar-days", bottom=True),
    NavItem("Bible", "bible:reader", "book-open", bottom=True),
    NavItem("Prayer", "prayer:list", "heart", roles=(COORDINATOR, MEMBER), bottom=True),
    NavItem("Goals", "faith:goals", "flag", roles=(COORDINATOR, MEMBER)),
    NavItem("Buddy", "buddy:home", "user-group", roles=(COORDINATOR, MEMBER)),
    NavItem("Attendance", "attendance:list", "clipboard-document-check", roles=(ADMIN, COORDINATOR, MEMBER)),
    NavItem("Tithes", "tithes:list", "banknotes", roles=(ADMIN, COORDINATOR, MEMBER)),
    NavItem("Resources", "resources:list", "folder-open"),
    NavItem("Settings", "theming:settings", "cog-6-tooth"),
    # Management section (sidebar heading "Manage")
    NavItem("Extensions", "superadmin:extension_list", "building-office", roles=(SUPER_ADMIN,), group="Manage"),
    NavItem("People", "superadmin:user_list", "users", roles=(SUPER_ADMIN,), group="Manage"),
    NavItem("Site settings", "core:site_settings", "adjustments-horizontal", roles=(SUPER_ADMIN, ADMIN), group="Manage"),
    NavItem("Audit log", "audit:log", "shield-check", roles=(SUPER_ADMIN,), group="Manage"),
    NavItem("Django admin", "admin:index", "wrench-screwdriver", roles=(SUPER_ADMIN,), group="Manage"),
]


def _resolve_href(item, role):
    """Return the item's URL, or None when its route does not exist (yet)."""
    name = item.url_name
    if name == ROLE_HOME:
        # Imported here to avoid a circular import with accounts.decorators.
        from accounts.decorators import ROLE_HOME_NAMES

        name = ROLE_HOME_NAMES.get(role)
        if not name:
            return None
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def _is_active(href, path, exact):
    """True when the current path belongs to this link."""
    if not href:
        return False
    if path == href:
        return True
    return not exact and href != "/" and path.startswith(href)


def _entry(item, role, path):
    """Turn a NavItem into the plain dict the templates render."""
    href = _resolve_href(item, role)
    return {
        "label": item.label,
        "icon": item.icon,
        "href": href or "#",
        "disabled": href is None,
        "active": _is_active(href, path, item.exact),
    }


def build_nav(role, path):
    """Build what the templates need for one user on one page.

    Returns a dict with:
      sections     -> [{"heading": str, "items": [entry, ...]}, ...] (sidebar, drawer)
      bottom_items -> the entries shown on the mobile bottom bar
      more_items   -> every other entry, shown in the bottom bar's "More" sheet
    """
    visible = [item for item in NAV_ITEMS if role in item.roles]
    entries = [_entry(item, role, path) for item in visible]

    sections = []
    for item, entry in zip(visible, entries):
        if not sections or sections[-1]["heading"] != item.group:
            sections.append({"heading": item.group, "items": []})
        sections[-1]["items"].append(entry)

    flagged = [entry for item, entry in zip(visible, entries) if item.bottom]
    bottom_items = flagged[:BOTTOM_BAR_SLOTS]
    more_items = [entry for entry in entries if entry not in bottom_items]

    return {"sections": sections, "bottom_items": bottom_items, "more_items": more_items}