"""accounts.site_sections: the registry behind the super-admin "Site settings" screen.

Site settings is one page with a section menu. A section is a small record: a key, a title, an icon,
a partial template and a function that returns that partial's context. To add a function to Site
settings, write its partial and call `register_section(...)` from the owning app (import the module
once, for example from that app's urls); the shell, the menu and the layout need no change.

Two guards still apply, as everywhere: the page is `role_required(SUPER_ADMIN)` and every action a
section posts goes to its own view and service, which check permission again. The registry only
decides what is DISPLAYED.

Dependency direction: this module lives in `accounts`, so apps that depend on `accounts` (such as
`bible`) register their own sections; `accounts` never imports them.
"""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class SiteSection:
    """One block of the Site settings screen."""
    key: str                    # used in ?section=<key>; lowercase, dashes
    title: str                  # menu text and heading
    icon: str                   # name that exists in static/icons/sprite.svg
    template: str               # partial included inside the shell
    context: Callable           # context(request) -> dict merged into the template context
    description: str = ""       # one line under the heading
    order: int = 100            # menu order (lower first)


_SECTIONS = {}


def register_section(section):
    """Add (or replace, so a re-import is harmless) a section."""
    _SECTIONS[section.key] = section


def all_sections():
    """Every registered section in menu order."""
    return sorted(_SECTIONS.values(), key=lambda s: (s.order, s.title))


def get_section(key):
    """The section for `key`, or None."""
    return _SECTIONS.get(key)


def _special_roles_context(request):
    """Context for the special-roles section: the same limits panel the admin page shows."""
    from .views.special_roles import SECTION_KEY, limits_context  # lazy: views import services

    return limits_context(request, section_key=SECTION_KEY)


register_section(SiteSection(
    key="special-roles",
    title="Special roles",
    icon="user-group",
    template="accounts/people/_special_role_limits.html",
    context=_special_roles_context,
    description="How many members of each extension may hold the tithes and attendance roles.",
    order=20,
))


def _jobs_context(request):
    """Context for the Jobs section (reminder switch, buddy end-request wait)."""
    from .views.job_settings import jobs_context  # lazy: views import services

    return jobs_context(request)


def _retention_context(request):
    """Context for the Retention section (one editable row per retention policy)."""
    from .views.job_settings import retention_context  # lazy: views import services

    return retention_context(request)


register_section(SiteSection(
    key="jobs",
    title="Jobs",
    icon="clock",  # verify: must exist in static/icons/sprite.svg
    template="accounts/site_settings/_jobs_section.html",
    context=_jobs_context,
    description="Goal reminders and how long buddy end requests wait.",
    order=40,
))

register_section(SiteSection(
    key="retention",
    title="Retention",
    icon="calendar",  # verify: must exist in static/icons/sprite.svg
    template="accounts/site_settings/_retention_section.html",
    context=_retention_context,
    description="How long archived records are kept before they are deleted for good.",
    order=50,
))
