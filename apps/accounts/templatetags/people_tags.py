"""accounts.templatetags.people_tags: helpers for the people screens shared by every role.

    {% load people_tags %}
    {% role_url 'user_detail' person.pk %}      -> /superadmin/users/5/, /admin/users/5/ or /coordinator/users/5/
    {% person_actions person as can %}           -> then {% if 'archive' in can %} ... {% endif %}

`role_url` reverses the name inside the url namespace of the page being shown, so one template
serves all three roles. `person_actions` asks `accounts.permissions.available_actions`, the same
matrix the services enforce, so a button is only drawn when the server would accept the click.
Hiding a button is not access control; the services still check.
"""
from django import template
from django.urls import reverse

from ..permissions import available_actions

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
