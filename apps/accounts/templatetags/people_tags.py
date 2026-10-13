"""accounts.templatetags.people_tags: helpers for the people screens shared by every role.

    {% load people_tags %}
    {% role_url 'user_detail' person.pk %}      -> /superadmin/users/5/, /admin/users/5/ or /coordinator/users/5/
    {% person_actions person as can %}           -> then {% if 'archive' in can %} ... {% endif %}
    {% may_type_password as may_type %}          -> then {% if may_type %} ... {% endif %}

`role_url` reverses the name inside the url namespace of the page being shown, so one template
serves all three roles. `person_actions` asks `accounts.permissions.available_actions`, the same
matrix the services enforce, so a button is only drawn when the server would accept the click.
Hiding a button is not access control; the services still check.
"""
from django import template
from django.urls import reverse

from ..permissions import available_actions, can_type_password

register = template.Library()


@register.simple_tag(takes_context=True)
def role_url(context, name, *args):
    """URL of `name` in the current role's namespace (superadmin, admin_area or coordinator_area)."""
    namespace = context["request"].resolver_match.namespace
    return reverse(f"{namespace}:{name}", args=args)


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
