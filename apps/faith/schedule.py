"""faith.schedule: pure rules for "is this goal due on this day?".

No database access and no call to ``now()``: every function takes the dates it needs, so the
rules are easy to test and later bricks (stats, reminders, partner view) share one definition.
Weekdays follow Python: 0 = Monday ... 6 = Sunday.
"""
from datetime import timedelta

# Plain strings (same values as Goal.Repeat) so this module never needs the database layer.
DAILY = "daily"
WEEKLY = "weekly"


def is_scheduled(goal, on_date):
    """Return True when ``goal`` is due on ``on_date``.

    A goal is due from ``start_date`` up to and including ``end_date`` (no end date = never ends).
    Daily goals are due every day in that window; weekly goals only on their chosen weekdays.
    Status (active, archived) is deliberately ignored here: callers decide which goals to ask about.
    """
    if on_date < goal.start_date:
        return False
    if goal.end_date is not None and on_date > goal.end_date:
        return False
    if goal.repeat_type == DAILY:
        return True
    return on_date.weekday() in (goal.weekdays or ())


def scheduled_dates(goal, start, end):
    """Return the dates from ``start`` to ``end`` (both included, oldest first) on which ``goal`` is due.

    An ``end`` earlier than ``start`` gives an empty list.
    """
    days = []
    current = start
    while current <= end:
        if is_scheduled(goal, current):
            days.append(current)
        current += timedelta(days=1)
    return days
