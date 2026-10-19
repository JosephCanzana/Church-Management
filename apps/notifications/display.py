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
    "event_reminder": ("calendar-days", "Reminder: {title} starts {lead}."),
    "event_changed": ("calendar-days", "{title} has a new time or place."),
    "event_cancelled": ("calendar-days", "{title} was cancelled."),
    "prayer_request": ("chat-bubble-left-ellipsis", "{actor} sent a prayer request."),
    "email_taken_over": ("envelope", "Your email address was verified on another account and removed from yours."),
    "admin_alert": ("exclamation-triangle", "{action} was recorded by {actor}."),
    "registration_request": ("user-plus", "A registration request is waiting for review."),
    "registration_email_failed": ("envelope", "An account email could not be sent. Review the account and issue a new password."),
}

# entity_type -> url name taking the entity id. A name that does not resolve yet falls back to the list page.
ENTITY_ROUTES = {
    "event": "events:detail",
    "prayer_request": "prayer:detail",
    "attendance": "attendance:detail",
    "tithes_offering": "tithes:detail",
    "registration_request": "superadmin:registration_detail",
    "app_user": "superadmin:user_detail",
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


def _lead_text(minutes):
    """'in about 1 day', 'in about 3 hours'... from a reminder's lead time in minutes."""
    if not isinstance(minutes, int) or minutes <= 0:
        return "soon"
    for size, word in ((1440, "day"), (60, "hour"), (1, "minute")):
        if minutes % size == 0:
            count = minutes // size
            return f"in about {count} {word}{'' if count == 1 else 's'}"


def describe(notification):
    """Return (icon, text) for one notification."""
    icon, template = TYPES.get(notification.type, (DEFAULT_ICON, None))
    if template is None:
        return icon, humanize_code(notification.type)
    values = _Safe(
        actor=_actor_name(notification),
        title=notification.data.get("title", "An event"),
        lead=_lead_text(notification.data.get("lead_minutes")),
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
