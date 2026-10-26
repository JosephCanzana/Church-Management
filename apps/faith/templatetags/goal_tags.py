"""faith template tags: ``{% goal_actions goal as can %}`` gives templates the same answers the services use."""
from django import template
from django.utils import timezone

from apps.faith import goal_rules

register = template.Library()


@register.simple_tag
def goal_actions(goal):
    """Return the action flags for ``goal`` as of today (Manila): can.edit, can.change_schedule, can.end, can.delete..."""
    return goal_rules.actions(goal, timezone.localdate())
