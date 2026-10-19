"""Template tag that draws the page tabs of a navigation hub.

Usage in a page template (inside the ``content`` block, above the page body):

    {% load nav_tags %}
    {% section_tabs "journey" %}

base_app.html also draws them automatically with ``{% hub_tabs %}`` on every page that belongs
to a hub (a page can switch that off by overriding ``{% block hub_tabs %}{% endblock %}``), so
most pages never need the tag above.

The tabs come from SECTION_TABS in ``accounts/navigation.py``, so adding or hiding
a tab never means editing a template.
"""
from django import template

from apps.accounts.navigation import build_tabs, hub_tabs_for

register = template.Library()


@register.inclusion_tag("includes/section_tabs.html", takes_context=True)
def section_tabs(context, section):
    """Render the tabs of hub ``section`` for the signed-in user and the current path."""
    request = context.get("request")
    user = getattr(request, "user", None)
    role = getattr(user, "role", None)
    path = getattr(request, "path", "")
    return {"tabs": build_tabs(section, role, path) if role else []}


@register.inclusion_tag("includes/section_tabs.html", takes_context=True)
def hub_tabs(context):
    """Render the tabs of the hub the current page belongs to (nothing for pages outside any hub)."""
    request = context.get("request")
    user = getattr(request, "user", None)
    role = getattr(user, "role", None)
    path = getattr(request, "path", "")
    return {"tabs": hub_tabs_for(role, path) if role else []}