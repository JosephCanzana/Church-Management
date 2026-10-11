"""Presentation for notifications: icon, plain-language text and target page for each type.

The model stores only a ``type``, an optional actor, an optional entity (``entity_type`` + ``entity_id``)
and a small ``data`` dict that never holds private text. Everything a person reads is built here from
those, so wording can change without a migration.

ADD A TYPE: one line in ``TYPES`` (icon, text). The text may use {actor}, {title} and {action}.
A type that is missing here still shows, with its name as the text and a bell icon.
"""
from django.urls import NoReverseMatch, reverse

from apps.audit.queries import action_label, humanize_code
from apps.core.text import title_case

DEFAULT_ICON = "bell"

# type slug -> (icon, text). The slugs are best guesses: rename them to match what notify() sends.
TYPES = {
    "event_reminder": ("calendar-days", "Reminder: {title} is coming up."),
    "event_change": ("calendar-days", "{title} was updated."),
    "event_cancelled": ("calendar-days", "{title} was cancelled."),
    "prayer_request": ("chat-bubble-left-ellipsis", "{actor} sent a prayer request."),
    "email_taken_over": ("envelope", "Your email address was verified on another account and removed from yours."),
    "admin_alert": ("exclamation-triangle", "{action} was recorded by {actor}."),
}

# entity_type -> url name taking the entity id. A name that does not resolve yet falls back to the list page.
ENTITY_ROUTES = {
    "event": "events:detail",
    "prayer_request": "prayer:detail",
    "attendance": "attendance:detail",
    "tithes_offering": "tithes:detail",
}


class _Safe(dict):
    """format_map helper: a placeholder with no value becomes a neutral word instead of raising."""

    def __missing__(self, key):
        return "an item"


def _actor_name(notification):
    """Who did it, as a person would read it. Anonymous senders and system jobs stay anonymous."""
    if notification.data.get("anonymous"):
        return "Someone"
    actor = notification.actor
    if actor is None:
        return "The system"
    return title_case(f"{actor.first_name} {actor.last_name}".strip()) or "Someone"


def describe(notification):
    """Return (icon, text) for one notification."""
    icon, template = TYPES.get(notification.type, (DEFAULT_ICON, None))
    if template is None:
        return icon, humanize_code(notification.type)
    values = _Safe(
        actor=_actor_name(notification),
        title=notification.data.get("title", "An event"),
        action=action_label(notification.data["action"]) if notification.data.get("action") else "An action",
    )
    return icon, template.format_map(values)


def target_url(notification):
    """The page this notification leads to, or None. The page itself does the permission check."""
    route = ENTITY_ROUTES.get(notification.entity_type or "")
    if not route or notification.entity_id is None:
        return None
    try:
        return reverse(route, args=[notification.entity_id])
    except NoReverseMatch:
        return None


def decorate(items):
    """Attach ``icon`` and ``text`` to each notification and return the same list."""
    for item in items:
        item.icon, item.text = describe(item)
    return items
