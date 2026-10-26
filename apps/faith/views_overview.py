"""faith.views_overview: the goal detail page and the global history page (Brick 4).

Both are read-only GET screens for the person's own goals. The numbers come from
``faith.overview``; nothing here writes, audits or notifies. A goal that does not exist and a goal that
belongs to someone else both answer 404, so the page never confirms that another person's goal exists.

Both views use ``faith.views.goals_screen``, the same guard as every other goal screen (signed in with any
role, able to sign in, never cached), so the whole Goals area is protected in one way.
"""
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from apps.faith import overview
from apps.faith.models import Goal
from apps.faith.permissions import can_manage_goal
from apps.faith.views import goals_screen


@goals_screen
def goal_detail(request, pk):
    """Show one goal's streak, ring, heatmap, weekly bars, best weekday and month grid (owner only)."""
    goal = Goal.objects.filter(pk=pk).first()
    if goal is None or not can_manage_goal(request.user, goal):
        raise Http404
    today = timezone.localdate()
    return render(request, "faith/goal_detail.html", {
        "goal": goal,
        "ov": overview.goal_overview(goal, today),
        "repeat": overview.repeat_summary(goal),
        "when": overview.when_summary(goal),
        "active_tab": "all",
    })


@goals_screen
def history(request):
    """Show every goal together: heatmap, perfect days, week and month rates, and the activity rows."""
    today = timezone.localdate()
    return render(request, "faith/history.html", {
        "ov": overview.global_overview(request.user, today),
        "active_tab": "history",
    })
