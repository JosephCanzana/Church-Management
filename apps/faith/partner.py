"""faith.partner: read-only goal overview for a buddy's "Their goals" screen (Brick 9).

Nothing here writes. The CALLER MUST ALREADY HAVE CHECKED PERMISSION (a live partnership,
see `buddy.services.partner_goals_for`): this function trusts the `user` it is given and
does no access check of its own.

Check-in notes are private to the owner. They are left out BY CONSTRUCTION: the only
check-in query used here is `faith.stats.done_dates_for`, which reads goal id, date and
the `done` flag and never touches the `note` column.
"""
import datetime as dt

from django.db.models import Q

from .models import Goal
from .schedule import is_scheduled
from .stats import ONE_DAY, WEEKDAY_SHORT, done_dates_for, goal_streaks, repeat_summary, streak_chips, time_label

STRIP_DAYS = 7  # the strip is the last 7 days, ending today

# Cell states of the 7-day strip. Plain strings so templates can compare them.
DONE = "done"                    # a done check-in exists
MISSED = "missed"                # scheduled, the day is over, no done check-in
NOT_SCHEDULED = "not_scheduled"  # the goal was not due that day (or had not started yet)
OPEN = "open"                    # scheduled today and not done yet: the day is still open

# Text and symbol per state, so no meaning is carried by colour alone.
_STATE_TEXT = {DONE: "Done", MISSED: "Missed", NOT_SCHEDULED: "Not scheduled", OPEN: "Still open today"}
_STATE_SYMBOL = {DONE: "\u2713", MISSED: "\u2715", NOT_SCHEDULED: "\u2013", OPEN: "\u2022"}


def _strip(goal, today, done_dates):
    """The 7 cells (oldest first, today last) for one goal, from dates already in memory."""
    cells = []
    for back in range(STRIP_DAYS - 1, -1, -1):
        day = today - dt.timedelta(days=back)
        if not is_scheduled(goal, day):
            state = NOT_SCHEDULED
        elif day in done_dates:
            state = DONE
        elif day == today:
            state = OPEN
        else:
            state = MISSED
        cells.append({
            "date": day,
            "label": WEEKDAY_SHORT[day.weekday()],
            "state": state,
            "text": _STATE_TEXT[state],
            "symbol": _STATE_SYMBOL[state],
        })
    return cells


def _sort_key(row):
    """All-day goals first, then timed goals in time order, each group by title."""
    start = row["start_time"]
    return (start is not None, start or dt.time.min, row["title"].lower(), row["id"])


def partner_goal_overview(user, today):
    """What a buddy may see of `user`'s goals on `today`. Read-only, two queries plus streaks.

    Only ACTIVE goals that have started and not ended are included (archived, completed,
    future and ended goals are not). Returns a dict:

    * `goals`: every such goal as a row (id, title, description, repeat_label, time_label,
      start_time, current and best streak, `done_today` = None when not scheduled today,
      `strip` = 7 cells).
    * `to_do_today` / `done_today`: the rows scheduled today that are not done / done.
    * `today_total`, `today_done`: counts for the goals scheduled today.
    * `streak_snapshot`: the person's open and Bible streaks (`stats.streak_chips`).

    No check-in notes are read. The caller must already have checked permission.
    """
    goals = list(
        Goal.objects.filter(user=user, status=Goal.Status.ACTIVE, start_date__lte=today)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .order_by("title", "pk")
    )
    done_dates = done_dates_for([goal.pk for goal in goals])

    rows = []
    for goal in goals:
        goal_done = done_dates[goal.pk]
        streaks = goal_streaks(goal, today, goal_done)
        scheduled_today = is_scheduled(goal, today)
        rows.append({
            "id": goal.pk,
            "title": goal.title,
            "description": goal.description,
            "repeat_label": repeat_summary(goal),
            "time_label": time_label(goal),
            "start_time": goal.start_time,
            "current": streaks.current,
            "best": streaks.best,
            "done_today": (today in goal_done) if scheduled_today else None,
            "strip": _strip(goal, today, goal_done),
        })
    rows.sort(key=_sort_key)

    to_do = [row for row in rows if row["done_today"] is False]
    done = [row for row in rows if row["done_today"] is True]
    return {
        "goals": rows,
        "to_do_today": to_do,
        "done_today": done,
        "today_total": len(to_do) + len(done),
        "today_done": len(done),
        "streak_snapshot": streak_chips(user, today),
    }
