"""faith.goal_rules: which actions a goal allows right now (one place, used by services, views and templates).

The rules (habit = no end date, task = has an end date):
* A goal that has not started yet (``start_date`` after today) can be edited fully ("edit_all"): nothing has happened.
* Once a goal has started, a TASK can only have its end date changed ("edit_end_date").
* Once a habit has started it has no Edit at all; it uses Change schedule ("change_schedule"), which
  changes the repeat and the time going forward and keeps the history.
* A completed goal cannot be edited, change its schedule or be ended. An archived goal is restored first.
* End goal needs a started, active goal. Delete needs an archived goal.
Pure functions on a goal and a date, so they are easy to test and never touch the database.
"""

ACTIVE = "active"
ARCHIVED = "archived"


def actions(goal, today):
    """Return a dict of booleans saying what the person may do with ``goal`` on ``today``."""
    active = goal.status == ACTIVE
    started = goal.start_date <= today
    has_end = goal.end_date is not None
    edit_all = active and not started
    edit_end_date = active and started and has_end
    return {
        "edit": edit_all or edit_end_date,
        "edit_all": edit_all,
        "edit_end_date": edit_end_date,
        "change_schedule": active and started and not has_end,
        "end": active and started,
        "archive": goal.status != ARCHIVED,
        "restore": goal.status == ARCHIVED,
        "delete": goal.status == ARCHIVED,
    }
