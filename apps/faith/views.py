"""faith.views: the Goals screens (Today, new, edit, check-in, note, change schedule, archive, end, delete, all).

Views stay thin. A goal that is missing and a goal that belongs to someone else are both a
404, so nobody can probe for other people's goals. Hiding a button is not access control:
every service checks the owner again on the locked row.
The check-in and note routes answer JSON for the page's fetch calls (and redirect for a
plain form post). The server decides "today"; the browser never sends a date.
"""
import datetime as dt
from collections.abc import Mapping
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.core.services import ServiceError

from . import goal_rules, permissions, services, stats
from .forms import GoalForm
from .forms_changes import ChangeScheduleForm, EndDateForm
from .models import Goal

ALL_ROLES = (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER)
TRUE_VALUES = ("1", "true", "on", "yes")


def goals_screen(view):
    """Every goals page: signed in (any role), able to sign in, never cached."""

    @wraps(view)
    def checked(request, *args, **kwargs):
        """Refuse people who cannot hold goals, then run the view."""
        if not permissions.can_use_goals(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return never_cache(role_required(*ALL_ROLES)(checked))


def _wants_json(request):
    """True for the page's own fetch calls."""
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


def _own_goal(request, pk):
    """The person's own goal, or 404 (the same answer for missing and not theirs)."""
    return get_object_or_404(Goal, pk=pk, user=request.user)


# ------------------------------------------------------------------ Today and All
@goals_screen
def today_view(request):
    """Today: streak chips, progress ring, and the goals scheduled today."""
    today = timezone.localdate()
    return render(request, "faith/today.html", {
        "overview": stats.today_overview(request.user, today),
        "chips": stats.streak_chips(request.user, today),
        "today": today,
        "active_tab": "today",
    })


STATUS_FILTERS = ("all", "active", "completed", "archived")
ENDS_FILTERS = ("any", "never", "deadline")


def _filter_groups(groups, status, ends):
    """Return the All goals groups narrowed by status and by how the goal ends.

    ``status`` is one of STATUS_FILTERS (which group to show); ``ends`` is ``never`` (no end date),
    ``deadline`` (has an end date) or ``any``. Works on the dict that ``stats.goal_list`` returns, or an
    object with the same three attributes, and always answers a plain dict of three lists.
    """
    result = {}
    for key in ("active", "completed", "archived"):
        items = groups[key] if isinstance(groups, Mapping) else getattr(groups, key)
        if status not in ("all", key):
            items = []
        elif ends == "never":
            items = [item for item in items if item["goal"].end_date is None]
        elif ends == "deadline":
            items = [item for item in items if item["goal"].end_date is not None]
        result[key] = list(items)
    return result


@goals_screen
def all_view(request):
    """Every goal the person has, grouped by status, with a status and "how it ends" filter (GET, bad values ignored)."""
    today = timezone.localdate()
    status = request.GET.get("status", "all")
    ends = request.GET.get("ends", "any")
    status = status if status in STATUS_FILTERS else "all"
    ends = ends if ends in ENDS_FILTERS else "any"
    groups = _filter_groups(stats.goal_list(request.user, today), status, ends)
    return render(request, "faith/all_goals.html", {
        "groups": groups,
        "shown": sum(len(items) for items in groups.values()),
        "flt": {"status": status, "ends": ends, "on": status != "all" or ends != "any"},
        "active_tab": "all",
    })


# ------------------------------------------------------------------ create and edit
@goals_screen
def new_view(request):
    """New goal form."""
    if request.method == "POST":
        form = GoalForm(request.POST)
        if form.is_valid():
            try:
                goal = services.create_goal(
                    request.user, form.payload(), token=form.cleaned_data["token"] or None, request=request,
                )
            except services.AlreadySaved:
                messages.info(request, "That goal was already saved.")
                return redirect("faith:goals")
            except ServiceError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"{goal.title} was created.")
                return redirect("faith:goals")
    else:
        form = GoalForm()
    return render(request, "faith/goal_form.html", {
        "form": form, "goal": None, "form_title": "New goal",
        "confirm_message": "Save this goal?", "active_tab": "all",
    })


def _edit_end_date(request, goal):
    """The only edit a started task allows: its end date."""
    if request.method == "POST":
        form = EndDateForm(request.POST)
        if form.is_valid():
            try:
                services.change_end_date(request.user, goal.pk, form.payload(), request=request)
            except PermissionDenied:
                raise Http404
            except services.GoalDataError as exc:
                form.add_error(exc.field if exc.field in form.fields else None, str(exc))
            except ServiceError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "The end date was saved.")
                return redirect("faith:goal_all")
    else:
        form = EndDateForm(initial={"end_date": goal.end_date})
    return render(request, "faith/goal_end_date.html", {
        "form": form, "goal": goal, "repeat_label": stats.repeat_summary(goal), "active_tab": "all",
    })


@goals_screen
def edit_view(request, pk):
    """Edit a goal. Only a goal that has not started is fully editable; a started task changes its end date only.

    A started habit (no end date) has no Edit: it is sent to Change schedule. A completed goal cannot be edited.
    """
    goal = _own_goal(request, pk)
    if goal.status == Goal.Status.ARCHIVED:
        messages.error(request, "Restore this goal before editing it.")
        return redirect("faith:goal_all")
    if goal.status == Goal.Status.COMPLETED:
        messages.error(request, "A completed goal cannot be edited.")
        return redirect("faith:goal_all")
    can = goal_rules.actions(goal, timezone.localdate())
    if can["change_schedule"]:
        messages.info(request, "A goal with no end date is changed with Change schedule.")
        return redirect("faith:goal_change_schedule", pk=goal.pk)
    if can["edit_end_date"]:
        return _edit_end_date(request, goal)

    locked = goal.checkins.exists()
    first = goal.checkins.order_by("checkin_date").values_list("checkin_date", flat=True).first()
    options = {"goal": goal, "schedule_locked": locked, "first_checkin": first}

    if request.method == "POST":
        form = GoalForm(request.POST, **options)
        if form.is_valid():
            try:
                services.update_goal(request.user, goal.pk, form.payload(), request=request)
            except PermissionDenied:
                raise Http404
            except services.GoalDataError as exc:
                form.add_error(exc.field if exc.field in form.fields else None, str(exc))
            except ServiceError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Your changes were saved.")
                return redirect("faith:goals")
    else:
        form = GoalForm(**options)
    return render(request, "faith/goal_form.html", {
        "form": form, "goal": goal, "locked": locked, "form_title": "Edit goal",
        "confirm_message": "Save your changes?", "active_tab": "all",
        "repeat_label": stats.repeat_summary(goal),
    })


@goals_screen
def change_schedule_view(request, pk):
    """Change how a habit repeats and its time of day: ends the old goal today, starts a new one tomorrow.

    Only for a started goal with no end date. A goal with an end date is sent to its end date edit.
    """
    goal = _own_goal(request, pk)
    if goal.status != Goal.Status.ACTIVE:
        messages.error(request, "Only an active goal can change its schedule.")
        return redirect("faith:goal_all")
    if goal.end_date is not None:
        messages.error(request, "A goal with an end date cannot change its schedule. Change its end date instead.")
        return redirect("faith:goal_edit", pk=goal.pk)
    if not goal_rules.actions(goal, timezone.localdate())["change_schedule"]:
        messages.info(request, "This goal has not started yet. Edit it instead.")
        return redirect("faith:goal_edit", pk=goal.pk)
    if request.method == "POST":
        form = ChangeScheduleForm(request.POST)
        if form.is_valid():
            try:
                services.change_schedule(request.user, goal.pk, form.payload(), request=request)
            except PermissionDenied:
                raise Http404
            except ServiceError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Done. The new schedule starts tomorrow.")
                return redirect("faith:goals")
    else:
        form = ChangeScheduleForm(initial={
            "repeat_type": goal.repeat_type, "weekdays": [str(d) for d in (goal.weekdays or [])],
            "all_day": goal.start_time is None, "start_time": goal.start_time, "end_time": goal.end_time,
        })
    today = timezone.localdate()
    return render(request, "faith/change_schedule.html", {
        "form": form, "goal": goal, "repeat_label": stats.repeat_summary(goal),
        "today": today, "tomorrow": today + dt.timedelta(days=1),
        "active_tab": "all",
    })


# ------------------------------------------------------------------ check-in and note
@goals_screen
@require_POST
def check_view(request, pk):
    """Check or uncheck today's box (POST `done` = 1 or 0). JSON for fetch, redirect otherwise."""
    goal = _own_goal(request, pk)
    wanted = request.POST.get("done", "").lower() in TRUE_VALUES
    try:
        result = services.set_checkin(request.user, goal.pk, wanted)
    except PermissionDenied:
        raise Http404
    except ServiceError as exc:
        messages.error(request, str(exc))          # shown as a toast after the page reloads
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        return redirect("faith:goals")
    if not _wants_json(request):
        return redirect("faith:goals")
    today = result["today"]
    done_count, total = stats.today_counts(request.user, today)
    return JsonResponse({
        "ok": True, "done": result["done"], "today": today.isoformat(),
        "done_count": done_count, "total": total,
        "streak": stats.goal_streaks(result["goal"], today).current,
    })


@goals_screen
@require_POST
def note_view(request, pk):
    """Save today's private note for one goal."""
    goal = _own_goal(request, pk)
    try:
        services.set_checkin_note(request.user, goal.pk, request.POST.get("note", ""))
    except PermissionDenied:
        raise Http404
    except ServiceError as exc:
        messages.error(request, str(exc))
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        return redirect("faith:goals")
    if _wants_json(request):
        return JsonResponse({"ok": True})
    messages.success(request, "Note saved.")
    return redirect("faith:goals")


# ------------------------------------------------------------------ archive and restore
@goals_screen
@require_POST
def archive_view(request, pk):
    """Archive a goal (POST only)."""
    goal = _own_goal(request, pk)
    try:
        services.archive_goal(request.user, goal.pk, request=request)
    except PermissionDenied:
        raise Http404
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{goal.title} was archived.")
    return redirect("faith:goal_all")


@goals_screen
@require_POST
def restore_view(request, pk):
    """Restore an archived goal (POST only)."""
    goal = _own_goal(request, pk)
    try:
        services.restore_goal(request.user, goal.pk, request=request)
    except PermissionDenied:
        raise Http404
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{goal.title} was restored.")
    return redirect("faith:goal_all")


@goals_screen
@require_POST
def end_view(request, pk):
    """End a goal today (POST only): it moves to Completed and keeps its check-ins."""
    goal = _own_goal(request, pk)
    try:
        services.end_goal(request.user, goal.pk, request=request)
    except PermissionDenied:
        raise Http404
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{goal.title} was ended.")
    return redirect("faith:goal_all")


@goals_screen
@require_POST
def delete_view(request, pk):
    """Permanently delete an archived goal (POST only)."""
    goal = _own_goal(request, pk)
    title = goal.title
    try:
        services.delete_goal(request.user, goal.pk, request=request)
    except PermissionDenied:
        raise Http404
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{title} was deleted.")
    return redirect("faith:goal_all")
