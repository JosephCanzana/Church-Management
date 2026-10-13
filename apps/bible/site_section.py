"""bible.site_section: the Bible switches as a section of the super-admin Site settings screen.

Importing this module registers the section (`apps/bible/urls_superadmin.py` imports it, so it is
registered before any request). The toggles themselves stay in `views_superadmin.py`.
"""
from apps.accounts.site_sections import SiteSection, register_section

from . import services
from .switches import read_switches


def bible_context(request):
    """Context for the Bible section: the master switch and one card per version."""
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
    return {"cards": cards, "online_on": state.master}


register_section(SiteSection(
    key="bible",
    title="Bible versions",
    icon="book-open",
    template="bible/superadmin/_bible_section.html",
    context=bible_context,
    description="Turn Bible versions and online Bible services on or off.",
    order=10,
))
