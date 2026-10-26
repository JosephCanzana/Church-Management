"""faith.views_overview: the goal detail page and the global history page (Brick 4).

Both are read-only GET screens for the person's own goals. The numbers come from
``faith.overview``; nothing here writes, audits or notifies. A goal that does not exist and a goal that
belongs to someone else both answer 404, so the page never confirms that another person's goal exists.

VERIFY against Brick 3's ``faith/views.py``: the decorator imports below follow the plan (login, role guard,
``never_cache``). Use whatever Brick 3 uses on its own views so every goal screen is guarded the same way.
"""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from apps.faith import overview
from apps.faith.models import Goal
from apps.faith.permissions import can_manage_goal, can_use_goals


@login_required
@never_cache
def goal_detail(request, pk):
    """Show one goal's streak, ring, heatmap, weekly bars, best weekday and month grid (owner only)."""
    if not can_use_goals(request.user):
        raise PermissionDenied
    goal = Goal.objects.filter(pk=pk).first()
    if goal is None or not can_manage_goal(request.user, goal):
        raise Http404
    today = timezone.localdate()
    return render(request, "faith/goal_detail.html", {
        "goal": goal,
        "ov": overview.goal_overview(goal, today),
        "repeat": overview.repeat_summary(goal),
        "when": overview.when_summary(goal),
    })


@login_required
@never_cache
def history(request):
    """Show every goal together: heatmap, perfect days, week and month rates, and the activity rows."""
    if not can_use_goals(request.user):
        raise PermissionDenied
    today = timezone.localdate()
    return render(request, "faith/history.html", {"ov": overview.global_overview(request.user, today)})
