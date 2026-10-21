"""Navigation for the signed-in app shell (sidebar, mobile drawer, bottom bar, page tabs).

Everything the menus show comes from NAV_ITEMS and SECTION_TABS below, so changing a
menu never means touching a template:

* add a link      -> add a NavItem line
* remove a link   -> delete its line
* rearrange       -> move the line (the order here is the order on screen)
* who sees it     -> change ``roles=``
* bottom bar      -> ``bottom=True`` (the first BOTTOM_BAR_SLOTS such items show
                     on the bar; everything else goes under "More")
* sidebar heading -> ``group="..."`` (consecutive items with the same group share
                     one heading; "" means no heading)
* hub (one sidebar entry, several pages) -> ``tabs="key"`` on the NavItem and a
                     matching entry in SECTION_TABS. The sidebar shows ONE link; the
                     pages themselves show the sub-pages as tabs with
                     ``{% section_tabs "key" %}`` (apps/accounts/templatetags/nav_tags.py).

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
MANAGERS = (SUPER_ADMIN, ADMIN)

# Special url_name: each role's own landing page, taken from ROLE_HOME_NAMES.
ROLE_HOME = "role_home"

# url_name of a hub item: its link is the first visible tab that resolves.
HUB = ""

# How many items fit on the mobile bottom bar (a "More" button is always added).
BOTTOM_BAR_SLOTS = 4


@dataclass(frozen=True)
class NavItem:
    """One link in the app menus.

    icon must be a name in static/icons/sprite.svg. exact=True makes the link
    active only on its own path (use it for home pages, whose path is a prefix
    of other pages). ``tabs`` turns the item into a hub: it links to the first
    visible tab of that SECTION_TABS entry and is active on every tab's pages.
    ``short`` is a shorter label used only on the mobile bottom bar, where five
    equal slots leave room for about 8 characters (empty = use ``label``).
    """

    label: str
    url_name: str
    icon: str
    roles: tuple = EVERYONE
    bottom: bool = False
    group: str = ""
    exact: bool = False
    tabs: str = ""
    short: str = ""


@dataclass(frozen=True)
class TabItem:
    """One page tab inside a hub (shown at the top of the page, not in the sidebar).

    ``also`` lists other url names whose pages keep this tab lit (a tab that owns a
    group of sibling lists, such as Resources: monthly, weekly and general).
    ``prefixes`` are literal path prefixes that also belong to the tab (a whole area
    such as /manage/landing/, so pages added later need no change here). When
    several tabs match the current path the most specific (longest) match wins.
    """

    label: str
    url_name: str
    roles: tuple = EVERYONE
    also: tuple = ()
    prefixes: tuple = ()


# ---------------------------------------------------------------------------
# EDIT HERE. The url names below are starting guesses: any that do not exist yet
# show as dimmed placeholders, so replace them with your real names as views land.
# ---------------------------------------------------------------------------
NAV_ITEMS = [
    # --- Personal ---------------------------------------------------------
    NavItem("Home", ROLE_HOME, "home", bottom=True, exact=True),
    NavItem("Events", "events:index", "calendar-days", bottom=True),
    NavItem("Bible", "bible:reader", "book-open", bottom=True),
    NavItem("Buddy", "buddy:home", "user-group", roles=(COORDINATOR, MEMBER)),
    # Habit hub: goals, my attendance and my tithes are all "how am I doing" pages.
    NavItem("My journey", HUB, "flag", bottom=True, tabs="journey", short="Journey"),
    NavItem("Resources", "resources:list", "folder-open", exact=True),
    # Members send prayer requests; a coordinator receives them (see Manage).
    NavItem("Prayer", "prayer:list", "heart", roles=(MEMBER,)),
    # A member holding the attendance seat takes sheets; anyone else is refused on the page.
    NavItem("Attendance sheets", "attendance:sheet_list", "clipboard-document-check", roles=(MEMBER,)),
    # A member holding the tithes seat works on draft reports; anyone else is refused on the page.
    NavItem("Tithes", "tithes:report_list", "banknotes", roles=(MEMBER,)),

    # --- Manage (sidebar heading) -------------------------------------------
    # One Attendance entry per role: each role area has its own url namespace (see attendance/area_urls.py).
    NavItem("Attendance sheets", "superadmin:sheet_list", "clipboard-document-check", roles=(SUPER_ADMIN,), group="Manage"),
    NavItem("Attendance sheets", "admin_area:sheet_list", "clipboard-document-check", roles=(ADMIN,), group="Manage"),
    NavItem("Attendance sheets", "coordinator_area:sheet_list", "clipboard-document-check", roles=(COORDINATOR,), group="Manage"),
    # Tithes reports: one entry per role area (same pattern as Attendance, see tithes/area_urls.py).
    NavItem("Tithes", "superadmin:report_list", "banknotes", roles=(SUPER_ADMIN,), group="Manage"),
    NavItem("Tithes", "admin_area:report_list", "banknotes", roles=(ADMIN,), group="Manage"),
    NavItem("Tithes", "coordinator_area:report_list", "banknotes", roles=(COORDINATOR,), group="Manage"),
    # The coordinator's prayer inbox (same page as a member's Prayer link for now).
    NavItem("Prayer requests", "prayer:list", "heart", roles=(COORDINATOR,), group="Manage"),
    NavItem("Extensions", "superadmin:extension_list", "building-office", roles=MANAGERS, group="Manage"),
    # People hub: People | Guests | Approvals, one entry per role area (see accounts/urls/).
    NavItem("People", HUB, "users", roles=LEADERS, group="Manage", tabs="people"),
    # Public website content: Landing | Give page | Resources (admin and super-admin).
    NavItem("Website", HUB, "document-text", roles=MANAGERS, group="Manage", tabs="website"),
    # Super-admin only: Bible switches, special-role limits, registration switch.
    NavItem("Site settings", "bible_superadmin:site_settings", "adjustments-horizontal", roles=(SUPER_ADMIN,), group="Manage"),
    NavItem("Audit log", "audit:log", "shield-check", roles=MANAGERS, group="Manage"),
]

# Page tabs for each hub. The order here is the order of the tabs.
SECTION_TABS = {
    "journey": [
        TabItem("Goals", "faith:goals", roles=(COORDINATOR, MEMBER)),
        TabItem("Attendance", "attendance:mine"),
        # "My tithes": the person's own giving history (entries where I am giver or co-giver), every role.
        # It lives at /tithes/mine/ in the member root only (tithes/urls.py).
        TabItem("Tithes", "tithes:mine"),
    ],
    "people": [
        TabItem("People", "superadmin:user_list", roles=(SUPER_ADMIN,)),
        TabItem("People", "admin_area:user_list", roles=(ADMIN,)),
        TabItem("People", "coordinator_area:user_list", roles=(COORDINATOR,)),
        TabItem("Guests", "superadmin:guest_list", roles=(SUPER_ADMIN,)),
        TabItem("Guests", "admin_area:guest_list", roles=(ADMIN,)),
        TabItem("Guests", "coordinator_area:guest_list", roles=(COORDINATOR,)),
        TabItem("Approvals", "superadmin:registration_approvals", roles=(SUPER_ADMIN,)),
        TabItem("Approvals", "admin_area:registration_approvals", roles=(ADMIN,)),
        TabItem("Approvals", "coordinator_area:registration_approvals", roles=(COORDINATOR,)),
    ],
    "website": [
        TabItem(
            "Resources",
            "resources:theme_list",
            roles=MANAGERS,
            also=("resources:weekly_list", "resources:general_list"),
        ),
        # Everything under /manage/landing/ (text, photos, footer links) is "Landing"; the Give page
        # tab below is more specific, so it wins on /manage/landing/donation-accounts/.
        TabItem("Landing", "pages:pages_manage:content_list", roles=MANAGERS, prefixes=("/manage/landing/",)),
        # Giving details: public on /give/, managed here.
        # The public /give/ page belongs to this tab too (pages:pages_manage:donation_account_list is its manager screen).
        TabItem(
            "Give page",
            "pages:pages_manage:donation_accounts|pages:pages_manage:donation_account_list",
            roles=MANAGERS,
            also=("pages:give",),
        ),
    ],
}


def _resolve_name(name, role):
    """Return the URL for a url name (or ROLE_HOME), or None when the route does not exist (yet).

    ``name`` may list alternatives separated by ``|`` ("app:a|app:b"): the first one that
    resolves wins. Use it while a route's final name is not settled.
    """
    if "|" in name:
        for alternative in name.split("|"):
            href = _resolve_name(alternative.strip(), role)
            if href:
                return href
        return None
    if name == ROLE_HOME:
        # Imported here to avoid a circular import with accounts.decorators.
        from apps.accounts.decorators import ROLE_HOME_NAMES

        name = ROLE_HOME_NAMES.get(role)
        if not name:
            return None
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def _resolve_href(item, role):
    """Return the item's URL, or None when its route does not exist (yet)."""
    return _resolve_name(item.url_name, role)


def _is_active(href, path, exact):
    """True when the current path belongs to this link."""
    if not href:
        return False
    if path == href:
        return True
    return not exact and href != "/" and path.startswith(href)


def _match_length(owned, path):
    """Length of the longest owned prefix that the path falls under (0 = no match)."""
    return max((len(h) for h in owned if h and h != "/" and path.startswith(h)), default=0)


def build_tabs(section, role, path):
    """Return the visible tabs of one hub as plain dicts (label, href, disabled, active).

    Tabs the role may not see are left out; a tab whose route does not exist yet
    is kept but ``disabled`` so the hub shape is visible while views are built.
    Only the tab with the most specific match for ``path`` is active.
    """
    tabs, scores = [], []
    for tab in SECTION_TABS.get(section, []):
        if role not in tab.roles:
            continue
        href = _resolve_name(tab.url_name, role)
        owned = [href] + [_resolve_name(name, role) for name in tab.also] + list(tab.prefixes)
        scores.append(_match_length(owned, path))
        tabs.append({"label": tab.label, "href": href or "#", "disabled": href is None, "active": False})
    best = max(scores, default=0)
    for tab, score in zip(tabs, scores):
        tab["active"] = best > 0 and score == best
    return tabs


def hub_tabs_for(role, path):
    """Return the tabs of whichever hub the current page belongs to ([] when it is in none)."""
    for section in SECTION_TABS:
        tabs = build_tabs(section, role, path)
        if any(tab["active"] for tab in tabs):
            return tabs
    return []


def _entry(item, role, path):
    """Turn a NavItem into the plain dict the templates render."""
    if item.tabs:
        # A hub links to its first usable tab and is lit up on any of its tabs.
        tabs = build_tabs(item.tabs, role, path)
        first = next((tab for tab in tabs if not tab["disabled"]), None)
        href = first["href"] if first else None
        active = any(tab["active"] for tab in tabs)
    else:
        href = _resolve_href(item, role)
        active = _is_active(href, path, item.exact)
    return {
        "label": item.label,
        "icon": item.icon,
        "href": href or "#",
        "disabled": href is None,
        "active": active,
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

    flagged = [(item, entry) for item, entry in zip(visible, entries) if item.bottom]
    on_bar = flagged[:BOTTOM_BAR_SLOTS]
    # Compare before relabelling, so the More sheet still lists everything that is not on the bar.
    more_items = [entry for entry in entries if entry not in [e for _, e in on_bar]]
    # The bar gets the short label (when set); the sidebar and More sheet keep the full one.
    bottom_items = [{**entry, "label": item.short or entry["label"]} for item, entry in on_bar]

    return {"sections": sections, "bottom_items": bottom_items, "more_items": more_items}