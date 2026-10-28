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
    "prayer_replied": ("chat-bubble-left-ellipsis", "Your prayer request has a reply."),
    "buddy_request": ("user-plus", "{actor} sent you a buddy request."),
    "buddy_accepted": ("user-plus", "{actor} accepted your buddy request."),
    "buddy_message": ("chat-bubble-left-ellipsis", "{actor} sent you a message."),
    "buddy_end_requested": ("user-plus", "{actor} asked to end your buddy pairing."),
    "buddy_ended": ("user-plus", "Your buddy pairing has ended."),
    "tithes_bypass_requested": ("exclamation-triangle", "{actor} asked for a tithes edit approval."),
    "tithes_bypass_approved": ("check-circle", "Your tithes edit request was approved."),
    "tithes_bypass_rejected": ("exclamation-triangle", "Your tithes edit request was rejected."),
    "extension_service_changed": ("calendar-days", "An extension's service schedule was changed."),
}

# The people screens exist once per role area; {area} becomes the recipient's area.
ROLE_AREAS = {"super_admin": "superadmin", "admin": "admin_area", "coordinator": "coordinator_area"}

# type -> url name with no id. Checked first: these types point at a page, not at a row.
TYPE_ROUTES = {
    "buddy_request": "buddy:home",
    "buddy_accepted": "buddy:home",
    "buddy_end_requested": "buddy:home",
    "buddy_ended": "buddy:home",
    "buddy_message": "buddy:messages",
    "prayer_replied": "prayer:list",
    "email_taken_over": "accounts:profile_email",
    "registration_email_failed": "{area}:registration_approvals",
}

# entity_type -> url name taking the entity id. A name that does not resolve falls back to the list page.
ENTITY_ROUTES = {
    "prayer_request": "{area}:prayer_detail",
    "registration_request": "{area}:registration_detail",
    "app_user": "{area}:user_detail",
    "attendance": "attendance:sheet_detail",
    "tithes_offering": "tithes:report_detail",
}
# Only the super-admin has these pages.
SUPER_ADMIN_ROUTES = {
    "extension": "superadmin:extension_detail",
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
    role = notification.user.role
    area = ROLE_AREAS.get(role)

    def resolve(route, args=()):
        if "{area}" in route:
            if area is None:                      # a member has no people screens
                return None
            route = route.format(area=area)
        try:
            return reverse(route, args=args)
        except NoReverseMatch:
            return None

    route = TYPE_ROUTES.get(notification.type)
    if route:
        return resolve(route)
    if notification.entity_type and notification.entity_id is not None:
        route = SUPER_ADMIN_ROUTES.get(notification.entity_type) if role == "super_admin" else None
        route = route or ENTITY_ROUTES.get(notification.entity_type)
        if route:
            return resolve(route, args=[notification.entity_id])
    return None


def decorate(items):
    """Attach ``icon`` and ``text`` to each notification and return the same list."""
    for item in items:
        item.icon, item.text = describe(item)
    return items
