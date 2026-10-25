"""accounts.templatetags.people_tags: helpers for the people screens shared by every role.

    {% load people_tags %}
    {% role_url 'user_detail' person.pk %}      -> /superadmin/users/5/, /admin/users/5/ or /coordinator/users/5/
    {% role_url_or_empty 'report_print' as url %} -> the same, or "" while the route does not exist
    {% person_actions person as can %}           -> then {% if 'archive' in can %} ... {% endif %}
    {% may_type_password as may_type %}          -> then {% if may_type %} ... {% endif %}
    {% person_tags person %}                     -> the badges: Needs password change, Guest, Member (no account), ...

`role_url` reverses the name inside the url namespace of the page being shown, so one template
serves all three roles. `person_actions` asks `accounts.permissions.available_actions`, the same
matrix the services enforce, so a button is only drawn when the server would accept the click.
Hiding a button is not access control; the services still check.
"""
from django import template
from django.urls import NoReverseMatch, reverse

from ..permissions import available_actions, can_type_password

register = template.Library()

#: Label and look of each tag. The look is a name `_person_tags.html` turns into classes; the label always
#: carries the meaning, so colour is never the only signal.
TAG_LABELS = {
    "needs_password_change": ("Needs password change", "warning"),
    "guest": ("Guest", "neutral"),
    "member_record": ("Member (no account)", "neutral"),
    "member_account": ("Member", "neutral"),
    "suspended": ("Suspended", "danger"),
    "archived": ("Archived", "muted"),
}


#: Url namespace of each leader's area. Used only when the page itself is not inside an area (the dashboards
#: live under their own ``dashboards`` namespace but link into the person's area: prayer inbox, reports...).
AREA_NAMESPACES = {"super_admin": "superadmin", "admin": "admin_area", "coordinator": "coordinator_area"}


def _area_namespace(context):
    """Namespace to reverse in: the page's own when it is an area, else the signed-in person's area."""
    request = context["request"]
    namespace = request.resolver_match.namespace
    if namespace in AREA_NAMESPACES.values():
        return namespace
    return AREA_NAMESPACES.get(getattr(request.user, "role", None), namespace)


@register.simple_tag(takes_context=True)
def role_url(context, name, *args):
    """URL of `name` in the current role's namespace (superadmin, admin_area or coordinator_area).

    On a page outside every area (the dashboards) it uses the signed-in person's own area, so a dashboard
    template never hardcodes a role namespace.
    """
    return reverse(f"{_area_namespace(context)}:{name}", args=args)


@register.simple_tag(takes_context=True)
def role_url_or_empty(context, name, *args):
    """Like `role_url`, but gives "" when the route does not exist (yet) instead of raising.

    For links to pages another brick adds later (the report and its print page): the template draws the link
    only when this is not empty, so nothing points at a missing route.
    """
    try:
        return reverse(f"{_area_namespace(context)}:{name}", args=args)
    except NoReverseMatch:
        return ""


@register.simple_tag(takes_context=True)
def person_actions(context, person):
    """Set of action names the signed-in person may do to `person` right now."""
    return available_actions(context["request"].user, person)


@register.simple_tag(takes_context=True)
def may_type_password(context):
    """True when the signed-in person may choose a password by hand (super-admin and admin only)."""
    return can_type_password(context["request"].user)


@register.inclusion_tag("accounts/people/_special_role_badges.html")
def special_role_badges(person):
    """Tags ("Tithes member", "Attendance member") for the special roles a person holds.

    The list and detail views call `attach_special_roles(people)` first, so this costs no
    extra query per row.
    """
    from ..services.special_roles import special_role_labels  # lazy: keeps the import order simple

    return {"labels": special_role_labels(getattr(person, "special_role_codes", []))}


@register.inclusion_tag("accounts/people/_person_tags.html")
def person_tags(person, with_status=True):
    """The tags for one person, in the order `User.tag_codes` gives them.

    `with_status=False` leaves out Suspended and Archived, for a row that already has a status column.
    """
    codes = [c for c in person.tag_codes if with_status or c not in ("suspended", "archived")]
    return {"tags": [{"label": TAG_LABELS[c][0], "look": TAG_LABELS[c][1]} for c in codes]}
