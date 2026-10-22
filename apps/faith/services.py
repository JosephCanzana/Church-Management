"""faith.services: every action that changes goals or check-ins.

Rules (AGENTS.md): each action is atomic, locks the rows it changes (the person first,
then the goal), re-checks ownership on the locked row, and raises `ServiceError` with a
message that is safe to show. Views stay thin and call these.

Privacy: audit rows carry ids and field NAMES only, never goal text. Check-ins and
check-in notes are working data and are not audited at all.
"""
import datetime as dt

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.core import services as core_services
from apps.core.services import ServiceError, consume_submission_token

from . import permissions
from .models import Goal, GoalCheckin
from .schedule import is_scheduled

TITLE_MAX = 255
DESCRIPTION_MAX = 2000
NOTE_MAX = 2000
ACTIVE_GOAL_CAP = 20
# Fields a person may always edit (repeat type and weekdays lock after the first check-in).
EDITABLE_FIELDS = ("title", "description", "start_date", "end_date", "start_time", "end_time")


class GoalDataError(ServiceError):
    """Bad goal data. `field` names the form field the message belongs to (or None)."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


class AlreadySaved(ServiceError):
    """The same form was submitted twice; the first submission already worked."""


# ------------------------------------------------------------------ small helpers
def _audit(actor, action, goal, *, fields=None, request=None):
    """Write one audit row with ids and field names only (never goal text).

    This is the single place that calls `audit.log_action`, so a signature difference
    is a one-line fix. (verify) the argument order against apps/audit/services.py.
    """
    after = {"goal_id": goal.pk}
    if fields:
        after["fields"] = sorted(fields)
    log_action(action, actor=actor, request=request, entity_type="goal", entity_id=goal.pk, after=after)


def _lock_actor(actor):
    """Refuse people who cannot use goals, then lock their user row.

    Locking the person first serialises their own actions, which makes the 20-goal cap
    safe against two tabs creating goals at the same moment.
    """
    if not permissions.can_use_goals(actor):
        raise PermissionDenied
    get_user_model().objects.select_for_update().get(pk=actor.pk)


def _locked_goal(actor, goal_id):
    """Lock one goal. A missing goal and another person's goal look the same (PermissionDenied)."""
    goal = Goal.objects.select_for_update().filter(pk=goal_id).first()
    if goal is None or not permissions.can_manage_goal(actor, goal):
        raise PermissionDenied
    return goal


def _check_cap(actor):
    """Refuse when the person already has the maximum number of active goals."""
    if Goal.objects.filter(user=actor, status=Goal.Status.ACTIVE).count() >= ACTIVE_GOAL_CAP:
        raise ServiceError(f"You can have up to {ACTIVE_GOAL_CAP} active goals. Archive one to add another.")


def _require_open_today(goal, today):
    """A check-in or note needs an active goal that is scheduled today."""
    if goal.status != Goal.Status.ACTIVE:
        raise ServiceError("This goal is not active.")
    if not is_scheduled(goal, today):
        raise ServiceError("This goal is not scheduled for today.")


# ------------------------------------------------------------------ validation
def clean_goal_data(data, *, today=None, current_end_date=None):
    """Validate and normalise goal data. The form and every service call this one function.

    `data` keys: title, description, start_date, end_date, start_time, end_time,
    repeat_type, weekdays. `current_end_date` is the end date already stored on an edit:
    keeping it unchanged is allowed even when it is in the past (a completed goal).
    Returns a clean dict; raises `GoalDataError` (with the field name) on the first problem.
    """
    today = today or timezone.localdate()

    title = " ".join(str(data.get("title") or "").split())
    if not title:
        raise GoalDataError("Give your goal a title.", "title")
    if len(title) > TITLE_MAX:
        raise GoalDataError(f"The title can be at most {TITLE_MAX} characters.", "title")

    description = str(data.get("description") or "").strip()
    if len(description) > DESCRIPTION_MAX:
        raise GoalDataError(f"The description can be at most {DESCRIPTION_MAX} characters.", "description")

    start_date = data.get("start_date")
    if not isinstance(start_date, dt.date):
        raise GoalDataError("Pick a start date.", "start_date")
    end_date = data.get("end_date") or None
    if end_date is not None:
        if end_date < start_date:
            raise GoalDataError("The end date cannot be before the start date.", "end_date")
        if end_date < today and end_date != current_end_date:
            raise GoalDataError("The end date cannot be in the past.", "end_date")

    start_time = data.get("start_time") or None
    end_time = data.get("end_time") or None
    if end_time and not start_time:
        raise GoalDataError("Set a start time first.", "end_time")
    if start_time and end_time and end_time <= start_time:
        raise GoalDataError("The end time must be after the start time. A goal cannot run past midnight.", "end_time")

    repeat_type = data.get("repeat_type")
    if repeat_type not in Goal.Repeat.values:
        raise GoalDataError("Choose how often this goal repeats.", "repeat_type")
    weekdays = None
    if repeat_type == Goal.Repeat.WEEKLY:
        try:
            weekdays = sorted({int(d) for d in (data.get("weekdays") or [])})
        except (TypeError, ValueError):
            raise GoalDataError("Pick weekdays from Monday to Sunday.", "weekdays")
        if not weekdays:
            raise GoalDataError("Pick at least one weekday.", "weekdays")
        if weekdays[0] < 0 or weekdays[-1] > 6:
            raise GoalDataError("Pick weekdays from Monday to Sunday.", "weekdays")

    return {
        "title": title, "description": description,
        "start_date": start_date, "end_date": end_date,
        "start_time": start_time, "end_time": end_time,
        "repeat_type": repeat_type, "weekdays": weekdays,
    }


# ------------------------------------------------------------------ goals
@transaction.atomic
def create_goal(actor, data, *, token=None, request=None):
    """Create a goal for `actor`. A replayed form token raises `AlreadySaved`."""
    _lock_actor(actor)
    if token is not None and not consume_submission_token(token):
        raise AlreadySaved("This goal was already saved.")
    clean = clean_goal_data(data, today=timezone.localdate())
    _check_cap(actor)
    goal = Goal.objects.create(user=actor, status=Goal.Status.ACTIVE, **clean)
    _audit(actor, "goal.create", goal, fields=["repeat_type"], request=request)
    return goal


@transaction.atomic
def update_goal(actor, goal_id, data, *, request=None):
    """Edit a goal. Returns the goal; changes nothing (and logs nothing) when nothing changed.

    Rules: the start date cannot move later than the first check-in; the end date cannot be
    earlier than today or earlier than the last done check-in; repeat type and weekdays are
    locked once any check-in exists (use `change_schedule`); giving a completed goal a
    future (or no) end date makes it active again.
    """
    _lock_actor(actor)
    goal = _locked_goal(actor, goal_id)
    if goal.status == Goal.Status.ARCHIVED:
        raise ServiceError("Restore this goal before editing it.")
    today = timezone.localdate()

    locked = goal.checkins.exists()
    data = dict(data)
    if locked:
        data.setdefault("repeat_type", goal.repeat_type)
        data.setdefault("weekdays", goal.weekdays)
    clean = clean_goal_data(data, today=today, current_end_date=goal.end_date)

    if locked and (clean["repeat_type"] != goal.repeat_type or (clean["weekdays"] or []) != sorted(goal.weekdays or [])):
        raise ServiceError("Use Change schedule to change how this goal repeats.")

    first = goal.checkins.order_by("checkin_date").values_list("checkin_date", flat=True).first()
    if first is not None and clean["start_date"] > first:
        raise GoalDataError("The start date cannot be later than your first check-in.", "start_date")
    last_done = goal.checkins.filter(done=True).order_by("-checkin_date").values_list("checkin_date", flat=True).first()
    if clean["end_date"] and last_done and clean["end_date"] < last_done:
        raise GoalDataError("The end date cannot be before your last completed check-in.", "end_date")

    changed = [f for f in EDITABLE_FIELDS if getattr(goal, f) != clean[f]]
    if not locked:
        changed += [f for f in ("repeat_type", "weekdays") if getattr(goal, f) != clean[f]]

    reactivated = goal.status == Goal.Status.COMPLETED and (clean["end_date"] is None or clean["end_date"] >= today)
    if reactivated:
        _check_cap(actor)
    if not changed and not reactivated:
        return goal

    for field in changed:
        setattr(goal, field, clean[field])
    update_fields = list(changed)
    if reactivated:
        goal.status = Goal.Status.ACTIVE
        goal.completed_at = None
        update_fields += ["status", "completed_at"]
        changed.append("status")
    goal.save(update_fields=update_fields)
    _audit(actor, "goal.update", goal, fields=changed, request=request)
    return goal


@transaction.atomic
def change_schedule(actor, goal_id, data, *, request=None):
    """End the old goal today and start a linked new goal tomorrow with a new repeat.

    `data` carries only `repeat_type` and `weekdays`. The new goal copies the title,
    description, time and end date, and points at the old one through `previous_goal`.
    Returns the new goal. The old goal keeps its history and stays checkable until midnight.
    """
    _lock_actor(actor)
    goal = _locked_goal(actor, goal_id)
    today = timezone.localdate()
    tomorrow = today + dt.timedelta(days=1)
    if goal.status != Goal.Status.ACTIVE:
        raise ServiceError("Only an active goal can change its schedule.")
    if goal.start_date > today:
        raise ServiceError("This goal has not started yet. Edit it instead.")
    if goal.end_date is not None and goal.end_date <= today:
        raise ServiceError("This goal ends today, so there is nothing to continue tomorrow.")

    clean = clean_goal_data({
        "title": goal.title, "description": goal.description,
        "start_date": tomorrow, "end_date": goal.end_date,
        "start_time": goal.start_time, "end_time": goal.end_time,
        "repeat_type": data.get("repeat_type"), "weekdays": data.get("weekdays"),
    }, today=today)
    if clean["repeat_type"] == goal.repeat_type and (clean["weekdays"] or []) == sorted(goal.weekdays or []):
        raise ServiceError("This goal already repeats that way.")

    goal.end_date = today
    goal.save(update_fields=["end_date"])
    new_goal = Goal.objects.create(user=actor, status=Goal.Status.ACTIVE, previous_goal=goal, **clean)
    _audit(actor, "goal.change_schedule", new_goal, fields=["repeat_type", "weekdays", "previous_goal"], request=request)
    return new_goal


@transaction.atomic
def archive_goal(actor, goal_id, *, request=None):
    """Archive a goal (hidden, left out of history). Archiving twice does nothing."""
    _lock_actor(actor)
    goal = _locked_goal(actor, goal_id)
    if goal.status == Goal.Status.ARCHIVED:
        return goal
    goal.status = Goal.Status.ARCHIVED
    core_services.archive(goal, extra_fields=["status"])
    _audit(actor, "goal.archive", goal, request=request)
    return goal


@transaction.atomic
def restore_goal(actor, goal_id, *, request=None):
    """Restore an archived goal: completed when its end date has passed, otherwise active."""
    _lock_actor(actor)
    goal = _locked_goal(actor, goal_id)
    if goal.status != Goal.Status.ARCHIVED:
        return goal
    today = timezone.localdate()
    if goal.end_date is not None and goal.end_date < today:
        goal.status = Goal.Status.COMPLETED
        goal.completed_at = goal.completed_at or timezone.now()
    else:
        _check_cap(actor)
        goal.status = Goal.Status.ACTIVE
        goal.completed_at = None
    core_services.restore(goal, extra_fields=["status", "completed_at"])
    _audit(actor, "goal.restore", goal, request=request)
    return goal


# ------------------------------------------------------------------ check-ins
@transaction.atomic
def set_checkin(actor, goal_id, done):
    """Check or uncheck today's box. The server decides "today"; no date is ever accepted.

    Unchecking keeps the row and its note (`done=False`). Asking for the state the box
    already has changes nothing. Returns {"done", "today", "changed", "goal"}.
    """
    if not permissions.can_use_goals(actor):
        raise PermissionDenied
    goal = _locked_goal(actor, goal_id)
    today = timezone.localdate()
    _require_open_today(goal, today)
    wanted = bool(done)
    checkin, created = GoalCheckin.objects.get_or_create(goal=goal, checkin_date=today, defaults={"done": wanted})
    changed = created
    if not created and checkin.done != wanted:
        checkin.done = wanted
        checkin.save(update_fields=["done"])
        changed = True
    return {"done": wanted, "today": today, "changed": changed, "goal": goal}


@transaction.atomic
def set_checkin_note(actor, goal_id, note):
    """Save the private note for today. A note alone creates the row with `done=False`."""
    if not permissions.can_use_goals(actor):
        raise PermissionDenied
    goal = _locked_goal(actor, goal_id)
    today = timezone.localdate()
    _require_open_today(goal, today)
    note = str(note or "").strip()
    if len(note) > NOTE_MAX:
        raise ServiceError(f"A note can be at most {NOTE_MAX} characters.")
    checkin, created = GoalCheckin.objects.get_or_create(
        goal=goal, checkin_date=today, defaults={"done": False, "note": note},
    )
    if not created and checkin.note != note:
        checkin.note = note
        checkin.save(update_fields=["note"])
    return checkin
