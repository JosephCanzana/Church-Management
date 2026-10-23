"""faith.jobs: the two periodic jobs of the goals feature.

`complete_ended_goals()` runs nightly, `send_goal_reminders()` every 5 minutes. Both are safe to
run twice. Privacy: a goal's title is used ONLY inside the push payload (it is not stored). The
bell item keeps just the goal id, and audit rows carry ids and counts only.
"""
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.core import job_settings
from apps.notifications.services import notify

from .models import Goal, GoalCheckin
from .schedule import is_scheduled

REMINDER_WINDOW_MINUTES = 15  # a missed run can still catch up this long after the start time
NOTIFICATION_TYPE = "goal_reminder"


def complete_ended_goals(today=None):
    """Mark active goals whose end date is before today as completed. Returns how many changed."""
    today = today or timezone.localdate()
    ids = list(
        Goal.objects.filter(status=Goal.Status.ACTIVE, end_date__lt=today)
        .order_by("pk").values_list("pk", flat=True)
    )
    done = 0
    for goal_id in ids:
        with transaction.atomic():
            goal = Goal.objects.select_for_update().filter(
                pk=goal_id, status=Goal.Status.ACTIVE, end_date__lt=today,
            ).first()
            if goal is None:  # changed since the first query: leave it
                continue
            goal.status = Goal.Status.COMPLETED
            goal.completed_at = timezone.now()
            goal.save(update_fields=["status", "completed_at"])
            log_action("goal.complete", entity_type="goal", entity_id=goal.pk,
                       after={"status": "completed"}, source=AuditLog.Source.SYSTEM_JOB)
            done += 1
    return done


def due_reminder_goals(now=None):
    """Active goals owed a reminder right now: scheduled today, not done, start time in the window."""
    now = timezone.localtime(now or timezone.now())
    today = now.date()
    window_start = (now - timedelta(minutes=REMINDER_WINDOW_MINUTES)).time()
    if now.time() < window_start:  # window crosses midnight: times cannot, so only the late part counts
        return []
    goals = (
        Goal.objects.filter(
            status=Goal.Status.ACTIVE, start_time__isnull=False,
            start_time__gt=window_start, start_time__lte=now.time(),
        ).select_related("user")
    )
    checked = set(GoalCheckin.objects.filter(checkin_date=today, done=True,
                                             goal__in=goals).values_list("goal_id", flat=True))
    return [g for g in goals
            if g.pk not in checked and g.user.can_sign_in and is_scheduled(g, today)]


def send_goal_reminders(now=None):
    """Send the due reminders (bell + push). Returns how many were sent; 0 when switched off."""
    if not job_settings.goal_reminders_enabled():
        return 0
    now = timezone.localtime(now or timezone.now())
    today = now.date()
    sent = 0
    for goal in due_reminder_goals(now):
        # The same dedupe key per goal and day keeps a second run (or the 15-minute overlap) quiet.
        # The title is in `push` only; `data` holds just the id.
        result = notify(
            goal.user, NOTIFICATION_TYPE, data={"goal_id": goal.pk},
            dedupe_key=f"goal_reminder:{goal.pk}:{today.isoformat()}",
            push={"title": "Goal reminder", "body": f'Time for "{goal.title}"', "url": "/goals/"},
        )
        if result:
            sent += 1
    return sent
